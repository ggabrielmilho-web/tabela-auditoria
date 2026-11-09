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

    INFORMATIVA, não é critério de classificação nem número exibido (ver
    `pico_exibido`): um salto de posição falsa a infla sem limite. Usa haversine, então
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


def pico_exibido(vel_max, vel_sustentada=None):
    """Pico a mostrar = a LEITURA do aparelho. Nunca a sustentada.

    O relatório vira advertência para motorista, então só mostra velocidade que
    o aparelho mediu. A regra antiga era max(leitura, sustentada), com a tese de
    que a sustentada seria "piso" do pico real — e ela só é piso quando as
    posições são verdadeiras. Quando o aparelho congela (repete posição e
    odômetro parados) e depois acorda 20–30 km adiante, o salto vira velocidade:
    GZQ3080 em 29/09 saiu 176 com leitura de 100 (31 km em 304 s, odômetro +0);
    GZQ3A40 em 18/09 saiu 1.511 com leitura de 109. A leitura é instantânea e
    erra para baixo, que é o lado certo de errar numa advertência.

    `vel_sustentada` segue crua na tabela, só para auditoria; o parâmetro fica
    na assinatura por compatibilidade com quem chama.
    """
    return vel_max or 0


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
    # o cache guarda a placa do SSW (03/10/2026); a busca e o índice são pela chave Mercosul
    gr = sorted({g for p in placas_norm for g in placas.grafias(p)})
    cur.execute("""
        SELECT placa_cavalo, placa_carreta, manifesto, data_ref, origem, destino,
               origem_lat, origem_lng, destino_lat, destino_lng,
               tomador, motorista, tipo_operacao
        FROM pgr_manifestos
        WHERE (placa_cavalo = ANY(%s) OR placa_carreta = ANY(%s))
          AND data_ref BETWEEN %s - 25 AND %s + 1
    """, (gr, gr, dia, dia))
    idx = {}
    for r in cur.fetchall():
        m = {'manifesto': r[2], 'data_ref': r[3], 'origem': r[4], 'destino': r[5],
             'o_lat': r[6], 'o_lng': r[7], 'd_lat': r[8], 'd_lng': r[9],
             'tomador': r[10], 'motorista': r[11], 'tipo_operacao': r[12],
             'placa_cavalo': r[0], 'placa_carreta': r[1]}
        for p in {placas.mercosul(x) for x in (r[0], r[1]) if x}:
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


# ── Situação de carga pelo Embarques (09/11/2026) ───────────────────
#
# O PGR casava o excesso com um manifesto por conta própria (o cache
# `pgr_manifestos` + os três testes de posição acima). Era a ÚNICA tela que
# respondia "que viagem estava nesta placa neste instante" sem perguntar ao
# Embarques — a Jornada, o km por placa/dia, a Ficha da viagem e a Torre
# perguntam. E errava: em 08/11 pôs na carreta HOA0J28 o Antonio Claudio de um
# manifesto de 20/10 (NOD004983-2) que o Embarques já dava como continuado e
# entregue em Brasília em 22/10, quando a carreta rodava com o Carlos Alberto
# Dantas (UDI029956-1, de 07/11). Nenhuma das regras que o Embarques ganhou
# depois de 12/08 — manifesto seguinte fecha a viagem, continuação, janela por
# veículo, perna vazia — chegava aqui.
#
# Com a chave, a resposta é a carga do Embarques em que a placa estava no
# instante do PICO, no mesmo intervalo que a Jornada usa: da saída medida (sem
# saída, do dia do carregamento) até a conclusão; para o cavalo, até o
# desengate, se vier antes (a carreta segue na carga, o cavalo foi liberado).
#   carga       → carregado, com cliente, rota, motorista e placas DELA
#   perna vazia → vazio, com "entregou em X" da última carga que a placa
#                 concluiu. A perna é DERIVADA pelo robô e leva a equipe da
#                 carga SEGUINTE — a V-2026-000546 (22/10 → 31/10) diz Eliomar,
#                 mas quem trouxe a HOA0J28 de Brasília em 22/10 foi o Antonio.
#                 Então, como na Jornada: a perna só tem motorista quando a
#                 carga de antes e a de depois na mesma placa são do mesmo; o
#                 par e o frota/agregado, quando também coincidem.
#   entre uma carga concluída e a saída da próxima, sem linha de perna
#               → vazio, pela MESMA definição: a perna do Embarques é exatamente
#                 esse intervalo (`_rederivar_vazias.py`: conclusão de A → saída
#                 de B). A linha da perna só nasce quando o manifesto seguinte
#                 sai — às 06:35, na apuração, a volta vazia de ontem ainda não
#                 existe —, e nem sempre nasce. Medido no dump de 06/11: sem esta
#                 regra o envio sairia com 21% de "não confirmado" (o antigo,
#                 3%); com ela, nenhum caso contradiz o Embarques convergido.
#   nada antes  → não confirmado. Sem carga concluída antes, o PGR não sabe.
# Erro de borda do Embarques (saída marcada tarde, por exemplo) passa a ser
# corrigido UMA vez, lá, e vale aqui sozinho.
FONTE_EMBARQUES = os.getenv('PGR_FONTE_EMBARQUES', 'false').lower() in ('1', 'true', 'sim')
# Até onde olhar para trás atrás da carga que a perna vazia acabou de entregar.
# A perna mais longa da HOA0J28 em out/26 durou 9 dias.
JANELA_EMBARQUES_DIAS = int(os.getenv('PGR_EMBARQUES_JANELA_DIAS', '45'))
# O Embarques diz "Terceiro"; o PGR e o histórico dele (vindo da Auditoria
# Receita) dizem "CARRETEIRO". Fica o vocabulário do PGR, para o período e o
# estudo não misturarem duas palavras para a mesma coisa.
_TIPO_EMBARQUES = {'FROTA': 'FROTA', 'AGREGADO': 'AGREGADO', 'TERCEIRO': 'CARRETEIRO'}


