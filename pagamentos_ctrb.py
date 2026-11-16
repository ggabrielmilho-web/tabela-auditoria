# -*- coding: utf-8 -*-
"""PAGAMENTOS DE CTRB — a conferência antes de pagar (16/11/2026, pedido do diretor).

O CTRB não tem trava nem vínculo com a receita: dá para emitir um CTRB de agregado em cima da viagem da
frota, ou pagar duas vezes o trecho de uma viagem que quebra no hub (São Paulo × Goiânia que troca de
cavalo em Uberlândia). Hoje a única trava é o financeiro desconfiar na hora de pagar. Esta conferência
cruza tudo o que existe sobre cada CTRB e diz, antes do pagamento, se aquele trecho já foi pago.

Estudo que originou (16/11/2026, 2026 inteiro; números no README local): o adiantamento sai no dia da emissão
(mediana 0 d) e o saldo ~13 d depois; o 477 nunca paga acima do CTRB — o furo é EMITIR o CTRB que não devia existir.

Fontes (só leitura):
  Power BI, dataset principal ... `public ctrbs_oss` (073), `public manifestos` (916), `public manifestos_ctrc`,
                                  `public conhecimentos_emitidos` (455), `public consulta_despesas_477` (5101/5102),
                                  `public veiculos_045`, `public rotas_km`
  Power BI, dataset contábil .... `public extrato_bancario_456`: a saída de cada pagamento (`ref_477_*`) e os
                                  ESTORNOS — entrada manual com o nº do CTRB no histórico e sem nome
                                  ("ESTORNO PAGAMENTO DO ADIANTAMENTO CTRB UDI 1234-5"). O 477 segue mostrando
                                  esses pagamentos como liquidados.
  Banco do app .................. `embarques_cargas`: o GPS diz se a carreta trocou de cavalo no caminho.

Três armadilhas medidas, que a régua respeita:
  - O 073 NÃO é extração completa (cada mês entra perto do fechamento e é reextraído por ~40 dias; `situacao`
    só existe desde jul/26). Por isso "pago" é sempre o 477 (completo), descontado o estorno do 456.
  - Desde jun/26 o PIX da Sicoob sai do banco num lote diário ("DEBITO VIA RET BCO-LIQUIDACAO"), sem o
    detalhe por motorista: a saída desses fica como "no lote do dia", não como "não encontrada".
  - Entrada no 456 vinda do agregado pelo NOME é fatura de frete (CT-e série UDS, ele como pagador), não
    devolução. Devolução de CTRB só se reconhece pelo nº do CTRB no histórico.

A lógica (`analisar`) é pura: recebe o que foi lido e devolve o pacote que a tela usa. `coletar` lê o BI,
`evidencia_embarques` lê o banco, `executar` junta, grava a rodada e é o que o laço chama.
"""
import gzip
import json
import os
import re
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

DESDE = os.getenv('PAGAMENTOS_CTRB_DESDE', '2026-01-01')
AUTO = os.getenv('PAGAMENTOS_CTRB_AUTO', 'false').lower() == 'true'   # recalcular após cada refresh do BI
ESPERA_POS_REFRESH_MIN = int(os.getenv('PAGAMENTOS_CTRB_ESPERA_MIN', '10'))
INTERVALO_MANUAL_MIN = 5          # "Atualizar agora" não reroda antes disso
RODADAS_GUARDADAS = 5
JANELA_DUPLICADO_H = 36           # mesma placa + mesma rota em até 36 h = o mesmo serviço
FOLGA_CAMINHO = 1.2               # B está no caminho de A quando km(A.origem→B.origem) + km(B) ≤ 1,2 × km(A)
DIAS_DESCONTO = 120               # desconto em conta corrente procurado até 120 dias depois
BRT = timedelta(hours=3)

TIPOS_CASO = {
    'ciot_substituido': 'CTRB refeito por erro no CIOT',
    'diaria_repetida': 'Mesma diária em dois CTRBs',
    'trecho_hub': 'Mesmo trecho em dois CTRBs',
    'sobre_frota': 'CTRB em cima da viagem da frota',
    'mesma_perna': 'Mesma perna em dois CTRBs',
    'reemissao': 'CTRB refeito (reemissão)',
    'mesmo_ciot': 'Mesmo CIOT em dois CTRBs',
    'dois_caminhoes': 'Mesmo CT-e em dois caminhões',
}
EXPLICA_CASO = {
    'ciot_substituido': 'O CTRB saiu com erro no CIOT (a Pamcard/ANTT recusou) e foi refeito — o antigo não pode ser pago.',
    'diaria_repetida': 'A mesma diária (mesmo motorista, mesma viagem de referência, mesmo valor) foi lançada mais de uma vez.',
    'trecho_hub': 'A carreta trocou de cavalo no caminho, mas o primeiro CTRB foi lançado até o destino final — '
                  'o trecho do ponto de troca até o destino está em dois CTRBs.',
    'sobre_frota': 'A carga já estava num CTRB da frota nesse trecho e ganhou um CTRB de agregado ou terceiro.',
    'mesma_perna': 'A mesma carga, na mesma rota, está em dois CTRBs com valor a pagar.',
    'reemissao': 'O CTRB foi refeito (mesma placa e rota) e o antigo continua com pagamento no 477.',
    'mesmo_ciot': 'Dois CTRBs com o mesmo CIOT — é o mesmo contrato de frete.',
    'dois_caminhoes': 'Conjuntos diferentes fizeram a mesma rota com o mesmo CT-e, e um deles não tem carga própria no '
                      'manifesto — confira se a carga foi mesmo dividida ou se o CT-e só foi manifestado duas vezes.',
}
PRIORIDADE_TIPO = ['ciot_substituido', 'reemissao', 'mesmo_ciot', 'sobre_frota', 'trecho_hub', 'mesma_perna',
                   'diaria_repetida', 'dois_caminhoes']
STATUS_CASO = {
    'pendente': 'Dá para segurar',
    'pago_2x': 'Pago a mais',
    'conferir': 'Conferir',
    'recuperado': 'Voltou',
    'explicado': 'Explicado',
    'um_recebeu': 'Só um recebeu',
}
ORDEM_STATUS = {'pendente': 0, 'pago_2x': 1, 'conferir': 2, 'recuperado': 3, 'explicado': 4, 'um_recebeu': 5}
DECISOES = {
    'ok': 'Conferido — está certo',
    'segurar': 'Pagamento segurado',
    'cobrar': 'Em cobrança / desconto',
    'resolvido': 'Resolvido',
    '': 'Sem decisão',
}


# ── utilidades ───────────────────────────────────────────────────────────────────────────────────────
def n(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def doc(s):
    return re.sub(r'[^A-Z0-9]', '', str(s or '').upper())


def base_ctrb(ctrb):
    """'UDI001234-5' → 'UDI001234' (como o 916 grava o CTRB)."""
    s = doc(ctrb)
    return s[:-1] if len(s) == 10 else s


def dt(s):
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    try:
        return datetime.fromisoformat(str(s)[:19])
    except ValueError:
        return None


def dia(s):
    d = dt(s)
    return d.date() if d else None


def placa(p):
    """Chave de placa: a grafia Mercosul (só para casar as duas grafias do mesmo veículo)."""
    p = doc(p)
    if len(p) == 7 and p[4].isdigit():
        p = p[:4] + 'ABCDEFGHIJ'[int(p[4])] + p[5:]
    return p


def ciot12(v):
    d = re.sub(r'\D', '', str(v or ''))
    return d[:12] if len(d) >= 12 else ''


_RE_CTRB = re.compile(r'CTRB\s+([A-Z]{3})\s+0*(\d+)-(\d)')
_RE_REEMITIDO = re.compile(r'BAIXADO PELA EMISSAO DE NOVO CTRB:\s*([A-Z]{3})\s*0*(\d+)-(\d)')
_RE_DESC = re.compile(r'DESC\.?\s*(?:DE\s*)?R\$\s*([\d.]+,\d{2})')
_RE_MAN073 = re.compile(r'([A-Z]{3})(?:-[A-Z]{3})?\s+(\d{6}-\d)')


_RE_CIOT_ERRO = re.compile(r'MENSAGEM|PAMCAR|ERRO|<BR')
_RE_REF_DIARIA = re.compile(r'FATURAMENTO(?:\s+MENCIONADO)?\s+([A-Z]{2,3})\s*(\d[\d.\-]*)')


def ctrb_do_texto(s):
    m = _RE_CTRB.search(str(s or '').upper())
    return f'{m.group(1)}{int(m.group(2)):06d}-{m.group(3)}' if m else None


def ciot_com_erro(v):
    """O SSW grava no campo CIOT a mensagem da Pamcard/ANTT quando o CIOT não sai ("Mensagem recebida da
    Pamcard:<br />4 - RNTRC ... nao esta ativo na antt"). Esse CTRB é refeito com um CIOT válido."""
    return bool(_RE_CIOT_ERRO.search(str(v or '').upper()))


def _resumo_ciot(v):
    s = re.sub(r'<[^>]+>', ' ', str(v or ''))
    s = re.sub(r'^.*?:\s*', '', s, count=1).strip(" -[]'")
    return re.sub(r'\s+', ' ', s)[:90]


def _ref_diaria(obs):
    """'... UMA DIARIA <CLIENTE> FATURAMENTO MENCIONADO NOD 1234-5 ...' → 'NOD12345' (a viagem a que a diária se refere)."""
    m = _RE_REF_DIARIA.search(str(obs or '').upper())
    return doc(m.group(1) + m.group(2)) if m else None


def cidade_bonita(s):
    """'UBERLANDIA/MG' → 'Uberlandia/MG' (o texto do caso fica legível)."""
    s = str(s or '').strip()
    if not s:
        return '—'
    cid, _, uf = s.rpartition('/')
    if not cid:
        cid, uf = s, ''
    mins = {'DE', 'DA', 'DO', 'DAS', 'DOS', 'E'}
    nome = ' '.join(w.lower() if w in mins else w.capitalize() for w in cid.upper().split())
    return f"{nome[:1].upper()}{nome[1:]}" + (f"/{uf.upper()}" if uf else '')


def _r2(v):
    return round(n(v), 2)


def brl(v):
    return f"R$ {n(v):,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')


def estado_dinheiro(p, frota=False):
    """O estado do dinheiro de um CTRB, com as palavras certas: PAGO = saiu (liquidado no 477); PROGRAMADO =
    lançado, ainda não saiu; VOLTOU = estorno no banco ou desconto na conta corrente. (Pedido do usuário em
    16/11/2026: "pago" só para o que foi pago de fato.)"""
    if frota:
        return 'frota (sem pagamento)'
    partes = []
    if p['liquidado'] > 1:
        partes.append(f"pago {brl(p['liquidado'])}")
    if p['programado'] > 1:
        partes.append(f"programado {brl(p['programado'])}")
    volta = (p.get('estornado') or 0) + ((p.get('desconto') or {}).get('valor') or 0)
    if volta > 1:
        partes.append(f"voltou {brl(volta)}")
    if (p.get('ajuste') or 0) > 1:
        partes.append(f"saiu {brl(p['ajuste'])} a menos (ajuste no banco)")
    return ', '.join(partes) if partes else 'nada pago nem programado'


# ── a semana do saldo ────────────────────────────────────────────────────────────────────────────────
# O saldo é pago às QUINTAS (medido em 16/11/2026: 95% vencem numa quinta; 84% aprovados numa quinta e 86% pagos
# no dia da aprovação). Toda semana o financeiro aprova o que pode pagar e o resto passa para a quinta seguinte —
# o 477 guarda o vencimento ORIGINAL e a data de aprovação, e é a diferença que conta as semanas adiadas (2026:
# 53% na quinta do vencimento, 16% uma semana depois, 8% duas, 3% três). Cada mudança de data, uma por uma, o 477
# não guarda: a conferência grava em `pag_ctrb_agenda_*` a cada rodada.
QUINTA = 3


def proxima_quinta(d):
    """A quinta em que o saldo pode sair: a própria data, se for quinta; senão a quinta seguinte."""
    return d + timedelta(days=(QUINTA - d.weekday()) % 7)


def dias_adiado(venc, data, situacao, hoje, aprov=None):
    """Quantos dias o saldo andou depois do vencimento. Pago: aprovação (ou pagamento) − vencimento. Programado
    com data de hoje em diante: a data que o financeiro marcou − vencimento. Programado com a data já vencida e
    não remarcada (parado): hoje − vencimento. (O 477 NÃO remarca sozinho: medido em 16/11/2026, os parados
    seguem com a data velha — jan, mar… — e só os que alguém remarcou têm data nova.)"""
    if not venc:
        return 0
    if situacao == 'pago':
        ref = aprov or data
        return max((ref - venc).days, 0) if ref else 0
    ref = data if (data and data >= hoje) else hoje
    return max((ref - venc).days, 0)


def semanas_adiado(venc, data, situacao, hoje, aprov=None):
    """As quintas que o saldo atravessou sem sair (dias ÷ 7; o parado conta a quinta de hoje se já passou dela)."""
    d = dias_adiado(venc, data, situacao, hoje, aprov)
    if situacao != 'pago' and not (data and data >= hoje):
        return (d + 6) // 7 if d > 0 else 0
    return d // 7


# ── leitura do Power BI ──────────────────────────────────────────────────────────────────────────────
def _meses(desde, ate):
    a, m = desde.year, desde.month
    while (a, m) <= (ate.year, ate.month):
        yield a, m
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)


