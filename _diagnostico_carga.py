# -*- coding: utf-8 -*-
"""DIAGNOSTICO DE UMA CARGA — por que a tela mostra o que mostra (so leitura).

Escrito para a pergunta "o mapa diz Sem rastreio, mas saida e chegada estao preenchidas".
Isso acontece porque quem le o GPS sao DOIS leitores com reguas diferentes:

  * a TELA (`/api/rastreamento/cargas/<id>/trajeto`) chama `_placa_tracking`, que exige a
    placa no CADASTRO `embarques_veiculos_rastreio` — a lista de veiculos sincronizada do
    /ListaVeiculos da 3S. Placa fora do cadastro => `rastreado_via = None` => "Sem rastreio",
    mesmo que existam posicoes gravadas para ela;
  * o ROBO (atemporal, motor, aferidor) le `embarques_posicoes_historico` DIRETO, pela
    grafia da placa. Nao consulta cadastro nenhum.

Entao ha quatro estados possiveis, e este script diz em qual a carga esta:

    cadastro SIM · posicoes SIM   -> tela normal
    cadastro NAO · posicoes SIM   -> "Sem rastreio" na tela, robo enxergando tudo  <= o caso
    cadastro SIM · posicoes NAO   -> placa muda (rastreador em silencio)
    cadastro NAO · posicoes NAO   -> veiculo sem rastreio de verdade (terceiro, agregado)

Reproduz ainda o RECORTE de origem que a tela aplica (`indice_saida_origem`): ele pode
esvaziar o trajeto desenhado mesmo havendo pontos, e ai a linha some e os KPIs zeram.

    python -X utf8 _diagnostico_carga.py C-2026-001011
    python -X utf8 _diagnostico_carga.py C-2026-001011 --log-tudo
"""
import os
import sys
import argparse
from datetime import datetime, timedelta, time as _time

# `__file__` nao existe quando o script e PIPADO para dentro do container.
_AQUI = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
sys.path.insert(0, _AQUI)
os.chdir(_AQUI)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import psycopg2
from dotenv import load_dotenv
load_dotenv('.env')
import geocoding
import placas as pl
import rastreamento_worker as rw

ap = argparse.ArgumentParser()
ap.add_argument('numero')
ap.add_argument('--log-tudo', action='store_true', help='log inteiro, nao so os campos de viagem')
A = ap.parse_args()

cn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                      user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = cn.cursor()

cur.execute("""SELECT id, numero, status, encerrada_motivo, tipo_operacao, cliente_nome,
                      motorista_nome, cavalo_placa, carreta1_placa, carreta2_placa,
                      origem_cidade, origem_uf, origem_latitude, origem_longitude,
                      data_carregamento, data_saida_real, inicio_viagem, no_local_desde,
                      no_local_fonte, data_conclusao, COALESCE(saida_auto,FALSE),
                      COALESCE(entregue_auto,FALSE), COALESCE(criada_por_robo,FALSE),
                      COALESCE(viagem_vazia,FALSE), manifesto_origem, ctrb_origem,
                      rota_planejada_polyline IS NOT NULL, distancia_planejada_km, criado_em
                 FROM embarques_cargas WHERE numero = %s""", (A.numero,))
r = cur.fetchone()
if not r:
    print(f'carga {A.numero} nao encontrada')
    sys.exit(1)
(cid, num, status, motivo, tipo, cliente, mot, cav, c1, c2, ocid, ouf, ola, oln, dcarg, dsaida,
 inicio, nolocal, nolocal_fonte, dconc, saida_auto, entregue_auto, robo, vazia, man, ctrb,
 tem_rota, dist_plan, criado) = r

print(f'=== {num} (id {cid}) ===')
print(f'status          {status}' + (f' ({motivo})' if motivo else ''))
print(f'tipo/cliente    {tipo} · {cliente or "—"} · motorista {mot or "—"}')
print(f'placas          cavalo {cav or "—"} · carreta1 {c1 or "—"} · carreta2 {c2 or "—"}')
print(f'origem          {ocid}/{ouf} ({ola},{oln})')
cur.execute("""SELECT ordem, cidade, uf, latitude, longitude FROM embarques_cargas_destinos
                WHERE carga_id=%s ORDER BY ordem""", (cid,))
dest = cur.fetchall()
for o, ci, uf, la, ln in dest:
    print(f'destino {o}       {ci}/{uf} ({la},{ln})')
print(f'carregamento    {dcarg} · criada {criado} · robo={robo} · vazia={vazia}')
print(f'saida           {dsaida}  (saida_auto={saida_auto}) · inicio_viagem {inicio}')
print(f'chegada         {nolocal}  (fonte={nolocal_fonte})')
print(f'conclusao       {dconc}  (entregue_auto={entregue_auto})')
print(f'documento       manifesto {man or "—"} · ctrb {ctrb or "—"}')
print(f'rota planejada  {"sim" if tem_rota else "NAO"} · {dist_plan} km')

# ── a janela que a TELA usa para buscar trajeto (mesma do endpoint)
base = dcarg if isinstance(dcarg, datetime) else datetime.combine(dcarg, _time())
ini_janela = base - timedelta(hours=12)
fim_janela = dconc or nolocal or datetime.utcnow()
print(f'\njanela da tela  {ini_janela} .. {fim_janela}')

print(f'\n{"placa":<12} {"papel":<9} {"cadastro 3S":<24} {"pts total":>9} {"pts janela":>11}'
      f'  primeira .. ultima posicao')
