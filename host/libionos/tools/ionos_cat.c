/* SPDX-License-Identifier: MIT
 *
 * ionos_cat.c — stream I/Q from the FG23 (or a replay file) to stdout
 *   Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
 *
 *   ionos_cat [-b spi|replay] [-p /dev/spidev0.0|file] [-s hz] [-g gpiochip0:25]
 *             [-c /dev/ttyAMA0] [-n samples] [-q] [-r]
 *
 * Output: int16 I,Q interleaved, little-endian (what the IQB2 blocks carry).
 * Statistics go to stderr every second (M4: lost_blocks must stay 0).
 */
#define _GNU_SOURCE
#include "ionos.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static void pkt_cb(void *user, const uint8_t *blk)
{
  (void)user;
  uint32_t seq; memcpy(&seq, blk + 4, 4);
  fprintf(stderr, "ionos_cat: PKT1 block seq %u\n", seq);
}

int main(int argc, char **argv)
{
  ionos_config_t cfg; ionos_config_init(&cfg);
  long long nmax = -1; int quiet = 0, opt;
  char chip[32] = "gpiochip0";
  while ((opt = getopt(argc, argv, "b:p:s:g:c:n:qrh")) != -1) {
    switch (opt) {
      case 'b': cfg.backend = optarg; break;
      case 'p': cfg.path = optarg; break;
      case 's': cfg.spi_hz = (uint32_t)strtoul(optarg, NULL, 10); break;
      case 'g': {
        char *colon = strchr(optarg, ':');
        if (colon) { *colon = 0; snprintf(chip, sizeof chip, "%s", optarg); cfg.drdy_chip = chip; cfg.drdy_line = atoi(colon + 1); }
        else if (strcmp(optarg, "none") == 0) cfg.drdy_chip = NULL;
        break;
      }
      case 'c': cfg.cmd_path = optarg; break;
      case 'n': nmax = atoll(optarg); break;
      case 'q': quiet = 1; break;
      case 'r': cfg.replay_realtime = 1; break;
      default:
        fprintf(stderr, "usage: ionos_cat [-b spi|replay] [-p path] [-s spi_hz] [-g gpiochipN:LINE|none] [-c cmd_tty] [-n samples] [-q] [-r]\n");
        return 2;
    }
  }
  ionos_dev_t *dev;
  int rc = ionos_open(&cfg, &dev);
  if (rc) { fprintf(stderr, "ionos_open: %s\n", ionos_strerror(rc)); return 1; }
  ionos_set_block_callback(dev, 0x31544B50u, pkt_cb, NULL);
  ionos_start(dev);

  static int16_t buf[8192 * 2];
  long long total = 0; time_t last = time(NULL);
  for (;;) {
    size_t want = sizeof buf / sizeof buf[0] / 2;
    if (nmax >= 0 && (long long)want > nmax - total) want = (size_t)(nmax - total);
    if (want == 0) break;
    int n = ionos_read_iq(dev, buf, want, 500);
    if (n == IONOS_E_EOF) break;
    if (n < 0) { fprintf(stderr, "read: %s\n", ionos_strerror(n)); break; }
    if (n > 0) { fwrite(buf, sizeof(int16_t) * 2, (size_t)n, stdout); total += n; }
    time_t now = time(NULL);
    if (!quiet && now != last) {
      last = now;
      ionos_stats_t st; ionos_stats(dev, &st);
      fprintf(stderr, "ionos_cat: %llu samples, %u sps, blocks %llu lost %llu ring_ovf %llu bad %llu pkt %llu fill %u\n",
              (unsigned long long)st.samples, st.sample_rate_hz, (unsigned long long)st.blocks,
              (unsigned long long)st.lost_blocks, (unsigned long long)st.ring_overflows,
              (unsigned long long)st.bad_blocks, (unsigned long long)st.pkt_blocks, st.ring_fill_blocks);
    }
  }
  ionos_stats_t st; ionos_stats(dev, &st);
  fprintf(stderr, "ionos_cat: done, %lld samples out, %llu blocks, %llu lost\n", total,
          (unsigned long long)st.blocks, (unsigned long long)st.lost_blocks);
  ionos_close(dev);
  return 0;
}
