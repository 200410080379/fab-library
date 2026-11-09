#!/bin/bash
set -euo pipefail
ProjectRoot="$(cd "$(dirname "$0")/.." && pwd)"
if [ "$(uname -s)" != Darwin ]; then echo 'This installer requires macOS.' >&2; exit 2; fi
if ! command -v xcrun >/dev/null; then echo 'Install Xcode Command Line Tools first.' >&2; exit 2; fi
if ! command -v uv >/dev/null; then echo 'Install uv first, or install this Python project in your own environment.' >&2; exit 2; fi
AppRoot="$HOME/Library/Application Support/fab-library/Fab Library Helper.app"
mkdir -p "$AppRoot/Contents/MacOS"
cat > "$AppRoot/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>local.fab-library.macbridge</string>
<key>CFBundleName</key><string>Fab Library Helper</string>
<key>CFBundleExecutable</key><string>fab-library-macbridge</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.3.0</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
xcrun swiftc -swift-version 5 -O -target "$(uname -m)-apple-macosx14.0" -framework Cocoa -framework WebKit "$ProjectRoot/macos/FabBridge.swift" -o "$AppRoot/Contents/MacOS/fab-library-macbridge"
codesign --force --sign - "$AppRoot" >/dev/null
"$AppRoot/Contents/MacOS/fab-library-macbridge" --self-test
uv tool install --force --no-cache "$ProjectRoot"
SkillRoot="${CODEX_HOME:-$HOME/.codex}/skills/fab-library"
if [ -d "$SkillRoot" ]; then
    BackupRoot="${CODEX_HOME:-$HOME/.codex}/skill-backups"
    mkdir -p "$BackupRoot"
    mv "$SkillRoot" "$BackupRoot/fab-library-$(date +%Y%m%d-%H%M%S)-$$"
fi
mkdir -p "$SkillRoot"
cp "$ProjectRoot/skills/fab-library/SKILL.md" "$SkillRoot/SKILL.md"
echo 'Installed. Run: fab-library store login'
