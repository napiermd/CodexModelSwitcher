import AppKit
import CoreText

let root = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
for name in ["newsreader", "instrumentsans"] {
    CTFontManagerRegisterFontsForURL(root.appendingPathComponent("site/assets/fonts/\(name).ttf") as CFURL, .process, nil)
}
let image = NSImage(size: NSSize(width: 1200, height: 630))
image.lockFocus()
NSColor(calibratedRed: 245/255, green: 243/255, blue: 235/255, alpha: 1).setFill()
NSBezierPath(rect: NSRect(x: 0, y: 0, width: 1200, height: 630)).fill()
let ink = NSColor(calibratedRed: 25/255, green: 59/255, blue: 59/255, alpha: 1)
func text(_ s: String, x: CGFloat, y: CGFloat, size: CGFloat, serif: Bool = false) {
    let family = serif ? "Newsreader" : "Instrument Sans"
    let font = NSFont(name: family, size: size) ?? (serif ? NSFont(name: "Georgia", size: size)! : NSFont.systemFont(ofSize: size))
    (s as NSString).draw(at: NSPoint(x: x, y: y), withAttributes: [.font: font, .foregroundColor: ink])
}
NSImage(contentsOf: root.appendingPathComponent("icon.png"))!.draw(in: NSRect(x: 72, y: 464, width: 74, height: 74))
text("Model Harbor", x: 165, y: 487, size: 32)
text("Keep your tasks.", x: 78, y: 322, size: 83, serif: true)
text("Change your models.", x: 78, y: 222, size: 83, serif: true)
text("A macOS companion for Codex.", x: 82, y: 150, size: 27)
text("By Andrew Napier  ·  MIT licensed  ·  github.com/napiermd/model-harbor", x: 82, y: 69, size: 20)
image.unlockFocus()
let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 1200, pixelsHigh: 630, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
image.draw(in: NSRect(x: 0, y: 0, width: 1200, height: 630))
NSGraphicsContext.restoreGraphicsState()
try bitmap.representation(using: .png, properties: [:])!.write(to: root.appendingPathComponent("site/assets/social.png"))
