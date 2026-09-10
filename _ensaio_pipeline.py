# -*- coding: utf-8 -*-
"""ENSAIO DO PIPELINE — um script so, que reproduz o ciclo inteiro e mede tudo.

POR QUE ELE EXISTE
------------------
O ciclo diario tem quatro passos (motor -> pernas -> rotas -> janela) que ate agora so
existiam como quatro scripts avulsos, dirigidos a mao ou pelo `server.rodar_pos_diario`.
Medir "como esta o pipeline" exigia rodar os quatro, ler quatro saidas diferentes e
juntar de cabeca — e foi assim que a §22.3 acabou escrita errada em tres pontos, e que
a §22.5 deixou uma variavel velha rodar em silencio.

Aqui e um comando so, com um veredito so. E ele NAO ESCREVE NADA por padrao: os tres
passos que tem `--aplicar` rodam sem ele, entao o numero que sai e "quantas cargas este
passo MUDARIA", que e exatamente a medida de convergencia (§20.6: rodar duas vezes tem
de dar zero; oscilacao e bug, nao "quase convergiu").

O QUE ELE RESPONDE
------------------
    1. FOTOGRAFIA   o que a base tem: cargas, pernas, rotas, janela real do historico
    2. CONVERGENCIA quantas cargas cada passo do ciclo ainda mudaria (0 = convergido)
    3. AFERIDOR     o placar de invariantes do `_auditoria_geral`
    4. FITA         densidade do historico e a POSICAO FALSA (a regua do odometro)
    5. FORMA        o defeito visivel no mapa, carga a carga, pela rota real
    6. VEREDITO     o resumo, e a lista do que difere entre esta base e producao

    python -X utf8 _ensaio_pipeline.py                    # local, so mede
    python -X utf8 _ensaio_pipeline.py --dias 40          # a janela que producao usa
    python -X utf8 _ensaio_pipeline.py --sem-passos       # pula os subprocessos (rapido)
    docker exec $CT python -X utf8 _ensaio_pipeline.py    # em producao, mesma regua

A REGUA DA POSICAO FALSA
------------------------
Entre dois pontos consecutivos da mesma placa, deslocamento que implica velocidade acima
do teto fisico e mentira: o veiculo nao fez aquilo. Quando o odometro existe nos dois
lados ele e o arbitro — cumulativo no aparelho, independente do GPS, e o proprio
repositorio ja o elegeu como fonte de km (§12.3). Medido na base local em 10/09/2026:
tirar so as pernas impossiveis derruba o erro do GPS contra o odometro de 415,8% para
7,5%, com 52 cargas melhorando contra 12 piorando.
"""
import argparse
import io
import os
import subprocess
import sys
from datetime import datetime, timedelta, time as _time

import psycopg2

if not os.getenv('DB_PASSWORD'):          # no container as env ja estao no ambiente
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))

ap = argparse.ArgumentParser()
ap.add_argument('--dias', type=int, default=int(os.getenv('EMBARQUES_ATEMPORAL_DIAS', '40')),
                help='janela para tras, igual a que o ciclo diario usa')
ap.add_argument('--ate', default=None, help='fim da janela (default: HOJE — ver §22.10)')
ap.add_argument('--sem-passos', action='store_true', help='nao roda os subprocessos')
ap.add_argument('--sem-forma', action='store_true', help='nao mede a forma do tracado')
ap.add_argument('--teto-kmh', type=float, default=150.0, help='teto fisico do cavalo')
ap.add_argument('--limite-cargas', type=int, default=400)
A = ap.parse_args()

ATE = datetime.strptime(A.ate, '%Y-%m-%d').date() if A.ate else datetime.utcnow().date()
DESDE = ATE - timedelta(days=A.dias)


def db():
    return psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT', 5432),
                            dbname=os.getenv('DB_NAME'), user=os.getenv('DB_USER'),
                            password=os.getenv('DB_PASSWORD'))


def titulo(n, t):
    print('\n' + '=' * 74)
    print('%d) %s' % (n, t))
    print('=' * 74)


