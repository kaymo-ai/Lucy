#!/usr/bin/env python3
"""
Client for the enrichment passes, against Google's Agent Platform.

The product this talks to was called Vertex AI when this file was written and
is now Agent Platform. The module name and the `vertex_client` import in nine
enrich_*.py files are historical -- renaming them is churn, not a fix.

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
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from google import genai

from enrich_cache import Cache, fingerprint

PROJECT = os.environ.get("GCP_PROJECT", "lucy-snails")

# "global", and this is a property of the MODEL, not a stale probe result.
#
# gemini-3.6-flash is served from the global endpoint. Asking us-central1 for
# it returns 404 NOT_FOUND, tried 2026-08-20. The console model card lists it
# as available, which is true and is a different question -- the project has
# access, the region has no endpoint. That distinction is what makes this look
# like a permissions problem when it is a routing one.
#
# Consequence worth knowing: the global endpoint carries its OWN quota,
# reported under the `..._global` metrics rather than the regional ones. Any
# quota reading taken against us-central1 says nothing about what this uses.
#
# Data residency does not survive this: global routing gives no regional
# guarantee. Irrelevant here -- it is a Burning Man camp's group chat.
LOCATION = "global"

# Pro, and this was MEASURED rather than assumed. gemini-3.6-flash ran the
# identical corpus (27,163 messages, 627 documents, byte for byte) on
# 2026-08-20 and found 64 distinct entities against Pro's 149.
#
# The headcount is not the interesting part. Flash keeps what you can
# photograph and loses what the camp SAYS:
#
#     kind        Pro   Flash
#     tradition    70      24
#     asset        26       7
#     place        16       6
#     vehicle      12      11     <- buses survive almost intact
#
# The hundred names it dropped are the camp's own language -- Edd's law,
# Ketamine Kittens, It Was Better Last Year, decomrecom, Cajun microwave
# fire, Meatany, the burn book. It also misread "Fickle Packtory" as "Fickle
# Pactory", so it is not only recall.
#
# Enrichment reads rambling group chat and decides that a phrase is a thing
# the camp has a name for. That is judgement, and the plan called it right the
# first time: capability beats throughput on this pass.
MODEL = "gemini-3.1-pro-preview"

MAX_WORKERS = 8          # never measured against the real quota; see below
MAX_RETRIES = 3

# A quota error is not a transient error and must not be backed off like one.
# 1s then 2s does not outlast a 429 storm, and the old path then mapped the
# job to None -- a hole in the corpus, reported as one WARNING line on stderr
# while the pass committed and exited 0.
QUOTA_RETRIES = 6
QUOTA_BASE_DELAY = 4.0      # 4, 8, 16, 32, 64, 128s -- minutes, because a
                            # per-minute quota heals in minutes

# How much of a pass may fail before the pass is a failure.
#
# There was no threshold at all. run_all printed a WARNING, both store()
# functions printed another, and the pass committed partial results and
# exited 0 -- so a quota event became a quietly incomplete corpus with the
# loss recorded only in scrollback. On 2026-08-20 exactly one job of 124 was
# lost this way and it cost a campmate his entire profile; nothing downstream
# knew.
#
# 2% rather than zero: a single safety-blocked message should not throw away
# a 1,100-job pass, and losing a fiftieth of a pass should stop everything.
MAX_FAILURE_RATE = 0.02


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
        # Reading .text can itself raise: a safety-blocked response, or one cut
        # off at MAX_TOKENS, may carry no usable candidate. That must cost one
        # job, not the whole pass — so catch broadly rather than naming the
        # SDK's exception types, which vary by failure mode and by version.
        try:
            out[job_id] = json.loads(response.text)
        except Exception:
            out[job_id] = None
    return out


def _is_quota(exc: Exception) -> bool:
    """A rate-limit or resource-exhausted error, by any of its spellings.

    Matched on the message because the SDK's exception types vary by failure
    mode and by version -- the same reason RefreshError is matched by name.
    """
    text = f"{type(exc).__name__} {exc}".lower()
    return ("429" in text or "resource_exhausted" in text
            or "resourceexhausted" in text or "rate limit" in text
            or "quota" in text)


def _one(c, job_id, prompt, config):
    # Two counters, on purpose. A quota wait is not evidence that the request
    # is broken, so it must not consume the budget for retries that ARE.
    attempt = 0
    quota_attempt = 0
    while True:
        try:
            resp = c.models.generate_content(
                model=MODEL, contents=prompt, config=config)
            return (job_id, resp, None)
        except Exception as exc:            # quota, transient 5xx
            # One failure is total, not per-job: expired application-default
            # credentials. Every job in the pool fails identically, retrying
            # cannot heal it, and mapping it to None turned an expired login
            # into "0 stories written" with no error in sight (2026-08-11 —
            # three whole passes reported empty results while the model was
            # never reached). Matched by name so this file still imports if
            # google-auth moves the class.
            if type(exc).__name__ == "RefreshError":
                raise SystemExit(
                    "Vertex credentials expired — run "
                    "`gcloud auth application-default login` and re-run. "
                    f"({exc})")
            # A 429 gets its own, much longer ladder. The old code treated
            # it as a transient blip and gave up after 3 seconds.
            if _is_quota(exc):
                if quota_attempt >= QUOTA_RETRIES - 1:
                    return (job_id, None, exc)
                delay = QUOTA_BASE_DELAY * (2 ** quota_attempt)
                quota_attempt += 1
                time.sleep(delay)
                continue          # deliberately NOT counted against attempt

            if attempt >= MAX_RETRIES - 1:
                return (job_id, None, exc)
            attempt += 1
            time.sleep(2 ** attempt)


def run_all(jobs: list[tuple[str, str]], system_text: str, schema: dict,
            pass_name: str = "") -> dict:
    """jobs is [(job_id, prompt), ...]. Returns {job_id: parsed | None}.

    Every enrichment pass funnels through here, which is why the cache lives
    here and not in each of them: one chokepoint makes all eight incremental
    at once, and none of them had to learn about it.

    A job is skipped when this exact (model, system prompt, schema, prompt)
    has been answered before. That is content addressing, not row ids --
    ingest_all recreates the database and reassigns every autoincrement, so a
    row-keyed cache would confidently return another message's answer.

    Measured 2026-08-20: 1,830 of 27,163 messages were newer than 23 January.
    Six per cent new, and every rebuild re-derived the other ninety-four.
    """
    cache = Cache()
    config = build_config(schema, system_text)

    # Each pass is its own process, so the script being run IS the pass name.
    # The alternative was threading a label through eight call sites for a
    # column nothing reads at runtime -- this keeps `python enrich_cache.py`
    # able to say WHICH pass a cached answer belongs to, which is the first
    # question asked when something does not hit.
    pass_name = pass_name or Path(sys.argv[0]).stem or "?"

    results: dict = {}
    pending: list[tuple[str, str, str]] = []      # (job_id, prompt, fingerprint)
    for jid, prompt in jobs:
        fp = fingerprint(MODEL, system_text, schema, prompt)
        hit = cache.get(fp)
        if hit is not None:
            results[jid] = hit
        else:
            pending.append((jid, prompt, fp))

    print(f"  {cache.report()}", file=sys.stderr)
    if not pending:
        return results

    c = client()
    finished = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(_one, c, jid, prompt, config)
                   for jid, prompt, _ in pending]
        for i, f in enumerate(futures, 1):
            finished.append(f.result())
            if i % 25 == 0:
                print(f"  {i}/{len(pending)}", file=sys.stderr)

    fresh = collect_results(finished)
    results.update(fresh)

    # Only successful answers are cached. Caching a None would make a
    # transient 5xx permanent -- the job would never be retried on any later
    # run, and the corpus would carry that gap for good.
    by_id = {jid: fp for jid, _, fp in pending}
    for jid, payload in fresh.items():
        if payload is not None:
            cache.put(by_id[jid], payload, pass_name=pass_name, model=MODEL)
    cache.commit()

    failed = [k for k, v in results.items() if v is None]
    if failed:
        print(f"WARNING: {len(failed)} jobs returned nothing: {failed[:10]}",
              file=sys.stderr)

    # A threshold, because a WARNING is not one.
    #
    # Every pass used to print this line, commit whatever came back, and exit
    # 0. On 2026-08-20 one job of 124 was lost that way -- Caspar G, 47
    # messages -- and he simply had no profile. Lucy would have said she did
    # not know much about him, confidently, and nothing anywhere recorded that
    # a person had gone missing.
    #
    # Raising MAX_WORKERS without this converts a quota event into a quietly
    # incomplete corpus, which is why the concurrency raise waits on it.
    rate = len(failed) / len(jobs) if jobs else 0.0
    if rate > MAX_FAILURE_RATE:
        raise SystemExit(
            f"{len(failed)} of {len(jobs)} jobs returned nothing "
            f"({rate:.0%}), past the {MAX_FAILURE_RATE:.0%} limit.\n"
            f"Refusing to commit a partial pass. Failures are not cached, so "
            f"re-running retries exactly the ones that failed.")
    return results
