package org.laden.opsdash

import android.app.ActivityManager
import android.content.Context
import android.net.Uri
import android.os.Build
import androidx.appcompat.app.AppCompatActivity
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.core.content.ContextCompat
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.Locale
import java.util.UUID
import java.util.concurrent.Executors
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.round

/**
 * Phone-side methods for window.laden.call. Files stay in the app files directory.
 * There is no shell, no other-app process list, and no window list.
 */
class PhoneHost(private val context: Context, private val chrome: PhoneChrome) {
    interface PhoneChrome {
        fun presentFile(file: File)
        fun openWeb(uri: Uri)
        fun shareFile(file: File)
    }

    private val io = Executors.newFixedThreadPool(3)
    private val lock = Any()
    private val filesRoot = context.filesDir
    private val vaultRoot = File(filesRoot, "vault-workspace")
    private val support = File(filesRoot, "meta")
    private var cpuPrev: Pair<Long, Long>? = null
    private val transfers = HashMap<String, Transfer>()

    private class Transfer(
        val kind: String,
        var at: Long,
        var data: String,
        val parts: ArrayList<String>,
        var size: Int,
        val rel: String,
        val filename: String,
    )

    fun dispatch(method: String, params: JSONObject, reply: (JSONObject) -> Unit) {
        when (method) {
            "verifyPassword" -> verifyOwner(reply)
            "launch" -> reply(openLink(str(params, "command")))
            "openPath" -> reply(openPath(str(params, "path")))
            "quit", "hide" -> reply(JSONObject().put("ok", true))
            else -> io.execute { reply(perform(method, params)) }
        }
    }

    private fun perform(method: String, params: JSONObject): JSONObject {
        return try {
            when (method) {
                "system" -> sampleSystem()
                "processes" -> JSONObject().put("ok", true).put("groups", JSONArray())
                "watchApp" -> watchApp(str(params, "query"))
                "windows" -> JSONObject().put("ok", true).put("windows", JSONArray())
                "fetch" -> fetch(str(params, "url"))
                "localUser" -> JSONObject().put("ok", true).put("username", "").put("homedir", filesRoot.path).put("uid", 0)
                "vaultEnsureDir" -> vaultEnsureDir(str(params, "rel"))
                "vaultWriteFile" -> vaultWriteFile(str(params, "rel"), str(params, "filename"), str(params, "data"))
                "vaultReadOpen" -> vaultReadOpen(str(params, "rel"), str(params, "filename"))
                "vaultReadChunk" -> vaultReadChunk(str(params, "token"), params.optInt("offset", 0))
                "vaultWriteOpen" -> vaultWriteOpen(str(params, "rel"), str(params, "filename"))
                "vaultWriteChunk" -> vaultWriteChunk(str(params, "token"), str(params, "data"))
                "vaultWriteFinish" -> vaultWriteFinish(str(params, "token"))
                "saveTextOpen" -> saveTextOpen(str(params, "filename"))
                "saveTextChunk" -> saveTextChunk(str(params, "token"), str(params, "data"))
                "saveTextFinish" -> saveTextFinish(str(params, "token"))
                "vaultWriteNote" -> vaultWriteNote(str(params, "rel"), str(params, "filename"), str(params, "body"))
                "exportBudgetPdf" -> exportBudget(str(params, "rel"), str(params, "title"), params.optJSONArray("sections"))
                "exportCasePdf" -> exportCase(params)
                "openTerminal" -> fail("A terminal is not available on Android.")
                "aiChat" -> aiChat(params)
                "setShortcut" -> setShortcut(str(params, "shortcut"))
                "shortcut" -> JSONObject().put("ok", true).put("shortcut", savedShortcut()).put("backend", "android")
                "prepareUserFolder" -> prepareUserFolder(str(params, "email"), str(params, "username"))
                "saveKit" -> saveKit(str(params, "json"))
                "saveVaultConf" -> saveVaultConf(str(params, "text"))
                "loadVaultConf" -> loadVaultConf()
                "accountFetch" -> accountFetch(params)
                "windowSnap" -> JSONObject().put("ok", true).put("mode", "off").put("supported", false)
                "setWindowSnap" -> JSONObject().put("ok", true).put("mode", str(params, "mode").ifBlank { "off" }).put("supported", false)
                else -> fail("unknown method $method")
            }
        } catch (exc: Exception) {
            fail(errorText(exc))
        }
    }

    private fun watchApp(query: String): JSONObject {
        return JSONObject()
            .put("ok", true)
            .put("query", query)
            .put("count", 0)
            .put("cpuPct", 0)
            .put("memPct", 0)
            .put("rssMb", 0)
            .put("pid", JSONObject.NULL)
            .put("state", "")
            .put("threads", 0)
            .put("comm", query)
    }

