"""
Aplica uma rodada de ajustes da configuração do Contábil a partir de um JSON:
eventos, contas fixas e conta por contrato — com as mesmas travas da tela.

    python aplicar_ajustes_contabil.py ajustes.json            simulação: mostra o que mudaria
    python aplicar_ajustes_contabil.py ajustes.json --gravar   grava (uma transação)

Existe para levar ao servidor, num comando, uma rodada de decisões que na tela
seriam várias edições — e a conta por contrato nem tem tela. O JSON é dado da
contabilidade: fica fora do git (o repositório é público) e vai por docker cp,
como os outros (HANDOFF-CONTABIL §14.4).

    {"autor": "arquivo de julho do escritório (16/10/2026)",
     "eventos":      [{"evento": "5228", "conta_debito_cod": 1451,
                       "contabiliza_provisao": "NAO", "observacao": "…"}],
     "contas_fixas": [{"chave": "JUROS_TD", "codigo_reduzido": 374, "observacao": "…"}],
     "contratos":    [{"evento": "5516", "numlancto": "6865", "conta_cod": 835,
                       "fornecedor": "…", "nfiscal": null, "observacao": "…"}]}

No evento, só os campos presentes mudam; o resto vem da linha corrente (a tabela
é append-only: a linha nova é o valor corrente e a antiga fica no histórico).
O que já está com o valor pedido é pulado — rodar duas vezes não duplica nada.
Conta que não existe no plano ou é sintética aborta TUDO antes de gravar.
"""

import json
import os
import sys

import psycopg2
from dotenv import load_dotenv

load_dotenv()

CAMPOS_EVENTO = ('conta_debito_cod', 'conta_credito_cod', 'contabiliza_despesa', 'contabiliza_provisao')
FLAGS = {'contabiliza_despesa': ('SIM', 'NAO', 'PARCIAL'), 'contabiliza_provisao': ('SIM', 'NAO')}


def _norm_nl(v):
    return str(v or '').strip().lstrip('0') or '0'


