# -*- coding: utf-8 -*-
"""Testa a IMPLEMENTACAO das regras novas de fechamento contra o banco local.

A simulacao (`_simular_regras_fechamento.py`) mede a regra no papel. Aqui a pergunta e
outra: o CODIGO que foi escrito faz o que a simulacao previu? Cada teste chama a funcao
de verdade, com dados de verdade, e compara com o caso conhecido.

Nao escreve nada: usa transacao com ROLLBACK no fim.

    python -X utf8 _testar_regras_fechamento.py
"""
import os
import sys

sys.path.insert(0, r'c:/Phyton-Projetos/Tabela Auditoria')
os.chdir(r'c:/Phyton-Projetos/Tabela Auditoria')
import psycopg2
from dotenv import load_dotenv

load_dotenv('.env')
import embarques_auto as ea

conn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                        user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = conn.cursor()
resultados = []


def checa(nome, obtido, esperado):
    ok = obtido == esperado
    resultados.append(ok)
    print('  %s  %-64s obtido=%s esperado=%s' % ('OK  ' if ok else 'FALHA', nome, obtido, esperado))


def num_para_id(numero):
    cur.execute("SELECT id FROM embarques_cargas WHERE numero = %s", (numero,))
    r = cur.fetchone()
    return r[0] if r else None


print('\n0) CHAVE DESLIGADA -- o robo tem de se comportar como a producao de hoje')
os.environ['EMBARQUES_MODELO_CARRETA'] = 'false'
checa('chave desligada por padrao', ea.modelo_carreta_ligado(), False)
cur.execute("""SELECT numero, cavalo_placa, carreta1_placa, status FROM embarques_cargas
                WHERE status IN ('Aberta','Em rota','No destino','Desengatada')
                  AND COALESCE(criada_por_robo,FALSE) AND carreta1_placa IS NOT NULL
                ORDER BY data_carregamento DESC LIMIT 1""")
_num, _cav, _c1, _st = cur.fetchone()
_id = num_para_id(_num)
_dia = __import__('datetime').date(2026, 9, 30)
# com a chave DESLIGADA, o cavalo com outra carreta volta a fechar -- que e o defeito antigo,
# e e exatamente o que tem de continuar acontecendo enquanto a mudanca esta congelada
ea.fechar_pendentes(cur, [{'_data': _dia, 'placa_cavalo': _cav, 'placa_carreta': 'ZZZ9Z99',
                           'CHAVE_MANIFESTO': 'TESTE-OFF'}])
cur.execute("SELECT status FROM embarques_cargas WHERE id=%s", (_id,))
checa('DESLIGADA: cavalo com outra carreta ainda fecha (comportamento de hoje)',
      cur.fetchone()[0], 'Entregue')
conn.rollback()
# reanalise nao roda com a chave desligada
checa('DESLIGADA: reanalise nao faz nada', dict(ea.reanalisar_pendentes(cur)), {})
conn.rollback()
os.environ['EMBARQUES_MODELO_CARRETA'] = 'true'
checa('chave ligada quando pedida', ea.modelo_carreta_ligado(), True)

print('\n1) _chegou_ao_destino -- a prova que o dedup passa a exigir')
# C-2026-000617: a carreta parou a 14 km de Catalao, dentro do raio de 20 km -> chegou
checa('C-2026-000617 (parou a 14 km do centro do destino)', ea._chegou_ao_destino(cur, num_para_id('C-2026-000617')), True)
# C-2026-000602: fechada em Guaratingueta, 199 km do Rio, e nunca entrou no raio
checa('C-2026-000602 (nunca entrou no raio do Rio)', ea._chegou_ao_destino(cur, num_para_id('C-2026-000602')), False)
# C-2026-000603: calou em Vitoria da Conquista, 411 km de Feira de Santana
checa('C-2026-000603 (calou a 411 km do destino)', ea._chegou_ao_destino(cur, num_para_id('C-2026-000603')), False)

print('\n2) fechar_pendentes -- manifesto novo so encerra a carga da MESMA carreta')
cur.execute("""SELECT numero, cavalo_placa, carreta1_placa FROM embarques_cargas
                WHERE status IN ('Aberta','Em rota','No destino','Desengatada')
                  AND COALESCE(criada_por_robo,FALSE) AND carreta1_placa IS NOT NULL
                ORDER BY data_carregamento DESC LIMIT 1""")
numero, cav, c1 = cur.fetchone()
antes = num_para_id(numero)
cur.execute("SELECT status FROM embarques_cargas WHERE id=%s", (antes,))
status_antes = cur.fetchone()[0]
dia = __import__('datetime').date(2026, 9, 30)

# (a) manifesto do MESMO cavalo com OUTRA carreta: e troca de recurso, nao pode fechar
ea.fechar_pendentes(cur, [{'_data': dia, 'placa_cavalo': cav, 'placa_carreta': 'ZZZ9Z99',
                           'CHAVE_MANIFESTO': 'TESTE-A'}])
cur.execute("SELECT status FROM embarques_cargas WHERE id=%s", (antes,))
checa('cavalo igual + carreta diferente NAO fecha (%s)' % numero, cur.fetchone()[0], status_antes)

# (b) manifesto da MESMA carreta: encerra a viagem anterior dela
ea.fechar_pendentes(cur, [{'_data': dia, 'placa_cavalo': 'ZZZ9Z98', 'placa_carreta': c1,
                           'CHAVE_MANIFESTO': 'TESTE-B'}])
