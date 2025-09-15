import os
import sys
import json
import re
from datetime import datetime, timezone, timedelta
from typing import List
from requests_oauthlib import OAuth1Session
import httpx
from bs4 import BeautifulSoup

POST_TWEET_ENDPOINT = "https://api.twitter.com/2/tweets"
TR_WOEID = 23424969  # Bilgi amaçlı

# ------------------ helpers ------------------
def istanbul_now_iso() -> str:
    tz_tr = timezone(timedelta(hours=3))
    return datetime.now(tz_tr).isoformat(timespec="seconds")

# ---- GOOGLE TRENDS: dailytrends JSON (stabil) ----
def get_google_trends_tr(limit: int = 5) -> List[str]:
    """
    Google Daily Trends JSON:
    https://trends.google.com/trends/api/dailytrends?hl=tr-TR&geo=TR&ns=15
    Dönen body başında ")]}'," var; onu temizleyip JSON parse edilir.
    """
    url = "https://trends.google.com/trends/api/dailytrends"
    params = {"hl": "tr-TR", "geo": "TR", "ns": "15"}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        with httpx.Client(timeout=20) as client:
            r = client.get(url, params=params, headers=headers)
            r.raise_for_status()
            text = r.text.strip()
            # Güvenlik prefiksi kaldır
            if text.startswith(")]}',"):
                text = text[5:]
            data = json.loads(text)
            days = data.get("default", {}).get("trendingSearchesDays", [])
            if not days:
                return []
            searches = days[0].get("trendingSearches", [])
            topics = []
            for s in searches:
                q = s.get("title", {}).get("query")
                if isinstance(q, str) and q.strip():
                    topics.append(q.strip())
                if len(topics) >= limit:
                    break
            return topics
    except Exception as e:
        print(f"[WARN] Google Trends (dailytrends) alınamadı: {e}", file=sys.stderr)
        return []

# ---- TWITTER TRENDS FALLBACK: Trends24 parsing ----
def get_twitter_trends_tr_fallback(limit: int = 5) -> List[str]:
    """
    Trends24 Turkey sayfasını parse eder:
    https://trends24.in/turkey/
    class="trend-name" linklerinden ilk 5'i alır.
    """
    url = "https://trends24.in/turkey/"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        with httpx.Client(timeout=20) as client:
            r = client.get(url, headers=headers)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            # En üst karttaki (günün/hazır bloğun) trendleri al
            # Çoğu sayfada ".trend-card .trend-card__list li a.trend-name"
            items = soup.select(".trend-card .trend-card__list li a.trend-name")
            names = []
            for a in items:
                name = (a.get_text() or "").strip()
                if not name:
                    continue
                # Twitter trendlerinde bazen çok uzun metin olabilir; normalize et
                name = re.sub(r"\s+", " ", name)
                names.append(name)
                if len(names) >= limit:
                    break
            return names
    except Exception as e:
        print(f"[WARN] Twitter trends (Trends24) alınamadı: {e}", file=sys.stderr)
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
    google5 = get_google_trends_tr(limit=5)

    # X v1.1 trends/place çoğu planda kısıtlı olduğu için fallback:
    twitter5 = get_twitter_trends_tr_fallback(limit=5)

    if not google5 and not twitter5:
        fallback = f"Deneme tweeti — {istanbul_now_iso()}"
        print("[WARN] Hiç trend çekilemedi, fallback metin kullanılacak.", file=sys.stderr)
        post_tweet_oauth1(fallback)
        return

    tweet_text = build_trend_tweet(google5, twitter5)
    post_tweet_oauth1(tweet_text)

if __name__ == "__main__":
    main()
