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
import placas

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


# ── Situação de carga (HANDOFF-PGR §8) ──────────────────────────────
#
# Casar o excesso com o manifesto POR DATA é frágil nos dois sentidos: estrito
# perde viagem longa (o CTRC sai dias antes de o caminhão chegar), frouxo casa
# 100% e mente. O risco real é dizer "a 105 carregado de Nestlé" quando o
# caminhão já tinha descarregado.
#
# Então a prova é POSIÇÃO, com três testes:
#   1. corredor — o ponto do excesso está entre origem e destino?
#   2. sentido  — estava se aproximando do destino?
#   3. janela   — o excesso é depois da emissão e dentro do prazo plausível?
DESVIO_MAX = float(os.getenv('PGR_DESVIO_CORREDOR', '1.35'))   # (dO+dD)/dOD
RAIO_CIDADE_KM = float(os.getenv('PGR_RAIO_CIDADE', '25'))     # "esteve na cidade"
# Dias de posição carregados ANTES do dia do relatório, para provar a passagem
# pela origem. Precisa ser generoso: viagem longa fica dias parada no meio do
# caminho (o TZC0I41 carregou em 06/08 e só excedeu em 10/08).
DIAS_LOOKBACK = int(os.getenv('PGR_DIAS_LOOKBACK', '12'))
# Teto de sanidade para não casar manifesto antigo demais. NÃO é o critério —
# quem decide é a posição.
MAX_DIAS_MANIFESTO = int(os.getenv('PGR_MAX_DIAS_MANIFESTO', '20'))
#
# ⚠️ CALIBRAÇÃO PENDENTE. Os limiares acima vieram do estudo da sessão anterior,
# que rodou sobre 10 dias de posição de produção. Não foi possível recalibrá-los
# aqui: o banco de desenvolvimento só tem 1 dia backfillado, e com 1 dia o teste
# "passou pela origem" fica fraco justamente onde mais importa — Uberlândia é a
# base, quase todo veículo passa por lá, então manifestos com origem Uberlândia
# casam fácil demais. Refazer a comparação contra o layout aprovado depois do
# reprocessamento de produção, e considerar reativar o teste de SENTIDO
# (aproximando do destino) como guarda extra se aparecer falso positivo.

IDADE_MAX_CADASTRO_DIAS = 3


def cadastro_do_cache(cur):
    """placa → dados do veículo, lidos de pgr_cadastro_veiculos.

    O cache é alimentado pelo server.py (o lado que fala Power BI). Se estiver
    velho, avisa no log e segue com o que tem: rótulo faltando é falha macia,
    job quebrado no meio da madrugada não é.

    Só devolve o que é propriedade do VEÍCULO. `tipo_operacao` (frota/agregado)
    não sai daqui — é propriedade da viagem (a regra é sobre o par
    cavalo+carreta), então vem do casamento com o manifesto.
    """
    cur.execute("SELECT placa_norm, tipo, proprietario, eh_rizza, atualizado_em "
                "FROM pgr_cadastro_veiculos")
    linhas = cur.fetchall()
    if not linhas:
        _logger.warning('PGR: cache de cadastro VAZIO — tipo de veículo sairá em branco. '
                        'Rodar POST /api/pgr/sync-cadastro.')
        return {}
    mais_novo = max(r[4] for r in linhas if r[4]) if any(r[4] for r in linhas) else None
    if mais_novo and (datetime.utcnow() - mais_novo).days > IDADE_MAX_CADASTRO_DIAS:
        _logger.warning(f'PGR: cache de cadastro com {(datetime.utcnow() - mais_novo).days}d '
                        f'(atualizado em {mais_novo:%d/%m %H:%M}) — seguindo com o que tem.')
    return {r[0]: {'tipo_veiculo': r[1], 'proprietario': r[2], 'eh_rizza': r[3]}
            for r in linhas}


def _cortar(v, n):
    """Corta no tamanho da coluna de pgr_eventos, que é menor que a do cache —
    razão social longa do Winthor derrubaria a apuração inteira."""
    s = (str(v).strip() if v is not None else '')
    return s[:n] or None