    private fun sampleSystem(): JSONObject {
        val ticks = readCpu()
        var cpuPct: Double? = null
        synchronized(lock) {
            val prev = cpuPrev
            if (prev != null && ticks != null && ticks.first > 0) {
                val dt = ticks.first - prev.first
                val idle = ticks.second - prev.second
                if (dt > 0) cpuPct = (100.0 * (1.0 - idle.toDouble() / dt.toDouble())).coerceIn(0.0, 100.0)
            }
            if (ticks != null && ticks.first > 0) cpuPrev = ticks
        }
        val memory = memorySample()
        val memPct = if (memory.first > 0) memory.second.toDouble() / memory.first.toDouble() * 100.0 else 0.0
        val load = readLoad()
        val row = JSONObject()
            .put("ok", true)
            .put("hostname", Build.MODEL?.ifBlank { "Android" } ?: "Android")
            .put("cpuCount", Runtime.getRuntime().availableProcessors())
            .put("cpuTempC", JSONObject.NULL)
            .put("memTotal", memory.first)
            .put("memUsed", memory.second)
            .put("memPct", rounded(memPct, 1))
            .put("load1", rounded(load[0], 2))
            .put("load5", rounded(load[1], 2))
            .put("load15", rounded(load[2], 2))
            .put("gpuPct", JSONObject.NULL)
            .put("gpuTempC", JSONObject.NULL)
        if (cpuPct == null) row.put("cpuPct", JSONObject.NULL) else row.put("cpuPct", rounded(cpuPct!!, 1))
        return row
    }

    private fun readCpu(): Pair<Long, Long>? {
        return try {
            val line = File("/proc/stat").bufferedReader().use { it.readLine() } ?: return null
            if (!line.startsWith("cpu ")) return null
            val parts = line.trim().split(Regex("\\s+")).drop(1).mapNotNull { it.toLongOrNull() }
            if (parts.size < 4) return null
            val idle = parts[3] + (parts.getOrNull(4) ?: 0L)
            parts.sum() to idle
        } catch (_: Exception) {
            null
        }
    }

    private fun memorySample(): Pair<Long, Long> {
        return try {
            val manager = context.getSystemService(ActivityManager::class.java) ?: return 0L to 0L
            val info = ActivityManager.MemoryInfo()
            manager.getMemoryInfo(info)
            val total = info.totalMem.coerceAtLeast(0L)
            val used = (info.totalMem - info.availMem).coerceAtLeast(0L)
            total to min(total, used)
        } catch (_: Exception) {
            0L to 0L
        }
    }

    private fun readLoad(): DoubleArray {
        return try {
            val parts = File("/proc/loadavg").readText().trim().split(Regex("\\s+"))
            doubleArrayOf(
                parts.getOrNull(0)?.toDoubleOrNull() ?: 0.0,
                parts.getOrNull(1)?.toDoubleOrNull() ?: 0.0,
                parts.getOrNull(2)?.toDoubleOrNull() ?: 0.0,
            )
        } catch (_: Exception) {
            doubleArrayOf(0.0, 0.0, 0.0)
        }
    }

    /** Web links only. A stored command is not run. */
    private fun openLink(command: String): JSONObject {
        val trimmed = command.trim()
        val uri = Uri.parse(trimmed)
        val scheme = uri.scheme?.lowercase()
        if ((scheme != "http" && scheme != "https") || uri.host.isNullOrEmpty()) {
            return fail("Only web links open on Android.")
        }
        chrome.openWeb(uri)
        return JSONObject().put("ok", true)
    }

    private fun openPath(target: String): JSONObject {
        return try {
            val trimmed = target.trim()
            if (trimmed.isEmpty()) return fail("empty path")
            if (trimmed.startsWith("http://") || trimmed.startsWith("https://")) return openLink(trimmed)
            val file = File(trimmed).canonicalFile
            if (!file.exists()) return fail("path not found")
            if (!isInsideFiles(file)) return fail("path is outside the app")
            if (file.isFile) chrome.presentFile(file)
            JSONObject().put("ok", true).put("path", file.path)
        } catch (exc: Exception) {
            fail(errorText(exc))
        }
    }

    private fun isInsideFiles(file: File): Boolean {
        val root = filesRoot.canonicalFile.path
        val path = file.canonicalFile.path
        return path == root || path.startsWith(root + File.separator)
    }

