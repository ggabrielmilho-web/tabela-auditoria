# -*- coding: utf-8 -*-
"""Programação — a ORDEM DE COLETA (SSW 157) como registro próprio, fora do painel de cargas.

Decisão do Gabriel (15/09/26): a coleta não entra no painel de cargas (viraria lixo), mas o dado
tem de ser preservado para medir programado × realizado × tempo × tipo × embarcador. Então cada
ordem vira uma linha em `embarques_programacao`, alimentada pela fita a cada refresh do BI, com os
instantes que o SSW sobrescreve.

O ESTADO é DERIVADO, não o do SSW: a `situacao` do 157 não fecha sozinha — só a filial de Uberlândia
marca COLETADA (andre/stenio), a CAR nunca marca; em 15/09 havia 89 ordens COMANDADAS com limite
vencido, inclusive uma que já era manifesto do dia anterior. O que fecha uma ordem é o documento:

    cancelada              cancelada_em preenchido
    carga (C-xxx)          existe carga do robô com coleta_origem = esta ordem
    documento emitido      ctrc_gerado preenchido (CTe existe; a carga nasce no diário seguinte)
    aguardando manifesto   COMANDADA com veículo, limite ainda não venceu
    vencida sem documento  COMANDADA/CADASTRADA, limite venceu, nada emitido
    sem veículo            CADASTRADA / PRE-CADAST (ainda sem conjunto)

Nada aqui é lido por régua nenhuma do app (raio, rota, KPI, status de carga).
"""
from datetime import datetime

DDL = """
CREATE TABLE IF NOT EXISTS embarques_programacao (
    coleta_origem   VARCHAR(20) PRIMARY KEY,      -- unidade-numero
    unidade         VARCHAR(5), numero VARCHAR(10), tipo VARCHAR(20),
    situacao_ssw    VARCHAR(20), situacao_em TIMESTAMP, limite_em TIMESTAMP,
    cadastrada_em   TIMESTAMP, cadastrada_por VARCHAR(40),
    comandada_em    TIMESTAMP, comandada_por VARCHAR(40),
    coletada_em     TIMESTAMP, coletada_por VARCHAR(40),
    cancelada_em    TIMESTAMP, cancelada_por VARCHAR(40),
    solicitante     VARCHAR(60), motorista VARCHAR(80),
    cavalo          VARCHAR(8), carreta VARCHAR(8),
    reme_cnpj       VARCHAR(14), reme_nome VARCHAR(160), reme_endereco VARCHAR(200), reme_cep VARCHAR(9), reme_cidade VARCHAR(80),
    dest_cnpj       VARCHAR(14), dest_nome VARCHAR(160), dest_cidade VARCHAR(80), dest_uf VARCHAR(2),
    ctrc_gerado     VARCHAR(20), manifesto VARCHAR(20),
    carga_id        INTEGER, carga_numero VARCHAR(20), carga_status VARCHAR(20), carga_via VARCHAR(10),
    embarcador      VARCHAR(40),
    estado          VARCHAR(24),
    primeira_vez    TIMESTAMP NOT NULL DEFAULT NOW(),
    ultima_vez      TIMESTAMP NOT NULL DEFAULT NOW(),
    sumiu_em        TIMESTAMP
);
ALTER TABLE embarques_programacao ADD COLUMN IF NOT EXISTS carga_via VARCHAR(10);
CREATE INDEX IF NOT EXISTS ix_prog_limite ON embarques_programacao (limite_em);
CREATE INDEX IF NOT EXISTS ix_prog_estado ON embarques_programacao (estado);
"""


def _dt(v):
    try:
        return datetime.fromisoformat(str(v)[:19]) if v else None
    except (TypeError, ValueError):
        return None


def _s(v, n):
    v = str(v or '').strip()
    return v[:n] or None


def derivar_estado(r, agora):
    if r.get('cancelada_em'):
        return 'cancelada'
    if r.get('carga_id'):
        return 'carga'
    if r.get('ctrc_gerado'):
        return 'documento emitido'
    sit = str(r.get('situacao_ssw') or '')
    lim = r.get('limite_em')
    if sit == 'COMANDADA':
        return 'vencida sem documento' if (lim and lim < agora) else 'aguardando manifesto'
    if lim and lim < agora:
        return 'vencida sem documento'
    return 'sem veículo'


