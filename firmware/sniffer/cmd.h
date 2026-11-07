/* SPDX-License-Identifier: MIT
 *
 * cmd.h — one-line text command parser of the sniffer firmware
 *   Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
 *
 * ONE parser serves both inputs: the VCOM terminal and the cmdlink UART
 * from the Raspberry Pi (GPIO14 -> PA06). Same style as the I/Q firmware:
 * a single letter, then the argument, no spaces needed, CR or LF ends
 * the line. Lines starting with '#' are ignored (comments / echoes).
 *
 *   P<name|idx>        select PHY: wmbus_tc wmbus_s wmbus_cb 154g
 *                      fsk2k4 fsk4k8 fsk9k6 fsk38k4 fsk50k fsk100k  (or 0..9)
 *   P?                 list PHYs
 *   F<kHz>             tune, e.g. F868950   (F<Hz> if the number > 10^6*20)
 *   H<kHz>,<kHz>,...   hop list (2..16 entries) with H...;<dwell_ms>
 *   H0                 hopping off
 *   Y<hex>,<bits>      sync word for fsk* PHYs, e.g. Y2DD4,16
 *   L<n>               fixed length n (0 = variable), fsk* PHYs
 *   K0 | K1            keep CRC-failed frames (flagged) 0 = drop (default)
 *   A<off>,<hex>       address filter: bytes <hex> at PSDU offset <off>; A = clear
 *   N<min>,<max>       length filter (0,0 = off)
 *   Ot | Or | Ob | Os  output: text (UART) | rtl_wmbus (UART) | binary PKT1 on UART | binary PKT1 on SPI
 *   D<unix_seconds>    set the wall clock used in rtl_wmbus lines
 *   s                  statistics        R  reset counters
 *   v                  version           ?  help
 */

#ifndef CMD_H
#define CMD_H

/* Handle one complete line (no CR/LF). Safe to call from the main loop
 * only. */
void cmd_handle_line(const char *line);

/* Non-blocking: collects characters from the VCOM iostream and calls
 * cmd_handle_line() on a complete line. */
void cmd_poll_vcom(void);

#endif /* CMD_H */
