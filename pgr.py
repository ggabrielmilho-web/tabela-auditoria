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
    """Minutos com/sem sinal e maior lacuna, para o rodapé do relatório.

    Sem isto, "zero excessos" vira falsa segurança: não dá para distinguir
    "ninguém correu" de "o worker estava fora do ar" — ainda mais depois que a
    retenção apagar as posições.
    """
    if not pontos:
        return {'posicoes': 0, 'minutos_com_sinal': 0,
                'minutos_sem_sinal': 24 * 60, 'maior_gap_min': 24 * 60}
    gaps = [(b['data'] - a['data']).total_seconds() / 60 for a, b in zip(pontos, pontos[1:])]
    # "sem sinal" = soma das lacunas acima do gap de episódio
    sem = sum(g for g in gaps if g > GAP_EPISODIO_MIN)
    total = (pontos[-1]['data'] - pontos[0]['data']).total_seconds() / 60
    return {
        'posicoes': len(pontos),
        'minutos_com_sinal': int(round(total - sem)),
        'minutos_sem_sinal': int(round(sem + (24 * 60 - total))),
        'maior_gap_min': int(round(max(gaps))) if gaps else 0,
    }


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
                                       minutos_sem_sinal, maior_gap_min, apurado_em)
            VALUES (%s,%s,%s,%s,%s,%s,NOW())
            ON CONFLICT (dia, placa) DO UPDATE SET
                posicoes = EXCLUDED.posicoes,
                minutos_com_sinal = EXCLUDED.minutos_com_sinal,
                minutos_sem_sinal = EXCLUDED.minutos_sem_sinal,
                maior_gap_min = EXCLUDED.maior_gap_min,
                apurado_em = NOW()
        """, (dia, placa, c['posicoes'], c['minutos_com_sinal'],
              c['minutos_sem_sinal'], c['maior_gap_min']))

    _logger.info(f'PGR {dia:%d/%m/%Y}: {n_ev} episódios em {len(por_placa)} placas')
    return n_ev
