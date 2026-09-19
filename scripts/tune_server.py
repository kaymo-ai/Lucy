#!/usr/bin/env python3
"""A local bench for Lucy's prompt: change it, run it, see it scored.

Every prompt change in this project has been made, built, installed on a
phone, and judged by reading three answers. docs/learnings-lucy-2026-08-10.md
is the record of what that costs -- a list of confident changes that did
something else -- and the 2026-08-19 pass could not settle whether the turn
marker fix helped, because 81 of 99 turns came from the one day the prompt
changed eight times.

The model runs on this Mac now, so none of that is necessary. This serves one
page: pick a question the camp actually asked, edit two prompts side by side,
run both against the same fact sheet the phone gave her, and read the answers
next to their scores.

WHAT IT CANNOT DO, so nobody mistakes it for the phone: it does not retrieve.
Retrieval is Swift, and reimplementing it here would create a second source of
truth that drifts from the first. The fact sheets come from the journal --
exactly what the phone handed her for that question, recorded at the time -- and
a hand-written fact sheet can be pasted in for anything else. So this bench
measures the prompt and the sampler. It says nothing about retrieval, and the
E7 work had to be measured a different way for exactly that reason.

Usage:
    scripts/.venv/bin/python scripts/tune_server.py <journal.txt> <model.gguf> [--port 8765]
"""
import argparse
import html
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import llama_cpp
from llama_cpp import Llama

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from score_answers import turns, invented, copied, we_rate, leak, words  # noqa: E402
from replay_prompts import (VARIANTS, MARKERS, wrap, turn_style)  # noqa: E402

# One llama context, one generation at a time. The page can fire two requests
# at once (the A/B button does exactly that) and llama_decode on a shared
# context from two threads is a crash, not a race you get to debug. One lock
# across ALL models, not one per model: they are competing for the same GPU
# and the same memory, and two 3 GB contexts decoding at once on a laptop is
# not a throughput win.
LOCK = threading.Lock()
ROWS: list[dict] = []

# name -> {"llm": Llama, "style": "gemma3"|"gemma4"}
#
# Both models loaded at once, because the question "does this prompt still
# work on the small model" is the one the phone actually asks: a 6 GB device
# gets Gemma 3 1B, and every prompt in this project has been tuned against
# the full model alone. They use different turn markers, so a bench that
# hardcoded one pair could not answer that question at all.
MODELS: dict[str, dict] = {}


def templates() -> dict[str, str]:
    """The variants from replay_prompts.py, as editable templates.

    Rendered with {facts} and {question} so the page can hand back anything;
    the two placeholders are the whole contract.
    """
    out = {}
    for name, fn in VARIANTS.items():
        out[name] = fn("{question}", "{facts}")
    return out


def generate(model: str, prompt_text: str, temperature: float, top_k: int,
             max_tokens: int) -> str:
    entry = MODELS.get(model) or next(iter(MODELS.values()))
    with LOCK:
        result = entry["llm"](wrap(prompt_text, entry["style"]),
                              max_tokens=max_tokens, temperature=temperature,
                              top_k=top_k, stop=[MARKERS[entry["style"]][1]])
    return result["choices"][0]["text"].strip()


def scored(question: str, facts: str, answer: str) -> dict:
    t = {"question": question, "facts": facts, "answer": answer}
    return {
        "answer": answer,
        "invented": invented(t),
        "copied": round(copied(t), 3),
        "we_rate": round(we_rate(t), 3),
        "leak": round(leak(t), 3),
        "length": len(words(answer)),
        "sentences": len([s for s in re.split(r"(?<=[.!?])\s+", answer) if s.strip()]),
    }


PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Lucy prompt bench</title>
<style>
:root{--bg:#0E0F10;--card:#17191B;--line:#2A2E32;--text:#E9EAE6;--mut:#8C9298;
--acc:#FF4F1F;--warm:#E8A33D;--good:#6FBF8B;--bad:#E4574C;
--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 -apple-system,
BlinkMacSystemFont,"Segoe UI",Helvetica,sans-serif}
header{padding:14px 20px;border-bottom:1px solid var(--line);display:flex;
gap:16px;align-items:center;flex-wrap:wrap}
h1{font-size:15px;margin:0;letter-spacing:.14em;text-transform:uppercase;color:var(--acc)}
select,input,textarea,button{background:var(--card);color:var(--text);
border:1px solid var(--line);border-radius:7px;padding:7px 9px;font-family:inherit;
font-size:13px}
textarea{width:100%;font-family:var(--mono);font-size:12px;line-height:1.5;resize:vertical}
button{cursor:pointer;background:var(--acc);border-color:var(--acc);color:#fff;
font-weight:600;padding:8px 16px}
button.ghost{background:var(--card);color:var(--text);border-color:var(--line);font-weight:400}
button:disabled{opacity:.5;cursor:default}
.wrap{display:grid;grid-template-columns:1fr 1fr;gap:14px;padding:16px 20px}
.col{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.col h2{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--mut);
margin:0 0 10px}
.facts{font-family:var(--mono);font-size:11.5px;color:var(--mut);white-space:pre-wrap;
max-height:190px;overflow:auto;border:1px solid var(--line);border-radius:7px;padding:9px}
.answer{font-size:15px;line-height:1.6;white-space:pre-wrap;min-height:70px;
border-left:2px solid var(--warm);padding-left:12px;margin:10px 0}
.metrics{display:flex;gap:14px;flex-wrap:wrap;font-family:var(--mono);font-size:11.5px;
color:var(--mut);border-top:1px solid var(--line);padding-top:10px}
.metrics b{color:var(--text);font-weight:600}
.bad{color:var(--bad)} .good{color:var(--good)}
.params{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:10px 0;
font-family:var(--mono);font-size:11.5px;color:var(--mut)}
.params input[type=number]{width:74px}
.full{grid-column:1/-1}
.batch{font-family:var(--mono);font-size:12px;white-space:pre;overflow:auto}
</style>

