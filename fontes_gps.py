# -*- coding: utf-8 -*-
"""Fontes de GPS — a 3S e a Insignia lidas como UMA série, cada uma guardada no seu lugar (06/10/2026).

Cada fonte é fato bruto na própria tabela, com as próprias rotinas: a 3S em
`embarques_posicoes_*` (worker, backfill, sincronização do cadastro), a Insignia em
`insignia_posicoes` (`insignia_coleta`). Misturar na tabela da 3S quebraria no dia em que a mesma
placa tivesse as duas: 8 dos 25 cavalos da Insignia também estão na 3S, com 6 a 670 m de
diferença entre os aparelhos e odômetros em unidades diferentes (HANDOFF-INSIGNIA §8–§9).

Os leitores que DECIDEM carga (worker, motor, aferidor, robô, continuação, mapa) trocam o nome da
tabela por `historico(cur)` / `atuais(cur)`. Rotina da 3S (backfill, consolidação diária, PGR,
sincronização) continua lendo a tabela da 3S — por isso a Insignia não entra no PGR nem gasta
cota da 3S.

Preferência POR PLACA: placa que a 3S rastreia (está em `embarques_veiculos_rastreio`) é só 3S;
a Insignia entra para placa que a 3S não vê. Nunca intercala dois aparelhos na mesma série.

    EMBARQUES_FONTE_INSIGNIA         ausente/false = só a 3S, idêntico ao de antes (é o gate)
    EMBARQUES_FONTE_INSIGNIA_ESCOPO  terceiro (padrão) = só cavalo de carga Terceiro
                                     todas            = qualquer placa que a 3S não rastreia

O odômetro da Insignia sai NULL, a não ser com `EMBARQUES_FONTE_INSIGNIA_ODOMETRO`: aí é convertido
por tecnologia, sem as leituras zeradas e sem placa de odômetro travado (`_sql_odometro`).
"""
import os
import re
from datetime import timedelta

import placas as pl

_TABELAS = {}


def ligado():
    return os.getenv('EMBARQUES_FONTE_INSIGNIA', 'false').strip().lower() == 'true'


def escopo():
    e = os.getenv('EMBARQUES_FONTE_INSIGNIA_ESCOPO', 'terceiro').strip().lower()
    return e if e in ('terceiro', 'todas') else 'terceiro'


def _tem_insignia(cur):
    """A coleta já criou as tabelas? (sem elas a união não compila). Uma vez por processo."""
    if 'ok' not in _TABELAS:
        cur.execute("SELECT to_regclass('insignia_posicoes') IS NOT NULL AND to_regclass('insignia_placas') IS NOT NULL")
        _TABELAS['ok'] = bool(cur.fetchone()[0])
    return _TABELAS['ok']


def _ativo(cur):
    return ligado() and _tem_insignia(cur)


def odometro_ligado():
    """`EMBARQUES_FONTE_INSIGNIA_ODOMETRO=true`: usa o odômetro da Insignia convertido para km.
    Validado em 06/10/26 contra o odômetro da 3S (HANDOFF-INSIGNIA §11): Autotrac +4% (9 pares),
    Onixsat e Omnilink ≈ +2..+3% contra o próprio GPS, depois de tirar as leituras zeradas."""
    return os.getenv('EMBARQUES_FONTE_INSIGNIA_ODOMETRO', 'false').strip().lower() == 'true'


def _sql_odometro(cur):
    """Odômetro da Insignia em km inteiros (a unidade da 3S), ou NULL. Três regras, cada uma com o
    caso que a obrigou (06/10/26):
      * unidade por tecnologia — Autotrac em centenas de metros, Omnilink em metros, Onixsat em km;
        tecnologia desconhecida fica NULL
      * leitura 0 é falha de transmissão (139128 → 0 → 139132), não odômetro — NULL
      * placa com odômetro TRAVADO (`insignia_placas.odometro_travado`, marcada pela coleta) — NULL
    Não é só o km: `embarques_regua.perna_impossivel` usa o odômetro para declarar posição falsa,
    e um zero ou um odômetro parado faria trecho REAL virar falso."""
    if not odometro_ligado():
        return 'NULL::integer'
    if 'travado' not in _TABELAS:
        cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'insignia_placas' "
                    "AND column_name = 'odometro_travado'")
        _TABELAS['travado'] = cur.fetchone() is not None
    trav = 'COALESCE(pl.odometro_travado, FALSE) OR ' if _TABELAS['travado'] else ''
    # Placa sem ficha de SM não tem `tecnologia`: vale a deduzida pela coleta a partir do próprio
    # odômetro (`insignia_coleta.marcar_tecnologia`, 08/10/26 — 26 de 26 certas contra a da SM).
    # A da SM tem precedência. Sem a coluna (base antiga), só a da SM, como antes.
    if 'deduzida' not in _TABELAS:
        cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'insignia_placas' "
                    "AND column_name = 'tecnologia_deduzida'")
        _TABELAS['deduzida'] = cur.fetchone() is not None
    tec = 'COALESCE(pl.tecnologia, pl.tecnologia_deduzida)' if _TABELAS['deduzida'] else 'pl.tecnologia'
    # Contador de 32 bits SEM sinal lido como COM sinal (13/10/26): o Omnilink do NJY8G40 manda as 2.060
    # leituras entre -1.586.735.116 e -1.586.576.011 — o valor absoluto não diz nada, a DIFERENÇA está
    # certa (a coleta deduziu a tecnologia pela razão, 1.087). Negativo + 2^32 é o mesmo contador; o km
    # do rastreador, que só aceita leitura > 0, voltava "—" (C-2026-001506). Só nas tecnologias em metro
    # e hectômetro: o Onixsat é em km e não tem leitura negativa.
    _u32 = "(CASE WHEN i.odometro < 0 THEN i.odometro + 4294967296 ELSE i.odometro END)"
    return (f"(CASE WHEN i.odometro IS NULL OR i.odometro = 0 OR {trav}FALSE THEN NULL "
            f"WHEN {tec} = 'AUTOTRAC' THEN round({_u32} / 100.0) "
            f"WHEN {tec} = 'OMNILINK' THEN round({_u32} / 1000.0) "
            f"WHEN {tec} = 'ONIXSAT' THEN i.odometro END)::integer")


def _filtro_insignia(alias='i'):
    """Placa da Insignia que entra: a 3S não a rastreia, e está no escopo."""
    f = (f"NOT EXISTS (SELECT 1 FROM embarques_veiculos_rastreio v "
         f"WHERE {pl.sql_chave('v.placa')} = {alias}.placa_chave)")
    if escopo() == 'terceiro':
        f += (f" AND {alias}.placa_chave IN (SELECT {pl.sql_chave('c.cavalo_placa')} FROM embarques_cargas c "
              f"WHERE c.tipo_operacao = 'Terceiro')")
    return f


def buraco_ligado():
    """`EMBARQUES_FONTE_INSIGNIA_BURACO=true`: a Insignia TAPA O BURACO da 3S na mesma placa (06/10/26).
    Sem ela, placa da 3S é só 3S (a preferência por placa); com ela, o ponto da Insignia entra
    onde a 3S calou. Medido: dos 47 buracos ≥ 30 min das 8 placas que estão nas duas, 42 eram o
    veículo PARADO (a 3S parada fala de hora em hora — não é buraco) e 5 em movimento; a
    QOY6F50 rodou 93 km em 106 min sem ponto da 3S e com 11 da Insignia andando."""
    return ligado() and os.getenv('EMBARQUES_FONTE_INSIGNIA_BURACO', 'false').strip().lower() == 'true'


