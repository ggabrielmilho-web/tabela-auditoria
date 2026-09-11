# -*- coding: utf-8 -*-
"""Snapshot e restore das tabelas DERIVADAS de embarques — a rede de segurança da §24.

    python -X utf8 _snapshot_embarques.py criar                       # schema snap_AAAAMMDD_HHMM
    python -X utf8 _snapshot_embarques.py listar
    python -X utf8 _snapshot_embarques.py diff     snap_20260912_1500  # o que mudou desde o snapshot
    python -X utf8 _snapshot_embarques.py restaurar snap_20260912_1500 --ids 709,731    (--aplicar)
    python -X utf8 _snapshot_embarques.py restaurar snap_20260912_1500 --desde-log '2026-09-12 15:00' (--aplicar)
    python -X utf8 _snapshot_embarques.py restaurar snap_20260912_1500 --chave  (--aplicar)   # so o que a §24 tocou

O que entra: embarques_cargas, _destinos, _rota, _log, _rastreio_kpi. O que NUNCA entra:
embarques_posicoes_* e embarques_rastreio_dia — posição é fato bruto (§23.2) e restaurar
essas tabelas apaga GPS que não volta.

RESTORE É POR ID, NÃO POR TABELA. Entre o snapshot e o restore o worker e o robô diário
continuam gravando (posições, cargas novas, chegadas). TRUNCATE + INSERT jogaria isso fora.
`--ids` devolve só as cargas indicadas ao estado do snapshot; `--desde-log` descobre os ids
pelo `embarques_cargas_log` (toda escrita do robô vai lá com autor e instante). Carga que
não existia no snapshot (criada depois) NÃO é apagada — é listada, e apagar é decisão sua.

Sem `--aplicar` só mostra. Roda dentro do container (as env DB_* já estão lá) ou local.
"""
import os
import sys
import argparse
from datetime import datetime

import psycopg2

if not os.getenv('DB_PASSWORD'):
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
    except Exception:
        pass

TABELAS = ('embarques_cargas', 'embarques_cargas_destinos', 'embarques_cargas_rota',
           'embarques_cargas_log', 'embarques_cargas_rastreio_kpi')
CAMPOS_CARGA = None   # preenchido em runtime (todas as colunas de embarques_cargas)


def conectar():
    return psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                            user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))


def criar(cur, nome=None):
    nome = nome or 'snap_' + datetime.now().strftime('%Y%m%d_%H%M')
    cur.execute(f'CREATE SCHEMA {nome}')
    for t in TABELAS:
        cur.execute(f'CREATE TABLE {nome}.{t} AS TABLE public.{t}')
        cur.execute(f'SELECT COUNT(*) FROM {nome}.{t}')
        print(f'  {nome}.{t:34} {cur.fetchone()[0]:7} linhas')
    cur.execute(f"COMMENT ON SCHEMA {nome} IS 'snapshot embarques {datetime.now().isoformat()} (_snapshot_embarques.py)'")
    print(f'snapshot criado: {nome}')
    return nome


def listar(cur):
    cur.execute("SELECT nspname, obj_description(oid, 'pg_namespace') FROM pg_namespace WHERE nspname LIKE 'snap_%' ORDER BY 1")
    for n, c in cur.fetchall():
        print(f'  {n:24} {c or ""}')


def colunas(cur, tabela, schema='public'):
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position",
                (schema, tabela))
    return [r[0] for r in cur.fetchall()]


def diff(cur, snap):
    """Cargas cujo estado difere do snapshot (só as colunas que existem nos dois)."""
    cols = [c for c in colunas(cur, 'embarques_cargas', snap) if c in colunas(cur, 'embarques_cargas')]
    comp = ' OR '.join(f'p.{c} IS DISTINCT FROM s.{c}' for c in cols if c != 'atualizado_em')
    cur.execute(f"""SELECT p.id, p.numero, s.status, p.status, s.encerrada_motivo, p.encerrada_motivo
                      FROM public.embarques_cargas p JOIN {snap}.embarques_cargas s ON s.id = p.id
                     WHERE {comp} ORDER BY p.id""")
    rows = cur.fetchall()
    print(f'cargas alteradas desde {snap}: {len(rows)}')
    for r in rows:
        print(f'  {r[0]:6} {r[1]:16} {r[2]:12} -> {r[3]:12}  {r[4] or "":26} -> {r[5] or ""}')
    cur.execute(f"SELECT id, numero, status FROM public.embarques_cargas WHERE id NOT IN (SELECT id FROM {snap}.embarques_cargas) ORDER BY id")
    novas = cur.fetchall()
    print(f'cargas criadas depois do snapshot (não serão apagadas): {len(novas)}')
    for r in novas[:40]:
        print(f'  {r[0]:6} {r[1]:16} {r[2]}')
    return [r[0] for r in rows]


