"""
Carrega o plano de contas da contabilidade (PERSETO) em `contabil_plano_contas`.

Rode quando o plano mudar (idempotente — substitui a tabela inteira):
    python seed_plano_contas.py ["..\\Rizza\\PLANO DE CONTAS RIZZA.pdf"]
    python seed_plano_contas.py plano.pdf --exportar plano_contas.json
    python seed_plano_contas.py plano_contas.json          (só stdlib + psycopg2)

Por que em tabela e não lendo o arquivo na hora: a tela de eventos preenche a
conta por CAMPO DE ESCOLHA, nunca por digitação. `4.1.6.01.013` digitado à mão
numa tarde cansada quebra o arquivo de importação sem ninguém saber por quê.
Para o select existir, o plano precisa estar no banco — o app roda em container
e não alcança o arquivo da máquina de ninguém.

═══════════════════════════════════════════════════════════════════════════════
SOBRE O ARQUIVO (HANDOFF-CONTABIL §12 e §13)
───────────────────────────────────────────────────────────────────────────────
A fonte é o relatório "PLANO DE CONTAS" do Domínio, em PDF: Código · T ·
Classificação · Nome · Grau. O de 14/10/2026 tem 2.295 contas.

Até 29/09/2026 a fonte era `rizza para teste.xls`, que é um BALANCETE: só lista
conta com saldo/movimento (312). Por isso o leitor de .xls saiu — carregá-lo de
novo apagaria 1.983 contas do plano em silêncio.

A CHAVE é o código reduzido. A classificação NÃO é única: `2.1.3.01.001` abriga
320 contas (um fornecedor cada), `1.1.2.01.001` 168 clientes, `4.2.2.04.0024`
duas contas diferentes. E é o código que vai no arquivo `Z;data;D;C;...`.

Analítica = coluna T do relatório (S = sintética). A regra antiga ("folha da
árvore") discorda do Domínio em 48 contas.

Extração por COORDENADA: o texto corrido tem "1" soltos do sombreado das linhas
(x < 10) e a primeira conta de algumas páginas fica em y = 76,5 — um corte de
cabeçalho em y < 80 engoliu 5 contas na primeira tentativa.
═══════════════════════════════════════════════════════════════════════════════
"""

import collections
import json
import os
import sys

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

PADRAO = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      '..', 'Rizza', 'PLANO DE CONTAS RIZZA.pdf')
MINIMO_CONTAS = 2000   # o plano tinha 2.283 em 29/09/2026 e 2.295 em 14/10/2026
CNPJ_RIZZA = '02.572.512/0001-58'

# Limites das colunas no PDF do Domínio (pontos). Medidos nos relatórios de
# 29/09 e 14/10/2026 — os dois idênticos.
_X_MIN, _X_COD, _X_T, _X_CLASSIF, _X_NOME = 10, 44, 60, 200, 520
_Y_CABECALHO = 70

# Conta criada pela contabilidade DEPOIS do último PDF, informada por mensagem.
# Entra se o PDF não a trouxer; quando vier um plano reemitido que a contenha,
# vale a do PDF (com a classificação certa). Sem isto, o seed abortaria: a
# configuração já usa estas contas e elas "sumiriam" do plano.
# Vem de `contabil_seed.json` (fora do git: é dado da Rizza, e o repositório é
# público) — seção contas_avulsas: codigo_reduzido · descricao · origem.
_SEED = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'contabil_seed.json')


def _contas_avulsas():
    if not os.path.exists(_SEED):
        return []
    with open(_SEED, encoding='utf-8') as f:
        return json.load(f).get('contas_avulsas', [])


def ler_pdf(caminho):
    """Lê o relatório do Domínio. Precisa de PyMuPDF (está na imagem)."""
    import fitz
    doc = fitz.open(caminho)
    if CNPJ_RIZZA not in doc[0].get_text() or 'PLANO DE CONTAS' not in doc[0].get_text():
        raise ValueError(f'O PDF não é o plano de contas da Rizza ({CNPJ_RIZZA}).')

    linhas, numeros_na_coluna = [], 0
    for pagina in doc:
        por_y = collections.defaultdict(list)
        for w in pagina.get_text('words'):
            if w[1] < _Y_CABECALHO or w[0] < _X_MIN:
                continue
            por_y[round(w[1])].append(w)
            if _X_MIN <= w[0] < _X_COD and w[4].isdigit():
                numeros_na_coluna += 1
        for y in sorted(por_y):
            ws = sorted(por_y[y], key=lambda w: w[0])
            cod = [w[4] for w in ws if _X_MIN <= w[0] < _X_COD]
            if not cod or not cod[0].isdigit():
                continue
            classif = ' '.join(w[4] for w in ws if _X_T <= w[0] < _X_CLASSIF)
            grau = [w[4] for w in ws if w[0] >= _X_NOME]
            linhas.append({
                'codigo_reduzido': int(cod[0]),
                'classificacao': classif,
                'descricao': ' '.join(w[4] for w in ws if _X_CLASSIF <= w[0] < _X_NOME),
                'niveis': classif.count('.') + 1,
                'analitica': not any(w[4] == 'S' for w in ws if _X_COD <= w[0] < _X_T),
                'grupo': classif[:1],
                '_grau': int(grau[0]) if grau and grau[0].isdigit() else None,
            })

    # As conferências que provaram a extração (§12.3). Qualquer uma falhando =
    # layout mudou ou o arquivo veio cortado: aborta antes de tocar no banco.
    erros = []
    if len(linhas) != numeros_na_coluna:
        erros.append(f'{len(linhas)} contas lidas, mas {numeros_na_coluna} números na '
                     f'coluna Código — alguma linha ficou de fora.')
    torto = [l for l in linhas if l['_grau'] != l['niveis']]
    if torto:
        erros.append(f'{len(torto)} contas com Grau diferente do nº de níveis '
                     f'(ex.: {torto[0]["codigo_reduzido"]} {torto[0]["classificacao"]}).')
    rep = [c for c, n in collections.Counter(l['codigo_reduzido'] for l in linhas).items() if n > 1]
    if rep:
        erros.append(f'Código reduzido repetido: {rep[:10]}')
    if erros:
        raise ValueError('Extração do PDF não conferiu:\n   ' + '\n   '.join(erros))
    for l in linhas:
        del l['_grau']
    return _validar(linhas)


