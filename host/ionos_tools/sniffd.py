#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
"""sniffd - Raspberry Pi daemon for the FG23 packet sniffer.

Reads packets from the sniffer (serial text / PKT1, SPI + DRDY, or a file)
and fans them out to:

  (a) rtl_wmbus text lines for wmbusmeters      --rtlwmbus - | FIFO path
  (b) pcap with TAP metadata                    --pcap file | -
  (c) JSON lines on a TCP or Unix socket        --jsonl host:port | unix:/path
  (d) ACC-based missed-telegram summary         --acc-summary file [--acc-interval s]

Examples
  sniffd --source serial:/dev/ttyACM0 --phy wmbus_tc --rtlwmbus -
  sniffd --source spi:/dev/spidev0.1 --drdy gpiochip0:4 --cmd /dev/ttyAMA0 \
         --phy 154g --freq 868100 --pcap /var/log/ionos/154g.pcap --jsonl 127.0.0.1:7771
  sniffd --source file:capture.pkt1 --text
"""
from __future__ import annotations

import argparse
import json
import os
import select
import socket
import sys
import threading
import time
from typing import List, Optional, TextIO

from . import pktlink
from . import wmbus
from .pcapout import PcapWriter, DLT_IEEE802_15_4_TAP, DLT_USER0
from .pktlink import Packet

LOG = sys.stderr


def log(msg: str) -> None:
    LOG.write("sniffd: %s\n" % msg)
    LOG.flush()


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------

class Source:
    """Yields raw bytes (serial/file) or whole blocks (SPI)."""
    block_mode = False

    def read(self, timeout: float) -> Optional[bytes]:
        raise NotImplementedError

    def write_cmd(self, line: str) -> bool:
        return False

    def close(self) -> None:
        pass


class SerialSource(Source):
    def __init__(self, dev: str, baud: int):
        import serial  # pyserial
        self.ser = serial.Serial(dev, baud, timeout=0)
        log("serial %s @ %d" % (dev, baud))

    def read(self, timeout: float) -> Optional[bytes]:
        r, _, _ = select.select([self.ser.fileno()], [], [], timeout)
        if not r:
            return None
        return self.ser.read(4096)

    def write_cmd(self, line: str) -> bool:
        self.ser.write((line + "\n").encode("ascii"))
        self.ser.flush()
        return True

    def close(self) -> None:
        self.ser.close()


class FileSource(Source):
    """Replay of a capture (text or PKT1 blocks). '-' = stdin."""
    def __init__(self, path: str, realtime: bool = False):
        self.fh = sys.stdin.buffer if path == "-" else open(path, "rb")
        self.realtime = realtime
        self.done = False

    def read(self, timeout: float) -> Optional[bytes]:
        if self.done:
            time.sleep(timeout)
            return None
        data = self.fh.read(4096)
        if not data:
            self.done = True
            return None
        if self.realtime:
            time.sleep(0.01)
        return data


