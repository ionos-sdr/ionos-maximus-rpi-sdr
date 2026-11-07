/* SPDX-License-Identifier: MIT
 * ionos_internal.h — Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs */
#ifndef IONOS_INTERNAL_H
#define IONOS_INTERNAL_H

#include "ionos.h"
#include "pktlink.h"
#include <pthread.h>
#include <stdbool.h>

/* IQB2 block header as written by iq_stream.c (16 bytes). */
typedef struct __attribute__((packed)) {
  uint32_t magic;      /* 'I','Q','B','2' */
  uint32_t seq;
  uint16_t nsamp;      /* 256 */
  uint16_t decim;
  uint32_t fs_in_hz;
} iqb2_hdr_t;

typedef struct __attribute__((packed)) {
  iqb2_hdr_t hdr;
  int16_t    iq[IONOS_BLK_SAMPLES * 2];
} iqb2_blk_t;

typedef char iqb2_size_check[(sizeof(iqb2_blk_t) == IONOS_BLK_BYTES) ? 1 : -1];

struct ionos_backend {
  /* Blocking read of exactly one block; returns 1 = got block, 0 = timeout
   * (nothing ready), IONOS_E_EOF at end of a replay, <0 error. */
  int  (*read_block)(struct ionos_dev *dev, uint8_t *blk, int timeout_ms);
  int  (*command)(struct ionos_dev *dev, const char *line);
  void (*close)(struct ionos_dev *dev);
  void *priv;
};

struct ionos_dev {
  ionos_config_t        cfg;
  struct ionos_backend  be;

  /* ring of IQB2 blocks */
  iqb2_blk_t  *ring;
  size_t       ring_n;
  size_t       rd, wr;          /* block indices (monotonic, masked by %) */
  size_t       rd_off;          /* samples already consumed from ring[rd] */
  pthread_mutex_t mtx;
  pthread_cond_t  cv;

  pthread_t    thread;
  bool         running;
  bool         eof;

  bool         have_seq;
  uint32_t     last_seq;
  ionos_stats_t st;

  ionos_block_cb_t pkt_cb;  void *pkt_user;
  ionos_block_cb_t spec_cb; void *spec_user;
};

int ionos_backend_replay_open(struct ionos_dev *dev);
int ionos_backend_spi_open(struct ionos_dev *dev);

#endif
