# Enrichment Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn 24,192 raw chat messages and 616 documents into evidence-linked knowledge — entities, person profiles, expertise, relationships and lore — so Lucy can answer "what is Doris" with a synthesized record instead of quoting someone asking the same question.

**Architecture:** Build-time only, on the Mac. Frontier-model passes over the corpus write derived rows into new SQLite tables, every claim linked to the source message or document it came from. The phone never runs any of this; it retrieves conclusions that were already drawn. Runs on Gemini via Vertex AI so it draws on the project's GCP credits, with structured output so the schema is guaranteed rather than parsed and retried, and a bounded concurrency pool sized to the ~650 requests the whole pipeline needs.

**Tech Stack:** Python 3.14, `google-genai` 2.17.0, Gemini on Vertex AI (`global` endpoint), bounded concurrency pool, structured output, SQLite, pytest.

## Global Constraints

- **Project: `lucy-snails`** (number 1024837628544). `aiplatform.googleapis.com` is enabled and ADC's quota project is set to it. Not `kaymo-production`.
- **Model: ~~`gemini-3.1-pro-preview`~~ `gemini-3.6-flash`.** *Superseded 2026-08-20.* The Pro choice was made because this pass reads ambiguous human conversation and capability was judged to matter more than throughput. 3.6 Flash is near-Pro at Flash cost and latency, which is the better trade for ~650 requests.
- **Region: ~~`location="global"`~~ `us-central1`.** *Superseded 2026-08-20 — the finding below expired.* Marcus confirmed 3.6 Flash serving from `us-central1` against the console. **Regional availability moves in weeks, so read every probe result here as a fact about its date and nothing later.** The original measurement, for the record: `client.models.list()` returns 25 Gemini models, but that list is *not* region-filtered. Live probes against `us-central1` returned 404 for `gemini-3.1-pro-preview`, `gemini-3.6-flash`, and `gemini-3.5-flash`; only `gemini-2.5-pro` actually served there. The same four all served from `global`. Pinning a region would mean running the most judgment-heavy work in the project on a materially older model.
- **Every derived row carries evidence.** No claim enters the database without at least one `evidence` row pointing at the `person_content` or `camp_knowledge` row it came from. Enforcement is in two halves, and both are required:
  - *Source side, at insert time.* The `evidence_source_exists` trigger rejects an evidence row whose `source_id` does not exist in the named source table, and CHECKs restrict `source_table` and `claim_table` to known tables.
  - *Claim side, at end of pass.* **Every task that writes claims must call `verify_evidence_complete(conn)` before it commits.** This cannot be a trigger — a claim row must exist before evidence can cite its id, so every claim is legitimately evidence-free for a moment. `verify_evidence_complete` raises `ValueError` naming the offending tables and ids; let it propagate and fail the run. A pass that writes claims and does not call it is incomplete, whatever its tests say.
- **Enrichment is fully re-derivable, so passes start clean.** `create_enrichment_tables` uses `CREATE TABLE IF NOT EXISTS`, which means a database created before a schema change silently keeps the old, less-constrained tables. Call `drop_enrichment_tables(conn)` then `create_enrichment_tables(conn)` when re-running from scratch rather than trusting an existing file's shape.
- **Voice: first person plural.** Lucy belongs to the camp and speaks as part of it — "our bike fleet", "our second storage vehicle", "we meet them on arrival". Never "the camp's bike fleet" or "the camp meets them", which is how an outsider describes a group they are not in. This binds every generated summary, fact, profile and lore entry, so put it in the shared system preamble rather than in individual prompts. Exception: quoted evidence is reproduced verbatim and never rephrased.
- **Contradictions are preserved, not resolved.** When two sources disagree, store both with their dates. The corpus contains real disagreements (Cece: "no ladders in Doris", 19 Aug 2022; Marcus: "at least 3 ladders in Doris", 24 Aug 2022) and flattening them into one answer would be a lie.
- **Never mutate `LucyPT/LucyPT/ps_knowledge.db`.** Work on `scripts/output/ps_knowledge.db`; `package_db.py --install` remains the only path to the app bundle.
- Source data is git-ignored. Never commit corpus content, extracted output, or API responses.
- Auth is GCP application-default credentials (`gcloud auth application-default login`). Never hardcode a key or a service-account file path.
- `google-genai` is the SDK. The `anthropic` package is not used by this pipeline.

## Why these API features

| Feature | Why it applies here |
|---|---|
| **Bounded concurrency** | ~650 independent extractions with no latency requirement. Results are keyed by the job id you supply — never by completion order. |
| **Context caching** | Every request shares the same large preamble (camp vocabulary, output contract). Cached, that prefix is billed at a fraction after the first write. |
| **Structured output** | `response_schema` + `response_mime_type: application/json` guarantees valid JSON against our schema, removing the parse-retry loop. |
| **Thinking** | Extraction over ambiguous human conversation is judgment work; enable it where the model supports it. |

---

### Task 1: Enrichment schema with mandatory evidence

**Files:**
- Create: `scripts/enrich_schema.py`
- Create: `scripts/tests/test_enrich_schema.py`

**Interfaces:**
- Consumes: the existing `person`, `person_content`, `camp_knowledge` tables.
- Produces: `create_enrichment_tables(conn)`, and the table set every later task writes to.

