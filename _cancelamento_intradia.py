# -*- coding: utf-8 -*-
"""CANCELAMENTO INTRADIARIO — quanto documento nasce, some e muda entre refreshes.

E a medicao que decide se o TETO da reconciliacao (`EMBARQUES_RECONCILIACAO_TETO`,
default 3 por rodada) esta calibrado: acima dele a rodada ABORTA sem cancelar nada,
tratando o sumico como extracao truncada. Ate 15/09 o dado era "zero manifestos
sumiram desde 01/08 com defasagem 1" — mas cancelamento e INTRADIARIO, e so a fita
documental (27.11) enxerga isso.

Duas perguntas, nesta ordem:

  1. FITA — entre duas rodadas consecutivas, quantas chaves sumiram? E dessas, quantas
     lastreiam carga do robo, que e o que o teto conta de fato? A que sumiu voltou numa
     rodada seguinte (extracao truncada) ou ficou fora (cancelamento de verdade)?
  2. LOG — o que a reconciliacao efetivamente escreveu na janela: `manifesto_cancelado`
     e `manifesto voltou`.

⚠ So compara rodadas do MESMO dia UTC. A fita pede ao BI `data_emissao >= hoje - 7d`, e
`hoje` ali e a data do container, que roda em UTC: na virada do dia UTC (21:00 BRT) o
piso da janela anda um dia e os manifestos da ponta velha somem sem terem sido cancelados.
Comparar por cima dessa fronteira contaria janela como sumico (10, 27.8, 27.12).

Nao grava nada.

    python -X utf8 _cancelamento_intradia.py            # ultimas 72 h
    python -X utf8 _cancelamento_intradia.py --horas 24
"""
import os
import sys
import argparse
from collections import defaultdict

# `__file__` nao existe quando o script e PIPADO para dentro do container
# (`docker exec -i ... python -X utf8 - < este_arquivo`).
_AQUI = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
sys.path.insert(0, _AQUI)
os.chdir(_AQUI)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import psycopg2
from dotenv import load_dotenv
load_dotenv('.env')

ap = argparse.ArgumentParser()
ap.add_argument('--horas', type=int, default=72)
ap.add_argument('--fonte', default='manifesto', help='fonte detalhada (manifesto/coleta/ctrb/cte)')
A = ap.parse_args()
TETO = int(os.getenv('EMBARQUES_RECONCILIACAO_TETO', '3'))

cn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                      user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = cn.cursor()

cur.execute("SELECT to_regclass('fita_documentos')")
if not cur.fetchone()[0]:
    print('fita_documentos nao existe nesta base — EMBARQUES_FITA nunca rodou aqui')
    sys.exit(0)

# ── quantas RODADAS por dia: tem de ser ~8 (uma por refresh). Perto de 144 = o marcador
# voltou a comparar texto (27.11).
cur.execute("""SELECT (rodada - interval '3 hours')::date AS dia_brt,
                      count(DISTINCT rodada) AS rodadas, count(*) AS linhas,
                      min(rodada - interval '3 hours') AS primeira_brt,
                      max(rodada - interval '3 hours') AS ultima_brt
                 FROM fita_documentos
                WHERE rodada >= NOW() - (%s || ' hours')::interval
                GROUP BY 1 ORDER BY 1 DESC""", (str(A.horas),))
print(f'{"dia BRT":<12} {"rodadas":>8} {"linhas":>8}   primeira .. ultima (BRT)')
for dia, n, li, p, u in cur.fetchall():
    print(f'{str(dia):<12} {n:>8} {li:>8}   {str(p)[:16]} .. {str(u)[:16]}'
          + ('   <-- esperado ~8' if n > 20 else ''))

# ── o retrato de cada rodada, por fonte
cur.execute("""SELECT rodada, fonte, chave, hash FROM fita_documentos
                WHERE rodada >= NOW() - (%s || ' hours')::interval
                ORDER BY rodada""", (str(A.horas),))
por_rodada = defaultdict(lambda: defaultdict(dict))
for rod, fonte, chave, h in cur.fetchall():
    por_rodada[rod][fonte][chave] = h
