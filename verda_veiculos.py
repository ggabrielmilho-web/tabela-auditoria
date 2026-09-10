# -*- coding: utf-8 -*-
"""
Classificação de veículo para a integração Verda (`VehicleTypeKey`).

A Verda exige o tipo do veículo em toda chamada do domínio RoadOrchestration.
O cadastro do SSW (`veiculos_045`) é a fonte natural, mas foi preenchido por
cinco anos de gente diferente e erra com frequência — 26% das carretas com
histórico declaram capacidade MENOR do que o peso que elas comprovadamente
carregam. Por isso a capacidade aqui não sai de um campo só: sai de uma
**cascata de pisos**, onde o cadastro é o elo mais fraco e só vence quando é
o maior dos três.

    1. piso estrutural   duas placas (cavalo+carreta) = articulado → 27 t
                         truck → 12 t, toco → 6 t
                         Não depende de ninguém digitar nada.
    2. evidência         p90 do peso realmente transportado pela placa
                         (a partir de MIN_VIAGENS_OBS viagens).
    3. declarado         `veiculos_045.capacidade`, e só dentro de faixa
                         plausível (descarta 0 e absurdos tipo 99 t).

Bitrem e rodotrem são invisíveis pelas placas — o manifesto traz só cavalo +
uma carreta. São detectados pelo peso transportado (> LIMITE_BITREM), com a
tração 6X4 do cavalo como reforço: das 44 viagens acima de 33 t medidas na
base, 40 foram puxadas por 6X4, e não existe 4X2 nenhum entre elas.

PENDÊNCIA ABERTA COM A VERDA: as faixas (3,5 / 7,5 / 17 / 33 t) são carga útil
ou PBT? A documentação não diz. `BASE_FAIXA` alterna entre as duas leituras —
é trocar a constante quando o Rafael responder, sem tocar em mais nada.
"""

import re

from placas import mercosul


# ════════════════════════════════════════
# PARÂMETROS — é aqui que se mexe
# ════════════════════════════════════════

# Piso de carga útil por configuração física (t). Calibrado com a operação.
PISO_CARGA_UTIL = {
    'CARRETA': 27.0,   # cavalo + semirreboque: simples 27, trucada 30, vanderleia 33
    'TRUCK': 12.0,
    'TOCO': 6.0,
}

# Teto de plausibilidade (t): acima disso o cadastro é lixo e é descartado.
#
# A CARRETA para em 33 t de propósito, no mesmo valor de LIMITE_BITREM: acima
# disso o veículo não é carreta, é conjunto pesado — e isso só se afirma com
# EVIDÊNCIA (p90 do histórico da placa ou o peso da viagem), nunca com a
# capacidade digitada no cadastro. Medido na base: das 284 viagens que subiam
# para `articulado_330`, 220 (77%) subiam só pelo campo `capacidade` de carretas
# comuns declaradas com 34 a 40,5 t que nunca carregaram mais de 24 t. Com o
# teto em 33, sobram 64 viagens em 9 carretas — e as três que mais aparecem
# batem com a lista de rodotrem da Rizza.
TETO_PLAUSIVEL = {'CARRETA': 33.0, 'TRUCK': 30.0, 'TOCO': 15.0}

# Tara média do conjunto (t) — somada à carga útil para chegar ao PBT.
# Confere com a Lei da Balança: 27 t de piso + 17 t de tara = 44 t, contra os
# 45 t de PBTC do cavalo + carreta de 3 eixos. Dois caminhos independentes.
TARA_CONJUNTO = {'CARRETA': 17.0, 'TRUCK': 8.0, 'TOCO': 5.0}

# 'carga_util' ou 'pbt'. É CARGA ÚTIL, por decisão do Gabriel em 10/09/2026.
#
# A leitura anterior era PBT, por pesquisa: DEFRA ("articulated >33 t") e GLEC
# ("European articulated truck >32 t *gross combined weight*") cortam em peso
# bruto. O problema é o que isso produzia na frota real — somando 27 t de piso
# + 17 t de tara, TODO articulado brasileiro passava de 33 t e caía em
# `articulado_330`: 122 das 125 viagens de uma semana. Ou seja, declarávamos a
# frota inteira como bitrem/rodotrem, e a faixa `articulado_35` virava uma
# gaveta impossível de usar no Brasil — categoria que nunca recebe nada é sinal
# de régua errada.
#
# Pela leitura de carga útil a faixa passa a dizer o que o operador entende:
# carreta é carreta até 33 t (trucada ou simples, não muda), e acima disso é
# conjunto pesado. Na mesma semana: 119 `articulado_35`, 3 `articulado_330`.
#
# Continua valendo perguntar ao Rafael qual das duas a Verda quis dizer. A
# resposta troca esta constante e mais nada — e não altera o CO2e, porque na
# API `Fuel` a emissão sai de litros x fator e o VehicleTypeKey não entra na
# conta.
BASE_FAIXA = 'carga_util'

