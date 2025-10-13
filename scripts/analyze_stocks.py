# analyze_stocks.py

import yfinance as yf
import pandas as pd
import json
import os
import re
from datetime import datetime, timedelta

# =========================
# TUM BIST LISTESI — DINAMIK
# =========================

FALLBACK_TICKERS = [
    # Bankalar
    "AKBNK.IS", "GARAN.IS", "YKBNK.IS", "ISCTR.IS", "HALKB.IS", 
    "VAKBN.IS", "TSKB.IS", "SKBNK.IS", "ALBRK.IS", "KLNMA.IS",

    # Holdingler
    "KCHOL.IS", "SAHOL.IS", "BIMAS.IS", "ENKAI.IS", "TAVHL.IS",
    "ALARK.IS", "DOAS.IS", "AGHOL.IS", "ULKER.IS", "TKFEN.IS",
    "MGROS.IS", "GOZDE.IS", "AEFES.IS", "GLYHO.IS", "IHLAS.IS",

    # Sanayi ve Üretim
    "EREGL.IS", "TUPRS.IS", "FROTO.IS", "TOASO.IS", "ARCLK.IS",
    "VESTL.IS", "SASA.IS", "HEKTS.IS", "PETKM.IS", "SISE.IS",
    "KRDMD.IS", "GUBRF.IS", "KORDS.IS", "TTRAK.IS", "OTKAR.IS",
    "AYGAZ.IS", "KMPUR.IS", "DEVA.IS", "ECILC.IS", "EGEEN.IS",
    "JANTS.IS", "KARTN.IS", "KONTR.IS", "SMRTG.IS", "ARZUM.IS", # ARZUM eklendi
    "ARTMS.IS", # ARTMS eklendi
    
    # Ulaştırma ve Lojistik
    "THYAO.IS", "PGSUS.IS", "MPARK.IS", "ULUSN.IS",

    # Teknoloji ve İletişim
    "TCELL.IS", "TTKOM.IS", "ASELS.IS", "LOGO.IS", "KAREL.IS",
    "ARDYZ.IS", "INDES.IS", 
    
    # Enerji
    "AKSEN.IS", "AYDEM.IS", "ZOREN.IS", "ENERY.IS", "ODAS.IS",
    "GWIND.IS", "BIOEN.IS", "AYEN.IS", "AKSUE.IS",
    
    # Sağlık
    "ONCSM.IS", 
    
    # Gayrimenkul Yatırım Ortaklıkları (GYO)
    "EKGYO.IS", "ISGYO.IS", "TRGYO.IS", "AKFGY.IS", "HLGYO.IS",
    
    # Çimento ve Toprak Ürünleri
    "AKCNS.IS", "CIMSA.IS", "OYAKC.IS", "BUCIM.IS", "AFYON.IS",
    "YBTAS.IS", 
    
    # Sigorta
    "AKGRT.IS", "ANHYT.IS", "AGESA.IS", "TURSG.IS",
    
    # Aracı Kurumlar ve Yatırım
    "TERA.IS", 
    
    # Diğer (Maden, Perakende vb.)
    "KOZAL.IS", "IPEKE.IS", "KOZAA.IS", "DOHOL.IS", "SOKM.IS"
]

def _ensure_is_suffix(sym: str) -> str:
    if not sym:
        return sym
    s = str(sym).strip().upper()
    return s if s.endswith(".IS") else (s + ".IS")

def _is_equity_symbol(sym: str) -> bool:
    # 3–5 harf + ".IS" — varant/kupon benzerlerini ele
    return bool(re.fullmatch(r"[A-Z]{3,5}\.IS", sym or ""))

def _load_from_file(path: str = "data/bist_all_tickers.txt") -> list[str]:
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            code = line.strip()
            if not code:
                continue
            sym = _ensure_is_suffix(code)
            if _is_equity_symbol(sym):
                out.append(sym)
    return sorted(set(out))

def _load_from_url(url: str) -> list[str]:
    """
    BIST sembollerini bir HTML tablo sayfasından çekmeye çalışır.
    Ortam değişkeni: BIST_TICKERS_URL
    - Sayfadaki tüm tabloları tarar ve 'Ticker', 'Kod', 'Symbol' benzeri sütun adlarını dener.
    - Başarısız olursa boş liste döner (kırılmaz).
    """
    try:
        dfs = pd.read_html(url)
    except Exception:
        return []
    candidates = []
    possible_cols = {"ticker", "kod", "symbol", "code", "sembol"}
    for df in dfs:
        cols_lower = [str(c).strip().lower() for c in df.columns]
        for i, c in enumerate(cols_lower):
            if c in possible_cols:
                for val in df.iloc[:, i].dropna().astype(str).tolist():
                    candidates.append(val.strip())
    # normalize + filtre
    norm = [_ensure_is_suffix(x) for x in candidates]
    norm = [x for x in norm if _is_equity_symbol(x)]
    return sorted(set(norm))