def _intervalo(a, m):
    a2, m2 = (a + 1, 1) if m == 12 else (a, m + 1)
    return f'DATE({a},{m},1)', f'DATE({a2},{m2},1)'


def _sel(tab, pares):
    return ', '.join(f'"{al}", {tab}[{col}]' for al, col in pares)


def coletar(dax_op, dax_cont, desde=None, hoje=None, log=print):
    """Tudo o que a conferência lê do BI, fatiado por mês (o executeQueries corta calado acima de 15 MB;
    `dax_op`/`dax_cont` levantam erro quando cortam). Leituras independentes vão em paralelo."""
    desde = desde or date.fromisoformat(DESDE)
    hoje = hoje or (datetime.utcnow() - BRT).date()
    fim = hoje + timedelta(days=40)
    OS, M, MC = "'public ctrbs_oss'", "'public manifestos'", "'public manifestos_ctrc'"
    CE, DZ, E = "'public conhecimentos_emitidos'", "'public consulta_despesas_477'", "'public extrato_bancario_456'"
    cols_os = [('ctrb', 'ctrb'), ('em', 'emissao'), ('tipo', 'Tipo Operação'), ('o', 'cidade_uf_origem'),
               ('d', 'cidade_uf_destino'), ('cav', 'placa_cavalo'), ('car', 'placa_carreta'),
               ('prop_doc', 'cnpj_cpf_proprietario'), ('prop', 'proprietario'), ('mot', 'motorista'),
               ('antt', 'tabela_antt'), ('vp', 'valor_a_pagar'), ('liq', 'valor_liquido'), ('ped', 'pedagio'),
               ('vped', 'vale_pedagio'), ('ccf', 'saldo_ccf'), ('ciot', 'ciot'), ('obs', 'observacao'),
               ('km', 'distancia_km'), ('man', 'manifesto'), ('sit', 'situacao'), ('usu', 'usuario_emissao')]
    tarefas = []
    for a, m in _meses(desde - timedelta(days=15), fim):
        i, f = _intervalo(a, m)
        tarefas.append(('ctrbs', dax_op, f"EVALUATE SELECTCOLUMNS(FILTER({OS}, {OS}[emissao] >= {i} && {OS}[emissao] < {f}), {_sel(OS, cols_os)})"))
        tarefas.append(('manifestos', dax_op, f"EVALUATE SELECTCOLUMNS(FILTER({M}, {M}[data_emissao] >= {i} && {M}[data_emissao] < {f}), "
                                              f"{_sel(M, [('man', 'CHAVE_MANIFESTO'), ('ctrb', 'CHAVE_CTRB'), ('os', 'numero_ctrb_os'), ('em', 'data_emissao')])})"))
        tarefas.append(('ponte', dax_op, f"EVALUATE SELECTCOLUMNS(FILTER({MC}, RELATED({M}[data_emissao]) >= {i} && RELATED({M}[data_emissao]) < {f}), "
                                         f"{_sel(MC, [('man', 'CHAVE_MANIFESTO'), ('cte', 'CHAVE_CTRC'), ('peso', 'peso')])})"))
    for a, m in _meses(desde - timedelta(days=45), fim):
        i, f = _intervalo(a, m)
        tarefas.append(('ctes', dax_op, f"EVALUATE SELECTCOLUMNS(FILTER({CE}, {CE}[data_emissao] >= {i} && {CE}[data_emissao] < {f}), "
                                        + _sel(CE, [('cte', 'serie_numero_ctrc'), ('em', 'data_emissao'), ('tipo', 'tipo_documento'),
                                                    ('o_cid', 'cidade_origem_prestacao'), ('o_uf', 'uf_origem_prestacao'),
                                                    ('e_cid', 'cidade_entrega'), ('e_uf', 'uf_entrega'),
                                                    ('d_cid', 'cidade_destinatario'), ('d_uf', 'uf_destinatario'),
                                                    ('fs', 'valor_frete_sem_icms'), ('pag', 'cliente_pagador'),
                                                    ('pm', 'primeiro_manifesto'), ('peso', 'peso_real_kg')]) + ')'))
    for a, m in _meses(desde - timedelta(days=31), fim):
        tarefas.append(('p477', dax_op, f"EVALUATE SELECTCOLUMNS(FILTER({DZ}, {DZ}[evento] IN {{\"5101\",\"5102\"}} && {DZ}[mes_competencia] = \"{m:02d}/{a % 100:02d}\"), "
                                        + _sel(DZ, [('uni', 'uni'), ('lanc', 'numlancto'), ('parc', 'parcela'), ('ev', 'evento'),
                                                    ('forn', 'nome_fornecedor'), ('v', 'vlr_final'), ('venc', 'vencimen'),
                                                    ('pg', 'data_pgto'), ('sit', 'sit_des'), ('h', 'historico_despesa'),
                                                    ('usu', 'liq_usu_liquid'), ('forma', 'liq_formaliquid'), ('inc', 'inclusao'),
                                                    ('aprov', 'aprovacao')]) + ')'))
    for a, m in _meses(desde, fim):
        i, f = _intervalo(a, m)
        tarefas.append(('e456', dax_cont, f"EVALUATE SELECTCOLUMNS(FILTER({E}, {E}[data] >= {i} && {E}[data] < {f} && "
                                          f"NOT({E}[transferencia_interna]) && (({E}[orig] = \"CPG\" && {E}[valor] < 0) || "
                                          f"({E}[valor] > 0 && CONTAINSSTRING({E}[historico], \"CTRB\")))), "
                                          + _sel(E, [('data', 'data'), ('conta', 'conta_apelido'), ('v', 'valor'), ('orig', 'orig'),
                                                     ('h', 'historico'), ('ru', 'ref_477_uni'), ('rl', 'ref_477_numlancto'),
                                                     ('rp', 'ref_477_parcela')]) + ')'))
    tarefas.append(('veic', dax_op, "EVALUATE SELECTCOLUMNS('public veiculos_045', \"placa\", 'public veiculos_045'[placa], "
                                    "\"prop\", 'public veiculos_045'[proprietario])"))
    tarefas.append(('rotas', dax_op, "EVALUATE SELECTCOLUMNS('public rotas_km', \"o\", 'public rotas_km'[cidade_uf_origem], "
                                     "\"d\", 'public rotas_km'[cidade_uf_destino], \"km\", 'public rotas_km'[km])"))
    def com_retentativa(fn, q):
        # O executeQueries tem teto de consultas por minuto (429): duas rodadas seguidas, ou junto dos outros
        # robôs, estouram (visto em 16/11/2026). Espera o que o serviço pedir e tenta de novo.
        for tent in range(6):
            try:
                return fn(q)
            except Exception as e:
                resp = getattr(e, 'response', None)
                if resp is None or resp.status_code != 429 or tent == 5:
                    raise
                espera = resp.headers.get('Retry-After')
                time.sleep(min(int(espera) if (espera or '').isdigit() else 15 * (tent + 1), 90))

    raw = defaultdict(list)
    with ThreadPoolExecutor(max_workers=3) as ex:
        futuros = [(nome, ex.submit(com_retentativa, fn, q)) for nome, fn, q in tarefas]
        for nome, fu in futuros:
            raw[nome].extend(fu.result())
    # CTRB emitido antes da janela que ainda tem pagamento programado (saldo segurado há meses): o 477 o traz,
    # o 073 da janela não — busca pelo número para a fila não mostrar "fora do BI" o que está no BI
    tem = {c.get('ctrb') for c in raw['ctrbs']}
    faltam = sorted({k for k in (ctrb_do_texto(p.get('h')) for p in raw['p477'] if (p.get('sit') or '').upper() != 'LIQU')
                     if k and k not in tem})
    for i in range(0, len(faltam), 150):
        lista = ', '.join(f'"{k}"' for k in faltam[i:i + 150])
        raw['ctrbs'].extend(com_retentativa(dax_op, f"EVALUATE SELECTCOLUMNS(FILTER({OS}, {OS}[ctrb] IN {{{lista}}}), {_sel(OS, cols_os)})"))
    log(f'pagamentos_ctrb: lido do BI — ' + ', '.join(f'{k} {len(v)}' for k, v in raw.items()))
    return dict(raw)


# ── evidência do Embarques (GPS) ─────────────────────────────────────────────────────────────────────
def evidencia_embarques(cur, bases):
    """{CTRB sem dígito: a carga do robô} — status, motivo de encerramento, desengate e para onde seguiu."""
    if not bases:
        return {}
    try:
        cur.execute("""SELECT c.ctrb_origem, c.numero, c.status, c.encerrada_motivo, c.no_local_desde,
                              c.data_conclusao, c.desengatada_em, x.numero, c.cavalo_placa, c.carreta1_placa
                         FROM embarques_cargas c
                         LEFT JOIN embarques_cargas x ON x.id = c.continua_em
                        WHERE NOT COALESCE(c.viagem_vazia, FALSE)
                          AND regexp_replace(upper(c.ctrb_origem), '[^A-Z0-9]', '', 'g') = ANY(%s)""",
                    (list(bases),))
    except Exception:
        cur.connection.rollback()
        return {}
    out = {}
    for r in cur.fetchall():
        out[doc(r[0])] = {'carga': r[1], 'status': r[2], 'motivo': r[3],
                          'chegada': r[4].isoformat() if r[4] else None,
                          'conclusao': r[5].isoformat() if r[5] else None,
                          'desengate': r[6].isoformat() if r[6] else None, 'continua': r[7],
                          'cavalo': r[8], 'carreta': r[9]}
    return out


# ── a análise (pura) ─────────────────────────────────────────────────────────────────────────────────
def _desmembrar(obs):
    try:
        import viagem
        segs, _, _ = viagem.desmembrar_observacao((obs or '').upper())
        return sum(s['km'] for s in segs if s['tipo'] == 'carregado')
    except Exception:
        return 0


def _tipo_estorno(h):
    h = (h or '').upper().strip()
    if h.startswith('ESTORNO'):
        t = 'Estorno'
    elif h.startswith('DUPL'):
        t = 'Duplicidade'
    elif h.startswith('DEV'):
        t = 'Devolução'
    elif h.startswith('CANC'):
        t = 'Cancelamento'
    elif 'MENOR' in h or 'FALTOU' in h:
        t = 'Ajuste'
    else:
        t = 'Outro'
    o = 'saldo' if 'SALDO' in h else ('adiantamento' if 'ADIANT' in h else
                                      ('vale-pedágio' if 'VALE-PED' in h else ('pedágio' if 'PEDAG' in h else 'outro')))
    return t, o


