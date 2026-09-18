#!/usr/bin/env python3
"""
Monitor News Agricoltura Campania
==================================

Monitora la sezione #news della pagina:
    https://agricoltura.regione.campania.it/#news

Rileva nuovi comunicati e modifiche a comunicati già esistenti
(proroghe, rettifiche, aggiornamenti testo, ecc.) e invia una
notifica via email per ogni variazione rilevata.

Uso:
    python monitor.py              # esecuzione normale (scrive su DB, invia email)
    python monitor.py --dry-run    # scarica ed estrae i comunicati, stampa il
                                    # risultato SENZA toccare il database e SENZA
                                    # inviare notifiche. Utile per verificare che
                                    # l'estrazione funzioni correttamente sulla
                                    # pagina reale prima di attivare il monitor.

Variabili d'ambiente richieste (vedi .env.example):
    DATABASE_URL, EMAIL_MITTENTE, EMAIL_PASSWORD, EMAIL_DESTINATARIO
    (opzionali: SMTP_HOST, SMTP_PORT, TARGET_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import smtplib
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from email.mime.text import MIMEText
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Configurazione
# ---------------------------------------------------------------------------

TARGET_URL = os.environ.get("TARGET_URL", "https://agricoltura.regione.campania.it/")
DATABASE_URL = os.environ.get("DATABASE_URL")

EMAIL_MITTENTE = os.environ.get("EMAIL_MITTENTE")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_DESTINATARIO = os.environ.get("EMAIL_DESTINATARIO", "")

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5
USER_AGENT = (
    "Mozilla/5.0 (compatible; MonitorNewsAgricolturaCampania/1.0; "
    "+https://agricoltura.regione.campania.it/)"
)

MESI_ITALIANI = (
    "gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|"
    "settembre|ottobre|novembre|dicembre"
)
DATA_REGEX = re.compile(rf"(\d{{1,2}})\s+({MESI_ITALIANI})\s+(\d{{4}})", re.IGNORECASE)
MESI_MAP = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
    "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("monitor-news-agricoltura")


# ---------------------------------------------------------------------------
# Modello dati
# ---------------------------------------------------------------------------

@dataclass
class Comunicato:
    url: str
    titolo: str
    testo_breve: str
    data_pubblicazione: Optional[date] = None
    categoria: Optional[str] = None
    documenti: list[str] = field(default_factory=list)

    @property
    def impronta(self) -> str:
        base = f"{self.titolo.strip()}|{self.testo_breve.strip()}|{self.url.strip()}"
        return hashlib.sha256(base.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Download pagina (con retry)
# ---------------------------------------------------------------------------

def scarica_pagina(url: str) -> str:
    ultimo_errore: Optional[Exception] = None
    for tentativo in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            resp.encoding = resp.apparent_encoding or resp.encoding
            return resp.text
        except requests.RequestException as exc:
            ultimo_errore = exc
            log.warning(
                "Tentativo %d/%d di scaricare %s fallito: %s",
                tentativo, MAX_RETRIES, url, exc,
            )
            if tentativo < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF_SECONDS * tentativo)
    raise RuntimeError(f"Impossibile scaricare {url} dopo {MAX_RETRIES} tentativi") from ultimo_errore


# ---------------------------------------------------------------------------
# Estrazione comunicati dalla sezione #news
# ---------------------------------------------------------------------------

def _parse_data_italiana(testo: str) -> Optional[date]:
    m = DATA_REGEX.search(testo)
    if not m:
        return None
    giorno, mese_nome, anno = m.groups()
    mese = MESI_MAP.get(mese_nome.lower())
    if not mese:
        return None
    try:
        return date(int(anno), mese, int(giorno))
    except ValueError:
        return None


def _trova_sezione_news(soup: BeautifulSoup) -> Tag:
    """Individua il contenitore della sezione #news.

    Prova prima con l'id="news" (l'ancora referenziata da #news nel link di
    'Accesso Rapido'). Se la struttura della pagina cambia e l'id non è più
    presente, ripiega sulla ricerca del titolo "News ed eventi" e raccoglie
    il contenuto fino al titolo di sezione successivo.
    """
    container = soup.find(id="news")
    if isinstance(container, Tag):
        return container

    log.warning("id=\"news\" non trovato, uso il fallback basato sul titolo di sezione")
    heading = None
    for h in soup.find_all(["h1", "h2", "h3"]):
        if "news ed eventi" in h.get_text(strip=True).lower():
            heading = h
            break
    if heading is None:
        raise ValueError(
            "Sezione #news non individuabile: né id=\"news\" né il titolo "
            "'News ed eventi' sono presenti. La struttura della pagina è "
            "probabilmente cambiata: rivedere _trova_sezione_news()."
        )

    fragment = BeautifulSoup("<div></div>", "html.parser")
    contenitore_fittizio = fragment.div
    for sib in heading.find_all_next():
        if sib.name == "h2" and sib is not heading:
            break
        if isinstance(sib, Tag):
            contenitore_fittizio.append(sib)
    return contenitore_fittizio


def estrai_comunicati(html: str, base_url: str) -> list[Comunicato]:
    """Estrae i comunicati presenti nella sezione #news della pagina.

    Euristica: ogni comunicato nella pagina inizia con un'immagine, seguita
    da una data in grassetto, un titolo (h2/h3), un breve testo (p) e uno o
    più link. Il parsing raggruppa quindi i tag rilevanti in "blocchi",
    ciascuno delimitato dall'inizio all'immagine successiva (o alla fine
    della sezione).
    """
    soup = BeautifulSoup(html, "html.parser")
    sezione = _trova_sezione_news(soup)

    tag_rilevanti = sezione.find_all(["img", "h2", "h3", "h4", "p", "a", "strong", "b"])

    blocchi: list[dict] = []
    corrente: Optional[dict] = None
    for tag in tag_rilevanti:
        if tag.name == "img":
            if corrente is not None:
                blocchi.append(corrente)
            corrente = {"tags": []}
        elif corrente is not None:
            corrente["tags"].append(tag)
    if corrente is not None:
        blocchi.append(corrente)

    comunicati: list[Comunicato] = []
    for blocco in blocchi:
        tags = blocco["tags"]

        titolo_tag = next((t for t in tags if t.name in ("h2", "h3", "h4")), None)
        if titolo_tag is None:
            # Blocco senza titolo: probabilmente non è un vero comunicato
            # (es. immagine decorativa isolata). Lo saltiamo.
            continue
        titolo = titolo_tag.get_text(strip=True)

        testo_breve = ""
        for p in tags:
            if p.name == "p":
                txt = p.get_text(strip=True)
                if txt:
                    testo_breve = txt
                    break

        data_pubblicazione = None
        for t in tags:
            if t.name in ("strong", "b"):
                d = _parse_data_italiana(t.get_text(strip=True))
                if d:
                    data_pubblicazione = d
                    break
        if data_pubblicazione is None:
            # fallback: cerca la data in un punto qualsiasi del blocco
            testo_blocco = " ".join(t.get_text(" ", strip=True) for t in tags)
            data_pubblicazione = _parse_data_italiana(testo_blocco)

        link_visti: list[str] = []
        for a in tags:
            if a.name != "a":
                continue
            href = a.get("href", "").strip()
            if not href or href.startswith("#"):
                continue
            href_assoluto = urljoin(base_url, href)
            if href_assoluto not in link_visti:
                link_visti.append(href_assoluto)

        if not link_visti:
            # Nessun link utilizzabile: non possiamo identificare il
            # comunicato in modo univoco (l'url è PRIMARY KEY), lo saltiamo
            # e lo segnaliamo nei log.
            log.warning("Comunicato '%s' scartato: nessun link trovato", titolo)
            continue

        url_principale = link_visti[0]
        documenti = link_visti[1:]

        comunicati.append(
            Comunicato(
                url=url_principale,
                titolo=titolo,
                testo_breve=testo_breve,
                data_pubblicazione=data_pubblicazione,
                categoria=None,  # non ricavabile in modo affidabile su questa pagina
                documenti=documenti,
            )
        )

    return comunicati


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_connessione_db():
    import psycopg2
    if not DATABASE_URL:
        raise RuntimeError("Variabile d'ambiente DATABASE_URL non impostata")
    return psycopg2.connect(DATABASE_URL)


def carica_comunicato_esistente(cur, url: str) -> Optional[dict]:
    cur.execute(
        """
        SELECT url, titolo, testo_breve, impronta, impronta_notificata
        FROM comunicati_news
        WHERE url = %s
        """,
        (url,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    cols = ["url", "titolo", "testo_breve", "impronta", "impronta_notificata"]
    return dict(zip(cols, row))


def inserisci_comunicato(cur, c: Comunicato) -> None:
    cur.execute(
        """
        INSERT INTO comunicati_news
            (url, titolo, testo_breve, data_pubblicazione, categoria,
             ultimo_aggiornamento, documenti, impronta, impronta_notificata,
             prima_rilevazione, ultima_verifica)
        VALUES
            (%s, %s, %s, %s, %s, %s, %s, %s, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (
            c.url, c.titolo, c.testo_breve, c.data_pubblicazione, c.categoria,
            c.data_pubblicazione, "\n".join(c.documenti), c.impronta,
        ),
    )


