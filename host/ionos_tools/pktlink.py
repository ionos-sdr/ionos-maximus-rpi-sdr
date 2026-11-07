# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
"""Frame parsers for the FG23 sniffer output.

Two encodings, one ``Packet`` dataclass:

* **text** - one ``P;...`` line per packet (``firmware/sniffer/README.md``)
* **PKT1** - 1040-byte binary blocks, ``firmware/sniffer/pktlink.h`` is the
  normative definition. The same stream may also carry ``IQB2`` (I/Q) and
  ``SPC1`` (spectrum) blocks; ``BlockDemux`` hands those to callbacks and
  only decodes ``PKT1`` here.
"""
from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field, asdict
from typing import Callable, Iterator, List, Optional

BLK_BYTES = 1040
HDR_BYTES = 16
PAYLOAD_BYTES = BLK_BYTES - HDR_BYTES
REC_HDR_BYTES = 24
MAX_PSDU = PAYLOAD_BYTES - REC_HDR_BYTES

MAGIC_PKT1 = b"PKT1"
MAGIC_IQB2 = b"IQB2"
MAGIC_SPC1 = b"SPC1"

_HDR = struct.Struct("<4sIHHI")          # magic, seq, nbytes, flags, f_center_hz
_REC = struct.Struct("<HBBBbBBIIIHH")    # len, phy, subphy, flags, rssi, lqi, hop, ts, seq, freq, ch, drops

PHY_NAMES = ["wmbus_tc", "wmbus_s", "wmbus_cb", "154g",
             "fsk2k4", "fsk4k8", "fsk9k6", "fsk38k4", "fsk50k", "fsk100k"]
PHY_BITRATE = [100000, 32768, 100000, 50000, 2400, 4800, 9600, 38400, 50000, 100000]

# record flags (pktlink.h)
F_CRC_OK = 0x01
F_TRUNCATED = 0x02
F_HAS_PHR = 0x04
F_HAS_FCS = 0x08
F_WMBUS_CRCS = 0x10
F_FILTERED = 0x20
F_HOPPING = 0x40

# RAIL_WMBUS_Phy_t subPhyId values
WMBUS_SUB_T_A = 0
WMBUS_SUB_C_A = 2
WMBUS_SUB_C_B = 3


@dataclass
class Packet:
    """One received frame with device- and host-side metadata."""
    psdu: bytes
    phy: str
    subphy: int = 0
    flags: int = 0
    rssi_dbm: int = -128
    lqi: int = 0
    dev_ts_us: int = 0           # RAIL timestamp (sync word end), 32-bit wrap
    seq: int = 0                 # firmware packet sequence
    freq_hz: int = 0
    channel: int = 0
    drops: int = 0
    hop_idx: Optional[int] = None
    host_ts: float = field(default_factory=time.time)   # UNIX seconds, float
    block_seq: Optional[int] = None

    @property
    def crc_ok(self) -> bool:
        return bool(self.flags & F_CRC_OK)

    @property
    def phy_id(self) -> int:
        return PHY_NAMES.index(self.phy) if self.phy in PHY_NAMES else 255

    @property
    def is_wmbus(self) -> bool:
        return self.phy.startswith("wmbus")

    @property
    def wmbus_mode(self) -> Optional[str]:
        """'T1' / 'C1' / 'S1' as wmbusmeters names them, or None."""
        if self.phy == "wmbus_s":
            return "S1"
        if self.phy == "wmbus_cb":
            return "C1"
        if self.phy == "wmbus_tc":
            return "C1" if self.subphy in (WMBUS_SUB_C_A, WMBUS_SUB_C_B) else "T1"
        return None

    @property
    def wmbus_frame_b(self) -> bool:
        return self.phy == "wmbus_cb" or (self.phy == "wmbus_tc" and self.subphy == WMBUS_SUB_C_B)

    @property
    def bitrate(self) -> int:
        i = self.phy_id
        return PHY_BITRATE[i] if i < len(PHY_BITRATE) else 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["psdu"] = self.psdu.hex()
        d["crc_ok"] = self.crc_ok
        d["len"] = len(self.psdu)
        return d


# --------------------------------------------------------------------------
# text lines
# --------------------------------------------------------------------------

def parse_text_line(line: str, host_ts: Optional[float] = None) -> Optional[Packet]:
    """Parse ``P;seq;ts_us;phy;subphy;freq;rssi;lqi;crc;drops;HEX``.

    Returns None for anything else (``#`` status lines, garbage)."""
    line = line.strip()
    if not line.startswith("P;"):
        return None
    f = line.split(";")
    if len(f) != 11:
        return None
    try:
        psdu = bytes.fromhex(f[10])
        return Packet(
            psdu=psdu, phy=f[3], subphy=int(f[4]), seq=int(f[1]), dev_ts_us=int(f[2]),
            freq_hz=int(f[5]), rssi_dbm=int(f[6]), lqi=int(f[7]),
            flags=F_CRC_OK if f[8] == "1" else 0, drops=int(f[9]),
            host_ts=host_ts if host_ts is not None else time.time(),
        )
    except ValueError:
        return None


def format_text_line(p: Packet) -> str:
    return "P;%d;%d;%s;%d;%d;%d;%d;%d;%d;%s" % (
        p.seq, p.dev_ts_us, p.phy, p.subphy, p.freq_hz, p.rssi_dbm, p.lqi,
        1 if p.crc_ok else 0, p.drops, p.psdu.hex().upper())


# --------------------------------------------------------------------------
# PKT1 blocks
# --------------------------------------------------------------------------

