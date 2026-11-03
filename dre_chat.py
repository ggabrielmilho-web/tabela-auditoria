# -*- coding: utf-8 -*-
"""Ferramentas do chat da DRE: abrir a conta na base (03/11/2026, pedido do diretor).

O chat só via o total de cada subgrupo que a tela carregou, e respondia "essa informação não está
no período" quando o diretor perguntava o que subiu no Administrativo de setembro para outubro.
Mandar o 477 linha a linha para a IA é caro e ela erra a conta. Aqui a conta é feita em Python e
a IA só escolhe a pergunta (function calling) e escreve a resposta com o resultado pronto:

- `comparar_despesas`: dois períodos, aberto por subgrupo, evento, fornecedor ou lançamento,
  ordenado pelo que mais mudou;
- `listar_lancamentos`: os lançamentos de um período (fornecedor, histórico, nº, inclusão);
- `comparar_receita`: a receita de dois períodos por cliente pagador.

A régua é a da DRE: o 477 pela competência (`REF`), o evento como o DRE o enxerga (o carro de sócio
vira Retirada — `_dax_evento_dre` do server) e só o que está no `MAPA_DRE` (o que a DRE descarta,
a ferramenta descarta). A receita é `valor_frete` por `data_autorizacao`, mês inteiro.

Cada resultado traz `avisos` quando o período ainda pode mudar: o mês corrente, mês futuro (só
provisão e parcela) e o mês anterior até o dia 10 — o financeiro lança provisão em valor redondo e
troca pelo real quando ele chega, e o 477 só fecha por volta do dia 10.

Este módulo não fala Flask nem OpenAI: recebe a função de consulta ao BI e o mapa do server.
"""

import difflib
import re
import threading
import time
import unicodedata
from calendar import monthrange
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

BRT = timezone(timedelta(hours=-3))
D477 = "'public consulta_despesas_477'"
CTE = "'public conhecimentos_emitidos'"
MAX_MESES_LADO = 12
_CACHE_TTL = 600
_cache = {}
_cache_trava = threading.Lock()

GRUPOS = ['Deduções', 'Operacional', 'Administrativo', 'Financeiro', 'Impostos', 'Investimento', 'Retirada']
QUEBRAS = ['subgrupo', 'evento', 'fornecedor', 'lancamento']

_PERIODO = {'type': 'array', 'items': {'type': 'string', 'pattern': r'^\d{4}-\d{2}$'},
            'description': 'Meses no formato YYYY-MM. Mais de um mês = soma dos meses.'}
_FILTROS = {
    'grupo': {'type': 'string', 'enum': GRUPOS, 'description': 'Grupo da DRE.'},
    'subgrupo': {'type': 'string', 'description': 'Subgrupo da DRE (ex.: Encargos, Sistemas, Mão de Obra).'},
    'evento': {'type': 'string', 'description': 'Evento do SSW, como aparece na DRE (ex.: INSS, '
                                                 'DESPESAS PJ - APOIO ADMINISTRATIVO E COMERCIAL).'},
    'fornecedor': {'type': 'string', 'description': 'Trecho do nome do fornecedor.'},
}

