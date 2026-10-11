import AppKit
import Carbon
import SayAllControl

/// A validated global shortcut. Grammar and canonical form mirror
/// daemon/product/shortcut_chord.zig; the key table and macOS validity rules
/// mirror daemon/darwin_shortcut.zig. Both are pinned by shared fixtures in
/// tests/contracts-0.2.
struct ShortcutChord: Equatable {
    static let defaultValue = "CTRL+SLASH"

    struct Modifiers: OptionSet, Equatable {
        let rawValue: Int
        static let command = Modifiers(rawValue: 1 << 0)
        static let control = Modifiers(rawValue: 1 << 1)
        static let option = Modifiers(rawValue: 1 << 2)
        static let shift = Modifiers(rawValue: 1 << 3)
    }

    /// Carbon virtual key codes (`kVK_*`) keyed by xkb keysym name.
    static let keyCodes: [String: UInt32] = [
        "A": 0x00, "S": 0x01, "D": 0x02, "F": 0x03, "H": 0x04, "G": 0x05, "Z": 0x06, "X": 0x07,
        "C": 0x08, "V": 0x09, "B": 0x0B, "Q": 0x0C, "W": 0x0D, "E": 0x0E, "R": 0x0F, "Y": 0x10,
        "T": 0x11, "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15, "6": 0x16, "5": 0x17, "EQUAL": 0x18,
        "9": 0x19, "7": 0x1A, "MINUS": 0x1B, "8": 0x1C, "0": 0x1D, "BRACKETRIGHT": 0x1E, "O": 0x1F,
        "U": 0x20, "BRACKETLEFT": 0x21, "I": 0x22, "P": 0x23, "RETURN": 0x24, "L": 0x25, "J": 0x26,
        "APOSTROPHE": 0x27, "K": 0x28, "SEMICOLON": 0x29, "BACKSLASH": 0x2A, "COMMA": 0x2B,
        "SLASH": 0x2C, "N": 0x2D, "M": 0x2E, "PERIOD": 0x2F, "TAB": 0x30, "SPACE": 0x31,
        "GRAVE": 0x32, "BACKSPACE": 0x33, "ESCAPE": 0x35, "F17": 0x40, "F18": 0x4F, "F19": 0x50,
        "F20": 0x5A, "F5": 0x60, "F6": 0x61, "F7": 0x62, "F3": 0x63, "F8": 0x64, "F9": 0x65,
        "F11": 0x67, "F13": 0x69, "F16": 0x6A, "F14": 0x6B, "F10": 0x6D, "F12": 0x6F, "F15": 0x71,
        "HELP": 0x72, "HOME": 0x73, "PAGE_UP": 0x74, "DELETE": 0x75, "F4": 0x76, "END": 0x77,
        "F2": 0x78, "PAGE_DOWN": 0x79, "F1": 0x7A, "LEFT": 0x7B, "RIGHT": 0x7C, "DOWN": 0x7D,
        "UP": 0x7E,
    ]

    private static let keySymbols: [String: String] = [
        "EQUAL": "=", "MINUS": "-", "BRACKETRIGHT": "]", "BRACKETLEFT": "[", "RETURN": "↩",
        "APOSTROPHE": "'", "SEMICOLON": ";", "BACKSLASH": "\\", "COMMA": ",", "SLASH": "/",
        "PERIOD": ".", "TAB": "⇥", "SPACE": "Space", "GRAVE": "`", "BACKSPACE": "⌫", "ESCAPE": "⎋",
        "HELP": "Help", "HOME": "↖", "PAGE_UP": "⇞", "DELETE": "⌦", "END": "↘", "PAGE_DOWN": "⇟",
        "LEFT": "←", "RIGHT": "→", "DOWN": "↓", "UP": "↑",
    ]

    let modifiers: Modifiers
    let key: String
    let keyCode: UInt32

    init?(_ value: String) {
        let trimmed = value.trimmingCharacters(in: CharacterSet(charactersIn: " \t\r\n"))
        guard !trimmed.isEmpty, !trimmed.contains(",") else { return nil }
        var modifiers: Modifiers = []
        var key: String?
        for raw in trimmed.split(separator: "+", omittingEmptySubsequences: false) {
            let part = raw.trimmingCharacters(in: CharacterSet(charactersIn: " \t"))
            guard !part.isEmpty, part.allSatisfy(\.isASCII) else { return nil }
            let modifier: Modifiers?
            switch part.uppercased() {
            case "CTRL", "CONTROL": modifier = .control
            case "ALT", "OPTION", "OPT": modifier = .option
            case "SHIFT": modifier = .shift
            case "SUPER", "WIN", "MOD4", "CMD", "COMMAND": modifier = .command
            default: modifier = nil
            }
            if let modifier {
                guard !modifiers.contains(modifier), key == nil else { return nil }
                modifiers.insert(modifier)
            } else {
                guard key == nil, Self.safeKey(part) else { return nil }
                key = part.uppercased()
            }
        }
        guard let key, let keyCode = Self.keyCodes[key] else { return nil }
        // Outside F1-F20 a chord needs Control, Option, or Command so it never captures typing.
        guard Self.isFunctionKey(key) || !modifiers.isDisjoint(with: [.command, .control, .option]) else { return nil }
        self.modifiers = modifiers
        self.key = key
        self.keyCode = keyCode
    }

