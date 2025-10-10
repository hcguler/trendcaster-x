# main.py
# -*- coding: utf-8 -*-
"""
BIST Analiz Botu — Tek Dosya
- Hafta içi 18:30 TSİ (cron/CI dışarıdan tetikleyecek) çalışır.
- Web'den (provider A/B) BIST verilerini toplar, uzlaştırır, cache'ler.
- Gemini ile kısa başlık + tweet metni + hashtag üretir (token dostu).
- 1080×1080 tek görselde 3 tablo (Gün/Ay/Yıl kazandıranları) çizer.
- X/Twitter'a görsel + tweet atar (OAuth1).
CLI:
  python main.py --dry-run --limit 6 --out /tmp/bist.png
  python main.py --post
"""

import os
import sys
import io
import json
import argparse
import random
import traceback
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any

# Third-party libraries
import requests
from requests_oauthlib import OAuth1Session
from PIL import Image, ImageDraw, ImageFont
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential
from google import genai

# -----------------------------------
# CONFIG
# -----------------------------------
OWNER_HANDLE = os.environ.get("OWNER_HANDLE", "@durbirbakiyim")

# X/Twitter API Endpoints
POST_TWEET_ENDPOINT = "https://api.twitter.com/2/tweets"
MEDIA_UPLOAD_ENDPOINT = "https://upload.twitter.com/1.1/media/upload.json"

# Gemini
GEMINI_MODEL = "gemini-2.5-flash-preview-05-20"

# Cache
CACHE_DIR = ".cache"
CACHE_FILE = os.path.join(CACHE_DIR, "bist_latest.json")
TR_TIMEZONE = timezone(timedelta(hours=3), "Europe/Istanbul")

# Image Configuration (güncellenmiş)
CANVAS_W, CANVAS_H = 1080, 1080
MARGIN_X, MARGIN_Y = 60, 90          # üst/yan boşluklar
TABLE_TITLE_H = 36
ROW_H = 42                           # satır yüksekliği azaltıldı
HEADER_H = 64                        # header satırı kompakt
FOOTER_H = 90
TABLE_GAP_Y = 28                     # tablolar arası boşluk
TABLE_INNER_Y = 200                  # (dinamik hesap kullanılsa da sabit referans)

# Veri kaynak adresleri (örnek — gerçek seçiciler güncellenmeli)
PROVIDER_A_URL = "https://tr.investing.com/equities/most-active-stocks"
PROVIDER_B_URL = "https://www.bloomberght.com/borsa/hisseler"

# -----------------------------------
# ENV / OAUTH
# -----------------------------------
def require_env(keys: List[str]) -> dict:
    envs = {k: os.environ.get(k) for k in keys}
    missing = [k for k, v in envs.items() if not v]
    if missing:
        print(f"HATA: Eksik secret(lar): {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    return envs


def oauth1_session_from_env() -> OAuth1Session:
    envs = require_env(
        [
            "TWITTER_API_KEY",
            "TWITTER_API_SECRET",
            "TWITTER_ACCESS_TOKEN",
            "TWITTER_ACCESS_TOKEN_SECRET",
        ]
    )
    return OAuth1Session(
        envs["TWITTER_API_KEY"],
        client_secret=envs["TWITTER_API_SECRET"],
        resource_owner_key=envs["TWITTER_ACCESS_TOKEN"],
        resource_owner_secret=envs["TWITTER_ACCESS_TOKEN_SECRET"],
    )


# -----------------------------------
# LOCALE HELPERS
# -----------------------------------
_TR_MONTHS = {
    1: "Ocak",
    2: "Şubat",
    3: "Mart",
    4: "Nisan",
    5: "Mayıs",
    6: "Haziran",
    7: "Temmuz",
    8: "Ağustos",
    9: "Eylül",
    10: "Ekim",
    11: "Kasım",
    12: "Aralık",
}
_TR_WEEKDAYS = {
    0: "Pazartesi",
    1: "Salı",
    2: "Çarşamba",
    3: "Perşembe",
    4: "Cuma",
    5: "Cumartesi",
    6: "Pazar",
}


def now_tr() -> datetime:
    return datetime.now(TR_TIMEZONE)


def tr_month_name(m: int) -> str:
    return _TR_MONTHS.get(m, str(m))


def tr_weekday_name(wd: int) -> str:
    return _TR_WEEKDAYS.get(wd, "")


# -----------------------------------
# UTILITIES / PARSING
# -----------------------------------
STOCK_MODEL = Dict[str, Any]


def pct_to_float(pct_str: str) -> Optional[float]:
    try:
        return float(pct_str.strip().replace("%", "").replace(",", "."))
    except Exception:
        return None


def float_to_pct_str(value: float, decimals: int = 2) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.{decimals}f}%"


