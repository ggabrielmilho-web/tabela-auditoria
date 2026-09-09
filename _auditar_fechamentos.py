# -*- coding: utf-8 -*-
"""Auditoria de CHEGADA e FECHAMENTO — a pergunta que o mapa levanta e a de mapas nao faz.

O `_auditar_mapas.py` audita o que a TELA desenha. Este audita o que a tela SIGNIFICA:
a carga diz "Entregue" — existe posicao provando que a carreta chegou ao destino?

Le direto do banco (nao precisa do servidor no ar) e reproduz a janela do endpoint do
mapa: de `data_carregamento - 12h` ate `data_conclusao` (Entregue) ou agora.

Classes:
  FECHOU SEM PROVA      Entregue e o trajeto NUNCA entrou no raio de 20 km do destino,
                        nem depois — ninguem viu essa carreta chegar
  FECHOU ANTES          entrou no raio, mas SO DEPOIS da conclusao — fechou cedo
  MEIA-NOITE            data_conclusao 00:00:00 exata — assinatura de fechamento
                        documental (o robo), nao de evento
  SILENCIO NO FIM       ultimo ponto muito antes do fim da janela — a linha para porque
                        o aparelho calou, nao porque cortaram
  VAZIA SEM SENSOR      perna vazia cuja carreta (o sensor) nao tem ponto na janela
  VAZIA NAO CONFERE     o trajeto desenhado cobre menos de 30% da reta da perna — a
                        placa mostrada nao fez esse trecho

    python -X utf8 _auditar_fechamentos.py --csv _auditoria_fechamentos.csv
"""
import os, sys, csv, argparse
from datetime import datetime as dt, timedelta as td, time as _time
sys.path.insert(0, r'c:/Phyton-Projetos/Tabela Auditoria')
os.chdir(r'c:/Phyton-Projetos/Tabela Auditoria')
import psycopg2
from dotenv import load_dotenv
load_dotenv('.env')
import geocoding as geo
import placas as pl

RAIO = float(os.getenv('RASTREAMENTO_RAIO_CHEGADA_DESTINO', '20'))
SILENCIO_H = 6.0

ap = argparse.ArgumentParser()
ap.add_argument('--desde', default='2026-08-01')
ap.add_argument('--ate', default='2026-09-30')
ap.add_argument('--csv')
a = ap.parse_args()

conn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                        user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = conn.cursor()
cur.execute("""SELECT id,numero,status,cavalo_placa,carreta1_placa,origem_cidade,origem_latitude,
                      origem_longitude,data_carregamento,data_conclusao,no_local_desde,
                      COALESCE(viagem_vazia,FALSE), entregue_auto, criada_por_robo
                 FROM embarques_cargas
                WHERE data_carregamento BETWEEN %s AND %s ORDER BY id""", (a.desde, a.ate))
cargas = cur.fetchall()

def pontos(placa, ini, fim):
    if not placa: return []
    cur.execute("""SELECT data_posicao, latitude, longitude, velocidade
                     FROM embarques_posicoes_historico
                    WHERE placa = ANY(%s) AND data_posicao BETWEEN %s AND %s
                    ORDER BY data_posicao""", (pl.grafias(placa), ini, fim))
    return [(d, float(la), float(ln), v) for d, la, ln, v in cur.fetchall()]

from collections import Counter, defaultdict
prob, exemplos, linhas = Counter(), defaultdict(list), []
print('auditando %d cargas (raio de chegada = %g km)...\n' % (len(cargas), RAIO))