# ══════════════════════════════════════════════════════════════════════════
# 1. FOTOGRAFIA
# ══════════════════════════════════════════════════════════════════════════
titulo(1, 'FOTOGRAFIA DA BASE   (janela %s .. %s)' % (DESDE, ATE))
c = db()
cur = c.cursor()
cur.execute("""
    SELECT count(*) FILTER (WHERE NOT COALESCE(viagem_vazia,FALSE))                AS cargas,
           count(*) FILTER (WHERE COALESCE(viagem_vazia,FALSE))                    AS pernas,
           count(*) FILTER (WHERE NOT COALESCE(viagem_vazia,FALSE)
                              AND rota_planejada_polyline IS NULL)                 AS carga_sem_rota,
           count(*) FILTER (WHERE COALESCE(viagem_vazia,FALSE)
                              AND rota_planejada_polyline IS NULL)                 AS perna_sem_rota,
           count(*) FILTER (WHERE status='Aberta')                                 AS abertas,
           count(*) FILTER (WHERE status='Em rota')                                AS em_rota,
           count(*) FILTER (WHERE data_saida_real IS NULL AND status<>'Aberta')    AS sem_saida,
           count(*) FILTER (WHERE COALESCE(criada_por_robo,FALSE))                 AS do_robo
      FROM embarques_cargas
     WHERE data_carregamento BETWEEN %s AND %s AND status <> 'Cancelada'
""", (DESDE, ATE))
f = cur.fetchone()
print('  cargas reais %-5d | pernas vazias %-5d | criadas pelo robo %d' % (f[0], f[1], f[7]))
print('  sem rota: carga %-3d perna %-3d | Aberta %-3d | Em rota %-3d | sem saida %d'
      % (f[2], f[3], f[4], f[5], f[6]))
cur.execute("""SELECT min(data_posicao), max(data_posicao), count(*),
                      count(DISTINCT placa) FROM embarques_posicoes_historico""")
h = cur.fetchone()
print('  historico de posicoes: %s .. %s  (%d pontos, %d placas)' % h)
cur.execute("""SELECT count(*), count(*) FILTER (WHERE atualizado_em > NOW()-interval '1 hour'),
                      max(NOW()-atualizado_em) FROM embarques_posicoes_atuais""")
p = cur.fetchone()
print('  polling ao vivo: %d placas, %d com posicao < 1h, mais velha %s' % p)
if p[1] == 0:
    print('  >>> ALERTA: nenhuma placa com posicao fresca — o polling ao vivo esta parado')
c.close()

# ══════════════════════════════════════════════════════════════════════════
# 2. CONVERGENCIA — os quatro passos, em dry-run
# ══════════════════════════════════════════════════════════════════════════
titulo(2, 'CONVERGENCIA DO CICLO   (dry-run: quantas cargas cada passo MUDARIA)')
if A.sem_passos:
    print('  (pulado por --sem-passos)')
