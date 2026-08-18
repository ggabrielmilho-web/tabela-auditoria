"""
Carrega o plano de contas da contabilidade (PERSETO) em `contabil_plano_contas`.

Rode quando o plano mudar (idempotente — substitui a tabela inteira):
    python seed_plano_contas.py [caminho\\do\\arquivo.xls]

Por que em tabela e não lendo o arquivo na hora: a tela de eventos preenche a
conta por CAMPO DE ESCOLHA, nunca por digitação. `4.1.6.01.013` digitado à mão
numa tarde cansada quebra o arquivo de importação sem ninguém saber por quê.
Para o select existir, o plano precisa estar no banco — o app roda em container
e não alcança o Excel da máquina de ninguém.

═══════════════════════════════════════════════════════════════════════════════
SOBRE O ARQUIVO
───────────────────────────────────────────────────────────────────────────────
Vem da contadora com o título "Balancete", mas NÃO tem coluna de saldo: são
`Código`, `Classificação` e `Descrição da conta`. É a listagem de contas.

Está COMPLETO — conferido pela árvore: zero contas analíticas órfãs (sem a
sintética pai) e zero sintéticas sem filha. As lacunas na numeração do `Código`
(9, 11, 14, 17, 29…) são contas desativadas, não corte de exportação.

O `Código` é o código reduzido, e é ele que vai no arquivo de importação
`Z;data;DÉBITO;CRÉDITO;valor;...` — a classificação serve para gente ler.
═══════════════════════════════════════════════════════════════════════════════
"""

import os
import sys

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

PADRAO = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      '..', 'Rizza', 'rizza para teste.xls')
MINIMO_CONTAS = 100   # piso de sanidade: o plano tinha 313 em 17/08/2026


def ler_plano(caminho):
    """Lê o arquivo e devolve as contas normalizadas."""
    d = pd.read_excel(caminho, header=2)
    d.columns = [str(c).strip() for c in d.columns]
    d = d.rename(columns={d.columns[0]: 'codigo',
                          d.columns[1]: 'classificacao',
                          d.columns[3]: 'descricao'})
    d = d[['codigo', 'classificacao', 'descricao']].dropna(subset=['classificacao'])
    d['classificacao'] = d['classificacao'].astype(str).str.strip()
    d['descricao'] = d['descricao'].astype(str).str.strip()
    d['codigo'] = pd.to_numeric(d['codigo'], errors='coerce')
    d['niveis'] = d['classificacao'].str.count(r'\.') + 1
    d['grupo'] = d['classificacao'].str[0]

    # Analítica = FOLHA da árvore, não "tem N níveis". O plano mistura
    # profundidades: `1.1.1.02.003` (banco) e `4.1.6.01.0013` (custo) têm 5
    # níveis, mas `4.1.6.01` tem 4 e é sintética — ela tem filhas.
    # Contar níveis deixaria passar conta sintética no campo de lançamento.
    todas = set(d['classificacao'])
    d['analitica'] = [not any(o.startswith(c + '.') for o in todas)
                      for c in d['classificacao']]

    if len(d) < MINIMO_CONTAS:
        raise ValueError(
            f'Só {len(d)} contas lidas (mínimo esperado {MINIMO_CONTAS}). '
            f'Arquivo truncado ou layout mudou — abortando antes de gravar.')

    # A mesma classificação com dois códigos existe de verdade no plano
    # (2.1.3.01.001 aparece como 166 FORNECEDOR SC e 506 FORNECEDORES DIVERSOS).
    # Mantém o de código maior — é a safra nova, criada junto com o 504
    # CLIENTES DIVERSOS — e avisa, porque é escolha que a contadora precisa
    # confirmar antes de o arquivo de importação referenciar por código.
    dup = d[d.duplicated('classificacao', keep=False)].sort_values('classificacao')
    if len(dup):
        print('\n⚠️  Classificações repetidas no plano (mantido o código maior):')
        for _, r in dup.iterrows():
            print(f'      {r["classificacao"]:<16} cód {int(r["codigo"]):>5}  {r["descricao"]}')
        print()
    d = d.sort_values('codigo').drop_duplicates('classificacao', keep='last')
    return d


def main():
    caminho = sys.argv[1] if len(sys.argv) > 1 else PADRAO
    if not os.path.exists(caminho):
        print(f'❌ Arquivo não encontrado: {caminho}')
        print('   Uso: python seed_plano_contas.py [caminho\\do\\arquivo.xls]')
        sys.exit(1)

    d = ler_plano(caminho)

    conn = psycopg2.connect(
        host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT', 5432),
        dbname=os.getenv('DB_NAME'), user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'))
    cur = conn.cursor()

    # Substituição total dentro de uma transação: se algo falhar, o plano
    # antigo continua de pé. Mesmo princípio das cargas do SSW.
    cur.execute('DELETE FROM contabil_plano_contas')
    execute_values(cur, """
        INSERT INTO contabil_plano_contas
            (classificacao, codigo_reduzido, descricao, niveis, analitica, grupo)
        VALUES %s
    """, [(r['classificacao'],
           None if pd.isna(r['codigo']) else int(r['codigo']),
           r['descricao'], int(r['niveis']), bool(r['analitica']), r['grupo'])
          for _, r in d.iterrows()])
    conn.commit()

    g34 = d[(d['grupo'].isin(['3', '4'])) & (d['analitica'])]
    print(f'✅ {len(d)} contas gravadas em contabil_plano_contas.')
    print(f'   analíticas: {int(d["analitica"].sum())}  |  '
          f'destino de despesa/receita (grupos 3 e 4): {len(g34)}')
    print(f'   por grupo: ' + ', '.join(
        f'{g}={int((d["grupo"] == g).sum())}' for g in sorted(d['grupo'].unique())))

    cur.close()
    conn.close()


if __name__ == '__main__':
    main()
