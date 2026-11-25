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

25/11/2026 — FONTE NOVA: o relatório 200 (`manifestos_200.py`) traz o cancelado com motivo e usuário.
Com o 200 CONFIÁVEL na rodada (cobre o 916), quem cancela é ele:
    manifesto CANCELADO no 200 → `Cancelada`, com o motivo e quem cancelou no log
    (campo `cancelamento_ssw`). Sem a janela do mês (fecha a brecha da virada: manifesto do
    dia 31 cancelado no dia 1) e com teto próprio (`EMBARQUES_RECONCILIACAO_TETO_200`, 15);
    manifesto que SUMIU do 916 mas o 200 não diz cancelado → NÃO cancela: vira aviso
    ("sumiu sem cancelamento no 200") — é falha de extração, não cancelamento.
200 fora (robô falhou, tabela vazia, BI fora, `MANIFESTOS_200=false`) → a regra antiga do sumiço,
com as mesmas guardas de sempre.
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


def _teto_200():
    try:
        return int(os.getenv('EMBARQUES_RECONCILIACAO_TETO_200', '15'))
    except ValueError:
        return 15


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


def _cancelar(cur, cid, numero, status, man, n, rotulo, chave_n):
    from embarques_auto import _log
    cur.execute("""UPDATE embarques_cargas
                      SET status='Cancelada', encerrada_motivo=%s, entregue_auto=FALSE,
                          atualizado_em=NOW()
                    WHERE id=%s""", (MOTIVO, cid))
    # o texto do status fica IGUAL ao da regra antiga: é por ele que `reabrir_retornados` acha o anterior
    _log(cur, cid, 'status', status, f'Cancelada (robô: {MOTIVO})')
    _log(cur, cid, 'encerrada_motivo', None, MOTIVO)
    if rotulo:
        _log(cur, cid, 'cancelamento_ssw', None, rotulo)
    n[chave_n] += 1
    _logger.info('reconciliação: %s cancelada — manifesto %s (%s)', numero, man, rotulo or 'sumiu do relatório')


def cancelar_confirmados(cur, manifestos, cancelados, ini, fim):
    """Com o 200 confiável: cancela a carga do robô cujo manifesto está CANCELADO no 200; o que só
    sumiu do 916 vira aviso. Sem janela de data para o confirmado (o 200 é fato, não ausência)."""
    from embarques_auto import _norm
    import embarques_continuacao as _ec
    import manifestos_200 as _m2
    n = Counter()
    vistos = _vistos(manifestos)
    lig = " AND continua_em IS NULL" if _ec.colunas_existem(cur) else ""
    cur.execute(f"""
        SELECT id, numero, status, manifesto_origem, data_carregamento
          FROM embarques_cargas
         WHERE COALESCE(criada_por_robo, FALSE) = TRUE
           AND manifesto_origem IS NOT NULL
           AND COALESCE(viagem_vazia, FALSE) = FALSE
           AND status <> 'Cancelada'{lig}
    """)
    linhas = cur.fetchall()
    conf = [r for r in linhas if _norm(r[3]) in cancelados and _norm(r[3]) not in vistos]
    if len(conf) > _teto_200():
        _logger.warning('reconciliação (200): %d cargas de manifesto cancelado de uma vez (teto %d) — '
                        'nada cancelado, conferir: %s', len(conf), _teto_200(), ', '.join(r[1] for r in conf))
        n[f'cancelados no 200 em massa ({len(conf)} > teto {_teto_200()}) — ABORTADO'] += 1
        return n
    for cid, numero, status, man, _d in conf:
        if _editada_a_mao(cur, cid):
            n['cancelado no 200, carga editada à mão — não tocada'] += 1
            continue
        _cancelar(cur, cid, numero, status, man, n, _m2.rotulo(cancelados[_norm(man)]),
                  'cancelada (cancelado no SSW, relatório 200)')
    # o sumiço sem confirmação: só a janela que o 916 recarrega, como a regra antiga
    piso = max(ini, date(fim.year, fim.month, 1))
    sumidos = [r for r in linhas if r[4] is not None and piso <= r[4] <= fim
               and _norm(r[3]) not in vistos and _norm(r[3]) not in cancelados]
    if vistos:
        for _cid, numero, _st, man, _d in sumidos:
            n['sumiu do 916 sem cancelamento no 200 — não cancelada'] += 1
            _logger.warning('reconciliação (200): %s — manifesto %s sumiu do 916 mas o 200 não diz '
                            'cancelado; nada feito (falha de extração?)', numero, man)
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


def executar(cur, manifestos, ini, fim, m200=None):
    """Uma passada. Devolve Counter para o resumo do diário.

    `m200` = `manifestos_200.ler(...)`. Confiável → o cancelamento vem do 200; senão (ou None) → sumiço."""
    n = Counter()
    n.update(reabrir_retornados(cur, manifestos))
    if m200 and m200.get('confiavel'):
        n['fonte: relatório 200'] += 1
        n.update(cancelar_confirmados(cur, manifestos, m200.get('cancelados') or {}, ini, fim))
    else:
        if m200 is not None:
            n[f"fonte: sumiço (200 fora: {m200.get('motivo') or '?'})"] += 1
        n.update(cancelar_sumidos(cur, manifestos, ini, fim))
    return n
