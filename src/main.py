import os
import sys
import io
import json
import random
from datetime import datetime, timezone, timedelta
from typing import List, Tuple
from requests_oauthlib import OAuth1Session
from PIL import Image, ImageDraw, ImageFont
from pytrends.request import TrendReq
from google import genai
import traceback # Hata izleme için eklendi

# -------------------- Sabitler --------------------
POST_TWEET_ENDPOINT = "https://api.twitter.com/2/tweets"
MEDIA_UPLOAD_ENDPOINT = "https://upload.twitter.com/1.1/media/upload.json"
OWNER_HANDLE = "@durbirbakiyim" # Footer'da görünsün diye kullanıcı adı
CANVAS_W, CANVAS_H = 1080, 1080 # Görsel boyutu (kare)
FACTS_START_Y = 320 # Görselin merkezine yakın başlangıç Y koordinatı

# -------------------- Türkçe Yerelleştirme --------------------
_TR_MONTHS = {
    1:"Ocak", 2:"Şubat", 3:"Mart", 4:"Nisan", 5:"Mayıs", 6:"Haziran",
    7:"Temmuz", 8:"Ağustos", 9:"Eylül", 10:"Ekim", 11:"Kasım", 12:"Aralık"
}

def tr_month_name(m: int) -> str:
    """Ay numarasını Türkçe ada çevirir."""
    return _TR_MONTHS.get(m, str(m))

# -------------------- Env / OAuth --------------------
def require_env(keys: List[str]) -> dict:
    """Gerekli ortam değişkenlerini kontrol eder ve çeker."""
    envs = {k: os.environ.get(k) for k in keys}
    missing = [k for k, v in envs.items() if not v]
    if missing:
        print(f"HATA: Eksik secret(lar): {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    return envs

def oauth1_session_from_env() -> OAuth1Session:
    """X/Twitter API için OAuth1 oturumu oluşturur."""
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

# -------------------- Trend Tespiti (Pytrends) --------------------
def get_daily_trending_topic() -> str:
    """Türkiye'nin en popüler günlük arama trendini Google Trends'ten çeker."""
    try:
        # Pytrends örneğini oluştur
        pytrends = TrendReq(hl='tr-TR', tz=180) # Türkiye (TR) ve UTC+3 (180 dakika) zaman dilimi

        # Günlük Arama Trendlerini çek (ülke kodu: TR - Türkiye)
        df = pytrends.trending_searches(pn='turkey')

        if df.empty:
            print("Pytrends: Trend verisi çekilemedi. Varsayılan metin kullanılıyor.")
            return "teknolojik yenilikler" # Varsayılan fallback

        # En üstteki (en popüler) trendi çek
        # DataFrame genellikle 'title' veya ilk sütun olarak trendleri içerir
        first_trend = df.iloc[0, 0]
        print(f"✅ Google Trend Tespiti: '{first_trend}'")
        return first_trend
    except Exception as e:
        print(f"Pytrends Hatası: {e}. Varsayılan metin kullanılıyor.")
        return "yapay zeka gelişmeleri" # Başka bir varsayılan fallback

# -------------------- İçerik Üretimi (Gemini API) --------------------

# Gemini için JSON şeması (yapılandırılmış çıktı almak için)
POST_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "analysis_title": {"type": "STRING", "description": "Analizin kısa ve merak uyandıran başlığı."},
        "tweet_text": {"type": "STRING", "description": "160 karakteri geçmeyen, analizi ve merak uyandıran soruyu içeren ana post metni."},
        "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Post ile ilgili en etkili 4 adet hashtag."},
        "key_facts": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Trendle ilgili 3 adet, her biri maksimum 50 karakter olan, ticari potansiyele odaklanan, çarpıcı ve güncel bilgi/veri içeren madde (bullet point)."}
    },
    "propertyOrdering": ["analysis_title", "tweet_text", "hashtags", "key_facts"]
}

