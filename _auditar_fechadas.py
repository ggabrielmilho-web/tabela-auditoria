# -*- coding: utf-8 -*-
"""AUDITORIA READ-ONLY: as cargas JA FECHADAS foram fechadas na hora certa?

Para cada carga do robo encerrada, compara o instante do fechamento com a prova de
GPS (entrada no raio do destino), usando a MESMA geometria do codigo (raio 20 km,
carreta como sensor, tolerancia de 12 h porque manifesto nao tem hora).

Responde tres perguntas:
  1. quantas foram fechadas ANTES do caminhao chegar (e por qual motivo);
  2. para essas, QUANDO a evidencia apareceu -> e o horizonte de convergencia;
  3. quantas seriam corrigiveis HOJE, so lendo o banco.

Nao grava nada.
"""
import os, sys
from collections import defaultdict, Counter
from datetime import datetime, date, timedelta
# A pasta do proprio arquivo, nao um caminho cravado: estes scripts precisam rodar
# TAMBEM dentro do container (Linux), que e de onde o robo atemporal corrige os dados
# de producao. O `c:/Phyton-Projetos/...` que estava aqui quebrava com FileNotFoundError.
_AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _AQUI); os.chdir(_AQUI)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import psycopg2
from dotenv import load_dotenv
load_dotenv('.env')
import geocoding, placas as _placas

RAIO = float(os.getenv('AUD_RAIO_KM', '20'))
TOL_H = float(os.getenv('AUD_TOLERANCIA_H', '12'))

cn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                      user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = cn.cursor()
cur.execute("""
    SELECT c.id, c.numero, COALESCE(NULLIF(c.carreta1_placa,''), c.cavalo_placa) AS placa,
           c.data_carregamento, c.data_conclusao, c.status,
           COALESCE(c.encerrada_motivo,'(nulo)'), COALESCE(c.entregue_auto,FALSE),
           d.cidade, d.latitude, d.longitude
      FROM embarques_cargas c
      LEFT JOIN embarques_cargas_destinos d ON d.carga_id = c.id
           AND d.ordem = (SELECT MIN(ordem) FROM embarques_cargas_destinos x WHERE x.carga_id=c.id)
     WHERE c.criada_por_robo AND NOT COALESCE(c.viagem_vazia,FALSE)
       AND c.status IN ('Entregue','Cancelada')
       AND c.data_conclusao IS NOT NULL
     ORDER BY c.data_carregamento, c.id
""")
linhas = cur.fetchall()
print(f'cargas do robo JA FECHADAS: {len(linhas)}   (raio {RAIO:.0f} km, tolerancia {TOL_H:.0f} h)\n')

def chegadas(placa, dla, dln, desde, ate):
    """(1a entrada no raio, ultima posicao vista) — None se nunca entrou."""
    cur.execute("""SELECT data_posicao, latitude, longitude FROM embarques_posicoes_historico
                    WHERE placa = ANY(%s) AND data_posicao >= %s AND data_posicao < %s
                    ORDER BY data_posicao""",
                (_placas.grafias(str(placa).strip().upper()), desde, ate))
    prim, tempos, ultima = None, 0, None
    for d, la, ln in cur.fetchall():
        tempos += 1
        ultima = d
        if la is None or ln is None: continue
        km = geocoding.km_entre(float(la), float(ln), dla, dln)
        if km is not None and km <= RAIO and prim is None:
            prim = d
    return prim, tempos, ultima

placar = Counter()
por_motivo = defaultdict(Counter)
atrasos = []          # horas entre fechamento e chegada real (fechou cedo)
detalhe = []

for cid, num, placa, dcarg, dconc, status, motivo, auto, dcid, dla, dln in linhas:
    if dla is None or not placa:
        placar['nao julgavel (sem destino/placa)'] += 1
        por_motivo[motivo]['nao julgavel'] += 1
        continue
    base = datetime.combine(dcarg, datetime.min.time()) - timedelta(hours=12)
    prim, n, ultima = chegadas(placa, float(dla), float(dln), base, base + timedelta(days=45))
    if n == 0:
        placar['cega (sem GPS nenhum)'] += 1; por_motivo[motivo]['cega'] += 1; continue
    conc = dconc if isinstance(dconc, datetime) else datetime.combine(dconc, datetime.min.time())
    if prim is None:
        # §14.2: "nunca chegou" e "nao temos a posicao" sao coisas diferentes.
        # Se o historico da placa PARA antes do fechamento, o silencio e nosso, nao dela.
        if ultima is not None and ultima < conc:
            placar['SEM COBERTURA (historico acaba antes)'] += 1
            por_motivo[motivo]['sem cobertura'] += 1
            detalhe.append((num, motivo, str(dcarg), str(conc)[:16], dcid,
                            f'historico acaba {str(ultima)[:16]}', -1)); continue
        placar['NUNCA chegou (com cobertura)'] += 1; por_motivo[motivo]['nunca chegou'] += 1
        detalhe.append((num, motivo, str(dcarg), str(conc)[:16], dcid, 'nunca chegou', None)); continue
    delta_h = (prim - conc).total_seconds() / 3600.0
    if delta_h <= TOL_H:
        placar['OK (chegou antes do fechamento)'] += 1; por_motivo[motivo]['ok'] += 1
    else:
        placar['FECHADA CEDO (chegou depois)'] += 1; por_motivo[motivo]['cedo'] += 1
        atrasos.append(delta_h)
        detalhe.append((num, motivo, str(dcarg), str(conc)[:16], dcid, f'chegou {delta_h/24:.1f} d depois', delta_h))

print('PLACAR')
for k, v in placar.most_common(): print(f'   {k:<36} {v:>4}')

print('\nPOR MOTIVO DE FECHAMENTO')
print(f'   {"motivo":<24} {"ok":>4} {"cedo":>5} {"nunca":>6} {"s/cob":>6} {"cega":>5} {"n/j":>4}')
for m in sorted(por_motivo, key=lambda x: -sum(por_motivo[x].values())):
    d = por_motivo[m]
    print(f'   {m:<24} {d["ok"]:>4} {d["cedo"]:>5} {d["nunca chegou"]:>6} {d["sem cobertura"]:>6} {d["cega"]:>5} {d["nao julgavel"]:>4}')

if atrasos:
    atrasos.sort()
    def pct(p): return atrasos[min(len(atrasos)-1, int(len(atrasos)*p))]
    print(f'\nHORIZONTE DE CONVERGENCIA — quando a prova aparece depois do fechamento')
    print(f'   n={len(atrasos)}  p50={pct(.5)/24:.1f} d   p75={pct(.75)/24:.1f} d   '
          f'p90={pct(.9)/24:.1f} d   max={atrasos[-1]/24:.1f} d')
    for lim in (2, 3, 5, 7, 10, 15, 30):
        dentro = sum(1 for h in atrasos if h/24 <= lim)
        print(f'   janela de {lim:>2} dias cobre {dentro:>3} de {len(atrasos)}  ({dentro/len(atrasos)*100:.0f}%)')

print(f'\nAS FECHADAS CEDO, uma a uma (as {min(25,len(detalhe))} primeiras):')
print(f'   {"carga":<15} {"motivo":<22} {"carreg":<11} {"fechou":<17} {"destino":<22} veredito')
for r in sorted(detalhe, key=lambda x: -(x[6] or 0))[:25]:
    print(f'   {r[0]:<15} {r[1]:<22} {r[2]:<11} {r[3]:<17} {str(r[4])[:20]:<22} {r[5]}')
cn.close()
