#!/usr/bin/env python3
"""Smolny robot.

Legge i feed RSS/Atom delle testate in _data/sources.yml, prende titolo,
breve estratto, link e immagine del feed, traduce in italiano e inglese e
scrive _data/news.json (letto da Jekyll) e _data/sources_status.json.

Del contenuto originale si pubblicano solo titolo, un estratto breve e il
link all'articolo: il testo completo resta sul sito della testata.
"""

from __future__ import annotations

import calendar
import hashlib
import html
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup

try:
    from deep_translator import GoogleTranslator
except ImportError:  # traduzione disattivata se la libreria manca
    GoogleTranslator = None

ROOT = Path(__file__).resolve().parent.parent
SOURCES_FILE = ROOT / "_data" / "sources.yml"
NEWS_FILE = ROOT / "_data" / "news.json"
STATUS_FILE = ROOT / "_data" / "sources_status.json"

TARGET_LANGS = ["it", "en"]
MAX_ITEMS = 700            # articoli tenuti in totale
MAX_PER_SOURCE = 40        # massimo per testata nell'archivio
MAX_NEW_PER_SOURCE = 12    # nuovi articoli letti per testata a ogni giro
MAX_TRANSLATIONS = int(os.environ.get("SMOLNY_MAX_TRANSLATIONS", "160"))
MAX_AGE_DAYS = 45
EXCERPT_CHARS = 280
TIMEOUT = 25

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36 SmolnyBot/1.0 (+https://github.com/anomaly73bit/smolny)"
    ),
    "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/html;q=0.8, */*;q=0.5",
    "Accept-Language": "en,it;q=0.8,*;q=0.5",
}

COMMON_FEED_PATHS = ["/feed", "/feed/", "/rss", "/rss.xml", "/feed.xml", "/index.xml",
                     "/atom.xml", "/rss/", "/?feed=rss2", "/index.rss"]

# codici lingua per Google Translate
GT_LANG = {"zh": "zh-CN", "he": "iw"}

session = requests.Session()
session.headers.update(HEADERS)


def log(msg: str) -> None:
    print(msg, flush=True)


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def fetch(url: str) -> requests.Response | None:
    try:
        r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
        if r.status_code == 200 and r.content:
            return r
        log(f"    HTTP {r.status_code} {url}")
    except requests.RequestException as e:
        log(f"    errore {type(e).__name__} {url}")
    return None


def parse_feed(url: str):
    r = fetch(url)
    if not r:
        return None
    parsed = feedparser.parse(r.content)
    if parsed.entries:
        return parsed
    return None


def discover_feeds(home: str) -> list[str]:
    """Cerca i feed dichiarati nella homepage, poi prova i percorsi comuni."""
    found: list[str] = []
    r = fetch(home)
    if r is not None:
        soup = BeautifulSoup(r.text, "html.parser")
        for link in soup.find_all("link", rel=lambda v: v and "alternate" in v):
            typ = (link.get("type") or "").lower()
            href = link.get("href")
            if href and ("rss" in typ or "atom" in typ):
                u = urljoin(r.url, href)
                if "comments" not in u and u not in found:
                    found.append(u)
    base = f"{urlparse(home).scheme}://{urlparse(home).netloc}"
    for p in COMMON_FEED_PATHS:
        u = base + p
        if u not in found:
            found.append(u)
    return found


def clean_text(raw: str) -> str:
    if not raw:
        return ""
    text = BeautifulSoup(raw, "html.parser").get_text(" ")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    # frasi standard dei feed WordPress e simili
    text = re.sub(r"(The post|L'articolo|El artículo|La entrada|Der Beitrag|O post)\s.*?(appeared first on|proviene da|se publicó primero en|apareció primero en|erschien zuerst auf|apareceu primeiro em).*$", "", text, flags=re.I).strip()
    text = re.sub(r"\[\s*(…|\.\.\.|&hellip;)\s*\]$", "", text).strip()
    text = re.sub(r"(Continue reading|Read more|Leggi tutto|Continua a leggere|Leer más|Weiterlesen|Lire la suite)\W*$", "", text, flags=re.I).strip()
    return text


