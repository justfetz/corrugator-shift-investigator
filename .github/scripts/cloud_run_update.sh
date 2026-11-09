#!/usr/bin/env bash
# Runs one command that writes the Cloud Run service, and runs it again when Cloud Run rejected
# the write because another run changed the service first.
#
#   bash .github/scripts/cloud_run_update.sh gcloud run deploy "$SERVICE" --source . ...
#
# Cloud Run updates are optimistically locked. gcloud reads the service, builds the image when
# deploying from source, reads the service AGAIN (its own comment: "building takes a while which
# leaves a long time for potential write conflicts") and writes it back naming the version it
# just read. The window is the second between that read and the write; when another run's write
# lands in it, Cloud Run answers
#
#   ERROR: (gcloud.run.deploy) ABORTED: Conflict for resource '<service>': version '...' was
#   specified but current version is '...'.
#
# and gcloud exits 1 without retrying. That message, and nothing else, is what is retried. The main deploy and every pull request's preview runs
# (deploy and close) write the one service, and GitHub serializes runs only within one pull
# request, so runs of two pull requests do cross: PR #50's preview failed this way after its
# build while PR #54's close job was removing its tag. A rejected write changed nothing, so the
# command is run again as it was: gcloud reads the current version, and a --source deploy
# rebuilds, about a minute. Bounded: CLOUD_RUN_UPDATE_ATTEMPTS tries in all (default 4), with a
# growing pause plus jitter between them (CLOUD_RUN_UPDATE_PAUSE seconds, default 15, times the
# attempt) so two runs that collided do not retry in step. Any other failure, and the last
# rejection, exit with the command's own status. The command's output streams through, so the
# step log reads as before, with one ::notice:: line per retry. tests/test_preview_workflow.py
# runs this under bash against a stand-in gcloud.
set -uo pipefail

attempts="${CLOUD_RUN_UPDATE_ATTEMPTS:-4}"
pause="${CLOUD_RUN_UPDATE_PAUSE:-15}"
log="$(mktemp)"
trap 'rm -f "$log"' EXIT

attempt=1
while :; do
  "$@" 2>&1 | tee "$log"
  status=${PIPESTATUS[0]}
  if [ "$status" -eq 0 ]; then exit 0; fi
  if ! grep -qE 'ABORTED: Conflict for resource|was specified but current version is' "$log"; then exit "$status"; fi
  if [ "$attempt" -ge "$attempts" ]; then
    echo "::warning::attempt ${attempt} of ${attempts} was still rejected: another run kept changing the Cloud Run service; re-run this job"
    exit "$status"
  fi
  wait=$(( pause * attempt + (pause > 0 ? RANDOM % pause : 0) ))
  echo "::notice::another run changed the Cloud Run service first; attempt ${attempt} of ${attempts} was rejected, running it again in ${wait}s"
  sleep "$wait"
  attempt=$(( attempt + 1 ))
done
