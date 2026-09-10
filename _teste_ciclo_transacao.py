# -*- coding: utf-8 -*-
"""As TRES etapas do ciclo do worker sao independentes? (regressao de 10/09/2026)

O CASO QUE ORIGINOU ESTE TESTE
------------------------------
Producao ficou 30 HORAS sem rastreamento e nada acusou. A tabela
`embarques_rastreio_dia` nunca tinha sido criada la — o `init_db.py` a cria desde o
commit 7de4860, mas ninguem o rodou depois. Entao, a cada ciclo:

    _persistir_posicoes(cur, posicoes)   grava as 93 posicoes
    _consolidar_dias(cur)                UndefinedTable  ->  levanta
    except: conn.rollback()              e leva as 93 posicoes junto

E o diagnostico ficava escondido porque o log da 3S roda em CONEXAO SEPARADA: o
`embarques_3s_log` mostrava 66 chamadas/hora, HTTP 200, ZERO erros, enquanto o banco
nao andava. O `/api/rastreamento/health` dizia verde pelo mesmo motivo.

O que transformou uma falha diaria em falha permanente foi `_ultima_retencao =
datetime.utcnow()` estar DEPOIS da chamada que levantava: nunca era marcada,
`_deve_rodar_retencao()` seguia True e TODO ciclo repetia. Sem isso: 1 ciclo perdido
por dia. Com isso: 100% deles.

O QUE ESTE TESTE EXIGE
----------------------
    1. a POSICAO grava mesmo com a consolidacao explodindo (fato bruto nao depende
       de derivacao nossa)
    2. a PURGA nao roda quando a consolidacao falhou (o que a purga leva nao volta —
       a 3S serve so ~35 dias)
    3. `_ultima_retencao` e marcada mesmo na falha (o retry certo e amanha, nao em 60s)

Roda contra o codigo REAL (`W._ciclo`), com dubles so nas bordas. Conferido nos dois
sentidos em 10/09/2026: no codigo anterior da "AINDA QUEBRADO" nos itens 1 e 3; no
corrigido, "CONSERTO PROVADO". Teste que passa nas duas versoes nao prova nada.

    python -X utf8 _teste_ciclo_transacao.py

ATENCAO: faz UMA chamada real a 3S e grava as posicoes do momento. E aditivo (posicao e
fato), mas nao rode contra producao sem querer isso.
"""
import os, sys, time, threading
os.environ['START_WORKER']='false'; os.environ['EMBARQUES_AUTO']='false'
os.environ['PGR_SYNC_CADASTRO']='false'; os.environ['MODO_SIMULADO']='false'
sys.path.insert(0, r'c:/Phyton-Projetos/Tabela Auditoria')
import psycopg2
from dotenv import load_dotenv
load_dotenv(r'c:/Phyton-Projetos/Tabela Auditoria/.env')
import rastreamento_worker as W

def snap():
    c = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'),
                         dbname=os.getenv('DB_NAME'), user=os.getenv('DB_USER'),
                         password=os.getenv('DB_PASSWORD'))
    cur = c.cursor()
    cur.execute("SELECT count(*), max(atualizado_em) FROM embarques_posicoes_atuais")
    a = cur.fetchone()
    cur.execute("SELECT count(*) FROM embarques_posicoes_historico")
    b = cur.fetchone()[0]
    c.close(); return a[0], a[1], b

# ── os dublês: a consolidação explode como em produção; o resto fica quieto
chamou = {'consolidar': 0, 'purgar': 0, 'cargas': 0}
def _consolidar_explode(cur):
    chamou['consolidar'] += 1
    raise psycopg2.errors.UndefinedTable('relation "embarques_rastreio_dia" does not exist')
def _purgar_espiao(cur):
    chamou['purgar'] += 1
def _cargas_noop(cur):
    chamou['cargas'] += 1          # noop: o teste é das FRONTEIRAS de transação
W._consolidar_dias = _consolidar_explode
W._purgar_posicoes_antigas = _purgar_espiao
W._processar_cargas = _cargas_noop
W._ultima_retencao = None          # força a retenção a rodar neste ciclo

n0, t0, h0 = snap()
print('ANTES   placas=%d  mais_recente=%s  historico=%d' % (n0, t0, h0))

W._running = True
th = threading.Thread(target=W._ciclo, daemon=True); th.start()
time.sleep(12)
W._running = False
th.join(timeout=15)

n1, t1, h1 = snap()
print('DEPOIS  placas=%d  mais_recente=%s  historico=%d' % (n1, t1, h1))
print('\nchamadas: consolidar=%d  purgar=%d  processar_cargas=%d'
      % (chamou['consolidar'], chamou['purgar'], chamou['cargas']))
print('_ultima_retencao marcada = %s' % (W._ultima_retencao is not None))

print()
ok = True
if chamou['consolidar'] == 0:
    print('INCONCLUSIVO: a consolidacao nem foi chamada'); ok = False
else:
    if t1 != t0:
        print('OK      a POSICAO foi gravada mesmo com a consolidacao explodindo')
    else:
        print('FALHOU  a posicao NAO andou — o rollback ainda leva tudo'); ok = False
    if chamou['purgar'] == 0:
        print('OK      a PURGA nao rodou (consolidar falhou -> nao se destroi o historico)')
    else:
        print('FALHOU  a purga rodou depois da consolidacao falhar'); ok = False
    if W._ultima_retencao is not None:
        print('OK      _ultima_retencao marcada — o retry e amanha, nao a cada 60s')
    else:
        print('FALHOU  _ultima_retencao ficou None — volta o hot loop de todo ciclo'); ok = False
print('\n%s' % ('>>> CONSERTO PROVADO' if ok else '>>> AINDA QUEBRADO'))
