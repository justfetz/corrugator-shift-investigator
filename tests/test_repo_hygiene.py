"""Guards for the deploy workflow and the documentation tree (issue #28).

The Cloud Run deploy is keyless: it must never read a stored Google credential again, and the
docs must not link to files that were moved. Plain text checks, so no YAML dependency.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / '.github' / 'workflows' / 'deploy-cloudrun.yml'


def test_cloud_run_deploy_is_keyless():
    text = DEPLOY.read_text(encoding='utf-8')
    assert 'secrets.GCP_SA_KEY' not in text and 'credentials_json' not in text, 'the deploy must not use a stored key'
    assert 'workload_identity_provider: ${{ vars.GCP_WORKLOAD_IDENTITY_PROVIDER }}' in text
    assert 'service_account: ${{ vars.GCP_DEPLOYER_SERVICE_ACCOUNT }}' in text
    assert re.search(r'^permissions:\n(?:  .*\n)*?  id-token: write', text, re.M), 'WIF needs id-token: write on the job'
    assert re.search(r'^\s+secrets\.', text, re.M) is None, 'no GitHub secret of any kind on the Cloud Run path'


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
