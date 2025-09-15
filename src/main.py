import os
import sys
from datetime import datetime, timezone, timedelta
from typing import List
from requests_oauthlib import OAuth1Session
import httpx
import feedparser

# pytrends yedek amaçlı; yoksa hatayı yumuşat
try:
    from pytrends.request import TrendReq
except Exception:
    TrendReq = None  # opsiyonel

POST_TWEET_ENDPOINT = "https://api.twitter.com/2/tweets"
TR_WOEID = 23424969  # Türkiye

# ------------------ helpers ------------------
def istanbul_now_iso():
    tz_tr = timezone(timedelta(hours=3))
    return datetime.now(tz_tr).isoformat(timespec="seconds")

# ---- GOOGLE TRENDS: Önce RSS, olmazsa pytrends fallback ----
def get_google_trends_tr(limit: int = 5) -> List[str]:
    topics = []
    
    # Try different RSS URL formats
    rss_urls = [
        "https://trends.google.com/trends/trendingsearches/daily/rss?geo=TR",
        "https://trends.google.com.tr/trends/trendingsearches/daily/rss?geo=TR",
        "https://trends.google.com/trends/trendingsearches/realtime?geo=TR&category=all"
    ]
    
    for rss_url in rss_urls:
        try:
            with httpx.Client(timeout=20) as client:
                r = client.get(rss_url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/rss+xml, application/xml"
                })
                if r.status_code == 200:
                    feed = feedparser.parse(r.text)
                    for e in feed.entries[:limit]:
                        title = (e.title or "").strip()
                        if title:
                            topics.append(title)
                    if topics:
                        break
        except Exception as e:
            continue
    
    if topics:
        return topics[:limit]
    
    # Fallback: pytrends with better error handling
    if TrendReq is None:
        print("[WARN] pytrends not installed", file=sys.stderr)
        return []
    
    try:
        pytrends = TrendReq(hl="tr-TR", tz=180, timeout=(10, 30))
        # Add retry logic
        for attempt in range(3):
            try:
                df = pytrends.trending_searches(pn="turkey")
                arr = [x for x in df[0].tolist() if isinstance(x, str)]
                return arr[:limit]
            except Exception:
                if attempt < 2:
                    time.sleep(2)  # Wait before retry
                continue
    except Exception as e:
        print(f"[WARN] Google Trends (pytrends) failed: {e}", file=sys.stderr)
        
    return []

# ---- TWITTER TRENDS: OAuth1 ile v1.1 trends/place ----
def get_twitter_trends_tr_oauth1(
    api_key: str, api_secret: str, access_token: str, access_secret: str, limit: int = 5
) -> List[str]:
    try:
        oauth = OAuth1Session(
            api_key,
            client_secret=api_secret,
            resource_owner_key=access_token,
            resource_owner_secret=access_secret
        )
        url = f"https://api.twitter.com/1.1/trends/place.json?id={TR_WOEID}"
        resp = oauth.get(url)
        resp.raise_for_status()
        data = resp.json()
        trends = data[0].get("trends", []) if data else []
        names = [t.get("name") for t in trends if isinstance(t.get("name"), str)]
        return names[:limit]
    except Exception as e:
        print(f"[WARN] Twitter trends alınamadı (OAuth1): {e}", file=sys.stderr)
        return []

def build_trend_tweet(google_topics: List[str], twitter_topics: List[str]) -> str:
    g = [t.strip() for t in google_topics if t and t.strip()]
    t = [x.strip() for x in twitter_topics if x and x.strip()]

    header = "🇹🇷 Türkiye Trendleri"
    g_line = "🔎 Google: " + (", ".join(g) if g else "—")
    t_line = "🐦 Twitter: " + (", ".join(t) if t else "—")

    text = f"{header}\n{g_line}\n{t_line}"
    if len(text) > 280:
        text = text[:277].rstrip() + "..."
    return text

def post_tweet_oauth1(tweet_text: str):
    api_key = os.environ.get("TWITTER_API_KEY")
    api_secret = os.environ.get("TWITTER_API_SECRET")
    access_token = os.environ.get("TWITTER_ACCESS_TOKEN")
    access_secret = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET")

    missing = [k for k, v in {
        "TWITTER_API_KEY": api_key,
        "TWITTER_API_SECRET": api_secret,
        "TWITTER_ACCESS_TOKEN": access_token,
        "TWITTER_ACCESS_TOKEN_SECRET": access_secret
    }.items() if not v]
    if missing:
        print(f"HATA: Eksik secret(lar): {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    oauth = OAuth1Session(
        api_key,
        client_secret=api_secret,
        resource_owner_key=access_token,
        resource_owner_secret=access_secret
    )

    resp = oauth.post(POST_TWEET_ENDPOINT, json={"text": tweet_text})
    if resp.status_code >= 400:
        print("X API Hatası:", resp.status_code, resp.text, file=sys.stderr)
        sys.exit(2)

    data = resp.json()
    tweet_id = (data or {}).get("data", {}).get("id")
    print(f"Başarılı ✅ Tweet ID: {tweet_id}")
    print(f"İçerik:\n{tweet_text}")

# ------------------ main ------------------
def main():
    # Google ve Twitter trendlerini çek
    google5 = get_google_trends_tr(limit=5)

    # OAuth1 secret'larını al (Twitter trendleri için de kullanacağız)
    api_key = os.environ.get("TWITTER_API_KEY")
    api_secret = os.environ.get("TWITTER_API_SECRET")
    access_token = os.environ.get("TWITTER_ACCESS_TOKEN")
    access_secret = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET")

    twitter5 = []
    if all([api_key, api_secret, access_token, access_secret]):
        twitter5 = get_twitter_trends_tr_oauth1(api_key, api_secret, access_token, access_secret, limit=5)
    else:
        print("[WARN] OAuth1 env eksik; Twitter trendleri atlanacak.", file=sys.stderr)

    if not google5 and not twitter5:
        fallback = f"Deneme tweeti — {istanbul_now_iso()}"
        print("[WARN] Hiç trend çekilemedi, fallback metin kullanılacak.", file=sys.stderr)
        post_tweet_oauth1(fallback)
        return

    tweet_text = build_trend_tweet(google5, twitter5)
    post_tweet_oauth1(tweet_text)

if __name__ == "__main__":
    main()
