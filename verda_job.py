# -*- coding: utf-8 -*-
"""
Robô da Verda — monta, envia e confere.

    python -X utf8 verda_job.py                      # D-1
    python -X utf8 verda_job.py --data 2026-08-20
    python -X utf8 verda_job.py --desde 2026-01-01 --ate 2026-08-27   # backfill
    python -X utf8 verda_job.py --conferir           # só o polling, sem enviar
    python -X utf8 verda_job.py --resumo             # situação da fila
    python -X utf8 verda_job.py --data 2026-08-20 --so-montar   # não toca na rede

Três etapas, e cada uma pode rodar sozinha:

  1. MONTAR    manifesto → payload → grava em `verda_envios` como pendente
  2. ENVIAR    pendentes → POST Fuel/Weight → guarda o TransactionId
  3. CONFERIR  enviadas → GetTransaction → executed ou rejected (com o campo do erro)

A conferência é etapa separada porque o processamento da Verda é assíncrono: o
POST devolve só Success/TransactionId, e o motivo da rejeição só aparece depois.
Marcar "enviado" e esquecer deixaria viagem fora do inventário sem ninguém saber.

Enquanto `VERDA_SIMULADO=1` (padrão), o envio não sai da máquina — dá para
exercitar o robô inteiro antes de a Verda liberar a URL.
"""

import argparse
import sys
from datetime import date, datetime, timedelta

import _verda_demo as fonte
import _verda_valida as validador
import verda_client
import verda_estado as estado
import verda_payload as vp
from server import get_token


# ════════════════════════════════════════
# TRAVAS
# ════════════════════════════════════════
# Emissão errada só aparece quando o cliente audita, meses depois. Cada trava
# aqui existe para um erro que já aconteceu ou que passaria despercebido.

# Teto de viagens por rodada. O D-1 tem ~25; um backfill tem milhares. Como a
# Verda não documenta limite de taxa (pergunta 6, em aberto), despejar tudo de
# uma vez pode derrubar ou render bloqueio. Passar exige --forcar-lote.
TETO_LOTE = 500

# Freio de anomalia: se o consumo médio do lote destoar tanto assim da linha de
# base já aceita, para. Um erro de parâmetro (2,59 virar 0,259) multiplicaria a
# emissão por dez em silêncio — o payload continua válido, só o número é absurdo.
DESVIO_MAXIMO = 0.25

# Janela que deveria ter viagens e veio vazia é sintoma de ETL parado, não de
# dia sem movimento. Sair com "sucesso" nesse caso esconde o problema.
AVISAR_JANELA_VAZIA = True

# Data em que o inventário da Rizza começa, por decisão da diretoria: nada
# anterior a 01/09/2026 entra em produção. Não é preferência — é o que a
# plataforma impõe. Mês fiscal fechado na conta recusa a viagem com
# "Fiscal month isn't open", e agosto nunca foi aberto: foi assim que as 18
# viagens de 31/08 do primeiro lote (11/09/2026) foram rejeitadas, enquanto as
# 101 de setembro passaram na mesma leva — o que prova que o problema é o mês
# fechado, não lote atravessando a virada.
#
# A trava é aqui, na montagem, e não na janela do comando, porque a janela
# semanal (segunda a domingo) atravessa a virada de mês uma vez por mês.
DATA_INICIO_INVENTARIO = '2026-09-01'


# ════════════════════════════════════════
# 1. MONTAR
# ════════════════════════════════════════

def tid_de(viagem):
    """O `TransportationId` de um manifesto, sem precisar montar o payload."""
    return '%s%s' % (viagem['sigla'], viagem['numero'])


def _sair_do_escopo(con, transportation_id, motivo, ambiente, expurgar):
    """Viagem que deixou de qualificar — e que talvez já esteja na Verda.

    Marcar no nosso banco não tira nada do inventário deles: enquanto a
    transação estiver viva, a emissão continua contando. Antes disto, uma viagem
    que perdesse o CTRB (ou o CTe) era pulada em silêncio e ficava lá para
    sempre, sem ninguém saber. `fora_de_escopo` devolve o `transaction_id` só
    quando ele chegou a existir; nos outros casos não há o que cancelar.
    """
    tid = estado.fora_de_escopo(con, transportation_id, motivo, ambiente)
    if tid:
        expurgar.append((transportation_id, tid))


