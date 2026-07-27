#!/usr/bin/env python3
"""
Folding Carton Nesting Engine
Calculates optimal die-cut blank arrangements on press sheets
and generates an interactive HTML report with SVG visualization.
"""

import json
import math
import sys
import os
from datetime import datetime

DEFAULTS = {
    'gap_x': 10, 'gap_y': 10,          # separation between boxes
    'margin_top': 20, 'margin_bottom': 10,
    'margin_left': 10, 'margin_right': 10,
    'nest_offset_x': 0, 'nest_offset_y': 0,
    'title': 'Nesting Layout Report',
    'client': '', 'project': '',
}

STANDARD_SHEETS = [
    {'name': 'Grande', 'w': 1040, 'h': 710},
    {'name': 'Mediano', 'w': 1040, 'h': 610},
    {'name': 'Chico', 'w': 1040, 'h': 450},
    {'name': 'Extra chico', 'w': 1040, 'h': 355},
]


# ── Arrangement calculators ─────────────────────────────────────────

def calc_grid(blank_w, blank_h, usable_w, usable_h, gap_x, gap_y, base_rot=0):
    """Standard grid: all pieces in the same orientation."""
    w, h = (blank_w, blank_h) if base_rot in (0, 180) else (blank_h, blank_w)
    if w <= 0 or h <= 0 or w > usable_w or h > usable_h:
        return {'total': 0, 'positions': [], 'piece_w': w, 'piece_h': h}

    cols = int((usable_w + gap_x) / (w + gap_x))
    rows = int((usable_h + gap_y) / (h + gap_y))
    positions = []
    for r in range(rows):
        for c in range(cols):
            positions.append({
                'x': c * (w + gap_x),
                'y': r * (h + gap_y),
                'w': w, 'h': h,
                'rotation': base_rot,
            })
    return {'total': cols * rows, 'cols': cols, 'rows': rows,
            'positions': positions, 'piece_w': w, 'piece_h': h}


def calc_tete_beche_v(blank_w, blank_h, usable_w, usable_h,
                      gap_x, gap_y, nest_offset_y, base_rot=0):
    """Tête-bêche with alternating rows (vertical nesting)."""
    w, h = (blank_w, blank_h) if base_rot in (0, 180) else (blank_h, blank_w)
    if w <= 0 or h <= 0 or w > usable_w or h > usable_h:
        return {'total': 0, 'positions': [], 'piece_w': w, 'piece_h': h}

    cols = int((usable_w + gap_x) / (w + gap_x))
    if cols <= 0:
        return {'total': 0, 'positions': [], 'piece_w': w, 'piece_h': h}
    positions = []

    # Place rows one at a time, alternating orientation. Every adjacent pair
    # has opposite orientation, so each row after the first nests into the one
    # above it — the die-cut flaps interlock, letting the bounding boxes overlap
    # by nest_offset_y. Effective row pitch = h + gap_y - nest_offset_y.
    pitch = h + gap_y - nest_offset_y
    if pitch < 1:                 # guard against nonsensical offsets
        pitch = 1.0
    y = 0.0
    row = 0
    while y + h <= usable_h + 0.01:
        rot = base_rot if row % 2 == 0 else (base_rot + 180) % 360
        for c in range(cols):
            positions.append({
                'x': c * (w + gap_x), 'y': y,
                'w': w, 'h': h, 'rotation': rot,
            })
        row += 1
        y += pitch

    return {'total': len(positions), 'cols': cols,
            'positions': positions, 'piece_w': w, 'piece_h': h}


