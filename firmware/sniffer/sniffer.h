/* SPDX-License-Identifier: MIT
 *
 * sniffer.h — multi-PHY packet sniffer core on RAIL (EFR32FG23)
 *   Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
 *
 * One radio, one of several precompiled PHYs at a time (pktlink_phy_t),
 * every received frame delivered with RSSI, LQI, RAIL timestamp, PHY and
 * sub-PHY id, CRC status and a sequence counter, through one of three
 * output formats (sniffer_out_t) on one of two transports (pktlink).
 */

#ifndef SNIFFER_H
#define SNIFFER_H

#include "rail.h"
#include "pktlink.h"
#include <stdbool.h>
#include <stdint.h>

#define SNIFFER_VERSION   "0.1.0"
#define SNIFFER_MAX_HOPS  16u

typedef enum {
  SNIFFER_OUT_TEXT     = 0,   /* one "P;..." line per packet on VCOM        */
  SNIFFER_OUT_RTLWMBUS = 1,   /* rtl_wmbus-compatible lines for wmbusmeters */
  SNIFFER_OUT_BINARY   = 2    /* PKT1 blocks via pktlink (UART or SPI)      */
} sniffer_out_t;

/* Static description of one PHY entry. */
typedef struct {
  const char *name;          /* command-line name, e.g. "wmbus_tc" */
  uint8_t     cfg_index;     /* index into the generated channelConfigs[] */
  uint8_t     kind;          /* SNIFFER_KIND_* */
  uint32_t    base_hz;       /* channel 0 frequency of the entry */
  uint32_t    spacing_hz;    /* channel spacing of the entry */
  uint16_t    ch_max;        /* highest channel number of the entry */
  uint32_t    default_hz;    /* where to tune when the PHY is selected */
  uint32_t    bitrate_bps;   /* informational, goes into the pcap TAP */
} sniffer_phy_desc_t;

#define SNIFFER_KIND_WMBUS_TC  0
#define SNIFFER_KIND_WMBUS_A   1   /* single-mode wM-Bus, frame A */
#define SNIFFER_KIND_WMBUS_B   2   /* single-mode wM-Bus, frame B */
#define SNIFFER_KIND_154G      3
#define SNIFFER_KIND_FSK       4

/* From app_init(), after RAIL is up. Selects PKT_PHY_WMBUS_TC. */
void sniffer_init(RAIL_Handle_t rail);

/* From sl_rail_util_on_event(). ISR context: only flags and holds. */
void sniffer_on_event(RAIL_Handle_t rail, RAIL_Events_t events);

/* From the main loop: drains held packets, formats and outputs them,
 * pumps the pktlink transport. */
void sniffer_process(void);

/* --- configuration (all restart RX as needed) --- */
bool sniffer_set_phy(pktlink_phy_t phy);
pktlink_phy_t sniffer_phy(void);
const sniffer_phy_desc_t *sniffer_phy_desc(pktlink_phy_t phy);
bool sniffer_phy_by_name(const char *name, pktlink_phy_t *out);

bool sniffer_tune_hz(uint32_t hz);
uint32_t sniffer_freq_hz(void);

/* Channel hopping: list of frequencies with a common dwell. n = 0 stops. */
bool sniffer_set_hop(const uint32_t *hz, uint8_t n, uint32_t dwell_ms);

/* Generic-FSK runtime options (ignored for wM-Bus / 802.15.4g). */
bool sniffer_set_sync(uint32_t word, uint8_t bits);
bool sniffer_set_fixed_len(uint16_t len);     /* 0 = variable (PHY default) */

/* Keep CRC-failed frames (flagged PKT_F_CRC_OK = 0) instead of dropping. */
void sniffer_set_keep_bad_crc(bool keep);

/* Filters. addr: match n bytes at PSDU offset off (n = 0 clears). */
void sniffer_set_addr_filter(uint8_t off, const uint8_t *bytes, uint8_t n);
void sniffer_set_len_filter(uint16_t min_len, uint16_t max_len);

void sniffer_set_output(sniffer_out_t out);
sniffer_out_t sniffer_output(void);

/* Wall clock for rtl_wmbus lines: UNIX seconds "now". 0 = uptime since
 * 1970-01-01, which wmbusmeters still accepts. */
void sniffer_set_epoch(uint32_t unix_now);

/* Statistics and reset. */
void sniffer_print_stats(void);
void sniffer_reset_stats(void);

#endif /* SNIFFER_H */
