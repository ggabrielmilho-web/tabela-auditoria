"""
Worker em thread daemon — Roda a cada 60s.

Por ciclo:
  1. Busca última posição de todos os veículos (3S ou simulador)
  2. UPSERT em embarques_posicoes_atuais + INSERT em embarques_posicoes_historico
  3. Pra cada carga Aberta/Em rota com mapeamento:
     - SAÍDA DA ORIGEM (Aberta → Em rota auto)
     - CHEGADA NA CIDADE DO DESTINO (marca no_local_desde)
     - SAÍDA DO DESTINO (= entrega automática)
     - RECÁLCULO ORS (se passou 30min ou divergiu >10km da rota)
  4. Consolidação diária placa+dia e job de retenção (1×/dia, NESTA ordem)
"""

import os
import time
import logging
import threading
from datetime import datetime, timedelta
from math import radians, sin, cos, asin, sqrt
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

import tres_s_client
import geocoding
import placas

load_dotenv()

INTERVALO_SEG = int(os.getenv('RASTREAMENTO_INTERVALO', '60'))
CICLOS_CONFIRMACAO = int(os.getenv('RASTREAMENTO_CICLOS_CONFIRMACAO', '3'))
RAIO_CONFIRMACAO_KM = float(os.getenv('RASTREAMENTO_RAIO_KM', '5'))
DESVIO_MAX_KM = float(os.getenv('RASTREAMENTO_DESVIO_KM', '10'))
RETENCAO_DIAS = int(os.getenv('RASTREAMENTO_RETENCAO_DIAS', '30'))
RECALCULO_INTERVALO_MIN = int(os.getenv('RASTREAMENTO_RECALCULO_MIN', '30'))
# Status 'No destino': na cidade da descarga há >= 60min E parado agora (vel <= 3 km/h).
CHEGADA_MIN_PARADO = 60   # minutos na cidade do destino p/ promover a 'No destino' (fixo)
PARADO_KMH = 3            # velocidade <= isso conta como parado (espelha _kpi_ao_vivo)
# Saída por DISTÂNCIA (o 3S mente o nome da cidade — etiqueta "Uberlândia" a 113 km!).
# Origem (pátio/base): raio menor. Destino (pode ser grande centro): raio alto p/ não bugar.
RAIO_SAIDA_ORIGEM_KM = float(os.getenv('RASTREAMENTO_RAIO_SAIDA_ORIGEM', '30'))
RAIO_SAIDA_DESTINO_KM = float(os.getenv('RASTREAMENTO_RAIO_SAIDA_DESTINO', '60'))
# "Carreta sem sinal recente": posição da carreta mais velha que isso → a detecção de
# CHEGADA/"No destino" passa a olhar o cavalo (vivo). Não é regra de chegada; só decide
# qual posição está utilizável. O fechamento continua exigindo a carreta fresca.
FRESCOR_H = float(os.getenv('RASTREAMENTO_FRESCOR_H', '12'))
# Raio (km) que conta como "dentro da cidade do destino". Chegada/No destino passam a ser
# decididas por POSIÇÃO (distância ao centroide), não pelo nome que o 3S reporta (mente a
# 100+ km). Mesmo env var lido no server.py (corte do mapa) p/ manter os dois coerentes.
RAIO_CHEGADA_DESTINO_KM = float(os.getenv('RASTREAMENTO_RAIO_CHEGADA_DESTINO', '20'))
# Teto de km num dia para a consolidação diária. Com dois motoristas revezando, um
# cavalo faz ~1.200 km/dia; acima de 2.500 é troca de rastreador ou leitura suja, não
# viagem. Serve de guarda, não de critério — o valor cru fica gravado do mesmo jeito.
KM_DIA_IMPLAUSIVEL = float(os.getenv('RASTREAMENTO_KM_DIA_MAX', '2500'))

_running = False
_thread = None
_ultima_retencao = None
_logger = logging.getLogger('rastreamento_worker')


def _get_db():
    return psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        port=os.getenv('DB_PORT', '5432'),
        dbname=os.getenv('DB_NAME', 'postgres'),
        user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD', ''),
    )


def _placa_tracking(cavalo_placa, carreta1_placa, carreta2_placa, cur, apenas_carreta=False):
    """Retorna a 1ª placa com GPS mapeado em embarques_veiculos_rastreio.
    Ordem: carreta1 → cavalo → carreta2 — o rastreador costuma estar na carreta,
    que segue a carga inteira (o cavalo pode trocar no meio do caminho).
    Retorna None se nenhuma das placas tem rastreio.

    apenas_carreta=True (carga 'Desengatada'): ignora o cavalo — ele foi liberado e
    pode já estar em outra viagem; rastrear o cavalo poluiria o trajeto/disparia falsa
    saída do destino. Só a carreta (que ficou carregada no destino) é considerada."""
    candidatas = (carreta1_placa, carreta2_placa) if apenas_carreta \
        else (carreta1_placa, cavalo_placa, carreta2_placa)
    for placa in candidatas:
        p = (placa or '').strip().upper()
        if not p:
            continue
        # Casa pelas DUAS grafias e devolve a que a 3S usa.
        #
        # O sync grava a placa CRUA da 3S (42 das 94 vêm na grafia antiga) e a
        # carga pode estar em Mercosul — o robô normaliza. Com igualdade exata,
        # `CZB4J27` (carga) não encontrava `CZB4927` (3S) e a carga ficava
        # invisível para o rastreamento: 11 de 11 cargas "sem rastreio" eram na
        # verdade rastreáveis.
        #
        # Devolver a grafia DA 3S (e não a da carga) é o que faz o resto do
        # worker funcionar sem alteração: posições, KPIs e detecção buscam em
        # embarques_posicoes_*, que também está na grafia da 3S.
        cur.execute("SELECT placa FROM embarques_veiculos_rastreio WHERE placa = ANY(%s)",
                    (placas.grafias(p),))
        r = cur.fetchone()
        if r:
            return r[0]
    return None


def _pos_fresca(cur, placa):
    """Posição atual da placa em embarques_posicoes_atuais SÓ se for recente
    (NOW - data_posicao <= FRESCOR_H). Retorna (lat,lng,cidade,uf,data,vel) ou None.
    Usado pra decidir se a carreta tem sinal utilizável ou se caímos no cavalo."""
    p = (placa or '').strip().upper()
    if not p:
        return None
    # ANY(grafias): a posição está na grafia da 3S e `p` vem da carga, que pode
    # estar em Mercosul (ver _placa_tracking).
    cur.execute("""
        SELECT latitude, longitude, cidade, uf, data_posicao, velocidade
        FROM embarques_posicoes_atuais
        WHERE placa = ANY(%s) AND data_posicao >= (NOW() AT TIME ZONE 'UTC') - %s::interval
        ORDER BY data_posicao DESC LIMIT 1
    """, (placas.grafias(p), f'{FRESCOR_H} hours'))
    return cur.fetchone()


def _esteve_no_destino(cur, placa, centroide_dest, raio_km, desde):
    """True se a placa teve ALGUMA posição dentro de raio_km do destino desde `desde`.
    Confirma que a carreta realmente passou pelo destino antes de aceitar uma "saída"
    como entrega — protege contra fechar a carga quando a carreta reaparece longe sem
    nunca ter chegado (ex.: chegada detectada pelo cavalo; carreta volta em outra cidade).
    Sem centroide do destino → True (degrada p/ o comportamento de hoje, sem surpresa)."""
    p = (placa or '').strip().upper()
    if not p or not centroide_dest or centroide_dest[0] is None:
        return True
    dlat, dlng = centroide_dest
    # Bounding-box generosa (graus) p/ limitar a varredura; refina com km_entre.
    m_lat = raio_km / 111.0
    m_lng = raio_km / 90.0   # folga p/ cos(lat) no Brasil (~0.9–0.96)
    cur.execute("""
        SELECT latitude, longitude FROM embarques_posicoes_historico
        WHERE placa = ANY(%s) AND data_posicao >= %s
          AND latitude BETWEEN %s AND %s AND longitude BETWEEN %s AND %s
    """, (placas.grafias(p), desde, dlat - m_lat, dlat + m_lat, dlng - m_lng, dlng + m_lng))
    for la, ln in cur.fetchall():
        if la is None or ln is None:
            continue
        d = geocoding.km_entre(float(la), float(ln), dlat, dlng)
        if d is not None and d <= raio_km:
            return True
    return False


