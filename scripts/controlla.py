"""Controlla le pagine "lavora con noi" e segnala le righe nuove legate al marketing.

Uso: python scripts/controlla.py [--senza-browser]

Per ogni pagina salva le impronte delle righe di testo (dati/stato.json).
Al giro successivo le righe che non c'erano prima e che contengono parole
del marketing finiscono in risultati/novita_AAAA-MM-GG.csv e .md.
Quando una pagina viene letta per la prima volta le righe marketing che contiene
sono comunque nuove per chi legge il riepilogo, quindi vengono segnalate come
"NUOVA (prima lettura della pagina)" e non nascoste.
"""
import argparse
import csv
import datetime as dt
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from comune import Limitatore, impronta, leggi_csv, scarica, testo_visibile  # noqa: E402

RADICE = Path(__file__).resolve().parent.parent
ELENCO = RADICE / "dati" / "aziende.csv"
STATO = RADICE / "dati" / "stato.json"
RISULTATI = RADICE / "risultati"

# una riga conta se parla di marketing E sembra un ruolo/annuncio (non menu, cookie o privacy)
RX_MKT = re.compile(r"marketing|comunicazione|communication|social[\s\-]?media|content[\s\-]?(creator|manager|specialist|marketing|editor|strategist)|"
    r"copywrit|digital[\s\-]?(marketing|specialist|media|content|communication|pr\b|advertising|strategist)|"
    r"brand[\s\-]?(manager|specialist|ambassador|activation|marketing|designer)|grafic[oa]|graphic|"
    r"(event|eventi)[\s\-]?(manager|specialist|coordinator|planner|organizer)|organizzazione eventi|"
    r"ufficio stampa|press office|public relations|relazioni esterne|\bpr (specialist|manager)|"
    r"e-?commerce|\bcrm\b|\bseo\b|\bsem\b|advertising|performance marketing|community manager|influencer|"
    r"customer experience|web ?content|trade marketing|product marketing|media ?planner|video ?maker|creative", re.I)
RX_RUOLO = re.compile(r"specialist|manager|addett|responsabile|coordinat|assistant|assistente|junior|senior|stage|stagista|"
    r"tirocin|internship|intern\b|impiegat|designer|copywriter|creator|account|executive|officer|analyst|"
    r"operator|collaborat|figura|cerchiamo|ricerchiamo|selezioniamo|posizione|\(m/f\)|m/f|full[\s\-]?time|"
    r"part[\s\-]?time|lead\b|editor|strategist|planner|consultant|consulente|esperto|esperta|referente|"
    r"apprendist|tecnico|expert|owner|director", re.I)
RX_RUMORE = re.compile(r"cookie|privacy|newsletter|iscri|seguici|follow us|copyright|©|p\.?\s?iva|cooperativa sociale|"
    r"ragione sociale|sede sociale|capitale sociale|responsabilit[aà] sociale|consenso|informativa|accetto|"
    r"profilazione|archiviazione|trattamento|titolare|interessat|finalit|comunicazioni commerciali|"
    r"lecturer|bocconi|ricercatore|skip to|vai al contenuto|salta al", re.I)

# posizioni non piu' disponibili
RX_SCADUTA = re.compile(r"\bscadut|posizione chiusa|non pi[uù] disponibile|\bclosed\b|\bexpired\b", re.I)
# sedi fuori dall'area di interesse: "- Houston, TX" e simili, paesi e citta' estere
# sigle di stati USA scelte una per una: sono escluse quelle che coincidono con una
# provincia italiana (MI Milano, CO Como, PA Palermo, VA Varese, TN Trento, CA Cagliari...)
STATI_USA = ("TX|AZ|FL|GA|NJ|OH|NY|NC|ND|SD|WA|WV|WI|WY|OK|NE|NV|NH|NM|KY|KS|IA|ID|IL|IN|"
             "HI|DE|CT|DC|MD|ME|MT|SC|AK|MA|LA|UT")
