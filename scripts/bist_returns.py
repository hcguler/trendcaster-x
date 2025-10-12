# -*- coding: utf-8 -*-
"""
BIST Returns Collector
- Kaynaklar:
  * Hisse listesi: İş Yatırım herkese açık uç nokta
  * Fiyat geçmişi: Yahoo Finance (yfinance) .IS uzantısı
- Çalışma Zamanı: Her gün 18:30 (Europe/Istanbul) -> GH Actions cron: 15:30 UTC
- Çıktı: out/bist_returns_YYYY-MM-DD.json
- Tanımlar:
  * "pre_open_price"  : Bir önceki işlem gününün "Close" fiyatı
  * "post_close_price": Bugünün (son işlem gününün) "Close" fiyatı
  * Getiri pencereleri: 1g (daily), 30g, 90g, 180g, 360g  — hepsi kapanışa göre

Not: Ücretsiz servislerde gerçek “pre-market” alanı bulunmadığından, piyasa açılmadan önceki referans olarak
önceki gün kapanışı alınmıştır.
"""

import os
import json
import time
import math
import pytz
import datetime as dt
from typing import List, Dict, Any, Optional

import requests
import pandas as pd
import numpy as np
import yfinance as yf

IST_TZ = pytz.timezone("Europe/Istanbul")
OUTPUT_DIR = os.path.join("out")
SESSION = requests.Session()

ISYATIRIM_ALLSTOCKS_URL = "https://www.isyatirim.com.tr/_layouts/15/IsYatirim.Website/Common/Handlers/StockHandler.ashx?action=allstocks"

# -------- Helpers -------- #

def log(msg: str) -> None:
    print(f"[bist] {msg}", flush=True)

def today_ist_date() -> dt.date:
    return dt.datetime.now(IST_TZ).date()

def ensure_out_dir() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)

