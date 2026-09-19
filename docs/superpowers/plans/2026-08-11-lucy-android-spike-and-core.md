# Lucy on Android — spike and retrieval core

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Settle the one unknown that can invalidate the Android design — token generation throughput for the 1B model on the real phone — and land the retrieval core with parity tests, which is valuable whatever that number turns out to be.

**Architecture:** Throughput is measured with upstream `llama-bench` pushed over adb, before any Kotlin exists. Retrieval is then ported into `android/core/`, a plain JVM Gradle module with no Android dependencies, tested on the desktop with JUnit against an in-memory fixture rather than SQLite.

**Tech Stack:** JDK 17, Android SDK + NDK r27, CMake, Gradle 8.x, Kotlin 2.0, JUnit 5.

This is plan 1 of several. It deliberately stops before the Compose UI, the JNI bridge, speech, capture and sync — those are shaped by the number Task 3 produces, and planning them now would be planning against a guess.

Spec: `docs/superpowers/specs/2026-08-11-lucy-android-design.md`

## Global Constraints

- `minSdk 29`, `arm64-v8a` only. No 32-bit ARM.
- llama.cpp stays pinned at `08659901c43b51de735740f1cf61bb82fbe0c4e4`, shared with the iOS xcframework. Do not bump it in this plan.
- `android/core/` is `kotlin("jvm")`, never `com.android.library`. An `import android.*` in that module must fail the build.
- **Never `git add -A`.** Stage named paths. Build directories and a 106 MB corpus live in this repo.
- Model under test is `gemma-3-1b-it-Q4_K_M.gguf`, 806058240 bytes, sha256 `8ccc5cd1f1b3602548715ae25a66ed73fd5dc68a210412eea643eb20eb75a135`.
- Nothing in this plan edits a file under `Lucy/` or `backend/`.

---

### Task 1: Android toolchain

Nothing Android exists on this machine. `adb` is present via Homebrew; everything else is missing.

**Files:**
- Create: `android/README.md`

**Interfaces:**
- Consumes: nothing.
- Produces: `$ANDROID_HOME` populated with `platform-tools`, `ndk`, `cmake`; `java` on PATH at 17; `gradle` on PATH.

- [ ] **Step 1: Install JDK 17 and Gradle**

Android Gradle Plugin 8.x requires JDK 17 exactly — 21 and 24 both fail with toolchain errors that read as unrelated Kotlin problems.

```bash
brew install --cask temurin@17 && brew install gradle
```

- [ ] **Step 2: Verify the JDK**

Run: `java -version 2>&1 | head -1`
Expected: a line containing `17.` — for example `openjdk version "17.0.13" 2024-10-15`

If it reports 21 or 24, set `JAVA_HOME` explicitly:

```bash
export JAVA_HOME=$(/usr/libexec/java_home -v 17)
```

- [ ] **Step 3: Install the Android command-line tools**

Android Studio is not needed and is a 1.2 GB download that buys nothing for a CLI workflow.

```bash
brew install --cask android-commandlinetools
```

- [ ] **Step 4: Install the SDK, NDK and CMake packages**

```bash
export ANDROID_HOME=/opt/homebrew/share/android-commandlinetools
sdkmanager --install "platform-tools" "platforms;android-35" "build-tools;35.0.0" "ndk;27.2.12479018" "cmake;3.22.1"
```

If `ndk;27.2.12479018` is rejected as unknown, list what is actually available and take the highest 27.x:

```bash
sdkmanager --list | grep -E '^\s+ndk;27'
```

- [ ] **Step 5: Verify the NDK landed**

Run: `ls $ANDROID_HOME/ndk`
Expected: a directory named `27.2.12479018` (or the 27.x you selected)

- [ ] **Step 6: Record the environment so it survives a new shell**

Create `android/README.md`:

```markdown
# Lucy for Android

## Toolchain

Requires JDK 17 (not 21, not 24 — AGP 8.x rejects both with errors that look
like Kotlin problems).

    export JAVA_HOME=$(/usr/libexec/java_home -v 17)
    export ANDROID_HOME=/opt/homebrew/share/android-commandlinetools
    export ANDROID_NDK=$ANDROID_HOME/ndk/27.2.12479018
    export PATH=$ANDROID_HOME/platform-tools:$PATH

Install with:

    brew install --cask temurin@17 android-commandlinetools
    brew install gradle
    sdkmanager --install "platform-tools" "platforms;android-35" \
      "build-tools;35.0.0" "ndk;27.2.12479018" "cmake;3.22.1"

## Modules

- `core/` — plain JVM. Retrieval and voice. No Android imports, ever.
  Runs on the desktop with `gradle :core:test`.
```

