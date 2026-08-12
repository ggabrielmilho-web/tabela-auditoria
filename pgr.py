# -*- coding: utf-8 -*-
"""PGR — apuração de excesso de velocidade.

Motor puro (agrupamento de episódios) + persistência em `pgr_eventos` /
`pgr_cobertura`. O relatório e o envio leem DA TABELA, nunca recalculam: é o
que garante que a mensagem do WhatsApp e a página mostrem o mesmo número.

Ordem obrigatória do job diário:

    1. backfill do dia anterior   (completa as posições)
    2. apurar_dia()               (lê posições já completas)
    3. envio                      (lê pgr_eventos)

Apurar antes do backfill entrega os ~81% do polling ao vivo em vez dos 100% do
histórico — e ninguém percebe, o número só vem menor.

Fusos: `embarques_posicoes_historico.data_posicao` está em **UTC**; o dia do
relatório é **de Brasília** (UTC−3). As colunas `ini`/`fim` de `pgr_eventos`
são gravadas em BRT, que é como o relatório é lido.
"""

import os
import logging
from datetime import datetime, timedelta

import geocoding

_logger = logging.getLogger(__name__)

# ── Regra de detecção (HANDOFF-PGR §7) ───────────────────────────────
LIMIAR_KMH = int(os.getenv('PGR_LIMIAR', '95'))
TETO_KMH = int(os.getenv('PGR_TETO', '130'))          # anti-ruído: equipamento travado
GAP_EPISODIO_MIN = int(os.getenv('PGR_GAP_EPISODIO', '10'))
MIN_REG_SUSTENTADO = int(os.getenv('PGR_MIN_REGISTROS_SUSTENTADO', '2'))

# Velocidade sustentada: só entra segmento em movimento e sem lacuna longa,
# senão contamina (§7 achou "sustentada de 120" com pico 100).
VEL_PARADO_KMH = 3
GAP_MAX_SUSTENTADA_MIN = 30

# Cobertura: acima desta velocidade implícita (deslocamento ÷ duração) o veículo
# estava rodando durante a lacuna — aí sim é sinal perdido, não pátio.
VEL_IMPLICITA_MOVIMENTO_KMH = 5
GAP_COBERTURA_RUIM_MIN = int(os.getenv('PGR_GAP_COBERTURA_RUIM', '60'))

BRT_OFFSET_H = 3


def _utc_para_brt(dt):
    return dt - timedelta(hours=BRT_OFFSET_H)


def janela_utc_do_dia(dia):
    """Dia de Brasília → [inicio, fim) em UTC, que é como o banco guarda."""
    ini = datetime(dia.year, dia.month, dia.day) + timedelta(hours=BRT_OFFSET_H)
    return ini, ini + timedelta(days=1)


# ── Motor puro ───────────────────────────────────────────────────────

def agrupar_episodios(pontos):
    """Agrupa leituras acima do limiar em episódios.

    `pontos`: lista de dicts ordenada por data, com data/velocidade/lat/lng/
    cidade/uf/endereco. Cada leitura é um INSTANTE, não um intervalo — por isso
    a métrica do relatório é "nº de registros" (recorrência), nunca "tempo
    acima de 95", que esta fonte não permite calcular (§6).

    Devolve lista de episódios; cada um é a lista dos seus pontos acima.
    """
    acima = [p for p in pontos
             if p.get('velocidade') is not None and LIMIAR_KMH < p['velocidade'] <= TETO_KMH]
    episodios, atual = [], None
    for p in acima:
        if atual and (p['data'] - atual[-1]['data']).total_seconds() <= GAP_EPISODIO_MIN * 60:
            atual.append(p)
        else:
            if atual:
                episodios.append(atual)
            atual = [p]
    if atual:
        episodios.append(atual)
    return episodios


