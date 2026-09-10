# -*- coding: utf-8 -*-
"""
Valida o payload de TODAS as viagens contra as regras da documentação da Verda:
campos mandatórios, tamanho máximo e formato. Serve de rede antes do primeiro
envio real — a API só devolve o motivo da rejeição via GetTransaction, então é
melhor pegar aqui.

Uso:  python -X utf8 _verda_valida.py [--exemplos N]
"""

import json
import random
import re
import sys
from collections import Counter, defaultdict

import _verda_demo as demo
import verda_payload as vp
from server import get_token

# Mandatórios por API (pgs. 23-29 para Fuel, 60-65 para Weight).
# VehicleTypeKey está como "Não" na Fuel (pg. 24), mas o e-mail do Ricardo diz
# que todo RoadOrchestration exige — tratamos como obrigatório nas duas.
RAIZ_COMUM = ['LocalDateTime', 'TransportationId', 'TransportationDate',
              'CountryVehicleKey', 'VehicleTypeKey', 'VehicleKey', 'VehicleModelYear',
              'VehicleUtilization', 'DistanceUnitKey', 'ItemsList', 'IsOwnOperation']
RAIZ = {'Fuel': RAIZ_COMUM + ['VolumeUnitKey', 'FuelTypeKey', 'FuelConsumption', 'ItemIndexUnitKey'],
        'Weight': RAIZ_COMUM + ['WeightUnitKey']}
ITEM_COMUM = ['WaypointOrder', 'WaypointDistance', 'DeliveryId', 'ShipperCountryKey',
              'ShipperKey', 'ItemId', 'IsFinalDestination']
ITEM = {'Fuel': ITEM_COMUM + ['ItemWeightIndex'], 'Weight': ITEM_COMUM + ['ItemWeight']}

# Tamanho máximo de texto (a doc repete 50 para quase tudo; 3 para os ISO Alpha-3)
TAMANHO = {'TransportationId': 50, 'VehicleKey': 50, 'VehicleTypeKey': 50, 'DriverId': 50,
           'RouteId': 50, 'AComment': 50, 'CountryVehicleKey': 3, 'DistanceUnitKey': 20,
           'VolumeUnitKey': 20, 'FuelTypeKey': 20, 'ItemIndexUnitKey': 20, 'WeightUnitKey': 20,
           'DeliveryId': 50, 'ItemId': 50, 'ShipperKey': 50, 'ShipperCountryKey': 3,
           'CounterpartKey': 50, 'CounterpartCountryKey': 3, 'ContractId': 50,
           'CarrierPartnerKey': 50, 'PreviousTransportationId': 50, 'ItemAComment': 50}