def _cargas_embarques(cur, placas_norm, dia):
    """placa (chave Mercosul) → cargas e pernas vazias do Embarques que podem
    explicar um excesso do dia ou ser a carga que a perna acabou de entregar.

    Placa comparada pela chave (`= ANY(grafias)`): o Embarques grava a placa do
    SSW, o GPS manda a do aparelho, e as duas grafias são o mesmo veículo."""
    if not placas_norm:
        return {}
    gr = sorted({g for p in placas_norm for g in placas.grafias(p)})
    ini, _ = janela_utc_do_dia(dia)
    # Sem teto no carregamento: a carga SEGUINTE à perna vazia (a que diz se a
    # perna tem dono) pode ter carregado depois do dia apurado.
    cur.execute("""
        SELECT c.id, c.numero, COALESCE(c.viagem_vazia, FALSE), c.tipo_operacao,
               c.cliente_nome, c.motorista_nome, c.manifesto_origem,
               c.cavalo_placa, c.carreta1_placa, c.carreta2_placa,
               c.origem_cidade, c.origem_uf,
               (SELECT d.cidade || '/' || d.uf FROM embarques_cargas_destinos d
                 WHERE d.carga_id = c.id ORDER BY d.ordem DESC LIMIT 1),
               COALESCE(c.inicio_viagem, c.data_saida_real,
                        c.data_carregamento + %s * interval '1 hour'),
               c.data_conclusao, c.desengatada_em, c.motorista_cpf
          FROM embarques_cargas c
         WHERE COALESCE(c.status, '') <> 'Cancelada'
           AND (c.cavalo_placa = ANY(%s) OR c.carreta1_placa = ANY(%s)
                OR c.carreta2_placa = ANY(%s))
           AND (c.data_conclusao IS NULL OR c.data_conclusao >= %s)
    """, (BRT_OFFSET_H, gr, gr, gr, ini - timedelta(days=JANELA_EMBARQUES_DIAS)))
    idx = {}
    for r in cur.fetchall():
        c = {'id': r[0], 'numero': r[1], 'vazia': bool(r[2]),
             'tipo_operacao': _TIPO_EMBARQUES.get((r[3] or '').strip().upper(),
                                                  (r[3] or '').strip().upper() or None),
             'tomador': r[4], 'motorista': r[5], 'manifesto': r[6],
             'placa_cavalo': r[7], 'placa_carreta': r[8],
             'cavalo': placas.mercosul(r[7]) if r[7] else None,
             'carretas': {placas.mercosul(x) for x in (r[8], r[9]) if x},
             'origem': f'{r[10]}/{r[11]}' if r[10] and r[11] else (r[10] or None),
             'destino': r[12], 'inicio': r[13], 'conclusao': r[14], 'desengate': r[15],
             'cpf': ''.join(ch for ch in (r[16] or '') if ch.isdigit()) or None}
        for p in {c['cavalo'], *c['carretas']} - {None}:
            idx.setdefault(p, []).append(c)
    return idx


