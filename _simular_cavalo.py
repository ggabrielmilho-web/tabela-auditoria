# -*- coding: utf-8 -*-
"""SIMULADOR do item 2 — e se o CAVALO fosse o sensor quando a carreta mede pior?
SO LEITURA: nao escreve uma linha no banco.

O motor ja cai no cavalo, mas so quando a carreta tem ZERO ponto:

    for placa, papel in ((c1,'carreta1'), (c2,'carreta2'), (cav,'cavalo')):
        s = serie(placa, ini, fim)
        if s:            # <- qualquer serie nao-vazia vence
            pts, sensor = s, ...; break

Uma carreta com 1 ponto ganha de um cavalo com 900. O aferidor ja mede essa populacao e lhe
deu nome — `S3`, "cavalo mede melhor que a carreta em uso", com o limiar `len(cavalo) >
3 * len(carreta)`. Este simulador usa ESSE limiar (uma regua so) e responde, carga a carga:
o que a saida e a chegada seriam se o cavalo medisse.

A CAUTELA que justifica o modelo carreta-cêntrico continua valendo: o cavalo pode estar
puxando OUTRA carreta. Por isso o simulador mede duas variantes:

    A  trocar o sensor sem nenhuma guarda
    B  trocar so quando o cavalo ESTEVE na origem desta carga (prova de que estavam juntos)

e classifica cada carga em GANHO (o motor nao tinha o instante e passaria a ter),
MUDANCA (tinha e mudaria) ou PERDA (tinha e deixaria de ter).

    START_WORKER=false DB_NAME=rizza_lab_0923 python -X utf8 _simular_cavalo.py
    ... --desde 2026-08-24 --detalhe
"""
import os
import sys
import argparse
from collections import Counter
from datetime import datetime, timedelta, time as _time

_AQUI = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
sys.path.insert(0, _AQUI)
os.chdir(_AQUI)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv('.env')
import psycopg2
import geocoding
import placas as pl
import embarques_regua as regua
from embarques_regua import (perto_com_parada, RAIO_METRO, RAIO_ORIGEM, JANELA_EVIDENCIA_D)

ap = argparse.ArgumentParser()
ap.add_argument('--desde', default='2026-08-24')
ap.add_argument('--fator', type=float, default=3.0, help='cavalo > fator x carreta (S3 usa 3)')
ap.add_argument('--max-car', type=int, default=0, dest='max_car',
                help='so emprestar quando a carreta tiver ATE N pontos (0 = sem limite)')
ap.add_argument('--detalhe', action='store_true')
A = ap.parse_args()

cn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                      user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = cn.cursor()
HOJE = datetime.utcnow()

cur.execute("""
    SELECT c.id, c.numero, c.status, c.data_carregamento, c.data_saida_real, c.no_local_desde,
           c.origem_latitude, c.origem_longitude, c.cavalo_placa, c.carreta1_placa,
           COALESCE(c.viagem_vazia,FALSE), d.latitude, d.longitude
      FROM embarques_cargas c
      LEFT JOIN embarques_cargas_destinos d ON d.carga_id = c.id AND d.ordem = 1
     WHERE c.data_carregamento >= %s
     ORDER BY c.data_carregamento
""", (A.desde,))
CARGAS = cur.fetchall()


def serie(placa, ini, fim):
    if not placa:
        return []
    cur.execute("""SELECT data_posicao,latitude,longitude,velocidade,odometer
                     FROM embarques_posicoes_historico
                    WHERE placa=ANY(%s) AND data_posicao>=%s AND data_posicao<%s
                    ORDER BY data_posicao""",
                (pl.grafias(str(placa).strip().upper()), ini, fim))
    brutos = [(d, float(la), float(ln), v, o) for d, la, ln, v, o in cur.fetchall()
              if la is not None and ln is not None]
    return regua.sem_posicao_falsa(brutos)      # §23.3, a mesma regua do motor


def distancias(pts, lat, lng):
    if lat is None:
        return []
    out = [(d, geocoding.km_entre(la, ln, float(lat), float(lng)), v) for d, la, ln, v in pts]
    return [(d, k, v) for d, k, v in out if k is not None]


