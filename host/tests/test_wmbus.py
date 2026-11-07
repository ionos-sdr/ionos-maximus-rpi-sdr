# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
"""wM-Bus link-layer tests.

Test vectors (public, from the wmbusmeters project test suite):
  * simulations/simulation_dll_crcs.txt  - frame A telegrams WITH DLL CRCs
    (Sontex heat cost allocator 27293981, Apator gas meter 000BC37C)
  * tests/test_frame_b_trim.sh            - 194-byte frame B telegram WITH CRCs
    (Engelmann Sensostar 43054304, manufacturer EFE)
  * simulations/simulation_rtlwmbus_errors.txt and tests/test_pipe.sh - rtl_wmbus lines
https://github.com/wmbusmeters/wmbusmeters (GPL-3.0; only the hex data is used as test input)
"""
import calendar
import json

import pytest

from ionos_tools import wmbus

SONTEX_WITH_CRC = bytes.fromhex(
    "3444EE4D813929271608" "811D"
    "7A51000000046D1912A62B036E000000" "5170"
    "426CE1F1436E00000002FF2C00000259" "D6D0"
    "D4090265FC0902FD66A000" "44C4")

APATOR_WITH_CRC = bytes.fromhex(
    "734414867CC30B000303" "0D58"
    "A00EDF0700DC41343CEA390306FF0A0D" "44EA"
    "DF07000DDF07000DDF07000DDF07000E" "44F9"
    "DF07000EDF07000EDF07000EDF07000E" "931E"
    "DF07000DDF07000DDF07000DDF070003" "5A45"
    "00000000374F0B20000000000000243412B31C07050C12280000000000002C002302062F19080F2F000000000000DCF5")

SENSOSTAR_FRAME_B = bytes.fromhex(
    "C244C5140443054300048C200E900F002C25895E02003B0877D31DA840B17A0E009007100F8FAD4C4E61400DA4F3B1F7E27"
    "3CC4C5C07F93CD20B237345DB39F9A98F8415E1C4B7A25C86E4C22D05C3352FA7E2E5627031F14643063F1DC8FE2C32459B6"
    "F02E4234B447B18E4BFDA8C4DA9B1EDE6CF29D0B2122A3E8B9DEEBEE3E6DBE6617B4E709EC6B595179CB01C2C0BDE6723A34"
    "5B708652880C114A58DA07C73AD9EB8ACB17FA0A88F19BC84CB1FC56672DE8A9603FD0C05010002FD0B2111F60B")

TECHEM_NO_CRC = bytes.fromhex(
    "574468506775720394088c0048900f002c25b3290900aa7c765a7dfed6737ab300300710bcabff50afff489e689f834a0b"
    "d9578ad52fb85f17854d3d5d522cbec020526a6242e2345a48214088a6a9b8ebfd47f80f15a3b4")


def test_crc16_known_blocks():
    assert wmbus.crc16(bytes.fromhex("3444EE4D813929271608")) == 0x811D
    assert wmbus.crc16(bytes.fromhex("7A51000000046D1912A62B036E000000")) == 0x5170
    assert wmbus.crc16(bytes.fromhex("D4090265FC0902FD66A000")) == 0x44C4
    assert wmbus.crc16(bytes.fromhex("734414867CC30B000303")) == 0x0D58


def test_frame_a_validate_and_strip():
    assert wmbus.frame_a_crcs_valid(SONTEX_WITH_CRC)
    assert not wmbus.frame_b_crcs_valid(SONTEX_WITH_CRC)
    out = wmbus.strip_crcs(SONTEX_WITH_CRC)
    assert out is not None
    assert out[0] == len(out) - 1 == 0x34          # L unchanged for frame A
    assert out.hex().upper() == ("3444EE4D813929271608" "7A51000000046D1912A62B036E000000"
                                 "426CE1F1436E00000002FF2C00000259" "D4090265FC0902FD66A000")
    # corrupting a byte must break it
    bad = bytearray(SONTEX_WITH_CRC)
    bad[20] ^= 0x01
    assert not wmbus.frame_a_crcs_valid(bytes(bad))
    assert wmbus.strip_crcs(bytes(bad)) is None


def test_frame_a_long_telegram():
    assert len(APATOR_WITH_CRC) == 132
    out = wmbus.strip_crcs(APATOR_WITH_CRC)
    assert out is not None and out[0] == 0x73 and len(out) == 0x74


