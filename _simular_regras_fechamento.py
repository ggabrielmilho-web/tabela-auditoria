# -*- coding: utf-8 -*-
"""Simula o FECHAMENTO do robo -- as regras de hoje e as propostas -- dia a dia.

O robo tem dois mecanismos que encerram carga:

  1. `manifesto_novo`  -- HOJE: qualquer placa do manifesto novo (cavalo OU carreta) fecha
                          qualquer carga aberta que tenha essa placa em qualquer papel.
                          PROPOSTA: so a CARRETA do manifesto novo encerra a carga da MESMA
                          carreta; o cavalo com outra carreta e troca de recurso, nao fim de
                          viagem (secao 4.1: uma carreta carregada nao fica em dois lugares,
                          um cavalo fica). Manifesto sem carreta (truck/toco) cai no cavalo.

  2. `dedup_veiculo`   -- HOJE: garante UMA carga ativa por cavalo E por carreta, fechando
                          todas menos a ultima, por ORDEM (data, CTRB, id) e nao por
                          evidencia. E o motivo `sequencia_viagem`.
                          PROPOSTA: dedup so por CARRETA.

Nos dois casos vale a excecao ja implementada em producao: manifesto novo com o MESMO
destino emitido EM ROTA e reforco de carga no hub, nao viagem nova.

DUAS REGUAS, porque medem coisas diferentes:

  * veredito pelo GPS -- no instante do fechamento a carreta ja tinha entrado no raio de
    20 km do destino? (acerto / ERRO se chegou depois / sem prova se nunca chegou).
    Tolerancia de 12 h por padrao: o manifesto nao tem hora, entao a emissao vira 00:00 e
    chegar de manha no mesmo dia nao e fechamento prematuro, e granularidade.

  * distancia no fechamento -- a mesma regua da secao 3.1 do handoff (% fechada a mais de
    100 km do destino). Mede o estrago que aparece na tela, independente de quando chegou.

    python -X utf8 _simular_regras_fechamento.py
    SIM_TOLERANCIA_H=24 python -X utf8 _simular_regras_fechamento.py --desde 2026-09-01
"""
import os
import sys
import argparse
import unicodedata
from collections import defaultdict
from datetime import datetime as dt, timedelta as td, time as _time

# A pasta do proprio arquivo, nao um caminho cravado: estes scripts precisam rodar
# TAMBEM dentro do container (Linux), que e de onde o robo atemporal corrige os dados
# de producao. O `c:/Phyton-Projetos/...` que estava aqui quebrava com FileNotFoundError.
_AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _AQUI)
os.chdir(_AQUI)
import psycopg2
from dotenv import load_dotenv

load_dotenv('.env')
import geocoding as geo
import placas as pl

RAIO = 20.0
TOLERANCIA_H = float(os.getenv('SIM_TOLERANCIA_H', '12'))
LONGE_KM = 100.0

ap = argparse.ArgumentParser()
ap.add_argument('--desde', default='2026-08-01')
ap.add_argument('--ate', default='2026-09-30')
a = ap.parse_args()

conn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                        user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = conn.cursor()


def norm(s):
    t = unicodedata.normalize('NFKD', str(s or ''))
    return ''.join(c for c in t if not unicodedata.combining(c)).upper().replace('-', '').replace(' ', '')


def mesma(p1, p2):
    if not p1 or not p2:
        return False
    return pl.mercosul(str(p1).strip().upper()) == pl.mercosul(str(p2).strip().upper())


cur.execute("""SELECT e.id, e.numero, e.cavalo_placa, e.carreta1_placa, e.carreta2_placa,
                      e.origem_cidade, e.data_carregamento, d.cidade, d.latitude, d.longitude
                 FROM embarques_cargas e
                 LEFT JOIN embarques_cargas_destinos d ON d.carga_id = e.id
                      AND d.ordem = (SELECT MIN(ordem) FROM embarques_cargas_destinos x WHERE x.carga_id = e.id)
                WHERE e.data_carregamento BETWEEN %s AND %s
                  AND COALESCE(e.viagem_vazia, FALSE) = FALSE
                  AND COALESCE(e.criada_por_robo, FALSE) = TRUE
                ORDER BY e.data_carregamento, e.id""", (a.desde, a.ate))
cols = ['id', 'numero', 'cav', 'c1', 'c2', 'ocid', 'dcarg', 'dcid', 'dla', 'dln']
cargas = [dict(zip(cols, r)) for r in cur.fetchall()]
por_dia = defaultdict(list)
for c in cargas:
    por_dia[c['dcarg']].append(c)
dias = sorted(por_dia)
print('%d cargas do robo entre %s e %s, em %d dias\n' % (len(cargas), a.desde, a.ate, len(dias)))

_cheg, _pos = {}, {}


