"""
Gerador do arquivo de importação contábil (HANDOFF-CONTABIL §14 e §15).

Só lógica: recebe as linhas já lidas (477, 456, 441, configuração) e devolve os
lançamentos, as pendências e as conferências. Não fala com banco nem com Power
BI — o server.py busca e chama os métodos. Assim dá para testar com dado fixo
(_teste_contabil_gerador.py).

O que entra (cada linha do arquivo é UM débito e UM crédito):

  despesa         477 da competência, evento com DESPESA = SIM
                  D conta do evento / C fornecedores (FORNECEDOR_PADRAO)
  pagamento       456 CPG ligado ao 477 pela chave (uni, numlancto, parcela)
                  D conta do contrato | fornecedores (PROVISÃO=SIM) | conta do
                  evento (PROVISÃO=NÃO)  /  C banco — pelo valor pago
                  + juros:    D JUROS_PAGOS / C a mesma conta    (pela liquidação)
                  + desconto: D a mesma conta / C DESCONTOS_OBTIDOS
  lote RET BCO    456 "DEBITO VIA RET BCO" detalhado pelas liquidações do 477
                  naquela conta e dia que NÃO aparecem uma a uma no 456 (PIX e
                  boleto) — só quando a soma fecha ao centavo; tudo ou nada
  transferência   456 entre contas próprias: CADA PONTA contra a ponte 1453
                  (TRANSFERENCIA_ENTRE_CONTAS, contabilidade 14/10) — saída
                  D 1453 / C banco, entrada D banco / C 1453; a 1453 zera.
                  Para conta fora do 456 (Pecúnia), a outra ponta é lançada
                  junto, a partir do histórico "TRANSF CTA … P CTA …"
  pagamento fora  liquidação do 477 por conta que o robô do 456 não baixa
  do 456          (Pecúnia): mesmo lançamento do pagamento, pela data da
                  liquidação — desliga sozinho quando a conta entrar no 456
  recebimento     456 FAT (CLIENTE): banco × CLIENTE_PADRAO (504)
  adiantamento    456 ACN (ADTO CLIENTE): banco × ADTO_CLIENTE (844)
  desc. duplicata 456 "CREDITO VIA RET BCO… OU CART DESCONTO" e FDBI: banco ×
                  DESCONTO_DUPLICATA (591)
                  — estes três pelo SINAL do extrato: crédito = D banco / C conta,
                    débito = D conta / C banco. Estorno de um deles (que o SSW
                    registra com sinal trocado) vira o contrário exato, e os pares
                    entre contas próprias (FDBI Bradesco → DD Solar) se anulam
                    na conta de contrapartida.
  juros/desc 441  fatura liquidada no mês com juros ou desconto (data da
                  liquidação, como a especificação manda):
                  juros D CLIENTE_PADRAO / C JUROS_RECEBIDOS;
                  desconto D DESCONTOS_CONCEDIDOS / C CLIENTE_PADRAO
                  (o recebimento credita a 504 pelo valor pago; com estes dois, a
                  504 fecha no valor da fatura)

Tudo o mais fica de fora COM MOTIVO — nunca calado, nunca torto:
  - pendência: era para entrar e falta algo (conta, valor diverge…);
  - fora: regra que a especificação não cobre ou que aguarda resposta.

Formato (PARA GABRIEL - MODELO IMPORTAÇÃO.txt, conferido byte a byte):
    X\r\n
    Z;DD/MM/AAAA;DÉBITO;CRÉDITO;VALOR;HISTÓRICO;COMPLEMENTO\r\n
UTF-8, CRLF, uma linha "X" antes de cada "Z", valor com vírgula e 2 casas,
contas pelo CÓDIGO REDUZIDO, histórico 2.

⚠ Provisório, para validar no primeiro teste com a contabilidade:
  - texto do complemento (é convenção dela; o modelo só mostra o do pagamento);
  - data da despesa: a data contábil do 477 quando cai no mês da competência,
    senão o último dia do mês (89% cai no mês — medido em 14/10/2026);
  - codificação UTF-8 (é a do modelo que ela mandou).
"""

import calendar
import re
import unicodedata
from datetime import date, datetime

HISTORICO = 2
VERSAO = '1.1 (14/10/2026 — com as respostas da contabilidade)'

