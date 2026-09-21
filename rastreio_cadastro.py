# -*- coding: utf-8 -*-
"""Cadastro de veículos rastreados — o sync, e quando ele precisa acontecer.

`embarques_veiculos_rastreio` é uma CÓPIA LOCAL do /ListaVeiculos da 3S. A posição não
depende dela (o worker grava em `embarques_posicoes_historico` tudo o que o polling da conta
devolve); quem a exige é um leitor só — a tela do mapa, via `_placa_tracking`.

Até 21/09/2026 essa cópia era atualizada apenas por um botão no Admin, e não havia sinal
nenhum de que ela tinha envelhecido: veículo que entrou na conta da 3S depois do último sync
ficava invisível para a tela, com posição gravada e viagem correndo. Foi o que aconteceu com
a C-2026-001011 — carreta `TZC9G24` com 352 pontos na janela, chegando a 5 km do destino, e o
mapa dizendo "Sem rastreio".

Duas peças aqui, e a segunda é a resposta para "como eu sei a hora de sincronizar?":

  `sincronizar(conn)`  o upsert, extraído da rota do Admin — rota e thread chamam ESTA
                       função, para não existirem duas noções de sync em dois arquivos (§20.6);
  `lacuna(cur)`        as placas que o worker está vendo e o cadastro não conhece. É uma
                       consulta LOCAL e barata: dá para perguntar de 30 em 30 minutos sem
                       gastar cota da 3S, e só chamar a API quando há o que buscar.

O laço fica atrás de `RASTREAMENTO_SYNC_AUTO` (nasce DESLIGADO) e tem dois gatilhos, pelo
mesmo motivo do robô na §27.12 — não trocar um mecanismo por outro:

  * LACUNA    placa nova aparecendo no polling -> sincroniza agora;
  * GARANTIA  passou `RASTREAMENTO_SYNC_MAX_H` (24 h) desde o último sync -> sincroniza.
              Pega o caso que a lacuna não vê: veículo que mudou de placa ou de equipamento
              sem placa nova aparecer.
"""
import os
import logging
import time as _time

import placas
import tres_s_client

_logger = logging.getLogger('rastreio_cadastro')


def ligado():
    """`RASTREAMENTO_SYNC_AUTO=true` liga a thread. Ligar pela CLI
    (`docker service update --env-add`), nunca pelo stack do Portainer (§22.10)."""
    return str(os.getenv('RASTREAMENTO_SYNC_AUTO', 'false')).strip().lower() in ('1', 'true', 'sim', 'yes')


def _intervalo_min():
    try:
        return max(5, int(os.getenv('RASTREAMENTO_SYNC_INTERVALO_MIN', '30')))
    except ValueError:
        return 30


def _max_h():
    try:
        return max(1, int(os.getenv('RASTREAMENTO_SYNC_MAX_H', '24')))
    except ValueError:
        return 24


def sincronizar(conn):
    """UPSERT do /ListaVeiculos da 3S em `embarques_veiculos_rastreio`.

    Movido da rota `POST /api/rastreamento/sync-veiculos` sem mudar uma linha de regra: a
    identidade do veículo é o `id_veiculo_3s` (a placa muda de antiga para Mercosul), placa
    presa em outra linha é liberada antes, e a posição ATUAL órfã da placa antiga é apagada
    para o veículo não aparecer 2× no mapa. Não toca em `embarques_posicoes_historico`, que
    é fato bruto (§23), e não remove veículo que saiu da lista.

    Devolve (total, novos, atualizados)."""
    veiculos = tres_s_client.lista_veiculos()
    cur = conn.cursor()
    novos = atualizados = 0
    for v in veiculos:
        placa = (v.get('placa') or '').strip().upper()
        id_veiculo = v.get('idVeiculo')
        if not placa or not id_veiculo:
            continue
        cur.execute("SELECT 1 FROM embarques_veiculos_rastreio WHERE id_veiculo_3s=%s", (id_veiculo,))
        existe = cur.fetchone() is not None
        cur.execute("DELETE FROM embarques_veiculos_rastreio WHERE placa=%s AND id_veiculo_3s<>%s",
                    (placa, id_veiculo))
        cur.execute("""
            INSERT INTO embarques_veiculos_rastreio
                (placa, id_veiculo_3s, id_equipamento, frota, modelo, tipo, sincronizado_em)
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (id_veiculo_3s) DO UPDATE SET
                placa = EXCLUDED.placa,
                id_equipamento = EXCLUDED.id_equipamento,
                frota = EXCLUDED.frota,
                modelo = EXCLUDED.modelo,
                tipo = EXCLUDED.tipo,
                sincronizado_em = NOW()
        """, (placa, id_veiculo, v.get('idEquipamento'), v.get('frota'),
              v.get('modelo'), v.get('tipo')))
        cur.execute("DELETE FROM embarques_posicoes_atuais WHERE id_veiculo_3s=%s AND placa<>%s",
                    (id_veiculo, placa))
        if existe:
            atualizados += 1
        else:
            novos += 1
    conn.commit()
    cur.close()
    return len(veiculos), novos, atualizados


