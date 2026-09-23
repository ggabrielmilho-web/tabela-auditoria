# -*- coding: utf-8 -*-
"""Regressão do motor do relatório 5100 — sem rede, sem banco.

Um caso por regra, todos tirados de lançamentos reais de 2026/09.
    python -X utf8 _teste_relatorio_5100.py
"""
from datetime import datetime, date

import relatorio_5100 as R

falhas = []


def check(nome, obtido, esperado):
    if obtido != esperado:
        falhas.append(f'{nome}\n     obtido:   {obtido!r}\n     esperado: {esperado!r}')


# ── extrair_nf ───────────────────────────────────────────────────────────────

check('NF simples',
      R.extrair_nf('NF 361230')['nfs'], ['361230'])

check('NF com cliente',
      R.extrair_nf('NF 492272 - FINI')['cliente'], 'FINI')

# O CNPJFORN tem 14 dígitos colados; sem removê-lo antes, o regex de número o pegaria.
p = R.extrair_nf('CNPJFORN02691482000107 NF 2117943 - HEINZ')
check('CNPJFORN não vira NF', p['nfs'], ['2117943'])
check('CNPJFORN é capturado à parte', p['cnpj'], '02691482000107')

check('NF colada no CNPJFORN',
      R.extrair_nf('CNPJFORN05868574001171NF 2119688 - HEINZ')['nfs'], ['2119688'])

check('separador solto (-- e *)',
      R.extrair_nf('CNPJFORN13338712000167  -- NF 441450 YPE')['nfs'], ['441450'])

# Lançamento de serviço pago por PIX: não tem NF, e o CPF não pode virar uma.
check('SD/PIX não tem NF',
      R.extrair_nf('SD - CARREGAMENTO ITATIAIA - PIX 186.223.937-12 ISABELLY')['nfs'], [])

# "B101492410" é ordem de carregamento da Nestlé, não nota fiscal.
check('ordem de carregamento não é NF',
      R.extrair_nf('SD - CARREGAMENTO NESTLE B101492410 - PIX CPF 013-973-950-52')['nfs'], [])

check('histórico vazio', R.extrair_nf(None)['nfs'], [])


# ── escolher_cte ─────────────────────────────────────────────────────────────

def cte(ctrc, nf, emissao, rem='X'):
    return {'ctrc': ctrc, 'nf': nf, 'emissao': emissao, 'cli_rem': rem}


idx = R.indexar_ctes([
    cte('UDI415682-0', 361230,  '2026-08-30T00:00:00'),
    # mesma NF em dois CTes: um antes da despesa, outro depois (duas pernas)
    cte('UDI415577-7', 3841991, '2026-08-29T00:00:00'),
    cte('UDI416382-6', 3841991, '2026-09-17T00:00:00'),
    # zeros à esquerda no cadastro têm de casar com a NF crua do histórico
    cte('UDI400000-1', 4288,    '2026-08-31T00:00:00'),
])

check('NF única',
      R.escolher_cte('NF 361230', '2026-09-01T00:00:00', idx),
      ('UDI415682-0', ''))

# A regra do desempate: fica o CTe emitido antes/no dia, e o outro é preservado.
check('duas pernas → fica a anterior',
      R.escolher_cte('CNPJFORN13274688000140 NF 3841991 - MERIO', '2026-09-04T00:00:00', idx),
      ('UDI415577-7', 'UDI416382-6'))

check('sem NF no histórico → célula vazia',
      R.escolher_cte('SD - CARREGAMENTO ITATIAIA - PIX 186.223.937-12', '2026-09-10T00:00:00', idx),
      ('', ''))

check('NF que não existe na base → célula vazia',
      R.escolher_cte('NF 38754121 - NESTLE', '2026-09-03T00:00:00', idx),
      ('', ''))

check('NF pequena casa sem zeros à esquerda',
      R.escolher_cte('CNPJFORN26452694000191 - NF 4288 TANGARA', '2026-09-04T00:00:00', idx)[0],
      'UDI400000-1')

# Todos posteriores à despesa: não deve devolver vazio, e sim o mais próximo.
idx_pos = R.indexar_ctes([cte('UDI999999-9', 555, '2026-09-20T00:00:00')])
check('só CTe posterior → o mais próximo, não vazio',
      R.escolher_cte('NF 555', '2026-09-10T00:00:00', idx_pos)[0], 'UDI999999-9')


# ── semana_anterior ──────────────────────────────────────────────────────────

check('segunda 21/09 → 14/09 a 20/09',
      R.semana_anterior(datetime(2026, 9, 21)), (date(2026, 9, 14), date(2026, 9, 20)))

check('domingo 20/09 → 07/09 a 13/09',
      R.semana_anterior(datetime(2026, 9, 20)), (date(2026, 9, 7), date(2026, 9, 13)))

check('quarta 23/09 → 14/09 a 20/09',
      R.semana_anterior(datetime(2026, 9, 23)), (date(2026, 9, 14), date(2026, 9, 20)))

# Virada de ano: a semana anterior atravessa dezembro.
check('segunda 04/01/2027 → 28/12 a 03/01',
      R.semana_anterior(datetime(2027, 1, 4)), (date(2026, 12, 28), date(2027, 1, 3)))


# ── montar: a coluna cte fica logo depois do histórico ───────────────────────

despesas = [{
    'emissao': '2026-09-01T00:00:00', 'nome_fornecedor': 'FULANO',
    'classificacao_dre': 'X', 'historico_despesa': 'NF 361230',
    'liq_empresa': 1, 'vlr_final': 100.0,
}]
cols, linhas = R.montar(despesas, [cte('UDI415682-0', 361230, '2026-08-30T00:00:00')])
i = cols.index('historico_despesa')
check('cte vem logo após historico_despesa', cols[i + 1], 'cte')
check('cte_outros vem em seguida', cols[i + 2], 'cte_outros')
check('a coluna seguinte do 477 é preservada', cols[i + 3], 'liq_empresa')
check('valor resolvido', linhas[0]['cte'], 'UDI415682-0')

check('sem despesas → nada', R.montar([], []), ([], []))


# ── corpo do e-mail ──────────────────────────────────────────────────────────

# O separador brasileiro vale só para o número: o texto não pode perder vírgulas.
corpo = R.corpo_texto(date(2026, 9, 14), date(2026, 9, 20),
                      {'n': 41, 'total': 33736.63, 'com_cte': 36, 'sem_cte': 5})
check('valor em pt-BR', 'R$ 33.736,63' in corpo, True)
check('texto intacto', 'terceiros (evento 5100)' in corpo, True)
check('semana sem lançamento é dita',
      'Nenhum lançamento' in R.corpo_texto(date(2026, 9, 14), date(2026, 9, 20),
                                           {'n': 0, 'total': 0, 'com_cte': 0, 'sem_cte': 0}),
      True)


if falhas:
    print(f'❌ {len(falhas)} falha(s):')
    for f in falhas:
        print('  -', f)
    raise SystemExit(1)
print('✅ tudo verde')
