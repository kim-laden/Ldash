package org.laden.opsdash

import java.io.File
import java.util.Locale

/** Printable sheet used by case PDFs and budget PDFs. Same layout as host.py render_sheet. */
object PdfSheet {
    fun write(file: File, title: String, sections: List<Pair<String, String>>, banner: String) {
        val pageW = 595.0
        val pageH = 842.0
        val items = ArrayList<Pair<String, String>>()
        items.add("title" to (title.ifEmpty { "sheet" }))
        for ((heading, body) in sections) {
            items.add("h" to heading.ifEmpty { "SECTION" }.take(40))
            for (line in wrap(body, 92)) items.add("b" to line)
            items.add("gap" to "")
        }

        val pages = ArrayList<List<String>>()
        var commands = ArrayList<String>()
        var y = 774.0

        fun newPage() {
            if (commands.isNotEmpty()) pages.add(commands)
            commands = ArrayList<String>()
            y = 774.0
        }

        newPage()
        val stepFor = mapOf("title" to 28.0, "h" to 20.0, "b" to 13.0, "gap" to 6.0)
        for ((kind, text) in items) {
            val step = stepFor[kind] ?: 13.0
            if (y - step < 54) newPage()
            when (kind) {
                "title" -> commands.add("BT /F2 16 Tf 0.05 0.07 0.09 rg 48 ${num(y)} Td (${escape(text)}) Tj ET")
                "h" -> {
                    commands.add("0 0.62 0.38 rg 48 ${num(y - 3)} 499 1.2 re f")
                    commands.add("BT /F2 10 Tf 0 0.45 0.28 rg 48 ${num(y)} Td (${escape(text)}) Tj ET")
                }
                "b" -> {
                    val shown = text.ifEmpty { " " }
                    commands.add("BT /F1 10 Tf 0.08 0.10 0.14 rg 48 ${num(y)} Td (${escape(shown)}) Tj ET")
                }
            }
            y -= step
        }
        if (commands.isNotEmpty()) pages.add(commands)
        if (pages.isEmpty()) pages.add(emptyList())

        val objects = ArrayList<ByteArray>()
        fun add(data: ByteArray): Int {
            objects.add(data)
            return objects.size
        }
        fun add(text: String) = add(latin1(text))

        val fontRegular = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        val fontBold = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
        val contentIds = ArrayList<Int>()
        val total = pages.size
        val sheetTitle = title.take(60)
        for ((index, content) in pages.withIndex()) {
            val header = listOf(
                "0.02 0.027 0.039 rg",
                "0 ${num(pageH - 36)} ${num(pageW)} 36 re f",
                "0 1 0.616 rg",
                "0 ${num(pageH - 38)} ${num(pageW)} 2 re f",
                "BT /F2 9 Tf 0 1 0.616 rg 48 ${num(pageH - 22)} Td (LADEN OPS) Tj ET",
                "BT /F2 9 Tf 0.90 0.945 1 rg 118 ${num(pageH - 22)} Td (${escape(banner)}) Tj ET",
                "BT /F1 8 Tf 0.55 0.61 0.70 rg 48 32 Td (${escape("$sheetTitle  ·  ${index + 1} / $total")}) Tj ET",
            )
            val payload = latin1((header + content).joinToString("\n") + "\n")
            val stream = latin1("<< /Length ${payload.size} >>\nstream\n") + payload + "endstream".toByteArray(Charsets.US_ASCII)
            contentIds.add(add(stream))
        }

        val pagesId = add("<< /Type /Pages /Count 0 /Kids [] >>")
        val firstPage = objects.size + 1
        val kids = (0 until total).joinToString(" ") { "${firstPage + it} 0 R" }
        objects[pagesId - 1] = latin1("<< /Type /Pages /Count $total /Kids [$kids] >>")
        for (contentId in contentIds) {
            add(
                "<< /Type /Page /Parent $pagesId 0 R " +
                    "/MediaBox [0 0 ${pageW.toInt()} ${pageH.toInt()}] " +
                    "/Resources << /Font << /F1 $fontRegular 0 R /F2 $fontBold 0 R >> >> " +
                    "/Contents $contentId 0 R >>"
            )
        }
        val catalog = add("<< /Type /Catalog /Pages $pagesId 0 R >>")

        val out = ArrayList<Byte>()
        fun append(bytes: ByteArray) {
            for (b in bytes) out.add(b)
        }
        fun append(text: String) = append(text.toByteArray(Charsets.US_ASCII))
        append("%PDF-1.4\n")
        val offsets = ArrayList<Int>()
        offsets.add(0)
        for ((number, objectBytes) in objects.withIndex()) {
            offsets.add(out.size)
            append("${number + 1} 0 obj\n")
            append(objectBytes)
            append("\nendobj\n")
        }
        val xref = out.size
        append("xref\n0 ${objects.size + 1}\n")
        append("0000000000 65535 f \n")
        for (offset in offsets.drop(1)) {
            append(String.format(Locale.US, "%010d 00000 n \n", offset))
        }
        append("trailer << /Size ${objects.size + 1} /Root $catalog 0 R >>\nstartxref\n$xref\n%%EOF\n")
        file.parentFile?.mkdirs()
        file.writeBytes(ByteArray(out.size) { out[it] })
    }

    private fun num(value: Double): String = String.format(Locale.US, "%.1f", value)

    private fun pdfText(value: String): String {
        var raw = value.replace("\r\n", "\n").replace("\r", "\n")
        val pairs = listOf(
            "\u2014" to "-",
            "\u2013" to "-",
            "\u2018" to "'",
            "\u2019" to "'",
            "\u201C" to "\"",
            "\u201D" to "\"",
            "\u2026" to "...",
            "\u00B7" to " - ",
            "\u2022" to "-",
        )
        for ((src, dst) in pairs) raw = raw.replace(src, dst)
        return String(latin1(raw), Charsets.ISO_8859_1)
    }

    private fun escape(value: String): String {
        return pdfText(value)
            .replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
    }

    private fun wrap(text: String, limit: Int): List<String> {
        val lines = ArrayList<String>()
        for (para in pdfText(text).split('\n')) {
            if (para.isBlank()) {
                lines.add("")
                continue
            }
            var current = ""
            for (rawWord in para.split(Regex("\\s+"))) {
                var word = rawWord
                if (word.isEmpty()) continue
                while (word.length > limit) {
                    if (current.isNotEmpty()) {
                        lines.add(current)
                        current = ""
                    }
                    lines.add(word.take(limit))
                    word = word.drop(limit)
                }
                val trial = if (current.isEmpty()) word else "$current $word"
                if (trial.length <= limit) current = trial
                else {
                    lines.add(current)
                    current = word
                }
            }
            if (current.isNotEmpty()) lines.add(current)
        }
        return if (lines.isEmpty()) listOf("") else lines
    }

    /** Latin-1, with unmappable characters replaced, matching Python encode("latin-1", "replace"). */
    private fun latin1(value: String): ByteArray {
        val out = ArrayList<Byte>(value.length)
        var i = 0
        while (i < value.length) {
            val cp = value.codePointAt(i)
            i += Character.charCount(cp)
            out.add(if (cp <= 255) cp.toByte() else '?'.code.toByte())
        }
        return ByteArray(out.size) { out[it] }
    }
}
