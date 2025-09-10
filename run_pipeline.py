# Orchestration de bout en bout :
#  1) Télécharger les VODs < 48h (Download_recent_vods.py)
#  2) Découper automatiquement en parties LoL (split_lol_games.py)
#  3) Créer les miniatures (make_thumbnail.py)
#  4) Générer les métadonnées (generate_metadata.py)
#  5) Uploader sur YouTube (upload_youtube.py)
#
# Dépendances installées dans le venv :
#   pip install -r requirements.txt
#
# Exemples :
#   python run_pipeline.py --profile main
#   python run_pipeline.py --profile alt --bg nebula.jpg --skip-upload
#   python run_pipeline.py --profile main --only "Game_01*.mp4" --reauth

from __future__ import annotations
import os
import sys
import json
import time
import shutil
import argparse
import subprocess
from pathlib import Path
from typing import Iterable, List, Dict, Optional, Tuple

REPO_DIR = Path(__file__).resolve().parent

def detect_config_dir() -> Path:
    try:
        import settings  # type: ignore
        cfg = getattr(settings, "CONFIG_DIR", None)
        if cfg:
            return Path(cfg).expanduser().resolve()
    except Exception:
        pass
    env_cfg = os.getenv("AUTOYT_CONFIG_DIR")
    if env_cfg:
        return Path(env_cfg).expanduser().resolve()
    return REPO_DIR / "config"

CONFIG_DIR = detect_config_dir()
CONFIG_DIR.mkdir(parents=True, exist_ok=True)

VODS_ROOT     = REPO_DIR / "vods"
EXPORTS_ROOT  = REPO_DIR / "exports"
STATE_DIR     = REPO_DIR / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

DOWNLOAD_SCRIPT = REPO_DIR / "Download_recent_vods.py"
SPLIT_SCRIPT    = REPO_DIR / "split_lol_games.py"
THUMB_SCRIPT    = REPO_DIR / "make_thumbnail.py"
META_SCRIPT     = REPO_DIR / "generate_metadata.py"
UPLOAD_SCRIPT   = REPO_DIR / "upload_youtube.py"

PYTHON_EXE = sys.executable 


def run(cmd: List[str], cwd: Optional[Path] = None, check: bool = True) -> subprocess.CompletedProcess:
    print("→", " ".join(cmd))
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=check)

