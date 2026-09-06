"""PDFs are built from a retained server-side investigation, never visitor HTML."""
from io import BytesIO
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether


def report_pdf(result):
    stream=BytesIO()
    styles=getSampleStyleSheet()
    styles['BodyText'].fontSize=9
    styles['BodyText'].leading=13
    styles['Heading2'].keepWithNext=True
    styles['Heading2'].textColor=colors.HexColor('#087f75')
    def p(value, style='BodyText'):
        return Paragraph(escape(str(value)).replace('\n','<br/>'), styles[style])
    story=[p('Corrugator Shift Investigator','Title'),p('SYNTHETIC PRODUCTION REPORT','Heading2'),
           p(f'Production scope: {result.get("period_label",result["production_day"])} | Shifts: '+', '.join(map(str,result['selected_shifts']))),
           p(f'Investigation status: {result["status"]} | {result["mode"]}'),Spacer(1,12)]
    for line in result['sections']:
        story.extend([p(line),Spacer(1,5)])
    def table(title, rows, columns, limit=40):
        if not rows:return
        story.append(p(title,'Heading2'))
        if len(rows)>limit:
            note=p(f'Summary shows {limit} of {len(rows)} rows. Full evidence is available in the application.');note.keepWithNext=True;story.append(note)
        cells=[[p(label) for key,label in columns]]
        for row in rows[:limit]:
            cells.append([p(f'{row.get(key):,.2f}' if isinstance(row.get(key),float) else row.get(key,'-') if row.get(key) is not None else 'Unavailable') for key,label in columns])
        width=A4[0]-80
        widths=[width/len(columns)]*len(columns)
        if columns[-1][0]=='notes':widths=[width*.17,width*.17,width*.21,width*.45]
        t=Table(cells,repeatRows=1,colWidths=widths,hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#deeeec')),('VALIGN',(0,0),(-1,-1),'TOP'),
            ('LINEBELOW',(0,0),(-1,0),1,colors.HexColor('#087f75')),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f3f6f9')]),
            ('LEFTPADDING',(0,0),(-1,-1),6),('RIGHTPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
        story.extend([t,Spacer(1,10)])
    for chart in result.get('charts',[]):
        cols=[(chart['x'],'Run / day / reason'),(chart['y'],chart['unit'])]
        if chart['unit']=='ft/min':cols.extend([('target_fpm','Target ft/min'),('speed_to_target_pct','% of target')])
        table(chart.get('title',f'Shift {chart["shift"]}: {chart["unit"]} evidence'),chart['rows'],cols,12)
    for item in result.get('tables',[]):
        rows=item['rows']; title=item['title']
        if not rows:continue
        row=rows[0]
        if 'setup_id' in row:
            cols=[('setup_id','Setup'),('start','Start'),('end','End'),('stop_count','Stops'),('down_minutes','Down min'),('speed_to_target_pct','% target')]
        elif 'current_count' in row:
            cols=[('place','Place'),('reason','Reason'),('current_count','Stops now'),('current_minutes','Min now'),('previous_minutes','Min before'),('minutes_change','Change min')]
        elif 'speed_rank' in row:
            cols=[('shift','Shift'),('speed_rank','Speed rank'),('maintenance_rank','Maint. rank'),('waste_rank','Waste rank'),('paper_changes','Paper changes'),('valid_setups','Setups')]
        elif 'notes' in row:
            cols=[('record_id','Record'),('day','Day'),('place','Place'),('notes','Reported note')]
        elif 'rank' in row:
            cols=[('rank','Rank'),('item','Item'),('value','Value'),('unit','Unit / direction')]
        else:
            cols=[('day','Day'),('shift','Shift'),('observed_shift_fpm','ft/min'),('speed_to_target_pct','% target'),('paper_changes','Paper changes'),('valid_setups','Setups')]
        table(title + (f' | Shift {item["shift"]}' if 'shift' in item else ''),rows,cols,12 if 'notes' in row else 40)
    story.append(KeepTogether([p('Definitions and limitations','Heading2'),p('Speed attainment = lineal feet / sum(grade target ft/min x elapsed minutes) x 100. Downtime is included. Paper changes equal wet-end runs. Stop counts per setup include every intersecting stop; allocated minutes avoid double counting.'),p(result['limitations']),p('Run reference: '+result['run_id'])]))
    def footer(canvas, doc):
        canvas.saveState();canvas.setFont('Helvetica',8);canvas.setFillColor(colors.HexColor('#536878'))
        canvas.drawString(40,25,'Synthetic data | Read-only analysis | Reported notes are unverified')
        canvas.drawRightString(A4[0]-40,25,str(doc.page));canvas.restoreState()
    SimpleDocTemplate(stream,pagesize=A4,rightMargin=40,leftMargin=40,topMargin=36,bottomMargin=45,
        title='Synthetic corrugator production report',author='Shift Investigator').build(story,onFirstPage=footer,onLaterPages=footer)
    return stream.getvalue()
