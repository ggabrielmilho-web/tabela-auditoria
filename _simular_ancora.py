# -*- coding: utf-8 -*-
"""SIMULADOR DE ÂNCORA — trocar o centroide muda alguma decisão? (só leitura)

Pergunta do Gabriel em 21/09/2026: dá para simular as três âncoras e ver se haveria regressão?
Aqui se responde SEM tocar em régua nenhuma — o script só refaz a pergunta da chegada com um
ponto de referência diferente e compara o resultado com o que está gravado.

    A · centroide           o que está no ar hoje (`geocoder_municipio`, centro do município)
    B · endereço            o ponto que o Google devolve para o endereço do cadastro `locais`,
                            com a GUARDA DE CIDADE: se o Google responder outra cidade, a
                            resposta é descartada (foi ela que pegou a Nestlé caindo em São
                            Simão/SP, 76 km, com selo ROOFTOP — o `location_type` não denuncia)
    C · GPS                 onde o caminhão de fato parou naquele CNPJ, em LEAVE-ONE-OUT

**Leave-one-out não é preciosismo.** A âncora C é construída das paradas das próprias cargas;
julgar a carga X com um ponto que ela ajudou a formar é corrigir a própria prova — o número
sairia lindo e não valeria nada (é o erro do simulador da Verda, §19). Então, para julgar X,
o ponto do CNPJ é calculado só com as OUTRAS cargas. De quebra isso mede uma coisa útil
sozinho: quantos CNPJs têm histórico suficiente para ancorar uma carga nova.

A régua da decisão é a MESMA do motor e do aferidor: `embarques_regua.chegada`, sobre a série
`(instante, km até o ponto, velocidade)` que o `embarques_auto._serie_destino` monta. O que
muda entre A, B e C é só o PONTO.

Três eixos de comparação, que é o que "regressão" quer dizer aqui:

    perdeu prova   tinha chegada com o centroide e não tem mais   <- a regressão que dói
    ganhou prova   não tinha e passa a ter                        <- o ganho, confere um a um
    moveu          continua tendo, mas em outro instante          <- empurra data_conclusao

    python -X utf8 _simular_ancora.py
    DB_NAME=rizza_lab_0921 python -X utf8 _simular_ancora.py --lado destino --n 200
"""
import os
import re
import sys
import json
import time
import argparse
import statistics
import urllib.parse
import urllib.request
from collections import defaultdict, Counter
from datetime import datetime, timedelta, time as _time

_AQUI = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
sys.path.insert(0, _AQUI)
os.chdir(_AQUI)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import psycopg2
from dotenv import load_dotenv
load_dotenv('.env')
import geocoding
import placas as pl
import embarques_regua as regua
import embarques_auto as ea
from embarques_regua import PARADO_KMH

ap = argparse.ArgumentParser()
ap.add_argument('--lado', default='destino', choices=('destino',),
                help='a régua `chegada` julga o destino; a saída usa outra função')
ap.add_argument('--n', type=int, default=0, help='limite de cargas (0 = todas)')
ap.add_argument('--raio-parada', type=float, default=60.0)
ap.add_argument('--min-h', type=float, default=0.5)
ap.add_argument('--chave-de', default='../Rizza/preencher_km_google.py')
ap.add_argument('--sem-google', action='store_true', help='pula a âncora B')
A = ap.parse_args()

cn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                      user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = cn.cursor()
print(f'banco: {os.getenv("DB_NAME")}')

cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name='embarques_cargas' "
            "AND column_name='destino_cnpj'")
if not cur.fetchone():
    print('base sem as colunas da coleta — rode contra o dump de produção')
    sys.exit(1)

cur.execute("""
    SELECT c.id, c.numero, c.status, c.destino_cnpj, c.data_carregamento, c.data_saida_real,
           c.no_local_desde, c.data_conclusao, c.cavalo_placa, c.carreta1_placa,
           d.cidade, d.uf, d.latitude, d.longitude
      FROM embarques_cargas c
      JOIN embarques_cargas_destinos d ON d.carga_id = c.id
           AND d.ordem = (SELECT MIN(ordem) FROM embarques_cargas_destinos x WHERE x.carga_id = c.id)
     WHERE c.destino_cnpj IS NOT NULL AND COALESCE(c.viagem_vazia, FALSE) = FALSE
       AND d.latitude IS NOT NULL
     ORDER BY c.data_carregamento
""")
CARGAS = cur.fetchall()
if A.n:
    CARGAS = CARGAS[:A.n]
