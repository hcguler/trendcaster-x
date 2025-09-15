import os
import sys
import re
from datetime import datetime, timezone, timedelta
from typing import List
from requests_oauthlib import OAuth1Session

# ---- Sabitler ----
POST_TWEET_ENDPOINT = "https://api.twitter.com/2/tweets"
TRENDS_PLACE_ENDPOINT = "https://api.twitter.com/1.1/trends/place.json"
TR_WOEID_DEFAULT = 23424969  # Türkiye

def istanbul_now_iso() -> str:
    tz_tr = timezone(timedelta(hours=3))
    return datetime.now(tz_tr).isoformat(timespec="seconds")

def require_env(keys: List[str]) -> dict:
    envs = {k: os.environ.get(k) for k in keys}
    missing = [k for k, v in envs.items() if not v]
    if missing:
        print(f"HATA: Eksik secret(lar): {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    return envs

def oauth1_session_from_env() -> OAuth1Session:
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

def get_twitter_trends_oauth1(woeid: int = TR_WOEID_DEFAULT, limit: int = 5) -> List[str]:
    """
    v1.1 trends/place ile ülke/bölge trendlerini çeker.
    Not: Bu uç noktayı kullanmak için hesabın plan/izinleri yeterli olmalıdır.
    """
    oauth = oauth1_session_from_env()
    resp = oauth.get(TRENDS_PLACE_ENDPOINT, params={"id": str(woeid)})
    if resp.status_code >= 400:
        print("X API Hatası (trends/place):", resp.status_code, resp.text, file=sys.stderr)
        # Sık görülen: 403 -> plan/izin yetersizliği
        return []
    data = resp.json()
    if not isinstance(data, list) or not data:
        return []
    trends = data[0].get("trends", []) or []
    names = []
    for t in trends:
        name = t.get("name")
        if isinstance(name, str) and name.strip():
            # fazlalık boşlukları normalize et
            name = re.sub(r"\s+", " ", name.strip())
            names.append(name)
            if len(names) >= limit:
                break
    return names

def build_tweet_from_trends(trends: List[str]) -> str:
    if not trends:
        return f"Türkiye trendleri alınamadı — {istanbul_now_iso()}"
    header = "🐦 Türkiye Twitter Trendleri (Top 5)"
    body = ", ".join(trends)
    text = f"{header}\n{body}"
    if len(text) > 280:
        text = text[:277].rstrip() + "..."
    return text

def post_tweet_oauth1(tweet_text: str):
    oauth = oauth1_session_from_env()
    resp = oauth.post(POST_TWEET_ENDPOINT, json={"text": tweet_text})
    if resp.status_code >= 400:
        print("X API Hatası (tweet):", resp.status_code, resp.text, file=sys.stderr)
        sys.exit(2)
    data = resp.json()
    tweet_id = (data or {}).get("data", {}).get("id")
    print(f"Başarılı ✅ Tweet ID: {tweet_id}")
    print(f"İçerik:\n{tweet_text}")

def main():
    # İstersen WOEID'i env ile geçebilirsin; boşsa Türkiye kullanır.
    woeid = int(os.environ.get("TRENDS_WOEID", TR_WOEID_DEFAULT))
    trends5 = get_twitter_trends_oauth1(woeid=woeid, limit=5)

    # Sadece çekmek istersen buraya kadar yeter; aşağıda tweet atıyoruz.
    tweet_text = build_tweet_from_trends(trends5)
    post_tweet_oauth1(tweet_text)

if __name__ == "__main__":
    main()