for (cid, num, status, cav, c1, ocid, olat, olng, dcarg, dconc, nld, vazia, ent_auto, robo) in cargas:
    cur.execute("SELECT cidade,uf,latitude,longitude FROM embarques_cargas_destinos WHERE carga_id=%s ORDER BY ordem", (cid,))
    dests = cur.fetchall()
    if not dests or dests[-1][2] is None:
        continue
    dcid, duf, dla, dln = dests[-1][0], dests[-1][1], float(dests[-1][2]), float(dests[-1][3])
    base = dcarg if isinstance(dcarg, dt) else dt.combine(dcarg, _time())
    ini = base - td(hours=12)
    fim = (dconc or nld or dt.utcnow()) if status == 'Entregue' else dt.utcnow()

    # a placa que o mapa desenha: carreta1 se tiver ponto, senao cavalo (mesmo fallback da tela)
    p1, pc = pontos(c1, ini, fim), pontos(cav, ini, fim)
    traj, via = (p1, 'carreta1') if p1 else ((pc, 'cavalo') if pc else ([], '—'))
    achados = []
    dist_fim = min_dist = gap_h = None
    if traj:
        dist_fim = geo.km_entre(traj[-1][1], traj[-1][2], dla, dln)
        min_dist = min(geo.km_entre(p[1], p[2], dla, dln) for p in traj)
        gap_h = (fim - traj[-1][0]).total_seconds() / 3600.0
        # silencio SO importa se o veiculo ainda nao tinha chegado: parar no destino e
        # calar e comportamento normal do aparelho (95% das lacunas sao veiculo parado)
        if gap_h > SILENCIO_H and dist_fim is not None and dist_fim > RAIO:
            achados.append('PERDEU O RASTREIO EM ROTA (%.0fh calado, a %0.0f km do destino)' % (gap_h, dist_fim))
    else:
        gap_h = None

    if status == 'Entregue' and traj:
        if min_dist is not None and min_dist > RAIO:
            # entrou no raio DEPOIS do fechamento?
            dep = pontos(c1 if via == 'carreta1' else cav, fim, fim + td(days=7))
            dep_min = min((geo.km_entre(p[1], p[2], dla, dln) for p in dep), default=None)
            if dep_min is not None and dep_min <= RAIO:
                achados.append('FECHOU ANTES (chegou a %0.0f km do destino so depois)' % dep_min)
            elif gap_h is not None and gap_h <= SILENCIO_H:
                # o aparelho estava FALANDO na hora do fechamento, e estava longe:
                # o GPS nao deixou de ver, ele viu o contrario
                achados.append('FECHOU CONTRA O GPS (a %0.0f km do destino, aparelho ativo)' % dist_fim)
            else:
                achados.append('FECHOU SEM EVIDENCIA (aparelho calado ha %0.0fh, a %0.0f km)' % (gap_h, dist_fim))
    if status == 'Entregue' and dconc and dconc.hour == 0 and dconc.minute == 0 and dconc.second == 0:
        achados.append('MEIA-NOITE (conclusao 00:00:00 — fechamento documental)')
    if vazia:
        if not p1:
            achados.append('VAZIA SEM SENSOR (a carreta nao tem posicao na janela)')
        if traj and olat is not None:
            reta = geo.km_entre(float(olat), float(olng), dla, dln)
            perc = 0.0
            for i in range(1, len(traj)):
                perc += geo.km_entre(traj[i-1][1], traj[i-1][2], traj[i][1], traj[i][2]) or 0
            if reta and reta > 50 and perc < reta * 0.3:
                achados.append('VAZIA NAO CONFERE (trajeto %0.0f km para uma perna de %0.0f km)' % (perc, reta))

    if achados:
        for x in achados:
            k = x.split(' (')[0]
            prob[k] += 1
            if len(exemplos[k]) < 4:
                exemplos[k].append('%s (%s%s)' % (num, status, ' · VAZIA' if vazia else ''))
        linhas.append({'numero': num, 'id': cid, 'status': status, 'vazia': 'S' if vazia else 'N',
                       'robo': 'S' if robo else 'N', 'entregue_auto': 'S' if ent_auto else 'N',
                       'origem': ocid, 'destino': '%s/%s' % (dcid, duf), 'carreta': c1, 'cavalo': cav,
                       'via': via, 'conclusao': dconc, 'pontos': len(traj),
                       'km_ultimo_ponto_ao_destino': round(dist_fim, 1) if dist_fim else None,
                       'km_maior_aproximacao': round(min_dist, 1) if min_dist else None,
                       'h_calado_no_fim': round(gap_h, 1) if gap_h is not None else None,
                       'problemas': ' | '.join(achados)})

print('=' * 84)
print('  %d cargas · %d limpas · %d com pelo menos um apontamento' % (len(cargas), len(cargas) - len(linhas), len(linhas)))
print('=' * 84)
print('\n%-56s %6s' % ('APONTAMENTO', 'CARGAS'))
for k, v in prob.most_common():
    print('  %-54s %5d' % (k, v))
    for e in exemplos[k][:3]:
        print('      %s' % e)
if a.csv and linhas:
    with open(a.csv, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(linhas[0].keys()), delimiter=';')
        w.writeheader(); w.writerows(linhas)
    print('\n  CSV: %s (%d linhas)' % (a.csv, len(linhas)))
conn.close()
