# Obtenir/renouveler des identifiants OAuth Twitch et écrire un .env propre dans config/.
#
# Dépendances:
#   pip install requests python-dotenv
#
# Usage (guidé) :
#   python auth_twitch.py
#
# Usage (paramétré) :
#   python auth_twitch.py --client-id XXX --client-secret YYY --redirect-uri http://localhost:3000/callback
#   python auth_twitch.py --port 3000  # si ton redirect-uri utilise un autre port, ajuste-le
#
# Le script :
#  - ouvre le navigateur sur l'écran d'autorisation ;
#  - reçoit le "code" sur http://localhost:<port>/callback ;
#  - échange ce code contre access_token + refresh_token ;
#  - sauvegarde/actualise le fichier .env dans CONFIG_DIR.

from __future__ import annotations
import os
import sys
import json
import time
import base64
import secrets
import threading
import urllib.parse
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Dict, Optional

import requests

def _detect_config_dir() -> Path:
    """
    Priorité:
      1) settings.CONFIG_DIR si settings.py existe
      2) env AUTOYT_CONFIG_DIR
      3) ./config
    """
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

CONFIG_DIR = _detect_config_dir()
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
ENV_PATH = CONFIG_DIR / ".env"


AUTH_URL  = "https://id.twitch.tv/oauth2/authorize"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"

DEFAULT_SCOPES = ["user:read:email", "user:read:follows"]

def _parse_env_file(path: Path) -> Dict[str, str]:
    data: Dict[str, str] = {}
    if not path.exists():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip()
    return data

def _write_env_file(path: Path, mapping: Dict[str, str]) -> None:
    existing = _parse_env_file(path)
    existing.update(mapping)
    lines = [f"{k}={v}" for k, v in existing.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

class OAuthHandler(BaseHTTPRequestHandler):
    expected_state: str = ""
    received_code: Optional[str] = None
    event: Optional[threading.Event] = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404); self.end_headers()
            self.wfile.write(b"Not Found (expected /callback)")
            return

        qs = urllib.parse.parse_qs(parsed.query)
        code  = qs.get("code",  [None])[0]
        state = qs.get("state", [None])[0]

        if not code or not state or state != OAuthHandler.expected_state:
            self.send_response(400); self.end_headers()
            self.wfile.write(b"Invalid request (missing/invalid code or state).")
            return

        OAuthHandler.received_code = code
        self.send_response(200); self.end_headers()
        self.wfile.write(b"Authorization successful. You can close this tab.")
        if OAuthHandler.event:
            OAuthHandler.event.set()

    def log_message(self, *args, **kwargs):
        return 

