# -*- coding: utf-8 -*-
"""TORRE DE CONTROLE — a consulta única (desenho acordado em 25/09/2026).

`montar(cur, dia=None)` devolve tudo o que a tela mostra, em dois modos:

  AO VIVO (dia=None ou hoje)
      o estado da carga é o STATUS; o "confere" cruza o status com as DATAS da carga, e a
      divergência aparece na tela (na 1ª simulação: 3 cargas 'No destino' sem chegada).

  RETRATO DE UM DIA (dia < hoje)
      o instante é a meia-noite daquele dia (Brasília) e o estado é DEDUZIDO DAS DATAS
      (criação, saída, chegada, desengate, conclusão). O status gravado é o de hoje e não serve
      para o passado — o worker muda status sem gravar log. É "o que hoje sabemos daquele dia",
      não "o que a tela mostrava naquele dia": o motor atemporal corrige datas depois, e o
      retrato do dia melhora junto.

Blocos:
    relogios   idade do GPS, dos documentos e do motor (só ao vivo)
    caixas     ESTOQUE no instante + FLUXO do dia + IDADE, por etapa
    frota      estado de cada carreta: carga ativa + GPS (até 12 h). NUNCA lê perna vazia —
               a perna é registro do passado (simulação de 25/09: 99,4% coerente sem ela)
    excecoes   o que precisa de alguém, com a regra que disparou
    proximas   coletas com prazo nas próximas 12 h × carretas livres (só ao vivo)

PRAZO DA ENTREGA (29/09/26, Gabriel): a prioridade é a ENTREGA AGENDADA que o embarcador escreve
na observação da ordem de coleta ("AGENDA 02/10" → `embarques_programacao.agendamento`, ligada à
carga por `carga_id`); vale até 23:59 de Brasília daquele dia. Sem ela, a conta dos 600 km/dia
com 20% de folga. Com agenda: "Entrega agendada vencida" (alta, também para carga que nem saiu),
"Vai atrasar" (a chegada estimada já não cabe no dia) e a pontualidade das chegadas do dia.

COLETA É ANÁLISE DO DIA (decisão do Gabriel, 25/09): entram as ordens cadastradas ou com
prazo no dia; ficam em aberto até a meia-noite e o dia seguinte começa limpo. O que ficou
para trás só aparece filtrando o dia anterior.

Só leitura. Painel igual para todos. Carreteiro/Terceiro fora (contados). O km NUNCA vem de
`embarques_cargas_rastreio_kpi`: o gate de 25/09 mostrou que diverge da tela em 184 de 407
viagens — produtividade/km entram depois, pela mesma função da tela.

TERCEIRO (09/10/26): desde 07/10 o robô abre carga de Terceiro (GPS do cavalo pela Insignia). A
CARGA entra nas caixas e nas exceções, com o GPS lido pela camada `fontes_gps`; a CARRETA não é
nossa e não entra na frota, e a frota segue lendo a 3S (custo — ver `_ultima_posicao`). Depois da
entrega, o terceiro fica até 48 h em "livres para carga" para ser aproveitado numa volta, com
contagem própria (`_terceiros_livres`). As ORDENS de Terceiro seguem só contadas.
"""
import os
import re
from collections import Counter
from datetime import date, datetime, timedelta

import fontes_gps
import geocoding
import placas as pl

# ── réguas (defaults do desenho, calibradas na simulação de 25/09) ───────────────────────────
KM_DIA = float(os.getenv('KM_DIA_PADRAO', '600'))       # previsão até existir agendamento
FOLGA_PREVISAO = 1.20                                   # "atrasando" só com 20% de folga
FOLGA_CURTA_H = 4.0                                     # perna < 300 km: carga/descarga pesa
GPS_ATUAL_H = 12.0                                      # posição até 12 h vale como atual
R_PATIO_KM = 3.0                                        # pátio = Uberlândia (coordenada da Rizza)
LIM_DOC_SEM_SAIDA_H = 12.0
LIM_PARADA_ESTRADA_H = 2.0
LIM_PARADA_NOITE_H = 11.0      # 22h-6h (Brasília) é descanso: só acende passando da interjornada.
                               # Retrato de 19/09 à meia-noite: 10 "paradas na estrada" — motorista dormindo
LIM_NO_DESTINO_H = 24.0
LIM_DESENGATADA_D = 3.0
PROXIMAS_H = 12
PARADO_KMH = 3.0
TERCEIRO_LIVRE_H = 48.0        # terceiro que entregou fica em "livres" até 48 h (decisão de 09/10/26)

ATIVAS = ('Aberta', 'Em rota', 'No destino', 'Desengatada')
BRT = timedelta(hours=3)


def _dia_brt(t):
    """(início, fim) em UTC do dia de Brasília que contém `t` (UTC)."""
    d = (t - BRT).date()
    ini = datetime.combine(d, datetime.min.time()) + BRT
    return ini, ini + timedelta(days=1)


def _h(a, b):
    return (a - b).total_seconds() / 3600.0 if (a and b) else None


# ── cargas ──────────────────────────────────────────────────────────────────────────────────
def _etapa_pelas_datas(c, t):
    """O estado da carga em `t` deduzido SÓ das datas. No retrato é o estado; ao vivo é a
    outra metade do 'confere'."""
    if c['status'] == 'Cancelada':
        return 'fechada' if (c['atualizado'] and c['atualizado'] <= t) else _sem_cancelamento(c, t)
    return _sem_cancelamento(c, t)


def _sem_cancelamento(c, t):
    if c['conclusao'] and c['conclusao'] <= t:
        return 'fechada'
    if c['desengate'] and c['desengate'] <= t:
        return 'Desengatada'
    if c['chegada'] and c['chegada'] <= t:
        return 'No destino'
    if c['saida'] and c['saida'] <= t:
        return 'Em rota'
    return 'Aberta'


def _etapa_viva(c):
    """Ao vivo manda o STATUS. `Desengatada` COM conclusão já acabou para esta carga — a
    mercadoria seguiu em outra (§24); na 1ª simulação eram 14 desde 29/08 inflando o trânsito."""
    if c['status'] not in ATIVAS or (c['status'] == 'Desengatada' and c['conclusao']):
        return 'fechada'
    return c['status']