# Motivos de pendência: o que é, e de quem depende resolver.
MOTIVOS = {
    'evento_sem_config':  ('Evento sem configuração (flags de despesa/provisão)', 'contabilidade'),
    'evento_sem_conta':   ('Evento precisa de conta contábil e está sem', 'contabilidade'),
    'contrato_sem_conta': ('Contrato (consórcio/financiamento) sem conta — contrato novo?', 'contabilidade'),
    'banco_sem_conta':    ('Conta bancária sem conta contábil', 'contabilidade'),
    'conta_fixa_ausente': ('Conta fixa do processo não preenchida (tela Contas fixas)', 'contabilidade'),
    'valor_diverge':      ('Valor do banco diferente do valor final do 477', 'análise'),
    'sem_477':            ('Pagamento sem a despesa correspondente no 477', 'análise'),
    'cpg_credito':        ('Contas a pagar lançado a CRÉDITO no banco (devolução/estorno)', 'análise'),
    'lote_nao_fecha':     ('Lote do RET BCO não fecha com as liquidações do 477 do dia', 'análise'),
    'excecao_sd':         ('Regra da contadora: lançamento que começa com SD não busca no 477', 'contabilidade'),
    'conta_invalida':     ('Conta inexistente ou sintética no plano', 'análise'),
}

# Regra do 456 que nenhum método lança — com o porquê, para a tela.
FORA = {
    'ESTORNO':            'Estorno manual de algo que também não tem regra (sinistro, empréstimo, juros de TD…)',
    'SEM REGRA':          'Lançamento manual sem padrão reconhecido (ver HANDOFF §17)',
}

# Nome de cada tipo de lançamento, para a tela e as conferências.
TIPOS_NOME = {
    'recebimento': 'Recebimentos (FAT, fatura manual, estorno de fatura, RET BCO sem fatura)',
    'adiantamento': 'Adiantamento de cliente e crédito não identificado (844)',
    'desconto_duplicata': 'Desconto de duplicata (RET BCO crédito, FDBI, baixa de boleto, liberação TD)',
    'acerto_ctrb': 'Acertos manuais de pagamento de CTRB (506)',
    'acerto_fornecedor': 'Pagamento indevido/duplicado/a maior a fornecedor e devoluções (506)',
    'financeiro': 'Conta garantida, aplicação, rendimento, juros de TD, tarifa, cota Sicoob',
    'socios_ligadas': 'Sócio e empresas do grupo (Cleivon, Singular)',
    'sinistro_imobilizado': 'Sinistro/indenização e compra/venda de veículo',
    'transferencia': 'Transferência entre contas próprias (ponte 1453)',
    'transferencia_externa': 'Transferência para conta fora do 456 (ponte 1453, as duas pontas)',
    'pagamento_fora_456': 'Pagamento por conta fora do 456 (liquidação do 477)',
    'aplicacao_adiantamento': 'Aplicação de adiantamento na fatura (571): 844 → 504',
}

# Lançamento MANUAL que a especificação não cobre (o SWITCH do 456 o deixa em
# SEM REGRA/ESTORNO), reconhecido pelo histórico. A primeira regra que casa vale;
# o estorno de cada um cai na MESMA regra e, lançado pelo sinal, desfaz o
# original. Varredura de 14/10/2026 — base de cada conta no HANDOFF §17.
REGRAS_MAN = [
    (r'FATURA:', 'FATURA MANUAL'),
    (r'BAIXA (DE )?BOLETO|BAIXA DE TITULO', 'BAIXA BOLETO'),
    (r'LIBERACAO TD', 'LIBERACAO TD'),
    (r'JUROS (DE )?TD|DEV\.? JUROS|JUROS REF PAGTO', 'JUROS TD'),
    (r'CLEIVON', 'SOCIO CLEIVON'),
    (r'SINGULAR SERVICOS', 'SINGULAR SERVICOS'),
    (r'SINGULAR FLEET', 'SINGULAR FLEET'),
    (r'CONTA GARANTIDA', 'CONTA GARANTIDA'),
    (r'SINISTRO|INDENIZACAO|SOMPO', 'SINISTRO'),
    (r'CARRETA|VENDA BAU', 'IMOBILIZADO'),
    (r'CDB|APLICACAO AUTOMATICA|RESGATE AUTOMATICA', 'APLICACAO'),
    (r'RENTABILIDADE|R?RENDIM', 'RENDIMENTO'),
    (r'INTEGRALIZACAO', 'COTA CAPITAL'),
    (r'TARIFA', 'DEV TARIFA'),
    (r'TRANSF CTA', 'TRANSF EXTERNA'),
    (r'NAO IDENTIFICADO|CRED\. INDEVIDO|INDEVIDO TRANSPORTES', 'CREDITO NAO IDENTIFICADO'),
    (r'INDEVIDO|DUPLICIDADE|\bDUPL|PAGTO (A )?MAIOR|PG TOTAL|\bDEV\b|\bDEV\.|DEVOL|DESCARGA|'
     r'MECANICA|PERSETO|RENATO|ADIANT|SALDO|\bDESC\.|TESTE|MVF|CLEBER|LOURIMAN|REC\. DIF',
     'ACERTO FORNECEDOR'),
]


