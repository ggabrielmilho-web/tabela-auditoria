# -*- coding: utf-8 -*-
"""
Reconsolida em LOTE as cargas ENTREGUES com chegada errada E corrigível para PERTO
do destino ("ganhos claros"). Corrige no_local_desde (por posição) + reconsolida KPI.

Seleção automática: status=Entregue, chegada torta (no_local_desde NULL ou > raio do
destino) E o ponto corrigido fica a <= --max km do destino (default 6 km). Assim os
casos "fuzzy" de metrópole (corrigido ainda alto) ficam de fora — trate um a um.

Seguro e REVERSÍVEL:
  - DRY-RUN por padrão: aplica na transação p/ dar o preview e desfaz (rollback);
  - --apply: imprime E salva o UNDO de TODAS antes de commitar (undo_lote_*.sql).

Uso (dentro do container):
  python _reconsolida_lote.py                 # dry-run (preview), limite 6 km
  python _reconsolida_lote.py --max 5         # dry-run, limite custom
  python _reconsolida_lote.py --apply         # aplica + salva UNDO
"""
import os
import sys
from datetime import datetime, timedelta, time as _time

import server
import rastreamento_worker as W
import geocoding

RAIO = float(os.getenv('RASTREAMENTO_RAIO_CHEGADA_DESTINO', '20'))


def _arg(flag, default):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def _sql_val(v):
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v) + "'"


def _janela(dcarr, iniv):
    if dcarr:
        base = dcarr if isinstance(dcarr, datetime) else datetime.combine(dcarr, _time())
        return base - timedelta(hours=12)
    return iniv or (datetime.utcnow() - timedelta(days=15))


def main():
    apply = '--apply' in sys.argv
    limite = float(_arg('--max', '6'))
    conn = server.get_db()
    cur = conn.cursor()
    cur.execute("""SELECT id, numero, no_local_desde, data_carregamento, data_conclusao,
                          inicio_viagem, cavalo_placa, carreta1_placa, carreta2_placa
                   FROM embarques_cargas WHERE status='Entregue' ORDER BY id""")
    cargas = cur.fetchall()

    selec = []
    for (cid, num, nld, dcarr, dconc, iniv, cav, c1, c2) in cargas:
        cur.execute("""SELECT latitude, longitude, cidade, uf FROM embarques_cargas_destinos
                       WHERE carga_id=%s ORDER BY ordem DESC LIMIT 1""", (cid,))
        d = cur.fetchone()
        if not d or d[0] is None:
            continue
        dest_lat, dest_lng, dcid, duf = float(d[0]), float(d[1]), d[2], d[3]
        placa = W._placa_tracking(cav, c1, c2, cur)
        if not placa:
            continue
        inicio = _janela(dcarr, iniv)
        fim = dconc or datetime.utcnow()
        cur.execute("""SELECT data_posicao, latitude, longitude, velocidade
                       FROM embarques_posicoes_historico
                       WHERE placa=%s AND data_posicao BETWEEN %s AND %s ORDER BY data_posicao""",
                    (placa, inicio, fim))
        traj = [{'lat': float(la), 'lng': float(ln), 'velocidade': ve, 'data': dp.isoformat() + 'Z', '_dt': dp}
                for (dp, la, ln, ve) in cur.fetchall()]
        if not traj:
            continue
        if nld is not None:
            p = min(traj, key=lambda x: abs((x['_dt'] - nld).total_seconds()))
            dist_reg = geocoding.km_entre(p['lat'], p['lng'], dest_lat, dest_lng)
        else:
            dist_reg = None
        idx = server._indice_chegada_destino(traj, dest_lat, dest_lng)
        if idx is None:
            continue
        dist_corr = geocoding.km_entre(traj[idx]['lat'], traj[idx]['lng'], dest_lat, dest_lng)
        torto = (nld is None) or (dist_reg is not None and dist_reg > RAIO)
        if torto and dist_corr is not None and dist_corr <= limite:
            cur.execute("""SELECT distancia_metros, velocidade_max, velocidade_media,
                                  tempo_movimento_seg, tempo_parado_seg
                           FROM embarques_cargas_rastreio_kpi WHERE carga_id=%s""", (cid,))
            kpi = cur.fetchone()
            selec.append((cid, num, f"{dcid}/{duf}", dist_reg, round(dist_corr, 1), nld, traj[idx]['_dt'], kpi))

    if not selec:
        print("Nenhuma carga se enquadra (ganho claro). Nada a fazer.")
        conn.rollback(); cur.close(); conn.close(); return

    print(f"Ganhos claros selecionados (corrigido <= {limite:.0f} km): {len(selec)}\n")
    print(f"{'id':>6}  {'numero':<16} {'destino':<18} {'reg':>8} {'corr':>7}   km_perc: atual -> novo")
    print("-" * 80)
    undo = []
    for (cid, num, dst, dr, dc, nld, nld_novo, kpi) in selec:
        undo.append(f"UPDATE embarques_cargas SET no_local_desde = {_sql_val(nld)} WHERE id = {cid};")
        if kpi:
            undo.append("UPDATE embarques_cargas_rastreio_kpi SET "
                        f"distancia_metros={_sql_val(kpi[0])}, velocidade_max={_sql_val(kpi[1])}, "
                        f"velocidade_media={_sql_val(kpi[2])}, tempo_movimento_seg={_sql_val(kpi[3])}, "
                        f"tempo_parado_seg={_sql_val(kpi[4])} WHERE carga_id = {cid};")
        else:
            undo.append(f"DELETE FROM embarques_cargas_rastreio_kpi WHERE carga_id = {cid};")
        cur.execute("UPDATE embarques_cargas SET no_local_desde=%s WHERE id=%s", (nld_novo, cid))
        W._consolidar_kpi(cur, cid, final=True)
        cur.execute("SELECT distancia_metros FROM embarques_cargas_rastreio_kpi WHERE carga_id=%s", (cid,))
        novo = cur.fetchone()
        km_a = (kpi[0] or 0) / 1000 if kpi else 0
        km_n = (novo[0] or 0) / 1000 if novo else 0
        reg = 'NULL' if dr is None else f'{round(dr, 1)}'
        print(f"{cid:>6}  {num:<16} {dst:<18} {reg:>8} {dc:>7}   {km_a:>7.1f} -> {km_n:.1f} km")

    if not apply:
        conn.rollback()
        print(f"\n[DRY-RUN] preview acima ({len(selec)} cargas). NADA foi gravado (rollback).")
        print("Rode com --apply para aplicar (vai salvar o UNDO de todas antes).")
        cur.close(); conn.close(); return

    fname = f"undo_lote_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.sql"
    bloco = "-- UNDO do lote. Cole no pgAdmin p/ reverter TUDO.\nBEGIN;\n" + "\n".join(undo) + "\nCOMMIT;\n"
    with open(fname, 'w', encoding='utf-8') as f:
        f.write(bloco)
    conn.commit()
    print(f"\nAPLICADO {len(selec)} cargas e commitado.")
    print(f"UNDO salvo em /app/{fname} — retire com:  docker cp $CID:/app/{fname} .")
    print("\n===== UNDO (copie e guarde tambem daqui) =====")
    print(bloco)
    print("==============================================")
    cur.close(); conn.close()


if __name__ == '__main__':
    main()
