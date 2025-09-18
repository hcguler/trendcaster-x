import os
import sys
import time
import json
from datetime import datetime, timedelta

# Kütüphane kontrolü
try:
    import feedparser
    import openai
    from dotenv import load_dotenv
    from tweepy import Client as TweepyClient
except Exception as e:
    print("Eksik kütüphane tespit edildi. Lütfen requirements.txt dosyasını kontrol edin.")
    print(str(e))
    sys.exit(1)

# .env dosyasını yükle
load_dotenv()

# Ortam değişkenleri
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
MODEL = os.getenv('MODEL', 'gpt-3.5-turbo')
POST_TO_TWITTER = os.getenv('POST_TO_TWITTER', 'true').lower() == 'true'

# Twitter credentials
TW_API_KEY = os.getenv('TWITTER_API_KEY')
TW_API_SECRET = os.getenv('TWITTER_API_SECRET')
TW_ACCESS_TOKEN = os.getenv('TWITTER_ACCESS_TOKEN')
TW_ACCESS_SECRET = os.getenv('TWITTER_ACCESS_TOKEN_SECRET')

if not OPENAI_API_KEY:
    print('HATA: OPENAI_API_KEY bulunamadı!')
    sys.exit(1)

# OpenAI API key'i ayarla
openai.api_key = OPENAI_API_KEY

# RSS kaynakları
RSS_SOURCES = [
    'https://www.aa.com.tr/rss/default?cat=turkiye',
    'https://www.hurriyet.com.tr/rss/gundem',
    'https://www.bbc.com/turkce/rss.xml',
    'https://www.sozcu.com.tr/feed/',
    'https://www.ntv.com.tr/son-dakika.rss'
]

# Son 12 saat için filtre
SINCE = datetime.utcnow() - timedelta(hours=12)

def fetch_recent_entries(sources, since_dt):
    """RSS kaynaklarından son haberleri çeker"""
    entries = []
    for url in sources:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries:
                published = None
                if hasattr(e, 'published_parsed') and e.published_parsed:
                    published = datetime(*e.published_parsed[:6])
                elif hasattr(e, 'updated_parsed') and e.updated_parsed:
                    published = datetime(*e.updated_parsed[:6])
                
                if published is None or published >= since_dt:
                    entries.append({
                        'title': getattr(e, 'title', '').strip(),
                        'link': getattr(e, 'link', ''),
                        'summary': getattr(e, 'summary', '').strip(),
                        'published': published.isoformat() if published else ''
                    })
        except Exception as ex:
            print(f'Hata: {url} -> {ex}')
    
    # Tekrar eden başlıkları temizle
    unique_entries = []
    seen_titles = set()
    for entry in entries:
        title_key = entry['title'][:100]
        if title_key not in seen_titles and entry['title']:
            seen_titles.add(title_key)
            unique_entries.append(entry)
    
    return unique_entries

def build_prompt(entries):
    """OpenAI için prompt oluşturur"""
    if not entries:
        return "Türkiye gündemi hakkında genel bir değerlendirme yaparak 4 tweet'lik bir thread oluştur."
    
    # En fazla 15 haber al
    news_items = []
    for e in entries[:15]:
        if e['title']:
            news_items.append(f"• {e['title']}")
    
    prompt = """Aşağıdaki güncel haberlere dayanarak Türkiye gündemini 4 tweet'lik bir thread olarak özetle.

KURALLAR:
- Her tweet MAX 280 karakter olmalı
- 1. Tweet: Gündemin en önemli konusu + genel giriş
- 2. Tweet: Detaylar veya ikinci önemli konu
- 3. Tweet: Diğer önemli gelişmeler
- 4. Tweet: Özet ve değerlendirme
- Her tweet'e uygun 2-3 hashtag ekle (#Türkiye #Gündem vb.)
- Objektif ve bilgilendirici ol
- Tweet'leri numaralandırma, sadece metinleri ver

HABERLER:
"""
    prompt += '\n'.join(news_items)
    return prompt

