# -*- coding: utf-8 -*-
"""
Gera o payload da Verda para manifestos reais e imprime o JSON.

Uso:  python -X utf8 _verda_demo.py [manifesto ...] [--n 2]
      sem argumento, escolhe casos representativos (frota, terceiro, multi-entrega)
      --n N sorteia N manifestos aleatórios além dos representativos
"""

import json
import random
import re
import sys
from collections import defaultdict

from server import get_token, execute_dax, clean_rows
from placas import mercosul
import verda_payload as vp
import verda_veiculos as vv

CNPJ_RIZZA = '02572512000158'

M = "'public manifestos'"
MC = "'public manifestos_ctrc'"
CE = "'public conhecimentos_emitidos'"
V = "'public veiculos_045'"
VC = "'public abastecimentos_valecard'"
AR = "'Auditoria Receita'"   # tabela TRATADA do Power BI: trecho e km do CTRB


LIMITE_PBI = 100000   # executeQueries corta aqui e devolve 200 OK, sem avisar


def _dax(token, q):
    res = execute_dax(token, q)
    linhas = clean_rows(res.get('results', [{}])[0].get('tables', [{}])[0].get('rows', []))
    if len(linhas) >= LIMITE_PBI:
        raise SystemExit('!! a consulta bateu no teto de %d linhas do Power BI e foi truncada '
                         'em silêncio. Filtre a tabela antes de trazer:\n%s' % (LIMITE_PBI, q[:200]))
    return linhas


def carregar(token):
    d = {}
    d['cadastro'] = {}
    for r in _dax(token, 'EVALUATE SELECTCOLUMNS(%s, "placa", %s[placa], "tipo", %s[tipo], '
                         '"capacidade", %s[capacidade], "modelo", %s[modelo], "ano", %s[ano], '
                         '"cnpj", %s[cnpj], "relacionamento", %s[relacionamento], '
                         '"proprietario", %s[proprietario])' % (V, V, V, V, V, V, V, V, V)):
        d['cadastro'][mercosul(r.get('placa'))] = {
            'tipo': r.get('tipo'), 'capacidade': r.get('capacidade'),
            'modelo': r.get('modelo'), 'ano': r.get('ano'), 'cnpj': r.get('cnpj'),
            'relacionamento': r.get('relacionamento'), 'proprietario': r.get('proprietario')}

    d['viagens'] = _dax(token, 'EVALUATE SELECTCOLUMNS(%s, "sigla", %s[sigla_manifesto], '
                               '"numero", %s[numero_manifesto], "data_emissao", %s[data_emissao], '
                               '"placa_cavalo", %s[placa_cavalo], "placa_carreta", %s[placa_carreta], '
                               '"proprietario_cavalo", %s[proprietario_cavalo], '
                               '"cpf_motorista", %s[cpf_motorista], "peso_total", %s[peso_total])'
                               % (M, M, M, M, M, M, M, M, M))
    d['observado'] = vv.observado_por_placa(
        [{'placa_carreta': v['placa_carreta'], 'peso_total': v['peso_total']} for v in d['viagens']])

    # manifestos_ctrc tem o histórico inteiro (392 mil linhas) e estoura o teto do
    # Power BI. Recorta para os manifestos da janela — 9 mil linhas.
    d['ctrc'] = defaultdict(list)
    for r in _dax(token, 'EVALUATE SELECTCOLUMNS(FILTER(%s, %s[CHAVE_MANIFESTO] IN VALUES(%s[CHAVE_MANIFESTO])), '
                         '"sm", %s[sigla_manifesto], "nm", %s[numero_manifesto], '
                         '"sc", %s[sigla_ctrc], "nc", %s[numero_ctrc], "peso", %s[peso])'
                         % (MC, MC, M, MC, MC, MC, MC, MC)):
        d['ctrc'][(r['sm'], r['nm'])].append(r)

    d['ce'] = {}
    for r in _dax(token, 'EVALUATE SELECTCOLUMNS(%s, "k", %s[serie_numero_ctrc], '
                         '"rem", %s[cnpj_remetente], "dest", %s[cnpj_destinatario], '
                         '"cid", %s[cidade_entrega], "uf", %s[uf_entrega], '
                         '"cid_o", %s[cidade_origem_prestacao], "uf_o", %s[uf_origem_prestacao], '
                         '"peso", %s[peso_real_kg], "nf", %s[numero_nota_fiscal], '
                         '"km", %s[distancia_km], "pag", %s[cnpj_pagador])'
                         % (CE, CE, CE, CE, CE, CE, CE, CE, CE, CE, CE, CE)):
        d['ce'][(r['k'] or '').strip()] = r

    # ── Auditoria Receita: o TRECHO que cada veiculo rodou ──
    # O CTe descreve o percurso COMERCIAL (Jundiai > Brasilia); quem descreve o
    # trecho rodado por aquele veiculo e o CTRB (Jundiai > Uberlandia). Usar o
    # CTe fazia cada manifesto de um transbordo declarar o percurso inteiro.
    #
    # `distancia_km` aqui vem 100% do CTRB (conferido: 110 de 110 identicos em
    # origem, destino e km contra `ctrbs_oss`). E o documento que PAGA O
    # MOTORISTA, entao tem consequencia financeira e e auditado por natureza.
    #
    # So existe no Power BI: e dado tratado, nao ha equivalente no Postgres.
    d['auditoria'] = {}
    for r in _dax(token, 'EVALUATE SELECTCOLUMNS(%s, "m", %s[Manifesto], "ctrb", %s[CTRB], '
                         '"o", %s[cidade_uf_origem], "d", %s[cidade_uf_destino], '
                         '"km", %s[distancia_km], "tipo", %s[Tipo Operacao], '
                         '"dt", %s[data_ref_ctrc])'
                         % (AR, AR, AR, AR, AR, AR, AR, AR)):
        if r.get('m'):
            d['auditoria'][r['m']] = r


    d['consumo'] = vv.abastecido_por_placa(
        {'placa': r['p'], 'litros': r['l'], 'hodometro': r['hod'],
         'data': r['dt'], 'produto': r['prod']}
        for r in _dax(token, 'EVALUATE SELECTCOLUMNS(%s, "p", %s[placa], "prod", %s[produto], '
                             '"l", %s[ncd_quantidade], "hod", %s[nsd_hodometro], '
                             '"dt", %s[dch_data])' % (VC, VC, VC, VC, VC, VC)))
    return d




