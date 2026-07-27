#!/usr/bin/env python3
"""
Polygon-based Folding Carton Nesting Engine
--------------------------------------------
Nests the ACTUAL die-cut outline (not just the bounding rectangle) so that
flaps drop into the notches of the flipped neighbouring row — the real
tête-bêche ganging used on folding-carton dies.

Method (heightmap / skyline nesting):
  * Each 1-up is a polygon in millimetres.
  * We reduce it to a 1-D edge profile: for every column x across the piece,
    the topmost (top[x]) and bottommost (bot[x]) material.
  * A row is that profile repeated at the column pitch (piece width + gap).
  * Rows are stacked one at a time, alternating orientation. Each new row is
    dropped until its top profile just touches the running "floor" left by the
    row above (per column). Because the floor is castellated, protruding flaps
    slide into the recesses — that is where the material saving comes from.
  * We try every base orientation, flip on/off, and horizontal phase shift
    between rows, and keep whichever fits the most pieces.

Dependencies: numpy, shapely  (pip install shapely numpy --break-system-packages)
"""

import json
import sys
import os
import math
from datetime import datetime

import numpy as np
from shapely.geometry import Polygon
from shapely import affinity

RES = 1.0            # sampling resolution in mm for the edge profile
SHIFT_STEPS = 40     # how many horizontal phase shifts to try per config


# ── Polygon construction ─────────────────────────────────────────────

def build_autobottom(spec):
    """
    Generate an auto-lock / crash-lock bottom (fondo automático) blank from box
    dimensions, applying the pegado (glue seam), traslape and holgura.

    spec = {
      "style": "auto_bottom",
      "L": 80,          # length panel (the wide face)
      "W": 45,          # width / depth panel (the narrow face)
      "H": 105,         # body height
      "glue_tab": None, # pegado width; default = clamp(0.5*W, 8, 18)
      "traslape": 3,    # glue overlap on the major bottom flaps (2-4 mm)
      "holgura": 1,     # clearance between flaps (0.5-1 mm)
      "top": "tuck",    # "tuck" lid (default) or "auto" (mirror the bottom)
      "glue_side": "left"
    }

    Panel order across the width: [pegado][L][W][L][W]. Auto-bottom flaps sit on
    the BOTTOM edge: the two L panels carry the *major* flaps (0.5*L, the one
    that glues gets +traslape), the two W panels carry the *minor* flaps
    (0.5*W - holgura). The alternating major/minor castellation is exactly what
    interlocks when neighbouring rows are flipped tête-bêche.
    """
    L = spec['L']; W = spec['W']; H = spec['H']
    traslape = spec.get('traslape', 3)
    holgura = spec.get('holgura', 1)
    glue = spec.get('glue_tab')
    if glue is None:
        glue = max(8.0, min(18.0, 0.5 * W))

    # Flap formulas
    flap_mayor = 0.5 * L
    flap_menor = 0.5 * W - holgura

    # Panel x-boundaries: pegado, then L, W, L, W
    xs = [0, glue, glue + L, glue + L + W, glue + 2 * L + W, glue + 2 * L + 2 * W]
    width = xs[-1]
    p_glue = (xs[0], xs[1])
    p_L1 = (xs[1], xs[2]); p_W1 = (xs[2], xs[3])
    p_L2 = (xs[3], xs[4]); p_W2 = (xs[4], xs[5])

    # Body zone. Top tuck-lid depth fills whatever remains after body + major flap.
    body_top = 44.0 if spec.get('top', 'tuck') == 'tuck' else flap_mayor
    body_bottom = body_top + H

    top_flaps = []
    if spec.get('top', 'tuck') == 'tuck':
        # Tuck lid on one length panel + shallow dust flaps on the width panels
        top_flaps = [[p_L1[0], p_L1[1], body_top],
                     [p_W1[0], p_W1[1], min(0.5 * W, body_top)],
                     [p_W2[0], p_W2[1], min(0.5 * W, body_top)]]
    else:  # mirror the auto bottom on top
        top_flaps = [[p_L1[0], p_L1[1], flap_mayor], [p_L2[0], p_L2[1], flap_mayor],
                     [p_W1[0], p_W1[1], flap_menor], [p_W2[0], p_W2[1], flap_menor]]

    bottom_flaps = [
        [p_L1[0], p_L1[1], flap_mayor + traslape],   # major flap with glue overlap
        [p_L2[0], p_L2[1], flap_mayor],              # major flap
        [p_W1[0], p_W1[1], flap_menor],              # minor flap
        [p_W2[0], p_W2[1], flap_menor],              # minor flap
    ]
    return build_carton_polygon({
        'width': width, 'body_top': body_top, 'body_bottom': body_bottom,
        'top_flaps': top_flaps, 'bottom_flaps': bottom_flaps,
    })


def build_carton_polygon(spec):
    """
    Build a folding-carton blank polygon from a parametric spec, OR pass an
    explicit list of points.

    Auto-bottom shortcut:
      spec = {"style": "auto_bottom", "L":80, "W":45, "H":105, ...}

    Explicit:
      spec = {"points": [[x,y], ...]}   # closed outline in mm

    Parametric (body rectangle + rectangular flaps on top / bottom edges):
      spec = {
        "width": 261.4,           # total blank width
        "body_top": 42,           # y where the full-width body starts
        "body_bottom": 147,       # y where the full-width body ends
        "top_flaps":    [[x0,x1,depth], ...],  # protrude ABOVE body_top
        "bottom_flaps": [[x0,x1,depth], ...],  # protrude BELOW body_bottom
      }
    The union of body + flaps is returned as a shapely Polygon.
    """
    if spec.get('style') == 'auto_bottom':
        return build_autobottom(spec)

    if 'points' in spec:
        return Polygon(spec['points'])

    w = spec['width']
    bt = spec['body_top']
    bb = spec['body_bottom']
    body = Polygon([(0, bt), (w, bt), (w, bb), (0, bb)])
    poly = body
    for x0, x1, d in spec.get('top_flaps', []):
        poly = poly.union(Polygon([(x0, bt - d), (x1, bt - d), (x1, bt), (x0, bt)]))
    for x0, x1, d in spec.get('bottom_flaps', []):
        poly = poly.union(Polygon([(x0, bb), (x1, bb), (x1, bb + d), (x0, bb + d)]))
    # Normalise so min corner is at origin
    minx, miny, maxx, maxy = poly.bounds
    poly = affinity.translate(poly, -minx, -miny)
    return poly


