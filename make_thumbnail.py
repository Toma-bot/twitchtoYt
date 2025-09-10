# Génère une miniature .jpg par .mp4 dans un dossier d'exports.
#
# Usage :
#   python make_thumbnail.py <export_dir> [--bg <nom|chemin>] [--choose-bg] [--list-bg]
#                            [--title-font <path>] [--subtitle-font <path>]
#                            [--shadow 0-255] [--no-line] [--config <dir>]
#
# Arbo attendu :
#   config/
#     players.json
#     backgrounds/*.jpg|png|webp
#     fonts/*.ttf|*.otf

from __future__ import annotations
import os, re, json, argparse
from pathlib import Path
from typing import Optional, Tuple
from PIL import Image, ImageDraw, ImageFont, ImageFilter

def detect_config_and_fonts(cli_config: Optional[str]) -> tuple[Path, Path]:
    if cli_config:
        cfg = Path(cli_config).expanduser().resolve()
    else:
        cfg = None
        try:
            import settings 
            if getattr(settings, "CONFIG_DIR", None):
                cfg = Path(settings.CONFIG_DIR).expanduser().resolve()
        except Exception:
            pass
        if cfg is None:
            env_cfg = os.getenv("AUTOYT_CONFIG_DIR")
            cfg = Path(env_cfg).expanduser().resolve() if env_cfg else (Path(__file__).resolve().parent / "config")

    fonts_dir = None
    try:
        import settings  
        if getattr(settings, "FONTS_DIR", None):
            fonts_dir = Path(settings.FONTS_DIR).expanduser().resolve()
    except Exception:
        pass
    if fonts_dir is None:
        fonts_dir = cfg / "fonts"

    return cfg, fonts_dir

W, H = 1280, 720
SAFE = 40
DEFAULT_BG_NAME = "dark-gradient.jpg"
DEFAULT_TITLE_SUFFIX = "stream"

def list_backgrounds(backdir: Path) -> list[Path]:
    if not backdir.exists():
        return []
    exts = {".jpg", ".jpeg", ".png", ".webp"}
    return sorted(p for p in backdir.iterdir() if p.suffix.lower() in exts)

def resolve_bg_path(backdir: Path, bg_arg: Optional[str]) -> Optional[Path]:
    if bg_arg:
        p = Path(bg_arg)
        if p.is_absolute() and p.exists():
            return p
        cand = backdir / bg_arg
        if cand.exists():
            return cand
        return None
    default_bg = backdir / DEFAULT_BG_NAME
    if default_bg.exists():
        return default_bg
    bgs = list_backgrounds(backdir)
    return bgs[0] if bgs else None

def load_image(path: Optional[Path], mode="RGBA") -> Optional[Image.Image]:
    if not path:
        return None
    try:
        return Image.open(path).convert(mode)
    except Exception:
        return None

def circle_crop(im: Image.Image, scale=1.0) -> Image.Image:
    size = min(im.size)
    im = im.resize((int(size*scale), int(size*scale)), Image.LANCZOS)
    size = im.size[0]
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out

def has_transparency(img: Image.Image) -> bool:
    if img.mode == "P":
        return img.info.get("transparency", -1) != -1
    if img.mode == "RGBA":
        extrema = img.getchannel("A").getextrema()
        return extrema[0] < 255
    return False

def place_image_with_shadow(
    base: Image.Image,
    im: Image.Image,
    center: Tuple[float, float],
    max_w: Optional[int] = None,
    max_h: Optional[int] = None,
    shadow: bool = True,
    shadow_offset: Tuple[int, int] = (18, 18),
    shadow_blur: int = 32,
    shadow_opacity: int = 220
) -> Tuple[int, int, int, int]:
    if max_w or max_h:
        im.thumbnail((max_w or im.width, max_h or im.height), Image.LANCZOS)
    w, h = im.size
    x = int(center[0] - w/2)
    y = int(center[1] - h/2)

    if shadow and im.mode == "RGBA":
        alpha = im.split()[-1]
        shadow_mask = alpha.filter(ImageFilter.GaussianBlur(shadow_blur))
        tint = Image.new("RGBA", (w, h), (0, 0, 0, max(0, min(255, int(shadow_opacity)))))
        shadow_img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        shadow_img.paste(tint, (0, 0), mask=shadow_mask)
        base.alpha_composite(shadow_img, (x + shadow_offset[0], y + shadow_offset[1]))

    base.alpha_composite(im, (x, y))
    return x, y, w, h