def calc_tete_beche_h(blank_w, blank_h, usable_w, usable_h,
                      gap_x, gap_y, nest_offset_x, base_rot=0):
    """Tête-bêche with alternating pieces within rows (horizontal nesting)."""
    w, h = (blank_w, blank_h) if base_rot in (0, 180) else (blank_h, blank_w)
    if w <= 0 or h <= 0 or w > usable_w or h > usable_h:
        return {'total': 0, 'positions': [], 'piece_w': w, 'piece_h': h}

    rows = int((usable_h + gap_y) / (h + gap_y))
    if rows <= 0:
        return {'total': 0, 'positions': [], 'piece_w': w, 'piece_h': h}
    positions = []

    # Within each row, pieces alternate orientation left-to-right. Each piece
    # after the first nests into its neighbor (flaps interlock), so the
    # horizontal pitch shrinks by nest_offset_x.
    pitch = w + gap_x - nest_offset_x
    if pitch < 1:
        pitch = 1.0

    for r in range(rows):
        y = r * (h + gap_y)
        x = 0.0
        col = 0
        while x + w <= usable_w + 0.01:
            rot = base_rot if col % 2 == 0 else (base_rot + 180) % 360
            positions.append({
                'x': x, 'y': y, 'w': w, 'h': h, 'rotation': rot,
            })
            col += 1
            x += pitch

    return {'total': len(positions), 'rows': rows,
            'positions': positions, 'piece_w': w, 'piece_h': h}


# ── Main engine ──────────────────────────────────────────────────────

def run_arrangements(cfg):
    bw, bh = cfg['blank_w'], cfg['blank_h']
    sw, sh = cfg['sheet_w'], cfg['sheet_h']
    gx, gy = cfg.get('gap_x', 10), cfg.get('gap_y', 10)
    m_top = cfg.get('margin_top', 20)
    m_bot = cfg.get('margin_bottom', 10)
    m_left = cfg.get('margin_left', 10)
    m_right = cfg.get('margin_right', 10)
    nox = cfg.get('nest_offset_x', 0)
    noy = cfg.get('nest_offset_y', 0)

    uw = sw - m_left - m_right
    uh = sh - m_top - m_bot
    blank_area = bw * bh
    sheet_area = sw * sh

    def enrich(result, name, name_es):
        t = result['total']
        used = t * blank_area
        # Final sheet size actually needed = margins + the real occupied extent
        # of the pieces. If the layout doesn't reach the max sheet, this tells
        # the operator the smaller sheet they can order/trim to.
        pos = result.get('positions', [])
        if pos:
            right = max(p['x'] + p['w'] for p in pos)
            bottom = max(p['y'] + p['h'] for p in pos)
            final_w = round(m_left + right + m_right, 1)
            final_h = round(m_top + bottom + m_bot, 1)
        else:
            final_w = final_h = 0
        # The press width is trimmable to anything up to the maximum, so the
        # board actually consumed is final_w × the format height. Measuring
        # against the 1040 mm max would make a narrow job look wasteful when
        # that material is never bought.
        real_area = final_w * final_h if final_w else 0      # sheet truly occupied
        bobina_area = final_w * sh if final_w else 0         # board actually bought
        return {
            **result,
            'name': name, 'name_es': name_es,
            'used_area': used,
            'final_sheet_w': final_w,
            'final_sheet_h': final_h,
            'real_sheet_w': final_w,
            'real_sheet_h': final_h,
            'bobina_h': sh,
            'sobra_h_mm': round(sh - final_h, 1),
            'trim_w': round(sw - final_w, 1),
            'trim_h': round(sh - final_h, 1),
            'waste_pct': round((1 - used / real_area) * 100, 1) if real_area else 100,
            'utilization_pct': round(used / real_area * 100, 1) if real_area else 0,
            'utilization_bobina_pct': round(used / bobina_area * 100, 1) if bobina_area else 0,
            'utilization_vs_max_pct': round(used / sheet_area * 100, 1) if sheet_area else 0,
        }

    arrangements = [
        enrich(calc_grid(bw, bh, uw, uh, gx, gy, 0),
               'Grid (Portrait)', 'Cuadrícula (Vertical)'),
        enrich(calc_grid(bw, bh, uw, uh, gx, gy, 90),
               'Grid (Landscape)', 'Cuadrícula (Horizontal)'),
        enrich(calc_tete_beche_v(bw, bh, uw, uh, gx, gy, noy, 0),
               'Tête-bêche V (Portrait)', 'Tête-bêche V (Vertical)'),
        enrich(calc_tete_beche_v(bw, bh, uw, uh, gx, gy, nox, 90),
               'Tête-bêche V (Landscape)', 'Tête-bêche V (Horizontal)'),
        enrich(calc_tete_beche_h(bw, bh, uw, uh, gx, gy, nox, 0),
               'Tête-bêche H (Portrait)', 'Tête-bêche H (Vertical)'),
        enrich(calc_tete_beche_h(bw, bh, uw, uh, gx, gy, noy, 90),
               'Tête-bêche H (Landscape)', 'Tête-bêche H (Horizontal)'),
    ]

    arrangements.sort(key=lambda a: (-a['total'], -a['utilization_pct']))
    return arrangements


