# -*- coding: utf-8 -*-
"""Manifestos sem ordem de coleta — pedido do gerente (06/10/26), aba `/embarques/sem-ordem`.

A pergunta é de PROCESSO: todo manifesto deveria nascer de uma ordem de coleta (SSW 157)
comandada pelo embarcador. Decisões do Gabriel: Terceiro entra, todos precisam de ordem,
a partir de 01/09/2026 (quando os embarcadores passaram a usar o 157).

A ligação manifesto ↔ ordem MORA AQUI (`ligar`) e é usada também pela aba Coletas
(`_programacao`) e pelo robô (`embarques_coleta`), para as três nunca discordarem (§20.6 — uma
régua). Uma ordem cobre um manifesto só (07/10/26):

    ordem_cte     um CTe do manifesto (primeiro_manifesto = ele) é o `ctrc_gerado` de uma
                  ordem — exata
    ordem_placa   ordem com o MESMO cavalo, carreta compatível (as duas presentes e
                  diferentes = não é), comandada/cadastrada de 3 dias antes a 1 dia depois
                  da emissão — reserva. Sem ela, CAR (que quase nunca executa a coleta pela
                  ordem, então o CTe não aponta para ela) sairia inteira como "sem ordem"
    continuacao   o manifesto só aparece como `ultimo_manifesto` dos CTes: é a 2ª perna de um
                  desengate/continuação; a ordem é da 1ª perna. Fica fora da lista
    sem_ordem     o resto — inclusive manifesto sem CTe nenhum

Só lê o Power BI (5 consultas) e o banco local (o nº da carga do painel). Nada é gravado.
O resultado fica em memória e só é refeito quando o BI carrega algo novo.
"""
import os
import threading
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

DESDE = os.getenv('SEM_ORDEM_DESDE', '2026-09-01')

M = "'public manifestos'"
CE = "'public conhecimentos_emitidos'"
CO = "'public coletas_0157'"
OS_ = "'public ctrbs_oss'"
CE_COLS = ('serie_numero_ctrc', 'primeiro_manifesto', 'ultimo_manifesto', 'tipo_documento', 'cliente_pagador')

_cache = {'marcador': None, 'dados': None, 'em': None, 'ligacoes': None}
_lock = threading.Lock()


def _dt(v):
    try:
        return datetime.fromisoformat(str(v)[:19]) if v else None
    except (TypeError, ValueError):
        return None


def _d(x):
    return f'DATE({x.year},{x.month},{x.day})'


def carregar(token, desde):
    """Tudo que a classificação precisa, do BI. CTe e CTRB começam 10 dias antes: o CTe de um
    manifesto de 01/09 pode ter sido emitido em 30/08."""
    import embarques_auto as e
    antes = desde - timedelta(days=10)
    man = e._dax(token, f"EVALUATE FILTER({M}, {M}[data_emissao] >= {_d(desde)})")
    ctes = e._dax(token, f"EVALUATE SELECTCOLUMNS(FILTER({CE}, {CE}[data_emissao] >= {_d(antes)}), "
                         + ', '.join(f'"{c}",{CE}[{c}]' for c in CE_COLS) + ')')
    coletas = e._dax(token, f"EVALUATE {CO}")
    ctrbs = e._dax(token, f"EVALUATE SELECTCOLUMNS(FILTER({OS_}, {OS_}[emissao] >= {_d(antes)}), "
                          f"\"ctrb\",{OS_}[ctrb], \"emissao\",{OS_}[emissao], \"usuario\",{OS_}[usuario_emissao], "
                          f"\"origem\",{OS_}[cidade_uf_origem], \"destino\",{OS_}[cidade_uf_destino])")
    cadastro = e.carregar_cadastro(token)
    return man, ctes, coletas, ctrbs, cadastro


def chave_ordem(o):
    return f"{o.get('unidade')}-{o.get('numero')}"[:20]


