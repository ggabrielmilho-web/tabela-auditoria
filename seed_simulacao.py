"""
Seed de simulação — cria 7 cargas com posições conhecidas pra testar o fluxo end-to-end.

Cenários:
  1. AAA1234 — Aberta, em Uberlândia (origem) ............. Estado inicial
  2. BBB5678 — Aberta, em Araguari (>5km de Uberlândia) ... Trigger saída automática
  3. CCC9012 — Em rota, em Manhuaçu (meio do caminho) ..... Polyline planejada vs percorrida
  4. DDD3456 — Em rota, em Vitória (cidade destino) ....... Chegada na cidade (📦)
  5. EEE7890 — Em rota, em Cariacica (>5km de Vitória) .... Trigger entrega automática
  6. FFF1111 — Entregue (ontem), em Goiânia ............... Carga histórica
  7. GGG2222 — Cancelada, em SP ........................... Status cancelada

CLI:
  python -X utf8 seed_simulacao.py             # cria/atualiza (idempotente)
  python -X utf8 seed_simulacao.py --reset     # apaga e refaz
  python -X utf8 seed_simulacao.py --avancar PLACA KM   # move N km na direção do destino
  python -X utf8 seed_simulacao.py --colocar PLACA LAT LNG CIDADE UF  # posiciona manualmente
"""

import os
import sys
import argparse
from datetime import datetime, timedelta
from math import radians, sin, cos, asin, sqrt, atan2, degrees
import psycopg2
from dotenv import load_dotenv

load_dotenv()