print(f'{len(CARGAS)} cargas com destino_cnpj e destino com coordenada\n')


def placa_da(cid, c1, cav, dcarg, fim):
    """A carreta mede; o cavalo é reserva. Mesma precedência do resto."""
    for p in (c1, cav):
        if not p:
            continue
        cur.execute("""SELECT 1 FROM embarques_posicoes_historico WHERE placa = ANY(%s)
                        AND data_posicao BETWEEN %s AND %s LIMIT 1""",
                    (pl.grafias(str(p).strip().upper()), dcarg - timedelta(hours=12), fim))
        if cur.fetchone():
            return p
    return None


def parada_no_destino(cid, placa, alat, alng, desde):
    """Onde o veículo parou perto do destino — a matéria-prima da âncora C."""
    cur.execute("""SELECT data_posicao, latitude, longitude, velocidade
                     FROM embarques_posicoes_historico
                    WHERE placa = ANY(%s) AND data_posicao >= %s
                    ORDER BY data_posicao""",
                (pl.grafias(str(placa).strip().upper()), desde))
    pts = [(d, float(la), float(ln), v) for d, la, ln, v in cur.fetchall()]
    perto = [p for p in pts
             if (geocoding.km_entre(p[1], p[2], alat, alng) or 9e9) <= A.raio_parada
             and (p[3] is None or float(p[3]) <= PARADO_KMH)]
    if not perto:
        return None
    blocos, atual = [], [perto[0]]
    for p in perto[1:]:
        if (geocoding.km_entre(p[1], p[2], atual[0][1], atual[0][2]) or 9e9) <= 3.0:
            atual.append(p)
        else:
            blocos.append(atual)
            atual = [p]
    blocos.append(atual)
    m = max(blocos, key=lambda b: (b[-1][0] - b[0][0]).total_seconds())
    if (m[-1][0] - m[0][0]).total_seconds() / 3600 < A.min_h:
        return None
    return statistics.median(p[1] for p in m), statistics.median(p[2] for p in m)


# ── matéria-prima da âncora C, por CNPJ (guardando a contribuição de CADA carga)
print('medindo as paradas por CNPJ (âncora C)...')
paradas = defaultdict(dict)
META = {}
for (cid, num, st, cnpj, dcarg, dsaida, nolocal, dconc, cav, c1, dcid, duf, dla, dln) in CARGAS:
    base = datetime.combine(dcarg, _time()) - timedelta(hours=12)
    fim = dconc or nolocal or (base + timedelta(days=10))
    placa = placa_da(cid, c1, cav, datetime.combine(dcarg, _time()), fim)
    META[cid] = (num, st, cnpj, dcarg, dsaida, nolocal, dconc, placa, dcid, duf, float(dla), float(dln))
    if not placa:
        continue
    p = parada_no_destino(cid, placa, float(dla), float(dln), dsaida or base)
    if p:
        paradas[cnpj][cid] = p
print(f'   {len(paradas)} CNPJs com pelo menos uma parada provada')


def ancora_c(cnpj, excluir_cid):
    """Leave-one-out: o ponto do CNPJ sem a contribuição da carga que está sendo julgada."""
    outros = [v for k, v in paradas.get(cnpj, {}).items() if k != excluir_cid]
    if not outros:
        return None
    return (statistics.median(p[0] for p in outros), statistics.median(p[1] for p in outros))


