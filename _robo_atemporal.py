# -*- coding: utf-8 -*-
"""ROBO ATEMPORAL — reconstroi o ciclo de vida da viagem lendo o HISTORICO.

Por que existe: o worker de hoje e um AMOSTRADOR AO VIVO. A cada 60 s ele pergunta "onde o
caminhao esta agora?" e so age se a posicao estiver fresca (FRESCOR_H=12 h), parada e dentro
do raio. O maior buraco mediano do rastreador de carreta e 12,0 h — os dois numeros colidem,
e por isso 144 chegadas que ESTAO no banco nunca foram marcadas. Medido em 08/09/2026.

E pior: uma transicao perdida mata as seguintes. O worker so procura chegada com status
'Em rota', e o status so avanca se a saida foi vista. Com saida gravada, a chegada aparece
em 73% dos casos; sem saida gravada, em 2%. Um estado por vez nao se recupera.

Este robo faz o contrario: le a serie inteira de posicoes de uma vez e deriva o ciclo
completo (saida -> chegada -> entrega) num passe so. Nao depende de estar olhando na hora
certa, entao pode rodar quantas vezes quiser — cada rodada reabre as perguntas e ve se o
mundo respondeu. E o lado convergente: o passado fica mais correto com o tempo em vez de
congelado errado.

REGRAS (secao 4.3 do handoff):
  * saiu da origem        GPS > 30 km depois de ter estado la  -> Em rota
  * chegou ao destino     GPS <= 20 km  (ou <= 60 km com parada >= 2 h, p/ metropole)
  * entregue              saiu do destino, OU ficou 24 h nele
  * manifesto novo da mesma carreta encerra a anterior — SO com prova de chegada (secao 18.2)
  * SILENCIO NUNCA E EVENTO: sem evidencia, nao mexe. Fica pendente e volta amanha.

O QUE ELE NAO FAZ:
  * nao reabre carga fechada. Se o fato esta certo e o instante errado, corrige o instante.
  * nao inventa entrega onde nao houve chegada — marca 'sem_prova_revisar' para o humano.
  * nao encosta em carga lancada a mao (use --tudo para incluir).

    python -X utf8 _robo_atemporal.py                    # dry-run
    python -X utf8 _robo_atemporal.py --aplicar
    python -X utf8 _robo_atemporal.py --aplicar --tudo   # inclui carga lancada a mao
"""
import os, sys, csv, argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, time as _time
sys.path.insert(0, r'c:/Phyton-Projetos/Tabela Auditoria')
os.chdir(r'c:/Phyton-Projetos/Tabela Auditoria')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import psycopg2
from dotenv import load_dotenv
load_dotenv('.env')
import geocoding, placas as pl

RAIO_CHEGADA = 20.0
RAIO_METRO = 60.0
PARADA_MIN_H = 2.0
RAIO_ORIGEM = 30.0
RAIO_SAIDA_DESTINO = 30.0
DWELL_H = 24.0
JANELA_FUTURO_D = 20      # ate onde procurar evidencia depois do carregamento
VEL_MAX_CRIVEL = 100.0    # km/h medios: acima disso o par saida/chegada nao e a mesma viagem
AUTOR = 'Robo atemporal'

ap = argparse.ArgumentParser()
ap.add_argument('--desde', default='2026-08-01')
ap.add_argument('--ate', default='2026-09-08')
ap.add_argument('--aplicar', action='store_true')
ap.add_argument('--tudo', action='store_true', help='inclui carga lancada a mao')
ap.add_argument('--csv', default='_robo_atemporal.csv')
A = ap.parse_args()
HOJE = datetime.now()

cn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                      user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = cn.cursor()

filtro = '' if A.tudo else 'AND COALESCE(c.criada_por_robo,FALSE)'
cur.execute(f"""SELECT c.id,c.numero,c.status,c.encerrada_motivo,COALESCE(c.entregue_auto,FALSE),
                       COALESCE(c.saida_auto,FALSE),c.data_carregamento,c.data_saida_real,
                       c.inicio_viagem,c.no_local_desde,c.data_conclusao,
                       c.origem_latitude,c.origem_longitude,c.cavalo_placa,c.carreta1_placa,
                       c.carreta2_placa,COALESCE(c.viagem_vazia,FALSE),
                       COALESCE(c.criada_por_robo,FALSE),c.distancia_planejada_km,
                       d.latitude,d.longitude,d.cidade
                  FROM embarques_cargas c
                  LEFT JOIN embarques_cargas_destinos d ON d.carga_id=c.id
                       AND d.ordem=(SELECT MIN(ordem) FROM embarques_cargas_destinos x WHERE x.carga_id=c.id)
                 WHERE c.data_carregamento BETWEEN %s AND %s
                   AND c.status <> 'Cancelada' {filtro}
                 ORDER BY c.data_carregamento, c.id""", (A.desde, A.ate))