def oriented(poly, rot):
    """Rotate a polygon by rot degrees about origin and re-normalise to origin."""
    p = affinity.rotate(poly, rot, origin=(0, 0))
    minx, miny, _, _ = p.bounds
    return affinity.translate(p, -minx, -miny)


# ── Edge profile ─────────────────────────────────────────────────────

def edge_profile(poly):
    """
    Return (width, height, top[], bot[]) where top/bot are arrays sampled every
    RES mm across the piece width. top[i] = topmost material y at that column,
    bot[i] = bottommost. Columns with no material are NaN.
    """
    minx, miny, maxx, maxy = poly.bounds
    w = maxx - minx
    h = maxy - miny
    n = max(1, int(math.ceil(w / RES)))
    top = np.full(n, np.nan)
    bot = np.full(n, np.nan)
    for i in range(n):
        x = min(i * RES + RES / 2.0, w - 1e-6)
        # vertical scan line through the polygon
        line = Polygon([(x - 1e-4, -1), (x + 1e-4, -1),
                        (x + 1e-4, h + 1), (x - 1e-4, h + 1)])
        inter = poly.intersection(line)
        if inter.is_empty:
            continue
        iy0, iy1 = inter.bounds[1], inter.bounds[3]
        top[i] = iy0
        bot[i] = iy1
    return w, h, top, bot


# ── Nesting ──────────────────────────────────────────────────────────

def nest_config(prof_a, prof_b, usable_w, usable_h, gap, shift):
    """
    Stack rows alternating profile A (normal) and profile B (flipped), with a
    horizontal phase `shift` applied to every other row. Returns list of placed
    piece dicts and the total occupied height.
    """
    wa, ha, top_a, bot_a = prof_a
    wb, hb, top_b, bot_b = prof_b
    col_w = int(math.ceil(usable_w / RES))
    floor = np.zeros(col_w)          # running bottom boundary per column (mm)
    placed = []
    # The floor carries a +gap so the next row keeps its distance, but the sheet
    # only has to reach the lowest piece of material — not that phantom gap.
    # Track the true bottom edge separately or the sheet reads one gap too tall.
    max_bottom = 0.0
    row = 0
    guard = 0
    while guard < 1000:
        guard += 1
        use_a = (row % 2 == 0)
        pw, ph, ptop, pbot = (wa, ha, top_a, bot_a) if use_a else (wb, hb, top_b, bot_b)
        phase = 0 if use_a else shift
        dx = pw + gap
        # piece origins across the row
        xs = []
        x = phase
        while x + pw <= usable_w + 1e-6:
            xs.append(x)
            x += dx
        if not xs:
            # allow a row that starts at 0 if phase pushed everything off
            if phase > 0:
                shift = 0
                continue
            break

        # Build this row's top/bottom profile over the whole usable width
        row_top = np.full(col_w, np.nan)
        row_bot = np.full(col_w, np.nan)
        pn = len(ptop)
        for ox in xs:
            base = int(round(ox / RES))
            for i in range(pn):
                c = base + i
                if 0 <= c < col_w and not np.isnan(ptop[i]):
                    row_top[c] = ptop[i]
                    row_bot[c] = pbot[i]

        mat = ~np.isnan(row_top)
        if not mat.any():
            break
        # Drop the row until its top touches the floor somewhere
        drop = np.nanmax(floor[mat] - row_top[mat])
        placed_top = drop
        row_bottom_edge = np.nanmax(placed_top + row_bot[mat])
        if row_bottom_edge > usable_h + 1e-6:
            break
        # Commit: record pieces, update floor
        for ox in xs:
            placed.append({'x': ox, 'y': placed_top, 'rot_profile': 'A' if use_a else 'B'})
        max_bottom = max(max_bottom, float(row_bottom_edge))
        new_floor = placed_top + row_bot + gap
        floor[mat] = new_floor[mat]
        row += 1

    return placed, (max_bottom if placed else 0.0)


def pack(poly, sheet_w, sheet_h, gap, margins, allow_flip=True, allow_rotate=True,
         overflow_tol=25.0):
    """
    Explore every arrangement and return them all, not just the winner.

    Gap and margins are fixed house standards, so the operator's real choice is
    *which arrangement to run*. And an arrangement that misses the reel by a few
    millimetres is worth seeing, because it can often be rescued by shaving a
    margin or nudging the die — so near-misses are packed against a slightly
    enlarged sheet and returned flagged with exactly how much they exceed,
    rather than being silently dropped.
    """
    m_top, m_bot, m_left, m_right = margins
    usable_w = sheet_w - m_left - m_right
    usable_h = sheet_h - m_top - m_bot
    explore_w = usable_w + overflow_tol
    explore_h = usable_h + overflow_tol

    base_rots = [0, 90] if allow_rotate else [0]
    buckets = {}          # (base_rot, flip, fits) -> best candidate
    for base in base_rots:
        A = oriented(poly, base)
        prof_a = edge_profile(A)
        if prof_a[0] > explore_w or prof_a[1] > explore_h:
            continue
        flips = [False, True] if allow_flip else [False]
        for flip in flips:
            B = oriented(poly, (base + 180) % 360) if flip else A
            prof_b = edge_profile(B) if flip else prof_a
            dx = prof_a[0] + gap
            # Always scan a horizontal phase shift between successive rows. Even
            # with same-orientation rows, a brick-lay shift lets one row's bottom
            # flaps drop into the top notches of the row below — that is a real
            # nesting win, not only the flipped (tête-bêche) case.
            shifts = [k * dx / SHIFT_STEPS for k in range(SHIFT_STEPS)]
            for sh in shifts:
                placed, occ_h = nest_config(prof_a, prof_b, explore_w, explore_h, gap, sh)
                count = len(placed)
                if count == 0:
                    continue
                # actual occupied width
                occ_w = max((p['x'] + (prof_a[0] if p['rot_profile'] == 'A' else prof_b[0]))
                            for p in placed)
                over_w = max(0.0, occ_w - usable_w)
                over_h = max(0.0, occ_h - usable_h)
                fits = (over_w <= 1e-6 and over_h <= 1e-6)
                cand = {
                    'count': count, 'placed': placed, 'occ_w': occ_w, 'occ_h': occ_h,
                    'base_rot': base, 'flip': flip, 'shift': round(sh, 1),
                    'prof_a': prof_a, 'prof_b': prof_b,
                    'A': A, 'B': B,
                    'fits': fits,
                    'over_w': round(over_w, 1), 'over_h': round(over_h, 1),
                }
                # The press width is trimmable to any size up to the max, while
                # the height comes in fixed formats. So among layouts with the
                # same piece count, the one that needs the narrowest sheet is
                # cheaper — it buys less board.
                key = (count, -occ_w, -occ_h, -(over_w + over_h))
                cur = buckets.get((base, flip, fits))
                if cur is None or key > (cur['count'], -cur['occ_w'], -cur['occ_h'],
                                         -(cur['over_w'] + cur['over_h'])):
                    buckets[(base, flip, fits)] = cand

    options = list(buckets.values())
    if not options:
        return None, [], usable_w, usable_h
    # A near-miss is only interesting if it beats every layout that already fits
    best_fit_count = max((c['count'] for c in options if c['fits']), default=0)
    options = [c for c in options if c['fits'] or c['count'] > best_fit_count]
    options.sort(key=lambda c: (not c['fits'], -c['count'], c['occ_w'], c['occ_h']))

    fitting = [c for c in options if c['fits']]
    best = fitting[0] if fitting else options[0]
    return best, options, usable_w, usable_h


