-- Schema per il monitor della pagina News Agricoltura Campania
-- Da eseguire una tantum sul database Neon PostgreSQL

CREATE TABLE IF NOT EXISTS comunicati_news (
    url TEXT PRIMARY KEY,
    titolo TEXT NOT NULL,
    testo_breve TEXT,
    data_pubblicazione DATE,
    categoria TEXT,
    ultimo_aggiornamento DATE,
    documenti TEXT,
    impronta TEXT NOT NULL,
    impronta_notificata TEXT,
    prima_rilevazione TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ultima_verifica TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Indice utile per query di debug/reportistica ordinate per data di rilevazione
CREATE INDEX IF NOT EXISTS idx_comunicati_news_prima_rilevazione
    ON comunicati_news (prima_rilevazione DESC);
