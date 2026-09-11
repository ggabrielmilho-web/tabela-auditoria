# -*- coding: utf-8 -*-
"""REPLAY PROGRESSIVO — reproduz a producao dia a dia (17/08 -> 10/09/2026) com dados reais e NAO GRAVA NADA.

Eventos, na ordem em que producao os ve:
  * MANIFESTO  — emitido no dia D; o robo diario so o enxerga em D+1 as 16:30 BRT (19:30 UTC)
  * CTe        — `ultimo_manifesto` so muda no dia em que o manifesto B e emitido (reconstruido a partir das datas)
  * GPS        — saida / chegada / saiu-do-destino nas horas em que aconteceram (eventos derivados da base local);
                 evento anterior a criacao da carga e aplicado NA criacao (e o atemporal alcancando o passado)
Duas rodadas: HOJE (regras em producao, p/ calibrar contra a base) e PROPOSTA (regras do Gabriel, 11/09).
"""
import os, sys, re
from collections import defaultdict, Counter
from datetime import datetime, timedelta, date, time as _t
os.environ.update(START_WORKER='false', EMBARQUES_AUTO='false', PGR_SYNC_CADASTRO='false')
sys.path.insert(0, r'c:\Phyton-Projetos\Tabela Auditoria')
import server, embarques_auto as ea, placas as pl, geocoding as g

MODO = sys.argv[1] if len(sys.argv) > 1 else 'PROPOSTA'
INI, FIM = date(2026, 8, 17), date(2026, 9, 10)
TICK = _t(19, 30)                                   # 16:30 BRT
norm = lambda s: re.sub(r'\s+', '', (s or '')).upper()
D = lambda s: datetime.fromisoformat(s[:19]) if s else None

# ── BI: manifestos, CTRBs, CTes
tok = server.get_token(); CE = ea.CE; M = ea.M; OS_ = ea.OS_
mans = ea._dax(tok, f"EVALUATE FILTER({M}, {M}[data_emissao] >= DATE(2026,8,1))")
ctrbs = ea._dax(tok, f"EVALUATE SELECTCOLUMNS(FILTER({OS_}, {OS_}[emissao] >= DATE(2026,7,25)), \"ctrb\",{OS_}[ctrb], \"em\",{OS_}[emissao], "
                     f"\"o\",{OS_}[cidade_uf_origem], \"d\",{OS_}[cidade_uf_destino], \"km\",{OS_}[distancia_km])")
ctes = ea._dax(tok, f"EVALUATE SELECTCOLUMNS(FILTER({CE}, {CE}[data_emissao] >= DATE(2026,8,1)), \"pm\",{CE}[primeiro_manifesto], \"um\",{CE}[ultimo_manifesto])")
ctrb = {ea._chave_ctrb(c['ctrb']): c for c in ctrbs if ea._chave_ctrb(c['ctrb'])}
man = {}
for m in mans:
    m['k'] = norm(m['CHAVE_MANIFESTO']); m['cav'] = pl.mercosul(m['placa_cavalo'] or ''); m['car'] = pl.mercosul(m['placa_carreta'] or '')
    m['d'] = D(m['data_emissao']).date(); m['cpf'] = m.get('cpf_motorista')
    ck = ea._chave_ctrb(m.get('CHAVE_CTRB')) or ea._chave_ctrb((m.get('sigla_ctrb_os') or '') + str(m.get('numero_ctrb_os') or ''))
    m['ctrb'] = ctrb.get(ck) or {}
    m['hora'] = D(m['ctrb']['em']) if m['ctrb'].get('em') else datetime.combine(m['d'], _t(12))
    man[m['k']] = m
# CTe: continuacao pm -> um (snapshot final); no replay so vale a partir do dia em que `um` e emitido
cont = defaultdict(set)
for c in ctes:
    a, b = norm(c['pm']), norm(c['um'])
    if a and b and a != b and b in man:
        cont[a].add(b)