def _sql_agenda(cur):
    """Entrega agendada pelo cliente, da ORDEM DE COLETA ligada à carga (29/09/26: o embarcador
    escreve "AGENDA 02/10" na observação; `_programacao.ler_agendamento`). N ordens → 1 carga
    (a filial junta ordens num CTRC): vale a MENOR data. Sem a coluna (fita ainda não rodou
    depois do deploy), devolve NULL e a torre segue nos 600 km/dia."""
    cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name='embarques_programacao' "
                "AND column_name='agendamento'")
    if not cur.fetchone():
        return "NULL::date, NULL::varchar"
    return ("""(SELECT p.agendamento FROM embarques_programacao p WHERE p.carga_id = c.id
                  AND p.agendamento IS NOT NULL ORDER BY p.agendamento LIMIT 1),
               (SELECT p.coleta_origem FROM embarques_programacao p WHERE p.carga_id = c.id
                  AND p.agendamento IS NOT NULL ORDER BY p.agendamento LIMIT 1)""")


def _cargas(cur, t, retrato):
    ini_dia, _ = _dia_brt(t)
    ag = _sql_agenda(cur)
    cur.execute(f"""
        SELECT c.id, c.numero, c.status, c.criado_em, c.data_saida_real, c.no_local_desde,
               c.data_conclusao, c.desengatada_em, c.encerrada_motivo, c.distancia_planejada_km,
               c.carreta1_placa, c.cavalo_placa, c.embarcador, c.origem_cidade,
               c.origem_latitude, c.origem_longitude, d.cidade, d.latitude, d.longitude,
               c.atualizado_em, c.motorista_nome, c.tipo_operacao, {ag}
          FROM embarques_cargas c
          LEFT JOIN embarques_cargas_destinos d ON d.carga_id = c.id
               AND d.ordem = (SELECT MIN(ordem) FROM embarques_cargas_destinos x WHERE x.carga_id = c.id)
         WHERE NOT COALESCE(c.viagem_vazia, FALSE)
           AND c.criado_em <= %s
           AND (c.status IN %s OR c.data_conclusao IS NULL OR c.data_conclusao >= %s
                OR c.atualizado_em >= %s)
    """, (t, ATIVAS, ini_dia, ini_dia))
    cols = ('id', 'numero', 'status', 'criado', 'saida', 'chegada', 'conclusao', 'desengate',
            'motivo', 'km_plan', 'carreta', 'cavalo', 'embarcador', 'origem', 'olat', 'olng',
            'destino', 'dlat', 'dlng', 'atualizado', 'motorista', 'tipo', 'agenda', 'agenda_ordem')
    out = []
    for r in cur.fetchall():
        c = dict(zip(cols, r))
        c['etapa'] = _etapa_pelas_datas(c, t) if retrato else _etapa_viva(c)
        out.append(c)
    return out


def _horas_viagem(c):
    horas = float(c['km_plan']) / (KM_DIA / 24.0)
    return horas + (FOLGA_CURTA_H if float(c['km_plan']) < 300 else 0.0)


def _previsao(c):
    """Chegada prevista pela régua dos 600 km/dia, por hora."""
    if not (c['saida'] and c['km_plan']):
        return None
    return c['saida'] + timedelta(hours=_horas_viagem(c))


def fim_da_agenda(ag):
    """Fim do dia agendado, em UTC: a data vem sem hora ("AGENDA 02/10"), então a carga está
    no prazo até 23:59 de Brasília daquele dia."""
    return datetime.combine(ag + timedelta(days=1), datetime.min.time()) + BRT


def prazo(c):
    """(instante-limite em UTC, régua). PRIORIDADE (Gabriel, 29/09/26): a entrega agendada da
    ordem de coleta; sem ela, a conta dos 600 km/dia com 20% de folga (que exige saída)."""
    if c.get('agenda'):
        return fim_da_agenda(c['agenda']), 'agenda'
    p = _previsao(c)
    if p:
        return c['saida'] + (p - c['saida']) * FOLGA_PREVISAO, '600 km/dia'
    return None, None


def _atrasando(c, t):
    """Passou do prazo e ainda não chegou. Com agenda vale também para carga que nem saiu."""
    lim, _ = prazo(c)
    return lim is not None and t > lim


def vai_atrasar(c, t):
    """Com agenda, ainda no prazo, mas a chegada estimada (600 km/dia, sem folga) cai depois
    do fim do dia agendado. Sem saída, a estimativa parte de agora."""
    if not c.get('agenda') or not c['km_plan'] or _atrasando(c, t):
        return None
    h = timedelta(hours=_horas_viagem(c))
    eta = max(t, c['saida'] + h) if c['saida'] else t + h
    return eta if eta > fim_da_agenda(c['agenda']) else None


# ── ordens de coleta (análise do DIA) ──────────────────────────────────────────────────────
def _estado_ordem_em(o, t):
    """Espelha `_programacao.derivar_estado`, mas no instante `t`, pelos horários — ao vivo
    e no retrato é a mesma régua. `documento emitido` não tem hora no dado: conta a partir da
    coleta marcada (quando houver) ou da carga criada."""
    if o['cancelada'] and o['cancelada'] <= t:
        return 'cancelada'
    if o['carga_criada'] and o['carga_criada'] <= t:
        return 'carga'
    if o['ctrc'] and ((o['coletada'] and o['coletada'] <= t) or t >= o['ultima_vez']):
        return 'documento emitido'
    if o['comandada'] and o['comandada'] <= t:
        return 'vencida sem documento' if (o['limite'] and o['limite'] < t) else 'aguardando manifesto'
    if o['limite'] and o['limite'] < t:
        return 'vencida sem documento'
    return 'sem veículo'


def _utc(col):
    """Carimbo do 157 (hora de Brasília sem fuso) → UTC sem fuso, o relógio da torre. Até
    06/10/26 a torre lia o limite como se fosse UTC: a coleta vencia 3 h antes e o texto da
    exceção mostrava o limite 3 h adiantado (`- BRT` em cima de um valor que já era BRT)."""
    return f"(({col} AT TIME ZONE 'America/Sao_Paulo') AT TIME ZONE 'UTC')"


