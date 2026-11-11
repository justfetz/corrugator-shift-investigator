"""Issue #79: an ordering the narrative asserts is checked against the values it cites.

"Shift 2 ran fastest at 562.75 ft/min, against 608.92 ft/min on Shift 1 and 592.11 ft/min on Shift 3"
passed every placeholder check, because every digit came from a tool row. The claim about the digits
was false. The shift tests run on the real rows of 2026-08-26 (All shifts), the day of the report,
frozen as the evidence packet of that day in the dataset the report was made on
(`narrative_packet_2026-08-26.json`): the orderings the lines assert are the report's, and issue
#35's regeneration of the days must not move them. The period tests run on small rows of the period
tools' shapes; the answer-level tests at the end run on the live store's day."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from investigator.agent import Agent, Context
from investigator.narrative import COMPARISON_WITHDRAWN, NarrativeStream, evidence_packet, render_narrative
from investigator.record_store import default_store

DAY = '2026-08-26'
LINE = COMPARISON_WITHDRAWN + '.'
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def analysis():
    value = default_store().day_analysis(DAY)
    yield value
    value.close()


def build_packet(analysis, store):
    """r1-r3: get_shift_kpis for shifts 1, 2, 3; r4-r6: each shift's downtime by reason and r7-r9 its rejects by
    reason (largest first); r10-r13: September's KPI tiles, downtime Pareto, speed opportunity and waste breakdown."""
    evidence = []

    def add(tool, data):
        evidence.append({'result_id': f'run:{len(evidence) + 1}', 'tool': tool, 'arguments': {}, 'ok': True, 'data': data})
    for tool in ('get_shift_kpis', 'get_downtime_breakdown', 'get_quality_breakdown'):
        for s in (1, 2, 3):
            add(tool, getattr(analysis, tool)(s))
    september = {'period': 'month', 'anchor': '2026-09-15'}
    for tool in ('get_period_kpis', 'get_downtime_pareto', 'get_speed_opportunity', 'get_waste_breakdown'):
        add(tool, getattr(store, tool)(window=september))
    return evidence_packet(evidence)


@pytest.fixture(scope='module')
def packet():
    """The report day's packet (build_packet over dataset d9143ff1d150, frozen when issue #35 regenerated the days)."""
    return json.loads((ROOT / 'tests' / 'narrative_packet_2026-08-26.json').read_text())


# Fields issue #101 added to these tools' rows, which the frozen day (dataset d9143ff1d150, no longer generated since
# issue #35) never carried: the owner's bands, the yield after trim and shear, maintenance and operator together, and each
# downtime reason's stop count, minutes per stop and whether it is scheduled time. The frozen rows' own values stay as
# they were; only these may be missing from them.
ADDED_BY_101 = {'get_shift_kpis': {'dry_end_band', 'dry_end_band_text', 'maintenance_band', 'maintenance_band_text', 'op_maint_band',
                                   'op_maint_band_text', 'op_maint_pct', 'operator_band', 'operator_band_text', 'shear_band',
                                   'shear_band_text', 'trim_band', 'trim_band_text', 'yield_sqft'},
                'get_downtime_breakdown': {'minutes_per_stop', 'scheduled', 'stops'},
                'get_waste_breakdown': {'band', 'band_text'}}


def test_the_frozen_packet_has_the_live_packet_shape(analysis):
    """The frozen rows stay what build_packet makes today: the same tools, row fields and coverage (but the fields
    issue #101 added, which the frozen day cannot carry)."""
    live, frozen = build_packet(analysis, default_store()), json.loads((ROOT / 'tests' / 'narrative_packet_2026-08-26.json').read_text())
    assert [(p['result_id'], p['tool'], p['coverage']) for p in live] == [(p['result_id'], p['tool'], p['coverage']) for p in frozen]
    for a, b in zip(live, frozen):
        assert set(a) == set(b) and a['rows'] and b['rows'], a['tool']
        assert set(b['rows'][0]) <= set(a['rows'][0]) and set(a['rows'][0]) - set(b['rows'][0]) <= ADDED_BY_101.get(a['tool'], set()), a['tool']


