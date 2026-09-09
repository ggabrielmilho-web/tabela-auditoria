# -*- coding: utf-8 -*-
"""REDERIVA a janela das viagens vazias a partir das cargas de HOJE — no lugar, com log.

Por que existe
==============
A perna vazia nao tem evento proprio: a janela dela e inteiramente derivada das cargas
vizinhas da mesma carreta —

    inicio = data_conclusao da carga A          (fim da viagem anterior)
    fim    = data_saida_real da carga B         (inicio da viagem seguinte)

E essas duas ancoras sao exatamente o que o robo atemporal corrige a cada passada. Resultado
medido em 09/09/26: das 63 pernas, **9 nao casavam mais** com a conclusao atual da carga
anterior (eram 13 antes do conserto do anel, que ja melhorou 4). Ou seja: os EVENTOS da perna
convergiram sobre uma JANELA que nao convergiu. Isso e pior que estar de fora do circuito,
porque a perna passou pelo robo e *parece* auditada.

Por que nao e o `_regerar_vazias_agosto.py`
===========================================
Aquele script faz DELETE + INSERT: apaga as pernas, apaga o `embarques_cargas_log` delas e
renumera tudo por sequencia. Serve para criar do zero, nao para reconciliar — usa-lo agora
destruiria a trilha de auditoria que o robo atemporal escreveu e trocaria a identidade das
pernas (o V- de cada uma). Aqui a perna e ATUALIZADA no lugar, e toda alteracao deixa rastro,
que e a mesma regra do resto do modelo (secao 5 do handoff).

O pareamento
============
A perna guarda a identidade dela nas pontas: `origem_cidade` e o DESTINO da carga A e o
destino da perna e a ORIGEM da carga B. Entao a perna se reconhece por
(carreta, origem, destino) — e nao por id, que nao existe como vinculo. Quando ha mais de uma
perna com as mesmas pontas para a mesma carreta, desempata pela proximidade no tempo.

    python -X utf8 _rederivar_vazias.py             # dry-run
    python -X utf8 _rederivar_vazias.py --aplicar
"""
import os
import sys
import csv
import argparse
import unicodedata
from collections import defaultdict, Counter

sys.path.insert(0, r'c:/Phyton-Projetos/Tabela Auditoria')
os.chdir(r'c:/Phyton-Projetos/Tabela Auditoria')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import psycopg2
from dotenv import load_dotenv
load_dotenv('.env')
import placas as pl

AUTOR = 'Rederivacao de vazias'

ap = argparse.ArgumentParser()
ap.add_argument('--desde', default='2026-08-01')
ap.add_argument('--ate', default='2026-09-08')
ap.add_argument('--aplicar', action='store_true')
ap.add_argument('--csv', default='_rederivar_vazias.csv')
A = ap.parse_args()

cn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'),
                      dbname=os.getenv('DB_NAME'), user=os.getenv('DB_USER'),
                      password=os.getenv('DB_PASSWORD'))
cur = cn.cursor()


def nrm(s):
    return unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().upper().strip()


# ── as cargas REAIS, por carreta, em ordem: e delas que sai toda janela de perna
cur.execute("""SELECT c.id, c.numero, c.carreta1_placa, c.data_carregamento,
                      c.data_conclusao, COALESCE(c.data_saida_real, c.inicio_viagem),
                      c.origem_cidade, d.cidade
                 FROM embarques_cargas c
                 LEFT JOIN LATERAL (SELECT cidade FROM embarques_cargas_destinos x
                                     WHERE x.carga_id = c.id ORDER BY x.ordem DESC LIMIT 1) d ON TRUE
                WHERE NOT COALESCE(c.viagem_vazia, FALSE)
                  AND c.status <> 'Cancelada'
                  AND c.carreta1_placa IS NOT NULL AND c.carreta1_placa <> ''
                ORDER BY c.carreta1_placa, c.data_carregamento, c.id""")
por_car = defaultdict(list)
for r in cur.fetchall():
    por_car[pl.mercosul(r[2]) or r[2]].append(r)

