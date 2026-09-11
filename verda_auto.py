# -*- coding: utf-8 -*-
"""
Robô semanal da Verda — o disparo automático.

Roda **sexta-feira**, sobre a janela que termina no domingo fechado anterior
(`verda_job.janela_automatica`). A folga de 5 dias existe para o CTRB consolidar:
sem CTRB a viagem não sobe, e CTRB é o que paga o motorista, então ele aparece.

    VERDA_AUTO=true            liga (sem isto, não roda — ver `ligado()`)
    VERDA_AUTO_DIA=sex         dia da semana
    VERDA_AUTO_HORA_BRT=07:00  horário
    VERDA_AUTO_DIAS_RETRO=14   quanto a janela olha para trás

Desligar é virar `VERDA_AUTO` no Portainer — sem deploy, sem editar código.

**Por que não vem ligado por padrão**, ao contrário do robô de embarques: este
manda dado para FORA, para a conta de um fornecedor, e no plano gratuito a Verda
guarda só o consolidado mensal — transação errada lá suja um número que ninguém
consegue inspecionar depois. Subir a imagem em qualquer ambiente não pode
significar começar a publicar inventário.

⚠ EXPOSIÇÃO CONHECIDA — a cauda do mês
───────────────────────────────────────
A janela termina no domingo fechado, então os últimos dias de um mês só sobem na
sexta seguinte. Concretamente: tudo até 27/09 vai em 02/10, mas **28, 29 e 30/09
só vão em 09/10**. Se a Verda fechar o mês fiscal antes disso, essas viagens são
recusadas com "Fiscal month isn't open." — foi o que derrubou as 18 de 31/08 no
primeiro lote de produção (11/09/2026).

Não dá para resolver agora porque **não sabemos que dia o mês fecha**, e chutar
significaria enviar com menos folga de CTRB — trocando um problema conhecido por
outro. O que existe aqui é detecção: `_alertas` reconhece a mensagem e diz o que
fazer. Quando a data do fechamento for conhecida, o conserto é antecipar a cauda
(estender `ate` até o fim do mês na primeira rodada do mês seguinte), e o custo
disso é churn de `alterada` — que a rodada de 14 dias corrige sozinha.
"""

import os
from datetime import datetime, timedelta

import verda_client
import verda_estado as estado
import verda_job


DIAS = {'seg': 0, 'ter': 1, 'qua': 2, 'qui': 3, 'sex': 4, 'sab': 5, 'dom': 6}

# Quanto tempo depois do horário-alvo ainda vale disparar. Existe porque o laço
# dorme 10 min e o container pode ter subido no meio: sem janela, um reinício às
# 07:05 perderia a semana inteira. O fim tem de caber no mesmo dia — a comparação
# não vira a meia-noite.
JANELA_DISPARO_MIN = int(os.getenv('VERDA_AUTO_JANELA_DISPARO_MIN', '180'))


def ligado():
    return os.getenv('VERDA_AUTO', '').strip().lower() in ('1', 'true', 'sim')


def config():
    hora = os.getenv('VERDA_AUTO_HORA_BRT', '07:00')
    try:
        hh, mm = [int(x) for x in hora.split(':')]
    except Exception:
        hh, mm = 7, 0
    dia = DIAS.get(os.getenv('VERDA_AUTO_DIA', 'sex').strip().lower()[:3], 4)
    return dia, hh, mm


def agora_brt():
    """BRT = UTC-3, na mão.

    O container roda em UTC e não tem tz database. Para o que este robô decide —
    "é sexta de manhã?" — a aproximação basta, e é a mesma que o robô de
    embarques usa. Horário de verão não existe no Brasil desde 2019.
    """
    return datetime.utcnow() - timedelta(hours=3)


def executar(hoje=None, log=print):
    """Uma rodada completa, no mesmo caminho da linha de comando.

    Devolve o dicionário do `verda_job.rodada` mais um `alertas` com o que um
    humano precisa olhar. Não levanta exceção: quem chama é uma thread, e
    derrubá-la deixaria o inventário parado em silêncio até alguém reparar.
    """
    cliente = verda_client.Verda()
    desde, ate = verda_job.janela_automatica(hoje)
    log('Verda auto: janela %s a %s (ambiente %s)' % (desde, ate, cliente.rotulo))

    con = estado.conectar()
    try:
        estado.garantir_tabela(con)
        r = verda_job.rodada(con, cliente, desde, ate, log=log)
        r['alertas'] = _alertas(con, cliente.rotulo, r)
    finally:
        con.close()

    for a in r.get('alertas') or []:
        log('!! Verda auto: %s' % a)
    return r


