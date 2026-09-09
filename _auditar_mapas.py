# -*- coding: utf-8 -*-
"""Auditoria em LARGA ESCALA dos mapas de carga.

Bate no MESMO endpoint que a tela do mapa consome (/api/rastreamento/cargas/<id>/trajeto)
para cada carga do periodo e classifica os defeitos por TIPO — para consertar classe, nao
instancia. Verifica as quatro camadas que a tela desenha:

    1. ORIGEM      tem coordenada? o marcador verde aparece?
    2. DESTINO     tem coordenada? o pin vermelho aparece?
    3. ROTA        tem polyline do ORS? a linha tracejada existe?
    4. TRAJETO     tem pontos? a linha do percurso existe?
    5. KPI         odometro X haversine X reta — coerentes entre si?

A validacao mais forte e geometrica: a distancia RODOVIARIA e sempre >= a linha reta
entre origem e destino. Odometro abaixo da reta prova que o aparelho nao contou o trecho.

    python -X utf8 _auditar_mapas.py                    # agosto+setembro
    python -X utf8 _auditar_mapas.py --desde 2026-08-01 --ate 2026-08-31
    python -X utf8 _auditar_mapas.py --csv problemas.csv
"""
import os
import sys
import csv
import math
import json
import argparse
import urllib.request
import urllib.error
import http.cookiejar

# A pasta do proprio arquivo, nao um caminho cravado: estes scripts precisam rodar
# TAMBEM dentro do container (Linux), que e de onde o robo atemporal corrige os dados
# de producao. O `c:/Phyton-Projetos/...` que estava aqui quebrava com FileNotFoundError.
_AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _AQUI)
os.chdir(_AQUI)
import psycopg2
from dotenv import load_dotenv

load_dotenv(os.path.join(_AQUI, '.env'))
BASE = 'http://localhost:5000'

ap = argparse.ArgumentParser()
ap.add_argument('--desde', default='2026-08-01')
ap.add_argument('--ate', default='2026-09-30')
ap.add_argument('--email', default='admin@rizzalog.com.br')
ap.add_argument('--senha', default='admin123')
ap.add_argument('--csv')
a = ap.parse_args()


def hav(x1, y1, x2, y2):
    R = 6371.0
    p1, p2 = math.radians(x1), math.radians(x2)
    dp, dl = math.radians(x2 - x1), math.radians(y2 - y1)
    return 2 * R * math.asin(math.sqrt(math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2))


cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
req = urllib.request.Request(BASE + '/login',
                             data=json.dumps({'email': a.email, 'senha': a.senha}).encode(),
                             headers={'Content-Type': 'application/json'})
try:
    op.open(req, timeout=20).read()
except urllib.error.HTTPError as e:
    print('Falha no login: %s — o servidor local esta no ar?' % e)
    raise SystemExit(1)

c = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                     user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = c.cursor()
cur.execute("""SELECT id, numero, status, COALESCE(viagem_vazia, FALSE), origem_cidade,
                      carreta1_placa, cavalo_placa, data_carregamento
                 FROM embarques_cargas
                WHERE data_carregamento BETWEEN %s AND %s ORDER BY id""", (a.desde, a.ate))
cargas = cur.fetchall()
c.close()
print('auditando %d cargas de %s a %s...\n' % (len(cargas), a.desde, a.ate))

from collections import Counter, defaultdict
prob = Counter()
exemplos = defaultdict(list)
linhas_csv = []
falhas = 0

