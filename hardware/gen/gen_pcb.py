"""Build ionos_maximus.kicad_pcb (placement, outline, zones; not routed) with KiCad 10 pcbnew.

Run with KiCad's Python:  "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" gen_pcb.py <netlist.net>
Board frame, outline and J1 are taken 1:1 from RPi_ISM (rev1), so the mechanics on the Pi are identical.
"""
import os, sys
import pcbnew
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sx import netmap, parse, find, val
import design

KDIR = os.path.normpath(os.path.join(HERE, '..', 'kicad'))
PCB = os.path.join(KDIR, 'ionos_maximus.kicad_pcb')
GH = os.path.normpath(os.path.join(HERE, '..', '..', '..'))
RPISM = os.path.join(GH, 'RPi_ISM', 'CAD', 'kicad', 'RPism_adapterboard_0V3.kicad_pcb')
STD = r'C:\Program Files\KiCad\10.0\share\kicad\footprints'
LIBS = {'ionos_maximus': os.path.join(KDIR, 'ionos_maximus.pretty')}

XC = 29.4                       # radio-board connector centre x
YP200, YP201 = -4.6, -28.6      # 24 mm apart; pair rotated 180 deg vs ionos-sdr-mini (P201 at the top)


def p201x(n):                   # P201 / P200 pad x for pin n (rotated 180 deg)
    return XC + 12.065 - 1.27 * ((n - 1) // 2)


# ref: (x, y, rot)
PLACE = {
    'J1': (4.0132, -17.667021, 90),
    'P200': (XC, YP200, 180), 'P201': (XC, YP201, 180),
    # top strip, radio side: series parts right above the P201 outer row, one per pin
    'R13': (p201x(7), -34.4, 90), 'R11': (p201x(9), -34.4, 90), 'R14': (p201x(11), -34.4, 90),
    'R6': (p201x(13), -34.4, 90), 'R4': (p201x(15), -34.4, 90),
    'R7': (34.6, -36.4, 0), 'R12': (37.0, -36.4, 0), 'TP1': (39.4, -36.4, 0), 'TP2': (41.4, -36.4, 0),
    # top strip, left: HAT ID EEPROM (outside the radio-board shadow)
    'U2': (18.6, -36.3, 0), 'JP8': (24.2, -36.3, 90), 'TP3': (26.6, -36.0, 0),
    'R15': (10.0, -36.2, 0), 'R16': (10.0, -37.6, 0), 'C6': (12.6, -36.2, 0), 'R17': (12.6, -37.6, 0),
    # left strip next to J1: Pi-side series parts and the isolation jumpers (README SJ1/SJ2)
    'JP2': (10.4, -31.4, 0), 'R3': (12.9, -31.4, 90), 'JP3': (10.4, -34.0, 0), 'R5': (12.9, -34.0, 90),
    'R8': (10.0, -28.6, 0), 'R10': (12.9, -28.6, 90), 'TP5': (10.0, -26.6, 0),
    'JP6': (10.4, -16.0, 0), 'JP7': (10.4, -18.7, 0), 'JP5': (10.4, -21.4, 0), 'JP4': (10.4, -24.1, 0),
    'TP7': (10.0, -13.6, 0),
    'JP9': (10.6, -4.6, 0), 'JP10': (10.6, -1.9, 0), 'R18': (13.0, -5.2, 90), 'R19': (13.0, -2.2, 90), 'R2': (15.2, -11.0, 0),
    # middle band (under the radio board): radio supply next to VMCU_IN (P201-2) and P200-1
    'C1': (30.2, -19.5, 90), 'FB1': (31.9, -19.5, 0), 'C2': (33.6, -19.5, 90), 'U1': (37.0, -19.5, 0),
    'C3': (40.3, -19.0, 90), 'C4': (41.6, -19.0, 90), 'R1': (p201x(2), -22.6, 90), 'C5': (39.9, -22.6, 90),
    'JP1': (43.6, -17.0, 90), 'TP4': (43.8, -22.4, 0),
    'R9': (p201x(10), -22.6, 90), 'TP6': (p201x(40), -23.4, 0), 'J2': (22.0, -19.5, 0),
}
# radio board outline (verify against a real BRD4265B): 32.5 mm along the connectors,
# 11 mm beyond P200 and 5 mm beyond P201
RB = (XC - 16.25, YP201 - 5.0, XC + 16.25, YP200 + 11.0)


def mm(v):
    return pcbnew.FromMM(v)


def vec(x, y):
    return pcbnew.VECTOR2I(mm(x), mm(y))


def load_fp(fpid):
    lib, name = fpid.split(':')
    path = LIBS.get(lib, os.path.join(STD, lib + '.pretty'))
    fp = pcbnew.FootprintLoad(path, name)
    if fp is None:
        raise SystemExit('footprint not found: ' + fpid)
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    return fp


def main(netfile):
    pins, comps = netmap(netfile)
    tree = parse(open(netfile, encoding='utf-8').read())
    tstamp = {val(c, 'ref'): val(c, 'tstamps') for c in find(find(tree, 'components')[0], 'comp')}
    dnp = {p[0] for p in design.PARTS if p[5]}
    global NOTE
    NOTE = {p[0]: p[6].get('Note', '') for p in design.PARTS}
    board = pcbnew.NewBoard(PCB)
    board.SetCopperLayerCount(2)
    nets = {}
    for (ref, pin), (net, _) in pins.items():
        if net not in nets:
            ni = pcbnew.NETINFO_ITEM(board, net)
            board.Add(ni)
            nets[net] = ni
    for ref, (value, fpid) in sorted(comps.items()):
        fp = load_fp(fpid)
        fp.SetReference(ref)
        fp.SetValue(value)
        fp.SetPath(pcbnew.KIID_PATH('/' + tstamp[ref]))
        for f in fp.GetFields():
            if f.GetName() == 'Description':
                f.SetText(NOTE.get(ref, ''))
            if f.GetName() == 'Datasheet':
                f.SetText('')
        if ref[0] in 'RCL' or ref.startswith(('JP', 'TP', 'FB', 'P20')):
            fp.Reference().SetVisible(False)       # 0402 field: refs live on F.Fab / assembly drawing
        board.Add(fp)
        x, y, rot = PLACE[ref]
        fp.SetPosition(vec(x, y))
        fp.SetOrientationDegrees(rot)
        if ref in dnp:
            fp.SetDNP(True)
            fp.SetExcludedFromBOM(True)
            fp.SetExcludedFromPosFiles(True)
        for pad in fp.Pads():
            key = (ref, pad.GetNumber())
            if key in pins and pins[key][0] in nets:
                pad.SetNet(nets[pins[key][0]])
    # outline from RPi_ISM, same coordinates
    src = pcbnew.LoadBoard(RPISM)
    for d in src.GetDrawings():
        if d.GetLayer() == pcbnew.Edge_Cuts:
            board.Add(d.Duplicate())
    # radio board outline for reference
    r = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_RECT)
    r.SetStart(vec(RB[0], RB[1]))
    r.SetEnd(vec(RB[2], RB[3]))
    r.SetLayer(pcbnew.Dwgs_User)
    r.SetWidth(mm(0.1))
    board.Add(r)
    t = pcbnew.PCB_TEXT(board)
    t.SetText('RADIO BOARD (BRD4265B) outline - verify')
    t.SetPosition(vec(XC, RB[1] + 1.0))
    t.SetLayer(pcbnew.Dwgs_User)
    board.Add(t)
    # GND pour on both layers over the whole outline
    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
        z = pcbnew.ZONE(board)
        z.SetLayer(layer)
        z.SetNet(nets['GND'])
        ol = z.Outline()
        ol.NewOutline()
        for x, y in ((-1, 1), (46, 1), (46, -40.1), (-1, -40.1)):
            ol.Append(mm(x), mm(y))
        z.SetLocalClearance(mm(0.3))
        z.SetMinThickness(mm(0.2))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_THT_THERMAL)   # THT thermal, SMD solid (as RPi_ISM)
        z.SetAssignedPriority(0)
        board.Add(z)
    board.Save(PCB)
    print('saved', PCB, 'footprints', len(board.GetFootprints()), 'nets', len(nets))


if __name__ == '__main__':
    main(sys.argv[1])
