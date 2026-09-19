import SwiftUI

// The camp as a list of people, each expanding to what we know about them.
//
// Two things this deliberately does not do. It does not pad: a person with a
// roster row and nothing else shows a roster row and nothing else, because a
// card that always fills the same space teaches you to stop reading it. And
// it does not present the portrait as fact — it is drawn from the group chat,
// it says so, and the messages it was drawn from are one tap away.

struct PeopleView: View {
    let face: Face
    let store: EntityStore

    /// Opens the first row on appear, so a screenshot can show the expanded
    /// state — otherwise it is only reachable by tapping.
    var expandsFirst = false
    /// Pre-fills the search box, so a screenshot can land on one person.
    var initialQuery = ""

    @State private var people: [PersonCard] = []
    @State private var query = ""
    /// One at a time. With several cards open the list becomes a wall of
    /// prose you have to scroll past to reach the next name, and opening
    /// someone is nearly always a way of asking "who is this" about one
    /// person.
    @State private var expanded: Int64?
    /// The keyboard covers the tab bar, so without a way to put it away there
    /// is no way back to the chat. Every plausible gesture dismisses it:
    /// scrolling the list, tapping a row, and Return.
    @FocusState private var searching: Bool

    /// The most recent year anybody has said yes to, taken from the roster
    /// rather than from the clock. It is 2026 today and it will be 2027 when
    /// next year's sign-ups land, with nothing to remember to change.
    private var currentYear: String {
        people
            .flatMap { $0.yearsAttended.split(separator: ",") }
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { $0.count == 4 }
            .max() ?? ""
    }

    private func isComing(_ person: PersonCard) -> Bool {
        guard !currentYear.isEmpty else { return false }
        return person.yearsAttended
            .split(separator: ",")
            .contains { $0.trimmingCharacters(in: .whitespaces) == currentYear }
    }

    private var filtered: [PersonCard] {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        guard !q.isEmpty else { return people }
        return people.filter {
            $0.name.lowercased().contains(q)
                || $0.nickname.lowercased().contains(q)
                || $0.knownFor.lowercased().contains(q)
                || $0.caresAbout.lowercased().contains(q)
                || $0.expertise.contains { t in t.lowercased().contains(q) }
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            searchField
            Rectangle().fill(face.hairline).frame(height: 1)

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    // Who is actually here, first. Three hundred names sorted
                    // alphabetically buries the seventy you might run into
                    // this week among ten years of everyone who ever came.
                    let coming = filtered.filter(isComing)
                    let rest = filtered.filter { !isComing($0) }

                    if !coming.isEmpty && !rest.isEmpty {
                        sectionHeader("Here this year", count: coming.count)
                    }
                    ForEach(coming) { person in
                        row(person)
                    }
                    if !rest.isEmpty && !coming.isEmpty {
                        sectionHeader("Camped before", count: rest.count)
                    }
                    ForEach(rest) { person in
                        row(person)
                    }
                    if filtered.isEmpty {
                        Text(Style.current.isPlayful
                             ? (people.isEmpty ? "No roster on board yet."
                                               : "Nobody by that name.")
                             : (people.isEmpty ? "NO ROSTER LOADED" : "NOBODY BY THAT NAME"))
                            .font(.eyebrow(13))
                            .tracking(Style.current.eyebrowTracking)
                            .foregroundStyle(face.muted)
                            .frame(maxWidth: .infinity)
                            .padding(.top, 40)
                    }
                }
                .padding(.bottom, 40)
            }
            .scrollDismissesKeyboard(.immediately)
        }
        .background(face.ground)
        // Deliberately no container-wide tap gesture to dismiss: it competes
        // with the row buttons for the same tap. Scrolling, Return, Done and
        // opening a row are enough, and none of them can break the list.
        .preferredColorScheme(face.night ? .dark : .light)
        .task {
            if people.isEmpty { people = store.allPeople() }
            if query.isEmpty { query = initialQuery }
            if expandsFirst, let first = filtered.first { expanded = first.id }
        }
    }

    @ViewBuilder
    private func row(_ person: PersonCard) -> some View {
        PersonRow(face: face, person: person,
                  isExpanded: expanded == person.id,
                  evidence: expanded == person.id ? evidence(for: person) : [],
                  onTap: { toggle(person) })
        Rectangle().fill(face.hairline).frame(height: 1)
            .padding(.leading, 22)
    }

    private func sectionHeader(_ title: String, count: Int) -> some View {
        HStack {
            Text(Style.current.label(title))
                .font(.eyebrow(11))
                .tracking(Style.current.eyebrowTracking)
                .foregroundStyle(face.accent)
            Spacer()
            Text("\(count)")
                .font(.data(12))
                .foregroundStyle(face.muted)
        }
        .padding(.horizontal, 22)
        .padding(.top, 22).padding(.bottom, 9)
    }

    private func toggle(_ person: PersonCard) {
        searching = false
        Haptics.tap()
        withAnimation(.snappy(duration: 0.22)) {
            expanded = expanded == person.id ? nil : person.id
        }
    }

    private func evidence(for person: PersonCard) -> [String] {
        guard let pid = person.personID, person.hasPortrait else { return [] }
        return store.portraitEvidence(personID: pid)
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(Style.current.label("Snails"))
                .font(.display(34))
                .foregroundStyle(face.text)
            Spacer()
            Text("\(people.count)")
                .font(.data(13))
                .foregroundStyle(face.muted)
            LucyMenu(face: face)
        }
        .padding(.horizontal, 22)
        .padding(.top, 8)
        .padding(.bottom, 10)
    }

    /// It was an icon and a placeholder floating on the ground, which is not
    /// obviously a thing you can type into. A filled capsule with an edge says
    /// "field" before anybody has to work it out, and the edge lights up in
    /// the accent while it has focus.
    private var searchField: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(face.muted)
            TextField("", text: $query, prompt:
                Text("name, or what they're known for")
                    .foregroundStyle(face.muted))
                .font(.body_(15))
                .foregroundStyle(face.text)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                .focused($searching)
                .submitLabel(.done)
                .onSubmit { searching = false }
            if !query.isEmpty {
                Button {
                    query = ""
                    searching = false
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 14))
                        .foregroundStyle(face.muted)
                }
            }
            // A visible way out, because the keyboard hides the tab bar and
            // "tap somewhere else" is not discoverable when there is nothing
            // else on screen.
            if searching {
                Button("Done") { searching = false }
                    .font(.eyebrow(13))
                    .tracking(Style.current.eyebrowTracking)
                    .foregroundStyle(face.accent)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 11)
        .background(
            RoundedRectangle(cornerRadius: 12)
                .fill(face.raised)
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .stroke(searching ? face.accent : face.muted.opacity(0.35),
                                lineWidth: searching ? 1.5 : 1)
                )
        )
        .animation(.easeOut(duration: 0.15), value: searching)
        .padding(.horizontal, 22)
        .padding(.bottom, 14)
    }
}

