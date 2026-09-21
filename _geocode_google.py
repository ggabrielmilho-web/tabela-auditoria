# -*- coding: utf-8 -*-
"""Passo 2 do teste: o endereço do cadastro cai onde o caminhão parou?

Lê o CSV do `_pontos_provados_local.py` (extraído de produção, só leitura) e mede o endereço
em texto contra o ponto PROVADO pelo GPS. Dois modos, porque a chave da Rizza hoje só tem
Distance Matrix habilitado — a Geocoding API devolve REQUEST_DENIED nela (21/09/2026):

  --modo distancia  (default, funciona com a chave de hoje)
      Distance Matrix com a ORIGEM em texto e o DESTINO na coordenada provada. A resposta dá
      a distância RODOVIÁRIA entre o que o Google entendeu do endereço e o ponto real, mais
      o `origin_addresses`, que é o endereço normalizado — ou seja, dá para ver o que ele
      entendeu. Mede também o CENTROIDE contra o mesmo ponto, pela MESMA régua: comparar
      rota do Google com linha reta do haversine seria comparar dois números diferentes.
      Limite honesto: distância rodoviária INFLA no urbano (Av. Paulista 1000 -> 1578 deu
      2,5 km de rota para ~600 m de linha reta). Serve para separar "é o mesmo lugar" de
      "caiu na cidade errada", não para medir metros.

  --modo geocode    (quando a Geocoding API for habilitada na chave)
      Aí vem coordenada de verdade e, melhor ainda, o `location_type`: ROOFTOP /
      RANGE_INTERPOLATED / GEOMETRIC_CENTER / APPROXIMATE. A pergunta que esse modo responde
      e o outro não: **o Google denuncia sozinho os casos ruins?** Se APPROXIMATE cobrir os
      erros grandes, o geocodificador é usável com filtro; se um ROOFTOP errar 30 km, o
      rótulo não serve e a âncora tem de sair do GPS.

A CHAVE nunca entra neste arquivo nem no repositório: é lida em tempo de execução do script
da Rizza que já a usa (`--chave-de`) e nunca é impressa.

    python -X utf8 _geocode_google.py provados.csv
    python -X utf8 _geocode_google.py provados.csv --modo geocode
"""
import os
import re
import sys
import csv
import json
import time
import argparse
import urllib.parse
import urllib.request

_AQUI = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
sys.path.insert(0, _AQUI)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import geocoding

ap = argparse.ArgumentParser()
ap.add_argument('csv')
ap.add_argument('--modo', default='distancia', choices=('distancia', 'geocode'))
ap.add_argument('--chave-de', default='../Rizza/preencher_km_google.py',
                help='arquivo de onde ler API_KEY (nunca é impressa)')
ap.add_argument('--pausa', type=float, default=0.2)
A = ap.parse_args()


def _chave(caminho):
    txt = open(caminho, encoding='utf-8', errors='replace').read()
    m = re.search(r'API_KEY\s*=\s*["\']([^"\']+)["\']', txt)
    if not m:
        print(f'não achei API_KEY em {caminho}')
        sys.exit(1)
    return m.group(1)


KEY = _chave(A.chave_de)
print(f'chave lida de {A.chave_de} ({len(KEY)} caracteres) — não será exibida')
print(f'modo: {A.modo}\n')


def _get(url, params):
    params['key'] = KEY
    with urllib.request.urlopen(url + '?' + urllib.parse.urlencode(params), timeout=30) as r:
        return json.loads(r.read().decode('utf-8'))


def por_rota(origem, lat, lng):
    """(km rodoviários até o ponto provado, o que o Google entendeu da origem)."""
    d = _get('https://maps.googleapis.com/maps/api/distancematrix/json',
             {'origins': origem, 'destinations': f'{lat},{lng}', 'units': 'metric'})
    if d.get('status') != 'OK':
        return None, f"{d.get('status')} {d.get('error_message') or ''}"
    e = d['rows'][0]['elements'][0]
    if e.get('status') != 'OK':
        return None, e.get('status')
    entendeu = (d.get('origin_addresses') or [''])[0]
    return e['distance']['value'] / 1000.0, entendeu


