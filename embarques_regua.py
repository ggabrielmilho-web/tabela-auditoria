# -*- coding: utf-8 -*-
"""REGUA DE CHEGADA — a fonte UNICA de "a carreta chegou, e quando".

Por que este modulo existe
==========================
Ate 09/09/2026 havia tres reguas de chegada no projeto, escritas separadamente:

  * `embarques_auto.reanalisar_pendentes` (branch) — raio de 20 km, estrito;
  * `_robo_atemporal.perto_com_parada`            — 20 km ou 60 km com parada >= 2 h;
  * `_auditoria_geral.chegada`                    — uma terceira copia da segunda.

Reguas separadas divergem sozinhas. O placar `F1` do aferidor oscilou 13 -> 32 -> 17 -> 38
so por causa disso (secao 20.6), e a divergencia entre motor e branch responde por 11 cargas
que descasariam no primeiro ciclo depois do deploy (secao 21.3). Medir com regua diferente da
do produto e fabricar defeito; escrever a regua duas vezes e garantir que isso aconteca.

Entao a regua passa a morar aqui, e quem precisa dela importa. Nao ha estado, nao ha banco,
nao ha I/O: so a decisao.

O defeito que este modulo conserta
==================================
A secao 4.3 do handoff especifica o evento assim: **"carreta entra no destino E PARA"**. O
codigo tinha perdido o "e para" — devolvia o primeiro ping dentro do raio, parado ou nao.

Medido sobre as 292 cargas de agosto com destino geocodificado:

    12,5-15,0 km    21 cargas  -  21 EM MOVIMENTO
    15,0-17,5 km    79 cargas  -  78 EM MOVIMENTO
    17,5-20,0 km   140 cargas  - 133 EM MOVIMENTO
     5,0- 7,5 km    11 cargas  -   0 em movimento   <- abaixo de 12,5 km o padrao inverte

**240 de 292 chegadas estavam carimbadas com o caminhao rodando.** O instante gravado nao era
a chegada: era a borda do anel de 20 km, o ponto em que a rodovia de acesso cruza a
circunferencia. Como caminhoes vindos da mesma origem cruzam no mesmo lugar, isso ainda
fabricava "clusters de doca" convincentes — seis destinos independentes com agrupamento
apertado, todos entre 16,7 e 20,4 km do centroide (secao 21.4).

Corrigido, 255 de 255 chegadas se moveram para FRENTE, p50 de 0,42 h, e o robo convergiu em
2 passadas. O ganho colateral: as 9 "velocidades impossiveis" que a guarda descartava sumiram
sozinhas — era o proprio anel que as fabricava, encurtando o tempo de viagem.
"""

import os as _os


def _f(nome, default):
    """Le a env var de producao. As chaves sao as MESMAS que o embarques_auto ja usava —
    a regua nao inventa configuracao, so passa a ser o unico lugar que a le."""
    try:
        return float(_os.getenv(nome, str(default)))
    except (TypeError, ValueError):
        return float(default)


PARADO_KMH = _f('RASTREAMENTO_PARADO_KMH', 3.0)             # espelha rastreamento_worker
RAIO_CHEGADA = _f('RASTREAMENTO_RAIO_CHEGADA_DESTINO', 20)  # conta como "no destino"
RAIO_METRO = _f('RASTREAMENTO_RAIO_METRO', 60)              # metropole — SO com parada
PARADA_MIN_H = _f('RASTREAMENTO_PARADA_MIN_H', 2.0)         # parada que prova presenca
RAIO_ORIGEM = _f('RASTREAMENTO_RAIO_ORIGEM', 30)            # esteve na origem
RAIO_SAIDA_DESTINO = _f('RASTREAMENTO_RAIO_SAIDA_DESTINO', 30)  # saiu do destino

# HORIZONTE DE EVIDENCIA — ate onde vale procurar resposta depois do carregamento.
# Sao 30 dias porque e ali que a evidencia de GPS acaba (a retencao da 3S), e porque e a
# janela de reanalise que a secao 13.4 dimensionou. O motor usava 20 e o aferidor tambem:
# na base local isso truncava so 1 carga, porque ela tem 5 semanas de fundo e nao existe
# pendencia com mais de 20 dias. Em producao, com historico fundo, o motor pararia de
# procurar 10 dias antes de a reanalise parar de perguntar — defeito invisivel no
# laboratorio POR CONSTRUCAO (secao 21.6).
JANELA_EVIDENCIA_D = int(_f('EMBARQUES_AUTO_JANELA_REANALISE_DIAS', 30))


