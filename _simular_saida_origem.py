# -*- coding: utf-8 -*-
"""SIMULADOR do item 3 — a saida exige prova de carregamento na origem?
SO LEITURA: nao escreve uma linha no banco.

A regra de hoje, no `_robo_atemporal`:

    t_org = perto_com_parada(d_org, RAIO_ORIGEM, RAIO_METRO)   # SEM exigir parada
    saiu  = primeiro ponto FORA do raio depois de t_org
    saida = ultimo ponto DENTRO do raio antes desse `saiu`

Duas escolhas, e as duas erram quando o caminhao so PASSA pela origem:
  * a origem pergunta apenas "esteve la?" — esta escrito em embarques_regua.py:130, e a
    decisao foi medida em 09/09 ("11 correcoes contra 167 da chegada — nao se conserta o
    que esta funcionando");
  * `saiu` e a PRIMEIRA saida do raio, entao um retorno posterior para carregar nem e olhado.
    Foi o caso da C-2026-001064 (print 3): passou por Resende a 82 km/h rumo a Seropedica,
    voltou no dia seguinte e carregou — a saida ficou 22 h adiantada.

O aferidor ja conta o defeito: classe `C7`, "saida fabricada — a placa passou pela origem
sem parar".

VARIANTES MEDIDAS
    A  exigir PARADA na origem (o mesmo `exigir_parada` que a chegada ja usa)
    B  ancorar no ULTIMO bloco de carregamento (geocoding._bloco_de_carregamento, a funcao
       que o recorte do mapa ja usa) e tomar o FIM desse bloco como a saida

PLAUSIBILIDADE: a saida de uma carga deveria ficar perto do `data_carregamento`. A distancia
em dias entre os dois e a regua que separa correcao de estrago.

    START_WORKER=false DB_NAME=rizza_lab_0923 python -X utf8 _simular_saida_origem.py
"""
import os
import sys
import argparse
import statistics
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
from embarques_regua import (perto_com_parada, RAIO_METRO, RAIO_ORIGEM, JANELA_EVIDENCIA_D,
                             PARADO_KMH)

ap = argparse.ArgumentParser()
ap.add_argument('--desde', default='2026-08-24')
ap.add_argument('--parada-min', type=int, default=60, help='minutos de parada exigidos (B)')
ap.add_argument('--limite-h', type=float, default=48.0, dest='limite_h',
                help='janela maxima entre a saida atual e o fim do bloco (variante C)')
ap.add_argument('--detalhe', action='store_true')
A = ap.parse_args()

cn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                      user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = cn.cursor()
HOJE = datetime.utcnow()

