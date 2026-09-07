#!/usr/bin/env bash
# Block until a workflow has a completed run for a commit, then exit 0 only if
# it succeeded. Used as a release gate: the version-bump tag (auto-tag.yaml),
# the PyPI publish (publish-pypi.yaml) and the Docker release
# (release-docker.yaml) each wait for "Test jvagent" on the same commit, so a
# red test run can no longer ship a release (0.1.8rc7 did, while jvchat's npm
# install hung — the publish path did not look at the tests at all).
#
#   scripts/ci/wait_for_workflow.sh "<workflow name>" <sha> [max_minutes]
#
# Needs GH_TOKEN with actions:read and GH_REPO (or a checkout with a remote).
# A run that was re-run counts by its latest attempt's conclusion.
set -euo pipefail

workflow="${1:?workflow name}"
sha="${2:?commit sha}"
if [ "${#sha}" -lt 40 ]; then
  echo "::error::need the full 40-character commit SHA, got '${sha}'"; exit 2
fi
max_minutes="${3:-45}"
deadline=$(( $(date +%s) + max_minutes * 60 ))

echo "waiting for '${workflow}' on ${sha:0:8} (up to ${max_minutes} min)"
while :; do
  # Newest run for this commit and workflow; conclusion is null while running.
  # ``--commit`` wants the full 40-char SHA (a short one matches nothing).
  row=$(gh run list --workflow "$workflow" --commit "$sha" --limit 1 \
          --json status,conclusion,databaseId,url \
          --jq '(.[0] // {}) | [(.status // "none"), (.conclusion // "none"), ((.databaseId // 0)|tostring), (.url // "")] | join(" ")' 2>/dev/null || true)
  status=$(echo "$row" | awk '{print $1}')
  conclusion=$(echo "$row" | awk '{print $2}')
  url=$(echo "$row" | awk '{print $4}')
  if [ "$status" = "completed" ]; then
    case "$conclusion" in
      success)
        echo "'${workflow}' succeeded: $url"; exit 0 ;;
      *)
        echo "::error::'${workflow}' concluded '${conclusion}' for ${sha:0:8}: $url"
        echo "::error::Re-run it (gh run rerun <id> --failed) and re-run this job; nothing was released."
        exit 1 ;;
    esac
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "::error::'${workflow}' did not complete for ${sha:0:8} within ${max_minutes} min (status='${status:-none}'; a short SHA or a commit without a run looks the same)"
    exit 1
  fi
  sleep 30
done
