# -*- coding: utf-8 -*-
"""Coleta da Insignia GR — guarda TUDO que a gerenciadora sabe das cargas da Rizza (05/10/2026).

Só LÊ a Insignia (ver `insignia.py`) e grava tabelas próprias `insignia_*` que nenhuma régua do
app lê ainda — o motor vem depois, em cima do histórico que esta coleta acumula. É ADITIVA
(HANDOFF-EMBARQUES §0): desligar a chave devolve o app exatamente ao que era.

Por que guardar e não consultar na hora: a API só devolve as SMs ABERTAS e só alcança 30 dias de
paradas. Viagem encerrada sai da lista; parada de 31 dias atrás não existe mais. Sem coletar, o
dado dos terceiros — que a 3S não vê — some.

Ciclo (thread do servidor, `INSIGNIA_COLETA=true`):
  a cada INSIGNIA_INTERVALO_SEG (60) SMs abertas (ficha, status, macro, posição) + posição/odômetro
                                     de todas as placas delas (cavalo E carretas). 60 s = a cadência
                                     do worker da 3S. A Insignia NÃO tem histórico de posições (só a
                                     última): o ponto que não for pego ao vivo se perde — a 3S tem o
                                     backfill da madrugada, a Insignia não.
  SM nova                            rota planejada (pontos obrigatórios + polyline)
  a cada INSIGNIA_PARADAS_MIN (60)   paradas de cada placa desde a última busca; na 1ª vez que a
                                     placa aparece, INSIGNIA_BACKFILL_DIAS (30) para trás
  SM que some da lista               encerrada_em + uma busca final de paradas cobrindo a viagem
  1×/dia                             cadastro de locais da GR

Horários gravados em UTC ingênuo, como `embarques_posicoes_historico` (a API fala em Brasília;
somamos 3 h — com UTC a concordância com a 3S caía de 96% para 41%). A placa é gravada COMO A
INSIGNIA MANDA (`placa`) e a chave Mercosul (`placa_chave`) serve só para cruzar.

    python -X utf8 insignia_coleta.py              # uma rodada completa (banco do .env)
    python -X utf8 insignia_coleta.py --resumo     # o que já foi coletado
"""
import os
import sys
import json
import time as _t
from datetime import datetime, timedelta, timezone

import insignia as I

BRT = timedelta(hours=3)

