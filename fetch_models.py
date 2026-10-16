"""Download Whisper models into models/<name>, to be packed into the app by the build.

    python fetch_models.py turbo large-v3
"""
import shutil
import sys
from pathlib import Path

from faster_whisper.utils import _MODELS
from huggingface_hub import snapshot_download

MODEL_FILES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]

for name in sys.argv[1:] or ["turbo", "large-v3"]:
    target = Path(__file__).parent / "models" / name
    if (target / "model.bin").is_file():
        print(f"{name}: already in {target}")
        continue
    print(f"{name}: downloading...")
    snapshot_download(_MODELS[name], local_dir=target, allow_patterns=MODEL_FILES)
    shutil.rmtree(target / ".cache", ignore_errors=True)  # download bookkeeping, not needed in the app
    print(f"{name}: saved in {target}")