cur.execute("""
    SELECT c.id, c.numero, c.status, c.data_carregamento, c.data_saida_real, c.no_local_desde,
           c.origem_latitude, c.origem_longitude, c.cavalo_placa, c.carreta1_placa,
           COALESCE(c.viagem_vazia,FALSE), c.distancia_planejada_km
      FROM embarques_cargas c
     WHERE c.data_carregamento >= %s AND c.origem_latitude IS NOT NULL
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
    return regua.sem_posicao_falsa(brutos)


def saida_atual(d_org):
    t_org, _ = perto_com_parada(d_org, RAIO_ORIGEM, RAIO_METRO)
    if not t_org:
        return None
    saiu = next((d for d, k, v in d_org if k > RAIO_ORIGEM and d > t_org), None)
    if not saiu:
        return None
    dentro = [d for d, k, v in d_org if k <= RAIO_ORIGEM and t_org <= d < saiu]
    return max(dentro) if dentro else saiu


def saida_A(d_org):
    """Igual, mas a origem passa a EXIGIR PARADA — a mesma regra da chegada."""
    t_org, _ = perto_com_parada(d_org, RAIO_ORIGEM, RAIO_METRO, exigir_parada=True)
    if not t_org:
        return None
    saiu = next((d for d, k, v in d_org if k > RAIO_ORIGEM and d > t_org), None)
    if not saiu:
        return None
    dentro = [d for d, k, v in d_org if k <= RAIO_ORIGEM and t_org <= d < saiu]
    return max(dentro) if dentro else saiu


def saida_B(pts, ola, oln, teto):
    """O ULTIMO bloco de carregamento (a funcao que o recorte do mapa ja usa); a saida e o
    FIM desse bloco — o ultimo instante em que ele ainda estava no patio."""
    if not pts:
        return None
    coords = [(la, ln) for _d, la, ln, _v in pts]
    vels = [v for _d, _la, _ln, v in pts]
    inst = [d for d, _la, _ln, _v in pts]
    bloco = geocoding._bloco_de_carregamento(coords, float(ola), float(oln), RAIO_ORIGEM,
                                             vels, inst, teto, PARADO_KMH, A.parada_min)
    if bloco is None:
        return None
    return inst[bloco[1]]


def dias(a, base):
    return None if a is None else (a - base).total_seconds() / 86400.0


res = {'A': Counter(), 'B': Counter(), 'C': Counter()}
plaus = {'A': [], 'B': [], 'C': []}
corrigidas = []
veloc = []
casos = []

for (cid, num, status, dcarg, dsaida, nolocal, ola, oln, cav, c1, vazia, distplan) in CARGAS:
    if not dcarg:
        continue
    ini = datetime.combine(dcarg, _time()) - timedelta(hours=12)
    fim = min(HOJE, datetime.combine(dcarg, _time()) + timedelta(days=JANELA_EVIDENCIA_D))
    pts = []
    for placa in (c1, cav):
        if placa:
            pts = serie(placa, ini, fim)
            if pts:
                break
    if not pts:
        continue
    d_org = [(d, geocoding.km_entre(la, ln, float(ola), float(oln)), v)
             for d, la, ln, v in pts]
    d_org = [(d, k, v) for d, k, v in d_org if k is not None]
    if not d_org:
        continue

    s_at = saida_atual(d_org)
    s_a = saida_A(d_org)
    s_b = saida_B(pts, ola, oln, nolocal)     # teto = chegada gravada (None se nao houver)
    base = datetime.combine(dcarg, _time())

    # VARIANTE C — ADITIVA E DIRIGIDA: so corrige quando existe BLOCO DE CARREGAMENTO
    # (parada sustentada no raio da origem) que termina DEPOIS da saida calculada. Isso e a
    # assinatura de "o caminhao passou, foi embora, voltou e SO ENTAO carregou": a saida de
    # hoje pegou a passagem. Sem bloco, nada muda — silencio nao e evento.
    s_c = s_at
    _dh = (s_b - s_at).total_seconds() / 3600.0 if (s_b and s_at) else None
    if _dh is not None and 0.5 <= _dh <= A.limite_h:
        s_c = s_b
        corrigidas.append((num, s_at, s_b, (s_b - s_at).total_seconds() / 3600.0))
        if nolocal and distplan and float(distplan) > 0:
            h_at = (nolocal - s_at).total_seconds() / 3600.0
            h_nv = (nolocal - s_b).total_seconds() / 3600.0
            if h_at > 0 and h_nv > 0:
                veloc.append((num, float(distplan) / h_at, float(distplan) / h_nv))
    elif s_b is not None and s_at is None and abs(dias(s_b, base)) <= 3:
        s_c = s_b

    for var, nv in (('A', s_a), ('B', s_b), ('C', s_c)):
        if s_at is None and nv is None:
            res[var]['ambos sem saida'] += 1
        elif s_at is None:
            res[var]['GANHO saida'] += 1
        elif nv is None:
            res[var]['PERDA saida'] += 1
        elif abs((nv - s_at).total_seconds()) < 1800:
            res[var]['igual (<30min)'] += 1
        else:
            res[var]['MUDANCA'] += 1
            plaus[var].append((abs(dias(s_at, base)), abs(dias(nv, base))))
    casos.append((num, status, vazia, s_at, s_a, s_b, base))

print(f'banco {os.getenv("DB_NAME")} · {len(casos)} cargas com serie e origem\n')
for var, tit in (('A', 'VARIANTE A — origem passa a EXIGIR PARADA'),
                 ('B', 'VARIANTE B — ancora no ULTIMO bloco de carregamento'),
                 ('C', 'VARIANTE C — ADITIVA: so corrige quando ha bloco DEPOIS da saida')):
    print(tit)
    for k, v in sorted(res[var].items(), key=lambda x: (-x[1], x[0])):
        print(f'   {v:>4}  {k}')
    if plaus[var]:
        a_ = [x[0] for x in plaus[var]]; b_ = [x[1] for x in plaus[var]]
        m = sum(1 for x, y in plaus[var] if y < x)
        print(f'   plausibilidade (|dias| do carregamento): atual mediana {statistics.median(a_):.2f} '
              f'max {max(a_):.1f} · novo mediana {statistics.median(b_):.2f} max {max(b_):.1f}')
        print(f'   {m} de {len(plaus[var])} mudancas ficam MAIS PERTO do carregamento')
    print()

if veloc:
    print('VELOCIDADE MEDIA IMPLICITA (rota planejada / duracao saida->chegada):')
    print(f'{"carga":<16}{"atual":>10}{"corrigido":>12}')
    ok = 0
    for (num, va, vn) in sorted(veloc, key=lambda x: x[1]):
        alvo = 55.0
        if abs(vn - alvo) < abs(va - alvo):
            ok += 1
        print(f'{num:<16}{va:>9.1f}{vn:>11.1f}')
    print(f'   {ok} de {len(veloc)} ficam mais perto de 55 km/h (media de caminhao com paradas)')
    print()

print('CORRIGIDAS pela variante C:')
for (num, at, nv, h) in sorted(corrigidas, key=lambda x: -x[3]):
    print(f'   {num}  {at:%d/%m %H:%M} -> {nv:%d/%m %H:%M}  (+{h:.1f} h)')
print()

if A.detalhe:
    print(f'{"carga":<16}{"vazia":<7}{"atual":<18}{"A (parada)":<18}{"B (bloco)":<18}')
    for (num, st, vazia, s_at, s_a, s_b, base) in casos:
        f = lambda d: (d.strftime('%d/%m %H:%M') if d else '—')
        if not (f(s_at) == f(s_a) == f(s_b)):
            print(f'{num:<16}{"sim" if vazia else "nao":<7}{f(s_at):<18}{f(s_a):<18}{f(s_b):<18}')

cn.rollback()
cn.close()
