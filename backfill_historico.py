# -*- coding: utf-8 -*-
"""Backfill manual do histórico de posições da 3S (POST /HistoricoPosicao).

O worker já faz isto sozinho 1×/dia (BACKFILL_HORA_UTC). Este script é para
reprocessar dias passados — útil depois de uma queda do worker ou para montar
base histórica.

    python backfill_historico.py 2026-08-11              # um dia
    python backfill_historico.py 2026-08-01 2026-08-11   # intervalo (inclusive)

As datas são DIAS DE BRASÍLIA, que é como o relatório PGR define o dia — não
dia-calendário UTC (era o desalinhamento de 3h da primeira extração).

É idempotente: UNIQUE(placa, data_posicao) faz o reprocesso não duplicar, então
rodar de novo o mesmo dia é seguro.

⚠️ Cota: a 3S limita 10 chamadas/min POR CONTA, não por processo. Rodando isto
enquanto o worker de produção está no ar, os dois somam. O espaçamento padrão
(BACKFILL_ESPACO_SEG=10 → ~6/min) já deixa margem para o worker (~1,1/min).
Estime ~16 min por dia de 93 veículos.
"""
import sys
import logging
from datetime import date, timedelta

import rastreamento_worker as worker


def _parse_dia(s):
    try:
        a, m, d = (int(x) for x in s.split('-'))
        return date(a, m, d)
    except (ValueError, TypeError):
        sys.exit(f'Data inválida: {s!r} — use AAAA-MM-DD')


def main(argv):
    if not argv or len(argv) > 2:
        sys.exit(__doc__)

    d0 = _parse_dia(argv[0])
    d1 = _parse_dia(argv[1]) if len(argv) == 2 else d0
    if d1 < d0:
        d0, d1 = d1, d0

    dias = (d1 - d0).days + 1
    print(f'Backfill de {d0:%d/%m/%Y} a {d1:%d/%m/%Y} ({dias} dia(s), horário de Brasília)')
    print(f'Espaçamento: {worker.BACKFILL_ESPACO_SEG}s/chamada\n')

    # backfill_dia respeita a flag de parada do worker; num run avulso ela
    # começa desligada e o loop sairia no primeiro veículo.
    worker._running = True

    total = 0
    for i in range(dias):
        dia = d0 + timedelta(days=i)
        total += worker.backfill_dia(dia)

    print(f'\nConcluído: {total} posições novas em {dias} dia(s).')


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    main(sys.argv[1:])