# ── base local: cargas do robo (eventos GPS derivados) + geocoding p/ sintetizar as B que faltam
conn = server.get_db(); cur = conn.cursor()
cur.execute("""SELECT c.manifesto_origem, c.numero, c.status, c.encerrada_motivo, COALESCE(c.entregue_auto,FALSE), c.data_saida_real, c.inicio_viagem,
   c.no_local_desde, c.data_conclusao, c.carreta1_placa, c.cavalo_placa, c.data_carregamento, c.origem_latitude, c.origem_longitude,
   d.cidade, d.latitude, d.longitude FROM embarques_cargas c LEFT JOIN embarques_cargas_destinos d ON d.carga_id=c.id
   AND d.ordem=(SELECT MAX(ordem) FROM embarques_cargas_destinos x WHERE x.carga_id=c.id)
   WHERE c.criada_por_robo AND NOT COALESCE(c.viagem_vazia,FALSE) AND c.manifesto_origem IS NOT NULL""")
cargas = {}
for r in cur.fetchall():
    k = norm(r[0])
    if k not in man:
        continue
    cargas[k] = dict(k=k, numero=r[1], status_base=r[2], motivo_base=r[3], auto_base=r[4], saida=r[5] or r[6], cheg=r[7],
                     conc=r[8] if (r[3] or '').startswith('gps') else None, motivo_gps=r[3], carreta=pl.mercosul(r[9] or ''), cavalo=pl.mercosul(r[10] or ''),
                     dcarg=r[11], dest=r[14], dlat=float(r[15]) if r[15] is not None else None, dlng=float(r[16]) if r[16] is not None else None,
                     man=man[k], sint=False)
# sintetiza a carga B de pares cujo A existe mas B nao (ex.: C-818) — sem eventos GPS, exceto os conhecidos de producao
PROD_B = {'UDI029149-8': dict(numero='C-2026-000818*', saida=datetime(2026, 9, 10, 0, 42), cheg=datetime(2026, 9, 10, 9, 56), conc=datetime(2026, 9, 10, 22, 39), motivo_gps='gps_saiu_do_destino')}
for a, bs in cont.items():
    if a in cargas:
        for b in bs:
            if b not in cargas and man[b]['d'] <= FIM:
                mb = man[b]; dcity = (mb['ctrb'].get('d') or '').split('/')[0]
                geo = g.geocoder_municipio(dcity, (mb['ctrb'].get('d') or '/').split('/')[-1], conn)
                extra = PROD_B.get(b, {})
                cargas[b] = dict(k=b, numero=extra.get('numero', f'({b})'), status_base=None, motivo_base=None, auto_base=False, saida=extra.get('saida'),
                                 cheg=extra.get('cheg'), conc=extra.get('conc'), motivo_gps=extra.get('motivo_gps'), carreta=mb['car'], cavalo=mb['cav'],
                                 dcarg=mb['d'], dest=dcity, dlat=geo[0] if geo else None, dlng=geo[1] if geo else None, man=mb, sint=True)

def pos_carreta(placa, t):
    cur.execute("SELECT latitude, longitude, cidade, data_posicao FROM embarques_posicoes_historico WHERE placa=ANY(%s) AND data_posicao<=%s AND data_posicao>=%s ORDER BY data_posicao DESC LIMIT 1",
                (pl.grafias(placa), t, t - timedelta(hours=36)))
    return cur.fetchone()

# ── maquina de estados
ATIVOS = ('Aberta', 'Em rota', 'No destino', 'Desengatada')
TERMINAIS = ('Entregue', 'Continuada', 'Cancelada', 'Desengatada->B')
S = {}                                    # k -> dict(status, motivo, local, link, hist=[(t, de, para, por)])
inc = Counter(); trans = Counter(); retro = []; timeline = defaultdict(dict); por_dia = defaultdict(Counter)

