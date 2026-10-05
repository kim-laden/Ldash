import Darwin
import Foundation
import LocalAuthentication

protocol PhoneChrome: AnyObject {
    func presentFile(_ url: URL)
    func openWeb(_ url: URL)
    func shareFile(_ url: URL)
}

/// Phone-side methods for `window.laden.call`. Files stay in the app Documents folder.
/// There is no shell, no other-app process list, and no window list.
final class PhoneHost {
    weak var chrome: PhoneChrome?

    private let fileManager = FileManager.default
    private let lock = NSLock()
    private var cpuPrev: (UInt64, UInt64)?
    private var parkedTransfers: [TransferBox] = []
    private let documents: URL
    private let vaultRoot: URL
    private let support: URL

    init() {
        let base = fileManager.urls(for: .documentDirectory, in: .userDomainMask)[0]
        documents = base
        vaultRoot = base.appendingPathComponent("vault-workspace", isDirectory: true)
        support = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("laden-ops", isDirectory: true)
    }

    func dispatch(method: String, params: [String: Any], reply: @escaping ([String: Any]) -> Void) {
        switch method {
        case "verifyPassword":
            verifyOwner(reply: reply)
        case "launch":
            openLink(string(params, "command"), reply: reply)
        case "openPath":
            openPath(string(params, "path"), reply: reply)
        case "quit", "hide":
            reply(["ok": true])
        default:
            DispatchQueue.global(qos: .userInitiated).async {
                reply(self.perform(method, params: params))
            }
        }
    }

    private func perform(_ method: String, params: [String: Any]) -> [String: Any] {
        switch method {
        case "system":
            return sampleSystem()
        case "processes":
            return ["ok": true, "groups": [Any]()]
        case "watchApp":
            let query = string(params, "query")
            return [
                "ok": true,
                "query": query,
                "count": 0,
                "cpuPct": 0,
                "memPct": 0,
                "rssMb": 0,
                "pid": NSNull(),
                "state": "",
                "threads": 0,
                "comm": query,
            ]
        case "windows":
            return ["ok": true, "windows": [Any]()]
        case "fetch":
            return fetch(string(params, "url"))
        case "localUser":
            return ["ok": true, "username": "", "homedir": documents.path, "uid": 0]
        case "vaultEnsureDir":
            return vaultEnsureDir(string(params, "rel"))
        case "vaultWriteFile":
            return vaultWriteFile(string(params, "rel"), filename: string(params, "filename"), data: string(params, "data"))
        case "vaultReadOpen":
            return vaultReadOpen(string(params, "rel"), filename: string(params, "filename"))
        case "vaultReadChunk":
            return vaultReadChunk(string(params, "token"), offset: int(params, "offset"))
        case "vaultWriteOpen":
            return vaultWriteOpen(string(params, "rel"), filename: string(params, "filename"))
        case "vaultWriteChunk":
            return vaultWriteChunk(string(params, "token"), data: string(params, "data"))
        case "vaultWriteFinish":
            return vaultWriteFinish(string(params, "token"))
        case "saveTextOpen":
            return saveTextOpen(string(params, "filename"))
        case "saveTextChunk":
            return saveTextChunk(string(params, "token"), data: string(params, "data"))
        case "saveTextFinish":
            return saveTextFinish(string(params, "token"))
        case "vaultWriteNote":
            return vaultWriteNote(string(params, "rel"), filename: string(params, "filename"), body: string(params, "body"))
        case "exportBudgetPdf":
            return exportBudget(string(params, "rel"), title: string(params, "title"), sections: params["sections"])
        case "exportCasePdf":
            return exportCase(params)
        case "openTerminal":
            return ["ok": false, "error": "A terminal is not available on iPhone."]
        case "aiChat":
            return aiChat(params)
        case "setShortcut":
            return setShortcut(string(params, "shortcut"))
        case "shortcut":
            return ["ok": true, "shortcut": savedShortcut(), "backend": "iphone"]
        case "prepareUserFolder":
            return prepareUserFolder(string(params, "email"), username: string(params, "username"))
        case "saveKit":
            return saveKit(string(params, "json"))
        case "saveVaultConf":
            return saveVaultConf(string(params, "text"))
        case "loadVaultConf":
            return loadVaultConf()
        case "accountFetch":
            return accountFetch(params)
        default:
            return ["ok": false, "error": "unknown method \(method)"]
        }
    }

    // MARK: Device sample

