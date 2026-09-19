# Playful design language for Lucy

2026-08-11. Brainstormed with Marcus; palette and motion chosen visually
(neon dusk mockup, live motion demo), copy direction approved from the
before/after table.

## Intent

Evolve the dormant `.soft` design language in `Lucy/Style.swift` into a
warm-and-friendly ("playful") language and make it the shipping default.
Clearly softer and more human than the industrial language — rounded
everything, springy touches, warmer copy — but still a calm tool. Nothing
moves unless you touched it. Night face stays serious.

## Constraints (non-negotiable)

- **Faces stay structurally untouched.** Day stays cool alkali dust; night
  stays amber-on-near-black for dark adaptation. Only accent values inside
  `Face` change, and night's accents do not get brighter.
- **Monospaced data text keeps its meaning** — measured facts read as
  recorded, not generated, in both languages.
- **Copy changes are UI chrome only.** Static strings in SwiftUI views.
  Nothing in `LucyBrain.prompt`, `LucyVoice`, or anything the model sees
  changes. (See CLAUDE.md: no quotable example sentences near the model.)
- `.industrial` keeps rendering exactly as today until the playful language
  is proven on-device; then playful becomes `Style.current`'s default.

## Architecture

`Style` stays the single switch; `.soft` evolves in place (no third case,
no rename yet). New language properties on `Style` so motion and press
behaviour are language decisions, not scattered constants:

- `spring: Animation` — industrial: response ~0.25, damping 0.8 (today's
  feel); playful: response ~0.35, damping ~0.65 (small overshoot).
- `pressScale: CGFloat` — industrial 1.0 (no squish), playful 0.96–0.97.

Existing ad-hoc `.spring(...)` constants in `ChatView`, `HoldToRecord`,
`AskVoiceView` migrate to `Style.current.spring`. The per-view
`eyebrowTracking + 0.5` boosts move into `Style` so soft can neutralise
them.

## Palette — "neon dusk"

Chosen on the dark face (the everyday default). Saturated, playa-art
energy:

| role | dark face | notes |
|---|---|---|
| ASK accent | `#FF6B47` | stays unmistakably the ASK red/orange family |
| REMEMBER keep | `#3FB8CC` | bright aqua; dark text on filled tiles |
| warm (new) | `#FFC46B` | gold; eyebrows, chips, small friendly moments |
| moment (new) | `#9D8CFF` | violet; rare accents (e.g. kept-count chip) |

- Day face gets the same hues translated for the alkali ground; exact hex
  values are chosen by rendering (`render.sh`), not on paper.
- Night face: completely unchanged.
- `warm` and `moment` are supporting colors with defined jobs, not a free
  palette. `warm` carries most of the personality; `moment` is rare.

## Type & shape

- SF Rounded stays; label weights go up (tiles at heavy/800-equivalent).
- Sentence case wherever playful is active; industrial keeps its caps.
- Radii near soft's current 30/12, `.continuous` everywhere; home tiles
  slightly taller with a hint of depth (soft shadow or darker underside).
- Hairlines: playful drops most row separators (soft's 0.07 opacity or
  none); the quote rule beside evidence stays (rounded, 5pt).

## Motion & haptics

Approved as demoed:

- **Squish on press** — home tiles and primary buttons scale to
  `pressScale` while held, with a `.soft` haptic tick (reuse the
  generators in `Capture.swift`), springing back with a small overshoot.
- **Arrival** — new answers and kept notes slide in on the playful spring.
- **No idle motion.** The only thing that moves untouched is the existing
  0.85s hold-to-record breathing pulse, which stays as-is.

## Voice & micro-copy

Lucy's chrome talks like a campmate, not a control panel. Approved
examples (static strings only):

- Home tiles: `Ask` / `Remember`.
- Kept notes empty: "Nothing kept yet — hold the button and tell me
  something."
- Chat first open: "Ask me where things are, who's on shift, what's on
  today…"
- People empty: "Nobody here yet — mention a campmate and I'll start
  keeping track."
- Model download: "Getting Lucy's brain on board (3.1 GB, one-time)".
- Sync failure: "Couldn't reach camp — I'll keep everything safe here."

Rules: button verbs stay verbs (Sync, Keep, Delete); destructive dialogs
stay plain ("Delete this note?"); monospaced facts stay unadorned; at most
one gentle line per screen. Error copy keeps the facts (sizes, causes) —
only tone changes.

## Verification

- `render.sh` side-by-sides (industrial vs playful, dark and day faces)
  at every color/type decision; judge the PNG, not the diff.
- Final judgment on the phone via `device.sh` — the Simulator does not
  prove the phone, and haptics only exist on the device.
- Night face renders before/after must be pixel-identical.

## Out of scope

- The camp site (`site/index.html`), the Android port, and the backend.
- Any change to retrieval, prompts, or answer composition.
- Renaming `.soft` → `.playful` and deleting `.industrial`: deferred until
  the language has been judged on-device.
