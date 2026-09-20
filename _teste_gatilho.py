# -*- coding: utf-8 -*-
"""Regressão do gatilho do diário (`embarques_auto.deve_rodar`) — §27.12.

Função pura: sem rede, sem banco. Roda em 0,1 s e cobre os dois regimes, inclusive as duas
armadilhas que já custaram rodada neste projeto (comparar instante como texto, e trocar um
mecanismo por outro perdendo a garantia diária).

    python -X utf8 _teste_gatilho.py
"""
from datetime import datetime, date

import embarques_auto as ea

OK = FALHA = 0


def caso(nome, obtido, esperado):
    global OK, FALHA
    bom = obtido == esperado
    globals().__setitem__('OK' if bom else 'FALHA', (OK if bom else FALHA) + 1)
    print(f"  {'OK  ' if bom else 'FALHA'}  {nome:<62} obtido={obtido} esperado={esperado}")


d = lambda h, m=0: datetime(2026, 9, 21, h, m)
M1 = datetime(2026, 9, 21, 8, 0)
M2 = datetime(2026, 9, 21, 10, 0)

print('\n1) REGIME ANTIGO (pos_refresh=False) — nada pode mudar')
caso('fora da janela nao roda', ea.deve_rodar(None, None, d(9), None, pos_refresh=False)[0], False)
caso('dentro da janela roda', ea.deve_rodar(None, None, d(16, 45), None, pos_refresh=False)[0], True)
caso('ja rodou hoje nao repete',
     ea.deve_rodar(None, None, d(17), date(2026, 9, 21), pos_refresh=False)[0], False)
caso('marcador novo NAO dispara com a chave desligada',
     ea.deve_rodar(M2, M1, d(9), date(2026, 9, 21), pos_refresh=False)[0], False)

print('\n2) POS-REFRESH — o BI andou')
caso('marcador andou: roda', ea.deve_rodar(M2, M1, d(9), date(2026, 9, 21), pos_refresh=True)[0], True)
caso('mesmo marcador: NAO roda',
     ea.deve_rodar(M1, M1, d(9), date(2026, 9, 21), pos_refresh=True)[0], False)
caso('marcador mais VELHO: nao roda (refresh que voltou atras)',
     ea.deve_rodar(M1, M2, d(9), date(2026, 9, 21), pos_refresh=True)[0], False)
caso('primeira vez (nada visto ainda): roda',
     ea.deve_rodar(M1, None, d(9), date(2026, 9, 21), pos_refresh=True)[0], True)
caso('sem marcador (BI fora do ar) fora da janela: nao roda',
     ea.deve_rodar(None, M1, d(9), date(2026, 9, 21), pos_refresh=True)[0], False)

print('\n3) A GARANTIA DIARIA sobrevive ao regime novo')
caso('BI nao atualizou o dia inteiro: a janela ainda roda',
     ea.deve_rodar(None, M1, d(16, 45), None, pos_refresh=True)[0], True)
caso('  e o motivo diz que foi a garantia',
     ea.deve_rodar(None, M1, d(16, 45), None, pos_refresh=True)[1], 'garantia diária')
caso('ja rodou hoje e o BI nao andou: nao repete',
     ea.deve_rodar(M1, M1, d(16, 45), date(2026, 9, 21), pos_refresh=True)[0], False)

print('\n4) O INSTANTE, que ja foi comparado como TEXTO e custou uma rodada (§27.11)')
caso('DAX com T == banco com espaco',
     ea._instante('2026-09-19T15:33:50.26') == ea._instante('2026-09-19 15:33:50.260000'), True)
caso('e por isso o mesmo refresh NAO dispara de novo',
     ea.deve_rodar(ea._instante('2026-09-19T15:33:50.26'),
                   ea._instante('2026-09-19 15:33:50.260000'),
                   d(9), date(2026, 9, 21), pos_refresh=True)[0], False)
caso('vazio vira None', (ea._instante(''), ea._instante(None)), (None, None))

print(f'\n{OK} de {OK + FALHA} testes passaram')
raise SystemExit(1 if FALHA else 0)
