# -*- coding: utf-8 -*-
"""
Roda a classificação de `verda_veiculos` sobre as viagens reais e imprime:
  1. distribuição de VehicleTypeKey
  2. de onde veio a capacidade de cada viagem
  3. relatório de exceções (o que precisa de olho humano)

Uso:  python -X utf8 _verda_classifica.py [--csv saida.csv]
Fonte: dataset principal do Power BI (manifestos + veiculos_045).
"""

import sys
from collections import Counter, defaultdict

from server import get_token, execute_dax, clean_rows
from placas import mercosul
import verda_veiculos as vv

M = "'public manifestos'"
V = "'public veiculos_045'"


def _dax(token, query):
    res = execute_dax(token, query)
    return clean_rows(res.get('results', [{}])[0].get('tables', [{}])[0].get('rows', []))


def carregar(token):
    cadastro = {}
    for r in _dax(token, 'EVALUATE SELECTCOLUMNS(%s, "placa", %s[placa], "tipo", %s[tipo], '
                         '"capacidade", %s[capacidade], "modelo", %s[modelo])' % (V, V, V, V, V)):
        cadastro[mercosul(r.get('placa'))] = {
            'tipo': r.get('tipo'), 'capacidade': r.get('capacidade'), 'modelo': r.get('modelo')}

    viagens = _dax(token, 'EVALUATE SELECTCOLUMNS(%s, "manifesto", %s[numero_manifesto], '
                          '"placa_cavalo", %s[placa_cavalo], "placa_carreta", %s[placa_carreta], '
                          '"peso_total", %s[peso_total], "proprietario", %s[proprietario_cavalo])'
                          % (M, M, M, M, M, M))
    return cadastro, viagens


def main():
    token = get_token()
    cadastro, viagens = carregar(token)
    observado = vv.observado_por_placa(viagens)
    print('cadastro: %d veículos | viagens: %d | carretas com histórico: %d\n'
          % (len(cadastro), len(viagens), len(observado)))

    tipos, fontes, excecoes = Counter(), Counter(), defaultdict(list)
    linhas = []
    for v in viagens:
        key, det = vv.vehicle_type_key(v.get('placa_cavalo'), v.get('placa_carreta'),
                                       cadastro, observado, v.get('peso_total'))
        tipos[key or '(NÃO CLASSIFICADO)'] += 1
        fontes[det['fonte'] or '(sem fonte)'] += 1
        for a in det['alertas']:
            excecoes[a].append((v.get('manifesto'), v.get('placa_cavalo'), v.get('placa_carreta')))
        linhas.append((v.get('manifesto'), v.get('placa_cavalo'), v.get('placa_carreta'),
                       key, det['capacidade'], det['fonte'], det['tracao'],
                       '; '.join(det['alertas'])))

    total = len(viagens)
    print('=== VehicleTypeKey (base da faixa: %s) ===' % vv.BASE_FAIXA)
    for k, n in tipos.most_common():
        print('   %-22s %5d  (%5.1f%%)' % (k, n, 100.0 * n / total))

    print('\n=== quem definiu a capacidade ===')
    for k, n in fontes.most_common():
        print('   %-34s %5d  (%5.1f%%)' % (k, n, 100.0 * n / total))

    print('\n=== EXCEÇÕES — conferir na mão ===')
    if not excecoes:
        print('   nenhuma')
    for motivo, casos in sorted(excecoes.items(), key=lambda x: -len(x[1])):
        print('   %s  (%d)' % (motivo, len(casos)))
        for manifesto, cav, car in casos[:5]:
            print('       manifesto %-10s cavalo %-9s carreta %s' % (manifesto, cav, car or '—'))
        if len(casos) > 5:
            print('       ... e mais %d' % (len(casos) - 5))

    if '--csv' in sys.argv:
        import csv
        destino = sys.argv[sys.argv.index('--csv') + 1]
        with open(destino, 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.writer(fh, delimiter=';')
            w.writerow(['manifesto', 'placa_cavalo', 'placa_carreta', 'vehicle_type_key',
                        'capacidade_t', 'fonte', 'tracao', 'alertas'])
            w.writerows(linhas)
        print('\nCSV gravado em %s (%d linhas)' % (destino, len(linhas)))


if __name__ == '__main__':
    main()
