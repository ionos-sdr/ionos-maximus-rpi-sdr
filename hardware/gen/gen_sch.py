"""Generate ionos_maximus.kicad_sch + ionos_maximus.kicad_sym (house schematic style).

House style: 1.27 mm grid, wired circuits, supplies as
upright power symbols, signals between blocks as global labels, framed functional blocks with a title and a
note, no text overlaps, decoupling next to its rail, A3 with a filled title block.
Connectivity must equal design.PARTS; check_pinmap.py and the KiCad netlist prove it.
Run:  python3 gen_sch.py   (writes into ../kicad/)
"""
import os, sys, uuid
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sx import Q, dump
import symlib, design

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'kicad')
PRJ = LIB = 'ionos_maximus'
NS = uuid.UUID('6b1f3a52-0d6e-4c7e-9a51-6d6178696d75')
ROOT = str(uuid.uuid5(NS, 'root'))
G = 1.27
PARTS = {p[0]: p for p in design.PARTS}


def uid(*k):
    return Q(str(uuid.uuid5(NS, '/'.join(map(str, k)))))


def g(v):
    return ('%.2f' % (round(v / G) * G)).rstrip('0').rstrip('.')


def eff(size=1.27, hide=False, justify=None, bold=False, thick=None):
    font = ['font', ['size', str(size), str(size)]]
    if thick:
        font.append(['thickness', str(thick)])
    if bold:
        font.append(['bold', 'yes'])
    e = ['effects', font]
    if justify:
        e.append(['justify'] + justify.split())
    return e


def P(k, v, x, y, hide=False, justify=None, size=1.27):
    o = ['property', Q(k), Q(v), ['at', g(x), g(y), '0']]
    if hide:
        o.append(['hide', 'yes'])
    o.append(eff(size, justify=justify))
    return o