def _ordens_do_dia(cur, t):
    cur.execute("SELECT to_regclass('embarques_programacao') IS NOT NULL")
    if not cur.fetchone()[0]:
        return []
    ini, fim = _dia_brt(t)
    cur.execute(f"""
        SELECT p.coleta_origem, {_utc('p.limite_em')}, {_utc('p.cadastrada_em')}, {_utc('p.comandada_em')},
               {_utc('p.coletada_em')}, {_utc('p.cancelada_em')},
               p.ctrc_gerado, p.embarcador, p.cavalo, p.carreta, p.reme_cidade,
               p.dest_cidade, COALESCE(p.tipo_frota, ''), c.criado_em, c.numero, p.ultima_vez
          FROM embarques_programacao p
          LEFT JOIN embarques_cargas c ON c.id = p.carga_id
         WHERE p.primeira_vez <= %s
           AND (({_utc('p.cadastrada_em')} >= %s AND {_utc('p.cadastrada_em')} < %s)
                OR ({_utc('p.limite_em')} >= %s AND {_utc('p.limite_em')} < %s))
    """, (t, ini, fim, ini, fim))
    cols = ('coleta', 'limite', 'cadastrada', 'comandada', 'coletada', 'cancelada', 'ctrc',
            'embarcador', 'cavalo', 'carreta', 'origem', 'destino', 'tipo', 'carga_criada',
            'carga_numero', 'ultima_vez')
    out = []
    for r in cur.fetchall():
        o = dict(zip(cols, r))
        o['estado'] = _estado_ordem_em(o, t)
        out.append(o)
    return out


def _ordens_proximas(cur, t, horas=PROXIMAS_H):
    """Ordens com prazo nas próximas horas, de qualquer dia de cadastro (ao vivo)."""
    cur.execute("SELECT to_regclass('embarques_programacao') IS NOT NULL")
    if not cur.fetchone()[0]:
        return []
    cur.execute(f"""
        SELECT coleta_origem, carreta, COALESCE(tipo_frota, '')
          FROM embarques_programacao
         WHERE sumiu_em IS NULL AND estado IN ('aguardando manifesto', 'sem veículo')
           AND {_utc('limite_em')} >= %s AND {_utc('limite_em')} < %s
    """, (t, t + timedelta(hours=horas)))
    return [{'coleta': a, 'carreta': b, 'tipo': c} for a, b, c in cur.fetchall()]


# ── caixas ─────────────────────────────────────────────────────────────────────────────────
def caixas(t, cargas, ordens, retrato):
    ini_dia, fim_dia = _dia_brt(t)
    no_dia = lambda x: x is not None and ini_dia <= x < fim_dia and x <= t
    ativas = [c for c in cargas if c['etapa'] in ATIVAS]

    nossas = [o for o in ordens if o['tipo'] != 'Terceiro']
    atendidas = [o for o in nossas if o['estado'] in ('carga', 'documento emitido')]
    col = {
        'do_dia': len(nossas),
        'atendidas': len(atendidas),
        'em_aberto': sum(1 for o in nossas if o['estado'] in ('aguardando manifesto', 'sem veículo')),
        'vencidas': sum(1 for o in nossas if o['estado'] == 'vencida sem documento'),
        'canceladas': sum(1 for o in nossas if o['estado'] == 'cancelada'),
        'terceiros': len(ordens) - len(nossas),
    }
    col['estoque'] = col['em_aberto'] + col['vencidas']

    doc = [c for c in ativas if c['etapa'] == 'Aberta']
    trans = [c for c in ativas if c['etapa'] in ('Em rota', 'Desengatada')]
    rota = [c for c in trans if c['etapa'] == 'Em rota']
    dest = [c for c in ativas if c['etapa'] == 'No destino']
    out = {
        'coletas': col,
        'documento_sem_saida': {
            'estoque': len(doc),
            'hoje_entraram': sum(1 for c in cargas if no_dia(c['criado'])),
            'hoje_sairam': sum(1 for c in cargas if no_dia(c['saida'])),
            'mais_12h': sum(1 for c in doc if (_h(t, c['criado']) or 0) >= LIM_DOC_SEM_SAIDA_H),
            # nem saiu e a entrega agendada já passou / já não cabe na viagem
            'agenda_vencida': sum(1 for c in doc if c.get('agenda') and _atrasando(c, t)),
            'vai_atrasar': sum(1 for c in doc if vai_atrasar(c, t)),
        },
        'em_transito': {
            'estoque': len(trans),
            'hoje_entraram': sum(1 for c in cargas if no_dia(c['saida'])),
            'hoje_chegaram': sum(1 for c in cargas if no_dia(c['chegada'])),
            'atrasando': sum(1 for c in rota if _atrasando(c, t)),
            'atrasando_agenda': sum(1 for c in rota if c.get('agenda') and _atrasando(c, t)),
            'vai_atrasar': sum(1 for c in rota if vai_atrasar(c, t)),
            'com_agenda': sum(1 for c in rota if c.get('agenda')),
            'desengatadas': sum(1 for c in trans if c['etapa'] == 'Desengatada'),
        },
        'no_destino': {
            'estoque': len(dest),
            'hoje_entraram': sum(1 for c in cargas if no_dia(c['chegada'])),
            'mais_24h': sum(1 for c in dest if (_h(t, c['chegada']) or 0) >= LIM_NO_DESTINO_H),
        },
        'entregues_no_dia': {
            'estoque': sum(1 for c in cargas if c['status'] == 'Entregue' and no_dia(c['conclusao'])),
        },
    }
    if not retrato:
        # CONFERE: status e datas têm de contar a mesma história.
        div = [c['numero'] for c in ativas if _etapa_pelas_datas(c, t) != c['etapa']]
        out['confere'] = {'ok': not div, 'abertas': len(ativas), 'n_divergentes': len(div),
                          'divergentes': div[:20]}
    return out


# ── frota ───────────────────────────────────────────────────────────────────────────────────
def _base_patio(cur):
    cur.execute("""SELECT latitude, longitude FROM municipios_ibge
                    WHERE cidade_normalizada IN ('UBERLANDIA', 'uberlandia') AND uf = 'MG' LIMIT 1""")
    r = cur.fetchone()
    return (float(r[0]), float(r[1])) if r else (-18.87572, -48.29714)


def _ultima_posicao(cur, placa, t, janela_h=168, camada=False):
    # `camada=True` lê pela `fontes_gps` (3S + Insignia), a mesma do worker e do motor — só a
    # carga de Terceiro usa, porque o cavalo dela só existe na Insignia (09/10/26). A frota segue
    # na tabela da 3S: com o escopo `terceiro` a Insignia não acrescenta nada a carreta nossa, e a
    # união varre a `insignia_posicoes` inteira a cada consulta (a frota faz ~140 por atualização;
    # medido 0,3 s → 0,8 s com 3 dias de coleta, e cresce com a tabela). Quando o escopo da
    # Insignia virar `todas`, a frota também tem de ler pela camada.
    if not placa:
        return None
    fonte = f'{fontes_gps.historico(cur)} h' if camada else 'embarques_posicoes_historico'
    cur.execute(f"""SELECT data_posicao, latitude, longitude, velocidade, cidade, uf
                     FROM {fonte}
                    WHERE placa = ANY(%s) AND data_posicao BETWEEN %s AND %s
                    ORDER BY data_posicao DESC LIMIT 1""",
                (pl.grafias(placa), t - timedelta(hours=janela_h), t))
    r = cur.fetchone()
    # cidade/uf = etiqueta da 3S: serve para MOSTRAR onde está, nunca para decidir (ela já
    # etiquetou cidade a 100+ km — as réguas usam coordenada)
    return (r[0], float(r[1]), float(r[2]), float(r[3] or 0), r[4], r[5]) if r and r[1] is not None else None