def lacuna(cur, dias=7):
    """Placas que o worker viu e o cadastro não conhece — só consulta local.

    Sai de `embarques_posicoes_atuais` (o worker a atualiza a cada ciclo) pelo mesmo motivo
    do `_veiculos_para_backfill`: é o que a 3S está devolvendo AGORA, sem gastar chamada.
    A comparação é pelas DUAS grafias — 42 das 94 placas vêm da 3S na grafia antiga e a
    carga guarda a Mercosul; comparar cru acusaria lacuna que não existe."""
    cur.execute("SELECT placa FROM embarques_veiculos_rastreio")
    cad = set()
    for (p,) in cur.fetchall():
        cad.update(placas.grafias(str(p).strip().upper()))
    cur.execute("""SELECT placa, MAX(data_posicao) FROM embarques_posicoes_atuais
                    WHERE placa <> '' AND data_posicao >= NOW() - (%s || ' days')::interval
                    GROUP BY 1 ORDER BY 1""", (str(dias),))
    return [(p, u) for p, u in cur.fetchall()
            if not any(g in cad for g in placas.grafias(str(p).strip().upper()))]


def _horas_desde_sync(cur):
    cur.execute("SELECT EXTRACT(EPOCH FROM (NOW() - MAX(sincronizado_em)))/3600 "
                "FROM embarques_veiculos_rastreio")
    r = cur.fetchone()
    return float(r[0]) if r and r[0] is not None else None


def deve_sincronizar(faltantes, horas_desde, max_h):
    """Função PURA, para poder testar sem rede nem banco. Devolve (bool, motivo)."""
    if faltantes:
        return True, f'lacuna: {len(faltantes)} placa(s) com posição fora do cadastro'
    if horas_desde is None:
        return True, 'cadastro nunca sincronizado'
    if horas_desde >= max_h:
        return True, f'garantia: {horas_desde:.0f} h desde o último sync'
    return False, ''


def loop():
    """Thread do servidor. Falha nunca derruba o laço: o pior caso é o cadastro ficar velho
    mais um ciclo, que é exatamente o estado de antes desta função existir."""
    from server import get_db
    intervalo = _intervalo_min() * 60
    while True:
        try:
            conn = get_db()
            try:
                cur = conn.cursor()
                faltantes = lacuna(cur)
                horas = _horas_desde_sync(cur)
                cur.close()
                rodar, motivo = deve_sincronizar(faltantes, horas, _max_h())
                if rodar:
                    total, novos, atualizados = sincronizar(conn)
                    print(f'✅ Cadastro de rastreio sincronizado ({motivo}) — {total} veículos '
                          f'· {novos} novo(s) · {atualizados} atualizado(s)'
                          + (f' · placas que faltavam: {", ".join(p for p, _u in faltantes)}'
                             if faltantes else ''))
            finally:
                conn.close()
        except Exception as e:
            print(f'⚠️  Cadastro de rastreio: falha no sync automático: {e}')
        _time.sleep(intervalo)