class Sheet:
    def __init__(self, lib):
        self.lib = lib
        self.items = []
        self.pos = {}
        self.npwr = 0
        self.nflg = 0
        self.used = set()

    # ---------------------------------------------------------------- primitives
    def wire(self, *pts):
        for a, b in zip(pts, pts[1:]):
            self.items.append(['wire', ['pts', ['xy', g(a[0]), g(a[1])], ['xy', g(b[0]), g(b[1])]],
                               ['stroke', ['width', '0'], ['type', 'default']], ['uuid', uid('w', a, b)]])

    def junction(self, p):
        self.items.append(['junction', ['at', g(p[0]), g(p[1])], ['diameter', '0'], ['color', '0', '0', '0', '0'],
                           ['uuid', uid('j', p)]])

    def nc(self, p):
        self.items.append(['no_connect', ['at', g(p[0]), g(p[1])], ['uuid', uid('nc', p)]])

    def glabel(self, net, p, right):
        """Global label at p; the label body extends to the right (right=True) or to the left."""
        self.items.append(['global_label', Q(net), ['shape', 'passive'], ['at', g(p[0]), g(p[1]), '0' if right else '180'],
                           ['fields_autoplaced', 'yes'], eff(justify='left' if right else 'right'),
                           ['uuid', uid('gl', net, p)],
                           P('Intersheetrefs', '${INTERSHEET_REFS}', p[0], p[1], hide=True)])

    def text(self, s, p, size=1.27, bold=False, thick=None):
        lines = s.split('\n')
        if len(lines) > 1:                    # one text item per line: p = bottom-left of the last line
            for i, ln in enumerate(lines):
                self.text(ln, (p[0], p[1] - 2.54 * (len(lines) - 1 - i)), size, bold, thick)
            return
        self.items.append(['text', Q(s), ['exclude_from_sim', 'no'], ['at', g(p[0]), g(p[1]), '0'],
                           eff(size, justify='left bottom', bold=bold, thick=thick), ['uuid', uid('t', s, p)]])

    def block(self, title, sub, x1, y1, x2, y2):
        self.items.append(['rectangle', ['start', g(x1), g(y1)], ['end', g(x2), g(y2)],
                           ['stroke', ['width', '0.254'], ['type', 'dash']], ['fill', ['type', 'none']],
                           ['uuid', uid('blk', title)]])
        self.text(title, (x1 + 2.54, y1 + 6.35), 3.0, bold=True, thick=0.6)
        if sub:
            self.text(sub, (x1 + 2.54, y1 + 10.16), 1.27)

    # ---------------------------------------------------------------- symbols
    def pin(self, ref, num):
        """Sheet position of pin `num` of placed part `ref` (all parts unrotated)."""
        x, y, lib = self.pos[ref]
        px, py, _, _ = symlib.pins(self.lib[lib])[num]
        return (x + px, y - py)

    def symbol(self, ref, lib, value, fp, x, y, fields, dnp=False, power=False, desc='', in_bom=True):
        """fields = ((dx, dy, justify) for Reference, (dx, dy, justify) for Value) or None for power."""
        self.used.add(lib)
        self.pos[ref] = (x, y, lib)
        o = ['symbol', ['lib_id', Q(LIB + ':' + lib)], ['at', g(x), g(y), '0'], ['unit', '1'],
             ['exclude_from_sim', 'no'], ['in_bom', 'yes' if in_bom and not (power or dnp) else 'no'],
             ['on_board', 'no' if power else 'yes'], ['dnp', 'yes' if dnp else 'no'], ['uuid', uid('sym', ref)]]
        if power:
            o.append(P('Reference', ref, x, y, hide=True))
            vy = y + 1.905 if lib == 'GND' else y - 3.81
            o.append(P('Value', value, x, vy, hide=lib == 'PWR_FLAG'))
        else:
            (rx, ry, rj), (vx, vy, vj) = fields
            o.append(P('Reference', ref, x + rx, y + ry, justify=rj))
            o.append(P('Value', value, x + vx, y + vy, justify=vj, hide=lib == 'TP'))
        o.append(P('Footprint', fp, x, y, hide=True))
        o.append(P('Datasheet', '', x, y, hide=True))
        o.append(P('Description', desc, x, y, hide=True))
        for p in symlib.pins(self.lib[lib]):
            o.append(['pin', Q(p), ['uuid', uid('pin', ref, p)]])
        o.append(['instances', ['project', Q(PRJ), ['path', Q('/' + ROOT), ['reference', Q(ref)], ['unit', '1']]]])
        self.items.append(o)

    def part(self, ref, x, y, fields):
        _, lib, value, fp, nets, dnp, extra = PARTS[ref]
        self.symbol(ref, lib, value, fp, x, y, fields, dnp=dnp, desc=extra.get('Note', ''),
                    in_bom=not lib.startswith('SJ'))

    def power(self, net, p):
        """Supply (or GND) symbol whose pin lands on p; drawn upright."""
        self.npwr += 1
        px, py, _, _ = list(symlib.pins(self.lib[net]).values())[0]
        self.symbol('#PWR%03d' % self.npwr, net, net, '', p[0] - px, p[1] + py, None, power=True)

    def flag(self, p):
        self.nflg += 1
        self.symbol('#FLG%02d' % self.nflg, 'PWR_FLAG', 'PWR_FLAG', '', p[0], p[1], None, power=True)


# field presets: ((ref dx, dy, justify), (value dx, dy, justify))
F_R = ((0, -2.54, None), (0, 2.54, None))
F_C = ((2.54, 0.0, 'left'), (2.54, 2.54, 'left'))
F_FB = ((0, -6.35, None), (0, -8.89, None))
F_SJ = ((0, -2.54, None), (0, 2.54, None))
F_TP = ((0, -3.81, None), (0, 0, None))          # TP value hidden
F_U = ((-5.08, -8.89, 'left'), (-5.08, -6.35, 'left'))


