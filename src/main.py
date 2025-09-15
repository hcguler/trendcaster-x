# main.py
import os, sys, io
from datetime import datetime, timezone, timedelta
from typing import List, Tuple
from requests_oauthlib import OAuth1Session
from PIL import Image, ImageDraw, ImageFont

# -------------------- Sabitler --------------------
POST_TWEET_ENDPOINT = "https://api.twitter.com/2/tweets"
MEDIA_UPLOAD_ENDPOINT = "https://upload.twitter.com/1.1/media/upload.json"

# Görsel boyutu (kare)
CANVAS_W = 1080
CANVAS_H = 1080

# Footer'da görünsün diye kullanıcı adı
OWNER_HANDLE = "@durbirbakiyim"

# Başlık havuzu (2–3 kelime, emojisiz)
CATCHY_TITLES = [
    "Zaman Akışı",
    "Takvim Hızı",
    "Bugün Kaydı",
    "Günlük Tempo",
    "Zaman Nabzı",
    "Kronometre Hal",
    "Anın Özeti",
    "Zaman Çizgisi",
    "Günlük İlerleme",
    "Takvim Nabzı",
    "Zaman Ölçümü",
    "Günün Durumu",
    "Zaman Özeti",
    "Bugün İlerleme",
    "Takvim Özeti",
    "Günlük Rapor",
    "Zaman Grafiği",
    "Anlık İlerleme",
    "Zaman Panosu",
    "Takvim Panosu",
]

# -------------------- Zaman yardımcıları --------------------
def now_tr():
    tz_tr = timezone(timedelta(hours=3))
    return datetime.now(tz_tr)

def year_progress(dt: datetime) -> float:
    start = datetime(dt.year, 1, 1, tzinfo=dt.tzinfo)
    end   = datetime(dt.year + 1, 1, 1, tzinfo=dt.tzinfo)
    return (dt - start).total_seconds() / (end - start).total_seconds()

def month_progress(dt: datetime) -> float:
    start = datetime(dt.year, dt.month, 1, tzinfo=dt.tzinfo)
    if dt.month == 12:
        end = datetime(dt.year + 1, 1, 1, tzinfo=dt.tzinfo)
    else:
        end = datetime(dt.year, dt.month + 1, 1, tzinfo=dt.tzinfo)
    return (dt - start).total_seconds() / (end - start).total_seconds()

def day_progress(dt: datetime) -> float:
    start = datetime(dt.year, dt.month, dt.day, tzinfo=dt.tzinfo)
    end   = start + timedelta(days=1)
    return (dt - start).total_seconds() / (end - start).total_seconds()

# -------------------- Yerelleştirme (TR) --------------------
_TR_MONTHS = {
    1:"Ocak", 2:"Şubat", 3:"Mart", 4:"Nisan", 5:"Mayıs", 6:"Haziran",
    7:"Temmuz", 8:"Ağustos", 9:"Eylül", 10:"Ekim", 11:"Kasım", 12:"Aralık"
}
_TR_WEEKDAYS = {  # Monday=0
    0:"Pazartesi", 1:"Salı", 2:"Çarşamba", 3:"Perşembe",
    4:"Cuma", 5:"Cumartesi", 6:"Pazar"
}

def tr_month_name(m: int) -> str:
    return _TR_MONTHS.get(m, str(m))

def tr_weekday_name(wd: int) -> str:
    return _TR_WEEKDAYS.get(wd, "")

def format_tr_datetime_line(dt: datetime) -> str:
    # dd.MM.yyyy HH:ss (günadı) — dakika yerine saniye istendi
    return f"{dt.day:02d}.{dt.month:02d}.{dt.year:04d} {dt.hour:02d}:{dt.second:02d} ({tr_weekday_name(dt.weekday())})"

