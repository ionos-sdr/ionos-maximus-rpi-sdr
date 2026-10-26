"""3D context render: the adapter on a Raspberry Pi 3B, BRD4265B radio board plugged in, SMA whip antenna.

The Pi, the radio board and the antenna are simple box/cylinder VRML stand-ins (dimensions below), attached to a
copy of the board as one board-only footprint; the board file itself is not touched.
Run with KiCad's Python:  python.exe render_stack.py
"""
import os, subprocess
import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
KDIR = os.path.normpath(os.path.join(HERE, '..', 'kicad'))
OUT = os.path.normpath(os.path.join(HERE, '..', 'out'))
CLI = r'C:\Program Files\KiCad\10.0\bin\kicad-cli.exe'
IN = 25.4 / 10          # KiCad VRML unit = 0.1 inch

# ---- RPi 3B (raspberrypi.com mechanical drawing): 85 x 56 x 1.4, header centred 32.5 mm from the SD edge,
# 3.5 mm from the top edge; pin 1 = (8.37, 4.77) in Pi coordinates (px along the header, py inward).
# J1 holes on our board: RPi pin 1 at (5.253, -2.457), pin 2 at (2.713, -2.457), pins advance along -Y.
P1X, P1Y = 5.253, -2.457
PI_Z = -1.6 - 2.5       # the adapter rests on the 2.5 mm header body


def pi_to_board(px, py):
    return P1X + (py - 4.77), P1Y - (px - 8.37)


# ---- radio board stand-in (assumed: 32.5 mm along the connectors, 11 mm beyond P200, 5 mm beyond P201,
# SMA edge-launch on the P200 end) - VERIFY on a real BRD4265B
XC, YP200, YP201 = 29.4, -4.6, -28.6
RB = (XC - 16.25, XC + 16.25, YP201 - 5.0, YP200 + 11.0)
RB_Z = 5.0               # mated TFM-120-02 / WCON stack, approx.


def vbox(x0, x1, y0, y1, z0, z1, rgb):
    """Axis-aligned box in board coordinates (y down) -> VRML (y up, 0.1 inch units)."""
    cx, cy, cz = (x0 + x1) / 2, -(y0 + y1) / 2, (z0 + z1) / 2
    sx, sy, sz = abs(x1 - x0), abs(y1 - y0), abs(z1 - z0)
    return ('Transform { translation %.4f %.4f %.4f children Shape { appearance Appearance { material Material '
            '{ diffuseColor %.3f %.3f %.3f } } geometry Box { size %.4f %.4f %.4f } } }\n'
            % (cx / IN, cy / IN, cz / IN, rgb[0], rgb[1], rgb[2], sx / IN, sy / IN, sz / IN))


def vcyl(x, y, z, r, h, axis, rgb):
    """Cylinder centred at (x, y, z) board coords, axis 'y' (along board Y) or 'z' (vertical)."""
    rot = '0 0 1 0' if axis == 'y' else '1 0 0 1.5708'
    return ('Transform { translation %.4f %.4f %.4f rotation %s children Shape { appearance Appearance { material '
            'Material { diffuseColor %.3f %.3f %.3f } } geometry Cylinder { radius %.4f height %.4f } } }\n'
            % (x / IN, -y / IN, z / IN, rot, rgb[0], rgb[1], rgb[2], r / IN, h / IN))


def pi_box(px0, px1, py0, py1, z0, h, rgb):
    a, b = pi_to_board(px0, py0), pi_to_board(px1, py1)
    return vbox(a[0], b[0], a[1], b[1], z0, z0 + h, rgb)


GREEN, BLACK, METAL, GOLD, WHITE, FR4 = (0.05, 0.35, 0.12), (0.06, 0.06, 0.06), (0.75, 0.75, 0.78), (0.85, 0.65, 0.2), (0.9, 0.9, 0.9), (0.1, 0.25, 0.55)


