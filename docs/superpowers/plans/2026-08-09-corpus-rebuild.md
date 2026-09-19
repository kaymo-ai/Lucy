# Corpus Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `ps_knowledge.db` from the WhatsApp exports with a correct parser, complete embedding coverage, and a manifest that makes dimension mismatches impossible — recovering the ~5,950 messages (22.6%) the current pipeline silently drops.

**Architecture:** Pure Mac-side Python. Fix `parse_chats.py` under test, re-ingest all nine exports, generate EmbeddingGemma vectors via `llama-cpp-python` (the same GGUF the phone will use, so device and corpus vectors are produced by identical code), store embeddings as Float32 BLOBs, and stamp a `meta` table the app validates at launch. Shares no code with the iOS app and is not blocked by the memory spike.

**Tech Stack:** Python 3.14, pytest, sqlite3, `llama-cpp-python`, EmbeddingGemma 300M Q8_0 GGUF.

## Global Constraints

- **Never mutate `LucyPT/LucyPT/ps_knowledge.db` in place.** The pipeline writes to `scripts/output/` and a separate task copies it. The shipped DB is a build artifact.
- Embedding model is **`embeddinggemma-300M-Q8_0.gguf`** (334 MB), 768-dim, pooling `LLAMA_POOLING_TYPE_MEAN`. Every embedding in the DB must come from this model.
- Embeddings are stored as **Float32 little-endian BLOBs**, not JSON text. Current storage is JSON text and is ~4x larger and slow to parse at launch.
- The `meta` table is written by the packaging step and is **required**; a DB without it is invalid.
- All nine exports in `Whatsapp PS Exports/` are inputs, including `PS BUILD 22`, which has never been ingested.
- Source data lives in git-ignored directories. Tests use committed fixtures, never the real exports.

## Context: what is actually broken

Verified against `LucyPT/LucyPT/ps_knowledge.db` and the exports:

| symptom | cause | scale |
|---|---|---|
| Media messages vanish and corrupt their predecessor | `WHATSAPP_PATTERN` (`parse_chats.py:22`) anchors `^\[`, but WhatsApp prefixes media lines with U+200E. Non-matching lines are appended as continuation text. | **2,867 lines** |
| Real messages dropped as "system" | `SYSTEM_PATTERNS` uses unanchored `re.search`; `r'added .+'` matches "I added the propane", `r'left$'` matches "turn left" | 92+ in the main group alone |
| Short messages dropped | `len(text) < 3` in `_save_message` | unmeasured, material for social signal |
| One group never ingested | `PS BUILD 22` absent from the DB | 593 lines |
| 72% of camp docs unsearchable | `camp_knowledge` 601 rows vs `knowledge_embeddings` 167 | 434 rows |

Export totals: **26,314** message lines (23,447 plain + 2,867 LTR-prefixed). Database holds **20,362**.

---

### Task 1: Test scaffolding and fixtures

**Files:**
- Create: `scripts/requirements.txt`
- Create: `scripts/tests/__init__.py`
- Create: `scripts/tests/fixtures/sample_chat.txt`
- Create: `scripts/tests/test_parse_chats.py`
- Create: `scripts/pytest.ini`

**Interfaces:**
- Consumes: nothing.
- Produces: a runnable `pytest` suite and a fixture exercising every parser edge case. Later tasks add tests to `test_parse_chats.py` and import `ChatParser` from `parse_chats`.

- [ ] **Step 1: Pin dependencies**

Create `scripts/requirements.txt`:

```
pytest==8.3.4
llama-cpp-python==0.3.16
numpy==2.2.1
tqdm==4.67.1
```

```bash
cd ~/Snails/scripts
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

- [ ] **Step 2: Configure pytest**

Create `scripts/pytest.ini`:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
addopts = -v
```

- [ ] **Step 3: Write the fixture covering every known failure mode**

Create `scripts/tests/fixtures/sample_chat.txt`. The `‎` characters below are literal U+200E marks — they must be real characters in the file, not escape sequences.

```
[8/18/22, 4:03:31 AM] PS BUILD 22: ‎Messages and calls are end-to-end encrypted.
[8/18/22, 4:03:31 AM] Jill Pennock: ‎Jill Pennock created this group
[8/18/22, 4:10:28 AM] Dean Wexler: ‎Jill Pennock added Dean Wexler
[8/18/22, 10:43:07 AM] Piotr: Hi everyone - who needs access to Reno storage before BM?
[8/24/22, 3:26:26 PM] Nick Hadley: What do you mean by a wrench adapter
‎[8/24/22, 3:28:34 PM] Marcus: ‎<attached: 00000231-PHOTO-2022-08-24-15-28-33.jpg>
[8/24/22, 3:31:29 PM] Nick Hadley: You will need a 9/16 socket as well for the lag bolts.
[8/24/22, 4:25:27 PM] Marcus: I added the propane to the shopping list
[8/24/22, 5:03:59 PM] Cece Garland: ok
[8/24/22, 6:50:36 PM] Marcus: Ok, placed. We have 3 frontages on C 8 and D
and the plot is basically square
[8/24/22, 6:55:05 PM] Nick Darnell: Turn left
‎[8/24/22, 7:31:30 PM] Christina Gallant: ‎image omitted
```

- [ ] **Step 4: Write the failing tests**