def _janela_da_carga(c, placa_norm):
    """(início, fim) da carga para ESTA placa, em UTC; fim None = aberta.
    None se a placa não é dela. A carreta fica na carga até a conclusão; o
    cavalo sai no que vier primeiro, conclusão ou desengate — mesma régua da
    Jornada (`_jornada_embarques`)."""
    if placa_norm in c['carretas']:
        return c['inicio'], c['conclusao']
    if placa_norm == c['cavalo']:
        fins = [x for x in (c['conclusao'], c['desengate']) if x is not None]
        return c['inicio'], (min(fins) if fins else None)
    return None


def carga_no_instante(cargas, placa_norm, instante):
    """(carga ativa, última carga carregada concluída antes, primeira carga
    carregada que começa depois) para a placa no instante (UTC). Função pura.

    Duas cargas cobrindo o mesmo instante (bitrem, carga A esperando no pátio
    enquanto a B já saiu): vale a que começou por último — é a viagem em curso.
    """
    ativa = anterior = proxima = None
    for c in cargas:
        jan = _janela_da_carga(c, placa_norm)
        if not jan or jan[0] is None:
            continue
        ini, fim = jan
        if ini <= instante and (fim is None or instante <= fim):
            if ativa is None or (ini, c['id']) > (_janela_da_carga(ativa, placa_norm)[0], ativa['id']):
                ativa = c
        elif c['vazia']:
            continue
        elif fim is not None and fim <= instante:
            if anterior is None or (fim, c['id']) > (_janela_da_carga(anterior, placa_norm)[1], anterior['id']):
                anterior = c
        elif ini > instante:
            if proxima is None or (ini, c['id']) < (_janela_da_carga(proxima, placa_norm)[0], proxima['id']):
                proxima = c
    return ativa, anterior, proxima


_CAMPOS_CONTEXTO = ('manifesto', 'tomador', 'origem', 'destino', 'motorista',
                    'tipo_operacao', 'placa_cavalo', 'placa_carreta')


def _mesma_pessoa(a, b):
    """Mesmo motorista nas duas cargas: pelo CPF; sem CPF em uma delas, pelo nome."""
    if a['cpf'] and b['cpf']:
        return a['cpf'] == b['cpf']
    na, nb = (' '.join((x['motorista'] or '').upper().split()) for x in (a, b))
    return bool(na) and na == nb


def _equipe_da_perna(perna, antes, depois, placa_norm):
    """Motorista, par e frota/agregado da perna vazia — só o que a carga de
    antes e a de depois, na mesma placa, confirmam juntas. O resto fica None."""
    eq = {'motorista': None, 'tipo_operacao': None, 'placa_cavalo': None, 'placa_carreta': None}
    if not (antes and depois and _mesma_pessoa(antes, depois)):
        return eq
    eq['motorista'] = perna.get('motorista') or depois['motorista']
    ehcav = placa_norm == depois['cavalo']
    if ehcav and antes['carretas'] == depois['carretas']:
        eq['placa_carreta'] = depois['placa_carreta']
    if not ehcav and antes['cavalo'] == depois['cavalo']:
        eq['placa_cavalo'] = depois['placa_cavalo']
    if antes['tipo_operacao'] == depois['tipo_operacao']:
        eq['tipo_operacao'] = depois['tipo_operacao']
    return eq


