#!/usr/bin/env python3
"""The deploy's changelog check (owner, 2026-11-12: the changelog is updated on every merge).

    python3 .github/scripts/check_changelog.py <changelog page> <head commit subject>

<changelog page> is the body of GET /docs/changelog from the revision now live. <head commit subject>
is `git log -1 --format=%s` of the commit deployed. When that subject is a pull request's merge
("Merge pull request #N from ...") or a squash ("... (#N)"), the page must link to pull request N.
Any other subject (a direct push) has no pull request to look for and passes.

The Changelog step before the build may not fail the deploy, so this check runs after the revision is
live: visitors keep the new revision either way, and a missing entry turns the run red instead of
shipping a stale page silently. Prints what it checked and exits 0, or names what is missing and exits 1.
"""
import re
import sys

REPO = 'https://github.com/justfetz/corrugator-shift-investigator'


def pull_number(subject):
    """The pull request a head commit subject merges, or None."""
    merge = re.match(r'Merge pull request #(\d+) from \S+', subject or '')
    squash = re.search(r'\(#(\d+)\)\s*$', subject or '')
    match = merge or squash
    return int(match.group(1)) if match else None


def check(page, subject):
    """[] when the page lists the subject's pull request (or the subject merges none), else the failure."""
    number = pull_number(subject)
    if number is None:
        return []
    if f'href="{REPO}/pull/{number}"' in (page or ''):
        return []
    return [f'/docs/changelog does not list pull request #{number}, the merge this run deployed ({subject!r})']


def main(argv):
    page_path, subject = argv[1], argv[2]
    try:
        page = open(page_path, encoding='utf-8').read()
    except OSError as error:
        print(f'::error::changelog page unreadable: {error}')
        return 1
    failures = check(page, subject)
    for failure in failures:
        print(f'::error::{failure}')
    if not failures:
        number = pull_number(subject)
        print(f'changelog lists #{number}' if number else f'no pull request in {subject!r}; nothing to look for')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
