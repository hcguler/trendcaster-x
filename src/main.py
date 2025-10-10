# main.py
# -*- coding: utf-8 -*-
"""
BIST Analiz Botu — Tek Dosya (GERÇEK VERİ)
- Hafta içi 18:30 TSİ (cron/CI dışarıdan tetikleyecek) çalışır.
- Yahoo Finance CSV'den BIST verilerini toplar, uzlaştırır (tek provider) ve cache'ler.
- Gemini ile kısa başlık + tweet metni + hashtag üretir (token dostu).
- 1280×1280 tek görselde 3 tablo (Gün/30 Gün/360 Gün kazandıranları) çizer.
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
from typing import List, Dict, Optional, Any, Tuple

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

# Image Configuration
CANVAS_W, CANVAS_H = 1280, 1280
MARGIN_X, MARGIN_Y = 60, 90
TABLE_TITLE_H = 36
ROW_H = 42
HEADER_H = 64
FOOTER_H = 90
TABLE_GAP_Y = 28

# Yahoo Finance CSV
YAHOO_CSV_TMPL = (
    "https://query1.finance.yahoo.com/v7/finance/download/{symbol}"
    "?period1={p1}&period2={p2}&interval=1d&events=history&includeAdjustedClose=true"
)

# -----------------------------------
# LOCALE HELPERS / DYNAMIC DATES
# -----------------------------------
_TR_MONTHS = {
    1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
    7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık",
}
_TR_WEEKDAYS = {
    0: "Pazartesi", 1: "Salı", 2: "Çarşamba", 3: "Perşembe", 4: "Cuma", 5: "Cumartesi", 6: "Pazar",
}


def now_tr() -> datetime:
    return datetime.now(TR_TIMEZONE)


def tr_month_name(m: int) -> str:
    return _TR_MONTHS.get(m, str(m))


def tr_weekday_name(wd: int) -> str:
    return _TR_WEEKDAYS.get(wd, "")


def get_dynamic_periods(now: datetime) -> Dict[str, Any]:
    dt_30d = now - timedelta(days=30)
    dt_360d = now - timedelta(days=360)
    return {
        "period_30d": {
            "key": "pct_30d",
            "title": f"Son 30 Gün ({dt_30d.strftime('%d.%m')}-Bugün)",
            "header": "30 Gün %",
        },
        "period_360d": {
            "key": "pct_360d",
            "title": f"Son 360 Gün ({dt_360d.strftime('%d.%m.%Y')}-Bugün)",
            "header": "360 Gün %",
        }
    }

# -----------------------------------
# UTILITIES
# -----------------------------------
STOCK_MODEL = Dict[str, Any]


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
# YAHOO FINANCE HELPERS
# -----------------------------------
def unix_ts(dt: datetime) -> int:
    return int(dt.timestamp())


def download_yahoo_csv(symbol: str, start: datetime, end: datetime) -> List[Dict[str, str]]:
    """symbol için [start, end) aralığında günlük CSV'yi indirir ve dict listesi döndürür."""
    url = YAHOO_CSV_TMPL.format(symbol=symbol, p1=unix_ts(start), p2=unix_ts(end))
    r = requests.get(url, headers=get_common_headers(), timeout=25)
    if r.status_code != 200:
        raise RuntimeError(f"Yahoo CSV hatası {symbol}: {r.status_code}")
    lines = [ln for ln in r.text.splitlines() if ln.strip()]
    if len(lines) <= 1:
        return []
    headers = [h.strip() for h in lines[0].split(",")]
    rows: List[Dict[str, str]] = []
    for ln in lines[1:]:
        parts = ln.split(",")
        if len(parts) != len(headers):
            continue
        row = {h: v for h, v in zip(headers, parts)}
        if row.get("Close") and row["Close"] != "null":
            rows.append(row)
    return rows


