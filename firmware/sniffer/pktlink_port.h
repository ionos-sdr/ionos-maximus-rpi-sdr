/* SPDX-License-Identifier: MIT
 *
 * pktlink_port.h — FG23-side API of the PKT1 block transport (pktlink.c)
 *   Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
 *
 * The wire format is in pktlink.h (shared with the host). This header is
 * firmware-only.
 */

#ifndef PKTLINK_PORT_H
#define PKTLINK_PORT_H

#include "pktlink.h"
#include <stdbool.h>
#include <stdint.h>

typedef enum {
  PKTLINK_MODE_UART = 0,   /* 1040-byte blocks on the VCOM iostream */
  PKTLINK_MODE_SPI  = 1    /* SPI secondary + DRDY (README mode A)   */
} pktlink_mode_t;

/* From app_init(): drives DRDY low, clears the buffers. No transport yet. */
void pktlink_init(void);

/* Select the transport. Switching to SPI configures USART0 secondary and
 * the LDMA; switching away tears it down and leaves DRDY low. */
void pktlink_set_mode(pktlink_mode_t mode);
pktlink_mode_t pktlink_mode(void);

/* Current centre frequency, copied into every block header. */
void pktlink_set_center(uint32_t f_hz);

/* Queue one packet record. rh->len bytes are copied from psdu.
 * Returns false (and counts a drop) when no buffer space is left. */
bool pktlink_put(const pktlink_rec_hdr_t *rh, const uint8_t *psdu);

/* From the main loop: moves completed blocks onto the wire. */
void pktlink_pump(void);

/* Counters for the 's' command. */
void pktlink_stats(uint32_t *blocks, uint32_t *blk_drops, uint32_t *rec_drops);
uint32_t pktlink_drops(void);

#endif /* PKTLINK_PORT_H */
