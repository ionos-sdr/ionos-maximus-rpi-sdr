/* SPDX-License-Identifier: MIT
 *
 * backend_spi.c — spidev + libgpiod DRDY backend (Raspberry Pi, mode A)
 *   Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
 *
 * One block per DRDY rising edge: the FG23 loads a 1040-byte block into
 * its LDMA and raises DRDY; we clock the block out with one SPI_IOC_MESSAGE
 * (the kernel driver uses DMA above a few bytes on the Pi), the FG23 sees
 * CS release and drops DRDY. Without libgpiod (IONOS_HAVE_GPIOD undefined)
 * the backend polls: it reads a block every poll interval and discards
 * blocks without a valid magic (the idle FG23 shifts zeros).
 *
 * Pi setup: dtoverlay=spi0-1cs (rev A) or spi0-2cs (rev B), the PL011 on
 * GPIO14 for cmdlink (dtoverlay=disable-bt). spidev buffer size: the
 * default bufsiz is 4096, enough for one block; set
 * spidev.bufsiz=65536 on the kernel command line for larger transfers.
 *
 * libgpiod: written against the v1 API (1.6, Raspberry Pi OS bookworm,
 * Ubuntu 24.04). v2 (2.x) has a different API; see the #if below.
 */

#define _GNU_SOURCE
#include "ionos_internal.h"
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

#ifdef __linux__
#include <linux/spi/spidev.h>
#endif
#ifdef IONOS_HAVE_GPIOD
#include <gpiod.h>
#endif

typedef struct {
  int spi_fd;
  int cmd_fd;
#ifdef IONOS_HAVE_GPIOD
  struct gpiod_chip *chip;
  struct gpiod_line *line;
#endif
} spi_priv_t;

static int spi_xfer_block(spi_priv_t *p, uint8_t *blk)
{
#ifdef __linux__
  uint8_t tx[IONOS_BLK_BYTES];
  memset(tx, 0, sizeof tx);
  struct spi_ioc_transfer tr;
  memset(&tr, 0, sizeof tr);
  tr.tx_buf = (unsigned long)tx;
  tr.rx_buf = (unsigned long)blk;
  tr.len = IONOS_BLK_BYTES;
  if (ioctl(p->spi_fd, SPI_IOC_MESSAGE(1), &tr) < 1) return IONOS_E_IO;
  return 1;
#else
  (void)p; (void)blk;
  return IONOS_E_UNSUPPORTED;
#endif
}

static bool magic_ok(const uint8_t *blk)
{
  uint32_t m; memcpy(&m, blk, 4);
  return m == PKTLINK_IQB2_MAGIC || m == PKTLINK_MAGIC || m == PKTLINK_SPC1_MAGIC;
}

static int spi_read(struct ionos_dev *d, uint8_t *blk, int timeout_ms)
{
  spi_priv_t *p = d->be.priv;
#ifdef IONOS_HAVE_GPIOD
  if (p->line) {
    /* Level first: a block may already be waiting (edge missed while we
     * were busy), then wait for the next rising edge. */
    if (gpiod_line_get_value(p->line) != 1) {
      struct timespec ts = { timeout_ms / 1000, (long)(timeout_ms % 1000) * 1000000L };
      int r = gpiod_line_event_wait(p->line, &ts);
      if (r < 0) return IONOS_E_IO;
      if (r == 0) return 0;
      struct gpiod_line_event ev;
      gpiod_line_event_read(p->line, &ev);
    }
    int rc = spi_xfer_block(p, blk);
    if (rc != 1) return rc;
    return magic_ok(blk) ? 1 : IONOS_E_IO;
  }
#endif
  /* polling fallback */
  struct timespec ts = { 0, (long)(timeout_ms < 5 ? timeout_ms : 5) * 1000000L };
  nanosleep(&ts, NULL);
  int rc = spi_xfer_block(p, blk);
  if (rc != 1) return rc;
  return magic_ok(blk) ? 1 : 0;
}

static int spi_command(struct ionos_dev *d, const char *line)
{
  spi_priv_t *p = d->be.priv;
  if (p->cmd_fd < 0) return IONOS_E_UNSUPPORTED;
  size_t n = strlen(line);
  if (write(p->cmd_fd, line, n) != (ssize_t)n) return IONOS_E_IO;
  if (write(p->cmd_fd, "\n", 1) != 1) return IONOS_E_IO;
  return IONOS_OK;
}

static void spi_close(struct ionos_dev *d)
{
  spi_priv_t *p = d->be.priv;
  if (!p) return;
#ifdef IONOS_HAVE_GPIOD
  if (p->line) gpiod_line_release(p->line);
  if (p->chip) gpiod_chip_close(p->chip);
#endif
  if (p->spi_fd >= 0) close(p->spi_fd);
  if (p->cmd_fd >= 0) close(p->cmd_fd);
  free(p);
  d->be.priv = NULL;
}

static int open_cmdlink(const char *path)
{
  int fd = open(path, O_WRONLY | O_NOCTTY | O_NONBLOCK);
  if (fd < 0) return -1;
  struct termios t;
  if (tcgetattr(fd, &t) == 0) {
    cfmakeraw(&t);
    cfsetospeed(&t, B115200);
    cfsetispeed(&t, B115200);
    t.c_cflag |= CLOCAL | CREAD;
    tcsetattr(fd, TCSANOW, &t);
  }
  return fd;
}

int ionos_backend_spi_open(struct ionos_dev *d)
{
  spi_priv_t *p = calloc(1, sizeof *p);
  if (!p) return IONOS_E_NOMEM;
  p->spi_fd = -1; p->cmd_fd = -1;

  p->spi_fd = open(d->cfg.path ? d->cfg.path : "/dev/spidev0.0", O_RDWR);
  if (p->spi_fd < 0) { free(p); return IONOS_E_IO; }
#ifdef __linux__
  uint8_t mode = SPI_MODE_0, bits = 8;
  uint32_t hz = d->cfg.spi_hz ? d->cfg.spi_hz : 4000000u;
  ioctl(p->spi_fd, SPI_IOC_WR_MODE, &mode);
  ioctl(p->spi_fd, SPI_IOC_WR_BITS_PER_WORD, &bits);
  ioctl(p->spi_fd, SPI_IOC_WR_MAX_SPEED_HZ, &hz);
#endif

#ifdef IONOS_HAVE_GPIOD
  if (d->cfg.drdy_chip) {
    p->chip = gpiod_chip_open_by_name(d->cfg.drdy_chip);
    if (p->chip) {
      p->line = gpiod_chip_get_line(p->chip, (unsigned)d->cfg.drdy_line);
      if (p->line && gpiod_line_request_rising_edge_events(p->line, "libionos") != 0) {
        p->line = NULL;
      }
    }
    if (!p->line) fprintf(stderr, "libionos: DRDY %s:%d unavailable, polling\n",
                          d->cfg.drdy_chip, d->cfg.drdy_line);
  }
#endif
  if (d->cfg.cmd_path) p->cmd_fd = open_cmdlink(d->cfg.cmd_path);

  d->be.priv = p;
  d->be.read_block = spi_read;
  d->be.command = spi_command;
  d->be.close = spi_close;
  return IONOS_OK;
}