def stream(packet, *lines, chunk=7):
    events = []
    parser = NarrativeStream(packet, events.append)
    text = '\n'.join(lines) + '\n'
    for i in range(0, len(text), chunk):
        parser.feed(text[i:i + chunk])
    return parser, events


def kept(packet, line):
    """True when the line survives the stream validator whole."""
    parser, _ = stream(packet, line, 'limitation: One production day.')
    narrative = parser.finish()
    if any(p['text'] == LINE for p in narrative):
        return False
    assert narrative[0]['kind'] != 'limitation', narrative   # survived, not omitted for some other rule
    return True


def test_the_reported_exchange_is_withdrawn_counted_and_disclosed(packet):
    fpm = [r['rows'][0]['observed_shift_fpm'] for r in packet[:3]]
    assert fpm[1] < min(fpm[0], fpm[2])     # the data the report was about: Shift 2 is the slowest of the three
    parser, events = stream(packet,
                            'finding: Shift 2 ran fastest at {{r2.0.observed_shift_fpm}} ft/min including downtime, against '
                            '{{r1.0.observed_shift_fpm}} ft/min on Shift 1 and {{r3.0.observed_shift_fpm}} ft/min on Shift 3.',
                            'check: Ask the Shift 1 crew how {{r4.0.category}} at {{r4.0.down_minutes}} minutes was handled.')
    shown = ''.join(e['text'] for e in events if e['type'] == 'delta' and e['paragraph'] == 0)
    assert shown.startswith('Shift 2 ran fastest at ')      # it streamed while the line was written ...
    assert [e for e in events if e['type'] == 'retract'] == [{'type': 'retract', 'paragraph': 0, 'reason': COMPARISON_WITHDRAWN}]   # ... and was withdrawn at its end
    assert not any(e['type'] == 'paragraph' and e['paragraph'] == 0 for e in events)
    narrative = parser.finish()
    assert [p['kind'] for p in narrative] == ['check', 'limitation']
    assert narrative[1] == {'kind': 'limitation', 'text': LINE, 'citations': [], 'withdrawn': 1}
    assert not any('fastest' in p['text'] for p in narrative)


def test_the_corrected_narrative_is_kept(packet):
    parser, events = stream(packet, 'finding: Shift 1 ran fastest at {{r1.0.observed_shift_fpm}} ft/min including downtime, against '
                                    '{{r2.0.observed_shift_fpm}} ft/min on Shift 2 and {{r3.0.observed_shift_fpm}} ft/min on Shift 3.')
    narrative = parser.finish()
    assert len(narrative) == 1 and narrative[0]['text'].startswith('Shift 1 ran fastest at ')
    assert not any(e['type'] == 'retract' for e in events)


FPM = '{{r%d.0.observed_shift_fpm}}'
DOWN = '{{r%d.0.downtime_pct}}'