# ── SVG / HTML report ────────────────────────────────────────────────

def poly_to_path(poly, scale, ox, oy):
    xs, ys = poly.exterior.xy
    pts = ' '.join(f'{(ox + x) * scale:.2f},{(oy + y) * scale:.2f}'
                   for x, y in zip(xs, ys))
    return f'<polygon points="{pts}" '


def build_svg(best, cfg, usable_w, usable_h):
    sw, sh = cfg['sheet_w'], cfg['sheet_h']
    m_top = cfg['margin_top']
    m_left = cfg['margin_left']
    # A near-miss layout spills past the reel. Widen the canvas so the operator
    # sees exactly what hangs over the edge instead of it being clipped away.
    span_w = max(sw, m_left + best['occ_w'] + cfg['margin_right'])
    span_h = max(sh, m_top + best['occ_h'] + cfg['margin_bottom'])
    scale = min(880 / span_w, 560 / span_h)
    svg_w, svg_h = sw * scale, sh * scale

    parts = [f'<rect x="0" y="0" width="{svg_w:.1f}" height="{svg_h:.1f}" '
             f'fill="#FFFFFF" stroke="#1E293B" stroke-width="1.5"/>']
    if m_top > 0:
        gh = m_top * scale
        parts.append(f'<rect x="0" y="0" width="{svg_w:.1f}" height="{gh:.1f}" '
                     f'fill="#FEE2E2"/>')
        parts.append(f'<text x="{svg_w/2:.1f}" y="{gh/2+4:.1f}" text-anchor="middle" '
                     f'font-size="10" fill="#B91C1C" font-family="sans-serif">Top margin {m_top}mm</text>')

    A, B = best['A'], best['B']
    for p in best['placed']:
        piece = A if p['rot_profile'] == 'A' else B
        col = '#2563EB' if p['rot_profile'] == 'A' else '#DC2626'
        ox = m_left + p['x']
        oy = m_top + p['y']
        parts.append(poly_to_path(piece, scale, ox, oy) +
                     f'fill="{col}" fill-opacity="0.18" stroke="{col}" stroke-width="0.9"/>')

    # ── Cut lines: where the real sheet is trimmed out of the max/reel ──
    # Seeing the trim in place makes the numbers concrete: everything beyond the
    # dashed line is board you never buy.
    m_right = cfg.get('margin_right', 10)
    m_bot = cfg.get('margin_bottom', 10)
    final_w = m_left + best['occ_w'] + m_right
    final_h = m_top + best['occ_h'] + m_bot
    cx = final_w * scale
    cy = final_h * scale

    if final_w < sw - 0.5:
        # grey out the strip that gets trimmed off the width
        parts.append(f'<rect x="{cx:.1f}" y="0" width="{svg_w-cx:.1f}" height="{svg_h:.1f}" '
                     f'fill="#94A3B8" fill-opacity="0.13"/>')
        parts.append(f'<line x1="{cx:.1f}" y1="0" x2="{cx:.1f}" y2="{svg_h:.1f}" '
                     f'stroke="#0F766E" stroke-width="1.6" stroke-dasharray="9,5"/>')
        parts.append(f'<text x="{cx-5:.1f}" y="14" text-anchor="end" font-size="10" '
                     f'font-weight="600" fill="#0F766E" font-family="sans-serif">'
                     f'corte {final_w:.1f} mm</text>')
        parts.append(f'<text x="{(cx+svg_w)/2:.1f}" y="{svg_h/2:.1f}" text-anchor="middle" '
                     f'font-size="10" fill="#64748B" font-family="sans-serif">'
                     f'sobra {sw-final_w:.1f} mm</text>')

    if final_h < sh - 0.5:
        parts.append(f'<rect x="0" y="{cy:.1f}" width="{cx if final_w < sw-0.5 else svg_w:.1f}" '
                     f'height="{svg_h-cy:.1f}" fill="#94A3B8" fill-opacity="0.13"/>')
        parts.append(f'<line x1="0" y1="{cy:.1f}" x2="{svg_w:.1f}" y2="{cy:.1f}" '
                     f'stroke="#0F766E" stroke-width="1.6" stroke-dasharray="9,5"/>')
        parts.append(f'<text x="6" y="{cy-5:.1f}" font-size="10" font-weight="600" '
                     f'fill="#0F766E" font-family="sans-serif">corte {final_h:.1f} mm '
                     f'(bobina {sh} — sobran {sh-final_h:.1f})</text>')

    # ── Overflow: show what spills past the reel and by how much ──
    if not best.get('fits', True):
        ow, oh = best.get('over_w', 0), best.get('over_h', 0)
        if ow > 0:
            parts.append(f'<rect x="{svg_w:.1f}" y="0" width="{ow*scale:.1f}" '
                         f'height="{max(svg_h, cy):.1f}" fill="#DC2626" fill-opacity="0.12"/>')
            parts.append(f'<line x1="{svg_w:.1f}" y1="0" x2="{svg_w:.1f}" '
                         f'y2="{max(svg_h, cy):.1f}" stroke="#DC2626" stroke-width="2"/>')
            parts.append(f'<text x="{svg_w + 4:.1f}" y="{svg_h/2:.1f}" font-size="11" '
                         f'font-weight="700" fill="#DC2626" font-family="sans-serif">'
                         f'se pasa {ow} mm de ancho</text>')
        if oh > 0:
            parts.append(f'<rect x="0" y="{svg_h:.1f}" width="{max(svg_w, cx):.1f}" '
                         f'height="{oh*scale:.1f}" fill="#DC2626" fill-opacity="0.12"/>')
            parts.append(f'<line x1="0" y1="{svg_h:.1f}" x2="{max(svg_w, cx):.1f}" '
                         f'y2="{svg_h:.1f}" stroke="#DC2626" stroke-width="2"/>')
            parts.append(f'<text x="6" y="{svg_h + oh*scale/2 + 4:.1f}" font-size="11" '
                         f'font-weight="700" fill="#DC2626" font-family="sans-serif">'
                         f'se pasa {oh} mm del alto de bobina ({sh} mm)</text>')

    out_w = max(svg_w, cx) + (best.get('over_w', 0) * scale if not best.get('fits', True) else 0) + 40
    out_h = max(svg_h, cy) + (best.get('over_h', 0) * scale if not best.get('fits', True) else 0) + 30
    inner = '\n  '.join(parts)
    return (f'<svg width="{out_w:.0f}" height="{out_h:.0f}" '
            f'viewBox="0 0 {out_w:.0f} {out_h:.0f}" xmlns="http://www.w3.org/2000/svg">'
            f'{inner}</svg>')