DDL = """
CREATE TABLE IF NOT EXISTS insignia_sm (
    sm                  BIGINT PRIMARY KEY,
    placa_cavalo        VARCHAR(12),
    placa_cavalo_chave  VARCHAR(10),
    carretas            TEXT,                 -- placas como a GR manda, separadas por vírgula
    carretas_chave      TEXT,
    tecnologia          VARCHAR(40),
    serial_rastreador   VARCHAR(40),
    exclusivo_cliente   VARCHAR(60),
    condutor_cpf        VARCHAR(14),
    condutor_nome       VARCHAR(120),
    status              VARCHAR(40),
    valor_carga         NUMERIC(15,2),
    prev_inicio         TIMESTAMP,
    prev_fim            TIMESTAMP,
    inicio              TIMESTAMP,
    criada_em           TIMESTAMP,
    criada_por          VARCHAR(80),
    origem_cnpj         VARCHAR(14),
    origem_ibge         VARCHAR(7),
    origem_cep          VARCHAR(9),
    origem_endereco     VARCHAR(200),
    origem_lat          DOUBLE PRECISION,
    origem_lng          DOUBLE PRECISION,
    origem_chegada      TIMESTAMP,
    origem_saida        TIMESTAMP,
    destino_cnpj        VARCHAR(14),
    destino_ibge        VARCHAR(7),
    destino_cep         VARCHAR(9),
    destino_endereco    VARCHAR(200),
    destino_lat         DOUBLE PRECISION,
    destino_lng         DOUBLE PRECISION,
    destino_chegada     TIMESTAMP,
    destino_saida       TIMESTAMP,
    link_sm             TEXT,
    link_rotograma      TEXT,
    link_timeline       TEXT,
    ult_pos_em          TIMESTAMP,
    ult_pos_lat         DOUBLE PRECISION,
    ult_pos_lng         DOUBLE PRECISION,
    ult_pos_vel         NUMERIC(6,1),
    ult_pos_ignicao     SMALLINT,
    ult_pos_referencia  VARCHAR(200),
    ult_macro_em        TIMESTAMP,
    ult_macro_nome      VARCHAR(80),
    primeira_vez        TIMESTAMP NOT NULL,   -- quando a coleta viu a SM aberta pela 1ª vez
    ultima_vez          TIMESTAMP NOT NULL,   -- última rodada em que ela estava aberta
    encerrada_em        TIMESTAMP,            -- 1ª rodada em que ela SUMIU da lista (≈ fim da SM)
    paradas_finais_em   TIMESTAMP,            -- busca final de paradas feita
    payload             JSONB NOT NULL        -- a ficha inteira, como veio
);
CREATE INDEX IF NOT EXISTS ix_insignia_sm_cavalo ON insignia_sm (placa_cavalo_chave, inicio);

CREATE TABLE IF NOT EXISTS insignia_sm_operacoes (
    sm              BIGINT NOT NULL,
    ordem           SMALLINT NOT NULL,
    produto         VARCHAR(120),
    valor           NUMERIC(15,2),
    previsao_chegada TIMESTAMP,
    chegada         TIMESTAMP,
    saida           TIMESTAMP,
    cnpj            VARCHAR(14),
    ibge            VARCHAR(7),
    local           VARCHAR(120),
    cep             VARCHAR(9),
    endereco        VARCHAR(200),
    lat             DOUBLE PRECISION,
    lng             DOUBLE PRECISION,
    notas           JSONB,
    PRIMARY KEY (sm, ordem)
);

CREATE TABLE IF NOT EXISTS insignia_sm_eventos (       -- mudança de status visto pela coleta
    sm              BIGINT NOT NULL,
    em              TIMESTAMP NOT NULL,
    campo           VARCHAR(30) NOT NULL,
    anterior        VARCHAR(80),
    novo            VARCHAR(80),
    PRIMARY KEY (sm, em, campo)
);

CREATE TABLE IF NOT EXISTS insignia_macros (           -- a sequência sai do polling (a API só dá a última)
    sm              BIGINT NOT NULL,
    em              TIMESTAMP NOT NULL,
    placa           VARCHAR(12),
    codigo          BIGINT,
    nome            VARCHAR(80),
    descricao       TEXT,
    lat             DOUBLE PRECISION,
    lng             DOUBLE PRECISION,
    referencia      VARCHAR(200),
    coletada_em     TIMESTAMP NOT NULL,
    PRIMARY KEY (sm, em)
);

CREATE TABLE IF NOT EXISTS insignia_posicoes (
    placa_chave     VARCHAR(10) NOT NULL,
    em              TIMESTAMP NOT NULL,
    placa           VARCHAR(12),
    lat             DOUBLE PRECISION,
    lng             DOUBLE PRECISION,
    velocidade      NUMERIC(6,1),
    ignicao         SMALLINT,
    odometro        BIGINT,                         -- cru: a unidade varia por tecnologia
    referencia      VARCHAR(200),
    sm              BIGINT,
    coletada_em     TIMESTAMP NOT NULL,
    PRIMARY KEY (placa_chave, em)
);

CREATE TABLE IF NOT EXISTS insignia_paradas (
    placa_chave     VARCHAR(10) NOT NULL,
    inicio          TIMESTAMP NOT NULL,
    placa           VARCHAR(12),
    fim             TIMESTAMP,
    duracao_s       INTEGER,
    local_inicio    VARCHAR(250),
    local_fim       VARCHAR(250),
    serial          VARCHAR(40),
    coletada_em     TIMESTAMP NOT NULL,
    PRIMARY KEY (placa_chave, inicio)
);
CREATE INDEX IF NOT EXISTS ix_insignia_paradas_fim ON insignia_paradas (placa_chave, fim);

CREATE TABLE IF NOT EXISTS insignia_placas (           -- o que a GR sabe de cada placa que apareceu
    placa_chave     VARCHAR(10) PRIMARY KEY,
    placa           VARCHAR(12),
    papel           VARCHAR(10),                    -- cavalo / carreta (na última SM)
    tecnologia      VARCHAR(40),
    rastreada       BOOLEAN,                        -- a GR devolve posição (ER0022 = não)
    ultimo_codigo   VARCHAR(10),
    ultima_posicao_em TIMESTAMP,
    paradas_ate     TIMESTAMP,                      -- paradas já buscadas até aqui
    primeira_vez    TIMESTAMP NOT NULL,
    ultima_vez      TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS insignia_sm_rota (
    sm              BIGINT PRIMARY KEY,
    pontos          JSONB,                          -- ORIGEM / ENTREGA / PTOBRIG / DESTINO
    polyline        TEXT,                           -- desenho da estrada, polyline precisão 5
    n_pontos        INTEGER,
    km              NUMERIC(9,1),
    coletada_em     TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS insignia_locais (
    codigo          BIGINT PRIMARY KEY,
    cnpj            VARCHAR(14),
    razao_social    VARCHAR(160),
    fantasia        VARCHAR(160),
    ativo           BOOLEAN,
    cep             VARCHAR(9),
    logradouro      VARCHAR(200),
    numero          VARCHAR(30),
    bairro          VARCHAR(120),
    ibge            VARCHAR(7),
    lat             DOUBLE PRECISION,
    lng             DOUBLE PRECISION,
    raio_m          INTEGER,
    poligono        BOOLEAN,
    wkt             TEXT,
    atualizado_em   TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_insignia_locais_cnpj ON insignia_locais (cnpj);
"""