def parado(v):
    """Velocidade que conta como parado. Nulo conta como parado, igual ao worker —
    mas na base de agosto isso nunca acontece: 0 nulos em 560.809 posicoes."""
    return v is None or float(v) <= PARADO_KMH


def primeira_parada_sustentada(pontos, min_h=PARADA_MIN_H, tol_km=3.0):
    """Instante da 1a parada que DURA `min_h` no mesmo lugar. `pontos` = (d, km, vel).

    Existe porque "parou" e "chegou" nao sao a mesma coisa a 60 km do destino. A
    C-2026-000684 (Brasilia) parou 20 min em Luziania a 56,9 km, depois 34 min a 40,7 km, e
    so encostou de verdade a 24,8 km — onde ficou 15 horas. Pegar a primeira parada faria a
    regra larga repetir o erro do anel um raio para fora, que e literalmente o fantasma que
    a secao 17.2 mediu ao reprovar o raio maior: "antecipa a chegada em cargas que pararam
    na periferia antes de entregar".

    "No mesmo lugar" e aproximado pela distancia ao alvo: com o veiculo parado, ela nao varia.
    """
    for i, (d0, k0, v0) in enumerate(pontos):
        if not parado(v0):
            continue
        fim = d0
        for d, k, v in pontos[i + 1:]:
            if abs(k - k0) > tol_km:
                break
            fim = d
        if (fim - d0).total_seconds() / 3600.0 >= min_h:
            return d0
    return None


def perto_com_parada(dd, raio_estrito=RAIO_CHEGADA, raio_largo=RAIO_METRO,
                     exigir_parada=False):
    """(instante, como) da presenca no alvo. `dd` = triplas (instante, km, velocidade)
    em ordem cronologica, ja recortadas pela janela da viagem.

    Com `exigir_parada` (o caminho da CHEGADA) ha quatro desfechos, com o numero de cada um
    medido sobre as 302 chegadas de agosto:

      274  ha ponto PARADO no raio estrito  -> 'estrita', a afirmacao mais forte
       15  so em movimento, e a serie CALA  -> 'presumida_silencio'. O rastreador da carreta
                                              dorme ao encostar (secao 20.4), entao silencio
                                              DEPOIS de entrar no raio nao desmente a
                                              chegada — silencio nunca e evento, nem contra.
                                              Usa o ULTIMO ponto no raio: e o mais proximo do
                                              repouso real e ainda e um piso.
       10  nada no raio estrito             -> cai na tolerancia de metropole, que exige
                                              parada SUSTENTADA (ver acima)
        3  so em movimento, e a serie SEGUE -> 'passou_sem_parar'. Prova POSITIVA de
                                              nao-chegada: esteve no raio, nunca parou, foi
                                              embora. Mas ainda cai no teste largo antes de
                                              desistir, porque pode ter estacionado FORA do
                                              raio estrito (foi o caso da C-2026-000684).

    A assimetria entre os dois raios e proposital: a 20 km do centroide uma parada curta ja e
    provavelmente a entrega, e exigir duracao perderia a carreta que dorme logo depois de
    encostar; a 60 km, uma parada curta e provavelmente um posto de estrada.

    SEM `exigir_parada` o comportamento e o antigo (primeiro ping no raio), e e assim que fica
    na ORIGEM: la a pergunta e "esteve la?", e a saida se deriva do ultimo ponto dentro do
    raio. Exigir parada ali mexeria na deteccao de saida, que precisa de 11 correcoes contra
    167 da chegada — nao se conserta o que esta funcionando.
    """
    passou = False
    no_raio = [(d, k, v) for d, k, v in dd if k <= raio_estrito]
    if no_raio:
        if not exigir_parada:
            return no_raio[0][0], 'raio'
        p = next((d for d, k, v in no_raio if parado(v)), None)
        if p:
            return p, 'estrita'
        if dd and no_raio[-1][0] >= dd[-1][0]:
            return no_raio[-1][0], 'presumida_silencio'
        passou = True

    largo = [(d, k, v) for d, k, v in dd if k <= raio_largo]
    if len(largo) > 1:
        h = (largo[-1][0] - largo[0][0]).total_seconds() / 3600
        if h >= PARADA_MIN_H:
            q = largo[0][0]
            if exigir_parada:
                q = primeira_parada_sustentada(largo)
                if q is None:
                    # Ficou 2 h na regiao sem assentar em lugar nenhum: rodou pela area
                    # metropolitana. Isso nao prova chegada — nao afirma.
                    return None, ('passou_sem_parar' if passou else None)
            return q, f'metropole({min(k for _, k, v in largo):.0f}km/{h:.0f}h)'
    return None, ('passou_sem_parar' if passou else None)


