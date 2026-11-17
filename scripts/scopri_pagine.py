"""Trova la pagina "lavora con noi" per le aziende dell'elenco che non ce l'hanno ancora.

Uso: python scripts/scopri_pagine.py [--max N] [--rifai] [--riprova-fallite]
                                     [--fetta i --di N] [--parziale FILE] [--workers N]

  --max N             controlla al massimo N aziende in questo giro (0 = tutte)
  --rifai             ricontrolla le aziende con esito piu' vecchio di 60 giorni
  --riprova-fallite G ricontrolla le aziende con esito "nessuna pagina lavoro trovata"
                      il cui ultimo controllo e' piu' vecchio di G giorni (0 = mai)
  --fetta i --di N    lavora solo sulla fetta i di N (per i job paralleli)
  --parziale FILE     scrive i soli risultati in FILE invece di riscrivere l'elenco
                      (obbligatorio quando si usa --fetta, per non sovrascrivere l'elenco)
  --workers N         richieste in parallelo (default 24)

Ordine di ricerca per ogni azienda: link nella home, poi sitemap.xml, poi indirizzi
probabili. Una pagina viene accettata solo se il suo contenuto parla davvero di lavoro.
"""
import argparse
import csv
import datetime as dt
import re
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent))
from comune import (Limitatore, dominio, leggi_csv, link, robots_ok, scarica,  # noqa: E402
                    scrivi_csv, sessione, testo_visibile)

RADICE = Path(__file__).resolve().parent.parent
ELENCO = RADICE / "dati" / "aziende.csv"
CAMPI = ["id", "nome", "comune", "settore", "sito", "fonte", "pagina_lavoro", "esito_ricerca",
         "data_ricerca", "note", "piva"]
CAMPI_PARZIALE = ["id", "pagina_lavoro", "esito_ricerca", "data_ricerca"]
# quante pagine candidate provare per azienda: con il browser costano molto di piu'
MAX_CANDIDATI = 12

# parole che indicano con buona certezza una pagina lavoro
RX_LINK = re.compile(
    r"lavora[\s\-_]*con[\s\-_]*noi|lavorare[\s\-_]*con[\s\-_]*noi|lavora[\s\-_]*in|work[\s\-_]*with[\s\-_]*us|"
    r"careers?|carriere?|carriera|\bjobs?\b|job[\s\-_]*opportunit|opportunit[aà][\s\-_]*di[\s\-_]*lavoro|"
    r"posizioni[\s\-_]*aperte|offerte[\s\-_]*di[\s\-_]*lavoro|join[\s\-_]*(us|our[\s\-_]*team)|unisciti|"
    r"entra[\s\-_]*(a[\s\-_]*far[\s\-_]*parte|nel[\s\-_]*team|in[\s\-_]*azienda|in)|candidatur|recruit|"
    r"work[\s\-_]*(at|for[\s\-_]*us|here)|chi[\s\-_]*cerchiamo|selezion[ei][\s\-_]*del[\s\-_]*personale|"
    r"risorse[\s\-_]*umane|vacanc|posti[\s\-_]*vacanti|\bassunzion|life[\s\-_]*at|invia[\s\-_]*(il[\s\-_]*tuo[\s\-_]*)?cv|"
    r"ricerche[\s\-_]*in[\s\-_]*corso|opportunit[aà][\s\-_]*professional",
    re.I)
# parole piu' incerte: valgono solo se confermate dall'indirizzo della pagina
RX_LINK_DEBOLE = re.compile(r"\bpersone\b|\bpeople\b|\bteam\b|talent|\bhr\b|human[\s\-_]*resources|"
                            r"\blavoro\b|opportunit|formazione[\s\-_]*e[\s\-_]*lavoro", re.I)
