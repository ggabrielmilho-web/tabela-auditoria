# -*- coding: utf-8 -*-
"""KM PLANEJADO x REALIZADO — o ponto do CD melhora a rota? (só leitura, chama o ORS)

Pergunta do Gabriel (21/09/2026): o endereço não serve para roteirizar? A âncora de CHEGADA já
foi medida e não muda decisão (§27.13 nº 4). Aqui é o outro uso: a ROTA PLANEJADA — a linha do
mapa e o `distancia_planejada_km`, que alimenta ETA, `KM FALTANDO` e o `T5` do aferidor.

Para cada carga entregue com KPI consolidado, três números:

    real   km que o GPS mediu entre sair da origem e chegar ao destino
           (`embarques_cargas_rastreio_kpi.distancia_metros`, o mesmo que a tela mostra)
    A      km planejado de HOJE: ORS centroide -> centroide (`distancia_planejada_km`)
    B      km planejado se a rota fosse traçada entre os pontos REAIS da viagem: onde o
           caminhão parou para carregar e onde parou para descarregar (parada sustentada,
           mesma régua do `_pontos_provados_local.py`), calculado agora no ORS

A pergunta é pareada, carga a carga: |B - real| é menor que |A - real|? E em quanto?

O que este número NÃO diz: o realizado tem desvio, abastecimento, volta errada — nenhuma rota
planejada bate com ele. O que interessa é se o erro CAI ao trocar o ponto, não se zera.

Custo: 1 chamada ORS por carga (cota de 2.000/dia; o robô usa ~30). `--n` limita.

    DB_NAME=rizza_lab_0921 python -X utf8 _medir_km_planejado.py --n 80
"""
import os
import sys
import time
import argparse
import statistics
from datetime import datetime, timedelta, time as _time

_AQUI = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
sys.path.insert(0, _AQUI)
os.chdir(_AQUI)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import psycopg2
from dotenv import load_dotenv
load_dotenv('.env')
import geocoding
import placas as pl
import ors_client
from embarques_regua import PARADO_KMH

ap = argparse.ArgumentParser()
ap.add_argument('--n', type=int, default=80, help='cargas (as mais recentes primeiro)')
ap.add_argument('--dias', type=int, default=45)
ap.add_argument('--raio', type=float, default=60.0)
ap.add_argument('--min-h', type=float, default=0.5)
ap.add_argument('--pausa', type=float, default=1.6, help='ORS free: 40/min')
A = ap.parse_args()

cn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                      user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = cn.cursor()
print(f'banco: {os.getenv("DB_NAME")}')

cur.execute("""
    SELECT c.id, c.numero, c.data_carregamento, c.data_saida_real, c.no_local_desde, c.data_conclusao,
           c.origem_cidade, c.origem_latitude, c.origem_longitude,
           c.cavalo_placa, c.carreta1_placa, c.distancia_planejada_km,
           d.cidade, d.uf, d.latitude, d.longitude, k.distancia_metros / 1000.0
      FROM embarques_cargas c
      JOIN embarques_cargas_rastreio_kpi k ON k.carga_id = c.id AND k.consolidado_final
      JOIN embarques_cargas_destinos d ON d.carga_id = c.id
     WHERE c.status = 'Entregue' AND COALESCE(c.viagem_vazia, FALSE) = FALSE
       AND c.distancia_planejada_km IS NOT NULL AND k.distancia_metros > 0
       AND c.origem_latitude IS NOT NULL AND d.latitude IS NOT NULL
       AND c.data_saida_real IS NOT NULL
       AND c.data_carregamento >= CURRENT_DATE - %s
       AND (SELECT count(*) FROM embarques_cargas_destinos x WHERE x.carga_id = c.id) = 1
     ORDER BY c.data_carregamento DESC
""", (A.dias,))
CARGAS = cur.fetchall()[:A.n]
print(f'{len(CARGAS)} cargas entregues, 1 destino, KPI consolidado, últimos {A.dias} d\n')


def pontos(placa, ini, fim):
    if not placa:
        return []
    cur.execute("""SELECT data_posicao, latitude, longitude, velocidade FROM embarques_posicoes_historico
                    WHERE placa = ANY(%s) AND data_posicao >= %s AND data_posicao < %s ORDER BY data_posicao""",
                (pl.grafias(str(placa).strip().upper()), ini, fim))
    return [(d, float(la), float(ln), v) for d, la, ln, v in cur.fetchall()]


