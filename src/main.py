import os
import sys
from datetime import datetime, timezone, timedelta
from requests_oauthlib import OAuth1Session

POST_ENDPOINT = "https://api.twitter.com/2/tweets"

def istanbul_now_iso():
    tz_tr = timezone(timedelta(hours=3))
    return datetime.now(tz_tr).isoformat(timespec="seconds")

def main():
    api_key = os.environ.get("TWITTER_API_KEY")
    api_secret = os.environ.get("TWITTER_API_SECRET")
    access_token = os.environ.get("TWITTER_ACCESS_TOKEN")
    access_secret = os.environ.get("TWITTER_ACCESS_TOKEN_SECRET")

    missing = [k for k,v in {
        "TWITTER_API_KEY": api_key,
        "TWITTER_API_SECRET": api_secret,
        "TWITTER_ACCESS_TOKEN": access_token,
        "TWITTER_ACCESS_TOKEN_SECRET": access_secret
    }.items() if not v]
    if missing:
        print(f"HATA: Eksik secret(lar): {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    tweet_text = os.environ.get("TWEET_TEXT") or f"Deneme tweeti (OAuth1) — {istanbul_now_iso()}"

    oauth = OAuth1Session(api_key, client_secret=api_secret,
                          resource_owner_key=access_token,
                          resource_owner_secret=access_secret)

    resp = oauth.post(POST_ENDPOINT, json={"text": tweet_text})
    if resp.status_code >= 400:
        print("X API Hatası:", resp.status_code, resp.text, file=sys.stderr)
        sys.exit(2)

    data = resp.json()
    tweet_id = data.get("data", {}).get("id")
    print(f"Başarılı ✅ Tweet ID: {tweet_id}")
    print(f"İçerik: {tweet_text}")

if __name__ == "__main__":
    main()