def _doc(s):
    """Nº de documento sem espaço e pontuação — a mesma régua de `embarques_auto._norm`, que é
    como a fita grava a chave do manifesto ('UDI029513-2' na carga = 'UDI0295132' na fita)."""
    return re.sub(r'[^A-Za-z0-9]', '', str(s or '')).upper()


def _terceiros_livres(cur, t):
    """TERCEIRO QUE ACABOU DE ENTREGAR (decisão de 09/10/26). Não é carreta nossa, mas pode ser
    aproveitado numa volta: entra em "livres para carga" quando a carga dele vira Entregue — o
    mesmo instante da frota — e fica até TERCEIRO_LIVRE_H horas. Sai antes se aparecer documento
    novo com a placa dele (cavalo ou carreta):
      * carga nova (qualquer tipo, não cancelada);
      * ordem de coleta viva no instante — a que VENCE sem documento devolve ele à lista enquanto
        as 48 h não passaram (C-2026-001406: ordem RIO-000066 cadastrada 3 h depois da entrega,
        vencida às 08:00 do dia seguinte);
      * manifesto novo, pela fita — metade das voltas de terceiro de 12/09 a 07/10 não teve ordem.
    "Novo" = nasceu depois da carga entregue (a ordem que a originou e o manifesto dela vêm
    antes). Caso que motivou: C-2026-001405 (DPB4G53) entregou no Rio em 05/10 15:08 e pegou a
    volta RIO0033006 45 h depois. Posição pelo cavalo (`_gps_terceiro`); sem posição atual, vale
    a cidade da descarga — depois de ~24 h a Insignia costuma não ter mais ponto do cavalo."""
    cur.execute("""
        SELECT c.id, c.numero, c.carreta1_placa, c.cavalo_placa, c.motorista_nome, c.criado_em,
               c.data_conclusao, c.manifesto_origem, c.origem_cidade, d.cidade, d.uf
          FROM embarques_cargas c
          LEFT JOIN embarques_cargas_destinos d ON d.carga_id = c.id
               AND d.ordem = (SELECT MIN(ordem) FROM embarques_cargas_destinos x WHERE x.carga_id = c.id)
         WHERE c.tipo_operacao = 'Terceiro' AND c.status = 'Entregue'
           AND NOT COALESCE(c.viagem_vazia, FALSE)
           AND c.data_conclusao > %s AND c.data_conclusao <= %s
         ORDER BY c.data_conclusao DESC""", (t - timedelta(hours=TERCEIRO_LIVRE_H), t))
    cols = ('id', 'numero', 'carreta', 'cavalo', 'motorista', 'criado', 'conclusao', 'manifesto',
            'origem', 'destino', 'uf')
    cands, vistos = [], set()
    for r in cur.fetchall():                    # a entrega mais recente de cada terceiro
        c = dict(zip(cols, r))
        k = pl.mercosul(c['cavalo'] or c['carreta'])
        if k and k not in vistos:
            vistos.add(k)
            c['grafias'] = sorted({g for p in (c['carreta'], c['cavalo']) if p for g in pl.grafias(p)})
            cands.append(c)
    if not cands:
        return []

    cur.execute("SELECT to_regclass('embarques_programacao') IS NOT NULL, to_regclass('fita_documentos') IS NOT NULL")
    tem_ordens, tem_fita = cur.fetchone()
    manifestos = []
    if tem_fita:
        # uma consulta para todos: as rodadas desde a carga mais antiga (PK começa por `rodada`)
        todas = sorted({g for c in cands for g in c['grafias']})
        cur.execute("""SELECT chave, rodada, replace(payload->>'placa_cavalo', '-', ''),
                              replace(payload->>'placa_carreta', '-', '')
                         FROM fita_documentos
                        WHERE rodada BETWEEN %s AND %s AND fonte = 'manifesto'
                          AND (replace(payload->>'placa_cavalo', '-', '') = ANY(%s)
                               OR replace(payload->>'placa_carreta', '-', '') = ANY(%s))""",
                    (min(c['criado'] for c in cands) - timedelta(days=1), t, todas, todas))
        manifestos = cur.fetchall()

    out = []
    for c in cands:
        g = c['grafias']
        cur.execute("""SELECT 1 FROM embarques_cargas
                        WHERE id <> %s AND status <> 'Cancelada' AND criado_em > %s AND criado_em <= %s
                          AND (cavalo_placa = ANY(%s) OR carreta1_placa = ANY(%s)) LIMIT 1""",
                    (c['id'], c['criado'], t, g, g))
        if cur.fetchone():
            continue
        if tem_ordens:
            cur.execute(f"""
                SELECT {_utc('p.limite_em')}, {_utc('p.comandada_em')}, {_utc('p.coletada_em')},
                       {_utc('p.cancelada_em')}, p.ctrc_gerado, p.ultima_vez, cc.criado_em
                  FROM embarques_programacao p
                  LEFT JOIN embarques_cargas cc ON cc.id = p.carga_id
                 WHERE (p.cavalo = ANY(%s) OR p.carreta = ANY(%s))
                   AND COALESCE(p.carga_id, -1) <> %s
                   AND {_utc('p.cadastrada_em')} > %s AND p.primeira_vez <= %s""",
                        (g, g, c['id'], c['criado'], t))
            ordens = [dict(zip(('limite', 'comandada', 'coletada', 'cancelada', 'ctrc', 'ultima_vez',
                                'carga_criada'), r)) for r in cur.fetchall()]
            if any(_estado_ordem_em(o, t) not in ('vencida sem documento', 'cancelada') for o in ordens):
                continue
        primeira = {}                           # 1ª vez que cada manifesto dele apareceu na fita
        for chave, rodada, cav, car in manifestos:
            if (cav in g or car in g) and _doc(chave) != _doc(c['manifesto']):
                primeira[chave] = min(rodada, primeira.get(chave, rodada))
        if any(c['criado'] < r <= t for r in primeira.values()):
            continue
        gps = _gps_terceiro(cur, c, t)
        atual = gps['estado'] != 'sem sinal'
        cidade = None
        if atual:
            m = geocoding.cidade_por_coord(gps['lat'], gps['lng'], cur.connection)
            cidade = f'{m[0]}/{m[1]}' if m else None
        descarga = f"{c['destino']}/{c['uf']}" if c['destino'] else None
        out.append({'carreta': c['carreta'] or c['cavalo'], 'chave': pl.mercosul(c['carreta'] or c['cavalo']),
                    'grupo': 'terceiro', 'estado': 'livre', 'carga': c['numero'], 'carga_id': c['id'],
                    'cavalo': c['cavalo'], 'motorista': c['motorista'],
                    'rota': f"{c['origem'] or '?'} → {descarga or '?'}",
                    'posicao_idade_h': gps['posicao_idade_h'],
                    'via': 'cavalo' if gps['placa_gps'] == c['cavalo'] else 'carreta',
                    'lat': gps['lat'], 'lng': gps['lng'], 'cidade': cidade or descarga,
                    'no_patio': False, 'balde': 'livres', 'posicao_atual': atual,
                    'descarregou_em': descarga, 'entregue_em': c['conclusao'].isoformat(),
                    'livre_ate': (c['conclusao'] + timedelta(hours=TERCEIRO_LIVRE_H)).isoformat()})
    return out