def _manifestos_candidatos(cur, placas_norm, dia):
    """placa_norm → manifestos que podem cobrir um excesso do dia.

    Busca por cavalo OU carreta: o rastreador está numa das duas, e a placa do
    evento pode aparecer em qualquer dos dois campos.
    """
    if not placas_norm:
        return {}
    cur.execute("""
        SELECT placa_cavalo, placa_carreta, manifesto, data_ref, origem, destino,
               origem_lat, origem_lng, destino_lat, destino_lng,
               tomador, motorista, tipo_operacao
        FROM pgr_manifestos
        WHERE (placa_cavalo = ANY(%s) OR placa_carreta = ANY(%s))
          AND data_ref BETWEEN %s - 25 AND %s + 1
    """, (list(placas_norm), list(placas_norm), dia, dia))
    idx = {}
    for r in cur.fetchall():
        m = {'manifesto': r[2], 'data_ref': r[3], 'origem': r[4], 'destino': r[5],
             'o_lat': r[6], 'o_lng': r[7], 'd_lat': r[8], 'd_lng': r[9],
             'tomador': r[10], 'motorista': r[11], 'tipo_operacao': r[12]}
        for p in (r[0], r[1]):
            if p:
                idx.setdefault(p, []).append(m)
    return idx


def _esteve_em(pontos, lat, lng, ate):
    """Quando o veículo esteve por último a menos de RAIO_CIDADE_KM daqui,
    antes de `ate`. Devolve o datetime (falsy quando nunca esteve), para servir
    tanto de teste quanto de data da entrega."""
    quando = None
    for p in pontos:
        if p['data'] > ate or p['latitude'] is None:
            continue
        d = geocoding.km_entre(p['latitude'], p['longitude'], lat, lng)
        if d is not None and d <= RAIO_CIDADE_KM:
            quando = p['data']
    return quando


def _avaliar_manifesto(ep, man, historico):
    """Os três testes do §8 — todos por POSIÇÃO. Devolve (ok, motivo, desvio).

    Deliberadamente SEM janela por data: casar por data é frágil nos dois
    sentidos, e o caso que prova isso é o TZC0I41 — carregou em Nerópolis
    06/08, ficou 3 dias parado em Uberlândia e só excedeu em 10/08. Qualquer
    limite de "dias plausíveis" rejeita essa viagem, que era legítima. A data
    entra só como sanidade (o excesso não pode ser ANTES da emissão, e há um
    teto para não casar manifesto antigo demais).
    """
    if man['o_lat'] is None or man['d_lat'] is None:
        return False, 'sem centroide', None
    O = (float(man['o_lat']), float(man['o_lng']))
    D = (float(man['d_lat']), float(man['d_lng']))
    pico = max(ep, key=lambda p: p['velocidade'])
    if pico['latitude'] is None:
        return False, 'evento sem posicao', None

    # Sanidade de data (não é o critério: quem decide é a posição)
    delta = (ep[0]['data'].date() - man['data_ref']).days
    if delta < 0:
        return False, f'evento antes da emissao ({delta}d)', None
    if delta > MAX_DIAS_MANIFESTO:
        return False, f'manifesto {delta}d antes (teto {MAX_DIAS_MANIFESTO}d)', None

    # 1) corredor — o excesso está entre origem e destino?
    dO = geocoding.km_entre(pico['latitude'], pico['longitude'], *O)
    dD = geocoding.km_entre(pico['latitude'], pico['longitude'], *D)
    dOD = geocoding.km_entre(O[0], O[1], D[0], D[1]) or 1
    if dO is None or dD is None:
        return False, 'sem distancia', None
    desvio = (dO + dD) / dOD
    if desvio > DESVIO_MAX:
        return False, f'fora do corredor ({desvio:.2f}x)', desvio

    # 2) passou pela ORIGEM antes do excesso? (é o que prova que pegou a carga)
    if not _esteve_em(historico, O[0], O[1], ep[0]['data']):
        return False, 'nao passou pela origem', desvio

    # 3) e ainda NÃO tinha chegado no destino? (se chegou, já entregou)
    entregue_em = _esteve_em(historico, D[0], D[1], ep[0]['data'])
    if entregue_em:
        return False, 'ja tinha chegado no destino', entregue_em

    return True, 'ok', desvio