def aggiorna_comunicato(cur, c: Comunicato) -> None:
    cur.execute(
        """
        UPDATE comunicati_news
        SET titolo = %s,
            testo_breve = %s,
            data_pubblicazione = COALESCE(%s, data_pubblicazione),
            categoria = %s,
            ultimo_aggiornamento = CURRENT_DATE,
            documenti = %s,
            impronta = %s,
            ultima_verifica = CURRENT_TIMESTAMP
        WHERE url = %s
        """,
        (
            c.titolo, c.testo_breve, c.data_pubblicazione, c.categoria,
            "\n".join(c.documenti), c.impronta, c.url,
        ),
    )


def aggiorna_solo_verifica(cur, url: str) -> None:
    cur.execute(
        "UPDATE comunicati_news SET ultima_verifica = CURRENT_TIMESTAMP WHERE url = %s",
        (url,),
    )


def segna_notificato(cur, url: str, impronta: str) -> None:
    cur.execute(
        "UPDATE comunicati_news SET impronta_notificata = %s WHERE url = %s",
        (impronta, url),
    )


# ---------------------------------------------------------------------------
# Notifiche
# ---------------------------------------------------------------------------

def invia_email(oggetto: str, corpo: str) -> bool:
    if not (EMAIL_MITTENTE and EMAIL_PASSWORD and EMAIL_DESTINATARIO):
        log.warning("Configurazione email incompleta: notifica email NON inviata")
        return False

    destinatari = [d.strip() for d in EMAIL_DESTINATARIO.split(",") if d.strip()]
    msg = MIMEText(corpo, "plain", "utf-8")
    msg["Subject"] = oggetto
    msg["From"] = EMAIL_MITTENTE
    msg["To"] = ", ".join(destinatari)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=REQUEST_TIMEOUT) as server:
            server.starttls()
            server.login(EMAIL_MITTENTE, EMAIL_PASSWORD)
            server.sendmail(EMAIL_MITTENTE, destinatari, msg.as_string())
        return True
    except Exception as exc:
        log.error("Invio email fallito: %s", exc)
        return False


