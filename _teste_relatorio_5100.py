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

def cte(ctrc, nf, emissao, rem='X', num=None):
    return {'ctrc': ctrc, 'nf': nf, 'emissao': emissao, 'cli_rem': rem,
            'cte_num': num}


idx = R.indexar_ctes([
    cte('UDI415682-0', 361230,  '2026-08-30T00:00:00', num=2000404674),
    # mesma NF em dois CTes: um antes da despesa, outro depois (duas pernas)
    cte('UDI415577-7', 3841991, '2026-08-29T00:00:00'),
    cte('UDI416382-6', 3841991, '2026-09-17T00:00:00'),
    # zeros à esquerda no cadastro têm de casar com a NF crua do histórico
    cte('UDI400000-1', 4288,    '2026-08-31T00:00:00'),
])

r = R.escolher_cte('NF 361230', '2026-09-01T00:00:00', idx)
check('NF única', (r['cte'], r['cte_outros']), ('UDI415682-0', ''))
check('critério da NF única', r['cte_criterio'], 'NF única')
# O CRM confere pelo número fiscal do CT-e, não pelo número interno do SSW.
check('nº fiscal do CTe vem junto', r['cte_numero'], 2000404674)

# A regra do desempate: fica o CTe emitido antes/no dia, e o outro é preservado.
r = R.escolher_cte('CNPJFORN13274688000140 NF 3841991 - MERIO', '2026-09-04T00:00:00', idx)
check('duas pernas → fica a anterior', (r['cte'], r['cte_outros']),
      ('UDI415577-7', 'UDI416382-6'))
check('critério diz que houve escolha',
      r['cte_criterio'], '2 CTes com a NF — ficou o anterior mais próximo')

# O rastro tem de permitir enxergar um casamento errado sem abrir o CTe.
check('rastro: NF que saiu do texto', r['nf_historico'], '3841991')
check('rastro: cliente declarado no histórico', r['cliente_historico'], 'MERIO')
check('rastro: emissão do CTe escolhido', r['cte_emissao'], '2026-08-29')

r = R.escolher_cte('SD - CARREGAMENTO ITATIAIA - PIX 186.223.937-12', '2026-09-10T00:00:00', idx)
check('sem NF no histórico → célula vazia', r['cte'], '')
check('sem NF: o motivo aparece', r['cte_criterio'], 'sem NF no histórico')
check('sem CTe, o nº fiscal fica vazio', r['cte_numero'], '')

r = R.escolher_cte('NF 38754121 - NESTLE', '2026-09-03T00:00:00', idx)
check('NF que não existe na base → célula vazia', r['cte'], '')
check('NF sem CTe: o motivo aparece', r['cte_criterio'], 'NF sem CTe na janela')
check('NF sem CTe ainda mostra a NF lida', r['nf_historico'], '38754121')

check('NF pequena casa sem zeros à esquerda',
      R.escolher_cte('CNPJFORN26452694000191 - NF 4288 TANGARA',
                     '2026-09-04T00:00:00', idx)['cte'],
      'UDI400000-1')

# Todos posteriores à despesa: não deve devolver vazio, e sim o mais próximo.
idx_pos = R.indexar_ctes([cte('UDI999999-9', 555, '2026-09-20T00:00:00')])
check('só CTe posterior → o mais próximo, não vazio',
      R.escolher_cte('NF 555', '2026-09-10T00:00:00', idx_pos)['cte'], 'UDI999999-9')


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


# ── deve_disparar: o relógio do servidor é UTC, o gatilho é de Brasília ──────
#
# Toda entrada aqui está em UTC, como o container vê. 08:00 BRT = 11:00 UTC.

def disp(utc, ultima=None):
    return R.deve_disparar(utc, ultima)


d, ini, fim = disp(datetime(2026, 9, 21, 11, 0))      # segunda 08:00 BRT
check('segunda 08:00 BRT dispara', d, True)
check('e manda a semana que fechou', (ini, fim), (date(2026, 9, 14), date(2026, 9, 20)))

# A ARMADILHA: 22:00 de domingo em Brasília já é SEGUNDA em UTC. Lendo o dia no
# relógio cru, dispararia no domingo à noite — e `semana_anterior` de um domingo
# devolve a semana RETRASADA, então sairia o relatório errado.
d, ini, _ = disp(datetime(2026, 9, 21, 1, 0))         # dom 22:00 BRT = seg 01:00 UTC
check('domingo 22:00 BRT não dispara', d, False)
check('e a semana lida ali seria a retrasada', ini, date(2026, 9, 7))

# O espelho: 21:00 BRT de segunda ainda é segunda; 00:00 UTC de terça não é gatilho.
check('segunda 21:00 BRT (terça 00:00 UTC) não dispara — fora da janela',
      disp(datetime(2026, 9, 22, 0, 0))[0], False)

check('segunda 07:59 BRT ainda não', disp(datetime(2026, 9, 21, 10, 59))[0], False)
check('segunda 10:59 BRT ainda dispara (janela de 180 min)',
      disp(datetime(2026, 9, 21, 13, 59))[0], True)
check('segunda 11:01 BRT já passou da janela',
      disp(datetime(2026, 9, 21, 14, 1))[0], False)
check('terça 08:00 BRT não dispara', disp(datetime(2026, 9, 22, 11, 0))[0], False)

# Restart no meio da manhã não manda duas vezes: o marcador é a semana.
check('semana já enviada não repete',
      disp(datetime(2026, 9, 21, 11, 30), date(2026, 9, 14))[0], False)
check('semana seguinte dispara de novo',
      disp(datetime(2026, 9, 28, 11, 0), date(2026, 9, 14))[0], True)


# ── montar: bloco de conferência na frente, resto do 477 atrás ───────────────

despesas = [{
    'empresa': 1, 'emissao': '2026-09-01T00:00:00', 'nome_fornecedor': 'FULANO',
    'uni': 'UDI', 'numlancto': 104376, 'parcela': '01', 'nfiscal': 361230,
    'classificacao_dre': 'X', 'historico_despesa': 'NF 361230',
    'liq_empresa': 1, 'vlr_final': 100.0,
}]
cols, linhas = R.montar(despesas, [cte('UDI415682-0', 361230, '2026-08-30T00:00:00')])

check('a ordem da frente é a combinada',
      cols[:10], ['emissao', 'uni', 'numlancto', 'parcela', 'nome_fornecedor',
                  'vlr_final', 'historico_despesa', 'nfiscal', 'cte', 'cte_numero'])
# O pedido de 23/09: a NF da despesa encostada no CTe, que é o par que se compara.
check('nfiscal vem imediatamente antes do cte',
      cols.index('nfiscal') + 1, cols.index('cte'))
check('valor resolvido', linhas[0]['cte'], 'UDI415682-0')

# Nada do 477 pode sumir: o que não está no cabeçalho vai para o fim, na ordem.
check('resto do 477 preservado, na ordem original',
      cols[10:], ['cte_outros', 'nf_historico', 'cliente_historico', 'cte_remetente',
                 'cte_emissao', 'cte_criterio',
                 'empresa', 'classificacao_dre', 'liq_empresa'])
check('nenhuma coluna do 477 se perde',
      set(despesas[0]).issubset(set(cols)), True)
check('nenhuma coluna repetida', len(cols), len(set(cols)))

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
