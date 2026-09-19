import Foundation
import os

enum MemoryProbe {

    /// Resident physical footprint of this process, as counted by jetsam.
    static func footprintBytes() -> UInt64 {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)

        let result = withUnsafeMutablePointer(to: &info) { ptr in
            ptr.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { intPtr in
                task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), intPtr, &count)
            }
        }

        guard result == KERN_SUCCESS else { return 0 }
        return UInt64(info.phys_footprint)
    }

    /// Bytes this process may still allocate before iOS terminates it.
    static func availableBytes() -> UInt64 {
        UInt64(os_proc_available_memory())
    }

    static func mb(_ bytes: UInt64) -> String {
        String(format: "%.0f MB", Double(bytes) / 1_048_576.0)
    }

    static func describe(_ label: String) -> String {
        "\(label): footprint=\(mb(footprintBytes())) available=\(mb(availableBytes()))"
    }
}

/// Thread-safe high-water mark, sampled from a background thread while a
/// blocking llama call runs on the calling thread.
final class PeakSampler: @unchecked Sendable {
    private let lock = NSLock()
    private var peak: UInt64 = 0
    private var stopped = false

    init() {
        peak = MemoryProbe.footprintBytes()
        Thread.detachNewThread { [weak self] in
            while let self, !self.isStopped {
                let now = MemoryProbe.footprintBytes()
                self.lock.lock()
                self.peak = max(self.peak, now)
                self.lock.unlock()
                Thread.sleep(forTimeInterval: 0.05)
            }
        }
    }

    private var isStopped: Bool {
        lock.lock(); defer { lock.unlock() }
        return stopped
    }

    /// Stops sampling and returns the high-water mark.
    func finish() -> UInt64 {
        lock.lock(); defer { lock.unlock() }
        stopped = true
        return peak
    }
}