def ligado():
    """`INSIGNIA_COLETA=true` liga a thread. Nasce desligada; ligar pela CLI
    (`docker service update --env-add`), nunca pelo stack do Portainer (§22.10)."""
    return os.getenv('INSIGNIA_COLETA', 'false').strip().lower() == 'true' and I.configurado()


# ── utilidades ──────────────────────────────────────────────────────────────────
def _utc(v):
    d = I.dt(v)
    return d + BRT if d else None


def _chave(p):
    import placas as pl
    return pl.mercosul(pl.limpar(p)) or None


def _s(v, n):
    v = str(v or '').strip()
    return v[:n] or None


def _endereco(e):
    if not isinstance(e, dict):
        return None
    partes = [e.get('sNm_Endereco'), e.get('sNo_Endereco'), e.get('sNm_Bairro')]
    return _s(', '.join(p for p in partes if p and p != 'SN') or None, 200)


def _polyline(pontos):
    """Encoder padrão (precisão 5) — o mesmo formato que `ors_client.decodificar_polyline` lê."""
    out, plat, plng = [], 0, 0
    for lat, lng in pontos:
        for v, ant in ((lat, plat), (lng, plng)):
            d = int(round(v * 1e5)) - int(round(ant * 1e5))
            d = ~(d << 1) if d < 0 else d << 1
            while d >= 0x20:
                out.append(chr((0x20 | (d & 0x1f)) + 63))
                d >>= 5
            out.append(chr(d + 63))
        plat, plng = lat, lng
    return ''.join(out)


