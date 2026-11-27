"""Acompanhamento das viagens — a planilha TRACKING (26/11/2026) virando tela.

A planilha era uma linha por CT-e, digitada à mão: CTRC, cliente, NF, placa (cópia do SSW) e depois
agenda, entrega, motorista, status, ocorrência, descarga e diária. Aqui a linha é a VIAGEM (o último
manifesto do CT-e, a mesma âncora do Embarques) e abre os CT-e dela; o que o SSW já sabe vem sozinho:

- CT-e, cliente, NF, frete, placa .......... conhecimentos_emitidos (BI Operacional)
- motorista, cavalo e carreta da viagem .... manifestos (916)
- saída, previsão e chegada ................ manifestos_200 (relatório 200, ssw0644)
- agenda ................................... ocorrência 15 do SSW ("ENTREGA AGENDADA P/ dd/mm/aa hh:mm");
                                            a 35 = aguardando agendamento do cliente
- entrega .................................. a da planilha TRACKING / marcada na tela vale; sem nada, a
                                            ocorrência 01 do SSW (MERCADORIA ENTREGUE) entra direto, com o
                                            selo da fonte (pedido de 27/11/2026; antes era sugestão a
                                            confirmar). A do portal da YPE continua sugestão a confirmar.
- documentação e custo extra ............... CRM - Documentos (só leitura)
- YPE (Química Amparo) ...................... portal MultiTMS (ype_cargas*, ype_monitoramento): pedido do cliente,
                                            prazo da YPE, entrega do portal (sugestão) e se o caminhão está
                                            monitorado — sem monitoramento ativo a YPE não paga diária

O que só a pessoa sabe fica em `acomp_marcacao` (um JSON de campos por CT-e) com cada mudança em
`acomp_log`: status (lista fechada), entrega confirmada, agenda (quando o SSW não tem), nº da ocorrência
aberta na plataforma do cliente e anotação. A leitura do SSW nunca sobrescreve o que a pessoa marcou.

Quem entra (o dono da planilha, 26/11/2026): todo CT-e do período MENOS complementar, substituto e
RPS — no BI, documento sem chave de CT-e ("SUBC … FORM LISO") —, exceto o RPS da Nestlé com entrega em
Contagem, que é entrega. Conferido em out/2026: os 610 CT-e da planilha entram e nenhum fica de fora.

Entrega pelo SSW (medido ago–nov/2026 contra a planilha): a oc. 01 lançada à mão ou pelo SSWMobile bate com
a data digitada; a do SSWScan (lê a data da IMAGEM do canhoto) trouxe o dia da EMISSÃO em 86% das
discordâncias. Por isso a data do SSWScan igual à emissão não vira sugestão — aparece como aviso.
"""
import json
import re
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone

# ── Regras ────────────────────────────────────────────────────────────────────

TIPOS_FORA = {'COMPLEMENTAR FRETE', 'SUBSTITUTO'}

# Status: lista fechada (a planilha tinha 64 jeitos de escrever). `ordem` = quanto a viagem andou: a viagem
# mostra o status do CT-e MENOS adiantado, que é o que ainda pede atenção.
STATUS = [
    {'cod': 'CARREGANDO',  'rot': 'Carregando',            'cor': 'cinza',    'ordem': 1},
    {'cod': 'TRANSITO',    'rot': 'Em trânsito',           'cor': 'azul',     'ordem': 2},
    {'cod': 'NO_CLIENTE',  'rot': 'No cliente',            'cor': 'ciano',    'ordem': 3},
    {'cod': 'REAGENDADO',  'rot': 'Reagendado',            'cor': 'amarelo',  'ordem': 3},
    {'cod': 'PERDEU_AGENDA', 'rot': 'Perda de agendamento', 'cor': 'laranja', 'ordem': 3},
    {'cod': 'DESCARGA',    'rot': 'Em descarga',           'cor': 'indigo',   'ordem': 4},
    {'cod': 'ENTREGUE',    'rot': 'Entregue',              'cor': 'verde',    'ordem': 5},
    {'cod': 'RECUSADA',    'rot': 'Recusada / devolução',  'cor': 'vermelho', 'ordem': 5},
]
STATUS_COD = {s['cod'] for s in STATUS}

CAMPOS = {
    'status': 'status',          # código de STATUS
    'entrega': 'data',           # entrega confirmada pela pessoa
    'agenda': 'data_hora',       # agenda quando o SSW não tem (ou corrigida)
    'ocorrencia': 'texto',       # nº da ocorrência aberta na plataforma do cliente
    'anotacao': 'texto',
    'descarga': 'valor',         # valor da descarga que a pessoa informa (a coluna VALOR DESCARGA da planilha),
                                 # por destinatário: num CT-e só do destino (251 de 258 na planilha)
}
ROTULO_CAMPO = {'status': 'Status', 'entrega': 'Entrega', 'agenda': 'Agenda',
                'ocorrencia': 'Ocorrência no cliente', 'anotacao': 'Anotação', 'descarga': 'Descarga informada'}

RE_AGENDA = re.compile(r'AGENDAD[AO]\s+(?:P/|PARA)\s*(\d{2})/(\d{2})/(\d{2,4})(?:\s+(\d{2}):(\d{2}))?', re.I)
RE_DATA_TXT = re.compile(r'(?:ENTREGUE|REALIZADA)\D{0,15}(\d{2})/(\d{2})/(\d{2,4})', re.I)


def _sem_acento(s):
    return ''.join(c for c in unicodedata.normalize('NFD', str(s or '')) if unicodedata.category(c) != 'Mn')


def hoje_brt():
    return datetime.now(timezone(timedelta(hours=-3))).date()


def _iso(v):
    """Data/hora do BI → 'AAAA-MM-DD' (ou None)."""
    s = str(v or '')[:10]
    return s if re.fullmatch(r'\d{4}-\d{2}-\d{2}', s) else None


def _iso_hora(d, h):
    """Data do BI + hora 'HH:MM' (ou dentro da data) → 'AAAA-MM-DDTHH:MM' ou só a data."""
    di = _iso(d)
    if not di:
        return None
    # o BI manda a hora como data-hora do Excel ('1899-12-30T10:40:00'); texto 'HH:MM' também vale
    m = re.search(r'(?:T|^)(\d{2}):(\d{2})', str(h or '').strip()) or re.search(r'T(\d{2}):(\d{2})', str(d or ''))
    hh = f'{m[1]}:{m[2]}' if m else None
    return f'{di}T{hh}' if hh and hh != '00:00' else di


