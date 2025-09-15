import os, sys, io, math
from datetime import datetime, timezone, timedelta
from typing import List, Tuple
from requests_oauthlib import OAuth1Session

from PIL import Image, ImageDraw, ImageFont

POST_TWEET_ENDPOINT = "https://api.twitter.com/2/tweets"
MEDIA_UPLOAD_ENDPOINT = "https://upload.twitter.com/1.1/media/upload.json"

# ---- Zaman yardımcıları ------------------------------------------------------

def istanbul_now():
    tz_tr = timezone(timedelta(hours=3))
    return datetime.now(tz_tr)

def year_progress(dt: datetime) -> float:
    start = datetime(dt.year, 1, 1, tzinfo=dt.tzinfo)
    end   = datetime(dt.year + 1, 1, 1, tzinfo=dt.tzinfo)
    return (dt - start).total_seconds() / (end - start).total_seconds()

def month_progress(dt: datetime) -> float:
    start = datetime(dt.year, dt.month, 1, tzinfo=dt.tzinfo)
    # sonraki ayın ilk günü
    if dt.month == 12:
        end = datetime(dt.year + 1, 1, 1, tzinfo=dt.tzinfo)
    else:
        end = datetime(dt.year, dt.month + 1, 1, tzinfo=dt.tzinfo)
    return (dt - start).total_seconds() / (end - start).total_seconds()

def day_progress(dt: datetime) -> float:
    start = datetime(dt.year, dt.month, dt.day, tzinfo=dt.tzinfo)  # 00:00
    end   = start + timedelta(days=1)
    return (dt - start).total_seconds() / (end - start).total_seconds()

# ---- Ortam / OAuth -----------------------------------------------------------

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

# ---- Görsel üretimi ----------------------------------------------------------

def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """
    Sistemde varsa DejaVuSans.ttf kullanır; yoksa PIL'in default bitmap fontuna düşer.
    """
    # En yaygın açık fontlardan bazılarını sırayla dene:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
    return ImageFont.load_default()

def draw_progress_bar(draw: ImageDraw.ImageDraw,
                      x: int, y: int, width: int, height: int,
                      progress: float,
                      segments: int = 100,
                      pad: int = 2,
                      radius: int = 8):
    """
    Segmented (100 dilimlik) bir progress bar çizer.
    - progress: 0.0–1.0
    """
    # Arka plan çerçevesi (rounded rectangle görünümü)
    # PIL'in rounded rectangle API'sı eski sürümlerde sınırlı olabilir, basitçe köşeleri oval gibi resmedelim:
    draw.rounded_rectangle([x, y, x + width, y + height], radius=radius, fill=None, outline=(220, 220, 220), width=2)

    # Segment hesapları
    total_inner_w = width - 2 * pad
    seg_gap = 2  # segmentler arası boşluk
    seg_w = (total_inner_w - (segments - 1) * seg_gap) / segments
    seg_h = height - 2 * pad
    filled_segments = int(round(progress * segments))

    # Renkler (açık tasarım; koyu mod için renkleri güncelleyebilirsiniz)
    filled_color = (40, 160, 240)     # mavi ton
    empty_color  = (235, 240, 245)    # açık gri/mavi
    edge = (255, 255, 255)

    # Segmentleri çiz
    for i in range(segments):
        seg_x = x + pad + i * (seg_w + seg_gap)
        seg_y = y + pad
        rect = [seg_x, seg_y, seg_x + seg_w, seg_y + seg_h]
        if i < filled_segments:
            draw.rectangle(rect, fill=filled_color, outline=None)
        else:
            draw.rectangle(rect, fill=empty_color, outline=None)

    # Parlaklık efekti (üst kısma hafif bir çizgi)
    draw.line([x + pad, y + pad, x + width - pad, y + pad], fill=edge, width=1)

def percent_str(p: float, digits: int = 1) -> str:
    return f"{max(0.0, min(100.0, p * 100.0)):.{digits}f}%"

