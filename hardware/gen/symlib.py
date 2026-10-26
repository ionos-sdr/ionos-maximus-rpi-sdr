"""Collect the symbols for the ionos-maximus HAT from the validated sibling projects
(ionos-sdr-mini, RPi_ISM) and write them as one project library ionos_maximus.kicad_sym."""
import os, sys, copy
sys.path.insert(0, os.path.dirname(__file__))
from sx import parse, find, val, dump, Q
GH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
SRC = {
 'mini_radio': os.path.join(GH, 'ionos-sdr-mini/CAD/ionosmini_1v0/radio.kicad_sch'),
 'mini_power': os.path.join(GH, 'ionos-sdr-mini/CAD/ionosmini_1v0/power.kicad_sch'),
 'rpism':      os.path.join(GH, 'RPi_ISM/CAD/kicad/RPism_adapterboard_0V3.kicad_sch'),
}
# new name -> (source, lib_id in source)
WANT = {
 'TFC-120-02':    ('mini_radio', 'ionosSDR_mini:TFC-120-02-F-D-A-K-TR'),
 'R':             ('mini_radio', 'rpw_resistors:R_10K_1%_0402(1005-METRIC)'),
 'C':             ('mini_radio', 'rpw_capacitors:C_100N_50V_0402(1005-METRIC)'),
 'FB':            ('mini_power', 'rpw_inductors:L5_FERRITE_BEAD/0603'),
 'LP5907-3.3':    ('mini_power', 'ionosSDR_mini:LP5907MFX-3.3'),
 'TP':            ('mini_radio', 'rpw_lib:TESTPOINT_40MIL_SMD'),
 'SJ_Bridged':    ('mini_radio', 'Jumper:SolderJumper_2_Bridged'),
 'TC2030':        ('mini_radio', 'Connector:Conn_ARM_SWD_TagConnect_TC2030'),
 'RPI_2X15':      ('rpism', 'RPism_adapterboard_0V3-eagle-import:PINHD-2X15_SMD2X15'),
 'GND':           ('mini_radio', 'rpw_lib:GND'),
 'PWR_FLAG':      ('mini_radio', 'ionosSDR_mini:PWR_FLAG'),
 'SUPPLY':        ('mini_radio', 'ionosSDR_mini:5V0'),
}
POWER_NETS = ['5V0', '5V_LDO', '3V3_PI', '3V3_RADIO', '3V3_RB', 'VMCU_IN']

def lib_symbols(fn):
    t = parse(open(fn, encoding='utf-8').read())
    return {s[1]: s for s in find(find(t, 'lib_symbols')[0], 'symbol')}

def rename(sym, new):
    old = sym[1].split(':')[-1]
    s = copy.deepcopy(sym); s[1] = Q(new)
    for sub in find(s, 'symbol'):
        sub[1] = Q(sub[1].replace(old, new, 1))
    return s

def power_symbol(name, base, desc=''):
    """Clone a supply symbol shape into a named supply symbol (net name = Value = pin name)."""
    s = rename(base, name)
    for p in find(s, 'property'):
        if p[1] == 'Value': p[2] = Q(name)
        if p[1] == 'Description': p[2] = Q('Supply net ' + name)
    for sub in find(s, 'symbol'):
        for pin in find(sub, 'pin'):
            for nm in find(pin, 'name'): nm[1] = Q(name)
    return s

def eeprom():
    """CAT24C32WI-GT3, SOIC-8 (A0 A1 A2 VSS SDA SCL WP VCC) - pinout as in the RPi HAT guide part."""
    pins = [('1','A0','input',-10.16,2.54,0),('2','A1','input',-10.16,0,0),('3','A2','input',-10.16,-2.54,0),
            ('4','VSS','power_in',0,-10.16,90),('5','SDA','bidirectional',10.16,2.54,180),
            ('6','SCL','input',10.16,0,180),('7','WP','input',10.16,-2.54,180),('8','VCC','power_in',0,10.16,270)]
    body = ['symbol', Q('CAT24C32_1_1')] + [
        ['pin', t, 'line', ['at', str(x), str(y), str(a)], ['length', '2.54'],
         ['name', Q(n), ['effects', ['font', ['size', '1.27', '1.27']]]],
         ['number', Q(num), ['effects', ['font', ['size', '1.27', '1.27']]]]] for num, n, t, x, y, a in pins]
    gfx = ['symbol', Q('CAT24C32_0_1'), ['rectangle', ['start', '-7.62', '7.62'], ['end', '7.62', '-7.62'],
           ['stroke', ['width', '0.254'], ['type', 'default']], ['fill', ['type', 'background']]]]
    def prop(k, v, y, hide=False):
        e = ['effects', ['font', ['size', '1.27', '1.27']]] + ([['hide', 'yes']] if hide else [])
        return ['property', Q(k), Q(v), ['at', '0', str(y), '0'], e]
    return ['symbol', Q('CAT24C32'), ['exclude_from_sim', 'no'], ['in_bom', 'yes'], ['on_board', 'yes'],
            prop('Reference', 'U', 9.5), prop('Value', 'CAT24C32WI-GT3', -9.5),
            prop('Footprint', 'Package_SO:SOIC-8_3.9x4.9mm_P1.27mm', 0, True),
            prop('Datasheet', 'https://www.onsemi.com/pdf/datasheet/cat24c32-d.pdf', 0, True),
            prop('Description', 'I2C EEPROM 32 kbit, 16-bit addressing (RPi HAT ID EEPROM)', 0, True),
            gfx, body, ['embedded_fonts', 'no']]