RX_ESTERO = re.compile(rf"[,\s-]\s*({STATI_USA})\s*$|\(w/m/d\)|\bUSA\b|United States|\b(Houston|Phoenix|Dallas|Austin|"
                       r"Chicago|Boston|Atlanta|Seattle|London|Paris|Madrid|Barcelona|Berlin|Munich|Hamburg|"
                       r"Warsaw|Krakow|Bucharest|Budapest|Prague|Amsterdam|Brussels|Shanghai|Singapore|"
                       r"Mumbai|Bangalore|Dubai|Istanbul)\b", re.I)
# testo che parla di marketing ma non e' un annuncio: pubblicita' del prodotto,
# biografie, firme email, descrizioni tecniche, frasi di contatto
RX_NON_ANNUNCIO = re.compile(r"^\s*(mi chiamo|sono un[ao]?\b|ti aiuto|vi aiuto|aiutiamo|offriamo|scopri come|"
                             r"grazie per|benvenut)|mai pi[uù] |ti permette di|vi permette di|"
                             r"for questions regarding|per (qualsiasi )?informazion[ei] (scriv|contatt)|"
                             r"puoi (sempre )?contattare|\bprofinet\b|ethernet|\bplc\b|protocoll[oi] di comunicazione|"
                             r"sistemi di comunicazione|communication systems|"
                             r"gestione della comunicazione tra|flusso delle lead|"
                             r"dynamics 365|functional consultant", re.I)
# enti e societa' pubbliche: la ricerca esclude sempre i concorsi pubblici
RX_PUBBLICO = re.compile(r"\bamcps\b|valore citt|comune di |provincia di |regione |azienda ospedalier|"
                         r"\bulss\b|\ba\.?s\.?l\.?\b|ospedale|universit|istituto comprensivo|"
                         r"scuola (primaria|secondaria)|camera di commercio|consorzio di bonifica", re.I)


def rilevante(r):
    """True se la riga sembra davvero un annuncio di marketing utile."""
    if len(r) > 160 or len(r.split()) < 2:
        return False
    if not RX_MKT.search(r) or not RX_RUOLO.search(r):
        return False
    for rx in (RX_RUMORE, RX_SCADUTA, RX_ESTERO, RX_NON_ANNUNCIO):
        if rx.search(r):
            return False
    return True


# citta' e paesi fuori dall'area di ricerca: il comune nell'elenco e' la sede legale
# dell'azienda, non il luogo di lavoro dell'annuncio, quindi la sede va cercata
# nelle righe intorno al titolo
RX_SEDE_ESTERA = re.compile(r"\b(barcelona|barcellona|madrid|valencia|vigo|bilbao|lisbo|porto\b|"
                            r"london|londra|manchester|dublin|dublino|paris|parigi|lyon|"
                            r"berlin|berlino|munich|monaco di baviera|hamburg|frankfurt|stuttgart|"
                            r"amsterdam|rotterdam|utrecht|brussels|bruxelles|antwerp|luxembourg|"
                            r"wien|vienna|zurich|zurigo|geneva|ginevra|basel|"
                            r"warsaw|varsavia|krakow|cracovia|prague|praga|budapest|bucharest|bucarest|"
                            r"sofia|belgrade|zagreb|athens|atene|istanbul|"
                            r"tirana|shkod|durres|albania|"
                            r"new york|boston|chicago|houston|dallas|austin|atlanta|seattle|"
                            r"san francisco|los angeles|miami|toronto|montreal|"
                            r"shanghai|beijing|pechino|hong kong|singapore|tokyo|seoul|"
                            r"mumbai|bangalore|delhi|dubai|tel aviv|"
                            r"sao paulo|mexico city|buenos aires|sydney|melbourne|"
                            # citta' italiane fuori dal raggio di un'ora da Selvazzano
                            # (Milano resta ammessa: raggiungibile in treno, come richiesto)
                            r"roma\b|napoli|torino|firenze|genova|bari\b|palermo|catania|cagliari|"
                            r"messina|taranto|lecce|brindisi|reggio calabria|reggio emilia|salerno|"
                            r"perugia|ancona|pescara|l'aquila|campobasso|potenza|catanzaro|sassari|"
                            r"trieste|udine|pordenone|trento|bolzano|brescia|bergamo|como\b|varese|"
                            r"monza|pavia|cremona|parma|modena|piacenza|ravenna|rimini|forl|cesena|"
                            r"pisa\b|livorno|siena|arezzo|prato\b|lucca|latina|frosinone|terni|"
                            r"novara|alessandria|asti\b|cuneo|savona|la spezia|imperia|aosta)\b|"
                            r"\b(spain|spagna|france|francia|germany|germania|deutschland|netherlands|"
                            r"belgium|belgio|portugal|portogallo|poland|polonia|romania|albania|"
                            r"united kingdom|ireland|irlanda|switzerland|svizzera|austria|"
                            r"czech|hungary|turkey|turchia|india|china|cina|brazil|brasile)\b", re.I)