# portali di selezione usati dalle aziende (link esterni ammessi)
RX_ATS = re.compile(
    r"inrecruiting|intervieweb|altamiraweb|allibo|factorialhr|teamtailor|personio|recruitee|workable|"
    r"jobylon|myworkdayjobs|smartrecruiters|join\.com|lever\.co|greenhouse\.io|zucchetti|taleo|successfactors|"
    r"softgarden|bamboohr|breezy|jobvite|ashbyhq|hirex|jobtome|inplace|talentia|cezanne|"
    r"jobs\.|careers\.|recruiting\.|lavoraconnoi\.", re.I)
RX_ESCLUDI = re.compile(r"facebook|instagram|twitter|x\.com|youtube|tiktok|privacy|cookie|informativa|"
                        r"\.pdf$|\.jpg$|\.png$|\.doc$|\.zip$", re.I)
PERCORSI = ["/lavora-con-noi", "/lavora-con-noi/", "/it/lavora-con-noi", "/it/lavora-con-noi/",
            "/careers", "/careers/", "/carriere", "/carriere/", "/jobs", "/jobs/",
            "/it/careers", "/en/careers", "/work-with-us", "/lavora-con-noi.html",
            "/azienda/lavora-con-noi", "/chi-siamo/lavora-con-noi", "/lavoro", "/lavoro/",
            "/posizioni-aperte", "/recruiting", "/join-us", "/persone", "/people",
            "/opportunita", "/candidature", "/risorse-umane", "/company/careers"]
# il contenuto della pagina deve parlare di lavoro, altrimenti non la accettiamo
RX_CONTENUTO = re.compile(r"lavora con noi|posizion[ei] apert|candidat|curriculum|\bcv\b|careers?|offerte di lavoro|"
                          r"work with us|open positions|selezioniamo|stiamo cercando|ricerchiamo|"
                          r"invia(re)? il tuo|unisciti|assunzion|colloqui|recruiting|vacanc|"
                          r"ricerca di personale|profili ricercati|annunci di lavoro", re.I)


def marchio(dom):
    """Dal dominio ricava il nome dell'azienda: careers.acme.it -> acme, acme.teamtailor.com -> teamtailor."""
    parti = dom.split(".")
    return parti[-2] if len(parti) >= 2 else dom


def punteggio(url, testo, titolo, dom_azienda):
    s = 0
    stringa = f"{testo} {titolo}"
    pezzi = urlparse(url)
    # anche il sottodominio conta: careers.azienda.it, lavoraconnoi.azienda.it
    percorso = pezzi.netloc + pezzi.path
    if RX_LINK.search(stringa):
        s += 3
    elif RX_LINK_DEBOLE.search(stringa):
        s += 2
    if RX_LINK.search(percorso):
        s += 2
    elif RX_LINK_DEBOLE.search(percorso):
        s += 1
    if s == 0:
        return 0
    d = dominio(url)
    if marchio(d) != dom_azienda:  # link esterno: ammesso solo se e' un portale di selezione
        if RX_ATS.search(url):
            s += 1
        elif "linkedin.com" in d and "/jobs" in url:
            s += 0
        else:
            return 0
    if RX_ESCLUDI.search(url):
        return 0
    if re.search(r"talent", stringa, re.I) and not re.search(r"lavor|career|job", stringa + url, re.I):
        s -= 2  # "talent" da solo e' spesso un prodotto o un evento
    return s


def scarica_xml(url):
    """Scarica una sitemap (il content-type dell'xml non passa da scarica())."""
    if not robots_ok(url):
        return None
    try:
        r = sessione().get(url, timeout=20)
    except Exception:
        return None
    if r.status_code >= 400 or len(r.content) > 8_000_000:
        return None
    return r.text


