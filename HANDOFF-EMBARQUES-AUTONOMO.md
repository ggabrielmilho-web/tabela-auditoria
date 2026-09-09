# Handoff — Painel de Embarques autônomo

**Estado em 09/09/2026 — ⚠ COMECE PELA §21.** Nove rodadas de adjudicação (sem tocar em
arquivo nenhum) acharam que **o instante de chegada da base inteira está enviesado por
construção**: o código marca chegada no primeiro ping dentro do raio, sem exigir parada, e
**240 de 292 chegadas foram carimbadas com o caminhão rodando** — a §4.3 sempre mandou
"entra no destino **e para**". Isso reordena a fila: consertar muda a régua, e mudar a
régua exige **reconvergir a base** antes que qualquer medição posterior valha. A §21 traz
os quatro defeitos novos, as dez conclusões derrubadas com o número que derrubou cada uma,
e o checklist de pré-deploy de dez itens.

**Antes disso:** a §20 (a 3S voltou; agosto e setembro reprocessados pelo robô na base
local, defeitos de 272 para 181 cargas, incoerências temporais a zero) e a §19 (o corte da
3S). O modelo carreta-cêntrico segue **FORA DA `main`**, na branch
`modelo-carreta-3s-congelado`; a `main` está no estado de produção.

> ⚠ **Suspeita aberta (§20.10):** produção pode estar rodando uma imagem antiga — há carga
> fechada por `baixa_ctrb`, regra removida do código em 03/09. Não verificado.

> 🔒 **O `.env` tem `START_WORKER=true`.** Subir o servidor local com `python server.py`
> direto **liga o worker e ele reprocessa a base convergida**, desfazendo o que a §20
> construiu. Sempre sobrepor no comando:
> `START_WORKER=false EMBARQUES_AUTO=false PGR_SYNC_CADASTRO=false python -X utf8 server.py`

| | estado |
|---|---|
| **o robô (`embarques_auto.py`)** | na `main`, **igual ao de produção**. O fechamento reescrito (§18) vive na branch `modelo-carreta-3s-congelado`, e lá ainda atrás de `EMBARQUES_MODELO_CARRETA=false`. A abertura não foi tocada em lugar nenhum |
| **KPI, alertas e correções de mapa** | também na branch — saíram da `main` em 07/09 |
| **rastreamento (3S)** | **DE VOLTA em 08/09/26** — 93 veículos, 38 placas com posição < 1 h. Ficou o rastro: 5 carretas novas mudas desde 01–03/09 (§20.1) |
| **motor + aferidor novos** | `_robo_atemporal.py` e `_auditoria_geral.py` — **VERSIONADOS em 09/09** na branch (`66451b4`, tag `estudo-embarques-2026-09-08`), junto deste handoff, dos simuladores e dos CSVs. Convergem a zero em 3 passadas (§20.6), e a passada de 09/09 confirmou: **0 alterações em 12,5 s** |
| **a adjudicação (§21)** | 9 rodadas, **nenhum arquivo alterado**. Achou o defeito do anel (240 de 292 chegadas marcadas em movimento), as duas réguas (11 cargas), a divergência dupla da §4.3 e a bimodalidade do worker (cauda de 53, p50 9,2 h) |
| agosto + setembro reprocessados 100% pelo robô (370 cargas) | **só no banco LOCAL** — §20.7 |
| `manifesto_origem` | **276/276 cargas reais com chave** (§20.2); as 63 vazias sem chave estão corretas |
| rota planejada (ORS) | **370/370** em agosto e setembro |
| camada de consolidação diária | **commitada** (`7de4860`) e no ar |
| auditoria dos 324 mapas | **rodada em 07/09** — §14; achou 1 bug na sanidade geométrica (§14.4) |
| auditoria de chegada/fechamento | **rodada em 07/09** — §15; o raio de 20 km no centroide acusa falso em metrópole |
| os 9 consertos da fila | **7 aplicados e medidos, 1 derrubado pelo teste, 1 é decisão de negócio** — §17 |

As medições foram feitas sobre agosto/2026 com dado real de produção — 433 mil posições
GPS, 229 cargas, 2.281 CTes.

> ⚠ **A ordem importa:** subir os dados de agosto **antes** de consertar o robô é limpar
> com a torneira aberta — ele refaz os mesmos ~32 fechamentos errados por mês e remexe as
> 12 cargas reabertas. Ver §13.3.

O objetivo é ter dois modelos convivendo:

- o **painel manual** de hoje, que funciona (400 cargas lançadas à mão sem problema) e
  fica preservado para o dia em que o operacional passar a lançar;
- um **modelo autônomo**, desenhado de origem para operar sem humano, que é o que este
  documento especifica.

---

## 0. Como voltar ao lançamento manual (leia antes de mexer em qualquer coisa)

**Uma variável de ambiente, sem deploy, sem perder nada:**

```
EMBARQUES_AUTO=false      no Portainer
```

O robô para no ciclo seguinte. O lançamento manual volta a ser o único caminho e
continua funcionando — porque **ele nunca dependeu do robô**. Está escrito na própria
`embarques_auto.ligado()`:

> *"DESLIGADO por default: se o operacional voltar a lançar, basta `EMBARQUES_AUTO=false`
> no Portainer e o motor para — sem deploy, sem remover código, sem perder o que já foi
> criado."*

Verificado em 04/09/2026: a única referência a `embarques_auto` no `server.py` é a thread
que sobe no boot. O formulário (`/embarques/novo`), o `POST /api/embarques/cargas`, a
edição e o histórico **não têm nenhuma chamada para o robô**.

### A tag NÃO é o mecanismo de volta

Existe a tag `modelo-manual-2026-09` (aponta para `7de4860`), e ela é útil para
**comparar** — `git diff modelo-manual-2026-09` mostra tudo que mudou desde o
congelamento. Mas voltar o código para ela **descarta todo o trabalho posterior**, o que
não é o que se quer quando a decisão é apenas "o operacional vai lançar à mão".

| | o que faz | custo |
|---|---|---|
| `EMBARQUES_AUTO=false` | desliga o robô, mantém tudo o mais | **nenhum** — é a forma certa |
| `git checkout modelo-manual-2026-09 -- .` | volta o código inteiro àquele dia | perde tudo que veio depois |

### A regra que mantém essa garantia viva

**O modelo autônomo é ADITIVO.** Ele pode acrescentar status, colunas, telas e regras —
nunca alterar o caminho manual. Enquanto isso valer, desligar o robô sempre devolve um
sistema íntegro, e qualquer coisa construída depois continua no lugar.

Concretamente, **não mexer** (sem substituir esta garantia por outra):

- o formulário `/embarques/novo` e o `POST /api/embarques/cargas`
- a validação `_validar_carga_payload` e o `_buscar_conflitos`
- os status que as telas entendem: `Aberta` · `Em rota` · `No destino` · `Desengatada` ·
  `Entregue` · `Cancelada` — status novo pode ser **acrescentado**, nenhum pode ser
  removido ou ter o significado trocado
- o `embarques_cargas_log`, que é a trilha de auditoria de quem lançou à mão

Mudança que precise tocar num desses itens deveria ficar atrás da própria chave, não
substituir o que existe.

---

## 1. A descoberta que reorganiza tudo

O painel de hoje trata **carga** e **viagem** como a mesma coisa. Não são, e quase todo
erro medido nasce disso.

| | do que se trata | começa | termina | fonte |
|---|---|---|---|---|
| **viagem do veículo** | a carreta rodando | manifesto N | manifesto N+1 da mesma carreta | **manifesto** |
| **trajeto da mercadoria** | o CTe | `primeiro_manifesto` | chega ao destinatário no `ultimo_manifesto` | **CTe** |

A relação é **muitos-para-muitos**:

- uma mercadoria pode atravessar **duas viagens** — 65 CTes de agosto (2,8%) têm
  `primeiro_manifesto ≠ ultimo_manifesto`, ou seja, trocaram de veículo no meio;
- uma viagem pode levar mercadoria de **vários destinos** — 22 manifestos de agosto (4%)
  têm mais de uma cidade de destino, sendo 10 com duas cidades e **12 com 16 a 27**
  (o extremo é `UDI0286079`, com 110 CTes para 27 cidades do RJ).

Modelado como um-para-um, o sistema fecha a viagem e "entrega" mercadoria que seguiu em
outro veículo; escolhe um destino por acaso entre vários; e lê transbordo no armazém como
falha de rastreamento.

---

## 2. As três fontes e o que cada uma garante

| fonte | cobertura | serve para |
|---|---|---|
| **manifesto** | **100% — não tem furo** | existência e limites da viagem |
| **GPS (3S)** | 94% das cargas · 6% cegas | quando saiu, chegou, quanto rodou, onde parou |
| **CTe** | 71,5% com manifesto único | o trajeto e a entrega da mercadoria |

> **O manifesto é a fundação, não o GPS.** Toda carga precisa gerar manifesto por
> exigência do CIOT — é obrigação legal, então não existe viagem sem manifesto. O GPS é
> mais preciso mas tem buraco; o manifesto nunca falta. Um desenho que apoia o
> fechamento no GPS quebra nas 6% de cargas cegas; apoiado no manifesto, não quebra
> nunca.

Os 586 CTes sem manifesto (25,7%) **não são falha**: são `COMPLEMENTAR FRETE`,
subcontratação e substitutos — entram no R$, não no nº de viagens. É a mesma regra que o
Módulo Faturamento por Tomador já aplica.

### Quem o GPS enxerga

```
carretas ......... 93% com rastreador (64 de 69)
cavalos .......... 27% (14 de 51)
cavalo trucado ... 50% (1 de 2)
truck ............ 100% (1 de 1)
```

**A carreta é o sensor.** Consequência direta: o km de um cavalo não sai da camada de
rastreamento sozinho — dos 186 cavalos com manifesto em agosto, só 17 têm GPS próprio.
Para atribuir km ao cavalo é preciso o par cavalo↔carreta, que vem do manifesto. Com as
cargas existentes, 64% do km de carreta (137.596 de 216.229) já é atribuível.

---

## 3. O que foi medido em agosto/2026

### 3.1 O fechamento das cargas erra 25%

Régua: a posição da **carreta carregada** no instante em que a carga foi fechada,
comparada com o momento em que ela chegou ao destino.

```
229 cargas
 36  fecharam ANTES de a carreta chegar
 21  fecharam e a carreta NUNCA chegou
 --
 57  fechamentos indevidos (25%)
```

Por motivo: `baixa_ctrb` 25 · **`manifesto_novo` 23** · GPS 6 · `sequencia_viagem` 3.

E por taxa de erro de cada regra (% fechada a mais de 100 km do destino):

| regra | cargas | no destino | longe (>100 km) |
|---|---|---|---|
| GPS (worker) | 111 | 73% | **10%** |
| `baixa_ctrb` — removida em 03/09 | 48 | 39,6% | **50,0%** |
| `manifesto_novo` — em produção | 36 | 41,7% | **47,2%** |
| `sequencia_viagem` — em produção | 7 | 42,9% | **57,1%** |

> ⚠ **A correção de 03/09 mirou no infrator errado.** Tirar o `baixa_ctrb` resolveu 25
> dos 57 casos; as duas regras que ficaram erram na mesma proporção e continuam no ar.
> Sobram ~32 fechamentos errados por mês (14%).

### 3.2 Por que o `manifesto_novo` erra

A regra casa **qualquer placa contra qualquer papel**:

```python
for bruta in (man['placa_cavalo'], man['placa_carreta']):
    SELECT id FROM embarques_cargas
     WHERE (cavalo_placa=%s OR carreta1_placa=%s OR carreta2_placa=%s)
```

Quando o cavalo larga a carreta e sai com outra, o manifesto novo **dele** fecha a carga
da carreta que ficou. Das 84 trocas de carreta de agosto:

```
57  fim normal (a carreta já tinha chegado ao destino)   → acerta
12  TROCA DE CAVALO (a carreta seguiu, até 1.933 km)     → erra
 7  NUNCA CHEGOU AO DESTINO                              → erra
 8  carreta sem sinal
```

Restringir a regra à **mesma carreta** derruba o erro de ~48% para **17%** (125 de 165
casos a carreta já havia chegado). Uma carreta carregada não fica em dois lugares; um
cavalo fica.

### 3.3 O destino registrado é hipótese, não fato

Destino que a carga gravou (vem do CTRB) × destinos dos CTes do mesmo manifesto:

```
bate com cidade_destinatario ..... 120  (70,2%)
bate com cidade_entrega .........    2  ( 1,2%)
NÃO bate com nenhum CTe .........   49  (28,7%)
```

Os 70,2% confirmam ao ponto os 69% que o README já registrava. Nos 28,7% o padrão é
único: **o CTe aponta Uberlândia e a carga aponta outro lugar.**

### 3.4 Transbordo no armazém de Uberlândia

Explica mais da metade das cargas que "nunca chegaram":

```
178  (88%)  chegou ao destino registrado
 13  ( 6%)  TRANSBORDO — parou em UDI e saiu com manifesto novo
  5  ( 2%)  parou em UDI, manifesto novo ainda não veio
  4  ( 2%)  manifesto novo sem passar por UDI
  2  ( 1%)  sem explicação
```

**São dois transbordos diferentes e o discriminador é o CTe:**

- **transbordo de mercadoria** — `primeiro_manifesto ≠ ultimo_manifesto`: a carga seguiu
  em outro veículo, a entrega **não** aconteceu. 65 CTes (2,8%), 92 manifestos.
- **troca de carga no veículo** — os 13 acima: nenhum CTe seguiu para outro manifesto,
  então a mercadoria ficou (descarregada no armazém ou entregue) e a carreta pegou carga
  nova. A viagem daquele veículo terminou de verdade.

Contexto operacional (do Gabriel): Uberlândia tem armazém. A carreta pode parar, ser
descarregada e sair com outro manifesto; ou o motorista deixa a carreta, vai a outra
cidade coletar/entregar e volta — ou outro cavalo termina. **A nova regra do CIOT
(Res. ANTT 6.078/2026, vigor 24/05/2026) reduziu muito essa prática**; os 6% de agosto já
são o patamar reduzido.

### 3.5 Os furos de rastreamento

```
2.574 lacunas > 30 min dentro de viagem
  95%  veículo PARADO — comportamento normal do aparelho, não cegueira
   5%  em movimento — 131 lacunas, 22.675 km no escuro (6,8% do mês)
  14   cargas totalmente cegas (6%) — 13 Agregado, 1 Frota
```

Mesma lição do PGR: parado, o aparelho reporta de hora em hora, então lacuna longa é
normal. O discriminador é a velocidade implícita (deslocamento ÷ duração).