def ligar(man, ctes, coletas, ctrbs=()):
    """A RÉGUA ÚNICA manifesto ↔ ordem de coleta (07/10/26) — a aba Sem ordem, a aba Coletas
    (`_programacao`) e o robô (`embarques_coleta`) usam esta função, para as três nunca
    discordarem. Função pura.

        1) pelo CTe   o CTe do manifesto (primeiro_manifesto = ele) é o `ctrc_gerado` da ordem — exata
        2) pela placa mesmo cavalo, carreta compatível, ordem comandada/cadastrada de 3 dias antes a
                      1 dia depois da emissão, ordem não cancelada — reserva

    UMA ORDEM COBRE UM MANIFESTO SÓ. Até 07/10/26 a placa pegava a primeira ordem do cavalo que
    coubesse na janela, já usada ou não: 33 manifestos "cobertos" por uma ordem que já era de outro
    manifesto pelo CTe, e 13 dividindo a mesma ordem pela placa. Agora o CTe reserva primeiro e a
    placa só usa ordem livre; havendo mais de uma, vence a mais próxima no tempo (a hora vem da
    emissão do CTRB — o manifesto só tem data).

    Devolve (lig, concorrentes): lig = {manifesto_norm: (ordem, 'cte'|'placa')}; concorrentes =
    {ordem_livre: (ordem_que_levou, manifesto)} — ordem aberta do mesmo cavalo que disputou um
    manifesto e perdeu para outra: provável ordem em duplicidade."""
    import embarques_auto as e
    import placas as pl
    por_pm = defaultdict(list)
    for c in ctes:
        por_pm[e._norm(c.get('primeiro_manifesto'))].append(c)
    ordem_do_ctrc = {str(c.get('ctrc_gerado') or '').strip(): c for c in coletas if c.get('ctrc_gerado')}
    ordens_do_cavalo = defaultdict(list)
    for c in coletas:
        p = pl.mercosul(str(c.get('veiculo') or '').strip())
        if p and not c.get('cancelada_em'):
            ordens_do_cavalo[p].append(c)
    ctrb_em = {e._chave_ctrb(r.get('ctrb')): _dt(r.get('emissao')) for r in ctrbs if e._chave_ctrb(r.get('ctrb'))}

    lig, usadas = {}, set()
    for m in man:                                          # 1) pelo CTe
        k = e._norm(m.get('CHAVE_MANIFESTO'))
        if not k:
            continue
        for c in por_pm.get(k, []):
            o = ordem_do_ctrc.get(str(c.get('serie_numero_ctrc') or '').strip())
            if o and chave_ordem(o) not in usadas:
                lig[k] = (o, 'cte')
                usadas.add(chave_ordem(o))
                break

    cand = []                                              # 2) pela placa: todos os pares possíveis
    for m in man:
        k = e._norm(m.get('CHAVE_MANIFESTO'))
        emissao = _dt(m.get('data_emissao'))
        cav = pl.mercosul(str(m.get('placa_cavalo') or ''))
        car = pl.mercosul(str(m.get('placa_carreta') or ''))
        if not k or k in lig or not cav or not emissao:
            continue
        ref = ctrb_em.get(str(m.get('CHAVE_CTRB') or '')) or emissao
        for o in ordens_do_cavalo.get(cav, []):
            t = _dt(o.get('comandada_em')) or _dt(o.get('cadastrada_em'))
            v2 = pl.mercosul(str(o.get('veiculo_2') or ''))
            if not t or not (emissao.date() - timedelta(days=3) <= t.date() <= emissao.date() + timedelta(days=1)):
                continue
            if car and v2 and v2 != car:
                continue
            cand.append((abs((t - ref).total_seconds()), chave_ordem(o), k, o))
    cand.sort(key=lambda x: (x[0], x[1], x[2]))
    for _dist, ko, k, o in cand:                           # o par mais próximo primeiro, 1 para 1
        if k in lig or ko in usadas:
            continue
        lig[k] = (o, 'placa')
        usadas.add(ko)

    concorrentes = {}
    for _dist, ko, k, o in cand:
        if ko not in usadas and ko not in concorrentes and k in lig and not o.get('ctrc_gerado'):
            concorrentes[ko] = (chave_ordem(lig[k][0]), k)
    return lig, concorrentes


