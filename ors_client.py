"""
Cliente OpenRouteService (https://openrouteservice.org/).
Free tier: 2.000 chamadas/dia, 40/min.

Endpoint usado: POST /v2/directions/driving-hgv (HGV = Heavy Goods Vehicle)
Retorna polyline encoded + distância total + duração estimada.

Loga toda chamada em embarques_3s_log com provider='ORS'.
"""

import os
import time
import json
import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv('OPENROUTE_API_KEY', '')
BASE_URL = 'https://api.openrouteservice.org/v2/directions/driving-hgv'


class ORSError(Exception):
    pass


def _get_db():
    return psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        port=os.getenv('DB_PORT', '5432'),
        dbname=os.getenv('DB_NAME', 'postgres'),
        user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD', ''),
    )


def _log(endpoint, duracao_ms, status_http, erro_codigo=None, erro_msg=None):
    try:
        conn = _get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO embarques_3s_log (provider, endpoint, duracao_ms, status_http, erro_codigo, erro_msg) "
            "VALUES ('ORS', %s, %s, %s, %s, %s)",
            (endpoint, duracao_ms, status_http, erro_codigo, erro_msg)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


def tracar_rota(origem, destino):
    """Calcula rota driving-hgv entre 2 pontos.

    Args:
        origem: dict com 'lat' e 'lng' (ou 'latitude'/'longitude')
        destino: idem

    Returns:
        dict com 'polyline' (encoded), 'distancia_km', 'duracao_min', 'geometry' (list de [lng, lat])
    """
    if not API_KEY:
        raise ORSError('OPENROUTE_API_KEY não configurada no .env')

    o_lat = origem.get('lat') or origem.get('latitude')
    o_lng = origem.get('lng') or origem.get('longitude')
    d_lat = destino.get('lat') or destino.get('latitude')
    d_lng = destino.get('lng') or destino.get('longitude')

    if None in (o_lat, o_lng, d_lat, d_lng):
        raise ORSError(f'Coordenadas inválidas: origem={origem} destino={destino}')

    # ORS espera [lng, lat] (GeoJSON convention)
    body = {
        'coordinates': [[float(o_lng), float(o_lat)], [float(d_lng), float(d_lat)]],
        'instructions': False,
        'geometry': True,
    }
    headers = {
        'Authorization': API_KEY,
        'Content-Type': 'application/json',
        'Accept': 'application/json, application/geo+json',
    }

    t0 = time.time()
    try:
        resp = requests.post(BASE_URL, json=body, headers=headers, timeout=30)
        dur_ms = int((time.time() - t0) * 1000)
        _log('directions/driving-hgv', dur_ms, resp.status_code)
        resp.raise_for_status()
        data = resp.json()
    except requests.HTTPError as e:
        dur_ms = int((time.time() - t0) * 1000)
        msg = ''
        try:
            msg = e.response.text[:500]
        except Exception:
            pass
        _log('directions/driving-hgv', dur_ms, e.response.status_code, str(e.response.status_code), msg)
        raise ORSError(f'ORS HTTP {e.response.status_code}: {msg}')
    except Exception as e:
        dur_ms = int((time.time() - t0) * 1000)
        _log('directions/driving-hgv', dur_ms, 0, 'EXCEPTION', str(e))
        raise ORSError(f'ORS falhou: {e}')

    # Resposta padrão ORS: data.routes[0].geometry (polyline encoded) + summary.distance/duration
    try:
        route = data['routes'][0]
        polyline = route['geometry']  # encoded polyline
        distancia_m = route['summary']['distance']
        duracao_s = route['summary']['duration']
    except (KeyError, IndexError, TypeError) as e:
        raise ORSError(f'Resposta ORS sem rota: {e}')

    return {
        'polyline': polyline,
        'distancia_km': round(distancia_m / 1000, 1),
        'duracao_min': int(duracao_s / 60),
    }


def decodificar_polyline(polyline_str, precision=5):
    """Decodifica polyline encoded (algoritmo Google) → lista de [lat, lng].
    ORS usa precision=5 por padrão.
    """
    coordinates = []
    index = 0
    lat = 0
    lng = 0
    length = len(polyline_str)
    factor = 10 ** precision

    while index < length:
        # latitude
        shift = 0
        result = 0
        while True:
            b = ord(polyline_str[index]) - 63
            index += 1
            result |= (b & 0x1f) << shift
            shift += 5
            if b < 0x20:
                break
        dlat = ~(result >> 1) if (result & 1) else (result >> 1)
        lat += dlat

        # longitude
        shift = 0
        result = 0
        while True:
            b = ord(polyline_str[index]) - 63
            index += 1
            result |= (b & 0x1f) << shift
            shift += 5
            if b < 0x20:
                break
        dlng = ~(result >> 1) if (result & 1) else (result >> 1)
        lng += dlng

        coordinates.append([lat / factor, lng / factor])

    return coordinates


if __name__ == '__main__':
    # Sanity check: Uberlândia → Vitória
    result = tracar_rota(
        {'lat': -18.9141, 'lng': -48.2749},
        {'lat': -20.3155, 'lng': -40.3128}
    )
    print(f"Distância: {result['distancia_km']} km")
    print(f"Duração: {result['duracao_min']} min ({result['duracao_min']/60:.1f}h)")
    print(f"Polyline (primeiros 50 chars): {result['polyline'][:50]}...")
    coords = decodificar_polyline(result['polyline'])
    print(f"Polyline decodificada: {len(coords)} pontos")
    print(f"Primeiro ponto: {coords[0]}, último: {coords[-1]}")
