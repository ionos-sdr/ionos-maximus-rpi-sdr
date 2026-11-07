/* SPDX-License-Identifier: MIT
 *
 * backend_replay.c — file replay backend (unit tests, offline work)
 *   Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
 *
 * The file is a plain concatenation of 1040-byte blocks, exactly what a
 * `dd` of the SPI stream or the UART binary mode produces. With
 * replay_realtime the reader paces IQB2 blocks to fs_in_hz/decim so a
 * SoapySDR consumer behaves as with live hardware.
 */

#define _GNU_SOURCE
#include "ionos_internal.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef struct {
  FILE *fh;
  struct timespec next;
  bool have_next;
} replay_priv_t;

static void ts_add_ns(struct timespec *t, long ns)
{
  t->tv_nsec += ns;
  while (t->tv_nsec >= 1000000000L) { t->tv_sec++; t->tv_nsec -= 1000000000L; }
}

static int replay_read(struct ionos_dev *d, uint8_t *blk, int timeout_ms)
{
  (void)timeout_ms;
  replay_priv_t *p = d->be.priv;
  size_t n = fread(blk, 1, IONOS_BLK_BYTES, p->fh);
  if (n == 0) {
    if (d->cfg.replay_loop) { rewind(p->fh); n = fread(blk, 1, IONOS_BLK_BYTES, p->fh); }
    if (n == 0) return IONOS_E_EOF;
  }
  if (n != IONOS_BLK_BYTES) return IONOS_E_IO;

  if (d->cfg.replay_realtime) {
    uint32_t magic; memcpy(&magic, blk, 4);
    if (magic == PKTLINK_IQB2_MAGIC) {
      const iqb2_hdr_t *h = (const iqb2_hdr_t *)blk;
      uint32_t sps = h->decim ? h->fs_in_hz / h->decim : 0;
      if (sps) {
        long ns = (long)((1000000000ull * IONOS_BLK_SAMPLES) / sps);
        if (!p->have_next) { clock_gettime(CLOCK_MONOTONIC, &p->next); p->have_next = true; }
        ts_add_ns(&p->next, ns);
        clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &p->next, NULL);
      }
    }
  }
  return 1;
}

static void replay_close(struct ionos_dev *d)
{
  replay_priv_t *p = d->be.priv;
  if (p) { if (p->fh) fclose(p->fh); free(p); }
  d->be.priv = NULL;
}

int ionos_backend_replay_open(struct ionos_dev *d)
{
  if (!d->cfg.path) return IONOS_E_ARG;
  replay_priv_t *p = calloc(1, sizeof *p);
  if (!p) return IONOS_E_NOMEM;
  p->fh = fopen(d->cfg.path, "rb");
  if (!p->fh) { free(p); return IONOS_E_IO; }
  d->be.priv = p;
  d->be.read_block = replay_read;
  d->be.command = NULL;
  d->be.close = replay_close;
  return IONOS_OK;
}