def _situacao_do_episodio(ep, historico, candidatos):
    """Devolve (situacao, manifesto|None, entregue_em|None)."""
    if not candidatos:
        return 'nao_confirmado', None, None

    aceitos, entregues = [], []
    for man in candidatos:
        ok, motivo, extra = _avaliar_manifesto(ep, man, historico)
        if ok:
            aceitos.append((extra if extra is not None else 9, man))
        elif motivo == 'ja tinha chegado no destino':
            # Evidência positiva de vazio: passou pelo destino deste manifesto
            # antes do excesso. `extra` é QUANDO passou.
            entregues.append((extra, man))
    if aceitos:
        # o mais "reto" no corredor é o mais provável
        return 'carregado', min(aceitos, key=lambda x: x[0])[1], None
    if entregues:
        # A viagem que acabou de terminar é a mais recente. Guardá-la não é
        # detalhe: é dela que sai o frota/agregado (propriedade da VIAGEM), e é
        # o que permite dizer "entregou em X em DD/MM" em vez de só "vazio".
        quando, man = max(entregues, key=lambda x: x[0])
        return 'vazio', man, quando
    # Sem prova, fica "não confirmado". Dizer "vazio" sem evidência é o mesmo
    # erro de dizer "carregado" sem evidência, só na direção oposta.
    return 'nao_confirmado', None, None


def _historico_lookback(cur, placas_alvo, dia):
    """placa → posições dos DIAS ANTERIORES + o dia, para provar origem/destino.

    Amostra 1 ponto a cada 15 min: para saber se o veículo passou por uma
    cidade não é preciso a série inteira, e carregar 12 dias de todas as placas
    na densidade cheia seria caro à toa (~676 pontos/veículo/dia).
    """
    if not placas_alvo:
        return {}
    ini, fim = janela_utc_do_dia(dia)
    cur.execute("""
        SELECT DISTINCT ON (placa, date_trunc('hour', data_posicao),
                            (EXTRACT(MINUTE FROM data_posicao)::int / 15))
               placa, data_posicao, latitude, longitude
        FROM embarques_posicoes_historico
        WHERE placa = ANY(%s) AND data_posicao >= %s AND data_posicao < %s
        ORDER BY placa, date_trunc('hour', data_posicao),
                 (EXTRACT(MINUTE FROM data_posicao)::int / 15), data_posicao
    """, (list(placas_alvo), ini - timedelta(days=DIAS_LOOKBACK), fim))
    out = {}
    for placa, data, lat, lng in cur.fetchall():
        out.setdefault(placa, []).append({
            'data': data,
            'latitude': float(lat) if lat is not None else None,
            'longitude': float(lng) if lng is not None else None,
        })
    for v in out.values():
        v.sort(key=lambda p: p['data'])
    return out