def get_common_headers() -> Dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0 Safari/537.36"
        ),
        "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
    }


# -----------------------------------
# DATA ACQUISITION (Web)
# -----------------------------------
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def fetch_provider_a() -> List[STOCK_MODEL]:
    """
    Provider A: Investing.com TR (örnek). HTML değişirse bozulur — mock veri ile dolduruluyor.
    """
    print(f"   [Provider A] Veri çekiliyor: {PROVIDER_A_URL}")
    try:
        _ = requests.get(PROVIDER_A_URL, headers=get_common_headers(), timeout=15)
        # --- Gerçek seçiciler burada olmalı ---
        stocks = [
            {
                "ticker": "ASELS",
                "name": "Aselsan",
                "pct_1d": 8.5,
                "pct_1m": 12.0,
                "pct_3m": 20.1,
                "pct_6m": 35.0,
                "pct_1y": 95.0,
                "last_updated": now_tr().timestamp(),
            },
            {
                "ticker": "THYAO",
                "name": "THY",
                "pct_1d": 7.2,
                "pct_1m": 5.5,
                "pct_3m": 10.5,
                "pct_6m": 28.0,
                "pct_1y": 110.0,
                "last_updated": now_tr().timestamp(),
            },
            {
                "ticker": "GARAN",
                "name": "Garanti",
                "pct_1d": 5.8,
                "pct_1m": 15.2,
                "pct_3m": 30.0,
                "pct_6m": 45.0,
                "pct_1y": 130.0,
                "last_updated": now_tr().timestamp(),
            },
            {
                "ticker": "EREGL",
                "name": "Ereğli",
                "pct_1d": 4.1,
                "pct_1m": 1.0,
                "pct_3m": 5.0,
                "pct_6m": 15.0,
                "pct_1y": 40.0,
                "last_updated": now_tr().timestamp(),
            },
            {
                "ticker": "TUPRS",
                "name": "Tüpraş",
                "pct_1d": -2.5,
                "pct_1m": 18.0,
                "pct_3m": 40.0,
                "pct_6m": 65.0,
                "pct_1y": 150.0,
                "last_updated": now_tr().timestamp(),
            },
            {
                "ticker": "BIMAS",
                "name": "Bim",
                "pct_1d": 9.0,
                "pct_1m": -0.5,
                "pct_3m": 15.0,
                "pct_6m": 30.0,
                "pct_1y": 80.0,
                "last_updated": now_tr().timestamp(),
            },
        ]
        print(f"   [Provider A] {len(stocks)} hisse başarıyla çekildi (Mock).")
        return stocks
    except Exception as e:
        print(f"   [Provider A] HATA: {e}")
        return []


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def fetch_provider_b() -> List[STOCK_MODEL]:
    """
    Provider B: BloombergHT (örnek). HTML değişirse bozulur — mock veri ile dolduruluyor.
    """
    print(f"   [Provider B] Veri çekiliyor: {PROVIDER_B_URL}")
    try:
        _ = requests.get(PROVIDER_B_URL, headers=get_common_headers(), timeout=15)
        # --- Gerçek seçiciler burada olmalı ---
        stocks = [
            {
                "ticker": "ASELS",
                "name": "Aselsan",
                "pct_1d": 8.4,
                "pct_1m": 12.5,
                "pct_3m": 20.0,
                "pct_6m": 35.5,
                "pct_1y": 94.8,
                "last_updated": now_tr().timestamp() - 60,
            },
            {
                "ticker": "THYAO",
                "name": "THY",
                "pct_1d": 7.3,
                "pct_1m": 5.4,
                "pct_3m": 10.6,
                "pct_6m": 28.1,
                "pct_1y": 110.5,
                "last_updated": now_tr().timestamp(),
            },
            {
                "ticker": "GARAN",
                "name": "Garanti",
                "pct_1d": 5.8,
                "pct_1m": 15.0,
                "pct_3m": 30.1,
                "pct_6m": 45.0,
                "pct_1y": 130.0,
                "last_updated": now_tr().timestamp(),
            },
            {
                "ticker": "ISCTR",
                "name": "İş Bankası",
                "pct_1d": 6.5,
                "pct_1m": 10.0,
                "pct_3m": 25.0,
                "pct_6m": 50.0,
                "pct_1y": 120.0,
                "last_updated": now_tr().timestamp(),
            },
            {
                "ticker": "TUPRS",
                "name": "Tüpraş",
                "pct_1d": -2.3,
                "pct_1m": 18.2,
                "pct_3m": 40.0,
                "pct_6m": 65.2,
                "pct_1y": 150.0,
                "last_updated": now_tr().timestamp(),
            },
        ]
        print(f"   [Provider B] {len(stocks)} hisse başarıyla çekildi (Mock).")
        return stocks
    except Exception as e:
        print(f"   [Provider B] HATA: {e}")
        return []


