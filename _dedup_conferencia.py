# -*- coding: utf-8 -*-
"""CONFERENCIA DO DEDUP — a regra de prova (27.11) foi exercitada? (so leitura)

Por que existe: o contador `mantida (sem prova de chegada)` so e impresso pelo `_imprimir`
do CLI. O agendador do servidor imprime apenas a SOMA (`N encerrada(s)`), entao em producao
nao ha como saber pelo log se a regra chegou a decidir alguma coisa. Aqui se pergunta o
mesmo que o `dedup_veiculo` pergunta, na mesma ordem e com a MESMA regua:

  1. quais carretas tem 2+ cargas ativas do robo AGORA  (as candidatas do dedup);
  2. para cada uma que nao e a ultima, `_chegou_ao_destino` diz sim ou nao
     -> nao  = e uma `mantida (sem prova de chegada)`: a regra agiu, segurando o fechamento;
     -> sim  = a proxima rodada fecha por `sequencia_viagem`;
  3. a aproximacao maxima do destino, em km, que e o numero que denunciou a C-1008 (715 km)
     e a C-1013 (1.345 km) em 18/09.

Nao grava nada: nenhuma transacao e confirmada, so SELECT.

    python -X utf8 _dedup_conferencia.py
    python -X utf8 _dedup_conferencia.py --horas 36     # janela do log de fechamentos
"""
import os
import sys
import argparse
from collections import defaultdict

# `__file__` nao existe quando o script e PIPADO para dentro do container
# (`docker exec -i ... python -X utf8 - < este_arquivo`), que e como ele roda em
# producao enquanto a imagem nao for refeita. Ali o cwd ja e o /app do Dockerfile.
_AQUI = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
sys.path.insert(0, _AQUI)
os.chdir(_AQUI)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import psycopg2
from dotenv import load_dotenv
load_dotenv('.env')
import embarques_auto as ea
import embarques_continuacao as _ec

ap = argparse.ArgumentParser()
ap.add_argument('--horas', type=int, default=36, help='janela do log de fechamentos')
A = ap.parse_args()

cn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                      user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = cn.cursor()

# A dimensao e a mesma do robo: com a continuacao ligada, so a CARRETA dedupa.
dimensoes = ('carreta1_placa',) if _ec.ligado() else ('cavalo_placa', 'carreta1_placa')
print(f'continuacao ligada: {_ec.ligado()} · dimensoes do dedup: {", ".join(dimensoes)}')

for campo in dimensoes:
    cur.execute(f"""
        SELECT {campo}, id, numero, data_carregamento, status, ctrb_origem
          FROM embarques_cargas
         WHERE status IN ('Aberta','Em rota','No destino','Desengatada')
           AND COALESCE(criada_por_robo, FALSE) = TRUE
           AND {campo} IS NOT NULL AND {campo} <> ''
           {_ec.filtro_ativas(cur, 'embarques_cargas')}
    """)
    grupos = defaultdict(list)
    for placa, cid, num, dt, status, ctrb in cur.fetchall():
        grupos[placa].append((dt, cid, num, status, ctrb))
    duplas = {p: v for p, v in grupos.items() if len(v) > 1}
    print(f'\n=== {campo}: {len(grupos)} placas com carga ativa · {len(duplas)} com 2+ (candidatas) ===')
    manteria = fecharia = 0
    for placa, itens in sorted(duplas.items()):
        itens.sort()
        print(f'\n   {placa}')
        for i, (dt, cid, num, status, ctrb) in enumerate(itens):
            ultima = (i == len(itens) - 1)
            marca = 'ULTIMA (fica aberta por desenho)' if ultima else ''
            prova = None
            if not ultima:
                prova = ea._chegou_ao_destino(cur, cid)
                marca = ('fecharia por sequencia_viagem' if prova
                         else 'MANTIDA (sem prova de chegada)')
                fecharia += 1 if prova else 0
                manteria += 0 if prova else 1
            # aproximacao maxima do destino — o numero que denunciou a C-1008 e a C-1013
            pl_, dla, dln, desde = ea._destino_e_placa(cur, cid)
            perto = '—'
            if dla is not None:
                serie = ea._serie_destino(cur, pl_, dla, dln, desde)
                if serie:
                    perto = f'{min(k for _, k, _v in serie):,.0f} km'.replace(',', '.')
            cur.execute("""SELECT cidade, uf FROM embarques_cargas_destinos
                            WHERE carga_id=%s ORDER BY ordem LIMIT 1""", (cid,))
            d = cur.fetchone()
            print(f'      {num}  carreg {dt}  {status:<12} destino {(d[0] + "/" + d[1]) if d else "—"}'
                  f'  aproximacao max {perto:>10}  {marca}')
    print(f'\n   -> nesta foto: {manteria} mantida(s) sem prova · {fecharia} fecharia(m) na proxima rodada')

# o que a regra JA fechou na janela — o outro lado da mesma moeda
cur.execute("""SELECT c.numero, l.editado_em - interval '3 hours', l.valor_anterior, l.valor_novo
                 FROM embarques_cargas_log l JOIN embarques_cargas c ON c.id = l.carga_id
                WHERE l.valor_novo ILIKE %s AND l.editado_em >= NOW() - (%s || ' hours')::interval
                ORDER BY 2""", ('%sequencia_viagem%', str(A.horas)))
linhas = cur.fetchall()
print(f'\n=== fechamentos por sequencia_viagem nas ultimas {A.horas} h: {len(linhas)} ===')
for num, quando, de, para in linhas:
    print(f'   {num}  {str(quando)[:16]} BRT  {de} -> {para}')

cn.rollback()      # nada desta conferencia entra no banco
cn.close()