def load_all_bist_tickers() -> list[str]:
    """
    Öncelik:
    1) data/bist_all_tickers.txt
    2) BIST_TICKERS_URL ortam değişkeni (pandas.read_html ile)
    3) FALLBACK_TICKERS
    """
    from_file = _load_from_file()
    if from_file:
        return from_file

    url = os.environ.get("BIST_TICKERS_URL")
    if url:
        from_url = _load_from_url(url)
        if from_url:
            return from_url

    return sorted(set(FALLBACK_TICKERS))

# --- BURAYI DÜZENLEDİK: Artik dinamik ---
BIST_TICKERS = load_all_bist_tickers()

# =========================
# YARDIMCI FONKSIYONLAR
# =========================

def get_closest_price(data_frame: pd.DataFrame, date: datetime):
    """Belirtilen tarihe en yakın geçmişteki kapanış fiyatını bulur."""
    try:
        return data_frame.loc[date.strftime('%Y-%m-%d')]['Close']
    except KeyError:
        past = data_frame.loc[:date.strftime('%Y-%m-%d')]
        if not past.empty:
            return past.iloc[-1]['Close']
        return None

def _pct_change(cur: float | None, past: float | None) -> float:
    """
    Yüzde değişim: ((cur - past) / past) * 100
    - past None/0 ise 0 döndür.
    - cur None ise 0 döndür.
    """
    try:
        if cur is None or past in (None, 0):
            return 0.0
        return float((cur - past) / past) * 100.0
    except Exception:
        return 0.0

def _fmt_pct(x: float | None) -> str:
    try:
        return f"{float(x or 0):.2f}%"
    except Exception:
        return "0.00%"

# =========================
# ANALIZ
# =========================

def analyze_stocks():
    """Hisseleri analiz eder ve verileri bir dict olarak döndürür."""
    today = datetime.now()
    analysis_results = {}

    # Son 1 yıldan biraz fazla tampon (iş günü/kapalı gün kaymaları için)
    start_date = today - timedelta(days=366)

    for ticker in BIST_TICKERS:
        print(f"'{ticker}' için veriler işleniyor...")
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(
                start=start_date.strftime('%Y-%m-%d'),
                end=today.strftime('%Y-%m-%d')
            )

            if hist.empty:
                print(f"'{ticker}' için geçmiş veri bulunamadı.")
                analysis_results[ticker] = {"hata": "no_history"}
                continue

            # Son kapanış ve bir önceki kapanış
            # Not: yfinance günlük seride son satır genellikle en son tamamlanmış seanstır.
            close_today = float(hist['Close'].iloc[-1] or 0)

            prev_close = None
            if len(hist) >= 2:
                prev_close = float(hist['Close'].iloc[-2] or 0)

            # Geçmiş referans tarihleri
            d30  = today - timedelta(days=30)
            d90  = today - timedelta(days=90)
            d180 = today - timedelta(days=180)
            d360 = today - timedelta(days=360)

            c30  = get_closest_price(hist, d30)
            c90  = get_closest_price(hist, d90)
            c180 = get_closest_price(hist, d180)
            c360 = get_closest_price(hist, d360)

            # Yüzdeler — verisi yoksa 0
            # GÜNCELLENDİ: Günlük getiri = (bugünkü Kapanış - dünkü Kapanış) / dünkü Kapanış
            daily_ret   = _pct_change(close_today, prev_close)
            monthly_ret = _pct_change(close_today, c30)
            q90_ret     = _pct_change(close_today, c90)
            h180_ret    = _pct_change(close_today, c180)
            y360_ret    = _pct_change(close_today, c360)

            analysis_results[ticker] = {
                "dun_kapanis": float(prev_close or 0),
                "bugun_kapanis": close_today,
                "gecmis_kapanis_fiyatlari": {
                    "30_gun_once": float(c30 or 0),
                    "90_gun_once": float(c90 or 0),
                    "180_gun_once": float(c180 or 0),
                    "360_gun_once": float(c360 or 0),
                },
                "kazandirma_oranlari_yuzde": {
                    "gunluk": _fmt_pct(daily_ret),           # DÜZENLENDİ
                    "aylik_30_gun": _fmt_pct(monthly_ret),
                    "3_aylik_90_gun": _fmt_pct(q90_ret),
                    "6_aylik_180_gun": _fmt_pct(h180_ret),
                    "12_aylik_360_gun": _fmt_pct(y360_ret),
                }
            }

        except Exception as e:
            print(f"'{ticker}' işlenirken bir hata oluştu: {e}")
            analysis_results[ticker] = {"hata": str(e)}

    return analysis_results

# =========================
# KAYIT
# =========================

def save_to_json(data):
    """Veriyi JSON dosyasına kaydeder."""
    output_dir = 'out'
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    today_str = datetime.now().strftime('%Y-%m-%d')
    file_path = os.path.join(output_dir, f'bist_analiz_{today_str}.json')

    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    print(f"Analiz sonuçları başarıyla '{file_path}' dosyasına kaydedildi.")

# =========================
# MAIN
# =========================

if __name__ == "__main__":
    results = analyze_stocks()
    if results:
        save_to_json(results)
