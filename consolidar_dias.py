# -*- coding: utf-8 -*-
"""
Consolida `embarques_posicoes_historico` no grão PLACA + DIA (dia de Brasília),
gravando em `embarques_rastreio_dia`.

POR QUE RODAR ISTO AGORA, À MÃO
    A purga do worker leva as posições aos RASTREAMENTO_RETENCAO_DIAS (30) e a 3S só
    serve ~35 dias de histórico — medido em 04/09/26: 31/07 respondia, 28/07 devolvia
    404. Julho/2026 já se perdeu por isso. Cada dia que passa, mais um dia de agosto
    morre. Este script salva o que ainda existe; depois o worker faz sozinho, no ciclo
    diário, antes de purgar.

    A tabela é a base de: km real por veículo (odômetro, que atravessa buraco de
    sinal), km VAZIO (dia sem carga_id), dias produtivos × parados, e o km/L
    tanque-a-tanque casando com o abastecimento — que só tem data, sem hora.

USO
    python -X utf8 consolidar_dias.py                      # tudo que existe
    python -X utf8 consolidar_dias.py --desde 2026-08-04
    python -X utf8 consolidar_dias.py --desde 2026-08-04 --ate 2026-08-31
    python -X utf8 consolidar_dias.py --refazer            # reprocessa dia já gravado
    python -X utf8 consolidar_dias.py --resumo             # só mostra o que há, não grava

É idempotente (PK placa+dia com upsert) — pode rodar quantas vezes quiser.
"""
import argparse
import sys
from datetime import datetime

from rastreamento_worker import _consolidar_dias, _get_db


def _data(s):
    return datetime.strptime(s, '%Y-%m-%d').date()


def resumo(cur):
    cur.execute("""
        SELECT to_char(data_posicao - INTERVAL '3 hours', 'YYYY-MM') AS mes,
               COUNT(*), COUNT(odometer), COUNT(DISTINCT placa),
               MIN((data_posicao - INTERVAL '3 hours')::date),
               MAX((data_posicao - INTERVAL '3 hours')::date)
          FROM embarques_posicoes_historico
         GROUP BY 1 ORDER BY 1
    """)
    print('\nO que existe em embarques_posicoes_historico:')
    print(f"  {'mês':9} {'posições':>10} {'c/ odômetro':>12} {'placas':>7}  período")
    for m, n, odo, pl, de, ate in cur.fetchall():
        print(f'  {m:9} {n:10,} {odo:12,} {pl:7}  {de} → {ate}'.replace(',', '.'))

    cur.execute("SELECT COUNT(*), MIN(dia), MAX(dia) FROM embarques_rastreio_dia")
    n, de, ate = cur.fetchone()
    print(f'\nJá consolidado: {n} linhas' + (f' ({de} → {ate})' if n else ''))


def main():
    ap = argparse.ArgumentParser(description='Consolida posições no grão placa+dia')
    ap.add_argument('--desde', type=_data, help='primeiro dia (YYYY-MM-DD)')
    ap.add_argument('--ate', type=_data, help='último dia (YYYY-MM-DD)')
    ap.add_argument('--refazer', action='store_true',
                    help='reprocessa dias já consolidados (padrão: só os que faltam + os 2 últimos)')
    ap.add_argument('--resumo', action='store_true', help='só diagnostica, não grava')
    a = ap.parse_args()

    conn = _get_db()
    cur = conn.cursor()
    try:
        resumo(cur)
        if a.resumo:
            return 0

        print('\nConsolidando…')
        n = _consolidar_dias(cur, desde=a.desde, ate=a.ate, refazer=a.refazer)
        conn.commit()
        print(f'✅ {n} linhas placa/dia gravadas.')

        # Prévia do que a tabela já responde — é o ponto de tudo isto existir.
        cur.execute("""
            SELECT placa,
                   COUNT(*)                                        AS dias,
                   COUNT(*) FILTER (WHERE carga_id IS NULL)         AS dias_sem_carga,
                   COALESCE(SUM(km_odo), 0)                         AS km_odo,
                   COALESCE(SUM(km_odo) FILTER (WHERE carga_id IS NULL), 0) AS km_sem_carga,
                   COUNT(*) FILTER (WHERE COALESCE(km_odo, 0) < 5)  AS dias_parado
              FROM embarques_rastreio_dia
             GROUP BY placa
             HAVING SUM(km_odo) > 0
             ORDER BY SUM(km_odo) DESC
             LIMIT 12
        """)
        linhas = cur.fetchall()
        if linhas:
            print(f"\n{'placa':9} {'dias':>5} {'s/ carga':>9} {'km total':>9} "
                  f"{'km s/ carga':>12} {'% s/ carga':>11} {'dias parado':>12}")
            for placa, dias, d_sc, km, km_sc, parado in linhas:
                pct = 100.0 * km_sc / km if km else 0
                print(f'{placa:9} {dias:5} {d_sc:9} {km:9,} {km_sc:12,} {pct:10.1f}% {parado:12}'
                      .replace(',', '.'))
            print('\n"km s/ carga" ≈ deslocamento sem documento (vazio + manobra). '
                  '\n"dias parado" = dias com menos de 5 km — veículo no pátio.')
        return 0
    except Exception as e:
        conn.rollback()
        print(f'❌ {type(e).__name__}: {e}', file=sys.stderr)
        return 1
    finally:
        cur.close()
        conn.close()


if __name__ == '__main__':
    sys.exit(main())
