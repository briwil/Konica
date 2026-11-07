#!/usr/bin/env bash
# Runs at boot (konica-update.service): if there's network, pull new code from
# GitHub and restart the camera service. Without network it gives up quietly;
# the camera starts on its own regardless and never waits for this.
set -uo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIR" || exit 0

if [ ! -d .git ]; then
  echo "$DIR is not a git checkout; auto-update skipped"
  exit 0
fi

# Wait up to ~60 s for GitHub to be reachable.
for _ in $(seq 1 30); do
  if git ls-remote --exit-code origin HEAD >/dev/null 2>&1; then
    online=1
    break
  fi
  sleep 2
done
if [ -z "${online:-}" ]; then
  echo "No network; not updating"
  exit 0
fi

before=$(git rev-parse HEAD)
if ! git fetch --quiet origin; then
  echo "Fetch failed; not updating"
  exit 0
fi
# Only fast-forward. If code was edited on the Pi, leave it alone.
if ! git merge --ff-only --quiet '@{u}'; then
  echo "Local changes on the Pi conflict with GitHub; not updating"
  exit 0
fi
after=$(git rev-parse HEAD)

if [ "$before" = "$after" ]; then
  echo "Already up to date ($(git log -1 --format='%h %s'))"
  exit 0
fi

echo "Updated $(git log -1 --format=%h "$before") -> $(git log -1 --format='%h %s')"
sudo -n systemctl restart konica-back