def apurar_dia(cur, dia, cadastro=None):
    """Apura um dia de Brasília e grava pgr_eventos + pgr_cobertura.

    `cadastro`: placa → {'tipo_veiculo', ...}. Se None, lê do cache local.
    Sem ele os campos ficam nulos e a apuração continua válida.

    Reprocessável: o UNIQUE (placa, ini) faz upsert, então rodar de novo depois
    do backfill corrige um dia que tenha sido apurado com dado incompleto.
    """
    if cadastro is None:
        cadastro = cadastro_do_cache(cur)
    por_placa = _posicoes_do_dia(cur, dia)
    n_ev = 0

    # Só as placas que de fato excederam precisam de manifesto e de histórico.
    placas_excesso = [p for p, pts in por_placa.items() if agrupar_episodios(pts)]
    com_excesso = {placas.mercosul(p) for p in placas_excesso}
    manifestos = _manifestos_candidatos(cur, com_excesso, dia)
    if com_excesso and not manifestos:
        _logger.warning('PGR: nenhum manifesto em cache — situação de carga sairá '
                        '"não confirmado". Rodar POST /api/pgr/sync-cadastro.')
    # Os testes de origem/destino precisam dos dias ANTERIORES: a carga pode ter
    # sido pega uma semana antes do excesso.
    historico = _historico_lookback(cur, placas_excesso, dia)

    for placa, pontos in sorted(por_placa.items()):
        # A placa do GPS pode vir na grafia antiga; o cache é chaveado em
        # Mercosul (as duas grafias do mesmo veículo colapsam numa chave só).
        pnorm = placas.mercosul(placa)
        cad = cadastro.get(pnorm) or cadastro.get(placa) or {}
        cands = manifestos.get(pnorm, [])
        hist = historico.get(placa) or pontos
        for ep in agrupar_episodios(pontos):
            r = resumir_episodio(ep, pontos)
            r['situacao_carga'], man, entregue = _situacao_do_episodio(ep, hist, cands)
            r['manifesto'] = man
            r['entregue_em'] = _utc_para_brt(entregue) if entregue else None
            m = r['manifesto'] or {}
            cur.execute("""
                INSERT INTO pgr_eventos (
                    dia, placa, tipo_veiculo, tipo_operacao, ini, fim, registros,
                    vel_max, vel_sustentada, sustentado, cidade, uf, endereco,
                    latitude, longitude, situacao_carga, manifesto, tomador,
                    origem, destino, motorista, entregue_em, apurado_em
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
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
                    situacao_carga = EXCLUDED.situacao_carga,
                    manifesto = EXCLUDED.manifesto,
                    tomador = EXCLUDED.tomador,
                    origem = EXCLUDED.origem,
                    destino = EXCLUDED.destino,
                    motorista = EXCLUDED.motorista,
                    entregue_em = EXCLUDED.entregue_em,
                    apurado_em = NOW()
            """, (dia, placa, cad.get('tipo_veiculo'), m.get('tipo_operacao'),
                  r['ini'], r['fim'], r['registros'], r['vel_max'], r['vel_sustentada'],
                  r['sustentado'], r['cidade'], r['uf'], r['endereco'],
                  r['latitude'], r['longitude'], r['situacao_carga'],
                  _cortar(m.get('manifesto'), 20), _cortar(m.get('tomador'), 120),
                  _cortar(m.get('origem'), 60), _cortar(m.get('destino'), 60),
                  _cortar(m.get('motorista'), 120), r.get('entregue_em')))
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

# Ruído de razão social: a partir daqui o nome deixa de identificar a empresa.
_LIXO_RAZAO = {
    'LTDA', 'LTD', 'LT', 'SA', 'S/A', 'S.A', 'S.A.', 'ME', 'EPP', 'EIRELI', 'CIA',
    'IND', 'IND.', 'INDUSTRIA', 'INDUSTRIAL', 'COM', 'COM.', 'COMERCIO', 'COMERCIAL',
    'DISTRIBUICAO', 'DISTRIBUIDORA', 'DIST', 'LOGISTICA', 'LOG', 'TRANSPORTES',
    'TRANSPORTE', 'IMPORTACAO', 'EXPORTACAO', 'ARMAZENS', 'ARMAZEM', 'GERAIS',
    'PRODUTOS', 'ALIMENTOS', 'SERVICOS', 'PARTICIPACOES', 'EMPREENDIMENTOS',
}
_CONECTIVOS = {'DE', 'DA', 'DO', 'DAS', 'DOS', 'E'}


def _titulo(tok):
    """Capitaliza preservando sigla curta (JC, RG) e conectivo minúsculo."""
    if tok in _CONECTIVOS:
        return tok.lower()
    if len(tok) <= 3 and not any(v in tok for v in 'AEIOU'):
        return tok            # sigla: JC, RG, TNT
    return tok.capitalize()


def nome_curto(razao, max_tokens=3):
    """Razão social crua → nome legível.

    'MARTINS URN-MG DISTRIBUICAO LT' → 'Martins'
    'PERNOD RICARD BRASIL IND E COMERCIO LTDA' → 'Pernod Ricard Brasil'

    A razão social completa ocupa metade da linha e empurra a rota para a
    quebra; o que identifica a empresa são as primeiras palavras. Vários
    tomadores no mesmo manifesto (separados por vírgula) viram 'Fulano +N'.
    """
    s = (str(razao).strip() if razao else '')
    if not s:
        return None
    partes = [p.strip() for p in s.split(',') if p.strip()]
    extras = len(partes) - 1
    tokens, usados = [], 0
    for tok in partes[0].upper().split():
        limpo = tok.strip('.,;')
        if limpo in _LIXO_RAZAO or any(c.isdigit() for c in limpo) or '-' in limpo:
            break
        tokens.append(_titulo(limpo))
        if limpo not in _CONECTIVOS:
            usados += 1
        if usados >= max_tokens:
            break
    nome = ' '.join(tokens) or partes[0][:24]
    return f'{nome} +{extras}' if extras > 0 else nome


