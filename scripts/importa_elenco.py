"""Aggiunge aziende a dati/aziende.csv da un altro elenco CSV (es. elenco acquistato dalla Camera di Commercio).

Uso: python scripts/importa_elenco.py elenco.csv --fonte "Camera di Commercio" [--min-dipendenti 20]

Il CSV deve avere almeno una colonna con il nome dell'azienda. Le colonne vengono riconosciute
dal titolo (nome/ragione sociale, comune, sito/web, dipendenti/addetti, settore/ateco, partita iva).
Le aziende già presenti (stessa partita IVA, stesso sito o stesso nome) non vengono duplicate.
"""
import argparse
import csv
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from comune import leggi_csv, scrivi_csv  # noqa: E402
from scopri_pagine import CAMPI, ELENCO  # noqa: E402


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(s\.?r\.?l\.?s?|s\.?p\.?a\.?|s\.?a\.?s\.?|s\.?n\.?c\.?|societa|soc|coop|unipersonale)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def trova(colonne, *parole):
    for c in colonne:
        if any(p in c.lower() for p in parole):
            return c
    return None


def numero_dipendenti(v):
    """'da 20 a 49' -> 20, '35' -> 35, '' -> None"""
    m = re.findall(r"\d+", v or "")
    return int(m[0]) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--fonte", default="elenco importato")
    ap.add_argument("--min-dipendenti", type=int, default=20)
    a = ap.parse_args()
    with open(a.file, newline="", encoding="utf-8-sig") as f:
        sniff = csv.Sniffer().sniff(f.read(4096), delimiters=",;\t")
        f.seek(0)
        nuove = list(csv.DictReader(f, dialect=sniff))
    col = list(nuove[0].keys())
    c_nome = trova(col, "ragione", "denominazione", "nome", "azienda")
    c_com = trova(col, "comune", "citt", "localit")
    c_sito = trova(col, "sito", "web", "url")
    c_dip = trova(col, "dipendent", "addett")
    c_set = trova(col, "settore", "ateco", "attivit")
    c_iva = trova(col, "partita", "p.iva", "piva", "codice fiscale")
    if not c_nome:
        sys.exit("Non trovo la colonna con il nome dell'azienda")
    aziende = leggi_csv(ELENCO)
    noti = {norm(x["nome"]) for x in aziende} | {x["id"] for x in aziende}
    siti = {re.sub(r"^https?://(www\.)?", "", x["sito"]).split("/")[0] for x in aziende if x.get("sito")}
    aggiunte = scartate = 0
    for r in nuove:
        nome = (r.get(c_nome) or "").strip()
        if not nome:
            continue
        dip = numero_dipendenti(r.get(c_dip)) if c_dip else None
        if dip is not None and dip < a.min_dipendenti:
            scartate += 1
            continue
        sito = (r.get(c_sito) or "").strip() if c_sito else ""
        if sito and not sito.startswith("http"):
            sito = "https://" + sito
        dom = re.sub(r"^https?://(www\.)?", "", sito).split("/")[0]
        iva = (r.get(c_iva) or "").strip() if c_iva else ""
        if norm(nome) in noti or (iva and iva in noti) or (dom and dom in siti):
            continue
        aziende.append({"id": iva or norm(nome)[:40], "nome": nome, "comune": (r.get(c_com) or "").strip() if c_com else "",
                        "settore": (r.get(c_set) or "").strip() if c_set else "", "sito": sito, "fonte": a.fonte,
                        "pagina_lavoro": "", "esito_ricerca": "" if sito else "sito mancante", "data_ricerca": "",
                        "note": f"dipendenti: {r.get(c_dip)}" if c_dip else ""})
        noti.add(norm(nome))
        aggiunte += 1
    scrivi_csv(ELENCO, aziende, CAMPI)
    print(f"Aggiunte {aggiunte} aziende; scartate {scartate} sotto i {a.min_dipendenti} dipendenti; totale {len(aziende)}")
    print("Le aziende senza sito vanno completate a mano (colonna 'sito') per poter essere controllate.")


if __name__ == "__main__":
    main()
