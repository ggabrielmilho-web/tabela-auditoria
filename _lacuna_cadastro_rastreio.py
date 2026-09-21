# -*- coding: utf-8 -*-
"""LACUNA DO CADASTRO DE RASTREIO — quantas cargas a tela esta cegando (so leitura).

A posicao NAO depende do cadastro: o worker grava em `embarques_posicoes_historico` tudo o
que a 3S devolve no polling da conta inteira. Ja o `embarques_veiculos_rastreio` e uma copia
local, preenchida SO pelo botao de sync do Admin (`POST /api/rastreamento/sync-veiculos`).
Quem exige essa copia e apenas um leitor: a tela do mapa, via `_placa_tracking`.

Entao um veiculo que entrou na conta da 3S depois do ultimo sync tem posicao gravada e fica
invisivel para a tela — e, pior, o endpoint cai no `else` e passa a desenhar o trajeto do
CAVALO, que pode ser exatamente a placa sem GPS (C-2026-001011: carreta com 352 pontos na
janela, cavalo com ZERO, tela em branco).

Este script mede o tamanho disso:

  1. o cadastro: quantos veiculos, e de quando e o ultimo sync;
  2. placas com posicao recente que estao FORA do cadastro;
  3. cargas ATIVAS afetadas, em dois graus:
       CEGA   — nenhuma placa no cadastro -> "Sem rastreio" e KPIs zerados;
       TROCA  — a carreta esta fora e o cavalo dentro -> a tela rastreia pelo cavalo sem
                avisar, que e sensor pior (a carreta segue a carga, o cavalo troca).

    python -X utf8 _lacuna_cadastro_rastreio.py
    python -X utf8 _lacuna_cadastro_rastreio.py --dias 15
"""
import os
import sys
import argparse

# `__file__` nao existe quando o script e PIPADO para dentro do container.
_AQUI = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
sys.path.insert(0, _AQUI)
os.chdir(_AQUI)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import psycopg2
from dotenv import load_dotenv
load_dotenv('.env')
import placas as pl

ap = argparse.ArgumentParser()
ap.add_argument('--dias', type=int, default=7, help='janela de "posicao recente"')
A = ap.parse_args()

cn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                      user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = cn.cursor()

cur.execute("SELECT count(*), MIN(sincronizado_em), MAX(sincronizado_em) FROM embarques_veiculos_rastreio")
n_cad, s_min, s_max = cur.fetchone()
print(f'cadastro: {n_cad} veiculos · ultimo sync {s_max} · mais antigo {s_min}')

cur.execute("SELECT placa FROM embarques_veiculos_rastreio")
CAD = set()
for (p,) in cur.fetchall():
    CAD.update(pl.grafias(str(p).strip().upper()))


def no_cadastro(placa):
    if not placa:
        return False
    return any(g in CAD for g in pl.grafias(str(placa).strip().upper()))


cur.execute("""SELECT placa, count(*), MAX(data_posicao)
                 FROM embarques_posicoes_historico
                WHERE data_posicao >= NOW() - (%s || ' days')::interval
                GROUP BY 1 ORDER BY 3 DESC""", (str(A.dias),))
linhas = cur.fetchall()
fora = [(p, n, u) for p, n, u in linhas if not no_cadastro(p)]
print(f'\nplacas com posicao nos ultimos {A.dias} d: {len(linhas)} · '
      f'FORA do cadastro: {len(fora)}')
for p, n, u in fora:
    print(f'   {p:<10} {n:>7} pontos · ultima {str(u)[:16]}')

cur.execute("""SELECT numero, status, data_carregamento, cavalo_placa, carreta1_placa, carreta2_placa
                 FROM embarques_cargas
                WHERE status IN ('Aberta','Em rota','No destino','Desengatada')
                  AND COALESCE(viagem_vazia,FALSE) = FALSE
                ORDER BY data_carregamento""")
cegas, trocas = [], []
for num, status, dt, cav, c1, c2 in cur.fetchall():
    tem = [p for p in (c1, c2, cav) if p]
    if not tem:
        continue
    if not any(no_cadastro(p) for p in tem):
        cegas.append((num, status, dt, cav, c1))
    elif c1 and not no_cadastro(c1) and cav and no_cadastro(cav):
        trocas.append((num, status, dt, cav, c1))

print(f'\n=== cargas ATIVAS cegas na tela (nenhuma placa no cadastro): {len(cegas)} ===')
for num, status, dt, cav, c1 in cegas:
    cur.execute("""SELECT count(*) FROM embarques_posicoes_historico
                    WHERE placa = ANY(%s) AND data_posicao >= NOW() - (%s || ' days')::interval""",
                (pl.grafias(str(c1 or cav).strip().upper()), str(A.dias)))
    n = cur.fetchone()[0]
    print(f'   {num}  {status:<12} carreg {dt}  cavalo {cav or "—":<9} carreta {c1 or "—":<9}'
          f'  · {n} pontos recentes da placa que mede')

print(f'\n=== cargas ATIVAS em que a tela troca a carreta pelo cavalo: {len(trocas)} ===')
for num, status, dt, cav, c1 in trocas:
    print(f'   {num}  {status:<12} carreg {dt}  carreta {c1} FORA · cavalo {cav} dentro')

cn.rollback()
cn.close()
