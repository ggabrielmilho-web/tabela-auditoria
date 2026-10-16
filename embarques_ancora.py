# -*- coding: utf-8 -*-
"""ÂNCORA — o ponto do CLIENTE de destino (PROPOSTA-ANCORA.md, 14/10/2026).

Etapas 1 e 2 da proposta: as COORDENADAS do endereço do cadastro e o APRENDIZADO pelas descargas. Nenhuma régua
lê isto ainda: a etapa 3 (os campos "No cliente desde", "Saiu do cliente", "Na região do cliente") vem atrás de
`EMBARQUES_ANCORA`, e nunca toca na saída, chegada, conclusão ou status de hoje.

Ordem da âncora de destino (lab de 30/09–07/10, refeito no dump de 14/10 — §8 da proposta):
  1 aprendido pelo CNPJ ....... >= 2 cargas concluídas pararam >= 30 min a <= 1 km entre si, perto do destino, fora
                                de pátio/posto (94% a <= 2 km nas viagens observadas)
  2 local da GR ............... `insignia_locais` pelo CNPJ (GR por último: 424 x 436 "no cliente")
  3 endereço do cadastro ...... só perfil "bom" (número, não rodovia, CEP != -000) e município conferido
  4 aprendido tomador+cidade .. como o 1, pela chave (tomador, cidade, UF) — resolve a subcontratação
O endereço ruim (rodovia, sem número, CEP -000) NUNCA vira âncora: 54–60% a <= 2 km.

As coordenadas ficam em `locais_fontes` (latitude, longitude, geocod_fonte, geocod_em — já existiam, vazias), na
linha que a view `locais` escolhe para o CNPJ. `geocod_fonte`:
  endereco ........ perfil bom e município conferido — a única que vira âncora
  endereco_fraco .. município conferido, perfil ruim (fica gravado, não é âncora)
  fora_da_cidade .. o Google achou outro município: coordenada NÃO gravada
  sem_resultado ... o Google não achou: coordenada NÃO gravada (não pergunta de novo)
Decisão do Gabriel (07 e 14/10/26): as coordenadas ficam numa tabela nossa, sem marcar de onde vieram.

A série de GPS é a do motor (`fontes_gps.historico`: 3S + Insignia + tapa-buraco, carreta descongelada com a chave):
no dump de 14/10, 13% das viagens já não aparecem na série da 3S da carreta.
"""
import os
import re
import json
import time
import statistics
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, time as _time, timezone

import geocoding
import placas as pl
import fontes_gps
import embarques_regua as regua


