"""Controlla le pagine "lavora con noi" e segnala le righe nuove legate al marketing.

Uso: python scripts/controlla.py [--senza-browser]

Per ogni pagina salva le impronte delle righe di testo (dati/stato.json).
Al giro successivo le righe che non c'erano prima e che contengono parole
del marketing finiscono in risultati/novita_AAAA-MM-GG.csv e .md.
Al primo controllo di una pagina le righe marketing presenti vengono
segnalate come "presente al primo controllo".
"""
import argparse
import csv
import datetime as dt
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from comune import Limitatore, impronta, leggi_csv, scarica, testo_visibile  # noqa: E402

RADICE = Path(__file__).resolve().parent.parent
ELENCO = RADICE / "dati" / "aziende.csv"
STATO = RADICE / "dati" / "stato.json"
RISULTATI = RADICE / "risultati"

# una riga conta se parla di marketing E sembra un ruolo/annuncio (non menu, cookie o privacy)
RX_MKT = re.compile(r"marketing|comunicazione|communication|social[\s\-]?media|content[\s\-]?(creator|manager|specialist|marketing|editor|strategist)|"
    r"copywrit|digital[\s\-]?(marketing|specialist|media|content|communication|pr\b|advertising|strategist)|"
    r"brand[\s\-]?(manager|specialist|ambassador|activation|marketing|designer)|grafic[oa]|graphic|"
    r"(event|eventi)[\s\-]?(manager|specialist|coordinator|planner|organizer)|organizzazione eventi|"
    r"ufficio stampa|press office|public relations|relazioni esterne|\bpr (specialist|manager)|"
    r"e-?commerce|\bcrm\b|\bseo\b|\bsem\b|advertising|performance marketing|community manager|influencer|"
    r"customer experience|web ?content|trade marketing|product marketing|media ?planner|video ?maker|creative", re.I)
RX_RUOLO = re.compile(r"specialist|manager|addett|responsabile|coordinat|assistant|assistente|junior|senior|stage|stagista|"
    r"tirocin|internship|intern\b|impiegat|designer|copywriter|creator|account|executive|officer|analyst|"
    r"operator|collaborat|figura|cerchiamo|ricerchiamo|selezioniamo|posizione|\(m/f\)|m/f|full[\s\-]?time|"
    r"part[\s\-]?time|lead\b|editor|strategist|planner|consultant|consulente|esperto|esperta|referente|"
    r"apprendist|tecnico|expert|owner|director", re.I)
RX_RUMORE = re.compile(r"cookie|privacy|newsletter|iscri|seguici|follow us|copyright|©|p\.?\s?iva|cooperativa sociale|"
    r"ragione sociale|sede sociale|capitale sociale|responsabilit[aà] sociale|consenso|informativa|accetto|"
    r"profilazione|archiviazione|trattamento|titolare|interessat|finalit|comunicazioni commerciali|"
    r"lecturer|bocconi|ricercatore|skip to|vai al contenuto|salta al", re.I)

def rilevante(r):
    if len(r) > 160 or len(r.split()) < 2:
        return False
    return bool(RX_MKT.search(r)) and bool(RX_RUOLO.search(r)) and not RX_RUMORE.search(r)


RX_JS = re.compile(r"enable javascript|abilita javascript|attiva javascript|__NEXT_DATA__|id=\"root\"></div>|id=\"app\"></div>", re.I)


def leggi_pagina(url, lim):
    lim.attendi(url)
    fin, html = scarica(url)
    if not fin:
        return None, html, False
    righe = testo_visibile(html)
    serve_browser = len(" ".join(righe)) < 400 or bool(RX_JS.search(html[:50000]))
    return righe, None, serve_browser


