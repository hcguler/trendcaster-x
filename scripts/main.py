# main.py
# -*- coding: utf-8 -*-
"""
BIST Analiz Botu — OUT/ JSON'dan Görsel + (Opsiyonel) Tweet
- Kaynak: out/*.json (senin "bist_analiz_YYYY-MM-DD.json" şeman)
- 1280x1280 görselde 3 tablo (Günlük / Aylık / Yıllık)
- Tweet için GEMINI YOK: 20 hazır şablondan rastgele seç, dinamik Hisse + % yerleştir
CLI:
  python scripts/main.py --dry-run --out /tmp/bist.png
  python scripts/main.py --post
  python scripts/main.py --post --json out/bist_analiz_2025-10-12.json
"""

import os
import io
import re
import sys
import json
import glob
import argparse
import random
import traceback
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Any

from requests_oauthlib import OAuth1Session
from PIL import Image, ImageDraw, ImageFont

# -------------------------
# CONFIG
# -------------------------
OWNER_HANDLE = os.environ.get("OWNER_HANDLE", "@durbirbakiyim")
TR_TZ = timezone(timedelta(hours=3), name="Europe/Istanbul")

POST_TWEET_ENDPOINT = "https://api.twitter.com/2/tweets"
MEDIA_UPLOAD_ENDPOINT = "https://upload.twitter.com/1.1/media/upload.json"

CANVAS_W, CANVAS_H = 1280, 1280
MARGIN_X, MARGIN_Y = 60, 90
TABLE_TITLE_H = 36
ROW_H = 42
HEADER_H = 64
FOOTER_H = 90
TABLE_GAP_Y = 28

_TR_MONTHS = {1:"Ocak",2:"Şubat",3:"Mart",4:"Nisan",5:"Mayıs",6:"Haziran",
              7:"Temmuz",8:"Ağustos",9:"Eylül",10:"Ekim",11:"Kasım",12:"Aralık"}
_TR_WD = {0:"Pazartesi",1:"Salı",2:"Çarşamba",3:"Perşembe",4:"Cuma",5:"Cumartesi",6:"Pazar"}

BASE_HASHTAGS = [
    "#Borsa", "#BIST", "#BIST100", "#Hisse", "#Yatırım",
    "#Finans", "#Borsaİstanbul", "#Piyasa", "#GününHisseleri", "#Portföy"
]

# -------------------------
# Yardımcılar
# -------------------------
def now_tr() -> datetime:
    return datetime.now(TR_TZ)

def tr_month_name(m: int) -> str: return _TR_MONTHS.get(m, str(m))
def tr_wd_name(w: int) -> str: return _TR_WD.get(w, "")

def float_to_pct_str(v: float, decimals: int=2) -> str:
    if v is None: return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v:.{decimals}f}%"

def require_env(keys: List[str]) -> dict:
    envs = {k: os.environ.get(k) for k in keys}
    missing = [k for k, v in envs.items() if not v]
    if missing:
        print(f"HATA: Eksik secret(lar): {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    return envs

def oauth1_session_from_env() -> OAuth1Session:
    envs = require_env(["TWITTER_API_KEY","TWITTER_API_SECRET","TWITTER_ACCESS_TOKEN","TWITTER_ACCESS_TOKEN_SECRET"])
    return OAuth1Session(
        envs["TWITTER_API_KEY"],
        client_secret=envs["TWITTER_API_SECRET"],
        resource_owner_key=envs["TWITTER_ACCESS_TOKEN"],
        resource_owner_secret=envs["TWITTER_ACCESS_TOKEN_SECRET"],
    )

def hashtag_from_ticker(sym: str) -> str:
    return f"#{sym.split('.')[0]}"

def display_ticker(sym: str) -> str:
    return sym.split(".")[0] if sym else sym

# -------------------------
# OUT/ JSON OKU & DÖNÜŞTÜR (bist_analiz_* şeması)
# -------------------------
STOCK = Dict[str, Any]

def find_latest_json(out_dir: str="out") -> Optional[str]:
    files = glob.glob(os.path.join(out_dir, "*.json"))
    if not files: return None
    def key_fn(p):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(p))
        if m:
            try: return datetime.fromisoformat(m.group(1))
            except: pass
        return datetime.fromtimestamp(os.path.getmtime(p))
    files.sort(key=key_fn, reverse=True)
    return files[0]

