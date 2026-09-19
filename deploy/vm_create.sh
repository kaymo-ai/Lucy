#!/usr/bin/env bash
# Creates the camp server. Run once.
#
# The VM is a pet, deliberately. The eval pipeline wants a machine that holds a
# database and runs batch jobs, and gluing four managed services together to
# avoid owning one box is a worse trade. The cost is that it can be down, which
# is why the app treats the backend as optional at every point.
set -euo pipefail

PROJECT=lucy-snails
ZONE=us-west1-b
REGION=us-west1
NAME=lucy-api

# Idempotent: the address may already be reserved.
gcloud compute addresses create "${NAME}-ip" --project "${PROJECT}" \
    --region "${REGION}" 2>/dev/null || true
IP=$(gcloud compute addresses describe "${NAME}-ip" --project "${PROJECT}" \
    --region "${REGION}" --format='value(address)')

gcloud compute firewall-rules create allow-lucy-web --project "${PROJECT}" \
    --allow tcp:80,tcp:443 --target-tags lucy-api \
    --source-ranges 0.0.0.0/0 2>/dev/null || true

# e2-small: 2 GB. Postgres, one uvicorn worker and Caddy fit with room.
# Debian rather than Container-Optimized OS because COS has no package manager
# and getting the compose plugin onto it is a fight for no benefit here.
if ! gcloud compute instances describe "${NAME}" --project "${PROJECT}" \
        --zone "${ZONE}" >/dev/null 2>&1; then
gcloud compute instances create "${NAME}" --project "${PROJECT}" \
    --zone "${ZONE}" --machine-type e2-small \
    --image-family debian-12 --image-project debian-cloud \
    --boot-disk-size 50GB --boot-disk-type pd-balanced \
    --address "${IP}" --tags lucy-api \
    --scopes https://www.googleapis.com/auth/devstorage.read_write \
    --metadata startup-script='#!/bin/bash
set -e
apt-get update
apt-get install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
    > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
systemctl enable --now docker
mkdir -p /opt/lucy/backend
touch /opt/lucy/.bootstrapped
'
fi

# Backups will go to the private bucket that already exists. The organisation
# enforces iam.disableServiceAccountKeyCreation, so this is the VM's attached
# service account via the metadata server -- there is no key file, and there
# cannot be one.
SA=$(gcloud compute instances describe "${NAME}" --project "${PROJECT}" \
    --zone "${ZONE}" --format='value(serviceAccounts[0].email)')
gcloud storage buckets add-iam-policy-binding gs://lucy-snails-backups \
    --member "serviceAccount:${SA}" --role roles/storage.objectAdmin \
    --project "${PROJECT}" >/dev/null

echo
echo "VM is up at ${IP}"
echo "DNS: lucy.marcusfoster.com must resolve here before Caddy can get a cert."
dig +short lucy.marcusfoster.com A
