from __future__ import annotations
import os, shutil
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent

CONFIG_DIR  = PROJECT_ROOT / "config"
EXPORTS_DIR = PROJECT_ROOT / "exports"
VODS_DIR    = PROJECT_ROOT / "vods"
STATE_DIR   = PROJECT_ROOT / "state"
ASSETS_DIR  = PROJECT_ROOT / "assets"

ENV_PATH_CONFIG = CONFIG_DIR / ".env"
ENV_PATH_ROOT   = PROJECT_ROOT / ".env"
if ENV_PATH_CONFIG.exists():
    load_dotenv(str(ENV_PATH_CONFIG))
elif ENV_PATH_ROOT.exists():
    load_dotenv(str(ENV_PATH_ROOT))

for d in (CONFIG_DIR, EXPORTS_DIR, VODS_DIR, STATE_DIR):
    d.mkdir(parents=True, exist_ok=True)

def get_ffmpeg() -> str:
    env = os.getenv("FFMPEG_BIN")
    if env and Path(env).exists():
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise FileNotFoundError(
        "ffmpeg introuvable. Installe-le (winget/choco/brew/apt) ou renseigne FFMPEG_BIN dans .env."
    )

def get_tesseract_cmd() -> str | None:
    env = os.getenv("TESSERACT_CMD")
    if env and Path(env).exists():
        return env
    found = shutil.which("tesseract")
    if found:
        return found
    win_default = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if win_default.exists():
        return str(win_default)
    return None
