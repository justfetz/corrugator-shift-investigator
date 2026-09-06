import math
import xml.etree.ElementTree as ET
import pytest
from investigator.fixture import generate
from investigator.analysis import Analysis, safe_xml

@pytest.fixture
def analysis():
    obj = Analysis(generate())
    yield obj
    obj.close()

def test_reproducible_three_shifts_and_known_downtime(analysis):
    assert generate() == generate()
    assert generate(8) != generate(7)
    assert not analysis.errors
    for shift in (1, 2, 3):
        assert analysis.get_shift_kpis(shift)["rows"][0]["valid_setups"] == 60
    rows = analysis.get_downtime_breakdown(2)["rows"]
    assert rows[0]["category"] == "Knife jam"
    assert rows[0]["down_minutes"] == 16
    assert rows[0]["shift_percent"] == pytest.approx(100*16/480)
    assert rows[1]["down_minutes"] == 4
    assert sum(r["down_minutes"] for r in analysis.get_downtime_breakdown(1)["rows"]) == 5

def test_area_and_speed_independent_calculation(analysis):
    root = ET.fromstring(generate())
    setups = root[0].findall("setup")
    feet = sum(float(s.get("gross_feet")) for s in setups)
    area = sum(float(s.get("gross_feet"))*float(s.get("width_in"))/12 for s in setups)
    row = analysis.get_shift_kpis(1)["rows"][0]
    assert row["observed_shift_fpm"] == pytest.approx(feet/480)
    assert row["gross_sqft"] == pytest.approx(area)
    assert row["throughput_in"] == pytest.approx(area/feet*12)
    assert row["dry_end_pct"] == pytest.approx((60*10*30*45/144)/area*100)
    assert len(row["source_ids"]) == 60

def test_continuing_order_and_shared_footage():
    setups = ET.fromstring(generate())[0].findall("setup")
    assert setups[0][0].get("order_id") == setups[1][0].get("order_id")
    assert setups[0].get("id") != setups[1].get("id")
    for setup in setups:
        travel = [float(k.get("cuts"))*float(k.get("length_in"))/12 for k in setup]
        assert travel[0] == travel[1]
        assert float(setup.get("gross_feet")) == pytest.approx(travel[0]+17)

def test_corrupt_trim_preserved_and_excluded():
    root = ET.fromstring(generate())
    root[0][0].set("trim_sqft", "99999999")
    obj = Analysis(ET.tostring(root))
    try:
        result = obj.get_shift_kpis(1)
        assert result["coverage"] == "incomplete"
        assert result["rows"][0]["valid_setups"] == 59
        assert result["excluded_records"][0]["raw"]["trim_sqft"] == "99999999"
        assert obj.get_shift_kpis(2)["coverage"] == "complete"
    finally:
        obj.close()

@pytest.mark.parametrize("value", ["NaN", "inf", "-1"])
def test_invalid_numbers(value):
    root = ET.fromstring(generate())
    root[0][0].set("gross_feet", value)
    obj = Analysis(ET.tostring(root))
    try:
        assert obj.errors[0]["record_id"] == "S1-00"
    finally:
        obj.close()

@pytest.mark.parametrize("data", [b'<!DOCTYPE x [<!ENTITY y "bad">]><x>&y;</x>', b'<x>'*10+b'</x>'*10, b'x'*1000001, '<x/>'.encode('utf-16')], ids=['dtd','depth','size','encoding'])
def test_unsafe_xml(data):
    with pytest.raises((ValueError, UnicodeError)):
        safe_xml(data)

def test_tool_arguments_and_bound_values(analysis):
    with pytest.raises(ValueError):
        analysis.get_downtime_breakdown(2, group_by="reason; DROP TABLE stops")
    with pytest.raises(ValueError):
        analysis.get_downtime_breakdown("2 OR 1=1")
    result = analysis.get_downtime_breakdown(2, kind="Maintenance")
    assert result["trace"]["parameters"] == [2, "Maintenance"]
    assert "Maintenance" not in result["trace"]["sql"]
    assert len(result["rows"]) == 1

def test_overlap_rejected():
    root = ET.fromstring(generate())
    stop = root[0].find("stop")
    duplicate = ET.SubElement(root[0], "stop", **stop.attrib)
    duplicate.set("id", "overlap")
    with pytest.raises(ValueError, match="Overlapping"):
        Analysis(ET.tostring(root))

def test_wrong_width_or_cut_counts_excluded():
    root = ET.fromstring(generate())
    root[0][0][1].set("cuts", "1")
    obj = Analysis(ET.tostring(root))
    try:
        assert "footage" in obj.errors[0]["error"]
    finally:
        obj.close()

def test_production_day_midnight():
    root = ET.fromstring(generate())
    assert root[2].get("start") == "2026-09-01T23:00:00"
    assert root[2].get("end") == "2026-09-02T07:00:00"


def test_chart_uses_rows_and_escapes_labels(analysis):
    from investigator.chart import downtime_svg
    result = analysis.get_downtime_breakdown(2)
    root = ET.fromstring(downtime_svg(result))
    text = ''.join(root.itertext())
    assert '16 min' in text and '4 min' in text
    result['rows'][0]['category'] = '<script>alert(1)</script>'
    assert '<script>' not in downtime_svg(result)
    result['rows'][0]['down_minutes'] = float('nan')
    with pytest.raises(ValueError):
        downtime_svg(result)

def test_wet_end_grade_consistency():
    root=ET.fromstring(generate())
    root[0].findall('setup')[1].set('grade','32-C')
    obj=Analysis(ET.tostring(root))
    try:
        assert 'Wet-end group changes' in obj.errors[0]['error']
        assert obj.get_wet_end_performance(1)['coverage']=='incomplete'
    finally:obj.close()