def montar(con, desde, ate, ambiente='simulado', dados=None):
    """Transforma em payload as viagens da janela e grava como pendentes.

    Payload que não passa na validação local **não entra na fila** — vira
    `bloqueado` com o motivo. Mandar inválido só gera `rejected` do outro lado.
    """
    dados = dados or fonte.carregar(get_token())
    contagem = {'nova': 0, 'alterada': 0, 'inalterada': 0, 'sem_cte': 0,
                'sem_payload': 0, 'BLOQUEADA': 0, 'sem CTRB': 0}
    alteradas = []
    expurgar = []   # (transportation_id, transaction_id) - saiu do escopo e ja foi enviada

    for viagem in dados['viagens']:
        # A janela e a data_ref_ctrc da Auditoria Receita, nao a data de emissao
        # do manifesto: manifesto de agosto pode ter CTe de julho, e alinhar as
        # duas pontas evita que a viagem suma da janela.
        aud_ref = (dados['auditoria'].get('%s%s' % (viagem['sigla'], viagem['numero'])) or {})
        d = str(aud_ref.get('dt') or viagem.get('data_emissao') or '')[:10]
        if not (desde <= d <= ate):
            continue

        # Anterior ao início do inventário: não sobe, e se por acaso já subiu
        # tem de sair de lá. O `_sair_do_escopo` devolve o transaction_id para
        # o EXPURGO cancelar — marcar só no nosso banco não tira emissão nenhuma
        # do inventário deles.
        if DATA_INICIO_INVENTARIO and d < DATA_INICIO_INVENTARIO:
            contagem['antes do início'] = contagem.get('antes do início', 0) + 1
            _sair_do_escopo(con, tid_de(viagem),
                            'anterior ao início do inventário (%s)' % DATA_INICIO_INVENTARIO,
                            ambiente, expurgar)
            continue

        v, itens, aud = fonte.preparar(viagem, dados)
        if v is None:
            # sem CTRB nao ha trecho rodado, e sem trecho a viagem nao sobe
            contagem['sem CTRB'] += 1
            _sair_do_escopo(con, tid_de(viagem), 'sem CTRB na Auditoria Receita',
                            ambiente, expurgar)
            continue
        if not itens:
            contagem['sem_cte'] += 1
            _sair_do_escopo(con, tid_de(viagem), 'manifesto sem CTe', ambiente, expurgar)
            continue

        api, payload, avisos = vp.montar(v, itens, dados['cadastro'], dados['observado'],
                                         fonte.CNPJ_RIZZA)
        if not payload.get('VehicleTypeKey'):
            contagem['sem_payload'] += 1
            _sair_do_escopo(con, v['transportation_id'], 'sem VehicleTypeKey',
                            ambiente, expurgar)
            continue

        problemas = validador.validar(api, payload)
        if problemas:
            estado.bloquear(con, v['transportation_id'], d, api, payload, problemas, ambiente)
            contagem['BLOQUEADA'] += 1
            continue

        situacao = estado.registrar(con, v['transportation_id'], d, api, payload, avisos,
                                    ambiente)
        contagem[situacao] += 1
        if situacao == 'alterada':
            alteradas.append(v['transportation_id'])
    con.commit()
    return contagem, alteradas, expurgar


# ════════════════════════════════════════
# 2. ENVIAR
# ════════════════════════════════════════

