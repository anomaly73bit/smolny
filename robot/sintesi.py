"""Sintesi degli articoli per Smolny.

Per ogni notizia il robot scarica la pagina dell'articolo, ne estrae il testo
e chiede a un modello linguistico (GitHub Models, gratuito con il token del
repository) una sintesi originale in italiano e in inglese: un riassunto con
parole proprie, molto più breve dell'originale, con al massimo una breve
citazione attribuita. Il testo completo resta sul sito della testata, che la
pagina di Smolny cita e linka sempre.

Scrive poi una pagina Jekyll per lingua in _notizie/ e _news_en/.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

try:
    import trafilatura
except ImportError:
    trafilatura = None

ROOT = Path(__file__).resolve().parent.parent
PAGE_DIRS = {"it": ROOT / "_notizie", "en": ROOT / "_news_en"}

API_URL = os.environ.get("SMOLNY_LLM_URL", "https://models.github.ai/inference/chat/completions")
MODELS = [m.strip() for m in os.environ.get(
    "SMOLNY_LLM_MODELS", "openai/gpt-4.1-mini,openai/gpt-4o-mini").split(",") if m.strip()]
TOKEN = os.environ.get("SMOLNY_LLM_TOKEN") or os.environ.get("GITHUB_TOKEN", "")
MAX_SOURCE_CHARS = 9000
MIN_SOURCE_CHARS = 400

LANG_NAMES = {"it": "Italian", "en": "English", "es": "Spanish", "pt": "Portuguese", "fr": "French",
              "de": "German", "el": "Greek", "ru": "Russian", "tr": "Turkish", "cs": "Czech",
              "sv": "Swedish", "da": "Danish", "zh": "Chinese", "vi": "Vietnamese"}

PROMPT = """You are an editor at Smolny, a news site that summarises the international communist and \
workers' press for Italian and English readers.

Below is an article published by {paper} ({country}), written in {lang}. Write an ORIGINAL news \
summary of it, once in Italian and once in English.

Rules:
- Write in your own words. Do not copy or closely paraphrase the original's sentences; report the facts.
- Each summary: a short headline and 2-4 paragraphs, 150-230 words in total. Never longer than a \
quarter of the original.
- Cover who, what, where, when and why, plus the article's main argument or position.
- Attribute the article's opinions to the paper or the author ("secondo {paper}…", \
"{paper} argues…"); keep facts and opinions distinct.
- At most ONE short direct quote (under 25 words), in quotation marks and attributed.
- Plain text paragraphs only: no markdown, no bullet points, no links.
- If the text is not a real news article (e.g. an error page, a list of links, a cookie notice), \
return {{"skip": true}}.

Return only JSON of this form:
{{"it": {{"title": "...", "paragraphs": ["...", "..."]}}, "en": {{"title": "...", "paragraphs": ["...", "..."]}}}}

ARTICLE TITLE: {title}

ARTICLE TEXT:
{text}
"""


class LimitReached(Exception):
    """Quota del modello esaurita: si riprende al prossimo giro."""


def fetch_article(session: requests.Session, url: str, timeout: int = 25) -> tuple[str, str | None]:
    """Restituisce (testo dell'articolo, immagine og:image)."""
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
    except requests.RequestException:
        return "", None
    if r.status_code != 200 or not r.text:
        return "", None
    html = r.text
    image = None
    soup = BeautifulSoup(html, "html.parser")
    for attr, name in (("property", "og:image"), ("name", "twitter:image"), ("property", "og:image:url")):
        tag = soup.find("meta", attrs={attr: name})
        if tag and tag.get("content", "").startswith("http"):
            image = tag["content"].replace("http://", "https://", 1)
            break
    text = ""
    if trafilatura is not None:
        try:
            text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False,
                                       favor_precision=True) or ""
        except Exception:
            text = ""
    if len(text) < MIN_SOURCE_CHARS:
        node = soup.find("article") or soup.find("main") or soup.body
        if node:
            paras = [p.get_text(" ", strip=True) for p in node.find_all("p")]
            alt = "\n".join(p for p in paras if len(p) > 60)
            if len(alt) > len(text):
                text = alt
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text, image


_model_index = 0


