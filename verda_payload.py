# -*- coding: utf-8 -*-
"""
Montador de payload da Verda — transforma um manifesto do SSW no JSON das APIs
`Fuel` e `Weight` (domínio RoadOrchestration).

Qual API cada viagem usa depende de quem é o dono do cavalo:

    frota própria (RIZZA)      → Fuel    exige FuelConsumption (km/l do ValeCard)
                                          IsOwnOperation=1, IsScopeOne=1
    agregado / carreteiro      → Weight  não pede consumo (não existe pra terceiro)
                                          IsOwnOperation=0, CarrierPartnerKey=CNPJ do dono

Essa é a ordem de prioridade que a própria Verda indicou por e-mail ("os métodos
de cálculo prioritários são Fuel e Weight, nessa ordem") e casa com o recorte do
Protocolo GHG: o diesel que a Rizza queima é escopo 1, o frete que ela contrata
é escopo 3.

O `VehicleTypeKey` vem de `verda_veiculos`, que não confia no cadastro.

TODO quando a Verda responder:
  - VehicleUtilization: fixado em 1 (deduzido do exemplo oficial, onde 20,1 t de
    carga aparecem com utilization 1 — logo é dedicação do veículo, não taxa de
    ocupação). Confirmar com o Rafael.
  - As faixas de VehicleTypeKey são carga útil ou PBT (ver verda_veiculos).
"""

import hashlib
import os
import re
from datetime import datetime

from placas import mercosul
import verda_veiculos as vv


# ════════════════════════════════════════
# PARÂMETROS
# ════════════════════════════════════════

PAIS = 'BRA'                    # ISO Alpha-3, todos os CNPJs da operação
DISTANCE_UNIT = 'kmeter'
VOLUME_UNIT = 'liter'
WEIGHT_UNIT = 'kgram'           # manda kg direto: o SSW já dá em kg, sem conversão
ITEM_INDEX_UNIT = 'kgram'
FUEL_TYPE = 'diesel'

# Fração renovável do diesel brasileiro: a mistura de biodiesel é compulsória
# (ANP/CNPE) e vale igual para S10 e S500 — a diferença entre os dois é enxofre,
# que não entra em CO2e. Indexado pela DATA DA VIAGEM, não pelo ano do veículo.
# Ordem decrescente; vale a primeira faixa cuja data de início já passou.
MISTURA_BIODIESEL = [
    ('2025-08-01', 0.15),   # B15, em vigor
    ('2024-03-01', 0.14),   # B14
    ('2023-04-01', 0.12),   # B12
    ('0000-00-00', 0.10),   # anterior — piso conservador
]
# B16 estava previsto para março/2026 e não entrou; quando sair, é uma linha aqui.

# DESLIGADO até o Rafael responder: se o fator do `diesel` deles já embute a
# mistura brasileira, mandar isto conta o biodiesel duas vezes. O campo é
# opcional (pg. 25), então não enviar é o lado seguro. Ligue quando confirmarem.
ENVIAR_RENEWABLE_SHARE = False

# Dedicação do veículo ao transportador. Carga fechada = veículo inteiro = 1.
VEHICLE_UTILIZATION = 1

# ── VehicleTypeKey em homologação ──
# A conta de teste da Verda só tem UM tipo cadastrado. O Rafael (WhatsApp, 31/08):
# "Nos testes, no campo VehicleTypeKey, utilize o valor 'veiculo_teste'. Em produção
# vc irá utilizar os tipos de veículos que vierem a ser cadastrados por vcs."
#
# Então em teste o campo é carimbado, mas a classificação REAL continua sendo
# calculada e vai para os avisos — ela é o miolo do projeto (é a decisão de PBT
# × carga útil) e precisa seguir auditável na `verda_envios`.
#
# ATENÇÃO ao ligar produção: os códigos que valem lá são os que a Rizza cadastrar
# na plataforma. Enquanto esse cadastro não existir, `articulado_330` e `rigido_170`
# são nomes nossos, tirados do DEFRA — podem não bater com os da conta.
VEHICLE_TYPE_KEY_FIXO = os.getenv('VERDA_VEHICLE_TYPE_KEY', '').strip()

