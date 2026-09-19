# Playful Design Language Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evolve the dormant `.soft` design language in the Lucy iOS app into the approved "playful" language (neon dusk palette, squish-on-press, campmate micro-copy) without changing `.industrial` rendering or the night face.

**Architecture:** All changes flow through the existing `Style` enum (language) and `Face` struct (color faces). Views already resolve type/radius/tracking via `Style.current`; this plan adds motion, press behaviour, label-casing, and two supporting colors to that same switch, then migrates the scattered per-view constants into it. Verification is by simulator render (`Lucy/render.sh`), not unit test — this project judges design work on the PNG (see CLAUDE.md).

**Tech Stack:** SwiftUI, xcodegen, `Lucy/render.sh` (simulator screenshot harness), `Lucy/device.sh` (device push).

**Spec:** `docs/superpowers/specs/2026-08-11-playful-design-language-design.md`

## Global Constraints

- **Never `git add -A`.** Stage named paths only (CLAUDE.md — repo contains untracked build dirs and a 106 MB corpus).
- **No string reachable by the model changes.** Nothing in `LucyBrain.swift`, `LucyVoice.swift`, `Retrieval.swift` is touched.
- **Night face renders must not change.** Every task that touches `Face` or a view re-renders night and compares against the pre-task render by eye (the status-bar clock differs between screenshots, so `cmp` will not work — compare content).
- **`.industrial` must render exactly as today** until the final gated task flips the default.
- **Monospaced facts stay monospaced** — `Font.data` call sites are never restyled to rounded.
- Render commands run from `Lucy/`: `SOFT=1 ./render.sh <screen> <face>` (the `--soft` launch arg is already wired through `render.sh:52` and `PreviewApp.swift:36`).
- Build check used throughout (from `Lucy/`):
  `xcodegen generate && xcodebuild -project Lucy.xcodeproj -scheme Lucy -sdk iphonesimulator -destination 'platform=iOS Simulator,name=iPhone 17 Pro' -configuration Debug build CODE_SIGNING_ALLOWED=NO -quiet`

---

### Task 1: Language properties on `Style` — motion, press, casing helpers

**Files:**
- Modify: `Lucy/Style.swift`

**Interfaces:**
- Produces: `Style.spring: Animation`, `Style.arrival: Animation`, `Style.pressScale: CGFloat`, `Style.isPlayful: Bool`, `Style.label(_ text: String) -> String`, `Style.boost(_ value: CGFloat) -> CGFloat`, `Style.tileLabel(_ size: CGFloat) -> Font`. All later tasks consume these exact names.

- [ ] **Step 1: Add the properties to the `Style` enum** (inside `enum Style`, after `rowSpacing`):

```swift
    /// True for the playful language. Copy and accent decisions key off this
    /// rather than comparing against the case directly, so a future rename
    /// of `.soft` touches one line.
    var isPlayful: Bool { self == .soft }

    /// The one spring. Industrial keeps today's tight feel; playful gets a
    /// small overshoot. New ad-hoc animation constants in views are a smell —
    /// see style-guide.md.
    var spring: Animation {
        switch self {
        case .industrial: return .spring(response: 0.25, dampingFraction: 0.8)
        case .soft:       return .spring(response: 0.35, dampingFraction: 0.65)
        }
    }

    /// Arrivals: a new answer or kept note entering the screen.
    var arrival: Animation {
        switch self {
        case .industrial: return .easeOut(duration: 0.25)
        case .soft:       return .spring(response: 0.4, dampingFraction: 0.7)
        }
    }

    /// Squish-on-press. 1.0 means no reaction (industrial).
    var pressScale: CGFloat {
        switch self {
        case .industrial: return 1.0
        case .soft:       return 0.96
        }
    }

    /// Labels are written in sentence case at the call site; industrial
    /// stencils them. "Ask" -> "ASK" under industrial, unchanged under soft.
    func label(_ text: String) -> String {
        switch self {
        case .industrial: return text.uppercased()
        case .soft:       return text
        }
    }

    /// Per-view tracking boosts (the `+ 0.5` sprinkled through views) only
    /// belong to the stencilled language. Soft neutralises them.
    func boost(_ value: CGFloat) -> CGFloat {
        switch self {
        case .industrial: return value
        case .soft:       return 0
        }
    }

    /// The big mode tiles' caption. Industrial matches today's eyebrow
    /// exactly; playful goes heavier than the eyebrow (spec: tiles at
    /// heavy/800-equivalent).
    func tileLabel(_ size: CGFloat) -> Font {
        switch self {
        case .industrial: return .system(size: size, weight: .heavy).width(.compressed)
        case .soft:       return .system(size: size, weight: .heavy, design: .rounded)
        }
    }
```