def metrics(cand, cfg):
    """All the numbers a layout is judged by, for one candidate arrangement."""
    sw, sh = cfg['sheet_w'], cfg['sheet_h']
    final_w = round(cfg['margin_left'] + cand['occ_w'] + cfg['margin_right'], 1)
    final_h = round(cfg['margin_top'] + cand['occ_h'] + cfg['margin_bottom'], 1)
    piece_area = cfg['_piece_area']
    real_area = final_w * final_h          # the trimmed sheet
    bobina_area = final_w * sh             # reel consumed: same length, full reel width
    # Three different questions, three different numbers — keep them separate:
    #  aprov_bobina : how much of the reel the SHEET uses. The loss here is the
    #                 trim strip trown away when the layout is shorter than the
    #                 reel width. This is the costing figure.
    #  util         : how much of that sheet the BOXES fill (the nesting result).
    #  util_total   : boxes measured straight against the reel consumed.
    aprov_bobina = round(real_area / bobina_area * 100, 1)
    util = round(cand['count'] * piece_area / real_area * 100, 1)
    util_total = round(cand['count'] * piece_area / bobina_area * 100, 1)
    return {
        'count': cand['count'],
        'final_w': final_w, 'final_h': final_h,
        'sobra_h': round(sh - final_h, 1),
        'trim_w': round(sw - final_w, 1),
        'aprov_bobina': aprov_bobina,
        'merma_bobina': round(100 - aprov_bobina, 1),
        'util': util, 'waste': round(100 - util, 1),
        'util_total': util_total, 'waste_total': round(100 - util_total, 1),
        'util_max': round(cand['count'] * piece_area / (sw * sh) * 100, 1),
        'reaches_w': final_w >= sw - 0.5,
        'pcs_per_m2': round(cand['count'] / (bobina_area / 1e6), 1),
    }


def orientation_note(cand, mt, cfg):
    sw, sh = cfg['sheet_w'], cfg['sheet_h']
    w_txt = (f"Ocupa todo el ancho de máquina ({sw} mm)." if mt['reaches_w'] else
             f"Ancho recortable: alimentas <b>{mt['final_w']} mm</b> de los {sw} mm "
             f"máximos ({mt['trim_w']} mm menos).")
    if mt['sobra_h'] > 0.5:
        h_txt = (f"El pliego llega a <b>{mt['final_h']} mm</b> de alto dentro de la "
                 f"bobina de {sh} mm — sobra una tira de <b>{mt['sobra_h']} mm</b>.")
    else:
        h_txt = f"El pliego ocupa la bobina completa de {sh} mm de alto."
    return (
        f"{w_txt} {h_txt}<br>"
        f"<b>Pliego real: {mt['final_w']} × {mt['final_h']} mm</b><br>"
        f"<b>Aprovechamiento de bobina: {mt['aprov_bobina']}%</b> "
        f"(merma {mt['merma_bobina']}%) — pliego {mt['final_w']}×{mt['final_h']} "
        f"÷ bobina consumida {mt['final_w']}×{sh}.<br>"
        f"Las cajas llenan {mt['util']}% del pliego; contra la bobina completa, "
        f"{mt['util_total']}%.")


def job_slug(cfg):
    """Filename stem for downloads: 1UP + project, e.g. AF1UP1260_Te-7-Azahares."""
    import unicodedata
    import re as _re
    parts = []
    if cfg.get('oneup'):
        parts.append(str(cfg['oneup']))
    if cfg.get('project'):
        p = unicodedata.normalize('NFKD', str(cfg['project']))
        p = p.encode('ascii', 'ignore').decode()
        p = _re.sub(r'[^A-Za-z0-9]+', '-', p).strip('-')
        parts.append(p)
    return '_'.join(parts) if parts else 'nesting'