def _alertas(con, ambiente, r):
    """O que a rodada produziu que ninguém vai ver sozinho.

    Rejeitada **não volta para a fila** (proteção contra retry cego), então sem
    isto ela fica fora do inventário para sempre e calada. O mês fiscal ganha
    linha própria porque é o único caso previsível: a janela seg–dom atravessa a
    virada de mês uma vez por mês, e se o mês anterior já fechou na conta a
    viagem é recusada com "Fiscal month isn't open."
    """
    alertas = []
    travadas = estado.travadas(con, ambiente)
    if travadas:
        mes_fiscal = [t for t in travadas if 'fiscal month' in (t['mensagem'] or '').lower()]
        if mes_fiscal:
            alertas.append(
                '%d viagem(ns) recusadas por MES FISCAL FECHADO (%s a %s). Elas nao '
                'voltam sozinhas: quando a Verda abrir o mes, reenvie pela aba /verda.'
                % (len(mes_fiscal), mes_fiscal[0]['data_viagem'], mes_fiscal[-1]['data_viagem']))
        outras = len(travadas) - len(mes_fiscal)
        if outras:
            alertas.append('%d viagem(ns) travadas por outros motivos — ver a aba /verda' % outras)

    if r.get('parada'):
        alertas.append('envio interrompido: %s' % r['parada'])
    if r.get('alerta_janela_vazia'):
        alertas.append('janela sem viagem nenhuma — suspeite do ETL de manifestos')
    if r.get('sem_veredito'):
        # Não é problema: o veredito leva 1 a 2 min e o POST devolve só o id. A
        # próxima passada resolve — e é por isso que a thread confere de novo.
        alertas.append('%d viagem(ns) ainda sem veredito; a proxima passada confere'
                       % r['sem_veredito'])
    return alertas


def decidir(agora, ultimo, dia=None, hh=None, mm=None):
    """O que fazer neste minuto: `'rodar'`, `'conferir'` ou `None`.

    Separado do laço para poder ser testado sem esperar uma sexta-feira.
    """
    if dia is None:
        dia, hh, mm = config()
    alvo = hh * 60 + mm
    minutos = agora.hour * 60 + agora.minute
    na_janela = alvo <= minutos <= alvo + JANELA_DISPARO_MIN
    if not na_janela:
        return None
    if agora.weekday() == dia and ultimo != agora.date():
        return 'rodar'
    if ultimo == agora.date():
        return 'conferir'
    return None


def loop(log=print):
    """O laço da thread: acorda de 10 em 10 min e dispara no dia e hora certos."""
    import time
    dia, hh, mm = config()
    ultimo = None          # data do último disparo, para não repetir no mesmo dia
    while True:
        try:
            agora = agora_brt()
            acao = decidir(agora, ultimo, dia, hh, mm)
            if acao == 'rodar':
                ultimo = agora.date()
                executar(hoje=agora.date(), log=log)
            elif acao == 'conferir':
                # Mesma janela, rodada já feita: só busca o veredito das que
                # ficaram `enviado`. É leitura pura, e sem isto o lote fecharia a
                # semana sem saber se foi aceito.
                _reconferir(log)
        except Exception as e:
            # Exceção aqui não pode derrubar a thread: o pior caso é a semana
            # ficar sem envio, e a rodada é idempotente — a seguinte recupera.
            log('!! Verda auto: falha na rodada: %s' % e)
        time.sleep(600)


def _reconferir(log):
    cliente = verda_client.Verda()
    con = estado.conectar()
    try:
        c = verda_job.conferir(con, cliente)
        if c:
            log('Verda auto: conferencia %s' % verda_job._resumir(c))
    finally:
        con.close()


if __name__ == '__main__':
    # `python verda_auto.py --agora` roda AGORA a rodada que a sexta rodaria —
    # mesma janela, mesmo caminho. É como se confere o robô sem esperar o dia.
    #
    # A trava de produção vale aqui igual à da linha de comando: rodar a mão o
    # que o robô faz sozinho continua sendo enviar para o inventário de verdade.
    # No laço da thread ela não existe porque `VERDA_AUTO=true` já é o ato
    # deliberado, feito uma vez, por quem tem acesso ao Portainer.
    import sys
    args = sys.argv[1:]
    if '--agora' not in args:
        print('uso: python verda_auto.py --agora [--sim-producao]')
        print('     (sem --agora nao faz nada; o laco de verdade sobe pelo server.py)')
        sys.exit(2)
    _cli = verda_client.Verda()
    if _cli.ambiente == 'producao' and '--sim-producao' not in args:
        print('!! PRODUCAO exige --sim-producao.')
        sys.exit(1)
    _r = executar()
    print('\njanela %s a %s' % tuple(_r['janela']))
    for _k in ('montagem', 'expurgo', 'envio', 'conferencia'):
        if _r.get(_k):
            print('%-12s %s' % (_k.upper(), verda_job._resumir(_r[_k])))