    /// Canonical `SUPER+CTRL+ALT+SHIFT+KEY` form shared with the CLI and Linux.
    var canonical: String {
        var parts: [String] = []
        if modifiers.contains(.command) { parts.append("SUPER") }
        if modifiers.contains(.control) { parts.append("CTRL") }
        if modifiers.contains(.option) { parts.append("ALT") }
        if modifiers.contains(.shift) { parts.append("SHIFT") }
        return (parts + [key]).joined(separator: "+")
    }

    /// Menu-style form in Apple's modifier order, such as `⌃/` or `⇧⌘Space`.
    var symbols: String {
        var text = ""
        if modifiers.contains(.control) { text += "⌃" }
        if modifiers.contains(.option) { text += "⌥" }
        if modifiers.contains(.shift) { text += "⇧" }
        if modifiers.contains(.command) { text += "⌘" }
        return text + (Self.keySymbols[key] ?? key)
    }

    var carbonModifiers: UInt32 {
        var flags: UInt32 = 0
        if modifiers.contains(.command) { flags |= UInt32(cmdKey) }
        if modifiers.contains(.control) { flags |= UInt32(controlKey) }
        if modifiers.contains(.option) { flags |= UInt32(optionKey) }
        if modifiers.contains(.shift) { flags |= UInt32(shiftKey) }
        return flags
    }

    private static func safeKey(_ value: String) -> Bool {
        (1...64).contains(value.utf8.count) && value.utf8.allSatisfy {
            ($0 >= 48 && $0 <= 57) || ($0 >= 65 && $0 <= 90) || ($0 >= 97 && $0 <= 122) || [45, 46, 58, 95].contains($0)
        }
    }

    private static func isFunctionKey(_ key: String) -> Bool {
        key.count >= 2 && key.hasPrefix("F") && key.dropFirst().allSatisfy { $0.isASCII && $0.isNumber }
    }
}

enum ShortcutPreferenceError: Error, Equatable {
    case malformed, unsupportedVersion, invalidShortcut
}

/// The persisted `shortcut.json` preference (version 1), shared with Linux.
struct ShortcutPreference: Equatable {
    static let `default` = ShortcutPreference(enabled: true, chord: ShortcutChord(ShortcutChord.defaultValue)!)
    private static let maximumBytes = 1_048_576

    let enabled: Bool
    let chord: ShortcutChord

    /// A missing file means the default shortcut, exactly as on Linux.
    static func load(from url: URL) throws -> ShortcutPreference {
        guard FileManager.default.fileExists(atPath: url.path) else { return .default }
        guard let attributes = try? FileManager.default.attributesOfItem(atPath: url.path),
              let size = attributes[.size] as? NSNumber, size.intValue <= maximumBytes,
              let data = try? Data(contentsOf: url) else { throw ShortcutPreferenceError.malformed }
        return try decode(data)
    }

    static func decode(_ data: Data) throws -> ShortcutPreference {
        guard let document = try? JSONDecoder().decode(Document.self, from: data) else {
            throw ShortcutPreferenceError.malformed
        }
        guard (document.version ?? 1) == 1 else { throw ShortcutPreferenceError.unsupportedVersion }
        guard let chord = ShortcutChord(document.shortcut ?? ShortcutChord.defaultValue) else {
            throw ShortcutPreferenceError.invalidShortcut
        }
        return ShortcutPreference(enabled: document.enabled ?? true, chord: chord)
    }

    private struct Document: Decodable {
        let version: Int?
        let enabled: Bool?
        let shortcut: String?
    }
}

/// Owns the single Carbon hotkey. A failed change never drops the shortcut that
/// is already registered.
@MainActor
final class GlobalShortcut {
    enum Status: Equatable {
        case registered(ShortcutChord)
        case disabled(ShortcutChord)
        case unavailable(ShortcutChord?, reason: String)
    }

    struct Failure: Error, Equatable {
        let code: String
        let message: String
        var legacy: String { "\(code): \(message)" }
    }