RE_DATA = re.compile(r'\d{4}-\d{2}-\d{2}$')
RE_DATAHORA = re.compile(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$')
RE_BOOL = re.compile(r'[01]$')


def _vazio(v):
    return v is None or (isinstance(v, str) and not v.strip())


def validar(api, payload):
    """Lista de problemas encontrados no payload (vazia = passou)."""
    p = []
    for campo in RAIZ[api]:
        if _vazio(payload.get(campo)):
            p.append('raiz obrigatória vazia: %s' % campo)
    if not payload.get('ItemsList'):
        p.append('ItemsList vazia')

    if not RE_DATAHORA.match(str(payload.get('LocalDateTime') or '')):
        p.append('LocalDateTime fora de AAAA-MM-DD HH:MM:SS')
    if not RE_DATA.match(str(payload.get('TransportationDate') or '')):
        p.append('TransportationDate fora de YYYY-MM-DD')
    ano = payload.get('VehicleModelYear')
    if ano is not None and not (1980 < int(ano) <= 2027):
        p.append('VehicleModelYear implausível: %s' % ano)
    for campo in ('IsOwnOperation', 'IsInbound', 'IsScopeOne'):
        v = payload.get(campo)
        if v not in ('', None) and not RE_BOOL.match(str(v)):
            p.append('%s não é 0/1: %r' % (campo, v))

    for campo, limite in TAMANHO.items():
        v = payload.get(campo)
        if isinstance(v, str) and len(v) > limite:
            p.append('%s excede %d caracteres (%d)' % (campo, limite, len(v)))

    ordens = set()
    for i, item in enumerate(payload['ItemsList'] or []):
        for campo in ITEM[api]:
            if _vazio(item.get(campo)):
                p.append('item[%d] obrigatório vazio: %s' % (i, campo))
        if not RE_BOOL.match(str(item.get('IsFinalDestination') or '')):
            p.append('item[%d] IsFinalDestination não é 0/1' % i)
        peso = item.get('ItemWeightIndex' if api == 'Fuel' else 'ItemWeight')
        if peso is not None and not (0 < float(peso) < 80000):
            p.append('item[%d] peso implausível: %s kg' % (i, peso))
        d = item.get('WaypointDistance')
        if d is not None and not (0 < float(d) < 6000):
            p.append('item[%d] WaypointDistance implausível: %s km' % (i, d))
        for campo in ('ShipperKey', 'CounterpartKey'):
            v = item.get(campo)
            if v and not re.fullmatch(r'[\d.]{2,3}\.\d{3}\.\d{3}[/-]\d{2,4}-?\d{0,2}', v):
                p.append('item[%d] %s com formato estranho: %s' % (i, campo, v))
        ordens.add(item.get('WaypointOrder'))
    if ordens and sorted(ordens) != list(range(len(ordens))):
        p.append('WaypointOrder não é sequência 0..n: %s' % sorted(ordens))

    # regra da doc: cada parada tem ≥1 entrega; cada entrega, 1 parada só
    por_entrega = defaultdict(set)
    for item in payload['ItemsList'] or []:
        por_entrega[item.get('DeliveryId')].add(item.get('WaypointOrder'))
    for entrega, paradas in por_entrega.items():
        if len(paradas) > 1:
            p.append('entrega %s em mais de uma parada: %s' % (entrega, sorted(paradas)))
    return p


def main():
    n_ex = int(sys.argv[sys.argv.index('--exemplos') + 1]) if '--exemplos' in sys.argv else 3
    dados = demo.carregar(get_token())
    viagens = [v for v in dados['viagens'] if dados['ctrc'].get((v['sigla'], v['numero']))]

    problemas, avisos, apis, ok = Counter(), Counter(), Counter(), []
    sem_ctrb = 0
    for viagem in viagens:
        v, itens, aud = demo.preparar(viagem, dados)
        if v is None or not itens:
            sem_ctrb += 1
            continue
        api, payload, avs = vp.montar(v, itens, dados['cadastro'], dados['observado'],
                                      demo.CNPJ_RIZZA)
        apis[api] += 1
        for a in dict.fromkeys(avs):
            avisos[re.sub(r'[\d.,]+', 'N', a)] += 1
        probs = validar(api, payload)
        for pr in dict.fromkeys(probs):
            problemas[re.sub(r'\[\d+\]', '[i]', re.sub(r'[\d.,]+ ?', 'N', pr))] += 1
        if not probs:
            ok.append((api, v['transportation_id'], payload))

    tot = len(viagens)
    print('viagens com CTe: %d | sem CTRB (nao sobem): %d' % (tot, sem_ctrb))
    print('Fuel: %d | Weight: %d' % (apis['Fuel'], apis['Weight']))
    print('APROVADAS sem nenhum problema: %d (%.1f%%)\n' % (len(ok), 100.0 * len(ok) / tot))

    print('=== PROBLEMAS (bloqueiam o envio) ===')
    if not problemas:
        print('   nenhum')
    for k, n in problemas.most_common():
        print('   %5d  %s' % (n, k))

    print('\n=== AVISOS (envia, mas registra) ===')
    for k, n in avisos.most_common():
        print('   %5d  %s' % (n, k))

    print('\n=== %d EXEMPLOS SORTEADOS ENTRE AS APROVADAS ===' % n_ex)
    for api, tid, payload in random.sample(ok, min(n_ex, len(ok))):
        itens = payload['ItemsList']
        print('\n--- %s | %s | %d item(ns) ---' % (api, tid, len(itens)))
        print(json.dumps({k: v for k, v in payload.items() if k != 'ItemsList'},
                         indent=2, ensure_ascii=False, default=str))
        print('  ItemsList[0]:', json.dumps(itens[0], ensure_ascii=False, default=str))


if __name__ == '__main__':
    main()