def _agora_utc():
    """UTC sem fuso, como o motor (`HOJE`): as posições e as datas da carga são gravadas assim."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

APRENDE_KM = 1.0          # paradas de cargas diferentes a <= isto entre si contam como o mesmo ponto
APRENDE_MIN_CARGAS = 2    # cargas distintas para o ponto valer
PARADA_MIN = 30           # minutos parado para uma parada ensinar
PARADA_TOL_KM = 0.3       # "mesmo lugar" dentro de uma parada
CANDIDATA_KM = 60         # a parada que ensina fica a <= isto do centroide do destino
PATIO_PLACAS, PATIO_NOITES = 5, 10   # célula (0,01°) onde >= 5 carretas dormem em >= 10 noites = pátio/posto
CLIENTE_EM_PATIO_KM = 1.0            # ... salvo se um cliente fica ali (cliente onde se pernoita)
ENTREGUE = ('Entregue', 'No destino', 'Desengatada')


def ligado():
    """`EMBARQUES_ANCORA=true`: os campos novos da carga (etapa 3). Desligada, nada lê a âncora."""
    return os.getenv('EMBARQUES_ANCORA', 'false').strip().lower() == 'true'


def geocodar_ligado():
    """`EMBARQUES_ANCORA_GEOCODAR=true` e a chave do Google no ambiente: o robô geocodifica endereço novo."""
    return (os.getenv('EMBARQUES_ANCORA_GEOCODAR', 'false').strip().lower() == 'true'
            and bool(os.getenv('GOOGLE_MAPS_API_KEY')))


def fecha_ligado():
    """`EMBARQUES_ANCORA_FECHA=true` (16/10/26): com o cliente PROVADO, a conclusão é a saída do cliente ou 24 h nele
    (o que vier primeiro) — nunca antes de chegar nele. Exige a âncora ligada. Medido no lab de 16/10 (estudo
    `_estudo_2026-10-16/LISTA.md`): sem Uberlândia, 99 melhoram, 4 regridem (2 a trava da carga seguinte já pega)."""
    return ligado() and os.getenv('EMBARQUES_ANCORA_FECHA', 'false').strip().lower() == 'true'


def cidades_fora():
    """`EMBARQUES_ANCORA_CIDADES_FORA` = "UBERLANDIA/MG,..." — destinos onde a âncora não vale: nem os campos do
    cliente nem o fechamento por ele. Uberlândia é a cidade da Rizza: oficina, manobra, pátio, "pode ser de tudo"
    (Gabriel, 16/10/26) — parada perto do cliente lá não prova entrega. Vazia = nenhuma (o de antes)."""
    out = set()
    for x in os.getenv('EMBARQUES_ANCORA_CIDADES_FORA', '').split(','):
        if '/' in x:
            c, u = x.rsplit('/', 1)
            out.add((_sem_acento(c), u.strip().upper()))
    return out


def cidade_fora(cidade, uf):
    return (_sem_acento(cidade), (uf or '').strip().upper()) in cidades_fora()


DDL = """
CREATE TABLE IF NOT EXISTS embarques_ancora_provas (
    carga_id     INTEGER NOT NULL,
    numero       VARCHAR(20),
    destino_cnpj VARCHAR(14),
    tomador      VARCHAR(160),
    cidade       VARCHAR(80),
    uf           VARCHAR(2),
    concluida_em TIMESTAMP,
    inicio       TIMESTAMP NOT NULL,
    minutos      REAL NOT NULL,
    latitude     DOUBLE PRECISION NOT NULL,
    longitude    DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (carga_id, inicio)
);
CREATE INDEX IF NOT EXISTS ix_ancora_provas_cnpj ON embarques_ancora_provas (destino_cnpj);
CREATE INDEX IF NOT EXISTS ix_ancora_provas_tomador ON embarques_ancora_provas (tomador, cidade, uf);
CREATE TABLE IF NOT EXISTS embarques_ancora_patios (
    latitude  DOUBLE PRECISION NOT NULL,
    longitude DOUBLE PRECISION NOT NULL,
    placas    INTEGER,
    noites    INTEGER,
    PRIMARY KEY (latitude, longitude)
);
"""


DDL_META = """
CREATE TABLE IF NOT EXISTS embarques_ancora_aprendidas (
    carga_id INTEGER PRIMARY KEY,
    chave    VARCHAR(120),
    em       TIMESTAMP
);
CREATE TABLE IF NOT EXISTS embarques_ancora_meta (
    chave VARCHAR(40) PRIMARY KEY,
    em    TIMESTAMP
);
"""

# Os campos novos da carga (etapa 3). Ficam ao lado dos de hoje e NUNCA os substituem.
COLUNAS = (('cliente_chegada_em', 'TIMESTAMP'),          # "No cliente desde"
           ('cliente_saida_em', 'TIMESTAMP'),            # "Saiu do cliente"
           ('cliente_regiao_em', 'TIMESTAMP'),           # "Na região do cliente" (<= 5 km)
           ('ancora_tipo', 'VARCHAR(20)'),               # aprendido_cnpj | gr | endereco | aprendido_tomador
           ('ancora_lat', 'DOUBLE PRECISION'),
           ('ancora_lng', 'DOUBLE PRECISION'))
CAMPOS = tuple(c for c, _ in COLUNAS)


def garantir_tabelas(cur):
    cur.execute(DDL)
    cur.execute(DDL_META)


def garantir_colunas(cur):
    for col, tipo in COLUNAS:
        cur.execute(f"ALTER TABLE embarques_cargas ADD COLUMN IF NOT EXISTS {col} {tipo}")


def colunas_existem(cur):
    cur.execute("SELECT count(*) FROM information_schema.columns WHERE table_name = 'embarques_cargas' "
                "AND column_name = ANY(%s)", (list(CAMPOS),))
    return cur.fetchone()[0] == len(CAMPOS)


# ── 1. COORDENADAS DO ENDEREÇO ──────────────────────────────────────────────────────────────────────────────────
_ROD = re.compile(r'\b(ROD|RODOVIA|ESTRADA|EST|BR[- ]?\d|KM)\b')


def perfil(endereco, cep):
    """bom / rodovia/KM / sem número / CEP -000 / sem endereço (medido em 30/09: só o bom serve de âncora)."""
    e = (endereco or '').strip().upper()
    if not e:
        return 'sem endereço'
    if _ROD.search(e):
        return 'rodovia/KM'
    if not re.search(r'\d', e):
        return 'sem número'
    if re.sub(r'\D', '', str(cep or '')).endswith('000'):
        return 'CEP -000'
    return 'bom'


def _sem_acento(s):
    return ''.join(c for c in unicodedata.normalize('NFD', str(s or '').upper())
                   if unicodedata.category(c) != 'Mn').strip()


def _cep(v):
    d = re.sub(r'\D', '', str(v or ''))
    if len(d) == 7:
        d = '0' + d
    return f'{d[:5]}-{d[5:]}' if len(d) == 8 else None


def consulta(l):
    """O texto que vai ao geocodificador — o MESMO do estudo de 30/09, que é a chave do cache."""
    local = f"{l['cidade']} - {l['uf']}" if (l.get('cidade') and l.get('uf')) else (l.get('cidade') or '')
    return ', '.join(x for x in [l.get('endereco'), l.get('bairro'), local, _cep(l.get('cep')), 'Brasil'] if x)


def guarda_cidade(l, g):
    """Município do geocodificador == o do cadastro (e a UF, quando o cadastro tem). O cadastro corta o nome em 17
    caracteres ("APARECIDA DE GOIA"): cortado, vale o prefixo; inteiro, tem de ser igual."""
    if not g or g.get('status') != 'OK':
        return False
    cad, geo = _sem_acento(l.get('cidade')), _sem_acento(g.get('municipio'))
    if not cad or not (cad == geo or (len(cad) >= 17 and geo.startswith(cad))):
        return False
    if l.get('uf') and g.get('uf') and g['uf'].upper() != l['uf'].strip().upper():
        return False
    return True


def classificar(l, g):
    """(geocod_fonte, lat, lng) para gravar."""
    if not g or g.get('status') != 'OK':
        return 'sem_resultado', None, None
    if not guarda_cidade(l, g):
        return 'fora_da_cidade', None, None
    tipo = 'endereco' if perfil(l.get('endereco'), l.get('cep')) == 'bom' else 'endereco_fraco'
    return tipo, float(g['lat']), float(g['lng'])


_COLS_LOCAIS = ('cnpj', 'fonte', 'nome', 'endereco', 'bairro', 'cep', 'cidade', 'uf', 'latitude', 'geocod_fonte')


def _pendentes(cur, so_usados=True):
    """Linhas escolhidas pela view `locais` ainda sem tentativa de geocodificação (e com endereço)."""
    usados = (" AND l.cnpj IN (SELECT destino_cnpj FROM embarques_cargas WHERE destino_cnpj IS NOT NULL)"
              if so_usados else '')
    cur.execute(f"""SELECT l.cnpj, l.fonte, l.nome, l.endereco, l.bairro, l.cep, l.cidade, l.uf, l.latitude, l.geocod_fonte
                      FROM locais l JOIN locais_fontes f ON f.cnpj = l.cnpj AND f.fonte = l.fonte
                     WHERE f.geocod_em IS NULL AND l.endereco IS NOT NULL{usados}""")
    return [dict(zip(_COLS_LOCAIS, r)) for r in cur.fetchall()]


def _gravar(cur, l, g):
    tipo, la, ln = classificar(l, g)
    cur.execute("""UPDATE locais_fontes SET latitude = %s, longitude = %s, geocod_fonte = %s, geocod_em = %s
                    WHERE cnpj = %s AND fonte = %s""",
                (la, ln, tipo, _agora_utc().replace(microsecond=0), l['cnpj'], l['fonte']))
    return tipo


def coordenadas_do_cache(cur, cache):
    """Carga inicial: grava o que o cache de estudo já tem (chave = `consulta(l)`). Não chama o Google.
    Devolve Counter por geocod_fonte; endereço fora do cache fica pendente para `geocodificar`."""
    n = Counter()
    for l in _pendentes(cur, so_usados=False):
        g = cache.get(consulta(l))
        if g is None:
            n['fora do cache'] += 1
            continue
        n[_gravar(cur, l, g)] += 1
    return n


def _google(q, chave):
    url = 'https://maps.googleapis.com/maps/api/geocode/json?' + urllib.parse.urlencode(
        {'address': q, 'region': 'br', 'key': chave})
    with urllib.request.urlopen(url, timeout=30) as r:
        d = json.loads(r.read().decode('utf-8'))
    if d.get('status') != 'OK' or not d.get('results'):
        return {'status': d.get('status')}
    r0 = d['results'][0]
    comp = {t: c for c in r0.get('address_components', []) for t in c['types']}
    return {'status': 'OK', 'lat': r0['geometry']['location']['lat'], 'lng': r0['geometry']['location']['lng'],
            'municipio': (comp.get('administrative_area_level_2') or {}).get('long_name'),
            'uf': (comp.get('administrative_area_level_1') or {}).get('short_name')}


def geocodificar(cur, limite=40, pausa_s=0.1):
    """Robô diário (com `geocodar_ligado()`): geocodifica os CNPJs de DESTINO de carga ainda sem tentativa. Poucos
    por dia (169 CNPJs de destino distintos de 27/08 a 14/10). Erro de rede não grava nada (tenta no outro dia)."""
    if not geocodar_ligado():
        return Counter()
    chave, n = os.getenv('GOOGLE_MAPS_API_KEY'), Counter()
    for l in _pendentes(cur)[:limite]:
        try:
            g = _google(consulta(l), chave)
        except Exception:
            n['erro de rede'] += 1
            continue
        if g.get('status') in ('OVER_QUERY_LIMIT', 'REQUEST_DENIED', 'UNKNOWN_ERROR'):
            n[g['status']] += 1          # problema da chave/cota, não do endereço: não marca como tentado
            break
        n[_gravar(cur, l, g)] += 1
        time.sleep(pausa_s)
    return n


# ── 2. APRENDIZADO PELAS DESCARGAS ──────────────────────────────────────────────────────────────────────────────
def km(a1, o1, a2, o2):
    return geocoding.km_entre(a1, o1, a2, o2) or 0.0


def recalcular_patios(cur):
    """Células de 0,01° onde >= 5 carretas dormem (0–4 h BRT, parado) em >= 10 noites — pátio da Rizza, posto,
    fila de CD. Não ensinam descarga. Exceto se um ENDEREÇO de cliente geocodificado está a <= 1 km (cliente onde se
    pernoita). O local da GR NÃO salva a célula: a GR cadastra também os pátios da própria Rizza ("RIZZA BARRACÃO",
    "GARAGEM RIZZA", CAIS) — com ela, 18 de 24 células deixavam de ser pátio no dump de 14/10."""
    cur.execute("""SELECT round(latitude::numeric, 2), round(longitude::numeric, 2),
                          count(DISTINCT placa), count(DISTINCT (data_posicao - INTERVAL '3 h')::date)
                     FROM embarques_posicoes_historico
                    WHERE COALESCE(velocidade, 0) <= 3
                      AND extract(hour FROM data_posicao - INTERVAL '3 h') BETWEEN 0 AND 4
                    GROUP BY 1, 2
                   HAVING count(DISTINCT placa) >= %s AND count(DISTINCT (data_posicao - INTERVAL '3 h')::date) >= %s""",
                (PATIO_PLACAS, PATIO_NOITES))
    celulas = [(float(a), float(b), n, d) for a, b, n, d in cur.fetchall()]
    cur.execute("SELECT latitude, longitude FROM locais_fontes WHERE geocod_fonte IN ('endereco', 'endereco_fraco')")
    clientes = [(float(a), float(b)) for a, b in cur.fetchall()]
    patios = [c for c in celulas if not any(km(c[0], c[1], a, b) <= CLIENTE_EM_PATIO_KM for a, b in clientes)]
    cur.execute("DELETE FROM embarques_ancora_patios")
    for c in patios:
        cur.execute("INSERT INTO embarques_ancora_patios VALUES (%s,%s,%s,%s)", c)
    return len(patios), len(celulas) - len(patios)


def serie(cur, placa, ini, fim):
    """(data, lat, lng, vel) da placa — a série do motor, sem posição falsa."""
    if not placa:
        return []
    cur.execute(f"""SELECT data_posicao, latitude, longitude, velocidade, odometer FROM {fontes_gps.historico(cur)} h
                     WHERE placa = ANY(%s) AND data_posicao >= %s AND data_posicao < %s ORDER BY data_posicao""",
                (pl.grafias(str(placa).strip().upper()), ini, fim))
    brutos = [(d, float(la), float(ln), v, o) for d, la, ln, v, o in cur.fetchall() if la is not None and ln is not None]
    return regua.sem_posicao_falsa(brutos)


def serie_da_carga(cur, c):
    """A série que mede a carga, como o motor: a carreta; muda, o cavalo; carreta congelada na 3S trocada pelo cavalo
    da SM (`fontes_gps.descongelar`, com a chave). Janela: carregamento −24 h até o fim do documento + 48 h."""
    t0 = datetime.combine(c['carreg'], _time()) - timedelta(hours=24)
    t1 = (c['conc'] or c['nolocal'] or t0 + timedelta(days=10)) + timedelta(hours=48)
    pts = serie(cur, c['carreta'], t0, t1)
    if pts and c['cavalo'] and fontes_gps.cavalo_tapa_ligado():
        # o cavalo tapa a carreta (EMBARQUES_CAVALO_TAPA, 15/10/26) — a régua do motor
        _dst = (float(c['dlat']), float(c['dlng'])) if c.get('dlat') is not None else None
        pts, _ = fontes_gps.tapar_com_cavalo(cur, c['carreta'], c['cavalo'], pts, serie(cur, c['cavalo'], t0, t1),
                                             c['carreg'], dest=_dst)
    elif pts and c['cavalo'] and fontes_gps.congelada_ligado():
        pts, _, _ = fontes_gps.descongelar(cur, c['carreta'], c['cavalo'], pts, serie(cur, c['cavalo'], t0, t1),
                                           c['carreg'])
    return pts or serie(cur, c['cavalo'], t0, t1), t0, t1


def paradas(pts, min_min=PARADA_MIN):
    """(lat, lng, início, minutos) dos blocos parados contíguos (<= 0,3 km do 1º ponto, sem buraco > 3 h)."""
    out, b = [], []
    for p in pts:
        if not regua.parado(p[3]):
            if b:
                out.append(b)
            b = []
            continue
        if b and (km(p[1], p[2], b[0][1], b[0][2]) > PARADA_TOL_KM or (p[0] - b[-1][0]).total_seconds() > 3 * 3600):
            out.append(b)
            b = []
        b.append(p)
    if b:
        out.append(b)
    res = []
    for b in out:
        mins = (b[-1][0] - b[0][0]).total_seconds() / 60 + 5
        if mins >= min_min:
            res.append((statistics.median(x[1] for x in b), statistics.median(x[2] for x in b), b[0][0], mins))
    return res


def viagem_local(c):
    """Origem = destino (mesma cidade e UF): a "saída" não existe — o cliente é procurado desde o carregamento
    (C-2026-001221: a régua de hoje gravou como saída a carreta indo para a viagem seguinte)."""
    return (_sem_acento(c.get('ocid')) == _sem_acento(c.get('dcid')) and (c.get('ouf') or '') == (c.get('duf') or '')
            and bool(c.get('dcid')))


SQL_CARGAS = """
    SELECT c.id, c.numero, c.status, COALESCE(c.criada_por_robo, FALSE), c.destino_cnpj, c.cliente_nome,
           c.data_carregamento, c.data_saida_real, c.no_local_desde, c.data_conclusao, c.cavalo_placa, c.carreta1_placa,
           c.origem_cidade, c.origem_uf, d.cidade, d.uf, d.latitude, d.longitude
      FROM embarques_cargas c
      JOIN embarques_cargas_destinos d ON d.carga_id = c.id
       AND d.ordem = (SELECT MAX(ordem) FROM embarques_cargas_destinos x WHERE x.carga_id = c.id)
     WHERE NOT COALESCE(c.viagem_vazia, FALSE) AND c.status <> 'Cancelada' {filtro}
     ORDER BY c.data_carregamento, c.id"""
_K = ('id', 'numero', 'status', 'robo', 'dcnpj', 'tomador', 'carreg', 'saida', 'nolocal', 'conc', 'cavalo',
      'carreta', 'ocid', 'ouf', 'dcid', 'duf', 'dlat', 'dlng')


def cargas(cur, filtro='', params=()):
    cur.execute(SQL_CARGAS.format(filtro=filtro), params)
    out = [dict(zip(_K, r)) for r in cur.fetchall()]
    for c in out:
        c['dlat'] = float(c['dlat']) if c['dlat'] is not None else None
        c['dlng'] = float(c['dlng']) if c['dlng'] is not None else None
    return out


def _patios(cur):
    cur.execute("SELECT latitude, longitude FROM embarques_ancora_patios")
    return cur.fetchall()


def candidatas(c, pts, patios):
    """Paradas >= 30 min desta carga que podem ser a descarga: depois da saída (viagem local: desde o carregamento),
    a <= 60 km do destino, fora de pátio/posto."""
    if c['dlat'] is None:
        return []
    ref = datetime.combine(c['carreg'], _time()) if (viagem_local(c) or not c['saida']) else c['saida']
    return [s for s in paradas(pts)
            if s[2] >= ref and km(s[0], s[1], c['dlat'], c['dlng']) <= CANDIDATA_KM
            and not any(km(s[0], s[1], a, b) <= CLIENTE_EM_PATIO_KM for a, b in patios)]


def _chave_aprendida(c):
    """O que, mudando, obriga a reaprender a carga: status, conclusão, saída e placas."""
    return f"{c['status']}|{c['conc']}|{c['saida']}|{c['carreta']}|{c['cavalo']}"[:120]


def aprender(cur, desde, tudo=False, recentes_h=72):
    """Grava em `embarques_ancora_provas` as paradas candidatas de cada carga CONCLUÍDA (Entregue / No destino /
    Desengatada) com carregamento >= `desde`. INCREMENTAL: só reaprende a carga cuja chave (status, conclusão,
    saída, placas) mudou desde a última vez, mais as concluídas nas últimas `recentes_h` horas (o GPS da 3S chega
    atrasado — §28.14 do HANDOFF-EMBARQUES). `tudo` refaz todas. Devolve (cargas aprendidas, provas gravadas)."""
    pat = _patios(cur)
    cs = [c for c in cargas(cur, "AND c.data_carregamento >= %s", (desde,)) if c['status'] in ENTREGUE]
    if not tudo:
        cur.execute("SELECT carga_id, chave FROM embarques_ancora_aprendidas")
        feitas = dict(cur.fetchall())
        limite = _agora_utc() - timedelta(hours=recentes_h)
        cs = [c for c in cs if feitas.get(c['id']) != _chave_aprendida(c)
              or (c['conc'] or c['nolocal'] or limite) > limite]
    n = 0
    for c in cs:
        pts, _, _ = serie_da_carga(cur, c)
        cur.execute("""INSERT INTO embarques_ancora_aprendidas (carga_id, chave, em) VALUES (%s, %s, now())
                       ON CONFLICT (carga_id) DO UPDATE SET chave = EXCLUDED.chave, em = EXCLUDED.em""",
                    (c['id'], _chave_aprendida(c)))
        cur.execute("DELETE FROM embarques_ancora_provas WHERE carga_id = %s", (c['id'],))
        for la, ln, ini, mins in candidatas(c, pts, pat):
            cur.execute("""INSERT INTO embarques_ancora_provas
                             (carga_id, numero, destino_cnpj, tomador, cidade, uf, concluida_em, inicio, minutos, latitude, longitude)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                        (c['id'], c['numero'], c['dcnpj'], (c['tomador'] or '').strip().upper() or None, c['dcid'],
                         c['duf'], c['conc'] or c['nolocal'], ini, mins, la, ln))
            n += 1
    return len(cs), n


