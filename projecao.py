# -*- coding: utf-8 -*-
"""Projeção financeira de 12 meses para a aba Projeção — motor puro.

Não fala DAX, Flask nem banco: quem busca os dados é a rota (`/api/projecao`), no
mesmo padrão do `jornada.py`. Também não usa numpy/pandas — a imagem de produção
não os tem, e o que se calcula aqui cabe em Python puro com folga.

A RÉGUA É A DO DRE, porque o DRE é o número que o diretor valida e confronta com o
sistema: receita = `valor_frete` por `data_autorizacao`; despesa = `vlr_final` do
477 por competência (`REF`), agrupada pelo `MAPA_DRE`; mesma cascata de linhas.
Uma exceção só, e é correção de dado: o CTe que foi SUBSTITUÍDO sai da receita
(o cliente paga só o substituto — ver `originais_substituidos`).

Como cada linha é projetada — escolha medida em retroanálise sobre 24 origens
(ago/24..jul/26), cada uma projetando 12 meses só com o que se sabia na época:

| Linha                           | Método                          | Erro medido |
|---------------------------------|---------------------------------|-------------|
| Receita                         | média de 3 métodos (abaixo)     | 7% no mês seguinte, 8% no trimestre, ~5% na soma de 12 meses |
| Operacional (variável e fixo)   | proporção da receita, 6 meses   | 2,6% (variável) · 8,9% (fixo) |
| Deduções                        | proporção da receita, 6 meses   | 8,4% |
| Administrativo, Financeiro,     | média dos últimos 6 meses       | 10% (adm. fixo); o resto é irregular por natureza (18–45%) |
| Retirada, Impostos              |                                 |             |
| Investimento                    | o maior entre as parcelas já    | 28% (só o contratado: 36% — a Rizza segue contratando, |
|                                 | lançadas e a média de 6 meses   | e todo método subestima) |

Receita = média de: Holt-Winters (tendência amortecida + sazonalidade, em log, só
os últimos 36 meses), "mesmo mês do ano passado × crescimento dos últimos 12" e
média dos últimos 3 meses. Testados e reprovados: Holt-Winters com o histórico
desde 2021 (a empresa mudou; 31% de erro), projeção cliente a cliente (36% na soma
do ano) e o Chronos (empata com a média, e custaria o torch na imagem).

O que estatística NÃO prevê: contrato novo. 65% do crescimento de 2026 (+31%)
veio de cliente que não existia (Química Amparo, Unilever, Ambev...). A faixa de
incerteza diz quanto o modelo errou no passado — não cobre um cliente grande
entrando ou saindo.

A faixa não é teórica: sai do erro REAL que a própria retroanálise mede, por
horizonte, e é SIMÉTRICA — receita ± o erro que cobre 90% dos erros passados; EBITDA
± o erro de margem (em pontos percentuais) que cobre 80% deles, porque com margem de
~10% um erro pequeno de receita ou custo vira erro grande de EBITDA. Escolhida em
teste às cegas (20 meses de 2025–26, o motor cortado em cada um): o p10–p90 da
razão, que parecia natural, deixava o real dentro da faixa só 65% das vezes no mês
seguinte e 23% no 2º semestre. A simétrica cobre 80% no mês seguinte, 70% no
trimestre, 65% em 4–6 meses e 45% depois disso — o que falta é cliente novo/perdido,
e a tela diz isso em vez de prometer 80%. A do EBITDA cobre ~90% em todo horizonte.

A provisão que o financeiro lança no SSW para meses futuros (PREVISAO/PROVISAO,
valores redondos) é o orçamento DELE: entra como linha de comparação, nunca
somada. Quando o custo real chega, ela é trocada — apagada e relançada no
Operacional, editada no lugar nas Deduções —, então mês fechado é sempre real.
"""

import math
import re

