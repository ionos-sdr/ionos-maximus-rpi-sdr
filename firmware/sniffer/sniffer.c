/* SPDX-License-Identifier: MIT
 *
 * sniffer.c — multi-PHY packet sniffer core on RAIL (EFR32FG23)
 *   Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
 *
 * ========================= HOW IT WORKS =========================
 *
 *  radio config  : config/rail/radio_settings.radioconf holds one
 *                  protocol entry per PHY (pktlink_phy_t order). The
 *                  Radio Configurator generates channelConfigs[] from it;
 *                  sniffer_set_phy() calls RAIL_ConfigChannels() with the
 *                  entry and restarts RX. All PHYs are in flash, so a
 *                  switch costs a few hundred microseconds, no reflash.
 *  wM-Bus T+C    : the "WMbus TC M2O (100k, frameA)" PHY plus
 *                  RAIL_WMBUS_Config(rail, true) receives T frame A, C
 *                  frame A and C frame B together; the mode of each packet
 *                  is in RAIL_RxPacketDetails_t::subPhyId (0/2/3). The
 *                  separate wmbus_c_frameB entry exists because the
 *                  SINGLE-mode C PHY cannot do A and B at once (frame type
 *                  is chosen by sync word; Silicon Labs limitation page) -
 *                  it is the fallback if the TC+Config path turns out not
 *                  to deliver frame B on this part.
 *  802.15.4g     : a SUN-FSK PHY from the configurator with 2-byte PHR;
 *                  RAIL_IEEE802154_Init() in promiscuous mode so every
 *                  frame passes, G-option enabled for the PHR.
 *  reception     : packet mode. The RX_PACKET_RECEIVED event (ISR) only
 *                  calls RAIL_HoldRxPacket(); the main loop pulls the
 *                  oldest held packet, reads details (RSSI, LQI, sync-word
 *                  end timestamp, subPhyId, crcPassed), copies the bytes,
 *                  releases the packet and emits it. No printf in the ISR.
 *  CRC errors    : RAIL_RX_OPTION_IGNORE_CRC_ERRORS makes RAIL deliver
 *                  CRC-failed frames with status READY_CRC_ERROR; we flag
 *                  them (PKT_F_CRC_OK clear) and keep or drop per setting.
 *  timestamps    : RAIL_GetRxTimeSyncWordEndAlt(), microseconds, 32-bit
 *                  wrap (71 min). The host pairs it with its own clock.
 *
 * TO VERIFY ON HARDWARE (see README "To verify"): the generated
 * channelConfigs[] order; whether RAIL delivers wM-Bus frames with or
 * without the per-block CRCs (we detect both); the PHR handling of
 * 802.15.4g frames; the tick size of RAIL_SetFreqOffset at 868 MHz.
 */

#include "sniffer.h"
#include "pktlink_port.h"
#include "wmbus_frame.h"
#include "rail_config.h"
#include "rail_ieee802154.h"
#include "rail_wmbus.h"
#include "sl_rail_util_init.h"
#include <stdio.h>
#include <string.h>

/* ================= PHY table =================
 * cfg_index MUST follow the order of the protocol entries in the
 * radioconf, which is the order the generator uses for channelConfigs[].
 * Frequency grids: 25 kHz spacing, fine offset for the rest. */
#define EU_BASE_HZ      863000000u
#define EU_SPACING_HZ   25000u
#define EU_CH_MAX       280u              /* 863.000 .. 870.000 MHz */

static const sniffer_phy_desc_t PHY_TABLE[PKT_PHY_COUNT] = {
  { "wmbus_tc",  0, SNIFFER_KIND_WMBUS_TC, 868950000u, 0u,            0u,        868950000u, 100000u },
  { "wmbus_s",   1, SNIFFER_KIND_WMBUS_A,  868300000u, 0u,            0u,        868300000u,  32768u },
  { "wmbus_cb",  2, SNIFFER_KIND_WMBUS_B,  868950000u, 0u,            0u,        868950000u, 100000u },
  { "154g",      3, SNIFFER_KIND_154G,     EU_BASE_HZ, 100000u,       69u,       868100000u,  50000u },
  { "fsk2k4",    4, SNIFFER_KIND_FSK,      EU_BASE_HZ, EU_SPACING_HZ, EU_CH_MAX, 868000000u,   2400u },
  { "fsk4k8",    5, SNIFFER_KIND_FSK,      EU_BASE_HZ, EU_SPACING_HZ, EU_CH_MAX, 868000000u,   4800u },
  { "fsk9k6",    6, SNIFFER_KIND_FSK,      EU_BASE_HZ, EU_SPACING_HZ, EU_CH_MAX, 868000000u,   9600u },
  { "fsk38k4",   7, SNIFFER_KIND_FSK,      EU_BASE_HZ, EU_SPACING_HZ, EU_CH_MAX, 868000000u,  38400u },
  { "fsk50k",    8, SNIFFER_KIND_FSK,      EU_BASE_HZ, EU_SPACING_HZ, EU_CH_MAX, 868000000u,  50000u },
  { "fsk100k",   9, SNIFFER_KIND_FSK,      EU_BASE_HZ, EU_SPACING_HZ, EU_CH_MAX, 868000000u, 100000u },
};

