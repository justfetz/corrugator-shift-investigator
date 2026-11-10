"""Guards for the Cloud Run workflows and the documentation tree (issue #28).

The deploy of `main` and the per-pull-request preview authenticate to Google the same way:
Workload Identity Federation when the two repository variables are set, the stored `GCP_SA_KEY`
secret with a warning until then, and a failure with the instruction only when neither is
available. The docs must not link to files that were moved. Plain text checks, plus the
credential-choice step's own shell run under bash for all eight combinations of the two variables
and the secret, so no YAML dependency.
"""
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
    import yaml
    workflow = yaml.safe_load(DEPLOY.read_text())
    triggers = workflow[True] if True in workflow else workflow['on']   # YAML 1.1 reads the key `on` as True
    assert triggers['push'] == {'branches': ['main']} and 'workflow_dispatch' in triggers
    assert triggers['schedule'] == [{'cron': '0 9 * * 1-5'}]


def test_the_image_prebuilds_the_demo_year_and_the_golden_year():
    """The demo's window ends on the build's yesterday; the /evals page scores against the golden
    window, whose as-of is read from golden_values.json so the two can never disagree."""
    dockerfile = (ROOT / 'Dockerfile').read_text()
    assert 'python -m investigator.prebuilt /app/prebuilt ' in dockerfile
    assert ('python -m investigator.prebuilt --as-of "$(python -c \'import json; print(json.load(open("evals/golden_values.json"))["as_of"])\')" '
            '/app/prebuilt-golden') in dockerfile
    assert 'INVESTIGATOR_PREBUILT=/app/prebuilt ' in dockerfile and 'INVESTIGATOR_GOLDEN_PREBUILT=/app/prebuilt-golden' in dockerfile
    assert not any('INVESTIGATOR_AS_OF' in line for line in dockerfile.splitlines() if line.startswith(('ENV', 'ARG')))   # never pinned