def write_models():
    top = PI_Z
    pi = [pi_box(0, 85, 0, 56, top - 1.4, 1.4, GREEN),                       # PCB
          pi_box(7.1, 57.9, 1.0, 6.0, top, 2.5, BLACK),                      # 2x20 header body
          pi_box(69, 87, 2.5, 15.5, top, 16, METAL),                         # USB stack 1
          pi_box(69, 87, 20.5, 33.5, top, 16, METAL),                        # USB stack 2
          pi_box(66, 87, 38.5, 54.5, top, 13.5, METAL),                      # Ethernet
          pi_box(24, 39, 50, 57, top, 6.5, METAL),                           # HDMI
          pi_box(7, 15, 51, 57, top, 3, METAL),                              # micro USB
          pi_box(50, 57, 48, 60, top, 6, BLACK),                             # audio jack
          pi_box(22, 36, 25, 39, top, 1.2, (0.2, 0.2, 0.22)),                # SoC
          pi_box(44, 48, 33, 56, top, 5.5, WHITE),                           # CSI connector
          pi_box(1, 5, 15, 37, top, 5.5, WHITE)]                             # DSI connector
    for k in range(15):                                                      # header pins 1-30 through the adapter
        for row in (2.23, 4.77):
            x, y = pi_to_board(8.37 + 2.54 * k, row)
            pi.append(vbox(x - 0.32, x + 0.32, y - 0.32, y + 0.32, top, top + 8.5, GOLD))
    x0, x1, y0, y1 = RB
    rb = [vbox(x0, x1, y0, y1, RB_Z, RB_Z + 1.6, FR4),                          # radio board
          vbox(XC - 6, XC + 6, -16.6 - 6, -16.6 + 6, RB_Z + 1.6, RB_Z + 3.6, METAL),   # RF shield / SoC area
          vbox(XC - 3.2, XC + 3.2, y1 - 3.0, y1 + 1.0, RB_Z + 1.6, RB_Z + 8.0, GOLD),   # SMA body at the P200 end
          vbox(XC - 3.2, XC + 3.2, y1 + 1.0, y1 + 9.0, RB_Z + 1.6, RB_Z + 8.0, GOLD),        # SMA barrel, pointing out
          vbox(XC - 4.5, XC + 4.5, y1 + 9.0, y1 + 18.0, RB_Z + 0.3, RB_Z + 9.3, BLACK),      # antenna knuckle
          vbox(XC - 4.0, XC + 4.0, y1 + 9.5, y1 + 17.5, RB_Z + 9.3, RB_Z + 110.0, BLACK)]    # 868 MHz whip, upright
    for name, shapes in (('stack_rpi3b.wrl', pi), ('stack_radio.wrl', rb)):
        with open(os.path.join(OUT, name), 'w') as f:
            f.write('#VRML V2.0 utf8\n' + ''.join(shapes))


def main():
    os.makedirs(OUT, exist_ok=True)
    write_models()
    b = pcbnew.LoadBoard(os.path.join(KDIR, 'ionos_maximus.kicad_pcb'))
    fp = pcbnew.FOOTPRINT(b)
    fp.SetReference('CTX1')
    fp.Reference().SetVisible(False)
    fp.Value().SetVisible(False)
    fp.SetBoardOnly(True)
    fp.SetExcludedFromBOM(True)
    fp.SetExcludedFromPosFiles(True)
    for name in ('stack_rpi3b.wrl', 'stack_radio.wrl'):
        m = pcbnew.FP_3DMODEL()
        m.m_Filename = os.path.join(OUT, name).replace('\\', '/')
        fp.Models().append(m)
    b.Add(fp)
    tmp = os.path.join(OUT, 'stack.kicad_pcb')
    b.Save(tmp)
    for tag, rot, zoom in (('iso', '-60,0,-35', '0.30'), ('side', '-90,0,-90', '0.30'), ('top', '0,0,0', '0.5')):
        subprocess.run([CLI, 'pcb', 'render', '--side', 'top', '--rotate', rot, '--zoom', zoom, '--width', '1800',
                        '--height', '1300', '--quality', 'high', '-o', os.path.join(OUT, 'stack_%s.png' % tag), tmp],
                       check=True)
    print('rendered', OUT)


if __name__ == '__main__':
    main()
