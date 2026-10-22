"""Native vector drawings for the investigation charts inside the PDF report.

Every drawing is built from the same chart envelope the browser renders (rows the tools
produced, never visitor HTML): horizontal bars (with the previous window as a thin bar
when the chart carries y2), the weekly trend line (hollow markers for partial weeks), and
the over/under-run bullet (zero mid-axis, the band shaded). The calendar heatmap stays a
table. Colors mirror the light theme of the web UI.
"""
from reportlab.graphics.shapes import Drawing, Rect, Line, String, Circle
from reportlab.lib import colors

TEAL = colors.HexColor('#0b9b8c'); PREV = colors.HexColor('#aab6c0'); OUT = colors.HexColor('#d57426')
BAND = colors.HexColor('#e2f0ee'); GRID = colors.HexColor('#dfe6ec'); INK = colors.HexColor('#142333'); INK3 = colors.HexColor('#6a7d8c')
W = 515  # A4 width minus margins


def _fmt(v):
    if v is None: return 'n/a'
    v = float(v)
    return f'{v:,.0f}' if abs(v) >= 100 else f'{v:,.1f}' if abs(v) >= 10 else f'{v:,.2f}'


def _nice(v):
    import math
    if not v > 0: return 1.0
    p = 10 ** math.floor(math.log10(v)); m = v / p
    return (1 if m <= 1 else 2 if m <= 2 else 2.5 if m <= 2.5 else 5 if m <= 5 else 10) * p


def _label(text, n=26):
    text = str(text)
    return text if len(text) <= n else text[:n-1] + '…'


def bar_drawing(chart, max_rows=14):
    rows = [r for r in chart['rows'] if r.get(chart['y']) is not None][:max_rows]
    y2 = chart.get('y2'); row_h = 22 if y2 else 18
    label_w, value_w, top = 150, 120, 8
    h = top + len(rows) * row_h + 24
    d = Drawing(W, h)
    x0, x1 = label_w + 6, W - value_w - 6
    vals = [abs(float(r[chart['y']] or 0)) for r in rows] + ([abs(float(r[y2] or 0)) for r in rows if r.get(y2) is not None] if y2 else [])
    mx = _nice(max(vals) if vals else 1)
    for t in (0, .5, 1):
        gx = x0 + (x1 - x0) * t
        d.add(Line(gx, h - (top + len(rows) * row_h), gx, h - top, strokeColor=GRID if t else INK3, strokeWidth=.6))
        d.add(String(gx, h - (top + len(rows) * row_h) - 12, _fmt(mx * t), fontName='Helvetica', fontSize=7, fillColor=INK3, textAnchor='start' if t == 0 else 'end' if t == 1 else 'middle'))
    for i, r in enumerate(rows):
        yb = h - (top + (i + 1) * row_h) + 4  # reportlab y grows upward
        d.add(String(x0 - 6, yb + 4, _label(r[chart['x']]), fontName='Helvetica', fontSize=7.5, fillColor=INK, textAnchor='end'))
        cur = abs(float(r[chart['y']] or 0))
        d.add(Rect(x0, yb + (6 if y2 else 2), (cur / mx) * (x1 - x0), 10 if y2 else 12, fillColor=TEAL, strokeColor=None))
        text = _fmt(r[chart['y']])
        if y2 and r.get(y2) is not None:
            prev = abs(float(r[y2]))
            d.add(Rect(x0, yb + 1, (prev / mx) * (x1 - x0), 4, fillColor=PREV, strokeColor=None))
            text += f' vs {_fmt(r[y2])}'
        d.add(String(x1 + 6, yb + 4, text, fontName='Helvetica', fontSize=7.5, fillColor=INK))
    return d


