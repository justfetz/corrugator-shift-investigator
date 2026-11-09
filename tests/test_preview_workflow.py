"""The per-pull-request preview deploy (issue #41): the origin arithmetic the workflow relies on,
and the retry around every write to the Cloud Run service.

`.github/scripts/preview_origins.py` turns `gcloud run services describe --format=json` into the
PUBLIC_DEMO_ORIGIN allow-list for a tagged revision, finds the tag's URL, and names the revisions
to delete. `.github/scripts/cloud_run_update.sh` runs a write again when Cloud Run rejected it
because another pull request's run (or the main deploy) changed the service first. These are the
pieces that would otherwise fail only in a live Cloud Run run.
"""
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import textwrap

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / '.github' / 'scripts' / 'preview_origins.py'
HELPER = ROOT / '.github' / 'scripts' / 'cloud_run_update.sh'
WORKFLOW = ROOT / '.github' / 'workflows' / 'preview.yml'
MAIN_WORKFLOW = ROOT / '.github' / 'workflows' / 'deploy-cloudrun.yml'

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


def test_orphaned_preview_revisions_span_all_prs_but_spare_tagged_serving_and_latest():
    svc = service_json(traffic=[{'percent': 100, 'revisionName': f'{SERVICE}-00046-7t5'},
                                {'tag': 'pr-50', 'url': 'https://pr-50---x.run.app', 'revisionName': f'{SERVICE}-pr-50-1111111-9'}])
    svc['status']['latestCreatedRevisionName'] = f'{SERVICE}-pr-47-e524061-4'
    svc['status']['latestReadyRevisionName'] = f'{SERVICE}-pr-47-e524061-4'
    revisions = [
        {'metadata': {'name': f'{SERVICE}-00046-7t5', 'creationTimestamp': '2026-11-08T08:00:00Z'}},          # production: not a preview
        {'metadata': {'name': f'{SERVICE}-pr-47-8104617-1', 'creationTimestamp': '2026-11-09T17:10:00Z'}},   # closed PR, untagged: orphan
        {'metadata': {'name': f'{SERVICE}-pr-47-e524061-4', 'creationTimestamp': '2026-11-09T17:25:00Z'}},   # orphan, but latest created
        {'metadata': {'name': f'{SERVICE}-pr-50-1111111-9', 'creationTimestamp': '2026-11-09T17:30:00Z'}},   # tagged: an open PR, keep
        {'metadata': {'name': f'{SERVICE}-pr-39-2222222-2', 'creationTimestamp': '2026-11-09T09:00:00Z'}},   # older closed PR: orphan
        {'metadata': {'name': f'other-service-pr-3-3333333-1', 'creationTimestamp': '2026-11-09T09:00:00Z'}},  # another service
    ]
    assert po.orphaned_preview_revisions(revisions, svc, SERVICE) == [
        (f'{SERVICE}-pr-39-2222222-2', False), (f'{SERVICE}-pr-47-8104617-1', False), (f'{SERVICE}-pr-47-e524061-4', True)]
    assert po.orphaned_preview_revisions(revisions, svc, SERVICE, keep=f'{SERVICE}-pr-47-e524061-4') == [
        (f'{SERVICE}-pr-39-2222222-2', False), (f'{SERVICE}-pr-47-8104617-1', False)]