for cid, num, status, vazia, ocid, car, cav, dcarg in cargas:
    try:
        raw = op.open(BASE + '/api/rastreamento/cargas/%d/trajeto' % cid, timeout=30).read()
        d = json.loads(raw)
    except Exception as e:
        prob['ENDPOINT falhou'] += 1
        exemplos['ENDPOINT falhou'].append('%s: %s' % (num, str(e)[:60]))
        falhas += 1
        continue
    if not d.get('ok'):
        prob['ENDPOINT devolveu erro'] += 1
        exemplos['ENDPOINT devolveu erro'].append('%s: %s' % (num, str(d.get('error'))[:60]))
        continue

    k = d.get('kpi') or {}
    rp = d.get('rota_planejada') or {}
    org = d.get('origem') or {}
    dests = d.get('destinos') or []
    # `trajeto` e um DICT {cavalo, carreta1, carreta2}, nao uma lista — contar as
    # chaves daria 3 para toda carga (foi o falso alarme da 1a rodada). O que a tela
    # desenha e a trilha da placa RASTREADA (rastreado_via), com as outras de apoio.
    tj = d.get('trajeto') or {}
    via = str(d.get('rastreado_via') or '')
    if 'arreta 1' in via:
        traj = tj.get('carreta1') or []
    elif 'arreta 2' in via:
        traj = tj.get('carreta2') or []
    elif 'avalo' in via:
        traj = tj.get('cavalo') or []
    else:
        traj = max((tj.get('carreta1') or [], tj.get('carreta2') or [], tj.get('cavalo') or []), key=len)
    todos = len((tj.get('cavalo') or [])) + len((tj.get('carreta1') or [])) + len((tj.get('carreta2') or []))
    achados = []

    # ── 1. ORIGEM
    if org.get('latitude') is None:
        achados.append('ORIGEM sem coordenada (marcador nao aparece)')
    # ── 2. DESTINO
    if not dests:
        achados.append('SEM destino cadastrado')
    elif all(x.get('latitude') is None for x in dests):
        achados.append('DESTINO sem coordenada (pin nao aparece)')
    # ── 3. ROTA planejada
    tem_poly = bool(rp.get('polyline'))
    if not tem_poly:
        achados.append('SEM rota do ORS (linha tracejada nao aparece)')
        if rp.get('distancia_km'):
            achados.append('distancia_planejada_km preenchida SEM polyline')
    # ── 4. TRAJETO
    if not traj and not todos:
        achados.append('SEM trajeto em NENHUMA placa (linha do percurso nao aparece)')
    elif not traj:
        achados.append('placa rastreada sem trajeto (so as outras tem)')
    elif len(traj) < 5:
        achados.append('trajeto com menos de 5 pontos')

    # ── 5. KPI: coerencia geometrica
    odo = k.get('km_odometro')
    gps = k.get('distancia_km') or 0
    reta = None
    if org.get('latitude') is not None and dests and dests[-1].get('latitude') is not None:
        reta = hav(float(org['latitude']), float(org['longitude']),
                   float(dests[-1]['latitude']), float(dests[-1]['longitude']))
    if odo is None:
        achados.append('KM rastreador NULO (aparelho nao reporta odometro)')
    elif status == 'Entregue' and reta is not None and reta > 30 and odo < reta * 0.85:
        # rodoviaria e sempre >= reta -- mas SO na viagem terminada. Quem ainda esta a
        # caminho rodou menos que a reta por definicao, e o produto deixou de barrar esses
        # casos em 07/09 (`_kpi_sanidade`, conserto 2). O auditor tem de medir com a MESMA
        # regua, senao acusa 6 cargas em rota que o produto publica de proposito.
        achados.append('ODOMETRO ABAIXO DA RETA (aparelho nao contou a viagem)')
    elif status != 'Entregue' and gps and gps > 30 and odo < gps * 0.5:
        achados.append('ODOMETRO discorda do GPS na viagem em curso')
    # O teste do jitter nao pode depender do odometro PUBLICADO: quando a sanidade
    # geometrica anula o valor, o jitter ficava invisivel — a V-2026-000025 perdeu o
    # rotulo sem nada nela mudar (gps 311 km contra reta de 124). Usa o odometro BRUTO
    # quando o publicado vem nulo, e a reta/rota como piso alternativo.
    odo_ref = odo if odo is not None else k.get('km_odometro_bruto')
    refs = [x for x in (odo_ref, reta, rp.get('distancia_km')) if x]
    if gps and refs and reta is not None and reta > 30:
        if gps > max(refs) * 2:
            achados.append('HAVERSINE inflado (jitter/saltos de posicao)')
    # ── 6. status X evidencia
    if status == 'Aberta' and not traj:
        achados.append('ABERTA e sem nenhuma posicao (nao dispara alerta hoje)')
    if status == 'Entregue' and reta is not None and reta > 50 and not traj:
        achados.append('ENTREGUE sem trajeto nenhum')

    if achados:
        for x in achados:
            prob[x] += 1
            if len(exemplos[x]) < 4:
                exemplos[x].append('%s (%s%s)' % (num, status, ' · VAZIA' if vazia else ''))
        linhas_csv.append({
            'numero': num, 'id': cid, 'status': status, 'vazia': 'S' if vazia else 'N',
            'origem': ocid, 'carreta': car, 'cavalo': cav, 'data': dcarg,
            'km_odometro': odo, 'km_gps': gps, 'km_reta': round(reta, 1) if reta else None,
            'rota_km': rp.get('distancia_km'), 'tem_polyline': 'S' if tem_poly else 'N',
            'pontos_trajeto': len(traj), 'pontos_todas_placas': todos, 'rastreado_via': via, 'problemas': ' | '.join(achados),
        })

ok = len(cargas) - len(linhas_csv) - falhas
print('=' * 78)
print('  %d cargas · %d sem nenhum problema · %d com pelo menos um' % (len(cargas), ok, len(linhas_csv)))
print('=' * 78)
print('\n%-58s %6s' % ('PROBLEMA', 'CARGAS'))
for kk, v in prob.most_common():
    print('  %-56s %5d' % (kk, v))
    for e in exemplos[kk][:3]:
        print('      %s' % e)

if a.csv and linhas_csv:
    with open(a.csv, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(linhas_csv[0].keys()), delimiter=';')
        w.writeheader()
        w.writerows(linhas_csv)
    print('\n  CSV com o detalhe de cada carga: %s (%d linhas)' % (a.csv, len(linhas_csv)))