# ── HTML / SVG generation ────────────────────────────────────────────

COLOR_NORMAL = '#3B82F6'
COLOR_ROTATED = '#EF4444'

def build_svg(arrangement, cfg):
    sw, sh = cfg['sheet_w'], cfg['sheet_h']
    m_top = cfg.get('margin_top', 20)
    m_left = cfg.get('margin_left', 10)
    offset_x = m_left
    offset_y = m_top

    # Scale SVG to max 860px wide
    scale = min(860 / sw, 540 / sh)
    svg_w = sw * scale
    svg_h = sh * scale

    parts = []
    # Sheet background
    parts.append(f'<rect x="0" y="0" width="{svg_w:.1f}" height="{svg_h:.1f}" '
                 f'fill="#FFFFFF" stroke="#1E293B" stroke-width="1.5" rx="2"/>')

    # Top margin band (gripper / lead edge)
    if m_top > 0:
        gh = m_top * scale
        parts.append(f'<rect x="0" y="0" width="{svg_w:.1f}" height="{gh:.1f}" '
                     f'fill="#FEE2E2" stroke="#FECACA" stroke-width="0.5"/>')
        parts.append(f'<text x="{svg_w/2:.1f}" y="{gh/2 + 4:.1f}" text-anchor="middle" '
                     f'font-size="10" fill="#B91C1C" font-family="sans-serif">'
                     f'Top margin {m_top}mm</text>')

    # Pieces
    for i, p in enumerate(arrangement.get('positions', [])):
        x = (offset_x + p['x']) * scale
        y = (offset_y + p['y']) * scale
        w = p['w'] * scale
        h = p['h'] * scale
        rot = p.get('rotation', 0)
        color = COLOR_NORMAL if rot in (0, 90) else COLOR_ROTATED
        opacity = '0.25'

        if rot in (0, 90):
            parts.append(
                f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
                f'fill="{color}" fill-opacity="{opacity}" stroke="{color}" stroke-width="0.8" rx="1">'
                f'<title>Piece {i+1} ({rot}°)</title></rect>')
        else:
            cx, cy = x + w/2, y + h/2
            parts.append(
                f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
                f'fill="{color}" fill-opacity="{opacity}" stroke="{color}" stroke-width="0.8" rx="1" '
                f'transform="rotate(180 {cx:.2f} {cy:.2f})">'
                f'<title>Piece {i+1} ({rot}°)</title></rect>')

        # Small orientation arrow
        arrow_size = min(w, h) * 0.18
        ax = x + w / 2
        ay = y + 4 + arrow_size / 2
        if rot in (180, 270):
            ay = y + h - 4 - arrow_size / 2
        parts.append(
            f'<polygon points="{ax:.1f},{ay - arrow_size/2:.1f} '
            f'{ax - arrow_size/2:.1f},{ay + arrow_size/2:.1f} '
            f'{ax + arrow_size/2:.1f},{ay + arrow_size/2:.1f}" '
            f'fill="{color}" fill-opacity="0.5" '
            f'transform="rotate({rot} {ax:.1f} {ay:.1f})"/>')

    # Dimension annotations
    # Width annotation (bottom)
    parts.append(f'<line x1="0" y1="{svg_h + 15:.1f}" x2="{svg_w:.1f}" y2="{svg_h + 15:.1f}" '
                 f'stroke="#64748B" stroke-width="0.8" marker-start="url(#arrow)" marker-end="url(#arrow)"/>')
    parts.append(f'<text x="{svg_w/2:.1f}" y="{svg_h + 30:.1f}" text-anchor="middle" '
                 f'font-size="11" fill="#334155" font-family="sans-serif">{sw} mm</text>')
    # Height annotation (right)
    parts.append(f'<line x1="{svg_w + 15:.1f}" y1="0" x2="{svg_w + 15:.1f}" y2="{svg_h:.1f}" '
                 f'stroke="#64748B" stroke-width="0.8"/>')
    parts.append(f'<text x="{svg_w + 20:.1f}" y="{svg_h/2:.1f}" '
                 f'font-size="11" fill="#334155" font-family="sans-serif" '
                 f'transform="rotate(90 {svg_w + 20:.1f} {svg_h/2:.1f})">{sh} mm</text>')

    inner = '\n    '.join(parts)
    return f'''<svg width="{svg_w + 50:.0f}" height="{svg_h + 40:.0f}"
     viewBox="0 0 {svg_w + 50:.1f} {svg_h + 40:.1f}"
     xmlns="http://www.w3.org/2000/svg">
  <defs>
    <marker id="arrow" markerWidth="6" markerHeight="6" refX="3" refY="3" orient="auto">
      <path d="M0,0 L6,3 L0,6" fill="#64748B" />
    </marker>
  </defs>
  {inner}
</svg>'''