- [ ] **Step 2: Build**

Run (from `Lucy/`): the build check from Global Constraints.
Expected: succeeds with no warnings about `Style`.

- [ ] **Step 3: Verify industrial renders are unchanged**

```bash
cd Lucy && ./render.sh home dark && ./render.sh home night
```
Expected: `render-home-dark.png` and `render-home-night.png` look identical to current `master` renders (additive change only — nothing consumes the new properties yet).

- [ ] **Step 4: Commit**

```bash
git add Lucy/Style.swift
git commit -m "feat(style): motion, press, and casing become language properties"
```

---

### Task 2: Neon dusk palette — `Face.warm`, `Face.moment`, playful accents

**Files:**
- Modify: `Lucy/DesignSystem.swift`

**Interfaces:**
- Consumes: `Style.current` (existing).
- Produces: `Face.warm: Color`, `Face.moment: Color`, `Face.keepText: Color` — consumed by Tasks 3, 5. Playful (`soft`) accent values change; industrial values must not.

- [ ] **Step 1: Extend the `Face` struct** — add three stored properties after `keep`:

```swift
    let keepText: Color    // text/icon on a keep-filled surface
    let warm: Color        // gold: eyebrows, chips, small friendly moments
    let moment: Color      // violet: rare accents; twice on one screen is once too many
```

- [ ] **Step 2: Update the three face constructors.** Every existing industrial value stays byte-identical; only `soft`-branch values and the three new properties are added.

`day` becomes:

```swift
    static var day: Face {
        let soft = Style.current == .soft
        return Face(
            ground:   Color(hex: 0xDFE2DB),
            raised:   Color(hex: 0xF2F4EE),
            text:     Color(hex: soft ? 0x1B1F24 : 0x111419),
            muted:    Color(hex: 0x676C63),
            accent:   Color(hex: soft ? 0xE05A32 : 0xDE320A),
            keep:     Color(hex: soft ? 0x2E8699 : 0x1B4D5A),
            keepText: .white,
            warm:     Color(hex: soft ? 0xB77F35 : 0x676C63),
            moment:   Color(hex: soft ? 0x6F5FD0 : 0x1B4D5A),
            hairline: Color(hex: 0x111419).opacity(Style.current.hairlineOpacity),
            night: false
        )
    }
```

`dark` becomes:

```swift
    static var dark: Face {
        let soft = Style.current == .soft
        return Face(
            ground:   Color(hex: 0x16181C),
            raised:   Color(hex: 0x22262C),
            text:     Color(hex: 0xECEDE9),
            muted:    Color(hex: 0x8B9199),
            accent:   Color(hex: soft ? 0xFF6B47 : 0xF04A1E),
            keep:     Color(hex: soft ? 0x3FB8CC : 0x3E8B9C),
            // Industrial dark must stay 0x07080A: the dark face has night:true,
            // so the pre-keepText idiom (face.night ? 0x07080A : .white)
            // rendered a near-black icon here — .white would be a regression.
            keepText: Color(hex: soft ? 0x07222A : 0x07080A),
            warm:     Color(hex: soft ? 0xFFC46B : 0x8B9199),
            moment:   Color(hex: soft ? 0x9D8CFF : 0x3E8B9C),
            hairline: Color(hex: 0xECEDE9).opacity(Style.current.hairlineOpacity + 0.03),
            night: true
        )
    }
```

`night` — **accents unchanged**; the new properties map onto colors the night face already uses, so any view that adopts `warm`/`moment` renders identically at night:

```swift
    static var night: Face {
        let soft = Style.current == .soft
        return Face(
            ground:   Color(hex: 0x07080A),
            raised:   Color(hex: 0x131519),
            text:     Color(hex: 0xEFE9DE),
            muted:    Color(hex: 0x8B7A61),
            accent:   Color(hex: soft ? 0xF0A867 : 0xFF9A3C),
            keep:     Color(hex: soft ? 0x74C7D3 : 0x4FC4D6),
            keepText: Color(hex: 0x07080A),
            warm:     Color(hex: 0x8B7A61),
            moment:   Color(hex: soft ? 0xF0A867 : 0xFF9A3C),
            hairline: Color(hex: 0xFF9A3C).opacity(Style.current.hairlineOpacity + 0.05),
            night: true
        )
    }
```

Note the industrial fallbacks for `warm`/`moment` are the colors those spots use today (`muted`, `keep`/`accent`), so industrial adoption sites keep rendering as before.

- [ ] **Step 3: Fix the `ModeBar` icon color to use `keepText`.** In `Lucy/HomeScreen.swift:93` the REMEMBER icon color is `face.night ? Color(hex: 0x07080A) : .white` — replace with:

```swift
                            .foregroundStyle(face.keepText)
```

(The ASK circle at `HomeScreen.swift:73` keeps its existing expression — coral is dark enough for white in every face.)

- [ ] **Step 4: Build, then render both languages**

```bash
cd Lucy && ./render.sh home dark && SOFT=1 OUT=render-home-dark-soft.png ./render.sh home dark && ./render.sh home night && SOFT=1 OUT=render-home-night-soft.png ./render.sh home night
```
Expected: industrial dark unchanged; soft dark shows coral `#FF6B47` circle and aqua `#3FB8CC` square with dark icon; both night renders identical to each other and to master's night render.

- [ ] **Step 5: Commit**

```bash
git add Lucy/DesignSystem.swift Lucy/HomeScreen.swift
git commit -m "feat(design): neon dusk palette behind the soft language"
```

---

### Task 3: Squish-on-press

**Files:**
- Create: `Lucy/Squish.swift`
- Modify: `Lucy/HomeScreen.swift:80,101` (the two `.buttonStyle(.plain)` lines in `ModeBar`), `Lucy/Capture.swift` (add `Haptics.press()`)

**Interfaces:**
- Consumes: `Style.current.pressScale`, `Style.current.spring` (Task 1).
- Produces: `SquishButtonStyle` (a `ButtonStyle`), `Haptics.press()`.

- [ ] **Step 1: Create `Lucy/Squish.swift`:**

```swift
import SwiftUI

/// Press acknowledgement for the playful language: scale to
/// `Style.pressScale` while held, spring back with the language spring, and
/// tick the Taptic Engine on the way down. Industrial resolves pressScale to
/// 1.0, so applying this style there is a no-op — call sites don't branch.
struct SquishButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? Style.current.pressScale : 1)
            .animation(Style.current.spring, value: configuration.isPressed)
            .onChange(of: configuration.isPressed) { _, pressed in
                if pressed, Style.current.isPlayful { Haptics.press() }
            }
    }
}
```

- [ ] **Step 2: Add the soft tick to `Haptics`** in `Lucy/Capture.swift`, next to `shutter()`:

```swift
    /// The squish tick. Deliberately quiet — this fires on every primary
    /// button, and recordStart() must stay the unmistakable one.
    static func press() { soft.impactOccurred(intensity: 0.7); soft.prepare() }
```

- [ ] **Step 3: Apply to the two home tiles.** In `ModeBar`, replace both `.buttonStyle(.plain)` (lines 80 and 101) with:

```swift
            .buttonStyle(SquishButtonStyle())
```

- [ ] **Step 3b: Apply to the live primary buttons** (added after discovering `HomeScreen`/`ModeBar` is design-harness dead code — the shipping flow opens straight into `ChatView`; Marcus chose "live screens + keep harness"). Apply `.buttonStyle(SquishButtonStyle())` to:

| site | control |
|---|---|
| `ChatView.swift:327-335` | the composer send button (currently `.buttonStyle(.plain)` at 335 — replace) |
| `ModelGate.swift:75-84` | the download/action button (no explicit style today — add after the `.padding(.top, 26)`) |
| `SyncView.swift` | the Sync button (the `Button` wrapping the `Text(busy ? "Syncing…" : "Sync")` label at line 164 — locate the wrapping `Button` and style it) |

Do not touch `HoldToRecord` — it has its own pressed-state treatment migrating in Task 4. Secondary/navigation buttons ("Done", "Close", "more") stay unstyled: squish marks primary actions only.

- [ ] **Step 4: Give the tiles their hint of depth** (spec: "slightly taller with a hint of depth" — the shapes are aspect-ratio-sized, so depth is the applicable half). On both mode shapes in `ModeBar` — the `ZStack` containing the ASK `Circle` (after `.aspectRatio(1, contentMode: .fit)` at `HomeScreen.swift:75`) and the REMEMBER square's `ZStack` (after `.padding(squareOpticalInset)` at line 96) — add:

```swift
                    .shadow(color: .black.opacity(Style.current.isPlayful ? 0.18 : 0),
                            radius: 0, y: 4)
```

A flat offset shadow (radius 0), matching the chosen mockup's underside — not a blur. Industrial resolves to invisible.

- [ ] **Step 5: Build and render**

Run the build check, then:
```bash
cd Lucy && ./render.sh home dark && SOFT=1 OUT=render-home-dark-soft.png ./render.sh home dark && ./render.sh home night
```
Expected: build passes; industrial and night renders unchanged from Task 2; soft dark shows the 4pt underside on both shapes. Press feel is judged on-device in Task 7.

- [ ] **Step 6: Commit**

```bash
git add Lucy/Squish.swift Lucy/Capture.swift Lucy/HomeScreen.swift
git commit -m "feat(motion): squish-on-press with soft haptic tick"
```

---

### Task 4: Migrate scattered springs to the language

**Files:**
- Modify: `Lucy/ChatView.swift:167,240,382`, `Lucy/HoldToRecord.swift:71,143`, `Lucy/AskVoiceView.swift:180`

**Interfaces:**
- Consumes: `Style.current.spring`, `Style.current.arrival` (Task 1).

- [ ] **Step 1: Replace the constants.** Exact substitutions (leave every other `.animation`/`withAnimation` alone — in particular the level meters at `HoldToRecord.swift:72,205` and the 0.85s breathing at `HoldToRecord.swift:210`):

| site | today | becomes |
|---|---|---|
| `ChatView.swift:167` | `withAnimation(.easeOut(duration: 0.25))` | `withAnimation(Style.current.arrival)` |
| `ChatView.swift:240` | `withAnimation(.easeOut(duration: 0.18))` | `withAnimation(Style.current.arrival)` |
| `ChatView.swift:382` | `.animation(.spring(response: 0.28, dampingFraction: 0.8), value: hasText)` | `.animation(Style.current.spring, value: hasText)` |
| `HoldToRecord.swift:71` | `.animation(.spring(response: 0.24, dampingFraction: 0.7), value: holding)` | `.animation(Style.current.spring, value: holding)` |
| `HoldToRecord.swift:143` | `.animation(.spring(response: 0.22, dampingFraction: 0.7), value: pressed)` | `.animation(Style.current.spring, value: pressed)` |
| `AskVoiceView.swift:180` | `.animation(.spring(response: 0.28, dampingFraction: 0.7), value: listening)` | `.animation(Style.current.spring, value: listening)` |

Industrial's `spring` (0.25/0.8) is within a hair of each replaced constant (0.22–0.28 / 0.7–0.8); the unification is deliberate and approved by the spec.

- [ ] **Step 2: Build and spot-render**

Run the build check, then:
```bash
cd Lucy && SOFT=1 QUERY="where are the barrels" SCREEN=ask OUT=render-ask-dark-soft.png ./render.sh ask dark
```
Expected: build passes; ask screen renders normally (animation end-states are identical to before).

- [ ] **Step 3: Commit**

