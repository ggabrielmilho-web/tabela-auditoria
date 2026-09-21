# -*- coding: utf-8 -*-
"""ONDE O CAMINHAO REALMENTE PAROU, por CNPJ — a regua para aferir geocodificador.

Passo 1 de 2 do teste de 21/09/2026. Aqui NAO se chama nenhuma API: o script so le o banco
e imprime um CSV com, para cada CNPJ do cadastro `locais`, o ponto em que o veiculo de fato
parou — na origem (carregando) ou no destino (descarregando). O passo 2 geocodifica o
endereco em texto e compara com este ponto.

Por que o ponto do GPS e a regua, e nao o concorrente: ele nao depende da qualidade do texto
do endereco nem de servico externo, e vem com afericao embutida — se as paradas de um mesmo
CNPJ em viagens diferentes se espalham por 300 m, a coordenada e boa; se espalham por 40 km,
aquele CNPJ e cadastro ruim e voce sabe ANTES de usar.

Como o ponto e achado (mesma regua do resto: `embarques_regua`):
  * origem  -> posicoes ANTES da saida registrada, dentro de RAIO do centroide da cidade;
  * destino -> posicoes DEPOIS da saida, dentro de RAIO do centroide da cidade;
  * so as paradas (velocidade <= PARADO_KMH), agrupadas em blocos contiguos de ate 3 km;
  * vence o bloco de MAIOR duracao — carregar/descarregar e a parada longa do trecho;
  * o ponto da carga e a MEDIANA do bloco; o do CNPJ e a mediana das cargas.

    python -X utf8 _pontos_provados_local.py                  # so o perfil "perigoso"
    python -X utf8 _pontos_provados_local.py --perfil todos --n 30
    python -X utf8 _pontos_provados_local.py --csv /tmp/provados.csv
"""
import os
import re
import sys
import csv
import argparse
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, time as _time

# `__file__` nao existe quando o script e PIPADO para dentro do container.
_AQUI = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
sys.path.insert(0, _AQUI)
os.chdir(_AQUI)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import psycopg2
from dotenv import load_dotenv
load_dotenv('.env')
import geocoding
import placas as pl
from embarques_regua import PARADO_KMH

ap = argparse.ArgumentParser()
ap.add_argument('--n', type=int, default=10, help='quantos CNPJs devolver')
ap.add_argument('--perfil', default='perigoso', choices=('perigoso', 'todos'))
ap.add_argument('--raio', type=float, default=60.0, help='km do centroide onde procurar a parada')
ap.add_argument('--min-h', type=float, default=0.5, help='duracao minima da parada, em horas')
ap.add_argument('--csv', default=None, help='arquivo de saida (default: imprime na tela)')
A = ap.parse_args()

cn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                      user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = cn.cursor()

cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name='embarques_cargas' "
            "AND column_name='destino_cnpj'")
if not cur.fetchone():
    print('esta base nao tem as colunas da coleta (origem_cnpj/destino_cnpj) — '
          'rode em producao, onde EMBARQUES_COLETA ja preencheu')
    sys.exit(1)

# ── perfil de endereco que o geocodificador tende a errar em silencio
_ROD = re.compile(r'\b(ROD|RODOVIA|ESTRADA|EST|BR[- ]?\d|KM)\b')


def perigoso(endereco, cep):
    e = (endereco or '').upper()
    return bool(_ROD.search(e)) or not re.search(r'\d', e) \
        or (cep or '').replace('-', '').endswith('000')


cur.execute("SELECT cnpj, nome, endereco, bairro, cep, cidade, uf FROM locais")
LOCAIS = {r[0]: r for r in cur.fetchall()}

# ── cargas que apontam para um CNPJ e tem chegada/saida registrada
cur.execute("""
    SELECT c.id, c.numero, c.origem_cnpj, c.destino_cnpj, c.data_carregamento,
           c.data_saida_real, c.no_local_desde, c.data_conclusao,
           c.origem_latitude, c.origem_longitude, c.cavalo_placa, c.carreta1_placa,
           d.latitude, d.longitude
      FROM embarques_cargas c
      LEFT JOIN embarques_cargas_destinos d ON d.carga_id = c.id
           AND d.ordem = (SELECT MIN(ordem) FROM embarques_cargas_destinos x WHERE x.carga_id = c.id)
     WHERE (c.origem_cnpj IS NOT NULL OR c.destino_cnpj IS NOT NULL)
       AND COALESCE(c.viagem_vazia, FALSE) = FALSE
     ORDER BY c.data_carregamento DESC
""")
CARGAS = cur.fetchall()
print(f'cargas com CNPJ preenchido: {len(CARGAS)}', file=sys.stderr)


def pontos(placa, ini, fim):
    if not placa:
        return []
    cur.execute("""SELECT data_posicao, latitude, longitude, velocidade
                     FROM embarques_posicoes_historico
                    WHERE placa = ANY(%s) AND data_posicao >= %s AND data_posicao < %s
                    ORDER BY data_posicao""",
                (pl.grafias(str(placa).strip().upper()), ini, fim))
    return [(d, float(la), float(ln), v) for d, la, ln, v in cur.fetchall()]