def frota(cur, t, cargas, ordens, dias=30):
    """Estado de cada carreta que teve carga (Frota/Agregado) nos últimos `dias`.

    O filtro de tipo era implícito — até 07/10/26 o robô só abria Frota/Agregado. Quando passou a
    abrir Terceiro, a carreta do terceiro entrou aqui como se fosse nossa: em viagem, "sem sinal"
    (a 3S não a vê; C-2026-001407 e C-001417 no dump de 08/10, com a Insignia tendo posição do
    cavalo de ~50 min antes); depois da entrega, 30 dias contando como carreta "livre"."""
    cur.execute("""SELECT carreta1_placa, MAX(criado_em) FROM embarques_cargas
                    WHERE carreta1_placa IS NOT NULL AND carreta1_placa <> ''
                      AND tipo_operacao IN ('Frota', 'Agregado')
                      AND NOT COALESCE(viagem_vazia, FALSE) AND criado_em BETWEEN %s AND %s
                    GROUP BY 1 ORDER BY 2 DESC""",
                (t - timedelta(days=dias), t))
    cruas = [r[0] for r in cur.fetchall()]
    # chave Mercosul junta as duas grafias; o que se MOSTRA é a placa da carga (a do SSW) —
    # a chave convertida é uma placa que não existe (GZQ3A80 para a GZQ3080, 03/10/2026)
    rot = pl.rotulos(cruas)
    carretas = sorted({pl.mercosul(c) for c in cruas if pl.mercosul(c)})
    base = _base_patio(cur)
    ativa_por = {pl.mercosul(c['carreta']): c for c in cargas if c['etapa'] in ATIVAS and c['carreta']}
    coleta_por = {pl.mercosul(o['carreta']): o for o in ordens
                  if o['estado'] == 'aguardando manifesto' and o['carreta']}
    linhas, cont = [], Counter()
    for p in carretas:
        c = ativa_por.get(p)
        pos = _ultima_posicao(cur, p, t)
        via = 'carreta'
        if (pos is None or _h(t, pos[0]) > GPS_ATUAL_H) and c and c['cavalo']:
            pc = _ultima_posicao(cur, c['cavalo'], t, janela_h=GPS_ATUAL_H)
            if pc:
                pos, via = pc, 'cavalo'
        idade = _h(t, pos[0]) if pos else None
        atual = idade is not None and idade <= GPS_ATUAL_H
        anda = atual and pos[3] > PARADO_KMH
        no_patio = atual and geocoding.km_entre(pos[1], pos[2], *base) <= R_PATIO_KM
        if c:
            grupo = 'carregada'
            if c['etapa'] == 'Desengatada':
                est = 'desengatada'
            elif c['etapa'] == 'No destino':
                est = 'no cliente'
            elif c['etapa'] == 'Aberta':
                est = 'aguardando saída'
            elif not atual:
                est = 'sem sinal'
            elif anda:
                est = 'rodando'
            else:
                est = 'parada no pátio' if no_patio else 'parada fora'
        else:
            grupo = 'vazia'
            if p in coleta_por:
                est = 'a caminho da coleta'
            elif not atual:
                est = 'sem sinal'
            elif anda:
                est = 'rodando'
            else:
                est = 'pátio' if no_patio else 'parada fora'
        balde = _balde(cur, grupo, est, p, t, c)
        cont[(grupo, est)] += 1
        linhas.append({'carreta': rot.get(p, p), 'chave': p, 'grupo': grupo, 'estado': est, 'carga': c['numero'] if c else None,
                       'carga_id': c['id'] if c else None, 'cavalo': c['cavalo'] if c else None,
                       'motorista': c['motorista'] if c else None,
                       'rota': f"{c['origem'] or '?'} → {c['destino'] or '?'}" if c else None,
                       'posicao_idade_h': round(idade, 1) if idade is not None else None, 'via': via,
                       'lat': pos[1] if pos else None, 'lng': pos[2] if pos else None,
                       'cidade': (f'{pos[4]}/{pos[5]}' if pos and pos[4] else None) if atual else None,
                       'no_patio': bool(no_patio), 'balde': balde})
    # Terceiro recém-entregue entra no balde "livres" com contagem própria (grupo 'terceiro'):
    # `total` e `por_estado` seguem sendo a NOSSA frota (Frota + Agregado).
    terceiros = _terceiros_livres(cur, t)
    todas = linhas + terceiros
    baldes = {b: Counter() for b in BALDES}
    for l in todas:
        baldes[l['balde']][l['grupo'] + ':' + l['estado']] += 1
    livres_cidade = Counter('Uberlândia (pátio)' if l['no_patio'] else (l['cidade'] or 'sem cidade')
                            for l in todas if l['balde'] == 'livres')
    return {'total': len(carretas), 'terceiros_livres': len(terceiros),
            'por_estado': [{'grupo': g, 'estado': e, 'n': n} for (g, e), n in sorted(cont.items())],
            'baldes': [{'id': b, 'n': sum(baldes[b].values()),
                        'detalhe': [{'estado': k, 'n': v} for k, v in baldes[b].most_common()]} for b in BALDES],
            'livres_por_cidade': [{'cidade': k, 'n': v} for k, v in livres_cidade.most_common()],
            'carretas': todas, '_rotulos': rot}