def chegada(dd):
    """Atalho: a regua da CHEGADA, com parada exigida. Motor e aferidor usam ESTA."""
    return perto_com_parada(dd, RAIO_CHEGADA, RAIO_METRO, exigir_parada=True)


SONO_CARRETA_H = 48.0


def chegada_emprestada(cheg, como, dd_carreta, dd_cavalo, sono_h=SONO_CARRETA_H):
    """CARRETA DORMIDA (17/09/26). A carreta e a identidade e continua sendo o sensor; mas se a
    serie dela nao tem ponto NENHUM nas `sono_h` seguintes a chegada que o cavalo mostra (o
    cavalo anda junto ate o desengate), o INSTANTE da chegada e o do cavalo. So empresta quando
    e anterior ao da carreta — nunca atrasa.

    Caso-tipo C-2026-000632: a carreta HMV3D38 deu 6 pontos em 10 dias, todos ao acordar no
    patio 9 dias depois (odometro +455 km = a mesma viagem, reportada tarde); o cavalo tinha
    6.745 pontos e a chegada em 02/09. Sem esta regra o motor gravou 11/09.
    Carreta que TRANSMITE nas 48 h e nao para no destino nao e sono: e documento/desengate
    (licao da C-913) — e ai a regra nao se aplica.

    `dd_carreta` = triplas (instante, km, vel) da carreta na janela inteira (sem piso/teto);
    `dd_cavalo` = idem do cavalo, JA recortado pelo mesmo piso/teto da carreta."""
    from datetime import timedelta
    if not dd_cavalo:
        return cheg, como
    ccav, ccomo = chegada(dd_cavalo)
    if not ccav or (cheg is not None and ccav >= cheg):
        return cheg, como
    a, b = ccav - timedelta(hours=1), ccav + timedelta(hours=sono_h)
    if any(a <= d <= b for d, _, _ in dd_carreta):
        return cheg, como
    return ccav, (ccomo or 'estrita') + '+cavalo'


# ══════════════════════════════════════════════════════════════════════════════
# POSICAO FALSA — a regua do que o veiculo NAO pode ter feito (10/09/2026)
# ══════════════════════════════════════════════════════════════════════════════
#
# O historico de posicoes contem pontos comprovadamente falsos, e ate hoje ninguem os
# filtrava. Nao e furo de sinal: sao dois pontos consecutivos da MESMA placa, minutos um
# do outro, separados por centenas de quilometros. O caso que o Gabriel viu na tela:
#
#     TYX9F52  07/09 07:37  Formosa   -> Jaborandi  257,5 km em 2,0 min  odo 37487 -> 37487
#     TYX9F52  07/09 07:57  Jaborandi -> Formosa    257,5 km em 2,0 min  odo 37487 -> 37487
#     HKE0321  08/09 14:30  Sta Luzia -> Serra      374,4 km em 3,5 min  odo 376904 -> 376904
#
# Foi e voltou, com o ODOMETRO CONGELADO no mesmo numero. O odometro e cumulativo no
# aparelho e independente do GPS — se o caminhao tivesse rodado 374 km ele marcaria +374.
# E o mesmo arbitro que a §12.3 ja elegeu como fonte de km ("atravessa buraco de sinal").
#
# ESCALA, medida na base local em agosto/setembro (fita 100% backfillada desde 13/08):
#     301 pares impossiveis em 34 placas · 235 com o odometro negando o deslocamento
#     327 buracos LEGITIMOS (salto grande, mas velocidade plausivel para o tempo decorrido)
#      74 de 334 mapas (22%) com pelo menos uma posicao falsa desenhada na linha
#
# O ESTRAGO ia alem do desenho: o km do KPI somava a distancia fantasma. A C-2026-000503
# publicava 1.864 km com um salto falso de 718 km dentro. As tres camadas de plausibilidade
# do KPI (`_kpi_sanidade`, `_kpi_plausibilidade`, `_kpi_sem_chegada`) pegavam 5 dos 74 —
# elas defendem o NUMERO no atacado, nao a perna individual.
#
# O TESTE QUE DECIDIU: nao da para saber QUAL dos dois pontos e o falso, entao nao se
# escolhe — apenas nao se soma a perna impossivel. Se a tese estiver certa, o km do GPS
# tem de convergir com o odometro, que e testemunha independente:
#
#     erro medio contra o odometro:  CRU 415,8%  ->  LIMPO 7,5%   (52 melhoraram, 12 nao)
#     C-2026-000486  cru 765,4  limpo  40,9  odo   41   -> 0,4%
#     C-2026-000503  cru 1864,0 limpo 1145,3 odo 1141   -> 0,4%
#     C-2026-000495  cru 1405,4 limpo  914,3 odo  914   -> 0,0%
#
# E POR QUE A LINHA QUEBRA EM VEZ DE EMENDAR: desenhar um vao e honesto ("nao sabemos como
# ele foi de A ate B"); adivinhar qual ponto descartar e chute com cara de dado. Mesmo
# criterio do "—" da §12.13 — numero sem lastro nao se publica.