BURACO_ANDANDO_MIN = 15     # ponto da Insignia andando entra se a 3S não tem ponto a ±15 min
BURACO_PARADO_MIN = 75      # parado, só se a 3S calou mais que o batimento de 1 h
BURACO_ATUAL_MIN = 30       # posição atual: a da Insignia vale se for 30+ min mais nova


def _buraco_hist():
    """Pontos da Insignia DENTRO dos buracos da 3S, na grafia da 3S (a mesma série da placa).
    Odômetro NULL: é outro aparelho, e misturar contadores quebraria o km e a régua de posição
    falsa (o salto entre os dois aparelhos é de dezenas a centenas de metros, nunca 30 km)."""
    return f"""
                UNION ALL
                SELECT NULL::bigint, v.placa::varchar(10), NULL::bigint, i.em,
                       i.lat::numeric(10,7), i.lng::numeric(10,7), round(i.velocidade)::integer, i.ignicao = 1,
                       NULL::char(2), NULL::varchar(120), i.referencia::varchar(200), NULL::integer,
                       'ins_buraco'::varchar(12)
                  FROM insignia_posicoes i
                  JOIN embarques_veiculos_rastreio v ON {pl.sql_chave('v.placa')} = i.placa_chave
                 WHERE NOT EXISTS (
                        SELECT 1 FROM embarques_posicoes_historico h3
                         WHERE h3.placa = v.placa
                           AND h3.data_posicao BETWEEN i.em - (CASE WHEN COALESCE(i.velocidade, 0) > 5
                                                                    THEN interval '{BURACO_ANDANDO_MIN} minutes'
                                                                    ELSE interval '{BURACO_PARADO_MIN} minutes' END)
                                                   AND i.em + (CASE WHEN COALESCE(i.velocidade, 0) > 5
                                                                    THEN interval '{BURACO_ANDANDO_MIN} minutes'
                                                                    ELSE interval '{BURACO_PARADO_MIN} minutes' END))"""


def historico(cur):
    """O que vai no FROM no lugar de `embarques_posicoes_historico`. Use SEMPRE com alias."""
    if not _ativo(cur):
        return 'embarques_posicoes_historico'
    return f"""(SELECT id, placa, id_veiculo_3s, data_posicao, latitude, longitude, velocidade, ignicao,
                       uf, cidade, endereco, odometer, '3s'::varchar(12) AS fonte
                  FROM embarques_posicoes_historico
                UNION ALL
                SELECT NULL::bigint, replace(i.placa, '-', '')::varchar(10), NULL::bigint, i.em,
                       i.lat::numeric(10,7), i.lng::numeric(10,7), round(i.velocidade)::integer, i.ignicao = 1,
                       NULL::char(2), NULL::varchar(120), i.referencia::varchar(200), {_sql_odometro(cur)},
                       'insignia'::varchar(12)
                  FROM insignia_posicoes i
                  LEFT JOIN insignia_placas pl ON pl.placa_chave = i.placa_chave
                 WHERE {_filtro_insignia()}{_buraco_hist() if buraco_ligado() else ''})"""


def atuais(cur):
    """O que vai no FROM no lugar de `embarques_posicoes_atuais`. Use SEMPRE com alias."""
    if not _ativo(cur):
        return 'embarques_posicoes_atuais'
    if buraco_ligado():
        # posição ATUAL da placa que está nas duas: a da Insignia, se for BURACO_ATUAL_MIN mais nova.
        # A mais nova de cada placa do CADASTRO da 3S (~94) sai do ÍNDICE (PK placa_chave, em): o LATERAL pega a
        # última de cada uma. Era um DISTINCT ON sobre `insignia_posicoes` inteira — a mesma releitura do histórico
        # por carga que a §17 do HANDOFF-INSIGNIA tirou do ramo de baixo (13/10/26). O DISTINCT ON que sobra é
        # sobre esse resultado pequeno e mantém a semântica de antes (uma linha por placa_chave).
        mais_nova = (f"(SELECT DISTINCT ON (n.placa_chave) n.*, v.placa AS p3 FROM embarques_veiculos_rastreio v "
                     f"CROSS JOIN LATERAL (SELECT * FROM insignia_posicoes x "
                     f"WHERE x.placa_chave = {pl.sql_chave('v.placa')} ORDER BY x.em DESC LIMIT 1) n "
                     f"ORDER BY n.placa_chave, n.em DESC)")
        tres = (f"FROM embarques_posicoes_atuais a WHERE NOT EXISTS (SELECT 1 FROM {mais_nova} n "
                f"WHERE n.p3 = a.placa AND n.em > a.data_posicao + interval '{BURACO_ATUAL_MIN} minutes')")
        tapa = f"""
                UNION ALL
                SELECT n.p3::varchar(10), NULL::bigint, n.em, n.lat::numeric(10,7), n.lng::numeric(10,7),
                       round(n.velocidade)::integer, n.ignicao = 1, NULL::varchar(20), NULL::char(2),
                       NULL::varchar(120), NULL::varchar(120), n.referencia::varchar(200), NULL::boolean,
                       NULL::bigint, n.coletada_em, 'ins_buraco'::varchar(12)
                  FROM {mais_nova} n JOIN embarques_posicoes_atuais a ON a.placa = n.p3
                 WHERE n.em > a.data_posicao + interval '{BURACO_ATUAL_MIN} minutes'"""
    else:
        tres, tapa = 'FROM embarques_posicoes_atuais a', ''
    # A última posição de cada placa da Insignia sai do ÍNDICE (PK placa_chave, em): `ch` pula de
    # placa em placa e o LATERAL pega a mais nova de cada uma. Era um DISTINCT ON sobre a tabela
    # inteira, que não tem purga — e os leitores citam `atuais()` numa subconsulta POR CARGA, então
    # cada carga relia o histórico todo. Medido em 12/10/26 no lab de 08/10: a listagem de
    # outubro do relatório de embarques levava 8 s com 17 mil posições e 43 s com 105 mil; o
    # worker, 32 ms → 167 ms por consulta. Mesmas linhas de antes: o filtro só depende da placa.
    return f"""(SELECT a.placa, a.id_veiculo_3s, a.data_posicao, a.latitude, a.longitude, a.velocidade, a.ignicao,
                       a.direcao, a.uf, a.cidade, a.bairro, a.endereco, a.bloqueio, a.odometer, a.atualizado_em,
                       '3s'::varchar(12) AS fonte
                  {tres}{tapa}
                UNION ALL
                (WITH RECURSIVE ch AS (
                        (SELECT placa_chave FROM insignia_posicoes ORDER BY placa_chave LIMIT 1)
                        UNION ALL
                        SELECT (SELECT x.placa_chave FROM insignia_posicoes x
                                 WHERE x.placa_chave > ch.placa_chave ORDER BY x.placa_chave LIMIT 1)
                          FROM ch WHERE ch.placa_chave IS NOT NULL)
                 SELECT replace(i.placa, '-', '')::varchar(10), NULL::bigint, i.em,
                        i.lat::numeric(10,7), i.lng::numeric(10,7), round(i.velocidade)::integer, i.ignicao = 1,
                        NULL::varchar(20), NULL::char(2), NULL::varchar(120), NULL::varchar(120),
                        i.referencia::varchar(200), NULL::boolean, {_sql_odometro(cur)}::bigint, i.coletada_em,
                        'insignia'::varchar(12)
                   FROM ch
                   CROSS JOIN LATERAL (SELECT * FROM insignia_posicoes x
                                        WHERE x.placa_chave = ch.placa_chave ORDER BY x.em DESC LIMIT 1) i
                   LEFT JOIN insignia_placas pl ON pl.placa_chave = i.placa_chave
                  WHERE {_filtro_insignia()}))"""