```bash
git add Lucy/ChatView.swift Lucy/HoldToRecord.swift Lucy/AskVoiceView.swift
git commit -m "refactor(motion): all springs resolve through the language"
```

---

### Task 5: Sentence case and warm accents

**Files:**
- Modify: `Lucy/DesignSystem.swift` (the `Eyebrow` view), `Lucy/HomeScreen.swift:76,97,180,206`, `Lucy/AppFlow.swift:77`, `Lucy/ChatView.swift:116`, `Lucy/AskVoiceView.swift:75`, `Lucy/SyncView.swift:29`, `Lucy/ModelGate.swift:59`

**Interfaces:**
- Consumes: `Style.label(_:)`, `Style.boost(_:)` (Task 1), `Face.warm` (Task 2).

- [ ] **Step 1: Teach `Eyebrow` the language.** In `Lucy/DesignSystem.swift`, replace the `Eyebrow` body so casing and color resolve per language (call sites keep passing today's caps — `label()` only uppercases, so playful still needs the source text fixed where it should read as a sentence; that's Step 3):

```swift
struct Eyebrow: View {
    let text: String
    let face: Face
    var body: some View {
        Text(Style.current.isPlayful ? sentenceCased : text)
            .font(.eyebrow())
            .tracking(Style.current.eyebrowTracking)
            .foregroundStyle(Style.current.isPlayful ? face.warm : face.muted)
    }
    /// "YOU REMEMBERED" -> "You remembered". Good enough for the chrome's
    /// short all-caps labels; anything needing real casing passes it already.
    private var sentenceCased: String {
        let lower = text.lowercased()
        return lower.prefix(1).uppercased() + lower.dropFirst()
    }
}
```

- [ ] **Step 2: Absorb the tracking boosts.** Exact substitutions:

| site | today | becomes |
|---|---|---|
| `HomeScreen.swift:76` | `.tracking(Style.current.eyebrowTracking + 0.5)` | `.tracking(Style.current.eyebrowTracking + Style.current.boost(0.5))` |
| `HomeScreen.swift:97` | same | same substitution |
| `HomeScreen.swift:180` | `.tracking(Style.current.eyebrowTracking + 0.2)` | `.tracking(Style.current.eyebrowTracking + Style.current.boost(0.2))` |
| `AppFlow.swift:77` | `.tracking(Style.current.eyebrowTracking + 0.5)` | `.tracking(Style.current.eyebrowTracking + Style.current.boost(0.5))` |
| `ChatView.swift:116` | same | same substitution |
| `AskVoiceView.swift:75` | `.tracking(Style.current.eyebrowTracking + 0.5)` | `.tracking(Style.current.eyebrowTracking + Style.current.boost(0.5))` |

- [ ] **Step 3: Route the standalone labels through `label()`.** These are `Text` literals, not `Eyebrow`s:

| site | today | becomes |
|---|---|---|
| `HomeScreen.swift:76` | `Text("ASK").font(.eyebrow(15))` | `Text(Style.current.label("Ask")).font(Style.current.tileLabel(15))` |
| `HomeScreen.swift:97` | `Text("REMEMBER").font(.eyebrow(15))` | `Text(Style.current.label("Remember")).font(Style.current.tileLabel(15))` |
| `HomeScreen.swift:180` | `Text("WEDNESDAY")` | `Text(Style.current.label("Wednesday"))` |
| `SyncView.swift:29` | `Text("SYNC")` | `Text(Style.current.label("Sync"))` |
| `ModelGate.swift:59` | `Text(Device.tooSmall ? "THIS PHONE\nIS TOO SMALL" : "LUCY NEEDS\nHER BRAIN")` | `Text(Style.current.label(Device.tooSmall ? "This phone\nis too small" : "Lucy needs\nher brain"))` |

(The two tile labels keep their existing `.tracking(...)` modifier, which Step 2 already converts to the `boost` form. `tileLabel`'s industrial branch is identical to `eyebrow`'s, so industrial renders unchanged.)

- [ ] **Step 4: The kept-count goes violet** — `face.moment`'s one job on this screen, matching the chosen mockup's kept-count chip (spec: "moment … e.g. kept-count chip"). In `Lucy/HomeScreen.swift:209`, replace:

```swift
                    Text("\(captures.count) saved").font(.data(11))
                        .foregroundStyle(face.muted)
```

with:

```swift
                    Text("\(captures.count) saved").font(.data(11))
                        .foregroundStyle(Style.current.isPlayful ? face.moment : face.muted)
```

(`night.moment` is night's accent color, and industrial's is today's muted-adjacent value — neither face regresses. This stays `moment`'s only appearance; a second violet on the home screen is a review-blocker per style-guide.md.)

- [ ] **Step 5: Build and render the pair**

```bash
cd Lucy && ./render.sh home dark && SOFT=1 OUT=render-home-dark-soft.png ./render.sh home dark && SOFT=1 OUT=render-home-night-soft.png ./render.sh home night
```
Expected: industrial identical to Task 2's render (uppercase, muted eyebrows, boosts intact). Soft: "Ask" / "Remember" / "Wednesday" in sentence case, eyebrows gold. Night+soft render shows *no* gold — `night.warm` is its muted color — and unchanged casing is acceptable there per spec only if it matches; it will show sentence case, which is a **playful-language** change, not a face change, and night ships on industrial until the flip. Confirm night+industrial (`./render.sh home night`) is untouched.

- [ ] **Step 6: Commit**

```bash
git add Lucy/DesignSystem.swift Lucy/HomeScreen.swift Lucy/AppFlow.swift Lucy/ChatView.swift Lucy/AskVoiceView.swift Lucy/SyncView.swift Lucy/ModelGate.swift
git commit -m "feat(design): sentence case and warm accents under the playful language"
```

---

### Task 6: Campmate micro-copy

**Files:**
- Modify: `Lucy/HomeScreen.swift:219-226`, `Lucy/KeptNotesView.swift:34`, `Lucy/SyncView.swift:42`, `Lucy/Sync.swift:84-85`, `Lucy/ChatView.swift:310`, `Lucy/PeopleView.swift:90`, `Lucy/ModelGate.swift:96-97`

**Interfaces:**
- Consumes: `Style.current.isPlayful` (Task 1). No new interfaces.

Every change is a static string in a view or a user-facing error. **None of these strings are visible to the model.** `Sync.swift`'s `errorDescription` is user-facing UI text (shown via `SyncView`'s `problem` label), not model input — confirmed by reading the call sites at `SyncView.swift:189,200,223,237`.

- [ ] **Step 1: Apply the copy.** Playful branch first, industrial keeps today's text:

`HomeScreen.swift:219` — the empty-state headline and explainer:

```swift
                    Text(Style.current.isPlayful ? "Nothing saved yet" : "Nothing saved yet.")
                        .font(.display(28))
                        .foregroundStyle(face.text)
                    Text(Style.current.isPlayful
                         ? "Hold Remember, point at a thing, and say what it is — "
                           + "the camp will still know about it in a year."
                         : "REMEMBER photographs a thing and takes a voice note, "
                           + "so the camp still knows about it in a year.")
                        .font(.body_(15))
                        .foregroundStyle(face.muted)
                        .fixedSize(horizontal: false, vertical: true)
```

`KeptNotesView.swift:34`:

```swift
                Text(Style.current.isPlayful
                     ? "Nothing kept yet — hold the button and tell me something."
                     : "Nothing kept yet.")
```

`SyncView.swift:42`:

```swift
                Text(Style.current.isPlayful ? "All caught up — nothing waiting to send."
                                             : "Nothing waiting to send.")
```

`Sync.swift:84-85` (inside `errorDescription`):

```swift
        case .unreachable:
            return Style.current.isPlayful
                ? "Couldn't reach camp — I'll keep everything safe here."
                : "Couldn't reach the camp server. Try again when you have signal."
```

`ChatView.swift:310` — the field placeholder:

```swift
            TextField(Style.current.isPlayful
                      ? "Ask me where things are, who's on shift, what's on today…"
                      : "Ask Lucy",
                      text: $typed, axis: .vertical)
```

