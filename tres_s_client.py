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
from datetime import datetime, timedelta
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

    # expiration vem ISO. Salva como naive UTC.
    exp = datetime.fromisoformat(exp_str.replace('Z', '+00:00')).replace(tzinfo=None)
    _salvar_token(token, exp)
    return token


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


# ── Wrappers públicos ──
if MODO_SIMULADO:
    from simulador_3s import lista_veiculos, lista_ultima_posicao  # noqa: F401
else:
    def lista_veiculos():
        return _call('GET', '/ListaVeiculos')

    def lista_ultima_posicao(id_veiculo=0):
        return _call('GET', f'/ListaUltimaPosicaoVeiculos/{id_veiculo}')


def is_modo_simulado():
    return MODO_SIMULADO


if __name__ == '__main__':
    print(f'MODO_SIMULADO = {MODO_SIMULADO}')
    veiculos = lista_veiculos()
    print(f'Veículos: {len(veiculos)}')
    posicoes = lista_ultima_posicao(0)
    print(f'Posições atuais: {len(posicoes)}')
