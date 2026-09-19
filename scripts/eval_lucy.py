#!/usr/bin/env python3
"""The common-questions regression gate.

Runs evals/questions.yaml against the shipped preview database through the
Python mirror of the app's answer path (lucy_mirror.py). Layer 1 (retrieval
and composition) always runs and is deterministic. Layer 2 (the model) is
optional and needs local GGUFs.

    python3 eval_lucy.py                       # layer 1 + golden check
    python3 eval_lucy.py --update-golden       # pin current behaviour
    python3 eval_lucy.py --model both --compare --models-dir ~/lucy-models

Exit codes: 0 clean (xfails allowed), 1 failures, 2 usage/environment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lucy_mirror

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

_CASE_KEYS = {"id", "q", "category", "previous", "retrieval", "answer", "xfail"}
_RETRIEVAL_KEYS = {"must_hit", "must_not_hit"}
_ANSWER_KEYS = {"must_mention", "must_not_mention", "may_refuse", "must_refuse"}


@dataclass
class Case:
    id: str
    q: str
    category: str
    previous: str | None = None
    must_hit: list[str] = field(default_factory=list)
    must_not_hit: list[str] = field(default_factory=list)
    must_mention: list[str] = field(default_factory=list)
    must_not_mention: list[str] = field(default_factory=list)
    may_refuse: bool = False
    must_refuse: bool = False
    xfail: str | None = None


def _die(msg: str) -> None:
    print(f"eval_lucy: {msg}", file=sys.stderr)
    raise SystemExit(2)


def load_cases(path: Path | str) -> list[Case]:
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, list) or not raw:
        _die(f"{path}: expected a non-empty list of cases")
    cases: list[Case] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            _die(f"case #{i}: not a mapping")
        unknown = set(entry) - _CASE_KEYS
        if unknown:
            _die(f"case #{i} ({entry.get('id')}): unknown keys {sorted(unknown)}")
        for key, allowed in (("retrieval", _RETRIEVAL_KEYS),
                             ("answer", _ANSWER_KEYS)):
            sub = entry.get(key) or {}
            bad = set(sub) - allowed
            if bad:
                _die(f"case {entry.get('id')}: unknown {key} keys {sorted(bad)}")
        cid = entry.get("id")
        if not cid or not entry.get("q") or not entry.get("category"):
            _die(f"case #{i}: id, q and category are required")
        if cid in seen:
            _die(f"duplicate case id: {cid}")
        seen.add(cid)
        r = entry.get("retrieval") or {}
        a = entry.get("answer") or {}
        cases.append(Case(
            id=cid, q=entry["q"], category=entry["category"],
            previous=entry.get("previous"),
            must_hit=list(r.get("must_hit") or []),
            must_not_hit=list(r.get("must_not_hit") or []),
            must_mention=list(a.get("must_mention") or []),
            must_not_mention=list(a.get("must_not_mention") or []),
            may_refuse=bool(a.get("may_refuse", False)),
            must_refuse=bool(a.get("must_refuse", False)),
            xfail=entry.get("xfail"),
        ))
    return cases


@dataclass
class CaseResult:
    case: Case
    terms: list[str]
    sheet: str
    answer_bundle: "lucy_mirror.Answer"
    failures: list[str] = field(default_factory=list)
    golden: dict = field(default_factory=dict)


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_layer1(cases: list[Case], store: "lucy_mirror.Store") -> list[CaseResult]:
    results = []
    for case in cases:
        t = lucy_mirror.blended_terms(case.q, case.previous)
        bundle = lucy_mirror.answer(case.q, store, previous=case.previous)
        sheet = lucy_mirror.fact_sheet(bundle)
        low = sheet.lower()
        failures = [f"missing from facts: {s!r}"
                    for s in case.must_hit if s.lower() not in low]
        failures += [f"present in facts: {s!r}"
                     for s in case.must_not_hit if s.lower() in low]
        results.append(CaseResult(case=case, terms=t, sheet=sheet,
                                  answer_bundle=bundle, failures=failures))
    return results


def golden_for(case: Case, result: CaseResult, db_sha: str) -> dict:
    bundle = result.answer_bundle
    p = lucy_mirror.prompt(case.q, result.sheet)
    return {
        "id": case.id,
        "question": case.q,
        "db_sha256": db_sha,
        "terms": result.terms,
        "camp_ids": [f.id for f in bundle.camp],
        "doc_ids": [d.id for d in bundle.docs],
        "entity_hits": [{"entity_id": h.entity.id,
                         "fact_ids": [f.id for f in h.facts]}
                        for h in bundle.hits],
        "people_sha256": sha256_text("\n".join(pers.name for pers in bundle.people)),
        "general_ids": [g.id for g in bundle.general],
        "fact_sheet_sha256": sha256_text(result.sheet),
        "prompt_sha256": {
            "gemma3": sha256_text(lucy_mirror.chat_wrap(p, "gemma3")),
            "gemma4": sha256_text(lucy_mirror.chat_wrap(p, "gemma4")),
        },
    }


def check_goldens(results: list[CaseResult], golden_dir: Path, db_sha: str,
                  update: bool) -> list[str]:
    golden_dir = Path(golden_dir)
    fails: list[str] = []
    if update:
        golden_dir.mkdir(parents=True, exist_ok=True)
    for r in results:
        r.golden = golden_for(r.case, r, db_sha)
        path = golden_dir / f"{r.case.id}.json"
        if update:
            path.write_text(json.dumps(r.golden, indent=2, ensure_ascii=False)
                            + "\n")
            continue
        if not path.exists():
            fails.append(f"{r.case.id}: no golden — run --update-golden and "
                         "review the diff")
            continue
        stored = json.loads(path.read_text())
        if stored.get("db_sha256") != db_sha:
            fails.append(f"{r.case.id}: database changed since goldens were "
                         "pinned (data drift, not logic drift) — rebuild, "
                         "review, --update-golden")
            continue
        for key in ("terms", "camp_ids", "doc_ids", "entity_hits", "people_sha256",
                    "general_ids", "fact_sheet_sha256", "prompt_sha256"):
            if stored.get(key) != r.golden.get(key):
                fails.append(f"{r.case.id}: golden mismatch on {key} — "
                             f"see output artifacts for the current text")
    return fails


def write_artifacts(results: list[CaseResult], out_dir: Path) -> None:
    # Full text lives here, gitignored — the debuggable side of the hashed
    # goldens. One file per case: terms, chosen rows, the sheet itself.
    out_dir.mkdir(parents=True, exist_ok=True)
    for r in results:
        lines = [f"QUESTION: {r.case.q}", f"TERMS: {r.terms}",
                 f"FAILURES: {r.failures or 'none'}", "",
                 f"PEOPLE: {[p.name for p in r.answer_bundle.people]}", "",
                 "FACT SHEET:", r.sheet, ""]
        (out_dir / f"{r.case.id}.txt").write_text("\n".join(lines))


def report(results: list[CaseResult], golden_fails: list[str]) -> tuple[str, int]:
    hard, xfailed, xpassed = [], [], []
    for r in results:
        if r.failures and r.case.xfail:
            xfailed.append(r)
        elif r.failures:
            hard.append(r)
        elif r.case.xfail:
            xpassed.append(r)
    lines = ["# Lucy eval — layer 1 (retrieval + composition)", ""]
    lines.append(f"{len(results)} cases: {len(hard)} failed, "
                 f"{len(xfailed)} xfail, {len(xpassed)} xpass, "
                 f"{len(golden_fails)} golden mismatches")
    for r in hard:
        lines.append(f"- FAIL {r.case.id}: " + "; ".join(r.failures))
    for f in golden_fails:
        lines.append(f"- GOLDEN {f}")
    for r in xfailed:
        lines.append(f"- xfail {r.case.id} ({r.case.xfail}): "
                     + "; ".join(r.failures))
    for r in xpassed:
        lines.append(f"- XPASS {r.case.id}: expected to fail "
                     f"({r.case.xfail}) but passed — promote it")
    rc = 1 if hard or golden_fails else 0
    return "\n".join(lines), rc


# --- Layer 2: the model -----------------------------------------------------
#
# Raw completion with the app's exact turn markers per model — NEVER a chat
# template. The library's template is where the turn-marker bug would sneak
# back in: markers that tokenise as ordinary text look fine and are wrong.
# Greedy sampling (temperature 0) is a deliberate, documented divergence
# from the app's sampler: the gate needs the same bytes for the same input.

import re as _re

MODEL_FILES = {
    "full": "gemma-4-E2B-it-Q4_K_M.gguf",    # <|turn> markers
    "light": "gemma-3-1b-it-Q4_K_M.gguf",    # <start_of_turn> markers
}
# The style the app would detect by tokenising; asserted at load time below.
MODEL_STYLES = {"full": "gemma4", "light": "gemma3"}

_REFUSALS = [
    r"\bi (do not|don't|cannot|can't) know\b",
    r"not something i'?ve been told",
    r"nothing in what i'?ve got",
    r"we have not written (this|that) down",
    r"\bi'?m not sure\b",
]


def is_refusal(text: str) -> bool:
    low = text.lower()
    return any(_re.search(p, low) for p in _REFUSALS)


def score_answer(case: Case, text: str) -> list[str]:
    if not text:
        return ["empty generation"]
    if is_refusal(text):
        if case.must_refuse or case.may_refuse:
            return []
        return ["refused, and the facts were there"]
    fails: list[str] = []
    if case.must_refuse:
        fails.append("should have refused (not in the corpus) but answered")
    low = text.lower()
    for s in case.must_mention:
        # A plural is the same fact: "voucher" must match "vouchers".
        pattern = rf"(?<![0-9a-z]){_re.escape(s.lower())}(?:s|es)?(?![0-9a-z])"
        if not _re.search(pattern, low):
            fails.append(f"answer omits: {s!r}")
    for s in case.must_not_mention:
        if s.lower() in low:
            fails.append(f"answer contains: {s!r}")
    return fails


def run_models(results: list[CaseResult], generators: dict, out_dir: Path,
               compare: bool) -> int:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    answers: dict[str, dict[str, str]] = {}
    verdicts: dict[str, dict[str, list[str]]] = {}
    rc = 0
    for name, generate in generators.items():
        style = MODEL_STYLES[name]
        answers[name] = {}
        verdicts[name] = {}
        lines = [f"# Lucy eval — answers from {name} ({MODEL_FILES[name]})", ""]
        for r in results:
            wrapped = lucy_mirror.chat_wrap(
                lucy_mirror.prompt(r.case.q, r.sheet), style)
            text = generate(wrapped).strip()
            fails = score_answer(r.case, text)
            answers[name][r.case.id] = text
            verdicts[name][r.case.id] = fails
            if fails and not r.case.xfail:
                rc = 1
            verdict = "ok" if not fails else (
                f"xfail ({r.case.xfail})" if r.case.xfail else "FAIL")
            lines += [f"## {r.case.id} — {verdict}",
                      f"Q: {r.case.q}", "", text, ""]
            if fails:
                lines += ["Failures: " + "; ".join(fails), ""]
        (out_dir / f"answers-{name}.md").write_text("\n".join(lines))
    if compare and len(generators) == 2:
        a, b = sorted(generators)
        lines = ["# Lucy eval — model comparison", "",
                 f"| case | {a} | {b} |", "|---|---|---|"]
        for r in results:
            def cell(m):
                v = "ok" if not verdicts[m][r.case.id] else "fail"
                text = answers[m][r.case.id].replace("\n", " ")
                return f"[{v}] {text}"
            lines.append(f"| {r.case.id} | {cell(a)} | {cell(b)} |")
        (out_dir / "compare.md").write_text("\n".join(lines) + "\n")
    return rc


def run_model_layer(results: list[CaseResult], args) -> int:
    wanted = ["light", "full"] if args.model == "both" else [args.model]
    if not args.models_dir:
        print("model layer skipped: pass --models-dir", file=sys.stderr)
        return 0
    generators = {}
    for name in wanted:
        path = Path(args.models_dir).expanduser() / MODEL_FILES[name]
        if not path.exists():
            print(f"model layer: {path} not present, skipping {name}",
                  file=sys.stderr)
            continue
        try:
            from llama_cpp import Llama
        except ImportError:
            print("model layer skipped: pip install llama-cpp-python",
                  file=sys.stderr)
            return 0
        llm = Llama(model_path=str(path), n_ctx=4096, n_gpu_layers=-1,
                    verbose=False)
        # The app decides markers by tokenising, never by filename
        # (LlamaHandle.turnStyle). Do the same, and refuse a mismatch.
        toks = llm.tokenize(b"<start_of_turn>", add_bos=False, special=True)
        detected = "gemma3" if len(toks) == 1 else "gemma4"
        if detected != MODEL_STYLES[name]:
            _die(f"{path.name} tokenises as {detected}, expected "
                 f"{MODEL_STYLES[name]} — wrong file under that name?")
        def generate(wrapped, _llm=llm):
            out = _llm(prompt=wrapped, max_tokens=320, temperature=0.0,
                       stop=["<end_of_turn>", "<turn|>"])
            return out["choices"][0]["text"]
        generators[name] = generate
    if not generators:
        return 0
    return run_models(results, generators, Path(args.out_dir),
                      compare=args.compare)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(HERE / "output" / "enriched_preview.db"))
    ap.add_argument("--cases", default=str(REPO / "evals" / "questions.yaml"))
    ap.add_argument("--golden-dir", default=str(REPO / "evals" / "golden"))
    ap.add_argument("--out-dir", default=str(HERE / "output" / "eval"))
    ap.add_argument("--update-golden", action="store_true")
    ap.add_argument("--model", choices=["light", "full", "both"])
    ap.add_argument("--models-dir")
    ap.add_argument("--compare", action="store_true")
    args = ap.parse_args(argv)

    db = Path(args.db)
    if not db.exists():
        _die(f"no database at {db} — build it with build_preview_db.py")
    cases = load_cases(args.cases)
    store = lucy_mirror.Store.open(str(db))
    results = run_layer1(cases, store)
    golden_fails = check_goldens(results, Path(args.golden_dir),
                                 sha256_file(db), update=args.update_golden)
    write_artifacts(results, Path(args.out_dir))
    text, rc = report(results, golden_fails)
    print(text)
    (Path(args.out_dir) / "report.md").write_text(text + "\n")
    if args.model:
        rc = max(rc, run_model_layer(results, args))   # Task 8
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
