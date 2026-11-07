/* SPDX-License-Identifier: MIT
 *
 * pktlink.c — PKT1 block transport: SPI secondary (mode A) or VCOM UART
 *   Copyright (c) 2026 Zoltan Doczi (HA7DCD), Zoltan Papp - Z2 Labs
 *
 * ===================== TWO TRANSPORTS, ONE BLOCK =====================
 *
 * The 1040-byte PKT1 block (pktlink.h) is identical on both paths:
 *
 *   SPI  : the Raspberry Pi is the SPI main (README "Mode A"). We load one
 *          block into an LDMA descriptor aimed at the USART0 TX register,
 *          raise DRDY (PD02 -> Pi GPIO25 / GPIO4 on rev B radio 2), and the
 *          Pi clocks out exactly 1040 bytes with CS low. When the LDMA has
 *          finished AND CS has returned high, the block is gone, DRDY
 *          drops, the next block is loaded. Sequence numbers are written
 *          at load time, so a block the Pi never fetched is a DROP counted
 *          here, not a gap on the far side.
 *   UART : the same 1040 bytes written to the VCOM iostream. The host
 *          resynchronises on the magic. For 100 kbps PHYs 115200 baud is
 *          too slow (one block = 90 ms); set VCOM to 921600 in
 *          config/sl_iostream_eusart_vcom_config.h for binary UART mode.
 *
 * ========================= WIRING (rev A) ==========================
 *   FG23      mezzanine   Pi
 *   PC05      P201-15     GPIO11  SPI0 SCLK   (input here: Pi drives it)
 *   PC00      P201-10     GPIO9   SPI0 MISO   (our TX)
 *   PA07      P201-13     GPIO8   SPI0 CE0    (input, active low)
 *   PA05      P201-7      GPIO10  SPI0 MOSI   (unused RX, optional)
 *   PD02      P201-9      GPIO25  DRDY        (output, block ready)
 *   Rev B radio 2: CE1 = GPIO7, DRDY2 = GPIO4, RESETn2 = GPIO5. From the
 *   FG23's point of view nothing changes: same ports, same code.
 *
 * ====================== WHY USART0, NOT EUSART =====================
 *   EUSART0 : VCOM (terminal / UART output path)
 *   EUSART1 : cmdlink RX (PA06)
 *   USART0  : free -> SPI secondary. The USART has a 2-deep TX buffer and
 *             the LDMA keeps it topped up from the TXBL signal. The
 *             maximum reliable SCLK in secondary mode is measurement M1;
 *             start the Pi at 4 MHz (spi_hz in sniffd), raise it later.
 *
 * TO VERIFY ON HARDWARE (cannot be compiled here):
 *   - USART0 secondary pin routing (CLKROUTE/CSROUTE/TXROUTE register
 *     names are the series-2 ones used by iq_stream.c).
 *   - Whether the USART TX shift register still holds a byte when the
 *     LDMA reports done; we therefore also wait for CS to go high.
 *   - USART_STATUS_TXC behaviour in secondary mode is not relied upon.
 */

#include "pktlink.h"
#include "pktlink_port.h"
#include "em_cmu.h"
#include "em_gpio.h"
#include "em_usart.h"
#include "em_ldma.h"
#include "dmadrv.h"
#include "sl_iostream.h"
#include "sl_iostream_handles.h"
#include <stdio.h>
#include <string.h>

/* ---------------- pins ---------------- */
#define SPI_TX_PORT    gpioPortC
#define SPI_TX_PIN     0      /* PC00 -> Pi MISO */
#define SPI_CLK_PORT   gpioPortC
#define SPI_CLK_PIN    5      /* PC05 <- Pi SCLK */
#define SPI_CS_PORT    gpioPortA
#define SPI_CS_PIN     7      /* PA07 <- Pi CE   */
#define SPI_RX_PORT    gpioPortA
#define SPI_RX_PIN     5      /* PA05 <- Pi MOSI (unused) */
#define DRDY_PORT      gpioPortD
#define DRDY_PIN       2      /* PD02 -> Pi DRDY */