def invia_telegram(testo: str) -> bool:
    """Canale opzionale, pronto per un'estensione futura (spento di default)."""
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": testo},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        log.error("Invio Telegram fallito: %s", exc)
        return False


def invia_notifica(c: Comunicato, tipo: str) -> bool:
    """Invia la notifica su tutti i canali configurati.

    Ritorna True se ALMENO un canale ha avuto successo (in tal caso la
    variazione viene comunque marcata come notificata, per evitare di
    accumulare notifiche duplicate su un canale che funziona già).
    """
    etichetta = "NUOVO COMUNICATO" if tipo == "nuovo" else "COMUNICATO MODIFICATO"
    oggetto = f"[Agricoltura Campania] {etichetta}: {c.titolo}"
    corpo = (
        f"Tipo: {etichetta}\n\n"
        f"Titolo: {c.titolo}\n\n"
        f"Testo: {c.testo_breve}\n\n"
        f"Link al comunicato: {c.url}\n"
        f"Pagina News: {TARGET_URL}\n"
    )

    esito_email = invia_email(oggetto, corpo)
    esito_telegram = invia_telegram(f"{oggetto}\n\n{corpo}")

    return esito_email or esito_telegram


# ---------------------------------------------------------------------------
# Ciclo principale
# ---------------------------------------------------------------------------

