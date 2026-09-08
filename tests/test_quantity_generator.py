"""Independent audits of quantities, geometry and time across the demo month."""
from datetime import datetime
import xml.etree.ElementTree as ET
import pytest
from investigator.fixture import generate
from investigator.analysis import Analysis


@pytest.mark.parametrize('seed', range(7,37))
def test_quantity_geometry_and_clock(seed):
    root=ET.fromstring(generate(seed))
    ledger={}; durations=set(); singles=0; continuations=0
    for shift in root:
        cursor=datetime.fromisoformat(shift.get('start'))
        stops=shift.findall('stop')
        for setup in shift.findall('setup'):
            start,end=(datetime.fromisoformat(setup.get(k)) for k in ('start','end'))
            assert start==cursor
            elapsed=(end-start).total_seconds(); durations.add(round(elapsed,2))
            down=sum(max(0,(min(end,datetime.fromisoformat(s.get('end')))-max(start,datetime.fromisoformat(s.get('start')))).total_seconds()) for s in stops)
            assert elapsed==pytest.approx(float(setup.get('running_seconds'))+float(setup.get('boundary_idle_seconds'))+down,abs=.000002)
            assert float(setup.get('gross_feet'))==pytest.approx(float(setup.get('running_fpm'))*float(setup.get('running_seconds'))/60)
            travel=[]; spans=[]
            for knife in setup:
                w,length,outs,cuts=[int(knife.get(k)) for k in ('width_in','length_in','outs','cuts')]
                assert 12<=w<=60 and 20<=length<=92
                produced=int(knife.get('produced_sheets'))
                requested,planned,before,after=[int(knife.get(k)) for k in ('order_quantity','planned_sheets','remaining_before','remaining_after')]
                assert produced==cuts*outs and before-produced==after>=0
                assert planned>=requested>0
                ident=knife.get('order_id')
                immutable=(setup.get('grade'),w,length,requested,planned)
                if ident in ledger:
                    previous_identity,remaining=ledger[ident]
                    assert previous_identity==immutable and remaining==before
                    continuations+=1
                else: assert before==planned
                ledger[ident]=(immutable,after)
                travel.append(cuts*length/12)
                spans.append((float(knife.get('start_in')),float(knife.get('start_in'))+w*outs))
            singles+=len(spans)==1
            assert all(t==travel[0] for t in travel)
            web=float(setup.get('width_in'))
            assert spans[0][0]==pytest.approx(web-spans[-1][1])
            assert all(a[1]==b[0] for a,b in zip(spans,spans[1:]))
            gross=float(setup.get('gross_sqft'))
            assert gross==pytest.approx(float(setup.get('gross_feet'))*web/12)
            trim,shear,reject=[float(setup.get(k)) for k in ('trim_sqft','shear_sqft','reject_sqft')]
            assert travel[0]+shear*12/web==pytest.approx(float(setup.get('gross_feet')))
            assert trim==pytest.approx(travel[0]*(web-sum(b-a for a,b in spans))/12)
            assert reject==pytest.approx(int(setup.get('reject_sheets'))*int(setup[0].get('width_in'))*int(setup[0].get('length_in'))/144)
            assert trim+shear+reject<=gross
            cursor=end
        assert cursor==datetime.fromisoformat(shift.get('end'))
    assert len(durations)>50 and singles>0 and continuations>0
    # Only the last active orders may remain unfinished at the day boundary.
    open_ids={key for key,(_,remaining) in ledger.items() if remaining}
    assert open_ids <= {k.get('order_id') for k in root[-1].findall('setup')[-1]}


def test_order_tool_and_identity_rejection():
    data=generate(); a=Analysis(data)
    try:
        assert not a.errors
        row=a.get_order_matrix(1)['rows'][0]
        assert row['produced_sheets']==row['outs']*row['cuts']
        assert row['remaining_before']-row['produced_sheets']==row['remaining_after']
    finally:a.close()
    root=ET.fromstring(data)
    for setup in root.findall('.//setup'):
        if int(setup[0].get('remaining_before'))<int(setup[0].get('planned_sheets')):
            setup[0].set('order_quantity',str(int(setup[0].get('order_quantity'))-1))
            break
    a=Analysis(ET.tostring(root))
    try: assert any('Order identity' in e['error'] for e in a.errors)
    finally:a.close()
