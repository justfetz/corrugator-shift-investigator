"""Answer shapes (issue #75): each offline answer as a headline, findings and where to look.

Presentation of tool results only. Every figure is a value of a tool row, or of the envelope that carries
the rows, in one of the application's formats, or a count of rows; nothing here computes a new number.
A shape depends on the plan that ran (the intent and the evidence) and the scope, never on the wording
of the question, so a question and its canonical URL answer with the same sections (docs/URLS.md).

The sections stay plain text, so the Markdown export, the PDF and the /evals page read them as before,
with a light structure the client renders (docs/AGENT_CONTRACT.md, "Answer shapes"):

- the headline: the first section that carries a `**figure**` and is not a bullet. The figure is the
  asked measure with its unit; the rest of the line is what it is held against (target, previous window).
- `- ` a bullet: one finding with exactly one `**figure**`, then its evidence `(query N · row M)`: the
  step of the trace (numbered as the query box numbers them) and the row of that result the figure came
  from (`detail M`, `weekday M` and `flag M` name the result's sub-reason, weekday and flag lists; no row:
  the result as a whole, a count of its rows or a total it carries).
- a label: a short line without digits or final punctuation that introduces the bullets after it.
- `Where to look` bullets name a place in the records, never an action, and end with the question that
  opens it, `→ “question”`; each is a question the offline router answers in the same scope.
Any other line is a plain sentence: a caveat, the window and its coverage, a reported note.
"""
import re

from . import targets   # the owner's bands (issue #101): a threshold in prose is read from targets.json

LOOK = 'Where to look'
# `(query 2 · row 0)`, `(query 3)`: the evidence a bullet or headline cites.
REF = re.compile(r' \(query (\d+)(?: · (row|detail|weekday|flag) (\d+))?\)')
LOOK_QUESTION = re.compile(r' → “([^”]+)”$')
BOLD = re.compile(r'\*\*([^*]+)\*\*')
PARTS = {'row': 'rows', 'detail': 'details', 'weekday': 'weekday_profile', 'flag': 'flags'}
# The URL grammar resolves these period intents as the same plan (urls.PERIOD_FORMS), so they share a shape.
ALIASES = {'period_downtime': 'period_pareto', 'period_quality': 'period_waste', 'period_dollars': 'period_money'}
MARGIN_NOT_SET = 'margin not set'   # cost_model.MARGIN_NOT_SET: no owner margin, so no contribution is priced
WORDS = {2: 'two', 3: 'three'}
TREND_NAMES = {'trim_pct': 'side trim', 'shear_pct': 'shear', 'dry_end_pct': 'dry-end rejects', 'warp_pct': 'warp', 'bond_pct': 'bond',
               'misalignment_pct': 'misalignment', 'moisture_pct': 'moisture', 'speed_run_fpm': 'speed without stops',
               'speed_all_fpm': 'speed with stops', 'downtime_pct': 'downtime', 'stops': 'stops', 'minutes_per_stop': 'minutes per stop',
               'msf_per_day': 'MSF per production day', 'lineal_per_setup': 'lineal per setup', 'lineal_per_paper_change': 'lineal per paper change'}
RANK_LABELS = {'speed': 'Lowest speed attainment', 'downtime': 'Most downtime', 'quality': 'Most rejected area'}


# ------------------------------------------------------------------ formats and markers

def f0(v):
    return f'{v:,.0f}'


def f1(v):
    return f'{v:,.1f}'


def f2(v):
    return f'{v:,.2f}'


def n(v):
    return f'{v:,}'


def query(result):
    return result['result_id'].rsplit(':', 1)[1]


def ref(result, row=None, part='row'):
    return f'(query {query(result)})' if row is None else f'(query {query(result)} · {part} {row})'


def bullet(text, result, row=None, part='row'):
    return f'- {text} {ref(result, row, part)}'


def look(text, question):
    return f'- {text} → “{question}”'


# ---- the money beside the quantities (issue #31): every dollar is a priced row's own field
def money(value):
    return 'unavailable' if value is None else f'${value:,.0f}'


def priced(row):
    """` · $127 ($97,795/yr)` after a quantity whose row the cost model priced; nothing for an unpriced row."""
    if row.get('usd') is None:
        return ''
    return f' · {money(row["usd"])}' + (f' ({money(row["usd_per_year"])}/yr)' if row.get('usd_per_year') is not None else '')


def money_notes(result, row=None, label=None, formula=True):
    """The rates and the cost note on one line, after one row's pricing formula as the cost model wrote it
    (`formula=False` on a day card, which keeps the one rates line the accepted layout has)."""
    data = result['data']
    if 'provenance' not in data:
        return []
    notes = [f'How a dollar is priced, for {row[label]}: {row["usd_basis"]}.'] if formula and row is not None and row.get('usd_basis') else []
    return notes + [f'{data["provenance"]} {data["cost_note"]}']


def contribution(data):
    if data.get('contribution_status') == MARGIN_NOT_SET:
        return f'Contribution {MARGIN_NOT_SET}: without an owner margin no lost contribution is priced, so the dollars are paper and machine time.'
    if data.get('total_contribution_usd') is not None:
        return f'Contribution {money(data["total_contribution_usd"])} at the set margin, not added to the losses above.'
    return None


def side(value, limit, high='above', low='under'):
    return high if value > limit else low if value < limit else 'at'


def stops_text(count):
    return f'{n(count)} stop' + ('' if count == 1 else 's')


def band(text):
    """` · on target (3% or under)`: the band a tool put beside a figure (targets.json, issue #101); nothing without one."""
    return f' · {text}' if text else ''


def flag_lines(result, flags):
    """Each flag of a downtime result as two findings: the flagged shift with who to ask, then the recurrence over
    the trailing year of the reason that led it (record_store.downtime_flags; issue #101)."""
    out = []
    for i, f in enumerate(flags):
        out.append(bullet(f'Shift {f["shift"]}, {f["production_day"]}: **{f2(f["pct_of_shift"])}%** of the shift in {f["measure"]}{band(f["band_text"])} · '
                          f'{f2(f["minutes"])} min over {stops_text(f["stops"])} · ask {f["supervisor"]}', result, i, 'flag'))
        out.append(bullet(f'Shift {f["shift"]}, {f["reason"]} led it with {f2(f["reason_minutes"])} min: over the band on its own on '
                          f'**{n(f["times_over_band"])}** of {n(f["shifts_counted"])} shifts since {f["recurrence_since"]}, {f["recurrence_scope"]} · '
                          f'in the weekly top five {n(f["weeks_in_top_five"])} of {n(f["weeks_counted"])} weeks', result, i, 'flag'))
    return out


PLANNED = ('Planned maintenance', 'PM')   # the Pareto's planned-maintenance category, by reason and by kind: scheduled time
FIRST_REASONS = 3   # the card shows five findings (web/app.js BULLETS_SHOWN): three reasons, then a flag and its recurrence
FIRST_FLAGS = 5     # the largest flags listed beside the top reasons; every other flag follows the other reasons


def with_flags(reasons, flags):
    """The top reasons, then the largest flags (always on the card, never folded), then the other reasons, then every
    other flag. `flags` is flag_lines' two lines per flag, the largest first; each flag keeps its recurrence."""
    first = 2 * FIRST_FLAGS
    out = reasons[:FIRST_REASONS] + ['Flags and who to ask', *flags[:first]]
    out += ['The other reasons', *reasons[FIRST_REASONS:]] if len(reasons) > FIRST_REASONS else []
    return out + (['More flags', *flags[first:]] if len(flags) > first else [])


def plain(text):
    """The text a reader sees: markers removed, evidence references kept."""
    return BOLD.sub(r'\1', text[2:] if text.startswith('- ') else text)


def is_label(text, following):
    return (bool(following) and following.startswith('- ') and not text.startswith('- ') and '**' not in text
            and not re.search(r'\d|[.:;!?]$', text) and len(text) <= 48)


def argmin(rows, key):
    candidates = [(r[key], i) for i, r in enumerate(rows) if r.get(key) is not None]
    return min(candidates)[1] if candidates else None


def argmax(rows, key):
    candidates = [(-r[key], i) for i, r in enumerate(rows) if r.get(key) is not None]
    return min(candidates)[1] if candidates else None


# ------------------------------------------------------------------ entry point

def answer_sections(intent, evidence, context, period_tools):
    """The sections of an offline answer: headline, bullets, caveats and where to look."""
    intent = ALIASES.get(intent, intent)
    period = [r for r in evidence if r['tool'] in period_tools]
    daily = [r for r in evidence if r['tool'] not in period_tools]
    lines = []
    if period:
        own = intent.startswith('period_')
        lines += PeriodAnswer(intent if own else infer_period(period), period, context, inferred=not own).sections()
    if daily:
        own = not intent.startswith('period_') and intent != 'model'
        lines += DailyAnswer(intent if own else infer_daily(daily), daily, context, inferred=not own).sections()
    return order(lines)


def order(lines):
    """One headline, first: a second answer's headline (a mixed analyst plan) reads as a bullet-free line."""
    seen = False
    out = []
    for line in lines:
        if not line.startswith('- ') and '**' in line:
            if seen:
                line = BOLD.sub(r'\1', line)
            seen = True
        out.append(line)
    looks = [i for i, line in enumerate(out) if line == LOOK]
    if len(looks) > 1:   # one where-to-look list, at the end
        keep, block = [], []
        i = 0
        while i < len(out):
            if out[i] == LOOK:
                i += 1
                while i < len(out) and out[i].startswith('- ') and LOOK_QUESTION.search(out[i]):
                    if out[i] not in block:
                        block.append(out[i])
                    i += 1
                continue
            keep.append(out[i])
            i += 1
        out = keep + [LOOK, *block[:3]]
    return out


def infer_daily(evidence):
    tools = {r['tool'] for r in evidence}
    for tool, intent in (('get_shift_dollars', 'dollars'), ('get_shift_overview', 'summary'), ('get_setup_matrix', 'matrix'), ('get_order_matrix', 'orders'),
                         ('get_rankings', 'ranking'), ('get_quality_breakdown', 'quality')):
        if tool in tools:
            return intent
    if {'get_downtime_breakdown', 'get_wet_end_performance'} <= tools:
        return 'overview'
    if 'get_downtime_breakdown' in tools:
        return 'downtime'
    if 'get_wet_end_performance' in tools or 'get_shift_kpis' in tools:
        return 'speed'
    return 'notes'