def _ponto_aprendido(provas):
    """O ponto onde >= 2 cargas DISTINTAS pararam a <= 1 km entre si (o de mais cargas; empate, mais minutos)."""
    melhor = None
    for i, (cid, la, ln, mins) in enumerate(provas):
        viz = [(c2, a, b, m) for c2, a, b, m in provas if km(la, ln, a, b) <= APRENDE_KM]
        n = len({v[0] for v in viz})
        if n >= APRENDE_MIN_CARGAS:
            dur = sum(v[3] for v in viz)
            if not melhor or (n, dur) > (melhor[0], melhor[1]):
                melhor = (n, dur, statistics.median(v[1] for v in viz), statistics.median(v[2] for v in viz))
    return melhor


def _candidatas(cur, c, ate=None):
    """As âncoras possíveis da carga `c` (dict de `cargas()`), NA ORDEM: aprendido pelo CNPJ -> local da GR ->
    endereço conferido -> aprendido por tomador + cidade. A própria carga NUNCA ensina a si mesma. `ate`
    (laboratório): só aprende com carga concluída antes disso — é como o lab mediu, sem olhar o futuro."""
    def provas(where, params):
        sql = f"SELECT carga_id, latitude, longitude, minutos FROM embarques_ancora_provas WHERE {where} AND carga_id <> %s"
        p = list(params) + [c['id']]
        if ate is not None:
            sql += " AND concluida_em < %s"
            p.append(ate)
        cur.execute(sql, p)
        return cur.fetchall()

    if c['dcnpj']:
        m = _ponto_aprendido(provas("destino_cnpj = %s", (c['dcnpj'],)))
        if m:
            yield {'tipo': 'aprendido_cnpj', 'lat': m[2], 'lng': m[3], 'n': m[0]}
        cur.execute("""SELECT lat, lng FROM insignia_locais WHERE lat IS NOT NULL AND COALESCE(ativo, TRUE)
                          AND lpad(regexp_replace(cnpj, '\\D', '', 'g'), 14, '0') = %s LIMIT 1""", (c['dcnpj'],))
        r = cur.fetchone()
        if r:
            yield {'tipo': 'gr', 'lat': float(r[0]), 'lng': float(r[1]), 'n': None}
        cur.execute("SELECT latitude, longitude FROM locais WHERE cnpj = %s AND geocod_fonte = 'endereco'", (c['dcnpj'],))
        r = cur.fetchone()
        if r and r[0] is not None:
            yield {'tipo': 'endereco', 'lat': float(r[0]), 'lng': float(r[1]), 'n': None}
    tom = (c['tomador'] or '').strip().upper()
    if tom and c['dcid']:
        m = _ponto_aprendido(provas("tomador = %s AND cidade = %s AND uf = %s", (tom, c['dcid'], c['duf'])))
        if m:
            yield {'tipo': 'aprendido_tomador', 'lat': m[2], 'lng': m[3], 'n': m[0]}