def parse_date(s: str) -> datetime:
    # Yahoo 'YYYY-MM-DD'
    y, m, d = s.split("-")
    return datetime(int(y), int(m), int(d), tzinfo=TR_TIMEZONE)


def closest_on_or_before(rows: List[Dict[str, str]], target: datetime) -> Optional[Dict[str, str]]:
    candidates = [r for r in rows if parse_date(r["Date"]) <= target]
    if not candidates:
        return None
    candidates.sort(key=lambda r: parse_date(r["Date"]), reverse=True)
    return candidates[0]


def previous_trading_row(rows: List[Dict[str, str]], date_iso: str) -> Optional[Dict[str, str]]:
    d = parse_date(date_iso)
    before = [r for r in rows if parse_date(r["Date"]) < d]
    if not before:
        return None
    before.sort(key=lambda r: parse_date(r["Date"]), reverse=True)
    return before[0]


def pct_change(cur: float, prev: float) -> float:
    if prev == 0:
        return 0.0
    return (cur - prev) / prev * 100.0

# -----------------------------------
# BIST100 TİCKER LİSTESİ (Wikipedia)
# -----------------------------------
WIKI_BIST100 = "https://en.wikipedia.org/wiki/BIST_100"

def normalize_ticker(name: str) -> Optional[str]:
    """
    Wikipedia metninden sembol tahmini (ASELS → ASELS.IS).
    Fazla agresif davranmayalım; yalnızca tamamen büyük harf ve harf/rakam içeren 3–6 karakterleri kabul edelim.
    """
    n = name.strip().upper()
    tr_map = str.maketrans("İŞĞÜÖÇÂÊÎÛÔ", "ISGUOCAEIUO")
    n = n.translate(tr_map)
    n = "".join(ch for ch in n if ch.isalnum())
    if 3 <= len(n) <= 6:
        return f"{n}.IS"
    return None


