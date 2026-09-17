# -*- coding: utf-8 -*-
"""Regressão do envio com banco — em TABELAS TEMPORÁRIAS (nada fica gravado).

Caso de 17/09/2026: no primeiro boot em produção todas as pendências eram "recém-vistas",
a carência de 2 h contava a partir daí e o resumo saiu "Tudo em dia" com 48 pendências
abertas. A carência agora é pela idade do documento.

    python -X utf8 _teste_ciot_banco.py
"""
from datetime import datetime, timedelta

import server
import ciot_conferencia as cc

falhas = []


def confere(nome, cond, extra=''):
    print(f"{'OK  ' if cond else 'FALHA'} {nome}" + ('' if cond else f' — {extra}'))
    if not cond:
        falhas.append(nome)


def pend(doc, tipo, emissao):
    return {'chave': f'{tipo}:{doc}', 'tipo': tipo, 'documento': doc, 'ctrb': doc, 'manifesto': '',
            'filial': doc[:3], 'emissao': emissao, 'tipo_operacao': 'Agregado', 'placa': 'AAA1B23',
            'motorista': '', 'detalhe': 'x'}


def main():
    agora = datetime(2026, 9, 17, 20, 0)                     # 17:00 BRT
    brt = agora - timedelta(hours=3)
    conn = server.get_db()
    cur = conn.cursor()
    try:
        for tb in ('ciot_pendencias', 'ciot_envios', 'ciot_tokens'):
            cur.execute(f"CREATE TEMP TABLE {tb} (LIKE public.{tb} INCLUDING ALL)")

        lote = [
            pend('UDI000001-1', 'sem_ciot', brt - timedelta(days=5)),               # backlog
            pend('UDI000002-9', 'sem_ciot', brt - timedelta(minutes=30)),           # emitido agora há pouco
            pend('UDI000003-7', 'ctrb_sem_manifesto', brt - timedelta(hours=3)),    # passou da carência
            pend('UDI000100-1', 'manifesto_sem_ctrb', datetime(brt.year, brt.month, brt.day)),  # MDF de hoje
            pend('UDI000101-1', 'manifesto_sem_ctrb', datetime(brt.year, brt.month, brt.day) - timedelta(days=1)),
        ]
        cc.gravar(cur, lote, agora)                          # 1º boot: tudo "visto agora"

        carencia = (cc._CARENCIA_SQL, {'corte_utc': agora - timedelta(hours=cc.CARENCIA_H),
                                       'corte_brt': brt - timedelta(hours=cc.CARENCIA_H),
                                       'hoje_brt': brt.date()})
        docs = {p['documento'] for p in cc._pendentes(cur, f'resolvido_em IS NULL AND {carencia[0]}', carencia[1])}
        confere('backlog entra no 1º resumo', 'UDI000001-1' in docs, docs)
        confere('CTRB de 30 min espera a carência', 'UDI000002-9' not in docs, docs)
        confere('CTRB de 3 h entra', 'UDI000003-7' in docs, docs)
        confere('MDF de hoje espera o dia seguinte', 'UDI000100-1' not in docs, docs)
        confere('MDF de ontem entra', 'UDI000101-1' in docs, docs)

        # o resumo mostra o que existe (não "Tudo em dia")
        p = cc._pendentes(cur, f'resolvido_em IS NULL AND {carencia[0]}', carencia[1])
        d = cc.dados_aviso('resumo', p, len(lote), 0, brt)
        confere('resumo não sai em branco', d['titulo'] == 'Resumo do dia · tudo em aberto' and d['contadores'], d['titulo'])

        # prévia do avisar (não envia nem marca)
        import os, tempfile                              # a prévia grava o PNG na pasta atual
        volta = os.getcwd()
        os.chdir(tempfile.gettempdir())
        try:
            tipo, n = cc.avisar(cur, agora, forcar_resumo=True, so_mostrar=True)
        finally:
            os.chdir(volta)
        confere('avisar vê 3 documentos', (tipo, n) == ('resumo', 3), (tipo, n))

        # duas horas depois, o CTRB recente entra nas novas
        depois = agora + timedelta(hours=2)
        c2 = (cc._CARENCIA_SQL, {'corte_utc': depois - timedelta(hours=cc.CARENCIA_H),
                                 'corte_brt': depois - timedelta(hours=3 + cc.CARENCIA_H),
                                 'hoje_brt': (depois - timedelta(hours=3)).date()})
        docs2 = {p['documento'] for p in cc._pendentes(cur, f'resolvido_em IS NULL AND {c2[0]}', c2[1])}
        confere('depois da carência o CTRB recente entra', 'UDI000002-9' in docs2, docs2)
        confere('MDF de hoje entra 2 h depois de visto', 'UDI000100-1' in docs2, docs2)
    finally:
        conn.rollback()
        conn.close()

    print(f'\n{len(falhas)} falha(s)')
    return not falhas


if __name__ == '__main__':
    raise SystemExit(0 if main() else 1)
