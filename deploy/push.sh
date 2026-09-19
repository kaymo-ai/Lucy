#!/usr/bin/env bash
# Copies the backend and the camp site to the VM and restarts the stack.
# Run for every deploy.
#
# No container registry: this is one box and one developer, and a registry is a
# second set of credentials to keep. The source goes up and builds there.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT=lucy-snails
ZONE=us-west1-b
NAME=lucy-api

# .env is never copied. It lives on the VM and only on the VM.
#
# COPYFILE_DISABLE and the ._* exclusion are both load-bearing: macOS tar
# otherwise writes an AppleDouble sidecar beside every file, `._001_init.sql`
# matches the migration runner's *.sql glob, and the api container dies at
# startup on a UnicodeDecodeError that names a byte offset and nothing useful.
COPYFILE_DISABLE=1 tar \
    --exclude .venv --exclude pgdata --exclude media --exclude caddy_data \
    --exclude caddy_config --exclude .env --exclude __pycache__ \
    --exclude .pytest_cache --exclude '._*' \
    -czf /tmp/lucy-backend.tgz -C "${DIR}" backend site

gcloud compute scp /tmp/lucy-backend.tgz "${NAME}:/tmp/" \
    --project "${PROJECT}" --zone "${ZONE}"

gcloud compute ssh "${NAME}" --project "${PROJECT}" --zone "${ZONE}" --command '
set -e
sudo mkdir -p /opt/lucy
sudo tar -xzf /tmp/lucy-backend.tgz -C /opt/lucy
# The site is copied INTO the mounted directory, never over it.
#
# ./site is bind-mounted into the api container, and a bind mount follows the
# inode, not the path. `rm -rf site && mv newsite site` therefore leaves the
# running container looking at the deleted directory: the files are on the
# host, /srv/site inside the container is empty, and the deploy reports
# success. It only ever appeared to work on the deploys where the container
# happened to be recreated for another reason.
sudo mkdir -p /opt/lucy/backend/site
sudo find /opt/lucy/backend/site -mindepth 1 -delete
sudo cp -a /opt/lucy/site/. /opt/lucy/backend/site/
sudo rm -rf /opt/lucy/site
cd /opt/lucy/backend
sudo docker compose up -d --build
# The Caddyfile is a bind mount, so editing it changes nothing on its own --
# compose sees no change to the caddy service and leaves the old config
# loaded. This cost a deploy that looked successful and served the previous
# rules. A reload is cheap and does not drop the certificate.
sudo docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile \
    2>&1 | tail -2 || sudo docker compose restart caddy
sudo docker compose ps --format "{{.Service}} {{.State}}"
'