def placa_rastreada(cur, placa):
    """A placa tem GPS em alguma fonte? Devolve a grafia que está nas posições, ou None.
    A 3S primeiro (é a principal); a Insignia só com a chave e dentro do escopo."""
    p = (placa or '').strip().upper()
    if not p:
        return None
    cur.execute("SELECT placa FROM embarques_veiculos_rastreio WHERE placa = ANY(%s)", (pl.grafias(p),))
    r = cur.fetchone()
    if r:
        return r[0]
    if not _ativo(cur):
        return None
    cur.execute(f"""SELECT replace(i.placa, '-', '') FROM insignia_placas i
                     WHERE i.placa_chave = %s AND i.rastreada AND {_filtro_insignia()}""", (pl.mercosul(p),))
    r = cur.fetchone()
    return r[0] if r else None


def encerramento_ligado():
    """`EMBARQUES_SM_ENCERRAMENTO=true`: carga rastreada pela Insignia que fica MUDA depois que a
    GR encerra a SM conclui pelo FIM DE VIAGEM (decisão do Gabriel, 06/10/26). Só vale com a
    fonte ligada."""
    return ligado() and os.getenv('EMBARQUES_SM_ENCERRAMENTO', 'false').strip().lower() == 'true'


CARENCIA_MUDA_H = 2.0       # depois do encerramento, quanto esperar um ponto antes de chamar de muda
MACROS_CHEGADA = ('CHEGADA NO CLIENTE', 'FIM DE VIAGEM', 'ABERTURA DE BAU')


def sm_da_carga_ligado():
    """`EMBARQUES_SM_DA_CARGA=true` (LAB 13/10/26): a SM usada é a DESTA carga, não a de outra perna do
    mesmo cavalo. A janela de ±2/3 dias casava SM de outra viagem o tempo todo — 45 de 110 pares carga ×
    SM de 01–13/10 com a origem da SM a mais de 60 km da origem da carga. Duas regras:
      * a ORIGEM da SM fica a ≤ SM_ORIGEM_KM da origem da carga (o raio de metrópole). C-2026-001464
        (Rio → Cordeirópolis) nasceu pela SM da C-1470 (Cordeirópolis → Rio, 434 km); a C-1442 tinha a
        SM da viagem anterior. Sem coordenada de um dos lados, vale como antes;
      * entre as encerradas, vale a 1ª que TEM macro. A GR abre SM em duplicidade e aborta uma: na
        C-2026-001444 a 212394 foi encerrada 51 min depois de aberta, sem macro, e a da viagem (212409,
        FIM DE VIAGEM 09/10 23:35) nunca era lida — a carga ficava em `No destino` para sempre."""
    return os.getenv('EMBARQUES_SM_DA_CARGA', 'false').strip().lower() == 'true'


SM_ORIGEM_KM = 60.0     # embarques_regua.RAIO_METRO: o mesmo "perto" da chegada de metrópole


def _sm_origem_ok(sm_lat, sm_lng, origem):
    """A origem da SM é compatível com a origem da carga? Sempre True com a chave desligada ou sem
    coordenada de um dos lados (o que era antes)."""
    if not sm_da_carga_ligado() or not origem or origem[0] is None or sm_lat is None or sm_lng is None:
        return True
    import geocoding as g
    k = g.km_entre(float(sm_lat), float(sm_lng), float(origem[0]), float(origem[1]))
    return k is None or k <= SM_ORIGEM_KM


def encerramento_sm(cur, placa, dia_carga, agora, dest=None, raio_km=20.0, ate=None, origem=None):
    """SM da Insignia encerrada com a placa MUDA depois dela. Devolve um dict ou None:

        chegada   instante da 1ª macro de chegada NO DESTINO (CHEGADA NO CLIENTE / ABERTURA DE
                  BAU / FIM DE VIAGEM) — é o "esteve no destino" declarado pelo motorista
        conclusao o FIM DE VIAGEM no destino; sem ele, o último ponto antes do encerramento
        sm, encerrada_em

    Por que existe: as regras de conclusão (saiu > 60 km, ou um ponto 24 h depois da chegada)
    precisam VER um ponto depois da chegada. Quando a GR encerra a SM no destino — o FIM DE
    VIAGEM é o motorista encerrando a viagem lá, horas depois de chegar — e o aparelho deixa de
    ser visível (`ER0121`), esse ponto nunca vem e a carga ficaria em `No destino` para sempre
    (lab 06/10: 3 de 7 terceiros). Encerramento da SM não é silêncio: é a GR declarando o fim do
    monitoramento — o mesmo raciocínio do manifesto novo (§21.17 do HANDOFF-EMBARQUES).

    Placa que CONTINUA posicionando depois da SM (a coleta a segue 48 h) não é muda: segue a
    regra de hoje. Destino = (lat, lng) da carga; a macro vale se estiver a ≤ `raio_km` dele OU
    a ≤ 5 km do destino da própria SM.

    `ate` (EMBARQUES_JANELA_VEICULO, 13/10/26): o instante em que o veículo começou OUTRA carga. SM
    que começa depois dele é da viagem seguinte, e ponto depois dele é testemunha da viagem
    seguinte — não prova que a placa "continua posicionando" NESTA. Sem `ate`, como sempre."""
    if not (encerramento_ligado() and _tem_insignia(cur)):
        return None
    chave = pl.mercosul(placa or '')
    if not chave:
        return None
    _teto_sm = " AND COALESCE(inicio, criada_em) < %s" if ate else ""
    _macro = ("EXISTS (SELECT 1 FROM insignia_macros m WHERE m.sm = s.sm) DESC, "
              if sm_da_carga_ligado() else "")
    cur.execute(f"""SELECT sm, encerrada_em, destino_lat, destino_lng, origem_lat, origem_lng FROM insignia_sm s
                    WHERE placa_cavalo_chave = %s AND encerrada_em IS NOT NULL
                      AND (COALESCE(inicio, criada_em) - interval '3 hours')::date BETWEEN %s AND %s{_teto_sm}
                    ORDER BY {_macro}encerrada_em""",
                (chave, dia_carga - timedelta(days=3), dia_carga + timedelta(days=2)) + ((ate,) if ate else ()))
    r = next((x for x in cur.fetchall() if _sm_origem_ok(x[4], x[5], origem)), None)
    if not r:
        return None
    sm, enc, sm_lat, sm_lng = r[:4]
    if (agora - enc).total_seconds() / 3600 < CARENCIA_MUDA_H:
        return None                                     # ainda dentro da carência
    if ate:
        cur.execute("SELECT 1 FROM insignia_posicoes WHERE placa_chave = %s AND em > %s AND em < %s LIMIT 1",
                    (chave, enc, ate))
    else:
        cur.execute("SELECT 1 FROM insignia_posicoes WHERE placa_chave = %s AND em > %s LIMIT 1", (chave, enc))
    if cur.fetchone():
        return None                                     # continua posicionando: regra de hoje
    import geocoding as g

    def no_destino(la, lo):
        if la is None or lo is None:
            return False
        if dest and dest[0] is not None and g.km_entre(float(la), float(lo), float(dest[0]), float(dest[1])) <= raio_km:
            return True
        return sm_lat is not None and g.km_entre(float(la), float(lo), float(sm_lat), float(sm_lng)) <= 5.0
    cur.execute("SELECT em, nome, lat, lng FROM insignia_macros WHERE sm = %s AND nome = ANY(%s) ORDER BY em",
                (sm, list(MACROS_CHEGADA)))
    macros = [(em, nome) for em, nome, la, lo in cur.fetchall() if no_destino(la, lo)]
    chegada = macros[0][0] if macros else None
    fins = [em for em, nome in macros if nome == 'FIM DE VIAGEM']
    if fins:
        conclusao = fins[-1]
    else:
        cur.execute("SELECT max(em) FROM insignia_posicoes WHERE placa_chave = %s AND em <= %s", (chave, enc))
        conclusao = cur.fetchone()[0]
    return {'sm': sm, 'encerrada_em': enc, 'chegada': chegada, 'conclusao': conclusao,
            'via_fim': bool(fins)}


