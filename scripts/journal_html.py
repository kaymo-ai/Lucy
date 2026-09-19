"""Turn Lucy's device journal into one self-contained local HTML page.

The journal is the only honest record of what the app actually did on the
phone: which model was loaded, what rows retrieval handed the model, what it
said back, and what the settings were at that moment. Read as text it is
5,700 lines and nobody reads it. This lays it out per turn and scores each
answer the same way `scripts/score_answers.py` does, so a turn that invented
a camp specific is visible without reading the facts block underneath it.

Local file on purpose. It carries campmates' names and what the camp says
about them, so it does not get published anywhere.

Usage:  journal_html.py <journal.txt> <out.html>
"""
import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from score_answers import invented, words  # noqa: E402

STAMP = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T[\d:]+Z)\]\s*(.*)$")
MODEL_LINE = re.compile(r"MODEL loaded: (\S+) \((\d+) MB, (\S+) turns\)")


def parse(text):
    """Journal -> (turns, model_events).

    A turn is QUESTION, optional FLAGS, FACTS GIVEN block, ANSWERED block,
    terminated by a `----` rule. Blocks run to the next stamped line, so the
    parser tracks which block it is inside rather than assuming line counts.
    """
    turns, models = [], []
    cur, mode = None, None
    current_model = None

    def close():
        nonlocal cur, mode
        if cur:
            cur["facts"] = "\n".join(cur["facts"]).strip()
            cur["answer"] = "\n".join(cur["answer"]).strip()
            turns.append(cur)
        cur, mode = None, None

    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if line.strip() == "----":
            close()
            continue

        m = STAMP.match(line)
        if not m:
            # Continuation of whichever block is open.
            if cur and mode in ("facts", "answer"):
                cur[mode].append(line)
            continue

        ts, rest = m.group(1), m.group(2)

        mm = MODEL_LINE.search(rest)
        if mm:
            current_model = mm.group(1)
            models.append({"ts": ts, "file": mm.group(1),
                           "mb": int(mm.group(2)), "style": mm.group(3)})
            continue

        if rest.startswith("QUESTION:"):
            close()
            cur = {"ts": ts, "question": rest[len("QUESTION:"):].strip(),
                   "flags": "", "facts": [], "answer": [],
                   "model": current_model}
            mode = None
        elif rest.startswith("FLAGS:") and cur:
            cur["flags"] = rest[len("FLAGS:"):].strip()
        elif rest.startswith("FACTS GIVEN:") and cur:
            mode = "facts"
        elif rest.startswith("ANSWERED:") and cur:
            mode = "answer"
        else:
            # SPEECH / ENCODER / EMBEDDER and anything else ends an open block.
            mode = None
    close()
    return turns, models


def score(turns):
    for t in turns:
        t["words"] = len(words(t["answer"]))
        t["invented"] = invented(t) if t["answer"] else []
        t["truncated"] = bool(t["answer"]) and not re.search(
            r"[.!?\"')\]]\s*$", t["answer"])
    return turns


