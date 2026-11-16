"""Trova il sito web delle aziende che hanno la partita IVA ma non il sito.

Uso: python scripts/trova_siti.py [--max 1500]

Per ogni azienda prova gli indirizzi più probabili ricavati dal nome (nome.it, nome.com, ...)
e accetta un sito solo se la sua pagina contiene la partita IVA dell'azienda
(obbligatoria per legge sui siti delle società italiane). Nessun motore di ricerca.
"""
import argparse
import re
import socket
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from comune import link, scarica, leggi_csv, scrivi_csv  # noqa: E402
from scopri_pagine import CAMPI, ELENCO  # noqa: E402

FORME = r"\b(s\.?r\.?l\.?s?|s\.?p\.?a\.?|s\.?a\.?s\.?|s\.?n\.?c\.?|societa|soc|coop|cooperativa|unipersonale|" \
        r"a responsabilita limitata|per azioni|semplificata|con socio unico|in liquidazione|di|e|&|c|f\.?lli|fratelli|group|" \
        r"italia|italy|industria|industrie|the)\b"


def parole(nome):
    s = unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)|in sigla.*|con sigla.*|o in forma abbreviata.*", " ", s)
    s = s.replace("'", "")
    s = re.sub(FORME, " ", s)
    return [w for w in re.split(r"[^a-z0-9]+", s) if w]


def candidati(nome):
    w = parole(nome)
    if not w:
        return []
    basi = []
    for b in ["".join(w[:3]), "".join(w[:2]), w[0], "-".join(w[:2]), "".join(w)]:
        if len(b) >= 3 and b not in basi:
            basi.append(b)
    out = []
    for b in basi[:4]:
        for tld in (".it", ".com", ".eu", ".net"):
            out.append(b + tld)
    return out


def esiste(dominio):
    try:
        socket.getaddrinfo(dominio, 443)
        return True
    except OSError:
        try:
            socket.getaddrinfo("www." + dominio, 443)
            return True
        except OSError:
            return False


def contiene_piva(html, piva):
    t = re.sub(r"[\s.\-]", "", html)
    return piva in t


def verifica(az):
    piva = re.sub(r"\D", "", az.get("piva", ""))
    if len(piva) != 11:
        return None
    for d in candidati(az["nome"]):
        if not esiste(d):
            continue
        for u in (f"https://www.{d}", f"https://{d}"):
            fin, html = scarica(u)
            if not fin:
                continue
            if contiene_piva(html, piva):
                return fin
            # la partita IVA a volte è solo nella pagina contatti o privacy
            for url, testo, _ in link(html, fin)[:200]:
                if re.search(r"contatt|contact|privacy|note legali|chi siamo|azienda", f"{testo} {url}", re.I) and d in url:
                    f2, h2 = scarica(url)
                    if f2 and contiene_piva(h2, piva):
                        return fin
                    break
            break
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=1500)
    a = ap.parse_args()
    aziende = leggi_csv(ELENCO)
    da_fare = [x for x in aziende if x.get("esito_ricerca") == "sito da trovare" and x.get("piva")][: a.max]
    print(f"Aziende di cui cercare il sito: {len(da_fare)}")
    trovati = 0
    with ThreadPoolExecutor(max_workers=16) as ex:
        fut = {ex.submit(verifica, x): x for x in da_fare}
        for i, f in enumerate(as_completed(fut), 1):
            x = fut[f]
            try:
                sito = f.result()
            except Exception:
                sito = None
            if sito:
                x["sito"] = sito
                x["esito_ricerca"] = ""  # pronta per la ricerca della pagina lavoro
                trovati += 1
            else:
                x["esito_ricerca"] = "sito non trovato (da cercare a mano)"
            if i % 200 == 0:
                print(f"  {i}/{len(da_fare)} - siti trovati {trovati}")
                scrivi_csv(ELENCO, aziende, CAMPI)
    scrivi_csv(ELENCO, aziende, CAMPI)
    print(f"Siti trovati e verificati con la partita IVA: {trovati} su {len(da_fare)}")


if __name__ == "__main__":
    main()