def excerpt(text: str, limit: int = EXCERPT_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:—-")
    return cut + "…"


def entry_image(entry) -> str | None:
    cands: list[str] = []
    for key in ("media_content", "media_thumbnail"):
        for m in entry.get(key, []) or []:
            if m.get("url") and (m.get("medium") in (None, "image") or "image" in (m.get("type") or "image")):
                cands.append(m["url"])
    for enc in entry.get("enclosures", []) or []:
        if "image" in (enc.get("type") or "") and enc.get("href"):
            cands.append(enc["href"])
    for link in entry.get("links", []) or []:
        if link.get("rel") == "enclosure" and "image" in (link.get("type") or ""):
            cands.append(link.get("href"))
    if not cands:
        blobs = [c.get("value", "") for c in entry.get("content", []) or []] + [entry.get("summary", "")]
        for blob in blobs:
            m = re.search(r"<img[^>]+src=[\"']([^\"']+)[\"']", blob or "")
            if m:
                cands.append(html.unescape(m.group(1)))
                break
    for c in cands:
        if not c or c.startswith("data:"):
            continue
        if c.startswith("//"):
            c = "https:" + c
        if c.startswith("http://"):
            c = "https://" + c[7:]
        if c.startswith("https://") and not re.search(r"(pixel|feedburner|gravatar|emoji|1x1)", c, re.I):
            return c
    return None


def entry_date(entry) -> datetime:
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        t = entry.get(key)
        if t:
            try:
                d = datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc)
                now = datetime.now(timezone.utc)
                return min(d, now)
            except (OverflowError, ValueError):
                pass
    return datetime.now(timezone.utc)


def item_id(link: str) -> str:
    norm = re.sub(r"[?#].*$", "", link.strip()).rstrip("/").replace("http://", "https://")
    return hashlib.sha1(norm.encode()).hexdigest()[:16]


def read_source(src: dict, status: dict) -> tuple[list[dict], dict]:
    sid = src["id"]
    prev = status.get(sid, {})
    tried: list[str] = []
    candidates = []
    for u in (src.get("feed"), prev.get("feed")):
        if u and u not in candidates:
            candidates.append(u)
    parsed, used = None, None
    for u in candidates:
        tried.append(u)
        parsed = parse_feed(u)
        if parsed:
            used = u
            break
    if not parsed:
        for u in discover_feeds(src["url"]):
            if u in tried:
                continue
            tried.append(u)
            parsed = parse_feed(u)
            if parsed:
                used = u
                break
    checked = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not parsed:
        log(f"  ✗ {sid}: nessun feed trovato")
        return [], {"ok": False, "feed": prev.get("feed"), "checked": checked,
                    "last_ok": prev.get("last_ok"), "error": "nessun feed leggibile"}

    items = []
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    for e in parsed.entries[:MAX_NEW_PER_SOURCE]:
        link = (e.get("link") or "").strip()
        title = clean_text(e.get("title", ""))
        if not link or not title:
            continue
        link = urljoin(used, link)
        d = entry_date(e)
        if d.timestamp() < cutoff:
            continue
        body = ""
        if e.get("summary"):
            body = e["summary"]
        elif e.get("content"):
            body = e["content"][0].get("value", "")
        summ = excerpt(clean_text(body))
        if summ.lower().startswith(title.lower()[:60]) and len(summ) < len(title) + 20:
            summ = ""
        items.append({
            "id": item_id(link),
            "source": sid,
            "name": src["name"],
            "country": src["country"],
            "lang": src["lang"],
            "url": link,
            "date": d.isoformat(timespec="seconds"),
            "image": entry_image(e),
            "title": {"orig": title},
            "summary": {"orig": summ},
        })
    log(f"  ✓ {sid}: {len(items)} articoli ({used})")
    return items, {"ok": True, "feed": used, "checked": checked, "last_ok": checked,
                   "count": len(parsed.entries)}


_translators: dict[tuple[str, str], object] = {}


