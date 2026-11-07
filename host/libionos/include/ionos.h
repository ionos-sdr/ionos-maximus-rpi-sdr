/* SPDX-License-Identifier: MIT
 *
 * ionos.h — libionos: Raspberry Pi I/Q (and packet) transport for ionos-maximus
 *   Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
 *
 * Mode A of the repository README: the Pi is the SPI main, the FG23 raises
 * DRDY (GPIO25, or GPIO4 for rev B radio 2) when a 1040-byte block is
 * loaded, the Pi clocks it out. Blocks are IQB2 (I/Q, 256 complex int16,
 * I first), SPC1 (spectrum line) or PKT1 (sniffer packets); libionos
 * demultiplexes on the magic, keeps I/Q in a ring buffer and hands the
 * other blocks to callbacks.
 *
 * Backends:
 *   "spi"     /dev/spidevB.C + libgpiod DRDY (polling fallback without gpiod)
 *   "replay"  a file of 1040-byte blocks, optionally paced to the sample rate
 *
 * Thread model: ionos_start() spawns a reader thread; ionos_read_iq() is
 * called from the consumer (SoapySDR, your code). Callbacks run on the
 * reader thread.
 */

#ifndef IONOS_H
#define IONOS_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define IONOS_BLK_BYTES    1040u
#define IONOS_BLK_SAMPLES  256u          /* complex samples per IQB2 block */

#define IONOS_OK            0
#define IONOS_E_ARG        -1
#define IONOS_E_IO         -2
#define IONOS_E_NOMEM      -3
#define IONOS_E_STATE      -4
#define IONOS_E_TIMEOUT    -5
#define IONOS_E_EOF        -6
#define IONOS_E_UNSUPPORTED -7

typedef struct ionos_dev ionos_dev_t;

typedef struct {
  const char *backend;       /* "spi" (default) or "replay" */
  const char *path;          /* /dev/spidev0.0 or the replay file */
  uint32_t    spi_hz;        /* SCLK; 0 = 4 MHz. M1 decides the maximum */
  const char *drdy_chip;     /* "gpiochip0"; NULL = poll (no DRDY) */
  int         drdy_line;     /* 25 (rev A, rev B radio 1) or 4 (rev B radio 2) */
  const char *cmd_path;      /* cmdlink tty (Pi TXD -> FG23 PA06), NULL = none */
  int         replay_realtime; /* replay: pace to the sample rate in the headers */
  int         replay_loop;     /* replay: wrap at EOF */
  size_t      ring_blocks;   /* I/Q ring depth in blocks; 0 = 256 (~65 k samples) */
} ionos_config_t;

typedef struct {
  uint64_t blocks;           /* IQB2 blocks consumed from the link */
  uint64_t samples;          /* complex samples delivered to the ring */
  uint64_t lost_blocks;      /* sequence gaps (FG23 sent, Pi did not get) */
  uint64_t ring_overflows;   /* blocks dropped because the consumer was slow */
  uint64_t bad_blocks;       /* unknown magic / short read */
  uint64_t pkt_blocks;       /* PKT1 blocks seen */
  uint64_t spec_blocks;      /* SPC1 blocks seen */
  uint32_t fs_in_hz;         /* from the last IQB2 header */
  uint32_t decim;            /* from the last IQB2 header */
  uint32_t sample_rate_hz;   /* fs_in_hz / decim */
  uint32_t ring_fill_blocks; /* current ring occupancy */
} ionos_stats_t;

/* Receives a whole 1040-byte block of the registered magic (PKT1, SPC1). */
typedef void (*ionos_block_cb_t)(void *user, const uint8_t *block);

/* Fill cfg with defaults ("spi", /dev/spidev0.0, 4 MHz, gpiochip0:25). */
void ionos_config_init(ionos_config_t *cfg);

int  ionos_open(const ionos_config_t *cfg, ionos_dev_t **out);
int  ionos_start(ionos_dev_t *dev);
int  ionos_stop(ionos_dev_t *dev);
void ionos_close(ionos_dev_t *dev);

/* Copy up to max_samples complex int16 samples (I,Q interleaved, I first)
 * into iq. Returns the number of complex samples, 0 on timeout,
 * IONOS_E_EOF when a replay has ended, or a negative error. */
int  ionos_read_iq(ionos_dev_t *dev, int16_t *iq, size_t max_samples, int timeout_ms);

int  ionos_stats(ionos_dev_t *dev, ionos_stats_t *st);

/* Send one command line to the FG23 over cmdlink (appends '\n').
 * IONOS_E_UNSUPPORTED without cmd_path. */
int  ionos_command(ionos_dev_t *dev, const char *line);

/* Register a callback for PKT1 or SPC1 blocks (magic as in pktlink.h). */
int  ionos_set_block_callback(ionos_dev_t *dev, uint32_t magic, ionos_block_cb_t cb, void *user);

const char *ionos_strerror(int err);
const char *ionos_version(void);

#ifdef __cplusplus
}
#endif
#endif /* IONOS_H */