# ── âncora B: Google, uma vez por CNPJ, com guarda de cidade
GEO = {}
if not A.sem_google:
    KEY = re.search(r'API_KEY\s*=\s*["\']([^"\']+)["\']',
                    open(A.chave_de, encoding='utf-8', errors='replace').read()).group(1)

    def _sem_acento(s):
        import unicodedata
        s = unicodedata.normalize('NFKD', str(s or ''))
        return ''.join(c for c in s if not unicodedata.combining(c)).upper().strip()

    def geocodificar(cnpj):
        cur.execute("SELECT nome, endereco, bairro, cep, cidade, uf FROM locais WHERE cnpj=%s", (cnpj,))
        r = cur.fetchone()
        if not r:
            return None, 'sem cadastro'
        nome, end, bairro, cep, cidade, uf = r
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
        # GUARDA DE CIDADE — a única que funcionou no teste dos 10 locais
        devolvida = ''
        for comp in r0.get('address_components', []):
            if 'administrative_area_level_2' in comp.get('types', []) or \
               'locality' in comp.get('types', []):
                devolvida = comp.get('long_name') or ''
                break
        if cidade and devolvida and _sem_acento(devolvida) != _sem_acento(cidade):
            return None, f'guarda de cidade: {devolvida} != {cidade}'
        loc = r0['geometry']['location']
        return (loc['lat'], loc['lng']), r0['geometry'].get('location_type')

    cnpjs = sorted({m[2] for m in META.values()})
    print(f'geocodificando {len(cnpjs)} CNPJs (uma chamada cada)...')
    barrados = Counter()
    for c in cnpjs:
        GEO[c] = geocodificar(c)
        if GEO[c][0] is None:
            barrados[str(GEO[c][1])[:40]] += 1
        time.sleep(0.15)
    print(f'   {sum(1 for v in GEO.values() if v[0])} com ponto · {sum(1 for v in GEO.values() if not v[0])} barrados')
    for k, n in barrados.most_common():
        print(f'      {n:>3}  {k}')


def julga(placa, lat, lng, desde):
    serie = ea._serie_destino(cur, placa, lat, lng, desde)
    if not serie:
        return None, 'sem série'
    inst, como = regua.chegada(serie)
    return inst, como


print('\njulgando cada carga com as três âncoras...')
placar = Counter()
linhas = []
for cid, (num, st, cnpj, dcarg, dsaida, nolocal, dconc, placa, dcid, duf, dla, dln) in META.items():
    if not placa:
        placar['sem placa com GPS'] += 1
        continue
    desde = dsaida or (datetime.combine(dcarg, _time()) - timedelta(hours=12))
    a_inst, a_como = julga(placa, dla, dln, desde)
    b = GEO.get(cnpj, (None, 'n/d'))[0]
    b_inst = julga(placa, b[0], b[1], desde)[0] if b else None
    c = ancora_c(cnpj, cid)
    c_inst = julga(placa, c[0], c[1], desde)[0] if c else None
    linhas.append((num, st, dcid, cnpj, a_inst, b_inst, c_inst, bool(b), bool(c)))
    for nome, inst, tem in (('B/endereço', b_inst, bool(b)), ('C/GPS', c_inst, bool(c))):
        if not tem:
            placar[f'{nome}: sem âncora'] += 1
        elif a_inst and not inst:
            placar[f'{nome}: PERDEU prova'] += 1
        elif inst and not a_inst:
            placar[f'{nome}: ganhou prova'] += 1
        elif inst and a_inst:
            dh = abs((inst - a_inst).total_seconds()) / 3600
            placar[f'{nome}: igual' if dh < 0.5 else f'{nome}: moveu o instante'] += 1
        else:
            placar[f'{nome}: sem prova nos dois'] += 1

print(f'\n--- placar contra a âncora de hoje (centroide), {len(linhas)} cargas ---')
for k, n in sorted(placar.items()):
    print(f'   {n:>4}  {k}')

print('\n--- as que MUDAM de veredito ---')
print(f'{"carga":<15} {"destino":<20} {"A centroide":<20} {"B endereço":<20} {"C GPS":<20}')
def f(x):
    return str(x)[:16] if x else '— sem prova'
for num, st, dcid, cnpj, a, b, c, tb, tc in linhas:
    if (tb and bool(a) != bool(b)) or (tc and bool(a) != bool(c)):
        print(f'{num:<15} {(dcid or "")[:20]:<20} {f(a):<20} {f(b) if tb else "(sem âncora)":<20} '
              f'{f(c) if tc else "(sem âncora)":<20}')

horas = [abs((c - a).total_seconds()) / 3600
         for _n, _s, _d, _c, a, _b, c, _tb, tc in linhas if a and c and tc]
if horas:
    horas.sort()
    print(f'\nC/GPS · deslocamento do instante quando os dois provam: mediana '
          f'{horas[len(horas)//2]:.1f} h · pior {horas[-1]:.1f} h · n={len(horas)}')
cn.close()