def ancora_destino(cur, c, ate=None):
    """A 1ª candidata, SEM as guardas (é a medida do lab de 30/09–14/10; o motor usa `ancora_da_carga`)."""
    return next(_candidatas(cur, c, ate=ate), None)


ANCORA_MAX_KM = 60.0      # âncora a mais disto do destino do CTRB é descartada (CNPJ errado, 14/10/26)
SEDES_TOP = 3             # a cidade do CTRB tem de estar entre as 3 sedes de município mais próximas do ponto
_SEDES = []


def _sedes(cur):
    if not _SEDES:
        cur.execute("SELECT cidade_normalizada, uf, latitude, longitude FROM municipios_ibge WHERE latitude IS NOT NULL")
        _SEDES.extend((_sem_acento(n), (u or '').upper(), float(a), float(b)) for n, u, a, b in cur.fetchall())
    return _SEDES


def cidade_confere(cur, lat, lng, cidade, uf):
    """O ponto cai na cidade do CTRB? Sem o polígono do município, a aproximação é a SEDE: a cidade do CTRB tem de
    estar entre as `SEDES_TOP` sedes mais próximas do ponto. Medido em 15/10/26 (lab de 14/10): a âncora cuja cidade
    nem está entre as 3 é o ponto de OUTRA entrega ou de espera — C-1531 (subcontratação, Ribeirão Pires) aprendeu a
    parada de Cotia, onde a mesma rota descarrega antes; C-583 (Rio) aprendeu um ponto perto de Itaguaí onde a
    carreta fica 35 h. Cliente da periferia de cidade grande (Osasco, Rio norte) cai entre a 2ª e a 3ª — passa."""
    alvo = (_sem_acento(cidade), (uf or '').upper())
    perto = sorted((km(lat, lng, a, b), (n, u)) for n, u, a, b in _sedes(cur)
                   if abs(a - lat) <= 0.8 and abs(b - lng) <= 0.8)
    return alvo in [x[1] for x in perto[:SEDES_TOP]]