    private func sampleSystem() -> [String: Any] {
        let ticks = cpuTicks()
        var cpuPct: Double?
        lock.lock()
        if let prev = cpuPrev, ticks.total > 0 {
            let dt = ticks.total &- prev.0
            let idle = ticks.idle &- prev.1
            if dt > 0 {
                cpuPct = max(0, min(100, 100 * (1 - Double(idle) / Double(dt))))
            }
        }
        if ticks.total > 0 {
            cpuPrev = ticks
        }
        lock.unlock()

        let memory = memorySample()
        let memPct = memory.total > 0 ? Double(memory.used) / Double(memory.total) * 100 : 0
        var load = [Double](repeating: 0, count: 3)
        if getloadavg(&load, 3) != 3 {
            load = [0, 0, 0]
        }
        var host = [CChar](repeating: 0, count: 256)
        gethostname(&host, host.count)
        let hostname = String(cString: host)
        var row: [String: Any] = [
            "ok": true,
            "hostname": hostname.isEmpty ? "iPhone" : hostname,
            "cpuCount": ProcessInfo.processInfo.processorCount,
            "cpuTempC": NSNull(),
            "memTotal": NSNumber(value: memory.total),
            "memUsed": NSNumber(value: memory.used),
            "memPct": rounded(memPct, places: 1),
            "load1": rounded(load[0], places: 2),
            "load5": rounded(load[1], places: 2),
            "load15": rounded(load[2], places: 2),
            "gpuPct": NSNull(),
            "gpuTempC": NSNull(),
        ]
        if let cpuPct {
            row["cpuPct"] = rounded(cpuPct, places: 1)
        } else {
            row["cpuPct"] = NSNull()
        }
        return row
    }

    private func cpuTicks() -> (total: UInt64, idle: UInt64) {
        var info = host_cpu_load_info()
        var count = mach_msg_type_number_t(MemoryLayout<host_cpu_load_info>.size / MemoryLayout<integer_t>.size)
        let result = withUnsafeMutablePointer(to: &info) { pointer -> kern_return_t in
            pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { rebound in
                host_statistics(mach_host_self(), HOST_CPU_LOAD_INFO, rebound, &count)
            }
        }
        if result != KERN_SUCCESS {
            return (0, 0)
        }
        let user = UInt64(info.cpu_ticks.0)
        let system = UInt64(info.cpu_ticks.1)
        let idle = UInt64(info.cpu_ticks.2)
        let nice = UInt64(info.cpu_ticks.3)
        return (user &+ system &+ idle &+ nice, idle)
    }

    private func memorySample() -> (total: UInt64, used: UInt64) {
        let total = sysctlU64("hw.memsize")
        let page = sysctlU64("hw.pagesize")
        var stats = vm_statistics64()
        var count = mach_msg_type_number_t(MemoryLayout<vm_statistics64>.size / MemoryLayout<integer_t>.size)
        let result = withUnsafeMutablePointer(to: &stats) { pointer -> kern_return_t in
            pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { rebound in
                host_statistics64(mach_host_self(), HOST_VM_INFO64, rebound, &count)
            }
        }
        if result == KERN_SUCCESS {
            let pages = UInt64(stats.active_count) &+ UInt64(stats.wire_count) &+ UInt64(stats.compressor_page_count)
            let used = pages &* (page == 0 ? 4096 : page)
            if total == 0 {
                return (used, used)
            }
            return (total, min(total, used))
        }
        return (total, 0)
    }

    private func sysctlU64(_ name: String) -> UInt64 {
        var value: UInt64 = 0
        var length = MemoryLayout<UInt64>.size
        if sysctlbyname(name, &value, &length, nil, 0) != 0 {
            return 0
        }
        return value
    }

    // MARK: Links and files