TOOLS = [
    {'type': 'function', 'function': {
        'name': 'comparar_despesas',
        'description': 'Compara as despesas do 477 entre dois períodos, na régua da DRE, e devolve o total '
                       'de cada lado e as linhas que mais mudaram (já com diferença e variação calculadas). '
                       'Use para "o que subiu/caiu", "o que teve em outubro que não teve em setembro", '
                       '"abra a conta". Para abrir uma conta, filtre pelo evento e quebre por fornecedor '
                       'ou lançamento.',
        'parameters': {'type': 'object', 'properties': {
            'periodo_a': {**_PERIODO, 'description': 'Período base (o anterior). ' + _PERIODO['description']},
            'periodo_b': {**_PERIODO, 'description': 'Período comparado (o mais recente). ' + _PERIODO['description']},
            'quebra': {'type': 'string', 'enum': QUEBRAS,
                       'description': 'Como abrir: subgrupo, evento, fornecedor ou lancamento (nº do SSW).'},
            **_FILTROS,
            'limite': {'type': 'integer', 'description': 'Quantas linhas devolver (padrão 15, máx. 40).'},
        }, 'required': ['periodo_a', 'periodo_b', 'quebra']},
    }},
    {'type': 'function', 'function': {
        'name': 'listar_lancamentos',
        'description': 'Lista os lançamentos do 477 de um período (fornecedor, histórico, nº do lançamento, '
                       'data de inclusão, valor), na régua da DRE, ou a soma por fornecedor/evento '
                       '(agrupar). Use para ver o detalhe de uma conta num mês só.',
        'parameters': {'type': 'object', 'properties': {
            'periodo': _PERIODO,
            **_FILTROS,
            'agrupar': {'type': 'string', 'enum': ['lancamento', 'fornecedor', 'evento'],
                        'description': 'lancamento (padrão) = um por linha; fornecedor/evento = soma por '
                                       'fornecedor ou evento ("quem são os maiores fornecedores").'},
            'ordem': {'type': 'string', 'enum': ['valor', 'inclusao'],
                      'description': 'valor = maiores primeiro; inclusao = lançados por último primeiro.'},
            'limite': {'type': 'integer', 'description': 'Quantas linhas devolver (padrão 20, máx. 50).'},
        }, 'required': ['periodo']},
    }},
    {'type': 'function', 'function': {
        'name': 'comparar_receita',
        'description': 'Compara a receita bruta (frete dos CT-e pela data de autorização, mesma régua da '
                       'DRE) entre dois períodos, por cliente pagador. Use para "por que a receita caiu", '
                       '"qual cliente cresceu".',
        'parameters': {'type': 'object', 'properties': {
            'periodo_a': {**_PERIODO, 'description': 'Período base (o anterior). ' + _PERIODO['description']},
            'periodo_b': {**_PERIODO, 'description': 'Período comparado. ' + _PERIODO['description']},
            'cliente': {'type': 'string', 'description': 'Trecho do nome do cliente pagador (opcional).'},
            'limite': {'type': 'integer', 'description': 'Quantas linhas devolver (padrão 15, máx. 40).'},
        }, 'required': ['periodo_a', 'periodo_b']},
    }},
]

STATUS = {
    'comparar_despesas': 'Comparando as despesas na base (477)…',
    'listar_lancamentos': 'Buscando os lançamentos no 477…',
    'comparar_receita': 'Comparando a receita por cliente…',
}


class ErroFerramenta(ValueError):
    """Pedido inválido: volta para a IA como {'erro': ...} para ela corrigir a chamada."""


def _sem_acento(s):
    return ''.join(c for c in unicodedata.normalize('NFD', str(s or '')) if unicodedata.category(c) != 'Mn').upper()


def _r(v):
    return round(float(v or 0), 2)


def _brl(v):
    return f'{v:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')


def _var(a, b):
    return round((b - a) / abs(a) * 100, 1) if a else None


def _meses(lista, nome):
    if isinstance(lista, str):
        lista = [lista]
    if not lista:
        raise ErroFerramenta(f'{nome}: informe ao menos um mês (YYYY-MM)')
    saida = []
    for m in lista:
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', str(m).strip()):
            raise ErroFerramenta(f'{nome}: mês inválido {m!r} — use YYYY-MM')
        saida.append(str(m).strip())
    saida = sorted(set(saida))
    if len(saida) > MAX_MESES_LADO:
        raise ErroFerramenta(f'{nome}: no máximo {MAX_MESES_LADO} meses')
    return saida


def _limite(v, padrao, maximo):
    try:
        return max(1, min(int(v), maximo))
    except (TypeError, ValueError):
        return padrao


