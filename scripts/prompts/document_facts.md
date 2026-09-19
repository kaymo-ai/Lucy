You are reading one chunk of a document from a Burning Man camp's archive —
manuals, inventories, shift plans, purchase orders, insurance paperwork,
build notes. Extract the facts a person standing in the desert, phone in
hand, no signal, could actually act on.

VOICE: first person plural. Write "we order water fills from the supplier",
never "the camp orders water fills". Quoted evidence stays verbatim — never
rephrased, never assembled from two places in the text.

WHAT COUNTS AS A FACT

- **Only what the document states.** No inference, no filling in what seems
  obviously implied. If the text doesn't say it, it isn't a fact, no matter
  how likely it seems.
- **A fact must be useful standing up.** "Gray water goes in the 4x4x4
  holding tank; United Site Services removes it at end of week" is a fact.
  "We value sustainability" is not — nobody can act on it. If you can't
  picture someone using the fact in the next hour, leave it out.
- Prefer facts about how something works, where something is, who is
  responsible, when something happens, or what the rule is.

WHAT TO SKIP

- **Boilerplate.** Shipping addresses, invoice line items, page numbers,
  headers/footers, form labels, and PDF-extraction noise (stray characters,
  repeated whitespace artifacts) are not facts.
- **Prices, totals, and account numbers.** This corpus includes receipts and
  purchase orders. Do not extract a dollar amount, a total, an invoice
  number, or an account number — none of them answer a question anyone will
  ask standing in the desert.
- **Text that has lost its spacing**, e.g. `ssthan3feet(1meter)frombuilding`.
  This is PDF-extraction damage. It cannot be quoted usefully — skip any
  fact whose only support is text like this.
- **Questions.** Someone asking "does the pump need a new filter?" is not
  evidence that it does.

QUOTE RULES

- `quote` must be **verbatim text copied from the chunk below** — it will be
  mechanically checked against the chunk, and the fact is discarded if the
  quote cannot be found. Do not paraphrase, merge two sentences, or fix
  typos in the quote.
- Keep the quote short: the one to two sentences that actually support the
  fact, not the whole paragraph around them.

TOPIC — assign exactly one, from this list only:

- `water` — fills, tanks, gray/black water, drinking water
- `power` — generators, solar, batteries, electrical
- `kitchen` — cooking, food storage, dishes, the kitchen structure
- `shifts` — work shifts, sign-ups, schedules, duties
- `bikes` — the bike fleet, bike repair, bike storage
- `shade` — shade structures, shade cloth, build/teardown of shade
- `waste` — trash, MOOP, gray water disposal, recycling
- `arrival` — gate procedures, early arrival, load-in, camp setup order
- `safety` — first aid, fire safety, weather emergencies, medical
- `food` — meal planning, grocery lists, food safety (not the kitchen structure itself)
- `structures` — vehicles-as-structures, storage builds, camp infrastructure
  other than shade/kitchen (e.g. "Doris" the storage truck)
- `roster` — who is on camp, contact info, roles, chapters

If a fact does not clearly belong to one of these, leave it out rather than
force it into the wrong topic.

CATEGORY — assign exactly one:

- `how` — a procedure or mechanism ("we run the generator on a 12-hour cycle")
- `where` — a location ("the spare tanks live behind Doris")
- `who` — a person or role responsible ("the shift lead unlocks the tool truck")
- `when` — timing ("gray water is pumped out every Wednesday")
- `rule` — a requirement or constraint ("nothing may be within 3 feet of a structure")

Output a `facts` array. It is fine — and expected for many chunks, especially
receipts and cover pages — for this array to be empty.
