"""
Carga da conta POR CONTRATO em `contabil_contrato_conta` (HANDOFF-CONTABIL §13).

    python importar_contratos_planilha.py ["Contratos 2026 - consorcios e financiamentos.xlsx"]
    python importar_contratos_planilha.py planilha.xlsx --exportar contratos.json
    python importar_contratos_planilha.py contratos.json         (só stdlib + psycopg2)
    ... --force   carrega de novo mesmo já tendo esta carga (a tabela é append-only)

A planilha foi montada por nós em 15/09 (25 contratos do 477 em 2026) e voltou
preenchida pela contabilidade em 14/10. Ninguém escreveu a regra de leitura; ela
foi lida da diferença célula a célula entre a versão enviada e a devolvida:

    coluna O  "Conta (preencher)"            -> preenchida por eles (11)
    coluna N  "Possível conta (a confirmar)" -> PINTADA DE VERDE por eles (6);
                                                a enviada não tinha cor nenhuma
    coluna M  "Conta da sua planilha"        -> casou pelo nº do contrato (7);
                                                ninguém contestou

Ordem: O, depois N verde, depois M. N sem verde NÃO entra (seria palpite nosso).
Contrato que veio em branco e foi respondido à parte, e contrato que surgiu depois
da planilha, vêm de `contabil_seed.json` (fora do git: é dado da Rizza, e o
repositório é público) — seções contratos_respostas_avulsas e contratos_extras.

A chave é (evento, numlancto): no 477 o contrato é o `numlancto`, e cada um dos
25 tem um único evento, unidade e nfiscal (conferido em 14/10/2026).
"""

import json
import os
import re
import sys

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

PADRAO = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      'Contratos 2026 - consorcios e financiamentos.xlsx')
AUTOR = 'planilha de contratos devolvida pela contabilidade (14/10/2026)'
VERDE = 'FF92D050'

SEED = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'contabil_seed.json')


def _seed():
    """Respondidos por outro canal: {(evento, numlancto): (conta, origem)} e a lista
    dos contratos que não estavam na planilha."""
    if not os.path.exists(SEED):
        return {}, []
    with open(SEED, encoding='utf-8') as f:
        s = json.load(f)
    avulsas = {(r['evento'], r['numlancto']): (r['conta_cod'], r['observacao'])
               for r in s.get('contratos_respostas_avulsas', [])}
    return avulsas, s.get('contratos_extras', [])


def _num(v):
    """'1367 - CONSORCIO BB…' / 1812 / '1812' -> 1367 / 1812 / 1812."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    m = re.match(r'\s*(\d+)', str(v))
    return int(m.group(1)) if m else None


def _txt(v):
    return None if v is None else str(v).strip()


def ler_planilha(caminho):
    import openpyxl
    ws = openpyxl.load_workbook(caminho).active
    cab = [_txt(c.value) for c in ws[1]]
    esperado = ['Evento', 'Fornecedor (SSW)', 'Conta (preencher)']
    if not all(e in cab for e in esperado):
        raise ValueError(f'Cabeçalho inesperado: {cab}')

    respostas_avulsas, extras = _seed()
    contratos = []
    for r in range(2, ws.max_row + 1):
        ev = _txt(ws.cell(r, 1).value)
        if not ev:
            continue
        nl = _txt(ws.cell(r, 4).value)
        m, n, o = ws.cell(r, 13), ws.cell(r, 14), ws.cell(r, 15)
        verde = bool(n.fill and n.fill.fill_type and n.fill.fgColor
                     and n.fill.fgColor.rgb == VERDE)
        if o.value is not None:
            cod, origem = _num(o.value), 'coluna O, preenchida pela contabilidade'
        elif n.value is not None and verde:
            cod, origem = _num(n.value), 'provável (coluna N) confirmada em verde pela contabilidade'
        elif m.value is not None:
            cod, origem = _num(m.value), 'nº do contrato bate (coluna M); não contestado'
        elif (ev, nl) in respostas_avulsas:
            cod, origem = respostas_avulsas[(ev, nl)]
        else:
            cod, origem = None, ('provável NÃO confirmada' if n.value is not None
                                 else 'sem conta na planilha devolvida')
        contratos.append({
            'evento': ev, 'numlancto': nl, 'conta_cod': cod,
            'nfiscal': _txt(ws.cell(r, 5).value), 'fornecedor': _txt(ws.cell(r, 3).value),
            'observacao': origem,
        })
    return contratos + [dict(c) for c in extras]


def main():
    force = '--force' in sys.argv
    exportar = sys.argv[sys.argv.index('--exportar') + 1] if '--exportar' in sys.argv else None
    args = [a for a in sys.argv[1:] if a not in ('--force', '--exportar') and a != exportar]
    caminho = args[0] if args else PADRAO
    if not os.path.exists(caminho):
        print(f'❌ Arquivo não encontrado: {caminho}')
        sys.exit(1)

    if caminho.lower().endswith('.json'):
        with open(caminho, encoding='utf-8') as f:
            contratos = json.load(f)
    else:
        contratos = ler_planilha(caminho)

    if exportar:
        with open(exportar, 'w', encoding='utf-8') as f:
            json.dump(contratos, f, ensure_ascii=False, indent=1)
        print(f'✅ {len(contratos)} contratos exportados para {exportar}.')
        return

    conn = psycopg2.connect(
        host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT', 5432),
        dbname=os.getenv('DB_NAME'), user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'))
    cur = conn.cursor()

    cur.execute('SELECT COUNT(*) FROM contabil_contrato_conta WHERE usuario_nome = %s', (AUTOR,))
    if cur.fetchone()[0] and not force:
        print('ℹ️  Esta carga já foi feita. A tabela é append-only — use --force para repetir.')
        sys.exit(0)

    # Mesma trava da tela: existe no plano E é analítica. Uma conta torta aborta
    # a carga inteira — melhor nenhum contrato do que um contrato na conta errada.
    erros = []
    for c in contratos:
        if c['conta_cod'] is None:
            continue
        cur.execute('SELECT analitica, classificacao, descricao FROM contabil_plano_contas '
                    'WHERE codigo_reduzido = %s', (c['conta_cod'],))
        p = cur.fetchone()
        if not p:
            erros.append(f"{c['evento']}/{c['numlancto']}: conta {c['conta_cod']} não existe no plano")
        elif not p[0]:
            erros.append(f"{c['evento']}/{c['numlancto']}: conta {c['conta_cod']} é sintética")
        else:
            c['_conta'] = f'{p[1]} {p[2]}'
    if erros:
        print('❌ Carga abortada, nada gravado:')
        for e in erros:
            print('   ' + e)
        sys.exit(1)

    execute_values(cur, """
        INSERT INTO contabil_contrato_conta
            (evento, numlancto, conta_cod, nfiscal, fornecedor, observacao, usuario_nome)
        VALUES %s
    """, [(c['evento'], c['numlancto'], c['conta_cod'], c['nfiscal'], c['fornecedor'],
           c['observacao'], AUTOR) for c in contratos])
    conn.commit()

    com = [c for c in contratos if c['conta_cod']]
    print(f'✅ {len(contratos)} contratos gravados — {len(com)} com conta, '
          f'{len(contratos) - len(com)} sem.')
    for c in contratos:
        print(f"   {c['evento']} {c['numlancto']:>7} {c['fornecedor'][:28]:28} -> "
              f"{c['conta_cod'] or '—':>5} {c.get('_conta', '')[:45]:45} [{c['observacao'][:40]}]")
    cur.close()
    conn.close()


if __name__ == '__main__':
    main()
