#!/bin/bash
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
case "${1:-}" in
  --help) echo 'Usage: scripts/build-app.sh [--ad-hoc]'; echo 'Set MODEL_HARBOR_SIGNING_IDENTITY to a stable certificate name or SHA.'; echo 'An installed Model Harbor signing identity is reused when available.'; exit 0 ;;
  --ad-hoc) identity='-' ;;
  '') identity="${MODEL_HARBOR_SIGNING_IDENTITY:-}" ;;
  *) echo "Unknown option: $1" >&2; exit 2 ;;
esac
xcode_version="$(xcodebuild -version | sed -n 's/^Xcode //p')"
if [[ -z "$xcode_version" || "${xcode_version%%.*}" -lt 16 ]]; then
  echo 'Xcode 16 or newer is required. Select it with xcode-select before building.' >&2
  exit 1
fi
if [[ -z "$identity" && -d '/Applications/Model Harbor.app' ]]; then
  identity="$(codesign -dvv '/Applications/Model Harbor.app' 2>&1 | sed -n 's/^Authority=//p' | head -1)"
fi
if [[ -z "$identity" ]]; then
  echo 'Set MODEL_HARBOR_SIGNING_IDENTITY to your signing certificate, or use --ad-hoc for a local evaluation build.' >&2
  echo 'Keep the same signing identity across updates to preserve Keychain trust.' >&2
  exit 1
fi
if [[ "$identity" != '-' ]] && ! security find-identity -v -p codesigning | grep -Fq -- "$identity"; then
  echo 'The selected signing certificate is unavailable. Set MODEL_HARBOR_SIGNING_IDENTITY explicitly.' >&2
  exit 1
fi
build_dir="${MODEL_HARBOR_BUILD_DIR:-$repo_root/build}"
xcodebuild -project "$repo_root/ModelHarbor.xcodeproj" -scheme ModelHarbor -configuration Debug -derivedDataPath "$build_dir" CODE_SIGNING_ALLOWED=NO ENABLE_DEBUG_DYLIB=NO build
app="$build_dir/Build/Products/Debug/Model Harbor.app"
codesign --force --sign "$identity" --options runtime "$app"
codesign --verify --deep --strict "$app"
echo "Built: $app"
if [[ "$identity" == '-' ]]; then
  echo 'Evaluation build: ad-hoc signatures can change Keychain trust between builds. Use a stable certificate for saved accounts.'
fi
