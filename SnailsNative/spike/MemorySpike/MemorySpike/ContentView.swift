import SwiftUI

@MainActor
final class SpikeLog: ObservableObject {
    @Published var lines: [String] = []

    /// Every line is flushed to Documents/spike-log.txt as it is written.
    /// If jetsam kills the app mid-run -- which is the outcome this spike is
    /// built to detect -- the in-memory list dies with it, but the file
    /// survives and can be pulled off with `devicectl device copy from`.
    private let logURL: URL = FileManager.default
        .urls(for: .documentDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("spike-log.txt")

    func log(_ line: String) {
        let stamped = "[\(Self.timestamp())] \(line)"
        print("SPIKE: \(stamped)")
        lines.append(stamped)
        append(stamped)
    }

    private func append(_ line: String) {
        guard let data = (line + "\n").data(using: .utf8) else { return }
        if let handle = try? FileHandle(forWritingTo: logURL) {
            defer { try? handle.close() }
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: data)
            try? handle.synchronize()
        } else {
            try? data.write(to: logURL)
        }
    }

    private static func timestamp() -> String {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return f.string(from: Date())
    }
}

struct ContentView: View {
    @StateObject private var log = SpikeLog()
    @State private var running = false

    var body: some View {
        NavigationStack {
            List(Array(log.lines.enumerated()), id: \.offset) { _, line in
                Text(line).font(.system(.caption, design: .monospaced))
            }
            .navigationTitle("Memory Spike")
            // Launched as `devicectl device process launch ... --autorun 4096`,
            // the run starts by itself. Each context size therefore gets its own
            // process, so the first run's Metal caches and allocator
            // fragmentation cannot contaminate the second's numbers.
            .task {
                if let n = Self.autorunContextTokens {
                    run(contextTokens: n,
                        useMmap: !ProcessInfo.processInfo.arguments.contains("--nommap"))
                }
            }
            .toolbar {
                ToolbarItemGroup(placement: .topBarTrailing) {
                    // Two context sizes: 4096 isolates the weights, 32768 is
                    // what the product would actually run. Both are needed for
                    // the results table, so they are buttons rather than a
                    // source edit + rebuild between runs.
                    Button("4K") { run(contextTokens: 4096) }.disabled(running)
                    Button("32K") { run(contextTokens: 32768) }.disabled(running)
                }
            }
        }
    }

    /// `--autorun <n>` in the launch arguments starts a run of that context
    /// size on appear, with no tap.
    private static var autorunContextTokens: Int32? {
        let args = ProcessInfo.processInfo.arguments
        guard let i = args.firstIndex(of: "--autorun"), i + 1 < args.count else { return nil }
        return Int32(args[i + 1])
    }

    private func run(contextTokens: Int32, useMmap: Bool = true) {
        running = true
        Task {
            // Detached: the llama calls block for tens of seconds and must not
            // sit on the main thread, or the watchdog becomes a confounder.
            await Task.detached(priority: .userInitiated) {
                await SpikeRunner(log: log,
                                  contextTokens: contextTokens,
                                  useMmap: useMmap).run()
            }.value
            running = false
        }
    }
}