def avisos_periodo(meses, hoje):
    """O que ainda pode mudar no período: mês corrente, futuro, ou o anterior antes do dia 10."""
    corrente = hoje.strftime('%Y-%m')
    anterior = (hoje.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
    avisos = []
    for m in meses:
        if m > corrente:
            avisos.append(f'{m} é mês futuro: o 477 só tem provisões e parcelas lançadas, não é custo realizado.')
        elif m == corrente:
            avisos.append(f'{m} é o mês corrente: ainda recebe lançamentos, e a despesa tem provisão.')
        elif m == anterior and hoje.day <= 10:
            avisos.append(f'{m} ainda não fechou (o 477 fecha por volta do dia 10): os valores ainda podem '
                          f'mudar quando o financeiro trocar provisões pelo real.')
    return avisos


def _do_cache(chave, buscar):
    agora = time.time()
    with _cache_trava:
        item = _cache.get(chave)
        if item and agora - item[0] < _CACHE_TTL:
            return item[1]
    dado = buscar()
    with _cache_trava:
        _cache[chave] = (agora, dado)
        for k in [k for k, (ts, _) in _cache.items() if agora - ts >= _CACHE_TTL]:
            _cache.pop(k, None)
    return dado


def _por_mes(meses, buscar_mes):
    """Busca mês a mês (4 em paralelo); cada mês tem cache próprio, então set × out e depois
    set × out por fornecedor reaproveitam a mesma leitura."""
    with ThreadPoolExecutor(max_workers=4) as ex:
        return dict(zip(meses, ex.map(buscar_mes, meses)))


def _linhas_477(ctx, mes):
    """Lançamentos do 477 de uma competência, com o evento DO DRE. Só o que o MAPA_DRE classifica."""
    ref = mes.replace('-', '/')

    def buscar():
        rows = ctx['dax'](
            f'EVALUATE SELECTCOLUMNS(FILTER({D477}, {D477}[REF] = "{ref}"), '
            f'"ev", {ctx["evento_expr"]}, "forn", {D477}[nome_fornecedor], "nl", {D477}[numlancto], '
            f'"pc", {D477}[parcela], "hist", {D477}[historico_despesa], "v", {D477}[vlr_final], '
            f'"inc", {D477}[inclusao])')
        mapa = ctx['mapa']
        saida = []
        for r in rows:
            ev = r.get('ev')
            if ev not in mapa:
                continue
            saida.append({'mes': mes, 'evento': ev, 'grupo': mapa[ev][0], 'subgrupo': mapa[ev][1],
                          'fornecedor': (r.get('forn') or '').strip() or '(sem fornecedor)',
                          'lancamento': str(r.get('nl') or ''), 'parcela': str(r.get('pc') or ''),
                          'historico': (r.get('hist') or '').strip(), 'valor': float(r.get('v') or 0),
                          'inclusao': str(r.get('inc') or '')[:10]})
        return saida
    return _do_cache(('477', ref), buscar)


def _resolver_filtros(args, ctx):
    """Valida grupo/subgrupo/evento contra o MAPA_DRE e devolve a função que filtra a linha."""
    mapa = ctx['mapa']
    grupo, sub, ev, forn = (args.get(k) or None for k in ('grupo', 'subgrupo', 'evento', 'fornecedor'))
    if grupo and grupo not in GRUPOS:
        raise ErroFerramenta(f'grupo inválido {grupo!r}; use um de {GRUPOS}')
    if sub:
        subs = sorted({s for _, s in mapa.values()})
        achado = next((s for s in subs if _sem_acento(s) == _sem_acento(sub)), None)
        if achado:
            sub = achado
        elif not ev and any(_sem_acento(sub) in _sem_acento(e) for e in mapa):
            ev, sub = sub, None          # a IA mandou o evento no campo do subgrupo ("Despesas PJ - ...")
        else:
            raise ErroFerramenta(f'subgrupo {sub!r} não existe; use um de {subs}')
    if ev:
        achado = next((e for e in mapa if _sem_acento(e) == _sem_acento(ev)), None)
        if not achado:
            parecidos = [e for e in mapa if _sem_acento(ev) in _sem_acento(e)]
            if len(parecidos) == 1:
                achado = parecidos[0]
            else:
                parecidos = parecidos or difflib.get_close_matches(ev.upper(), list(mapa), n=5, cutoff=0.4)
                raise ErroFerramenta(f'evento {ev!r} não encontrado; parecidos: {parecidos}')
        ev = achado
        # o evento já diz o grupo e o subgrupo: um subgrupo errado junto zerava o resultado
        grupo = sub = None
    forn_n = _sem_acento(forn) if forn else None

    def passa(r):
        return ((not grupo or r['grupo'] == grupo) and (not sub or r['subgrupo'] == sub)
                and (not ev or r['evento'] == ev) and (not forn_n or forn_n in _sem_acento(r['fornecedor'])))
    filtro = {k: v for k, v in (('grupo', grupo), ('subgrupo', sub), ('evento', ev), ('fornecedor', forn)) if v}
    return passa, filtro


def _redondo(v):
    return abs(v) >= 1000 and round(v, 2) % 500 == 0


def _parece_provisao(a, b):
    """Redondo no período B e quebrado (real) no A, no mesmo lançamento recorrente: o financeiro ainda não
    trocou a provisão pelo real. Redondo sozinho NÃO é provisão: contrato fixo também é redondo todo mês."""
    return _redondo(b) and abs(a) >= 0.005 and not _redondo(a) and abs(b - a) >= 0.005


def _provisao(hist):
    h = _sem_acento(hist)
    return 'PROVIS' in h or 'PREVIS' in h


def comparar_despesas(args, ctx):
    pa, pb = _meses(args.get('periodo_a'), 'periodo_a'), _meses(args.get('periodo_b'), 'periodo_b')
    quebra = args.get('quebra') or 'evento'
    if quebra not in QUEBRAS:
        raise ErroFerramenta(f'quebra inválida {quebra!r}; use {QUEBRAS}')
    limite = _limite(args.get('limite'), 15, 40)
    passa, filtro = _resolver_filtros(args, ctx)
    lidos = _por_mes(sorted(set(pa) | set(pb)), lambda m: _linhas_477(ctx, m))

    itens = defaultdict(lambda: {'a': 0.0, 'b': 0.0, 'info': {}})
    for lado, meses in (('a', pa), ('b', pb)):
        for m in meses:
            for r in lidos[m]:
                if not passa(r):
                    continue
                if quebra == 'lancamento':
                    chave = r['lancamento']
                elif quebra == 'subgrupo':
                    chave = (r['grupo'], r['subgrupo'])
                else:
                    chave = r[quebra]
                it = itens[chave]
                it[lado] += r['valor']
                info = it['info']
                if quebra == 'evento':
                    info.update(grupo=r['grupo'], subgrupo=r['subgrupo'])
                elif quebra == 'lancamento':
                    # o que descreve o lançamento é o lado mais recente; parcelas dos dois lados
                    if lado == 'b' or not info:
                        info.update(fornecedor=r['fornecedor'], evento=r['evento'], historico=r['historico'][:120],
                                    inclusao=r['inclusao'])
                    info.setdefault('parcelas', set()).add(f"{r['mes']}:{r['parcela']}")
                    if _provisao(r['historico']):
                        info['provisao'] = True

    total_a = sum(i['a'] for i in itens.values())
    total_b = sum(i['b'] for i in itens.values())
    linhas = []
    for chave, it in itens.items():
        a, b = it['a'], it['b']
        if abs(a) < 0.005 and abs(b) < 0.005:
            continue
        if quebra == 'subgrupo':
            linha = {'subgrupo': chave[1], 'grupo': chave[0]}
        elif quebra == 'lancamento':
            linha = {'lancamento': chave}
        else:
            linha = {quebra: chave}
        info = dict(it['info'])
        if 'parcelas' in info:
            info['parcelas'] = ', '.join(sorted(info['parcelas']))
        linha.update(info)
        linha.update(a=_r(a), b=_r(b), diferenca=_r(b - a), variacao_pct=_var(a, b))
        if abs(a) < 0.005:
            linha['novo_no_periodo_b'] = True
        elif abs(b) < 0.005:
            linha['sumiu_no_periodo_b'] = True
        # só dentro da mesma conta: o fornecedor somado no grupo mistura eventos diferentes
        mesma_conta = quebra in ('evento', 'lancamento') or (quebra == 'fornecedor' and filtro.get('evento'))
        if mesma_conta and _parece_provisao(a, b):
            linha['parece_provisao'] = True
        linhas.append(linha)
    linhas.sort(key=lambda l: -abs(l['diferenca']))
    mostradas, resto = linhas[:limite], linhas[limite:]

    # A resposta a "o que subiu no grupo" já vem com a conta aberta: os 3 itens que mais mudaram
    # trazem quem mudou dentro deles (evento → fornecedor; subgrupo → evento), sem outra rodada
    filho = {'evento': 'fornecedor', 'subgrupo': 'evento'}.get(quebra)
    if filho:
        for linha in mostradas[:3]:
            if quebra == 'evento':
                dentro = lambda r, l=linha: r['evento'] == l['evento']
            else:
                dentro = lambda r, l=linha: r['grupo'] == l['grupo'] and r['subgrupo'] == l['subgrupo']
            sub = defaultdict(lambda: [0.0, 0.0])
            for lado, meses in ((0, pa), (1, pb)):
                for m in meses:
                    for r in lidos[m]:
                        if passa(r) and dentro(r):
                            sub[r[filho]][lado] += r['valor']
            abertura = []
            for k, (a, b) in sorted(sub.items(), key=lambda x: -abs(x[1][1] - x[1][0]))[:5]:
                if abs(b - a) < 0.005:
                    continue
                item = {filho: k, 'a': _r(a), 'b': _r(b), 'diferenca': _r(b - a)}
                if abs(a) < 0.005:
                    item['novo_no_periodo_b'] = True
                elif abs(b) < 0.005:
                    item['sumiu_no_periodo_b'] = True
                if quebra == 'evento' and _parece_provisao(a, b):
                    item['parece_provisao'] = True
                abertura.append(item)
            if abertura:
                linha[f'abertura_por_{filho}'] = abertura

    avisos = avisos_periodo(sorted(set(pa) | set(pb)), ctx['hoje'])
    if avisos_periodo(pb, ctx['hoje']):
        # provável provisão num período que ainda muda: a IA ignorava a marca, então vai escrito
        for linha in mostradas:
            nome = linha.get(quebra) or linha.get('fornecedor') or linha.get('subgrupo')
            candidatos = [(nome, linha)] + [(f"{nome} / {x.get('fornecedor') or x.get('evento')}", x)
                                           for x in linha.get('abertura_por_fornecedor', [])]
            for rotulo, x in candidatos:
                if x.get('parece_provisao'):
                    avisos.append(f"{rotulo}: R$ {_brl(x['b'])} redondo no período B contra R$ {_brl(x['a'])} no A, "
                                  f"num mês que ainda não fechou — provável provisão do financeiro, a trocar "
                                  f"pelo real.")
    if not linhas:
        avisos.append('Nenhum lançamento com esse filtro nos dois períodos: não houve despesa — não especule.')
    if len(pa) != len(pb):
        avisos.append(f'Períodos de tamanhos diferentes ({len(pa)} × {len(pb)} meses): compare com cuidado.')
    saida = {
        'periodo_a': pa, 'periodo_b': pb, 'filtro': filtro, 'quebra': quebra,
        'total_a': _r(total_a), 'total_b': _r(total_b), 'diferenca': _r(total_b - total_a),
        'variacao_pct': _var(total_a, total_b), 'qtd_linhas': len(linhas), 'linhas': mostradas,
    }
    if resto:
        ra, rb = sum(l['a'] for l in resto), sum(l['b'] for l in resto)
        saida['demais'] = {'qtd': len(resto), 'a': _r(ra), 'b': _r(rb), 'diferenca': _r(rb - ra)}
    if avisos:
        saida['avisos'] = avisos
    return saida


def listar_lancamentos(args, ctx):
    periodo = _meses(args.get('periodo'), 'periodo')
    limite = _limite(args.get('limite'), 20, 50)
    passa, filtro = _resolver_filtros(args, ctx)
    lidos = _por_mes(periodo, lambda m: _linhas_477(ctx, m))
    rows = [r for m in periodo for r in lidos[m] if passa(r)]
    agrupar = args.get('agrupar') if args.get('agrupar') in ('fornecedor', 'evento') else None
    if agrupar:
        soma = defaultdict(lambda: [0.0, 0])
        for r in rows:
            soma[r[agrupar]][0] += r['valor']
            soma[r[agrupar]][1] += 1
        grupos = sorted(soma.items(), key=lambda x: -abs(x[1][0]))
        saida = {'periodo': periodo, 'filtro': filtro, 'agrupado_por': agrupar,
                 'total': _r(sum(r['valor'] for r in rows)), f'qtd_{agrupar}': len(grupos),
                 'qtd_lancamentos': len(rows),
                 'linhas': [{agrupar: k, 'valor': _r(v), 'lancamentos': n} for k, (v, n) in grupos[:limite]]}
        resto = grupos[limite:]
        if resto:
            saida['demais'] = {f'qtd_{agrupar}': len(resto), 'valor': _r(sum(v for _, (v, _) in resto))}
        avisos = avisos_periodo(periodo, ctx['hoje'])
        if not rows:
            avisos.append('Nenhum lançamento com esse filtro no período: não houve despesa — não especule.')
        if avisos:
            saida['avisos'] = avisos
        return saida
    if args.get('ordem') == 'inclusao':
        rows.sort(key=lambda r: (r['inclusao'], r['valor']), reverse=True)
    else:
        rows.sort(key=lambda r: -abs(r['valor']))
    linhas = []
    for r in rows[:limite]:
        l = {'mes': r['mes'], 'lancamento': f"{r['lancamento']}/{r['parcela']}", 'fornecedor': r['fornecedor'],
             'evento': r['evento'], 'historico': r['historico'][:120], 'valor': _r(r['valor']),
             'inclusao': r['inclusao']}
        if _provisao(r['historico']):
            l['provisao'] = True
        linhas.append(l)
    saida = {'periodo': periodo, 'filtro': filtro, 'total': _r(sum(r['valor'] for r in rows)),
             'qtd_lancamentos': len(rows), 'lancamentos': linhas}
    if len(rows) > limite:
        resto = rows[limite:]
        saida['demais'] = {'qtd': len(resto), 'valor': _r(sum(r['valor'] for r in resto))}
    avisos = avisos_periodo(periodo, ctx['hoje'])
    if not rows:
        avisos.append('Nenhum lançamento com esse filtro no período: não houve despesa — não especule.')
    if avisos:
        saida['avisos'] = avisos
    return saida


def _receita_mes(ctx, mes):
    y, m = map(int, mes.split('-'))

    def buscar():
        rows = ctx['dax'](
            f'EVALUATE GROUPBY(SELECTCOLUMNS(FILTER({CTE}, {CTE}[data_autorizacao] >= DATE({y},{m},1) && '
            f'{CTE}[data_autorizacao] <= DATE({y},{m},{monthrange(y, m)[1]})), '
            f'"cli", {CTE}[cliente_pagador], "v", {CTE}[valor_frete]), '
            f'[cli], "v", SUMX(CURRENTGROUP(), [v]), "n", COUNTX(CURRENTGROUP(), 1))')
        return [{'cliente': (r.get('cli') or '').strip() or '(sem pagador)', 'valor': float(r.get('v') or 0),
                 'ctes': int(r.get('n') or 0)} for r in rows]
    return _do_cache(('receita', mes), buscar)


def comparar_receita(args, ctx):
    pa, pb = _meses(args.get('periodo_a'), 'periodo_a'), _meses(args.get('periodo_b'), 'periodo_b')
    limite = _limite(args.get('limite'), 15, 40)
    cli_n = _sem_acento(args.get('cliente')) if args.get('cliente') else None
    lidos = _por_mes(sorted(set(pa) | set(pb)), lambda m: _receita_mes(ctx, m))
    itens = defaultdict(lambda: {'a': 0.0, 'b': 0.0, 'ctes_a': 0, 'ctes_b': 0})
    for lado, meses in (('a', pa), ('b', pb)):
        for m in meses:
            for r in lidos[m]:
                if cli_n and cli_n not in _sem_acento(r['cliente']):
                    continue
                itens[r['cliente']][lado] += r['valor']
                itens[r['cliente']]['ctes_' + lado] += r['ctes']
    linhas = []
    for cli, it in itens.items():
        a, b = it['a'], it['b']
        l = {'cliente': cli, 'a': _r(a), 'b': _r(b), 'diferenca': _r(b - a), 'variacao_pct': _var(a, b),
             'ctes_a': it['ctes_a'], 'ctes_b': it['ctes_b']}
        if abs(a) < 0.005:
            l['novo_no_periodo_b'] = True
        elif abs(b) < 0.005:
            l['sumiu_no_periodo_b'] = True
        linhas.append(l)
    linhas.sort(key=lambda l: -abs(l['diferenca']))
    total_a, total_b = sum(i['a'] for i in itens.values()), sum(i['b'] for i in itens.values())
    saida = {'periodo_a': pa, 'periodo_b': pb, 'filtro': {'cliente': args['cliente']} if cli_n else {},
             'total_a': _r(total_a), 'total_b': _r(total_b), 'diferenca': _r(total_b - total_a),
             'variacao_pct': _var(total_a, total_b), 'qtd_clientes': len(linhas), 'linhas': linhas[:limite]}
    resto = linhas[limite:]
    if resto:
        ra, rb = sum(l['a'] for l in resto), sum(l['b'] for l in resto)
        saida['demais'] = {'qtd': len(resto), 'a': _r(ra), 'b': _r(rb), 'diferenca': _r(rb - ra)}
    corrente = ctx['hoje'].strftime('%Y-%m')
    if corrente in pa or corrente in pb:
        saida['avisos'] = [f'{corrente} é o mês corrente: a receita ainda está entrando.']
    return saida


_FERRAMENTAS = {'comparar_despesas': comparar_despesas, 'listar_lancamentos': listar_lancamentos,
                'comparar_receita': comparar_receita}


def executar(nome, args, ctx):
    """Roda a ferramenta pedida pela IA. Erro de pedido volta como {'erro'} para ela corrigir."""
    f = _FERRAMENTAS.get(nome)
    if not f:
        return {'erro': f'ferramenta desconhecida {nome!r}'}
    try:
        return f(args if isinstance(args, dict) else {}, ctx)
    except ErroFerramenta as e:
        return {'erro': str(e)}


def hoje_brt():
    return datetime.now(BRT).date()
