"""
Migração única (14/10/2026): conta contábil passa a ser o CÓDIGO REDUZIDO.
HANDOFF-CONTABIL §12.3.

    python migrar_contas_reduzido.py            (mostra o que faria)
    python migrar_contas_reduzido.py --gravar

ORDEM — rodar ANTES de carregar o plano novo:
    1. python init_db.py                         (cria as colunas *_cod)
    2. python migrar_contas_reduzido.py --gravar (este)
    3. python seed_plano_contas.py <plano.pdf|plano.json>

Por que antes: até hoje a tabela `contabil_plano_contas` tinha a classificação
como chave (veio do balancete, uma conta por classificação). Foi DELA que a tela
e o importador tiraram cada conta gravada — então a tradução classificação →
código contra ESSA tabela é exata, sem chute. Depois do plano completo a
classificação deixa de ser única (2.1.3.01.001 tem 320 contas) e a tradução
viraria adivinhação; por isso o script se recusa a rodar se o plano novo já
estiver carregado e ainda houver linha sem código.

As tabelas de configuração são append-only. Aqui é UPDATE só da coluna nova,
derivada da que já existe — nenhuma decisão muda, nenhuma linha some.
"""

import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv()


def main():
    gravar = '--gravar' in sys.argv
    conn = psycopg2.connect(
        host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT', 5432),
        dbname=os.getenv('DB_NAME'), user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'))
    cur = conn.cursor()

    cur.execute("SELECT classificacao, codigo_reduzido FROM contabil_plano_contas")
    plano = cur.fetchall()
    por_classif = {}
    for cl, cod in plano:
        por_classif.setdefault(cl, set()).add(cod)
    repetidas = {cl for cl, cods in por_classif.items() if len(cods) > 1}

    pend = []
    for tabela, col_cl, col_cod in [
        ('contabil_evento_conta', 'conta_debito', 'conta_debito_cod'),
        ('contabil_evento_conta', 'conta_credito', 'conta_credito_cod'),
        ('contabil_conta_fixa', 'classificacao', 'codigo_reduzido'),
    ]:
        cur.execute(f"SELECT COUNT(*) FROM {tabela} WHERE {col_cl} IS NOT NULL AND {col_cod} IS NULL")
        pend.append((tabela, col_cl, col_cod, cur.fetchone()[0]))

    total = sum(p[3] for p in pend)
    print(f'Plano carregado: {len(plano)} contas, {len(repetidas)} classificações repetidas.')
    for t, cl, cod, n in pend:
        print(f'   {t}.{cod}: {n} linha(s) sem código')
    if not total:
        print('✅ Nada a migrar — todas as linhas já têm código.')
        return

    if repetidas:
        print(f'❌ O plano carregado já tem classificação repetida ({len(repetidas)}) — é o plano')
        print('   completo, e a tradução classificação → código deixou de ser exata.')
        print('   Rode este script com o plano ANTIGO na tabela (ver ORDEM no docstring).')
        sys.exit(1)

    mapa = {cl: next(iter(cods)) for cl, cods in por_classif.items()}
    sem_mapa = []
    for tabela, col_cl, col_cod, n in pend:
        if not n:
            continue
        cur.execute(f"SELECT DISTINCT {col_cl} FROM {tabela} "
                    f"WHERE {col_cl} IS NOT NULL AND {col_cod} IS NULL")
        for (cl,) in cur.fetchall():
            if cl not in mapa:
                sem_mapa.append((tabela, cl))
                continue
            cur.execute(f"UPDATE {tabela} SET {col_cod} = %s "
                        f"WHERE {col_cl} = %s AND {col_cod} IS NULL", (mapa[cl], cl))
            print(f'   {tabela}.{col_cod}: {cl:<16} -> {mapa[cl]:>5}  ({cur.rowcount} linha(s))')

    if sem_mapa:
        conn.rollback()
        print('❌ Classificação gravada que não existe no plano carregado — nada foi gravado:')
        for t, cl in sem_mapa:
            print(f'   {t}: {cl}')
        sys.exit(1)

    if gravar:
        conn.commit()
        print(f'✅ Migração gravada. Agora: python seed_plano_contas.py <plano novo>')
    else:
        conn.rollback()
        print('ℹ️  Simulação — nada gravado. Rode com --gravar.')
    cur.close()
    conn.close()


if __name__ == '__main__':
    main()
