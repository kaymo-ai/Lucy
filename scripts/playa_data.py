#!/usr/bin/env python3
"""Turn the three saved Playa webapps into three JSON files the app ships.

`Playa data/` holds three pages saved out of a browser: the Better Playa
Guide (events), and playamap.org twice (camps, art). None of them is a data
feed -- two are rendered DOM with the app's own JavaScript missing, and the
third carries its payload in a `window.__GUIDE__` assignment. So this reads
what each page actually preserved, which is not the same thing in each case:

  events  full records, from the guide's data.js
  camps   name and address only, scraped out of the rendered table
  art     name and artist only. The saved page shows "Location not released
          yet", and every description is fetched on tap, so 324 of 326 are
          simply absent. That is the honest shape of the data, and the Info
          row says so rather than the screen implying a fetch failed.

Deliberately dropped from the events: `sm` (a summary the guide's author
generated with a model), `gr`, `f`/`fv` and `k` (its search and ranking
scaffolding). What ships is what Burning Man's own listing said. Putting
model-written text on a screen in THIS app, where invented text is a bug
rather than a blemish, is not a trade worth making for a nicer sentence.

Run:  python3 scripts/playa_data.py
Writes Lucy/playa-events-2026.json, -camps-, -art-, and playa-counts.json.
"""

import datetime
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "Playa data"
OUT = ROOT / "Lucy"

GUIDE = SRC / "Better Playa Guide_ Gift from Muse Cafe_files" / "data.js"
MAP = SRC / "PlayaMap.html"
ART = SRC / "PlayaMap-art.html"
# The art, from playamap's own API rather than from the saved DOM. The page
# bulk-fetches descriptions AFTER it loads (`loadArtDescriptions()`, in the
# page's inline script), so the save preserved names and artists and nothing
# else. These two are `curl https://playamap.org/api/art/` and `...?desc=1`,
# taken 2026-08-26. Optional: without them the DOM scrape still works, minus
# the descriptions.
ART_API_LIST = SRC / "api-art-list.json"
ART_API_DESC = SRC / "api-art-desc.json"

YEAR = 2026


# --------------------------------------------------------------------------
# Shared


def clean(s):
    """Unescape and squeeze. The map pages are saved DOM, so an address
    arrives as `9:15 &amp; G` and would render with the entity showing."""
    if not s:
        return ""
    s = html.unescape(str(s))
    return re.sub(r"\s+", " ", s).strip()