def infer_period(evidence):
    tools = {r['tool'] for r in evidence}
    kpis = tools & {'get_period_kpis', 'compare_periods'}
    for tool, intent in (('get_downtime_review', 'period_review'), ('get_opportunities', 'period_money'), ('get_speed_variance', 'period_speedvar'),
                         ('get_speed_opportunity', 'period_speed_opportunity'), ('get_overrun', 'period_overrun'),
                         ('get_rollstock', 'period_rollstock'), ('get_trend', 'period_trend')):
        if tool in tools:
            return intent
    if 'get_grade_totals' in tools:
        speed = any(r['data'].get('measure') == 'speed' for r in evidence if r['tool'] == 'get_grade_totals')
        return 'period_speed' if speed else 'period_grade'
    if kpis and tools & {'get_downtime_pareto', 'get_waste_breakdown'}:
        return 'period_overview'
    if kpis:
        return 'period_compare'
    for tool, intent in (('get_downtime_pareto', 'period_pareto'), ('get_waste_breakdown', 'period_waste'),
                         ('get_stops_profile', 'period_stops')):
        if tool in tools:
            return intent
    return 'period_compare'


def group(evidence):
    """Results by shift, in the order the evidence names them; a tool's later results are kept in a list."""
    shifts = {}
    for result in evidence:
        tools = shifts.setdefault(result['data']['shift'], {})
        tools.setdefault(result['tool'], result)
        tools.setdefault('all:' + result['tool'], []).append(result)
    return shifts


def rank_words(count, incomplete, low=True):
    if count < 2:
        return ''
    word = ('lower' if low else 'higher') if count == 2 else ('lowest' if low else 'highest')
    return f', the {word} of the {WORDS.get(count, "")}{" complete" if incomplete else ""} shifts'.replace('the  ', 'the ')


# ------------------------------------------------------------------ the production day (daily tools)