def mudar(k, t, novo, por, **kw):
    s = S[k]; de = s['status']
    if de == novo:
        return
    if de in TERMINAIS:
        if de == 'Entregue' and novo in ('Continuada', 'Desengatada->B', 'Cancelada') and kw.get('link'):
            inc[f'RELABEL de Entregue -> {novo} (GPS fechou A antes de B nascer; CTe diz que seguiu)'] += 1
        else:
            inc[f'REGRESSAO: {de} -> {novo} ({por})'] += 1
            return
    s['status'] = novo; s.update(kw); s['hist'].append((t, de, novo, por)); trans[(de, novo, por)] += 1

def aplicar_gps(k, t, ev, motivo):
    s = S[k]; st = s['status']
    if st in TERMINAIS:
        inc[f'gps ignorado em terminal ({ev})'] += 1; return
    if MODO == 'PROPOSTA' and st == 'Desengatada' and s.get('local') == 'patio':
        inc[f'PATIO: gps de OUTRA viagem ignorado ({ev}) — hoje viraria {"No destino" if ev=="cheg" else "Entregue"}'] += 1; return
    if ev == 'saida' and st == 'Aberta':
        mudar(k, t, 'Em rota', 'gps')
    elif ev == 'cheg' and st in ('Aberta', 'Em rota'):
        mudar(k, t, 'No destino', 'gps')
    elif ev == 'conc' and st in ('Aberta', 'Em rota', 'No destino', 'Desengatada'):
        mudar(k, t, 'Entregue', motivo or 'gps')

def ativas(pred):
    return [k for k, s in S.items() if s['status'] in ATIVOS and not (s['status'] == 'Desengatada' and s.get('local') == 'patio') and pred(cargas[k])]

def rota_igual(a, b): return bool(a) and bool(b) and (a.get('o'), a.get('d')) == (b.get('o'), b.get('d'))

def diario(dia, t):
    """robo diario em D+1: manifestos de D (e reprocessa a janela — aqui basta o dia, os anteriores ja foram)"""
    novos = sorted([m for m in man.values() if m['d'] == dia], key=lambda m: m['hora'])
    for m in novos:
        # (a) manifesto novo do CAVALO com OUTRA carreta -> desengata a carga ativa dele
        for k in ativas(lambda c: c['cavalo'] == m['cav'] and c['carreta'] and m['car'] and c['carreta'] != m['car'] and c['k'] != m['k']):
            if MODO == 'HOJE':
                mudar(k, t, 'Entregue', 'manifesto_novo(cavalo)')
            else:
                c = cargas[k]; p = pos_carreta(c['carreta'], t)
                if p and c['dlat'] is not None:
                    km = g.km_entre(float(p[0]), float(p[1]), c['dlat'], c['dlng']) or 0
                    local = 'destino' if km <= 25 else 'patio'; onde = f'{p[2]} ({km:.0f} km do destino)'
                else:
                    local = 'destino' if S[k]['status'] == 'No destino' else 'patio'; onde = 'sem GPS'
                mudar(k, t, 'Desengatada', 'manifesto_novo(cavalo)', local=local, onde=onde)
        # (b) manifesto novo da CARRETA
        for k, s in list(S.items()):
            c = cargas[k]
            if c['carreta'] and c['carreta'] == m['car'] and c['k'] != m['k'] and (c['man']['d'], c['man']['hora'], c['k']) < (m['d'], m['hora'], m['k']) and (s['status'] not in TERMINAIS or (s['status'] == 'Entregue' and m['k'] in cont.get(k, ()))):
                if MODO == 'HOJE':
                    mudar(k, t, 'Entregue', 'manifesto_novo(carreta)')
                    continue
                if m['k'] in cont.get(k, ()):                       # o CTe diz: a mercadoria seguiu neste manifesto
                    reem = (c['cavalo'] == m['cav'] and (m['d'] - c['man']['d']).days <= 1 and rota_igual(c['man']['ctrb'], m['ctrb']))
                    if reem:
                        mudar(k, t, 'Cancelada', 'reemitido', link=m['k'])
                    elif s['status'] == 'Desengatada' and s.get('local') == 'patio':
                        mudar(k, t, 'Desengatada->B', 'carreta saiu com B', link=m['k'])
                    elif c['cavalo'] == m['cav']:
                        mudar(k, t, 'Continuada', 'mesmo conjunto, doc novo', link=m['k'])
                    else:
                        retro.append((c['numero'], m['k'])); mudar(k, t, 'Desengatada->B', 'desengate RETROATIVO (cavalo sumiu sem manifesto)', link=m['k'])
                else:                                                 # carreta pegou carga sem relacao: a anterior acabou (como hoje)
                    mudar(k, t, 'Entregue', 'manifesto_novo(carreta) sem continuacao')
    # (c) cria as cargas dos manifestos de D e aplica o GPS que ja aconteceu (atemporal)
    for m in novos:
        if m['k'] in cargas and m['k'] not in S:
            S[m['k']] = dict(status='Aberta', motivo=None, hist=[(t, None, 'Aberta', 'robo')])
            c = cargas[m['k']]
            for ev in ('saida', 'cheg', 'conc'):
                if c.get(ev) and c[ev] < t:
                    aplicar_gps(m['k'], t, ev, c.get('motivo_gps'))

