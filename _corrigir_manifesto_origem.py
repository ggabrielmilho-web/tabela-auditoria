# -*- coding: utf-8 -*-
"""Repreenche `manifesto_origem` das cargas importadas de producao (base LOCAL de analise).

Por que existe: a API /api/embarques/cargas nao devolve `manifesto_origem`, entao as cargas
trazidas da producao ficaram sem a chave. Sem ela o indice unico parcial nao ve nada e o
robo recria a carga (duplicata silenciosa).

Rota A  cavalo + dia                       -> resolve a maioria
Rota B  cadeia da Auditoria, pelo TOMADOR  -> desempata 2+ manifestos no mesmo cavalo/dia
        manifesto -> manifestos_ctrc -> conhecimentos_emitidos[cliente_pagador]
        (o DESTINO do CTRC nao serve: bate com a viagem em so 69%)

NUNCA toca em viagem_vazia: perna vazia nao tem manifesto, NULL ali e correto.

    python -X utf8 _corrigir_manifesto_origem.py            # dry-run
    python -X utf8 _corrigir_manifesto_origem.py --aplicar
"""
import os, sys, unicodedata
from collections import defaultdict
# A pasta do proprio arquivo, nao um caminho cravado: estes scripts precisam rodar
# TAMBEM dentro do container (Linux), que e de onde o robo atemporal corrige os dados
# de producao. O `c:/Phyton-Projetos/...` que estava aqui quebrava com FileNotFoundError.
_AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _AQUI); os.chdir(_AQUI)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import psycopg2
from dotenv import load_dotenv
load_dotenv('.env')
import placas as pl
from server import get_token, execute_dax, clean_rows

APLICAR = '--aplicar' in sys.argv
DE, ATE = '2026-08-01', '2026-09-08'
M="'public manifestos'"; MC="'public manifestos_ctrc'"; CE="'public conhecimentos_emitidos'"

def q(t,d):
    r=execute_dax(t,d); return clean_rows(r.get('results',[{}])[0].get('tables',[{}])[0].get('rows',[]))
def dia(v):
    s=str(v or '')[:10]; return s if len(s)==10 else None
def nm(s):
    t=unicodedata.normalize('NFKD',str(s or ''))
    return ''.join(c for c in t if not unicodedata.combining(c)).upper().strip()

tk=get_token()
d=lambda s:'DATE(%s,%s,%s)'%tuple(int(x) for x in s.split('-'))
mans=q(tk,f"EVALUATE SELECTCOLUMNS(FILTER({M}, {M}[data_emissao]>={d(DE)} && {M}[data_emissao]<={d(ATE)}), "
         f"\"man\",{M}[CHAVE_MANIFESTO], \"cav\",{M}[placa_cavalo], \"car\",{M}[placa_carreta], \"dt\",{M}[data_emissao])")
idx=defaultdict(list)
for m in mans:
    k=(pl.mercosul(m.get('cav')), dia(m.get('dt')))
    if k[0] and k[1]: idx[k].append(m)
print(f'manifestos {DE}..{ATE}: {len(mans)}')

cn=psycopg2.connect(host=os.getenv('DB_HOST'),port=os.getenv('DB_PORT'),dbname=os.getenv('DB_NAME'),
                    user=os.getenv('DB_USER'),password=os.getenv('DB_PASSWORD'))
cur=cn.cursor()
cur.execute("SELECT manifesto_origem FROM embarques_cargas WHERE manifesto_origem IS NOT NULL")
usadas={r[0] for r in cur.fetchall()}
cur.execute("""SELECT id,numero,cavalo_placa,carreta1_placa,data_carregamento,cliente_nome
                 FROM embarques_cargas
                WHERE criada_por_robo AND manifesto_origem IS NULL
                  AND NOT COALESCE(viagem_vazia,FALSE)
                ORDER BY data_carregamento,id""")
alvo=cur.fetchall()
print(f'cargas sem chave (nao-vazias): {len(alvo)}\n')