def draw_header(sh, X, Y):
    """J1: RPi header pins 1-30; supplies wired into one symbol per side, signals to global labels."""
    sh.part('J1', X, Y, ((0, -29.21, None), (0, 29.21, None)))
    nets = PARTS['J1'][4]
    for side, sgn in ((symlib.RPI_LEFT, -1), (symlib.RPI_RIGHT, 1)):
        bus = {}
        for row, num, name in side:
            p = sh.pin('J1', str(num))
            net = nets.get(str(num))
            if net is None:
                sh.nc(p)
            elif net in design.POWER:
                e = (p[0] + sgn * 2.54, p[1])
                sh.wire(p, e)
                bus.setdefault(net, []).append(e)
            else:
                e = (p[0] + sgn * 2.54, p[1])
                sh.wire(p, e)
                sh.glabel(net, e, right=sgn > 0)
        for net, pts in bus.items():
            pts.sort(key=lambda q: q[1])
            sh.wire(*pts)
            for q in pts[1:-1]:
                sh.junction(q)
            if net == 'GND':                      # bus down into one GND symbol
                end = (pts[-1][0], pts[-1][1] + 2.54)
                sh.wire(pts[-1], end)
                if len(pts) > 1:
                    sh.junction(pts[-1])
                sh.power('GND', end)
                if sgn > 0:                       # ERC flag for GND on a stub off the top of the bus
                    f = (pts[0][0] + 2.54, pts[0][1])
                    sh.wire(pts[0], f)
                    sh.junction(pts[0])
                    sh.flag(f)
            else:                                 # bus up into one supply symbol
                top = (pts[0][0], pts[0][1] - 2.54)
                sh.wire(pts[0], top)
                if len(pts) > 1:
                    sh.junction(pts[0])
                sh.power(net, top)
                f = (pts[-1][0] + sgn * 2.54, pts[-1][1])
                sh.wire(pts[-1], f)
                sh.junction(pts[-1])
                sh.flag(f)


def draw_mezz(sh, ref, X, Y, special):
    """Radio-board connector: unused pins no-connect, signals 2.54 mm wire + global label,
    supplies / GND / test pad as given in `special` {pin: callable(sheet, pin_pos)}."""
    sh.part(ref, X, Y, ((11.43, -10.16, None), (11.43, -7.62, None)))
    nets = PARTS[ref][4]
    for num in symlib.pins(sh.lib['TFC-120-02']):
        p = sh.pin(ref, num)
        sgn = -1 if int(num) % 2 else 1
        if num in special:
            special[num](sh, p, sgn)
        elif num not in nets:
            sh.nc(p)
        else:
            e = (p[0] + sgn * 2.54, p[1])
            sh.wire(p, e)
            sh.glabel(nets[num], e, right=sgn > 0)


def to_power(net, length=5.08):
    def f(sh, p, sgn):
        e = (p[0] + sgn * length, p[1])
        sh.wire(p, e)
        sh.power(net, e)
    return f


def gnd_flagged(sh, p, sgn):
    m, e = (p[0] + sgn * 2.54, p[1]), (p[0] + sgn * 5.08, p[1])
    sh.wire(p, m, e)
    sh.power('GND', e)


def to_tp(ref):
    def f(sh, p, sgn):
        e = (p[0] + sgn * 7.62, p[1])
        sh.wire(p, e)
        sh.part(ref, e[0] + sgn * 0.0, e[1], F_TP)
    return f


def gnd_crossing(sh, p, sgn):
    """P200-38: out 2.54, then down past the P200-40 wire (plain crossing, no node) into GND."""
    a = (p[0] + 2.54, p[1])
    b = (a[0], a[1] + 5.08)
    sh.wire(p, a, b)
    sh.power('GND', b)


def long_label(net, length):
    def f(sh, p, sgn):
        e = (p[0] + sgn * length, p[1])
        sh.wire(p, e)
        sh.glabel(net, e, right=sgn > 0)
    return f


