/* SPDX-License-Identifier: MIT
 *
 * pktlink.h — SHARED block format of the packet-sniffer output (PKT1)
 *   Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
 *
 * THE SAME FILE lives in the FG23 firmware (firmware/sniffer/) and is the
 * normative description for the Raspberry Pi side (host/ionos_tools/
 * pktlink.py, host/libionos/). If it changes, change BOTH.
 *
 * PRINCIPLE (same as specline.h): a PKT1 block is a 1040-byte block of
 * EXACTLY the same size and framing (CS frame, DRDY, LDMA) as the IQB2
 * I/Q block. The Pi SPI DMA path, the reorder buffer and the DRDY
 * handshake therefore work UNCHANGED - only the magic differs, and the
 * host branches on the magic.
 *
 * Header (16 B, the iq_blk_hdr_t fields at the SAME offsets, different
 * meaning):
 *   magic     'P','K','T','1'  (0x31544B50 LE)
 *   seq       block sequence number (written at send time, as for IQ)
 *   nbytes    number of payload bytes used (<= PKTLINK_PAYLOAD_BYTES)
 *   flags     bit0 = last chunk of a multi-block record (always 1 today)
 *             bit8..15 = chunk index (always 0 today)
 *   f_center  CURRENT TUNED FREQUENCY of the sniffer in Hz
 * Payload (1024 B): zero or more packet RECORDS back to back, each
 *   pkt_rec_hdr_t (24 B) + psdu[len] + zero padding to a 4-byte boundary.
 *   Unused tail bytes are zero.
 *
 * Record header (24 B, little-endian):
 *   len       u16   PSDU bytes that follow (0..PKTLINK_MAX_PSDU)
 *   phy       u8    PHY id (pktlink_phy_t)
 *   subphy    u8    RAIL subPhyId (wM-Bus: 0 = T frame A, 2 = C frame A,
 *                   3 = C frame B; 0 otherwise)
 *   flags     u8    PKT_F_* bits
 *   rssi_dbm  i8    RSSI in dBm (RAIL_RxPacketDetails_t::rssi)
 *   lqi       u8    LQI 0..255
 *   hop_idx   u8    index into the hop list (0xFF = not hopping)
 *   ts_us     u32   RAIL timestamp of the SYNC WORD END in us (wraps)
 *   pkt_seq   u32   per-packet sequence counter (gap = host-side loss)
 *   freq_hz   u32   channel frequency the packet was received on
 *   channel   u16   RAIL channel number
 *   drops     u16   low 16 bits of the firmware drop counter
 *
 * UART binary mode sends the identical 1040-byte blocks; the receiver
 * resynchronises on the magic. A block is sent as soon as it holds at
 * least one record and the transport is idle, so latency is one packet.
 */

#ifndef PKTLINK_H
#define PKTLINK_H

#include <stdint.h>

#define PKTLINK_MAGIC          0x31544B50u      /* 'P','K','T','1' */
#define PKTLINK_BLK_BYTES      1040u
#define PKTLINK_HDR_BYTES      16u
#define PKTLINK_PAYLOAD_BYTES  (PKTLINK_BLK_BYTES - PKTLINK_HDR_BYTES)  /* 1024 */
#define PKTLINK_REC_HDR_BYTES  24u
#define PKTLINK_MAX_PSDU       (PKTLINK_PAYLOAD_BYTES - PKTLINK_REC_HDR_BYTES) /* 1000 */
#define PKTLINK_FLAG_LAST      0x0001u

/* For reference: the IQB2 block of the I/Q firmware uses this magic. The
 * host demultiplexes IQB2 / SPC1 / PKT1 on the first 4 bytes. */
#define PKTLINK_IQB2_MAGIC     0x32425149u      /* 'I','Q','B','2' */
#define PKTLINK_SPC1_MAGIC     0x31435053u      /* 'S','P','C','1' */

/* PHY ids. The order is the order of the protocol entries in
 * config/rail/radio_settings.radioconf, i.e. the index into the generated
 * channelConfigs[] (see sniffer.c, PHY_TABLE). Keep both in sync. */
typedef enum {
  PKT_PHY_WMBUS_TC    = 0,   /* wM-Bus T+C M2O 100 kbps, frame A (+ C frame B via RAIL_WMBUS_Config) */
  PKT_PHY_WMBUS_S     = 1,   /* wM-Bus S 32.768 kbps Manchester, frame A */
  PKT_PHY_WMBUS_C_FB  = 2,   /* wM-Bus C M2O 100 kbps, frame B only */
  PKT_PHY_154G        = 3,   /* IEEE 802.15.4g SUN-FSK 50 kbps, EU 863-870 */
  PKT_PHY_FSK_2K4     = 4,   /* generic 2-FSK presets, see sniffer.c */
  PKT_PHY_FSK_4K8     = 5,
  PKT_PHY_FSK_9K6     = 6,
  PKT_PHY_FSK_38K4    = 7,
  PKT_PHY_FSK_50K     = 8,
  PKT_PHY_FSK_100K    = 9,
  PKT_PHY_COUNT       = 10
} pktlink_phy_t;

/* Record flags */
#define PKT_F_CRC_OK      0x01u   /* RAIL reported the CRC / frame check as passed */
#define PKT_F_TRUNCATED   0x02u   /* PSDU longer than PKTLINK_MAX_PSDU, cut */
#define PKT_F_HAS_PHR     0x04u   /* 802.15.4g: first 2 PSDU bytes are the PHR */
#define PKT_F_HAS_FCS     0x08u   /* FCS / CRC bytes are included in the PSDU */
#define PKT_F_WMBUS_CRCS  0x10u   /* wM-Bus: per-block DLL CRCs present in the PSDU */
#define PKT_F_FILTERED    0x20u   /* would have been dropped by a filter (debug mode) */
#define PKT_F_HOPPING     0x40u   /* received while channel hopping */

typedef struct __attribute__((packed)) {
  uint32_t magic;
  uint32_t seq;
  uint16_t nbytes;
  uint16_t flags;
  uint32_t f_center_hz;
} pktlink_hdr_t;

typedef struct __attribute__((packed)) {
  uint16_t len;
  uint8_t  phy;
  uint8_t  subphy;
  uint8_t  flags;
  int8_t   rssi_dbm;
  uint8_t  lqi;
  uint8_t  hop_idx;
  uint32_t ts_us;
  uint32_t pkt_seq;
  uint32_t freq_hz;
  uint16_t channel;
  uint16_t drops;
} pktlink_rec_hdr_t;

typedef struct __attribute__((packed)) {
  pktlink_hdr_t hdr;
  uint8_t       payload[PKTLINK_PAYLOAD_BYTES];
} pktlink_blk_t;

/* Compile-time checks: the block is exactly the size of the IQB2 block,
 * and the record header is what the host expects. */
typedef char pktlink_blk_size_check[(sizeof(pktlink_blk_t) == PKTLINK_BLK_BYTES) ? 1 : -1];
typedef char pktlink_hdr_size_check[(sizeof(pktlink_hdr_t) == PKTLINK_HDR_BYTES) ? 1 : -1];
typedef char pktlink_rec_size_check[(sizeof(pktlink_rec_hdr_t) == PKTLINK_REC_HDR_BYTES) ? 1 : -1];

/* Bytes a record occupies in the payload (header + PSDU, padded to 4). */
#define PKTLINK_REC_BYTES(len) \
  ((PKTLINK_REC_HDR_BYTES + (uint32_t)(len) + 3u) & ~3u)

#endif /* PKTLINK_H */