def summarise(turns):
    by = {}
    for t in turns:
        key = t["model"] or "unknown"
        b = by.setdefault(key, {"turns": 0, "answered": 0, "invented": 0,
                                "words": [], "truncated": 0})
        b["turns"] += 1
        if t["answer"]:
            b["answered"] += 1
            b["words"].append(t["words"])
            if t["invented"]:
                b["invented"] += 1
            if t["truncated"]:
                b["truncated"] += 1
    for b in by.values():
        w = sorted(b["words"])
        b["median"] = w[len(w) // 2] if w else 0
        b["max"] = max(w) if w else 0
        b["pct_invented"] = (100 * b["invented"] / b["answered"]
                             if b["answered"] else 0)
    return by


def mark(answer, terms):
    """Escape, then wrap each invented token so it is visible in place."""
    out = html.escape(answer)
    for term in sorted(set(terms), key=len, reverse=True):
        out = re.sub(rf"(?<![\w>]){re.escape(html.escape(term))}(?![\w<])",
                     lambda m: f'<mark>{m.group(0)}</mark>', out)
    return out


def render(turns, models, by):
    label = {}
    for k in by:
        label[k] = ("lucy4 (tuned)" if "lucy-tuned" in k
                    else "stock gemma-4" if "gemma-4" in k
                    else "gemma-3 light" if "gemma-3" in k
                    else "not recorded" if k == "unknown" else k)

    cards = []
    for i, t in enumerate(turns):
        key = t["model"] or "unknown"
        # A turn whose model was never logged must not be classed as stock:
        # it would style and filter as if we knew what answered it.
        cls = ("tuned" if "lucy-tuned" in key
               else "light" if "gemma-3" in key
               else "unrec" if key == "unknown" else "stock")
        badges = []
        if t["invented"]:
            badges.append(f'<span class="b bad">{len(t["invented"])} invented</span>')
        if t["truncated"]:
            badges.append('<span class="b warn">cut off</span>')
        if not t["answer"]:
            badges.append('<span class="b warn">no answer</span>')
        badges.append(f'<span class="b">{t["words"]}w</span>')

        flags = ""
        if t["flags"]:
            flags = f'<div class="flags">{html.escape(t["flags"])}</div>'

        facts = ""
        if t["facts"]:
            n = len([l for l in t["facts"].splitlines() if l.strip().startswith("-")])
            facts = (f'<details><summary>Facts given &middot; {n} rows</summary>'
                     f'<pre>{html.escape(t["facts"])}</pre></details>')

        answer = (f'<div class="a">{mark(t["answer"], t["invented"])}</div>'
                  if t["answer"] else '<div class="a none">No answer recorded.</div>')

        inv = ""
        if t["invented"]:
            chips = " ".join(f'<code>{html.escape(x)}</code>' for x in t["invented"])
            inv = f'<div class="inv">Not in the facts: {chips}</div>'

        cards.append(f'''
<article class="turn {cls}" data-model="{html.escape(key)}"
         data-invented="{1 if t['invented'] else 0}">
  <header>
    <span class="ts">{t["ts"]}</span>
    <span class="model {cls}">{html.escape(label.get(key, key))}</span>
    <span class="badges">{"".join(badges)}</span>
  </header>
  <h3>{html.escape(t["question"]) or "<em>(empty)</em>"}</h3>
  {answer}
  {inv}
  {facts}
  {flags}
</article>''')

    rows = "".join(
        f'<tr><td>{html.escape(label.get(k, k))}</td><td>{b["turns"]}</td>'
        f'<td>{b["median"]}</td><td>{b["max"]}</td>'
        f'<td class="{"bad" if b["pct_invented"] > 20 else ""}">'
        f'{b["pct_invented"]:.0f}%</td><td>{b["truncated"]}</td></tr>'
        for k, b in sorted(by.items(), key=lambda kv: -kv[1]["turns"]))

    # Only the CHANGES. The app logs a load on every cold start, so the raw
    # list is 88 rows of mostly the same file, which buries the handful of
    # moments the model actually changed.
    runs = []
    for m in models:
        if runs and runs[-1]["file"] == m["file"]:
            runs[-1]["n"] += 1
            runs[-1]["last"] = m["ts"]
            continue
        runs.append({**m, "n": 1, "last": m["ts"]})

    def mcls(f):
        return ("tuned" if "lucy-tuned" in f
                else "light" if "gemma-3" in f else "stock")

    switches = "".join(
        f'<li><span class="ts">{r["ts"]}</span> '
        f'<span class="model {mcls(r["file"])}">{html.escape(r["file"])}</span> '
        f'<span class="dim">{r["mb"]} MB &middot; {r["style"]} turns'
        + (f' &middot; reloaded {r["n"]}&times; through {r["last"]}'
           if r["n"] > 1 else '')
        + '</span></li>'
        for r in runs)

    span = f'{turns[0]["ts"]} to {turns[-1]["ts"]}' if turns else "no turns"

    return f'''<meta charset="utf-8">
<title>Lucy Journal</title>
<style>
:root {{
  --bg:#fbfaf8; --fg:#1a1a19; --dim:#6b6a66; --line:#e2e0db; --card:#fff;
  --tuned:#c2410c; --stock:#0f766e; --light:#7c3aed; --bad:#b91c1c;
  --mark:#fde68a;
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme=light]) {{
    --bg:#16161a; --fg:#ececec; --dim:#9a9a95; --line:#2d2d33; --card:#1d1d22;
    --tuned:#fb923c; --stock:#2dd4bf; --light:#a78bfa; --bad:#f87171;
    --mark:#78350f;
  }}
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg);
  font:15px/1.55 ui-sans-serif,-apple-system,"Segoe UI",sans-serif; }}
.wrap {{ max-width:900px; margin:0 auto; padding:32px 20px 80px; }}
h1 {{ font-size:30px; margin:0 0 4px; letter-spacing:-.02em; }}
.sub {{ color:var(--dim); margin:0 0 28px; }}
h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.09em;
  color:var(--dim); margin:34px 0 12px; font-weight:600; }}
table {{ width:100%; border-collapse:collapse; font-size:14px; }}
th,td {{ text-align:left; padding:7px 10px; border-bottom:1px solid var(--line); }}
th {{ color:var(--dim); font-weight:600; font-size:12px;
  text-transform:uppercase; letter-spacing:.05em; }}
td.bad, .bad {{ color:var(--bad); font-weight:600; }}
ul.sw {{ list-style:none; padding:0; margin:0; font-size:13px; }}
ul.sw li {{ padding:5px 0; border-bottom:1px solid var(--line); }}
.ts {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:12px; color:var(--dim); }}
.dim {{ color:var(--dim); }}
.model {{ font-size:12px; font-weight:600; }}
.model.tuned {{ color:var(--tuned); }}
.model.stock {{ color:var(--stock); }}
.model.light {{ color:var(--light); }}
.controls {{ display:flex; gap:8px; flex-wrap:wrap; margin:22px 0 6px; }}
button {{ font:inherit; font-size:13px; padding:6px 13px; cursor:pointer;
  border:1px solid var(--line); background:var(--card); color:var(--fg);
  border-radius:99px; }}
button[aria-pressed=true] {{ background:var(--fg); color:var(--bg);
  border-color:var(--fg); }}
.turn {{ background:var(--card); border:1px solid var(--line);
  border-left:3px solid var(--line); border-radius:9px;
  padding:14px 16px; margin:12px 0; }}
.turn.tuned {{ border-left-color:var(--tuned); }}
.turn.stock {{ border-left-color:var(--stock); }}
.turn.light {{ border-left-color:var(--light); }}
.turn.unrec {{ border-left-color:var(--dim); }}
.model.unrec {{ color:var(--dim); }}
.turn header {{ display:flex; gap:10px; align-items:baseline;
  flex-wrap:wrap; margin-bottom:7px; }}
.badges {{ margin-left:auto; display:flex; gap:5px; }}
.b {{ font-size:11px; padding:2px 7px; border-radius:99px;
  background:var(--line); color:var(--dim); white-space:nowrap; }}
.b.bad {{ background:var(--bad); color:#fff; }}
.b.warn {{ background:var(--mark); color:var(--fg); }}
.turn h3 {{ font-size:16px; margin:0 0 8px; font-weight:650; }}
.a {{ white-space:pre-wrap; }}
.a.none {{ color:var(--dim); font-style:italic; }}
mark {{ background:var(--mark); color:inherit; padding:0 2px;
  border-radius:3px; }}
.inv {{ margin-top:9px; font-size:12.5px; color:var(--dim); }}
.inv code {{ font-size:12px; background:var(--line); padding:1px 5px;
  border-radius:4px; margin-right:3px; }}
details {{ margin-top:10px; }}
summary {{ cursor:pointer; font-size:12.5px; color:var(--dim); }}
pre {{ white-space:pre-wrap; font-size:12px; line-height:1.5;
  background:var(--bg); border:1px solid var(--line); border-radius:7px;
  padding:11px; overflow-x:auto; max-height:420px; margin:9px 0 0; }}
.flags {{ margin-top:8px; font-family:ui-monospace,Menlo,monospace;
  font-size:11px; color:var(--dim); }}
.tablewrap {{ overflow-x:auto; }}
</style>
<div class="wrap">
<h1>Lucy journal</h1>
<p class="sub">{len(turns)} turns &middot; {span} &middot; from the dev app on the phone</p>

<h2>What the models did</h2>
<div class="tablewrap">
<table>
<tr><th>Model</th><th>Turns</th><th>Median words</th><th>Longest</th>
<th>Answers inventing a specific</th><th>Cut off</th></tr>
{rows}
</table>
</div>
<p class="sub" style="font-size:13px;margin-top:10px">
Read the small rows carefully: lucy4 answered only 5 turns here, so its
percentages are a handful of answers, not a measurement. The separate 59-question
replay put it at 286 median words and 63% inventing, which these 5 match.
"Not recorded" is the oldest stretch of the journal, before the app logged which
model it had loaded &mdash; those turns predate the turn-marker fix, so the model
was being handed malformed prompts.
</p>
<p class="sub" style="font-size:13px;margin-top:10px">
"Inventing a specific" is <code>score_answers.invented()</code>: a name, number
or place in the answer that appears in neither the facts retrieval supplied nor
the question. That is rule 1 of the invariant, checked. Highlighted in the
answers below.</p>

<h2>Model switches</h2>
<ul class="sw">{switches}</ul>

<h2>Every turn</h2>
<div class="controls">
  <button data-f="all" aria-pressed="true">All</button>
  <button data-f="tuned" aria-pressed="false">lucy4 only</button>
  <button data-f="stock" aria-pressed="false">Stock only</button>
  <button data-f="invented" aria-pressed="false">Invented something</button>
</div>
<div id="turns">{"".join(cards)}</div>
</div>
<script>
const btns = document.querySelectorAll('.controls button');
btns.forEach(b => b.addEventListener('click', () => {{
  btns.forEach(o => o.setAttribute('aria-pressed', o === b));
  const f = b.dataset.f;
  document.querySelectorAll('.turn').forEach(t => {{
    let show = true;
    if (f === 'tuned') show = t.classList.contains('tuned');
    else if (f === 'stock') show = t.classList.contains('stock');
    else if (f === 'invented') show = t.dataset.invented === '1';
    t.style.display = show ? '' : 'none';
  }});
}}));
</script>'''


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    turns, models = parse(src.read_text(encoding="utf-8", errors="replace"))
    turns = score(turns)
    by = summarise(turns)
    out.write_text(render(turns, models, by), encoding="utf-8")

    print(f"{len(turns)} turns, {len(models)} model loads -> {out}")
    for k, b in sorted(by.items(), key=lambda kv: -kv[1]["turns"]):
        print(f"  {k:32} {b['turns']:>4} turns  "
              f"median {b['median']:>3}w  "
              f"invented {b['pct_invented']:.0f}%  "
              f"cut off {b['truncated']}")


if __name__ == "__main__":
    main()