def test_cli_prints_github_env_lines():
    out = subprocess.run([sys.executable, '-I', str(SCRIPT), 'origins', 'pr-41'], input=json.dumps(service_json()),
                         capture_output=True, text=True, check=True).stdout.splitlines()
    assert out[0] == f'BASE_ORIGINS={LEGACY},{DETERMINISTIC}'
    assert out[1].startswith('PREVIEW_ORIGINS=https://pr-41---')
    assert out[2] == 'ORIGINS=' + po.allow_list(service_json(), 'pr-41')
    url = subprocess.run([sys.executable, '-I', str(SCRIPT), 'url', 'pr-41'], input=json.dumps(service_json()),
                         capture_output=True, text=True, check=True).stdout
    assert url == 'PREVIEW_URL=\n'
    svc_path = ROOT / 'tests' / '_tmp_service.json'
    svc = service_json(); svc['status']['latestCreatedRevisionName'] = f'{SERVICE}-pr-9-aaaaaaa-1'
    svc_path.write_text(json.dumps(svc))
    try:
        orphans = subprocess.run([sys.executable, '-I', str(SCRIPT), 'orphans', SERVICE, str(svc_path)],
                                 input=json.dumps([{'metadata': {'name': f'{SERVICE}-pr-9-aaaaaaa-1'}}, {'metadata': {'name': f'{SERVICE}-pr-8-bbbbbbb-1'}}]),
                                 capture_output=True, text=True, check=True).stdout
    finally:
        svc_path.unlink()
    assert orphans == f'{SERVICE}-pr-8-bbbbbbb-1\n{SERVICE}-pr-9-aaaaaaa-1\tlatest\n'
    bad = subprocess.run([sys.executable, '-I', str(SCRIPT), 'nonsense'], input='{}', capture_output=True, text=True)
    assert bad.returncode == 2


def test_base_origins_learn_the_tag_host_form_from_a_live_tag_url():
    # Status only shows the deterministic URL, but a live tag uses the legacy hash host: the
    # untagged base of that tag URL is kept, so the pr-41--- form matches what the browser sends.
    svc = service_json(env_origins=DETERMINISTIC, status_url=DETERMINISTIC,
                       traffic=[{'percent': 100, 'latestRevision': True},
                                {'tag': 'pr-40', 'url': f'https://pr-40---{SERVICE}-abc123xyz-uc.a.run.app', 'revisionName': 'r'}])
    assert po.base_origins(svc) == [DETERMINISTIC, LEGACY]
    assert f'https://pr-41---{SERVICE}-abc123xyz-uc.a.run.app' in po.preview_origins(svc, 'pr-41')


def test_workflow_deploys_no_traffic_tagged_revisions_for_same_repo_prs_only():
    # Plain text checks, so CI (which installs only `-e . pytest`, no PyYAML) runs them too.
    text = WORKFLOW.read_text()
    assert 'types: [opened, synchronize, reopened, closed]' in text
    assert text.count('head.repo.full_name == github.repository') == 2, 'both jobs must refuse fork PRs'
    assert "github.event.action != 'closed'" in text and "github.event.action == 'closed'" in text
    assert '--no-traffic --tag "$TAG" --revision-suffix "$SUFFIX"' in text
    assert '--update-env-vars "^|^PUBLIC_DEMO_ORIGIN=${ORIGINS}"' in text
    assert 'traffic does not sum to 100' in text, 'the smoke test asserts production traffic is unchanged'
    assert '-H "Origin: $PREVIEW_URL"' in text, 'the smoke test proves the allow-list change works'
    assert '--remove-tags "$TAG"' in text
    assert text.count('preview_origins.py orphans "$SERVICE" /tmp/service.json') == 2, 'both jobs sweep orphaned preview revisions'
    assert "if [ \"$FLAG\" = \"latest\" ]" in text, 'the latest created revision is reported, not a failure'
    assert "cancel-in-progress: ${{ github.event.action != 'closed' }}" in text, 'a close must queue, not be cancellable'
    assert 'ref: ${{ github.event.repository.default_branch }}' in text, 'cleanup takes the helper script from main, not from a PR head that may predate it (#53)'
    assert 'ref: ${{ github.event.pull_request.head.sha }}' not in text
    assert text.index('Post the URL on the pull request') < text.index('Delete orphaned preview revisions')
    assert 'git fetch --depth=1 origin "refs/pull/$PR/merge"' in text, 'a run queued for an older head deploys the current one'
    assert text.index('refs/pull/$PR/merge') < text.index('google-github-actions/auth@v2'), 'the head check comes before any deploy step'