# Faixas da Verda: (limite superior em t, código). O último é o teto aberto.
FAIXAS_ARTICULADO = [(33.0, 'articulado_35'), (float('inf'), 'articulado_330')]
FAIXAS_RIGIDO = [(7.5, 'rigido_35'), (17.0, 'rigido_75'), (float('inf'), 'rigido_170')]

# Evidência observada
MIN_VIAGENS_OBS = 5     # viagens mínimas para o peso observado valer como fonte
PERCENTIL_OBS = 0.90    # p90 em vez de máximo: ignora a carga excepcional isolada

# ── Placas declaradas como conjunto pesado (bitrem/rodotrem) ──
# A detecção por peso só funciona depois que o veículo já rodou: precisa de 5
# viagens para o p90 valer, ou de uma carga acima de 33 t. Conjunto NOVO
# rodando leve passaria por carreta comum até acumular evidência.
#
# Esta lista fecha isso, e é a fonte que o Gabriel apontou desde o começo: o
# tipo do veículo é propriedade dele, não da carga do dia. Vale na primeira
# viagem, sem esperar histórico.
#
# NÃO dá para tirar do cadastro: no dataset publicado as quatro estão como
# `carroceria=CARRETA`, `capacidade=30,00`, `eixos=3` — a palavra RODOTREM não
# existe lá. Enquanto o SSW não publicar a configuração do conjunto, a lista é
# mantida à mão pela Rizza.
#
# Cuidado ao manter: são 4 placas para 2 conjuntos (rodotrem = 2 semirreboques
# + dolly), e o manifesto grava só UMA delas — por isso a parceira pode nunca
# aparecer em viagem nenhuma (é o caso da TYN8I60). Registrar as duas mesmo
# assim: qual das duas o manifesto vai gravar não é previsível.
PLACAS_CONJUNTO_PESADO = {
    'TYO9J49', 'TYO9J56',   # conjunto 1
    'TYN8I98', 'TYN8I60',   # conjunto 2
}

# Detecção de bitrem/rodotrem
LIMITE_BITREM = 33.0        # acima disso o conjunto não é carreta simples
MIN_VIAGENS_BITREM = 3      # abaixo disso vira exceção para conferência humana
TRACAO_COMPATIVEL = {'6X4', '8X2'}   # puxa bitrem/rodotrem
TRACAO_INCOMPATIVEL = {'4X2'}        # nunca puxa — trava na faixa de baixo

# Tipos do SSW por família
TIPOS_ARTICULADO = {'CAVALO', 'CAVALO TRUCADO'}
TIPOS_RIGIDO = {'TRUCK', 'TOCO'}

# Teto físico de carga no Brasil (t) — filtra peso corrompido no manifesto.
PESO_MAX_PLAUSIVEL = 60.0
PESO_MIN_PLAUSIVEL = 0.5


# ════════════════════════════════════════
# CONSUMO (km/l) — parâmetros
# ════════════════════════════════════════
# Fonte: janela de hodômetro do ValeCard (ver `abastecido_por_placa`). O campo
# media_min/media_max do cadastro SSW NÃO é usado — 2.939 dos 2.984 cavalos
# estão zerados.
#
# O abastecimento do tanque interno ESTÁ na tabela, como importação manual sem
# cartão e sem hodômetro: são os litros que existem sem km atrelado. Por isso o
# método do hodômetro resolve — ele mede o km pelo painel e conta todos os
# litros da janela, não importa onde foram abastecidos.

# Faixa fisicamente plausível por tipo; fora disso o dado é descartado.
FAIXA_KML = {
    'CAVALO': (1.5, 3.6),
    'CAVALO TRUCADO': (1.5, 3.6),
    'TRUCK': (2.5, 5.0),
    'TOCO': (3.0, 6.5),
}