# ── gravação ────────────────────────────────────────────────────────────────────
def _gravar_sm(cur, v, agora):
    """Upsert da ficha + operações + macro + posição da SM. Devolve (sm, status_anterior, status_novo)."""
    info = v.get('InfoViagem') or {}
    sm = int(info.get('iCd_Viagem') or 0)
    if not sm:
        return None
    car = [c.get('sCd_Placa') for c in I.lista(v, 'Carretas', 'stCarretasViagem') if c.get('sCd_Placa')]
    o, d = info.get('EnderecoOrigem') or {}, info.get('EnderecoDestino') or {}
    up, um = v.get('UltimaPosicao') or {}, v.get('UltimaMacro') or {}
    cur.execute("SELECT status FROM insignia_sm WHERE sm = %s", (sm,))
    ant = (cur.fetchone() or [None])[0]
    status = _s(info.get('sStatusViagem'), 40)
    lin = {
        'sm': sm, 'placa_cavalo': _s(v.get('sCd_Placa'), 12), 'placa_cavalo_chave': _chave(v.get('sCd_Placa')),
        'carretas': ','.join(car) or None, 'carretas_chave': ','.join(filter(None, map(_chave, car))) or None,
        'tecnologia': _s(v.get('sNm_Tecnologia'), 40), 'serial_rastreador': _s(v.get('sCd_SerialRastreador'), 40),
        'exclusivo_cliente': _s(v.get('sCd_ExclusivoCliente'), 60),
        'condutor_cpf': _s(v.get('sNo_CpfCondutor'), 14), 'condutor_nome': _s(v.get('sNm_Condutor'), 120),
        'status': status, 'valor_carga': I.num(info.get('nVl_Carga')),
        'prev_inicio': _utc(info.get('dDh_PrevInicio')), 'prev_fim': _utc(info.get('dDh_PrevFim')),
        'inicio': _utc(info.get('dDh_Inicio')), 'criada_em': _utc(info.get('dDh_InicioCriacao')),
        'criada_por': _s(info.get('sNm_UsuarioInicioCriacao'), 80),
        'origem_cnpj': _s(info.get('sCd_CnpjOrigem'), 14), 'origem_ibge': _s(o.get('sCd_Municipio'), 7),
        'origem_cep': _s(o.get('sCEP'), 9), 'origem_endereco': _endereco(o),
        'origem_lat': I.num(o.get('sLatitude')), 'origem_lng': I.num(o.get('sLongitude')),
        'origem_chegada': _utc(o.get('dDh_Chegada')), 'origem_saida': _utc(o.get('dDh_Saida')),
        'destino_cnpj': _s(info.get('sCd_CnpjDestinoFinal'), 14), 'destino_ibge': _s(d.get('sCd_Municipio'), 7),
        'destino_cep': _s(d.get('sCEP'), 9), 'destino_endereco': _endereco(d),
        'destino_lat': I.num(d.get('sLatitude')), 'destino_lng': I.num(d.get('sLongitude')),
        'destino_chegada': _utc(d.get('dDh_Chegada')), 'destino_saida': _utc(d.get('dDh_Saida')),
        'link_sm': info.get('sLinkSM') or None, 'link_rotograma': info.get('sLinkRotograma') or None,
        'link_timeline': info.get('sLinkTimeLine') or None,
        'ult_pos_em': _utc(up.get('dDh_GeracaoEv')), 'ult_pos_lat': I.num(up.get('sCd_Latitude')),
        'ult_pos_lng': I.num(up.get('sCd_Longitude')), 'ult_pos_vel': I.num(up.get('nDc_VelocInst')),
        'ult_pos_ignicao': int(up['iSt_Ignicao']) if str(up.get('iSt_Ignicao', '')).isdigit() else None,
        'ult_pos_referencia': _s(up.get('sDc_Referencia'), 200),
        'ult_macro_em': _utc(um.get('dDh_UltMacro')), 'ult_macro_nome': _s(um.get('sNm_Macro'), 80),
        'primeira_vez': agora, 'ultima_vez': agora, 'payload': json.dumps(v, ensure_ascii=False),
    }
    cols = list(lin)
    atualiza = [c for c in cols if c not in ('sm', 'primeira_vez')]
    cur.execute(
        f"INSERT INTO insignia_sm ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
        f"ON CONFLICT (sm) DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in atualiza)}, "
        f"encerrada_em = NULL", [lin[c] for c in cols])
    if ant != status:
        cur.execute("INSERT INTO insignia_sm_eventos (sm, em, campo, anterior, novo) VALUES (%s,%s,'status',%s,%s) "
                    "ON CONFLICT DO NOTHING", (sm, agora, ant, status))
    cur.execute("DELETE FROM insignia_sm_operacoes WHERE sm = %s", (sm,))
    for i, op in enumerate(I.lista(info, 'SequenciaOperacao', 'stSeqOperacao'), 1):
        notas = I.lista(op, 'DocFiscal', 'stDocFiscal')
        cur.execute("""INSERT INTO insignia_sm_operacoes (sm, ordem, produto, valor, previsao_chegada, chegada, saida,
                         cnpj, ibge, local, cep, endereco, lat, lng, notas)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (sm, i, _s(op.get('sNm_Produto'), 120), I.num(op.get('nVl_Produto')),
                     _utc(op.get('dDh_PrevisaoChegada')), _utc(op.get('dDh_Chegada')), _utc(op.get('dDh_Saida')),
                     _s(op.get('sCd_CnpjEmbarcCliente'), 14), _s(op.get('sCd_Municipio'), 7),
                     _s(op.get('sDc_Local'), 120), _s(op.get('sCEP'), 9), _endereco(op),
                     I.num(op.get('sLatitude')), I.num(op.get('sLongitude')),
                     json.dumps(notas, ensure_ascii=False) if notas else None))
    if lin['ult_macro_em']:
        cur.execute("""INSERT INTO insignia_macros (sm, em, placa, codigo, nome, descricao, lat, lng, referencia, coletada_em)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (sm, lin['ult_macro_em'], lin['placa_cavalo'],
                     int(um['iCd_Macro']) if str(um.get('iCd_Macro', '')).isdigit() else None,
                     lin['ult_macro_nome'], um.get('sDc_Macro') or None, I.num(um.get('sCd_Latitude')),
                     I.num(um.get('sCd_Longitude')), _s(um.get('sDc_Referencia'), 200), agora))
    if lin['ult_pos_em'] and lin['placa_cavalo_chave']:
        _gravar_posicao(cur, lin['placa_cavalo_chave'], lin['placa_cavalo'], lin['ult_pos_em'], lin['ult_pos_lat'],
                        lin['ult_pos_lng'], lin['ult_pos_vel'], lin['ult_pos_ignicao'], I.num(up.get('iOdometro')),
                        lin['ult_pos_referencia'], sm, agora)
    for papel, p in [('cavalo', lin['placa_cavalo'])] + [('carreta', c) for c in car]:
        _placa_vista(cur, p, papel, lin['tecnologia'] if papel == 'cavalo' else None, agora)
    return sm, ant, status, [lin['placa_cavalo']] + car