    /** Creates the config folder and the vault. The password is hashed in the page and is not written here. */
    private fun prepareUserFolder(email: String, username: String): JSONObject {
        val mail = email.trim()
        val name = username.trim()
        if (!mail.contains("@") || mail.length > 120) return fail("Need an email address")
        if (name.isEmpty() || name.length > 32) return fail("Need a username")
        return try {
            filesRoot.mkdirs()
            vaultRoot.mkdirs()
            for (folder in listOf("legal", "credentials", "engagements")) {
                val made = vaultEnsureDir(folder)
                if (!made.optBoolean("ok")) return made
            }
            val created = java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'Z'")
                .withZone(java.time.ZoneOffset.UTC)
                .format(java.time.Instant.now())
            val account = JSONObject()
                .put("product", "Ldash")
                .put("version", "1.0.0")
                .put("email", mail)
                .put("username", name)
                .put("createdAt", created)
            File(filesRoot, "account.json").writeText(account.toString(2) + "\n", Charsets.UTF_8)
            JSONObject().put("ok", true).put("path", filesRoot.path).put("vault", vaultRoot.path)
        } catch (exc: Exception) {
            fail(errorText(exc))
        }
    }

    /** Writes kit.json next to account.json. The text is not logged. */
    private fun saveKit(json: String): JSONObject {
        val text = json
        if (text.isBlank()) return fail("Nothing to save")
        val raw = text.toByteArray(Charsets.UTF_8)
        if (raw.size > 24 * 1024 * 1024) return fail("The kit is too large to save")
        try {
            val parsed = JSONObject(text)
            if (parsed.optJSONObject("settings") == null) return fail("The kit is not valid")
        } catch (_: Exception) {
            return fail("The kit is not valid")
        }
        return try {
            filesRoot.mkdirs()
            val dest = File(filesRoot, "kit.json")
            val tmp = File(filesRoot, ".kit.json.tmp")
            tmp.writeBytes(raw)
            if (dest.exists() && !dest.delete()) return fail("Could not write the kit")
            if (!tmp.renameTo(dest)) {
                dest.writeBytes(raw)
                tmp.delete()
            }
            val savedAt = java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'Z'")
                .withZone(java.time.ZoneOffset.UTC)
                .format(java.time.Instant.now())
            JSONObject().put("ok", true).put("bytes", raw.size).put("savedAt", savedAt)
        } catch (_: Exception) {
            fail("Could not write the kit")
        }
    }

    /** Reads vault.conf next to account.json. The text is not logged. */
    private fun loadVaultConf(): JSONObject {
        val dest = File(filesRoot, "vault.conf")
        if (!dest.isFile) return JSONObject().put("ok", true).put("exists", false)
        return try {
            val raw = dest.readBytes()
            if (raw.size > 24 * 1024 * 1024) return fail("vault.conf is too large")
            JSONObject().put("ok", true).put("exists", true).put("text", String(raw, Charsets.UTF_8))
        } catch (_: Exception) {
            fail("Could not read vault.conf")
        }
    }

    /** Writes vault.conf next to account.json. The text is not logged. */
    private fun saveVaultConf(text: String): JSONObject {
        if (text.isBlank()) return fail("Nothing to save")
        val raw = text.toByteArray(Charsets.UTF_8)
        if (raw.size > 24 * 1024 * 1024) return fail("The kit is too large to save")
        val start = text.indexOf('{')
        try {
            val parsed = JSONObject(if (start >= 0) text.substring(start) else text)
            if (parsed.optString("kind") != "laden.vault.conf") return fail("The kit is not valid")
        } catch (_: Exception) {
            return fail("The kit is not valid")
        }
        return try {
            filesRoot.mkdirs()
            val dest = File(filesRoot, "vault.conf")
            val tmp = File(filesRoot, ".vault.conf.tmp")
            tmp.writeBytes(raw)
            if (dest.exists() && !dest.delete()) return fail("Could not write vault.conf")
            if (!tmp.renameTo(dest)) {
                dest.writeBytes(raw)
                tmp.delete()
            }
            val savedAt = java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'Z'")
                .withZone(java.time.ZoneOffset.UTC)
                .format(java.time.Instant.now())
            JSONObject().put("ok", true).put("bytes", raw.size).put("savedAt", savedAt)
        } catch (_: Exception) {
            fail("Could not write vault.conf")
        }
    }

