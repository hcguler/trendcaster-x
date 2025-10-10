İşte mock verileri kaldırılmış ve gerçek BIST verilerini Yahoo Finance'den çeken güncellenmiş kod:

```python
# main.py
# -*- coding: utf-8 -*-
"""
BIST Analiz Botu — Tek Dosya
- Hafta içi 18:30 TSİ (cron/CI dışarıdan tetikleyecek) çalışır.
- Yahoo Finance'den BIST verilerini toplar, cache'ler.
- Gemini ile kısa başlık + tweet metni + hashtag üretir.
- 1080×1080 tek görselde 3 tablo (Gün/30 Gün/360 Gün kazandıranları) çizer.
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
import yfinance as yf
import pandas as pd

# -----------------------------------
# CONFIG
# -----------------------------------
OWNER_HANDLE = os.environ.get("OWNER_HANDLE", "@durbirbakiyim")

# X/Twitter API Endpoints
POST_TWEET_ENDPOINT = "https://api.twitter.com/2/tweets"
MEDIA_UPLOAD_ENDPOINT = "https://upload.twitter.com/1.1/media/upload.json"

# Gemini
GEMINI_MODEL = "gemini-2.0-flash-exp"

# Cache
CACHE_DIR = ".cache"
CACHE_FILE = os.path.join(CACHE_DIR, "bist_latest.json")
TICKER_CACHE_FILE = os.path.join(CACHE_DIR, "bist_tickers.json")

TR_TIMEZONE = timezone(timedelta(hours=3), "Europe/Istanbul")

# Image Configuration
CANVAS_W, CANVAS_H = 1280, 1280
MARGIN_X, MARGIN_Y = 60, 90
TABLE_TITLE_H = 36
ROW_H = 42
HEADER_H = 64
FOOTER_H = 90
TABLE_GAP_Y = 28

# BIST Ana Hisseler (En likit 50 hisse)
BIST_CORE_TICKERS = [
    "AKBNK", "AKSEN", "ALARK", "ASELS", "BIMAS", "DOHOL", "EKGYO", "ENJSA",
    "ENKAI", "EREGL", "FROTO", "GARAN", "GUBRF", "HALKB", "ISCTR", "KCHOL",
    "KONTR", "KOZAA", "KOZAL", "KRDMD", "MGROS", "ODAS", "PGSUS", "PETKM",
    "SAHOL", "SASA", "SISE", "SOKM", "TAVHL", "TCELL", "THYAO", "TKFEN",
    "TOASO", "TSKB", "TUPRS", "TURSG", "TTKOM", "VAKBN", "VESBE", "YKBNK",
    "BERA", "GESAN", "LOGO", "MAVI", "OYAKC", "PARSN", "SDTTR", "SNGYO",
    "TMSN", "ZOREN"
]

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
# LOCALE HELPERS / DYNAMIC DATES
# -----------------------------------
_TR_MONTHS = {
    1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
    7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık",
}

_TR_WEEKDAYS = {
    0: "Pazartesi", 1: "Salı", 2: "Çarşamba", 3: "Perşembe",
    4: "Cuma", 5: "Cumartesi", 6: "Pazar",
}


def now_tr() -> datetime:
    return datetime.now(TR_TIMEZONE)


def tr_month_name(m: int) -> str:
    return _TR_MONTHS.get(m, str(m))


def tr_weekday_name(wd: int) -> str:
    return _TR_WEEKDAYS.get(wd, "")


def get_dynamic_periods(now: datetime) -> Dict[str, Any]:
    """Sorgulanan günden itibaren son 30 ve 360 günün etiketlerini oluşturur."""
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
# BIST TICKER LIST (Yahoo Finance)
# -----------------------------------
def get_bist_tickers() -> List[str]:
    """
    BIST hisse listesini döndürür.
    Cache varsa kullanır, yoksa sabit listeden yararlanır.
    """
    # Cache kontrolü
    if os.path.exists(TICKER_CACHE_FILE):
        try:
            with open(TICKER_CACHE_FILE, "r", encoding="utf-8") as f:
                cache_data = json.load(f)
                cache_time = datetime.fromisoformat(cache_data["timestamp"])
                # Cache 7 günden yeniyse kullan
                if (datetime.now() - cache_time).days < 7:
                    print(f" [Ticker Cache] {len(cache_data['tickers'])} hisse cache'den okundu.")
                    return cache_data["tickers"]
        except Exception as e:
            print(f" [Ticker Cache] Okuma hatası: {e}")
    
    # Sabit BIST listesini kullan
    tickers = [f"{t}.IS" for t in BIST_CORE_TICKERS]
    
    # Cache'e kaydet
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(TICKER_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": datetime.now().isoformat(),
                "tickers": tickers
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f" [Ticker Cache] Yazma hatası: {e}")
    
    return tickers


# -----------------------------------
# DATA ACQUISITION (Yahoo Finance)
# -----------------------------------
def calculate_percentage_change(current: float, past: float) -> float:
    """İki fiyat arasındaki yüzde değişimi hesaplar."""
    if past == 0 or pd.isna(past) or pd.isna(current):
        return 0.0
    return ((current - past) / past) * 100


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def fetch_stock_data(ticker: str, now: datetime) -> Optional[STOCK_MODEL]:
    """
    Tek bir hisse için Yahoo Finance'den veri çeker.
    Güncel, 30 gün öncesi ve 360 gün öncesi fiyatları alır.
    """
    try:
        # Yahoo Finance ticker formatı: HISSE.IS
        stock = yf.Ticker(ticker)
        
        # Son 400 günlük veri çek (margin için)
        end_date = now.date()
        start_date = end_date - timedelta(days=400)
        
        hist = stock.history(start=start_date, end=end_date)
        
        if hist.empty:
            print(f"  [!] {ticker}: Veri bulunamadı")
            return None
        
        # Tarihleri hesapla
        target_date = now.date()
        date_30d = target_date - timedelta(days=30)
        date_360d = target_date - timedelta(days=360)
        
        # En yakın işlem günlerini bul
        hist_dates = hist.index.date
        
        # Güncel fiyat (son kapanış)
        current_price = hist['Close'].iloc[-1]
        
        # 30 gün öncesi fiyat
        price_30d = None
        for i in range(35):  # 30 günden 35 güne kadar ara (hafta sonları için)
            check_date = target_date - timedelta(days=30 + i)
            if check_date in hist_dates:
                price_30d = hist.loc[hist.index.date == check_date, 'Close'].iloc[0]
                break
        
        # 360 gün öncesi fiyat
        price_360d = None
        for i in range(370):  # 360 günden 370 güne kadar ara
            check_date = target_date - timedelta(days=360 + i)
            if check_date in hist_dates:
                price_360d = hist.loc[hist.index.date == check_date, 'Close'].iloc[0]
                break
        
        # Günlük değişim (önceki güne göre)
        if len(hist) >= 2:
            prev_close = hist['Close'].iloc[-2]
            pct_1d = calculate_percentage_change(current_price, prev_close)
        else:
            pct_1d = 0.0
        
        # 30 günlük değişim
        pct_30d = calculate_percentage_change(current_price, price_30d) if price_30d else 0.0
        
        # 360 günlük değişim
        pct_360d = calculate_percentage_change(current_price, price_360d) if price_360d else 0.0
        
        # 3 aylık değişim (90 gün)
        price_90d = None
        for i in range(95):
            check_date = target_date - timedelta(days=90 + i)
            if check_date in hist_dates:
                price_90d = hist.loc[hist.index.date == check_date, 'Close'].iloc[0]
                break
        pct_3m = calculate_percentage_change(current_price, price_90d) if price_90d else 0.0
        
        # 6 aylık değişim (180 gün)
        price_180d = None
        for i in range(190):
            check_date = target_date - timedelta(days=180 + i)
            if check_date in hist_dates:
                price_180d = hist.loc[hist.index.date == check_date, 'Close'].iloc[0]
                break
        pct_6m = calculate_percentage_change(current_price, price_180d) if price_180d else 0.0
        
        # Hisse kodundan .IS ekini çıkar
        ticker_clean = ticker.replace(".IS", "")
        
        # Hisse adını al (info'dan)
        try:
            stock_info = stock.info
            stock_name = stock_info.get('longName', ticker_clean)
            # Türkçe karakter ve kısa isim için düzenleme
            if len(stock_name) > 20:
                stock_name = ticker_clean
        except:
            stock_name = ticker_clean
        
        periods = get_dynamic_periods(now)
        key_30d = periods["period_30d"]["key"]
        key_360d = periods["period_360d"]["key"]
        
        return {
            "ticker": ticker_clean,
            "name": stock_name,
            "current_price": float(current_price),
            "pct_1d": float(pct_1d),
            key_30d: float(pct_30d),
            "pct_3m": float(pct_3m),
            "pct_6m": float(pct_6m),
            key_360d: float(pct_360d),
            "last_updated": now.timestamp(),
        }
        
    except Exception as e:
        print(f"  [!] {ticker}: Hata - {e}")
        return None


def fetch_all_bist_stocks(now: datetime) -> List[STOCK_MODEL]:
    """
    Tüm BIST hisselerinin verilerini Yahoo Finance'den çeker.
    """
    print(" [Yahoo Finance] BIST hisseleri çekiliyor...")
    
    tickers = get_bist_tickers()
    stocks = []
    
    total = len(tickers)
    for idx, ticker in enumerate(tickers, 1):
        print(f"  [{idx}/{total}] {ticker} işleniyor...", end="\r")
        
        stock_data = fetch_stock_data(ticker, now)
        if stock_data:
            stocks.append(stock_data)
    
    print(f"\n [Yahoo Finance] {len(stocks)}/{total} hisse başarıyla çekildi.")
    return stocks


# -----------------------------------
# CACHE
# -----------------------------------
def read_cache() -> Optional[Dict[str, Any]]:
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            print(f" [Cache] '{CACHE_FILE}' okunuyor...")
            return json.load(f)
    except Exception as e:
        print(f" [Cache] Cache okuma hatası: {e}", file=sys.stderr)
        return None


def write_cache(data: List[STOCK_MODEL], run_dt: datetime):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        cache_data = {"timestamp": run_dt.isoformat(), "data": data}
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        print(f" [Cache] {len(data)} hisse cache'e yazıldı.")
    except Exception as e:
        print(f" [Cache] Cache yazma hatası: {e}", file=sys.stderr)


def get_final_data(run_dt: datetime) -> List[STOCK_MODEL]:
    """
    Yahoo Finance'den veri çeker, başarısız olursa cache'e döner.
    """
    final_data = []
    
    try:
        final_data = fetch_all_bist_stocks(run_dt)
    except Exception as e:
        print(f"UYARI: Yahoo Finance verisi çekilemedi: {e}", file=sys.stderr)
    
    if not final_data:
        print("!!! Yahoo Finance'den güncel veri çekilemedi. Cache'e dönülüyor...")
        cache = read_cache()
        if cache and cache.get("data"):
            final_data = cache["data"]
            cache_ts = datetime.fromisoformat(cache["timestamp"])
            print(f"✅ Cache verisi kullanılıyor (Tarih: {cache_ts.strftime('%d.%m.%Y %H:%M')})")
        else:
            print("!!! KRİTİK HATA: Ne Yahoo Finance'den ne de cache'ten veri çekilemedi. Çıkılıyor.")
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
    
    # Gemini'ye sadece günlük veriyi gönderiyoruz
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
    """Tek tabloyu çizer, bir sonraki başlangıç Y değerini döndürür."""
    W = CANVAS_W
    INNER_W = W - 2 * MARGIN_X
    
    # Ana sıralama anahtarını kontrol et
    sorted_data = sorted(
        data, key=lambda x: x.get(sort_key, 0.0), reverse=True
    )[:limit]
    
    # 6 kolon düzeni
    COL_MAP = {
        "Hisse": 0.18 * INNER_W,
        "P1": 0.164 * INNER_W,
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
            draw.text(
                (x_pos, current_y),
                label,
                fill=(0, 0, 0),
                font=header_font,
                anchor="lt",
            )
        else:
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
    
    now = now_tr()
    periods = get_dynamic_periods(now)
    key_30d = periods["period_30d"]["key"]
    key_360d = periods["period_360d"]["key"]
    
    img = Image.new("RGB", (W, H), color=BG_COLOR)
    draw = ImageDraw.Draw(img)
    
    # Fontlar
    header_font = load_font(44, bold=True)
    sub_header_font = load_font(28)
    table_title_font = load_font(28, bold=True)
    table_header_font = load_font(19, bold=True)
    table_data_font = load_font(22)
    foot_font = load_font(20)
    
    # 1) Üst başlık
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
    per_table_fixed = TABLE_TITLE_H + (HEADER_H - 28) + 4 + 12
    space_for_rows = max(0, total_table_space - 2 * TABLE_GAP_Y - 3 * per_table_fixed)
    rows_fit = max(5, min(limit, int(space_for_rows // (3 * ROW_H))))
    
    if rows_fit < 5:
        rows_fit = 5
    
    # DİNAMİK HEADER MAP
    HEADER_MAP = {
        "ticker": "Hisse",
        "pct_1d": "Günlük %",
        key_30d: periods["period_30d"]["header"],
        "pct_3m": "3 Aylık %",
        "pct_6m": "6 Aylık %",
        key_360d: periods["period_360d"]["header"],
    }
    
    current_y = top_block_bottom + 16
    
    # Tablo 1: Günlük Kazandıranlar
    col_order_1d = ["ticker", "pct_1d", key_30d, key_360d, "pct_3m", "pct_6m"]
    current_y = render_table(
        draw, stock_data, current_y, "Günün Kazandıranları", "pct_1d", rows_fit,
        col_order_1d, HEADER_MAP, table_title_font, table_header_font, table_data_font,
    )
    current_y += TABLE_GAP_Y
    
    # Tablo 2: Son 30 Gün Kazandıranları
        # Tablo 2: Son 30 Gün Kazandıranları
    col_order_30d = ["ticker", key_30d, "pct_1d", key_360d, "pct_3m", "pct_6m"]
    current_y = render_table(
        draw, stock_data, current_y, periods["period_30d"]["title"], key_30d, rows_fit,
        col_order_30d, HEADER_MAP, table_title_font, table_header_font, table_data_font,
    )
    current_y += TABLE_GAP_Y
    
    # Tablo 3: Son 360 Gün Kazandıranları
    col_order_360d = ["ticker", key_360d, "pct_1d", key_30d, "pct_3m", "pct_6m"]
    current_y = render_table(
        draw, stock_data, current_y, periods["period_360d"]["title"], key_360d, rows_fit,
        col_order_360d, HEADER_MAP, table_title_font, table_header_font, table_data_font,
    )
    
    # 3) Footer — Türkçe tarih + gün adı
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
    parser.add_argument("--post", action="store_true", default=False, 
                       help="Görseli X/Twitter hesabına postala.")
    parser.add_argument("--dry-run", action="store_true",
                       help="Postalamadan veri çek + görsel oluştur + konsola yaz.")
    parser.add_argument("--limit", type=int, default=6,
                       help="Her tabloda gösterilecek hisse üst sınırı (default 6).")
    parser.add_argument("--out", type=str, default="bist_output.png",
                       help="--dry-run modunda görselin kaydedileceği yol.")
    
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