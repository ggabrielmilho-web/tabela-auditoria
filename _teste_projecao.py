# -*- coding: utf-8 -*-
"""Regressão do motor da Projeção (projecao.py) — sem rede nem banco.

    python -X utf8 _teste_projecao.py

O que mais importa aqui é a projeção continuar na RÉGUA DO DRE (o número que o
diretor valida): mesmas linhas, mesma cascata, mesmos grupos. Os casos 1 e 2 leem
o server.py para pegar divergência no dia em que alguém mexer num lado só.
"""
import math
import re

import projecao as pj

FALHAS = []


def caso(nome, cond, detalhe=''):
    print(('OK    ' if cond else 'FALHA ') + nome + (f' — {detalhe}' if detalhe and not cond else ''))
    if not cond:
        FALHAS.append(nome)


def _server():
    with open('server.py', encoding='utf-8') as f:
        return f.read()


# 1. As linhas da DRE são as mesmas do server.py
src = _server()
bloco = re.search(r'DRE_LINHAS = \[(.*?)\n\]', src, re.S).group(1)
linhas_srv = re.findall(r"\('([^']+)',\s*'(\w+)',\s*'(\w+)'\)", bloco)
caso('1. DRE_LINHAS igual ao server.py', [tuple(x) for x in linhas_srv] == [tuple(x) for x in pj.DRE_LINHAS])

# 2. Os grupos do MAPA_DRE são exatamente os que o motor projeta
i = src.index('MAPA_DRE = {')
ns = {}
exec(src[i:src.index('\n}\n', i) + 3], ns)
grupos_srv = {g for g, _ in ns['MAPA_DRE'].values()}
caso('2. grupos do MAPA_DRE = grupos do motor', grupos_srv == set(pj.GRUPOS), f'{grupos_srv ^ set(pj.GRUPOS)}')

# 3. A cascata faz a mesma conta do _calcular_dre_periodo
c = pj.cascata(1000.0, {'Deduções': 100, 'Operacional': 600, 'Administrativo': 100, 'Financeiro': 50,
                        'Impostos': 10, 'Investimento': 60, 'Retirada': 30})
caso('3. cascata', (c['receita_liquida'], c['ebitda'], c['lair'], c['lucro_liquido'], c['pos_investimento'],
                    c['resultado_final']) == (900, 200, 150, 140, 80, 50), str(c))
caso('3b. cascata tem todas as chaves da DRE', set(c) == {k for _, _, k in pj.DRE_LINHAS})

# 4. Calendário
caso('4. mes_add vira o ano', pj.mes_add('2026-11', 3) == '2027-02' and pj.mes_add('2026-01', -1) == '2025-12')
caso('4b. meses_entre', pj.meses_entre('2026-11', '2027-02') == ['2026-11', '2026-12', '2027-01', '2027-02'])

# 5. Holt-Winters recupera tendência + sazonalidade limpa
saz = [1.0, .9, 1.1, .95, 1.05, 1.0, .98, 1.02, 1.08, 1.12, 1.1, .9]
serie = [1e6 * (1.01 ** t) * saz[t % 12] for t in range(48)]
real = [1e6 * (1.01 ** t) * saz[t % 12] for t in range(48, 54)]
prev = pj.holt_winters(serie[-36:], 6)
err = max(abs(p / r - 1) for p, r in zip(prev, real))
caso('5. Holt-Winters em série sazonal limpa (erro < 3%)', err < 0.03, f'erro máx {err:.3f}')

# 6. naive sazonal: mesmo mês do ano passado × crescimento
y = [100.0] * 12 + [110.0] * 12
caso('6. naive sazonal', all(abs(v - 121.0) < 1e-9 for v in pj.naive_sazonal(y, 3)))

# 7. prever_receita exige 24 meses
try:
    pj.prever_receita([1.0] * 23)
    caso('7. recusa série curta', False)
except ValueError:
    caso('7. recusa série curta', True)

# 8. nowcast: início do mês é o modelo; fim do mês é o ritmo
caso('8. nowcast no dia 1 = modelo', pj.nowcast(100, 0.02, 5000) == 5000)
caso('8b. nowcast no fim do mês ≈ realizado/fração',
     abs(pj.nowcast(4800, 0.96, 5000) - (0.96 * 5000 + 0.04 * 5000)) < 1e-6)
diario = {'2026-07-05': 10, '2026-07-20': 30, '2026-08-05': 20, '2026-08-20': 20}
caso('8c. fração do mês', abs(pj.fracao_do_mes(diario, 10, ['2026-07', '2026-08']) - (0.25 + 0.5) / 2) < 1e-9)

# 9. CTe substituído (texto real do SSW)
obs = ['CTRC EMITIDO PARA SUBSTITUIR O CTRC UDI 409658-4,CT-e 002 000399333 3126...',
       'ctrc emitido para substituir o ctrc CAR 043201-6', 'SUBSTITUTO SEM REFERENCIA', None]
caso('9. originais substituídos', pj.originais_substituidos(obs) == {'UDI409658-4', 'CAR043201-6'},
     str(pj.originais_substituidos(obs)))

