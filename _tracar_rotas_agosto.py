# -*- coding: utf-8 -*-
"""Geocodifica a origem e traca a rota ORS das cargas sem polyline (vazias e carregadas).

Ritmo de 2,6 s entre chamadas (~23 req/min), com backoff no 403 — a licao da secao 12.12:
o free tier do ORS tem DOIS limites (~2.000/dia e ~40/min) e devolve a MESMA mensagem
"Quota exceeded" nos dois casos. O 403 que parecia cota diaria esgotada era rajada.

Por que ele e necessario mesmo com o robo diario rodando: o `tracar_rotas_pendentes` do
`embarques_auto` exclui `status IN ('Entregue','Cancelada')`, e a PERNA VAZIA nasce
'Entregue'. Ou seja, o robo diario nunca tracaria as pernas — este script e quem faz.

    python -X utf8 _tracar_rotas_agosto.py --desde 2026-08-01 --ate 2026-09-09
"""
import os, sys, time, argparse, unicodedata
# A pasta do proprio arquivo, nao um caminho cravado: estes scripts precisam rodar
# TAMBEM dentro do container (Linux), que e de onde o robo atemporal corrige os dados
# de producao. O `c:/Phyton-Projetos/...` que estava aqui quebrava com FileNotFoundError.
_AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _AQUI)
os.chdir(_AQUI)
import psycopg2, ors_client
from dotenv import load_dotenv
load_dotenv(os.path.join(_AQUI, '.env'))
ap = argparse.ArgumentParser()
ap.add_argument('--desde', default='2026-08-01')
ap.add_argument('--ate', default='2026-12-31')
A = ap.parse_args()


def nrm(s): return unicodedata.normalize('NFKD', str(s or '')).encode('ascii','ignore').decode().upper().strip()
c = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                     user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = c.cursor()
cur.execute("SELECT cidade_normalizada, uf, latitude, longitude FROM municipios_ibge")
CENT = {(nrm(a), b): (float(x), float(y)) for a, b, x, y in cur.fetchall()}

# ── 1. geocodifica origem
cur.execute("""SELECT id, origem_cidade, origem_uf FROM embarques_cargas
                WHERE data_carregamento BETWEEN %s AND %s
                  AND origem_latitude IS NULL""", (A.desde, A.ate))
n_geo = n_falha = 0
for cid, ci, uf in cur.fetchall():
    cc = CENT.get((nrm(ci), (uf or '')[:2].upper()))
    if not cc:
        n_falha += 1; continue
    cur.execute("UPDATE embarques_cargas SET origem_latitude=%s, origem_longitude=%s WHERE id=%s",
                (cc[0], cc[1], cid))
    n_geo += 1
c.commit()
print(f'origens geocodificadas: {n_geo}   sem centroide: {n_falha}', flush=True)

# ── 2. traca rota origem -> (cidades de rota) -> destinos
#
# Ordem: id DESCRESCENTE. As cargas recentes sao as que o operacional abre; uma de
# 05/08 ja entregue interessa menos que a de ontem.
cur.execute("""SELECT id, numero, origem_latitude, origem_longitude, COALESCE(viagem_vazia,false)
                 FROM embarques_cargas
                WHERE data_carregamento BETWEEN %s AND %s
                  AND rota_planejada_polyline IS NULL AND origem_latitude IS NOT NULL
                ORDER BY id DESC""", (A.desde, A.ate))
alvo = cur.fetchall()
print(f'cargas para tracar: {len(alvo)}', flush=True)
ok = erro = pulou = 0
for i, (cid, num, ola, oln, vazia) in enumerate(alvo, 1):
    cur.execute("""SELECT latitude, longitude FROM embarques_cargas_rota
                    WHERE carga_id=%s AND latitude IS NOT NULL ORDER BY ordem""", (cid,))
    pontos = [{'lat': float(a), 'lng': float(b)} for a, b in cur.fetchall()]
    cur.execute("""SELECT latitude, longitude FROM embarques_cargas_destinos
                    WHERE carga_id=%s AND latitude IS NOT NULL ORDER BY ordem""", (cid,))
    dests = [{'lat': float(a), 'lng': float(b)} for a, b in cur.fetchall()]
    if not dests:
        pulou += 1; continue
    # ORS estoura com muitos waypoints; carga de distribuicao usa so o ultimo destino
    if len(pontos) + len(dests) > 8:
        dests = dests[-1:]
        pontos = pontos[:6]
    seq = [{'lat': float(ola), 'lng': float(oln)}] + pontos + dests
    # O 403 "Quota exceeded" do ORS free tier NAO e a cota diaria — e o limite POR
    # MINUTO. Medido em 04/09: 54 erros concentrados entre 13:04:01 e 13:05:55, e a
    # chave voltou a responder sozinha (inclusive para a producao, as 16:30). Entao
    # nao se aborta: espera e tenta de novo.
    for tentativa in range(4):
        try:
            r = ors_client.tracar_rota_multi(seq)
            cur.execute("""UPDATE embarques_cargas
                              SET rota_planejada_polyline=%s, distancia_planejada_km=%s,
                                  duracao_estimada_min=%s, rota_recalculada_em=NOW()
                            WHERE id=%s""",
                        (r['polyline'], r['distancia_km'], r['duracao_min'], cid))
            c.commit(); ok += 1
            break
        except Exception as e:
            if 'Quota exceeded' in str(e) or '429' in str(e):
                espera = 70 * (tentativa + 1)
                print(f'  limite por minuto — aguardando {espera}s ({num})', flush=True)
                time.sleep(espera)
                continue
            erro += 1
            if erro <= 6: print(f'  falha {num}: {str(e)[:110]}', flush=True)
            break
    else:
        erro += 1
        print(f'  desisti de {num} apos 4 tentativas', flush=True)
    if i % 25 == 0:
        print(f'  {i}/{len(alvo)} · ok={ok} erro={erro} pulou={pulou}', flush=True)
    time.sleep(2.6)          # ~23 req/min, folga contra o teto de ~40/min
print(f'\nFIM: {ok} rotas tracadas · {erro} falhas · {pulou} sem destino', flush=True)
c.close()