def da_sitemap(radice, lim, max_file=4):
    """Cerca indirizzi di pagine lavoro dentro la sitemap del sito. Poche richieste, buona resa."""
    da_vedere, visti, trovati = [radice + "/sitemap.xml", radice + "/sitemap_index.xml"], set(), []
    letti = 0
    while da_vedere and letti < max_file:
        u = da_vedere.pop(0)
        if u in visti:
            continue
        visti.add(u)
        lim.attendi(u)
        testo = scarica_xml(u)
        if not testo:
            continue
        letti += 1
        try:
            albero = ET.fromstring(testo.encode("utf-8", "ignore"))
        except ET.ParseError:
            continue
        indirizzi = [e.text.strip() for e in albero.iter() if e.tag.endswith("loc") and e.text]
        if albero.tag.endswith("sitemapindex"):
            # dentro un indice: apriamo prima le sitemap che sembrano contenere pagine utili
            utili = [x for x in indirizzi if re.search(r"page|pagin|post|lavor|career|job", x, re.I)]
            da_vedere += (utili or indirizzi)[:max_file]
            continue
        for x in indirizzi:
            if RX_ESCLUDI.search(x):
                continue
            if RX_LINK.search(urlparse(x).path):
                trovati.append(x)
    # prima gli indirizzi piu' corti: di solito sono la pagina principale e non un singolo annuncio
    trovati.sort(key=len)
    return trovati[:3]


def pagina_valida(url, lim):
    """Scarica la pagina e dice se parla davvero di lavoro. Ritorna (url_finale, html) o (None, motivo)."""
    lim.attendi(url)
    fin, html = scarica(url)
    if not fin:
        return None, html
    if not RX_CONTENUTO.search(" ".join(testo_visibile(html))[:30000]):
        return None, "contenuto non pertinente"
    return fin, html


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
    radice = f"{urlparse(base).scheme}://{urlparse(base).netloc}"
    marchio_az = marchio(dom)

    # 1) link nella home
    trovati = []
    for url, testo, titolo in link(html, base):
        p = punteggio(url, testo, titolo, marchio_az)
        if p >= 3:
            trovati.append((p, url.split("#")[0]))
    trovati.sort(key=lambda x: -x[0])
    candidati, visti = [], set()
    for _, u in trovati:
        if u not in visti and u.rstrip("/") != radice:
            visti.add(u)
            candidati.append(u)

    # 2) sitemap del sito
    if not candidati:
        for u in da_sitemap(radice, lim):
            if u not in visti:
                visti.add(u)
                candidati.append(u)

    # 3) indirizzi probabili
    if not candidati:
        for perc in PERCORSI:
            u = radice + perc
            if u not in visti:
                candidati.append(u)
            if len(candidati) >= 10:
                break

    # verifica: accettiamo la prima pagina che parla davvero di lavoro
    principale = html_pagina = None
    non_pertinenti = 0
    for u in candidati[:MAX_CANDIDATI]:
        if "linkedin.com" in u:
            principale, html_pagina = u, None
            break
        fin, h = pagina_valida(u, lim)
        if fin:
            principale, html_pagina = fin, h
            break
        if h == "contenuto non pertinente":
            non_pertinenti += 1
        if non_pertinenti >= 4:
            break

    if not principale:
        if len(" ".join(testo_visibile(html))) < 200:
            return {"esito_ricerca": "homepage senza testo (sito in JavaScript?): controllare a mano"}
        return {"esito_ricerca": "nessuna pagina lavoro trovata"}

    # se la pagina lavoro rimanda a un elenco "posizioni aperte" lo aggiungiamo
    extra = []
    if html_pagina:
        for url, testo, titolo in link(html_pagina, principale):
            if re.search(r"posizioni aperte|offerte|open positions|annunci|vacanc|opportunit|ricerche in corso",
                         f"{testo} {titolo}", re.I) \
                    and not RX_ESCLUDI.search(url) and url.split("#")[0] != principale:
                if dominio(url).split(".")[-2:] == dominio(principale).split(".")[-2:] or RX_ATS.search(url):
                    extra.append(url.split("#")[0])
    tutte = [principale] + [e for e in dict.fromkeys(extra) if e != principale][:2]
    esito = "solo LinkedIn (non monitorabile)" if any("linkedin.com" in u for u in tutte) else "trovata"
    return {"pagina_lavoro": " | ".join(tutte), "esito_ricerca": esito}


