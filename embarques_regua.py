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

PARADO_KMH = 3.0          # espelha rastreamento_worker.PARADO_KMH
RAIO_CHEGADA = 20.0       # raio do centroide que conta como "no destino"
RAIO_METRO = 60.0         # tolerancia de metropole — SO com parada sustentada
PARADA_MIN_H = 2.0        # parada que prova presenca (passagem dura minutos)
RAIO_ORIGEM = 30.0        # esteve na origem
RAIO_SAIDA_DESTINO = 30.0 # saiu do destino


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