def test_main_deploy_routes_the_traffic_to_the_revision_it_named_not_to_latest():
    # gcloud: after any --no-traffic deploy the service is pinned to a named revision and later
    # deploys get 0% unless traffic is re-bound. main must always take the traffic, and it must be
    # main's own revision: LATEST follows whichever revision became ready last, and a preview can
    # land between the deploy and the traffic step (the runs are not serialized), so the deploy
    # names its revision and routes to that name.
    text = MAIN_WORKFLOW.read_text()
    traffic_lines = [line for line in text.splitlines() if 'gcloud run services update-traffic' in line]
    assert len(traffic_lines) == 1 and '--to-latest' not in traffic_lines[0], traffic_lines
    assert 'SUFFIX="main-${GITHUB_SHA:0:7}-${{ github.run_number }}-${{ github.run_attempt }}"' in text, 'unique per run and per re-run'
    assert text.count('--revision-suffix') == 2, 'the deploy and the allow-list update both name the revision they make'
    assert '--revision-suffix "${SUFFIX}-urls"' in text and 'REVISION=${SERVICE}-${SUFFIX}-urls' in text
    assert '--to-revisions "${REVISION}=100"' in text
    assert text.index('--to-revisions') > text.index('gcloud run deploy "$SERVICE" --source .')
    assert text.index('--to-revisions') > text.index('Add any URL the first deploy created'), 'services update also makes a revision'
    assert text.index('--to-revisions') < text.index('- name: Smoke test')
    assert 'does not carry 100% of the traffic' in text


# --- the retry around every write to the service (two pull requests' runs are not serialized) ---

CONFLICT = ("ERROR: (gcloud.run.deploy) ABORTED: Conflict for resource 'corrugator-shift-investigator': "
            "version '1794249923487033' was specified but current version is '1794250052170767'.")