class DailyAnswer:
    """One shift's answer (the focus), with a bullet per other shift when several are in the scope."""
    # intent -> (row field the focus is chosen by, lowest first?) ; None: the first shift
    FOCUS = {'overview': ('speed_to_target_pct', True), 'speed': ('speed_to_target_pct', True),
             'summary': ('speed_to_target_pct', True), 'compare': ('speed_to_target_pct', True),
             'downtime': ('downtime_pct', False), 'quality': ('dry_end_pct', False)}
    # The tools each shape reads for its focus shift; any other result there (an analyst's own plan) still gets
    # its own finding, so nothing the evidence holds drops out of the answer. Notes are always listed (all_notes).
    KPIS = 'get_shift_kpis'
    USES = {'overview': {KPIS, 'get_downtime_breakdown', 'get_wet_end_performance'}, 'speed': {KPIS, 'get_wet_end_performance'},
            'downtime': {KPIS, 'get_downtime_breakdown'}, 'quality': {KPIS, 'get_quality_breakdown'},
            'matrix': {KPIS, 'get_setup_matrix'}, 'notes': {KPIS}, 'orders': {KPIS, 'get_order_matrix'},
            'ranking': {KPIS, 'get_rankings'}, 'summary': {'get_shift_overview'}, 'dollars': {'get_shift_dollars'},
            'compare': {KPIS, 'get_downtime_breakdown', 'get_wet_end_performance'}}

    def __init__(self, intent, evidence, context, inferred=False):
        self.intent, self.context, self.inferred, self.generic = intent, context, inferred, False
        self.shifts = group(evidence)
        self.incomplete = [s for s, tools in self.shifts.items()
                           if any(r['data']['coverage'] != 'complete' for k, r in tools.items() if not k.startswith('all:'))]
        self.complete = [s for s in self.shifts if s not in self.incomplete]

    def suffix(self, shift):
        """The words that point a where-to-look question at the focus shift when the scope is all shifts."""
        return f' for shift {shift}' if self.context.scope == 'day' and shift else ''

    @staticmethod
    def kpis(tools):
        result = tools.get('get_shift_kpis') or tools.get('get_shift_overview')
        return result, (result['data']['rows'][0] if result and result['data']['rows'] else None)

    def kind_pct(self, tools):
        dt = tools.get('get_downtime_breakdown')
        kind = dt['arguments'].get('kind') if dt else None
        return {'Maintenance': 'maintenance_pct', 'Operator': 'operator_pct', 'PM': 'pm_pct'}.get(kind, 'op_maint_pct')

    def focus(self):
        if self.intent == 'dollars' and len(self.complete) > 1:
            totals = [(self.shifts[s]['get_shift_dollars']['data']['total_usd'], s) for s in self.complete if 'get_shift_dollars' in self.shifts[s]]
            if totals:
                return max(totals)[1]   # the shift that lost the most dollars
        if self.intent == 'ranking' and len(self.complete) > 1:
            # the shift whose first ranking leads: the lowest speed attainment, the most downtime or rejected area
            tops = [(r['data']['rows'][0]['value'], s, r['data'].get('metric')) for s in self.complete
                    for r in self.shifts[s].get('all:get_rankings', [])[:1] if r['data']['rows']]
            if tops:
                return (min if tops[0][2] == 'speed' else max)(tops)[1]
        field_low = self.FOCUS.get(self.intent)
        if not field_low or len(self.complete) < 2:
            return self.complete[0]
        field, low = field_low
        if self.intent == 'downtime':
            field = self.kind_pct(self.shifts[self.complete[0]])
        scored = [(self.kpis(self.shifts[s])[1], s) for s in self.complete]
        scored = [(row[field], s) for row, s in scored if row and row.get(field) is not None]
        if not scored or self.intent == 'compare':
            return self.complete[0]
        return (min if low else max)(scored)[1]

    def sections(self):
        lines = []
        for shift in self.incomplete:
            tools = self.shifts[shift]
            first = next(r for k, r in tools.items() if not k.startswith('all:') and r['data']['coverage'] != 'complete')
            lines.append(f'Shift {shift}: data incomplete; {len(first["data"]["excluded_records"])} excluded records. Do not rank its observed production totals.')
        if not self.complete:
            return lines
        focus = self.focus()
        shape = getattr(self, 'shape_' + self.intent, None) or self.shape_speed
        head, bullets, notes, looks = shape(focus)
        bullets += self.leftovers(focus)
        others = [] if self.intent == 'compare' else [b for s in self.complete if s != focus for b in [self.other(s)] if b]
        if self.inferred:   # an analyst's plan: every other shift's evidence is listed too
            others += [f'- Shift {s} · {b[2:]}' for s in self.complete if s != focus for b in self.leftovers(s, all_tools=True)]
        if others:   # several shifts: the focus shift's findings say whose they are
            bullets = [f'- Shift {focus} · {b[2:]}' if b.startswith('- ') and not b.startswith('- Shift ') else b for b in bullets]
        notes = list(dict.fromkeys(notes))   # two priced results name the same rates once
        out = [head, *others, *bullets, *lines, *self.all_notes(focus), *notes]   # the reported notes stay with the findings; caveats read under the charts
        if looks:
            out += [LOOK, *looks]
        return out

    def clause(self, focus, low=True):
        """`on shift 2, the lowest of the three shifts` when the scope holds several shifts; nothing for one.
        Put right after the figure the focus was chosen by, so the rank is said of the measure that was ranked."""
        return self.where(focus) + self.rank(low)

    def where(self, focus):
        return f' on shift {focus}' if self.context.scope == 'day' else ''

    def rank(self, low=True):
        return rank_words(len(self.complete), bool(self.incomplete), low) if self.context.scope == 'day' else ''

    def leftovers(self, shift, all_tools=False):
        """A finding for each result of the shift its shape did not read (an analyst's own plan); none offline."""
        if self.generic and not all_tools:
            return []
        used = set() if all_tools else self.USES.get(self.intent, set())
        out = []
        for key, results in self.shifts[shift].items():
            tool = key[4:] if key.startswith('all:') else None
            if tool is None or tool == 'get_shift_notes' or (all_tools and tool == 'get_shift_kpis'):
                continue   # notes are always listed; another shift's own bullet already states its KPIs
            if tool == 'get_rankings':   # one finding set covers every ranking of the shift
                results = [] if tool in used else results[:1]
            elif tool in used:
                results = results[1:]   # the shape read the first; a repeated call (by place after by reason) keeps its own
            for result in results:
                out += self.findings_of(result, self.shifts[shift])[0]
        return out

    def all_notes(self, focus):
        """Reported notes of every complete shift (the client folds them into one disclosure)."""
        lines = []
        for shift in [focus, *[s for s in self.complete if s != focus]]:
            notes = self.shifts[shift].get('get_shift_notes')
            if notes:
                lines += [f'Shift {shift}, {row["record_id"]} - reported note (synthetic, unverified): {row["notes"] or "No note recorded"}'
                          for row in notes['data']['rows']]
        return lines

    # ---- one bullet for a shift that is not the focus
    def other(self, shift):
        tools = self.shifts[shift]
        k, row = self.kpis(tools)
        if self.intent in ('overview', 'speed', 'summary') and row:
            return bullet(f'Shift {shift}: **{f1(row["speed_to_target_pct"])}%** of grade target · {f1(row["observed_shift_fpm"])} ft/min including downtime', k, 0)
        if self.intent == 'downtime' and row:
            field = self.kind_pct(tools)
            words = {'op_maint_pct': 'maintenance and operator', 'pm_pct': 'planned maintenance'}.get(field, field[:-4])
            return bullet(f'Shift {shift}: **{f2(row[field])}%** of the shift in {words} downtime{band(row.get(field[:-4] + "_band_text"))}', k, 0)
        if self.intent == 'quality' and row:
            return bullet(f'Shift {shift}: **{f2(row["dry_end_pct"])}%** of yield rejected at the dry end{band(row["dry_end_band_text"])}', k, 0)
        if self.intent == 'matrix' and row:
            return bullet(f'Shift {shift}: **{n(row["valid_setups"])}** dry-end setups · {n(row["paper_changes"])} paper changes', k, 0)
        if self.intent == 'notes' and tools.get('get_shift_notes'):
            return bullet(f'Shift {shift}: **{len(tools["get_shift_notes"]["data"]["rows"])}** reported notes', tools['get_shift_notes'])
        if self.intent == 'orders' and tools.get('get_order_matrix'):
            return bullet(f'Shift {shift}: **{len(tools["get_order_matrix"]["data"]["rows"])}** order fragments', tools['get_order_matrix'])
        if self.intent == 'ranking':
            ranking = next((r for r in tools.get('all:get_rankings', []) if r['data']['rows']), None)
            return self.ranked(ranking, 0, prefix=f'Shift {shift}, ') if ranking else None
        if self.intent == 'dollars' and tools.get('get_shift_dollars'):
            d = tools['get_shift_dollars']
            return bullet(f'Shift {shift}: **{money(d["data"]["total_usd"])}** of recorded losses · {money(d["data"]["total_usd_per_year"])} per year', d)
        return None

    # ---- findings shared by several shapes
    @staticmethod
    def largest_reason(dt, prefix=''):
        rows = dt['data']['rows']
        i = argmax(rows, 'down_minutes')
        if i is None:
            return None
        noun = {'reason': 'reasons', 'place': 'places', 'kind': 'kinds'}.get(dt['arguments'].get('group_by', 'reason'), 'reasons')
        rank = f'the largest of {len(rows)} {noun}' if len(rows) > 1 else f'the only one of the {noun}'
        return bullet(f'{prefix}{rows[i]["category"]}: **{f2(rows[i]["down_minutes"])} min** of recorded downtime, {rank}{priced(rows[i])}', dt, i)

    @staticmethod
    def slowest_run(we, prefix=''):
        rows = we['data']['rows']
        i = argmin(rows, 'speed_to_target_pct')
        if i is None:
            return None, None
        r = rows[i]
        rank = f'the slowest of {len(rows)} runs' if len(rows) > 1 else 'the only run'
        return bullet(f'{prefix}{r["wet_end_id"]} ({r["grade"]}): **{f1(r["speed_to_target_pct"])}%** of its {f0(r["target_fpm"])} ft/min grade target, {rank}', we, i), r

    @staticmethod
    def closest_run(we):
        rows = we['data']['rows']
        i = argmax(rows, 'speed_to_target_pct')
        if i is None or len(rows) < 2:
            return None
        r = rows[i]
        return bullet(f'{r["wet_end_id"]} ({r["grade"]}): **{f1(r["speed_to_target_pct"])}%** of its {f0(r["target_fpm"])} ft/min grade target, the highest of {len(rows)} runs', we, i)

    @staticmethod
    def below(we):
        rows = we['data']['rows']
        if not rows:
            return None
        below = sum(r['actual_fpm'] < r['target_fpm'] for r in rows)
        return bullet(f'Wet-end runs below their grade target: **{below}** of {len(rows)}', we)

    @staticmethod
    def under_baseline(we):
        """Runs under their grade's sustained speed over the trailing year (issue #101); none on a fixture day."""
        data = we['data']
        if data.get('runs_under_baseline') is None or not data['rows']:
            return None
        return bullet(f"Runs under their grade's trailing-year running speed: **{n(data['runs_under_baseline'])}** of {len(data['rows'])}", we)

    @staticmethod
    def kind_split(k, row):
        """The larger of maintenance and operator downtime against its target (targets.json), the other beside it."""
        pairs = [('Operator', row['operator_pct'], 'maintenance', row['maintenance_pct']),
                 ('Maintenance', row['maintenance_pct'], 'operator', row['operator_pct'])]
        lead, value, other, other_value = max(pairs, key=lambda p: p[1])
        target = targets.BANDS[lead.lower()]['target']
        return bullet(f'{lead} downtime: **{f2(value)}%** of the shift, {side(value, target, "over", "under")} the {targets.pct(target)} target · '
                      f'{other} {f2(other_value)}%', k, 0)

    def ranked(self, ranking, i, prefix=''):
        r = ranking['data']['rows'][i]
        metric = ranking['data'].get('metric')
        value = f'{f1(r["value"])}% of target' if metric == 'speed' else f'{f2(r["value"])} min' if metric == 'downtime' else f'{f1(r["value"])} sq ft'
        return bullet(f'{prefix}{r["rank"]}. {r["item"]}: **{value}**', ranking, i)

    # ---- shapes: (headline, bullets, caveat lines, where-to-look lines) for the focus shift
    def shape_overview(self, s):
        tools = self.shifts[s]
        k, row = self.kpis(tools)
        if not row:
            return self.shape_generic(s)
        head = (f'**{f1(row["speed_to_target_pct"])}%** of grade target{self.clause(s)} · {f1(row["observed_shift_fpm"])} ft/min '
                f'including downtime · {f2(row["downtime_pct"])}% of the shift down {ref(k, 0)}')
        bullets, looks, slow = [], [], None
        dt, we = tools.get('get_downtime_breakdown'), tools.get('get_wet_end_performance')
        if dt:
            bullets.append(self.largest_reason(dt))
        if we:
            line, slow = self.slowest_run(we)
            bullets += [line, self.below(we)]
        bullets.append(self.kind_split(k, row))
        if slow:
            looks.append(look(f'The setups on {slow["wet_end_id"]}, the slowest run, with their stops and rejects', f'Show the setup matrix{self.suffix(s)}'))
        if dt:
            looks.append(self.regroup(dt, s))
        if row.get('dry_end_band') in ('over target', 'flag'):
            looks.append(look(f'The dry-end rejects by reason, {row["dry_end_band_text"]}', f'Show dry-end rejects by reason{self.suffix(s)}'))
        notes = self.priced_notes(dt, 'down_minutes', 'category') if dt else []
        return head, [b for b in bullets if b], notes, looks

    @staticmethod
    def priced_notes(result, key, label):
        """The money notes of a priced daily result, its formula shown for the row with the most of `key`."""
        rows = result['data']['rows']
        i = argmax(rows, key)
        return money_notes(result, rows[i] if i is not None else None, label, formula=False)

    def regroup(self, dt, s):
        if dt['arguments'].get('group_by', 'reason') == 'reason':
            return look('The same stops by equipment, where on the line each minute was recorded', f'Show downtime by equipment{self.suffix(s)}')
        return look('The same stops by recorded reason', f'Show downtime by reason{self.suffix(s)}')

    def shape_speed(self, s):
        tools = self.shifts[s]
        k, row = self.kpis(tools)
        if not row:
            return self.shape_generic(s)
        head = f'**{f1(row["observed_shift_fpm"])} ft/min** including downtime{self.where(s)} · {f1(row["speed_to_target_pct"])}% of grade target{self.rank()} {ref(k, 0)}'
        bullets, looks = [], []
        we = tools.get('get_wet_end_performance')
        slow = None
        if we:
            line, slow = self.slowest_run(we)
            bullets += [line, self.closest_run(we), self.below(we), self.under_baseline(we)]
        bullets.append(bullet(f'Lineal feet: **{f0(row["observed_lineal_ft"])}** over {n(row["valid_setups"])} setups and {n(row["paper_changes"])} paper changes', k, 0))
        if slow:
            looks.append(look(f'The setups on {slow["wet_end_id"]}, the slowest run', f'Show the setup matrix{self.suffix(s)}'))
        looks.append(look('The downtime inside the elapsed time, by reason', f'Show the downtime breakdown{self.suffix(s)}'))
        return head, [b for b in bullets if b], [], looks

    def shape_downtime(self, s):
        tools = self.shifts[s]
        k, row = self.kpis(tools)
        dt = tools.get('get_downtime_breakdown')
        if not row or not dt:
            return self.shape_generic(s)
        kind = dt['arguments'].get('kind')
        if kind in ('Maintenance', 'Operator'):
            key = kind.lower()
            head = f'**{f2(row[key + "_pct"])}%** of the shift in {key} downtime{self.clause(s, low=False)}{band(row[key + "_band_text"])} {ref(k, 0)}'
        elif kind == 'PM':
            head = f'**{f2(row["pm_pct"])}%** of the shift in planned maintenance{self.clause(s, low=False)} {ref(k, 0)}'
        else:
            # The owner's rule (issue #101): maintenance and operator downtime lead, each against its band;
            # planned maintenance is scheduled time and stays out of the figure.
            head = (f'**{f2(row["op_maint_pct"])}%** of the shift in maintenance and operator downtime{self.clause(s, low=False)}{band(row["op_maint_band_text"])} · '
                    f'maintenance {f2(row["maintenance_pct"])}%, {row["maintenance_band_text"]} · operator {f2(row["operator_pct"])}%, {row["operator_band_text"]} {ref(k, 0)}')
        rows = dt['data']['rows']
        # Ranked by downtime % of the shift (the tool's order), stop count and minutes per stop beside; PM last.
        bullets = [bullet(f'{r["category"]}: **{f2(r["shift_percent"])}%** of the shift · {f2(r["down_minutes"])} min over {stops_text(r["stops"])}, '
                          f'{f2(r["minutes_per_stop"])} min per stop' + (' · scheduled time, not ranked' if r.get('scheduled') else '') + priced(r), dt, i)
                   for i, r in enumerate(rows)]
        flags = [line for shift in [s, *[x for x in self.complete if x != s]] for d in [self.shifts[shift].get('get_downtime_breakdown')]
                 if d and d['data'].get('flags') for line in flag_lines(d, d['data']['flags'])]
        if flags:   # a flag pulls in the shift: who to ask, and how often the same reason crossed its band
            bullets = with_flags(bullets, flags)
        notes = (self.priced_notes(dt, 'down_minutes', 'category') if rows else ['No recorded stops in this filter.'])
        looks = [look('The reported notes on those stops (synthetic, unverified)', f'Show the shift notes{self.suffix(s)}'),
                 look('The stop minutes allocated to each setup', f'Show the setup matrix{self.suffix(s)}'), self.regroup(dt, s)]
        return head, bullets, notes, looks

    def shape_quality(self, s):
        tools = self.shifts[s]
        k, row = self.kpis(tools)
        q = tools.get('get_quality_breakdown')
        if not row or not q:
            return self.shape_generic(s)
        de = row['dry_end_pct']
        head = f'**{f2(de)}%** of yield rejected at the dry end{self.clause(s, low=False)}{band(row["dry_end_band_text"])} {ref(k, 0)}'
        bullets = [bullet(f'{r["reason"]}: **{f1(r["rejected_sqft"])} sq ft**{priced(r)}', q, i) for i, r in enumerate(q['data']['rows'])]
        notes = self.priced_notes(q, 'rejected_sqft', 'reason') if bullets else ['No dry-end rejects recorded.']
        details = q['data'].get('details') or []
        if details:
            bullets += ['Reported sub-reasons'] + [bullet(f'{d["detail"]} ({d["reason"].lower()}): **{f1(d["rejected_sqft"])} sq ft**', q, i, 'detail')
                                                   for i, d in enumerate(details)]
        looks = [look('The rejected area setup by setup', f'Show the setup matrix{self.suffix(s)}'),
                 look('The shift and downtime notes (synthetic, unverified)', f'Show the shift notes{self.suffix(s)}')]
        return head, bullets, notes, looks

    def shape_matrix(self, s):
        tools = self.shifts[s]
        k, row = self.kpis(tools)
        m = tools.get('get_setup_matrix')
        if not row:
            return self.shape_generic(s)
        head = (f'**{n(row["valid_setups"])}** dry-end setups{self.clause(s, low=False).split(",")[0]} · {n(row["paper_changes"])} paper changes · '
                f'{f0(row["lineal_per_setup"])} ft per setup {ref(k, 0)}' if row['lineal_per_setup'] is not None else
                f'**{n(row["valid_setups"])}** dry-end setups{self.clause(s, low=False).split(",")[0]} {ref(k, 0)}')
        bullets, notes = [], []
        if m and m['data']['rows']:
            rows = m['data']['rows']
            i = argmin(rows, 'speed_to_target_pct')
            if i is not None:
                r = rows[i]
                bullets.append(bullet(f'{r["setup_id"]} ({r["grade"]} on {r["wet_end_id"]}): **{f1(r["speed_to_target_pct"])}%** of grade target, the slowest of {len(rows)} setups', m, i))
            i = argmax(rows, 'down_minutes')
            if i is not None and rows[i]['down_minutes'] > 0:
                r = rows[i]
                bullets.append(bullet(f'{r["setup_id"]}: **{f2(r["down_minutes"])} min** of allocated stops over {r["stop_count"]} stops, the most of any setup', m, i))
            i = argmax(rows, 'rejected_sqft')
            if i is not None and rows[i]['rejected_sqft'] > 0:
                r = rows[i]
                bullets.append(bullet(f'{r["setup_id"]}: **{f1(r["rejected_sqft"])} sq ft** rejected, the most of any setup', m, i))
            notes.append('Stop minutes are allocated by interval overlap; a stop spanning two setups appears in both stop counts.')
        bullets.append(bullet(f'Setups above the {targets.pct(targets.TRIM_FLAG_PCT)} trim flag: **{n(row["setups_above_trim_target"])}** of {n(row["valid_setups"])}', k, 0))
        looks = [look('The downtime behind the allocated stop minutes, by reason', f'Show the downtime breakdown{self.suffix(s)}'),
                 look('The rejected area by reason', f'Show dry-end rejects by reason{self.suffix(s)}')]
        return head, bullets, notes, looks

    def shape_notes(self, s):
        tools = self.shifts[s]
        notes = tools.get('get_shift_notes')
        if not notes:
            return self.shape_generic(s)
        head = f'**{len(notes["data"]["rows"])}** reported notes{self.clause(s, low=False).split(",")[0]} · synthetic and unverified: observations, not causes {ref(notes)}'
        looks = [look('The downtime those stops add up to, by reason', f'Show the downtime breakdown{self.suffix(s)}'),
                 look('Each setup with its stops and rejects', f'Show the setup matrix{self.suffix(s)}')]
        return head, [], [], looks

    def shape_orders(self, s):
        tools = self.shifts[s]
        orders = tools.get('get_order_matrix')
        if not orders:
            return self.shape_generic(s)
        head = f'**{len(orders["data"]["rows"])}** order fragments{self.clause(s, low=False).split(",")[0]} · demand repeats on continuing orders {ref(orders)}'
        notes = ['Requested and planned quantities repeat on continuing orders; sum produced sheets only. Remaining demand carries across shifts.',
                 'Produced sheets include rejects; accepted delivery and replacement scheduling are not modeled.']
        looks = [look('The setups those orders ran on', f'Show the setup matrix{self.suffix(s)}'),
                 look('The downtime between those orders, by reason', f'Show the downtime breakdown{self.suffix(s)}')]
        return head, [], notes, looks

    def shape_ranking(self, s):
        tools = self.shifts[s]
        rankings = tools.get('all:get_rankings', [])
        first = next((r for r in rankings if r['data']['rows']), None)
        if not first:
            return self.shape_generic(s)
        r0, metric = first['data']['rows'][0], first['data'].get('metric')
        where = self.clause(s, low=False).split(',')[0]
        head = (f'**{f1(r0["value"])}%** of target on {r0["item"]}{where}, the lowest speed attainment {ref(first, 0)}' if metric == 'speed' else
                f'**{f2(r0["value"])} min** at {r0["item"]}{where}, the most downtime {ref(first, 0)}' if metric == 'downtime' else
                f'**{f1(r0["value"])} sq ft** of {r0["item"]}{where}, the most rejected area {ref(first, 0)}')
        bullets = []
        for ranking in rankings:
            if ranking['data']['rows']:
                bullets.append(RANK_LABELS.get(ranking['data'].get('metric'), 'Ranking'))
                bullets += [self.ranked(ranking, i) for i in range(len(ranking['data']['rows']))]
        looks = [look('The setups behind the ranked losses', f'Show the setup matrix{self.suffix(s)}'),
                 look('The reported notes on the stops (synthetic, unverified)', f'Show the shift notes{self.suffix(s)}')]
        return head, bullets, [], looks

    def shape_summary(self, s):
        tools = self.shifts[s]
        o = tools.get('get_shift_overview')
        if not o:
            return self.shape_overview(s)
        row = o['data']['rows'][0]
        head = (f'**{f0(row["observed_lineal_ft"])} lineal ft**{self.where(s)} at {f1(row["observed_shift_fpm"])} ft/min including downtime · '
                f'{f1(row["speed_to_target_pct"])}% of grade target{self.rank()} {ref(o, 0)}')
        bullets = []
        if row['largest_downtime_minutes']:
            kind = 'scheduled time, the only downtime' if row.get('largest_downtime_scheduled') else 'the largest unplanned downtime reason'
            bullets.append(bullet(f'{row["largest_downtime_reason"]}: **{f2(row["largest_downtime_minutes"])} min**, {kind}', o, 0))
        if row['largest_reject_sqft']:
            bullets.append(bullet(f'{row["largest_reject_reason"]}: **{f1(row["largest_reject_sqft"])} sq ft**, the largest dry-end reject reason', o, 0))
        if row['wet_end_runs_observed']:
            bullets.append(bullet(f'Wet-end runs below their grade target: **{row["wet_end_runs_below_target"]}** of {row["wet_end_runs_observed"]}', o, 0))
        bullets.append(bullet(f'Downtime: **{f2(row["downtime_pct"])}%** of the shift · maintenance {f2(row["maintenance_pct"])}% · operator {f2(row["operator_pct"])}%', o, 0))
        bullets.append(bullet(f'Dry-end waste: **{f2(row["dry_end_pct"])}%** of the yield after trim and shear{band(row["dry_end_band_text"])}', o, 0))
        looks = [look('Each setup with its speed, stops and rejects', f'Show the setup matrix{self.suffix(s)}'),
                 look('The shift and downtime notes (synthetic, unverified)', f'Show the shift notes{self.suffix(s)}')]
        return head, bullets, [], looks

    def shape_compare(self, s):
        k, row = self.kpis(self.shifts[s])
        if not row:
            return self.shape_generic(s)
        # the fastest shift by elapsed speed, so the rank is said of the figure it was chosen by; every number on
        # the line is a value of its own row (the other shifts are bullets, each citing theirs)
        rated = [(self.kpis(self.shifts[o])[1]['observed_shift_fpm'], o) for o in self.complete if self.kpis(self.shifts[o])[1]]
        fast = max(rated)[1] if len(rated) > 1 else s
        k, row = self.kpis(self.shifts[fast])
        lead = f'Fastest: shift {fast} at ' if len(rated) > 1 else f'Shift {fast}: '
        head = f'{lead}**{f1(row["observed_shift_fpm"])} ft/min** including downtime · {f1(row["speed_to_target_pct"])}% of grade target {ref(k, 0)}'
        bullets = []
        for shift in self.complete:
            kk, rr = self.kpis(self.shifts[shift])
            if rr:
                bullets.append(bullet(f'Shift {shift}: **{f1(rr["speed_to_target_pct"])}%** of grade target · {f2(rr["downtime_pct"])}% of the shift down', kk, 0))
        lowest = DailyAnswer.focus_of(self, 'speed_to_target_pct')
        tools = self.shifts[lowest]
        prefix = f'Shift {lowest}, ' if len(self.complete) > 1 else ''
        if tools.get('get_downtime_breakdown'):
            bullets.append(self.largest_reason(tools['get_downtime_breakdown'], prefix))
        slow = None
        if tools.get('get_wet_end_performance'):
            line, slow = self.slowest_run(tools['get_wet_end_performance'], prefix)
            bullets.append(line)
        sfx = f' for shift {lowest}' if self.context.scope == 'day' else ''
        looks = []
        if slow:
            looks.append(look(f'The setups on {slow["wet_end_id"]}, the slowest run of shift {lowest}', f'Show the setup matrix{sfx}'))
        looks.append(look(f'The downtime of shift {lowest} by equipment', f'Show downtime by equipment{sfx}'))
        dt = tools.get('get_downtime_breakdown')
        return head, [b for b in bullets if b], self.priced_notes(dt, 'down_minutes', 'category') if dt else [], looks

    def shape_dollars(self, s):
        """Where the money went in one shift (get_shift_dollars): the total, then each gap ranked by dollars as the tool ranked it."""
        d = self.shifts[s].get('get_shift_dollars')
        if not d:
            return self.shape_generic(s)
        data, rows = d['data'], d['data']['rows']
        head = f'**{money(data["total_usd"])}** of recorded losses{self.clause(s, low=False)} · {money(data["total_usd_per_year"])} per year at the post\'s volumes {ref(d)}'
        bullets = [bullet(f'{r["rank"]}. {r["gap"]}: **{money(r["usd"])}**' + (f' · {money(r["usd_per_year"])}/yr' if r.get('usd_per_year') is not None else ''), d, i)
                   for i, r in enumerate(rows)]
        notes = [x for x in [contribution(data)] if x] + (money_notes(d, rows[0], 'gap') if rows else money_notes(d))
        looks = [look('The downtime behind the machine-time dollars, by reason', f'Show the downtime breakdown{self.suffix(s)}'),
                 look('The rejected area behind the dry-end dollars, by reason', f'Show dry-end rejects by reason{self.suffix(s)}')]
        return head, bullets, notes, looks

    def focus_of(self, field):
        scored = [(self.kpis(self.shifts[s])[1] or {}).get(field) for s in self.complete]
        pairs = [(v, s) for v, s in zip(scored, self.complete) if v is not None]
        return min(pairs)[1] if pairs else self.complete[0]

    def findings_of(self, result, tools):
        """(bullets, caveats) for one result, whatever shape it falls in: the generic finding of each daily tool."""
        data, tool = result['data'], result['tool']
        if tool in ('get_shift_kpis', 'get_shift_overview') and data['rows']:
            row = data['rows'][0]
            return [bullet(f'Speed: **{f1(row["observed_shift_fpm"])} ft/min** including downtime · {f1(row["speed_to_target_pct"])}% of grade target', result, 0)], []
        if tool == 'get_downtime_breakdown':
            by = {'reason': '', 'place': ' at ', 'kind': ' of kind '}.get(result['arguments'].get('group_by', 'reason'), '')
            return [bullet(f'{by.strip().capitalize() + " " if by else ""}{r["category"]}: **{f2(r["down_minutes"])} min** of recorded downtime, {f2(r["shift_percent"])}% of the shift{priced(r)}', result, i)
                    for i, r in enumerate(data['rows'])], self.priced_notes(result, 'down_minutes', 'category')
        if tool == 'get_wet_end_performance':
            return [b for b in (self.slowest_run(result)[0], self.below(result)) if b], []
        if tool == 'get_quality_breakdown':
            return [bullet(f'{r["reason"]}: **{f1(r["rejected_sqft"])} sq ft** rejected at the dry end{priced(r)}', result, i)
                    for i, r in enumerate(data['rows'])], self.priced_notes(result, 'rejected_sqft', 'reason')
        if tool == 'get_setup_matrix':
            return [bullet(f'Setup matrix: **{len(data["rows"])}** valid setups', result)], [
                'Stop minutes are allocated by interval overlap; a stop spanning two setups appears in both stop counts.']
        if tool == 'get_order_matrix':
            return [bullet(f'Order matrix: **{len(data["rows"])}** order fragments', result)], [
                'Requested and planned quantities repeat on continuing orders; sum produced sheets only.']
        if tool == 'get_rankings':
            return [self.ranked(r, 0) for r in tools.get('all:get_rankings', []) if r['data']['rows']], []
        if tool == 'get_shift_dollars':
            return [bullet(f'Recorded losses: **{money(data["total_usd"])}** · {money(data["total_usd_per_year"])} per year', result)], money_notes(result)
        return [], []

    def shape_generic(self, s):
        """Evidence a shape does not expect (an analyst's own plan): one finding per result, the first as the headline."""
        self.generic = True
        tools = self.shifts[s]
        bullets, notes = [], []
        for key, results in tools.items():
            if not key.startswith('all:') or key == 'all:get_shift_notes':
                continue
            for result in results[:1] if key == 'all:get_rankings' else results:   # every call, a repeated one too
                more, caveats = self.findings_of(result, tools)
                bullets += more
                notes += [c for c in caveats if c not in notes]
        if not bullets:
            return f'Shift {s}: no figures in this evidence.', [], notes, []
        return bullets[0][2:], bullets[1:], notes, []


