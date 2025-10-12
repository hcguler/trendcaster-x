# analyze_stocks.py

import yfinance as yf
import pandas as pd
import json
import os
from datetime import datetime, timedelta

# --- BURAYI DÜZENLEYİN ---
# Analiz edilecek BIST hisselerinin listesi. 
# Örnek olarak birkaç popüler hisse eklendi.
# Yahoo Finance formatına uygun olarak sonuna ".IS" eklenmelidir.
# Tam listeyi bir kaynaktan alıp buraya ekleyebilirsiniz.
BIST_TICKERS = [
    "AKBNK.IS", "ARCLK.IS", "ASELS.IS", "BIMAS.IS", "EKGYO.IS", 
    "EREGL.IS", "FROTO.IS", "GARAN.IS", "GUBRF.IS", "HEKTS.IS",
    "KCHOL.IS", "KOZAL.IS", "KRDMD.IS", "PETKM.IS", "PGSUS.IS",
    "SAHOL.IS", "SASA.IS", "SISE.IS", "TCELL.IS", "THYAO.IS",
    "TOASO.IS", "TTKOM.IS", "TUPRS.IS", "ULKER.IS", "VESTL.IS",
    "YKBNK.IS"
]

def get_closest_price(data_frame, date):
    """Belirtilen tarihe en yakın geçmişteki kapanış fiyatını bulur."""
    try:
        # Tarihe göre doğrudan arama yap
        return data_frame.loc[date.strftime('%Y-%m-%d')]['Close']
    except KeyError:
        # Eğer tam o gün veri yoksa (hafta sonu, tatil vb.), bir önceki mevcut güne bak
        past_dates = data_frame.loc[:date.strftime('%Y-%m-%d')]
        if not past_dates.empty:
            return past_dates.iloc[-1]['Close']
        return None

def analyze_stocks():
    """Hisseleri analiz eder ve verileri bir sözlük olarak döndürür."""
    today = datetime.now()
    analysis_results = {}

    # Geçmiş verileri çekmek için başlangıç tarihini belirle (1 yıl yeterli)
    start_date = today - timedelta(days=366)

    for ticker in BIST_TICKERS:
        print(f"'{ticker}' için veriler işleniyor...")
        try:
            stock = yf.Ticker(ticker)

            # Son 1 yıllık tarihsel veriyi al
            hist_data = stock.history(start=start_date.strftime('%Y-%m-%d'), end=today.strftime('%Y-%m-%d'))
            
            if hist_data.empty:
                print(f"'{ticker}' için geçmiş veri bulunamadı.")
                continue

            # Güncel günün verisini al (açılış ve kapanış)
            # Piyasa kapandıktan sonra çalışacağı için son günün verisi mevcut olacaktır.
            today_data = hist_data.iloc[-1]
            open_price_today = today_data['Open']
            close_price_today = today_data['Close']
            
            # Geçmiş tarihleri hesapla
            date_30_days_ago = today - timedelta(days=30)
            date_90_days_ago = today - timedelta(days=90)
            date_180_days_ago = today - timedelta(days=180)
            date_360_days_ago = today - timedelta(days=360)

            # Geçmiş kapanış fiyatlarını al
            close_30_days_ago = get_closest_price(hist_data, date_30_days_ago)
            close_90_days_ago = get_closest_price(hist_data, date_90_days_ago)
            close_180_days_ago = get_closest_price(hist_data, date_180_days_ago)
            close_360_days_ago = get_closest_price(hist_data, date_360_days_ago)

            # Kazandırma oranlarını hesapla
            # Hata vermemesi için değerlerin None olup olmadığını kontrol et
            daily_return = ((close_price_today - open_price_today) / open_price_today) * 100 if open_price_today else 0
            monthly_return = ((close_price_today - close_30_days_ago) / close_30_days_ago) * 100 if close_30_days_ago else None
            quarterly_return = ((close_price_today - close_90_days_ago) / close_90_days_ago) * 100 if close_90_days_ago else None
            half_yearly_return = ((close_price_today - close_180_days_ago) / close_180_days_ago) * 100 if close_180_days_ago else None
            yearly_return = ((close_price_today - close_360_days_ago) / close_360_days_ago) * 100 if close_360_days_ago else None

            analysis_results[ticker] = {
                "bugun_acilis": open_price_today,
                "bugun_kapanis": close_price_today,
                "gecmis_kapanis_fiyatlari": {
                    "30_gun_once": close_30_days_ago,
                    "90_gun_once": close_90_days_ago,
                    "180_gun_once": close_180_days_ago,
                    "360_gun_once": close_360_days_ago,
                },
                "kazandirma_oranlari_yuzde": {
                    "gunluk": f"{daily_return:.2f}%",
                    "aylik_30_gun": f"{monthly_return:.2f}%" if monthly_return is not None else "N/A",
                    "3_aylik_90_gun": f"{quarterly_return:.2f}%" if quarterly_return is not None else "N/A",
                    "6_aylik_180_gun": f"{half_yearly_return:.2f}%" if half_yearly_return is not None else "N/A",
                    "12_aylik_360_gun": f"{yearly_return:.2f}%" if yearly_return is not None else "N/A",
                }
            }

        except Exception as e:
            print(f"'{ticker}' işlenirken bir hata oluştu: {e}")
            analysis_results[ticker] = {"hata": str(e)}

    return analysis_results

def save_to_json(data):
    """Veriyi JSON dosyasına kaydeder."""
    # Çıktı dizininin var olduğundan emin ol
    output_dir = 'out'
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Dosya adını tarihle birlikte oluştur
    today_str = datetime.now().strftime('%Y-%m-%d')
    file_path = os.path.join(output_dir, f'bist_analiz_{today_str}.json')

    # JSON dosyasına yaz
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    print(f"Analiz sonuçları başarıyla '{file_path}' dosyasına kaydedildi.")


if __name__ == "__main__":
    results = analyze_stocks()
    if results:
        save_to_json(results)