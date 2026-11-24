# -*- coding: utf-8 -*-
"""Vale-pedágio pago pelo cliente não é custo da Rizza (24/11/2026).

O `frete_motorista_total` da Auditoria Receita = valor a pagar + vale-pedágio + pedágio + conta corrente.
Quando o vale é a TAG do cliente, ele aparece no CTRB mas não sai do caixa: contar como custo derruba o
resultado da viagem.

A Rizza pagou o vale quando há QUALQUER um dos dois:
  - despesa do vale no próprio CTRB (`ctrbs_oss[despesa_vale_pedagio]`) — só vem preenchida a partir de
    set/26, mas sai na emissão, sem esperar o 477;
  - lançamento 5403 no 477 citando o CTRB ('PAGAMENTO DO VALE-PEDAGIO CTRB XXX  nnnn-d') — a régua da aba
    Comissões; cobre o histórico, mas chega dias depois da emissão.
Em set–nov/26 as duas concordaram em praticamente todos os CTRBs. Sem nenhuma das duas = TAG do cliente.
Frota fica fora: o pedágio dela entra pelo Sem Parar da placa (aba Veículos), não pelo CTRB.

Não fala Flask nem abre conexão: recebe `dax(query) -> linhas` e devolve conjuntos/expressões DAX.
"""
import re
import threading
import time

OSS = "'public ctrbs_oss'"
DZ = "'public consulta_despesas_477'"
AR_PADRAO = "'Auditoria Receita'"
TTL_S = 600

_RE_CTRB = re.compile(r'CTRB\s+([A-Z]{3})\s*(\d{1,6})-?(\d)')
_cache = {}
_trava = threading.Lock()


def _lista(vals):
    return '{' + ','.join('"' + str(v).replace('"', '') + '"' for v in vals) + '}'


def _pagos_5403(dax):
    """CTRBs com lançamento 5403 no 477 (cache de 10 min: é a mesma lista para todas as telas)."""
    with _trava:
        hit = _cache.get('5403')
        if hit and time.time() - hit[0] < TTL_S:
            return hit[1]
    pagos = set()
    for r in dax(f'EVALUATE SELECTCOLUMNS(FILTER({DZ}, {DZ}[evento] = "5403" && '
                 f'SEARCH("CTRB", {DZ}[historico_despesa], 1, 0) > 0), "h", {DZ}[historico_despesa])'):
        m = _RE_CTRB.search(str(r.get('h') or '').upper())
        if m:
            pagos.add(f'{m.group(1)}{int(m.group(2)):06d}-{m.group(3)}')
    with _trava:
        _cache['5403'] = (time.time(), pagos)
    return pagos


def pagos_pela_rizza(dax, ctrbs):
    """Dos CTRBs pedidos, os que tiveram o vale pago pela Rizza (despesa no CTRB ou 5403 no 477)."""
    ctrbs = sorted({str(c) for c in ctrbs if c})
    if not ctrbs:
        return set()
    pagos = _pagos_5403(dax) & set(ctrbs)
    for i in range(0, len(ctrbs), 300):
        for r in dax(f'EVALUATE SELECTCOLUMNS(FILTER({OSS}, {OSS}[ctrb] IN {_lista(ctrbs[i:i + 300])}), '
                     f'"ctrb", {OSS}[ctrb], "dv", {OSS}[despesa_vale_pedagio])'):
            if str(r.get('dv') or '').strip():
                pagos.add(str(r.get('ctrb')))
    return pagos


def ctrbs_vale_cliente(dax, filtro_ar, AR=AR_PADRAO):
    """CTRBs (não-frota) do recorte `filtro_ar` (expressão DAX sobre a Auditoria) cujo vale é do cliente."""
    rows = dax(f'EVALUATE SELECTCOLUMNS(FILTER({AR}, ({filtro_ar}) && {AR}[vale_pedagio] > 0 && '
               f'{AR}[Tipo Operacao] <> "FROTA"), "ctrb", {AR}[CTRB])')
    ctrbs = {str(r.get('ctrb')) for r in rows if r.get('ctrb')}
    return sorted(ctrbs - pagos_pela_rizza(dax, ctrbs))


def dax_vale_cliente(lista, AR=AR_PADRAO):
    """R$ do vale do cliente na linha (0 se a Rizza pagou). `lista` = saída de ctrbs_vale_cliente."""
    if not lista:
        return '0'
    return f'IF({AR}[CTRB] IN {_lista(lista)}, COALESCE({AR}[vale_pedagio], 0), 0)'


def dax_vale_rizza(lista, AR=AR_PADRAO):
    """R$ do vale que a Rizza pagou (custo de verdade)."""
    return f'(COALESCE({AR}[vale_pedagio], 0) - {dax_vale_cliente(lista, AR)})'


def dax_frete_custo(lista, AR=AR_PADRAO):
    """Frete ao motorista que é custo da Rizza: `frete_motorista_total` sem o vale do cliente."""
    return f'(COALESCE({AR}[frete_motorista_total], 0) - {dax_vale_cliente(lista, AR)})'


def ajustar_linhas_auditoria(dax, rows):
    """Linhas cruas da Auditoria Receita (tela principal): tira o vale do cliente do frete e refaz
    resultado/margem. Acrescenta `vale_pedagio_cliente` (R$) e `vale_pedagio_pago_por`."""
    com_vale = [r for r in rows if float(r.get('vale_pedagio') or 0) > 0
                and str(r.get('Tipo Operacao') or '').upper() != 'FROTA']
    pagos = pagos_pela_rizza(dax, [r.get('CTRB') for r in com_vale])
    for r in com_vale:
        if str(r.get('CTRB')) in pagos:
            r['vale_pedagio_pago_por'] = 'Rizza'
            continue
        v = float(r.get('vale_pedagio') or 0)
        r['vale_pedagio_pago_por'] = 'Cliente'
        r['vale_pedagio_cliente'] = round(v, 2)
        r['frete_motorista_total'] = round(float(r.get('frete_motorista_total') or 0) - v, 2)
        rec = float(r.get('receita_rateada') or 0)
        r['resultado'] = round(float(r.get('resultado') or 0) + v, 2)
        r['margem'] = (r['resultado'] / rec) if rec else r.get('margem')
    return rows