# -------------------- Env / OAuth --------------------
def require_env(keys: List[str]) -> dict:
    envs = {k: os.environ.get(k) for k in keys}
    missing = [k for k, v in envs.items() if not v]
    if missing:
        print(f"HATA: Eksik secret(lar): {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    return envs

def oauth1_session_from_env() -> OAuth1Session:
    envs = require_env([
        "TWITTER_API_KEY",
        "TWITTER_API_SECRET",
        "TWITTER_ACCESS_TOKEN",
        "TWITTER_ACCESS_TOKEN_SECRET",
    ])
    return OAuth1Session(
        envs["TWITTER_API_KEY"],
        client_secret=envs["TWITTER_API_SECRET"],
        resource_owner_key=envs["TWITTER_ACCESS_TOKEN"],
        resource_owner_secret=envs["TWITTER_ACCESS_TOKEN_SECRET"],
    )

# -------------------- Görsel yardımcıları --------------------
def load_font(size: int):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
    return ImageFont.load_default()

def percent_str(p: float, digits: int = 2) -> str:
    v = max(0.0, min(1.0, p)) * 100.0
    return f"{v:.{digits}f}%"

def draw_progress_bar(draw: ImageDraw.ImageDraw,
                      x: int, y: int, width: int, height: int,
                      progress: float,
                      segments: int = 100,
                      pad: int = 6,
                      radius: int = 12):
    draw.rounded_rectangle([x, y, x + width, y + height], radius=radius,
                           fill=None, outline=(220,220,220), width=2)
    total_inner_w = width - 2*pad
    seg_gap = 2
    seg_w = (total_inner_w - (segments - 1) * seg_gap) / segments
    seg_h = height - 2*pad
    filled_segments = int(round(max(0.0, min(1.0, progress)) * segments))

    filled_color = (40,160,240)
    empty_color  = (235,240,245)
    edge = (255,255,255)

    for i in range(segments):
        seg_x = x + pad + i * (seg_w + seg_gap)
        seg_y = y + pad
        rect = [seg_x, seg_y, seg_x + seg_w, seg_y + seg_h]
        draw.rectangle(rect, fill=(filled_color if i < filled_segments else empty_color))
    draw.line([x + pad, y + pad, x + width - pad, y + pad], fill=edge, width=1)

# -------------------- Görsel oluşturma --------------------
def make_image(now: datetime) -> bytes:
    W, H = CANVAS_W, CANVAS_H
    img = Image.new("RGB", (W, H), color=(248,250,252))
    draw = ImageDraw.Draw(img)

    def text_wh(txt: str, font: ImageFont.ImageFont) -> Tuple[int,int]:
        l, t, r, b = draw.textbbox((0,0), txt, font=font)
        return (r-l, b-t)

    # Yazı tipleri
    title_font = load_font(72)
    date_font  = load_font(40)
    label_font = load_font(44)
    value_font = load_font(44)
    foot_font  = load_font(28)

    # Kenar boşlukları ve ölçüler (kare tuvale göre ayarlandı)
    margin_x = 80
    top_y = 90
    line_gap = 50
    bar_h = 46

    # Başlık (deterministik seçim)
    idx = (now.timetuple().tm_yday * 24 + now.hour) % len(CATCHY_TITLES)
    title = CATCHY_TITLES[idx] if CATCHY_TITLES else "Zaman İlerlemesi"
    tw, th = text_wh(title, title_font)
    draw.text(((W - tw)//2, top_y), title, fill=(20,24,28), font=title_font)

    # Türkçe tarih satırı
    date_line = format_tr_datetime_line(now)
    dw, dh = text_wh(date_line, date_font)
    draw.text(((W - dw)//2, top_y + th + 16), date_line, fill=(80,90,100), font=date_font)

    # İlerlemeler
    yp, mp, dp = year_progress(now), month_progress(now), day_progress(now)

    section_y = top_y + th + 16 + dh + 70
    blocks = [
        (f"Yıl {now.year}", yp),
        (f"Ay {tr_month_name(now.month)}", mp),
        ("Gün", dp),
    ]

    for idx, (label, p) in enumerate(blocks):
        y = section_y + idx * (bar_h + 2 * line_gap + 24)
        lw, lh = text_wh(label, label_font)
        draw.text((margin_x, y), label, fill=(30,34,40), font=label_font)

        val = percent_str(p, digits=2)
        vw, vh = text_wh(val, value_font)
        draw.text((W - margin_x - vw, y), val, fill=(30,34,40), font=value_font)

        bar_y = y + lh + 18
        draw_progress_bar(draw, x=margin_x, y=bar_y,
                          width=W - 2*margin_x, height=bar_h,
                          progress=p, segments=100, pad=6, radius=12)

    # Footer — sahiplik ve nazik çağrı
    footer = f"© {OWNER_HANDLE} — paylaşırsan beni etiketle; ben de uğrarım."
    fw, fh = text_wh(footer, foot_font)
    draw.text(((W - fw)//2, H - fh - 40), footer, fill=(90,100,110), font=foot_font)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()

# -------------------- Medya yükleme & Tweet --------------------
def upload_media(oauth: OAuth1Session, image_bytes: bytes) -> str:
    files = {"media": ("progress.png", image_bytes, "image/png")}
    resp = oauth.post(MEDIA_UPLOAD_ENDPOINT, files=files)
    if resp.status_code >= 400:
        print("X API Hatası (media/upload):", resp.status_code, resp.text, file=sys.stderr)
        sys.exit(2)
    media_id = resp.json().get("media_id_string")
    if not media_id:
        print("X API Hatası: media_id alınamadı", file=sys.stderr)
        sys.exit(2)
    return media_id

def post_tweet_with_media(oauth: OAuth1Session, text: str, media_id: str):
    payload = {"text": text, "media": {"media_ids": [media_id]}}
    resp = oauth.post(POST_TWEET_ENDPOINT, json=payload)
    if resp.status_code >= 400:
        print("X API Hatası (tweet):", resp.status_code, resp.text, file=sys.stderr)
        sys.exit(2)
    data = resp.json()
    tweet_id = (data or {}).get("data", {}).get("id")
    print(f"Başarılı ✅ Tweet ID: {tweet_id}")
    print(f"İçerik:\n{text}")

# -------------------- Metin (caption) --------------------
def build_caption(now: datetime, yp: float, mp: float, dp: float) -> str:
    # Konumsuz, emojisiz, Türkçe ay adıyla
    lines = [
        {title},  # 20 başlıktan rastgele biri
        f"• {now.year} (YIL): {percent_str(yp, 2)}",
        f"• {tr_month_name(now.month)} (AY): {percent_str(mp, 2)}",
        f"• {tr_weekday_name(now.weekday())} (GÜN): {percent_str(dp, 2)}",
        f"Beni takip etmeyi unutma {OWNER_HANDLE}",  # footer
    ]
    text = "\n".join(lines)
    return (text[:279] + "…") if len(text) > 280 else text

# -------------------- main --------------------
def main():
    now = now_tr()
    yp, mp, dp = year_progress(now), month_progress(now), day_progress(now)
    caption = build_caption(now, yp, mp, dp)
    image_bytes = make_image(now)

    oauth = oauth1_session_from_env()
    media_id = upload_media(oauth, image_bytes)
    post_tweet_with_media(oauth, caption, media_id)

if __name__ == "__main__":
    main()