CARGAS = cur.fetchall()

# "manifesto novo da mesma carreta": 1 carga = 1 manifesto, entao a proxima carga da placa serve
prox = defaultdict(list)
for r in CARGAS:
    if not r[16] and r[14]:
        prox[pl.mercosul(r[14])].append((r[6], r[1], r[0]))
for k in prox:
    prox[k].sort()

def serie(placa, ini, fim):
    if not placa:
        return []
    cur.execute("""SELECT data_posicao,latitude,longitude,velocidade
                     FROM embarques_posicoes_historico
                    WHERE placa=ANY(%s) AND data_posicao>=%s AND data_posicao<%s
                    ORDER BY data_posicao""",
                (pl.grafias(str(placa).strip().upper()), ini, fim))
    return [(d, float(la), float(ln), v) for d, la, ln, v in cur.fetchall() if la is not None]


def perto_com_parada(dd, raio_estrito, raio_largo):
    """(instante, como) da 1a presenca — raio estrito, ou raio largo com parada longa.

    A parada e o que separa CHEGOU de PASSOU POR PERTO: medido em agosto, quem chegou num CD
    fora do centroide ficou de 20 a 214 h por perto; quem so passou fica minutos."""
    p = next((d for d, k in dd if k <= raio_estrito), None)
    if p:
        return p, 'raio'
    largo = [(d, k) for d, k in dd if k <= raio_largo]
    if len(largo) > 1:
        h = (largo[-1][0] - largo[0][0]).total_seconds() / 3600
        if h >= PARADA_MIN_H:
            return largo[0][0], f'metropole({min(k for _, k in largo):.0f}km/{h:.0f}h)'
    return None, None


mudancas, resumo, detalhe = [], Counter(), []