def situacao_pelo_embarques(cargas, placa_norm, instante):
    """(situacao, contexto|None, entregue_em_utc|None) — o mesmo formato de
    `_situacao_do_episodio`, para gravar e mostrar sem mudar a tela."""
    ativa, anterior, proxima = carga_no_instante(cargas, placa_norm, instante)
    if ativa is None and anterior is None:
        return 'nao_confirmado', None, None
    if ativa is not None and not ativa['vazia']:
        return 'carregado', {k: ativa[k] for k in _CAMPOS_CONTEXTO}, None
    # Perna vazia (com linha ou não): o "entregou em X" (cliente, rota, manifesto)
    # é da carga que a placa acabou de concluir; quem dirigia, só se antes e
    # depois concordam.
    ctx = _equipe_da_perna(ativa or {}, anterior, proxima, placa_norm)
    ctx.update({k: (anterior[k] if anterior else None)
                for k in ('manifesto', 'tomador', 'origem', 'destino')})
    entregue = _janela_da_carga(anterior, placa_norm)[1] if anterior else None
    return 'vazio', ctx, entregue


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
    if FONTE_EMBARQUES:
        # A viagem vem do Embarques: sem cache de manifesto e sem os 12 dias de
        # posição que os testes de origem/destino pediam.
        cargas = _cargas_embarques(cur, com_excesso, dia)
        manifestos, historico = {}, {}
    else:
        cargas = {}
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
            if FONTE_EMBARQUES:
                # O instante é o do PICO, o mesmo ponto que a linha mostra.
                instante = max(ep, key=lambda p: p['velocidade'])['data']
                r['situacao_carga'], man, entregue = situacao_pelo_embarques(
                    cargas.get(pnorm, []), pnorm, instante)
            else:
                r['situacao_carga'], man, entregue = _situacao_do_episodio(ep, hist, cands)
            r['manifesto'] = man
            r['entregue_em'] = _utc_para_brt(entregue) if entregue else None
            m = r['manifesto'] or {}
            cur.execute("""
                INSERT INTO pgr_eventos (
                    dia, placa, tipo_veiculo, tipo_operacao, ini, fim, registros,
                    vel_max, vel_sustentada, sustentado, cidade, uf, endereco,
                    latitude, longitude, situacao_carga, manifesto, tomador,
                    origem, destino, motorista, entregue_em,
                    placa_cavalo, placa_carreta, apurado_em
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
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
                    placa_cavalo = EXCLUDED.placa_cavalo,
                    placa_carreta = EXCLUDED.placa_carreta,
                    apurado_em = NOW()
            """, (dia, placa, cad.get('tipo_veiculo'), m.get('tipo_operacao'),
                  r['ini'], r['fim'], r['registros'], r['vel_max'], r['vel_sustentada'],
                  r['sustentado'], r['cidade'], r['uf'], r['endereco'],
                  r['latitude'], r['longitude'], r['situacao_carga'],
                  _cortar(m.get('manifesto'), 20), _cortar(m.get('tomador'), 120),
                  _cortar(m.get('origem'), 60), _cortar(m.get('destino'), 60),
                  _cortar(m.get('motorista'), 120), r.get('entregue_em'),
                  m.get('placa_cavalo'), m.get('placa_carreta')))
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


_COLS_EVENTO = ('placa', 'tipo_veiculo', 'tipo_operacao', 'ini', 'fim', 'registros',
                'vel_max', 'vel_sustentada', 'sustentado', 'cidade', 'uf', 'endereco',
                'situacao_carga', 'manifesto', 'tomador', 'origem', 'destino', 'motorista',
                'entregue_em', 'placa_cavalo', 'placa_carreta')

_SELECT_EVENTO = """
    SELECT placa, tipo_veiculo, tipo_operacao, ini, fim, registros, vel_max,
           vel_sustentada, sustentado, cidade, uf, endereco, situacao_carga,
           manifesto, tomador, origem, destino, motorista, entregue_em,
           placa_cavalo, placa_carreta
    FROM pgr_eventos
"""


def _grupos_de_viagem(eventos):
    """placa -> chave da viagem, quando cavalo e carreta rodaram o MESMO servico.

    A prova de que estao juntos e o MANIFESTO igual no mesmo dia — nao a
    proximidade das placas nem o horario. Onde o manifesto nao casou
    ("nao confirmado") nao funde: sem prova de que estao na mesma viagem,
    juntar seria o mesmo pecado de dizer "carregado" sem prova.

    Uma placa que aparece em dois manifestos agrupados no mesmo dia fica com o
    de maior peso (mais registros), para nao partir a linha dela em duas.
    """
    placas_por_manif, peso = {}, {}
    for e in eventos:
        m = e.get('manifesto')
        if not m:
            continue
        placas_por_manif.setdefault(m, set()).add(e['placa'])
        peso[(e['placa'], m)] = peso.get((e['placa'], m), 0) + (e['registros'] or 0)

    duplas = {m: p for m, p in placas_por_manif.items() if len(p) > 1}
    chave = {}
    for m, placas_ in duplas.items():
        for p in placas_:
            atual = chave.get(p)
            if atual is None or peso[(p, m)] > peso[(p, atual)]:
                chave[p] = m
    return chave


def _registros_sem_duplicar(eps):
    """Registros de uma viagem rastreada por dois aparelhos.

    `eps`: [(ini, fim, registros)] das duas placas. Episodios que se SOBREPOEM
    no tempo sao o mesmo trecho visto duas vezes e contam uma vez (o maior dos
    dois); episodios que nao se sobrepoem sao trechos diferentes e somam — foi
    o caso de Joanopolis e Porto Ferreira, que so a carreta pegou.

    O maior-dos-dois e piso, nao media: dois aparelhos amostrando o mesmo
    trecho viram entre max(a,b) e a+b momentos distintos. Mesmo criterio do
    `pico_exibido` — erra sempre para baixo.
    """
    if not eps:
        return 0
    total, cluster_fim, cluster_max = 0, None, 0
    for i, f, n in sorted(eps):
        if cluster_fim is not None and i <= cluster_fim:
            cluster_fim = max(cluster_fim, f)
            cluster_max = max(cluster_max, n or 0)
        else:
            total += cluster_max
            cluster_fim, cluster_max = f, (n or 0)
    return total + cluster_max


