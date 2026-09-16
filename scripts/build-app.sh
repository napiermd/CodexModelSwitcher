#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
identity="${MODEL_HARBOR_SIGNING_IDENTITY:-1E562E0CB11277309DC88DFED78D34A475DFC995}"
xcodebuild -project CodexModelSwitcher.xcodeproj -scheme CodexModelSwitcher -configuration Debug -derivedDataPath ../build CODE_SIGNING_ALLOWED=NO ENABLE_DEBUG_DYLIB=NO build
app='../build/Build/Products/Debug/Model Harbor.app'
codesign --force --sign "$identity" --options runtime "$app"
codesign --verify --deep --strict "$app"