- [ ] **Step 7: Commit**

```bash
git add android/README.md
git commit -m "docs: android toolchain requirements"
```

---

### Task 2: Build llama.cpp for arm64 Android

No Kotlin, no Gradle. This produces a native binary that answers the throughput question directly.

**Files:**
- Create: `SnailsNative/vendor/build-llama-android.sh`

**Interfaces:**
- Consumes: `$ANDROID_NDK` from Task 1.
- Produces: `SnailsNative/vendor/build-android/bin/llama-bench`, an arm64 Android executable.

- [ ] **Step 1: Confirm the submodule is at the pinned SHA**

Run: `git -C SnailsNative/vendor/llama.cpp rev-parse HEAD`
Expected: `08659901c43b51de735740f1cf61bb82fbe0c4e4`

If it differs: `git submodule update --init --recursive`

- [ ] **Step 2: Write the build script**

Create `SnailsNative/vendor/build-llama-android.sh`:

```bash
#!/usr/bin/env bash
# Builds llama.cpp CLI tools for arm64 Android from the pinned submodule.
# Mirrors build-llama-xcframework.sh: same source, same SHA, different toolchain.
set -euo pipefail

VENDOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="${VENDOR_DIR}/llama.cpp"
BUILD_DIR="${VENDOR_DIR}/build-android"
EXPECTED_SHA="$(cat "${VENDOR_DIR}/llama.cpp.pinned-sha")"
ACTUAL_SHA="$(git -C "${SRC_DIR}" rev-parse HEAD)"

if [ "${EXPECTED_SHA}" != "${ACTUAL_SHA}" ]; then
  echo "ERROR: llama.cpp submodule is at ${ACTUAL_SHA}, expected ${EXPECTED_SHA}" >&2
  echo "Run: git submodule update --init --recursive" >&2
  exit 1
fi

: "${ANDROID_NDK:?set ANDROID_NDK — see android/README.md}"

# GGML_OPENMP=OFF is not optional. The NDK does not ship a libomp that links
# cleanly for Android, and leaving it on fails at the very end of a long build
# with an unresolved-symbol error that reads as a CMake problem.
cmake -B "${BUILD_DIR}" -S "${SRC_DIR}" \
  -DCMAKE_TOOLCHAIN_FILE="${ANDROID_NDK}/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI=arm64-v8a \
  -DANDROID_PLATFORM=android-29 \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_OPENMP=OFF \
  -DLLAMA_CURL=OFF \
  -DLLAMA_BUILD_TESTS=OFF \
  -DLLAMA_BUILD_SERVER=OFF

cmake --build "${BUILD_DIR}" --config Release -j "$(sysctl -n hw.ncpu)" \
  --target llama-bench llama-cli

echo "Built: ${BUILD_DIR}/bin/llama-bench"
```

- [ ] **Step 3: Make it executable and run it**

```bash
chmod +x SnailsNative/vendor/build-llama-android.sh
export ANDROID_NDK=$ANDROID_HOME/ndk/27.2.12479018
./SnailsNative/vendor/build-llama-android.sh
```

Expected: ends with `Built: .../build-android/bin/llama-bench`

- [ ] **Step 4: Verify the binary is actually arm64 Android, not macOS**

Run: `file SnailsNative/vendor/build-android/bin/llama-bench`
Expected: `ELF 64-bit LSB pie executable, ARM aarch64`

A macOS Mach-O here means the toolchain file was not picked up — the build succeeded and produced the wrong thing, which is exactly the kind of silent success this project keeps getting caught by. Do not proceed past this step until it reads `ARM aarch64`.

- [ ] **Step 5: Keep build output out of git**

Append to `.gitignore`:

```
SnailsNative/vendor/build-android/
```

- [ ] **Step 6: Commit**

