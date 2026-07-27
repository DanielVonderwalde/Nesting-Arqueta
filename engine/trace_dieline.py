#!/usr/bin/env python3
"""
Die-line tracer
---------------
Extracts the real die-cut outline from a die-line drawing (PNG/JPG, or the
raster image embedded in a PDF) and emits it as a millimetre polygon that
poly_nesting.py can nest directly.

Why this exists: folding cartons are not rectangles, and the flap/notch
castellation is exactly what lets pieces interlock. Tracing the true outline
beats typing approximate flap dimensions.

How it works:
  1. Find the die-line stroke (dark, low-saturation pixels).
  2. Flood-fill the background from the image border. Anything the fill cannot
     reach is inside the blank, so the blank = NOT background. This is robust to
     colour labels and dimension annotations drawn inside the shape.
  3. Keep the largest connected region (drops stray annotation marks outside).
  4. Trace its boundary, simplify, and scale so the bounding box matches the
     real width/height in mm.

Usage:
  python3 trace_dieline.py '<JSON>'
  JSON = {
    "image": "/path/to/dieline.png",     # or "pdf": "/path/file.pdf"
    "real_width_mm": 396,                 # known blank width
    "real_height_mm": 280,                # optional; used to verify aspect
    "simplify_mm": 0.8,                   # outline simplification tolerance
    "out_json": "/path/outline.json"      # where to write the points
  }
Prints a summary plus the point count; writes {"points": [[x,y],...]} in mm.
"""

import json
import sys
import os

import numpy as np
from PIL import Image
from scipy import ndimage
from shapely.geometry import Polygon


def load_image(cfg):
    """Load a raster die-line, either a normal image file or the biggest image
    embedded in a PDF (Illustrator/CAD exports often wrap a raster preview)."""
    if 'image' in cfg:
        return Image.open(cfg['image']).convert('RGB')

    # Pull the largest uncompressed DeviceRGB image out of the PDF
    import re
    data = open(cfg['pdf'], 'rb').read()
    best = None
    for m in re.finditer(rb'/Subtype\s*/Image', data):
        head = data[max(0, m.start() - 800):m.start() + 800]
        w = re.search(rb'/Width\s+(\d+)', head)
        h = re.search(rb'/Height\s+(\d+)', head)
        cs = re.search(rb'/ColorSpace\s*/(\w+)', head)
        if not (w and h and cs) or cs.group(1) != b'DeviceRGB':
            continue
        W, H = int(w.group(1)), int(h.group(1))
        st = data.find(b'stream', m.start() + 800 - 800)
        st = data.find(b'stream', m.start())
        if st < 0:
            continue
        start = st + 7
        need = W * H * 3
        if len(data) - start < need:
            continue
        if best is None or need > best[0]:
            best = (need, W, H, start)
    if best is None:
        raise SystemExit('No uncompressed RGB image found in PDF')
    need, W, H, start = best
    return Image.frombytes('RGB', (W, H), data[start:start + need])