def ancora_da_carga(cur, c, ate=None):
    """A 1ª candidata que passa nas guardas: a <= 60 km do destino do CTRB e na cidade do CTRB (`cidade_confere`).
    A que não passa dá lugar à próxima da ordem — não deixa a carga sem âncora por causa de uma camada errada."""
    for a in _candidatas(cur, c, ate=ate):
        if c.get('dlat') is not None and km(a['lat'], a['lng'], c['dlat'], c['dlng']) > ANCORA_MAX_KM:
            continue
        if c.get('dcid') and not cidade_confere(cur, a['lat'], a['lng'], c['dcid'], c.get('duf')):
            continue
        return a
    return None


# ── 3. OS EVENTOS — a régua do lab (`_lab_ancora.eventos_novos`, configuração `--r-chegada 2 --r-chegando 5
# --d2 b`, validada em 30/09 e refeita em 14/10) ────────────────────────────────────────────────────────────────
R_CHEGADA_KM = 2.0        # "No cliente": parado a <= 2 km da âncora...
DWELL_MIN = 10            # ... por >= 10 min ...
MIN_PARADOS = 2           # ... com >= 2 pontos parados
R_REGIAO_KM = 5.0         # "Na região do cliente": >= 2 pontos seguidos a <= 5 km, em qualquer velocidade
R_SAIDA_KM = 3.0          # "Saiu do cliente": ponto a > 3 km e 30 min observados sem voltar a <= 2 km
OBSERVA_MIN = 30
SALTO_D2B_KM = 5.0        # D2b: repetição de posição cuja entrada ou saída é salto impossível


