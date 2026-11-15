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

# The vendor, its products, record codes and source tables, and the other corrugator makers, spelled in pieces in
# tests/vendor_names.py, which says what each pattern matches (issue #100).
from vendor_names import LONG_MAKER_NAMES, MAKER_NAMES, MAKER_PATTERN, PROSE_PATTERN, SHORT_MAKER_NAMES, VENDOR_PATTERN

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
    window = {'as_of': as_of, 'first_day': '2025-01-01', 'last_day': as_of, 'production_days': 485}
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
           'RUN_DAY': '2026-11-11', 'STUB_AS_OF': as_of, 'GITHUB_ENV': str(tmp_path / 'github_env')}   # never CI's own env file
    proc = subprocess.run(['bash', '-c', step_script(DEPLOY.read_text(encoding='utf-8'), 'Smoke test')],
                          env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)   # interleaved, as in the run log
    return proc.returncode, proc.stdout, log.read_text().split()


def test_smoke_test_reports_main_and_never_a_preview(tmp_path):
    """A tagged preview revision (pr-<n>---…) answers on the same service and sits in ORIGINS for the allow-list,
    but it runs a pull request's image: run 74 printed `Live at https://pr-91---…` and read pr-91's /api/config."""
    code, log, requested = smoke(tmp_path, up=[MAIN_HASH, MAIN, *PREVIEWS], live=[MAIN, MAIN_HASH], live_model='1')
    assert code == 0, log
    assert log.rstrip().endswith(f'Live at {MAIN}'), 'the deterministic run.app URL (the custom domain is the one the README publishes)'
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
    assert code == 0 and 'window 2025-01-01 to 2026-11-10 (as of 2026-11-10, 485 production days): ends yesterday' in log, log
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
    custom domain would have beaten the deterministic run.app URL. The priority is explicit, not ORIGINS order."""
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

    def run(as_of, last_day, run_day, today, first_day='2025-01-01'):
        health = tmp_path / 'health.json'
        health.write_text(json.dumps({'ok': True, 'store': {'source': 'prebuilt', 'window': {
            'as_of': as_of, 'first_day': first_day, 'last_day': last_day, 'production_days': 485}}}))
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
    code, out = run('2026-11-10', '2026-11-10', '2026-11-11', '2026-11-11', '2025-11-11')     # the old rolling year's start (issue #100)
    assert code == 1 and 'first_day is 2025-11-11, expected 2025-01-01' in out, out
    from investigator.synth import DATASET_FIRST_DAY
    assert DATASET_FIRST_DAY.isoformat() == '2025-01-01' in script.read_text()
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


def test_the_vendor_pattern_catches_the_names_the_owner_gave_and_not_a_base64_blob():
    for text in ("Production" + "_Center1", "the production" + "_center1 table", "B" + "HS-2", "B" + "HS2", "a b" + "hs line",
                 "BQMES" + "PAHT", "P0" + "4P-shaped", "Modul" + "eFacer", "H" + "QM1", "a" + "sm2", "K" + "QFSquareFootage",
                 "k" + "qf_sqft", "b" + "hs_line", "h" + "qm_total", "P0" + "4P_x", "col_a" + "sm", "Modul" + "e-Facer", "Modul" + "e_Facer"):
        assert VENDOR_PATTERN.search(text), text
    for text in ("x9B" + "HSq", "+B" + "HS/", "aB" + "HS", "CORR-2", "LINE-2", "wa" + "sm", "cutoff_knife", "stacker_waste_boards"):
        assert not VENDOR_PATTERN.search(text), text
    assert VENDOR_PATTERN.search("P0" + "4X-shaped") and VENDOR_PATTERN.search("p0" + "4a_code"), 'any record code of the family'


def test_the_maker_pattern_catches_every_maker_and_not_a_base64_blob_or_a_longer_word():
    """#120's case-study check listed the other corrugator makers literally; they are spelled in pieces now, and the
    repo-wide guard below reads them too (coordinator re-review of #123, item 2). A long name beside a '+' or '/' is still
    the name, as #120's word boundaries had it; only the two three-letter names keep the base64 rule."""
    for name in MAKER_NAMES:
        for text in (f"a {name} line", f"the {name.lower()} corrugator", f"{name.upper()}-2", f"{name}2", f"{name.lower()}_dryend"):
            assert MAKER_PATTERN.search(text), text
        for text in (f"x9{name}q", f"a{name}", f"{name}s"):
            assert not MAKER_PATTERN.search(text), text
    for name in LONG_MAKER_NAMES:
        for text in (f"+{name}/", f"see /{name}", f"https://x.com/{name}", f"{name}+X", f"A/{name}"):
            assert MAKER_PATTERN.search(text), text
    for name in SHORT_MAKER_NAMES:
        assert not MAKER_PATTERN.search(f"+{name}/"), name   # a blob's run of characters, not the name
    for text in ("CORR-2", "efficient", "simonize", "petersburg", "cutoff_knife", "stacker_waste_boards"):
        assert not MAKER_PATTERN.search(text), text


def test_the_case_study_pattern_keeps_120s_word_boundaries():
    """The case study is prose with no blob in it: #120 checked it between word boundaries, so a vendor's short name or
    a maker beside a '/' or '+' was caught there; PROSE_PATTERN keeps that (the reviewer of the #123 fix round)."""
    pairs = ("B" + "HS/" + "Fos" + "ber", "Kiwi" + "plan/" + "Am" + "tech", "see /" + "Fos" + "ber", "Fos" + "ber+" + "Ag" + "nati",
             "+B" + "HS/", "a P0" + "4X code", "T" + "CY/x", "x/E" + "FI")
    for text in pairs:
        assert PROSE_PATTERN.search(text), text
    for text in ("efficient", "simonize", "CORR-2", "the shear chops", "P04"):
        assert not PROSE_PATTERN.search(text), text


def test_no_tracked_file_names_the_vendor_or_its_source_tables():
    """A test greps the repo for the vendor name and fails if it appears (owner, 2026-11-11): code, docs, column
    names, comments and goldens, every tracked text file. No other corrugator maker's name appears either."""
    try:
        tracked = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT, capture_output=True, timeout=30, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip('git unavailable')
    hits = []
    for name in tracked.decode('utf-8').split('\0'):
        path = ROOT / name
        if not name or not path.is_file():
            continue
        try:
            text = path.read_text(encoding='utf-8')
        except (UnicodeDecodeError, OSError):
            continue          # binary files (fonts, images) carry no prose
        for pattern in (VENDOR_PATTERN, MAKER_PATTERN):
            hits += [f'{name}:{text.count(chr(10), 0, m.start()) + 1}: {m.group(0)}' for m in pattern.finditer(text)]
    assert hits == []


def test_the_deploy_fails_a_live_changelog_without_the_merged_pull_request(tmp_path):
    """Owner, 2026-11-12: the changelog is updated on every merge. The Changelog step before the build may not fail
    the deploy, so a step after the smoke test reads /docs/changelog from the live revision and fails the run when
    the merged pull request is missing. The check matches the link the docs renderer actually writes."""
    import sys
    from investigator.docsite import Renderer, changelog
    text = DEPLOY.read_text(encoding='utf-8')
    name = '- name: Changelog lists the merged pull request'
    assert text.index('- name: Smoke test') < text.index(name)
    step = text.split(name, 1)[1].split('- name:', 1)[0]
    assert 'continue-on-error' not in step and 'set -euo pipefail' in step
    assert 'curl -sS --max-time 30 -o /tmp/changelog.html "$LIVE/docs/changelog"' in step
    assert 'python3 .github/scripts/check_changelog.py /tmp/changelog.html "$(git log -1 --format=%s)"' in step
    smoke_step = text.split('- name: Smoke test', 1)[1].split('- name:', 1)[0]
    assert 'echo "LIVE=$LIVE" >> "${GITHUB_ENV:-/dev/null}"' in smoke_step
    assert smoke_step.index('GITHUB_ENV:-/dev/null') < smoke_step.index('echo "Live at')
    code, log, _ = smoke(tmp_path, up=[MAIN_HASH, MAIN])
    assert code == 0 and (tmp_path / 'github_env').read_text() == f'LIVE={MAIN}\n', 'the check reads the URL the smoke test reported'
    page = tmp_path / 'changelog.html'
    page.write_text(Renderer().render(changelog([('2026-11-12', 128, 'CLAUDE.md aligned with how the project runs (#101)')])))
    script = ROOT / '.github' / 'scripts' / 'check_changelog.py'

    def run(subject):
        proc = subprocess.run([sys.executable, str(script), str(page), subject], capture_output=True, text=True)
        return proc.returncode, proc.stdout

    assert run('Merge pull request #128 from justfetz/docs-claude-md-alignment') == (0, 'changelog lists #128\n')
    assert run('Merge pull request #128') == (0, 'changelog lists #128\n')        # a title edited at merge (#119 was)
    code, out = run('Merge pull request #12 from justfetz/x')                      # #128 on the page is not #12
    assert code == 1 and '#12' in out
    assert run('CLAUDE.md aligned (#128)')[0] == 0                                   # a squash merge
    code, out = run('Merge pull request #129 from justfetz/issue-129-x')
    assert code == 1 and '::error::' in out and '#129' in out
    code, out = run('Merge pull request #101 from justfetz/x')                     # #101 is only an issue link on the page
    assert code == 1, 'an issue link is not the pull request entry'
    assert run('Fix a typo on main')[0] == 0                                         # a direct push merges no pull request
    code, out = subprocess.run([sys.executable, str(script), str(tmp_path / 'missing.html'), 'Merge pull request #128 from a/b'],
                               capture_output=True, text=True).returncode, ''
    assert code == 1


# --- Issue #133: no job that holds the Google credential runs the pull request's code ---------------------------------
#
# A job that holds the Google credential may check the pull request out only as data, in a folder of its own (`pr/`),
# and hand it to base code: `gcloud run deploy --source pr`, and main's symlink check `preview_origins.py links pr`.
# Nothing else in the job may name that folder (a script, an env value such as PYTHONPATH or BASH_ENV, a `shell:`, a
# `with:` input such as a build context, a working directory, a local action), in any step: not after the auth step,
# where the credential file is on disk, and not before it either, since a process a step starts in the background
# outlives the step and a step can rewrite PATH or the environment of the steps after it. No script may fetch or check
# out the pull request another way (`refs/pull/`, `git checkout`, `gh pr checkout`, …) or interpolate what its author
# wrote (`${{ github.event.pull_request.title }}`, `github.head_ref`). The job's definition must not be the pull
# request's (`pull_request` runs the PR's copy of the workflow), the workspace root, where the auth step writes
# gha-creds-*.json, must be the base, and an upload (`gcloud run deploy`, `gcloud builds submit`) may only be a
# checkout of its own. The workflows are parsed by tests/workflow_yaml.py, since CI has no YAML library.
#
# A guard rail, not a proof: a script that reaches pr/ by a glob or by quoting (`p?/x`, `p""r`) is not found here.
# Review is what catches those; the guard catches the plain ways in, each shown below to be found.

import json as json_lib
import posixpath

import workflow_yaml

AUTH_ACTION = 'google-github-actions/auth@'
PR_EVENTS = {'pull_request', 'pull_request_review', 'pull_request_review_comment', 'merge_group'}   # these run the PR's code as the definition
BASE_REFS = {'${{ github.event.repository.default_branch }}', 'main', 'refs/heads/main'}
OTHER_FETCH = re.compile(r'refs/pull/|(?<![\w/])pull/[^\s/"\']+/(?:head|merge)\b|FETCH_HEAD|\bgit\s+(?:-C\s+\S+\s+)?(?:checkout|switch|worktree|reset|restore|pull|merge|am|apply|'
                         r'cherry-pick|rebase)\b|\bgh\s+pr\s+(?:checkout|diff)\b|pull_request\.head\.(?:sha|ref)|merge_commit_sha')
AUTHOR_TEXT = re.compile(r'\$\{\{[^}]*(?:github\.event\.pull_request\.(?:title|body|head\.ref|head\.label)|github\.head_ref|'
                         r'github\.event\.(?:comment|review|issue|commits|head_commit)\b)')


def workflow_events(workflow):
    on = workflow.get('on')
    return {on} if isinstance(on, str) else set(on or ())


# The stored key in any spelling (`secrets.GCP_SA_KEY`, `secrets['GCP_SA_KEY']`), and the whole secrets context, which
# holds it too (`toJSON(secrets)`, `secrets[matrix.name]`, a bare `secrets`). A job that names only another secret
# (the agents' Anthropic credential, HF_TOKEN) holds a credential, but not Google's: its own workflow's tests cover it.
GOOGLE_KEY = re.compile(r'secrets\s*(?:\.\s*GCP_SA_KEY\b|\[\s*[\'"]GCP_SA_KEY[\'"]\s*\])')
WHOLE_SECRETS = re.compile(r'\$\{\{[^}]*\bsecrets\b(?!\s*\.\s*\w|\s*\[\s*[\'"])')


def holds_google_credential(workflow, job):
    """The job authenticates to Google: the auth action, the stored key or the whole secrets context, or gcloud's login."""
    text = json_lib.dumps([job, workflow.get('env')]).replace('\\"', '"')
    return (any(str(s.get('uses') or '').startswith(AUTH_ACTION) for s in job.get('steps') or ())
            or GOOGLE_KEY.search(text) is not None or WHOLE_SECRETS.search(text) is not None
            or re.search(r'gcloud\s+auth\s+(?:activate-service-account|login)', text) is not None)


def checkouts(job, events):
    """(path, is_base, step) for each actions/checkout step. A checkout is the base when it is of this repository and
    its ref names main, or names none (or the run's own sha) and the event is not a pull request's: push, schedule and
    dispatch check out the commit the workflow itself came from, pull_request_target the default branch."""
    found = []
    for step in job.get('steps') or ():
        if str(step.get('uses') or '').startswith('actions/checkout@'):
            given = step.get('with') or {}
            ref = str(given.get('ref') or '').strip()
            path = posixpath.normpath(str(given.get('path') or '.'))
            base = not given.get('repository') and (
                ref in BASE_REFS or (ref in ('', '${{ github.sha }}') and not events & PR_EVENTS))
            found.append((path, base, step))
    return found


def inside(path, folders):
    path = posixpath.normpath(path.removeprefix('./'))
    return any(path == f or path.startswith(f + '/') for f in folders)


def names_path(folder):
    """`folder` as a path: `pr`, `pr/x`, `./pr`, `"pr"`, `$GITHUB_WORKSPACE/pr`, `PYTHONPATH=pr`; not `pr-41` or `$PR`."""
    return re.compile(r'(?<![\w.-])(?:\./)?' + re.escape(folder) + r'(?![\w.-])')


def allowed_mentions(folder):
    """The base code a script may hand the pull request's folder to, as data."""
    name = re.escape(folder)
    return re.compile(r'(gcloud run deploy\b[^\n]*?)--source[ =]["\']?(?:\./)?' + name + r'/?["\']?(?=\s|$)'
                      r'|(python3 \.github/scripts/preview_origins\.py links )' + name + r'(?=\s|$)')


def strings(value, where=''):
    """Every string in a parsed workflow value, with where it sits."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from strings(item, f'{where}.{key}' if where else str(key))
    elif isinstance(value, list):
        for n, item in enumerate(value):
            yield from strings(item, f'{where}[{n}]')
    elif isinstance(value, str):
        yield where, value


def uploads(script, workdir):
    """The directory each gcloud upload in a script sends to Cloud Build, resolved against its working directory."""
    found = []
    for line in script.replace('\\\n', ' ').splitlines():
        # gcloud [alpha|beta] run deploy, run jobs deploy, functions deploy: --source, else the working directory
        # (run: unless --image names a built image)
        if re.search(r'\bgcloud\s+(?:(?:alpha|beta)\s+)?(?:run\s+(?:jobs\s+)?deploy|functions\s+deploy)\b', line):
            source = re.search(r'--source[ =]["\']?([^\s"\']+)', line)
            if source or '--image' not in line:
                found.append(source.group(1) if source else '.')
        submit = re.search(r'\bgcloud\s+(?:(?:alpha|beta)\s+)?(?:builds\s+submit|app\s+deploy)\b(.*)', line)
        if submit:
            positional = [w for w in submit.group(1).split() if not w.startswith('-')]
            found.append(positional[0].strip('"\'') if positional else '.')
    return [posixpath.normpath(posixpath.join(workdir, source)) for source in found]


def credential_exposures(workflow):
    """Every way a job that holds the Google credential lets the pull request's code near it, as sentences."""
    events = workflow_events(workflow)
    problems = []
    for name, job in (workflow.get('jobs') or {}).items():
        if not holds_google_credential(workflow, job):
            continue
        steps = job.get('steps') or []
        if events & PR_EVENTS:
            problems.append(f'{name}: runs on {", ".join(sorted(events & PR_EVENTS))}, so its steps are the pull request\'s own copy')
        found = checkouts(job, events)
        pr_folders = [path for path, base, _ in found if not base]
        if '.' in pr_folders:
            problems.append(f'{name}: checks the pull request out at the workspace root, where the credential file is written '
                            'and every relative path is the pull request\'s')
        pr_folders = [path for path in pr_folders if path != '.']
        for path, base, step in found:
            if not base and (step.get('with') or {}).get('persist-credentials') is not False:
                problems.append(f'{name}: the checkout of the pull request in {path} keeps the job\'s GitHub token in its .git/config')
        folders = [path for path, _, _ in found if path != '.']
        default_dir = str(((job.get('defaults') or {}).get('run') or {}).get('working-directory') or '.')
        checkout_paths = {id(step): path for path, _, step in found}
        # Every string of the job, and the workflow's own env and defaults, other than a checkout's own path and an `if`.
        spots = [('workflow.' + where, where, text) for where, text in strings({'env': workflow.get('env'), 'defaults': workflow.get('defaults')})]
        spots += [(f'{name}.{where}', where, text) for where, text in strings({k: v for k, v in job.items() if k not in ('steps', 'if')})]
        for number, step in enumerate(steps, 1):
            label = f'{name}: step {number} ({step.get("name") or step.get("uses") or "run"})'
            for where, text in strings({k: v for k, v in step.items() if k != 'if'}):
                if where == 'with.path' and id(step) in checkout_paths:
                    continue
                spots.append((f'{label} {where}', where, text))
            if 'run' in step:
                workdir = str(step.get('working-directory') or default_dir)
                for upload in uploads(step['run'], workdir):
                    if upload not in folders:
                        problems.append(f'{label} uploads {upload}, not a checkout of its own: the workspace root holds the '
                                        'credential file the auth step wrote')
                if OTHER_FETCH.search(step['run']):
                    problems.append(f'{label} fetches or checks out code another way ({OTHER_FETCH.search(step["run"]).group(0)})')
        for where, field, text in spots:
            if AUTHOR_TEXT.search(text):
                problems.append(f'{where} interpolates what the pull request\'s author wrote: {AUTHOR_TEXT.search(text).group(0)}')
            for folder in pr_folders:
                rest = allowed_mentions(folder).sub(lambda m: m.group(1) or m.group(2), text) if field == 'run' else text
                if names_path(folder).search(rest):
                    problems.append(f'{where} names {folder}/ other than as data for main\'s code')
    return problems


def all_workflows():
    return {path.name: workflow_yaml.load(path.read_text(encoding='utf-8')) for path in sorted(WORKFLOWS.glob('*.yml'))}


def credential_jobs(workflows):
    return {(file, job) for file, wf in workflows.items() for job, spec in wf['jobs'].items() if holds_google_credential(wf, spec)}


def test_no_job_that_holds_the_google_credential_runs_the_pull_requests_code():
    """Issue #133 (H4): Preview ran the pull request's own .github/scripts/ after the credential was on disk."""
    workflows = all_workflows()
    assert credential_jobs(workflows) == {('deploy-cloudrun.yml', 'deploy'), ('preview.yml', 'deploy'), ('preview.yml', 'cleanup')}, \
        'a new job that holds the Google credential: check it here, and in the Workload Identity condition (docs/DEPLOYMENT.md)'
    problems = {file: credential_exposures(wf) for file, wf in workflows.items()}
    assert problems == {file: [] for file in workflows}, problems
    preview = workflows['preview.yml']
    assert workflow_events(preview) == {'pull_request_target'}, 'main\'s copy of preview.yml runs, never the pull request\'s'
    (pr_path, _, _), = [c for c in checkouts(preview['jobs']['deploy'], {'pull_request_target'}) if not c[1]]
    assert pr_path == 'pr', 'the pull request is checked out into pr/, as data'
    runs = [str(s.get('run')) for s in preview['jobs']['deploy']['steps']]
    links = runs.index('python3 .github/scripts/preview_origins.py links pr')
    auth = next(n for n, s in enumerate(preview['jobs']['deploy']['steps']) if str(s.get('uses')).startswith(AUTH_ACTION))
    deploy = next(n for n, r in enumerate(runs) if 'gcloud run deploy "$SERVICE" --source pr ' in r)
    assert links < auth < deploy, 'a symlink out of pr/ fails the run before the credential exists and before the upload'
    for job in ('head', 'deploy', 'cleanup'):
        condition = preview['jobs'][job]['if']
        assert 'github.event.pull_request.head.repo.full_name == github.repository' in condition, f'{job}: a fork\'s run gets the secrets'
        # what is previewed is the pull request merged onto main; the close job removes any same-repository PR's tag
        assert ('github.event.pull_request.base.ref == github.event.repository.default_branch' in condition) == (job != 'cleanup'), job
    head = preview['jobs']['head']
    assert head['permissions'] == {'contents': 'read', 'pull-requests': 'read'}
    assert not any(s.get('uses') for s in head['steps']), 'the head job checks out nothing and runs no action'


def test_only_the_three_deploy_jobs_can_mint_an_oidc_token():
    """H5: until the owner applies the pool's condition, any job with `id-token: write` can become the deployer; after,
    the condition refuses all but main's deploy-cloudrun.yml and preview.yml. A new holder is a decision, made here."""
    holders = set()
    for file, wf in all_workflows().items():
        for name, job in wf['jobs'].items():
            permissions = job['permissions'] if 'permissions' in job else wf.get('permissions')
            if permissions == 'write-all' or (isinstance(permissions, dict) and permissions.get('id-token') == 'write'):
                holders.add((file, name))
    assert holders == {('deploy-cloudrun.yml', 'deploy'), ('preview.yml', 'deploy'), ('preview.yml', 'cleanup')}, holders


def test_the_main_deploy_runs_main_only_and_uploads_its_checkout_not_the_credential_file():
    """Issue #133: a dispatch on another branch deployed that branch to production; and `--source .` from the workspace
    root uploaded the auth step's gha-creds-*.json (in key mode the deployer's key) to Cloud Build with the source."""
    deploy = all_workflows()['deploy-cloudrun.yml']['jobs']['deploy']
    assert deploy['if'] == "${{ vars.GCP_PROJECT_ID != '' && github.ref == 'refs/heads/main' }}"
    assert deploy['defaults'] == {'run': {'working-directory': 'app'}}
    assert [path for path, _, _ in checkouts(deploy, {'push'})] == ['app']


PRE_133_PREVIEW = """\
on:
  pull_request:
    types: [opened]
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: google-github-actions/auth@v2
        with:
          credentials_json: ${{ secrets.GCP_SA_KEY }}
      - name: Allow-list the preview origins
        run: python3 .github/scripts/preview_origins.py origins "$TAG" < /tmp/service.json
      - name: Deploy
        run: bash .github/scripts/cloud_run_update.sh gcloud run deploy "$SERVICE" --source . --no-traffic
"""

SAFE = """\
on:
  pull_request_target:
    types: [opened]
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.repository.default_branch }}
          persist-credentials: false
      - uses: actions/checkout@v4
        with:
          ref: ${{ needs.head.outputs.merge }}
          path: pr
          persist-credentials: false
      - uses: google-github-actions/auth@v2
        with:
          credentials_json: ${{ secrets.GCP_SA_KEY }}
      - name: Deploy
        run: |
          python3 .github/scripts/preview_origins.py origins "pr-$PR" < /tmp/service.json
          bash .github/scripts/cloud_run_update.sh gcloud run deploy "$SERVICE" --source pr --tag "pr-$PR" \\
            --no-traffic --quiet
"""


@pytest.mark.parametrize('change, finding', [
    (None, None),
    ('- name: Deploy\n        run: |\n', '- name: Deploy\n        run: |\n          python3 pr/.github/scripts/preview_origins.py\n'),
    ('- name: Deploy\n        run: |\n', '- name: Deploy\n        run: |\n          bash "$GITHUB_WORKSPACE/pr/x.sh"\n'),
    ('- name: Deploy\n        run: |\n', '- name: Deploy\n        run: |\n          cd pr && make\n'),
    ('- name: Deploy\n        run: |\n', '- name: Deploy\n        working-directory: pr\n        run: |\n'),
    ('- name: Deploy\n', '- uses: ./pr/.github/actions/setup\n      - name: Deploy\n'),
    ('- uses: google-github-actions/auth@v2\n', '- run: nohup python3 pr/watch.py &\n      - uses: google-github-actions/auth@v2\n'),
    ('--source pr --tag', '--source . --tag'),
    ('          path: pr\n          persist-credentials: false\n', '          path: pr\n'),
    ('ref: ${{ needs.head.outputs.merge }}\n          path: pr\n', 'ref: ${{ github.event.pull_request.head.sha }}\n'),
    ('pull_request_target:', 'pull_request:'),
    ('pull_request_target:', 'merge_group:'),
    # the review of PR #169's first head (issue #133): each of these passed the first version of the check
    ('- name: Deploy\n        run: |\n', '- run: git fetch origin "refs/pull/$PR/merge" && git checkout --detach FETCH_HEAD\n      - name: Deploy\n        run: |\n'),
    ('- name: Deploy\n        run: |\n', '- name: Deploy\n        env:\n          BASH_ENV: pr/evil.sh\n        run: |\n'),
    ('    runs-on: ubuntu-latest\n', '    runs-on: ubuntu-latest\n    env:\n      PYTHONPATH: pr\n'),
    ('jobs:\n', 'env:\n  PYTHONPATH: ./pr\njobs:\n'),
    ('- name: Deploy\n        run: |\n', '- name: Deploy\n        shell: bash pr/wrap.sh {0}\n        run: |\n'),
    ('- name: Deploy\n', '- uses: docker/build-push-action@v6\n        with:\n          context: pr\n      - name: Deploy\n'),
    ('- name: Deploy\n', '- uses: actions/github-script@v7\n        with:\n          script: require("./pr/x.js")\n      - name: Deploy\n'),
    ('ref: ${{ github.event.repository.default_branch }}\n', 'ref: ${{ github.event.repository.default_branch }}\n          repository: ${{ github.event.pull_request.head.repo.full_name }}\n'),
    ('--source pr --tag', '--tag'),
    ('          bash .github/scripts/cloud_run_update.sh gcloud run deploy', '          gcloud builds submit .\n          bash .github/scripts/cloud_run_update.sh gcloud run deploy'),
    ('--tag "pr-$PR"', '--tag "pr-$PR" --description "${{ github.event.pull_request.title }}"'),
    ('      - uses: google-github-actions/auth@v2\n        with:\n          credentials_json: ${{ secrets.GCP_SA_KEY }}\n',
     '      - run: echo "$KEY" > k.json && gcloud auth activate-service-account --key-file k.json && python3 pr/x.py\n        env:\n          KEY: ${{ secrets.GCP_SA_KEY }}\n'),
    # the coordinator's review of PR #170: spellings the second version missed
    ('      - uses: google-github-actions/auth@v2\n        with:\n          credentials_json: ${{ secrets.GCP_SA_KEY }}\n',
     "      - run: python3 pr/x.py\n        env:\n          KEY: ${{ secrets['GCP_SA_KEY'] }}\n"),
    ('      - uses: google-github-actions/auth@v2\n        with:\n          credentials_json: ${{ secrets.GCP_SA_KEY }}\n',
     '      - run: python3 pr/x.py\n        env:\n          ALL: ${{ toJSON(secrets) }}\n'),
    ('--source pr --tag', '--source pr --tag "pr-$PR"\n          gcloud beta run deploy "$SERVICE" --tag'),
    ('--source pr --tag', '--source pr --tag "pr-$PR"\n          gcloud run jobs deploy job --source .\n          echo --tag'),
    ('--source pr --tag', '--source pr --tag "pr-$PR"\n          gcloud functions deploy f --runtime python312\n          echo --tag'),
    ('--source pr --tag', '--source pr --tag "pr-$PR"\n          gcloud beta builds submit\n          echo --tag'),
    ('- name: Deploy\n        run: |\n', '- run: git fetch origin pull/41/head\n      - name: Deploy\n        run: |\n'),
])
def test_the_exposure_check_finds_each_way_in(change, finding):
    """The check is not vacuous: each edit of a safe job is found, and the safe job passes."""
    text = SAFE if change is None else SAFE.replace(change, finding, 1)
    assert change is None or text != SAFE, change
    problems = credential_exposures(workflow_yaml.load(text))
    assert bool(problems) == (change is not None), problems


def test_the_pre_133_preview_is_what_the_check_refuses():
    problems = credential_exposures(workflow_yaml.load(PRE_133_PREVIEW))
    assert any('pull_request, so its steps are the pull request\'s own copy' in p for p in problems), problems
    assert any('at the workspace root' in p for p in problems), problems


def test_the_workflow_reader_reads_every_workflow_as_pyyaml_does():
    yaml = pytest.importorskip('yaml')

    def on_key(value):   # YAML 1.1 reads the key `on` as true; the reader keeps it a string, as GitHub does
        if isinstance(value, dict):
            return {('on' if k is True else k): on_key(v) for k, v in value.items()}
        return [on_key(v) for v in value] if isinstance(value, list) else value

    for path in sorted(WORKFLOWS.glob('*.yml')):
        text = path.read_text(encoding='utf-8')
        assert workflow_yaml.load(text) == on_key(yaml.safe_load(text)), path.name
    for text in (SAFE, PRE_133_PREVIEW, "a: >-\n  x\n  y\n\n  z\n   w\nb: 'it''s'\nc: [x, y]  # a comment\nd:\n- 1\n- e: 2\n"):
        assert workflow_yaml.load(text) == on_key(yaml.safe_load(text))


@pytest.mark.parametrize('text', ['a: {x: 1}\n', 'a: b\n  c\n', "a: 'x\n  y'\n", 'a: &x 1\n', 'k:\n  - - 1\n', 'a: 1\na: 2\n',
                                  'run: "bash p\\x72/x.sh"\n', 'run: "\\u0070r"\n', "a: [x, 'y, z']\n", 'a: |+\n  x\n\nb: 1\n'])
def test_the_workflow_reader_refuses_what_it_does_not_read(text):
    with pytest.raises(ValueError):
        workflow_yaml.load(text)


def test_the_documented_pool_condition_trusts_mains_two_deploy_workflows_and_nothing_else():
    """Issue #133 (H5): the provider condition names the deploy and preview workflows by workflow_ref on main, in the
    create command and in the update-oidc command the owner runs, and maps every claim it reads (Google wants a mapping
    for each condition field: the Codex review of PR #170); neither workflow can be called as a reusable one, and both
    name main's copy because neither runs a pull request's own definition."""
    text = (ROOT / 'docs' / 'DEPLOYMENT.md').read_text(encoding='utf-8')
    conditions = re.findall(r'^CONDITION="(.+)"$', text, re.M)
    assert len(conditions) == 2 and len(set(conditions)) == 1, 'the create and the update command carry one condition'
    condition = conditions[0]
    mappings = re.findall(r'^MAPPING="(.+)"$', text, re.M)
    assert len(mappings) == 2 and len(set(mappings)) == 1, 'the create and the update command carry one mapping'
    mapped = {source.removeprefix('assertion.') for source in (pair.split('=', 1)[1] for pair in mappings[0].split(','))}
    read = set(re.findall(r'assertion\.(\w+)', condition))
    assert read == {'repository_id', 'repository_owner_id', 'workflow_ref'} and read <= mapped, (read, mapped)
    refs = re.findall(r"assertion\.workflow_ref == '([^']+)'", condition)
    assert refs == ['${REPO}/.github/workflows/deploy-cloudrun.yml@refs/heads/main', '${REPO}/.github/workflows/preview.yml@refs/heads/main']
    assert "assertion.repository_id == '1359219994'" in condition and "assertion.repository_owner_id == '12513697'" in condition
    assert 'assertion.repository ==' not in condition, 'a name alone follows a renamed or re-created repository'
    assert re.search(r'gcloud iam workload-identity-pools providers update-oidc github-oidc .*\n.*--attribute-condition "\$CONDITION"', text)
    assert re.search(r'providers create-oidc github-oidc [^`]*--attribute-condition "\$CONDITION"', text)
    assert "--attribute-condition \"assertion.repository == " not in text
    assert 'applies once the keyless cutover (#28, below) is live' in text
    workflows = all_workflows()
    named = {ref.split('@')[0].rsplit('/', 1)[1] for ref in refs}
    authenticating = {file for file, wf in workflows.items()
                      if any(str(s.get('uses') or '').startswith(AUTH_ACTION) for j in wf['jobs'].values() for s in j.get('steps') or ())}
    assert named == authenticating, 'every workflow that authenticates to Google is named, and only those'
    for file in named:
        events = workflow_events(workflows[file])
        assert 'workflow_call' not in events and not events & PR_EVENTS, (file, events)


def test_the_owner_is_told_to_end_the_stored_key_and_the_stale_branches_before_agents_run():
    """The coordinator's review of PR #170: a pull request between two branches cut before #133 runs their old
    preview.yml (on pull_request, the pull request's own scripts after auth), which in key mode holds GCP_SA_KEY. No
    file on main can change another branch, so the guard for it is the owner's list: "Fix before enabling" names the
    cutover's key deletion or an Environment for main only, and the stale branches; DEPLOYMENT.md lists them."""
    agents = (ROOT / 'docs' / 'AGENTS.md').read_text(encoding='utf-8')
    table = agents.split('## Fix before enabling `agent-ready`', 1)[1].split('\n## ', 1)[0]
    row = next(line for line in table.splitlines() if line.startswith('| **The stored key is gone'))
    assert 'delete `GCP_SA_KEY`' in row and 'Environment' in row and "`main` only" in row and 'stale' in row
    assert 'Nothing left' not in table, 'H4 is not closed on branches cut before #133'
    deployment = (ROOT / 'docs' / 'DEPLOYMENT.md').read_text(encoding='utf-8')
    assert '**Branches cut before #133 still carry the old Preview.**' in deployment
    assert "git show \"origin/$B:.github/workflows/preview.yml\" 2>/dev/null | grep -q '^  pull_request:$'" in deployment
    old = workflow_yaml.load(PRE_133_PREVIEW)   # what such a branch still holds
    assert workflow_events(old) == {'pull_request'} and credential_exposures(old)
