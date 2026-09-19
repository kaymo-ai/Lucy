# Lucy style guide

How Lucy looks, moves, and talks. The mechanics live in `Lucy/Style.swift`
(design language) and `Lucy/DesignSystem.swift` (color faces); this file is
the human-readable contract. Design decisions are judged on renders
(`Lucy/render.sh`) and on the phone (`Lucy/device.sh`), not in code review.

## Two axes, kept separate

- **Face** — day / dark / night. Answers "what light are we in".
- **Style** — the design language. Answers "how hard-edged is this".

They never collapse into one switch. Night is not a theme preference; it is
a dark-adaptation mode for 3am on playa and trades color fidelity for night
vision.

## The languages

| | industrial (legacy) | playful (target default) |
|---|---|---|
| display type | compressed black, wide tracking | SF Rounded, bold–heavy |
| case | STENCIL CAPS | Sentence case |
| corners | 14 / 5 | 30 / 12, `.continuous` |
| hairlines | 0.15 — ruled form | 0.07 or none |
| press | no reaction | squish to ~0.96 + soft haptic |
| springs | tight (0.25 / 0.8) | bouncy (0.35 / ~0.65), small overshoot |

`playful` is the evolved `.soft` case in `Style.swift`. `industrial` stays
selectable until playful is proven on-device.

## Palette — neon dusk (dark face)

| role | hex | job |
|---|---|---|
| ASK | `#FF5326` | the ask color. Always red/orange family — muscle memory. |
| REMEMBER | `#2BC7E3` | the keep color. Dark text when used as a fill. |
| warm | `#FFB23E` | gold. Eyebrows, chips, small friendly moments. Carries most of the personality. |
| moment | `#8B72FF` | violet. Rare accents only. If it appears twice on one screen, that's once too many. |

(Vibrancy bumped from the first approved set on 2026-08-11, judged on renders.)

Ground `#16181C`, raised `#22262C`, text `#ECEDE9`, muted `#8B9199`
(unchanged). Day-face equivalents are tuned by render against the alkali
ground, not derived arithmetically. **Night face uses none of this** — it
keeps its amber/teal accents and nothing gets brighter there.

## Motion rules

1. Nothing moves unless you touched it. The single exception is the 0.85s
   hold-to-record breathing pulse.
2. Press = squish + soft haptic tick; release springs back with a small
   overshoot.
3. Arrivals (new answer, kept note) slide in on the language spring. No
   entrance animation is ever longer than half a second.
4. All springs come from `Style.current.spring`. New ad-hoc animation
   constants in views are a smell.

## Type rules

- Measured facts — times, quantities, quotes from the record — are always
  monospaced (`Font.data`). Monospace means "recorded, not generated";
  it is a meaning, not a decoration.
- Sentence case in playful, everywhere. No exclamation marks in labels.

## The chat

Both sides of the conversation are one bubble pattern: a raised card with a
3pt rule on the outside edge. Her rule is warm gold; yours is keep teal.
The coral accent belongs to the record/ask actions alone — nothing else on
the chat screen may use it.

## Voice

Lucy's chrome talks like a campmate, not a control panel.

- Button verbs stay verbs: Sync, Keep, Delete.
- Destructive dialogs stay plain. "Delete this note?" — no cuteness where
  data is at stake.
- Empty states get one warm line, at most one gentle joke per screen.
- Error copy keeps the facts (sizes, causes); only the tone softens.
  "Couldn't reach camp — I'll keep everything safe here."
- **UI copy never goes near the model.** Static strings in views only.
  Nothing here changes `LucyBrain.prompt` or what Gemma sees; she must
  never be able to quote the chrome as an answer.

## Verifying a design change

- Render both languages, both faces, before/after. Judge the PNG.
- Night-face renders must be pixel-identical unless the change is
  explicitly about night.
- Haptics and true feel are judged on the phone via `device.sh`; the
  Simulator does not prove the phone.

Full design rationale: `docs/superpowers/specs/2026-08-11-playful-design-language-design.md`.