def generate_content_with_gemini(trend_keyword: str) -> dict:
    """Gemini API'yi kullanarak post metni, hashtag'leri ve ana bilgileri oluşturur."""
    envs = require_env(["GEMINI_API_KEY"])
    API_KEY = envs["GEMINI_API_KEY"]
    
    client = genai.Client(api_key=API_KEY)

    system_prompt = (
        "Sen, 'Dur Bir Bakayım' adlı bir X (Twitter) hesabının Veri Analistisin. "
        "Görevin, sana verilen trend anahtar kelimesi hakkında **e-ticaret, dropshipping, veya teknoloji girişimciliği** perspektifinden hızlı, güncel ve **ticari değeri olan çarpıcı verilerle** bir analiz sunmaktır. "
        "Çıktı sadece JSON formatında olmalı ve şu kurallara uymalıdır: "
        "1. Analiz başlığı (analysis_title) 3-5 kelime olmalı, emoji içermemelidir. "
        "2. Post metni (tweet_text) **160 karakteri kesinlikle geçmemelidir**. 'Dur bir bakayım' formatına uygun olarak merak uyandırmalı ve sonunda mutlaka bir soru sormalıdır. "
        "3. Hashtag'ler güncel, ilgili ve Türkçe olmalıdır. "
        "4. Key_facts listesi için, trendle ilgili internetten bulduğun **en güncel, ticari potansiyeli gösteren** ve ilgi çekici 3 gelişmeyi veya veriyi, her madde **maksimum 50 karakter** olacak şekilde oluştur. Bu maddeler görselin odak noktası olacaktır."
    )

    user_query = f"Bugünün Google Trend kelimesi: '{trend_keyword}'. Bu kelimenin e-ticaret veya girişimcilik potansiyelini analiz et. X post metnini, hashtag'lerini ve görselde gösterilecek 3 ana bilgiyi oluştur."

    print("⏳ Gemini'ye içerik oluşturma isteği gönderiliyor...")

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash-preview-05-20',
            contents=user_query,
            config={
                'system_instruction': system_prompt,
                'response_mime_type': 'application/json',
                'response_schema': POST_SCHEMA,
                'temperature': 0.7
            }
        )

        json_string = response.text.strip()
        print("✅ Gemini Yanıtı Alındı (JSON)")
        return json.loads(json_string)

    except Exception as e:
        print(f"Gemini API Hatası: {e}", file=sys.stderr)
        # Hata durumunda varsayılan metin döndür
        return {
            "analysis_title": "Veri Analiz Hatası",
            "tweet_text": f"🚨 Dur Bir Bakayım: '{trend_keyword}' trendini analiz ederken hata oluştu. Yine de bu kelimeye bir bak! 🤔 Bu kelime sana ne ifade ediyor?",
            "hashtags": ["#durbirbakiyim", "#TrendAnaliz", "#GeminiAI", "#Gündem"],
            "key_facts": ["Trend verisi yüklenemedi.", "Güncel bilgiye ulaşılamadı.", "Girişim fırsatını sen bul!"]
        }