- [ ] **Step 1: Write the failing tests**

Create `scripts/tests/test_enrich_schema.py`:

```python
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrich_schema import create_enrichment_tables


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE person (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE person_content (id INTEGER PRIMARY KEY, content TEXT);
        CREATE TABLE camp_knowledge (id INTEGER PRIMARY KEY, title TEXT);
        INSERT INTO person (id, name) VALUES (1, 'Nick Hadley');
        INSERT INTO person_content (id, content) VALUES (1, 'You will need a 9/16 socket');
    """)
    c.execute("PRAGMA foreign_keys = ON")
    create_enrichment_tables(c)
    return c


def test_creates_every_enrichment_table(conn):
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("entity", "entity_alias", "entity_fact", "person_profile",
              "expertise", "relationship", "lore", "evidence"):
        assert t in names, f"missing table: {t}"


def test_evidence_requires_a_real_source_row(conn):
    """An evidence row must point at content that exists."""
    conn.execute("INSERT INTO entity (name, kind, summary) VALUES (?,?,?)",
                 ("Doris", "vehicle", "A storage vehicle."))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("""
            INSERT INTO evidence (claim_table, claim_id, source_table, source_id, quote)
            VALUES ('entity', 1, 'person_content', 99999, 'nonexistent')
        """)


def test_evidence_rejects_an_unknown_source_table(conn):
    conn.execute("INSERT INTO entity (name, kind, summary) VALUES (?,?,?)",
                 ("Doris", "vehicle", "A storage vehicle."))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("""
            INSERT INTO evidence (claim_table, claim_id, source_table, source_id, quote)
            VALUES ('entity', 1, 'twitter', 1, 'nope')
        """)


def test_entity_facts_can_disagree_with_each_other(conn):
    """Cece said no ladders; Marcus said three. Both are recorded."""
    conn.execute("INSERT INTO entity (name, kind, summary) VALUES (?,?,?)",
                 ("Doris", "vehicle", "A storage vehicle."))
    conn.execute("""INSERT INTO entity_fact (entity_id, fact, category, asserted_on)
                    VALUES (1, 'Contains no ladders', 'contents', '2022-08-19')""")
    conn.execute("""INSERT INTO entity_fact (entity_id, fact, category, asserted_on)
                    VALUES (1, 'Contains at least 3 ladders', 'contents', '2022-08-24')""")
    rows = conn.execute(
        "SELECT COUNT(*) FROM entity_fact WHERE category='contents'").fetchone()[0]
    assert rows == 2


def test_aliases_are_unique_per_entity(conn):
    conn.execute("INSERT INTO entity (name, kind, summary) VALUES (?,?,?)",
                 ("Doris", "vehicle", "A storage vehicle."))
    conn.execute("INSERT INTO entity_alias (entity_id, alias) VALUES (1, 'Boris/Doris')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO entity_alias (entity_id, alias) VALUES (1, 'Boris/Doris')")
```

- [ ] **Step 2: Run and watch them fail**

```bash
cd ~/Snails-enrichment/scripts
source .venv/bin/activate
python -m pytest tests/test_enrich_schema.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'enrich_schema'`.

- [ ] **Step 3: Write the schema**

Create `scripts/enrich_schema.py`:

```python
#!/usr/bin/env python3
"""
Schema for build-time enrichment output.

The organising rule: every derived claim is traceable to the message or
document it came from. `evidence` is not optional metadata — it is what
separates "Lucy knows Doris holds the med kit" from "Lucy made that up",
and it is what lets a wrong inference be found and corrected later.
"""
import sqlite3

SOURCE_TABLES = ("person_content", "camp_knowledge")

SCHEMA = """
-- A named thing the camp talks about that no document defines.
CREATE TABLE IF NOT EXISTS entity (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    kind          TEXT NOT NULL,          -- vehicle | structure | tool | place | tradition | asset
    summary       TEXT NOT NULL,
    first_seen    TEXT,
    last_seen     TEXT,
    mention_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS entity_alias (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    alias     TEXT NOT NULL,
    UNIQUE (entity_id, alias)
);

-- One fact per row, dated. Two rows may contradict each other on purpose:
-- the corpus contains real disagreements and resolving them silently would
-- be inventing an answer the camp never reached.
CREATE TABLE IF NOT EXISTS entity_fact (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id   INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    fact        TEXT NOT NULL,
    category    TEXT,                     -- access | contents | location | handling | history
    asserted_on TEXT
);

CREATE TABLE IF NOT EXISTS person_profile (
    person_id    INTEGER PRIMARY KEY REFERENCES person(id) ON DELETE CASCADE,
    summary      TEXT NOT NULL,
    years_active TEXT,
    chapter      TEXT,
    known_for    TEXT
);

CREATE TABLE IF NOT EXISTS expertise (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    topic     TEXT NOT NULL,
    strength  TEXT NOT NULL,              -- strong | moderate | mentioned
    UNIQUE (person_id, topic)
);

CREATE TABLE IF NOT EXISTS relationship (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    person_a INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    person_b INTEGER NOT NULL REFERENCES person(id) ON DELETE CASCADE,
    kind     TEXT NOT NULL,               -- builds_with | chapter | co_shift | mentors
    strength INTEGER DEFAULT 1,
    UNIQUE (person_a, person_b, kind)
);

CREATE TABLE IF NOT EXISTS lore (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    title  TEXT NOT NULL,
    story  TEXT NOT NULL,
    year   INTEGER,
    people TEXT                           -- comma-separated names, for display only
);

-- The spine. claim_table/claim_id name the derived row; source_table/source_id
-- name the corpus row it came from; quote is the exact supporting text.
CREATE TABLE IF NOT EXISTS evidence (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_table  TEXT NOT NULL,
    claim_id     INTEGER NOT NULL,
    source_table TEXT NOT NULL CHECK (source_table IN ('person_content', 'camp_knowledge')),
    source_id    INTEGER NOT NULL,
    quote        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evidence_claim  ON evidence(claim_table, claim_id);
CREATE INDEX IF NOT EXISTS idx_evidence_source ON evidence(source_table, source_id);
CREATE INDEX IF NOT EXISTS idx_fact_entity     ON entity_fact(entity_id);
CREATE INDEX IF NOT EXISTS idx_expertise_topic ON expertise(topic);
"""

# SQLite cannot express a foreign key whose target table varies by row, so the
# referential check for `evidence.source_id` is enforced by trigger instead.
TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS evidence_source_exists
BEFORE INSERT ON evidence
BEGIN
    SELECT RAISE(ABORT, 'evidence.source_id does not exist in the named source table')
    WHERE (NEW.source_table = 'person_content'
           AND NOT EXISTS (SELECT 1 FROM person_content WHERE id = NEW.source_id))
       OR (NEW.source_table = 'camp_knowledge'
           AND NOT EXISTS (SELECT 1 FROM camp_knowledge WHERE id = NEW.source_id));
END;
"""


def create_enrichment_tables(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.executescript(TRIGGERS)
    conn.commit()


def drop_enrichment_tables(conn: sqlite3.Connection) -> None:
    """Enrichment is fully re-derivable; dropping is how a re-run starts clean."""
    for t in ("evidence", "lore", "relationship", "expertise",
              "person_profile", "entity_fact", "entity_alias", "entity"):
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.commit()
```

- [ ] **Step 4: Run the tests**

```bash
cd ~/Snails-enrichment/scripts
source .venv/bin/activate
python -m pytest tests/test_enrich_schema.py -v
```

Expected: all five PASS. If `test_evidence_requires_a_real_source_row` fails, `PRAGMA foreign_keys = ON` is not active on the test connection — the fixture sets it, and `create_enrichment_tables` sets it again deliberately.

- [ ] **Step 5: Commit**

```bash
cd ~/Snails-enrichment
git add scripts/enrich_schema.py scripts/tests/test_enrich_schema.py
git commit -m "feat: enrichment schema with mandatory evidence links"
```

---

### Task 2: Vertex client with structured output and bounded concurrency

**Files:**
- Modify: `scripts/requirements.txt`
- Create: `scripts/vertex_client.py`
- Create: `scripts/tests/test_vertex_client.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `run_all(jobs, system_text, schema) -> dict[str, dict | None]` keyed by job id. Tasks 3–6 use only that.

**Why a concurrency pool rather than Vertex batch prediction.** Batch prediction needs input staged in GCS or BigQuery and output collected back from there. This pipeline runs roughly 650 requests in total — that staging machinery costs more to build and debug than it saves. A bounded thread pool is the right size for the job. Revisit batch prediction only if the corpus grows an order of magnitude.

- [ ] **Step 1: Confirm the SDK and the resolved model still hold**

`google-genai` 2.17.0 is already installed in `scripts/.venv`, and the model and endpoint were resolved live against `lucy-snails` on 2026-08-09. Your job here is to confirm that still holds, not to rediscover it. Pin the version in `requirements.txt` and remove any `anthropic` line — this pipeline does not use it.

```bash
cd ~/Snails-enrichment/scripts
source .venv/bin/activate
python -c "from google import genai; print(genai.__version__)"   # expect 2.17.0
```

**Availability is not what `models.list()` reports.** That call returns 25 Gemini models regardless of region; it is a catalogue, not an availability check. The only reliable test is a real request. Confirm the chosen model answers:

```bash
python -c "
from google import genai
c = genai.Client(vertexai=True, project='lucy-snails', location='global')
r = c.models.generate_content(
    model='gemini-3.1-pro-preview', contents='Say ok.',
    config={'response_mime_type':'application/json',
            'response_schema':{'type':'object','properties':{'ok':{'type':'string'}},'required':['ok']}})
print(r.text)
"
```

Expected: a JSON object. A 404 means the model has moved — re-probe candidates with real `generate_content` calls (not `models.list()`) across `global` and any region you try, record what actually answered in the task report, and pick the most capable one that did. This pass reads ambiguous human conversation and writes claims about named people, so capability matters more than throughput.

- [ ] **Step 2: Write the failing tests**

These cover our own logic — job keying, config wiring, failure handling — against a fake response object. **No network calls in tests.**

Create `scripts/tests/test_vertex_client.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vertex_client import build_config, collect_results


class FakeResponse:
    def __init__(self, text):
        self.text = text