Create `scripts/tests/test_parse_chats.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parse_chats import ChatParser

FIXTURE = Path(__file__).parent / "fixtures" / "sample_chat.txt"


def parse_fixture():
    parser = ChatParser()
    parser.parse_file(FIXTURE, "test-group")
    return parser


def texts(parser):
    return [m["text"] for m in parser.messages]


def test_ltr_prefixed_media_message_is_its_own_message():
    """WhatsApp prefixes media lines with U+200E; they must not be swallowed."""
    parser = parse_fixture()
    senders_times = [(m["sender"], m["timestamp"].strftime("%H:%M:%S"))
                     for m in parser.messages if m["timestamp"]]
    assert ("Marcus", "15:28:34") in senders_times


def test_ltr_media_message_does_not_corrupt_previous_message():
    parser = parse_fixture()
    wrench = [t for t in texts(parser) if "wrench adapter" in t]
    assert len(wrench) == 1
    assert "attached" not in wrench[0]
    assert "PHOTO" not in wrench[0]


def test_message_containing_the_word_added_is_kept():
    """SYSTEM_PATTERNS must not match real prose containing 'added'."""
    parser = parse_fixture()
    assert any("propane to the shopping list" in t for t in texts(parser))


def test_message_ending_in_left_is_kept():
    parser = parse_fixture()
    assert any(t.strip() == "Turn left" for t in texts(parser))


def test_genuine_system_messages_are_dropped():
    parser = parse_fixture()
    joined = " ".join(texts(parser))
    assert "created this group" not in joined
    assert "end-to-end encrypted" not in joined
    assert "added Dean Wexler" not in joined


def test_short_messages_are_kept():
    parser = parse_fixture()
    assert any(t.strip() == "ok" for t in texts(parser))


def test_genuine_multiline_message_is_joined():
    parser = parse_fixture()
    placed = [t for t in texts(parser) if "3 frontages" in t]
    assert len(placed) == 1
    assert "basically square" in placed[0]


def test_media_reference_is_preserved():
    parser = parse_fixture()
    media = [m for m in parser.messages if m.get("media_ref")]
    refs = {m["media_ref"] for m in media}
    assert "00000231-PHOTO-2022-08-24-15-28-33.jpg" in refs
```

- [ ] **Step 5: Run the tests and confirm they fail for the right reasons**

```bash
cd ~/Snails/scripts
source .venv/bin/activate
python -m pytest tests/test_parse_chats.py -v
```

Expected: `test_genuine_multiline_message_is_joined` and `test_genuine_system_messages_are_dropped` PASS (already-correct behaviour). The other six FAIL. If a test you expect to fail passes, the fixture is not exercising the bug — fix the fixture before writing any parser code.

- [ ] **Step 6: Commit**

```bash
cd ~/Snails
git add scripts/requirements.txt scripts/pytest.ini scripts/tests
git commit -m "test: add failing parser tests covering LTR media lines and system-pattern over-matching"
```

---

### Task 2: Fix message detection and system filtering

**Files:**
- Modify: `scripts/parse_chats.py:20-45` (patterns), `scripts/parse_chats.py:94-113` (`_save_message`)

**Interfaces:**
- Consumes: tests from Task 1.
- Produces: `ChatParser` messages gain a `media_ref: str | None` key. Task 3 writes it to the database.

- [ ] **Step 1: Replace the message and system patterns**

In `scripts/parse_chats.py`, replace the `WHATSAPP_PATTERN` and `SYSTEM_PATTERNS` definitions:

```python
# WhatsApp message pattern. WhatsApp prefixes media/attachment lines with
# U+200E (LEFT-TO-RIGHT MARK) *before* the opening bracket, so the optional
# ‎ prefix is required — without it those lines are swallowed as
# continuations of the previous message. That bug dropped 2,867 lines.
WHATSAPP_PATTERN = re.compile(
    r'^[‎‏]*\[(\d{1,2}/\d{1,2}/\d{2,4}),\s+'
    r'(\d{1,2}:\d{2}:\d{2}\s*[AP]M)\]\s+([^:]+):\s*(.*)$'
)

# Attachment marker inside a message body: ‎<attached: FILENAME>
ATTACHMENT_PATTERN = re.compile(r'<attached:\s*([^>]+)>')

# Invisible bidirectional marks WhatsApp uses to tag machine-generated content.
BIDI_MARKS = '‎‏'
```

**There is deliberately no `SYSTEM_PATTERNS` list.** Delete the existing one.

WhatsApp marks every machine-generated notice by prefixing the *message body* with U+200E. Verified against `PS BUILD 22`: all 31 body-prefixed lines are system notices ("X added Y", "joined using a group link", "This message was deleted"), and no genuine user prose carries the mark. That structural signal is exact, where keyword matching is not — the previous `r'added .+'` with `re.search` discarded "I added the propane to the shopping list", and any anchored rewrite of it would too, because that sentence genuinely contains the word.

- [ ] **Step 2: Rewrite `_save_message` filtering**

Replace the top of `_save_message` (currently `parse_chats.py:94-113`):