def _impossivel(a, b):
    return regua.perna_impossivel({'lat': a[1], 'lng': a[2], 'data': a[0], 'odometer': None},
                                  {'lat': b[1], 'lng': b[2], 'data': b[0], 'odometer': None},
                                  salto_min_km=SALTO_D2B_KM)


def limpa_d2b(T):
    """D2b (§28.10): sequência de >= 2 pontos na MESMA posição sai da série se a perna que entra nela ou a que sai
    dela é impossível pela velocidade — o aparelho congelado. Só os pontos repetidos; nunca a região inteira; sem
    odômetro (o da carreta para de contar quando ela dorme — C-510)."""
    T = list(T)
    for _ in range(3):
        fora, i, n = set(), 0, len(T)
        while i < n:
            j = i
            while j + 1 < n and round(T[j + 1][1], 5) == round(T[i][1], 5) and round(T[j + 1][2], 5) == round(T[i][2], 5):
                j += 1
            if j > i and ((i > 0 and _impossivel(T[i - 1], T[i])) or (j + 1 < n and _impossivel(T[j], T[j + 1]))):
                fora.update(range(i, j + 1))
            i = j + 1
        if not fora:
            break
        T = [p for k, p in enumerate(T) if k not in fora]
    return T


def presencas(T, alvo, raio):
    """Blocos contíguos a <= `raio` do alvo: (i_ini, i_fim, minutos, n_parados)."""
    out, i, n = [], 0, len(T)
    while i < n:
        if km(T[i][1], T[i][2], alvo[0], alvo[1]) > raio:
            i += 1
            continue
        j = i
        while j + 1 < n and km(T[j + 1][1], T[j + 1][2], alvo[0], alvo[1]) <= raio:
            j += 1
        mins = (T[j][0] - T[i][0]).total_seconds() / 60 + 5
        out.append((i, j, mins, sum(1 for p in T[i:j + 1] if regua.parado(p[3]))))
        i = j + 1
    return out