    /** Laden cloud vault call. The body and token are not logged. */
    private fun accountFetch(params: JSONObject): JSONObject {
        val urlText = str(params, "url").trim()
        val method = str(params, "method").ifBlank { "GET" }.uppercase()
        if (method != "GET" && method != "POST" && method != "PUT") return fail("method not allowed")
        val url = try {
            URL(urlText)
        } catch (_: Exception) {
            return fail("only http(s) URLs")
        }
        val scheme = url.protocol.lowercase()
        val host = url.host?.lowercase().orEmpty()
        if (scheme != "http" && scheme != "https") return fail("only http(s) URLs")
        if (scheme == "http" && host != "127.0.0.1" && host != "localhost") return fail("the cloud copy needs https")
        val bodyText = if (method == "GET") "" else str(params, "body")
        val raw = if (method == "GET") null else bodyText.toByteArray(Charsets.UTF_8)
        if (raw != null && raw.size > 8_000_000) return fail("the kit is too large to send")
        val headers = linkedMapOf("User-Agent" to "laden-ops", "Accept" to "application/json, text/plain")
        val token = str(params, "token")
        if (token.isNotEmpty()) headers["Authorization"] = "Bearer $token"
        if (raw != null) headers["Content-Type"] = "text/plain; charset=utf-8"
        val timeoutSec = params.optDouble("timeout", 8.0)
        val timeoutMs = (timeoutSec.coerceIn(1.0, 30.0) * 1000).toInt()
        val (status, data, failure) = transfer(url, method, headers, raw, httpsOnly = scheme == "https", timeoutMs = timeoutMs)
        if (failure != null) return fail("Could not reach the cloud copy")
        return JSONObject().put("ok", true).put("status", status).put("body", data.toString(Charsets.UTF_8))
    }

    private fun vaultEnsureDir(rel: String): JSONObject {
        return try {
            val dest = vaultDir(rel)
            dest.mkdirs()
            val readme = File(dest, "NOTES.md")
            if (!readme.exists()) {
                readme.writeText("# Vault workspace\n\nAuthorized engagement notes only.\nPath: ${dest.path}\n", Charsets.UTF_8)
            }
            JSONObject().put("ok", true).put("path", dest.path)
        } catch (exc: Exception) {
            fail(errorText(exc))
        }
    }

    private fun vaultWriteFile(rel: String, filename: String, data: String): JSONObject {
        if (data.length > 12_000_000) return fail("file is over 8 MB")
        val raw = try {
            android.util.Base64.decode(data, android.util.Base64.DEFAULT)
        } catch (_: IllegalArgumentException) {
            return fail("file could not be read")
        }
        if (raw.size > 8_000_000) return fail("file is over 8 MB")
        return try {
            val dest = vaultDir(rel)
            dest.mkdirs()
            val file = File(dest, uploadName(filename))
            file.writeBytes(raw)
            JSONObject().put("ok", true).put("path", file.path)
        } catch (exc: Exception) {
            fail(errorText(exc))
        }
    }

    private fun vaultWriteNote(rel: String, filename: String, body: String): JSONObject {
        if (body.toByteArray(Charsets.UTF_8).size > 4_000_000) return fail("note is too large")
        return try {
            val dest = vaultDir(rel)
            dest.mkdirs()
            val file = File(dest, noteName(filename))
            file.writeText(body, Charsets.UTF_8)
            JSONObject().put("ok", true).put("path", file.path)
        } catch (exc: Exception) {
            fail(errorText(exc))
        }
    }

    private fun vaultDir(rel: String): File {
        val root = vaultRoot.canonicalFile
        val raw = rel.replace('\\', '/')
        if (raw.contains("..") || raw.startsWith("/")) throw VaultEscape()
        var safe = raw.replace(Regex("[^A-Za-z0-9_./-]+"), "_").trim('/')
        if (safe.length > 180) safe = safe.take(180)
        if (safe.isEmpty()) safe = "_root"
        var dest = root
        for (part in safe.split('/')) {
            if (part.isEmpty() || part == "." || part == "..") throw VaultEscape()
            dest = File(dest, part)
        }
        val canon = dest.canonicalFile
        val rootPath = root.path
        val destPath = canon.path
        if (destPath != rootPath && !destPath.startsWith(rootPath + File.separator)) throw VaultEscape()
        return canon
    }

    private fun uploadName(raw: String): String {
        val base = File(raw.ifEmpty { "file" }).name
        var name = base.replace(Regex("[^A-Za-z0-9._-]+"), "_").trim { it == '.' || it == '_' }
        if (name.length > 80) name = name.take(80)
        if (name.isEmpty() || name == "." || name == "..") return "file"
        return name
    }

