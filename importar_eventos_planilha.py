"""
Carga ÚNICA da planilha de eventos da contadora em `contabil_evento_conta`.

    python importar_eventos_planilha.py [caminho\\EVENTOS COM INFORMAÇÕES.xlsx]
    python importar_eventos_planilha.py --force     (importa de novo)

Depois desta carga a planilha SAI DO FLUXO: quem passa a ser dono da
configuração é a tela `/contabil/eventos`. O objetivo desta carga é só não
fazer a contadora redigitar as 97 linhas que ela acabou de preencher — pedir
isso queimaria a boa vontade e ainda arriscaria divergir do que ela mandou.

A tabela é APPEND-ONLY: cada carga acrescenta linhas, não sobrescreve. Rodar
duas vezes não corrompe nada, só polui o histórico — daí o --force.

═══════════════════════════════════════════════════════════════════════════════
ARMADILHAS DO ARQUIVO (medidas, não supostas)
───────────────────────────────────────────────────────────────────────────────
• A coluna da provisão vem como  " CONTABILIZA PROVISÃO"  — com aspas E espaço
  à esquerda no próprio nome. Sem limpar, o KeyError é na hora.
• `Evento` vem numérico (2000) e a chave do cruzamento é texto ('2000').
• As 4 flags antigas vêm VERDADEIRO/FALSO (pandas lê como bool).
• As 2 novas vêm SIM / NÃO / PARCIAL, em texto.
• `Grupo de Importação` vem vazio na maioria (NaN).
═══════════════════════════════════════════════════════════════════════════════
"""

import os
import sys
import unicodedata

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

PADRAO = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      '..', 'Rizza', 'EVENTOS COM INFORMAÇÕES.xlsx')
AUTOR = 'importação da planilha da contadora'
MINIMO = 50


def _sem_acento(s):
    s = unicodedata.normalize('NFKD', str(s))
    return ''.join(c for c in s if not unicodedata.combining(c))


def _norm_col(c):
    """" CONTABILIZA PROVISÃO" -> CONTABILIZA PROVISAO"""
    return _sem_acento(str(c)).strip().strip('"').strip().upper()


def _enum(v):
    """SIM / NÃO / PARCIAL -> SIM / NAO / PARCIAL (sem acento, maiúsculo)."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    t = _sem_acento(v).strip().upper()
    return t if t in ('SIM', 'NAO', 'PARCIAL') else None


def _bool(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, bool):
        return v
    t = _sem_acento(v).strip().upper()
    return True if t in ('VERDADEIRO', 'TRUE', 'SIM', '1') else \
        (False if t in ('FALSO', 'FALSE', 'NAO', '0') else None)


def _texto(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    t = str(v).strip()
    return t or None


def main():
    args = [a for a in sys.argv[1:] if a != '--force']
    force = '--force' in sys.argv
    caminho = args[0] if args else PADRAO

    if not os.path.exists(caminho):
        print(f'❌ Arquivo não encontrado: {caminho}')
        sys.exit(1)

    d = pd.read_excel(caminho)
    d.columns = [_norm_col(c) for c in d.columns]

    faltando = [c for c in ('EVENTO', 'DESCRICAO', 'TEM NOTA?',
                            'CONTABILIZA DESPESA POR IMPORTACAO SSW',
                            'CONTABILIZA PROVISAO') if c not in d.columns]
    if faltando:
        print(f'❌ Colunas ausentes: {faltando}')
        print(f'   colunas do arquivo: {list(d.columns)}')
        sys.exit(1)

    d = d.dropna(subset=['EVENTO'])
    if len(d) < MINIMO:
        print(f'❌ Só {len(d)} eventos lidos (mínimo {MINIMO}). Abortando.')
        sys.exit(1)

    conn = psycopg2.connect(
        host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT', 5432),
        dbname=os.getenv('DB_NAME'), user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'))
    cur = conn.cursor()

    cur.execute('SELECT COUNT(*) FROM contabil_evento_conta WHERE usuario_nome = %s', (AUTOR,))
    ja = cur.fetchone()[0]
    if ja and not force:
        print(f'ℹ️  Já existem {ja} linhas desta importação. A tabela é append-only:')
        print('   rodar de novo acrescenta linhas e a tela passa a mostrar as novas.')
        print('   Se é isso mesmo, rode com --force.')
        cur.close(); conn.close()
        sys.exit(0)

    linhas = []
    for _, r in d.iterrows():
        linhas.append((
            str(int(r['EVENTO'])).strip(),
            _texto(r.get('DESCRICAO')),
            None, None,                                   # contas: ela preenche na tela
            _bool(r.get('TEM NOTA?')),
            _enum(r.get('CONTABILIZA DESPESA POR IMPORTACAO SSW')),
            _enum(r.get('CONTABILIZA PROVISAO')),
            _bool(r.get('APROVEITA CREDITO')),
            _bool(r.get('IMPORTAR PARA FISCAL?')),
            _bool(r.get('VALIDAR SIMPLES?')),
            _texto(r.get('GRUPO DE IMPORTACAO')),
            AUTOR,
        ))

    execute_values(cur, """
        INSERT INTO contabil_evento_conta
            (evento, descricao, conta_debito, conta_credito, tem_nota,
             contabiliza_despesa, contabiliza_provisao, aproveita_credito,
             importar_fiscal, validar_simples, grupo_importacao, usuario_nome)
        VALUES %s
    """, linhas)
    conn.commit()

    resumo = {}
    for l in linhas:
        resumo[l[5]] = resumo.get(l[5], 0) + 1
    print(f'✅ {len(linhas)} eventos importados de {os.path.basename(caminho)}.')
    print('   CONTABILIZA DESPESA: ' + ', '.join(f'{k or "(vazio)"}={v}'
                                                 for k, v in sorted(resumo.items(), key=lambda x: -x[1])))
    prov = {}
    for l in linhas:
        prov[l[6]] = prov.get(l[6], 0) + 1
    print('   CONTABILIZA PROVISÃO: ' + ', '.join(f'{k or "(vazio)"}={v}'
                                                  for k, v in sorted(prov.items(), key=lambda x: -x[1])))
    print('   contas ficam em branco — é o que ela vai preencher na tela.')

    cur.close()
    conn.close()


if __name__ == '__main__':
    main()