# ------------------------------------------------------------------ a window of the record store (period tools)

def pval(value, unit=''):
    if value is None:
        return 'unavailable'
    if isinstance(value, int):
        return n(value)
    return f1(value) if unit in ('MSF', 'MSF/day', 'MSF per production day', 'ft', 'ft/min', 'ft/setup', 'ft/paper change') else f2(value)


def figure(value, unit):
    """(bold part, the rest): `3.26%` + ` of gross`, `601.2 ft/min` + ``."""
    text = pval(value, unit)
    if value is None:
        return text, ''
    if unit.startswith('%'):
        return text + '%', unit[1:]
    if unit in ('count', ''):
        return text, ''
    return f'{text} {unit}', ''


def signed(value, unit=''):
    if isinstance(value, int):
        return f'{value:+,}'
    return f'{value:+,.1f}' if pval(1.5, unit) == '1.5' else f'{value:+,.2f}'


def delta_clause(row):
    unit = row['unit']
    if row.get('delta') is not None:
        prev, rest = figure(row['previous'], unit)
        return f'{signed(row["delta"], unit)} vs {prev}{rest}, {row["change"]}'
    if row.get('previous') is not None:
        prev, rest = figure(row['previous'], unit)
        return f'previous {prev}{rest}'
    return 'previous unavailable'


