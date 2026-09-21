# -*- coding: utf-8 -*-
"""DIFF DO AFERIDOR — compara duas rodadas pela LISTA, nao pela contagem (HANDOFF 27.12).

Motivo de existir: o V1 subiu de 44 (18/09) para 64 (20/09) e nenhuma contagem explica
isso — dois numeros diferentes podem ser as MESMAS cargas mais algumas, ou cargas
completamente outras. Aqui se pergunta quem ENTROU, quem SAIU, e o que mudou na carga
entre as duas rodadas.

Para cada carga que entrou ou saiu do codigo, imprime a evidencia que discrimina as
causas possiveis, todas medidas com a MESMA regua do aferidor (embarques_regua):

  * quantos pontos cada placa tem na janela e QUAL delas o aferidor usa como principal
    (carreta1 -> carreta2 -> cavalo): sensor que acorda troca a placa principal e pode
    trazer o V1 junto;
  * primeiro ponto disponivel da placa contra o piso da janela (carregamento - 12 h):
    se o primeiro ponto e DEPOIS do piso, a evidencia da origem foi purgada (30 d);
  * distancia minima a origem com e sem o filtro de posicao falsa (23.3): se o bruto
    encosta na origem e o limpo nao, quem tirou o V1 do lugar foi o filtro;
  * o log da carga na janela: alguem (robo ou gente) escreveu placa/origem no meio.

Nao grava nada. Roda dentro do container, ao lado do _auditoria_geral.py.

    python -X utf8 _diff_aferidor.py aferidor_20260920.csv _auditoria_geral.csv
    python -X utf8 _diff_aferidor.py A.csv B.csv --codigo F1 --desde-log 2026-09-18
"""
import os
import sys
import csv
import argparse
from collections import defaultdict, Counter
from datetime import datetime, timedelta, time as _time, timezone

# `__file__` nao existe quando o script e PIPADO para dentro do container
# (`docker exec -i ... python -X utf8 - < este_arquivo`), que e como ele roda em
# producao enquanto a imagem nao for refeita. Ali o cwd ja e o /app do Dockerfile.
_AQUI = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
sys.path.insert(0, _AQUI)
os.chdir(_AQUI)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import psycopg2
from dotenv import load_dotenv
load_dotenv('.env')
import geocoding
import placas as pl
import embarques_regua as regua
from embarques_regua import RAIO_ORIGEM, RAIO_METRO, PARADA_MIN_H, JANELA_EVIDENCIA_D

ap = argparse.ArgumentParser()
ap.add_argument('gabarito')
ap.add_argument('novo')
ap.add_argument('--codigo', default='V1')
ap.add_argument('--desde-log', default=None, help='default: 5 dias atras')
ap.add_argument('--limite', type=int, default=40)
ap.add_argument('--todos', action='store_true',
                help='detalha tambem as cargas que FICARAM no codigo (o perfil do conjunto)')
A = ap.parse_args()
HOJE = datetime.now(timezone.utc).replace(tzinfo=None)   # posicoes sao UTC (secao 10)
CORTE_LOG = (datetime.strptime(A.desde_log, '%Y-%m-%d') if A.desde_log
             else HOJE - timedelta(days=5))

cn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                      user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = cn.cursor()


def ler(caminho):
    """{carga: {codigo: texto}} — o CSV do aferidor sai com BOM e separador ';'."""
    por_carga = defaultdict(dict)
    with open(caminho, encoding='utf-8-sig', newline='') as f:
        for r in csv.DictReader(f, delimiter=';'):
            if r.get('carga'):
                por_carga[r['carga'].strip()][r['codigo'].strip()] = (r.get('achado') or '').strip()
    return por_carga


GAB, NOV = ler(A.gabarito), ler(A.novo)
print(f'GABARITO {A.gabarito}: {len(GAB)} cargas com achado · '
      f'{sum(len(v) for v in GAB.values())} achados')
print(f'NOVO     {A.novo}: {len(NOV)} cargas com achado · '
      f'{sum(len(v) for v in NOV.values())} achados')

