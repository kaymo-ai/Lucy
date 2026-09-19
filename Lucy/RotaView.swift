import SwiftUI

// The week, as a week.
//
// A rota is a table and this renders it as one. It is deliberately not
// something Lucy answers out of: the failure mode of paraphrasing a rota is
// somebody missing their shift, and a wrong answer about the burn barrel at
// 9am is a real cost in a way that a wrong answer about lore is not.
//
// Nothing here is generated. `shift_grid` is a copy of the camp's sign-up
// sheet, parsed by scripts/parse_shift_grid.py, and the sheet stays the
// authority.

struct RotaView: View {
    let face: Face
    let assignments: [ShiftAssignment]

    @Environment(\.dismiss) private var dismiss
    /// Filter to one person. The question people actually have is "when am
    /// I on", and scanning eighty-five rows for your own name is the thing a
    /// screen should do for you.
    @State private var mine: String

    /// `filter` exists so a render can capture the filtered state; the app
    /// always opens with an empty box.
    init(face: Face, assignments: [ShiftAssignment], filter: String = "") {
        self.face = face
        self.assignments = assignments
        _mine = State(initialValue: filter)
    }

    private var query: String {
        mine.trimmingCharacters(in: .whitespaces).lowercased()
    }

    /// The burn this sheet is for, when the file name gave one.
    private var year: String? {
        assignments.compactMap(\.year).max().map(String.init)
    }

    private var shown: [ShiftAssignment] {
        guard !query.isEmpty else { return assignments }
        return assignments.filter { $0.person.lowercased().contains(query) }
    }

    /// Grouped by day, in the order the rota already arrived in — the store
    /// sorts by the week, so this must not re-sort by anything else.
    private var days: [(day: String, rows: [ShiftAssignment])] {
        var order: [String] = []
        var byDay: [String: [ShiftAssignment]] = [:]
        for a in shown {
            if byDay[a.day] == nil { order.append(a.day) }
            byDay[a.day, default: []].append(a)
        }
        return order.map { ($0, byDay[$0] ?? []) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            search
            if shown.isEmpty {
                Text(mine.isEmpty
                     ? "No rota in this database yet."
                     : "Nobody by that name is on the rota.")
                    .font(.body_(18)).foregroundStyle(face.muted)
                    .padding(.top, 18)
                Spacer()
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(days, id: \.day) { group in
                            dayBlock(group.day, group.rows)
                        }
                    }
                    .padding(.bottom, 30)
                }
                .scrollIndicators(.hidden)
            }
        }
        .padding(22)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(face.ground)
        .preferredColorScheme(face.night ? .dark : .light)
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 3) {
                Text(Style.current.label("Shifts"))
                    .font(.eyebrow(16))
                    .tracking(Style.current.eyebrowTracking + Style.current.boost(0.5))
                    .foregroundStyle(face.accent)
                // Filtered, the count is the thing being checked: the sheet
                // asks everyone for three, so seeing "2 shifts" is the answer
                // to a question people are actually asking.
                //
                // The year leads, always. The store hands back one year, but
                // WHICH year is the difference between this week's rota and a
                // 2024 one, and a screen that does not say cannot be checked.
                Text([year, query.isEmpty
                        ? "\(assignments.count) sign-ups, "
                          + "\(Set(assignments.map(\.person)).count) people"
                        : "\(shown.count) shift\(shown.count == 1 ? "" : "s")"]
                     .compactMap { $0 }.joined(separator: " · "))
                    .font(.data(13)).foregroundStyle(face.muted)
            }
            Spacer()
            Button("Done") { dismiss() }
                .font(.body_(17)).foregroundStyle(face.muted)
        }
        .padding(.bottom, 14)
    }

    private var search: some View {
        TextField("Your name", text: $mine)
            .textFieldStyle(.plain)
            .font(.body_(18))
            .foregroundStyle(face.text)
            .padding(.horizontal, 12).padding(.vertical, 11)
            .background(RoundedRectangle(cornerRadius: 9).fill(face.raised))
            .padding(.bottom, 14)
            .autocorrectionDisabled()
    }

    private func dayBlock(_ day: String, _ rows: [ShiftAssignment]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(Style.current.label(day))
                .font(.eyebrow(13))
                .tracking(Style.current.eyebrowTracking)
                .foregroundStyle(face.warm)
                .padding(.top, 16).padding(.bottom, 6)

            ForEach(rows) { a in
                row(a)
                Divider().overlay(face.muted.opacity(0.2))
            }
        }
    }

    @ViewBuilder
    private func row(_ a: ShiftAssignment) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                // The name leads, because the name is what you scan for.
                Text(a.person)
                    .font(.body_(19)).foregroundStyle(face.text)
                Spacer(minLength: 8)
                // Right-aligned, and simply absent when the sheet gives no
                // time. A third of the rota is untimed (the bus crew sign up
                // for a run, not for an hour), and a column of em dashes held
                // ninety points open to say nothing.
                if !a.timeSlot.isEmpty {
                    Text(a.timeSlot)
                        .font(.data(14)).foregroundStyle(face.muted)
                }
            }
            Text(a.category.isEmpty ? a.shift : "\(a.shift) · \(a.category)")
                .font(.body_(16)).foregroundStyle(face.muted)
                .fixedSize(horizontal: false, vertical: true)
            // The sheet's own description of the job, shown once you have
            // narrowed to a person. Unfiltered this is an index and eighty-five
            // paragraphs would drown it; filtered it is your own briefing, and
            // "Burn Barrel" alone does not tell you what to do.
            if !query.isEmpty && !a.detail.isEmpty {
                Text(a.detail)
                    .font(.body_(16)).foregroundStyle(face.text.opacity(0.75))
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 2)
            }
        }
        .padding(.vertical, 9)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
