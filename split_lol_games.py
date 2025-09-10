# Découpe automatique des parties LoL en se basant sur le chrono (haut-droite).
# Dépendances : opencv-python, pytesseract, Pillow (indirect), tqdm.
# Prérequis système : ffmpeg, tesseract-ocr installés (ou fournis via .env).

# Usage:
#   python split_lol_games.py <input_video.mp4> <output_dir> [--reencode]

from __future__ import annotations
import os, re, subprocess, shutil, cv2, pytesseract
from datetime import timedelta
from tqdm import tqdm

from settings import get_ffmpeg, get_tesseract_cmd

_tess = get_tesseract_cmd()
if _tess:
    pytesseract.pytesseract.tesseract_cmd = _tess

SAMPLE_EVERY_SEC     = 3.0   # échantillonnage ~1 frame / 3 s
START_THRESHOLD_SEC  = 120   # START si chrono <= 2:00
MIN_GAP_BETWEEN_GAMES= 90    # Anti double START (secondes entre débuts)
CLOCK_MISSING_LIMIT  = 10    # nombre d'échantillons consécutifs sans chrono
TAIL_AFTER_CLOCK_MISS= 20    # ajoute 20 s après la dernière vue du chrono
MIN_SEGMENT_SEC      = 120   # durée minimale d’un segment valable (2 min)

CLOCK_SEARCH_ROI     = (0.68, 0.00, 0.32, 0.22)

MMSS_ANY_RE = re.compile(r"(\d{1,2})\s*[:|;]?\s*(\d{2})")

def ts_format(seconds: float) -> str:
    td = timedelta(seconds=max(0, int(seconds)))
    total = int(td.total_seconds())
    h = total // 3600; m = (total % 3600) // 60; s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def crop(frame, roi):
    h, w = frame.shape[:2]
    x, y, ww, hh = roi
    x1, y1 = int(x*w), int(y*h); x2, y2 = int((x+ww)*w), int((y+hh)*h)
    return frame[y1:y2, x1:x2]

def ocr_text(img, config):
    return pytesseract.image_to_string(img, config=config).strip()

def preprocess_variants(gray):
    """Crée quelques variantes (inversion, seuillage Otsu, morph close) pour aider l’OCR."""
    vars = []
    for invert in (False, True):
        g = 255 - gray if invert else gray
        _, th = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        vars.append(th)
        k = cv2.getStructuringElement(cv2.MORPH_RECT, (2,2))
        vars.append(cv2.morphologyEx(th, cv2.MORPH_CLOSE, k, iterations=1))
    return vars

def extract_clock_seconds(search_img):
    """Retourne mm*60+ss si un chrono mm:ss est détecté dans search_img, sinon None."""
    g = cv2.cvtColor(search_img, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, None, fx=2, fy=2, interpolation=cv2.INTER_LINEAR)
    g = cv2.GaussianBlur(g, (3,3), 0)

    # 1) OCR 'mots' multi-lignes
    cfg_multi = r'--oem 3 --psm 6'
    for var in preprocess_variants(g):
        txt = ocr_text(var, cfg_multi)
        m = MMSS_ANY_RE.search(txt.replace(" ", ""))
        if m:
            mm, ss = int(m.group(1)), int(m.group(2))
            if 0 <= ss <= 59:
                return mm*60 + ss

    # 2) Sliding windows (petites zones) + whitelist
    H, W = g.shape[:2]
    cols, rows = 3, 2
    cfg_word = r'--oem 3 --psm 7 -c tessedit_char_whitelist=0123456789:'
    win_w, win_h = W // cols, H // rows
    for ry in range(rows):
        for cx in range(cols):
            sx, sy = cx*win_w, ry*win_h
            sub = g[sy:sy+win_h, sx:sx+win_w]
            for var in preprocess_variants(sub):
                txt = ocr_text(var, cfg_word)
                s = txt.replace(" ", "")
                m = MMSS_ANY_RE.search(s)
                if not m and len(s) in (3,4) and s.isdigit():
                    mm = int(s[:-2]); ss = int(s[-2:])
                    if 0 <= ss <= 59:
                        return mm*60 + ss
                if m:
                    mm, ss = int(m.group(1)), int(m.group(2))
                    if 0 <= ss <= 59:
                        return mm*60 + ss
    return None