# Linhas da DRE — mesmas chaves e mesma cascata de server.py:_calcular_dre_periodo
DRE_LINHAS = [
    ('Receita Bruta',                'Subtotal', 'receita_bruta'),
    ('(-) Deduções',                 'Grupo',    'deducoes'),
    ('= Receita Líquida',            'Subtotal', 'receita_liquida'),
    ('(-) Custo Operacional',        'Grupo',    'custo_operacional'),
    ('(-) Despesas Administrativas', 'Grupo',    'despesas_administrativas'),
    ('= EBITDA',                     'Subtotal', 'ebitda'),
    ('(-) Despesas Financeiras',     'Grupo',    'despesas_financeiras'),
    ('= LAIR',                       'Subtotal', 'lair'),
    ('(-) Impostos',                 'Grupo',    'impostos'),
    ('= Lucro Líquido',              'Subtotal', 'lucro_liquido'),
    ('(-) Investimentos',            'Grupo',    'investimentos'),
    ('= Pós Investimento',           'Subtotal', 'pos_investimento'),
    ('(-) Retiradas',                'Grupo',    'retiradas'),
    ('= Resultado Final',            'Subtotal', 'resultado_final'),
]
GRUPO_CHAVE = {
    'Deduções': 'deducoes', 'Operacional': 'custo_operacional',
    'Administrativo': 'despesas_administrativas', 'Financeiro': 'despesas_financeiras',
    'Impostos': 'impostos', 'Investimento': 'investimentos', 'Retirada': 'retiradas',
}
GRUPOS = list(GRUPO_CHAVE)

# Método por grupo (ver a tabela do docstring). A projeção é feita no grão
# grupo × fixo/variável do 477 e somada ao grupo.
METODO_GRUPO = {
    'Deduções': 'proporcao', 'Operacional': 'proporcao',
    'Administrativo': 'media', 'Financeiro': 'media', 'Retirada': 'media', 'Impostos': 'media',
    'Investimento': 'contrato_ou_media',
}
ORIGEM_METODO = {'proporcao': 'direcionado', 'media': 'estatistico', 'contrato_ou_media': 'contratado'}

# Faixa simétrica: quantil do erro absoluto (ver o docstring — p10–p90 cobria 65%/23%)
Q_FAIXA_RECEITA = 0.9
Q_FAIXA_MARGEM = 0.8

JANELA_RECEITA = 36    # meses de histórico da receita (desde 2021 o erro triplica)
JANELA_CUSTO = 6       # meses da proporção/média dos custos
HORIZONTE = 12
N_ORIGENS = 24         # pontos de partida da retroanálise
FAIXAS_H = [(1, 1), (2, 3), (4, 6), (7, 12)]


# ── calendário ───────────────────────────────────────────────────────────────

def mes_add(m, k):
    """'2026-09' + 4 → '2027-01'"""
    y, mo = int(m[:4]), int(m[5:7])
    t = y * 12 + (mo - 1) + k
    return f'{t // 12:04d}-{t % 12 + 1:02d}'


def meses_entre(a, b):
    """['2026-01', ..., '2026-04'] inclusive"""
    out, m = [], a
    while m <= b:
        out.append(m)
        m = mes_add(m, 1)
    return out


# ── receita: três métodos e a média ──────────────────────────────────────────

_GRADE = [(a, b, g, p)
          for a in (0.1, 0.2, 0.35, 0.5, 0.7)
          for b in (0.01, 0.05, 0.15)
          for g in (0.05, 0.15, 0.3)
          for p in (0.8, 0.9, 0.98, 1.0)]


def _hw_rodar(ly, a, b, g, p, h):
    """Holt-Winters aditivo (em log), tendência amortecida, período 12.
    Devolve (soma dos erros² de 1 passo, previsão de h passos)."""
    n = len(ly)
    # A média do 1º ano é o nível no MEIO dele (t = 5,5). O estado inicial é o de
    # t = −1, e a sazonalidade inicial sai descontada da tendência — sem isso o viés
    # do começo não se desfaz em 36 meses (errava 9,7% numa série sintética limpa).
    centro = sum(ly[:12]) / 12
    tend = (sum(ly[12:24]) / 12 - centro) / 12 if n >= 24 else 0.0
    s = [ly[i] - (centro + (i - 5.5) * tend) for i in range(12)]
    l = centro - 6.5 * tend
    sse = 0.0
    for t in range(n):
        i = t % 12
        e = ly[t] - (l + p * tend + s[i])
        sse += e * e
        l_novo = a * (ly[t] - s[i]) + (1 - a) * (l + p * tend)
        tend = b * (l_novo - l) + (1 - b) * p * tend
        s[i] = g * (ly[t] - l_novo) + (1 - g) * s[i]
        l = l_novo
    prev, acum = [], 0.0
    for k in range(1, h + 1):
        acum += p ** k
        prev.append(l + acum * tend + s[(n + k - 1) % 12])
    return sse, prev


