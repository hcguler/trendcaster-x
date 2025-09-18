#!/usr/bin/env python3
import os
import json
import time
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

import requests
from requests_oauthlib import OAuth1
from openai import OpenAI
# OpenAI v1 exceptions
from openai import APIError, RateLimitError, APIConnectionError, APITimeoutError

# -------------------- Config --------------------
TW_POST_ENDPOINT = "https://api.twitter.com/1.1/statuses/update.json"

OWNER_HANDLE = os.getenv("OWNER_HANDLE", "")  # İsteğe bağlı footer/cta için
MODEL = os.getenv("MODEL", "gpt-4o-mini")
POST_TO_TWITTER = (os.getenv("POST_TO_TWITTER", "false").lower() == "true")

# Kota biterse ne yapalım? 'skip' | 'template' | 'fail'
ON_QUOTA_EXCEEDED = os.getenv("ON_QUOTA_EXCEEDED", "skip").lower()

# Retry ayarları
MAX_RETRIES = int(os.getenv("OAI_MAX_RETRIES", "4"))
BASE_DELAY = float(os.getenv("OAI_BASE_DELAY", "6.0"))  # saniye

# Türkiye UTC+3
TR_TZ = timezone(timedelta(hours=3))


def require_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return v


def get_oai_client() -> OpenAI:
    require_env("OPENAI_API_KEY")
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


def call_openai_with_retry(client: OpenAI, messages: List[Dict[str, Any]], model: str) -> Dict[str, Any]:
    """OpenAI chat.completions.create için retry + backoff + hata haritalama."""
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
            content = resp.choices[0].message.content
            return json.loads(content)
        except RateLimitError as e:
            last_err = e
            # Kota veya rate limit
            msg = str(getattr(e, "message", e))
            print(f"[WARN] OpenAI RateLimitError (attempt {attempt}/{MAX_RETRIES}): {msg}")
        except (APIConnectionError, APITimeoutError) as e:
            last_err = e
            print(f"[WARN] OpenAI bağlantı/timeout (attempt {attempt}/{MAX_RETRIES}): {e}")
        except APIError as e:
            last_err = e
            print(f"[WARN] OpenAI APIError (attempt {attempt}/{MAX_RETRIES}): {e}")
        except Exception as e:
            last_err = e
            print(f"[WARN] OpenAI beklenmeyen hata (attempt {attempt}/{MAX_RETRIES}): {e}")

        # backoff
        sleep_s = BASE_DELAY * (2 ** (attempt - 1))
        time.sleep(sleep_s)

    # Tüm denemeler bitti
    if isinstance(last_err, RateLimitError):
        # Kota özel akış
        if ON_QUOTA_EXCEEDED == "skip":
            print("[INFO] insufficient_quota: skip modunda—tweet atılmayacak, job başarıyla sonlanacak.")
            return {"topics": [], "tweets": [], "skip_due_to_quota": True}
        elif ON_QUOTA_EXCEEDED == "template":
            print("[INFO] insufficient_quota: template modunda—nötr bir şablon thread üretilecek.")
            return {"topics": _fallback_topics(), "tweets": _fallback_tweets(), "fallback_template": True}
        else:
            print("[ERROR] insufficient_quota: fail modunda—hata yükseltilecek.")
            raise last_err
    # Kota dışı sürekli hata
    raise last_err


def _fallback_topics() -> List[str]:
    """Model olmadan riskli iddialara girmeden genel başlık şablonları (zararsız)."""
    return [
        "Ekonomi ve piyasalar",
        "Eğitim ve sınav gündemi",
        "Sağlık ve yaşam",
        "Spor ve transfer haberleri",
        "Teknoloji ve dijital trendler",
        "Kültür-sanat ve etkinlikler",
        "Ulaşım ve şehir yaşamı",
        "Hava durumu ve afet bilgilendirmeleri",
        "İş dünyası ve girişimler",
        "Gündelik yaşam pratikleri"
    ]


def _fallback_tweets() -> List[str]:
    """Model/fresh veri yokken kullanılacak nötr/zararsız 4 tweetlik özet."""
    return [
        "Günün öne çıkan başlıkları ekonomi, eğitim, sağlık ve teknoloji etrafında yoğunlaşıyor. Gelişmeleri sade ve doğrulanabilir bilgilerle takip etmek, bilgi kirliliğinden kaçınmanın en güvenli yolu.",
        "Piyasalardaki dalgalanmalara karşı uzun vadeli perspektif ve risk yönetimi öne çıkıyor. Eğitim ve sınav gündeminde planlı çalışma, kaynak doğrulama ve resmi duyuruları izlemek kritik.",
        "Sağlık tarafında mevsimsel konular ve toplum sağlığı önerileri öne çıkıyor. Teknolojide yapay zekâ, siber güvenlik ve dijital güvenlik pratikleri gündemde.",
        "Spor, kültür-sanat ve şehir yaşamında etkinlik yoğunluğu dikkat çekiyor. Güncel ve doğru bilgi için resmi kanalları izlemek en sağlıklı yaklaşım."
    ]


def generate_tr_trend_tweets(client: OpenAI, now_tr: datetime) -> Dict[str, Any]:
    messages = build_prompt(now_tr)
    data = call_openai_with_retry(client, messages, MODEL)

    # skip/template sinyalleri geldiyse direkt dön
    if data.get("skip_due_to_quota") or data.get("fallback_template"):
        return data

    topics = data.get("topics", [])
    tweets = data.get("tweets", [])

    # Güvenlik: sınırlar
    topics = [str(t)[:120] for t in topics][:10]
    tweets = [str(t).strip()[:270] for t in tweets][:5]

    # En az 4 tweet garanti
    if len(tweets) < 4:
        extra = [f"Özet: {t[:230]}" for t in topics[len(tweets):len(tweets)+(4-len(tweets))]]
        tweets.extend(extra)

    return {"topics": topics, "tweets": tweets}


def post_tweet_oauth1(status: str, reply_to_id: str = None) -> Dict[str, Any]:
    api_key = require_env("TWITTER_API_KEY")
    api_secret = require_env("TWITTER_API_SECRET")
    access_token = require_env("TWITTER_ACCESS_TOKEN")
    access_secret = require_env("TWITTER_ACCESS_TOKEN_SECRET")

    auth = OAuth1(api_key, api_secret, access_token, access_secret)
    payload = {"status": status}
    if reply_to_id:
        payload["in_reply_to_status_id"] = reply_to_id
        payload["auto_populate_reply_metadata"] = "true"

    r = requests.post(TW_POST_ENDPOINT, data=payload, a