def build_panel(cand, cfg, usable_w, usable_h, is_best):
    """One switchable view: stats + note + layout for a single orientation."""
    sw, sh = cfg['sheet_w'], cfg['sheet_h']
    mt = metrics(cand, cfg)
    m = (cfg['margin_top'], cfg['margin_bottom'], cfg['margin_left'], cfg['margin_right'])
    svg = build_svg(cand, cfg, usable_w, usable_h)
    desc = ('caja vertical (0°)' if cand['base_rot'] == 0 else 'caja horizontal (90°)')
    desc += (', tête-bêche filas volteadas' if cand['flip'] else ', filas misma orientación')
    if cand['flip'] and cand['shift']:
        desc += f", desfase {cand['shift']} mm"
    badge = (' <span style="background:#DCFCE7;color:#15803D;padding:2px 7px;'
             'border-radius:99px;font-size:11px;font-weight:600">recomendada</span>'
             if is_best else '')

    # A near-miss needs its own banner: the percentages below are computed on a
    # sheet that does not exist yet, so they must not be read as achievable
    # until the overflow is resolved.
    warn = ''
    if not cand.get('fits', True):
        bits = []
        if cand['over_w']:
            bits.append(f"<b>{cand['over_w']} mm de ancho</b> (máx {sw} mm)")
        if cand['over_h']:
            bits.append(f"<b>{cand['over_h']} mm de alto</b> (bobina {sh} mm)")
        warn = (
            '<div class="card" style="border-left:4px solid #DC2626;background:#FEF2F2">'
            f'<div style="font-size:13px;color:#7F1D1D">'
            f'<b>Esta opción NO cabe todavía.</b> Da {mt["count"]} piezas pero se pasa por '
            f'{" y ".join(bits)}. Los porcentajes de abajo suponen ese pliego más grande, '
            f'así que no son válidos hasta que resuelvas el excedente — '
            f'recortando margen, separación, o ajustando el trazado.</div></div>')

    return warn + f'''
<div class="stats">
<div class="s g"><div class="v">{mt['count']}</div><div class="l">Piezas / pliego</div></div>
<div class="s"><div class="v" style="font-size:17px">{mt['final_w']} × {mt['final_h']}</div><div class="l">Pliego real (mm)</div></div>
<div class="s g"><div class="v">{mt['aprov_bobina']}%</div><div class="l">Aprov. de bobina</div></div>
<div class="s"><div class="v" style="color:#DC2626">{mt['merma_bobina']}%</div><div class="l">Merma de bobina</div></div>
<div class="s"><div class="v">{mt['util']}%</div><div class="l">Cajas s/ pliego</div></div>
</div>
<div class="card" style="border-left:4px solid #16A34A"><div style="font-size:13px">{orientation_note(cand, mt, cfg)}</div></div>
<div class="card"><h2>Acomodo — contorno real del suaje &nbsp;<span style="font-weight:400;color:#64748B">({desc})</span>{badge}</h2>
<div class="wrap">{svg}</div>
<div class="dims">Bobina: {sw} × {sh} mm &nbsp;|&nbsp; Separación: {cfg['gap']} mm &nbsp;|&nbsp; Márgenes S/I/Izq/Der: {m[0]}/{m[1]}/{m[2]}/{m[3]} mm &nbsp;|&nbsp; <span style="color:#2563EB">■</span> normal &nbsp; <span style="color:#DC2626">■</span> volteada 180° &nbsp;|&nbsp; <span style="color:#0F766E">- - -</span> línea de corte del pliego real ({mt['final_w']} × {mt['final_h']} mm); el área gris no se compra</div>
<button class="btn" data-fn="{job_slug(cfg)}_{cfg.get('_reel_name','')}-{'vertical' if cand['base_rot']==0 else 'horizontal'}{'-TB' if cand['flip'] else ''}" onclick="dl(this)">Descargar SVG</button></div>'''


def generate_html(best, options, cfg, usable_w, usable_h):
    sw, sh = cfg['sheet_w'], cfg['sheet_h']
    title = cfg.get('title', 'Polygon Nesting Report')
    final_w = round(cfg['margin_left'] + best['occ_w'] + cfg['margin_right'], 1)
    final_h = round(cfg['margin_top'] + best['occ_h'] + cfg['margin_bottom'], 1)
    reaches_w = final_w >= sw - 0.5
    reaches_h = final_h >= sh - 0.5
    # The press can be fed any width up to the maximum, so the sheet actually
    # consumed is final_w wide by the format height. Utilisation is measured
    # against that real sheet, not against the 1040 mm maximum — otherwise a
    # narrow job would look wasteful when in fact no board is bought.
    # Two figures matter and they are not the same:
    #  * the sheet the layout truly occupies (final_w × final_h) — the geometric
    #    result, and the size to cut to;
    #  * the board you actually pay for: the trimmed width by the *reel* height,
    #    because the reel comes in fixed formats (610, 710 …) even if the layout
    #    stops short of it.
    piece_area = cfg['_piece_area']
    real_area = final_w * final_h
    bobina_area = final_w * sh
    util = round(best['count'] * piece_area / real_area * 100, 1)
    waste = round(100 - util, 1)
    util_bobina = round(best['count'] * piece_area / bobina_area * 100, 1)
    waste_bobina = round(100 - util_bobina, 1)
    util_max = round(best['count'] * piece_area / (sw * sh) * 100, 1)
    sobra_h = round(sh - final_h, 1)
    layout = f"Base {best['base_rot']}°" + (", tête-bêche flipped rows" if best['flip'] else ", same-orientation rows")
    if best['flip']:
        layout += f", row shift {best['shift']}mm"

    trim_w = round(sw - final_w, 1)
    w_txt = (f"Ocupa todo el ancho de máquina ({sw} mm)." if reaches_w else
             f"Ancho recortable: alimentas <b>{final_w} mm</b> de los {sw} mm "
             f"máximos ({trim_w} mm menos).")
    if sobra_h > 0.5:
        h_txt = (f"El pliego llega a <b>{final_h} mm</b> de alto dentro de la "
                 f"bobina de {sh} mm — sobran <b>{sobra_h} mm</b>.")
    else:
        h_txt = f"El pliego ocupa la bobina completa de {sh} mm de alto."
    note = (f"{w_txt} {h_txt}<br><b>Pliego real: {final_w} × {final_h} mm</b> "
            f"(aprov. {util}%). &nbsp;Sobre la bobina {final_w} × {sh} mm: "
            f"aprov. {util_bobina}%, desperdicio {waste_bobina}%.")

    date = datetime.now().strftime('%Y-%m-%d %H:%M')

    # One switchable panel per arrangement — every option the engine found, not
    # just the winner. Gap and margins are fixed house standards, so the choice
    # the operator actually has is which arrangement to run; near-misses are
    # included (flagged) because a few millimetres are often fixable at the die.
    panels, buttons = [], []
    for i, cand in enumerate(options):
        mt = metrics(cand, cfg)
        base = 'Vertical' if cand['base_rot'] == 0 else 'Horizontal'
        if cand['flip']:
            base += ' TB'
        is_best = cand is best
        if cand['fits']:
            sub = f"{mt['count']} pzas · {mt['aprov_bobina']}%"
            cls = ''
        else:
            over = []
            if cand['over_w']:
                over.append(f"+{cand['over_w']}mm ancho")
            if cand['over_h']:
                over.append(f"+{cand['over_h']}mm alto")
            sub = f"{mt['count']} pzas · se pasa {', '.join(over)}"
            cls = ' over'
        active = ' active' if is_best else ''
        show = '' if is_best else 'display:none'
        buttons.append(
            f'<button class="tab{cls}{active}" data-i="{i}" onclick="sw({i})">'
            f'{base} <span class="n">{sub}</span></button>')
        panels.append(f'<div class="pane" id="pane{i}" style="{show}">'
                      f'{build_panel(cand, cfg, usable_w, usable_h, is_best)}</div>')
    tabs_html = ''.join(buttons)
    panes_html = ''.join(panels)

    return f'''<!DOCTYPE html><html><head><meta charset="utf-8">
<title>{title}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#F1F5F9;color:#1E293B;padding:24px}}
.c{{max-width:960px;margin:0 auto}}
.h{{background:#fff;border-radius:12px;padding:18px 22px;margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,.08);display:flex;justify-content:space-between;align-items:center}}
.h h1{{font-size:19px}} .h .d{{font-size:12px;color:#94A3B8}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:12px}}
.s{{background:#fff;border-radius:10px;padding:14px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.s .v{{font-size:24px;font-weight:800;color:#2563EB}} .s.g .v{{color:#16A34A}}
.s .l{{font-size:11px;color:#64748B;margin-top:2px;text-transform:uppercase;letter-spacing:.4px}}
.card{{background:#fff;border-radius:12px;padding:18px 22px;margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.card h2{{font-size:14px;margin-bottom:12px}}
.wrap{{display:flex;justify-content:center;background:#F8FAFC;border:1px solid #E2E8F0;border-radius:8px;padding:14px;overflow:auto}}
.dims{{font-size:12px;color:#94A3B8;margin-top:10px}}
.btn{{display:inline-flex;gap:6px;padding:8px 16px;background:#2563EB;color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:13px;margin-top:10px}}
.tabs{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px;background:#fff;padding:8px;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.tab{{flex:1 1 150px;padding:10px 12px;border:1.5px solid #E2E8F0;background:#fff;border-radius:8px;cursor:pointer;font-size:13px;font-weight:600;color:#475569;transition:.15s;text-align:left}}
.tab:hover{{border-color:#93C5FD;background:#F8FAFC}}
.tab.active{{background:#2563EB;border-color:#2563EB;color:#fff}}
.tab .n{{display:block;font-size:10.5px;font-weight:500;opacity:.85;margin-top:3px;line-height:1.3}}
.tab.over{{border-color:#FCA5A5;background:#FEF2F2;color:#B91C1C}}
.tab.over:hover{{border-color:#F87171}}
.tab.over.active{{background:#DC2626;border-color:#DC2626;color:#fff}}
</style></head><body><div class="c">
<div class="h"><div><h1>{title}</h1><div style="font-size:13px;color:#64748B">Orientación de la caja — elige cuál usar</div></div><div class="d">{date}</div></div>
<div class="tabs">{tabs_html}</div>
{panes_html}
</div>
<script>
function sw(i){{
  document.querySelectorAll('.pane').forEach(function(p){{p.style.display='none';}});
  document.getElementById('pane'+i).style.display='';
  document.querySelectorAll('.tab').forEach(function(t){{
    t.classList.toggle('active', t.dataset.i==String(i));
  }});
}}
function dl(btn){{
  var s=btn.closest('.card').querySelector('svg');
  var b=new Blob([new XMLSerializer().serializeToString(s)],{{type:'image/svg+xml'}});
  var a=document.createElement('a');a.href=URL.createObjectURL(b);
  a.download=(btn.dataset.fn||'nesting-layout')+'.svg';a.click();URL.revokeObjectURL(a.href);
}}
</script></body></html>'''