    nonisolated static let failureCodes: Set<String> = ["shortcut_conflict", "invalid_shortcut", "shortcut_unavailable"]

    private(set) var status: Status = .unavailable(nil, reason: "Not registered")
    private var hotKey: EventHotKeyRef?
    private var registered: ShortcutChord?
    private var nextIdentifier: UInt32 = 1
    private var handlerInstalled = false
    private let action: @MainActor () -> Void
    private let systemReservation: (ShortcutChord) -> Bool

    init(systemReservation: @escaping (ShortcutChord) -> Bool = GlobalShortcut.isReservedBySystem,
         action: @escaping @MainActor () -> Void) {
        self.systemReservation = systemReservation
        self.action = action
    }

    /// Loads `url` and applies it, returning the registered state or a failure.
    func apply(contentsOf url: URL) -> Result<HostShortcutState, Failure> {
        let preference: ShortcutPreference
        do {
            preference = try ShortcutPreference.load(from: url)
        } catch {
            let failure = Failure(code: "invalid_shortcut",
                message: "\(url.path) is invalid; run 'sayall shortcut reset' to replace it")
            if registered == nil { status = .unavailable(nil, reason: failure.message) }
            return .failure(failure)
        }
        return apply(preference)
    }

    func apply(_ preference: ShortcutPreference) -> Result<HostShortcutState, Failure> {
        let chord = preference.chord
        guard preference.enabled else {
            unregister()
            status = .disabled(chord)
            return .success(HostShortcutState(enabled: false, shortcut: chord.canonical))
        }
        if registered == chord {
            status = .registered(chord)
            return .success(HostShortcutState(enabled: true, shortcut: chord.canonical))
        }
        if systemReservation(chord) {
            return fail(chord, Failure(code: "shortcut_conflict",
                message: "\(chord.symbols) is reserved by a macOS keyboard shortcut; change it in System Settings › Keyboard › Keyboard Shortcuts or choose another"))
        }
        installHandlerIfNeeded()
        var reference: EventHotKeyRef?
        let identifier = EventHotKeyID(signature: OSType(0x53415941), id: nextIdentifier)
        nextIdentifier &+= 1
        let result = RegisterEventHotKey(chord.keyCode, chord.carbonModifiers, identifier,
                                         GetApplicationEventTarget(), 0, &reference)
        guard result == noErr, let reference else {
            let message = result == OSStatus(eventHotKeyExistsErr)
                ? "\(chord.symbols) is already used by another app"
                : "macOS could not register \(chord.symbols) (error \(result))"
            return fail(chord, Failure(code: result == OSStatus(eventHotKeyExistsErr) ? "shortcut_conflict" : "shortcut_unavailable",
                                       message: message))
        }
        unregister()
        hotKey = reference
        registered = chord
        status = .registered(chord)
        return .success(HostShortcutState(enabled: true, shortcut: chord.canonical))
    }

    private func fail(_ chord: ShortcutChord, _ failure: Failure) -> Result<HostShortcutState, Failure> {
        if registered == nil { status = .unavailable(chord, reason: failure.message) }
        return .failure(failure)
    }

    private func unregister() {
        if let hotKey { UnregisterEventHotKey(hotKey) }
        hotKey = nil
        registered = nil
    }

    private func installHandlerIfNeeded() {
        guard !handlerInstalled else { return }
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        // The shortcut object lives for the app's lifetime, so the unretained context stays valid.
        handlerInstalled = InstallEventHandler(GetApplicationEventTarget(), { _, _, context in
            guard let context else { return noErr }
            let shortcut = Unmanaged<GlobalShortcut>.fromOpaque(context).takeUnretainedValue()
            MainActor.assumeIsolated { shortcut.action() }
            return noErr
        }, 1, &spec, Unmanaged.passUnretained(self).toOpaque(), nil) == noErr
    }

    /// Enabled system-wide shortcuts (Spotlight, input sources, Mission Control, ...)
    /// swallow the key before a Carbon hotkey sees it, so treat them as conflicts.
    nonisolated static func isReservedBySystem(_ chord: ShortcutChord) -> Bool {
        var unmanaged: Unmanaged<CFArray>?
        guard CopySymbolicHotKeys(&unmanaged) == noErr,
              let entries = unmanaged?.takeRetainedValue() as? [[String: Any]] else { return false }
        return entries.contains { entry in
            (entry[kHISymbolicHotKeyEnabled as String] as? Bool) == true
                && (entry[kHISymbolicHotKeyCode as String] as? Int) == Int(chord.keyCode)
                && (entry[kHISymbolicHotKeyModifiers as String] as? Int) == Int(chord.carbonModifiers)
        }
    }
}