> ⚠ **CORRIGIDO em 04/09/2026.** Este parágrafo dizia que *"o odômetro atravessa o
> furo"* e estava **errado**. Existem dois furos diferentes e ele só atravessa um. Ver
> [§12.3](#123-o-odômetro-não-atravessa-todo-furo--a-correção-mais-importante-do-dia).

### 3.6 O robô abre bem — o problema é só no fechamento

No escopo dele (Frota/Agregado) e desde que entrou no ar (19/08):

```
manifestos no escopo: 168     sem carga: 2  (1%)
```

Os "398 manifestos sem carga" de agosto são 304 anteriores ao robô entrar no ar e 196 de
**CARRETEIRO**, que ele não abre por design. Nada a corrigir na abertura.

---

## 4. O modelo proposto

### 4.1 Eixo: a carreta

A carga acompanha a **carreta**, não o cavalo. O cavalo é recurso acoplável, com
histórico dentro da viagem (quem puxou de quando a quando). Isso conserta de uma vez os
12 casos de troca de cavalo e o efeito que já era conhecido — *"troca de cavalo vira 2
cargas para 1 viagem e parece erro sem ser"* passa a ser **1 viagem com 2 cavalos**, que
é o que de fato aconteceu.

### 4.2 Dois eventos que hoje são um só

| dimensão | fonte | evento |
|---|---|---|
| **entrega** | GPS / CTe | carreta chegou ao destino, saiu dele, ou transbordou |
| **encerramento** | manifesto | a carreta recebeu outro manifesto → a viagem acabou |

Separadas, a carreta pode estar **entregue e ainda comprometida** (parada no destino
aguardando novo manifesto) — que é justamente o estado que o operacional precisa ver para
decidir o que fazer com ela.

### 4.3 A tabela de regras

| evento | sinal | efeito |
|---|---|---|
| carreta sai da origem | GPS > 30 km | `Em rota` |
| carreta entra no destino e para | GPS ≤ 25 km | `No destino` |
| carreta sai do destino **ou** fica 24 h nele | GPS | `Entregue` |
| carreta para em UDI e sai com manifesto novo, sem CTe seguindo | GPS + CTe | `Transbordo` |
| cavalo aparece em manifesto novo com **outra** carreta | documento | **troca o cavalo — não fecha** |
| manifesto novo da **mesma** carreta | documento | encerra a viagem anterior |
| silêncio | — | **nada.** Fica pendente e rotulada |

> A cláusula das 24 h não é detalhe: sem ela, carga cujo destino é a própria base só
> fecharia na viagem seguinte. Foi um defeito da primeira simulação, não do modelo.

### 4.4 Regra de ouro

**Silêncio nunca é evento.** Não fechar, não entregar, não inferir chegada por ausência.
É essa regra que zera os 21 casos de "fechou e a carreta nunca chegou".

---

## 5. O robô atemporal

O robô deixa de ser um **decisor diário** e vira um **convergente**: cada rodada não olha
só ontem, reabre as perguntas que ficaram sem resposta e vê se o mundo respondeu. O
passado fica mais correto com o tempo em vez de congelado errado.

Simulado sobre agosto:

```
229 cargas
183  o modelo fecha na hora (80%)
 46  ficam PENDENTES (24 nunca chegaram + 22 cegas)
      40  resolvem numa reanálise posterior (87%)
       6  sobram para o humano — 0,2 por dia
```

A evidência que resolve é quase sempre a mesma: **manifesto novo da mesma carreta**. Uma
carreta carregada não fica em dois lugares, então quando ela sai de novo a viagem
anterior acabou — mesmo que o GPS nunca tenha visto. É o que cobre as 22 cargas cegas.

```
C-2026-000083  Agregado  cega (sem GPS)           → manifesto novo da MESMA carreta em 24/08
C-2026-000376  Frota     nunca chegou ao destino  → manifesto novo da MESMA carreta em 19/08
```

**Placar contra o sistema atual:** 96% das cargas explicadas automaticamente, 2 sem
explicação no mês inteiro — contra 57 fechamentos indevidos (25%) que ninguém vê.

### Convergência × imutabilidade

O módulo Contábil é append-only de propósito ("mês fechado não pode mudar"). No
operacional é o contrário: revisar é a função. A conciliação é o
`embarques_cargas_log`, que já grava diff por campo — toda revisão deixa rastro, então
converge sem apagar história.

---

## 6. Viagem vazia — funciona e não depende de rastreador

Reconstruída pelos manifestos: perna vazia = destino do manifesto N → origem do
manifesto N+1, mesma carreta.

```
108 carretas com 2+ viagens
266 pernas vazias · 97.319 km · média 366 km por perna
112 vezes a carreta recarregou NO MESMO LUGAR onde descarregou (o caso ideal)
```

As recorrentes — que são a lista de prospecção de frete de retorno:

```
UBERLANDIA/MG       → EXTREMA/MG            14×   6.829 km
RIO DE JANEIRO/RJ   → CORDEIROPOLIS/SP      10×   4.388 km
GUARULHOS/SP        → EXTREMA/MG             9×     632 km
FEIRA DE SANTANA/BA → MONTES CLAROS/MG       5×   3.617 km
```

---

## 7. O que foi descartado, e por quê

| ideia | por que caiu |
|---|---|
| GPS como fundação do fechamento | 6% das cargas são cegas; o manifesto nunca falta |
| Fechar por `baixa_ctrb` | baixa administrativa da OS, não chegada física — 50% de erro |
| Fechar por `timeout` | fecha por idade, sem viagem nova nenhuma |
| Fechar pelo manifesto do **cavalo** | 47% de erro; o cavalo troca de carreta, a carreta não se divide |
| Odômetro do Autotrac | a Rizza não tem o pacote de rede CAN — não há km ali |
| Recuperar julho pelo GPS | banco purga aos 30 dias, a 3S retém ~35; julho não existe em lugar nenhum |
| Persistir a atribuição cavalo↔carreta | cargas não são purgadas — resolve na consulta, sempre atual |

---

## 8. O que falta decidir

1. **Transbordo vira status próprio?** São 6% das cargas, com endereço conhecido e causa
   operacional real (armazém de Uberlândia). Hoje é lido como erro.
2. ~~**Carreteiro entra no robô?**~~ **DECIDIDO em 09/09/2026 (Gabriel): NÃO — fora de
   escopo.** Carreteiro carrega uma vez ou outra; o modelo autônomo é para **Frota e
   Agregado**, e é assim que o código já está (`EMBARQUES_AUTO_TIPOS=Frota,Agregado`,
   com Terceiro fora por design). Os 196 manifestos e a fatia dos 36% de km de carreta
   sem dono **não são lacuna a fechar** — são volume que o modelo não persegue.

   Consequência para o desenho: o "balde de período não documentado" do ladrilhamento
   perde a pergunta que o justificava. Ele continua útil como **rótulo** ("este buraco na
   linha do tempo da carreta é provavelmente Carreteiro, ignore"), nunca como métrica a
   ser reduzida.
3. **A carga deve carregar todos os destinos do manifesto?** Para os 12 manifestos de
   distribuição (16 a 27 cidades) um destino só não tem significado. "Chegou ao destino"
   viraria "entregou N de M".
4. **Reabrir as cargas fechadas indevidamente em agosto?** Existe o
   `_reabrir_fechamento_indevido.py`, hoje escrito para `baixa_ctrb`/`timeout`.

---

## 9. O que falta testar

1. **Remedir o km vazio com o eixo na carreta.** Os 34,3% que a camada diária reporta
   estão inflados pelos fechamentos prematuros — carga fechada cedo faz o veículo parecer
   livre, e o km que ele ainda rodou entregando conta como vazio.
2. **Particionar o dia pela janela da carga.** No grão diário, um dia que teve *qualquer*
   carga ativa conta inteiro como produtivo; a perna vazia da manhã some.
3. **Separar parado-no-pátio de parado-no-cliente.** São 28.542 h paradas contra 7.227 em
   movimento. Pátio é ociosidade nossa; cliente é tempo de espera, que é verba e
   discussão comercial.
4. **As 2 cargas sem explicação** — não foram olhadas uma a uma.

---

## 10. Como reproduzir as medições

O estudo rodou sobre o banco **local** carregado com dumps de produção:

```bash
# no servidor
docker exec -i $(docker ps -q -f name=postgres) psql -U postgres -d rizza_auditoria -c \
  "COPY (SELECT placa,id_veiculo_3s,data_posicao,latitude,longitude,velocidade,ignicao,
                uf,cidade,endereco,odometer
           FROM embarques_posicoes_historico WHERE data_posicao >= '2026-08-01'
          ORDER BY placa,data_posicao) TO STDOUT WITH CSV HEADER" | gzip > posicoes_ago.csv.gz
# + embarques_cargas e embarques_cargas_destinos no mesmo formato
```

Carregados no Postgres local via `COPY` para tabela temporária + `INSERT ... ON CONFLICT
DO NOTHING`, e consolidados com `python -X utf8 consolidar_dias.py --refazer`.

Validação contra o servidor: das 1.942 linhas do resumo diário gerado lá, **1.936 batem**.
As 25 divergências de `odo_fim` são, na maioria, o dia corrente (dumps tirados em momentos
diferentes) e casos em que o `MAX` do dia pega um pico de jitter — a camada usa a
**última leitura cronológica**, que é o correto:

```
23/08 15:53  193526  vel 40
23/08 16:07  193528  vel  0   ← pico de 1 km com o aparelho parado
23/08 23:31  193527  vel  0   ← última leitura real
```

> **Cuidado com o fuso.** `data_posicao` é gravada em **UTC** e o dia da operação é de
> **Brasília**: todo recorte precisa de `- INTERVAL '3 hours'`. E o `dch_data` do ValeCard
> não tem hora nenhuma — o dia é a resolução em que as fontes se encontram.

### Armadilhas da API da 3S encontradas no caminho

Três modos de falha distintos, e só o primeiro é informação:

1. **404 legítimo** — fora da janela de retenção (~35 dias). Pedir 01/07→01/08 numa
   chamada devolve, sem erro, apenas 30 e 31/07.
2. **404 errático** — dentro da janela. 20/08 responde, 10/08 dá 404, 06/08 responde.
3. **Resposta curta sem erro** — a mesma janela que devolveu 1 posição devolveu 2.578 na
   segunda tentativa. É a mesma classe de armadilha do `executeQueries` do Power BI.

O teste barato que pega os três: **costura**. Janelas consecutivas têm que encostar —
se a janela A termina em 871.264 e a B começa em 871.264, fechou; degrau significa dado
faltando.

---

## 11. Arquivos

> Tabela original de 04/09, mantida. Tudo que ela chamava de "não versionado" foi
> **commitado em 09/09** na branch (`66451b4`) — ver a lista completa abaixo.

| arquivo | papel |
|---|---|
| `embarques_auto.py` | o robô — na `main` é o de produção; na branch tem o fechamento reescrito (§18) |
| `rastreamento_worker.py` | worker + `_consolidar_dias()` (commit `7de4860`) |
| `consolidar_dias.py` | consolidação avulsa placa+dia |
| `PLANO-EMBARQUES.md` | o plano da Fase 1, do modelo manual |
| este arquivo | o estudo do modelo autônomo |

**Versionados em 09/09 na branch `modelo-carreta-3s-congelado`** (`66451b4`, tag
`estudo-embarques-2026-09-08`) — ficam fora da `main` de propósito: o `Dockerfile` faz
`COPY . .` e não há `.dockerignore` para `_*.py`, então na `main` entrariam na imagem de
produção.

| arquivo | papel |
|---|---|
| `_robo_atemporal.py` | **o motor convergente** — lê a série inteira e deriva saída→chegada→entrega (§20.6) |
| `_auditoria_geral.py` | **o aferidor** — bateria de invariantes T/S/V/C/F/D (§20.5) |
| `_corrigir_manifesto_origem.py` | reconstrução da chave anti-duplicata (§20.2) |
| `_simular_regras_fechamento.py` | as réguas de fechamento medidas dia a dia (§18.1) |
| `_simular_regra_manifesto.py` | a exceção do reforço no meio da rota (§17.3 nº 8) |
| `_testar_regras_fechamento.py` | 15 testes contra o banco real, em transação com rollback |
| `_auditar_mapas.py` | auditoria em larga escala pelo endpoint do mapa (§14) |
| `_auditar_fechamentos.py` | auditoria de chegada e fechamento (§15) |
| `_auditar_reprocessamento.py` | a régua do reprocessamento de 04/09 (§15.4) |
| `_regerar_vazias_agosto.py` | gerador de perna vazia por evento real (§12.5) |
| `_tracar_rotas_agosto.py` | backfill ORS com backoff e ritmo (§12.12) |
| `_reabrir_fechamento_indevido.py` | desfaz fechamento por `baixa_ctrb`/`timeout` |
| `_auditoria_ANTES.csv` · `_auditoria_DEPOIS.csv` | **os snapshots do placar da §20.7** — 272 cargas com achado × 181. Não se reproduzem: a base mudou depois deles |
| `_auditoria_mapas*.csv` · `_auditoria_fechamentos.csv` · `_auditoria_km_nulo.csv` · `_auditoria_reprocessamento.csv` | as rodadas de 04 e 07/09 |

> ⚠ **O `_testar_regras_fechamento.py` só roda na branch.** As funções que ele testa
> (`_chegou_ao_destino`, `reanalisar_pendentes`) não existem na `main` — com a `main` em
> checkout ele quebra na primeira chamada.

> ⚠ **O benchmark das 202 cargas da §17.2 NÃO existe em arquivo** — a medição vive só na
> prosa desta documentação. Ver §21.9 para o estado do resgate.

---

## 12. Sessão de 04/09/2026 — o KPI do odômetro e o reprocessamento de agosto

Tudo desta seção foi feito no **banco LOCAL**, com dado real de produção carregado por
dump. **Produção não foi tocada.** O que existe de código está no working tree, não
commitado — ver §12.8.

### 12.1 O odômetro real existe, e ninguém estava usando

A 3S devolve `Odometro` tanto no `/ListaUltimaPosicaoVeiculos` quanto no
`/HistoricoPosicao`. O `tres_s_client.py` já normalizava o campo e o worker já gravava em
`embarques_posicoes_atuais` e `embarques_posicoes_historico` — só nunca foi consumido.

Medido em produção (chamada real, 04/09):

```
93 de 93 veículos reportam Odometro   ·   zero nulo, zero zero
faixa 6.309 … 1.752.887   ·   mediana 295.205   →  a unidade é QUILÔMETRO
```

Contra o GPS, em pares de posições com intervalo ≤ 10 min e veículo em movimento
(1.844 pares): razão Δodo/haversine com **p50 = 1,046** e agregado **0,995**. É o esperado
de um contador honesto — o haversine corta curva e subestima 3 a 5%.

**O odômetro é do RASTREADOR, não do veículo.** Prova: 74 carretas reportam odômetro, e
carreta não tem odômetro de painel. Ele é acumulado pelo próprio aparelho, a partir do
GPS. Isso tem consequência direta em §12.3.

> O **horímetro** (`Hourmeter`) veio junto e **não serve**: 19 de 93 preenchidos, cinco
> deles com o valor constante `1042` e outros na casa dos 33 milhões. Unidades misturadas
> entre aparelhos. Descartado.

### 12.2 O KPI "Km rastreador" no mapa da carga

Card novo em `mapa-carga.html`, ao lado de "Km percorridos", para viagem **carregada e
vazia**. Vem de `_kpi_ao_vivo` em `server.py`, campos `km_odometro`,
`odometro_cobertura`, `odometro_trechos` e `odometro_trechos_gps`.

Regras que casos reais obrigaram, cada uma com o caso que a motivou:

| regra | por quê |
|---|---|
| soma de **deltas positivos**, não primeira−última | o contador recua um ponto e volta; a subtração das pontas perdia o trecho inteiro (viagem de 454 km de GPS marcava 314) |
| teto por **velocidade** (110 km/h × horas), não km fixo | 200 km fixos descartavam um salto legítimo através de um furo de 13 h e liberavam um absurdo em 2 min |
| **nulo aparece como `—`**, nunca zero | zero é afirmação; "não sei" é outra coisa |
| na carga normal, respeita o **recorte pré-origem** | sem ele a `C-2026-000443` saltou de 425 para 1.030 km contra 416 do GPS |
| na **viagem vazia**, ignora o recorte | ali a "origem" é onde o veículo já estava parado; o recorte derrubava a `V-2026-000047` de 119 para 11 km |
| na viagem vazia, corta pela **janela real da perna** | o trajeto bruto começa 12 h antes; sem cortar, a `V-2026-000002` marcava 575 km numa perna de 122 |
| segue a placa do **`rastreado_via`**, não a `placa_track` nominal | quando a carreta está muda e o sistema cai no cavalo, a nominal continua sendo a carreta e o odômetro perdia a referência |
| na viagem vazia, **só a carreta mede** | quem faz o reposicionamento é o ativo sem carga; o cavalo pode estar em outra viagem. A `V-2026-000021` marcava 142 km de uma perna de 551, e os 142 eram do cavalo fazendo outra coisa |

### 12.3 O odômetro NÃO atravessa todo furo — a correção mais importante do dia

Durante a maior parte da sessão eu afirmei que *"o odômetro atravessa o buraco de sinal"*.
**Está errado**, e o Gabriel identificou o caso que derruba. São dois furos diferentes:

| tipo de furo | o odômetro | por quê |
|---|---|---|
| **transmissão rala** (aparelho ligado, manda pouco) | **atravessa** ✓ | ele contou, só não mandou |
| **aparelho mudo/desligado** | **não conta** ✗ | não havia quem contasse |

A prova, carreta `HKE0D21`:

```
29/08 10:10–10:45  Catuji/MG     parado    odômetro 375513
30/08 00:04        Manhuaçu/MG   ← salto de 327,6 km, 13 h depois
                                 odômetro AINDA 375513
```

O aparelho ficou 13 h mudo enquanto o caminhão rodava 327 km, e o contador não avançou.
No período inteiro essa placa vai de 373.420 a 376.629 com 669 valores distintos — o
contador funciona, só não registrou aquele trecho.

O caso que me enganou foi o oposto (`TZB1D35`: 22 posições marcando 563 km contra 521 do
haversine) — furo do tipo 1, onde a conclusão vale.

**A regra que saiu disso**, por trecho:

```
Δodo > 0 e plausível ........ o aparelho contou   → usa o odômetro
Δodo = 0 e houve deslocamento  estava mudo        → usa o haversine
Δodo = 0 e sem deslocamento .. parado             → zero (é jitter)
```

**E a validação geométrica que fecha:** a distância rodoviária é sempre ≥ a linha reta
entre origem e destino. Odômetro abaixo da reta **prova** que o aparelho não contou o
trecho. Não depende de calibrar nada.

### 12.4 Reprocessamento de agosto (LOCAL)

Agosto foi refeito no banco local pelo modelo carreta-cêntrico, para inspeção visual:

```
229 cargas com mercadoria + 63 viagens vazias
160 linhas em embarques_cargas_log — toda alteração com rastro
```

Correções aplicadas às cargas:

| motivo gravado | n |
|---|---|
| `gps_carreta` — a carreta chegou e saiu, provado | 132 |
| já estava certa | 65 |
| `transbordo_udi` — parou no armazém e saiu com manifesto novo | 11 |
| `manifesto_novo_carreta` — fallback documental (carga cega) | 9 |
| **reabertas por falta de prova** | 12 |

As 12 reabertas são as que o sistema tinha fechado sem evidência. Voltaram ao status que
tinham antes (lido do `valor_anterior` no log) e agora aparecem como o que são: viagem
que ninguém sabe se terminou.

### 12.5 As viagens vazias — dois geradores, o segundo é o bom

**O primeiro gerador estava quebrado** e vale registrar por quê: derivava a janela de
`data_carregamento`, que é DATE (meia-noite), misturada com `data_conclusao`, que tem
hora. Quando a carga seguinte carregava no mesmo dia, meia-noite < conclusão e um `if`
fabricava uma janela de **1 hora**. Das 93 geradas, **45 (48%) tinham ~1 h** e 30 (32%)
passavam de um dia.

Regra atual (`_regerar_vazias_agosto.py`), só evento real:

```
início = data_conclusao da carga A          (fim de viagem, com hora)
fim    = data_saida_real (ou inicio_viagem) da carga B
descarta se faltar um dos dois, ou se fim <= início
```

Resultado: 93 fabricadas → **63 defensáveis** (39 com km medido, 17 com janela longa e
7 sem GPS — as duas últimas classes **rotuladas, com km nulo**).

Descartes, e o que cada um significa:

| descarte | n | leitura |
|---|---|---|
| cargas sobrepostas (`fim <= início`) | 36 | a carga B partiu antes de a A concluir — **resíduo dos fechamentos errados**, deve cair quando o modelo novo entrar |
| recarregou no mesmo lugar | 30 | não há perna vazia — é o caso ideal |
| carga B sem saída real | 13 | não dá para saber quando a perna terminou |
| menos de 50 km | 12 | manobra, não viagem |

**Trava de plausibilidade:** janela acima de 3× o tempo cabível para a distância
(600 km/dia + 1 dia de folga) → o km **não é atribuído**. O caso que a motivou:
`HMV3G86`, 575 h de janela para 346 km de reta, com 1.419 km de odômetro. Aquilo não é
perna vazia — é lacuna de 24 dias com conteúdo desconhecido dentro (provavelmente viagem
de Carreteiro, que o robô não abre).

### 12.6 Bugs encontrados e corrigidos

| bug | onde | efeito medido |
|---|---|---|
| alerta de rastreio comparava placa por **igualdade exata** | `api_embarques_cargas_list` | 13 de 25 cargas ativas rastreáveis devolviam NULL — e NULL nunca dispara o alerta. Mais da metade ficava sem aviso mesmo com a carreta muda. Corrigido com `_pn()` |
| alerta não cobria `Aberta` | idem | a carga mais urgente (nunca saiu, veículo mudo há 64 dias) era a única sem aviso. Agora `Aberta` entra, e "sem posição nenhuma" também alarma |
| **`km faltando` mostrava a rota inteira** numa carga já chegada | `api_rastreamento_trajeto` | sem polyline o código caía para a distância total; a `C-2026-000629` dizia "faltam 1.028 km" de um caminhão parado no destino. Agora é 0 quando `no_local_desde`/`data_conclusao` existem |
| **origem sem coordenada** | dados | 331 cargas. Sem ela o recorte pré-origem não roda e o trajeto começa antes da origem. Geocodificadas — hoje **zero** sem origem |
| **odômetro gravado em `distancia_planejada_km`** | erro meu no gerador de vazias | 39 vazias mostravam "Rota planejada (ORS): 87 km" numa perna de 400. O campo é da rota; o odômetro vive no KPI. Limpo |

### 12.7 Rastreadores mortos — lista para a manutenção

Nenhuma correção de software resolve. **10 placas reais** sem transmitir há mais de 7 dias
(fora 7 do simulador):

```
HNL0A70    52 dias   ·  9 cargas em ago/set   ← o pior: em uso e cego
HOA0819   248 dias      HKE0320  127 dias
HBG6B81    87 dias      HOA0467   83 dias  · 1 carga
GSV8203    72 dias      HKE0420   65 dias  · 1 carga
HHK5956    57 dias      HKE0530   23 dias
HGA9547    16 dias
```

### 12.8 Estado do código (nada commitado)

Working tree, sobre a tag `modelo-manual-2026-09` (= commit `7de4860`):

| arquivo | mudança |
|---|---|
| `server.py` | odômetro no trajeto · `km_odometro` no `_kpi_ao_vivo` · `_traj_odo()` · `km faltando`=0 na chegada · alerta por `_pn()` e cobrindo `Aberta` |
| `mapa-carga.html` | card "Km rastreador" |
| `.gitignore` | dumps do servidor |
| `_regerar_vazias_agosto.py` | novo — gerador de vazias por evento real |
| `_tracar_rotas_agosto.py` | novo — geocodifica origem e traça rota ORS |
| `_auditar_mapas.py` | novo — auditoria em larga escala |

**O caminho manual segue intacto** — `git diff modelo-manual-2026-09 -- server.py` mostra
só `_kpi_ao_vivo`, `api_rastreamento_trajeto` e `api_embarques_cargas_list`. O
`embarques_auto.py` e os HTML do fluxo manual **não mudaram**.

### 12.9 Pendências

1. ~~129 rotas do ORS nao tracadas~~ — **RESOLVIDO em 04/09**. Nao era cota diaria e sim
   limite por minuto; ver [12.12]. Todas as 324 cargas tem rota.
2. **Subir agosto para produção** — o script SQL ainda não foi escrito. Cuidados: as `V-`
   precisam entrar **sem id explícito** (a sequência da produção já avançou) e os UPDATEs
   por id podem atropelar o que o robô mexeu desde o dump.
3. **A auditoria completa** (`_auditar_mapas.py`) precisa rodar depois de tudo aplicado —
   é ela que diz o que sobrou.

### 12.10 Ferramentas desta sessão

```bash
python -X utf8 consolidar_dias.py --resumo          # o que existe, sem gravar
python -X utf8 _regerar_vazias_agosto.py            # dry-run das vazias
python -X utf8 _tracar_rotas_agosto.py              # geocodifica + traça ORS
python -X utf8 _auditar_mapas.py --csv saida.csv    # auditoria (exige o server local no ar)
```

> Para inspeção visual, subir o servidor local **com o worker desligado**, senão ele
> reprocessa as cargas e desfaz a correção:
> ```
> START_WORKER=false PYTHONIOENCODING=utf-8 python -X utf8 server.py
> ```
> O `-X utf8` não é opcional: o banner do boot tem emoji e o console do Windows quebra.

### 12.11 Armadilhas do ambiente (para não redescobrir)

- **`embarques_posicoes_atuais` não vem no dump do histórico.** Carregar só o histórico
  deixa a posição atual congelada, e **toda carga ativa parece sem GPS**. Repopular com
  `DISTINCT ON (placa) ... ORDER BY placa, data_posicao DESC`.
- **A sequência de `embarques_cargas`** fica atrás depois de carregar dump com id
  explícito. `setval(pg_get_serial_sequence(...), MAX(id))` antes de inserir.
- **`embarques_cargas_log`** usa `usuario_nome`, não `editado_por_nome`.
- **`motorista_cpf` e `cavalo_tipo` são NOT NULL** — dump que não os traga precisa de
  placeholder no INSERT.
- **O `trajeto` da API é um DICT** (`cavalo`/`carreta1`/`carreta2`), não uma lista.
  Contar as chaves dá 3 para toda carga — foi um falso alarme numa auditoria inteira.

---

### 12.12 O 403 do ORS é limite POR MINUTO, não cota diária

Durante a sessão eu diagnosticei errado e propaguei o erro: disse que a cota diária do ORS
tinha estourado, que era preciso esperar até o dia seguinte, e que eu havia **queimado a
cota da produção**. Nada disso era verdade. O Gabriel desconfiou porque **as viagens das
16:30 da produção traçaram normalmente** — se a cota diária tivesse acabado, elas não
teriam.

O log (`embarques_3s_log`, provider `ORS`) fecha a questão:

```
403 → 54 chamadas entre 04/09 13:04:01 e 13:05:55   ·  tudo em 1min54s, e parou sozinho
200 → até 04/09 17:10                                ·  produção 16:30 + teste manual
```

**O free tier do ORS tem dois limites** — ~2.000/dia e ~40/minuto — e **o 403 devolve a
mesma mensagem `"Quota exceeded"` nos dois casos**. O meu script dormia 1,6 s entre
chamadas (~37/min), colado no teto. Foi rajada, não esgotamento.

Correções no `_tracar_rotas_agosto.py`:

| antes | agora |
|---|---|
| `sleep(1.6)` → ~37 req/min | **`sleep(2.6)` → ~23 req/min**, com folga |
| no 403, **abortava a fila inteira** | **espera 70 s / 140 s / 210 s e retenta** |
| ordem por id crescente | **id decrescente** — as recentes são as que se abre |
| filtro `BETWEEN '2026-08-01' AND '2026-08-31'` | `>= '2026-08-01'` — pega setembro |

Havia também um **erro de sintaxe** que eu introduzi numa edição anterior
(`-- vazias primeiro` fora da string) — o script não rodaria nem com a cota liberada.

**Resultado depois do conserto: 129 de 129 rotas traçadas, zero falhas, no mesmo dia e
na mesma chave.** Estado final:

```
cargas com mercadoria ...  261  ·  261 com rota  ·  0 sem
viagens vazias .........    63  ·   63 com rota  ·  0 sem
```

> **Lição para o próximo backfill:** ritmo abaixo de 25 req/min e backoff no 403. E
> `"Quota exceeded"` no ORS **não significa** que a cota diária acabou — conferir o log
> por minuto antes de concluir qualquer coisa.

### 12.13 Últimos ajustes do KPI (o princípio: número sem lastro não se publica)

Três correções finais, todas do mesmo vício:

| ajuste | por quê |
|---|---|
| **zero vira `—`** | se nenhum trecho contribuiu (contador congelado o tempo todo), o número não existe. "0 km" é uma afirmação; `—` é honesto |
| **sanidade geométrica** (`_kpi_sanidade`) | a rodoviária é sempre ≥ a reta origem→destino. Km abaixo disso é medição que não aconteceu. O valor cru fica em `km_odometro_bruto` e o motivo em `km_odometro_motivo`, que a tela mostra no tooltip |
| **rótulo honesto da rota** | o card dizia "Rota planejada (ORS)" mesmo quando o número vinha do CTRB. Agora é `Rota planejada (ORS)` **só com polyline**; sem ela, `Distância do manifesto (rota não traçada)` |

---

## 13. Estado ao fim da sessão de 04/09/2026

### 13.1 O que está PRONTO (só no banco local)

| item | estado |
|---|---|
| camada de consolidação diária | **commitada** (`7de4860`), independente do resto |
| KPI "Km rastreador" com 11 regras | working tree |
| alertas corrigidos (grafia + `Aberta`) | working tree |
| agosto reprocessado: 261 cargas + 63 vazias | banco local |
| 324 rotas do ORS traçadas | banco local |
| 331 origens geocodificadas | banco local |
| documentação (este arquivo) | working tree |

### 13.2 O que NÃO foi feito

**O robô não foi tocado.** O `embarques_auto.py` está exatamente igual ao de produção.
Todo o estudo do modelo carreta-cêntrico (§1 a §11) é **projeto, não implementação**.

Concretamente, continuam em produção:
- `manifesto_novo` casando **qualquer placa contra qualquer papel** → 47,2% de erro
- `sequencia_viagem` → 57,1% de erro
- ~32 fechamentos indevidos por mês

### 13.3 A ordem recomendada para retomar

**1. Subir o código (pode ir sozinho, é aditivo)**

`server.py` · `mapa-carga.html` · `.gitignore` — 251 linhas, 13 removidas, todas nas
regiões pretendidas. O caminho manual está intacto (`git diff modelo-manual-2026-09`
mostra só `_kpi_ao_vivo`, `api_rastreamento_trajeto` e `api_embarques_cargas_list`).

Ganho imediato e independente de qualquer dado: o KPI, o alerta que hoje **não dispara em
mais da metade** das cargas ativas, o alerta cobrindo `Aberta`, o "km faltando" correto e
a sanidade geométrica.

```bash
git add server.py mapa-carga.html .gitignore HANDOFF-EMBARQUES-AUTONOMO.md \
        _regerar_vazias_agosto.py _tracar_rotas_agosto.py _auditar_mapas.py
git commit -m "feat(rastreamento): KPI km do rastreador + correcoes de alerta e rota"
git push origin main
# no servidor: git pull && docker build && docker service update --force
```

Junto, no Portainer: **`EMBARQUES_AUTO_JANELA_DIAS=5`** (voltar ao default — hoje está 1,
e com 1 o manifesto cujo CTRB atrasa nunca é revisitado; ~4% dos casos).

**2. Consertar o robô — ANTES de subir os dados**

Esta é a ordem que importa. Subir agosto corrigido enquanto o robô que o quebrou continua
rodando é limpar com a torneira aberta:

- as **12 cargas reabertas por falta de prova** viram candidatas imediatas do robô, que as
  fecha de novo com data de meia-noite e sem prova
- os ~32 fechamentos errados por mês continuam sendo produzidos

O conserto está desenhado em §4.3: regra de fechamento **por papel da placa** (mesma
carreta encerra; cavalo com outra carreta apenas troca o recurso), eixo na carreta, e a
janela de reanálise (§13.4).

**3. Só então subir os dados de agosto**

Script SQL ainda **não escrito**. Cuidados:
- as `V-` precisam entrar **sem id explícito** — a sequência da produção já avançou
- os `UPDATE ... WHERE id=` podem atropelar o que o robô mexeu desde o dump (04/09 manhã);
  precisam de guarda comparando o estado atual
- as rotas e origens geocodificadas são `UPDATE` por id — mesmo cuidado

### 13.4 As duas janelas do robô atemporal

Hoje existe **uma** janela (`EMBARQUES_AUTO_JANELA_DIAS`) e ela serve só para abrir.
O modelo autônomo precisa de duas, porque são trabalhos com risco diferente:

| janela | para quê | tamanho | risco |
|---|---|---|---|
| **abertura** | criar carga de manifesto novo | **5 dias** | cria registro — janela curta, e o índice único em `manifesto_origem` como rede |
| **reanálise** | revisitar pendente com evidência posterior | **~30 dias** | nenhum: não chama 3S nem ORS, só lê o que já está no banco, e só toca em carga com `criada_por_robo = TRUE`, sempre com log |

O tamanho da reanálise vem da medição: 87% das pendências resolvem, e a evidência que
resolve (manifesto novo da mesma carreta) tem **p50 de 3 dias e p90 de 6**. O teto de
30 dias é onde a evidência de GPS acaba (a retenção) — a documental não expira, e a
camada `embarques_rastreio_dia` sobrevive à purga.

### 13.5 Auditoria — como refazer

`_auditar_mapas.py` varre as 324 cargas batendo no mesmo endpoint do mapa e classifica os
defeitos por tipo. Leva ~10 min e **exige o servidor local no ar**.

> ⚠ Ela só imprime no fim (sem progresso). Para acompanhar, contar as requisições no log
> do servidor: `grep -c trajeto srvN.log`.

Última rodada completa (antes das rotas serem traçadas): 181 de 324 com algum defeito.
Com as 129 rotas no lugar, as classes "sem rota do ORS" (129) e "distância sem polyline"
(64) devem zerar, sobrando essencialmente **realidade do dado, não código**:

```
KM rastreador nulo .............. ~63   aparelho não reporta odômetro
sem trajeto em nenhuma placa ....  13   aparelho mudo na janela
odômetro abaixo da reta .........  ~4   agora barrado pela sanidade geométrica
haversine inflado ...............   4   jitter
Aberta sem posição ..............   1   já sinalizada
```

Essas não têm conserto em software — são **rastreador para trocar** (lista em §12.7).

---

## 14. A auditoria completa — resultado (07/09/2026)

A rodada de 04/09 às 17:22 morreu junto com a sessão (`audit4.log` ficou com 0 byte).
Refeita em 07/09 sobre o **mesmo banco local**, intacto (261 cargas + 63 vazias, 324/324
com polyline e com origem geocodificada), servidor local com `START_WORKER=false` e
`EMBARQUES_AUTO=false`. 324 requisições, todas 200, zero erro.

### 14.1 O placar

```
ANTES (04/09 15:17)   181 de 324 com defeito
DEPOIS (07/09)         98 de 324 com defeito     -83 cargas
```

| classe de defeito | antes | depois | |
|---|---|---|---|
| SEM rota do ORS | 129 | **0** | ✔ como previsto |
| distância planejada SEM polyline | 64 | **0** | ✔ como previsto |
| ODÔMETRO ABAIXO DA RETA | 31 | **0** | migraram para "KM nulo" — a sanidade fez o trabalho |
| KM rastreador NULO | 63 | **95** | +32: é para onde os barrados foram |
| SEM trajeto em nenhuma placa | 13 | 13 | hardware |
| ENTREGUE sem trajeto nenhum | 12 | 12 | hardware |
| trajeto com menos de 5 pontos | 5 | 5 | hardware |
| HAVERSINE inflado | 4 | 3 | ⚠ um sumiu por efeito colateral — §14.3 |
| ABERTA sem posição nenhuma | 1 | 1 | já sinalizada |

Nenhuma carga que estava limpa passou a ter defeito. As 83 que sumiram da lista são as
que só tinham a rota faltando.

> **A previsão da §13.5 errou num ponto e é bom saber por quê:** ela esperava ~63 "KM
> nulo" e ~4 "abaixo da reta". Como a sanidade **anula** o valor, o barrado não fica na
> sua própria classe — ele **vira "KM nulo"**. A classe "abaixo da reta" só existe
> enquanto o número errado é publicado.

### 14.2 O "KM rastreador NULO" esconde três causas diferentes

O rótulo do auditor diz "aparelho não reporta odômetro", mas depois da sanidade
geométrica são três coisas (detalhe em `_auditoria_km_nulo.csv`):

| causa | n | leitura |
|---|---|---|
| **A** — não reporta odômetro nenhum | 67 | hardware, nada a fazer |
| **B** — reportou, a sanidade barrou, viagem **concluída** | 20 | o aparelho ficou mudo em parte do trecho — a sanidade acertou |
| **C** — reportou, a sanidade barrou, mas a carga **ainda está rodando** | **8** | **a regra é que não vale aqui** — §14.4 |

### 14.3 Furo do próprio auditor: o "haversine inflado" depende do odômetro

`V-2026-000025` perdeu o rótulo sem que nada nela mudasse: gps 311 km contra reta de
124 km, igual antes. O teste do auditor exige `odo and gps and reta` — com o odômetro
anulado pela sanidade, ele **não roda**. O jitter continua lá, invisível.

Conserto (no `_auditar_mapas.py`): comparar contra a reta e a rota, que não dependem do
odômetro, e usar `km_odometro_bruto` quando o publicado vier nulo.

### 14.4 Bug encontrado: a sanidade geométrica não vale para carga em trânsito

`_kpi_sanidade` barra todo km abaixo de 85% da reta origem→destino. **A regra "rodoviária
≥ reta" só vale para viagem terminada** — quem ainda está a caminho rodou menos que a
reta por definição. Resultado: 8 cargas em curso ficam com `—` no card, sendo que em
várias o odômetro **concorda com o GPS**, que é a evidência independente:

| carga | status | odo bruto | GPS | reta | |
|---|---|---|---|---|---|
| C-2026-000634 | Em rota | 578 | 587 | 1.053 | odo bate com GPS (−1,5%) |
| C-2026-000595 | Em rota | 630 | 637 | 792 | bate (−1,1%) |
| C-2026-000623 | Em rota | 524 | 491 | 849 | bate (+6,7%) |
| C-2026-000628 | Aberta | 480 | 587 | 642 | plausível |
| C-2026-000586/587 | Em rota | 556 | 700 | 733 | plausível |
| C-2026-000593 | Em rota | 609 | 807 | 793 | plausível |
| C-2026-000603 | Aberta | 58 | 787 | 1.140 | **este sim é medição falsa** (odo = 7% do GPS) |

O piso geométrico é o teste certo para viagem fechada e o teste errado para viagem aberta.
Para a carga em curso o comparador honesto é o **GPS da mesma janela** — duas medidas
independentes do mesmo trecho. Proposta:

```
viagem concluída ....... mantém o piso geométrico (reta origem→destino)
viagem em curso ........ piso é o GPS da janela: odo < 0,5 × haversine → barra
                         (barraria a 603 e publicaria as outras 7)
```

Custo: um `if` em `_kpi_sanidade` mais o `data_conclusao` no dicionário do KPI. **Não foi
aplicado** — é decisão do Gabriel.

### 14.5 O que sobra é hardware, e isso ficou provado

As 13 "sem trajeto em nenhuma placa": **10 usam placa da lista de rastreador morto**
(§12.7). Das outras 3:

```
MWP0H69 (2 cargas)   52 posições em 33 dias — semi-mudo; nada na janela das cargas
TZC0I41 (1 carga)    4.126 posições no mês, mas NENHUMA entre 24/08 e 02/09,
                     que é exatamente a janela da C-2026-000495 — mudo na janela
```

> ⚠ **Correção (07/09, mais tarde).** Aqui dizia que a `MWP0H69` não tinha *nenhuma*
> posição. Errado: eu consultei com igualdade exata em vez de `placas.grafias()`, e ela
> vive no banco na grafia antiga (`MWP0769`). É a mesma armadilha que o §12.6 já tinha
> registrado para o alerta. O achado não muda — o aparelho fala pouquíssimo — mas a
> frase estava mais forte do que o dado.

Ou seja: nenhuma das 13 é defeito de software. Confirma a leitura da §13.5.

### 14.6 Como refazer

```bash
START_WORKER=false EMBARQUES_AUTO=false PYTHONIOENCODING=utf-8 python -X utf8 server.py
python -X utf8 _auditar_mapas.py --csv _auditoria_mapas.csv     # ~12 min, 324 cargas
```

Arquivos desta rodada:

| arquivo | conteúdo |
|---|---|
| `_auditoria_mapas.csv` | 98 linhas — a rodada de 07/09 |
| `_auditoria_mapas_pre_rotas.csv` | 181 linhas — a de 04/09, guardada para comparar |
| `_auditoria_km_nulo.csv` | as 95 do "KM nulo" abertas nas causas A/B/C |

---

## 15. Segunda passada — auditoria de CHEGADA e FECHAMENTO (07/09/2026)

A §14 auditou o que a tela **desenha**. Olhando quatro mapas, o Gabriel levantou o que ela
**significa**: "fechou antes", "a linha para quando entra na cidade", "rastreou pelo cavalo
mas não ficou percurso". Cada pergunta virou um teste, no `_auditar_fechamentos.py` (lê o
banco direto, não precisa do servidor).

### 15.1 Os quatro casos, um a um

| carga | o que parecia | o que é |
|---|---|---|
| `V-2026-000021` | rastreou pelo cavalo e não ficou percurso | a carreta `HNL0A70` é o sensor da perna e está **morta há 52 dias**; o cavalo `TYX9F59` **não fez essa perna** — ficou em Uberlândia/Uberaba nas 32 h da janela e só passou por Embu em 01/09, depois. Os 14,2 km do card são o cavalo manobrando, não a viagem |
| `C-2026-000617` | corte antes do destino? | **não é corte.** A carreta passou por Catalão às 03:43 rodando, parou em Goiandira (14 km do centro) às 04:36 e **calou até 11:05**; a carga fechou às 10:08, dentro do silêncio. Às 11:22 ela está em Catalão parada — a entrega aconteceu **depois** do fechamento |
| `C-2026-000602` | fechou antes | **fechou mesmo.** Conclusão gravada em `02/09 00:00:00` (meia-noite) com a carreta em Guaratinguetá, a 199 km do Rio, **transmitindo 26 min antes**. Ela chegou no máximo a 148 km (Itatiaia) e voltou para SP |
| `C-2026-000603` | fechou antes | **não fechou** — está `Aberta`. A linha para porque o rastreador calou em Vitória da Conquista, a 411 km de Feira de Santana, e **não voltou mais** (5 dias). É a regra de ouro funcionando: silêncio não virou evento |

### 15.2 A varredura das 324

```
324 cargas · 253 sem apontamento · 71 com pelo menos um
```

| apontamento | n | o que é |
|---|---|---|
| MEIA-NOITE (conclusão 00:00:00) | 37 | assinatura de fechamento documental, não de evento |
| PERDEU O RASTREIO EM ROTA | 29 | ≥6 h calado com o veículo ainda longe do destino (mediana **74 h**) |
| FECHOU CONTRA O GPS | 10 | o aparelho estava **ativo** na hora do fechamento e longe do destino |
| SEM EVIDÊNCIA | 7 | fechou com o aparelho calado |
| VAZIA NÃO CONFERE | 6 | o trajeto desenhado cobre <30% da perna |
| FECHOU ANTES | 5 | entrou no raio do destino **só depois** da conclusão |
| VAZIA SEM SENSOR | 4 | a carreta, que é quem mede a perna, não tem posição na janela |

> O silêncio **benigno** (chegou, parou e o aparelho calou) foi retirado de propósito: 95%
> das lacunas são veículo parado (§3.5). Só conta como apontamento quando o veículo ainda
> não tinha chegado.

### 15.3 Das 22 questionáveis, 12 são o raio, 10 são de verdade

Medindo a **maior aproximação real** de cada uma (janela + 7 dias):

```
 9  CHEGARAM — o raio de 20 km no centroide da cidade é apertado demais
    (Rio 25–41 km, Brasília 29,8 km) ou a chegada foi depois da conclusão
    (Teresina 3,5 · Embu 1,4 · Serra 8,9 · Aparecida 0,8 · Uberlândia 1,4)
 3  chegaram à REGIÃO (22–42 km) — mesmo efeito de centroide
10  NÃO CHEGARAM — a carreta nunca esteve perto, nem 7 dias depois
```

**O raio de 20 km medido do centroide é o defeito de método.** Em metrópole ele acusa
falso: a carga entregue no Rio marca "não chegou" e a tela mostra `KM FALTANDO` numa carga
`Entregue`. Proposta: raio por porte da cidade (metrópole 40 km, demais 20 km), ou raio a
partir do **endereço do destinatário**, não do centro.

Das **10 que não chegaram**, quatro terminam em **Uberlândia** com destino em Goiás
(`C-2026-000084`, `473`, `609`, `V-2026-000014`). É a assinatura do **transbordo no
armazém** (§3.4): a viagem *daquela carreta* acabou, mas a *mercadoria* não foi entregue —
e a tela diz "Entregue". Não é bug de código: é a decisão pendente nº 1 da §8.

> **Não deu para fechar pelo CTe.** O dump local só tem `manifestos` até 31/03/2026 e
> `conhecimentos_emitidos` até 10/07 — o cruzamento destino registrado × destino do CTe,
> que separaria transbordo de destino errado, precisa de um dump novo dessas duas tabelas.

### 15.4 O reprocessamento de 04/09 funcionou — e eu quase o acusei do contrário

Medi as 152 conclusões reescritas contra a hora da chegada pelo GPS e o placar deu
"69 pioraram". **A régua estava errada.** Pela §4.3 a conclusão não é a chegada: é a saída
do destino **ou 24 h nele**. Os "pioraram" eram, quase todos, exatamente `chegada + 24 h`.

Pela régua certa:

| onde ficou a conclusão (a partir da chegada) | antes | depois |
|---|---|---|
| **ANTES da chegada** (fechamento indevido) | **32** | **0** |
| na chegada (0–2 h) | 20 | 0 |
| regra das 24 h | 34 | **126** |
| mais tarde | 38 | 10 |
| sem GPS para julgar | 28 | 16 |

**Zerou os 32 fechamentos anteriores à chegada.** É a medição que faltava para provar que o
modelo carreta-cêntrico entrega o que promete — e ela só apareceu depois de trocar a régua.

### 15.5 O que fazer com isto

| achado | conserto | onde |
|---|---|---|
| raio de 20 km no centroide | raio por porte da cidade, ou usar o endereço do destinatário | `RASTREAMENTO_RAIO_CHEGADA_DESTINO` / `_indice_chegada_destino` |
| sanidade geométrica em carga em trânsito | piso pelo GPS da janela, não pela reta (§14.4) | `_kpi_sanidade` |
| vazia desenhando trajeto de placa que não fez a perna | não cair para o cavalo em viagem vazia; sem a carreta, `—` e rótulo | `api_rastreamento_trajeto` |
| 10 cargas `Entregue` sem chegada, 4 delas com cara de transbordo | decisão de negócio (§8 nº 1), não código | — |
| `_auditar_mapas.py` deixa de ver o jitter quando o odômetro é anulado | comparar contra a reta e a rota, e ler `km_odometro_bruto` | `_auditar_mapas.py` |

Ferramentas novas: `_auditar_fechamentos.py` (chegada e fechamento) e
`_auditar_reprocessamento.py` (a régua do 04/09). Saídas em `_auditoria_fechamentos.csv`
e `_auditoria_reprocessamento.csv`.

---

## 16. Treze mapas olhados um a um (07/09/2026)

O Gabriel separou 13 telas e pediu leitura completa de cada uma (KPI + rota + trajeto +
posição + manifesto). Quatro defeitos saíram daqui, e três deles **não apareciam em
nenhuma auditoria** porque só se manifestam quando se olha carga antiga.

### 16.1 O fallback "carreta muda" mede a idade contra HOJE

```python
ult_carreta = _idade_h(traj_principal[-1]['data'])          # utcnow() - último ponto
carreta_muda = ... or (ult_carreta > FRESCOR_H)             # 12 h
```

Numa carga que terminou há 5 dias, a carreta **sempre** parece muda — então o mapa troca
para o cavalo e mostra `carreta sem sinal` mesmo quando a carreta rastreou a viagem
inteira. É o que estava em 5 das 13 telas. O caso que prova: `C-2026-000569`, cuja carreta
`TCE7I47` tem 491 pontos e **chega a 1 km do destino**; a tela desenha o cavalo, que
terminou em Cordeirópolis, 656 km longe.

O teste certo é a idade **contra o fim da janela da carga**, não contra agora.

### 16.2 Carga aberta não tem teto de janela

Carga sem `data_conclusao` busca posição até **agora**. Se a placa rastreada é o cavalo,
o KPI vai somando as viagens seguintes dele:

| carga | rota planejada | KM rastreador exibido | pontos desenhados |
|---|---|---|---|
| `C-2026-000601` | **131 km** | **1.659 km** | 3.535 |
| `C-2026-000588` | **255 km** | **1.909 km** | 3.781 |

Ambas já tinham `no_local_desde` gravado (chegaram) — bastava fechar a janela na chegada.
Essas duas estão assim **no banco local** porque o reprocessamento as reabriu por falta de
prova (são 2 das 8 reabertas); em produção estão `Entregue`. Mas o defeito é geral: vale
para toda carga que fica aberta, como as duas `Aberta` de verdade abaixo.

### 16.3 `manifesto_novo` fechando viagem que continuava — o caso limpo

`C-2026-000550` (Ananindeua/PA → Embu das Artes/SP, 2.895 km) foi fechada em
**31/08 00:00** por `manifesto_novo`. O manifesto que a fechou é o `UDI029030-1`, emitido
em **Uberlândia** — no meio do caminho — e que virou a `C-2026-000605`… **com destino Embu
das Artes, o mesmo**. O caminhão pegou carga em Uberlândia e seguiu para o mesmo lugar.

```
31/08 00:00  fechada por manifesto novo (caminhão em Uberlândia, 551 km do destino)
01/09 06:06  o cavalo entra em Embu das Artes  ← a viagem tinha 30 h pela frente
```

É a §3.2 com nome e sobrenome: mesmo par de placas, mesmo destino, segundo manifesto no
meio da rota, e o robô leu como "viagem acabou".

**Efeito em cascata:** a `V-2026-000021` (perna vazia Embu → Uberlândia) foi gerada a
partir dessa conclusão errada, então a janela dela (30–31/08) é **anterior à chegada real**
em Embu (01/09). Por isso o cavalo aparece "em Uberlândia" na perna: ele ainda nem tinha
chegado ao ponto de origem dela. Fechamento errado **fabrica perna vazia errada** — a
§12.5 suspeitava, aqui está o par completo.

### 16.4 `baixa_ctrb` ainda fechou três cargas em 02/09

`C-2026-000607`, `C-2026-000609` e `C-2026-000614` foram fechadas no mesmo ciclo
(`02/09 19:42:34`, milissegundos de diferença) com `encerrada_motivo = baixa_ctrb` — a
regra removida em **03/09**, um dia depois. As três estavam longe do destino, e duas
chegaram no dia seguinte:

```
C-2026-000607  fechada em Contagem/MG (389 km de Serra/ES)   → chegou 03/09 06:03
C-2026-000614  fechada em Uberlândia (252 km de Ap. Goiânia) → chegou 03/09 01:30
C-2026-000609  fechada em Uberlândia (234 km de Hidrolândia) → NÃO chegou até 04/09
```

Nada a consertar: a regra já saiu. Serve como medida do estrago dela no último dia de vida.

### 16.5 As duas `Aberta` — e por que estão certas

| carga | o que o rastreador fez | por que fica `Aberta` |
|---|---|---|
| `C-2026-000615` | 6 pontos, **21 minutos**, todos em Luziânia/GO — 292 km depois da origem | a saída exige a placa **vista na origem**; ela nunca esteve lá. `KM percorridos 0,0` porque os 6 pontos são o mesmo lugar |
| `C-2026-000603` | rajadas curtas (9 a 35 pontos/dia); nada em Ibiá; some em 02/09 | idem — e a guarda está certa: abrir sem prova gravaria saída com carimbo velho |

O que está errado nas duas não é o status, é o **rótulo**: `Aberta` é o mesmo balde de
"nem saiu" e "sumiu no meio".

### 16.6 As vazias sem sensor

| perna | o que a tela mostra | o que houve |
|---|---|---|
| `V-2026-000020` (Teresina→Ananindeua) | 1.556 km numa perna de 898 | trajeto do cavalo **incluindo as 12 h anteriores** à janela — a perna em si está certa (chegou a 1 km) |
| `V-2026-000014` (Osasco→Nerópolis) | 36,6 km numa perna de 949 | a carreta calou em Osasco e o cavalo estava em Uberlândia. **A perna é real**: o próximo manifesto da mesma carreta (`C-2026-000571`) sai de Nerópolis em 29/08 — o documento prova o que o GPS não viu |

### 16.7 O que isto acrescenta à lista de consertos

| # | conserto | onde |
|---|---|---|
| 6 | idade da carreta medida contra o **fim da janela**, não contra `utcnow()` | `api_rastreamento_trajeto` |
| 7 | carga aberta: fechar a janela em `no_local_desde` quando existir | idem |
| 8 | `manifesto_novo` não deve fechar quando o novo manifesto tem **o mesmo destino** e é emitido **em rota** | `embarques_auto.fechar_pendentes` |
| 9 | vazia: cortar o trajeto pela janela real da perna também no `distancia_km` (hoje só o odômetro corta) | `_kpi_ao_vivo` |

(1 a 5 estão na §15.5.)

---

## 17. Consertos aplicados e medidos (07/09/2026)

Nove consertos estavam na fila (§15.5 e §16.7). **Um foi derrubado pelo próprio teste**, sete
foram aplicados e dois novos apareceram durante a medição. Método: fotografar as 324 cargas
pelo endpoint do mapa antes, aplicar, fotografar de novo e comparar carga a carga
(`_snap_antes.csv` × `_snap_final.csv`).

### 17.1 O placar do que o usuário vê

O melhor indicador de saúde é quantas telas mostram **km maior que a rota planejada** —
número que ninguém consegue defender:

```
km exibido acima de 1,5x a rota ....  22  ->  3
km exibido acima de 2,0x a rota ....   9  ->  0
km exibido acima de 3,0x a rota ....   4  ->  0
```

Os 3 que sobram têm **chegada provada por GPS** — ali rodar mais que a rota é desvio, que é
informação, não defeito. Os piores casos antes: `C-2026-000601` com 1.619 km numa rota de
131 (12,3x) e `C-2026-000588` com 1.865 numa rota de 255 (7,3x).

Outras medidas da mesma foto:

| | |
|---|---|
| cargas que trocaram a placa rastreada (cavalo → carreta) | **81** |
| KM do rastreador que voltou a ser publicado | 16 |
| KM do rastreador barrado com motivo explícito | 20 |
| km do GPS que caiu mais de 20% | 22 |

### 17.2 O conserto que o teste derrubou

**Raio de chegada por porte de cidade (nº 1) — NÃO aplicado.** A ideia era subir de 20 km
para cobrir metrópole (o Rio acusa "não chegou" a 38 km do centroide). Duas medições contra:

- a mancha por cidade não sai do dado: o p90 das posições rotuladas dá **Uberaba 60 km** e
  **Ibiá 30 km** — cidade pequena — porque o nome que a 3S manda erra (p99 de 72 km);
- subir para 30 km, medido nas 202 cargas com parada de entrega identificável, **ganha 3 e
  estraga 3**: antecipa a chegada em 3 a 6 h em cargas que pararam na periferia antes de
  entregar.

A cobertura por raio é 93,1% (20 km), 95,0% (30 km), 95,5% (40 km) — o joelho é raso e o
custo é real. **Fica como está.**

> ⚠ **PENDENTE:** o `_auditar_fechamentos.py` continua com o raio de 20 km, então ele ainda
> classifica como "não chegou" 12 cargas que chegaram (§15.3 — Rio a 25–41 km do centroide,
> Brasília a 29,8). A tolerância de metrópole é do AUDITOR, não do produto, e não foi feita.

### 17.3 O que foi aplicado

| # | conserto | evidência |
|---|---|---|
| 2 | sanidade geométrica só em viagem **fechada**; na aberta o piso é o GPS da janela | `C-634` publica 578 km (GPS 587); `C-603` segue barrada, agora por discordar do GPS (58 × 787) |
| 3 | vazia só empresta o cavalo se ele **fez a perna** (encosta nas duas pontas) | 63 vazias: 59 com carreta viva, 1 aprovada, 3 reprovadas |
| 5 | auditor enxerga jitter sem depender do odômetro publicado | classe "haversine inflado" saiu de 3 para 0 sem esconder nada |
| 6 | idade da carreta medida contra o **fim da janela**, não contra agora | 81 cargas voltaram a ser rastreadas pela carreta; `C-569` desenha a carreta que chega a 1 km do destino |
| 7 | janela fecha na **chegada** mesmo com a carga aberta | `C-601` 1.619 → 138 km · `C-588` 1.865 → 246 km |
| 8 | `manifesto_novo` não fecha **reforço no meio da rota** | 7 de 179 fechamentos suprimidos, **7/7** com chegada posterior provada; 7 testes unitários |
| 9 | vazia recortada pela **janela real da perna**, do trajeto bruto | `V-2026-000020` 1.556 → sem km inventado |

E três que nasceram do próprio teste:

| conserto | por quê |
|---|---|
| `0,0 km` vira `—` quando não há posição | zero é afirmação; `—` é o que se sabe |
| "Posição atual" desacoplada da janela | com o nº 7 o card congelaria na chegada; agora a linha conta a viagem e o card diz onde o veículo está |
| trava de **janela implausível** (3x o tempo cabível) e de **km sem prova de chegada** (2x a rota) | 24 + 1 cargas passam a mostrar `—` com o motivo no tooltip |

### 17.4 Duas regressões que a medição pegou — e que não eram do conserto

**`C-2026-000468` sumiu do mapa.** O nº 6 passou a seguir a carreta, e a carreta registrada
**ficou parada em Uberlândia (o destino) os dois dias inteiros** — 153 pontos no mesmo ponto,
293 km da origem — enquanto o cavalo rodava os 427 km. Meu primeiro critério ("poucos
pontos") media no lugar errado: quem reduzia a linha a 1 ponto era o corte de chegada, depois
do teste. Critério final: **carreta parada (<5 km) enquanto o cavalo rodou (≥50 km)** — pega
2 cargas em agosto/setembro.

**Seis vazias perderam o trajeto.** O nº 9 recortava uma lista já podada. Reposicionado para
recortar o trajeto **bruto** (a mesma fonte que o odômetro usa), e aí veio o achado: das 63
pernas, medidas **dentro da janela que elas mesmas declaram**,

```
46  perna coerente
 9  a carreta JÁ ESTAVA no destino no primeiro ponto
 4  não saiu do lugar
 4  sem posição nenhuma
```

A `V-2026-000009` diz "Jundiaí → Monte Mor" e o veículo já estava parado em Monte Mor, 54 km
da suposta origem. Os 129 km que ela exibia vinham das **12 h anteriores à perna**. O número
sumir é o conserto funcionando — a perna é que não existe, pelo efeito cascata da §16.3.

### 17.5 A auditoria de mapas depois de tudo

```
181 (antes das rotas) -> 98 (antes dos consertos) -> 101 (depois)
```

Subir 3 não é piora: são as vazias incoerentes que **deixaram de desenhar trilha que não era
a perna**. As classes que sobram são as mesmas de sempre — 99 "KM rastreador nulo" (agora com
motivo explícito em 20 delas), 16 sem trajeto em nenhuma placa, 1 `Aberta` sem posição.

O auditor também foi alinhado à régua nova: ele acusava 6 cargas em rota por "odômetro abaixo
da reta" que o produto passou a publicar de propósito. Medir com régua diferente da do produto
é fabricar defeito.

### 17.6 Estado do código

```
server.py · mapa-carga.html · embarques_auto.py     473 inserções, 33 remoções
```

O caminho manual segue intacto — `_validar_carga_payload`, `_buscar_conflitos`, o
`POST /api/embarques/cargas` e o formulário **não têm uma linha alterada**. No robô o diff
toca só o fechamento (`encerrar`, `fechar_pendentes` e a chamada em `executar`); a abertura,
que a §3.6 mediu como correta, não foi tocada.

**Nada foi commitado nem subiu.** A ordem da §13.3 continua valendo: código primeiro (agora
com o conserto do robô junto, que era o passo 2), dados de agosto por último.

---

## 18. Itens 1, 2 e 3 do robô — implementados e congelados (07/09/2026)

### 18.1 O que foi medido antes de escrever

`_simular_regras_fechamento.py` roda o fechamento **dia a dia**, como o robô: primeiro os
manifestos do dia, depois o dedup. Duas réguas, porque medem coisas diferentes — o veredito
pelo GPS (chegou antes do fechamento?) e a distância no instante do fechamento (a régua da
§3.1). Universo: 276 cargas do robô em ago + set.

```
REGRA                           fecha  acerto   ERRO  s/prova     >100km
hoje                              233     150     25       58  120 (52%)
proposta (eixo na carreta)        209     141     19       49  104 (50%)
proposta + dedup só com prova     206     151      9       46   95 (46%)
```

**Erros de 25 para 9 (−64%), sem perder acerto** (150 → 151).

> **A tolerância importa e quase me enganou.** O manifesto não tem hora, então a emissão
> vira 00:00. Sem tolerância, "fechou 00:00 e chegou 08:00 do mesmo dia" conta como erro e o
> placar dá 15% × 12%. Com 12 h de tolerância vira 7% × 4%; com 24 h, 3% × 3%. O número só
> significa alguma coisa depois de escolher a régua — aqui, 12 h.

### 18.2 O item 2 não era o que parecia

A hipótese era "o `dedup` erra porque olha o cavalo". Errado: trocar a dimensão não muda
nada (19 → 20 fechamentos, os mesmos 10 erros). O defeito é fechar **por ORDEM** — dos 19
fechamentos que ele produzia, **os 19** eram a mais de 100 km do destino.

O que conserta é exigir prova: só encerra a carga anterior se o GPS mostrar que a carreta
esteve no destino dela. Aí o `dedup` vai a **10 fechamentos, zero erro**.

### 18.3 O item 3 — a janela de reanálise

Sobre as 116 pendências que as regras novas deixam abertas (70 nunca fechadas + 46 fechadas
sem prova):

```
resolvem em até 30 dias ....  71 (61%)   ← 25 pelo GPS, 46 pelo manifesto da mesma carreta
sobram para o humano .......  45
atraso da evidência ........  p50 2,1 dias · p90 5,0 dias
```

O p50/p90 confirma a §5 (3 e 6 dias). Os 61% ficam abaixo dos 87% previstos porque as cargas
do fim da amostra não têm 30 dias de futuro no banco.

### 18.4 O que foi escrito

| onde | o quê |
|---|---|
| `fechar_pendentes` | o manifesto novo da **carreta** encerra a viagem anterior dela; sem carreta no manifesto (truck/toco) cai no cavalo |
| `dedup_veiculo` | só encerra com `_chegou_ao_destino` — bounding-box no banco, sem varrer histórico |
| `reanalisar_pendentes` | novo: revisita pendência de até 30 d e fecha a que o GPS respondeu (saiu do destino, ou 24 h nele). Só lê o banco |
| `_testar_regras_fechamento.py` | 15 testes contra o banco real, em transação com rollback |

A armadilha das duas grafias **não se aplica aqui**: `_placa()` normaliza para Mercosul,
`_veiculo()` grava assim, e o fechamento só toca em carga do robô. Conferido.

---

## 19. CONGELAMENTO — a 3S cortou o acesso (07/09/2026)

### 19.1 O que aconteceu

Ao puxar setembro para ampliar a amostra, o backfill falhou em **93 de 93 placas**. Não era
o 404 errático da §10: é sistemático e atinge tudo, inclusive `/ListaVeiculos`
(`3S.1001 - Veículos não encontrados`). O login funciona — quem recusa é o endpoint de dados,
com código de negócio da própria 3S.

Causa, informada pelo Gabriel: **desacordo comercial** da Rizza com a 3S.

Estado da produção no momento da checagem (07/09, 22:55 UTC):

```
94 veículos no painel · posição mais fresca: 9,0 h · mediana: 13,6 h
até 1h: 0    1 a 6h: 0    6 a 12h: 39    12 a 24h: 37
```

Última posição boa da frota: **07/09 13:55 UTC (10:55 BRT)**. A primeira chamada minha foi
às 22:02 UTC — **oito horas depois**. O backfill não causou nem agravou nada.

### 19.2 Por que congelar, e não seguir

Todas as regras novas dependem de prova por GPS. Sem feed elas **não fecham nada** — falham
no lado seguro, mas param de trabalhar. Enquanto o rastreamento não voltar, o robô tem de
seguir com as regras antigas, que decidem por documento.

> Um registro que vale: se a queda tivesse pego o robô antigo, ele continuaria fechando por
> ordem e por manifesto, produzindo "Entregue" sem caminhão nenhum ter chegado. Não é a
> validação que eu queria, mas é validação.

### 19.3 Como está isolado — o código saiu da `main`

Decisão do Gabriel em 07/09: **não subir nada**, porque se o comercial com a 3S não voltar
isso vira código morto em produção. Então o pacote saiu do caminho do deploy:

```
branch:  modelo-carreta-3s-congelado        commit 49d1b44
arquivos: embarques_auto.py · server.py · mapa-carga.html · README.md
main:    voltou ao estado de produção nesses quatro arquivos
```

Conferido depois do checkout: na `main` o `embarques_auto.py` não tem **nenhuma** referência
ao modelo novo; na branch tem. Não há como o pacote subir por acidente junto de outro
trabalho, porque ele simplesmente não está na `main`.

**O estudo e as ferramentas continuam na pasta** — este handoff, os simuladores, os
auditores e os CSVs não são versionados, então sobrevivem à troca de branch e seguem à mão
para consulta.

Dentro da branch ainda vale a segunda rede: chave **`EMBARQUES_MODELO_CARRETA`, default
`false`**. Desligada, o robô é byte a byte o de produção — o bloco 0 do
`_testar_regras_fechamento.py` prova isso (com a chave desligada o defeito antigo volta a
acontecer, que é o esperado, e a reanálise não faz nada). Ou seja: mesmo que a branch seja
mergeada um dia, o comportamento só muda quando alguém ligar a chave.

Como voltar ao trabalho:

```bash
git checkout modelo-carreta-3s-congelado     # o código volta para a pasta
python -X utf8 _testar_regras_fechamento.py  # 15 testes
```

### 19.4 Para religar

1. conferir que o feed voltou (idade da posição mais fresca da frota);
2. refazer `_simular_regras_fechamento.py` com dado novo — os números da §18 são de uma
   amostra sem setembro completo;
3. rodar `_testar_regras_fechamento.py` (15 testes);
4. `EMBARQUES_MODELO_CARRETA=true` no Portainer.

### 19.5 O que ficou pela metade por causa do corte

- **Setembro só entrou pela metade**: as 46 cargas de 03 a 06/09 foram trazidas da produção
  (leitura da API, id novo, destino geocodificado pelo IBGE) e a base local tem 370 cargas —
  mas as **posições** de 05 a 07/09 não vieram. As medições da §18 valem para agosto cheio e
  setembro parcial.
- A auditoria de fechamento (`_auditar_fechamentos.py`) continua com raio de 20 km, sem a
  tolerância de metrópole (§17.2).

---

## 20. A 3S voltou e o robô virou convergente de verdade (08/09/2026)

> **Estado**: a 3S restabeleceu o acesso. Agosto e setembro foram reprocessados **inteiramente
> pelo robô** na base local, sem intervenção humana. O motor está em `_robo_atemporal.py` e o
> aferidor em `_auditoria_geral.py` — os dois **fora da `main`**, como o resto do pacote (§19.3).

### 20.1 O feed voltou — e o que ficou pelo caminho

`/ListaVeiculos` responde 93 veículos (era `3S.1001`), 38 placas com posição de menos de 1 h
contra 0 no dia do corte. O backfill de 04 a 08/09 trouxe **65.077 posições**; a costura fecha
(volume 13k–19k/dia, 75–81 placas, sem degrau).

**5 carretas novas ficaram mudas entre 01 e 03/09** — `MWP0769`, `HMV3D38`, `QXA9775`,
`HFB9703`, `HMV3714`. A 3S reporta as mesmas datas que a nossa base, então **não é entrega
parcial dela: os aparelhos calaram**. As cinco são carreta, nenhuma é cavalo, e todas estavam
em uso (3 a 6 cargas desde 01/08). Somadas às 10 da §12.7, a cegueira sobe. É manutenção.

### 20.2 `manifesto_origem`: 46, não 109 — e viagem vazia sem chave está certo

As cargas trazidas de produção vieram sem `manifesto_origem` (a API `/api/embarques/cargas`
não devolve o campo), e sem ela o índice único parcial **não vê nada**: rodar o robô recriaria
as cargas em duplicata. Eram 109 linhas sem chave, mas **63 são viagens vazias, que por
definição não têm manifesto** — NULL ali é correto. O defeito real eram **46**.

Reconstruídas 46/46 (`_corrigir_manifesto_origem.py`): 45 por cavalo+dia, 1 pela cadeia da
Auditoria. O desempate da cadeia **é pelo TOMADOR, não pelo destino** — os dois manifestos
ambíguos tinham CTRC dizendo "Aparecida de Goiânia" enquanto as cargas iam para Extrema/MG e
Aparecida/GO. A armadilha dos 69% reaparece aqui.

> Armadilha do conserto: sem filtrar `viagem_vazia`, 7 pernas vazias casavam por acaso com
> manifesto de outro veículo. O filtro não é opcional.

### 20.3 A descoberta que reorganiza o rastreamento: a carreta é um sensor pobre

|  | placas | pontos/dia | intervalo mediano | maior buraco |
|---|---|---|---|---|
| **carreta** | 63 | 59 | 5,0 min | **12,0 h** |
| **cavalo** | 16 | 522 | 2,0 min | 0,9 h |

A distribuição por hora do dia é **plana** nos dois, então o buraco não é horário fixo da
frota: é por veículo, atrelado ao estado dele (o rastreador da carreta dorme parada).

E o número colide com o código: `rastreamento_worker.FRESCOR_H = 12`. **O limite de frescor é
exatamente o buraco mediano da carreta.**

> A §2 diz "a carreta é o sensor" e continua verdade para *identidade* — é ela que fica amarrada
> à carga. Mas para *medição* o cavalo é 9x melhor quando existe. São papéis diferentes, e o
> desenho atual usa um para os dois.

### 20.4 A causa raiz: o worker amostra ao vivo, ninguém relê o histórico

O worker pergunta "onde o caminhão está AGORA?" a cada 60 s e só marca chegada se, naquele
instante, a posição estiver fresca, parada e dentro do raio. A carreta chega, transmite alguns
pontos, adormece, a posição envelhece além de 12 h — e a chegada nunca é marcada.

**144 cargas tinham a chegada no banco e não registrada.** Testei a hipótese de o aparelho
esconder a prova (dormir ao parar) e **o teste a matou**: em 73 das 75 há parada observada
dentro do raio. A prova estava lá; ninguém volta para lê-la.

Somado a isso, a **cascata**: o worker só procura chegada com status `Em rota`, e o status só
avança se a saída foi vista.

    com saída gravada  ->  chegada gravada em 73%
    sem saída gravada  ->  chegada gravada em  2%   (1 de 63)

Uma transição perdida mata as seguintes. Não se recupera um estado por vez.

### 20.5 `_auditoria_geral.py` — parar de achar problema abrindo carga a carga

A auditoria anterior fazia UMA pergunta ("chegou antes de fechar?") e por isso dizia "176 OK"
enquanto a inspeção visual achava defeito em quase toda carga aberta. O aferidor novo aplica
uma bateria de invariantes por carga:

| grupo | o que testa |
|---|---|
| `T1`–`T5` | coerência temporal e **velocidade implícita** |
| `S1`–`S3` | existe sensor? ele fala? é o melhor disponível? |
| `V1` | a placa rastreada esteve mesmo na origem? |
| `C1`–`C5` | saída/chegada registradas quando o GPS mostra |
| `F1`–`F6` | fechou com prova? no instante certo? deixou de fechar? |
| `D1`–`D3` | rota, manifesto, coordenada de destino |

**Validado contra inspeção humana**: dos 14 casos diagnosticados a olho pelo Gabriel, o auditor
reproduziu **13 sozinho**; no 14º ele calou, e calar era o certo (não havia defeito ali).

### 20.6 `_robo_atemporal.py` — e os cinco bugs que só a tela achou

O motor lê a série inteira e deriva saída -> chegada -> entrega num passe só. Não reabre carga:
onde o fato está certo e o instante errado, corrige o instante.

Cinco bugs foram encontrados **rodando o robô várias vezes e olhando o mapa**, nenhum por
leitura de código:

| bug | sintoma | correção |
|---|---|---|
| chegada antes da saída (18 cargas) | impossível; em viagem vazia era quase garantido, porque a perna vazia termina onde o veículo já estava | **piso**: a chegada só conta depois da saída |
| conclusão antes da chegada/saída (13) | **"KM percorridos 0.0"** com o caminhão do outro lado do país — a tela monta o trajeto até `data_conclusao` e a janela fecha vazia | coerência `saída <= chegada <= conclusão` |
| piso ancorado na menor distância da origem | numa viagem A->B->A esse mínimo é a **volta**, e a chegada em B era descartada | primeiro bloco, não mínimo global — a mesma armadilha que `geocoding.indice_saida_origem` já documenta |
| duas fontes de verdade | o robô derivava a conclusão da SUA chegada e respeitava a do BANCO: gravava e descartava para sempre | com prova própria, a dele manda — **sem tolerância** (folga de 1 h deixava 4 cargas oscilando) |
| saída = primeiro ponto FORA do raio | com buraco de 12 h esse ponto está a centenas de km: 94 km em 20 min = 282 km/h | saída = **último ponto dentro do pátio** |

E uma **guarda de velocidade**: nenhum par saída/chegada pode implicar mais de 100 km/h médios.
Se implicar, tenta a próxima chegada candidata; se nenhuma servir, não afirma chegada nenhuma.

> **Convergência é o teste.** Uma passada só esconde tudo isso. O robô é rodado em sequência até
> o número de alterações chegar a **zero** — hoje: passada 1 = 12 cargas, passada 2 = 1,
> passada 3 = 0. Oscilação (o mesmo número toda passada) é bug, não convergência, e foi assim
> que 4 dos 5 bugs apareceram.

> **As duas réguas têm de ser a mesma.** Enquanto auditor e motor usavam piso, teto ou janela de
> posições diferentes, um gravava o que o outro não achava e o placar oscilava (F1 foi
> 13 -> 32 -> 17 -> 38 conforme eu alinhava). Só vale comparar depois de igualá-los.

### 20.7 O placar

| | antes | depois |
|---|---|---|
| cargas com achado | 272 de 370 (74%) | **181 (49%)** |
| chegada não gravada (`C3`) | 144 | **0** |
| saída não gravada (`C1`) | 31 | **0** |
| incoerência temporal (`T1`–`T4`) | 31 | **0** |
| velocidade impossível (`T5`) | 17 | **1** |
| chegou e não fechou (`F4`) | 26 | **1** |

**Metade dos defeitos da base se conserta sem nenhum dado novo** — 235 achados em 175 cargas.
A prova já estava gravada.

### 20.8 O que sobra, e por que não é regra

**38 fechadas sem prova de chegada**, e o recorte por motivo mostra que não é uma coisa só:

    gps_carreta          12   o worker afirmou ver a chegada; relendo o histórico, não achamos
    transbordo_udi        8   precisa do status próprio
    manifesto_novo        7   a regra fechando sem prova
    reposicionamento      5   todas viagens vazias
    gps_saiu_do_destino   3
    sem_prova_revisar     2
    sequencia_viagem      1

Os **12 do `gps_carreta`** são o próximo assunto: um dos dois lados está errado e ainda não se
sabe qual — o worker fechando por posição velha, ou a régua nova rejeitando chegada legítima.

O resto é fora do alcance de software: **139 achados de hardware** (11 cegas, 71 buracos de
sinal, 68 onde o cavalo mede melhor que a carreta), **22 de documento errado** (a carreta do
manifesto não é a que rodou) e 2 de saída digitada à mão.

### 20.9 Transbordo: o critério certo

Critério do Gabriel, validado contra a base: **transbordo é quando a MESMA CARRETA sai com carga
nova**. Se só o cavalo sai, a carreta segue carregada — isso é **desengate**, e o status já
existe.

Mas o critério precisa da negativa de chegada junto: medindo os 7 `transbordo_udi` de agosto,
**3 na verdade entregaram** (pararam 23, 23 e 31 h na região do destino) e só depois seguiram
para o manifesto seguinte. Sair com carga nova não prova que não entregou.

### 20.10 A pista sobre produção

A `C-2026-000609` está fechada por **`baixa_ctrb`** e não tem log local — veio fechada de
produção. A regra foi **removida do código em 03/09**. Ou a carga é anterior, ou **produção está
rodando uma imagem antiga** (já aconteceu: o Portainer devolve o serviço à imagem anterior
quando se edita env pela stack). Três cargas de 06/09 fecharam no mesmo segundo, sem manifesto
novo e sem prova de GPS, o que nenhuma regra do código atual explica. **Não verificado** — exige
tocar produção.

### 20.11 Ferramentas desta sessão

    python -X utf8 _auditoria_geral.py                    # bateria de invariantes, não grava
    python -X utf8 _robo_atemporal.py --tudo              # dry-run
    python -X utf8 _robo_atemporal.py --tudo --aplicar    # grava + log por campo
    python -X utf8 _corrigir_manifesto_origem.py          # dry-run da reconstrução da chave

> Para inspecionar na tela, subir o servidor **sem worker e sem robô** — senão eles reprocessam
> e desfazem a correção. Importar `server` e chamar `app.run` noutra porta faz isso: o bloco
> `if __name__ == '__main__'`, que sobe as três threads (worker, robô e sync do PGR), não roda
> no import.

---

## 21. Nove rodadas de adjudicação (09/09/2026)

> **A linha que abre esta seção, porque é a lição que ela custou três vezes:
> confundir o que o INSTRUMENTO fez com o que o MUNDO fez.**

O que está aqui não é sessão de implementação: é uma **adjudicação**. Nove rodadas de
crítica e contra-crítica sobre o modelo autônomo, cada desacordo levado ao banco em vez de
ao argumento. **Nenhum arquivo foi alterado** — só leitura de código, `git show` na branch
congelada e consulta à base local convergida.

Ela achou **quatro defeitos que nenhuma auditoria anterior tinha visto**, derrubou **dez
conclusões** que já circulavam como assentadas (várias nascidas dentro da própria
adjudicação), e fechou com uma descoberta que reordena a fila: o instante de chegada da
base inteira está enviesado por construção.

O critério de parada foi o mesmo do robô: **convergência**. Os deltas por rodada caíram
monotonicamente — a rodada 6 derrubou duas conclusões maiores, a 7 produziu uma retração,
a 8 lapidou uma frase e achou um custo, a 9 fechou uma pendência. Queda monotônica, não
oscilação, que é a distinção da §20.6.

### 21.1 O defeito do anel — a descoberta que reordena a fila

A §4.3 especifica o evento de chegada assim:

> | carreta entra no destino **e para** | GPS **≤ 25 km** | `No destino` |

O código faz outra coisa:

```python
def perto_com_parada(dd, raio_estrito, raio_largo):
    p = next((d for d, k in dd if k <= raio_estrito), None)
    if p:
        return p, 'raio'          # primeiro ponto no raio. SEM exigir parada.
```

`RAIO_CHEGADA = 20.0`, e a exigência de parada existe **só no raio largo**. São **duas**
divergências de uma decisão escrita, nenhuma guardada por teste — o mesmo gênero do 60 km
reintroduzido sem reauditoria.

O efeito, medido sobre as 292 cargas de agosto com destino geocodificado:

```
DISTANCIA ao destino no instante marcado como chegada   (raio estrito = 20 km)
12,5-15,0 km    21 cargas  -  21 EM MOVIMENTO
15,0-17,5 km    79 cargas  -  78 EM MOVIMENTO
17,5-20,0 km   140 cargas  - 133 EM MOVIMENTO
 5,0- 7,5 km    11 cargas  -   0 em movimento   <- abaixo de 12,5 km o padrao inverte
```

**240 de 292 chegadas foram marcadas com o veículo rodando**, no ponto em que ele cruza a
circunferência de 20 km. `no_local_desde` não significa "chegou ao cliente" — significa
"entrou no anel".

O viés disso é medível diretamente (do 1º ping no anel até a 1ª parada sustentada dentro
dele, 230 cargas):

```
p50 = 0,42 h   p75 = 0,50 h   p90 = 0,65 h   p99 = 1,00 h   ·   99% abaixo de 1 hora
```

Pequeno, mas **sistemático e herdado**: a conclusão pela regra das 24 h herda; o início de
cada perna vazia (= conclusão da carga A) herda; e o placar da §20.7 fica com asterisco —
as 144 chegadas foram gravadas, a maioria no instante errado por construção.

> **Consequência de ordem:** consertar isto muda a régua, e mudar a régua exige
> **reconvergir a base inteira** antes que qualquer medição posterior valha. É o primeiro
> item da fila técnica, e ele paga três vezes: conserta o anel, define a cauda do
> experimento natural e desvenda o modo 1 (abaixo).

### 21.2 O relógio de observação — o worker é bimodal, não impreciso

O worker escreve três instantes e **não os escreve do mesmo jeito**:

```
data_saida_real   <- derivado da serie historica
no_local_desde    <- NOW()      ...o instante em que o worker OLHOU
data_conclusao    <- NOW()      ...idem
```

É um experimento natural dentro do mesmo código: mesmo worker, mesmos dados, um campo
derivado e outro carimbado com relógio de parede. As correções do robô atemporal **sobre
valor já existente**, separadas por proveniência (o log grava autor; o worker não loga,
então valor sem escrita logada = worker):

| valor anterior escrito por | n | p50 | p90 | máx | antecipou |
|---|---|---|---|---|---|
| **worker** | **167** | 0,6 h | 13,3 h | 228,8 h | **159 (95%)** |
| o próprio robô (passadas anteriores) | 19 | 0,3 h | 203,3 h | 222,6 h | 3 (16%) |
| *(controle)* `data_saida_real`, derivado | **11** | 0,6 h | 7,3 h | 13,6 h | 11/11 |

O campo derivado precisou de **11** consertos; o carimbado, de **167** — e em 95% deles a
chegada verdadeira era anterior, que é a assinatura de um relógio de parede: ele só pode
chegar atrasado. O p90 de 13,3 h encosta em `FRESCOR_H = 12`, que a §20.3 já tinha medido
como o buraco mediano da carreta.

**Mas o número agregado não é erro do worker** — é a soma do atraso dele com o
adiantamento do anel. Particionando pelo teto de viés do robô (1 h, p99):

| faixa | n | leitura |
|---|---|---|
| ≤ 1 h | **100 (60%)** | cabe inteiro no viés do anel — **não atribuível ao worker** |
| 1–2 h | 14 (8%) | zona cinzenta |
| > 2 h | **53 (32%)** | além de qualquer viés do robô — **worker**, p50 **9,2 h**, p90 **25,3 h** |

**O experimento encolhe de 167 casos para 53 e endurece de p50 0,6 h para 9,2 h.** Menos
numeroso, mais grave, e sobrevive onde importa: na cauda, que é a que destrói janela de
trajeto, km e início de perna vazia.

E a distribuição é **bimodal**, com vale nítido:

```
0,25-0,50 h   39  #######################################   <- modo 1
0,75-1,00 h   10  ##########
1,50-2,00 h    5  #####                                     <- vale
2,00-3,00 h    3  ###                                       <- vale
6,00-12,0 h   17  #################                         <- modo 2
12,0-24,0 h   15  ###############
```

Não é decaimento suave: cai a 5 e 3 e **volta a subir**. **O worker é bimodal** — ou
dispara em tempo, ou falha por horas (a cascata da §20.4, o `FRESCOR_H`, o aparelho
dormindo).

> ⚠ **Caveat com prazo de validade.** No modo 1 o erro verdadeiro do worker é
> **inobservável**: o piso de viés do anel (0,42 h) fica exatamente ali. O histograma prova
> que as correções se separam em dois regimes; **não** prova que o worker acerta a 0,4 h —
> prova que ali seu erro está abaixo do piso do instrumento. O conserto do anel derruba o
> piso, e a reconvergência revela o modo 1 pela primeira vez.

**A consequência de arquitetura** — e é a melhor coisa que a adjudicação produziu: não é
"substituir o escritor ao vivo porque ele erra". É **dono do registro × autor de escrita**.
O worker grava provisório (`fonte=ao_vivo`) e serve a tela no segundo em que acontece; o
robô convergente confirma ou corrige (`fonte=derivada`) e a versão dele é a que vale para
janela, km e perna. A latência deixa de ser dilema — o ao-vivo alimenta a operação, o
convergido alimenta o registro — e o bug das duas verdades da §20.6 não reabre, porque a
precedência passa a ser **declarada** em vez de disputada.

### 21.3 As duas réguas de chegada — bloqueador de deploy

| | régua de chegada |
|---|---|
| branch (`embarques_auto.reanalisar_pendentes`) | `RAIO_CHEGADA_KM = 20`, estrito |
| `_robo_atemporal.py` | 20 km **ou** 60 km com parada ≥ 2 h |

**11 das 302 chegadas gravadas (3,6%) existem só pela regra larga** — C-371 a 54,6 km,
C-392 a 59,0, C-434 a 59,8, C-470 a 58,7, C-500 a 57,6, C-507 a 22,1, C-511 a 25,5,
C-566 a 58,7, C-637 a 44,7, C-639 a 58,8, C-653 a 56,7.

Subir o branch sobre a base convergida faz os dois motores discordarem em 11 cargas **no
primeiro ciclo**. E a §17.2 **mediu e reprovou** subir o raio (ganha 3, estraga 3); o motor
reintroduziu 60 km por outro mecanismo — a parada como discriminador, que é melhor
raciocínio — mas **esse mecanismo nunca passou pelo teste da §17.2**.

**Adjudicação das 11**, pela aproximação posterior:

```
C-371  Japeri            marcou 54,6 km -> chegou depois a  6,7 km   PERIFERIA (errada)
C-500  Duque de Caxias   marcou 57,6 km -> chegou depois a  2,2 km   PERIFERIA (errada)
outras 9 (Rio x4, Brasilia x2, R. Pires x3)   nunca chegaram mais perto
```

> ⚠ **CORRIGIDO em 09/09 — ver 21.15. O placar certo é 0 comprovadamente erradas, 11
> indeterminadas:** as duas "derrotas" eram medição minha sem teto documental, e a
> aproximação posterior pertencia à viagem seguinte da mesma carreta para o mesmo
> destino. O texto original fica abaixo como registro.
>
> **O placar honesto é assimétrico: 2 comprovadamente erradas, 9 indeterminadas.** "Nunca
> chegou mais perto" não é vitória — é não-falsificação, e é compatível com nunca ter
> entregado, que é classe real e medida (§15.3: 10 cargas sem chegada nem 7 dias depois).
> Somado ao p99 de 72 km do rótulo de cidade da 3S, distância-de-centroide é régua suja nos
> dois sentidos.

Consertos que saem daqui: **guarda de aproximação posterior** (com teto na fronteira
documental, senão a viagem seguinte migra a chegada da anterior) e **precisão declarada por
força da afirmação** — `derivada_estrita`, `derivada_larga`, `migrada`. A regra larga fica
publicada como **presumida** até corroboração. Assim ela pode errar sem mentir.

### 21.4 Âncoras por destino são ADITIVAS, nunca substituição

Destinos recorrentes têm ponto de entrega fixo deslocado do centroide. Medido sobre cargas
distintas com o mesmo destino (≥3 cargas):

| destino | n | centroide → agrupamento | dispersão p50 |
|---|---|---|---|
| Guarulhos/SP | 3 | 18,7 km | 0,9 km |
| Fortaleza/CE | 3 | 18,5 km | 1,5 km |
| Duque de Caxias/RJ | 13 | 17,5 km | 1,3 km |
| Cariacica/ES | 3 | 18,2 km | 2,5 km |
| Serra/ES | 18 | 16,7 km | 2,9 km |
| Brasília/DF | 19 | 20,4 km | 4,1 km |

**Duque de Caxias tem quatro âncoras, não uma:** o agrupamento de 10 cargas a 14–20 km, a
C-383 a 12,3 km, a C-532 a **2,2 km** e a C-500 a 57,6 km. Substituir o centroide pelo
agrupamento **quebraria a C-532** — o defeito antigo com sinal trocado. A forma certa é
chegada = raio estrito em torno de **qualquer âncora** do destino, cada uma com seu
histórico de suporte.

> ⚠ **E o agrupamento medido assim é artefato.** Os seis valores acima estão todos entre
> 16,7 e 20,4 km — colados na circunferência de 20 km. Não são docas: são o ponto em que a
> rodovia de acesso cruza o anel (21.1), e caminhões da mesma origem cruzam no mesmo lugar,
> por isso a dispersão dá 0,9 km. Aprender coordenada a partir desses pontos seria
> **circular**. A versão salvável aprende sobre **paradas longas**, não sobre instantes
> marcados.

### 21.5 O fechamento documental é pré-condição do escritor único

Um desacordo que só o código resolveu:

- no branch, `fechar_pendentes` encerra pelo **manifesto novo da mesma carreta sem
  consultar GPS nenhum** (só o `dedup_veiculo` exige `_chegou_ao_destino`);
- no `_robo_atemporal.py`, a mesma regra está atrás de `if n_cheg and not n_conc and c1:` —
  **exige chegada provada por GPS**.

Promover o motor a escritor único, como está, **apaga o caminho documental que o branch
tem**. Sob apagão da 3S ele fecha zero — e apagão já aconteceu (§19). A §19.2 é literal:
*"o robô tem de seguir com as regras antigas, que decidem por documento"*.

Manifesto novo **não é silêncio** — é evento documental positivo — então portar
`fechar_pendentes` para o motor não fere a regra de ouro em nada. **É pré-condição, não
refinamento.**

### 21.6 O que ficou assentado

| | número |
|---|---|
| vazias **entram** no circuito do robô por default | 63/63 `criada_por_robo=TRUE`, 63/63 com log; o que não converge são os **limites** da perna |
| janelas de perna descoladas das âncoras atuais | **13 de 63** |
| custo de uma passada do motor | **12,5 s** para 370 cargas; idempotente sobre base parada |
| `JANELA_FUTURO_D=20` × `JANELA_REANALISE_DIAS=30` | eixos distintos, não divergência; trunca **1** carga aqui, e o defeito é **invisível no laboratório** (base de 5 semanas, 0 pendentes com +20 d) |
| defeito do anel | **240 de 292** marcadas em movimento; viés p99 = 1 h |
| divergência dupla da §4.3 | parada perdida **e** 25 → 20 km |
| duas réguas de chegada | **11 cargas** (3,6%) |
| âncoras por destino | **4** em Duque de Caxias; aditivas, nunca substituição |
| cauda do worker | **53 casos**, p50 **9,2 h**, p90 25,3 h, máx 228,8 h |
| fechamento documental ausente no motor | pré-condição do escritor único |

### 21.7 O que foi derrubado — e o número que derrubou

Registrado porque **"assentado" nesta base tem meia-vida**, e só invariante rodando
continuamente mantém conclusão honesta.

| conclusão derrubada | o que a derrubou |
|---|---|
| "as vazias ficaram fora do circuito de convergência" | 63/63 entram por default; o defeito são os **limites**, o que é pior — passou pelo robô e *parece* auditado |
| "os agrupamentos por destino são docas reais" | seis medições independentes entre 16,7 e 20,4 km = a borda do anel |
| "Ribeirão Pires tem CD fixo fora do centroide" | 8 cargas, dispersão p50 **15,1 km** — é espalhamento, não doca |
| "a regra larga ganha 9 e perde 2" | assimetria: 2 **provadas**, 9 **indeterminadas** |
| "o worker erra p90 de 13,3 h" | **60%** cabe no viés do próprio robô; o erro dele é 53 casos a p50 9,2 h |
| "o placar 'depois' da §20.7 não está em arquivo nenhum" | `_auditoria_DEPOIS.csv` — 181 cargas, `C3` e `C1` zerados |
| "a C-147 tem chegada a 676 km do destino" | artefato de consulta: as placas dela têm **zero posições antes de 03/08** (carga de 29/06, GPS purgado) |
| "medir a partição antes do conserto mediria o viés duas vezes" | o delta anel→parada é medido sobre **posições**, independente do worker e do conserto |
| "escritor único agrava o risco 3S" *(conclusão certa, raciocínio vago)* | o mecanismo real é o gate `n_cheg` do motor, não a dependência de GPS em abstrato |
| "as 19 auto-correções são oscilação pós-convergência" | 15 delas no lote 17:23; o motor foi editado às **17:24** — é resposta mudando porque o motor mudou |

### 21.8 O que continua aberto, com o teste que fecha

| aberto | o teste |
|---|---|
| cauda definitiva do experimento natural | reconvergência **depois** do conserto do anel |
| erro do worker no modo 1 | idem — hoje está abaixo do piso do instrumento (**temporário por construção**) |
| as 9 chegadas presumidas | cruzamento com o CTe — precisa de dump novo de `conhecimentos_emitidos` e `manifestos` (o local tem até 10/07 e 31/03) |
| o universo do benchmark da §17.2 | identificar as **16 cargas** excluídas (ver 21.9) |
| ~~a imagem de produção (§20.10)~~ | ✅ **VERIFICADO em 09/09 — ver 21.14** |
| **modo sombra** | o motor rodando contra produção por alguns dias, comparando o que gravaria com o que o worker grava — **a única pergunta que nenhuma rodada pôde responder daqui** |

### 21.9 O benchmark da §17.2 se perdeu — e foi resgatado pela prosa

O script que produziu *"202 cargas com parada de entrega identificável, ganha 3 e estraga
3"* **não está na pasta**. A medição existe só como texto nesta documentação. É a terceira
vítima de o instrumental viver fora do git (as outras duas: a forense por `mtime` da
rodada 5, e as edições do motor anteriores às 17:24, irrecuperáveis).

Mas a §17.2 publicou os **números**, e isso salvou o instrumento. Reconstrução da cobertura
por raio sobre agosto:

```
                 reconstruido   §17.2 publicou   denominador que reproduziria
<= 20 km    256      87,7%           93,1%                  275
<= 30 km    262      89,7%           95,0%                  276
<= 40 km    264      90,4%           95,5%                  276
```

**Três razões independentes convergem no mesmo denominador.** Se os numeradores estivessem
errados, elas não se alinhariam num único número — são três equações e uma incógnita com
solução consistente. Conferindo: 256/275 = 93,09% · 262/276 = 94,93% · 264/276 = 95,65%.

Os numeradores estão certos; **o universo original era ~276 cargas, não as 292** do recorte
ingênuo — 16 a menos, das quais 10 já identificadas (cegas, sem posição utilizável na
janela). Resta identificar ~6.

> **A lição:** prosa com número é backup **degradado** de código — salva o veredito, não a
> calibração. Publicar placares completos, que pareceu redundância por nove rodadas, virou
> o mecanismo de resgate do instrumento que se perdeu. Prosa **sem** número seria perda
> total.

### 21.10 Regras de método

1. **Confundir o que o instrumento fez com o que o mundo fez** é o erro-mãe. Ocorreu três
   vezes em três rodadas — a métrica de dispersão que media a mediana contra a média, o CD
   inferido de marcas que o instrumento fabricou, e a consulta que atravessou a fronteira
   de retenção.
2. **Quando N medições independentes dão o mesmo número, a primeira hipótese é que se está
   medindo o instrumento.** Seis "docas" entre 16,7 e 20,4 km eram o anel.
3. **As duas réguas têm de ser a mesma** (já era da §20.6) — e a fronteira entre **motores
   diferentes** é onde ninguém olha.
4. **Convergência é o teste**, e vale para o debate: delta por rodada caindo
   monotonicamente é convergência; oscilando é bug.
5. **Primitiva compartilhada e instrumento versionado são a mesma exigência vista de dois
   ângulos.** As armadilhas conhecidas estão blindadas dentro do motor e do aferidor, e
   desprotegidas em toda consulta escrita à mão.

### 21.11 Checklist de pré-deploy — dez itens

Cada linha carrega o número que a justifica.

| # | item | por quê |
|---|---|---|
| 1 | **parada exigida no raio estrito** | 240 de 292 marcadas em movimento · §4.3 já mandava |
| 2 | **raio re-derivado** do benchmark (não "restaurado por fidelidade") | pré-condição: identificar as 16 cargas do universo original (21.9) |
| 3 | **régua de chegada única** entre motor e branch | 11 cargas descasam no primeiro ciclo |
| 4 | **guarda de aproximação posterior**, com teto na fronteira documental | pega C-371 e C-500; sem teto, a viagem seguinte migra a chegada da anterior |
| 5 | **âncoras aditivas**, nunca substituição | 4 âncoras em Caxias; substituir quebraria a C-532 |
| 6 | **`fechar_pendentes` documental portado para o motor** | pré-condição do escritor único; sem ele, apagão da 3S fecha zero |
| 7 | **janelas alinhadas** (`JANELA_FUTURO_D` × `JANELA_REANALISE_DIAS`) | invisível no laboratório, real em produção |
| 8 | **invariante de velocidade-na-chegada** no aferidor | teria acendido 240 luzes na primeira rodada |
| 9 | **reconvergência com remedição** | muda a régua ⇒ a base inteira precisa reconvergir antes de qualquer número valer |
| 10 | **biblioteca de primitivas compartilhadas** — janela de evidência, grafias, âncoras, proveniência de instante | grafia mordeu 2× (§12.6, §14.5), retenção mordeu a C-147, anel mordeu os agrupamentos |

E o **religar** da §19.4 continua valendo como portão da chave
`EMBARQUES_MODELO_CARRETA=true`: conferir o feed, re-simular com setembro **completo** (os
números da §18 são de amostra parcial), rodar os 15 testes, e só então virar a variável.

### 21.12 As decisões de negócio da §8, com o artefato que destrava cada uma

Para que não voltem a ficar paradas por falta de dado que já existe:

| decisão da §8 | o que a destrava |
|---|---|
| Carreteiro entra no robô? | o **balde de período não documentado** do ladrilhamento — hoje 36% do km de carreta não tem dono e ninguém sabe quanto disso é Carreteiro |
| Transbordo vira status próprio? | dump novo de CTe (critério já validado na §20.9: **mesma carreta sai com carga nova**, mais a negativa de chegada) |
| A carga carrega todos os destinos do manifesto? | **sem bloqueio técnico** — é decisão pura |
| Reabrir as fechadas indevidamente em agosto? | absorvida pela reconvergência (item 9) |

### 21.13 As consultas que decidiram, e por que viram invariante

Estas quatro decidiram o que nove rodadas de argumento não decidiram. Enquanto viverem como
consulta ad-hoc, a próxima "pergunta que ninguém fez" custa outra adjudicação; como
invariante do `_auditoria_geral.py`, custa uma rodada de aferidor.

```
proveniencia por autor    quem escreveu o valor anterior de cada correcao
                          (o worker nao loga; valor sem log anterior = worker)
drift de ancora           o inicio da perna vazia ainda casa com a conclusao
                          atual da carga anterior da mesma carreta?
velocidade na chegada     o veiculo estava parado no instante marcado?
aproximacao posterior     ele chegou mais perto depois, dentro da janela da carga?
```

> **Todas as descobertas destas nove rodadas estavam no banco desde sempre** — o relógio de
> observação no `embarques_cargas_log`, as duas réguas nos dois arquivos desde 07/09, o
> anel no histórico de posições desde agosto. Nenhuma precisou de dado novo. Todas
> precisaram de uma **pergunta** nova.

### 21.14 A imagem de produção — VERIFICADO em 09/09/2026

A suspeita da §20.10 estava certa, e o mecanismo é pior do que "o Portainer reverteu a
imagem": **foi um deploy que nunca aconteceu e ficou registrado como se tivesse
acontecido.**

```
servico ..... rizza-auditoria_app        imagem  ghcr.io/.../rizza-auditoria:latest
container ... criado 02/09/2026 17:01:52 -0300   ·   Up 6 dias, NUNCA reiniciado

--- codigo NO AR, lido dentro do container ---
709:            if encerrar(cur, cid, 'baixa_ctrb'):
721:        if encerrar(cur, cid, 'timeout'):
722:            fechadas['timeout'] += 1

--- env NO AR ---
EMBARQUES_AUTO=true          EMBARQUES_AUTO_JANELA_DIAS=1          START_WORKER=true
```

**As duas regras que a §7 descartou estão ativas em produção** — não só a `baixa_ctrb` que
a §20.10 suspeitava, mas o `timeout` junto. E a `EMBARQUES_AUTO_JANELA_DIAS=1` que a §13.3
mandava voltar para 5 também segue.

O commit que removeu as duas diz, na própria mensagem:

> `76f07a3` (04/09) — *"Já estava em produção; este commit alinha o repositório."*

**Não estava.** O container é de 02/09, anterior ao commit, e nunca foi reiniciado. A
§16.4 documentou a `baixa_ctrb` fechando C-607, C-609 e C-614 em **02/09 19:42:34** —
dentro deste mesmo container, que subiu às 17:01 do mesmo dia. Mesmo container, mesmo
código: a regra ativa está nessa imagem, e roda todo dia às 16:30 desde então.

> **A lição, e é gêmea das outras desta seção:** a §16.4 escreveu *"Nada a consertar: a
> regra já saiu."* Ela saiu **do repositório**, não do ar. Estado do código ≠ estado do
> serviço, e a única prova é ler o código **dentro do container** — a tag da imagem não
> serve (é sempre `:latest`), e a memória de quem fez o deploy serve menos ainda.

**O que a verificação também provou, do lado bom:** `MODELO_CARRETA = 0`,
`reanalisar_pendentes = 0` e `km_odometro = 0` no ar — **a isolação da §19.3 está
funcionando**. Nada do pacote congelado vazou para produção. O que está velho é o modelo
antigo, não o novo.

**Decisão de 09/09 (Gabriel):** não mexer em produção agora. Dois motivos, os dois bons —
o plano termina subindo código e dados corrigidos, então o que produção fizer até lá é
sobrescrito pelo reprocessamento; e **editar env pela stack do Portainer é justamente o
gesto que já fez o serviço voltar para imagem antiga em 21/08**. A validação é local até o
fim.

> ⚠ **Consequência para o passo 3 da §13.3:** a base de produção diverge um pouco mais a
> cada dia que a torneira fica aberta (~7 execuções do robô desde 02/09, cada uma com uma
> regra medida em 50% de erro na §3.1). O script de subida **precisa** das guardas de
> comparação de estado — não é zelo, é requisito.

**O marcador certo, para a próxima vez.** `grep -c "baixa_ctrb"` **não serve**: a `main`
também tem uma ocorrência, na docstring que explica a remoção. O que decide é o **sítio de
chamada**:

```bash
docker exec <container> grep -n "baixa_ctrb\|'timeout'" embarques_auto.py
# so linha de comentario  -> imagem atual
# if encerrar(cur, cid, ...) -> imagem velha
```

### 21.15 Fase 1 e 2 aplicadas — e a retratação que elas produziram (09/09/2026)

O checklist saiu do papel. O que mudou, com o numero de cada coisa:

| item | estado |
|---|---|
| 1 · parada exigida no raio estrito | **aplicado** — 255/255 chegadas para FRENTE, p50 0,42 h, convergiu em 2 passadas |
| 3 · regua unica motor × branch × aferidor | **aplicado** — `embarques_regua.py`; as 11 cargas de metropole passaram de 0/11 para **11/11** de concordancia |
| 7 · janelas alinhadas | **aplicado** — horizonte de evidencia unico de 30 dias nos tres |
| 8 · invariante de velocidade-na-chegada | **aplicado** — `C6`; dispara em 135 de 279 (48%) nos valores originais do worker e em **zero** na base corrigida |
| 10 · biblioteca de primitivas | **iniciada** — a regua e a primeira peca; faltam janela de evidencia, grafias, ancoras e proveniencia |
| 2 · raio re-derivado · 4 · guarda de aproximacao · 5 · ancoras aditivas | ver abaixo — **a evidencia que os motivava mudou** |

15 de 15 testes passam. Aferidor: 181 -> 180 cargas com achado.

#### O que a Fase 1 revelou: o worker e bimodal, e e otimo

Com o piso de 0,42 h do anel fora do caminho, o erro real do worker aparece pela primeira
vez (o "modo 1 inobservavel" da 21.2 deixou de ser inobservavel):

```
0-5 min      90  ##########################################
5-15 min      6  ###
15-30 min     2  #
30-60 min     8  ####
1-2 h         8  ####     <- vale
2-3 h         3  ##       <- vale
3-6 h        14  #######
6-12 h       19  #########
12-24 h      11  #####
```

**55% das chegadas do worker estao a menos de CINCO MINUTOS do instante real.** Ele nao e
impreciso — e bimodal, e o modo bom e quase exato. Isso fecha a questao de arquitetura da
21.2 a favor de **dono do registro × autor de escrita**: nao ha por que tirar da tela um
escritor que acerta em minutos; ha por que nao deixar o registro na mao dele.

#### Dois achados que nasceram do proprio conserto

**A guarda de velocidade SUBSTITUIA em vez de validar.** Ela montava a lista de candidatas
pelo raio estrito e trocava a chegada sempre que a primeira candidata diferisse — o que
descartava, por construcao, toda chegada derivada por outra regua (metropole, presumida).
Agora valida, e so procura substituta quando a apurada e implausivel. Efeito: as 9
"velocidades impossiveis" sumiram sozinhas, porque **era o anel que as fabricava** —
encurtando o tempo de viagem, ele inflava a velocidade media. Corroboracao independente do
diagnostico.

**O `F4` do aferidor media permanencia contra o RELOGIO DE PAREDE.** Acusava 7 cargas de
06-08/09 que o motor deixou abertas com toda a razao: a base termina em 08/09 18:32 e o
motor nao afirma permanencia que ninguem observou. Mesma armadilha da secao 16.1.

#### RETRATACAO — as "2 derrotas comprovadas" da regra larga nao existem

A secao 21.3 registrou o placar da regra de 60 km como **"2 comprovadamente erradas, 9
indeterminadas"**, e as duas eram a C-2026-000371 (marcou 54,6 km, "chegou depois a 6,7 km")
e a C-2026-000500 (marcou 57,6 km, "chegou depois a 2,2 km"). O documento chegou a chamar
isso de "o fantasma da secao 17.2 medido em escala".

**Estava errado, e o erro era da minha medicao, nao da regra:**

```
C-2026-000371  01/08  carreta TYN8I98  Rubiataba  -> Japeri/RJ
C-2026-000481  22/08  carreta TYN8I98  Rubiataba  -> Japeri/RJ          <- a aproximacao de 26/08 e DESTA

C-2026-000500  24/08  carreta QXA9H76  Neropolis  -> Duque de Caxias/RJ
C-2026-000532  26/08  carreta QXA9H76  Seropedica -> Duque de Caxias/RJ <- a de 27/08 e DESTA
```

Nos dois casos a "aproximacao posterior" pertence a **viagem seguinte da mesma carreta para
o MESMO destino**. Eu medi aproximacao sem teto documental — que e literalmente a ressalva
que a revisao tinha feito ao propor a guarda: *"'se o veiculo depois se aproxima' tem que
valer dentro da janela da carga, senao a passagem da viagem seguinte pelo mesmo corredor
migra a chegada da carga anterior para um evento que nao e dela."* O teto do motor ja fazia
a coisa certa; o auditor ad-hoc e que nao fazia.

**Consequencias:**

1. O placar honesto da regra larga passa a ser **0 comprovadamente erradas, 11
   indeterminadas**. Ela nao foi falsificada em caso nenhum.
2. O **item 4 do checklist (guarda de aproximacao posterior) perde a evidencia que o
   motivava** e sai da fila. Construi-lo agora seria *introduzir* o defeito que eu pensei
   estar consertando: migrar a chegada para um evento da viagem seguinte.
3. O **item 2 (raio re-derivado)** perde urgencia junto — nao ha erro medido do raio largo
   para corrigir. Continua valendo reconstruir o benchmark da secao 17.2 (21.9), mas como
   instrumento, nao como arbitro de uma disputa que deixou de existir.
4. O **item 5 (ancoras aditivas)** segue de pe: as 4 ancoras de Duque de Caxias sao fato, e
   substituir centroide por agrupamento quebraria a C-2026-000532. So que agora e melhoria,
   nao conserto de defeito.

> **A regra de metodo, que e a mesma de sempre e ja errou quatro vezes nesta documentacao:**
> toda comparacao "depois ele fez X" precisa da janela que define **de quem** e o "depois".
> Sem teto, a viagem seguinte se disfarca de correcao da anterior.

#### O que o conserto do anel fez com essas duas cargas

Vale registrar porque parece regressao e nao e:

* a **C-2026-000371 ficou SEM chegada**. Correto: dentro da janela dela (ate o manifesto de
  22/08) a carreta nunca parou perto de Japeri. A carga esta `Entregue` sem prova, que e o
  que o motor sinaliza com `sem_prova_revisar` — e e mais honesto que a chegada inventada
  na borda do anel que ela tinha antes.
* a **C-2026-000500 passou de 57,6 para 47,0 km**, pela exigencia de parada sustentada. Nao
  chegou a ficar certa, mas o instante agora tem lastro de parada, nao de travessia.

### 21.16 A perna vazia entra na convergência — e o escritor duplo que eu criei (09/09/2026)

Item 3.5 do plano: a janela da perna vazia nao converge junto com as cargas. Ela e derivada
inteiramente das vizinhas —

```
inicio = data_conclusao da carga A       fim = data_saida_real da carga B
```

— e essas duas ancoras sao exatamente o que o robo atemporal corrige a cada passada. Os
EVENTOS da perna convergiam sobre uma JANELA que nao convergia, o que e pior do que estar
fora do circuito: a perna passa pelo robo e *parece* auditada.

Ferramenta nova: **`_rederivar_vazias.py`**, que atualiza a janela **no lugar**, com log. Nao
e o `_regerar_vazias_agosto.py` — aquele faz DELETE + INSERT, apagaria o
`embarques_cargas_log` das 63 pernas e trocaria a identidade (o `V-`) de cada uma. Serve para
criar do zero, nao para reconciliar.

**Resultado:** 32 pernas rederivadas, 27 ja coerentes, **4 com par sobreposto** (a carga B
partiu antes de a A concluir — problema NAS CARGAS, e a perna fica contestada em vez de
maquiada com janela artificial).

#### O escritor duplo, e como ele apareceu

Ao rodar motor e rederivacao em sequencia, os dois **oscilaram: 13 e 13, toda passada.**

```
passada 1:  motor=13   rederivacao=13
passada 2:  motor=13   rederivacao=13
passada 3:  motor=13   rederivacao=13
```

O motor derivava `data_conclusao` da perna pelo GPS; a rederivacao a escrevia de volta pelas
vizinhas; e assim para sempre. **Oscilacao e bug, nao convergencia** (secao 20.6) — e o bug
era meu: eu tinha acabado de criar um segundo escritor para o mesmo campo, que e literalmente
o defeito que a secao 21.2 diagnosticou no par worker/robo.

A regra que resolve sai do primeiro principio: **a perna vazia nao tem evento proprio.** A
janela dela e, por definicao, o intervalo entre o fim da carga A e o inicio da carga B — e
por isso o dono e a rederivacao. O motor so apura o que acontece DENTRO dela (chegada,
status). Com o dono declarado, a convergencia conjunta passou a ser imediata e estavel em 3
rodadas.

> **Um campo, um dono.** Vale para worker × robo (21.2) e vale aqui. Foi a terceira vez neste
> projeto que dois escritores do mesmo campo produziram oscilacao, e a primeira em que o
> segundo escritor fui eu.

#### Regua de carga nao mede perna

Com as janelas verdadeiras a vista, o aferidor passou a acusar **7 achados novos de uma vez**
— e estava errado, porque aplicava regras de carga a pernas:

* **`F3` (recorte da conclusao errado)** espera conclusao = "saiu do destino" ou
  "chegada + 24 h". Mas a conclusao de uma perna E a saida da carga B, por definicao: a
  carreta chega ao ponto de recarga e **espera**. A `V-2026-000017` esperou 23 dias. Nao e
  recorte errado, e o significado do campo.
* **`T5` (velocidade impossivel)** dava "165 km em 0,0 h" quando a carreta **ja estava** no
  destino da perna no inicio da janela. Informacao real, mensagem errada.

Os dois passam a exemplar `vazia`, e a perna ganha a invariante que ela precisa:

**`P1` — PERNA SEM DESLOCAMENTO.** A perna so existe se a carreta saiu de um lugar e foi para
outro; quando ela ja estava no destino quando a janela abriu, nao ha reposicionamento — ha
carreta parada esperando carga, e a "perna" e efeito cascata de um fechamento errado da carga
anterior (secao 16.3). **Dispara em 5 pernas.** A secao 17.4 tinha achado essa mesma classe
(9 casos) abrindo mapa a mapa; aqui e uma linha de aferidor.

#### Lacuna nao documentada — rotulo, nao metrica

A trava de plausibilidade da secao 12.5 (que ja existia para o km) passou a valer para a
**janela**: se o intervalo nao cabe na distancia, aquilo nao e perna, e um buraco com
conteudo desconhecido dentro. O caso-tipo e a `V-2026-000035` (Brasilia -> Uberlandia), cujas
ancoras de hoje dizem **22 dias** contra os 15 h que ela tinha gravados.

**16 das 63 pernas** sao assim, e todas levam rotulo em `observacoes` para a tela nao desenhar
22 dias de reposicionamento como se fosse viagem. Pela decisao da secao 8 nº 2 esses buracos
sao Carreteiro e estao **fora de escopo**: o rotulo existe para explicar, nao para virar meta.

#### Placar

```
aferidor:  180 cargas / 238 achados  ->  179 / 235
           V1  22 -> 18   (janela rederivada consertou 4 "nunca esteve na origem")
           F3  19 -> 16   ·  T5  1 -> 0  ·  C6  1 -> 0  ·  P1  0 -> 5 (nova)
15 de 15 testes passam · convergencia conjunta motor+pernas estavel em 3 rodadas
```

### 21.17 Fechamento documental no motor — e a separação entre encerrar e entregar (09/09/2026)

Item 3.2, a pre-condicao do escritor unico. O motor exigia `n_cheg` — prova de chegada por
GPS — para fechar pelo manifesto novo. O efeito so aparece no apagao: sem feed o motor fecha
**zero**, enquanto o `fechar_pendentes` do branch continua fechando por documento. A secao
19.2 e literal sobre qual e o comportamento desejado: *"o robo tem de seguir com as regras
antigas, que decidem por documento"*.

E nao fere a regra de ouro: **manifesto novo nao e silencio, e evento documental positivo.**
Uma carreta carregada nao fica em dois lugares — quando ela sai de novo, a viagem anterior
acabou.

O que muda e a FORCA da afirmacao, e ela passa a ser **declarada no motivo**:

```
manifesto_novo_carreta   a viagem acabou E o GPS provou a chegada ao destino
manifesto_novo_sem_gps   a viagem acabou (documento); a ENTREGA nao esta provada
```

**Efeito medido: 5 cargas** que ficariam abertas para sempre passam a fechar — 3 `Em rota` e
2 `Aberta`. Nenhuma carga ja fechada foi tocada, pelo motivo abaixo.

#### Dois consertos que a medicao obrigou, antes de aplicar

**1. Nao rebaixar precisao.** Na primeira versao a regra mexeria em **28** cargas, e 23 delas
so para reescrever a `data_conclusao` para **meia-noite** — porque o manifesto nao tem hora
(secao 18.1). Isso trocaria um instante com hora por uma resolucao de dia, que e exatamente
como nasceram as 37 conclusoes em 00:00 da secao 15.2. A guarda: a conclusao documental so
grava quando **nao ha** conclusao; ela nunca sobrescreve um instante que tem hora. A
discordancia nao some — vira achado do aferidor, que e onde deve estar.

**2. `entregue_auto` significa ENTREGA PROVADA, nao "o robo fechou".** O motor marcava `TRUE`
incondicionalmente. A convencao e do `embarques_auto.encerrar`: *"fica em 'Entregue' com
entregue_auto=FALSE e encerrada_motivo != 'gps' [...] a coluna de motivo impede que
fechamento por regra se confunda com entrega provada por GPS"* — ate a confirmacao manual da
tela grava FALSE. Fechamento documental nao prova entrega nenhuma; marcar TRUE publicaria
como entrega verificada o que e so fim de viagem. Agora e `TRUE` so quando o motivo comeca
com `gps`.

#### O `F1` era um balde com duas coisas dentro

A secao 4.2 separa **encerramento** (manifesto: a viagem acabou) de **entrega** (GPS/CTe: a
mercadoria chegou). O aferidor nao separava: tudo caia em `F1 — FECHADA SEM PROVA`. Medido:
**38 das 39** cargas do `F1` antigo tinham manifesto novo da mesma carreta.

Elas nao deixam de ser entregas nao provadas — mas deixam de ser encerramentos inexplicados,
que e outra coisa e leva a outra acao. Entao o codigo se divide:

```
F1    fechada SEM PROVA NENHUMA — nem GPS, nem manifesto novo          gravidade alta
F1d   entrega nao provada, mas o ENCERRAMENTO tem lastro documental    gravidade media
```

Resultado:

```
F1   39 -> 1        F1d  0 -> 43
```

O unico `F1` puro que sobra e a `C-2026-000662`: fechada com `sem_prova_revisar`, destino
Santa Izabel do Para, **aproximacao maxima de 1.726 km**. E o caso em que o sistema tem de
dizer "nao sei" e chamar um humano — e agora ele diz isso sozinho, em vez de esconder o caso
no meio de 43 outros.

#### Um teste corrigido pela segunda vez no mesmo dia

O caso "C-2026-000603 nao foi encerrada" quebrou de novo — desta vez porque o motor passou a
fecha-la por documento, que e **outro componente com outra regra**, e nao e o que aquele teste
protege. Ele lia o estado ambiente do banco. Agora **monta a propria pre-condicao** (abre a
carga, roda a reanalise, afirma sobre ela), como o teste vizinho da C-617 ja fazia.

> Teste que depende de estado ambiente mede o vizinho. Foi a segunda quebra do mesmo teste em
> um dia, e as duas foram legitimas — o vizinho tinha melhorado.

#### Placar

```
aferidor:  179 cargas / 235 achados  ->  181 / 240
           F1  39 -> 1   ·   F1d  0 -> 43   (o +5 sao as cargas recem-fechadas por documento)
15 de 15 testes · convergencia conjunta estavel em 3 rodadas
```