def dedupe_options(options):
    """
    Drop arrangements that are indistinguishable in practice. A tête-bêche
    variant that yields the same count on the same sheet as the plain one is
    noise on the screen — the operator cannot act on the difference.
    Also drop anything that placed no pieces.
    """
    out, seen = [], set()
    for c in options:
        if c['count'] <= 0:
            continue
        key = (c['base_rot'], c['count'], round(c['occ_w'], 1), round(c['occ_h'], 1),
               c['fits'])
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def generate_multi_html(results, cfg_base):
    """
    One report covering every reel. Two levels of tabs: pick the reel, then the
    arrangement inside it. Seeing all four reels side by side is what makes the
    choice obvious — the same layout can be excellent on one reel and wasteful
    on another purely because of the leftover trim strip.

    `results` = list of dicts: {name, sheet_w, sheet_h, best, options, uw, uh}
    """
    date = datetime.now().strftime('%Y-%m-%d %H:%M')
    project = cfg_base.get('project')
    title = project or cfg_base.get('title', 'Nesting — todas las bobinas')
    sub_bits = []
    if cfg_base.get('oneup'):
        sub_bits.append(f"<b>1UP {cfg_base['oneup']}</b>")
    if cfg_base.get('client'):
        sub_bits.append(f"Cliente: {cfg_base['client']}")
    sub_bits.append('Elige bobina, luego orientación')
    subtitle = ' · '.join(sub_bits)

    reel_buttons, reel_blocks, summary_rows = [], [], []
    default_reel = 0
    best_ppm = -1
    for ri, res in enumerate(results):
        if res['best'] is None:
            continue
        cfg = dict(cfg_base)
        cfg['sheet_w'], cfg['sheet_h'] = res['sheet_w'], res['sheet_h']
        mb = metrics(res['best'], cfg)
        if mb['pcs_per_m2'] > best_ppm:
            best_ppm = mb['pcs_per_m2']
            default_reel = ri

    for ri, res in enumerate(results):
        if res['best'] is None:
            continue
        cfg = dict(cfg_base)
        cfg['sheet_w'], cfg['sheet_h'] = res['sheet_w'], res['sheet_h']
        cfg['_reel_name'] = res['name'].replace(' ', '-')
        opts = res['options']
        best = res['best']
        mb = metrics(best, cfg)

        # reel selector button
        active = ' active' if ri == default_reel else ''
        reel_buttons.append(
            f'<button class="rtab{active}" data-r="{ri}" onclick="swR({ri})">'
            f'{res["name"]} <span class="n">{res["sheet_h"]} mm · '
            f'{mb["count"]} pzas · {mb["aprov_bobina"]}%</span></button>')

        # summary row
        summary_rows.append(
            f'<tr{" class=best" if ri == default_reel else ""}>'
            f'<td>{res["name"]}</td><td>{res["sheet_w"]} × {res["sheet_h"]}</td>'
            f'<td>{mb["count"]}</td>'
            f'<td>{"vertical" if best["base_rot"] == 0 else "horizontal"}</td>'
            f'<td>{mb["final_w"]} × {mb["final_h"]}</td>'
            f'<td>{mb["aprov_bobina"]}%</td><td>{mb["merma_bobina"]}%</td>'
            f'<td>{mb["pcs_per_m2"]}</td></tr>')

        # orientation tabs inside this reel
        obtns, opanes = [], []
        for oi, cand in enumerate(opts):
            m = metrics(cand, cfg)
            nm = 'Vertical' if cand['base_rot'] == 0 else 'Horizontal'
            if cand['flip']:
                nm += ' TB'
            if cand['fits']:
                sub = f"{m['count']} pzas · {m['aprov_bobina']}%"
                cls = ''
            else:
                bits = []
                if cand['over_w']:
                    bits.append(f"+{cand['over_w']}mm ancho")
                if cand['over_h']:
                    bits.append(f"+{cand['over_h']}mm alto")
                sub = f"{m['count']} pzas · se pasa {', '.join(bits)}"
                cls = ' over'
            is_b = cand is best
            act = ' active' if is_b else ''
            shw = '' if is_b else 'display:none'
            obtns.append(
                f'<button class="tab{cls}{act}" data-o="{ri}_{oi}" '
                f'onclick="swO({ri},{oi})">{nm}<span class="n">{sub}</span></button>')
            opanes.append(
                f'<div class="pane" id="p{ri}_{oi}" style="{shw}">'
                f'{build_panel(cand, cfg, res["uw"], res["uh"], is_b)}</div>')

        blk_show = '' if ri == default_reel else 'display:none'
        reel_blocks.append(
            f'<div class="reel" id="reel{ri}" style="{blk_show}">'
            f'<div class="tabs">{"".join(obtns)}</div>{"".join(opanes)}</div>')

    return f'''<!DOCTYPE html><html><head><meta charset="utf-8">
<title>{title}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#F1F5F9;color:#1E293B;padding:24px}}
.c{{max-width:980px;margin:0 auto}}
.h{{background:#fff;border-radius:12px;padding:18px 22px;margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,.08);display:flex;justify-content:space-between;align-items:center}}
.h h1{{font-size:19px}} .h .d{{font-size:12px;color:#94A3B8}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:12px}}
.s{{background:#fff;border-radius:10px;padding:14px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.s .v{{font-size:24px;font-weight:800;color:#2563EB}} .s.g .v{{color:#16A34A}}
.s .l{{font-size:11px;color:#64748B;margin-top:2px;text-transform:uppercase;letter-spacing:.4px}}
.card{{background:#fff;border-radius:12px;padding:18px 22px;margin-bottom:12px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.card h2{{font-size:14px;margin-bottom:12px}}
.wrap{{display:flex;justify-content:center;background:#F8FAFC;border:1px solid #E2E8F0;border-radius:8px;padding:14px;overflow:auto}}
.dims{{font-size:12px;color:#94A3B8;margin-top:10px}}
.btn{{display:inline-flex;gap:6px;padding:8px 16px;background:#2563EB;color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:13px;margin-top:10px}}
.rtabs{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;background:#0F172A;padding:8px;border-radius:10px}}
.rtab{{flex:1 1 160px;padding:11px 12px;border:none;background:#1E293B;color:#CBD5E1;border-radius:8px;cursor:pointer;font-size:13.5px;font-weight:700;text-align:left}}
.rtab:hover{{background:#334155}}
.rtab.active{{background:#F8FAFC;color:#0F172A}}
.rtab .n{{display:block;font-size:10.5px;font-weight:500;opacity:.85;margin-top:3px}}
.tabs{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px;background:#fff;padding:8px;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.tab{{flex:1 1 150px;padding:10px 12px;border:1.5px solid #E2E8F0;background:#fff;border-radius:8px;cursor:pointer;font-size:13px;font-weight:600;color:#475569;text-align:left}}
.tab:hover{{border-color:#93C5FD;background:#F8FAFC}}
.tab.active{{background:#2563EB;border-color:#2563EB;color:#fff}}
.tab .n{{display:block;font-size:10.5px;font-weight:500;opacity:.85;margin-top:3px;line-height:1.3}}
.tab.over{{border-color:#FCA5A5;background:#FEF2F2;color:#B91C1C}}
.tab.over.active{{background:#DC2626;border-color:#DC2626;color:#fff}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;padding:8px 10px;font-size:10.5px;text-transform:uppercase;letter-spacing:.4px;color:#64748B;border-bottom:2px solid #E2E8F0}}
td{{padding:8px 10px;border-bottom:1px solid #F1F5F9}}
tr.best td{{background:#F0FDF4;font-weight:600}}
</style></head><body><div class="c">
<div class="h"><div><h1>{title}</h1><div style="font-size:13px;color:#64748B">{subtitle}</div></div><div class="d">{date}</div></div>

<div class="card"><h2>Resumen — mejor opción que cabe en cada bobina</h2>
<table><thead><tr><th>Bobina</th><th>Máximo</th><th>Pzas</th><th>Orient.</th><th>Pliego real</th><th>Aprov. bobina</th><th>Merma</th><th>pz/m²</th></tr></thead>
<tbody>{''.join(summary_rows)}</tbody></table></div>

<div class="rtabs">{''.join(reel_buttons)}</div>
{''.join(reel_blocks)}
</div>
<script>
function swR(r){{
  document.querySelectorAll('.reel').forEach(function(x){{x.style.display='none';}});
  document.getElementById('reel'+r).style.display='';
  document.querySelectorAll('.rtab').forEach(function(t){{
    t.classList.toggle('active', t.dataset.r==String(r));
  }});
}}
function swO(r,o){{
  document.querySelectorAll('#reel'+r+' .pane').forEach(function(p){{p.style.display='none';}});
  document.getElementById('p'+r+'_'+o).style.display='';
  document.querySelectorAll('#reel'+r+' .tab').forEach(function(t){{
    t.classList.toggle('active', t.dataset.o==(r+'_'+o));
  }});
}}
function dl(btn){{
  var s=btn.closest('.card').querySelector('svg');
  var b=new Blob([new XMLSerializer().serializeToString(s)],{{type:'image/svg+xml'}});
  var a=document.createElement('a');a.href=URL.createObjectURL(b);
  a.download=(btn.dataset.fn||'nesting-layout')+'.svg';a.click();URL.revokeObjectURL(a.href);
}}
</script></body></html>'''


