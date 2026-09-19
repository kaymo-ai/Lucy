#!/usr/bin/env bash
# The whole corpus rebuild, in the one order that works.
#
# Encoded here rather than in prose because the ordering is not a preference
# and every constraint below was learned by breaking it:
#
#   ingest_all recreates ps_knowledge.db from scratch, so it is first and
#   everything else appends to what it wrote.
#
#   dedupe_people MUST come before enrichment. It deletes non-people and the
#   person_content they wrote, and enrichment's `evidence` rows cite
#   person_content by id -- run it after and 247 evidence rows point at
#   deleted messages, dropping those leaves 49 claims unevidenced, and
#   verify_evidence_complete refuses the artifact. Measured 2026-08-20.
#
#   entities before entity_records: pass 2 reads the entities pass 1 found.
#   people before personality and pronouns: both rewrite or extend
#   person_profile, which enrich_people writes.
#   ask_vocab last: it generates the words people would ASK with for
#   camp_fact rows and entity aliases, so both have to exist.
#
#   embed_claims after everything that writes a claim, because it embeds
#   camp_fact, entity_fact and lore -- and check_shipped_db now refuses a
#   database whose vector coverage is partial, so a forgotten run fails loudly
#   rather than shipping half-blind retrieval.
#
# Every enrichment pass takes --yes to skip its confirmation. They cost money.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${HERE}/.venv/bin/python"
DB="${HERE}/output/ps_knowledge.db"
EXPORTS="${EXPORTS:-~/Snails/Whatsapp PS Exports}"
MODEL="${MODEL:-~/Snails/scripts/models/embeddinggemma-300M-Q8_0.gguf}"

step() { printf '\n\033[1;33m=== %s ===\033[0m\n' "$*"; }

step "1/12  ingest: exports and documents into a fresh corpus"
"${PY}" "${HERE}/ingest_all.py" "${EXPORTS}" "${HERE}/output"

step "2/12  dedupe people, and record what they are called"
"${PY}" "${HERE}/dedupe_people.py"

step "3/12  build camp_member and the rota from the sign-up sheets"
# --install is what writes camp_member INTO the knowledge database.
# Without it build_people writes output/people.db and a CSV that
# nothing downstream reads, and camp_member stays empty -- which is
# how the roster was missing from every build until 2026-08-20.
"${PY}" "${HERE}/build_people.py" --install || echo "(build_people skipped)"
# The rota is read as a grid, not as rows -- see parse_shift_grid.py. It must
# run after stage 1, which recreates the corpus file and would drop the table,
# and before stage 12, which packages it.
"${PY}" "${HERE}/parse_shift_grid.py" "${DB}" || echo "(no shift grid found)"

step "4/12  pass 1: entities"
"${PY}" "${HERE}/enrich_entities.py" "${DB}" --yes

step "5/12  pass 2: entity records"
"${PY}" "${HERE}/enrich_entity_records.py" "${DB}" --yes

step "6/12  pass 3: documents into camp_fact"
"${PY}" "${HERE}/enrich_documents.py" "${DB}" --yes

step "7/12  pass 3: people profiles and expertise"
"${PY}" "${HERE}/enrich_people.py" "${DB}" --yes

step "8/12  personality portraits"
"${PY}" "${HERE}/enrich_personality.py" "${DB}" --yes

step "9/12  pronouns: they/them in person_profile"
"${PY}" "${HERE}/enrich_pronouns.py" "${DB}" --yes

step "10/12 lore: the stories the camp tells"
"${PY}" "${HERE}/enrich_lore.py" "${DB}" --yes

step "11/12 pass 5: ask vocabulary and entity aliases"
"${PY}" "${HERE}/enrich_ask_vocab.py" "${DB}" --yes

step "12/12 manual facts, embed the claim layer, then package and gate"
# Manual facts BEFORE embed_claims, so the curated rows get vectors like every
# other claim -- check_shipped_db refuses partial coverage.
"${PY}" "${HERE}/ingest_manual_facts.py" "${DB}"
"${PY}" "${HERE}/embed_claims.py" "${DB}" "${MODEL}"
"${PY}" "${HERE}/build_preview_db.py"
"${PY}" "${HERE}/check_shipped_db.py" "${HERE}/output/enriched_preview.db"

printf '\n\033[1;32mDone.\033[0m\n'
