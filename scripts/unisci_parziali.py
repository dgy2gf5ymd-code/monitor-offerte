"""Riunisce nell'elenco aziende i risultati dei job paralleli di ricerca pagine.

Uso: python scripts/unisci_parziali.py parziali/*.csv

Ogni job paralleli scrive un file con le sole colonne id, pagina_lavoro, esito_ricerca,
data_ricerca. Qui vengono riportati nell'elenco, riconoscendo le aziende dall'id.
Un id presente in piu' file viene applicato una volta sola (vince il risultato con la pagina).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from comune import leggi_csv, scrivi_csv  # noqa: E402
from scopri_pagine import CAMPI, ELENCO  # noqa: E402


def main():
    file = [Path(x) for x in sys.argv[1:]]
    if not file:
        sys.exit("indicare almeno un file parziale")
    aziende = leggi_csv(ELENCO)
    per_id = {x["id"]: x for x in aziende}
    migliori = {}
    letti = 0
    for f in file:
        if not f.exists() or f.stat().st_size == 0:
            print(f"  {f.name}: assente o vuoto, salto")
            continue
        righe = leggi_csv(f)
        letti += 1
        print(f"  {f.name}: {len(righe)} risultati")
        for r in righe:
            vecchio = migliori.get(r["id"])
            if vecchio and vecchio.get("pagina_lavoro") and not r.get("pagina_lavoro"):
                continue  # non sovrascrivere un risultato buono con uno vuoto
            migliori[r["id"]] = r
    applicati = ignorati = 0
    for id_, r in migliori.items():
        az = per_id.get(id_)
        if az is None:
            ignorati += 1
            continue
        az["pagina_lavoro"] = r.get("pagina_lavoro", "")
        az["esito_ricerca"] = r.get("esito_ricerca", "")
        az["data_ricerca"] = r.get("data_ricerca", "")
        applicati += 1
    scrivi_csv(ELENCO, aziende, CAMPI)
    trovate = sum(1 for x in aziende if x.get("pagina_lavoro"))
    print(f"File letti: {letti}; risultati applicati: {applicati}"
          + (f"; id non piu' presenti nell'elenco: {ignorati}" if ignorati else ""))
    print(f"Aziende con pagina lavoro: {trovate} su {len(aziende)}")


if __name__ == "__main__":
    main()