/* Synth fine-tune resolution. The I/Q firmware measured 4.6492 Hz/tick
 * in the 144/434 MHz configurations; the 868 MHz LO divider may differ.
 * Verify with a CW source and adjust (README "To verify"). */
#define TUNE_TICK_HZ      4.6492
#define TUNE_TICK_MAX     16383

/* ================= state ================= */
static RAIL_Handle_t   s_rail = NULL;
static pktlink_phy_t   s_phy = PKT_PHY_WMBUS_TC;
static uint16_t        s_channel = 0;
static int32_t         s_tick = 0;
static uint32_t        s_freq_hz = 868950000u;
static sniffer_out_t   s_out = SNIFFER_OUT_TEXT;
static bool            s_keep_bad_crc = false;
static bool            s_154_inited = false;

/* generic FSK overrides */
static bool     s_sync_override = false;
static uint32_t s_sync_word = 0;
static uint8_t  s_sync_bits = 0;
static uint16_t s_fixed_len = 0;

/* hopping */
static uint8_t  s_hop_n = 0;
static uint32_t s_hop_hz[SNIFFER_MAX_HOPS];
static uint16_t s_hop_ch[SNIFFER_MAX_HOPS];
static uint32_t s_hop_dwell_ms = 50;
static RAIL_RxChannelHoppingConfigEntry_t s_hop_entries[SNIFFER_MAX_HOPS];
static uint32_t s_hop_buf[SNIFFER_MAX_HOPS * RAIL_CHANNEL_HOPPING_BUFFER_SIZE_PER_CHANNEL];

/* filters */
static uint8_t  s_flt_off = 0, s_flt_n = 0;
static uint8_t  s_flt_bytes[8];
static uint16_t s_flt_min = 0, s_flt_max = 0xFFFF;

/* wall clock for rtl_wmbus lines */
static uint32_t s_epoch_unix = 0;        /* UNIX seconds at s_epoch_rail */
static RAIL_Time_t s_epoch_rail = 0;

/* counters */
static volatile uint32_t s_ev_rx = 0, s_ev_err = 0, s_ev_abort = 0, s_ev_ovf = 0;
static uint32_t s_pkt_seq = 0, s_pkt_crc_bad = 0, s_pkt_filtered = 0;
static uint32_t s_pkt_out = 0, s_drops = 0;
static volatile bool s_need_restart = false;

static uint8_t s_buf[PKTLINK_MAX_PSDU + 4];

/* ================= helpers ================= */

static void start_rx(void)
{
  RAIL_Idle(s_rail, RAIL_IDLE_ABORT, true);
  if (s_hop_n > 1) {
    RAIL_EnableRxChannelHopping(s_rail, true, true);
    RAIL_StartRx(s_rail, s_hop_ch[0], NULL);
  } else {
    RAIL_StartRx(s_rail, s_channel, NULL);
  }
  /* RAIL_StartRx does not keep the fine offset: rewrite it every time
   * (lesson from the I/Q firmware). */
  RAIL_SetFreqOffset(s_rail, (RAIL_FrequencyOffset_t)s_tick);
}

