# PyInstaller recipe for the Mac app, Titulky.app. Build it with build_mac.sh on a Mac.
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH)
# turbo only by default: large-v3 is very slow without an NVIDIA card and twice the size.
MODELS = os.environ.get("TITULKY_MODELS", "turbo").split()
# A Developer ID lets other Macs open the app without warnings; without one it gets an ad-hoc signature.
IDENTITY = os.environ.get("MAC_SIGN_IDENTITY") or None

# The Whisper models go inside the app, so nothing has to be downloaded.
datas = collect_data_files("faster_whisper") + [
    (str(ROOT / "models" / m), f"models/{m}") for m in MODELS]

a = Analysis([str(ROOT / "app.py")], pathex=[str(ROOT)], datas=datas,
             binaries=collect_dynamic_libs("ctranslate2"))
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Titulky", console=False,
          argv_emulation=False,  # app.py takes files dropped on the icon from Tk instead
          codesign_identity=IDENTITY,
          entitlements_file=str(ROOT / "mac" / "entitlements.plist") if IDENTITY else None)
coll = COLLECT(exe, a.binaries, a.datas, name="Titulky")
app = BUNDLE(
    coll,
    name="Titulky.app",
    bundle_identifier="cz.titulky.app",
    info_plist={
        "CFBundleDisplayName": "Titulky",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "14.0",  # what the bundled PyAV and onnxruntime need
        "NSAppSleepDisabled": True,  # keep working at full speed while the window is hidden
        # Lets Finder drop videos, sound and .srt files on the app's icon.
        "CFBundleDocumentTypes": [
            {"CFBundleTypeName": "Video or sound", "CFBundleTypeRole": "Viewer",
             "LSHandlerRank": "Alternate",
             "LSItemContentTypes": ["public.movie", "public.audio", "public.audiovisual-content"]},
            {"CFBundleTypeName": "Subtitles and other video", "CFBundleTypeRole": "Viewer",
             "LSHandlerRank": "Alternate",
             "CFBundleTypeExtensions": ["srt", "mkv", "webm", "ts", "m2ts", "mts", "avi", "wmv",
                                        "flv", "vob", "ogv", "divx"]},
        ],
    },
)
