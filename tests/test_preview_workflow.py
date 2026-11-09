"""The per-pull-request preview deploy (issue #41): the origin arithmetic the workflow relies on.

`.github/scripts/preview_origins.py` turns `gcloud run services describe --format=json` into the
PUBLIC_DEMO_ORIGIN allow-list for a tagged revision, finds the tag's URL, and names the revisions
to delete. These are the pieces that would otherwise fail only in a live Cloud Run run.
"""
import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / '.github' / 'scripts' / 'preview_origins.py'
WORKFLOW = ROOT / '.github' / 'workflows' / 'preview.yml'

spec = importlib.util.spec_from_file_location('preview_origins', SCRIPT)
po = importlib.util.module_from_spec(spec)
spec.loader.exec_module(po)

SERVICE = 'corrugator-shift-investigator'
LEGACY = f'https://{SERVICE}-abc123xyz-uc.a.run.app'
DETERMINISTIC = f'https://{SERVICE}-925041489941.us-central1.run.app'


def service_json(env_origins=f'{LEGACY},{DETERMINISTIC}', traffic=None, status_url=LEGACY):
    """The shape gcloud prints: the allow-list lives in the template env, URLs in status."""
    return {
        'metadata': {'name': SERVICE},
        'spec': {'template': {'spec': {'containers': [{'image': 'x', 'env': [
            {'name': 'PUBLIC_DEMO_ORIGIN', 'value': env_origins},
            {'name': 'TRUST_PROXY', 'value': '1'},
        ]}]}}},
        'status': {'url': status_url, 'address': {'url': status_url},
                   'traffic': traffic if traffic is not None else [{'percent': 100, 'latestRevision': True, 'revisionName': f'{SERVICE}-00042-abc'}]},
    }


def test_base_origins_union_template_and_status_without_tag_urls():
    svc = service_json(env_origins=f'{LEGACY},https://pr-39---{SERVICE}-abc123xyz-uc.a.run.app',
                       traffic=[{'percent': 100, 'latestRevision': True},
                                {'tag': 'pr-40', 'url': f'https://pr-40---{SERVICE}-abc123xyz-uc.a.run.app', 'revisionName': 'r'}],
                       status_url=DETERMINISTIC)
    # The stale pr-39 origin an earlier preview left in the template and the pr-40 tag URL in
    # status are both dropped; the template's own order comes first, then what status adds.
    assert po.base_origins(svc) == [LEGACY, DETERMINISTIC]


def test_base_origins_keep_the_template_order_so_the_token_seed_is_unchanged():
    svc = service_json(env_origins=f'{DETERMINISTIC},{LEGACY}', status_url=LEGACY)
    assert po.base_origins(svc)[0] == DETERMINISTIC


def test_preview_origins_and_allow_list_put_production_first():
    svc = service_json()
    assert po.preview_origins(svc, 'pr-41') == [
        f'https://pr-41---{SERVICE}-abc123xyz-uc.a.run.app',
        f'https://pr-41---{SERVICE}-925041489941.us-central1.run.app',
    ]
    allow = po.allow_list(svc, 'pr-41').split(',')
    assert allow[:2] == po.base_origins(svc), 'origins[0] must stay production (session-token seed)'
    assert allow[2:] == po.preview_origins(svc, 'pr-41')
    assert len(allow) == len(set(allow))


def test_preview_origins_pass_the_server_origin_validator():
    from investigator.server import PublicDemo
    allow = po.allow_list(service_json(), 'pr-41')
    public = PublicDemo.from_env({'PUBLIC_DEMO_ORIGIN': allow})
    assert f'https://pr-41---{SERVICE}-abc123xyz-uc.a.run.app' in public.origins
    assert public.origins[0] == LEGACY


def test_no_origins_known_yields_empty_lists_not_an_error():
    svc = {'metadata': {'name': SERVICE}, 'spec': {}, 'status': {}}
    assert po.base_origins(svc) == []
    assert po.preview_origins(svc, 'pr-41') == []
    assert po.allow_list(svc, 'pr-41') == ''


