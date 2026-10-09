// pdf_tool.swift — extract text + render pages from a PDF (macOS PDFKit).
// Usage: pdf_tool <in.pdf> <outdir>
//   - prints a JSON array to stdout: [{page, text, image}]
//   - writes page<NN>.jpg renders into outdir (max ~1500px, JPEG q0.8)
// Build: swiftc -O pdf_tool.swift -o pdf_tool   (requires Xcode Command Line Tools)
import Foundation
import PDFKit
import ImageIO
import AppKit

struct StderrStream: TextOutputStream {
    mutating func write(_ s: String) { FileHandle.standardError.write(Data(s.utf8)) }
}
var errOut = StderrStream()

guard CommandLine.arguments.count >= 3 else {
    print("usage: pdf_tool <in.pdf> <outdir>", to: &errOut); exit(1)
}
let inPath = CommandLine.arguments[1]
let outDir = CommandLine.arguments[2]
try FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)
guard let doc = PDFDocument(url: URL(fileURLWithPath: inPath)) else {
    print("cannot open \(inPath)", to: &errOut); exit(1)
}

func saveJpeg(_ ns: NSImage, to path: String) -> Bool {
    guard let tiff = ns.tiffRepresentation,
          let rep = NSBitmapImageRep(data: tiff),
          let jpg = rep.representation(using: .jpeg,
              properties: [.compressionFactor: 0.8]) else { return false }
    do { try jpg.write(to: URL(fileURLWithPath: path)); return true }
    catch { return false }
}

func collectOutline(_ outline: PDFOutline?, prefix: String) -> [[String: Any]] {
    guard let outline else { return [] }
    var rows: [[String: Any]] = []
    for i in 0..<outline.numberOfChildren {
        guard let child = outline.child(at: i) else { continue }
        let label = (child.label ?? "").trimmingCharacters(in: .whitespaces)
        let path = prefix.isEmpty ? label : (label.isEmpty ? prefix : "\(prefix) › \(label)")
        var page = -1
        if let dest = child.destination, let p = dest.page, let doc = p.document {
            page = doc.index(for: p)  // 0-based
        } else if let go = child.action as? PDFActionGoTo,
                  let p = go.destination.page, let doc = p.document {
            page = doc.index(for: p)
        }
        if page >= 0 && !label.isEmpty {
            rows.append(["page": page, "label": path])
        }
        rows.append(contentsOf: collectOutline(child, prefix: path))
    }
    return rows
}

var out: [[String: Any]] = []
for i in 0..<doc.pageCount {
    guard let page = doc.page(at: i) else { continue }
    let n = i + 1
    let text = page.string ?? ""
    let bounds = page.bounds(for: .mediaBox)
    let scale: CGFloat = min(1.0, 1500.0 / max(bounds.width, bounds.height))
    let w = Int(bounds.width * scale), h = Int(bounds.height * scale)
    let imgPath = "\(outDir)/page\(n).jpg"
    // AppKit path: thumbnail() handles PDF -> bitmap orientation correctly
    let thumb = page.thumbnail(of: NSSize(width: w, height: h), for: .mediaBox)
    _ = saveJpeg(thumb, to: imgPath)
    out.append(["page": n, "text": text, "image": imgPath])
}
let data = try JSONSerialization.data(
    withJSONObject: ["pages": out, "sections": collectOutline(doc.outlineRoot, prefix: "")],
    options: [.prettyPrinted, .withoutEscapingSlashes])
print(String(data: data, encoding: .utf8)!)
