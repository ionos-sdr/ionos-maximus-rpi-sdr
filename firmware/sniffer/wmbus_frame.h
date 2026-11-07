/* SPDX-License-Identifier: MIT
 *
 * wmbus_frame.h — EN 13757-4 link-layer CRC and frame A/B block handling
 *   Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
 *
 * Written from the public standard (EN 13757-4:2019, 11.2 / 12.2) and the
 * OMS specification. No Silicon Labs or GPL code involved. The same
 * algorithm is in host/ionos_tools/wmbus.py; both are tested against the
 * same public telegrams.
 *
 * CRC-16: polynomial x^16 + x^13 + x^12 + x^11 + x^10 + x^8 + x^6 + x^5
 * + x^2 + 1 (0x3D65), initial value 0x0000, final XOR 0xFFFF, MSB first,
 * transmitted high byte first.
 *
 * Frame A: block 1 = L C M(2) A(6) + CRC (12 bytes); then 16-byte data
 *   blocks each + CRC; the last block may be shorter. L counts the bytes
 *   AFTER L, CRCs excluded.
 * Frame B: L counts everything after L INCLUDING the CRCs. Up to 128
 *   bytes total: one CRC over bytes 0..len-3 at the end. Longer: a CRC
 *   over bytes 0..125 at offset 126, and a second over 128..len-3 at the
 *   end.
 */

#ifndef WMBUS_FRAME_H
#define WMBUS_FRAME_H

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

uint16_t wmbus_crc16(const uint8_t *data, size_t len);

/* Frame-A helper: does buf[0..len) look like an L-field frame WITH per
 * block CRCs that all verify? */
bool wmbus_frame_a_has_valid_crcs(const uint8_t *buf, size_t len);

/* Frame-B helper. */
bool wmbus_frame_b_has_valid_crcs(const uint8_t *buf, size_t len);

/* Strip the DLL CRCs in place (frame A or B, selected by frame_b) and
 * rewrite L to the new "bytes after L" count (frame A convention, which
 * is what wmbusmeters' rtlwmbus input expects). Returns the new length
 * or 0 if the CRCs do not verify. If the input already has no CRCs
 * (len == L + 1 and no CRC pattern found) it is returned unchanged. */
size_t wmbus_strip_crcs(uint8_t *buf, size_t len, bool frame_b);

/* Three-letter manufacturer code from the little-endian M-field. */
void wmbus_manuf_str(uint16_t m, char out[4]);

#endif /* WMBUS_FRAME_H */