# OS QUATRO BALDES DA FROTA (25/09, pedido do Gabriel): doze estados técnicos viraram as três
# perguntas de quem programa carga — quem está trabalhando, quem está livre para carregar, quem
# precisa que eu olhe — mais a falta de dado, separada para não virar "problema da viagem".
BALDES = ('trabalhando', 'livres', 'olhar', 'sem_info')


def _balde(cur, grupo, est, placa, t, c):
    if est == 'sem sinal':
        return 'sem_info'
    if grupo == 'vazia':
        return 'trabalhando' if est == 'a caminho da coleta' else 'livres'
    if est == 'desengatada':
        return 'olhar'
    if est == 'parada fora':
        # parada curta (almoço, posto) é viagem normal; só vira "olhar" com a MESMA régua da
        # exceção "parada na estrada" — 2 h de dia, 11 h à noite
        hora_brt = (t - BRT).hour
        limite = LIM_PARADA_NOITE_H if (hora_brt >= 22 or hora_brt < 6) else LIM_PARADA_ESTRADA_H
        return 'olhar' if _tempo_parado(cur, placa, t) >= limite else 'trabalhando'
    return 'trabalhando'


# ── exceções ────────────────────────────────────────────────────────────────────────────────
def _tempo_parado(cur, placa, t, janela_h=24, camada=False):
    """Há quantas horas a placa está parada (último ponto em movimento → t).
    `camada`: o mesmo critério de `_ultima_posicao` — só a carga de Terceiro lê pela `fontes_gps`."""
    fonte = f'{fontes_gps.historico(cur)} h' if camada else 'embarques_posicoes_historico'
    cur.execute(f"""SELECT MAX(data_posicao) FROM {fonte}
                    WHERE placa = ANY(%s) AND data_posicao BETWEEN %s AND %s AND velocidade > %s""",
                (pl.grafias(placa), t - timedelta(hours=janela_h), t, PARADO_KMH))
    r = cur.fetchone()[0]
    return _h(t, r) if r else float(janela_h)


def _gps_terceiro(cur, c, t):
    """Posição da carga de Terceiro em viagem, no formato da linha da frota que as exceções leem.

    Terceiro não entra na frota (a carreta não é nossa), mas a viagem segue precisando de GPS para
    "Parada na estrada" e "Rastreador sem sinal". Vale a posição mais recente entre carreta e
    cavalo — na prática o cavalo, o único que a GR rastreia (HANDOFF-INSIGNIA §16.4). "Sem sinal"
    só quando nenhuma fonte tem posição nas últimas GPS_ATUAL_H horas. `placa_gps` diz de qual
    placa veio, para a parada ser medida no mesmo aparelho."""
    pos, placa = None, None
    for p in (c['carreta'], c['cavalo']):
        q = _ultima_posicao(cur, p, t, camada=True) if p else None
        if q and (pos is None or q[0] > pos[0]):
            pos, placa = q, p
    idade = _h(t, pos[0]) if pos else None
    if idade is None or idade > GPS_ATUAL_H:
        return {'estado': 'sem sinal', 'placa_gps': placa or c['cavalo'] or c['carreta'],
                'lat': None, 'lng': None, 'posicao_idade_h': round(idade, 1) if idade is not None else None}
    return {'estado': 'rodando' if pos[3] > PARADO_KMH else 'parada fora', 'placa_gps': placa,
            'lat': pos[1], 'lng': pos[2], 'posicao_idade_h': round(idade, 1)}


def _exc_prazo(add, c, t, ref, rota):
    """Atraso pela entrega agendada da ordem de coleta; sem agenda, pelos 600 km/dia."""
    lim, regua = prazo(c)
    nem_saiu = '' if c['saida'] else ' — e ainda nem saiu'
    if lim and t > lim:
        if regua == 'agenda':
            add('alta', 'Entrega agendada vencida', ref, rota,
                f"entrega agendada para {c['agenda']:%d/%m} (ordem {c['agenda_ordem']}) e a carga não chegou{nem_saiu}",
                _h(t, lim), c['embarcador'])
        else:
            add('media', 'Atrasando', ref, rota,
                f"previsão {_previsao(c) - BRT:%d/%m %H:%M} (600 km/dia, sem entrega agendada) + 20% de folga já passou",
                _h(t, lim), c['embarcador'])
        return
    eta = vai_atrasar(c, t)
    if eta:
        add('media', 'Vai atrasar', ref, rota,
            f"chegada estimada {eta - BRT:%d/%m %H:%M} (600 km/dia) passa da entrega agendada para "
            f"{c['agenda']:%d/%m} (ordem {c['agenda_ordem']}){nem_saiu}",
            None, c['embarcador'])