# -------------------- Görsel yardımcıları --------------------
def load_font(size: int):
    """Sistemde yüklü bir TrueType fontu yükler."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", # Kalın font tercih edildi
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        # Ubuntu üzerinde sık bulunan fontlar
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
    return ImageFont.load_default()

def make_branded_image(title: str, key_facts: List[str]) -> bytes:
    """Trendle ilgili 3 ana bilgiyi içeren markalı bir görsel oluşturur."""
    W, H = CANVAS_W, CANVAS_H
    # Daha profesyonel ve kontrastlı renkler
    BG_COLOR = (240, 245, 250)
    TEXT_COLOR = (20, 30, 40)
    HIGHLIGHT_COLOR = (0, 102, 204) # Mavi

    img = Image.new("RGB", (W, H), color=BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Yazı tipleri
    brand_font = load_font(60)
    fact_font = load_font(40) # Font boyutu 48'den 40'a düşürüldü (Taşmayı önlemek için)
    foot_font  = load_font(32)

    # 1. Başlık: 'DUR BİR BAKAYIM ANALİZİ' (Mercek ikonu ile)
    brand_text = "🔍 DUR BİR BAKAYIM ANALİZİ"
    draw.text((W // 2, 180), brand_text, fill=TEXT_COLOR, font=brand_font, anchor="mm")
    
    # 2. Ana Bilgi Maddeleri (Key Facts)
    
    line_spacing = 110 # Satırlar arası boşluk artırıldı (Taşmayı önlemek için)
    start_y = FACTS_START_Y 

    for i, fact in enumerate(key_facts):
        # Basit bir nokta işareti yerine, daha belirgin bir karakter kullanılıyor
        fact_line = f"● {fact.strip()}" 
        
        y_pos = start_y + i * line_spacing

        # Metni çiz
        draw.text(
            (W // 2, y_pos), 
            fact_line, 
            fill=HIGHLIGHT_COLOR, 
            font=fact_font, 
            anchor="mm" # Metin kutusunun ortası (middle-middle) y pozisyonuna sabitlenir
        )

    # 3. Footer — sahiplik
    now_tr = datetime.now(timezone(timedelta(hours=3)))
    date_str_tr = f"{now_tr.day:02d} {tr_month_name(now_tr.month)} {now_tr.year}"
    footer = f"Analiz Başlığı: {title} | {date_str_tr}"
    
    draw.text((W // 2, H - 100), footer, fill=TEXT_COLOR, font=foot_font, anchor="ms")

    # Çerçeve Ekleme (Opsiyonel ama estetiği artırır)
    draw.rectangle([50, 50, W - 50, H - 50], outline=HIGHLIGHT_COLOR, width=5)


    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()

# -------------------- Medya yükleme & Tweet (Mevcut mantık korunmuştur) --------------------
def upload_media(oauth: OAuth1Session, image_bytes: bytes) -> str:
    """Görseli X API'ye yükler ve media ID'yi döndürür."""
    files = {"media": ("trend.png", image_bytes, "image/png")}
    resp = oauth.post(MEDIA_UPLOAD_ENDPOINT, files=files)
    if resp.status_code >= 400:
        print("X API Hatası (media/upload):", resp.status_code, resp.text, file=sys.stderr)
        # Hata durumunda ilerlemeyi durdur
        sys.exit(2)
    media_id = resp.json().get("media_id_string")
    if not media_id:
        print("X API Hatası: media_id alınamadı", file=sys.stderr)
        sys.exit(2)
    return media_id

def post_tweet_with_media(oauth: OAuth1Session, text: str, media_id: str):
    """Metin ve media ID ile tweet atar."""
    payload = {"text": text, "media": {"media_ids": [media_id]}}
    resp = oauth.post(POST_TWEET_ENDPOINT, json=payload)
    
    # YENİ KONTROL: 403 Forbidden Hatası için özel mesaj
    if resp.status_code == 403:
        print("-" * 50, file=sys.stderr)
        print("!!! KRİTİK X API HATASI: 403 YASAK (FORBIDDEN) !!!", file=sys.stderr)
        print("Gerekli izinleriniz eksik veya tokenlarınız yanlış. Lütfen X/Twitter geliştirici portalına gidin ve:", file=sys.stderr)
        print("1. Uygulamanızın **Permissions (İzinler)** bölümünde **Read and Write (Oku ve Yaz)** iznine sahip olduğundan emin olun.", file=sys.stderr)
        print("2. Environment variable/secret'larınızı (TWITTER_...) doğru şekilde girdiğinizi kontrol edin.", file=sys.stderr)
        print("-" * 50, file=sys.stderr)
        sys.exit(2)
        
    if resp.status_code >= 400:
        print("X API Hatası (tweet):", resp.status_code, resp.text, file=sys.stderr)
        sys.exit(2)
        
    data = resp.json()
    tweet_id = (data or {}).get("data", {}).get("id")
    print(f"✅ Başarılı Tweet ID: {tweet_id}")
    print(f"İçerik:\n{text}")

# -------------------- main --------------------
def main():
    try:
        # 1. Trend Tespiti
        trending_topic = get_daily_trending_topic()

        # 2. İçerik Oluşturma (Gemini)
        gemini_data = generate_content_with_gemini(trending_topic)
        
        # 3. Post Metni ve Hashtag Hazırlama
        analysis_title = gemini_data["analysis_title"]
        tweet_text = gemini_data["tweet_text"]
        hashtags = " ".join(f"#{tag.strip('#')}" for tag in gemini_data["hashtags"])
        key_facts = gemini_data.get("key_facts", []) # Yeni bilgi listesini çek

        # Post metnine hashtag'leri ve affiliate/çağrı satırını ekle
        final_tweet_text = f"🚨 {analysis_title}\n\n{tweet_text}\n\n{hashtags}\n\n{OWNER_HANDLE}"
        
        # X karakter limitini kontrol et (280)
        if len(final_tweet_text) > 280:
            print(f"UYARI: Tweet metni 280 karakteri aşıyor. Kırpılıyor. Uzunluk: {len(final_tweet_text)}")
            final_tweet_text = final_tweet_text[:277] + "..."
            
        print(f"📝 Son Tweet Uzunluğu: {len(final_tweet_text)}")

        # 4. Görsel Oluşturma (Yeni markalı görsel - Anahtar bilgileri görselde gösterir)
        image_bytes = make_branded_image(analysis_title, key_facts)

        # 5. X'e Post Atma
        oauth = oauth1_session_from_env()
        media_id = upload_media(oauth, image_bytes)
        post_tweet_with_media(oauth, final_tweet_text, media_id)

    except Exception as e:
        print(f"!!! KRİTİK HATA - İşlem Başarısız: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1) # Hata kodu 1'i tekrardan döndürüyoruz

if __name__ == "__main__":
    main()