def classificar_man(historico):
    """Regra para um lançamento manual do 456, ou None (fica fora do arquivo)."""
    h = norm_txt(historico)
    for padrao, regra in REGRAS_MAN:
        if re.search(padrao, h):
            return regra
    return None

# Regra do 456 → (tipo do lançamento, conta fixa de contrapartida, rótulo)
CONTRAPARTIDA = {
    'CLIENTE':         ('recebimento', 'CLIENTE_PADRAO', 'RECEBIMENTO'),
    'ADTO CLIENTE':    ('adiantamento', 'ADTO_CLIENTE', 'ADTO CLIENTE'),
    'RET BCO CREDITO': ('desconto_duplicata', 'DESCONTO_DUPLICATA', 'DESC DUPLICATA'),
    'DESC DUPLICATA':  ('desconto_duplicata', 'DESCONTO_DUPLICATA', 'DESC DUPLICATA'),
    # "o lançamento deve ser o exato contrário do documento estornado": o estorno
    # de um recebimento vai contra a mesma 504, com o sinal do extrato
    'ESTORNO FATURA':  ('recebimento', 'CLIENTE_PADRAO', 'ESTORNO RECEBIMENTO'),
    # crédito de cobrança sem fatura identificada: cliente pagou, e o recebimento
    # é "no momento somente a 504" (contabilidade, 14/10/2026)
    'RET BCO SEM FATURA': ('recebimento', 'CLIENTE_PADRAO', 'RECEBIMENTO SEM FATURA'),
    # acerto manual de pagamento de CTRB: eventos 5101/5102, PROVISÃO = SIM → 506
    'ACERTO CTRB':     ('acerto_ctrb', 'FORNECEDOR_PADRAO', 'ACERTO CTRB'),
    # --- lançamentos manuais reconhecidos por classificar_man (HANDOFF §17) ---
    # '{banco}' = a conta bancária da linha; '{outra_ponta}' = a outra conta do
    # "TRANSF CTA b a c P CTA b a c". Toda conta mora em contabil_conta_fixa.
    'FATURA MANUAL':   ('recebimento', 'CLIENTE_PADRAO', 'FATURA MANUAL'),
    'BAIXA BOLETO':    ('desconto_duplicata', 'DESCONTO_DUPLICATA', 'BAIXA BOLETO'),
    'LIBERACAO TD':    ('desconto_duplicata', 'DESCONTO_DUPLICATA', 'LIBERACAO TD'),
    'CREDITO NAO IDENTIFICADO': ('adiantamento', 'ADTO_CLIENTE', 'CREDITO NAO IDENTIFICADO'),
    'ACERTO FORNECEDOR': ('acerto_fornecedor', 'FORNECEDOR_PADRAO', 'ACERTO FORNECEDOR'),
    'JUROS TD':        ('financeiro', 'JUROS_TD', 'JUROS TD'),
    'CONTA GARANTIDA': ('financeiro', 'GARANTIDA:{banco}', 'CONTA GARANTIDA'),
    'APLICACAO':       ('financeiro', 'APLICACAO:{banco}', 'APLICACAO'),
    'RENDIMENTO':      ('financeiro', 'RENDIMENTO_APLICACAO', 'RENDIMENTO'),
    'COTA CAPITAL':    ('financeiro', 'COTA_CAPITAL:{banco}', 'COTA CAPITAL'),
    'DEV TARIFA':      ('financeiro', 'TARIFA_BANCARIA', 'DEV TARIFA'),
    'SOCIO CLEIVON':   ('socios_ligadas', 'SOCIO_CLEIVON', 'CLEIVON'),
    'SINGULAR SERVICOS': ('socios_ligadas', 'SINGULAR_SERVICOS', 'SINGULAR SERVICOS'),
    'SINGULAR FLEET':  ('socios_ligadas', 'SINGULAR_FLEET', 'SINGULAR FLEET'),
    'SINISTRO':        ('sinistro_imobilizado', 'SINISTRO', 'SINISTRO'),
    'IMOBILIZADO':     ('sinistro_imobilizado', 'IMOBILIZADO_VEICULOS', 'VEICULO'),
    'TRANSF EXTERNA':  ('transferencia_externa', 'TRANSFERENCIA_ENTRE_CONTAS', 'TRANSF'),
    'TRANSFERENCIA':   ('transferencia', 'TRANSFERENCIA_ENTRE_CONTAS', 'TRANSF ENTRE CONTAS'),
}

