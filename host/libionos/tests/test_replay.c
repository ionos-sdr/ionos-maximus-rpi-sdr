/* SPDX-License-Identifier: MIT
 *
 * test_replay.c — libionos unit test with a synthetic IQB2/PKT1 stream
 *   Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
 */
#define _GNU_SOURCE
#include "ionos.h"
#include "ionos_internal.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define CHECK(c) do { if (!(c)) { fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); return 1; } } while (0)

static int s_pkt_blocks = 0;
static void pkt_cb(void *u, const uint8_t *blk) { (void)u; s_pkt_blocks++; uint32_t m; memcpy(&m, blk, 4); if (m != PKTLINK_MAGIC) abort(); }

static void write_iq_block(FILE *f, uint32_t seq, int16_t base)
{
  iqb2_blk_t b; memset(&b, 0, sizeof b);
  b.hdr.magic = PKTLINK_IQB2_MAGIC; b.hdr.seq = seq; b.hdr.nsamp = IONOS_BLK_SAMPLES;
  b.hdr.decim = 32; b.hdr.fs_in_hz = 1000000u;
  for (unsigned i = 0; i < IONOS_BLK_SAMPLES; i++) { b.iq[2*i] = (int16_t)(base + (int16_t)i); b.iq[2*i+1] = (int16_t)(-(base + (int16_t)i)); }
  fwrite(&b, 1, sizeof b, f);
}

static void write_pkt_block(FILE *f, uint32_t seq)
{
  pktlink_blk_t b; memset(&b, 0, sizeof b);
  b.hdr.magic = PKTLINK_MAGIC; b.hdr.seq = seq; b.hdr.nbytes = 0; b.hdr.flags = 1;
  fwrite(&b, 1, sizeof b, f);
}

int main(void)
{
  CHECK(sizeof(iqb2_blk_t) == 1040 && sizeof(pktlink_blk_t) == 1040 && sizeof(pktlink_rec_hdr_t) == 24);

  char path[] = "/tmp/ionos_replay_XXXXXX";
  int fd = mkstemp(path); CHECK(fd >= 0);
  FILE *f = fdopen(fd, "wb"); CHECK(f);
  /* blocks 0,1,2, PKT1, 5 (3 and 4 lost), 6, 7 */
  write_iq_block(f, 0, 0); write_iq_block(f, 1, 1000); write_iq_block(f, 2, 2000);
  write_pkt_block(f, 77);
  write_iq_block(f, 5, 5000); write_iq_block(f, 6, 6000); write_iq_block(f, 7, 7000);
  fclose(f);

  ionos_config_t cfg; ionos_config_init(&cfg);
  cfg.backend = "replay"; cfg.path = path; cfg.ring_blocks = 16;
  ionos_dev_t *dev = NULL;
  CHECK(ionos_open(&cfg, &dev) == IONOS_OK);
  CHECK(ionos_set_block_callback(dev, PKTLINK_MAGIC, pkt_cb, NULL) == IONOS_OK);
  CHECK(ionos_command(dev, "s") == IONOS_E_UNSUPPORTED);
  CHECK(ionos_start(dev) == IONOS_OK);

  /* read in odd chunk sizes to exercise the partial-block path */
  static int16_t out[6 * IONOS_BLK_SAMPLES * 2];
  size_t got = 0;
  for (int guard = 0; guard < 1000; guard++) {
    int n = ionos_read_iq(dev, out + got * 2, 100, 500);
    if (n == IONOS_E_EOF) break;
    CHECK(n >= 0);
    got += (size_t)n;
    if (got >= 6 * IONOS_BLK_SAMPLES) break;
  }
  CHECK(got == 6 * IONOS_BLK_SAMPLES);
  /* the sample content must be intact and in order (I first, Q = -I) */
  int16_t bases[6] = { 0, 1000, 2000, 5000, 6000, 7000 };
  for (int b = 0; b < 6; b++) for (unsigned i = 0; i < IONOS_BLK_SAMPLES; i++) {
    int16_t exp = (int16_t)(bases[b] + (int16_t)i);
    CHECK(out[(b * IONOS_BLK_SAMPLES + i) * 2] == exp);
    CHECK(out[(b * IONOS_BLK_SAMPLES + i) * 2 + 1] == (int16_t)-exp);
  }
  /* EOF after the data */
  int n = ionos_read_iq(dev, out, 10, 200);
  CHECK(n == IONOS_E_EOF);

  ionos_stats_t st; CHECK(ionos_stats(dev, &st) == IONOS_OK);
  CHECK(st.blocks == 6);
  CHECK(st.lost_blocks == 2);
  CHECK(st.pkt_blocks == 1 && s_pkt_blocks == 1);
  CHECK(st.sample_rate_hz == 31250 && st.decim == 32 && st.fs_in_hz == 1000000u);
  CHECK(st.bad_blocks == 0);
  CHECK(st.ring_overflows == 0);

  ionos_close(dev);

  /* ring overflow: 2-block ring, do not read until EOF -> oldest dropped */
  cfg.ring_blocks = 2;
  CHECK(ionos_open(&cfg, &dev) == IONOS_OK);
  CHECK(ionos_start(dev) == IONOS_OK);
  for (int guard = 0; guard < 500; guard++) {      /* let the reader hit EOF */
    CHECK(ionos_stats(dev, &st) == IONOS_OK);
    if (st.blocks == 6) break;
    usleep(2000);
  }
  ionos_stop(dev);
  CHECK(st.blocks == 6 && st.ring_overflows == 4);
  n = ionos_read_iq(dev, out, 1024, 0);
  CHECK(n == 2 * (int)IONOS_BLK_SAMPLES);
  CHECK(out[0] == 6000 && out[2 * IONOS_BLK_SAMPLES] == 7000);
  ionos_close(dev);

  unlink(path);
  printf("test_replay: OK\n");
  return 0;
}