def holt_winters(y, h):
    """Escolhe os parâmetros pela grade (menor erro de 1 passo) e projeta h meses."""
    ly = [math.log(max(v, 1.0)) for v in y]
    melhor = None
    for a, b, g, p in _GRADE:
        sse, prev = _hw_rodar(ly, a, b, g, p, h)
        if melhor is None or sse < melhor[0]:
            melhor = (sse, prev)
    return [math.exp(v) for v in melhor[1]]


def naive_sazonal(y, h):
    """Mesmo mês do ano passado × crescimento dos últimos 12 sobre os 12 anteriores."""
    ant = sum(y[-24:-12])
    cresc = sum(y[-12:]) / ant if ant else 1.0
    return [y[len(y) - 12 + (k % 12)] * cresc for k in range(h)]


def media3(y, h):
    return [sum(y[-3:]) / 3] * h


def prever_receita(y, h=HORIZONTE):
    """Média dos três métodos. `y` = receita mensal fechada, do mais antigo ao mais novo."""
    y = list(y)[-JANELA_RECEITA:]
    if len(y) < 24:
        raise ValueError('a projeção de receita precisa de pelo menos 24 meses fechados')
    ps = (holt_winters(y, h), naive_sazonal(y, h), media3(y, h))
    return [sum(p[k] for p in ps) / 3 for k in range(h)]


def nowcast(receita_ate_hoje, fracao_esperada, previsto):
    """Mês corrente: o que já entrou ÷ a fração do mês que costuma ter entrado até
    este dia, misturado com a projeção pelo peso dessa fração (dia 5 ainda é quase
    só a projeção; dia 28 é quase só o realizado)."""
    f = max(0.0, min(1.0, fracao_esperada or 0.0))
    if f <= 0.05:
        return previsto
    return f * (receita_ate_hoje / f) + (1 - f) * previsto


def fracao_do_mes(diario, dia, meses):
    """Fração média da receita de um mês que entrou até `dia`, nos `meses` dados.
    `diario` = {'YYYY-MM-DD': valor}."""
    fr = []
    for m in meses:
        tot = sum(v for d, v in diario.items() if d[:7] == m)
        if tot <= 0:
            continue
        ate = sum(v for d, v in diario.items() if d[:7] == m and int(d[8:10]) <= dia)
        fr.append(ate / tot)
    return sum(fr) / len(fr) if fr else 0.0


# ── custos ───────────────────────────────────────────────────────────────────

def _linhas_do_grupo(linhas, grupo):
    chaves = set()
    for v in linhas.values():
        chaves.update(k for k in v if k.split('|')[0] == grupo)
    return sorted(chaves)


def prever_custos(receita_hist, linhas_hist, meses_hist, receita_prev, meses_prev, contratos=None):
    """Projeta cada grupo (no grão grupo|fixo_variavel) para `meses_prev`.

    receita_hist / linhas_hist: {mes: valor} / {mes: {'Grupo|Fixo': valor}} fechados.
    meses_hist: os meses fechados a considerar (a janela sai do fim deles).
    receita_prev: receita projetada, alinhada a meses_prev.
    contratos: {mes: {grupo: valor}} — parcelas já contratadas (PEND de contrato).
    Devolve {grupo: [valor por mês]}.
    """
    contratos = contratos or {}
    jan = meses_hist[-JANELA_CUSTO:]
    rec_jan = sum(receita_hist.get(m, 0.0) for m in jan)
    out = {}
    for g in GRUPOS:
        met = METODO_GRUPO[g]
        vals = [0.0] * len(meses_prev)
        for chave in _linhas_do_grupo({m: linhas_hist.get(m, {}) for m in jan}, g):
            soma = sum(linhas_hist.get(m, {}).get(chave, 0.0) for m in jan)
            for i, rp in enumerate(receita_prev):
                if met == 'proporcao':
                    vals[i] += (soma / rec_jan) * rp if rec_jan else 0.0
                else:
                    vals[i] += soma / len(jan)
        if met == 'contrato_ou_media':
            # O contratado é piso, não teto: a Rizza segue contratando (o investimento
            # dobrou em 2026) e só o contratado subestimava 36% em teste às cegas.
            vals = [max(contratos.get(m, {}).get(g, 0.0), v) for m, v in zip(meses_prev, vals)]
        out[g] = vals
    return out