def draw_text_with_outline(draw: ImageDraw.ImageDraw, xy, text, font, fill=(255,255,255), outline=8, outline_fill=(0,0,0)):
    x, y = xy
    r = outline
    if r > 0:
        for dx in range(-r, r+1):
            for dy in range(-r, r+1):
                if dx*dx + dy*dy <= r*r:
                    draw.text((x+dx, y+dy), text, font=font, fill=outline_fill)
    draw.text((x, y), text, font=font, fill=fill)

def load_players_db(players_json: Path) -> tuple[dict, dict]:
    if not players_json.exists():
        return {}, {}
    data = json.loads(players_json.read_text(encoding="utf-8"))
    alias_index = {}
    for key, meta in data.items():
        for a in meta.get("aliases", []):
            alias_index[a.lower()] = key
        alias_index[key.lower()] = key
    return data, alias_index

def resolve_asset_path(config_dir: Path, relative_or_abs: str) -> Path:
    p = Path(relative_or_abs)
    if p.is_absolute():
        return p
    return (config_dir / relative_or_abs).resolve()

def pick_player_from_export_dir(export_dir: Path, alias_index: dict) -> Optional[str]:
    name = export_dir.name
    m = re.match(r"(.+)_\d{4}-\d{2}-\d{2}$", name)
    base = m.group(1) if m else name
    key = alias_index.get(base.lower())
    if key:
        return key
    low = name.lower()
    for alias, k in alias_index.items():
        if alias in low:
            return k
    return None

def make_thumbnail_for_video(
    players_db: dict,
    player_key: str,
    video_path: Path,
    config_dir: Path,
    bg_path: Optional[Path],
    title_font_path: Optional[Path],
    subtitle_font_path: Optional[Path],
    shadow_opacity: int = 220,
    draw_line: bool = True
):
    meta = players_db.get(player_key, {})
    display_name = player_key
    title_text = f"{display_name} {DEFAULT_TITLE_SUFFIX}"

    bg = load_image(bg_path, mode="RGB")
    if bg is None:
        bg = Image.new("RGB", (W, H), (22, 22, 30))
    bg = bg.resize((W, H), Image.LANCZOS)
    canvas = Image.new("RGBA", (W, H))
    canvas.paste(bg, (0, 0))
    canvas = Image.alpha_composite(canvas, Image.new("RGBA", (W, H), (0, 0, 0, 70)))

    player_rel = meta.get("image")
    player_box: Optional[Tuple[int, int, int, int]] = None
    if player_rel:
        player_path = resolve_asset_path(config_dir, player_rel)
        player_img = load_image(player_path, mode="RGBA")
        if player_img:
            if not has_transparency(player_img):
                player_img = circle_crop(player_img, scale=1.0)
            player_box = place_image_with_shadow(
                canvas, player_img,
                center=(W*0.80, H*0.58),
                max_h=int(H*0.88),
                shadow=True, shadow_offset=(18, 18), shadow_blur=32,
                shadow_opacity=shadow_opacity
            )
        else:
            print(f"⚠️  Image joueur introuvable: {player_path}")

    team_logo_rel = meta.get("team-image") or meta.get("team_image") or ""
    if team_logo_rel:
        team_logo_path = resolve_asset_path(config_dir, team_logo_rel)
        team_logo = load_image(team_logo_path, mode="RGBA")
        if team_logo and player_box:
            team_logo.thumbnail((140, 140), Image.LANCZOS)
            px, py, pw, ph = player_box
            lx = max(SAFE, px - team_logo.width - 24)
            ly = py + ph//2 - team_logo.height//2
            canvas.alpha_composite(team_logo, (lx, max(SAFE, ly)))
        elif not team_logo:
            print(f"⚠️  Logo équipe introuvable: {team_logo_path}")

    def _try_font(p: Optional[Path], size: int):
        try:
            if p and p.exists():
                return ImageFont.truetype(str(p), size)
        except Exception:
            pass
        return ImageFont.load_default()

    title_font = _try_font(title_font_path, 132)
    sub_font   = _try_font(subtitle_font_path, 54)

    d = ImageDraw.Draw(canvas)

    title_y = int(H*0.12)
    draw_text_with_outline(d, (SAFE, title_y), title_text, title_font,
                           fill=(255, 255, 255), outline=10, outline_fill=(0, 0, 0))

    if draw_line:
        tw = d.textlength(title_text, font=title_font) if hasattr(d, "textlength") else 800
        line_x1 = SAFE
        line_y  = title_y + 118
        line_x2 = SAFE + int(tw * 0.65)
        acc = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ad = ImageDraw.Draw(acc)
        ad.line((line_x1, line_y, line_x2, line_y), fill=(255, 255, 255, 220), width=6)
        acc = acc.filter(ImageFilter.GaussianBlur(0.8))
        canvas = Image.alpha_composite(canvas, acc)

    sub_parts = []
    if meta.get("team"):
        sub_parts.append(meta["team"])
    if meta.get("role") or meta.get("Role"):
        sub_parts.append(meta.get("role") or meta.get("Role"))
    subtitle = "  •  ".join(sub_parts)
    if subtitle:
        draw_text_with_outline(d, (SAFE, title_y + 130), subtitle, sub_font,
                               fill=(235, 235, 235), outline=6, outline_fill=(0, 0, 0))

    out_path = video_path.with_suffix(".jpg")
    canvas.convert("RGB").save(out_path, quality=94, optimize=True)
    print(f"✅ Miniature: {out_path}")