def _agregar_por_placa(eventos, cobertura):
    """Episodios de UM dia -> uma linha por veiculo (ou por VIAGEM, quando
    cavalo e carreta rastrearam a mesma)."""
    viagem_de = _grupos_de_viagem(eventos)
    por_chave = {}
    for e in eventos:
        chave = ('v', viagem_de[e['placa']]) if e['placa'] in viagem_de else ('p', e['placa'])
        p = por_chave.setdefault(chave, {
            'placas': [], 'tipos': [], 'tipo_veiculo': e['tipo_veiculo'],
            'tipo_operacao': e['tipo_operacao'], 'registros': 0, 'pico': 0,
            'sustentado': False, 'cidades': [], 'situacoes': [], 'episodios': [],
            'tomador': None, 'origem': None, 'destino': None,
            'motorista': None, 'manifesto': None, 'entregue_em': None,
            'placa_cavalo': None, 'placa_carreta': None, '_eps': [],
        })
        if e['placa'] not in p['placas']:
            p['placas'].append(e['placa'])
            if e['tipo_veiculo'] and e['tipo_veiculo'] not in p['tipos']:
                p['tipos'].append(e['tipo_veiculo'])
        # O contexto vem de um episodio que TENHA manifesto, nao do primeiro em
        # ordem cronologica: numa placa "parcial" o primeiro episodio do dia
        # costuma ser o vazio, e a linha saia com "-" mesmo havendo carga
        # provada no resto do dia.
        if e['tomador'] and not p['tomador']:
            p.update({k: e[k] for k in
                      ('tomador', 'origem', 'destino', 'motorista', 'manifesto',
                       'entregue_em', 'placa_cavalo', 'placa_carreta')})
        if e['tipo_operacao'] and not p['tipo_operacao']:
            p['tipo_operacao'] = e['tipo_operacao']
        p['pico'] = max(p['pico'], pico_exibido(e['vel_max'], e['vel_sustentada']))
        p['sustentado'] = p['sustentado'] or bool(e['sustentado'])
        p['situacoes'].append(e['situacao_carga'])
        p['_eps'].append((e['ini'], e['fim'], e['registros']))
        cid = f"{e['cidade']}/{e['uf']}" if e['cidade'] and e['uf'] else (e['cidade'] or '')
        if cid and cid not in p['cidades']:
            p['cidades'].append(cid)
        p['episodios'].append({
            'placa': e['placa'],
            'ini': e['ini'].strftime('%H:%M') if e['ini'] else None,
            'fim': e['fim'].strftime('%H:%M') if e['fim'] else None,
            'registros': e['registros'], 'vel_max': e['vel_max'],
            'vel_sustentada': e['vel_sustentada'],
            'pico': pico_exibido(e['vel_max'], e['vel_sustentada']),
            'sustentado': e['sustentado'], 'cidade': e['cidade'], 'uf': e['uf'],
            'endereco': e['endereco'], 'situacao_carga': e['situacao_carga'],
        })

    linhas = []
    for p in por_chave.values():
        fundida = len(p['placas']) > 1
        p['placa'] = ' + '.join(p['placas'])
        p['fundida'] = fundida
        # Numa viagem rastreada pelos dois, somar daria um numero inflado: o
        # veiculo de frota (dois rastreadores) pareceria sempre pior que o
        # agregado (um so), para o mesmo comportamento.
        p['registros'] = (_registros_sem_duplicar(p['_eps']) if fundida
                          else sum(n or 0 for _, _, n in p['_eps']))
        if fundida:
            p['tipo_veiculo'] = '+'.join(p['tipos']) if p['tipos'] else None
        del p['_eps']
        p['situacao_carga'] = _situacao_consolidada(p['situacoes'])
        p['situacao_rotulo'] = _SITUACAO_ROTULO.get(p['situacao_carga'], p['situacao_carga'])
        # Cobertura e por placa; na linha fundida vale a do primeiro rastreador.
        p['cobertura'] = cobertura.get(p['placas'][0], {})
        p['tomador'] = nome_curto(p['tomador'])
        p['motorista'] = nome_pessoa(p['motorista'])
        p['origem'] = cidade_uf_titulo(p['origem'])
        p['destino'] = cidade_uf_titulo(p['destino'])
        # `entregue_em` sai do banco como datetime. Quem consome precisa so do
        # "09/08", e cada consumidor formatando por conta propria ja causou um
        # bug em cada ponta: a imagem fatiava o datetime (TypeError) e o JS
        # fatiava o que o jsonify vira. Sai daqui pronto.
        q = p['entregue_em']
        p['entregue_em'] = q.isoformat(timespec='seconds') if q else None
        p['entregue_em_br'] = q.strftime('%d/%m') if q else None
        del p['situacoes']
        linhas.append(p)
    # Por RECORRENCIA, nao por gravidade - e a ordem do layout aprovado, e a
    # que serve para cobrar: 81% dos episodios sao pico isolado, entao quem
    # tocou 117 uma vez e ruido e quem fez 13 registros e conduta.
    linhas.sort(key=lambda x: (-x['registros'], -x['pico'], x['placa']))
    return linhas