def fetch_bist100_tickers() -> List[str]:
    try:
        r = requests.get(WIKI_BIST100, headers=get_common_headers(), timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        tickers: List[str] = []

        # 1) Eğer sayfada ".IS" geçen <code> veya link metinleri varsa doğrudan çek
        for el in soup.select("code"):
            t = el.get_text(strip=True)
            if t.endswith(".IS") and 3 <= len(t.split(".")[0]) <= 6:
                tickers.append(t)

        for a in soup.select('a[href*=".IS"]'):
            t = a.get_text(strip=True)
            if t.endswith(".IS") and 3 <= len(t.split(".")[0]) <= 6:
                tickers.append(t)

        tickers = list(dict.fromkeys(tickers))  # unique, order-preserving

        # 2) Yeterli değilse tablo hücrelerinden şirket adlarını sembole çevirerek deneriz
        if len(tickers) < 50:
            for td in soup.select("table tbody tr td:first-child"):
                guess = normalize_ticker(td.get_text(" ", strip=True))
                if guess:
                    tickers.append(guess)
            tickers = list(dict.fromkeys(tickers))

        # 3) Hâlâ düşükse çok bilinen yedekler:
        common = [
            "AKBNK.IS","GARAN.IS","ISCTR.IS","YKBNK.IS","THYAO.IS","BIMAS.IS","ARCLK.IS",
            "ASELS.IS","KCHOL.IS","SAHOL.IS","TUPRS.IS","TCELL.IS","SISE.IS","EREGL.IS",
        ]
        for c in common:
            if c not in tickers:
                tickers.append(c)

        # Yahoo tarafında bulunamayanlar için aşırı uzun/şüphelileri at
        clean = []
        for t in tickers:
            root = t.split(".")[0]
            if 2 < len(root) <= 6 and root.isalnum():
                clean.append(t)
        return clean
    except Exception:
        # Tamamen yedek liste
        return [
            "AKBNK.IS","GARAN.IS","ISCTR.IS","YKBNK.IS","THYAO.IS","BIMAS.IS","ARCLK.IS",
            "ASELS.IS","KCHOL.IS","SAHOL.IS","TUPRS.IS","TCELL.IS","SISE.IS","EREGL.IS",
        ]

# -----------------------------------
# VERİ TOPLAMA (GERÇEK)
# -----------------------------------
def compute_periods(now: datetime) -> Dict[str, datetime]:
    return {
        "d1_prev": now - timedelta(days=1),
        "d30": now - timedelta(days=30),
        "d90": now - timedelta(days=90),
        "d180": now - timedelta(days=180),
        "d360": now - timedelta(days=360),
    }


def build_stock_record(symbol: str, rows: List[Dict[str, str]], now: datetime) -> Optional[STOCK_MODEL]:
    if not rows:
        return None

    # t (bugün/son işlem)
    t_row = closest_on_or_before(rows, now)
    if not t_row:
        return None
    t_date = t_row["Date"]
    t_close = float(t_row["Close"])

    # t-1 iş günü
    prev_row = previous_trading_row(rows, t_date)
    pct_1d = pct_change(t_close, float(prev_row["Close"])) if prev_row else 0.0

    periods = compute_periods(now)
    d30_row = closest_on_or_before(rows, periods["d30"])
    d90_row = closest_on_or_before(rows, periods["d90"])
    d180_row = closest_on_or_before(rows, periods["d180"])
    d360_row = closest_on_or_before(rows, periods["d360"])

    pct_30d = pct_change(t_close, float(d30_row["Close"])) if d30_row else 0.0
    pct_90d = pct_change(t_close, float(d90_row["Close"])) if d90_row else 0.0
    pct_180d = pct_change(t_close, float(d180_row["Close"])) if d180_row else 0.0
    pct_360d = pct_change(t_close, float(d360_row["Close"])) if d360_row else 0.0

    return {
        "ticker": symbol.replace(".IS", ""),
        "name": symbol.replace(".IS", ""),
        "last_price": t_close,
        "pct_1d": pct_1d,
        "pct_30d": pct_30d,
        "pct_3m": pct_90d,
        "pct_6m": pct_180d,
        "pct_360d": pct_360d,
        "last_updated": now.timestamp(),
    }


def fetch_all_from_yahoo(now: datetime, tickers: List[str]) -> List[STOCK_MODEL]:
    """
    Tüm semboller için son 370 günü kapsayacak şekilde CSV indirir ve yüzdeleri hesaplar.
    """
    start = now - timedelta(days=380)   # güvenlik payı
    end = now + timedelta(days=1)       # Yahoo period2 exclusive

    out: List[STOCK_MODEL] = []
    for idx, sym in enumerate(tickers, 1):
        try:
            rows = download_yahoo_csv(sym, start, end)
            rec = build_stock_record(sym, rows, now)
            if rec is not None:
                out.append(rec)
            else:
                # print(f"[WARN] Hesaplanamadı: {sym}")
                pass
        except Exception as e:
            # print(f"[ERR] {sym}: {e}")
            continue
    return out

# -----------------------------------
# UZLAŞTIRMA (tek provider olduğu için sade)
# -----------------------------------
def reconcile_data(data_a: List[STOCK_MODEL], data_b: List[STOCK_MODEL]) -> List[STOCK_MODEL]:
    """
    Burada tek gerçek kaynak Yahoo olduğu için 'data_b' boş gelecek.
    Fonksiyon imzasını koruyoruz.
    """
    if data_a:
        return data_a
    return data_b or []

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
    try:
        tickers = fetch_bist100_tickers()
        print(f"✅ BIST evreni yüklendi: {len(tickers)} sembol")
        data_yahoo = fetch_all_from_yahoo(run_dt, tickers)
    except Exception as e:
        print(f"UYARI: Veri çekimi başarısız oldu: {e}", file=sys.stderr)
        data_yahoo = []

    final_data = reconcile_data(data_yahoo, [])
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
# IMAGE RENDER
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
    W = CANVAS_W
    INNER_W = W - 2 * MARGIN_X

    sorted_data = sorted(
        data, 
        key=lambda x: x.get(sort_key, 0.0), 
        reverse=True
    )[:limit]

    COL_MAP = {
        "Hisse": 0.18 * INNER_W,
        "P1": 0.164 * INNER_W,
        "P2": 0.164 * INNER_W,
        "P3": 0.164 * INNER_W,
        "P4": 0.164 * INNER_W,
        "P5": 0.164 * INNER_W,
    }

    draw.text((MARGIN_X, start_y), table_title, fill=(50, 50, 50), font=title_font)
    current_y = start_y + TABLE_TITLE_H + 6

    x_pos = MARGIN_X
    labels = ["Hisse", "P1", "P2", "P3", "P4", "P5"]
    header_labels = ["Hisse"] + [header_map.get(c, c) for c in col_order[1:]]
    for key, label in zip(labels, header_labels):
        width = COL_MAP[key]
        if key == "Hisse":
            draw.text((x_pos, current_y), label, fill=(0,0,0), font=header_font, anchor="lt")
        else:
            draw.text((x_pos + width - 4, current_y), label, fill=(0,0,0), font=header_font, anchor="rt")
        x_pos += width

    current_y += HEADER_H - 28
    draw.line([MARGIN_X, current_y, W - MARGIN_X, current_y], fill=(185,185,185), width=1)
    current_y += 4

    for i, stock in enumerate(sorted_data):
        x_pos = MARGIN_X
        row_y = current_y + i * ROW_H

        draw.text((x_pos, row_y), stock["ticker"], fill=(20,20,20), font=data_font)
        x_pos += COL_MAP["Hisse"]

        for data_key, col in zip(col_order[1:], ["P1","P2","P3","P4","P5"]):
            width = COL_MAP[col]
            v = stock.get(data_key)
            if v is not None:
                pct_str = float_to_pct_str(v, 2)
                color = (0,128,0) if v >= 0 else (204,0,0)
                draw.text((x_pos + width - 4, row_y), pct_str, fill=color, font=data_font, anchor="rt")
            x_pos += width

        if i < len(sorted_data) - 1:
            y_sep = row_y + ROW_H - 6
            draw.line([MARGIN_X, y_sep, W - MARGIN_X, y_sep], fill=(235,235,235), width=1)

    return current_y + len(sorted_data) * ROW_H + 12


def render_image(title: str, stock_data: List[STOCK_MODEL], limit: int) -> bytes:
    W, H = CANVAS_W, CANVAS_H
    BG_COLOR = (248, 248, 252)
    FRAME_COLOR = (0, 102, 204)
    now = now_tr()
    periods = get_dynamic_periods(now)
    key_30d = periods["period_30d"]["key"]
    key_360d = periods["period_360d"]["key"]

    img = Image.new("RGB", (W, H), color=BG_COLOR)
    draw = ImageDraw.Draw(img)

    header_font = load_font(44, bold=True)
    sub_header_font = load_font(28)
    table_title_font = load_font(28, bold=True)
    table_header_font = load_font(19, bold=True)
    table_data_font = load_font(22)
    foot_font = load_font(20)

    main_title = "DUR BİR BAKAYIM — BIST Analizi"
    draw.text((W // 2, MARGIN_Y), main_title, fill=(20,30,40), font=header_font, anchor="mm")
    draw.text((W // 2, MARGIN_Y + 44), f"({OWNER_HANDLE})", fill=(80,90,100), font=sub_header_font, anchor="mm")

    line_y = MARGIN_Y + 44 + 20
    draw.line([MARGIN_X, line_y, W - MARGIN_X, line_y], fill=FRAME_COLOR, width=2)

    top_block_bottom = line_y + 12
    bottom_reserved = FOOTER_H + 20
    total_table_space = H - top_block_bottom - bottom_reserved
    per_table_fixed = TABLE_TITLE_H + (HEADER_H - 28) + 4 + 12
    space_for_rows = max(0, total_table_space - 2 * TABLE_GAP_Y - 3 * per_table_fixed)
    rows_fit = max(5, min(limit, int(space_for_rows // (5 * ROW_H))))
    if rows_fit < 5:
        rows_fit = 5

    HEADER_MAP = {
        "ticker": "Hisse",
        "pct_1d": "Günlük %",
        key_30d: periods["period_30d"]["header"],
        "pct_3m": "3 Aylık %",
        "pct_6m": "6 Aylık %",
        key_360d: periods["period_360d"]["header"],
    }

    current_y = top_block_bottom + 16

    col_order_1d = ["ticker", "pct_1d", key_30d, key_360d, "pct_3m", "pct_6m"]
    current_y = render_table(
        draw, stock_data, current_y,
        "Günün Kazandıranları",
        "pct_1d", rows_fit, col_order_1d, HEADER_MAP,
        table_title_font, table_header_font, table_data_font,
    )
    current_y += TABLE_GAP_Y

    col_order_30d = ["ticker", key_30d, "pct_1d", key_360d, "pct_3m", "pct_6m"]
    current_y = render_table(
        draw, stock_data, current_y,
        periods["period_30d"]["title"],
        key_30d, rows_fit, col_order_30d, HEADER_MAP,
        table_title_font, table_header_font, table_data_font,
    )
    current_y += TABLE_GAP_Y

    col_order_360d = ["ticker", key_360d, "pct_1d", key_30d, "pct_3m", "pct_6m"]
    current_y = render_table(
        draw, stock_data, current_y,
        periods["period_360d"]["title"],
        key_360d, rows_fit, col_order_360d, HEADER_MAP,
        table_title_font, table_header_font, table_data_font,
    )

    date_line = f"{now.day:02d} {tr_month_name(now.month)} {now.year}, {tr_weekday_name(now.weekday())}"
    footer_text = f"Veri Güncel: {date_line}"
    draw.text((W // 2, H - 40), footer_text, fill=(80,90,100), font=foot_font, anchor="ms")

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

    final_text = attempt(tweet_text, all_hashtags, ticker_hashtags[:3])
    if len(final_text) <= 280:
        return final_text

    gem_pruned = gemini_hashtags[:3]
    tick_pruned = ticker_hashtags[:2]
    mixed = list(set(gem_pruned + tick_pruned))
    final_text = attempt(tweet_text, mixed, [])
    if len(final_text) <= 280:
        return final_text

    short_text = tweet_text.split("?")[0].strip()
    if len(short_text) > 120:
        short_text = tweet_text[:117] + "..."
    final_text = attempt(short_text, mixed, [])
    if len(final_text) <= 280:
        return final_text

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
    parser = argparse.ArgumentParser(description="BIST Multi-Period Kazanç Analiz Botu (Gerçek Veri).")
    parser.add_argument("--post", action="store_true", default=False, help="Görseli X/Twitter hesabına postala.")
    parser.add_argument("--dry-run", action="store_true", help="Postalamadan veri çek + görsel oluştur + konsola yaz.")
    parser.add_argument("--limit", type=int, default=6, help="Her tabloda gösterilecek hisse üst sınırı (default 6).")
    parser.add_argument("--out", type=str, default="bist_output.png", help="--dry-run modunda görselin kaydedileceği yol.")
    args = parser.parse_args()

    # Varsayılan davranış: argüman verilmediyse dry-run
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
        # 1) Veri (Yahoo + Wikipedia)
        stock_data = get_final_data(run_dt)
        if not stock_data:
            raise RuntimeError("Veri çekilemedi ve Cache boş.")
        print(f"✅ Hesaplanan kayıt sayısı: {len(stock_data)}")

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