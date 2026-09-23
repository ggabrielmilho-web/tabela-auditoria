# -*- coding: utf-8 -*-
"""MEDE a qualidade das pernas vazias de um banco. SO LEITURA.

Tres numeros, todos medidos contra o GPS e nenhum contra o documento:

  A  origem NUNCA visitada  — a carreta nao chegou a 30 km da cidade de onde a perna diz
     que ela saiu, durante a janela da propria perna. A origem da perna e o DESTINO da carga
     anterior; quando esse destino nunca foi provado, a perna nasce num lugar inventado.

  B  ja estava NO DESTINO ao partir — no instante da saida declarada a carreta ja estava a
     menos de 30 km do destino, numa perna de 100 km ou mais. Perna que nao aconteceu.

  C  km planejado somado de A e B — o tamanho do estrago no indicador de km vazio.

    DB_NAME=rizza_lab_0923 python -X utf8 _medir_vazias.py
"""
import os
import sys

_AQUI = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
sys.path.insert(0, _AQUI)
os.chdir(_AQUI)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv('.env')
import psycopg2
import geocoding
import placas as pl

cn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                      user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = cn.cursor()

cur.execute("""
    SELECT c.id, c.numero, c.carreta1_placa, c.origem_cidade,
           c.origem_latitude, c.origem_longitude, d.latitude, d.longitude,
           c.data_carregamento, c.data_saida_real,
           COALESCE(c.data_conclusao, c.no_local_desde) fim,
           c.distancia_planejada_km
      FROM embarques_cargas c
      LEFT JOIN embarques_cargas_destinos d ON d.carga_id = c.id AND d.ordem = 1
     WHERE COALESCE(c.viagem_vazia, FALSE) = TRUE
       AND c.data_carregamento >= CURRENT_DATE - 30
       AND c.carreta1_placa IS NOT NULL AND c.origem_latitude IS NOT NULL
     ORDER BY c.data_carregamento
""")
PERNAS = cur.fetchall()


def pontos(placa, ini, fim):
    cur.execute("""SELECT latitude, longitude, data_posicao
                     FROM embarques_posicoes_historico
                    WHERE placa = ANY(%s) AND data_posicao BETWEEN %s AND %s""",
                (pl.grafias(str(placa).strip().upper()), ini, fim))
    return cur.fetchall()


a_lista, b_lista = [], []
for (cid, num, placa, ocid, ola, oln, dla, dln, dcarg, dsaida, fim, kmplan) in PERNAS:
    from datetime import datetime, timedelta, time as _t
    ini = datetime.combine(dcarg, _t()) - timedelta(hours=12)
    ffim = fim or datetime.utcnow()
    pts = pontos(placa, ini, ffim)
    kmp = float(kmplan or 0)
    if pts:
        dmin = min((geocoding.km_entre(float(la), float(ln), float(ola), float(oln)) or 9e9)
                   for la, ln, _d in pts)
        if dmin > 30:
            a_lista.append((num, ocid, kmp, dmin))
    if dsaida and dla is not None and kmp >= 100:
        perto = pontos(placa, dsaida - timedelta(hours=6), dsaida + timedelta(hours=6))
        if perto:
            dd = min((geocoding.km_entre(float(la), float(ln), float(dla), float(dln)) or 9e9)
                     for la, ln, _d in perto)
            if dd <= 30:
                b_lista.append((num, ocid, kmp, dd))

nums = {x[0] for x in a_lista} | {x[0] for x in b_lista}
km = sum(x[2] for x in a_lista) + sum(x[2] for x in b_lista if x[0] not in {y[0] for y in a_lista})
print(f'banco {os.getenv("DB_NAME")}')
print(f'  pernas vazias na janela ........ {len(PERNAS)}')
print(f'  A origem NUNCA visitada ........ {len(a_lista)}')
print(f'  B ja estava no destino ao sair .. {len(b_lista)}')
print(f'  pernas distintas afetadas ...... {len(nums)}')
print(f'  km planejado envolvido ......... {km:,.0f}'.replace(',', '.'))
kmA = sum(x[2] for x in a_lista); kmB = sum(x[2] for x in b_lista)
tot = sum(float(x[11] or 0) for x in PERNAS)
print(f'     so a regra A ................ {len(a_lista):>3} pernas · {kmA:>8,.0f} km'.replace(',', '.'))
print(f'     so a regra B ................ {len(b_lista):>3} pernas · {kmB:>8,.0f} km'.replace(',', '.'))
print(f'  TOTAL de pernas na janela ...... {len(PERNAS):>3} pernas · {tot:>8,.0f} km'.replace(',', '.'))
print(f'  fracao do km vazio afetada ..... {100*km/tot:.0f}%')
if os.getenv('DETALHE'):
    print('\n  A:', ', '.join(f'{n}({d:.0f}km)' for n, _c, _k, d in sorted(a_lista)))
    print('\n  B:', ', '.join(f'{n}({d:.0f}km)' for n, _c, _k, d in sorted(b_lista)))
cn.rollback()
cn.close()
