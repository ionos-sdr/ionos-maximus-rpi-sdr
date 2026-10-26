"""ionos-maximus RPi adapter rev A - single source of truth for the netlist.

Link modes A/B per README. J1 pin numbers follow the RPi_ISM CON2 (Samtec CLH-115) socket
numbering: J1 pin n <-> RPi header pin n+1 (n odd) / n-1 (n even).
"""
FP = 'ionos_maximus:'
R0402, C0402 = FP + 'RESC1005X40_0402', FP + 'CAPC1005X60_0402'
TPFP = FP + 'TESTPOINT_40MIL_SMD'
SJB = 'Jumper:SolderJumper-2_P1.3mm_Bridged_RoundedPad1.0x1.5mm'
SJO = 'Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm'


def rpi_to_j1(p):
    return p - 1 if p % 2 == 0 else p + 1


# RPi header pin -> net (pins not listed get a no-connect)
RPI = {1: '3V3_PI', 17: '3V3_PI', 2: '5V0', 4: '5V0',
       6: 'GND', 9: 'GND', 14: 'GND', 20: 'GND', 25: 'GND', 30: 'GND',
       3: 'I2C_SDA', 5: 'I2C_SCL', 8: 'CMD_PI', 13: 'SWO_PI', 15: 'RESETn_PI', 16: 'SWCLK_PI',
       18: 'SWDIO_PI', 19: 'IQ_MOSI_PI', 21: 'IQ_DATA_PI', 22: 'DRDY_PI', 23: 'IQ_SCLK_HDR',
       24: 'IQ_CS_HDR', 27: 'ID_SD', 28: 'ID_SC'}

# Radio board mezzanine (WSTK radio board interface, as on ionos-sdr-mini)
P200 = {1: '3V3_RB', 2: 'GND', 17: 'RESETn_RB', 37: '5V_RB', 38: 'GND', 39: 'I2C_SCL', 40: 'I2C_SDA'}
P201 = {1: 'GND', 2: 'VMCU_IN', 7: 'IQ_MOSI_RB', 9: 'DRDY_RB', 10: 'IQ_DATA_RB', 11: 'CMD_RB',
        13: 'IQ_CS_RB', 15: 'IQ_SCLK_RB', 19: 'SWO_RB', 21: 'SWDIO_RB', 23: 'SWCLK_RB', 39: 'GND', 40: 'VRF_IN'}