def parada_mais_longa(pts, alvo_lat, alvo_lng):
    """O bloco parado de maior duracao dentro do raio. Devolve (lat, lng, horas, n)."""
    perto = [p for p in pts
             if (geocoding.km_entre(p[1], p[2], alvo_lat, alvo_lng) or 9e9) <= A.raio
             and (p[3] is None or float(p[3]) <= PARADO_KMH)]
    if not perto:
        return None
    blocos, atual = [], [perto[0]]
    for p in perto[1:]:
        base = atual[0]
        if (geocoding.km_entre(p[1], p[2], base[1], base[2]) or 9e9) <= 3.0:
            atual.append(p)
        else:
            blocos.append(atual)
            atual = [p]
    blocos.append(atual)
    melhor = max(blocos, key=lambda b: (b[-1][0] - b[0][0]).total_seconds())
    horas = (melhor[-1][0] - melhor[0][0]).total_seconds() / 3600
    if horas < A.min_h:
        return None
    return (statistics.median(p[1] for p in melhor),
            statistics.median(p[2] for p in melhor), horas, len(melhor))


por_cnpj = defaultdict(list)
for (cid, num, ocnpj, dcnpj, dcarg, dsaida, nolocal, dconc, ola, oln, cav, c1, dla, dln) in CARGAS:
    base = datetime.combine(dcarg, _time()) - timedelta(hours=12)
    fim = dconc or nolocal or (base + timedelta(days=10))
    # carreta primeiro, cavalo como reserva — a mesma precedencia do resto do sistema
    pts = pontos(c1, base, fim) or pontos(cav, base, fim)
    if not pts:
        continue
    corte = dsaida or nolocal
    if ocnpj and ola is not None and corte:
        r = parada_mais_longa([p for p in pts if p[0] <= corte], float(ola), float(oln))
        if r:
            por_cnpj[(ocnpj, 'origem')].append((num, r, float(ola), float(oln)))
    if dcnpj and dla is not None and dsaida:
        r = parada_mais_longa([p for p in pts if p[0] >= dsaida], float(dla), float(dln))
        if r:
            por_cnpj[(dcnpj, 'destino')].append((num, r, float(dla), float(dln)))

linhas = []
for (cnpj, lado), itens in por_cnpj.items():
    loc = LOCAIS.get(cnpj)
    if not loc:
        continue
    _c, nome, end, bairro, cep, cidade, uf = loc
    if A.perfil == 'perigoso' and not perigoso(end, cep):
        continue
    lat = statistics.median(i[1][0] for i in itens)
    lng = statistics.median(i[1][1] for i in itens)
    disp = max((geocoding.km_entre(i[1][0], i[1][1], lat, lng) or 0) for i in itens)
    km_cent = geocoding.km_entre(lat, lng, itens[0][2], itens[0][3])
    linhas.append({
        'cnpj': cnpj, 'lado': lado, 'nome': nome, 'endereco': end, 'bairro': bairro,
        'cep': cep, 'cidade': cidade, 'uf': uf,
        'n_cargas': len(itens),
        'lat_provado': f'{lat:.6f}', 'lng_provado': f'{lng:.6f}',
        # o centroide VAI JUNTO, não só a distância até ele: o passo 2 precisa medir o
        # centroide e o endereço com a MESMA régua (distância rodoviária do Google), senão
        # estaria comparando linha reta com rota — dois números que não se comparam.
        'lat_centroide': f'{float(itens[0][2]):.6f}', 'lng_centroide': f'{float(itens[0][3]):.6f}',
        'dispersao_km': f'{disp:.2f}',
        'km_do_centroide': f'{km_cent:.2f}' if km_cent is not None else '',
        'horas_parado': f'{statistics.median(i[1][2] for i in itens):.1f}',
        'cargas': ' '.join(i[0] for i in itens[:4]),
    })

# mais cargas primeiro (mais prova), e entre iguais o que mais foge do centroide
linhas.sort(key=lambda r: (-r['n_cargas'], -float(r['km_do_centroide'] or 0)))
linhas = linhas[:A.n]
campos = ['cnpj', 'lado', 'nome', 'endereco', 'bairro', 'cep', 'cidade', 'uf', 'n_cargas',
          'lat_provado', 'lng_provado', 'lat_centroide', 'lng_centroide',
          'dispersao_km', 'km_do_centroide', 'horas_parado', 'cargas']
saida = open(A.csv, 'w', newline='', encoding='utf-8') if A.csv else sys.stdout
w = csv.DictWriter(saida, campos, delimiter=';', lineterminator='\n')
w.writeheader()
w.writerows(linhas)
if A.csv:
    saida.close()
    print(f'-> {A.csv} ({len(linhas)} linhas)')
print(f'{len(por_cnpj)} pares CNPJ/lado com parada provada · {len(linhas)} no CSV',
      file=sys.stderr)
cn.close()
