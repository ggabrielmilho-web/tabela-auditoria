# -*- coding: utf-8 -*-
"""Conformidade de SM (solicitação de monitoramento da gerenciadora de risco) — aba /embarques/sm.

Uma linha por MANIFESTO: o que a regra exige, o que a Insignia mostra, e o estado. Recalculada a
cada rodada da fita documental (8×/dia, quando o BI atualiza). Só LÊ o Power BI e a Insignia;
grava só as próprias tabelas (`embarques_sm`, `embarques_sm_log`).

A REGRA (operação, 08/10/2026):
    carga para o RIO DE JANEIRO (qualquer CTe com destinatário/entrega no RJ) → SM, sempre
    valor da carga > R$ 150 mil (soma do `valor_mercadoria` dos CTes do manifesto) → SM, todos
    TERCEIRO até R$ 150 mil → a GR pega só o SINAL do cavalo (sem SM) — tem de haver posição
    Frota/Agregado até R$ 150 mil → nada (a 3S cobre)
    PAGADORES ISENTOS (raiz do CNPJ do pagador) → sem SM, mesmo no RJ: Martins 18485037 e Evidência
    Logística 07632502 (decisões de 08/10/26); outros por EMBARQUES_SM_ISENTOS
Fora da regra o seguro (PGR da gerenciadora) não cobre: é ALERTA MÁXIMO.
Para abrir SM: o "embarcador da viagem" (sCd_CnpjEmbarcViagem) é o CNPJ do PAGADOR do frete (`pagador_cnpj`).

Por que o valor vem do CTe e não do manifesto: é a regra da operação, e o manifesto traz o
`valor_total_mercadoria` do SSW, que pode divergir — guardamos os dois e a tela mostra a diferença.
Por que a Martins é reconhecida pelo pagador e não pelo tipo de documento: a subcontratação dela
(`SUBC REC FORM LISO`) vem com R$ 1 e destino Uberlândia → Uberlândia (formal); outras
subcontratações (Supplog, ILOG, J Macedo) vêm com valor real e precisam da regra. CTe que não é
Martins e vem com valor ≤ R$ 1 (PRIME VIX, 1 caso) cai em `valor_desconhecido`.

Medido em 05–07/10/26 (50 manifestos): 17 SM ok · 5 não exige · 8 Martins · 13 terceiro só-sinal ·
7 SEM SM — os 7 eram exatamente as exceções vistas à mão (HANDOFF-INSIGNIA §16.2).

LIMITE CONHECIDO: a API da Insignia só enxerga a unidade 02572512000158. SM aberta em OUTRA
unidade aparece aqui como "sem SM" — por isso a tela tem a VALIDAÇÃO do responsável. Alerta por
WhatsApp: fora por ora (decisão de 08/10/26, talvez nunca) — as colunas `alerta_*` só ficam reservadas.

PREPARADO, NÃO LIGADO: as colunas `disponibilidade*` (pré-cheque `Get_ConsultaDisponibilidade`) e
`sm_solicitada*` (abrir SM por `Set_SolicitaMonitoramento`) existem e a tela já tem o lugar delas;
nada aqui chama a Insignia — `insignia.chamar` continua recusando escrita.
"""
import os
import re
import json
from datetime import date, datetime, timedelta, timezone

LIMITE_VALOR = float(os.getenv('EMBARQUES_SM_LIMITE_VALOR', '150000'))
MARTINS_RAIZ = '18485037'
# Pagadores ISENTOS de SM (decisão da operação), pela raiz do CNPJ do pagador do frete:
#   Martins (08/10/26) — subcontratação; vem com R$ 1 e destino formal
#   Evidência Logística (08/10/26) — subcontratação (SUBC FEC FORM CTRC), com valor real
# Para incluir outro sem deploy: EMBARQUES_SM_ISENTOS="12345678:NOME,87654321:OUTRO" (soma a estes).
ISENTOS = {MARTINS_RAIZ: 'Martins', '07632502': 'Evidência'}
for _it in os.getenv('EMBARQUES_SM_ISENTOS', '').split(','):
    if ':' in _it:
        _r, _n = _it.split(':', 1)
        if _r.strip().isdigit():
            ISENTOS[_r.strip().zfill(8)[:8]] = _n.strip() or _r.strip()