def eventos(c, pts, anc):
    """{'cliente_chegada_em', 'cliente_saida_em', 'cliente_regiao_em'} da carga pela âncora `anc`. `pts` = a série
    do motor (data, lat, lng, vel), já com a janela da carga. Procura a partir da saída (viagem local: do
    carregamento) menos 6 h. Sem prova, o campo fica None — vale a régua de hoje."""
    ev = {'cliente_chegada_em': None, 'cliente_saida_em': None, 'cliente_regiao_em': None}
    if not anc or not pts:
        return ev
    alvo = (anc['lat'], anc['lng'])
    T = limpa_d2b(pts)
    ini = datetime.combine(c['carreg'], _time()) if (viagem_local(c) or not c['saida']) else c['saida']
    idx0 = next((k for k, p in enumerate(T) if p[0] >= ini - timedelta(hours=6)), len(T))
    T = T[idx0:]
    reg = [p for p in presencas(T, alvo, R_REGIAO_KM) if p[1] > p[0]]
    if reg:
        ev['cliente_regiao_em'] = T[reg[0][0]][0]
    pres = [p for p in presencas(T, alvo, R_CHEGADA_KM) if p[2] >= DWELL_MIN and p[3] >= MIN_PARADOS]
    if not pres:
        return ev
    i, j = pres[0][0], pres[0][1]
    ev['cliente_chegada_em'] = T[i][0]
    for k in range(j + 1, len(T)):
        if km(T[k][1], T[k][2], alvo[0], alvo[1]) > R_SAIDA_KM:
            volta = any(km(p[1], p[2], alvo[0], alvo[1]) <= R_CHEGADA_KM
                        for p in T[k:] if p[0] <= T[k][0] + timedelta(minutes=OBSERVA_MIN))
            # ao vivo: "não voltou em 30 min" só vale com 30 min de pontos DEPOIS (replay de 30/09)
            if not volta and T[-1][0] >= T[k][0] + timedelta(minutes=OBSERVA_MIN):
                ev['cliente_saida_em'] = T[k - 1][0]
                break
    return ev


FECHA_OBSERVA_H = 6.0     # a saída do cliente só fecha a carga se o caminhão não voltar a ele em 6 h
FECHA_DWELL_H = 24.0      # "fica 24 h no destino" (a régua de sempre), contada da chegada no CLIENTE


def _voltou(T, alvo, t0, horas):
    """depois de t0 + 30 min e até t0 + horas: bloco >= 30 min a <= R_CHEGADA_KM do cliente"""
    blk = []
    for p in T:
        if p[0] <= t0 + timedelta(minutes=OBSERVA_MIN):
            continue
        if p[0] > t0 + timedelta(hours=horas):
            break
        if km(p[1], p[2], alvo[0], alvo[1]) <= R_CHEGADA_KM:
            blk.append(p[0])
            if blk[-1] - blk[0] >= timedelta(minutes=30):
                return True
        else:
            blk = []
    return False