def _start_local_server(port: int, expected_state: str):
    OAuthHandler.expected_state = expected_state
    OAuthHandler.received_code = None
    OAuthHandler.event = threading.Event()
    httpd = HTTPServer(("127.0.0.1", port), OAuthHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, t, OAuthHandler.event

def build_auth_url(client_id: str, redirect_uri: str, scopes: list[str], state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state,
        "force_verify": "true",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

def exchange_code_for_tokens(code: str, client_id: str, client_secret: str, redirect_uri: str) -> dict:
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    r = requests.post(TOKEN_URL, data=data, timeout=20)
    r.raise_for_status()
    return r.json()

def refresh_access_token(refresh_token: str, client_id: str, client_secret: str) -> dict:
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    r = requests.post(TOKEN_URL, data=data, timeout=20)
    r.raise_for_status()
    return r.json()

def _guess_port_from_redirect(redirect_uri: str) -> int:
    try:
        u = urllib.parse.urlparse(redirect_uri)
        if u.port:
            return int(u.port)
    except Exception:
        pass
    return 3000

def main():
    import argparse

    env = _parse_env_file(ENV_PATH)
    default_client_id     = env.get("TWITCH_CLIENT_ID", "")
    default_client_secret = env.get("TWITCH_CLIENT_SECRET", "")
    default_redirect_uri  = env.get("TWITCH_REDIRECT_URI", "http://localhost:3000/callback")

    ap = argparse.ArgumentParser(description="Assistant d'authentification OAuth Twitch (écrit .env dans config/).")
    ap.add_argument("--client-id", default=default_client_id, help="TWITCH_CLIENT_ID (sinon demandé)")
    ap.add_argument("--client-secret", default=default_client_secret, help="TWITCH_CLIENT_SECRET (sinon demandé)")
    ap.add_argument("--redirect-uri", default=default_redirect_uri,
                    help="TWITCH_REDIRECT_URI (ex: http://localhost:3000/callback)")
    ap.add_argument("--port", type=int, default=None, help="Port local du callback (par défaut, déduit de redirect-uri)")
    ap.add_argument("--scopes", nargs="*", default=DEFAULT_SCOPES, help="Liste de scopes OAuth à demander")
    ap.add_argument("--refresh", action="store_true",
                    help="Essayer d'abord de rafraîchir via TWITCH_REFRESH_TOKEN existant (si présent).")
    args = ap.parse_args()

    client_id = args.client_id or input("TWITCH_CLIENT_ID: ").strip()
    client_secret = args.client_secret or input("TWITCH_CLIENT_SECRET: ").strip()
    redirect_uri = args.redirect_uri or "http://localhost:3000/callback"
    port = args.port or _guess_port_from_redirect(redirect_uri)
    scopes = args.scopes or DEFAULT_SCOPES

    if not client_id or not client_secret:
        print("❌ CLIENT_ID/CLIENT_SECRET requis.")
        sys.exit(1)

    if args.refresh:
        refresh_token = env.get("TWITCH_REFRESH_TOKEN")
        if refresh_token:
            print("🔁 Tentative de refresh du token existant…")
            try:
                tok = refresh_access_token(refresh_token, client_id, client_secret)
                access_token  = tok.get("access_token")
                new_refresh   = tok.get("refresh_token", refresh_token)
                if access_token:
                    _write_env_file(ENV_PATH, {
                        "TWITCH_CLIENT_ID": client_id,
                        "TWITCH_CLIENT_SECRET": client_secret,
                        "TWITCH_REDIRECT_URI": redirect_uri,
                        "TWITCH_USER_ACCESS_TOKEN": access_token,
                        "TWITCH_REFRESH_TOKEN": new_refresh,
                    })
                    print(f"✅ Refresh OK. .env mis à jour @ {ENV_PATH}")
                    return
            except requests.HTTPError as e:
                print("⚠️  Refresh échoué :", e.response.text)
        else:
            print("ℹ️ Aucun TWITCH_REFRESH_TOKEN dans .env — on passe au flux d'autorisation complet.")

    state = base64.urlsafe_b64encode(secrets.token_bytes(18)).decode().rstrip("=")
    httpd, thread, event = _start_local_server(port=port, expected_state=state)

    url = build_auth_url(client_id, redirect_uri, scopes, state)
    print("\n➡️  Ouvre ton navigateur (ou copie l'URL) pour autoriser l’application :")
    print(url)
    try:
        webbrowser.open(url, new=1, autoraise=True)
    except Exception:
        pass

    print("\n⏳ En attente de l’autorisation (300s max)…")
    if not event.wait(timeout=300):
        httpd.shutdown()
        print("❌ Temps dépassé sans autorisation.")
        sys.exit(1)

    code = OAuthHandler.received_code
    httpd.shutdown()

    print("🔄 Échange du code contre des tokens…")
    try:
        tok = exchange_code_for_tokens(code, client_id, client_secret, redirect_uri)
    except requests.HTTPError as e:
        print("❌ Erreur d’échange :", e.response.text)
        sys.exit(1)

    access_token  = tok.get("access_token")
    refresh_token = tok.get("refresh_token")
    expires_in    = tok.get("expires_in")

    if not access_token:
        print("❌ Pas d'access_token retourné par Twitch.")
        sys.exit(1)

    _write_env_file(ENV_PATH, {
        "TWITCH_CLIENT_ID": client_id,
        "TWITCH_CLIENT_SECRET": client_secret,
        "TWITCH_REDIRECT_URI": redirect_uri,
        "TWITCH_USER_ACCESS_TOKEN": access_token,
        **({"TWITCH_REFRESH_TOKEN": refresh_token} if refresh_token else {}),
    })

    print("\n✅ Tokens obtenus & .env mis à jour !")
    print(f"• CONFIG_DIR : {CONFIG_DIR}")
    print(f"• .env       : {ENV_PATH.name}")
    print(f"• access_token (tronqué) : {access_token[:10]}...")
    if refresh_token:
        print(f"• refresh_token (tronqué): {refresh_token[:10]}...")
    if expires_in:
        print(f"• expire dans ~{expires_in}s")

    print("\nTu peux lancer maintenant :")
    print("  python run_pipeline.py")
    print("ou directement :")
    print("  python Download_recent_vods.py")

if __name__ == "__main__":
    main()