```bash
git add SnailsNative/vendor/build-llama-android.sh .gitignore
git commit -m "build: llama.cpp for arm64 android at the pinned sha"
```

---

### Task 3: Measure throughput on the phone

**This is the decision gate.** Every later plan is shaped by the number this produces.

**Files:**
- Create: `docs/superpowers/specs/2026-08-11-android-throughput-results.md`

**Interfaces:**
- Consumes: `llama-bench` from Task 2.
- Produces: a recorded tokens/sec figure and a go / degrade / stop verdict.

- [ ] **Step 1: Put the phone in debug mode and connect it**

On the phone: Settings → About phone → tap Build number seven times → back → System → Developer options → enable USB debugging. Connect by USB and accept the RSA fingerprint prompt on the phone's screen.

Run: `adb devices`
Expected: one line with a serial and the word `device`. If it says `unauthorized`, the on-screen prompt has not been accepted. If the list is empty, the cable is charge-only — a surprising number are.

- [ ] **Step 2: Record what the phone actually is**

```bash
adb shell getprop ro.product.model
adb shell getprop ro.soc.model
adb shell "cat /proc/meminfo | head -1"
adb shell "cat /proc/cpuinfo | grep -c processor"
```

Keep this output; it goes in the results doc.

Note `MemTotal` here — it is the number Android reports, and it will be visibly *below* the phone's advertised RAM. That gap is the trap the spec describes for `Device.marginal`.

- [ ] **Step 3: Download the 1B model and verify it**

```bash
mkdir -p /tmp/lucy-android
curl -L -o /tmp/lucy-android/gemma-3-1b-it-Q4_K_M.gguf \
  https://storage.googleapis.com/lucy-snails-releases/models/gemma-3-1b-it-Q4_K_M.gguf
shasum -a 256 /tmp/lucy-android/gemma-3-1b-it-Q4_K_M.gguf
```

Expected: `8ccc5cd1f1b3602548715ae25a66ed73fd5dc68a210412eea643eb20eb75a135`

- [ ] **Step 4: Push the binary and the model**

`/data/local/tmp` is the only location that is both writable by adb and not mounted `noexec`. Pushing to `/sdcard` produces "Permission denied" on exec and looks like a permissions bug.

```bash
adb shell mkdir -p /data/local/tmp/lucy
adb push SnailsNative/vendor/build-android/bin/llama-bench /data/local/tmp/lucy/
adb push /tmp/lucy-android/gemma-3-1b-it-Q4_K_M.gguf /data/local/tmp/lucy/
adb shell chmod +x /data/local/tmp/lucy/llama-bench
```

The model push is 806 MB over USB and takes a few minutes.

- [ ] **Step 5: Measure**

```bash
adb shell "cd /data/local/tmp/lucy && ./llama-bench -m gemma-3-1b-it-Q4_K_M.gguf -p 512 -n 128 -t 4"
```

Expected: a table with `pp512` and `tg128` rows, each with a `t/s` column.

`tg128` is the number that matters — token *generation*, which is what a person waits on while Lucy answers. `pp512` is prompt processing, which affects how long she pauses before starting.

- [ ] **Step 6: Repeat with different thread counts**

Big.LITTLE scheduling means more threads is not always faster, and the default is frequently wrong.

```bash
for t in 2 4 6 8; do
  echo "--- threads=$t ---"
  adb shell "cd /data/local/tmp/lucy && ./llama-bench -m gemma-3-1b-it-Q4_K_M.gguf -p 512 -n 128 -t $t"
done
```

- [ ] **Step 7: Write the results doc**

Create `docs/superpowers/specs/2026-08-11-android-throughput-results.md` with the phone's model, SoC, `MemTotal`, core count, and the full `tg128` / `pp512` table across thread counts. Then state the verdict against these thresholds:

| `tg128` | Verdict |
|---|---|
| **≥ 15 t/s** | Go. Matches the iPhone's feel. The design proceeds unchanged. |
| **8–15 t/s** | Go, with streaming mandatory and answers kept short. `LucyVoice` may need a tighter fact budget so prompts stay small. |
| **3–8 t/s** | Degrade. Usable only with visible streaming and an expectation reset; revisit whether Ask-only for this burn is the honest scope after all. |
| **< 3 t/s** | Stop and redesign. A question would take over a minute. Reopen the camp-wifi option from brainstorming. |