def decidir(pts, ola, oln, dla, dln, dsaida):
    """A MESMA sequencia do `_robo_atemporal`: saida = ultimo ponto no raio da origem antes
    da primeira saida dele; chegada = regua estrita, com piso na saida."""
    d_org = distancias(pts, ola, oln)
    d_dst = distancias(pts, dla, dln)
    n_saida = t_org = None
    if d_org:
        t_org, _ = perto_com_parada(d_org, RAIO_ORIGEM, RAIO_METRO)
        if t_org:
            saiu = next((d for d, k, v in d_org if k > RAIO_ORIGEM and d > t_org), None)
            if saiu:
                dentro = [d for d, k, v in d_org if k <= RAIO_ORIGEM and t_org <= d < saiu]
                n_saida = max(dentro) if dentro else saiu
    pisos = [x for x in (n_saida, dsaida) if x is not None]
    piso = max(pisos) if pisos else t_org
    validos = [(d, k, v) for d, k, v in d_dst if piso is None or d >= piso]
    n_cheg, como = regua.chegada(validos)
    return n_saida, n_cheg, como


def delta(a, b):
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 3600.0


res = {'A': Counter(), 'B': Counter(), 'E': Counter()}
direcao = Counter()
perdas = []
mudancas = []
plaus = []
plausE = []
linhas = []
n_s3 = 0

for (cid, num, status, dcarg, dsaida, nolocal, ola, oln, cav, c1, vazia, dla, dln) in CARGAS:
    if not dcarg:
        continue
    ini = datetime.combine(dcarg, _time()) - timedelta(hours=12)
    fim = min(HOJE, datetime.combine(dcarg, _time()) + timedelta(days=JANELA_EVIDENCIA_D))

    p_car = serie(c1, ini, fim)
    p_cav = serie(cav, ini, fim)
    if not p_car and not p_cav:
        continue
    # sensor de HOJE: a primeira serie nao vazia, carreta primeiro
    atual = p_car if p_car else p_cav
    papel_atual = 'carreta' if p_car else 'cavalo'
    if papel_atual != 'carreta' or not p_cav:
        continue                     # ja usa o cavalo, ou nao ha cavalo: nada a simular
    if len(p_cav) <= A.fator * max(1, len(p_car)):
        continue                     # fora do criterio S3
    if A.max_car and len(p_car) > A.max_car:
        continue                 # carreta com dado de sobra: nao ha o que emprestar
    n_s3 += 1

    s_at, c_at, _ = decidir(atual, ola, oln, dla, dln, dsaida)
    s_nv, c_nv, como_nv = decidir(p_cav, ola, oln, dla, dln, dsaida)

    # guarda B: o cavalo esteve na origem desta carga?
    d_org_cav = distancias(p_cav, ola, oln)
    cav_na_origem = bool(d_org_cav) and min(k for _, k, _v in d_org_cav) <= RAIO_ORIGEM

    for var, aplica in (('A', True), ('B', cav_na_origem)):
        if not aplica:
            res[var]['nao trocaria (guarda barrou)'] += 1
            continue
        for campo, at, nv in (('saida', s_at, s_nv), ('chegada', c_at, c_nv)):
            if at is None and nv is not None:
                res[var][f'GANHO {campo}'] += 1
            elif at is not None and nv is None:
                res[var][f'PERDA {campo}'] += 1
                if var == 'B':
                    perdas.append((num, campo, at, len(p_car), len(p_cav)))
            elif at is not None and nv is not None:
                h = abs(delta(at, nv) or 0)
                if h < 0.5:
                    res[var][f'igual {campo} (<30min)'] += 1
                else:
                    res[var][f'MUDANCA {campo}'] += 1
                    if var == 'B':
                        d = delta(at, nv)
                        direcao[f'{campo}: ' + ('antecipa' if d < 0 else 'atrasa')] += 1
                        mudancas.append((num, campo, at, nv, d, len(p_car), len(p_cav)))
                        if campo == 'saida':
                            base = datetime.combine(dcarg, _time())
                            plaus.append(((at - base).total_seconds()/86400,
                                          (nv - base).total_seconds()/86400))

    # ── VARIANTE E — ADITIVA: a mesma filosofia do `chegada_emprestada` (§26.10).
    # Emprestar nunca APAGA o que a carreta deu, e so corrige quando o instante do cavalo e
    # ANTERIOR: o frame congelado sempre ATRASA o evento (a placa acorda tarde e longe), nunca
    # o adianta. Isso torna PERDA impossivel por construcao e derruba as mudancas na direcao
    # errada, que sao as unicas suspeitas.
    if cav_na_origem:
        for campo, at, nv in (('saida', s_at, s_nv), ('chegada', c_at, c_nv)):
            if at is None and nv is not None:
                res['E'][f'GANHO {campo}'] += 1
            elif at is not None and nv is not None and nv < at and abs(delta(at, nv)) >= 0.5:
                res['E'][f'CORRIGE {campo} (antecipa)'] += 1
                if campo == 'saida':
                    base = datetime.combine(dcarg, _time())
                    plausE.append(((at - base).total_seconds()/86400,
                                   (nv - base).total_seconds()/86400))
            elif at is not None:
                res['E'][f'mantem {campo} (carreta manda)'] += 1
    else:
        res['E']['nao trocaria (guarda barrou)'] += 1

    linhas.append((num, status, vazia, len(p_car), len(p_cav), cav_na_origem,
                   s_at, s_nv, c_at, c_nv, como_nv))