/* Blocks held. One is being filled, one is loaded in the LDMA, the rest
 * is reserve while the Pi is busy. 4 x 1040 = 4160 bytes. */
#define NBLK           4u

static pktlink_blk_t s_blk[NBLK] __attribute__((aligned(4)));
static volatile uint32_t s_prod = 0;      /* blocks completed (filled)   */
static volatile uint32_t s_cons = 0;      /* blocks sent                 */
static uint16_t          s_fill = 0;      /* payload bytes in the block being filled */
static uint32_t          s_seq  = 0;      /* outgoing block sequence     */
static uint32_t          s_f_center_hz = 0;

static pktlink_mode_t    s_mode = PKTLINK_MODE_UART;
static bool              s_spi_up = false;

/* LDMA */
static LDMA_Descriptor_t s_desc;
static unsigned int      s_dma_ch = 0;
static bool              s_dma_alloc = false;
static volatile bool     s_tx_busy = false;
static volatile bool     s_cs_seen_low = false;

/* statistics */
static uint32_t s_blk_sent = 0, s_blk_drops = 0, s_rec_drops = 0;
static uint32_t s_uart_bytes = 0;

/* ================= SPI secondary ================= */

static void spi_setup(void)
{
  CMU_ClockEnable(cmuClock_GPIO, true);
  CMU_ClockEnable(cmuClock_USART0, true);

  GPIO_PinModeSet(SPI_TX_PORT,  SPI_TX_PIN,  gpioModePushPull, 0);
  GPIO_PinModeSet(SPI_CLK_PORT, SPI_CLK_PIN, gpioModeInput, 0);
  GPIO_PinModeSet(SPI_CS_PORT,  SPI_CS_PIN,  gpioModeInputPull, 1);  /* idle high */
  GPIO_PinModeSet(SPI_RX_PORT,  SPI_RX_PIN,  gpioModeInput, 0);

  USART_InitSync_TypeDef init = USART_INITSYNC_DEFAULT;
  init.enable       = usartEnableTx;      /* we only transmit */
  init.baudrate     = 0;                  /* ignored in secondary mode */
  init.databits     = usartDatabits8;
  init.master       = false;              /* SECONDARY: the Pi clocks */
  init.msbf         = true;               /* SPI convention */
  init.clockMode    = usartClockMode0;    /* CPOL=0, CPHA=0 */
  init.autoCsEnable = false;
  init.autoTx       = false;
  USART_InitSync(USART0, &init);

  GPIO->USARTROUTE[0].TXROUTE =
      ((uint32_t)SPI_TX_PORT << _GPIO_USART_TXROUTE_PORT_SHIFT)
    | ((uint32_t)SPI_TX_PIN  << _GPIO_USART_TXROUTE_PIN_SHIFT);
  GPIO->USARTROUTE[0].CLKROUTE =
      ((uint32_t)SPI_CLK_PORT << _GPIO_USART_CLKROUTE_PORT_SHIFT)
    | ((uint32_t)SPI_CLK_PIN  << _GPIO_USART_CLKROUTE_PIN_SHIFT);
  GPIO->USARTROUTE[0].CSROUTE =
      ((uint32_t)SPI_CS_PORT << _GPIO_USART_CSROUTE_PORT_SHIFT)
    | ((uint32_t)SPI_CS_PIN  << _GPIO_USART_CSROUTE_PIN_SHIFT);
  GPIO->USARTROUTE[0].RXROUTE =
      ((uint32_t)SPI_RX_PORT << _GPIO_USART_RXROUTE_PORT_SHIFT)
    | ((uint32_t)SPI_RX_PIN  << _GPIO_USART_RXROUTE_PIN_SHIFT);
  /* In secondary mode the CS input gates the shifter, so CSPEN is ON
   * here - unlike the I/Q firmware's main mode where CS is a GPIO. */
  GPIO->USARTROUTE[0].ROUTEEN =
      GPIO_USART_ROUTEEN_TXPEN | GPIO_USART_ROUTEEN_CLKPEN
    | GPIO_USART_ROUTEEN_CSPEN | GPIO_USART_ROUTEEN_RXPEN;

  /* DRDY low = nothing to fetch. Push-pull so it never floats. */
  GPIO_PinModeSet(DRDY_PORT, DRDY_PIN, gpioModePushPull, 0);

  if (!s_dma_alloc) {
    DMADRV_Init();
    if (DMADRV_AllocateChannel(&s_dma_ch, NULL) == ECODE_EMDRV_DMADRV_OK) {
      s_dma_alloc = true;
    } else {
      printf("# pktlink: no LDMA channel!\r\n");
    }
  }
  s_spi_up = true;
  printf("# pktlink: SPI secondary on PC05/PC00/PA07, DRDY=PD02, block %u B\r\n",
         (unsigned)PKTLINK_BLK_BYTES);
}