# ── Conversões ────────────────────────────────────────────────────────

def _str_to_bool_ignicao(s):
    if isinstance(s, bool):
        return s
    return str(s or '').strip().lower() in ('ligada', 'true', '1', 'on', 'sim')


def _str_to_bool_bloqueio(s):
    if isinstance(s, bool):
        return s
    return str(s or '').strip().lower() in ('bloqueado', 'true', '1')


def _safe_float(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(',', '.'))
    except (ValueError, TypeError):
        return None


def _safe_int(v, default=None):
    if v is None:
        return default
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return default


def _parse_data(d):
    if isinstance(d, datetime):
        return d
    if not d:
        return datetime.utcnow()
    s = str(d).replace('Z', '')
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        try:
            return datetime.strptime(s[:19], '%Y-%m-%dT%H:%M:%S')
        except ValueError:
            return datetime.utcnow()


# ── Persistência de posição ──────────────────────────────────────────

def _persistir_posicoes(cur, posicoes_raw):
    """UPSERT em posicoes_atuais + INSERT em posicoes_historico."""
    for p in posicoes_raw:
        placa = (p.get('placa') or '').strip().upper()
        if not placa:
            continue
        id_veiculo_3s = _safe_int(p.get('idVeiculo'))
        lat = _safe_float(p.get('latitude'))
        lng = _safe_float(p.get('longitude'))
        if id_veiculo_3s is None or lat is None or lng is None:
            continue

        data_pos = _parse_data(p.get('data'))
        velocidade = _safe_int(p.get('velocidade'), 0)
        ignicao = _str_to_bool_ignicao(p.get('ignicao'))
        direcao = (p.get('direcao') or '')[:20]
        uf = (p.get('uf') or '')[:2]
        cidade = (p.get('cidade') or '')[:120]
        bairro = (p.get('bairro') or '')[:120] or None
        endereco = (p.get('endereco') or '')[:200] or None
        bloqueio = _str_to_bool_bloqueio(p.get('bloqueio'))
        odometer = _safe_int(p.get('odometer'))

        cur.execute("""
            INSERT INTO embarques_posicoes_atuais (
                placa, id_veiculo_3s, data_posicao, latitude, longitude,
                velocidade, ignicao, direcao, uf, cidade, bairro, endereco,
                bloqueio, odometer, atualizado_em
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ON CONFLICT (placa) DO UPDATE SET
                id_veiculo_3s = EXCLUDED.id_veiculo_3s,
                data_posicao = EXCLUDED.data_posicao,
                latitude = EXCLUDED.latitude,
                longitude = EXCLUDED.longitude,
                velocidade = EXCLUDED.velocidade,
                ignicao = EXCLUDED.ignicao,
                direcao = EXCLUDED.direcao,
                uf = EXCLUDED.uf,
                cidade = EXCLUDED.cidade,
                bairro = EXCLUDED.bairro,
                endereco = EXCLUDED.endereco,
                bloqueio = EXCLUDED.bloqueio,
                odometer = EXCLUDED.odometer,
                atualizado_em = NOW()
        """, (placa, id_veiculo_3s, data_pos, lat, lng,
              velocidade, ignicao, direcao, uf, cidade, bairro, endereco,
              bloqueio, odometer))

        # Auto-cura de troca de placa (antiga -> Mercosul): remove a posição da placa
        # antiga do MESMO veículo (identidade = id_veiculo_3s), senão o mapa mostra 2×.
        cur.execute(
            "DELETE FROM embarques_posicoes_atuais WHERE id_veiculo_3s=%s AND placa<>%s",
            (id_veiculo_3s, placa))

        cur.execute("""
            INSERT INTO embarques_posicoes_historico (
                placa, id_veiculo_3s, data_posicao, latitude, longitude,
                velocidade, ignicao, uf, cidade, endereco, odometer
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (placa, data_posicao) DO NOTHING
        """, (placa, id_veiculo_3s, data_pos, lat, lng,
              velocidade, ignicao, uf, cidade, endereco, odometer))


# ── Detecção (cidade + raio + N ciclos) ─────────────────────────────

def _n_ciclos_fora_cidade(cur, placa, cidade_referencia, n):
    """True se as últimas N posições da placa estão FORA da cidade de referência."""
    cur.execute("""
        SELECT cidade FROM embarques_posicoes_historico
        WHERE placa = ANY(%s)
        ORDER BY data_posicao DESC
        LIMIT %s
    """, (placas.grafias(placa), n))
    rows = cur.fetchall()
    if len(rows) < n:
        return False
    cidade_norm = geocoding.normalizar_cidade(cidade_referencia)
    return all(geocoding.normalizar_cidade(r[0]) != cidade_norm for r in rows)


def _n_ciclos_fora_raio(cur, placa, centroide, raio_km, n):
    """True se as últimas N posições da placa estão a > raio_km do centroide.
    Confirmação por DISTÂNCIA (não por nome, que o 3S erra)."""
    cur.execute("""
        SELECT latitude, longitude FROM embarques_posicoes_historico
        WHERE placa = ANY(%s) ORDER BY data_posicao DESC LIMIT %s
    """, (placas.grafias(placa), n))
    rows = cur.fetchall()
    if len(rows) < n:
        return False
    for la, ln in rows:
        if la is None or ln is None:
            return False
        d = geocoding.km_entre(float(la), float(ln), centroide[0], centroide[1])
        if d is None or d <= raio_km:
            return False
    return True


def _saiu_da_cidade(cur, placa, pos_cidade, cidade_ref, uf_ref, centroide_ref, raio_saida_km):
    """Confirma SAÍDA da cidade de referência, robusto ao 3S mentir o nome.
    Saiu se: (nome mudou E > RAIO_CONFIRMACAO) OU (distância > raio_saida_km).
    Confirma por N ciclos FORA do raio de confirmação. raio_saida alto não buga em
    grande centro e cobre o caso do 3S etiquetar a cidade errada longe do centro."""
    if not centroide_ref or centroide_ref[0] is None:
        # sem centroide, cai no comportamento antigo (nome)
        if geocoding.normalizar_cidade(pos_cidade) == geocoding.normalizar_cidade(cidade_ref):
            return False
        return _n_ciclos_fora_cidade(cur, placa, cidade_ref, CICLOS_CONFIRMACAO)
    cur.execute("SELECT latitude, longitude FROM embarques_posicoes_atuais "
                "WHERE placa = ANY(%s) ORDER BY data_posicao DESC LIMIT 1",
                (placas.grafias(placa),))
    r = cur.fetchone()
    if not r or r[0] is None:
        return False
    d = geocoding.km_entre(float(r[0]), float(r[1]), centroide_ref[0], centroide_ref[1])
    if d is None:
        return False
    nome_mudou = geocoding.normalizar_cidade(pos_cidade) != geocoding.normalizar_cidade(cidade_ref)
    saiu = (nome_mudou and d > RAIO_CONFIRMACAO_KM) or (d > raio_saida_km)
    if not saiu:
        return False
    return _n_ciclos_fora_raio(cur, placa, centroide_ref, RAIO_CONFIRMACAO_KM, CICLOS_CONFIRMACAO)


def _mesma_cidade(pos_cidade, pos_uf, cidade_ref, uf_ref):
    if not pos_cidade or not cidade_ref:
        return False
    if (pos_uf or '').upper() != (uf_ref or '').upper():
        return False
    return geocoding.normalizar_cidade(pos_cidade) == geocoding.normalizar_cidade(cidade_ref)


