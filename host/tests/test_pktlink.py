# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
import io
import struct

from ionos_tools import pktlink, pcapout
from ionos_tools.pktlink import Packet


def mk(i, phy="wmbus_tc", n=20, flags=pktlink.F_CRC_OK):
    return Packet(psdu=bytes((i + k) & 0xFF for k in range(n)), phy=phy, subphy=2, flags=flags,
                  rssi_dbm=-60 - i, lqi=200, dev_ts_us=1000 * i, seq=i, freq_hz=868950000,
                  channel=0, drops=0, host_ts=1700000000.25 + i)


def test_block_roundtrip_multiple_records():
    pk = [mk(i, n=10 + i) for i in range(5)]
    blk = pktlink.build_pkt1_block(pk, block_seq=7, f_center_hz=868950000)
    assert len(blk) == pktlink.BLK_BYTES and blk[:4] == b"PKT1"
    out = pktlink.parse_pkt1_block(blk, host_ts=1.0)
    assert len(out) == 5
    for a, b in zip(pk, out):
        assert a.psdu == b.psdu and a.rssi_dbm == b.rssi_dbm and a.seq == b.seq
        assert b.phy == "wmbus_tc" and b.subphy == 2 and b.crc_ok and b.block_seq == 7
        assert b.wmbus_mode == "C1" and not b.wmbus_frame_b


def test_record_sizes_match_header():
    # 24-byte record header, 16-byte block header, as in pktlink.h
    assert pktlink._REC.size == 24 and pktlink._HDR.size == 16
    assert pktlink.MAX_PSDU == 1000


def test_demux_resync_and_loss():
    d = pktlink.BlockDemux()
    b0 = pktlink.build_pkt1_block([mk(0)], block_seq=0)
    b1 = pktlink.build_pkt1_block([mk(1)], block_seq=1)
    b3 = pktlink.build_pkt1_block([mk(3)], block_seq=3)        # block 2 lost
    stream = b"garbage\x00\x01" + b0 + b1[:500]
    d.feed(stream)
    d.feed(b1[500:] + b3)
    pk = list(d.packets())
    assert [p.seq for p in pk] == [0, 1, 3]
    assert d.blocks == 3 and d.lost_blocks == 1 and d.resyncs == 1


def test_demux_dispatches_iq_blocks():
    got = []
    d = pktlink.BlockDemux(on_iq=got.append)
    iq = b"IQB2" + struct.pack("<IHHI", 5, 256, 32, 1000000) + b"\0" * 1024
    d.feed(iq + pktlink.build_pkt1_block([mk(9)], block_seq=6))
    assert len(got) == 1 and got[0][:4] == b"IQB2"
    assert [p.seq for p in d.packets()] == [9]


def test_text_line_roundtrip():
    p = mk(3)
    line = pktlink.format_text_line(p)
    assert line.startswith("P;3;3000;wmbus_tc;2;868950000;-63;200;1;0;")
    q = pktlink.parse_text_line(line)
    assert q.psdu == p.psdu and q.rssi_dbm == -63 and q.crc_ok
    assert pktlink.parse_text_line("# status line") is None
    t = pktlink.TextDemux()
    t.feed(b"# ionos\r\n" + line.encode() + b"\r\nP;garbage\n")
    assert [x.seq for x in t.packets()] == [3]
    assert t.status == ["# ionos", "P;garbage"]      # unparseable lines are kept for logging


def test_pcap_tap_154g_and_user_dlt():
    # 802.15.4g frame: PHR (len 7, FCS16 bit 11 set) + 5 bytes MAC + 2 FCS
    phr = (7 | (1 << 11)).to_bytes(2, "little")
    p = Packet(psdu=phr + b"\x41\x88\x01\x02\x03" + b"\xAA\xBB", phy="154g",
               flags=pktlink.F_CRC_OK | pktlink.F_HAS_PHR | pktlink.F_HAS_FCS,
               rssi_dbm=-80, lqi=100, dev_ts_us=42, seq=1, freq_hz=868100000, host_ts=1.5)
    bio = io.BytesIO()
    w = pcapout.PcapWriter(bio, pcapout.DLT_IEEE802_15_4_TAP)
    w.write(p)
    data = bio.getvalue()
    magic, _, _, _, _, _, dlt = struct.unpack_from("<IHHiIII", data, 0)
    assert magic == 0xA1B2C3D4 and dlt == 283
    ts_s, ts_us, incl, orig = struct.unpack_from("<IIII", data, 24)
    assert (ts_s, ts_us) == (1, 500000) and incl == orig
    rec = data[40:40 + incl]
    tlvs, payload = pcapout.parse_tap_record(rec)
    assert payload == b"\x41\x88\x01\x02\x03\xAA\xBB"            # PHR stripped, FCS kept
    assert struct.unpack("<I", tlvs[pcapout.TLV_FCS_TYPE])[0] == 1   # 16-bit FCS
    assert struct.unpack("<f", tlvs[pcapout.TLV_RSS])[0] == -80.0
    assert struct.unpack("<I", tlvs[pcapout.TLV_LQI])[0] == 100
    assert struct.unpack("<I", tlvs[pcapout.TLV_CHANNEL_FREQ])[0] == 868100
    assert struct.unpack("<I", tlvs[pcapout.TLV_BIT_RATE])[0] == 50000
    meta = struct.unpack("<BBBBIII", tlvs[pcapout.TLV_IONOS_META])
    assert meta[0] == 3 and meta[4] == 1 and meta[5] == 42

    bio = io.BytesIO()
    w = pcapout.PcapWriter(bio, pcapout.DLT_USER0)
    w.write(mk(0))
    data = bio.getvalue()
    assert struct.unpack_from("<I", data, 20)[0] == 147
    incl = struct.unpack_from("<IIII", data, 24)[2]
    tlvs, payload = pcapout.parse_tap_record(data[40:40 + incl])
    assert payload == mk(0).psdu and pcapout.TLV_FCS_TYPE not in tlvs
