import os, sys, re
from datetime import datetime, timezone, timedelta
from typing import List
from requests_oauthlib import OAuth1Session
import httpx
from bs4 import BeautifulSoup

POST_TWEET_ENDPOINT = "https://api.twitter.com/2/tweets"
TRENDS24_URL = os.environ.get("TRENDS24_URL", "https://trends24.in/turkey/")  # ülke/şehir sayfası

def istanbul_now_iso() -> str:
    tz_tr = timezone(timedelta(hours=3))
    return datetime.now(tz_tr).isoformat(timespec="seconds")

def require_env(keys: List[str]) -> dict:
    envs = {k: os.environ.get(k) for k in keys}
    missing = [k for k,v in envs.items() if not v]
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

def get_trends_from_trends24(limit: int = 5) -> List[str]:
    """Trends24 sayfasını parse eder ve ilk 5 trendi döner."""
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        with httpx.Client(timeout=20) as client:
            r = client.get(TRENDS24_URL, headers=headers)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            # En üst karttaki trendleri al: genellikle ilk ".trend-card" bloğu
            card = soup.select_one(".trend-card")
            items = (card.select(".trend-card__list li a.trend-name") if card else []) or \
                    soup.select(".trend-card .trend-card__list li a.trend-name")
            names = []
            for a in items:
                name = (a.get_text() or "").strip()
                if not name:
                    continue
                name = re.sub(r"\s+", " ", name)
                names.append(name)
                if len(names) >= limit:
                    break
            return names
    except Exception as e:
        print(f"[WARN] Trends24 alınamadı: {e}", file=sys.stderr)
        return []

def build_trend_tweet(trends: List[str]) -> str:
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
    trends5 = get_trends_from_trends24(limit=5)
    tweet_text = build_trend_tweet(trends5)
    post_tweet_oauth1(tweet_text)

if __name__ == "__main__":
    main()
