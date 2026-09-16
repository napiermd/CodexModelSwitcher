import AppKit
import SwiftUI

struct WindowTransparencyConfigurator: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async {
            configure(window: view.window)
        }
        return view
    }

    func updateNSView(_ nsView: NSView, context: Context) {
        DispatchQueue.main.async {
            configure(window: nsView.window)
        }
    }

    private func configure(window: NSWindow?) {
        guard window?.styleMask.contains(.titled) != true else { return }
        window?.isOpaque = false
        window?.backgroundColor = .clear
        window?.hasShadow = true
    }
}