def velocidade_sustentada(pontos, ini, fim):
    """Média do trecho por deslocamento ÷ tempo, no intervalo do episódio.

    INFORMATIVA, não é critério de classificação: usa haversine, então
    **subestima 5–15%** (a estrada é mais longa que a reta). Serve para
    distinguir "cruzou 20 min a 98" de "tocou 111 numa descida".

    Ignora segmento parado (≤3 km/h nas duas pontas) e lacuna > 30 min, senão
    contamina — foi o que produziu "sustentada de 120 com pico 100" na 1ª versão.
    """
    janela = [p for p in pontos
              if ini - timedelta(minutes=GAP_EPISODIO_MIN) <= p['data']
              <= fim + timedelta(minutes=GAP_EPISODIO_MIN)]
    km = horas = 0.0
    for a, b in zip(janela, janela[1:]):
        dt_h = (b['data'] - a['data']).total_seconds() / 3600
        if dt_h <= 0 or dt_h > GAP_MAX_SUSTENTADA_MIN / 60:
            continue
        if (a.get('velocidade') or 0) <= VEL_PARADO_KMH and (b.get('velocidade') or 0) <= VEL_PARADO_KMH:
            continue
        d = geocoding.km_entre(a['latitude'], a['longitude'], b['latitude'], b['longitude'])
        if d is None:
            continue
        km += d
        horas += dt_h
    if horas <= 0:
        return None
    return int(round(km / horas))


def pico_exibido(vel_max, vel_sustentada):
    """Pico a mostrar = max(leitura, sustentada). Regra geral, não exceção.

    As duas colunas são **piso** do pico real, não "uma medida e uma
    estimativa":
      - a leitura é instantânea, então o pico do intervalo é ≥ leitura;
      - a sustentada usa haversine (subestima 5–15%) e é uma média, e o máximo
        de qualquer função é ≥ sua média — logo o pico real é ≥ sustentada.

    Mostrar o maior dos dois é o número mais correto disponível, e erra sempre
    para baixo: se o motorista contestar, a resposta é "no mínimo X". Na
    prática só muda o caso raro em que a amostragem perdeu o pico verdadeiro
    (11/08: 1 de 41). `vel_max` e `vel_sustentada` seguem crus na tabela, então
    a auditoria de onde saiu o número continua possível.
    """
    return max(vel_max or 0, vel_sustentada or 0)


def resumir_episodio(ep, pontos_placa):
    """Episódio → dict pronto para gravar. O ponto de referência é o do PICO."""
    ini, fim = ep[0]['data'], ep[-1]['data']
    pico = max(ep, key=lambda p: p['velocidade'])
    return {
        'ini': _utc_para_brt(ini),
        'fim': _utc_para_brt(fim),
        'registros': len(ep),
        'vel_max': pico['velocidade'],
        'vel_sustentada': velocidade_sustentada(pontos_placa, ini, fim),
        # 2+ registros no mesmo episódio. A média do trecho saiu do critério:
        # é derivada e subestima, então não serve de porteiro (só de coluna).
        'sustentado': len(ep) >= MIN_REG_SUSTENTADO,
        'cidade': pico.get('cidade'),
        'uf': pico.get('uf'),
        'endereco': pico.get('endereco'),
        'latitude': pico.get('latitude'),
        'longitude': pico.get('longitude'),
    }


def resumir_cobertura(pontos):
    """Cobertura do dia por placa, para o rodapé do relatório.

    Sem isto, "zero excessos" vira falsa segurança: não dá para distinguir
    "ninguém correu" de "o worker estava fora do ar" — ainda mais depois que a
    retenção apagar as posições.

    **Lacuna só conta como falta de sinal se o veículo SE MOVEU durante ela.**
    Parado, o aparelho reporta de 1 em 1 hora (ou 12 em 12), então lacuna longa
    é o comportamento normal, não cegueira: medido em 11/08, as maiores
    lacunas do dia (243, 239, 142 min) tinham deslocamento de 0,0 km — eram
    pátio, não perda de sinal. Alarmar nelas seria acusar todo caminhão parado
    e queimar o rótulo no primeiro dia.

    O discriminador é a velocidade implícita (deslocamento ÷ duração), o mesmo
    critério que o §8 usa para lacuna × parada.
    """
    if not pontos:
        return {'posicoes': 0, 'minutos_com_sinal': 0, 'minutos_sem_sinal': 24 * 60,
                'maior_gap_min': 24 * 60, 'minutos_sem_sinal_mov': 24 * 60,
                'maior_gap_mov_min': 24 * 60}

    gaps, gaps_mov = [], []
    for a, b in zip(pontos, pontos[1:]):
        g = (b['data'] - a['data']).total_seconds() / 60
        if g <= 0:
            continue
        gaps.append(g)
        if g <= GAP_EPISODIO_MIN:
            continue
        d = geocoding.km_entre(a['latitude'], a['longitude'], b['latitude'], b['longitude'])
        if d is not None and (d / (g / 60)) > VEL_IMPLICITA_MOVIMENTO_KMH:
            gaps_mov.append(g)

    total = (pontos[-1]['data'] - pontos[0]['data']).total_seconds() / 60
    sem = sum(g for g in gaps if g > GAP_EPISODIO_MIN)
    return {
        'posicoes': len(pontos),
        'minutos_com_sinal': int(round(total - sem)),
        'minutos_sem_sinal': int(round(sem + (24 * 60 - total))),
        'maior_gap_min': int(round(max(gaps))) if gaps else 0,
        # as duas que o relatório usa para alarmar
        'minutos_sem_sinal_mov': int(round(sum(gaps_mov))),
        'maior_gap_mov_min': int(round(max(gaps_mov))) if gaps_mov else 0,
    }


