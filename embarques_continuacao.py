# -*- coding: utf-8 -*-
"""Continuação de carga: desengate no pátio, mercadoria que segue em outro manifesto, reemissão.

Modelo (HANDOFF-EMBARQUES-AUTONOMO.md §24, decidido com o Gabriel em 11/09/2026):

    primeira linha (A)          quando                                   terminal?  conta entrega?
    Desengatada (sem ligação)   cavalo saiu, carreta esperando           não        não
    Desengatada → C-B           outro cavalo levou a carreta             sim        não
    Continuada  → C-B           mesmo conjunto, manifesto novo, seguiu   sim        não
    Cancelada   → C-B           reemissão do manifesto                   sim        não
    Entregue                    chegou ao cliente                        sim        SIM

Quem manda no significado é a coluna `continua_em` (id da carga seguinte): a mesma palavra
`Desengatada` é ATIVA sem ligação e TERMINAL com ela. `Desengatada` no PÁTIO (carreta parada
longe do destino) não é rastreada pelo worker — só o documento a encerra.

Medido em 01/08 → 10/09/26 (`_ensaio_continuacao.py`, `_replay_producao.py`): 33 desengates,
10 continuações do mesmo conjunto, 8 reemissões, 4 transbordos; `entregues_mes` caía 7–12%.

TUDO atrás de `EMBARQUES_CONTINUACAO` (nasce desligada). Desligada, nenhuma função daqui
executa e o robô se comporta como antes. É ADITIVO (§0): nenhum status muda de significado
para o lançamento manual — o `Desengatada` de destino continua exatamente como era.
"""
import os
import logging
from datetime import datetime, timedelta

_logger = logging.getLogger('embarques_continuacao')

RAIO_DESTINO_KM = float(os.getenv('EMBARQUES_CONTINUACAO_RAIO_DESTINO', '25'))
_HAS_COLS = None   # cache por processo: as colunas existem no banco?


def ligado():
    """`EMBARQUES_CONTINUACAO=true` liga. Desligar pela CLI (`docker service update
    --env-add`), nunca pelo stack do Portainer (devolve a imagem antiga — §22.10)."""
    return os.getenv('EMBARQUES_CONTINUACAO', 'false').strip().lower() == 'true'


# ── DDL (idempotente, aditiva) ───────────────────────────────────────────

def garantir_colunas(cur):
    cur.execute("ALTER TABLE embarques_cargas ADD COLUMN IF NOT EXISTS continua_em INTEGER;")
    cur.execute("ALTER TABLE embarques_cargas ADD COLUMN IF NOT EXISTS desengate_local VARCHAR(10);")
    cur.execute("CREATE INDEX IF NOT EXISTS ix_cargas_continua_em ON embarques_cargas (continua_em) "
                "WHERE continua_em IS NOT NULL;")
    global _HAS_COLS
    _HAS_COLS = True


def colunas_existem(cur):
    """Os leitores (worker, telas, scripts) só podem citar as colunas se elas existirem —
    senão o SELECT quebra antes de a chave ser sequer consultada."""
    global _HAS_COLS
    if _HAS_COLS is None:
        cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name='embarques_cargas' "
                    "AND column_name='continua_em'")
        _HAS_COLS = cur.fetchone() is not None
    return _HAS_COLS


def ativo(cur):
    """Chave ligada E colunas no banco — a condição para um leitor aplicar as regras."""
    return ligado() and colunas_existem(cur)


# Cláusula SQL que tira da lista de ATIVAS o que a continuação torna terminal ou intocável:
#   * qualquer carga com ligação (continua_em) — acabou, a próxima carga é que vive;
#   * Desengatada no PÁTIO — só o documento encerra (o GPS ali é de OUTRA viagem).
SQL_NAO_ATIVA = ("(c.continua_em IS NOT NULL OR c.status IN ('Continuada') "
                 "OR (c.status = 'Desengatada' AND c.desengate_local = 'patio'))")


