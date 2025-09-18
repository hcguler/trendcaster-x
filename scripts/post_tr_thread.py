#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import time
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

import requests
from requests_oauthlib import OAuth1Session
from openai import OpenAI
from openai import RateLimitError, APIError, APIConnectionError, APITimeoutError

# -------------------------------------------------------------------
# Sabitler (minimum env ile çalışır)
# -------------------------------------------------------------------
POST_TWEET_ENDPOINT = "https://api.twitter.com/2/tweets"  # v2 tweet
TR_TZ = timezone(timedelta(hours=3))
MAX_RETRIES = 3
BASE_DELAY = 5.0  # sn

# -------------------------------------------------------------------
# Yardımcılar
# -------------------------------------------------------------------
def require_env(keys: List[str]) -> Dict[str, str]:
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

def get_openai_client() -> OpenAI:
    require_env(["OPENAI_API_KEY"])
    return OpenAI()

def build_prompt(now_tr: datetime) -> List[Dict[str, Any]]:
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
2) Bu gündemi 4–5 kısa tweet ile özetle. Her tweet bağımsız anlamlı olsun; devam hissi verebilir.
3) Tweet metinleri 270 karakteri geçmesin. Net ve bilgi odaklı olsun.

SADECE JSON DÖN:
{{
  "topics": ["...", "...", "...", "...", "...", "...", "...", "...", "...", "..."],
  "tweets": ["tweet-1", "tweet-2", "tweet-3", "tweet-4", "tweet-5"]
}}

Kurallar:
- Link verme.
- Gereksiz emoji/etiket kullanma; en fazla 1-2 uygun hashtag.
- Clickbait yapma.
"""
    return [
        {"role": "system", "content": sys_msg},
        {"role": "user", "content": user_msg},
    ]

def call_openai_with_retry(client: OpenAI, messages: List[Dict[str, Any]], model: str) -> Dict[str, Any]:
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.7,
                response_format={"type": "json_object"},
                timeout=60,
            )
            return json.loads(resp.choices[0].message.content)
        except (RateLimitError, APIError, APIConnectionError, APITimeoutError, Exception) as e:
            last_err = e
            print(f"[WARN] OpenAI hata (attempt {attempt}/{MAX_RETRIES}): {e}", file=sys.stderr)
            if attempt < MAX_RETRIES:
                time.sleep(BASE_DELAY * (2 ** (attempt - 1)))
    raise last_err

def fetch_topics_and_tweets(now_tr: datetime) -> Dict[str, Any]:
    client = get_openai_client()
    model = os.environ.get("MODEL", "gpt-4o-mini")
    data = call_openai_with_retry(client, build_prompt(now_tr), model)

    topics = [str(t)[:120] for t in data.get("topics", [])][:10]
    tweets = [str(t).strip()[:270] for t in data.get("tweets", [])][:5]
    if len(tweets) < 4:
        # 4'ün altı kalırsa başlıklardan kısa özetlerle tamamla
        for t in topics:
            if len(tweets) >= 4:
                break
            tweets.append(f"Özet: {t[:230]}")
    return {"topics": topics, "tweets": tweets}

def post_tweet(oauth: OAuth1Session, text: str, reply_to_id: str | None = None) -> str:
    payload = {"text": text}
    if reply_to_id:
        payload["reply"] = {"in_reply_to_tweet_id": reply_to_id}
    resp = oauth.post(POST_TWEET_ENDPOINT, json=payload)
    if resp.status_code >= 400:
        print("X API Hatası (tweet):", resp.status_code, resp.text, file=sys.stderr)
        sys.exit(2)
    return (resp.json() or {}).get("data", {}).get("id")

# -------------------------------------------------------------------
# main
# -------------------------------------------------------------------
def main():
    now_tr = datetime.now(tz=TR_TZ)
    bundle = fetch_topics_and_tweets(now_tr)

    topics = bundle["topics"]
    tweets = bundle["tweets"]

    # Giriş + 4 tweet = toplam 5 tut
    intro = f"Türkiye gündemi ({now_tr.strftime('%d %B %Y')}):"
    thread = [intro] + tweets
    thread = thread[:5]

    oauth = oauth1_session_from_env()

    # Thread postla
    first_id = None
    for i, text in enumerate(thread):
        tid = post_tweet(oauth, text, reply_to_id=first_id if i > 0 else None)
        if i == 0:
            first_id = tid
        time.sleep(2)

    print("Başarılı ✅ İlk tweet ID:", first_id)
    print("Konu başlıkları:", "; ".join(topics))

if __name__ == "__main__":
    main()