def isento_de(cte):
    """Nome do pagador isento deste CTe, ou None."""
    return ISENTOS.get(str(cte.get('cnpj_pagador') or '').zfill(14)[:8])
UF_RIO = 'RJ'
# SM: a coleta da Insignia existe desde 05/10/26 ~15h BRT. Manifesto anterior a isso sem SM achada é
# "sem dado", não "sem SM" (a SM pode ter aberto e fechado antes de a coleta existir).
SM_DESDE = date.fromisoformat(os.getenv('EMBARQUES_SM_SM_DESDE', '2026-10-06'))
# SINAL do cavalo sem SM: a coleta só segue esses cavalos desde 08/10/26 (`INSIGNIA_COLETA_CARGAS`).
SINAL_DESDE = date.fromisoformat(os.getenv('EMBARQUES_SM_SINAL_DESDE', '2026-10-08'))
SM_TOLERANCIA_D = 2          # SM casa com o manifesto se começou até 2 dias antes/depois (= fontes_gps.tem_sm)
SM_TARDIA_H = 1.0            # SM aberta mais de 1 h depois da saída real = o caminhão rodou descoberto

# estado → (severidade, rótulo). A severidade ordena a tela e os cards.
ESTADOS = {
    'sem_sm':             ('alerta', 'Exige SM e não tem'),
    'sem_sinal':          ('alerta', 'Terceiro sem sinal na GR'),
    'sm_tardia':          ('atencao', 'SM aberta depois da saída'),
    'sm_divergente':      ('atencao', 'SM com outro conjunto'),
    'valor_desconhecido': ('atencao', 'Valor da carga desconhecido'),
    'aguardando_cte':     ('pendente', 'Aguardando CTe'),
    'sem_dado':           ('pendente', 'Sem dado da GR no período'),
    'sm_ok':              ('ok', 'SM ok'),
    'sinal_ok':           ('ok', 'Sinal ok (sem SM)'),
    'nao_exige':          ('ok', 'Não exige'),
    'isento':             ('ok', 'Isento (pagador dispensado)'),
}

