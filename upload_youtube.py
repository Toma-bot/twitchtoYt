# Uploade toutes les .mp4 d'un dossier exports/* vers YouTube (non répertorié),
# applique la miniature .jpg si présente et génère des métadonnées depuis config/players.json.
#
# Dépendances:
#   pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
#
# Auth multi-profils:
#   - client_secret_<profile>.json dans config/
#   - youtube_token_<profile>.json (créé après 1ère autorisation)
#
# Usage:
#   python upload_youtube.py <export_dir> --profile supa
#   python upload_youtube.py <export_dir> --profile supa --only "Game_01*.mp4"
#   python upload_youtube.py <export_dir> --profile supa --dry-run
#   python upload_youtube.py <export_dir> --profile supa --reauth

from __future__ import annotations
import os
import re
import json
import time
import argparse
from pathlib import Path
from typing import Tuple, Dict, Any, Optional

def _detect_config_dir() -> Path:
    try:
        import settings 
        cfg = getattr(settings, "CONFIG_DIR", None)
        if cfg:
            return Path(cfg).expanduser().resolve()
    except Exception:
        pass
    env_cfg = os.getenv("AUTOYT_CONFIG_DIR")
    if env_cfg:
        return Path(env_cfg).expanduser().resolve()
    return Path(__file__).resolve().parent / "config"

CONFIG_DIR: Path = _detect_config_dir()
PLAYERS_JSON: Path = CONFIG_DIR / "players.json"

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

def paths_for_profile(profile: str) -> Tuple[Path, Path]:
    """
    Retourne (client_secret_path, token_path) pour un profil donné.
    """
    client = CONFIG_DIR / f"client_secret_{profile}.json"
    token  = CONFIG_DIR / f"youtube_token_{profile}.json"
    return client, token

def get_youtube_service(profile: str, force_reauth: bool=False):
    """
    Ouvre un navigateur pour choisir le compte/chaîne lors de la 1ère auth
    ou si --reauth est passé.
    """
    client_secret, token_file = paths_for_profile(profile)
    assert client_secret.exists(), (
        f"[YouTube] client_secret introuvable pour le profil '{profile}':\n"
        f"  {client_secret}\n"
        f"→ Télécharge le JSON OAuth 2.0 (Type: Application de bureau) et enregistre-le à ce chemin."
    )
    creds: Optional[Credentials] = None

    if not force_reauth and token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token and not force_reauth:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json(), encoding="utf-8")

    return build("youtube", "v3", credentials=creds)

def load_players() -> Tuple[Dict[str, Any], Dict[str, str]]:
    """
    Retourne (players_db, alias_index).
    alias_index mappe alias.lower() -> clé du joueur (ex: "supa_lol" -> "Supa")
    """
    if not PLAYERS_JSON.exists():
        return {}, {}
    try:
        data = json.loads(PLAYERS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}, {}

    alias_index: Dict[str, str] = {}
    for key, meta in data.items():
        alias_index[key.lower()] = key 
        for a in meta.get("aliases", []):
            alias_index[a.lower()] = key
    return data, alias_index

def detect_player_from_dir(export_dir: Path, alias_index: Dict[str, str]) -> Optional[str]:
    """
    Exemple: 'supa_lol_2025-09-06' → 'supa_lol' → 'Supa' via alias_index
    """
    name = export_dir.name
    m = re.match(r"(.+)_\d{4}-\d{2}-\d{2}$", name)
    base = (m.group(1) if m else name).lower()
    if base in alias_index:
        return alias_index[base]
    for alias, key in alias_index.items():
        if alias in base:
            return key
    return None

def generate_metadata(export_dir: Path, video_path: Path,
                      players_db: Dict[str, Any], player_key: Optional[str]):
    """
    Fabrique: title, description, tags, categoryId, privacyStatus
    → privacyStatus = 'unlisted' (non répertorié).
    """
    privacy_status = "unlisted"
    category_id = "20" 

    if player_key and player_key in players_db:
        p = players_db[player_key]
        display = player_key
        team = p.get("team")
        role = p.get("role") or p.get("Role")

        base_title = f"{display} stream" if not team else f"{display} ({team}) stream"
        m = re.search(r"Game_(\d+)$", video_path.stem)
        suffix = f" — Highlights #{m.group(1)}" if m else " — Highlights"
        title = f"{base_title}{suffix}"

        tags = ["League of Legends", "LoL", "Highlights", display]
        if team: tags.append(team)
        if role: tags.append(role)
        tags += ["Ranked", "SoloQ", "Twitch VOD", "YouTube Gaming", "EUW"]

        desc_lines = [
            f"{display} — stream League of Legends",
            f"Équipe : {team}" if team else "",
            f"Rôle : {role}" if role else "",
            "",
            "👉 Abonne-toi pour plus de highlights !",
            "",
            "—",
            "Source : VOD Twitch (découpée automatiquement)",
            "Jeu : League of Legends",
        ]
        description = "\n".join([l for l in desc_lines if l])
    else:
        title = f"{export_dir.name} — Highlights"
        tags = ["League of Legends", "LoL", "Highlights", "Twitch VOD", "YouTube Gaming"]
        description = f"{export_dir.name} — Compilation de moments marquants.\n\nJeu : League of Legends"

    return title, description, tags, category_id, privacy_status

