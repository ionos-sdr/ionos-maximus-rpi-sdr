"""Realistic context renders: the adapter on a Raspberry Pi 3B, BRD4265B plugged in, SMA whip antenna.

No vendor CAD is used (none is published under a licence that allows this repo): the Pi and the radio board are
built here as KiCad boards from KiCad's own library footprints and 3D models (CC-BY-SA), at the positions of the
Raspberry Pi 3B mechanical drawing and the assumed BRD4265B outline. Both are built directly in the adapter's
coordinate frame, exported to STEP and attached to a copy of the adapter as board-only 3D models.
Run with KiCad's Python:  python.exe render_context.py
"""
import os, math, subprocess
import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
KDIR = os.path.normpath(os.path.join(HERE, '..', 'kicad'))
OUT = os.path.normpath(os.path.join(HERE, '..', 'out', 'context'))
CLI = r'C:\Program Files\KiCad\10.0\bin\kicad-cli.exe'
FPL = r'C:\Program Files\KiCad\10.0\share\kicad\footprints'
IN = 25.4 / 10                       # VRML unit

# ---- Raspberry Pi 3B, mechanical drawing frame: px along the header edge (0..85), py from the header edge (0..56).
# GPIO pin 1 at (8.37, 4.77); J1 hole of RPi pin 1 on the adapter is at (5.253, -2.457).
P1X, P1Y = 5.253, -2.457
PI_TOP = -2.5                        # adapter bottom rests on the 2.5 mm header body
PI_T = 1.4


def pi_xy(px, py):
    return P1X + (py - 4.77), P1Y - (px - 8.37)


# (footprint, px, py, rot in the Pi frame, side)
PI_PARTS = [
    ('Connector_PinHeader_2.54mm:PinHeader_2x20_P2.54mm_Vertical', 8.37, 4.77, 90, 'F'),
    ('Connector_USB:USB_A_Molex_67643_Horizontal', 79.0, 9.0, 0, 'F'),
    ('Connector_USB:USB_A_Molex_67643_Horizontal', 79.0, 27.0, 0, 'F'),
    ('Connector_RJ:RJ45_Amphenol_RJHSE538X', 76.0, 45.75, 0, 'F'),
    ('Connector_Video:HDMI_A_Molex_208658-1001_Horizontal', 32.0, 52.0, 0, 'F'),
    ('Connector_USB:USB_Micro-B_Molex_47346-0001', 10.6, 54.0, 0, 'F'),
    ('Connector_Audio:Jack_3.5mm_CUI_SJ1-3523N_Horizontal', 53.5, 50.0, 0, 'F'),
    ('Package_BGA:BGA-144_13.0x13.0mm_Layout12x12_P1.0mm', 30.0, 31.0, 0, 'F'),
    ('Package_DFN_QFN:QFN-64-1EP_9x9mm_P0.5mm_EP4.1x4.1mm', 59.0, 31.0, 0, 'F'),
    ('Connector_Card:microSD_HC_Hirose_DM3D-SF', 6.0, 28.0, 90, 'B'),
]
STACKED = {'Connector_USB:USB_A_Molex_67643_Horizontal': 7.2}   # second port of each USB stack, mm above the first
PI_HOLES = [(3.5, 3.5), (61.5, 3.5), (3.5, 52.5), (61.5, 52.5)]

# ---- radio board stand-in (VERIFY on a real BRD4265B): 32.5 mm along the connectors, 11 mm beyond P200,
# 5 mm beyond P201; SMA on the P200 end; sockets on its bottom side.
XC, YP200, YP201 = 29.4, -4.6, -28.6
RB = (XC - 16.25, YP201 - 5.0, XC + 16.25, YP200 + 11.0)
RB_Z = 1.6 + 4.6                     # adapter top + mated TFM-120 / WCON stack (approx.)
RB_PARTS = [
    ('Connector_PinSocket_1.27mm:PinSocket_2x20_P1.27mm_Vertical_SMD', XC, YP200, 90, 'B'),
    ('Connector_PinSocket_1.27mm:PinSocket_2x20_P1.27mm_Vertical_SMD', XC, YP201, 90, 'B'),
    ('Package_DFN_QFN:QFN-48-1EP_7x7mm_P0.5mm_EP5.15x5.15mm', XC, -15.5, 45, 'F'),
    ('Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm', XC - 7.0, -19.0, 0, 'F'),
    ('Connector_Coaxial:SMA_Samtec_SMA-J-P-H-ST-EM1_EdgeMount', XC, RB[3], 270, 'F'),
    ('Package_TO_SOT_SMD:SOT-23-5', XC + 9.0, -27.0, 0, 'F'),
    ('Inductor_SMD:L_0603_1608Metric', XC + 6.0, -9.0, 0, 'F'),
] + [('Capacitor_SMD:C_0402_1005Metric', XC + dx, y, 90, 'F')
     for dx, y in ((-3.0, -9.0), (-1.5, -9.0), (0.0, -9.0), (1.5, -9.0), (3.0, -9.0), (5.0, -20.0),
                   (6.5, -20.0), (-5.0, -12.0), (-5.0, -10.5), (2.0, -3.0), (-2.0, -3.0))] + \
    [('Resistor_SMD:R_0402_1005Metric', XC + dx, -24.0, 0, 'F') for dx in (-8.0, -6.0, 6.0, 8.0)]


