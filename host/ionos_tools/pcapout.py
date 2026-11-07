# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
"""pcap output with per-frame radio metadata.

Two link types, one TLV container (the IEEE 802.15.4 TAP layout, so the
same code and the same reader handle both):

* ``DLT 283`` LINKTYPE_IEEE802_15_4_TAP for ``154g`` frames. TLVs from the
  TAP specification: 0 FCS type, 1 RSS (float dBm), 2 bit rate, 10 LQI,
  11 channel frequency (kHz), 5 start-of-frame timestamp (ns) - plus the
  private TLV 0x8000 below. Wireshark decodes these natively.
* ``DLT 147`` LINKTYPE_USER0 (configurable) for wM-Bus and generic FSK
  frames: identical header and TLVs, followed by the raw PSDU. Because it
  is a USER DLT, document it in Wireshark as "User 0 (DLT=147)" with the
  payload protocol of your choice, or read it with ``pktlink.py``.

TAP header: ``u8 version=0, u8 reserved=0, u16 length`` (total header
incl. TLVs, little-endian), then TLVs ``u16 type, u16 len, value, pad to 4``.

Private TLV 0x8000 "ionos packet meta", 16 bytes:
``u8 phy, u8 subphy, u8 flags, u8 hop_idx, u32 pkt_seq, u32 dev_ts_us, u32 drops``
(same fields and meaning as ``pktlink_rec_hdr_t``).
"""
from __future__ import annotations

import struct
from typing import BinaryIO, Optional

from .pktlink import Packet, F_HAS_PHR, F_HAS_FCS

DLT_IEEE802_15_4_TAP = 283
DLT_USER0 = 147

TLV_FCS_TYPE = 0
TLV_RSS = 1
TLV_BIT_RATE = 2
TLV_SOF_TS = 5
TLV_LQI = 10
TLV_CHANNEL_FREQ = 11
TLV_IONOS_META = 0x8000


def tlv(t: int, value: bytes) -> bytes:
    pad = (-len(value)) % 4
    return struct.pack("<HH", t, len(value)) + value + b"\0" * pad


def tap_header(p: Packet, fcs_type: Optional[int]) -> bytes:
    tlvs = b""
    if fcs_type is not None:
        tlvs += tlv(TLV_FCS_TYPE, struct.pack("<I", fcs_type))
    tlvs += tlv(TLV_RSS, struct.pack("<f", float(p.rssi_dbm)))
    if p.bitrate:
        tlvs += tlv(TLV_BIT_RATE, struct.pack("<I", p.bitrate))
    tlvs += tlv(TLV_LQI, struct.pack("<I", p.lqi))
    if p.freq_hz:
        tlvs += tlv(TLV_CHANNEL_FREQ, struct.pack("<I", p.freq_hz // 1000))
    tlvs += tlv(TLV_SOF_TS, struct.pack("<Q", (p.dev_ts_us & 0xFFFFFFFF) * 1000))
    tlvs += tlv(TLV_IONOS_META, struct.pack("<BBBBIII", p.phy_id, p.subphy, p.flags & 0xFF,
                                            0xFF if p.hop_idx is None else p.hop_idx,
                                            p.seq & 0xFFFFFFFF, p.dev_ts_us & 0xFFFFFFFF,
                                            p.drops & 0xFFFFFFFF))
    return struct.pack("<BBH", 0, 0, 4 + len(tlvs)) + tlvs


def frame_154g(p: Packet) -> tuple[bytes, int]:
    """Return (PSDU without PHR, fcs_type) for an 802.15.4g frame.

    PHR (2 bytes, LSB first on air; RAIL hands it over as received):
    bit 11 = FCS type (0 = 4-byte CRC32, 1 = 2-byte CRC16), bit 12 = data
    whitening, bits 15..5 reserved/mode switch, low 11 bits = frame length.
    TAP FCS type: 0 none, 1 = 16-bit CRC, 2 = 32-bit CRC."""
    psdu = p.psdu
    fcs_type = 0
    if p.flags & F_HAS_PHR and len(psdu) >= 2:
        phr = psdu[0] | psdu[1] << 8
        fcs16 = bool(phr & (1 << 11))
        psdu = psdu[2:]
        if p.flags & F_HAS_FCS:
            fcs_type = 1 if fcs16 else 2
    elif p.flags & F_HAS_FCS:
        fcs_type = 1
    return psdu, fcs_type


class PcapWriter:
    """Classic pcap (magic 0xA1B2C3D4, microsecond resolution)."""

    def __init__(self, fh: BinaryIO, dlt: int):
        self.fh = fh
        self.dlt = dlt
        self.fh.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, dlt))
        self.fh.flush()
        self.frames = 0

    def write(self, p: Packet) -> None:
        if self.dlt == DLT_IEEE802_15_4_TAP:
            body, fcs = frame_154g(p)
            rec = tap_header(p, fcs) + body
        else:
            rec = tap_header(p, None) + p.psdu
        ts = p.host_ts
        self.fh.write(struct.pack("<IIII", int(ts), int((ts % 1) * 1e6), len(rec), len(rec)) + rec)
        self.fh.flush()
        self.frames += 1


def parse_tap_record(rec: bytes) -> tuple[dict, bytes]:
    """Reader for both DLTs: returns ({tlv_type: value_bytes}, payload)."""
    _v, _r, hlen = struct.unpack_from("<BBH", rec, 0)
    pos, tlvs = 4, {}
    while pos + 4 <= hlen:
        t, ln = struct.unpack_from("<HH", rec, pos)
        tlvs[t] = rec[pos + 4: pos + 4 + ln]
        pos += 4 + ln + ((-ln) % 4)
    return tlvs, rec[hlen:]
