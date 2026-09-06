from investigator.archive import build_archive
from investigator.analysis import Analysis
import pytest


def test_archive_manifest_and_dates(tmp_path):
    manifest=build_archive(tmp_path,2)
    assert [d['production_day'] for d in manifest['days']]==['2026-09-01','2026-09-02']
    assert sum(d['setups'] for d in manifest['days'])==360
    for entry in manifest['days']:
        a=Analysis((tmp_path/entry['file']).read_bytes())
        try:assert a.version==entry['sha256']
        finally:a.close()
    with pytest.raises(ValueError):build_archive(tmp_path,31)