def load(fpid):
    lib, name = fpid.split(':')
    fp = pcbnew.FootprintLoad(os.path.join(FPL, lib + '.pretty'), name)
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    return fp


def v(x, y):
    return pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y))


def outline(b, pts, r):
    """Closed outline with rounded corners (pts in order, convex)."""
    n = len(pts)
    for i in range(n):
        a, c, d = pts[i - 1], pts[i], pts[(i + 1) % n]
        u = ((a[0] - c[0]), (a[1] - c[1])); w = ((d[0] - c[0]), (d[1] - c[1]))
        lu, lw = math.hypot(*u), math.hypot(*w)
        p1 = (c[0] + u[0] / lu * r, c[1] + u[1] / lu * r)
        p2 = (c[0] + w[0] / lw * r, c[1] + w[1] / lw * r)
        mid = (c[0] + (u[0] / lu + w[0] / lw) * r * (1 - math.sqrt(0.5)), c[1] + (u[1] / lu + w[1] / lw) * r * (1 - math.sqrt(0.5)))
        arc = pcbnew.PCB_SHAPE(b, pcbnew.SHAPE_T_ARC)
        arc.SetArcGeometry(v(*p1), v(*mid), v(*p2))
        arc.SetLayer(pcbnew.Edge_Cuts); arc.SetWidth(pcbnew.FromMM(0.1)); b.Add(arc)
        ln = pcbnew.PCB_SHAPE(b, pcbnew.SHAPE_T_SEGMENT)
        q1 = p2
        q2 = (d[0] - w[0] / lw * r, d[1] - w[1] / lw * r)
        ln.SetStart(v(*q1)); ln.SetEnd(v(*q2)); ln.SetLayer(pcbnew.Edge_Cuts); ln.SetWidth(pcbnew.FromMM(0.1)); b.Add(ln)


def hole(b, x, y, d):
    fp = pcbnew.FOOTPRINT(b)
    pad = pcbnew.PAD(fp)
    pad.SetAttribute(pcbnew.PAD_ATTRIB_NPTH); pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
    pad.SetSize(v(d, d)); pad.SetDrillSize(v(d, d)); pad.SetLayerSet(pad.UnplatedHoleMask())
    fp.Add(pad); fp.SetPosition(v(x, y)); b.Add(fp)


def place(b, fpid, x, y, rot, side):
    fp = load(fpid)
    fp.Reference().SetVisible(False); fp.Value().SetVisible(False)
    b.Add(fp)
    fp.SetPosition(v(x, y)); fp.SetOrientationDegrees(rot)
    if side == 'B':
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)
    return fp


def build(path, corners, r, parts, holes, color):
    b = pcbnew.NewBoard(path)
    outline(b, corners, r)
    for x, y, d in holes:
        hole(b, x, y, d)
    for p in parts:
        fp = place(b, *p)
        dz = STACKED.get(p[0])
        if dz:
            m0 = fp.Models()[0]
            m = pcbnew.FP_3DMODEL()
            m.m_Filename = m0.m_Filename
            m.m_Offset = pcbnew.VECTOR3D(m0.m_Offset.x, m0.m_Offset.y, m0.m_Offset.z + dz)
            m.m_Rotation = m0.m_Rotation
            m.m_Scale = m0.m_Scale
            fp.Models().append(m)
    b.Save(path)


def step(pcb, out):
    subprocess.run([CLI, 'pcb', 'export', 'step', '--force', '--user-origin', '0x0mm', '--subst-models',
                    '-o', out, pcb], check=True, capture_output=True)