def trace(cfg):
    img = load_image(cfg)
    a = np.asarray(img).astype(np.int16)
    W, H = img.size

    # 1. Find the CUT line. CAD exports use two conventions:
    #    - black/grey strokes (Arqueta "dieline desde trazado" style)
    #    - red cut / green crease (classic ArtiosCAD colour coding)
    #    Picking the wrong one traces the creases or the dimension arrows, so
    #    detect both and use whichever actually forms the outline.
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    R, G, B = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    dark = (mx < cfg.get('dark_threshold', 110)) & ((mx - mn) < 60)
    red = (R > G + 40) & (R > B + 40) & (R > 80)

    mode = cfg.get('stroke', 'auto')
    if mode == 'red':
        stroke = red
    elif mode == 'dark':
        stroke = dark
    else:  # auto — red cut lines win when clearly present
        stroke = red if red.sum() > max(500, 0.3 * dark.sum()) else dark
    # close small gaps in the stroke so the flood fill can't leak through
    stroke = ndimage.binary_dilation(stroke, iterations=cfg.get('close_iters', 2))

    # 2. Flood fill background inward from the border
    free = ~stroke
    lbl, n = ndimage.label(free)
    border = set(lbl[0, :]) | set(lbl[-1, :]) | set(lbl[:, 0]) | set(lbl[:, -1])
    border.discard(0)
    background = np.isin(lbl, list(border))

    inside = ~background          # blank material (stroke + interior)

    # 3. Largest connected component only
    lbl2, n2 = ndimage.label(inside)
    if n2 == 0:
        raise SystemExit('Could not isolate the blank outline')
    sizes = ndimage.sum(inside, lbl2, range(1, n2 + 1))
    keep = int(np.argmax(sizes)) + 1
    blank = lbl2 == keep
    # undo the dilation so the outline sits on the true cut line
    blank = ndimage.binary_erosion(blank, iterations=cfg.get('close_iters', 2))
    blank = ndimage.binary_fill_holes(blank)

    # 4. Boundary -> polygon. Use marching-squares style contour via find_objects
    poly = mask_to_polygon(blank)

    # Scale to real millimetres using the known width
    minx, miny, maxx, maxy = poly.bounds
    px_w = maxx - minx
    px_h = maxy - miny
    real_w = cfg['real_width_mm']
    scale = real_w / px_w
    from shapely import affinity
    poly = affinity.translate(poly, -minx, -miny)
    poly = affinity.scale(poly, xfact=scale, yfact=scale, origin=(0, 0))

    tol = cfg.get('simplify_mm', 0.8)
    poly = poly.simplify(tol, preserve_topology=True)

    implied_h = px_h * scale
    return poly, implied_h, (px_w, px_h)


def mask_to_polygon(mask):
    """Convert a boolean mask to a shapely Polygon by tracing its boundary."""
    try:
        from skimage import measure
        contours = measure.find_contours(mask.astype(float), 0.5)
        contours.sort(key=len, reverse=True)
        pts = [(c[1], c[0]) for c in contours[0]]
        return Polygon(pts).buffer(0)
    except ImportError:
        pass
    # Fallback: build the polygon from the union of per-row material spans.
    # Coarser but dependency-free and good enough at typical image resolution.
    from shapely.ops import unary_union
    from shapely.geometry import box
    boxes = []
    step = 1
    for y in range(0, mask.shape[0], step):
        row = mask[y]
        if not row.any():
            continue
        idx = np.flatnonzero(row)
        splits = np.where(np.diff(idx) > 1)[0]
        starts = np.r_[idx[0], idx[splits + 1]]
        ends = np.r_[idx[splits], idx[-1]]
        for s, e in zip(starts, ends):
            boxes.append(box(s, y, e + 1, y + step))
    return unary_union(boxes).buffer(0)


def main():
    cfg = json.loads(sys.argv[1])
    poly, implied_h, px = trace(cfg)
    if poly.geom_type != 'Polygon':
        poly = max(poly.geoms, key=lambda g: g.area)

    minx, miny, maxx, maxy = poly.bounds
    pts = [[round(x, 2), round(y, 2)] for x, y in poly.exterior.coords]

    out = {
        'points': pts,
        'width_mm': round(maxx - minx, 1),
        'height_mm': round(maxy - miny, 1),
        'area_mm2': round(poly.area, 1),
        'bbox_area_mm2': round((maxx - minx) * (maxy - miny), 1),
        'shape_efficiency_pct': round(poly.area / ((maxx - minx) * (maxy - miny)) * 100, 1),
        'n_points': len(pts),
        'source_pixels': [int(px[0]), int(px[1])],
    }
    if cfg.get('real_height_mm'):
        out['expected_height_mm'] = cfg['real_height_mm']
        out['height_match_delta_mm'] = round((maxy - miny) - cfg['real_height_mm'], 1)

    if cfg.get('out_json'):
        os.makedirs(os.path.dirname(os.path.abspath(cfg['out_json'])), exist_ok=True)
        with open(cfg['out_json'], 'w') as f:
            json.dump({'points': pts}, f)
        out['out_json'] = cfg['out_json']

    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