```python
    def _save_message(self, msg: Dict, source: str):
        """Save a parsed message, dropping only machine-generated notices.

        Classification is structural, not lexical. WhatsApp prefixes the body
        of every system notice with U+200E; user prose never carries it. A
        message with an attachment is always kept, because the mark there
        belongs to the <attached:> token rather than to the message.
        """
        raw = msg['text']

        # Extract the attachment reference before anything else, so media
        # messages survive as first-class rows pointing at their file.
        media_ref = None
        attachment = ATTACHMENT_PATTERN.search(raw)
        if attachment:
            media_ref = attachment.group(1).strip()
            raw = ATTACHMENT_PATTERN.sub('', raw)

        # Did the ORIGINAL body open with a bidi mark? Check before stripping.
        # Leading whitespace can precede it, so lstrip whitespace only.
        is_system_notice = raw.lstrip().startswith(tuple(BIDI_MARKS))

        clean = raw.strip(BIDI_MARKS + ' \t\n').strip()

        if media_ref is None:
            if is_system_notice:
                return          # "X added Y", "joined using a group link", …
            if not clean:
                return          # genuinely empty
        # Short messages are kept deliberately: "ok", "ya", "👍" carry real
        # social signal for the enrichment pass, and the old len(text) < 3
        # floor was skewing per-person message counts.

        msg['text'] = clean
        msg['media_ref'] = media_ref
        msg['source'] = source
        self.messages.append(msg)
```

Leave the person-stat tracking below this block unchanged.

Worked examples, all covered by Task 1's tests:

| body | media_ref | kept? | why |
|---|---|---|---|
| `‎Jill Pennock added Dean Wexler` | – | no | bidi-prefixed notice |
| `I added the propane to the shopping list` | – | **yes** | no mark; the old pattern dropped this |
| `‎<attached: 00000231-PHOTO….jpg>` | the filename | **yes** | attachment present |
| `Dust? What dust? ‎<attached: X.jpg>` | `X.jpg` | **yes** | caption preserved |
| `‎image omitted` | – | no | bidi-prefixed, nothing recoverable |
| `Turn left` | – | **yes** | no mark |
| `ok` | – | **yes** | short messages are signal |

- [ ] **Step 3: Run the tests**

```bash
cd ~/Snails/scripts
source .venv/bin/activate
python -m pytest tests/test_parse_chats.py -v
```

Expected: all eight PASS.

If `test_message_containing_the_word_added_is_kept` fails, the most likely cause is that the U+200E characters in `tests/fixtures/sample_chat.txt` were written as the literal escape text `‎` rather than as real characters. Verify with:

```bash
cd ~/Snails/scripts
python3 -c "
from pathlib import Path
t = Path('tests/fixtures/sample_chat.txt').read_text()
print('U+200E occurrences:', t.count('‎'))
print('lines containing one:', sum(1 for l in t.splitlines() if '‎' in l))
"
```

Expected: **7 occurrences across 5 lines**. If it reports 0, the marks were written as escape text rather than real characters — rewrite the fixture.

- [ ] **Step 4: Commit**

```bash
cd ~/Snails
git add scripts/parse_chats.py
git commit -m "fix: recover 2,867 LTR-prefixed media messages and stop dropping prose containing 'added'"
```

---

### Task 3: Persist media references and re-ingest all nine exports

**Files:**
- Modify: `scripts/parse_chats.py` (schema + insert)
- Create: `scripts/ingest_all.py`

**Interfaces:**
- Consumes: `ChatParser` with `media_ref` from Task 2.
- Produces: `scripts/output/ps_knowledge.db` with `person_content.media_ref TEXT` populated, and a printed per-group reconciliation table. Task 4 embeds this database.

- [ ] **Step 1: Add the column to the schema**

In `create_database()` in `parse_chats.py`, add `media_ref TEXT` to the `person_content` table definition:

```python
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS person_content (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER,
            content TEXT NOT NULL,
            source TEXT,
            content_type TEXT,
            timestamp TEXT,
            media_ref TEXT,
            FOREIGN KEY (person_id) REFERENCES person(id)
        )
    ''')
```

- [ ] **Step 2: Write it on insert**

In `main()`, change the `INSERT INTO person_content` statement (currently around `parse_chats.py:256`) to include the new column:

```python
            cursor.execute('''
                INSERT INTO person_content
                    (person_id, content, source, content_type, timestamp, media_ref)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                person_id,
                msg['text'],
                msg['source'],
                'sent',
                msg['timestamp'].isoformat() if msg['timestamp'] else None,
                msg.get('media_ref'),
            ))
```

- [ ] **Step 3: Write the ingest driver with reconciliation**

Create `scripts/ingest_all.py`:

```python
#!/usr/bin/env python3
"""
Unzip every WhatsApp export, ingest all of them, and reconcile the row count
against the raw line count so silent parser losses can never go unnoticed again.

Usage:
    python ingest_all.py "../Whatsapp PS Exports" ./output
"""
import re
import sys
import shutil
import sqlite3
import zipfile
import tempfile
from pathlib import Path

from parse_chats import ChatParser, create_database

MESSAGE_LINE = re.compile(r'^[‎‏]*\[\d{1,2}/\d{1,2}/\d{2,4},')


def raw_line_count(path: Path) -> int:
    """Count every line that starts a message, LTR-prefixed or not."""
    with open(path, encoding='utf-8') as f:
        return sum(1 for line in f if MESSAGE_LINE.match(line))


def main():
    export_dir = Path(sys.argv[1])
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('./output')
    output_dir.mkdir(parents=True, exist_ok=True)

    parser = ChatParser()
    report = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for zpath in sorted(export_dir.glob('*.zip')):
            dest = tmp / zpath.stem
            dest.mkdir()
            with zipfile.ZipFile(zpath) as z:
                z.extractall(dest)

            txts = list(dest.glob('*.txt'))
            if not txts:
                print(f"WARNING: no .txt inside {zpath.name}", file=sys.stderr)
                continue

            source = zpath.stem.replace('WhatsApp Chat - ', '')
            before = len(parser.messages)
            parser.parse_file(txts[0], source)
            parsed = len(parser.messages) - before
            raw = raw_line_count(txts[0])
            report.append((source, raw, parsed))

    print(f"\n{'GROUP':<34}{'RAW':>8}{'KEPT':>8}{'DROPPED':>9}{'%':>7}")
    print('-' * 66)
    tr = tk = 0
    for source, raw, parsed in report:
        drop = raw - parsed
        pct = (drop / raw * 100) if raw else 0
        print(f"{source:<34}{raw:>8}{parsed:>8}{drop:>9}{pct:>6.1f}%")
        tr += raw
        tk += parsed
    print('-' * 66)
    print(f"{'TOTAL':<34}{tr:>8}{tk:>8}{tr - tk:>9}{(tr - tk) / tr * 100:>6.1f}%")

    if (tr - tk) / tr > 0.05:
        print("\nERROR: more than 5% of message lines were dropped.", file=sys.stderr)
        print("System notices account for roughly 2-3%. Investigate before shipping.",
              file=sys.stderr)
        sys.exit(1)

    db_path = output_dir / 'ps_knowledge.db'
    if db_path.exists():
        shutil.move(str(db_path), str(db_path) + '.bak')
    create_database(output_dir)
    print(f"\nWrote {db_path}")


if __name__ == '__main__':
    main()
```

The driver reuses `create_database` and the insert path already in `parse_chats.main()`; if that logic is not importable as-is, extract it into a `write_database(parser, output_dir)` function in `parse_chats.py` and call it from both.

- [ ] **Step 4: Run the full ingest**

```bash
cd ~/Snails/scripts
source .venv/bin/activate
python ingest_all.py "../Whatsapp PS Exports" ./output
```

Expected: a nine-row table. Total RAW should be **26,314**. Dropped should be roughly 2–3% (genuine system notices), so KEPT should land near **25,500** — against the current database's 20,362.

If dropped exceeds 5% the script exits non-zero by design. Read the table to see which group regressed.

- [ ] **Step 5: Verify `PS BUILD 22` is present and media survived**

```bash
cd ~/Snails/scripts
sqlite3 output/ps_knowledge.db \
  "select source, count(*) from person_content group by 1 order by 2 desc;"
sqlite3 output/ps_knowledge.db \
  "select count(*) from person_content where media_ref is not null;"
```

Expected: nine sources including `PS BUILD 22`; media_ref count in the low thousands.

- [ ] **Step 6: Commit**

```bash
cd ~/Snails
git add scripts/parse_chats.py scripts/ingest_all.py
git commit -m "feat: ingest all nine exports with media refs and count reconciliation"
```

---

### Task 3.5: Make the full corpus build a single command

**Added mid-execution.** Task 3 rebuilt the database from scratch, but `create_database()` only creates `person` and `person_content`. The document-derived tables — `camp_knowledge` (601), `roster` (790), `shifts` (2087) — are produced by `parse_docs.py` and `ingest_additional_context.py`, which `ingest_all.py` never invoked. The rebuild silently dropped all three.

The ordering was written into Task 6's README text rather than into the pipeline, so it was documented but never executed. That is the same class of defect this plan exists to eliminate: a correctness requirement living in prose instead of in code. The fix is to make one command build the whole corpus.

**Files:**
- Modify: `scripts/ingest_all.py` — orchestrate all three stages
- Delete: `Expanding Knowledge Base_ Burning Man.md` (repo root; byte-identical duplicate of the copy in `Additional-context/`)

**Interfaces:**
- Consumes: `write_database` from Task 3.
- Produces: a database with all seven source tables populated, ready for Task 4 to embed.

- [ ] **Step 1: Orchestrate the three ingest stages in `ingest_all.py`**

After the chat ingest writes the database, invoke the two document stages in order. Import and call them rather than shelling out, so a failure surfaces as an exception rather than an ignored exit code:

```python
# Stage 2: camp documentation → camp_knowledge, roster, shifts
# Stage 3: Burning Man context → additional camp_knowledge rows
#
# These MUST run after the chat ingest, because create_database() recreates
# the file from scratch and would otherwise destroy their tables. Encoding the
# order here rather than in the README is deliberate: an earlier version of
# this pipeline documented the order in prose and silently shipped a database
# missing 601 knowledge rows, 790 roster rows and 2,087 shift rows.
```

Both stages must run against the same `output_dir / 'ps_knowledge.db'` the chat stage just wrote.

- [ ] **Step 2: Verify every table is populated before declaring success**

Add a final check to `ingest_all.py` that fails loudly if any expected table is missing or empty:

```python
EXPECTED_TABLES = {
    'person': 1,
    'person_content': 20000,
    'camp_knowledge': 500,
    'roster': 700,
    'shifts': 2000,
}
```

For each, assert the table exists and holds at least the given floor; print the actual count. Exit non-zero listing every table that failed. These floors are deliberately below the known-good values (601 / 790 / 2087) so that normal source-data drift does not trip them, while a table vanishing entirely does.

- [ ] **Step 3: Remove the duplicate knowledge file**

