# Monitor News Agricoltura Campania

Monitora esclusivamente la sezione `#news` di
`https://agricoltura.regione.campania.it/` e invia una notifica email per
ogni nuovo comunicato o modifica ai comunicati esistenti (proroghe,
rettifiche, integrazioni, aggiornamenti testo, ecc.). Non tocca l'archivio
storico `comunicati.htm`.

## Come funziona

1. Scarica la pagina e individua il blocco `id="news"`.
2. Per ogni comunicato estrae: titolo, testo breve, data, link ufficiale ed
   eventuali link aggiuntivi (documenti).
3. Calcola un hash SHA-256 (`impronta`) su titolo + testo breve + url.
4. Confronta l'impronta con quella salvata su Neon PostgreSQL:
   - url non presente → **nuovo comunicato** → salva + notifica
   - impronta diversa → **comunicato modificato** → salva + notifica
   - impronta uguale → nessuna azione (oltre ad aggiornare `ultima_verifica`)
5. Se l'invio della notifica fallisce, il comunicato resta segnato come "da
   notificare" (`impronta_notificata` diversa da `impronta`) e viene
   ritentato al ciclo successivo, senza generare duplicati.

⚠️ **Nota sull'estrazione**: il parsing è basato sulla struttura HTML
osservata sulla pagina al momento della scrittura (ogni comunicato inizia
con un'immagine, seguita da data, titolo, testo e link). Se il sito
regionale cambia struttura, verificare ed eventualmente adattare la
funzione `estrai_comunicati()` in `monitor.py`. Usare sempre prima
`--dry-run` (vedi sotto) dopo ogni deploy o dopo modifiche al sito per
controllare che l'estrazione sia corretta.

## Test locale (dry-run)

Prima di collegare database ed email, verificare che lo scraping funzioni:

```bash
pip install -r requirements.txt
python monitor.py --dry-run
```

Questo comando stampa a schermo tutti i comunicati estratti (titolo, data,
testo, url, impronta) **senza** scrivere sul database né inviare email.

## Deploy

Percorso scelto: **GitHub (repository + Actions) + Neon**, a costo zero.
Render non è necessario in questa configurazione (resta descritto in fondo
come alternativa, nel caso in futuro serva un orario più preciso).

### 1. Neon PostgreSQL

1. Creare un progetto/database su [Neon](https://neon.tech).
2. Eseguire `schema.sql` sul database (es. tramite l'SQL Editor della
   dashboard Neon, oppure `psql "$DATABASE_URL" -f schema.sql`).
3. Copiare la connection string (`DATABASE_URL`), che deve includere
   `sslmode=require`.

### 2. GitHub

1. Caricare (o sostituire) tutti i file di questo progetto nel repository,
   incluso `.github/workflows/monitor.yml`.
2. Aggiungere questi **Secrets** del repository (Settings → Secrets and
   variables → Actions → New repository secret):
   - `DATABASE_URL`
   - `EMAIL_MITTENTE`
   - `EMAIL_PASSWORD` (per Gmail: una [password per le
     app](https://myaccount.google.com/apppasswords), non la password
     normale dell'account)
   - `EMAIL_DESTINATARIO` (uno o più indirizzi separati da virgola)
3. Il workflow è già programmato ogni 5 minuti (`*/5 * * * *`). Può anche
   essere avviato manualmente dalla tab "Actions" del repository, scheda
   "Monitor News Agricoltura Campania" → "Run workflow" (utile per il primo
   test).
4. Nota: GitHub Actions non garantisce l'esecuzione puntuale al minuto
   (può ritardare di qualche minuto nei periodi di alto carico): l'intervallo
   effettivo sarà "circa 5 minuti", non esatto.

### 3. (Alternativa a pagamento) Render come cron job

Da valutare solo se in futuro serve un orario più preciso: Render non offre
più un piano gratuito per i cron job (minimo ~1$/mese, tariffazione al
secondo). In tal caso:

1. Su Render creare un nuovo **Blueprint** puntando al repository: viene
   letto automaticamente `render.yaml`, che configura un Cron Job ogni 5
   minuti (`*/5 * * * *`).
2. Impostare le stesse variabili d'ambiente (`DATABASE_URL`,
   `EMAIL_MITTENTE`, `EMAIL_PASSWORD`, `EMAIL_DESTINATARIO`) nella sezione
   "Environment" del servizio Render.
3. In questo caso è consigliabile disabilitare lo schedule del workflow
   GitHub Actions (per evitare che monitor.py giri due volte in parallelo
   da due esecutori diversi), lasciando solo `workflow_dispatch` per i test
   manuali.

## Struttura del progetto

```
.
├── monitor.py                       # script principale
├── schema.sql                       # schema tabella comunicati_news (Neon)
├── requirements.txt                 # dipendenze Python
├── .env.example                     # variabili d'ambiente di esempio
├── render.yaml                      # blueprint cron job per Render
└── .github/workflows/monitor.yml    # workflow opzionale GitHub Actions
```

## Estensioni future

L'invio delle notifiche è centralizzato nella funzione `invia_notifica()`,
che già prova un canale Telegram opzionale (disattivato di default: basta
impostare `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` per attivarlo) oltre
all'email. Aggiungere altri canali richiede solo di scrivere una nuova
funzione `invia_x()` e richiamarla da `invia_notifica()`.

## Limiti noti

- Il campo `categoria` non è attualmente ricavabile in modo affidabile
  dalla struttura della pagina e viene lasciato vuoto.
- Il parsing è euristico (basato su pattern HTML osservati) e non su un
  selettore CSS/id stabile per singolo comunicato: un cambio di grafica del
  sito regionale può richiedere un aggiornamento di `estrai_comunicati()`.
