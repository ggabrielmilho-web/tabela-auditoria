# -*- coding: utf-8 -*-
"""TESTE ANTES DO CÓDIGO — rota pelo endereço: o que mudaria, e o que regrediria (21/09/2026)

Pedido do Gabriel: a rota planejada tem de sair do endereço de origem e chegar no de destino.
Regra dele: testar antes de mexer, nada de regressão. Este script mede no lab, sem alterar
nenhuma linha de produção, os cinco números que decidem o desenho:

  T1 cobertura   quantas cargas do robô têm CNPJ nas duas pontas, e quantos desses CNPJs o
                 Google resolve COM a guarda de cidade — o resto cai no centroide (fallback)
  T2 delta de km rota ORS pelos pontos do endereço x `distancia_planejada_km` de hoje
  T3 REGRESSÃO   o motor decide com esse km: a guarda de plausibilidade rejeita chegada ou
                 move saída quando km/h > VEL_MAX_CRIVEL (100). Conta quantas cargas CRUZAM o
                 limiar em qualquer direção com o km novo — é a regressão que dói
  T4 ETA         quanto o km novo desloca a ETA (km/dia padrão)
  T5 carga ORS   quantas retraçagens a ativação dispararia de uma vez

Só leitura no banco. Chama Google uma vez por CNPJ e ORS uma vez por carga da amostra.

    DB_NAME=rizza_lab_0921 python -X utf8 _teste_rota_por_endereco.py --n 60
"""
import os
import re
import sys
import json
import time
import argparse
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter
from datetime import timedelta

_AQUI = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
sys.path.insert(0, _AQUI)
os.chdir(_AQUI)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import psycopg2
from dotenv import load_dotenv
load_dotenv('.env')
import geocoding
import ors_client

ap = argparse.ArgumentParser()
ap.add_argument('--n', type=int, default=60, help='cargas na amostra do ORS')
ap.add_argument('--dias', type=int, default=30)
ap.add_argument('--chave-de', default='../Rizza/preencher_km_google.py')
ap.add_argument('--pausa', type=float, default=1.6)
A = ap.parse_args()
VEL_MAX = 100.0
KM_DIA = float(os.getenv('KM_DIA_PADRAO', '600'))

cn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                      user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = cn.cursor()
print(f'banco: {os.getenv("DB_NAME")}\n')

# ── T1: cobertura de CNPJ nas cargas do robô
cur.execute("""
    SELECT c.id, c.numero, c.status, c.origem_cnpj, c.destino_cnpj, c.origem_cidade,
           c.origem_latitude, c.origem_longitude, c.distancia_planejada_km,
           c.data_saida_real, c.no_local_desde, c.data_carregamento,
           d.cidade, d.latitude, d.longitude,
           (SELECT count(*) FROM embarques_cargas_destinos x WHERE x.carga_id = c.id) AS n_dest,
           (SELECT count(*) FROM embarques_cargas_rota x WHERE x.carga_id = c.id) AS n_rota
      FROM embarques_cargas c
      LEFT JOIN embarques_cargas_destinos d ON d.carga_id = c.id AND d.ordem = 1
     WHERE COALESCE(c.criada_por_robo, FALSE) AND COALESCE(c.viagem_vazia, FALSE) = FALSE
       AND c.data_carregamento >= CURRENT_DATE - %s
     ORDER BY c.data_carregamento DESC
""", (A.dias,))
CARGAS = cur.fetchall()
amb = [c for c in CARGAS if c[3] and c[4]]
um = [c for c in CARGAS if bool(c[3]) != bool(c[4])]
print(f'T1 · cargas do robô nos últimos {A.dias} d: {len(CARGAS)}')
print(f'     CNPJ nas duas pontas: {len(amb)} ({len(amb)/max(1,len(CARGAS))*100:.0f}%) · só numa: {len(um)} · nenhuma: {len(CARGAS)-len(amb)-len(um)}')
print(f'     (a coleta preenche CNPJ desde 19/09 — antes disso é 0 por construção)')

# ── geocodificação com guarda, uma vez por CNPJ
KEY = re.search(r'API_KEY\s*=\s*["\']([^"\']+)["\']',
                open(A.chave_de, encoding='utf-8', errors='replace').read()).group(1)


def _sa(s):
    s = unicodedata.normalize('NFKD', str(s or ''))
    return ''.join(c for c in s if not unicodedata.combining(c)).upper().strip()


def geocodificar(cnpj):
    cur.execute("SELECT nome, endereco, bairro, cep, cidade, uf FROM locais WHERE cnpj=%s", (cnpj,))
    r = cur.fetchone()
    if not r:
        return None, 'sem cadastro'
    nome, end, bairro, cep, cidade, uf = r
    if not end:
        return None, 'sem endereço'
    d = re.sub(r'\D', '', str(cep or ''))
    d = '0' + d if len(d) == 7 else d
    cep_fmt = f'{d[:5]}-{d[5:]}' if len(d) == 8 else None
    local = f'{cidade} - {uf}' if (cidade and uf) else cidade
    txt = ', '.join(x for x in (end, bairro, local, cep_fmt, 'Brasil') if x)
    u = ('https://maps.googleapis.com/maps/api/geocode/json?'
         + urllib.parse.urlencode({'address': txt, 'region': 'br', 'key': KEY}))
    try:
        with urllib.request.urlopen(u, timeout=30) as resp:
            j = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        return None, f'erro {e}'
    if j.get('status') != 'OK' or not j.get('results'):
        return None, j.get('status')
    r0 = j['results'][0]
    dev = ''
    for comp in r0.get('address_components', []):
        if 'administrative_area_level_2' in comp.get('types', []) or 'locality' in comp.get('types', []):
            dev = comp.get('long_name') or ''
            break
    if cidade and dev and _sa(dev) != _sa(cidade):
        return None, f'guarda: {dev} != {cidade}'
    loc = r0['geometry']['location']
    return (loc['lat'], loc['lng']), r0['geometry'].get('location_type')