`Expanding Knowledge Base_ Burning Man.md` at the repository root is byte-identical to `Additional-context/Expanding Knowledge Base_ Burning Man.md` (both 8,129 bytes, verified with `diff -q`). `ingest_additional_context.py` reads only from `Additional-context/`, so the root copy is never ingested and only creates ambiguity about which is authoritative.

```bash
cd ~/Snails
diff -q "Expanding Knowledge Base_ Burning Man.md" "Additional-context/Expanding Knowledge Base_ Burning Man.md"
rm "Expanding Knowledge Base_ Burning Man.md"
```

Re-run the `diff` first and only delete if it reports the files identical.

- [ ] **Step 4: Rebuild and verify**

```bash
cd ~/Snails/scripts
source .venv/bin/activate
python ingest_all.py "../Whatsapp PS Exports" ./output
sqlite3 output/ps_knowledge.db ".tables"
sqlite3 output/ps_knowledge.db "
  select 'person_content', count(*) from person_content
  union all select 'camp_knowledge', count(*) from camp_knowledge
  union all select 'roster', count(*) from roster
  union all select 'shifts', count(*) from shifts;"
```

Expected: `person_content` ≈ 24,192; `camp_knowledge` ≈ 601; `roster` ≈ 790; `shifts` ≈ 2087. Report the actual figures. A material deviation from the reference counts means a document source moved or a parser regressed — investigate rather than accepting it.

- [ ] **Step 5: Commit**

```bash
cd ~/Snails
git add scripts/ingest_all.py
git rm "Expanding Knowledge Base_ Burning Man.md"
git commit -m "fix: build the whole corpus in one command, not three documented ones"
```

---

### Task 4: Generate EmbeddingGemma vectors as Float32 BLOBs

**Files:**
- Create: `scripts/embed_corpus.py`
- Create: `scripts/tests/test_embed_corpus.py`

**Interfaces:**
- Consumes: `scripts/output/ps_knowledge.db` from Task 3.
- Produces: `embeddings` and `knowledge_embeddings` tables with `embedding BLOB` (768 Float32 LE) and `model_name = 'embeddinggemma-300M-Q8_0'`. Exposes `pack_vector(list[float]) -> bytes` and `unpack_vector(bytes) -> list[float]`, which Task 5 and the iOS loader must agree with.

- [ ] **Step 1: Download the embedding model**

```bash
cd ~/Snails/scripts
mkdir -p models
curl -L -o models/embeddinggemma-300M-Q8_0.gguf \
  https://huggingface.co/ggml-org/embeddinggemma-300M-GGUF/resolve/main/embeddinggemma-300M-Q8_0.gguf
ls -lh models/embeddinggemma-300M-Q8_0.gguf
```

Expected: 334 MB. Add `scripts/models/` to `.gitignore`.

- [ ] **Step 2: Write the failing round-trip test**

Create `scripts/tests/test_embed_corpus.py`:

```python
import sys
import struct
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embed_corpus import pack_vector, unpack_vector, EMBED_DIM


def test_pack_produces_float32_little_endian():
    packed = pack_vector([1.0, -2.5, 0.0])
    assert packed == struct.pack('<3f', 1.0, -2.5, 0.0)


def test_round_trip_preserves_values():
    original = [0.125, -0.5, 1.0, 0.0]
    assert unpack_vector(pack_vector(original)) == original


def test_packed_length_matches_dimension():
    packed = pack_vector([0.0] * EMBED_DIM)
    assert len(packed) == EMBED_DIM * 4
```

Add a second test file, `scripts/tests/test_embedding_semantics.py`. Correctly-shaped vectors are not the same thing as meaningful ones — this project already shipped 512-dim vectors that scored zero against every query, and the shape check passed the whole time. This test is the guard against that class of failure:

```python
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MODEL = Path(__file__).resolve().parent.parent / "models" / "embeddinggemma-300M-Q8_0.gguf"

pytestmark = pytest.mark.skipif(
    not MODEL.exists(), reason="embedding model not downloaded"
)


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb)


@pytest.fixture(scope="module")
def llm():
    from embed_corpus import load_model
    return load_model(str(MODEL))


def test_embedding_has_expected_dimension(llm):
    from embed_corpus import EMBED_DIM
    assert len(llm.embed("how much propane does the burner need")) == EMBED_DIM


def test_identical_text_embeds_identically(llm):
    a = llm.embed("the container delivery is booked for Thursday")
    b = llm.embed("the container delivery is booked for Thursday")
    assert cosine(a, b) > 0.999


def test_related_text_scores_higher_than_unrelated(llm):
    """The actual point of an embedding. A model that returns constant or
    near-random vectors passes every shape check and fails this one."""
    fuel_a = llm.embed("how much propane does the pancake burner need")
    fuel_b = llm.embed("what quantity of fuel for the camp stove")
    music = llm.embed("the DJ played deep house until sunrise")

    assert cosine(fuel_a, fuel_b) > cosine(fuel_a, music)


def test_vectors_are_not_degenerate(llm):
    """Guards against an all-zero or constant vector, which would make every
    cosine similarity identical and retrieval uniformly useless."""
    v = llm.embed("MOOP sweep starts at 9am on the Esplanade")
    assert max(v) != min(v)
    assert any(abs(x) > 1e-6 for x in v)
```

```bash
cd ~/Snails/scripts
source .venv/bin/activate
python -m pytest tests/test_embed_corpus.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'embed_corpus'`.

- [ ] **Step 3: Write the embedder**

Create `scripts/embed_corpus.py`:

```python
#!/usr/bin/env python3
"""
Generate EmbeddingGemma vectors for person_content and camp_knowledge.

Vectors are produced by llama-cpp-python against the SAME GGUF the iOS app
loads, so corpus vectors and query vectors come from identical code. Using a
different implementation on either side (e.g. sentence-transformers here,
llama.cpp there) risks subtly different pooling or normalisation, which shows
up as quietly degraded retrieval rather than an error.

Usage:
    python embed_corpus.py ./output/ps_knowledge.db ./models/embeddinggemma-300M-Q8_0.gguf
"""
import sys
import struct
import sqlite3
from pathlib import Path

import llama_cpp
from llama_cpp import Llama
from tqdm import tqdm

EMBED_DIM = 768
MODEL_NAME = 'embeddinggemma-300M-Q8_0'


def pack_vector(values) -> bytes:
    """Pack floats as little-endian Float32. iOS reads these directly."""
    return struct.pack(f'<{len(values)}f', *values)


def unpack_vector(blob: bytes):
    return list(struct.unpack(f'<{len(blob) // 4}f', blob))


def load_model(model_path: str) -> Llama:
    """Load EmbeddingGemma for sequence-level embeddings.

    pooling_type is set explicitly with the named constant rather than left to
    the GGUF metadata: llama-cpp-python raises "Failed to get embeddings from
    sequence, pooling type is not set" when a model ships without it, and a
    magic integer here would be unreadable and easy to get wrong.
    """
    return Llama(
        model_path=model_path,
        embedding=True,
        pooling_type=llama_cpp.LLAMA_POOLING_TYPE_MEAN,
        n_ctx=2048,
        verbose=False,
    )


def ensure_schema(conn):
    conn.executescript('''
        DROP TABLE IF EXISTS embeddings;
        DROP TABLE IF EXISTS knowledge_embeddings;

        CREATE TABLE embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id INTEGER UNIQUE NOT NULL,
            embedding BLOB NOT NULL,
            model_name TEXT NOT NULL,
            FOREIGN KEY (content_id) REFERENCES person_content(id)
        );
        CREATE INDEX idx_emb_content ON embeddings(content_id);

        CREATE TABLE knowledge_embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            knowledge_id INTEGER UNIQUE NOT NULL,
            embedding BLOB NOT NULL,
            model_name TEXT NOT NULL,
            FOREIGN KEY (knowledge_id) REFERENCES camp_knowledge(id)
        );
        CREATE INDEX idx_kemb_knowledge ON knowledge_embeddings(knowledge_id);
    ''')
    conn.commit()


def embed_table(conn, llm, select_sql, insert_sql, label):
    rows = conn.execute(select_sql).fetchall()
    inserted = 0
    for row_id, text in tqdm(rows, desc=label):
        if not text or not text.strip():
            continue
        vector = llm.embed(text)
        if len(vector) != EMBED_DIM:
            raise SystemExit(
                f"{label}: expected {EMBED_DIM} dims, model returned {len(vector)}"
            )
        conn.execute(insert_sql, (row_id, pack_vector(vector), MODEL_NAME))
        inserted += 1
    conn.commit()
    return len(rows), inserted


def main():
    db_path = Path(sys.argv[1])
    model_path = Path(sys.argv[2])

    llm = load_model(str(model_path))

    conn = sqlite3.connect(db_path)
    ensure_schema(conn)

    total_c, done_c = embed_table(
        conn, llm,
        "SELECT id, content FROM person_content",
        "INSERT INTO embeddings (content_id, embedding, model_name) VALUES (?, ?, ?)",
        "person_content",
    )
    total_k, done_k = embed_table(
        conn, llm,
        "SELECT id, title || '\n\n' || content FROM camp_knowledge",
        "INSERT INTO knowledge_embeddings (knowledge_id, embedding, model_name) VALUES (?, ?, ?)",
        "camp_knowledge",
    )

    print(f"\nperson_content:  {done_c}/{total_c} embedded")
    print(f"camp_knowledge:  {done_k}/{total_k} embedded")

    if done_k < total_k:
        raise SystemExit(
            f"ERROR: {total_k - done_k} camp_knowledge rows have no embedding. "
            "This is the defect that left 72% of camp docs unsearchable. "
            "Every row must be embedded."
        )

    conn.close()


if __name__ == '__main__':
    main()
```

- [ ] **Step 4: Run the round-trip tests**

```bash
cd ~/Snails/scripts
source .venv/bin/activate
python -m pytest tests/test_embed_corpus.py tests/test_embedding_semantics.py -v
```

Expected: the three packing tests PASS, and the four semantic tests PASS once the model is downloaded (they skip if it is not).

`test_related_text_scores_higher_than_unrelated` is the one that matters. If it fails, the vectors are shaped correctly but carry no meaning — check that `pooling_type` took effect and that `embed()` is returning sequence-level rather than token-level output. Do not proceed to Step 5 with that test red; embedding 25,000 messages with a broken model wastes an hour and produces a database that looks fine and retrieves nothing.

- [ ] **Step 5: Embed the corpus**

```bash
cd ~/Snails/scripts
source .venv/bin/activate
python embed_corpus.py ./output/ps_knowledge.db ./models/embeddinggemma-300M-Q8_0.gguf
```

Expected: two progress bars, then `camp_knowledge: 601/601 embedded` — no shortfall. The script exits non-zero if any knowledge row is unembedded, which is the guard against the original defect recurring.