# Juros e tarifa pagos pela Pecúnia vão na 374 (contabilidade, 14/10/2026), não na
# conta do evento (372/781 dos demais bancos). Chave por banco em contas fixas.
EVENTOS_JUROS_TARIFA = ('5312', '5317')

_RE_TRANSF = re.compile(r'TRANSF CTA\s+(\d+)\s+(\d+)\s+(\d+)\s+P CTA\s+(\d+)\s+(\d+)\s+(\d+)')


# ── utilidades ──────────────────────────────────────────────────────────────

def _data(v):
    if v is None or v == '':
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.fromisoformat(str(v)[:19]).date()


def _ultimo_dia(mes):
    a, m = int(mes[:4]), int(mes[5:7])
    return date(a, m, calendar.monthrange(a, m)[1])


def _no_mes(d, mes):
    return d is not None and f'{d.year:04d}-{d.month:02d}' == mes


def norm_num(v):
    """'038112', ' 91440', 91440 -> '38112' / '91440' (456 e 477 preenchem diferente)."""
    return str(v if v is not None else '').strip().lstrip('0') or '0'


def norm_txt(v):
    s = unicodedata.normalize('NFKD', str(v or '').strip().upper())
    return ''.join(c for c in s if not unicodedata.combining(c))


def chave_477(uni, numlancto, parcela):
    return (norm_txt(uni), norm_num(numlancto), norm_num(parcela))


def chave_banco(banco, agencia, conta):
    return f'BANCO:{norm_num(banco)}/{norm_num(agencia)}/{norm_num(conta)}'


def valor_br(v):
    return f'{abs(v):.2f}'.replace('.', ',')


def _complemento(txt, limite=200):
    t = re.sub(r'[;\r\n\t]+', ' ', str(txt or ''))
    return re.sub(r'\s+', ' ', t).strip()[:limite]


def _r2(v):
    return round(float(v or 0), 2)


# ── o motor ─────────────────────────────────────────────────────────────────