def expurgar_do_inventario(con, cliente, expurgar):
    """Cancela na Verda as viagens que saíram do escopo.

    Marcar no nosso banco não tira nada do inventário deles: enquanto a transação
    estiver viva, a emissão continua contando. Só depois do `canceled` confirmado
    o vínculo é apagado — se o cancelamento falhar, o `transaction_id` fica
    gravado para tentar de novo na próxima rodada.
    """
    contagem = {'cancelada': 0, 'FALHOU': 0}
    for tpid, tid in expurgar:
        # Transação já morta não segura emissão nenhuma: não há o que cancelar,
        # só o vínculo a apagar. Tentar cancelá-la devolveria erro e a viagem
        # ficaria marcada como falha de expurgo sem nada de errado acontecendo.
        if not _transacao_viva(cliente, tid):
            estado.limpar_cancelada(con, tpid, cliente.rotulo)
            contagem['ja estava morta'] = contagem.get('ja estava morta', 0) + 1
            con.commit()
            continue
        try:
            ok, msg = cliente.cancelar(tid, tpid)
        except verda_client.VerdaErro as e:
            ok, msg = False, str(e)
        if ok:
            estado.limpar_cancelada(con, tpid, cliente.rotulo)
            contagem['cancelada'] += 1
        else:
            contagem['FALHOU'] += 1
            print('   !! nao cancelou %s (%s): %s' % (tpid, tid, msg))
        con.commit()
    return contagem


def freio_de_anomalia(con, fila, ambiente='simulado'):
    """Compara o consumo médio do lote com a linha de base já aceita.

    Retorna None se pode seguir, ou o motivo da parada. Não olha o payload campo
    a campo — isso o validador já fez. Olha o **agregado**, que é onde erro de
    parâmetro se esconde: cada viagem parece certa e o total está dez vezes fora.
    """
    base, n_base = estado.media_litros_enviados(con, ambiente)
    if base is None:
        return None   # sem histórico suficiente ainda; a primeira leva passa
    consumos = [float(l['payload'].get('FuelConsumption') or 0)
                for l in fila if l['api'] == 'Fuel' and l['payload'].get('FuelConsumption')]
    if len(consumos) < 10:
        return None
    media = sum(consumos) / len(consumos)
    desvio = abs(media - base) / base
    if desvio > DESVIO_MAXIMO:
        return ('consumo médio do lote %.2f km/l contra %.2f da base (%d viagens) — '
                'desvio de %.0f%%, acima do limite de %.0f%%. Confira os parâmetros de '
                'consumo antes de seguir; para ignorar, use --ignorar-freio.'
                % (media, base, n_base, 100 * desvio, 100 * DESVIO_MAXIMO))
    return None


def _transacao_viva(cliente, transaction_id):
    """A transação ainda ocupa o `TransportationId`?

    Só transação VIVA bloqueia o id (§13). Uma `rejected` já está morta, e
    tentar cancelá-la devolve `Success: false` — que a trava de segurança lia
    como "não consegui liberar" e usava para pular a viagem **para sempre**. Foi
    o que aconteceria com as 3 viagens de rígido rejeitadas por
    `Invalid 'VehicleTypeKey'` em 11/09/2026: corrigida a chave, elas voltariam
    à fila e seriam puladas em silêncio, sem nunca entrar no inventário.

    Na dúvida devolve True. Errar para "viva" custa uma viagem pulada, com
    aviso; errar para "morta" cria uma segunda transação viva no mesmo id e
    conta a emissão duas vezes — e isso o cliente só descobre numa auditoria.
    """
    try:
        r = cliente.conferir(transaction_id=transaction_id)
    except verda_client.VerdaErro:
        return True      # não deu para perguntar: não se decide no escuro
    if not r:
        # A Verda não conhece esta transação — e o §17.1 já mostrou o que isso
        # significa: perguntar por uma transação de homologação com a chave de
        # produção devolve vazio. Ou seja, "vazio" é "não existe NESTA conta", e
        # uma transação que não existe aqui não pode estar ocupando o
        # `TransportationId` aqui. É o caso do id herdado de outro ambiente.
        return False
    return r[0]['status'] not in verda_client.STATUS_FINAL_RUIM