Record the verdict explicitly. A number in a table that nobody converted into a decision is how this gets rationalised later.

- [ ] **Step 8: Commit**

```bash
git add docs/superpowers/specs/2026-08-11-android-throughput-results.md
git commit -m "docs: measured llama.cpp throughput on the android test phone"
```

---

### Task 4: Gradle skeleton with a JVM-only core module

**Files:**
- Create: `android/settings.gradle.kts`, `android/build.gradle.kts`, `android/gradle.properties`, `android/core/build.gradle.kts`
- Create: `android/core/src/test/kotlin/camp/lucy/core/ModuleIsolationTest.kt`

**Interfaces:**
- Consumes: JDK 17 and Gradle from Task 1.
- Produces: `gradle :core:test` runs JUnit 5 against `camp.lucy.core`.

- [ ] **Step 1: Write the failing test**

This test is the enforcement mechanism for the design's load-bearing rule. It fails if anyone makes `core/` an Android module, because `android.os.Build` would then resolve.

Create `android/core/src/test/kotlin/camp/lucy/core/ModuleIsolationTest.kt`:

```kotlin
package camp.lucy.core

import kotlin.test.Test
import kotlin.test.assertFailsWith

class ModuleIsolationTest {
    /**
     * core/ must never gain an Android dependency. Retrieval is the layer the
     * invariant lives in, and it has to be testable on the desktop with no
     * emulator. If someone converts this module to com.android.library to make
     * one import convenient, this test is what tells them.
     */
    @Test
    fun `android classes are not on the core classpath`() {
        assertFailsWith<ClassNotFoundException> {
            Class.forName("android.os.Build")
        }
    }
}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd android && gradle :core:test`
Expected: FAIL — `Project 'core' not found` or `settings file not found`. Nothing is wired up yet.

- [ ] **Step 3: Write the Gradle files**

Create `android/settings.gradle.kts`:

```kotlin
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "lucy-android"
include(":core")
```

Create `android/build.gradle.kts`:

```kotlin
plugins {
    kotlin("jvm") version "2.0.21" apply false
}
```

Create `android/gradle.properties`:

```properties
org.gradle.jvmargs=-Xmx2g
kotlin.code.style=official
```

Create `android/core/build.gradle.kts`:

```kotlin
// kotlin("jvm"), NOT com.android.library. This is deliberate and load-bearing:
// it makes an accidental Android dependency a build failure rather than a
// review catch, and it keeps this module runnable on the desktop JVM so
// retrieval tests need no emulator.
plugins {
    kotlin("jvm") version "2.0.21"
}

kotlin {
    jvmToolchain(17)
}

dependencies {
    testImplementation(kotlin("test"))
}

tasks.test {
    useJUnitPlatform()
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd android && gradle :core:test`
Expected: `BUILD SUCCESSFUL`, 1 test passed

- [ ] **Step 5: Generate the Gradle wrapper so the version is pinned**

```bash
cd android && gradle wrapper --gradle-version 8.10.2
```

- [ ] **Step 6: Verify the wrapper works**

Run: `cd android && ./gradlew :core:test`
Expected: `BUILD SUCCESSFUL`, 1 test passed

- [ ] **Step 7: Ignore Gradle build output**

Append to `.gitignore`:

```
android/.gradle/
android/build/
android/*/build/
android/local.properties
```

- [ ] **Step 8: Commit**

```bash
git add android/settings.gradle.kts android/build.gradle.kts \
  android/gradle.properties android/core/build.gradle.kts \
  android/core/src/test/kotlin/camp/lucy/core/ModuleIsolationTest.kt \
  android/gradlew android/gradlew.bat android/gradle/wrapper .gitignore
git commit -m "build: gradle skeleton with a jvm-only core module"
```

---

### Task 5: Port `containsWord`

The word-boundary check. This is the function that stopped "water" matching "floodwaters" and pulling a Noah's Ark party into an answer about drinking water.

**Files:**
- Create: `android/core/src/main/kotlin/camp/lucy/core/Retrieval.kt`
- Create: `android/core/src/test/kotlin/camp/lucy/core/ContainsWordTest.kt`