# ── linha do tempo: eventos GPS futuros entram na hora; ticks diarios as 19:30 UTC
eventos = []
for k, c in cargas.items():
    for ev in ('saida', 'cheg', 'conc'):
        if c.get(ev):
            eventos.append((c[ev], 1, 'gps', k, ev))
dia = INI
while dia <= FIM:
    eventos.append((datetime.combine(dia, TICK), 0, 'tick', dia - timedelta(days=1), None))
    dia += timedelta(days=1)
eventos.sort(key=lambda e: (e[0], e[1]))
for t, _, tipo, a, b in eventos:
    if tipo == 'tick':
        diario(a, t)
        for k, s in S.items():
            st = s['status'] + ('(patio)' if s['status'] == 'Desengatada' and s.get('local') == 'patio' else '')
            timeline[k][t.date()] = st; por_dia[t.date()][st] += 1
    elif a in S:
        aplicar_gps(a, t, b, cargas[a].get('motivo_gps'))

# ── RESULTADOS
print('=' * 110); print(f'REPLAY {MODO}  {INI} -> {FIM}   cargas simuladas: {len(S)} (sintetizadas: {sum(1 for k in S if cargas[k]["sint"])})'); print('=' * 110)
if MODO == 'HOJE':
    ok = tot = 0; dif = Counter()
    for k, s in S.items():
        c = cargas[k]
        if c['sint'] or not c['status_base']:
            continue
        tot += 1
        if s['status'] == c['status_base']:
            ok += 1
        else:
            dif[(c['status_base'], s['status'])] += 1
    print(f'\nCALIBRACAO — status final do replay x base local: {ok}/{tot} iguais ({100*ok/tot:.0f}%)')
    for kk, v in dif.most_common(8):
        print(f'   {v:3}  base={kk[0]:12} replay={kk[1]}')
print('\nTRANSICOES (de -> para, por):')
for (de, para, por), v in sorted(trans.items(), key=lambda x: -x[1]):
    print(f'   {v:4}  {str(de):14} -> {para:16} {por}')
print('\nINCIDENTES:')
for k, v in inc.most_common():
    print(f'   {v:4}  {k}')
print(f'\nDESENGATES RETROATIVOS (cavalo sumiu sem manifesto, so soubemos quando B nasceu): {len(retro)}')
for n, b in retro[:10]:
    print(f'   {n} -> {b}')
fim = Counter(S[k]['status'] + ('(patio)' if S[k]['status']=='Desengatada' and S[k].get('local')=='patio' else '') for k in S)
print('\nESTADO FINAL em', FIM, dict(fim))
presas = [(cargas[k]['numero'], S[k].get('onde'), S[k]['hist'][-1][0].date()) for k in S if S[k]['status'] == 'Desengatada' and S[k].get('local') == 'patio']
print(f'   Desengatada(patio) SEM continuacao ate o fim (pendencia real p/ o aferidor): {len(presas)}')
for p in presas:
    print(f'      {p[0]}  {p[1]}  desde {p[2]}')