@pytest.mark.parametrize('line,ok', [
    # superlatives: the subject named before the word, after it, or by the value placed beside it
    (f'finding: At {FPM % 1} ft/min, Shift 1 was the fastest, against {FPM % 2} and {FPM % 3}.', True),
    (f'finding: At {FPM % 2} ft/min, Shift 2 was the fastest, against {FPM % 1} and {FPM % 3}.', False),
    (f'finding: The fastest shift was Shift 1 at {FPM % 1} ft/min; Shift 2 ran {FPM % 2} and Shift 3 ran {FPM % 3}.', True),
    (f'finding: Shift 1 ran {FPM % 1} and Shift 2 ran {FPM % 2}, so the fastest was Shift 2.', False),
    (f'finding: Shift 1 ran {FPM % 1} and Shift 2 ran {FPM % 2}, so the fastest was Shift 1.', True),
    (f'finding: Shift 2 was the slowest at {FPM % 2} ft/min, against {FPM % 1} on Shift 1 and {FPM % 3} on Shift 3.', True),
    (f'finding: Shift 3 was the slowest at {FPM % 3} ft/min, against {FPM % 1} on Shift 1 and {FPM % 2} on Shift 2.', False),
    (f'finding: Shift 3 posted {FPM % 3} ft/min, the fastest of the three, against {FPM % 1} on Shift 1.', False),
    (f'finding: Speed was highest on Shift 1 at {FPM % 1} ft/min, against {FPM % 2} on Shift 2.', True),
    (f'finding: Speed was highest on Shift 2 at {FPM % 2} ft/min, against {FPM % 1} on Shift 1.', False),
    # the same rule for other quantities; best and worst follow the field's better side
    (f'finding: Shift 1 had the most downtime at {DOWN % 1} percent, against {DOWN % 2} on Shift 2.', True),
    (f'finding: Shift 2 had the most downtime at {DOWN % 2} percent, against {DOWN % 1} on Shift 1.', False),
    (f'finding: Shift 1 had the worst downtime share at {DOWN % 1} percent, against {DOWN % 3} on Shift 3.', True),
    (f'finding: Shift 1 had the best downtime share at {DOWN % 1} percent, against {DOWN % 3} on Shift 3.', False),
    # a ranking named in words or by a placeholder of the row's own words
    ('finding: Glue / starch was the largest loss at {{r4.0.down_minutes}} minutes, ahead of Order change at {{r4.1.down_minutes}}.', True),
    ('finding: Order change was the largest loss at {{r4.1.down_minutes}} minutes, against Glue / starch at {{r4.0.down_minutes}}.', False),
    ('finding: {{r4.1.category}} cost the most minutes, {{r4.1.down_minutes}}, against {{r4.0.category}} at {{r4.0.down_minutes}}.', False),
    # comparisons between the two placed values around the word
    (f'finding: Shift 2 ran at {FPM % 2} ft/min, below {FPM % 1} on Shift 1.', True),
    (f'finding: Shift 2 ran at {FPM % 2} ft/min, above {FPM % 1} on Shift 1.', False),
    (f'finding: Shift 2 at {FPM % 2} ft/min was faster than Shift 1 at {FPM % 1}.', False),
    (f'finding: Shift 1 lost {DOWN % 1} percent to stops, more than {DOWN % 2} on Shift 2.', True),
    (f'finding: Shift 3 was the second-fastest at {FPM % 3}, behind Shift 1 at {FPM % 1}.', True),
    (f'finding: Shift 3 was the second-fastest at {FPM % 3}, ahead of Shift 1 at {FPM % 1}.', False),
])
def test_orderings_are_checked_against_the_cited_values(packet, line, ok):
    assert kept(packet, line) is ok


@pytest.mark.parametrize('line', [
    f'finding: Shift 2 ran fastest at {FPM % 2} ft/min including downtime.',                          # one value: nothing to order
    f'finding: Shift 2 was not the fastest, at {FPM % 2} against {FPM % 1} on Shift 1.',               # negated
    f'check: Was Shift 2 the fastest at {FPM % 2} against {FPM % 1} on Shift 1? Ask the crews.',       # a question
    f'finding: Shift 2 had the most stops; it ran {FPM % 2} ft/min against {FPM % 1} on Shift 1.',     # "most stops" is not the speed
    f'finding: Shift 2 ran at {FPM % 2} ft/min, below its grade target, while Shift 1 ran {FPM % 1}.', # "below" is the target, not Shift 1
    f'hypothesis: Uncertain: the most likely reading is mix, with {FPM % 2} on Shift 2 against {FPM % 1} on Shift 1.',
    f'finding: Most of the shift ran near {FPM % 2} ft/min on Shift 2 against {FPM % 1} on Shift 1.',
    f'finding: Shift 2 ran {FPM % 2} ft/min and Shift 1 ran {FPM % 1}; at least the speeds are recorded.',
    'finding: Shift 1 lost {{r1.0.downtime_pct}} percent to stops, the largest share, while running {{r2.0.observed_shift_fpm}} on Shift 2 '
    'against {{r1.0.observed_shift_fpm}} on Shift 1.',                                                 # two quantities, no value beside the word
])
def test_claims_the_rules_cannot_pin_down_are_left_alone(packet, line):
    assert kept(packet, line)