def congelada_ligado():
    """`EMBARQUES_CARRETA_CONGELADA=true` (LAB 07/10/26): carreta da 3S CONGELADA durante a SM vira
    buraco, tapado pelo cavalo da mesma SM. Exige a fonte ligada."""
    return ligado() and os.getenv('EMBARQUES_CARRETA_CONGELADA', 'false').strip().lower() == 'true'


CONGELADA_MIN_PTS = 4       # pontos seguidos no mesmo lugar com o odômetro parado
CONGELADA_TOL_KM = 0.3      # "mesmo lugar"
CONGELADA_LONGE_KM = 5.0    # o cavalo engatado estava a mais que isso do ponto congelado


def descongelar(cur, carreta, cavalo, pts_carreta, pts_cavalo, dia_carga=None):
    """Tira da série da carreta os trechos em que o aparelho da 3S CONGELOU e põe no lugar os
    pontos do cavalo — só enquanto a SM da Insignia declara os dois ENGATADOS (07/10/26, lab).

    O caso: o aparelho repete a mesma posição com o odômetro parado por horas (C-1363: 83 pontos,
    3 posições, odômetro 425842 de 06/10 08:45 a 07/10 00:04) e depois pula 239 km. O tapa-buraco
    por tempo não vê isso — a 3S não calou, repetiu. E a repetição é EVIDÊNCIA FALSA para o motor:
    na C-1276 o ponto congelado em Uberlândia virou "saiu do destino" 2 min depois de o cavalo
    chegar a Embu (549 km dali).

    Carreta parada de verdade também repete posição e odômetro. O que separa as duas coisas é a
    SEGUNDA TESTEMUNHA com o DOCUMENTO: a SM da GR lista a carreta engatada no cavalo, e o cavalo,
    no mesmo instante, está a mais de CONGELADA_LONGE_KM do ponto congelado. Desengate de verdade
    (carreta no pátio, cavalo em outra viagem) não tem a carreta na SM do cavalo.

    A SM tem de ser DESTA carga (início de 3 dias antes a 2 depois do carregamento, a mesma régua
    do `encerramento_sm`): a janela de 30 dias do motor alcança a SM da viagem SEGUINTE da mesma
    dupla, e trocar pontos lá mexia em carga antiga (C-1055, lab 07/10).

    `pts_*` = (data, lat, lng, vel). Devolve (pts_novos, n_congelados_tirados, n_cavalo_postos)."""
    if not (congelada_ligado() and _tem_insignia(cur) and pts_carreta and pts_cavalo and cavalo):
        return pts_carreta, 0, 0
    import geocoding as g
    car, cav = pl.mercosul(carreta or ''), pl.mercosul(cavalo or '')
    cur.execute("""SELECT COALESCE(inicio, criada_em), COALESCE(encerrada_em, now() at time zone 'utc')
                     FROM insignia_sm
                    WHERE placa_cavalo_chave = %s AND %s = ANY(string_to_array(COALESCE(carretas_chave, ''), ','))
                      AND (%s::date IS NULL OR (COALESCE(inicio, criada_em) - interval '3 hours')::date
                                               BETWEEN %s::date - 3 AND %s::date + 2)""",
                (cav, car, dia_carga, dia_carga, dia_carga))
    janelas = cur.fetchall()
    if not janelas:
        return pts_carreta, 0, 0
    na_sm = lambda t: any(a <= t <= b for a, b in janelas)
    # odômetro não vem nas triplas: relê da série bruta da 3S
    cur.execute("SELECT data_posicao, odometer FROM embarques_posicoes_historico WHERE placa = ANY(%s) "
                "AND data_posicao BETWEEN %s AND %s", (pl.grafias(carreta), pts_carreta[0][0], pts_carreta[-1][0]))
    odo = dict(cur.fetchall())
    # trechos de repetição
    runs, i, n = [], 0, len(pts_carreta)
    while i < n:
        j = i
        while (j + 1 < n and odo.get(pts_carreta[j + 1][0]) is not None
               and odo.get(pts_carreta[j + 1][0]) == odo.get(pts_carreta[i][0])
               and g.km_entre(pts_carreta[j + 1][1], pts_carreta[j + 1][2],
                              pts_carreta[i][1], pts_carreta[i][2]) <= CONGELADA_TOL_KM):
            j += 1
        if j - i + 1 >= CONGELADA_MIN_PTS:
            runs.append((i, j))
        i = j + 1
    tirar, faixas = set(), []
    for i, j in runs:
        a, b = pts_carreta[i][0], pts_carreta[j][0]
        la, lo = pts_carreta[i][1], pts_carreta[i][2]
        longe = [p for p in pts_cavalo if a <= p[0] <= b and na_sm(p[0])
                 and g.km_entre(p[1], p[2], la, lo) > CONGELADA_LONGE_KM]
        if longe:
            # do primeiro instante em que o cavalo engatado está longe até o fim da repetição
            ini = longe[0][0]
            tirar.update(k for k in range(i, j + 1) if pts_carreta[k][0] >= ini)
            faixas.append((ini, b))
    if not tirar:
        return pts_carreta, 0, 0
    postos = [p for p in pts_cavalo if any(a <= p[0] <= b for a, b in faixas) and na_sm(p[0])]
    novos = sorted([p for k, p in enumerate(pts_carreta) if k not in tirar] + postos, key=lambda p: p[0])
    return novos, len(tirar), len(postos)