def _ano(a):
    return a if len(a) == 4 else '20' + a


def chave_man(v):
    """'UDI 012345-6' / 'UDI012345-6' → 'UDI012345-6'."""
    s = re.sub(r'\s+', '', str(v or '')).upper()
    return s or None


def raiz_cnpj(cnpj, grupos=None):
    d = re.sub(r'\D', '', str(cnpj or ''))
    if len(d) < 8:
        return None
    r = d[:8]
    return (grupos or {}).get(r, r)


def eh_rps(ce):
    return not str(ce.get('chave') or '').strip()


def entra(ce):
    """Regra do dono da planilha: tudo menos complementar, substituto e RPS (fica o RPS da Nestlé em Contagem)."""
    if str(ce.get('tipo') or '').strip().upper() in TIPOS_FORA:
        return False
    if eh_rps(ce):
        return (_sem_acento(ce.get('pag')).upper().startswith('NESTLE')
                and _sem_acento(ce.get('cid')).upper().strip() == 'CONTAGEM')
    return True


def agenda_do_texto(txt):
    """'ENTREGA AGENDADA P/ 16/11/26 08:00 - CONTATO …' → '2026-11-16T08:00' (ou só a data)."""
    m = RE_AGENDA.search(str(txt or ''))
    if not m:
        return None
    d = f'{_ano(m[3])}-{m[2]}-{m[1]}'
    try:
        date.fromisoformat(d)
    except ValueError:
        return None
    return f'{d}T{m[4]}:{m[5]}' if m[4] else d


def entrega_do_ssw(ocorrencias_01, emissao):
    """A oc. 01 do CT-e → {'data', 'fonte', 'aviso'}.

    Vale a primeira baixa de entrega. A data é a do texto ("ENTREGUE EM dd/mm", "REALIZADA EM dd/mm") quando
    houver, senão a da ocorrência. SSWScan com a data igual à emissão não vira sugestão: em ago–nov/2026 foi
    a leitura errada (o dia impresso no canhoto) em 86% das discordâncias com a planilha."""
    if not ocorrencias_01:
        return None
    o = min(ocorrencias_01, key=lambda x: x.get('oc') or '9999')
    txt = str(o.get('compl') or '')
    m = RE_DATA_TXT.search(txt)
    d = f'{_ano(m[3])}-{m[2]}-{m[1]}' if m else _iso(o.get('oc'))
    scan = 'SSWSCAN' in txt.upper()
    mobile = 'SSWMOBILE' in txt.upper()
    fonte = 'SSWScan' if scan else 'SSWMobile' if mobile else 'SSW'
    if scan and d and emissao and d == emissao:
        return {'data': None, 'fonte': fonte, 'lida': d,
                'aviso': f'o SSWScan leu {d[8:10]}/{d[5:7]} no canhoto, o dia da emissão — confira a data real'}
    return {'data': d, 'fonte': fonte, 'hora': str(o.get('oc') or '')[11:16] or None}


# ── Leitura do BI ─────────────────────────────────────────────────────────────

def _dax_datas(ini, fim):
    return f"DATE({ini.year},{ini.month},{ini.day})", f"DATE({fim.year},{fim.month},{fim.day})"


def dax_ctes(ini, fim):
    a, b = _dax_datas(ini, fim)
    t = "'public conhecimentos_emitidos'"
    return (f"EVALUATE CALCULATETABLE(SELECTCOLUMNS({t}, \"ctrc\", [serie_numero_ctrc], \"tipo\", [tipo_documento], "
            f"\"emissao\", [data_emissao], \"cnpj_pag\", [cnpj_pagador], \"pag\", [cliente_pagador], "
            f"\"reme\", [cliente_remetente], \"dest\", [cliente_destinatario], \"cid\", [cidade_entrega], \"uf\", [uf_entrega], "
            f"\"cid_o\", [cidade_origem_prestacao], \"uf_o\", [uf_origem_prestacao], \"nf\", [numero_nota_fiscal], "
            f"\"frete\", [valor_frete], \"placa\", [placa_cavalo], \"carreta\", [placa_carreta], \"manu\", [ultimo_manifesto], "
            f"\"obs\", [observacao], \"chave\", [chave_cte], \"peso\", [peso_real_kg], \"placa_col\", [placa_coleta], "
            f"\"outros\", [outros], \"tf\", [tipo_frete]), "
            f"{t}[data_emissao] >= {a}, {t}[data_emissao] <= {b})")


def dax_manifestos(ini):
    a, _ = _dax_datas(ini, ini)
    t = "'public manifestos'"
    return (f"EVALUATE CALCULATETABLE(SELECTCOLUMNS({t}, \"chave\", [CHAVE_MANIFESTO], \"emi\", [data_emissao], "
            f"\"placa\", [placa_cavalo], \"carreta\", [placa_carreta], \"mot\", [nome_motorista], \"prop\", [proprietario_cavalo]), "
            f"{t}[data_emissao] >= {a})")


def dax_200(ini):
    a, _ = _dax_datas(ini, ini)
    t = "'public manifestos_200'"
    return (f"EVALUATE CALCULATETABLE(SELECTCOLUMNS({t}, \"num\", [num_manif], \"emissao\", [emissao], "
            f"\"saida\", [saida_data], \"saida_h\", [saida_hora], \"chegada\", [chegada_data], \"chegada_h\", [chegada_hora], "
            f"\"prev\", [prev_chegada_data], \"prev_h\", [prev_chegada_hora], \"mot\", [motorista], \"placa\", [placa_cavalo], "
            f"\"sit\", [situacao_manifesto], \"motivo\", [motivo], \"dest\", [cidade_uf_dest], \"orig\", [cidade_uf_origem]), "
            f"{t}[emissao] >= {a})")


DAX_031 = ("EVALUATE CALCULATETABLE(SELECTCOLUMNS('public ocorrencias_031', \"ctrc\", [ctrc], \"cod\", [ocor_codigo], "
           "\"inc\", [incluida_em], \"oc\", [ocorrida_em], \"compl\", [ocor_complemento]), "
           "'public ocorrencias_031'[ocor_codigo] IN {1, 15, 35})")