def classificar(man, ctes, coletas, ctrbs, cadastro, vendidas=frozenset(), hoje=None, lig=None):
    """Função pura: uma linha por manifesto, com `situacao` e o que a tela mostra."""
    import embarques_auto as e
    import placas as pl
    por_pm, por_um = defaultdict(list), defaultdict(list)
    for c in ctes:
        por_pm[e._norm(c.get('primeiro_manifesto'))].append(c)
        por_um[e._norm(c.get('ultimo_manifesto'))].append(c)
    ctrb_por = {e._chave_ctrb(r.get('ctrb')): r for r in ctrbs if e._chave_ctrb(r.get('ctrb'))}
    if lig is None:
        lig, _ = ligar(man, ctes, coletas, ctrbs)

    out = []
    for m in man:
        chave = m.get('CHAVE_MANIFESTO')
        if not chave:
            continue
        k = e._norm(chave)
        emissao = _dt(m.get('data_emissao'))
        cav_raw, car_raw = m.get('placa_cavalo'), m.get('placa_carreta')
        pms, ums = por_pm.get(k, []), por_um.get(k, [])

        ordem, via = lig.get(k, (None, None))
        if ordem:
            situacao = 'ordem_cte' if via == 'cte' else 'ordem_placa'
        else:
            situacao = 'continuacao' if (not pms and ums) else 'sem_ordem'

        cli = Counter(str(c.get('cliente_pagador') or '').strip() for c in (pms or ums) if c.get('cliente_pagador'))
        ctrb = ctrb_por.get(str(m.get('CHAVE_CTRB') or ''))
        out.append({
            'manifesto': chave, 'emissao': emissao.date().isoformat() if emissao else None,
            'unidade': m.get('unidade_origem'), 'unidade_destino': m.get('unidade_destino'),
            'tipo': e.classificar(cav_raw, car_raw, cadastro, vendidas) if cadastro else None,
            'cavalo': cav_raw, 'carreta': car_raw, 'motorista': m.get('nome_motorista'),
            'cliente': cli.most_common(1)[0][0] if cli else None,
            'n_cte': len(pms) or len(ums),
            'subcontratacao': any('SUBC' in str(c.get('tipo_documento') or '') for c in pms + ums),
            'ctrb': m.get('CHAVE_CTRB') if str(m.get('numero_ctrb_os') or '000000') != '000000' else None,
            'ctrb_emissao': ctrb.get('emissao') if ctrb else None,      # hora de Brasília, sem fuso
            'ctrb_por': ctrb.get('usuario') if ctrb else None,
            'origem': ctrb.get('origem') if ctrb else None, 'destino': ctrb.get('destino') if ctrb else None,
            'situacao': situacao,
            'ordem': f"{ordem.get('unidade')}-{ordem.get('numero')}" if ordem else None,
            'ordem_embarcador': (ordem.get('comandada_por') or ordem.get('cadastrada_por')) if ordem else None,
            'hoje': bool(hoje and emissao and emissao.date() == hoje),
        })
    return out


def _cargas_do_painel(conn, linhas):
    """manifesto → (id, numero, status) da carga do robô. Normaliza dos dois lados: o
    `manifesto_origem` é gravado com hífen e o BI às vezes traz espaço (§24.5)."""
    cur = conn.cursor()
    cur.execute("SELECT regexp_replace(upper(manifesto_origem), '[^A-Z0-9]', '', 'g'), id, numero, status "
                "FROM embarques_cargas WHERE manifesto_origem IS NOT NULL "
                "AND COALESCE(viagem_vazia, FALSE) = FALSE")
    por = {r[0]: r[1:] for r in cur.fetchall()}
    import embarques_auto as e
    for l in linhas:
        c = por.get(e._norm(l['manifesto']))
        l['carga_id'], l['carga_numero'], l['carga_status'] = (c if c else (None, None, None))


def _calcular(token, forcar=False):
    """Refaz o cálculo só quando o BI carregou algo novo (o marcador da fita, ~1 s); senão devolve
    o que já está em memória. Serve a aba e as `ligacoes` da aba Coletas e do robô."""
    import _fita_documentos as fita
    import embarques_auto as e
    with _lock:
        marc = fita.marcador(token)
        if forcar or _cache['dados'] is None or marc != _cache['marcador']:
            desde = date.fromisoformat(DESDE)
            man, ctes, coletas, ctrbs, cadastro = carregar(token, desde)
            try:
                vendidas = e._config()['vendidas']
            except Exception:
                vendidas = frozenset()
            hoje = (datetime.utcnow() - timedelta(hours=3)).date()
            lig, conc = ligar(man, ctes, coletas, ctrbs)
            linhas = classificar(man, ctes, coletas, ctrbs, cadastro, vendidas, hoje=hoje, lig=lig)
            _cache.update(marcador=marc, dados=linhas, em=datetime.utcnow(),
                          ligacoes={'por_manifesto': {k: (chave_ordem(o), via) for k, (o, via) in lig.items()},
                                    'por_ordem': {chave_ordem(o): (m, via) for m, (o, via) in lig.items()},
                                    'concorrentes': conc})
        return marc, [dict(l) for l in _cache['dados']], _cache['ligacoes']


def ligacoes(token):
    """A régua única pronta para quem grava: `por_ordem` {ordem: (manifesto_norm, via)},
    `por_manifesto` {manifesto_norm: (ordem, via)} e `concorrentes` {ordem: (ordem_que_levou,
    manifesto_norm)}. Mesmo cálculo (e mesmo cache) da aba Sem ordem."""
    return _calcular(token)[2]


def montar(token, conn, forcar=False):
    """O resultado pronto para a tela. Pergunta ao BI se houve refresh (~1 s) e só refaz as
    5 consultas quando houve — o BI atualiza 8×/dia, a tela é aberta muito mais que isso."""
    marc, linhas, _lig = _calcular(token, forcar)
    _cargas_do_painel(conn, linhas)        # local e barato: sempre fresco
    return {'desde': DESDE, 'linhas': linhas,
            'bi_atualizado': marc.isoformat() if marc else None,   # data_importacao: hora de Brasília
            'calculado_em': _cache['em'].isoformat() + 'Z' if _cache['em'] else None}
