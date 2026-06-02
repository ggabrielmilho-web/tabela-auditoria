"""
Simulador da API 3S. Lê posições da tabela `embarques_simulacao`.
Usado quando MODO_SIMULADO=true no .env.

Mesma interface que tres_s_client real: lista_veiculos(), lista_ultima_posicao(idVeiculo=0).
Loga em embarques_3s_log com provider='SIM'.
"""

import os
import time
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()


def _get_db():
    return psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        port=os.getenv('DB_PORT', '5432'),
        dbname=os.getenv('DB_NAME', 'postgres'),
        user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD', ''),
    )


def _log(endpoint, duracao_ms, status=200, erro=None):
    try:
        conn = _get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO embarques_3s_log (provider, endpoint, duracao_ms, status_http, erro_msg) "
            "VALUES ('SIM', %s, %s, %s, %s)",
            (endpoint, duracao_ms, status, erro)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass


def lista_veiculos():
    """Retorna lista de veículos distintos da tabela de simulação.
    Formato espelha resposta da 3S: frota, placa, modelo, idVeiculo, tipo, idEquipamento.
    """
    t0 = time.time()
    conn = _get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT DISTINCT ON (placa)
            placa,
            id_veiculo_3s AS "idVeiculo",
            'Simulado' AS frota,
            'Simulador' AS modelo,
            '-' AS chassis,
            id_veiculo_3s AS "idEquipamento",
            1 AS "idCliente",
            id_veiculo_3s AS "numSerie",
            'Cavalo' AS tipo
        FROM embarques_simulacao
        ORDER BY placa, data_posicao DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    _log('/ListaVeiculos', int((time.time() - t0) * 1000))
    return [dict(r) for r in rows]


def lista_ultima_posicao(id_veiculo=0):
    """Retorna última posição de um veículo (id_veiculo > 0) ou de TODOS (id_veiculo=0).
    Formato espelha resposta da 3S: idPosicao, placa, data, latitude, longitude, etc.
    """
    t0 = time.time()
    conn = _get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if id_veiculo == 0:
        # Última posição de cada placa (DISTINCT ON)
        cur.execute("""
            SELECT DISTINCT ON (placa)
                id AS "idPosicao",
                'Simulado' AS frota,
                placa,
                'Simulador' AS modelo,
                NOW() AS data,
                COALESCE(velocidade, 0) AS velocidade,
                10 AS satelite,
                CASE WHEN ignicao THEN 'Ligada' ELSE 'Desligada' END AS ignicao,
                COALESCE(direcao, 'N') AS direcao,
                uf,
                cidade,
                COALESCE(bairro, '') AS bairro,
                COALESCE(endereco, '') AS endereco,
                0 AS numero,
                '' AS cep,
                latitude::text AS latitude,
                longitude::text AS longitude,
                id_veiculo_3s AS "idEquipamento",
                id_veiculo_3s AS "idVeiculo",
                CASE WHEN bloqueio THEN 'Bloqueado' ELSE 'Liberado' END AS bloqueio,
                COALESCE(odometer, 0) AS odometer,
                0 AS hourmeter
            FROM embarques_simulacao
            ORDER BY placa, data_posicao DESC
        """)
    else:
        cur.execute("""
            SELECT
                id AS "idPosicao",
                'Simulado' AS frota,
                placa,
                'Simulador' AS modelo,
                NOW() AS data,
                COALESCE(velocidade, 0) AS velocidade,
                10 AS satelite,
                CASE WHEN ignicao THEN 'Ligada' ELSE 'Desligada' END AS ignicao,
                COALESCE(direcao, 'N') AS direcao,
                uf,
                cidade,
                COALESCE(bairro, '') AS bairro,
                COALESCE(endereco, '') AS endereco,
                0 AS numero,
                '' AS cep,
                latitude::text AS latitude,
                longitude::text AS longitude,
                id_veiculo_3s AS "idEquipamento",
                id_veiculo_3s AS "idVeiculo",
                CASE WHEN bloqueio THEN 'Bloqueado' ELSE 'Liberado' END AS bloqueio,
                COALESCE(odometer, 0) AS odometer,
                0 AS hourmeter
            FROM embarques_simulacao
            WHERE id_veiculo_3s = %s
            ORDER BY data_posicao DESC
            LIMIT 1
        """, (id_veiculo,))

    rows = cur.fetchall()
    cur.close()
    conn.close()
    _log(f'/ListaUltimaPosicaoVeiculos/{id_veiculo}', int((time.time() - t0) * 1000))
    return [dict(r) for r in rows]


if __name__ == '__main__':
    print('Veículos simulados:', len(lista_veiculos()))
    pos = lista_ultima_posicao(0)
    print(f'Posições atuais: {len(pos)}')
    if pos:
        print('Exemplo:', pos[0])