    private fun noteName(raw: String): String {
        val base = File(raw.ifEmpty { "note.md" }).name
        if (base.isEmpty() || base == "." || base == "..") return "note.md"
        var name = base.replace(Regex("[^A-Za-z0-9._-]+"), "_")
        if (name.length > 80) name = name.take(80)
        if (name.isEmpty() || name == "." || name == "..") return "note.md"
        return name
    }

    private fun exportName(raw: String): String {
        val base = File(raw.ifEmpty { "laden.vault.conf" }).name
        var name = base.replace(Regex("[^A-Za-z0-9._-]+"), "_").trim { it == '.' || it == '_' }
        if (name.length > 80) name = name.take(80)
        if (name.isEmpty() || name == "." || name == "..") return "laden.vault.conf"
        return name
    }

    private fun pruneLocked() {
        val now = System.currentTimeMillis()
        val dead = transfers.filterValues { now - it.at > 600_000 }.keys.toList()
        for (key in dead) transfers.remove(key)
        if (transfers.size > 8) {
            val oldest = transfers.entries.sortedBy { it.value.at }
            for (entry in oldest.take(transfers.size - 8)) transfers.remove(entry.key)
        }
    }

    private fun newToken(): String {
        return UUID.randomUUID().toString().replace("-", "").take(16).lowercase()
    }

    private fun vaultReadOpen(rel: String, filename: String): JSONObject {
        val data = try {
            val file = File(vaultDir(rel), uploadName(filename))
            if (!file.isFile) return fail("file not found")
            val raw = file.readBytes()
            if (raw.size > 8_000_000) return fail("file is over 8 MB")
            android.util.Base64.encodeToString(raw, android.util.Base64.NO_WRAP)
        } catch (exc: Exception) {
            return fail(errorText(exc))
        }
        val token = synchronized(lock) {
            pruneLocked()
            val token = newToken()
            transfers[token] = Transfer("read", System.currentTimeMillis(), data, ArrayList(), data.length, "", "")
            token
        }
        return JSONObject().put("ok", true).put("token", token).put("length", data.length)
    }

    private fun vaultReadChunk(token: String, offset: Int): JSONObject {
        return synchronized(lock) {
            val item = transfers[token]
            if (item == null || item.kind != "read") return@synchronized fail("transfer expired")
            val start = offset.coerceAtLeast(0)
            val end = min(item.data.length, start + 240_000)
            val chunk = if (start >= item.data.length) "" else item.data.substring(start, end)
            val done = end >= item.data.length
            if (done) transfers.remove(token) else item.at = System.currentTimeMillis()
            JSONObject().put("ok", true).put("data", chunk).put("next", end).put("done", done)
        }
    }

    private fun vaultWriteOpen(rel: String, filename: String): JSONObject {
        val token = synchronized(lock) {
            pruneLocked()
            val token = newToken()
            transfers[token] = Transfer("write", System.currentTimeMillis(), "", ArrayList(), 0, rel, filename.ifEmpty { "file" })
            token
        }
        return JSONObject().put("ok", true).put("token", token)
    }

    private fun vaultWriteChunk(token: String, data: String): JSONObject {
        return synchronized(lock) {
            val item = transfers[token]
            if (item == null || item.kind != "write") return@synchronized fail("transfer expired")
            val next = item.size + data.length
            if (next > 12_000_000) {
                transfers.remove(token)
                return@synchronized fail("file is over 8 MB")
            }
            item.size = next
            item.parts.add(data)
            item.at = System.currentTimeMillis()
            JSONObject().put("ok", true)
        }
    }

    private fun vaultWriteFinish(token: String): JSONObject {
        val item = synchronized(lock) { transfers.remove(token) }
        if (item == null || item.kind != "write") return fail("transfer expired")
        return vaultWriteFile(item.rel, item.filename, item.parts.joinToString(""))
    }

    private fun saveTextOpen(filename: String): JSONObject {
        val token = synchronized(lock) {
            pruneLocked()
            val token = newToken()
            transfers[token] = Transfer("text", System.currentTimeMillis(), "", ArrayList(), 0, "", exportName(filename))
            token
        }
        return JSONObject().put("ok", true).put("token", token)
    }

    private fun saveTextChunk(token: String, data: String): JSONObject {
        return synchronized(lock) {
            val item = transfers[token]
            if (item == null || item.kind != "text") return@synchronized fail("transfer expired")
            val next = item.size + data.length
            if (next > 96_000_000) {
                transfers.remove(token)
                return@synchronized fail("vault.conf is too large")
            }
            item.size = next
            item.parts.add(data)
            item.at = System.currentTimeMillis()
            JSONObject().put("ok", true)
        }
    }

