# -*- coding: utf-8 -*-
"""Leitura da `verda_envios` para a aba Verda.

**A tela não chama a Verda.** Tudo sai do que gravamos ao enviar: o payload fica
em JSONB na `verda_envios`, e o CO2e é calculado aqui com o fator reconstruído.

Isso não é atalho — é o que torna a aba possível. No plano gratuito a Verda
guarda **só o consolidado mensal**, sem detalhe de viagem: para o dado por
viagem, esta tela é o único lugar onde ele existe. E o fator foi conferido
contra o relatório oficial deles em 597 viagens, com erro de 0,0008%.

O módulo recebe um cursor e não abre conexão — quem manda é a rota, no mesmo
padrão do `pgr`.
"""

import json
from collections import defaultdict


# Fator de emissão reconstruído da API `Fuel` (kg CO2e por litro de diesel).
#
# Não é escolha nossa: é o que a Verda aplica, medido por engenharia reversa do
# dashboard e confirmado três vezes de forma independente — (1) uma viagem
# isolada, ao milésimo; (2) o relatório Excel das 597 de agosto, erro de
# 0,0008%; (3) o `CI Distance` do relatório, que é exatamente (1/km_l) x este
# valor para cada faixa de consumo.
#
# É menor que a estequiometria do diesel fóssil (~2,6-2,7) porque a plataforma
# já desconta a fração renovável da mistura brasileira. Por isso o
# `RenewableShare` não é enviado — mandar contaria o biodiesel duas vezes.
FATOR_CO2E = 2.28429

# A descrição que a PRÓPRIA VERDA mostra em Atributos do cliente, palavra por
# palavra. Não traduzir, não apelidar: o código é a chave do payload e o texto é
# o da plataforma, então a tela, a conta da Verda e o JSON falam a mesma língua.
#
# Já tentei apelidar aqui ("truck", "bitrem/rodotrem") e confunde: `rigido_75` é
# faixa de PESO (7,5 a 17 t) e "truck" é configuração de eixos — coisas
# diferentes com o mesmo nome. Quem lê a tela precisa poder procurar o código na
# conta da Verda e achar.
#
# As duas grafias dos rígidos convivem de propósito: a acentuada é a chave real
# da conta (e a que o código passa a gerar), e a sem acento é o que ficou
# gravado no payload das viagens enviadas antes de 11/09/2026. Tirar a antiga
# faria a tela perder a descrição justamente das linhas históricas.
DESCRICAO_VERDA = {
    'articulado_35': 'Caminhão articulado - 3,5 a 33 ton',
    'articulado_330': 'Caminhão articulado - acima de 33',
    'rígido_35': 'Caminhão rígido - 3,5 a 7,5 ton',
    'rígido_75': 'Caminhão rígido - 7,5 a 17 ton',
    'rígido_170': 'Caminhão rígido - acima de 17 ton',
    'rigido_35': 'Caminhão rígido - 3,5 a 7,5 ton (grafia antiga)',
    'rigido_75': 'Caminhão rígido - 7,5 a 17 ton (grafia antiga)',
    'rigido_170': 'Caminhão rígido - acima de 17 ton (grafia antiga)',
}

# Uma viagem parada em `enviado` por mais que isto sem veredito é sintoma, não
# espera normal: o processamento da Verda leva 1 a 2 minutos. Foi assim que o
# bug do `TransactionKey` escondeu viagens presas para sempre.
HORAS_PRESA = 2


def _payload(valor):
    return json.loads(valor) if isinstance(valor, str) else (valor or {})