def test_preview_url_reads_the_tag_from_traffic():
    url = f'https://pr-41---{SERVICE}-abc123xyz-uc.a.run.app'
    svc = service_json(traffic=[{'percent': 100, 'latestRevision': True},
                                {'tag': 'pr-41', 'url': url + '/', 'revisionName': f'{SERVICE}-pr-41-deadbee-7'}])
    assert po.preview_url(svc, 'pr-41') == url
    assert po.preview_url(svc, 'pr-42') == ''


def test_preview_revisions_match_this_tag_only_oldest_first_minus_keep():
    revisions = [
        {'metadata': {'name': f'{SERVICE}-pr-41-aaaaaaa-3', 'creationTimestamp': '2026-11-09T10:00:00Z'}},
        {'metadata': {'name': f'{SERVICE}-pr-41-bbbbbbb-4', 'creationTimestamp': '2026-11-09T11:00:00Z'}},
        {'metadata': {'name': f'{SERVICE}-pr-41-ccccccc-5', 'creationTimestamp': '2026-11-09T09:00:00Z'}},
        {'metadata': {'name': f'{SERVICE}-pr-410-ddddddd-1', 'creationTimestamp': '2026-11-09T08:00:00Z'}},  # another PR
        {'metadata': {'name': f'{SERVICE}-00042-abc', 'creationTimestamp': '2026-11-08T08:00:00Z'}},          # production
    ]
    assert po.preview_revisions(revisions, SERVICE, 'pr-41', keep=f'{SERVICE}-pr-41-bbbbbbb-4') == [
        f'{SERVICE}-pr-41-ccccccc-5', f'{SERVICE}-pr-41-aaaaaaa-3']
    assert po.preview_revisions(revisions, SERVICE, 'pr-41') == [
        f'{SERVICE}-pr-41-ccccccc-5', f'{SERVICE}-pr-41-aaaaaaa-3', f'{SERVICE}-pr-41-bbbbbbb-4']
    assert po.preview_revisions(revisions, SERVICE, 'pr-7') == []


def test_cli_prints_github_env_lines():
    out = subprocess.run([sys.executable, '-I', str(SCRIPT), 'origins', 'pr-41'], input=json.dumps(service_json()),
                         capture_output=True, text=True, check=True).stdout.splitlines()
    assert out[0] == f'BASE_ORIGINS={LEGACY},{DETERMINISTIC}'
    assert out[1].startswith('PREVIEW_ORIGINS=https://pr-41---')
    assert out[2] == 'ORIGINS=' + po.allow_list(service_json(), 'pr-41')
    url = subprocess.run([sys.executable, '-I', str(SCRIPT), 'url', 'pr-41'], input=json.dumps(service_json()),
                         capture_output=True, text=True, check=True).stdout
    assert url == 'PREVIEW_URL=\n'
    bad = subprocess.run([sys.executable, '-I', str(SCRIPT), 'nonsense'], input='{}', capture_output=True, text=True)
    assert bad.returncode == 2


def test_workflow_deploys_no_traffic_tagged_revisions_for_same_repo_prs_only():
    yaml = pytest.importorskip('yaml')
    wf = yaml.safe_load(WORKFLOW.read_text())
    assert set(wf[True]['pull_request']['types']) == {'opened', 'synchronize', 'reopened', 'closed'}
    deploy, cleanup = wf['jobs']['deploy'], wf['jobs']['cleanup']
    for job in (deploy, cleanup):
        assert 'head.repo.full_name == github.repository' in job['if'], 'forks must never reach GCP'
    runs = '\n'.join(step.get('run', '') for step in deploy['steps'])
    assert '--no-traffic' in runs and '--tag "$TAG"' in runs and '--revision-suffix' in runs
    assert '--update-env-vars "^|^PUBLIC_DEMO_ORIGIN=${ORIGINS}"' in runs
    assert 'traffic does not sum to 100' in runs, 'the smoke test asserts production traffic is unchanged'
    assert '--remove-tags "$TAG"' in '\n'.join(step.get('run', '') for step in cleanup['steps'])
    assert wf['concurrency']['cancel-in-progress'] is True
