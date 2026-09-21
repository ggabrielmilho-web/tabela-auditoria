# -*- coding: utf-8 -*-
"""Gate da correção do `rastreado_via` — a tela fora do cadastro (21/09/2026).

O que se garante aqui, pela ROTA REAL (Flask test client, sem subir porta):

  1. carga com a placa NO cadastro       -> nada muda: mesmo tipo, mesma linha, sem rótulo
                                            novo na resposta;
  2. a MESMA carga com `_placa_tracking` devolvendo None (é exatamente a condição "fora do
     cadastro", simulada sem escrever no banco) -> a tela passa a rastrear pela placa que
     TEM pontos, marca `fora_cadastro`, e o trajeto principal é o mesmo do caso 1;
  3. carga sem ponto nenhum em nenhuma placa -> segue `rastreado_via = null`. "Sem rastreio"
     continua sendo dito só quando é verdade.

O caso 2 é o da C-2026-001011: carreta `TZC9G24` com 352 pontos na janela e cavalo
`AZK2I93` sem um único ponto — o `else` antigo escolhia o cavalo e a tela ficava em branco.

    python -X utf8 _teste_rastreado_via.py
    python -X utf8 _teste_rastreado_via.py --carga C-2026-000662 --sem-gps C-2026-000011
"""
import os
import sys
import argparse

_AQUI = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
sys.path.insert(0, _AQUI)
os.chdir(_AQUI)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv('.env')
import psycopg2
import server
import rastreamento_worker

ap = argparse.ArgumentParser()
ap.add_argument('--carga', default=None, help='carga com placa NO cadastro e com pontos')
ap.add_argument('--sem-gps', default=None, help='carga sem ponto em nenhuma placa')
A = ap.parse_args()

cn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                      user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = cn.cursor()


def _id(numero):
    cur.execute("SELECT id FROM embarques_cargas WHERE numero=%s", (numero,))
    r = cur.fetchone()
    return r[0] if r else None


# A escolha das cargas de teste passa pelas DUAS grafias. A primeira versão deste arquivo
# comparava `p.placa = c.carreta1_placa` cru e elegeu como "carga sem GPS" uma que tinha
# 1.300 pontos gravados na grafia antiga (QXE0830 × QXE0I30) — o teste reprovava o código
# por um defeito do próprio teste. É a mesma armadilha que o `_placa_tracking` documenta.
import placas as _pl


def _pontos(placa, cid):
    if not placa:
        return 0
    cur.execute("""SELECT count(*) FROM embarques_posicoes_historico p, embarques_cargas c
                    WHERE c.id = %s AND p.placa = ANY(%s)
                      AND p.data_posicao BETWEEN c.data_carregamento - interval '12 hours'
                          AND COALESCE(c.data_conclusao, c.no_local_desde, NOW())""",
                (cid, _pl.grafias(str(placa).strip().upper())))
    return cur.fetchone()[0]


def _cargas():
    cur.execute("""SELECT numero, id, cavalo_placa, carreta1_placa, carreta2_placa
                     FROM embarques_cargas
                    WHERE COALESCE(viagem_vazia, FALSE) = FALSE
                    ORDER BY data_carregamento DESC""")
    return cur.fetchall()


def _escolher_com_pontos():
    """A carga com MAIS pontos da carreta na janela, com a placa no cadastro — o caso 1.
    Mais pontos = o trajeto tem o que comparar; com 1 ponto o teste passaria à toa."""
    melhor, n_melhor = (None, None), 0
    for numero, cid, cav, c1, c2 in _cargas():
        if not c1:
            continue
        cur.execute("SELECT 1 FROM embarques_veiculos_rastreio WHERE placa = ANY(%s)",
                    (_pl.grafias(str(c1).strip().upper()),))
        if not cur.fetchone():
            continue
        n = _pontos(c1, cid)
        if n > n_melhor:
            melhor, n_melhor = (numero, cid), n
    return melhor


