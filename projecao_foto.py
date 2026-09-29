# -*- coding: utf-8 -*-
"""Foto diária da Projeção — para medir, depois, o que a tela previu.

Três coisas somem se ninguém guardar (decisão de 29/09/2026):
  1. o que a tela MOSTROU: ela recalcula a cada hora, e a retroanálise usa o modelo
     de hoje — toda mudança no motor reescreve o "quanto errou";
  2. a provisão do financeiro: quando o custo real chega, ela é apagada e relançada
     (Operacional) ou editada no lugar (Deduções) — é o único orçamento que existe e
     nunca foi medido;
  3. qual versão do motor produziu cada número.

Uma linha por dia (Brasília), gravada depois de PROJECAO_FOTO_HORA_BRT. A chave é o
dia: restart não duplica, e dia que falhou tenta de novo no ciclo seguinte (só lê o
BI e grava a própria tabela — repetir não tem efeito colateral).

⚠ Como comparar: o custo de um mês ainda recebe 0,1–4,8% (até R$ 238 mil, medido
set/25–jul/26) depois do dia 10 do mês seguinte, e ~0 depois do dia 30. Só compare a
foto com o real DEPOIS que o mês seguinte terminar.

`PROJECAO_FOTO=false` desliga sem deploy.
"""
import hashlib
import json
import os
import time
from datetime import datetime, timedelta

HORA_BRT = os.getenv('PROJECAO_FOTO_HORA_BRT', '07:00')
INTERVALO_SEG = 15 * 60

DDL = """
CREATE TABLE IF NOT EXISTS projecao_fotos (
    dia          DATE PRIMARY KEY,         -- Brasília
    gravada_em   TIMESTAMP NOT NULL,       -- UTC
    versao_motor VARCHAR(12) NOT NULL,     -- sha1 do projecao.py
    mes_corrente VARCHAR(7) NOT NULL,
    ult_receita  VARCHAR(7),
    ult_custo    VARCHAR(7),
    meses        JSONB NOT NULL,           -- projeção por mês: dre, origem, faixa, previsão do financeiro, contratado
    retro        JSONB                     -- o acerto que a tela mostrava naquele dia
);
"""


def ligado():
    return os.getenv('PROJECAO_FOTO', 'true').strip().lower() not in ('0', 'false', 'nao', 'não')


def _hora():
    try:
        h, m = [int(x) for x in HORA_BRT.strip().split(':')]
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except (ValueError, AttributeError):
        pass
    return 7, 0


def dia_a_gravar(agora_utc, ultimo_dia):
    """→ o dia (Brasília) a gravar agora, ou None. Recebe UTC, que é o relógio do
    container; o dia é lido em Brasília (22:00 BRT já é o dia seguinte em UTC)."""
    agora = agora_utc - timedelta(hours=3)
    h, m = _hora()
    if (agora.hour, agora.minute) < (h, m):
        return None
    return None if ultimo_dia == agora.date() else agora.date()


def versao_motor():
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'projecao.py'), 'rb') as f:
            return hashlib.sha1(f.read()).hexdigest()[:12]
    except OSError:
        return 'desconhecida'


def compactar(res):
    """Só o que não dá para reconstruir: a projeção e o que o SSW mostrava. O histórico
    real sai do BI a qualquer momento."""
    meses = [{k: m.get(k) for k in ('mes', 'h', 'receita_realizada', 'dre', 'origem', 'previsao_financeiro',
                                    'contratado', 'receita_p10', 'receita_p90', 'ebitda_p10', 'ebitda_p90',
                                    'nowcast') if k in m}
             for m in res['meses']]
    retro = {k: res['retroanalise'].get(k) for k in ('origens', 'por_faixa', 'erro_12m_medio_abs', 'n_12m')}
    return meses, retro


def gravar(cur, dia, res):
    cur.execute(DDL)
    meses, retro = compactar(res)
    cur.execute(
        """INSERT INTO projecao_fotos (dia, gravada_em, versao_motor, mes_corrente, ult_receita, ult_custo, meses, retro)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (dia) DO NOTHING""",
        (dia, datetime.utcnow().replace(microsecond=0), versao_motor(), res['mes_corrente'],
         res.get('ultimo_fechado_receita'), res.get('ultimo_fechado_custo'),
         json.dumps(meses, ensure_ascii=False), json.dumps(retro, ensure_ascii=False)))
    return cur.rowcount == 1


def ja_gravado(cur, dia):
    cur.execute(DDL)
    cur.execute("SELECT 1 FROM projecao_fotos WHERE dia = %s", (dia,))
    return cur.fetchone() is not None


def loop(dados_fn, get_db):
    """dados_fn() → o resultado de /api/projecao; get_db() → conexão psycopg2.
    Nunca derruba o processo."""
    h, m = _hora()
    print(f'✅ Foto diária da projeção LIGADA (a partir das {h:02d}:{m:02d} BRT)')
    ultimo = None
    while True:
        try:
            dia = dia_a_gravar(datetime.utcnow(), ultimo)
            if dia:
                conn = get_db()
                try:
                    with conn, conn.cursor() as cur:
                        if ja_gravado(cur, dia):
                            ultimo = dia
                        else:
                            res = dados_fn()
                            if gravar(cur, dia, res):
                                print(f'📸 Projeção: foto de {dia} gravada ({res["mes_corrente"]} + 11 meses)')
                            ultimo = dia
                finally:
                    conn.close()
        except Exception as e:
            # Falhou (BI fora, banco fora)? `ultimo` não anda: tenta no próximo ciclo.
            print(f'⚠️  Projeção: foto diária falhou: {e}')
        time.sleep(INTERVALO_SEG)
