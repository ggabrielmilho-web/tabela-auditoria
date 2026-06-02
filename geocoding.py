"""
Helpers de geocoding e cálculo de distância.
- km_entre: Haversine (linha reta sobre a esfera da Terra)
- normalizar_cidade: UPPERCASE + sem acento (chave de match)
- geocoder_municipio: busca centroide IBGE em municipios_ibge
"""

import os
import unicodedata
from math import radians, sin, cos, asin, sqrt
import psycopg2
from dotenv import load_dotenv

load_dotenv()


def _get_db():
    return psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        port=os.getenv('DB_PORT', '5432'),
        dbname=os.getenv('DB_NAME', 'postgres'),
        user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD', ''),
    )


def km_entre(lat1, lng1, lat2, lng2):
    """Distância em km entre duas coordenadas (Haversine)."""
    if None in (lat1, lng1, lat2, lng2):
        return None
    R = 6371
    lat1, lng1, lat2, lng2 = map(radians, [float(lat1), float(lng1), float(lat2), float(lng2)])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlng / 2) ** 2
    return 2 * R * asin(sqrt(a))


def normalizar_cidade(s):
    """UPPERCASE + sem acento + trim. Compara 'São Paulo' == 'SAO PAULO'."""
    if not s:
        return ''
    nfkd = unicodedata.normalize('NFKD', str(s))
    sem_acento = ''.join(c for c in nfkd if not unicodedata.combining(c))
    return sem_acento.upper().strip()


def geocoder_municipio(cidade, uf, conn=None):
    """Busca centroide do município em municipios_ibge.
    Retorna (lat, lng) ou (None, None) se não achar.
    Aceita conn externa pra reusar conexão em loops.
    """
    if not cidade or not uf:
        return (None, None)
    cidade_norm = normalizar_cidade(cidade)
    uf_norm = uf.upper().strip()[:2]

    fechar = False
    if conn is None:
        conn = _get_db()
        fechar = True

    cur = conn.cursor()
    cur.execute(
        "SELECT latitude, longitude FROM municipios_ibge WHERE cidade_normalizada=%s AND uf=%s",
        (cidade_norm, uf_norm)
    )
    r = cur.fetchone()
    cur.close()
    if fechar:
        conn.close()

    if r:
        return (float(r[0]), float(r[1]))
    return (None, None)


if __name__ == '__main__':
    # Sanity check
    print('Vitória/ES:', geocoder_municipio('Vitória', 'ES'))
    print('Uberlândia/MG:', geocoder_municipio('Uberlândia', 'MG'))
    print('SAO PAULO/SP:', geocoder_municipio('SAO PAULO', 'SP'))
    d = km_entre(-18.91, -48.27, -20.31, -40.31)
    print(f'Uberlândia → Vitória (linha reta): {d:.1f} km')
