"""Aggiunge a dati/aziende.csv le aziende associate a Confindustria Vicenza (elenco pubblico sul sito).

Uso: python scripts/carica_confindustria_vicenza.py [--min-dipendenti 20]

Per ogni azienda legge la scheda pubblica: sito web, pagina lavoro (se indicata), numero dipendenti.
Scarta le aziende con fascia dipendenti sotto la soglia; tiene quelle senza dato.
"""
import argparse
import re
import string
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from comune import leggi_csv, scrivi_csv, sessione  # noqa: E402
from scopri_pagine import CAMPI, ELENCO  # noqa: E402

API = "https://doc.confindustria.vicenza.it/companies"


def get_json(url):
    for tentativo in range(3):
        try:
            r = sessione().get(url, timeout=30)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        time.sleep(5 * (tentativo + 1))
    return None


def soglia_ok(fascia, minimo):
    """'da 21 a 50 dipendenti' -> ok se il massimo della fascia è >= minimo; senza dato -> ok"""
    n = [int(x) for x in re.findall(r"\d+", fascia or "")]
    if not n:
        return True
    return max(n) >= minimo


def dominio(u):
    return re.sub(r"^https?://(www\.)?", "", (u or "").strip().lower()).split("/")[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-dipendenti", type=int, default=20)
    a = ap.parse_args()
    aziende = leggi_csv(ELENCO) if ELENCO.exists() else []
    ids = {x["id"] for x in aziende}
    scarti_f = Path(__file__).resolve().parent.parent / "dati" / "vicenza_scartate.txt"
    scarti = set(scarti_f.read_text().split()) if scarti_f.exists() else set()
    ids |= scarti
    domini = {dominio(x["sito"]) for x in aziende if x.get("sito")}
    elenco = []
    for lettera in list(string.ascii_lowercase) + ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]:
        pagina = 1
        while True:
            j = get_json(f"{API}/alphabetically/{lettera}?page={pagina}")
            if not j or not j.get("data"):
                break
            elenco += j["data"]
            tot = j.get("pagination", {}).get("totalPages", 1)
            if pagina >= tot:
                break
            pagina += 1
            time.sleep(0.3)
    print(f"Aziende Confindustria Vicenza nell'elenco: {len(elenco)}")
    nuove = scartate = 0
    for x in elenco:
        id_ = f"cvi-{x.get('id')}"
        if id_ in ids:
            continue
        j = get_json(API + x["slug"].replace("/aziende", "", 1))
        c = (j or {}).get("company") or {}
        fascia = c.get("employees_nr") or ""
        if not soglia_ok(fascia, a.min_dipendenti):
            scartate += 1
            scarti.add(id_)
            continue
        sito = (c.get("website") or "").strip()
        if sito and not sito.startswith("http"):
            sito = "https://" + sito
        if sito and dominio(sito) in domini:
            scarti.add(id_)
            continue
        lavoro = (c.get("websitejob") or "").strip()
        if lavoro and not lavoro.startswith("http"):
            lavoro = "https://" + lavoro
        aziende.append({"id": id_, "nome": c.get("name") or x.get("title", ""),
                        "comune": c.get("municipality") or x.get("municipality", ""),
                        "settore": (x.get("commercial_sector_slug") or "").replace("-", " "),
                        "sito": sito, "fonte": "Confindustria Vicenza",
                        "pagina_lavoro": lavoro, "esito_ricerca": "pagina indicata da Confindustria" if lavoro else ("" if sito else "sito mancante"),
                        "data_ricerca": "", "note": "; ".join(v for v in [fascia, c.get("revenue") or ""] if v)})
        ids.add(id_)
        if sito:
            domini.add(dominio(sito))
        nuove += 1
        time.sleep(0.3)
    scrivi_csv(ELENCO, aziende, CAMPI)
    scarti_f.write_text("\n".join(sorted(scarti)))
    print(f"Aggiunte {nuove} aziende; scartate {scartate} sotto i {a.min_dipendenti} dipendenti; totale elenco {len(aziende)}")


if __name__ == "__main__":
    main()