def test_config_requests_json_against_the_schema():
    schema = {"type": "object", "properties": {"name": {"type": "string"}},
              "required": ["name"]}
    cfg = build_config(schema, system_text="preamble")
    assert cfg["response_mime_type"] == "application/json"
    assert cfg["response_schema"] == schema


def test_config_carries_the_shared_preamble_as_system_instruction():
    cfg = build_config({"type": "object"}, system_text="camp vocabulary")
    assert cfg["system_instruction"] == "camp vocabulary"


def test_results_are_keyed_by_job_id_not_position():
    """Concurrent completion means arrival order carries no meaning."""
    out = collect_results([
        ("entity-42", FakeResponse('{"name": "Doris"}'), None),
        ("entity-7", FakeResponse('{"name": "Boris"}'), None),
    ])
    assert out["entity-42"] == {"name": "Doris"}
    assert out["entity-7"] == {"name": "Boris"}


def test_failed_jobs_map_to_none_rather_than_vanishing():
    out = collect_results([
        ("entity-1", FakeResponse('{"name": "Doris"}'), None),
        ("entity-2", None, RuntimeError("quota exceeded")),
    ])
    assert out["entity-1"] == {"name": "Doris"}
    assert out["entity-2"] is None


def test_unparseable_output_maps_to_none_not_a_crash():
    out = collect_results([("entity-3", FakeResponse("not json"), None)])
    assert out["entity-3"] is None
```

```bash
cd ~/Snails-enrichment/scripts
source .venv/bin/activate
python -m pytest tests/test_vertex_client.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'vertex_client'`.

- [ ] **Step 3: Write the client**

Create `scripts/vertex_client.py`:

```python
#!/usr/bin/env python3
"""
Vertex AI client for the enrichment passes.

Concurrency rather than batch prediction: ~650 requests total does not justify
staging input through GCS. A bounded pool keeps the code small and the failure
modes obvious.

Auth is application-default credentials — run `gcloud auth application-default
login` once. Nothing here reads a key from the environment or from disk.
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from google import genai

PROJECT = os.environ.get("GCP_PROJECT", "lucy-snails")

# "global", not a pinned region: probed 2026-08-09, us-central1 served only
# gemini-2.5-pro while global served every current model. models.list() is a
# catalogue and does not reflect this — only a real request does.
LOCATION = "global"

# Resolved by live generate_content against lucy-snails on 2026-08-09.
# Do not edit this from memory; re-run Task 2 Step 1 instead.
MODEL = "gemini-3.1-pro-preview"

MAX_WORKERS = 8          # stay well under the project's per-minute quota
MAX_RETRIES = 3


def client() -> genai.Client:
    return genai.Client(vertexai=True, project=PROJECT, location=LOCATION)


def build_config(schema: dict, system_text: str) -> dict:
    """Structured output plus the shared preamble.

    The preamble is byte-identical across every request in a pass, which is
    what makes it worth caching. Anything that varies per request belongs in
    the prompt, not here.
    """
    return {
        "response_mime_type": "application/json",
        "response_schema": schema,
        "system_instruction": system_text,
    }


def collect_results(finished) -> dict:
    """Key by job id — concurrent completion means order carries no meaning.

    A job that failed or returned unparseable output maps to None rather than
    disappearing, so callers can count and report what did not come back.
    """
    out: dict[str, dict | None] = {}
    for job_id, response, error in finished:
        if error is not None or response is None:
            out[job_id] = None
            continue
        try:
            out[job_id] = json.loads(response.text)
        except (json.JSONDecodeError, TypeError):
            out[job_id] = None
    return out


def _one(c, job_id, prompt, config):
    for attempt in range(MAX_RETRIES):
        try:
            resp = c.models.generate_content(
                model=MODEL, contents=prompt, config=config)
            return (job_id, resp, None)
        except Exception as exc:            # quota, transient 5xx
            if attempt == MAX_RETRIES - 1:
                return (job_id, None, exc)
            time.sleep(2 ** attempt)
    return (job_id, None, RuntimeError("unreachable"))


def run_all(jobs: list[tuple[str, str]], system_text: str, schema: dict) -> dict:
    """jobs is [(job_id, prompt), ...]. Returns {job_id: parsed | None}."""
    c = client()
    config = build_config(schema, system_text)
    finished = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(_one, c, jid, prompt, config)
                   for jid, prompt in jobs]
        for i, f in enumerate(futures, 1):
            finished.append(f.result())
            if i % 25 == 0:
                print(f"  {i}/{len(jobs)}", file=sys.stderr)

    results = collect_results(finished)
    failed = [k for k, v in results.items() if v is None]
    if failed:
        print(f"WARNING: {len(failed)} jobs returned nothing: {failed[:10]}",
              file=sys.stderr)
    return results
```

- [ ] **Step 4: Run the tests**

```bash
cd ~/Snails-enrichment/scripts
source .venv/bin/activate
python -m pytest tests/test_vertex_client.py -v
```

Expected: all five PASS, with no network access.

- [ ] **Step 5: One live smoke call, then commit**

```bash
cd ~/Snails-enrichment/scripts
source .venv/bin/activate
export GCP_PROJECT=lucy-snails
python -c "
from vertex_client import run_all
out = run_all(
    [('smoke', 'Return the name Doris and the kind vehicle.')],
    system_text='You extract structured facts.',
    schema={'type': 'object',
            'properties': {'name': {'type': 'string'}, 'kind': {'type': 'string'}},
            'required': ['name', 'kind']},
)
print(out)
"
```

Expected: `{'smoke': {'name': 'Doris', 'kind': 'vehicle'}}`. A `None` here means auth, model id, or region is wrong — fix it before Task 3, because every later task depends on this call working.

```bash
cd ~/Snails-enrichment
git add scripts/vertex_client.py scripts/tests/test_vertex_client.py scripts/requirements.txt
git commit -m "feat: Vertex client with structured output and bounded concurrency"
```

---

### Task 3: Entity discovery

**Files:**
- Create: `scripts/enrich_entities.py`
- Create: `scripts/prompts/entity_discovery.md`

**Interfaces:**
- Consumes: `run_all` from Task 2; `create_enrichment_tables` from Task 1.
- Produces: populated `entity` and `entity_alias` tables. Task 4 enriches each row.

- [ ] **Step 1: Write the discovery prompt**

Create `scripts/prompts/entity_discovery.md`:

```markdown
You are reading messages from a Burning Man theme camp's group chats to find
**named things the camp treats as common knowledge** — the objects, vehicles,
structures and traditions that members refer to by name without ever explaining.

These are what a new member cannot look up. "Doris" means nothing to an
outsider; to this camp it is a storage vehicle with a padlock code.

Report an entity when the messages refer to it by a specific name and treat its
meaning as already shared. Do not report:

- People (they are handled separately)
- Generic nouns used generically — "the kitchen", "a ladder", "the bar"
- Places outside camp — Reno, Walmart, the Temple
- One-off references that carry no shared meaning

For each entity give the name exactly as the camp writes it, any other spellings
or paired names you see, a kind, and the single most telling message id.

Kinds: vehicle, structure, tool, place, tradition, asset.
```

- [ ] **Step 2: Write the discovery pass**

Create `scripts/enrich_entities.py`:

```python
#!/usr/bin/env python3
"""
Pass 1 — find the named things the camp never defines.