# Portal MultiTMS da YPE (Química Amparo), lido 6×/dia no SLTBL-10 pelo ype_multitms_postgres.py (27/11/2026):
# a carga deles tem o pedido do cliente, o prazo que a YPE cobra, a entrega e o monitoramento (sem monitoramento
# ativo a YPE não paga diária). Liga com o CT-e pela chave.
DAX_YPE_CTE = "EVALUATE SELECTCOLUMNS('public ype_cargas_cte', \"chave\", [chave_cte], \"id\", [id_carga])"
DAX_YPE_CARGAS = ("EVALUATE SELECTCOLUMNS('public ype_cargas', \"id\", [id_carga], \"num\", [numero_carga], "
                  "\"sit\", [situacao], \"ped\", [pedido_cliente], \"prazo1\", [prev_entrega_primeiro], "
                  "\"prazo\", [prev_entrega_ultimo], \"real\", [realizada_ultima_entrega], "
                  "\"n_real\", [entregas_realizadas], \"n_pend\", [entregas_pendentes], \"n_man\", [entregas_baixa_manual], "
                  "\"ini\", [inicio_viagem], \"fim\", [fim_viagem], \"lido\", [lido_em])")


def dax_ype_monit(ini):
    a, _ = _dax_datas(ini, ini)
    t = "'public ype_monitoramento'"
    return (f"EVALUATE CALCULATETABLE(SELECTCOLUMNS({t}, \"id\", [id_carga], \"em\", [lido_em], "
            f"\"st\", [status_monitoramento], \"r\", [rastreador], \"up\", [ultima_posicao_em]), {t}[lido_em] >= {a})")


# ── Descarga (27/11/2026): pago ao chapa × cobrado do cliente, por destinatário ──────────────────────
# Pago: 477, evento 5100 (CARGA E DESCARGA C/ TERCEIROS), ligado ao CT-e pela NF do histórico (a mesma régua do
# relatório de segunda, relatorio_5100). Cobrado: o "Outros" do CT-e (clientes que incluem a descarga no CT-e) e
# os CT-e complementares/RPS com "descarga" na observação, ligados ao CT-e original pela referência que a
# observação traz (chave, CTRC, nº do CT-e ou NF). "Valor adicional devido ao reajuste do diesel" (Heinz) e
# diária NÃO são descarga. Medido em 2026: 1.400 cobranças ligadas sozinhas; as em lote sem referência (fatura
# consolidada, fechamento mensal) ficam para a etapa 2 (associar à mão).

# Regra de cada cliente (planilha PROCESSO REEMBOLSO DESCARGA, 27/11/2026, e o que a operação explicou):
#   todos     reembolsa tudo                  incluso   parte vai no "Outros" do CT-e; a diferença se pede
#   acima_500 R$ 500 já vão no frete; reembolsa o que passar   so_cif   só frete CIF (FOB não)   nao   não reembolsa
REGRAS_DESCARGA = [
    (r'SANCHEZ', 'Sanchez Cano', 'todos', 'planilha toda segunda-feira', ''),
    (r'PERNOD', 'Pernod Ricard', 'todos', 'planilha toda sexta-feira',
     '1ª quinzena paga dentro do mês; 2ª até o dia 15 do mês seguinte'),
    (r'HEINZ|KRAFT', 'Heinz', 'todos', 'portal OTM, uma a uma', '15 dias após a entrega'),
    (r'QUIMICA AMPARO|\bYPE\b', 'Ypê', 'incluso', 'parte no CT-e (Outros); a diferença no portal Multi', ''),
    (r'DOCE MINEIRO', 'Doce Mineiro', 'acima_500', 'o que passar de R$ 500, por e-mail', ''),
    (r'DOAR ?BELLEZA', 'Doarbelleza', 'todos', 'e-mail, uma a uma', ''),
    (r'J\.? ?MACEDO', 'J. Macedo', 'nao', '', ''),
    (r'JC DISTRIB', 'JC Distribuição', 'nao', '', ''),
    (r'\bSTART\b', 'Start Química', 'so_cif', 'o financeiro gera o CT-e (transferência para Merio não)', ''),
    (r"L.?OREAL", "L'Oréal", 'todos', 'o financeiro gera o CT-e no fim do mês', ''),
    (r'SUPER ?PODEROSA', 'Superpoderosa', 'todos', 'e-mail, uma a uma', ''),
    (r'SUPPLOG|SUPPORTE', 'Supplog', 'todos', 'portal Supporte, uma a uma', '30 dias'),
    (r'UNILEVER', 'Unilever', 'incluso', 'no CT-e (Outros); a diferença no portal Multi', ''),
    (r'BERGAMO', 'Bergamo', 'todos', 'portal Lincros, uma a uma', ''),
    (r'TANGARA', 'Tangará', 'todos', 'e-mail, uma a uma', ''),
    (r'NESTL|GAROTO', 'Nestlé', 'todos', 'Galileu, uma a uma', 'antes de finalizar a viagem'),
    (r'LIMA E PERGHER', 'Lima e Pergher', 'so_cif', '', ''),
]
DESCARGA_LIMITE_FRETE = 500.0   # Doce Mineiro: o que já vai no frete


def regra_descarga(pagador):
    s = _sem_acento(pagador).upper()
    for rx, nome, tipo, canal, prazo in REGRAS_DESCARGA:
        if re.search(rx, s):
            return {'cliente': nome, 'tipo': tipo, 'canal': canal, 'prazo': prazo}
    return None


RE_COBR_DESCARGA = re.compile(r'DESCARG|DESCARREG|\bCHAPA\b')
RE_CARREGAMENTO = re.compile(r'CARREGAM|CARREGAMN|TRANSBORD')


def dax_cobrancas(ini):
    """Complementares e RPS com descarga na observação, emitidos desde `ini` (a cobrança vem depois do CT-e)."""
    a, _ = _dax_datas(ini, ini)
    t = "'public conhecimentos_emitidos'"
    cond = (f"({t}[tipo_documento] = \"COMPLEMENTAR FRETE\" || LEFT({t}[tipo_documento], 4) = \"SUBC\") && "
            f"({t}[data_emissao] >= {a}) && (SEARCH(\"DESCARG\", {t}[observacao], 1, 0) > 0 || "
            f"SEARCH(\"DESCARREG\", {t}[observacao], 1, 0) > 0 || SEARCH(\"CHAPA\", {t}[observacao], 1, 0) > 0)")
    return (f"EVALUATE SELECTCOLUMNS(FILTER({t}, {cond}), \"ctrc\", [serie_numero_ctrc], \"tipo\", [tipo_documento], "
            f"\"emissao\", [data_emissao], \"frete\", [valor_frete], \"obs\", [observacao], \"cnpj_pag\", [cnpj_pagador])")


