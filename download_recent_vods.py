import os
import time
import json
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional

import requests
import yt_dlp
from dotenv import load_dotenv
from pathlib import Path

from settings import PROJECT_ROOT, CONFIG_DIR

BASE_DIR     = str(PROJECT_ROOT)
ENV_PATH     = str(CONFIG_DIR / ".env")             # <— ICI
STATE_PATH   = str(PROJECT_ROOT / "state" / "downloaded_vods.json")
OUTPUT_ROOT  = str(PROJECT_ROOT / "vods")
CUTOFF_HOURS = 48

load_dotenv(ENV_PATH)
CLIENT_ID     = os.getenv("TWITCH_CLIENT_ID")
CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
ACCESS_TOKEN  = os.getenv("TWITCH_USER_ACCESS_TOKEN")
REFRESH_TOKEN = os.getenv("TWITCH_REFRESH_TOKEN")
BASE_URL      = "https://api.twitch.tv/helix"

HEADERS = {
    "Client-ID": CLIENT_ID,
    "Authorization": f"Bearer {ACCESS_TOKEN}" if ACCESS_TOKEN else "",
}

def load_state(path: str) -> Dict[str, bool]:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_state(path: str, data: Dict[str, bool]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _save_env_map(path: str, mapping: dict) -> None:
    lines = []
    existing = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    existing[k] = v
    existing.update(mapping)
    for k, v in existing.items():
        lines.append(f"{k}={v}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

def _reload_env_into_process():
    global CLIENT_ID, CLIENT_SECRET, ACCESS_TOKEN, REFRESH_TOKEN, HEADERS
    load_dotenv(ENV_PATH, override=True)
    CLIENT_ID     = os.getenv("TWITCH_CLIENT_ID")
    CLIENT_SECRET = os.getenv("TWITCH_CLIENT_SECRET")
    ACCESS_TOKEN  = os.getenv("TWITCH_USER_ACCESS_TOKEN")
    REFRESH_TOKEN = os.getenv("TWITCH_REFRESH_TOKEN")
    HEADERS = {
        "Client-ID": CLIENT_ID,
        "Authorization": f"Bearer {ACCESS_TOKEN}" if ACCESS_TOKEN else "",
    }

def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> dict:
    url = "https://id.twitch.tv/oauth2/token"
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    r = requests.post(url, data=data, timeout=20)
    r.raise_for_status()
    return r.json()

def run_interactive_auth():
    print("🌍 Réauthentification interactive requise (lancement auth_twitch.py)...")
    os.system(f'python "{os.path.join(BASE_DIR, "auth_twitch.py")}"')
    _reload_env_into_process()

def ensure_valid_twitch_token():
    global ACCESS_TOKEN, REFRESH_TOKEN, HEADERS
    test_url = f"{BASE_URL}/users"
    r = requests.get(test_url, headers=HEADERS, timeout=10)
    if r.status_code == 200:
        return ACCESS_TOKEN

    print("🔁 Access token invalide → refresh en cours…")
    try:
        resp = refresh_access_token(CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN)
        new_access  = resp.get("access_token")
        new_refresh = resp.get("refresh_token") or REFRESH_TOKEN
    except requests.HTTPError as e:
        print(f"⚠️  Refresh impossible ({e.response.status_code}). Réauthentification interactive…")
        run_interactive_auth()
        return ACCESS_TOKEN

    if not new_access:
        print("⚠️  Refresh sans access_token. Réauthentification interactive…")
        run_interactive_auth()
        return ACCESS_TOKEN

    _save_env_map(ENV_PATH, {
        "TWITCH_USER_ACCESS_TOKEN": new_access,
        "TWITCH_REFRESH_TOKEN": new_refresh
    })
    _reload_env_into_process()
    print("✅ Access token Twitch rafraîchi.")
    return ACCESS_TOKEN

def api_get(endpoint: str, params: Dict) -> Dict:
    r = requests.get(f"{BASE_URL}{endpoint}", headers=HEADERS, params=params, timeout=20)
    if r.status_code == 401:
        ensure_valid_twitch_token()
        r = requests.get(f"{BASE_URL}{endpoint}", headers=HEADERS, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def iso_to_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

def get_user_id() -> str:
    data = api_get("/users", {})
    return data["data"][0]["id"]

def get_followed_channels(user_id: str) -> List[Dict]:
    out: List[Dict] = []
    cursor: Optional[str] = None
    while True:
        params = {"user_id": user_id, "first": 100}
        if cursor:
            params["after"] = cursor
        data = api_get("/channels/followed", params)
        out.extend(data.get("data", []))
        cursor = data.get("pagination", {}).get("cursor")
        if not cursor:
            break
    return out

def list_archives_since(user_id: str, cutoff: datetime, first_per_page: int = 50, max_pages: int = 5) -> List[Dict]:
    vids: List[Dict] = []
    cursor: Optional[str] = None
    pages = 0
    while pages < max_pages:
        params = {"user_id": user_id, "type": "archive", "first": first_per_page}
        if cursor:
            params["after"] = cursor
        data = api_get("/videos", params)
        items = data.get("data", [])
        if not items:
            break

        for v in items:
            created = iso_to_dt(v["created_at"])
            if created >= cutoff:
                vids.append(v)
        cursor = data.get("pagination", {}).get("cursor")
        pages += 1
        if not cursor:
            break
    return vids

def download_vod(vod: Dict, login: str) -> None:
    url = vod["url"]
    vod_date = iso_to_dt(vod["created_at"]).strftime("%Y-%m-%d")
    outdir = os.path.join(OUTPUT_ROOT, login, vod_date)
    os.makedirs(outdir, exist_ok=True)

    ydl_opts = {
        "outtmpl": os.path.join(outdir, "%(uploader)s_%(upload_date)s_%(id)s.%(ext)s"),
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
    }
    print(f"⬇️  Téléchargement : {url}")
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])
    print(f"✅ Terminé → {outdir}")

def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("❌ TWITCH_CLIENT_ID ou TWITCH_CLIENT_SECRET manquant dans .env")
        return

    ensure_valid_twitch_token()

    cutoff = datetime.now(timezone.utc) - timedelta(hours=CUTOFF_HOURS)
    print(f"📅 Fenêtre : VODs depuis {cutoff:%Y-%m-%d %H:%M UTC}")

    me = get_user_id()
    follows = get_followed_channels(me)
    print(f"👤 Tu suis {len(follows)} chaînes.")

    state = load_state(STATE_PATH)
    newly_downloaded = 0
    checked_streamers = 0

    for f in follows:
        checked_streamers += 1
        login = f["broadcaster_login"]
        uid = f["broadcaster_id"]

        try:
            vids = list_archives_since(uid, cutoff)
        except requests.HTTPError as e:
            print(f"⚠️  Erreur API /videos pour {login} : {e}")
            continue

        if not vids:
            print(f"— {login} : aucune VOD dans les {CUTOFF_HOURS}h")
            continue

        print(f"— {login} : {len(vids)} VOD(s) éligible(s)")
        for v in vids:
            vid_id = v["id"]
            if state.get(vid_id):
                continue
            try:
                download_vod(v, login)
                state[vid_id] = True
                newly_downloaded += 1
                save_state(STATE_PATH, state)
            except Exception as ex:
                print(f"❌ Échec téléchargement VOD {vid_id} ({login}) : {ex}")

    print(f"\n🧾 Bilan : {checked_streamers} streamers vérifiés | {newly_downloaded} VOD(s) téléchargée(s).")
    save_state(STATE_PATH, state)

if __name__ == "__main__":
    main()
