# -*- coding: utf-8 -*-
"""O reprocessamento de 04/09 melhorou ou piorou cada fechamento?

Para cada carga que ele tocou, compara a conclusao ANTES e DEPOIS contra a unica
referencia independente: o instante em que a carreta ENTROU no raio de 20 km do destino.
Sem GPS na janela, nao ha juiz — a carga entra em 'sem referencia', nao em 'piorou'.
"""
import os, sys, csv
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
cur.execute("""SELECT e.id, e.numero, e.carreta1_placa, e.cavalo_placa, e.data_carregamento,
                      l.valor_anterior, l.valor_novo
                 FROM embarques_cargas_log l JOIN embarques_cargas e ON e.id = l.carga_id
                WHERE l.campo = 'data_conclusao' AND l.editado_em::date = '2026-09-04'
                ORDER BY e.id""")
tocadas = cur.fetchall()

def chegada_gps(placa, ini, fim, dla, dln):
    if not placa: return None
    cur.execute("""SELECT data_posicao, latitude, longitude FROM embarques_posicoes_historico
                    WHERE placa = ANY(%s) AND data_posicao BETWEEN %s AND %s ORDER BY data_posicao""",
                (pl.grafias(placa), ini, fim))
    for d, la, ln in cur.fetchall():
        if geo.km_entre(float(la), float(ln), dla, dln) <= RAIO:
            return d
    return None

def parse(v):
    try: return dt.fromisoformat(str(v))
    except Exception: return None

res, linhas = {'melhorou': 0, 'piorou': 0, 'empate': 0, 'sem referencia': 0}, []
for cid, num, c1, cav, dcarg, ant, nov in tocadas:
    cur.execute("SELECT latitude, longitude FROM embarques_cargas_destinos WHERE carga_id=%s ORDER BY ordem DESC LIMIT 1", (cid,))
    d = cur.fetchone()
    if not d or d[0] is None:
        res['sem referencia'] += 1; continue
    dla, dln = float(d[0]), float(d[1])
    base = dcarg if isinstance(dcarg, dt) else dt.combine(dcarg, _time())
    ini, fim = base - td(hours=12), base + td(days=20)
    t = chegada_gps(c1, ini, fim, dla, dln) or chegada_gps(cav, ini, fim, dla, dln)
    a, n = parse(ant), parse(nov)
    if t is None or a is None or n is None:
        res['sem referencia'] += 1
        linhas.append({'numero': num, 'antes': ant, 'depois': nov, 'chegada_gps': t,
                       'erro_antes_h': None, 'erro_depois_h': None, 'veredito': 'sem referencia'})
        continue
    ea, en = abs((a - t).total_seconds()) / 3600.0, abs((n - t).total_seconds()) / 3600.0
    v = 'melhorou' if en < ea - 0.5 else ('piorou' if en > ea + 0.5 else 'empate')
    res[v] += 1
    linhas.append({'numero': num, 'antes': ant, 'depois': nov, 'chegada_gps': t,
                   'erro_antes_h': round(ea, 1), 'erro_depois_h': round(en, 1), 'veredito': v})

print('=' * 78)
print('  %d cargas tiveram a conclusao reescrita em 04/09' % len(tocadas))
print('=' * 78)
for k in ('melhorou', 'piorou', 'empate', 'sem referencia'):
    print('  %-16s %3d' % (k, res[k]))
piores = sorted([x for x in linhas if x['veredito'] == 'piorou'],
                key=lambda x: -(x['erro_depois_h'] - x['erro_antes_h']))
print('\n  as 12 que mais pioraram (erro contra a chegada vista pelo GPS):')
print('  %-16s %-19s %-19s %-19s %8s %8s' % ('CARGA', 'ANTES', 'DEPOIS', 'CHEGADA GPS', 'ERRO_ANT', 'ERRO_DEP'))
for x in piores[:12]:
    print('  %-16s %-19s %-19s %-19s %7.1fh %7.1fh' % (x['numero'], str(x['antes'])[:19], str(x['depois'])[:19],
                                                       str(x['chegada_gps'])[:19], x['erro_antes_h'], x['erro_depois_h']))
mel = [x for x in linhas if x['veredito'] == 'melhorou']
if mel:
    print('\n  ganho mediano nas que melhoraram: %.1f h' % sorted(x['erro_antes_h'] - x['erro_depois_h'] for x in mel)[len(mel)//2])
with open('_auditoria_reprocessamento.csv', 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.DictWriter(f, fieldnames=list(linhas[0].keys()), delimiter=';'); w.writeheader(); w.writerows(linhas)
print('\n  CSV: _auditoria_reprocessamento.csv (%d linhas)' % len(linhas))
conn.close()