@pytest.mark.parametrize('line', [
    # each shift's own table ranks its own reasons: "largest" is not one shift's top reason against another's
    'finding: On Shift 2 the largest stop reason was Knife / slitter jam at {{r5.0.down_minutes}} minutes; on Shift 1 it was Glue / starch at {{r4.0.down_minutes}} minutes.',
    "finding: Shift 2's largest stop was {{r5.0.category}} at {{r5.0.down_minutes}} minutes, against {{r4.0.category}} at {{r4.0.down_minutes}} minutes on Shift 1.",
    'finding: The largest reject reason on Shift 1 was BOND at {{r7.0.rejected_sqft}} square feet; on Shift 2 it was WARP at {{r8.0.rejected_sqft}} square feet.',
    'finding: Shift 3 lost the most time to Raw material (roll), {{r6.0.down_minutes}} minutes, while Shift 1 lost {{r4.0.down_minutes}} minutes to Glue / starch.',
    "finding: Shift 1's longest stop reason was Glue / starch at {{r4.0.down_minutes}} minutes, while Shift 2's largest was Knife / slitter jam at {{r5.0.down_minutes}} minutes.",
    'finding: Glue / starch was the largest stop reason on Shift 1 at {{r4.0.down_minutes}} minutes, ahead of Order change at {{r4.1.down_minutes}} and Stacker at {{r4.2.down_minutes}}.',
    # "fastest" is said of the ft/min beside it, not of a percent-of-target pair later in the sentence
    f'finding: Shift 1 ran fastest at {FPM % 1} ft/min even though Shift 2 ran closer to target, {{{{r2.0.speed_to_target_pct}}}} percent against {{{{r1.0.speed_to_target_pct}}}}.',
    f"finding: Shift 1 ran fastest at {FPM % 1} ft/min even though its speed to target, {{{{r1.0.speed_to_target_pct}}}} percent, was below Shift 2's {{{{r2.0.speed_to_target_pct}}}}.",
    f'finding: Shift 1 ran fastest, at {FPM % 1} ft/min, though at {{{{r1.0.speed_to_target_pct}}}} percent of target it trailed Shift 2 at {{{{r2.0.speed_to_target_pct}}}}.',
    f'finding: Shift 2 had the highest speed to target at {{{{r2.0.speed_to_target_pct}}}} percent against {{{{r1.0.speed_to_target_pct}}}} on Shift 1, yet the lowest observed speed at {FPM % 2} against {FPM % 1}.',
    f'finding: Shift 1 posted the fastest speed at {FPM % 1} ft/min but the highest downtime at {DOWN % 1} percent, against {FPM % 2} ft/min and {DOWN % 2} percent on Shift 2.',
    f'finding: Shift 1 at {FPM % 1} and Shift 3 at {FPM % 3} both ran faster than Shift 2 at {FPM % 2}.',
    # "down" meaning stopped and "made up" are not directions; "the best fifth" is a field, not a superlative
    'finding: The corrugator was down {{r11.1.minutes}} minutes for Missed splice, against {{r11.1.previous_minutes}} in the previous month.',
    'finding: Order change made up {{r11.3.minutes}} minutes of stops, against {{r11.3.previous_minutes}} in the previous month.',
    'finding: On {{r12.0.label}} the best fifth of setups ran {{r12.0.best_fifth_fpm}} ft/min against a typical {{r12.0.typical_fpm}}; on {{r12.1.label}} the best fifth ran {{r12.1.best_fifth_fpm}} ft/min.',
    'finding: Missed splice was the largest reason after Planned maintenance ({{r11.0.minutes}} minutes), at {{r11.1.minutes}} minutes against {{r11.3.minutes}} for Order change.',
    # true directions on the real September tables
    'finding: Stops went up to {{r10.7.current}} from {{r10.7.previous}}, even though downtime minutes went down to {{r10.6.current}} from {{r10.6.previous}}.',
    'finding: Side trim rose to {{r13.0.pct_of_gross}} percent of gross from {{r13.0.previous_pct_of_gross}}, the largest of the waste categories at {{r13.0.sqft}} square feet against {{r13.1.sqft}} for shear.',
])
def test_true_orderings_from_review_are_kept(packet, line):
    """Each line is true on these rows; the first version of the check withdrew most of them."""
    assert kept(packet, line)