def vcyl(x, y, z, r, h, axis, rgb, n=40):
    """Cylinder as an IndexedFaceSet (board coords: y down; axis 'z' vertical or 'y' along the board)."""
    pts, faces = [], []
    for k in range(n):
        a = 2 * math.pi * k / n
        c, s_ = r * math.cos(a), r * math.sin(a)
        for t in (-h / 2, h / 2):
            if axis == 'z':
                px, py, pz = x + c, y + s_, z + t
            else:
                px, py, pz = x + c, y + t, z + s_
            pts.append((px / IN, -py / IN, pz / IN))
    for k in range(n):
        a0, a1, b0, b1 = 2 * k, 2 * k + 1, 2 * ((k + 1) % n), 2 * ((k + 1) % n) + 1
        faces.append((a0, b0, b1, a1))
    faces.append(tuple(2 * k for k in range(n))[::-1])
    faces.append(tuple(2 * k + 1 for k in range(n)))
    return ('Shape { appearance Appearance { material Material { diffuseColor %.3f %.3f %.3f specularColor 0.3 0.3 0.3 '
            'shininess 0.4 } } geometry IndexedFaceSet { solid FALSE coord Coordinate { point [ %s ] } coordIndex [ %s ] } }\n'
            % (rgb[0], rgb[1], rgb[2], ', '.join('%.4f %.4f %.4f' % p for p in pts),
               ', '.join(' '.join(map(str, f)) + ' -1' for f in faces)))


def antenna(path):
    """Hinged 868 MHz whip on the SMA: gold coupling nut, black hinge, black radome, bent upright."""
    x, y0, z = XC, RB[3] + 7.0, RB_Z + 0.8
    gold, black = (0.85, 0.68, 0.25), (0.05, 0.05, 0.05)
    s = [vcyl(x, y0 + 3.0, z, 4.0, 6.0, 'y', gold),           # coupling nut
         vcyl(x, y0 + 7.5, z, 4.6, 3.0, 'y', black),          # hinge
         vcyl(x, y0 + 9.0, z + 37.0, 4.4, 70.0, 'z', black),   # radome, lower part
         vcyl(x, y0 + 9.0, z + 87.0, 3.7, 30.0, 'z', black),   # radome, upper part
         vcyl(x, y0 + 9.0, z + 103.0, 2.6, 2.0, 'z', black)]   # tip
    open(path, 'w').write('#VRML V2.0 utf8\n' + ''.join(s))


def main():
    os.makedirs(OUT, exist_ok=True)
    # Raspberry Pi 3B in the adapter frame
    c = [pi_xy(*p) for p in ((0, 0), (85, 0), (85, 56), (0, 56))]
    parts = [(f, *pi_xy(px, py), rot + 90, side) for f, px, py, rot, side in PI_PARTS]
    holes = [(*pi_xy(px, py), 2.75) for px, py in PI_HOLES]
    pi = os.path.join(OUT, 'rpi3b.kicad_pcb')
    build(pi, c, 3.0, parts, holes, '#1E7A3A')
    rb = os.path.join(OUT, 'brd4265b.kicad_pcb')
    build(rb, [(RB[0], RB[1]), (RB[2], RB[1]), (RB[2], RB[3]), (RB[0], RB[3])], 1.0, RB_PARTS, [], '#14191F')
    step(pi, os.path.join(OUT, 'rpi3b.step'))
    step(rb, os.path.join(OUT, 'brd4265b.step'))
    antenna(os.path.join(OUT, 'antenna.wrl'))
    # adapter copy with the context models
    b = pcbnew.LoadBoard(os.path.join(KDIR, 'ionos_maximus.kicad_pcb'))
    fp = pcbnew.FOOTPRINT(b)
    fp.SetReference('CTX1'); fp.Reference().SetVisible(False); fp.Value().SetVisible(False)
    fp.SetBoardOnly(True); fp.SetExcludedFromBOM(True); fp.SetExcludedFromPosFiles(True)
    for name, z in (('rpi3b.step', PI_TOP - PI_T), ('brd4265b.step', RB_Z), ('antenna.wrl', 0.0)):
        m = pcbnew.FP_3DMODEL()
        m.m_Filename = os.path.join(OUT, name).replace('\\', '/')
        m.m_Offset = pcbnew.VECTOR3D(0, 0, z)
        fp.Models().append(m)
    b.Add(fp)
    ctx = os.path.join(KDIR, '_context.kicad_pcb')       # next to the project, so ${KIPRJMOD} models resolve
    b.Save(ctx)
    print('built', ctx)
    views = [('iso', '-62,0,150', '0.27'), ('iso_back', '-62,0,35', '0.28'), ('side', '-88,0,-90', '0.30'),
             ('top', '0,0,0', '0.55')]
    only = os.environ.get('VIEWS')
    if only:
        views = [x for x in views if x[0] in only.split(',')]
    for tag, rot, zoom in views:
        subprocess.run([CLI, 'pcb', 'render', '--side', 'top', '--rotate', rot, '--zoom', zoom, '--perspective',
                        '--floor', '--width', '1800', '--height', '1350', '--quality', os.environ.get('Q', 'high'),
                        '-o', os.path.join(OUT, 'stack_%s.png' % tag), ctx], check=True, capture_output=True)
        print('rendered', tag)


if __name__ == '__main__':
    main()
