""" Türkiye gündemi için günlük tek prompt ile 4 ardışık Tweet hazırlayan ve (isteğe bağlı) X/Twitter'a yollayan Python scripti.

Kullanım:

GitHub workspace içine bu dosyayı koyun.

GitHub Secrets veya .env içine aşağıdaki değişkenleri ekleyin: OPENAI_API_KEY  (zorunlu) MODEL           (opsiyonel, default: gpt-3.5-turbo) TWITTER_API_KEY TWITTER_API_SECRET TWITTER_ACCESS_TOKEN TWITTER_ACCESS_TOKEN_SECRET POST_TO_TWITTER ("true" veya "false", default false)


Script neler yapar:

Belirtilen RSS kaynaklarını çeker (Türkçe haber kaynakları).

Son 24 saatteki önemli başlıkları seçer ve tek bir prompt'ta OpenAI'ye gönderir.

OpenAI'den dönen yanıtı 4 ardışık, thread olacak şekilde 280 karakter limitlerine uyan tweet'lere böler.

İsteğe bağlı olarak X/Twitter API'si ile otomatik olarak paylaşır.

Ayrıca workspace içine tweets.txt dosyası yazar.


Notlar:

X/Twitter API erişimi kısıtlı/ücretli olabilir; öncelikle kendi hesabınızın API erişimini kontrol edin.

Model tercihini değiştirebilirsiniz. gpt-3.5-turbo güvenli ve yaygın çalışır.

İyileştirme: haber filtreleri, kaynak ekleme/çıkarma, dil modeli parametreleri.


"""

import os import sys import time import json from datetime import datetime, timedelta

try: import feedparser import openai from dotenv import load_dotenv from tweepy import Client as TweepyClient except Exception as e: print("Eksik kütüphane tespit edildi. Lütfen requirements.txt oluşturup yükleyin veya 'pip install -r requirements.txt' çalıştırın.") print(str(e)) # Create a simple requirements.txt for convenience with open('requirements.txt', 'w', encoding='utf-8') as f: f.write('openai\nfeedparser\npython-dotenv\ntweepy\n') sys.exit(1)

load .env if exists

load_dotenv()

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY') MODEL = os.getenv('MODEL', 'gpt-3.5-turbo') POST_TO_TWITTER = os.getenv('POST_TO_TWITTER', 'false').lower() == 'true'

Twitter credentials (optional)

TW_API_KEY = os.getenv('TWITTER_API_KEY') TW_API_SECRET = os.getenv('TWITTER_API_SECRET') TW_ACCESS_TOKEN = os.getenv('TWITTER_ACCESS_TOKEN') TW_ACCESS_SECRET = os.getenv('TWITTER_ACCESS_TOKEN_SECRET')

if not OPENAI_API_KEY: print('Lütfen OPENAI_API_KEY ortam değişkenini ayarlayın. (GitHub Secrets veya .env)') sys.exit(1)

openai.api_key = OPENAI_API_KEY

RSS kaynakları (isteğe göre ekleyin/çıkarın)