@pytest.mark.parametrize('line', [
    f'finding: Shift 2 ran faster than Shift 1, {FPM % 2} ft/min against {FPM % 1} ft/min.',          # names in words, values after a comma
    f'finding: Shift 2 ran faster than Shift 1 at {FPM % 1} ft/min, posting {FPM % 2} ft/min.',
    f'finding: Shift 1 lost less time to stops than Shift 2, {DOWN % 1} percent against {DOWN % 2} percent.',
    f'finding: Shift 1 had better downtime than Shift 2, {DOWN % 1} percent against {DOWN % 2}.',
    f'finding: Shift 2 at {FPM % 2} ft/min outpaced Shift 1 at {FPM % 1} and Shift 3 at {FPM % 3}.',
    f'finding: The fastest shift was Shift 2, at {FPM % 2} ft/min against {FPM % 1} on Shift 1 and {FPM % 3} on Shift 3.',
    f'finding: Shift 2 posted the highest speed, {FPM % 2} ft/min including downtime, against {FPM % 1} ft/min on Shift 1.',
    f'finding: Shift 2 achieved the best speed, {FPM % 2} ft/min, versus {FPM % 1} for Shift 1.',
    f'finding: Including downtime, Shift 2 ran fastest at {FPM % 2} ft/min (Shift 1 {FPM % 1}, Shift 3 {FPM % 3}).',
    f'finding: Shift 2 ran the fastest. It averaged {FPM % 2} ft/min against {FPM % 1} on Shift 1 and {FPM % 3} on Shift 3.',
    'finding: Stacker was the largest stop reason on Shift 1 at {{r4.2.down_minutes}} minutes, ahead of Glue / starch at {{r4.0.down_minutes}}.',
])
def test_false_orderings_from_review_are_withdrawn(packet, line):
    assert not kept(packet, line)


PERIOD = [
    {'result_id': 'p1', 'tool': 'get_downtime_pareto', 'shift': None, 'coverage': 'complete', 'total_rows': 2, 'preview_rows': 2,
     'rows': [{'category': 'Missed splice', 'stops': 128, 'minutes': 196.5, 'previous_stops': 81, 'previous_minutes': 124.65},
              {'category': 'Stacker', 'stops': 40, 'minutes': 60.0, 'previous_stops': 52, 'previous_minutes': 75.0}]},
    {'result_id': 'p2', 'tool': 'get_waste_breakdown', 'shift': None, 'coverage': 'complete', 'total_rows': 2, 'preview_rows': 2,
     'rows': [{'category': 'trim', 'label': 'Side trim', 'sqft': 5172189.4, 'pct_of_gross': 3.2274, 'previous_pct_of_gross': 2.8669, 'delta_pct_points': 0.3605},
              {'category': 'warp', 'label': 'Warp', 'sqft': 400.0, 'pct_of_gross': 0.21, 'previous_pct_of_gross': 0.35, 'delta_pct_points': -0.14}]},
    {'result_id': 'p3', 'tool': 'get_period_kpis', 'shift': None, 'coverage': 'complete', 'total_rows': 1, 'preview_rows': 1,
     'rows': [{'metric': 'stops', 'label': 'Stops', 'unit': 'stops', 'current': 300, 'previous': 340, 'delta': -40, 'better': 'lower', 'change': 'better'}]},
]


@pytest.mark.parametrize('line,ok', [
    ('finding: Missed splice minutes rose to {{r1.0.minutes}} from {{r1.0.previous_minutes}}.', True),
    ('finding: Missed splice minutes fell to {{r1.0.minutes}} from {{r1.0.previous_minutes}}.', False),
    ('finding: Missed splice logged {{r1.0.minutes}} minutes, up from {{r1.0.previous_minutes}} in the previous window.', True),
    ('finding: Stacker logged {{r1.1.minutes}} minutes, up from {{r1.1.previous_minutes}} in the previous window.', False),
    ('finding: Stacker stops fell from {{r1.1.previous_stops}} to {{r1.1.stops}}.', True),
    ('finding: Side trim rose by {{r2.0.delta_pct_points}} points of gross.', True),
    ('finding: Warp rose by {{r2.1.delta_pct_points}} points of gross.', False),
    ('finding: Warp moved by {{r2.1.delta_pct_points}} points of gross, which is a decrease.', True),
    ('finding: Side trim moved by {{r2.0.delta_pct_points}} points of gross, which is a decrease.', False),
    ('finding: Stops went down to {{r3.0.current}} from {{r3.0.previous}}, a change of {{r3.0.delta}}.', True),
    ('finding: Stops increased to {{r3.0.current}} from {{r3.0.previous}}.', False),
    ('finding: Stops were better than the previous window, {{r3.0.current}} against {{r3.0.previous}}.', True),   # "better than" has no value right after it
    ('finding: Stops did not rise: {{r3.0.current}} against {{r3.0.previous}} before.', True),                    # negated
    ('finding: Missed splice was the largest reason at {{r1.0.minutes}} minutes, against {{r1.1.minutes}} for Stacker.', True),
    ('finding: Stacker was the largest reason at {{r1.1.minutes}} minutes, against {{r1.0.minutes}} for Missed splice.', False),
])
def test_directions_against_the_previous_window_and_deltas(line, ok):
    assert kept(PERIOD, line) is ok


