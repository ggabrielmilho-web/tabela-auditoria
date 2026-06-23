"""
Helpers de geocoding e cálculo de distância.
- km_entre: Haversine (linha reta sobre a esfera da Terra)
- normalizar_cidade: UPPERCASE + sem acento (chave de match)
- geocoder_municipio: busca centroide IBGE em municipios_ibge
"""

import os
import unicodedata
from math import radians, sin, cos, asin, sqrt
from datetime import datetime, timedelta, time as _time, date as _date
import psycopg2
from dotenv import load_dotenv

load_dotenv()

RECONSTRUCAO_MAX_DIAS = int(os.getenv('RASTREAMENTO_RECONSTRUCAO_DIAS', '15'))
MARGEM_FUSO_H = 12     # absorve skew de fuso no piso da janela
BBOX_GRAUS = 0.3       # ~33 km em torno do centroide da origem


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


def indice_saida_origem(coords, origem_lat, origem_lng, raio_km=30):
    """Índice da PRIMEIRA saída da origem — recorta só o trecho PRÉ-origem (caminhão
    rodando/chegando antes do lançamento). A viagem passa a começar no ponto mais perto
    do pátio do BLOCO INICIAL perto da origem.

    Pega o ponto mais próximo da origem APENAS dentro do primeiro bloco contíguo dentro
    do raio (a "estadia inicial" na origem). Assim, uma viagem que VOLTA pra base
    (origem→destino→origem) não tem o recorte puxado pro ponto da volta — o que zerava o
    trajeto/KPI.

    `coords`: lista de (lat, lng) na ordem cronológica.
    Sem origem (None) ou nenhum ponto dentro do raio → 0 (não recorta; degrada sem surpresa).
    """
    if origem_lat is None or origem_lng is None or not coords:
        return 0
    # 1) Primeira entrada na origem (1º ponto dentro do raio)
    start = None
    for i, (la, ln) in enumerate(coords):
        if la is None or ln is None:
            continue
        d = km_entre(origem_lat, origem_lng, la, ln)
        if d is not None and d <= raio_km:
            start = i
            break
    if start is None:
        return 0  # nunca passou perto da origem → não recorta (degrada como hoje)
    # 2) Bloco contíguo dentro do raio a partir de `start`; escolhe o ponto mais perto
    #    do pátio NESSE bloco inicial (encerra ao sair do raio; tolera ponto inválido).
    melhor_i, melhor_d = start, None
    for i in range(start, len(coords)):
        la, ln = coords[i]
        if la is None or ln is None:
            continue
        d = km_entre(origem_lat, origem_lng, la, ln)
        if d is None or d > raio_km:
            break  # saiu da origem → fim do bloco inicial
        if melhor_d is None or d < melhor_d:
            melhor_d, melhor_i = d, i
    return melhor_i


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


def cidade_por_coord(lat, lng, conn=None):
    """Município mais próximo na municipios_ibge (bbox + menor distância).
    Retorna (cidade, uf) ou None. Útil pra resolver a cidade quando o 3S não manda."""
    if lat is None or lng is None:
        return None
    lat, lng = float(lat), float(lng)
    fechar = False
    if conn is None:
        conn = _get_db(); fechar = True
    cur = conn.cursor()
    cur.execute(
        "SELECT cidade, uf, latitude, longitude FROM municipios_ibge "
        "WHERE latitude BETWEEN %s AND %s AND longitude BETWEEN %s AND %s",
        (lat - 0.5, lat + 0.5, lng - 0.5, lng + 0.5)
    )
    rows = cur.fetchall()
    cur.close()
    if fechar:
        conn.close()
    melhor, melhor_d = None, None
    for cidade, uf, la, ln in rows:
        d = km_entre(lat, lng, float(la), float(ln))
        if d is not None and (melhor_d is None or d < melhor_d):
            melhor_d, melhor = d, (cidade, uf)
    return melhor


def detectar_inicio_viagem(placa, origem_cidade, origem_uf, data_carregamento, conn,
                           origem_lat=None, origem_lng=None, max_dias=RECONSTRUCAO_MAX_DIAS):
    """Detecta a SAÍDA da origem (último ponto na cidade de origem) no histórico do veículo.
    Match por (cidade, uf) por nome OU por bbox em torno do centroide da origem — o bbox
    cobre 'cidade' vazia e CD na periferia, sem geoquery por ponto. Roda 1x por carga
    (o worker persiste). Retorna datetime (saída) ou None (caller usa fallback)."""
    if not placa:
        return None
    placa = placa.strip().upper()
    agora = datetime.utcnow()
    # Piso: data_carregamento@00:00 (com margem de fuso), limitado pelo teto de max_dias
    bound = agora - timedelta(days=max_dias)
    if data_carregamento:
        dc = data_carregamento
        if isinstance(dc, _date) and not isinstance(dc, datetime):
            dc = datetime.combine(dc, _time())
        dc = dc - timedelta(hours=MARGEM_FUSO_H)
        if dc > bound:
            bound = dc

    # Centroide da origem (pro bbox): usa o passado ou geocoda pelo nome
    if origem_lat is None or origem_lng is None:
        origem_lat, origem_lng = geocoder_municipio(origem_cidade, origem_uf, conn=conn)

    cond, params = [], []
    if origem_cidade and origem_uf:
        cond.append("(UPPER(cidade) LIKE %s AND uf = %s)")
        params += [f"%{origem_cidade.upper()}%", origem_uf.upper()]
    if origem_lat is not None and origem_lng is not None:
        cond.append("(latitude BETWEEN %s AND %s AND longitude BETWEEN %s AND %s)")
        params += [float(origem_lat) - BBOX_GRAUS, float(origem_lat) + BBOX_GRAUS,
                   float(origem_lng) - BBOX_GRAUS, float(origem_lng) + BBOX_GRAUS]
    if not cond:
        return None

    cur = conn.cursor()
    cur.execute(
        f"""SELECT data_posicao FROM embarques_posicoes_historico
            WHERE placa = %s AND data_posicao BETWEEN %s AND %s AND ({' OR '.join(cond)})
            ORDER BY data_posicao DESC LIMIT 1""",
        [placa, bound, agora] + params
    )
    r = cur.fetchone()
    cur.close()
    return r[0] if r else None


if __name__ == '__main__':
    # Sanity check
    print('Vitória/ES:', geocoder_municipio('Vitória', 'ES'))
    print('Uberlândia/MG:', geocoder_municipio('Uberlândia', 'MG'))
    print('SAO PAULO/SP:', geocoder_municipio('SAO PAULO', 'SP'))
    d = km_entre(-18.91, -48.27, -20.31, -40.31)
    print(f'Uberlândia → Vitória (linha reta): {d:.1f} km')