def enviar(con, cliente, limite=None, forcar_lote=False, ignorar_freio=False, ids=None):
    """Envia os pendentes, cancelando antes o que ainda tem transação viva.

    O cancelamento acontece num lugar só: no laço de envio, para qualquer viagem
    que chegue com `transaction_id`. Já houve aqui um segundo laço que cancelava
    as "alteradas" antes — os dois colidiam: o primeiro cancelava mas não limpava
    o `transaction_id`, e o segundo recebia "This 'TransactionKey' has been
    canceled", tratava como falha e pulava o envio. Resultado: 76 viagens
    canceladas e nunca reenviadas (01/09/2026).
    """
    ambiente = cliente.rotulo
    fila = estado.a_enviar(con, limite=limite, ambiente=ambiente, ids=ids)

    if len(fila) > TETO_LOTE and not forcar_lote:
        return {}, ('%d viagens na fila, acima do teto de %d por rodada. Rode em janelas '
                    'menores, ou use --forcar-lote se tiver certeza (a Verda não documenta '
                    'limite de taxa).' % (len(fila), TETO_LOTE))

    if not ignorar_freio:
        motivo = freio_de_anomalia(con, fila, ambiente)
        if motivo:
            return {}, motivo

    contagem = {'aceita': 0, 'recusada': 0, 'falha': 0, 'cancelada antes': 0}
    for linha in fila:
        tid, api, payload = linha['transportation_id'], linha['api'], linha['payload']

        # A Verda recusa TransportationId que ja tenha transacao ativa, e o
        # cancelamento e o unico jeito de liberar o id. Nao basta cancelar as
        # marcadas 'alterada': a viagem pode ter sido montada numa execucao e
        # enviada em outra, chegando aqui 'inalterada' com a transacao antiga
        # ainda viva. Foi assim que 61 viagens voltaram 'rejected' em 01/09/2026.
        if linha.get('transaction_id'):
            # Transação já morta (rejected/canceled) não ocupa o id: não há o que
            # cancelar, só o vínculo a apagar. Chamar o CancelTransaction aqui
            # falharia e a viagem seria pulada.
            if not _transacao_viva(cliente, linha['transaction_id']):
                estado.limpar_cancelada(con, tid, ambiente)
                con.commit()
                contagem['id ja estava livre'] = contagem.get('id ja estava livre', 0) + 1
                linha['transaction_id'] = None
        if linha.get('transaction_id'):
            try:
                ok, msg = cliente.cancelar(linha['transaction_id'], tid)
            except verda_client.VerdaErro as ex:
                ok, msg = False, str(ex)
            # "ja esta cancelada" e SUCESSO: o objetivo era liberar o
            # TransportationId, e ele esta livre. Tratar como falha deixava a
            # viagem cancelada e nunca reenviada.
            if ok or 'has been canceled' in str(msg):
                estado.limpar_cancelada(con, tid, ambiente)
                con.commit()
                contagem['cancelada antes'] += 1
            else:
                print('   !! %s tem transacao viva e nao cancelou: %s' % (tid, msg))
                continue

        try:
            aceito, transaction_id, mensagem = cliente.enviar(api, payload)
        except verda_client.VerdaErro as e:
            estado.marcar_erro(con, tid, e, ambiente=ambiente)
            contagem['falha'] += 1
        else:
            if aceito and transaction_id:
                estado.marcar_enviado(con, tid, transaction_id, mensagem, ambiente)
                contagem['aceita'] += 1
            else:
                # recusa de negócio: guarda o TransactionId se veio, para conferir depois
                if transaction_id:
                    estado.marcar_enviado(con, tid, transaction_id, mensagem, ambiente)
                    contagem['aceita'] += 1
                else:
                    estado.marcar_erro(con, tid, mensagem or 'recusada sem TransactionId',
                                       ambiente=ambiente)
                    contagem['recusada'] += 1
        con.commit()
    return contagem, None


def reenviar(con, cliente, ids):
    """Devolve à fila e reenvia viagens que travaram, com o payload já montado.

    É o caminho para o travamento que se resolve do OUTRO lado: mês fiscal que
    abre, tipo de veículo que passa a existir na conta, instabilidade da
    plataforma. Nesses casos o payload que temos já está certo e remontar seria
    trabalho à toa — a única coisa errada era o momento.

    **Não serve para conserto de regra nossa.** Se o que mudou foi a montagem
    (uma chave, uma constante, o cálculo de distância), o payload gravado ainda
    é o antigo e reenviar repete o mesmo erro: aí o caminho é rodar o job na
    janela, que remonta, detecta 'alterada' e reenvia com o conteúdo novo.
    """
    devolvidas = estado.devolver_a_fila(con, ids, cliente.rotulo)
    con.commit()
    if not devolvidas:
        return {}, ('nenhuma das viagens indicadas está travada — só `rejected` e `erro` '
                    'voltam para a fila')
    envio, parada = enviar(con, cliente, ids=devolvidas)
    if parada:
        return {}, parada
    envio['devolvidas a fila'] = len(devolvidas)
    return envio, None


