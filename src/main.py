import os
import sys
import io
import json
import argparse
import random
import time
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any, Tuple

# Third-party libraries
import requests
from requests_oauthlib import OAuth1Session
from PIL import Image, ImageDraw, ImageFont
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential
from google import genai

# --- CONSTANTS & CONFIGURATION ---
OWNER_HANDLE = os.environ.get("OWNER_HANDLE", "@durbirbakiyim")

# API Endpoints
POST_TWEET_ENDPOINT = "https://api.twitter.com/2/tweets"
MEDIA_UPLOAD_ENDPOINT = "https://upload.twitter.com/1.1/media/upload.json"
GEMINI_MODEL = 'gemini-2.5-flash-preview-05-20'

# Cache Configuration
CACHE_DIR = ".cache"
CACHE_FILE = os.path.join(CACHE_DIR, "bist_latest.json")
TR_TIMEZONE = timezone(timedelta(hours=3), 'Europe/Istanbul')

# Image Configuration
CANVAS_W, CANVAS_H = 1080, 1080
MARGIN_X, MARGIN_Y = 60, 80
TABLE_TITLE_H = 40
ROW_H = 50
HEADER_H = 80
FOOTER_H = 100
TABLE_GAP_Y = 40
TABLE_INNER_Y = 240 # Başlangıç Y koordinatı

# Canonical Data Model (Dict structure for simplicity in a single file)
# pct_1d: Günlük, pct_1m: Aylık, pct_1y: Yıllık
STOCK_MODEL = Dict[str, Any]

# Web Scraping Sources (These URLs are illustrative and may need adjustment)
PROVIDER_A_URL = "https://tr.investing.com/equities/most-active-stocks" 
PROVIDER_B_URL = "https://www.bloomberght.com/borsa/hisseler"

# --- LOCALE & TIME HELPERS ---
_TR_MONTHS = {
    1:"Ocak", 2:"Şubat", 3:"Mart", 4:"Nisan", 5:"Mayıs", 6:"Haziran",
    7:"Temmuz", 8:"Ağustos", 9:"Eylül", 10:"Ekim", 11:"Kasım", 12:"Aralık"
}
_TR_WEEKDAYS = {
    0:"Pazartesi", 1:"Salı", 2:"Çarşamba", 3:"Perşembe",
    4:"Cuma", 5:"Cumartesi", 6:"Pazar"
}

def now_tr() -> datetime:
    """Returns the current time in TRT (UTC+3) timezone."""
    return datetime.now(TR_TIMEZONE)

def tr_month_name(m: int) -> str:
    return _TR_MONTHS.get(m, str(m))

def tr_weekday_name(wd: int) -> str:
    return _TR_WEEKDAYS.get(wd, "")

# --- DATA PARSING & UTILITIES ---

def pct_to_float(pct_str: str) -> Optional[float]:
    """Converts percentage string (e.g., '+5.12%') to float (e.g., 5.12)."""
    try:
        return float(pct_str.strip().replace('%', '').replace(',', '.'))
    except Exception:
        return None

def float_to_pct_str(value: float, decimals: int = 2) -> str:
    """Converts float to formatted percentage string (e.g., 5.12 -> +5.12%)."""
    sign = '+' if value >= 0 else ''
    return f"{sign}{value:.{decimals}f}%"

def get_common_headers() -> Dict[str, str]:
    """Returns common headers for web scraping."""
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept-Language': 'tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7',
    }

