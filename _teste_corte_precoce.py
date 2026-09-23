# -*- coding: utf-8 -*-
"""GATE do item 5 — raio de chegada proporcional a perna, e o KPI final vazio (23/09/2026).

Duas correcoes, as duas SO de leitura, as duas medidas pela ROTA REAL:

  5a  `_corta_chegada` varre a janela INTEIRA procurando a chegada, e a janela abre 12 h
      antes do carregamento. Numa perna curta entre cidades vizinhas o raio fixo de 20 km
      cobre o PROPRIO PATIO da origem: a "chegada" cai antes de a viagem comecar e o desenho
      morre ali. C-2026-001085 (Aparecida de Goiania -> Goiania, 19 km) ficava com 3 pontos,
      e eram da viagem ANTERIOR — 7,8 km publicados numa viagem de 49.

      A 1a tentativa foi copiar a guarda do `_consolidar_kpi` do worker (nao cortar quando
      origem e destino estao a menos de 2 raios). O gate reprovou: sem corte o trajeto segue
      pela viagem SEGUINTE da placa, e a C-2026-000559, de 24 km de rota, passava a desenhar
      851 km. Trocava um erro por outro.

      O que ficou: raio = min(20 km, d_od / 2). O patio da origem fica sempre fora do raio do
      destino e o corte continua existindo. Aplicado nos DOIS leitores (endpoint e worker),
      porque uma regua em cada arquivo e como eles divergem (§20.6).

  5b  o ramo `len(rows) < 2` do `_consolidar_kpi` grava a linha do KPI com
      consolidado_final=TRUE e todas as metricas NULAS. O endpoint preferia esse vazio ao
      calculo ao vivo, e o `(rk[0] or 0)` virava "0,0 km" — ausencia virando afirmacao, o
      oposto do §12.13.

COMO O "ANTES" E RECONSTRUIDO, sem trocar de versao: o endpoint agora corta com o raio
proporcional; aplicar `_indice_chegada_destino` no resultado com o raio CHEIO reproduz o que
o codigo antigo fazia, com as mesmas funcoes e os mesmos dados.

A REGRESSAO e verificada nas cargas NAO afetadas (d_od > 40 km): ali min(20, d_od/2) = 20 e
nada muda por construcao. A invariante que prova o corte e `o ultimo ponto do trajeto esta
dentro do raio do destino` — as duas regras do `_indice_chegada_destino` so devolvem indice
de ponto dentro do raio. Sem corte o trajeto seguiria ate o fim da janela e terminaria na
viagem seguinte, longe dali.

  (A 1a versao deste gate reaplicava `_indice_chegada_destino` no trajeto JA cortado e exigia
  que caisse no ultimo ponto. A funcao NAO e idempotente: a regra 1 pede parada de 60 min
  DENTRO da serie, e cortar no inicio da parada apaga a evidencia dela — a regra 2 entao
  devolve a maior aproximacao, que e anterior. Oito cargas boas foram reprovadas por esse
  defeito do proprio teste.)

    START_WORKER=false EMBARQUES_AUTO=false PGR_SYNC_CADASTRO=false
    DB_NAME=rizza_lab_0923 python -X utf8 _teste_corte_precoce.py
"""
import os
import sys

_AQUI = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
sys.path.insert(0, _AQUI)
os.chdir(_AQUI)
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from dotenv import load_dotenv
load_dotenv('.env')
import psycopg2
import server
import geocoding

RAIO = server.RAIO_CHEGADA_DESTINO_KM

cn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                      user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = cn.cursor()
print(f'banco: {os.getenv("DB_NAME")} · raio cheio {RAIO:.0f} km · usado = min({RAIO:.0f}, d_od/2)\n')

cur.execute("""
    SELECT c.id, c.numero, COALESCE(c.viagem_vazia,FALSE), c.origem_latitude, c.origem_longitude,
           d.latitude, d.longitude, c.distancia_planejada_km
      FROM embarques_cargas c
      JOIN embarques_cargas_destinos d ON d.carga_id = c.id
                                      AND d.ordem = (SELECT max(ordem) FROM embarques_cargas_destinos
                                                      WHERE carga_id = c.id)
     WHERE c.status = 'Entregue' AND c.data_carregamento >= CURRENT_DATE - 30
       AND c.origem_latitude IS NOT NULL AND d.latitude IS NOT NULL
     ORDER BY c.data_carregamento
""")
linhas = cur.fetchall()
DEST = {x[0]: (float(x[5]), float(x[6])) for x in linhas}

afetadas, normais = [], []
for cid, num, vazia, ola, oln, dla, dln, kmplan in linhas:
    dod = geocoding.km_entre(float(ola), float(oln), float(dla), float(dln))
    reg = (cid, num, vazia, dod, float(kmplan or 0))
    (afetadas if (dod is not None and dod <= RAIO * 2) else normais).append(reg)

print(f'{len(linhas)} cargas Entregue com coordenadas · {len(afetadas)} com raio encolhido '
      f'· {len(normais)} inalteradas por construcao')

app = server.app
c = app.test_client()
with c.session_transaction() as s:
    s['user_id'] = 1
    s['nome'] = 'gate'
    s['role'] = 'admin'
    s['tipos_permitidos'] = []
    s['paginas_permitidas'] = []