    private fun saveTextFinish(token: String): JSONObject {
        val item = synchronized(lock) { transfers.remove(token) }
        if (item == null || item.kind != "text") return fail("transfer expired")
        return try {
            val dest = File(filesRoot, "exports")
            dest.mkdirs()
            val file = File(dest, item.filename)
            file.writeText(item.parts.joinToString(""), Charsets.UTF_8)
            chrome.shareFile(file)
            JSONObject().put("ok", true).put("path", file.path)
        } catch (exc: Exception) {
            fail(errorText(exc))
        }
    }

    private fun exportCase(params: JSONObject): JSONObject {
        val title = clip(str(params, "title").ifEmpty { "case" }, 120)
        val scope = clip(str(params, "scope"), 400)
        val summary = clip(str(params, "summary"), 8000)
        val caseBody = clip(str(params, "caseBody"), 20000)
        val sections = listOf(
            "SCOPE" to scope.ifEmpty { "(none)" },
            "CALEB'S THOUGHTS" to summary.ifEmpty { "(no summary yet)" },
            "CASE" to caseBody.ifEmpty { "(no analysed intel yet)" },
            "DUMP" to dumpText(params.optJSONArray("dumps")),
        )
        return writePdf(str(params, "rel"), "case.pdf", title, sections, "CASE")
    }

    private fun exportBudget(rel: String, title: String, raw: JSONArray?): JSONObject {
        val clean = ArrayList<Pair<String, String>>()
        val rows = raw ?: JSONArray()
        val limit = min(8, rows.length())
        for (i in 0 until limit) {
            val row = rows.optJSONObject(i) ?: continue
            val heading = clip(str(row, "heading").ifEmpty { "SECTION" }, 40)
            clean.add(heading to clip(str(row, "body"), 12000))
        }
        if (clean.isEmpty()) clean.add("MONTH" to "(empty)")
        return writePdf(rel.ifEmpty { "economy" }, "budget.pdf", clip(title.ifEmpty { "month" }, 120), clean, "BOOKS")
    }

    private fun writePdf(rel: String, name: String, title: String, sections: List<Pair<String, String>>, banner: String): JSONObject {
        return try {
            val dest = vaultDir(rel)
            dest.mkdirs()
            val file = File(dest, name)
            PdfSheet.write(file, title, sections, banner)
            chrome.presentFile(file)
            JSONObject().put("ok", true).put("path", file.path)
        } catch (exc: Exception) {
            fail(errorText(exc))
        }
    }

    private fun dumpText(dumps: JSONArray?): String {
        val rows = dumps ?: return "(empty)"
        val blocks = ArrayList<String>()
        val limit = min(40, rows.length())
        for (i in 0 until limit) {
            val row = rows.optJSONObject(i) ?: continue
            var name = str(row, "name").trim().ifEmpty { "dump" }.take(80)
            val body = str(row, "body").trim().take(700)
            blocks.add(if (body.isEmpty()) name else "$name\n$body")
        }
        return if (blocks.isEmpty()) "(empty)" else blocks.joinToString("\n\n")
    }

    private fun fetch(raw: String): JSONObject {
        val urlText = raw.trim()
        val url = try {
            URL(urlText)
        } catch (_: Exception) {
            return fail("only http(s) URLs")
        }
        val scheme = url.protocol.lowercase()
        if ((scheme != "http" && scheme != "https") || url.host.isNullOrEmpty()) return fail("only http(s) URLs")
        val (status, data, failure) = transfer(url, "GET", mapOf("User-Agent" to "laden-ops"), null, httpsOnly = false, timeoutMs = 8_000)
        if (failure != null) return fail(scrub(failure))
        val cap = if (status >= 400) 80_000 else 200_000
        val clipped = data.copyOf(min(data.size, cap))
        val text = clipped.toString(Charsets.UTF_8)
        val body: Any = try {
            val trimmed = text.trim()
            when {
                trimmed.startsWith("{") -> JSONObject(trimmed)
                trimmed.startsWith("[") -> JSONArray(trimmed)
                else -> text.take(4000)
            }
        } catch (_: Exception) {
            text.take(4000)
        }
        return JSONObject().put("ok", true).put("status", status).put("body", body)
    }