DDL = """
CREATE TABLE IF NOT EXISTS embarques_sm (
    manifesto           VARCHAR(20) PRIMARY KEY,     -- normalizado (sem espaço/pontuação)
    manifesto_exibir    VARCHAR(24),
    data_emissao        DATE,
    unidade_origem      VARCHAR(10),
    cavalo              VARCHAR(10),
    carreta             VARCHAR(10),
    motorista           VARCHAR(120),
    motorista_cpf       VARCHAR(14),
    proprietario        VARCHAR(160),
    tipo                VARCHAR(10),                 -- Frota / Agregado / Terceiro
    tipo_fonte          VARCHAR(10),                 -- carga (do robô) / cadastro
    carga_id            INTEGER,
    carga_numero        VARCHAR(20),
    carga_status        VARCHAR(20),
    saida_real          TIMESTAMP,                   -- UTC, da carga
    -- a regra
    valor_cte           NUMERIC(14,2),               -- soma do valor_mercadoria dos CTes (exceto pagadores isentos)
    valor_manifesto     NUMERIC(14,2),               -- valor_total_mercadoria do manifesto, para conferência
    n_ctes              INTEGER,
    n_ctes_martins      INTEGER,                     -- CTes de pagador ISENTO (nome histórico: nasceu só com a Martins)
    uf_destinos         VARCHAR(60),
    rio                 BOOLEAN,
    exige               VARCHAR(10),                 -- sm / sinal / nada / isento / ?
    motivos             VARCHAR(80),                 -- rio, valor, terceiro
    ctes                JSONB,
    -- o que a GR mostra
    sm                  BIGINT,
    sm_status           VARCHAR(40),
    sm_inicio           TIMESTAMP,                   -- UTC: quando a SM foi PEDIDA (criada_em)
    sm_encerrada_em     TIMESTAMP,
    sm_valor            NUMERIC(14,2),
    sm_conjunto         VARCHAR(20),                 -- igual / so_cavalo / so_carreta
    sm_link             TEXT,
    sm_atraso_h         NUMERIC(8,2),                -- SM pedida − saída real (positivo = tardia)
    sinal_pontos        INTEGER,
    sinal_ultimo        TIMESTAMP,
    -- o estado
    estado              VARCHAR(24) NOT NULL,
    severidade          VARCHAR(10) NOT NULL,
    detalhe             TEXT,
    calculado_em        TIMESTAMP NOT NULL,
    primeira_vez        TIMESTAMP NOT NULL,
    estado_desde        TIMESTAMP NOT NULL,
    sumiu_em            TIMESTAMP,                   -- manifesto saiu do BI (cancelado)
    -- validação do responsável (é ela que valida a régua antes de o alerta ligar)
    validacao           VARCHAR(20),                 -- confirmado / falso_alerta / justificado
    validacao_obs       TEXT,
    validado_por        VARCHAR(120),
    validado_em         TIMESTAMP,
    -- alerta por WhatsApp (preparado, NÃO ligado)
    alerta_enviado_em   TIMESTAMP,
    alerta_para         TEXT,
    -- pré-cheque e abertura de SM pela API (preparado, NÃO ligado)
    disponibilidade     JSONB,                       -- retorno do Get_ConsultaDisponibilidade
    disponibilidade_em  TIMESTAMP,
    sm_solicitada_em    TIMESTAMP,
    sm_solicitada_por   VARCHAR(120),
    sm_retorno          JSONB                        -- retorno do Set_SolicitaMonitoramento
);
-- 08/10/26: cliente (da carga; sem carga, o tomador dos CTes) e a ordem de coleta (OC) com quem a abriu
ALTER TABLE embarques_sm ADD COLUMN IF NOT EXISTS cliente VARCHAR(160);
ALTER TABLE embarques_sm ADD COLUMN IF NOT EXISTS oc VARCHAR(20);
ALTER TABLE embarques_sm ADD COLUMN IF NOT EXISTS oc_embarcador VARCHAR(60);
-- o "embarcador da viagem" da SM (sCd_CnpjEmbarcViagem) é o CNPJ do PAGADOR do frete (Gabriel, 08/10/26)
ALTER TABLE embarques_sm ADD COLUMN IF NOT EXISTS pagador_cnpj VARCHAR(14);
-- a GR cadastrou o cavalo/carreta na OUTRA grafia (antiga × Mercosul): rótulo, não estado — "cavalo HKE0G75 → GR: HKE-0675"
ALTER TABLE embarques_sm ADD COLUMN IF NOT EXISTS placa_gr_divergente VARCHAR(80);
CREATE INDEX IF NOT EXISTS ix_embarques_sm_data ON embarques_sm (data_emissao);
CREATE INDEX IF NOT EXISTS ix_embarques_sm_estado ON embarques_sm (estado);
CREATE TABLE IF NOT EXISTS embarques_sm_log (
    id          BIGSERIAL PRIMARY KEY,
    manifesto   VARCHAR(20) NOT NULL,
    em          TIMESTAMP NOT NULL DEFAULT NOW(),
    autor       VARCHAR(120) NOT NULL,
    campo       VARCHAR(30) NOT NULL,
    antes       TEXT,
    depois      TEXT
);
CREATE INDEX IF NOT EXISTS ix_embarques_sm_log_man ON embarques_sm_log (manifesto, em);
"""

CE = "'public conhecimentos_emitidos'"
CE_COLS = ('serie_numero_ctrc', 'tipo_documento', 'data_emissao', 'primeiro_manifesto', 'ultimo_manifesto',
           'valor_mercadoria', 'cnpj_pagador', 'cliente_pagador', 'cliente_destinatario',
           'cidade_entrega', 'uf_entrega', 'uf_destinatario')
M = "'public manifestos'"


def norm(s):
    return re.sub(r'[^A-Za-z0-9]', '', str(s or '')).upper()


def placa(p):
    import placas as pl
    return pl.mercosul(p or '') or None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _dia(v):
    if not v:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v)[:19]).date()
    except ValueError:
        return None