# 10. Natureza da despesa pendente
caso('10. parcela de investimento = contrato', pj.natureza_pend('Investimento', 'PARCELA 12/48 FINAME') == 'contrato')
caso('10b. PREVISAO = previsão mesmo em grupo de contrato', pj.natureza_pend('Financeiro', 'PREVISAO JUROS') == 'previsao')
caso('10c. provisão operacional = previsão', pj.natureza_pend('Operacional', 'PROVISAO - ABASTECIMENTO') == 'previsao')
caso('10d. operacional sem texto = previsão', pj.natureza_pend('Operacional', 'SALARIO MENSAL') == 'previsao')

# 11. Custos: proporção acompanha a receita, média não, contrato vem do lançado
meses = pj.meses_entre('2026-01', '2026-06')
rec = {m: 1000.0 for m in meses}
lin = {m: {'Operacional|Variável': 600.0, 'Administrativo|Fixo': 80.0} for m in meses}
cp = pj.prever_custos(rec, lin, meses, [2000.0], ['2026-07'], {'2026-07': {'Investimento': 55.0}})
caso('11. operacional = 60% da receita projetada', abs(cp['Operacional'][0] - 1200) < 1e-9, str(cp['Operacional']))
caso('11b. administrativo = média', abs(cp['Administrativo'][0] - 80) < 1e-9)
caso('11c. investimento = contratado', cp['Investimento'][0] == 55.0)
caso('11d. grupo sem histórico = 0', cp['Retirada'][0] == 0.0)
lin_inv = {m: {'Investimento|Fixo': 100.0} for m in meses}
cp = pj.prever_custos(rec, lin_inv, meses, [1000.0, 1000.0], ['2026-07', '2026-08'],
                      {'2026-07': {'Investimento': 55.0}, '2026-08': {'Investimento': 150.0}})
caso('11e. investimento: contratado é piso, a média vale quando é maior', cp['Investimento'] == [100.0, 150.0],
     str(cp['Investimento']))

# 16. Custo por natureza (Administrativo e Financeiro)
subs_srv = {}
for ev, (g, sub) in ns['MAPA_DRE'].items():
    subs_srv.setdefault(g, set()).add(sub)
caso('16. subcategorias de folha existem no Administrativo do MAPA_DRE', pj.SUB_FOLHA <= subs_srv['Administrativo'],
     str(pj.SUB_FOLHA - subs_srv['Administrativo']))
caso('16b. eventos de calendário existem no Administrativo do MAPA_DRE',
     all(ns['MAPA_DRE'].get(e, ('',))[0] == 'Administrativo' for e in pj.EVENTOS_CALENDARIO))
caso('16c. dívida existe no Financeiro do MAPA_DRE', pj.SUB_DIVIDA in subs_srv['Financeiro'])
caso('16d. métodos por linha',
     (pj.metodo_linha('Administrativo|Encargos|INSS'), pj.metodo_linha('Administrativo|Mão de Obra|13O SALARIOS'),
      pj.metodo_linha('Administrativo|Sistemas|SOFTWARE E LICENCAS'), pj.metodo_linha('Financeiro|Dívida|CAPITAL DE GIRO'),
      pj.metodo_linha('Financeiro|Dívida|CAPITAL DE GIRO', com_cronograma=False),
      pj.metodo_linha('Financeiro|Custos Financeiros|JUROS E ENCARGOS'))
     == ('proporcao', 'calendario', 'media', 'cronograma', 'media', 'media'))

ms12 = pj.meses_entre('2025-09', '2026-08')
rec12 = {m: 1000.0 for m in ms12}
lin12 = {m: {'Administrativo|Encargos|INSS': 100.0, 'Administrativo|Sistemas|SOFTWARE E LICENCAS': 20.0,
             'Financeiro|Custos Financeiros|JUROS E ENCARGOS': 50.0, 'Financeiro|Dívida|CAPITAL DE GIRO': 80.0}
         for m in ms12}
lin12['2025-11']['Administrativo|Mão de Obra|13O SALARIOS'] = 40.0
lin12['2025-12']['Administrativo|Mão de Obra|13O SALARIOS'] = 30.0
prev_m = ['2026-10', '2026-11', '2026-12']
cp = pj.prever_custos(rec12, lin12, ms12, [2000.0] * 3, prev_m,
                      {'2026-10': {'Financeiro': 70.0}, '2026-11': {'Financeiro': 70.0}})
caso('16e. administrativo: folha × receita + fixo na média + 13º no mês certo',
     cp['Administrativo'] == [200.0 + 20.0, 200.0 + 20.0 + 40.0, 200.0 + 20.0 + 30.0], str(cp['Administrativo']))
caso('16f. financeiro: juros na média + dívida pelo cronograma (quitada some)',
     cp['Financeiro'] == [50.0 + 70.0, 50.0 + 70.0, 50.0], str(cp['Financeiro']))
cp = pj.prever_custos(rec12, lin12, ms12, [2000.0], ['2026-10'])
caso('16g. sem cronograma (retroanálise) a dívida volta para a média', cp['Financeiro'] == [130.0], str(cp['Financeiro']))


