"""Guards for the Cloud Run workflows and the documentation tree (issue #28).

The deploy of `main` and the per-pull-request preview authenticate to Google the same way:
Workload Identity Federation when the two repository variables are set, the stored `GCP_SA_KEY`
secret with a warning until then, and a failure with the instruction only when neither is
available. The docs must not link to files that were moved. Plain text checks, plus the
credential-choice step's own shell run under bash for all eight combinations of the two variables
and the secret, so no YAML dependency.
"""
import json
import os
import re
import subprocess
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / '.github' / 'workflows'
DEPLOY = WORKFLOWS / 'deploy-cloudrun.yml'
PREVIEW = WORKFLOWS / 'preview.yml'
JOBS = {DEPLOY.name: 1, PREVIEW.name: 2}
CHOOSE = '- name: Choose the Google credential'
WIF_STEP = ("- uses: google-github-actions/auth@v2\n"
            "        if: steps.gcp_auth.outputs.mode == 'wif'\n"
            "        with:\n"
            "          workload_identity_provider: ${{ vars.GCP_WORKLOAD_IDENTITY_PROVIDER }}\n"
            "          service_account: ${{ vars.GCP_DEPLOYER_SERVICE_ACCOUNT }}\n")
KEY_STEP = ("- uses: google-github-actions/auth@v2\n"
            "        if: steps.gcp_auth.outputs.mode == 'key'\n"
            "        with:\n"
            "          credentials_json: ${{ secrets.GCP_SA_KEY }}\n")
PROVIDER = 'projects/123/locations/global/workloadIdentityPools/github/providers/github-oidc'
ACCOUNT = 'github-deployer@demo.iam.gserviceaccount.com'


def auth_blocks(text):
    """Each job's credential block: the choice step through the key fallback step."""
    blocks = []
    for match in re.finditer(re.escape(CHOOSE), text):
        end = text.index(KEY_STEP, match.start()) + len(KEY_STEP)
        blocks.append(text[match.start():end])
    return blocks


def choice_script(text):
    """The shell of the credential-choice step, dedented, as the runner would execute it."""
    start = text.index('run: |\n', text.index(CHOOSE)) + len('run: |\n')
    lines = []
    for line in text[start:].splitlines():
        if line.strip() and not line.startswith(' ' * 10):
            break
        lines.append(line)
    return textwrap.dedent('\n'.join(lines)) + '\n'