def make_generic_thumbnail(
    video_path: Path,
    bg_path: Optional[Path],
    title="Highlights",
    title_font_path: Optional[Path] = None
):
    bg = load_image(bg_path, mode="RGB")
    if bg is None:
        bg = Image.new("RGB", (W, H), (18, 18, 26))
    bg = bg.resize((W, H), Image.LANCZOS)
    canvas = Image.new("RGBA", (W, H)); canvas.paste(bg, (0, 0))
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 90))
    canvas = Image.alpha_composite(canvas, overlay)

    try:
        font = ImageFont.truetype(str(title_font_path), 128) if title_font_path and Path(title_font_path).exists() else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    d = ImageDraw.Draw(canvas)
    draw_text_with_outline(d, (SAFE, int(H*0.12)), title, font, fill=(255, 255, 255), outline=10)
    out_path = video_path.with_suffix(".jpg")
    canvas.convert("RGB").save(out_path, quality=94, optimize=True)
    print(f"✅ Miniature (générique): {out_path}")

def generate_thumbnails_for_export(
    export_dir: str,
    config_dir: Path,
    fonts_dir: Path,
    bg_arg: Optional[str] = None,
    list_only: bool = False,
    choose: bool = False,
    title_font: Optional[str] = None,
    subtitle_font: Optional[str] = None,
    shadow_opacity: int = 220,
    no_line: bool = False
):
    export = Path(export_dir).resolve()
    assert export.exists() and export.is_dir(), f"Dossier invalide: {export}"

    BACKGROUNDS_DIR = config_dir / "backgrounds"
    PLAYERS_JSON    = config_dir / "players.json"

    if list_only:
        bgs = list_backgrounds(BACKGROUNDS_DIR)
        if not bgs:
            print(f"Aucun background trouvé dans: {BACKGROUNDS_DIR}")
            return
        print("Backgrounds disponibles :")
        for i, p in enumerate(bgs, 1):
            print(f"{i:2d}. {p.name}")
        return

    if choose:
        bgs = list_backgrounds(BACKGROUNDS_DIR)
        if not bgs:
            print(f"Aucun background trouvé dans: {BACKGROUNDS_DIR}")
            return
        print("Choisis un background :")
        for i, p in enumerate(bgs, 1):
            print(f"{i:2d}. {p.name}")
        try:
            idx = int(input("Numéro: ").strip())
            bg_path = bgs[idx-1]
        except Exception:
            print("Sélection invalide, utilisation du défaut.")
            bg_path = resolve_bg_path(BACKGROUNDS_DIR, None)
    else:
        bg_path = resolve_bg_path(BACKGROUNDS_DIR, bg_arg)

    players_db, alias_index = load_players_db(PLAYERS_JSON)
    player_key = pick_player_from_export_dir(export, alias_index)

    mp4s = sorted(export.glob("*.mp4"))
    if not mp4s:
        print("Aucune vidéo .mp4 trouvée dans ce dossier.")
        return

    title_font_path = Path(title_font).expanduser().resolve() if title_font else None
    if (not title_font_path or not title_font_path.exists()) and fonts_dir.exists():
        for cand in ["BebasNeue-Regular.ttf", "Inter-Bold.ttf", "Oswald-VariableFont_wght.ttf"]:
            p = fonts_dir / cand
            if p.exists():
                title_font_path = p
                break

    subtitle_font_path = Path(subtitle_font).expanduser().resolve() if subtitle_font else None
    if (not subtitle_font_path or not subtitle_font_path.exists()) and fonts_dir.exists():
        for cand in ["Oswald-VariableFont_wght.ttf", "Inter-Regular.ttf"]:
            p = fonts_dir / cand
            if p.exists():
                subtitle_font_path = p
                break

    if not player_key:
        print(f"⚠️ Aucun joueur reconnu pour '{export.name}'. Miniatures génériques.")
        for mp4 in mp4s:
            make_generic_thumbnail(mp4, bg_path, title=f"{export.name} stream", title_font_path=title_font_path)
        return

    for mp4 in mp4s:
        make_thumbnail_for_video(
            players_db, player_key, mp4,
            config_dir=config_dir,
            bg_path=bg_path,
            title_font_path=title_font_path,
            subtitle_font_path=subtitle_font_path,
            shadow_opacity=shadow_opacity,
            draw_line=(not no_line)
        )