class SpiSource(Source):
    """Mode A: Pi is the SPI main. Wait for DRDY, clock one 1040-byte block.

    DRDY is read through libgpiod (v2 or v1 Python bindings); with
    --drdy none the block is polled every ``poll_ms`` and rejected if the
    magic is not valid (the FG23 shifts zeros while idle)."""
    block_mode = True

    def __init__(self, dev: str, hz: int, drdy: Optional[str], poll_ms: float = 5.0):
        import spidev
        bus, cs = self._parse_dev(dev)
        self.spi = spidev.SpiDev()
        self.spi.open(bus, cs)
        self.spi.max_speed_hz = hz
        self.spi.mode = 0
        self.spi.bits_per_word = 8
        self.poll_s = poll_ms / 1000.0
        self.drdy = self._open_drdy(drdy) if drdy and drdy != "none" else None
        log("spi %s @ %d Hz, DRDY %s" % (dev, hz, drdy or "poll"))

    @staticmethod
    def _parse_dev(dev: str):
        name = os.path.basename(dev)          # spidev0.1
        b, c = name.replace("spidev", "").split(".")
        return int(b), int(c)

    def _open_drdy(self, spec: str):
        chip, line = spec.split(":")
        line = int(line)
        import gpiod
        if hasattr(gpiod, "request_lines"):           # libgpiod v2
            from gpiod.line import Direction, Edge
            req = gpiod.request_lines("/dev/" + chip if not chip.startswith("/") else chip,
                                      consumer="sniffd",
                                      config={line: gpiod.LineSettings(direction=Direction.INPUT,
                                                                        edge_detection=Edge.RISING)})
            return ("v2", req, line)
        c = gpiod.Chip(chip)                           # libgpiod v1
        ln = c.get_line(line)
        ln.request(consumer="sniffd", type=gpiod.LINE_REQ_EV_RISING_EDGE)
        return ("v1", ln, line)

    def _drdy_high(self) -> bool:
        kind, obj, line = self.drdy
        if kind == "v2":
            from gpiod.line import Value
            return obj.get_value(line) == Value.ACTIVE
        return obj.get_value() == 1

    def _wait_drdy(self, timeout: float) -> bool:
        if self.drdy is None:
            time.sleep(self.poll_s)
            return True
        if self._drdy_high():
            return True
        kind, obj, _ = self.drdy
        if kind == "v2":
            if obj.wait_edge_events(timeout):
                obj.read_edge_events()
                return True
            return False
        if obj.event_wait(sec=int(timeout), nsec=int((timeout % 1) * 1e9)):
            obj.event_read()
            return True
        return False

    def read(self, timeout: float) -> Optional[bytes]:
        if not self._wait_drdy(timeout):
            return None
        blk = bytes(self.spi.readbytes(pktlink.BLK_BYTES))
        return blk

    def close(self) -> None:
        self.spi.close()


class CmdLink:
    """Pi UART TXD (GPIO14) -> FG23 PA06, 115200 8N1, write-only."""
    def __init__(self, dev: str):
        import serial
        self.ser = serial.Serial(dev, 115200, timeout=0)

    def send(self, line: str) -> None:
        self.ser.write((line + "\n").encode("ascii"))
        self.ser.flush()
        time.sleep(0.05)


# --------------------------------------------------------------------------
# sinks
# --------------------------------------------------------------------------

class JsonlServer:
    """Broadcasts one JSON object per packet to every connected client.
    Shape follows what a Sniff-Sight ``PacketSource`` consumes: t, rssi,
    lqi, seq, freq_mhz, channel, len, hex payload plus our fields."""
    def __init__(self, spec: str):
        if spec.startswith("unix:"):
            path = spec[5:]
            if os.path.exists(path):
                os.unlink(path)
            self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.srv.bind(path)
        else:
            host, port = spec.rsplit(":", 1)
            self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.srv.bind((host, int(port)))
        self.srv.listen(8)
        self.clients: List[socket.socket] = []
        self.lock = threading.Lock()
        threading.Thread(target=self._accept, daemon=True).start()
        log("jsonl on %s" % spec)

    def _accept(self) -> None:
        while True:
            c, _ = self.srv.accept()
            c.setblocking(False)
            with self.lock:
                self.clients.append(c)

    def send(self, obj: dict) -> None:
        data = (json.dumps(obj, separators=(",", ":")) + "\n").encode()
        with self.lock:
            dead = []
            for c in self.clients:
                try:
                    c.sendall(data)
                except (BlockingIOError, BrokenPipeError, ConnectionResetError, OSError):
                    dead.append(c)
            for c in dead:
                self.clients.remove(c)
                c.close()