# ── O CAVALO TAPA A CARRETA (15/10/26) ──────────────────────────────────────────────────────────────
#
# `descongelar` só age com a SM da GR, e só no trecho congelado. A C-2026-001563 (agregado, sem SM, cavalo
# HIF2439 com 3S própria) mostrou o resto: o aparelho da carreta QXE8460 dormiu em Ibiá na viagem anterior,
# a 3S repetiu "Ibiá" com hora nova por 4 dias e meio, a carreta acordou em BH junto do cavalo (0,2–1,6 km em
# 18 pares andando) e dormiu de novo num posto — e o mapa media 266 km de uma viagem de 1.124, com a posição
# atual 330 km atrás do caminhão. Medido nos 28 dias até 15/10 (`_estudo_2026-10-15/lab/medir_muda.py`): 51
# cargas com a carreta congelada ou muda em parte da viagem e o cavalo provadamente junto, só 27 com SM.

def cavalo_tapa_ligado():
    """`EMBARQUES_CAVALO_TAPA=true`: os buracos da carreta (trecho congelado e silêncio) são tapados pelo cavalo
    da MESMA carga quando há prova de que andam juntos — a SM da GR ou as duas séries. Desligada, é o
    `descongelar` de sempre."""
    return os.getenv('EMBARQUES_CAVALO_TAPA', 'false').strip().lower() == 'true'


TAPA_PAR_KMH = 20          # a CARRETA andando: ponto congelado tem 0, e carreta andando é puxada por algum cavalo
TAPA_JUNTO_KM = 3.0        # carreta × cavalo interpolado no mesmo instante: "junto"
TAPA_SEP_KM = 20.0         # ... "separado" (outro cavalo puxa a carreta)
TAPA_MIN_PARES = 3
TAPA_JUNTO_FRAC = 0.8      # Insignia × 3S: relógios e cadência diferentes — a 80 km/h, 5 min são 6 km
TAPA_SEP_FRAC = 0.05       # 1 par fora em 234 (C-1009) não separa ninguém
TAPA_SEP_FRAC_MAX = 0.2    # acima disto (com >= 3 pares longe) são outra dupla (C-1321: 265 de 273)
TAPA_BURACO_MIN = 30       # silêncio da carreta que vale tapar
TAPA_MOVEU_KM = 5.0        # buraco do meio: a carreta tem de reaparecer longe de onde calou
TAPA_VOLTA_SAIU_KM = 10.0  # o cavalo se afastou do ponto onde a carreta calou...
TAPA_VOLTA_KM = 5.0        # ... e VOLTOU a ele: ela estava lá (estacionada), a volta foi só do cavalo
TAPA_CAUDA_DESVIO_KM = 30.0  # cauda: o cavalo se afastou do destino isto além do mais perto que chegou — parou de ir
TAPA_INTERP_MIN = 10       # vizinhos do cavalo para interpolar a posição num instante
TAPA_UM_VIZINHO_MIN = 3


def _cavalo_em(pts, t):
    """(lat, lng) do cavalo no instante t: linear entre os vizinhos (cada um a <= TAPA_INTERP_MIN), ou o
    vizinho a <= TAPA_UM_VIZINHO_MIN. `pts` ordenado por instante. Sem vizinho perto, None."""
    lo, hi = 0, len(pts)
    while lo < hi:
        m = (lo + hi) // 2
        if pts[m][0] < t:
            lo = m + 1
        else:
            hi = m
    a = pts[lo - 1] if lo > 0 else None
    b = pts[lo] if lo < len(pts) else None
    da = (t - a[0]).total_seconds() / 60 if a else None
    db = (b[0] - t).total_seconds() / 60 if b else None
    if a and b and da <= TAPA_INTERP_MIN and db <= TAPA_INTERP_MIN:
        f = da / (da + db) if da + db > 0 else 0.0
        return (float(a[1]) + f * (float(b[1]) - float(a[1])), float(a[2]) + f * (float(b[2]) - float(a[2])))
    for x, dx in ((a, da), (b, db)):
        if x is not None and dx is not None and dx <= TAPA_UM_VIZINHO_MIN:
            return (float(x[1]), float(x[2]))
    return None


def juntos(pts_carreta, pts_cavalo):
    """As duas séries andam juntas? Pares = ponto da carreta ANDANDO × cavalo interpolado no mesmo instante.
    Devolve (veredito, pares, perto, longe), veredito ∈ 'juntos' · 'separados' · 'inconclusivo' · 'sem par'."""
    import geocoding as g
    pares = perto = longe = 0
    for p in pts_carreta:
        if (p[3] or 0) <= TAPA_PAR_KMH:
            continue
        q = _cavalo_em(pts_cavalo, p[0])
        if not q:
            continue
        d = g.km_entre(float(p[1]), float(p[2]), q[0], q[1])
        if d is None:
            continue
        pares += 1
        perto += d <= TAPA_JUNTO_KM
        longe += d > TAPA_SEP_KM
    if pares >= TAPA_MIN_PARES and perto >= TAPA_JUNTO_FRAC * pares and longe <= TAPA_SEP_FRAC * pares:
        return 'juntos', pares, perto, longe
    if longe >= TAPA_MIN_PARES and longe > TAPA_SEP_FRAC_MAX * pares:
        return 'separados', pares, perto, longe
    return ('inconclusivo' if pares else 'sem par'), pares, perto, longe