def filtro_ligadas(cur, alias='c'):
    """Só o que a LIGAÇÃO torna terminal. Para o conflito de recursos: a carreta desengatada
    no pátio ainda está comprometida (é isso que o aviso amarelo deve dizer), mas a carga com
    `continua_em` não prende ninguém — a carreta vive na carga seguinte."""
    if not ativo(cur):
        return ''
    return f" AND {alias}.continua_em IS NULL AND {alias}.status <> 'Continuada'"


def filtro_ativas(cur, alias='c'):
    """Fragmento a somar ao WHERE das consultas de carga ativa. Vazio quando a chave está
    desligada ou as colunas não existem — o comportamento fica byte a byte o de antes."""
    if not ativo(cur):
        return ''
    return ' AND NOT ' + SQL_NAO_ATIVA.replace('c.', alias + '.')


# ── GPS: onde e desde quando a carreta está parada ───────────────────────

def parada_carreta(cur, placa, ate=None, dias=7):
    """Último bloco parado da carreta até `ate`: (inicio, lat, lng, cidade, ultimo_ponto).
    Anda para trás enquanto os pontos ficam a < 3 km do último. None sem GPS."""
    import placas as pl
    import geocoding as g
    ate = ate or datetime.utcnow()
    cur.execute("""SELECT data_posicao, latitude, longitude, cidade FROM embarques_posicoes_historico
                    WHERE placa = ANY(%s) AND data_posicao <= %s AND data_posicao >= %s
                    ORDER BY data_posicao DESC LIMIT 3000""",
                (pl.grafias(placa), ate, ate - timedelta(days=dias)))
    pts = cur.fetchall()
    if not pts:
        cur.execute("SELECT data_posicao, latitude, longitude, cidade FROM embarques_posicoes_atuais "
                    "WHERE placa = ANY(%s) ORDER BY data_posicao DESC LIMIT 1", (pl.grafias(placa),))
        r = cur.fetchone()
        return (r[0], float(r[1]), float(r[2]), r[3], r[0]) if r else None
    ult = pts[0]
    ini = ult[0]
    for p in pts[1:]:
        if (g.km_entre(float(p[1]), float(p[2]), float(ult[1]), float(ult[2])) or 0) >= 3:
            break
        ini = p[0]
    return (ini, float(ult[1]), float(ult[2]), ult[3], ult[0])


def _destino(cur, carga_id):
    cur.execute("SELECT cidade, latitude, longitude FROM embarques_cargas_destinos "
                "WHERE carga_id=%s ORDER BY ordem DESC LIMIT 1", (carga_id,))
    return cur.fetchone()


def _local_do_desengate(cur, carga_id, carreta):
    """'destino' se a carreta está parada a <= RAIO do destino da carga; 'patio' se longe.
    Sem GPS: 'destino' quando a carga já registrou chegada, 'patio' caso contrário."""
    import geocoding as g
    d = _destino(cur, carga_id)
    p = parada_carreta(cur, carreta) if carreta else None
    if p and d and d[1] is not None:
        km = g.km_entre(p[1], p[2], float(d[1]), float(d[2]))
        if km is not None:
            return ('destino' if km <= RAIO_DESTINO_KM else 'patio'), p, round(km)
    cur.execute("SELECT no_local_desde FROM embarques_cargas WHERE id=%s", (carga_id,))
    r = cur.fetchone()
    return ('destino' if (r and r[0]) else 'patio'), p, None


# `manifesto_origem` é gravado COM hífen (UDI029121-8) e o `_norm` do robô compara SEM
# (UDI0291218). Comparar dos dois lados normalizado é o que faz A e B se acharem.
SQL_MAN = "regexp_replace(upper(COALESCE(%s, '')), '[^A-Z0-9]', '', 'g')"


# ── 1. Desengate: manifesto novo do CAVALO com OUTRA carreta ─────────────

