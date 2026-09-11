# -*- coding: utf-8 -*-
# Ensaio da §24 (11/09/26). So leitura. Roda local ou dentro do container.
"""ENSAIO — desenho (B) de continuacao/desengate de patio. NAO GRAVA NADA.
Parte 1: motor puro (regras propostas) sobre ago+set: o que mudaria, carga a carga.
Parte 2: como cada peca do ambiente reagiria HOJE, sem ser ensinada (regressoes)."""
import os, sys, re
from collections import defaultdict, Counter
from datetime import datetime, timedelta, time as _t
os.environ.update(START_WORKER='false', EMBARQUES_AUTO='false', PGR_SYNC_CADASTRO='false')
sys.path.insert(0, r'c:\Phyton-Projetos\Tabela Auditoria')
import server, embarques_auto as ea, placas as pl, geocoding as g
tok = server.get_token(); CE = ea.CE; M = ea.M; OS_ = ea.OS_
norm = lambda s: re.sub(r'\s+', '', (s or '')).upper()
D = lambda s: datetime.fromisoformat(s[:19]) if s else None

# ── dados
ctes = ea._dax(tok, f"EVALUATE SELECTCOLUMNS(FILTER({CE}, {CE}[data_emissao] >= DATE(2026,8,1)), "
                    f"\"cli\",{CE}[cliente_pagador], \"pm\",{CE}[primeiro_manifesto], \"um\",{CE}[ultimo_manifesto])")
mans = ea._dax(tok, f"EVALUATE FILTER({M}, {M}[data_emissao] >= DATE(2026,7,15))")
ctrbs = ea._dax(tok, f"EVALUATE SELECTCOLUMNS(FILTER({OS_}, {OS_}[emissao] >= DATE(2026,7,15)), "
                     f"\"ctrb\",{OS_}[ctrb], \"o\",{OS_}[cidade_uf_origem], \"d\",{OS_}[cidade_uf_destino], \"km\",{OS_}[distancia_km])")
ctrb = {ea._chave_ctrb(c['ctrb']): c for c in ctrbs if ea._chave_ctrb(c['ctrb'])}
man = {}
for m in mans:
    m['_k'] = norm(m['CHAVE_MANIFESTO']); m['_cav'] = pl.mercosul(m['placa_cavalo'] or '')
    m['_car'] = pl.mercosul(m['placa_carreta'] or ''); m['_d'] = D(m['data_emissao'])
    ck = ea._chave_ctrb(m.get('CHAVE_CTRB')) or ea._chave_ctrb((m.get('sigla_ctrb_os') or '') + str(m.get('numero_ctrb_os') or ''))
    m['_ctrb'] = ctrb.get(ck) or {}
    man[m['_k']] = m
pares = defaultdict(int)
for c in ctes:
    if norm(c['pm']) and norm(c['pm']) != norm(c['um']):
        pares[(norm(c['pm']), norm(c['um']))] += 1

conn = server.get_db(); cur = conn.cursor()
cur.execute("""SELECT c.id, c.manifesto_origem, c.numero, c.status, c.encerrada_motivo, COALESCE(c.entregue_auto,FALSE), c.no_local_desde,
   c.data_saida_real, c.inicio_viagem, c.data_conclusao, c.carreta1_placa, c.cavalo_placa, COALESCE(c.criada_por_robo,FALSE),
   COALESCE(c.viagem_vazia,FALSE), c.distancia_planejada_km, c.data_carregamento, d.cidade, d.latitude, d.longitude
   FROM embarques_cargas c LEFT JOIN embarques_cargas_destinos d ON d.carga_id=c.id
   AND d.ordem=(SELECT MAX(ordem) FROM embarques_cargas_destinos x WHERE x.carga_id=c.id)""")
COLS = ('id', 'man', 'numero', 'status', 'motivo', 'auto', 'cheg', 'saida', 'inicio', 'conc', 'carreta', 'cavalo', 'robo', 'vazia', 'km_rota', 'dcarg', 'dest', 'dlat', 'dlng')
cargas_all = [dict(zip(COLS, r)) for r in cur.fetchall()]
by_man = {norm(c['man']): c for c in cargas_all if c['man']}
pernas = [c for c in cargas_all if c['vazia'] and c['robo']]


def rota_igual(a, b):
    return bool(a) and bool(b) and (a.get('o'), a.get('d')) == (b.get('o'), b.get('d'))


# ── PARTE 1: motor puro
def classificar(a, b):
    ma, mb = man.get(a), man.get(b)
    if not ma or not mb:
        return None
    dias = (mb['_d'] - ma['_d']).days
    if ma['_car'] != mb['_car']:
        return 'transbordo'
    if ma['_cav'] == mb['_cav'] and dias <= 1 and rota_igual(ma['_ctrb'], mb['_ctrb']):
        return 'reemissao'
    if ma['_cav'] != mb['_cav']:
        return 'desengate'
    return 'hub_mesmo_conjunto'