# luoghi dell'area di interesse: se compaiono, la sede estera vicina riguarda un altro annuncio
RX_SEDE_NOSTRA = re.compile(r"\b(padova|vicenza|rovigo|venezia|treviso|verona|mestre|milano|milan|"
                            r"bologna|veneto|lombardia|italia|italy|remoto|remote|smart working)\b", re.I)


def sede_fuori_area(riga, vicine):
    """'fuori area' se nelle righe intorno compare una sede estera e nessuna nostra."""
    testo = " / ".join(vicine)
    if RX_SEDE_ESTERA.search(testo) and not RX_SEDE_NOSTRA.search(testo):
        return "fuori area"
    return ""


RX_JS = re.compile(r"enable javascript|abilita javascript|attiva javascript|__NEXT_DATA__|id=\"root\"></div>|id=\"app\"></div>", re.I)


def leggi_pagina(url, lim):
    lim.attendi(url)
    fin, html = scarica(url)
    if not fin:
        return None, html, False
    righe = testo_visibile(html)
    serve_browser = len(" ".join(righe)) < 400 or bool(RX_JS.search(html[:50000]))
    return righe, None, serve_browser


def leggi_con_browser(urls):
    """Rende con Chromium le pagine che caricano gli annunci via JavaScript."""
    out = {}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return out
    from comune import UA, robots_ok
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(user_agent=UA, locale="it-IT")
        for u in urls:
            if not robots_ok(u):
                continue
            pg = ctx.new_page()
            try:
                pg.goto(u, timeout=30000, wait_until="networkidle")
                html = pg.content()
                out[u] = testo_visibile(html)
            except Exception:
                pass
            finally:
                pg.close()
        b.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--senza-browser", action="store_true")
    a = ap.parse_args()
    oggi = dt.date.today().isoformat()
    aziende = [x for x in leggi_csv(ELENCO) if x.get("pagina_lavoro") and "LinkedIn" not in x.get("esito_ricerca", "")]
    stato = json.loads(STATO.read_text("utf-8")) if STATO.exists() else {}
    lavori = []
    for az in aziende:
        for u in az["pagina_lavoro"].split(" | "):
            if u.strip():
                lavori.append((az, u.strip()))
    print(f"Pagine da controllare: {len(lavori)}")
    lim = Limitatore(2.0)
    lette, errori, js = {}, {}, []
    with ThreadPoolExecutor(max_workers=16) as ex:
        fut = {ex.submit(leggi_pagina, u, lim): (az, u) for az, u in lavori}
        for f in as_completed(fut):
            az, u = fut[f]
            try:
                righe, err, serve_browser = f.result()
            except Exception as e:
                righe, err, serve_browser = None, type(e).__name__, False
            if righe is None:
                errori[u] = err
                continue
            lette[u] = righe
            if serve_browser:
                js.append(u)
    if js and not a.senza_browser:
        print(f"Pagine da aprire con il browser: {len(js)}")
        for u, righe in leggi_con_browser(js).items():
            if len(" ".join(righe)) > len(" ".join(lette.get(u, []))):
                lette[u] = righe

    novita = []
    for az, u in lavori:
        if u not in lette:
            continue
        righe = lette[u]
        prima = u not in stato
        vecchie = set(stato.get(u, {}).get("righe", []))
        for i, r in enumerate(righe):
            if rilevante(r):
                if prima or impronta(r) not in vecchie:
                    vicine = righe[max(0, i - 2):i + 3]
                    contesto = " / ".join(x for x in vicine if x != r)[:300]
                    novita.append({
                        "data": oggi, "azienda": az["nome"], "comune": az["comune"], "settore": az.get("settore", ""),
                        "riga_trovata": r, "pagina": u, "contesto": contesto,
                        "sede": sede_fuori_area(r, vicine),
                        "tipo": "NUOVA (prima lettura della pagina)" if prima else "NUOVA",
                    })
        stato[u] = {"righe": sorted({impronta(r) for r in righe}), "ultimo_controllo": oggi}
    for u, err in errori.items():
        s = stato.setdefault(u, {"righe": []})
        s["ultimo_errore"] = f"{oggi}: {err}"

    STATO.write_text(json.dumps(stato, ensure_ascii=False, separators=(",", ":")), "utf-8")
    RISULTATI.mkdir(exist_ok=True)
    campi = ["data", "tipo", "azienda", "comune", "settore", "riga_trovata", "sede", "contesto", "pagina"]
    fuori = [n for n in novita if n["sede"]]
    in_area = [n for n in novita if not n["sede"]]
    nuove = [n for n in in_area if n["tipo"] == "NUOVA"]
    prime_lettura = [n for n in in_area if n["tipo"] != "NUOVA"]
    if novita:
        with open(RISULTATI / f"novita_{oggi}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=campi)
            w.writeheader()
            w.writerows(sorted(novita, key=lambda n: (n["tipo"] != "NUOVA", n["azienda"])))
        storico = RISULTATI / "storico_novita.csv"
        nuovo_file = not storico.exists()
        with open(storico, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=campi)
            if nuovo_file:
                w.writeheader()
            w.writerows(novita)
    # riepilogo leggibile (usato anche per la notifica)
    md = [f"# Controllo pagine lavoro del {oggi}", "",
          f"Pagine controllate: {len(lette)} su {len(lavori)} (non leggibili: {len(errori)})", "",
          f"Posizioni marketing da vedere: {len(in_area)} "
          f"({len(nuove)} comparse su pagine già note, {len(prime_lettura)} su pagine lette per la prima volta)"
          + (f"; altre {len(fuori)} con sede probabilmente fuori area" if fuori else ""), "",
          "Il comune indicato è la sede legale dell'azienda: il luogo di lavoro "
          "dell'annuncio può essere diverso, vedere la colonna contesto nel CSV.", ""]

    def elenca(titolo, righe):
        if not righe:
            return
        md.append(f"## {titolo}")
        md.append("")
        per_az = {}
        for n in righe:
            per_az.setdefault((n["azienda"], n["comune"], n["pagina"]), []).append(n["riga_trovata"])
        for (az, com, pag), rr in sorted(per_az.items()):
            md.append(f"### {az}" + (f" ({com})" if com else ""))
            md.append(pag)
            md += [f"- {r}" for r in rr[:8]]
            md.append("")

    elenca("Nuove su pagine già controllate in passato", nuove)
    elenca("Trovate su pagine controllate per la prima volta", prime_lettura)
    elenca("FUORI ZONA - aprire solo se l'annuncio è full remote", fuori)
    (RISULTATI / "ultimo_controllo.md").write_text("\n".join(md), "utf-8")
    print("\n".join(md[:6]))
    # file usato dal workflow per decidere se aprire una segnalazione
    (RISULTATI / ".ci_sono_novita").write_text("1" if novita else "0")


if __name__ == "__main__":
    main()
