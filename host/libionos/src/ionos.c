/* SPDX-License-Identifier: MIT
 *
 * ionos.c — libionos core: reader thread, block demux, I/Q ring buffer
 *   Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
 */

#define _GNU_SOURCE
#include "ionos_internal.h"
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define IONOS_VERSION_STR "0.1.0"

const char *ionos_version(void) { return IONOS_VERSION_STR; }

const char *ionos_strerror(int err)
{
  switch (err) {
    case IONOS_OK:            return "ok";
    case IONOS_E_ARG:         return "invalid argument";
    case IONOS_E_IO:          return "I/O error";
    case IONOS_E_NOMEM:       return "out of memory";
    case IONOS_E_STATE:       return "wrong state";
    case IONOS_E_TIMEOUT:     return "timeout";
    case IONOS_E_EOF:         return "end of replay";
    case IONOS_E_UNSUPPORTED: return "not supported by this backend";
    default:                  return "unknown error";
  }
}

void ionos_config_init(ionos_config_t *cfg)
{
  memset(cfg, 0, sizeof *cfg);
  cfg->backend = "spi";
  cfg->path = "/dev/spidev0.0";
  cfg->spi_hz = 4000000u;
  cfg->drdy_chip = "gpiochip0";
  cfg->drdy_line = 25;
  cfg->ring_blocks = 256;
}

/* ---------------- block handling (reader thread) ---------------- */

static void handle_block(struct ionos_dev *d, const uint8_t *blk)
{
  uint32_t magic;
  memcpy(&magic, blk, 4);

  if (magic == PKTLINK_IQB2_MAGIC) {
    const iqb2_blk_t *b = (const iqb2_blk_t *)blk;
    if (b->hdr.nsamp != IONOS_BLK_SAMPLES) { d->st.bad_blocks++; return; }

    pthread_mutex_lock(&d->mtx);
    if (d->have_seq) {
      uint32_t gap = b->hdr.seq - d->last_seq - 1u;
      if (gap != 0u && gap < 0x40000000u) d->st.lost_blocks += gap;
    }
    d->have_seq = true;
    d->last_seq = b->hdr.seq;
    d->st.fs_in_hz = b->hdr.fs_in_hz;
    d->st.decim = b->hdr.decim;
    d->st.sample_rate_hz = b->hdr.decim ? b->hdr.fs_in_hz / b->hdr.decim : 0;
    d->st.blocks++;

    if (d->wr - d->rd >= d->ring_n) {
      /* consumer too slow: drop the OLDEST block, keep the stream fresh */
      d->rd++; d->rd_off = 0;
      d->st.ring_overflows++;
    }
    memcpy(&d->ring[d->wr % d->ring_n], b, sizeof *b);
    d->wr++;
    d->st.samples += IONOS_BLK_SAMPLES;
    d->st.ring_fill_blocks = (uint32_t)(d->wr - d->rd);
    pthread_cond_signal(&d->cv);
    pthread_mutex_unlock(&d->mtx);
  } else if (magic == PKTLINK_MAGIC) {
    d->st.pkt_blocks++;
    if (d->pkt_cb) d->pkt_cb(d->pkt_user, blk);
  } else if (magic == PKTLINK_SPC1_MAGIC) {
    d->st.spec_blocks++;
    if (d->spec_cb) d->spec_cb(d->spec_user, blk);
  } else {
    d->st.bad_blocks++;
  }
}

static void *reader_main(void *arg)
{
  struct ionos_dev *d = arg;
  uint8_t blk[IONOS_BLK_BYTES];
  while (d->running) {
    int r = d->be.read_block(d, blk, 200);
    if (r == 1) {
      handle_block(d, blk);
    } else if (r == IONOS_E_EOF) {
      pthread_mutex_lock(&d->mtx);
      d->eof = true;
      pthread_cond_broadcast(&d->cv);
      pthread_mutex_unlock(&d->mtx);
      break;
    } else if (r < 0) {
      d->st.bad_blocks++;
      struct timespec ts = { 0, 10 * 1000 * 1000 };
      nanosleep(&ts, NULL);
    }
  }
  return NULL;
}

/* ---------------- public ---------------- */