def _totais(linhas, n_monitoradas):
    """No periodo, `veiculos` precisa ser DISTINTO: somar a contagem de cada dia
    daria mais caminhoes do que a frota tem."""
    return {
        'veiculos': len({l['placa'] for l in linhas}),   # linha = veículo ou viagem
        'registros': sum(l['registros'] for l in linhas),
        'pico': max((l['pico'] for l in linhas), default=0),
        'sustentados': sum(1 for l in linhas if l['sustentado']),
        'com_carga': sum(1 for l in linhas if l['situacao_carga'] in ('carregado', 'parcial')),
        'frota': len({l['placa'] for l in linhas if (l['tipo_operacao'] or '').upper() == 'FROTA'}),
        'agregado': len({l['placa'] for l in linhas if (l['tipo_operacao'] or '').upper() == 'AGREGADO'}),
        'placas_monitoradas': n_monitoradas,
    }


def _cobertura_do_dia(cur, dia):
    cur.execute("""
        SELECT placa, posicoes, maior_gap_min, maior_gap_mov_min, minutos_sem_sinal_mov
        FROM pgr_cobertura WHERE dia = %s
    """, (dia,))
    return {r[0]: {'posicoes': r[1], 'maior_gap_min': r[2],
                   'maior_gap_mov_min': r[3], 'minutos_sem_sinal_mov': r[4]}
            for r in cur.fetchall()}


def _sem_cobertura(cobertura):
    """Placa que nao aparece le como 'comportou-se bem'; sem este bloco nao da
    para distinguir isso de 'nao sabemos'."""
    return sorted(
        ({'placa': pl, **c} for pl, c in cobertura.items() if cobertura_insuficiente(c)),
        key=lambda x: -(x['maior_gap_mov_min'] or 0))


def listar_dia(cur, dia):
    """Payload de UM dia: uma linha por placa + cobertura.

    O grao gravado e o EPISODIO; a linha do relatorio e a agregacao - mesmo
    padrao do resto do app (grao CTRB -> agrega).
    """
    cur.execute(_SELECT_EVENTO + ' WHERE dia = %s ORDER BY placa, ini', (dia,))
    eventos = [dict(zip(_COLS_EVENTO, r)) for r in cur.fetchall()]
    cobertura = _cobertura_do_dia(cur, dia)
    linhas = _agregar_por_placa(eventos, cobertura)
    return {
        'dia': dia.isoformat(),
        'linhas': linhas,
        'sem_cobertura': _sem_cobertura(cobertura),
        'totais': _totais(linhas, len(cobertura)),
        'limiar': LIMIAR_KMH,
    }


def meses_apurados(cur):
    """Meses que TEM dia apurado, do mais recente para o mais antigo.

    Sai de pgr_cobertura, nao de pgr_eventos: um dia sem nenhum excesso tambem
    foi apurado, e some da lista se a fonte for a tabela de eventos.
    """
    cur.execute("""
        SELECT to_char(dia, 'YYYY-MM') AS mes, COUNT(DISTINCT dia) AS dias,
               MIN(dia) AS primeiro, MAX(dia) AS ultimo
        FROM pgr_cobertura GROUP BY 1 ORDER BY 1 DESC
    """)
    return [{'mes': r[0], 'dias': r[1],
             'primeiro': r[2].isoformat(), 'ultimo': r[3].isoformat()}
            for r in cur.fetchall()]