**Interfaces:**
- Consumes: the Gradle module from Task 4.
- Produces: `fun containsWord(haystack: String, word: String): Boolean` in `camp.lucy.core`.

- [ ] **Step 1: Write the failing test**

Create `android/core/src/test/kotlin/camp/lucy/core/ContainsWordTest.kt`:

```kotlin
package camp.lucy.core

import kotlin.test.Test
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class ContainsWordTest {
    @Test
    fun `matches a standalone word`() {
        assertTrue(containsWord("we need more water", "water"))
    }

    @Test
    fun `matches at the start`() {
        assertTrue(containsWord("water is low", "water"))
    }

    @Test
    fun `matches at the end`() {
        assertTrue(containsWord("bring water", "water"))
    }

    @Test
    fun `does not match inside a longer word`() {
        assertFalse(containsWord("the floodwaters rose", "water"))
    }

    @Test
    fun `punctuation counts as a boundary`() {
        assertTrue(containsWord("water, and ice", "water"))
    }

    @Test
    fun `digits do not count as a boundary`() {
        assertFalse(containsWord("water2 tank", "water"))
    }

    @Test
    fun `absent word is false`() {
        assertFalse(containsWord("bring ice", "water"))
    }

    /**
     * Documents a real limitation carried over deliberately from the Swift.
     *
     * Swift's `range(of:)` returns only the FIRST occurrence, and that is the
     * only one the boundary check ever sees. So a fact mentioning both
     * "floodwaters" and "water" fails to match "water" — the standalone use
     * later in the string is never reached.
     *
     * Kotlin replicates this rather than fixing it, because a fix in one
     * client only is precisely the silent divergence the two-client design is
     * built to prevent. See open question 1 in the plan.
     */
    @Test
    fun `only the first occurrence is checked - known limitation`() {
        assertFalse(containsWord("floodwaters and water", "water"))
    }
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd android && ./gradlew :core:test --tests '*ContainsWordTest*'`
Expected: FAIL — `Unresolved reference: containsWord`

- [ ] **Step 3: Write the implementation**

Create `android/core/src/main/kotlin/camp/lucy/core/Retrieval.kt`:

```kotlin
package camp.lucy.core

/**
 * Whole-word containment.
 *
 * Substring matching made "water" match "floodwaters", which pulled a Noah's
 * Ark party into an answer about drinking water — the same bug the Python
 * pipeline had, where the alias "PS" matched 2,953 messages through "perhaps"
 * and "apps".
 *
 * Only the first occurrence is examined, matching the Swift exactly. See
 * ContainsWordTest for why that is deliberate.
 */
fun containsWord(haystack: String, word: String): Boolean {
    val start = haystack.indexOf(word)
    if (start < 0) return false

    val before = if (start == 0) null else haystack[start - 1]
    val afterIndex = start + word.length
    val after = if (afterIndex >= haystack.length) null else haystack[afterIndex]

    fun boundary(c: Char?): Boolean = c == null || (!c.isLetter() && !c.isDigit())

    return boundary(before) && boundary(after)
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd android && ./gradlew :core:test --tests '*ContainsWordTest*'`
Expected: `BUILD SUCCESSFUL`, 8 tests passed

- [ ] **Step 5: Commit**

```bash
git add android/core/src/main/kotlin/camp/lucy/core/Retrieval.kt \
  android/core/src/test/kotlin/camp/lucy/core/ContainsWordTest.kt
git commit -m "feat(android): port containsWord with its boundary cases"
```

---

### Task 6: Port `terms` and the stopword list

**Files:**
- Modify: `android/core/src/main/kotlin/camp/lucy/core/Retrieval.kt`
- Create: `android/core/src/test/kotlin/camp/lucy/core/TermsTest.kt`

**Interfaces:**
- Consumes: `Retrieval.kt` from Task 5.
- Produces: `object Retrieval { fun terms(query: String): List<String> }`.

- [ ] **Step 1: Write the failing test**

Create `android/core/src/test/kotlin/camp/lucy/core/TermsTest.kt`:

```kotlin
package camp.lucy.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class TermsTest {
    @Test
    fun `strips question words and short tokens`() {
        assertEquals(listOf("barrels"), Retrieval.terms("where are the barrels"))
    }

    @Test
    fun `lowercases`() {
        assertEquals(listOf("doris"), Retrieval.terms("Where is DORIS"))
    }

    @Test
    fun `splits on punctuation`() {
        assertEquals(listOf("socket", "lag", "bolts"),
            Retrieval.terms("what socket, for lag-bolts?"))
    }

    @Test
    fun `drops tokens of two characters or fewer`() {
        // The filter is length > 2, so three-letter words like "axe" and "lag"
        // survive. Only one- and two-character tokens are dropped.
        assertTrue(Retrieval.terms("who has an ox").isEmpty())
        assertEquals(listOf("axe"), Retrieval.terms("who has an axe"))
    }

    @Test
    fun `drops her own name`() {
        assertEquals(listOf("shower"), Retrieval.terms("lucy where is the shower"))
    }

    @Test
    fun `empty query gives no terms`() {
        assertTrue(Retrieval.terms("").isEmpty())
    }
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd android && ./gradlew :core:test --tests '*TermsTest*'`
Expected: FAIL — `Unresolved reference: Retrieval`

- [ ] **Step 3: Add the implementation**

Append to `android/core/src/main/kotlin/camp/lucy/core/Retrieval.kt`:

```kotlin
object Retrieval {
    /**
     * Words that appear in nearly every question and so tell us nothing about
     * which fact is wanted.
     */
    private val stop = setOf(
        "what", "whats", "who", "whos", "where", "wheres", "when", "whens",
        "why", "how", "is", "are", "was", "were", "the", "a", "an", "of", "in",
        "on", "at", "to", "for", "do", "does", "did", "i", "we", "you", "my",
        "our", "it", "its", "and", "or", "with", "about", "tell", "me", "get",
        "got", "need", "any", "some", "there", "have", "has", "can", "should",
        "know", "lucy",
    )

    fun terms(query: String): List<String> =
        query.lowercase()
            .split(Regex("[^a-z0-9]+"))
            .filter { it.length > 2 && it !in stop }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd android && ./gradlew :core:test --tests '*TermsTest*'`
Expected: `BUILD SUCCESSFUL`, 6 tests passed

- [ ] **Step 5: Commit**

```bash
git add android/core/src/main/kotlin/camp/lucy/core/Retrieval.kt \
  android/core/src/test/kotlin/camp/lucy/core/TermsTest.kt
git commit -m "feat(android): port term extraction and the stopword list"
```

---

### Task 7: Entity model and a fixture-backed source

Swift's `Retrieval.search` takes a concrete `EntityStore` that owns a SQLite handle. Kotlin introduces an interface instead, so `core/` stays testable with no database and no device. This is the one deliberate structural deviation from the Swift in this plan.

**Files:**
- Create: `android/core/src/main/kotlin/camp/lucy/core/Entities.kt`
- Create: `android/core/src/test/kotlin/camp/lucy/core/FakeEntitySource.kt`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `data class Entity(id: String, name: String, aliases: List<String>)`, `data class EntityFact(fact: String, assertedOn: String?)`, `interface EntitySource { fun allEntities(): List<Entity>; fun facts(entityId: String): List<EntityFact> }`, and `FakeEntitySource` for tests.

- [ ] **Step 1: Write the model and the interface**

Create `android/core/src/main/kotlin/camp/lucy/core/Entities.kt`:

```kotlin
package camp.lucy.core

data class Entity(
    val id: String,
    val name: String,
    val aliases: List<String> = emptyList(),
)

data class EntityFact(
    val fact: String,
    val assertedOn: String? = null,
)

/**
 * Where entities come from. On the phone this is backed by the read-only
 * SQLite knowledge database; in tests it is a list in memory.
 *
 * Swift passes a concrete EntityStore here. Kotlin uses an interface so that
 * core/ can be exercised on the desktop JVM without a database file, which is
 * the whole point of core/ being a plain JVM module.
 */
interface EntitySource {
    fun allEntities(): List<Entity>
    fun facts(entityId: String): List<EntityFact>
}
```

- [ ] **Step 2: Write the test fixture**

Create `android/core/src/test/kotlin/camp/lucy/core/FakeEntitySource.kt`:

```kotlin
package camp.lucy.core

class FakeEntitySource(
    private val entities: List<Entity>,
    private val factsByEntity: Map<String, List<EntityFact>>,
) : EntitySource {
    override fun allEntities(): List<Entity> = entities
    override fun facts(entityId: String): List<EntityFact> =
        factsByEntity[entityId] ?: emptyList()

    companion object {
        /** A small stand-in for the camp's knowledge, used across tests. */
        fun camp(): FakeEntitySource = FakeEntitySource(
            entities = listOf(
                Entity("doris", "doris", listOf("truck")),
                Entity("shower", "shower"),
            ),
            factsByEntity = mapOf(
                "doris" to listOf(
                    EntityFact("the water barrels are stored in doris", "2026-07-01"),
                    EntityFact("doris needs a 15mm socket for the lag bolts", "2026-07-02"),
                    EntityFact("doris was repainted before the burn", "2026-06-01"),
                ),
                "shower" to listOf(
                    EntityFact("the shower uses grey water tanks", "2026-07-03"),
                ),
            ),
        )
    }
}
```

- [ ] **Step 3: Verify the module still compiles and all tests pass**

Run: `cd android && ./gradlew :core:test`
Expected: `BUILD SUCCESSFUL`, 15 tests passed

- [ ] **Step 4: Commit**

```bash
git add android/core/src/main/kotlin/camp/lucy/core/Entities.kt \
  android/core/src/test/kotlin/camp/lucy/core/FakeEntitySource.kt
git commit -m "feat(android): entity model and a fixture-backed source"
```

---

### Task 8: Port `Retrieval.search`

The scoring. This is the layer the invariant lives in.

**Files:**
- Modify: `android/core/src/main/kotlin/camp/lucy/core/Retrieval.kt`
- Create: `android/core/src/test/kotlin/camp/lucy/core/SearchTest.kt`

**Interfaces:**
- Consumes: `EntitySource`, `Entity`, `EntityFact` from Task 7; `containsWord` and `Retrieval.terms` from Tasks 5–6.
- Produces: `data class Hit(entity: Entity, facts: List<EntityFact>, score: Double)` and `Retrieval.search(query: String, source: EntitySource, limit: Int = 3): List<Hit>`.

- [ ] **Step 1: Write the failing test**

Create `android/core/src/test/kotlin/camp/lucy/core/SearchTest.kt`:

```kotlin
package camp.lucy.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertTrue

class SearchTest {
    private val camp = FakeEntitySource.camp()

    @Test
    fun `naming an entity finds it`() {
        val hits = Retrieval.search("where is doris", camp)
        assertEquals("doris", hits.first().entity.id)
    }

    @Test
    fun `an alias finds the entity`() {
        val hits = Retrieval.search("where is the truck", camp)
        assertEquals("doris", hits.first().entity.id)
    }

    @Test
    fun `a question about a fact finds the entity holding it`() {
        val hits = Retrieval.search("what socket for the lag bolts", camp)
        assertEquals("doris", hits.first().entity.id)
    }

    /**
     * Asking about ladders must not return everything known about Doris.
     * Only facts that actually matched come back.
     */
    @Test
    fun `returns only the facts that matched`() {
        val hits = Retrieval.search("what socket for the lag bolts", camp)
        assertEquals(1, hits.first().facts.size)
        assertTrue(hits.first().facts.first().fact.contains("15mm socket"))
    }

    /**
     * The entity's own name appears in most of its own facts, so it ranks
     * everything equally and the reply pads out with whatever happened to be
     * first. Only the rest of the question picks facts.
     */
    @Test
    fun `naming alone falls back to the most recent facts`() {
        val hits = Retrieval.search("doris", camp)
        assertEquals("2026-07-02", hits.first().facts.first().assertedOn)
    }

    @Test
    fun `two matching terms in one fact outrank one term in two facts`() {
        val hits = Retrieval.search("water barrels", camp)
        assertEquals("doris", hits.first().entity.id)
    }

    @Test
    fun `an unmatched question returns nothing`() {
        assertTrue(Retrieval.search("where are the generators", camp).isEmpty())
    }

    @Test
    fun `a question with only stopwords returns nothing`() {
        assertTrue(Retrieval.search("what is it", camp).isEmpty())
    }

    @Test
    fun `limit is respected`() {
        val hits = Retrieval.search("water doris shower", camp, limit = 1)
        assertEquals(1, hits.size)
    }
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd android && ./gradlew :core:test --tests '*SearchTest*'`
Expected: FAIL — `Unresolved reference: search`