cnpjs = sorted({c[3] for c in amb} | {c[4] for c in amb})
print(f'\n     geocodificando {len(cnpjs)} CNPJs (uma vez cada)...')
GEO, motivos = {}, Counter()
for c in cnpjs:
    GEO[c] = geocodificar(c)
    if GEO[c][0] is None:
        motivos[str(GEO[c][1]).split(':')[0]] += 1
    time.sleep(0.15)
ok = sum(1 for v in GEO.values() if v[0])
print(f'     aprovados pela guarda: {ok}/{len(cnpjs)} · barrados: {dict(motivos)}')
prontas = [c for c in amb if GEO.get(c[3], (None,))[0] and GEO.get(c[4], (None,))[0]]
print(f'     cargas com as DUAS pontas resolvidas: {len(prontas)} de {len(amb)} '
      f'({len(prontas)/max(1,len(amb))*100:.0f}%) — o resto cairia no centroide')

# ── T2/T3/T4: ORS pelo endereço numa amostra, com regressão do motor
amostra = [c for c in prontas if c[8] is not None and c[15] == 1 and c[16] == 0][:A.n]
print(f'\nT2 · traçando {len(amostra)} cargas no ORS pelos pontos do endereço (1 destino, sem cidades de rota)...')
res = []
for (cid, num, st, oc, dc, ocid, ola, oln, plan, dsaida, nolocal, dcarg, dcid, dla, dln, _nd, _nr) in amostra:
    po, pd = GEO[oc][0], GEO[dc][0]
    try:
        rota = ors_client.tracar_rota({'lat': po[0], 'lng': po[1]}, {'lat': pd[0], 'lng': pd[1]})
        novo = float(rota['distancia_km'])
    except Exception as e:
        print(f'   {num}: ORS falhou — {str(e)[:60]}')
        time.sleep(A.pausa)
        continue
    time.sleep(A.pausa)
    plan = float(plan)
    off_o = geocoding.km_entre(po[0], po[1], float(ola), float(oln)) if ola is not None else None
    off_d = geocoding.km_entre(pd[0], pd[1], float(dla), float(dln)) if dla is not None else None
    h = ((nolocal - dsaida).total_seconds() / 3600.0) if (dsaida and nolocal and nolocal > dsaida) else None
    v_old = plan / h if h else None
    v_new = novo / h if h else None
    res.append((num, st, ocid, dcid, plan, novo, off_o, off_d, h, v_old, v_new))
    flag = ''
    if v_old is not None and (v_old > VEL_MAX) != (v_new > VEL_MAX):
        flag = f'   <-- CRUZA 100 km/h: {v_old:.0f} -> {v_new:.0f}'
    print(f'   {num}  {(ocid or "")[:12]:<12} -> {(dcid or "")[:12]:<12}  hoje {plan:>7.1f}  endereço {novo:>7.1f} '
          f'({novo-plan:+6.1f} km, {(novo-plan)/plan*100:+5.1f}%)   ponta {off_o or 0:.0f}/{off_d or 0:.0f} km{flag}')

if not res:
    print('\nsem amostra — nada a medir'); sys.exit(0)

deltas = sorted(n - p for _n, _s, _o, _d, p, n, *_ in res)
pct = sorted((n - p) / p * 100 for _n, _s, _o, _d, p, n, *_ in res)
print(f'\n     delta de km (endereço - hoje): mediana {deltas[len(deltas)//2]:+.1f} · p10 {deltas[len(deltas)//10]:+.1f} · p90 {deltas[int(len(deltas)*0.9)]:+.1f}')
print(f'     em %: mediana {pct[len(pct)//2]:+.1f}% · p10 {pct[len(pct)//10]:+.1f}% · p90 {pct[int(len(pct)*0.9)]:+.1f}%')

com_v = [r for r in res if r[9] is not None]
flips = [r for r in com_v if (r[9] > VEL_MAX) != (r[10] > VEL_MAX)]
perto = [r for r in com_v if abs(r[9] - VEL_MAX) / VEL_MAX <= 0.05]
print(f'\nT3 · REGRESSÃO do motor (guarda km/h <= {VEL_MAX:.0f}): {len(com_v)} cargas com saída e chegada')
print(f'     cruzam o limiar com o km novo: {len(flips)}')
for r in flips:
    print(f'        {r[0]}  {r[9]:.1f} -> {r[10]:.1f} km/h')
print(f'     dentro de ±5% do limiar (zona de risco, mesmo sem cruzar aqui): {len(perto)}')
vs = sorted(r[9] for r in com_v)
print(f'     km/h implícito hoje: mediana {vs[len(vs)//2]:.0f} · p90 {vs[int(len(vs)*0.9)]:.0f} · máx {vs[-1]:.0f}')

eta = sorted(abs(n - p) / KM_DIA * 24 for _n, _s, _o, _d, p, n, *_ in res)
print(f'\nT4 · ETA ({KM_DIA:.0f} km/dia): deslocamento mediano {eta[len(eta)//2]:.1f} h · p90 {eta[int(len(eta)*0.9)]:.1f} h')

ativas = [c for c in prontas if c[2] in ('Aberta', 'Em rota', 'No destino', 'Desengatada')]
print(f'\nT5 · ORS na ativação: {len(ativas)} cargas ativas com as duas pontas resolvidas seriam retraçadas '
      f'(cota 2.000/dia; o robô usa ~30)')
cn.close()
