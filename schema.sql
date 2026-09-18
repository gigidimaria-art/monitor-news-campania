CREATE TABLE IF NOT EXISTS news (
    id SERIAL PRIMARY KEY,
    titolo TEXT NOT NULL,
    link TEXT NOT NULL,
    data_pubblicazione TIMESTAMP DEFAULT NOW(),
    fonte TEXT NOT NULL
);