# ── Polyline + distância ponto-rota ─────────────────────────────────

def _decodificar_polyline(polyline_str, precision=5):
    coords = []
    index = 0
    lat = 0
    lng = 0
    length = len(polyline_str)
    factor = 10 ** precision
    while index < length:
        shift = 0; result = 0
        while True:
            b = ord(polyline_str[index]) - 63
            index += 1
            result |= (b & 0x1f) << shift
            shift += 5
            if b < 0x20:
                break
        lat += ~(result >> 1) if (result & 1) else (result >> 1)
        shift = 0; result = 0
        while True:
            b = ord(polyline_str[index]) - 63
            index += 1
            result |= (b & 0x1f) << shift
            shift += 5
            if b < 0x20:
                break
        lng += ~(result >> 1) if (result & 1) else (result >> 1)
        coords.append((lat / factor, lng / factor))
    return coords


def _km_min_ponto_rota(pos_lat, pos_lng, polyline_str):
    """Distância mínima da posição a qualquer ponto da rota. Amostra a polyline."""
    if not polyline_str:
        return None
    try:
        pts = _decodificar_polyline(polyline_str)
    except Exception:
        return None
    if not pts:
        return None
    # Amostra: pula pontos pra agilizar (rotas longas têm milhares)
    step = max(1, len(pts) // 500)
    minimo = None
    for plat, plng in pts[::step]:
        d = geocoding.km_entre(pos_lat, pos_lng, plat, plng)
        if d is not None and (minimo is None or d < minimo):
            minimo = d
    return minimo


# ── Consolidação KPI ─────────────────────────────────────────────────

def _consolidar_kpi(cur, carga_id, final=False):
    """Soma segmentos do histórico + agrega vel/tempo. Grava em embarques_cargas_rastreio_kpi."""
    cur.execute("""
        SELECT cavalo_placa, carreta1_placa, carreta2_placa,
               data_carregamento, data_saida_real, data_conclusao, inicio_viagem,
               origem_latitude, origem_longitude, no_local_desde
        FROM embarques_cargas WHERE id=%s
    """, (carga_id,))
    r = cur.fetchone()
    if not r:
        return
    (cavalo_placa, carreta1_placa, carreta2_placa, data_carreg, data_saida_real,
     data_conclusao, inicio_viagem, origem_lat, origem_lng, no_local_desde) = r
    # Segue a mesma placa de rastreio usada na detecção (carreta primeiro)
    placa = _placa_tracking(cavalo_placa, carreta1_placa, carreta2_placa, cur)
    if not placa:
        return
    # Último destino (maior ordem) — usado só no corte de fallback abaixo.
    cur.execute("""
        SELECT latitude, longitude FROM embarques_cargas_destinos
        WHERE carga_id=%s ORDER BY ordem DESC LIMIT 1
    """, (carga_id,))
    _rd = cur.fetchone()
    dest_lat, dest_lng = (_rd[0], _rd[1]) if _rd else (None, None)
    # Janela da viagem: [saída da origem, CHEGADA ao destino].
    # Busca LARGO por DATA (data_carregamento c/ folga de fuso) — NÃO por inicio_viagem,
    # que é definido por nome de cidade e o 3S mente (etiqueta a origem a 100+ km). O
    # recorte por distância (abaixo) define o começo real perto do pátio.
    # Fim na CHEGADA (no_local_desde) — não conta o pós-entrega (destino → cidade seguinte).
    from datetime import time as _t
    if data_carreg:
        base = data_carreg if isinstance(data_carreg, datetime) else datetime.combine(data_carreg, _t())
        inicio = base - timedelta(hours=12)
    else:
        inicio = inicio_viagem or data_saida_real or (datetime.utcnow() - timedelta(days=15))
    fim = no_local_desde or data_conclusao or datetime.utcnow()

    def _pontos(p):
        cur.execute("""
            SELECT latitude, longitude, velocidade, data_posicao
            FROM embarques_posicoes_historico
            WHERE placa = ANY(%s) AND data_posicao BETWEEN %s AND %s
            ORDER BY data_posicao
        """, (placas.grafias(p), inicio, fim))
        return cur.fetchall()

    rows = _pontos(placa)
    # Carreta sem trilha na janela (rastreador mudo): consolida pelo CAVALO, senão o KPI
    # final gravaria distância nula. Hoje isso não aparece na tela (o endpoint ignora o
    # persistido quando cai no cavalo), mas deixa lixo no banco.
    if len(rows) < 2 and cavalo_placa and (cavalo_placa or '').strip().upper() != placa:
        alt = _pontos((cavalo_placa or '').strip().upper())
        if len(alt) >= 2:
            _logger.info(f'[Carga {carga_id}] KPI: carreta {placa} sem trilha — consolidando pelo cavalo {cavalo_placa}')
            placa, rows = (cavalo_placa or '').strip().upper(), alt
    # Recorta o trecho PRÉ-origem (caminhão rodando antes do lançamento)
    if rows and origem_lat is not None and origem_lng is not None:
        coords = [(float(la), float(ln)) for (la, ln, _v, _d) in rows]
        idx = geocoding.indice_saida_origem(coords, float(origem_lat), float(origem_lng))
        rows = rows[idx:]

    # ── FALLBACK: chegada NÃO detectada (no_local_desde nulo). Aí a janela por data vai
    # até a conclusão/agora e soma a viagem de VOLTA — a carga C-2026-000261 fechou com
    # 1588 km numa rota de 928 porque entregou em Guarulhos e voltou pra Goiás. Corta na
    # chegada por POSIÇÃO, exatamente como o mapa já faz em _indice_chegada_destino.
    # Com no_local_desde preenchido (o caso normal) NADA muda aqui.
    if rows and no_local_desde is None and dest_lat is not None:
        # Guarda: destino perto da origem (ida-e-volta curta) — o corte poderia cair no
        # começo do trajeto e encolher a viagem. Degrada pro comportamento de hoje.
        d_od = geocoding.km_entre(origem_lat, origem_lng, dest_lat, dest_lng) \
            if origem_lat is not None else None
        if d_od is None or d_od > RAIO_CHEGADA_DESTINO_KM * 2:
            i_cheg = geocoding.indice_chegada_destino(
                rows, dest_lat, dest_lng,
                raio_km=RAIO_CHEGADA_DESTINO_KM,
                parado_kmh=PARADO_KMH, parado_min=CHEGADA_MIN_PARADO)
            if i_cheg is not None:
                _logger.info(f'[Carga {carga_id}/{placa}] KPI sem chegada registrada — '
                             f'cortando na chegada por posição ({len(rows)} → {i_cheg + 1} pontos)')
                rows = rows[:i_cheg + 1]
    if len(rows) < 2:
        cur.execute("""
            INSERT INTO embarques_cargas_rastreio_kpi (carga_id, placa, consolidado_em, consolidado_final)
            VALUES (%s, %s, NOW(), %s)
            ON CONFLICT (carga_id) DO UPDATE SET consolidado_em=NOW(), consolidado_final=EXCLUDED.consolidado_final
        """, (carga_id, placa, final))
        return

    total_m = 0.0
    vel_max = 0
    vel_sum = 0
    vel_n = 0
    tempo_mov = 0
    tempo_par = 0
    for i in range(len(rows) - 1):
        a_lat, a_lng, a_vel, a_data = rows[i]
        b_lat, b_lng, b_vel, b_data = rows[i + 1]
        seg_km = geocoding.km_entre(float(a_lat), float(a_lng), float(b_lat), float(b_lng))
        if seg_km is None:
            continue
        total_m += seg_km * 1000
        if a_vel is not None:
            vel_max = max(vel_max, int(a_vel))
            vel_sum += int(a_vel); vel_n += 1
        delta_s = (b_data - a_data).total_seconds()
        if (a_vel or 0) > 3:
            tempo_mov += delta_s
        else:
            tempo_par += delta_s

    vel_media = round(vel_sum / vel_n, 1) if vel_n else 0

    cur.execute("""
        INSERT INTO embarques_cargas_rastreio_kpi (
            carga_id, placa, distancia_metros, velocidade_max, velocidade_media,
            tempo_movimento_seg, tempo_parado_seg, consolidado_em, consolidado_final
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,NOW(),%s)
        ON CONFLICT (carga_id) DO UPDATE SET
            distancia_metros = EXCLUDED.distancia_metros,
            velocidade_max = EXCLUDED.velocidade_max,
            velocidade_media = EXCLUDED.velocidade_media,
            tempo_movimento_seg = EXCLUDED.tempo_movimento_seg,
            tempo_parado_seg = EXCLUDED.tempo_parado_seg,
            consolidado_em = NOW(),
            consolidado_final = EXCLUDED.consolidado_final
    """, (carga_id, placa, int(total_m), vel_max, vel_media,
          int(tempo_mov), int(tempo_par), final))


# ── Detecção por carga ───────────────────────────────────────────────

def _processar_cargas(cur):
    """Itera cargas Aberta/Em rota com ao menos 1 veículo rastreado e aplica detecção.
    Segue a carreta (carreta1 → cavalo → carreta2) — o rastreador costuma estar nela."""
    cur.execute("""
        SELECT c.id, c.status, c.cavalo_placa, c.carreta1_placa, c.carreta2_placa,
               c.origem_cidade, c.origem_uf, c.origem_latitude, c.origem_longitude,
               c.no_local_desde, c.rota_planejada_polyline, c.rota_recalculada_em,
               c.distancia_planejada_km, c.inicio_viagem, c.data_carregamento
        FROM embarques_cargas c
        WHERE c.status IN ('Aberta', 'Em rota', 'No destino', 'Desengatada')
    """)
    # O pré-filtro por EXISTS saiu de propósito: em SQL não dá para gerar a
    # grafia alternativa da placa, e ele descartava carga rastreável cuja placa
    # estava na outra grafia. Quem decide agora é _placa_tracking (devolve None
    # e o laço segue). São dezenas de cargas ativas — o custo é irrelevante.
    cargas = cur.fetchall()

    for cg in cargas:
        (carga_id, status, cavalo_placa, carreta1_placa, carreta2_placa,
         origem_cid, origem_uf, origem_lat, origem_lng,
         no_local_desde, polyline, rota_rec_em, dist_plan, inicio_viagem, data_carreg) = cg

        # Desengatada: cavalo+motorista liberados → rastreia SÓ a carreta (carregada no
        # destino). Só interessa detectar a saída da carreta = finalização automática.
        desengatada = (status == 'Desengatada')
        placa = _placa_tracking(cavalo_placa, carreta1_placa, carreta2_placa, cur,
                                apenas_carreta=desengatada)
        if not placa:
            continue

        # Detecta a saída da origem 1x e persiste (trajeto/KPIs leem daqui depois).
        # Nunca grava NULL → não recalcula nos próximos ciclos.
        iv_detectado = None   # presença REAL na origem (None = placa nunca vista lá)
        if inicio_viagem is None:
            iv_detectado = geocoding.detectar_inicio_viagem(
                placa, origem_cid, origem_uf, data_carreg, cur.connection,
                origem_lat=float(origem_lat) if origem_lat is not None else None,
                origem_lng=float(origem_lng) if origem_lng is not None else None,
            )
            iv = iv_detectado
            if iv is None:
                from datetime import time as _t
                iv = (datetime.combine(data_carreg, _t()) if data_carreg
                      else datetime.utcnow() - timedelta(days=geocoding.RECONSTRUCAO_MAX_DIAS))
            cur.execute("UPDATE embarques_cargas SET inicio_viagem=%s WHERE id=%s", (iv, carga_id))
            inicio_viagem = iv

        cur.execute("""
            SELECT latitude, longitude, cidade, uf, data_posicao, velocidade
            FROM embarques_posicoes_atuais WHERE placa = ANY(%s)
            ORDER BY data_posicao DESC LIMIT 1
        """, (placas.grafias(placa),))
        r = cur.fetchone()
        if not r:
            continue
        pos_lat, pos_lng, pos_cidade, pos_uf, pos_data, pos_vel = r
        pos_lat = float(pos_lat); pos_lng = float(pos_lng)
        pos_vel = float(pos_vel) if pos_vel is not None else None

        # Pega último destino (maior ordem)
        cur.execute("""
            SELECT cidade, uf, latitude, longitude FROM embarques_cargas_destinos
            WHERE carga_id=%s ORDER BY ordem DESC LIMIT 1
        """, (carga_id,))
        rd = cur.fetchone()
        if not rd:
            continue
        dest_cidade, dest_uf, dest_lat, dest_lng = rd
        centroide_dest = (float(dest_lat), float(dest_lng)) if dest_lat is not None else (None, None)
        centroide_origem = (float(origem_lat), float(origem_lng)) if origem_lat is not None else (None, None)

        # Frescor da placa rastreada + REFERÊNCIA DE POSIÇÃO para a detecção.
        # A carreta continua sendo a principal (ela acompanha a carga; o cavalo desengata e
        # puxa outra). Mas com a carreta MUDA — rastreador sem energia ou com defeito — a
        # detecção (saída da origem, chegada e promoção) passa a ler a posição do CAVALO,
        # senão a carga trava em 'Aberta' para sempre.
        # O FECHAMENTO não usa esta referência: continua exigindo `placa` (carreta) fresca,
        # porque o cavalo pode já ter largado a carreta e ido para outra viagem.
        # Em carga 'Desengatada' não há fallback nenhum — o cavalo foi liberado.
        placa_fresca = pos_data is not None and (datetime.utcnow() - pos_data) <= timedelta(hours=FRESCOR_H)
        placa_ref = placa
        ref_lat, ref_lng, ref_cidade, ref_vel = pos_lat, pos_lng, pos_cidade, pos_vel
        ref_fresca = placa_fresca
        ref_via_cavalo = False
        if not placa_fresca and not desengatada:
            cav = _pos_fresca(cur, cavalo_placa)
            if cav:
                placa_ref = (cavalo_placa or '').strip().upper()
                ref_lat, ref_lng = float(cav[0]), float(cav[1])
                ref_cidade = cav[2]        # só o nome é consumido (_saiu_da_cidade)
                ref_vel = float(cav[5]) if cav[5] is not None else None
                ref_fresca = True
                ref_via_cavalo = True
                _logger.info(f'[Carga {carga_id}] Carreta {placa} sem sinal recente — detecção via cavalo {placa_ref}')

        # ── SAÍDA DA ORIGEM (status Aberta → Em rota)
        # GUARDA (simétrica à de _esteve_no_destino, no fechamento): só é "saída da origem"
        # se a placa REALMENTE esteve na origem. Sem isso, "está longe da origem" era lido
        # como "saiu dela", e a viagem abria no próprio lançamento nos dois casos em que o
        # veículo nunca passou no pátio: rastreador mudo (posição congelada semanas atrás,
        # virando data_saida_real com carimbo velho) ou carreta ainda a caminho pra carregar.
        # Exige também posição fresca — dado velho não move status (igual chegada/fechamento).
        if status == 'Aberta' and ref_fresca:
            if _saiu_da_cidade(cur, placa_ref, ref_cidade, origem_cid, origem_uf, centroide_origem, RAIO_SAIDA_ORIGEM_KM):
                # A prova de presença é a própria detecção do início da viagem (última
                # posição DENTRO da origem). Ela também vira data_saida_real — melhor que o
                # horário do ciclo, que só diz quando o worker percebeu, não quando saiu.
                # No fallback pelo cavalo, `iv_detectado` é da carreta e não serve: redetecta.
                saida_real = (iv_detectado if (iv_detectado is not None and not ref_via_cavalo)
                              else None) or geocoding.detectar_inicio_viagem(
                    placa_ref, origem_cid, origem_uf, data_carreg, cur.connection,
                    origem_lat=float(origem_lat) if origem_lat is not None else None,
                    origem_lng=float(origem_lng) if origem_lng is not None else None,
                )
                if saida_real is None:
                    _logger.info(f'[Carga {carga_id}/{placa}] Longe da origem, mas a placa nunca foi vista NA origem — não abre viagem (aguarda GPS ou saída manual)')
                else:
                    # Corrige junto o inicio_viagem, que pode ter sido gravado como fallback
                    # (data_carregamento@00:00) num ciclo anterior a esta detecção.
                    cur.execute("""
                        UPDATE embarques_cargas
                        SET status='Em rota', saida_auto=TRUE, data_saida_real=%s,
                            inicio_viagem=%s, atualizado_em=NOW()
                        WHERE id=%s
                    """, (saida_real, saida_real, carga_id))
                    _logger.info(f'[Carga {carga_id}/{placa}] Saída automática da origem detectada ({saida_real})')
                    status = 'Em rota'
                    inicio_viagem = saida_real

        # ── CHEGADA NO DESTINO por POSIÇÃO (marca no_local_desde; segue 'Em rota' + ícone 📦).
        # Decidida por DISTÂNCIA + PARADO, NUNCA pelo nome — o 3S etiqueta a cidade-destino a
        # 100+ km (viu-se cravar chegada a 111 km!). A placa rastreada está dentro do raio do
        # destino, fresca e parada → é onde ela encostou p/ descarga. Vale também p/ 'Desengatada'.
        if status in ('Em rota', 'Desengatada') and no_local_desde is None \
                and centroide_dest[0] is not None and ref_fresca \
                and (ref_vel is None or ref_vel <= PARADO_KMH):
            _d_dest = geocoding.km_entre(ref_lat, ref_lng, centroide_dest[0], centroide_dest[1])
            if _d_dest is not None and _d_dest <= RAIO_CHEGADA_DESTINO_KM:
                cur.execute("UPDATE embarques_cargas SET no_local_desde=NOW(), atualizado_em=NOW() WHERE id=%s", (carga_id,))
                _logger.info(f'[Carga {carga_id}/{placa}] Chegou e parou no destino ({_d_dest:.1f} km do centro)')
                no_local_desde = datetime.utcnow()

        # ── PROMOÇÃO p/ 'No destino' (parado no raio do destino há >= CHEGADA_MIN_PARADO).
        # Também por POSIÇÃO (distância ao destino), não por nome.
        if status == 'Em rota' and no_local_desde is not None and ref_fresca \
                and centroide_dest[0] is not None \
                and (datetime.utcnow() - no_local_desde) >= timedelta(minutes=CHEGADA_MIN_PARADO) \
                and (ref_vel is None or ref_vel <= PARADO_KMH) \
                and (_d := geocoding.km_entre(ref_lat, ref_lng, centroide_dest[0], centroide_dest[1])) is not None \
                and _d <= RAIO_CHEGADA_DESTINO_KM:
            cur.execute("UPDATE embarques_cargas SET status='No destino', atualizado_em=NOW() WHERE id=%s", (carga_id,))
            _logger.info(f'[Carga {carga_id}/{placa}] Parado no destino há +{CHEGADA_MIN_PARADO}min → status "No destino"')
            status = 'No destino'

        # ── SAÍDA DO DESTINO (= entrega automática) — 'Em rota', 'No destino' ou 'Desengatada'
        # GUARDA: só finaliza com a placa rastreada (carreta) FRESCA. Carreta muda → não fecha
        # (cai no alerta/finalização manual), pra não fechar errado por posição velha ou desengate.
        if status in ('Em rota', 'No destino', 'Desengatada') and no_local_desde is not None and placa_fresca:
            if _saiu_da_cidade(cur, placa, pos_cidade, dest_cidade, dest_uf, centroide_dest, RAIO_SAIDA_DESTINO_KM):
                # GUARDA: só é "saída do destino" se a carreta REALMENTE esteve no destino.
                # Senão (carreta reaparece longe sem nunca ter chegado — ex.: chegada veio do
                # cavalo, ou ping de reconexão), não fecha; fica pro alerta/finalização manual.
                # inicio_viagem já foi garantido não-nulo no começo do loop
                if not _esteve_no_destino(cur, placa, centroide_dest, RAIO_SAIDA_DESTINO_KM, inicio_viagem):
                    _logger.info(f'[Carga {carga_id}/{placa}] Saída detectada mas a carreta nunca esteve no destino — não finaliza (aguarda confirmação/manual)')
                else:
                    cur.execute("""
                        UPDATE embarques_cargas
                        SET status='Entregue', entregue_auto=TRUE, data_conclusao=NOW(),
                            atualizado_em=NOW()
                        WHERE id=%s
                    """, (carga_id,))
                    _logger.info(f'[Carga {carga_id}/{placa}] Entrega automática detectada')
                    _consolidar_kpi(cur, carga_id, final=True)
                    continue  # Pula recálculo ORS pra carga já fechada

        # ── CÁLCULO ORS — rota planejada origem → cidades de rota → TODOS os destinos.
        # Calcula 1× quando ainda não há rota (criação ou reset/regeneração ao editar). NÃO
        # recalcula "da posição atual" (truncava a rota e soltava a origem); o "km faltando"
        # é derivado da posição sobre esta rota completa, no endpoint.
        if polyline is None and centroide_origem[0] is not None and not desengatada:
            try:
                import ors_client
                # origem + cidades de rota (ordem) + todos os destinos (ordem)
                cur.execute("SELECT latitude, longitude FROM embarques_cargas_rota WHERE carga_id=%s ORDER BY ordem", (carga_id,))
                rota_pts = cur.fetchall()
                cur.execute("SELECT latitude, longitude FROM embarques_cargas_destinos WHERE carga_id=%s ORDER BY ordem", (carga_id,))
                dest_pts = cur.fetchall()
                pontos = [{'lat': centroide_origem[0], 'lng': centroide_origem[1]}]
                for la, ln in list(rota_pts) + list(dest_pts):
                    if la is not None and ln is not None:
                        pontos.append({'lat': float(la), 'lng': float(ln)})
                if len(pontos) >= 2:
                    nova = ors_client.tracar_rota_multi(pontos)
                    cur.execute("""
                        UPDATE embarques_cargas SET
                            rota_planejada_polyline=%s,
                            distancia_planejada_km=%s,
                            duracao_estimada_min=%s,
                            rota_recalculada_em=NOW(),
                            atualizado_em=NOW()
                        WHERE id=%s
                    """, (nova['polyline'], nova['distancia_km'], nova['duracao_min'], carga_id))
                    _logger.info(f'[Carga {carga_id}/{placa}] Rota planejada ({len(pontos)} pts): {nova["distancia_km"]}km, {nova["duracao_min"]}min')
            except Exception as e:
                _logger.warning(f'[Carga {carga_id}/{placa}] Falha ao calcular ORS: {e}')


# ── Retenção ─────────────────────────────────────────────────────────

def _consolidar_dias(cur, desde=None, ate=None, refazer=False):
    """Consolida o histórico de posições no grão PLACA + DIA (dia de Brasília).

    RODA ANTES DA PURGA — a ordem não pode inverter. Purgado, o dado não volta: a 3S
    serve ~35 dias de histórico (medido em 04/09/26: 31/07 respondia, 28/07 dava 404),
    então não há segunda chance. É a mesma razão pela qual `pgr_eventos` persiste o
    resultado em vez de recalcular.

    Por que placa+dia e não carga: `embarques_cargas_rastreio_kpi` tem carga_id como PK,
    então só existe linha enquanto o caminhão está dentro de um documento. O dia no
    pátio e o deslocamento vazio — que é o que falta para medir produtividade — não têm
    carga_id e sumiam inteiros.

    Idempotente (PK placa+dia com upsert). Por padrão consolida o que ainda não tem
    linha, mais os 2 últimos dias, que continuam recebendo posições.
    """
    hoje = datetime.utcnow().date()
    d_ini = desde or (hoje - timedelta(days=RETENCAO_DIAS + 1))
    d_fim = ate or hoje

    # Dias candidatos: têm posição na janela e (ainda não foram consolidados OU são
    # recentes o bastante para ainda estar mudando).
    cond_refazer = '' if refazer else """
          AND (d.consolidado_em IS NULL OR h.dia >= %(recentes)s)"""
    cur.execute(f"""
        SELECT h.placa, h.dia FROM (
            SELECT placa, (data_posicao - INTERVAL '3 hours')::date AS dia
              FROM embarques_posicoes_historico
             WHERE data_posicao >= %(ini)s AND data_posicao < %(fim)s
             GROUP BY 1, 2
        ) h
        LEFT JOIN embarques_rastreio_dia d ON d.placa = h.placa AND d.dia = h.dia
        WHERE TRUE {cond_refazer}
        ORDER BY h.placa, h.dia
    """, {'ini': datetime.combine(d_ini, datetime.min.time()) + timedelta(hours=3),
          'fim': datetime.combine(d_fim + timedelta(days=1), datetime.min.time()) + timedelta(hours=3),
          'recentes': hoje - timedelta(days=2)})
    candidatos = cur.fetchall()
    if not candidatos:
        return 0

    # carga ativa por (placa, dia): resolvido em UMA consulta para a janela inteira.
    # Dia sem carga é o dia vazio — é o próprio dado que se quer enxergar.
    cur.execute("""
        SELECT UPPER(REPLACE(REPLACE(p, ' ', ''), '-', '')) AS placa,
               COALESCE(c.inicio_viagem, c.data_saida_real,
                        c.data_carregamento::timestamp)::date AS de,
               COALESCE(c.data_conclusao::date, CURRENT_DATE) AS ate,
               c.id
          FROM embarques_cargas c,
               LATERAL unnest(ARRAY[c.cavalo_placa, c.carreta1_placa, c.carreta2_placa]) AS p
         WHERE p IS NOT NULL AND p <> ''
           AND COALESCE(c.data_conclusao::date, CURRENT_DATE) >= %s
         ORDER BY c.id
    """, (d_ini - timedelta(days=30),))
    janelas = cur.fetchall()

    def _carga_de(placa, dia):
        # grafias: o histórico guarda a placa crua do GPS, a carga guarda a digitada
        gr = {g.upper() for g in placas.grafias(placa)}
        achou = None
        for pl, de, ate_, cid in janelas:
            if pl in gr and de and de <= dia <= ate_:
                achou = cid          # a última vence (ordenado por id)
        return achou

    gravadas = 0
    for placa, dia in candidatos:
        ini_utc = datetime.combine(dia, datetime.min.time()) + timedelta(hours=3)
        cur.execute("""
            SELECT latitude, longitude, velocidade, data_posicao, odometer, cidade, uf
              FROM embarques_posicoes_historico
             WHERE placa = %s AND data_posicao >= %s AND data_posicao < %s
             ORDER BY data_posicao
        """, (placa, ini_utc, ini_utc + timedelta(days=1)))
        pts = cur.fetchall()
        if not pts:
            continue

        # Odômetro: cumulativo no aparelho, então atravessa buraco de sinal — é a régua
        # boa. km_gps (haversine) perde o que acontece no buraco e subestima curva; fica
        # como conferência e como plano B quando o aparelho não reporta odômetro.
        # PRIMEIRA e ÚLTIMA leitura em ordem CRONOLÓGICA — não min/max: numa troca de
        # rastreador o contador volta a zero, e min/max transformaria isso num delta de
        # centenas de milhares de km. Delta negativo (reset/troca) ou implausível vira
        # NULL: melhor não ter o número do que ter um errado, e a linha ainda guarda
        # odo_ini/odo_fim crus para auditoria.
        odos = [(p[3], int(p[4])) for p in pts if p[4] is not None and int(p[4]) > 0]
        odo_ini = odos[0][1] if odos else None
        odo_fim = odos[-1][1] if odos else None
        km_odo = None
        if odo_ini is not None:
            d = odo_fim - odo_ini
            if 0 <= d <= KM_DIA_IMPLAUSIVEL:
                km_odo = d
            else:
                _logger.warning(f'{placa} {dia}: odômetro {odo_ini}→{odo_fim} '
                                f'({d} km) — descartado (troca de aparelho ou leitura suja)')

        km_gps = 0.0
        tempo_mov = tempo_par = 0
        vel_max = 0
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            seg = geocoding.km_entre(float(a[0]), float(a[1]), float(b[0]), float(b[1]))
            if seg is not None:
                km_gps += seg
            if a[2] is not None:
                vel_max = max(vel_max, int(a[2]))
            delta_s = (b[3] - a[3]).total_seconds()
            if (a[2] or 0) > PARADO_KMH:      # mesmo corte do _consolidar_kpi
                tempo_mov += delta_s
            else:
                tempo_par += delta_s
        if pts and pts[-1][2] is not None:
            vel_max = max(vel_max, int(pts[-1][2]))

        # Cidade do dia = onde caiu o maior número de posições (pátio × estrada).
        # O 3S erra o nome a 100+ km, então isto é rótulo, nunca critério.
        contagem = {}
        for p in pts:
            if p[5]:
                contagem[(p[5], p[6])] = contagem.get((p[5], p[6]), 0) + 1
        cidade, uf = max(contagem, key=contagem.get) if contagem else (None, None)

        cur.execute("""
            INSERT INTO embarques_rastreio_dia (
                placa, dia, odo_ini, odo_fim, km_odo, km_gps, n_posicoes,
                primeira, ultima, tempo_movimento_seg, tempo_parado_seg,
                velocidade_max, cidade, uf, carga_id, consolidado_em
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ON CONFLICT (placa, dia) DO UPDATE SET
                odo_ini=EXCLUDED.odo_ini, odo_fim=EXCLUDED.odo_fim, km_odo=EXCLUDED.km_odo,
                km_gps=EXCLUDED.km_gps, n_posicoes=EXCLUDED.n_posicoes,
                primeira=EXCLUDED.primeira, ultima=EXCLUDED.ultima,
                tempo_movimento_seg=EXCLUDED.tempo_movimento_seg,
                tempo_parado_seg=EXCLUDED.tempo_parado_seg,
                velocidade_max=EXCLUDED.velocidade_max,
                cidade=EXCLUDED.cidade, uf=EXCLUDED.uf, carga_id=EXCLUDED.carga_id,
                consolidado_em=NOW()
        """, (placa, dia, odo_ini, odo_fim, km_odo, round(km_gps, 2), len(pts),
              pts[0][3], pts[-1][3], int(tempo_mov), int(tempo_par), vel_max,
              (cidade or None), (uf or None), _carga_de(placa, dia)))
        gravadas += 1

    if gravadas:
        _logger.info(f'Consolidação diária: {gravadas} linhas placa/dia gravadas')
    return gravadas


def _purgar_posicoes_antigas(cur):
    """DELETE posições com mais de RETENCAO_DIAS, independente de carga.

    A regra anterior só purgava placas que tivessem aparecido numa carga
    CONCLUÍDA. Como a maior parte dos veículos rastreados nunca entra numa
    carga lançada, o histórico dessas placas nunca era purgado e crescia sem
    teto — o que passa de detalhe a problema com o backfill diário, que grava
    ~500 pontos por veículo por dia.

    Exceção: posições de uma placa a partir do início de uma carga AINDA ABERTA
    são preservadas mesmo passando dos RETENCAO_DIAS, para não mutilar o
    trajeto de uma viagem em curso (carga longa, parada de dias no destino).
    Cargas concluídas não precisam da exceção: o KPI já foi consolidado em
    embarques_cargas_rastreio_kpi, que sobrevive à limpeza.
    """
    cur.execute("""
        DELETE FROM embarques_posicoes_historico h
        WHERE h.data_posicao < NOW() - %s::interval
          AND NOT EXISTS (
              SELECT 1
              FROM embarques_cargas c,
                   LATERAL unnest(ARRAY[c.cavalo_placa, c.carreta1_placa, c.carreta2_placa]) AS p
              WHERE p = h.placa
                AND c.status NOT IN ('Entregue', 'Cancelada')
                AND h.data_posicao >= COALESCE(c.inicio_viagem, c.data_saida_real, c.criado_em)
          )
    """, (f'{RETENCAO_DIAS} days',))
    n = cur.rowcount
    if n > 0:
        _logger.info(f'Retenção: {n} posições históricas purgadas (> {RETENCAO_DIAS} dias)')
    return n


def _deve_rodar_retencao():
    global _ultima_retencao
    if _ultima_retencao is None:
        return True
    return (datetime.utcnow() - _ultima_retencao).total_seconds() > 86400  # 1×/dia


# ── Backfill do histórico (/HistoricoPosicao) ────────────────────────
#
# Por que existe: /ListaUltimaPosicaoVeiculos devolve só o ÚLTIMO ponto. Se o
# aparelho transmitiu duas vezes entre dois ciclos nossos, a primeira leitura
# se perde para sempre. Medido em 11/08 contra o alerta nativo da 3S (que
# detecta a 1 Hz no aparelho): o polling ao vivo pegou 26 de 32 episódios de
# excesso na janela comparável (81%); o backfill pegou 37 de 37 (100%) e ainda
# recuperou o pico real, que o polling subestimava (OWH0F53 111 e não 104).
#
# Não é detecção absoluta — é o fechamento da lacuna de AMOSTRAGEM: onde o
# aparelho transmitiu, agora nós temos. A cadência continua sendo a do
# aparelho (2–5 min conforme o modelo).
#
# Papéis separados: o polling continua sendo o tempo real (mapa e detecção de
# saída/entrega); o backfill é o registro histórico, base do PGR.

BACKFILL_ATIVO = os.getenv('BACKFILL_HISTORICO', 'true').lower() == 'true'
# 12s ≈ 5/min. Com o polling (~1/min) e os logins no MESMO processo dividindo o
# bucket de 8/min, 10s raspava o teto e derrubava chamadas.
BACKFILL_ESPACO_SEG = float(os.getenv('BACKFILL_ESPACO_SEG', '12'))

# Horário do job diário em BRASÍLIA (HH:MM). Antes era hora cheia em UTC, o que
# ninguém consegue conferir de cabeça — o default de 4 valia 01:00 da manhã.
PGR_HORA_BRT = os.getenv('PGR_HORA_BRT', '06:35')
# Tolerância: se o contêiner estiver reiniciando na hora exata, ainda dispara
# dentro desta janela. Sem ela, um restart às 06:36 pularia o dia inteiro.
JANELA_DISPARO_MIN = int(os.getenv('PGR_JANELA_DISPARO_MIN', '30'))

_ultimo_backfill_dia = None


def _janela_dia_brasilia(dia):
    """Dia de Brasília → (inicio, fim) NAIVE em horário de Brasília.

    A 3S interpreta a requisição no mesmo fuso em que devolve o campo Data
    (verificado: pedindo 21:00–23:59 vieram 21:02…23:57). Então o dia do
    relatório é pedido direto, sem conversão — é justamente o desalinhamento
    que fazia a extração antiga rodar em dia-calendário UTC e deslocar o
    relatório em 3 horas.
    """
    return (datetime(dia.year, dia.month, dia.day, 0, 0, 0),
            datetime(dia.year, dia.month, dia.day, 23, 59, 59))


def _veiculos_para_backfill(cur):
    """placa → id_veiculo_3s de todo veículo que o worker já viu.

    Sai de embarques_posicoes_atuais (o worker atualiza a cada ciclo) para não
    gastar uma chamada de /ListaVeiculos. Não depende de
    embarques_veiculos_rastreio, que exige carga lançada.

    O corte por atualizado_em faz a lista se auto-limpar: veículo que saiu da
    conta da 3S para de ser devolvido pelo polling, envelhece e cai fora
    sozinho — senão gastaríamos cota todo dia pedindo histórico de placa que
    não existe mais (na base local são 101 linhas para 93 veículos vivos).
    """
    cur.execute("""
        SELECT placa, id_veiculo_3s,
               atualizado_em > NOW() - %s::interval AS ativo
        FROM embarques_posicoes_atuais
        WHERE id_veiculo_3s IS NOT NULL AND placa <> ''
        ORDER BY placa
    """, (f'{RETENCAO_DIAS} days',))
    linhas = cur.fetchall()
    fora = [p for p, _, ativo in linhas if not ativo]
    if fora:
        # A saída precisa ser VISÍVEL: veículo com rastreador em conserto some
        # da lista em silêncio e sai do PGR sem ninguém saber.
        _logger.info(f'Backfill: {len(fora)} veículo(s) fora por inatividade '
                     f'(> {RETENCAO_DIAS}d sem posição): {", ".join(fora)}')
    return [(p, i) for p, i, ativo in linhas if ativo]


def _gravar_historico(cur, pontos):
    """UPSERT dos pontos do histórico. Devolve (inseridos, enriquecidos).

    `DO NOTHING` seria idempotente, mas descartaria informação: o ponto que já
    veio do polling ao vivo ocupa a chave (placa, data_posicao) e **não tem**
    `endereco` nem `odometer` — colunas que só o /HistoricoPosicao entrega.
    Resultado prático: em dias já polados o relatório mostrava a cidade em vez
    da rodovia, porque o pico do episódio caía num ponto vindo do polling.

    Então completa o que está nulo, sem sobrescrever o que já existe: a linha
    do polling é tão válida quanto a do histórico para os campos comuns.
    """
    inseridos = enriquecidos = 0
    for p in pontos:
        lat, lng = _safe_float(p.get('latitude')), _safe_float(p.get('longitude'))
        if lat is None or lng is None:
            continue
        cur.execute("""
            INSERT INTO embarques_posicoes_historico (
                placa, id_veiculo_3s, data_posicao, latitude, longitude,
                velocidade, ignicao, uf, cidade, endereco, odometer
            ) VALUES (%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s)
            ON CONFLICT (placa, data_posicao) DO UPDATE SET
                endereco = COALESCE(embarques_posicoes_historico.endereco, EXCLUDED.endereco),
                odometer = COALESCE(embarques_posicoes_historico.odometer, EXCLUDED.odometer)
            RETURNING (xmax = 0)
        """, (p['placa'], _safe_int(p.get('idVeiculo')), _parse_data(p.get('data')),
              lat, lng, _safe_int(p.get('velocidade'), 0),
              (p.get('uf') or '')[:2], (p.get('cidade') or '')[:120],
              (p.get('endereco') or '')[:200] or None, _safe_int(p.get('odometer'))))
        r = cur.fetchone()
        if r and r[0]:
            inseridos += 1
        else:
            enriquecidos += 1
    return inseridos, enriquecidos


def _historico_com_retry(id_veiculo, placa, dt_ini, dt_fim, tentativas=3):
    """Busca o histórico, esperando quando o bucket da 3S estoura.

    O bucket de 8/min é POR PROCESSO e o polling vive no mesmo processo, então
    backfill (~6/min) + ciclo (~1/min) + logins raspam o teto. Quando raspava,
    o veículo era contado como falha e PULADO — perdendo o dia inteiro dele
    (aconteceu com 5 placas em 12/08). Esperar e repetir custa segundos; pular
    custa o dado.
    """
    for tentativa in range(tentativas):
        try:
            return tres_s_client.historico_posicao(id_veiculo, placa, dt_ini, dt_fim)
        except tres_s_client.RateLimitExceeded as e:
            if tentativa == tentativas - 1:
                raise
            espera = BACKFILL_ESPACO_SEG * (tentativa + 1)
            _logger.info(f'  {placa}: cota cheia ({e}) — aguardando {espera:.0f}s')
            time.sleep(espera)
    return []


def backfill_dia(dia):
    """Puxa /HistoricoPosicao de todos os veículos para um dia de Brasília.

    Conexão e transação próprias, commit por veículo: um veículo que falhe não
    derruba os anteriores, e nada fica segurando transação por 16 minutos.

    Auto-espaçado em BACKFILL_ESPACO_SEG. Não dá para confiar no token bucket
    do tres_s_client aqui: ele levanta RateLimitExceeded quando a espera passa
    de 5s, o que estouraria num loop de ~93 chamadas. E o bucket é por
    PROCESSO — como o worker é thread do mesmo Flask, backfill e polling
    dividem os mesmos 8/min, dos quais o polling já consome ~1,4/min.
    """
    if tres_s_client.is_modo_simulado():
        _logger.info('Backfill ignorado: MODO_SIMULADO')
        return 0

    dt_ini, dt_fim = _janela_dia_brasilia(dia)
    conn = _get_db()
    cur = conn.cursor()
    try:
        veiculos = _veiculos_para_backfill(cur)
    finally:
        cur.close()

    _logger.info(f'Backfill {dia:%d/%m/%Y} (BRT): {len(veiculos)} veículos, '
                 f'~{len(veiculos) * BACKFILL_ESPACO_SEG / 60:.0f} min')

    total = total_enriq = falhas = 0
    for i, (placa, idv) in enumerate(veiculos):
        if not _running:
            _logger.warning('Backfill interrompido: worker parando')
            break
        if i:
            time.sleep(BACKFILL_ESPACO_SEG)
        try:
            pontos = _historico_com_retry(idv, placa, dt_ini, dt_fim)
            cur = conn.cursor()
            try:
                n, enriq = _gravar_historico(cur, pontos)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()
            total += n
            total_enriq += enriq
            # INFO, não DEBUG: num job de ~16 min o silêncio no log é
            # indistinguível de travamento, e foi o que levou a disparar o
            # backfill várias vezes achando que o anterior tinha morrido.
            _logger.info(f'  [{i + 1}/{len(veiculos)}] {placa}: {len(pontos)} pontos, '
                         f'{n} novos, {enriq} completados')
        except Exception as e:
            falhas += 1
            _logger.warning(f'  {placa}: falha no backfill — {e}')

    conn.close()
    _logger.info(f'Backfill {dia:%d/%m/%Y} concluído: {total} posições novas, '
                 f'{total_enriq} completadas, {falhas} falhas')
    return total


def _apurar_pgr(dia):
    """Passo 2 do job diário: apura os excessos DEPOIS do backfill.

    A ordem não pode inverter. Apurando antes do backfill, o relatório sai com
    os ~81% de cobertura do polling ao vivo em vez dos 100% do histórico — e
    ninguém percebe, porque o número simplesmente vem menor.

    Falha aqui não pode derrubar o loop: o backfill do dia já foi gravado e a
    apuração é reprocessável (upsert por placa+ini).
    """
    try:
        import pgr
    except ImportError:
        _logger.warning('Módulo pgr indisponível — apuração ignorada')
        return
    conn = _get_db()
    try:
        cur = conn.cursor()
        try:
            n = pgr.apurar_dia(cur, dia)
            conn.commit()
            _logger.info(f'PGR {dia:%d/%m/%Y}: {n} episódios apurados')
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()

        # Passo 3, em transação separada: uma falha no envio não pode desfazer
        # a apuração já gravada.
        try:
            import pgr_envio
            cur = conn.cursor()
            try:
                pgr_envio.enviar_relatorio(cur, dia)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()
        except Exception as e:
            _logger.exception(f'Falha ao enviar PGR de {dia:%d/%m/%Y}: {e}')
    except Exception as e:
        _logger.exception(f'Falha ao apurar PGR de {dia:%d/%m/%Y}: {e}')
    finally:
        conn.close()


def _hora_agendada():
    """PGR_HORA_BRT → (hora, minuto). Formato inválido cai no default."""
    try:
        h, m = PGR_HORA_BRT.strip().split(':')
        h, m = int(h), int(m)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except (ValueError, AttributeError):
        pass
    _logger.warning(f'PGR_HORA_BRT inválido ({PGR_HORA_BRT!r}) — usando 06:35')
    return 6, 35


def _loop_backfill():
    """Job diário: backfill → apuração → envio, no horário de Brasília.

    A ordem não pode inverter (apurar antes do backfill entrega ~81% de
    cobertura em vez de 100%), mas uma falha no backfill NÃO pode impedir a
    apuração e o envio: o dia teria os dados do polling, incompletos porém
    reais, e o silêncio é pior — quem recebe não distingue "ninguém correu" de
    "o job caiu".
    """
    global _ultimo_backfill_dia
    hh, mm = _hora_agendada()
    _logger.info(f'Job do PGR agendado para {hh:02d}:{mm:02d} (Brasília) '
                 f'— espaçamento {BACKFILL_ESPACO_SEG}s/chamada')
    while _running:
        try:
            agora_brt = datetime.utcnow() - timedelta(hours=3)
            alvo = agora_brt.date() - timedelta(days=1)   # o dia BRT que fechou
            marcado = agora_brt.replace(hour=hh, minute=mm, second=0, microsecond=0)
            atraso = (agora_brt - marcado).total_seconds() / 60
            if 0 <= atraso < JANELA_DISPARO_MIN and _ultimo_backfill_dia != alvo:
                _ultimo_backfill_dia = alvo
                try:
                    backfill_dia(alvo)
                except Exception:
                    _logger.exception('Backfill falhou — seguindo para a apuração '
                                      'com o que houver')
                _apurar_pgr(alvo)
        except Exception:
            _logger.exception('Falha no loop do job diário')
        for _ in range(60):
            if not _running:
                break
            time.sleep(1)


# ── Loop principal ───────────────────────────────────────────────────

def _ciclo():
    global _ultima_retencao
    _logger.info(f'Worker iniciado (modo_simulado={tres_s_client.is_modo_simulado()}, intervalo={INTERVALO_SEG}s)')

    while _running:
        t0 = time.time()
        try:
            posicoes = tres_s_client.lista_ultima_posicao(0)

            conn = _get_db()
            cur = conn.cursor()
            try:
                _persistir_posicoes(cur, posicoes)
                _processar_cargas(cur)

                if _deve_rodar_retencao():
                    # A ORDEM NÃO PODE INVERTER: consolidar antes de purgar. O que a
                    # purga leva não volta (a 3S serve ~35 dias de histórico).
                    _consolidar_dias(cur)
                    _purgar_posicoes_antigas(cur)
                    _ultima_retencao = datetime.utcnow()

                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cur.close()
                conn.close()

            dur = time.time() - t0
            _logger.debug(f'Ciclo OK: {len(posicoes)} posições em {dur:.2f}s')

        except tres_s_client.RateLimitExceeded as e:
            _logger.warning(f'Rate limit 3S atingido — pulando ciclo: {e}')
        except tres_s_client.TresSError as e:
            _logger.error(f'Erro 3S: {e}')
        except Exception:
            _logger.exception('Falha inesperada no ciclo do worker')

        # sleep restante do intervalo
        elapsed = time.time() - t0
        sleep_for = max(1, INTERVALO_SEG - elapsed)
        for _ in range(int(sleep_for)):
            if not _running:
                break
            time.sleep(1)


def start():
    global _running, _thread
    if _running:
        return
    _running = True
    _thread = threading.Thread(target=_ciclo, daemon=True, name='RastreamentoWorker')
    _thread.start()
    if BACKFILL_ATIVO:
        # Thread separada: o backfill leva ~16 min e travaria o ciclo de 60s
        # (e seguraria a transação do polling aberta o tempo todo).
        threading.Thread(target=_loop_backfill, daemon=True, name='BackfillHistorico').start()


def stop():
    global _running
    _running = False


def is_running():
    return _running and _thread is not None and _thread.is_alive()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    start()
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        print('\nParando worker...')
        stop()