# ── os pares consecutivos (A, B) e a janela que cada um define
pares = defaultdict(list)                       # (carreta, org, dst) -> [(ini, fim, A, B)]
for car, lst in por_car.items():
    for i in range(len(lst) - 1):
        Ax, Bx = lst[i], lst[i + 1]
        ini, fim = Ax[4], Bx[5]                 # conclusao de A, saida de B
        org, dst = Ax[7], Bx[6]                 # destino de A, origem de B
        if not org or not dst:
            continue
        pares[(car, nrm(org), nrm(dst))].append((ini, fim, Ax[1], Bx[1]))

# ── as pernas existentes
cur.execute("""SELECT c.id, c.numero, c.carreta1_placa, c.data_carregamento,
                      c.data_saida_real, c.inicio_viagem, c.data_conclusao,
                      c.origem_cidade, d.cidade, c.distancia_planejada_km, c.observacoes
                 FROM embarques_cargas c
                 LEFT JOIN LATERAL (SELECT cidade FROM embarques_cargas_destinos x
                                     WHERE x.carga_id = c.id ORDER BY x.ordem LIMIT 1) d ON TRUE
                WHERE COALESCE(c.viagem_vazia, FALSE)
                  AND c.data_carregamento BETWEEN %s AND %s
                ORDER BY c.numero""", (A.desde, A.ate))
PERNAS = cur.fetchall()

resumo, mudancas, detalhe = Counter(), [], []

for pid, num, car, dcar, dsai, iviag, dconc, org, dst, dist, obs in PERNAS:
    if not car:
        resumo['sem carreta — nao se pareia'] += 1
        continue
    chave = (pl.mercosul(car) or car, nrm(org), nrm(dst))
    cands = pares.get(chave) or []
    if not cands:
        resumo['sem par (A,B) correspondente hoje'] += 1
        detalhe.append({'perna': num, 'situacao': 'sem par', 'de': '', 'para': '',
                        'ini_atual': str(dsai or iviag)[:16], 'ini_novo': '',
                        'fim_atual': str(dconc)[:16], 'fim_novo': ''})
        continue

    # desempate por proximidade: a perna certa e a do par cuja janela esta mais perto da atual
    ref = dsai or iviag or dconc
    ini, fim, na, nb = min(
        cands, key=lambda x: abs(((x[0] or x[1]) - ref).total_seconds()) if (x[0] or x[1]) and ref else 1e18)

    if ini is None or fim is None:
        resumo['par incompleto (A sem conclusao ou B sem saida)'] += 1
        continue
    if fim <= ini:
        # Nao se maquia com janela artificial: se as cargas se sobrepoem, o problema esta NAS
        # CARGAS. A perna fica como esta e vira achado.
        resumo['par SOBREPOSTO hoje (fim <= inicio) — perna contestada'] += 1
        detalhe.append({'perna': num, 'situacao': 'par sobreposto', 'de': na, 'para': nb,
                        'ini_atual': str(dsai or iviag)[:16], 'ini_novo': str(ini)[:16],
                        'fim_atual': str(dconc)[:16], 'fim_novo': str(fim)[:16]})
        continue

    # ── PLAUSIBILIDADE (a trava da secao 12.5, aplicada a janela e nao so ao km).
    # Uma perna vazia e um reposicionamento: se a janela nao cabe na distancia, aquilo nao e
    # uma perna, e uma LACUNA NAO DOCUMENTADA com conteudo desconhecido dentro. O caso-tipo e
    # a V-2026-000035 (Brasilia -> Uberlandia): as ancoras de hoje dizem 07/08 14:55 ->
    # 29/08 21:30, ou seja 22 DIAS, contra os 15 h que ela tinha gravados.
    # Por decisao de 09/09/26 (secao 8 nº 2) esses buracos sao Carreteiro e estao FORA DE
    # ESCOPO: a janela e rederivada porque e a verdade sobre o intervalo, mas a perna leva
    # rotulo para a tela nao desenhar 22 dias de reposicionamento como se fosse viagem.
    horas = (fim - ini).total_seconds() / 3600.0
    cabivel = (float(dist or 0) / 600.0) * 24.0 + 24.0 if dist else 24.0
    implausivel = horas > 3 * cabivel

    campos = {}
    if (dsai or iviag) != ini:
        campos['data_saida_real'] = ini
        campos['inicio_viagem'] = ini
        campos['data_carregamento'] = ini.date()
    if dconc != fim:
        campos['data_conclusao'] = fim
    if implausivel:
        rot = (f'LACUNA NAO DOCUMENTADA: {horas/24:.1f} dias para {float(dist or 0):.0f} km '
               f'de rota — nao e reposicionamento, e intervalo sem carga (provavel Carreteiro, '
               f'fora do escopo do robo)')
        if rot not in (obs or ''):
            campos['observacoes'] = ((obs + ' | ') if obs else '') + rot
        resumo['LACUNA nao documentada (rotulada, fora de escopo)'] += 1
    if not campos:
        resumo['ja estava coerente'] += 1
        continue

    resumo['PERNAS REDERIVADAS'] += 1
    for k in campos:
        resumo[f'campo: {k}'] += 1
    mudancas.append((pid, num, campos))
    detalhe.append({'perna': num, 'situacao': 'rederivada', 'de': na, 'para': nb,
                    'ini_atual': str(dsai or iviag)[:16], 'ini_novo': str(ini)[:16],
                    'fim_atual': str(dconc)[:16], 'fim_novo': str(fim)[:16]})

