"""
Cliente da WebAPI 3S DataExportAPI.

Quando MODO_SIMULADO=true no .env, roteia tudo pra simulador_3s.py.
Quando MODO_SIMULADO=false, faz HTTP real com:
  - Token cacheado em embarques_3s_token (persiste entre restarts)
  - Token bucket de 8/min (margem do limite oficial 10/min)
  - Renovação automática 60s antes de expirar
  - Retry no 401 (token expirado meio-caminho)

Resto do código sempre faz: from tres_s_client import lista_ultima_posicao, lista_veiculos
"""

import os
import time
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()

MODO_SIMULADO = os.getenv('MODO_SIMULADO', 'true').lower() == 'true'
BASE_URL = os.getenv('TRES_S_BASE_URL', '').rstrip('/')
USUARIO = os.getenv('TRES_S_USUARIO', '')
SENHA = os.getenv('TRES_S_SENHA', '')

# Token bucket — 8 calls/min (margem dos 10 oficiais)
_LIMIT_PER_MIN = 8
_CALL_TIMESTAMPS = deque(maxlen=_LIMIT_PER_MIN)
_LOCK = threading.Lock()


class RateLimitExceeded(Exception):
    pass


class TresSError(Exception):
    def __init__(self, codigo, msg):
        self.codigo = codigo
        super().__init__(f'[{codigo}] {msg}')


def _get_db():
    return psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        port=os.getenv('DB_PORT', '5432'),
        dbname=os.getenv('DB_NAME', 'postgres'),
        user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD', ''),
    )


def _log(endpoint, duracao_ms, status_http, erro_codigo=None, erro_msg=None):
    try:
        conn = _get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO embarques_3s_log (provider, endpoint, duracao_ms, status_http, erro_codigo, erro_msg) "
            "VALUES ('3S', %s, %s, %s, %s, %s)",
            (endpoint, duracao_ms, status_http, erro_codigo, erro_msg)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


def _throttle():
    """Bloqueia se já atingiu _LIMIT_PER_MIN chamadas nos últimos 60s."""
    with _LOCK:
        agora = time.time()
        while _CALL_TIMESTAMPS and (agora - _CALL_TIMESTAMPS[0]) > 60:
            _CALL_TIMESTAMPS.popleft()
        if len(_CALL_TIMESTAMPS) >= _LIMIT_PER_MIN:
            espera = 60 - (agora - _CALL_TIMESTAMPS[0])
            if espera > 5:
                raise RateLimitExceeded(f'Limit 3S atingido; aguardar {espera:.1f}s')
            time.sleep(max(0.1, espera))
        _CALL_TIMESTAMPS.append(time.time())


def _token_do_db():
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("SELECT token, expiration FROM embarques_3s_token WHERE id=1")
    r = cur.fetchone()
    cur.close()
    conn.close()
    if r:
        return r[0], r[1]
    return None, None


