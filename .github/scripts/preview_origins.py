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
    preview_origins.py orphans SERVICE SERVICE_JSON [KEEP] < revisions.json
                                       -> every pr-* revision with no tag and no traffic, oldest first,
                                          "name" or "name<TAB>latest" when Cloud Run will refuse to delete it yet
    preview_origins.py links TREE      -> exit 1, naming each one, if a symlink in TREE resolves outside it

The service description is data a preview can change (a pull request's code runs as the build's and the
runtime's service accounts), so every value printed for $GITHUB_ENV must be an https origin or URL and
nothing else; anything else, a newline above all, fails the step (issue #133). ``links`` reads the pull
request's tree, never runs it: gcloud uploads a symlinked file's target, so a link out of the tree would
carry a runner file (the credential gcloud keeps) to Cloud Build.

Runs on the GitHub runner's python3 with the standard library only.
"""
from __future__ import annotations

import json
import os
import re
import sys

RUN_APP = re.compile(r'https://[a-z0-9.-]+\.run\.app')
ORIGIN = re.compile(r'https://[a-z0-9.-]+(?::[0-9]+)?')
TAG_SEPARATOR = '---'


def checked(name: str, values: list[str]) -> list[str]:
    """``values`` unchanged when each is an https origin; otherwise SystemExit naming the first that is not."""
    for value in values:
        if not ORIGIN.fullmatch(value):
            raise SystemExit(f'::error::{name} would carry {ascii(value)}, which is not an https origin; '
                             'nothing was written (the service description is data a preview can change)')
    return values


def outward_links(tree: str) -> list[tuple[str, str]]:
    """(path, link text) for each symlink under ``tree`` (its .git excluded) that is absolute, or whose target,
    followed to the end, is outside ``tree``. An absolute link is refused by its text alone, whatever it resolves
    to here and now; a relative one is followed. Nothing is followed while walking."""
    root = os.path.realpath(tree)
    found = []
    for dirpath, dirnames, filenames in os.walk(tree):
        if dirpath == tree and '.git' in dirnames:
            dirnames.remove('.git')
        for name in dirnames + filenames:
            path = os.path.join(dirpath, name)
            if os.path.islink(path):
                text = os.readlink(path)
                target = os.path.realpath(path)
                if os.path.isabs(text) or (target != root and not target.startswith(root + os.sep)):
                    found.append((os.path.relpath(path, tree), text))
    return sorted(found)


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


PREVIEW_NAME = re.compile(r'^(?P<service>.+)-pr-\d+-')


def orphaned_preview_revisions(revisions: list[dict], service: dict, service_name: str, keep: str = '') -> list[tuple[str, bool]]:
    """Every preview revision of any pull request that nothing references any more.

    A revision is an orphan when its name is ``<service>-pr-<n>-...``, no traffic target names it
    (no tag, no percent), and it is not ``keep`` (the one just deployed). Cloud Run refuses to
    delete a service's latest created (or latest ready) revision, which a fresh preview always is
    until the next deploy of any kind; those are returned flagged ``True`` so the workflow can
    say so instead of failing, and the next run deletes them. Oldest first."""
    status = service.get('status') or {}
    referenced = {t.get('revisionName') for t in status.get('traffic') or [] if t.get('tag') or t.get('percent')}
    latest = {status.get('latestCreatedRevisionName'), status.get('latestReadyRevisionName')} - {None}
    out = []
    for revision in revisions:
        meta = revision.get('metadata') or {}
        name = meta.get('name') or ''
        m = PREVIEW_NAME.match(name)
        if not m or m.group('service') != service_name or name == keep or name in referenced:
            continue
        out.append((meta.get('creationTimestamp') or '', name, name in latest))
    return [(name, is_latest) for _, name, is_latest in sorted(out)]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    command, args = argv[1], argv[2:]
    if command == 'links' and len(args) == 1:
        links = outward_links(args[0])
        for path, target in links:
            print(f'::error::{ascii(path)} is a symlink to {ascii(target)}, outside the pull request\'s tree; '
                  'gcloud would upload what it points to')
        return 1 if links else 0
    payload = json.load(sys.stdin)
    if command == 'origins' and len(args) == 1:
        tag = args[0]
        base = checked('BASE_ORIGINS', base_origins(payload))
        preview = checked('PREVIEW_ORIGINS', preview_origins(payload, tag))
        print(f'BASE_ORIGINS={",".join(base)}')
        print(f'PREVIEW_ORIGINS={",".join(preview)}')
        print(f'ORIGINS={",".join(base + preview)}')
        return 0
    if command == 'url' and len(args) == 1:
        url = preview_url(payload, args[0])
        print(f'PREVIEW_URL={",".join(checked("PREVIEW_URL", [url])) if url else ""}')
        return 0
    if command == 'revisions' and 2 <= len(args) <= 3:
        service, tag = args[0], args[1]
        keep = args[2] if len(args) == 3 else ''
        for name in preview_revisions(payload, service, tag, keep):
            print(name)
        return 0
    if command == 'orphans' and 2 <= len(args) <= 3:
        service_name, service_path = args[0], args[1]
        keep = args[2] if len(args) == 3 else ''
        with open(service_path) as fh:
            service = json.load(fh)
        for name, is_latest in orphaned_preview_revisions(payload, service, service_name, keep):
            print(f'{name}\tlatest' if is_latest else name)
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
