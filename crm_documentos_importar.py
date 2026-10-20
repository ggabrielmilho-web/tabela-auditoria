# -*- coding: utf-8 -*-
"""Importa a planilha do CRM ("Relatório de Documentos Nestle") para a aba CRM - Documentos.

    python -X utf8 crm_documentos_importar.py "Relatório de Documentos Nestle V_02.xlsx"            # só mostra
    python -X utf8 crm_documentos_importar.py "Relatório de Documentos Nestle V_02.xlsx" --aplicar  # grava

Lê as quatro abas (NESTLE 2025, QUIMICA AMPARO, UNILEVER 2025, AMBEV), casa cada linha com o
CT-e do 455 (pelo CTRC; sem ele, pelo nº do CT-e) e grava a marcação dela — etapa, tipo de
frete, canal, datas, anotações — em `crm_doc_marcacao`. O valor, a emissão e o pagamento NÃO
vêm da planilha: são do SSW. A linha como veio fica guardada em `planilha` para auditoria.

Pode rodar de novo: linha já editada na tela é preservada; as outras são regravadas com o
que a planilha diz.

A planilha é dado de cliente: NUNCA entra no git (o repositório é público). Em produção:
    scp "<planilha>.xlsx" root@<servidor>:/tmp/crm.xlsx
    docker cp /tmp/crm.xlsx $(docker ps -q -f name=rizza-auditoria_app):/tmp/crm.xlsx
    docker exec $(docker ps -q -f name=rizza-auditoria_app) python -X utf8 crm_documentos_importar.py /tmp/crm.xlsx
    (conferir o relatório; depois o mesmo comando com --aplicar)
"""
import sys
from collections import Counter

import crm_documentos as crm
import server


def _dax_op(q):
    return server.contabil_dax(q, dataset_id=server.CONFIG['dataset_id'])


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print(__doc__)
        sys.exit(1)
    aplicar = '--aplicar' in sys.argv
    planilha = crm.ler_planilha(args[0])
    abas = ', '.join('%s (%d)' % (crm.CARTEIRAS[c]['aba'], len(l)) for c, l in planilha.items())
    print(f'Planilha: {abas}')
    dados = crm.dados_ssw(_dax_op, server.contabil_dax, forcar=True)
    print(f"455: {len(dados['ce'])} CT-e das carteiras (lido em {dados['lido_455']}) · "
          f"441: {len(dados['pag'])} CTRCs pagos (lido em {dados['lido_441']})")
    regs, rel = crm.preparar_importacao(planilha, dados['ce'])

    ce = {crm.chave_ctrc(r.get('ctrc')): r for r in dados['ce'] if crm.chave_ctrc(r.get('ctrc'))}
    for cid, st in rel.items():
        print(f"\n== {crm.CARTEIRAS[cid]['nome']}: {st['linhas']} linhas · pelo CTRC {st['pelo_ctrc']} · "
              f"pelo CT-e {st['pelo_cte']} · sem CT-e no SSW (linha manual) {st['manual']} · repetidas {st['repetidas']}")
        print('   etapas:', sorted(st['etapas'].items(), key=lambda x: -x[1]))
        if st['etapa_desconhecida']:
            print('   ⚠ etapa fora da lista da carteira:', st['etapa_desconhecida'])
        if st['tipos_para_anotacao']:
            print('   tipo de frete que virou anotação:', sorted(st['tipos_para_anotacao'].items(), key=lambda x: -x[1])[:12])
        dif = [(g['chave'], g['planilha']['frete'], ce[g['chave']].get('frete')) for g in regs
               if g['carteira'] == cid and g['chave'] in ce and g['planilha']['frete'] is not None
               and abs((g['planilha']['frete'] or 0) - (ce[g['chave']].get('frete') or 0)) > 0.01]
        if dif:
            print(f'   valor da planilha ≠ valor do CT-e no SSW ({len(dif)}; vale o do SSW):', dif[:8])
        campos = Counter(k for g in regs if g['carteira'] == cid for k in g['campos'])
        print('   campos preenchidos:', dict(campos))

    if not aplicar:
        print('\nNada gravado. Rode com --aplicar para gravar.')
        return
    conn = server.get_db()
    try:
        with conn.cursor() as cur:
            crm.garantir_tabelas(cur)
            novas, atual, preserv = crm.gravar_importacao(cur, regs)
        conn.commit()
    finally:
        conn.close()
    print(f'\nGravado: {novas} novas · {atual} atualizadas · {preserv} preservadas (já editadas na tela)')


if __name__ == '__main__':
    main()
