"""QR encoding, for the two-factor enrolment code.

Lifted out of xxops-server.py unchanged. Pure arithmetic: text in, SVG out. It
has no idea what xxOps is, which is the point - it was 14% of a file that also
did authentication, persistence, HTML and dispatch.

Only qr_svg() is used from outside. Everything prefixed _qr_ is internal.
"""

_qr_SPEC = {
    1:  (26,  10, [(1, 16)]),
    2:  (44,  16, [(1, 28)]),
    3:  (70,  26, [(1, 44)]),
    4:  (100, 18, [(2, 32)]),
    5:  (134, 24, [(2, 43)]),
    6:  (172, 16, [(4, 27)]),
    7:  (196, 18, [(4, 31)]),
    8:  (242, 22, [(2, 38), (2, 39)]),
    9:  (292, 22, [(3, 36), (2, 37)]),
    10: (346, 26, [(4, 43), (1, 44)]),
}

_qr_ALIGN = {
    1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
    6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46],
    10: [6, 28, 50],
}

# --- GF(256) ---------------------------------------------------------------
_qr_EXP = [0] * 512
_qr_LOG = [0] * 256
_x = 1
for _i in range(255):
    _qr_EXP[_i] = _x
    _qr_LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _qr_EXP[_i] = _qr_EXP[_i - 255]


def _qr_mul(a, b):
    if a == 0 or b == 0:
        return 0
    return _qr_EXP[_qr_LOG[a] + _qr_LOG[b]]


def _qr_rs_generator(n):
    g = [1]
    for i in range(n):
        ng = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            ng[j] ^= _qr_mul(c, 1)
            ng[j + 1] ^= _qr_mul(c, _qr_EXP[i])
        g = ng
    return g


def _qr_rs_encode(data, n):
    gen = _qr_rs_generator(n)
    rem = [0] * n
    for d in data:
        factor = d ^ rem[0]
        rem = rem[1:] + [0]
        for i, g in enumerate(gen[1:]):
            rem[i] ^= _qr_mul(g, factor)
    return rem


# --- BCH for format and version info ---------------------------------------
def _qr_bch(data, poly, bits):
    d = data << bits
    g = poly
    gbits = poly.bit_length() - 1
    for i in range(data.bit_length() + bits - 1, gbits - 1, -1):
        if d >> i & 1:
            d ^= g << (i - gbits)
    return (data << bits) | d


def _qr_format_bits(mask):
    # EC level M == 0b00
    v = _qr_bch((0b00 << 3) | mask, 0x537, 10)
    return v ^ 0b101010000010010


def _qr_version_bits(version):
    return _qr_bch(version, 0x1F25, 12)


# --- encoding --------------------------------------------------------------
def _qr_pick_version(nbytes):
    for v in range(1, 11):
        total, ecpb, groups = _qr_SPEC[v]
        cap = sum(nb * dc for nb, dc in groups)
        # 4 bit mode + 8 bit length (versions 1-9) or 16 bit (10+)
        header = 4 + (8 if v < 10 else 16)
        if nbytes * 8 + header <= cap * 8:
            return v
    raise ValueError("too long for version 10 at EC level M")


def _qr_bitstream(data, version):
    total, ecpb, groups = _qr_SPEC[version]
    cap = sum(nb * dc for nb, dc in groups)
    bits = []

    def put(val, n):
        for i in range(n - 1, -1, -1):
            bits.append(val >> i & 1)

    put(0b0100, 4)
    put(len(data), 8 if version < 10 else 16)
    for b in data:
        put(b, 8)

    put(0, min(4, cap * 8 - len(bits)))
    while len(bits) % 8:
        bits.append(0)

    cws = [int("".join(str(b) for b in bits[i:i + 8]), 2)
           for i in range(0, len(bits), 8)]
    pad = [0xEC, 0x11]
    i = 0
    while len(cws) < cap:
        cws.append(pad[i % 2])
        i += 1
    return cws


def _qr_interleave(cws, version):
    total, ecpb, groups = _qr_SPEC[version]
    blocks, ecblocks = [], []
    pos = 0
    for nb, dc in groups:
        for _ in range(nb):
            chunk = cws[pos:pos + dc]
            pos += dc
            blocks.append(chunk)
            ecblocks.append(_qr_rs_encode(chunk, ecpb))

    out = []
    for i in range(max(len(b) for b in blocks)):
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(ecpb):
        for b in ecblocks:
            out.append(b[i])
    return out