def desengatar_por_cavalo(cur, manifestos):
    """Para cada manifesto novo (cavalo X + carreta Y), toda carga ATIVA do robô cujo cavalo
    é X e cuja carreta NÃO é Y vira `Desengatada`: o cavalo foi embora, a carreta ficou com a
    carga. Onde ficou (destino × pátio) decide o GPS. Hoje esse mesmo evento fecha a carga
    como `Entregue (manifesto_novo)` — o verbo errado (§21.20, §24)."""
    from collections import Counter
    from embarques_auto import _placa, _log, _norm
    n = Counter()
    for man in manifestos:
        cav, car = _placa(man.get('placa_cavalo')), _placa(man.get('placa_carreta'))
        dt = man.get('_data')
        if not cav or not car or not dt:
            continue
        cur.execute("""SELECT id, numero, status, carreta1_placa FROM embarques_cargas c
                        WHERE c.cavalo_placa = %s AND COALESCE(c.carreta1_placa,'') <> %s
                          AND COALESCE(c.criada_por_robo, FALSE) = TRUE
                          AND c.status IN ('Aberta','Em rota','No destino')
                          AND c.data_carregamento <= %s
                          AND """ + (SQL_MAN % 'c.manifesto_origem') + """ <> %s
                          AND c.continua_em IS NULL""",
                    (cav, car, dt, _norm(man.get('CHAVE_MANIFESTO')) or ''))
        for cid, numero, status, carreta in cur.fetchall():
            local, p, km = _local_do_desengate(cur, cid, carreta)
            desde = p[0] if p else datetime.combine(dt, datetime.min.time())
            onde = f'{p[3]}' + (f', {km} km do destino' if km is not None else '') if p else 'sem GPS'
            cur.execute("""UPDATE embarques_cargas
                              SET status='Desengatada', desengate_local=%s,
                                  desengatada_em=COALESCE(desengatada_em, %s),
                                  desengatada_por_nome=COALESCE(desengatada_por_nome, 'Robô SSW (manifesto)'),
                                  atualizado_em=NOW()
                            WHERE id=%s""", (local, desde, cid))
            _log(cur, cid, 'status', status,
                 f'Desengatada (robô: cavalo {cav} saiu com {car} em {dt.isoformat()}; carreta em {onde}; {local})')
            n[f'desengatada ({local})'] += 1
    return n


# ── 2. Ligação: o CTe diz que a mercadoria seguiu em outro manifesto ─────

def _rota_ctrb(ctrbs, chave):
    from embarques_auto import _chave_ctrb
    c = ctrbs.get(_chave_ctrb(chave)) if chave else None
    return (c.get('o'), c.get('d')) if c else None