def draw_supply(sh, X0, Yr):
    """Rail: 5V0 -> FB1 -> LP5907 -> 3V3_RADIO -> R1 -> VMCU_IN, shunt caps hanging off the rail."""
    x = X0
    tp = sh.pin  # noqa
    sh.part('TP7', x, Yr, F_TP)
    nodes = []

    def node(dx):
        nonlocal x
        x += dx
        nodes.append((x, Yr))
        return (x, Yr)

    def cap(ref, n):
        sh.part(ref, n[0], Yr + 2.54, F_C)
        sh.power('GND', sh.pin(ref, '2'))
        sh.junction(n)

    n0 = node(7.62); sh.power('5V0', n0); sh.junction(n0)
    n1 = node(7.62); cap('C1', n1)
    x += 10.16; sh.part('FB1', x, Yr, F_FB); fb_in, fb_out = sh.pin('FB1', '1'), sh.pin('FB1', '2'); x = fb_out[0]
    n2 = node(5.08); sh.power('5V_LDO', n2); sh.junction(n2)
    n3 = node(7.62); cap('C2', n3)
    n4 = node(12.7); sh.flag(n4); sh.junction(n4)
    en = node(7.62)
    ux = en[0] + 12.7
    sh.part('U1', ux, Yr + 2.54, F_U)
    sh.wire(sh.pin('U1', '3'), (en[0], sh.pin('U1', '3')[1]), en); sh.junction(en)
    sh.power('GND', sh.pin('U1', '2'))
    sh.nc(sh.pin('U1', '4'))
    x = sh.pin('U1', '5')[0]
    out = (x, Yr)
    n5 = node(7.62); cap('C3', n5)
    n6 = node(10.16); sh.power('3V3_RADIO', n6); sh.junction(n6)
    n7 = node(10.16); cap('C4', n7)
    x += 20.32; sh.part('R1', x, Yr, F_R); r_in, r_out = sh.pin('R1', '1'), sh.pin('R1', '2'); x = r_out[0]
    n8 = node(5.08); sh.power('VMCU_IN', n8); sh.junction(n8)
    n9 = node(7.62); cap('C5', n9)
    n10 = node(15.24); sh.flag(n10); sh.junction(n10)
    end = (n10[0] + 7.62, Yr)
    sh.part('TP4', end[0], Yr, F_TP)
    # rail wires
    sh.wire((X0, Yr), n0, n1, fb_in)
    sh.wire(fb_out, n2, n3, n4, en, sh.pin('U1', '1'))
    sh.wire(out, n5, n6, n7, r_in)
    sh.wire(r_out, n8, n9, n10, end)
    return n6


def draw_jp1(sh, x, y):
    """3V3_RADIO -> JP1 -> 3V3_RB (board-ID EEPROM supply on P200-1); 5V0 -> R2 (DNP) -> P200-37."""
    sh.power('3V3_RADIO', (x, y))
    sh.part('JP1', x + 10.16, y, F_SJ)
    f = (sh.pin('JP1', '2')[0] + 5.08, y)
    e = (f[0] + 5.08, y)
    sh.wire((x, y), sh.pin('JP1', '1'))
    sh.wire(sh.pin('JP1', '2'), f, e)
    sh.flag(f)
    sh.junction(f)
    sh.power('3V3_RB', e)
    y2 = y + 12.7
    sh.power('5V0', (x, y2))
    sh.part('R2', x + 10.16, y2, F_R)
    sh.wire((x, y2), sh.pin('R2', '1'))
    e2 = (sh.pin('R2', '2')[0] + 2.54, y2)
    sh.wire(sh.pin('R2', '2'), e2)
    sh.glabel('5V_RB', e2, right=True)


def draw_chain(sh, y, x0, left, jp, r_pi, r_rb, right, up=None, down=None, tp=None):
    """left label -> [JP] -> R_pi -> mid -> [R_rb] -> right label.
    up = (ref, supply): pull-up from the mid node; down = ref: pull-down to GND; tp = test pad ref."""
    xl = x0
    sh.glabel(left, (xl, y), right=False)
    pts = [(xl, y)]
    if jp:
        sh.part(jp, xl + 10.16, y, F_SJ)
        sh.wire((xl, y), sh.pin(jp, '1'))
        pts = [sh.pin(jp, '2')]
    sh.part(r_pi, xl + 25.4, y, F_R)
    sh.wire(pts[-1], sh.pin(r_pi, '1'))
    a = sh.pin(r_pi, '2')
    if r_rb is None:                              # single series part straight to the radio side label
        mid = [a]
        b = (a[0] + 2.54 + (20.32 if tp else 0), y)
        if tp:
            n = (a[0] + 5.08, y)
            sh.wire(a, n, b)
            sh.junction(n)
            sh.wire(n, (n[0], y - 5.08))
            sh.part(tp, n[0], y - 5.08, F_TP)
        else:
            sh.wire(a, b)
        sh.glabel(right, b, right=True)
        return
    sh.part(r_rb, xl + 50.8, y, F_R)
    b = sh.pin(r_rb, '1')
    nodes = []
    if up:
        nodes.append(('up', (a[0] + 5.08, y)))
    if down:
        nodes.append(('down', (a[0] + 5.08, y)))
    if tp:
        nodes.append(('tp', (a[0] + 10.16, y)))
    sh.wire(a, *[n for _, n in nodes], b)
    for kind, n in nodes:
        sh.junction(n)
        if kind == 'up':
            ref, supply = up
            k = (n[0], y - 5.08)
            sh.wire(n, k)
            sh.part(ref, k[0] + 5.08, k[1], ((0, -2.54, None), (0, 2.54, None)))
            sh.power(supply, sh.pin(ref, '2'))
        elif kind == 'down':
            k = (n[0], y + 5.08)
            sh.wire(n, k)
            sh.part(down, k[0] + 5.08, k[1], ((0, -2.54, None), (0, 2.54, None)))
            sh.power('GND', sh.pin(down, '2'))
        else:
            k = (n[0], y - 5.08)
            sh.wire(n, k)
            sh.part(tp, k[0], k[1], F_TP)
    c = sh.pin(r_rb, '2')
    e = (c[0] + 2.54, y)
    sh.wire(c, e)
    sh.glabel(right, e, right=True)


