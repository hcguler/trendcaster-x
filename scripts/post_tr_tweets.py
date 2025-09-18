"# scripts/post_image.py
import os
import sys
from datetime import datetime
from requests_oauthlib import OAuth1Session

from src.common import tz_tr, slot_floor, select_title, build_caption, make_image

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

POST_TWEET_ENDPOINT = "https://api.twitter.com/2/tweets"
MEDIA_UPLOAD_ENDPOINT = "https://upload.twitter.com/1.1/media/upload.json"
OUT_DIR = "out/daily"

def require_env(keys):
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

def upload_media(oauth: OAuth1Session, image_bytes: bytes) -> str:
    files = {"media": ("progress.png", image_bytes, "image/png")}
    resp = oauth.post(MEDIA_UPLOAD_ENDPOINT, files=files)
    if resp.status_code >= 400:
        print("X API Hatası (media/upload):", resp.status_code, resp.text, file=sys.stderr)
        sys.exit(2)
    media_id = resp.json().get("media_id_string")
    if not media_id:
        print("X API Hatası: media_id alınamadı", file=sys.stderr)
        sys.exit(2)
    return media_id

def post_tweet_with_media(oauth: OAuth1Session, text: str, media_id: str):
    payload = {"text": text, "media": {"media_ids": [media_id]}}
    resp = oauth.post(POST_TWEET_ENDPOINT, json=payload)
    if resp.status_code >= 400:
        print("X API Hatası (tweet):", resp.status_code, resp.text, file=sys.stderr)
        sys.exit(2)
    data = resp.json()
    tweet_id = (data or {}).get("data", {}).get("id")
    print(f"Başarılı ✅ Tweet ID: {tweet_id}")
    print(f"İçerik:\n{text}")

def main():
    # İstanbul saatini slot'a yuvarla (örn. 08:03 ise 08:00 görseli)
    now_tr = datetime.now(tz_tr())
    slot_dt = slot_floor(now_tr)

    date_str = slot_dt.strftime("%Y-%m-%d")
    file_name = f"{date_str}_{slot_dt.hour:02d}00.png"
    path = os.path.join(OUT_DIR, file_name)

    # Görsel yoksa (olağan dışı), yerinde üret
    if not os.path.exists(path):
        print(f"Uyarı: {path} bulunamadı, anlık üretim yapılacak.")
        title = select_title(slot_dt)
        image_bytes = make_image(slot_dt, title)
    else:
        with open(path, "rb") as f:
            image_bytes = f.read()
        title = select_title(slot_dt)

    caption = build_caption(slot_dt, title)

    oauth = oauth1_session_from_env()
    media_id = upload_media(oauth, image_bytes)
    post_tweet_with_media(oauth, caption, media_id)

if __name__ == "__main__":
    main()"  bu formatta yapmanı istiyorum çok fazla environment yonettirme bana