# ── a regra (função pura) ─────────────────────────────────────────────────────────────────────────
def regra(ctes, tipo):
    """`ctes` = lista de dicts com valor_mercadoria / cnpj_pagador / uf_entrega / uf_destinatario.
    Devolve dict com exige ('sm'|'sinal'|'nada'|'isento'|'?'), motivos, valor, rio e contagens."""
    martins = [c for c in ctes if isento_de(c)]      # todos os pagadores isentos, não só a Martins
    resto = [c for c in ctes if c not in martins]
    valor = sum(_num(c.get('valor_mercadoria')) for c in resto)
    ufs = sorted({u for c in resto for u in (c.get('uf_entrega'), c.get('uf_destinatario')) if u})
    rio = UF_RIO in ufs
    out = {'valor': round(valor, 2), 'rio': rio, 'ufs': ufs, 'n': len(ctes), 'n_martins': len(martins), 'isentos': [],
           'motivos': [], 'exige': None, 'valor_conhecido': True}
    if not ctes:
        out['exige'] = '?'
        return out
    if not resto:
        out['exige'] = 'isento'
        out['motivos'] = ['isento']
        out['isentos'] = sorted({isento_de(c) for c in martins})
        return out
    # valor formal (≤ R$ 1 em todos) fora da Martins: não dá para dizer se passa de 150 mil
    out['valor_conhecido'] = any(_num(c.get('valor_mercadoria')) > 1 for c in resto)
    if rio:
        out['motivos'].append('rio')
    if valor > LIMITE_VALOR:
        out['motivos'].append('valor')
    if out['motivos']:
        out['exige'] = 'sm'
    elif not out['valor_conhecido']:
        out['exige'] = '?'
    elif tipo in ('Frota', 'Agregado'):
        out['exige'] = 'nada'
    else:                                   # Terceiro, ou tipo desconhecido: o conservador é exigir sinal
        out['exige'] = 'sinal'
        out['motivos'].append('terceiro')
    return out


def avaliar(r, dia, sm, sinal, saida_real, conjunto):
    """O estado a partir da regra (`r`), da SM casada (ou None) e do sinal. Função pura."""
    if r['exige'] == '?' and r['n'] == 0:
        return 'aguardando_cte', 'nenhum CTe do manifesto chegou ao BI ainda'
    if r['exige'] == 'isento':
        return 'isento', ('todos os CTes são de pagador isento (' + ', '.join(r.get('isentos') or []) +
                          ') — sem SM por decisão da operação')
    if r['exige'] == '?':
        return 'valor_desconhecido', 'CTes com valor formal (≤ R$ 1) fora dos isentos — não dá para aplicar o limite de R$ 150 mil'
    if r['exige'] == 'nada':
        return 'nao_exige', f"Frota/Agregado até R$ {LIMITE_VALOR:,.0f} fora do RJ — a 3S cobre".replace(',', '.')
    if r['exige'] == 'sinal':
        if sm:
            return 'sm_ok', 'terceiro até R$ 150 mil com SM (a regra pede só o sinal)'
        if sinal and sinal[0] > 0:
            return 'sinal_ok', f'cavalo com {sinal[0]} posição(ões) na GR'
        if dia < SINAL_DESDE:
            return 'sem_dado', f'a coleta de sinal sem SM só existe desde {SINAL_DESDE:%d/%m}'
        return 'sem_sinal', 'terceiro sem SM e o cavalo não tem posição na GR — o seguro não cobre'
    # exige SM
    if not sm:
        if dia < SM_DESDE:
            return 'sem_dado', f'a coleta de SM da Insignia só existe desde {SM_DESDE:%d/%m}'
        return 'sem_sm', 'exige SM (' + ' + '.join(r['motivos']) + ') e nenhuma SM da unidade casa com o conjunto'
    if conjunto != 'igual':
        return 'sm_divergente', ('a SM casa só pelo cavalo — carreta diferente' if conjunto == 'so_cavalo'
                                 else 'a SM casa só pela carreta — cavalo diferente')
    if saida_real and sm.get('pedida'):
        # saída anterior ao dia do manifesto é a "saída adiantada" conhecida do motor (passagem pela
        # origem antes de carregar, HANDOFF-INSIGNIA §15.3 C) — com ela o atraso não se julga
        if (saida_real - timedelta(hours=3)).date() < dia:
            return 'sm_ok', f"SM {sm['sm']} (saída registrada antes da emissão — atraso não avaliado)"
        atraso = (sm['pedida'] - saida_real).total_seconds() / 3600
        if atraso > SM_TARDIA_H:
            return 'sm_tardia', f'SM pedida {atraso:.1f} h depois da saída real — o caminhão rodou descoberto'
    return 'sm_ok', f"SM {sm['sm']}"