# Definição das cargas. Cada item: (placa, id_veiculo_3s, scenario)
CARGAS = [
    {
        'placa': 'AAA1234', 'id_veiculo_3s': 1001,
        'cliente_nome': 'CLIENTE TESTE 1',
        'origem_cidade': 'Uberlândia', 'origem_uf': 'MG',
        'destino_cidade': 'Vitória', 'destino_uf': 'ES',
        'motorista_nome': 'João da Silva', 'motorista_cpf': '11122233344',
        'motorista_telefone': '(34) 99999-1111',
        'status': 'Aberta', 'no_local_desde': None, 'entregue_auto': False, 'saida_auto': False,
        'posicao': {'cidade': 'Uberlândia', 'uf': 'MG', 'lat': -18.9141, 'lng': -48.2749, 'velocidade': 0, 'ignicao': False},
    },
    {
        'placa': 'BBB5678', 'id_veiculo_3s': 1002,
        'cliente_nome': 'CLIENTE TESTE 1',
        'origem_cidade': 'Uberlândia', 'origem_uf': 'MG',
        'destino_cidade': 'Vitória', 'destino_uf': 'ES',
        'motorista_nome': 'Pedro Oliveira', 'motorista_cpf': '22233344455',
        'motorista_telefone': '(34) 99999-2222',
        'status': 'Aberta', 'no_local_desde': None, 'entregue_auto': False, 'saida_auto': False,
        # Araguari/MG: -18.6478, -48.1862 (~35km de Uberlândia)
        'posicao': {'cidade': 'Araguari', 'uf': 'MG', 'lat': -18.6478, 'lng': -48.1862, 'velocidade': 65, 'ignicao': True, 'direcao': 'L'},
    },
    {
        'placa': 'CCC9012', 'id_veiculo_3s': 1003,
        'cliente_nome': 'CLIENTE TESTE 2',
        'origem_cidade': 'Uberlândia', 'origem_uf': 'MG',
        'destino_cidade': 'Vitória', 'destino_uf': 'ES',
        'motorista_nome': 'Carlos Mendes', 'motorista_cpf': '33344455566',
        'motorista_telefone': '(34) 99999-3333',
        'status': 'Em rota', 'no_local_desde': None, 'entregue_auto': False, 'saida_auto': True,
        'data_saida_real': datetime.utcnow() - timedelta(hours=8),
        # Manhuaçu/MG: -20.2580, -42.0339 (meio do caminho)
        'posicao': {'cidade': 'Manhuaçu', 'uf': 'MG', 'lat': -20.2580, 'lng': -42.0339, 'velocidade': 72, 'ignicao': True, 'direcao': 'L'},
    },
    {
        'placa': 'DDD3456', 'id_veiculo_3s': 1004,
        'cliente_nome': 'CLIENTE TESTE 2',
        'origem_cidade': 'Uberlândia', 'origem_uf': 'MG',
        'destino_cidade': 'Vitória', 'destino_uf': 'ES',
        'motorista_nome': 'Antônio Costa', 'motorista_cpf': '44455566677',
        'motorista_telefone': '(34) 99999-4444',
        'status': 'Em rota', 'no_local_desde': None, 'entregue_auto': False, 'saida_auto': True,
        'data_saida_real': datetime.utcnow() - timedelta(hours=14),
        # Vitória/ES: centro
        'posicao': {'cidade': 'Vitória', 'uf': 'ES', 'lat': -20.3155, 'lng': -40.3128, 'velocidade': 15, 'ignicao': True, 'direcao': 'L'},
    },
    {
        'placa': 'EEE7890', 'id_veiculo_3s': 1005,
        'cliente_nome': 'CLIENTE TESTE 3',
        'origem_cidade': 'Uberlândia', 'origem_uf': 'MG',
        'destino_cidade': 'Vitória', 'destino_uf': 'ES',
        'motorista_nome': 'Marcos Souza', 'motorista_cpf': '55566677788',
        'motorista_telefone': '(34) 99999-5555',
        'status': 'Em rota', 'no_local_desde': datetime.utcnow() - timedelta(hours=20),
        'entregue_auto': False, 'saida_auto': True,
        'data_saida_real': datetime.utcnow() - timedelta(hours=24),
        # Cariacica/ES: -20.2628, -40.4196 (~10km de Vitória)
        'posicao': {'cidade': 'Cariacica', 'uf': 'ES', 'lat': -20.2628, 'lng': -40.4196, 'velocidade': 50, 'ignicao': True, 'direcao': 'O'},
    },
    {
        'placa': 'FFF1111', 'id_veiculo_3s': 1006,
        'cliente_nome': 'CLIENTE TESTE 4',
        'origem_cidade': 'Goiânia', 'origem_uf': 'GO',
        'destino_cidade': 'São Paulo', 'destino_uf': 'SP',
        'motorista_nome': 'Roberto Lima', 'motorista_cpf': '66677788899',
        'motorista_telefone': '(62) 99999-6666',
        'status': 'Entregue', 'no_local_desde': None, 'entregue_auto': True, 'saida_auto': True,
        'data_saida_real': datetime.utcnow() - timedelta(days=2),
        'data_conclusao': datetime.utcnow() - timedelta(days=1),
        # Goiânia (caminhão voltou)
        'posicao': {'cidade': 'Goiânia', 'uf': 'GO', 'lat': -16.6869, 'lng': -49.2648, 'velocidade': 0, 'ignicao': False},
    },
    {
        'placa': 'GGG2222', 'id_veiculo_3s': 1007,
        'cliente_nome': 'CLIENTE TESTE 5',
        'origem_cidade': 'São Paulo', 'origem_uf': 'SP',
        'destino_cidade': 'Rio de Janeiro', 'destino_uf': 'RJ',
        'motorista_nome': 'Fernando Alves', 'motorista_cpf': '77788899900',
        'motorista_telefone': '(11) 99999-7777',
        'status': 'Cancelada', 'no_local_desde': None, 'entregue_auto': False, 'saida_auto': False,
        # SP capital
        'posicao': {'cidade': 'São Paulo', 'uf': 'SP', 'lat': -23.5329, 'lng': -46.6395, 'velocidade': 0, 'ignicao': False},
    },
]


def _conn():
    return psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        port=os.getenv('DB_PORT', '5432'),
        dbname=os.getenv('DB_NAME', 'postgres'),
        user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD', ''),
    )


def _admin_id(cur):
    cur.execute("SELECT id, nome FROM auditoria_users WHERE role='admin' ORDER BY id LIMIT 1")
    return cur.fetchone()


def _upsert_cliente(cur, nome):
    cur.execute(
        "INSERT INTO clientes (nome, importado_em) VALUES (%s, NULL) "
        "ON CONFLICT ((LOWER(TRIM(nome)))) DO NOTHING RETURNING id",
        (nome,)
    )
    r = cur.fetchone()
    if r:
        return r[0]
    cur.execute("SELECT id FROM clientes WHERE LOWER(TRIM(nome))=LOWER(TRIM(%s))", (nome,))
    return cur.fetchone()[0]