# ── tomador por manifesto, so para os dias com ambiguidade (rota B) ──
ambiguos=set()
for cid,num,cav,c1,dcar,cli in alvo:
    c=[m for m in idx.get((pl.mercosul(cav),str(dcar)),[]) if str(m.get('man')).strip() not in usadas]
    if len(c)>1: ambiguos.update(str(m.get('man')).strip() for m in c)
tomador={}
if ambiguos:
    lista="{"+",".join(f'"{m}"' for m in sorted(ambiguos))+"}"
    mc=q(tk,f"EVALUATE SELECTCOLUMNS(FILTER({MC}, {MC}[CHAVE_MANIFESTO] IN {lista}), "
           f"\"man\",{MC}[CHAVE_MANIFESTO], \"ctrc\",{MC}[CHAVE_CTRC])")
    ctrcs=sorted({str(r['ctrc']) for r in mc})
    if ctrcs:
        lct="{"+",".join(f'"{c}"' for c in ctrcs[:200])+"}"
        ce=q(tk,f"EVALUATE SELECTCOLUMNS(FILTER({CE}, {CE}[serie_numero_ctrc] IN {lct}), "
               f"\"pm\",{CE}[primeiro_manifesto], \"cli\",{CE}[cliente_pagador])")
        for r in ce:
            k=str(r.get('pm') or '').replace(' ','')
            if k and r.get('cli'): tomador.setdefault(k,set()).add(nm(r['cli']))
    print(f'rota B: {len(ambiguos)} manifestos ambiguos, tomador resolvido p/ {len(tomador)}\n')

planos, sem = [], []
for cid,num,cav,c1,dcar,cli in alvo:
    cands=[m for m in idx.get((pl.mercosul(cav),str(dcar)),[]) if str(m.get('man')).strip() not in usadas]
    escolhido=via=None
    if len(cands)==1:
        escolhido, via = str(cands[0]['man']).strip(), 'A cavalo+dia'
    elif len(cands)>1:
        fino=[m for m in cands if pl.mercosul(m.get('car'))==pl.mercosul(c1)]
        base = fino if len(fino)==1 else cands
        if len(base)==1:
            escolhido, via = str(base[0]['man']).strip(), 'A+carreta'
        else:
            alvo_cli=nm(cli)
            bate=[m for m in base if alvo_cli and alvo_cli in tomador.get(str(m['man']).replace(' ',''),set())]
            if len(bate)==1:
                escolhido, via = str(bate[0]['man']).strip(), 'B tomador'
    if escolhido:
        planos.append((cid,num,escolhido,via)); usadas.add(escolhido)
    else:
        sem.append((num,cav,c1,str(dcar),cli,[str(m['man']) for m in cands]))

print(f'{"carga":<15} {"manifesto":<14} via')
for cid,num,man,via in planos: print(f'   {num:<15} {man:<14} {via}')
if sem:
    print(f'\nNAO RESOLVIDAS ({len(sem)}):')
    for r in sem: print('   ',r)
print(f'\nRESUMO: {len(planos)} resolvidas de {len(alvo)}  |  {len(sem)} pendentes')

if not APLICAR:
    print('\n[DRY-RUN] nada gravado. Use --aplicar para gravar.')
    cn.close(); sys.exit(0)

for cid,num,man,via in planos:
    cur.execute("UPDATE embarques_cargas SET manifesto_origem=%s WHERE id=%s AND manifesto_origem IS NULL",(man,cid))
    cur.execute("""INSERT INTO embarques_cargas_log
                       (carga_id,campo,valor_anterior,valor_novo,usuario_nome,editado_em)
                   VALUES (%s,'manifesto_origem',NULL,%s,'Reconstrucao (auditoria)',NOW())""",(cid,man))
cn.commit()
print(f'\nGRAVADO: {len(planos)} cargas + log de auditoria.')
cn.close()