ACAO = {'desengate': ('Desengatada (patio)', 'Continuada'), 'hub_mesmo_conjunto': (None, 'Continuada'),
        'reemissao': (None, 'Cancelada (reemitido)'), 'transbordo': (None, None)}
linhas = []; cls_count = Counter()
for (a, b), n in pares.items():
    k = classificar(a, b)
    if not k:
        continue
    cls_count[k] += 1
    inter, term = ACAO[k]
    linhas.append(dict(a=a, b=b, k=k, A=by_man.get(a), B=by_man.get(b), inter=inter, term=term, ma=man[a], mb=man[b]))

print('=' * 100); print('PARTE 1 — o que as regras propostas fariam (ago+set, pares CTe pm->um)'); print('=' * 100)
print('pares por classe:', dict(cls_count))
com_A = [l for l in linhas if l['A']]
print(f'pares com a carga A na base local: {len(com_A)}  (o resto e fora do escopo do robo: Terceiro / antes da janela)\n')
print(f"{'carga A':15} {'classe':18} {'status hoje':11} {'motivo hoje':24} {'auto':5} {'-> intermediario':20} {'-> terminal':24} {'B':15} manual?")
mud = Counter()
for l in sorted(com_A, key=lambda l: l['A']['numero']):
    A = l['A']; B = l['B']
    term = l['term'] or 'Entregue (chegou ao hub)'
    if l['term']:
        mud[(A['status'], term)] += 1
    print(f"{A['numero']:15} {l['k']:18} {A['status']:11} {str(A['motivo']):24} {'GPS' if A['auto'] else '':5} "
          f"{str(l['inter'] or '-'):20} {term:24} {(B['numero'] if B else '(nao criada)'):15} {'A MAO!' if not A['robo'] else ''}")
print('\ntransicoes de terminal (hoje -> proposto):')
for k, v in mud.items():
    print(f'  {v:3}  {k[0]} -> {k[1]}')
print('cargas A lancadas A MAO que seriam tocadas (a §0 exige zero):', sum(1 for l in com_A if not l['A']['robo']))
print('cargas A com entregue_auto=TRUE (chegada provada por GPS) que virariam Continuada:',
      sum(1 for l in com_A if l['A']['auto'] and l['term'] == 'Continuada'))

# ── PARTE 2: reacao das pecas
print('\n' + '=' * 100); print('PARTE 2 — como cada peca reage HOJE, sem ser ensinada'); print('=' * 100)

# 2.1 worker
print('\n[2.1] WORKER — Desengatada(patio) cai no filtro ativo do worker (status IN ... Desengatada), que rastreia a carreta contra o DESTINO de A.')
print('      Ele veria a carreta chegar ao destino de A SOB o documento B, antes de o robo diario ligar A->B (D+1 do manifesto B, 16:30 BRT = 19:30 UTC).')
n_cheg_antes = n_conc_antes = n_total = 0
for l in com_A:
    if l['k'] != 'desengate' or not l['B']:
        continue
    A, B = l['A'], l['B']; n_total += 1
    link = datetime.combine(l['mb']['_d'].date() + timedelta(days=1), _t(19, 30))
    mesmo_dest = False
    if A['dlat'] is not None and B['dlat'] is not None:
        mesmo_dest = (g.km_entre(float(A['dlat']), float(A['dlng']), float(B['dlat']), float(B['dlng'])) or 999) < 25
    tag = ''
    if mesmo_dest and B['cheg'] and B['cheg'] < link:
        n_cheg_antes += 1; tag = 'CHEGADA antes do link'
    if mesmo_dest and B['conc'] and B['conc'] < link and (B['motivo'] or '').startswith('gps'):
        n_conc_antes += 1; tag += ' + SAIU DO DESTINO antes do link (A viraria Entregue com entregue_auto=TRUE)'
    if tag:
        print(f"      {A['numero']} -> {B['numero']}: B chegou {str(B['cheg'])[:16]}  link {str(link)[:16]}   {tag}")
print(f'      desengates com B na base: {n_total} · worker marcaria A "No destino" antes do link: {n_cheg_antes} · '
      f'marcaria A "Entregue" com prova de OUTRA viagem: {n_conc_antes}')

# 2.2 robo diario
print('\n[2.2] ROBO DIARIO — fechar_pendentes/dedup so olham status ativos; Continuada/Cancelada ficam invisiveis para eles (ok).')
prod = Counter()
for l in com_A:
    if l['k'] == 'desengate':
        prod[l['A']['motivo'] or 'None'] += 1
print('      motivo de fechamento HOJE das pernas 1 de desengate:', dict(prod))
print('      -> o evento que hoje vira encerrar("manifesto_novo")=Entregue e o mesmo que viraria Desengatada(patio): so muda o verbo,')
print('         condicionado a carreta parada >25 km do destino. Com EMBARQUES_MODELO_CARRETA=false producao fecha pelo manifesto do CAVALO.')