else:
    # A ordem e a do `rodar_pos_diario`, e cada emenda dela ja custou uma rodada:
    # motor antes das pernas (a perna deriva a janela das vizinhas) e rotas antes da
    # rederivacao (a regua da lacuna divide a janela pela distancia da rota).
    # ESCRITA nao e ACHADO, e confundir os dois faz o ensaio mentir. O `par sobreposto`
    # do `_rederivar_vazias` faz `continue` de proposito ("nao se maquia com janela
    # artificial — a perna fica como esta e vira achado"), mas ele SAI NO MESMO CSV das
    # rederivacoes de verdade. Contar linhas do CSV dava 4 escritas onde havia 0.
    import csv as _csv

    def _classifica(caminho, coluna, valor_escrita):
        try:
            with io.open(caminho, encoding='utf-8-sig') as f:
                linhas = list(_csv.DictReader(f, delimiter=';'))
        except Exception:
            return None, None
        if coluna is None:
            return len(linhas), 0
        esc = sum(1 for l in linhas if (l.get(coluna) or '') == valor_escrita)
        return esc, len(linhas) - esc

    PASSOS = (
        ('motor  (_robo_atemporal)',   '_robo_atemporal.py',        '_robo_atemporal.csv',   None, None),
        ('pernas (_regerar_vazias)',   '_regerar_vazias_agosto.py', None,                    None, None),
        ('janela (_rederivar_vazias)', '_rederivar_vazias.py',      '_rederivar_vazias.csv', 'situacao', 'rederivada'),
    )
    for rotulo, script, csvnome, col, val in PASSOS:
        if not os.path.exists(os.path.join(BASE, script)):
            print('  %-30s AUSENTE (%s)' % (rotulo, script)); continue
        try:
            r = subprocess.run([sys.executable, '-X', 'utf8', script,
                                '--desde', DESDE.isoformat(), '--ate', ATE.isoformat()],
                               cwd=BASE, capture_output=True, text=True, timeout=1800)
            if r.returncode != 0:
                print('  %-30s EXIT %d — %s' % (rotulo, r.returncode, (r.stderr or '').strip()[-160:]))
                continue
            if csvnome:
                esc, ach = _classifica(os.path.join(BASE, csvnome), col, val)
                if esc is None:
                    print('  %-30s (sem csv)' % rotulo)
                else:
                    marca = '' if esc == 0 else '   <== NAO CONVERGIU'
                    print('  %-30s escreveria %3d   |   achados (nao escreve) %3d%s'
                          % (rotulo, esc, ach, marca))
            else:
                eco = [l for l in (r.stdout or '').splitlines() if 'pernas ·' in l or 'pernas·' in l]
                print('  %-30s %s' % (rotulo, (eco[-1] if eco else '(sem contagem)').strip()[:90]))
        except Exception as e:
            print('  %-30s FALHOU: %s' % (rotulo, e))
    # rotas: nao roda (chama o ORS e escreve). Conta o que ele teria a fazer.
    c = db(); cur = c.cursor()
    cur.execute("""SELECT count(*) FROM embarques_cargas
                    WHERE data_carregamento BETWEEN %s AND %s AND status <> 'Cancelada'
                      AND rota_planejada_polyline IS NULL
                      AND origem_latitude IS NOT NULL""", (DESDE, ATE))
    print('  %-30s %d carga(s) tracaria no ORS (nao executado: escreve e gasta cota)'
          % ('rotas  (_tracar_rotas)', cur.fetchone()[0]))
    c.close()

# ══════════════════════════════════════════════════════════════════════════
# 3. AFERIDOR
# ══════════════════════════════════════════════════════════════════════════
titulo(3, 'AFERIDOR   (_auditoria_geral — as invariantes T/S/V/C/F/D)')
if A.sem_passos or not os.path.exists(os.path.join(BASE, '_auditoria_geral.py')):
    print('  (pulado)')
else:
    try:
        r = subprocess.run([sys.executable, '-X', 'utf8', '_auditoria_geral.py',
                            '--desde', DESDE.isoformat(), '--ate', ATE.isoformat()],
                           cwd=BASE, capture_output=True, text=True, timeout=1800)
        for l in (r.stdout or '').splitlines():
            if l.strip():
                print('  ' + l.rstrip()[:120])
    except Exception as e:
        print('  FALHOU: %s' % e)

# ══════════════════════════════════════════════════════════════════════════
# 4. A FITA — densidade e posicao falsa
# ══════════════════════════════════════════════════════════════════════════
titulo(4, 'A FITA   (densidade do historico e a POSICAO FALSA)')
c = db(); cur = c.cursor()
print('  odometro so vem do /HistoricoPosicao — "sem odo" alto = o backfill nao passou')
cur.execute("""SELECT (data_posicao - interval '3 hours')::date AS dia, count(*),
                      count(DISTINCT placa),
                      round(100.0*count(*) FILTER (WHERE odometer IS NULL)/count(*),1)
                 FROM embarques_posicoes_historico
                WHERE data_posicao >= %s GROUP BY 1 ORDER BY 1 DESC LIMIT 12""",
            (datetime.combine(ATE, _time()) - timedelta(days=12),))
print('  %-12s %8s %7s %11s' % ('dia(BRT)', 'pontos', 'placas', 'sem_odo'))
for dia, pts, pl, pct in cur.fetchall():
    print('  %-12s %8d %7d %10s%%%s' % (dia, pts, pl, pct,
          '  <== backfill NAO passou' if (pct or 0) > 20 else ''))

