"""Trusted SVG renderer using the analysis rows directly."""
import math
import xml.etree.ElementTree as ET


def downtime_svg(result):
    rows = result['rows']
    if len(rows) > 50:
        raise ValueError('Too many chart categories')
    for row in rows:
        value = row['down_minutes']
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
            raise ValueError('Invalid chart value')
    svg = ET.Element('svg', xmlns='http://www.w3.org/2000/svg', width='800', height=str(100+len(rows)*55), role='img')
    ET.SubElement(svg, 'title').text = f"Synthetic shift {result['shift']} downtime by reason"
    ET.SubElement(svg, 'rect', width='100%', height='100%', fill='#f8fafc')
    ET.SubElement(svg, 'text', x='20', y='30', fill='#0f172a').text = f"Synthetic shift {result['shift']} | Downtime (minutes) | {result['coverage']} coverage"
    maximum = max((r['down_minutes'] for r in rows), default=1) or 1
    for index, row in enumerate(rows):
        y = 65+index*55
        ET.SubElement(svg, 'text', x='20', y=str(y+18), fill='#0f172a').text = str(row['category'])[:60]
        ET.SubElement(svg, 'rect', x='220', y=str(y), width=str(450*row['down_minutes']/maximum), height='25', fill='#2563eb')
        ET.SubElement(svg, 'text', x='685', y=str(y+18), fill='#0f172a').text = f"{row['down_minutes']:g} min"
    return ET.tostring(svg, encoding='unicode')
