"""The plant's targets and flag bands (issue #101): the owner's standards of 2026-11-10, in `targets.json`.

A band places a figure against its standard: home run (at or under the home-run line), on target (at or
under the target), over target (above the target, at or under the flag) or flag (above the flag: "what's
going on?"). Downtime has no gap between target and flag, so over 2.5% is a flag. The tools put the band on
the row beside the figure, as a word (`*_band`) and as the words an answer prints (`*_band_text`, which
names the thresholds), so a band is tool output like any other value, never typed into the prose.
"""
import json
from pathlib import Path

DEFINITION = json.loads(Path(__file__).with_name('targets.json').read_text())
BANDS = {b['id']: b for b in DEFINITION['bands']}
SOURCE = DEFINITION['source']
HOME_RUN, ON_TARGET, OVER_TARGET, FLAG = 'home run', 'on target', 'over target', 'flag'
TRIM_FLAG_PCT = BANDS['trim']['flag']          # the per-setup trim flag the KPI tools count setups above
SHIFT_MINUTES = 480


def pct(value):
    return f'{value:g}%'


def band(measure, value):
    """The band of `value` (a percentage in the measure's unit) against the measure's standard; None for no value."""
    if value is None:
        return None
    spec = BANDS[measure]
    home_run, target, flag = spec['home_run'], spec['target'], spec['flag']
    if home_run is not None and value <= home_run:
        return HOME_RUN
    if value <= target:
        return ON_TARGET
    if flag is not None and value > flag:
        return FLAG
    return OVER_TARGET


def band_text(measure, word):
    """The words an answer prints beside the figure: the band and the thresholds it was read against."""
    if word is None:
        return None
    spec = BANDS[measure]
    home_run, target, flag = spec['home_run'], spec['target'], spec['flag']
    if word == HOME_RUN:
        return f'home run ({pct(home_run)} or under)'
    if word == ON_TARGET:
        return f'on target ({pct(target)} or under)'
    if word == OVER_TARGET:
        return f'over the {pct(target)} target' + (f', under the {pct(flag)} flag' if flag is not None else '')
    return f'flag: over {pct(flag)}' + (f', the target is {pct(target)}' if target != flag else '')


def annotate(row, fields):
    """Add `<prefix>_band` and `<prefix>_band_text` to a row for each {value field: (measure, prefix)}."""
    for field, (measure, prefix) in fields.items():
        word = band(measure, row.get(field))
        row[f'{prefix}_band'] = word
        row[f'{prefix}_band_text'] = band_text(measure, word)
    return row


def mark(row, measure, value):
    """`band` and `band_text` on a row that holds one measure (a KPI tile, a waste category, a trend week)."""
    word = band(measure, value)
    row['band'] = word
    row['band_text'] = band_text(measure, word)
    return row


def is_flag(measure, value):
    return band(measure, value) == FLAG


def threshold_minutes(measure, scheduled_minutes=SHIFT_MINUTES):
    """The minutes at which a downtime measure crosses its flag over `scheduled_minutes` (12 of a 480-minute shift)."""
    return BANDS[measure]['flag'] / 100 * scheduled_minutes


def prompt_text():
    """The bands as the analyst's process context states them (agent.PROCESS_CONTEXT), read from targets.json so
    the prompt never carries a threshold of its own."""
    m, o, both = BANDS['maintenance'], BANDS['operator'], BANDS['op_maint']
    downtime = (f'Maintenance and Operator downtime targets are separately {pct(m["target"])} of scheduled minutes'
                if m['target'] == o['target'] else
                f'Maintenance downtime targets {pct(m["target"])} and Operator {pct(o["target"])} of scheduled minutes')
    of = {'% of gross': 'of gross', '% of yield': 'of the yield after trim and shear'}

    def three(spec):
        return '/'.join(f'{spec[k]:g}' for k in ('home_run', 'target', 'flag')) + f'% {of.get(spec["unit"], spec["unit"])}'
    return (f'{downtime} ({pct(both["target"])} together); over is a flag.\n'
            f'Owner bands (home run / target / flag): trim {three(BANDS["trim"])}; shear {three(BANDS["shear"])};\n'
            f'dry-end waste {three(BANDS["dry_end"])}. Planned maintenance is scheduled time.')


# The KPI tile (record_store.KPI_CATALOG) each band reads, for the period tools' one-measure rows.
KPI_BANDS = {'trim_pct': 'trim', 'shear_pct': 'shear', 'dry_end_pct': 'dry_end', 'operator_pct': 'operator'}
# The daily KPI row's fields (analysis.get_shift_kpis): value field -> (band, prefix of the band fields).
SHIFT_KPI_BANDS = {'maintenance_pct': ('maintenance', 'maintenance'), 'operator_pct': ('operator', 'operator'),
                   'op_maint_pct': ('op_maint', 'op_maint'), 'trim_pct': ('trim', 'trim'), 'shear_pct': ('shear', 'shear'),
                   'dry_end_pct': ('dry_end', 'dry_end')}
