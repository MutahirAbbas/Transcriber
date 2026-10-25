#!/usr/bin/env bash
# On the server, as the deploy user: pull the latest code, rebuild and restart, then wait
# for the app's healthcheck.
#
#   /srv/call-analyzer/app/deploy/deploy.sh
#
# Only ever touches compose project "call-analyzer".
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE=(docker compose -p call-analyzer -f deploy/docker-compose.yml)

[[ -f /srv/call-analyzer/.env ]] || { echo "Missing /srv/call-analyzer/.env (see deploy/.env.example)" >&2; exit 1; }

git pull --ff-only
echo "Deploying $(git log --oneline -1)"
"${COMPOSE[@]}" up -d --build

printf "Waiting for the app to report healthy"
state=starting
for _ in $(seq 60); do
  state=$(docker inspect -f '{{.State.Health.Status}}' "$("${COMPOSE[@]}" ps -q app)" 2>/dev/null || echo starting)
  [[ "$state" == healthy || "$state" == unhealthy ]] && break
  printf "."
  sleep 2
done
echo " $state"

"${COMPOSE[@]}" ps
if [[ "$state" != healthy ]]; then
  "${COMPOSE[@]}" logs --tail 50 app
  exit 1
fi
