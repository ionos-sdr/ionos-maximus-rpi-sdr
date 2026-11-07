# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
"""Wireless M-Bus link layer (EN 13757-4) helpers - no keys needed.

* CRC-16 with polynomial 0x3D65 (init 0, final XOR 0xFFFF)
* frame A / frame B block CRC verification and stripping
* link-layer header: C, M-field (manufacturer), A-field (id, version, type)
* access number (ACC) from the short / long TPL header or the ELL
* ACC-based missed-telegram counter per meter (PER without decryption)
* rtl_wmbus line formatting for wmbusmeters

Written from the public standard and the OMS specification; the test
vectors come from the wmbusmeters test suite (see tests/test_wmbus.py).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional

POLY = 0x3D65


def crc16(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ POLY) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc ^ 0xFFFF


def _crc_ok(buf: bytes, start: int, n: int, pos: int) -> bool:
    c = crc16(buf[start:start + n])
    return buf[pos] == (c >> 8) and buf[pos + 1] == (c & 0xFF)


def frame_a_crcs_valid(buf: bytes) -> bool:
    n = len(buf)
    if n < 12 or not _crc_ok(buf, 0, 10, 10):
        return False
    pos = 12
    while pos + 18 <= n:
        if not _crc_ok(buf, pos, 16, pos + 16):
            return False
        pos += 18
    if pos < n:
        if n - pos < 3:
            return False
        if not _crc_ok(buf, pos, n - pos - 2, n - 2):
            return False
    return True


def frame_b_crcs_valid(buf: bytes) -> bool:
    n = len(buf)
    if n < 12:
        return False
    if n <= 128:
        return _crc_ok(buf, 0, n - 2, n - 2)
    return _crc_ok(buf, 0, 126, 126) and _crc_ok(buf, 128, n - 130, n - 2)


def strip_crcs(buf: bytes, frame_b: Optional[bool] = None) -> Optional[bytes]:
    """Return the datagram without DLL CRCs and with L rewritten to the
    frame-A convention (bytes after L), as wmbusmeters wants it.

    ``frame_b=None`` tries A, then B, then "already stripped". Returns
    None when nothing verifies."""
    n = len(buf)
    if n < 2:
        return None
    if frame_b is not True and frame_a_crcs_valid(buf):
        out = bytearray(buf[:10])
        pos = 12
        while pos + 18 <= n:
            out += buf[pos:pos + 16]
            pos += 18
        if pos < n:
            out += buf[pos:n - 2]
        out[0] = len(out) - 1
        return bytes(out)
    if frame_b is not False and frame_b_crcs_valid(buf):
        if n <= 128:
            out = bytearray(buf[:n - 2])
        else:
            out = bytearray(buf[:126] + buf[128:n - 2])
        out[0] = len(out) - 1
        return bytes(out)
    if buf[0] + 1 == n:
        return bytes(buf)           # already without CRCs
    return None


def manufacturer(m: int) -> str:
    return "".join(chr(((m >> s) & 0x1F) + 64) for s in (10, 5, 0))


def bcd_id(b: bytes) -> str:
    """4 little-endian BCD bytes -> 8-digit string (hex nibbles tolerated)."""
    return "%02X%02X%02X%02X" % (b[3], b[2], b[1], b[0])


@dataclass
class LinkHeader:
    length: int
    c_field: int
    manufacturer_id: int
    manufacturer: str
    ident: str
    version: int
    device_type: int
    ci: Optional[int] = None
    acc: Optional[int] = None
    status: Optional[int] = None
    ell_cc: Optional[int] = None

    @property
    def key(self) -> str:
        """Per-meter key: manufacturer, id, version, type."""
        return "%s.%s.%02X.%02X" % (self.manufacturer, self.ident, self.version, self.device_type)


DEVICE_TYPES = {
    0x00: "Other", 0x01: "Oil", 0x02: "Electricity", 0x03: "Gas", 0x04: "Heat",
    0x05: "Steam", 0x06: "Warm water", 0x07: "Water", 0x08: "Heat cost allocator",
    0x0A: "Cooling (outlet)", 0x0B: "Cooling (inlet)", 0x0C: "Heat (inlet)",
    0x0D: "Heat/cooling", 0x0E: "Bus/system", 0x15: "Hot water", 0x16: "Cold water",
    0x17: "Dual water", 0x18: "Pressure", 0x19: "A/D converter", 0x1A: "Smoke detector",
    0x1B: "Room sensor", 0x1C: "Gas detector", 0x1D: "Breaker (electricity)",
    0x1E: "Valve (gas/water)", 0x20: "Display", 0x25: "Customer unit",
    0x28: "Waste water", 0x29: "Garbage", 0x31: "Communication controller",
    0x32: "Unidirectional repeater", 0x33: "Bidirectional repeater",
    0x36: "Radio converter (system)", 0x37: "Radio converter (meter)",
}

# CI fields: short TPL header (ACC, STATUS, CFG), long TPL header (ID4 MFR2 VER TYPE ACC STATUS CFG)
CI_SHORT_TPL = {0x7A, 0x7B, 0x7D, 0x7F, 0x8A}
CI_LONG_TPL = {0x72, 0x73, 0x75, 0x7C, 0x7E, 0x8B}
CI_ELL = {0x8C: 2, 0x8D: 6, 0x8E: 10, 0x8F: 16}   # ELL length incl. CC and ACC


def parse_header(frame: bytes) -> Optional[LinkHeader]:
    """Parse the DLL header (and ACC where it can be reached) of a
    CRC-stripped datagram ``L C M M A A A A A A CI ...``."""
    if len(frame) < 10:
        return None
    m = frame[2] | frame[3] << 8
    h = LinkHeader(length=frame[0], c_field=frame[1], manufacturer_id=m,
                   manufacturer=manufacturer(m), ident=bcd_id(frame[4:8]),
                   version=frame[8], device_type=frame[9])
    if len(frame) < 11:
        return h
    ci = frame[10]
    h.ci = ci
    pos = 11
    if ci in CI_ELL:
        # ELL: CC, ACC [, SN(4) [, PayloadCRC(2)]] / [M(2) A(6) ...]
        if len(frame) >= pos + 2:
            h.ell_cc = frame[pos]
            h.acc = frame[pos + 1]
        return h
    if ci in CI_SHORT_TPL and len(frame) >= pos + 2:
        h.acc = frame[pos]
        h.status = frame[pos + 1]
    elif ci in CI_LONG_TPL and len(frame) >= pos + 10:
        h.acc = frame[pos + 8]
        h.status = frame[pos + 9]
    return h


@dataclass
class MeterStats:
    key: str
    manufacturer: str
    ident: str
    version: int
    device_type: int
    received: int = 0
    missed: int = 0
    duplicates: int = 0
    resets: int = 0
    last_acc: Optional[int] = None
    first_ts: float = 0.0
    last_ts: float = 0.0
    rssi_sum: float = 0.0
    rssi_min: int = 127
    rssi_max: int = -128
    modes: Dict[str, int] = field(default_factory=dict)

    def per(self) -> Optional[float]:
        tot = self.received + self.missed
        return (self.missed / tot) if tot else None

    def to_dict(self) -> dict:
        d = {
            "key": self.key, "manufacturer": self.manufacturer, "id": self.ident,
            "version": self.version, "type": self.device_type,
            "type_name": DEVICE_TYPES.get(self.device_type, "0x%02X" % self.device_type),
            "received": self.received, "missed": self.missed, "duplicates": self.duplicates,
            "acc_resets": self.resets, "per": self.per(), "last_acc": self.last_acc,
            "first_ts": self.first_ts, "last_ts": self.last_ts,
            "rssi_mean": round(self.rssi_sum / self.received, 1) if self.received else None,
            "rssi_min": self.rssi_min if self.received else None,
            "rssi_max": self.rssi_max if self.received else None,
            "modes": self.modes,
        }
        return d


class AccTracker:
    """Missed-telegram counter per meter from the access number (ACC).

    The ACC increments by one per transmitted telegram (EN 13757-7 / OMS
    7.2.4), modulo 256, so the gap between consecutive received values is
    the number of telegrams we did not hear - no decryption needed. A gap
    larger than ``max_gap`` is treated as a meter reset / re-association
    and not counted as loss. Same ACC again = duplicate (repeaters or
    multi-telegram bursts)."""

    def __init__(self, max_gap: int = 64):
        self.meters: Dict[str, MeterStats] = {}
        self.max_gap = max_gap
        self.unparsed = 0

    def update(self, frame: bytes, rssi_dbm: int = -128, ts: Optional[float] = None,
               mode: Optional[str] = None) -> Optional[LinkHeader]:
        ts = ts if ts is not None else time.time()
        h = parse_header(frame)
        if h is None:
            self.unparsed += 1
            return None
        ms = self.meters.get(h.key)
        if ms is None:
            ms = MeterStats(h.key, h.manufacturer, h.ident, h.version, h.device_type, first_ts=ts)
            self.meters[h.key] = ms
        ms.received += 1
        ms.last_ts = ts
        ms.rssi_sum += rssi_dbm
        ms.rssi_min = min(ms.rssi_min, rssi_dbm)
        ms.rssi_max = max(ms.rssi_max, rssi_dbm)
        if mode:
            ms.modes[mode] = ms.modes.get(mode, 0) + 1
        if h.acc is not None:
            if ms.last_acc is not None:
                gap = (h.acc - ms.last_acc) & 0xFF
                if gap == 0:
                    ms.duplicates += 1
                    ms.received -= 1
                elif gap - 1 <= self.max_gap:
                    ms.missed += gap - 1
                else:
                    ms.resets += 1
            ms.last_acc = h.acc
        return h

    def summary(self) -> dict:
        return {
            "generated": time.time(),
            "meters": sorted((m.to_dict() for m in self.meters.values()), key=lambda d: d["key"]),
            "unparsed": self.unparsed,
        }


def rtlwmbus_line(frame_no_crc: bytes, mode: str, rssi_dbm: int, ts: float,
                  crc_ok: bool = True) -> str:
    """Format one rtl_wmbus line as wmbusmeters' ``wmbus_rtlwmbus.cc`` parses it:

    ``MODE;CRC_OK;3OUTOF6OK;YYYY-mm-dd HH:MM:SS.mmm;PACKET_RSSI;CURRENT_RSSI;ID;0xHEX``

    wmbusmeters checks that characters 3 and 5 are ``1``, parses the
    timestamp with ``%Y-%m-%d %H:%M:%S`` and converts it with ``timegm()``,
    i.e. it is UTC, and reads the RSSI with ``atof`` when the field is
    shorter than 5 characters."""
    tstr = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(ts)) + ".%03d" % int((ts % 1) * 1000)
    ident = bcd_id(frame_no_crc[4:8]) if len(frame_no_crc) >= 8 else "00000000"
    return "%s;%d;1;%s;%d;%d;%s;0x%s" % (mode, 1 if crc_ok else 0, tstr, rssi_dbm, rssi_dbm,
                                         ident, frame_no_crc.hex())