def choose(tmp_path, provider='', account='', key=False):
    """Run the choice step with these repository settings; returns (exit code, log, $GITHUB_OUTPUT)."""
    output = tmp_path / 'github_output'
    output.write_text('')
    env = {**os.environ, 'WIF_PROVIDER': provider, 'DEPLOYER_SA': account,
           'HAVE_KEY': 'true' if key else 'false', 'GITHUB_OUTPUT': str(output)}
    proc = subprocess.run(['bash', '-c', choice_script(DEPLOY.read_text(encoding='utf-8'))],
                          env=env, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr, output.read_text()


@pytest.mark.parametrize('name', sorted(JOBS))
def test_every_cloud_run_job_prefers_wif_and_falls_back_to_the_key_with_a_warning(name):
    text = (WORKFLOWS / name).read_text(encoding='utf-8')
    blocks = auth_blocks(text)
    assert len(blocks) == JOBS[name], f'one credential block per job in {name}'
    assert re.search(r'^permissions:\n(?:  .*\n)*?  id-token: write', text, re.M), 'WIF needs id-token: write'
    assert text.count(WIF_STEP) == len(blocks) and text.count(KEY_STEP) == len(blocks)
    assert text.count('google-github-actions/auth@v2') == 2 * len(blocks), 'no auth step outside the block'
    assert set(re.findall(r'secrets\.(\w+)', text)) == {'GCP_SA_KEY'}, 'the key is the only GitHub secret on the Cloud Run path'
    assert text.count('${{ secrets.GCP_SA_KEY }}') == len(blocks), 'the key value reaches only the fallback auth step'
    assert text.count("HAVE_KEY: ${{ secrets.GCP_SA_KEY != '' }}") == len(blocks)
    for block in blocks:
        warning = block.split('::warning::', 1)[1].split('\n', 1)[0]
        assert 'GCP_SA_KEY' in warning and 'GCP_WORKLOAD_IDENTITY_PROVIDER' in warning, 'the warning says the key was used and how to stop'
        assert '::error::' in block and 'exit 1' in block
        assert block.index(WIF_STEP) < block.index(KEY_STEP)
    starts = [m.start() for m in re.finditer(re.escape(CHOOSE), text)]
    setups = [m.start() for m in re.finditer(re.escape('google-github-actions/setup-gcloud@v2'), text)]
    assert len(setups) == len(blocks) and all(s < g for s, g in zip(starts, setups)), 'the credential is chosen before gcloud is set up, in every job'


def test_preview_authenticates_exactly_like_the_main_deploy():
    deploy, = auth_blocks(DEPLOY.read_text(encoding='utf-8'))
    preview = PREVIEW.read_text(encoding='utf-8')
    assert [block == deploy for block in auth_blocks(preview)] == [True, True], \
        'both preview jobs must carry the credential block of deploy-cloudrun.yml, byte for byte'
    assert 'WIF_PROVIDER: ${{ vars.GCP_WORKLOAD_IDENTITY_PROVIDER }}' in preview
    assert 'DEPLOYER_SA: ${{ vars.GCP_DEPLOYER_SERVICE_ACCOUNT }}' in preview
    assert "github.event.pull_request.head.repo.full_name == github.repository" in preview, 'a fork gets neither the key nor an OIDC token'


def test_credential_choice_prefers_wif_even_while_the_key_still_exists(tmp_path):
    code, log, output = choose(tmp_path, PROVIDER, ACCOUNT, key=True)
    assert (code, output) == (0, 'mode=wif\n')
    assert f'deploying as {ACCOUNT} through {PROVIDER}' in log
    assert '::warning::' not in log and '::error::' not in log
    code, log, output = choose(tmp_path, PROVIDER, ACCOUNT, key=False)   # after the cutover: the key is gone
    assert (code, output) == (0, 'mode=wif\n') and '::warning::' not in log


def test_credential_choice_falls_back_to_the_key_with_a_warning(tmp_path):
    code, log, output = choose(tmp_path, key=True)
    assert (code, output) == (0, 'mode=key\n')
    assert '::warning::' in log and 'GCP_SA_KEY' in log
    assert 'GCP_WORKLOAD_IDENTITY_PROVIDER and GCP_DEPLOYER_SERVICE_ACCOUNT' in log, 'the warning names both unset variables'
    code, log, output = choose(tmp_path, provider=PROVIDER, key=True)   # half-configured WIF is not WIF
    assert (code, output) == (0, 'mode=key\n')
    assert 'unset: GCP_DEPLOYER_SERVICE_ACCOUNT' in log and 'unset: GCP_WORKLOAD_IDENTITY_PROVIDER' not in log
    code, log, output = choose(tmp_path, account=ACCOUNT, key=True)
    assert (code, output) == (0, 'mode=key\n') and 'unset: GCP_WORKLOAD_IDENTITY_PROVIDER)' in log


def test_credential_choice_fails_only_when_neither_is_available(tmp_path):
    code, log, output = choose(tmp_path, key=False)
    assert (code, output) == (1, '')
    assert '::error::' in log and 'GCP_SA_KEY' in log and "'Keyless deploy'" in log
    code, log, output = choose(tmp_path, account=ACCOUNT, key=False)
    assert (code, output) == (1, '') and 'unset: GCP_WORKLOAD_IDENTITY_PROVIDER' in log
    code, log, output = choose(tmp_path, provider=PROVIDER, key=False)
    assert (code, output) == (1, '') and 'unset: GCP_DEPLOYER_SERVICE_ACCOUNT' in log


MAIN_HASH = 'https://corrugator-shift-investigator-4s5ogdb7lq-uc.a.run.app'
MAIN = 'https://corrugator-shift-investigator-65612311806.us-central1.run.app'
PREVIEWS = ['https://pr-107---corrugator-shift-investigator-4s5ogdb7lq-uc.a.run.app',
            'https://pr-91---corrugator-shift-investigator-4s5ogdb7lq-uc.a.run.app']
CUSTOM = 'https://shift.talktomymachine.ai'   # CUSTOM_ORIGINS' default (PR #112); sorts after every *.run.app URL
CURL_STUB = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
url = args[-1]
base = url.rsplit('/api/', 1)[0]
open(os.environ['STUB_LOG'], 'a').write(url + '\\n')
if base in os.environ.get('STUB_NXDOMAIN', '').split():
    # what curl does when DNS fails: -w still prints 000, the error goes to stderr, exit 6, and the -o file is untouched
    sys.stdout.write('000' if '-w' in args else '')
    sys.stderr.write('curl: (6) Could not resolve host: ' + base.split('://', 1)[1] + '\\n')
    sys.exit(6)
up = base in os.environ.get('STUB_UP', '').split()
if url.endswith('/api/health'):
    # the image's health body: its window ends the day before the build (issue #35), or STUB_AS_OF when a test freezes it
    as_of = os.environ['STUB_AS_OF']
    window = {'as_of': as_of, 'first_day': '2025-11-10', 'last_day': as_of, 'production_days': 262}
    open(args[args.index('-o') + 1], 'w').write(json.dumps({'ok': True, 'store': {'window': window}}) if up else 'down')
    if '-w' in args:
        sys.stdout.write('200' if up else '503')
elif up:
    sys.stdout.write(json.dumps({'live_modes': ['anthropic'] if base in os.environ.get('STUB_LIVE', '').split() else []}))
else:
    sys.exit(22)
"""


def step_script(text, name):
    """The shell of a workflow step's `run: |` block, dedented."""
    start = text.index('run: |\n', text.index(f'- name: {name}')) + len('run: |\n')
    lines = []
    for line in text[start:].splitlines():
        if line.strip() and not line.startswith(' ' * 10):
            break
        lines.append(line)
    return textwrap.dedent('\n'.join(lines)) + '\n'


def smoke(tmp_path, up, live=(), live_model='0', origins=(MAIN_HASH, MAIN, *PREVIEWS), as_of='2026-11-10', nxdomain=()):
    """Run the main deploy's smoke test against a stub curl; returns (exit code, log, URLs requested). The build ran on
    RUN_DAY 2026-11-11 (a Wednesday), so a fresh image's window ends 2026-11-10; `as_of` freezes it elsewhere.
    `nxdomain` URLs fail like a host that does not resolve (curl exit 6, HTTP 000, no body written)."""
    stub = tmp_path / 'bin'
    stub.mkdir(exist_ok=True)
    (stub / 'curl').write_text(CURL_STUB)
    (stub / 'curl').chmod(0o755)
    log = tmp_path / 'requests.log'
    log.write_text('')
    env = {**os.environ, 'PATH': f'{stub}:{os.environ["PATH"]}', 'ORIGINS': ','.join(origins), 'LIVE_MODEL': live_model,
           'STUB_UP': ' '.join(up), 'STUB_LIVE': ' '.join(live), 'STUB_NXDOMAIN': ' '.join(nxdomain), 'STUB_LOG': str(log),
           'RUN_DAY': '2026-11-11', 'STUB_AS_OF': as_of}
    proc = subprocess.run(['bash', '-c', step_script(DEPLOY.read_text(encoding='utf-8'), 'Smoke test')],
                          env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)   # interleaved, as in the run log
    return proc.returncode, proc.stdout, log.read_text().split()


def test_smoke_test_reports_main_and_never_a_preview(tmp_path):
    """A tagged preview revision (pr-<n>---…) answers on the same service and sits in ORIGINS for the allow-list,
    but it runs a pull request's image: run 74 printed `Live at https://pr-91---…` and read pr-91's /api/config."""
    code, log, requested = smoke(tmp_path, up=[MAIN_HASH, MAIN, *PREVIEWS], live=[MAIN, MAIN_HASH], live_model='1')
    assert code == 0, log
    assert log.rstrip().endswith(f'Live at {MAIN}'), 'the deterministic URL, the one the README publishes'
    assert not any('---' in url for url in requested), 'a preview is neither probed nor asked for its config'
    assert f'{MAIN}/api/config' in requested
    code, log, _ = smoke(tmp_path, up=[MAIN_HASH, *PREVIEWS], live=[MAIN_HASH])   # no deterministic URL on this project
    assert code == 0 and log.rstrip().endswith(f'Live at {MAIN_HASH}'), log


def test_smoke_test_skips_only_preview_tags_not_a_service_named_with_triple_hyphens(tmp_path):
    """Codex on #110: CLOUD_RUN_SERVICE may itself contain '---'; only preview.yml's `pr-<n>` tag URLs are skipped."""
    main_hash = 'https://line---demo-4s5ogdb7lq-uc.a.run.app'
    main = 'https://line---demo-65612311806.us-central1.run.app'
    preview = 'https://pr-7---line---demo-4s5ogdb7lq-uc.a.run.app'
    code, log, requested = smoke(tmp_path, up=[main_hash, main, preview], live=[main], live_model='1',
                                 origins=(main_hash, main, preview))
    assert code == 0 and log.rstrip().endswith(f'Live at {main}'), log
    assert preview not in ' '.join(requested) and f'{main}/api/config' in requested


def test_smoke_test_fails_a_frozen_window_on_mains_own_url(tmp_path):
    """Issue #35 with #110: the window check reads main's own URL, the one the smoke test reports, and fails the deploy
    when its window does not end the day before the build."""
    code, log, requested = smoke(tmp_path, up=[MAIN_HASH, MAIN, *PREVIEWS], live=[MAIN])
    assert code == 0 and 'window 2025-11-10 to 2026-11-10 (as of 2026-11-10, 262 production days): ends yesterday' in log, log
    assert requested.count(f'{MAIN}/api/health') == 2 and not any('---' in url for url in requested)
    code, log, _ = smoke(tmp_path, up=[MAIN_HASH, MAIN, *PREVIEWS], live=[MAIN], as_of='2026-10-01')
    assert code != 0 and '::error::store.window ends 2026-10-01' in log and 'Live at' not in log, log


def test_smoke_test_fails_when_only_a_preview_answers(tmp_path):
    code, log, _ = smoke(tmp_path, up=PREVIEWS, live=PREVIEWS)
    assert code != 0 and 'Live at' not in log, 'main is down: a healthy preview must not pass the deploy'
    code, log, _ = smoke(tmp_path, up=[MAIN, *PREVIEWS], live=PREVIEWS, live_model='1')
    assert code != 0, "PUBLIC_DEMO_LIVE_MODEL=1 checks main's own live modes, not a preview's"


# ORIGINS as the "Add any URL" step leaves it: sorted, so the custom domain comes last.
WITH_CUSTOM = tuple(sorted((MAIN_HASH, MAIN, *PREVIEWS, CUSTOM)))


def health_line(log, url):
    """The smoke test's `GET <url>/api/health -> HTTP <code> <body>` line for one URL."""
    return next(line for line in log.splitlines() if line.startswith(f'GET {url}/api/health'))


def test_smoke_test_prefers_the_deterministic_url_over_the_custom_domain(tmp_path):
    """Run 77: with CUSTOM_ORIGINS appended (PR #112), whichever non-*.a.run.app URL answered last became LIVE, so the
    custom domain would have beaten the deterministic URL the README publishes. The priority is explicit, not ORIGINS order."""
    assert WITH_CUSTOM[-1] == CUSTOM
    for origins in (WITH_CUSTOM, tuple(reversed(WITH_CUSTOM))):
        code, log, requested = smoke(tmp_path, up=[MAIN_HASH, MAIN, CUSTOM], live=[MAIN], live_model='1', origins=origins)
        assert code == 0 and log.rstrip().endswith(f'Live at {MAIN}'), log
        assert f'{MAIN}/api/config' in requested and f'{CUSTOM}/api/config' not in requested
        assert f'{CUSTOM}/api/health' in requested, 'the custom domain is still probed'
    # the live-mode check reads the reported URL: live modes only on the custom domain do not pass a deploy reported at MAIN
    code, log, _ = smoke(tmp_path, up=[MAIN_HASH, MAIN, CUSTOM], live=[CUSTOM], live_model='1', origins=WITH_CUSTOM)
    assert code != 0 and 'Live at' not in log, log


def test_smoke_test_falls_back_to_the_custom_domain_before_the_legacy_url(tmp_path):
    """Deterministic first, then the custom domain, then the legacy *.a.run.app hash URL."""
    code, log, requested = smoke(tmp_path, up=[MAIN_HASH, CUSTOM], live=[CUSTOM], live_model='1', origins=WITH_CUSTOM)
    assert code == 0 and log.rstrip().endswith(f'Live at {CUSTOM}'), log
    assert f'{CUSTOM}/api/config' in requested and f'{MAIN_HASH}/api/config' not in requested
    code, log, _ = smoke(tmp_path, up=[MAIN_HASH], live=[MAIN_HASH], live_model='1', origins=WITH_CUSTOM)
    assert code == 0 and log.rstrip().endswith(f'Live at {MAIN_HASH}'), log


def test_smoke_test_logs_no_stale_body_when_the_custom_domain_does_not_resolve(tmp_path):
    """Run 77: `curl: (6) Could not resolve host` left the previous URL's body in the -o file, so the log read
    `GET https://shift.talktomymachine.ai/api/health -> HTTP 000 {"ok": true, ...}`, a healthy-looking line."""
    code, log, requested = smoke(tmp_path, up=[MAIN_HASH, MAIN], live=[MAIN], live_model='1', origins=WITH_CUSTOM,
                                 nxdomain=[CUSTOM])
    assert code == 0 and log.rstrip().endswith(f'Live at {MAIN}'), log
    assert health_line(log, MAIN).__contains__('HTTP 200 {"ok": true'), log
    assert health_line(log, CUSTOM).rstrip() == f'GET {CUSTOM}/api/health -> HTTP 000', 'no body carried over from MAIN'
    assert 'Could not resolve host: shift.talktomymachine.ai' in log
    assert f'{CUSTOM}/api/config' not in requested
    # the deterministic URL down as well: the legacy URL is reported, and the failing domain still shows no body
    code, log, _ = smoke(tmp_path, up=[MAIN_HASH], live=[MAIN_HASH], live_model='1', origins=WITH_CUSTOM, nxdomain=[CUSTOM])
    assert code == 0 and log.rstrip().endswith(f'Live at {MAIN_HASH}'), log
    assert health_line(log, MAIN).endswith('HTTP 503 down')
    assert health_line(log, CUSTOM).rstrip() == f'GET {CUSTOM}/api/health -> HTTP 000', log
    # nothing answers, the custom domain unresolvable: the run fails as before
    code, log, _ = smoke(tmp_path, up=[], origins=WITH_CUSTOM, nxdomain=[CUSTOM])
    assert code != 0 and 'Live at' not in log, log


def test_docs_and_workflow_agree_that_no_secret_name_has_a_default():
    """docs/ANTHROPIC_DEMO.md said OPENAI_SECRET_NAME defaulted to `openai-api-key` (and a workflow comment said OpenAI
    kept its historical default) while the workflow's default is empty, as docs/DEPLOYMENT.md says."""
    deploy = DEPLOY.read_text(encoding='utf-8')
    assert "OPENAI_SECRET: ${{ vars.OPENAI_SECRET_NAME || '' }}" in deploy
    assert "ANTHROPIC_SECRET: ${{ vars.ANTHROPIC_SECRET_NAME || '' }}" in deploy
    assert 'keeps its historical default' not in deploy
    for page in ('ANTHROPIC_DEMO.md', 'DEPLOYMENT.md'):
        text = (ROOT / 'docs' / page).read_text(encoding='utf-8')
        assert not re.search(r'\(default `(openai|anthropic)-api-key`\)', text), page


def test_relative_markdown_links_resolve():
    """Every relative link in the current docs points at a file that exists (the September review moved)."""
    pages = [ROOT / 'README.md', ROOT / 'CLAUDE.md', *(ROOT / 'docs').glob('*.md'), ROOT / 'evals' / 'README.md',
             ROOT / 'deploy' / 'huggingface' / 'README.md']
    broken = []
    for page in pages:
        for target in re.findall(r'\]\(([^)\s#]+)(?:#[^)]*)?\)', page.read_text(encoding='utf-8')):
            if re.match(r'^[a-z]+:', target):
                continue
            if not (page.parent / target).exists():
                broken.append(f'{page.relative_to(ROOT)} -> {target}')
    assert not broken, broken


def test_september_review_is_archived():
    assert not (ROOT / 'docs' / 'REVIEW_2026-09-07.md').exists()
    assert not (ROOT / 'docs' / '_scratch').exists()
    assert (ROOT / 'docs' / 'archive' / 'REVIEW_2026-09-07.md').exists()
    assert (ROOT / 'docs' / 'archive' / 'review-2026-09-07' / 'probes.py').exists()


def test_main_redeploys_every_weekday_night_and_by_hand():
    """Issue #35: the dataset is the year ending yesterday, fixed per image, so main is rebuilt and
    redeployed on a schedule (and on demand); the push trigger stays."""
    text = DEPLOY.read_text()
    on = text.split('\non:\n', 1)[1].split('\npermissions:', 1)[0]   # the trigger block, as plain text (CI has no YAML library)
    assert re.search(r"^  push:\n    branches: \[main\]$", on, re.M), on
    assert re.search(r"^  schedule:\n    - cron: '0 13 \* \* 1-5'", on, re.M), on   # 07:00 or 08:00 Chicago, after the overnight shift (owner, PR #91)
    assert re.search(r"^  workflow_dispatch:$", on, re.M), on


def test_the_deploy_smoke_test_fails_a_window_that_does_not_end_yesterday(tmp_path):
    """Owner, PR #91: the smoke test asserts /api/health's window ends yesterday, so a cached image layer
    that froze an older window cannot pass silently. as_of must be the day before the build started (or,
    past midnight UTC, before the smoke test), last_day the last production day on or before it."""
    text = DEPLOY.read_text()
    deploy = text.split('- name: Deploy from source', 1)[1].split('- name:', 1)[0]
    smoke = text.split('- name: Smoke test', 1)[1]
    assert 'echo "RUN_DAY=$(date -u +%F)" >> "$GITHUB_ENV"' in deploy.split('gcloud run deploy', 1)[0]   # recorded before the build
    assert 'curl -sS --max-time 30 -o /tmp/health.json "$LIVE/api/health"' in smoke
    assert 'python3 .github/scripts/check_window.py /tmp/health.json "$RUN_DAY"' in smoke
    assert smoke.index('check_window.py') < smoke.index('echo "Live at')
    script = ROOT / '.github' / 'scripts' / 'check_window.py'

    def run(as_of, last_day, run_day, today):
        health = tmp_path / 'health.json'
        health.write_text(json.dumps({'ok': True, 'store': {'source': 'prebuilt', 'window': {
            'as_of': as_of, 'first_day': '2025-11-11', 'last_day': last_day, 'production_days': 261}}}))
        proc = subprocess.run(['python3', str(script), str(health), run_day, today], capture_output=True, text=True)
        return proc.returncode, proc.stdout + proc.stderr

    assert run('2026-11-10', '2026-11-10', '2026-11-11', '2026-11-11')[0] == 0                # a Wednesday run: Tuesday
    assert run('2026-11-08', '2026-11-06', '2026-11-09', '2026-11-09')[0] == 0                # a Monday run: Sunday, Friday's data
    assert run('2026-11-07', '2026-11-06', '2026-11-08', '2026-11-08')[0] == 0                # a Sunday push: Saturday, Friday's data
    assert run('2026-11-10', '2026-11-10', '2026-11-11', '2026-11-12')[0] == 0                # the build crossed midnight UTC
    code, out = run('2026-11-09', '2026-11-09', '2026-11-11', '2026-11-11')                   # a day stale: a cached layer
    assert code == 1 and '::error::store.window ends 2026-11-09' in out and 'expected yesterday, 2026-11-10' in out, out
    code, out = run('2026-11-08', '2026-11-08', '2026-11-09', '2026-11-09')                   # Sunday reported as a production day
    assert code == 1 and 'last_day is 2026-11-08, expected 2026-11-06' in out, out
    health = tmp_path / 'health.json'
    health.write_text(json.dumps({'ok': True, 'store': {'source': 'generated'}}))           # an image without the window
    proc = subprocess.run(['python3', str(script), str(health), '2026-11-11'], capture_output=True, text=True)
    assert proc.returncode == 1 and 'no store.window' in proc.stdout, proc.stdout


def test_the_image_prebuilds_the_demo_year_and_the_golden_year():
    """The demo's window ends on the build's yesterday; the /evals page scores against the golden
    window, whose as-of is read from golden_values.json so the two can never disagree."""
    dockerfile = (ROOT / 'Dockerfile').read_text()
    assert 'python -m investigator.prebuilt /app/prebuilt ' in dockerfile
    assert ('python -m investigator.prebuilt --as-of "$(python -c \'import json; print(json.load(open("evals/golden_values.json"))["as_of"])\')" '
            '/app/prebuilt-golden') in dockerfile
    assert 'INVESTIGATOR_PREBUILT=/app/prebuilt ' in dockerfile and 'INVESTIGATOR_GOLDEN_PREBUILT=/app/prebuilt-golden' in dockerfile
    assert not any('INVESTIGATOR_AS_OF' in line for line in dockerfile.splitlines() if line.startswith(('ENV', 'ARG')))   # never pinned