SQL_PARES = """
WITH s AS (
  SELECT placa, data_posicao, latitude, longitude, odometer, cidade,
         LAG(latitude) OVER w la0, LAG(longitude) OVER w ln0,
         LAG(odometer) OVER w odo0, LAG(data_posicao) OVER w dt0, LAG(cidade) OVER w cid0
    FROM embarques_posicoes_historico WHERE data_posicao >= %s
  WINDOW w AS (PARTITION BY placa ORDER BY data_posicao)
), d AS (
  SELECT *, 6371*acos(LEAST(1,GREATEST(-1, sin(radians(la0))*sin(radians(latitude))+
       cos(radians(la0))*cos(radians(latitude))*cos(radians(longitude-ln0))))) km,
     EXTRACT(EPOCH FROM (data_posicao-dt0))/3600.0 h
   FROM s WHERE la0 IS NOT NULL)
"""
ini_fita = datetime.combine(DESDE, _time())
cur.execute(SQL_PARES + """
SELECT count(*),
       count(*) FILTER (WHERE km>30 AND h>0 AND km/h>%s),
       count(DISTINCT placa) FILTER (WHERE km>30 AND h>0 AND km/h>%s),
       count(*) FILTER (WHERE km>30 AND h>0 AND km/h>%s AND odometer IS NOT NULL
                          AND odo0 IS NOT NULL AND (odometer-odo0) < km*0.5),
       count(*) FILTER (WHERE km>30 AND h>0 AND km/h<=%s)
  FROM d""", (ini_fita, A.teto_kmh, A.teto_kmh, A.teto_kmh, A.teto_kmh))
pares, falsas, plfalsas, odonega, buracos = cur.fetchone()
print('\n  pares consecutivos ................... %8d' % pares)
print('  POSICAO FALSA (> %.0f km/h implicito) . %8d  em %d placas'
      % (A.teto_kmh, falsas, plfalsas))
print('    com o ODOMETRO negando o desloc. ... %8d  <- prova, nao inferencia' % odonega)
print('  buraco real na fita (vel. plausivel) . %8d' % buracos)
cur.execute(SQL_PARES + """
SELECT placa, to_char(dt0,'DD/MM HH24:MI'), to_char(data_posicao,'DD/MM HH24:MI'),
       left(coalesce(cid0,''),14), left(coalesce(cidade,''),14), odo0, odometer,
       round(km::numeric,1), round((h*60)::numeric,1)
  FROM d WHERE km>30 AND h>0 AND km/h>%s ORDER BY km DESC LIMIT 10""",
            (ini_fita, A.teto_kmh))
linhas = cur.fetchall()
if linhas:
    print('\n  %-9s %-12s %-12s %-14s %-14s %8s %8s %8s %6s' % (
        'placa', 'de', 'ate', 'cid_de', 'cid_ate', 'odo_de', 'odo_ate', 'km', 'min'))
    for l in linhas:
        print('  %-9s %-12s %-12s %-14s %-14s %8s %8s %8s %6s' % l)
c.close()

# ══════════════════════════════════════════════════════════════════════════
# 5. A FORMA DO TRACADO — pela rota real que a tela usa
# ══════════════════════════════════════════════════════════════════════════
titulo(5, 'A FORMA DO TRACADO   (pelo endpoint real, o mesmo que a tela consome)')
resumo_forma = None
if A.sem_forma:
    print('  (pulado por --sem-forma)')