/* hz -> channel + tick within the current PHY entry. */
static bool hz_to_channel(uint32_t hz, uint16_t *ch, int32_t *tick)
{
  const sniffer_phy_desc_t *d = &PHY_TABLE[s_phy];
  int32_t c = 0;
  int64_t rem;
  if (d->spacing_hz == 0u) {
    rem = (int64_t)hz - (int64_t)d->base_hz;
  } else {
    int64_t dhz = (int64_t)hz - (int64_t)d->base_hz;
    int64_t sp = d->spacing_hz;
    c = (int32_t)((dhz >= 0) ? (dhz + sp / 2) / sp : (dhz - sp / 2) / sp);
    if (c < 0) c = 0;
    if (c > (int32_t)d->ch_max) c = (int32_t)d->ch_max;
    rem = dhz - (int64_t)c * sp;
  }
  double t = (double)rem / TUNE_TICK_HZ;
  int32_t ti = (int32_t)(t + (t >= 0 ? 0.5 : -0.5));
  if (ti > TUNE_TICK_MAX || ti < -TUNE_TICK_MAX) return false;
  *ch = (uint16_t)c;
  *tick = ti;
  return true;
}

static void apply_rx_options(void)
{
  RAIL_ConfigRxOptions(s_rail, RAIL_RX_OPTION_IGNORE_CRC_ERRORS,
                       s_keep_bad_crc ? RAIL_RX_OPTION_IGNORE_CRC_ERRORS : RAIL_RX_OPTIONS_NONE);
}

static void apply_fsk_overrides(void)
{
  if (PHY_TABLE[s_phy].kind != SNIFFER_KIND_FSK) return;
  if (s_sync_override) {
    RAIL_SyncWordConfig_t sw = { .syncWordBits = s_sync_bits,
                                 .syncWord1 = s_sync_word, .syncWord2 = s_sync_word };
    RAIL_ConfigSyncWords(s_rail, &sw);
  }
  RAIL_SetFixedLength(s_rail, s_fixed_len ? s_fixed_len : RAIL_SETFIXEDLENGTH_INVALID);
}

/* ================= public: init / events ================= */

void sniffer_init(RAIL_Handle_t rail)
{
  s_rail = rail;

  RAIL_DataConfig_t dc = {
    .txSource = TX_PACKET_DATA,
    .rxSource = RX_PACKET_DATA,
    .txMethod = PACKET_MODE,
    .rxMethod = PACKET_MODE,
  };
  RAIL_ConfigData(s_rail, &dc);

  RAIL_ConfigEvents(s_rail, RAIL_EVENTS_ALL,
                    RAIL_EVENT_RX_PACKET_RECEIVED
                    | RAIL_EVENT_RX_FRAME_ERROR
                    | RAIL_EVENT_RX_PACKET_ABORTED
                    | RAIL_EVENT_RX_FIFO_OVERFLOW
                    | RAIL_EVENT_CAL_NEEDED);

  s_epoch_rail = RAIL_GetTime();
  sniffer_set_phy(PKT_PHY_WMBUS_TC);
}

void sniffer_on_event(RAIL_Handle_t rail, RAIL_Events_t events)
{
  if (events & RAIL_EVENT_RX_PACKET_RECEIVED) {
    s_ev_rx++;
    /* Keep the packet in the RAIL FIFO until the main loop reads it. */
    RAIL_HoldRxPacket(rail);
  }
  if (events & RAIL_EVENT_RX_FRAME_ERROR)    s_ev_err++;
  if (events & RAIL_EVENT_RX_PACKET_ABORTED) s_ev_abort++;
  if (events & RAIL_EVENT_RX_FIFO_OVERFLOW)  { s_ev_ovf++; s_need_restart = true; }
  if (events & RAIL_EVENT_CAL_NEEDED) {
    RAIL_Calibrate(rail, NULL, RAIL_CAL_ALL_PENDING);
  }
}

/* ================= output formatting ================= */

static const char *wmbus_mode_str(uint8_t subphy, bool *frame_b)
{
  *frame_b = false;
  switch (subphy) {
    case RAIL_WMBUS_ModeTFrameA: return "T1";
    case RAIL_WMBUS_ModeCFrameA: return "C1";
    case RAIL_WMBUS_ModeCFrameB: *frame_b = true; return "C1";
    default: return "T1";
  }
}

static void print_hex(const uint8_t *p, uint16_t n)
{
  static const char H[] = "0123456789ABCDEF";
  char line[65];
  uint16_t k = 0;
  while (n--) {
    line[k++] = H[*p >> 4];
    line[k++] = H[*p & 0xF];
    p++;
    if (k >= 64) { line[k] = '\0'; fputs(line, stdout); k = 0; }
  }
  if (k) { line[k] = '\0'; fputs(line, stdout); }
}