def _gravar_posicao(cur, chave, placa, em, lat, lng, vel, ign, odo, ref, sm, agora):
    if not (em and lat is not None and lng is not None):
        return 0
    cur.execute("""INSERT INTO insignia_posicoes (placa_chave, em, placa, lat, lng, velocidade, ignicao, odometro,
                     referencia, sm, coletada_em)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (chave, em, placa, lat, lng, vel, ign, int(odo) if odo is not None else None, ref, sm, agora))
    return cur.rowcount


def _placa_vista(cur, placa, papel, tecnologia, agora):
    chave = _chave(placa)
    if not chave:
        return
    cur.execute("""INSERT INTO insignia_placas (placa_chave, placa, papel, tecnologia, primeira_vez, ultima_vez)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (placa_chave) DO UPDATE SET placa = EXCLUDED.placa, papel = EXCLUDED.papel,
                     tecnologia = COALESCE(EXCLUDED.tecnologia, insignia_placas.tecnologia),
                     ultima_vez = EXCLUDED.ultima_vez""",
                (chave, _s(placa, 12), papel, tecnologia, agora, agora))


def _gravar_paradas(cur, placa, serial, paradas, agora):
    chave, n = _chave(placa), 0
    for p in paradas:
        ini = _utc(p.get('dtInicio'))
        if not ini:
            continue
        cur.execute("""INSERT INTO insignia_paradas (placa_chave, inicio, placa, fim, duracao_s, local_inicio, local_fim,
                         serial, coletada_em)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (placa_chave, inicio) DO UPDATE SET fim = EXCLUDED.fim, duracao_s = EXCLUDED.duracao_s,
                         local_fim = EXCLUDED.local_fim, coletada_em = EXCLUDED.coletada_em""",
                    (chave, ini, _s(placa, 12), _utc(p.get('dtFim')),
                     int(p['iDuracao']) if str(p.get('iDuracao', '')).isdigit() else None,
                     _s(p.get('sLocalInicio'), 250), _s(p.get('sLocalFim'), 250), _s(serial, 40), agora))
        n += 1
    return n


# ── a rodada ────────────────────────────────────────────────────────────────────
def rodada(conn, agora=None, paradas_min=None, backfill_dias=None):
    """Uma rodada completa. Cada bloco tem o próprio try: falha em um não impede os outros
    (a posição de agora não pode esperar o cadastro de locais)."""
    agora = agora or datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    paradas_min = paradas_min if paradas_min is not None else int(os.getenv('INSIGNIA_PARADAS_MIN', '60'))
    backfill_dias = backfill_dias if backfill_dias is not None else int(os.getenv('INSIGNIA_BACKFILL_DIAS', '30'))
    r = {'sms': 0, 'novas': 0, 'encerradas': 0, 'status': 0, 'posicoes': 0, 'rastreadas': 0,
         'paradas': 0, 'rotas': 0, 'locais': None, 'erros': []}
    cur = conn.cursor()
    cur.execute(DDL)
    conn.commit()

    # 1. SMs abertas
    placas_abertas = []
    try:
        vs, _ = I.viagens()
        cur.execute("SELECT sm FROM insignia_sm WHERE encerrada_em IS NULL")
        abertas_antes = {x[0] for x in cur.fetchall()}
        vistas = set()
        for v in vs:
            g = _gravar_sm(cur, v, agora)
            if not g:
                continue
            sm, ant, novo, pls = g
            vistas.add(sm)
            placas_abertas += [p for p in pls if p]
            r['novas'] += ant is None
            r['status'] += ant is not None and ant != novo
        r['sms'] = len(vistas)
        sumiram = abertas_antes - vistas
        if sumiram:
            cur.execute("UPDATE insignia_sm SET encerrada_em = %s WHERE sm = ANY(%s)", (agora, list(sumiram)))
            for sm in sumiram:
                cur.execute("INSERT INTO insignia_sm_eventos (sm, em, campo, anterior, novo) "
                            "VALUES (%s,%s,'encerrada',NULL,'saiu da lista') ON CONFLICT DO NOTHING", (sm, agora))
            r['encerradas'] = len(sumiram)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        r['erros'].append(f'viagens: {exc}')

    # 2. posição + odômetro de todas as placas das SMs abertas (cavalo e carretas)
    try:
        placas_u = sorted({I.placa_api(p) for p in placas_abertas})
        for i in range(0, len(placas_u), 50):
            for p in I.posicoes(placas_u[i:i + 50]):
                ok = str(p.get('sCode', '')).startswith('OK') and p.get('sCd_Latitude')
                em = _utc(p.get('dDh_GeracaoEv'))
                chave = _chave(p.get('sPlaca'))
                if not chave:
                    continue
                cur.execute("""UPDATE insignia_placas SET rastreada = %s, ultimo_codigo = %s,
                                 ultima_posicao_em = COALESCE(%s, ultima_posicao_em) WHERE placa_chave = %s""",
                            (bool(ok), _s(p.get('sCode'), 10), em if ok else None, chave))
                if ok:
                    r['rastreadas'] += 1
                    r['posicoes'] += _gravar_posicao(
                        cur, chave, p.get('sPlaca'), em, I.num(p.get('sCd_Latitude')), I.num(p.get('sCd_Longitude')),
                        I.num(p.get('nDc_VelocInst')),
                        int(p['iSt_Ignicao']) if str(p.get('iSt_Ignicao', '')).isdigit() else None,
                        I.num(p.get('iOdometro')), _s(p.get('sDc_Referencia'), 200), None, agora)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        r['erros'].append(f'posicoes: {exc}')

    # 3. rota planejada das SMs que ainda não têm
    try:
        cur.execute("""SELECT s.sm FROM insignia_sm s LEFT JOIN insignia_sm_rota r ON r.sm = s.sm
                        WHERE r.sm IS NULL AND s.encerrada_em IS NULL""")
        import geocoding as g
        for (sm,) in cur.fetchall():
            pontos, desenho, _ = I.shape(sm)
            pts = [(I.num(p.get('nLatitude')), I.num(p.get('nLongitude'))) for p in desenho]
            pts = [p for p in pts if None not in p]
            km = sum(g.km_entre(a[0], a[1], b[0], b[1]) or 0 for a, b in zip(pts, pts[1:]))
            pr = [{'seq': int(p.get('iSequencia') or 0), 'tipo': p.get('sTipoPonto'),
                   'lat': I.num(p.get('nLatitude')), 'lng': I.num(p.get('nLongitude'))} for p in pontos]
            cur.execute("""INSERT INTO insignia_sm_rota (sm, pontos, polyline, n_pontos, km, coletada_em)
                           VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (sm) DO NOTHING""",
                        (sm, json.dumps(pr), _polyline(pts) if pts else None, len(pts), round(km, 1), agora))
            conn.commit()
            r['rotas'] += 1
    except Exception as exc:
        conn.rollback()
        r['erros'].append(f'rota: {exc}')

    # 4. paradas: placas rastreadas, de SM aberta ou recém-encerrada, buscadas há mais de `paradas_min`
    try:
        cur.execute("""
            SELECT p.placa_chave, p.placa, p.paradas_ate
              FROM insignia_placas p
             WHERE p.rastreada IS NOT FALSE
               AND p.ultima_vez >= %s
               AND (p.paradas_ate IS NULL OR p.paradas_ate <= %s)""",
                    (agora - timedelta(days=2), agora - timedelta(minutes=paradas_min)))
        for chave, placa, ate in cur.fetchall():
            ini = (ate - timedelta(hours=6)) if ate else (agora - timedelta(days=backfill_dias))
            ini = max(ini, agora - timedelta(days=30) + timedelta(minutes=5))
            serial, ps, rets = I.tempo_parado(placa, ini - BRT, agora - BRT)
            if rets and not rets[0][0].startswith('OK'):
                cur.execute("UPDATE insignia_placas SET ultimo_codigo = %s, paradas_ate = %s WHERE placa_chave = %s",
                            (_s(rets[0][0], 10), agora, chave))
            else:
                r['paradas'] += _gravar_paradas(cur, placa, serial, ps, agora)
                cur.execute("UPDATE insignia_placas SET paradas_ate = %s WHERE placa_chave = %s", (agora, chave))
            conn.commit()
        # busca final das SMs encerradas: a viagem inteira, uma vez
        cur.execute("""SELECT sm, placa_cavalo, COALESCE(inicio, criada_em, primeira_vez), encerrada_em
                         FROM insignia_sm WHERE encerrada_em IS NOT NULL AND paradas_finais_em IS NULL""")
        for sm, placa, ini, fim in cur.fetchall():
            if placa:
                ini = max(ini - timedelta(hours=12), agora - timedelta(days=30) + timedelta(minutes=5))
                serial, ps, rets = I.tempo_parado(placa, ini - BRT, fim - BRT + timedelta(hours=1))
                if not rets or rets[0][0].startswith('OK'):
                    r['paradas'] += _gravar_paradas(cur, placa, serial, ps, agora)
            cur.execute("UPDATE insignia_sm SET paradas_finais_em = %s WHERE sm = %s", (agora, sm))
            conn.commit()
    except Exception as exc:
        conn.rollback()
        r['erros'].append(f'paradas: {exc}')

    # 5. cadastro de locais, 1×/dia
    try:
        cur.execute("SELECT MAX(atualizado_em) FROM insignia_locais")
        ult = cur.fetchone()[0]
        if not ult or ult <= agora - timedelta(hours=24):
            n = 0
            for l in I.locais():
                cur.execute("""INSERT INTO insignia_locais (codigo, cnpj, razao_social, fantasia, ativo, cep, logradouro,
                                 numero, bairro, ibge, lat, lng, raio_m, poligono, wkt, atualizado_em)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (codigo) DO UPDATE SET cnpj = EXCLUDED.cnpj, razao_social = EXCLUDED.razao_social,
                                 fantasia = EXCLUDED.fantasia, ativo = EXCLUDED.ativo, cep = EXCLUDED.cep,
                                 logradouro = EXCLUDED.logradouro, numero = EXCLUDED.numero, bairro = EXCLUDED.bairro,
                                 ibge = EXCLUDED.ibge, lat = EXCLUDED.lat, lng = EXCLUDED.lng, raio_m = EXCLUDED.raio_m,
                                 poligono = EXCLUDED.poligono, wkt = EXCLUDED.wkt, atualizado_em = EXCLUDED.atualizado_em""",
                            (int(l.get('iCodigo') or 0), _s(l.get('sCNPJ'), 14), _s(l.get('sRazaoSocial'), 160),
                             _s(l.get('sFantasia'), 160), (l.get('sAtivo') or '').upper() == 'SIM', _s(l.get('sCEP'), 9),
                             _s(l.get('sLogradouro'), 200), _s(l.get('sNoLogradouro'), 30), _s(l.get('sBairro'), 120),
                             _s(l.get('sMunicipioIBGE'), 7), I.num(l.get('sLatitude')), I.num(l.get('sLongitude')),
                             int(l['iRaio']) if str(l.get('iRaio', '')).isdigit() else None,
                             (l.get('sPoligono') or '').upper() == 'SIM', l.get('sWKTPoligono') or None, agora))
                n += 1
            conn.commit()
            r['locais'] = n
    except Exception as exc:
        conn.rollback()
        r['erros'].append(f'locais: {exc}')
    return r


def resumo(conn):
    cur = conn.cursor()
    cur.execute(DDL)
    conn.commit()
    q = {
        'SMs (abertas / encerradas)': "SELECT count(*) FILTER (WHERE encerrada_em IS NULL), count(*) FILTER (WHERE encerrada_em IS NOT NULL) FROM insignia_sm",
        'placas (rastreadas / sem rastreio na GR)': "SELECT count(*) FILTER (WHERE rastreada), count(*) FILTER (WHERE rastreada IS FALSE) FROM insignia_placas",
        'posições (total / placas)': "SELECT count(*), count(DISTINCT placa_chave) FROM insignia_posicoes",
        'paradas (total / placas / desde)': "SELECT count(*), count(DISTINCT placa_chave), min(inicio) FROM insignia_paradas",
        'macros': "SELECT count(*), count(DISTINCT sm) FROM insignia_macros",
        'rotas planejadas': "SELECT count(*), round(avg(km)) FROM insignia_sm_rota",
        'locais (total / com polígono)': "SELECT count(*), count(*) FILTER (WHERE poligono) FROM insignia_locais",
    }
    for rot, sql in q.items():
        cur.execute(sql)
        print(f'  {rot:<42} {cur.fetchone()}')


def loop():
    """Thread do servidor. Falha nunca derruba a thread nem o app: o pior caso é uma rodada
    sem dado, e a seguinte pega (as SMs abertas continuam abertas; as paradas têm 30 dias)."""
    from server import get_db
    intervalo = int(os.getenv('INSIGNIA_INTERVALO_SEG', '60'))
    while True:
        conn = None
        try:
            conn = get_db()
            r = rodada(conn)
            if r['novas'] or r['encerradas'] or r['erros'] or r['locais']:
                print(f"🛰️  Insignia: {r['sms']} SMs abertas (+{r['novas']} novas, {r['encerradas']} encerradas, "
                      f"{r['status']} mudaram de status) · {r['posicoes']} posições · {r['paradas']} paradas · "
                      f"{r['rotas']} rotas" + (f" · {r['locais']} locais" if r['locais'] else '')
                      + (f" · ERROS: {r['erros']}" if r['erros'] else ''))
        except Exception as exc:
            print(f'⚠️  Insignia: falha na rodada: {exc}')
        finally:
            try:
                conn and conn.close()
            except Exception:
                pass
        _t.sleep(intervalo)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    from dotenv import load_dotenv
    load_dotenv()
    import psycopg2
    conn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                            user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
    if '--resumo' not in sys.argv:
        if not I.configurado():
            sys.exit('faltam INSIGNIA_USER / INSIGNIA_SENHA / INSIGNIA_TOKEN no ambiente')
        t0 = _t.time()
        r = rodada(conn)
        print(f'rodada em {_t.time() - t0:.0f}s: {r}')
    resumo(conn)