# ── Consumo por IDADE do veículo (definido pela diretoria) ──
# `FuelConsumption` é o consumo CARREGADO, não a média do ciclo. A medição do
# ValeCard mistura ida carregada e volta vazia — dá 2,52 km/l na faixa de 6 a 10
# anos, mas a volta vazia puxa esse número para cima e não representa o trecho
# que estamos declarando. A API separa as duas coisas: a Fuel cobre a viagem
# carregada (o manifesto) e o retorno vazio vai pela UnloadedTrip.
#
# A escala vale para frota e terceiro igualmente — 76,6% das viagens são de
# veículo com mais de 10 anos, e é justamente onde não temos medição própria
# (uma placa, 2.949 L).
#
# Ela vale só para os ARTICULADOS. Rígido tem escala própria (CONSUMO_RIGIDO,
# logo abaixo).
#
# (idade máxima da faixa, km/l carregado). Faixas definidas pela diretoria.
#
# ── REVISADO EM 10/09/2026, POR DECISÃO DA DIRETORIA ──
# A escala anterior era 2,20 / 1,80 / 1,50 em três faixas. A diretoria decidiu
# elevar as médias e detalhar em quatro. Efeito no ano de 2026: o consumo médio
# ponderado sobe de 1,62 para 2,55 km/l, os litros caem 37% e o inventário CO2e
# vai de 5.800 t para 3.681 t.
#
# ⚠ DIVERGÊNCIA REGISTRADA, para quem for auditar ou revisar isto:
# nas duas faixas em que a Rizza TEM medição própria, o valor adotado ficou
# ACIMA do ciclo medido no ValeCard (ver CICLO_MEDIDO logo abaixo):
#
#        faixa        ciclo medido      adotado (carregado)
#        0-5 anos       2,71 km/l           3,00 km/l
#        6-10 anos      2,52 km/l           2,80 km/l
#
# Isso inverte a relação física entre os dois: o ciclo inclui a volta vazia, que
# gasta menos por km, então o ciclo é sempre >= o carregado. Foi apontado antes
# da mudança e a diretoria manteve a decisão. Nas faixas acima de 10 anos (76%
# da operação) não há medição própria, então ali os valores são premissa e não
# contradizem nada.
CONSUMO_POR_IDADE = [
    (5, 3.00),
    (10, 2.80),
    (20, 2.50),
    (999, 2.20),
]

# ── Consumo do RÍGIDO (truck/toco) ──
# A escala por idade acima foi calibrada na frota própria, que é de cavalos, e
# estava sendo aplicada também aos rígidos: 158 das 173 viagens de truck iam
# com 1,50 km/l — consumo de conjunto de 45 t num veículo que carrega 5,6 t em
# média. O próprio arquivo já dizia que isso é impossível: FAIXA_KML põe o piso
# do TRUCK em 2,5 km/l.
#
# Medição do ValeCard pelo método do hodômetro, os dois únicos trucks com
# histórico: HGA9F47 = 3,70 km/l (27.048 km / 7.313 L) e OWH0F53 = 3,96.
#
# Vale a ressalva de método, para quem reabrir isto: 3,70 é média de CICLO
# (inclui a volta vazia), enquanto o articulado usa valor CARREGADO, abaixo do
# ciclo medido. Aplicando o mesmo desconto o truck daria ~2,64 km/l. O Gabriel
# optou por 3,70, o número medido, em 10/09/2026 — é a média que a Rizza já
# tinha combinado internamente.
#
# Efeito na base: 208,4 t -> 86,6 t CO2e nos rígidos, -2,05% do inventário.
# Estávamos superestimando 121,8 t/ano.
CONSUMO_RIGIDO = 3.70

# Veículo sem ano no cadastro cai na última faixa (a mais econômica agora, que
# antes era a mais pesada — atenção ao reler: a troca de escala inverteu o lado
# conservador dessa regra).
IDADE_SEM_ANO = 999

# Referência de conferência, não é o que se envia: média de CICLO medida no
# hodômetro do ValeCard, para comparar com a escala acima.
CICLO_MEDIDO = {'ate 5': 2.71, '6 a 10': 2.52}

# Litros mínimos para a média da placa valer (evita 1 abastecimento virar verdade).
LITROS_MIN_OBS = 2000.0


# ════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════

_RE_TRACAO = re.compile(r'\d\s?X\s?\d')


def numero(valor):
    """Converte texto do SSW em float. Aceita '25,00' e '25.00'; lixo vira 0.0."""
    s = str(valor or '0').strip()
    if ',' in s:
        s = s.replace('.', '').replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return 0.0


