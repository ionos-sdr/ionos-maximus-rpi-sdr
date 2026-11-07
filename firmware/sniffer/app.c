/* SPDX-License-Identifier: MIT
 *
 * app.c — ionos-maximus FG23 packet sniffer: init and main-loop glue
 *   Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
 *
 * ROLE SELECTION. The same binary runs on rev A (one FG23, WSTK radio
 * board) and on rev B radio 2 (chip-down, SPI0 CE1 / DRDY2). The FG23
 * cannot tell the two apart and does not need to: the host decides which
 * transport and output it wants with 'O<t|r|b|s>' over cmdlink. The
 * default after reset is text mode on VCOM, so a terminal always shows
 * something. SNIFFER_DEFAULT_SPI=1 boots straight into PKT1-on-SPI for
 * headless rev B units.
 *
 * Bench use (rev A): the WSTK's VCOM appears on the PC as a USB CDC port.
 *   Or         -> rtl_wmbus lines; wmbusmeters reads them with
 *                 rtlwmbus:CMD(cat /dev/ttyACM0)   (see host/README.md)
 */

#include "app_init.h"
#include "app_process.h"
#include "sl_rail_util_init.h"
#include "sl_rail_util_init_inst0_config.h"
#include "rail.h"
#include "sniffer.h"
#include "pktlink_port.h"
#include "cmd.h"
#include "cmdlink.h"
#include <stdio.h>

#ifndef SNIFFER_DEFAULT_SPI
#define SNIFFER_DEFAULT_SPI 0
#endif

static RAIL_Handle_t s_rail = NULL;

/* RAIL event hook of the rail_util_init component. */
void sl_rail_util_on_event(RAIL_Handle_t rail_handle, RAIL_Events_t events)
{
  sniffer_on_event(rail_handle, events);
}

/* Called by rail_util_init when the radio config changes (multi-PHY). */
void sl_rail_util_on_channel_config_change(RAIL_Handle_t rail_handle,
                                           const RAIL_ChannelConfigEntry_t *entry)
{
  (void)rail_handle; (void)entry;
}

void sl_rail_util_on_rf_ready(RAIL_Handle_t rail_handle)
{
  (void)rail_handle;
}

void app_init(void)
{
  s_rail = sl_rail_util_get_handle(SL_RAIL_UTIL_HANDLE_INST0);

  printf("\r\n# ionos-maximus sniffer %s  (? = help)\r\n", SNIFFER_VERSION);

  pktlink_init();
  cmdlink_init(cmd_handle_line);
  sniffer_init(s_rail);

#if SNIFFER_DEFAULT_SPI
  cmd_handle_line("Os");
#endif
}

void app_process_action(void)
{
  cmdlink_poll();        /* Pi -> commands (GPIO14 -> PA06) */
  cmd_poll_vcom();       /* terminal -> commands */
  sniffer_process();     /* packets -> output, pktlink pump */
}