def preparar(viagem, dados):
    """Traduz o manifesto e seus CTes para o formato que `verda_payload.montar` espera.

    A distância vem do CTRB (`dados['auditoria']`), não do CTe: é o trecho que
    aquele veículo rodou. Uma parada só — a Auditoria Receita trata a viagem como
    origem → destino direto, sem paradas intermediárias.

    Devolve (viagem, itens, auditoria) ou (None, None, None) quando não há CTRB:
    sem ele não há trecho, e sem trecho a viagem não sobe.
    """
    chave = (viagem['sigla'], viagem['numero'])
    tid = '%s%s' % chave
    aud = dados['auditoria'].get(tid)
    if not aud or aud.get('km') in (None, '') or float(aud['km']) <= 0:
        return None, None, None

    itens = []
    for c in dados['ctrc'].get(chave, []):
        ce = dados['ce'].get('%s%s' % ((c['sc'] or '').strip(), (c['nc'] or '').strip())) or {}
        itens.append({
            'delivery_id': '%s%s' % ((c['sc'] or '').strip(), (c['nc'] or '').strip()),
            'item_id': str(ce.get('nf') or '') or None,
            # embarcador = quem PAGA o frete (tomador), nao quem remete: e ele
            # que reporta esta emissao no escopo 3 dele. Em 17% dos CTes o
            # pagador nao e o remetente (frete FOB ou terceiro contratante).
            'cnpj_embarcador': ce.get('pag') or ce.get('rem'),
            'cnpj_remetente': ce.get('rem'), 'cnpj_destinatario': ce.get('dest'),
            'peso_kg': vv.numero(c.get('peso')) or vv.numero(ce.get('peso')),
            'contrato': None, 'servico': None,
        })

    v = dict(viagem)
    v['transportation_id'] = tid
    v['km_trecho'] = float(aud['km'])
    v['rota'] = '%s > %s' % (aud.get('o') or '?', aud.get('d') or '?')
    v['frota_propria'] = (aud.get('tipo') == 'FROTA')
    v['data_ref'] = str(aud.get('dt') or '')[:10]
    return v, itens, aud


def escolher(dados, n_aleatorios):
    """Casos representativos + N sorteados."""
    viagens = [v for v in dados['viagens'] if dados['ctrc'].get((v['sigla'], v['numero']))]
    frota = [v for v in viagens if (dados['auditoria'].get('%s%s' % (v['sigla'], v['numero'])) or {}).get('tipo') == 'FROTA']
    terceiro = [v for v in viagens if (dados['auditoria'].get('%s%s' % (v['sigla'], v['numero'])) or {}).get('tipo') != 'FROTA']
    multi = [v for v in viagens if len(dados['ctrc'][(v['sigla'], v['numero'])]) > 2]

    escolhidos, vistos = [], set()
    for rotulo, pool in (('FROTA PRÓPRIA (espera Fuel)', frota),
                         ('TERCEIRO (espera Weight)', terceiro),
                         ('MULTI-ENTREGA', multi)):
        for v in pool:
            if (v['sigla'], v['numero']) not in vistos:
                escolhidos.append((rotulo, v)); vistos.add((v['sigla'], v['numero'])); break

    random.seed()
    sorteio = [v for v in viagens if (v['sigla'], v['numero']) not in vistos]
    for v in random.sample(sorteio, min(n_aleatorios, len(sorteio))):
        escolhidos.append(('ALEATÓRIO', v))
    return escolhidos


def main():
    args = sys.argv[1:]
    n = int(args[args.index('--n') + 1]) if '--n' in args else 2
    pedidos = [a for a in args if not a.startswith('--') and not a.isdigit()]

    token = get_token()
    dados = carregar(token)

    if pedidos:
        alvos = [('PEDIDO %s' % p, v) for p in pedidos
                 for v in dados['viagens'] if v['numero'] == p or
                 '%s%s' % (v['sigla'], v['numero']) == p]
    else:
        alvos = escolher(dados, n)

    for rotulo, viagem in alvos:
        v, itens, aud = preparar(viagem, dados)
        if v is None:
            print('=' * 78)
            print('%s  —  manifesto %s%s NAO TEM CTRB na Auditoria Receita (nao sobe)'
                  % (rotulo, viagem['sigla'], viagem['numero']))
            continue
        api, payload, avisos = vp.montar(v, itens, dados['cadastro'], dados['observado'],
                                         CNPJ_RIZZA)
        print('=' * 78)
        print('%s  —  manifesto %s  |  %s  |  %d entrega(s)  |  API: %s'
              % (rotulo, v['transportation_id'],
                 (viagem['proprietario_cavalo'] or '?')[:34], len(itens), api))
        print('=' * 78)
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        if avisos:
            print('\n  AVISOS:')
            for a in dict.fromkeys(avisos):
                print('   - %s' % a)
        print()


if __name__ == '__main__':
    main()
