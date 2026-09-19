import SwiftUI

// The ASK answer is an entity RECORD, not a list of chat messages that
// happened to embed near the query. Asking "what is Doris" returns what it is,
// how to get into it, what's in it, who handles it, and when each of those was
// last confirmed.

struct EntityRecordView: View {
    let face: Face
    let entity: Entity
    let factsByCategory: [FactCategory: [EntityFact]]

    /// Seeded so a screenshot can show the evidence state, which is otherwise
    /// only reachable by tapping.
    var initiallyExpanded: Set<Int64> = []

    @State private var expanded: Set<Int64> = []
    @State private var summaryExpanded = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                header
                Rectangle().fill(face.hairline).frame(height: 1)
                    .padding(.top, 18).padding(.horizontal, 22)

                ForEach(FactCategory.allCases, id: \.self) { category in
                    if let facts = factsByCategory[category], !facts.isEmpty {
                        section(category, facts)
                    }
                }

                provenance
            }
        }
        .background(face.ground)
        .preferredColorScheme(face.night ? .dark : .light)
        .onAppear { if expanded.isEmpty { expanded = initiallyExpanded } }
    }

    // MARK: - Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 0) {
            Eyebrow(text: entity.kind.uppercased(), face: face)
                .padding(.top, 10)

            Text(entity.name.uppercased())
                .font(.display(64))
                .foregroundStyle(face.text)
                .padding(.top, 1)

            if !usefulAliases.isEmpty {
                Text("also called \(usefulAliases.joined(separator: ", "))")
                    .font(.data(12))
                    .foregroundStyle(face.muted)
                    .padding(.top, 3)
            }

            // Three lines, then "more". The summary can run to fifteen lines,
            // and the whole premise is answering a question while standing in
            // dust — nobody reads a paragraph in that position. The facts below
            // are the answer; this is orientation.
            Text(entity.summary)
                .font(.body_(17))
                .foregroundStyle(face.text)
                .fixedSize(horizontal: false, vertical: true)
                .lineLimit(summaryExpanded ? nil : 3)
                .padding(.top, 12)

            if !summaryExpanded, entity.summary.count > 190 {
                Button("more") { withAnimation(.easeOut(duration: 0.2)) {
                    summaryExpanded = true } }
                    .font(.body_(15))
                    .foregroundStyle(face.accent)
                    .padding(.top, 4)
            }
        }
        .padding(.horizontal, 22)
    }

    // MARK: - A category of facts

    /// Discovery captured some aliases that are typos or the entity's own name
    /// in another case — "Oris" for Doris. Harmless in matching, but shown to a
    /// person they read as though the camp uses them.
    private var usefulAliases: [String] {
        let name = entity.name.lowercased()
        return entity.aliases.filter { alias in
            let a = alias.lowercased()
            if a == name { return false }
            // A one-character edit away from the name is a typo, not a nickname.
            if abs(a.count - name.count) <= 1, editDistance(a, name) <= 1 { return false }
            return true
        }
    }

    private func editDistance(_ a: String, _ b: String) -> Int {
        let a = Array(a), b = Array(b)
        var prev = Array(0...b.count)
        for i in 1...max(a.count, 1) where !a.isEmpty {
            var cur = [i] + [Int](repeating: 0, count: b.count)
            for j in 1...max(b.count, 1) where !b.isEmpty {
                cur[j] = a[i-1] == b[j-1] ? prev[j-1]
                       : min(prev[j-1], prev[j], cur[j-1]) + 1
            }
            prev = cur
        }
        return prev[b.count]
    }

    private func section(_ category: FactCategory, _ facts: [EntityFact]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Eyebrow(text: category.label, face: face)
                .padding(.top, 26)

            VStack(alignment: .leading, spacing: 0) {
                ForEach(facts) { fact in
                    factRow(fact)
                    if fact.id != facts.last?.id {
                        Rectangle().fill(face.hairline).frame(height: 1)
                    }
                }
            }
            .padding(.top, 12)
        }
        .padding(.horizontal, 22)
    }

    /// One fact per row, and the date it was asserted is never hidden.
    ///
    /// Facts are ordered oldest first, so where the camp changed its mind the
    /// two statements sit next to each other with their dates visible and the
    /// reader can see the disagreement for themselves. Silently showing only
    /// the newer one would be presenting a resolution the camp never reached.
    private func factRow(_ fact: EntityFact) -> some View {
        let isOpen = expanded.contains(fact.id)

        return VStack(alignment: .leading, spacing: 0) {
            Button {
                withAnimation(.easeOut(duration: 0.16)) {
                    if isOpen { expanded.remove(fact.id) } else { expanded.insert(fact.id) }
                }
            } label: {
                HStack(alignment: .top, spacing: 12) {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(fact.fact)
                            .font(.body_(16))
                            .foregroundStyle(face.text)
                            .multilineTextAlignment(.leading)
                            .fixedSize(horizontal: false, vertical: true)

                        HStack(spacing: 8) {
                            if let date = EntityStore.humanDate(fact.assertedOn) {
                                Text(date).font(.data(12)).foregroundStyle(face.muted)
                            }
                            if !fact.evidence.isEmpty {
                                Text(fact.evidence.count == 1
                                     ? "1 source" : "\(fact.evidence.count) sources")
                                    .font(.data(12))
                                    .foregroundStyle(face.accent)
                            }
                        }
                    }
                    Spacer(minLength: 0)

                    // Every claim can show its receipt; this is the way in.
                    Image(systemName: isOpen ? "chevron.up" : "chevron.down")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(face.muted)
                        .padding(.top, 3)
                }
                .padding(.vertical, Style.current.rowSpacing)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if isOpen {
                VStack(alignment: .leading, spacing: 14) {
                    ForEach(fact.evidence) { ev in
                        // A rule down the left, not a bubble: this is testimony
                        // being cited, not Lucy speaking.
                        VStack(alignment: .leading, spacing: 6) {
                            Text(ev.quote)
                                .font(.body_(15))
                                .foregroundStyle(face.text)
                                .fixedSize(horizontal: false, vertical: true)
                            Text(ev.attribution)
                                .font(.data(11))
                                .foregroundStyle(face.muted)
                        }
                        .padding(.leading, 13)
                        .overlay(alignment: .leading) {
                            RoundedRectangle(cornerRadius: Style.current.quoteRuleWidth / 2, style: .continuous)
                                .fill(face.accent).frame(width: Style.current.quoteRuleWidth)
                        }
                    }
                }
                .padding(.bottom, 15)
            }
        }
    }

    // MARK: - Provenance footer

    private var provenance: some View {
        VStack(alignment: .leading, spacing: 5) {
            Rectangle().fill(face.hairline).frame(height: 1).padding(.bottom, 14)
            Eyebrow(text: "WHAT THIS IS BUILT FROM", face: face)
            Text("\(entity.mentionCount) mentions"
                 + (EntityStore.humanDate(entity.firstSeen).map { " · first \($0)" } ?? "")
                 + (EntityStore.humanDate(entity.lastSeen).map { " · last \($0)" } ?? ""))
                .font(.data(12))
                .foregroundStyle(face.muted)
        }
        .padding(.horizontal, 22)
        .padding(.top, 30)
        .padding(.bottom, 40)
    }
}