def reconcile_data(data_a: List[STOCK_MODEL], data_b: List[STOCK_MODEL]) -> List[STOCK_MODEL]:
    """İki kaynaktan gelen veriyi uzlaştırır ve birleştirir."""
    if not data_a and not data_b:
        return []

    all_data = {item["ticker"]: item for item in data_a}

    for item_b in data_b:
        ticker = item_b["ticker"]
        if ticker in all_data:
            item_a = all_data[ticker]
            ts_a = item_a.get("last_updated", 0)
            ts_b = item_b.get("last_updated", 0)

            if ts_b > ts_a:
                all_data[ticker] = item_b
            elif ts_a == ts_b:
                # aynı anda gelirse ortalama (basit yaklaşım)
                for key in ["pct_1d", "pct_1m", "pct_3m", "pct_6m", "pct_1y"]:
                    va = item_a.get(key)
                    vb = item_b.get(key)
                    if va is not None and vb is not None:
                        all_data[ticker][key] = (va + vb) / 2
            # ts_a > ts_b ise A kalır
        else:
            all_data[ticker] = item_b

    for stock in all_data.values():
        for key in ["pct_1d", "pct_1m", "pct_3m", "pct_6m", "pct_1y"]:
            if key not in stock or stock[key] is None:
                stock[key] = 0.0

    return list(all_data.values())


# -----------------------------------
# CACHE
# -----------------------------------
def read_cache() -> Optional[Dict[str, Any]]:
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            print(f"   [Cache] '{CACHE_FILE}' okunuyor...")
            return json.load(f)
    except Exception as e:
        print(f"   [Cache] Cache okuma hatası: {e}", file=sys.stderr)
        return None


def write_cache(data: List[STOCK_MODEL], run_dt: datetime):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        cache_data = {"timestamp": run_dt.isoformat(), "data": data}
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        print(f"   [Cache] {len(data)} hisse cache'e yazıldı.")
    except Exception as e:
        print(f"   [Cache] Cache yazma hatası: {e}", file=sys.stderr)