static void spi_teardown(void)
{
  if (s_dma_alloc) LDMA_StopTransfer((int)s_dma_ch);
  s_tx_busy = false;
  GPIO_PinOutClear(DRDY_PORT, DRDY_PIN);
  GPIO->USARTROUTE[0].ROUTEEN = 0;
  USART_Reset(USART0);
  s_spi_up = false;
}

/* Load one block into the LDMA and raise DRDY. The Pi does the rest. */
static void spi_load_block(pktlink_blk_t *b)
{
  if (!s_dma_alloc) { s_blk_drops++; s_cons++; return; }

  b->hdr.seq = s_seq++;

  s_desc.xfer.structType  = ldmaCtrlStructTypeXfer;
  s_desc.xfer.structReq   = 0;
  s_desc.xfer.xferCnt     = PKTLINK_BLK_BYTES - 1u;
  s_desc.xfer.byteSwap    = 0;
  s_desc.xfer.blockSize   = ldmaCtrlBlockSizeUnit1;
  s_desc.xfer.doneIfs     = 0;
  s_desc.xfer.reqMode     = ldmaCtrlReqModeBlock;
  s_desc.xfer.decLoopCnt  = 0;
  s_desc.xfer.ignoreSrec  = 0;
  s_desc.xfer.srcInc      = ldmaCtrlSrcIncOne;
  s_desc.xfer.size        = ldmaCtrlSizeByte;
  s_desc.xfer.dstInc      = ldmaCtrlDstIncNone;
  s_desc.xfer.srcAddrMode = ldmaCtrlSrcAddrModeAbs;
  s_desc.xfer.dstAddrMode = ldmaCtrlDstAddrModeAbs;
  s_desc.xfer.srcAddr     = (uint32_t)b;
  s_desc.xfer.dstAddr     = (uint32_t)&USART0->TXDATA;
  s_desc.xfer.linkMode    = ldmaLinkModeAbs;
  s_desc.xfer.link        = 0;
  s_desc.xfer.linkAddr    = 0;

  LDMA_TransferCfg_t cfg =
      LDMA_TRANSFER_CFG_PERIPHERAL(ldmaPeripheralSignal_USART0_TXBL);

  /* Flush anything stale in the TX buffer, then let the LDMA pre-fill it
   * (TXBL is asserted while CS is high, the data waits for the clock). */
  USART0->CMD = USART_CMD_CLEARTX;
  LDMA_StartTransfer((int)s_dma_ch, &cfg, &s_desc);
  s_cs_seen_low = false;
  s_tx_busy = true;
  GPIO_PinOutSet(DRDY_PORT, DRDY_PIN);        /* "come and get it" */
}

/* Completes an SPI send: LDMA done AND CS back high after having been
 * low. Returns true while still busy. */
static bool spi_tx_poll(void)
{
  if (!s_tx_busy) return false;

  bool cs_low = (GPIO_PinInGet(SPI_CS_PORT, SPI_CS_PIN) == 0u);
  if (cs_low) s_cs_seen_low = true;

  if (s_cs_seen_low && !cs_low && LDMA_TransferDone((int)s_dma_ch)) {
    GPIO_PinOutClear(DRDY_PORT, DRDY_PIN);
    s_tx_busy = false;
    s_cons++;
    s_blk_sent++;
  }
  return s_tx_busy;
}

/* ================= UART ================= */