def make_image(now: datetime) -> bytes:
    """
    1080x1350 dikey görsel oluşturur (Instagram/Twitter için uygun boy).
    Üstte başlık ve tarih; altta Yıl / Ay / Gün için 100 dilimlik progress bar'lar.
    """
    W, H = 1080, 1350
    img = Image.new("RGB", (W, H), color=(248, 250, 252))
    draw = ImageDraw.Draw(img)

    # Yazı tipleri
    title_font   = load_font(72)
    date_font    = load_font(42)
    label_font   = load_font(44)
    value_font   = load_font(44)
    foot_font    = load_font(28)

    # Kenar boşlukları
    margin_x = 80
    top_y = 120
    line_gap = 60
    bar_h = 48

    # Başlık
    title = "Zaman İlerlemesi — İstanbul"
    tw, th = draw.textsize(title, font=title_font)
    draw.text(((W - tw) / 2, top_y), title, fill=(20, 24, 28), font=title_font)

    # Tarih-saat
    date_str = now.strftime("%Y-%m-%d %H:%M:%S %Z")
    dw, dh = draw.textsize(date_str, font=date_font)
    draw.text(((W - dw) / 2, top_y + th + 20), date_str, fill=(80, 90, 100), font=date_font)

    # Progress hesapları
    yp = year_progress(now)
    mp = month_progress(now)
    dp = day_progress(now)

    # Bölüm başlıkları ve barlar
    section_y = top_y + th + 20 + dh + 100

    blocks: list[Tuple[str, float]] = [
        (f"Yıl {now.year}", yp),
        (now.strftime("Ay %B"), mp),
        ("Gün", dp),
    ]

    for idx, (label, p) in enumerate(blocks):
        y = section_y + idx * (bar_h + 2 * line_gap + 30)

        # Etiket
        lw, lh = draw.textsize(label, font=label_font)
        draw.text((margin_x, y), label, fill=(30, 34, 40), font=label_font)

        # Yüzde değeri (sağa hizalı)
        val = percent_str(p, digits=2)
        vw, vh = draw.textsize(val, font=value_font)
        draw.text((W - margin_x - vw, y), val, fill=(30, 34, 40), font=value_font)

        # Bar
        bar_y = y + lh + 20
        draw_progress_bar(draw,
                          x=margin_x,
                          y=bar_y,
                          width=W - 2 * margin_x,
                          height=bar_h,
                          progress=p,
                          segments=100,
                          pad=6,
                          radius=12)

    # Alt bilgi
    footer = "Yıl/Ay/Gün ilerlemeleri 100 dilimlik çubuklarla görselleştirilmiştir."
    fw, fh = draw.textsize(footer, font=foot_font)
    draw.text(((W - fw) / 2, H - fh - 60), footer, fill=(90, 100, 110), font=foot_font)

    # PNG baytları
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()

# ---- X (Twitter) API: medya yükleme + tweet ----------------------------------

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

# ---- Metin oluşturma ---------------------------------------------------------

def build_caption(now: datetime, yp: float, mp: float, dp: float) -> str:
    lines = [
        "🗓️ Türkiye/İstanbul Zaman İlerlemesi",
        f"• Yıl {now.year}: {percent_str(yp, 2)}",
        f"• {now.strftime('Ay %B')}: {percent_str(mp, 2)}",
        f"• Gün: {percent_str(dp, 2)}",
        now.strftime("⏱️ %Y-%m-%d %H:%M:%S %Z"),
    ]
    text = "\n".join(lines)
    # 280 sınırına güvenli kırpma (görsel zaten bilgiyi taşıyor)
    return (text[:279] + "…") if len(text) > 280 else text

# ---- main --------------------------------------------------------------------

def main():
    now = istanbul_now()
    yp, mp, dp = year_progress(now), month_progress(now), day_progress(now)
    caption = build_caption(now, yp, mp, dp)
    image_bytes = make_image(now)

    oauth = oauth1_session_from_env()
    media_id = upload_media(oauth, image_bytes)
    post_tweet_with_media(oauth, caption, media_id)

if __name__ == "__main__":
    main()