def refs_da_obs(obs):
    """Referências que a observação de uma cobrança traz → {'chave', 'ctrc', 'cte_num', 'nf'} (listas).

    O SSW quebra a observação a cada 40 caracteres com vírgula — a chave do CT-e original chega partida
    ("3126010000000000000,1570840000001234100000012"): para a chave, as vírgulas saem."""
    o = _sem_acento(obs).upper()
    r = {'chave': re.findall(r'\d{44}', o.replace(',', '')),
         'ctrc': ['%s%s-%s' % m for m in re.findall(r'\b([A-Z]{3})\s?(\d{6})-(\d)\b', o)]}
    nums = []
    for m in re.finditer(r'CT-?ES?\s*:*\s*((?:\d{3,9}\s*(?:[/,E-]|A|ATE)?\s*)+)', o):
        nums += re.findall(r'\d{3,9}', m[1])
    r['cte_num'] = nums
    nfs = []
    for m in re.finditer(r'\bN\.?\s?F[S\.]?(?:-E)?S?\s*(?:N[°ºO]?\.?)?\s*:*\s*((?:\d{3,9}\s*(?:[/,E-]|A|ATE)?\s*)+)', o):
        nfs += re.findall(r'\d{3,9}', m[1])
    for m in re.finditer(r'DESCARGA\s+((?:\d{4,9}\s*(?:[/,E]|A|ATE)?\s*)+)', o):   # "DESCARGA 216866 A 216870 / 216889"
        nfs += re.findall(r'\d{4,9}', m[1])
    for a, b in re.findall(r'(\d{4,9})\s+(?:A|ATE)\s+(\d{4,9})', o):
        a, b = int(a), int(b)
        if 0 < b - a <= 50:
            nfs += [str(x) for x in range(a, b + 1)]
    r['nf'] = list(dict.fromkeys(nfs))
    return r


def pagamentos_5100(dax_dre, ini, fim, hoje=None):
    """Lançamentos do 5100 de `ini`−10 d até `fim`+75 d (a descarga é paga depois da viagem), cada um com o CT-e
    que a NF do histórico aponta (`relatorio_5100`)."""
    import relatorio_5100 as r5
    hoje = hoje or hoje_brt()
    a, b = ini - timedelta(days=10), min(hoje, fim + timedelta(days=75))
    desp, ctes = r5.coletar(None, a, b, dax=dax_dre)
    _, linhas = r5.montar(desp, ctes)
    return [{'cte': str(l.get('cte') or '').replace(' ', '').upper(), 'lanc': f"{l.get('numlancto') or ''}",
             'em': _iso(l.get('emissao')), 'forn': l.get('nome_fornecedor'), 'v': _num(l.get('vlr_final')) or 0.0,
             'hist': l.get('historico_despesa'), 'pgto': _iso(l.get('data_pgto'))}
            for l in linhas if l.get('cte')]


def _dax_ou_vazio(dax, q):
    """As tabelas da YPE são opcionais: sem elas no BI (ou com erro), a tela segue sem os dados do portal."""
    try:
        return dax(q)
    except Exception:
        return []


_CACHE = {}
CACHE_S = 600   # o 031 e o 200 chegam 8×/dia; 10 min segura a tela sem esconder o que chegou


def periodo_do_mes(mes, hoje=None):
    """'AAAA-MM' → (1º dia, último dia ou hoje)."""
    hoje = hoje or hoje_brt()
    if not mes or not re.fullmatch(r'\d{4}-\d{2}', mes):
        mes = hoje.strftime('%Y-%m')
    ini = date(int(mes[:4]), int(mes[5:]), 1)
    prox = (ini.replace(day=28) + timedelta(days=4)).replace(day=1)
    return mes, ini, min(prox - timedelta(days=1), hoje)


def dados_bi(dax, mes, forcar=False, hoje=None, dax_dre=None):
    """BI → {'ctes', 'man', 'm200', 'oc', 'ype_*', 'p5100', 'cobr'} do mês (cache de 10 min por mês).

    `dax_dre` lê o dataset do DRE (onde mora o 477); sem ele, a tela segue sem o pago da descarga."""
    mes, ini, fim = periodo_do_mes(mes, hoje)
    c = _CACHE.get(mes)
    if c and not forcar and time.time() - c['t'] < CACHE_S and (c['d'].get('p5100_lido') or not dax_dre):
        return c['d']
    folga = ini - timedelta(days=20)   # manifesto de CT-e do dia 1º pode ser do mês anterior
    d = {'mes': mes, 'ini': ini.isoformat(), 'fim': fim.isoformat(),
         'ctes': dax(dax_ctes(ini, fim)), 'man': dax(dax_manifestos(folga)),
         'm200': dax(dax_200(folga)), 'oc': dax(DAX_031),
         'ype_cte': _dax_ou_vazio(dax, DAX_YPE_CTE), 'ype_cargas': _dax_ou_vazio(dax, DAX_YPE_CARGAS),
         'ype_mon': _dax_ou_vazio(dax, dax_ype_monit(folga)),
         'cobr': _dax_ou_vazio(dax, dax_cobrancas(ini)),
         'p5100': None,
         'lido_em': datetime.now(timezone(timedelta(hours=-3))).strftime('%d/%m/%Y %H:%M')}
    if dax_dre:
        try:
            d['p5100'] = pagamentos_5100(dax_dre, ini, fim, hoje)
        except Exception:
            d['p5100'] = None   # o DRE fora do ar não derruba a tela; o pago da descarga fica vazio
        d['p5100_lido'] = True   # tentou (mesmo com erro): não relê a cada clique dentro dos 10 min
    _CACHE[mes] = {'t': time.time(), 'd': d}
    return d


# ── Montagem ──────────────────────────────────────────────────────────────────