# ── leitura ───────────────────────────────────────────────────────────────────────────────────────
def coletar_ctes(tok, desde):
    import embarques_auto as e
    q = (f"EVALUATE SELECTCOLUMNS(FILTER({CE}, {CE}[data_emissao] >= DATE({desde.year},{desde.month},{desde.day})), "
         + ', '.join(f'"{c}",{CE}[{c}]' for c in CE_COLS) + ')')
    return e._dax(tok, q)


def coletar_manifestos(tok, desde):
    import embarques_auto as e
    m = e._dax(tok, f"EVALUATE FILTER({M}, {M}[data_emissao] >= DATE({desde.year},{desde.month},{desde.day}))")
    return {e._norm(r['CHAVE_MANIFESTO']): r for r in m if r.get('CHAVE_MANIFESTO')}


def _sms(cur, desde):
    cur.execute("""SELECT sm, placa_cavalo_chave, COALESCE(carretas_chave, ''), status, inicio, criada_em,
                          encerrada_em, valor_carga, link_sm
                     FROM insignia_sm WHERE COALESCE(inicio, criada_em) >= %s""", (desde - timedelta(days=3),))
    out = []
    for sm, cav, cars, st, ini, cri, enc, val, link in cur.fetchall():
        # O instante que conta é o PEDIDO (`criada_em`), não o `inicio`: a GR carimba o início dias
        # depois do pedido (SM 211360: criada 03/10 18:50, início 05/10; a carga saiu 03/10 19:12 —
        # coberta). Com o início, ela parecia 48 h tardia e casava com a viagem SEGUINTE da carreta.
        t = cri or ini
        out.append({'sm': sm, 'cavalo': cav, 'carretas': [c for c in cars.split(',') if c], 'status': st,
                    'pedida': t, 'inicio': ini, 'dia': (t - timedelta(hours=3)).date() if t else None,
                    'encerrada_em': enc, 'valor': val, 'link': link})
    return out


def casar_sm(sms, cavalo, carreta, dia):
    """A SM do conjunto mais perto do dia do manifesto. Devolve (sm, conjunto).
      * conjunto igual: pedida até 2 dias antes/depois (= `fontes_gps.tem_sm`)
      * só cavalo ou só carreta: até 1 dia — com 2, a SM da viagem ANTERIOR do cavalo casava
      * SM encerrada antes do dia do manifesto não cobre o manifesto (HIK5A05: SM fechada 07/10,
        manifesto 08/10 com outra carreta)"""
    cands = []
    for s in sms:
        if not s['dia']:
            continue
        if s['encerrada_em'] and (s['encerrada_em'] - timedelta(hours=3)).date() < dia:
            continue
        dd = abs((s['dia'] - dia).days)
        c_ok = bool(cavalo) and s['cavalo'] == cavalo
        r_ok = bool(carreta) and carreta in s['carretas']
        if not (c_ok or r_ok):
            continue
        conj = 'igual' if (c_ok and (r_ok or not carreta)) else ('so_cavalo' if c_ok else 'so_carreta')
        if dd <= (SM_TOLERANCIA_D if conj == 'igual' else 1):
            cands.append((conj != 'igual', dd, s, conj))
    if not cands:
        return None, None
    cands.sort(key=lambda x: (x[0], x[1]))
    return cands[0][2], cands[0][3]


def _sinal(cur, cavalo, dia, fim):
    if not cavalo:
        return (0, None)
    cur.execute("SELECT to_regclass('insignia_posicoes') IS NOT NULL")
    if not cur.fetchone()[0]:
        return (0, None)
    cur.execute("""SELECT count(*), max(em) FROM insignia_posicoes
                    WHERE placa_chave = %s AND em >= %s AND em < %s""",
                (cavalo, datetime.combine(dia, datetime.min.time()) - timedelta(days=1) + timedelta(hours=3), fim))
    return cur.fetchone()