def _escolher_sem_pontos():
    """Carga sem UM ponto sequer em qualquer placa e em qualquer grafia."""
    for numero, cid, cav, c1, c2 in _cargas():
        if not any((c1, c2, cav)):
            continue
        if all(_pontos(p, cid) == 0 for p in (c1, c2, cav) if p):
            return numero, cid
    return None, None


num_ok, id_ok = (A.carga, _id(A.carga)) if A.carga else _escolher_com_pontos()
num_sem, id_sem = (A.sem_gps, _id(A.sem_gps)) if A.sem_gps else _escolher_sem_pontos()
print(f'carga com cadastro+GPS: {num_ok} (id {id_ok})')
print(f'carga sem GPS nenhum  : {num_sem} (id {id_sem})')

app = server.app
c = app.test_client()
with c.session_transaction() as s:
    s['user_id'] = 1
    s['nome'] = 'gate'
    s['role'] = 'admin'
    s['tipos_permitidos'] = []
    s['paginas_permitidas'] = []


def buscar(carga_id):
    r = c.get(f'/api/rastreamento/cargas/{carga_id}/trajeto')
    assert r.status_code == 200, f'HTTP {r.status_code}'
    return r.get_json()


falhas = []


def checa(nome, cond, detalhe=''):
    print(('   OK   ' if cond else '   FALHA ') + nome + (f'  [{detalhe}]' if detalhe else ''))
    if not cond:
        falhas.append(nome)


print('\n── caso 1: placa NO cadastro (não pode mudar nada)')
d1 = buscar(id_ok)
rv1 = d1.get('rastreado_via')
checa('rastreado_via preenchido', bool(rv1), str(rv1))
checa('sem rótulo de fora do cadastro', not (rv1 or {}).get('fora_cadastro'))
checa('sem rótulo de grafia não casada', not (rv1 or {}).get('grafia_nao_casou'))
tipo1 = (rv1 or {}).get('tipo')
n_pontos1 = len(d1['trajeto'].get(tipo1) or []) if tipo1 else 0
checa('trajeto da placa rastreada não é vazio', n_pontos1 > 0, f'{n_pontos1} pontos')
km1 = (d1.get('kpi') or {}).get('distancia_km')

print('\n── caso 2: a MESMA carga com _placa_tracking = None (fora do cadastro)')
_orig = rastreamento_worker._placa_tracking
rastreamento_worker._placa_tracking = lambda *a, **k: None
try:
    d2 = buscar(id_ok)
finally:
    rastreamento_worker._placa_tracking = _orig
rv2 = d2.get('rastreado_via')
checa('rastreado_via NÃO é null (era o defeito)', bool(rv2), str(rv2))
checa('marcado como fora do cadastro', bool((rv2 or {}).get('fora_cadastro')))
checa('escolheu a MESMA placa do caso 1', (rv2 or {}).get('tipo') == tipo1,
      f"{(rv2 or {}).get('tipo')} vs {tipo1}")
n_pontos2 = len(d2['trajeto'].get((rv2 or {}).get('tipo')) or []) if rv2 else 0
checa('mesmo trajeto do caso 1', n_pontos2 == n_pontos1, f'{n_pontos2} vs {n_pontos1}')
km2 = (d2.get('kpi') or {}).get('distancia_km')
checa('KPI de km igual ao caso 1', km2 == km1, f'{km2} vs {km1}')

print('\n── caso 3: fora do cadastro E sem ponto nenhum — "sem rastreio" tem de continuar')
if id_sem:
    rastreamento_worker._placa_tracking = lambda *a, **k: None
    try:
        d3 = buscar(id_sem)
    finally:
        rastreamento_worker._placa_tracking = _orig
    checa('rastreado_via é null', d3.get('rastreado_via') is None, str(d3.get('rastreado_via')))
    checa('nenhum trajeto desenhado',
          not any(d3['trajeto'].get(k) for k in ('cavalo', 'carreta1', 'carreta2')))
else:
    print('   (pulado: nenhuma carga sem pontos nesta base)')

print(f'\n{"TODOS OS TESTES PASSARAM" if not falhas else "FALHAS: " + ", ".join(falhas)}')
cn.close()
sys.exit(1 if falhas else 0)
