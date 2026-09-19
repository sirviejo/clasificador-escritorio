// extraer_texto — texto de imágenes (OCR con Vision) y PDFs (PDFKit).
// Uso: extraer_texto <max_caracteres> <archivo>...   → una línea JSON por archivo
import AppKit
import Foundation
import PDFKit
import Vision

let args = CommandLine.arguments
guard args.count >= 3, let limit = Int(args[1]) else {
    FileHandle.standardError.write("uso: extraer_texto <max_caracteres> <archivo>...\n".data(using: .utf8)!)
    exit(2)
}

func ocr(_ image: CGImage) -> String {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["es-ES", "en-US"]
    request.usesLanguageCorrection = true
    try? VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
    return (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
}

func text(of url: URL) -> String {
    if url.pathExtension.lowercased() == "pdf" {
        guard let doc = PDFDocument(url: url) else { return "" }
        var out = ""
        for i in 0..<min(doc.pageCount, 3) where out.count < limit {
            out += (doc.page(at: i)?.string ?? "") + "\n"
        }
        // PDF escaneado: sin capa de texto, se hace OCR de la primera página
        if out.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, let page = doc.page(at: 0) {
            let thumb = page.thumbnail(of: NSSize(width: 1600, height: 2000), for: .mediaBox)
            if let cg = thumb.cgImage(forProposedRect: nil, context: nil, hints: nil) { out = ocr(cg) }
        }
        return out
    }
    guard let image = NSImage(contentsOf: url),
          let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return "" }
    return ocr(cg)
}

for path in args.dropFirst(2) {
    let extracted = String(text(of: URL(fileURLWithPath: path)).prefix(limit))
    let line = try! JSONSerialization.data(withJSONObject: ["path": path, "text": extracted])
    print(String(data: line, encoding: .utf8)!)
}