def esegui_ciclo(dry_run: bool = False) -> None:
    log.info("Avvio ciclo di monitoraggio su %s", TARGET_URL)

    html = scarica_pagina(TARGET_URL)
    comunicati = estrai_comunicati(html, TARGET_URL)
    log.info("Estratti %d comunicati dalla sezione #news", len(comunicati))

    if dry_run:
        for c in comunicati:
            print("-" * 70)
            print(f"Titolo:   {c.titolo}")
            print(f"Data:     {c.data_pubblicazione}")
            print(f"Testo:    {c.testo_breve}")
            print(f"URL:      {c.url}")
            print(f"Documenti:{c.documenti}")
            print(f"Impronta: {c.impronta}")
        print("-" * 70)
        print(f"Totale comunicati estratti: {len(comunicati)}")
        print("Dry-run completato: nessuna scrittura su DB, nessuna email inviata.")
        return

    if not comunicati:
        log.warning(
            "Nessun comunicato estratto: probabile problema di parsing o pagina "
            "temporaneamente non disponibile. Nessuna modifica al database."
        )
        return

    conn = get_connessione_db()
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            nuovi = 0
            modificati = 0
            invariati = 0

            for c in comunicati:
                esistente = carica_comunicato_esistente(cur, c.url)

                if esistente is None:
                    inserisci_comunicato(cur, c)
                    conn.commit()
                    nuovi += 1
                    if invia_notifica(c, "nuovo"):
                        with conn.cursor() as cur2:
                            segna_notificato(cur2, c.url, c.impronta)
                        conn.commit()
                    else:
                        log.error(
                            "Notifica non inviata per il nuovo comunicato '%s' "
                            "(url=%s): verrà ritentata al prossimo ciclo",
                            c.titolo, c.url,
                        )
                    continue

                impronta_cambiata = esistente["impronta"] != c.impronta
                gia_notificato = esistente["impronta_notificata"] == c.impronta

                if impronta_cambiata:
                    aggiorna_comunicato(cur, c)
                    conn.commit()
                    modificati += 1
                    if invia_notifica(c, "modificato"):
                        with conn.cursor() as cur2:
                            segna_notificato(cur2, c.url, c.impronta)
                        conn.commit()
                    else:
                        log.error(
                            "Notifica non inviata per la modifica di '%s' "
                            "(url=%s): verrà ritentata al prossimo ciclo",
                            c.titolo, c.url,
                        )
                elif not gia_notificato:
                    # L'impronta non è cambiata rispetto all'ultima scansione,
                    # ma un tentativo di notifica precedente era fallito: la
                    # ritentiamo ora senza duplicare la scrittura dei dati.
                    aggiorna_solo_verifica(cur, c.url)
                    conn.commit()
                    if invia_notifica(c, "modificato"):
                        with conn.cursor() as cur2:
                            segna_notificato(cur2, c.url, c.impronta)
                        conn.commit()
                else:
                    aggiorna_solo_verifica(cur, c.url)
                    conn.commit()
                    invariati += 1

            log.info(
                "Ciclo completato: %d nuovi, %d modificati, %d invariati",
                nuovi, modificati, invariati,
            )
    finally:
        conn.close()


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    try:
        esegui_ciclo(dry_run=dry_run)
    except Exception as exc:
        log.exception("Esecuzione del monitor interrotta da un errore: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