def parse_pkt1_block(blk: bytes, host_ts: Optional[float] = None) -> List[Packet]:
    """Decode the records of one 1040-byte PKT1 block."""
    if len(blk) != BLK_BYTES:
        raise ValueError("block must be %d bytes" % BLK_BYTES)
    magic, bseq, nbytes, _flags, f_center = _HDR.unpack_from(blk, 0)
    if magic != MAGIC_PKT1:
        raise ValueError("not a PKT1 block")
    if nbytes > PAYLOAD_BYTES:
        raise ValueError("nbytes out of range")
    ts = host_ts if host_ts is not None else time.time()
    out: List[Packet] = []
    pos = HDR_BYTES
    end = HDR_BYTES + nbytes
    while pos + REC_HDR_BYTES <= end:
        (ln, phy, sub, fl, rssi, lqi, hop, dts, seq, freq, ch, drops) = _REC.unpack_from(blk, pos)
        if ln > MAX_PSDU or pos + REC_HDR_BYTES + ln > end:
            break
        psdu = blk[pos + REC_HDR_BYTES: pos + REC_HDR_BYTES + ln]
        out.append(Packet(
            psdu=psdu, phy=PHY_NAMES[phy] if phy < len(PHY_NAMES) else "phy%d" % phy,
            subphy=sub, flags=fl, rssi_dbm=rssi, lqi=lqi, dev_ts_us=dts, seq=seq,
            freq_hz=freq or f_center, channel=ch, drops=drops,
            hop_idx=None if hop == 0xFF else hop, host_ts=ts, block_seq=bseq))
        pos += (REC_HDR_BYTES + ln + 3) & ~3
    return out


def build_pkt1_block(packets: List[Packet], block_seq: int = 0, f_center_hz: int = 0) -> bytes:
    """Inverse of parse_pkt1_block (tests, simulators)."""
    payload = bytearray()
    for p in packets:
        rec = _REC.pack(len(p.psdu), p.phy_id, p.subphy, p.flags, p.rssi_dbm, p.lqi,
                        0xFF if p.hop_idx is None else p.hop_idx, p.dev_ts_us & 0xFFFFFFFF,
                        p.seq & 0xFFFFFFFF, p.freq_hz, p.channel, p.drops & 0xFFFF)
        payload += rec + p.psdu
        payload += b"\0" * ((-len(payload)) % 4)
    if len(payload) > PAYLOAD_BYTES:
        raise ValueError("records do not fit one block")
    hdr = _HDR.pack(MAGIC_PKT1, block_seq, len(payload), 1, f_center_hz)
    return hdr + bytes(payload) + b"\0" * (PAYLOAD_BYTES - len(payload))


class BlockDemux:
    """Byte-stream resynchroniser for 1040-byte blocks (UART or SPI).

    Feed arbitrary chunks; ``packets()`` yields decoded PKT1 packets. Other
    magics go to ``on_iq`` / ``on_spec`` callbacks (raw 1040-byte blocks).
    Sequence gaps are counted in ``lost_blocks``."""

    MAGICS = (MAGIC_PKT1, MAGIC_IQB2, MAGIC_SPC1)

    def __init__(self, on_iq: Optional[Callable[[bytes], None]] = None,
                 on_spec: Optional[Callable[[bytes], None]] = None):
        self.buf = bytearray()
        self.on_iq = on_iq
        self.on_spec = on_spec
        self.blocks = 0
        self.lost_blocks = 0
        self.resyncs = 0
        self._last_seq: Optional[int] = None
        self._pending: List[Packet] = []

    def feed(self, data: bytes) -> None:
        self.buf += data
        while True:
            if len(self.buf) < BLK_BYTES:
                return
            if self.buf[:4] not in self.MAGICS:
                # find the next plausible magic
                idx = -1
                for m in self.MAGICS:
                    j = self.buf.find(m, 1)
                    if j >= 0 and (idx < 0 or j < idx):
                        idx = j
                if idx < 0:
                    del self.buf[:-3]
                    return
                del self.buf[:idx]
                self.resyncs += 1
                continue
            blk = bytes(self.buf[:BLK_BYTES])
            del self.buf[:BLK_BYTES]
            self._block(blk)

    def feed_block(self, blk: bytes) -> None:
        """For SPI, where the transport already delivers whole blocks."""
        if len(blk) != BLK_BYTES or blk[:4] not in self.MAGICS:
            self.resyncs += 1
            return
        self._block(blk)

    def _block(self, blk: bytes) -> None:
        self.blocks += 1
        seq = struct.unpack_from("<I", blk, 4)[0]
        if self._last_seq is not None:
            gap = (seq - self._last_seq - 1) & 0xFFFFFFFF
            if 0 < gap < 1 << 30:
                self.lost_blocks += gap
        self._last_seq = seq
        magic = blk[:4]
        if magic == MAGIC_PKT1:
            self._pending.extend(parse_pkt1_block(blk))
        elif magic == MAGIC_IQB2 and self.on_iq:
            self.on_iq(blk)
        elif magic == MAGIC_SPC1 and self.on_spec:
            self.on_spec(blk)

    def packets(self) -> Iterator[Packet]:
        while self._pending:
            yield self._pending.pop(0)


class TextDemux:
    """Line assembler for the text mode; ignores ``#`` status lines but
    keeps them in ``status`` for logging."""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.status: List[str] = []
        self._pending: List[Packet] = []

    def feed(self, data: bytes) -> None:
        self.buf += data
        while True:
            i = self.buf.find(b"\n")
            if i < 0:
                return
            line = self.buf[:i].decode("ascii", errors="replace")
            del self.buf[:i + 1]
            p = parse_text_line(line)
            if p is not None:
                self._pending.append(p)
            elif line.strip():
                self.status.append(line.strip())

    def packets(self) -> Iterator[Packet]:
        while self._pending:
            yield self._pending.pop(0)
