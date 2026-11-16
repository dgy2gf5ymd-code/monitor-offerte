"""Aggiunge a dati/aziende.csv le società con fatturato almeno 2 milioni dall'elenco pubblico di aziende.it
(dati del Registro Imprese), poi legge la scheda di ognuna per partita IVA e numero di dipendenti.

Uso: python scripts/carica_aziende_it.py [--province padova,vicenza,venezia,treviso,rovigo]
                                         [--min-dipendenti 20] [--max-schede 2000]

L'elenco per provincia è ordinato per fatturato (fino a 5.000 aziende per provincia).
Le schede vengono lette a blocchi (--max-schede per giro): le aziende ancora da leggere
restano segnate 'scheda da leggere' e vengono completate nei giri successivi.
"""
import argparse
import re
import sys
import time
import unicodedata
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from comune import leggi_csv, scarica, scrivi_csv  # noqa: E402
from scopri_pagine import CAMPI, ELENCO  # noqa: E402

BASE = "https://www.aziende.it"
FASCE_OK = ("milioni",)  # le fasce sotto il milione sono scritte in migliaia
FASCE_NO = re.compile(r"^\s*(0-1|1-2)\s*milion", re.I)


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(s\.?r\.?l\.?s?|s\.?p\.?a\.?|s\.?a\.?s\.?|s\.?n\.?c\.?|societa|soc|coop|cooperativa|unipersonale|"
               r"a responsabilita limitata|per azioni|semplificata|in sigla.*|con socio unico)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def fascia_ok(f):
    f = f.replace("&gt;", ">").strip()
    if not f or "milion" not in f.lower():
        return False
    return not FASCE_NO.search(f)


def leggi_elenco(prov, pausa):
    out = []
    for pag in range(1, 101):
        url = f"{BASE}/veneto/{prov}/" + (f"?pag={pag}" if pag > 1 else "")
        fin, html = scarica(url)
        if not fin:
            print(f"  {prov} pagina {pag}: {html}")
            break
        soup = BeautifulSoup(html, "html.parser")
        righe = soup.select("div.row.border-bottom")
        if not righe:
            break
        sotto = 0
        for r in righe:
            a = r.find("a", href=True)
            col = r.find_all("div", recursive=False)
            if not a or len(col) < 5:
                continue
            fascia = col[1].get_text(" ", strip=True)
            comune = col[4].get_text(" ", strip=True)
            if not fascia_ok(fascia):
                sotto += 1
                continue
            out.append({"slug": a["href"].strip("/"), "nome": a.get_text(" ", strip=True), "fascia": fascia,
                        "ateco": col[2].get_text(" ", strip=True), "comune": comune})
        if sotto == len(righe):  # elenco ordinato per fatturato: da qui in poi sono tutte sotto soglia
            break
        time.sleep(pausa)
    return out


def leggi_scheda(slug):
    fin, html = scarica(f"{BASE}/{slug}")
    if not fin:
        return None
    t = re.sub(r"\s+", " ", BeautifulSoup(html, "html.parser").get_text(" "))
    piva = re.search(r"P\.?\s?IVA\s*(\d{11})", t)
    dip = re.search(r"([\d.]+)\s+Dipendenti", t)
    attiva = not re.search(r"\b(Cessata|In liquidazione|Fallita)\b", t[:600], re.I)
    return {"piva": piva.group(1) if piva else "",
            "dipendenti": int(dip.group(1).replace(".", "")) if dip else None,
            "attiva": attiva}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--province", default="padova,vicenza,venezia,treviso,rovigo")
    ap.add_argument("--min-dipendenti", type=int, default=20)
    ap.add_argument("--max-schede", type=int, default=2000)
    a = ap.parse_args()
    aziende = leggi_csv(ELENCO) if ELENCO.exists() else []
    nomi = {norm(x["nome"]) for x in aziende}
    ids = {x["id"] for x in aziende}
    nuove = 0
    for prov in a.province.split(","):
        prov = prov.strip()
        elenco = leggi_elenco(prov, 1.0)
        print(f"Provincia {prov}: {len(elenco)} società con fatturato da 2 milioni in su")
        for x in elenco:
            id_ = "azit-" + x["slug"]
            if id_ in ids or norm(x["nome"]) in nomi:
                continue
            aziende.append({"id": id_, "nome": x["nome"], "comune": x["comune"], "settore": f"ATECO {x['ateco']}",
                            "sito": "", "fonte": f"aziende.it ({prov})", "pagina_lavoro": "",
                            "esito_ricerca": "scheda da leggere", "data_ricerca": "",
                            "note": f"fatturato {x['fascia']}", "piva": ""})
            ids.add(id_)
            nomi.add(norm(x["nome"]))
            nuove += 1
    print(f"Società nuove da aziende.it: {nuove}")
    # lettura schede (partita IVA e dipendenti), a blocchi
    da_leggere = [x for x in aziende if x.get("esito_ricerca") == "scheda da leggere"][: a.max_schede]
    tenute = scartate = 0
    for i, x in enumerate(da_leggere, 1):
        s = leggi_scheda(x["id"].removeprefix("azit-"))
        if s is None:
            continue
        if not s["attiva"] or (s["dipendenti"] is not None and s["dipendenti"] < a.min_dipendenti):
            x["esito_ricerca"] = "scartata (sotto soglia dipendenti o non attiva)"
            scartate += 1
        else:
            x["piva"] = s["piva"]
            x["note"] = (x.get("note", "") + (f"; {s['dipendenti']} dipendenti" if s["dipendenti"] is not None else "")).strip("; ")
            x["esito_ricerca"] = "sito da trovare"
            tenute += 1
        if i % 200 == 0:
            print(f"  schede lette {i}/{len(da_leggere)}")
            scrivi_csv(ELENCO, aziende, CAMPI)
        time.sleep(1.0)
    scrivi_csv(ELENCO, aziende, CAMPI)
    rimaste = sum(1 for x in aziende if x.get("esito_ricerca") == "scheda da leggere")
    print(f"Schede lette: tenute {tenute}, scartate {scartate}; ancora da leggere {rimaste}; totale elenco {len(aziende)}")


if __name__ == "__main__":
    main()