# ---------- Upload + miniature ----------
def resumable_upload(youtube, file_path: Path, title: str, description: str,
                     tags, category_id: str, privacy_status: str) -> str:
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:500],
            "categoryId": category_id,
            "defaultLanguage": "fr",
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(str(file_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    attempt = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                print(f"  ⏳ Upload {int(status.progress()*100)}%")
        except HttpError as e:
            attempt += 1
            if attempt > 5:
                raise
            wait = 5 * attempt
            print(f"  ⚠️ Erreur réseau/API ({e.resp.status}). Retry dans {wait}s…")
            time.sleep(wait)

    video_id = response["id"]
    print(f"  ✅ Upload OK — videoId={video_id}")
    return video_id

def set_thumbnail(youtube, video_id: str, thumb_path: Optional[Path]):
    if not thumb_path or not thumb_path.exists():
        print("  ℹ️ Miniature absente, étape ignorée.")
        return
    media = MediaFileUpload(str(thumb_path), mimetype="image/jpeg")
    youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
    print("  🖼️ Miniature appliquée.")

def upload_export_dir(export_dir: str, profile: str, only_pattern: Optional[str]=None,
                      dry_run: bool=False, force_reauth: bool=False):
    export_dir_p = Path(export_dir).resolve()
    assert export_dir_p.exists() and export_dir_p.is_dir(), f"Dossier invalide: {export_dir_p}"

    players_db, alias_index = load_players()
    player_key = detect_player_from_dir(export_dir_p, alias_index)

    youtube = get_youtube_service(profile, force_reauth=force_reauth)

    mp4s = sorted(export_dir_p.glob(only_pattern or "*.mp4"))
    if not mp4s:
        print("Aucune vidéo .mp4 trouvée dans ce dossier (filtre?).")
        return

    print(f"📤 {len(mp4s)} fichier(s) à uploader depuis: {export_dir_p}")
    print(f"👤 Profil: {profile}")
    print(f"🗂️  Config: {CONFIG_DIR}")

    for mp4 in mp4s:
        print(f"\n🎬 Fichier: {mp4.name}")
        title, description, tags, category_id, privacy = generate_metadata(export_dir_p, mp4, players_db, player_key)
        jpg = mp4.with_suffix(".jpg")
        print(f"  Titre: {title}")
        print(f"  Visibilité: {privacy}")
        print(f"  Miniature: {'oui' if jpg.exists() else 'non'}")

        if dry_run:
            print("  (dry-run) Upload simulé, aucun appel API.")
            continue

        try:
            video_id = resumable_upload(youtube, mp4, title, description, tags, category_id, privacy)
            set_thumbnail(youtube, video_id, jpg if jpg.exists() else None)
        except HttpError as e:
            print(f"❌ Échec upload {mp4.name} — {e}")
        except Exception as e:
            print(f"❌ Erreur inattendue {mp4.name} — {e}")

def main():
    ap = argparse.ArgumentParser(
        description="Uploader un dossier exports/* sur YouTube (non répertorié + miniature), multi-profils."
    )
    ap.add_argument("export_dir", help=r'Ex: C:\...\twitchtoYt\exports\supa_lol_2025-09-06')
    ap.add_argument("--profile", required=True, help="Nom du profil/chaîne (ex: supa, adam).")
    ap.add_argument("--only", help="Pattern glob pour filtrer les vidéos (ex: 'Game_01*.mp4').")
    ap.add_argument("--dry-run", action="store_true", help="N'upload pas, affiche ce qui serait fait.")
    ap.add_argument("--reauth", action="store_true", help="Force une nouvelle autorisation OAuth (choix de compte).")
    args = ap.parse_args()

    upload_export_dir(args.export_dir, args.profile, only_pattern=args.only,
                      dry_run=args.dry_run, force_reauth=args.reauth)

if __name__ == "__main__":
    main()