def fake_gcloud(tmp_path, failures, message=CONFLICT, status=1):
    """A stand-in gcloud: fails `failures` times with `message` on stderr, then succeeds; logs every call."""
    calls = tmp_path / 'calls'
    calls.write_text('')
    stub = tmp_path / 'gcloud'
    stub.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "$*" >> "{calls}"
        if [ "$(wc -l < "{calls}")" -le {failures} ]; then
          echo "Building Container...done"; echo "Deployment failed"
          echo "{message}" >&2
          exit {status}
        fi
        echo "Service [corrugator-shift-investigator] revision [r] has been deployed."
        """))
    stub.chmod(0o755)
    return stub, calls


def run_helper(stub, *args, attempts=None):
    env = {**os.environ, 'CLOUD_RUN_UPDATE_PAUSE': '0'}
    if attempts is not None:
        env['CLOUD_RUN_UPDATE_ATTEMPTS'] = str(attempts)
    return subprocess.run(['bash', str(HELPER), str(stub), *args], env=env, capture_output=True, text=True)


def test_a_write_another_run_collided_with_is_run_again_and_the_run_goes_green(tmp_path):
    # The race of PR #50's Preview run 16: PR #54's close job wrote the service in the second
    # between this deploy's re-read after its build and its write. Attempt 1 is rejected exactly
    # as Cloud Run rejected it, attempt 2 is the same command again, and the step passes.
    stub, calls = fake_gcloud(tmp_path, failures=1)
    proc = run_helper(stub, 'run', 'deploy', SERVICE, '--source', '.', '--no-traffic', '--tag', 'pr-41')
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls.read_text().splitlines() == [f'run deploy {SERVICE} --source . --no-traffic --tag pr-41'] * 2, 'the same command, as it was'
    assert proc.stdout.count('ABORTED: Conflict for resource') == 1 and 'has been deployed' in proc.stdout, 'the output streams through'
    assert proc.stdout.count('::notice::') == 1 and 'attempt 1 of 4 was rejected' in proc.stdout
    assert '::warning::' not in proc.stdout and '::error::' not in proc.stdout


def test_the_retry_is_bounded_and_only_for_a_rejected_write(tmp_path):
    stub, calls = fake_gcloud(tmp_path, failures=99)
    proc = run_helper(stub, 'run', 'services', 'update-traffic', SERVICE, '--remove-tags', 'pr-41')
    assert proc.returncode == 1 and len(calls.read_text().splitlines()) == 4, "four attempts in all, then the command's own status"
    assert proc.stdout.count('::notice::') == 3 and 'attempt 4 of 4 was still rejected' in proc.stdout.split('::warning::')[1]
    stub, calls = fake_gcloud(tmp_path, failures=99)
    proc = run_helper(stub, 'run', 'deploy', SERVICE, attempts=1)
    assert proc.returncode == 1 and len(calls.read_text().splitlines()) == 1
    aborted = "ERROR: (gcloud.run.deploy) ABORTED: the operation was aborted"   # the word alone is not the rejected write
    stub, calls = fake_gcloud(tmp_path, failures=99, message=aborted)
    proc = run_helper(stub, 'run', 'deploy', SERVICE)
    assert proc.returncode == 1 and len(calls.read_text().splitlines()) == 1 and '::notice::' not in proc.stdout
    denied = "ERROR: (gcloud.run.services.update-traffic) PERMISSION_DENIED: Permission 'run.services.update' denied"
    stub, calls = fake_gcloud(tmp_path, failures=99, message=denied, status=7)
    proc = run_helper(stub, 'run', 'services', 'update-traffic', SERVICE, '--to-revisions', 'r=100')
    assert proc.returncode == 7 and len(calls.read_text().splitlines()) == 1, 'any other failure is not retried'
    assert 'PERMISSION_DENIED' in proc.stdout and '::notice::' not in proc.stdout and '::warning::' not in proc.stdout
    stub, calls = fake_gcloud(tmp_path, failures=0)
    proc = run_helper(stub, 'run', 'revisions', 'delete', 'r')
    assert proc.returncode == 0 and len(calls.read_text().splitlines()) == 1 and '::notice::' not in proc.stdout


@pytest.mark.parametrize('workflow, writes', [(WORKFLOW, 4), (MAIN_WORKFLOW, 3)])
def test_every_write_to_the_service_goes_through_the_retry_helper(workflow, writes):
    # Both workflows write the one service and GitHub serializes runs only per group (preview-pr-<n>
    # per pull request, cloud-run-deploy for main): a write that lost the race is run again rather
    # than failing the run. Reads are not retried. A sweep that loses a revision to another run's
    # sweep treats gone as deleted instead of failing (the close job did fail on it).
    text = workflow.read_text()
    write_lines = [line for line in text.splitlines() if re.search(r'gcloud run (deploy|services update|services update-traffic|revisions delete)\b', line)]
    assert len(write_lines) == writes, write_lines
    assert all('bash .github/scripts/cloud_run_update.sh gcloud run ' in line for line in write_lines), write_lines
    read_lines = [line for line in text.splitlines() if re.search(r'gcloud run (services describe|revisions list|revisions describe)\b', line)]
    assert read_lines and not any('cloud_run_update.sh' in line for line in read_lines)
    if workflow is WORKFLOW:
        assert text.count('is already gone; another run deleted it first') == 2, 'both sweeps'
        assert text.count('if ! gcloud run revisions describe "$NAME"') == 2
        assert text.count("&& grep -qiE 'NOT_FOUND|could not be found|not found' /tmp/revision.err") == 2, 'gone means NOT_FOUND, not any failed describe'
        assert 'cancel-in-progress: false' not in text and text.count('group:') == 1, 'no repository-wide group: GitHub keeps one pending run per group'


def test_workflow_yaml_parses_when_pyyaml_is_available():
    yaml = pytest.importorskip('yaml')
    wf = yaml.safe_load(WORKFLOW.read_text())
    assert set(wf['jobs']) == {'deploy', 'cleanup'}
    assert yaml.safe_load((ROOT / '.github' / 'workflows' / 'deploy-cloudrun.yml').read_text())['jobs']['deploy']