# placar por codigo, lado a lado. A contagem so serve para saber ONDE olhar.
ca = Counter(c for v in GAB.values() for c in v)
cb = Counter(c for v in NOV.values() for c in v)
print(f'\n{"cod":<5} {"gab":>5} {"novo":>5} {"delta":>6}')
for cod in sorted(set(ca) | set(cb), key=lambda k: -abs(cb[k] - ca[k])):
    d = cb[cod] - ca[cod]
    print(f'{cod:<5} {ca[cod]:>5} {cb[cod]:>5} {d:>+6}' + ('   <-- moveu' if d else ''))

alvo_a = {n for n, v in GAB.items() if A.codigo in v}
alvo_b = {n for n, v in NOV.items() if A.codigo in v}
saiu, entrou, ficou = sorted(alvo_a - alvo_b), sorted(alvo_b - alvo_a), alvo_a & alvo_b
universo = sorted(set(GAB) | set(NOV))
cur.execute("""SELECT numero, id, data_carregamento, status, encerrada_motivo, origem_cidade,
                      origem_latitude, origem_longitude, cavalo_placa, carreta1_placa,
                      carreta2_placa, criado_em, COALESCE(criada_por_robo, FALSE),
                      COALESCE(viagem_vazia, FALSE)
                 FROM embarques_cargas WHERE numero = ANY(%s)""", (universo,))
INFO = {r[0]: r for r in cur.fetchall()}


def janela(nomes):
    ds = [INFO[n][2] for n in nomes if n in INFO and INFO[n][2]]
    return (min(ds), max(ds), len(ds)) if ds else (None, None, 0)


# A JANELA de cada CSV, deduzida das cargas que cada um enxergou. O aferidor so se compara
# na MESMA janela do gabarito (secao 21.10): se estas duas linhas nao baterem, o resto da
# comparacao nao quer dizer nada.
ja, jb = janela(GAB), janela(NOV)
print('\nJANELA (pela data_carregamento das cargas com achado)')
print(f'   gabarito {ja[0]} .. {ja[1]}   ({ja[2]} cargas)')
print(f'   novo     {jb[0]} .. {jb[1]}   ({jb[2]} cargas)')
so_b = [n for n in set(NOV) - set(GAB) if n in INFO and ja[1] and INFO[n][2] > ja[1]]
print(f'   cargas que o novo enxerga ALEM da data final do gabarito: {len(so_b)}'
      '  <- isto e janela, nao regressao')

# piso de retencao das posicoes: a purga come a evidencia da ponta VELHA primeiro
cur.execute('SELECT MIN(data_posicao), MAX(data_posicao) FROM embarques_posicoes_historico')
PISO_POS, TOPO_POS = cur.fetchone()
print(f'\nposicoes retidas: {PISO_POS} .. {TOPO_POS}')

_cache = {}


def pontos(placa, ini, fim):
    """Mesma leitura do aferidor, devolvendo BRUTOS e LIMPOS: a diferenca entre os dois e
    o filtro de posicao falsa, que e uma das causas candidatas."""
    if not placa:
        return [], []
    k = (pl.mercosul(placa), ini, fim)
    if k in _cache:
        return _cache[k]
    cur.execute("""SELECT data_posicao,latitude,longitude,velocidade,odometer
                     FROM embarques_posicoes_historico
                    WHERE placa=ANY(%s) AND data_posicao>=%s AND data_posicao<%s
                    ORDER BY data_posicao""",
                (pl.grafias(str(placa).strip().upper()), ini, fim))
    brutos = [(d, float(la), float(ln), v, o) for d, la, ln, v, o in cur.fetchall()
              if la is not None and ln is not None]
    v = (brutos, regua.sem_posicao_falsa(brutos))
    _cache[k] = v
    return v


def minkm(pts, lat, lng):
    ks = [geocoding.km_entre(p[1], p[2], lat, lng) for p in pts]
    ks = [k for k in ks if k is not None]
    return min(ks) if ks else None


def horas_perto(pts, lat, lng, raio):
    dd = [p[0] for p in pts if (geocoding.km_entre(p[1], p[2], lat, lng) or 9e9) <= raio]
    return (dd[-1] - dd[0]).total_seconds() / 3600 if len(dd) > 1 else 0.0


