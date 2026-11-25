# -*- coding: utf-8 -*-
"""Manifestos CANCELADOS no SSW, lidos do relatório 200 (Relação de Manifestos Operacionais, 25/11/2026).

O 916 (`'public manifestos'`) não tem cancelado: o manifesto cancelado simplesmente SOME. Até aqui quem
precisava saber disso (reconciliação do Embarques, conformidade de SM, Canhotos) inferia pelo sumiço.
O 200 (`'public manifestos_200'`, robô `ssw_relatorio 200.py` na mesma rodada do 916) traz o cancelado
com `situacao_manifesto = 'CANCELADO'`, o MOTIVO digitado e o USUÁRIO que cancelou.

Medido no BI em 25/11/2026 (26/10–25/11): 613 manifestos = os 577 do 916 + 36 CANCELADO; nenhum
cancelado está no 916; todos os do 916 desde 26/10 estão no 200. O cancelado vem SEM CTRB e SEM CIOT
(o SSW desfaz o vínculo). O 200 só tem dado desde 25/10/2026 e recarrega a janela do 455 (dia 1º até
hoje; do dia 1 ao 10, os últimos 10 dias).

CONFIÁVEL = o 200 cobre o 916 da rodada. Se o robô do 200 falhou (tabela vazia, parada num dia antigo),
os manifestos novos do 916 não estão nele: `ler()` devolve confiavel=False e quem chama volta à regra
antiga (sumiço). Nunca se cancela nada só porque o 200 não respondeu.

Não fala Flask. A consulta usa a mesma checagem de truncamento do Embarques.
"""
import logging
import os
import re
from datetime import date, datetime

_logger = logging.getLogger('manifestos_200')

T = "'public manifestos_200'"
COBERTURA_MIN = 0.98     # fração dos manifestos do 916 (desde o 1º dia do 200) que tem de estar no 200


def ligado():
    """`MANIFESTOS_200` (padrão ligado): false = todo mundo volta a inferir pelo sumiço."""
    return str(os.getenv('MANIFESTOS_200', 'true')).strip().lower() in ('1', 'true', 'sim', 'yes')


def norm(s):
    """'UDI 000123-1' = 'UDI000123-1' (a mesma régua do `embarques_auto._norm`)."""
    return re.sub(r'[^A-Za-z0-9]', '', str(s or '')).upper()


def _dia(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.fromisoformat(str(v)[:10]).date()
    except (TypeError, ValueError):
        return None


def _dax(token, query):
    from embarques_auto import _dax as dax
    return dax(token, query)


def consulta(desde):
    """O DAX: linhas do 200 com emissão >= `desde` — manifesto, emissão, situação, motivo, usuário."""
    d = f'DATE({desde.year},{desde.month},{desde.day})'
    return (f"EVALUATE SELECTCOLUMNS(FILTER({T}, {T}[emissao] >= {d}), "
            f"\"m\", {T}[num_manif], \"e\", {T}[emissao], \"s\", {T}[situacao_manifesto], "
            f"\"mot\", {T}[motivo], \"u\", {T}[usuario])")


def consultar(token, desde, dax=None):
    """`dax(q)` (a função de quem chama, ex.: a do Canhotos) ou o `_dax` do Embarques com o token."""
    q = consulta(desde)
    return dax(q) if dax else _dax(token, q)


def montar(linhas, chaves_916):
    """Puro (testável): das linhas do 200 e das chaves do 916 da MESMA janela, o retrato da rodada.

    `chaves_916` = {chave_normalizada: data_emissao}. Devolve dict:
      confiavel  bool — o 200 cobre o 916 (>= COBERTURA_MIN dos manifestos desde o 1º dia do 200)
      motivo     por que não é confiável ('' quando é)
      cancelados {chave: {'manifesto', 'emissao', 'motivo', 'usuario'}}
      situacao   {chave: situação} de todos (para quem quiser 'EM TRANSITO'/'CHEGOU')
      cobertura  fração medida, desde_200 (1º dia do 200)
    """
    situacao, cancelados, dias = {}, {}, []
    for r in linhas or []:
        k = norm(r.get('m'))
        if not k:
            continue
        s = str(r.get('s') or '').strip().upper()
        situacao[k] = s
        e = _dia(r.get('e'))
        if e:
            dias.append(e)
        if s == 'CANCELADO':
            cancelados[k] = {'manifesto': str(r.get('m') or '').strip(), 'emissao': e,
                             'motivo': (str(r.get('mot') or '').strip() or None),
                             'usuario': (str(r.get('u') or '').strip() or None)}
    out = {'confiavel': False, 'motivo': '', 'cancelados': cancelados, 'situacao': situacao,
           'cobertura': None, 'desde_200': min(dias) if dias else None}
    if not situacao:
        out['motivo'] = '200 vazio na janela'
        return out
    piso = out['desde_200']
    alvo = [k for k, e in (chaves_916 or {}).items() if e is None or e >= piso]
    if not alvo:
        # nada do 916 para comparar (janela inteira antes do 200, ou 916 vazio): não dá para atestar
        out['motivo'] = '916 sem manifesto na janela do 200'
        return out
    tem = sum(1 for k in alvo if k in situacao)
    out['cobertura'] = tem / len(alvo)
    conflito = [k for k in alvo if k in cancelados]       # cancelado no 200 e vivo no 916
    if out['cobertura'] < COBERTURA_MIN:
        out['motivo'] = (f'200 cobre só {tem} de {len(alvo)} manifestos do 916 '
                         f'({out["cobertura"]:.0%}) — robô do 200 atrasado ou falhou')
        return out
    for k in conflito:                                    # o 916 manda: manifesto vivo não é cancelado
        cancelados.pop(k, None)
        _logger.warning('manifestos_200: %s CANCELADO no 200 mas presente no 916 — ignorado', k)
    out['conflitos'] = len(conflito)
    out['confiavel'] = True
    return out


def ler(token, chaves_916, desde, dax=None):
    """Consulta o BI e monta o retrato. Erro na consulta (tabela fora do modelo, BI fora) = não
    confiável, nunca exceção: quem chama cai na regra antiga."""
    if not ligado():
        return {'confiavel': False, 'motivo': 'MANIFESTOS_200 desligado', 'cancelados': {}, 'situacao': {},
                'cobertura': None, 'desde_200': None}
    try:
        linhas = consultar(token, desde, dax)
    except Exception as e:                                # noqa: BLE001 — qualquer falha = regra antiga
        _logger.warning('manifestos_200: consulta falhou (%s) — volta à regra do sumiço', e)
        return {'confiavel': False, 'motivo': f'consulta ao 200 falhou: {str(e)[:120]}', 'cancelados': {},
                'situacao': {}, 'cobertura': None, 'desde_200': None}
    r = montar(linhas, chaves_916)
    if not r['confiavel']:
        _logger.warning('manifestos_200: não confiável nesta rodada — %s', r['motivo'])
    return r


def chaves_do_916(manifestos, campo_chave='CHAVE_MANIFESTO', campo_data='data_emissao'):
    """{chave: data} a partir das linhas do 916 que o chamador já leu."""
    return {norm(m.get(campo_chave)): _dia(m.get(campo_data)) for m in manifestos or [] if m.get(campo_chave)}


def rotulo(c):
    """'ERRO NO NOME DO MOTORISTA (fulano)' — o texto que vai para log e tela."""
    if not c:
        return None
    mot, u = c.get('motivo'), c.get('usuario')
    return ((mot or 'sem motivo') + (f' ({u})' if u else ''))[:250]