def _mapear_veiculo_rastreio(cur, placa, id_veiculo_3s):
    cur.execute("""
        INSERT INTO embarques_veiculos_rastreio (placa, id_veiculo_3s, frota, modelo, tipo)
        VALUES (%s, %s, 'Simulado', 'Simulador', 'Cavalo')
        ON CONFLICT (placa) DO UPDATE SET id_veiculo_3s = EXCLUDED.id_veiculo_3s,
            sincronizado_em = NOW()
    """, (placa, id_veiculo_3s))


def _centroide(cur, cidade, uf):
    cur.execute("""
        SELECT latitude, longitude FROM municipios_ibge
        WHERE cidade_normalizada = UPPER(TRANSLATE(%s,
            'áéíóúâêîôûãõàèìòùäëïöüçÁÉÍÓÚÂÊÎÔÛÃÕÀÈÌÒÙÄËÏÖÜÇ',
            'aeiouaeiouaoaeiouaeiouclAEIOUAEIOUAOAEIOUAEIOUC')) AND uf = %s
    """, (cidade.strip(), uf.upper().strip()))
    r = cur.fetchone()
    if r:
        return float(r[0]), float(r[1])
    return None, None


def _criar_ou_atualizar_carga(cur, admin_id, admin_nome, c):
    cliente_id = _upsert_cliente(cur, c['cliente_nome'])

    olat, olng = _centroide(cur, c['origem_cidade'], c['origem_uf'])
    dlat, dlng = _centroide(cur, c['destino_cidade'], c['destino_uf'])

    # Carga já existe pelo cavalo_placa?
    cur.execute("SELECT id FROM embarques_cargas WHERE cavalo_placa = %s LIMIT 1", (c['placa'],))
    r = cur.fetchone()

    data_conclusao = c.get('data_conclusao')
    data_saida_real = c.get('data_saida_real')

    if r:
        carga_id = r[0]
        cur.execute("""
            UPDATE embarques_cargas SET
                status = %s,
                no_local_desde = %s,
                entregue_auto = %s,
                saida_auto = %s,
                data_saida_real = %s,
                data_conclusao = %s,
                origem_latitude = %s,
                origem_longitude = %s,
                atualizado_em = NOW()
            WHERE id = %s
        """, (c['status'], c.get('no_local_desde'), c['entregue_auto'], c['saida_auto'],
              data_saida_real, data_conclusao, olat, olng, carga_id))
    else:
        cur.execute("""
            INSERT INTO embarques_cargas (
                tipo_operacao, status,
                cliente_id, cliente_nome,
                origem_cidade, origem_uf,
                motorista_nome, motorista_cpf, motorista_telefone,
                cavalo_placa, cavalo_tipo, cavalo_marca_modelo, cavalo_proprietario, cavalo_eh_rizza,
                data_carregamento,
                origem_latitude, origem_longitude,
                no_local_desde, entregue_auto, saida_auto, data_saida_real, data_conclusao,
                criado_em, criado_por_id, criado_por_nome
            ) VALUES (
                'Frota', %s,
                %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, 'Cavalo', 'Volvo FH 540', 'RIZZA TRANSPORTES LTDA', TRUE,
                CURRENT_DATE,
                %s, %s,
                %s, %s, %s, %s, %s,
                NOW(), %s, %s
            ) RETURNING id
        """, (
            c['status'],
            cliente_id, c['cliente_nome'],
            c['origem_cidade'], c['origem_uf'],
            c['motorista_nome'], c['motorista_cpf'], c['motorista_telefone'],
            c['placa'],
            olat, olng,
            c.get('no_local_desde'), c['entregue_auto'], c['saida_auto'],
            data_saida_real, data_conclusao,
            admin_id, admin_nome
        ))
        carga_id = cur.fetchone()[0]
        # Gera número
        cur.execute("""
            UPDATE embarques_cargas
            SET numero = 'C-' || EXTRACT(YEAR FROM criado_em)::int || '-' || LPAD(id::text, 6, '0')
            WHERE id = %s
        """, (carga_id,))

    # Destinos: apaga e recria
    cur.execute("DELETE FROM embarques_cargas_destinos WHERE carga_id = %s", (carga_id,))
    cur.execute("""
        INSERT INTO embarques_cargas_destinos (carga_id, ordem, cidade, uf, latitude, longitude)
        VALUES (%s, 1, %s, %s, %s, %s)
    """, (carga_id, c['destino_cidade'], c['destino_uf'], dlat, dlng))

    # Mapeamento rastreio
    _mapear_veiculo_rastreio(cur, c['placa'], c['id_veiculo_3s'])

    # Posição inicial em embarques_simulacao
    pos = c['posicao']
    cur.execute("DELETE FROM embarques_simulacao WHERE placa = %s", (c['placa'],))
    cur.execute("""
        INSERT INTO embarques_simulacao (
            placa, id_veiculo_3s, data_posicao, latitude, longitude,
            velocidade, ignicao, direcao, uf, cidade, bairro, endereco, bloqueio, odometer
        ) VALUES (%s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, '', '', FALSE, 100000)
    """, (
        c['placa'], c['id_veiculo_3s'], pos['lat'], pos['lng'],
        pos.get('velocidade', 0), pos.get('ignicao', False),
        pos.get('direcao', 'N'), pos['uf'], pos['cidade']
    ))

    return carga_id


