from investigator.archive import build_archive
from investigator.analysis import Analysis
import pytest


def test_archive_manifest_and_dates(tmp_path):
    manifest=build_archive(tmp_path,2)
    assert [d['production_day'] for d in manifest['days']]==['2026-09-01','2026-09-02']
    assert all(100 < d['setups'] < 260 for d in manifest['days'])
    assert sum(d['wet_end_runs'] for d in manifest['days']) != 180
    for entry in manifest['days']:
        a=Analysis((tmp_path/entry['file']).read_bytes())
        try:
            assert a.version==entry['sha256']
            assert sum(a.get_shift_kpis(shift)['rows'][0]['paper_changes'] for shift in (1,2,3))==entry['wet_end_runs']
        finally:a.close()
    with pytest.raises(ValueError):build_archive(tmp_path,31)
