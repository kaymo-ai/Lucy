import Foundation

// Which build is this, answerable from inside the app.
//
// Two apps named Lucy live on the dev phone — the cable build and the
// TestFlight build — and the cable one is replaced ten times an afternoon.
// "Is this the build with X" kept being settled by re-installing, which is
// the same class of question as "which brain is loaded": the app should
// simply say. The commit and build time are stamped into the built
// product's Info.plist by a post-build script in project.yml; nothing is
// generated into the source tree and a stale checkout cannot lie about it.
//
// The database gets its own line because it changes on a different schedule
// from the code: two identical binaries can carry different knowledge, and
// the row counts are the cheapest honest fingerprint of which one shipped.

enum BuildInfo {
    private static var info: [String: Any] { Bundle.main.infoDictionary ?? [:] }

    /// One line that identifies the build: "dev · a1b2c3d+ · 11 Aug 15:04",
    /// or "TestFlight 1.0 (142) · a1b2c3d · 11 Aug". The two flavors carry
    /// different identities on purpose — a dev build's version is always
    /// "1.0 (1)", so printing it says nothing, while its commit and time
    /// change ten times an afternoon; TestFlight is the reverse. The bundle
    /// id is the flavor test because it is the one thing the two apps can
    /// never share. A trailing + on the commit means uncommitted changes
    /// were in the tree. Builds from before the stamp existed fall back to
    /// the binary's own file date, marked as such rather than passed off
    /// as a stamp.
    static var stamp: String {
        let flavor: String
        if (Bundle.main.bundleIdentifier ?? "").hasSuffix(".dev") {
            flavor = "dev"
        } else {
            let version = info["CFBundleShortVersionString"] as? String ?? "?"
            let build = info["CFBundleVersion"] as? String ?? "?"
            flavor = "TestFlight \(version) (\(build))"
        }
        if let commit = info["LucyBuildCommit"] as? String,
           let when = info["LucyBuildDate"] as? String {
            return "\(flavor) · \(commit) · \(when)"
        }
        guard let exe = Bundle.main.executableURL,
              let date = (try? FileManager.default
                  .attributesOfItem(atPath: exe.path))?[.modificationDate] as? Date
        else { return "\(flavor) · unstamped" }
        let f = DateFormatter()
        f.dateFormat = "d MMM HH:mm"
        return "\(flavor) · binary of \(f.string(from: date))"
    }

    /// "1,745 facts · 163 stories · 6,517 ask words" — the database's
    /// fingerprint. Counted once per launch; three COUNT(*)s on an indexed
    /// SQLite file are not worth caching harder than that.
    static let knowledge: String = {
        guard let store = EntityStore(path: PreviewRoot.fixturePath) else {
            return "no database"
        }
        let n = NumberFormatter()
        n.numberStyle = .decimal
        func c(_ table: String) -> String {
            n.string(from: NSNumber(value: store.rowCount(table))) ?? "0"
        }
        return "\(c("camp_fact")) facts · \(c("lore")) stories · "
            + "\(c("ask_word")) ask words"
    }()
}
