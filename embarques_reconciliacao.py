# -*- coding: utf-8 -*-
"""Reconciliação de documento — o "atemporal do papel" (HANDOFF §25.8, Passo 1).

O robô atemporal relê o GPS; ninguém relia o DOCUMENTO. Com a extração do SSW rodando
8×/dia, o manifesto que nasce às 08:00 pode ser cancelado às 11:00 — e cancelado, ele
simplesmente SOME do relatório 916 (não há status de cancelado: o Gabriel testou à mão
em 14/09/26). O loader faz DELETE do período + INSERT numa transação, então "sumiu" é o
único sinal, e é um sinal limpo.

Regra desta rodada (só o cancelamento; placa/destino corrigidos ficam para depois da
fita medir quanto acontece):

    manifesto_origem da carga não está mais na lista que o `coletar()` devolveu
    → `Cancelada` (encerrada_motivo = 'manifesto_cancelado'), com log

    o manifesto VOLTOU (a extração de ontem falhou, não era cancelamento)
    → volta ao status anterior, lido do log. Cancelar por sumiço é RETRATÁVEL.

Guardas — cada uma tem um caso que a justifica:
  * só carga `criada_por_robo`, nunca editada à mão (o log diz quem escreveu) — §0;
  * sem `continua_em` e fora de `Cancelada`/perna vazia — a ligação já é terminal;
  * só carga cuja data de carregamento está na JANELA que o 916 recarrega (mês corrente,
    interseção com a janela do robô). Fora dela, ausência não é cancelamento — é só um
    manifesto que a extração não reprocessa mais;
  * TETO por rodada (`EMBARQUES_RECONCILIACAO_TETO`, default 3): mais que isso numa rodada
    é assinatura de extração truncada, não de cancelamento em massa — aborta e avisa.
    Zero manifestos sumiram entre 01/08 e 15/09 com defasagem 1, então o normal é 0–1.

TUDO atrás de `EMBARQUES_RECONCILIACAO` (nasce desligada). Sem DDL: usa `status`,
`encerrada_motivo` e o log, que já existem.
"""
import os
import logging
from collections import Counter
from datetime import date

_logger = logging.getLogger('embarques_reconciliacao')

MOTIVO = 'manifesto_cancelado'
AUTORES_ROBO = ('Robô SSW (manifesto)', 'Robo atemporal', 'Rederivacao de vazias')


def ligado():
    return str(os.getenv('EMBARQUES_RECONCILIACAO', 'false')).strip().lower() in ('1', 'true', 'sim', 'yes')


def _teto():
    try:
        return int(os.getenv('EMBARQUES_RECONCILIACAO_TETO', '3'))
    except ValueError:
        return 3


def _vistos(manifestos):
    from embarques_auto import _norm
    return {_norm(m.get('CHAVE_MANIFESTO')) for m in manifestos if m.get('CHAVE_MANIFESTO')}


def _editada_a_mao(cur, carga_id):
    cur.execute("SELECT 1 FROM embarques_cargas_log WHERE carga_id=%s AND usuario_nome NOT IN %s LIMIT 1",
                (carga_id, AUTORES_ROBO))
    return cur.fetchone() is not None


def cancelar_sumidos(cur, manifestos, ini, fim):
    """Carga do robô cujo manifesto não está mais no relatório → Cancelada."""
    from embarques_auto import _norm, _log
    import embarques_continuacao as _ec
    n = Counter()
    vistos = _vistos(manifestos)
    if not vistos:
        n['lista de manifestos vazia — nada a reconciliar'] += 1     # extração falhou? não decide
        return n
    piso = max(ini, date(fim.year, fim.month, 1))       # o 916 recarrega dia 1..ontem (+ hoje)
    lig = " AND continua_em IS NULL" if _ec.colunas_existem(cur) else ""
    cur.execute(f"""
        SELECT id, numero, status, manifesto_origem
          FROM embarques_cargas
         WHERE COALESCE(criada_por_robo, FALSE) = TRUE
           AND manifesto_origem IS NOT NULL
           AND COALESCE(viagem_vazia, FALSE) = FALSE
           AND status <> 'Cancelada'
           AND data_carregamento BETWEEN %s AND %s{lig}
    """, (piso, fim))
    cand = [r for r in cur.fetchall() if _norm(r[3]) not in vistos]
    if len(cand) > _teto():
        _logger.warning('reconciliação: %d manifestos sumiram de uma vez (teto %d) — '
                        'provável falha de extração, nada cancelado: %s',
                        len(cand), _teto(), ', '.join(r[1] for r in cand))
        n[f'sumiço em massa ({len(cand)} > teto {_teto()}) — ABORTADO'] += 1
        return n
    for cid, numero, status, man in cand:
        if _editada_a_mao(cur, cid):
            n['manifesto sumiu, carga editada à mão — não tocada'] += 1
            continue
        cur.execute("""UPDATE embarques_cargas
                          SET status='Cancelada', encerrada_motivo=%s, entregue_auto=FALSE,
                              atualizado_em=NOW()
                        WHERE id=%s""", (MOTIVO, cid))
        _log(cur, cid, 'status', status, f'Cancelada (robô: {MOTIVO})')
        _log(cur, cid, 'encerrada_motivo', None, MOTIVO)
        n['cancelada (manifesto sumiu)'] += 1
        _logger.info('reconciliação: %s cancelada — manifesto %s sumiu do relatório', numero, man)
    return n


def reabrir_retornados(cur, manifestos):
    """O manifesto que 'sumiu' voltou: o cancelamento era falha de extração. Volta ao status
    anterior, que o log guarda — convergência, não incremento."""
    from embarques_auto import _norm, _log
    n = Counter()
    vistos = _vistos(manifestos)
    cur.execute("""
        SELECT c.id, c.numero, c.manifesto_origem,
               (SELECT l.valor_anterior FROM embarques_cargas_log l
                 WHERE l.carga_id = c.id AND l.campo = 'status' AND l.valor_novo = %s
                 ORDER BY l.editado_em DESC LIMIT 1)
          FROM embarques_cargas c
         WHERE c.status = 'Cancelada' AND c.encerrada_motivo = %s
           AND COALESCE(c.criada_por_robo, FALSE) = TRUE
    """, (f'Cancelada (robô: {MOTIVO})', MOTIVO))
    for cid, numero, man, anterior in cur.fetchall():
        if _norm(man) not in vistos:
            continue
        anterior = anterior or 'Aberta'
        cur.execute("""UPDATE embarques_cargas
                          SET status=%s, encerrada_motivo=NULL, atualizado_em=NOW()
                        WHERE id=%s""", (anterior, cid))
        _log(cur, cid, 'status', 'Cancelada', f'{anterior} (robô: manifesto voltou ao relatório)')
        _log(cur, cid, 'encerrada_motivo', MOTIVO, None)
        n[f'reaberta (manifesto voltou) → {anterior}'] += 1
        _logger.info('reconciliação: %s reaberta — manifesto %s voltou', numero, man)
    return n


def executar(cur, manifestos, ini, fim):
    """Uma passada. Devolve Counter para o resumo do diário."""
    n = Counter()
    n.update(reabrir_retornados(cur, manifestos))
    n.update(cancelar_sumidos(cur, manifestos, ini, fim))
    return n