/* "YYYY-mm-dd HH:MM:SS.mmm" from a UNIX time in ms (UTC, proleptic
 * Gregorian). Own implementation: no <time.h> on the bare-metal target. */
static void fmt_timestamp(uint64_t unix_ms, char *out /* >= 24 */)
{
  uint64_t s = unix_ms / 1000u;
  uint32_t ms = (uint32_t)(unix_ms % 1000u);
  uint32_t days = (uint32_t)(s / 86400u);
  uint32_t rem  = (uint32_t)(s % 86400u);
  uint32_t hh = rem / 3600u, mm = (rem / 60u) % 60u, ss = rem % 60u;
  /* civil-from-days (Howard Hinnant's algorithm, public domain) */
  int64_t z = (int64_t)days + 719468;
  int64_t era = (z >= 0 ? z : z - 146096) / 146097;
  uint32_t doe = (uint32_t)(z - era * 146097);
  uint32_t yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
  int64_t y = (int64_t)yoe + era * 400;
  uint32_t doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
  uint32_t mp = (5 * doy + 2) / 153;
  uint32_t d = doy - (153 * mp + 2) / 5 + 1;
  uint32_t m = mp < 10 ? mp + 3 : mp - 9;
  if (m <= 2) y++;
  snprintf(out, 24, "%04ld-%02lu-%02lu %02lu:%02lu:%02lu.%03lu",
           (long)y, (unsigned long)m, (unsigned long)d,
           (unsigned long)hh, (unsigned long)mm, (unsigned long)ss,
           (unsigned long)ms);
}

static uint64_t wall_ms(void)
{
  uint32_t dt_us = (uint32_t)(RAIL_GetTime() - s_epoch_rail);
  return (uint64_t)s_epoch_unix * 1000u + dt_us / 1000u;
}

/* Text line: P;<seq>;<ts_us>;<phy>;<subphy>;<freq_hz>;<rssi>;<lqi>;<crc>;<drops>;<hex> */
static void emit_text(const pktlink_rec_hdr_t *rh, const uint8_t *psdu)
{
  printf("P;%lu;%lu;%s;%u;%lu;%d;%u;%u;%lu;",
         (unsigned long)rh->pkt_seq, (unsigned long)rh->ts_us,
         PHY_TABLE[rh->phy].name, (unsigned)rh->subphy,
         (unsigned long)rh->freq_hz, (int)rh->rssi_dbm, (unsigned)rh->lqi,
         (rh->flags & PKT_F_CRC_OK) ? 1u : 0u, (unsigned long)s_drops);
  print_hex(psdu, rh->len);
  printf("\r\n");
}

/* rtl_wmbus line, as parsed by wmbusmeters/src/wmbus_rtlwmbus.cc:
 *   MODE;CRC_OK;3OUTOF6OK;TIMESTAMP;PACKET_RSSI;CURRENT_RSSI;ID;0x<DATAGRAM WITHOUT CRC BYTES>
 * MODE is T1/C1/S1; the two flags must be the character '1'; the
 * timestamp must parse with "%Y-%m-%d %H:%M:%S"; the RSSI field is read
 * with atof() only when shorter than 5 characters; the payload starts
 * with L (bytes after L, CRCs excluded) and wmbusmeters rewrites L if it
 * disagrees with the hex length. */
static void emit_rtlwmbus(const pktlink_rec_hdr_t *rh, uint8_t *psdu)
{
  const sniffer_phy_desc_t *d = &PHY_TABLE[rh->phy];
  bool frame_b = false;
  const char *mode = "T1";
  if (d->kind == SNIFFER_KIND_WMBUS_TC)       mode = wmbus_mode_str(rh->subphy, &frame_b);
  else if (d->kind == SNIFFER_KIND_WMBUS_A)   mode = (rh->phy == PKT_PHY_WMBUS_S) ? "S1" : "T1";
  else if (d->kind == SNIFFER_KIND_WMBUS_B) { mode = "C1"; frame_b = true; }
  else return;                                  /* not a wM-Bus PHY: nothing to say */

  size_t n = wmbus_strip_crcs(psdu, rh->len, frame_b);
  bool crc_ok = (n > 0) && (rh->flags & PKT_F_CRC_OK);
  if (n == 0) n = rh->len;                      /* CRC bad: emit as is, flagged 0 */

  char ts[24];
  fmt_timestamp(wall_ms(), ts);

  /* ID = A-field 4 BCD bytes, little-endian, printed as 8 digits */
  char id[9] = "00000000";
  if (n >= 8) {
    snprintf(id, sizeof id, "%02X%02X%02X%02X", psdu[7], psdu[6], psdu[5], psdu[4]);
  }
  printf("%s;%c;1;%s;%d;%d;%s;0x", mode, crc_ok ? '1' : '0', ts,
         (int)rh->rssi_dbm, (int)rh->rssi_dbm, id);
  print_hex(psdu, (uint16_t)n);
  printf("\n");
}