def translate(text: str, src: str, dst: str) -> str | None:
    if not text or GoogleTranslator is None:
        return None
    key = (src, dst)
    try:
        if key not in _translators:
            _translators[key] = GoogleTranslator(source=GT_LANG.get(src, src), target=GT_LANG.get(dst, dst))
        out = _translators[key].translate(text[:4500])
        return out.strip() if out else None
    except Exception as e:  # la traduzione non deve mai bloccare il robot
        log(f"    traduzione {src}->{dst} fallita: {type(e).__name__}")
        try:
            out = GoogleTranslator(source="auto", target=GT_LANG.get(dst, dst)).translate(text[:4500])
            return out.strip() if out else None
        except Exception:
            return None


SEP = "\n\n§§\n\n"


def translate_item(item: dict) -> bool:
    """Traduce titolo + estratto nelle lingue mancanti. True se ha tradotto qualcosa."""
    did = False
    for dst in TARGET_LANGS:
        if item["title"].get(dst):
            continue
        if item["lang"] == dst:
            item["title"][dst] = item["title"]["orig"]
            item["summary"][dst] = item["summary"]["orig"]
            continue
        joined = item["title"]["orig"] + (SEP + item["summary"]["orig"] if item["summary"]["orig"] else "")
        out = translate(joined, item["lang"], dst)
        if not out:
            continue
        parts = re.split(r"\s*§§\s*", out, maxsplit=1)
        if item["summary"]["orig"] and len(parts) == 2:
            item["title"][dst], item["summary"][dst] = parts[0].strip(), parts[1].strip()
        elif not item["summary"]["orig"]:
            item["title"][dst], item["summary"][dst] = out.replace("§§", "").strip(), ""
        else:  # il separatore si è perso: traduco i due pezzi separatamente
            t = translate(item["title"]["orig"], item["lang"], dst)
            s = translate(item["summary"]["orig"], item["lang"], dst)
            if t:
                item["title"][dst] = t
                item["summary"][dst] = s or ""
        did = True
        time.sleep(0.2)
    return did


def main() -> int:
    sources = yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8"))
    news = load_json(NEWS_FILE, {"items": []})
    status = load_json(STATUS_FILE, {})
    old = {it["id"]: it for it in news.get("items", [])}

    only = set(sys.argv[1:])
    if only:
        sources = [s for s in sources if s["id"] in only]

    log(f"Smolny robot — {len(sources)} fonti")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda s: read_source(s, status), sources))

    fresh = 0
    for src, (items, st) in zip(sources, results):
        status[src["id"]] = st
        for it in items:
            if it["id"] in old:
                # tengo le traduzioni già fatte, aggiorno l'immagine se mancava
                if not old[it["id"]].get("image") and it.get("image"):
                    old[it["id"]]["image"] = it["image"]
                continue
            old[it["id"]] = it
            fresh += 1

    # pulizia: età massima, ordine cronologico, tetto per fonte e totale
    known = {s["id"] for s in yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8"))}
    cutoff = time.time() - MAX_AGE_DAYS * 86400
    items = [it for it in old.values()
             if it["source"] in known and datetime.fromisoformat(it["date"]).timestamp() >= cutoff]
    items.sort(key=lambda it: it["date"], reverse=True)
    per_source: dict[str, int] = {}
    kept = []
    for it in items:
        n = per_source.get(it["source"], 0)
        if n < MAX_PER_SOURCE:
            kept.append(it)
            per_source[it["source"]] = n + 1
    kept = kept[:MAX_ITEMS]

    # traduzioni: prima i più recenti, con un tetto per giro
    done = 0
    for it in kept:
        if done >= MAX_TRANSLATIONS:
            break
        if all(it["title"].get(l) for l in TARGET_LANGS):
            continue
        if translate_item(it):
            done += 1
    # gli articoli non ancora tradotti escono dopo, al prossimo giro
    ready = [it for it in kept if all(it["title"].get(l) for l in TARGET_LANGS)]

    out = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "count": len(ready), "items": ready}
    NEWS_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    STATUS_FILE.write_text(json.dumps(dict(sorted(status.items())), ensure_ascii=False, indent=1),
                           encoding="utf-8")
    ok = sum(1 for s in status.values() if s.get("ok"))
    pending = len(kept) - len(ready)
    log(f"Fatto: {fresh} nuovi, {done} tradotti, {len(ready)} pubblicati, "
        f"{pending} in attesa di traduzione, fonti attive {ok}/{len(status)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
