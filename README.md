# Smolny

**La stampa comunista del mondo, in un solo posto.** — *The world's communist press, in one place.*

Smolny è un aggregatore: un robot legge i feed delle testate comuniste, marxiste-leniniste e della sinistra di classe di tutto il mondo, traduce titolo ed estratto in italiano e inglese e li pubblica su un sito Jekyll. Ogni notizia rimanda all'articolo originale.

Sito: https://anomaly73bit.github.io/smolny/ (italiano) · https://anomaly73bit.github.io/smolny/en/ (English)

## Come funziona

| Pezzo | Dove |
|---|---|
| Elenco testate | `_data/sources.yml` |
| Robot (Python) | `robot/smolny.py` |
| Notizie raccolte | `_data/news.json` (scritto dal robot) |
| Stato dei feed | `_data/sources_status.json` (scritto dal robot) |
| Esecuzione automatica | `.github/workflows/robot.yml`, ogni 3 ore |
| Tema | [Jasper2](https://github.com/jekyllt/jasper2) (porting Jekyll di Casper, MIT) ridisegnato in `assets/css/smolny.css` |
| Testi IT/EN | `_data/i18n.yml` |

Il robot:
1. legge il feed di ogni testata; se manca o non risponde, cerca il feed dalla homepage;
2. prende titolo, un estratto di massimo 280 caratteri, link, data e foto del feed;
3. traduce titolo ed estratto in italiano e inglese (Google Translate via `deep-translator`);
4. tiene gli ultimi 45 giorni, al massimo 40 articoli per testata e 700 in totale;
5. fa commit di `_data/news.json`, e GitHub Pages ripubblica il sito.

## Aggiungere una testata

Aggiungi un blocco in `_data/sources.yml`:

```yaml
- id: nometestata
  name: Nome Testata
  country: IT        # codice in _data/countries.yml
  lang: it           # lingua originale
  url: https://www.esempio.org
  feed: https://www.esempio.org/feed/   # facoltativo
```

Il push su `main` fa partire subito il robot.

## Lanciare il robot a mano

Da GitHub: **Actions → Robot Smolny → Run workflow**. In locale:

```bash
pip install -r robot/requirements.txt
python robot/smolny.py              # tutte le fonti
python robot/smolny.py granma kprf  # solo alcune
```

## Sito in locale

```bash
bundle install
bundle exec jekyll serve
```

## Pubblicazione

GitHub Pages, da **Settings → Pages → Deploy from a branch → `main` / root**.