for (cid, num, status, motivo, auto, saida_auto, dcarg, dsaida, inicio, nolocal, dconc,
     ola, oln, cav, c1, c2, vazia, robo, dist_plan, dla, dln, dcid) in CARGAS:

    ini = datetime.combine(dcarg, _time()) - timedelta(hours=12)
    fim = min(HOJE, datetime.combine(dcarg, _time()) + timedelta(days=JANELA_FUTURO_D))

    # SENSOR: a carreta e a identidade da carga; se ela nao fala, o cavalo mede.
    pts, sensor = [], None
    for placa, papel in ((c1, 'carreta1'), (c2, 'carreta2'), (cav, 'cavalo')):
        if placa:
            s = serie(placa, ini, fim)
            if s:
                pts, sensor = s, f'{papel}:{placa}'
                break
    if not pts:
        resumo['cega — sem posicao, nada a decidir'] += 1
        continue

    d_org = ([(d, geocoding.km_entre(la, ln, float(ola), float(oln))) for d, la, ln, v in pts]
             if ola is not None else [])
    d_org = [(d, k) for d, k in d_org if k is not None]
    d_dst = ([(d, geocoding.km_entre(la, ln, float(dla), float(dln))) for d, la, ln, v in pts]
             if dla is not None else [])
    d_dst = [(d, k) for d, k in d_dst if k is not None]

    # ── SAIDA: precisa ter estado na origem e depois se afastado
    # A saida e o ULTIMO ponto visto DENTRO do raio da origem, nao o primeiro visto fora.
    # Com a carreta transmitindo a cada 12 h, o "primeiro ponto fora" pode estar a centenas
    # de km — e ai o par saida/chegada descrevia 94 km em 20 min (282 km/h) na C-2026-000642.
    # O ultimo ponto no patio e o instante da partida a menos do intervalo de amostragem.
    n_saida, t_org = None, None
    if d_org:
        t_org, _como = perto_com_parada(d_org, RAIO_ORIGEM, RAIO_METRO)
        if t_org:
            _saiu = next((d for d, k in d_org if k > RAIO_ORIGEM and d > t_org), None)
            if _saiu:
                _dentro = [d for d, k in d_org if k <= RAIO_ORIGEM and t_org <= d < _saiu]
                n_saida = max(_dentro) if _dentro else _saiu

    # ── CHEGADA: SO DEPOIS DE TER SAIDO.
    # A 1a versao varria a serie inteira e por isso gravou 18 cargas com chegada ANTERIOR a
    # saida — o veiculo passa perto do destino antes de partir (na viagem vazia isso e quase
    # garantido: a perna vazia termina onde ele ja estava). O piso e a saida; sem saida, o
    # instante em que esteve na origem; sem nenhum dos dois, a serie inteira.
    # O piso tem de ser o MAIOR entre a saida que o GPS mostra e a que ja esta gravada.
    # Usar so a do GPS fazia o robo gravar uma chegada valida para ele e invalida para a
    # regra de coerencia (que olha o banco) — ele gravava, descartava e recalculava a cada
    # passada, oscilando em 8 cargas sem nunca convergir.
    _pisos = [x for x in (n_saida, dsaida) if x is not None]
    piso = max(_pisos) if _pisos else t_org
    # E um TETO, simetrico do piso: a viagem nao pode ter chegada DEPOIS de a carreta ja
    # ter comecado outra. Sem ele a V-2026-000015 aceitava chegada em 01/09 numa viagem que
    # o manifesto seguinte encerrou em 27/08 — conclusao anterior a chegada, oscilando.
    teto = None
    if c1:
        _seg = [dt for dt, nm, i in prox.get(pl.mercosul(c1), []) if dt > dcarg and i != cid]
        if _seg:
            teto = datetime.combine(_seg[0], _time()) + timedelta(days=1)
    d_dst_validos = [(d, k) for d, k in d_dst
                     if (piso is None or d >= piso) and (teto is None or d <= teto)]
    n_cheg, como_cheg = (perto_com_parada(d_dst_validos, RAIO_CHEGADA, RAIO_METRO)
                         if d_dst_validos else (None, None))

    # GUARDA DE VELOCIDADE: o par saida/chegada tem de ser fisicamente possivel. Se implicar
    # mais de VEL_MAX_CRIVEL km/h medios, esses dois instantes nao descrevem a mesma viagem —
    # tenta a proxima chegada candidata e, se nenhuma servir, nao afirma chegada nenhuma.
    # Silencio continua sendo melhor que um numero bonito e errado.
    if n_cheg and n_saida and dist_plan:
        _cands = [d for d, k in d_dst_validos if k <= RAIO_CHEGADA]
        _ok = None
        for _c in _cands:
            _h = (_c - n_saida).total_seconds() / 3600.0
            if _h > 0 and float(dist_plan) / _h <= VEL_MAX_CRIVEL:
                _ok = _c
                break
        if _ok != n_cheg:
            if _ok:
                n_cheg, como_cheg = _ok, (como_cheg or 'raio') + '+velocidade'
                resumo['guarda de velocidade: chegada recalculada'] += 1
            else:
                n_cheg, como_cheg = None, None
                resumo['guarda de velocidade: chegada descartada (impossivel)'] += 1

    # ── ENTREGA: saiu do destino, ou ficou DWELL_H nele
    n_conc, n_motivo = None, None
    if n_cheg:
        depois = [(d, k) for d, k in d_dst_validos if d > n_cheg]
        saiu_dst = next((d for d, k in depois if k > RAIO_SAIDA_DESTINO), None)
        if saiu_dst:
            n_conc, n_motivo = saiu_dst, 'gps_saiu_do_destino'
        else:
            ultima = depois[-1][0] if depois else n_cheg
            if (ultima - n_cheg).total_seconds()/3600 >= DWELL_H:
                n_conc, n_motivo = n_cheg + timedelta(hours=DWELL_H), 'gps_dwell_destino'

    # ── manifesto novo da MESMA CARRETA encerra a anterior — so COM prova de chegada
    if n_cheg and not n_conc and c1:
        seg = [(dt, nm) for dt, nm, i in prox.get(pl.mercosul(c1), []) if dt > dcarg and i != cid]
        if seg:
            n_conc, n_motivo = datetime.combine(seg[0][0], _time()), 'manifesto_novo_carreta'

    # O manifesto nao tem hora, entao a data do seguinte vira 00:00 e pode cair ANTES da
    # chegada do mesmo dia. A viagem nao termina antes de chegar: o piso da conclusao e a
    # chegada. Travar aqui, na origem do calculo, e o que faz a passada ser idempotente.
    if n_conc and n_cheg and n_conc < n_cheg:
        n_conc = n_cheg

    # ── estado derivado. SILENCIO NUNCA E EVENTO.
    if n_conc:
        n_status = 'Entregue'
    elif n_cheg:
        n_status = 'No destino'
    elif n_saida:
        n_status = 'Em rota'
    else:
        n_status = status if status != 'Aberta' else 'Aberta'

    # Nunca reabrir: se o fato esta certo e o instante errado, corrige o instante.
    # Mas ENTREGUE sem prova de CHEGADA nao pode passar calado — a saida da origem nao prova
    # entrega nenhuma (foi o erro que deixava 18 cargas escaparem na 1a versao deste robo).
    if status == 'Entregue':
        n_status = 'Entregue'
        if not n_cheg and not motivo:
            n_motivo = 'sem_prova_revisar'              # sinaliza p/ humano, nao reabre

    campos = {}
    if n_saida and not dsaida:
        campos['data_saida_real'] = n_saida
        campos['saida_auto'] = True
    if n_saida and not inicio:
        campos['inicio_viagem'] = n_saida
    # UMA fonte de verdade. Antes o robo so gravava a chegada quando o campo estava vazio e
    # respeitava a existente mesmo contradizendo o que ele apurou — a conclusao saia da SUA
    # chegada e a coerencia conferia contra a do BANCO, e 3 cargas ficavam oscilando para
    # sempre. Com prova propria, a dele manda; sem prova, nao mexe.
    if n_cheg and nolocal != n_cheg:
        # Sem tolerancia. Com folga de 1 h sobravam 4 cargas oscilando: o robo mantinha a
        # chegada do banco (24 min de diferenca) mas derivava a conclusao da SUA, que ficava
        # 24 min antes — e a coerencia, estrita, corrigia a cada passada. Tolerar na escrita
        # e ser estrito na conferencia nunca converge.
        campos['no_local_desde'] = n_cheg
    elif nolocal and d_dst and ((piso and nolocal < piso) or (teto and nolocal > teto)):
        # A chegada guardada esta FORA da janela possivel da viagem (antes de sair, ou depois
        # de a carreta ja ter partido em outra). Nao e questao de o robo nao ter visto: e
        # impossivel. Apagar fecha o princípio de fonte unica — sem isso a regra de coerencia
        # segue comparando com um valor que o proprio robo ja rejeitou, e oscila.
        campos['no_local_desde'] = None
        resumo['chegada guardada fora da janela da viagem (apagada)'] += 1
    if n_conc and not dconc:
        campos['data_conclusao'] = n_conc
        campos['entregue_auto'] = True
    elif n_conc and dconc:
        erro_h = abs((dconc - n_conc).total_seconds())/3600
        if erro_h > 24:                                  # recorte errado: corrige o instante
            campos['data_conclusao'] = n_conc

    # ── COERENCIA TEMPORAL: saida <= chegada <= conclusao. Nao e refinamento, e o que
    # impede a tela de montar janela negativa — o trajeto de carga entregue vai ate a
    # data_conclusao, entao conclusao anterior a saida zera o "km percorrido" no mapa.
    # (Foi o que aconteceu em 9 cargas na 1a rodada deste robo.)
    ref_saida = campos.get('data_saida_real', dsaida)
    ref_cheg = campos.get('no_local_desde', nolocal)
    ref_conc = campos.get('data_conclusao', dconc)
    if ref_conc and ref_cheg and ref_conc < ref_cheg:
        campos['data_conclusao'] = n_conc or ref_cheg
        resumo['coerencia: conclusao anterior a chegada'] += 1
    elif ref_conc and ref_saida and ref_conc < ref_saida:
        campos['data_conclusao'] = n_conc or ref_cheg or ref_saida
        resumo['coerencia: conclusao anterior a saida'] += 1
    # A saida GUARDADA tambem entra na guarda de velocidade. O robo so preenchia saida vazia,
    # entao um par impossivel vindo de producao sobrevivia: 12 cargas seguiam com T5 depois de
    # a guarda ja rodar. Se a guardada e impossivel e a apurada e possivel, a apurada manda.
    if ref_saida and ref_cheg and dist_plan and ref_cheg > ref_saida:
        _h = (ref_cheg - ref_saida).total_seconds() / 3600.0
        if _h > 0 and float(dist_plan) / _h > VEL_MAX_CRIVEL and n_saida and n_saida != ref_saida:
            _h2 = (ref_cheg - n_saida).total_seconds() / 3600.0
            if _h2 > 0 and float(dist_plan) / _h2 <= VEL_MAX_CRIVEL:
                campos['data_saida_real'] = n_saida
                campos['inicio_viagem'] = n_saida
                ref_saida = n_saida
                resumo['guarda de velocidade: saida guardada era impossivel'] += 1

    if ref_cheg and ref_saida and ref_cheg < ref_saida:
        # nao ha chegada valida antes da saida: descarta a chegada em vez de mentir
        campos['no_local_desde'] = None
        resumo['coerencia: chegada anterior a saida (descartada)'] += 1
    if n_status != status:
        campos['status'] = n_status
    if n_motivo and not motivo:
        campos['encerrada_motivo'] = n_motivo

    if campos:
        mudancas.append((cid, num, campos, sensor, como_cheg))
        for k in campos:
            resumo[f'campo: {k}'] += 1
        detalhe.append({
            'carga': num, 'status_antes': status, 'status_depois': n_status,
            'sensor': sensor, 'chegada': str(n_cheg)[:16] if n_cheg else '',
            'como': como_cheg or '', 'saida': str(n_saida)[:16] if n_saida else '',
            'conclusao': str(n_conc)[:16] if n_conc else '', 'motivo': n_motivo or '',
            'campos': ','.join(sorted(campos)),
        })
        resumo['CARGAS ALTERADAS'] += 1
    else:
        resumo['ja estava correta'] += 1