def cobertura_insuficiente(cob):
    """True se perdemos o veículo EM MOVIMENTO tempo demais.

    Placa que não aparece no relatório lê como "comportou-se bem"; com este
    rótulo, lê como "não sabemos". São coisas muito diferentes para quem
    recebe.
    """
    return (cob.get('maior_gap_mov_min') or 0) >= GAP_COBERTURA_RUIM_MIN


# ── Persistência ─────────────────────────────────────────────────────

def _posicoes_do_dia(cur, dia):
    """placa → pontos do dia (BRT), ordenados. data_posicao vem em UTC."""
    ini, fim = janela_utc_do_dia(dia)
    cur.execute("""
        SELECT placa, data_posicao, velocidade, latitude, longitude,
               cidade, uf, endereco
        FROM embarques_posicoes_historico
        WHERE data_posicao >= %s AND data_posicao < %s
        ORDER BY placa, data_posicao
    """, (ini, fim))
    por_placa = {}
    for placa, data, vel, lat, lng, cidade, uf, endereco in cur.fetchall():
        por_placa.setdefault(placa, []).append({
            'data': data, 'velocidade': vel,
            'latitude': float(lat) if lat is not None else None,
            'longitude': float(lng) if lng is not None else None,
            'cidade': cidade, 'uf': uf, 'endereco': endereco,
        })
    return por_placa


