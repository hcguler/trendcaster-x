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
        "tweet_text": {"type": "STRING", "description": "180 karakteri geçmeyen, analizi ve merak uyandıran soruyu içeren ana post metni. Başlık içermemelidir. (Toplam tweet limitine uyum için azaltıldı)."},
        "hashtags": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Post ile ilgili en etkili 4 adet hashtag."},
    },
    "propertyOrdering": ["analysis_title", "tweet_text", "hashtags"]
}

def generate_content_with_gemini(trend_keyword: str) -> dict:
    """Gemini API'yi kullanarak post metni ve hashtag'leri oluşturur."""
    envs = require_env(["GEMINI_API_KEY"])
    API_KEY = envs["GEMINI_API_KEY"]
    
    # API çağrısı için istemci oluşturulur
    client = genai.Client(api_key=API_KEY)

    system_prompt = (
        "Sen, 'Dur Bir Bakayım' adlı bir X (Twitter) hesabının Veri Analistisin. "
        "Görevin, sana verilen trend anahtar kelimesi hakkında e-ticaret, girişimcilik veya teknoloji perspektifinden hızlı ve ticari değeri olan bir analiz sunmaktır. "
        "Çıktı sadece JSON formatında olmalı ve şu kurallara uymalıdır: "
        "1. Analiz başlığı (analysis_title) 3-5 kelime olmalı, emoji içermemelidir. "
        "2. Post metni (tweet_text) 180 karakteri kesinlikle geçmemelidir. 'Dur bir bakayım' formatına uygun olarak merak uyandırmalı ve sonunda mutlaka bir soru sormalıdır. "
        "3. Hashtag'ler güncel, ilgili ve Türkçe olmalıdır."
    )

    user_query = f"Bugünün Google Trend kelimesi: '{trend_keyword}'. Bu kelimenin e-ticaret veya girişimcilik potansiyelini analiz et, X post metnini ve hashtag'lerini oluştur."

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

        # Yanıt içeriğini JSON olarak parse et
        json_string = response.text.strip()
        print("✅ Gemini Yanıtı Alındı (JSON)")
        return json.loads(json_string)

    except Exception as e:
        print(f"Gemini API Hatası: {e}", file=sys.stderr)
        # Hata durumunda varsayılan metin döndür
        return {
            "analysis_title": "Veri Analiz Hatası",
            "tweet_text": f"🚨 Dur Bir Bakayım: '{trend_keyword}' trendini analiz ederken hata oluştu. Yine de bu kelimeye bir bak! 🤔 Bu kelime sana ne ifade ediyor?",
            "hashtags": ["#durbirbakiyim", "#TrendAnaliz", "#GeminiAI", "#Gündem"]
        }

# -------------------- Görsel yardımcıları --------------------
def load_font(size: int):
    """Sistemde yüklü bir TrueType fontu yükler."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        # Ubuntu üzerinde sık bulunan fontlar
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                pass
    return ImageFont.load_default()

def make_branded_image(title: str, trend_text: str) -> bytes:
    """Trend adını içeren markalı bir görsel oluşturur."""
    W, H = CANVAS_W, CANVAS_H
    img = Image.new("RGB", (W, H), color=(248, 250, 252)) # Açık Mavi/Gri Arkaplan
    draw = ImageDraw.Draw(img)

    # Yazı tipleri
    brand_font = load_font(60)
    trend_font = load_font(90)
    foot_font  = load_font(32)

    # 1. Başlık: 'DUR BİR BAKAYIM'
    brand_text = "🚨 DUR BİR BAKAYIM ANALİZİ"
    # anchor="mm" kullanıldığında x,y noktası merkeze hizalanır
    draw.text((W // 2, 180), brand_text, fill=(40, 50, 60), font=brand_font, anchor="mm")

    # 2. Ana Trend Metni (Otomatik Satır Sarma ve Merkezi)
    words = trend_text.split()
    line_limit = 18 # Karakter limiti (yaklaşık)
    lines = []
    current_line = ""

    for word in words:
        if len(current_line + " " + word) <= line_limit or not current_line:
            current_line += (" " if current_line else "") + word
        else:
            lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)

    # --- PILLOW DEPRECATED METHOD FIX ---
    # Pillow'un yeni versiyonlarında getsize() metodu kaldırıldığı için textbbox() kullanılıyor.
    line_heights = []
    total_text_height = 0
    line_spacing = 15 # Satırlar arası boşluk

    for line in lines:
        try:
            # draw.textbbox(xy, text, font=font) -> (left, top, right, bottom)
            bbox = draw.textbbox((0, 0), line, font=trend_font)
            h = bbox[3] - bbox[1] # bottom - top
        except Exception:
            # Hata durumunda fontun varsayılan büyüklüğünü kullan
            h = trend_font.size 

        line_heights.append(h)
        total_text_height += h + line_spacing

    # Son satırın boşluğunu çıkar
    if lines:
        total_text_height -= line_spacing
    
    # Metni ortalamak için başlangıç Y koordinatını bul
    start_y = H // 2 - total_text_height // 2 + 50 # +50 Footer için kaydırır
    
    # Metni çiz
    current_y = start_y
    for line, h in zip(lines, line_heights):
        # Anchor "mm" (middle-middle) kullanıldığı için, y'yi satır yüksekliğinin yarısı kadar kaydırarak merkezi pozisyonu buluyoruz.
        draw.text((W // 2, current_y + h / 2), line, fill=(0, 100, 200), font=trend_font, anchor="mm")
        current_y += h + line_spacing # Satır yüksekliği + aralık
    # --- PILLOW DEPRECATED METHOD FIX END ---

    # 3. Footer — sahiplik
    footer = f"Analiz Başlığı: {title} | {datetime.now(timezone(timedelta(hours=3))).strftime('%d %b %Y')}"
    draw.text((W // 2, H - 100), footer, fill=(90, 100, 110), font=foot_font, anchor="ms")

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
        
        # Post metnine hashtag'leri ve affiliate/çağrı satırını ekle
        final_tweet_text = f"🚨 {analysis_title}\n\n{tweet_text}\n\n{hashtags}\n\n{OWNER_HANDLE}"
        
        # X karakter limitini kontrol et (280)
        # Gemini'den gelen metin 180 karaktere çekildiği için buraya nadiren düşülecektir.
        if len(final_tweet_text) > 280:
            print(f"UYARI: Tweet metni 280 karakteri aşıyor. Kırpılıyor. Uzunluk: {len(final_tweet_text)}")
            # Güvenli kırpma: '...' (3 karakter) için yer bırak
            final_tweet_text = final_tweet_text[:277] + "..."
            
        print(f"📝 Son Tweet Uzunluğu: {len(final_tweet_text)}")

        # 4. Görsel Oluşturma (Yeni markalı görsel)
        image_bytes = make_branded_image(analysis_title, trending_topic)

        # 5. X'e Post Atma
        oauth = oauth1_session_from_env()
        media_id = upload_media(oauth, image_bytes)
        post_tweet_with_media(oauth, final_tweet_text, media_id)

    except Exception as e:
        print(f"!!! KRİTİK HATA - İşlem Başarısız: {e}", file=sys.stderr)
        # Hata izleme (traceback) ekleyerek neden çöktüğünü Actions loglarında görmenizi sağlar
        import traceback
        traceback.print_exc(file=sys.stderr)
        sys.exit(1) # Hata kodu 1'i tekrardan döndürüyoruz

if __name__ == "__main__":
    main()
