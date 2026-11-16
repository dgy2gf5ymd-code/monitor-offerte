# Monitor offerte di lavoro sui siti aziendali

Controlla in automatico le pagine "Lavora con noi" delle aziende dell'elenco e segnala
le righe nuove che parlano di marketing, comunicazione, social, grafica, eventi, e-commerce.

Non invia candidature, non compila moduli, non contatta nessuno: legge solo pagine pubbliche,
rispetta il file robots.txt di ogni sito e fa una richiesta alla volta per sito.

## Come funziona

1. `dati/aziende.csv` — elenco aziende (nome, comune, settore, sito, pagina lavoro). A ogni giro
   `scripts/carica_confindustria.py` aggiunge le associate Confindustria Veneto Est della provincia di Padova.
2. `scripts/scopri_pagine.py` — per le aziende senza pagina lavoro apre il sito e cerca il link
   "Lavora con noi / Careers / Posizioni aperte"; se non c'è prova gli indirizzi più comuni.
3. `scripts/controlla.py` — scarica tutte le pagine lavoro, le confronta con il giro precedente
   e salva le righe nuove che contengono parole del marketing. Le pagine che caricano gli annunci
   con JavaScript vengono aperte con un browser (Chromium).
4. `.github/workflows/controllo.yml` — fa girare tutto lunedì, mercoledì e venerdì mattina sui
   server di GitHub (gratis) e apre una "issue" quando ci sono novità: GitHub ti manda una email.

## Risultati

- `risultati/ultimo_controllo.md` — riepilogo dell'ultimo giro
- `risultati/novita_AAAA-MM-GG.csv` — novità del giorno
- `risultati/storico_novita.csv` — tutte le novità trovate
- `dati/aziende.csv` — colonna `esito_ricerca` per capire quali siti vanno guardati a mano

Al primo giro ogni pagina viene "fotografata": le righe marketing già presenti sono segnate
come "presente al primo controllo". Dal secondo giro arrivano solo le righe NUOVE.

## Installazione (una volta sola, circa 10 minuti)

1. Crea un account gratuito su https://github.com (se non ce l'hai).
2. In alto a destra "+" → "New repository". Nome a scelta (es. `monitor-offerte`),
   seleziona **Private**, poi "Create repository".
3. Nella pagina del repository: "uploading an existing file" → trascina **il contenuto** della
   cartella (le cartelle `scripts`, `dati`, `.github` e i file `README.md`, `requirements.txt`) → "Commit changes".
   Nota: su Mac la cartella `.github` è nascosta nel Finder; premi Cmd+Shift+. per vederla.
4. Vai su "Actions" → se richiesto, abilita i workflow → apri "Controllo pagine lavora con noi" →
   "Run workflow" per il primo giro (dura da 1 a 3 ore la prima volta).
5. Per ricevere le email: in alto a destra "Watch" → "All Activity" (oppure almeno "Issues").

## Aggiungere aziende

- A mano: aggiungi una riga in `dati/aziende.csv` (basta nome, comune e sito; la pagina lavoro la trova lo script).
- Da un elenco acquistato (es. Camera di Commercio, con filtro dipendenti):
  `python scripts/importa_elenco.py elenco.csv --fonte "Camera di Commercio" --min-dipendenti 20`

## Limiti

- I siti che vietano la lettura automatica (robots.txt) non vengono letti: vanno guardati a mano.
- Le aziende che pubblicano solo su LinkedIn sono segnate "solo LinkedIn": per quelle servono gli avvisi email di LinkedIn.
- Lo script segnala righe di testo, non annunci interpretati: la valutazione (distanza, contratto,
  corrispondenza col profilo) va fatta dopo.