# ── Exposição de terceiros ──
# Nenhum dos dois é exigido pela Verda (CarrierPartnerKey: pgs. 29/65 "Não";
# DriverId: pgs. 24/61 "Não"), e o exemplo oficial usa "queira identificar".
# 52% das viagens são de carreteiro autônomo — identificar o parceiro significa
# mandar CPF de pessoa física para fora. O que o inventário precisa de verdade
# é IsOwnOperation + IsScopeOne, que separam escopo 1 de escopo 3 sem nomear
# ninguém. Terceiro vai pela Weight, que não tem consumo, então a Verda também
# não conseguiria calcular eficiência por parceiro mesmo recebendo o documento.
IDENTIFICAR_PARCEIRO = False

# Mesma regra para o destinatário (CounterpartKey/CounterpartCountryKey, "Não"
# nas pgs. 27 e 63): é o cliente do embarcador, dado de terceiro que não é nosso.
# Se a Nestlé quiser emissão quebrada por destino, ela pede e a gente liga.
# Não afeta o IsInbound, que é calculado antes, a partir do CTe.
IDENTIFICAR_CONTRAPARTE = False

# Motorista: 'nenhum' omite; 'pseudonimo' manda um código estável derivado do
# CPF (permite agrupar por motorista na plataforma sem expor o documento);
# 'cpf' manda o CPF cru — só se a Verda exigir e houver base legal.
MOTORISTA = 'pseudonimo'
PSEUDONIMO_SAL = 'rizza-verda'   # troque para rotacionar os códigos

# Dono que caracteriza frota própria (prefixo, para pegar as variações do cadastro).
PROPRIETARIO_FROTA = 'RIZZA TRANSPORTES'

SEM_DADO = ''

# `RouteId` aceita 50 caracteres (pgs. 29/65).
MAX_ROUTE_ID = 50


# ════════════════════════════════════════
# FORMATADORES
# ════════════════════════════════════════

def _dv_cpf(d):
    if len(d) != 11 or len(set(d)) == 1:
        return False
    for n in (9, 10):
        s = sum(int(d[i]) * ((n + 1) - i) for i in range(n))
        if int(d[n]) != (0 if (s * 10 % 11) == 10 else s * 10 % 11):
            return False
    return True


def _dv_cnpj(d):
    if len(d) != 14 or len(set(d)) == 1:
        return False
    for n, pesos in ((12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
                     (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2])):
        s = sum(int(d[i]) * pesos[i] for i in range(n))
        r = s % 11
        if int(d[n]) != (0 if r < 2 else 11 - r):
            return False
    return True


def documento(valor):
    """Identificação fiscal formatada — CNPJ ou CPF.

    O `veiculos_045` guarda os dois no mesmo campo, sempre com 14 dígitos: o CPF
    do carreteiro autônomo vem com zeros à esquerda. E autônomo não é exceção —
    é 52% das viagens. Mascarar CPF como CNPJ produziria um documento inexistente.

    Decide pelo dígito verificador, não pelo tamanho, porque o zero-padding
    destrói a pista do tamanho.

    ATENÇÃO: a Verda só documenta o formato empresarial para o Brasil
    (99.999.999/9999-00). O formato de pessoa física está sendo enviado como
    999.999.999-99 por analogia — confirmar com o Rafael.
    """
    d = re.sub(r'\D', '', str(valor or ''))
    if _dv_cnpj(d):
        return '%s.%s.%s/%s-%s' % (d[:2], d[2:5], d[5:8], d[8:12], d[12:])
    cpf = d[3:] if len(d) == 14 and d.startswith('000') else d
    if _dv_cpf(cpf):
        return '%s.%s.%s-%s' % (cpf[:3], cpf[3:6], cpf[6:9], cpf[9:])
    return SEM_DADO


def driver_id(cpf):
    """`DriverId` conforme a política de MOTORISTA.

    O pseudônimo é estável (mesmo motorista → mesmo código sempre), o que
    preserva a análise por motorista na plataforma, mas não é reversível para
    o CPF sem o sal.
    """
    d = re.sub(r'\D', '', str(cpf or ''))
    if not d or MOTORISTA == 'nenhum':
        return SEM_DADO
    if MOTORISTA == 'cpf':
        return d
    return 'M' + hashlib.sha256((PSEUDONIMO_SAL + d).encode()).hexdigest()[:12].upper()