def cmd_seed(reset=False):
    conn = _conn()
    cur = conn.cursor()
    admin = _admin_id(cur)
    if not admin:
        print('❌ Nenhum admin em auditoria_users. Crie um primeiro.')
        return
    admin_id, admin_nome = admin

    if reset:
        # Apaga só as cargas e posições simuladas com placas conhecidas
        placas = tuple(c['placa'] for c in CARGAS)
        cur.execute("DELETE FROM embarques_cargas_log WHERE carga_id IN (SELECT id FROM embarques_cargas WHERE cavalo_placa IN %s)", (placas,))
        cur.execute("DELETE FROM embarques_cargas_destinos WHERE carga_id IN (SELECT id FROM embarques_cargas WHERE cavalo_placa IN %s)", (placas,))
        cur.execute("DELETE FROM embarques_cargas_rastreio_kpi WHERE placa IN %s", (placas,))
        cur.execute("DELETE FROM embarques_posicoes_historico WHERE placa IN %s", (placas,))
        cur.execute("DELETE FROM embarques_posicoes_atuais WHERE placa IN %s", (placas,))
        cur.execute("DELETE FROM embarques_simulacao WHERE placa IN %s", (placas,))
        cur.execute("DELETE FROM embarques_cargas WHERE cavalo_placa IN %s", (placas,))
        cur.execute("DELETE FROM embarques_veiculos_rastreio WHERE placa IN %s", (placas,))
        print('  ↻ Reset feito.')

    for c in CARGAS:
        carga_id = _criar_ou_atualizar_carga(cur, admin_id, admin_nome, c)
        print(f"  ✓ {c['placa']} → carga #{carga_id} ({c['status']}) — em {c['posicao']['cidade']}/{c['posicao']['uf']}")

    conn.commit()
    cur.close()
    conn.close()
    print(f"\n✅ Seed concluído: {len(CARGAS)} cargas + posições simuladas.")


