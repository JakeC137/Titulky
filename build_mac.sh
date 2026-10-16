#!/bin/bash
# Builds dist/Titulky.app and dist/Titulky-mac-<arm64|x86_64>.dmg on a Mac:  bash build_mac.sh
#
# Needs Python 3.12 from python.org (it includes Tk, which the window uses):
#   https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg
# The app runs on Macs with the same kind of chip as the one building it (Apple Silicon or Intel).
#
# Optional settings:
#   TITULKY_MODELS="turbo large-v3"  Whisper models to put into the app (default "turbo")
#   MAC_SIGN_IDENTITY="Developer ID Application: Name (TEAMID)"
#                                sign with a paid Apple developer certificate, so other Macs open
#                                the app without warnings
#   MAC_NOTARY_PROFILE=titulky   also notarize the .dmg with Apple; set the profile up once with
#                                xcrun notarytool store-credentials titulky
set -euo pipefail
cd "$(dirname "$0")"

PYTHON=${PYTHON:-/Library/Frameworks/Python.framework/Versions/3.12/bin/python3}
if ! "$PYTHON" -c "import tkinter" 2>/dev/null; then
    echo "Python 3.12 from python.org is needed: https://www.python.org/ftp/python/3.12.10/python-3.12.10-macos11.pkg"
    exit 1
fi
export TITULKY_MODELS=${TITULKY_MODELS:-turbo}

"$PYTHON" -m venv .venv-mac
.venv-mac/bin/pip install --quiet --upgrade pip
.venv-mac/bin/pip install --quiet -r requirements.txt
.venv-mac/bin/python fetch_models.py $TITULKY_MODELS
.venv-mac/bin/pyinstaller --noconfirm --distpath dist --workpath build/mac titulky_mac.spec
rm -rf dist/Titulky  # PyInstaller's in-between folder; everything is in Titulky.app
codesign --verify --deep --strict dist/Titulky.app

# Disk image with the app, the README and a shortcut for dragging the app into Applications.
STAGE=build/mac/dmg
rm -rf "$STAGE"
mkdir -p "$STAGE"
ditto dist/Titulky.app "$STAGE/Titulky.app"
cp README.txt "$STAGE/"
ln -s /Applications "$STAGE/Applications"
DMG="dist/Titulky-mac-$(uname -m).dmg"
hdiutil create -volname Titulky -srcfolder "$STAGE" -format ULFO -ov "$DMG"

if [ -n "${MAC_NOTARY_PROFILE:-}" ]; then
    xcrun notarytool submit "$DMG" --keychain-profile "$MAC_NOTARY_PROFILE" --wait
    xcrun stapler staple "$DMG"
fi
echo "Built dist/Titulky.app and $DMG"