/* ================= main-loop drain ================= */

static bool passes_filters(const uint8_t *psdu, uint16_t len)
{
  if (len < s_flt_min || len > s_flt_max) return false;
  if (s_flt_n) {
    if ((uint16_t)(s_flt_off + s_flt_n) > len) return false;
    if (memcmp(&psdu[s_flt_off], s_flt_bytes, s_flt_n) != 0) return false;
  }
  return true;
}

static void drain_one(void)
{
  RAIL_RxPacketInfo_t info;
  RAIL_RxPacketHandle_t h =
      RAIL_GetRxPacketInfo(s_rail, RAIL_RX_PACKET_HANDLE_OLDEST_COMPLETE, &info);
  if (h == RAIL_RX_PACKET_HANDLE_INVALID) return;

  RAIL_RxPacketDetails_t det;
  memset(&det, 0, sizeof det);
  det.timeReceived.timePosition = RAIL_PACKET_TIME_DEFAULT;
  RAIL_GetRxPacketDetailsAlt(s_rail, h, &det);
  RAIL_GetRxTimeSyncWordEndAlt(s_rail, &det);

  uint16_t len = info.packetBytes;
  uint8_t flags = 0;
  if (len > PKTLINK_MAX_PSDU) {
    /* copy only what fits: shorten the portions */
    if (info.firstPortionBytes > PKTLINK_MAX_PSDU) info.firstPortionBytes = PKTLINK_MAX_PSDU;
    info.packetBytes = PKTLINK_MAX_PSDU;
    len = PKTLINK_MAX_PSDU;
    flags |= PKT_F_TRUNCATED;
  }
  RAIL_CopyRxPacket(s_buf, &info);
  RAIL_ReleaseRxPacket(s_rail, h);

  bool crc_ok = (info.packetStatus == RAIL_RX_PACKET_READY_SUCCESS) && det.crcPassed;
  if (crc_ok) flags |= PKT_F_CRC_OK; else s_pkt_crc_bad++;
  if (!crc_ok && !s_keep_bad_crc) return;

  const sniffer_phy_desc_t *d = &PHY_TABLE[s_phy];
  if (d->kind == SNIFFER_KIND_154G) flags |= PKT_F_HAS_PHR | PKT_F_HAS_FCS;
  if (d->kind == SNIFFER_KIND_WMBUS_TC || d->kind == SNIFFER_KIND_WMBUS_A
      || d->kind == SNIFFER_KIND_WMBUS_B) {
    bool fb = (d->kind == SNIFFER_KIND_WMBUS_B) || (det.subPhyId == RAIL_WMBUS_ModeCFrameB);
    if (fb ? wmbus_frame_b_has_valid_crcs(s_buf, len) : wmbus_frame_a_has_valid_crcs(s_buf, len)) {
      flags |= PKT_F_WMBUS_CRCS;
    }
  }
  if (s_hop_n > 1) flags |= PKT_F_HOPPING;

  uint32_t seq = s_pkt_seq++;
  if (!passes_filters(s_buf, len)) { s_pkt_filtered++; return; }

  uint32_t fhz = s_freq_hz;
  uint8_t hop_idx = 0xFF;
  if (s_hop_n > 1) {
    for (uint8_t i = 0; i < s_hop_n; i++) {
      if (s_hop_ch[i] == det.channel) { hop_idx = i; fhz = s_hop_hz[i]; break; }
    }
  }

  pktlink_rec_hdr_t rh = {
    .len      = len,
    .phy      = (uint8_t)s_phy,
    .subphy   = det.subPhyId,
    .flags    = flags,
    .rssi_dbm = det.rssi,
    .lqi      = det.lqi,
    .hop_idx  = hop_idx,
    .ts_us    = (uint32_t)det.timeReceived.packetTime,
    .pkt_seq  = seq,
    .freq_hz  = fhz,
    .channel  = det.channel,
    .drops    = (uint16_t)s_drops,
  };

  switch (s_out) {
    case SNIFFER_OUT_BINARY:
      if (!pktlink_put(&rh, s_buf)) s_drops++;
      break;
    case SNIFFER_OUT_RTLWMBUS:
      emit_rtlwmbus(&rh, s_buf);
      break;
    default:
      emit_text(&rh, s_buf);
      break;
  }
  s_pkt_out++;
}