# RPi GPIO header pins 1-30 on the CLH-115 socket, grouped by function. Pin number = J1 (socket) number,
# pin name carries the BCM GPIO and the RPi header pin in brackets. (row, J1 pin, name); rows 2.54 apart.
RPI_LEFT = [(0, 1, '5V [2]'), (1, 3, '5V [4]'),
            (3, 24, 'GPIO11 SCLK [23]'), (4, 23, 'GPIO8 CE0 [24]'), (5, 22, 'GPIO9 MISO [21]'),
            (6, 20, 'GPIO10 MOSI [19]'), (7, 21, 'GPIO25 [22]'), (8, 7, 'GPIO14 TXD [8]'),
            (10, 4, 'GPIO2 SDA [3]'), (11, 6, 'GPIO3 SCL [5]'),
            (17, 5, 'GND [6]'), (18, 10, 'GND [9]'), (19, 13, 'GND [14]')]
RPI_RIGHT = [(0, 2, '3V3 [1]'), (1, 18, '3V3 [17]'),
             (3, 17, 'GPIO24 [18]'), (4, 15, 'GPIO23 [16]'), (5, 14, 'GPIO27 [13]'), (6, 16, 'GPIO22 [15]'),
             (8, 8, 'GPIO4 [7]'), (9, 9, 'GPIO15 RXD [10]'), (10, 12, 'GPIO17 [11]'), (11, 11, 'GPIO18 [12]'),
             (12, 25, 'GPIO7 CE1 [26]'), (13, 28, 'ID_SD [27]'), (14, 27, 'ID_SC [28]'), (15, 30, 'GPIO5 [29]'),
             (17, 19, 'GND [20]'), (18, 26, 'GND [25]'), (19, 29, 'GND [30]')]
RPI_Y0, RPI_PX, RPI_BX, RPI_BY = 24.13, 20.32, 17.78, 26.67


def rpi_hdr():
    def f(sz='1.27', hide=False):
        e = ['effects', ['font', ['size', sz, sz]]]
        return e + ([['hide', 'yes']] if hide else [])
    def prop(k, v, y, hide=False):
        return ['property', Q(k), Q(v), ['at', '0', str(y), '0'], f(hide=hide)]
    pins = []
    for side, x, ang in ((RPI_LEFT, -RPI_PX, '0'), (RPI_RIGHT, RPI_PX, '180')):
        for row, num, name in side:
            y = round(RPI_Y0 - 2.54 * row, 2)
            pins.append(['pin', 'passive', 'line', ['at', str(x), str(y), ang], ['length', '2.54'],
                         ['name', Q(name), f('1.016')], ['number', Q(str(num)), f('1.016')]])
    body = [Q('RPI_HDR30_0_1'), ['rectangle', ['start', str(-RPI_BX), str(RPI_BY)], ['end', str(RPI_BX), str(-RPI_BY)],
                                 ['stroke', ['width', '0.254'], ['type', 'default']], ['fill', ['type', 'background']]]]
    return ['symbol', Q('RPI_HDR30'), ['pin_names', ['offset', '0.762']], ['exclude_from_sim', 'no'],
            ['in_bom', 'yes'], ['on_board', 'yes'],
            prop('Reference', 'J', RPI_BY + 2.54), prop('Value', 'RPi GPIO 1-30', -RPI_BY - 2.54),
            prop('Footprint', '', 0, True), prop('Datasheet', '', 0, True),
            prop('Description', 'Raspberry Pi GPIO header pins 1-30 (2x15 socket, RPi_ISM numbering)', 0, True),
            ['symbol'] + body, ['symbol', Q('RPI_HDR30_1_1')] + pins, ['embedded_fonts', 'no']]


def build():
    cache = {k: lib_symbols(v) for k, v in SRC.items()}
    out = {}
    for new, (src, lid) in WANT.items():
        out[new] = rename(cache[src][lid], new)
    out['SJ_Open'] = copy.deepcopy(out['SJ_Bridged']); out['SJ_Open'] = rename(out['SJ_Bridged'], 'SJ_Open')
    for p in find(out['SJ_Open'], 'property'):
        if p[1] == 'Value': p[2] = Q('Open')
        if p[1] == 'Footprint': p[2] = Q('Jumper:SolderJumper-2_P1.3mm_Open_RoundedPad1.0x1.5mm')
        if p[1] == 'Description': p[2] = Q('Solder Jumper, 2-pole, open')
        if p[1] == 'ki_fp_filters': p[2] = Q('SolderJumper*Open*')
    # strip the bridge graphic from the open jumper (any filled rectangle between the pads)
    for sub in find(out['SJ_Open'], 'symbol'):
        sub[:] = [x for x in sub if not (isinstance(x, list) and x[0] == 'rectangle')]
    for n in POWER_NETS:
        out[n] = power_symbol(n, out['SUPPLY'])
    del out['SUPPLY']
    for n in ('FB', 'TP'):                       # mini symbols use 'bidirectional'; passive is what they are
        for sub in find(out[n], 'symbol'):
            for pin in find(sub, 'pin'):
                pin[1] = 'passive'
    out['CAT24C32'] = eeprom()
    out['RPI_HDR30'] = rpi_hdr()
    return out

def pins(sym):
    r = {}
    for sub in find(sym, 'symbol'):
        for p in find(sub, 'pin'):
            at = find(p, 'at')[0]
            r[val(p, 'number')] = (float(at[1]), float(at[2]), int(float(at[3]) if len(at) > 3 else 0), val(p, 'name'))
    return r

if __name__ == '__main__':
    lib = build()
    for k, s in lib.items():
        print(k, sorted(pins(s).items(), key=lambda x: x[0].zfill(3))[:8], '...' if len(pins(s)) > 8 else '')