def leggi_con_browser(urls):
    """Rende con Chromium le pagine che caricano gli annunci via JavaScript."""
    out = {}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return out
    from comune import UA, robots_ok
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(user_agent=UA, locale="it-IT")
        for u in urls:
            if not robots_ok(u):
                continue
            pg = ctx.new_page()
            try:
                pg.goto(u, timeout=30000, wait_until="networkidle")
                html = pg.content()
                out[u] = testo_visibile(html)
            except Exception:
                pass
            finally:
                pg.close()
        b.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--senza-browser", action="store_true")
    a = ap.parse_args()
    oggi = dt.date.today().isoformat()
    aziende = [x for x in leggi_csv(ELENCO) if x.get("pagina_lavoro") and "LinkedIn" not in x.get("esito_ricerca", "")]
    stato = json.loads(STATO.read_text("utf-8")) if STATO.exists() else {}
    lavori = []
    for az in aziende:
        for u in az["pagina_lavoro"].split(" | "):
            if u.strip():
                lavori.append((az, u.strip()))
    print(f"Pagine da controllare: {len(lavori)}")
    lim = Limitatore(2.0)
    lette, errori, js = {}, {}, []
    with ThreadPoolExecutor(max_workers=16) as ex:
        fut = {ex.submit(leggi_pagina, u, lim): (az, u) for az, u in lavori}
        for f in as_completed(fut):
            az, u = fut[f]
            try:
                righe, err, serve_browser = f.result()
            except Exception as e:
                righe, err, serve_browser = None, type(e).__name__, False
            if righe is None:
                errori[u] = err
                continue
            lette[u] = righe
            if serve_browser:
                js.append(u)
    if js and not a.senza_browser:
        print(f"Pagine da aprire con il browser: {len(js)}")
        for u, righe in leggi_con_browser(js).items():
            if len(" ".join(righe)) > len(" ".join(lette.get(u, []))):
                lette[u] = righe

    novita = []
    for az, u in lavori:
        if u not in lette:
            continue
        righe = lette[u]
        prima = u not in stato
        vecchie = set(stato.get(u, {}).get("righe", []))
        for r in righe:
            if rilevante(r):
                if prima or impronta(r) not in vecchie:
                    novita.append({
                        "data": oggi, "azienda": az["nome"], "comune": az["comune"], "settore": az.get("settore", ""),
                        "riga_trovata": r, "pagina": u,
                        "tipo": "presente al primo controllo" if prima else "NUOVA",
                    })
        stato[u] = {"righe": sorted({impronta(r) for r in righe}), "ultimo_controllo": oggi}
    for u, err in errori.items():
        s = stato.setdefault(u, {"righe": []})
        s["ultimo_errore"] = f"{oggi}: {err}"

    STATO.write_text(json.dumps(stato, ensure_ascii=False, separators=(",", ":")), "utf-8")
    RISULTATI.mkdir(exist_ok=True)
    campi = ["data", "tipo", "azienda", "comune", "settore", "riga_trovata", "pagina"]
    nuove = [n for n in novita if n["tipo"] == "NUOVA"]
    if novita:
        with open(RISULTATI / f"novita_{oggi}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=campi)
            w.writeheader()
            w.writerows(sorted(novita, key=lambda n: (n["tipo"] != "NUOVA", n["azienda"])))
        storico = RISULTATI / "storico_novita.csv"
        nuovo_file = not storico.exists()
        with open(storico, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=campi)
            if nuovo_file:
                w.writeheader()
            w.writerows(novita)
    # riepilogo leggibile (usato anche per la notifica)
    md = [f"# Controllo pagine lavoro del {oggi}", "",
          f"Pagine controllate: {len(lette)} su {len(lavori)} (non leggibili: {len(errori)})", "",
          f"Righe marketing NUOVE: {len(nuove)}", ""]
    per_az = {}
    for n in nuove:
        per_az.setdefault((n["azienda"], n["comune"], n["pagina"]), []).append(n["riga_trovata"])
    for (az, com, pag), rr in sorted(per_az.items()):
        md.append(f"## {az} ({com})")
        md.append(pag)
        md += [f"- {r}" for r in rr[:8]]
        md.append("")
    prime = len(novita) - len(nuove)
    if prime:
        md.append(f"Pagine controllate per la prima volta: {prime} righe marketing già presenti (vedi CSV).")
    (RISULTATI / "ultimo_controllo.md").write_text("\n".join(md), "utf-8")
    print("\n".join(md[:6]))
    # file usato dal workflow per decidere se aprire una segnalazione
    (RISULTATI / ".ci_sono_novita").write_text("1" if nuove or prime else "0")


if __name__ == "__main__":
    main()