def opcoes_filtro(cur, meses):
    """Valores que EXISTEM no periodo, para o dropdown nao oferecer vazio.

    Sai dos dados apurados, nao do cadastro: oferecer os 6.645 veiculos do
    veiculos_045 daria uma lista impossivel e cheia de opcao que nao retorna
    nada.
    """
    if not meses:
        return {'cavalos': [], 'carretas': [], 'motoristas': []}
    cur.execute("""
        SELECT placa, tipo_veiculo, placa_cavalo, placa_carreta, motorista
        FROM pgr_eventos WHERE to_char(dia, 'YYYY-MM') = ANY(%s)
    """, (list(meses),))
    # Uma entrada por VEÍCULO (chave Mercosul), senão o mesmo aparecia duas vezes (HIF2439 e
    # HIF2E39) e escolher uma perderia os eventos da outra. O que se MOSTRA é a placa real:
    # a do GPS primeiro (é o aparelho no caminhão), senão a do manifesto — nunca a chave
    # convertida, que é uma placa que não existe (03/10/2026). O filtro casa as duas grafias.
    cavalos, carretas, motoristas = {}, {}, {}
    linhas = cur.fetchall()
    for fonte in ('gps', 'manifesto'):
        for placa, tipo, cav, car, mot in linhas:
            t = (tipo or '').upper()
            if fonte == 'gps':
                if placa and t == 'CAVALO':
                    cavalos.setdefault(placas.mercosul(placa), placas.limpar(placa))
                elif placa and t in ('CARRETA', 'TRUCK'):
                    carretas.setdefault(placas.mercosul(placa), placas.limpar(placa))
                if mot:
                    motoristas[mot] = nome_pessoa(mot)
            else:
                if cav:
                    cavalos.setdefault(placas.mercosul(cav), placas.limpar(cav))
                if car:
                    carretas.setdefault(placas.mercosul(car), placas.limpar(car))
    return {
        'cavalos': sorted(cavalos.values()),
        'carretas': sorted(carretas.values()),
        'motoristas': [{'valor': k, 'label': v}
                       for k, v in sorted(motoristas.items(), key=lambda x: x[1] or '')],
    }


