# -*- coding: utf-8 -*-
"""Reabre as cargas que o robô fechou por `baixa_ctrb` / `timeout`.

Contexto: até 03/09/26 a cascata de fechamento tinha duas etapas que fechavam
carga AINDA EM VIAGEM — a baixa administrativa do CTRB e um timeout por idade.
Ambas foram removidas de `embarques_auto.fechar_pendentes`. Este script desfaz
o que elas já tinham fechado.

O status não é chutado: `encerrar()` gravou em `embarques_cargas_log` uma linha
`campo='status'` cujo `valor_anterior` é o status exato de antes do fechamento
(Aberta / Em rota / No destino / Desengatada). É ele que volta.

NÃO reabre a carga cuja placa saiu em viagem depois. Nesse caso a viagem
terminou de fato — a regra `manifesto_novo` teria fechado a carga de qualquer
jeito — e reabrir criaria duas cargas ativas para o mesmo veículo, exatamente o
que `dedup_veiculo` existe para impedir.

    python -X utf8 _reabrir_fechamento_indevido.py                    # só mostra o plano
    python -X utf8 _reabrir_fechamento_indevido.py --desde 2026-08-28
    python -X utf8 _reabrir_fechamento_indevido.py --aplicar          # grava

`--desde` corta por data de carregamento. Sem ele a varredura vai até a primeira
carga do robô (19/08/26), e sem a regra de timeout uma carga antiga reaberta
fica aberta até o GPS ou o operacional fechar.
"""
import sys

from server import get_db

AUTOR = 'Correção: fechamento indevido (baixa_ctrb/timeout)'
MOTIVOS = ('baixa_ctrb', 'timeout')
ATIVOS = ('Aberta', 'Em rota', 'No destino', 'Desengatada')

CANDIDATAS = """
    SELECT c.id, c.numero, c.data_carregamento, c.encerrada_motivo,
           c.cavalo_placa, c.carreta1_placa, c.carreta2_placa,
           l.valor_anterior, l.editado_em
    FROM embarques_cargas c
    JOIN LATERAL (
        SELECT valor_anterior, editado_em
        FROM embarques_cargas_log
        WHERE carga_id = c.id AND campo = 'status'
          AND valor_novo LIKE 'Entregue (rob%%'
        ORDER BY editado_em DESC
        LIMIT 1
    ) l ON TRUE
    WHERE c.status = 'Entregue'
      AND COALESCE(c.entregue_auto, FALSE) = FALSE
      AND COALESCE(c.criada_por_robo, FALSE) = TRUE
      AND c.encerrada_motivo IN %s
      AND (%s IS NULL OR c.data_carregamento >= %s)
    ORDER BY c.data_carregamento, c.id
"""

# Viagem posterior da mesma placa: prova que a carga realmente terminou.
POSTERIOR = """
    SELECT numero, data_carregamento
    FROM embarques_cargas
    WHERE id <> %s
      AND data_carregamento > %s
      AND (cavalo_placa = ANY(%s) OR carreta1_placa = ANY(%s)
           OR carreta2_placa = ANY(%s))
    ORDER BY data_carregamento
    LIMIT 1
"""


def main(aplicar=False, desde=None):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(CANDIDATAS, (MOTIVOS, desde, desde))
    linhas = cur.fetchall()

    reabrir, manter, sem_status = [], [], []
    for (cid, numero, dt, motivo, cav, c1, c2, antes, quando) in linhas:
        placas = [p for p in (cav, c1, c2) if p]
        cur.execute(POSTERIOR, (cid, dt, placas, placas, placas))
        prox = cur.fetchone()
        if prox:
            manter.append((numero, dt, motivo, prox[0], prox[1]))
        elif antes not in ATIVOS:
            sem_status.append((numero, dt, motivo, antes))
        else:
            reabrir.append((cid, numero, dt, motivo, antes, quando))

    corte = f' desde {desde}' if desde else ' (todo o histórico do robô)'
    print(f'\ncandidatas ({", ".join(MOTIVOS)}){corte}: {len(linhas)}\n')

    if manter:
        print(f'MANTER FECHADAS — {len(manter)}: a placa saiu em viagem depois,')
        print('  a viagem terminou mesmo (manifesto_novo teria fechado igual)')
        for numero, dt, motivo, pnum, pdt in manter:
            print(f'   {numero}  {dt}  {motivo:<11} -> depois veio {pnum} ({pdt})')
        print()

    if sem_status:
        print(f'PULAR — {len(sem_status)}: log não tem status ativo anterior')
        for numero, dt, motivo, antes in sem_status:
            print(f'   {numero}  {dt}  {motivo:<11} valor_anterior={antes!r}')
        print()

    if not reabrir:
        print('nada a reabrir.')
        conn.close()
        return

    print(f'REABRIR — {len(reabrir)}: fechada sem viagem nova nenhuma')
    for _, numero, dt, motivo, antes, quando in reabrir:
        print(f'   {numero}  carga {dt}  fechada {str(quando)[:16]} '
              f'por {motivo:<11} -> volta para {antes!r}')

    if not aplicar:
        print(f'\n[DRY-RUN] nada gravado. Rode com --aplicar para reabrir as '
              f'{len(reabrir)} acima.')
        conn.close()
        return

    for cid, numero, _dt, motivo, antes, _q in reabrir:
        cur.execute("""
            UPDATE embarques_cargas
            SET status=%s, encerrada_motivo=NULL, data_conclusao=NULL,
                atualizado_em=NOW()
            WHERE id=%s
        """, (antes, cid))
        cur.execute("""
            INSERT INTO embarques_cargas_log
                (carga_id, usuario_nome, campo, valor_anterior, valor_novo)
            VALUES (%s,%s,%s,%s,%s)
        """, (cid, AUTOR, 'status', f'Entregue (robô: {motivo})', antes))
    conn.commit()
    print(f'\n✅ {len(reabrir)} carga(s) reaberta(s) e registrada(s) no histórico.')
    print('   O worker de GPS volta a acompanhá-las no próximo ciclo.')
    conn.close()


if __name__ == '__main__':
    _desde = None
    if '--desde' in sys.argv:
        _desde = sys.argv[sys.argv.index('--desde') + 1]
    main(aplicar='--aplicar' in sys.argv, desde=_desde)
