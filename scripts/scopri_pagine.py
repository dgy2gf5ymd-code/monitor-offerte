"""Trova la pagina "lavora con noi" per le aziende dell'elenco che non ce l'hanno ancora.

Uso: python scripts/scopri_pagine.py [--max N] [--rifai]
  --max N    controlla al massimo N aziende in questo giro (default 600)
  --rifai    ricontrolla anche le aziende con esito 'nessuna pagina' più vecchio di 60 giorni
"""
import argparse
import datetime as dt
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent))
from comune import Limitatore, dominio, leggi_csv, link, scarica, scrivi_csv, testo_visibile  # noqa: E402

RADICE = Path(__file__).resolve().parent.parent
ELENCO = RADICE / "dati" / "aziende.csv"
CAMPI = ["id", "nome", "comune", "settore", "sito", "fonte", "pagina_lavoro", "esito_ricerca",
         "data_ricerca", "note"]

# parole che indicano un link verso la pagina lavoro (testo o indirizzo del link)
RX_LINK = re.compile(
    r"lavora[\s\-_]*con[\s\-_]*noi|lavorare[\s\-_]*con[\s\-_]*noi|lavora[\s\-_]*in|work[\s\-_]*with[\s\-_]*us|"
    r"careers?|carriere?|carriera|\bjobs?\b|job[\s\-_]*opportunit|opportunit[aà][\s\-_]*di[\s\-_]*lavoro|"
    r"posizioni[\s\-_]*aperte|offerte[\s\-_]*di[\s\-_]*lavoro|join[\s\-_]*(us|our[\s\-_]*team)|unisciti|"
    r"entra[\s\-_]*(a[\s\-_]*far[\s\-_]*parte|nel[\s\-_]*team|in)|candidatur|recruit|talent|"
    r"work[\s\-_]*at|chi[\s\-_]*cerchiamo|selezion[ei][\s\-_]*del[\s\-_]*personale|risorse[\s\-_]*umane",
    re.I)
# portali di selezione usati dalle aziende (link esterni ammessi)
RX_ATS = re.compile(
    r"inrecruiting|intervieweb|altamiraweb|allibo|factorialhr|teamtailor|personio|recruitee|workable|"
    r"jobylon|myworkdayjobs|smartrecruiters|join\.com|lever\.co|greenhouse\.io|zucchetti|taleo|successfactors|"
    r"softgarden|bamboohr|breezy|jobs\.|careers\.|recruiting\.", re.I)
RX_ESCLUDI = re.compile(r"facebook|instagram|twitter|x\.com|youtube|tiktok|privacy|cookie|\.pdf$|\.jpg$|\.png$", re.I)
PERCORSI = ["/lavora-con-noi", "/lavora-con-noi/", "/it/lavora-con-noi", "/careers", "/carriere", "/jobs",
            "/it/careers", "/work-with-us", "/lavora-con-noi.html", "/azienda/lavora-con-noi"]
RX_CONTENUTO = re.compile(r"lavora con noi|posizion[ei] apert|candidat|curriculum|\bcv\b|careers?|offerte di lavoro|"
                          r"work with us|open positions|selezioniamo|stiamo cercando|ricerchiamo", re.I)


def punteggio(url, testo, titolo, dom_azienda):
    s = 0
    stringa = f"{testo} {titolo}"
    if RX_LINK.search(stringa):
        s += 3
    if RX_LINK.search(urlparse(url).path):
        s += 2
    d = dominio(url)
    esterno = dom_azienda not in d
    if esterno:
        if RX_ATS.search(url):
            s += 1
        elif "linkedin.com" in d and "/jobs" in url:
            s += 0
        else:
            return 0
    if RX_ESCLUDI.search(url):
        return 0
    if re.search(r"talent", stringa, re.I) and not re.search(r"lavor|career|job", stringa + url, re.I):
        s -= 2  # "talent" da solo è spesso un prodotto o un evento
    return s