def tapar_com_cavalo(cur, carreta, cavalo, pts_carreta, pts_cavalo, dia_carga=None, dest=None, raio_dest_km=20.0):
    """A série da carreta com os buracos tapados pelo cavalo da mesma carga. Três regras, cada uma com o caso:

    0. O PISO é o `descongelar` de hoje (SM): o que ele troca continua trocado; as regras abaixo SOMAM.
    1. PROVA de que os dois andam juntos NESTA janela: a SM da GR desta carga (a de `descongelar`) ou as duas
       séries (`juntos`). Séries que dizem 'separados' vencem a SM — o documento não move carreta (C-1321: outra
       carreta, 265 de 273 pares a mais de 20 km). Sem prova, nada muda.
    2. BURACO = trecho congelado (>= CONGELADA_MIN_PTS no mesmo lugar com o odômetro parado, a partir do ponto
       em que o cavalo DAQUELE instante está a > CONGELADA_LONGE_KM) ou silêncio >= TAPA_BURACO_MIN. Só é tapado
       quem tem prova nas BORDAS — a carreta junto do cavalo onde calou e onde voltou — e se DESLOCOU (reaparece
       a > TAPA_MOVEU_KM de onde calou, e o cavalo NÃO voltou ao ponto onde ela calou). Carreta estacionada
       enquanto o cavalo dá uma volta e vem buscá-la não ganha a volta: o trecho repetido fica (é verdade).
       Na PONTA DE TRÁS (calou e não voltou) não há borda de chegada: vale se calou ANDANDO junto do cavalo e
       antes do destino (só com destino conhecido), e o cavalo tapa até sair do destino — depois disso pode ser
       desengate; para antes se ele desvia do destino ou volta aonde a carreta calou. Na PONTA DA FRENTE
       (a janela já começa com ela congelada, C-1563 em Ibiá) não há borda de saída: vale o trecho congelado cujo
       ponto está longe de onde a carreta reaparece junto do cavalo. Silêncio na frente não se tapa: sem ponto de
       partida não há como saber se ela andou (o cavalo pode estar vindo buscá-la).
    3. O ponto do cavalo entra como ele é. Quem soma odômetro tem de saber de que aparelho é cada ponto — o
       contador da carreta e o do cavalo são outros (`instantes_do_cavalo` no retorno).

    `pts_*` = tuplas ordenadas (instante, lat, lng, vel, ...). Devolve (pts_novos, info); info None = nada mudou,
    senão {'prova', 'tirados', 'postos', 'buracos': [(ini, fim, tipo)], 'cauda': bool,
    'instantes_do_cavalo': set}."""
    if not (cavalo_tapa_ligado() and pts_carreta and pts_cavalo and cavalo and carreta):
        return pts_carreta, None
    # A régua de hoje é o PISO: o que o `descongelar` troca com a SM continua trocado, do mesmo jeito (C-1363 e
    # C-1472 não têm um ponto vivo da carreta — sem borda nenhuma, só a SM as prova). O que vem abaixo SOMA.
    base, b_tir, _b_pos = descongelar(cur, carreta, cavalo, pts_carreta, pts_cavalo, dia_carga)
    ids_car = {id(p) for p in pts_carreta}

    def _info_base():
        if not b_tir:
            return None
        return {'prova': 'sm', 'tirados': b_tir, 'postos': _b_pos, 'buracos': [], 'cauda': False,
                'instantes_do_cavalo': {p[0] for p in base if id(p) not in ids_car}}
    import geocoding as g
    km = lambda a, b: g.km_entre(float(a[0]), float(a[1]), float(b[0]), float(b[1])) or 0.0
    car = pl.mercosul(carreta or '')
    # odômetro: as tuplas do motor já não o trazem (sem_posicao_falsa) — relê da série bruta da 3S
    cur.execute("SELECT data_posicao, odometer FROM embarques_posicoes_historico WHERE placa = ANY(%s) "
                "AND data_posicao BETWEEN %s AND %s", (pl.grafias(carreta), pts_carreta[0][0], pts_carreta[-1][0]))
    odo = dict(cur.fetchall())
    n = len(pts_carreta)
    # ── trechos congelados (a régua de `descongelar`), marcados pelo cavalo DAQUELE instante
    cong = set()
    i = 0
    while i < n:
        j = i
        while (j + 1 < n and odo.get(pts_carreta[j + 1][0]) is not None
               and odo.get(pts_carreta[j + 1][0]) == odo.get(pts_carreta[i][0])
               and g.km_entre(pts_carreta[j + 1][1], pts_carreta[j + 1][2],
                              pts_carreta[i][1], pts_carreta[i][2]) <= CONGELADA_TOL_KM):
            j += 1
        if j - i + 1 >= CONGELADA_MIN_PTS:
            spot = (pts_carreta[i][1], pts_carreta[i][2])
            for k in range(i, j + 1):
                q = _cavalo_em(pts_cavalo, pts_carreta[k][0])
                if q and km(q, spot) > CONGELADA_LONGE_KM:
                    cong.update(range(k, j + 1))
                    break
        i = j + 1
    vivos = [k for k in range(n) if k not in cong]
    # ── prova
    veredito = juntos([pts_carreta[k] for k in vivos], pts_cavalo)[0]
    if veredito == 'separados':
        return base, _info_base()
    prova = 'series' if veredito == 'juntos' else None
    if prova is None and _tem_insignia(cur) and dia_carga is not None:
        cur.execute("""SELECT 1 FROM insignia_sm
                        WHERE placa_cavalo_chave = %s AND %s = ANY(string_to_array(COALESCE(carretas_chave, ''), ','))
                          AND (COALESCE(inicio, criada_em) - interval '3 hours')::date BETWEEN %s::date - 3 AND %s::date + 2
                        LIMIT 1""", (pl.mercosul(cavalo), car, dia_carga, dia_carga))
        prova = 'sm' if cur.fetchone() else None
    if prova is None:
        return base, _info_base()

    def borda(k):
        """a carreta no ponto k estava junto do cavalo?"""
        q = _cavalo_em(pts_cavalo, pts_carreta[k][0])
        return q is not None and km(q, (pts_carreta[k][1], pts_carreta[k][2])) <= TAPA_JUNTO_KM

    def no_destino(p):
        return dest is not None and dest[0] is not None and km((p[1], p[2]), dest) <= raio_dest_km

    def voltou(spot, ta, tb):
        """o cavalo se afastou do ponto onde a carreta calou e voltou a ele entre ta e tb? É a carreta que ficou
        estacionada enquanto o cavalo deu uma volta e veio buscá-la — a bateria de falhas (cenário 3) pegou: o 1o
        ponto depois do buraco, a 80 km/h, já está a 6,7 km do lugar, e só a distância não separava as duas."""
        saiu = False
        for p in pts_cavalo:
            if p[0] <= ta:
                continue
            if p[0] >= tb:
                break
            d = km((p[1], p[2]), spot)
            if d > TAPA_VOLTA_SAIU_KM:
                saiu = True
            elif saiu and d <= TAPA_VOLTA_KM:
                return True
        return False

    faixas = []        # (ini exclusivo, fim exclusivo, tipo)
    # ponta da frente: janela começa congelada
    if vivos and vivos[0] > 0 and 0 in cong:
        f = vivos[0]
        k0 = min(k for k in range(f) if k in cong)
        spot0 = (pts_carreta[k0][1], pts_carreta[k0][2])
        if (borda(f) and km(spot0, (pts_carreta[f][1], pts_carreta[f][2])) > TAPA_MOVEU_KM
                and not any(km((p[1], p[2]), spot0) <= TAPA_VOLTA_KM
                            for p in pts_cavalo if pts_carreta[k0][0] <= p[0] < pts_carreta[f][0])):
            faixas.append((pts_carreta[k0][0] - timedelta(seconds=1), pts_carreta[f][0], 'frente'))
    # meio
    for a, b in zip(vivos, vivos[1:]):
        ta, tb = pts_carreta[a][0], pts_carreta[b][0]
        tem_cong = any(k in cong for k in range(a + 1, b))
        if not tem_cong and (tb - ta).total_seconds() / 60 < TAPA_BURACO_MIN:
            continue
        if not (borda(a) and borda(b)):
            continue
        if km((pts_carreta[a][1], pts_carreta[a][2]), (pts_carreta[b][1], pts_carreta[b][2])) <= TAPA_MOVEU_KM:
            continue
        # onde ela calou: o ponto congelado, se o buraco tem; senão o último vivo
        kc = next((k for k in range(a + 1, b) if k in cong), None)
        spot = (pts_carreta[kc][1], pts_carreta[kc][2]) if kc is not None else (pts_carreta[a][1], pts_carreta[a][2])
        if voltou(spot, ta, tb):
            continue
        faixas.append((ta, tb, 'meio'))
    # ponta de trás: calou (ou congelou) andando junto do cavalo, antes do destino
    cauda = False
    if vivos:
        z = vivos[-1]
        pz = pts_carreta[z]
        # só com destino: é ele que diz até onde o cavalo ainda está levando a carreta
        if (dest is not None and dest[0] is not None and (pz[3] or 0) > TAPA_PAR_KMH and borda(z) and not no_destino(pz)
                and (z < n - 1 or (pts_cavalo[-1][0] - pz[0]).total_seconds() / 60 >= TAPA_BURACO_MIN)):
            fim_c, dentro, saiu = None, False, False
            dmin = km((pz[1], pz[2]), dest)
            for p in pts_cavalo:
                if p[0] <= pz[0]:
                    continue
                if no_destino(p):
                    dentro = True
                elif dentro:
                    fim_c = p[0] + timedelta(seconds=1)     # o 1o ponto fora depois de ter estado: entra
                    break
                else:
                    dd = km((p[1], p[2]), dest)
                    dz = km((p[1], p[2]), (pz[1], pz[2]))
                    saiu = saiu or dz > TAPA_VOLTA_SAIU_KM
                    # parou de ir para o destino (desviou) ou voltou aonde a carreta calou: daqui em diante pode ser
                    # o cavalo sem ela (desengate no caminho) — o tapa para ANTES deste ponto
                    if dd > dmin + TAPA_CAUDA_DESVIO_KM or (saiu and dz <= TAPA_VOLTA_KM):
                        fim_c = p[0]
                        break
                    dmin = min(dmin, dd)
            faixas.append((pz[0], fim_c or (pts_cavalo[-1][0] + timedelta(seconds=1)), 'cauda'))
            cauda = True
    if not faixas:
        return base, _info_base()
    dentro_f = lambda t: any(a < t < b for a, b, _ in faixas)
    tirar = {k for k in cong if dentro_f(pts_carreta[k][0])}
    if cauda:
        tirar |= {k for k in range(vivos[-1] + 1, n)}      # o que vem depois do último vivo é repetição
    postos = [p for p in pts_cavalo if dentro_f(p[0])]
    if not postos and not tirar:
        return base, _info_base()
    # SOMA sobre o piso: tira da base os pontos da carreta dos buracos aceitos e acrescenta os do cavalo que ela
    # ainda não tem (identidade da tupla: o `descongelar` devolve os MESMOS objetos)
    fora = {id(pts_carreta[k]) for k in tirar}
    ja = {id(p) for p in base}
    novos = sorted([p for p in base if id(p) not in fora] + [p for p in postos if id(p) not in ja],
                   key=lambda p: p[0])
    do_cavalo = {p[0] for p in novos if id(p) not in ids_car}
    return novos, {'prova': prova if not b_tir else prova + '+sm', 'tirados': len(tirar) + b_tir,
                   'postos': len(do_cavalo), 'buracos': [(a, b, t) for a, b, t in faixas], 'cauda': cauda,
                   'instantes_do_cavalo': do_cavalo}


