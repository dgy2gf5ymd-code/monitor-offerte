"""Aggiorna dati/aziende.csv con le aziende associate a Confindustria Veneto Est (portale BeCOME).

Uso: python scripts/carica_confindustria.py [--provincia Padova]

Legge l'elenco pubblico per gruppo merceologico, tiene le aziende della provincia scelta
e per quelle nuove apre la scheda per prendere il sito web. Le aziende già presenti
non vengono toccate (si conservano pagina lavoro ed esiti).
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from comune import leggi_csv, scarica, scrivi_csv  # noqa: E402
from scopri_pagine import CAMPI, ELENCO  # noqa: E402

BASE = "https://bcomcve.confindustriavenest.it"
GRUPPI = {1: "Agroalimentare", 2: "Alimentari", 3: "Calzature", 4: "Carta e stampa", 5: "Chimica e farmaceutica",
          6: "Commercio", 7: "Gomma e plastica", 8: "ICT", 9: "Legno e arredo", 10: "Edilizia e materiali",
          11: "Meccanica ed elettronica", 12: "Sanità", 13: "Servizi", 14: "Altro", 15: "Moda e tessile",
          16: "Media e telecomunicazioni", 17: "Trasporti e logistica", 18: "Turismo", 19: "Energia e ambiente",
          20: "Vetro e ceramica", 21: "Vino e bevande"}
WRAP = {"ShallowReactive", "Reactive", "Ref", "ShallowRef", "EmptyRef"}


def nuxt(payload_text):
    """Decodifica il formato __NUXT_DATA__ (array con riferimenti per indice)."""
    P = json.loads(payload_text)
    memo = {}
    sys.setrecursionlimit(20000)

    def r(i):
        if not isinstance(i, int) or isinstance(i, bool):
            return i
        if i in memo:
            return memo[i]
        v = P[i]
        if isinstance(v, list):
            if v and isinstance(v[0], str) and v[0] in WRAP:
                memo[i] = r(v[1])
                return memo[i]
            a = []
            memo[i] = a
            a.extend(r(x) for x in v)
            return a
        if isinstance(v, dict):
            o = {}
            memo[i] = o
            for k, x in v.items():
                o[k] = r(x)
            return o
        memo[i] = v
        return v

    return r(0)["data"]


def dati_pagina(html):
    s = BeautifulSoup(html, "html.parser").find("script", id="__NUXT_DATA__")
    return nuxt(s.string) if s and s.string else {}


def trova_marker(o, prof=0):
    if prof > 6 or not isinstance(o, (dict, list)):
        return None
    if isinstance(o, list) and len(o) > 0 and isinstance(o[0], dict) and "slug" in o[0] and "district" in o[0]:
        return o
    valori = o.values() if isinstance(o, dict) else o
    for v in valori:
        x = trova_marker(v, prof + 1)
        if x is not None:
            return x
    return None


def attributi_scheda(d):
    for v in d.values():
        if isinstance(v, dict) and isinstance(v.get("data"), list) and v["data"]:
            at = v["data"][0].get("attributes") if isinstance(v["data"][0], dict) else None
            if isinstance(at, dict) and "website" in at:
                return at
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provincia", default="Padova")
    a = ap.parse_args()
    trovate = {}
    for g, nome_g in GRUPPI.items():
        fin, html = scarica(f"{BASE}/search-results?productGroup={g}")
        if not fin:
            print(f"gruppo {g}: {html}")
            continue
        for m in trova_marker(dati_pagina(html)) or []:
            if re.search(a.provincia, m.get("district") or "", re.I):
                x = trovate.setdefault(m["slug"], {"nome": m.get("name", ""), "comune": m.get("city", ""), "gruppi": []})
                if nome_g not in x["gruppi"]:
                    x["gruppi"].append(nome_g)
        time.sleep(1)
    print(f"Aziende Confindustria in provincia di {a.provincia}: {len(trovate)}")
    aziende = leggi_csv(ELENCO) if ELENCO.exists() else []
    per_id = {x["id"]: x for x in aziende}
    nuove = 0
    for slug, x in trovate.items():
        if slug in per_id:
            per_id[slug]["settore"] = "; ".join(x["gruppi"])
            continue
        fin, html = scarica(f"{BASE}/aziende/{slug}")
        sito = ""
        if fin:
            sito = (attributi_scheda(dati_pagina(html)).get("website") or "").strip()
            if sito and not sito.startswith("http"):
                sito = "https://" + sito
        aziende.append({"id": slug, "nome": x["nome"], "comune": x["comune"], "settore": "; ".join(x["gruppi"]),
                        "sito": sito, "fonte": "Confindustria Veneto Est", "pagina_lavoro": "",
                        "esito_ricerca": "" if sito else "sito mancante", "data_ricerca": "", "note": ""})
        nuove += 1
        time.sleep(0.5)
    ELENCO.parent.mkdir(exist_ok=True)
    scrivi_csv(ELENCO, aziende, CAMPI)
    print(f"Aggiunte {nuove} aziende nuove; totale elenco {len(aziende)}")


if __name__ == "__main__":
    main()
