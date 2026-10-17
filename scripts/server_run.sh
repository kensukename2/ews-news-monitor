#!/usr/bin/env bash
# Daily job on the Hetzner server, started by the systemd timer
# deploy/ews-news.timer at 05:00 Europe/Zurich.
#
#   1. update the code from GitHub (main branch)
#   2. update the saved state (data branch, checked out in state/)
#   3. run the pipeline for all topics (EWS News, AI News)
#   4. save the new state to the data branch
#   5. publish the site to the gh-pages branch (served at knaito.xyz)
#
# Run by hand:  sudo systemctl start ews-news.service
# Logs:         journalctl -u ews-news.service   (and state/logs/ for run details)
set -uo pipefail
cd "$(dirname "$0")/.."

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

log "updating code"
if git pull --ff-only --quiet origin main; then
  .venv/bin/pip install --quiet -r requirements.txt || log "WARNING: pip install failed"
else
  log "WARNING: could not update the code; running the current version"
fi

log "updating saved state (data branch)"
git -C state pull --rebase --quiet origin data || log "WARNING: could not update the data branch"

log "running pipeline"
.venv/bin/python -m ews_news run
status=$?
log "pipeline finished with exit code $status"

log "saving state to the data branch"
rm -f state/published/*.json
for page in ews_news ai_news; do
  if [ -d "site/$page/data" ]; then
    mkdir -p "state/published/$page"
    cp -r "site/$page/data/." "state/published/$page/"
  fi
done
(
  cd state
  git add -A
  if ! git diff --cached --quiet; then
    git commit --quiet -m "Data update $(date -u +%Y-%m-%dT%H:%MZ)"
    git pull --rebase --quiet origin data && git push --quiet origin data
  fi
) || log "WARNING: could not push the data branch"

log "publishing site"
scripts/publish_pages.sh || { log "ERROR: publishing failed"; exit 1; }

exit $status