`PeopleView.swift:90` — **deviation from the spec's example, on purpose.** The spec's approved line ("Nobody here yet — mention a campmate and I'll start keeping track") misdescribes the mechanism: the roster loads from the corpus database, not from mentions. Inventing a mechanism in copy is the same sin as a claim no row supports. Truthful playful version:

```swift
                        Text(Style.current.isPlayful
                             ? (people.isEmpty ? "No roster on board yet."
                                               : "Nobody by that name.")
                             : (people.isEmpty ? "NO ROSTER LOADED" : "NOBODY BY THAT NAME"))
```

`ModelGate.swift:96-97` — the wi-fi line:

```swift
                Text(Style.current.isPlayful
                     ? "Getting Lucy's brain on board is a one-time, on-wi-fi job — "
                       + "there's no signal on playa, and she keeps it once she has it."
                     : "Do this on wi-fi, before you leave. There is no signal on "
                       + "playa, and she keeps her brain once she has it.")
```

- [ ] **Step 2: Build and render copy-bearing screens**

```bash
cd Lucy && SOFT=1 OUT=render-home-dark-soft.png ./render.sh home dark && SOFT=1 SCREEN=ask OUT=render-ask-dark-soft.png ./render.sh ask dark
```
Expected: playful copy appears; verify no truncation on the empty state. Then `./render.sh home dark` — industrial copy byte-identical to before.

- [ ] **Step 3: Commit**

```bash
git add Lucy/HomeScreen.swift Lucy/KeptNotesView.swift Lucy/SyncView.swift Lucy/Sync.swift Lucy/ChatView.swift Lucy/PeopleView.swift Lucy/ModelGate.swift
git commit -m "feat(copy): the chrome talks like a campmate under the playful language"
```

---

### Task 7: Full render sweep, device judgment, gated default flip

**Files:**
- Modify: `Lucy/Style.swift:18` (only after on-device approval)

- [ ] **Step 1: Render the full comparison set**

```bash
cd Lucy
for screen in home ask entity; do
  ./render.sh $screen dark
  SOFT=1 OUT=render-$screen-dark-soft.png ./render.sh $screen dark
  ./render.sh $screen day
  SOFT=1 OUT=render-$screen-day-soft.png ./render.sh $screen day
  ./render.sh $screen night
  SOFT=1 OUT=render-$screen-night-soft.png ./render.sh $screen night
done
```
Expected: 18 PNGs. Check: industrial set matches master; night industrial untouched; soft day accents legible on the alkali ground (this is where the day-face hexes from Task 2 get tuned — iterate `DesignSystem.swift` values and re-render until they hold up, then amend the Task 2 commit or add a `tweak(design): day-face neon dusk tuning` commit).

- [ ] **Step 2: Show Marcus the renders.** Attach the soft/industrial pairs in the conversation (house rule: no claim about how something looks without an image in the message). Wait for approval.

- [ ] **Step 3: Push to the phone for feel**

```bash
cd Lucy && ./device.sh
```
Squish, haptic tick, and springs are judged on the device — the Simulator proves neither haptics nor real spring feel. Marcus judges; do not proceed to Step 4 without his explicit go-ahead.

- [ ] **Step 4 (gated on Step 3 approval): Flip the default.** In `Lucy/Style.swift:18`:

```swift
    static var current: Style = .soft
```

- [ ] **Step 5: Re-render night to prove the flip didn't touch it**

```bash
cd Lucy && ./render.sh home night
```
Expected: identical content to the pre-flip night render (night's soft accent values are within a hair of industrial's; if any visible difference appears, set night's soft-branch accents in `DesignSystem.swift` equal to the industrial values and re-render).

- [ ] **Step 6: Commit**

```bash
git add Lucy/Style.swift
git commit -m "feat(design): the playful language becomes the default"
```

---

## Explicitly out of scope

- The camp site (`site/index.html`), the Android port (`android/`), the backend.
- Renaming `.soft` → `.playful` and deleting `.industrial` — deferred until the language has survived a week of real use.
- Re-rendering `docs/design/renders/` (already stale for other reasons) and the camp-site screenshots — do them when the language ships in a release, via `Lucy/render.sh`.