<header>
  <h1>Lucy prompt bench</h1>
  <select id=q style="min-width:340px"></select>
  <button id=run>Run both</button>
  <button id=batch class=ghost>Batch A over 20</button>
  <span id=status style="color:var(--mut);font-family:var(--mono);font-size:12px"></span>
</header>

<div class=wrap>
  <div class="col full">
    <h2>Facts she is given (recorded from the phone)</h2>
    <div class=facts id=facts></div>
  </div>

  <div class=col>
    <h2>A</h2>
    <select id=tplA></select>
    <select id=mdlA></select>
    <div class=params>
      temp <input type=number id=tA value=0.7 step=0.1 min=0 max=2>
      top_k <input type=number id=kA value=40 step=5 min=1 max=200>
      max <input type=number id=mA value=320 step=40 min=40 max=1024>
    </div>
    <textarea id=pA rows=14></textarea>
    <div class=answer id=aA></div>
    <div class=metrics id=sA></div>
  </div>

  <div class=col>
    <h2>B</h2>
    <select id=tplB></select>
    <select id=mdlB></select>
    <div class=params>
      temp <input type=number id=tB value=0.7 step=0.1 min=0 max=2>
      top_k <input type=number id=kB value=40 step=5 min=1 max=200>
      max <input type=number id=mB value=320 step=40 min=40 max=1024>
    </div>
    <textarea id=pB rows=14></textarea>
    <div class=answer id=aB></div>
    <div class=metrics id=sB></div>
  </div>

  <div class="col full" id=batchbox style="display:none">
    <h2>Batch — prompt A over the camp's real questions</h2>
    <div class=batch id=batchout></div>
  </div>
</div>

<script>
let QS = [], TPL = {}, MDL = [];