print(f'REDERIVACAO DE VAZIAS — {len(PERNAS)} pernas entre {A.desde} e {A.ate}')
print(f'{"[DRY-RUN] " if not A.aplicar else ""}ancora: conclusao da carga A -> saida da carga B\n')
for k, v in sorted(resumo.items(), key=lambda x: (not x[0].startswith('PERNAS'), -x[1])):
    print(f'   {k:<48} {v:>4}')

if detalhe:
    print('\nDETALHE')
    for d in detalhe:
        print(f'   {d["perna"]:<16} {d["situacao"]:<16} {d["ini_atual"]:>16} -> {d["ini_novo"]:<16} '
              f'{d["fim_atual"]:>16} -> {d["fim_novo"]}')

with open(A.csv, 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.DictWriter(f, ['perna', 'situacao', 'de', 'para', 'ini_atual', 'ini_novo',
                           'fim_atual', 'fim_novo'], delimiter=';')
    w.writeheader()
    w.writerows(detalhe)
print(f'\n-> {A.csv} ({len(detalhe)} linhas)')

if not A.aplicar:
    print('\n[DRY-RUN] nada gravado. Use --aplicar para gravar.')
    cn.close()
    sys.exit(0)

n = 0
for pid, num, campos in mudancas:
    cur.execute("""SELECT data_carregamento, data_saida_real, inicio_viagem, data_conclusao,
                          observacoes FROM embarques_cargas WHERE id=%s""", (pid,))
    antes = dict(zip(['data_carregamento', 'data_saida_real', 'inicio_viagem', 'data_conclusao',
                      'observacoes'], cur.fetchone()))
    sets = ', '.join(f'{k}=%s' for k in campos)
    cur.execute(f"UPDATE embarques_cargas SET {sets}, atualizado_em=NOW() WHERE id=%s",
                list(campos.values()) + [pid])
    for k, v in campos.items():
        cur.execute("""INSERT INTO embarques_cargas_log
                          (carga_id, campo, valor_anterior, valor_novo, usuario_nome, editado_em)
                       VALUES (%s,%s,%s,%s,%s,NOW())""",
                    (pid, k, str(antes.get(k)) if antes.get(k) is not None else None,
                     str(v), AUTOR))
    n += 1
cn.commit()
print(f'\nGRAVADO: {n} pernas rederivadas + log de auditoria em cada campo.')
cn.close()
