import requests
import psycopg2
import os
from bs4 import BeautifulSoup

DATABASE_URL = os.getenv("DATABASE_URL")

def salva_notizia(titolo, link, fonte):
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO news (titolo, link, fonte)
        VALUES (%s, %s, %s)
    """, (titolo, link, fonte))
    conn.commit()
    cur.close()
    conn.close()

def controlla_bandi():
    url = "https://www.regione.campania.it/notizie"
    r = requests.get(url)
    soup = BeautifulSoup(r.text, "html.parser")

    for item in soup.select(".news-item"):
        titolo = item.select_one("h2").get_text(strip=True)
        link = item.select_one("a")["href"]
        fonte = "Regione Campania"

        salva_notizia(titolo, link, fonte)

if __name__ == "__main__":
    controlla_bandi()
