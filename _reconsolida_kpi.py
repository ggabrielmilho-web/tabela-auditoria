# -*- coding: utf-8 -*-
"""
Reconsolida uma carga ENTREGUE cujo no_local_desde foi cravado errado (por nome do 3S):
  1) recalcula no_local_desde por POSIÇÃO (mesma lógica do fix);
  2) reconsolida o KPI (km percorridos, vel., tempo) com a janela correta.

Seguro e REVERSÍVEL:
  - roda em DRY-RUN por padrão (não grava nada);
  - com --apply, imprime o SQL de UNDO ANTES de gravar (guarde p/ reverter);
  - altera só 2 linhas: embarques_cargas.no_local_desde e a linha de KPI da carga.

Uso (dentro do container do app):
  python _reconsolida_kpi.py 173            # DRY-RUN (só mostra)
  python _reconsolida_kpi.py 173 --apply    # aplica (imprime UNDO antes)
"""
import sys
from datetime import datetime, timedelta, time as _time

import server
import rastreamento_worker as W
import geocoding


def _sql_val(v):
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v) + "'"


def _dist_ponto(traj, dt, dest_lat, dest_lng):
    """Distância (km) ao destino do ponto do trajeto mais próximo no tempo de `dt`."""
    if dt is None or not traj or dest_lat is None:
        return None
    melhor = min(traj, key=lambda p: abs((p['_dt'] - dt).total_seconds()))
    return round(geocoding.km_entre(melhor['lat'], melhor['lng'], dest_lat, dest_lng), 1)


def main():
    if len(sys.argv) < 2 or not sys.argv[1].isdigit():
        print("uso: python _reconsolida_kpi.py <carga_id> [--apply]")
        return
    carga_id = int(sys.argv[1])
    apply = '--apply' in sys.argv

    conn = server.get_db()
    cur = conn.cursor()
    try:
        cur.execute("""SELECT numero, status, no_local_desde, data_carregamento, data_conclusao,
                              inicio_viagem, cavalo_placa, carreta1_placa, carreta2_placa
                       FROM embarques_cargas WHERE id=%s""", (carga_id,))
        r = cur.fetchone()
        if not r:
            print(f"carga {carga_id} nao encontrada"); return
        numero, status, nld_atual, dcarr, dconc, iniv, cav, c1, c2 = r
        if status != 'Entregue':
            print(f"carga {numero} esta '{status}', nao 'Entregue' — abortando (so reconsolida entregues).")
            return

        cur.execute("""SELECT distancia_metros, velocidade_max, velocidade_media,
                              tempo_movimento_seg, tempo_parado_seg
                       FROM embarques_cargas_rastreio_kpi WHERE carga_id=%s""", (carga_id,))
        kpi_atual = cur.fetchone()

        cur.execute("""SELECT latitude, longitude FROM embarques_cargas_destinos
                       WHERE carga_id=%s ORDER BY ordem DESC LIMIT 1""", (carga_id,))
        d = cur.fetchone()
        dest_lat, dest_lng = (float(d[0]), float(d[1])) if d and d[0] is not None else (None, None)

        placa = W._placa_tracking(cav, c1, c2, cur)

        # Janela igual à do trajeto/consolidação
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
        traj = [{'data': dp.isoformat() + 'Z', 'lat': float(la), 'lng': float(ln),
                 'velocidade': ve, '_dt': dp} for (dp, la, ln, ve) in cur.fetchall()]

        idx = server._indice_chegada_destino(traj, dest_lat, dest_lng) if (traj and dest_lat is not None) else None
        nld_novo = traj[idx]['_dt'] if idx is not None else None

        print(f"=== Carga {numero} (id {carga_id}) — {status} ===")
        print(f"placa rastreada: {placa} | destino: {dest_lat},{dest_lng} | pontos no trajeto: {len(traj)}")
        print(f"no_local_desde ATUAL: {nld_atual}  ({_dist_ponto(traj, nld_atual, dest_lat, dest_lng)} km do destino)")
        if nld_novo is None:
            print("no_local_desde NOVO : nao determinavel (nenhum ponto entrou no raio) — ABORTA, nada muda.")
            return
        print(f"no_local_desde NOVO : {nld_novo}  ({_dist_ponto(traj, nld_novo, dest_lat, dest_lng)} km do destino)")

        # Aplica na transação e lê o KPI resultante (preview). Em dry-run isso é desfeito.
        cur.execute("UPDATE embarques_cargas SET no_local_desde=%s WHERE id=%s", (nld_novo, carga_id))
        W._consolidar_kpi(cur, carga_id, final=True)
        cur.execute("""SELECT distancia_metros, velocidade_max, velocidade_media,
                              tempo_movimento_seg, tempo_parado_seg
                       FROM embarques_cargas_rastreio_kpi WHERE carga_id=%s""", (carga_id,))
        kpi_novo = cur.fetchone()

        def _fmt_kpi(k):
            if not k:
                return "(sem KPI)"
            return (f"dist={ (k[0] or 0)/1000:.1f}km  vmax={k[1]}  vmed={k[2]}  "
                    f"mov={ (k[3] or 0)//60}min  parado={ (k[4] or 0)//60}min")
        print(f"KPI ATUAL : {_fmt_kpi(kpi_atual)}")
        print(f"KPI NOVO  : {_fmt_kpi(kpi_novo)}")

        if not apply:
            conn.rollback()
            print("\n[DRY-RUN] preview acima. NADA foi gravado (rollback). Rode com --apply para valer.")
            return

        print("\n===== SQL DE UNDO (copie e guarde — cola no pgAdmin p/ reverter) =====")
        print(f"UPDATE embarques_cargas SET no_local_desde = {_sql_val(nld_atual)} WHERE id = {carga_id};")
        if kpi_atual:
            print("UPDATE embarques_cargas_rastreio_kpi SET "
                  f"distancia_metros={_sql_val(kpi_atual[0])}, velocidade_max={_sql_val(kpi_atual[1])}, "
                  f"velocidade_media={_sql_val(kpi_atual[2])}, tempo_movimento_seg={_sql_val(kpi_atual[3])}, "
                  f"tempo_parado_seg={_sql_val(kpi_atual[4])} WHERE carga_id = {carga_id};")
        else:
            print(f"DELETE FROM embarques_cargas_rastreio_kpi WHERE carga_id = {carga_id};")
        print("======================================================================\n")

        conn.commit()  # as alterações já estão na transação (feitas acima p/ o preview)
        print("APLICADO e commitado. (guarde o UNDO acima)")
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print("ERRO — rollback feito, nada gravado:", e)
    finally:
        cur.close(); conn.close()


if __name__ == '__main__':
    main()
