# src/common.py
import io
import os
from datetime import datetime, timezone, timedelta
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont

# -------------------- Sabitler --------------------
CANVAS_W = 1080
CANVAS_H = 1080
OWNER_HANDLE = "@durbirbakiyim"

CATCHY_TITLES = [
    "Zaman Akışı","Takvim Hızı","Bugün Kaydı","Günlük Tempo","Zaman Nabzı",
    "Kronometre Hal","Anın Özeti","Zaman Çizgisi","Günlük İlerleme","Takvim Nabzı",
    "Zaman Ölçümü","Günün Durumu","Zaman Özeti","Bugün İlerleme","Takvim Özeti",
    "Günlük Rapor","Zaman Grafiği","Anlık İlerleme","Zaman Panosu","Takvim Panosu",
]

# -------------------- Zaman yardımcıları --------------------
def tz_tr():
    return timezone(timedelta(hours=3))  # Türkiye sabit UTC+3

def now_tr():
    return datetime.now(tz_tr())

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
    1:"Ocak",2:"Şubat",3:"Mart",4:"Nisan",5:"Mayıs",6:"Haziran",
    7:"Temmuz",8:"Ağustos",9:"Eylül",10:"Ekim",11:"Kasım",12:"Aralık"
}
_TR_WEEKDAYS = {  # Monday=0
    0:"Pazartesi",1:"Salı",2:"Çarşamba",3:"Perşembe",4:"Cuma",5:"Cumartesi",6:"Pazar"
}

def tr_month_name(m: int) -> str:
    return _TR_MONTHS.get(m, str(m))

def tr_weekday_name(wd: int) -> str:
    return _TR_WEEKDAYS.get(wd, "")

def format_tr_datetime_line(dt: datetime) -> str:
    # dd.MM.yyyy (Günadı) HH:mm — (dakika kullanıyoruz; saniye yok)
    return f"{dt.day:02d}.{dt.month:02d}.{dt.year:04d} ({tr_weekday_name(dt.weekday())}) {dt.hour:02d}:{dt.minute:02d}"

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

def select_title(dt: datetime) -> str:
    if not CATCHY_TITLES:
        return "Zaman İlerlemesi"
    idx = (dt.timetuple().tm_yday * 24 + dt.hour) % len(CATCHY_TITLES)
    return CATCHY_TITLES[idx]

# -------------------- Görsel oluşturma --------------------
def make_image(dt: datetime, title: str) -> bytes:
    W, H = CANVAS_W, CANVAS_H
    img = Image.new("RGB", (W, H), color=(248,250,252))
    draw = ImageDraw.Draw(img)

    def text_wh(txt: str, font: ImageFont.ImageFont) -> Tuple[int,int]:
        l, t, r, b = draw.textbbox((0,0), txt, font=font)
        return (r-l, b-t)

    title_font = load_font(72)
    date_font  = load_font(40)
    label_font = load_font(44)
    value_font = load_font(44)
    foot_font  = load_font(28)

    margin_x = 80
    top_y = 90
    line_gap = 50
    bar_h = 46

    # Başlık
    tw, th = text_wh(title, title_font)
    draw.text(((W - tw)//2, top_y), title, fill=(20,24,28), font=title_font)

    # TR tarih satırı
    date_line = format_tr_datetime_line(dt)
    dw, dh = text_wh(date_line, date_font)
    draw.text(((W - dw)//2, top_y + th + 16), date_line, fill=(80,90,100), font=date_font)

    # İlerlemeler
    yp, mp, dp = year_progress(dt), month_progress(dt), day_progress(dt)

    section_y = top_y + th + 16 + dh + 70
    blocks = [
        (f"{dt.year}", yp),
        (f"{dt.day} {tr_month_name(dt.month)}", mp),
        (f"{dt.hour:02d}:{dt.minute:02d} {tr_weekday_name(dt.weekday())}", dp),
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

    footer = f"© {OWNER_HANDLE} — paylaşırsan beni etiketle; ben de uğrarım."
    fw, fh = text_wh(footer, foot_font)
    draw.text(((W - fw)//2, H - fh - 40), footer, fill=(90,100,110), font=foot_font)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()

# -------------------- Metin (caption) --------------------
def build_caption(dt: datetime, title: str) -> str:
    yp, mp, dp = year_progress(dt), month_progress(dt), day_progress(dt)
    lines = [
        title,
        f"• {dt.year}: {percent_str(yp, 2)}",
        f"• {dt.day} {tr_month_name(dt.month)}: {percent_str(mp, 2)}",
        f"• {dt.hour:02d}:{dt.minute:02d} {tr_weekday_name(dt.weekday())}: {percent_str(dp, 2)}",
        f"Beni takip etmeyi unutma {OWNER_HANDLE}, #TrendingNow, #Gündem, #TrendTweets",
    ]
    text = "\n".join(lines)
    return (text[:279] + "…") if len(text) > 280 else text

# -------------------- Slot yardımcıları --------------------
FOUR_HOUR_SLOTS = [0, 4, 8, 12, 16, 20]

def slot_floor(dt: datetime) -> datetime:
    h = (dt.hour // 4) * 4
    return dt.replace(hour=h, minute=0, second=0, microsecond=0)

def today_slots(dt: datetime) -> list:
    base = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return [base.replace(hour=h) for h in FOUR_HOUR_SLOTS]