    private fun aiChat(params: JSONObject): JSONObject {
        val key = str(params, "apiKey").trim()
        if (key.isEmpty()) return fail("missing api key")
        val requested = str(params, "provider")
        val which = if (requested == "groq" || requested == "openrouter") requested else "xai"
        val system = clip(str(params, "system"), 4000)
        val user = clip(str(params, "user"), 12000)
        val model = str(params, "model").trim().ifEmpty { null }
        val headers = HashMap<String, String>()
        headers["Authorization"] = "Bearer $key"
        headers["Content-Type"] = "application/json"
        headers["User-Agent"] = "laden-ops"
        val urlText: String
        val payload: JSONObject
        if (which == "xai") {
            urlText = "https://api.x.ai/v1/responses"
            payload = JSONObject()
                .put("model", model ?: "grok-4.7")
                .put(
                    "input",
                    JSONArray()
                        .put(JSONObject().put("role", "system").put("content", system))
                        .put(JSONObject().put("role", "user").put("content", user)),
                )
        } else {
            urlText = if (which == "groq") "https://api.groq.com/openai/v1/chat/completions" else "https://openrouter.ai/api/v1/chat/completions"
            if (which == "openrouter") {
                headers["HTTP-Referer"] = "https://laden.no"
                headers["X-Title"] = "Laden Ops Journal"
            }
            val fallback = if (which == "groq") "llama-3.3-70b-versatile" else "openrouter/free"
            payload = JSONObject()
                .put("model", model ?: fallback)
                .put("temperature", 0.4)
                .put("max_tokens", 700)
                .put(
                    "messages",
                    JSONArray()
                        .put(JSONObject().put("role", "system").put("content", system))
                        .put(JSONObject().put("role", "user").put("content", user)),
                )
        }
        val url = URL(urlText)
        val body = payload.toString().toByteArray(Charsets.UTF_8)
        val (status, data, failure) = transfer(url, "POST", headers, body, httpsOnly = true, timeoutMs = 45_000)
        if (failure != null) return fail(scrub(failure))
        val text = data.copyOf(min(data.size, 200_000)).toString(Charsets.UTF_8)
        if (status >= 400) return fail(scrub("$which $status: ${text.take(180)}"))
        val parsed = try {
            JSONObject(text)
        } catch (_: Exception) {
            return fail("bad AI JSON")
        }
        val content = aiText(parsed)
        if (content.isEmpty()) return fail("empty AI response")
        return JSONObject().put("ok", true).put("text", content)
    }

    private fun aiText(data: JSONObject): String {
        val direct = data.optString("output_text", "").trim()
        if (direct.isNotEmpty()) return direct
        val chunks = ArrayList<String>()
        val output = data.optJSONArray("output")
        if (output != null) {
            for (i in 0 until output.length()) {
                val row = output.optJSONObject(i) ?: continue
                val content = row.optJSONArray("content") ?: continue
                for (j in 0 until content.length()) {
                    val block = content.optJSONObject(j) ?: continue
                    val text = block.optString("text", "")
                    if (text.isNotEmpty()) chunks.add(text)
                }
            }
        }
        if (chunks.isNotEmpty()) return chunks.joinToString("\n").trim()
        val choices = data.optJSONArray("choices") ?: return ""
        val first = choices.optJSONObject(0) ?: return ""
        val message = first.optJSONObject("message") ?: return ""
        return message.optString("content", "").trim()
    }

    private fun transfer(
        start: URL,
        method: String,
        headers: Map<String, String>,
        body: ByteArray?,
        httpsOnly: Boolean,
        timeoutMs: Int,
    ): Triple<Int, ByteArray, String?> {
        var current = start
        var verb = method
        var payload = body
        var headerMap = headers
        for (hop in 0 until 5) {
            val conn = (current.openConnection() as HttpURLConnection).apply {
                instanceFollowRedirects = false
                connectTimeout = timeoutMs
                readTimeout = timeoutMs
                requestMethod = verb
                for ((name, value) in headerMap) setRequestProperty(name, value)
            }
            try {
                if (payload != null && (verb == "POST" || verb == "PUT")) {
                    conn.doOutput = true
                    conn.outputStream.use { it.write(payload) }
                }
                val status = conn.responseCode
                if (status in 300..399) {
                    val location = conn.getHeaderField("Location") ?: return Triple(0, ByteArray(0), "redirect missing")
                    val next = URL(current, location)
                    val scheme = next.protocol.lowercase()
                    if (scheme != "https" && !(!httpsOnly && scheme == "http")) {
                        return Triple(0, ByteArray(0), "redirect blocked")
                    }
                    if (!next.host.equals(current.host, ignoreCase = true)) {
                        headerMap = headerMap.filterKeys { !it.equals("Authorization", ignoreCase = true) }
                    }
                    current = next
                    if (status == 303 || (status == 302 && verb != "GET")) {
                        verb = "GET"
                        payload = null
                    }
                    continue
                }
                val stream: InputStream? = if (status >= 400) conn.errorStream else conn.inputStream
                val bytes = readCap(stream, 200_000)
                return Triple(status, bytes, null)
            } catch (exc: Exception) {
                return Triple(0, ByteArray(0), exc.message ?: "request failed")
            } finally {
                conn.disconnect()
            }
        }
        return Triple(0, ByteArray(0), "too many redirects")
    }

