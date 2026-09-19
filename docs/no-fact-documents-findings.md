# The device database was shipping personal data — and why 492 documents produced no facts

Run against `scripts/output/ps_knowledge.db` and `scripts/output/enriched_preview.db`
at commit `bf98151`. Every number below came from querying those files.

The second question is the one that was asked. The first is what tracing it
turned up, and it is the one that mattered, so it goes first.

---

## 1. The device database shipped home addresses and card digits

`build_preview_db.py` shipped all 616 `camp_knowledge` rows verbatim, and
`Retrieval.answer` falls back to searching those raw documents whenever
`camp_fact` returns nothing:

```swift
docs: camp.isEmpty ? store.searchDocs(terms: t) : []
```

In the database as built at `bf98151`:

- **59 documents** contained a street-address pattern
- **42 documents** contained a phone-number pattern
- **57 documents** contained Amazon order numbers, shipping addresses or access codes

This was reachable. Simulating the real path — `searchCampFacts` →
`searchDocs` → `passage()` — faithfully against the shipped database, the
question **"what's our shipping address"** returned no camp facts, fell
through to documents, and rendered:

> …Sold by: The Official BIC Store … Shipping Address: **[a camp member's
> name, their full street address, city, state and ZIP]**, United States …
> Payment Method: Item(s) Subtotal: $13.98 **Visa | Last digits: [four
> digits]** …

A camp member's home address and the last four of their card, on an unlocked
phone at a festival.

(The real values are deliberately not reproduced here. The first version of
this document quoted them verbatim — in a document about removing them —
and was committed that way before the mistake was caught.)

### Why deleting the receipts was the wrong fix

My first recommendation was to exclude receipt documents from the device
build, on the grounds that `EntityStore.authority()` already scores them −10
so nothing would be lost. **That reasoning was wrong**, and the evidence
against it was in my own output: document 109 scored **33.2 with the −10
already applied**, because the frequency term
(`min(occ,40)*0.6 + min(per1k,3)`, up to 27) swamps the penalty. Receipts win
queries today.

Worse, they are the only record of what the camp owns. `Med kit supplies` is
the **only** document in the corpus containing the word "tourniquet". Modelling
the `passage() == nil → continue` filter that the Swift code applies (and that
my first simulation omitted), it is the *only* candidate that survives for
"what tourniquet do we have" — not the third hit, the sole one. Delete the
receipts and a medical question stops having an answer, along with
`ShadePoleConnectors`, `Stickers`, `LagBoltSockets_RyobiBatteries` and
`Doris Inventory 2022_Hardware`.

### And a blanket address pattern was the wrong fix too

The first redaction matched street addresses anywhere in the text. It removed
the personal data, and it also ate:

- `1056 Greg St` — the camp's storage unit
- `470 S Rock Blvd`, `8 Glendale Ave`, `5275 W. 4th Street` — suppliers, and
  Empire General
- `Reno, NV 89523`, `Sparks, NV 89431` — 13 redactions inside the camp manual
  alone, and 2 more in the Reno storage inventory
- `1\nHeat a skillet to medium-high heat. Place` — four recipe documents,
  because "Place" is a street suffix

That is exactly the inventory loss the previous section argues against, caused
by the fix for it. Caught only by listing the 98 touched documents and reading
the ~36 that were not receipts.

### What actually shipped: label-scoped redaction

What separates a personal address from the camp's is not the address, it is
where it sits. Personal addresses appear under a label on a receipt; the
camp's appear in prose. So `redact()` in `build_preview_db.py` removes
addresses **only inside a 200-character window opened by an explicit label**
(`Shipping Address`, `Billing Address`, `Ship To`, `Mailing Address`) or
closed by a trailing one (`Shipping Speed`, `Payment information` — needed
because the PDF extraction drops the label at page breaks but never the line
after the block). Card last-four, order numbers, bare card numbers and bank
routing/account numbers are removed everywhere, since none of them answers
anything.

Applied to `camp_knowledge` (title and content) and to `person_content`. The
corpus itself is left intact for enrichment.

Verified on the rebuilt database — every count is a query, not an assumption:

| | before | after |
|---|---:|---:|
| the home address (6 occurrences) | 6 | **0** |
| its city/state/ZIP | — | **0** |
| `Last digits: <n>` | 57 | **0** |
| order numbers | 57 | **0** |
| bank routing number in chat | 1 | **0** |
| bank account number in chat | 1 | **0** |
| **manual** `1056 Greg St` | kept | **kept** |
| **manual** `470 S Rock Blvd`, `Reno, NV 89523` | kept | **kept** |
| **recipe** `Heat a skillet` | kept | **kept** |
| **med kit** `tourniquet` | kept | **kept** |

Covered by 16 tests in `scripts/tests/test_build_preview_db.py`. Suite: 125
passing (7 pre-existing errors in `test_swift_integration.py` from a missing
`conn` fixture, unrelated).

### The other shipped table had worse in it

`person_content` ships too (1,374 rows) and was not covered by the first pass.
Checking it turned up the camp's **bank routing number and account number**,
pasted into the group chat once. On a lost phone that is worse than any of the
receipts. Now redacted — verified positively, not by absence:

```
…Business Address: 819 51st St, Oakland, CA - 94608
Routing Number: [removed]
Account Number: [removed]
```

`person.phone` is empty in the shipped build (0 of 127 rows), so nothing to do
there.

**What is deliberately still in `person_content`:** home addresses shared in
prose — five of them, in the shape "Address is [number] [street]" or
"[number] [street], [town]", posted by members inviting the camp to a party or
a build day. The label-scoped rule does not touch them by design: these are
party and build-day invitations a member posted to the camp, they carry no
label, and removing them would need the blanket pattern that ate the manual.
The "0" counts above are for `camp_knowledge`; this table is *reduced*, not
clean. Worth a decision rather than an assumption — the addresses belong to
people who shared them with the camp, not with a phone that goes to playa.

---

## 2. Why 492 of 616 documents produced no facts

The enrichment has **no readability gate**. All 616 documents were chunked
(942 chunk jobs) and sent to the model. So the documents that yielded nothing
were *seen and rejected by the model*, not filtered out beforehand.

124 documents produced facts; 492 did not.

> The enrichment agent's own report says 112 / 504. The live database says
> 124 / 492, and that is the current number — the report counted before the
> concurrent-run deduplication (1276 → 1243 facts) settled.

| Cause | Docs | Chars | Correct rejection? |
|---|---:|---:|---|
| JSON exports (spreadsheets → JSON) | 367 | 437,989 | **No — real loss** |
| Receipts / invoices | 78 | 312,771 | Yes, prompt says skip prices |
| Other prose | 36 | 421,731 | Mixed |
| PDF-garbled (OCR junk) | 3 | 137,018 | **No — real loss** |
| Thin (<400 chars) | 8 | 1,480 | Yes |

Median length: no-fact docs 720 chars, fact-producing docs 4,208 chars.

### The two real losses

**JSON exports — 367 documents, 75% of the misses.** The prompt in
`scripts/prompts/document_facts.md` was written for prose. Handed a JSON blob
the model returns nothing. What is being lost is not junk:

- `id=418 Storage` — facility name, address, unit #, access code, lock combo
- `id=223 PS - FOOD - … Food Purchase Sheet_Ingredients` — and ~8 more,
  including variants scaled to 50 / 55 / 60 people
- `id=414 PS 2016_Yurtshare`, `id=325 PS 2017_Yurtshare`

**PDF-garbled — 3 documents, 137 KB.** `id=2` is the Honda EU2000i generator
owner's manual. The extraction is full of doubled OCR characters
(`0000XX3311--ZZ0077--66330000`) and empty markdown tables, so the model
correctly refused it. The *content* is exactly what someone needs at 3am; the
*extraction* is unusable.

### Recommended next steps, in order of value

1. **Re-extract `id=2`, the generator manual**, from the source PDF, then
   re-run enrichment on that single document.
2. **A JSON-aware enrichment pass** for the 367 structured exports. These are
   tables; flattening each row to a sentence before the model sees it would
   likely be cheaper and more reliable than teaching the prose prompt to read
   JSON.

---

## Still open

`camp_fact` has no embeddings, so "how do we get water delivered" cannot reach
"pick up service vouchers at the USS Camp" — the two share no words. That is
the E7 embedding pass, and it remains the right fix rather than more keyword
scoring.