class Gerador:
    def __init__(self, mes, plano, eventos, contratos, fixas):
        """
        plano     {codigo: (classificacao, descricao, analitica)}
        eventos   {evento: {'conta_debito': cod|None, 'contabiliza_despesa': 'SIM'|…,
                            'contabiliza_provisao': 'SIM'|'NAO'|None}}
        contratos {(evento, numlancto normalizado): cod|None}
        fixas     {chave: cod|None} — FORNECEDOR_PADRAO, CLIENTE_PADRAO, JUROS_PAGOS…
                  e as contas bancárias 'BANCO:b/a/c'
        """
        self.mes = mes
        self.plano, self.eventos, self.contratos, self.fixas = plano, eventos, contratos, fixas
        self.eventos_de_contrato = {ev for ev, _ in contratos}
        self.fornecedor = fixas.get('FORNECEDOR_PADRAO')
        self.lancamentos, self.pendencias = [], []

    # -- infraestrutura -----------------------------------------------------
    def _valida(self, cod):
        p = self.plano.get(cod)
        return bool(p and p[2])

    def _pendencia(self, tipo, motivo, valor, data_, ident, detalhe=''):
        self.pendencias.append({'tipo': tipo, 'motivo': motivo, 'valor': _r2(valor),
                                'data': data_.isoformat() if data_ else None,
                                'identificacao': ident, 'detalhe': detalhe})

    def _lanca(self, tipo, data_, partidas, origem, ident, valor_pend):
        """Grava as partidas de UM fato, todas ou nenhuma.

        partidas = [(papel, debito, credito, valor, complemento)]. O papel
        'principal' é a partida que mexe no banco/na origem — é a que as
        conferências somam; 'juros'/'desconto' são as que a acompanham."""
        # Partida de valor zero não existe; partida com débito = crédito não tem
        # efeito — acontece no evento 5312 JUROS E ENCARGOS, cuja conta já é a
        # própria 372 de juros (o pagamento inteiro já cai nela).
        partidas = [p for p in partidas if _r2(p[3]) > 0 and p[1] != p[2]]
        for _, d, c, _, _ in partidas:
            for cod in (d, c):
                if not self._valida(cod):
                    self._pendencia(tipo, 'conta_invalida', valor_pend, data_, ident, f'conta {cod}')
                    return False
        for papel, d, c, v, comp in partidas:
            self.lancamentos.append({'tipo': tipo, 'papel': papel, 'data': data_, 'debito': d,
                                     'credito': c, 'valor': _r2(v),
                                     'complemento': _complemento(comp), 'origem': origem})
        return True

    def _conta_pagamento(self, d, cb=None):
        """Conta debitada no pagamento de uma despesa do 477, ou (None, motivo).
        `cb` = chave da conta bancária que pagou (para regra por banco)."""
        ev = str(d.get('evento') or '').strip()
        ct = self.contratos.get((ev, norm_num(d.get('numlancto'))))
        if ct:
            return ct, None
        if ev in EVENTOS_JUROS_TARIFA and self.fixas.get(f'JUROS_TARIFAS:{cb}'):
            return self.fixas[f'JUROS_TARIFAS:{cb}'], None
        if ev in self.eventos_de_contrato:
            return None, 'contrato_sem_conta'
        cfg = self.eventos.get(ev)
        if not cfg or not cfg.get('contabiliza_provisao'):
            return None, 'evento_sem_config'
        if cfg['contabiliza_provisao'] == 'SIM':
            return self.fornecedor, None
        return (cfg.get('conta_debito'), None) if cfg.get('conta_debito') else (None, 'evento_sem_conta')

    # -- 1. despesa por competência ----------------------------------------
    def despesas(self, linhas_477):
        ultimo = _ultimo_dia(self.mes)
        for d in linhas_477:
            ev = str(d.get('evento') or '').strip()
            cfg = self.eventos.get(ev)
            ident = f"{d.get('uni')} {norm_num(d.get('numlancto'))}/{norm_num(d.get('parcela'))} ev {ev}"
            dc = _data(d.get('contabil'))
            data_ = dc if _no_mes(dc, self.mes) else ultimo
            valor = d.get('vlr_parcela') or 0
            if not cfg or not cfg.get('contabiliza_despesa'):
                self._pendencia('despesa', 'evento_sem_config', valor, data_, ident)
                continue
            if cfg['contabiliza_despesa'] != 'SIM':
                continue        # NÃO/PARCIAL: entra pela escrita fiscal (decisão de 15/09)
            if not cfg.get('conta_debito'):
                self._pendencia('despesa', 'evento_sem_conta', valor, data_, ident)
                continue
            self._lanca('despesa', data_, [(
                'principal', cfg['conta_debito'], self.fornecedor, valor,
                f"NF {d.get('nfiscal') or '-'} DE {d.get('nome_fornecedor') or ''} - "
                f"{d.get('descr_evento') or ''} - {norm_num(d.get('numlancto'))}/"
                f"{norm_num(d.get('parcela'))}")],
                {'fonte': '477', 'chave': ident, 'ref': d.get('REF')}, ident, valor)

    # -- 2. pagamento (456 CPG) e 3. lote RET BCO ---------------------------
    def _paga_documento(self, tipo, d, data_, cod_banco, origem, ident, cb=None):
        """Pagamento de uma despesa do 477: valor pago contra o banco, e juros e
        desconto separados nas contas fixas (especificação: "conta fixa JUROS /
        DESCONTOS OBTIDOS, conta variável conforme evento da despesa")."""
        pago = d.get('vlr_final') or 0
        juros, desc = _r2(d.get('juros')), _r2(d.get('descontos'))
        conta, motivo = self._conta_pagamento(d, cb)
        if motivo:
            self._pendencia(tipo, motivo, pago, data_, ident)
            return False
        cj, cd = self.fixas.get('JUROS_PAGOS'), self.fixas.get('DESCONTOS_OBTIDOS')
        if (juros and not cj) or (desc and not cd):
            self._pendencia(tipo, 'conta_fixa_ausente', pago, data_, ident, 'JUROS_PAGOS/DESCONTOS_OBTIDOS')
            return False
        base = (f"{norm_num(d.get('numlancto'))}/{norm_num(d.get('parcela'))} DE "
                f"{d.get('nome_fornecedor') or ''} - {d.get('descr_evento') or ''}")
        return self._lanca(tipo, data_, [
            ('principal', conta, cod_banco, pago, base),
            ('juros', cj, conta, juros, 'JUROS ' + base),
            ('desconto', conta, cd, desc, 'DESCONTO ' + base),
        ], origem, ident, pago)

    def pagamentos(self, mov_cpg, despesas_por_chave):
        for m in mov_cpg:
            data_ = _data(m.get('data'))
            ident = f"{m.get('conta_apelido')} nlanca {m.get('nlanca')}"
            valor = -(m.get('valor') or 0)
            if str(m.get('historico') or '').upper().startswith('SD'):
                self._pendencia('pagamento', 'excecao_sd', valor, data_, ident)
                continue
            if valor <= 0:
                self._pendencia('pagamento', 'cpg_credito', -valor, data_, ident)
                continue
            cod_banco = self.fixas.get(chave_banco(m.get('banco'), m.get('agencia'), m.get('conta')))
            if not cod_banco:
                self._pendencia('pagamento', 'banco_sem_conta', valor, data_, ident, m.get('conta_apelido'))
                continue
            k = chave_477(m.get('ref_477_uni'), m.get('ref_477_numlancto'), m.get('ref_477_parcela'))
            d = despesas_por_chave.get(k)
            if not d:
                self._pendencia('pagamento', 'sem_477', valor, data_, ident, '/'.join(k))
                continue
            if abs(_r2(d.get('vlr_final')) - _r2(valor)) >= 0.01:
                self._pendencia('pagamento', 'valor_diverge', valor, data_, ident,
                                f"banco {valor:.2f} × 477 {d.get('vlr_final') or 0:.2f}")
                continue
            self._paga_documento('pagamento', d, data_, cod_banco,
                                 {'fonte': '456+477', 'nlanca': m.get('nlanca'),
                                  'chave_477': '/'.join(k)}, ident,
                                 chave_banco(m.get('banco'), m.get('agencia'), m.get('conta')))

    def lotes_ret_bco(self, mov_lote, liquidacoes, chaves_cpg):
        """Detalha o "DEBITO VIA RET BCO" pelas liquidações do 477.

        Medido em 14/10/2026: o lote do dia é a soma de TODAS as liquidações do
        477 naquela conta e data de extrato que não estão no 456 uma a uma (não
        têm linha CPG) — PIX e boleto. Fechou ao centavo em todos os lotes de
        2026. A primeira hipótese, só boleto, fechava apenas nos dias sem PIX.
        Só lança o lote que fecha; o resto é pendência com a diferença à vista."""
        por_dia = {}
        for m in mov_lote:
            k = (chave_banco(m.get('banco'), m.get('agencia'), m.get('conta')), _data(m.get('data')))
            g = por_dia.setdefault(k, {'apelido': m.get('conta_apelido'), 'valor': 0.0})
            g['valor'] += -(m.get('valor') or 0)
        docs = {}
        for b in liquidacoes:
            k = chave_477(b.get('uni'), b.get('numlancto'), b.get('parcela'))
            if k in chaves_cpg:
                continue    # tem linha própria (CPG) no 456: não é deste lote
            kk = (chave_banco(b.get('liq_ban'), b.get('liq_agen'), b.get('liq_ctacorrent')),
                  _data(b.get('liq_data_extrato')))
            docs.setdefault(kk, []).append(b)

        for (cb, dia), g in sorted(por_dia.items(), key=lambda x: (x[0][1], x[0][0])):
            ident = f"{g['apelido']} {dia.isoformat()} lote RET BCO"
            cod_banco = self.fixas.get(cb)
            if not cod_banco:
                self._pendencia('lote', 'banco_sem_conta', g['valor'], dia, ident)
                continue
            ds = docs.get((cb, dia), [])
            soma = sum(_r2(d.get('liq_valor')) for d in ds)
            if abs(soma - _r2(g['valor'])) >= 0.01:
                self._pendencia('lote', 'lote_nao_fecha', g['valor'], dia, ident,
                                f'lote {g["valor"]:.2f} × {len(ds)} liquidação(ões) do 477 {soma:.2f}')
                continue
            antes_l, antes_p = len(self.lancamentos), len(self.pendencias)
            for d in ds:
                kd = chave_477(d.get('uni'), d.get('numlancto'), d.get('parcela'))
                self._paga_documento('lote', d, dia, cod_banco,
                                     {'fonte': '456 RET BCO+477', 'lote': ident,
                                      'chave_477': '/'.join(kd)},
                                     f"{ident} · {'/'.join(kd)}", cb)
            # Lote é tudo ou nada: se algum documento virou pendência, o lote inteiro
            # sai — senão o arquivo lançaria parte de um débito de banco.
            if len(self.pendencias) > antes_p:
                motivos = sorted({p['motivo'] for p in self.pendencias[antes_p:]})
                del self.lancamentos[antes_l:]
                del self.pendencias[antes_p:]
                self._pendencia('lote', 'lote_nao_fecha', g['valor'], dia, ident,
                                'documento(s) do lote com pendência: ' + ', '.join(motivos))

    # -- 4. transferência entre contas próprias -----------------------------
    # -- 4b. pagamento por conta que o robô do 456 não baixa ----------------
    def pagamentos_fora_456(self, liquidacoes, contas_456):
        """Liquidação do 477 por conta sem extrato (Pecúnia, 613): lança o pagamento
        pela própria liquidação. Conta que entrar no 456 sai daqui sozinha (passa
        a ter CPG), então não duplica."""
        for d in liquidacoes:
            if not d.get('liq_ban'):
                continue
            cb = chave_banco(d.get('liq_ban'), d.get('liq_agen'), d.get('liq_ctacorrent'))
            if cb in contas_456:
                continue
            data_ = _data(d.get('liq_data_extrato'))
            kd = chave_477(d.get('uni'), d.get('numlancto'), d.get('parcela'))
            ident = f"{cb} · {'/'.join(kd)}"
            cod_banco = self.fixas.get(cb)
            if not cod_banco:
                self._pendencia('pagamento_fora_456', 'banco_sem_conta', d.get('vlr_final') or 0,
                                data_, ident, cb)
                continue
            self._paga_documento('pagamento_fora_456', d, data_, cod_banco,
                                 {'fonte': '477 (liquidação)', 'chave_477': '/'.join(kd)}, ident, cb)

    # -- 5. recebimento, adiantamento, desconto de duplicata ----------------
    def contrapartidas(self, regra, linhas):
        """Movimento do 456 que só troca o banco por uma conta fixa, pelo sinal."""
        tipo, chave_fixa, rotulo = CONTRAPARTIDA[regra]
        for m in linhas:
            v = _r2(m.get('valor'))
            if not v:
                continue
            data_ = _data(m.get('data'))
            ident = f"{m.get('conta_apelido')} nlanca {m.get('nlanca')}"
            cb = chave_banco(m.get('banco'), m.get('agencia'), m.get('conta'))
            chave = chave_fixa.format(banco=cb) if '{banco}' in chave_fixa else chave_fixa
            outra = None
            if regra == 'TRANSF EXTERNA':
                # a outra conta não tem extrato: a ponta dela é lançada daqui
                t = _RE_TRANSF.search(norm_txt(m.get('historico')))
                pontas = [chave_banco(*t.groups()[:3]), chave_banco(*t.groups()[3:])] if t else []
                ko = next((x for x in pontas if x != cb), 'BANCO:?')
                outra = self.fixas.get(ko)
                if not outra:
                    self._pendencia(tipo, 'banco_sem_conta', abs(v), data_, ident, ko)
                    continue
            contra = self.fixas.get(chave)
            if not contra:
                motivo = 'banco_sem_conta' if chave.startswith('BANCO:') else 'conta_fixa_ausente'
                self._pendencia(tipo, motivo, abs(v), data_, ident, chave)
                continue
            banco = self.fixas.get(cb)
            if not banco:
                self._pendencia(tipo, 'banco_sem_conta', abs(v), data_, ident, m.get('conta_apelido'))
                continue
            d, c = (banco, contra) if v > 0 else (contra, banco)
            texto = m.get('historico') or f"{m.get('documento') or ''} {m.get('cliente_fornecedor') or ''}"
            partidas = [('principal', d, c, abs(v), f'{rotulo} - {texto}')]
            if outra:   # sinal oposto na outra conta: saiu daqui = entrou lá
                partidas.append(('outra_ponta', *((contra, outra) if v > 0 else (outra, contra)),
                                 abs(v), f'{rotulo} (outra ponta) - {texto}'))
            self._lanca(tipo, data_, partidas,
                        {'fonte': '456', 'regra': regra, 'nlanca': m.get('nlanca')}, ident, abs(v))

    # -- 6. juros e descontos do recebimento (441) --------------------------
    def juros_descontos_441(self, faturas):
        """Fatura liquidada no mês com juros/desconto, pela data da liquidação."""
        cli = self.fixas.get('CLIENTE_PADRAO')
        cj, cd = self.fixas.get('JUROS_RECEBIDOS'), self.fixas.get('DESCONTOS_CONCEDIDOS')
        for f in faturas:
            juros, desc = _r2(f.get('juros')), _r2(f.get('desconto'))
            if not juros and not desc:
                continue
            data_ = _data(f.get('pagamento'))
            ident = f"fatura {f.get('fatura')}"
            if not (cli and (cj or not juros) and (cd or not desc)):
                self._pendencia('juros_desconto_441', 'conta_fixa_ausente', abs(juros) + abs(desc),
                                data_, ident, 'CLIENTE_PADRAO/JUROS_RECEBIDOS/DESCONTOS_CONCEDIDOS')
                continue
            base = f"FATURA {f.get('fatura')} {f.get('cliente') or ''}"
            # sinal trocado (estorno) inverte a partida
            pj = (cli, cj) if juros > 0 else (cj, cli)
            pd = (cd, cli) if desc > 0 else (cli, cd)
            self._lanca('juros_desconto_441', data_, [
                ('principal', pj[0], pj[1], abs(juros), 'JUROS ' + base),
                ('principal', pd[0], pd[1], abs(desc), 'DESCONTO ' + base),
            ], {'fonte': '441', 'fatura': f.get('fatura')}, ident, abs(juros) + abs(desc))

    # -- 7. aplicação do adiantamento na fatura (571) ----------------------
    def aplicacoes_571(self, linhas):
        """Adiantamento de cliente aplicado numa fatura, na data da liquidação.

        O dinheiro do adiantamento já entrou pelo extrato (ACN → D banco / C 844).
        Quando o 571 o aplica numa fatura, não há movimento de banco — e, medido
        em 14/10/2026, 519 das 585 aplicações também não têm FAT no 456. Sem esta
        partida a fatura ficaria aberta na 504 e o adiantamento parado na 844.

        ⚠ A especificação (PARA GABRIEL.xlsx) diz "conta de débito financeiro será
        o banco utilizado"; lançar assim duplicaria o dinheiro que o ACN já lançou.
        Desvio consciente — conferir no 1º teste (HANDOFF §17)."""
        adto, cli = self.fixas.get('ADTO_CLIENTE'), self.fixas.get('CLIENTE_PADRAO')
        for a in linhas:
            v = _r2(a.get('valor_doc'))
            if not v:
                continue
            data_ = _data(a.get('liquidac'))
            ident = f"ACNI {a.get('numero')} → fatura {a.get('ctrc_fatura')}"
            if not (adto and cli):
                self._pendencia('aplicacao_adiantamento', 'conta_fixa_ausente', abs(v), data_, ident,
                                'ADTO_CLIENTE/CLIENTE_PADRAO')
                continue
            d, c = (adto, cli) if v > 0 else (cli, adto)
            self._lanca('aplicacao_adiantamento', data_, [(
                'principal', d, c, abs(v),
                f"APLICACAO ACNI {a.get('numero')} NA FATURA {a.get('ctrc_fatura')} {a.get('cliente') or ''}")],
                {'fonte': '571', 'acni': a.get('numero')}, ident, abs(v))

    # -- saída ----------------------------------------------------------------
    def texto(self):
        partes = []
        for l in sorted(self.lancamentos, key=lambda x: (x['data'], x['tipo'])):
            partes.append('X')
            partes.append(f"Z;{l['data'].strftime('%d/%m/%Y')};{l['debito']};{l['credito']};"
                          f"{valor_br(l['valor'])};{HISTORICO};{l['complemento']}")
        return '\r\n'.join(partes) + ('\r\n' if partes else '')


