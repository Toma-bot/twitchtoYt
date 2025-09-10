from __future__ import annotations
import json, re, os, argparse
from pathlib import Path
from typing import Dict, Tuple, Optional

try:
    from settings import PROJECT_ROOT, CONFIG_DIR 
    BASE_DIR   = Path(PROJECT_ROOT)
    CONFIG_DIR = Path(CONFIG_DIR)
except Exception:
    BASE_DIR   = Path(__file__).resolve().parent
    CONFIG_DIR = BASE_DIR / "config"

PLAYERS_JSON = CONFIG_DIR / "players.json"

def load_players_db() -> Tuple[Dict, Dict[str, str]]:
    """
    Charge players.json si présent. Retourne (db, alias_index).
    alias_index mappe alias.lower() -> NomClé (ex: 'supa_lol' -> 'Supa').
    """
    if not PLAYERS_JSON.exists():
        return {}, {}
    with open(PLAYERS_JSON, "r", encoding="utf-8") as f:
        db = json.load(f)
    alias_index: Dict[str, str] = {}
    for key, meta in db.items():
        alias_index[key.lower()] = key
        for a in meta.get("aliases", []):
            alias_index[a.lower()] = key
    return db, alias_index


def infer_player_from_export_dir(export_dir: Path, alias_index: Dict[str, str]) -> Optional[str]:
    """
    Exemple: 'supa_lol_2025-09-06' -> 'supa_lol' -> alias_index -> 'Supa'.
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


def parse_export_date(export_dir: Path) -> Optional[str]:
    m = re.match(r".*_(\d{4}-\d{2}-\d{2})$", export_dir.name)
    return m.group(1) if m else None

def build_title(player_key: str, meta: Dict) -> str:
    display = player_key.upper()
    role = meta.get("role") or meta.get("Role") or ""
    team = meta.get("team") or ""
    parts = [f"{display} stream — Highlights"]
    extra = " ".join([p for p in [team, role] if p]).strip()
    if extra:
        parts.append(f" | {extra}")
    return "".join(parts)


def build_hashtags(player_key: str, meta: Dict) -> list[str]:
    tags = []
    name_tag = re.sub(r"\s+", "", player_key)
    tags.append(f"#{name_tag}")
    team = meta.get("team")
    if team:
        tags.append("#" + re.sub(r"[^A-Za-z0-9]+", "", team))
    role = (meta.get("role") or meta.get("Role") or "").replace(" ", "")
    if role:
        tags.append(f"#{role}")
    tags.extend(["#LeagueOfLegends", "#LoL", "#Highlights"])
    seen = set(); out=[]
    for t in tags:
        key = t.lower()
        if key not in seen:
            seen.add(key); out.append(t)
    return out[:6]


def build_keywords(player_key: str, meta: Dict) -> list[str]:
    aliases = meta.get("aliases", [])
    team    = meta.get("team")
    role    = meta.get("role") or meta.get("Role")
    base = set()

    base.add(player_key)
    for a in aliases:
        base.add(a)
        base.add(a.lower())

    roots = [player_key] + list(aliases[:3])
    for r in roots:
        if not r:
            continue
        base.add(f"{r} stream")
        base.add(f"{r} highlights")

    base.update([
        "League of Legends", "LoL", "lol highlights", "lol stream",
        "ranked", "soloQ", "solo queue", "gameplay", "montage", "outplays"
    ])

    if team:
        base.add(team)
        base.add(f"{team} highlights")
    if role:
        base.add(role)
        base.add(f"{role} highlights")

    keywords = [k.strip() for k in base if k and len(k.strip()) >= 2]
    return sorted(keywords, key=lambda x: (len(x) > 22, x.lower()))[:40]


def build_description(player_key: str, meta: Dict, folder_date: Optional[str], hashtags: list[str]) -> str:
    team = meta.get("team") or "—"
    role = meta.get("role") or meta.get("Role") or "—"
    palmares = meta.get("palmares") or ""
    date_line = f"Publié le : {folder_date}" if folder_date else ""

    lines = [
        f"{player_key} — {team} • {role}",
        date_line,
        "",
        "👉 Abonne-toi pour plus de highlights, clips et analyses !",
        "👍 Like la vidéo si elle t'a plu et dis-moi en commentaire quel moment t'a le plus marqué.",
        "",
        "— À propos du joueur —",
        f"• Équipe : {team}",
        f"• Rôle   : {role}",
    ]
    if palmares:
        lines.append(f"• Palmarès : {palmares}")
    lines += [
        "",
        "— Liens utiles —",
        "• Twitch : https://twitch.tv/ (ajoute le lien du joueur si dispo)",
        "• Twitter/X : https://twitter.com/ (facultatif)",
        "• Discord : https://discord.gg/ (facultatif)",
        "",
        "— Matériel & musique —",
        "• Musique : YouTube Audio Library (libre de droits) / ou mention du créateur",
        "• Montage : ffmpeg + scripts Python automatiques",
        "",
        "— Hashtags —",
        " ".join(hashtags)
    ]
    return "\n".join([l for l in lines if l is not None])


def write_sidecar_files(video_path: Path, meta_json: dict, description: str, dry_run: bool=False):
    json_path = video_path.with_suffix(".metadata.json")
    txt_path  = video_path.with_suffix(".description.txt")
    if dry_run:
        print(f"  (dry-run) Écrirait {json_path.name} et {txt_path.name}")
        return
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(meta_json, f, ensure_ascii=False, indent=2)
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(description)
    print(f"  ✅ Métadonnées : {json_path.name}")
    print(f"  ✅ Description : {txt_path.name}")


def generate_metadata_for_export(
    export_dir: str,
    privacy_status: str = "unlisted",
    category_id: str = "20",
    default_lang: str = "fr",
    no_hashtags: bool = False,
    dry_run: bool = False
):
    export = Path(export_dir).resolve()
    assert export.exists() and export.is_dir(), f"Dossier invalide: {export}"

    players_db, alias_index = load_players_db()

    player_key = infer_player_from_export_dir(export, alias_index)
    if not player_key:
        low = export.name.lower()
        for alias, key in alias_index.items():
            if alias in low:
                player_key = key
                break

    mp4s = sorted(export.glob("*.mp4"))
    if not mp4s:
        print("Aucune vidéo .mp4 trouvée.")
        return

    folder_date = parse_export_date(export)
    print(f"📦 Dossier: {export.name}  |  Vidéos: {len(mp4s)}")
    if player_key:
        print(f"👤 Joueur détecté: {player_key}")
    else:
        print("👤 Joueur non reconnu → métadonnées génériques.")

    for mp4 in mp4s:
        if player_key and player_key in players_db:
            meta = players_db[player_key]
            title = build_title(player_key, meta)
            hashtags = [] if no_hashtags else build_hashtags(player_key, meta)
            tags = build_keywords(player_key, meta)
            description = build_description(player_key, meta, folder_date, hashtags)
        else:
            base_name = export.name.replace("_", " ")
            title = f"{base_name} — Highlights"
            hashtags = [] if no_hashtags else ["#LeagueOfLegends", "#LoL", "#Highlights"]
            tags = ["League of Legends", "LoL", "highlights", "stream"]
            description = "\n".join([
                f"{base_name} — Compilation de moments marquants.",
                "",
                "Jeu : League of Legends",
                "",
                "— Hashtags —",
                " ".join(hashtags)
            ])

        meta_json = {
            "title": title[:100],              
            "description": description[:5000],   
            "tags": tags[:40],                  
            "categoryId": category_id,         
            "privacyStatus": privacy_status,     # unlisted/public/private
            "defaultLanguage": default_lang,
            # "publishAt": "2025-09-07T16:00:00Z",  # optionnel: planification
            # "madeForKids": False,
        }

        print(f"\n🎬 {mp4.name}")
        print(f"  Titre: {meta_json['title']}")
        print(f"  Visibilité: {meta_json['privacyStatus']}")
        write_sidecar_files(mp4, meta_json, description, dry_run=dry_run)

    print("\n🎯 Fichiers .metadata.json et .description.txt créés à côté de chaque vidéo.")

def main():
    ap = argparse.ArgumentParser(
        description="Génère des métadonnées YouTube pour un dossier d'exports (un fichier .metadata.json + .description.txt par vidéo)."
    )
    ap.add_argument("export_dir", help="Ex: C:\\...\\exports\\supa_lol_2025-09-06")
    ap.add_argument("--privacy", choices=["unlisted", "public", "private"],
                    default=os.getenv("YT_DEFAULT_PRIVACY", "unlisted"),
                    help="Visibilité YouTube (défaut: unlisted).")
    ap.add_argument("--category-id", default=os.getenv("YT_CATEGORY_ID", "20"),
                    help="ID catégorie YouTube (Gaming=20).")
    ap.add_argument("--lang", default=os.getenv("YT_DEFAULT_LANG", "fr"),
                    help="Langue par défaut du contenu (défaut: fr).")
    ap.add_argument("--no-hashtags", action="store_true",
                    help="Ne pas ajouter la section hashtags dans la description.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Ne rien écrire, juste afficher ce qui serait généré.")
    args = ap.parse_args()

    generate_metadata_for_export(
        args.export_dir,
        privacy_status=args.privacy,
        category_id=args.category_id,
        default_lang=args.lang,
        no_hashtags=args.no_hashtags,
        dry_run=args.dry_run
    )

if __name__ == "__main__":
    main()