print('\nO QUE O RELATORIO MOSTRARIA A CADA DIA (contagem por status ao fim do tick):')
for d in sorted(por_dia):
    c = por_dia[d]
    print(f"   {d}  ativas={sum(v for s,v in c.items() if s in ATIVOS or s.startswith('Desengatada'))-c.get('Desengatada(patio)',0):3}  "
          f"Desengatada(patio)={c.get('Desengatada(patio)',0):2}  Desengatada={c.get('Desengatada',0):2}  Entregue={c.get('Entregue',0):3}  "
          f"Continuada={c.get('Continuada',0):2}  Desengatada->B={c.get('Desengatada->B',0):2}  Cancelada={c.get('Cancelada',0)}")
print('\nLINHA DO TEMPO das cadeias-exemplo (status ao fim de cada dia):')
EXEMPLOS = ['UDI029121-8', 'UDI029149-8', 'UDI029131-5', 'NOD004787-2', 'UDI028899-3', 'UDI028938-8', 'UDI028961-2', 'UDI028960-4', 'UDI028920-5', 'UDI028941-8', 'GYN005385-6', 'NOD004805-4']
dias = sorted({d for k in EXEMPLOS if k in timeline for d in timeline[k]})
dias = [d for d in dias if d >= date(2026, 8, 22)]
print(' ' * 18 + ' '.join(f'{d.day:02d}/{d.month:02d}' for d in dias))
ABREV = {'Aberta': 'Abert', 'Em rota': 'Rota ', 'No destino': 'NoDst', 'Desengatada(patio)': 'DESpt', 'Desengatada': 'DESds', 'Entregue': 'Entr ', 'Continuada': 'CONT ', 'Desengatada->B': 'DES>B', 'Cancelada': 'CANC '}
for k in EXEMPLOS:
    if k not in S:
        continue
    row = ' '.join(ABREV.get(timeline[k].get(d, ''), '  .  ') if d in timeline[k] else '  .  ' for d in dias)
    print(f"{cargas[k]['numero']:16}  {row}   {S[k].get('link','')}")
print('\nHISTORICO das cadeias-exemplo:')
for k in EXEMPLOS:
    if k in S:
        print(f"  {cargas[k]['numero']} [{k}] cav={cargas[k]['cavalo']} car={cargas[k]['carreta']} dest={cargas[k]['dest']}")
        for t, de, para, por in S[k]['hist']:
            print(f"      {str(t)[:16]}  {str(de):14} -> {para:16} {por}  {S[k].get('onde','') if para=='Desengatada' else ''}")

print('\nO QUE O RELATORIO MOSTRARIA EM 08/09 como Desengatada(patio) [carga, onde parou, desde, cavalo que saiu]:')
d8 = date(2026, 9, 8)
for k in S:
    if timeline[k].get(d8) == 'Desengatada(patio)':
        h = [x for x in S[k]['hist'] if x[2] == 'Desengatada'][0]
        print(f"   {cargas[k]['numero']}  {S[k].get('onde')}  desde {str(h[0])[:10]}  cav {cargas[k]['cavalo']}  dest {cargas[k]['dest']}")
print('\nESPERA NO PATIO (desengate -> B nasce), dias:')
esp = []
for k in S:
    hs = S[k]['hist']; d1 = [x for x in hs if x[2] == 'Desengatada']; d2 = [x for x in hs if x[2] == 'Desengatada->B']
    if d1 and d2: esp.append((cargas[k]['numero'], (d2[0][0] - d1[0][0]).days))
print('   ' + ', '.join(f'{n}:{d}d' for n, d in sorted(esp, key=lambda x: -x[1])))
