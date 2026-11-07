/* SPDX-License-Identifier: MIT
 *
 * test_wmbus_frame.c — the firmware's wmbus_frame.c on the host
 *   Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
 *
 * Vectors: wmbusmeters test suite (simulation_dll_crcs.txt,
 * test_frame_b_trim.sh), public telegrams; see host/tests/test_wmbus.py.
 */
#include "wmbus_frame.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK(c) do { if (!(c)) { fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); return 1; } } while (0)

static size_t unhex(const char *s, uint8_t *out)
{
  size_t n = 0;
  while (s[0] && s[1]) { unsigned v; sscanf(s, "%2x", &v); out[n++] = (uint8_t)v; s += 2; }
  return n;
}

static const char *SONTEX =
  "3444EE4D813929271608811D7A51000000046D1912A62B036E0000005170426CE1F1436E00000002FF2C00000259D6D0D4090265FC0902FD66A00044C4";
static const char *SENSOSTAR_B =
  "C244C5140443054300048C200E900F002C25895E02003B0877D31DA840B17A0E009007100F8FAD4C4E61400DA4F3B1F7E27"
  "3CC4C5C07F93CD20B237345DB39F9A98F8415E1C4B7A25C86E4C22D05C3352FA7E2E5627031F14643063F1DC8FE2C32459B6"
  "F02E4234B447B18E4BFDA8C4DA9B1EDE6CF29D0B2122A3E8B9DEEBEE3E6DBE6617B4E709EC6B595179CB01C2C0BDE6723A34"
  "5B708652880C114A58DA07C73AD9EB8ACB17FA0A88F19BC84CB1FC56672DE8A9603FD0C05010002FD0B2111F60B";
static const char *TECHEM_NOCRC =
  "574468506775720394088c0048900f002c25b3290900aa7c765a7dfed6737ab300300710bcabff50afff489e689f834a0b"
  "d9578ad52fb85f17854d3d5d522cbec020526a6242e2345a48214088a6a9b8ebfd47f80f15a3b4";

int main(void)
{
  uint8_t buf[300];
  uint8_t t[10] = { 0x34,0x44,0xEE,0x4D,0x81,0x39,0x29,0x27,0x16,0x08 };
  CHECK(wmbus_crc16(t, 10) == 0x811D);

  size_t n = unhex(SONTEX, buf);
  CHECK(n == 61);
  CHECK(wmbus_frame_a_has_valid_crcs(buf, n));
  CHECK(!wmbus_frame_b_has_valid_crcs(buf, n));
  size_t m = wmbus_strip_crcs(buf, n, false);
  CHECK(m == 53 && buf[0] == 0x34);
  CHECK(buf[10] == 0x7A && buf[11] == 0x51);                /* CI, ACC follow block 1 directly */
  CHECK(buf[m - 1] == 0x00 && buf[m - 2] == 0xA0);           /* ... 66 A0 00 */
  /* corrupt -> reject */
  n = unhex(SONTEX, buf); buf[20] ^= 1;
  CHECK(wmbus_strip_crcs(buf, n, false) == 0);

  n = unhex(SENSOSTAR_B, buf);
  CHECK(n == 195);
  CHECK(wmbus_frame_b_has_valid_crcs(buf, n));
  CHECK(!wmbus_frame_a_has_valid_crcs(buf, n));
  uint8_t ref[300]; memcpy(ref, buf, n);
  m = wmbus_strip_crcs(buf, n, true);
  CHECK(m == 191 && buf[0] == 190);
  CHECK(memcmp(buf + 1, ref + 1, 125) == 0);
  CHECK(memcmp(buf + 126, ref + 128, 191 - 126) == 0);

  n = unhex(TECHEM_NOCRC, buf);
  CHECK(wmbus_strip_crcs(buf, n, false) == n);               /* passthrough */
  CHECK(wmbus_strip_crcs(buf, n - 1, false) == 0);

  char mf[4];
  wmbus_manuf_str(0x4DEE, mf); CHECK(strcmp(mf, "SON") == 0);
  wmbus_manuf_str(0x5068, mf); CHECK(strcmp(mf, "TCH") == 0);
  wmbus_manuf_str(0x14C5, mf); CHECK(strcmp(mf, "EFE") == 0);

  printf("test_wmbus_frame: OK\n");
  return 0;
}