RSS_SOURCES = [ 'https://www.aa.com.tr/rss/default?cat=turkiye',            # Anadolu Ajansı 'https://www.hurriyet.com.tr/rss/gundem',                  # Hürriyet 'https://www.bbc.com/turkce/rss.xml',                     # BBC Türkçe 'https://www.sozcu.com.tr/feed/',                          # Sözcü 'https://www.ntv.com.tr/son-dakika.rss'                    # NTV (format değişebilir) ]

zaman filtresi: son 24 saat

SINCE = datetime.utcnow() - timedelta(hours=24)

def fetch_recent_entries(sources, since_dt): entries = [] for url in sources: try: feed = feedparser.parse(url) for e in feed.entries: # Tarih parsing farklı kaynaklarda değişir; feedparser kullanarak genelde e.published_parsed mevcut published = None if hasattr(e, 'published_parsed') and e.published_parsed: published = datetime(*e.published_parsed[:6]) elif hasattr(e, 'updated_parsed') and e.updated_parsed: published = datetime(*e.updated_parsed[:6]) else: # fallback: ignore date published = None

if published is None or published >= since_dt:
                entries.append({'title': getattr(e, 'title', '') or '',
                                'link': getattr(e, 'link', ''),
                                'summary': getattr(e, 'summary', '') or '',
                                'published': published.isoformat() if published else ''})
    except Exception as ex:
        print(f'Kaynağı çekerken hata: {url} -> {ex}')
# Kısa liste: tekrar eden başlıkları temizle
uniq = []
seen = set()
for e in entries:
    key = (e['title'][:120], e['link'])
    if key in seen:
        continue
    seen.add(key)
    uniq.append(e)
return uniq

def build_prompt(entries): # Türkçe prompt bullets = [] for e in entries[:12]: t = e['title'].strip() l = e['link'] if t: bullets.append(f"- {t} ({l})")

prompt = (
    "Lütfen aşağıdaki maddelere dayanarak Türkiye gündemini açık ve öz bir şekilde, toplam 4 ardışık Tweet (thread) oluşturacak şekilde özetle."
    " Her tweet en fazla 280 karakter olmalı. Her tweet sonunda uygun 2-3 etiket (hashtag) ekle."
    " Tweet'ler birbirini takip eden bir hikâye gibi olsun; ilk tweet giriş ve en önemli nokta olsun, son tweet ise sonuç/özet/eylem çağrısı içerebilir."
    " Sadece tweet metinlerini sırayla ver, numara/başlık kullanma."
    " Haber maddeleri (kaynaklar):\n\n"
)
prompt += '\n'.join(bullets)
return prompt

def ask_openai(prompt, model=MODEL, max_tokens=600): try: resp = openai.ChatCompletion.create( model=model, messages=[{'role':'system','content':'Türkçe olarak, Twitter formatına uygun, kısa ve net özetler üret.'}, {'role':'user','content':prompt}], max_tokens=max_tokens, temperature=0.6, ) text = resp['choices'][0]['message']['content'].strip() return text except Exception as e: print('OpenAI isteğinde hata:', e) return None

def split_into_tweets(text): # Model'den beklenti: zaten 4 adet tweet dönecek. # Yine de güvenlik için satırlara böl, 280 karakter sınırına uy. parts = [p.strip('- ').strip() for p in text.split('\n') if p.strip()] tweets = [] for p in parts: if len(p) <= 280: tweets.append(p) else: # uzun ise cümle bazında bölmeye çalış words = p.split() cur = '' for w in words: if len(cur) + len(w) + 1 <= 270:  # biraz esneklik cur = (cur + ' ' + w).strip() else: tweets.append(cur) cur = w if cur: tweets.append(cur) # Sadece ilk 4'ü al, eğer model daha az üretmişse eksikse doldurma return tweets[:4]

def save_tweets_to_file(tweets, path='tweets.txt'): with open(path, 'w', encoding='utf-8') as f: for i, t in enumerate(tweets, 1): f.write(f"--- TWEET {i} ---\n") f.write(t + '\n\n') print(f'Tweetler {path} dosyasına kaydedildi.')

def post_thread_to_twitter(tweets): if not (TW_API_KEY and TW_API_SECRET and TW_ACCESS_TOKEN and TW_ACCESS_SECRET): print('Twitter için gerekli credentiallar eksik. Tweet gönderilmiyor.') return None try: client = TweepyClient( consumer_key=TW_API_KEY, consumer_secret=TW_API_SECRET, access_token=TW_ACCESS_TOKEN, access_token_secret=TW_ACCESS_SECRET, wait_on_rate_limit=True ) reply_to = None first_tweet_id = None for t in tweets: if reply_to is None: res = client.create_tweet(text=t) first_tweet_id = res.data['id'] if hasattr(res, 'data') else res['data']['id'] reply_to = first_tweet_id time.sleep(1) else: res = client.create_tweet(text=t, in_reply_to_tweet_id=reply_to) # set new reply_to to the most recent tweet reply_to = res.data['id'] if hasattr(res, 'data') else res['data']['id'] time.sleep(1) print('Thread başarıyla gönderildi. İlk tweet id:', first_tweet_id) return first_tweet_id except Exception as e: print('Twitter gönderim hatası:', e) return None

def main(): entries = fetch_recent_entries(RSS_SOURCES, SINCE) if not entries: print('Son 24 saatte alınabilecek haber başlığı bulunamadı. Yine de model için boş özet üretilecek.') prompt = build_prompt(entries) print('OpenAI prompt hazır. Model çağrılıyor...') answer = ask_openai(prompt) if not answer: print('Model yanıtı alınamadı.') sys.exit(1)

tweets = split_into_tweets(answer)
if not tweets:
    print('Tweet üretilemedi.')
    sys.exit(1)

save_tweets_to_file(tweets)

if POST_TO_TWITTER:
    print('Twitter'a gönderiliyor...')
    post_thread_to_twitter(tweets)
else:
    print('POST_TO_TWITTER false olarak ayarlı; sadece dosyaya kaydedildi.')

if name == 'main': main()

Ek: Bu script çalıştırıldığında requirements.txt oluşturulmuş olacak.

GitHub Actions için secrets ayarlayarak bu scripti günlük olarak çalıştırabilirsiniz.

Örnek GitHub Actions workflow (manuel olarak .github/workflows/daily.yml olarak ekleyin):

name: Daily Turkey News Thread

on:

schedule:

- cron: '0 9 * * *'  # Her gün UTC 09:00'da (İstanbul için saat ayarlayın)

workflow_dispatch: {}

jobs:

build:

runs-on: ubuntu-latest

steps:

- uses: actions/checkout@v4

- name: Set up Python

uses: actions/setup-python@v4

with:

python-version: '3.11'

- name: Install requirements

run: |

python -m pip install --upgrade pip

pip install -r requirements.txt

- name: Run bot

env:

OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}

MODEL: gpt-3.5-turbo

POST_TO_TWITTER: ${{ secrets.POST_TO_TWITTER }}

TWITTER_API_KEY: ${{ secrets.TWITTER_API_KEY }}

TWITTER_API_SECRET: ${{ secrets.TWITTER_API_SECRET }}

TWITTER_ACCESS_TOKEN: ${{ secrets.TWITTER_ACCESS_TOKEN }}

TWITTER_ACCESS_TOKEN_SECRET: ${{ secrets.TWITTER_ACCESS_TOKEN_SECRET }}

run: |

python turkiye_gundemi_tweet_bot.py