def listar_periodo(cur, meses, cavalo=None, carreta=None, motorista=None):
    """Um bloco por DIA, cada um no mesmo formato de `listar_dia`.

    A linha continua sendo placa-no-dia: agregar o periodo inteiro numa linha
    so perderia o quando, e a lista de cidades viraria um paragrafo.

    Filtro de cavalo/carreta casa a placa do evento OU a do par no manifesto —
    o rastreador costuma estar na carreta, entao um cavalo quase nunca e a
    placa do evento; procurar so por ela devolveria quase nada.
    """
    if not meses:
        return {'dias': [], 'totais': _totais([], 0), 'limiar': LIMIAR_KMH,
                'dias_apurados': 0, 'meses': []}

    where = ["to_char(dia, 'YYYY-MM') = ANY(%s)"]
    args = [list(meses)]
    # A placa chega normalizada em Mercosul, mas no banco convivem as duas
    # grafias (evento vem crua do GPS, par vem do manifesto). Casa contra ambas.
    if cavalo:
        where.append('(placa = ANY(%s) OR placa_cavalo = ANY(%s))')
        g = placas.grafias(cavalo)
        args += [g, g]
    if carreta:
        where.append('(placa = ANY(%s) OR placa_carreta = ANY(%s))')
        g = placas.grafias(carreta)
        args += [g, g]
    if motorista:
        # Trecho, não igualdade: o filtro da tela oferece o nome CURTO
        # ("Pedro Henrique de S.") e o banco guarda o do manifesto inteiro
        # ("PEDRO HENRIQUE DE S. FIGUEIREDO"). ILIKE também deixa quem digita
        # só o primeiro nome achar a pessoa.
        where.append('motorista ILIKE %s')
        args.append(f'%{motorista.strip()}%')

    cur.execute(_SELECT_EVENTO.replace('FROM pgr_eventos', ', dia FROM pgr_eventos')
                + ' WHERE ' + ' AND '.join(where) +
                ' ORDER BY dia DESC, placa, ini', args)
    por_dia = {}
    for r in cur.fetchall():
        e = dict(zip(_COLS_EVENTO, r[:-1]))
        por_dia.setdefault(r[-1], []).append(e)

    cur.execute("""SELECT DISTINCT dia FROM pgr_cobertura
                   WHERE to_char(dia, 'YYYY-MM') = ANY(%s) ORDER BY dia DESC""",
                (list(meses),))
    dias_apurados = [r[0] for r in cur.fetchall()]

    filtrando = bool(cavalo or carreta or motorista)
    blocos, todas = [], []
    for dia in dias_apurados:
        eventos = por_dia.get(dia, [])
        # Com filtro, dia sem ocorrencia daquele veiculo/motorista vira ruido:
        # seriam 30 blocos vazios. Sem filtro, o dia zerado importa - e a
        # diferenca entre "ninguem correu" e "nao sabemos".
        if filtrando and not eventos:
            continue
        cobertura = _cobertura_do_dia(cur, dia)
        linhas = _agregar_por_placa(eventos, cobertura)
        todas += linhas
        blocos.append({
            'dia': dia.isoformat(),
            'linhas': linhas,
            'sem_cobertura': [] if filtrando else _sem_cobertura(cobertura),
            'totais': _totais(linhas, len(cobertura)),
        })

    return {
        'dias': blocos,
        'totais': _totais(todas, 0),
        'dias_apurados': len(dias_apurados),
        'meses': sorted(meses),
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


# ── Reapuração avulsa (09/11/2026) ───────────────────────────────────
#
# Refaz `pgr_eventos` de dias já apurados — para levar a chave
# PGR_FONTE_EMBARQUES aos dias que já saíram pela régua antiga. NÃO envia
# WhatsApp: o envio é o passo 3 do job, e dia passado já foi enviado.
# Sem --aplicar, roda tudo numa transação desfeita e só mostra o que mudaria.
#
#     python -X utf8 pgr.py --reapurar 2026-11-08                      # simula 1 dia
#     python -X utf8 pgr.py --reapurar 2026-10-14 2026-11-08 --aplicar # grava
#
# A fonte é a da chave no ambiente: com ela desligada, reapurar só repete a
# régua antiga.

def _reapurar(d0, d1, aplicar=False):
    from collections import Counter
    import rastreamento_worker as worker
    if d1 < d0:
        d0, d1 = d1, d0
    fonte = 'Embarques' if FONTE_EMBARQUES else 'manifesto (régua antiga)'
    print(f'Reapuração {d0:%d/%m/%Y} a {d1:%d/%m/%Y} — fonte: {fonte} — '
          f'{"GRAVA" if aplicar else "simulação (nada é gravado)"}')
    cols = 'placa, ini, situacao_carga, motorista, tipo_operacao, placa_cavalo, placa_carreta'
    conn = worker._get_db()
    try:
        cur = conn.cursor()
        tot_sit, tot_mot, n = Counter(), Counter(), 0
        dia = d0
        while dia <= d1:
            cur.execute(f'SELECT {cols} FROM pgr_eventos WHERE dia = %s', (dia,))
            antes = {(r[0], r[1]): r for r in cur.fetchall()}
            apurar_dia(cur, dia)
            cur.execute(f'SELECT {cols} FROM pgr_eventos WHERE dia = %s', (dia,))
            mud_sit = mud_mot = 0
            for r in cur.fetchall():
                a = antes.get((r[0], r[1]))
                n += 1
                if a and a[2] != r[2]:
                    mud_sit += 1
                    tot_sit[(a[2], r[2])] += 1
                if a and (a[3] or '') != (r[3] or ''):
                    mud_mot += 1
                    tot_mot['sem nome' if not r[3] else 'outro motorista'] += 1
            print(f'  {dia:%d/%m}: {len(antes)} episódios; situação muda em {mud_sit}, motorista em {mud_mot}')
            dia += timedelta(days=1)
        print(f'\n{n} episódios. Situação (antes → depois): '
              + (', '.join(f'{a} → {b}: {k}' for (a, b), k in tot_sit.most_common()) or 'nada muda'))
        print('Motorista: ' + (', '.join(f'{k}: {v}' for k, v in tot_mot.items()) or 'nada muda'))
        if aplicar:
            conn.commit()
            print('Gravado.')
        else:
            conn.rollback()
            print('Simulação: nada foi gravado. Repita com --aplicar para gravar.')
    finally:
        conn.close()


if __name__ == '__main__':
    import sys
    from datetime import date as _date
    args = [a for a in sys.argv[1:] if a != '--aplicar']
    if not args or args[0] != '--reapurar' or len(args) not in (2, 3):
        sys.exit('uso: python -X utf8 pgr.py --reapurar AAAA-MM-DD [AAAA-MM-DD] [--aplicar]')
    try:
        _d0 = _date.fromisoformat(args[1])
        _d1 = _date.fromisoformat(args[2]) if len(args) == 3 else _d0
    except ValueError:
        sys.exit('Data inválida — use AAAA-MM-DD')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    _reapurar(_d0, _d1, aplicar='--aplicar' in sys.argv)