void sniffer_process(void)
{
  if (s_need_restart) {
    s_need_restart = false;
    RAIL_ResetFifo(s_rail, false, true);
    start_rx();
  }
  /* Bounded: at most 4 packets per loop so the command parser gets its turn. */
  for (int i = 0; i < 4; i++) {
    RAIL_RxPacketInfo_t probe;
    if (RAIL_GetRxPacketInfo(s_rail, RAIL_RX_PACKET_HANDLE_OLDEST_COMPLETE, &probe)
        == RAIL_RX_PACKET_HANDLE_INVALID) break;
    drain_one();
  }
  pktlink_pump();
}

/* ================= public: configuration ================= */

bool sniffer_set_phy(pktlink_phy_t phy)
{
  if (phy >= PKT_PHY_COUNT) return false;
  const sniffer_phy_desc_t *d = &PHY_TABLE[phy];

  RAIL_Idle(s_rail, RAIL_IDLE_ABORT, true);
  if (s_hop_n) { RAIL_EnableRxChannelHopping(s_rail, false, true); s_hop_n = 0; }

  if (s_154_inited && d->kind != SNIFFER_KIND_154G) {
    RAIL_IEEE802154_Deinit(s_rail);
    s_154_inited = false;
  }

  /* channelConfigs[] is generated from the radioconf; NULL-terminated. */
  const RAIL_ChannelConfig_t *cfg = channelConfigs[d->cfg_index];
  if (cfg == NULL) {
    printf("# PHY %s: channelConfigs[%u] missing - regenerate the radio config\r\n",
           d->name, (unsigned)d->cfg_index);
    return false;
  }
  RAIL_ConfigChannels(s_rail, cfg, &sl_rail_util_on_channel_config_change);
  s_phy = phy;

  if (d->kind == SNIFFER_KIND_WMBUS_TC) {
    RAIL_Status_t st = RAIL_WMBUS_Config(s_rail, true);
    printf("# wM-Bus simultaneous T+C RX: %s (st=%d)\r\n",
           RAIL_WMBUS_SupportsSimultaneousTCRx(s_rail) ? "supported" : "NOT supported", (int)st);
  } else if (d->kind == SNIFFER_KIND_154G) {
    /* Promiscuous: no PAN/address filtering, no auto-ACK, no FP handling. */
    RAIL_IEEE802154_Config_t c;
    memset(&c, 0, sizeof c);
    c.addresses = NULL;
    c.ackConfig.enable = false;
    c.ackConfig.ackTimeout = 0;
    c.ackConfig.rxTransitions.success = RAIL_RF_STATE_RX;
    c.ackConfig.rxTransitions.error   = RAIL_RF_STATE_RX;
    c.ackConfig.txTransitions.success = RAIL_RF_STATE_RX;
    c.ackConfig.txTransitions.error   = RAIL_RF_STATE_RX;
    c.timings.idleToRx = 100;
    c.timings.idleToTx = 100;
    c.timings.rxToTx   = 192;
    c.timings.txToRx   = 192;
    c.timings.rxSearchTimeout = 0;
    c.timings.txToRxSearchTimeout = 0;
    c.framesMask = RAIL_IEEE802154_ACCEPT_STANDARD_FRAMES
                 | RAIL_IEEE802154_ACCEPT_ACK_FRAMES;
    c.promiscuousMode = true;
    c.isPanCoordinator = false;
    c.defaultFramePendingInOutgoingAcks = false;
    RAIL_Status_t st = RAIL_IEEE802154_Init(s_rail, &c);
    RAIL_IEEE802154_SetPromiscuousMode(s_rail, true);
    /* 2-byte PHR (frame length + FCS type + whitening bits) handling. */
    RAIL_IEEE802154_ConfigGOptions(s_rail, RAIL_IEEE802154_G_OPTION_GB868,
                                   RAIL_IEEE802154_G_OPTION_GB868);
    s_154_inited = true;
    printf("# 802.15.4g init st=%d\r\n", (int)st);
  }

  apply_rx_options();
  apply_fsk_overrides();

  s_freq_hz = d->default_hz;
  if (!hz_to_channel(s_freq_hz, &s_channel, &s_tick)) { s_channel = 0; s_tick = 0; }
  pktlink_set_center(s_freq_hz);
  start_rx();
  printf("# PHY %s (cfg %u) @ %lu Hz, ch %u, tick %ld\r\n", d->name,
         (unsigned)d->cfg_index, (unsigned long)s_freq_hz, (unsigned)s_channel, (long)s_tick);
  return true;
}

