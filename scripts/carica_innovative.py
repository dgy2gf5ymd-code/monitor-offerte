"""Aggiunge a dati/aziende.csv le startup e PMI innovative delle province scelte
(elenco ufficiale scaricabile dal Registro Imprese, sezione speciale).

Uso: python scripts/carica_innovative.py [--province PD,VI,VE,TV,RO]
"""
import argparse
import io
import re
import sys
import unicodedata
import zipfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from comune import leggi_csv, scrivi_csv, sessione  # noqa: E402
from scopri_pagine import CAMPI, ELENCO  # noqa: E402

FILE = {"PMI innovative": "https://startup.registroimprese.it/isin/report?fileId=pminnovative.zip",
        "Startup innovative": "https://startup.registroimprese.it/isin/report?fileId=startup.zip"}


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\b(s\.?r\.?l\.?s?|s\.?p\.?a\.?|societa|soc|unipersonale|semplificata)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def tabelle(contenuto):
    """Restituisce i DataFrame trovati nel file scaricato (zip con xls/xlsx/csv)."""
    out = []
    try:
        z = zipfile.ZipFile(io.BytesIO(contenuto))
        nomi = z.namelist()
    except zipfile.BadZipFile:
        z, nomi = None, ["file"]
    for n in nomi:
        dati = z.read(n) if z else contenuto
        low = n.lower()
        try:
            if low.endswith(".csv"):
                out.append(pd.read_csv(io.BytesIO(dati), sep=None, engine="python", header=None, dtype=str))
            else:
                for df in pd.read_excel(io.BytesIO(dati), header=None, dtype=str, sheet_name=None).values():
                    out.append(df)
        except Exception as e:
            print(f"  {n}: non leggibile ({type(e).__name__})")
    return out


def con_intestazione(df):
    for i in range(min(30, len(df))):
        riga = [str(x).strip().lower() for x in df.iloc[i].tolist()]
        if any("denominazione" in c for c in riga):
            d = df.iloc[i + 1:].copy()
            d.columns = riga
            return d
    return None


def colonna(cols, *chiavi):
    for c in cols:
        if any(k in c for k in chiavi):
            return c
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--province", default="PD,VI,VE,TV,RO")
    a = ap.parse_args()
    prov = {p.strip().upper() for p in a.province.split(",")}
    aziende = leggi_csv(ELENCO) if ELENCO.exists() else []
    nomi = {norm(x["nome"]) for x in aziende}
    domini = {re.sub(r"^https?://(www\.)?", "", x["sito"].lower()).split("/")[0] for x in aziende if x.get("sito")}
    nuove = 0
    for tipo, url in FILE.items():
        try:
            r = sessione().get(url, timeout=120)
        except Exception as e:
            print(f"{tipo}: download non riuscito ({type(e).__name__})")
            continue
        print(f"{tipo}: scaricati {len(r.content)} byte ({r.headers.get('content-type')})")
        for df in tabelle(r.content):
            d = con_intestazione(df)
            if d is None:
                continue
            cols = list(d.columns)
            c_nome = colonna(cols, "denominazione")
            c_pv = colonna(cols, "pv", "prov")
            c_com = colonna(cols, "comune")
            c_cf = colonna(cols, "codice fiscale", "partita iva", "cf")
            c_web = colonna(cols, "sito", "web", "internet")
            c_set = colonna(cols, "settore", "ateco")
            c_add = colonna(cols, "addetti")
            print(f"  colonne riconosciute: nome={c_nome} prov={c_pv} sito={c_web} cf={c_cf}")
            for _, r in d.iterrows():
                if c_pv and str(r.get(c_pv, "")).strip().upper() not in prov:
                    continue
                nome = str(r.get(c_nome, "") or "").strip()
                if not nome or nome.lower() == "nan" or norm(nome) in nomi:
                    continue
                sito = str(r.get(c_web, "") or "").strip() if c_web else ""
                if sito.lower() in ("nan", "none"):
                    sito = ""
                if sito and not sito.startswith("http"):
                    sito = "https://" + sito
                dom = re.sub(r"^https?://(www\.)?", "", sito.lower()).split("/")[0]
                if dom and dom in domini:
                    continue
                cf = re.sub(r"\D", "", str(r.get(c_cf, "") or "")) if c_cf else ""
                aziende.append({"id": f"inn-{cf or norm(nome)[:30]}", "nome": nome,
                                "comune": str(r.get(c_com, "") or "").strip() if c_com else "",
                                "settore": str(r.get(c_set, "") or "")[:60] if c_set else tipo,
                                "sito": sito, "fonte": f"Registro {tipo}", "pagina_lavoro": "",
                                "esito_ricerca": "" if sito else ("sito da trovare" if len(cf) == 11 else "sito mancante"),
                                "data_ricerca": "",
                                "note": f"addetti: {r.get(c_add)}" if c_add else "", "piva": cf if len(cf) == 11 else ""})
                nomi.add(norm(nome))
                if dom:
                    domini.add(dom)
                nuove += 1
    scrivi_csv(ELENCO, aziende, CAMPI)
    print(f"Aggiunte {nuove} imprese innovative; totale elenco {len(aziende)}")


if __name__ == "__main__":
    main()