def apurar_dia(cur, dia, cadastro=None):
    """Apura um dia de Brasília e grava pgr_eventos + pgr_cobertura.

    `cadastro`: placa → {'tipo_veiculo','tipo_operacao'} (de veiculos_045).
    Opcional — sem ele os campos ficam nulos e a apuração continua válida.

    Reprocessável: o UNIQUE (placa, ini) faz upsert, então rodar de novo depois
    do backfill corrige um dia que tenha sido apurado com dado incompleto.
    """
    cadastro = cadastro or {}
    por_placa = _posicoes_do_dia(cur, dia)
    n_ev = 0

    for placa, pontos in sorted(por_placa.items()):
        cad = cadastro.get(placa) or {}
        for ep in agrupar_episodios(pontos):
            r = resumir_episodio(ep, pontos)
            cur.execute("""
                INSERT INTO pgr_eventos (
                    dia, placa, tipo_veiculo, tipo_operacao, ini, fim, registros,
                    vel_max, vel_sustentada, sustentado, cidade, uf, endereco,
                    latitude, longitude, situacao_carga, apurado_em
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                ON CONFLICT (placa, ini) DO UPDATE SET
                    dia = EXCLUDED.dia,
                    tipo_veiculo = EXCLUDED.tipo_veiculo,
                    tipo_operacao = EXCLUDED.tipo_operacao,
                    fim = EXCLUDED.fim,
                    registros = EXCLUDED.registros,
                    vel_max = EXCLUDED.vel_max,
                    vel_sustentada = EXCLUDED.vel_sustentada,
                    sustentado = EXCLUDED.sustentado,
                    cidade = EXCLUDED.cidade,
                    uf = EXCLUDED.uf,
                    endereco = EXCLUDED.endereco,
                    latitude = EXCLUDED.latitude,
                    longitude = EXCLUDED.longitude,
                    apurado_em = NOW()
            """, (dia, placa, cad.get('tipo_veiculo'), cad.get('tipo_operacao'),
                  r['ini'], r['fim'], r['registros'], r['vel_max'], r['vel_sustentada'],
                  r['sustentado'], r['cidade'], r['uf'], r['endereco'],
                  r['latitude'], r['longitude'], 'nao_confirmado'))
            n_ev += 1

        c = resumir_cobertura(pontos)
        cur.execute("""
            INSERT INTO pgr_cobertura (dia, placa, posicoes, minutos_com_sinal,
                                       minutos_sem_sinal, maior_gap_min,
                                       minutos_sem_sinal_mov, maior_gap_mov_min, apurado_em)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ON CONFLICT (dia, placa) DO UPDATE SET
                posicoes = EXCLUDED.posicoes,
                minutos_com_sinal = EXCLUDED.minutos_com_sinal,
                minutos_sem_sinal = EXCLUDED.minutos_sem_sinal,
                maior_gap_min = EXCLUDED.maior_gap_min,
                minutos_sem_sinal_mov = EXCLUDED.minutos_sem_sinal_mov,
                maior_gap_mov_min = EXCLUDED.maior_gap_mov_min,
                apurado_em = NOW()
        """, (dia, placa, c['posicoes'], c['minutos_com_sinal'],
              c['minutos_sem_sinal'], c['maior_gap_min'],
              c['minutos_sem_sinal_mov'], c['maior_gap_mov_min']))

    _logger.info(f'PGR {dia:%d/%m/%Y}: {n_ev} episódios em {len(por_placa)} placas')
    return n_ev


# ── Leitura (o relatório e o envio leem DAQUI, nunca recalculam) ─────

_SITUACAO_ROTULO = {
    'carregado': 'carregado', 'vazio': 'vazio',
    'parcial': 'parcial', 'nao_confirmado': 'não confirmado',
}


def _situacao_consolidada(situacoes):
    """Placa com episódios em situações diferentes vira 'parcial'.

    Apareceu em 4 de 20 placas no levantamento: entrega de manhã, volta à
    tarde, corre nas duas pernas. Por isso são quatro situações, não duas.
    """
    s = {x for x in situacoes if x}
    if not s:
        return 'nao_confirmado'
    if len(s) == 1:
        return s.pop()
    if s <= {'carregado', 'vazio', 'parcial'}:
        return 'parcial'
    return 'nao_confirmado'