def generate_html(arrangements, cfg):
    best = arrangements[0]
    sw, sh = cfg['sheet_w'], cfg['sheet_h']
    bw, bh = cfg['blank_w'], cfg['blank_h']
    title = cfg.get('title', 'Nesting Layout Report')
    client = cfg.get('client', '')
    project = cfg.get('project', '')

    svg_best = build_svg(best, cfg)

    # Build comparison rows
    table_rows = []
    for i, a in enumerate(arrangements):
        cls = ' class="best"' if i == 0 else ''
        star = ' ★' if i == 0 else ''
        table_rows.append(
            f'<tr{cls}><td>{a["name"]}{star}</td>'
            f'<td>{a["total"]}</td>'
            f'<td>{a["final_sheet_w"]} × {a["final_sheet_h"]}</td>'
            f'<td>{a["utilization_pct"]}%</td>'
            f'<td>{a["waste_pct"]}%</td></tr>')

    # Final-size callout
    reaches_w = best['final_sheet_w'] >= sw - 0.5
    reaches_h = best['final_sheet_h'] >= sh - 0.5
    if reaches_w and reaches_h:
        final_note = (f'Uses the full max sheet: <b>{sw} × {sh} mm</b>.')
    else:
        final_note = (f'Does not reach the max ({sw} × {sh} mm). '
                      f'You only need a sheet of <b>{best["final_sheet_w"]} × '
                      f'{best["final_sheet_h"]} mm</b> '
                      f'(save {best["trim_w"]} mm wide × {best["trim_h"]} mm long).')

    # Build per-sheet comparison if we have all standard sheets
    multi_sheet_html = ''
    if cfg.get('_multi_sheet_results'):
        ms_rows = []
        for ms in cfg['_multi_sheet_results']:
            ms_rows.append(
                f'<tr><td>{ms["sheet_name"]}</td>'
                f'<td>{ms["sheet_w"]} × {ms["sheet_h"]}</td>'
                f'<td>{ms["best_total"]}</td>'
                f'<td>{ms["best_arrangement"]}</td>'
                f'<td>{ms["utilization_pct"]}%</td>'
                f'<td>{ms["waste_pct"]}%</td></tr>')
        multi_sheet_html = f'''
  <div class="card">
    <h2>Sheet Size Comparison</h2>
    <table>
      <thead><tr><th>Sheet</th><th>Dimensions</th><th>Pieces</th><th>Best Arrangement</th><th>Utilization</th><th>Waste</th></tr></thead>
      <tbody>{"".join(ms_rows)}</tbody>
    </table>
  </div>'''

    subtitle_parts = []
    if client:
        subtitle_parts.append(f'Client: {client}')
    if project:
        subtitle_parts.append(f'Project: {project}')
    subtitle_parts.append(f'Best: {best["name"]}')
    subtitle = ' — '.join(subtitle_parts)

    date_str = datetime.now().strftime('%Y-%m-%d %H:%M')

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #F1F5F9; color: #1E293B; padding: 24px;
  }}
  .container {{ max-width: 960px; margin: 0 auto; }}
  .header {{
    background: #FFFFFF; border-radius: 12px; padding: 20px 24px;
    margin-bottom: 14px; box-shadow: 0 1px 3px rgba(0,0,0,.08);
    display: flex; justify-content: space-between; align-items: center;
  }}
  .header h1 {{ font-size: 20px; font-weight: 700; }}
  .header .sub {{ font-size: 13px; color: #64748B; margin-top: 2px; }}
  .header .date {{ font-size: 12px; color: #94A3B8; }}
  .stats {{
    display: grid; grid-template-columns: repeat(4,1fr); gap: 10px;
    margin-bottom: 14px;
  }}
  .stat {{
    background: #FFF; border-radius: 10px; padding: 14px; text-align: center;
    box-shadow: 0 1px 3px rgba(0,0,0,.08);
  }}
  .stat .val {{ font-size: 26px; font-weight: 800; color: #3B82F6; }}
  .stat .lbl {{ font-size: 11px; color: #64748B; margin-top: 2px; text-transform: uppercase; letter-spacing: .4px; }}
  .stat.green .val {{ color: #16A34A; }}
  .card {{
    background: #FFF; border-radius: 12px; padding: 20px 24px;
    margin-bottom: 14px; box-shadow: 0 1px 3px rgba(0,0,0,.08);
  }}
  .card h2 {{ font-size: 15px; font-weight: 600; margin-bottom: 14px; }}
  .svg-wrap {{
    display: flex; justify-content: center; background: #F8FAFC;
    border: 1px solid #E2E8F0; border-radius: 8px; padding: 16px;
    overflow-x: auto;
  }}
  .dims {{
    font-size: 12px; color: #94A3B8; margin-top: 10px;
  }}
  .legend {{ display: flex; gap: 18px; margin-top: 10px; font-size: 12px; color: #64748B; }}
  .legend i {{ display: inline-block; width: 12px; height: 12px; border-radius: 2px; vertical-align: -1px; margin-right: 4px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; padding: 8px 12px; font-size: 11px; text-transform: uppercase;
       letter-spacing: .4px; color: #64748B; border-bottom: 2px solid #E2E8F0; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #F1F5F9; }}
  tr.best td {{ background: #F0FDF4; font-weight: 600; }}
  .btn {{
    display: inline-flex; align-items: center; gap: 6px; padding: 8px 16px;
    background: #3B82F6; color: #FFF; border: none; border-radius: 6px;
    cursor: pointer; font-size: 13px; font-weight: 500; margin-top: 10px;
  }}
  .btn:hover {{ background: #2563EB; }}
  @media (max-width: 640px) {{
    .stats {{ grid-template-columns: repeat(2,1fr); }}
  }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div>
      <h1>{title}</h1>
      <div class="sub">{subtitle}</div>
    </div>
    <div class="date">{date_str}</div>
  </div>

  <div class="stats">
    <div class="stat green"><div class="val">{best['total']}</div><div class="lbl">Pieces / Sheet</div></div>
    <div class="stat"><div class="val" style="font-size:18px">{best['final_sheet_w']} × {best['final_sheet_h']}</div><div class="lbl">Final Sheet (mm)</div></div>
    <div class="stat"><div class="val">{best['utilization_pct']}%</div><div class="lbl">Utilization</div></div>
    <div class="stat"><div class="val">{best['waste_pct']}%</div><div class="lbl">Waste</div></div>
  </div>

  <div class="card" style="border-left:4px solid #16A34A">
    <div style="font-size:13px;color:#334155">{final_note}</div>
  </div>

  <div class="card">
    <h2>Layout — {best['name']}</h2>
    <div class="svg-wrap">{svg_best}</div>
    <div class="dims">
      Sheet: {sw} × {sh} mm &nbsp;|&nbsp;
      Blank: {bw} × {bh} mm &nbsp;|&nbsp;
      Gap: {cfg.get('gap_x',10)} × {cfg.get('gap_y',10)} mm &nbsp;|&nbsp;
      Margins T/B/L/R: {cfg.get('margin_top',20)}/{cfg.get('margin_bottom',10)}/{cfg.get('margin_left',10)}/{cfg.get('margin_right',10)} mm
    </div>
    <div class="legend">
      <span><i style="background:{COLOR_NORMAL};opacity:.35"></i> Normal (0° / 90°)</span>
      <span><i style="background:{COLOR_ROTATED};opacity:.35"></i> Rotated (180° / 270°)</span>
    </div>
  </div>

  <div class="card">
    <h2>All Arrangements</h2>
    <table>
      <thead><tr><th>Arrangement</th><th>Pieces</th><th>Final Sheet (mm)</th><th>Utilization</th><th>Waste</th></tr></thead>
      <tbody>{"".join(table_rows)}</tbody>
    </table>
  </div>

  {multi_sheet_html}

  <button class="btn" onclick="dlSvg()">
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="M7 10l5 5 5-5"/><path d="M12 15V3"/>
    </svg>
    Download SVG
  </button>
</div>
<script>
function dlSvg(){{
  const s=document.querySelector('.svg-wrap svg');
  const b=new Blob([new XMLSerializer().serializeToString(s)],{{type:'image/svg+xml'}});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(b);
  a.download='nesting-layout.svg';
  a.click();
  URL.revokeObjectURL(a.href);
}}
</script>
</body>
</html>'''


# ── CLI entry point ──────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print('Usage: nesting_engine.py \'<json_config>\'', file=sys.stderr)
        sys.exit(1)

    cfg = json.loads(sys.argv[1])
    for k, v in DEFAULTS.items():
        cfg.setdefault(k, v)

    arrangements = run_arrangements(cfg)
    html = generate_html(arrangements, cfg)

    out = cfg.get('output_path', 'nesting_report.html')
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)

    best = arrangements[0]
    print(json.dumps({
        'best_arrangement': best['name'],
        'total_pieces': best['total'],
        'waste_pct': best['waste_pct'],
        'utilization_pct': best['utilization_pct'],
        'max_sheet_w': cfg['sheet_w'],
        'max_sheet_h': cfg['sheet_h'],
        'final_sheet_w': best['final_sheet_w'],
        'final_sheet_h': best['final_sheet_h'],
        'reaches_max_width': best['final_sheet_w'] >= cfg['sheet_w'] - 0.5,
        'reaches_max_height': best['final_sheet_h'] >= cfg['sheet_h'] - 0.5,
        'output_path': out,
        'all_arrangements': [
            {'name': a['name'], 'pieces': a['total'],
             'final_sheet_w': a['final_sheet_w'], 'final_sheet_h': a['final_sheet_h'],
             'waste_pct': a['waste_pct'], 'utilization_pct': a['utilization_pct']}
            for a in arrangements
        ],
    }, indent=2))


if __name__ == '__main__':
    main()
