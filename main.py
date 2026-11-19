#!/usr/bin/env python3
"""THE WORLD — Thursday, 19 November 2026.

Builds the front-page image for make.fun news-image-20261119.

Pipeline: assets/hero.png (an AI-generated photo of the Shenzhen summit
hall, with no text in it) is cover-cropped into the lead tile, and
assets/text.png (the typeset layer rendered from assets/text.svg) is
alpha-composited on top of a paper canvas. The output is one 1024x1024
PNG at /output/entry.png.

Stdlib only, no network, no clock, no randomness: the same inputs always
produce byte-identical output.
"""

import os
import struct
import sys
import zlib

PAGE_W = 1024
PAGE_H = 1024
PAPER = (247, 244, 237)
HERO_RECT = (24, 120, 576, 252)  # x, y, width, height of the lead photo
HERO_FILE = "hero.png"
TEXT_FILE = "text.png"


def _paeth(a, b, c):
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def read_png(path):
    """Decode a non-interlaced 8-bit PNG (grey, RGB or RGBA) to RGBA rows."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(path + " is not a PNG file")
    pos = 8
    width = height = None
    bit_depth = None
    colour = None
    interlace = None
    idat = bytearray()
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width, height, bit_depth, colour, _c, _f, interlace = struct.unpack(
                ">IIBBBBB", chunk)
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
        pos += 12 + length
    if width is None or bit_depth != 8 or interlace != 0:
        raise ValueError(path + ": need an 8-bit non-interlaced PNG")
    if colour == 0:
        channels = 1
    elif colour == 2:
        channels = 3
    elif colour == 6:
        channels = 4
    else:
        raise ValueError(path + ": unsupported colour type %d" % colour)
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    rows = []
    prev = bytearray(stride)
    off = 0
    for _ in range(height):
        ftype = raw[off]
        line = bytearray(raw[off + 1:off + 1 + stride])
        off += 1 + stride
        if ftype == 0:
            pass
        elif ftype == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                line[i] = (line[i] + _paeth(a, b, c)) & 0xFF
        else:
            raise ValueError(path + ": bad filter type %d" % ftype)
        prev = line
        row = []
        if channels == 1:
            for i in range(width):
                g = line[i]
                row.append((g, g, g, 255))
        elif channels == 3:
            for i in range(width):
                j = i * 3
                row.append((line[j], line[j + 1], line[j + 2], 255))
        else:
            for i in range(width):
                j = i * 4
                row.append((line[j], line[j + 1], line[j + 2], line[j + 3]))
        rows.append(row)
    return width, height, rows


def _filter_row(kind, row, prev):
    """Filter one RGB row; returns (filtered bytes, signed-byte cost)."""
    out = bytearray()
    cost = 0
    n = len(row)
    for i in range(n):
        if kind == 0:
            v = row[i]
        elif kind == 1:
            left = row[i - 3] if i >= 3 else 0
            v = (row[i] - left) & 0xFF
        elif kind == 2:
            v = (row[i] - prev[i]) & 0xFF
        else:  # Paeth
            a = row[i - 3] if i >= 3 else 0
            b = prev[i]
            c = prev[i - 3] if i >= 3 else 0
            v = (row[i] - _paeth(a, b, c)) & 0xFF
        out.append(v)
        cost += v if v < 128 else 256 - v
    return bytes(out), cost


def write_png(path, width, height, rows):
    """Encode RGB rows to a deterministic PNG (best filter per row)."""
    raw = bytearray()
    prev = bytes(width * 3)
    for row in rows:
        flat = bytearray()
        for (r, g, b) in row:
            flat += bytes((r, g, b))
        flat = bytes(flat)
        best_kind, best_data, best_cost = 0, None, None
        for kind in (0, 1, 2, 4):
            cand, cost = _filter_row(kind, flat, prev)
            if best_cost is None or cost < best_cost:
                best_kind, best_data, best_cost = kind, cand, cost
        raw.append(best_kind)
        raw += best_data
        prev = flat
    comp = zlib.compress(bytes(raw), 9)

    def chunk(ctype, payload):
        out = struct.pack(">I", len(payload)) + ctype + payload
        out += struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF)
        return out

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    with open(path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", ihdr))
        fh.write(chunk(b"IDAT", comp))
        fh.write(chunk(b"IEND", b""))


def _axis_weights(s0, s1, n):
    """(index, weight) pairs covering source interval [s0, s1)."""
    i0 = int(s0)
    i1 = int(s1)
    if i1 <= i0:
        i1 = i0 + 1
    out = []
    for i in range(i0, i1 + 1):
        lo = max(s0, float(i))
        hi = min(s1, float(i + 1))
        wgt = hi - lo
        if wgt <= 0:
            continue
        out.append((min(max(i, 0), n - 1), wgt))
    tot = sum(w for _, w in out)
    return [(i, w / tot) for i, w in out]


def scale_cover(pixels, sw, sh, dw, dh):
    """Scale an RGBA image to cover dw x dh, centre-cropped.

    Each output pixel averages every source pixel its footprint covers
    (area averaging), so a large downscale stays free of the aliasing
    that point-sampled bilinear leaves in fine detail.
    """
    scale = max(dw / sw, dh / sh)
    off_x = (sw * scale - dw) / 2.0
    off_y = (sh * scale - dh) / 2.0
    xw = [_axis_weights((x + off_x) / scale, (x + 1 + off_x) / scale, sw)
          for x in range(dw)]
    yw = [_axis_weights((y + off_y) / scale, (y + 1 + off_y) / scale, sh)
          for y in range(dh)]
    out = []
    for y in range(dh):
        wy = yw[y]
        row = []
        for x in range(dw):
            wx = xw[x]
            r = g = b = 0.0
            for y0, wyy in wy:
                prow = pixels[y0]
                for x0, wxx in wx:
                    wgt = wxx * wyy
                    px = prow[x0]
                    r += px[0] * wgt
                    g += px[1] * wgt
                    b += px[2] * wgt
            row.append((int(r + 0.5), int(g + 0.5), int(b + 0.5)))
        out.append(row)
    return out


def build(assets_dir):
    hw, hh, hero = read_png(os.path.join(assets_dir, HERO_FILE))
    tw, th, text = read_png(os.path.join(assets_dir, TEXT_FILE))
    if (tw, th) != (PAGE_W, PAGE_H):
        raise ValueError("text layer must be 1024x1024, got %dx%d" % (tw, th))
    x, y, w, h = HERO_RECT
    photo = scale_cover(hero, hw, hh, w, h)
    canvas = [[PAPER for _ in range(PAGE_W)] for _ in range(PAGE_H)]
    for dy in range(h):
        canvas[y + dy][x:x + w] = photo[dy]
    for py in range(PAGE_H):
        trow = text[py]
        crow = canvas[py]
        for px in range(PAGE_W):
            tr, tg, tb, ta = trow[px]
            if ta == 0:
                continue
            if ta == 255:
                crow[px] = (tr, tg, tb)
            else:
                br, bg, bb = crow[px]
                crow[px] = ((tr * ta + br * (255 - ta)) // 255,
                            (tg * ta + bg * (255 - ta)) // 255,
                            (tb * ta + bb * (255 - ta)) // 255)
    return canvas


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    assets = os.path.join(here, "assets")
    out_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    out_path = os.path.join(out_dir, "entry.png")
    os.makedirs(out_dir, exist_ok=True)
    canvas = build(assets)
    write_png(out_path, PAGE_W, PAGE_H, canvas)
    size = os.path.getsize(out_path)
    print("wrote %s (%d bytes)" % (out_path, size))
    if size > 1024 * 1024:
        print("WARNING: entry.png exceeds the 1 MiB limit", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