def draw_swd(sh, X, Y):
    rows = [('JP4', 'SWDIO_PI', 'SWDIO_RB'), ('JP5', 'SWCLK_PI', 'SWCLK_RB'),
            ('JP6', 'SWO_PI', 'SWO_RB'), ('JP7', 'RESETn_PI', 'RESETn_RB')]
    for i, (jp, l, r) in enumerate(rows):
        y = Y + 7.62 * i
        sh.glabel(l, (X, y), right=False)
        sh.part(jp, X + 10.16, y, F_SJ)
        sh.wire((X, y), sh.pin(jp, '1'))
        e = (sh.pin(jp, '2')[0] + 5.08, y)
        sh.wire(sh.pin(jp, '2'), e)
        sh.glabel(r, e, right=True)
    # J2 Tag-Connect TC2030-NL (DNP), radio side of the cut points
    jx, jy = X + 53.34, Y + 10.16
    sh.part('J2', jx, jy, ((10.16, -10.16, 'left'), (10.16, 10.16, 'left')))
    nets = PARTS['J2'][4]
    v = sh.pin('J2', '1')
    top = (v[0], v[1] - 2.54)
    sh.wire(v, top)
    sh.power('VMCU_IN', top)
    sh.power('GND', sh.pin('J2', '5'))
    for num in ('2', '3', '4', '6'):
        p = sh.pin('J2', num)
        e = (p[0] + 2.54, p[1])
        sh.wire(p, e)
        sh.glabel(nets[num], e, right=True)


def draw_eeprom(sh, X, Y):
    """HAT ID EEPROM per the RPi HAT design guide: CAT24C32 at 0x50, 3.9k pull-ups, WP 1k pull-up + jumper."""
    sh.part('U2', X, Y, ((2.54, -12.7, 'left'), (2.54, 12.7, 'left')))
    sh.power('3V3_PI', sh.pin('U2', '8'))
    sh.power('GND', sh.pin('U2', '4'))
    bx = X - 12.7
    a = [sh.pin('U2', n) for n in ('1', '2', '3')]
    for p in a:
        sh.wire(p, (bx, p[1]))
    bot = (bx, a[-1][1] + 2.54)
    sh.wire((bx, a[0][1]), (bx, a[1][1]), (bx, a[2][1]), bot)
    sh.junction((bx, a[1][1])); sh.junction((bx, a[2][1]))
    sh.power('GND', bot)
    for num, net in (('5', 'ID_SD'), ('6', 'ID_SC'), ('7', 'EE_WP')):
        p = sh.pin('U2', num)
        e = (p[0] + 2.54, p[1])
        sh.wire(p, e)
        sh.glabel(net, e, right=True)
    # pull-ups: label -> R -> 3V3_PI
    xl = X + 40.64
    for i, (ref, net) in enumerate((('R15', 'ID_SD'), ('R16', 'ID_SC'), ('R17', 'EE_WP'))):
        y = Y - 10.16 + 8.89 * i
        sh.glabel(net, (xl, y), right=False)
        sh.part(ref, xl + 7.62, y, F_R)
        sh.wire((xl, y), sh.pin(ref, '1'))
        sh.power('3V3_PI', sh.pin(ref, '2'))
    # WP: jumper to GND (bridge to re-flash), test pad; C6 decoupling
    x2 = xl + 30.48
    y = Y - 10.16
    sh.glabel('EE_WP', (x2, y), right=False)
    sh.part('JP8', x2 + 7.62, y, F_SJ)
    sh.wire((x2, y), sh.pin('JP8', '1'))
    g_ = (sh.pin('JP8', '2')[0] + 2.54, y)
    sh.wire(sh.pin('JP8', '2'), g_)
    sh.power('GND', g_)
    y = Y - 1.27
    sh.glabel('EE_WP', (x2, y), right=False)
    sh.wire((x2, y), (x2 + 20.32, y))
    sh.part('TP3', x2 + 20.32, y, F_TP)
    sh.part('C6', x2 + 7.62, Y + 7.62, F_C)
    sh.power('3V3_PI', sh.pin('C6', '1'))
    sh.power('GND', sh.pin('C6', '2'))


