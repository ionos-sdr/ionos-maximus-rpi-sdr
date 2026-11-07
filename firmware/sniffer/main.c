/* SPDX-License-Identifier: MIT
 *
 * main.c — bare-metal super loop (Simplicity SDK sl_main)
 *   Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
 *
 * No kernel, no power manager: the sniffer polls the RAIL packet queue,
 * two command inputs and the SPI/UART transport, so EM1/EM2 would only
 * add latency. Written against the public sl_main API; equivalent to the
 * generated "RAIL - SoC Empty" main.c.
 */

#include "sl_component_catalog.h"
#include "sl_main_init.h"
#include "sl_main_process_action.h"
#include "app_init.h"
#include "app_process.h"

int main(void)
{
  sl_main_init();          /* device, clocks, iostream, RAIL (rail_util_init) */
  app_init();

  while (1) {
    sl_main_process_action();
    app_process_action();
  }
}