# ── CLI ──────────────────────────────────────────────────────────────

DEFAULTS = {'gap': 10, 'margin_top': 20, 'margin_bottom': 10,
            'margin_left': 10, 'margin_right': 10,
            'allow_flip': True, 'allow_rotate': True,
            'title': 'Polygon Nesting Report'}

# The plant's reels. Width is the press maximum (trimmable); height is fixed.
STANDARD_REELS = [
    {'name': 'Grande', 'w': 1040, 'h': 710},
    {'name': 'Mediano', 'w': 1040, 'h': 610},
    {'name': 'Chico', 'w': 1040, 'h': 450},
    {'name': 'Extra chico', 'w': 1040, 'h': 355},
]


def main_multi(cfg):
    """Run every reel and emit a single report with reel + orientation tabs."""
    poly = build_carton_polygon(cfg['blank'])
    cfg['_piece_area'] = poly.area
    margins = (cfg['margin_top'], cfg['margin_bottom'],
               cfg['margin_left'], cfg['margin_right'])

    reels = cfg.get('sheets')
    if reels is True or reels == 'standard':
        reels = STANDARD_REELS

    results, summary = [], []
    for r in reels:
        sw, sh = r['w'], r['h']
        best, options, uw, uh = pack(poly, sw, sh, cfg['gap'], margins,
                                     cfg['allow_flip'], cfg['allow_rotate'],
                                     cfg.get('overflow_tol', 25.0))
        options = dedupe_options(options)
        if best is not None and best['count'] == 0:
            best = None
        results.append({'name': r['name'], 'sheet_w': sw, 'sheet_h': sh,
                        'best': best, 'options': options, 'uw': uw, 'uh': uh})
        if best is None:
            continue
        c2 = dict(cfg); c2['sheet_w'], c2['sheet_h'] = sw, sh
        mb = metrics(best, c2)
        summary.append({
            'reel': r['name'], 'reel_h': sh, 'pieces': mb['count'],
            'orientation': 'vertical' if best['base_rot'] == 0 else 'horizontal',
            'real_sheet_w': mb['final_w'], 'real_sheet_h': mb['final_h'],
            'aprov_bobina_pct': mb['aprov_bobina'],
            'merma_bobina_pct': mb['merma_bobina'],
            'pcs_per_m2': mb['pcs_per_m2'],
            'options': [
                {'orientation': 'vertical' if c['base_rot'] == 0 else 'horizontal',
                 'tete_beche': c['flip'], 'pieces': m['count'], 'fits': c['fits'],
                 'over_w_mm': c['over_w'], 'over_h_mm': c['over_h'],
                 'real_sheet_w': m['final_w'], 'real_sheet_h': m['final_h'],
                 'aprov_bobina_pct': m['aprov_bobina'],
                 'pcs_per_m2': m['pcs_per_m2']}
                for c, m in ((c, metrics(c, c2)) for c in options)
            ],
        })

    out = cfg.get('output_path', 'nesting_multi.html')
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, 'w') as f:
        f.write(generate_multi_html(results, cfg))

    best_overall = max(summary, key=lambda s: s['pcs_per_m2']) if summary else None
    print(json.dumps({
        'piece_area_mm2': round(poly.area, 1),
        'recommended_reel': best_overall['reel'] if best_overall else None,
        'reels': summary,
        'output_path': out,
    }, indent=2))


