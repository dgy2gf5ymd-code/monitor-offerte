"""Funzioni comuni: download pagine nel rispetto di robots.txt, estrazione testo."""
import csv
import hashlib
import re
import threading
import time
import urllib.robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

UA = "MonitorOfferteLavoro/1.0 (controllo pagine 'lavora con noi'; uso personale non commerciale)"
TIMEOUT = 20
HEADERS = {"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9,en;q=0.6"}

_robots = {}
_robots_lock = threading.Lock()
_session = threading.local()


def sessione():
    s = getattr(_session, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update(HEADERS)
        _session.s = s
    return s


def robots_ok(url):
    """True se robots.txt consente di leggere l'url (in caso di errore si assume consentito)."""
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    with _robots_lock:
        rp = _robots.get(base)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = sessione().get(base + "/robots.txt", timeout=10)
            if r.status_code == 200:
                rp.parse(r.text.splitlines())
            else:
                rp.parse([])
        except Exception:
            rp.parse([])
        with _robots_lock:
            _robots[base] = rp
    try:
        return rp.can_fetch(UA, url)
    except Exception:
        return True


def scarica(url):
    """Ritorna (url_finale, html) oppure (None, motivo_errore)."""
    if not robots_ok(url):
        return None, "bloccato da robots.txt"
    try:
        r = sessione().get(url, timeout=TIMEOUT, allow_redirects=True)
    except requests.exceptions.SSLError:
        try:
            r = sessione().get(url.replace("https://", "http://", 1), timeout=TIMEOUT)
        except Exception as e:
            return None, f"errore connessione ({type(e).__name__})"
    except Exception as e:
        return None, f"errore connessione ({type(e).__name__})"
    if r.status_code >= 400:
        return None, f"HTTP {r.status_code}"
    ct = r.headers.get("content-type", "")
    if "html" not in ct and "text" not in ct:
        return None, f"contenuto non html ({ct[:30]})"
    r.encoding = r.apparent_encoding if not r.encoding or r.encoding.lower() == "iso-8859-1" else r.encoding
    return r.url, r.text


def testo_visibile(html):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "svg", "iframe", "template"]):
        t.decompose()
    # menu e piè di pagina cambiano spesso e non contengono annunci
    for t in soup.find_all(["nav", "footer"]):
        t.decompose()
    righe = []
    for riga in soup.get_text("\n").split("\n"):
        riga = re.sub(r"\s+", " ", riga).strip()
        if 2 < len(riga) < 400:
            righe.append(riga)
    # elimina duplicati mantenendo l'ordine
    visti, out = set(), []
    for r in righe:
        if r not in visti:
            visti.add(r)
            out.append(r)
    return out


def link(html, base):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        testo = re.sub(r"\s+", " ", a.get_text(" ")).strip()
        out.append((urljoin(base, href), testo, a.get("title", "") or ""))
    return out


def impronta(riga):
    return hashlib.sha1(riga.lower().encode("utf-8")).hexdigest()[:10]


def leggi_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def scrivi_csv(path, righe, campi):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=campi, extrasaction="ignore")
        w.writeheader()
        w.writerows(righe)


def dominio(url):
    return urlparse(url).netloc.lower().removeprefix("www.")


class Limitatore:
    """Al massimo una richiesta ogni `pausa` secondi per dominio."""

    def __init__(self, pausa=2.0):
        self.pausa = pausa
        self.ultimo = {}
        self.lock = threading.Lock()

    def attendi(self, url):
        d = dominio(url)
        with self.lock:
            ora = time.time()
            prossimo = max(ora, self.ultimo.get(d, 0) + self.pausa)
            self.ultimo[d] = prossimo
        if prossimo > ora:
            time.sleep(prossimo - ora)