pktlink_phy_t sniffer_phy(void) { return s_phy; }

const sniffer_phy_desc_t *sniffer_phy_desc(pktlink_phy_t phy)
{
  return (phy < PKT_PHY_COUNT) ? &PHY_TABLE[phy] : NULL;
}

bool sniffer_phy_by_name(const char *name, pktlink_phy_t *out)
{
  for (unsigned i = 0; i < PKT_PHY_COUNT; i++) {
    if (strcmp(name, PHY_TABLE[i].name) == 0) { *out = (pktlink_phy_t)i; return true; }
  }
  /* numeric form */
  if (name[0] >= '0' && name[0] <= '9') {
    unsigned v = (unsigned)(name[0] - '0');
    if (v < PKT_PHY_COUNT) { *out = (pktlink_phy_t)v; return true; }
  }
  return false;
}

bool sniffer_tune_hz(uint32_t hz)
{
  uint16_t ch; int32_t tick;
  if (!hz_to_channel(hz, &ch, &tick)) {
    printf("# %lu Hz not reachable from the %s grid (base %lu, spacing %lu, fine +-%d ticks)\r\n",
           (unsigned long)hz, PHY_TABLE[s_phy].name,
           (unsigned long)PHY_TABLE[s_phy].base_hz,
           (unsigned long)PHY_TABLE[s_phy].spacing_hz, TUNE_TICK_MAX);
    return false;
  }
  s_channel = ch; s_tick = tick; s_freq_hz = hz;
  if (s_hop_n) { RAIL_EnableRxChannelHopping(s_rail, false, true); s_hop_n = 0; }
  pktlink_set_center(hz);
  start_rx();
  printf("# tuned %lu Hz: ch %u + %ld ticks\r\n", (unsigned long)hz, (unsigned)ch, (long)tick);
  return true;
}

uint32_t sniffer_freq_hz(void) { return s_freq_hz; }

bool sniffer_set_hop(const uint32_t *hz, uint8_t n, uint32_t dwell_ms)
{
  RAIL_Idle(s_rail, RAIL_IDLE_ABORT, true);
  if (n < 2) {
    if (s_hop_n) RAIL_EnableRxChannelHopping(s_rail, false, true);
    s_hop_n = 0;
    start_rx();
    return true;
  }
  if (n > SNIFFER_MAX_HOPS) n = SNIFFER_MAX_HOPS;
  if (PHY_TABLE[s_phy].spacing_hz == 0u) {
    printf("# hopping needs a channel grid; %s has a single channel\r\n", PHY_TABLE[s_phy].name);
    return false;
  }
  for (uint8_t i = 0; i < n; i++) {
    int32_t tick;
    if (!hz_to_channel(hz[i], &s_hop_ch[i], &tick)) return false;
    s_hop_hz[i] = hz[i];
    memset(&s_hop_entries[i], 0, sizeof s_hop_entries[i]);
    s_hop_entries[i].channel   = s_hop_ch[i];
    s_hop_entries[i].mode      = RAIL_RX_CHANNEL_HOPPING_MODE_TIMEOUT;
    s_hop_entries[i].parameter = dwell_ms * 1000u;     /* us on the channel */
    s_hop_entries[i].delay     = 0;
    s_hop_entries[i].delayMode = RAIL_RX_CHANNEL_HOPPING_DELAY_MODE_STATIC;
  }
  /* Hardware hopping cannot carry a per-hop fine offset: the hop
   * frequencies snap to the grid. Fine offset is zero while hopping. */
  s_tick = 0;
  RAIL_RxChannelHoppingConfig_t cfg = {
    .buffer           = s_hop_buf,
    .bufferLength     = (uint16_t)(sizeof s_hop_buf / sizeof s_hop_buf[0]),
    .numberOfChannels = n,
    .entries          = s_hop_entries,
  };
  RAIL_Status_t st = RAIL_ConfigRxChannelHopping(s_rail, &cfg);
  if (st != RAIL_STATUS_NO_ERROR) {
    printf("# RAIL_ConfigRxChannelHopping st=%d\r\n", (int)st);
    s_hop_n = 0; start_rx();
    return false;
  }
  s_hop_n = n; s_hop_dwell_ms = dwell_ms;
  start_rx();
  printf("# hopping over %u channels, %lu ms dwell\r\n", (unsigned)n, (unsigned long)dwell_ms);
  return true;
}

