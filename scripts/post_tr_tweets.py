#!/usr/bin/env python3
import os
import json
import time
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

import requests
from requests_oauthlib import OAuth1
from openai import OpenAI

# -------------------- Config --------------------
TW_POST_ENDPOINT = "https://api.twitter.com/1.1/statuses/update.json"

OWNER_HANDLE = os.getenv("OWNER_HANDLE", "")  # İstersen sabit bir hesap çağrısı eklemek için kullan
MODEL = os.getenv("MODEL", "gpt-4o-mini")
POST_TO_TWITTER = (os.getenv("POST_TO_TWITTER", "false").lower() == "true")

# Türkiye sabiti (Türkiye sabit UTC+3)
TR_TZ = timezone(timedelta(hours=3))


def require_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return v


def get_oai_client() -> OpenAI:
    # OPENAI_API_KEY zorunlu
    require_env("OPENAI_API_KEY")
    return OpenAI()


def build_prompt(now_tr: datetime) -> List[Dict[str, Any]]:
    """
    Modelden iki şey istiyoruz:
      1) Türkiye'de bugün en çok konuşulan 10 konu başlığı (çok kısa)
      2) Bu gündemi özetleyen 4–5 adet, 270 karakteri geçmeyen, Türkçe tweet (thread olarak paylaşılacak)
    """
    date_str = now_tr.strftime("%d %B %Y, %A %H:%M (TR)")
    sys_msg = (
        "Sen sosyal medya için Türkçe içerik üreten, özetlemeyi iyi yapan bir asistansın. "
        "Tüm çıktılar Türkçe olacak. Abartılı emoji ve hashtag kullanma; "
        "tweet başına en fazla 2 makul hashtag yer verebilirsin (zorunlu değil). "
        "Her tweet 270 karakteri geçmemeli."
    )
    user_msg = f"""
Bugünün tarihi: {date_str}.
Görev:
1) Türkiye gündeminde bugün en popüler 10 konuyu (çok kısa başlıklar) listele.
2) Bu gündemi 4–5 kısa tweet ile özetle. Her tweet bağımsız anlamlı olsun; ister istemez birbiriyle devam hissi de verebilir.
3) Tweet metinleri 270 karakteri geçmesin. Net ve bilgi odaklı olsun.

ÇIKTIYI SADECE JSON OLARAK DÖN:
{{
  "topics": ["...", "...", "...", "...", "...", "...", "...", "...", "...", "..."],
  "tweets": ["tweet-1", "tweet-2", "tweet-3", "tweet-4", "tweet-5"]
}}

Kurallar:
- Tweetlerde kaynak/veri linki verme.
- Mümkünse gereksiz emoji/etiket kullanma; en fazla 1-2 uygun hashtag.
- Clickbait yapma.
"""
    return [
        {"role": "system", "content": sys_msg},
        {"role": "user", "content": user_msg},
    ]


def generate_tr_trend_tweets(client: OpenAI, now_tr: datetime) -> Dict[str, Any]:
    messages = build_prompt(now_tr)
    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.7,
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content
    data = json.loads(content)

    topics = data.get("topics", [])
    tweets = data.get("tweets", [])

    # Güvenlik: sınırlar
    topics = [str(t)[:120] for t in topics][:10]
    tweets = [str(t).strip()[:270] for t in tweets][:5]

    # En az 4 tweet garanti
    if len(tweets) < 4:
        # Tweet sayısı yetersizse, başlıklardan kısa özet döşeyelim
        extra = [f"Özet: {t[:230]}" for t in topics[len(tweets):len(tweets)+ (4-len(tweets))]]
        tweets.extend(extra)

    return {"topics": topics, "tweets": tweets}


def post_tweet_oauth1(status: str, reply_to_id: str = None) -> Dict[str, Any]:
    api_key = require_env("TWITTER_API_KEY")
    api_secret = require_env("TWITTER_API_SECRET")
    access_token = require_env("TWITTER_ACCESS_TOKEN")
    access_secret = require_env("TWITTER_ACCESS_TOKEN_SECRET")

    auth = OAuth1(api_key, api_secret, access_token, access_secret)
    payload = {
        "status": status
    }
    if reply_to_id:
        payload["in_reply_to_status_id"] = reply_to_id
        payload["auto_populate_reply_metadata"] = "true"

    r = requests.post(TW_POST_ENDPOINT, data=payload, auth=auth, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"Twitter API error {r.status_code}: {r.text}")
    return r.json()


def post_thread(tweets: List[str]) -> List[Dict[str, Any]]:
    results = []
    in_reply_to = None
    for i, tw in enumerate(tweets):
        # Fazlalık kontrolü (Twitter 280 sınırı—biz 270 kestik ama emniyet)
        text = tw.strip()
        if len(text) > 280:
            text = text[:279]
        res = post_tweet_oauth1(text, reply_to_id=in_reply_to)
        results.append(res)
        in_reply_to = res.get("id_str")
        time.sleep(2)  # X API nazik gecikme
    return results


def main():
    now_tr = datetime.now(tz=TR_TZ)

    client = get_oai_client()
    bundle = generate_tr_trend_tweets(client, now_tr)
    topics = bundle["topics"]
    tweets = bundle["tweets"]

    header = f"Türkiye Gündemi – {now_tr.strftime('%d %B %Y, %A')}\n"
    header += "Günün çok konuşulan başlıkları: " + "; ".join(topics[:10])
    # Header'ı ilk tweetin başına ekleyebiliriz, fakat 270 sınırı var.
    # Bu nedenle ilk tweete kısa bir giriş yapalım:
    intro = f"Türkiye gündemi ({now_tr.strftime('%d %B %Y')}):"
    tweets = [f"{intro}"] + tweets
    tweets = tweets[:5]  # Toplam 5 tweeti aşmayalım (intro + 4)

    if not POST_TO_TWITTER:
        print("=== DRY RUN (POST_TO_TWITTER != true) ===")
        print("Konular (10):")
        for i, t in enumerate(topics, 1):
            print(f"{i:02d}. {t}")
        print("\nTweet Thread (max 5):")
        for i, tw in enumerate(tweets, 1):
            print(f"[{i}] {tw}\n")
        return

    # Gönderim
    try:
        results = post_thread(tweets)
        print("Thread gönderildi. İlk tweet id:", results[0].get("id_str"))
    except Exception as e:
        print("Gönderim hatası:", e)
        raise


if __name__ == "__main__":
    main()