def excecoes(cur, t, cargas, ordens, frota_):
    ex = []

    alvo = {}

    def add(grav, tipo, ref, texto, por_que, idade_h, embarcador=None, sensor=False):
        ex.append({'gravidade': grav, 'tipo': tipo, 'ref': ref, 'texto': texto, 'por_que': por_que,
                   'idade_h': round(idade_h, 1) if idade_h is not None else None,
                   'embarcador': embarcador, 'sensor': sensor, 'carga_id': alvo.get('id'),
                   'etapa': alvo.get('etapa')})

    for o in ordens:
        alvo.clear(); alvo.update(etapa='coleta')
        if o['tipo'] != 'Terceiro' and o['estado'] == 'vencida sem documento':
            add('alta', 'Coleta vencida sem documento', o['coleta'],
                f"{o['origem'] or '?'} → {o['destino'] or '?'}",
                f"limite {o['limite'] - BRT:%d/%m %H:%M} passou e nenhum documento foi emitido",
                _h(t, o['limite']), o['embarcador'])

    # só a nossa frota: a linha de terceiro livre aponta para a carga JÁ ENTREGUE
    pos_frota = {f['carga']: f for f in frota_['carretas'] if f['carga'] and f['grupo'] != 'terceiro'}
    for c in cargas:
        if c['etapa'] not in ATIVAS:
            continue
        ref, rota = c['numero'], f"{c['origem'] or '?'} → {c['destino'] or '?'}"
        alvo.clear(); alvo.update(id=c['id'], etapa=c['etapa'])
        if c['etapa'] == 'Aberta':
            h = _h(t, c['criado'])
            if h is not None and h >= LIM_DOC_SEM_SAIDA_H:
                add('media', 'Documento sem saída', ref, rota,
                    f"carga existe há {h:.0f} h e o GPS não mostrou a saída", h, c['embarcador'])
            _exc_prazo(add, c, t, ref, rota)
        elif c['etapa'] == 'Em rota':
            _exc_prazo(add, c, t, ref, rota)
            f = pos_frota.get(ref)
            if f is None and c['tipo'] == 'Terceiro':
                f = _gps_terceiro(cur, c, t)
            if f and f['estado'] == 'parada fora' and f['lat'] is not None:
                parada_h = _tempo_parado(cur, f.get('placa_gps') or c['carreta'], t, camada='placa_gps' in f)
                d_dst = geocoding.km_entre(f['lat'], f['lng'], float(c['dlat']), float(c['dlng'])) if c['dlat'] else None
                d_org = geocoding.km_entre(f['lat'], f['lng'], float(c['olat']), float(c['olng'])) if c['olat'] else None
                hora_brt = (t - BRT).hour
                limite = LIM_PARADA_NOITE_H if (hora_brt >= 22 or hora_brt < 6) else LIM_PARADA_ESTRADA_H
                if parada_h >= limite and (d_dst or 0) > 30 and (d_org or 0) > 30:
                    add('alta', 'Parada na estrada', ref, rota,
                        f"parada há {parada_h:.1f} h a {d_dst:.0f} km do destino, longe da origem",
                        parada_h, c['embarcador'])
            if f and f['estado'] == 'sem sinal':
                add('sensor', 'Rastreador sem sinal', f.get('placa_gps') or c['carreta'], f'{ref} em viagem',
                    f"última posição há {f['posicao_idade_h'] or '—'} h — a viagem segue pelo documento",
                    f['posicao_idade_h'], sensor=True)
        elif c['etapa'] == 'No destino':
            h = _h(t, c['chegada'])
            if h is not None and h >= LIM_NO_DESTINO_H:
                add('media', 'Esperando no cliente', ref, rota,
                    f"{h:.0f} h no destino sem conclusão", h, c['embarcador'])
        elif c['etapa'] == 'Desengatada':
            h = _h(t, c['desengate'] or c['atualizado'])
            if h is not None and h >= LIM_DESENGATADA_D * 24:
                add('media', 'Desengatada', ref, rota, f"sem cavalo há {h / 24:.1f} dias", h, c['embarcador'])
    ordem = {'alta': 0, 'media': 1, 'sensor': 2}
    ex.sort(key=lambda e: (ordem[e['gravidade']], -(e['idade_h'] or 0)))
    return ex


# ── relógios e próximas horas (só ao vivo) ──────────────────────────────────────────────────
def relogios(cur, t):
    cur.execute("SELECT MAX(data_posicao) FROM embarques_posicoes_historico WHERE data_posicao <= %s", (t,))
    gps = cur.fetchone()[0]
    doc = None
    cur.execute("SELECT to_regclass('embarques_programacao') IS NOT NULL")
    if cur.fetchone()[0]:
        cur.execute("SELECT MAX(ultima_vez) FROM embarques_programacao")
        doc = cur.fetchone()[0]
    cur.execute("""SELECT MAX(editado_em) FROM embarques_cargas_log
                    WHERE usuario_nome = 'Robo atemporal' AND editado_em <= %s""", (t,))
    motor = cur.fetchone()[0]

    def _r(x, verde_h, amarelo_h):
        h = _h(t, x)
        cor = 'cinza' if h is None else 'verde' if h <= verde_h else 'amarelo' if h <= amarelo_h else 'vermelho'
        return {'em': x.isoformat() if x else None, 'idade_h': round(h, 2) if h is not None else None, 'cor': cor}
    return {'gps': _r(gps, 0.25, 2), 'documentos': _r(doc, 3, 8), 'motor': _r(motor, 3, 8)}


def proximas(cur, t, frota_):
    """PRÓXIMAS 12 h, não 'amanhã': a ordem é cadastrada no próprio dia, com prazo poucas horas
    à frente (1 a 7 h na base de 01-14/09) — 'amanhã' dava sempre 0 na simulação."""
    prev = [o for o in _ordens_proximas(cur, t) if o['tipo'] != 'Terceiro']
    livres = sum(1 for f in frota_['carretas'] if f['grupo'] == 'vazia' and f['estado'] != 'a caminho da coleta')
    return {'horas': PROXIMAS_H, 'coletas_previstas': len(prev),
            'coletas_sem_carreta': sum(1 for o in prev if not o['carreta']), 'carretas_livres': livres}


# ── linha do tempo da frota (36 h, em blocos de 1 h) ───────────────────────────────────────
def linha_do_tempo(cur, t, carretas, horas=36, rotulos=None):
    """Por carreta, um estado por hora: carregada (pelas DATAS das cargas) × movimento (GPS).
    Mesma separação do "agora": documento diz carregada/vazia, GPS diz rodando/parada."""
    ini = t - timedelta(hours=horas)
    placas_ = [p for p in carretas]
    if not placas_:
        return {'horas': horas, 'inicio': ini.isoformat(), 'linhas': []}
    grafias = sorted({g for p in placas_ for g in pl.grafias(p)})
    cur.execute("""SELECT placa, date_trunc('hour', data_posicao) h, MAX(velocidade), COUNT(*)
                     FROM embarques_posicoes_historico
                    WHERE placa = ANY(%s) AND data_posicao >= %s AND data_posicao <= %s
                    GROUP BY 1, 2""", (grafias, ini, t))
    mov = {}
    for placa, h, vmax, n in cur.fetchall():
        mov[(pl.mercosul(placa), h)] = float(vmax or 0)
    cur.execute("""SELECT carreta1_placa, data_saida_real, criado_em, no_local_desde, data_conclusao, numero
                     FROM embarques_cargas
                    WHERE carreta1_placa = ANY(%s) AND NOT COALESCE(viagem_vazia, FALSE)
                      AND status <> 'Cancelada' AND criado_em <= %s
                      AND (data_conclusao IS NULL OR data_conclusao >= %s)""", (grafias, t, ini))
    janelas = {}
    for car, saida, criado, cheg, conc, num in cur.fetchall():
        janelas.setdefault(pl.mercosul(car), []).append((saida or criado, cheg, conc or t, num))
    base_h = ini.replace(minute=0, second=0, microsecond=0)
    linhas = []
    for p in placas_:
        blocos, ultimo_sinal = [], None
        for i in range(horas + 1):
            h = base_h + timedelta(hours=i)
            v = mov.get((p, h))
            if v is not None:
                ultimo_sinal = h
            dentro = next((j for j in janelas.get(p, []) if j[0] <= h + timedelta(minutes=59) and h <= j[2]), None)
            sem_sinal = v is None and (ultimo_sinal is None or (h - ultimo_sinal).total_seconds() > GPS_ATUAL_H * 3600)
            if dentro and dentro[1] and dentro[1] <= h:
                b = 'cliente'
            elif dentro:
                # 'xc' = sem sinal COM carga: desenha igual ao 'x', mas não conta como hora sem
                # carga (a 1ª versão chamava de "sem carga 37 h" carreta carregada e muda)
                b = 'xc' if sem_sinal else ('carregada' if (v or 0) > PARADO_KMH else 'carregada_parada')
            else:
                b = 'x' if sem_sinal else ('vazia' if (v or 0) > PARADO_KMH else 'parada')
            blocos.append(b)
        # ociosas primeiro: horas seguidas sem carga no fim da linha
        ocio = 0
        for b in reversed(blocos):
            if b in ('parada', 'vazia', 'x'):
                ocio += 1
            else:
                break
        linhas.append({'carreta': (rotulos or {}).get(p, p), 'blocos': blocos, 'ocioso_h': ocio})
    linhas.sort(key=lambda l: -l['ocioso_h'])
    return {'horas': horas, 'inicio': base_h.isoformat(), 'linhas': linhas}


