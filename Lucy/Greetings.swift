import Foundation

// The first bubble. UI chrome only: the greeting is never part of what the
// model sees — prompt history is built from question/answer pairs in
// LucyVoice.chatLogSection, and a greeting is neither. That is why these
// lines are allowed to exist at all; if the greeting ever starts riding
// along in the prompt, she will start quoting these back as answers
// (see CLAUDE.md, "the barrels live in Doris").
//
// Industrial keeps the single original line, from LucyVoice where it has
// always lived. Playful picks per open, by clock and by calendar.

enum Greetings {
    static func line(now: Date = Date()) -> String {
        guard Style.current.isPlayful else { return LucyVoice.greeting }

        let calendar = Calendar.current
        let hour = calendar.component(.hour, from: now)
        let weekday = calendar.component(.weekday, from: now)   // 1 = Sunday

        var pool: [String] = [
            // Always in the running, any hour.
            LucyVoice.greeting,
            "Hey you. What do you need?",
            "Ask me anything — I've got the whole camp in my head.",
            "What can I dig up for you?",
        ]

        switch hour {
        case 5..<11:
            pool += [
                "Good morning beautiful!",
                "Morning, sunshine. What's the plan?",
                "Up with the sun. What do you need?",
            ]
        case 11..<17:
            pool += [
                "Afternoon, lovely. What's going on out there?",
                "Big day? Tell me what you need.",
            ]
        case 17..<22:
            pool += [
                "Evening, gorgeous. How was the day?",
                "Golden hour. Golden questions.",
                "What do you need before dark?",
            ]
        default:
            pool += [
                "Still up? Me too. Always.",
                "Late one, huh. I'm here.",
                "The camp never really sleeps. Ask away.",
            ]
        }

        switch weekday {
        case 7: pool.append("Saturday! Got one more wind in you?")
        case 6: pool.append("Friday! The weekend's knocking.")
        case 1: pool.append("Easy Sunday. What's on your mind?")
        default: break
        }

        return pool.randomElement() ?? LucyVoice.greeting
    }
}