def packet_json(p: Packet, hdr: Optional[wmbus.LinkHeader]) -> dict:
    d = {
        "t": p.host_ts, "dev_ts_us": p.dev_ts_us, "seq": p.seq, "phy": p.phy,
        "subphy": p.subphy, "mode": p.wmbus_mode, "freq_mhz": p.freq_hz / 1e6,
        "channel": p.channel, "rssi": p.rssi_dbm, "lqi": p.lqi, "crc_ok": p.crc_ok,
        "flags": p.flags, "drops": p.drops, "len": len(p.psdu), "hex": p.psdu.hex(),
    }
    if hdr:
        d.update({"src": "%s %s" % (hdr.manufacturer, hdr.ident), "manufacturer": hdr.manufacturer,
                  "id": hdr.ident, "version": hdr.version, "type": hdr.device_type,
                  "c": hdr.c_field, "ci": hdr.ci, "acc": hdr.acc})
    return d


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def open_source(spec: str, baud: int, spi_hz: int, drdy: Optional[str], realtime: bool) -> Source:
    if spec.startswith("serial:"):
        return SerialSource(spec[7:], baud)
    if spec.startswith("spi:"):
        return SpiSource(spec[4:], spi_hz, drdy)
    if spec.startswith("file:"):
        return FileSource(spec[5:], realtime)
    if spec == "-":
        return FileSource("-", realtime)
    if spec.startswith("/dev/tty"):
        return SerialSource(spec, baud)
    raise SystemExit("unknown source %r" % spec)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="sniffd", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", required=True,
                    help="serial:/dev/ttyACM0 | spi:/dev/spidev0.0 | file:PATH | -")
    ap.add_argument("--baud", type=int, default=921600)
    ap.add_argument("--spi-hz", type=int, default=4000000, help="SPI clock (M1 decides the maximum)")
    ap.add_argument("--drdy", default="gpiochip0:25", help="gpiochipN:LINE or 'none' (rev B radio 2: gpiochip0:4)")
    ap.add_argument("--format", choices=["auto", "text", "pkt1"], default="auto")
    ap.add_argument("--cmd", help="cmdlink UART device (Pi TXD -> FG23 PA06), e.g. /dev/ttyAMA0")
    ap.add_argument("--phy", help="PHY to select at start (wmbus_tc, wmbus_s, wmbus_cb, 154g, fsk*)")
    ap.add_argument("--freq", type=int, help="frequency in kHz to tune at start")
    ap.add_argument("--hop", help="hop list 'kHz,kHz,...;dwell_ms'")
    ap.add_argument("--keep-bad-crc", action="store_true")
    ap.add_argument("--raw-cmd", action="append", default=[], help="extra command line(s) to send at start")
    ap.add_argument("--rtlwmbus", help="write rtl_wmbus lines to '-' (stdout) or a file/FIFO")
    ap.add_argument("--pcap", help="pcap output path or '-'")
    ap.add_argument("--dlt", type=int, help="override pcap DLT (default 283 for 154g, 147 otherwise)")
    ap.add_argument("--jsonl", help="JSON lines server: host:port or unix:/path")
    ap.add_argument("--acc-summary", help="write the ACC-based PER summary JSON here")
    ap.add_argument("--acc-interval", type=float, default=60.0)
    ap.add_argument("--text", action="store_true", help="echo P; text lines to stdout")
    ap.add_argument("--realtime", action="store_true", help="file source: pace the replay")
    ap.add_argument("--exit-on-eof", action="store_true", help="file source: exit when the file ends")
    ap.add_argument("--stats-interval", type=float, default=0, help="log counters every N s (0 = off)")
    a = ap.parse_args(argv)

    src = open_source(a.source, a.baud, a.spi_hz, a.drdy, a.realtime)

    # --- configure the FG23 ---
    cmds: List[str] = ["D%d" % int(time.time())]
    if a.phy:
        cmds.append("P" + a.phy)
    if a.freq:
        cmds.append("F%d" % a.freq)
    if a.hop:
        cmds.append("H" + a.hop)
    if a.keep_bad_crc:
        cmds.append("K1")
    cmds += a.raw_cmd
    if src.block_mode:
        cmds.append("Os")
    elif a.format == "pkt1":
        cmds.append("Ob")
    elif a.format == "text" or (a.format == "auto" and not src.block_mode):
        cmds.append("Ot")
    cl = CmdLink(a.cmd) if a.cmd else None
    for c in cmds:
        if cl:
            cl.send(c)
        elif not src.write_cmd(c):
            break
        else:
            time.sleep(0.05)

    # --- sinks ---
    rtl_out: Optional[TextIO] = None
    if a.rtlwmbus:
        rtl_out = sys.stdout if a.rtlwmbus == "-" else open(a.rtlwmbus, "w", buffering=1)
    pcap: Optional[PcapWriter] = None
    if a.pcap:
        dlt = a.dlt if a.dlt is not None else (DLT_IEEE802_15_4_TAP if a.phy == "154g" else DLT_USER0)
        fh = sys.stdout.buffer if a.pcap == "-" else open(a.pcap, "wb")
        pcap = PcapWriter(fh, dlt)
    jsonl = JsonlServer(a.jsonl) if a.jsonl else None
    acc = wmbus.AccTracker()
    last_summary = time.time()
    last_stats = time.time()

    text_dmx = pktlink.TextDemux()
    blk_dmx = pktlink.BlockDemux()
    fmt = "pkt1" if src.block_mode else a.format
    n_pkts = 0

    def handle(p: Packet) -> None:
        nonlocal n_pkts
        n_pkts += 1
        hdr = None
        if p.is_wmbus:
            frame = wmbus.strip_crcs(p.psdu, p.wmbus_frame_b if p.phy != "wmbus_tc" else None)
            if frame is not None:
                hdr = acc.update(frame, p.rssi_dbm, p.host_ts, p.wmbus_mode)
                if rtl_out and p.wmbus_mode:
                    rtl_out.write(wmbus.rtlwmbus_line(frame, p.wmbus_mode, p.rssi_dbm, p.host_ts,
                                                      crc_ok=p.crc_ok) + "\n")
                    rtl_out.flush()
        if a.text:
            sys.stdout.write(pktlink.format_text_line(p) + "\n")
            sys.stdout.flush()
        if pcap:
            pcap.write(p)
        if jsonl:
            jsonl.send(packet_json(p, hdr))

    try:
        while True:
            data = src.read(0.5)
            if data:
                if src.block_mode:
                    blk_dmx.feed_block(data)
                else:
                    if fmt == "auto":
                        if b"PKT1" in data or b"IQB2" in data:
                            fmt = "pkt1"
                        elif b"P;" in data or b"#" in data:
                            fmt = "text"
                    if fmt == "pkt1":
                        blk_dmx.feed(data)
                    else:
                        text_dmx.feed(data)
                        for s in text_dmx.status:
                            log("fg23: " + s)
                        text_dmx.status.clear()
                for p in list(blk_dmx.packets()) + list(text_dmx.packets()):
                    handle(p)
            elif isinstance(src, FileSource) and src.done and a.exit_on_eof:
                break
            now = time.time()
            if a.acc_summary and now - last_summary >= a.acc_interval:
                last_summary = now
                with open(a.acc_summary + ".tmp", "w") as fh:
                    json.dump(acc.summary(), fh, indent=1)
                os.replace(a.acc_summary + ".tmp", a.acc_summary)
            if a.stats_interval and now - last_stats >= a.stats_interval:
                last_stats = now
                log("packets=%d blocks=%d lost_blocks=%d resyncs=%d meters=%d" % (
                    n_pkts, blk_dmx.blocks, blk_dmx.lost_blocks, blk_dmx.resyncs, len(acc.meters)))
    except KeyboardInterrupt:
        pass
    finally:
        if a.acc_summary:
            with open(a.acc_summary, "w") as fh:
                json.dump(acc.summary(), fh, indent=1)
        src.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