static void uart_send_block(pktlink_blk_t *b)
{
  b->hdr.seq = s_seq++;
  sl_iostream_write(sl_iostream_vcom_handle, b, PKTLINK_BLK_BYTES);
  s_uart_bytes += PKTLINK_BLK_BYTES;
  s_cons++;
  s_blk_sent++;
}

/* ================= public ================= */

void pktlink_init(void)
{
  CMU_ClockEnable(cmuClock_GPIO, true);
  /* DRDY must never float: the Pi has a 10 k pull-down on the board, but
   * drive it low from the first instruction anyway. */
  GPIO_PinModeSet(DRDY_PORT, DRDY_PIN, gpioModePushPull, 0);
  memset(s_blk, 0, sizeof(s_blk));
  s_prod = s_cons = 0; s_fill = 0; s_seq = 0;
}

void pktlink_set_mode(pktlink_mode_t mode)
{
  if (mode == s_mode) return;
  if (s_mode == PKTLINK_MODE_SPI && s_spi_up) spi_teardown();
  s_mode = mode;
  /* Drop whatever was queued for the old transport. */
  s_prod = s_cons; s_fill = 0;
  if (mode == PKTLINK_MODE_SPI) spi_setup();
}

pktlink_mode_t pktlink_mode(void) { return s_mode; }

void pktlink_set_center(uint32_t f_hz) { s_f_center_hz = f_hz; }

/* Close the block being filled and make it sendable. */
static void close_block(void)
{
  if (s_fill == 0u) return;
  pktlink_blk_t *b = &s_blk[s_prod % NBLK];
  b->hdr.magic       = PKTLINK_MAGIC;
  b->hdr.nbytes      = s_fill;
  b->hdr.flags       = PKTLINK_FLAG_LAST;
  b->hdr.f_center_hz = s_f_center_hz;
  /* zero the unused tail so the host never sees stale records */
  memset(&b->payload[s_fill], 0, PKTLINK_PAYLOAD_BYTES - s_fill);
  s_fill = 0;
  s_prod++;
}

bool pktlink_put(const pktlink_rec_hdr_t *rh, const uint8_t *psdu)
{
  uint32_t need = PKTLINK_REC_BYTES(rh->len);
  if (need > PKTLINK_PAYLOAD_BYTES) { s_rec_drops++; return false; }

  if (s_fill + need > PKTLINK_PAYLOAD_BYTES) close_block();

  if ((s_prod - s_cons) >= NBLK) {       /* all blocks full: the Pi is not taking them */
    s_blk_drops++;
    s_rec_drops++;
    return false;
  }

  pktlink_blk_t *b = &s_blk[s_prod % NBLK];
  memcpy(&b->payload[s_fill], rh, PKTLINK_REC_HDR_BYTES);
  memcpy(&b->payload[s_fill + PKTLINK_REC_HDR_BYTES], psdu, rh->len);
  uint32_t pad = need - PKTLINK_REC_HDR_BYTES - rh->len;
  if (pad) memset(&b->payload[s_fill + PKTLINK_REC_HDR_BYTES + rh->len], 0, pad);
  s_fill = (uint16_t)(s_fill + need);
  return true;
}

void pktlink_pump(void)
{
  /* Low latency: a half-filled block goes out as soon as the transport
   * is idle. Packets are rare compared with the link speed. */
  if (s_mode == PKTLINK_MODE_SPI) {
    if (!s_spi_up) return;
    if (spi_tx_poll()) return;             /* still in flight */
    if (s_prod == s_cons) close_block();
    if (s_prod != s_cons) spi_load_block(&s_blk[s_cons % NBLK]);
  } else {
    close_block();
    while (s_prod != s_cons) uart_send_block(&s_blk[s_cons % NBLK]);
  }
}

void pktlink_stats(uint32_t *blocks, uint32_t *blk_drops, uint32_t *rec_drops)
{
  if (blocks)    *blocks    = s_blk_sent;
  if (blk_drops) *blk_drops = s_blk_drops;
  if (rec_drops) *rec_drops = s_rec_drops;
}

uint32_t pktlink_drops(void) { return s_rec_drops; }