int ionos_open(const ionos_config_t *cfg, ionos_dev_t **out)
{
  if (!cfg || !out) return IONOS_E_ARG;
  struct ionos_dev *d = calloc(1, sizeof *d);
  if (!d) return IONOS_E_NOMEM;
  d->cfg = *cfg;
  d->ring_n = cfg->ring_blocks ? cfg->ring_blocks : 256;
  d->ring = calloc(d->ring_n, sizeof(iqb2_blk_t));
  if (!d->ring) { free(d); return IONOS_E_NOMEM; }
  pthread_mutex_init(&d->mtx, NULL);
  pthread_cond_init(&d->cv, NULL);

  int rc;
  const char *be = cfg->backend ? cfg->backend : "spi";
  if (strcmp(be, "replay") == 0)      rc = ionos_backend_replay_open(d);
  else if (strcmp(be, "spi") == 0)    rc = ionos_backend_spi_open(d);
  else                                rc = IONOS_E_ARG;
  if (rc != IONOS_OK) {
    free(d->ring); free(d);
    return rc;
  }
  *out = d;
  return IONOS_OK;
}

int ionos_start(ionos_dev_t *d)
{
  if (!d) return IONOS_E_ARG;
  if (d->running) return IONOS_E_STATE;
  d->running = true;
  d->eof = false;
  if (pthread_create(&d->thread, NULL, reader_main, d) != 0) {
    d->running = false;
    return IONOS_E_IO;
  }
  return IONOS_OK;
}

int ionos_stop(ionos_dev_t *d)
{
  if (!d) return IONOS_E_ARG;
  if (!d->running) return IONOS_OK;
  d->running = false;
  pthread_mutex_lock(&d->mtx);
  pthread_cond_broadcast(&d->cv);
  pthread_mutex_unlock(&d->mtx);
  pthread_join(d->thread, NULL);
  return IONOS_OK;
}

void ionos_close(ionos_dev_t *d)
{
  if (!d) return;
  ionos_stop(d);
  if (d->be.close) d->be.close(d);
  pthread_mutex_destroy(&d->mtx);
  pthread_cond_destroy(&d->cv);
  free(d->ring);
  free(d);
}

int ionos_read_iq(ionos_dev_t *d, int16_t *iq, size_t max_samples, int timeout_ms)
{
  if (!d || !iq || max_samples == 0) return IONOS_E_ARG;

  pthread_mutex_lock(&d->mtx);
  if (d->wr == d->rd) {
    if (d->eof) { pthread_mutex_unlock(&d->mtx); return IONOS_E_EOF; }
    if (timeout_ms > 0) {
      struct timespec ts;
      clock_gettime(CLOCK_REALTIME, &ts);
      ts.tv_sec  += timeout_ms / 1000;
      ts.tv_nsec += (long)(timeout_ms % 1000) * 1000000L;
      if (ts.tv_nsec >= 1000000000L) { ts.tv_sec++; ts.tv_nsec -= 1000000000L; }
      while (d->wr == d->rd && !d->eof) {
        if (pthread_cond_timedwait(&d->cv, &d->mtx, &ts) == ETIMEDOUT) break;
      }
    }
    if (d->wr == d->rd) {
      int rc = d->eof ? IONOS_E_EOF : 0;
      pthread_mutex_unlock(&d->mtx);
      return rc;
    }
  }

  size_t got = 0;
  while (got < max_samples && d->wr != d->rd) {
    const iqb2_blk_t *b = &d->ring[d->rd % d->ring_n];
    size_t avail = IONOS_BLK_SAMPLES - d->rd_off;
    size_t n = max_samples - got;
    if (n > avail) n = avail;
    memcpy(&iq[got * 2], &b->iq[d->rd_off * 2], n * 2 * sizeof(int16_t));
    got += n;
    d->rd_off += n;
    if (d->rd_off >= IONOS_BLK_SAMPLES) { d->rd++; d->rd_off = 0; }
  }
  d->st.ring_fill_blocks = (uint32_t)(d->wr - d->rd);
  pthread_mutex_unlock(&d->mtx);
  return (int)got;
}

int ionos_stats(ionos_dev_t *d, ionos_stats_t *st)
{
  if (!d || !st) return IONOS_E_ARG;
  pthread_mutex_lock(&d->mtx);
  *st = d->st;
  pthread_mutex_unlock(&d->mtx);
  return IONOS_OK;
}

int ionos_command(ionos_dev_t *d, const char *line)
{
  if (!d || !line) return IONOS_E_ARG;
  if (!d->be.command) return IONOS_E_UNSUPPORTED;
  return d->be.command(d, line);
}

int ionos_set_block_callback(ionos_dev_t *d, uint32_t magic, ionos_block_cb_t cb, void *user)
{
  if (!d) return IONOS_E_ARG;
  if (magic == PKTLINK_MAGIC)       { d->pkt_cb = cb;  d->pkt_user = user;  return IONOS_OK; }
  if (magic == PKTLINK_SPC1_MAGIC)  { d->spec_cb = cb; d->spec_user = user; return IONOS_OK; }
  return IONOS_E_ARG;
}