# --- DATA ACQUISITION (WEB SCRAPING) ---

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
def fetch_provider_a() -> List[STOCK_MODEL]:
    """
    Provider A: Investing.com TR'den hisse verilerini çekme denemesi.
    
    UYARI: Bu scraping kodu, hedef sitenin HTML yapısı değiştiğinde BOZULACAKTIR.
    Gerçek bir üretim ortamında, bu selector'lar düzenli olarak kontrol edilmelidir.
    Şu an için, kodun geri kalanını test etmek amacıyla Mock Veri/Basitleştirilmiş Çekim kullanılır.
    """
    print(f"   [Provider A] Veri çekiliyor: {PROVIDER_A_URL}")
    try:
        response = requests.get(PROVIDER_A_URL, headers=get_common_headers(), timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # --- Gerçek Scraping Başlangıcı (Örnek Selector'lar) ---
        # Örnek: Genellikle tablo satırlarını içeren bir selector kullanılır.
        # rows = soup.select('#top-gainers-table tbody tr') 
        # stocks = []
        # for row in rows[:20]: # İlk 20 hisseyi çek
        #     # Ticker, 1D, 1M, 1Y kolonlarını çekme mantığı buraya gelmeli
        #     ... 
        #     stocks.append(...)
        # --- Gerçek Scraping Bitişi ---

        # Test için zengin Mock Veri
        stocks = [
            {'ticker': 'ASELS', 'name': 'Aselsan', 'pct_1d': 8.5, 'pct_1m': 12.0, 'pct_3m': 20.1, 'pct_6m': 35.0, 'pct_1y': 95.0, 'last_updated': now_tr().timestamp()},
            {'ticker': 'THYAO', 'name': 'THY', 'pct_1d': 7.2, 'pct_1m': 5.5, 'pct_3m': 10.5, 'pct_6m': 28.0, 'pct_1y': 110.0, 'last_updated': now_tr().timestamp()},
            {'ticker': 'GARAN', 'name': 'Garanti', 'pct_1d': 5.8, 'pct_1m': 15.2, 'pct_3m': 30.0, 'pct_6m': 45.0, 'pct_1y': 130.0, 'last_updated': now_tr().timestamp()},
            {'ticker': 'EREGL', 'name': 'Ereğli', 'pct_1d': 4.1, 'pct_1m': 1.0, 'pct_3m': 5.0, 'pct_6m': 15.0, 'pct_1y': 40.0, 'last_updated': now_tr().timestamp()},
            {'ticker': 'TUPRS', 'name': 'Tüpraş', 'pct_1d': -2.5, 'pct_1m': 18.0, 'pct_3m': 40.0, 'pct_6m': 65.0, 'pct_1y': 150.0, 'last_updated': now_tr().timestamp()},
            {'ticker': 'BIMAS', 'name': 'Bim', 'pct_1d': 9.0, 'pct_1m': -0.5, 'pct_3m': 15.0, 'pct_6m': 30.0, 'pct_1y': 80.0, 'last_updated': now_tr().timestamp()},
        ]
        
        print(f"   [Provider A] {len(stocks)} hisse başarıyla çekildi.")
        return stocks

    except Exception as e:
        print(f"   [Provider A] HATA: {e}")
        return []

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
def fetch_provider_b() -> List[STOCK_MODEL]:
    """
    Provider B: BloombergHT'den hisse verilerini çekme denemesi.
    """
    print(f"   [Provider B] Veri çekiliyor: {PROVIDER_B_URL}")
    try:
        response = requests.get(PROVIDER_B_URL, headers=get_common_headers(), timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        # --- Gerçek Scraping Başlangıcı (Örnek Selector'lar) ---
        # rows = soup.select('.dataTable tbody tr') 
        # stocks = []
        # for row in rows[:20]:
        #     ...
        #     stocks.append(...)
        # --- Gerçek Scraping Bitişi ---

        # Test için Mock Veri (A'dan biraz farklı değerler ve farklı bir hisse)
        stocks = [
            {'ticker': 'ASELS', 'name': 'Aselsan', 'pct_1d': 8.4, 'pct_1m': 12.5, 'pct_3m': 20.0, 'pct_6m': 35.5, 'pct_1y': 94.8, 'last_updated': now_tr().timestamp() - 60}, # 1 dakika eski
            {'ticker': 'THYAO', 'name': 'THY', 'pct_1d': 7.3, 'pct_1m': 5.4, 'pct_3m': 10.6, 'pct_6m': 28.1, 'pct_1y': 110.5, 'last_updated': now_tr().timestamp()},
            {'ticker': 'GARAN', 'name': 'Garanti', 'pct_1d': 5.8, 'pct_1m': 15.0, 'pct_3m': 30.1, 'pct_6m': 45.0, 'pct_1y': 130.0, 'last_updated': now_tr().timestamp()},
            {'ticker': 'ISCTR', 'name': 'İş Bankası', 'pct_1d': 6.5, 'pct_1m': 10.0, 'pct_3m': 25.0, 'pct_6m': 50.0, 'pct_1y': 120.0, 'last_updated': now_tr().timestamp()},
            {'ticker': 'TUPRS', 'name': 'Tüpraş', 'pct_1d': -2.3, 'pct_1m': 18.2, 'pct_3m': 40.0, 'pct_6m': 65.2, 'pct_1y': 150.0, 'last_updated': now_tr().timestamp()},
        ]

        print(f"   [Provider B] {len(stocks)} hisse başarıyla çekildi.")
        return stocks

    except Exception as e:
        print(f"   [Provider B] HATA: {e}")
        return []

def reconcile_data(data_a: List[STOCK_MODEL], data_b: List[STOCK_MODEL]) -> List[STOCK_MODEL]:
    """İki kaynaktan gelen veriyi uzlaştırır ve birleştirir."""
    if not data_a and not data_b:
        return []
    
    all_data = {item['ticker']: item for item in data_a}

    for item_b in data_b:
        ticker = item_b['ticker']
        if ticker in all_data:
            item_a = all_data[ticker]
            
            # Uzlaştırma: Ana kıstas en güncel veri
            ts_a = item_a.get('last_updated', 0)
            ts_b = item_b.get('last_updated', 0)
            
            if ts_b > ts_a:
                all_data[ticker] = item_b
            elif ts_a > ts_b:
                # A'nın verisi daha yeni, B'yi yok say
                pass
            else:
                # Zaman damgaları aynı veya yok. Ortalamayı almayı deneyelim (Özellikle % değerlerinde)
                for key in ['pct_1d', 'pct_1m', 'pct_1y', 'pct_3m', 'pct_6m']:
                    val_a = item_a.get(key, 0)
                    val_b = item_b.get(key, 0)
                    if val_a is not None and val_b is not None:
                         # Değerler arasındaki fark büyükse (örneğin %5), ortalama alma riskli olabilir,
                         # ancak burada basitlik için ortalama alalım.
                         all_data[ticker][key] = (val_a + val_b) / 2
        else:
            # Yeni hisseyi ekle
            all_data[ticker] = item_b

    # Eksik periyotları (3m, 6m vb.) 0 ile doldur
    for stock in all_data.values():
        for key in ['pct_1d', 'pct_1m', 'pct_3m', 'pct_6m', 'pct_1y']:
            if key not in stock or stock[key] is None:
                stock[key] = 0.0

    return list(all_data.values())

# --- CACHING LOGIC ---

def read_cache() -> Optional[Dict[str, Any]]:
    """Loads the last successful BIST data from cache."""
    if not os.path.exists(CACHE_FILE):
        return None
    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            print(f"   [Cache] '{CACHE_FILE}' okunuyor...")
            return json.load(f)
    except Exception as e:
        print(f"   [Cache] Cache okuma hatası: {e}", file=sys.stderr)
        return None

def write_cache(data: List[STOCK_MODEL], run_dt: datetime):
    """Writes the successful data to cache."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        cache_data = {
            "timestamp": run_dt.isoformat(),
            "data": data
        }
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        print(f"   [Cache] {len(data)} hisse cache'e yazıldı.")
    except Exception as e:
        print(f"   [Cache] Cache yazma hatası: {e}", file=sys.stderr)

def get_final_data(run_dt: datetime) -> List[STOCK_MODEL]:
    """Fetches, reconciles data, or loads from cache."""
    data_a = []
    data_b = []
    
    # 1. Veri Çekme Girişimi
    try:
        data_a = fetch_provider_a()
        data_b = fetch_provider_b()
    except Exception as e:
        print(f"UYARI: Web scraping girişimi başarısız oldu: {e}", file=sys.stderr)
    
    final_data = reconcile_data(data_a, data_b)

    # 2. Uzlaştırma Sonucu Başarısızsa, Cache'i Kullan
    if not final_data:
        print("!!! Web'den güncel veri çekilemedi. Cache'e dönülüyor...")
        cache = read_cache()
        if cache and cache.get('data'):
            final_data = cache['data']
            cache_ts = datetime.fromisoformat(cache['timestamp'])
            print(f"✅ Cache verisi kullanılıyor (Tarih: {cache_ts.strftime('%d.%m.%Y %H:%M')})")
        else:
            print("!!! KRİTİK HATA: Ne web'den ne de cache'ten veri çekilemedi. Çıkılıyor.")
            sys.exit(1)
    else:
        # Başarılı olursa cache'i yaz
        write_cache(final_data, run_dt)

    return final_data

# --- GEMINI CLIENT ---

BIST_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "analysis_title": {"type": "STRING"},
        "tweet_text": {"type": "STRING"},
        "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}}
    },
    "required": ["analysis_title", "tweet_text", "hashtags"]
}

def generate_analysis(stock_data: List[STOCK_MODEL]) -> Dict[str, Any]:
    """Generates analysis using Gemini API."""
    envs = require_env(["GEMINI_API_KEY"])
    API_KEY = envs["GEMINI_API_KEY"]
    client = genai.Client(api_key=API_KEY)

    # Veriyi Gemini'ya sunmak için özetle
    top_daily = sorted(stock_data, key=lambda x: x['pct_1d'], reverse=True)[:3]
    top_daily_text = ", ".join([f"{s['ticker']} ({float_to_pct_str(s['pct_1d'])})" for s in top_daily])

    system_prompt = (
        "You output Turkish JSON for a Borsa İstanbul post. Keep it short. No emojis in title. "
        "tweet_text ≤160 chars and ends with a question. 3–5 Turkish finance hashtags. Do not invent numbers."
    )

    user_query = (
        "Konu: Borsa İstanbul Günlük Kazanç Analizi. "
        "En çok kazananlar: " + top_daily_text + ". "
        "Bu verilere göre piyasa eğilimini yorumla. "
        "Kısa başlık (3–5 kelime, emojisiz), 160 karakteri geçmeyen ve soru ile biten bir tweet metni, 3–5 Türkçe finans hashtag üret. JSON dön."
    )

    print("⏳ Gemini'ye analiz isteği gönderiliyor...")

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_query,
            config={
                'system_instruction': system_prompt,
                'response_mime_type': 'application/json',
                'response_schema': BIST_SCHEMA,
                'temperature': 0.7
            }
        )

        json_string = response.text.strip()
        return json.loads(json_string)

    except Exception as e:
        print(f"Gemini API Hatası: {e}", file=sys.stderr)
        # Hata durumunda varsayılan metin döndür
        return {
            "analysis_title": "Piyasa Analizi",
            "tweet_text": "🚨 Günün hisse kazançları piyasada fırtına estirdi! Bu hareketlilik nereye kadar sürer dersin? 🤔",
            "hashtags": ["#Borsa", "#BIST", "#HisseSenetleri", "#Yatırım"]
        }

# --- IMAGE RENDERING (PILLOW) ---

def load_font(size: int, bold: bool = False):
    """Loads a suitable system font."""
    font_name = "-Bold" if bold else ""
    candidates = [
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{font_name}.ttf",
        f"/usr/share/fonts/truetype/liberation/LiberationSans{font_name}.ttf",
        # Fallbacks
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
    return ImageFont.load_default()

def render_table(draw: ImageDraw.ImageDraw, data: List[STOCK_MODEL], start_y: int, table_title: str, sort_key: str, limit: int, col_order: List[str], header_map: Dict[str, str], title_font: ImageFont.ImageFont, header_font: ImageFont.ImageFont, data_font: ImageFont.ImageFont) -> int:
    """Renders a single stock table and returns the next starting Y coordinate."""
    W = CANVAS_W
    INNER_W = W - 2 * MARGIN_X
    
    # Sort data by the lead period (e.g., 'pct_1d')
    sorted_data = sorted(data, key=lambda x: x.get(sort_key, 0.0), reverse=True)[:limit]
    
    # Define Column Widths (Adjusted for 6 columns)
    COL_MAP = {
        'Hisse': 0.15 * INNER_W, # Ticker
        'P1': 0.17 * INNER_W,    # Lead Period
        'P2': 0.17 * INNER_W,    # 2nd Period
        'P3': 0.17 * INNER_W,    # 3rd Period
        'P4': 0.17 * INNER_W,    # 4th Period
        'P5': 0.17 * INNER_W     # 5th Period
    }
    
    # 1. Table Title
    draw.text((MARGIN_X, start_y), table_title, fill=(50, 50, 50), font=title_font)
    current_y = start_y + TABLE_TITLE_H + 10
    
    # 2. Headers
    x_pos = MARGIN_X
    for col_key, col_label in zip(['Hisse'] + [f'P{i}' for i in range(1, 6)], col_order):
        width = COL_MAP[col_key]
        draw.text((x_pos + width - 5, current_y + 5), header_map.get(col_label, col_label), fill=(0, 0, 0), font=header_font, anchor="rt")
        x_pos += width
    
    current_y += HEADER_H - 40
    
    # Header/Data Separator
    draw.line([MARGIN_X, current_y, W - MARGIN_X, current_y], fill=(180, 180, 180), width=1)
    current_y += 5
    
    # 3. Data Rows
    for i, stock in enumerate(sorted_data):
        x_pos = MARGIN_X
        row_y = current_y + i * ROW_H + 5
        
        # Ticker (Left Align)
        ticker_width = COL_MAP['Hisse']
        ticker_text = stock['ticker']
        draw.text((x_pos, row_y), ticker_text, fill=(20, 20, 20), font=data_font)
        x_pos += ticker_width
        
        # Percentage Values (Right Align)
        for col_key, data_key in zip([f'P{i}' for i in range(1, 6)], col_order[1:]):
            width = COL_MAP[col_key]
            value = stock.get(data_key)
            
            if value is not None:
                pct_str = float_to_pct_str(value)
                color = (0, 128, 0) if value >= 0 else (204, 0, 0)
                draw.text((x_pos + width - 5, row_y), pct_str, fill=color, font=data_font, anchor="rt")
            
            x_pos += width
        
        # Row Separator
        if i < len(sorted_data) - 1:
            draw.line([MARGIN_X, row_y + ROW_H - 5, W - MARGIN_X, row_y + ROW_H - 5], fill=(240, 240, 240), width=1)

    return current_y + len(sorted_data) * ROW_H + 20

def render_image(title: str, stock_data: List[STOCK_MODEL], limit: int) -> bytes:
    """Renders the final 1080x1080 image with three tables."""
    W, H = CANVAS_W, CANVAS_H
    BG_COLOR = (248, 248, 252)
    FRAME_COLOR = (0, 102, 204) # Mavi
    
    img = Image.new("RGB", (W, H), color=BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Fonts
    header_font_large = load_font(48, bold=True)
    table_title_font = load_font(30, bold=True)
    table_header_font = load_font(20, bold=True)
    table_data_font = load_font(24)
    foot_font = load_font(20)

    # 1. Main Header
    header_text = f"🔍 DUR BİR BAKAYIM — BIST Analizi ({OWNER_HANDLE})"
    draw.text((W // 2, MARGIN_Y), header_text, fill=(20, 30, 40), font=header_font_large, anchor="mm")
    draw.line([MARGIN_X, MARGIN_Y + HEADER_H - 40, W - MARGIN_X, MARGIN_Y + HEADER_H - 40], fill=FRAME_COLOR, width=2)
    
    current_y = TABLE_INNER_Y

    # Column Mapping for Headers
    HEADER_MAP = {
        'pct_1d': 'Günlük %',
        'pct_1m': 'Aylık %',
        'pct_3m': '3 Aylık %',
        'pct_6m': '6 Aylık %',
        'pct_1y': 'Yıllık %',
    }

    # 2. Table 1: Günün Kazandıranları (Sort by 1D)
    col_order_1d = ['ticker', 'pct_1d', 'pct_1m', 'pct_3m', 'pct_6m', 'pct_1y']
    current_y = render_table(draw, stock_data, current_y, "🏆 Günün Kazandıranları", 'pct_1d', limit, col_order_1d, HEADER_MAP, table_title_font, table_header_font, table_data_font)

    current_y += TABLE_GAP_Y

    # 3. Table 2: Ayın Kazandıranları (Sort by 1M)
    col_order_1m = ['ticker', 'pct_1m', 'pct_1d', 'pct_3m', 'pct_6m', 'pct_1y']
    current_y = render_table(draw, stock_data, current_y, "📈 Ayın Kazandıranları", 'pct_1m', limit, col_order_1m, HEADER_MAP, table_title_font, table_header_font, table_data_font)

    current_y += TABLE_GAP_Y

    # 4. Table 3: Yılın Kazandıranları (Sort by 1Y)
    col_order_1y = ['ticker', 'pct_1y', 'pct_1d', 'pct_1m', 'pct_3m', 'pct_6m']
    current_y = render_table(draw, stock_data, current_y, "👑 Yılın Kazandıranları", 'pct_1y', limit, col_order_1y, HEADER_MAP, table_title_font, table_header_font, table_data_font)
    
    # 5. Footer
    now = now_tr()
    date_line = f"{now.day} {tr_month_name(now.month)} {now.year}, {tr_weekday_name(now.weekday())}"
    footer_text = f"Veri Güncel: {date_line}"
    draw.text((W // 2, H - 40), footer_text, fill=(80, 90, 100), font=foot_font, anchor="ms")

    # Outer Frame
    draw.rectangle([20, 20, W - 20, H - 20], outline=FRAME_COLOR, width=5)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()

# --- TWEET COMPOSITION ---

def compose_tweet(gemini_data: Dict[str, Any], stock_data: List[STOCK_MODEL]) -> str:
    """Composes the final tweet with pruning to stay within 280 characters."""
    analysis_title = gemini_data["analysis_title"]
    tweet_text = gemini_data["tweet_text"]
    gemini_hashtags = [f"#{tag.strip('#')}" for tag in gemini_data["hashtags"]]
    
    # Tickers: Günün en çok kazananlarından 6 tanesini al
    top_tickers = sorted(stock_data, key=lambda x: x['pct_1d'], reverse=True)[:6]
    ticker_hashtags = [f"#{s['ticker']}" for s in top_tickers]
    
    # 1. Başlangıç Kompozisyonu
    initial_template = "🚨 {title}\n\n{text}\n\n{tags_line}\n{ticker_line}\n{handle}"
    
    # Birleşik hashtag listesi (önce Gemini'den gelenler, sonra Ticker'lar)
    all_hashtags = list(set(gemini_hashtags + ticker_hashtags)) 
    random.shuffle(all_hashtags) # Rastgelelik katmak için

    def attempt_composition(text, tags, tickers):
        tags_line = " ".join(tags)
        ticker_line = " ".join(tickers)
        
        # Başlık ve metin satırları
        composed_text = initial_template.format(
            title=analysis_title,
            text=text,
            tags_line=tags_line,
            ticker_line=ticker_line,
            handle=OWNER_HANDLE
        )
        # Birden fazla boş satırı tek boş satıra indir (Twitter/X bunu otomatik yapar, ancak karakter sayımı için manuel yapalım)
        composed_text = "\n".join([line for line in composed_text.split('\n') if line.strip() or line == ''])
        return composed_text.strip()

    # Pruning Stages
    # Stage 0: Full attempt
    final_text = attempt_composition(tweet_text, all_hashtags, ticker_hashtags)
    if len(final_text) <= 280:
        return final_text
    
    # Stage 1: Ticker sayısını 3'e düşür
    ticker_hashtags_pruned = ticker_hashtags[:3]
    final_text = attempt_composition(tweet_text, all_hashtags, ticker_hashtags_pruned)
    if len(final_text) <= 280:
        return final_text

    # Stage 2: Gemini hashtag sayısını 3'e düşür ve Ticker'ları 2'ye indir
    gemini_hashtags_pruned = gemini_hashtags[:3]
    ticker_hashtags_pruned = ticker_hashtags[:2]
    all_hashtags_pruned = list(set(gemini_hashtags_pruned + ticker_hashtags_pruned))
    final_text = attempt_composition(tweet_text, all_hashtags_pruned, []) # Ticker'ları ayrı satır yerine ana hashtag'e dahil et
    if len(final_text) <= 280:
        return final_text
    
    # Stage 3: Tweet metnini kısalt (ilk cümleyi veya 120 karakteri al)
    short_text = tweet_text.split('?')[0].strip() # Soruya kadar kısalt
    if len(short_text) > 120:
        short_text = tweet_text[:117] + "..."
    
    final_text = attempt_composition(short_text, all_hashtags_pruned, [])
    if len(final_text) <= 280:
        return final_text
        
    # Stage 4: Son çare, 277 karaktere zorla
    final_text = final_text[:277] + "..."
    print(f"!!! KRİTİK KISALTMA: Metin 277 karaktere indirildi. Son Uzunluk: {len(final_text)}")
    return final_text
    
# --- X/TWITTER CLIENT ---

def upload_media(oauth: OAuth1Session, image_bytes: bytes) -> str:
    """Uploads the image and returns media ID."""
    files = {"media": ("bist_analysis.png", image_bytes, "image/png")}
    resp = oauth.post(MEDIA_UPLOAD_ENDPOINT, files=files)
    resp.raise_for_status() # HTTP 4xx/5xx hatalarında hata fırlat
    media_id = resp.json().get("media_id_string")
    if not media_id:
        raise ValueError("X API Hatası: media_id alınamadı.")
    return media_id

def post_tweet(oauth: OAuth1Session, text: str, media_id: str):
    """Posts the tweet with media."""
    payload = {"text": text, "media": {"media_ids": [media_id]}}
    resp = oauth.post(POST_TWEET_ENDPOINT, json=payload)
    
    if resp.status_code == 403:
        raise PermissionError("X API Hatası: 403 Forbidden. Lütfen Read and Write izinlerinizi kontrol edin.")
    
    resp.raise_for_status()
    tweet_id = (resp.json() or {}).get("data", {}).get("id")
    print(f"✅ Başarılı Tweet ID: {tweet_id}")

# --- CLI & MAIN ORCHESTRATION ---

def parse_args() -> argparse.Namespace:
    """Parses command line arguments."""
    parser = argparse.ArgumentParser(description="BIST Multi-Period Kazanç Analiz Botu.")
    parser.add_argument('--post', action='store_true', default=True, help='Görseli X/Twitter hesabına postala (Varsayılan).')
    parser.add_argument('--dry-run', action='store_true', help='Postalamadan sadece veri çek, görsel oluştur ve konsola yazdır.')
    parser.add_argument('--limit', type=int, default=6, help='Her tabloda gösterilecek hisse sayısı (default 6).')
    parser.add_argument('--out', type=str, default="bist_output.png", help='--dry-run modunda görselin kaydedileceği yol.')
    
    # --post ve --dry-run aynı anda verilirse --dry-run kazanır
    args = parser.parse_args()
    if args.dry_run:
        args.post = False
    
    return args

def main():
    """Ana orkestrasyon fonksiyonu."""
    args = parse_args()
    run_dt = now_tr()
    print(f"\n--- BIST Analiz Botu Başlatıldı ({run_dt.strftime('%d.%m.%Y %H:%M:%S')}) ---")

    try:
        # 1. Veri Çekme, Uzlaştırma ve Cache Yönetimi
        stock_data = get_final_data(run_dt)
        if not stock_data:
            raise RuntimeError("Veri çekilemedi ve Cache boş. İşlem durduruluyor.")
        
        # 2. Gemini Analizi
        gemini_data = generate_analysis(stock_data)

        # 3. Görsel Oluşturma
        image_bytes = render_image(gemini_data["analysis_title"], stock_data, args.limit)
        print("✅ Görsel başarıyla oluşturuldu.")

        # 4. Tweet Kompozisyonu
        final_tweet_text = compose_tweet(gemini_data, stock_data)
        print(f"✅ Tweet hazırlandı. Uzunluk: {len(final_tweet_text)}")

        if args.dry_run:
            # DRY-RUN modu
            print("\n--- DRY RUN SONUÇLARI ---")
            print(f"Kaydedilen Görsel: {args.out}")
            with open(args.out, 'wb') as f:
                f.write(image_bytes)
            print("\n--- TWEET METNİ ---\n" + final_tweet_text)
            print("-----------------------\n")
        
        elif args.post:
            # POST modu
            print("\n--- X POSTALA MODU ---")
            oauth = oauth1_session_from_env()
            media_id = upload_media(oauth, image_bytes)
            print(f"✅ Medya yüklendi (ID: {media_id}). Tweet gönderiliyor...")
            post_tweet(oauth, final_tweet_text, media_id)
            print("🎉 İşlem başarıyla tamamlandı!")
            
    except Exception as e:
        print(f"\n!!! KRİTİK HATA - İşlem Başarısız: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