def terceiro_gps_ligado():
    """`EMBARQUES_TERCEIRO_GPS=true` (08/10/26): o Terceiro nasce se a Insignia tem GPS do cavalo — com
    ou sem SM. Sem a chave, a trava é a de 06/10 (só com SM)."""
    return os.getenv('EMBARQUES_TERCEIRO_GPS', 'false').strip().lower() == 'true'


def tem_gps(cur, cavalo, carreta, dia, origem=None):
    """A Insignia acompanha este conjunto DESDE o carregamento? SM (como antes) OU uma posição do CAVALO
    vista pela coleta entre o dia anterior e o FIM do dia do carregamento (BRT).

    Por que (08/10/26): a GR rastreia o cavalo de boa parte dos terceiros sem SM aberta para a nossa
    unidade (5 de 6 cavalos das ordens de terceiro de 07/10, nenhum em SM). Por que a janela: a coleta
    das cargas (`insignia_coleta.placas_de_cargas`) passa a seguir o cavalo quando o manifesto aparece —
    viagem NOVA tem ponto no próprio dia e nasce com a saída observada; viagem que já rodava antes de a
    coleta começar só tem ponto de depois, e nasceria quebrada (chegada no 1º ponto coletado, sem saída,
    km ~0 — MQU7E81 no lab: 08/10 12:17 × 01:38 real). Paradas NÃO contam: vêm com 30 dias de histórico e
    deixariam esse estoque passar."""
    if tem_sm(cur, cavalo, carreta, dia, origem):
        return True
    if not _tem_insignia(cur):
        return False
    cav = pl.mercosul(cavalo or '')
    if not cav:
        return False
    cur.execute("""SELECT EXISTS (SELECT 1 FROM insignia_posicoes WHERE placa_chave = %s
                                    AND em >= %s::date - 1 AND em < %s::date + interval '1 day 3 hours')""",
                (cav, dia, dia))
    return bool(cur.fetchone()[0])


def tem_sm(cur, cavalo, carreta, dia, origem=None):
    """Existe SM da Insignia para este conjunto perto deste dia? É a trava do Terceiro no robô:
    sem SM não há GPS, e 75% dos manifestos de terceiro nunca têm o seguinte da mesma carreta —
    a carga nasceria 'Aberta' e nunca sairia (HANDOFF-INSIGNIA §8).

    `origem` = (lat, lng) da origem da carga: com EMBARQUES_SM_DA_CARGA, SM de outra perna do mesmo
    cavalo não conta (C-1464 nasceu pela SM da viagem seguinte)."""
    if not _tem_insignia(cur):
        return False
    cav, car = pl.mercosul(cavalo or ''), pl.mercosul(carreta or '')
    if not (cav or car):
        return False
    cur.execute("""SELECT origem_lat, origem_lng FROM insignia_sm
                    WHERE ((%s <> '' AND placa_cavalo_chave = %s)
                           OR (%s <> '' AND %s = ANY(string_to_array(COALESCE(carretas_chave, ''), ','))))
                      AND (COALESCE(inicio, criada_em) - interval '3 hours')::date BETWEEN %s AND %s""",
                (cav or '', cav or '', car or '', car or '', dia - timedelta(days=2), dia + timedelta(days=2)))
    return any(_sm_origem_ok(la, ln, origem) for la, ln in cur.fetchall())


# ── PARADAS DA GR COMO FONTE DE SAÍDA E DE CHEGADA (13/10/26, itens 2 + 12) ──────────────────────

def paradas_gr_ligado():
    """`EMBARQUES_PARADAS_GR=true` (13/10/26): a parada da GR dá a SAÍDA e a CHEGADA que o GPS não pôde
    ver porque a coleta da Insignia começou DEPOIS do evento. Decisão do Gabriel (13/10):
      * saída  = fim da ÚLTIMA parada na cidade de origem antes do 1º ponto coletado;
      * chegada = início da 1ª parada na cidade de destino, quando a chegada do GPS é o 1º ponto.
    Medido no dump de 13/10 17:13: 11 de 13 terceiros sem saída ganham saída; nas 17 cargas em que a
    chegada do GPS e a 1ª parada na cidade de destino existem, as duas batem a 0–14 min; nas 4 com a
    chegada carimbada no 1º ponto (C-1426..1429) a GR a põe 7,8–14 h antes. Exige a fonte ligada."""
    return ligado() and os.getenv('EMBARQUES_PARADAS_GR', 'false').strip().lower() == 'true'


_RE_REFERENCIA = re.compile(r'^\s*\d+(?:[.,]\d+)?\s*KM\s+[A-Z]+\s+DE\s')
_RE_CIDADE_UF = re.compile(r'(.+)-([A-Z]{2})$')


