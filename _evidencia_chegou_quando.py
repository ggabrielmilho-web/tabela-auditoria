# -*- coding: utf-8 -*-
"""QUANDO A EVIDENCIA CHEGOU — o ponto de GPS e antigo, mas entrou no banco quando?

Por que existe: o V1 do aferidor ("a placa rastreada nunca esteve na origem") so dispara
quando a placa TEM pontos na janela. Carreta muda cai em S1 e sai pela porta do `continue`,
sem V1. Entao uma carreta que estava muda e volta a falar — ou que recebe backfill — pode
fazer TODAS as cargas dela no periodo virarem V1 de uma vez, sem nada ter mudado no robo,
na regra ou no documento. Foi essa a suspeita levantada pelo salto 44 -> 64 em 20/09.

A `embarques_posicoes_historico` nao guarda instante de insercao, mas o `id` e BIGSERIAL:
ids vizinhos foram gravados juntos. Entao o "relogio da gravacao" de uma linha e o
data_posicao MAIS NOVO entre os vizinhos de id — para uma linha ao vivo isso e ela mesma;
para uma linha que entrou por backfill, os vizinhos sao do dia da carga do backfill.

    ponto de 18/09 com vizinhos de 20/09  ->  entrou em 20/09, depois do gabarito
    ponto de 18/09 com vizinhos de 18/09  ->  entrou ao vivo, ja estava no gabarito

So leitura.

    python -X utf8 _evidencia_chegou_quando.py _afer_mesma_janela.csv
    python -X utf8 _evidencia_chegou_quando.py CSV --codigo V1 --corte 2026-09-18
"""
import os
import sys
import csv
import argparse
from collections import defaultdict, Counter
from datetime import datetime, timedelta, time as _time, timezone

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
from embarques_regua import RAIO_ORIGEM, JANELA_EVIDENCIA_D

ap = argparse.ArgumentParser()
ap.add_argument('csv')
ap.add_argument('--codigo', default='V1')
ap.add_argument('--corte', default='2026-09-18',
                help='data da rodada-gabarito: o que entrou depois dela e evidencia nova')
ap.add_argument('--vizinhos', type=int, default=400, help='janela de ids para o relogio de gravacao')
A = ap.parse_args()
CORTE = datetime.strptime(A.corte, '%Y-%m-%d')
HOJE = datetime.now(timezone.utc).replace(tzinfo=None)

cn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                      user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = cn.cursor()

alvo = set()
with open(A.csv, encoding='utf-8-sig', newline='') as f:
    for r in csv.DictReader(f, delimiter=';'):
        if (r.get('codigo') or '').strip() == A.codigo and r.get('carga'):
            alvo.add(r['carga'].strip())
print(f'{len(alvo)} cargas com {A.codigo} em {A.csv}')

cur.execute("""SELECT numero, id, data_carregamento, origem_cidade, origem_latitude, origem_longitude,
                      cavalo_placa, carreta1_placa, carreta2_placa, COALESCE(viagem_vazia, FALSE)
                 FROM embarques_cargas WHERE numero = ANY(%s)
                ORDER BY data_carregamento, numero""", (sorted(alvo),))
CARGAS = cur.fetchall()


def relogio(ponto_id):
    """O data_posicao mais novo entre os vizinhos de id — o instante em que esta linha foi
    gravada, aproximado pelo fluxo ao vivo que estava entrando junto com ela."""
    cur.execute("""SELECT MAX(data_posicao) FROM embarques_posicoes_historico
                    WHERE id BETWEEN %s AND %s""",
                (ponto_id - A.vizinhos, ponto_id + A.vizinhos))
    return cur.fetchone()[0]


print(f'\n{"carga":<15} {"carreg":<11} {"placa":<9} {"pts":>5}  {"1o ponto (data_posicao)":<22}'
      f' {"gravado por volta de":<22} veredito')
tally = Counter()
por_placa = defaultdict(list)
for (num, cid, dcarg, ocid, ola, oln, cav, c1, c2, vazia) in CARGAS:
    base = datetime.combine(dcarg, _time()) - timedelta(hours=12)
    fim = min(HOJE, datetime.combine(dcarg, _time()) + timedelta(days=JANELA_EVIDENCIA_D))
    principal, pts = None, []
    for placa in (c1, c2, cav):        # mesma precedencia do aferidor
        if not placa:
            continue
        cur.execute("""SELECT id, data_posicao, latitude, longitude
                         FROM embarques_posicoes_historico
                        WHERE placa = ANY(%s) AND data_posicao >= %s AND data_posicao < %s
                        ORDER BY data_posicao""",
                    (pl.grafias(str(placa).strip().upper()), base, fim))
        linhas = cur.fetchall()
        if linhas:
            principal, pts = placa, linhas
            break
    if not principal:
        tally['sem pontos (seria S1, nao V1)'] += 1
        print(f'{num:<15} {str(dcarg):<11} {"—":<9} {0:>5}')
        continue
    pid, pdata = pts[0][0], pts[0][1]
    quando = relogio(pid)
    novo = quando is not None and quando >= CORTE
    # a placa chegou a encostar na origem?
    if ola is not None:
        perto = min((geocoding.km_entre(float(la), float(ln), float(ola), float(oln)) or 9e9)
                    for _i, _d, la, ln in pts)
    else:
        perto = None
    v = ('EVIDENCIA NOVA: entrou depois de ' + A.corte) if novo else 'ja estava no gabarito'
    tally[v] += 1
    por_placa[pl.mercosul(principal)].append((num, novo, len(pts)))
    print(f'{num:<15} {str(dcarg):<11} {principal:<9} {len(pts):>5}  {str(pdata)[:19]:<22}'
          f' {str(quando)[:19]:<22} {v}'
          + (f' · min origem {perto:,.0f} km'.replace(',', '.') if perto is not None else ''))

print('\n--- resumo ---')
for k, n in tally.most_common():
    print(f'   {n:>4}  {k}')

print(f'\n--- por PLACA principal (a carreta muda leva junto todas as cargas dela) ---')
print(f'{"placa":<10} {"cargas":>7} {"evid. nova":>11}  pontos na janela (min..max)')
for placa, itens in sorted(por_placa.items(), key=lambda x: -len(x[1])):
    novos = sum(1 for _n, nv, _p in itens if nv)
    ns = [p for _n, _nv, p in itens]
    cur.execute("""SELECT count(*), MIN(data_posicao), MAX(data_posicao)
                     FROM embarques_posicoes_historico WHERE placa = ANY(%s)""",
                (pl.grafias(placa),))
    tot, pmin, pmax = cur.fetchone()
    print(f'{placa:<10} {len(itens):>7} {novos:>11}  {min(ns)}..{max(ns)}'
          f'   · total da placa {tot} pts  {str(pmin)[:16]} .. {str(pmax)[:16]}')

cn.rollback()
cn.close()