def por_geocode(endereco, lat, lng):
    d = _get('https://maps.googleapis.com/maps/api/geocode/json',
             {'address': endereco, 'region': 'br'})
    if d.get('status') != 'OK' or not d.get('results'):
        return None, f"{d.get('status')} {d.get('error_message') or ''}", None
    r0 = d['results'][0]
    loc = r0['geometry']['location']
    km = geocoding.km_entre(loc['lat'], loc['lng'], float(lat), float(lng))
    rot = r0['geometry'].get('location_type') + (' ~parcial' if r0.get('partial_match') else '')
    return km, r0.get('formatted_address'), rot


linhas = list(csv.DictReader(open(A.csv, encoding='utf-8-sig'), delimiter=';'))
print(f'{len(linhas)} locais no CSV')
res = []
for r in linhas:
    lat, lng = r['lat_provado'], r['lng_provado']
    end = ', '.join(x for x in (r.get('endereco'), r.get('bairro'),
                                f"{r.get('cidade')} - {r.get('uf')}", r.get('cep')) if x)
    if A.modo == 'distancia':
        d_end, entendeu = por_rota(end, lat, lng)
        time.sleep(A.pausa)
        d_cent, _ = por_rota(f"{r['lat_centroide']},{r['lng_centroide']}", lat, lng) \
            if r.get('lat_centroide') else (None, None)
        time.sleep(A.pausa)
        rotulo = None
    else:
        d_end, entendeu, rotulo = por_geocode(end, lat, lng)
        time.sleep(A.pausa)
        d_cent = float(r['km_do_centroide']) if r.get('km_do_centroide') else None
    res.append((r, end, d_end, d_cent, entendeu, rotulo))

unidade = 'km rota' if A.modo == 'distancia' else 'km reta'
print(f'\n{"nome":<28} {"lado":<8} {"n":>2} {"disp":>5} {"CENTROIDE":>10} {"ENDEREÇO":>9}'
      f'   {"rótulo" if A.modo == "geocode" else "o que o Google entendeu"}')
print(f'{"":<28} {"":<8} {"":>2} {"km":>5} {unidade:>10} {unidade:>9}')
for r, end, d_end, d_cent, entendeu, rotulo in res:
    print(f"{(r.get('nome') or '')[:28]:<28} {r['lado']:<8} {r['n_cargas']:>2} "
          f"{float(r['dispersao_km']):>5.1f} "
          f"{(f'{d_cent:.1f}' if d_cent is not None else '—'):>10} "
          f"{(f'{d_end:.1f}' if d_end is not None else '—'):>9}   "
          f"{(rotulo if rotulo else (entendeu or ''))[:60]}")

print('\n--- detalhe ---')
for r, end, d_end, d_cent, entendeu, rotulo in res:
    print(f"\n{r['cnpj']} · {r['lado']} · {r.get('nome')}")
    print(f"   cadastro : {end}")
    print(f"   provado  : {r['lat_provado']},{r['lng_provado']} · {r['n_cargas']} carga(s), "
          f"dispersão {r['dispersao_km']} km, {r['horas_parado']} h parado ({r['cargas']})")
    print(f"   centroide: {r['km_do_centroide']} km em linha reta"
          + (f" · {d_cent:.1f} km de rota" if d_cent is not None else ''))
    if d_end is None:
        print(f"   google   : FALHOU — {entendeu}")
    else:
        print(f"   google   : {d_end:.1f} {unidade}" + (f" [{rotulo}]" if rotulo else ''))
        print(f"              entendeu como: {entendeu}")

ok = [(r, d_end, d_cent, rotulo) for r, _e, d_end, d_cent, _en, rotulo in res if d_end is not None]
if ok:
    print(f'\n--- resumo ({len(ok)} de {len(res)} responderam) ---')
    for lim in (1, 5, 20):
        n = sum(1 for _r, d, _c, _t in ok if d <= lim)
        print(f'   endereço dentro de {lim:>2} {unidade} do ponto provado: {n}/{len(ok)}')
    comp = [(d, c) for _r, d, c, _t in ok if c is not None]
    melhor = sum(1 for d, c in comp if d < c)
    print(f'   endereço MELHOR que o centroide (mesma régua): {melhor}/{len(comp)}')
    if any(t for _r, _d, _c, t in ok):
        por_tipo = {}
        for _r, d, _c, t in ok:
            por_tipo.setdefault(t, []).append(d)
        print('   erro por location_type — o rótulo denuncia o caso ruim?')
        for t, ds in sorted(por_tipo.items(), key=lambda x: -len(x[1])):
            ds = sorted(ds)
            print(f'      {t:<24} n={len(ds):<3} mediana {ds[len(ds)//2]:>7.1f} · pior {ds[-1]:>7.1f}')
