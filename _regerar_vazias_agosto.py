# -*- coding: utf-8 -*-
"""Regera as viagens VAZIAS de agosto/2026 com janela por EVENTO REAL.

O gerador anterior derivava a janela de `data_carregamento`, que e DATE (meia-noite),
misturada com `data_conclusao`, que tem hora. Quando a carga seguinte carregava no
mesmo dia, meia-noite < conclusao e um `if` fabricava uma janela de 1 hora. Medido:
das 93 vazias, 45 (48%) ficaram com ~1h e 30 (32%) passaram de um dia.

Regra nova, so evento real:
    inicio = data_conclusao da carga A          (fim de viagem, com hora)
    fim    = data_saida_real (ou inicio_viagem) da carga B
    descarta se faltar um dos dois, ou se fim <= inicio
    (carga sobreposta e problema NAS CARGAS — nao se maquia com janela artificial)

km vem do ODOMETRO na janela. Sem GPS a perna e criada mesmo assim, com km nulo e
rotulo: o deslocamento vazio aconteceu, so nao foi medido — apagar seria pior.

    python -X utf8 _regerar_vazias_agosto.py            # so mostra
    python -X utf8 _regerar_vazias_agosto.py --aplicar
"""
import os
import sys
import math
import argparse
import unicodedata

sys.path.insert(0, r'c:/Phyton-Projetos/Tabela Auditoria')
os.chdir(r'c:/Phyton-Projetos/Tabela Auditoria')
import psycopg2
import placas
from dotenv import load_dotenv

load_dotenv(r'c:/Phyton-Projetos/Tabela Auditoria/.env')
MIN_KM = 50

ap = argparse.ArgumentParser()
ap.add_argument('--aplicar', action='store_true')
a = ap.parse_args()


def nrm(s):
    return unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().upper().strip()


def hav(x1, y1, x2, y2):
    R = 6371.0
    p1, p2 = math.radians(x1), math.radians(x2)
    dp, dl = math.radians(x2 - x1), math.radians(y2 - y1)
    return 2 * R * math.asin(math.sqrt(math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2))


c = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                     user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = c.cursor()
cur.execute("SELECT cidade_normalizada, uf, latitude, longitude FROM municipios_ibge")
CENT = {(nrm(x), y): (float(p), float(q)) for x, y, p, q in cur.fetchall()}

cur.execute("""
    SELECT c.id, c.numero, c.carreta1_placa, c.cavalo_placa, c.cavalo_tipo, c.motorista_nome,
           c.tipo_operacao, c.data_carregamento, c.data_conclusao,
           COALESCE(c.data_saida_real, c.inicio_viagem) AS partiu,
           c.origem_cidade, c.origem_uf, d.cidade, d.uf, d.latitude, d.longitude
      FROM embarques_cargas c
      LEFT JOIN LATERAL (SELECT * FROM embarques_cargas_destinos x
                          WHERE x.carga_id=c.id ORDER BY x.ordem DESC LIMIT 1) d ON TRUE
     WHERE c.data_carregamento BETWEEN '2026-08-01' AND '2026-08-31'
       AND NOT COALESCE(c.viagem_vazia, FALSE)
       AND c.carreta1_placa IS NOT NULL AND c.carreta1_placa <> ''
     ORDER BY c.carreta1_placa, c.data_carregamento, c.id""")
por_car = {}
for r in cur.fetchall():
    por_car.setdefault(placas.mercosul(r[2]) or r[2], []).append(r)

from collections import Counter
desc = Counter()
novas = []
for car, lst in por_car.items():
    for i in range(len(lst) - 1):
        A, B = lst[i], lst[i + 1]
        ini, fim = A[8], B[9]
        if ini is None:
            desc['carga A sem data_conclusao'] += 1
            continue
        if fim is None:
            desc['carga B sem saida real'] += 1
            continue
        if fim <= ini:
            desc['cargas sobrepostas (fim <= inicio)'] += 1
            continue
        dcid, duf, dlat, dlng = A[12], A[13], A[14], A[15]
        ocid, ouf = B[10], B[11]
        if not dcid or not ocid:
            desc['sem cidade nas pontas'] += 1
            continue
        if nrm(dcid) == nrm(ocid):
            desc['recarregou no mesmo lugar'] += 1
            continue
        oc = CENT.get((nrm(ocid), (ouf or '')[:2].upper()))
        if dlat is None or not oc:
            desc['sem coordenada'] += 1
            continue
        reta = hav(float(dlat), float(dlng), oc[0], oc[1])
        if reta < MIN_KM:
            desc['menos de %d km (manobra)' % MIN_KM] += 1
            continue
        cur.execute("""SELECT odometer FROM embarques_posicoes_historico
                        WHERE placa=ANY(%s) AND odometer IS NOT NULL AND odometer>0
                          AND data_posicao BETWEEN %s AND %s ORDER BY data_posicao""",
                    (placas.grafias(A[2]), ini, fim))
        odos = [int(x[0]) for x in cur.fetchall()]
        km = None
        if len(odos) >= 2:
            km = sum(d for d in (odos[k] - odos[k - 1] for k in range(1, len(odos))) if 0 < d <= 200)
        # TRAVA DE PLAUSIBILIDADE. Janela muito maior que a viagem cabivel nao e uma
        # perna vazia — e uma lacuna com conteudo desconhecido dentro (viagem de
        # Carreteiro, que o robo nao abre, ou dias de patio). Nesse caso a perna
        # existe, mas o km NAO e dela: fica nulo e rotulado.
        horas = (fim - ini).total_seconds() / 3600
        esperado_h = reta / 600.0 * 24 + 24        # 600 km/dia + 1 dia de folga
        janela_longa = horas > 3 * esperado_h
        if janela_longa:
            km = None
        novas.append({'carreta': A[2], 'cavalo': B[3], 'cavalo_tipo': B[4] or 'Cavalo',
                      'motorista': B[5] or 'A definir', 'tipo': B[6] or 'Frota',
                      'o_ci': dcid, 'o_uf': duf, 'd_ci': ocid, 'd_uf': ouf,
                      'dlat': oc[0], 'dlng': oc[1], 'ini': ini, 'fim': fim,
                      'reta': reta, 'km': km, 'de': A[1], 'para': B[1],
                      'longa': janela_longa, 'h': horas})
        if km is not None:
            desc['CRIADA (km medido)'] += 1
        elif janela_longa:
            desc['CRIADA (janela longa - km nao atribuido)'] += 1
        else:
            desc['CRIADA (sem GPS na janela)'] += 1