def conclusao_pelo_cliente(ev, anc, pts, fim_janela=None, agora=None):
    """(instante, motivo) da conclusão pelo cliente provado, ou (None, None) se ainda não dá para afirmar.

    A régua de sempre ("sai do destino OU fica 24 h nele, o que vier primeiro", §4.3) com o destino trocado pelo
    CLIENTE: a saída do cliente (`ev['cliente_saida_em']`), se nas FECHA_OBSERVA_H seguintes ele não voltou ao cliente
    (lab de 16/10: o caminhão que se afasta 3 km para estacionar ou fazer fila e volta para descarregar fechava na
    hora errada — C-835, C-818); ou a chegada no cliente + 24 h, quando as 24 h foram observadas (a carreta que fica
    dias largada no cliente fechava dias depois — C-1452). Sem as horas de observação, nada: a próxima rodada decide.
    Motivos com o prefixo 'gps_' — é entrega provada pelo GPS (`entregue_auto`)."""
    ca = ev.get('cliente_chegada_em')
    if not (ca and anc and pts):
        return None, None
    alvo = (anc['lat'], anc['lng'])
    T = limpa_d2b(pts)
    if not T:
        return None, None
    ult = T[-1][0]
    fim24 = ca + timedelta(hours=FECHA_DWELL_H)
    # a 1a saída (> R_SAIDA_KM) que NÃO volta ao cliente em FECHA_OBSERVA_H — pulando as idas e vindas (sai às 4 h para
    # estacionar, volta às 6 h, descarrega e sai de vez às 10 h: a saída é a das 10 h). O campo "Saiu do cliente" segue
    # a 1a saída (`eventos`); a conclusão pede mais prova.
    cands, esteve = [], False
    for k, p in enumerate(T):
        if p[0] < ca:
            continue
        d = km(p[1], p[2], alvo[0], alvo[1])
        if d <= R_CHEGADA_KM:
            esteve = True
            continue
        if d <= R_SAIDA_KM or not esteve or k == 0:
            continue
        cs = T[k - 1][0]
        if cs >= fim24:
            break
        # janela CORTADA pela carga seguinte do veículo (`fim_janela` < agora): não haverá mais prova — e a carreta ter
        # começado outra carga já diz que ela foi embora. Vale o que se viu até o corte (C-2026-000570: saiu do
        # cliente às 20:11, a carga seguinte abre à meia-noite, as 6 h nunca vinham e a conclusão ficava presa na
        # do worker, 1 min depois da chegada na cidade vizinha).
        cortada = fim_janela is not None and agora is not None and fim_janela < agora
        if ult < p[0] + timedelta(hours=FECHA_OBSERVA_H) and not cortada:
            return None, None              # a saída ainda está em observação: não fecha nas 24 h por cima dela
        if not _voltou(T, alvo, cs, FECHA_OBSERVA_H):
            cands.append((cs, 'gps_saiu_do_cliente'))
            break
        esteve = False                     # voltou: a próxima saída só conta depois de estar de novo no cliente
    if ult >= fim24:
        cands.append((fim24, 'gps_24h_no_cliente'))
    return min(cands) if cands else (None, None)


# ── 4. O PREPARO DE CADA RODADA DO MOTOR ────────────────────────────────────────────────────────────────────────
PATIOS_H = 20             # pátios recalculados no máximo uma vez a cada 20 h


def preparar(cur, desde):
    """Antes do motor gravar (só com `--aplicar` e a chave): tabelas e colunas, geocodificação do endereço novo
    (com `EMBARQUES_ANCORA_GEOCODAR`), pátios (1x/dia) e o aprendizado incremental. Devolve um resumo."""
    garantir_tabelas(cur)
    garantir_colunas(cur)
    res = {'geocodificados': dict(geocodificar(cur))}
    cur.execute("SELECT em FROM embarques_ancora_meta WHERE chave = 'patios'")
    r = cur.fetchone()
    if not r or r[0] < _agora_utc() - timedelta(hours=PATIOS_H):
        res['patios'] = recalcular_patios(cur)
        cur.execute("""INSERT INTO embarques_ancora_meta VALUES ('patios', %s)
                       ON CONFLICT (chave) DO UPDATE SET em = EXCLUDED.em""", (_agora_utc(),))
    res['aprendidas'], res['provas'] = aprender(cur, desde)
    return res


if __name__ == '__main__':
    # Carga inicial em produção (uma vez, depois do deploy):
    #   python -X utf8 embarques_ancora.py --aprender-desde 2026-08-01 [--cache geocode_cache.json]
    import argparse, sys, psycopg2
    from dotenv import load_dotenv
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument('--aprender-desde', required=True)
    ap.add_argument('--cache', default=None, help='JSON {consulta: geocodificação} — grava sem chamar o Google')
    ap.add_argument('--aplicar', action='store_true')
    a = ap.parse_args()
    cn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                          user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
    cur = cn.cursor()
    garantir_tabelas(cur)
    garantir_colunas(cur)
    if a.cache:
        print('coordenadas do cache:', dict(coordenadas_do_cache(cur, json.load(open(a.cache, encoding='utf-8')))))
    print('geocodificados:', dict(geocodificar(cur, limite=400)))
    print('pátios (mantidos, descartados por cliente perto):', recalcular_patios(cur))
    cur.execute("""INSERT INTO embarques_ancora_meta VALUES ('patios', %s)
                   ON CONFLICT (chave) DO UPDATE SET em = EXCLUDED.em""", (_agora_utc(),))
    print('aprendidas, provas:', aprender(cur, a.aprender_desde, tudo=True))
    cur.execute("SELECT COALESCE(geocod_fonte, '(sem tentativa)'), count(*) FROM locais GROUP BY 1 ORDER BY 2 DESC")
    print('locais por geocod_fonte:', cur.fetchall())
    if a.aplicar:
        cn.commit()
        print('GRAVADO')
    else:
        cn.rollback()
        print('[DRY-RUN] nada gravado. Use --aplicar.')
    sys.exit(0)