def ensure_readable_video(path: str) -> str:
    """Si OpenCV n'ouvre pas la vidéo, remux puis réencode en dernier recours."""
    cap = cv2.VideoCapture(path)
    if cap.isOpened():
        cap.release(); return path
    cap.release()

    ffmpeg = get_ffmpeg()
    base, _ = os.path.splitext(path)
    fixed = base + "_fixed.mp4"
    print("⚠️  Remux ffmpeg…")
    cmd = [ffmpeg, "-y", "-err_detect", "ignore_err", "-i", path,
           "-map", "0", "-c", "copy", "-movflags", "+faststart", fixed]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        cap2 = cv2.VideoCapture(fixed)
        if cap2.isOpened():
            cap2.release(); print(f"✅ Fichier réparé: {fixed}"); return fixed
        cap2.release()
    except Exception:
        pass

    reenc = base + "_reenc.mp4"
    print("⚠️  Ré-encodage (plus lent)…")
    cmd = [ffmpeg, "-y", "-err_detect", "ignore_err", "-i", path, "-map", "0",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", reenc]
    subprocess.run(cmd, check=True)
    cap3 = cv2.VideoCapture(reenc)
    if cap3.isOpened():
        cap3.release(); print(f"✅ Fichier ré-encodé: {reenc}"); return reenc
    cap3.release()
    raise RuntimeError("Impossible d'ouvrir/réparer la vidéo.")

def detect_segments(input_path: str):
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened():
        raise RuntimeError(f"Impossible d'ouvrir la vidéo: {input_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps
    step = max(1, int(fps * SAMPLE_EVERY_SEC))

    frame_idx = 0
    last_start_time = -9999.0
    segments = []
    open_segment_start = None
    last_clock_seen_time = None
    clock_missing_count = 0

    pbar = tqdm(total=total_frames, desc="Analyse vidéo", unit="frames")

    while True:
        ret = cap.grab()
        if not ret:
            break
        if frame_idx % step != 0:
            frame_idx += 1
            continue

        ret, frame = cap.retrieve()
        if not ret:
            break

        t_sec = frame_idx / fps
        search_img = crop(frame, CLOCK_SEARCH_ROI)
        clock_val = extract_clock_seconds(search_img)

        if clock_val is not None:
            last_clock_seen_time = t_sec
            clock_missing_count = 0
        else:
            clock_missing_count += 1

        if open_segment_start is None and clock_val is not None and clock_val <= START_THRESHOLD_SEC:
            if t_sec - last_start_time >= MIN_GAP_BETWEEN_GAMES:
                open_segment_start = t_sec
                last_start_time = open_segment_start
                print(f"🟢 START @ {ts_format(open_segment_start)} (clock={clock_val}s)")

        if open_segment_start is not None and clock_missing_count >= CLOCK_MISSING_LIMIT:
            if last_clock_seen_time is not None:
                end_sec = min(last_clock_seen_time + TAIL_AFTER_CLOCK_MISS, duration)
                dur = end_sec - open_segment_start
                print(f"🔴 END   @ {ts_format(end_sec)} (durée ~ {ts_format(dur)})")
                if dur >= MIN_SEGMENT_SEC:
                    segments.append((open_segment_start, end_sec))
                open_segment_start = None
                clock_missing_count = 0

        frame_idx += 1
        pbar.update(step)

    pbar.close()

    if open_segment_start is not None:
        end_sec = duration
        dur = end_sec - open_segment_start
        print(f"🔴 END @ EOF ⇒ {ts_format(open_segment_start)} → {ts_format(end_sec)} (durée ~ {ts_format(dur)})")
        if dur >= MIN_SEGMENT_SEC:
            segments.append((open_segment_start, end_sec))

    cap.release()
    return segments


def cut_segments(input_path: str, segments, output_dir: str, reencode=False):
    os.makedirs(output_dir, exist_ok=True)
    ffmpeg = get_ffmpeg()

    for i, (start, end) in enumerate(segments, 1):
        out = os.path.join(output_dir, f"Game_{i:02d}.mp4")
        if reencode:
            cmd = [
                ffmpeg, "-y",
                "-ss", str(start), "-to", str(end),
                "-i", input_path,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-c:a", "aac", "-b:a", "160k",
                out
            ]
        else:
            cmd = [
                ffmpeg, "-y",
                "-ss", str(start), "-to", str(end),
                "-i", input_path,
                "-c", "copy",
                out
            ]
        print("→", " ".join(cmd))
        subprocess.run(cmd, check=True)
    print(f"🎉 {len(segments)} fichier(s) écrit(s) dans {output_dir}")

def main(input_video: str, output_dir: str, reencode=False):
    good_path = ensure_readable_video(input_video)
    print(f"🔎 Analyse (chrono haut-droite) : {good_path}")
    segs = detect_segments(good_path)
    if not segs:
        print("❌ Aucune partie détectée.")
        return
    print(f"✅ {len(segs)} partie(s) trouvée(s) :")
    for i, (s,e) in enumerate(segs, 1):
        print(f"  Game {i}: {ts_format(s)} → {ts_format(e)} (~{ts_format(e-s)})")
    cut_segments(good_path, segs, output_dir, reencode)

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python split_lol_games.py <input_video.mp4> <output_dir> [--reencode]")
        sys.exit(1)
    input_video = sys.argv[1]
    output_dir = sys.argv[2]
    reenc = ("--reencode" in sys.argv)
    main(input_video, output_dir, reencode=reenc)