def cidade_da_parada(local):
    """('JAPERI', 'RJ') a partir do texto da parada da GR, ou None quando o texto NÃO diz a cidade.

    São dois formatos, e a tecnologia decide qual (medido em 13/10 nas paradas desde 01/10):
      * ENDEREÇO — Omnilink, Onixsat, Sascar: 'Rua C. Rodrigues de Andrade - JAPERI - RJ - JAPERI-RJ'. O
        sufixo CIDADE-UF é o município;
      * PONTO DE REFERÊNCIA — Autotrac (9.776 de 9.868): '2.59 KM SO DE PRE GOIANIA, GO - BRASIL, 0.00 KM/H -
        PRE GOIANIA-GO'. O sufixo é o ponto de referência ('PRE GOIANIA', 'AEROPORTO DE GOIANIA', 'POSTO BOA
        VIAGEM'), não a cidade. Lido como cidade, a C-2026-001510 ganhava a saída de uma parada 'DE GOIANIA'
        do dia ANTERIOR — 19 h antes da real, carregada em 'PRE GOIANIA'. Aqui vira cidade desconhecida."""
    import geocoding as g
    t = g.normalizar_cidade(local)
    if not t or _RE_REFERENCIA.match(t):
        return None
    m = _RE_CIDADE_UF.match(re.split(r'\s*-\s+', t)[-1].strip())
    return (m.group(1).strip(), m.group(2)) if m else None


def paradas_juntadas(cur, placa, ini, fim):
    """Paradas da GR da placa que tocam [ini, fim], com as linhas SOBREPOSTAS juntadas: [(inicio, fim, cidade)].

    A coleta busca as paradas de hora em hora e a GR devolve a parada EM CURSO com o início deslizando a
    cada busca — a mesma parada longa vira várias linhas sobrepostas (item 7 da lista de 13/10: 12% das
    linhas). A tabela fica como está (é fato bruto, como a posição — §23.2 do HANDOFF-EMBARQUES); juntar é
    papel de quem lê. A cidade é a da primeira linha: linha sobreposta é o mesmo lugar."""
    chave = pl.mercosul(placa or '')
    if not chave:
        return []
    if 'paradas' not in _TABELAS:
        cur.execute("SELECT to_regclass('insignia_paradas') IS NOT NULL")
        _TABELAS['paradas'] = bool(cur.fetchone()[0])
    if not _TABELAS['paradas']:
        return []
    cur.execute("""SELECT inicio, COALESCE(fim, inicio), local_inicio FROM insignia_paradas
                    WHERE placa_chave = %s AND COALESCE(fim, inicio) >= %s AND inicio <= %s
                    ORDER BY inicio""", (chave, ini, fim))
    out = []
    for i, f, loc in cur.fetchall():
        if out and i <= out[-1][1]:
            out[-1][1] = max(out[-1][1], f)
        else:
            out.append([i, f, cidade_da_parada(loc)])
    return [tuple(x) for x in out]


def paradas_valem(cur, placa):
    """A parada da GR pode decidir esta placa? Chave ligada e a placa é das que a série lê da Insignia
    (`_filtro_insignia`: a 3S não a rastreia e está no escopo). Placa da 3S tem a série da 3S, que
    começa antes e vê o evento — a régua das paradas não é para ela."""
    if not (paradas_gr_ligado() and _tem_insignia(cur)):
        return False
    chave = pl.mercosul(placa or '')
    if not chave:
        return False
    cur.execute(f"SELECT EXISTS (SELECT 1 FROM insignia_placas i WHERE i.placa_chave = %s AND {_filtro_insignia()})",
                (chave,))
    return bool(cur.fetchone()[0])


def _piso_do_dia(dia_carga):
    """00:00 de Brasília do dia ANTERIOR ao carregamento, em UTC (as paradas são gravadas em UTC)."""
    from datetime import datetime, time
    return datetime.combine(dia_carga, time()) - timedelta(hours=21)


def saida_pela_parada(cur, placa, cidade, uf, dia_carga, primeiro_ponto, ate=None):
    """Fim da ÚLTIMA parada na cidade de origem antes do 1º ponto coletado, ou None.

    Para quem chama: só quando o GPS não deu saída e a série começa com o caminhão JÁ FORA da origem — a
    coleta da Insignia passou a seguir o cavalo depois de ele sair (documento, OC ou SM emitidos depois da
    saída: C-1484, C-1490, C-1506). Mede outra coisa que a saída do GPS: a GR marca quando o caminhão
    deixou o PONTO de carga, o GPS quando deixou o raio de 30 km. Onde as duas existem (21 terceiros), a da
    GR vem antes — mediana 50 min; 15 de 21 a até 1 h; as mais longas são o GPS contando a parada num posto
    dentro dos 30 km (C-1504: saiu de Extrema 06:04, posto em Bragança até 08:54, GPS 09:09).

    Parada de cidade DESCONHECIDA depois da escolhida (ponto de referência do Autotrac) = não dá para dizer
    que o caminhão ainda estava na origem: None. Silêncio, não palpite."""
    import geocoding as g
    alvo = (g.normalizar_cidade(cidade), (uf or '').strip().upper())
    if not alvo[0] or not primeiro_ponto:
        return None
    ini = _piso_do_dia(dia_carga)
    fim = min(primeiro_ponto, ate) if ate else primeiro_ponto
    if fim <= ini:
        return None
    ps = [p for p in paradas_juntadas(cur, placa, ini, fim) if ini <= p[1] <= fim]
    idx = [k for k, p in enumerate(ps) if p[2] == alvo]
    if not idx or any(p[2] is None for p in ps[idx[-1] + 1:]):
        return None
    return ps[idx[-1]][1]


def chegada_pela_parada(cur, placa, cidade, uf, dia_carga, piso, ate, dist_km=None, vel_max=None):
    """Início da 1ª parada na cidade de destino entre `piso` (a saída; sem ela, 00:00 BRT do dia anterior ao
    carregamento) e `ate` (o 1º ponto coletado), ou None.

    Para quem chama: só quando a chegada do GPS É o 1º ponto — a coleta começou com o caminhão já no
    destino (trava antiga de 08/10: C-1426..1429 com a chegada 7,8–14 h depois da real). Onde a chegada do
    GPS e a parada existem juntas, batem a 0–14 min (17 cargas): a régua é a mesma "chegou na cidade".

    Parada de cidade DESCONHECIDA antes da escolhida pode ser a própria chegada: None. Com `dist_km` e
    `vel_max`, a chegada tem de ser fisicamente possível a partir do piso (a mesma guarda do motor)."""
    import geocoding as g
    alvo = (g.normalizar_cidade(cidade), (uf or '').strip().upper())
    if not alvo[0] or not ate:
        return None
    ini = max(piso, _piso_do_dia(dia_carga)) if piso else _piso_do_dia(dia_carga)
    if ate <= ini:
        return None
    for p in paradas_juntadas(cur, placa, ini, ate):
        if not (ini <= p[0] <= ate):
            continue
        if p[2] is None:
            return None
        if p[2] == alvo:
            if piso and dist_km and vel_max:
                h = (p[0] - piso).total_seconds() / 3600.0
                if not (h > 0 and float(dist_km) / h <= vel_max):
                    return None
            return p[0]
    return None