# 12. Ponta a ponta com série sintética
def sintetico(ate_rec, ate_custo):
    ms = pj.meses_entre('2022-01', ate_rec)
    r = {m: 5e6 * (1.008 ** i) * saz[i % 12] for i, m in enumerate(ms)}
    l = {m: {'Operacional|Variável': 0.7 * r[m], 'Deduções|Variável': 0.07 * r[m],
             'Administrativo|Fixo': 450e3, 'Financeiro|Variável': 200e3}
         for m in pj.meses_entre('2022-01', ate_custo)}
    return r, l


r, l = sintetico('2026-08', '2026-08')
res = pj.projetar(r, l, '2026-09', contratos={'2026-10': {'Investimento': 250e3}},
                  receita_mes_corrente=4.5e6, fracao_mes_corrente=0.8)
ms = res['meses']
caso('12. 12 meses a partir do corrente', [m['mes'] for m in ms] == pj.meses_entre('2026-09', '2027-08')
     and [m['h'] for m in ms] == list(range(1, 13)))
caso('12b. mês corrente usa o nowcast', 'nowcast' in ms[0]
     and abs(ms[0]['dre']['receita_bruta'] - pj.nowcast(4.5e6, 0.8, ms[0]['nowcast']['modelo'])) < 1e-6)
caso('12c. faixa p10 ≤ projeção ≤ p90', all(m['receita_p10'] <= m['dre']['receita_bruta'] <= m['receita_p90'] for m in ms))
caso('12d. investimento contratado entra no mês certo', ms[1]['dre']['investimentos'] == 250e3
     and ms[2]['dre']['investimentos'] == 0)
caso('12e. margem operacional preservada (proporção)',
     abs(ms[5]['dre']['custo_operacional'] / ms[5]['dre']['receita_bruta'] - 0.7) < 1e-9)
rt = res['retroanalise']
caso('12f. retroanálise tem as 4 faixas e erro baixo em série limpa',
     set(rt['por_faixa']) == {'1', '2-3', '4-6', '7-12'} and rt['por_faixa']['1']['wape_receita'] < 0.05,
     str({k: round(v['wape_receita'], 3) for k, v in rt['por_faixa'].items()}))

# 13. Despesa um mês atrás da receita (antes do dia 10): o mês intermediário tem receita real
r, l = sintetico('2026-08', '2026-07')
res = pj.projetar(r, l, '2026-09')
m0 = res['meses'][0]
caso('13. mês em fechamento: receita real, custo projetado',
     m0['mes'] == '2026-08' and m0['h'] == 0 and m0['receita_realizada'] and m0['dre']['receita_bruta'] == r['2026-08']
     and len(res['meses']) == 13)

# 14. Receita tem de ir até o mês anterior ao corrente
try:
    pj.projetar(r, l, '2026-11')
    caso('14. recusa buraco entre a receita e o mês corrente', False)
except ValueError:
    caso('14. recusa buraco entre a receita e o mês corrente', True)

# 15. Escada de compromissos soma por ano
esc = pj.escada_compromissos({'2027-01': {'Investimento': 10.0}, '2027-02': {'Investimento': 5.0, 'Financeiro': 1.0},
                              '2028-01': {'Investimento': 3.0}})
caso('15. escada por ano', esc == [{'ano': 2027, 'Investimento': 15.0, 'Financeiro': 1.0, 'total': 16.0},
                                   {'ano': 2028, 'Investimento': 3.0, 'total': 3.0}], str(esc))

# 17. Foto diária: dia lido em Brasília, uma por dia, só depois do horário
import datetime as _dt
import projecao_foto as pf
U = lambda s_: _dt.datetime.strptime(s_, '%Y-%m-%d %H:%M')
caso('17. antes das 07:00 BRT não grava', pf.dia_a_gravar(U('2026-09-29 09:59'), None) is None)     # 06:59 BRT
caso('17b. depois das 07:00 BRT grava o dia de Brasília',
     pf.dia_a_gravar(U('2026-09-29 10:00'), None) == _dt.date(2026, 9, 29))
caso('17c. 22:00 BRT (já é o dia seguinte em UTC) grava o dia de Brasília',
     pf.dia_a_gravar(U('2026-09-30 01:00'), None) == _dt.date(2026, 9, 29))
caso('17d. dia já gravado não grava de novo', pf.dia_a_gravar(U('2026-09-29 15:00'), _dt.date(2026, 9, 29)) is None)
meses_f, retro_f = pf.compactar(res)
caso('17e. a foto guarda a projeção, a previsão do financeiro e o acerto',
     len(meses_f) == len(res['meses']) and 'dre' in meses_f[0] and 'previsao_financeiro' in meses_f[0]
     and 'por_faixa' in retro_f and len(pf.versao_motor()) == 12)

print(f'\n{"TUDO OK" if not FALHAS else str(len(FALHAS)) + " FALHA(S)"}')
raise SystemExit(1 if FALHAS else 0)