bool sniffer_set_sync(uint32_t word, uint8_t bits)
{
  if (bits < 2 || bits > 32) return false;
  s_sync_override = true; s_sync_word = word; s_sync_bits = bits;
  if (PHY_TABLE[s_phy].kind != SNIFFER_KIND_FSK) {
    printf("# sync word stored; applies to fsk* PHYs only\r\n");
    return true;
  }
  RAIL_Idle(s_rail, RAIL_IDLE_ABORT, true);
  apply_fsk_overrides();
  start_rx();
  return true;
}

bool sniffer_set_fixed_len(uint16_t len)
{
  if (len > PKTLINK_MAX_PSDU) return false;
  s_fixed_len = len;
  if (PHY_TABLE[s_phy].kind != SNIFFER_KIND_FSK) return true;
  RAIL_Idle(s_rail, RAIL_IDLE_ABORT, true);
  apply_fsk_overrides();
  start_rx();
  return true;
}

void sniffer_set_keep_bad_crc(bool keep)
{
  s_keep_bad_crc = keep;
  apply_rx_options();
}

void sniffer_set_addr_filter(uint8_t off, const uint8_t *bytes, uint8_t n)
{
  if (n > sizeof s_flt_bytes) n = sizeof s_flt_bytes;
  s_flt_off = off; s_flt_n = n;
  if (n) memcpy(s_flt_bytes, bytes, n);
}

void sniffer_set_len_filter(uint16_t min_len, uint16_t max_len)
{
  s_flt_min = min_len; s_flt_max = max_len ? max_len : 0xFFFF;
}

void sniffer_set_output(sniffer_out_t out) { s_out = out; }
sniffer_out_t sniffer_output(void) { return s_out; }

void sniffer_set_epoch(uint32_t unix_now)
{
  s_epoch_unix = unix_now;
  s_epoch_rail = RAIL_GetTime();
}

void sniffer_print_stats(void)
{
  uint32_t blocks, bdrops, rdrops;
  pktlink_stats(&blocks, &bdrops, &rdrops);
  printf("# phy=%s f=%lu Hz out=%d link=%s hop=%u keep_bad_crc=%d\r\n",
         PHY_TABLE[s_phy].name, (unsigned long)s_freq_hz, (int)s_out,
         pktlink_mode() == PKTLINK_MODE_SPI ? "spi" : "uart",
         (unsigned)s_hop_n, (int)s_keep_bad_crc);
  printf("# rx events=%lu frame_err=%lu aborted=%lu fifo_ovf=%lu\r\n",
         (unsigned long)s_ev_rx, (unsigned long)s_ev_err,
         (unsigned long)s_ev_abort, (unsigned long)s_ev_ovf);
  printf("# packets seq=%lu out=%lu crc_bad=%lu filtered=%lu drops=%lu\r\n",
         (unsigned long)s_pkt_seq, (unsigned long)s_pkt_out,
         (unsigned long)s_pkt_crc_bad, (unsigned long)s_pkt_filtered,
         (unsigned long)s_drops);
  printf("# pktlink blocks=%lu blk_drops=%lu rec_drops=%lu rssi_now=%d dBm\r\n",
         (unsigned long)blocks, (unsigned long)bdrops, (unsigned long)rdrops,
         (int)(RAIL_GetRssi(s_rail, false) / 4));
}

void sniffer_reset_stats(void)
{
  s_ev_rx = s_ev_err = s_ev_abort = s_ev_ovf = 0;
  s_pkt_seq = s_pkt_crc_bad = s_pkt_filtered = s_pkt_out = s_drops = 0;
}