def load_json(path: Path) -> Dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_json(path: Path, data: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

SPLIT_STATE = STATE_DIR / "split_done.json"

def mark_split_done(vod_path: Path) -> None:
    st = load_json(SPLIT_STATE)
    st[str(vod_path.resolve())] = True
    save_json(SPLIT_STATE, st)

def is_split_done(vod_path: Path) -> bool:
    st = load_json(SPLIT_STATE)
    return st.get(str(vod_path.resolve()), False) is True

def find_all_vods(root: Path) -> List[Path]:
    if not root.exists():
        return []
    return sorted(root.rglob("*.mp4"))

def export_dir_for_vod(vod_path: Path) -> Tuple[Path, str]:
    try:
        date_folder = vod_path.parent.name
        streamer    = vod_path.parent.parent.name
    except Exception:
        date_folder = "unknown-date"
        streamer    = "misc"
    export_dir = EXPORTS_ROOT / f"{streamer}_{date_folder}"
    return export_dir, streamer

def step_1_download_vods() -> None:
    print("▶️  Étape 1/5 : Download VODs <48h")
    if not DOWNLOAD_SCRIPT.exists():
        print(f"  ⚠️ Script absent: {DOWNLOAD_SCRIPT}")
        return
    try:
        run([PYTHON_EXE, str(DOWNLOAD_SCRIPT)])
    except subprocess.CalledProcessError as e:
        print(f"  ❌ Download_recent_vods.py a échoué (code {e.returncode}). On continue quand même.")

def step_2_split_all_new_vods(limit: Optional[int] = None) -> List[Path]:
    print("\n▶️  Étape 2/5 : Split des nouvelles VODs")
    new_export_dirs: List[Path] = []
    if not SPLIT_SCRIPT.exists():
        print(f"  ⚠️ Script absent: {SPLIT_SCRIPT}")
        return new_export_dirs

    vods = find_all_vods(VODS_ROOT)
    if not vods:
        print("  ℹ️ Aucune VOD trouvée.")
        return new_export_dirs

    count = 0
    for vod in vods:
        if limit is not None and count >= limit:
            break
        if is_split_done(vod):
            continue

        export_dir, streamer = export_dir_for_vod(vod)
        export_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n🎮 Split: {vod.name} → {export_dir}")
        try:
            run([PYTHON_EXE, str(SPLIT_SCRIPT), str(vod), str(export_dir)])
            mark_split_done(vod)
            if export_dir not in new_export_dirs:
                new_export_dirs.append(export_dir)
            count += 1
        except subprocess.CalledProcessError as e:
            print(f"  ❌ Split échoué ({vod.name}) — code {e.returncode}")
        except KeyboardInterrupt:
            print("  ⛔ Interrompu par l'utilisateur.")
            break

    if not new_export_dirs:
        print("  ℹ️ Rien de nouveau à découper.")
    return new_export_dirs

def step_3_make_thumbnails(export_dirs: Iterable[Path], bg: Optional[str]) -> None:
    print("\n▶️  Étape 3/5 : Miniatures")
    if not THUMB_SCRIPT.exists():
        print(f"  ⚠️ Script absent: {THUMB_SCRIPT}")
        return

    for d in export_dirs:
        if not d.exists():
            continue
        args = [PYTHON_EXE, str(THUMB_SCRIPT), str(d)]
        if bg:
            args += ["--bg", bg]
        try:
            print(f"🖼️  {d.name}")
            run(args)
        except subprocess.CalledProcessError as e:
            print(f"  ❌ Miniature échouée pour {d.name} — code {e.returncode}")

def step_4_generate_metadata(export_dirs: Iterable[Path]) -> None:
    print("\n▶️  Étape 4/5 : Métadonnées")
    if not META_SCRIPT.exists():
        print(f"  ⚠️ Script absent: {META_SCRIPT}")
        return

    for d in export_dirs:
        if not d.exists():
            continue
        try:
            print(f"📝  {d.name}")
            run([PYTHON_EXE, str(META_SCRIPT), str(d)])
        except subprocess.CalledProcessError as e:
            print(f"  ❌ Métadonnées échouées pour {d.name} — code {e.returncode}")

def step_5_upload_to_youtube(export_dirs: Iterable[Path], profile: str,
                             only_pattern: Optional[str], reauth: bool, dry_run: bool) -> None:
    print("\n▶️  Étape 5/5 : Upload YouTube")
    if not UPLOAD_SCRIPT.exists():
        print(f"  ⚠️ Script absent: {UPLOAD_SCRIPT}")
        return

    for d in export_dirs:
        if not d.exists():
            continue
        args = [
            PYTHON_EXE, str(UPLOAD_SCRIPT),
            str(d),
            "--profile", profile,
        ]
        if only_pattern:
            args += ["--only", only_pattern]
        if reauth:
            args += ["--reauth"]
        if dry_run:
            args += ["--dry-run"]
        try:
            print(f"📤  {d.name}")
            run(args)
        except subprocess.CalledProcessError as e:
            print(f"  ❌ Upload échoué pour {d.name} — code {e.returncode}")

def main():
    ap = argparse.ArgumentParser(
        description="Pipeline complet : Download VODs → Split → Thumbnails → Metadata → Upload YouTube"
    )
    ap.add_argument("--profile", required=False,
                    help="Profil/chaîne YouTube (correspond à client_secret_<profile>.json dans config/).")
    ap.add_argument("--bg", help="Nom/chemin d'un background pour make_thumbnail.py (optionnel).")
    ap.add_argument("--only", help="Pattern glob pour limiter les vidéos à uploader (ex: 'Game_01*.mp4').")
    ap.add_argument("--reauth", action="store_true", help="Forcer la réauth YouTube (choix de compte).")
    ap.add_argument("--dry-run", action="store_true", help="Simuler l'upload (aucun appel API).")
    ap.add_argument("--skip-upload", action="store_true", help="Ne pas faire l'étape 5 (upload).")
    ap.add_argument("--limit-split", type=int, default=None,
                    help="Limiter le nombre de VODs à découper sur ce run (debug).")
    ap.add_argument("--exports", help="Traiter uniquement ce dossier d'exports (au lieu de scanner les VODs).")
    args = ap.parse_args()

    step_1_download_vods()

    if args.exports:
        export_dirs = [Path(args.exports).resolve()]
        export_dirs = [d for d in export_dirs if d.exists()]
        if not export_dirs:
            print(f"❌ Dossier d'exports invalide: {args.exports}")
            return
    else:
        export_dirs = step_2_split_all_new_vods(limit=args.limit_split)

    if not export_dirs:
        print("\nℹ️ Aucune export-list à traiter pour les étapes 3–5.")
        return

    step_3_make_thumbnails(export_dirs, args.bg)

    step_4_generate_metadata(export_dirs)

    if args.skip_upload:
        print("\n⏭️  Étape upload sautée (--skip-upload).")
        return

    if not args.profile:
        print("\n❌ L'upload YouTube nécessite --profile (client_secret_<profile>.json dans config/).")
        print("   Exemple: python run_pipeline.py --profile main")
        return

    step_5_upload_to_youtube(export_dirs, args.profile, args.only, args.reauth, args.dry_run)

    print("\n✅ Pipeline terminé.")

if __name__ == "__main__":
    main()