- [ ] **Step 3: Write the implementation**

Add to `android/core/src/main/kotlin/camp/lucy/core/Retrieval.kt`, inside `object Retrieval`:

```kotlin
    /**
     * Ranks entities by how well the question matches their name, aliases and
     * facts, then returns only the facts that actually matched — asking about
     * ladders should not return everything known about Doris.
     */
    fun search(query: String, source: EntitySource, limit: Int = 3): List<Hit> {
        val terms = terms(query)
        if (terms.isEmpty()) return emptyList()

        val hits = mutableListOf<Hit>()
        for (entity in source.allEntities()) {
            val name = entity.name.lowercase()
            val aliases = entity.aliases.map { it.lowercase() }
            var score = 0.0

            // A question that names a thing is almost always about that thing.
            for (term in terms) {
                if (name == term) score += 12 else if (name.contains(term)) score += 6
                if (aliases.contains(term)) score += 8
            }

            // The entity's own name appears in most of its own facts, so it
            // ranks everything equally and the reply pads out with whatever
            // happened to be first. Only the rest of the question picks facts.
            val naming = (listOf(name) + aliases).toSet()
            val distinguishing = terms.filter { term -> naming.none { it.contains(term) } }

            val facts = source.facts(entity.id)
            val matched = mutableListOf<Pair<EntityFact, Double>>()
            for (fact in facts) {
                val text = fact.fact.lowercase()
                val overlap = distinguishing.count { containsWord(text, it) }
                if (overlap > 0) {
                    // Two matching terms in one fact is a far better signal
                    // than one term matching in two facts.
                    val factScore = (overlap * overlap).toDouble()
                    matched += fact to factScore
                    score += factScore
                }
            }

            if (score <= 0) continue

            val ordered: List<EntityFact> = if (matched.isEmpty()) {
                facts.sortedByDescending { it.assertedOn ?: "" }.take(4)
            } else {
                matched.sortedByDescending { it.second }.map { it.first }
            }

            hits += Hit(entity, ordered.take(5), score)
        }

        return hits.sortedByDescending { it.score }.take(limit)
    }
```

And at the top level of the file, beside `containsWord`:

```kotlin
data class Hit(
    val entity: Entity,
    val facts: List<EntityFact>,
    /** Higher is better. Only meaningful for ranking within one query. */
    val score: Double,
)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd android && ./gradlew :core:test --tests '*SearchTest*'`
Expected: `BUILD SUCCESSFUL`, 9 tests passed

- [ ] **Step 5: Run the whole core suite**

Run: `cd android && ./gradlew :core:test`
Expected: `BUILD SUCCESSFUL`, 24 tests passed

- [ ] **Step 6: Commit**

```bash
git add android/core/src/main/kotlin/camp/lucy/core/Retrieval.kt \
  android/core/src/test/kotlin/camp/lucy/core/SearchTest.kt
git commit -m "feat(android): port entity search and fact scoring"
```

---

## What this plan does not cover

Deliberately out of scope, to be planned once Task 3 produces a number:

- The JNI bridge and `LlamaHandle` equivalent
- The Compose UI and the 21 screens
- `LucyVoice` and `LucyBrain`, including the 1B prompt rewrite
- The SQLite-backed `EntitySource`, and the first-run copy out of `assets/`
- `PeopleStore`, `ChatLog`, capture, speech, sync
- The measured device gate
- Cross-client parity fixtures — these need both implementations complete enough to compare, and they need the real knowledge DB, which is not in this checkout

## Open questions

1. **`containsWord` only checks the first occurrence.** Task 5 replicates this
   faithfully and documents it. It is a real miss — a fact mentioning both
   "floodwaters" and "water" will not match "water". Fixing it in Kotlin alone
   would create exactly the divergence this architecture exists to prevent, so
   the fix belongs in both clients or neither. Marcus's call, and it is cheap:
   iterate occurrences instead of taking the first.

2. **The knowledge DB is not in this checkout**, so no task here opens a real
   database. The `EntitySource` seam means that is not blocking, but the
   SQLite implementation cannot be written or tested until the pipeline is run.