def e_pessoa_fisica(valor):
    """True se o documento é CPF — usado para sinalizar no relatório."""
    d = re.sub(r'\D', '', str(valor or ''))
    return _dv_cpf(d[3:] if len(d) == 14 and d.startswith('000') else d)


# compatibilidade: o nome antigo continua funcionando
cnpj = documento


def ano_quatro_digitos(ano):
    """'18' → 2018. O SSW guarda o ano do veículo com dois dígitos."""
    d = re.sub(r'\D', '', str(ano or ''))
    if len(d) == 4:
        return int(d)
    if len(d) == 2 and d != '00':
        n = int(d)
        return 2000 + n if n <= datetime.now().year % 100 else 1900 + n
    return None


def data(valor):
    """Data em YYYY-MM-DD (formato `TransportationDate`)."""
    if isinstance(valor, datetime):
        return valor.strftime('%Y-%m-%d')
    s = str(valor or '')[:10]
    return s if re.fullmatch(r'\d{4}-\d{2}-\d{2}', s) else SEM_DADO


def agora():
    """`LocalDateTime` da chamada."""
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def booleano(valor):
    """Booleano da Verda: '1' / '0' como texto."""
    return '1' if valor else '0'


# ════════════════════════════════════════
# REGRAS DE NEGÓCIO
# ════════════════════════════════════════

def is_inbound(cnpj_remetente, cnpj_destinatario, cnpj_emissor):
    """Regra literal da documentação (nota de rodapé das pgs. 28/43/64):

    SE CNPJ do embarcador = CNPJ do destinatário E CNPJ do emissor ≠ embarcador
    ENTÃO 1 SENÃO 0. É transferência entre unidades do próprio embarcador.
    """
    r = re.sub(r'\D', '', str(cnpj_remetente or ''))
    d = re.sub(r'\D', '', str(cnpj_destinatario or ''))
    e = re.sub(r'\D', '', str(cnpj_emissor or ''))
    return booleano(r and r == d and e != r)


def renovavel(data_viagem):
    """Fração de biodiesel vigente na data da viagem, ou None se desligado."""
    if not ENVIAR_RENEWABLE_SHARE:
        return None
    d = data(data_viagem) or '9999-99-99'
    for inicio, fracao in MISTURA_BIODIESEL:
        if d >= inicio:
            return fracao
    return None


# ════════════════════════════════════════
# MONTAGEM
# ════════════════════════════════════════