def km(v):
    return '—' if v is None else f'{v:,.0f} km'.replace(',', '.')


VEREDITOS = Counter()


def _classificar(base, medidas, principal):
    """Leitura automatica do PORQUE, com a mesma precedencia de placa do aferidor
    (carreta1 -> carreta2 -> cavalo). E rotulo de triagem, nao decisao: os numeros da
    linha de cada placa ficam impressos ao lado para conferir."""
    if principal is None:
        return ('sem GPS', 'nenhuma placa transmitiu na janela — isso e S1, nao V1')
    lbl, n_br, n_lp, prim, d_br, d_lp = principal
    if PISO_POS and base < PISO_POS:
        return ('purga', f'a janela comeca em {str(base)[:10]}, ABAIXO do piso de retencao '
                         f'({str(PISO_POS)[:10]}) — a evidencia da origem ja foi apagada')
    if d_lp is not None and d_lp <= RAIO_ORIGEM:
        return ('nao reproduz', f'a placa {lbl} ESTA na origem hoje ({d_lp:.0f} km)')
    if d_br is not None and d_lp is not None and d_br <= RAIO_ORIGEM < d_lp:
        return ('posicao falsa', f'bruto {d_br:.0f} km, limpo {d_lp:.0f} km — o filtro da 23.3 '
                                 f'tirou justamente o ponto da origem')
    cav = medidas.get('cavalo')
    if lbl != 'cavalo' and cav and cav[5] is not None and cav[5] <= RAIO_ORIGEM:
        return ('carreta errada', f'so o CAVALO esteve na origem ({cav[5]:.0f} km) — Fase D')
    if prim is not None and (prim - base).total_seconds() / 3600 > 24:
        return ('sensor mudo', f'1o ponto de {lbl} {(prim - base).total_seconds()/3600:.0f} h '
                               f'depois do piso da janela')
    return ('sem origem', f'nem carreta nem cavalo estiveram na origem (min {d_lp:.0f} km)'
                          if d_lp is not None else 'nem carreta nem cavalo estiveram na origem')


def detalhar(num, titulo):
    r = INFO.get(num)
    if not r:
        print(f'   {num:<15} (nao esta mais na tabela?)')
        return
    (_, cid, dcarg, status, motivo, ocid, ola, oln, cav, c1, c2, criado, robo, vazia) = r
    base = datetime.combine(dcarg, _time()) - timedelta(hours=12)
    fim = min(HOJE, datetime.combine(dcarg, _time()) + timedelta(days=JANELA_EVIDENCIA_D))
    print(f'\n   {num}  {titulo}')
    print(f'      carregamento {dcarg} · criada {str(criado)[:16]}'
          f'{" · PERNA VAZIA" if vazia else ""} · status {status}'
          f'{" (" + str(motivo) + ")" if motivo else ""} · robo={robo}')
    print(f'      origem {ocid} ({ola},{oln}) · cavalo {cav or "—"} · carreta1 {c1 or "—"}'
          f' · carreta2 {c2 or "—"}')
    print(f'      gabarito: {",".join(sorted(GAB.get(num, {}))) or "(sem achado)"}'
          f'   ->   novo: {",".join(sorted(NOV.get(num, {}))) or "(sem achado)"}')
    medidas, principal = {}, None
    for lbl, placa in (('carreta1', c1), ('carreta2', c2), ('cavalo', cav)):
        if not placa:
            continue
        br, lp = pontos(placa, base, fim)
        if not br:
            print(f'      {lbl:<9} {placa:<9} 0 pontos na janela'
                  f' (piso da janela {str(base)[:16]}; retencao comeca {str(PISO_POS)[:16]})')
            continue
        d_br = minkm(br, float(ola), float(oln)) if ola is not None else None
        d_lp = minkm(lp, float(ola), float(oln)) if ola is not None else None
        h = horas_perto(lp, float(ola), float(oln), RAIO_METRO) if ola is not None else 0.0
        atraso = (br[0][0] - base).total_seconds() / 3600
        medidas[lbl] = (lbl, len(br), len(lp), br[0][0], d_br, d_lp)
        if principal is None:
            principal = medidas[lbl]
        print(f'      {lbl:<9} {placa:<9} {len(br):>5} pts (limpos {len(lp):>5})'
              f' · 1o ponto {str(br[0][0])[:16]} ({atraso:+.0f} h do piso)'
              f' · min origem bruto {km(d_br)} / limpo {km(d_lp)}'
              f' · {h:.1f} h dentro de {RAIO_METRO:.0f} km'
              + ('   <- PRINCIPAL' if medidas[lbl] is principal else ''))
    tag, detalhe = _classificar(base, medidas, principal)
    VEREDITOS[f'{titulo:<7} {tag}'] += 1
    print(f'      => [{tag}] {detalhe}')
    txt = NOV.get(num, {}).get(A.codigo) or GAB.get(num, {}).get(A.codigo) or ''
    if txt:
        print(f'      achado: {txt[:150]}')
    cur.execute("""SELECT editado_em, usuario_nome, campo, valor_anterior, valor_novo
                     FROM embarques_cargas_log WHERE carga_id=%s AND editado_em >= %s
                    ORDER BY editado_em""", (cid, CORTE_LOG))
    for q, quem, campo, de, para in cur.fetchall():
        print(f'      log {str(q)[:16]} {quem:<22} {campo}: {str(de)[:28]} -> {str(para)[:40]}')


