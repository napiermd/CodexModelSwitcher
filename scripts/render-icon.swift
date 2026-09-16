import AppKit

let root = URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "ModelHarbor/Assets.xcassets")
let master = URL(fileURLWithPath: "assets/brand/harbor-icon-master.png")
guard let image = NSImage(contentsOf: master) else {
    fatalError("Missing icon master at assets/brand/harbor-icon-master.png")
}
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
