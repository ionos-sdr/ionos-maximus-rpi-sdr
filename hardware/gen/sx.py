"""Minimal S-expression reader/writer for KiCad files; keeps track of quoted atoms."""
import re


class Q(str):
    """An atom that was quoted in the source (or must be quoted on output)."""


def _unesc(s):
    return s.replace('\\"', '"').replace('\\\\', '\\')


def parse(s):
    toks = re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+', s)
    st = [[]]
    for t in toks:
        if t == '(':
            st.append([])
        elif t == ')':
            x = st.pop()
            st[-1].append(x)
        else:
            st[-1].append(Q(_unesc(t[1:-1])) if t.startswith('"') else t)
    return st[0][0]


def find(n, key):
    return [c for c in n if isinstance(c, list) and c and c[0] == key]


def val(n, key, d=None):
    f = find(n, key)
    return f[0][1] if f and len(f[0]) > 1 else d


def atom(n):
    if isinstance(n, Q) or n == '' or re.search(r'[\s()"]', n):
        return '"' + str(n).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'
    return str(n)


def dump(n, ind=0):
    if not isinstance(n, list):
        return atom(n)
    if all(not isinstance(x, list) for x in n):
        return '(' + ' '.join(atom(x) for x in n) + ')'
    head = [x for x in n if not isinstance(x, list)]
    body = [x for x in n if isinstance(x, list)]
    out = '(' + ' '.join(atom(x) for x in head)
    for b in body:
        out += '\n' + '\t' * (ind + 1) + dump(b, ind + 1)
    return out + '\n' + '\t' * ind + ')'


def netmap(fn):
    t = parse(open(fn, encoding='utf-8').read())
    out, comps = {}, {}
    for c in find(find(t, 'components')[0], 'comp'):
        comps[val(c, 'ref')] = (val(c, 'value'), val(c, 'footprint'))
    for n in find(find(t, 'nets')[0], 'net'):
        for nd in find(n, 'node'):
            out[(val(nd, 'ref'), val(nd, 'pin'))] = (val(n, 'name'), val(nd, 'pinfunction'))
    return out, comps
