# -*- coding: utf-8 -*-
"""Junta as linhas repetidas da aba CRM - Documentos: a manual da planilha (MAN-…) e a do CT-e no SSW.

    python -X utf8 crm_documentos_fundir.py            # só mostra
    python -X utf8 crm_documentos_fundir.py --aplicar  # grava

A linha do SSW fica e recebe só o que falta nela (etapa e campos); o que as duas têm diferente
continua como está na do SSW. A manual sai, e a cópia inteira dela fica no histórico (`*fusao*`).
Frete que não confere não se junta: aparece no fim, para olhar à mão. Pode rodar de novo.

Em produção:
    docker exec $(docker ps -q -f name=rizza-auditoria_app) python -X utf8 crm_documentos_fundir.py
"""
import sys

import crm_documentos as crm
import server


def _dax_op(q):
    return server.contabil_dax(q, dataset_id=server.CONFIG['dataset_id'])


def _brl(v):
    return f'R$ {float(v or 0):,.2f}'.replace(',', 'x').replace('.', ',').replace('x', '.')


def main():
    aplicar = '--aplicar' in sys.argv
    dados = crm.dados_ssw(_dax_op, server.contabil_dax, forcar=True)
    print(f"455: {len(dados['ce'])} CT-e das carteiras (lido em {dados['lido_455']})")
    total = juntas = 0
    conferir = []
    conn = server.get_db()
    try:
        with conn.cursor() as cur:
            # a mesma trava do FATURADO automático: a tela não grava etapa no meio da fusão
            cur.execute('SELECT pg_advisory_xact_lock(%s)', (server._CRM_TRAVA_AUTO,))
            for carteira, cfg in crm.CARTEIRAS.items():
                marc = crm.marcacoes(cur, carteira)
                linhas = server._crm_montar(carteira, dados, marc)
                gemeas = crm.linhas_gemeas(linhas, marc)
                conferir += [(cfg['nome'], g) for g in gemeas if not g['confere']]
                gemeas = [g for g in gemeas if g['confere']]
                aberto = sum(float(l.get('frete') or 0) for l in linhas if l['aberto'])
                dobrado = sum(float(g['man'].get('frete') or 0) for g in gemeas
                              if g['man']['aberto'] and any(a['aberto'] for a in g['alvos']))
                print(f"\n== {cfg['nome']}: {len(linhas)} linhas · {len(gemeas)} repetidas · "
                      f"Em aberto {_brl(aberto)} → {_brl(aberto - dobrado)}")
                for g in gemeas:
                    man = g['man']
                    total += 1
                    print(f"  {man['chave']} ({man.get('ctrc') or man.get('cte')}, {_brl(man.get('frete'))}, "
                          f"etapa {man.get('etapa') or '—'}) → {' + '.join(a['chave'] for a in g['alvos'])} [{g['como']}]")
                    for a in g['alvos']:
                        plano = crm.plano_fusao(marc[man['chave']], marc.get(a['chave']))
                        if plano['etapa']:
                            print(f"      {a['chave']}: etapa recebe {plano['etapa']}")
                        if plano['campos']:
                            print(f"      {a['chave']}: recebe {plano['campos']}")
                        if plano['divergem']:
                            print(f"      {a['chave']}: diferente, fica o do SSW (SSW, manual): {plano['divergem']}")
                    if aplicar:
                        juntas += crm.fundir_gemea(cur, carteira, g, marc)
        conn.commit()
    finally:
        conn.close()
    if conferir:
        print(f'\nFrete não confere, ficam como estão ({len(conferir)}):')
        for nome, g in conferir:
            print(f"  {nome}: {g['man']['chave']} {_brl(g['man'].get('frete'))} × "
                  f"{[(a['chave'], a.get('frete')) for a in g['alvos']]}")
    print(f'\n{juntas} linhas juntadas.' if aplicar else f'\nNada gravado: {total} linhas para juntar. Rode com --aplicar para gravar.')


if __name__ == '__main__':
    main()