def analisar(raw, emb=None, hoje=None, desde=None):
    """O pacote da tela a partir do que foi lido. Puro: sem rede, sem banco."""
    emb = emb or {}
    hoje = hoje or (datetime.utcnow() - BRT).date()
    desde = desde or date.fromisoformat(DESDE)

    # CTRBs (o mesmo CTRB pode vir em duas fatias por causa da folga: fica o último lido)
    ctrb = {}
    for c in raw.get('ctrbs', []):
        if c.get('ctrb'):
            ctrb[c['ctrb']] = c
    por_base = defaultdict(list)
    for k, c in ctrb.items():
        c['_em'] = dt(c.get('em'))
        c['_base'] = base_ctrb(k)
        c['_ativo'] = (c.get('sit') or '').upper() != 'CANCELADO'
        c['_tipo'] = (c.get('tipo') or '').upper()
        c['_pago'] = c['_tipo'] != 'FROTA' and n(c.get('vp')) > 0
        c['_local'] = (c.get('o') or '') == (c.get('d') or '')
        por_base[c['_base']].append(k)

    # CTRB → manifestos → CT-e
    man_do = defaultdict(set)
    for m in raw.get('manifestos', []):
        if m.get('ctrb') and str(m.get('os') or '') != '000000':
            for k in por_base.get(doc(m['ctrb']), []):
                man_do[k].add(doc(m['man']))
    for k, c in ctrb.items():
        for sig, num in _RE_MAN073.findall(str(c.get('man') or '')):
            man_do[k].add(doc(sig + num))
    ctes_do_man = defaultdict(set)
    peso_no_man = {}
    for p in raw.get('ponte', []):
        ctes_do_man[doc(p['man'])].add(doc(p['cte']))
        peso_no_man[(doc(p['man']), doc(p['cte']))] = n(p.get('peso'))
    cte = {doc(e['cte']): e for e in raw.get('ctes', []) if e.get('cte')}
    ctes_do = {k: {ce for m in man_do.get(k, ()) for ce in ctes_do_man.get(m, ())} for k in ctrb}
    ctrbs_do_cte = defaultdict(set)
    for k, ces in ctes_do.items():
        for ce in ces:
            ctrbs_do_cte[ce].add(k)

    # rotas e veículos
    rk = {}
    for r in raw.get('rotas', []):
        rk[(r.get('o'), r.get('d'))] = n(r.get('km'))

    def km(o, d):
        if o == d:
            return 0.0
        return rk.get((o, d)) or rk.get((d, o))
    rizza = {placa(v['placa']) for v in raw.get('veic', []) if 'RIZZA' in str(v.get('prop') or '').upper()}

    # 456: saídas (pela ref do 477) e estornos (pelo nº do CTRB no histórico)
    saida = {}
    estornos = defaultdict(list)
    for x in raw.get('e456', []):
        v = n(x.get('v'))
        if v < 0 and x.get('rl'):
            saida[(str(x.get('ru') or '').strip(), str(x['rl']).strip().lstrip('0'),
                   str(x.get('rp') or '').strip().lstrip('0'))] = x
        elif v > 0:
            k = ctrb_do_texto(x.get('h'))
            if k:
                t, o = _tipo_estorno(x.get('h'))
                estornos[k].append({'data': str(x.get('data') or '')[:10], 'conta': x.get('conta'), 'valor': _r2(v),
                                    'tipo': t, 'de': o, 'historico': (x.get('h') or '')[:140]})

    # 477: os pagamentos de cada CTRB
    pag = defaultdict(list)
    for p in raw.get('p477', []):
        k = ctrb_do_texto(p.get('h'))
        if not k:
            continue
        h = (p.get('h') or '').upper()
        chave = (str(p.get('uni') or '').strip(), str(p.get('lanc') or '').strip().lstrip('0'),
                 str(p.get('parc') or '').strip().lstrip('0'))
        sx = saida.get(chave)
        liq = (p.get('sit') or '').upper() == 'LIQU'
        pg = str(p.get('pg') or '')[:10]
        if sx:
            banco = {'como': 'individual', 'conta': sx.get('conta'), 'data': str(sx.get('data') or '')[:10]}
        elif liq and (p.get('forma') or '').upper() == 'PIX' and pg >= '2026-06-01':
            banco = {'como': 'lote', 'conta': 'SICOOB', 'data': pg}
        elif liq:
            banco = {'como': 'nao_achado'}
        else:
            banco = None
        tp = 'saldo' if 'SALDO' in h else ('adiantamento' if 'ADIANT' in h else 'outro')
        venc, data_d, aprov = dia(p.get('venc')), dia(p.get('pg')), dia(p.get('aprov'))
        situ = 'pago' if liq else 'programado'
        adiado = semanas_adiado(venc, data_d, situ, hoje, aprov) if tp == 'saldo' else 0
        adiado_d = dias_adiado(venc, data_d, situ, hoje, aprov) if tp == 'saldo' else 0
        # o lote é a data que o financeiro marcou, quando ainda está por vir; data vencida e não remarcada = parado
        lote = data_d.isoformat() if (tp == 'saldo' and situ == 'programado' and data_d and data_d >= hoje) else None
        pag[k].append({'tipo': tp, 'valor': _r2(p.get('v')), 'situacao': situ,
                       'data': pg, 'venc': str(p.get('venc') or '')[:10], 'aprov': aprov.isoformat() if aprov else None,
                       'adiado': adiado, 'adiado_dias': adiado_d, 'lote': lote, 'quem': p.get('usu'),
                       'favorecido': (p.get('forn') or '').strip(), 'forma': p.get('forma'),
                       'lanc': f"{p.get('uni')}{p.get('lanc')}-{p.get('parc')}", 'banco': banco})

    def pago_477(k):
        return sum(x['valor'] for x in pag.get(k, []) if x['situacao'] == 'pago')

    def programado(k):
        return sum(x['valor'] for x in pag.get(k, []) if x['situacao'] == 'programado')

    def estornado(k):
        """O que VOLTOU: estorno, devolução, duplicidade, cancelamento."""
        return sum(e['valor'] for e in estornos.get(k, []) if e['tipo'] != 'Ajuste')

    def ajustado(k):
        """'MENOR PAGAMENTO DO ADIANTAMENTO': saiu menos do que o 477 diz — não é devolução, é o banco acertando."""
        return sum(e['valor'] for e in estornos.get(k, []) if e['tipo'] == 'Ajuste')

    # ── casos ────────────────────────────────────────────────────────────────────────────────────────
    # Revisão de 16/11/2026 ("transparecer o que realmente é"): o caso só diz "pago a mais" quando o DINHEIRO
    # comprova — a soma do que saiu nos CTRBs do caso passa do valor de UMA viagem (no hub: do devido a cada
    # um). Ficam de fora o que não é duplicidade: DIÁRIA (serviço por cliente, várias para o mesmo motorista no
    # mesmo dia) e COMPLEMENTO de frete (cita o CTRB original). Reemissão em que o adiantamento saiu pelo CTRB
    # antigo e o saldo pelo novo soma UMA viagem: não é pagamento em dobro.
    casos = {}
    for c in ctrb.values():
        obs_up = (c.get('obs') or '').upper()
        c['_diaria'] = 'DIARIA' in obs_up
        c['_compl'] = 'COMPLEMENT' in obs_up
        c['_ciot_erro'] = ciot_com_erro(c.get('ciot'))
        c['_ciot_ok'] = bool(ciot12(c.get('ciot'))) and not c['_ciot_erro']
        c['_valor'] = n(c.get('liq')) or n(c.get('vp'))      # o que UMA viagem deste CTRB vale (líquido)
        # CTRB de centavos (R$ 0,01) só formaliza a troca de cavalo — "pagamento feito no primeiro contrato"
        c['_elegivel'] = (c['_ativo'] and c['_pago'] and c['_valor'] >= 50 and not c['_diaria'] and not c['_compl'])

    def mesmo_conjunto(a, b):
        """O mesmo caminhão: cavalo ou carreta em comum. Conjuntos diferentes são duas viagens de verdade."""
        return bool((placa(a.get('cav')) and placa(a.get('cav')) == placa(b.get('cav'))) or
                    (placa(a.get('car')) and placa(a.get('car')) == placa(b.get('car'))))

    def carga_propria(k):
        """Os CT-e que são deste CTRB: os que nasceram num manifesto dele (o CT-e conta na viagem do manifesto em
        que nasceu — a mesma regra da Ficha da viagem) ou que só ele leva. Um CT-e manifestado de novo noutro
        caminhão (medido em 16/11/2026: UDI-FEC com o CT-e de outra carreta, peso cheio nos dois) não é carga."""
        mans = man_do.get(k, set())
        return {ce for ce in ctes_do.get(k, set())
                if doc((cte.get(ce) or {}).get('pm')) in mans or ctrbs_do_cte.get(ce) == {k}}

    def caso(ks, tipo, evid, cte_k=None, forte=True, nivel=None, meta=None):
        """nivel 2 = mesmo caminhão/mesma carga (o dinheiro decide se é pago a mais); 1 = conferir; 0 = explicado."""
        ks = tuple(sorted(set(ks)))
        if len(ks) < 2:
            return
        nv = nivel if nivel is not None else (2 if forte else 1)
        c = casos.setdefault('|'.join(ks), {'ctrbs': ks, 'tipos': set(), 'evid': [], 'ctes': set(), 'nivel': -1, 'meta': {}})
        c['nivel'] = max(c['nivel'], nv)
        c['tipos'].add(tipo)
        if meta:
            c['meta'].update(meta)
        for e in evid:
            if e and e not in c['evid']:
                c['evid'].append(e)
        if cte_k:
            c['ctes'].add(cte_k)

    def rot(c):
        return f"{cidade_bonita(c.get('o'))} → {cidade_bonita(c.get('d'))}"

    def horas(a, b):
        return abs((ctrb[b]['_em'] - ctrb[a]['_em']).total_seconds()) / 3600 if ctrb[a]['_em'] and ctrb[b]['_em'] else None

    def da_tempo(a, b):
        """Daria tempo de o mesmo caminhão fazer as duas viagens? Ida, volta vazio e nova ida a 60 km/h sem parar."""
        h, kk = horas(a['ctrb'], b['ctrb']), max(n(a.get('km')), n(b.get('km')))
        return h is not None and (kk == 0 or h >= 2 * kk / 60)

    for ce, ks in ctrbs_do_cte.items():
        cs = [ctrb[k] for k in ks if ctrb[k]['_ativo'] and not ctrb[k]['_diaria'] and not ctrb[k]['_compl']]
        if len(cs) < 2:
            continue
        por_leg = defaultdict(list)
        for c in cs:
            por_leg[(c.get('o'), c.get('d'))].append(c)
        for lg, lst in por_leg.items():
            pg = [c for c in lst if c['_elegivel']]
            fr = [c for c in lst if c['_tipo'] == 'FROTA']
            if lg[0] == lg[1]:      # perna local (entrega/coleta na cidade): só conta com a mesma placa
                pg = [c for c in pg if sum(1 for x in pg if placa(x.get('cav')) == placa(c.get('cav'))) >= 2]
            # o mesmo caminhão com a mesma carga na mesma rota = o dinheiro decide; caminhões diferentes são duas
            # viagens — só vira caso (para conferir) se um deles não tem carga própria no manifesto
            for i, a in enumerate(pg):
                for b in pg[i + 1:]:
                    if mesmo_conjunto(a, b):
                        caso([a['ctrb'], b['ctrb']], 'mesma_perna',
                             [f"O mesmo caminhão ({a.get('cav')}/{a.get('car') or '—'}) leva o CT-e {ce} em dois CTRBs "
                              f"na rota {rot(a)}"], ce)
                    elif not carga_propria(a['ctrb']) or not carga_propria(b['ctrb']):
                        sem = [x for x in (a, b) if not carga_propria(x['ctrb'])]
                        pesos = ', '.join(f"{x.get('cav')}: {peso_no_man.get((m, ce), 0):,.0f} kg".replace(',', '.')
                                          for x in (a, b) for m in sorted(man_do.get(x['ctrb'], ())) if (m, ce) in peso_no_man)
                        caso([a['ctrb'], b['ctrb']], 'dois_caminhoes',
                             [f"O CT-e {ce} está no manifesto de dois conjuntos diferentes em {rot(a)} ({pesos})",
                              f"{', '.join(x['ctrb'] for x in sem)} não leva CT-e próprio: só este, que nasceu no "
                              f"manifesto do outro"], ce, forte=False)
                    else:
                        dono = next((x for x in (a, b) if doc((cte.get(ce) or {}).get('pm')) in man_do.get(x['ctrb'], ())), None)
                        caso([a['ctrb'], b['ctrb']], 'dois_caminhoes',
                             [f"O CT-e {ce} aparece no manifesto de dois conjuntos diferentes em {rot(a)}",
                              (f"Ele nasceu no manifesto de {dono['ctrb']} ({dono.get('cav')}); " if dono else '')
                              + 'cada caminhão leva a própria carga — é documento repetido, não a mesma viagem lançada duas vezes'],
                             ce, nivel=0)
            if pg and fr and lg[0] != lg[1]:
                caso([c['ctrb'] for c in pg + fr], 'sobre_frota',
                     [f"O CT-e {ce} já estava no CTRB da frota {fr[0]['ctrb']} ({fr[0].get('cav')}) em {rot(fr[0])}"], ce)
        # trecho repetido: B dentro do caminho de A
        for a in cs:
            for b in cs:
                if a is b or not b['_elegivel'] or a['_local'] or b['_local']:
                    continue
                if (a.get('o'), a.get('d')) == (b.get('o'), b.get('d')):
                    continue
                ka, kb = n(a.get('km')), n(b.get('km'))
                if not ka or kb >= ka:
                    continue
                if a.get('d') == b.get('d'):
                    meio = b.get('o'); k1 = km(a.get('o'), meio)
                elif a.get('o') == b.get('o'):
                    meio = b.get('d'); k1 = km(meio, a.get('d'))
                else:
                    continue
                if k1 is None or k1 + kb > FOLGA_CAMINHO * ka:
                    continue
                if a['_tipo'] == 'FROTA':
                    caso([a['ctrb'], b['ctrb']], 'sobre_frota',
                         [f"A frota ({a['ctrb']}) levou {rot(a)} e o trecho {rot(b)} ganhou CTRB de agregado/terceiro ({b['ctrb']})"], ce)
                    continue
                if not a['_elegivel']:
                    continue
                kd = _desmembrar(a.get('obs'))
                if kd and kd < 0.85 * ka and abs(kd - k1) <= 0.15 * max(k1, 1):
                    continue        # A foi lançado só até o ponto de troca: está certo
                decl = (f"{a['ctrb']} declara {kd:,.0f} km carregado na observação (até o destino final)".replace(',', '.')
                        if kd else f"{a['ctrb']} não declara o km na observação; o CTRB vai até o destino final")
                junto = mesmo_conjunto(a, b)
                caso([a['ctrb'], b['ctrb']], 'trecho_hub', [decl]
                     + ([] if junto else ['Os dois são de conjuntos diferentes (nem cavalo nem carreta em comum) — '
                                          'pode ser transbordo de parte da carga']), ce, forte=junto,
                     meta={'a': a['ctrb'], 'b': b['ctrb'], 'k1': k1, 'meio': meio})

    # mesma placa + mesma rota em pouco tempo, mesmo sem o CT-e ligado: reemissão, erro no CIOT ou duas viagens
    por_cav = defaultdict(list)
    for c in ctrb.values():
        if c['_elegivel'] and placa(c.get('cav')) and c['_em']:
            por_cav[placa(c['cav'])].append(c)
    for lst in por_cav.values():
        lst.sort(key=lambda c: c['_em'])
        for i, a in enumerate(lst):
            for b in lst[i + 1:]:
                if (b['_em'] - a['_em']).total_seconds() > JANELA_DUPLICADO_H * 3600:
                    break
                if (a.get('o'), a.get('d')) != (b.get('o'), b.get('d')):
                    continue
                m = _RE_REEMITIDO.search((a.get('obs') or '').upper())
                novo = f'{m.group(1)}{int(m.group(2)):06d}-{m.group(3)}' if m else None
                tempo = da_tempo(a, b)
                if a['_ciot_erro'] and b['_ciot_ok']:
                    caso([a['ctrb'], b['ctrb']], 'ciot_substituido',
                         [f"{a['ctrb']} saiu com erro no CIOT (\"{_resumo_ciot(a.get('ciot'))}\"); {b['ctrb']} tem CIOT válido"],
                         meta={'velho': a['ctrb'], 'novo': b['ctrb']})
                elif novo == b['ctrb']:
                    caso([a['ctrb'], b['ctrb']], 'reemissao',
                         [f"{a['ctrb']} diz \"baixado pela emissão de novo CTRB: {b['ctrb']}\""] +
                         (["Daria tempo de terem sido duas viagens — o SSW escreve a mesma frase quando só encerra o CTRB "
                           "anterior ao emitir o da viagem seguinte"] if tempo else []),
                         forte=not tempo, meta={'velho': a['ctrb'], 'novo': b['ctrb']})
                elif placa(a.get('car')) == placa(b.get('car')):
                    caso([a['ctrb'], b['ctrb']], 'mesma_perna',
                         [f"Mesmo cavalo e carreta ({a.get('cav')}/{a.get('car') or '—'}), mesma rota {rot(a)}"] +
                         (["Daria tempo de terem sido duas viagens"] if tempo else ["Não dá tempo de terem sido duas viagens"]),
                         forte=not tempo)
    # a mesma diária lançada duas vezes: mesmo cavalo, mesma referência de faturamento, mesmo valor
    por_diaria = defaultdict(list)
    for c in ctrb.values():
        if c['_ativo'] and c['_diaria'] and n(c.get('vp')) > 0 and placa(c.get('cav')):
            ref = _ref_diaria(c.get('obs'))
            if ref:
                por_diaria[(placa(c['cav']), ref, round(n(c.get('vp')), 2))].append(c)
    for (_, ref, _), lst in por_diaria.items():
        if len(lst) >= 2:
            caso([c['ctrb'] for c in lst], 'diaria_repetida',
                 [f"A diária do faturamento {ref} aparece em {len(lst)} CTRBs, mesmo motorista e mesmo valor"],
                 meta={'ref': ref})
    # mesmo CIOT — só no carreteiro: o agregado (TAC) tem CIOT por PERÍODO, o mesmo para várias viagens
    por_ciot = defaultdict(list)
    for c in ctrb.values():
        if c['_ativo'] and c['_ciot_ok']:
            por_ciot[ciot12(c['ciot'])].append(c)
    for ci, lst in por_ciot.items():
        car = [c for c in lst if c['_elegivel'] and c['_tipo'] == 'CARRETEIRO']
        if len(car) >= 2:
            caso([c['ctrb'] for c in car], 'mesmo_ciot', [f"Os CTRBs de carreteiro têm o mesmo CIOT {ci} (CIOT de carreteiro é por viagem)"])
        for c in lst:
            for x in lst:
                if c is not x:
                    ch = '|'.join(sorted((c['ctrb'], x['ctrb'])))
                    if ch in casos:
                        casos[ch]['evid'].append(f"Os dois têm o mesmo CIOT {ci}")

    # desconto em conta corrente (provável): CTRB seguinte do mesmo proprietário com o valor parecido
    por_prop = defaultdict(list)
    for c in ctrb.values():
        if doc(c.get('prop_doc')) and c['_em']:
            por_prop[doc(c['prop_doc'])].append(c)

    def desconto_provavel(k, valor):
        c = ctrb[k]
        for x in sorted(por_prop.get(doc(c.get('prop_doc')), []), key=lambda x: x['_em']):
            if x['_em'] <= c['_em'] or (x['_em'] - c['_em']).days > DIAS_DESCONTO:
                continue
            d = -n(x.get('ccf'))
            mm = _RE_DESC.search((x.get('obs') or '').upper())
            dv = float(mm.group(1).replace('.', '').replace(',', '.')) if mm else 0.0
            if d > 0 and abs(d - valor) <= 0.1 * valor:
                return {'ctrb': x['ctrb'], 'valor': _r2(d), 'como': 'conta corrente (CCF)', 'data': str(x['_em'])[:10]}
            if dv and abs(dv - valor) <= 0.1 * valor:
                return {'ctrb': x['ctrb'], 'valor': _r2(dv), 'como': 'desconto na observação', 'data': str(x['_em'])[:10]}
        return None

    def regua(ks, ce):
        e = cte.get(ce or '', {})
        O = f"{e.get('o_cid')}/{e.get('o_uf')}" if e.get('o_cid') else None
        D = f"{e.get('e_cid') or e.get('d_cid')}/{e.get('e_uf') or e.get('d_uf')}" if (e.get('e_cid') or e.get('d_cid')) else None
        legs = [ctrb[k] for k in ks]
        longa = max(legs, key=lambda c: n(c.get('km')))
        if not O or not D or D.startswith('None') or O == D:
            # CT-e sem cidade, ou de subcontratação (série UDS: origem = destino = Uberlândia): as pontas
            # do CT-e não descrevem a viagem — a régua usa a perna mais longa
            O, D = longa.get('o'), longa.get('d')
        pos = []
        for c in legs:
            kk = n(c.get('km'))
            ini = km(O, c.get('o'))
            pos.append([c, ini, kk])
        total = max([(p[1] or 0) + p[2] for p in pos] + [1.0])
        out = []
        for c, ini, kk in pos:
            if ini is None:        # sem km até a origem: alinha pelo destino quando bate, senão começa no zero
                fim_ref = max((p[1] or 0) + p[2] for p in pos if p[0].get('d') == c.get('d')) if any(
                    p[1] is not None for p in pos if p[0].get('d') == c.get('d')) else total
                ini = max(fim_ref - kk, 0) if c.get('d') == (D or c.get('d')) else 0
            fim = ini + (kk if kk else total * 0.02)
            out.append({'ctrb': c['ctrb'], 'ini': round(min(ini / total, 1), 4), 'fim': round(min(fim / total, 1), 4),
                        'o': c.get('o'), 'd': c.get('d'), 'km': kk, 'tipo': c['_tipo'], 'pago': c['_pago'],
                        'cav': c.get('cav'), 'car': c.get('car')})
        out.sort(key=lambda r: (r['ini'], -r['fim']))
        # trechos cobertos por 2+ CTRBs pagos
        cortes = sorted({r['ini'] for r in out} | {r['fim'] for r in out})
        sob = []
        for a, b in zip(cortes, cortes[1:]):
            if b - a < 1e-4:
                continue
            q = sum(1 for r in out if (r['pago'] or r['tipo'] == 'FROTA') and r['ini'] <= a + 1e-6 and r['fim'] >= b - 1e-6)
            if q >= 2:
                if sob and abs(sob[-1][1] - a) < 1e-6:
                    sob[-1][1] = b
                else:
                    sob.append([a, b])
        return {'origem': O, 'destino': D, 'km_total': round(total), 'pernas': out, 'sobreposto': sob}

    def contexto(ks):
        """O quadro que decide o caso: CTRB → manifestos → CT-e, com o peso no manifesto, onde o CT-e nasceu e em
        que outro CTRB do caso ele também está. É o que separa carga dividida de documento repetido."""
        out = []
        for k in ks:
            c = ctrb[k]
            proprios = carga_propria(k)
            mans = []
            for m in sorted(man_do.get(k, ())):
                todos = sorted(ctes_do_man.get(m, ()))
                itens = []
                for ce in todos[:12]:
                    e = cte.get(ce) or {}
                    itens.append({'cte': ce, 'peso': _r2(peso_no_man.get((m, ce))), 'fs': _r2(e.get('fs')),
                                  'nasceu_aqui': doc(e.get('pm')) == m, 'proprio': ce in proprios,
                                  'pm': e.get('pm'), 'tambem_em': sorted(x for x in ctrbs_do_cte.get(ce, ()) if x != k and x in ks)})
                mans.append({'man': m, 'ctes': itens, 'n': len(todos),
                             'peso': _r2(sum(peso_no_man.get((m, ce), 0) for ce in todos))})
            vistos, peso_prop = set(), 0.0
            for m in man_do.get(k, ()):
                for ce in ctes_do_man.get(m, ()):
                    if ce in proprios and ce not in vistos:
                        vistos.add(ce)
                        peso_prop += peso_no_man.get((m, ce), 0)
            out.append({'ctrb': k, 'cav': c.get('cav'), 'car': c.get('car'), 'tipo': c['_tipo'], 'manifestos': mans,
                        'n_proprio': len(proprios), 'peso_proprio': _r2(peso_prop)})
        return out

    def programados_txt(k):
        """'saldo R$ 500,00 (para 19/11) e adiantamento R$ 2.000,00 (parado desde 30/05)' — o que está no 477 sem sair."""
        itens = []
        for p in pag.get(k, []):
            if p['situacao'] != 'programado':
                continue
            d = p.get('lote') or p.get('data')
            quando = ((f"para {d[8:10]}/{d[5:7]}" if d >= hoje.isoformat() else f"parado desde {d[8:10]}/{d[5:7]}")
                      if d else '')
            itens.append(f"{p['tipo']} {brl(p['valor'])}" + (f" ({quando})" if quando else ''))
        return ' e '.join(itens)

    def distribuir(ordem, total, por):
        """Quanto segurar em cada CTRB: na ordem dada, até somar o total."""
        hold, resta = {}, total
        for k in ordem:
            h = min(por[k]['programado'], resta)
            if h > 0.5:
                hold[k] = _r2(h)
                resta -= h
        return hold

    def narrar(tipo, status, ks, por, conta, meta, forte, hold, ce):
        """'O que aconteceu' (frases curtas, só fatos) e 'O que fazer' (a ação, com CTRB e valor)."""
        nf = [k for k in ks if ctrb[k]['_tipo'] != 'FROTA']
        conj = lambda k: f"{ctrb[k].get('cav') or '—'}" + (f"/{ctrb[k].get('car')}" if ctrb[k].get('car') else '')
        gap = horas(ks[0], ks[-1])
        gap_txt = ('' if gap is None else (f"{gap * 60:.0f} min" if gap < 1 else
                   (f"{gap:.0f} h" if gap < 48 else f"{gap / 24:.0f} dias")))
        tempo = da_tempo(ctrb[ks[0]], ctrb[ks[-1]])
        velho, novo = meta.get('velho'), meta.get('novo')
        a, b = meta.get('a'), meta.get('b')
        fatos = []
        if tipo in ('ciot_substituido', 'reemissao') and velho:
            if tipo == 'ciot_substituido':
                fatos.append(f"O CTRB {velho} saiu com erro no CIOT e foi refeito como {novo}, {gap_txt} depois — mesmo "
                             f"caminhão ({conj(velho)}), mesma rota ({rot(ctrb[velho])}).")
            else:
                fatos.append(f"O CTRB {velho} foi baixado pela emissão do {novo}, {gap_txt} depois — mesmo caminhão "
                             f"({conj(velho)}), mesma rota ({rot(ctrb[velho])}).")
                if tempo:
                    fatos.append('Daria tempo de terem sido duas viagens: o SSW escreve "baixado pela emissão de novo '
                                 'CTRB" também quando só encerra o CTRB anterior ao emitir o da viagem seguinte.')
        elif tipo == 'trecho_hub' and a:
            A, B = ctrb[a], ctrb[b]
            fatos.append(f"O CTRB {a} ({conj(a)}) foi lançado de {cidade_bonita(A.get('o'))} até "
                         f"{cidade_bonita(A.get('d'))} ({n(A.get('km')):,.0f} km).".replace(',', '.'))
            fatos.append(f"O CTRB {b} ({conj(b)}) foi lançado de {cidade_bonita(B.get('o'))} até "
                         f"{cidade_bonita(B.get('d'))} ({n(B.get('km')):,.0f} km) — esse trecho também está dentro do "
                         f"primeiro.".replace(',', '.'))
            fatos.append('É a mesma carreta: ela trocou de cavalo em ' + cidade_bonita(meta.get('meio')) +
                         ', mas o primeiro CTRB foi feito até o fim.' if forte else
                         'São conjuntos diferentes: pode ter havido transbordo de parte da carga.')
        elif tipo == 'mesma_perna':
            fatos.append(f"O mesmo caminhão ({conj(ks[0])}) está em {len(ks)} CTRBs na rota {rot(ctrb[ks[0]])}"
                         + (f", com {gap_txt} de diferença" if gap_txt else '')
                         + (f" e o mesmo CT-e {ce} nos manifestos" if ce and forte else '') + '.')
            if tempo and forte:
                fatos.append('Daria tempo de ir e voltar, mas a carga (o CT-e) é a mesma — só se explica por reentrega.')
            elif tempo:
                fatos.append('Daria tempo de terem sido duas viagens de verdade.')
        elif tipo == 'dois_caminhoes':
            fatos.append(f"Dois caminhões diferentes ({conj(ks[0])} e {conj(ks[-1])}) estão com o mesmo CT-e na rota "
                         f"{rot(ctrb[ks[0]])}.")
            if status == 'explicado':
                fatos.append('Cada um leva a própria carga; o CT-e de um deles só foi manifestado também no outro.')
        elif tipo == 'sobre_frota':
            fr = [k for k in ks if ctrb[k]['_tipo'] == 'FROTA']
            fatos.append(f"A carga estava num CTRB da frota ({', '.join(fr)}) e também num CTRB de agregado/terceiro "
                         f"({', '.join(nf)}).")
            if status == 'explicado':
                fatos.append('O GPS mostra que a frota levou a carreta até o ponto de troca e o agregado/terceiro '
                             'seguiu com ela.')
        elif tipo == 'mesmo_ciot':
            fatos.append(f"Os CTRBs {', '.join(ks)} de carreteiro têm o mesmo CIOT — o CIOT de carreteiro é de uma "
                         f"viagem só.")
        elif tipo == 'diaria_repetida':
            fatos.append(f"A mesma diária (referente ao faturamento {meta.get('ref') or '—'}) foi lançada em {len(ks)} CTRBs, "
                         f"para o mesmo motorista e com o mesmo valor.")
        fatos.append('Dinheiro: ' + ' · '.join(f"{k}: {estado_dinheiro(por[k], ctrb[k]['_tipo'] == 'FROTA')}"
                                               for k in ks) + '.')
        if conta.get('devido') is not None and status != 'um_recebeu':
            if tipo == 'trecho_hub' and meta.get('parte_a') is not None:
                ka = n(ctrb[a].get('km'))
                if meta['k1'] >= 0.95 * ka:
                    parte = f"o {a} inteiro ({brl(meta['parte_a'])}: {cidade_bonita(meta.get('meio'))} fica no fim do trajeto)"
                else:
                    kms = f"{meta['k1']:,.0f} de {ka:,.0f} km".replace(',', '.')
                    parte = (f"o {a} só até {cidade_bonita(meta.get('meio'))} ({kms} ≈ {brl(meta['parte_a'])}, "
                             f"proporcional ao km)")
                fatos.append(f"Devido: {parte} + o {b} {brl(ctrb[b]['_valor'])} = {brl(conta['devido'])}.")
            else:
                fatos.append(f"Devido: {'uma diária' if tipo == 'diaria_repetida' else 'uma viagem'}, {brl(conta['devido'])}"
                             + (f" — o valor do {novo}, o CTRB que vale" if tipo == 'ciot_substituido' else '') + '.')

        # ── o que fazer ──
        quem = conta.get('quem')
        dono = (ctrb[quem].get('prop') if quem else None) or 'o proprietário'
        liberar = {k: _r2(por[k]['programado'] - hold.get(k, 0)) for k in nf if por[k]['programado'] - hold.get(k, 0) > 1}

        def lista_hold():
            return '; '.join((f"{k} — {programados_txt(k)}" if h >= por[k]['programado'] - 1 else
                              f"{k} — {brl(h)} de {programados_txt(k)}") for k, h in hold.items())

        def lista_liberar():
            return '; '.join(f"{k} {brl(v)}" for k, v in liberar.items())

        pagos_nf = [k for k in nf if por[k]['liquido'] > 1]
        if status == 'pendente':
            if conta['pago'] >= conta['devido'] - 50:
                lead = (f"A viagem ({brl(conta['devido'])}) já saiu inteira"
                        + (' somando os CTRBs' if len(pagos_nf) >= 2 else (f" pelo {pagos_nf[0]}" if pagos_nf else ''))
                        + '. ')
            else:
                lead = f"Já saiu {brl(conta['pago'])} de {brl(conta['devido'])}. "
            if velho and velho in hold:
                lead += f"O {velho} não vale: foi refeito como {novo}. "
            elif tipo == 'trecho_hub' and a:
                lead += f"O {a} foi lançado até o fim, mas a carga trocou de cavalo em {cidade_bonita(meta.get('meio'))}. "
            verbo = 'Cancelar' if (velho in hold and len(hold) == 1) else 'Segurar'
            fazer = lead + f"{verbo} no 477: {lista_hold()}."
            if tipo == 'trecho_hub' and b in hold:
                fazer += f" O {b} é devido, mas é o mesmo proprietário: segurar desconta o que ele já recebeu a mais."
            if liberar:
                fazer += f" Pode sair (é devido): {lista_liberar()}."
            if conta['cobrar'] > 50:
                fazer += f" Mesmo assim ficam {brl(conta['cobrar'])} pagos a mais — cobrar ou descontar de {dono}."
        elif status == 'pago_2x':
            fazer = f"Saíram {brl(conta['cobrar'])} a mais. Cobrar ou descontar de {dono} (próximo CTRB ou conta corrente)."
            if tipo == 'trecho_hub' and a:
                fazer += (f" O {a} foi pago pelo trajeto inteiro, mas a carga trocou de cavalo em "
                          f"{cidade_bonita(meta.get('meio'))}.")
            if liberar:
                fazer += f" O que ainda está programado é devido: {lista_liberar()}."
        elif status == 'conferir':
            o_que = {'trecho_hub': f"se houve transbordo de parte da carga em {cidade_bonita(meta.get('meio'))}",
                     'dois_caminhoes': 'se os dois caminhões levaram carga',
                     'sobre_frota': 'se o agregado/terceiro fez mesmo a viagem',
                     'reemissao': 'se foram duas viagens',
                     }.get(tipo, 'se foram duas viagens de verdade')
            acoes = []
            if hold:
                acoes.append(f"segurar no 477: {lista_hold()}")
            if conta['cobrar'] > 50:
                acoes.append(f"cobrar {brl(conta['cobrar'])} de {dono}")
            fazer = f"Confirmar com o operacional {o_que}." + (f" Se não, {' e '.join(acoes)}." if acoes else '')
        elif status == 'recuperado':
            fazer = f"Nada a fazer: {brl(conta['voltou'])} já voltou (estorno no banco ou desconto na conta corrente)."
        elif status == 'um_recebeu':
            com = [k for k in nf if por[k]['liquidado'] > 1 or por[k]['programado'] > 1]
            sem = [k for k in nf if k not in com]
            if velho and velho in sem:
                fazer = f"Nada a fazer: o {velho} foi refeito e não tem pagamento; só o {novo} recebe."
            elif com and sem:
                fazer = (f"Nada a fazer agora: só o {', '.join(com)} tem pagamento no 477. Atenção: se o "
                         f"{', '.join(sem)} for lançado para pagamento, o mesmo trajeto sai duas vezes.")
            else:
                fazer = 'Nada a fazer: nenhum dos CTRBs tem pagamento no 477.'
        elif conta.get('motivo') == 'soma_ok' and tipo == 'trecho_hub':
            fazer = 'Nada a fazer: somando os dois, não passa do devido — cada um recebe a sua parte do trajeto.'
        elif conta.get('motivo') == 'soma_ok':
            fazer = ('Nada a fazer: somando os CTRBs, saiu o valor de uma viagem só — o pagamento foi dividido entre eles '
                     '(por exemplo, o adiantamento por um e o saldo pelo outro).')
        else:
            fazer = 'Nada a fazer: não é pagamento em dobro.'
        return fatos, fazer

    lista_casos = []
    for chave, c in casos.items():
        ks = sorted(c['ctrbs'], key=lambda k: (ctrb[k]['_em'] or datetime.min, k))
        por = {}
        for k in ks:
            liq, est, aj = pago_477(k), estornado(k), ajustado(k)
            por[k] = {'liquidado': _r2(liq), 'programado': _r2(programado(k)), 'estornado': _r2(est),
                      'ajuste': _r2(aj), 'desconto': None, 'liquido': _r2(liq - est - aj), 'segurar': 0.0}
        tipo = next(t for t in PRIORIDADE_TIPO if t in c['tipos'])
        meta = dict(c['meta'])
        forte = c['nivel'] == 2
        nf = [k for k in ks if ctrb[k]['_tipo'] != 'FROTA']
        # só conta no devido quem tem lançamento no 477: CTRB sem pagamento nenhum não deve nada a ninguém (ainda)
        com_din = [k for k in nf if por[k]['liquidado'] > 1 or por[k]['programado'] > 1]
        a, b = meta.get('a'), meta.get('b')
        velho = meta.get('velho') if tipo in ('ciot_substituido', 'reemissao') else None
        hub = bool(tipo == 'trecho_hub' and a in com_din and b in com_din and n(ctrb[a].get('km')))
        # o DEVIDO: uma viagem (o maior valor); no hub, a parte de A até o ponto de troca (proporcional ao km) + B;
        # no CTRB refeito por erro de CIOT, o valor do novo (o antigo nunca vale)
        if hub:
            meta['parte_a'] = _r2(ctrb[a]['_valor'] * min(meta['k1'] / n(ctrb[a].get('km')), 1))
            devido = meta['parte_a'] + ctrb[b]['_valor']
        elif velho and meta.get('novo') in ctrb:
            devido = ctrb[meta['novo']]['_valor']       # refeito: só o novo vale
        else:
            devido = max((ctrb[k]['_valor'] for k in com_din), default=0.0)
        pago = sum(por[k]['liquido'] for k in nf)
        if pago - devido > 50 and len(com_din) >= 2:      # desconto em conta corrente que bate com o valor a mais
            for k in sorted(nf, key=lambda k: por[k]['liquido']):
                dsc = desconto_provavel(k, pago - devido)
                if dsc:
                    por[k]['desconto'] = dsc
                    por[k]['liquido'] = _r2(por[k]['liquido'] - dsc['valor'])
                    pago -= dsc['valor']
                    break
        bruto = sum(por[k]['liquidado'] - por[k]['ajuste'] for k in nf)
        prog = sum(por[k]['programado'] for k in nf)
        vai_passar = pago + prog - devido
        segurar = max(0.0, min(prog, vai_passar - max(pago - devido, 0.0)))
        # QUEM segurar: o antigo refeito primeiro; no hub, o que foi lançado até o fim (o B fez o trecho dele —
        # só entra se for o mesmo proprietário); senão, o que recebeu menos (o lançamento que completa a viagem sai)
        if hub:
            pa, pb = doc(ctrb[a].get('prop_doc')), doc(ctrb[b].get('prop_doc'))
            ordem = [a] + ([b] if pa and pa == pb else [])
        else:
            resto = sorted(reversed([k for k in nf if k != velho]), key=lambda k: por[k]['liquido'])
            ordem = ([velho] if velho in nf else []) + resto
        hold = distribuir(ordem, segurar, por)
        if velho in nf and por[velho]['programado'] > 1 and (forte or tipo == 'ciot_substituido'):
            hold[velho] = por[velho]['programado']      # o CTRB refeito não vale: cancela tudo o que tem programado
        seg_tot = sum(hold.values())
        cobrar = max(0.0, pago + prog - seg_tot - devido)
        voltou = max(0.0, min(bruto - devido, bruto - pago))
        quem = a if hub else (velho if velho and por[velho]['liquido'] > 1 else
                              max(nf, key=lambda k: por[k]['liquido']) if nf else None)
        so_frota = c['tipos'] == {'sobre_frota'}
        if so_frota:
            # O CTRB da frota não gera pagamento: o que se confere é se o agregado/terceiro fez mesmo a viagem —
            # e o GPS responde quando a frota largou a carreta para ele seguir.
            frota_passou = any(ctrb[k]['_tipo'] == 'FROTA' and (emb.get(ctrb[k]['_base']) or {}).get('continua')
                               for k in ks)
            hold = {k: por[k]['programado'] for k in nf if por[k]['programado'] > 1}
            seg_tot, cobrar = sum(hold.values()), sum(max(por[k]['liquido'], 0) for k in nf)
            devido, quem = 0.0, (nf[0] if nf else None)
            if frota_passou or seg_tot + cobrar <= 1:
                status, excesso, hold, seg_tot, cobrar = 'explicado', 0.0, {}, 0.0, 0.0
            else:
                status, excesso = 'conferir', seg_tot + cobrar
        elif len(com_din) < 2:
            status, excesso, hold, seg_tot, cobrar = 'um_recebeu', 0.0, {}, 0.0, 0.0
        elif c['nivel'] == 0:
            status, excesso, hold, seg_tot, cobrar = 'explicado', 0.0, {}, 0.0, 0.0
        elif seg_tot > 50:
            status, excesso = ('pendente' if forte else 'conferir'), seg_tot
        elif cobrar > 50:
            status, excesso = ('pago_2x' if forte else 'conferir'), cobrar
        elif voltou > 50:
            status, excesso = 'recuperado', 0.0
        else:
            status, excesso = 'explicado', 0.0
        if status == 'pendente':
            for k, h in hold.items():
                por[k]['segurar'] = h
        conta = {'devido': _r2(devido), 'pago': _r2(pago), 'programado': _r2(prog), 'pago_a_mais': _r2(pago - devido),
                 'vai_passar': _r2(vai_passar), 'segurar': _r2(seg_tot), 'cobrar': _r2(cobrar), 'voltou': _r2(voltou),
                 'quem': quem, 'motivo': 'soma_ok' if (status == 'explicado' and c['nivel'] != 0 and not so_frota) else None}
        if so_frota or (status == 'explicado' and c['nivel'] == 0):
            conta.update(devido=None, pago_a_mais=None, vai_passar=None)   # cada um tem a sua viagem: não há "uma viagem" 
        ce = (sorted(c['ctes'])[0] if c['ctes'] else
              next(iter(sorted(set().union(*[ctes_do.get(k, set()) for k in ks]))), None))
        evid = list(c['evid'])
        for k in ks:
            ev = emb.get(ctrb[k]['_base'])
            if ev and (ev.get('continua') or ev.get('status') in ('Desengatada', 'Cancelada', 'Continuada')):
                txt = f"GPS: a carga {ev['carga']} de {k} ficou {ev['status']}"
                if ev.get('motivo'):
                    txt += f" ({ev['motivo'].replace('_', ' ')})"
                if ev.get('continua'):
                    txt += f" e seguiu na {ev['continua']}"
                evid.append(txt)
        fatos, fazer = narrar(tipo, status, ks, por, conta, meta, forte, hold, ce)
        datas = [ctrb[k]['_em'] for k in ks if ctrb[k]['_em']]
        rg = regua(ks, ce)
        if status in ('explicado', 'recuperado', 'um_recebeu'):
            rg['sobreposto'] = []          # não há trecho pago duas vezes para pintar
        lista_casos.append({
            'chave': '|'.join(sorted(ks)), 'tipo': tipo, 'tipo_texto': TIPOS_CASO[tipo], 'explica': EXPLICA_CASO[tipo],
            'tipos': sorted(c['tipos'], key=PRIORIDADE_TIPO.index), 'status': status,
            'status_texto': STATUS_CASO[status], 'excesso': _r2(excesso), 'conta': conta, 'aconteceu': fatos,
            'fazer': fazer, 'forte': forte, 'ctrbs': list(ks), 'cte': ce, 'por_ctrb': por,
            'evidencias': list(dict.fromkeys(evid))[:8], 'meta': meta,
            'voltou': conta['voltou'] if status in ('pago_2x', 'recuperado', 'pendente') else 0.0,
            'emissao': min(datas).isoformat() if datas else None,
            'regua': rg, 'contexto': contexto(ks),
        })
    lista_casos.sort(key=lambda x: (ORDEM_STATUS[x['status']], -(x['excesso'] or 0), x['emissao'] or ''))
    casos_do = defaultdict(list)
    for cs in lista_casos:
        for k in cs['ctrbs']:
            casos_do[k].append(cs['chave'])

    # ── sinais por CTRB (sem as decisões: elas entram na leitura) ─────────────────────────────────────
    def receita_de(k):
        ces = ctes_do.get(k, set())
        so_meus = [ce for ce in ces if ctrbs_do_cte.get(ce) == {k}]
        return sum(n(cte[ce].get('fs')) for ce in so_meus if ce in cte), len(so_meus) == len(ces) and ces

    sinais = {}
    for k, c in ctrb.items():
        s = []
        if not c['_ativo'] and programado(k) > 1:
            s.append({'nivel': 'vermelho', 'cod': 'cancelado', 'texto': 'CTRB cancelado no SSW com pagamento programado'})
        if c['_pago'] and c['_ativo']:
            if not man_do.get(k):
                if n(c.get('antt')) == 0 and c['_local']:
                    s.append({'nivel': 'info', 'cod': 'diaria', 'texto': 'Sem manifesto — tem cara de diária (sem tabela ANTT e origem = destino)'})
                else:
                    s.append({'nivel': 'amarelo', 'cod': 'sem_receita', 'texto': 'Sem manifesto: o pagamento não se liga a CT-e nenhum'})
            elif not ctes_do.get(k):
                s.append({'nivel': 'amarelo', 'cod': 'sem_receita', 'texto': 'O manifesto não tem CT-e: pagamento sem receita'})
            if placa(c.get('cav')) in rizza and placa(c.get('car')) and placa(c.get('car')) in rizza:
                s.append({'nivel': 'amarelo', 'cod': 'frota_rizza',
                          'texto': f"Cavalo e carreta são da Rizza no cadastro, mas o CTRB é de {c['_tipo'].lower()}"})
            rec, todos = receita_de(k)
            if todos and rec > 0 and n(c.get('vp')) > rec:
                s.append({'nivel': 'amarelo', 'cod': 'acima_receita',
                          'texto': f"O CTRB vale R$ {n(c.get('vp')):,.2f} e a receita dos CT-e é R$ {rec:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')})
        sinais[k] = s

    # ── fila de pagamento (o que está programado no 477) ─────────────────────────────────────────────
    fila = []
    for k, ps in pag.items():
        for p in ps:
            if p['situacao'] != 'programado':
                continue
            c = ctrb.get(k)
            fila.append({'ctrb': k, 'tipo_pag': p['tipo'], 'valor': p['valor'], 'data': p['data'], 'venc': p['venc'],
                         'aprov': p['aprov'], 'adiado': p['adiado'], 'adiado_dias': p['adiado_dias'], 'lote': p['lote'],
                         'favorecido': p['favorecido'], 'lanc': p['lanc'], 'no_bi': bool(c),
                         'tipo': c['_tipo'] if c else None, 'o': c.get('o') if c else None, 'd': c.get('d') if c else None,
                         'cav': c.get('cav') if c else None, 'car': c.get('car') if c else None,
                         'emissao': str(c['_em'])[:16] if c and c['_em'] else None,
                         'usu': c.get('usu') if c else None})
    fila.sort(key=lambda f: (f['data'] or '9999', -f['valor']))

    # ── estornos (456) ───────────────────────────────────────────────────────────────────────────────
    lista_est = []
    for k, es in estornos.items():
        favs = Counter(p['favorecido'] for p in pag.get(k, []) if p['favorecido'])
        for e in es:
            lista_est.append({**e, 'ctrb': k, 'favorecido': favs.most_common(1)[0][0] if favs else
                              ((ctrb.get(k) or {}).get('prop') or ''), 'pago_477': _r2(pago_477(k)),
                              'no_bi': k in ctrb, 'casos': casos_do.get(k, [])})
    lista_est.sort(key=lambda e: e['data'], reverse=True)

    # ── controles ────────────────────────────────────────────────────────────────────────────────────
    emissor = defaultdict(lambda: {'ctrbs': 0, 'adiant': 0, 'adiant_proprio': 0, 'valor_proprio': 0.0})
    for k, c in ctrb.items():
        u = (c.get('usu') or '').strip().lower()
        if not u or not c['_em'] or c['_em'].date() < desde:
            continue
        emissor[u]['ctrbs'] += 1
        for p in pag.get(k, []):
            if p['tipo'] == 'adiantamento' and p['situacao'] == 'pago':
                emissor[u]['adiant'] += 1
                if (p['quem'] or '').strip().lower() == u:
                    emissor[u]['adiant_proprio'] += 1
                    emissor[u]['valor_proprio'] += p['valor']
    segreg = sorted(({'usuario': u, **{kk: (round(vv, 2) if isinstance(vv, float) else vv) for kk, vv in v.items()}}
                     for u, v in emissor.items()), key=lambda r: -r['ctrbs'])
    sem_receita = [k for k, s in sinais.items() if any(x['cod'] == 'sem_receita' for x in s)
                   and ctrb[k]['_em'] and ctrb[k]['_em'].date() >= desde]
    frota_paga = [k for k, s in sinais.items() if any(x['cod'] == 'frota_rizza' for x in s)
                  and ctrb[k]['_em'] and ctrb[k]['_em'].date() >= desde]

    # ── os CTRBs compactos (dossiê e busca) ──────────────────────────────────────────────────────────
    ctrbs_out = {}
    ctes_usados = set()
    for k, c in ctrb.items():
        ces = sorted(ctes_do.get(k, set()))
        ctes_usados.update(ces)
        ctrbs_out[k] = {
            'ctrb': k, 'emissao': c['_em'].isoformat() if c['_em'] else None, 'tipo': c['_tipo'],
            'situacao': c.get('sit') or None, 'ativo': c['_ativo'], 'o': c.get('o'), 'd': c.get('d'), 'km': n(c.get('km')),
            'cav': c.get('cav'), 'car': c.get('car'), 'prop': c.get('prop'), 'prop_doc': c.get('prop_doc'),
            'mot': c.get('mot'), 'valor_a_pagar': _r2(c.get('vp')), 'liquido': _r2(c.get('liq')),
            'pedagio': _r2(c.get('ped')), 'vale_pedagio': _r2(c.get('vped')), 'ccf': _r2(c.get('ccf')),
            'ciot': c.get('ciot'), 'obs': (c.get('obs') or '')[:400], 'usu': c.get('usu'),
            'manifestos': sorted(man_do.get(k, set())), 'ctes': ces[:60], 'n_ctes': len(ces),
            'pagamentos': pag.get(k, []), 'estornos': estornos.get(k, []),
            'casos': casos_do.get(k, []), 'sinais': sinais.get(k, []), 'gps': emb.get(c['_base']),
            'mesmo_cte': sorted({x for ce in ces for x in ctrbs_do_cte.get(ce, ()) if x != k})[:20],
        }
    ctes_out = {}
    for ce in ctes_usados:
        e = cte.get(ce)
        if e:
            ctes_out[ce] = {'cte': e.get('cte'), 'em': str(e.get('em') or '')[:10], 'tipo': e.get('tipo'),
                            'o': f"{e.get('o_cid')}/{e.get('o_uf')}" if e.get('o_cid') else None,
                            'd': (f"{e.get('e_cid')}/{e.get('e_uf')}" if e.get('e_cid') else
                                  (f"{e.get('d_cid')}/{e.get('d_uf')}" if e.get('d_cid') else None)),
                            'fs': _r2(e.get('fs')), 'pag': e.get('pag'), 'n_ctrbs': len(ctrbs_do_cte.get(ce, ()))}

    adiant_tot = sum(v['adiant'] for v in emissor.values())
    adiant_prop = sum(v['adiant_proprio'] for v in emissor.values())

    # quantas semanas o saldo atravessa antes de sair (os pagos do período) — a régua do "normal" da semana
    faixa = lambda s: '0' if s <= 0 else ('1' if s == 1 else ('2' if s == 2 else '3+'))
    adi_pagos = Counter()
    for k, ps in pag.items():
        c = ctrb.get(k)
        if not c or not c['_em'] or c['_em'].date() < desde:
            continue
        for p in ps:
            if p['tipo'] == 'saldo' and p['situacao'] == 'pago' and p['venc']:
                adi_pagos[faixa(p['adiado'])] += 1
    return {
        'versao': 1, 'desde': desde.isoformat(), 'hoje': hoje.isoformat(),
        'inicio_073': min((c['_em'] for c in ctrb.values() if c['_em']), default=None) and
                      min(c['_em'] for c in ctrb.values() if c['_em']).date().isoformat(),
        'casos': lista_casos, 'fila': fila, 'estornos': lista_est,
        'controles': {'segregacao': segreg, 'adiant_total': adiant_tot, 'adiant_proprio': adiant_prop,
                      'sem_receita': sem_receita, 'frota_paga': frota_paga, 'adiamento_pagos': dict(adi_pagos)},
        'ctrbs': ctrbs_out, 'ctes': ctes_out,
        'contagens': {'ctrbs': len(ctrb), 'pagamentos': sum(len(v) for v in pag.values()),
                      'estornos': len(lista_est), 'casos': len(lista_casos)},
    }


# ── leitura para a tela (aplica as decisões) ─────────────────────────────────────────────────────────
def _sinal_final(k, pacote, decis):
    c = pacote['ctrbs'].get(k)
    if not c:
        ini = pacote.get('inicio_073')
        txt = (f"CTRB anterior ao 073 do BI (que começa em {ini[8:10]}/{ini[5:7]}/{ini[2:4]}) — saldo segurado há meses; confira no SSW"
               if ini else 'CTRB não encontrado no 073 do BI — confira no SSW')
        return 'cinza', [{'nivel': 'cinza', 'cod': 'fora_bi', 'texto': txt}]
    motivos = list(c['sinais'])
    casos = {cs['chave']: cs for cs in pacote['casos']}
    for ch in c['casos']:
        cs = casos.get(ch)
        if not cs:
            continue
        d = (decis.get(ch) or {}).get('status', '')
        outros = ', '.join(x for x in cs['ctrbs'] if x != k)
        frota_k = {x for x in cs['ctrbs'] if (pacote['ctrbs'].get(x) or {}).get('tipo') == 'FROTA'}
        com_estado = ' · '.join(f"{x}: {estado_dinheiro(cs['por_ctrb'][x], x in frota_k)}"
                                for x in cs['ctrbs'] if x != k and x in cs['por_ctrb'])
        seg = (cs['por_ctrb'].get(k) or {}).get('segurar') or 0
        if d in ('ok', 'resolvido'):
            motivos.insert(0, {'nivel': 'ok', 'cod': 'decidido', 'caso': ch,
                               'texto': f"{cs['tipo_texto']} com {outros} — {DECISOES[d].lower()} por {decis[ch]['usuario']}"})
        elif cs['status'] == 'pendente' and seg > 1:
            motivos.insert(0, {'nivel': 'vermelho', 'cod': cs['tipo'], 'caso': ch, 'segurar': seg,
                               'texto': f"{cs['tipo_texto']} — segurar {brl(seg)} deste CTRB · {com_estado or outros}"})
        elif cs['status'] == 'pendente':
            motivos.append({'nivel': 'info', 'cod': cs['tipo'], 'caso': ch,
                            'texto': f"{cs['tipo_texto']} com {outros} — o programado deste CTRB é devido (o que segura é o outro)"})
        elif cs['status'] == 'pago_2x':
            quem = (cs.get('conta') or {}).get('quem')
            mesmo = bool(quem) and doc((pacote['ctrbs'].get(quem) or {}).get('prop_doc')) == doc(c.get('prop_doc'))
            motivos.insert(0, {'nivel': 'amarelo' if mesmo else 'info', 'cod': cs['tipo'], 'caso': ch,
                               'texto': f"Pago a mais no caso com {outros}: {brl(cs['excesso'])}"
                                        + (' — é o mesmo proprietário: dá para descontar daqui' if mesmo else '')})
        elif cs['status'] == 'conferir':
            motivos.insert(0, {'nivel': 'amarelo', 'cod': cs['tipo'], 'caso': ch,
                               'texto': f"{cs['tipo_texto']} — {com_estado or outros} — confira se fez a viagem"})
        elif cs['status'] in ('explicado', 'recuperado', 'um_recebeu'):
            motivos.append({'nivel': 'info', 'cod': cs['tipo'], 'caso': ch,
                            'texto': f"{cs['tipo_texto']} com {outros} — {cs['status_texto'].lower()}: {cs.get('fazer') or ''}"[:220]})
    ordem = ['vermelho', 'amarelo']
    nivel = next((o for o in ordem if any(m['nivel'] == o for m in motivos)), 'verde')
    return nivel, motivos


def ler_painel(pacote, decis, mudancas=None):
    """O que a tela abre: KPIs, fila, casos (resumo), estornos e controles. As decisões do financeiro
    entram aqui, não no pacote — mudam na hora, sem recalcular. `mudancas`: o que mudou na programação do 477."""
    hoje = (datetime.utcnow() - BRT).date()
    h7 = (hoje + timedelta(days=7)).isoformat()
    quinta = proxima_quinta(hoje).isoformat()
    fila = []
    # quanto segurar de cada CTRB (casos "dá para segurar" sem decisão): reparte pelos lançamentos, o próximo lote
    # primeiro; o que sobra do CTRB é devido e não fica vermelho
    resta = defaultdict(float)
    for cs in pacote['casos']:
        if cs['status'] == 'pendente' and (decis.get(cs['chave']) or {}).get('status') not in ('ok', 'resolvido'):
            for k, p in cs['por_ctrb'].items():
                resta[k] = max(resta[k], p.get('segurar') or 0)
    for f in sorted(pacote['fila'], key=lambda f: (f.get('lote') is None, f['data'] or '9999')):
        nivel, motivos = _sinal_final(f['ctrb'], pacote, decis)
        seg = 0.0
        if any(m['cod'] == 'cancelado' for m in motivos):
            seg = f['valor']
        elif resta[f['ctrb']] > 1:
            seg = min(f['valor'], resta[f['ctrb']])
            resta[f['ctrb']] -= seg
        if nivel == 'vermelho' and seg < 1:
            nivel = 'amarelo' if any(m['nivel'] == 'amarelo' for m in motivos) else 'verde'
            motivos = [m for m in motivos if m['nivel'] != 'vermelho'] + [
                {'nivel': 'info', 'cod': 'devido',
                 'texto': 'Este lançamento é devido: o valor a segurar deste CTRB já está em outro lançamento'}]
        fila.append({**f, 'sinal': nivel, 'motivos': motivos, 'segurar': _r2(seg)})
    fila.sort(key=lambda f: (f['data'] or '9999', -f['valor']))
    casos = []
    for cs in pacote['casos']:
        d = decis.get(cs['chave']) or {}
        casos.append({k: v for k, v in cs.items() if k != 'regua'} | {
            'regua': cs['regua'], 'decisao': d.get('status', ''), 'decisao_texto': DECISOES.get(d.get('status', ''), ''),
            'decisao_por': d.get('usuario'), 'decisao_em': d.get('em'), 'decisao_obs': d.get('obs'),
            'resumo_ctrbs': [{'ctrb': k, 'tipo': (pacote['ctrbs'].get(k) or {}).get('tipo'),
                              'o': (pacote['ctrbs'].get(k) or {}).get('o'), 'd': (pacote['ctrbs'].get(k) or {}).get('d'),
                              'cav': (pacote['ctrbs'].get(k) or {}).get('cav'), 'car': (pacote['ctrbs'].get(k) or {}).get('car'),
                              'prop': (pacote['ctrbs'].get(k) or {}).get('prop'),
                              'emissao': (pacote['ctrbs'].get(k) or {}).get('emissao'),
                              'usu': (pacote['ctrbs'].get(k) or {}).get('usu'), **cs['por_ctrb'][k]} for k in cs['ctrbs']]})
    aberto = [c for c in casos if c['status'] in ('pendente', 'pago_2x') and c['decisao'] not in ('ok', 'resolvido')]
    prox = [f for f in fila if (f['data'] or '') <= h7]
    vermelhos = [f for f in fila if f['sinal'] == 'vermelho']
    ano = pacote['desde'][:4]
    ctl = pacote['controles']

    def mini(k):
        c = pacote['ctrbs'].get(k) or {}
        return {'ctrb': k, 'tipo': c.get('tipo'), 'emissao': c.get('emissao'), 'o': c.get('o'), 'd': c.get('d'),
                'cav': c.get('cav'), 'car': c.get('car'), 'prop': c.get('prop'), 'valor': c.get('valor_a_pagar'),
                'pago': round(sum(p['valor'] for p in c.get('pagamentos', []) if p['situacao'] == 'pago'), 2),
                'usu': c.get('usu'), 'obs': (c.get('obs') or '')[:120],
                'texto': next((s['texto'] for s in c.get('sinais', []) if s['cod'] in ('sem_receita', 'frota_rizza')), '')}
    estornos = pacote['estornos']
    lote = [f for f in fila if f['tipo_pag'] == 'saldo' and f.get('lote') and hoje.isoformat() <= f['lote'] <= quinta]
    parados = [f for f in fila if f['tipo_pag'] == 'saldo' and not f.get('lote')]
    faixa = lambda s: '0' if s <= 0 else ('1' if s == 1 else ('2' if s == 2 else '3+'))
    saldos_pend = [f for f in fila if f['tipo_pag'] == 'saldo']
    mud = mudancas or []
    adiamento = {
        'pagos': ctl.get('adiamento_pagos') or {},
        'pendentes': dict(Counter(faixa(f.get('adiado') or 0) for f in saldos_pend)),
        'segurados': sorted(({'ctrb': f['ctrb'], 'favorecido': f['favorecido'], 'valor': f['valor'], 'venc': f['venc'],
                              'adiado': f['adiado'], 'lote': f['lote'], 'sinal': f['sinal'], 'tipo': f['tipo'],
                              'o': f['o'], 'd': f['d'], 'aprov': f['aprov']}
                             for f in saldos_pend if (f.get('adiado') or 0) >= 3), key=lambda r: (-r['adiado'], -r['valor'])),
        'mudancas': mud, 'mudancas_resumo': dict(Counter(m['evento'] for m in mud)),
    }
    kpis = {
        'quinta': {'data': quinta, 'n': len(lote), 'valor': round(sum(f['valor'] for f in lote), 2),
                   'alerta_n': sum(1 for f in lote if f['sinal'] == 'vermelho'),
                   'alerta_valor': round(sum(f['segurar'] for f in lote), 2),
                   'adiados': sum(1 for f in lote if (f.get('adiado') or 0) >= 1),
                   'aprovados': sum(1 for f in lote if f.get('aprov')),
                   'parados_n': len(parados), 'parados_valor': round(sum(f['valor'] for f in parados), 2)},
        'prox7': {'valor': round(sum(f['valor'] for f in prox), 2), 'n': len(prox),
                  'alerta_valor': round(sum(f['segurar'] for f in prox), 2),
                  'alerta_n': sum(1 for f in prox if f['sinal'] == 'vermelho')},
        'segurar': {'valor': round(sum(f['segurar'] for f in fila), 2), 'n': len(vermelhos),
                    'casos': sum(1 for c in aberto if c['status'] == 'pendente')},
        # pago a mais = o que já saiu além do devido e não voltou (inclui o que fica a cobrar nos casos de segurar)
        'pago_2x': {'valor': round(sum((c.get('conta') or {}).get('cobrar') or 0 for c in aberto), 2),
                    'n': sum(1 for c in aberto if ((c.get('conta') or {}).get('cobrar') or 0) > 50), 'ano': ano},
        'voltou': {'valor': round(sum(c.get('voltou') or 0 for c in casos), 2),
                   'n': sum(1 for c in casos if (c.get('voltou') or 0) > 1)},
        'estornos': {'valor': round(sum(e['valor'] for e in estornos), 2), 'n': len(estornos),
                     'ctrbs': len({e['ctrb'] for e in estornos})},
        'segregacao': {'proprio': ctl['adiant_proprio'], 'total': ctl['adiant_total'],
                       'pct': round(100 * ctl['adiant_proprio'] / ctl['adiant_total'], 1) if ctl['adiant_total'] else 0,
                       'valor': round(sum(r['valor_proprio'] for r in ctl['segregacao']), 2)},
    }
    return {
        'kpis': kpis, 'fila': fila, 'casos': casos, 'estornos': estornos,
        'controles': {'segregacao': ctl['segregacao'], 'adiamento': adiamento,
                      'sem_receita': sorted((mini(k) for k in ctl['sem_receita']), key=lambda r: r['emissao'] or '', reverse=True),
                      'frota_paga': sorted((mini(k) for k in ctl['frota_paga']), key=lambda r: r['emissao'] or '', reverse=True)},
        'hoje': hoje.isoformat(), 'quinta': quinta, 'desde': pacote['desde'], 'contagens': pacote['contagens'],
    }


def dossie(pacote, k, decis):
    """Tudo sobre um CTRB, cruzado: rota, receita, pagamentos, banco, estornos, GPS, os outros CTRBs do CT-e."""
    c = pacote['ctrbs'].get(k)
    if not c:
        return None
    nivel, motivos = _sinal_final(k, pacote, decis)
    casos = {cs['chave']: cs for cs in pacote['casos']}
    meus = []
    for ch in c['casos']:
        cs = casos.get(ch)
        if cs:
            d = decis.get(ch) or {}
            meus.append({**cs, 'decisao': d.get('status', ''), 'decisao_texto': DECISOES.get(d.get('status', ''), ''),
                         'decisao_por': d.get('usuario'), 'decisao_em': d.get('em'), 'decisao_obs': d.get('obs')})
    vizinhos = []
    for x in c['mesmo_cte']:
        v = pacote['ctrbs'].get(x)
        if v:
            vizinhos.append({'ctrb': x, 'tipo': v['tipo'], 'o': v['o'], 'd': v['d'], 'km': v['km'], 'cav': v['cav'],
                             'car': v['car'], 'ativo': v['ativo'], 'emissao': v['emissao'], 'valor': v['valor_a_pagar'],
                             'pago': round(sum(p['valor'] for p in v['pagamentos'] if p['situacao'] == 'pago'), 2),
                             'programado': round(sum(p['valor'] for p in v['pagamentos'] if p['situacao'] == 'programado'), 2),
                             'estornado': round(sum(e['valor'] for e in v['estornos']), 2)})
    return {**c, 'sinal': nivel, 'motivos': motivos, 'casos_det': meus, 'vizinhos': vizinhos,
            'ctes_det': [pacote['ctes'][ce] for ce in c['ctes'] if ce in pacote['ctes']],
            'pago': round(sum(p['valor'] for p in c['pagamentos'] if p['situacao'] == 'pago'), 2),
            'programado': round(sum(p['valor'] for p in c['pagamentos'] if p['situacao'] == 'programado'), 2),
            'estornado': round(sum(e['valor'] for e in c['estornos']), 2)}


def buscar(pacote, q, limite=30):
    """CTRB, placa, CT-e, manifesto, CIOT ou nome/documento do proprietário."""
    q = (q or '').strip().upper()
    if len(q) < 3:
        return []
    qd, qp = doc(q), placa(q)
    achados = []
    for k, c in pacote['ctrbs'].items():
        if (qd and (qd in doc(k) or qd == base_ctrb(k))) or (qp and (qp == placa(c['cav']) or qp == placa(c['car']))) \
                or (qd and any(qd == doc(m) for m in c['manifestos'])) or (qd and any(qd == doc(ce) for ce in c['ctes'])) \
                or (qd and len(qd) >= 8 and (qd in doc(c['prop_doc']) or qd in doc(c['ciot']))) \
                or (len(q) >= 4 and not q.replace(' ', '').isdigit() and q in (c['prop'] or '').upper()):
            achados.append(c)
    achados.sort(key=lambda c: c['emissao'] or '', reverse=True)
    return [{'ctrb': c['ctrb'], 'emissao': c['emissao'], 'tipo': c['tipo'], 'o': c['o'], 'd': c['d'], 'cav': c['cav'],
             'car': c['car'], 'prop': c['prop'], 'valor': c['valor_a_pagar'], 'n_sinais': len(c['sinais']) + len(c['casos'])}
            for c in achados[:limite]]


# ── banco do app ─────────────────────────────────────────────────────────────────────────────────────
DDL = """
CREATE TABLE IF NOT EXISTS pag_ctrb_rodadas (
    id          SERIAL PRIMARY KEY,
    gerado_em   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    bi_refresh  TIMESTAMP,
    duracao_s   REAL,
    resumo      JSONB,
    pacote      BYTEA NOT NULL
);
CREATE TABLE IF NOT EXISTS pag_ctrb_decisoes (
    id          SERIAL PRIMARY KEY,
    chave       TEXT NOT NULL,
    status      TEXT NOT NULL,
    obs         TEXT,
    usuario     TEXT,
    em          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_pag_ctrb_decisoes_chave ON pag_ctrb_decisoes (chave, em DESC);
CREATE TABLE IF NOT EXISTS pag_ctrb_agenda_atual (
    lanc        TEXT PRIMARY KEY,           -- unidade + nº do lançamento do 477 + parcela
    ctrb        TEXT NOT NULL,
    tipo        TEXT,
    situacao    TEXT,
    data        DATE,
    venc        DATE,
    aprov       DATE,
    valor       NUMERIC(14,2),
    visto_em    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS pag_ctrb_agenda_hist (
    id          SERIAL PRIMARY KEY,
    lanc        TEXT NOT NULL,
    ctrb        TEXT NOT NULL,
    tipo        TEXT,
    evento      TEXT NOT NULL,              -- adiado | antecipado | aprovado | desaprovado | pago | valor | saiu | novo
    de          TEXT,
    para        TEXT,
    valor       NUMERIC(14,2),
    visto_em    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_pag_ctrb_agenda_hist_ctrb ON pag_ctrb_agenda_hist (ctrb, visto_em DESC);
CREATE INDEX IF NOT EXISTS ix_pag_ctrb_agenda_hist_visto ON pag_ctrb_agenda_hist (visto_em DESC);
"""


def registrar_agenda(cur, pacote):
    """Compara a programação do 477 desta rodada com a anterior e grava cada mudança — é o histórico que o 477 não
    guarda (ele só tem o estado atual). Na primeira rodada só fotografa. Devolve quantas mudanças achou.

    A foto é só do programado; o que sumiu dela e aparece pago nesta rodada vira "pago", o resto vira "saiu"."""
    atual, todos = {}, {}
    hoje = pacote['hoje']
    for k, c in pacote['ctrbs'].items():
        for p in c.get('pagamentos', []):
            # A data que conta é a da QUINTA em que o saldo pode sair (o lote): o 477 puxa o atrasado para o dia da
            # extração, e a data crua andaria todo dia sozinha. Adiantamento atrasado fica sem data (não é mudança).
            if p['situacao'] == 'programado':
                efetiva = p['data'] if (p['data'] or '') >= hoje else None      # None = parado (data passou)
            else:
                efetiva = p['data'] or None
            reg = {'ctrb': k, 'tipo': p['tipo'], 'situacao': p['situacao'], 'data': efetiva,
                   'venc': p['venc'] or None, 'aprov': p.get('aprov'), 'valor': p['valor']}
            todos[p['lanc']] = reg
            if p['situacao'] == 'programado':
                atual[p['lanc']] = reg
    cur.execute("SELECT lanc, ctrb, tipo, situacao, data, venc, aprov, valor FROM pag_ctrb_agenda_atual")
    antes = {r[0]: {'ctrb': r[1], 'tipo': r[2], 'situacao': r[3], 'data': r[4].isoformat() if r[4] else None,
                    'venc': r[5].isoformat() if r[5] else None, 'aprov': r[6].isoformat() if r[6] else None,
                    'valor': float(r[7]) if r[7] is not None else None} for r in cur.fetchall()}
    eventos = eventos_agenda(antes, atual, todos)
    for lanc, a, ev, de, para in eventos:
        cur.execute("""INSERT INTO pag_ctrb_agenda_hist (lanc, ctrb, tipo, evento, de, para, valor)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""", (lanc, a['ctrb'], a['tipo'], ev, de, para, a['valor']))
    cur.execute("DELETE FROM pag_ctrb_agenda_atual")
    for lanc, a in atual.items():
        cur.execute("""INSERT INTO pag_ctrb_agenda_atual (lanc, ctrb, tipo, situacao, data, venc, aprov, valor)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (lanc, a['ctrb'], a['tipo'], a['situacao'], a['data'], a['venc'], a.get('aprov'), a['valor']))
    return len(eventos)


def eventos_agenda(antes, atual, todos):
    """As mudanças entre duas fotos da programação (pura). Sem foto anterior, nada (a 1ª rodada só fotografa)."""
    eventos = []
    if antes:
        for lanc, a in atual.items():
            b = antes.get(lanc)
            if not b:
                if a['situacao'] == 'programado':
                    eventos.append((lanc, a, 'novo', None, a['data']))
                continue
            if a['data'] != b['data'] and a['data'] and b['data']:
                eventos.append((lanc, a, 'adiado' if a['data'] > b['data'] else 'antecipado', b['data'], a['data']))
            elif b['data'] and not a['data']:
                eventos.append((lanc, a, 'parado', b['data'], None))     # a data chegou e passou sem pagar nem remarcar
            elif a['data'] and not b['data']:
                eventos.append((lanc, a, 'remarcado', None, a['data']))  # estava parado e ganhou data nova
            if a.get('aprov') != b.get('aprov'):
                eventos.append((lanc, a, 'aprovado' if a.get('aprov') else 'desaprovado', b.get('aprov'), a.get('aprov')))
            if b['valor'] is not None and abs((a['valor'] or 0) - b['valor']) > 0.01:
                eventos.append((lanc, a, 'valor', f"{b['valor']:.2f}", f"{a['valor']:.2f}"))
        for lanc, b in antes.items():
            if lanc in atual:
                continue
            agora = todos.get(lanc)
            if agora and agora['situacao'] == 'pago':
                eventos.append((lanc, agora, 'pago', b['data'], agora['data']))
            else:
                eventos.append((lanc, b, 'saiu', b['data'], None))
    return eventos


EVENTO_AGENDA = {'adiado': 'Adiado', 'antecipado': 'Antecipado', 'parado': 'Passou da data sem pagar',
                 'remarcado': 'Remarcado', 'aprovado': 'Aprovado para pagar',
                 'desaprovado': 'Aprovação retirada', 'pago': 'Pago', 'valor': 'Valor mudou',
                 'saiu': 'Saiu da programação', 'novo': 'Programado'}


def historico_agenda(cur, ctrb):
    cur.execute("""SELECT lanc, tipo, evento, de, para, valor, visto_em FROM pag_ctrb_agenda_hist
                    WHERE ctrb = %s ORDER BY visto_em DESC, id DESC LIMIT 60""", (ctrb,))
    return [{'lanc': r[0], 'tipo': r[1], 'evento': r[2], 'evento_texto': EVENTO_AGENDA.get(r[2], r[2]), 'de': r[3],
             'para': r[4], 'valor': float(r[5]) if r[5] is not None else None,
             'visto_em': r[6].isoformat() if r[6] else None} for r in cur.fetchall()]


def mudancas_recentes(cur, dias=8):
    cur.execute("""SELECT lanc, ctrb, tipo, evento, de, para, valor, visto_em FROM pag_ctrb_agenda_hist
                    WHERE visto_em >= NOW() - (%s || ' days')::interval AND evento <> 'novo'
                    ORDER BY visto_em DESC, id DESC LIMIT 400""", (str(dias),))
    return [{'lanc': r[0], 'ctrb': r[1], 'tipo': r[2], 'evento': r[3], 'evento_texto': EVENTO_AGENDA.get(r[3], r[3]),
             'de': r[4], 'para': r[5], 'valor': float(r[6]) if r[6] is not None else None,
             'visto_em': r[7].isoformat() if r[7] else None} for r in cur.fetchall()]


def garantir(cur):
    cur.execute(DDL)


def gravar_rodada(cur, pacote, bi_refresh, duracao_s):
    blob = gzip.compress(json.dumps(pacote, ensure_ascii=False, default=str).encode('utf-8'), 6)
    resumo = {**pacote['contagens'], 'bytes': len(blob)}
    cur.execute("INSERT INTO pag_ctrb_rodadas (bi_refresh, duracao_s, resumo, pacote) VALUES (%s, %s, %s, %s) RETURNING id",
                (bi_refresh, duracao_s, json.dumps(resumo), blob))
    rid = cur.fetchone()[0]
    cur.execute("DELETE FROM pag_ctrb_rodadas WHERE id NOT IN (SELECT id FROM pag_ctrb_rodadas ORDER BY id DESC LIMIT %s)",
                (RODADAS_GUARDADAS,))
    return rid


def ultima_rodada_id(cur):
    cur.execute("SELECT id, gerado_em, bi_refresh, duracao_s, resumo FROM pag_ctrb_rodadas ORDER BY id DESC LIMIT 1")
    r = cur.fetchone()
    if not r:
        return None
    return {'id': r[0], 'gerado_em': r[1].isoformat() if r[1] else None,
            'bi_refresh': r[2].isoformat() if r[2] else None, 'duracao_s': r[3], 'resumo': r[4]}


def carregar_pacote(cur, rid):
    cur.execute("SELECT pacote FROM pag_ctrb_rodadas WHERE id = %s", (rid,))
    r = cur.fetchone()
    return json.loads(gzip.decompress(bytes(r[0])).decode('utf-8')) if r else None


def decisoes(cur):
    cur.execute("""SELECT DISTINCT ON (chave) chave, status, obs, usuario, em
                     FROM pag_ctrb_decisoes ORDER BY chave, em DESC, id DESC""")
    return {r[0]: {'status': r[1], 'obs': r[2], 'usuario': r[3], 'em': r[4].isoformat() if r[4] else None}
            for r in cur.fetchall()}


def historico_decisoes(cur, chave):
    cur.execute("SELECT status, obs, usuario, em FROM pag_ctrb_decisoes WHERE chave = %s ORDER BY em DESC, id DESC", (chave,))
    return [{'status': r[0], 'status_texto': DECISOES.get(r[0], r[0]), 'obs': r[1], 'usuario': r[2],
             'em': r[3].isoformat() if r[3] else None} for r in cur.fetchall()]


def decidir(cur, chave, status, obs, usuario):
    if status not in DECISOES:
        raise ValueError('Decisão inválida.')
    if not re.fullmatch(r'[A-Z]{3}\d{6}-\d(\|[A-Z]{3}\d{6}-\d)+', chave or ''):
        raise ValueError('Caso inválido.')
    if status in ('ok', 'resolvido') and not (obs or '').strip():
        raise ValueError('Diga em uma linha por que está certo (fica no histórico do caso).')
    cur.execute("INSERT INTO pag_ctrb_decisoes (chave, status, obs, usuario) VALUES (%s, %s, %s, %s)",
                (chave, status, (obs or '').strip()[:500] or None, usuario))


# ── execução ─────────────────────────────────────────────────────────────────────────────────────────
ESTADO = {'rodando': False, 'inicio': None, 'etapa': None, 'erro': None, 'fim': None}
_TRAVA = threading.Lock()


def executar(dax_op, dax_cont, get_db, bi_refresh=None, log=print):
    """Lê o BI e o banco, analisa e grava a rodada. Uma por vez (a segunda chamada volta na hora)."""
    if not _TRAVA.acquire(blocking=False):
        return {'ok': False, 'motivo': 'já está rodando'}
    t0 = time.time()
    ESTADO.update(rodando=True, inicio=datetime.utcnow().isoformat(), etapa='lendo o Power BI', erro=None)
    try:
        raw = coletar(dax_op, dax_cont, log=log)
        ESTADO['etapa'] = 'cruzando com o GPS do Embarques'
        bases = {base_ctrb(c['ctrb']) for c in raw.get('ctrbs', []) if c.get('ctrb')}
        conn = get_db()
        try:
            with conn.cursor() as cur:
                emb = evidencia_embarques(cur, bases)
            ESTADO['etapa'] = 'analisando'
            pacote = analisar(raw, emb)
            with conn.cursor() as cur:
                garantir(cur)
                rid = gravar_rodada(cur, pacote, bi_refresh, round(time.time() - t0, 1))
                mud = registrar_agenda(cur, pacote)
            conn.commit()
            if mud:
                log(f'pagamentos_ctrb: {mud} mudança(s) na programação do 477 desde a rodada anterior')
        finally:
            conn.close()
        log(f"pagamentos_ctrb: rodada {rid} gravada em {time.time() - t0:.0f} s — {pacote['contagens']}")
        return {'ok': True, 'id': rid, 'contagens': pacote['contagens']}
    except Exception as e:
        ESTADO['erro'] = str(e)[:300]
        log(f'⚠️  pagamentos_ctrb: falha na rodada: {e}')
        return {'ok': False, 'motivo': str(e)}
    finally:
        ESTADO.update(rodando=False, etapa=None, fim=datetime.utcnow().isoformat())
        _TRAVA.release()


def loop(dax_op, dax_cont, get_db):
    """Recalcula depois de cada refresh do BI (passada a folga), como a conferência do CIOT."""
    from server import get_token, CONFIG
    import ciot_conferencia as cc
    ultimo_ref = None
    no_boot = True
    while True:
        try:
            fim, rodando = cc.estado_refresh(get_token(), CONFIG['group_id'], CONFIG['dataset_id'])
            agora = datetime.utcnow()
            if no_boot:
                no_boot = False
                ultimo_ref = fim
                conn = get_db()
                try:
                    with conn.cursor() as cur:
                        garantir(cur)
                        tem = ultima_rodada_id(cur)
                    conn.commit()
                finally:
                    conn.close()
                if not tem:
                    executar(dax_op, dax_cont, get_db, bi_refresh=fim)
            elif fim and fim != ultimo_ref and not rodando and agora >= fim + timedelta(minutes=ESPERA_POS_REFRESH_MIN):
                executar(dax_op, dax_cont, get_db, bi_refresh=fim)
                ultimo_ref = fim
        except Exception as e:
            print(f'⚠️  pagamentos_ctrb: laço: {e}')
        time.sleep(300)