TIES = [
    {'result_id': 'p1', 'tool': 'get_downtime_pareto', 'shift': None, 'coverage': 'complete', 'total_rows': 2, 'preview_rows': 2,
     'rows': [{'category': 'Missed splice', 'stops': 40, 'minutes': 60.0, 'previous_stops': 40, 'previous_minutes': 60.0},
              {'category': 'Stacker', 'stops': 40, 'minutes': 60.0, 'previous_stops': 52, 'previous_minutes': 75.0}]},
    {'result_id': 'p2', 'tool': 'get_period_kpis', 'shift': None, 'coverage': 'complete', 'total_rows': 1, 'preview_rows': 1,
     'rows': [{'metric': 'stops', 'label': 'Stops', 'unit': 'stops', 'current': 300, 'previous': 300, 'delta': 0, 'better': 'lower', 'change': 'same'}]},
]


@pytest.mark.parametrize('line', [
    'finding: Missed splice at {{r1.0.minutes}} minutes ran above {{r1.1.minutes}} for Stacker.',          # equal: not above
    'finding: Missed splice logged {{r1.0.minutes}} minutes, more than {{r1.1.minutes}} for Stacker.',
    'finding: Missed splice minutes rose to {{r1.0.minutes}} from {{r1.0.previous_minutes}}.',             # equal: neither rose
    'finding: Missed splice minutes fell to {{r1.0.minutes}} from {{r1.0.previous_minutes}}.',             # nor fell
    'finding: Stops increased to {{r2.0.current}} from {{r2.0.previous}}.',
    'finding: Stops rose by {{r2.0.delta}}.',                                                              # a zero delta
    'finding: Stops fell by {{r2.0.delta}}.',
])
def test_strict_words_over_equal_values_are_withdrawn(line):
    assert not kept(TIES, line)


def test_a_negation_in_the_clause_before_does_not_excuse_the_ordering(packet):
    # "not" belongs to "did not meet target"; "fastest" after "but" is still checked (Shift 2 is the slowest)
    assert not kept(packet, f'finding: Shift 2 did not meet target but ran fastest at {FPM % 2} ft/min, against {FPM % 1} on Shift 1.')
    assert kept(packet, f'finding: Shift 2 did not run fastest at {FPM % 2} ft/min, against {FPM % 1} on Shift 1.')


def test_equal_values_without_an_ordering_word_are_kept():
    assert kept(TIES, 'finding: Missed splice logged {{r1.0.minutes}} minutes, the same as {{r1.1.minutes}} for Stacker.')
    assert kept(TIES, 'finding: Stops held at {{r2.0.current}} against {{r2.0.previous}} in the previous window.')


def test_the_structured_writer_is_checked_the_same_way(packet):
    value = {'paragraphs': [
        {'kind': 'finding', 'text': 'Shift 2 ran fastest at {{0}} ft/min, against {{1}} on Shift 1.',
         'facts': [{'result_id': 'run:2', 'row': 0, 'field': 'observed_shift_fpm'}, {'result_id': 'run:1', 'row': 0, 'field': 'observed_shift_fpm'}]},
        {'kind': 'limitation', 'text': 'One production day.', 'facts': []}]}
    out = render_narrative(value, packet)
    assert [p['text'] for p in out] == ['One production day.', LINE]
    value['paragraphs'].pop()
    with pytest.raises(ValueError, match=COMPARISON_WITHDRAWN) as raised:
        render_narrative(value, packet)
    assert raised.value.withdrawn == 1