def review_previous(row):
    """A review row's previous window: its percent of that window's scheduled minutes, or unavailable (never zero)."""
    if row.get('previous_pct_of_scheduled') is None:
        return 'previous unavailable'
    return f'previous {pval(row["previous_pct_of_scheduled"])}%'


def window_head(data):
    window, previous = data['window'], data.get('previous_window')
    scope = f'shift {data["shift"]}' if data['shift'] else 'all shifts'
    return (f'{window["label"]}, {scope}' + (f', versus {previous["label"]}' if previous else '') +
            f'. Window coverage: {window["coverage"]}' + (f'; previous {previous["coverage"]}' if previous else '') + '.')


def sentences(note):
    """A tool's note, one section per sentence, so no line runs past about forty words."""
    return [s for s in re.split(r'(?<=[.])\s+(?=[A-Z])', note or '') if s]


class PeriodAnswer:
    LEAD = {'period_compare': ('get_period_kpis', 'compare_periods'), 'period_overview': ('get_period_kpis', 'compare_periods'),
            'period_summary': ('get_period_kpis', 'compare_periods'), 'period_speed': ('get_grade_totals',),
            'period_grade': ('get_grade_totals',), 'period_pareto': ('get_downtime_pareto',), 'period_waste': ('get_waste_breakdown',),
            'period_stops': ('get_stops_profile',), 'period_trend': ('get_trend',), 'period_rollstock': ('get_rollstock',),
            'period_overrun': ('get_overrun',), 'period_speed_opportunity': ('get_speed_opportunity',),
            'period_money': ('get_opportunities',), 'period_speedvar': ('get_speed_variance',), 'period_review': ('get_downtime_review',)}

    KPI_TOOLS = ('get_period_kpis', 'compare_periods')
    USES = {'period_overview': (*KPI_TOOLS, 'get_downtime_pareto', 'get_waste_breakdown'),
            'period_summary': (*KPI_TOOLS, 'get_downtime_pareto', 'get_waste_breakdown'), 'period_speed': (*KPI_TOOLS, 'get_grade_totals')}

    def __init__(self, intent, evidence, context, inferred=False):
        self.intent = intent if intent in self.LEAD else infer_period(evidence)
        self.context, self.evidence, self.inferred = context, evidence, inferred
        self.shifts = group(evidence)

    def wp(self, data):
        """`this week` when the window is the selected day's week (month, 4-4-5 period); '' for a day; None otherwise."""
        window = data.get('window') or {}
        if window.get('period') == 'day' or 'start' not in window:
            return ''
        if not window['start'] <= self.context.production_day <= window['end']:
            return None
        return {'week': ' this week', 'month': ' this month', 'fiscal': ' this period'}.get(window['period'], '')

    def sfx(self, data):
        return f' for shift {data["shift"]}' if self.context.scope == 'day' and data.get('shift') else ''

    def sections(self):
        keys = list(self.shifts)
        lead_tools = self.LEAD[self.intent]
        focus = next((s for s in keys if any(t in self.shifts[s] for t in lead_tools)), keys[0])
        tools = self.shifts[focus]
        lead = next((tools[t] for t in lead_tools if t in tools), None)
        if lead is None:
            self.intent = infer_period([r for k, r in tools.items() if not k.startswith('all:')])
            lead = next(tools[t] for t in self.LEAD[self.intent] if t in tools)
        head, bullets, notes, looks = getattr(self, self.intent)(tools, lead)
        others, windows = [], [window_head(lead['data'])]
        for s in keys:
            if s == focus:
                continue
            other = next((self.shifts[s][t] for t in lead_tools if t in self.shifts[s]), None)
            if other:
                others.append(self.brief(other))
                windows.append(window_head(other['data']))   # each shift's window and coverage
        # any other result (an analyst's own plan, a repeated call) keeps its own finding: its headline, as a bullet
        used = set(self.USES.get(self.intent, lead_tools)) | set(lead_tools)
        for s in keys:
            if s != focus and not self.inferred:
                continue
            for key, results in self.shifts[s].items():
                if not key.startswith('all:'):
                    continue
                tool = key[4:]
                skip = 1 if (s == focus and tool in used) or (s != focus and tool in lead_tools) else 0
                for result in results[skip:]:
                    line = self.brief(result)
                    if line and line not in others:
                        bullets = [*bullets, line]
        if not self.whole(lead['data']):
            notes = [f'The window is incomplete (coverage {lead["data"]["coverage"]}): observed values only, and rankings are withheld.', *notes]
        notes = list(dict.fromkeys(notes))
        out = [head, *others, *[b for b in bullets if b], *dict.fromkeys(windows), *notes]
        looks = [x for x in looks if x]
        if looks:
            out += [LOOK, *looks]
        return out

    @staticmethod
    def whole(data):
        """A complete window: only then are its rows ranked (an incomplete one is observed values, never a ranking)."""
        return data.get('coverage') == 'complete'

    def where(self, data):
        """`on shift 2` for a window narrowed to a shift under the all-shift scope."""
        return f' on shift {data["shift"]}' if self.context.scope == 'day' and data.get('shift') else ''

    def brief(self, result):
        """One bullet for another shift's result of the lead tool."""
        data = result['data']
        label = f'Shift {data["shift"]}' if data.get('shift') else 'All shifts'
        if result['tool'] in ('get_period_kpis', 'compare_periods'):
            i, row = self.metric(result, 'volume_msf')
            return bullet(f'{label}: **{pval(row["current"], "MSF")} MSF** · {delta_clause(row)}', result, i)
        head = self.headline_of(result)
        return '- ' + label + ': ' + head.replace(self.where(data), '', 1) if head else None

    def headline_of(self, result):
        tools = {result['tool']: result}
        head = getattr(self, PeriodAnswer.intent_of(result))(tools, result)[0]
        return head

    @staticmethod
    def intent_of(result):
        return infer_period([result])

    @staticmethod
    def metric(result, key):
        for i, row in enumerate(result['data']['rows']):
            if row['metric'] == key:
                return i, row
        return None, None

    def kpi_bullet(self, result, key):
        i, row = self.metric(result, key)
        if row is None:
            return None
        fig, rest = figure(row['current'], row['unit'])
        return bullet(f'{row["label"]}: **{fig}**{rest} · {delta_clause(row)}{band(row.get("band_text"))}', result, i)

    def kpi_notes(self, data):
        notes = []
        if data.get('comparison', 'complete') != 'complete':
            notes.append('Deltas are withheld: a window is incomplete or unavailable. Missing days are not zero production.')
        if data.get('day_basis_note'):
            notes.append(data['day_basis_note'])
        return notes

    # ---- shapes: (headline, bullets, caveat lines, where-to-look lines)
    def period_compare(self, tools, k):
        data = k['data']
        key = 'msf_per_day' if data.get('equal_production_days') is False else 'volume_msf'
        i, row = self.metric(k, key)
        fig, rest = figure(row['current'], row['unit'])
        head = f'**{fig}**{rest}{self.where(data)} · {delta_clause(row)} {ref(k, i)}'
        bullets = [self.kpi_bullet(k, m) for m in ('speed_all_fpm', 'downtime_pct', 'minutes_per_stop', 'trim_pct', 'dry_end_pct', 'lineal_per_paper_change')]
        return head, bullets, self.kpi_notes(data), self.window_looks(data, ('pareto', 'waste', 'grade'))

    def period_overview(self, tools, k):
        data = k['data']
        i, row = self.metric(k, 'speed_all_fpm')
        head = f'**{pval(row["current"], "ft/min")} ft/min** with stops{self.where(data)} · {delta_clause(row)} {ref(k, i)}'
        return head, self.loss_bullets(tools, k), self.kpi_notes(data) + self.loss_notes(tools), self.window_looks(data, ('stops', 'grade', 'trend'))

    def period_summary(self, tools, k):
        data = k['data']
        i, vol = self.metric(k, 'volume_msf')
        head = f'**{pval(vol["current"], "MSF")} MSF** produced{self.where(data)} · {delta_clause(vol)} {ref(k, i)}'
        return head, [self.kpi_bullet(k, 'speed_all_fpm'), *self.loss_bullets(tools, k)], self.kpi_notes(data) + self.loss_notes(tools), self.window_looks(data, ('stops', 'grade', 'trend'))

    def loss_bullets(self, tools, k):
        bullets = []
        pareto, waste = tools.get('get_downtime_pareto'), tools.get('get_waste_breakdown')
        if pareto and pareto['data']['rows']:
            rows = pareto['data']['rows']
            i = next((j for j, x in enumerate(rows) if x['category'] not in PLANNED), 0)   # planned maintenance never leads (#101)
            r = rows[i]
            bullets.append(bullet(f'{r["category"]}: **{pval(r["minutes"])} min** of downtime, {pval(r["share_pct"])}% of the window\'s minutes{priced(r)}', pareto, i))
        if waste:
            i, r = self.largest_waste(waste)
            if r:
                rank = ', the largest waste' if self.whole(waste['data']) else ''
                bullets.append(bullet(f'{r["label"]}: **{pval(r["pct_of_gross"])}%** of gross{rank} · {self.points(r)}{priced(r)}', waste, i))
        return bullets + [self.kpi_bullet(k, 'downtime_pct'), self.kpi_bullet(k, 'dry_end_pct')]

    @staticmethod
    def loss_notes(tools):
        notes = []
        for key, label in (('get_downtime_pareto', 'category'), ('get_waste_breakdown', 'label')):
            result = tools.get(key)
            if result and result['data']['rows']:
                notes += money_notes(result, result['data']['rows'][0], label)
        return notes

    @staticmethod
    def largest_waste(waste):
        rows = [(r['pct_of_gross'], i) for i, r in enumerate(waste['data']['rows'])
                if r['category'] not in ('bale', 'unexplained', 'dry_end') and r['pct_of_gross'] is not None]
        if not rows:
            return None, None
        i = max(rows)[1]
        return i, waste['data']['rows'][i]

    @staticmethod
    def points(r):
        if r.get('previous_pct_of_gross') is None:
            return 'previous unavailable'
        return f'previous {pval(r["previous_pct_of_gross"])}%, {r["delta_pct_points"]:+.2f} points'

    def period_speed(self, tools, g):
        data, rows = g['data'], g['data']['rows']
        order = sorted((r['speed_to_target_pct'], i) for i, r in enumerate(rows) if r.get('speed_to_target_pct') is not None)
        k = tools.get('get_period_kpis') or tools.get('compare_periods')
        if k:
            j, run = self.metric(k, 'speed_run_fpm')
            head = f'**{pval(run["current"], "ft/min")} ft/min** without stops{self.where(data)} · {delta_clause(run)} {ref(k, j)}'
        elif order and self.whole(data):
            i = order[0][1]
            r = rows[i]
            head = f'Slowest: {r["grade"]} at **{f1(r["speed_run_fpm"])} ft/min**{self.where(data)} · {f1(r["speed_to_target_pct"])}% of its {f0(r["target_fpm"])} target {ref(g, i)}'
        elif order:
            head = f'**{len(rows)}** grades observed{self.where(data)} in an incomplete window · their speeds against target, not ranked {ref(g)}'
        else:
            head = f'No setups in this window{self.where(data)} {ref(g)}'
        shown = order[:3] if self.whole(data) else [(None, i) for i in range(len(rows))]   # an incomplete window is listed, never ranked
        bullets = ([self.kpi_bullet(k, 'speed_all_fpm')] if k else []) + [
            bullet(f'{rows[i]["grade"]}: **{f1(rows[i]["speed_run_fpm"])} ft/min** · {f1(rows[i]["speed_to_target_pct"])}% of target · {n(rows[i]["setups"])} setups', g, i)
            for _, i in shown]
        notes = (self.kpi_notes(k['data']) if k else []) + sentences(data.get('speed_note'))
        wp, sfx = self.wp(data), self.sfx(data)
        looks = []
        if order and self.whole(data):
            looks.append(look(f'{rows[order[0][1]]["grade"]} by web width, typical against best-fifth speed', f'Where is the speed opportunity{wp or ""}{sfx}?'))
        elif order:
            looks.append(look('Each grade by web width, typical against best-fifth speed', f'Where is the speed opportunity{wp or ""}{sfx}?'))
        looks.append(look('Running speed week by week', f'Trend of running speed over eight weeks{sfx}'))
        return head, bullets, notes, looks

    def period_grade(self, tools, g):
        data, rows = g['data'], g['data']['rows']
        head = f'**{f1(data["total_volume_msf"])} MSF**{self.where(data)} across {len(rows)} grades {ref(g)}'
        bullets = []
        for i, r in enumerate(rows):
            previous = f'{signed(r["delta_volume_msf"], "MSF")} vs {f1(r["previous_volume_msf"])} MSF' if r.get('previous_volume_msf') is not None else 'previous unavailable'
            stops = f' · {n(r["setup_stops"])} setup stops, {f2(r["downtime_pct_of_elapsed"])}% of its elapsed time' if r.get('downtime_pct_of_elapsed') is not None else ''
            bullets.append(bullet(f'{r["grade"]}: **{f1(r["volume_msf"])} MSF** · {f2(r["share_of_volume_pct"])}% of the volume · {previous}{stops}', g, i))
        return head, bullets, sentences(data.get('speed_note')), self.window_looks(data, ('speed', 'opportunity', 'rollstock'))

    def period_pareto(self, tools, p):
        data, rows = p['data'], p['data']['rows']
        minutes = data.get('measure', 'minutes') == 'minutes'
        noun = {'reason': 'reasons', 'place': 'places', 'kind': 'kinds'}.get(data.get('group_by'), 'reasons')
        # Planned maintenance is scheduled time (issue #101): it never leads; it is listed where the tool put it, marked.
        lead = next((j for j, x in enumerate(rows) if x['category'] not in PLANNED), 0)
        if rows:
            r = rows[lead]
            fig = f'{pval(r["minutes"])} min' if minutes else f'{n(r["stops"])} stops'
            rank = (', the largest unplanned reason' if lead else f', the largest of {len(rows)} {noun}') if self.whole(data) else ''
            head = f'**{fig}** at {r["category"]}{self.where(data)}{rank} · {pval(r["share_pct"])}% of the window\'s {"minutes" if minutes else "stops"}{priced(r)} {ref(p, lead)}'
        else:
            head = f'No recorded stops in this window{self.where(data)} {ref(p)}'
        bullets = []
        for i, r in sorted(enumerate(rows), key=lambda x: (x[1]['category'] in PLANNED, x[0])):   # scheduled time drops to the bottom
            if i == lead:
                continue
            fig = f'{pval(r["minutes"])} min' if minutes else f'{n(r["stops"])} stops'
            other = f'over {n(r["stops"])} stops' if minutes else f'· {pval(r["minutes"])} min'
            previous = f'previous {pval(r["previous_minutes"])} min' if r.get('previous_minutes') is not None else 'previous unavailable'
            planned = ' · scheduled time, not an unplanned loss' if r['category'] in PLANNED else ''
            bullets.append(bullet(f'{r["category"]}: **{fig}** {other} · {pval(r["share_pct"])}%, cumulative {pval(r["cumulative_pct"])}% · {previous}{planned}{priced(r)}', p, i))
        notes = ['Recorded reasons are symptoms, not proven causes.'] + (money_notes(p, rows[lead], 'category') if rows else [])
        return head, bullets, notes, self.window_looks(data, ('stops', 'trend_downtime', 'waste'))

    def period_waste(self, tools, w):
        data, rows = w['data'], w['data']['rows']
        i, r = self.largest_waste(w)
        if r:
            rank = ', the largest waste' if self.whole(data) else ''
            head = f'**{pval(r["pct_of_gross"])}%** of gross in {r["label"].lower()}{self.where(data)}{rank} · {self.points(r)}{priced(r)}{band(r.get("band_text"))} {ref(w, i)}'
        else:
            head = f'No waste recorded in this window{self.where(data)} {ref(w)}'
        bullets = [bullet(f'{x["label"]}: **{pval(x["pct_of_yield"])}%** of the yield after trim and shear · {pval(x["pct_of_gross"])}% of gross · '
                          f'{f1(x["sqft"])} sq ft{priced(x)}{band(x.get("band_text"))}', w, j) if x['category'] == 'dry_end' else
                   bullet(f'{x["label"]}: **{pval(x["pct_of_gross"])}%** of gross · {f1(x["sqft"])} sq ft · {self.points(x)}{priced(x)}{band(x.get("band_text"))}', w, j)
                   for j, x in enumerate(rows) if j != i]
        details = data.get('details') or []
        if details:
            bullets += ['Dry-end by reported sub-reason'] + [
                bullet(f'{d["detail"]} ({d["reason"].lower()}): **{pval(d["share_of_dry_end_pct"])}%** of dry-end rejects · {f1(d["sqft"])} sq ft', w, j, 'detail')
                for j, d in enumerate(details)]
        notes = ['Bale is a scale reading; unexplained is bale minus trim, shear and rejects.']
        if details:
            notes.append('Sub-reasons are reported observations, not proven causes.')
        notes += money_notes(w, r, 'label') if r else []
        return head, bullets, notes, self.window_looks(data, ('trend_dry_end', 'grade', 'pareto'))

    def period_stops(self, tools, st):
        data, rows = st['data'], st['data']['rows']
        i = argmax(rows, 'stops')
        if i is not None:
            r = rows[i]
            rank = (f', the most of {len(rows)} production days' if len(rows) > 1 else ', the only production day') if self.whole(data) else ''
            head = f'**{n(r["stops"])} stops** on {r["production_day"]} ({r["weekday"]}){self.where(data)}{rank} · {pval(r["minutes"])} min {ref(st, i)}'
        else:
            head = f'No production days in this window{self.where(data)} {ref(st)}'
        profile = [(j, p) for j, p in enumerate(data.get('weekday_profile') or []) if p['days']]
        bullets = (['Weekday profile'] + [bullet(f'{p["weekday"]}: **{pval(p["stops_per_day"])} stops per day** · {pval(p["minutes_per_day"])} min per day over {n(p["days"])} day(s)', st, j, 'weekday')
                                          for j, p in profile]) if profile else []
        return head, bullets, [], self.window_looks(data, ('pareto', 'trend_stops'))

    def period_trend(self, tools, t):
        data, rows = t['data'], t['data']['rows']
        unit = data['unit']
        notes = []
        if data.get('missing_weeks'):
            notes.append('Weeks without records: ' + ', '.join(data['missing_weeks']) + '.')
        if not rows:
            return f'No weeks available for {data["metric"]} {ref(t)}', [], notes, []

        def value(r):
            fig, rest = figure(r['value'], unit)
            return f'**{fig}**{rest}' + (' (partial week)' if r['partial'] else '')
        last = len(rows) - 1
        head = (f'{value(rows[last])} in the week of {rows[last]["week_start"]}{self.where(data)}, the latest of {len(rows)} weeks of '
                f'{TREND_NAMES.get(data["metric"], data["metric"])}{band(rows[last].get("band_text"))} {ref(t, last)}')
        bullets, used = [], {last}
        among = '' if self.whole(data) else ' of the weeks with records'
        for label, i in (('the first week of the series', 0), ('the highest' + among, argmax(rows, 'value')), ('the lowest' + among, argmin(rows, 'value'))):
            if i is not None and i not in used:
                used.add(i)
                bullets.append(bullet(f'Week of {rows[i]["week_start"]}: {value(rows[i])}, {label}', t, i))
        metric = data['metric']
        kind = 'pareto' if metric in ('downtime_pct', 'stops', 'minutes_per_stop') else 'speed' if 'speed' in metric else \
            'grade' if metric in ('msf_per_day', 'lineal_per_setup', 'lineal_per_paper_change') else 'waste'
        sfx = self.sfx(data)
        looks = {'pareto': [look('The downtime of the latest week by reason', f'Which downtime reason cost the most minutes this week{sfx}?')],
                 'speed': [look('The latest week by grade, against each grade target', f'Which grade is slowest against target this week{sfx}?')],
                 'grade': [look('The latest week by grade', f'Volume by grade this week{sfx}')],
                 'waste': [look('The latest week\'s waste by component and sub-reason', f'Waste breakdown this week{sfx}')]}[kind]
        looks.append(look('The latest week\'s KPIs against the week before', f'Compare this week to last{sfx}'))
        return head, bullets, notes, looks

    def period_rollstock(self, tools, r):
        data, rows = r['data'], r['data']['rows']
        head = f'**{f1(data["total_msf"])} MSF** of paper consumed{self.where(data)} · {f1(data["total_lineal_ft"])} ft over {n(data["paper_runs"])} paper runs {ref(r)}'
        bullets = []
        if data.get('previous_total_msf') is not None:
            bullets.append(bullet(f'Previous window: **{f1(data["previous_total_msf"])} MSF** · {f1(data["previous_total_lineal_ft"])} ft over {n(data["previous_paper_runs"])} paper runs · '
                                  f'{signed(data["delta_msf"], "MSF")} MSF this window', r))
        else:
            bullets.append(f'- Previous window unavailable {ref(r)}')
        order = sorted(((-x['msf'], i) for i, x in enumerate(rows) if x.get('msf') is not None))[:3]
        for _, i in order:
            x = rows[i]
            previous = f'previous {f1(x["previous_msf"])} MSF' if x.get('previous_msf') is not None else 'previous unavailable'
            bullets.append(bullet(f'{x["label"]}: **{f1(x["msf"])} MSF** · {f1(x["lineal_ft"])} ft over {n(x["paper_runs"])} runs · {previous}', r, i))
        notes = [f'{f1(data["lineal_per_run"])} ft per paper run.' if data.get('lineal_per_run') is not None else None,
                 *sentences(data.get('attribution_note')), *sentences(data.get('msf_note'))]
        return head, bullets, [x for x in notes if x], self.window_looks(data, ('grade', 'trend_volume'))

    def period_overrun(self, tools, o):
        data, rows = o['data'], o['data']['rows']
        total = next((i for i, x in enumerate(rows) if x['knife'] == 'all knives'), None)
        if total is not None:
            x = rows[total]
            head = (f'**{x["overrun_pct"] + 0.0:+.2f}%** of nominal cuts across all knives{self.where(data)} · {f1(x["lines_inside_band_pct"])}% of lines inside '
                    f'the band {ref(o, total)}')
        else:
            head = f'No customer lines in this window{self.where(data)} {ref(o)}'
        bullets = [bullet(f'{x["knife"]}: **{x["overrun_pct"] + 0.0:+.2f}%** of nominal cuts · {n(x["over_sheets"])} sheets over and {n(x["under_sheets"])} under · '
                          f'{f1(x["lines_inside_band_pct"])}% of lines inside the band', o, i) for i, x in enumerate(rows) if i != total]
        return head, bullets, sentences(data.get('band_note')), self.window_looks(data, ('grade', 'rollstock'))

    def period_speed_opportunity(self, tools, s):
        data, rows = s['data'], s['data']['rows']
        head = (f'**{f2(data["total_opportunity_hours"])} hours**{self.where(data)} if every cell ran at its best-fifth speed · '
                f'{n(data["excluded_short_setups"])} setups under {data["min_run_minutes"]:g} running minutes excluded {ref(s)}')
        order = sorted((-x['opportunity_hours'], i) for i, x in enumerate(rows) if x.get('opportunity_hours') is not None)[:3]
        if not self.whole(data):
            order = [(None, i) for i in range(len(rows))]   # an incomplete window is listed in the tool's order, never ranked
        bullets = []
        for _, i in order:
            x = rows[i]
            bullets.append(bullet(f'{x["label"]}: **{f2(x["opportunity_hours"])} hours** · typical {f1(x["typical_fpm"])} ft/min ({f1(x["typical_pct_of_target"])}% of target) '
                                  f'vs best fifth {f1(x["best_fifth_fpm"])} ({f1(x["best_fifth_pct_of_target"])}%) · dry-end {f2(x["best_fifth_dry_end_pct"])}% on the fast fifth '
                                  f'vs {f2(x["rest_dry_end_pct"])}% on the rest{priced(x)}', s, i))
        notes = []
        if data.get('thin_cells'):
            notes.append(f'{len(data["thin_cells"])} cells with fewer than {n(data["min_setups"])} setups are listed in the evidence but not ranked.')
        notes += sentences(data.get('method_note')) + sentences(data.get('temperature_note'))
        notes += money_notes(s, rows[order[0][1]], 'label') if order else []
        return head, bullets, notes, self.window_looks(data, ('speed', 'trend_speed'))

    def period_money(self, tools, o):
        """Where the money is over a window (get_opportunities): the levers' total a year, then each lever as the tool ranked it,
        marked controllable or schedule-driven. The supervisor and ownership lenses (issue #101) read the same rows their way."""
        data, rows = o['data'], o['data']['rows']
        if data.get('lens') == 'supervisor':
            return self.money_supervisor(o)
        if data.get('lens') == 'ownership':
            return self.money_ownership(o)
        if data.get('total_usd_per_year') is not None:
            head = f'**{money(data["total_usd_per_year"])} a year** across {len(rows)} levers{self.where(data)} · {money(data["total_usd"])} in this window {ref(o)}'
        else:
            head = f'**{money(data["total_usd"])}** across {len(rows)} levers in this window{self.where(data)} {ref(o)}'
        bullets = [bullet(f'{r["rank"]}. {r["lever"]}: **{money(r["usd_per_year"])}/yr**' if r.get('usd_per_year') is not None else
                          f'{r["rank"]}. {r["lever"]}: **{money(r["usd"])}** in the window', o, i) + ''
                   for i, r in enumerate(rows)]
        bullets = [b[:b.rindex(' (query')] + f' · {r["records"]} · {r["control"]}' + b[b.rindex(' (query'):] for b, r in zip(bullets, rows)]
        notes = [x for x in [contribution(data)] if x] + sentences(data.get('method_note')) + (money_notes(o, rows[0], 'lever') if rows else money_notes(o))
        return head, bullets, notes, self.window_looks(data, ('opportunity', 'pareto', 'waste'))

    @staticmethod
    def lever_line(o, i, r, numbered=True):
        figure = f'**{money(r["usd_per_year"])}/yr**' if r.get('usd_per_year') is not None else f'**{money(r["usd"])}** in the window'
        return bullet(f'{str(r["rank"]) + ". " if numbered and r.get("rank") else ""}{r["lever"]}: {figure} · {r["records"]} · {r["control"]}, {r["control_note"]}', o, i)

    def money_supervisor(self, o):
        """The supervisor lens: the controllable losses ranked by dollars a year; side trim and planned maintenance after, schedule-driven."""
        data, rows = o['data'], o['data']['rows']
        mine = [(i, r) for i, r in enumerate(rows) if r['control'] == 'controllable']
        head = (f'**{money(data["controllable_usd_per_year"])} a year** in controllable losses across {n(data["controllable_levers"])} levers{self.where(data)} · '
                f'{money(data["controllable_usd"])} in this window {ref(o)}' if data.get('controllable_usd_per_year') is not None else
                f'**{money(data["controllable_usd"])}** in controllable losses in this window{self.where(data)} {ref(o)}')
        bullets = [self.lever_line(o, i, r) for i, r in mine[:5]]
        rest = [(i, r) for i, r in enumerate(rows) if r['control'] != 'controllable']
        if rest:
            bullets += ['Schedule-driven, shown apart'] + [self.lever_line(o, i, r, numbered=False) for i, r in rest]
        notes = [x for x in [contribution(data)] if x] + sentences(data.get('method_note')) + (money_notes(o, mine[0][1], 'lever') if mine else money_notes(o))
        return head, bullets, notes, self.window_looks(data, ('pareto', 'waste', 'opportunity'))

    def money_ownership(self, o):
        """The ownership lens: the total cost of waste in dollars, side trim first (every owner asks what trim costs)."""
        data, rows = o['data'], o['data']['rows']
        trim = rows[0]
        figure = f'**{money(trim["usd_per_year"])} a year**' if trim.get('usd_per_year') is not None else f'**{money(trim["usd"])}**'
        head = (f'Side trim costs {figure}{self.where(data)} · {money(trim["usd"])} in this window · {pval(trim["pct_of_gross"])}% of gross'
                f'{band(trim.get("band_text"))} {ref(o, 0)}')
        bullets = [self.lever_line(o, i, r) for i, r in enumerate(rows) if i > 0]
        total = (f'**{money(data["total_usd_per_year"])} a year**' if data.get('total_usd_per_year') is not None else f'**{money(data["total_usd"])}**')
        bullets.append(bullet(f'All the waste recorded: {total} across {len(rows)} kinds · {money(data["total_usd"])} in this window', o))
        notes = [x for x in [contribution(data)] if x] + sentences(data.get('method_note')) + money_notes(o, trim, 'lever')
        return head, bullets, notes, self.window_looks(data, ('trend_trim', 'waste', 'grade'))

    def period_review(self, tools, r):
        """The owner's downtime answer (get_downtime_review, issue #101): maintenance and operator downtime against their
        bands, the reasons ranked by % of scheduled minutes with stops and minutes per stop, the flags with who to ask and
        how often the reason recurred, and planned maintenance last as scheduled time."""
        data, rows = r['data'], r['data']['rows']
        where = self.where(data)
        if not data['scheduled_minutes']:
            return f'No shifts in this window{where} {ref(r)}', [], [], self.window_looks(data, ('pareto',))
        head = (f'**{pval(data["op_maint_pct"])}%** of scheduled minutes in maintenance and operator downtime{where}{band(data["op_maint_band_text"])} · '
                f'maintenance {pval(data["maintenance_pct"])}%, {data["maintenance_band_text"]} · operator {pval(data["operator_pct"])}%, '
                f'{data["operator_band_text"]} {ref(r)}')
        # An incomplete window is listed in the tool's order, never ranked; the previous window's coverage does not
        # touch the ranking of this one (it is the comparison beside it, as in the Pareto).
        whole = data['window']['coverage'] == 'complete'
        bullets = []
        for i, x in [(i, x) for i, x in enumerate(rows) if not x['scheduled']][:5]:   # the top five; the chart and table hold all
            bullets.append(bullet(f'{str(x["rank"]) + ". " if whole else ""}{x["reason"]}, {x["kind"].lower()}: '
                                  f'**{pval(x["pct_of_scheduled"])}%** of scheduled minutes · {pval(x["minutes"])} min over {stops_text(x["stops"])}, '
                                  f'{pval(x["minutes_per_stop"])} min per stop' + (band(x['band_text']) if x['band'] in ('over target', 'flag') else '') +
                                  f' · {review_previous(x)}', r, i))
        flags = flag_lines(r, data['flags'])
        if flags:   # a flag pulls in the shift: who to ask, and how often the same reason crossed its band
            bullets = with_flags(bullets, flags)
        if data.get('previous_op_maint_pct') is not None:   # the previous window of the same kind, beside (the owner, #111)
            bullets += ['The previous window'] + [bullet(
                f'**{pval(data["previous_op_maint_pct"])}%** of its scheduled minutes in maintenance and operator downtime · '
                f'maintenance {pval(data["previous_maintenance_pct"])}% · operator {pval(data["previous_operator_pct"])}%', r)]
        planned = [(i, x) for i, x in enumerate(rows) if x['scheduled']]
        if planned:
            bullets += ['Scheduled time, not ranked'] + [
                bullet(f'{x["reason"]}: **{pval(x["minutes"])} min** over {stops_text(x["stops"])} · {pval(x["pct_of_scheduled"])}% of scheduled minutes', r, i)
                for i, x in planned]
        notes = sentences(data.get('method_note'))
        if data['flag_count'] > FIRST_FLAGS:
            notes.append(f'{n(data["flag_count"])} flags fired in this window, the largest first, each with its recurrence.')
        elif not data['flags']:
            notes.append('No shift is over its maintenance or operator band.')
        looks = []
        if data['flags'] and data['window'].get('period') == 'day' and self.context.period == 'day':
            f = data['flags'][0]
            looks.append(look(f'The reported notes on the stops of shift {f["shift"]} (synthetic, unverified)',
                              'Show the shift notes' + (f' for shift {f["shift"]}' if self.context.scope == 'day' else '')))
        looks += self.window_looks(data, ('pareto', 'stops', 'trend_downtime'))
        return head, bullets, notes, looks[:3]

    # speed variance (issue #92): what each bucket is called in the headline, and where its records are
    SPEEDVAR_WORDS = {'stops': 'stops', 'setups': 'setups', 'below_baseline': 'running under baseline', 'mix': 'mix'}
    SPEEDVAR_LOOKS = {'stops': 'pareto', 'setups': 'pareto', 'below_baseline': 'speed', 'mix': 'grade'}

    def period_speedvar(self, tools, v):
        """The speed gap and the share of each bucket (get_speed_variance), then the three largest buckets with the
        stop reason or grade that leads each. The shares are the envelope's; every bullet is one bucket row."""
        data, rows = v['data'], v['data']['rows']
        reference = {'baseline': 'baseline', 'previous': 'the previous window', 'shift': f'shift {data.get("against_shift")}'}[data['against']]
        pct = lambda x: f0(x) if abs(x) >= 9.95 else f1(x)   # "stops 45%", "mix 0.5%"
        shares = [f'{self.SPEEDVAR_WORDS[r["bucket"]]} {pct(data[r["bucket"] + "_share_pct"])}%' for r in rows
                  if data.get(r['bucket'] + '_share_pct') is not None]
        head = (f'Speed gap **{f1(abs(data["gap_fpm"]))} ft/min**{self.where(data)} {data["direction"]} {reference}'
                + (': ' + ', '.join(shares) if shares else '') + f' {ref(v)}')
        sign = -1 if data['gap_fpm'] < 0 else 1
        whole = self.whole(data)
        bullets = []
        for i, r in enumerate(rows[:3] if whole else rows):   # an incomplete window is listed in the tool's order, never ranked
            toward = r['gap_fpm'] * sign >= 0
            share = f' · {pct(r["share_of_gap_pct"])}%' if r.get('share_of_gap_pct') is not None else ''
            top = (f' · largest {r["top_item"]} at {f1(abs(r["top_item_fpm"]))} ft/min'
                   if whole and r.get('top_item') and r.get('top_item_fpm') is not None else '')
            # every bucket is marked controllable or schedule-driven (issue #101); the grade mix is the order book's, never priced
            control = (f' · schedule-driven, {r["control_note"]}' if r.get('control') == 'schedule-driven' else
                       ' · controllable' if r.get('control') else '')
            bullets.append(bullet(f'{r["label"]}: **{f1(abs(r["gap_fpm"]))} ft/min** {"of the gap" if toward else "the other way, an offset"}{share}'
                                  f' · {f0(abs(r["gap_lineal_ft"]))} lineal ft{top}{priced(r)}{control}', v, i))
        notes = sentences(data.get('reference_note')) + sentences(data.get('method_note'))
        # "an offset" pulls against the gap; what is priced is a controllable loss against the reference, whichever way the gap goes
        if data['direction'] == 'above' and 'controllable_usd' in data:
            subject = f'Shift {data["shift"]}' if data['against'] == 'shift' else 'This window'
            notes.append(f'{subject} is faster than {reference}, so the gap is a gain and is not priced; the controllable buckets that ran '
                         'worse are priced as machine time.')
        elif data.get('controllable_usd') is not None:
            notes.append(f'The controllable buckets are {money(data["controllable_usd"])} of machine time' +
                         (f' ({money(data["controllable_usd_per_year"])}/yr at the post\'s volumes)' if data.get('controllable_usd_per_year') is not None else '') +
                         '; the grade mix is schedule-driven and not priced, nor is a bucket that ran better than the reference.')
        if data.get('contribution_status') == MARGIN_NOT_SET:
            notes.append(contribution(data))
        elif data.get('controllable_contribution_usd') is not None:
            notes.append(f'Contribution {money(data["controllable_contribution_usd"])} for the controllable buckets at the set margin, '
                         'not added to the machine time.')
        priced_rows = [r for r in rows if r.get('usd') is not None]
        notes += money_notes(v, priced_rows[0], 'label') if priced_rows else money_notes(v)
        kinds = list(dict.fromkeys(self.SPEEDVAR_LOOKS[r['bucket']] for r in rows if r['gap_fpm'] * sign > 0)) + ['opportunity']
        return head, bullets, notes, self.window_looks(data, list(dict.fromkeys(kinds)))

    def window_looks(self, data, kinds):
        """Where-to-look lines over the same window (or the trends that end on the selected day)."""
        wp, sfx = self.wp(data), self.sfx(data)
        lines = []
        for kind in kinds:
            if kind.startswith('trend'):
                metric = {'trend': 'speed', 'trend_downtime': 'downtime', 'trend_dry_end': 'dry-end rejects', 'trend_stops': 'stops', 'trend_trim': 'trim',
                          'trend_volume': 'MSF per day', 'trend_speed': 'running speed'}[kind]
                lines.append(look(f'The weekly trend of {metric}', f'Trend of {metric} over eight weeks{sfx}'))
                continue
            if wp is None:
                continue
            text, question = {'pareto': ('The downtime minutes by reason', f'Which downtime reason cost the most minutes{wp}{sfx}?'),
                              'waste': ('Side trim, shear and each dry-end reason', f'Waste breakdown{wp}{sfx}'),
                              'grade': ('Volume, speed and waste by grade', f'Volume by grade{wp}{sfx}'),
                              'stops': ('The stops of each production day', f'Stops per day{wp}{sfx}'),
                              'speed': ('Each grade against its speed target', f'Which grade is slowest against target{wp}{sfx}?'),
                              'opportunity': ('Each grade and web width against its best-fifth speed', f'Where is the speed opportunity{wp}{sfx}?'),
                              'rollstock': ('The paper each grade and width consumed', f'How much paper did we consume{wp}{sfx}?')}[kind]
            lines.append(look(text, question))
        return lines[:3]