def listar_dia(cur, dia):
    """Payload do relatório de um dia: uma linha por placa + cobertura.

    O grão gravado é o EPISÓDIO; a linha do relatório é a agregação — mesmo
    padrão do resto do app (grão CTRB → agrega).
    """
    cur.execute("""
        SELECT placa, tipo_veiculo, tipo_operacao, ini, fim, registros, vel_max,
               vel_sustentada, sustentado, cidade, uf, endereco, situacao_carga,
               manifesto, tomador, origem, destino, motorista
        FROM pgr_eventos WHERE dia = %s ORDER BY placa, ini
    """, (dia,))
    cols = ('placa', 'tipo_veiculo', 'tipo_operacao', 'ini', 'fim', 'registros',
            'vel_max', 'vel_sustentada', 'sustentado', 'cidade', 'uf', 'endereco',
            'situacao_carga', 'manifesto', 'tomador', 'origem', 'destino', 'motorista')
    eventos = [dict(zip(cols, r)) for r in cur.fetchall()]

    cur.execute("""
        SELECT placa, posicoes, maior_gap_min, maior_gap_mov_min, minutos_sem_sinal_mov
        FROM pgr_cobertura WHERE dia = %s
    """, (dia,))
    cobertura = {r[0]: {'posicoes': r[1], 'maior_gap_min': r[2],
                        'maior_gap_mov_min': r[3], 'minutos_sem_sinal_mov': r[4]}
                 for r in cur.fetchall()}

    por_placa = {}
    for e in eventos:
        p = por_placa.setdefault(e['placa'], {
            'placa': e['placa'], 'tipo_veiculo': e['tipo_veiculo'],
            'tipo_operacao': e['tipo_operacao'], 'registros': 0, 'pico': 0,
            'sustentado': False, 'cidades': [], 'situacoes': [], 'episodios': [],
            'tomador': e['tomador'], 'origem': e['origem'], 'destino': e['destino'],
            'motorista': e['motorista'], 'manifesto': e['manifesto'],
        })
        p['registros'] += e['registros'] or 0
        p['pico'] = max(p['pico'], pico_exibido(e['vel_max'], e['vel_sustentada']))
        p['sustentado'] = p['sustentado'] or bool(e['sustentado'])
        p['situacoes'].append(e['situacao_carga'])
        cid = f"{e['cidade']}/{e['uf']}" if e['cidade'] and e['uf'] else (e['cidade'] or '')
        if cid and cid not in p['cidades']:
            p['cidades'].append(cid)
        p['episodios'].append({
            'ini': e['ini'].strftime('%H:%M') if e['ini'] else None,
            'fim': e['fim'].strftime('%H:%M') if e['fim'] else None,
            'registros': e['registros'], 'vel_max': e['vel_max'],
            'vel_sustentada': e['vel_sustentada'],
            'pico': pico_exibido(e['vel_max'], e['vel_sustentada']),
            'sustentado': e['sustentado'], 'cidade': e['cidade'], 'uf': e['uf'],
            'endereco': e['endereco'], 'situacao_carga': e['situacao_carga'],
        })

    linhas = []
    for p in por_placa.values():
        p['situacao_carga'] = _situacao_consolidada(p['situacoes'])
        p['situacao_rotulo'] = _SITUACAO_ROTULO.get(p['situacao_carga'], p['situacao_carga'])
        p['cobertura'] = cobertura.get(p['placa'], {})
        del p['situacoes']
        linhas.append(p)
    linhas.sort(key=lambda x: (-x['pico'], -x['registros'], x['placa']))

    # Placa que não aparece lê como "comportou-se bem"; sem este bloco não dá
    # para distinguir isso de "não sabemos".
    sem_cobertura = sorted(
        ({'placa': pl, **c} for pl, c in cobertura.items() if cobertura_insuficiente(c)),
        key=lambda x: -(x['maior_gap_mov_min'] or 0))

    return {
        'dia': dia.isoformat(),
        'linhas': linhas,
        'sem_cobertura': sem_cobertura,
        'totais': {
            'veiculos': len(linhas),
            'registros': sum(l['registros'] for l in linhas),
            'pico': max((l['pico'] for l in linhas), default=0),
            'sustentados': sum(1 for l in linhas if l['sustentado']),
            'com_carga': sum(1 for l in linhas if l['situacao_carga'] in ('carregado', 'parcial')),
            'frota': sum(1 for l in linhas if (l['tipo_operacao'] or '').upper() == 'FROTA'),
            'agregado': sum(1 for l in linhas if (l['tipo_operacao'] or '').upper() == 'AGREGADO'),
            'placas_monitoradas': len(cobertura),
        },
        'limiar': LIMIAR_KMH,
    }


# ── Token de leitura (link do WhatsApp sem login) ────────────────────

def criar_token(cur, dia, validade_dias=7):
    """Token de leitura de UM relatório. Vazou um link, expôs um dia."""
    import secrets
    token = secrets.token_urlsafe(24)
    cur.execute("""
        INSERT INTO pgr_tokens (token, dia, expira_em)
        VALUES (%s, %s, NOW() + %s::interval)
    """, (token, dia, f'{validade_dias} days'))
    return token


def validar_token(cur, token, dia=None):
    """Devolve o dia do token se válido; None se inexistente/expirado.

    Registra o acesso — barato, e ajuda a saber depois se o diretor abriu.
    """
    if not token:
        return None
    cur.execute("SELECT dia FROM pgr_tokens WHERE token = %s AND expira_em > NOW()", (token,))
    r = cur.fetchone()
    if not r:
        return None
    if dia is not None and r[0] != dia:
        return None
    cur.execute("""UPDATE pgr_tokens SET acessos = acessos + 1, ultimo_acesso = NOW()
                   WHERE token = %s""", (token,))
    return r[0]