def cascata(receita, grupos):
    """Mesma conta de server.py:_calcular_dre_periodo. `grupos` = {grupo: valor}."""
    gv = lambda g: grupos.get(g, 0.0)
    rl = receita - gv('Deduções')
    ebitda = rl - gv('Operacional') - gv('Administrativo')
    lair = ebitda - gv('Financeiro')
    ll = lair - gv('Impostos')
    pos = ll - gv('Investimento')
    return {
        'receita_bruta': receita, 'deducoes': gv('Deduções'), 'receita_liquida': rl,
        'custo_operacional': gv('Operacional'), 'despesas_administrativas': gv('Administrativo'),
        'ebitda': ebitda, 'despesas_financeiras': gv('Financeiro'), 'lair': lair,
        'impostos': gv('Impostos'), 'lucro_liquido': ll, 'investimentos': gv('Investimento'),
        'pos_investimento': pos, 'retiradas': gv('Retirada'), 'resultado_final': pos - gv('Retirada'),
    }


def grupos_do_mes(linhas_mes):
    """{'Operacional|Variável': v, ...} → {'Operacional': soma, ...}"""
    out = {}
    for k, v in (linhas_mes or {}).items():
        g = k.split('|')[0]
        out[g] = out.get(g, 0.0) + v
    return out


# ── retroanálise: o erro real, que vira a faixa e o card de acurácia ─────────