def _cargas(cur, desde):
    cur.execute("""SELECT id, numero, status, tipo_operacao, manifesto_origem, data_saida_real, data_conclusao,
                          cliente_nome, embarcador, coleta_origem
                     FROM embarques_cargas
                    WHERE manifesto_origem IS NOT NULL AND data_carregamento >= %s""", (desde - timedelta(days=10),))
    return {norm(r[4]): r for r in cur.fetchall()}


def _ordens(cur):
    """manifesto → (OC, quem abriu), da `embarques_programacao` (a aba Coletas). Sem a tabela, vazio."""
    if not _tem(cur, 'embarques_programacao'):
        return {}
    cur.execute("""SELECT manifesto, coleta_origem, embarcador FROM embarques_programacao
                     WHERE manifesto IS NOT NULL AND sumiu_em IS NULL
                     ORDER BY COALESCE(comandada_em, cadastrada_em)""")
    return {norm(m): (oc, emb) for m, oc, emb in cur.fetchall()}


def _grafias_gr(cur):
    """chave Mercosul → a grafia que a GR usa, só para as placas que a coleta achou na outra grafia."""
    if not _tem(cur, 'insignia_placas'):
        return {}
    cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'insignia_placas' AND column_name = 'placa_gr'")
    if not cur.fetchone():
        return {}
    cur.execute("SELECT placa_chave, placa_gr FROM insignia_placas WHERE placa_gr IS NOT NULL")
    return dict(cur.fetchall())


def divergencia_gr(grafias_gr, cavalo_ssw, carreta_ssw):
    """Texto do rótulo quando a GR escreve a placa diferente do SSW (a placa do SSW é a verdade). Função pura."""
    import placas as pl
    out = []
    for papel, p in (('cavalo', cavalo_ssw), ('carreta', carreta_ssw)):
        if not p:
            continue
        gr = grafias_gr.get(pl.mercosul(p) or '')
        if gr and pl.limpar(gr) != pl.limpar(p):
            out.append(f'{papel} {pl.limpar(p)} → GR: {gr}')
    return '; '.join(out)[:80] or None


def _tomador(ctes, campo='cliente_pagador'):
    """O tomador mais frequente entre os CTes que não são da Martins (o cliente quando não há carga).
    Com `campo='cnpj_pagador'`, o CNPJ dele — é o embarcador da viagem na SM."""
    from collections import Counter
    c = Counter(x.get(campo) for x in ctes if x.get(campo) and not isento_de(x))
    if not c:
        c = Counter(x.get(campo) for x in ctes if x.get(campo))
    return c.most_common(1)[0][0] if c else None


# ── gravação ──────────────────────────────────────────────────────────────────────────────────────
CAMPOS = ('manifesto_exibir', 'data_emissao', 'unidade_origem', 'cavalo', 'carreta', 'motorista', 'motorista_cpf',
          'proprietario', 'tipo', 'tipo_fonte', 'carga_id', 'carga_numero', 'carga_status', 'saida_real',
          'valor_cte', 'valor_manifesto', 'n_ctes', 'n_ctes_martins', 'uf_destinos', 'rio', 'exige', 'motivos',
          'ctes', 'sm', 'sm_status', 'sm_inicio', 'sm_encerrada_em', 'sm_valor', 'sm_conjunto', 'sm_link',
          'sm_atraso_h', 'sinal_pontos', 'sinal_ultimo', 'estado', 'severidade', 'detalhe',
          'cliente', 'oc', 'oc_embarcador', 'pagador_cnpj', 'placa_gr_divergente')