print(f'ROBO ATEMPORAL — {len(CARGAS)} cargas entre {A.desde} e {A.ate}'
      f'{" (inclui lancadas a mao)" if A.tudo else " (so as do robo)"}')
print(f'{"[DRY-RUN] " if not A.aplicar else ""}janela de evidencia: {JANELA_FUTURO_D} dias\n')
for k, v in sorted(resumo.items(), key=lambda x: (not x[0].startswith('CARGAS'), -x[1])):
    print(f'   {k:<42} {v:>4}')

trans = Counter((d['status_antes'], d['status_depois']) for d in detalhe if 'status' in d['campos'])
if trans:
    print('\nTRANSICOES DE STATUS')
    for (a, b), n in trans.most_common():
        print(f'   {a:<14} -> {b:<14} {n:>4}')
mot = Counter(d['motivo'] for d in detalhe if d['motivo'])
if mot:
    print('\nMOTIVO DO FECHAMENTO')
    for m, n in mot.most_common():
        print(f'   {m:<26} {n:>4}')
como = Counter(d['como'].split('(')[0] for d in detalhe if d['como'])
if como:
    print('\nCOMO A CHEGADA FOI PROVADA')
    for m, n in como.most_common():
        print(f'   {m:<26} {n:>4}')

with open(A.csv, 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.DictWriter(f, ['carga', 'status_antes', 'status_depois', 'sensor', 'saida',
                           'chegada', 'como', 'conclusao', 'motivo', 'campos'], delimiter=';')
    w.writeheader(); w.writerows(detalhe)
print(f'\n-> {A.csv} ({len(detalhe)} cargas)')

if not A.aplicar:
    print('\n[DRY-RUN] nada gravado. Use --aplicar para gravar.')
    cn.close(); sys.exit(0)

n = 0
for cid, num, campos, sensor, como in mudancas:
    cur.execute("""SELECT status,data_saida_real,inicio_viagem,no_local_desde,data_conclusao,
                          encerrada_motivo,saida_auto,entregue_auto
                     FROM embarques_cargas WHERE id=%s""", (cid,))
    antes = dict(zip(['status', 'data_saida_real', 'inicio_viagem', 'no_local_desde',
                      'data_conclusao', 'encerrada_motivo', 'saida_auto', 'entregue_auto'],
                     cur.fetchone()))
    sets = ', '.join(f'{k}=%s' for k in campos)
    cur.execute(f"UPDATE embarques_cargas SET {sets}, atualizado_em=NOW() WHERE id=%s",
                list(campos.values()) + [cid])
    for k, v in campos.items():
        cur.execute("""INSERT INTO embarques_cargas_log
                          (carga_id,campo,valor_anterior,valor_novo,usuario_nome,editado_em)
                       VALUES (%s,%s,%s,%s,%s,NOW())""",
                    (cid, k, str(antes.get(k)) if antes.get(k) is not None else None, str(v), AUTOR))
    n += 1
cn.commit()
print(f'\nGRAVADO: {n} cargas atualizadas + log de auditoria em cada campo.')
cn.close()
