"""Check a KiCad netlist export against the README pin map, independently of design.py.

Each README row is an RPi header pin and a radio-board connector pin that must end up on the same
signal path; the path may pass through the listed series parts (22 R, 0 R, solder jumpers), never
through anything else. Usage: python3 check_pinmap.py <netlist.net>
"""
import os, sys
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sx import netmap

# README tables: signal, RPi physical pin, mezzanine pin
README = [
    ('IQ_SCLK', 23, 'P201', 15), ('IQ_DATA', 21, 'P201', 10), ('IQ_CS', 24, 'P201', 13),
    ('IQ_MOSI', 19, 'P201', 7), ('DRDY', 22, 'P201', 9), ('CMD', 8, 'P201', 11),
    ('SWDIO', 18, 'P201', 21), ('SWCLK', 16, 'P201', 23), ('SWO', 13, 'P201', 19),
    ('RESETn', 15, 'P200', 17), ('BOARD_ID_SDA', 3, 'P200', 40), ('BOARD_ID_SCL', 5, 'P200', 39),
]
GND_RPI = [6, 9, 14, 20, 25, 30]          # within J1 (pins 1-30); 34 and 39 are beyond the 2x15 socket
GND_MEZ = [('P200', 2), ('P200', 38), ('P201', 1), ('P201', 39)]
SERIES = ('R', 'JP')                       # two-pin parts allowed in a signal path


def j1(rpi_pin):                           # RPi_ISM CLH-115 socket numbering
    return rpi_pin - 1 if rpi_pin % 2 == 0 else rpi_pin + 1


def main(fn):
    pins, comps = netmap(fn)
    net_of = {k: v[0] for k, v in pins.items()}
    members = defaultdict(set)
    for (r, p), n in net_of.items():
        members[n].add((r, p))
    fails = 0

    def path(a, b):
        """BFS over nets through two-pin series parts; returns the part chain or None."""
        start, goal = net_of.get(a), net_of.get(b)
        seen, q = {start: []}, [start]
        while q:
            n = q.pop(0)
            if n == goal:
                return seen[n]
            for r, p in members[n]:
                if not r.startswith(SERIES) or r in sum(seen.values(), []):
                    continue
                other = [net_of[(r, x)] for x in ('1', '2') if (r, x) in net_of and net_of[(r, x)] != n]
                for o in other:
                    if o not in seen:
                        seen[o] = seen[n] + [r]
                        q.append(o)
        return None

    for sig, rp, con, cp in README:
        ch = path(('J1', str(j1(rp))), (con, str(cp)))
        ok = ch is not None
        fails += not ok
        print('%-4s %-13s RPi %2d (J1-%-2d) -> %s-%-2d via %s' % ('PASS' if ok else 'FAIL', sig, rp, j1(rp), con, cp,
              ('-'.join(ch) or 'direct') if ch is not None else 'NO PATH'))
    for rp in GND_RPI:
        ok = net_of.get(('J1', str(j1(rp)))) == 'GND'
        fails += not ok
        print('%-4s GND RPi %d' % ('PASS' if ok else 'FAIL', rp))
    for c, p in GND_MEZ:
        ok = net_of.get((c, str(p))) == 'GND'
        fails += not ok
        print('%-4s GND %s-%d' % ('PASS' if ok else 'FAIL', c, p))
    # supply: VMCU_IN (P201-2) from the LDO, which is fed from RPi 5 V (pins 2, 4); 3V3_RB on P200-1
    checks = [(net_of.get(('P201', '2')) == 'VMCU_IN', 'P201-2 = VMCU_IN'),
              (net_of.get(('U1', '5')) == '3V3_RADIO', 'LDO out = 3V3_RADIO'),
              (path(('U1', '5'), ('P201', '2')) is not None, 'LDO -> VMCU_IN via 0 R'),
              (path(('U1', '5'), ('P200', '1')) is not None, 'LDO -> P200-1 (board ID supply)'),
              (net_of.get(('J1', str(j1(2)))) == '5V0' and net_of.get(('J1', str(j1(4)))) == '5V0', 'RPi 5 V pins 2, 4'),
              (net_of.get(('P200', '36'), 'unconnected').startswith('unconnected'), 'P200-36 USB_VREG left open'),
              ({r for r, _ in members[net_of[('J1', str(j1(27)))]]} <= {'J1', 'U2', 'R15'}, 'ID_SD only EEPROM + pull-up'),
              ({r for r, _ in members[net_of[('J1', str(j1(28)))]]} <= {'J1', 'U2', 'R16'}, 'ID_SC only EEPROM + pull-up')]
    for ok, what in checks:
        fails += not ok
        print('%-4s %s' % ('PASS' if ok else 'FAIL', what))
    # which RPi pins carry anything at all (must be exactly the README set)
    used = sorted(rp for rp in range(1, 31) if not net_of.get(('J1', str(j1(rp))), 'unconnected').startswith('unconnected'))
    print('RPi pins used:', used)
    print('RESULT', 'PASS' if not fails else 'FAIL (%d)' % fails)
    return fails


if __name__ == '__main__':
    sys.exit(1 if main(sys.argv[1]) else 0)