trajes = {}
for papel, placa in (('carreta1', c1), ('carreta2', c2), ('cavalo', cav)):
    if not placa:
        continue
    gs = pl.grafias(str(placa).strip().upper())
    cur.execute("SELECT placa, id_veiculo_3s, sincronizado_em FROM embarques_veiculos_rastreio "
                "WHERE placa = ANY(%s)", (gs,))
    cad = cur.fetchone()
    cur.execute("SELECT count(*), MIN(data_posicao), MAX(data_posicao) "
                "FROM embarques_posicoes_historico WHERE placa = ANY(%s)", (gs,))
    tot, pmin, pmax = cur.fetchone()
    cur.execute("""SELECT data_posicao, latitude, longitude, velocidade
                     FROM embarques_posicoes_historico
                    WHERE placa = ANY(%s) AND data_posicao BETWEEN %s AND %s
                    ORDER BY data_posicao""", (gs, ini_janela, fim_janela))
    pts = cur.fetchall()
    trajes[papel] = pts
    print(f'{placa:<12} {papel:<9} '
          f'{(cad[0] + " (id " + str(cad[1]) + ")") if cad else "*** FORA DO CADASTRO ***":<24}'
          f' {tot:>9} {len(pts):>11}  {str(pmin)[:16]} .. {str(pmax)[:16]}')

# ── a decisao da TELA, exatamente como o endpoint a toma
placa_track = rw._placa_tracking(cav, c1, c2, cur)
print(f'\n_placa_tracking -> {placa_track or "None"}')
if not placa_track:
    print('   => a tela mostra "Sem rastreio": NENHUMA placa da carga esta no cadastro')
    print('      embarques_veiculos_rastreio. Isso e cadastro, nao ausencia de posicao —')
    print('      veja a coluna "pts total" acima para saber se o robo enxerga a viagem.')
else:
    _pt = pl.mercosul(placa_track)
    papel = ('carreta1' if _pt == pl.mercosul(c1 or '') else
             'carreta2' if _pt == pl.mercosul(c2 or '') else
             'cavalo' if _pt == pl.mercosul(cav or '') else '???')
    print(f'   => a tela rastreia por {placa_track} ({papel})')
    if papel == '???':
        print('      ATENCAO: o cadastro devolveu uma grafia que nao casa com nenhuma placa da')
        print('      carga — o endpoint cai no else e mostra "Sem rastreio" mesmo assim.')

# ── posicao atual (o card POSICAO ATUAL da tela)
for papel, placa in (('carreta1', c1), ('carreta2', c2), ('cavalo', cav)):
    if not placa:
        continue
    cur.execute("""SELECT placa, data_posicao, cidade, uf, velocidade FROM embarques_posicoes_atuais
                    WHERE placa = ANY(%s) ORDER BY data_posicao DESC LIMIT 1""",
                (pl.grafias(str(placa).strip().upper()),))
    a = cur.fetchone()
    print(f'posicao atual   {placa:<10} {papel:<9} ' + (f'{a[1]} {a[2]}/{a[3]} {a[4]} km/h' if a else '— (nenhuma)'))

# ── o RECORTE de origem que a tela aplica: pode esvaziar o desenho mesmo havendo pontos
if ola is not None and trajes:
    print('\nrecorte de origem (indice_saida_origem — o que a tela desenha depois de cortar):')
    for papel, pts in trajes.items():
        if not pts:
            print(f'   {papel:<9} 0 pontos na janela — nada a cortar')
            continue
        idx = geocoding.indice_saida_origem(
            [(float(p[1]), float(p[2])) for p in pts], float(ola), float(oln),
            velocidades=[p[3] for p in pts],
            instantes=[p[0] for p in pts], ate=(dsaida or inicio))
        print(f'   {papel:<9} {len(pts)} pontos -> corta {idx} -> sobram {len(pts) - idx}'
              + ('   *** o desenho fica VAZIO ***' if len(pts) - idx == 0 else ''))

# ── distancia minima a origem e ao destino, com a regua do aferidor
if trajes and ola is not None:
    print('\ndistancia minima (sobre os pontos da janela):')
    dla, dln = (dest[0][3], dest[0][4]) if dest else (None, None)
    for papel, pts in trajes.items():
        if not pts:
            continue
        do = min((geocoding.km_entre(float(p[1]), float(p[2]), float(ola), float(oln)) or 9e9) for p in pts)
        dd = (min((geocoding.km_entre(float(p[1]), float(p[2]), float(dla), float(dln)) or 9e9) for p in pts)
              if dla is not None else None)
        print(f'   {papel:<9} origem {do:>8,.0f} km'.replace(',', '.')
              + (f' · destino {dd:>8,.0f} km'.replace(',', '.') if dd is not None else ''))

# ── o log: quem escreveu saida, chegada e status
campos = ('status', 'data_saida_real', 'no_local_desde', 'no_local_fonte', 'data_conclusao',
          'saida_auto', 'entregue_auto', 'encerrada_motivo', 'inicio_viagem')
if A.log_tudo:
    cur.execute("""SELECT editado_em, usuario_nome, campo, valor_anterior, valor_novo
                     FROM embarques_cargas_log WHERE carga_id=%s ORDER BY editado_em""", (cid,))
else:
    cur.execute("""SELECT editado_em, usuario_nome, campo, valor_anterior, valor_novo
                     FROM embarques_cargas_log WHERE carga_id=%s AND campo = ANY(%s)
                    ORDER BY editado_em""", (cid, list(campos)))
linhas = cur.fetchall()
print(f'\nlog da carga ({len(linhas)} linha(s)) — quem escreveu saida, chegada e status:')
for q, quem, campo, de, para in linhas:
    print(f'   {str(q)[:19]}  {quem:<24} {campo:<18} {str(de)[:26]:<26} -> {str(para)[:40]}')

cn.rollback()
cn.close()