# --- matrix ----------------------------------------------------------------
def _qr_new_matrix(version):
    size = version * 4 + 17
    m = [[None] * size for _ in range(size)]

    def finder(r, c):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                rr, cc = r + dr, c + dc
                if not (0 <= rr < size and 0 <= cc < size):
                    continue
                inring = (dr in (0, 6) and 0 <= dc <= 6) or \
                         (dc in (0, 6) and 0 <= dr <= 6)
                incore = 2 <= dr <= 4 and 2 <= dc <= 4
                m[rr][cc] = 1 if (inring or incore) else 0

    finder(0, 0)
    finder(0, size - 7)
    finder(size - 7, 0)

    for i in range(8, size - 8):
        bit = 1 if i % 2 == 0 else 0
        m[6][i] = bit
        m[i][6] = bit

    centers = _qr_ALIGN[version]
    for r in centers:
        for c in centers:
            if (r < 8 and c < 8) or (r < 8 and c > size - 9) or \
               (r > size - 9 and c < 8):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    m[r + dr][c + dc] = 1 if max(abs(dr), abs(dc)) != 1 else 0

    m[size - 8][8] = 1  # dark module

    # reserve format areas
    for i in range(9):
        if m[8][i] is None:
            m[8][i] = 0
        if m[i][8] is None:
            m[i][8] = 0
    for i in range(8):
        if m[8][size - 1 - i] is None:
            m[8][size - 1 - i] = 0
        if m[size - 1 - i][8] is None:
            m[size - 1 - i][8] = 0

    if version >= 7:
        for i in range(6):
            for j in range(3):
                m[size - 11 + j][i] = 0
                m[i][size - 11 + j] = 0
    return m, size


def _qr_reserved(version, size):
    """Mask of cells that hold function patterns, built before data goes in."""
    m, _ = _qr_new_matrix(version)
    return [[m[r][c] is not None for c in range(size)] for r in range(size)]


def _qr_place(matrix, reserved, size, data):
    bits = []
    for cw in data:
        for i in range(7, -1, -1):
            bits.append(cw >> i & 1)
    idx = 0
    up = True
    col = size - 1
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if up else range(size)
        for r in rows:
            for c in (col, col - 1):
                if reserved[r][c]:
                    continue
                matrix[r][c] = bits[idx] if idx < len(bits) else 0
                idx += 1
        up = not up
        col -= 2


def _qr_mask_fn(k):
    return [
        lambda r, c: (r + c) % 2 == 0,
        lambda r, c: r % 2 == 0,
        lambda r, c: c % 3 == 0,
        lambda r, c: (r + c) % 3 == 0,
        lambda r, c: (r // 2 + c // 3) % 2 == 0,
        lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
        lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
        lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
    ][k]


def _qr_penalty(m, size):
    score = 0
    for line in list(m) + [list(col) for col in zip(*m)]:
        run, prev = 0, None
        for v in line:
            if v == prev:
                run += 1
            else:
                if run >= 5:
                    score += run - 2
                run, prev = 1, v
        if run >= 5:
            score += run - 2
        s = "".join(str(v) for v in line)
        score += 40 * (s.count("10111010000") + s.count("00001011101"))

    for r in range(size - 1):
        for c in range(size - 1):
            b = m[r][c] + m[r][c + 1] + m[r + 1][c] + m[r + 1][c + 1]
            if b in (0, 4):
                score += 3

    dark = sum(sum(row) for row in m)
    pct = dark * 100.0 / (size * size)
    low = int(pct // 5) * 5
    high = low + 5
    score += 10 * min(abs(low - 50) // 5, abs(high - 50) // 5)
    return score


def _qr_apply_format(m, size, version, mask):
    fb = _qr_format_bits(mask)
    bits = [fb >> i & 1 for i in range(14, -1, -1)]
    coords1 = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
               (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
    for bit, (r, c) in zip(bits, coords1):
        m[r][c] = bit
    coords2 = [(size - 1 - i, 8) for i in range(7)] + \
              [(8, size - 8 + i) for i in range(8)]
    for bit, (r, c) in zip(bits, coords2):
        m[r][c] = bit

    if version >= 7:
        vb = _qr_version_bits(version)
        vbits = [vb >> i & 1 for i in range(17, -1, -1)]
        k = 0
        for i in range(6):
            for j in range(3):
                m[size - 11 + j][i] = vbits[17 - k]
                m[i][size - 11 + j] = vbits[17 - k]
                k += 1


def _qr_encode(text):
    data = text.encode("utf-8")
    version = _qr_pick_version(len(data))
    cws = _qr_interleave(_qr_bitstream(data, version), version)
    size = version * 4 + 17
    reserved = _qr_reserved(version, size)

    best, best_score = None, None
    for mask in range(8):
        m, _ = _qr_new_matrix(version)
        _qr_place(m, reserved, size, cws)
        for r in range(size):
            for c in range(size):
                if not reserved[r][c] and _qr_mask_fn(mask)(r, c):
                    m[r][c] ^= 1
        _qr_apply_format(m, size, version, mask)
        sc = _qr_penalty(m, size)
        if best_score is None or sc < best_score:
            best, best_score = m, sc
    return best


def qr_svg(text, quiet=4):
    """Scalable QR as a single-path SVG, sized in module units."""
    m = _qr_encode(text)
    n = len(m)
    dim = n + quiet * 2
    d = []
    for r in range(n):
        c = 0
        while c < n:
            if m[r][c]:
                start = c
                while c < n and m[r][c]:
                    c += 1
                d.append("M%d %dh%dv1h-%dz" % (start + quiet, r + quiet,
                                               c - start, c - start))
            else:
                c += 1
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
            'shape-rendering="crispEdges" width="100%%" height="100%%">'
            '<rect width="%d" height="%d" fill="#fff"/>'
            '<path d="%s" fill="#000"/></svg>'
            % (dim, dim, dim, dim, "".join(d)))

# --- totp, RFC 6238 --------------------------------------------------------
