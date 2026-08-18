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

import json
import os
import sys
import unicodedata

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

# `pandas` só entra para LER o Excel; a imagem de produção não o tem. Por isso
# o import é preguiçoso e existe o caminho do .json:
#
#   local      python importar_eventos_planilha.py "EVENTOS COM INFORMAÇÕES.xlsx" --exportar eventos.json
#   servidor   docker exec $CT python importar_eventos_planilha.py /app/eventos.json

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
    if v is None or (isinstance(v, float) and v != v):
        return None
    t = _sem_acento(v).strip().upper()
    return t if t in ('SIM', 'NAO', 'PARCIAL') else None


def _vazio(v):
    return v is None or (isinstance(v, float) and v != v)   # NaN != NaN


def _bool(v):
    if _vazio(v):
        return None
    if isinstance(v, bool):
        return v
    t = _sem_acento(v).strip().upper()
    return True if t in ('VERDADEIRO', 'TRUE', 'SIM', '1') else \
        (False if t in ('FALSO', 'FALSE', 'NAO', '0') else None)


def _texto(v):
    if _vazio(v):
        return None
    t = str(v).strip()
    return t or None


def ler_excel(caminho):
    """Lê a planilha e devolve a lista já normalizada. Precisa de pandas."""
    import pandas as pd
    d = pd.read_excel(caminho)
    d.columns = [_norm_col(c) for c in d.columns]

    faltando = [c for c in ('EVENTO', 'DESCRICAO', 'TEM NOTA?',
                            'CONTABILIZA DESPESA POR IMPORTACAO SSW',
                            'CONTABILIZA PROVISAO') if c not in d.columns]
    if faltando:
        raise ValueError(f'Colunas ausentes: {faltando}\n'
                         f'   colunas do arquivo: {list(d.columns)}')

    d = d.dropna(subset=['EVENTO'])
    return [{
        'evento': str(int(r['EVENTO'])).strip(),
        'descricao': _texto(r.get('DESCRICAO')),
        'tem_nota': _bool(r.get('TEM NOTA?')),
        'contabiliza_despesa': _enum(r.get('CONTABILIZA DESPESA POR IMPORTACAO SSW')),
        'contabiliza_provisao': _enum(r.get('CONTABILIZA PROVISAO')),
        'aproveita_credito': _bool(r.get('APROVEITA CREDITO')),
        'importar_fiscal': _bool(r.get('IMPORTAR PARA FISCAL?')),
        'validar_simples': _bool(r.get('VALIDAR SIMPLES?')),
        'grupo_importacao': _texto(r.get('GRUPO DE IMPORTACAO')),
    } for _, r in d.iterrows()]


def main():
    force = '--force' in sys.argv
    exportar = sys.argv[sys.argv.index('--exportar') + 1] if '--exportar' in sys.argv else None
    args = [a for a in sys.argv[1:]
            if a not in ('--force', '--exportar') and a != exportar]
    caminho = args[0] if args else PADRAO

    if not os.path.exists(caminho):
        print(f'❌ Arquivo não encontrado: {caminho}')
        print('   Uso: python importar_eventos_planilha.py [arquivo.xlsx|arquivo.json] '
              '[--exportar saida.json] [--force]')
        sys.exit(1)

    if caminho.lower().endswith('.json'):
        with open(caminho, encoding='utf-8') as f:
            eventos = json.load(f)
    else:
        eventos = ler_excel(caminho)

    if len(eventos) < MINIMO:
        print(f'❌ Só {len(eventos)} eventos lidos (mínimo {MINIMO}). Abortando.')
        sys.exit(1)

    if exportar:
        with open(exportar, 'w', encoding='utf-8') as f:
            json.dump(eventos, f, ensure_ascii=False, indent=1)
        print(f'✅ {len(eventos)} eventos exportados para {exportar}.')
        print('   Leve esse arquivo para o servidor — ele carrega sem pandas.')
        return

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

    linhas = [(
        e['evento'], e['descricao'],
        None, None,                                   # contas: ela preenche na tela
        e['tem_nota'], e['contabiliza_despesa'], e['contabiliza_provisao'],
        e['aproveita_credito'], e['importar_fiscal'], e['validar_simples'],
        e['grupo_importacao'], AUTOR,
    ) for e in eventos]

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