def buscar(cid):
    r = c.get(f'/api/rastreamento/cargas/{cid}/trajeto')
    return r.get_json() if r.status_code == 200 else None


def traj_principal(d):
    tipo = (d.get('rastreado_via') or {}).get('tipo')
    return (d.get('trajeto') or {}).get(tipo) or [] if tipo else []


def km_de(traj):
    return (server._kpi_ao_vivo(traj) or {}).get('distancia_km')


falhas = []


def checa(nome, cond, detalhe=''):
    print(('   OK    ' if cond else '   FALHA ') + nome + (f'  [{detalhe}]' if detalhe else ''))
    if not cond:
        falhas.append(nome)


print('\n── 5a · CARGAS COM RAIO ENCOLHIDO')
print(f'{"carga":<16}{"d_od":>6}{"km_plan":>9}{"pts_antes":>11}{"pts_depois":>12}'
      f'{"km_antes":>10}{"km_depois":>11}{"fim_a_dest":>12}')
ganhos = melhor = pior = 0
for cid, num, vazia, dod, kmplan in afetadas:
    d = buscar(cid)
    if not d:
        continue
    tj = traj_principal(d)
    dla, dln = DEST[cid]
    if not tj:
        print(f'{num:<16}{dod:>6.0f}{kmplan:>9.0f}{"—":>11}{"—":>12}{"—":>10}{"—":>11}{"—":>12}')
        continue
    idx = server._indice_chegada_destino(tj, dla, dln, raio_km=RAIO)   # o corte ANTIGO
    tj_antes = tj[:idx + 1] if idx is not None else tj
    km_antes, km_depois = km_de(tj_antes), km_de(tj)
    fim = geocoding.km_entre(tj[-1]['lat'], tj[-1]['lng'], dla, dln)
    if len(tj_antes) < len(tj):
        ganhos += 1
    if kmplan > 0 and km_antes is not None and km_depois is not None:
        if abs(km_depois - kmplan) < abs(km_antes - kmplan):
            melhor += 1
        elif abs(km_depois - kmplan) > abs(km_antes - kmplan):
            pior += 1
    print(f'{num:<16}{dod:>6.0f}{kmplan:>9.0f}{len(tj_antes):>11}{len(tj):>12}'
          f'{(f"{km_antes:.1f}" if km_antes is not None else "—"):>10}'
          f'{(f"{km_depois:.1f}" if km_depois is not None else "—"):>11}'
          f'{(f"{fim:.0f}" if fim is not None else "—"):>12}')

checa('a correcao devolveu trajeto', ganhos > 0, f'{ganhos} cargas')
checa('o km desenhado ficou mais perto da rota planejada', melhor > pior,
      f'{melhor} melhoraram · {pior} pioraram')

print('\n── REGRESSAO · nao afetadas: o corte na chegada tem de continuar acontecendo')
ainda_corta = sem_ponto = nunca_no_raio = fora = 0
exemplos = []
for cid, num, vazia, dod, kmplan in normais:
    d = buscar(cid)
    if not d:
        continue
    tj = traj_principal(d)
    if not tj:
        sem_ponto += 1
        continue
    dla, dln = DEST[cid]
    ult = geocoding.km_entre(tj[-1]['lat'], tj[-1]['lng'], dla, dln)
    perto = any((geocoding.km_entre(p['lat'], p['lng'], dla, dln) or 9e9) <= RAIO for p in tj)
    if not perto:
        nunca_no_raio += 1
    elif ult is not None and ult <= RAIO:
        ainda_corta += 1
    else:
        fora += 1
        exemplos.append(f'{num}: ultimo a {ult:.0f} km do destino')

checa('nenhuma nao-afetada perdeu o corte', fora == 0,
      f'{ainda_corta} terminam no destino · {nunca_no_raio} nunca entraram no raio · '
      f'{sem_ponto} sem ponto · {fora} passaram do destino'
      + (' · ' + '; '.join(exemplos[:3]) if exemplos else ''))

print('\n── 5b · KPI final com metricas NULAS')
cur.execute("""SELECT k.carga_id, c.numero FROM embarques_cargas_rastreio_kpi k
                 JOIN embarques_cargas c ON c.id = k.carga_id
                WHERE k.consolidado_final AND k.distancia_metros IS NULL""")
vazios = cur.fetchall()
zero_fabricado = com_trajeto = 0
for cid, num in vazios:
    d = buscar(cid)
    if not d:
        continue
    kpi = d.get('kpi') or {}
    tj = traj_principal(d)
    km_vivo = km_de(tj)
    if tj:
        com_trajeto += 1
        print(f'   {num}: pontos={len(tj)} · kpi.distancia_km={kpi.get("distancia_km")} '
              f'· ao vivo={km_vivo} · tempo_mov={kpi.get("tempo_movimento_seg")}')
        if kpi.get('distancia_km') in (0, 0.0) and (km_vivo or 0) > 1:
            zero_fabricado += 1
checa('KPI vazio nao publica mais 0,0 km com trajeto disponivel', zero_fabricado == 0,
      f'{len(vazios)} linhas vazias · {com_trajeto} com trajeto · {zero_fabricado} com zero fabricado')

print('\n' + ('TODOS OS TESTES PASSARAM' if not falhas else f'FALHAS: {falhas}'))
cn.rollback()
cn.close()
sys.exit(1 if falhas else 0)