Messages are chunked so each request sees a contiguous run of conversation:
entity names are established by how people use them in context, so shuffling
the corpus would destroy the signal this pass depends on.

Usage:
    GCP_PROJECT=lucy-snails python enrich_entities.py ./output/ps_knowledge.db
"""
import sqlite3
import sys
from pathlib import Path

from vertex_client import run_all
from enrich_schema import create_enrichment_tables

PROMPT = (Path(__file__).parent / "prompts" / "entity_discovery.md").read_text()
CHUNK = 300          # messages per request — a contiguous run of conversation

SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string",
                             "enum": ["vehicle", "structure", "tool",
                                      "place", "tradition", "asset"]},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "evidence_message_id": {"type": "integer"},
                },
                "required": ["name", "kind", "aliases", "evidence_message_id"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["entities"],
    "additionalProperties": False,
}


def build_jobs(conn) -> list[tuple[str, str]]:
    """Contiguous runs of conversation, ordered by group then time.

    Entity names are established by how people use them in context, so
    shuffling the corpus would destroy the signal this pass depends on.
    """
    rows = conn.execute("""
        SELECT pc.id, COALESCE(p.name,'?'), substr(pc.timestamp,1,10), pc.content
        FROM person_content pc LEFT JOIN person p ON p.id = pc.person_id
        WHERE length(pc.content) > 15
        ORDER BY pc.source, pc.timestamp
    """).fetchall()

    jobs = []
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        body = "\n".join(f"[{mid}] {date} {who}: {text}"
                         for mid, who, date, text in chunk)
        jobs.append((f"discover-{i // CHUNK}", f"Messages:\n\n{body}"))
    return jobs


def store(conn, results):
    """Merge chunk results. The same entity surfaces in many chunks; each
    sighting adds aliases and increments the mention count."""
    create_enrichment_tables(conn)
    missing = [k for k, v in results.items() if v is None]
    if missing:
        print(f"WARNING: {len(missing)} chunks returned nothing: "
              f"{missing[:5]}", file=sys.stderr)

    for payload in filter(None, results.values()):
        for e in payload["entities"]:
            conn.execute("""
                INSERT INTO entity (name, kind, summary, mention_count)
                VALUES (?, ?, '', 1)
                ON CONFLICT(name) DO UPDATE SET mention_count = mention_count + 1
            """, (e["name"], e["kind"]))
            eid = conn.execute("SELECT id FROM entity WHERE name = ?",
                               (e["name"],)).fetchone()[0]
            for alias in e["aliases"]:
                conn.execute(
                    "INSERT OR IGNORE INTO entity_alias (entity_id, alias) VALUES (?,?)",
                    (eid, alias))
    conn.commit()


def main():
    conn = sqlite3.connect(Path(sys.argv[1]))
    jobs = build_jobs(conn)

    print(f"{len(jobs)} chunks to send")
    if input("proceed? [y/N] ").strip().lower() != "y":
        sys.exit("aborted")

    store(conn, run_all(jobs, PROMPT, SCHEMA))

    n = conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0]
    print(f"\n{n} distinct entities")
    for name, kind, seen in conn.execute(
            "SELECT name, kind, mention_count FROM entity "
            "ORDER BY mention_count DESC LIMIT 25"):
        print(f"  {seen:>4}x  {kind:<10} {name}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run discovery**

```bash
cd ~/Snails-enrichment/scripts
source .venv/bin/activate
export GCP_PROJECT=lucy-snails
python enrich_entities.py ./output/ps_knowledge.db
```

Expected: roughly 80 chunks, an estimate under $10, and a ranked entity list. **`Doris` must appear near the top** — it has 51 chat mentions and 74 document mentions. If it does not, the prompt or the chunking is wrong; fix that before spending money on Task 4.

- [ ] **Step 4: Sanity-check the output by reading it**

```bash
sqlite3 output/ps_knowledge.db \
  "SELECT e.name, e.kind, group_concat(a.alias, ' / ')
   FROM entity e LEFT JOIN entity_alias a ON a.entity_id = e.id
   GROUP BY e.id ORDER BY e.mention_count DESC LIMIT 40;"
```

Read the list. Entities that are actually generic nouns or outside places mean the prompt needs tightening — this is judgment, not a metric, so look at it rather than counting it.

- [ ] **Step 5: Commit**

```bash
cd ~/Snails-enrichment
git add scripts/enrich_entities.py scripts/prompts/entity_discovery.md
git commit -m "feat: entity discovery pass over the chat corpus"
```

---

### Task 4: Entity enrichment

**Files:**
- Create: `scripts/enrich_entity_records.py`
- Create: `scripts/prompts/entity_record.md`

**Interfaces:**
- Consumes: the `entity` table from Task 3.
- Produces: populated `entity.summary`, `entity_fact`, and `evidence` rows for every entity.

- [ ] **Step 1: Write the enrichment prompt**

Create `scripts/prompts/entity_record.md`:

```markdown
You are building the camp's reference entry for one named thing, from every
message that mentions it.

Write a summary a new member could read to understand what it is and why it
comes up. Then list the specific, useful facts — how to get into it, what is in
it, where it lives, how it is handled, what happened to it.

Rules that matter more than completeness:

- **Every fact cites the message it came from.** A fact you cannot point at is
  a fact you invented; leave it out.
- **When sources disagree, record both.** Do not average them, pick the newer
  one, or quietly drop the loser. Two people genuinely contradicting each other
  months apart is information, and the dates are part of the fact.
- **A single message can carry a fact that no message states outright** only if
  the inference is immediate and you can quote the line it rests on. Anything
  further is speculation.
- Facts are specific. "Contains tools" is not worth storing; "the 9/16 sockets
  live in it and Nick brings spares" is.

If the messages are people asking what the thing is rather than telling you,
say so in the summary. That is the honest answer and it is more useful than a
confident guess.
```

- [ ] **Step 2: Write the enrichment pass**

Create `scripts/enrich_entity_records.py` following the same single-phase `run_all` shape as Task 3. Key differences:

- One job per entity, `job_id = f"entity-{entity_id}"`.
- The user message contains every message mentioning the entity name or any of its aliases, each prefixed with `[message_id] date sender:`.
- Output schema:

```python
SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string"},
                    "category": {"type": "string",
                                 "enum": ["access", "contents", "location",
                                          "handling", "history"]},
                    "asserted_on": {"type": "string"},
                    "evidence_message_id": {"type": "integer"},
                    "quote": {"type": "string"},
                },
                "required": ["fact", "category", "asserted_on",
                             "evidence_message_id", "quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "facts"],
    "additionalProperties": False,
}
```

The mention query:

```sql
SELECT pc.id, COALESCE(p.name,'?'), substr(pc.timestamp,1,10), pc.content
FROM person_content pc LEFT JOIN person p ON p.id = pc.person_id
WHERE lower(pc.content) LIKE '%' || lower(?) || '%'
ORDER BY pc.timestamp
```

Run it once per name and once per alias, then deduplicate by message id.

On store: write `entity.summary`, one `entity_fact` per fact, and one `evidence` row per fact pointing at `person_content` with the returned quote. **The evidence insert is inside the same transaction as the fact** — a fact whose evidence insert fails must not survive, and the trigger from Task 1 will abort it if the message id is fabricated.

- [ ] **Step 3: Run it on Doris alone first**

```bash
cd ~/Snails-enrichment/scripts
source .venv/bin/activate
export GCP_PROJECT=lucy-snails
python enrich_entity_records.py ./output/ps_knowledge.db --only Doris
sqlite3 output/ps_knowledge.db "
  SELECT f.category, f.fact, f.asserted_on, ev.quote
  FROM entity_fact f
  JOIN entity e ON e.id = f.entity_id
  LEFT JOIN evidence ev ON ev.claim_table='entity_fact' AND ev.claim_id=f.id
  WHERE e.name='Doris' ORDER BY f.category;"
```

**Read the output before running the full pass.** The known-good result includes the padlock code `8765` with its hammer note, the 9/16 sockets, the med kit, and *both* ladder claims with their differing dates. If the two ladder facts have been collapsed into one, the contradiction rule is not landing — fix the prompt before spending on 300 entities.

- [ ] **Step 4: Run the full pass, then verify every fact has evidence**

```bash
python enrich_entity_records.py ./output/ps_knowledge.db
sqlite3 output/ps_knowledge.db "
  SELECT COUNT(*) AS facts_without_evidence FROM entity_fact f
  WHERE NOT EXISTS (SELECT 1 FROM evidence e
                    WHERE e.claim_table='entity_fact' AND e.claim_id=f.id);"
```

Expected: **0**. Anything else is a bug in the store path, not a data characteristic.

- [ ] **Step 5: Commit**

```bash
cd ~/Snails-enrichment
git add scripts/enrich_entity_records.py scripts/prompts/entity_record.md
git commit -m "feat: entity enrichment with evidence-linked, contradiction-preserving facts"
```

---

### Task 5: Person profiles, expertise and relationships

**Files:**
- Create: `scripts/enrich_people.py`
- Create: `scripts/prompts/person_profile.md`

**Interfaces:**
- Consumes: Task 2's batch client, Task 1's schema.
- Produces: `person_profile`, `expertise`, `relationship`, and their `evidence` rows.

- [ ] **Step 1: Decide who gets a profile, from the data**

```bash
cd ~/Snails-enrichment/scripts
sqlite3 output/ps_knowledge.db "
  SELECT COUNT(*) FROM (SELECT person_id FROM person_content
                        GROUP BY person_id HAVING COUNT(*) >= 20);"
```

Profile only people at or above that floor. Someone with three messages has no profile to write, and asking for one invites invention — which is the single worst failure mode for a product 1,142 real people will use.

- [ ] **Step 2: Write the prompt**

Create `scripts/prompts/person_profile.md`:

```markdown
You are writing a short reference note about one member of a Burning Man camp,
from the messages they wrote.

This note will be read by other members of their camp, and by them. Write only
what the messages actually show.

Capture: what they are involved in, what they clearly know about, which chapter
or crew they belong to, and roughly which years they were around.

Hard rules:

- **Only what the messages support.** No inference about personality, character,
  relationships, or anything they did not write about. If the messages show
  someone coordinating storage logistics, say that; do not conclude they are
  organised, senior, or well-liked.
- **Expertise means demonstrated knowledge**, not enthusiasm. Someone answering
  detailed hardware questions has hardware expertise. Someone asking about
  hardware does not.
- **Every claim cites a message.** No citation, no claim.
- **Say when you don't know.** A short note that covers two things confidently
  is worth more than a full one that guesses at five.
- Write nothing about health, relationships, money, or conflict, even where the
  messages discuss them.
```

- [ ] **Step 3: Implement, then run on one person you can check**

Same single-phase `run_all` shape. `job_id = f"person-{person_id}"`. Output schema carries `summary`, `years_active`, `chapter`, `known_for`, and an `expertise` array of `{topic, strength, evidence_message_id, quote}`.

Run first on Nick Hadley alone and read the result:

```bash
export GCP_PROJECT=lucy-snails
python enrich_people.py ./output/ps_knowledge.db --only "Nick Hadley"
```

The corpus supports hardware and tools expertise for him (the 9/16 socket exchange, the spare sets in Doris). If the profile asserts anything you cannot trace to a message, the rules are not landing — fix the prompt before running the rest.

- [ ] **Step 4: Derive relationships in plain code, not from the model**

Relationships are counting, not judgment: co-membership in a source group, and messages in the same conversation window. Compute them with SQL and insert directly. Do not spend model calls on arithmetic, and do not let a model infer social closeness — that is exactly the kind of claim about real people that the corpus cannot support.

```sql
INSERT OR IGNORE INTO relationship (person_a, person_b, kind, strength)
SELECT a.person_id, b.person_id, 'chapter', COUNT(*)
FROM person_content a
JOIN person_content b
  ON a.source = b.source AND a.person_id < b.person_id
GROUP BY a.person_id, b.person_id
HAVING COUNT(*) >= 50;
```

- [ ] **Step 5: Run the full pass and commit**

```bash
python enrich_people.py ./output/ps_knowledge.db
cd ~/Snails-enrichment
git add scripts/enrich_people.py scripts/prompts/person_profile.md
git commit -m "feat: person profiles and expertise, with relationships derived in SQL"
```

---

### Task 6: Adversarial audit of every claim

**Files:**
- Create: `scripts/audit_enrichment.py`
- Create: `scripts/prompts/enrichment_audit.md`

**Interfaces:**
- Consumes: every derived row plus its evidence.
- Produces: `docs/superpowers/specs/2026-08-09-enrichment-audit.md` and a `verdict` column on the audited tables.

This task exists because the enrichment output makes claims about 1,142 real people that will ship to those same people. A wrong claim about a person is worse than a missing one.

- [ ] **Step 1: Write the audit prompt**

Create `scripts/prompts/enrichment_audit.md`:

```markdown
You are checking one claim against the evidence offered for it. Your job is to
**try to refute it.**

You will be given a claim and the exact message quoted as its support.

Reject the claim if:

- The quote does not actually say it
- The claim generalises well beyond what one message can support
- The claim states as settled something the quote presents as a question,
  a guess, or a joke
- The claim is about a person's character, relationships, or standing rather
  than something they demonstrably did or knew

Accept only if the quote plainly supports the claim on its own. If you are
unsure, reject — a missing fact costs the camp nothing, and a wrong one about a
named person costs them trust in everything else Lucy says.
```

- [ ] **Step 2: Audit every claim, most-cited first**

One job per claim. Each request contains only the claim and its quote — no surrounding context, because the question is precisely whether the quote alone supports it.

Schema: `{"verdict": "supported" | "unsupported", "reason": string}`.

Add a `verdict` column to `entity_fact`, `person_profile` and `expertise`, defaulting to `'unaudited'`, and write results back.

- [ ] **Step 3: Report, and make the retrieval layer respect the verdict**

```bash
sqlite3 output/ps_knowledge.db "
  SELECT 'entity_fact' t, verdict, COUNT(*) FROM entity_fact GROUP BY verdict
  UNION ALL
  SELECT 'expertise', verdict, COUNT(*) FROM expertise GROUP BY verdict;"
```

Write the counts and every rejected claim with its reason into `docs/superpowers/specs/2026-08-09-enrichment-audit.md`, and commit that file — it is the record of what the pipeline got wrong, which is the input to improving the prompts next year.

**Unsupported claims stay in the database and are excluded from retrieval.** Deleting them loses the signal about which prompts produce bad output; hiding them protects the product. Task 7's embedding step filters on `verdict = 'supported'`.

- [ ] **Step 4: Read a sample of the rejections yourself**

Print twenty rejected claims with their quotes and reasons and read them. If the auditor is rejecting claims that are plainly supported, it is too aggressive and the audit prompt needs work — an audit that rejects everything is as useless as one that accepts everything.

- [ ] **Step 5: Commit**

```bash
cd ~/Snails-enrichment
git add scripts/audit_enrichment.py scripts/prompts/enrichment_audit.md \
        docs/superpowers/specs/2026-08-09-enrichment-audit.md
git commit -m "feat: adversarial audit of enrichment claims against their evidence"
```

---

### Task 7: Embed the enriched knowledge and wire it into the build

**Files:**
- Modify: `scripts/embed_corpus.py`
- Modify: `scripts/package_db.py`
- Modify: `scripts/ingest_all.py`

**Interfaces:**
- Consumes: audited enrichment tables.
- Produces: enrichment rows in the semantic index; the manifest records enrichment coverage.

- [ ] **Step 1: Embed entity records and profiles**

Extend `embed_corpus.py` with an `enrichment_embeddings` table on the same 768-dim EmbeddingGemma vectors, embedding one row per entity (name + summary + supported facts) and one per person profile. **Filter on `verdict = 'supported'`** — an unsupported claim must never reach retrieval.

This is what makes "what is Doris" return the record instead of someone asking about it.

- [ ] **Step 2: Extend the manifest**

In `package_db.py`, add `entity_count`, `fact_count`, `audited_fact_count` and `enrichment_model` to the `meta` table, and refuse to package if any table has facts but zero audited facts — shipping unaudited claims about real people is the failure this whole task chain exists to prevent.

- [ ] **Step 3: Document the order without putting it only in prose**

Enrichment runs after ingest and before embedding. Add it to `ingest_all.py`'s pipeline comment **and** make `embed_corpus.py` warn loudly when the enrichment tables are absent or empty, so the ordering is enforced by the code rather than remembered. This is the same defect that cost a rebuild in the corpus plan; do not repeat it.

- [ ] **Step 4: Full rebuild and verify**

```bash
cd ~/Snails-enrichment/scripts
source .venv/bin/activate
python ingest_all.py "../Whatsapp PS Exports" ./output
# enrichment passes (Tasks 3-6)
python embed_corpus.py ./output/ps_knowledge.db ./models/embeddinggemma-300M-Q8_0.gguf
python package_db.py ./output/ps_knowledge.db
```

Then re-run the probe from the corpus work and confirm **"what is Doris" returns the entity record**, not Becca's question.

- [ ] **Step 5: Commit**

```bash
cd ~/Snails-enrichment
git add scripts/embed_corpus.py scripts/package_db.py scripts/ingest_all.py
git commit -m "feat: embed audited enrichment and record coverage in the manifest"
```

---

## Cost

Every pass draws on the project's GCP credits rather than a card, so the
practical question is not "can we afford this" but "did it cost what we
expected". Each script prints its job count and asks for confirmation before
sending anything.

**Record the actual spend after the first full run** — from the Vertex billing
page — and write the per-pass figures into this section. Estimating them here
in advance would be guessing at Gemini's per-token rates, and a measured
number is worth more than a projected one for deciding how often to re-run.

## Dependency on the corpus rebuild

Task 7 modifies `scripts/package_db.py`, which is created by Task 5 of
`2026-08-09-corpus-rebuild.md` and does not exist yet. Tasks 1–6 here are
independent of it. Finish corpus-rebuild Tasks 5 and 6 before starting Task 7.

## Deliberately out of scope

- **Lore extraction.** The `lore` table exists in the schema and stays empty for now. Multi-message narrative synthesis — the 2024 padlock story, where the joke only exists because two people said different halves two minutes apart — is the hardest extraction here and deserves its own pass once entities and people are proven.
- **Photo corpus.** Its own plan, blocked on a Google Takeout export.
- **Any iOS work.** The phone reads these tables; it does not produce them.