def line_drawing(chart):
    rows = [r for r in chart['rows'] if r.get(chart['y']) is not None]
    h, m = 150, dict(l=50, r=20, t=10, b=28)
    d = Drawing(W, h)
    if not rows: return d
    vals = [float(r[chart['y']]) for r in rows]; lo, hi = min(vals), max(vals)
    if lo == hi: lo, hi = lo - 1, hi + 1
    pad = (hi - lo) * .25; lo, hi = max(0, lo - pad), hi + pad
    xs = lambda i: (m['l'] + (W - m['l'] - m['r']) / 2) if len(rows) == 1 else m['l'] + (W - m['l'] - m['r']) * i / (len(rows) - 1)
    ys = lambda v: m['b'] + (h - m['t'] - m['b']) * (v - lo) / (hi - lo)
    for t in (0, .5, 1):
        v = lo + (hi - lo) * t
        d.add(Line(m['l'], ys(v), W - m['r'], ys(v), strokeColor=GRID, strokeWidth=.6))
        d.add(String(m['l'] - 4, ys(v) - 2.5, _fmt(v), fontName='Helvetica', fontSize=7, fillColor=INK3, textAnchor='end'))
    for i in range(1, len(rows)):
        d.add(Line(xs(i - 1), ys(vals[i - 1]), xs(i), ys(vals[i]), strokeColor=TEAL, strokeWidth=1.5))
    for i, r in enumerate(rows):
        partial = bool(r.get('partial'))
        d.add(Circle(xs(i), ys(vals[i]), 3, fillColor=colors.white if partial else TEAL, strokeColor=TEAL, strokeWidth=1.2))
        d.add(String(xs(i), 10, str(r[chart['x']])[5:], fontName='Helvetica', fontSize=6.5, fillColor=INK3, textAnchor='middle'))
    d.add(String(xs(len(rows) - 1) + 5, ys(vals[-1]) - 2.5, _fmt(vals[-1]), fontName='Helvetica', fontSize=7.5, fillColor=INK))
    return d


def range_drawing(chart):
    rows = chart['rows']; band = float(chart.get('band_pct') or 2); row_h = 20; label_w, value_w, top = 110, 150, 8
    h = top + len(rows) * row_h + 24
    d = Drawing(W, h)
    x0, x1 = label_w + 6, W - value_w - 6
    span = _nice(max([band * 1.5] + [abs(float(r[chart['y']] or 0)) for r in rows]))
    xp = lambda v: x0 + (x1 - x0) * (v + span) / (2 * span)
    ybot = h - (top + len(rows) * row_h)
    d.add(Rect(xp(-band), ybot, xp(band) - xp(-band), len(rows) * row_h, fillColor=BAND, strokeColor=None))
    for v in (-span, -band, 0, band, span):
        d.add(Line(xp(v), ybot, xp(v), ybot + len(rows) * row_h, strokeColor=INK3 if v == 0 else GRID, strokeWidth=.6))
        d.add(String(xp(v), ybot - 10, f'{v:+g}%' if v else '0%', fontName='Helvetica', fontSize=7, fillColor=INK3, textAnchor='middle'))
    for i, r in enumerate(rows):
        yb = h - (top + (i + 1) * row_h) + 4
        v = float(r[chart['y']] or 0); inside = abs(v) <= band
        d.add(String(x0 - 6, yb + 4, _label(r[chart['x']]), fontName='Helvetica', fontSize=7.5, fillColor=INK, textAnchor='end'))
        d.add(Rect(min(xp(0), xp(v)), yb + 3, max(abs(xp(v) - xp(0)), 1.5), 9, fillColor=TEAL if inside else OUT, strokeColor=None))
        d.add(Circle(xp(v), yb + 7.5, 3.2, fillColor=TEAL if inside else OUT, strokeColor=None))
        d.add(String(x1 + 6, yb + 4, f'{v:+.2f}% · {round(float(r.get("lines_inside_band_pct") or 0))}% in band', fontName='Helvetica', fontSize=7.5, fillColor=INK))
    return d


def drawing_for(chart):
    kind = chart.get('type')
    if kind == 'line': return line_drawing(chart)
    if kind == 'range': return range_drawing(chart)
    if kind == 'calendar_heatmap': return None
    return bar_drawing(chart)
