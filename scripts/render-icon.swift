import AppKit

let root = URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "ModelHarbor/Assets.xcassets")
let image = NSImage(size: NSSize(width: 1024, height: 1024))
image.lockFocus()
NSColor(calibratedRed: 7/255, green: 81/255, blue: 84/255, alpha: 1).setFill()
NSBezierPath(roundedRect: NSRect(x: 40, y: 40, width: 944, height: 944), xRadius: 215, yRadius: 215).fill()
let route = NSBezierPath()
route.lineWidth = 62
route.lineCapStyle = .round
route.lineJoinStyle = .round
route.move(to: NSPoint(x: 512, y: 266))
route.line(to: NSPoint(x: 512, y: 505))
route.move(to: NSPoint(x: 280, y: 744))
route.line(to: NSPoint(x: 280, y: 615))
route.curve(to: NSPoint(x: 512, y: 505), controlPoint1: NSPoint(x: 280, y: 515), controlPoint2: NSPoint(x: 407, y: 505))
route.curve(to: NSPoint(x: 744, y: 615), controlPoint1: NSPoint(x: 617, y: 505), controlPoint2: NSPoint(x: 744, y: 515))
route.line(to: NSPoint(x: 744, y: 744))
NSColor(calibratedRed: 239/255, green: 247/255, blue: 237/255, alpha: 1).setStroke()
route.stroke()
for x in [280.0, 744.0] {
    NSColor(calibratedRed: 239/255, green: 247/255, blue: 237/255, alpha: 1).setFill()
    NSBezierPath(ovalIn: NSRect(x: x-53, y: 699, width: 106, height: 106)).fill()
}
NSColor(calibratedRed: 245/255, green: 188/255, blue: 83/255, alpha: 1).setFill()
NSBezierPath(ovalIn: NSRect(x: 444, y: 198, width: 136, height: 136)).fill()
image.unlockFocus()
func writePNG(side: Int, to url: URL) throws {
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: side, pixelsHigh: side,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    image.draw(in: NSRect(x: 0, y: 0, width: side, height: side))
    NSGraphicsContext.restoreGraphicsState()
    try bitmap.representation(using: .png, properties: [:])!.write(to: url)
}
let manifest = root.appendingPathComponent("AppIcon.appiconset/Contents.json")
let json = try JSONSerialization.jsonObject(with: Data(contentsOf: manifest)) as! [String: Any]
for item in json["images"] as! [[String: String]] {
    let side = Int(item["size"]!.split(separator: "x")[0])! * (item["scale"] == "2x" ? 2 : 1)
    try writePNG(side: side, to: manifest.deletingLastPathComponent().appendingPathComponent(item["filename"]!))
}
try writePNG(side: 512, to: root.appendingPathComponent("HarborMark.imageset/mark.png"))
try writePNG(side: 1024, to: URL(fileURLWithPath: "icon.png"))
try writePNG(side: 512, to: URL(fileURLWithPath: "site/assets/icon.png"))