def ask_openai(prompt, model=MODEL, max_tokens=800):
    """OpenAI API çağrısı yapar"""
    try:
        response = openai.ChatCompletion.create(
            model=model,
            messages=[
                {
                    'role': 'system',
                    'content': 'Sen Türkiye gündemi hakkında objektif ve bilgilendirici tweet thread\'leri oluşturan bir asistansın.'
                },
                {
                    'role': 'user',
                    'content': prompt
                }
            ],
            max_tokens=max_tokens,
            temperature=0.7,
        )
        return response['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f'OpenAI API hatası: {e}')
        return None

def parse_tweets(text):
    """Metni 4 tweet'e böler"""
    # Satırlara ayır ve boşlukları temizle
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    
    tweets = []
    current_tweet = ""
    
    for line in lines:
        # Eğer satır bir tweet gibi görünüyorsa
        if len(line) > 50 and len(tweets) < 4:
            if current_tweet:
                tweets.append(current_tweet[:280])
            current_tweet = line
        elif current_tweet:
            # Mevcut tweet'e ekle
            if len(current_tweet + " " + line) <= 280:
                current_tweet += " " + line
            else:
                tweets.append(current_tweet[:280])
                current_tweet = line
    
    # Son tweet'i ekle
    if current_tweet and len(tweets) < 4:
        tweets.append(current_tweet[:280])
    
    # 4 tweet'e tamamla
    while len(tweets) < 4:
        tweets.append("Güncel haberler için takipte kalın! #Türkiye #Gündem")
    
    return tweets[:4]

def save_tweets(tweets, filename='tweets.txt'):
    """Tweet'leri dosyaya kaydet"""
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(f"=== {datetime.now().strftime('%Y-%m-%d %H:%M')} ===\n\n")
        for i, tweet in enumerate(tweets, 1):
            f.write(f"TWEET {i}:\n{tweet}\n\n")
    print(f'✓ Tweetler {filename} dosyasına kaydedildi.')

def post_twitter_thread(tweets):
    """Twitter'a thread olarak gönder"""
    if not all([TW_API_KEY, TW_API_SECRET, TW_ACCESS_TOKEN, TW_ACCESS_SECRET]):
        print('Twitter credentials eksik!')
        return None
    
    try:
        client = TweepyClient(
            consumer_key=TW_API_KEY,
            consumer_secret=TW_API_SECRET,
            access_token=TW_ACCESS_TOKEN,
            access_token_secret=TW_ACCESS_SECRET,
            wait_on_rate_limit=True
        )
        
        tweet_ids = []
        reply_to_id = None
        
        for i, tweet_text in enumerate(tweets):
            print(f'Tweet {i+1} gönderiliyor...')
            
            if reply_to_id is None:
                # İlk tweet
                response = client.create_tweet(text=tweet_text)
            else:
                # Reply olarak gönder
                response = client.create_tweet(
                    text=tweet_text,
                    in_reply_to_tweet_id=reply_to_id
                )
            
            tweet_id = response.data['id']
            tweet_ids.append(tweet_id)
            reply_to_id = tweet_id
            
            time.sleep(2)  # Rate limit için bekle
        
        print(f'✓ Thread başarıyla gönderildi! İlk tweet ID: {tweet_ids[0]}')
        return tweet_ids[0]
        
    except Exception as e:
        print(f'Twitter gönderim hatası: {e}')
        return None

def main():
    print("=== Türkiye Gündem Bot Başlatıldı ===")
    print(f"Tarih: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    
    # Haberleri çek
    print("\n1. Haberler çekiliyor...")
    entries = fetch_recent_entries(RSS_SOURCES, SINCE)
    print(f"✓ {len(entries)} haber bulundu.")
    
    # Prompt oluştur ve OpenAI'ya gönder
    print("\n2. AI ile analiz yapılıyor...")
    prompt = build_prompt(entries)
    ai_response = ask_openai(prompt)
    
    if not ai_response:
        print("✗ AI yanıtı alınamadı!")
        sys.exit(1)
    
    # Tweet'leri parse et
    print("\n3. Tweetler oluşturuluyor...")
    tweets = parse_tweets(ai_response)
    
    # Kaydet
    save_tweets(tweets)
    
    # Twitter'a gönder
    if POST_TO_TWITTER:
        print("\n4. Twitter'a gönderiliyor...")
        post_twitter_thread(tweets)
    else:
        print("\n4. Twitter gönderimi kapalı (POST_TO_TWITTER=false)")
        print("\nÖnizleme:")
        for i, tweet in enumerate(tweets, 1):
            print(f"\nTWEET {i}:\n{tweet}")

if __name__ == '__main__':
    main()
