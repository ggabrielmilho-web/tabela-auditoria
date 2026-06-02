"""
Importa centroides dos municípios brasileiros (IBGE) para `municipios_ibge`.
Fonte: https://github.com/kelvins/Municipios-Brasileiros (CSV mantido, com lat/lng).

Rodar UMA VEZ:  python -X utf8 import_municipios.py
Idempotente: usa ON CONFLICT DO UPDATE pra sobrescrever.
"""

import csv
import io
import os
import unicodedata
import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()

URL_ESTADOS = 'https://raw.githubusercontent.com/kelvins/municipios-brasileiros/main/csv/estados.csv'
URL_MUNICIPIOS = 'https://raw.githubusercontent.com/kelvins/municipios-brasileiros/main/csv/municipios.csv'


def normalizar(s: str) -> str:
    """UPPERCASE + sem acento + trim. Usado como chave de match."""
    if not s:
        return ''
    nfkd = unicodedata.normalize('NFKD', s)
    sem_acento = ''.join(c for c in nfkd if not unicodedata.combining(c))
    return sem_acento.upper().strip()


def baixar_csv(url):
    print(f'  → baixando {url}')
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    # CSV vem com BOM; decodifica como utf-8-sig pra remover
    texto = r.content.decode('utf-8-sig')
    return list(csv.DictReader(io.StringIO(texto)))


def main():
    print('Importando municípios IBGE...')

    estados = baixar_csv(URL_ESTADOS)
    cod_to_uf = {e['codigo_uf']: e['uf'] for e in estados}
    print(f'  ✓ {len(cod_to_uf)} estados')

    municipios = baixar_csv(URL_MUNICIPIOS)
    print(f'  ✓ {len(municipios)} municípios')

    conn = psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        port=os.getenv('DB_PORT', '5432'),
        dbname=os.getenv('DB_NAME', 'postgres'),
        user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD', ''),
    )
    cur = conn.cursor()

    inseridos = 0
    pulados = 0
    for m in municipios:
        uf = cod_to_uf.get(m['codigo_uf'])
        if not uf:
            pulados += 1
            continue
        try:
            lat = float(m['latitude'])
            lng = float(m['longitude'])
        except (ValueError, KeyError):
            pulados += 1
            continue

        cidade = m['nome'].strip()
        cidade_norm = normalizar(cidade)

        cur.execute("""
            INSERT INTO municipios_ibge (cidade_normalizada, uf, cidade, latitude, longitude)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (cidade_normalizada, uf) DO UPDATE SET
                cidade = EXCLUDED.cidade,
                latitude = EXCLUDED.latitude,
                longitude = EXCLUDED.longitude
        """, (cidade_norm, uf, cidade, lat, lng))
        inseridos += 1

    conn.commit()
    cur.close()
    conn.close()

    print(f'\n✅ {inseridos} municípios importados/atualizados ({pulados} pulados).')

    # Sanity check
    conn = psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        port=os.getenv('DB_PORT', '5432'),
        dbname=os.getenv('DB_NAME', 'postgres'),
        user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD', ''),
    )
    cur = conn.cursor()
    cur.execute("SELECT cidade, uf, latitude, longitude FROM municipios_ibge WHERE cidade_normalizada='VITORIA' AND uf='ES'")
    r = cur.fetchone()
    if r:
        print(f"   Sanity check: Vitória/ES → {r[2]}, {r[3]}")
    cur.close()
    conn.close()


if __name__ == '__main__':
    main()