def nome_pessoa(nome, max_tokens=3):
    """'DANIEL DOS SANTOS ALMEIDA' → 'Daniel dos Santos'."""
    s = (str(nome).strip() if nome else '')
    if not s:
        return None
    tokens, usados = [], 0
    for tok in s.upper().split():
        tokens.append(_titulo(tok))
        if tok not in _CONECTIVOS:
            usados += 1
        if usados >= max_tokens:
            break
    return ' '.join(tokens)


def cidade_uf_titulo(s):
    """'UBERLANDIA/MG' → 'Uberlandia/MG'. UF fica em caixa alta.

    A origem/destino vem do Power BI em caixa alta, enquanto as cidades do
    trecho vêm do 3S já capitalizadas — misturar os dois na mesma linha fica
    visualmente inconsistente.
    """
    s = (str(s).strip() if s else '')
    if not s:
        return None
    if '/' in s:
        cid, uf = s.rsplit('/', 1)
        return f'{" ".join(_titulo(t) for t in cid.upper().split())}/{uf.strip().upper()}'
    return ' '.join(_titulo(t) for t in s.upper().split())


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
    # Basta UM episódio provado com carga para a placa ter rodado carregada em
    # parte do dia. Rotular a placa inteira de "não confirmado" nesse caso
    # jogaria fora prova que temos; o detalhe por episódio fica na página.
    if 'carregado' in s:
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
               manifesto, tomador, origem, destino, motorista, entregue_em
        FROM pgr_eventos WHERE dia = %s ORDER BY placa, ini
    """, (dia,))
    cols = ('placa', 'tipo_veiculo', 'tipo_operacao', 'ini', 'fim', 'registros',
            'vel_max', 'vel_sustentada', 'sustentado', 'cidade', 'uf', 'endereco',
            'situacao_carga', 'manifesto', 'tomador', 'origem', 'destino', 'motorista',
            'entregue_em')
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
            'tomador': None, 'origem': None, 'destino': None,
            'motorista': None, 'manifesto': None, 'entregue_em': None,
        })
        # O contexto da placa vem de um episódio que TENHA manifesto, não do
        # primeiro em ordem cronológica: numa placa "parcial", o primeiro
        # episódio do dia costuma ser o vazio, e a linha saía com "—" mesmo
        # havendo carga provada no resto do dia.
        if e['tomador'] and not p['tomador']:
            p.update({k: e[k] for k in
                      ('tomador', 'origem', 'destino', 'motorista', 'manifesto',
                       'entregue_em')})
        if e['tipo_operacao'] and not p['tipo_operacao']:
            p['tipo_operacao'] = e['tipo_operacao']
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
        p['tomador'] = nome_curto(p['tomador'])
        p['motorista'] = nome_pessoa(p['motorista'])
        p['origem'] = cidade_uf_titulo(p['origem'])
        p['destino'] = cidade_uf_titulo(p['destino'])
        del p['situacoes']
        linhas.append(p)
    # Por RECORRÊNCIA, não por gravidade — é a ordem do layout aprovado, e a
    # que serve para cobrar: 81% dos episódios são pico isolado, então quem
    # tocou 117 uma vez é ruído e quem fez 13 registros é conduta.
    linhas.sort(key=lambda x: (-x['registros'], -x['pico'], x['placa']))

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


def token_do_dia(cur, dia, validade_dias=7):
    """Token válido do dia, reaproveitando se já existir.

    Sem isto, cada reenvio (ou reprocessamento) criaria um token novo e a
    tabela viraria depósito de links órfãos ainda válidos.
    """
    cur.execute("""SELECT token FROM pgr_tokens
                   WHERE dia = %s AND expira_em > NOW() + interval '1 day'
                   ORDER BY criado_em DESC LIMIT 1""", (dia,))
    r = cur.fetchone()
    if r:
        return r[0]
    return criar_token(cur, dia, validade_dias)


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