def tracao(modelo):
    """Configuração de eixo no modelo do cavalo ('6X4', '4X2'...) ou None.

    Vem da descrição de fábrica ('FH 460 6X2T', 'R450 A6X2'), não de campo
    digitado à mão — por isso é confiável quando existe. Cobre ~52% dos cavalos.
    """
    achado = _RE_TRACAO.findall(re.sub(r'\s+', '', str(modelo or '')).upper())
    return achado[0].replace(' ', '') if achado else None


def peso_plausivel(peso_kg):
    """Peso do manifesto em toneladas, ou None se estiver fora da faixa física."""
    t = numero(peso_kg) / 1000.0
    return t if PESO_MIN_PLAUSIVEL < t < PESO_MAX_PLAUSIVEL else None


def percentil(valores, q):
    """Percentil simples (sem numpy) sobre lista não vazia."""
    ordenados = sorted(valores)
    return ordenados[min(len(ordenados) - 1, int(q * len(ordenados)))]


# ════════════════════════════════════════
# CASCATA DE CAPACIDADE
# ════════════════════════════════════════

def capacidade(placa, familia, cadastro, observado=None):
    """Capacidade de carga (t) pela cascata de pisos.

    `cadastro`  {placa_mercosul: {'tipo', 'capacidade', 'modelo'}}
    `observado` {placa_mercosul: [pesos em t já transportados]}
    Retorna (valor_em_toneladas, fonte) — `fonte` alimenta o relatório de exceções.
    """
    piso = PISO_CARGA_UTIL.get(familia, 0.0)
    teto = TETO_PLAUSIVEL.get(familia, float('inf'))
    candidatos = [(piso, 'piso estrutural')]

    pesos = (observado or {}).get(placa) or []
    if len(pesos) >= MIN_VIAGENS_OBS:
        candidatos.append((percentil(pesos, PERCENTIL_OBS), 'observado (p90)'))

    declarado = numero((cadastro.get(placa) or {}).get('capacidade'))
    if piso * 0.5 <= declarado <= teto:
        candidatos.append((declarado, 'declarado'))

    return max(candidatos, key=lambda c: c[0])


def _faixa(valor, faixas):
    for limite, codigo in faixas:
        if valor <= limite:
            return codigo
    return faixas[-1][1]


# ════════════════════════════════════════
# CLASSIFICAÇÃO
# ════════════════════════════════════════

def vehicle_type_key(placa_cavalo, placa_carreta, cadastro,
                     observado=None, peso_viagem_kg=None):
    """`VehicleTypeKey` da Verda para uma viagem.

    Retorna (codigo, detalhe) onde `detalhe` traz capacidade, fonte, tração e a
    lista de alertas — o que o relatório de exceções consome.
    """
    cav = mercosul(placa_cavalo or '')
    car = mercosul(placa_carreta or '')
    reg_cav = cadastro.get(cav) or {}
    familia_veiculo = (reg_cav.get('tipo') or '').strip().upper()
    alertas = []

    if familia_veiculo in TIPOS_ARTICULADO:
        familia, placa_ref, faixas = 'CARRETA', car, FAIXAS_ARTICULADO
        if not car:
            alertas.append('viagem articulada sem placa de carreta')
    elif familia_veiculo in TIPOS_RIGIDO:
        familia, placa_ref, faixas = familia_veiculo, cav, FAIXAS_RIGIDO
    else:
        return None, {'alertas': ['tipo desconhecido no cadastro: %r' % familia_veiculo],
                      'capacidade': None, 'fonte': None, 'tracao': None}

    cap, fonte = capacidade(placa_ref, familia, cadastro, observado)
    tr = tracao(reg_cav.get('modelo'))

    # Bitrem/rodotrem: o peso da viagem denuncia o que a 2ª placa esconde.
    if familia == 'CARRETA':
        # A lista declarada é um PISO, não um substituto: garante a faixa já na
        # primeira viagem, e o peso real continua valendo quando for maior.
        if placa_ref in PLACAS_CONJUNTO_PESADO:
            cap, fonte = max((cap, fonte),
                             (LIMITE_BITREM + 0.1, 'placa declarada (conjunto pesado)'),
                             key=lambda c: c[0])
        peso = peso_plausivel(peso_viagem_kg) if peso_viagem_kg is not None else None
        if peso and peso > LIMITE_BITREM:
            if tr in TRACAO_INCOMPATIVEL:
                alertas.append('peso de %.1f t com tração %s (incompatível) — conferir' % (peso, tr))
            else:
                cap, fonte = max((cap, fonte), (peso, 'peso da viagem (bitrem/rodotrem)'),
                                 key=lambda c: c[0])
                if len((observado or {}).get(placa_ref) or []) < MIN_VIAGENS_BITREM:
                    alertas.append('carga acima de %.0f t sem histórico recorrente — conferir'
                                   % LIMITE_BITREM)

    base = cap + TARA_CONJUNTO.get(familia, 0.0) if BASE_FAIXA == 'pbt' else cap
    return _faixa(base, faixas), {'capacidade': round(cap, 1), 'fonte': fonte,
                                  'tracao': tr, 'alertas': alertas}


