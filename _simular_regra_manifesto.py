# -*- coding: utf-8 -*-
"""Simula a regra proposta para `fechar_pendentes` (conserto 8 do handoff).

REGRA HOJE:    qualquer manifesto novo da placa fecha a carga anterior.
REGRA PROPOSTA: nao fecha quando o manifesto novo tem o MESMO destino da carga
                aberta E foi emitido com o veiculo JA EM ROTA (origem do novo
                manifesto != origem da carga aberta). Isso e o caso do reforco de
                carga no hub (C-2026-000550 x C-2026-000605: Belem->Embu fechada por
                um manifesto emitido em Uberlandia, tambem para Embu).

O juiz e o GPS: a carga fechada chegou ao destino DEPOIS do fechamento? Se sim, o
fechamento foi prematuro e suprimi-lo e acerto.

Aviso de escopo: aqui os "manifestos" sao as CARGAS do robo (uma carga = um manifesto).
Manifesto fora do escopo do robo (CARRETEIRO) nao aparece — a simulacao mede o que da
para medir, nao o universo inteiro.
"""
import os, sys, unicodedata
from datetime import datetime as dt, timedelta as td, time as _time
# A pasta do proprio arquivo, nao um caminho cravado: estes scripts precisam rodar
# TAMBEM dentro do container (Linux), que e de onde o robo atemporal corrige os dados
# de producao. O `c:/Phyton-Projetos/...` que estava aqui quebrava com FileNotFoundError.
_AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _AQUI); os.chdir(_AQUI)
import psycopg2
from dotenv import load_dotenv
load_dotenv('.env')
import geocoding as geo, placas as pl

RAIO = 20.0
conn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                        user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = conn.cursor()

def norm(s):
    return ''.join(ch for ch in unicodedata.normalize('NFD', (s or '').upper()) if ch.isalnum())

cur.execute("""SELECT e.id, e.numero, e.cavalo_placa, e.carreta1_placa, e.origem_cidade, e.data_carregamento,
                      e.data_saida_real, e.data_conclusao, e.status, COALESCE(e.criada_por_robo,FALSE),
                      d.cidade, d.latitude, d.longitude
                 FROM embarques_cargas e
                 LEFT JOIN embarques_cargas_destinos d ON d.carga_id=e.id
                      AND d.ordem=(SELECT MIN(ordem) FROM embarques_cargas_destinos x WHERE x.carga_id=e.id)
                WHERE e.data_carregamento BETWEEN '2026-08-01' AND '2026-09-30'
                  AND COALESCE(e.viagem_vazia,FALSE)=FALSE
                ORDER BY e.data_carregamento, e.id""")
cargas = [dict(zip(['id','numero','cav','c1','ocid','dcarg','dsaida','dconc','status','robo','dcid','dla','dln'], r))
          for r in cur.fetchall()]

def chegou_em(placa, dla, dln, ini, fim):
    if not placa or dla is None: return None
    cur.execute("""SELECT data_posicao, latitude, longitude FROM embarques_posicoes_historico
                    WHERE placa=ANY(%s) AND data_posicao BETWEEN %s AND %s ORDER BY data_posicao""",
                (pl.grafias(placa), ini, fim))
    for d, la, ln in cur.fetchall():
        if geo.km_entre(float(la), float(ln), float(dla), float(dln)) <= RAIO:
            return d
    return None

fecha_hoje = suprime = 0
casos = []
for i, a in enumerate(cargas):
    if not a['robo'] or a['dla'] is None:
        continue
    # o proximo manifesto da mesma placa (o que a regra de hoje usa para fechar)
    b = None
    for x in cargas[i+1:]:
        if x['dcarg'] <= a['dcarg']:
            continue
        if norm(x['cav']) == norm(a['cav']) or norm(x['c1']) == norm(a['c1']) \
           or norm(x['c1']) == norm(a['cav']) or norm(x['cav']) == norm(a['c1']):
            b = x; break
    if not b:
        continue
    fecha_hoje += 1
    mesmo_destino = norm(b['dcid']) == norm(a['dcid'])
    base_b = b['dcarg'] if isinstance(b['dcarg'], dt) else dt.combine(b['dcarg'], _time())
    em_rota = norm(b['ocid']) != norm(a['ocid'])
    if not (mesmo_destino and em_rota):
        continue
    suprime += 1
    # o juiz: a carga A chegou ao destino DEPOIS da data do manifesto B?
    # as duas placas: a carreta e o sensor, mas varias estao mortas — sem o cavalo
    # a chegada some (foi o que aconteceu com a C-550, cuja carreta HNL0A70 nao fala)
    ch = (chegou_em(a['c1'], a['dla'], a['dln'], base_b, base_b + td(days=10))
          or chegou_em(a['cav'], a['dla'], a['dln'], base_b, base_b + td(days=10)))
    casos.append((a['numero'], b['numero'], a['ocid'], b['ocid'], a['dcid'], str(base_b)[:10],
                  str(ch)[:16] if ch else None))

print('=' * 96)
print('  %d cargas do robo teriam a regra de fechamento disparada por manifesto novo' % fecha_hoje)
print('  %d delas a regra PROPOSTA suprime (mesmo destino + emitido em rota)' % suprime)
print('=' * 96)
acerto = sum(1 for x in casos if x[6])
print('\n  das %d suprimidas, %d chegaram ao destino DEPOIS do manifesto novo — ou seja,' % (len(casos), acerto))
print('  o fechamento de hoje seria prematuro e suprimir e ACERTO.\n')
print('  %-16s %-16s %-16s -> %-16s %-16s %s' % ('CARGA', 'MANIFESTO NOVO', 'ORIGEM A', 'ORIGEM B', 'DESTINO (igual)', 'CHEGADA APOS'))
for x in casos:
    print('  %-16s %-16s %-16s -> %-16s %-16s %s' % (x[0], x[1], (x[2] or '')[:16], (x[3] or '')[:16], (x[4] or '')[:16], x[6] or 'nao chegou'))
conn.close()