    private fun readCap(stream: InputStream?, cap: Int): ByteArray {
        if (stream == null) return ByteArray(0)
        stream.use { input ->
            val buf = ByteArrayOutputStream()
            val tmp = ByteArray(8192)
            while (buf.size() < cap) {
                val n = input.read(tmp, 0, min(tmp.size, cap - buf.size()))
                if (n < 0) break
                buf.write(tmp, 0, n)
            }
            return buf.toByteArray()
        }
    }

    /**
     * Fingerprint, face unlock, or the device passcode.
     * The typed vault field is not compared and not stored.
     */
    private fun verifyOwner(reply: (JSONObject) -> Unit) {
        val activity = context as? AppCompatActivity
        if (activity == null) {
            reply(fail("A fingerprint, face unlock, or the device passcode is required.").put("method", "local-authentication"))
            return
        }
        val start = Runnable {
            val manager = BiometricManager.from(activity)
            val code = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                manager.canAuthenticate(
                    BiometricManager.Authenticators.BIOMETRIC_WEAK or BiometricManager.Authenticators.DEVICE_CREDENTIAL
                )
            } else {
                @Suppress("DEPRECATION")
                manager.canAuthenticate()
            }
            if (code != BiometricManager.BIOMETRIC_SUCCESS) {
                reply(
                    fail("A fingerprint, face unlock, or the device passcode is required.")
                        .put("method", "local-authentication")
                )
                return@Runnable
            }
            val executor = ContextCompat.getMainExecutor(activity)
            val prompt = BiometricPrompt(activity, executor, object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                    reply(JSONObject().put("ok", true).put("method", "local-authentication"))
                }

                override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                    reply(
                        JSONObject()
                            .put("ok", false)
                            .put("method", "local-authentication")
                            .put("error", errString.toString().ifBlank { "The device lock was not accepted." })
                    )
                }
            })
            val info = BiometricPrompt.PromptInfo.Builder().setTitle("Unlock the Laden Ops vault.")
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                info.setAllowedAuthenticators(
                    BiometricManager.Authenticators.BIOMETRIC_WEAK or BiometricManager.Authenticators.DEVICE_CREDENTIAL
                )
            } else {
                @Suppress("DEPRECATION")
                info.setDeviceCredentialAllowed(true)
            }
            prompt.authenticate(info.build())
        }
        if (android.os.Looper.myLooper() == android.os.Looper.getMainLooper()) start.run() else activity.runOnUiThread(start)
    }

    private fun savedShortcut(): String {
        val file = File(support, "shortcut")
        val line = try {
            file.readText(Charsets.UTF_8).lineSequence().firstOrNull()?.trim().orEmpty()
        } catch (_: Exception) {
            ""
        }
        return line.ifEmpty { "Ctrl+`" }
    }

    private fun setShortcut(accel: String): JSONObject {
        var cleaned = accel.trim().ifEmpty { "Ctrl+`" }
        cleaned = cleaned.replace("CommandOrControl", "Ctrl").replace("Control", "Ctrl")
        return try {
            support.mkdirs()
            File(support, "shortcut").writeText("$cleaned\n", Charsets.UTF_8)
            JSONObject().put("ok", true).put("shortcut", cleaned).put("backend", "android")
        } catch (exc: Exception) {
            fail(errorText(exc)).put("shortcut", cleaned).put("backend", "android")
        }
    }

    private fun str(params: JSONObject, key: String): String {
        if (!params.has(key) || params.isNull(key)) return ""
        return params.optString(key, "")
    }

    private fun clip(text: String, limit: Int): String = if (text.length <= limit) text else text.take(limit)

    private fun rounded(value: Double, places: Int): Double {
        val factor = 10.0.pow(places)
        return round(value * factor) / factor
    }

    private fun scrub(text: String): String {
        return Regex("(?i)(?:sk-|xai-|gsk_|sk-or-|Bearer\\s+)[A-Za-z0-9_\\-]{4,}").replace(text, "[redacted]")
    }

    private fun errorText(error: Exception): String {
        if (error is VaultEscape) return error.message ?: "path escapes vault workspace"
        return error.message ?: "request failed"
    }

    private fun fail(message: String): JSONObject = JSONObject().put("ok", false).put("error", message)

    private class VaultEscape : Exception("path escapes vault workspace")
}