// MARK: - One person

private struct PersonRow: View {
    let face: Face
    let person: PersonCard
    let isExpanded: Bool
    let evidence: [String]
    let onTap: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button(action: onTap) {
                HStack(alignment: .top, spacing: 12) {
                    // Their colour, derived from their name and therefore the
                    // same on every phone. Three hundred identical grey rows
                    // is a spreadsheet; a camp is not.
                    Monogram(face: face, name: person.name)
                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 6) {
                            Text(person.name)
                                .font(.body_(19))
                                .foregroundStyle(face.text)
                            if !person.nickname.isEmpty {
                                Text("“\(person.nickname)”")
                                    .font(.data(12))
                                    .foregroundStyle(face.accent)
                            }
                        }
                        Text(person.subtitle)
                            // Was 12pt monospace on one clipped line, which
                            // is how "Managing vehicle logistics for ret…"
                            // told you nothing. Bigger, and given the second
                            // line it needed: a subtitle that stops mid-word
                            // is decoration, not information.
                            .font(.body_(14.5))
                            .foregroundStyle(face.muted)
                            .lineLimit(2)
                    }
                    Spacer(minLength: 8)
                    Image(systemName: "chevron.down")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(face.muted)
                        .rotationEffect(.degrees(isExpanded ? 0 : -90))
                        .padding(.top, 4)
                }
                .padding(.horizontal, 22)
                .padding(.vertical, 13)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if isExpanded { detail }
        }
    }

    private var detail: some View {
        VStack(alignment: .leading, spacing: 14) {
            if person.hasPortrait {
                // Labelled, always. This is a reading of a group chat, not a
                // record of who someone is, and the label is what keeps the
                // difference visible.
                block("HOW THEY COME ACROSS", person.summary)
                if !person.signatureQuote.isEmpty {
                    Text("“\(person.signatureQuote)”")
                        .font(.body_(15))
                        .italic()
                        .foregroundStyle(face.text)
                        .padding(.leading, 10)
                        .overlay(alignment: .leading) {
                            Rectangle().fill(face.accent).frame(width: 2)
                        }
                }
                if !person.voice.isEmpty { line("VOICE", person.voice) }
                if !person.caresAbout.isEmpty { line("CARES ABOUT", person.caresAbout) }
                if !person.showsUpAs.isEmpty { line("ON A BUILD", person.showsUpAs) }
            }

            if !person.roleSummary.isEmpty {
                block("WHAT THEY DO", person.roleSummary)
            }
            if !person.expertise.isEmpty {
                line("KNOWS ABOUT", person.expertise.prefix(6).joined(separator: " · "))
            }

            if person.hasRoster {
                if !person.yearsAttended.isEmpty {
                    line("SAID YES IN", PersonCard.spaced(person.yearsAttended))
                } else {
                    line("On the sheet for", PersonCard.spaced(person.yearsListed))
                }
                if !person.homeCity.isEmpty { line("Base", person.homeCity) }
                if !person.email.isEmpty { line("Email", person.email) }
                if !person.phone.isEmpty { line("Phone", person.phone) }
            }

            if !evidence.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text(Style.current.label("Drawn from their own messages"))
                        .font(.eyebrow(10))
                        .tracking(Style.current.eyebrowTracking)
                        .foregroundStyle(face.muted)
                    ForEach(Array(evidence.enumerated()), id: \.offset) { _, quote in
                        Text("“\(quote)”")
                            .font(.data(12))
                            .foregroundStyle(face.muted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }

            // Nothing is written when there is nothing to say. An apology for
            // an empty record is still a line to read, and a card that always
            // fills the same space teaches you to stop reading it.
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 22)
        .padding(.bottom, 18)
    }

    private func block(_ label: String, _ text: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.eyebrow(10))
                .tracking(Style.current.eyebrowTracking)
                .foregroundStyle(face.muted)
            Text(text)
                .font(.body_(15))
                .foregroundStyle(face.text)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func line(_ label: String, _ text: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(Style.current.label(label))
                .font(.eyebrow(10))
                .tracking(Style.current.eyebrowTracking)
                .foregroundStyle(face.muted)
            Text(text)
                .font(.data(13))
                .foregroundStyle(face.text)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