def main():
    args = [a for a in sys.argv[1:] if a != '--gravar']
    gravar = '--gravar' in sys.argv
    if len(args) != 1 or not os.path.exists(args[0]):
        print('Uso: python aplicar_ajustes_contabil.py ajustes.json [--gravar]')
        sys.exit(1)
    with open(args[0], encoding='utf-8') as f:
        aj = json.load(f)
    autor = (aj.get('autor') or '').strip()
    if not autor:
        print('❌ O JSON precisa de "autor" — é o que a tela mostra como quem mudou.')
        sys.exit(1)

    conn = psycopg2.connect(
        host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT', 5432),
        dbname=os.getenv('DB_NAME'), user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'))
    cur = conn.cursor()
    cur.execute('SELECT codigo_reduzido, classificacao, descricao, analitica FROM contabil_plano_contas')
    plano = {r[0]: r[1:] for r in cur.fetchall()}

    erros, plano_acoes = [], []

    def conta_ok(cod, onde):
        p = plano.get(cod)
        if not p:
            erros.append(f'{onde}: a conta {cod} não existe no plano')
        elif not p[2]:
            erros.append(f'{onde}: a conta {cod} ({p[0]}) é sintética')
        return p

    def rot(cod):
        p = plano.get(cod)
        return f'{cod} {p[1]}' if p else str(cod)

    # ── eventos (append-only) ───────────────────────────────────────────────
    cur.execute("""SELECT DISTINCT ON (evento) evento, descricao, conta_debito, conta_credito,
                          conta_debito_cod, conta_credito_cod, tem_nota, contabiliza_despesa,
                          contabiliza_provisao, aproveita_credito, importar_fiscal, validar_simples,
                          grupo_importacao
                     FROM contabil_evento_conta ORDER BY evento, criado_em DESC, id DESC""")
    cols = [d[0] for d in cur.description]
    atual = {r[0].strip(): dict(zip(cols, r)) for r in cur.fetchall()}
    for e in aj.get('eventos', []):
        ev = str(e['evento']).strip()
        cur_ev = atual.get(ev)
        if not cur_ev:
            erros.append(f'evento {ev}: não tem configuração — não se inventa evento por aqui')
            continue
        novo = dict(cur_ev)
        for c in CAMPOS_EVENTO:
            if c in e:
                novo[c] = e[c]
        for c, ok in FLAGS.items():
            if novo.get(c) and novo[c] not in ok:
                erros.append(f'evento {ev}: {c} = {novo[c]!r} (permitido {ok})')
        for c in ('conta_debito_cod', 'conta_credito_cod'):
            if novo.get(c):
                p = conta_ok(novo[c], f'evento {ev}')
                if p:   # snapshot legível da classificação, como a tela grava
                    novo[c.replace('_cod', '')] = p[0]
        mudou = [c for c in CAMPOS_EVENTO if novo.get(c) != cur_ev.get(c)]
        if not mudou:
            print(f'   = evento {ev}: já está assim')
            continue
        print(f'   ~ evento {ev} {cur_ev["descricao"] or ""}: ' + ', '.join(
            f'{c} {rot(cur_ev.get(c)) if c.endswith("_cod") else cur_ev.get(c)} -> '
            f'{rot(novo.get(c)) if c.endswith("_cod") else novo.get(c)}' for c in mudou))
        plano_acoes.append(('evento', novo, e.get('observacao')))

    # ── contas fixas (UPDATE, como a tela) ───────────────────────────────────
    cur.execute('SELECT chave, codigo_reduzido FROM contabil_conta_fixa')
    fixas = dict(cur.fetchall())
    for c in aj.get('contas_fixas', []):
        ch, cod = c['chave'], c['codigo_reduzido']
        if ch not in fixas:
            erros.append(f'conta fixa {ch}: chave não existe (crie pela semente/init_db)')
            continue
        p = conta_ok(cod, f'conta fixa {ch}')
        if fixas[ch] == cod:
            print(f'   = conta fixa {ch}: já está {rot(cod)}')
            continue
        print(f'   ~ conta fixa {ch}: {rot(fixas[ch])} -> {rot(cod)}')
        plano_acoes.append(('fixa', dict(c, classificacao=p[0] if p else None), c.get('observacao')))

    # ── conta por contrato (append-only) ─────────────────────────────────────
    cur.execute("""SELECT DISTINCT ON (evento, numlancto) evento, numlancto, conta_cod
                     FROM contabil_contrato_conta ORDER BY evento, numlancto, criado_em DESC, id DESC""")
    contratos = {(r[0].strip(), _norm_nl(r[1])): r[2] for r in cur.fetchall()}
    for c in aj.get('contratos', []):
        k = (str(c['evento']).strip(), _norm_nl(c['numlancto']))
        conta_ok(c['conta_cod'], f'contrato {k[0]}/{k[1]}')
        if contratos.get(k) == c['conta_cod']:
            print(f'   = contrato {k[0]}/{k[1]}: já está {rot(c["conta_cod"])}')
            continue
        print(f'   ~ contrato {k[0]}/{k[1]} {c.get("fornecedor") or ""}: '
              f'{rot(contratos[k]) if k in contratos else "(novo)"} -> {rot(c["conta_cod"])}')
        plano_acoes.append(('contrato', c, c.get('observacao')))

    if erros:
        print('\n❌ Nada gravado:')
        for e in erros:
            print('   ' + e)
        sys.exit(1)
    if not plano_acoes:
        print('\nNada a mudar.')
        return
    if not gravar:
        print(f'\nSimulação: {len(plano_acoes)} mudança(s). Para gravar, rode de novo com --gravar.')
        return

    for tipo, x, obs in plano_acoes:
        if tipo == 'evento':
            cur.execute("""
                INSERT INTO contabil_evento_conta
                    (evento, descricao, conta_debito, conta_credito, conta_debito_cod, conta_credito_cod,
                     tem_nota, contabiliza_despesa, contabiliza_provisao, aproveita_credito,
                     importar_fiscal, validar_simples, grupo_importacao, observacao, usuario_nome)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (x['evento'], x['descricao'], x['conta_debito'], x['conta_credito'],
                  x['conta_debito_cod'], x['conta_credito_cod'], x['tem_nota'],
                  x['contabiliza_despesa'], x['contabiliza_provisao'], x['aproveita_credito'],
                  x['importar_fiscal'], x['validar_simples'], x['grupo_importacao'], obs, autor))
        elif tipo == 'fixa':
            cur.execute("""
                UPDATE contabil_conta_fixa
                   SET codigo_reduzido = %s, classificacao = %s, observacao = %s,
                       usuario_nome = %s, atualizado_em = NOW()
                 WHERE chave = %s
            """, (x['codigo_reduzido'], x['classificacao'], obs, autor, x['chave']))
        else:
            cur.execute("""
                INSERT INTO contabil_contrato_conta
                    (evento, numlancto, conta_cod, nfiscal, fornecedor, observacao, usuario_nome)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
            """, (str(x['evento']).strip(), str(x['numlancto']).strip(), x['conta_cod'],
                  x.get('nfiscal'), x.get('fornecedor'), obs, autor))
    conn.commit()
    cur.close(); conn.close()
    print(f'\n✅ {len(plano_acoes)} mudança(s) gravada(s), autor "{autor}".')
    print('   No app: a prévia guardada do arquivo vale 30 min — use "Refazer" para ver já.')


if __name__ == '__main__':
    main()