print(f'banco {os.getenv("DB_NAME")} · {len(CARGAS)} cargas desde {A.desde} · '
      f'{n_s3} no criterio S3 (cavalo > {A.fator:.0f}x carreta)\n')
for var, titulo in (('A', 'VARIANTE A — trocar sem guarda'),
                    ('B', 'VARIANTE B — trocar so com o cavalo provado na origem'),
                    ('E', 'VARIANTE E — ADITIVA: so preenche vazio e so antecipa')):
    print(titulo)
    for k, v in sorted(res[var].items(), key=lambda x: (-x[1], x[0])):
        print(f'   {v:>4}  {k}')
    print()

if plausE:
    import statistics as _s2
    a_ = [abs(x[0]) for x in plausE]; b_ = [abs(x[1]) for x in plausE]
    print('PLAUSIBILIDADE da saida na VARIANTE E (|dias| do carregamento):')
    print(f'   atual : mediana {_s2.median(a_):.1f} d · max {max(a_):.1f} d')
    print(f'   cavalo: mediana {_s2.median(b_):.1f} d · max {max(b_):.1f} d')
    print(f'   {sum(1 for x, y in plausE if abs(y) < abs(x))} de {len(plausE)} melhoram')
    print()

if plaus:
    import statistics as _s
    a_ = [abs(x[0]) for x in plaus]; b_ = [abs(x[1]) for x in plaus]
    melhor = sum(1 for x, y in plaus if abs(y) < abs(x))
    print(f'PLAUSIBILIDADE da saida (distancia ao carregamento, |dias|):')
    print(f'   atual : mediana {_s.median(a_):.1f} d · max {max(a_):.1f} d')
    print(f'   cavalo: mediana {_s.median(b_):.1f} d · max {max(b_):.1f} d')
    print(f'   {melhor} de {len(plaus)} ficam MAIS PERTO do carregamento com o cavalo')
    print()

print('DIRECAO das mudancas (variante B):')
for k, v in sorted(direcao.items()):
    print(f'   {v:>4}  {k}')
import statistics as _st
for campo in ('saida', 'chegada'):
    ds = [abs(m[4]) for m in mudancas if m[1] == campo]
    if ds:
        print(f'   {campo}: mediana {_st.median(ds):.1f} h · max {max(ds):.1f} h')
print()
print('PERDAS na variante B (o motor tinha o instante e deixaria de ter):')
for (num, campo, at, ncar, ncav) in perdas:
    print(f'   {num}  {campo}  atual={at}  pts carreta={ncar} cavalo={ncav}')
print()
print('MUDANCAS de SAIDA acima de 3 h (variante B):')
for (num, campo, at, nv, d, ncar, ncav) in sorted(mudancas, key=lambda m: -abs(m[4]))[:12]:
    if campo == 'saida':
        print(f'   {num}  {at} -> {nv}  ({d:+.1f} h)  pts carreta={ncar} cavalo={ncav}')
print()

if A.detalhe:
    print(f'{"carga":<16}{"st":<12}{"pts_car":>8}{"pts_cav":>8}{"cav_org":>8}  '
          f'{"saida atual":<20}{"saida cavalo":<20}{"cheg atual":<20}{"cheg cavalo":<20}')
    for (num, st, vazia, ncar, ncav, cavorg, s_at, s_nv, c_at, c_nv, como) in linhas:
        f = lambda d: (d.strftime('%d/%m %H:%M') if d else '—')
        print(f'{num:<16}{st[:11]:<12}{ncar:>8}{ncav:>8}{"sim" if cavorg else "nao":>8}  '
              f'{f(s_at):<20}{f(s_nv):<20}{f(c_at):<20}{f(c_nv):<20}')

cn.rollback()
cn.close()