def _num(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _transporte(obs):
    from crm_documentos import transporte_da_obs
    return transporte_da_obs(obs)


# O pedido do cliente na observação do CT-e (nov/2026: 137 de 1.603 CT-e): "PEDIDO DE COMPRA 7200000001" e
# "PEDIDO 7200000002" (Nestlé, a mesma leitura do Arquivo Nestlé) e "O.C. 600001" (ordem de compra).
_RE_PEDIDO = re.compile(r'\bPEDIDO(?:\s+DE\s+COMPRA)?\s*[:\-=Nº°.]*\s*(\d{5,})', re.I)
_RE_OC = re.compile(r'\bO\.?\s?C\.?\s*[:\-=Nº°.]*\s*(\d{4,})', re.I)


def pedido_da_obs(obs):
    """Observação do CT-e → '7200000001' / 'OC 600001' (vários: separados por vírgula), ou None."""
    s = str(obs or '')
    achou = [m[1] for m in _RE_PEDIDO.finditer(s)] + ['OC ' + m[1] for m in _RE_OC.finditer(s)]
    return ', '.join(dict.fromkeys(achou)) or None


def _nf(v):
    s = str(v or '').strip()
    return s[:-2] if s.endswith('.0') else s


def _id(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _dh(v):
    """Data-hora do BI → 'AAAA-MM-DDTHH:MM' (ou None)."""
    s = str(v or '')
    return s[:16] if re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}.*', s) and not s.startswith('0001') else None


RASTREADOR_ON, RASTREADOR_OFF = 3, 4   # código do portal (o ícone da tela de cargas), conferido em 27/11/2026


def ype_por_chave(d):
    """{chave do CT-e: dados da carga no portal da YPE} — pedido do cliente, prazo, entrega e monitoramento.

    A mesma chave pode estar em duas cargas (a YPE refaz a carga): vale a de ID maior, a mais nova.
    Monitoramento: a última foto diz o AGORA; as fotos anteriores dizem se o sinal caiu no caminho
    ("Em Monitoramento" com o rastreador offline)."""
    if not d.get('ype_cte'):
        return {}
    carga_da_chave = {}
    for r in d['ype_cte']:
        ch, i = str(r.get('chave') or '').strip(), _id(r.get('id'))
        if ch and i and i > carga_da_chave.get(ch, 0):
            carga_da_chave[ch] = i
    cargas = {_id(r.get('id')): r for r in d.get('ype_cargas') or []}
    fotos = {}
    for r in d.get('ype_mon') or []:
        fotos.setdefault(_id(r.get('id')), []).append(r)
    out_carga = {}
    for i in set(carga_da_chave.values()):
        c = cargas.get(i)
        if not c:
            continue
        n_real, n_pend, n_man = _id(c.get('n_real')) or 0, _id(c.get('n_pend')) or 0, _id(c.get('n_man')) or 0
        # "Data Realizada Última Entrega" só vale com a entrega realizada e nada pendente: sem isso o portal repete
        # o início da viagem. Baixa manual = o dia em que alguém clicou, não o da entrega (pode vir dias depois).
        real = _dh(c.get('real')) if n_real >= 1 and n_pend == 0 else None
        mon = None
        fs = sorted(fotos.get(i, []), key=lambda f: str(f.get('em') or ''))
        if fs:
            u = fs[-1]
            no_ar = [f for f in fs if str(f.get('st') or '') == 'Em Monitoramento']
            off = [f for f in no_ar if _id(f.get('r')) == RASTREADOR_OFF]
            r_u = _id(u.get('r'))
            st = str(u.get('st') or '').strip()
            if st == 'Em Monitoramento':
                estado = 'online' if r_u == RASTREADOR_ON else 'offline' if r_u == RASTREADOR_OFF else 'sem_posicao'
            elif st == 'Finalizado':
                estado = 'finalizado'
            else:
                estado = 'sem_monitoramento'
            mon = {'estado': estado, 'status': st or None, 'foto': _dh(u.get('em')), 'posicao': _dh(u.get('up')),
                   'fotos': len(fs), 'fotos_ativo': len(no_ar), 'fotos_off': len(off),
                   'primeira_off': _dh(off[0].get('em')) if off else None,
                   'ultima_off': _dh(off[-1].get('em')) if off else None}
        ped = str(c.get('ped') or '').strip()
        out_carga[i] = {
            'id': i, 'carga': str(c.get('num') or '').strip() or None, 'situacao': c.get('sit'),
            # o portal manda com zeros à esquerda ('0001234567'); a planilha e o cliente usam sem
            'pedido': ', '.join(dict.fromkeys(p.lstrip('0') for p in re.findall(r'\d+', ped))) or None,
            'prazo': _dh(c.get('prazo')) or _dh(c.get('prazo1')),
            'entrega': real, 'baixa_manual': bool(real and n_man), 'inicio': _dh(c.get('ini')), 'fim': _dh(c.get('fim')),
            'lido': _dh(c.get('lido')), 'mon': mon}
    return {ch: out_carga[i] for ch, i in carga_da_chave.items() if i in out_carga}


def _ctrc(v):
    return str(v or '').replace(' ', '').upper()


def descargas_por_cte(d, ctes, marc):
    """{CTRC: {'pago': [...], 'carregamento': [...], 'cobr': [...], 'outros': float, 'tf': 'CP'}} dos CT-e da tela.

    Uma cobrança pode citar vários CT-e (RPS "descarga das NFs 1 / 2 / 3"): o valor se reparte entre os CT-e que
    ela liga, na proporção do que foi pago (ou informado) em cada um; sem nada, em partes iguais."""
    por_ctrc = {_ctrc(c.get('ctrc')): c for c in ctes}
    por_ch, por_num, por_nf = {}, {}, {}
    for k, c in por_ctrc.items():
        ch = str(c.get('chave') or '').strip()
        rz = re.sub(r'\D', '', str(c.get('cnpj_pag') or ''))[:8]
        if len(ch) == 44:
            por_ch[ch] = k
            por_num.setdefault((rz, int(ch[25:34])), []).append(k)
        for n in re.findall(r'\d+', str(c.get('nf') or '').replace('.0', '')):
            por_nf.setdefault((rz, int(n)), []).append(k)
    out = {k: {'pago': [], 'carregamento': [], 'cobr': [], 'outros': _num(c.get('outros')) or 0.0, 'tf': c.get('tf')}
           for k, c in por_ctrc.items()}

    for p in d.get('p5100') or []:
        if p['cte'] in out:
            onde = 'carregamento' if RE_CARREGAMENTO.search(_sem_acento(p['hist']).upper()) else 'pago'
            out[p['cte']][onde].append({k: p[k] for k in ('lanc', 'em', 'forn', 'v', 'hist', 'pgto')})

    def peso(k):
        pg = sum(x['v'] for x in out[k]['pago'])
        if pg:
            return pg
        return _num(str(((marc.get(k) or {}).get('campos') or {}).get('descarga') or '').replace(',', '.')) or 0.0

    for c in d.get('cobr') or []:
        if not RE_COBR_DESCARGA.search(_sem_acento(c.get('obs')).upper()):
            continue
        r, eu = refs_da_obs(c.get('obs')), _ctrc(c.get('ctrc'))
        rz = re.sub(r'\D', '', str(c.get('cnpj_pag') or ''))[:8]
        alvo, por = [por_ch[x] for x in r['chave'] if x in por_ch] + [x for x in r['ctrc'] if x in out], 'chave/CTRC'
        if not alvo:
            alvo, por = [k for n in r['cte_num'] for k in por_num.get((rz, int(n)), [])], 'nº do CT-e'
        if not alvo:
            alvo, por = [k for n in r['nf'] for k in por_nf.get((rz, int(n)), [])], 'NF'
        alvo = [k for k in dict.fromkeys(alvo) if k != eu]
        if not alvo:
            continue
        total = _num(c.get('frete')) or 0.0
        pesos = {k: peso(k) for k in alvo}
        soma = sum(pesos.values())
        for k in alvo:
            parte = total * (pesos[k] / soma if soma else 1 / len(alvo))
            out[k]['cobr'].append({'ctrc': eu, 'tipo': c.get('tipo'), 'em': _iso(c.get('emissao')),
                                   'v': round(parte, 2), 'total': total, 'por': por, 'n': len(alvo),
                                   'obs': re.sub(r'\s+', ' ', str(c.get('obs') or ''))[:220]})
    return out


def montar(d, marc, crm=None, grupos=None, hoje=None):
    """Dados do BI + marcações → {'ctes': [...], 'viagens': {...}}.

    Cada CT-e leva o que o SSW diz (`ssw`: agenda, entrega sugerida, status sugerido, avisos) e o que a
    pessoa marcou (`m`). A tela junta por viagem e escolhe o efetivo (marcado vence o sugerido)."""
    hoje = (hoje or hoje_brt()).isoformat()
    crm = crm or {}
    man = {chave_man(m.get('chave')): m for m in d['man']}
    m200 = {chave_man(m.get('num')): m for m in d['m200']}
    oc = {}
    for o in d['oc']:
        oc.setdefault(str(o.get('ctrc') or '').replace(' ', '').upper(), []).append(o)

    ype = ype_por_chave(d)
    desc = descargas_por_cte(d, [c for c in d['ctes'] if entra(c)], marc)

    ctes, viagens = [], {}
    for c in d['ctes']:
        if not entra(c):
            continue
        ctrc = str(c.get('ctrc') or '').replace(' ', '').upper()
        emissao = _iso(c.get('emissao'))
        km = chave_man(c.get('manu'))
        # CT-e sem manifesto no SSW (out/2026: 39, quase todos coletas da Sanchez Cano com frete de rateio): a placa
        # só existe na COLETA. Os da mesma placa no mesmo dia viram uma "viagem" só, sem motorista (ele mora no
        # manifesto). A planilha TRACKING não trazia esses CT-e (1 de 39).
        placa_col = str(c.get('placa_col') or '').strip().upper()
        vid = km or (f'SEM:{placa_col}:{emissao}' if placa_col else f'SEM:{ctrc}')
        ocs = oc.get(ctrc, [])
        o15 = sorted([x for x in ocs if int(x.get('cod') or 0) == 15], key=lambda x: x.get('inc') or '')
        o35 = [x for x in ocs if int(x.get('cod') or 0) == 35]
        agenda = agenda_do_texto(o15[-1].get('compl')) if o15 else None
        ent = entrega_do_ssw([x for x in ocs if int(x.get('cod') or 0) == 1], emissao)
        yp = ype.get(str(c.get('chave') or '').strip())
        # sem baixa no SSW, a entrega do portal da YPE vira a sugestão (mesmo ✓ de confirmar). Com as duas, fica a
        # do SSW e a do portal aparece na gaveta: em nov/2026 elas batiam no mesmo dia em 9 de 22 cargas.
        if yp and yp['entrega'] and not (ent and ent.get('data')):
            ent = {'data': yp['entrega'][:10], 'hora': yp['entrega'][11:16] or None,
                   'fonte': 'Portal Ypê' + (' (baixa manual)' if yp['baixa_manual'] else '')}
        r2 = m200.get(km) if km else None
        mf = man.get(km) if km else None

        avisos = []
        if ent and ent.get('aviso'):
            avisos.append(ent['aviso'])
        # a placa que vale é a do MANIFESTO (pedido de 26/11/2026); a do CT-e só serve quando não há manifesto
        placa = str(c.get('placa') or '').strip().upper() or placa_col
        if r2 and str(r2.get('sit') or '').upper() == 'CANCELADO':
            avisos.append('manifesto CANCELADO no SSW' + (f" ({r2.get('motivo')})" if r2.get('motivo') else ''))

        # status sugerido: só o que o SSW prova
        if ent and ent.get('data'):
            onde = ent['fonte'] if ent['fonte'].startswith('Portal') else f"SSW ({ent['fonte']})"
            sug, por = 'ENTREGUE', f"entrega no {onde} em {ent['data'][8:10]}/{ent['data'][5:7]}"
        elif r2 and _iso(r2.get('chegada')):
            sug, por = 'NO_CLIENTE', f"chegada do manifesto no SSW em {_iso(r2['chegada'])[8:10]}/{_iso(r2['chegada'])[5:7]}"
        elif r2 and (_iso(r2.get('saida')) or str(r2.get('sit') or '').upper() == 'EM TRANSITO'):
            sug, por = 'TRANSITO', 'manifesto em trânsito no SSW'
        else:
            sug, por = 'CARREGANDO', ('manifesto emitido, sem saída no relatório 200 do SSW' if km
                                      else 'CT-e sem manifesto no SSW')

        mk = marc.get(ctrc) or {}
        k_crm = crm.get(ctrc) or {}
        ctes.append({
            'k': ctrc, 'v': vid, 'emissao': emissao, 'tipo': c.get('tipo'), 'nf': _nf(c.get('nf')),
            'frete': _num(c.get('frete')), 'peso': _num(c.get('peso')),
            'cliente': str(c.get('pag') or '').strip(), 'raiz': raiz_cnpj(c.get('cnpj_pag'), grupos),
            'reme': c.get('reme'), 'dest': c.get('dest'), 'cid': c.get('cid'), 'uf': c.get('uf'),
            'cid_o': c.get('cid_o'), 'uf_o': c.get('uf_o'), 'placa': placa, 'rps': eh_rps(c),
            # a coluna B10 da aba Nestlé da planilha: a carga na observação (a mesma leitura do CRM)
            'b10': _transporte(c.get('obs')),
            'pedido': pedido_da_obs(c.get('obs')),
            'ype': yp,
            # descarga: o destinatário da viagem é a unidade (`g`); a regra do cliente decide o que se cobra
            'desc': dict(desc.get(ctrc) or {}, g=f"{vid}|{raiz_cnpj(c.get('cnpj_pag'), grupos)}|{_sem_acento(c.get('dest')).upper().strip()}",
                         regra=regra_descarga(c.get('pag'))),
            'ssw': {'agenda': agenda, 'aguardando_agenda': bool(o35) and not o15,
                    'entrega': ent, 'status': sug, 'status_por': por, 'avisos': avisos},
            'crm': k_crm or None,
            'm': mk.get('campos') or {}, 'por': mk.get('por'), 'em': mk.get('em'), 'fontes': mk.get('fontes') or {},
        })
        if vid not in viagens:
            viagens[vid] = {
                'v': vid, 'manifesto': km,
                'mot': (mf or {}).get('mot') or (r2 or {}).get('mot'),
                'placa': ((mf or {}).get('placa') or (r2 or {}).get('placa') or ('' if km else placa) or '').upper(),
                'carreta': ((mf or {}).get('carreta') or c.get('carreta') or '').upper(),
                'prop': (mf or {}).get('prop'),
                'saida': _iso_hora(r2.get('saida'), r2.get('saida_h')) if r2 else None,
                'prev': _iso_hora(r2.get('prev'), r2.get('prev_h')) if r2 else None,
                'chegada': _iso_hora(r2.get('chegada'), r2.get('chegada_h')) if r2 else None,
                'sit200': (r2 or {}).get('sit'), 'orig200': (r2 or {}).get('orig'), 'dest200': (r2 or {}).get('dest'),
                'emissao_man': _iso((mf or {}).get('emi')) or _iso((r2 or {}).get('emissao')),
            }
    for v in viagens.values():
        v['atrasada'] = bool(v['prev'] and not v['chegada'] and v['prev'][:10] < hoje)
    return {'ctes': ctes, 'viagens': viagens}


# ── Banco: marcações e histórico ──────────────────────────────────────────────

DDL = """
CREATE TABLE IF NOT EXISTS acomp_marcacao (
    chave          TEXT PRIMARY KEY,               -- CTRC (formato do conhecimentos_emitidos)
    campos         JSONB NOT NULL DEFAULT '{}'::jsonb,
    atualizado_em  TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_por TEXT
);
CREATE TABLE IF NOT EXISTS acomp_log (
    id     BIGSERIAL PRIMARY KEY,
    chave  TEXT NOT NULL,
    campo  TEXT NOT NULL,
    antes  TEXT,
    depois TEXT,
    por    TEXT,
    em     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS acomp_log_chave ON acomp_log (chave, em);
"""


def garantir_tabelas(cur):
    cur.execute(DDL)


def marcacoes(cur, chaves=None):
    if chaves is not None and not chaves:
        return {}
    sql = ("SELECT chave, campos, to_char(atualizado_em AT TIME ZONE 'America/Sao_Paulo', 'DD/MM HH24:MI'), "
           "atualizado_por FROM acomp_marcacao")
    if chaves is None:
        cur.execute(sql)
    else:
        cur.execute(sql + ' WHERE chave = ANY(%s)', (list(chaves),))
    out = {k: {'campos': c or {}, 'em': e, 'por': p, 'fontes': {}} for k, c, e, p in cur.fetchall()}
    if not out:
        return out
    # quem gravou cada campo por último ("Planilha TRACKING" = veio da importação da planilha): a tela mostra
    # de onde veio a entrega/agenda/status de cada CT-e
    cur.execute("""SELECT DISTINCT ON (chave, campo) chave, campo, por FROM acomp_log
                    WHERE chave = ANY(%s) ORDER BY chave, campo, em DESC, id DESC""", (list(out),))
    for k, campo, por in cur.fetchall():
        out[k]['fontes'][campo] = por
    return out


def crm_dos_ctes(cur, chaves, carteiras):
    """Etapa e custo extra do CRM - Documentos (só leitura): {ctrc: {'carteira', 'etapa', 'rot', 'cor', 'custos'}}."""
    if not chaves:
        return {}
    cur.execute("SELECT to_regclass('crm_doc_marcacao') IS NOT NULL")
    if not cur.fetchone()[0]:
        return {}
    cur.execute("SELECT chave, carteira, etapa, campos -> 'custos' FROM crm_doc_marcacao WHERE chave = ANY(%s)",
                (list(chaves),))
    out = {}
    for k, cart, etapa, custos in cur.fetchall():
        cfg = carteiras.get(cart) or {}
        e = next((x for x in cfg.get('etapas', []) if x.get('cod') == etapa), None)
        itens = custos if isinstance(custos, list) else []
        out[k] = {'carteira': cfg.get('nome') or cart, 'etapa': etapa, 'rot': (e or {}).get('rotulo') or etapa,
                  'cor': (e or {}).get('cor'), 'custos': [
                      {'tipo': i.get('tipo'), 'valor': _num(i.get('valor')), 'status': i.get('status')}
                      for i in itens if isinstance(i, dict)]}
    return out


def _brt(ts):
    """Hora gravada pelo Embarques (UTC, sem fuso — o worker usa utcnow/NOW()) → 'AAAA-MM-DDTHH:MM' de Brasília."""
    return (ts - timedelta(hours=3)).strftime('%Y-%m-%dT%H:%M') if ts else None


def cargas_dos_manifestos(cur, manifestos):
    """{manifesto: carga do Embarques} — o robô abre a carga a partir do manifesto (`manifesto_origem`); o id é o do
    mapa (/embarques/cargas/<id>/mapa) e da ficha (/viagem/<id>). Mesma escolha do `viagem.resolver`: sem perna
    vazia nem cancelada, a de carregamento mais recente.

    Vem junto o STATUS do Embarques, que é o "status embarque" da viagem (27/11/2026): o worker do rastreamento põe
    'Em rota' quando o GPS sai da origem e 'No destino' depois de 60 min parado na cidade da descarga."""
    mans = [re.sub(r'[^A-Z0-9]', '', m.upper()) for m in manifestos if m]
    if not mans:
        return {}
    cur.execute("SELECT to_regclass('embarques_cargas') IS NOT NULL")
    if not cur.fetchone()[0]:
        return {}
    cur.execute("""SELECT DISTINCT ON (m) m, id, status, origem, destinos, saida, no_local, conclusao, desengate,
                          desengate_local, continua_em FROM (
                       SELECT regexp_replace(upper(c.manifesto_origem), '[^A-Z0-9]', '', 'g') AS m, c.id, c.status,
                              c.data_carregamento, c.origem_cidade || '/' || c.origem_uf AS origem,
                              (SELECT string_agg(d.cidade || '/' || d.uf, ' · ' ORDER BY d.ordem)
                                 FROM embarques_cargas_destinos d WHERE d.carga_id = c.id) AS destinos,
                              c.data_saida_real AS saida, c.no_local_desde AS no_local, c.data_conclusao AS conclusao,
                              c.desengatada_em AS desengate, c.desengate_local, c.continua_em
                         FROM embarques_cargas c
                        WHERE c.manifesto_origem IS NOT NULL AND NOT COALESCE(c.viagem_vazia, FALSE)
                          AND c.status <> 'Cancelada') x
                    WHERE m = ANY(%s) ORDER BY m, data_carregamento DESC NULLS LAST, id DESC""", (mans,))
    out = {}
    for m, i, st, orig, dests, saida, no_local, concl, deseng, deseng_local, continua in cur.fetchall():
        out[m] = {'id': i, 'status': st, 'origem': orig, 'destinos': dests, 'saida': _brt(saida),
                  'no_local': _brt(no_local), 'conclusao': _brt(concl), 'desengate': _brt(deseng),
                  'desengate_local': deseng_local, 'continua_em': continua}
    return out


def normalizar(campo, valor):
    """Valor digitado → o que se grava (None apaga). Levanta ValueError com a mensagem para a tela."""
    if campo not in CAMPOS:
        raise ValueError('Campo desconhecido.')
    if valor is None or str(valor).strip() == '':
        return None
    s = str(valor).strip()
    tipo = CAMPOS[campo]
    if tipo == 'status':
        if s not in STATUS_COD:
            raise ValueError('Status fora da lista.')
        return s
    if tipo in ('data', 'data_hora'):
        m = re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?', s) or \
            re.fullmatch(r'(\d{2})/(\d{2})/(\d{2,4})(?:\s+(\d{2}):(\d{2}))?', s)
        if not m:
            raise ValueError('Data inválida — use dd/mm/aaaa.')
        if '/' in s:
            d = f'{_ano(m[3])}-{m[2]}-{m[1]}'
        else:
            d = f'{m[1]}-{m[2]}-{m[3]}'
        try:
            date.fromisoformat(d)
        except ValueError:
            raise ValueError('Data inválida.')
        if tipo == 'data_hora' and m[4]:
            return f'{d}T{m[4]}:{m[5]}'
        return d
    if tipo == 'valor':
        v = re.sub(r'[R$\s]', '', s)
        if re.fullmatch(r'\d{1,3}(\.\d{3})*(,\d{1,2})?|\d+(,\d{1,2})?', v):
            v = v.replace('.', '').replace(',', '.')
        elif not re.fullmatch(r'\d+(\.\d{1,2})?', v):
            raise ValueError('Valor inválido — use 1.234,56.')
        n = float(v)
        if n < 0 or n >= 100000:
            raise ValueError('Valor fora do esperado para uma descarga.')
        return f'{n:.2f}'
    return s[:300]


def gravar(cur, chaves, campo, valor, por):
    """Grava `campo` = `valor` nas `chaves` (CT-e), com log de cada mudança. Devolve {chave: antes} das que mudaram."""
    mudou = {}
    for k in chaves:
        cur.execute('SELECT campos FROM acomp_marcacao WHERE chave = %s FOR UPDATE', (k,))
        r = cur.fetchone()
        campos = (r[0] if r else None) or {}
        antes = campos.get(campo)
        if (antes or None) == (valor or None):
            continue
        if valor is None:
            campos.pop(campo, None)
        else:
            campos[campo] = valor
        cur.execute("""INSERT INTO acomp_marcacao (chave, campos, atualizado_em, atualizado_por)
                       VALUES (%s, %s::jsonb, now(), %s)
                       ON CONFLICT (chave) DO UPDATE SET campos = EXCLUDED.campos, atualizado_em = now(),
                                                         atualizado_por = EXCLUDED.atualizado_por""",
                    (k, json.dumps(campos, ensure_ascii=False), por))
        cur.execute('INSERT INTO acomp_log (chave, campo, antes, depois, por) VALUES (%s, %s, %s, %s, %s)',
                    (k, campo, antes, valor, por))
        mudou[k] = antes
    return mudou


def historico(cur, chave):
    cur.execute("""SELECT campo, antes, depois, por, to_char(em AT TIME ZONE 'America/Sao_Paulo', 'DD/MM/YYYY HH24:MI')
                     FROM acomp_log WHERE chave = %s ORDER BY em DESC, id DESC LIMIT 200""", (chave,))
    return [{'campo': c, 'antes': a, 'depois': d, 'por': p, 'em': e} for c, a, d, p, e in cur.fetchall()]


def ocorrencias_do_cte(d, chave):
    """As ocorrências 01/15/35 do SSW do CT-e (para a gaveta), da mais nova para a mais velha."""
    rot = {1: 'Mercadoria entregue', 15: 'Entrega agendada', 35: 'Aguardando agendamento'}
    out = [{'cod': int(o.get('cod') or 0), 'rot': rot.get(int(o.get('cod') or 0)), 'em': str(o.get('oc') or '')[:16],
            'texto': o.get('compl')}
           for o in d['oc'] if str(o.get('ctrc') or '').replace(' ', '').upper() == chave]
    return sorted(out, key=lambda x: x['em'], reverse=True)
