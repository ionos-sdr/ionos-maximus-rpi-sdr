/* SPDX-License-Identifier: MIT
 *
 * cmdlink.h — command input on the FG23 (Raspberry Pi -> FG23)
 *   Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
 *
 * PURPOSE: the Raspberry Pi drives the sniffer through this line (PHY,
 * frequency, output mode), while the FG23 answers and streams on VCOM or
 * SPI. Carried over from the I/Q firmware (iq_capture/cmdlink.*), where
 * the ESP32 used the same wire to retune the receiver.
 *
 * ONE WIRE: Pi GPIO14 (UART TXD, PL011) -> FG23 PA06 (mezzanine P201-11).
 * Common GND via the header. No return path is needed - the FG23 talks on
 * VCOM (rev A, via the WSTK) or on the SPI link.
 *
 * Incoming lines go to the SAME cmd_handle_line() that serves the
 * terminal, so EVERY command is available from the Pi as well. One
 * parser, one behaviour - there cannot be two separate truths.
 *
 * NOTE: the module is unchanged from the I/Q firmware, where the wiring
 * had not yet been verified on hardware at the time of writing.
 */

#ifndef CMDLINK_H
#define CMDLINK_H

#include <stdbool.h>
#include <stdint.h>

/* Command handler called with each complete incoming line.
 * In app.c this is handle_line(). */
typedef void (*cmdlink_line_fn)(const char *line);

/* Initialisation. From app_init(), after the GPIO clock is enabled. */
void cmdlink_init(cmdlink_line_fn on_line);

/* From the main loop. Non-blocking: reads at most as many characters as
 * are currently in the FIFO. Returns true if a COMPLETE line was processed.
 *
 * IMPORTANT: may also be called while the stream is running — tuning
 * itself stops and restarts the stream when needed. */
bool cmdlink_poll(void);

/* Number of lines received so far, and number of characters lost to
 * framing/parity errors. For diagnostics ('s' command). */
void cmdlink_stats(uint32_t *lines, uint32_t *errors);

#endif /* CMDLINK_H */
