# Monitor News Campania

Sistema automatico che controlla periodicamente le notizie pubblicate dalla Regione Campania e salva i risultati in un database PostgreSQL.

## Funzionamento

- Ogni 10 minuti Render esegue `monitor.py`
- Lo script scarica la pagina delle notizie
- Estrae titolo, link e fonte
- Salva tutto nella tabella `news`

## File del progetto

- `monitor.py` — script principale
- `requirements.txt` — dipendenze Python
- `schema.sql` — struttura del database
- `.env.example` — variabili ambiente
- `render.yaml` — configurazione cron job

## Deploy su Render

1. Creare un nuovo servizio **Cron Job**
2. Caricare il repository GitHub
3. Impostare la variabile ambiente `DATABASE_URL`
4. Render eseguirà automaticamente lo script ogni 10 minuti

## Autore
Luigi Di Maria (gigidimaria-art)