cur.execute("SELECT status, encerrada_motivo FROM embarques_cargas WHERE id=%s", (antes,))
st, mot = cur.fetchone()
checa('mesma carreta FECHA (%s)' % numero, (st, mot), ('Entregue', 'manifesto_novo'))
conn.rollback()

print('\n3) dedup_veiculo -- so fecha com prova de chegada')
# duas cargas abertas na mesma carreta: a antiga so fecha se tiver chegado ao destino
cur.execute("""SELECT c.id, c.numero, c.carreta1_placa FROM embarques_cargas c
                WHERE c.numero = 'C-2026-000602'""")
cid602, num602, placa602 = cur.fetchone()
cur.execute("UPDATE embarques_cargas SET status='Em rota', data_conclusao=NULL, encerrada_motivo=NULL WHERE id=%s", (cid602,))
cur.execute("""INSERT INTO embarques_cargas
               (numero, tipo_operacao, status, motorista_nome, motorista_cpf, cavalo_placa,
                cavalo_tipo, carreta1_placa, origem_cidade, origem_uf, data_carregamento,
                criada_por_robo, viagem_vazia)
               VALUES ('TESTE-DEDUP','Frota','Aberta','t','0','AAA0A00','Cavalo',%s,'x','MG',
                       '2026-09-30', TRUE, FALSE) RETURNING id""", (placa602,))
novo = cur.fetchone()[0]
ea.dedup_veiculo(cur, {})
cur.execute("SELECT status FROM embarques_cargas WHERE id=%s", (cid602,))
checa('a antiga SEM prova de chegada continua aberta (%s)' % num602, cur.fetchone()[0], 'Em rota')

# agora a mesma coisa com uma carga que CHEGOU: tem de fechar
conn.rollback()
cur.execute("""SELECT id, numero, carreta1_placa FROM embarques_cargas WHERE numero='C-2026-000617'""")
cid617, num617, placa617 = cur.fetchone()
cur.execute("UPDATE embarques_cargas SET status='Em rota', data_conclusao=NULL, encerrada_motivo=NULL WHERE id=%s", (cid617,))
cur.execute("""INSERT INTO embarques_cargas
               (numero, tipo_operacao, status, motorista_nome, motorista_cpf, cavalo_placa,
                cavalo_tipo, carreta1_placa, origem_cidade, origem_uf, data_carregamento,
                criada_por_robo, viagem_vazia)
               VALUES ('TESTE-DEDUP2','Frota','Aberta','t','0','AAA0A00','Cavalo',%s,'x','MG',
                       '2026-09-30', TRUE, FALSE)""", (placa617,))
ea.dedup_veiculo(cur, {})
cur.execute("SELECT status, encerrada_motivo FROM embarques_cargas WHERE id=%s", (cid617,))
checa('a antiga COM prova de chegada fecha (%s)' % num617, cur.fetchone(), ('Entregue', 'sequencia_viagem'))
conn.rollback()

print('\n4) reanalisar_pendentes -- fecha pendencia que o GPS ja respondeu')
# a C-2026-000617 chegou e saiu do destino: reaberta, a reanalise tem de fecha-la de novo
cur.execute("UPDATE embarques_cargas SET status='Em rota', data_conclusao=NULL, encerrada_motivo=NULL WHERE id=%s", (cid617,))
f = ea.reanalisar_pendentes(cur)
cur.execute("SELECT status, encerrada_motivo FROM embarques_cargas WHERE id=%s", (cid617,))
st, mot = cur.fetchone()
checa('C-2026-000617 volta a fechar pela reanalise', (st, mot in ('gps_saiu_do_destino', 'gps_dwell_destino')), ('Entregue', True))
# a C-2026-000603 nunca chegou: a reanalise NAO pode fecha-la.
#
# A asserção mudou em 09/09/26, e vale registrar por que: ela era `status == 'Aberta'`, um
# PROXY de "nao fechou". O proxy quebrou sozinho quando o robo atemporal leu a serie inteira,
# achou a saida da origem pelo GPS (30/08 20:39) e promoveu a carga de 'Aberta' para
# 'Em rota' — que e ele acertando, nao errando: a secao 16.5 dizia que ela ficava 'Aberta'
# porque o WORKER exige ver a placa na origem no instante certo, e o robo nao tem esse limite.
# Teste que mede um proxy falha quando o proxy melhora. Agora ele afirma o que protege:
# a carga NAO pode ter sido encerrada.
cur.execute("SELECT status, data_conclusao FROM embarques_cargas WHERE numero='C-2026-000603'")
_st, _conc = cur.fetchone()
checa('C-2026-000603 (nunca chegou) NAO foi encerrada',
      (_st not in ('Entregue', 'Cancelada'), _conc is None), (True, True))
conn.rollback()

print('\n5) a excecao do reforco no meio da rota continua valendo')
checa('mesmo destino + emitido em rota', ea._reforco_no_meio_da_rota(
    {'origem': {'cidade': 'Uberlândia'}, 'destinos': [{'cidade': 'Embu das Artes'}]},
    'Ananindeua', 'Embu das Artes'), True)
checa('destino diferente', ea._reforco_no_meio_da_rota(
    {'origem': {'cidade': 'Uberlândia'}, 'destinos': [{'cidade': 'Goiânia'}]},
    'Ananindeua', 'Embu das Artes'), False)

conn.rollback()
conn.close()
print('\n%d de %d testes passaram' % (sum(resultados), len(resultados)))
sys.exit(0 if all(resultados) else 1)
