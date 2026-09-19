import Foundation
import Security

// Two strings that have to survive a reinstall: this phone's identity, and its
// camp token.
//
// UserDefaults would lose both when the app is deleted, and losing the device
// id is not a small thing — the server treats it as the member id, so a phone
// that forgets it rejoins as a second person, and every note that phone ever
// uploaded now belongs to a stranger with the same name.
//
// Nothing stored here is a secret worth a Secure Enclave. The keychain is used
// for one property: it outlives the app.
enum Keychain {
    private static let service = "ai.kaymo.Lucy.sync"

    static func read(_ key: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var out: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &out) == errSecSuccess,
              let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    @discardableResult
    static func write(_ key: String, _ value: String) -> Bool {
        delete(key)
        let item: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecValueData as String: Data(value.utf8),
            // The phone is in a pocket most of the burn and sync only runs
            // while someone is holding it, so after-first-unlock is enough.
            // ThisDeviceOnly keeps it out of an iCloud backup, which matters
            // because a restored backup would put two phones on one identity.
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        return SecItemAdd(item as CFDictionary, nil) == errSecSuccess
    }

    static func delete(_ key: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
        ]
        SecItemDelete(query as CFDictionary)
    }
}
