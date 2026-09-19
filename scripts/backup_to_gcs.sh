#!/usr/bin/env bash
# Puts the irreplaceable parts of this project somewhere other than one laptop.
#
#   ./scripts/backup_to_gcs.sh            back up
#   ./scripts/backup_to_gcs.sh --list     what is up there, and when
#   ./scripts/backup_to_gcs.sh --restore  bring it all back down
#
# What goes up, and why each one:
#
#   Whatsapp PS Exports/   the raw chat history. Genuinely irreplaceable -- it
#                          cannot be regenerated from anything, and everything
#                          else in this project is derived from it.
#   PS Processed/          curated, hand-checked, and not cheaply redone.
#   ps_knowledge.db        106 MB, rebuildable from the above but only by
#                          spending frontier-model API calls again.
#
# It goes to gs://lucy-snails-backups, which is a DIFFERENT bucket from
# lucy-snails-releases and deliberately so: releases is public so phones can
# fetch the model without credentials, and this is the camp's entire WhatsApp
# history. Putting it in the public bucket would publish every message anyone
# in the camp has ever sent. Backups has public access prevention *enforced*,
# not merely unset.
#
# Object versioning is on, so an overwrite keeps the previous copy. A bad
# enrichment run is recoverable rather than final.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUCKET="gs://lucy-snails-backups"

case "${1:-}" in
--list)
    echo "== current =="
    gcloud storage ls -l "${BUCKET}/**" 2>/dev/null || echo "  (nothing yet)"
    echo
    echo "== previous versions of the database =="
    # Versioning keeps every overwrite; this is what a rollback picks from.
    gcloud storage ls -a -l "${BUCKET}/ps_knowledge.db" 2>/dev/null \
        || echo "  (none)"
    exit 0
    ;;
--restore)
    echo "Restoring into ${DIR}. Existing files are overwritten."
    read -r -p "Continue? [y/N] " ok
    [ "${ok}" = "y" ] || exit 1
    gcloud storage rsync -r "${BUCKET}/Whatsapp PS Exports" \
        "${DIR}/Whatsapp PS Exports"
    gcloud storage rsync -r "${BUCKET}/PS Processed" "${DIR}/PS Processed"
    gcloud storage cp "${BUCKET}/ps_knowledge.db" \
        "${DIR}/scripts/output/ps_knowledge.db"
    echo "Restored."
    exit 0
    ;;
esac

echo "==> raw sources"
# rsync rather than cp: these barely change, and re-uploading 31 MB every time
# is a way to stop running the backup.
gcloud storage rsync -r "${DIR}/Whatsapp PS Exports" \
    "${BUCKET}/Whatsapp PS Exports"
gcloud storage rsync -r "${DIR}/PS Processed" "${BUCKET}/PS Processed"

DB="${DIR}/scripts/output/ps_knowledge.db"
if [ -f "${DB}" ]; then
    echo "==> ps_knowledge.db ($(du -h "${DB}" | cut -f1))"
    gcloud storage cp "${DB}" "${BUCKET}/ps_knowledge.db"
fi

# A record of what was backed up and when, so a restore can be checked rather
# than assumed. Written last: if anything above failed, set -e means this never
# runs and the stamp cannot claim a backup that did not happen.
STAMP="$(mktemp -t backup)"
{
    echo "{"
    echo "  \"at\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\","
    echo "  \"commit\": \"$(git -C "${DIR}" rev-parse HEAD)\","
    if [ -f "${DB}" ]; then
        echo "  \"ps_knowledge_sha256\": \"$(shasum -a 256 "${DB}" | cut -d' ' -f1)\","
        echo "  \"ps_knowledge_bytes\": $(stat -f%z "${DB}"),"
    fi
    echo "  \"host\": \"$(hostname)\""
    echo "}"
} > "${STAMP}"
gcloud storage cp "${STAMP}" "${BUCKET}/backup-manifest.json" >/dev/null
rm -f "${STAMP}"

echo
echo "Backed up to ${BUCKET}."
gcloud storage cat "${BUCKET}/backup-manifest.json"
