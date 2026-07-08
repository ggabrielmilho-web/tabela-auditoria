# -*- coding: utf-8 -*-
"""
Lista (SÓ LEITURA) as cargas ENTREGUES cuja CHEGADA foi registrada errada:
o no_local_desde caiu longe do destino (bug do nome do 3S) ou está NULL — e que
podem ser corrigidas por posição. Não grava nada.

Uso (dentro do container do app):
  python _lista_torto.py
"""
from datetime import datetime, timedelta, time as _time

import server
import rastreamento_worker as W
import geocoding

RAIO = float(__import__('os').getenv('RASTREAMENTO_RAIO_CHEGADA_DESTINO', '20'))


def main():
    conn = server.get_db()
    cur = conn.cursor()
    cur.execute("""SELECT id, numero, no_local_desde, data_carregamento, data_conclusao,
                          inicio_viagem, cavalo_placa, carreta1_placa, carreta2_placa
                   FROM embarques_cargas WHERE status='Entregue' ORDER BY id""")
    cargas = cur.fetchall()

    afetadas = []
    total = 0
    sem_dados = 0
    for (cid, num, nld, dcarr, dconc, iniv, cav, c1, c2) in cargas:
        total += 1
        cur.execute("""SELECT latitude, longitude, cidade, uf FROM embarques_cargas_destinos
                       WHERE carga_id=%s ORDER BY ordem DESC LIMIT 1""", (cid,))
        d = cur.fetchone()
        if not d or d[0] is None:
            sem_dados += 1; continue
        dest_lat, dest_lng, dcid, duf = float(d[0]), float(d[1]), d[2], d[3]

        placa = W._placa_tracking(cav, c1, c2, cur)
        if not placa:
            sem_dados += 1; continue

        if dcarr:
            base = dcarr if isinstance(dcarr, datetime) else datetime.combine(dcarr, _time())
            inicio = base - timedelta(hours=12)
        else:
            inicio = iniv or (datetime.utcnow() - timedelta(days=15))
        fim = dconc or datetime.utcnow()

        cur.execute("""SELECT data_posicao, latitude, longitude, velocidade
                       FROM embarques_posicoes_historico
                       WHERE placa=%s AND data_posicao BETWEEN %s AND %s
                       ORDER BY data_posicao""", (placa, inicio, fim))
        traj = [{'lat': float(la), 'lng': float(ln), 'velocidade': ve, 'data': dp.isoformat() + 'Z', '_dt': dp}
                for (dp, la, ln, ve) in cur.fetchall()]
        if not traj:
            sem_dados += 1; continue

        # distância do registro ATUAL ao destino (posição mais próxima no tempo do no_local_desde)
        if nld is not None:
            p = min(traj, key=lambda x: abs((x['_dt'] - nld).total_seconds()))
            dist_reg = geocoding.km_entre(p['lat'], p['lng'], dest_lat, dest_lng)
        else:
            dist_reg = None

        # o que a correção por posição daria
        idx = server._indice_chegada_destino(traj, dest_lat, dest_lng)
        dist_corr = geocoding.km_entre(traj[idx]['lat'], traj[idx]['lng'], dest_lat, dest_lng) if idx is not None else None

        torto = (nld is None) or (dist_reg is not None and dist_reg > RAIO)
        if torto and idx is not None:
            afetadas.append((cid, num, f"{dcid}/{duf}", None if dist_reg is None else round(dist_reg, 1), round(dist_corr, 1)))

    print(f"Entregues analisadas: {total}  |  sem dados p/ checar (sem destino/placa/GPS): {sem_dados}")
    print(f"Com chegada ERRADA (registro NULL ou > {RAIO:.0f} km do destino) e corrigível: {len(afetadas)}\n")
    if afetadas:
        print(f"{'id':>6}  {'numero':<16} {'destino':<20} {'registrado':>12}  {'corrigido':>10}")
        print("-" * 70)
        for cid, num, dst, dr, dc in sorted(afetadas, key=lambda x: -(x[3] if x[3] is not None else 99999)):
            reg = 'NULL' if dr is None else f'{dr} km'
            print(f"{cid:>6}  {num:<16} {dst:<20} {reg:>12}  {dc} km")
        print("\nPara corrigir uma (preview e depois --apply):")
        print("  docker exec $CID python /app/_reconsolida_kpi.py <id>")
        print("  docker exec $CID python /app/_reconsolida_kpi.py <id> --apply")
    else:
        print("Nenhuma carga entregue com chegada errada. 🎉")
    cur.close(); conn.close()


if __name__ == '__main__':
    main()