def consumo(placa, cadastro, ano_referencia=2026):
    """Consumo carregado em km/l, pela idade do veículo.

    Retorna (km_por_litro, fonte). A idade sai do ano do cadastro; sem ano, cai
    na faixa mais velha.
    """
    reg = cadastro.get(placa) or {}
    tipo = (reg.get('tipo') or '').strip().upper()
    if tipo not in TIPOS_ARTICULADO | TIPOS_RIGIDO:
        return None, 'tipo sem parâmetro de consumo: %r' % reg.get('tipo')

    # Rígido não segue a escala por idade: ela é de conjunto articulado.
    if tipo in TIPOS_RIGIDO:
        return CONSUMO_RIGIDO, 'rígido (%s) — média medida no ValeCard' % tipo

    ano = _ano(reg.get('ano'))
    idade = (ano_referencia - ano) if ano else IDADE_SEM_ANO
    for limite, kml in CONSUMO_POR_IDADE:
        if idade <= limite:
            faixa = ('até %d anos' % limite) if limite < 999 else 'acima de 10 anos'
            return kml, ('idade %d anos (%s)' % (idade, faixa) if ano
                         else 'sem ano no cadastro — faixa mais velha')
    return None, 'idade fora das faixas'


def _ano(valor):
    """Ano do cadastro (2 ou 4 dígitos) em 4 dígitos, ou None."""
    d = re.sub(r'\D', '', str(valor or ''))
    if len(d) == 4:
        return int(d)
    if len(d) == 2 and d != '00':
        n = int(d)
        return 2000 + n if n <= 26 else 1900 + n
    return None


def abastecido_por_placa(registros):
    """Litros e km por placa pelo MÉTODO DO HODÔMETRO.

    A tabela do ValeCard tem duas populações: a transação de cartão (com posto,
    cartão e hodômetro) e a importação manual do tanque interno (sem nada disso,
    só litros). Somar `nsd_distancia` conta litro completo contra km parcial e
    puxa o consumo para baixo; em placas que abastecem muito no tanque, distorce
    feio — o HGA9F47 dava 6,00 km/l assim, e 3,70 pelo hodômetro.

    O método correto fecha a janela entre o PRIMEIRO e o ÚLTIMO abastecimento com
    hodômetro, e nela numerador e denominador ficam consistentes:

        km     = hodômetro final − hodômetro inicial   (todo o rodado da janela)
        litros = TUDO abastecido na janela             (cartão + tanque interno)

    `registros` é iterável de dicts com 'placa', 'data', 'hodometro', 'litros'
    e 'produto'. Retorna {placa: (litros, km)}.
    """
    por_placa = {}
    for r in registros:
        if 'ARLA' in str(r.get('produto') or '').upper():
            continue
        p = mercosul(r.get('placa') or '')
        if p:
            por_placa.setdefault(p, []).append(r)

    acc = {}
    for p, linhas in por_placa.items():
        linhas.sort(key=lambda r: str(r.get('data') or ''))
        com_hod = [i for i, r in enumerate(linhas) if numero(r.get('hodometro')) > 0]
        if len(com_hod) < 2:
            continue
        ini, fim = com_hod[0], com_hod[-1]
        km = numero(linhas[fim].get('hodometro')) - numero(linhas[ini].get('hodometro'))
        # do abastecimento seguinte ao inicial até o final: é o diesel que rodou esse km
        litros = sum(numero(r.get('litros')) for r in linhas[ini + 1:fim + 1])
        if km > 0 and litros > 0:
            acc[p] = (litros, km)
    return acc


def observado_por_placa(viagens):
    """Histórico de pesos por placa de carreta, para alimentar a cascata.

    `viagens` é iterável de dicts com 'placa_carreta' e 'peso_total' (kg).
    """
    hist = {}
    for v in viagens:
        placa = mercosul(v.get('placa_carreta') or '')
        peso = peso_plausivel(v.get('peso_total'))
        if placa and peso:
            hist.setdefault(placa, []).append(peso)
    return hist