# ════════════════════════════════════════
# 3. CONFERIR
# ════════════════════════════════════════

def conferir(con, cliente, limite=None):
    """Pergunta à Verda o que aconteceu com cada transação enviada."""
    fila = estado.a_conferir(con, limite=limite, ambiente=cliente.rotulo)
    contagem = {}
    for linha in fila:
        try:
            resultado = cliente.conferir(transaction_id=linha['transaction_id'])
        except verda_client.VerdaErro as e:
            print('   !! %s: %s' % (linha['transaction_id'], e))
            continue
        if not resultado:
            contagem['sem resposta'] = contagem.get('sem resposta', 0) + 1
            continue
        t = resultado[0]
        if t['status'] in verda_client.STATUS_EM_CURSO:
            contagem['ainda processando'] = contagem.get('ainda processando', 0) + 1
            continue
        estado.marcar_status(con, t['transaction_id'], t['status'], t['mensagem'],
                             [{'campo': c, 'erro': d} for c, d in t['erros']])
        contagem[t['status']] = contagem.get(t['status'], 0) + 1
        con.commit()
    con.commit()
    return contagem


# ════════════════════════════════════════
# CLI
# ════════════════════════════════════════

def _imprimir(titulo, contagem):
    print('\n%s' % titulo)
    if not contagem:
        print('   nada')
    for k, n in sorted(contagem.items(), key=lambda x: -x[1]):
        print('   %-22s %5d' % (k, n))