def _metricas(payload):
    """km, peso (t), litros e CO2e de uma viagem, a partir do payload enviado.

    **`WaypointDistance` é por PARADA, não por item.** Somar todos os itens
    infla a distância em 3x. Como a regra do CTRB deixou a viagem com uma parada
    só, basta o primeiro item — mas a leitura por `WaypointOrder` distinto fica
    aqui para não quebrar se voltar a haver multi-parada.
    """
    itens = payload.get('ItemsList') or []
    if not itens:
        return 0.0, 0.0, 0.0, 0.0

    por_parada = {}
    for it in itens:
        por_parada[it.get('WaypointOrder', 0)] = float(it.get('WaypointDistance') or 0)
    km = sum(por_parada.values())

    peso = sum(float(i.get('ItemWeightIndex') or i.get('ItemWeight') or 0) for i in itens)
    kml = float(payload.get('FuelConsumption') or 0)
    litros = (km / kml) if kml else 0.0
    return km, peso / 1000.0, litros, litros * FATOR_CO2E / 1000.0


def painel(cur, desde, ate, ambiente):
    """Tudo o que a aba mostra, numa consulta só."""
    cur.execute("""
        SELECT transportation_id, data_viagem, api, status, payload, avisos,
               transaction_id, mensagem, enviado_em, conferido_em, atualizado_em,
               EXTRACT(EPOCH FROM (NOW() - enviado_em)) / 3600 AS horas_desde_envio
          FROM verda_envios
         WHERE ambiente = %s AND data_viagem BETWEEN %s AND %s
         ORDER BY data_viagem, transportation_id
    """, (ambiente, desde, ate))
    linhas = cur.fetchall()

    viagens, bloqueadas, alertas = [], [], []
    por_dia = defaultdict(float)
    consumo = defaultdict(int)
    tipos = defaultdict(int)
    tot = {'km': 0.0, 't': 0.0, 'litros': 0.0, 'co2': 0.0, 'ctes': 0,
           'co2_esc1': 0.0, 'n_esc1': 0, 'n_inbound': 0}
    contagem = defaultdict(int)

    for r in linhas:
        (tid, data, api, status, payload_raw, avisos_raw, transaction_id,
         mensagem, enviado_em, conferido_em, atualizado_em, horas) = r
        p = _payload(payload_raw)
        avisos = _payload(avisos_raw) or []
        contagem[status] += 1

        if status == 'bloqueado':
            bloqueadas.append({
                'id': tid, 'data': str(data), 'placa': p.get('VehicleKey'),
                'rota': p.get('RouteId'), 'motivo': mensagem or 'sem motivo registrado',
            })
            continue

        # Os alertas destes dois saem ANTES da exclusão: a viagem não entra no
        # inventário, mas precisa continuar gritando.
        if status == 'rejected':
            alertas.append({'tipo': 'rejeitada', 'id': tid,
                            'texto': mensagem or 'rejeitada sem motivo informado'})
        if status == 'fora_escopo' and transaction_id:
            alertas.append({'tipo': 'expurgo', 'id': tid,
                            'texto': 'saiu do escopo e a transacao continua viva na Verda'})

        # Só conta o que está — ou vai estar — no inventário da Verda.
        # `rejected` a Verda recusou e `fora_escopo` nós retiramos: somar
        # qualquer um dos dois faz a tela declarar emissão que não existe lá.
        # Ficavam somados até 11/09/2026, quando as 21 rejeitadas do primeiro
        # lote de produção apareceram nos KPIs como se estivessem no inventário.
        # `pendente` continua contando de propósito: é o que faz a tela servir
        # de prévia do lote antes de mandar.
        if status in ('rejected', 'fora_escopo'):
            continue

        km, t, litros, co2 = _metricas(p)
        esc1 = str(p.get('IsScopeOne')) == '1'
        inbound = str(p.get('IsInbound')) == '1'
        n_ctes = len(p.get('ItemsList') or [])

        tot['km'] += km; tot['t'] += t; tot['litros'] += litros
        tot['co2'] += co2; tot['ctes'] += n_ctes
        if esc1:
            tot['co2_esc1'] += co2; tot['n_esc1'] += 1
        if inbound:
            tot['n_inbound'] += 1
        por_dia[str(data)] += co2
        kml = float(p.get('FuelConsumption') or 0)
        if kml:
            consumo[kml] += 1
        tipos[p.get('VehicleTypeKey') or '(sem tipo)'] += 1

        # Presa em `enviado` sem veredito: o processamento leva 1 a 2 minutos.
        if status == 'enviado' and horas and horas > HORAS_PRESA:
            alertas.append({'tipo': 'presa', 'id': tid,
                            'texto': 'enviada ha %.0f h e ainda sem veredito da Verda' % horas})

        viagens.append({
            'id': tid, 'data': str(data), 'api': api, 'status': status,
            'rota': p.get('RouteId'), 'placa': p.get('VehicleKey'),
            'ano': p.get('VehicleModelYear'), 'tipo': p.get('VehicleTypeKey'),
            'tipo_descricao': DESCRICAO_VERDA.get(p.get('VehicleTypeKey'), ''),
            'km': round(km, 1), 't': round(t, 2), 'kml': kml,
            'litros': round(litros, 1), 'co2': round(co2, 3),
            'esc1': esc1, 'inbound': inbound, 'ctes': n_ctes,
            'transaction_id': transaction_id, 'avisos': avisos,
            'ctes_lista': [i.get('DeliveryId') for i in (p.get('ItemsList') or [])],
        })

    # Placas que estao segurando viagem, agrupadas — e a lista que vai para o
    # cadastro resolver. E o que faz a aba ser ferramenta e nao espelho.
    por_placa = defaultdict(lambda: {'viagens': 0, 'motivo': '', 'rota': ''})
    for b in bloqueadas:
        g = por_placa[b['placa'] or '(sem placa)']
        g['viagens'] += 1
        g['motivo'] = g['motivo'] or b['motivo']
        g['rota'] = g['rota'] or b['rota']

    n = len(viagens)
    litros_km = (tot['km'] / tot['litros']) if tot['litros'] else 0
    t_km = sum(v['km'] * v['t'] for v in viagens)
    return {
        'ambiente': ambiente,
        'janela': {'desde': str(desde), 'ate': str(ate)},
        'kpis': {
            'viagens': n,
            'co2': round(tot['co2'], 2),
            'km': round(tot['km']),
            't': round(tot['t'], 1),
            'litros': round(tot['litros']),
            'ctes': tot['ctes'],
            'intensidade': round(tot['co2'] * 1e6 / t_km, 1) if t_km else 0,
            'kml_medio': round(litros_km, 2),
            'co2_esc1': round(tot['co2_esc1'], 2),
            'co2_esc3': round(tot['co2'] - tot['co2_esc1'], 2),
            'n_esc1': tot['n_esc1'],
            'n_esc3': n - tot['n_esc1'],
            'n_inbound': tot['n_inbound'],
            'n_outbound': n - tot['n_inbound'],
        },
        'status': dict(contagem),
        'por_dia': [{'data': d, 'co2': round(v, 3)} for d, v in sorted(por_dia.items())],
        'consumo': [{'kml': k, 'viagens': v} for k, v in sorted(consumo.items(), reverse=True)],
        'tipos': [{'tipo': k, 'descricao': DESCRICAO_VERDA.get(k, ''), 'viagens': v}
                  for k, v in sorted(tipos.items(), key=lambda x: -x[1])],
        'bloqueadas': [{'placa': k, **v} for k, v in
                       sorted(por_placa.items(), key=lambda x: -x[1]['viagens'])],
        'n_bloqueadas': len(bloqueadas),
        'alertas': alertas,
        'viagens': sorted(viagens, key=lambda v: -v['co2']),
    }


def ultima_rodada(cur, ambiente):
    """Quando o robô mexeu nesta tabela pela última vez."""
    cur.execute("""SELECT MAX(atualizado_em), MAX(enviado_em), COUNT(*)
                     FROM verda_envios WHERE ambiente = %s""", (ambiente,))
    r = cur.fetchone()
    return {'atualizado_em': r[0].isoformat() if r[0] else None,
            'enviado_em': r[1].isoformat() if r[1] else None,
            'linhas': r[2]}