def chegou_ate(carga, limite):
    """Instante da 1a entrada no raio do destino ate `limite` (None se nao entrou)."""
    placa = carga['c1'] or carga['cav']
    if not placa or carga['dla'] is None:
        return None
    k = (carga['id'], limite)
    if k in _cheg:
        return _cheg[k]
    base = carga['dcarg'] if isinstance(carga['dcarg'], dt) else dt.combine(carga['dcarg'], _time())
    cur.execute("""SELECT data_posicao, latitude, longitude FROM embarques_posicoes_historico
                    WHERE placa = ANY(%s) AND data_posicao BETWEEN %s AND %s ORDER BY data_posicao""",
                (pl.grafias(placa), base - td(hours=12), limite))
    achou = None
    for d, la, ln in cur.fetchall():
        if geo.km_entre(float(la), float(ln), float(carga['dla']), float(carga['dln'])) <= RAIO:
            achou = d
            break
    _cheg[k] = achou
    return achou


def distancia_no_fechamento(carga, quando):
    """Km entre a ultima posicao conhecida ATE `quando` e o destino (None sem posicao)."""
    placa = carga['c1'] or carga['cav']
    if not placa or carga['dla'] is None:
        return None
    k = (carga['id'], quando)
    if k in _pos:
        return _pos[k]
    cur.execute("""SELECT latitude, longitude FROM embarques_posicoes_historico
                    WHERE placa = ANY(%s) AND data_posicao <= %s
                    ORDER BY data_posicao DESC LIMIT 1""", (pl.grafias(placa), quando))
    r = cur.fetchone()
    d = geo.km_entre(float(r[0]), float(r[1]), float(carga['dla']), float(carga['dln'])) if r else None
    _pos[k] = d
    return d


def julga(carga, quando):
    if carga['dla'] is None:
        return 'sem prova'
    if chegou_ate(carga, quando + td(hours=TOLERANCIA_H)):
        return 'acerto'
    if chegou_ate(carga, quando + td(days=10)):
        return 'erro'
    return 'sem prova'


def reforco_no_meio_da_rota(B, A):
    return norm(B['dcid']) == norm(A['dcid']) and norm(B['ocid']) != norm(A['ocid'])


def simular(so_mesma_carreta, dedup_com_prova=False):
    """Roda dia a dia: primeiro os manifestos do dia, depois o dedup -- a ordem do robo.

    `dedup_com_prova`: o dedup so encerra a carga anterior quando o GPS mostra que a
    carreta dela JA CHEGOU ao destino. Sem prova ela fica aberta (vira pendencia), em vez
    de ser encerrada por ordem de chegada do documento.
    """
    abertas = []
    fechamentos = []

    def fecha(A, quando, motivo):
        fechamentos.append({'carga': A, 'quando': quando, 'motivo': motivo,
                            'veredito': julga(A, quando),
                            'dist': distancia_no_fechamento(A, quando)})

    for dia in dias:
        novas = por_dia[dia]
        quando = dia if isinstance(dia, dt) else dt.combine(dia, _time())

        # 1) manifesto novo encerra carga anterior
        for B in novas:
            restam = []
            for A in abertas:
                if A['dcarg'] >= B['dcarg']:
                    restam.append(A)
                    continue
                if so_mesma_carreta:
                    casa = (mesma(B['c1'], A['c1']) or mesma(B['c1'], A['c2'])) if B['c1'] \
                        else mesma(B['cav'], A['cav'])
                else:
                    casa = any(mesma(p, q) for p in (B['cav'], B['c1'])
                               for q in (A['cav'], A['c1'], A['c2']))
                if not casa or reforco_no_meio_da_rota(B, A):
                    restam.append(A)
                    continue
                fecha(A, quando, 'manifesto_novo')
            abertas = restam
        abertas.extend(novas)

        # 2) dedup: uma carga ativa por placa (hoje cavalo E carreta; proposta so carreta)
        campos = ('c1',) if so_mesma_carreta else ('cav', 'c1')
        globals()['_abertas_no_fim'] = abertas
        for campo in campos:
            grupos = defaultdict(list)
            for A in abertas:
                if A[campo]:
                    grupos[pl.mercosul(str(A[campo]).strip().upper())].append(A)
            for placa, itens in grupos.items():
                if len(itens) < 2:
                    continue
                itens.sort(key=lambda x: (x['dcarg'], x['id']))
                for A in itens[:-1]:
                    if A not in abertas:
                        continue
                    if dedup_com_prova and not chegou_ate(A, quando + td(hours=TOLERANCIA_H)):
                        continue          # sem prova de chegada: fica aberta, vira pendencia
                    fecha(A, quando, 'sequencia_viagem')
                    abertas.remove(A)
    return fechamentos