def parada(pts, lat, lng):
    perto = [p for p in pts if (geocoding.km_entre(p[1], p[2], lat, lng) or 9e9) <= A.raio
             and (p[3] is None or float(p[3]) <= PARADO_KMH)]
    if not perto:
        return None
    blocos, atual = [], [perto[0]]
    for p in perto[1:]:
        if (geocoding.km_entre(p[1], p[2], atual[0][1], atual[0][2]) or 9e9) <= 3.0:
            atual.append(p)
        else:
            blocos.append(atual)
            atual = [p]
    blocos.append(atual)
    m = max(blocos, key=lambda b: (b[-1][0] - b[0][0]).total_seconds())
    if (m[-1][0] - m[0][0]).total_seconds() / 3600 < A.min_h:
        return None
    return statistics.median(p[1] for p in m), statistics.median(p[2] for p in m)


res = []
pulos = {}
for (cid, num, dcarg, dsaida, nolocal, dconc, ocid, ola, oln, cav, c1, plan, dcid, duf, dla, dln, real) in CARGAS:
    base = datetime.combine(dcarg, _time()) - timedelta(hours=12)
    fim = dconc or nolocal
    pts = pontos(c1, base, fim) or pontos(cav, base, fim)
    if not pts:
        pulos['sem GPS'] = pulos.get('sem GPS', 0) + 1
        continue
    po = parada([p for p in pts if p[0] <= dsaida], float(ola), float(oln))
    pd = parada([p for p in pts if p[0] >= dsaida], float(dla), float(dln))
    if not po or not pd:
        k = 'sem parada na origem' if not po else 'sem parada no destino'
        pulos[k] = pulos.get(k, 0) + 1
        continue
    try:
        rota = ors_client.tracar_rota({'lat': po[0], 'lng': po[1]}, {'lat': pd[0], 'lng': pd[1]})
        b = float(rota['distancia_km'])
    except Exception as e:
        pulos['ORS falhou'] = pulos.get('ORS falhou', 0) + 1
        print(f'   {num}: ORS falhou — {str(e)[:60]}')
        time.sleep(A.pausa)
        continue
    time.sleep(A.pausa)
    off_o = geocoding.km_entre(po[0], po[1], float(ola), float(oln))
    off_d = geocoding.km_entre(pd[0], pd[1], float(dla), float(dln))
    res.append((num, ocid, dcid, float(real), float(plan), b, off_o, off_d))
    print(f'{num}  {ocid[:14]:<14} -> {dcid[:14]:<14}  real {float(real):>7.1f}  A {float(plan):>7.1f} ({float(plan)-float(real):+6.1f})'
          f'  B {b:>7.1f} ({b-float(real):+6.1f})   centroide errou {off_o:.0f} / {off_d:.0f} km')

print(f'\npuladas: {pulos}')
if not res:
    sys.exit(0)

ea = [abs(a - r) for _n, _o, _d, r, a, _b, _oo, _od in res]
eb = [abs(b - r) for _n, _o, _d, r, _a, b, _oo, _od in res]
pa = [abs(a - r) / r * 100 for _n, _o, _d, r, a, _b, _oo, _od in res]
pb = [abs(b - r) / r * 100 for _n, _o, _d, r, _a, b, _oo, _od in res]
melhor = sum(1 for x, y in zip(ea, eb) if y < x - 0.5)
pior = sum(1 for x, y in zip(ea, eb) if y > x + 0.5)
ganho = sorted(x - y for x, y in zip(ea, eb))
print(f'\n--- {len(res)} cargas, erro |planejado - real| ---')
print(f'{"":<28} {"mediana":>9} {"média":>9} {"p90":>9}   {"≤5%":>5} {"≤10%":>5}')
for nome, e, p in (('A · centroide (hoje)', ea, pa), ('B · pontos reais da viagem', eb, pb)):
    e2, p2 = sorted(e), sorted(p)
    print(f'{nome:<28} {e2[len(e2)//2]:>7.1f} km {statistics.mean(e2):>7.1f} km {e2[int(len(e2)*0.9)]:>7.1f} km'
          f'   {sum(1 for x in p2 if x <= 5):>5} {sum(1 for x in p2 if x <= 10):>5}')
print(f'\npareado: B melhor em {melhor} · pior em {pior} · empate (±0,5 km) em {len(res) - melhor - pior}')
print(f'ganho por carga (A - B, km): mediana {ganho[len(ganho)//2]:+.1f} · p10 {ganho[int(len(ganho)*0.1)]:+.1f} · p90 {ganho[int(len(ganho)*0.9)]:+.1f}')
offs = sorted(o + d for _n, _o, _d, _r, _a, _b, o, d in res)
print(f'desvio do centroide (origem + destino): mediana {offs[len(offs)//2]:.1f} km · p90 {offs[int(len(offs)*0.9)]:.1f} km')
cn.close()