class _Analyst:
    """A planned round with a fixed plan and fixed narrative lines, no model."""
    planned = True
    mode = 'test analyst; no language model'

    def __init__(self, lines):
        self.lines = lines

    def plan(self, context):
        return {'steps': [{'tool': 'get_shift_kpis', 'arguments': {'shift': s}} for s in (1, 2, 3)]}

    def narrate(self, question, evidence, emit=None):
        parser = NarrativeStream(evidence_packet(evidence), emit)
        parser.feed('\n'.join(self.lines) + '\n')
        return parser.finish()


def ask(analysis, *lines):
    context = Context(DAY, 2)
    context.scope = 'day'
    return Agent(analysis, planner=_Analyst(lines)).ask_planned('What shift was fastest and why?', context)


BAD = 'finding: Shift 2 ran fastest at {{r2.0.observed_shift_fpm}} ft/min, against {{r1.0.observed_shift_fpm}} on Shift 1.'


def test_the_answer_counts_withdrawn_comparisons_in_usage(analysis):
    answer = ask(analysis, BAD, 'limitation: One production day.')
    assert answer['narrative_status'] == 'available' and answer['usage']['comparisons_withdrawn'] == 1
    assert [p['text'] for p in answer['narrative']] == ['One production day.', LINE]
    assert all(set(p) == {'kind', 'text', 'citations', 'charts'} for p in answer['narrative'])   # the count moved into usage
    assert any(s.startswith('Limitation: ' + LINE) for s in answer['sections'])
    # nothing survived: the narrative is unavailable, the reason is the same sentence, and it is still counted
    answer = ask(analysis, BAD)
    assert answer['narrative_status'] == 'unavailable' and answer['usage']['comparisons_withdrawn'] == 1
    assert answer['sections'][0] == f'The model narrative failed validation ({COMPARISON_WITHDRAWN}). Calculated evidence and charts are retained.'
    # a clean narrative counts zero; an offline answer has no narrative and no count
    assert ask(analysis, 'finding: Shift 1 ran fastest at {{r1.0.observed_shift_fpm}} ft/min.')['usage']['comparisons_withdrawn'] == 0
    context = Context(DAY, 2)
    context.scope = 'day'
    assert 'comparisons_withdrawn' not in Agent(analysis).ask('What shift was fastest and why?', context)['usage']


def _runner():
    spec = importlib.util.spec_from_file_location('golden_runner_79', ROOT / 'evals' / 'run.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules['golden_runner_79'] = module
    spec.loader.exec_module(module)
    return module


def test_the_run_summary_counts_comparisons_withdrawn_and_the_live_variant_runs_on_demand(tmp_path):
    runner = _runner()
    runner.FIXTURES = tmp_path      # whether the owner has recorded this case yet (#71) does not matter here
    rows = [{'provider': None, 'comparisons_withdrawn': 1}, {'provider': None, 'comparisons_withdrawn': None},
            {'provider': 'anthropic', 'called': True, 'comparisons_withdrawn': 0},
            {'provider': 'anthropic', 'called': True, 'comparisons_withdrawn': 2},
            {'provider': 'anthropic', 'comparisons_withdrawn': 0}]
    assert runner.comparisons_withdrawn(rows) == {'offline': (1, 1), 'live anthropic': (2, 2), 'replayed anthropic': (0, 1)}
    case = next(c for c in runner.load_cases() if c['id'] == 'analyst-fastest-shift-contradicted')
    assert case.get('live') and case.get('plan')
    assert runner.plan_rows([case], False, None) == [(case, None)]                          # no skipped @live row offline
    assert runner.plan_rows([case], True, 'anthropic') == [(case, None), (case, 'anthropic')]   # --live asks the real analyst


def test_both_narrative_prompts_ask_for_orderings_computed_from_the_cited_values():
    from investigator.anthropic_provider import NARRATIVE_STREAM_INSTRUCTIONS
    from investigator.openai_provider import NARRATIVE_INSTRUCTIONS
    for prompt in (NARRATIVE_STREAM_INSTRUCTIONS, NARRATIVE_INSTRUCTIONS):
        assert 'compare those values before you write it' in prompt
        assert 'maximum (or minimum)' in prompt and 'contradicts its own cited values is removed' in prompt
