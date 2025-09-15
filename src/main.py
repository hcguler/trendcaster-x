import os, json, yaml, hashlib
from typing import List
from tenacity import retry, stop_after_attempt, wait_exponential
from pytrends.request import TrendReq
import httpx

# ---------- Config ----------
RULES_PATH = "config/rules.yml"
POST_ENDPOINT = "https://api.twitter.com/2/tweets"  # X/Twitter v2
MAX_LEN = 280

# ---------- Helpers ----------
def load_rules():
    with open(RULES_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def fetch_trends(country="TR", limit=10) -> List[str]:
    # pytrends bölge kodları 'pn' için: 'turkey', 'united_states' vb.
    pn_map = {
        "TR": "turkey",
        "US": "united_states",
        "GB": "united_kingdom",
        "DE": "germany",
        "FR": "france",
        "IT": "italy"
    }
    pn = pn_map.get(country.upper(), "turkey")
    pytrends = TrendReq(hl='tr-TR', tz=180)
    df = pytrends.trending_searches(pn=pn)
    topics = df[0].tolist()
    return topics[:limit]

def build_prompt(trends: List[str], rules: dict) -> str:
    tone = rules.get("style", {}).get("tone", "bilgilendirici")
    max_hashtags = rules.get("style", {}).get("max_hashtags", 2)
    tweet_count = rules.get("tweet_count", 4)
    banned_words = rules.get("banned_words", [])
    return f"""
SENİN GÖREVİN:
- Aşağıdaki trend başlıklarına dayanarak {tweet_count} adet Türkçe tweet üret.
- Her tweet bağımsız olsun (tekrar yok), {MAX_LEN} karakteri geçme, spam hashtag kullanma.
- Ton: {tone}. Maks. hashtag: {max_hashtags}. Mention yok. Link varsa tek link.
- Şu kelimeler/asla geçmesin: {banned_words}.

Girdi Trendler: {trends}

Çıktı formatı (JSON):
{{"tweets":[{{"text":"..."}},{{"text":"..."}},{{"text":"..."}},{{"text":"..."}}]}}
""".strip()

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def call_llm(prompt: str) -> List[str]:
    api_key = os.environ["OPENAI_API_KEY"]
    headers = {"Authorization": f"Bearer {api_key}"}
    body = {
        "model": "gpt-4.1-mini",
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}]
    }
    with httpx.Client(timeout=60) as client:
        r = client.post("https://api.openai.com/v1/chat/completions", headers=headers, json=body)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        data = json.loads(content)
        tweets = [t["text"].strip() for t in data["tweets"]]
        return tweets

def sanitize_and_validate(tweets: List[str], rules: dict) -> List[str]:
    banned = set(w.lower() for w in rules.get("banned_words", []))
    seen_hashes = set()
    clean = []
    for t in tweets:
        tt = " ".join(t.split())  # whitespace normalize
        if len(tt) > MAX_LEN:
            continue
        if any(b in tt.lower() for b in banned):
            continue
        h = hashlib.sha256(tt.encode("utf-8")).hexdigest()[:16]
        if h in seen_hashes:
            continue
        seen_hashes.add(h)
        clean.append(tt)
    want = rules.get("tweet_count", 4)
    return clean[:want]

def post_to_x(tweets: List[str]):
    # DRY_RUN = true ise sadece loglar.
    dry = os.environ.get("DRY_RUN", "true").lower() == "true"
    token = os.environ.get("X_BEARER_TOKEN")
    if not token and not dry:
        raise RuntimeError("X_BEARER_TOKEN yok. Secrets'a ekleyin ya da DRY_RUN=true bırakın.")

    for t in tweets:
        if dry:
            print("[DRY_RUN] Tweet:", t)
        else:
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    POST_ENDPOINT,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json"
                    },
                    json={"text": t}
                )
                if resp.status_code >= 300:
                    print("X API error:", resp.status_code, resp.text)
                else:
                    print("Yayınlandı:", resp.json())

def main():
    rules = load_rules()
    trends = fetch_trends(rules.get("country", "TR"), limit=10)
    prompt = build_prompt(trends, rules)
    raw_tweets = call_llm(prompt)
    tweets = sanitize_and_validate(raw_tweets, rules)
    if not tweets:
        print("Uyarı: Geçerli tweet üretilmedi.")
        return
    post_to_x(tweets)

if __name__ == "__main__":
    main()
