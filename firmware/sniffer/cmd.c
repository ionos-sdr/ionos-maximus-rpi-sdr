/* SPDX-License-Identifier: MIT
 *
 * cmd.c — one-line text command parser of the sniffer firmware
 *   Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
 */

#include "cmd.h"
#include "sniffer.h"
#include "pktlink_port.h"
#include "cmdlink.h"
#include "sl_iostream.h"
#include "sl_iostream_handles.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int hexval(char c)
{
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

/* "2DD4" -> bytes; returns count, 0 on error. */
static unsigned parse_hex(const char *s, uint8_t *out, unsigned max)
{
  unsigned n = 0;
  while (s[0] && s[1] && n < max) {
    int hi = hexval(s[0]), lo = hexval(s[1]);
    if (hi < 0 || lo < 0) return 0;
    out[n++] = (uint8_t)((hi << 4) | lo);
    s += 2;
  }
  return (*s == '\0' || *s == ',' || *s == ';') ? n : 0;
}

static void print_help(void)
{
  printf("# ionos sniffer %s - commands:\r\n", SNIFFER_VERSION);
  printf("#  P<name|idx>  PHY (P? lists)      F<kHz>  tune\r\n");
  printf("#  H<kHz>,...;<dwell_ms>  hop list   H0      hop off\r\n");
  printf("#  Y<hex>,<bits> sync (fsk*)        L<n>    fixed length (0=var)\r\n");
  printf("#  K0|K1 keep CRC-failed            A<off>,<hex> / A  addr filter\r\n");
  printf("#  N<min>,<max> length filter       Ot|Or|Ob|Os output\r\n");
  printf("#  D<unix_s> wall clock   s stats   R reset   v version   ? help\r\n");
}

static void list_phys(void)
{
  for (unsigned i = 0; i < PKT_PHY_COUNT; i++) {
    const sniffer_phy_desc_t *d = sniffer_phy_desc((pktlink_phy_t)i);
    printf("#  %u %-9s %lu bps  default %lu Hz%s\r\n", i, d->name,
           (unsigned long)d->bitrate_bps, (unsigned long)d->default_hz,
           (i == (unsigned)sniffer_phy()) ? "  <- active" : "");
  }
}

void cmd_handle_line(const char *line)
{
  while (*line == ' ') line++;
  char c = line[0];
  const char *arg = line + 1;
  if (c == '\0' || c == '#') return;

  switch (c) {
    case 'P': {
      if (arg[0] == '?' || arg[0] == '\0') { list_phys(); break; }
      pktlink_phy_t phy;
      if (!sniffer_phy_by_name(arg, &phy)) { printf("# unknown PHY '%s'\r\n", arg); list_phys(); break; }
      sniffer_set_phy(phy);
      break;
    }
    case 'F': {
      unsigned long v = strtoul(arg, NULL, 10);
      if (v == 0) { printf("# usage: F<kHz>, e.g. F868950\r\n"); break; }
      uint32_t hz = (v > 20000000ul) ? (uint32_t)v : (uint32_t)(v * 1000ul);
      sniffer_tune_hz(hz);
      break;
    }
    case 'H': {
      uint32_t list[SNIFFER_MAX_HOPS];
      uint8_t n = 0;
      uint32_t dwell = 50;
      const char *p = arg;
      if (*p == '0' && p[1] == '\0') { sniffer_set_hop(NULL, 0, 0); printf("# hopping off\r\n"); break; }
      while (*p && n < SNIFFER_MAX_HOPS) {
        char *end;
        unsigned long v = strtoul(p, &end, 10);
        if (end == p) break;
        list[n++] = (v > 20000000ul) ? (uint32_t)v : (uint32_t)(v * 1000ul);
        p = end;
        if (*p == ',') { p++; continue; }
        if (*p == ';') { dwell = (uint32_t)strtoul(p + 1, NULL, 10); if (!dwell) dwell = 50; }
        break;
      }
      if (n < 2) { printf("# usage: H<kHz>,<kHz>[,...];<dwell_ms>\r\n"); break; }
      sniffer_set_hop(list, n, dwell);
      break;
    }
    case 'Y': {
      uint8_t b[4];
      unsigned n = parse_hex(arg, b, 4);
      const char *comma = strchr(arg, ',');
      unsigned bits = comma ? (unsigned)atoi(comma + 1) : n * 8u;
      if (!n || !bits) { printf("# usage: Y<hex>,<bits>  e.g. Y2DD4,16\r\n"); break; }
      uint32_t w = 0;
      for (unsigned i = 0; i < n; i++) w = (w << 8) | b[i];
      if (sniffer_set_sync(w, (uint8_t)bits)) printf("# sync 0x%lX / %u bits\r\n", (unsigned long)w, bits);
      break;
    }
    case 'L':
      if (sniffer_set_fixed_len((uint16_t)atoi(arg))) printf("# fixed length %d\r\n", atoi(arg));
      else printf("# length out of range\r\n");
      break;
    case 'K':
      sniffer_set_keep_bad_crc(arg[0] == '1');
      printf("# keep CRC-failed frames: %d\r\n", arg[0] == '1');
      break;
    case 'A': {
      if (arg[0] == '\0') { sniffer_set_addr_filter(0, NULL, 0); printf("# addr filter off\r\n"); break; }
      char *end;
      unsigned long off = strtoul(arg, &end, 10);
      if (*end != ',') { printf("# usage: A<off>,<hex>\r\n"); break; }
      uint8_t b[8];
      unsigned n = parse_hex(end + 1, b, 8);
      if (!n) { printf("# usage: A<off>,<hex>\r\n"); break; }
      sniffer_set_addr_filter((uint8_t)off, b, (uint8_t)n);
      printf("# addr filter: %u bytes at offset %lu\r\n", n, off);
      break;
    }
    case 'N': {
      char *end;
      unsigned long mn = strtoul(arg, &end, 10);
      unsigned long mx = (*end == ',') ? strtoul(end + 1, NULL, 10) : 0;
      sniffer_set_len_filter((uint16_t)mn, (uint16_t)mx);
      printf("# length filter %lu..%lu\r\n", mn, mx ? mx : 65535ul);
      break;
    }
    case 'O':
      switch (arg[0]) {
        case 't': pktlink_set_mode(PKTLINK_MODE_UART); sniffer_set_output(SNIFFER_OUT_TEXT);     printf("# output: text on UART\r\n"); break;
        case 'r': pktlink_set_mode(PKTLINK_MODE_UART); sniffer_set_output(SNIFFER_OUT_RTLWMBUS); printf("# output: rtl_wmbus lines on UART\r\n"); break;
        case 'b': pktlink_set_mode(PKTLINK_MODE_UART); sniffer_set_output(SNIFFER_OUT_BINARY);   printf("# output: PKT1 blocks on UART\r\n"); break;
        case 's': sniffer_set_output(SNIFFER_OUT_BINARY); pktlink_set_mode(PKTLINK_MODE_SPI);    printf("# output: PKT1 blocks on SPI\r\n"); break;
        default:  printf("# usage: Ot | Or | Ob | Os\r\n"); break;
      }
      break;
    case 'D': {
      unsigned long t = strtoul(arg, NULL, 10);
      sniffer_set_epoch((uint32_t)t);
      printf("# clock set: %lu\r\n", t);
      break;
    }
    case 's': sniffer_print_stats(); break;
    case 'R': sniffer_reset_stats(); printf("# counters reset\r\n"); break;
    case 'v': {
      uint32_t lines, errs;
      RAIL_Version_t rv;
      cmdlink_stats(&lines, &errs);
      RAIL_GetVersion(&rv, false);
      printf("# ionos-maximus sniffer %s, RAIL %u.%u.%u, cmdlink lines=%lu errors=%lu\r\n",
             SNIFFER_VERSION, (unsigned)rv.major, (unsigned)rv.minor, (unsigned)rv.rev,
             (unsigned long)lines, (unsigned long)errs);
      break;
    }
    case '?': case 'h': print_help(); break;
    default:
      printf("# unknown command '%c' - ? for help\r\n", c);
      break;
  }
}

void cmd_poll_vcom(void)
{
  static char buf[96];
  static uint8_t len = 0;
  char ch;
  /* Bounded per call: the main loop also drains packets. */
  for (int i = 0; i < 32; i++) {
    if (sl_iostream_getchar(sl_iostream_vcom_handle, &ch) != SL_STATUS_OK) return;
    if (ch == '\r' || ch == '\n') {
      if (len) { buf[len] = '\0'; len = 0; cmd_handle_line(buf); }
    } else if (len < sizeof buf - 1) {
      buf[len++] = ch;
    } else {
      len = 0;    /* overflow: drop the line */
    }
  }
}
