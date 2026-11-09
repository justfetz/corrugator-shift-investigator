"""Helpers for the per-pull-request preview deploy (.github/workflows/preview.yml, issue #41).

Cloud Run serves a tagged revision at ``https://<tag>---<service host>``. The server only
accepts POSTs whose ``Origin`` is on its exact-match allow-list (``PUBLIC_DEMO_ORIGIN``), so a
preview revision must be deployed with its own tag origins added to the list. This module turns
the service description ``gcloud run services describe --format=json`` prints into the values
the workflow needs. Pure functions, unit-tested in ``tests/test_preview_workflow.py``; the CLI
reads the JSON on stdin and prints ``NAME=value`` lines for ``$GITHUB_ENV``.

    preview_origins.py origins pr-41   -> BASE_ORIGINS=..., PREVIEW_ORIGINS=..., ORIGINS=...
    preview_origins.py url pr-41       -> PREVIEW_URL=...   (empty when the tag is not serving)
    preview_origins.py revisions SERVICE pr-41 [KEEP]  -> one revision name per line, oldest first

Runs on the GitHub runner's python3 with the standard library only.
"""
from __future__ import annotations

import json
import re
import sys

RUN_APP = re.compile(r'https://[a-z0-9.-]+\.run\.app')
TAG_SEPARATOR = '---'


def _env_value(service: dict, name: str) -> str:
    containers = (((service.get('spec') or {}).get('template') or {}).get('spec') or {}).get('containers') or []
    for container in containers:
        for item in container.get('env') or []:
            if item.get('name') == name:
                return item.get('value') or ''
    return ''


def base_origins(service: dict) -> list[str]:
    """Every origin the production service answers on, without tag URLs.

    The allow-list the current revision runs with (``PUBLIC_DEMO_ORIGIN`` in the template, the
    authoritative list, in its own order so ``origins[0]`` stays what production's is), followed
    by any ``*.run.app`` URL in ``status`` it lacks (the legacy hash form and the deterministic
    project-number form). Tag URLs (host contains ``---``), which an earlier preview may have
    left in either place, are dropped so the list cannot grow by one preview per pull request.
    """
    ordered = []
    for item in _env_value(service, 'PUBLIC_DEMO_ORIGIN').split(','):
        item = item.strip().rstrip('/')
        if item and item not in ordered:
            ordered.append(item)
    for item in sorted(set(RUN_APP.findall(json.dumps(service.get('status') or {})))):
        scheme, _, host = item.partition('://')
        if TAG_SEPARATOR in host:
            # A live tag URL names the host form Cloud Run actually uses for tags; keep its
            # untagged base so the pr-<n>--- form we add matches what the browser will send.
            item = f'{scheme}://{host.split(TAG_SEPARATOR, 1)[1]}'
        if item not in ordered:
            ordered.append(item)
    return [o for o in ordered if TAG_SEPARATOR not in o.split('://', 1)[-1]]


def preview_origins(service: dict, tag: str) -> list[str]:
    """The tag form of every base origin: ``https://host`` -> ``https://<tag>---host``."""
    out = []
    for origin in base_origins(service):
        scheme, _, host = origin.partition('://')
        out.append(f'{scheme}://{tag}{TAG_SEPARATOR}{host}')
    return out


def allow_list(service: dict, tag: str) -> str:
    """``PUBLIC_DEMO_ORIGIN`` for the preview revision: production first, so ``origins[0]``
    (the session-token seed when WORKBENCH_SECRET is unset) is the same as production's."""
    return ','.join(base_origins(service) + preview_origins(service, tag))


def preview_url(service: dict, tag: str) -> str:
    """The URL Cloud Run reports for the tag in ``status.traffic``, or '' when absent."""
    for target in (service.get('status') or {}).get('traffic') or []:
        if target.get('tag') == tag and target.get('url'):
            return target['url'].rstrip('/')
    return ''


def preview_revisions(revisions: list[dict], service: str, tag: str, keep: str = '') -> list[str]:
    """Names of this pull request's preview revisions (``<service>-<tag>-...``), oldest first,
    minus ``keep`` (the one just deployed). Revision names are chosen by the workflow with
    ``--revision-suffix``, so the prefix is exact."""
    prefix = f'{service}-{tag}-'
    named = []
    for revision in revisions:
        meta = revision.get('metadata') or {}
        name = meta.get('name') or ''
        if name.startswith(prefix) and name != keep:
            named.append((meta.get('creationTimestamp') or '', name))
    return [name for _, name in sorted(named)]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    command, args = argv[1], argv[2:]
    payload = json.load(sys.stdin)
    if command == 'origins' and len(args) == 1:
        tag = args[0]
        print(f'BASE_ORIGINS={",".join(base_origins(payload))}')
        print(f'PREVIEW_ORIGINS={",".join(preview_origins(payload, tag))}')
        print(f'ORIGINS={allow_list(payload, tag)}')
        return 0
    if command == 'url' and len(args) == 1:
        print(f'PREVIEW_URL={preview_url(payload, args[0])}')
        return 0
    if command == 'revisions' and 2 <= len(args) <= 3:
        service, tag = args[0], args[1]
        keep = args[2] if len(args) == 3 else ''
        for name in preview_revisions(payload, service, tag, keep):
            print(name)
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