# 2.3 atemporal
print('\n[2.3] ATEMPORAL — processa tudo que nao e Cancelada; deriva n_status de (n_conc, n_cheg, n_saida) e escreve `status` se diferir.')
flip_cont = sum(1 for l in com_A if l['term'] == 'Continuada')
flip_des = sum(1 for l in com_A if l['inter'])
print(f'      Continuada nao existe na derivacao -> n_status=Entregue (manifesto novo da carreta) != Continuada -> REESCREVE. Cargas que oscilariam: {flip_cont}')
print(f'      Desengatada(patio) idem -> Entregue (documental) ou Em rota (sem chegada). Cargas: {flip_des}')
print('      Cancelada(reemitido): status <> Cancelada -> ignorada (ok).')
print('      -> oscilacao e bug (§20.6). O atemporal nao le CTe: so respeita Continuada se houver COLUNA (continua_em) gravada pelo diario.')

# 2.4 pernas
print('\n[2.4] PERNAS VAZIAS — o gerador usa o DESTINO DO PAPEL de A como origem da perna seguinte. Pernas na base entre A e B da mesma carreta:')
km_fab = 0.0; n_fab = 0; n_reem = 0
for l in com_A:
    A = l['A']
    if not l['term']:
        continue
    for v in pernas:
        if pl.mercosul(v['carreta'] or '') != pl.mercosul(A['carreta'] or ''):
            continue
        ini = v['inicio'] or v['saida'] or v['dcarg']
        ini = ini if isinstance(ini, datetime) else datetime.combine(ini, _t())
        if A['conc'] and ini >= A['conc'] - timedelta(hours=1) and ini <= l['mb']['_d'] + timedelta(days=2):
            n_fab += 1; km_fab += float(v['km_rota'] or 0)
            if l['k'] == 'reemissao':
                n_reem += 1
            print(f"      {v['numero']} apos {A['numero']} ({l['k']}): origem da perna = {A['dest']!r} (destino do papel), "
                  f"rota {v['km_rota'] or 0} km, {str(ini)[:16]} -> {str(v['conc'])[:16]}")
print(f'      pernas derivadas de A continuada/reemitida: {n_fab} · km de rota "vazio" que a carreta nao rodou: {km_fab:.0f} km · de reemissao: {n_reem}')
print('      -> com Continuada/Cancelada o gerador tem de PULAR A (a carreta nao foi a lugar nenhum). Hoje so pula Cancelada.')

# 2.5 KPI
print('\n[2.5] KPI entregues_mes (local, ja sem viagem vazia como a §23.5) — hoje x proposto:')
cont_ids = {l['A']['id'] for l in com_A if l['term']}
for mes in ('2026-08', '2026-09'):
    hoje = [c for c in cargas_all if c['status'] == 'Entregue' and not c['vazia'] and c['conc'] and str(c['conc'])[:7] == mes]
    prop = [c for c in hoje if c['id'] not in cont_ids]
    print(f'      {mes}: hoje {len(hoje)}  ->  proposto {len(prop)}   ({len(hoje) - len(prop)} pernas 1 saem, {100 * (len(hoje) - len(prop)) / max(1, len(hoje)):.0f}%)')

# 2.6 aferidor
print('\n[2.6] AFERIDOR — `ativo = status in (4 ativos)` e `if status in (Entregue, Cancelada) and dconc`: Continuada nao e nem ativo nem fechado.')
print('      Cai fora das duas familias de teste e SOME da auditoria em silencio. Precisa entrar na lista de fechados.')

# 2.7 literais
print('\n[2.7] QUEM ENUMERA STATUS (literal "Desengatada" como proxy do custo de ensinar um status novo):')
for f in ('server.py', 'rastreamento_worker.py', 'embarques_auto.py', '_robo_atemporal.py', '_auditoria_geral.py', '_rederivar_vazias.py',
          '_regerar_vazias_agosto.py', 'embarques.html', 'embarques-relatorio.html', 'mapa.html', 'mapa-carga.html', 'embarques-novo.html', 'nav-perms.js'):
    p = os.path.join(r'c:\Phyton-Projetos\Tabela Auditoria', f)
    if not os.path.exists(p):
        continue
    src = open(p, encoding='utf-8', errors='ignore').read()
    n = src.count('Desengatada'); n2 = src.count("'Cancelada'") + src.count('"Cancelada"')
    if n or n2:
        print(f'      {f:28} Desengatada x{n:2}   Cancelada x{n2:2}')

# 2.8 conflitos
print('\n[2.8] CONFLITOS — Desengatada(patio) mantem a CARRETA comprometida (igual ao desengate no destino). O robo cria B sem checar conflito')
print('      de carreta (so checa carga lancada a mao do mesmo cavalo ±1d) -> B nasce. O formulario manual avisaria em amarelo; nao bloqueia.')