def ligar_continuacoes(cur, ctrcs, ctrbs):
    """`primeiro_manifesto ≠ ultimo_manifesto` no CTe = a mercadoria atravessou duas cargas.
    A (pm) recebe `continua_em = B.id` e o terminal certo:

        reemissão   mesmo cavalo, ≤1 dia, mesma rota de CTRB  → Cancelada (reemitido)
        desengate   cavalo diferente                          → Desengatada (terminal pela ligação)
        hub         mesmo cavalo, doc novo                    → Continuada

    Roda DEPOIS de criar (precisa do id de B). Idempotente: A com ligação não é revisitada.
    Nunca toca carga lançada à mão (§0). A direção vem do próprio CTe — não precisa
    comparar horas, e é o que faz a reemissão do mesmo dia funcionar (o CTRB é o mesmo)."""
    from collections import Counter
    from embarques_auto import _norm, _log
    n = Counter()
    pares = {}
    for chave, rows in ctrcs.items():
        for r in rows:
            a, b = _norm(r.get('pm')), _norm(r.get('um'))
            if a and b and a != b:
                pares[(a, b)] = True
    for a, b in pares:
        cur.execute("""SELECT id, numero, status, cavalo_placa, carreta1_placa, data_carregamento, ctrb_origem,
                              continua_em, desengate_local, no_local_desde, data_conclusao, entregue_auto
                         FROM embarques_cargas WHERE """ + (SQL_MAN % 'manifesto_origem') + """ = %s
                          AND COALESCE(criada_por_robo,FALSE)""", (a,))
        A = cur.fetchone()
        cur.execute("""SELECT id, numero, cavalo_placa, carreta1_placa, data_carregamento, ctrb_origem,
                              COALESCE(data_saida_real, inicio_viagem)
                         FROM embarques_cargas WHERE """ + (SQL_MAN % 'manifesto_origem') + """ = %s""", (b,))
        B = cur.fetchone()
        if not A or not B or A[7] is not None or A[2] == 'Cancelada':
            continue
        (aid, anum, astatus, acav, acar, adt, actrb, _, alocal, acheg, aconc, aauto) = A
        (bid, bnum, bcav, bcar, bdt, bctrb, bsaida) = B
        if not acar or acar != bcar:
            n['transbordo (carreta trocou) — sem ligação'] += 1     # fora do desenho (§24)
            continue
        dias = (bdt - adt).days if (adt and bdt) else 99
        mesma_rota = _rota_ctrb(ctrbs, actrb) is not None and _rota_ctrb(ctrbs, actrb) == _rota_ctrb(ctrbs, bctrb)
        if not mesma_rota and (actrb is None or bctrb is None):
            # sem CTRB na carga (base antiga): compara as pontas das próprias cargas
            cur.execute("SELECT c.origem_cidade, (SELECT cidade FROM embarques_cargas_destinos d WHERE d.carga_id=c.id ORDER BY ordem DESC LIMIT 1) "
                        "FROM embarques_cargas c WHERE c.id IN (%s, %s) ORDER BY c.id = %s", (aid, bid, bid))
            pontas = cur.fetchall()
            mesma_rota = len(pontas) == 2 and pontas[0] == pontas[1] and pontas[0][0] is not None
        # fim da janela de A = a carreta saindo com B (ou o dia do manifesto B)
        fim = aconc or bsaida or datetime.combine(bdt, datetime.min.time())
        if acav == bcav and dias <= 1 and mesma_rota:
            novo, motivo = 'Cancelada', 'reemitido'
        elif acav != bcav:
            novo, motivo = 'Desengatada', 'desengate'
        else:
            novo, motivo = 'Continuada', 'continuou_no_hub'
        campos = {'status': novo, 'continua_em': bid, 'encerrada_motivo': motivo, 'entregue_auto': False,
                  'data_conclusao': fim}
        if novo == 'Desengatada':
            campos['desengate_local'] = alocal or ('destino' if acheg else 'patio')
            campos['desengatada_em'] = None    # COALESCE abaixo: só preenche se vazio
        sets = ', '.join(f'{k}=%s' if k != 'desengatada_em' else 'desengatada_em=COALESCE(desengatada_em, %s)'
                         for k in campos)
        # instante do desengate = inicio da parada da carreta antes de B sair (GPS); sem GPS, a
        # chegada registrada; sem nada, o dia do carregamento
        _p = parada_carreta(cur, acar, ate=(bsaida or fim)) if novo == 'Desengatada' else None
        _des = (_p[0] if _p else None) or acheg or datetime.combine(adt, datetime.min.time())
        vals = [campos[k] if k != 'desengatada_em' else _des for k in campos]
        cur.execute(f"UPDATE embarques_cargas SET {sets}, atualizado_em=NOW() WHERE id=%s", vals + [aid])
        _log(cur, aid, 'status', astatus, f'{novo} → {bnum} (robô: {motivo})')
        _log(cur, aid, 'continua_em', None, f'{bid} ({bnum})')
        n[f'{novo} ({motivo})'] += 1
        if aauto and novo != 'Cancelada':
            n['  (relabel de Entregue com prova de GPS)'] += 1
    return n


def continua_manifesto(ctrcs, manifesto_a, manifesto_b):
    """A mercadoria de `manifesto_a` seguiu em `manifesto_b`? (para o fechamento pular)."""
    from embarques_auto import _norm
    a = _norm(manifesto_a)
    for r in ctrcs.get(a, []):
        if _norm(r.get('pm')) == a and _norm(r.get('um')) == _norm(manifesto_b):
            return True
    return False