rodadas = sorted(por_rodada)
print(f'\n{len(rodadas)} rodadas na janela de {A.horas} h\n')

print(f'{"de":<12} -> {"para":<12} {"fonte":<10} {"antes":>6} {"nasc":>5} {"SUMIU":>6} {"mudou":>6}')
sumidos = defaultdict(list)      # chave -> [(rodada em que sumiu)]
for i in range(1, len(rodadas)):
    ant, atu = rodadas[i - 1], rodadas[i]
    if ant.date() != atu.date():
        print(f'{ant:%d/%m %H:%M} -> {atu:%d/%m %H:%M}  (virada do dia UTC — nao se compara)')
        continue
    for fonte in ('manifesto', 'coleta', 'ctrb', 'cte'):
        a, b = por_rodada[ant].get(fonte, {}), por_rodada[atu].get(fonte, {})
        if not a and not b:
            continue
        nasc = set(b) - set(a)
        sum_ = set(a) - set(b)
        mud = [c for c in set(a) & set(b) if a[c] != b[c]]
        print(f'{ant:%d/%m %H:%M} -> {atu:%d/%m %H:%M} {fonte:<10} {len(a):>6} {len(nasc):>5} '
              f'{len(sum_):>6} {len(mud):>6}' + ('   <-- olhar' if sum_ else ''))
        if fonte == A.fonte:
            for c in sum_:
                sumidos[c].append(atu)

# ── quem sumiu: voltou depois? lastreia carga do robo? (e o que o teto conta)
print(f'\n=== {A.fonte}: {len(sumidos)} chave(s) sumiram em alguma rodada ===')
if sumidos:
    cur.execute("""SELECT UPPER(REGEXP_REPLACE(manifesto_origem, '[^A-Za-z0-9]', '', 'g')),
                          numero, status, data_carregamento, COALESCE(criada_por_robo, FALSE)
                     FROM embarques_cargas WHERE manifesto_origem IS NOT NULL""")
    cargas = {r[0]: r[1:] for r in cur.fetchall()}
    por_rodada_sum = defaultdict(int)
    for chave, quando in sorted(sumidos.items()):
        voltou = [r for r in rodadas if r > quando[-1] and chave in por_rodada[r].get(A.fonte, {})]
        c = cargas.get(chave)
        por_rodada_sum[quando[-1]] += 1 if (c and c[3]) else 0
        print(f'   {chave:<22} sumiu em {", ".join(f"{q:%d/%m %H:%M}" for q in quando)}'
              f'  {"VOLTOU em " + format(voltou[0], "%d/%m %H:%M") if voltou else "nao voltou"}'
              f'  carga {c[0] + " [" + c[1] + "]" if c else "—  (nenhuma carga lastreada)"}'
              f'{"  <- CARGA DO ROBO: e uma candidata da reconciliacao" if c and c[3] else ""}')
    pior = max(por_rodada_sum.values()) if por_rodada_sum else 0
    print(f'\n   pior rodada: {pior} carga(s) do robo com manifesto sumido · teto atual {TETO}'
          + ('   -> ABORTARIA a rodada' if pior > TETO else '   -> dentro do teto'))
else:
    print('   nenhuma — nada a decidir sobre o teto com este volume')

# ── o que a reconciliacao escreveu de fato
cur.execute("""SELECT c.numero, l.editado_em - interval '3 hours', l.usuario_nome,
                      l.valor_anterior, l.valor_novo
                 FROM embarques_cargas_log l JOIN embarques_cargas c ON c.id = l.carga_id
                WHERE (l.valor_novo ILIKE '%%manifesto_cancelado%%'
                       OR l.valor_novo ILIKE '%%manifesto voltou%%')
                  AND l.editado_em >= NOW() - (%s || ' hours')::interval
                ORDER BY 2""", (str(A.horas),))
linhas = cur.fetchall()
print(f'\n=== reconciliacao no log, ultimas {A.horas} h: {len(linhas)} linha(s) ===')
for num, quando, quem, de, para in linhas:
    print(f'   {num}  {str(quando)[:16]} BRT  {quem:<22} {de} -> {para}')

cn.rollback()
cn.close()
