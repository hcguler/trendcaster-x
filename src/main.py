import os
import sys
from datetime import datetime, timezone, timedelta
from typing import List
from requests_oauthlib import OAuth1Session

# ---- read-only fetch deps
try:
    from pytrends.request import TrendReq
    import httpx
except Exception as e:
    print("Bağımlılık hatası:", e, file=sys.stderr)
    print("requirements.txt içine 'pytrends' ve 'httpx' eklediğinden emin ol.", file=sys.stderr)
    sys.exit(1)

POST_TWEET_ENDPOINT = "https://api.twitter.com/2/tweets"
TR_WOEID = 23424969  # Türkiye (Twitter Trends v1.1)

# ------------------ helpers ------------------
def istanbul_now_iso():
    tz_tr = timezone(timedelta(hours=3))
    return datetime.now(tz_tr).isoformat(timespec="seconds")

def get_google_trends_tr(limit: int = 5) -> List[str]:
    try:
        pytrends = TrendReq(hl="tr-TR", tz=180)
        df = pytrends.trending_searches(pn="turkey")
        topics = df[0].tolist()
        return [t for t in topics if isinstance(t, str)][:limit]
    except Exception as e:
        print(f"[WARN] Google Trends alınamadı: {e}", file=sys.stderr)
        return []

def get_twitter_trends_tr(bearer_token: str, limit: int = 5) -> List[str]:
    if not bearer_token:
        print("[INFO] X_BEARER_TOKEN yok, Twitter trendleri atlanacak.", file=sys.stderr)
        return []
    url = f"https://api.twitter.com/1.1/trends/place.json?id={TR_WOEID}"
    headers = {"Authorization": f"Bearer {bearer_token}"}
    try:
        with httpx.Client(timeout=30) as client:
            r = client.get(url, headers=headers)
            r.raise_for_status()
            data = r.json()
            trends = data[0].get("trends", []) if data else []
            names = [t.get("name") for t in trends if isinstance(t.get("name"), str)]
            # Hashtaglerin başındaki # kalsın, ama None'ları filtrele
            return names[:limit]
    except Exception as e:
        print(f"[WARN] Twitter trends (v1.1) alınamadı: {e}", file=sys.stderr)
        return []

def build_trend_tweet(google_topics: List[str], twitter_topics: List[str]) -> str:
    # Boşlukları temizle, yinelenenleri azalt
    g = [t.strip() for t in google_topics if t and t.strip()]
    t = [x.strip() for x in twitter_topics if x and x.strip()]

    header = "🇹🇷 Türkiye Trendleri"
    g_line = "🔎 Google: " + (", ".join(g) if g else "—")
    t_line = "🐦 Twitter: " + (", ".join(t) if t else "—")

    text = f"{header}\n{g_line}\n{t_line}"
    # 280 sınırı: kelime ortasında kesme yerine nazik kırpma
    if len(text) > 280:
        # sonda üç nokta payı bırak
        text = text[:277].rstrip()
        text += "..."
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
        # Yaygın sorunlar:
        # 403 oauth1-permissions -> App perms Read/Write değil ya da access token eski
        # 401 -> anahtarlar hatalı
        sys.exit(2)

    data = resp.json()
    tweet_id = (data or {}).get("data", {}).get("id")
    print(f"Başarılı ✅ Tweet ID: {tweet_id}")
    print(f"İçerik:\n{tweet_text}")

# ------------------ main ------------------
def main():
    # 1) Trendleri topla
    google5 = get_google_trends_tr(limit=5)
    twitter5 = get_twitter_trends_tr(os.environ.get("X_BEARER_TOKEN"), limit=5)

    if not google5 and not twitter5:
        # Yine de bir şey atsın istersek timestamp'li fallback
        fallback = f"Deneme tweeti — {istanbul_now_iso()}"
        print("[WARN] Hiç trend çekilemedi, fallback metin kullanılacak.", file=sys.stderr)
        post_tweet_oauth1(fallback)
        return

    # 2) Tweet metnini hazırla
    tweet_text = build_trend_tweet(google5, twitter5)

    # 3) Gönder
    post_tweet_oauth1(tweet_text)

if __name__ == "__main__":
    main()