def main():
    ap = argparse.ArgumentParser(description='Robô de envio de viagens para a Verda')
    ap.add_argument('--data', help='uma data (YYYY-MM-DD)')
    ap.add_argument('--desde', help='início da janela (YYYY-MM-DD)')
    ap.add_argument('--ate', help='fim da janela (YYYY-MM-DD)')
    ap.add_argument('--conferir', action='store_true', help='só o polling do GetTransaction')
    ap.add_argument('--resumo', action='store_true', help='situação da fila')
    ap.add_argument('--so-montar', action='store_true', help='monta e grava, não envia')
    ap.add_argument('--limite', type=int, help='no máximo N viagens (para testar)')
    ap.add_argument('--forcar-lote', action='store_true',
                    help='permite passar do teto de %d viagens por rodada' % TETO_LOTE)
    ap.add_argument('--ignorar-freio', action='store_true',
                    help='envia mesmo com o consumo médio destoando da base')
    ap.add_argument('--sim-producao', action='store_true',
                    help='confirma envio para PRODUÇÃO (obrigatório fora do modo simulado)')
    args = ap.parse_args()

    con = estado.conectar()
    estado.garantir_tabela(con)
    cliente = verda_client.Verda()
    if cliente.simulado:
        print('>> MODO SIMULADO (VERDA_SIMULADO=1) — nada sai para a rede\n')
    else:
        print('>> ambiente: %s [ENVIO REAL]' % cliente.ambiente.upper())
        print('   %s' % cliente.base)
        if vp.VEHICLE_TYPE_KEY_FIXO:
            print('   VehicleTypeKey carimbado como %r (conta de homologacao)'
                  % vp.VEHICLE_TYPE_KEY_FIXO)
        print('')
        # Trava de ambiente: mandar para produção tem que ser ato deliberado. Na
        # versão gratuita não fica detalhe de viagem, só o consolidado do mês —
        # transação errada lá suja um número que ninguém consegue inspecionar
        # depois para conferir se o CancelTransaction limpou de verdade.
        #
        # `--so-montar` e `--resumo` NÃO passam por aqui: nenhum dos dois fala com
        # a Verda. O `--so-montar` lê o Power BI e grava pendente no nosso banco,
        # e o expurgo é pulado. Barrá-lo tirava justamente a conferência que a
        # trava quer incentivar — dava para ver o lote antes de mandar, e a trava
        # obrigava a usar a flag de envio real só para poder olhar.
        if (cliente.ambiente == 'producao' and not args.sim_producao
                and not args.resumo and not args.so_montar):
            print('!! PRODUÇÃO exige --sim-producao. Valide em homologação primeiro.')
            con.close()
            return 1

    if args.resumo:
        print('%-10s %-14s %6s' % ('ambiente', 'status', 'viagens'))
        for r in estado.resumo(con):
            print('%-10s %-14s %6d' % (r['ambiente'], r['status'], r['n']))
        ruins = estado.rejeitadas(con, limite=10)
        if ruins:
            print('\nrejeitadas mais recentes:')
            for r in ruins:
                campos = ', '.join(e.get('campo') or '?' for e in (r['erro_detalhe'] or []))
                print('   %-10s %-14s %s  %s' % (r['ambiente'], r['transportation_id'],
                                                 r['data_viagem'], campos or r['mensagem']))
        con.close()
        return

    if args.conferir:
        _imprimir('CONFERÊNCIA', conferir(con, cliente, limite=args.limite))
        con.close()
        return

    if args.data:
        desde = ate = args.data
    elif args.desde:
        desde, ate = args.desde, args.ate or date.today().isoformat()
    else:
        ontem = (date.today() - timedelta(days=1)).isoformat()
        desde = ate = ontem
    print('janela: %s a %s' % (desde, ate))

    contagem, alteradas, expurgar = montar(con, desde, ate, cliente.rotulo)
    _imprimir('MONTAGEM', contagem)
    if contagem.get('BLOQUEADA'):
        print('   %d viagens NÃO entraram na fila (payload inválido) — '
              'veja com --resumo' % contagem['BLOQUEADA'])
    if alteradas:
        print('   (%d viagens mudaram de conteúdo e serão canceladas e reenviadas)' % len(alteradas))

    # 'antes do início' entra na conta para não disparar alarme falso: janela
    # inteiramente anterior a DATA_INICIO_INVENTARIO não é ETL parado.
    if AVISAR_JANELA_VAZIA and not any(contagem.get(k) for k in
                                       ('nova', 'alterada', 'inalterada', 'BLOQUEADA',
                                        'antes do início')):
        print('\n!! NENHUMA viagem na janela %s a %s. Se era dia útil, suspeite do ETL '
              'de manifestos, não de falta de movimento.' % (desde, ate))

    # Junta o que ficou pendente de rodadas anteriores — tipicamente um
    # `--so-montar` que marcou fora de escopo sem cancelar nada. Sem isto, aquela
    # transação nunca mais seria alcançada por nenhuma rodada.
    ja_marcadas = [par for par in estado.escopo_com_vinculo(con, cliente.rotulo)
                   if par[0] not in {t for t, _ in expurgar}]
    if ja_marcadas:
        print('\n%d viagem(ns) ja estavam fora de escopo com vinculo pendente.'
              % len(ja_marcadas))
        expurgar += ja_marcadas

    if expurgar:
        print('')
        print('%d viagem(ns) sairam do escopo e ainda estao no inventario da Verda.'
              % len(expurgar))
        if args.so_montar:
            print('   (--so-montar: nada foi cancelado)')
        else:
            _imprimir('EXPURGO', expurgar_do_inventario(con, cliente, expurgar))

    if args.so_montar:
        con.close()
        return

    envio, parada = enviar(con, cliente, limite=args.limite,
                           forcar_lote=args.forcar_lote, ignorar_freio=args.ignorar_freio)
    if parada:
        print('\n!! ENVIO INTERROMPIDO\n   %s' % parada)
        con.close()
        return 1
    _imprimir('ENVIO', envio)
    _imprimir('CONFERÊNCIA', conferir(con, cliente, limite=args.limite))
    con.close()


if __name__ == '__main__':
    sys.exit(main())