def get_final_data(run_dt: datetime) -> List[STOCK_MODEL]:
    data_a, data_b = [], []
    try:
        data_a = fetch_provider_a()
        data_b = fetch_provider_b()
    except Exception as e:
        print(f"UYARI: Web scraping girişimi başarısız oldu: {e}", file=sys.stderr)

    final_data = reconcile_data(data_a, data_b)

    if not final_data:
        print("!!! Web'den güncel veri çekilemedi. Cache'e dönülüyor...")
        cache = read_cache()
        if cache and cache.get("data"):
            final_data = cache["data"]
            cache_ts = datetime.fromisoformat(cache["timestamp"])
            print(f"✅ Cache verisi kullanılıyor (Tarih: {cache_ts.strftime('%d.%m.%Y %H:%M')})")
        else:
            print("!!! KRİTİK HATA: Ne web'den ne de cache'ten veri çekilemedi. Çıkılıyor.")
            sys.exit(1)
    else:
        write_cache(final_data, run_dt)

    return final_data


# -----------------------------------
# GEMINI (token dostu)
# -----------------------------------
BIST_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "analysis_title": {"type": "STRING"},
        "tweet_text": {"type": "STRING"},
        "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["analysis_title", "tweet_text", "hashtags"],
}


def generate_analysis(stock_data: List[STOCK_MODEL]) -> Dict[str, Any]:
    envs = require_env(["GEMINI_API_KEY"])
    API_KEY = envs["GEMINI_API_KEY"]
    client = genai.Client(api_key=API_KEY)

    top_daily = sorted(stock_data, key=lambda x: x["pct_1d"], reverse=True)[:3]
    top_daily_text = ", ".join(
        [f"{s['ticker']} ({float_to_pct_str(s['pct_1d'])})" for s in top_daily]
    )

    system_prompt = (
        "You output Turkish JSON for a Borsa İstanbul post. Keep it short. "
        "No emojis in title. tweet_text ≤160 chars and ends with a question. "
        "3–5 Turkish finance hashtags. Do not invent numbers."
    )

    user_query = (
        "Konu: Borsa İstanbul Günlük Kazanç Analizi. "
        f"En çok kazananlar: {top_daily_text}. "
        "Kısa başlık (3–5 kelime, emojisiz), 160 karakteri geçmeyen ve soru ile biten bir tweet metni, "
        "3–5 Türkçe finans hashtag üret. JSON dön."
    )

    print("⏳ Gemini'ye analiz isteği gönderiliyor...")
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_query,
            config={
                "system_instruction": system_prompt,
                "response_mime_type": "application/json",
                "response_schema": BIST_SCHEMA,
                "temperature": 0.7,
            },
        )
        json_string = response.text.strip()
        return json.loads(json_string)
    except Exception as e:
        print(f"Gemini API Hatası: {e}", file=sys.stderr)
        return {
            "analysis_title": "Piyasa Analizi",
            "tweet_text": "Bugünün güçlü hisseleri dikkat çekiyor. Bu ivme sürer mi?",
            "hashtags": ["#Borsa", "#BIST", "#Hisse", "#Yatırım"],
        }


