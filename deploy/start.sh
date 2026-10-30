#!/usr/bin/env bash
# Runs in the container: load any missing seed accounts, then the API and the voice agent side
# by side. If either stops, both stop, so the platform restarts the whole service.
set -euo pipefail

baatein migrate

if [[ -n "${BAATEIN_SEED_FILES:-}" ]]; then
  # The files' own passwords are public in the repo, so seeding needs a secret one. Without
  # it the app still starts; admins can load the files from Settings instead.
  if [[ -n "${BAATEIN_AUTH__SEED_PASSWORD:-}" ]]; then
    # shellcheck disable=SC2086  # a space-separated list of paths
    baatein seed $BAATEIN_SEED_FILES --if-missing
  else
    echo "BAATEIN_AUTH__SEED_PASSWORD is not set: not loading BAATEIN_SEED_FILES" >&2
  fi
fi

baatein serve --host 0.0.0.0 --port "${PORT:-8000}" &
api=$!
baatein-agent start &
agent=$!

# On a redeploy, SIGTERM lets the agent finish live calls (up to the platform's drain time).
stopping=0
stop() { kill -TERM "$api" "$agent" 2>/dev/null || true; }
trap 'stopping=1; stop' TERM INT

set +e
wait -n "$api" "$agent"
status=$?
stop
wait
# Asked to stop is a clean exit; a process dying on its own is a failure, so it restarts.
if (( stopping )); then exit 0; fi
exit "$status"