def placar(nome, f):
    tot = len(f)
    ver = defaultdict(int)
    for x in f:
        ver[x['veredito']] += 1
    longe = [x for x in f if x['dist'] is not None and x['dist'] > LONGE_KM]
    com_pos = [x for x in f if x['dist'] is not None]
    print('%-30s %6d %7d %6d %8d %10s' % (
        nome, tot, ver['acerto'], ver['erro'], ver['sem prova'],
        ('%d (%.0f%%)' % (len(longe), 100.0 * len(longe) / len(com_pos))) if com_pos else '-'))
    return ver, longe


print('%-30s %6s %7s %6s %8s %10s' % ('REGRA', 'fecha', 'acerto', 'ERRO', 's/prova', '>100km'))
print('-' * 76)
f_hoje = simular(False)
f_prop = simular(True)
f_mais = simular(True, dedup_com_prova=True)
v_hoje, l_hoje = placar('hoje', f_hoje)
v_prop, l_prop = placar('proposta (eixo na carreta)', f_prop)
v_mais, l_mais = placar('proposta + dedup so com prova', f_mais)

print('\npor motivo:')
for nome, f in (('hoje', f_hoje), ('proposta', f_prop), ('prop+', f_mais)):
    por_motivo = defaultdict(lambda: [0, 0, 0])
    for x in f:
        por_motivo[x['motivo']][0] += 1
        if x['veredito'] == 'erro':
            por_motivo[x['motivo']][1] += 1
        if x['dist'] is not None and x['dist'] > LONGE_KM:
            por_motivo[x['motivo']][2] += 1
    for m, (n, e, lg) in sorted(por_motivo.items()):
        print('   %-9s %-18s %4d fechamentos · %2d erros · %2d a mais de 100 km' % (nome, m, n, e, lg))

# ── Item 3: a JANELA DE REANALISE (o robo atemporal, secao 13.4) ─────────────────────────
# Com as regras propostas o robo fecha menos -- de proposito. A pergunta e o que acontece
# com o que sobra: quantas dessas pendencias o mundo responde depois, e em quanto tempo?
# Duas evidencias, as duas de graca (so leem o banco): o GPS mostrando a carreta entrando no
# destino, ou um manifesto novo da MESMA carreta.
pendentes = list(globals().get('_abertas_no_fim') or [])
sem_prova = [x['carga'] for x in f_mais if x['veredito'] == 'sem prova']
revisitar = {c['id']: c for c in pendentes + sem_prova}.values()
print('\n\nITEM 3 -- janela de reanalise sobre %d pendencias '
      '(%d nunca fechadas + %d fechadas sem prova):' % (len(revisitar), len(pendentes), len(sem_prova)))

por_gps = por_doc = nada = 0
atrasos = []
for A in revisitar:
    base = A['dcarg'] if isinstance(A['dcarg'], dt) else dt.combine(A['dcarg'], _time())
    teto = base + td(days=30)
    ch = chegou_ate(A, teto)
    if ch:
        por_gps += 1
        atrasos.append((ch - base).total_seconds() / 86400.0)
        continue
    # manifesto novo da MESMA carreta depois desta carga
    doc = None
    for B in cargas:
        if B['id'] == A['id'] or B['dcarg'] <= A['dcarg']:
            continue
        if mesma(B['c1'], A['c1']) or mesma(B['c1'], A['c2']):
            b = B['dcarg'] if isinstance(B['dcarg'], dt) else dt.combine(B['dcarg'], _time())
            if b <= teto:
                doc = b
                break
    if doc:
        por_doc += 1
        atrasos.append((doc - base).total_seconds() / 86400.0)
    else:
        nada += 1
tot_rev = len(list(revisitar))
if tot_rev:
    resolvidas = por_gps + por_doc
    print('   resolvem em ate 30 dias ......... %3d (%.0f%%)' % (resolvidas, 100.0 * resolvidas / tot_rev))
    print('      pelo GPS (chegou ao destino) .. %3d' % por_gps)
    print('      pelo documento (manifesto novo da mesma carreta) %3d' % por_doc)
    print('   sobram para o humano ............ %3d' % nada)
    if atrasos:
        atrasos.sort()
        print('   atraso da evidencia: p50 %.1f dias · p90 %.1f dias · max %.1f dias'
              % (atrasos[len(atrasos) // 2], atrasos[int(len(atrasos) * 0.9)], atrasos[-1]))

print('\nos fechamentos que a proposta EVITA (>100 km do destino):')
evita = {x['carga']['numero'] for x in l_hoje} - {x['carga']['numero'] for x in l_prop}
for x in sorted([y for y in l_hoje if y['carga']['numero'] in evita], key=lambda y: -y['dist'])[:12]:
    print('   %-16s %-16s a %6.0f km do destino (%s)' % (
        x['carga']['numero'], x['motivo'], x['dist'], x['veredito']))
conn.close()