def undouble(text):
    """The guide's descriptions are very often the same text twice over --
    3,382 of 4,224 of them. It is a fault in the source, not in the reading.

    Only an EXACT doubling is cut. A near-doubling falls through and keeps
    everything: an ugly repeated sentence costs a reader a moment, and a
    wrongly trimmed one loses what the camp actually said."""
    t = text.strip()
    if len(t) < 20:
        return t
    for split in (len(t) // 2, (len(t) + 1) // 2):
        head, tail = t[:split].strip(), t[split:].strip()
        if head and head == tail:
            return head
    return t


# --------------------------------------------------------------------------
# Events


def read_guide():
    raw = GUIDE.read_text(encoding="utf-8", errors="replace")
    start = raw.index("{")
    end = raw.rindex("}") + 1
    return json.loads(raw[start:end])


def slots_of(item):
    """The times one event runs, cleaned up.

    The guide's slot list is repetitive in two distinct ways, and both of them
    reached the screen before they were noticed here -- "Dusi Bubbly Rose"
    drew four identical All-day rows under one Sunday.

    1. 181 slots are exact duplicates of another slot on the same event.
    2. A slot can carry a date and no clock time. 194 of those are the only
       thing that event does that day, and are genuinely all-day. The other
       15 sit alongside a real time on the same date, where they are not a
       second listing but the same one written twice, once without its hour.

    Returns `(date, at, until)` triples, `at` empty for an all-day.
    """
    seen = set()
    by_date = {}
    for slot in item.get("s") or []:
        # A null slot. 43 of them, across 31 events -- Robot Heart's "DJ sets"
        # is one. Not dropped: the caller files them under "No date given".
        if not slot or not slot[0]:
            continue
        stamp = str(slot[0])
        date, _, at = stamp.partition(" ")
        if not re.fullmatch(r"\d{2}-\d{2}", date):
            continue
        until = clean(slot[1]) if len(slot) > 1 else ""
        key = (date, at, until)
        if key in seen:
            continue
        seen.add(key)
        by_date.setdefault(date, []).append(key)

    out = []
    for date in by_date:
        entries = by_date[date]
        timed = [e for e in entries if e[1]]
        out.extend(timed if timed else entries)
    return out


def build_events():
    guide = read_guide()
    raw = guide["ev"]["e"]

    events = []
    # date -> list of {"e": index, "at": "21:00", "to": "01:00"}
    by_date = {}
    # Events the guide gives no date for at all. They get a section of their
    # own at the end rather than being dropped, because "when is Robot Heart"
    # is a question somebody will ask this screen and the honest answer is
    # that the listing does not say.
    untimed = []

    for item in raw:
        about = undouble(clean(item.get("d")))
        record = {
            "title": clean(item.get("t")) or "Untitled",
            "camp": clean(item.get("c")),
            "address": clean(item.get("a")),
            "tags": [clean(g) for g in (item.get("g") or []) if clean(g)],
        }
        who = clean(item.get("p"))
        if who:
            record["who"] = who
        if about:
            record["about"] = about
        index = len(events)
        events.append(record)

        placed = False
        for slot in slots_of(item):
            date, at, until = slot
            # Keyed on the START date. Slots wrap past midnight -- a set that
            # begins 21:00 and ends 01:00 belongs to the night it started,
            # not to the morning it finished.
            entry = {"e": index}
            if at:
                entry["at"] = at
            if until and at:
                entry["to"] = until
            by_date.setdefault(date, []).append(entry)
            placed = True

        if not placed:
            untimed.append(index)

    days = []
    for date in sorted(by_date):
        month, day = (int(p) for p in date.split("-"))
        when = datetime.date(YEAR, month, day)
        days.append({
            # Weekday first, so this reads the same way as the camp's own
            # schedule, which heads its days "Sunday", "Monday". The date is
            # there because the week runs over two Sundays and the bare name
            # would be ambiguous.
            "name": when.strftime("%A %-d %B"),
            "date": date,
            # No time sorts first, which is where an all-day thing belongs.
            "slots": sorted(by_date[date], key=lambda s: s.get("at", "")),
        })

    if untimed:
        days.append({
            "name": "No date given",
            "date": "",
            "slots": [{"e": i} for i in sorted(untimed)],
        })

    occurrences = sum(len(d["slots"]) for d in days)
    dated = sum(1 for x in raw for s in (x.get("s") or []) if s and s[0])
    print(f"events: {len(events)} events, {occurrences} occurrences, "
          f"{len(days)} days ({len(untimed)} events carry no date at all)")
    print(f"        {dated} dated slots in the source, "
          f"{dated - occurrences} of them repeats")
    doubled = sum(1 for i, r in zip(raw, events)
                  if clean(i.get("d")) != r.get("about", ""))
    print(f"        {doubled} descriptions were the same text twice")
    return {"events": events, "days": days}, {
        "events": len(events), "occurrences": occurrences, "days": len(days)}


# --------------------------------------------------------------------------
# Camps and art
#
# Both pages are saved DOM. The app's own playamap.js was not saved, so the
# only data that survives is what had already been rendered into the table.

CAMP_ROW = re.compile(
    r'<tr id="\d+" onclick="goCamp\(\d+\)">'
    r'<td class="campn">(.*?)</td><td>(.*?)</td></tr>')

ART_ROW = re.compile(
    r'<div class="artn">(.*?)</div>'
    r'<div class="artsub"><span class="artr">(.*?)</span>')

# `7:45 & B`, `3:00 & Esp`, and `4:30 & G@ 2:15` -- the last is not damage
# but playamap's notation for a camp on a plaza frontage, at the 2:15
# position round it. 86 camps are written that way.
ADDRESS = re.compile(
    r"^\d{1,2}:\d{2} & (?:[A-K]|Esp(?:lanade)?)(?:@ ?\d{1,2}:\d{2})?$",
    re.IGNORECASE)


def read_page(path):
    # Saved as windows-1252; reading it as UTF-8 turns the file binary and
    # every grep and regex silently finds nothing.
    return path.read_bytes().decode("windows-1252", errors="replace")


def build_camps():
    page = read_page(MAP)
    seen, camps, odd = set(), [], []
    for name, address in CAMP_ROW.findall(page):
        name, address = clean(name), clean(address)
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        camps.append({"name": name, "address": address})
        if address and not ADDRESS.fullmatch(address):
            odd.append(f"{name} @ {address}")
    camps.sort(key=lambda c: c["name"].lower())
    print(f"camps:  {len(camps)} camps, {len(odd)} addresses not of the "
          f"form `7:45 & B`")
    # Shipped verbatim. Some of these are real (Epicenter, Center Camp
    # Plaza) and some are damage in the source (`10:10:00 & 00 B`). Guessing
    # which is which and rewriting it would be inventing an address, so the
    # count gets printed and a human can question it.
    for line in odd[:8]:
        print(f"        {line}")
    if len(odd) > 8:
        print(f"        ... and {len(odd) - 8} more")
    return {"camps": camps}, {"camps": len(camps)}


def build_art():
    if ART_API_LIST.exists() and ART_API_DESC.exists():
        return build_art_from_api()
    page = read_page(ART)
    seen, art = set(), []
    for name, artist in ART_ROW.findall(page):
        name, artist = clean(name), clean(artist)
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        art.append({"name": name, "artist": artist})
    art.sort(key=lambda a: a["name"].lower())
    print(f"art:    {len(art)} pieces from the saved DOM, name and artist "
          f"only -- fetch the api-art-* files for the descriptions")
    return {"art": art}, {"art": len(art)}


def build_art_from_api():
    """The API list truncates its own `d` field with a literal `...` -- 297
    of 327 are shorter there than in the desc payload -- so the description
    always comes from `?desc=1`, with the truncated one as the fallback."""
    listing = json.loads(ART_API_LIST.read_text(encoding="utf-8"))
    desc = json.loads(ART_API_DESC.read_text(encoding="utf-8"))
    seen, art, described = set(), [], 0
    for item in listing.get("art") or []:
        name = clean(item.get("n"))
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        entry = {"name": name, "artist": clean(item.get("r"))}
        about = clean(desc.get(item.get("u")) or item.get("d"))
        if about:
            entry["about"] = about
            described += 1
        art.append(entry)
    art.sort(key=lambda a: a["name"].lower())
    print(f"art:    {len(art)} pieces from the API, {described} with a "
          f"description (locations are still unreleased until the Sunday)")
    return {"art": art}, {"art": len(art)}


# --------------------------------------------------------------------------


def write(name, payload):
    path = OUT / name
    # Compact: this is shipped in an app bundle and read by a decoder, never
    # by a person. Pretty-printing the events costs ~400 KB of indentation.
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    path.write_text(text, encoding="utf-8")
    print(f"wrote   {path.relative_to(ROOT)}  {len(text.encode()) / 1024:.0f} KB")


def main():
    missing = [p for p in (GUIDE, MAP, ART) if not p.exists()]
    if missing:
        for p in missing:
            print(f"missing: {p}", file=sys.stderr)
        return 1

    events, n_events = build_events()
    camps, n_camps = build_camps()
    art, n_art = build_art()

    write("playa-events-2026.json", events)
    write("playa-camps-2026.json", camps)
    write("playa-art-2026.json", art)
    # The captions on the Info tab come from here rather than from the files
    # themselves. Counting 4,224 events means decoding 1.4 MB, and the Info
    # tab is drawn on every launch. Generated alongside the data it counts,
    # so it still cannot drift the way a hardcoded "7 days" would.
    write("playa-counts.json", {
        "year": YEAR,
        "events": n_events,
        "camps": n_camps,
        "art": n_art,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
