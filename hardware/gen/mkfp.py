"""Build hardware/kicad/ionos_maximus.pretty from the validated sibling projects."""
import os, sys, shutil, copy, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sx import parse, find, val, dump, Q
GH = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..', '..'))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'kicad', 'ionos_maximus.pretty')
MINI = os.path.join(GH, 'ionos-sdr-mini/CAD/ionosmini_1v0')
RPISM = os.path.join(GH, 'RPi_ISM/CAD/kicad')
FROM_BOARD = ['RESC1005X40_0402', 'CAPC1005X60_0402', 'L5_0603', 'SOT-23-5', 'TESTPOINT_40MIL_SMD']
COPY = [os.path.join(MINI, 'ionosSDR_mini.pretty/TFC-120-02-XX-D-A-K-TR.kicad_mod'),
        os.path.join(RPISM, 'RPism_adapterboard_0V3-import-fps.pretty/2X15_SMT_CROSSPCB_ASSYM.kicad_mod')]
RPW3D = os.path.join(os.path.expanduser('~'), 'mnt', '3d_models') if os.path.isdir(
    os.path.join(os.path.expanduser('~'), 'mnt', '3d_models')) else os.path.join(os.environ.get('RPW_LIB', 'C:/Gitlab/rpw-lib'), '3d_models')
MODELS = [os.path.join(RPW3D, f) for f in ('CAPC1005X60_0402.step', 'L5_0603.step', 'RESC1005X40_0402.step', 'SOT-23-5.step')] + [
    os.path.join(MINI, 'ionosSDR_mini_3d_models', 'TFC-120-02-F-D-A.stp'),
    os.path.join(RPISM, '3dmodels', 'PinSocket_2x15_P2.54mm_Vertical_SMD.step')]
# CLH-115 stand-in model and placement exactly as on the RPi_ISM board
CLH_MODEL = '''
	(model "${KIPRJMOD}/3dmodels/PinSocket_2x15_P2.54mm_Vertical_SMD.step"
		(offset (xyz 2.57 0.03 0))
		(scale (xyz 1 1 1))
		(rotate (xyz 0 0 90))
	)
'''
DROP = {'at', 'uuid', 'path', 'sheetname', 'sheetfile', 'units', 'component_classes'}

def from_board():
    t = parse(open(os.path.join(MINI, 'ionosSDR_mini.kicad_pcb'), encoding='utf-8').read())
    got = {}
    for f in find(t, 'footprint'):
        name = f[1].split(':')[-1]
        at = find(f, 'at')[0]
        if name in FROM_BOARD and val(f, 'layer') == 'F.Cu':
            rot = float(at[3]) if len(at) > 3 else 0.0
            if name not in got or (got[name][1] != 0 and rot == 0):
                got[name] = (f, rot)
    out = {}
    for name, (f, rot) in got.items():
        m = [Q('footprint'), Q(name)][:0] + ['footprint', Q(name), ['version', '20260206'], ['generator', Q('pcbnew')],
                                              ['generator_version', Q('10.0')]]
        for x in f[2:]:
            if not isinstance(x, list) or x[0] in DROP:
                continue
            x = copy.deepcopy(x)
            if x[0] == 'property' and x[1] in ('Reference', 'Value'):
                x[2] = Q({'Reference': 'REF**', 'Value': name}[x[1]])
            if x[0] == 'property' and x[1] not in ('Reference', 'Value', 'Footprint', 'Datasheet', 'Description'):
                continue
            if x[0] == 'property' and x[1] == 'Footprint':
                continue
            for a in [y for y in x if isinstance(y, list) and y[0] == 'at' and len(y) > 3] if x[0] in ('pad', 'property', 'fp_text') else []:
                r = (float(a[3]) - rot) % 360
                a[:] = a[:3] + ([('%g' % r)] if r else [])
            if x[0] == 'pad':
                x[:] = [y for y in x if not (isinstance(y, list) and y[0] in ('net', 'uuid', 'pintype', 'pinfunction'))]
            if x[0] in ('fp_line', 'fp_circle', 'fp_arc', 'fp_poly', 'fp_rect', 'fp_text', 'zone'):
                x[:] = [y for y in x if not (isinstance(y, list) and y[0] == 'uuid')]
            m.append(x)
        out[name] = m
    return out

if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    fps = from_board()
    missing = set(FROM_BOARD) - set(fps)
    assert not missing, missing
    for name, m in fps.items():
        open(os.path.join(OUT, name + '.kicad_mod'), 'w', encoding='utf-8', newline='\n').write(dump(m) + '\n')
    for p in COPY:
        shutil.copy(p, OUT)
    # 3D models: copied into the project, referenced through ${KIPRJMOD} (RPi_ISM / QRPi style)
    m3d = os.path.join(os.path.dirname(OUT), '3dmodels')
    os.makedirs(m3d, exist_ok=True)
    for src in MODELS:
        shutil.copy(src, m3d)
    for n in os.listdir(OUT):
        p = os.path.join(OUT, n)
        t = open(p, encoding='utf-8').read()
        t = re.sub(r'\(model "[^"]*/([^"/]+)"', lambda m: '(model "${KIPRJMOD}/3dmodels/%s"' % m.group(1), t)
        if n.startswith('2X15') and '(model' not in t:
            t = t.rstrip().rstrip(')') + CLH_MODEL + ')\n'
        open(p, 'w', encoding='utf-8', newline='\n').write(t)
    for n in sorted(os.listdir(OUT)):
        s = open(os.path.join(OUT, n), encoding='utf-8').read()
        print(n, s.count('(pad '), [l.strip() for l in s.splitlines() if 'model' in l][:2])
