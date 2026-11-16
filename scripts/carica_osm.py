"""Aggiunge a dati/aziende.csv le attività con sito web presenti su OpenStreetMap (dati aperti, licenza ODbL).

Uso: python scripts/carica_osm.py [--province PD,VI,RO,VE,TV]

Prende uffici, stabilimenti, laboratori artigiani e hotel che hanno un sito web.
Le aziende già presenti (stesso sito) non vengono duplicate.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from comune import UA, leggi_csv, scrivi_csv  # noqa: E402
from scopri_pagine import CAMPI, ELENCO  # noqa: E402

SERVER = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter",
          "https://maps.mail.ru/osm/tools/overpass/api/interpreter"]
# siti di catene, enti e portali che non sono aziende da controllare
ESCLUDI = re.compile(r"facebook|instagram|google|booking\.com|tripadvisor|poste|enel|eni\.com|comune\.|\.gov\.it|"
                     r"carabinieri|polizia|unipd|wikipedia|linktr|paginegialle|virgilio", re.I)


def interroga(prov):
    q = f"""[out:json][timeout:180];
area["ISO3166-2"="IT-{prov}"]->.a;
(
  nwr(area.a)["office"]["website"];
  nwr(area.a)["office"]["contact:website"];
  nwr(area.a)["industrial"]["website"];
  nwr(area.a)["man_made"="works"]["website"];
  nwr(area.a)["craft"]["website"];
  nwr(area.a)["tourism"="hotel"]["website"];
);
out tags;"""
    attesa = 30
    for tentativo in range(6):
        url = SERVER[tentativo % len(SERVER)]
        try:
            r = requests.post(url, data={"data": q}, headers={"User-Agent": UA}, timeout=240)
            if r.status_code == 200:
                return r.json().get("elements", [])
            print(f"  {prov}: {url} risponde {r.status_code}, riprovo tra {attesa}s")
        except Exception as e:
            print(f"  {prov}: errore {type(e).__name__}, riprovo tra {attesa}s")
        time.sleep(attesa)
        attesa = min(attesa * 2, 300)
    return []


def dominio(u):
    return re.sub(r"^https?://(www\.)?", "", u.strip().lower()).split("/")[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--province", default="PD,VI,RO,VE,TV")
    a = ap.parse_args()
    aziende = leggi_csv(ELENCO) if ELENCO.exists() else []
    noti = {dominio(x["sito"]) for x in aziende if x.get("sito")}
    nuove = 0
    for prov in a.province.split(","):
        el = interroga(prov.strip())
        print(f"Provincia {prov}: {len(el)} elementi OSM con sito")
        for e in el:
            t = e.get("tags", {})
            nome = t.get("name") or t.get("operator") or ""
            sito = (t.get("website") or t.get("contact:website") or "").strip()
            if not nome or not sito or ESCLUDI.search(sito):
                continue
            if not sito.startswith("http"):
                sito = "https://" + sito
            d = dominio(sito)
            if not d or d in noti:
                continue
            noti.add(d)
            tipo = t.get("office") or t.get("industrial") or t.get("craft") or t.get("tourism") or t.get("man_made") or ""
            aziende.append({"id": f"osm-{e.get('type', 'x')[0]}{e.get('id')}", "nome": nome,
                            "comune": t.get("addr:city", ""), "settore": f"OSM: {tipo}", "sito": sito,
                            "fonte": f"OpenStreetMap ({prov})", "pagina_lavoro": "", "esito_ricerca": "",
                            "data_ricerca": "", "note": ""})
            nuove += 1
        time.sleep(10)
    scrivi_csv(ELENCO, aziende, CAMPI)
    print(f"Aggiunte {nuove} attività da OpenStreetMap; totale elenco {len(aziende)}")


if __name__ == "__main__":
    main()