def da_controllare(aziende, a, oggi):
    out = []
    for az in aziende:
        if az.get("pagina_lavoro"):
            continue
        es = az.get("esito_ricerca", "")
        if es in ("scheda da leggere", "sito da trovare") or es.startswith("scartata"):
            continue
        if not es:
            out.append(az)
            continue
        vecchia = False
        if az.get("data_ricerca"):
            try:
                vecchia = (oggi - dt.date.fromisoformat(az["data_ricerca"])).days > 60
            except ValueError:
                pass
        if a.riprova_fallite and es.startswith(("nessuna pagina lavoro", "homepage senza testo")):
            if not az.get("data_ricerca"):
                out.append(az)
            else:
                try:
                    if (oggi - dt.date.fromisoformat(az["data_ricerca"])).days >= a.riprova_fallite:
                        out.append(az)
                except ValueError:
                    out.append(az)
        elif a.rifai and vecchia:
            out.append(az)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0, help="0 = nessun limite")
    ap.add_argument("--rifai", action="store_true")
    ap.add_argument("--riprova-fallite", type=int, default=0,
                    metavar="GIORNI", help="ricontrolla i fallimenti piu' vecchi di GIORNI giorni")
    ap.add_argument("--fetta", type=int, default=0)
    ap.add_argument("--di", type=int, default=1)
    ap.add_argument("--parziale", default="")
    ap.add_argument("--workers", type=int, default=24)
    a = ap.parse_args()
    if a.di > 1 and not a.parziale:
        sys.exit("con --di serve --parziale, altrimenti i job si sovrascrivono a vicenda")
    aziende = leggi_csv(ELENCO)
    oggi = dt.date.today()
    da_fare = da_controllare(aziende, a, oggi)
    if a.di > 1:  # fette a pettine: ogni job prende una azienda ogni N, cosi' il carico e' omogeneo
        da_fare = da_fare[a.fetta :: a.di]
    if a.max:
        da_fare = da_fare[: a.max]
    print(f"Aziende da controllare in questo giro: {len(da_fare)}"
          f"{f' (fetta {a.fetta + 1} di {a.di})' if a.di > 1 else ''}", flush=True)
    lim = Limitatore(1.5)
    risultati, fatti = [], 0

    def salva():
        if a.parziale:
            with open(a.parziale, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=CAMPI_PARZIALE, extrasaction="ignore")
                w.writeheader()
                w.writerows(risultati)
        else:
            scrivi_csv(ELENCO, aziende, CAMPI)

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        fut = {ex.submit(cerca_azienda, az, lim): az for az in da_fare}
        for f in as_completed(fut):
            az = fut[f]
            try:
                ris = f.result()
            except Exception as e:  # una singola azienda non deve fermare il giro
                ris = {"esito_ricerca": f"errore: {type(e).__name__}"}
            az.update(ris)
            az["data_ricerca"] = oggi.isoformat()
            risultati.append({"id": az["id"], "pagina_lavoro": az.get("pagina_lavoro", ""),
                              "esito_ricerca": az["esito_ricerca"], "data_ricerca": az["data_ricerca"]})
            fatti += 1
            if fatti % 100 == 0:
                trovate = sum(1 for x in risultati if x["pagina_lavoro"])
                print(f"  {fatti}/{len(da_fare)} - pagine trovate {trovate}", flush=True)
                salva()
    salva()
    trovate = sum(1 for x in risultati if x["pagina_lavoro"])
    print(f"Fatto. Controllate {len(da_fare)}, pagine lavoro trovate in questo giro: {trovate}")
    if not a.parziale:
        print(f"Aziende con pagina lavoro in totale: {sum(1 for x in aziende if x.get('pagina_lavoro'))} su {len(aziende)}")


if __name__ == "__main__":
    main()