def fetch_all_bist_tickers(timeout: int = 20) -> List[Dict[str, Any]]:
    """
    İş Yatırım uç noktasından tüm hisseleri alır.
    Dönüş ör.: [{"Kod":"AKBNK","HisseAdi":"AKBANK T.A.S.","Endeks":"BIST 30",...}, ...]
    """
    log("Hisse listesi alınıyor (İş Yatırım)...")
    r = SESSION.get(ISYATIRIM_ALLSTOCKS_URL, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    # Bazı alan adları: Kod, HisseAdi, Endeks
    # Sadece normal hisse kodlarını filtrele (warrant, BYF, vb. ayıklama için basit regex)
    df = pd.DataFrame(data)
    df = df.rename(columns={"Kod": "Symbol", "HisseAdi": "Name"})
    # Harf-sayı ve maksimum 5-6 karakterli normal BIST sembolleri kabaca:
    df = df[df["Symbol"].str.fullmatch(r"[A-Z]{2,6}")]
    # Yinelenenleri temizle
    df = df.drop_duplicates(subset=["Symbol"])
    out = df[["Symbol", "Name"]].to_dict(orient="records")
    log(f"Toplam {len(out)} hisse bulundu.")
    return out

def chunked(seq: List[str], size: int) -> List[List[str]]:
    return [seq[i:i+size] for i in range(0, len(seq), size)]

def nearest_on_or_before(idx: pd.DatetimeIndex, target_date: dt.date) -> Optional[pd.Timestamp]:
    """
    İstenen tarihe en yakın, aynı gün veya öncesindeki son işlem gününü döndürür.
    """
    # idx tz-aware olabilir; normalize edip date kıyaslayalım
    s = pd.Series(idx)
    s_dates = s.dt.tz_localize(None).dt.date if s.dt.tz is not None else s.dt.date
    # Boolean mask: <= target_date
    mask = s_dates <= target_date
    if not mask.any():
        return None
    # En son (max) tarihi seç
    pos = mask[mask].index.max()
    return s.iloc[pos]

def compute_return(cur_close: float, past_close: Optional[float]) -> Optional[float]:
    if past_close is None or past_close == 0 or np.isnan(past_close):
        return None
    return float(cur_close / past_close - 1.0)

# -------- Core -------- #

def collect_returns() -> Dict[str, Any]:
    ist_today = today_ist_date()
    ensure_out_dir()

    # 1) Hisse listesi
    listings = fetch_all_bist_tickers()
    symbols = [x["Symbol"] for x in listings]
    symbol_to_name = {x["Symbol"]: x["Name"] for x in listings}

    # 2) Yahoo Finance sembollerini hazırla (.IS)
    y_symbols = [f"{s}.IS" for s in symbols]

    # 3) Tarih aralığı: 400 gün geriye gidiyoruz (360 + güven payı)
    start_date = ist_today - dt.timedelta(days=400)
    end_date = ist_today + dt.timedelta(days=1)  # güvenli uç

    log(f"Fiyat geçmişi alınıyor (yfinance)... {len(y_symbols)} sembol")
    all_data: Dict[str, pd.DataFrame] = {}

    # YF çok sayıda sembolde hata/limit yaratmaması için küçük partilere bölelim
    for batch in chunked(y_symbols, 50):
        try:
            df = yf.download(
                tickers=" ".join(batch),
                start=start_date.strftime("%Y-%m-%d"),
                end=end_date.strftime("%Y-%m-%d"),
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                threads=True,
                progress=False,
            )
            # yfinance, tek sembolde sütunlar düz; çoklu sembolde MultiIndex gelir.
            if isinstance(df.columns, pd.MultiIndex):
                for t in batch:
                    if t in df.columns.levels[0]:
                        sub = df[t].copy()
                        # Tüm sütun isimlerini tek seviyeye indir
                        sub.columns = [c.capitalize() for c in sub.columns]
                        all_data[t] = sub
            else:
                # Tek sembol durumu (nadiren buraya düşeriz)
                t = batch[0]
                sub = df.copy()
                sub.columns = [c.capitalize() for c in sub.columns]
                all_data[t] = sub

            time.sleep(0.7)  # nazik ol
        except Exception as e:
            log(f"UYARI: yfinance batch hatası: {e}")

    log(f"{len(all_data)} sembol için veri alındı.")

    # 4) Getiri hesapları
    targets = {
        "d30": 30,
        "d90": 90,
        "d180": 180,
        "d360": 360,
    }

    results = []
    for s in symbols:
        ys = f"{s}.IS"
        name = symbol_to_name.get(s, "")
        df = all_data.get(ys)

        # Veri yoksa atla
        if df is None or df.empty or "Close" not in df.columns:
            continue

        # Mevcut/son işlem günü (bugün veya önceki en yakın)
        last_ts = nearest_on_or_before(df.index, ist_today)
        if last_ts is None:
            continue

        # Önceki işlem günü
        prev_idx = df.index.get_loc(last_ts)
        prev_ts = df.index[prev_idx - 1] if prev_idx - 1 >= 0 else None

        post_close_price = float(df.loc[last_ts, "Close"]) if not pd.isna(df.loc[last_ts, "Close"]) else None
        pre_open_price = float(df.loc[prev_ts, "Close"]) if (prev_ts is not None and not pd.isna(df.loc[prev_ts, "Close"])) else None

        # Hedef geçmiş kapanışlar
        past_prices: Dict[str, Optional[float]] = {}
        for key, days in targets.items():
            target_date = ist_today - dt.timedelta(days=days)
            target_ts = nearest_on_or_before(df.index, target_date)
            if target_ts is not None and not pd.isna(df.loc[target_ts, "Close"]):
                past_prices[key] = float(df.loc[target_ts, "Close"])
            else:
                past_prices[key] = None

        # Getiriler
        daily_ret = compute_return(post_close_price, pre_open_price) if (post_close_price and pre_open_price) else None
        d30_ret  = compute_return(post_close_price, past_prices["d30"])
        d90_ret  = compute_return(post_close_price, past_prices["d90"])
        d180_ret = compute_return(post_close_price, past_prices["d180"])
        d360_ret = compute_return(post_close_price, past_prices["d360"])

        row = {
            "symbol": s,
            "name": name,
            "pre_open_price": pre_open_price,      # önceki gün kapanışı
            "post_close_price": post_close_price,  # bugünkü kapanış
            "returns": {
                "daily": daily_ret,
                "30d": d30_ret,
                "90d": d90_ret,
                "180d": d180_ret,
                "360d": d360_ret,
            },
            "past_closes": {
                "d30_close": past_prices["d30"],
                "d90_close": past_prices["d90"],
                "d180_close": past_prices["d180"],
                "d360_close": past_prices["d360"],
            },
        }
        results.append(row)

    payload = {
        "as_of_date": ist_today.strftime("%Y-%m-%d"),
        "timezone": "Europe/Istanbul",
        "universe": "BIST All Stocks (via İş Yatırım list + Yahoo Finance .IS)",
        "count": len(results),
        "data": results,
        "notes": [
            "pre_open_price=previous trading day's close",
            "post_close_price=most recent trading day's close",
            "returns computed as (post_close / reference_close - 1)",
        ],
        "credits": {
            "symbols": "İş Yatırım public endpoint",
            "prices": "Yahoo Finance via yfinance",
        }
    }

    out_path = os.path.join(OUTPUT_DIR, f"bist_returns_{ist_today.strftime('%Y-%m-%d')}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    log(f"Yazıldı: {out_path}")
    return payload


if __name__ == "__main__":
    collect_returns()