def _avancar(placa, km):
    """Move a placa N km na direção do destino (calculado em linha reta)."""
    conn = _conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT s.latitude, s.longitude, s.id_veiculo_3s, c.id
        FROM embarques_simulacao s
        LEFT JOIN embarques_cargas c ON c.cavalo_placa = s.placa
        WHERE s.placa = %s
        ORDER BY s.data_posicao DESC LIMIT 1
    """, (placa,))
    r = cur.fetchone()
    if not r:
        print(f'❌ Placa {placa} não encontrada na simulação.')
        return
    lat, lng, idv, carga_id = float(r[0]), float(r[1]), r[2], r[3]

    if carga_id is None:
        print(f'❌ Placa {placa} sem carga vinculada.')
        return

    cur.execute("""
        SELECT latitude, longitude, cidade, uf FROM embarques_cargas_destinos
        WHERE carga_id = %s ORDER BY ordem DESC LIMIT 1
    """, (carga_id,))
    dst = cur.fetchone()
    if not dst or dst[0] is None:
        print('❌ Destino sem coordenadas.')
        return
    dlat, dlng = float(dst[0]), float(dst[1])

    # Bearing inicial → next point at km distance
    R = 6371.0
    lat1, lng1 = radians(lat), radians(lng)
    lat2, lng2 = radians(dlat), radians(dlng)
    dLng = lng2 - lng1
    bearing = atan2(sin(dLng) * cos(lat2), cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(dLng))

    d = km / R
    new_lat = asin(sin(lat1) * cos(d) + cos(lat1) * sin(d) * cos(bearing))
    new_lng = lng1 + atan2(sin(bearing) * sin(d) * cos(lat1), cos(d) - sin(lat1) * sin(new_lat))

    new_lat_deg = degrees(new_lat)
    new_lng_deg = degrees(new_lng)

    # Resolve cidade via centroide próximo (lookup IBGE simplificado: cidade do destino se < 50 km, else 'Em trânsito')
    R2 = 6371.0
    def haversine(a, b, c, d):
        la1, ln1, la2, ln2 = map(radians, [a, b, c, d])
        h = sin((la2 - la1) / 2) ** 2 + cos(la1) * cos(la2) * sin((ln2 - ln1) / 2) ** 2
        return 2 * R2 * asin(sqrt(h))

    dist_destino = haversine(new_lat_deg, new_lng_deg, dlat, dlng)
    if dist_destino < 30:
        cidade, uf = dst[2], dst[3]
    else:
        # Acha cidade mais próxima
        cur.execute("""
            SELECT cidade, uf,
                   (latitude - %s)^2 + (longitude - %s)^2 AS dist2
            FROM municipios_ibge ORDER BY dist2 LIMIT 1
        """, (new_lat_deg, new_lng_deg))
        rr = cur.fetchone()
        cidade, uf = (rr[0], rr[1]) if rr else ('Desconhecido', 'XX')

    cur.execute("""
        INSERT INTO embarques_simulacao (
            placa, id_veiculo_3s, data_posicao, latitude, longitude,
            velocidade, ignicao, direcao, uf, cidade, bairro, endereco, bloqueio, odometer
        ) VALUES (%s, %s, NOW(), %s, %s, 70, TRUE, 'L', %s, %s, '', '', FALSE, 100000)
    """, (placa, idv, new_lat_deg, new_lng_deg, uf, cidade))

    conn.commit()
    cur.close()
    conn.close()
    print(f'✓ {placa} avançado {km} km → ({new_lat_deg:.4f}, {new_lng_deg:.4f}) em {cidade}/{uf}')


def _colocar(placa, lat, lng, cidade, uf):
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT id_veiculo_3s FROM embarques_simulacao WHERE placa=%s ORDER BY data_posicao DESC LIMIT 1", (placa,))
    r = cur.fetchone()
    if not r:
        print(f'❌ Placa {placa} sem simulação prévia.')
        return
    idv = r[0]
    cur.execute("""
        INSERT INTO embarques_simulacao (
            placa, id_veiculo_3s, data_posicao, latitude, longitude,
            velocidade, ignicao, direcao, uf, cidade, bairro, endereco, bloqueio, odometer
        ) VALUES (%s, %s, NOW(), %s, %s, 0, TRUE, 'N', %s, %s, '', '', FALSE, 100000)
    """, (placa, idv, lat, lng, uf.upper(), cidade))
    conn.commit()
    cur.close()
    conn.close()
    print(f'✓ {placa} colocado em ({lat}, {lng}) — {cidade}/{uf}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reset', action='store_true', help='Apaga cargas/posições e refaz')
    parser.add_argument('--avancar', nargs=2, metavar=('PLACA', 'KM'), help='Move placa N km em direção ao destino')
    parser.add_argument('--colocar', nargs=5, metavar=('PLACA', 'LAT', 'LNG', 'CIDADE', 'UF'), help='Posiciona placa manualmente')
    args = parser.parse_args()

    if args.avancar:
        _avancar(args.avancar[0], float(args.avancar[1]))
    elif args.colocar:
        _colocar(args.colocar[0], float(args.colocar[1]), float(args.colocar[2]), args.colocar[3], args.colocar[4])
    else:
        cmd_seed(reset=args.reset)


if __name__ == '__main__':
    main()