def atualizar(conn, tok, manifestos=None, cadastro=None, desde=None, agora=None):
    """Recalcula a conformidade dos manifestos. `manifestos` = o retrato da fita (indexado por
    `_norm(CHAVE_MANIFESTO)`); sem ele (backfill, `desde`), lê do BI. Devolve {estado: n}."""
    agora = agora or datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    cur = conn.cursor()
    cur.execute(DDL)
    if manifestos is None:
        desde = desde or (date.today() - timedelta(days=7))
        manifestos = coletar_manifestos(tok, desde)
    if not manifestos:                               # BI fora / extração vazia: não decide nada
        conn.commit()
        return {}
    dias = [d for d in (_dia(m.get('data_emissao')) for m in manifestos.values()) if d]
    desde = min(dias) if dias else (desde or date.today() - timedelta(days=7))
    ctes = coletar_ctes(tok, desde - timedelta(days=10))
    por_man = {}
    for c in ctes:
        for k in {norm(c.get('primeiro_manifesto')), norm(c.get('ultimo_manifesto'))} - {''}:
            por_man.setdefault(k, []).append(c)
    sms = _sms(cur, desde) if _tem(cur, 'insignia_sm') else []
    cargas = _cargas(cur, desde)
    ordens = _ordens(cur)
    grafias_gr = _grafias_gr(cur)
    vendidas = frozenset()
    if cadastro is not None:
        try:
            from embarques_auto import _config, classificar
            vendidas = _config()['vendidas']
        except Exception:
            pass

    cur.execute("SELECT manifesto, estado FROM embarques_sm")
    antes = dict(cur.fetchall())
    contagem = {}
    for k, m in manifestos.items():
        dia = _dia(m.get('data_emissao'))
        if not dia:
            continue
        cav, car = placa(m.get('placa_cavalo')), placa(m.get('placa_carreta'))
        cg = cargas.get(k)
        if cg:
            tipo, tipo_fonte = cg[3], 'carga'
        elif cadastro is not None:
            from embarques_auto import classificar
            tipo, tipo_fonte = classificar(m.get('placa_cavalo'), m.get('placa_carreta'), cadastro, vendidas), 'cadastro'
        else:
            tipo, tipo_fonte = None, None
        lista = por_man.get(k, [])
        r = regra(lista, tipo)
        sm, conj = casar_sm(sms, cav, car, dia)
        saida = cg[5] if cg else None
        fim = (cg[6] if cg and cg[6] else agora)
        sinal = _sinal(cur, cav, dia, fim) if r['exige'] == 'sinal' and not sm else (0, None)
        estado, detalhe = avaliar(r, dia, sm, sinal, saida, conj)
        sev = ESTADOS[estado][0]
        atraso = round((sm['pedida'] - saida).total_seconds() / 3600, 2) if sm and saida and sm.get('pedida') else None
        lin = {
            'manifesto_exibir': m.get('CHAVE_MANIFESTO'), 'data_emissao': dia,
            'unidade_origem': m.get('unidade_origem'), 'cavalo': cav, 'carreta': car,
            'motorista': (m.get('nome_motorista') or '')[:120] or None,
            'motorista_cpf': re.sub(r'\D', '', str(m.get('cpf_motorista') or '')) or None,
            'proprietario': (m.get('proprietario_cavalo') or '')[:160] or None,
            'tipo': tipo, 'tipo_fonte': tipo_fonte,
            'carga_id': cg[0] if cg else None, 'carga_numero': cg[1] if cg else None,
            'carga_status': cg[2] if cg else None, 'saida_real': saida,
            'valor_cte': r['valor'], 'valor_manifesto': _num(m.get('valor_total_mercadoria')) or None,
            'n_ctes': r['n'], 'n_ctes_martins': r['n_martins'], 'uf_destinos': ','.join(r['ufs'])[:60] or None,
            'rio': r['rio'], 'exige': r['exige'], 'motivos': ','.join(r['motivos']) or None,
            'ctes': json.dumps([{'ctrc': c.get('serie_numero_ctrc'), 'tipo': c.get('tipo_documento'),
                                 'valor': _num(c.get('valor_mercadoria')), 'pagador': c.get('cliente_pagador'),
                                 'martins': bool(isento_de(c)), 'isento': isento_de(c),
                                 'destinatario': c.get('cliente_destinatario'),
                                 'entrega': f"{c.get('cidade_entrega') or ''}/{c.get('uf_entrega') or ''}"}
                                for c in lista], ensure_ascii=False, default=str),
            'sm': sm['sm'] if sm else None, 'sm_status': sm['status'] if sm else None,
            'sm_inicio': sm['pedida'] if sm else None, 'sm_encerrada_em': sm['encerrada_em'] if sm else None,
            'sm_valor': sm['valor'] if sm else None, 'sm_conjunto': conj, 'sm_link': sm['link'] if sm else None,
            'sm_atraso_h': atraso, 'sinal_pontos': sinal[0], 'sinal_ultimo': sinal[1],
            'estado': estado, 'severidade': sev, 'detalhe': detalhe,
            'cliente': ((cg[7] if cg else None) or _tomador(lista) or '')[:160] or None,
            'oc': (ordens.get(k) or (None, None))[0] or (cg[9] if cg else None),
            'oc_embarcador': (ordens.get(k) or (None, None))[1] or (cg[8] if cg else None),
            'placa_gr_divergente': divergencia_gr(grafias_gr, m.get('placa_cavalo'), m.get('placa_carreta')),
            'pagador_cnpj': (re.sub(r'\D', '', str(_tomador(lista, 'cnpj_pagador') or '')).zfill(14)
                             if _tomador(lista, 'cnpj_pagador') else None),
        }
        cols = ', '.join(CAMPOS)
        cur.execute(f"""
            INSERT INTO embarques_sm (manifesto, {cols}, calculado_em, primeira_vez, estado_desde, sumiu_em)
            VALUES (%s, {', '.join(['%s'] * len(CAMPOS))}, %s, %s, %s, NULL)
            ON CONFLICT (manifesto) DO UPDATE SET
              {', '.join(f'{c} = EXCLUDED.{c}' for c in CAMPOS)},
              calculado_em = EXCLUDED.calculado_em, sumiu_em = NULL,
              estado_desde = CASE WHEN embarques_sm.estado = EXCLUDED.estado THEN embarques_sm.estado_desde
                                  ELSE EXCLUDED.estado_desde END""",
                    [k] + [lin[c] for c in CAMPOS] + [agora, agora, agora])
        if antes.get(k) != estado:
            cur.execute("INSERT INTO embarques_sm_log (manifesto, em, autor, campo, antes, depois) "
                        "VALUES (%s, %s, 'Regra SM', 'estado', %s, %s)", (k, agora, antes.get(k), estado))
        contagem[estado] = contagem.get(estado, 0) + 1

    # manifesto que estava na janela e saiu do BI = cancelado: marca, não apaga (a validação fica)
    if dias:
        cur.execute("""UPDATE embarques_sm SET sumiu_em = %s
                        WHERE sumiu_em IS NULL AND data_emissao >= %s AND NOT (manifesto = ANY(%s))""",
                    (agora, min(dias), list(manifestos)))
        contagem['sumiu'] = cur.rowcount
    conn.commit()
    return contagem