def cerca_azienda(az, lim):
    sito = (az.get("sito") or "").strip()
    if not sito:
        return {"esito_ricerca": "sito mancante"}
    if not sito.startswith("http"):
        sito = "https://" + sito
    candidati_home = [sito]
    if "://www." not in sito:
        candidati_home.append(sito.replace("://", "://www.", 1))
    html, base, motivo = None, None, ""
    for u in candidati_home:
        lim.attendi(u)
        base, html = scarica(u)
        if base:
            break
        motivo = html
        html = None
    if not html:
        return {"esito_ricerca": f"sito non raggiungibile: {motivo}"}
    dom = dominio(base)
    trovati = []
    for url, testo, titolo in link(html, base):
        p = punteggio(url, testo, titolo, dom.split(".")[-2] if dom.count(".") else dom)
        if p >= 3:
            trovati.append((p, url))
    trovati.sort(key=lambda x: -x[0])
    pagine = []
    for _, url in trovati:
        u = url.split("#")[0]
        if u not in pagine:
            pagine.append(u)
    if not pagine:
        radice = f"{urlparse(base).scheme}://{urlparse(base).netloc}"
        for perc in PERCORSI:
            u = radice + perc
            lim.attendi(u)
            fin, h = scarica(u)
            if fin and fin.rstrip("/") != radice and RX_CONTENUTO.search(" ".join(testo_visibile(h))[:20000]):
                pagine.append(fin)
                break
    if not pagine:
        if len(" ".join(testo_visibile(html))) < 200:
            return {"esito_ricerca": "homepage senza testo (sito in JavaScript?): controllare a mano"}
        return {"esito_ricerca": "nessuna pagina lavoro trovata"}
    principale = pagine[0]
    # se la pagina lavoro rimanda a un elenco "posizioni aperte" lo aggiungiamo
    extra = []
    if "linkedin.com" not in principale:
        lim.attendi(principale)
        fin, h = scarica(principale)
        if fin:
            for url, testo, titolo in link(h, fin):
                if re.search(r"posizioni aperte|offerte|open positions|annunci|vacanc|opportunit", f"{testo} {titolo}", re.I) \
                        and not RX_ESCLUDI.search(url) and url.split("#")[0] not in (principale, fin):
                    if dominio(url).split(".")[-2:] == dominio(fin).split(".")[-2:] or RX_ATS.search(url):
                        extra.append(url.split("#")[0])
        else:
            return {"pagina_lavoro": principale, "esito_ricerca": f"pagina trovata ma non leggibile: {h}"}
    tutte = [principale] + [e for e in extra[:2] if e != principale]
    esito = "trovata"
    if any("linkedin.com" in u for u in tutte):
        esito = "solo LinkedIn (non monitorabile)"
    return {"pagina_lavoro": " | ".join(tutte), "esito_ricerca": esito}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=600)
    ap.add_argument("--rifai", action="store_true")
    a = ap.parse_args()
    aziende = leggi_csv(ELENCO)
    oggi = dt.date.today()
    da_fare = []
    for az in aziende:
        if az.get("pagina_lavoro"):
            continue
        es = az.get("esito_ricerca", "")
        if not es:
            da_fare.append(az)
        elif a.rifai and az.get("data_ricerca"):
            try:
                if (oggi - dt.date.fromisoformat(az["data_ricerca"])).days > 60:
                    da_fare.append(az)
            except ValueError:
                pass
    da_fare = da_fare[: a.max]
    print(f"Aziende da controllare in questo giro: {len(da_fare)}")
    lim = Limitatore(1.5)
    fatti = 0
    with ThreadPoolExecutor(max_workers=12) as ex:
        fut = {ex.submit(cerca_azienda, az, lim): az for az in da_fare}
        for f in as_completed(fut):
            az = fut[f]
            try:
                ris = f.result()
            except Exception as e:  # una singola azienda non deve fermare il giro
                ris = {"esito_ricerca": f"errore: {type(e).__name__}"}
            az.update(ris)
            az["data_ricerca"] = oggi.isoformat()
            fatti += 1
            if fatti % 50 == 0:
                print(f"  {fatti}/{len(da_fare)}")
                scrivi_csv(ELENCO, aziende, CAMPI)
    scrivi_csv(ELENCO, aziende, CAMPI)
    tot = sum(1 for x in aziende if x.get("pagina_lavoro"))
    print(f"Fatto. Aziende con pagina lavoro: {tot} su {len(aziende)}")


if __name__ == "__main__":
    main()