def _call_llm(prompt: str) -> dict | None:
    global _model_index
    if not TOKEN:
        raise LimitReached("nessun token per il modello")
    while _model_index < len(MODELS):
        model = MODELS[_model_index]
        try:
            r = requests.post(
                API_URL,
                headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
                         "Accept": "application/json"},
                json={"model": model, "temperature": 0.3, "max_tokens": 1400,
                      "response_format": {"type": "json_object"},
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=90,
            )
        except requests.RequestException as e:
            raise LimitReached(f"rete: {type(e).__name__}")
        if r.status_code == 429:
            raise LimitReached(f"quota {model}: {r.text[:160]}")
        if r.status_code in (400, 403, 404) and ("model" in r.text.lower() or r.status_code == 404):
            # modello non disponibile: provo il successivo
            print(f"    modello {model} non disponibile ({r.status_code}): {r.text[:160]}", flush=True)
            _model_index += 1
            continue
        if r.status_code in (401, 403):
            raise LimitReached(f"accesso negato ({r.status_code}): {r.text[:160]}")
        if r.status_code != 200:
            print(f"    modello {model}: HTTP {r.status_code} {r.text[:160]}", flush=True)
            return None
        try:
            content = r.json()["choices"][0]["message"]["content"]
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
            return json.loads(content)
        except (KeyError, IndexError, ValueError):
            return None
    raise LimitReached("nessun modello disponibile")


def _valid(block) -> bool:
    return (isinstance(block, dict) and isinstance(block.get("title"), str) and block["title"].strip()
            and isinstance(block.get("paragraphs"), list)
            and sum(len(p) for p in block["paragraphs"] if isinstance(p, str)) > 200)


def summarise(item: dict, text: str, country_name: str) -> dict | None:
    prompt = PROMPT.format(
        paper=item["name"], country=country_name, lang=LANG_NAMES.get(item["lang"], item["lang"]),
        title=item["title"]["orig"], text=text[:MAX_SOURCE_CHARS])
    data = _call_llm(prompt)
    if not isinstance(data, dict):
        return None
    if data.get("skip"):
        return {"skip": True}
    if not (_valid(data.get("it")) and _valid(data.get("en"))):
        return None
    out = {}
    for lang in ("it", "en"):
        out[lang] = {
            "title": data[lang]["title"].strip(),
            "paragraphs": [p.strip() for p in data[lang]["paragraphs"] if isinstance(p, str) and p.strip()],
        }
    return out


def run(items: list[dict], session: requests.Session, countries: dict, limit: int,
        report: dict) -> int:
    """Scrive le sintesi mancanti (le più recenti prima). Restituisce quante ne ha fatte."""
    done = 0
    todo = [it for it in items if "synth" not in it and it.get("synth_tries", 0) < 3]
    for it in todo:
        if done >= limit:
            break
        text, image = fetch_article(session, it["url"])
        if image and not it.get("image"):
            it["image"] = image
        if len(text) < MIN_SOURCE_CHARS:
            it["synth_tries"] = it.get("synth_tries", 0) + 1
            report["synth_short_text"] = report.get("synth_short_text", 0) + 1
            continue
        try:
            res = summarise(it, text, countries.get(it["country"], {}).get("en", it["country"]))
        except LimitReached as e:
            report["synth_stopped"] = str(e)[:200]
            print(f"  sintesi ferme: {e}", flush=True)
            break
        if res is None:
            it["synth_tries"] = it.get("synth_tries", 0) + 1
            report["synth_failed"] = report.get("synth_failed", 0) + 1
            continue
        if res.get("skip"):
            it["synth_tries"] = 3
            report["synth_skipped"] = report.get("synth_skipped", 0) + 1
            continue
        it["synth"] = res
        it.pop("synth_tries", None)
        done += 1
        time.sleep(float(os.environ.get("SMOLNY_LLM_PAUSE", "4")))
    report["synth_done"] = done
    report["synth_total"] = sum(1 for it in items if "synth" in it)
    return done


def write_pages(items: list[dict]) -> None:
    """Una pagina per notizia e per lingua; le notizie uscite dall'archivio spariscono."""
    for lang, folder in PAGE_DIRS.items():
        folder.mkdir(exist_ok=True)
        wanted = set()
        for it in items:
            name = f"{it['id']}.md"
            wanted.add(name)
            other = "en" if lang == "it" else "it"
            fm = {
                "layout": "article",
                "lang": lang,
                "nid": it["id"],
                "title": (it.get("synth", {}).get(lang, {}).get("title")
                          or it["title"].get(lang) or it["title"]["orig"]),
                "orig_title": it["title"]["orig"],
                "date": it["date"],
                "source": it["source"],
                "paper": it["name"],
                "country": it["country"],
                "orig_lang": it["lang"],
                "orig_url": it["url"],
                "image": it.get("image"),
                "paragraphs": it.get("synth", {}).get(lang, {}).get("paragraphs"),
                "excerpt_text": it["summary"].get(lang) or it["summary"].get("orig") or "",
                "alt": ("/en/news/" if other == "en" else "/notizia/") + it["id"] + "/",
                "sitemap": "synth" in it,
            }
            # JSON è YAML valido: una chiave per riga
            body = "---\n" + "\n".join(
                f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in fm.items()) + "\n---\n"
            path = folder / name
            if not path.exists() or path.read_text(encoding="utf-8") != body:
                path.write_text(body, encoding="utf-8")
        for f in folder.glob("*.md"):
            if f.name not in wanted:
                f.unlink()