print('=== REGERACAO das viagens vazias de agosto ===')
for k, v in desc.most_common():
    print('  %-38s %4d' % (k, v))
com = [n for n in novas if n['km'] is not None]
print('\n  %d pernas · %d com km medido pelo odometro' % (len(novas), len(com)))
if novas:
    hs = sorted(n['h'] for n in novas)
    print('  janela: mediana %.1fh · p90 %.1fh · max %.1fh'
          % (hs[len(hs) // 2], hs[9 * len(hs) // 10], hs[-1]))
if com:
    print('  km odometro somado: %s' % format(sum(n['km'] for n in com), ',d').replace(',', '.'))
    print('  km linha reta somado: %s' % format(int(sum(n['reta'] for n in com)), ',d').replace(',', '.'))
print('\n  %-9s %-18s %-18s %6s %6s %6s' % ('carreta', 'de', 'para', 'h', 'reta', 'odo'))
for n in sorted(novas, key=lambda x: -(x['km'] or 0))[:12]:
    print('  %-9s %-18s %-18s %6.1f %6.0f %6s'
          % (n['carreta'], n['o_ci'][:18], n['d_ci'][:18], n['h'], n['reta'],
             n['km'] if n['km'] is not None else '—'))

if a.aplicar:
    cur.execute("SELECT id FROM embarques_cargas WHERE numero LIKE 'V-2026-%'")
    velhas = [r[0] for r in cur.fetchall()]
    cur.execute("DELETE FROM embarques_cargas_destinos WHERE carga_id = ANY(%s)", (velhas,))
    cur.execute("DELETE FROM embarques_cargas_log WHERE carga_id = ANY(%s)", (velhas,))
    cur.execute("DELETE FROM embarques_cargas WHERE id = ANY(%s)", (velhas,))
    print('\n  %d vazias antigas removidas' % len(velhas))
    cur.execute("SELECT setval(pg_get_serial_sequence('embarques_cargas','id'), "
                "(SELECT MAX(id) FROM embarques_cargas))")
    seq = 0
    for n in novas:
        seq += 1
        if n['km'] is not None:
            rot = ''
        elif n['longa']:
            rot = ' | JANELA LONGA (%.0f dias) — km nao atribuido' % (n['h'] / 24)
        else:
            rot = ' | SEM GPS na janela — km nao medido'
        cur.execute("""
            INSERT INTO embarques_cargas
              (numero, status, tipo_operacao, cliente_nome, viagem_vazia,
               cavalo_placa, cavalo_tipo, carreta1_placa, motorista_nome, motorista_cpf,
               origem_cidade, origem_uf, data_carregamento, data_saida_real, data_conclusao,
               criada_por_robo, encerrada_motivo, distancia_planejada_km, observacoes)
            VALUES (%s,'Entregue',%s,NULL,TRUE,%s,%s,%s,%s,'',%s,%s,%s,%s,%s,TRUE,
                    'reposicionamento',%s,%s) RETURNING id""",
            ('V-2026-%06d' % seq, n['tipo'], n['cavalo'], n['cavalo_tipo'], n['carreta'],
             n['motorista'], n['o_ci'], (n['o_uf'] or 'MG')[:2], n['ini'].date(), n['ini'],
             n['fim'], n['km'],
             'Vazia reconstruida: %s -> %s · janela %.1fh%s' % (n['de'], n['para'], n['h'], rot)))
        cid = cur.fetchone()[0]
        cur.execute("""INSERT INTO embarques_cargas_destinos
                       (carga_id, ordem, cidade, uf, latitude, longitude)
                       VALUES (%s,1,%s,%s,%s,%s)""",
                    (cid, n['d_ci'], (n['d_uf'] or 'MG')[:2], n['dlat'], n['dlng']))
    c.commit()
    print('  OK — %d viagens vazias regeradas' % len(novas))
c.close()