else:
    os.environ.setdefault('START_WORKER', 'false')
    os.environ.setdefault('EMBARQUES_AUTO', 'false')
    os.environ.setdefault('PGR_SYNC_CADASTRO', 'false')
    os.environ.setdefault('EMBARQUES_ATEMPORAL', 'false')
    sys.path.insert(0, BASE)
    try:
        from server import app
        import geocoding as geo
    except Exception as e:
        print('  nao consegui importar o server (%s)' % e)
        app = None
    if app is not None:
        c = db(); cur = c.cursor()
        cur.execute("""SELECT id, numero FROM embarques_cargas
                        WHERE data_carregamento BETWEEN %s AND %s AND status <> 'Cancelada'
                        ORDER BY id DESC LIMIT %s""", (DESDE, ATE, A.limite_cargas))
        alvos = cur.fetchall(); c.close()
        cli = app.test_client()
        with cli.session_transaction() as s:
            s['user_id'] = 1; s['nome'] = 'ensaio'; s['role'] = 'admin'
            s['tipos_permitidos'] = []; s['paginas_permitidas'] = []

        def _dt(v):
            try:
                return datetime.fromisoformat(str(v).replace('Z', ''))
            except Exception:
                return None

        med, tot, comfalsa, kpimudo, ambos = [], 0, 0, 0, 0
        for cid, num in alvos:
            j = cli.get('/api/rastreamento/cargas/%d/trajeto' % cid).get_json() or {}
            if not j.get('ok'):
                continue
            rv = (j.get('rastreado_via') or {}).get('tipo') or 'cavalo'
            tr = (j.get('trajeto') or {}).get(rv) or []
            if len(tr) < 2:
                tr = ((j.get('trajeto') or {}).get('cavalo')
                      or (j.get('trajeto') or {}).get('carreta1') or [])
            if len(tr) < 2:
                continue
            tot += 1
            km_cru = km_limpo = 0.0
            n_falsa = 0
            pior = 0.0
            for a, b in zip(tr, tr[1:]):
                km = geo.km_entre(a['lat'], a['lng'], b['lat'], b['lng']) or 0
                km_cru += km
                ta, tb = _dt(a['data']), _dt(b['data'])
                hh = ((tb - ta).total_seconds() / 3600.0) if (ta and tb) else 0
                oa, ob = a.get('odometer'), b.get('odometer')
                nega = (oa is not None and ob is not None and (ob - oa) < km * 0.5)
                if km > 30 and ((hh > 0 and km / hh > A.teto_kmh) or nega):
                    n_falsa += 1
                    pior = max(pior, km)
                else:
                    km_limpo += km
            k = j.get('kpi') or {}
            mudo = k.get('distancia_km') is None
            if n_falsa:
                comfalsa += 1
            if mudo:
                kpimudo += 1
            if n_falsa and mudo:
                ambos += 1
            odos = [p.get('odometer') for p in tr if p.get('odometer') is not None]
            km_odo = (odos[-1] - odos[0]) if len(odos) >= 2 else None
            if n_falsa and km_odo and km_odo > 0:
                med.append((num, n_falsa, round(km_cru, 1), round(km_limpo, 1), km_odo,
                            abs(km_cru - km_odo) / km_odo * 100,
                            abs(km_limpo - km_odo) / km_odo * 100))
        print('  cargas com tracado desenhavel ................ %d' % tot)
        if tot:
            print('  com posicao FALSA na linha ................... %d  (%.0f%%)'
                  % (comfalsa, 100.0 * comfalsa / tot))
            print('  com o KPI de km ja suprimido ("—") ........... %d' % kpimudo)
            print('  linha impossivel E KPI suprimido ............. %d  <- a tela se contradiz'
                  % ambos)
        if med:
            mc = sum(x[5] for x in med) / len(med)
            ml = sum(x[6] for x in med) / len(med)
            melhor = sum(1 for x in med if x[6] < x[5])
            print('\n  -- o teste que decide: tirar SO a perna impossivel aproxima do odometro? --')
            print('  erro medio contra o odometro:  CRU %7.1f%%   ->   LIMPO %6.1f%%' % (mc, ml))
            print('  cargas que melhoraram %d de %d' % (melhor, len(med)))
            print('\n  %-16s %6s %10s %10s %9s %9s %10s' % (
                'numero', 'falsas', 'km_CRU', 'km_LIMPO', 'km_ODO', 'err_cru', 'err_limpo'))
            for x in sorted(med, key=lambda y: -y[5])[:12]:
                print('  %-16s %6d %10s %10s %9s %8.1f%% %9.1f%%'
                      % (x[0], x[1], x[2], x[3], x[4], x[5], x[6]))
            resumo_forma = (tot, comfalsa, mc, ml)

# ══════════════════════════════════════════════════════════════════════════
# 6. VEREDITO
# ══════════════════════════════════════════════════════════════════════════
titulo(6, 'VEREDITO')
print('  janela usada ............ %s .. %s  (%d dias, --ate %s)'
      % (DESDE, ATE, A.dias, 'HOJE' if not A.ate else A.ate))
print('  teto fisico ............. %.0f km/h' % A.teto_kmh)
if resumo_forma:
    tot, comfalsa, mc, ml = resumo_forma
    print('  mapas com linha impossivel  %d de %d (%.0f%%)' % (comfalsa, tot, 100.0 * comfalsa / tot))
    print('  erro contra o odometro ...  %.1f%% cru  ->  %.1f%% limpo' % (mc, ml))
print("""
  COMO LER: convergencia (secao 2) tem de dar ZERO nos tres passos. Diferente de zero
  significa que a base ainda nao esta no ponto fixo do proprio motor — e a mesma medida
  vale local e em producao, com a MESMA regua, que e o ponto da §20.5.
""")