def resumir(g, fora, conferencias):
    """Agrupa o resultado para a tela: por tipo, por motivo, por regra."""
    por_tipo = {}
    for l in g.lancamentos:
        t = por_tipo.setdefault(l['tipo'], {'n': 0, 'valor': 0.0})
        t['n'] += 1
        t['valor'] += l['valor']
    por_motivo = {}
    for p in g.pendencias:
        m = por_motivo.setdefault((p['tipo'], p['motivo']), {'n': 0, 'valor': 0.0})
        m['n'] += 1
        m['valor'] += p['valor']
    return {
        'mes': g.mes, 'versao': VERSAO,
        'lancamentos': len(g.lancamentos),
        'valor_total': _r2(sum(l['valor'] for l in g.lancamentos)),
        'por_tipo': {k: {'n': v['n'], 'valor': _r2(v['valor'])} for k, v in por_tipo.items()},
        'pendencias': [{'tipo': t, 'motivo': m, 'descricao': MOTIVOS[m][0],
                        'depende_de': MOTIVOS[m][1], 'n': v['n'], 'valor': _r2(v['valor'])}
                       for (t, m), v in sorted(por_motivo.items(), key=lambda x: -x[1]['valor'])],
        'fora_da_fase': fora,
        'conferencias': conferencias,
    }
