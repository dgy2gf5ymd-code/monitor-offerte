"""Cerca la pagina lavoro sui siti che il lettore normale non riesce a vedere,
aprendoli con un browser vero (Chromium).

Uso: python scripts/scopri_pagine_browser.py [--max N] [--fetta i --di N]
                                             [--parziale FILE] [--attesa 2500]

Riguarda due casi:
  - siti costruiti in JavaScript, dove la pagina scaricata e' vuota
    ("homepage senza testo")
  - siti che rifiutano il lettore automatico con un errore 403

I siti che vietano la lettura nel loro robots.txt restano esclusi: il browser
controlla il robots.txt esattamente come il lettore normale.
La logica di ricerca (link, sitemap, indirizzi probabili, verifica del contenuto)
e' la stessa di scopri_pagine.py: qui cambia solo il modo di scaricare le pagine.
"""
import argparse
import csv
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import scopri_pagine as sp  # noqa: E402
from comune import Limitatore, leggi_csv, robots_ok  # noqa: E402

DA_RIPRENDERE = ("homepage senza testo", "sito non raggiungibile: HTTP 403")


def scelte(aziende):
    return [x for x in aziende
            if not x.get("pagina_lavoro") and x.get("esito_ricerca", "").startswith(DA_RIPRENDERE)]


def fabbrica_scarica(ctx, attesa):
    """Restituisce una funzione con la stessa firma di comune.scarica() che usa il browser."""
    def scarica_browser(url):
        if not robots_ok(url):
            return None, "bloccato da robots.txt"
        pagina = ctx.new_page()
        try:
            risposta = pagina.goto(url, timeout=35000, wait_until="domcontentloaded")
            pagina.wait_for_timeout(attesa)  # tempo perche' il JavaScript carichi i contenuti
            if risposta is not None and risposta.status >= 400:
                return None, f"HTTP {risposta.status}"
            return pagina.url, pagina.content()
        except Exception as e:
            return None, f"errore browser ({type(e).__name__})"
        finally:
            try:
                pagina.close()
            except Exception:
                pass
    return scarica_browser


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0, help="0 = nessun limite")
    ap.add_argument("--fetta", type=int, default=0)
    ap.add_argument("--di", type=int, default=1)
    ap.add_argument("--parziale", default="")
    ap.add_argument("--attesa", type=int, default=2500, help="millisecondi di attesa per il JavaScript")
    ap.add_argument("--candidati", type=int, default=6, help="pagine candidate per azienda")
    a = ap.parse_args()
    if a.di > 1 and not a.parziale:
        sys.exit("con --di serve --parziale, altrimenti i job si sovrascrivono a vicenda")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Playwright non installato: eseguire pip install playwright && playwright install chromium")

    sp.MAX_CANDIDATI = a.candidati  # con il browser ogni tentativo costa, quindi meno tentativi
    aziende = leggi_csv(sp.ELENCO)
    da_fare = scelte(aziende)
    if a.di > 1:
        da_fare = da_fare[a.fetta :: a.di]
    if a.max:
        da_fare = da_fare[: a.max]
    oggi = dt.date.today()
    print(f"Siti da riprovare col browser: {len(da_fare)}"
          f"{f' (fetta {a.fetta + 1} di {a.di})' if a.di > 1 else ''}", flush=True)
    if not da_fare:
        if a.parziale:
            Path(a.parziale).write_text(",".join(sp.CAMPI_PARZIALE) + "\n", encoding="utf-8")
        return

    lim = Limitatore(1.5)
    risultati = []

    def salva():
        if a.parziale:
            with open(a.parziale, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=sp.CAMPI_PARZIALE, extrasaction="ignore")
                w.writeheader()
                w.writerows(risultati)
        else:
            sp.scrivi_csv(sp.ELENCO, aziende, sp.CAMPI)

    originale = sp.scarica
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                                             "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                                  locale="it-IT", viewport={"width": 1280, "height": 900})
        sp.scarica = fabbrica_scarica(ctx, a.attesa)
        try:
            # il browser non si puo' usare da piu' thread: si procede un'azienda alla volta
            for i, az in enumerate(da_fare, 1):
                try:
                    ris = sp.cerca_azienda(az, lim)
                except Exception as e:
                    ris = {"esito_ricerca": f"errore: {type(e).__name__}"}
                az.update(ris)
                az["data_ricerca"] = oggi.isoformat()
                risultati.append({"id": az["id"], "pagina_lavoro": az.get("pagina_lavoro", ""),
                                  "esito_ricerca": az["esito_ricerca"], "data_ricerca": az["data_ricerca"]})
                if i % 25 == 0:
                    trovate = sum(1 for x in risultati if x["pagina_lavoro"])
                    print(f"  {i}/{len(da_fare)} - pagine trovate {trovate}", flush=True)
                    salva()
        finally:
            sp.scarica = originale
            ctx.close()
            browser.close()
    salva()
    trovate = sum(1 for x in risultati if x["pagina_lavoro"])
    print(f"Fatto. Riprovati {len(da_fare)} siti col browser, pagine lavoro trovate: {trovate}")


if __name__ == "__main__":
    main()