# ── produtividade do dia ───────────────────────────────────────────────────────────────────
def pontualidade(t, cargas):
    """Chegadas do dia × entrega agendada da ordem de coleta: chegou até o fim do dia agendado?"""
    ini, fim = _dia_brt(t)
    chegaram = [c for c in cargas if c['chegada'] and ini <= c['chegada'] < fim and c['chegada'] <= t]
    com = [c for c in chegaram if c.get('agenda')]
    atras = [c for c in com if c['chegada'] > fim_da_agenda(c['agenda'])]
    return {'chegaram': len(chegaram), 'com_agenda': len(com), 'no_prazo': len(com) - len(atras),
            'atrasadas': len(atras),
            'lista_atrasadas': [{'carga': c['numero'], 'carga_id': c['id'], 'agenda': c['agenda'].isoformat(),
                                 'chegada': c['chegada'].isoformat(), 'ordem': c['agenda_ordem']} for c in atras]}


def produtividade(cur, t, ordens, frota_):
    """O que já tem lastro: coletas por embarcador, entregas e km ROTEIRIZADO (100% das viagens).
    Km RASTREADO e margem do agregado entram pela mesma função da tela / pelo BI — não por
    `embarques_cargas_rastreio_kpi` (gate de 25/09)."""
    ini, fim = _dia_brt(t)
    emb = {}
    for o in ordens:
        if o['tipo'] == 'Terceiro':
            continue
        e = (o['embarcador'] or '—').lower()
        d = emb.setdefault(e, {'coletas': 0, 'atendidas': 0, 'vencidas': 0, 'ordens': []})
        d['coletas'] += 1
        d['ordens'].append({'coleta': o['coleta'], 'origem': o['origem'], 'destino': o['destino'],
                            'limite': o['limite'].isoformat() if o['limite'] else None,
                            'estado': o['estado'], 'carga': o['carga_numero'],
                            'cavalo': o['cavalo'], 'carreta': o['carreta']})
        d['atendidas'] += o['estado'] in ('carga', 'documento emitido')
        d['vencidas'] += o['estado'] == 'vencida sem documento'
    cur.execute("""SELECT COALESCE(viagem_vazia, FALSE), COUNT(*), COALESCE(SUM(distancia_planejada_km), 0)
                     FROM embarques_cargas
                    WHERE status = 'Entregue' AND data_conclusao >= %s AND data_conclusao < %s
                      AND data_conclusao <= %s
                    GROUP BY 1""", (ini, fim, t))
    km = {'carregado': 0.0, 'vazio': 0.0, 'viagens': 0, 'pernas_vazias': 0}
    for vz, n, s in cur.fetchall():
        if vz:
            km['vazio'], km['pernas_vazias'] = float(s), n
        else:
            km['carregado'], km['viagens'] = float(s), n
    nossas = [f for f in frota_['carretas'] if f['grupo'] != 'terceiro']   # % da NOSSA frota
    tot = len(nossas) or 1
    tempo = Counter(f['grupo'] + ':' + f['estado'] for f in nossas)
    return {'embarcadores': [{'nome': k, **v} for k, v in sorted(emb.items())],
            'km_roteirizado_entregues': km,
            'frota_agora_pct': {k: round(100 * v / tot) for k, v in tempo.items()}}


# ── a consulta única ───────────────────────────────────────────────────────────────────────
def montar(cur, dia=None, agora=None):
    """`dia` (date, de Brasília): None ou hoje = AO VIVO; anterior = RETRATO daquele dia,
    no instante da meia-noite. `agora` (UTC) só para teste — simula o "ao vivo" no passado."""
    agora = agora or datetime.utcnow()
    hoje = (agora - BRT).date()
    retrato = dia is not None and dia < hoje
    t = (datetime.combine(dia + timedelta(days=1), datetime.min.time()) + BRT - timedelta(seconds=1)
         if retrato else agora)
    cargas = _cargas(cur, t, retrato)
    ordens = _ordens_do_dia(cur, t)
    fr = frota(cur, t, cargas, ordens)
    out = {
        'modo': 'retrato' if retrato else 'ao_vivo',
        'dia': ((t - BRT).date()).isoformat(),
        'instante': t.isoformat(),
        'caixas': caixas(t, cargas, ordens, retrato),
        'frota': fr,
        'excecoes': excecoes(cur, t, cargas, ordens, fr),
        'linha_do_tempo': linha_do_tempo(cur, t, [f['chave'] for f in fr['carretas'] if f['grupo'] != 'terceiro'],
                                         rotulos=fr.pop('_rotulos')),
        'produtividade': {**produtividade(cur, t, ordens, fr), 'pontualidade': pontualidade(t, cargas)},
    }
    if not retrato:
        out['relogios'] = relogios(cur, t)
        out['proximas'] = proximas(cur, t, fr)
    return out
