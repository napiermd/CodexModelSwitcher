import AppKit
let root = CommandLine.arguments[1]
let image = NSImage(size: NSSize(width: 1024, height: 1024))
image.lockFocus()
NSColor(calibratedRed: 0.025, green: 0.30, blue: 0.32, alpha: 1).setFill()
NSBezierPath(roundedRect: NSRect(x: 48, y: 48, width: 928, height: 928), xRadius: 210, yRadius: 210).fill()
let route = NSBezierPath()
route.lineWidth = 64
route.lineCapStyle = .round
route.lineJoinStyle = .round
route.move(to: NSPoint(x: 512, y: 258))
route.line(to: NSPoint(x: 512, y: 520))
route.move(to: NSPoint(x: 285, y: 744))
route.line(to: NSPoint(x: 285, y: 614))
route.curve(to: NSPoint(x: 512, y: 442), controlPoint1: NSPoint(x: 285, y: 480), controlPoint2: NSPoint(x: 512, y: 574))
route.curve(to: NSPoint(x: 739, y: 614), controlPoint1: NSPoint(x: 512, y: 574), controlPoint2: NSPoint(x: 739, y: 480))
route.line(to: NSPoint(x: 739, y: 744))
NSColor(calibratedRed: 0.90, green: 0.98, blue: 0.95, alpha: 1).setStroke()
route.stroke()
for x in [285.0, 739.0] {
 NSColor(calibratedRed: 0.90, green: 0.98, blue: 0.95, alpha: 1).setFill()
 NSBezierPath(ovalIn: NSRect(x: x-58, y: 706, width: 116, height: 116)).fill()
}
NSColor(calibratedRed: 1, green: 0.73, blue: 0.28, alpha: 1).setFill()
NSBezierPath(ovalIn: NSRect(x: 442, y: 196, width: 140, height: 140)).fill()
image.unlockFocus()
let manifest = URL(fileURLWithPath: root).appendingPathComponent("AppIcon.appiconset/Contents.json")
let json = try JSONSerialization.jsonObject(with: Data(contentsOf: manifest)) as! [String: Any]
for item in json["images"] as! [[String: String]] {
 let side = Int(item["size"]!.split(separator: "x")[0])! * (item["scale"] == "2x" ? 2 : 1)
 let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: side, pixelsHigh: side, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
 NSGraphicsContext.saveGraphicsState()
 NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
 image.draw(in: NSRect(x: 0, y: 0, width: side, height: side))
 NSGraphicsContext.restoreGraphicsState()
 try bitmap.representation(using: .png, properties: [:])!.write(to: manifest.deletingLastPathComponent().appendingPathComponent(item["filename"]!))
}