def main():
    cfg = json.loads(sys.argv[1])
    for k, v in DEFAULTS.items():
        cfg.setdefault(k, v)

    if cfg.get('sheets'):
        main_multi(cfg)
        return

    poly = build_carton_polygon(cfg['blank'])
    cfg['_piece_area'] = poly.area
    margins = (cfg['margin_top'], cfg['margin_bottom'], cfg['margin_left'], cfg['margin_right'])
    best, options, uw, uh = pack(poly, cfg['sheet_w'], cfg['sheet_h'], cfg['gap'],
                                 margins, cfg['allow_flip'], cfg['allow_rotate'],
                                 cfg.get('overflow_tol', 25.0))
    if best is None:
        print(json.dumps({'error': 'piece does not fit on this sheet'}))
        return
    options = dedupe_options(options)

    out = cfg.get('output_path', 'poly_nesting_report.html')
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, 'w') as f:
        f.write(generate_html(best, options, cfg, uw, uh))

    final_w = round(cfg['margin_left'] + best['occ_w'] + cfg['margin_right'], 1)
    final_h = round(cfg['margin_top'] + best['occ_h'] + cfg['margin_bottom'], 1)
    print(json.dumps({
        'total_pieces': best['count'],
        'base_rotation': best['base_rot'],
        'tete_beche': best['flip'],
        'row_shift_mm': best['shift'],
        'piece_area_mm2': round(poly.area, 1),
        'max_sheet': [cfg['sheet_w'], cfg['sheet_h']],
        'final_sheet_w': final_w,
        'final_sheet_h': final_h,
        'reaches_max_width': final_w >= cfg['sheet_w'] - 0.5,
        'reaches_max_height': final_h >= cfg['sheet_h'] - 0.5,
        # Real sheet = trimmed width × format height (width is free up to max)
        # Sheet the layout truly occupies (what to cut to)
        'real_sheet_w': final_w,
        'real_sheet_h': final_h,
        # Reel it is cut from: fixed height format, trimmable width
        'bobina_h': cfg['sheet_h'],
        'sobra_h_mm': round(cfg['sheet_h'] - final_h, 1),
        'trim_w_mm': round(cfg['sheet_w'] - final_w, 1),
        # Costing figure: how much of the reel consumed the sheet actually uses.
        'aprov_bobina_pct': round(final_h / cfg['sheet_h'] * 100, 1),
        'merma_bobina_pct': round(100 - final_h / cfg['sheet_h'] * 100, 1),
        # Nesting figure: how much of the sheet the boxes fill.
        'utilization_pct': round(best['count'] * poly.area / (final_w * final_h) * 100, 1),
        'waste_pct': round(100 - best['count'] * poly.area / (final_w * final_h) * 100, 1),
        'utilization_total_pct': round(best['count'] * poly.area / (final_w * cfg['sheet_h']) * 100, 1),
        'utilization_vs_max_pct': round(best['count'] * poly.area / (cfg['sheet_w'] * cfg['sheet_h']) * 100, 1),
        'options': [
            {
                'orientation': 'vertical' if c['base_rot'] == 0 else 'horizontal',
                'tete_beche': c['flip'],
                'pieces': m['count'],
                'fits': c['fits'],
                'over_w_mm': c['over_w'], 'over_h_mm': c['over_h'],
                'real_sheet_w': m['final_w'], 'real_sheet_h': m['final_h'],
                'sobra_h_mm': m['sobra_h'],
                'aprov_bobina_pct': m['aprov_bobina'],
                'merma_bobina_pct': m['merma_bobina'],
                'utilization_pct': m['util'],
                'pcs_per_m2': m['pcs_per_m2'],
                'recommended': c is best,
            }
            for c, m in ((c, metrics(c, cfg)) for c in options)
        ],
        'output_path': out,
    }, indent=2))


if __name__ == '__main__':
    main()
