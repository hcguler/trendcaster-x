import os
import sys
from datetime import datetime, timezone, timedelta
import httpx

POST_ENDPOINT = "https://api.twitter.com/2/tweets"

def istanbul_now_iso():
    # Europe/Istanbul = UTC+3 (sabit offset; DST'e gerek duyulmayan pratik bir timestamp)
    tz_tr = timezone(timedelta(hours=3))
    return datetime.now(tz_tr).isoformat(timespec="seconds")

def main():
    token = os.environ.get("X_BEARER_TOKEN")
    if not token:
        print("HATA: X_BEARER_TOKEN tanımlı değil. GitHub Actions Secrets içine ekleyin.", file=sys.stderr)
        sys.exit(1)

    # İstersen Actions'ta TWEET_TEXT secret/var ekleyip içeriği buradan yönetebilirsin.
    tweet_text = os.environ.get("TWEET_TEXT") or f"Deneme tweeti (otomatik) — {istanbul_now_iso()}"

    # X API v2 ile tek tweet post
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {"text": tweet_text}

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(POST_ENDPOINT, headers=headers, json=payload)
            if resp.status_code >= 400:
                print("X API Hatası:", resp.status_code, resp.text, file=sys.stderr)
                # 401/403: yazma izni yok ya da token yanlış
                # 429: rate limit
                # 4xx/5xx: geçici hata için yeniden deneme mantığı eklemek istersen tenacity kullanabilirsin.
                sys.exit(2)
            data = resp.json()
            tweet_id = data.get("data", {}).get("id")
            print(f"Başarılı ✅ Tweet ID: {tweet_id}")
            print(f"İçerik: {tweet_text}")
    except httpx.RequestError as e:
        print(f"İstek hatası: {e!r}", file=sys.stderr)
        sys.exit(3)

if __name__ == "__main__":
    main()