    /// Web links only. A stored command is not run.
    private func openLink(_ command: String, reply: @escaping ([String: Any]) -> Void) {
        let trimmed = command.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: trimmed), let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https", url.host != nil else {
            reply(["ok": false, "error": "Only web links open on iPhone."])
            return
        }
        DispatchQueue.main.async {
            self.chrome?.openWeb(url)
        }
        reply(["ok": true])
    }

    private func openPath(_ target: String, reply: @escaping ([String: Any]) -> Void) {
        let trimmed = target.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty {
            reply(["ok": false, "error": "empty path"])
            return
        }
        if trimmed.hasPrefix("http://") || trimmed.hasPrefix("https://") {
            openLink(trimmed, reply: reply)
            return
        }
        let url = URL(fileURLWithPath: (trimmed as NSString).expandingTildeInPath).standardizedFileURL
        var isDirectory: ObjCBool = false
        guard fileManager.fileExists(atPath: url.path, isDirectory: &isDirectory) else {
            reply(["ok": false, "error": "path not found"])
            return
        }
        guard isInsideDocuments(url) else {
            reply(["ok": false, "error": "path is outside the app"])
            return
        }
        if !isDirectory.boolValue {
            DispatchQueue.main.async {
                self.chrome?.presentFile(url)
            }
        }
        reply(["ok": true, "path": url.path])
    }

    private func isInsideDocuments(_ url: URL) -> Bool {
        let root = documents.standardizedFileURL.path
        let path = url.standardizedFileURL.path
        return path == root || path.hasPrefix(root + "/")
    }

    // MARK: Account

    /// Creates the config folder and the vault. The password is hashed in the page and is not written here.
    private func prepareUserFolder(_ email: String, username: String) -> [String: Any] {
        let mail = email.trimmingCharacters(in: .whitespacesAndNewlines)
        let name = username.trimmingCharacters(in: .whitespacesAndNewlines)
        if !mail.contains("@") || mail.count > 120 {
            return ["ok": false, "error": "Need an email address"]
        }
        if name.isEmpty || name.count > 32 {
            return ["ok": false, "error": "Need a username"]
        }
        do {
            try fileManager.createDirectory(at: support, withIntermediateDirectories: true)
            try fileManager.createDirectory(at: vaultRoot, withIntermediateDirectories: true)
            for folder in ["legal", "credentials", "engagements"] {
                let made = vaultEnsureDir(folder)
                if (made["ok"] as? Bool) != true { return made }
            }
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "en_US_POSIX")
            formatter.timeZone = TimeZone(identifier: "UTC")
            formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss'Z'"
            let account: [String: String] = [
                "product": "Ldash",
                "version": "1.0.0",
                "email": mail,
                "username": name,
                "createdAt": formatter.string(from: Date()),
            ]
            let data = try JSONSerialization.data(withJSONObject: account, options: [.prettyPrinted, .sortedKeys])
            var text = String(data: data, encoding: .utf8) ?? "{}\n"
            if !text.hasSuffix("\n") { text += "\n" }
            let file = support.appendingPathComponent("account.json")
            try text.write(to: file, atomically: true, encoding: .utf8)
            return ["ok": true, "path": support.path, "vault": vaultRoot.path]
        } catch {
            return ["ok": false, "error": errorText(error)]
        }
    }

    /// Writes kit.json next to account.json. The text is not logged.
    private func saveKit(_ json: String) -> [String: Any] {
        let text = json
        if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return ["ok": false, "error": "Nothing to save"]
        }
        if text.utf8.count > 24 * 1024 * 1024 {
            return ["ok": false, "error": "The kit is too large to save"]
        }
        guard let data = text.data(using: .utf8),
              let parsed = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              parsed["settings"] is [String: Any] else {
            return ["ok": false, "error": "The kit is not valid"]
        }
        do {
            try fileManager.createDirectory(at: support, withIntermediateDirectories: true)
            let dest = support.appendingPathComponent("kit.json")
            try data.write(to: dest, options: .atomic)
            try fileManager.setAttributes([.protectionKey: FileProtectionType.complete], ofItemAtPath: dest.path)
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "en_US_POSIX")
            formatter.timeZone = TimeZone(identifier: "UTC")
            formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss'Z'"
            return ["ok": true, "bytes": data.count, "savedAt": formatter.string(from: Date())]
        } catch {
            return ["ok": false, "error": "Could not write the kit"]
        }
    }

    /// Reads vault.conf next to account.json. The text is not logged.
    private func loadVaultConf() -> [String: Any] {
        let dest = support.appendingPathComponent("vault.conf")
        guard fileManager.fileExists(atPath: dest.path) else {
            return ["ok": true, "exists": false]
        }
        do {
            let data = try Data(contentsOf: dest)
            if data.count > 24 * 1024 * 1024 {
                return ["ok": false, "error": "vault.conf is too large"]
            }
            guard let text = String(data: data, encoding: .utf8) else {
                return ["ok": false, "error": "Could not read vault.conf"]
            }
            return ["ok": true, "exists": true, "text": text]
        } catch {
            return ["ok": false, "error": "Could not read vault.conf"]
        }
    }

    /// Writes vault.conf next to account.json. The text is not logged.
    private func saveVaultConf(_ text: String) -> [String: Any] {
        if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return ["ok": false, "error": "Nothing to save"]
        }
        guard let data = text.data(using: .utf8) else {
            return ["ok": false, "error": "The kit is not valid"]
        }
        if data.count > 24 * 1024 * 1024 {
            return ["ok": false, "error": "The kit is too large to save"]
        }
        let start = text.firstIndex(of: "{") ?? text.startIndex
        guard let parsed = try? JSONSerialization.jsonObject(with: Data(text[start...].utf8)) as? [String: Any],
              parsed["kind"] as? String == "laden.vault.conf" else {
            return ["ok": false, "error": "The kit is not valid"]
        }
        do {
            try fileManager.createDirectory(at: support, withIntermediateDirectories: true)
            let dest = support.appendingPathComponent("vault.conf")
            try data.write(to: dest, options: .atomic)
            try fileManager.setAttributes([.protectionKey: FileProtectionType.complete], ofItemAtPath: dest.path)
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "en_US_POSIX")
            formatter.timeZone = TimeZone(identifier: "UTC")
            formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss'Z'"
            return ["ok": true, "bytes": data.count, "savedAt": formatter.string(from: Date())]
        } catch {
            return ["ok": false, "error": "Could not write vault.conf"]
        }
    }

    /// Laden cloud vault call. The body and token are not logged.
    private func accountFetch(_ params: [String: Any]) -> [String: Any] {
        let urlText = string(params, "url").trimmingCharacters(in: .whitespacesAndNewlines)
        let method = string(params, "method").trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        let verb = method.isEmpty ? "GET" : method
        if verb != "GET" && verb != "POST" && verb != "PUT" {
            return ["ok": false, "error": "method not allowed"]
        }
        guard let url = URL(string: urlText), let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https", let host = url.host?.lowercased() else {
            return ["ok": false, "error": "only http(s) URLs"]
        }
        if scheme == "http" && host != "127.0.0.1" && host != "localhost" {
            return ["ok": false, "error": "the cloud copy needs https"]
        }
        var request = URLRequest(url: url)
        request.httpMethod = verb
        request.timeoutInterval = 20
        request.setValue("laden-ops", forHTTPHeaderField: "User-Agent")
        request.setValue("application/json, text/plain", forHTTPHeaderField: "Accept")
        let token = string(params, "token")
        if !token.isEmpty {
            request.setValue("Bearer " + token, forHTTPHeaderField: "Authorization")
        }
        if verb != "GET" {
            let body = string(params, "body")
            if body.utf8.count > 8_000_000 {
                return ["ok": false, "error": "the kit is too large to send"]
            }
            request.setValue("text/plain; charset=utf-8", forHTTPHeaderField: "Content-Type")
            request.httpBody = body.data(using: .utf8)
        }
        let (status, data, failure) = transfer(request, httpsOnly: scheme == "https", timeout: 20)
        if failure != nil {
            return ["ok": false, "error": "Could not reach the cloud copy"]
        }
        let text = String(data: data, encoding: .utf8) ?? ""
        return ["ok": true, "status": status, "body": text]
    }

    // MARK: Vault

    private func vaultEnsureDir(_ rel: String) -> [String: Any] {
        do {
            let dest = try vaultDir(rel)
            try fileManager.createDirectory(at: dest, withIntermediateDirectories: true)
            let readme = dest.appendingPathComponent("NOTES.md")
            if !fileManager.fileExists(atPath: readme.path) {
                let text = "# Vault workspace\n\nAuthorized engagement notes only.\nPath: \(dest.path)\n"
                try text.write(to: readme, atomically: true, encoding: .utf8)
            }
            return ["ok": true, "path": dest.path]
        } catch {
            return ["ok": false, "error": errorText(error)]
        }
    }

    private func vaultWriteFile(_ rel: String, filename: String, data: String) -> [String: Any] {
        if data.count > 12_000_000 {
            return ["ok": false, "error": "file is over 8 MB"]
        }
        guard let raw = Data(base64Encoded: data, options: .ignoreUnknownCharacters) else {
            return ["ok": false, "error": "file could not be read"]
        }
        if raw.count > 8_000_000 {
            return ["ok": false, "error": "file is over 8 MB"]
        }
        do {
            let dest = try vaultDir(rel)
            try fileManager.createDirectory(at: dest, withIntermediateDirectories: true)
            let file = dest.appendingPathComponent(uploadName(filename))
            try raw.write(to: file, options: .atomic)
            return ["ok": true, "path": file.path]
        } catch {
            return ["ok": false, "error": errorText(error)]
        }
    }

    private func vaultWriteNote(_ rel: String, filename: String, body: String) -> [String: Any] {
        if body.utf8.count > 4_000_000 {
            return ["ok": false, "error": "note is too large"]
        }
        do {
            let dest = try vaultDir(rel)
            try fileManager.createDirectory(at: dest, withIntermediateDirectories: true)
            let file = dest.appendingPathComponent(noteName(filename))
            try body.write(to: file, atomically: true, encoding: .utf8)
            return ["ok": true, "path": file.path]
        } catch {
            return ["ok": false, "error": errorText(error)]
        }
    }

    private func vaultDir(_ rel: String) throws -> URL {
        let root = vaultRoot.standardizedFileURL
        let raw = rel.replacingOccurrences(of: "\\", with: "/")
        if raw.contains("..") || raw.hasPrefix("/") {
            throw VaultError.escape
        }
        let allowed = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_./-")
        var safe = ""
        for scalar in raw.unicodeScalars {
            safe.unicodeScalars.append(allowed.contains(scalar) ? scalar : "_")
        }
        while safe.hasPrefix("/") {
            safe.removeFirst()
        }
        while safe.hasSuffix("/") {
            safe.removeLast()
        }
        if safe.count > 180 {
            safe = String(safe.prefix(180))
        }
        if safe.isEmpty {
            safe = "_root"
        }
        var dest = root
        for part in safe.split(separator: "/") {
            if part.isEmpty || part == "." || part == ".." {
                throw VaultError.escape
            }
            dest.appendPathComponent(String(part))
        }
        dest = dest.standardizedFileURL
        let rootPath = root.path
        let destPath = dest.path
        if destPath != rootPath && !destPath.hasPrefix(rootPath + "/") {
            throw VaultError.escape
        }
        return dest
    }

    private func uploadName(_ raw: String) -> String {
        let base = (raw as NSString).lastPathComponent
        let allowed = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")
        var name = ""
        for scalar in base.unicodeScalars {
            name.unicodeScalars.append(allowed.contains(scalar) ? scalar : "_")
        }
        name = name.trimmingCharacters(in: CharacterSet(charactersIn: "._"))
        if name.count > 80 {
            name = String(name.prefix(80))
        }
        if name.isEmpty || name == "." || name == ".." {
            return "file"
        }
        return name
    }

    private struct Transfer {
        var kind: String
        var at: Date
        var data: String
        var parts: [String]
        var size: Int
        var rel: String
        var filename: String
    }

    private var transfers: [String: Transfer] = [:]

    private func pruneTransfersLocked() {
        let now = Date()
        for (key, item) in transfers where now.timeIntervalSince(item.at) > 600 {
            transfers.removeValue(forKey: key)
        }
        if transfers.count > 8 {
            let oldest = transfers.sorted { $0.value.at < $1.value.at }
            for entry in oldest.prefix(transfers.count - 8) {
                transfers.removeValue(forKey: entry.key)
            }
        }
    }

    private func newToken() -> String {
        String(UUID().uuidString.replacingOccurrences(of: "-", with: "").prefix(16)).lowercased()
    }

    private func exportName(_ raw: String) -> String {
        let base = ((raw.isEmpty ? "laden.vault.conf" : raw) as NSString).lastPathComponent
        let allowed = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")
        var name = ""
        for scalar in base.unicodeScalars {
            name.unicodeScalars.append(allowed.contains(scalar) ? scalar : "_")
        }
        name = name.trimmingCharacters(in: CharacterSet(charactersIn: "._"))
        if name.count > 80 {
            name = String(name.prefix(80))
        }
        if name.isEmpty || name == "." || name == ".." {
            return "laden.vault.conf"
        }
        return name
    }

    private func vaultReadOpen(_ rel: String, filename: String) -> [String: Any] {
        let data: String
        do {
            let file = try vaultDir(rel).appendingPathComponent(uploadName(filename))
            guard fileManager.fileExists(atPath: file.path) else {
                return ["ok": false, "error": "file not found"]
            }
            let raw = try Data(contentsOf: file)
            if raw.count > 8_000_000 {
                return ["ok": false, "error": "file is over 8 MB"]
            }
            data = raw.base64EncodedString()
        } catch {
            return ["ok": false, "error": errorText(error)]
        }
        lock.lock()
        pruneTransfersLocked()
        let token = newToken()
        transfers[token] = Transfer(kind: "read", at: Date(), data: data, parts: [], size: data.count, rel: "", filename: "")
        lock.unlock()
        return ["ok": true, "token": token, "length": data.count]
    }

    private func vaultReadChunk(_ token: String, offset: Int) -> [String: Any] {
        lock.lock()
        defer { lock.unlock() }
        guard var item = transfers[token], item.kind == "read" else {
            return ["ok": false, "error": "transfer expired"]
        }
        let data = item.data
        let start = max(0, offset)
        let end = min(data.count, start + 240_000)
        let from = data.index(data.startIndex, offsetBy: start)
        let to = data.index(data.startIndex, offsetBy: end)
        let chunk = String(data[from..<to])
        let done = end >= data.count
        if done {
            transfers.removeValue(forKey: token)
        } else {
            item.at = Date()
            transfers[token] = item
        }
        return ["ok": true, "data": chunk, "next": end, "done": done]
    }

    private func vaultWriteOpen(_ rel: String, filename: String) -> [String: Any] {
        lock.lock()
        pruneTransfersLocked()
        let token = newToken()
        transfers[token] = Transfer(kind: "write", at: Date(), data: "", parts: [], size: 0, rel: rel, filename: filename.isEmpty ? "file" : filename)
        lock.unlock()
        return ["ok": true, "token": token]
    }

    private func vaultWriteChunk(_ token: String, data: String) -> [String: Any] {
        lock.lock()
        defer { lock.unlock() }
        guard var item = transfers[token], item.kind == "write" else {
            return ["ok": false, "error": "transfer expired"]
        }
        let next = item.size + data.count
        if next > 12_000_000 {
            transfers.removeValue(forKey: token)
            return ["ok": false, "error": "file is over 8 MB"]
        }
        item.size = next
        item.parts.append(data)
        item.at = Date()
        transfers[token] = item
        return ["ok": true]
    }

    private func vaultWriteFinish(_ token: String) -> [String: Any] {
        lock.lock()
        let item = transfers.removeValue(forKey: token)
        lock.unlock()
        guard let item, item.kind == "write" else {
            return ["ok": false, "error": "transfer expired"]
        }
        return vaultWriteFile(item.rel, filename: item.filename, data: item.parts.joined())
    }

    private func saveTextOpen(_ filename: String) -> [String: Any] {
        lock.lock()
        pruneTransfersLocked()
        let token = newToken()
        transfers[token] = Transfer(kind: "text", at: Date(), data: "", parts: [], size: 0, rel: "", filename: exportName(filename))
        lock.unlock()
        return ["ok": true, "token": token]
    }

    private func saveTextChunk(_ token: String, data: String) -> [String: Any] {
        lock.lock()
        defer { lock.unlock() }
        guard var item = transfers[token], item.kind == "text" else {
            return ["ok": false, "error": "transfer expired"]
        }
        let next = item.size + data.count
        if next > 96_000_000 {
            transfers.removeValue(forKey: token)
            return ["ok": false, "error": "vault.conf is too large"]
        }
        item.size = next
        item.parts.append(data)
        item.at = Date()
        transfers[token] = item
        return ["ok": true]
    }

    private func saveTextFinish(_ token: String) -> [String: Any] {
        lock.lock()
        let item = transfers.removeValue(forKey: token)
        lock.unlock()
        guard let item, item.kind == "text" else {
            return ["ok": false, "error": "transfer expired"]
        }
        do {
            let dest = support.appendingPathComponent("exports", isDirectory: true)
            try fileManager.createDirectory(at: dest, withIntermediateDirectories: true)
            let file = dest.appendingPathComponent(item.filename)
            try item.parts.joined().write(to: file, atomically: true, encoding: .utf8)
            DispatchQueue.main.async {
                self.chrome?.shareFile(file)
            }
            return ["ok": true, "path": file.path]
        } catch {
            return ["ok": false, "error": errorText(error)]
        }
    }

    private func noteName(_ raw: String) -> String {
        let base = (raw.isEmpty ? "note.md" : raw as NSString).lastPathComponent
        if base.isEmpty || base == "." || base == ".." {
            return "note.md"
        }
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "._-"))
        var name = ""
        for scalar in base.unicodeScalars {
            name.unicodeScalars.append(allowed.contains(scalar) ? scalar : "_")
        }
        if name.count > 80 {
            name = String(name.prefix(80))
        }
        if name.isEmpty || name == "." || name == ".." {
            return "note.md"
        }
        return name
    }

    // MARK: PDF

    private func exportCase(_ params: [String: Any]) -> [String: Any] {
        let title = clip(string(params, "title").isEmpty ? "case" : string(params, "title"), 120)
        let scope = clip(string(params, "scope"), 400)
        let summary = clip(string(params, "summary"), 8000)
        let caseBody = clip(string(params, "caseBody"), 20000)
        let dumps = Array((params["dumps"] as? [Any] ?? []).prefix(40))
        let sections = [
            ("SCOPE", scope.isEmpty ? "(none)" : scope),
            ("CALEB'S THOUGHTS", summary.isEmpty ? "(no summary yet)" : summary),
            ("CASE", caseBody.isEmpty ? "(no analysed intel yet)" : caseBody),
            ("DUMP", dumpText(dumps)),
        ]
        return writePdf(rel: string(params, "rel"), name: "case.pdf", title: title, sections: sections, banner: "CASE")
    }

    private func exportBudget(_ rel: String, title: String, sections raw: Any?) -> [String: Any] {
        var clean: [(String, String)] = []
        for item in Array((raw as? [Any] ?? []).prefix(8)) {
            guard let row = item as? [String: Any] else { continue }
            let heading = clip(string(row, "heading").isEmpty ? "SECTION" : string(row, "heading"), 40)
            clean.append((heading, clip(string(row, "body"), 12000)))
        }
        if clean.isEmpty {
            clean = [("MONTH", "(empty)")]
        }
        let sheet = clip(title.isEmpty ? "month" : title, 120)
        return writePdf(rel: rel.isEmpty ? "economy" : rel, name: "budget.pdf", title: sheet, sections: clean, banner: "BOOKS")
    }

    private func writePdf(rel: String, name: String, title: String, sections: [(String, String)], banner: String) -> [String: Any] {
        do {
            let dest = try vaultDir(rel)
            try fileManager.createDirectory(at: dest, withIntermediateDirectories: true)
            let file = dest.appendingPathComponent(name)
            try PdfSheet.write(to: file, title: title, sections: sections, banner: banner)
            DispatchQueue.main.async {
                self.chrome?.presentFile(file)
            }
            return ["ok": true, "path": file.path]
        } catch {
            return ["ok": false, "error": errorText(error)]
        }
    }

    private func dumpText(_ dumps: [Any]) -> String {
        var blocks: [String] = []
        for item in dumps {
            guard let row = item as? [String: Any] else { continue }
            var name = string(row, "name").trimmingCharacters(in: .whitespacesAndNewlines)
            if name.isEmpty { name = "dump" }
            name = String(name.prefix(80))
            let body = String(string(row, "body").trimmingCharacters(in: .whitespacesAndNewlines).prefix(700))
            blocks.append(body.isEmpty ? name : name + "\n" + body)
        }
        return blocks.isEmpty ? "(empty)" : blocks.joined(separator: "\n\n")
    }

    // MARK: AI and fetch

    private func fetch(_ raw: String) -> [String: Any] {
        let urlText = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: urlText), let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https", url.host != nil else {
            return ["ok": false, "error": "only http(s) URLs"]
        }
        var request = URLRequest(url: url)
        request.setValue("laden-ops", forHTTPHeaderField: "User-Agent")
        request.timeoutInterval = 8
        let (status, data, failure) = transfer(request, httpsOnly: false, timeout: 8)
        if let failure {
            return ["ok": false, "error": scrub(failure)]
        }
        let cap = status >= 400 ? 80_000 : 200_000
        let clipped = Data(data.prefix(cap))
        if let parsed = try? JSONSerialization.jsonObject(with: clipped, options: [.fragmentsAllowed]) {
            return ["ok": true, "status": status, "body": parsed]
        }
        let text = String(data: clipped, encoding: .utf8) ?? ""
        return ["ok": true, "status": status, "body": String(text.prefix(4000))]
    }

    private func aiChat(_ params: [String: Any]) -> [String: Any] {
        let key = string(params, "apiKey").trimmingCharacters(in: .whitespacesAndNewlines)
        if key.isEmpty {
            return ["ok": false, "error": "missing api key"]
        }
        let requested = string(params, "provider")
        let which = ["xai", "groq", "openrouter"].contains(requested) ? requested : "xai"
        let system = clip(string(params, "system"), 4000)
        let user = clip(string(params, "user"), 12000)
        let model = optionalString(params, "model")
        var headers = [
            "Authorization": "Bearer \(key)",
            "Content-Type": "application/json",
            "User-Agent": "laden-ops",
        ]
        let urlText: String
        let payload: [String: Any]
        if which == "xai" {
            urlText = "https://api.x.ai/v1/responses"
            payload = [
                "model": model ?? "grok-4.7",
                "input": [
                    ["role": "system", "content": system],
                    ["role": "user", "content": user],
                ],
            ]
        } else {
            urlText = which == "groq"
                ? "https://api.groq.com/openai/v1/chat/completions"
                : "https://openrouter.ai/api/v1/chat/completions"
            if which == "openrouter" {
                headers["HTTP-Referer"] = "https://laden.no"
                headers["X-Title"] = "Laden Ops Journal"
            }
            let fallback = which == "groq" ? "llama-3.3-70b-versatile" : "openrouter/free"
            payload = [
                "model": model ?? fallback,
                "temperature": 0.4,
                "max_tokens": 700,
                "messages": [
                    ["role": "system", "content": system],
                    ["role": "user", "content": user],
                ],
            ]
        }
        guard let url = URL(string: urlText), let body = try? JSONSerialization.data(withJSONObject: payload) else {
            return ["ok": false, "error": "bad AI request"]
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.httpBody = body
        for (name, value) in headers {
            request.setValue(value, forHTTPHeaderField: name)
        }
        let (status, data, failure) = transfer(request, httpsOnly: true, timeout: 45)
        if let failure {
            return ["ok": false, "error": scrub(failure)]
        }
        let text = String(data: data.prefix(200_000), encoding: .utf8) ?? ""
        if status >= 400 {
            return ["ok": false, "error": scrub("\(which) \(status): \(String(text.prefix(180)))")]
        }
        guard let parsed = try? JSONSerialization.jsonObject(with: Data(text.utf8)) else {
            return ["ok": false, "error": "bad AI JSON"]
        }
        let content = aiText(parsed)
        if content.isEmpty {
            return ["ok": false, "error": "empty AI response"]
        }
        return ["ok": true, "text": content]
    }

    private func aiText(_ data: Any) -> String {
        guard let dict = data as? [String: Any] else { return "" }
        if let direct = dict["output_text"] as? String {
            let trimmed = direct.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty { return trimmed }
        }
        var chunks: [String] = []
        if let output = dict["output"] as? [Any] {
            for item in output {
                guard let row = item as? [String: Any], let content = row["content"] as? [Any] else { continue }
                for part in content {
                    if let block = part as? [String: Any], let text = block["text"] as? String {
                        chunks.append(text)
                    }
                }
            }
        }
        if !chunks.isEmpty {
            return chunks.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
        }
        if let choices = dict["choices"] as? [Any],
           let first = choices.first as? [String: Any],
           let message = first["message"] as? [String: Any],
           let content = message["content"] as? String {
            return content.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return ""
    }

    private func transfer(_ request: URLRequest, httpsOnly: Bool, timeout: TimeInterval) -> (Int, Data, String?) {
        let gate = RedirectGate(httpsOnly: httpsOnly)
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = timeout
        config.timeoutIntervalForResource = timeout + 5
        config.waitsForConnectivity = false
        let session = URLSession(configuration: config, delegate: gate, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        let box = TransferBox()
        let task = session.dataTask(with: request) { data, response, error in
            box.lock.lock()
            if box.failure == nil && box.body.isEmpty && box.status == 0 {
                if let error {
                    box.failure = error.localizedDescription
                } else {
                    box.status = (response as? HTTPURLResponse)?.statusCode ?? 200
                    box.body = data ?? Data()
                }
            }
            box.lock.unlock()
            box.wait.signal()
        }
        task.resume()
        if box.wait.wait(timeout: .now() + timeout + 5) == .timedOut {
            task.cancel()
            if box.wait.wait(timeout: .now() + 5) == .timedOut {
                lock.lock()
                parkedTransfers.append(box)
                lock.unlock()
            }
            return (0, Data(), "timed out")
        }
        box.lock.lock()
        let status = box.status
        let body = box.body
        let failure = box.failure
        box.lock.unlock()
        return (status, body, failure)
    }

    // MARK: Unlock and shortcut

    /// Face ID or the device passcode. The typed vault field is not compared and not stored.
    private func verifyOwner(reply: @escaping ([String: Any]) -> Void) {
        let start = {
            let context = LAContext()
            var authError: NSError?
            guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &authError) else {
                reply([
                    "ok": false,
                    "method": "local-authentication",
                    "error": "Face ID or a device passcode is required.",
                ])
                return
            }
            context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: "Unlock the Laden Ops vault.") { ok, _ in
                if ok {
                    reply(["ok": true, "method": "local-authentication"])
                } else {
                    reply(["ok": false, "method": "local-authentication"])
                }
            }
        }
        if Thread.isMainThread {
            start()
        } else {
            DispatchQueue.main.async(execute: start)
        }
    }

    private func savedShortcut() -> String {
        let file = support.appendingPathComponent("shortcut")
        if let text = try? String(contentsOf: file, encoding: .utf8) {
            let line = text.split(separator: "\n", maxSplits: 1, omittingEmptySubsequences: true).first.map(String.init) ?? ""
            let cleaned = line.trimmingCharacters(in: .whitespacesAndNewlines)
            if !cleaned.isEmpty { return cleaned }
        }
        return "Ctrl+`"
    }

    private func setShortcut(_ accel: String) -> [String: Any] {
        var cleaned = accel.trimmingCharacters(in: .whitespacesAndNewlines)
        if cleaned.isEmpty { cleaned = "Ctrl+`" }
        cleaned = cleaned.replacingOccurrences(of: "CommandOrControl", with: "Ctrl")
        cleaned = cleaned.replacingOccurrences(of: "Control", with: "Ctrl")
        do {
            try fileManager.createDirectory(at: support, withIntermediateDirectories: true)
            try (cleaned + "\n").write(to: support.appendingPathComponent("shortcut"), atomically: true, encoding: .utf8)
            return ["ok": true, "shortcut": cleaned, "backend": "iphone"]
        } catch {
            return ["ok": false, "shortcut": cleaned, "backend": "iphone", "error": errorText(error)]
        }
    }

    // MARK: Text

    private func int(_ params: [String: Any], _ key: String) -> Int {
        let value = params[key]
        if let number = value as? NSNumber { return number.intValue }
        if let text = value as? String { return Int(text) ?? 0 }
        return 0
    }

    private func string(_ params: [String: Any], _ key: String) -> String {
        let value = params[key]
        if value == nil || value is NSNull { return "" }
        if let text = value as? String { return text }
        if let number = value as? NSNumber { return number.stringValue }
        return ""
    }

    private func optionalString(_ params: [String: Any], _ key: String) -> String? {
        let text = string(params, key).trimmingCharacters(in: .whitespacesAndNewlines)
        return text.isEmpty ? nil : text
    }

    private func clip(_ text: String, _ limit: Int) -> String {
        text.count <= limit ? text : String(text.prefix(limit))
    }

    private func rounded(_ value: Double, places: Int) -> Double {
        let factor = pow(10.0, Double(places))
        return (value * factor).rounded() / factor
    }

    private func scrub(_ text: String) -> String {
        guard let regex = try? NSRegularExpression(
            pattern: "(?i)(?:sk-|xai-|gsk_|sk-or-|Bearer\\s+)[A-Za-z0-9_\\-]{4,}"
        ) else { return text }
        let range = NSRange(text.startIndex..., in: text)
        return regex.stringByReplacingMatches(in: text, options: [], range: range, withTemplate: "[redacted]")
    }

    private func errorText(_ error: Error) -> String {
        if let vault = error as? VaultError {
            return vault.message
        }
        return error.localizedDescription
    }
}

private enum VaultError: Error {
    case escape
    var message: String { "path escapes vault workspace" }
}

private final class TransferBox {
    let wait = DispatchSemaphore(value: 0)
    let lock = NSLock()
    var status = 0
    var body = Data()
    var failure: String?
}

private final class RedirectGate: NSObject, URLSessionTaskDelegate {
    let httpsOnly: Bool
    init(httpsOnly: Bool) { self.httpsOnly = httpsOnly }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        let scheme = request.url?.scheme?.lowercased() ?? ""
        if scheme == "https" || (!httpsOnly && scheme == "http") {
            completionHandler(request)
        } else {
            completionHandler(nil)
        }
    }
}