# (ref, symbol, value, footprint, {pin: net}, dnp, extra fields)
PARTS = [
    ('J1', 'RPI_HDR30', 'CLH-115-F-D-DV', FP + '2X15_SMT_CROSSPCB_ASSYM',
     {str(rpi_to_j1(p)): n for p, n in RPI.items()}, False,
     {'Note': 'RPi GPIO header pins 1-30, RPi_ISM socket numbering'}),
    ('P200', 'TFC-120-02', 'TFC-120-02-F-D-A-K-TR', FP + 'TFC-120-02-XX-D-A-K-TR',
     {str(k): v for k, v in P200.items()}, False,
     {'Note': 'radio board P200 (CON1); JLC fits TFM-120-02-L-D-K-TR C17227108'}),
    ('P201', 'TFC-120-02', 'TFC-120-02-F-D-A-K-TR', FP + 'TFC-120-02-XX-D-A-K-TR',
     {str(k): v for k, v in P201.items()}, False,
     {'Note': 'radio board P201 (CON2); JLC fits TFM-120-02-L-D-K-TR C17227108'}),
    # radio supply: Pi 5 V -> ferrite -> LP5907 -> VMCU_IN (as on ionos-sdr-mini)
    ('FB1', 'FB', '600R@100MHz', FP + 'L5_0603', {'1': '5V0', '2': '5V_LDO'}, False, {}),
    ('C1', 'C', '10uF/6.3V', C0402, {'1': '5V0', '2': 'GND'}, False, {}),
    ('C2', 'C', '1uF/16V/10%', C0402, {'1': '5V_LDO', '2': 'GND'}, False, {}),
    ('U1', 'LP5907-3.3', 'LP5907MFX-3.3', FP + 'SOT-23-5',
     {'1': '5V_LDO', '2': 'GND', '3': '5V_LDO', '5': '3V3_RADIO'}, False, {}),
    ('C3', 'C', '1uF/16V/10%', C0402, {'1': '3V3_RADIO', '2': 'GND'}, False, {}),
    ('C4', 'C', '100nF/50V/10%', C0402, {'1': '3V3_RADIO', '2': 'GND'}, False, {}),
    ('R1', 'R', '0R', R0402, {'1': '3V3_RADIO', '2': 'VMCU_IN'}, False, {'Note': 'VMCU_IN current probe point'}),
    ('C5', 'C', '100nF/50V/10%', C0402, {'1': 'VMCU_IN', '2': 'GND'}, False, {}),
    ('JP1', 'SJ_Bridged', 'Bridged', SJB, {'1': '3V3_RADIO', '2': '3V3_RB'}, False,
     {'Note': 'board-ID EEPROM supply, P200-1'}),
    ('R2', 'R', 'NA(0R)', R0402, {'1': '5V0', '2': '5V_RB'}, True, {'Note': 'P200-37 5 V option, not fitted'}),
    # I/Q SPI link, 22 R at both ends
    ('JP2', 'SJ_Bridged', 'Bridged', SJB, {'1': 'IQ_SCLK_HDR', '2': 'IQ_SCLK_PI'}, False,
     {'Note': 'README SJ1: cut to isolate the Pi'}),
    ('R3', 'R', '22R', R0402, {'1': 'IQ_SCLK_PI', '2': 'IQ_SCLK'}, False, {}),
    ('R4', 'R', '22R', R0402, {'1': 'IQ_SCLK', '2': 'IQ_SCLK_RB'}, False, {}),
    ('JP3', 'SJ_Bridged', 'Bridged', SJB, {'1': 'IQ_CS_HDR', '2': 'IQ_CS_PI'}, False,
     {'Note': 'README SJ2: cut to isolate the Pi'}),
    ('R5', 'R', '22R', R0402, {'1': 'IQ_CS_PI', '2': 'IQ_CS'}, False, {}),
    ('R6', 'R', '22R', R0402, {'1': 'IQ_CS', '2': 'IQ_CS_RB'}, False, {}),
    ('R7', 'R', '10k', R0402, {'1': 'IQ_CS', '2': '3V3_RADIO'}, False, {'Note': 'CS idle high while either side boots'}),
    ('R8', 'R', '22R', R0402, {'1': 'IQ_DATA_PI', '2': 'IQ_DATA'}, False, {}),
    ('R9', 'R', '22R', R0402, {'1': 'IQ_DATA', '2': 'IQ_DATA_RB'}, False, {}),
    ('R10', 'R', '22R', R0402, {'1': 'DRDY_PI', '2': 'DRDY'}, False, {}),
    ('R11', 'R', '22R', R0402, {'1': 'DRDY', '2': 'DRDY_RB'}, False, {}),
    ('R12', 'R', '10k', R0402, {'1': 'DRDY', '2': 'GND'}, False, {}),
    ('R13', 'R', 'NA(0R)', R0402, {'1': 'IQ_MOSI_PI', '2': 'IQ_MOSI_RB'}, True, {'Note': 'optional in-band commands'}),
    ('R14', 'R', '0R', R0402, {'1': 'CMD_PI', '2': 'CMD_RB'}, False, {'Note': 'cmdlink, Pi PL011 TXD'}),
    # SWD from the Pi (OpenOCD linuxgpiod), Tag-Connect for an external probe
    ('JP4', 'SJ_Bridged', 'Bridged', SJB, {'1': 'SWDIO_PI', '2': 'SWDIO_RB'}, False, {}),
    ('JP5', 'SJ_Bridged', 'Bridged', SJB, {'1': 'SWCLK_PI', '2': 'SWCLK_RB'}, False, {}),
    ('JP6', 'SJ_Bridged', 'Bridged', SJB, {'1': 'SWO_PI', '2': 'SWO_RB'}, False, {}),
    ('JP7', 'SJ_Bridged', 'Bridged', SJB, {'1': 'RESETn_PI', '2': 'RESETn_RB'}, False, {'Note': 'drive open-drain only'}),
    ('J2', 'TC2030', 'NA(TC2030-NL)', 'Connector:Tag-Connect_TC2030-IDC-NL_2x03_P1.27mm_Vertical',
     {'1': 'VMCU_IN', '2': 'SWDIO_RB', '3': 'RESETn_RB', '4': 'SWCLK_RB', '5': 'GND', '6': 'SWO_RB'}, True, {}),
    # HAT ID EEPROM (RPi HAT design guide: CAT24C32, 3.9k pull-ups, WP 1k pull-up + jumper)
    ('U2', 'CAT24C32', 'CAT24C32WI-GT3', 'Package_SO:SOIC-8_3.9x4.9mm_P1.27mm',
     {'1': 'GND', '2': 'GND', '3': 'GND', '4': 'GND', '5': 'ID_SD', '6': 'ID_SC', '7': 'EE_WP', '8': '3V3_PI'}, False, {}),
    ('C6', 'C', '100nF/50V/10%', C0402, {'1': '3V3_PI', '2': 'GND'}, False, {}),
    ('R15', 'R', '3.9k', R0402, {'1': 'ID_SD', '2': '3V3_PI'}, False, {}),
    ('R16', 'R', '3.9k', R0402, {'1': 'ID_SC', '2': '3V3_PI'}, False, {}),
    ('R17', 'R', '1k', R0402, {'1': 'EE_WP', '2': '3V3_PI'}, False, {'Note': 'write protected by default'}),
    ('JP8', 'SJ_Open', 'Open', SJO, {'1': 'EE_WP', '2': 'GND'}, False, {'Note': 'bridge to re-flash the ID EEPROM'}),
    # test pads
    ('TP1', 'TP', 'DRDY', TPFP, {'PP': 'DRDY'}, False, {}),
    ('TP2', 'TP', 'CMD', TPFP, {'PP': 'CMD_RB'}, False, {}),
    ('TP3', 'TP', 'EE_WP', TPFP, {'PP': 'EE_WP'}, False, {}),
    ('TP4', 'TP', 'VMCU_IN', TPFP, {'PP': 'VMCU_IN'}, False, {}),
    ('TP5', 'TP', 'GND', TPFP, {'PP': 'GND'}, False, {}),
    ('TP6', 'TP', 'VRF_IN', TPFP, {'PP': 'VRF_IN'}, False, {}),
    ('TP7', 'TP', '5V0', TPFP, {'PP': '5V0'}, False, {}),
]
POWER = {'GND', '5V0', '5V_LDO', '3V3_PI', '3V3_RADIO', '3V3_RB', 'VMCU_IN'}