TETO_KMH = _f('RASTREAMENTO_TETO_KMH', 150.0)   # teto fisico de um cavalo mecanico
SALTO_MIN_KM = _f('RASTREAMENTO_SALTO_MIN_KM', 30.0)  # abaixo disso e jitter, nao teleporte


def _instante(v):
    from datetime import datetime as _d
    if hasattr(v, 'year'):
        return v
    try:
        return _d.fromisoformat(str(v).replace('Z', ''))
    except Exception:
        return None


def perna_impossivel(a, b, teto_kmh=None, salto_min_km=None):
    """True quando o trecho a->b nao pode ter acontecido.

    Dois testes, e basta um. O primeiro e fisico e nao precisa de odometro: deslocamento
    grande num tempo curto demais. O segundo e a testemunha: o odometro do aparelho nao
    andou o que a distancia afirma. O odometro so entra quando existe nos dois lados — em
    ponto vindo do polling ao vivo ele e nulo, e ausencia nao e prova de nada.
    """
    import geocoding
    teto = TETO_KMH if teto_kmh is None else teto_kmh
    minimo = SALTO_MIN_KM if salto_min_km is None else salto_min_km
    km = geocoding.km_entre(a.get('lat'), a.get('lng'), b.get('lat'), b.get('lng'))
    if km is None or km <= minimo:
        return False
    ta, tb = _instante(a.get('data')), _instante(b.get('data'))
    if ta and tb:
        h = (tb - ta).total_seconds() / 3600.0
        if h > 0 and (km / h) > teto:
            return True
    oa, ob = a.get('odometer'), b.get('odometer')
    if oa is not None and ob is not None:
        try:
            if (int(ob) - int(oa)) < km * 0.5:
                return True
        except (TypeError, ValueError):
            pass
    return False


def cortes_do_trajeto(pontos, teto_kmh=None, salto_min_km=None):
    """Indices i em que a linha tem de QUEBRAR entre pontos[i] e pontos[i+1]."""
    if not pontos or len(pontos) < 2:
        return []
    return [i for i in range(len(pontos) - 1)
            if perna_impossivel(pontos[i], pontos[i + 1], teto_kmh, salto_min_km)]


def segmentos(pontos, teto_kmh=None, salto_min_km=None):
    """O trajeto partido nos trechos contiguos que sao possiveis.

    Um segmento de um ponto so nao vira linha (Leaflet nao desenha), mas fica na lista:
    quem quiser marcar "aqui houve um salto" precisa saber que existe ponto ali.
    """
    if not pontos:
        return []
    fora = set(cortes_do_trajeto(pontos, teto_kmh, salto_min_km))
    out, atual = [], [pontos[0]]
    for i in range(len(pontos) - 1):
        if i in fora:
            out.append(atual)
            atual = []
        atual.append(pontos[i + 1])
    out.append(atual)
    return out


def sem_posicao_falsa(linhas, teto_kmh=None, salto_min_km=None):
    """Tira da serie as DUAS pontas de todo trecho impossivel (§23.3): nao se sabe qual das
    duas posicoes e a falsa, entao nenhuma serve de EVIDENCIA de saida/chegada. Descoberto em
    15/09/26 na C-2026-000902: o rastreador acordou mandando "Uberlandia" e 3 min depois
    "Bom Jesus do Amparo" (513 km, odometro parado) — o motor tomou o ping falso como
    "visto na origem" e fabricou a saida, que ele nunca apaga.

    `linhas` = tuplas (data, lat, lng, velocidade, odometer). Devolve (data, lat, lng, vel)."""
    pts = [{'data': d, 'lat': la, 'lng': ln, 'vel': v, 'odometer': o} for d, la, ln, v, o in linhas]
    fora = set()
    for i in range(1, len(pts)):
        if perna_impossivel(pts[i - 1], pts[i], teto_kmh, salto_min_km):
            fora.add(i - 1); fora.add(i)
    return [(p['data'], p['lat'], p['lng'], p['vel']) for i, p in enumerate(pts) if i not in fora]