# -----------------------------------
# IMAGE RENDER (güncellenmiş, 3 tablo sığdırır)
# -----------------------------------
def load_font(size: int, bold: bool = False):
    suffix = "-Bold" if bold else ""
    candidates = [
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{suffix}.ttf",
        f"/usr/share/fonts/truetype/liberation/LiberationSans{suffix}.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
    return ImageFont.load_default()


def render_table(
    draw: ImageDraw.ImageDraw,
    data: List[STOCK_MODEL],
    start_y: int,
    table_title: str,
    sort_key: str,
    limit: int,
    col_order: List[str],
    header_map: Dict[str, str],
    title_font: ImageFont.ImageFont,
    header_font: ImageFont.ImageFont,
    data_font: ImageFont.ImageFont,
) -> int:
    """Tek tabloyu çizer, bir sonraki başlangıç Y değerini döndürür."""
    W = CANVAS_W
    INNER_W = W - 2 * MARGIN_X

    sorted_data = sorted(data, key=lambda x: x.get(sort_key, 0.0), reverse=True)[:limit]

    # 6 kolon düzeni
    COL_MAP = {
        "Hisse": 0.18 * INNER_W,  # ticker
        "P1": 0.164 * INNER_W,    # lead
        "P2": 0.164 * INNER_W,
        "P3": 0.164 * INNER_W,
        "P4": 0.164 * INNER_W,
        "P5": 0.164 * INNER_W,
    }

    # 1) Tablo başlığı
    draw.text((MARGIN_X, start_y), table_title, fill=(50, 50, 50), font=title_font)
    current_y = start_y + TABLE_TITLE_H + 6

    # 2) Header
    x_pos = MARGIN_X
    labels = ["Hisse", "P1", "P2", "P3", "P4", "P5"]
    header_labels = ["Hisse"] + [header_map.get(c, c) for c in col_order[1:]]
    for key, label in zip(labels, header_labels):
        width = COL_MAP[key]
        if key == "Hisse":
            # ⬅️ Hisse sütun başlığı SOLA hizalı
            draw.text(
                (x_pos, current_y),
                label,
                fill=(0, 0, 0),
                font=header_font,
                anchor="lt",
            )
        else:
            # ⬅️ Diğer başlıklar sağa hizalı kalsın
            draw.text(
                (x_pos + width - 4, current_y),
                label,
                fill=(0, 0, 0),
                font=header_font,
                anchor="rt",
            )
        x_pos += width

    current_y += HEADER_H - 28
    draw.line([MARGIN_X, current_y, W - MARGIN_X, current_y], fill=(185, 185, 185), width=1)
    current_y += 4

    # 3) Satırlar
    for i, stock in enumerate(sorted_data):
        x_pos = MARGIN_X
        row_y = current_y + i * ROW_H

        # Hisse/Ticker — sol
        draw.text((x_pos, row_y), stock["ticker"], fill=(20, 20, 20), font=data_font)
        x_pos += COL_MAP["Hisse"]

        # Yüzdeler — sağ hizalı
        for data_key, col in zip(col_order[1:], ["P1", "P2", "P3", "P4", "P5"]):
            width = COL_MAP[col]
            v = stock.get(data_key)
            if v is not None:
                pct_str = float_to_pct_str(v, 2)
                color = (0, 128, 0) if v >= 0 else (204, 0, 0)
                draw.text((x_pos + width - 4, row_y), pct_str, fill=color, font=data_font, anchor="rt")
            x_pos += width

        # satır ayırıcı
        if i < len(sorted_data) - 1:
            y_sep = row_y + ROW_H - 6
            draw.line([MARGIN_X, y_sep, W - MARGIN_X, y_sep], fill=(235, 235, 235), width=1)

    return current_y + len(sorted_data) * ROW_H + 12


def render_image(title: str, stock_data: List[STOCK_MODEL], limit: int) -> bytes:
    """1080x1080 tek görselde üç tabloyu sığacak şekilde çizer."""
    W, H = CANVAS_W, CANVAS_H
    BG_COLOR = (248, 248, 252)
    FRAME_COLOR = (0, 102, 204)

    img = Image.new("RGB", (W, H), color=BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Fontlar
    header_font = load_font(44, bold=True)        # ana başlık
    sub_header_font = load_font(28)               # handle
    table_title_font = load_font(28, bold=True)
    table_header_font = load_font(19, bold=True)
    table_data_font = load_font(22)
    foot_font = load_font(20)

    # 1) Üst başlık (iki satır)
    main_title = "DUR BİR BAKAYIM — BIST Analizi"
    draw.text((W // 2, MARGIN_Y), main_title, fill=(20, 30, 40), font=header_font, anchor="mm")
    draw.text((W // 2, MARGIN_Y + 44), f"({OWNER_HANDLE})", fill=(80, 90, 100), font=sub_header_font, anchor="mm")

    # İnce çizgi
    line_y = MARGIN_Y + 44 + 20
    draw.line([MARGIN_X, line_y, W - MARGIN_X, line_y], fill=FRAME_COLOR, width=2)

    # 2) Dinamik satır sayısı hesabı
    top_block_bottom = line_y + 12
    bottom_reserved = FOOTER_H + 20
    total_table_space = H - top_block_bottom - bottom_reserved
    per_table_fixed = TABLE_TITLE_H + (HEADER_H - 28) + 4 + 12  # başlık + header + çizgiler + padding
    space_for_rows = max(0, total_table_space - 2 * TABLE_GAP_Y - 3 * per_table_fixed)
    rows_fit = max(5, min(limit, int(space_for_rows // (5 * ROW_H))))
    if rows_fit < 5:
        rows_fit = 5

    HEADER_MAP = {
        "ticker": "Hisse",
        "pct_1d": "Günlük %",
        "pct_1m": "Aylık %",
        "pct_3m": "3 Aylık %",
        "pct_6m": "6 Aylık %",
        "pct_1y": "Yıllık %",
    }

    current_y = top_block_bottom + 16

    # Tablo 1: Gün
    col_order_1d = ["ticker", "pct_1d", "pct_1m", "pct_3m", "pct_6m", "pct_1y"]
    current_y = render_table(
        draw,
        stock_data,
        current_y,
        "Günün Kazandıranları",
        "pct_1d",
        rows_fit,
        col_order_1d,
        HEADER_MAP,
        table_title_font,
        table_header_font,
        table_data_font,
    )
    current_y += TABLE_GAP_Y

    # Tablo 2: Ay
    col_order_1m = ["ticker", "pct_1m", "pct_1d", "pct_3m", "pct_6m", "pct_1y"]
    current_y = render_table(
        draw,
        stock_data,
        current_y,
        "Ayın Kazandıranları",
        "pct_1m",
        rows_fit,
        col_order_1m,
        HEADER_MAP,
        table_title_font,
        table_header_font,
        table_data_font,
    )
    current_y += TABLE_GAP_Y

    # Tablo 3: Yıl
    col_order_1y = ["ticker", "pct_1y", "pct_1d", "pct_1m", "pct_3m", "pct_6m"]
    current_y = render_table(
        draw,
        stock_data,
        current_y,
        "Yılın Kazandıranları",
        "pct_1y",
        rows_fit,
        col_order_1y,
        HEADER_MAP,
        table_title_font,
        table_header_font,
        table_data_font,
    )

    # 3) Footer — Türkçe tarih + gün adı
    now = now_tr()
    date_line = f"{now.day:02d} {tr_month_name(now.month)} {now.year}, {tr_weekday_name(now.weekday())}"
    footer_text = f"Veri Güncel: {date_line}"
    draw.text((W // 2, H - 40), footer_text, fill=(80, 90, 100), font=foot_font, anchor="ms")

    # Dış çerçeve
    draw.rectangle([20, 20, W - 20, H - 20], outline=FRAME_COLOR, width=5)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# -----------------------------------
# TWEET COMPOSITION
# -----------------------------------
def compose_tweet(gemini_data: Dict[str, Any], stock_data: List[STOCK_MODEL]) -> str:
    analysis_title = gemini_data["analysis_title"]
    tweet_text = gemini_data["tweet_text"]
    gemini_hashtags = [f"#{tag.strip('#')}" for tag in gemini_data["hashtags"]]

    top_tickers = sorted(stock_data, key=lambda x: x["pct_1d"], reverse=True)[:6]
    ticker_hashtags = [f"#{s['ticker']}" for s in top_tickers]

    template = "🚨 {title}\n\n{text}\n\n{tags_line}\n{ticker_line}\n{handle}"

    all_hashtags = list(set(gemini_hashtags + ticker_hashtags))
    random.shuffle(all_hashtags)

    def attempt(text, tags, tickers):
        tags_line = " ".join(tags)
        ticker_line = " ".join(tickers)
        composed = template.format(
            title=analysis_title,
            text=text,
            tags_line=tags_line,
            ticker_line=ticker_line,
            handle=OWNER_HANDLE,
        )
        return composed.strip()

    final_text = attempt(tweet_text, all_hashtags, ticker_hashtags)
    if len(final_text) <= 280:
        return final_text

    # 1) ticker'ları 3'e indir
    final_text = attempt(tweet_text, all_hashtags, ticker_hashtags[:3])
    if len(final_text) <= 280:
        return final_text

    # 2) hashtag 3'e indir, ticker'ları 0–2'ye indir
    gem_pruned = gemini_hashtags[:3]
    tick_pruned = ticker_hashtags[:2]
    mixed = list(set(gem_pruned + tick_pruned))
    final_text = attempt(tweet_text, mixed, [])
    if len(final_text) <= 280:
        return final_text

    # 3) tweet_text kısalt
    short_text = tweet_text.split("?")[0].strip()
    if len(short_text) > 120:
        short_text = tweet_text[:117] + "..."
    final_text = attempt(short_text, mixed, [])
    if len(final_text) <= 280:
        return final_text

    # 4) son çare
    return final_text[:277] + "..."


# -----------------------------------
# X/Twitter Client
# -----------------------------------
def upload_media(oauth: OAuth1Session, image_bytes: bytes) -> str:
    files = {"media": ("bist_analysis.png", image_bytes, "image/png")}
    resp = oauth.post(MEDIA_UPLOAD_ENDPOINT, files=files)
    resp.raise_for_status()
    media_id = resp.json().get("media_id_string")
    if not media_id:
        raise ValueError("X API Hatası: media_id alınamadı.")
    return media_id


def post_tweet(oauth: OAuth1Session, text: str, media_id: str):
    payload = {"text": text, "media": {"media_ids": [media_id]}}
    resp = oauth.post(POST_TWEET_ENDPOINT, json=payload)
    if resp.status_code == 403:
        raise PermissionError("X API Hatası: 403 Forbidden. Read/Write izinlerini kontrol edin.")
    resp.raise_for_status()
    tweet_id = (resp.json() or {}).get("data", {}).get("id")
    print(f"✅ Başarılı Tweet ID: {tweet_id}")


# -----------------------------------
# CLI / MAIN
# -----------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="BIST Multi-Period Kazanç Analiz Botu.")
    parser.add_argument("--post", action="store_true", default=False, help="Görseli X/Twitter hesabına postala.")
    parser.add_argument("--dry-run", action="store_true", help="Postalamadan veri çek + görsel oluştur + konsola yaz.")
    parser.add_argument("--limit", type=int, default=6, help="Her tabloda gösterilecek hisse üst sınırı (default 6).")
    parser.add_argument("--out", type=str, default="bist_output.png", help="--dry-run modunda görselin kaydedileceği yol.")
    args = parser.parse_args()

    # Varsayılan: dry-run (hiç argüman yoksa)
    if args.dry_run:
        args.post = False
    elif not args.post:
        args.dry_run = True
    return args


def main():
    args = parse_args()
    run_dt = now_tr()
    print(f"\n--- BIST Analiz Botu Başlatıldı ({run_dt.strftime('%d.%m.%Y %H:%M:%S')}) ---")

    try:
        # 1) Veri
        stock_data = get_final_data(run_dt)
        if not stock_data:
            raise RuntimeError("Veri çekilemedi ve Cache boş.")

        # 2) Gemini
        gemini_data = generate_analysis(stock_data)

        # 3) Görsel
        image_bytes = render_image(gemini_data["analysis_title"], stock_data, args.limit)
        print("✅ Görsel başarıyla oluşturuldu.")

        # 4) Tweet metni
        final_tweet_text = compose_tweet(gemini_data, stock_data)
        print(f"✅ Tweet hazırlandı. Uzunluk: {len(final_tweet_text)}")

        if args.dry_run:
            with open(args.out, "wb") as f:
                f.write(image_bytes)
            print("\n--- DRY RUN ---")
            print(f"Görsel kaydedildi: {args.out}")
            print("\n--- TWEET ---\n" + final_tweet_text + "\n")
        else:
            print("\n--- X POST MODU ---")
            oauth = oauth1_session_from_env()
            media_id = upload_media(oauth, image_bytes)
            print(f"✅ Medya yüklendi (ID: {media_id}). Tweet gönderiliyor...")
            post_tweet(oauth, final_tweet_text, media_id)
            print("🎉 İşlem tamam!")

    except Exception as e:
        print(f"\n!!! KRİTİK HATA: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