def ids_tocados_pela_chave(cur):
    """Exatamente o raio de acao da EMBARQUES_CONTINUACAO (§24): carga que ganhou ligacao,
    local de desengate ou o status Continuada. Nao pega o que o worker/robo fizeram de
    legitimo no meio-tempo (chegada, saida, carga nova)."""
    cur.execute("SELECT id FROM embarques_cargas WHERE continua_em IS NOT NULL OR desengate_local IS NOT NULL "
                "OR status = 'Continuada' ORDER BY id")
    return [r[0] for r in cur.fetchall()]


def ids_do_log(cur, desde):
    cur.execute("SELECT DISTINCT carga_id FROM embarques_cargas_log WHERE editado_em >= %s ORDER BY 1", (desde,))
    return [r[0] for r in cur.fetchall()]


def restaurar(cur, snap, ids, aplicar):
    if not ids:
        print('nada a restaurar'); return
    atuais = colunas(cur, 'embarques_cargas'); no_snap = colunas(cur, 'embarques_cargas', snap)
    cols = [c for c in no_snap if c in atuais and c != 'id']
    # coluna que nasceu depois do snapshot (ex.: continua_em) volta a NULL — "estado de antes"
    # inclui não ter o que ainda não existia. Só nas cargas restauradas.
    novas = [c for c in atuais if c not in no_snap]
    sets = ', '.join([f'{c} = s.{c}' for c in cols] + [f'{c} = NULL' for c in novas])
    if novas:
        print(f'  colunas posteriores ao snapshot, zeradas nas restauradas: {novas}')
    print(f'restaurar {len(ids)} cargas de {snap} ({"APLICANDO" if aplicar else "só mostrando"})')
    cur.execute(f"SELECT p.id, p.numero, p.status, s.status FROM public.embarques_cargas p JOIN {snap}.embarques_cargas s ON s.id=p.id WHERE p.id = ANY(%s) ORDER BY p.id", (ids,))
    for r in cur.fetchall():
        print(f'  {r[0]:6} {r[1]:16} {r[2]:12} <- {r[3]}')
    if not aplicar:
        return
    cur.execute(f"UPDATE public.embarques_cargas p SET {sets} FROM {snap}.embarques_cargas s WHERE s.id = p.id AND p.id = ANY(%s)", (ids,))
    n = cur.rowcount
    # destinos/rota/kpi: substitui o conjunto da carga pelo do snapshot
    for t, chave in (('embarques_cargas_destinos', 'carga_id'), ('embarques_cargas_rota', 'carga_id'),
                     ('embarques_cargas_rastreio_kpi', 'carga_id')):
        cur.execute(f"DELETE FROM public.{t} WHERE {chave} = ANY(%s)", (ids,))
        cur.execute(f"INSERT INTO public.{t} SELECT * FROM {snap}.{t} WHERE {chave} = ANY(%s)", (ids,))
    # o log NÃO é restaurado: a trilha de auditoria guarda também o que foi desfeito
    cur.execute("INSERT INTO embarques_cargas_log (carga_id, usuario_nome, campo, valor_anterior, valor_novo) "
                "SELECT id, 'Restore (_snapshot_embarques)', 'status', NULL, %s FROM unnest(%s::int[]) AS id",
                (f'restaurado de {snap}', ids))
    print(f'restauradas: {n}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('acao', choices=['criar', 'listar', 'diff', 'restaurar'])
    ap.add_argument('snap', nargs='?')
    ap.add_argument('--nome')
    ap.add_argument('--ids', help='ids separados por vírgula')
    ap.add_argument('--desde-log', help="instante (UTC) a partir do qual o log identifica as cargas tocadas")
    ap.add_argument('--chave', action='store_true', help='so as cargas tocadas pela EMBARQUES_CONTINUACAO (continua_em / desengate_local / Continuada)')
    ap.add_argument('--aplicar', action='store_true')
    a = ap.parse_args()
    cn = conectar(); cur = cn.cursor()
    try:
        if a.acao == 'criar':
            criar(cur, a.nome); cn.commit()
        elif a.acao == 'listar':
            listar(cur)
        elif a.acao == 'diff':
            diff(cur, a.snap)
        elif a.acao == 'restaurar':
            ids = ([int(x) for x in a.ids.split(',')] if a.ids else ids_tocados_pela_chave(cur) if a.chave
                   else ids_do_log(cur, a.desde_log) if a.desde_log else diff(cur, a.snap))
            restaurar(cur, a.snap, ids, a.aplicar)
            if a.aplicar:
                cn.commit()
            else:
                cn.rollback()
    finally:
        cur.close(); cn.close()