def _quantil(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    pos = (len(xs) - 1) * q
    i = int(pos)
    j = min(i + 1, len(xs) - 1)
    return xs[i] + (xs[j] - xs[i]) * (pos - i)


def _faixa(h):
    for a, b in FAIXAS_H:
        if a <= h <= b:
            return f'{a}-{b}' if a != b else str(a)


def retroanalise(receita_hist, linhas_hist, meses, n_origens=N_ORIGENS):
    """Projeta o passado "às cegas" a partir de cada uma das `n_origens` últimas
    origens possíveis e mede o erro contra o realizado.

    Investimento fica de fora do EBITDA (está abaixo dele), então a retroanálise
    não precisa do histórico de contratos — que não existe: a provisão é trocada
    quando o real chega.
    """
    y = [receita_hist[m] for m in meses]
    origens = list(range(max(24, len(meses) - 1 - n_origens), len(meses) - 1))
    amostras = []
    for o in origens:
        mh = meses[:o + 1]
        h = min(HORIZONTE, len(meses) - 1 - o)
        rp = prever_receita(y[:o + 1], h)
        mp = meses[o + 1:o + 1 + h]
        cp = prever_custos(receita_hist, linhas_hist, mh, rp, mp)
        for k in range(h):
            m = mp[k]
            real_r = receita_hist[m]
            gr = grupos_do_mes(linhas_hist.get(m))
            eb_real = cascata(real_r, gr)['ebitda']
            eb_prev = cascata(rp[k], {g: cp[g][k] for g in GRUPOS})['ebitda']
            amostras.append({'origem': meses[o], 'h': k + 1, 'r_prev': rp[k], 'r_real': real_r,
                             'eb_prev': eb_prev, 'eb_real': eb_real})
    por_faixa = {}
    for a, b in FAIXAS_H:
        xs = [s for s in amostras if a <= s['h'] <= b]
        if not xs:
            continue
        err_log = [abs(math.log(s['r_real'] / s['r_prev'])) for s in xs if s['r_prev'] > 0 and s['r_real'] > 0]
        pp = [abs(s['eb_real'] / s['r_real'] - s['eb_prev'] / s['r_prev']) for s in xs if s['r_real'] and s['r_prev']]
        por_faixa[_faixa(a)] = {
            'n': len(xs),
            'wape_receita': sum(abs(s['r_prev'] - s['r_real']) for s in xs) / sum(s['r_real'] for s in xs),
            'receita_erro_log': _quantil(err_log, Q_FAIXA_RECEITA),   # faixa = projeção × e^(±isto)
            'margem_pp_faixa': _quantil(pp, Q_FAIXA_MARGEM),           # faixa = EBITDA ± isto × receita
            'margem_pp_mediana_abs': _quantil(pp, 0.5),
        }
    # soma de 12 meses (o "orçamento")
    anos = []
    for o in {s['origem'] for s in amostras}:
        xs = [s for s in amostras if s['origem'] == o]
        if len(xs) == HORIZONTE:
            anos.append(sum(s['r_prev'] for s in xs) / sum(s['r_real'] for s in xs) - 1)
    return {
        'origens': len(origens), 'por_faixa': por_faixa,
        'erro_12m_medio_abs': sum(abs(e) for e in anos) / len(anos) if anos else None,
        'n_12m': len(anos),
    }


# ── a projeção ───────────────────────────────────────────────────────────────

def projetar(receita_hist, linhas_hist, mes_corrente, contratos=None, previsao_fin=None,
             receita_mes_corrente=None, fracao_mes_corrente=None, retro=None):
    """Projeção de 12 meses a partir do mês corrente (inclusive).

    receita_hist: {mes: valor} dos meses FECHADOS de receita (CTe de mês encerrado é final).
    linhas_hist:  {mes: {'Grupo|Fixo': valor}} dos meses FECHADOS de despesa — o 477
                  fecha depois (a provisão é trocada até ~dia 10), então pode acabar um
                  mês antes da receita; esse mês intermediário tem custo projetado.
    contratos:    {mes: {grupo: valor}} — PEND de contrato (parcelas).
    previsao_fin: {mes: {grupo: valor}} — PEND de previsão/provisão do financeiro.
    receita_mes_corrente / fracao_mes_corrente: para o nowcast do mês em curso.
    retro:        resultado de `retroanalise` (se None, calcula).
    """
    contratos = contratos or {}
    previsao_fin = previsao_fin or {}
    mr = sorted(receita_hist)
    mc = sorted(linhas_hist)
    ult_rec, ult_custo = mr[-1], mc[-1]
    if mes_add(ult_rec, 1) != mes_corrente:
        raise ValueError(f'a receita fechada tem de ir até o mês anterior ao corrente ({ult_rec} × {mes_corrente})')
    meses_rec = meses_entre(mr[0], ult_rec)
    y = [receita_hist.get(m, 0.0) for m in meses_rec]

    futuros = meses_entre(mes_corrente, mes_add(mes_corrente, HORIZONTE - 1))
    rp = prever_receita(y, HORIZONTE)
    modelo_corrente = rp[0]
    if receita_mes_corrente is not None:
        rp[0] = nowcast(receita_mes_corrente, fracao_mes_corrente, rp[0])

    # meses com custo projetado = do primeiro sem 477 fechado até o fim do horizonte
    meses_custo_prev = meses_entre(mes_add(ult_custo, 1), futuros[-1])
    rec_para_custo = [receita_hist[m] if m in receita_hist else rp[futuros.index(m)]
                      for m in meses_custo_prev]
    meses_custo_hist = meses_entre(mc[0], ult_custo)
    cp = prever_custos(receita_hist, linhas_hist, meses_custo_hist, rec_para_custo,
                       meses_custo_prev, contratos)

    if retro is None:
        retro = retroanalise(receita_hist, linhas_hist, meses_entre(mc[0], min(ult_rec, ult_custo)))
    pf = retro['por_faixa']

    meses_out = []
    for i, m in enumerate(meses_custo_prev):
        h = i - (len(meses_custo_prev) - HORIZONTE) + 1   # 1 = mês corrente
        real_receita = m in receita_hist
        receita = rec_para_custo[i]
        grupos = {g: cp[g][i] for g in GRUPOS}
        dre = cascata(receita, grupos)
        item = {'mes': m, 'h': h, 'receita_realizada': real_receita, 'dre': dre,
                'origem': {GRUPO_CHAVE[g]: ORIGEM_METODO[METODO_GRUPO[g]] for g in GRUPOS},
                'previsao_financeiro': {GRUPO_CHAVE[g]: v for g, v in previsao_fin.get(m, {}).items()
                                        if g in GRUPO_CHAVE},
                'contratado': {GRUPO_CHAVE[g]: v for g, v in contratos.get(m, {}).items() if g in GRUPO_CHAVE}}
        item['origem']['receita_bruta'] = 'realizado' if real_receita else 'estatistico'
        if grupos['Investimento'] > contratos.get(m, {}).get('Investimento', 0.0) + 0.005:
            item['origem']['investimentos'] = 'estatistico'   # a média passou do contratado
        f = pf.get(_faixa(max(h, 1))) if h >= 1 and not real_receita else None
        if f:
            item['receita_p10'] = receita * math.exp(-f['receita_erro_log'])
            item['receita_p90'] = receita * math.exp(f['receita_erro_log'])
            item['ebitda_p10'] = dre['ebitda'] - f['margem_pp_faixa'] * receita
            item['ebitda_p90'] = dre['ebitda'] + f['margem_pp_faixa'] * receita
        if m == mes_corrente and receita_mes_corrente is not None:
            item['nowcast'] = {'realizado_ate_hoje': receita_mes_corrente,
                               'fracao_esperada': fracao_mes_corrente, 'modelo': modelo_corrente}
        meses_out.append(item)

    return {'mes_corrente': mes_corrente, 'ultimo_fechado_receita': ult_rec,
            'ultimo_fechado_custo': ult_custo, 'meses': meses_out, 'retroanalise': retro}


def escada_compromissos(contratos):
    """{mes: {grupo: v}} → [{'ano': 2027, 'Investimento': v, 'Financeiro': v, 'total': v}, ...]"""
    anos = {}
    for m, gs in contratos.items():
        a = anos.setdefault(int(m[:4]), {})
        for g, v in gs.items():
            a[g] = a.get(g, 0.0) + v
    return [{'ano': a, **gs, 'total': sum(gs.values())} for a, gs in sorted(anos.items())]


# ── regras de dado ───────────────────────────────────────────────────────────

_RE_SUBST = re.compile(r'SUBSTITUIR O CTRC\s+([A-Z]{3})\s*(\d{6}-\d)')


def originais_substituidos(observacoes):
    """CTe substituto traz na observação "CTRC EMITIDO PARA SUBSTITUIR O CTRC UDI 409658-4".
    O cliente paga só o substituto (conferido no 441: nenhum original está em fatura), e
    na maioria dos casos o original some da base — mas 16 de 62 ficaram (fev–mar/26),
    somando duas vezes. Devolve os originais citados, no formato de `serie_numero_ctrc`."""
    out = set()
    for obs in observacoes:
        m = _RE_SUBST.search(str(obs or '').upper())
        if m:
            out.add(f'{m.group(1)}{m.group(2)}')
    return out


_RE_PREV = re.compile(r'PREVIS|PROVIS|PREVIA|ESTIMAT')


def natureza_pend(grupo, historico):
    """Despesa PENDENTE futura: 'contrato' (parcela que vai acontecer) ou 'previsao'
    (estimativa que o financeiro lança e troca pelo real quando ele chega).

    Investimento e Financeiro são contrato (CDC, FINAME, consórcio, empréstimo), a não
    ser que o próprio histórico diga que é previsão. O resto é previsão do financeiro —
    inclusive o que não traz a palavra: 75% do valor sem "PREVISAO" no texto é número
    redondo (salário, combustível, pedágio lançados por estimativa)."""
    if _RE_PREV.search(str(historico or '').upper()):
        return 'previsao'
    return 'contrato' if grupo in ('Investimento', 'Financeiro') else 'previsao'
