# -*- coding: utf-8 -*-
"""Roda o robo diario REAL (embarques_auto.executar) dia a dia na base LOCAL com a chave ligada.
NUNCA em producao: liga EMBARQUES_AUTO e GRAVA. E o gate da §24 — o gabarito e o _replay_producao.py."""
import os, sys, time
from datetime import date, timedelta
from collections import Counter
os.environ.update(START_WORKER='false', PGR_SYNC_CADASTRO='false',
                  EMBARQUES_AUTO='true', EMBARQUES_CONTINUACAO='true', EMBARQUES_AUTO_MAX_ROTAS='0')
sys.path.insert(0, r'c:\Phyton-Projetos\Tabela Auditoria')
import server, embarques_auto as ea
tok = server.get_token()
conn = server.get_db()
tot = Counter(); criadas = 0
d = date(2026, 8, 21)
while d <= date(2026, 9, 10):
    t0 = time.time()
    r = ea.executar(dia=d, token=tok, conn=conn)
    criadas += r['criadas']
    f = {k: v for k, v in r['fechadas'].items()}
    for k, v in f.items():
        tot[k] += v
    print(f"{d}  criadas={r['criadas']:2}  {dict(f)}  ({time.time()-t0:.1f}s)")
    d += timedelta(days=1)
print('\nTOTAL criadas:', criadas)
for k, v in tot.most_common():
    print(f'  {v:4}  {k}')