def _salvar_token(token, expiration):
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO embarques_3s_token (id, token, expiration, atualizado_em)
        VALUES (1, %s, %s, NOW())
        ON CONFLICT (id) DO UPDATE SET
            token = EXCLUDED.token,
            expiration = EXCLUDED.expiration,
            atualizado_em = NOW()
    """, (token, expiration))
    conn.commit()
    cur.close()
    conn.close()


def _renovar_token():
    """POST /ValidaLogin. Loga. Salva no DB."""
    if not USUARIO or not SENHA:
        raise TresSError('NOCRED', 'TRES_S_USUARIO/SENHA não configurados no .env')
    _throttle()
    t0 = time.time()
    try:
        resp = requests.post(
            f'{BASE_URL}/ValidaLogin',
            json={'usuario': USUARIO, 'senha': SENHA},
            timeout=30
        )
        dur = int((time.time() - t0) * 1000)
        _log('/ValidaLogin', dur, resp.status_code)
        resp.raise_for_status()
        data = resp.json()
    except requests.HTTPError as e:
        dur = int((time.time() - t0) * 1000)
        _log('/ValidaLogin', dur, e.response.status_code, str(e.response.status_code), e.response.text[:500])
        raise TresSError('LOGIN_FAIL', f'HTTP {e.response.status_code}')

    token = data.get('token')
    exp_str = data.get('expiration')
    if not token:
        raise TresSError('LOGIN_FAIL', 'Resposta sem token')

    _salvar_token(token, _expiration_para_utc(exp_str))
    return token


def _expiration_para_utc(exp_str):
    """expiration da 3S → naive UTC.

    A API devolve a expiração em horário de BRASÍLIA e sem fuso
    ('2026-08-12T11:07:57.5' com o UTC em 14:07). Guardar o valor cru e
    comparar com utcnow() em _get_token fazia o token parecer vencido SEMPRE
    (BRT = UTC-3), então toda chamada era precedida de um /ValidaLogin: cada
    requisição custava duas, metade da cota de 10/min indo embora em login
    redundante, e o cache em embarques_3s_token nunca surtia efeito.
    """
    BRT = timezone(timedelta(hours=-3))
    dt = datetime.fromisoformat(str(exp_str).replace('Z', ''))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BRT)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _get_token():
    """Pega token do DB; renova se < 60s pra expirar."""
    tok, exp = _token_do_db()
    if not tok or exp is None or exp < datetime.utcnow() + timedelta(seconds=60):
        return _renovar_token()
    return tok


def _call(method, path, *, json_body=None, params=None, _retry=False):
    _throttle()
    token = _get_token()
    headers = {'Authorization': f'Bearer {token}'}
    t0 = time.time()
    try:
        resp = requests.request(
            method, f'{BASE_URL}{path}',
            headers=headers, json=json_body, params=params, timeout=30
        )
        dur = int((time.time() - t0) * 1000)
        _log(path, dur, resp.status_code)
        if resp.status_code == 401 and not _retry:
            # Token virou — renova e tenta 1x mais
            _renovar_token()
            return _call(method, path, json_body=json_body, params=params, _retry=True)
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as e:
        dur = int((time.time() - t0) * 1000)
        msg = ''
        try:
            msg = e.response.text[:500]
        except Exception:
            pass
        _log(path, dur, e.response.status_code, str(e.response.status_code), msg)
        raise TresSError(f'HTTP_{e.response.status_code}', msg)
    except Exception as e:
        dur = int((time.time() - t0) * 1000)
        _log(path, dur, 0, 'EXCEPTION', str(e))
        raise TresSError('EXCEPTION', str(e))


# ── Normalização da resposta real da 3S ────────────────────────────
# A API retorna campos em PascalCase, lat/lng como string com vírgula,
# Data em formato BR e Odometro em vez de odometer. Normalizamos pra
# que o resto do código (worker, endpoints) trate igual ao simulador.

def _to_float_br(s):
    """'-22,487853' → -22.487853 ; aceita None/float/str."""
    if s is None or s == '':
        return None
    if isinstance(s, (int, float)):
        return float(s)
    try:
        return float(str(s).replace(',', '.'))
    except (ValueError, TypeError):
        return None


def _parse_data_br(s):
    """A 3S retorna data em horário de Brasília (UTC-3). Convertemos pra UTC
    e devolvemos string ISO **com Z**, pra que o JS no frontend interprete
    corretamente como UTC e o '*há X min*' bata.
    Ex: '23/06/2025 17:13:22' (BR) → '2025-06-23T20:13:22Z' (UTC).
    """
    if not s:
        return None
    s = str(s).strip()
    from datetime import datetime, timezone, timedelta
    BRT = timezone(timedelta(hours=-3))
    try:
        if 'T' in s or '-' in s[:4]:
            dt = datetime.fromisoformat(s.replace('Z', ''))
        else:
            dt = datetime.strptime(s, '%d/%m/%Y %H:%M:%S')
        # Trata como BR e converte pra UTC
        dt_utc = dt.replace(tzinfo=BRT).astimezone(timezone.utc)
        return dt_utc.strftime('%Y-%m-%dT%H:%M:%SZ')
    except ValueError:
        return s


def _normalizar_veiculo(v):
    """PascalCase → minúsculo. Preserva idVeiculo/idEquipamento/idCliente em camelCase."""
    return {
        'frota': v.get('Frota') or v.get('frota'),
        'placa': (v.get('Placa') or v.get('placa') or '').replace(' ', '').replace('-', '').strip().upper(),
        'modelo': v.get('Modelo') or v.get('modelo'),
        'chassis': v.get('Chassis') or v.get('chassis'),
        'renavam': v.get('Renavam') or v.get('renavam'),
        'idEquipamento': v.get('idEquipamento'),
        'idCliente': v.get('idCliente'),
        'numSerie': v.get('NumSerie') or v.get('numSerie'),
        'idVeiculo': v.get('idVeiculo'),
        'tipo': v.get('Tipo') or v.get('tipo'),
    }


def _normalizar_historico(p, placa, id_veiculo):
    """Normaliza um item de /HistoricoPosicao.

    O DTO do histórico NÃO traz Placa, idVeiculo, Ignicao nem Bloqueio — por
    isso placa/id_veiculo vêm por fora (a consulta é por veículo). Ignição fica
    None: só o mapa a consome, a detecção de velocidade não depende dela.
    Traz, em compensação, Endereco (nome da rodovia) e Odometro.
    """
    return {
        'idPosicao':     p.get('idPosicao'),
        'placa':         placa,
        'idVeiculo':     id_veiculo,
        'data':          _parse_data_br(p.get('Data') or p.get('data')),
        'velocidade':    p.get('Velocidade') if p.get('Velocidade') is not None else p.get('velocidade'),
        'satelite':      p.get('Satelite') or p.get('satelite'),
        'direcao':       p.get('Direcao') or p.get('direcao'),
        'uf':            p.get('UF') or p.get('uf'),
        'cidade':        p.get('Cidade') or p.get('cidade'),
        'bairro':        p.get('Bairro') or p.get('bairro'),
        'endereco':      p.get('Endereco') or p.get('endereco'),
        'latitude':      _to_float_br(p.get('Latitude') or p.get('latitude')),
        'longitude':     _to_float_br(p.get('Longitude') or p.get('longitude')),
        'odometer':      p.get('Odometro') if p.get('Odometro') is not None else p.get('odometer'),
        'ignicao':       None,
        'bloqueio':      None,
    }


def _normalizar_posicao(p):
    """PascalCase → minúsculo. Converte lat/lng/data."""
    return {
        'idPosicao':     p.get('idPosicao'),
        'frota':         p.get('Frota') or p.get('frota'),
        'placa':         (p.get('Placa') or p.get('placa') or '').replace(' ', '').replace('-', '').strip().upper(),
        'modelo':        p.get('Modelo') or p.get('modelo'),
        'data':          _parse_data_br(p.get('Data') or p.get('data')),
        'velocidade':    p.get('Velocidade') if p.get('Velocidade') is not None else p.get('velocidade'),
        'satelite':      p.get('Satelite') or p.get('satelite'),
        'ignicao':       p.get('Ignicao') or p.get('ignicao'),
        'direcao':       p.get('Direcao') or p.get('direcao'),
        'uf':            p.get('UF') or p.get('uf'),
        'cidade':        p.get('Cidade') or p.get('cidade'),
        'bairro':        p.get('Bairro') or p.get('bairro'),
        'endereco':      p.get('Endereco') or p.get('endereco'),
        'numero':        p.get('Numero') or p.get('numero'),
        'cep':           p.get('CEP') or p.get('cep'),
        'latitude':      _to_float_br(p.get('Latitude') or p.get('latitude')),
        'longitude':     _to_float_br(p.get('Longitude') or p.get('longitude')),
        'idEquipamento': p.get('idEquipamento'),
        'idVeiculo':     p.get('idVeiculo'),
        'bloqueio':      p.get('Bloqueio') or p.get('bloqueio'),
        'odometer':      p.get('Odometro') if p.get('Odometro') is not None else p.get('odometer'),
        'hourmeter':     p.get('Hourmeter') or p.get('hourmeter'),
    }


# ── Wrappers públicos ──
if MODO_SIMULADO:
    from simulador_3s import lista_veiculos, lista_ultima_posicao  # noqa: F401

    def historico_posicao(id_veiculo, placa, dt_inicio, dt_fim):
        """O simulador não tem histórico denso — backfill vira no-op."""
        return []
else:
    def lista_veiculos():
        data = _call('GET', '/ListaVeiculos')
        return [_normalizar_veiculo(v) for v in (data or [])]

    def lista_ultima_posicao(id_veiculo=0):
        data = _call('GET', f'/ListaUltimaPosicaoVeiculos/{id_veiculo}')
        return [_normalizar_posicao(p) for p in (data or [])]

    def historico_posicao(id_veiculo, placa, dt_inicio, dt_fim):
        """POST /HistoricoPosicao — todas as posições transmitidas no período.

        dt_inicio/dt_fim são datetimes NAIVE em horário de BRASÍLIA: a API
        interpreta a requisição no mesmo fuso em que devolve o campo Data
        (verificado empiricamente — pedindo 21:00–23:59 vieram 21:02…23:57).

        Diferente de /ListaUltimaPosicaoVeiculos, que devolve só o último ponto
        e por isso descarta o que o aparelho transmitiu entre dois ciclos do
        worker, aqui vem tudo. A cadência é a mesma (2–5 min conforme o
        aparelho); o que muda é não haver perda.

        A API repete o mesmo instante com idPosicao diferente — deduplicamos
        por data, que é a chave que o banco também usa (UNIQUE placa+data).
        """
        data = _call('POST', '/HistoricoPosicao', json_body={
            'idVeiculo': id_veiculo,
            'dataInicio': dt_inicio.strftime('%Y-%m-%dT%H:%M:%S'),
            'dataFim': dt_fim.strftime('%Y-%m-%dT%H:%M:%S'),
        })
        vistos, saida = set(), []
        for p in (data or []):
            chave = p.get('Data') or p.get('data')
            if chave in vistos:
                continue
            vistos.add(chave)
            saida.append(_normalizar_historico(p, placa, id_veiculo))
        return saida


def is_modo_simulado():
    return MODO_SIMULADO


if __name__ == '__main__':
    print(f'MODO_SIMULADO = {MODO_SIMULADO}')
    veiculos = lista_veiculos()
    print(f'Veículos: {len(veiculos)}')
    posicoes = lista_ultima_posicao(0)
    print(f'Posições atuais: {len(posicoes)}')