def _parse_pct_str(s: Any) -> Optional[float]:
    """ '12.14%' -> 12.14 ; '-0.83%' -> -0.83 ; 0.5 -> 0.5 """
    if s is None: return None
    if isinstance(s, (int, float)): return float(s)
    try:
        txt = str(s).strip()
        if txt.endswith("%"): txt = txt[:-1]
        txt = txt.replace(",", ".")
        return float(txt)
    except Exception:
        return None

def load_out_json(path: Optional[str]=None) -> Dict[str, Any]:
    if path is None:
        path = find_latest_json()
        if not path:
            raise FileNotFoundError("out/ altında JSON bulunamadı.")
    with open(path, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        raise ValueError("JSON beklenen şemada değil (dict olmalı).")
    return {"_path": path, "data": payload}

def transform_payload_to_stocks(payload: Dict[str, Any]) -> List[STOCK]:
    raw: Dict[str, Any] = payload["data"]
    out: List[STOCK] = []
    ts = now_tr().timestamp()

    for sym, row in raw.items():
        perf = (row or {}).get("kazandirma_oranlari_yuzde", {}) or {}
        out.append({
            "ticker": sym,
            "name": sym,
            "last_price": row.get("bugun_kapanis"),
            "pct_1d": _parse_pct_str(perf.get("gunluk")),
            "pct_30d": _parse_pct_str(perf.get("aylik_30_gun")),
            "pct_3m": _parse_pct_str(perf.get("3_aylik_90_gun")),
            "pct_6m": _parse_pct_str(perf.get("6_aylik_180_gun")),
            "pct_360d": _parse_pct_str(perf.get("12_aylik_360_gun")),
            "last_updated": ts,
        })
    return out

# -------------------------
# ŞABLONLAR (20 farklı)
# -------------------------
TEMPLATES = [
    "🚨 Günün yıldızı: {TOP1} ({TOP1_PCT}). İlk üç: {TOP1}, {TOP2}, {TOP3}. Yarın ivme sürer mi?",
    "Bugün öne çıkanlar: {TOP1} {TOP1_PCT}, {TOP2} {TOP2_PCT}, {TOP3} {TOP3_PCT}. Takipte misiniz?",
    "{COUNT} hissenin içinde en parlak performans {TOP1} — {TOP1_PCT}. Listeyi {TOP2} ve {TOP3} izledi. Sürer mi?",
    "Piyasanın kazandıranları: 1) {TOP1} ({TOP1_PCT}) 2) {TOP2} ({TOP2_PCT}) 3) {TOP3} ({TOP3_PCT}). Sizce yarın kim öne çıkar?",
    "Günün sürprizi {TOP1} — {TOP1_PCT}. {TOP2} ve {TOP3} de güçlü kapattı. Ralli devam eder mi?",
    "Kapanışta pozitif tablo: {TOP1} {TOP1_PCT}, {TOP2} {TOP2_PCT}, {TOP3} {TOP3_PCT}. Portföyünüz hazır mı?",
    "Bugün en çok konuşulan hisse: {TOP1} ({TOP1_PCT}). Onu {TOP2} ve {TOP3} takip etti. Yarın stratejiniz ne?",
    "Momentum güçlü: {TOP1} {TOP1_PCT}. Ardından {TOP2} ve {TOP3}. Bu ivme kalıcı mı?",
    "En çok kazandıran: {TOP1} ({TOP1_PCT}). İlk üçte {TOP2} ve {TOP3} var. Sizce düzeltme gelir mi?",
    "Zirve yarışında {TOP1} {TOP1_PCT}. Peşinde {TOP2} ve {TOP3}. Hangi seviyeler kritik?",
    "{TOP1} günün lideri: {TOP1_PCT}. {TOP2} ve {TOP3} ile tablo yeşil. Yarın planınız ne?",
    "Pozitif kapanış: {TOP1} ({TOP1_PCT}), {TOP2} ({TOP2_PCT}), {TOP3} ({TOP3_PCT}). Risk yönetiminiz hazır mı?",
    "Trend yakalandı mı? {TOP1} {TOP1_PCT}. {TOP2} ve {TOP3} de destekliyor. İzlemeye değer mi?",
    "Günün üçlüsü: {TOP1} {TOP1_PCT}, {TOP2} {TOP2_PCT}, {TOP3} {TOP3_PCT}. Hedefler güncellenmeli mi?",
    "Likidite nereye aktı? {TOP1} önde {TOP1_PCT}. {TOP2} ve {TOP3} de güçlü. Yarına taşımalı mı?",
    "Getiri listesinde zirve: {TOP1} ({TOP1_PCT}). Takipçiler: {TOP2}, {TOP3}. Strateji: bekle-gör mü?",
    "{COUNT} hisselik evrende lider {TOP1} — {TOP1_PCT}. {TOP2}/{TOP3} yakın. Kırılım gelir mi?",
    "Piyasa nabzı: {TOP1} {TOP1_PCT}. Arkasında {TOP2}, {TOP3}. Destek/dirençler çalışır mı?",
    "Gün sonu özet: {TOP1} ({TOP1_PCT}) ilk sırada. {TOP2}, {TOP3} listede. Yarın hikâye devam eder mi?",
    "Riski sevene gündem: {TOP1} {TOP1_PCT}. {TOP2} ve {TOP3} hızlandı. Sizce trend güçlenir mi?",
]

def build_context(stocks: List[STOCK]) -> Dict[str, Any]:
    top = sorted([s for s in stocks if s.get("pct_1d") is not None], key=lambda x: x["pct_1d"], reverse=True)[:3]
    if not top:
        return {"COUNT": len(stocks), "TOP1": "-", "TOP1_PCT": "-", "TOP2": "-", "TOP2_PCT": "-", "TOP3": "-", "TOP3_PCT": "-"}
    def sym(i): return display_ticker(top[i]["ticker"]) if i < len(top) else "-"
    def pct(i): return float_to_pct_str(top[i]["pct_1d"]) if i < len(top) else "-"
    return {
        "COUNT": len(stocks),
        "TOP1": sym(0), "TOP1_PCT": pct(0),
        "TOP2": sym(1), "TOP2_PCT": pct(1),
        "TOP3": sym(2), "TOP3_PCT": pct(2),
    }

def compose_tweet_from_templates(stocks: List[STOCK]) -> str:
    ctx = build_context(stocks)
    template = random.choice(TEMPLATES)
    body = template.format(**ctx).strip()

    # Hashtagler: baz havuzdan 3–5 + en fazla 4 ticker etiketi ('.IS' atılır)
    tags = BASE_HASHTAGS[:]
    random.shuffle(tags)
    base_pick = tags[:random.randint(3,5)]

    top = sorted([s for s in stocks if s.get("pct_1d") is not None], key=lambda x: x["pct_1d"], reverse=True)[:4]
    ticker_tags = [hashtag_from_ticker(s['ticker']) for s in top]

    all_tags = list(dict.fromkeys(base_pick + ticker_tags))
    random.shuffle(all_tags)

    template_full = "{body}\n\n{tags}\n{handle}"
    def attempt(tlist, ttags):
        tag_line = " ".join(ttags)
        return template_full.format(body=tlist, tags=tag_line, handle=OWNER_HANDLE).strip()

    msg = attempt(body, all_tags)
    if len(msg) <= 280: return msg
    msg = attempt(body, base_pick + ticker_tags[:2])
    if len(msg) <= 280: return msg
    msg = attempt(body, base_pick[:3])
    return msg if len(msg) <= 280 else (msg[:277] + "...")

# -------------------------
# RENDER
# -------------------------
def load_font(size: int, bold: bool=False):
    suffix = "-Bold" if bold else ""
    candidates = [
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{suffix}.ttf",
        f"/usr/share/fonts/truetype/liberation/LiberationSans{suffix}.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        if os.path.exists(p):
            try: return ImageFont.truetype(p, size=size)
            except: pass
    return ImageFont.load_default()

def render_table(draw: ImageDraw.ImageDraw, data: List[STOCK], start_y: int, table_title: str,
                 sort_key: str, limit: int, col_order: List[str], header_map: Dict[str,str],
                 title_font, header_font, data_font) -> int:
    W = CANVAS_W
    INNER_W = W - 2*MARGIN_X
    # İlgili döneme göre en çok kazandıran ilk 5 (None'lar sona)
    sorted_data = sorted(
        data,
        key=lambda x: (x.get(sort_key) is not None, x.get(sort_key) if x.get(sort_key) is not None else -1e9),
        reverse=True
    )[:5]  # her zaman 5

    COL_MAP = {"Hisse": 0.18*INNER_W, "P1": 0.164*INNER_W, "P2": 0.164*INNER_W, "P3": 0.164*INNER_W, "P4": 0.164*INNER_W, "P5": 0.164*INNER_W}

    draw.text((MARGIN_X, start_y), table_title, fill=(50,50,50), font=title_font)
    current_y = start_y + TABLE_TITLE_H + 6

    x = MARGIN_X
    labels = ["Hisse","P1","P2","P3","P4","P5"]
    header_labels = ["Hisse"] + [header_map.get(c, c) for c in col_order[1:]]
    for key, label in zip(labels, header_labels):
        w = COL_MAP[key]
        if key == "Hisse":
            draw.text((x, current_y), label, fill=(0,0,0), font=header_font, anchor="lt")
        else:
            draw.text((x + w - 4, current_y), label, fill=(0,0,0), font=header_font, anchor="rt")
        x += w

    current_y += HEADER_H - 28
    draw.line([MARGIN_X, current_y, W - MARGIN_X, current_y], fill=(185,185,185), width=1)
    current_y += 4

    for i, s in enumerate(sorted_data):
        x = MARGIN_X
        row_y = current_y + i*ROW_H
        # .IS'siz göster
        draw.text((x, row_y), display_ticker(s["ticker"]), fill=(20,20,20), font=data_font)
        x += COL_MAP["Hisse"]
        for data_key, col in zip(col_order[1:], ["P1","P2","P3","P4","P5"]):
            w = COL_MAP[col]
            v = s.get(data_key)
            if v is not None:
                pct_str = float_to_pct_str(float(v), 2)
                color = (0,128,0) if v >= 0 else (204,0,0)
                draw.text((x + w - 4, row_y), pct_str, fill=color, font=data_font, anchor="rt")
            x += w
        if i < len(sorted_data)-1:
            y_sep = row_y + ROW_H - 6
            draw.line([MARGIN_X, y_sep, W - MARGIN_X, y_sep], fill=(235,235,235), width=1)

    return current_y + len(sorted_data)*ROW_H + 12

def render_image(stock_data: List[STOCK], limit: int) -> bytes:
    W, H = CANVAS_W, CANVAS_H
    BG = (248,248,252)
    FRAME = (0,102,204)
    now = now_tr()

    # Kolon sabitleri: tüm tablolarda aynı sıra
    # "Günlük, Aylık, 3 Ay, 6 Ay, Yıllık"
    key_1d = "pct_1d"
    key_30d = "pct_30d"
    key_3m = "pct_3m"
    key_6m = "pct_6m"
    key_360d = "pct_360d"

    img = Image.new("RGB", (W,H), color=BG)
    draw = ImageDraw.Draw(img)

    header_font = load_font(44, bold=True)
    sub_header_font = load_font(28)
    table_title_font = load_font(28, bold=True)
    table_header_font = load_font(19, bold=True)
    table_data_font = load_font(22)
    foot_font = load_font(20)

    main_title = "DUR BİR BAKAYIM — BIST Analizi"
    draw.text((W//2, MARGIN_Y), main_title, fill=(20,30,40), font=header_font, anchor="mm")
    draw.text((W//2, MARGIN_Y + 44), f"({OWNER_HANDLE})", fill=(80,90,100), font=sub_header_font, anchor="mm")
    line_y = MARGIN_Y + 44 + 20
    draw.line([MARGIN_X, line_y, W - MARGIN_X, line_y], fill=FRAME, width=2)

    top_block_bottom = line_y + 12
    current_y = top_block_bottom + 16

    HEADER_MAP = {
        "ticker": "Hisse",
        key_1d:  "Günlük %",
        key_30d: "Aylık %",
        key_3m:  "3 Ay %",
        key_6m:  "6 Ay %",
        key_360d:"Yıllık %",
    }

    # Tüm tablolarda kolon sırası sabit:
    col_order_common = ["ticker", key_1d, key_30d, key_3m, key_6m, key_360d]

    # 1) GÜNLÜK – en çok kazandıran 5
    current_y = render_table(
        draw, stock_data, current_y,
        "GÜNLÜK: En Çok Kazandıranlar",
        key_1d, 5, col_order_common, HEADER_MAP,
        table_title_font, table_header_font, table_data_font
    )
    current_y += TABLE_GAP_Y

    # 2) AYLIK – en çok kazandıran 5
    current_y = render_table(
        draw, stock_data, current_y,
        "AYLIK: En Çok Kazandıranlar",
        key_30d, 5, col_order_common, HEADER_MAP,
        table_title_font, table_header_font, table_data_font
    )
    current_y += TABLE_GAP_Y

    # 3) YILLIK – en çok kazandıran 5
    current_y = render_table(
        draw, stock_data, current_y,
        "YILLIK: En Çok Kazandıranlar",
        key_360d, 5, col_order_common, HEADER_MAP,
        table_title_font, table_header_font, table_data_font
    )

    date_line = f"{now.day:02d} {tr_month_name(now.month)} {now.year}, {tr_wd_name(now.weekday())}"
    footer_text = f"Tarih: {date_line}"
    draw.text((W//2, H - 40), footer_text, fill=(80,90,100), font=foot_font, anchor="ms")
    draw.rectangle([20,20, W-20, H-20], outline=FRAME, width=5)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()

# -------------------------
# X/Twitter
# -------------------------
def upload_media(oauth: OAuth1Session, image_bytes: bytes) -> str:
    files = {"media": ("bist_analysis.png", image_bytes, "image/png")}
    resp = oauth.post(MEDIA_UPLOAD_ENDPOINT, files=files)
    resp.raise_for_status()
    mid = resp.json().get("media_id_string")
    if not mid: raise ValueError("X API: media_id alınamadı.")
    return mid

def post_tweet(oauth: OAuth1Session, text: str, media_id: str):
    payload = {"text": text, "media": {"media_ids": [media_id]}}
    resp = oauth.post(POST_TWEET_ENDPOINT, json=payload)
    if resp.status_code == 403:
        raise PermissionError("X API 403: Read/Write izinlerini kontrol edin.")
    resp.raise_for_status()
    tid = (resp.json() or {}).get("data", {}).get("id")
    print(f"✅ Tweet ID: {tid}")

# -------------------------
# CLI / MAIN
# -------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="BIST out/ JSON → Görsel + (opsiyonel) Tweet (Gemini yok)")
    p.add_argument("--post", action="store_true", help="Tweet at (X/Twitter).")
    p.add_argument("--dry-run", action="store_true", help="Post atma; görseli dosyaya kaydet ve metni konsola yaz.")
    p.add_argument("--json", type=str, default=None, help="Kullanılacak JSON yolu. Verilmezse out/ içindeki en yenisi.")
    p.add_argument("--limit", type=int, default=5, help="Her tablo için maksimum satır (sabit: 5).")
    p.add_argument("--out", type=str, default="bist_output.png", help="--dry-run çıktısı.")
    p.add_argument("--seed", type=int, default=None, help="Rastgele seçim için seed (tekrarlanabilirlik).")
    args = p.parse_args()
    if not args.post:
        args.dry_run = True
    if args.seed is not None:
        random.seed(args.seed)
    return args

def main():
    args = parse_args()
    run_dt = now_tr()
    print(f"\n--- BIST Post Botu (OUT JSON • Gemini yok) [{run_dt.strftime('%d.%m.%Y %H:%M:%S')}] ---")

    try:
        payload = load_out_json(args.json)
        stocks = transform_payload_to_stocks(payload)
        if not stocks:
            raise RuntimeError("JSON içinden hisse verisi çıkarılamadı.")

        # Görsel
        img_bytes = render_image(stocks, args.limit)
        print("✅ Görsel hazır.")

        # Tweet metni (20 şablondan rastgele)
        tweet = compose_tweet_from_templates(stocks)
        print(f"✅ Tweet metni hazır (len={len(tweet)}).")

        if args.dry_run:
            with open(args.out, "wb") as f:
                f.write(img_bytes)
            print("\n--- DRY RUN ---")
            print(f"Görsel kaydedildi: {args.out}")
            print("\n--- TWEET ---\n" + tweet + "\n")
            return

        # Post
        print("\n--- X POST MODU ---")
        oauth = oauth1_session_from_env()
        mid = upload_media(oauth, img_bytes)
        print(f"✅ Medya yüklendi (ID: {mid}). Tweet gönderiliyor...")
        post_tweet(oauth, tweet, mid)
        print("🎉 İşlem tamam.")

    except Exception as e:
        print(f"\n!!! HATA: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
