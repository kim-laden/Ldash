import Foundation

/// Printable sheet used by case PDFs and budget PDFs. Same layout as host.py `render_sheet`.
enum PdfSheet {
    static func write(to url: URL, title: String, sections: [(String, String)], banner: String) throws {
        let pageW = 595.0
        let pageH = 842.0
        var items: [(String, String)] = [("title", title.isEmpty ? "sheet" : title)]
        for (heading, body) in sections {
            let label = heading.isEmpty ? "SECTION" : heading
            items.append(("h", String(label.prefix(40))))
            for line in wrap(body, limit: 92) {
                items.append(("b", line))
            }
            items.append(("gap", ""))
        }

        var pages: [[String]] = []
        var commands: [String] = []
        var y = 774.0

        func newPage() {
            if !commands.isEmpty {
                pages.append(commands)
            }
            commands = []
            y = 774.0
        }

        newPage()
        let stepFor = ["title": 28.0, "h": 20.0, "b": 13.0, "gap": 6.0]
        for (kind, text) in items {
            let step = stepFor[kind] ?? 13.0
            if y - step < 54 {
                newPage()
            }
            if kind == "title" {
                commands.append("BT /F2 16 Tf 0.05 0.07 0.09 rg 48 \(num(y)) Td (\(escape(text))) Tj ET")
            } else if kind == "h" {
                commands.append("0 0.62 0.38 rg 48 \(num(y - 3)) 499 1.2 re f")
                commands.append("BT /F2 10 Tf 0 0.45 0.28 rg 48 \(num(y)) Td (\(escape(text))) Tj ET")
            } else if kind == "b" {
                let shown = text.isEmpty ? " " : text
                commands.append("BT /F1 10 Tf 0.08 0.10 0.14 rg 48 \(num(y)) Td (\(escape(shown))) Tj ET")
            }
            y -= step
        }
        if !commands.isEmpty {
            pages.append(commands)
        }
        if pages.isEmpty {
            pages.append([])
        }

        var objects: [Data] = []
        func add(_ data: Data) -> Int {
            objects.append(data)
            return objects.count
        }
        func add(_ text: String) -> Int {
            add(Data(latin1(text)))
        }

        let fontRegular = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        let fontBold = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
        var contentIDs: [Int] = []
        let total = pages.count
        let sheetTitle = String(title.prefix(60))
        for (index, content) in pages.enumerated() {
            let header = [
                "0.02 0.027 0.039 rg",
                "0 \(num(pageH - 36)) \(num(pageW)) 36 re f",
                "0 1 0.616 rg",
                "0 \(num(pageH - 38)) \(num(pageW)) 2 re f",
                "BT /F2 9 Tf 0 1 0.616 rg 48 \(num(pageH - 22)) Td (LADEN OPS) Tj ET",
                "BT /F2 9 Tf 0.90 0.945 1 rg 118 \(num(pageH - 22)) Td (\(escape(banner))) Tj ET",
                "BT /F1 8 Tf 0.55 0.61 0.70 rg 48 32 Td (\(escape(sheetTitle + "  ·  \(index + 1) / \(total)"))) Tj ET",
            ]
            let payload = Data(latin1((header + content).joined(separator: "\n") + "\n"))
            var stream = Data(latin1("<< /Length \(payload.count) >>\nstream\n"))
            stream.append(payload)
            stream.append(Data("endstream".utf8))
            contentIDs.append(add(stream))
        }

        let pagesID = add("<< /Type /Pages /Count 0 /Kids [] >>")
        let firstPage = objects.count + 1
        let kids = (0..<total).map { "\(firstPage + $0) 0 R" }.joined(separator: " ")
        objects[pagesID - 1] = Data(latin1("<< /Type /Pages /Count \(total) /Kids [\(kids)] >>"))
        for contentID in contentIDs {
            _ = add(
                "<< /Type /Page /Parent \(pagesID) 0 R "
                    + "/MediaBox [0 0 \(Int(pageW)) \(Int(pageH))] "
                    + "/Resources << /Font << /F1 \(fontRegular) 0 R /F2 \(fontBold) 0 R >> >> "
                    + "/Contents \(contentID) 0 R >>"
            )
        }
        let catalog = add("<< /Type /Catalog /Pages \(pagesID) 0 R >>")

        var out = Data("%PDF-1.4\n".utf8)
        var offsets = [0]
        for (number, object) in objects.enumerated() {
            offsets.append(out.count)
            out.append(Data("\(number + 1) 0 obj\n".utf8))
            out.append(object)
            out.append(Data("\nendobj\n".utf8))
        }
        let xref = out.count
        out.append(Data("xref\n0 \(objects.count + 1)\n".utf8))
        out.append(Data("0000000000 65535 f \n".utf8))
        for offset in offsets.dropFirst() {
            out.append(Data(String(format: "%010d 00000 n \n", offset).utf8))
        }
        out.append(Data("trailer << /Size \(objects.count + 1) /Root \(catalog) 0 R >>\nstartxref\n\(xref)\n%%EOF\n".utf8))
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try out.write(to: url, options: .atomic)
    }

    private static func num(_ value: Double) -> String {
        String(format: "%.1f", locale: Locale(identifier: "en_US_POSIX"), value)
    }

    private static func pdfText(_ value: String) -> String {
        var raw = value.replacingOccurrences(of: "\r\n", with: "\n").replacingOccurrences(of: "\r", with: "\n")
        let pairs = [
            ("\u{2014}", "-"),
            ("\u{2013}", "-"),
            ("\u{2018}", "'"),
            ("\u{2019}", "'"),
            ("\u{201C}", "\""),
            ("\u{201D}", "\""),
            ("\u{2026}", "..."),
            ("\u{00B7}", " - "),
            ("\u{2022}", "-"),
        ]
        for (src, dst) in pairs {
            raw = raw.replacingOccurrences(of: src, with: dst)
        }
        String(data: Data(latin1(raw)), encoding: .isoLatin1) ?? ""
    }

    private static func escape(_ value: String) -> String {
        pdfText(value)
            .replacingOccurrences(of: "\\", with: "\\\\")
            .replacingOccurrences(of: "(", with: "\\(")
            .replacingOccurrences(of: ")", with: "\\)")
    }

    private static func wrap(_ text: String, limit: Int) -> [String] {
        var lines: [String] = []
        for para in pdfText(text).split(separator: "\n", omittingEmptySubsequences: false) {
            let paragraph = String(para)
            if paragraph.trimmingCharacters(in: .whitespaces).isEmpty {
                lines.append("")
                continue
            }
            var current = ""
            for rawWord in paragraph.split(whereSeparator: { $0.isWhitespace }) {
                var word = String(rawWord)
                while word.count > limit {
                    if !current.isEmpty {
                        lines.append(current)
                        current = ""
                    }
                    lines.append(String(word.prefix(limit)))
                    word = String(word.dropFirst(limit))
                }
                let trial = current.isEmpty ? word : current + " " + word
                if trial.count <= limit {
                    current = trial
                } else {
                    lines.append(current)
                    current = word
                }
            }
            if !current.isEmpty {
                lines.append(current)
            }
        }
        return lines.isEmpty ? [""] : lines
    }

    /// Latin-1, with unmappable characters replaced, matching Python `encode("latin-1", "replace")`.
    private static func latin1(_ value: String) -> [UInt8] {
        var bytes: [UInt8] = []
        bytes.reserveCapacity(value.utf8.count)
        for scalar in value.unicodeScalars {
            if scalar.value <= 255 {
                bytes.append(UInt8(scalar.value))
            } else {
                bytes.append(0x3F)
            }
        }
        return bytes
    }
}
