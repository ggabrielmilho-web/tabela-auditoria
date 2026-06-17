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
  4. Job de retenção (1×/dia)
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
        cur.execute("SELECT 1 FROM embarques_veiculos_rastreio WHERE placa = %s", (p,))
        if cur.fetchone():
            return p
    return None


def _pos_fresca(cur, placa):
    """Posição atual da placa em embarques_posicoes_atuais SÓ se for recente
    (NOW - data_posicao <= FRESCOR_H). Retorna (lat,lng,cidade,uf,data,vel) ou None.
    Usado pra decidir se a carreta tem sinal utilizável ou se caímos no cavalo."""
    p = (placa or '').strip().upper()
    if not p:
        return None
    cur.execute("""
        SELECT latitude, longitude, cidade, uf, data_posicao, velocidade
        FROM embarques_posicoes_atuais
        WHERE placa = %s AND data_posicao >= (NOW() AT TIME ZONE 'UTC') - %s::interval
    """, (p, f'{FRESCOR_H} hours'))
    return cur.fetchone()


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
                velocidade, ignicao, uf, cidade
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (placa, data_posicao) DO NOTHING
        """, (placa, id_veiculo_3s, data_pos, lat, lng,
              velocidade, ignicao, uf, cidade))


# ── Detecção (cidade + raio + N ciclos) ─────────────────────────────

def _n_ciclos_fora_cidade(cur, placa, cidade_referencia, n):
    """True se as últimas N posições da placa estão FORA da cidade de referência."""
    cur.execute("""
        SELECT cidade FROM embarques_posicoes_historico
        WHERE placa = %s
        ORDER BY data_posicao DESC
        LIMIT %s
    """, (placa, n))
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
        WHERE placa = %s ORDER BY data_posicao DESC LIMIT %s
    """, (placa, n))
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
    cur.execute("SELECT latitude, longitude FROM embarques_posicoes_atuais WHERE placa=%s", (placa,))
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

    cur.execute("""
        SELECT latitude, longitude, velocidade, data_posicao
        FROM embarques_posicoes_historico
        WHERE placa=%s AND data_posicao BETWEEN %s AND %s
        ORDER BY data_posicao
    """, (placa, inicio, fim))
    rows = cur.fetchall()
    # Recorta o trecho PRÉ-origem (caminhão rodando antes do lançamento)
    if rows and origem_lat is not None and origem_lng is not None:
        coords = [(float(la), float(ln)) for (la, ln, _v, _d) in rows]
        idx = geocoding.indice_saida_origem(coords, float(origem_lat), float(origem_lng))
        rows = rows[idx:]
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
          AND (
            EXISTS (SELECT 1 FROM embarques_veiculos_rastreio v WHERE v.placa = c.carreta1_placa)
            OR EXISTS (SELECT 1 FROM embarques_veiculos_rastreio v WHERE v.placa = c.cavalo_placa)
            OR EXISTS (SELECT 1 FROM embarques_veiculos_rastreio v WHERE v.placa = c.carreta2_placa)
          )
    """)
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
        if inicio_viagem is None:
            iv = geocoding.detectar_inicio_viagem(
                placa, origem_cid, origem_uf, data_carreg, cur.connection,
                origem_lat=float(origem_lat) if origem_lat is not None else None,
                origem_lng=float(origem_lng) if origem_lng is not None else None,
            )
            if iv is None:
                from datetime import time as _t
                iv = (datetime.combine(data_carreg, _t()) if data_carreg
                      else datetime.utcnow() - timedelta(days=geocoding.RECONSTRUCAO_MAX_DIAS))
            cur.execute("UPDATE embarques_cargas SET inicio_viagem=%s WHERE id=%s", (iv, carga_id))
            inicio_viagem = iv

        cur.execute("""
            SELECT latitude, longitude, cidade, uf, data_posicao, velocidade
            FROM embarques_posicoes_atuais WHERE placa=%s
        """, (placa,))
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

        # Frescor da posição da placa rastreada. Se a CARRETA estiver sem sinal recente, a
        # CHEGADA/"No destino" passam a olhar o CAVALO (vivo) — mesmas regras (nome+parado).
        # Saída-da-origem e fechamento continuam SÓ na placa rastreada (carreta), e o
        # fechamento só dispara com ela fresca (senão, posição velha fecharia errado).
        placa_fresca = pos_data is not None and (datetime.utcnow() - pos_data) <= timedelta(hours=FRESCOR_H)
        if placa_fresca:
            ref_cidade, ref_uf, ref_vel = pos_cidade, pos_uf, pos_vel
        elif not desengatada:
            cav = _pos_fresca(cur, cavalo_placa)
            if cav:
                ref_cidade, ref_uf = cav[2], cav[3]
                ref_vel = float(cav[5]) if cav[5] is not None else None
                _logger.info(f'[Carga {carga_id}] Carreta {placa} sem sinal recente — chegada via cavalo {cavalo_placa}')
            else:
                ref_cidade = ref_uf = ref_vel = None
        else:
            ref_cidade = ref_uf = ref_vel = None

        # ── SAÍDA DA ORIGEM (status Aberta → Em rota)
        if status == 'Aberta':
            if _saiu_da_cidade(cur, placa, pos_cidade, origem_cid, origem_uf, centroide_origem, RAIO_SAIDA_ORIGEM_KM):
                # data_saida_real = horário GPS da posição (não NOW do worker), pra alinhar
                # com os pontos e nunca excluir o histórico da viagem na janela.
                cur.execute("""
                    UPDATE embarques_cargas
                    SET status='Em rota', saida_auto=TRUE, data_saida_real=%s,
                        atualizado_em=NOW()
                    WHERE id=%s
                """, (pos_data, carga_id))
                _logger.info(f'[Carga {carga_id}/{placa}] Saída automática da origem detectada')
                status = 'Em rota'

        # ── CHEGADA NA CIDADE DO DESTINO (marca no_local_desde; segue 'Em rota' + ícone 📦)
        # Vale também p/ 'Desengatada' (desengate "Em rota" antes do GPS confirmar a chegada):
        # o worker detecta a chegada da carreta e só então a saída finaliza — sem fechar cedo.
        if status in ('Em rota', 'Desengatada') and no_local_desde is None:
            if ref_cidade is not None and _mesma_cidade(ref_cidade, ref_uf, dest_cidade, dest_uf):
                cur.execute("UPDATE embarques_cargas SET no_local_desde=NOW(), atualizado_em=NOW() WHERE id=%s", (carga_id,))
                _logger.info(f'[Carga {carga_id}/{placa}] Chegou na cidade do destino ({dest_cidade}/{dest_uf})')
                no_local_desde = datetime.utcnow()

        # ── PROMOÇÃO p/ 'No destino' (na cidade da descarga há >= CHEGADA_MIN_PARADO E parado agora)
        if status == 'Em rota' and no_local_desde is not None \
                and ref_cidade is not None and _mesma_cidade(ref_cidade, ref_uf, dest_cidade, dest_uf) \
                and (datetime.utcnow() - no_local_desde) >= timedelta(minutes=CHEGADA_MIN_PARADO) \
                and (ref_vel is None or ref_vel <= PARADO_KMH):
            cur.execute("UPDATE embarques_cargas SET status='No destino', atualizado_em=NOW() WHERE id=%s", (carga_id,))
            _logger.info(f'[Carga {carga_id}/{placa}] Parado na cidade do destino há +{CHEGADA_MIN_PARADO}min → status "No destino"')
            status = 'No destino'

        # ── SAÍDA DO DESTINO (= entrega automática) — 'Em rota', 'No destino' ou 'Desengatada'
        # GUARDA: só finaliza com a placa rastreada (carreta) FRESCA. Carreta muda → não fecha
        # (cai no alerta/finalização manual), pra não fechar errado por posição velha ou desengate.
        if status in ('Em rota', 'No destino', 'Desengatada') and no_local_desde is not None and placa_fresca:
            if _saiu_da_cidade(cur, placa, pos_cidade, dest_cidade, dest_uf, centroide_dest, RAIO_SAIDA_DESTINO_KM):
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

def _purgar_posicoes_antigas(cur):
    """DELETE posições > RETENCAO_DIAS APÓS conclusão da carga. KPIs mantidos."""
    cur.execute("""
        DELETE FROM embarques_posicoes_historico
        WHERE placa IN (
            SELECT p FROM embarques_cargas c,
                 LATERAL unnest(ARRAY[c.cavalo_placa, c.carreta1_placa, c.carreta2_placa]) AS p
            WHERE c.status IN ('Entregue','Cancelada')
              AND c.data_conclusao IS NOT NULL
              AND c.data_conclusao < NOW() - %s::interval
              AND p IS NOT NULL
        )
        AND data_posicao < NOW() - %s::interval
    """, (f'{RETENCAO_DIAS} days', f'{RETENCAO_DIAS} days'))
    n = cur.rowcount
    if n > 0:
        _logger.info(f'Retenção: {n} posições históricas purgadas (> {RETENCAO_DIAS} dias após conclusão)')
    return n


def _deve_rodar_retencao():
    global _ultima_retencao
    if _ultima_retencao is None:
        return True
    return (datetime.utcnow() - _ultima_retencao).total_seconds() > 86400  # 1×/dia


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