- [ ] **Step 6: Verify coverage and storage format**

```bash
cd ~/Snails/scripts
sqlite3 output/ps_knowledge.db "
  select 'knowledge', count(*), typeof(embedding), length(embedding)
  from knowledge_embeddings group by 3,4;
  select 'content', count(*), typeof(embedding), length(embedding)
  from embeddings group by 3,4;
  select (select count(*) from camp_knowledge) - (select count(*) from knowledge_embeddings)
    as knowledge_gap;
"
```

Expected: `typeof` is `blob`, `length` is **3072** (768 × 4), `knowledge_gap` is **0**.

- [ ] **Step 7: Commit**

```bash
cd ~/Snails
git add scripts/embed_corpus.py scripts/tests/test_embed_corpus.py .gitignore
git commit -m "feat: embed corpus with EmbeddingGemma as Float32 BLOBs, fail loudly on coverage gaps"
```

---

### Task 5: Stamp the manifest and package the database

**Files:**
- Create: `scripts/package_db.py`
- Create: `scripts/tests/test_package_db.py`

**Interfaces:**
- Consumes: the embedded database from Task 4.
- Produces: a `meta` table with keys `embedding_model`, `embedding_dim`, `schema_version`, `built_at`, `source_commit`, `message_count`, `knowledge_count`. The iOS loader validates `embedding_model` and `embedding_dim` at launch and refuses to run on a mismatch.

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_package_db.py`:

```python
import sys
import sqlite3
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from package_db import write_manifest, REQUIRED_KEYS


