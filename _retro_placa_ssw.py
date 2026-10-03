# -*- coding: utf-8 -*-
"""Devolve às cargas do robô a placa COMO ESTÁ NO SSW (03/10/2026).

Até 03/10 o robô convertia a placa do manifesto para Mercosul antes de gravar (GZQ3080 virava
GZQ3A80) — uma placa que não existe no SSW, na 3S nem no caminhão. A conversão continua como
CHAVE interna (casar as duas grafias do mesmo veículo); o que se grava e se mostra é a do SSW.

Regra, por coluna (cavalo_placa, carreta1_placa, carreta2_placa):
  * carga do robô com manifesto: a placa do PRÓPRIO manifesto (carreta 2: a do CTRB de origem);
    sem manifesto no BI (cancelado): a grafia mais recente do veículo, como a perna vazia
  * perna vazia (V-): a grafia do manifesto mais recente daquele veículo
  * só troca quando é o MESMO veículo (mesma chave Mercosul) e a grafia difere;
    chave diferente é reportada e NÃO mexida (carga editada à mão, troca de veículo)
  * carga lançada à mão: não toca — a placa é a que a pessoa digitou
Toda troca vai para o embarques_cargas_log como 'Correção placa SSW'.

    python -X utf8 _retro_placa_ssw.py              # dry-run: só conta e lista
    python -X utf8 _retro_placa_ssw.py --aplicar    # grava (uma transação)
Banco = DB_NAME do ambiente (no lab: DB_NAME=rizza_lab_1003).
"""
import os, sys
from collections import Counter
os.environ.update(START_WORKER='false', EMBARQUES_AUTO='false', PGR_SYNC_CADASTRO='false')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server, embarques_auto as ea, placas as pl

APLICAR = '--aplicar' in sys.argv
USUARIO = 'Correção placa SSW'
COLS = ('cavalo_placa', 'carreta1_placa', 'carreta2_placa')

tok = server.get_token()
mans = ea._dax(tok, f"EVALUATE SELECTCOLUMNS(FILTER({ea.M}, {ea.M}[data_emissao] >= DATE(2026,5,1)), "
                    f"\"k\",{ea.M}[CHAVE_MANIFESTO], \"d\",{ea.M}[data_emissao], "
                    f"\"cav\",{ea.M}[placa_cavalo], \"car\",{ea.M}[placa_carreta])")
ctrbs = ea._dax(tok, f"EVALUATE SELECTCOLUMNS(FILTER({ea.OS_}, {ea.OS_}[emissao] >= DATE(2026,5,1) "
                     f"&& NOT(ISBLANK({ea.OS_}[placa_carreta_2]))), \"ctrb\",{ea.OS_}[ctrb], \"c2\",{ea.OS_}[placa_carreta_2])")
por_man = {ea._norm(m.get('k')): m for m in mans if m.get('k')}
c2_por_ctrb = {ea._chave_ctrb(c.get('ctrb')): pl.limpar(c.get('c2')) for c in ctrbs if c.get('c2')}

# grafia mais recente de cada veículo no SSW (para as pernas vazias, que não têm manifesto)
recente = {}
for m in sorted(mans, key=lambda m: str(m.get('d') or '')):
    for campo in ('cav', 'car'):
        crua = pl.limpar(m.get(campo))
        if crua:
            recente[pl.mercosul(crua)] = crua

conn = server.get_db(); cur = conn.cursor()
cur.execute("""SELECT id, numero, COALESCE(criada_por_robo, FALSE), COALESCE(viagem_vazia, FALSE),
                      manifesto_origem, ctrb_origem, cavalo_placa, carreta1_placa, carreta2_placa
                 FROM embarques_cargas ORDER BY id""")
n = Counter(); trocas, divergentes = [], []
for cid, num, robo, vazia, mf, ctrb, *atuais in cur.fetchall():
    if not robo:
        n['manual — não toca'] += 1
        continue
    m = None if vazia else por_man.get(ea._norm(mf))
    if not m:
        # perna vazia, ou carga cujo manifesto saiu do BI (cancelado): a grafia mais recente do
        # veículo no SSW — mesma chave, então é o mesmo veículo
        if not vazia:
            n['robô sem manifesto no BI — grafia mais recente do veículo'] += 1
        ssw = {c: recente.get(pl.mercosul(v)) if v else None for c, v in zip(COLS, atuais)}
    else:
        ssw = {'cavalo_placa': pl.limpar(m.get('cav')), 'carreta1_placa': pl.limpar(m.get('car')),
               'carreta2_placa': c2_por_ctrb.get(ea._chave_ctrb(ctrb)) if ctrb else None}
    for col, atual in zip(COLS, atuais):
        novo = ssw.get(col)
        if not atual or not novo or atual == novo:
            continue
        if pl.mercosul(atual) != pl.mercosul(novo):
            divergentes.append((num, col, atual, novo))
            continue
        trocas.append((cid, num, col, atual, novo))
        n[f"troca {col}{' (perna vazia)' if vazia else ''}"] += 1

print(f"BANCO {os.getenv('DB_NAME')} · {'APLICAR' if APLICAR else 'DRY-RUN'}")
for k, v in sorted(n.items()):
    print(f"  {v:5}  {k}")
print(f"  {len({t[0] for t in trocas}):5}  cargas com alguma troca")
print(f"  {len(divergentes):5}  veículo DIFERENTE do manifesto (não mexido)")
for d in divergentes[:15]:
    print('     ', *d)
print('  exemplos:', [t[1:] for t in trocas[:8]])

# ── PGR: eventos já apurados guardam o par do manifesto (convertido até 03/10)
pgr = []
cur.execute("SELECT to_regclass('pgr_eventos') IS NOT NULL")
if cur.fetchone()[0]:
    for col in ('placa_cavalo', 'placa_carreta'):
        cur.execute(f"SELECT DISTINCT {col} FROM pgr_eventos WHERE {col} IS NOT NULL AND {col} <> ''")
        for (atual,) in cur.fetchall():
            novo = recente.get(pl.mercosul(atual))
            if novo and novo != atual:
                pgr.append((col, atual, novo))
print(f"  {len(pgr):5}  placas distintas a corrigir em pgr_eventos")

if APLICAR:
    for cid, num, col, atual, novo in trocas:
        cur.execute(f"UPDATE embarques_cargas SET {col} = %s WHERE id = %s", (novo, cid))
        cur.execute("INSERT INTO embarques_cargas_log (carga_id, usuario_nome, campo, valor_anterior, valor_novo) "
                    "VALUES (%s,%s,%s,%s,%s)", (cid, USUARIO, col, atual, novo))
    for col, atual, novo in pgr:
        cur.execute(f"UPDATE pgr_eventos SET {col} = %s WHERE {col} = %s", (novo, atual))
    conn.commit()
    print(f"GRAVADO: {len(trocas)} cargas-campo · {len(pgr)} placas no PGR")
    # o cache de manifestos do PGR é só cache: refaz do BI já com a placa do SSW (a chave única
    # inclui a placa — atualizar no lugar colidiria com a linha nova)
    cur.execute("SELECT to_regclass('pgr_manifestos') IS NOT NULL")
    if cur.fetchone()[0]:
        cur.execute("DELETE FROM pgr_manifestos"); conn.commit()
        print('pgr_manifestos refeito:', server.sincronizar_manifestos_pgr(dias=2 * server.PGR_MANIFESTOS_DIAS))
conn.close()