async function boot() {
  QS = await (await fetch('/api/questions')).json();
  TPL = await (await fetch('/api/templates')).json();
  MDL = await (await fetch('/api/models')).json();
  const q = document.getElementById('q');
  QS.forEach((r, i) => q.add(new Option(r.question, i)));
  for (const side of ['A','B']) {
    const sel = document.getElementById('tpl'+side);
    Object.keys(TPL).forEach(n => sel.add(new Option(n, n)));
    sel.value = side === 'A' ? 'full' : 'minimal';
    const m = document.getElementById('mdl'+side);
    MDL.forEach(x => m.add(new Option(`${x.name} · ${x.style}`, x.name)));
    // A defaults to the first model listed, B to the last: with both models
    // passed the two sides compare full against light out of the box.
    m.value = side === 'A' ? MDL[0].name : MDL[MDL.length-1].name;
    sel.onchange = () => document.getElementById('p'+side).value = TPL[sel.value];
    sel.onchange();
  }
  q.onchange = showFacts; showFacts();
}
function showFacts() {
  const r = QS[document.getElementById('q').value];
  document.getElementById('facts').textContent = r.facts || '(nothing)';
}
function metric(label, value, bad) {
  return `<span>${label} <b class="${bad ? 'bad' : ''}">${value}</b></span>`;
}
function render(side, s) {
  document.getElementById('a'+side).textContent = s.answer || '(nothing)';
  const inv = s.invented.length;
  document.getElementById('s'+side).innerHTML =
    metric('invented', inv ? s.invented.join(', ') : 'none', inv > 0) +
    metric('lifted', Math.round(s.copied*100)+'%', s.copied > 0.5) +
    metric('persona leak', Math.round(s.leak*100)+'%', s.leak > 0) +
    metric('words', s.length, false) +
    metric('sentences', s.sentences, false);
}
async function runSide(side) {
  const r = QS[document.getElementById('q').value];
  const body = {
    question: r.question, facts: r.facts,
    template: document.getElementById('p'+side).value,
    model: document.getElementById('mdl'+side).value,
    temperature: +document.getElementById('t'+side).value,
    top_k: +document.getElementById('k'+side).value,
    max_tokens: +document.getElementById('m'+side).value,
  };
  const res = await fetch('/api/generate', {method:'POST', body: JSON.stringify(body)});
  render(side, await res.json());
}
document.getElementById('run').onclick = async (e) => {
  const btn = e.target; btn.disabled = true;
  document.getElementById('status').textContent = 'generating…';
  document.getElementById('aA').textContent = '…';
  document.getElementById('aB').textContent = '…';
  // Serial on purpose: one llama context, one generation at a time.
  await runSide('A'); await runSide('B');
  document.getElementById('status').textContent = '';
  btn.disabled = false;
};
document.getElementById('batch').onclick = async (e) => {
  const btn = e.target; btn.disabled = true;
  document.getElementById('batchbox').style.display = '';
  document.getElementById('batchout').textContent = 'running 20 questions…';
  document.getElementById('status').textContent = 'batch running…';
  const body = {
    template: document.getElementById('pA').value,
    model: document.getElementById('mdlA').value,
    temperature: +document.getElementById('tA').value,
    top_k: +document.getElementById('kA').value,
    max_tokens: +document.getElementById('mA').value,
    limit: 20,
  };
  const res = await fetch('/api/batch', {method:'POST', body: JSON.stringify(body)});
  const d = await res.json();
  document.getElementById('batchout').textContent =
    `questions                     ${d.turns}\n` +
    `answers inventing a specific  ${Math.round(d.invented_any*100)}%\n` +
    `median share lifted verbatim  ${Math.round(d.copied_median*100)}%\n` +
    `answers over half lifted      ${Math.round(d.copied_over_half*100)}%\n` +
    `answers reciting the persona  ${Math.round(d.leak_any*100)}%\n` +
    `median length (words)         ${d.length_median}\n`;
  document.getElementById('status').textContent = '';
  btn.disabled = false;
};
boot();
</script>
"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj) -> None:
        self._send(200, json.dumps(obj).encode(), "application/json")

    def log_message(self, *a):  # the page is chatty; the console is for errors
        pass

    def do_GET(self):
        if self.path == "/":
            return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        if self.path == "/api/questions":
            return self._json([{"question": r["question"], "facts": r["facts"]}
                               for r in ROWS])
        if self.path == "/api/templates":
            return self._json(templates())
        if self.path == "/api/models":
            return self._json([{"name": n, "style": e["style"]}
                               for n, e in MODELS.items()])
        self._send(404, b"no", "text/plain")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")

        if self.path == "/api/generate":
            text = (body["template"]
                    .replace("{facts}", body.get("facts") or "(nothing found)")
                    .replace("{question}", body.get("question", "")))
            answer = generate(body.get("model", ""), text,
                              body.get("temperature", 0.7),
                              body.get("top_k", 40), body.get("max_tokens", 320))
            return self._json(scored(body.get("question", ""),
                                     body.get("facts", ""), answer))

        if self.path == "/api/batch":
            limit = int(body.get("limit", 20))
            inv, cop, lek, lens = [], [], [], []
            for r in ROWS[:limit]:
                text = (body["template"]
                        .replace("{facts}", r["facts"] or "(nothing found)")
                        .replace("{question}", r["question"]))
                a = generate(body.get("model", ""), text,
                             body.get("temperature", 0.7),
                             body.get("top_k", 40), body.get("max_tokens", 320))
                s = scored(r["question"], r["facts"], a)
                inv.append(len(s["invented"])); cop.append(s["copied"])
                lek.append(s["leak"]); lens.append(s["length"])
            import statistics as st
            return self._json({
                "turns": len(inv),
                "invented_any": sum(1 for x in inv if x) / max(len(inv), 1),
                "copied_median": st.median(cop) if cop else 0,
                "copied_over_half": sum(1 for x in cop if x > .5) / max(len(cop), 1),
                "leak_any": sum(1 for x in lek if x) / max(len(lek), 1),
                "length_median": st.median(lens) if lens else 0,
            })
        self._send(404, b"no", "text/plain")


def main() -> int:
    global ROWS
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("journal")
    ap.add_argument("model", nargs="+",
                    help="one or more .gguf paths; each becomes a choice on "
                         "both sides of the page")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    rows = turns(Path(args.journal).read_text(encoding="utf-8", errors="replace"))
    seen = set()
    for t in rows:
        k = t["question"].lower().strip()
        if k not in seen:
            seen.add(k)
            ROWS.append(t)
    print(f"{len(ROWS)} distinct questions with their recorded fact sheets")

    for path in args.model:
        name = Path(path).stem
        print(f"loading {name}…")
        llm = Llama(model_path=path, n_ctx=4096, n_batch=4096,
                    n_gpu_layers=-1, verbose=False)
        style = turn_style(llm)
        MODELS[name] = {"llm": llm, "style": style}
        # Printed, not asserted. Both marker styles are legal now; what must
        # never happen is using one without knowing which.
        print(f"  turn markers: {style}")
    print(f"\n  http://127.0.0.1:{args.port}\n")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