print(f'\n{"=" * 78}\n{A.codigo}: gabarito {len(alvo_a)} · novo {len(alvo_b)} · '
      f'ficou {len(ficou)} · ENTROU {len(entrou)} · SAIU {len(saiu)}\n{"=" * 78}')


def semana(n):
    d = INFO.get(n, (None, None, None))[2]
    return str(d - timedelta(days=d.weekday()))[:10] if d else '?'


sa, sb = Counter(semana(n) for n in alvo_a), Counter(semana(n) for n in alvo_b)
print(f'{"semana":<12} {"gab":>5} {"novo":>5}')
for s in sorted(set(sa) | set(sb)):
    print(f'{s:<12} {sa[s]:>5} {sb[s]:>5}')

# PERFIL do conjunto. A `data_carregamento` NAO data a entrada da carga na base: a perna
# vazia nasce com a data de CONCLUSAO da carga anterior (retroativa, _regerar_vazias) e o
# robo lanca dentro de uma janela de 5 dias. Quem data a entrada e `criado_em`.
print(f'\n--- PERFIL do conjunto atual de {A.codigo} ({len(alvo_b)}) ---')
perfil = Counter()
for n in alvo_b:
    r = INFO.get(n)
    if r:
        perfil[('perna vazia' if r[13] else 'carga'), str(r[11])[:10] if r[11] else '?'] += 1
print(f'{"tipo":<12} {"criada em (UTC)":<16} {"n":>4}')
for (tipo, dia), n in sorted(perfil.items(), key=lambda x: (x[0][1], x[0][0])):
    print(f'{tipo:<12} {dia:<16} {n:>4}')
print(f'   pernas vazias {sum(v for (t, _d), v in perfil.items() if t == "perna vazia")}'
      f' · cargas {sum(v for (t, _d), v in perfil.items() if t == "carga")}')

print(f'\n--- ENTRARAM no {A.codigo} ({len(entrou)}) ---')
for n in entrou[:A.limite]:
    detalhar(n, 'ENTROU')
if len(entrou) > A.limite:
    print(f'   ... e mais {len(entrou) - A.limite}')

print(f'\n--- SAIRAM do {A.codigo} ({len(saiu)}) ---')
for n in saiu[:A.limite]:
    detalhar(n, 'SAIU')
if len(saiu) > A.limite:
    print(f'   ... e mais {len(saiu) - A.limite}')

print(f'\n--- FICARAM no {A.codigo} ({len(ficou)}) ---')
print('   ' + ' '.join(sorted(ficou)))
if A.todos:
    for n in sorted(ficou):
        detalhar(n, 'FICOU')

print(f'\n--- LEITURA AUTOMATICA (triagem, medida HOJE com a regua do aferidor) ---')
for k, n in VEREDITOS.most_common():
    print(f'   {n:>4}  {k}')
cn.close()