def montar(viagem, itens, cadastro, observado=None, cnpj_emissor=None):
    """Monta o payload de uma viagem.

    viagem    dict de `_verda_demo.preparar`: transportation_id, data_emissao,
              placa_cavalo, placa_carreta, cpf_motorista, peso_total,
              **km_trecho** (distância do CTRB), **rota**, **frota_propria**
    itens     lista de dicts, um por CTe: delivery_id, item_id, cnpj_remetente,
              cnpj_destinatario, peso_kg, contrato
    cadastro  {placa: {'tipo','capacidade','modelo','ano','cnpj'}}

    **Uma parada só.** A Auditoria Receita trata a viagem como origem → destino
    direto, e a distância é a do trecho que aquele veículo rodou. Todos os itens
    ficam no waypoint 0 — o que a doc permite ("cada parada tem ao menos uma
    entrega; cada entrega, uma parada só").

    Retorna (nome_da_api, payload, avisos).
    """
    avisos = []
    cav = mercosul(viagem.get('placa_cavalo') or '')
    reg = cadastro.get(cav) or {}
    propria = bool(viagem.get('frota_propria'))
    km = float(viagem.get('km_trecho') or 0)

    tipo_veiculo, det = vv.vehicle_type_key(
        viagem.get('placa_cavalo'), viagem.get('placa_carreta'),
        cadastro, observado, viagem.get('peso_total'))
    avisos.extend(det.get('alertas') or [])
    if not tipo_veiculo:
        avisos.append('sem VehicleTypeKey — viagem não pode ser enviada')
    elif VEHICLE_TYPE_KEY_FIXO:
        avisos.append('VehicleTypeKey carimbado como %r (classificação real: %s)'
                      % (VEHICLE_TYPE_KEY_FIXO, tipo_veiculo))
        tipo_veiculo = VEHICLE_TYPE_KEY_FIXO

    kml, fonte_kml = vv.consumo(cav, cadastro)
    usa_fuel = kml is not None
    if not usa_fuel:
        avisos.append('sem consumo — caiu para Weight (%s)' % fonte_kml)

    ano = ano_quatro_digitos(reg.get('ano'))
    if not ano:
        avisos.append('veículo %s sem ano no cadastro' % cav)

    lista = []
    for it in itens:
        item = {
            'WaypointOrder': 0,
            'WaypointDistance': km,
            'DeliveryServiceKey': it.get('servico') or SEM_DADO,
            'DeliveryId': it.get('delivery_id') or SEM_DADO,
            'PreviousTransportationId': SEM_DADO,
            'ShipperCountryKey': PAIS,
            'ShipperKey': cnpj(it.get('cnpj_embarcador') or it.get('cnpj_remetente')),
            'CounterpartCountryKey': PAIS if IDENTIFICAR_CONTRAPARTE else SEM_DADO,
            'CounterpartKey': (documento(it.get('cnpj_destinatario'))
                               if IDENTIFICAR_CONTRAPARTE else SEM_DADO),
            'ItemId': it.get('item_id') or it.get('delivery_id') or SEM_DADO,
            'ContractId': it.get('contrato') or SEM_DADO,
            'IsFinalDestination': '1',
            'ItemAComment': SEM_DADO,
        }
        item['ItemWeightIndex' if usa_fuel else 'ItemWeight'] = it.get('peso_kg')
        if not item['ShipperKey']:
            avisos.append('CTe %s sem CNPJ de remetente válido' % item['DeliveryId'])
        lista.append(item)

    payload = {
        'LocalDateTime': agora(),
        'TransportationId': viagem.get('transportation_id'),
        'TransportationDate': data(viagem.get('data_emissao')),
        'CountryVehicleKey': PAIS,
        'VehicleTypeKey': tipo_veiculo or SEM_DADO,
        'VehicleKey': cav,
        'VehicleModelYear': ano,
        'DriverId': driver_id(viagem.get('cpf_motorista')),
        'VehicleUtilization': VEHICLE_UTILIZATION,
        'DistanceUnitKey': DISTANCE_UNIT,
    }

    if usa_fuel:
        payload.update({
            'VolumeUnitKey': VOLUME_UNIT,
            'FuelTypeKey': FUEL_TYPE,
            'FuelConsumption': round(kml, 2),
            'RenewableShare': renovavel(viagem.get('data_emissao')),
            'ItemIndexUnitKey': ITEM_INDEX_UNIT,
        })
    else:
        payload['WeightUnitKey'] = WEIGHT_UNIT

    payload['ItemsList'] = lista

    primeiro = itens[0] if itens else {}
    payload.update({
        # a regra da doc compara o ShipperKey com o destinatario. Como o
        # ShipperKey agora e o PAGADOR, isto passa a marcar corretamente o frete
        # FOB (quem paga e quem recebe = transporte de entrada para ele).
        'IsInbound': is_inbound(primeiro.get('cnpj_embarcador') or primeiro.get('cnpj_remetente'),
                                primeiro.get('cnpj_destinatario'), cnpj_emissor),
        'IsOwnOperation': booleano(propria),
        'CarrierPartnerCountryKey': PAIS if (not propria and IDENTIFICAR_PARCEIRO) else SEM_DADO,
        'CarrierPartnerKey': (documento(reg.get('cnpj'))
                              if (not propria and IDENTIFICAR_PARCEIRO) else SEM_DADO),
        'AgentCountryKey': SEM_DADO,
        'AgentKey': SEM_DADO,
        'IsScopeOne': booleano(propria),
        'RouteId': str(viagem.get('rota') or '')[:MAX_ROUTE_ID] or SEM_DADO,
        'AComment': SEM_DADO,
    })

    if not propria and IDENTIFICAR_PARCEIRO and not payload['CarrierPartnerKey']:
        avisos.append('terceiro sem documento do proprietário no cadastro (CarrierPartnerKey vazio)')

    return ('Fuel' if usa_fuel else 'Weight'), payload, avisos
