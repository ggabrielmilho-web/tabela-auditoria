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
• Versão de 14/09/2026 ("… - contas.xlsx"): ganhou a coluna `CONTA`, em CÓDIGO
  REDUZIDO (778), e a tabela guarda CLASSIFICAÇÃO (4.2.2.04.0019) — a tradução
  é pelo plano, com a MESMA trava da tela (existe + analítica). Conta que não
  passa fica em branco com o motivo na observação; nunca entra torta.
• Essa versão também tem 24 sub-linhas de contas POR CONTRATO (consórcio,
  financiamento) com `Evento` vazio ou em texto ("contas de consórcios do
  balancete"). Não são eventos: são ignoradas aqui e listadas no fim.
• `CONTA = "não há"` (5321 SIMPLES) é decisão dela, não erro: fica em branco
  com observação.
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
                      '..', 'Rizza', 'EVENTOS COM INFORMAÇÕES - contas.xlsx')
AUTOR = 'importação da planilha da contadora (14/09/2026)'
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

    # Só linha com evento NUMÉRICO é evento. As sub-linhas de contrato têm
    # `Evento` vazio ou em texto — vão para `extras`, não para a tabela.
    ev_num = d['EVENTO'].map(lambda v: str(v).strip().replace('.0', '').isdigit()
                             if not _vazio(v) else False)
    extras = [(_texto(r.get('DESCRICAO')), _texto(r.get('CONTA')))
              for _, r in d[~ev_num].iterrows() if not _vazio(r.get('CONTA'))]
    d = d[ev_num]
    return [{
        'evento': str(int(float(r['EVENTO']))).strip(),
        'conta_reduzida': _texto(r.get('CONTA')),
        'descricao': _texto(r.get('DESCRICAO')),
        'tem_nota': _bool(r.get('TEM NOTA?')),
        'contabiliza_despesa': _enum(r.get('CONTABILIZA DESPESA POR IMPORTACAO SSW')),
        'contabiliza_provisao': _enum(r.get('CONTABILIZA PROVISAO')),
        'aproveita_credito': _bool(r.get('APROVEITA CREDITO')),
        'importar_fiscal': _bool(r.get('IMPORTAR PARA FISCAL?')),
        'validar_simples': _bool(r.get('VALIDAR SIMPLES?')),
        'grupo_importacao': _texto(r.get('GRUPO DE IMPORTACAO')),
    } for _, r in d.iterrows()], extras


def traduzir_conta(cur, reduzida):
    """Código reduzido da planilha -> classificação da tabela, ou (None, motivo).

    Mesma trava de `/api/contabil/eventos`: existe no plano E é analítica."""
    if reduzida is None:
        return None, None
    t = str(reduzida).strip()
    if t.endswith('.0'):
        t = t[:-2]
    if not t.isdigit():
        return None, f'planilha: CONTA = "{t}"'
    cur.execute("SELECT classificacao, analitica FROM contabil_plano_contas "
                "WHERE codigo_reduzido = %s", (int(t),))
    r = cur.fetchone()
    if not r:
        return None, f'planilha: conta reduzida {t} não existe no plano carregado'
    if not r[1]:
        return None, f'planilha: conta reduzida {t} ({r[0]}) é sintética'
    return r[0], None


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

    extras = []
    if caminho.lower().endswith('.json'):
        with open(caminho, encoding='utf-8') as f:
            eventos = json.load(f)
    else:
        eventos, extras = ler_excel(caminho)

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

    # A conta da planilha é UMA por evento e vai em `conta_debito`: é "a conta
    # do evento" da mecânica (despesa quando DESPESA=SIM; o que o pagamento
    # debita quando PROVISÃO=NÃO). A contrapartida é conta fixa, não vem daqui.
    linhas, recusadas = [], []
    for e in eventos:
        conta, motivo = traduzir_conta(cur, e.get('conta_reduzida'))
        if motivo:
            recusadas.append((e['evento'], e['descricao'], e.get('conta_reduzida'), motivo))
        linhas.append((
            e['evento'], e['descricao'],
            conta, None,
            e['tem_nota'], e['contabiliza_despesa'], e['contabiliza_provisao'],
            e['aproveita_credito'], e['importar_fiscal'], e['validar_simples'],
            e['grupo_importacao'], motivo, AUTOR,
        ))

    execute_values(cur, """
        INSERT INTO contabil_evento_conta
            (evento, descricao, conta_debito, conta_credito, tem_nota,
             contabiliza_despesa, contabiliza_provisao, aproveita_credito,
             importar_fiscal, validar_simples, grupo_importacao, observacao,
             usuario_nome)
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
    com_conta = sum(1 for l in linhas if l[2])
    print(f'   conta_debito preenchida: {com_conta} · em branco: {len(linhas) - com_conta}')
    for ev, desc, red, motivo in recusadas:
        print(f'   ⚠ {ev} {desc[:40]:40} CONTA={red!s:8} -> {motivo}')
    if extras:
        print(f'   {len(extras)} sub-linhas de conta POR CONTRATO ignoradas '
              '(não há tabela para elas ainda):')
        for desc, conta in extras:
            print(f'      {conta!s:>6}  {desc}')

    cur.close()
    conn.close()


if __name__ == '__main__':
    main()