def test_frame_b_validate_and_strip():
    assert len(SENSOSTAR_FRAME_B) == 0xC2 + 1 == 195
    assert wmbus.frame_b_crcs_valid(SENSOSTAR_FRAME_B)
    assert not wmbus.frame_a_crcs_valid(SENSOSTAR_FRAME_B)
    out = wmbus.strip_crcs(SENSOSTAR_FRAME_B, frame_b=True)
    assert out is not None
    assert len(out) == 195 - 4                    # two CRCs removed
    assert out[0] == len(out) - 1                 # L rewritten to frame-A convention
    assert out[1:126] == SENSOSTAR_FRAME_B[1:126]
    assert out[126:] == SENSOSTAR_FRAME_B[128:-2]
    # auto-detect picks frame B
    assert wmbus.strip_crcs(SENSOSTAR_FRAME_B) == out


def test_already_stripped_passthrough():
    assert wmbus.strip_crcs(TECHEM_NO_CRC) == TECHEM_NO_CRC
    assert wmbus.strip_crcs(TECHEM_NO_CRC[:-1]) is None


def test_header_fields():
    h = wmbus.parse_header(wmbus.strip_crcs(SONTEX_WITH_CRC))
    assert h.manufacturer == "SON" and h.manufacturer_id == 0x4DEE
    assert h.ident == "27293981" and h.version == 0x16 and h.device_type == 0x08
    assert h.c_field == 0x44 and h.ci == 0x7A and h.acc == 0x51 and h.status == 0x00

    h = wmbus.parse_header(wmbus.strip_crcs(APATOR_WITH_CRC))
    assert h.manufacturer == "APT" and h.ident == "000BC37C" and h.device_type == 0x03

    h = wmbus.parse_header(TECHEM_NO_CRC)
    assert h.manufacturer == "TCH" and h.manufacturer_id == 0x5068 and h.ident == "03727567"
    assert h.version == 0x94 and h.device_type == 0x08 and h.ci == 0x8C
    assert h.ell_cc == 0x00 and h.acc == 0x48

    h = wmbus.parse_header(wmbus.strip_crcs(SENSOSTAR_FRAME_B))
    assert h.manufacturer == "EFE" and h.manufacturer_id == 0x14C5 and h.ident == "43054304" and h.ci == 0x8C and h.acc == 0x0E


def test_acc_tracker_counts_missed_and_duplicates():
    tr = wmbus.AccTracker()
    base = bytearray(wmbus.strip_crcs(SONTEX_WITH_CRC))

    def tele(acc):
        b = bytearray(base)
        b[11] = acc & 0xFF
        return bytes(b)

    t0 = 1000.0
    for i, acc in enumerate([10, 11, 12, 14, 14, 20, 21]):     # 13 missing, 14 dup, 15..19 missing
        tr.update(tele(acc), rssi_dbm=-70 - i, ts=t0 + i, mode="T1")
    s = tr.summary()["meters"]
    assert len(s) == 1
    m = s[0]
    assert m["id"] == "27293981" and m["manufacturer"] == "SON"
    assert m["received"] == 6 and m["duplicates"] == 1 and m["missed"] == 6
    assert m["per"] == pytest.approx(6 / 12)
    assert m["modes"] == {"T1": 7}
    assert m["last_acc"] == 21
    # 21 -> 255 is a jump of 233 > max_gap: a reset, not loss; 255 -> 0 wraps as one step
    tr.update(tele(255), ts=t0 + 10)
    tr.update(tele(0), ts=t0 + 11)
    ms = tr.meters[m["key"]]
    assert ms.missed == 6 and ms.resets == 1 and ms.last_acc == 0 and ms.received == 8
    json.dumps(tr.summary())


def test_rtlwmbus_line_format():
    frame = wmbus.strip_crcs(SONTEX_WITH_CRC)
    ts = calendar.timegm((2019, 4, 3, 19, 0, 42, 0, 0, 0)) + 0.5   # wmbusmeters uses timegm(): UTC
    line = wmbus.rtlwmbus_line(frame, "T1", -73, ts)
    f = line.split(";")
    assert f[0] == "T1" and f[1] == "1" and f[2] == "1"
    assert f[3] == "2019-04-03 19:00:42.500"
    assert f[4] == "-73" and len(f[4]) < 5                     # wmbusmeters: atof only if < 5 chars
    assert f[6] == "27293981"
    assert f[7] == "0x" + frame.hex()
    # same shape as the lines in wmbusmeters' own tests
    ref = "T1;1;1;2019-04-03 19:00:42.000;97;148;33225544;0x1844AE4C4455223368077A55000000041389E20100023B0000"
    assert len(ref.split(";")) == len(f)
    assert line[3] == "1" and line[5] == "1"                   # checked by character index in wmbusmeters