def atualizar(conn, dados):
    """`dados` = dict da fita (coleta + cte). Upsert por ordem; liga manifesto (via CTe) e carga
    (via embarques_cargas.coleta_origem); marca `sumiu_em` na ordem que saiu do relatório."""
    import placas as pl
    from _locais import cnpj14
    from embarques_auto import _norm
    cur = conn.cursor()
    cur.execute(DDL)
    cte = {str(r.get('serie_numero_ctrc') or '').strip(): r for r in dados.get('cte', {}).values()}
    cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name='embarques_cargas' AND column_name='coleta_origem'")
    tem_col = cur.fetchone() is not None
    cargas = {}
    if tem_col:
        cur.execute("SELECT coleta_origem, id, numero, status, coleta_via, manifesto_origem FROM embarques_cargas WHERE coleta_origem IS NOT NULL")
        cargas = {r[0]: r[1:] for r in cur.fetchall()}
    # N ordens -> 1 documento (a filial junta duas ordens num CTRC): a carga tambem se resolve pelo
    # manifesto, senao so a primeira ordem que casou mostra a carga (UDI-164/UDI-182, 15/09/26)
    cur.execute("SELECT manifesto_origem, id, numero, status FROM embarques_cargas WHERE manifesto_origem IS NOT NULL AND COALESCE(viagem_vazia, FALSE) = FALSE")
    por_man = {_norm(r[0]): r[1:] for r in cur.fetchall()}
    agora = datetime.now()
    vistas = set()
    n = 0
    for r in dados.get('coleta', {}).values():
        k = f"{r.get('unidade')}-{r.get('numero')}"[:20]
        vistas.add(k)
        ctrc = _s(r.get('ctrc_gerado'), 20)
        cg = cargas.get(k)
        # manifesto: pelo CTe da coleta; sem CTe, o da carga ligada (ligacao por placa)
        man = _norm(cte[ctrc].get('primeiro_manifesto')) if ctrc and ctrc in cte else (cg[4] if cg and cg[4] else None)
        if not cg and man and man in por_man:
            cid_, num_, st_ = por_man[man]
            cg = (cid_, num_, st_, 'manifesto', man)
        row = {
            'coleta_origem': k, 'unidade': _s(r.get('unidade'), 5), 'numero': _s(r.get('numero'), 10), 'tipo': _s(r.get('tipo'), 20),
            'situacao_ssw': _s(r.get('situacao'), 20), 'situacao_em': _dt(r.get('situacao_em')), 'limite_em': _dt(r.get('limite_em')),
            'cadastrada_em': _dt(r.get('cadastrada_em')), 'cadastrada_por': _s(r.get('cadastrada_por'), 40),
            'comandada_em': _dt(r.get('comandada_em')), 'comandada_por': _s(r.get('comandada_por'), 40),
            'coletada_em': _dt(r.get('coletada_em')), 'coletada_por': _s(r.get('coletada_por'), 40),
            'cancelada_em': _dt(r.get('cancelada_em')), 'cancelada_por': _s(r.get('cancelada_por'), 40),
            'solicitante': _s(r.get('solicitante'), 60), 'motorista': _s(r.get('motorista'), 80),
            'cavalo': pl.mercosul(str(r.get('veiculo') or '')) or None, 'carreta': pl.mercosul(str(r.get('veiculo_2') or '')) or None,
            'reme_cnpj': cnpj14(r.get('reme_cnpj')), 'reme_nome': _s(r.get('reme_nome'), 160), 'reme_endereco': _s(r.get('reme_endereco'), 200),
            'reme_cep': _s(r.get('reme_cep'), 9), 'reme_cidade': _s(r.get('reme_cidade'), 80),
            'dest_cnpj': cnpj14(r.get('dest_cnpj')), 'dest_nome': _s(r.get('dest_nome'), 160), 'dest_cidade': _s(r.get('dest_cidade'), 80), 'dest_uf': _s(r.get('dest_uf'), 2),
            'ctrc_gerado': ctrc, 'manifesto': man,
            'carga_id': cg[0] if cg else None, 'carga_numero': cg[1] if cg else None, 'carga_status': cg[2] if cg else None,
            'carga_via': cg[3] if cg else None,
            'embarcador': _s(r.get('comandada_por') or r.get('cadastrada_por'), 40),
        }
        row['estado'] = derivar_estado(row, agora)
        cols = list(row.keys())
        sets = ', '.join(f'{c}=EXCLUDED.{c}' for c in cols if c != 'coleta_origem')
        cur.execute(f"INSERT INTO embarques_programacao ({', '.join(cols)}) VALUES ({', '.join(['%s']*len(cols))}) "
                    f"ON CONFLICT (coleta_origem) DO UPDATE SET {sets}, ultima_vez=NOW(), sumiu_em=NULL",
                    [row[c] for c in cols])
        n += 1
    # ordem que estava e não veio mais: o 157 só guarda ~14 dias, então "sumiu" = saiu da janela
    # OU foi apagada. Marca o instante e preserva a linha — é o histórico que o SSW não tem.
    if vistas:
        cur.execute("UPDATE embarques_programacao SET sumiu_em = COALESCE(sumiu_em, NOW()) WHERE NOT (coleta_origem = ANY(%s))", (list(vistas),))
    conn.commit()
    cur.execute("SELECT estado, count(*) FROM embarques_programacao WHERE sumiu_em IS NULL GROUP BY 1 ORDER BY 2 DESC")
    return n, cur.fetchall()