def main():
    ap = argparse.ArgumentParser(description="Génère des miniatures .jpg pour un dossier d'exports.")
    ap.add_argument("export_dir", help=r"Dossier d'exports (contient des .mp4)")
    ap.add_argument("--config", help="Dossier config (sinon settings.CONFIG_DIR, env AUTOYT_CONFIG_DIR, ou ./config)")
    ap.add_argument("--bg", help="Nom de fichier dans config/backgrounds ou chemin absolu")
    ap.add_argument("--list-bg", action="store_true", help="Lister les backgrounds disponibles puis quitter")
    ap.add_argument("--choose-bg", action="store_true", help="Menu interactif de sélection du background")
    ap.add_argument("--title-font", help="Chemin police TTF/OTF pour le titre")
    ap.add_argument("--subtitle-font", help="Chemin police TTF/OTF pour le sous-titre")
    ap.add_argument("--shadow", type=int, default=220, help="Opacité de l'ombre (0-255, défaut 220)")
    ap.add_argument("--no-line", action="store_true", help="Désactiver la petite ligne d'accent sous le titre")
    args = ap.parse_args()

    config_dir, fonts_dir = detect_config_and_fonts(args.config)
    generate_thumbnails_for_export(
        export_dir=args.export_dir,
        config_dir=config_dir,
        fonts_dir=fonts_dir,
        bg_arg=args.bg,
        list_only=args.list_bg,
        choose=args.choose_bg,
        title_font=args.title_font,
        subtitle_font=args.subtitle_font,
        shadow_opacity=max(0, min(255, args.shadow)),
        no_line=args.no_line
    )

if __name__ == "__main__":
    main()