def ler_json(caminho):
    """Carrega o que o --exportar gerou. Só stdlib — roda em qualquer lugar."""
    with open(caminho, encoding='utf-8') as f:
        linhas = json.load(f)
    if linhas and 'codigo_reduzido' not in linhas[0]:
        raise ValueError('JSON em formato antigo (do balancete). Exporte de novo a partir do PDF.')
    return _validar(linhas)


def _com_avulsas(linhas):
    tem = {l['codigo_reduzido'] for l in linhas}
    for a in _contas_avulsas():
        if a['codigo_reduzido'] not in tem:
            print(f"   + conta avulsa {a['codigo_reduzido']} {a['descricao']} ({a['origem']}) "
                  f"— classificação a confirmar")
            linhas.append({'codigo_reduzido': a['codigo_reduzido'], 'classificacao': 'a confirmar',
                           'descricao': a['descricao'], 'niveis': 5, 'analitica': True, 'grupo': '1'})
    return linhas


def _validar(linhas):
    linhas = _com_avulsas(linhas)
    if len(linhas) < MINIMO_CONTAS:
        raise ValueError(
            f'Só {len(linhas)} contas (mínimo {MINIMO_CONTAS}). Arquivo truncado, '
            f'ou é balancete e não plano — abortando antes de gravar.')
    sem = [l for l in linhas if not l.get('codigo_reduzido') or not l.get('classificacao')]
    if sem:
        raise ValueError(f'{len(sem)} contas sem código ou classificação.')
    return linhas


def main():
    args = [a for a in sys.argv[1:] if a != '--exportar']
    exportar = sys.argv[sys.argv.index('--exportar') + 1] if '--exportar' in sys.argv else None
    if exportar and exportar in args:
        args.remove(exportar)
    caminho = args[0] if args else PADRAO

    if not os.path.exists(caminho):
        print(f'❌ Arquivo não encontrado: {caminho}')
        print('   Uso: python seed_plano_contas.py [plano.pdf|plano.json] [--exportar saida.json]')
        sys.exit(1)

    ext = caminho.lower().rsplit('.', 1)[-1]
    if ext in ('xls', 'xlsx'):
        print('❌ Planilha recusada: o .xls que veio da contabilidade é BALANCETE (só contas')
        print('   com movimento). Use o PDF "PLANO DE CONTAS" do Domínio — ver o docstring.')
        sys.exit(1)
    linhas = ler_json(caminho) if ext == 'json' else ler_pdf(caminho)

    if exportar:
        with open(exportar, 'w', encoding='utf-8') as f:
            json.dump(linhas, f, ensure_ascii=False, indent=1)
        print(f'✅ {len(linhas)} contas exportadas para {exportar}.')
        return

    conn = psycopg2.connect(
        host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT', 5432),
        dbname=os.getenv('DB_NAME'), user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'))
    cur = conn.cursor()

    # Conta que a configuração já usa e que sumiu do plano novo: não pode sair
    # calada — o arquivo de importação passaria a referenciar código morto.
    novos = {l['codigo_reduzido'] for l in linhas}
    cur.execute("""
        SELECT DISTINCT c FROM (
            SELECT conta_debito_cod  AS c FROM contabil_evento_conta
            UNION SELECT conta_credito_cod FROM contabil_evento_conta
            UNION SELECT codigo_reduzido   FROM contabil_conta_fixa
            UNION SELECT conta_cod         FROM contabil_contrato_conta
        ) x WHERE c IS NOT NULL
    """)
    orfas = sorted(r[0] for r in cur.fetchall() if r[0] not in novos)
    if orfas:
        print(f'❌ O plano novo não tem {len(orfas)} conta(s) que a configuração usa: {orfas}')
        print('   Abortado sem gravar. Confira com a contabilidade antes de trocar o plano.')
        sys.exit(1)

    # Substituição total dentro de uma transação: se algo falhar, o plano
    # antigo continua de pé. Mesmo princípio das cargas do SSW.
    cur.execute('DELETE FROM contabil_plano_contas')
    execute_values(cur, """
        INSERT INTO contabil_plano_contas
            (codigo_reduzido, classificacao, descricao, niveis, analitica, grupo)
        VALUES %s
    """, [(l['codigo_reduzido'], l['classificacao'], l['descricao'],
           l['niveis'], l['analitica'], l['grupo']) for l in linhas])
    conn.commit()

    ana = sum(1 for l in linhas if l['analitica'])
    grupos = collections.Counter(l['grupo'] for l in linhas)
    print(f'✅ {len(linhas)} contas gravadas em contabil_plano_contas '
          f'({ana} analíticas, {len(linhas) - ana} sintéticas).')
    print('   por grupo: ' + ', '.join(f'{g}={n}' for g, n in sorted(grupos.items())))
    cur.close()
    conn.close()


if __name__ == '__main__':
    main()