def build():
    lib = symlib.build()
    sh = Sheet(lib)
    # ---- block A: RPi header
    sh.block('Raspberry Pi header', 'J1 = Samtec CLH-115 on RPi pins 1-30. Pin number = J1 (socket), [n] = RPi header pin.',
             15.24, 15.24, 134.62, 127.0)
    draw_header(sh, 74.93, 82.55)
    sh.text('Pi 3V3 (1, 17) feeds only the ID EEPROM. ID_SD / ID_SC: EEPROM + 3.9k only.\n'
            'cmdlink: PL011 on GPIO14 (dtoverlay=disable-bt).\nMode A: dtoverlay=spi0-1cs.', (20.32, 124.46))
    # ---- block B: P201
    sh.block('P201 - radio board header', 'I/Q SPI (EUSART), DRDY, cmdlink, SWD, VMCU  [BRD4265B expansion]',
             139.7, 15.24, 274.32, 127.0)
    draw_mezz(sh, 'P201', 194.31, 38.1, {'1': gnd_flagged, '2': to_power('VMCU_IN'), '39': to_power('GND'),
                                          '40': to_tp('TP6')})
    sh.text('P201-40 VRF_IN unused on BRD4265B (on-board DC-DC\nfeeds PAVDD): test pad only.\n'
            'Pin map = ionos-sdr-mini rev-B3 (validated).', (144.78, 124.46))
    # ---- block C: P200
    sh.block('P200 - radio board header', 'supply, reset, board-ID EEPROM (I2C)', 279.4, 15.24, 405.13, 127.0)
    draw_mezz(sh, 'P200', 321.31, 38.1, {'1': to_power('3V3_RB'), '2': to_power('GND'), '38': gnd_crossing,
                                          '40': long_label('I2C_SDA', 7.62)})
    sh.text('Board-ID EEPROM (M24C02, radio board) on Pi I2C1;\npull-ups: 1k8 on the Pi, none on the radio board (only WP).\n'
            'P200-36 USB_VREG: radio-board output, never drive.', (284.48, 124.46))
    # ---- block D: radio supply
    sh.block('Radio supply', 'Pi 5 V -> FB1 -> LP5907 -> 3V3_RADIO -> R1 0R -> VMCU_IN (P201-2)', 15.24, 132.08, 274.32, 198.12)
    draw_supply(sh, 22.86, 154.94)
    draw_jp1(sh, 200.66, 170.18)
    sh.part('TP5', 236.22, 154.94, F_TP)               # GND test pad
    sh.wire((236.22, 154.94), (231.14, 154.94))
    sh.power('GND', (231.14, 154.94))
    sh.text('FB1 + C1/C2 keep the Pi 5 V noise out of the LDO. LP5907: 250 mA, 6.5 uVrms, EN tied to IN.\n'
            'R1 0R = VMCU_IN current probe point. JP1 cut = board-ID EEPROM unpowered. R2 not fitted.',
            (20.32, 193.04))
    # ---- block E: I/Q link
    sh.block('I/Q link', '22 R at both ends (mode A: Pi drives SCLK/CS; mode B: FG23 drives them). JP2/JP3 = README SJ1/SJ2.',
             15.24, 203.2, 274.32, 284.48)
    x0, y0 = 50.8, 226.06
    draw_chain(sh, y0, x0, 'IQ_CS_HDR', 'JP3', 'R5', 'R6', 'IQ_CS_RB', up=('R7', '3V3_RADIO'))
    draw_chain(sh, y0 + 10.16, x0, 'IQ_SCLK_HDR', 'JP2', 'R3', 'R4', 'IQ_SCLK_RB')
    draw_chain(sh, y0 + 20.32, x0, 'IQ_DATA_PI', None, 'R8', 'R9', 'IQ_DATA_RB')
    draw_chain(sh, y0 + 30.48, x0, 'DRDY_PI', None, 'R10', 'R11', 'DRDY_RB', down='R12', tp='TP1')
    x1 = 165.1
    draw_chain(sh, y0, x1, 'IQ_MOSI_PI', None, 'R13', None, 'IQ_MOSI_RB')
    draw_chain(sh, y0 + 15.24, x1, 'CMD_PI', None, 'R14', None, 'CMD_RB', tp='TP2')
    sh.text('R13 (not fitted) = IQ_MOSI, in-band commands.\nR14 0R = cmdlink, Pi TXD -> FG23 PA06.', (165.1, 271.78))
    # ---- block F: SWD
    sh.block('SWD cut points', 'OpenOCD / pyOCD linuxgpiod from the Pi', 279.4, 132.08, 405.13, 198.12)
    draw_swd(sh, 299.72, 154.94)
    sh.text('Cut JP4-JP7 to use an external probe on J2\n(Tag-Connect TC2030-NL, not fitted). RESETn: open-drain only.',
            (284.48, 193.04))
    # ---- block G: ID EEPROM
    sh.block('ID EEPROM', 'RPi HAT design guide: CAT24C32 @ 0x50 on ID_SD / ID_SC, auto-loads the overlays',
             279.4, 203.2, 405.13, 248.92)
    draw_eeprom(sh, 304.8, 233.68)
    # ---- write
    tb = ['title_block', ['title', Q('ionos-maximus RPi adapter (FG23 radio board)')], ['date', Q('2026-10-26')],
          ['rev', Q('A')], ['company', Q('Z2 Labs')],
          ['comment', '1', Q('github.com/ionos-sdr/ionos-maximus-rpi-sdr  -  CERN-OHL-P-2.0')],
          ['comment', '2', Q('Generated by hardware/gen/gen_sch.py from design.py')]]
    libsyms = []
    for name in sorted(sh.used):
        s = list(lib[name])
        s[1] = Q(LIB + ':' + name)
        libsyms.append(s)
    sch = ['kicad_sch', ['version', '20260306'], ['generator', Q('eeschema')], ['generator_version', Q('10.0')],
           ['uuid', Q(ROOT)], ['paper', Q('A3')], tb, ['lib_symbols'] + libsyms] + sh.items + \
          [['sheet_instances', ['path', Q('/'), ['page', Q('1')]]], ['embedded_fonts', 'no']]
    symfile = ['kicad_symbol_lib', ['version', '20251024'], ['generator', Q('kicad_symbol_editor')],
               ['generator_version', Q('10.0')]] + [lib[n] for n in sorted(sh.used)]
    missing = set(PARTS) - set(sh.pos)
    assert not missing, 'parts not drawn: %s' % sorted(missing)
    os.makedirs(OUT, exist_ok=True)
    open(os.path.join(OUT, PRJ + '.kicad_sch'), 'w', encoding='utf-8', newline='\n').write(dump(sch) + '\n')
    open(os.path.join(OUT, PRJ + '.kicad_sym'), 'w', encoding='utf-8', newline='\n').write(dump(symfile) + '\n')
    print('parts', len(PARTS), 'power', sh.npwr, 'flags', sh.nflg, 'items', len(sh.items))


if __name__ == '__main__':
    build()