def make_db(tmp_path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.executescript('''
        CREATE TABLE person_content (id INTEGER PRIMARY KEY, content TEXT);
        CREATE TABLE camp_knowledge (id INTEGER PRIMARY KEY, title TEXT, content TEXT);
        CREATE TABLE knowledge_embeddings (knowledge_id INTEGER, embedding BLOB, model_name TEXT);
        INSERT INTO person_content (content) VALUES ('hello');
        INSERT INTO camp_knowledge (title, content) VALUES ('t', 'c');
        INSERT INTO knowledge_embeddings VALUES (1, zeroblob(3072), 'embeddinggemma-300M-Q8_0');
    ''')
    conn.commit()
    return conn, db


def test_manifest_contains_every_required_key(tmp_path):
    conn, _ = make_db(tmp_path)
    write_manifest(conn, source_commit="abc123")
    rows = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    for key in REQUIRED_KEYS:
        assert key in rows, f"missing manifest key: {key}"


def test_manifest_records_dimension_and_model(tmp_path):
    conn, _ = make_db(tmp_path)
    write_manifest(conn, source_commit="abc123")
    rows = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    assert rows["embedding_dim"] == "768"
    assert rows["embedding_model"] == "embeddinggemma-300M-Q8_0"


def test_manifest_rejects_unembedded_knowledge(tmp_path):
    conn, _ = make_db(tmp_path)
    conn.execute("INSERT INTO camp_knowledge (title, content) VALUES ('t2', 'c2')")
    conn.commit()
    try:
        write_manifest(conn, source_commit="abc123")
    except SystemExit:
        return
    raise AssertionError("expected SystemExit when knowledge rows lack embeddings")
```

```bash
cd ~/Snails/scripts
source .venv/bin/activate
python -m pytest tests/test_package_db.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'package_db'`.

- [ ] **Step 2: Write the packager**

Create `scripts/package_db.py`:

```python
#!/usr/bin/env python3
"""
Stamp a build manifest into the knowledge database and copy it to the app.

The meta table is the guard against the defect that shipped 384-dim vectors
into a 512-dim runtime: the app validates embedding_model and embedding_dim at
launch and refuses to start on a mismatch, instead of silently returning zero
similarity for every query.

Usage:
    python package_db.py ./output/ps_knowledge.db [--install]
"""
import sys
import sqlite3
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

EMBED_MODEL = 'embeddinggemma-300M-Q8_0'
EMBED_DIM = 768
SCHEMA_VERSION = '2'

REQUIRED_KEYS = (
    'embedding_model', 'embedding_dim', 'schema_version',
    'built_at', 'source_commit', 'message_count', 'knowledge_count',
)

APP_DB = Path(__file__).resolve().parent.parent / 'LucyPT' / 'LucyPT' / 'ps_knowledge.db'


def write_manifest(conn, source_commit: str):
    knowledge = conn.execute("SELECT COUNT(*) FROM camp_knowledge").fetchone()[0]
    embedded = conn.execute("SELECT COUNT(*) FROM knowledge_embeddings").fetchone()[0]
    if embedded < knowledge:
        raise SystemExit(
            f"refusing to package: {knowledge - embedded} of {knowledge} "
            "camp_knowledge rows have no embedding"
        )

    messages = conn.execute("SELECT COUNT(*) FROM person_content").fetchone()[0]

    conn.executescript('''
        DROP TABLE IF EXISTS meta;
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    ''')
    conn.executemany(
        "INSERT INTO meta (key, value) VALUES (?, ?)",
        [
            ('embedding_model', EMBED_MODEL),
            ('embedding_dim', str(EMBED_DIM)),
            ('schema_version', SCHEMA_VERSION),
            ('built_at', datetime.now(timezone.utc).isoformat()),
            ('source_commit', source_commit),
            ('message_count', str(messages)),
            ('knowledge_count', str(knowledge)),
        ],
    )
    conn.commit()


def main():
    db_path = Path(sys.argv[1])
    install = '--install' in sys.argv

    commit = subprocess.run(
        ['git', 'rev-parse', 'HEAD'],
        capture_output=True, text=True, cwd=db_path.parent,
    ).stdout.strip() or 'unknown'

    conn = sqlite3.connect(db_path)
    write_manifest(conn, commit)

    print("manifest:")
    for key, value in conn.execute("SELECT key, value FROM meta ORDER BY key"):
        print(f"  {key:<18} {value}")

    conn.execute("VACUUM")
    conn.close()

    size_mb = db_path.stat().st_size / 1_048_576
    print(f"\n{db_path} — {size_mb:.0f} MB")

    if install:
        shutil.copy2(db_path, APP_DB)
        print(f"installed to {APP_DB}")


if __name__ == '__main__':
    main()
```

- [ ] **Step 3: Run the tests**

```bash
cd ~/Snails/scripts
source .venv/bin/activate
python -m pytest tests/ -v
```

Expected: all tests across all three files PASS.

- [ ] **Step 4: Package and inspect**

```bash
cd ~/Snails/scripts
source .venv/bin/activate
python package_db.py ./output/ps_knowledge.db
```

Expected: the manifest prints with `embedding_dim 768`, `knowledge_count 601`, and a message count near 25,500. The BLOB conversion plus `VACUUM` should bring the file well below the current 193 MB.

- [ ] **Step 5: Install into the app bundle**

```bash
cd ~/Snails/scripts
source .venv/bin/activate
python package_db.py ./output/ps_knowledge.db --install
ls -lh ../LucyPT/LucyPT/ps_knowledge.db
```

Expected: copied. This replaces the manual `cp` in `README.md:74` that caused the dimension-mismatch defect — `package_db.py --install` is now the only supported way to update the shipped database.

- [ ] **Step 6: Commit**

```bash
cd ~/Snails
git add scripts/package_db.py scripts/tests/test_package_db.py
git commit -m "feat: stamp build manifest and package DB, replacing the unguarded cp step"
```

---

### Task 6: Correct the stale documentation

**Files:**
- Modify: `README.md:47-88` (Setup / Data Pipeline)
- Modify: `SnailsNative/CLAUDE.md:11-56`

**Interfaces:**
- Consumes: the working pipeline from Tasks 1–5.
- Produces: documentation matching the code. No behavioural change.

- [ ] **Step 1: Rewrite the pipeline section of README.md**

Replace the "Data Pipeline" block (currently `README.md:56-75`):

````markdown
### Data Pipeline

```bash
cd scripts
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# One-time: fetch the embedding model (334 MB)
mkdir -p models
curl -L -o models/embeddinggemma-300M-Q8_0.gguf \
  https://huggingface.co/ggml-org/embeddinggemma-300M-GGUF/resolve/main/embeddinggemma-300M-Q8_0.gguf

# 1. Ingest every WhatsApp export, with loss reconciliation
python ingest_all.py "../Whatsapp PS Exports" ./output

# 2. Parse documents and additional context
python parse_docs.py
python ingest_additional_context.py

# 3. Generate 768-dim EmbeddingGemma vectors (fails if any doc is unembedded)
python embed_corpus.py ./output/ps_knowledge.db ./models/embeddinggemma-300M-Q8_0.gguf

# 4. Stamp the manifest and install into the app bundle
python package_db.py ./output/ps_knowledge.db --install
```

**Never `cp` the database into the app manually.** `package_db.py --install` stamps the
`meta` table the app validates at launch; a hand-copied database will be rejected.
````

- [ ] **Step 2: Correct the architecture claims in CLAUDE.md**

In `SnailsNative/CLAUDE.md`, fix the three documented-but-false claims:

- Replace `LLM-based classification (JSON structured output)` under QueryRouter with: `Regex/keyword routing (being replaced by LLM structured extraction — see docs/superpowers/specs/2026-08-09-lucy-pt-architecture-design.md §6)`
- Replace `EmbeddingService - MiniLM-L6-v2 embeddings (384 dim)` with `EmbeddingService - EmbeddingGemma 300M (768 dim), vectors stored as Float32 BLOBs`
- Replace the table list `(people, document_chunks, recipes, shift_assignments, chat_messages)` with the real names: `(person, person_content, camp_knowledge, roster, shifts, embeddings, knowledge_embeddings, meta)`

- [ ] **Step 3: Verify the documented commands actually run**

```bash
cd ~/Snails/scripts
source .venv/bin/activate
rm -rf output/ps_knowledge.db
python ingest_all.py "../Whatsapp PS Exports" ./output
python embed_corpus.py ./output/ps_knowledge.db ./models/embeddinggemma-300M-Q8_0.gguf
python package_db.py ./output/ps_knowledge.db
```

Expected: a clean end-to-end run with no manual intervention. Any step that needs a hand-edit to work is a documentation bug — fix the doc.

- [ ] **Step 4: Commit**

```bash
cd ~/Snails
git add README.md SnailsNative/CLAUDE.md
git commit -m "docs: correct pipeline instructions and stale architecture claims"
```

---

## Next plan

Enrichment (`person_profile`, `expertise`, `relationship`, `lore`, `entity` with `evidence_ref`) is the second half of spec §6 Tier 1 and gets its own plan once this database is rebuilt and verified. It depends on Task 5's output and on nothing in the iOS app.