def _tem(cur, tabela):
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (tabela,))
    return cur.fetchone()[0]


def validar(conn, manifesto, validacao, obs, usuario):
    """Grava a validação do responsável (confirmado / falso_alerta / justificado), com log."""
    if validacao not in ('confirmado', 'falso_alerta', 'justificado', ''):
        raise ValueError('validação inválida')
    cur = conn.cursor()
    cur.execute("SELECT validacao, validacao_obs FROM embarques_sm WHERE manifesto = %s FOR UPDATE", (manifesto,))
    r = cur.fetchone()
    if not r:
        raise KeyError(manifesto)
    agora = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    novo = validacao or None
    cur.execute("""UPDATE embarques_sm SET validacao = %s, validacao_obs = %s, validado_por = %s, validado_em = %s
                    WHERE manifesto = %s""", (novo, (obs or '').strip() or None, usuario, agora, manifesto))
    for campo, a, d in (('validacao', r[0], novo), ('validacao_obs', r[1], (obs or '').strip() or None)):
        if a != d:
            cur.execute("INSERT INTO embarques_sm_log (manifesto, em, autor, campo, antes, depois) VALUES (%s,%s,%s,%s,%s,%s)",
                        (manifesto, agora, usuario or '?', campo, a, d))
    conn.commit()


if __name__ == '__main__':
    # backfill / conferência à mão:  python -X utf8 embarques_sm.py [--desde AAAA-MM-DD]
    import sys
    from server import get_token, get_db
    import embarques_auto as e
    d = date.fromisoformat(sys.argv[sys.argv.index('--desde') + 1]) if '--desde' in sys.argv else date.today() - timedelta(days=7)
    tok = get_token()
    try:
        cad = e.carregar_cadastro(tok)
    except Exception as exc:
        print(f'cadastro indisponível ({exc}) — tipo só pelas cargas')
        cad = None
    conn = get_db()
    print(atualizar(conn, tok, cadastro=cad, desde=d))
