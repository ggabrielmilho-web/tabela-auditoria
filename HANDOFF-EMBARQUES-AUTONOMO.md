# Handoff — Painel de Embarques autônomo

**Estado em 11/09/2026 — ⚠ COMECE PELA §24.** Continuação e desengate de pátio: a C-677
provou que "trocou o cavalo = desengate, e só a carga seguinte entrega". Implementado atrás de
`EMBARQUES_CONTINUACAO` (desligada), testado com o robô real dia a dia na base local (achou
2 bugs que os simuladores não viam), **não deployado**. Rede de segurança: tag
`pre-continuacao-2026-09-11` + chave + `_snapshot_embarques.py`. Receita de subida na §24.7.

**Antes disso — 10/09 (noite), §23.** Três defeitos sem relação entre si,
dois deles invisíveis para todo instrumento que existia. O maior: **produção passou 30 horas
sem rastreamento e nada acusou** — a tabela `embarques_rastreio_dia` nunca tinha sido criada
lá, `_consolidar_dias` levantava, e o `rollback` levava junto as posições que já estavam
gravadas, enquanto o log da 3S (conexão separada) mostrava HTTP 200 sem um único erro. Junto
vieram a **posição falsa** do GPS (301 pares em 34 placas, 22% dos mapas) e o **ponto fixo
conjunto** do par motor×janela. Ver §23. O que está aberto está na §23.8, e o item nº 1 é o
health, que mente.

**Antes disso — §22:** O pacote está em **produção** e o pipeline
**fechou**: gravidade alta de 92 para 50, sete classes de defeito a zero, as **110 pernas
vazias** criadas, geocodificadas e com rota, e a convergência conjunta atingida — pernas
24 → 0 → 0 e motor 26 → 0 → 0. É a primeira vez que produção converge com as pernas dentro
da base. A §22.9 conta o que fechou o ciclo e os dois erros que custaram uma rodada cada
(rederivar antes de traçar; aplicar sem conferir que a imagem tinha o conserto).

E a §22.10 é a mudança de regime: o robô diário estreou com o código novo, e ficou provado
que **a carga nasce já em viagem** — 11 cargas `Aberta` cujo caminhão já tinha saído, 4 deles
em cima do destino. Quem conserta isso é o robô atemporal, que existia desde 09/09 e **nunca
era chamado**. Agora ele roda sozinho logo depois do diário, em quatro passos (motor →
pernas → rotas → janela), com a janela terminando SEMPRE em hoje. O que continua aberto está
na §22.8 e no fim da §22.10.

**Antes disso:** a §21 (nove rodadas de adjudicação — o defeito do anel, as duas réguas, os
consertos das Fases 1 e 2) e a §20 (a 3S voltou; agosto e setembro reprocessados na base
local). O modelo carreta-cêntrico **entrou na `main`** em 09/09 (`62ac510`), atrás da chave
`EMBARQUES_MODELO_CARRETA=false` — nenhuma regra dele executa hoje.

> ✅ **A suspeita da §20.10 estava certa e foi resolvida.** Produção rodava a imagem de
> 02/09, com o `baixa_ctrb` e o `timeout` ativos — regras removidas do código em 04/09 que
> nunca chegaram à imagem no ar (§21.14). O deploy de 09/09 as tirou de circulação; os
> `baixa_ctrb` que ainda aparecem na coluna `encerrada_motivo` são **histórico**.

> 🔒 **O `.env` tem `START_WORKER=true`.** Subir o servidor local com `python server.py`
> direto **liga o worker e ele reprocessa a base convergida**, desfazendo o que a §20
> construiu. Sempre sobrepor no comando:
> `START_WORKER=false EMBARQUES_AUTO=false PGR_SYNC_CADASTRO=false python -X utf8 server.py`

| | estado |
|---|---|
| **o robô (`embarques_auto.py`)** | na `main` e **no ar em produção** desde 09/09, sem `baixa_ctrb` e sem `timeout`. O fechamento reescrito (§18) veio junto no merge, atrás de `EMBARQUES_MODELO_CARRETA=false`. A abertura não foi tocada em lugar nenhum |
| **KPI, alertas e correções de mapa** | **na `main` e em produção** desde 09/09 (`62ac510`) |
| **rastreamento (3S)** | **DE VOLTA em 08/09/26** — 93 veículos, 38 placas com posição < 1 h. Ficou o rastro: 5 carretas novas mudas desde 01–03/09 (§20.1) |
| **motor + aferidor novos** | `_robo_atemporal.py` e `_auditoria_geral.py` — **na `main` desde 09/09** (`66451b4`, tag `estudo-embarques-2026-09-08`), junto deste handoff, dos simuladores e dos CSVs. Rodam DENTRO do container, contra o banco de produção. Convergem a zero em 3 passadas (§20.6), e a passada de 09/09 confirmou: **0 alterações em 12,5 s** |
| **a adjudicação (§21)** | 9 rodadas, **nenhum arquivo alterado**. Achou o defeito do anel (240 de 292 chegadas marcadas em movimento), as duas réguas (11 cargas), a divergência dupla da §4.3 e a bimodalidade do worker (cauda de 53, p50 9,2 h) |
| agosto + setembro reprocessados pelo robô | **local E produção**. Em produção o robô atemporal rodou contra o banco real e convergiu (§22.1); não houve transporte de dados |
| `manifesto_origem` | **276/276 cargas reais com chave** (§20.2); as vazias sem chave estão corretas — 63 local, 109 em produção |
| rota planejada (ORS) | **370/370** local. Em produção **faltam as 109 pernas vazias** — §22.3 |
| camada de consolidação diária | **commitada** (`7de4860`) e no ar |
| auditoria dos 324 mapas | **rodada em 07/09** — §14; achou 1 bug na sanidade geométrica (§14.4) |
| auditoria de chegada/fechamento | **rodada em 07/09** — §15; o raio de 20 km no centroide acusa falso em metrópole |
| os 9 consertos da fila | **7 aplicados e medidos, 1 derrubado pelo teste, 1 é decisão de negócio** — §17 |

As medições foram feitas sobre agosto/2026 com dado real de produção — 433 mil posições
GPS, 229 cargas, 2.281 CTes.

> ✅ **A ordem foi respeitada:** o robô foi consertado primeiro, e só então a base de
> produção foi rederivada — pelo próprio robô atemporal rodando lá dentro, sem transporte de
> dados e sem a defasagem da base local. Ver §22.1.

> ⏭ **O que falta e é de negócio:** a perna vazia mistura **reposicionamento** (horas) com
> **lacuna sem carga** (dias — provável Carreteiro). Um terço do "km vazio" é a segunda
> coisa. Separar as duas na tela é decisão do Gabriel — §22.4.

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
| `_ensaio_pipeline.py` | **o ensaio único** do ciclo inteiro (§23.6) — não escreve nada por padrão |
| `_teste_ciclo_transacao.py` | regressão do apagão de 30 h: as três etapas do worker são independentes? (§23.2) |
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

### 21.18 Subrótulo de `Aberta` — rótulo, nunca status (09/09/2026)

Item 3.6. A [§16.5](#165) tinha medido o problema e ja dado o veredito: *"o que esta errado
nas duas nao e o status, e o ROTULO"* — `Aberta` e o mesmo balde de "nem saiu" e "sumiu no
meio", e os dois pedem acoes opostas. A 21.7 registrou por que a versao "dividir o status"
estava errada: a secao 0 garante que nenhum status muda de significado, e toda tela que
filtra `Aberta` quebraria.

Entao o status fica, e o que se acrescenta e uma pista. Tres valores, todos derivados:

```
na_origem      a placa esta na origem. Ainda nao saiu — normal, nao precisa de nada.
placa_longe    a placa transmite, mas de outro lugar: provavel carreta errada no
               documento. E o que o aferidor chama de V1, e ha 22 delas na base.
sem_posicao    ninguem sabe (o alerta de rastreio ja cobre).
```

**Custo zero de varredura:** le `embarques_posicoes_atuais`, uma linha por placa, em vez de
percorrer historico na tela principal do operacional. O raio de "esta na origem" vem da
**regua unica** (`embarques_regua.RAIO_ORIGEM`), nao de um numero novo — senao a tela
discordaria do motor.

#### A guarda que a medicao obrigou

Na primeira versao a `C-2026-000582` saiu rotulada **"placa longe, 720 km"** — e a carreta
dela nao transmite desde **julho**. A posicao "atual" era de dois meses atras. Publicar essa
distancia convidaria o operacional a concluir que o veiculo esta em outro lugar, quando o que
se sabe e que **ninguem sabe**.

**Posicao velha nao e fato sobre hoje.** Com a guarda de frescor, as 6 cargas de rastreio
defasado caem em `sem_posicao` — onde o alerta que ja existe diz a coisa certa — e sobram
duas `placa_longe` de verdade, com posicao fresca: a `C-2026-000674` a 411 km da origem e a
`C-2026-000676` a 332 km.

```
sem_posicao   6      placa_longe  2      na_origem  2
```

#### A garantia da §0 conferida, nao presumida

```
diff: 62 insercoes, 0 remocoes
_validar_carga_payload · _buscar_conflitos · POST /api/embarques/cargas · edicao ... intocados
nenhum status novo: o unico literal que o diff acrescenta e a LEITURA de 'Aberta'
os demais status devolvem aberta_situacao = None, e nenhum campo interno vaza no JSON
```

### 21.19 A chegada passa a declarar a própria fonte (09/09/2026)

Item 3.1, escopado pelo que estava **concretamente se perdendo**. A conclusao ja tinha
`encerrada_motivo` e a saida ja tinha `saida_auto`; a chegada era o unico evento sem lastro
declarado — o motor CALCULAVA a forca dela (`estrita`, `presumida_silencio`, `metropole`) e
jogava fora, gravando so o instante. Tela, aferidor e a rodada seguinte tinham de re-derivar,
cada um com a sua regua, o que e como as reguas divergem.

Coluna nova `no_local_fonte` (DDL idempotente no `garantir_colunas`, como o resto), com o
**mesmo vocabulario da `embarques_regua`** — uma linguagem so. Estado da base:

```
estrita              283      metropole             11
estrita+velocidade     4      presumida_silencio     1      (sem fonte)  1
```

A unica sem fonte e a `C-2026-000632`: chegada gravada por producao que a regua atual **nao
corrobora nem rejeita**. `NULL` ali significa "nao estabelecida por esta regua", que e a
verdade — e agora da para perguntar isso ao banco em vez de adivinhar.

A fonte tambem se atualiza **sem o instante mudar**: quando o backfill traz o ponto parado
que faltava, a mesma chegada passa de `presumida_silencio` para `estrita`. Sem isso o campo
congelaria na primeira gravacao.

#### O bug que eu introduzi, e por que a base se curou sozinha

Acrescentar a coluna ao `SELECT` **deslocou os indices posicionais** que montavam o indice de
"proximo manifesto":

```python
if not r[16] and r[14]:                      # r[16] era viagem_vazia, r[14] era carreta1
    prox[pl.mercosul(r[14])].append(...)     # viraram carreta2 e cavalo
```

O robo passou a parear manifesto **pela placa errada** e nao reclamou de nada — escreveu 316
linhas assim. Indice posicional sobre `SELECT` que cresce e armadilha esperando o dia.
Agora sao indices **nomeados**, com `assert` que quebra se o mapa e o `SELECT` divergirem.

**A recuperacao foi de uma passada: 42 cargas corrigidas, depois zero.** E o desenho
convergente pagando — o motor rederiva da fonte em vez de incrementar estado, entao escrita
errada se conserta na rodada seguinte.

> Mas convergencia sozinha **nao prova** que o estrago sumiu: se o bug preencheu um campo
> VAZIO, o motor corrigido pode simplesmente nao mexer mais nele (o `if n_conc and not dconc`
> nao dispara com `dconc` ja preenchido). O teste que fecha e direto, no log: *escritas do
> lote bugado em campo vazio que o lote corretivo nao revisitou* — **zero**. Verificado, nao
> inferido.

#### Placar

```
aferidor:  181 cargas / 240 achados  ->  176 / 233      (F3  16 -> 9)
15 de 15 testes · convergencia conjunta estavel · campo circulando ate a API
```

### 21.20 A C-2026-000677 — o caso que o Gabriel achou na tela (09/09/2026)

Vale inteiro porque **o sistema ja tinha diagnosticado sozinho**, e o que faltava era a tela
nao contradizer o aferidor.

**O que a tela mostrava:** carga `Entregue`, linha parando em Pirassununga com 101 km
rodados, "Posicao atual: Pirassununga/SP, ha 3d", `KM FALTANDO 823,8 km`, chegada `—`.

**Quem fechou:** producao, nao o robo. O log tem tres linhas e nenhuma e o fechamento — a
carga chegou de producao ja `Entregue` com `data_conclusao = 06/09 00:00:00` (meia-noite, a
assinatura documental da secao 15.2). O robo so acrescentou `inicio_viagem` e carimbou
`sem_prova_revisar`.

**Por que fechou** — a secao 3.2 com nome e sobrenome:

```
C-2026-000639  03/09  cav=FFA2I61  car=QXA9H76   Hidrolandia -> Ribeirao Pires
C-2026-000677  05/09  cav=FFA2I61  car=QXA9H76   Amparo      -> Brasilia      <- esta
C-2026-000684  06/09  cav=FFA2I61  car=TZB1D35   Uberlandia  -> Brasilia      <- carreta OUTRA
```

O cavalo trocou de carreta. A regra de producao casa **qualquer placa contra qualquer
papel**, entao o manifesto novo do CAVALO fechou a carga da CARRETA que ficou.

**Onde a carga esta:** a carreta `QXA9H76` saiu de Amparo em 05/09 22:01, rodou a noite
inteira pelo interior de SP, dormiu em Aramina e chegou a **Uberlandia em 06/09 11:06** —
onde esta parada ate 08/09 14:49, a **347 km de Brasilia** (421 km de rota). Rodou **434 km
depois de ser marcada como entregue**, e nao tem manifesto novo: continua com esta carga.

O cavalo `FFA2I61` nao tem rastreador (e um dos 73% sem GPS) — por isso nao aparece.

#### A hipotese da reemissao, testada

Gabriel levantou: *"nao pode ter sido emissao de manifesto errado, cancelaram e reemitiram?"*
Boa hipotese, e o dado a **refuta**:

| | C-2026-000677 | C-2026-000684 |
|---|---|---|
| cliente | **QUIMICA AMPARO LTDA CD01** | **PERNOD RICARD BRASIL** |
| origem | Amparo | Uberlandia |
| manifesto | UDI029121-8 | UDI029131-5 |

Clientes diferentes, origens diferentes, manifestos diferentes: sao **duas cargas distintas**
que por acaso vao as duas para Brasilia. Nao ha nenhuma outra carga da Quimica Amparo para
Brasilia em setembro, e **zero cargas canceladas** no mes inteiro. O motorista e o mesmo nas
tres (MARLEY CORDEIRO DA ROCHA), o que e coerente com "o motorista trocou de carreta", nao
com "reemitiram o documento".

> **Limite do que da para afirmar:** a tabela `manifestos` do SSW no dump local vai so ate
> **31/03/2026**. Entao posso afirmar que a C-684 nao e reemissao da C-677; **nao** posso
> verificar se o proprio manifesto UDI029121-8 foi cancelado e reemitido dentro do SSW. Isso
> exige dump novo da tabela — a mesma dependencia da secao 15.3.

#### O que o sistema ja acertava

O aferidor achou sozinho, e com o diagnostico exato:

```
C-2026-000677 · F5 · ENTREGUE mas a carreta nunca chegou (346 km) e so o CAVALO
pegou carga nova — e desengate, nao entrega; proxima do cavalo = C-2026-000684
```

E a regra do branch **nao fecharia**: `_chegou_ao_destino(C-677) = False`, e existe teste
cobrindo exatamente este padrao (*"cavalo igual + carreta diferente NAO fecha"*). Ela esta
atras de `EMBARQUES_MODELO_CARRETA=false` **e** producao roda a imagem de 02/09 (21.14).

#### O conserto: posicao ao vivo em carga fechada SEM PROVA

O card so buscava posicao ao vivo em carga **aberta** — tratava "fechada" como "chegou", e a
secao 4.2 separa as duas coisas. Congelar faz sentido para entrega provada; para fechamento
sem prova, esconde justamente o que importa.

**Mas o conserto ingenuo seria pior que o defeito.** Medido antes de escrever:

```
51  cargas fechadas sem prova de chegada
47  a CARRETA ja comecou outra viagem   <- posicao ao vivo seria de OUTRA viagem
 4  a carreta NAO comecou outra viagem  <- a posicao ao vivo E desta carga
```

Sem **teto documental** o conserto erraria em 47 de 51. Com ele, sobram 4 — e sao a classe
mais urgente que existe: carga parada em algum lugar, carimbada de entregue. Efeito na C-677:

```
posicao   Pirassununga/SP 06/09  ->  Uberlandia/MG 08/09
km falta          823,8 km       ->        421,2 km
```

Controles conferidos: carga aberta, entregue COM prova, e fechada cuja carreta ja saiu em
outra viagem — as tres **inalteradas**. E o card leva rotulo (`⚠ depois do fechamento`),
porque a linha do mapa termina na conclusao e o card e de hoje: sem aviso, o operacional le
as duas como a mesma afirmacao.

> Um bug meu no caminho: usei `_pn('%s')` para normalizar a placa dentro do SQL, e o helper
> **repete a coluna quatro vezes** — quatro placeholders para um valor so ("tuple index out
> of range"). Trocado por `placas.grafias()`, que e o padrao do arquivo.

#### O conserto criou um segundo problema, e o Gabriel viu na tela

Mostrar so a POSICAO de agora deixou o desenho **pior num aspecto**: o icone aparecia em
Uberlandia **sem rastro nenhum ate la**, porque a linha do trajeto morre na `data_conclusao`.
A rota planejada (azul) passa por Uberlandia por coincidencia, e o olho lia "teleporte" — foi
literalmente por isso que ele perguntou se eu tinha reconstruido por documentacao.

Nao tinha: os **119 pontos de GPS existiam no banco desde sempre**, ninguem os pedia.

```
posicoes da carreta QXA9H76 desde 05/09 22:00
   antes da conclusao (o que o mapa desenhava) ....  24
   depois dela (existiam, fora da linha) .......... 119
```

Entao o trecho posterior passa a vir junto, como **segunda linha**, laranja pontilhada, com
legenda propria. Nao se mistura com a primeira de proposito: uma e *"a viagem como foi
registrada"*, a outra e *"o que aconteceu depois que disseram que ela acabou"*. Mesma condicao
estreita: fechada sem prova E carreta ainda nesta carga.

```
C-2026-000677    linha 60 pontos   +   pos-fechamento 119   -> Uberlandia 08/09
C-2026-000609                          pos-fechamento  12
C-2026-000644                          pos-fechamento 127
C-2026-000662                          pos-fechamento 151
controles (aberta · entregue com prova · carreta ja saiu)  ->  0, inalterados
```

> **A licao que sobra e sobre a tela, nao sobre o motor:** a linha do mapa e a evidencia
> MENOS confiavel quando o fechamento esta errado, porque ela herda o erro em silencio —
> 119 de 143 pontos ficaram invisiveis sem nenhum aviso. Os KPIs e o card denunciam; a linha
> esconde. Numero errado se discute; desenho errado convence.

### 21.21 A C-2026-000648 — saída fabricada e janela degenerada (09/09/2026)

Segundo caso que o Gabriel achou olhando a tela. A carga aparecia `Entregue`, rota de 899 km
(Canapolis -> Juiz de Fora) e KPIs de **34,2 km · 30 min em movimento · 0 min parado**.

**A cadeia inteira:**

1. Producao fechou em **04/09 00:00** — um dia depois do carregamento e **antes** de qualquer
   saida. Conclusao anterior a saida e impossivel.
2. O robo detectou a impossibilidade e a regra de coerencia fixou a conclusao **no instante da
   saida**. Ficou coerente e ficou inutil: a viagem passou a ter **duracao zero**, e a tela
   publicou o que sobrou da folga pre-origem como se fosse a viagem.
3. **E a propria saida era falsa.** A carreta `TZC9F36` nunca parou em Canapolis — passou a
   **6,6 km** as 01:55, a 74 km/h, a caminho de outro lugar.
4. Aquele "outro lugar" e a **C-2026-000656** — outra carga da mesma carreta (04/09,
   Hidrolandia -> Duque de Caxias, cliente MARTINS). O GPS confirma: Hidrolandia ->
   Uberlandia -> Ribeirao Preto -> Limeira -> **Nova Iguacu/RJ** em 07/09.
5. Os "110 km de Juiz de Fora" que o aferidor reportava sao coincidencia geografica: Duque de
   Caxias fica a ~110 km de la.

**A viagem Canapolis -> Juiz de Fora nao aconteceu com esta carreta.** O manifesto existe; o
GPS conta outra historia.

#### O que eu propus, e por que retirei metade

A primeira proposta foi **exigir parada na origem**, simetrica ao conserto do anel. A analise
antes de implementar (a pedido do Gabriel) derrubou:

* **escopo menor do que eu dissera:** sobre a janela real do robo sao **2 cargas**, nao as que
  eu contara numa janela de 36 h — a C-2026-000521 sai porque para perto de Amparo em outro
  momento da janela;
* **nao consertaria o caso:** o motor grava a saida so quando o campo esta vazio
  (`if n_saida and not dsaida`) e **nunca apaga**. A mudanca seria prospectiva, e a saida
  falsa da C-648 continuaria na tela. Consertar de verdade exigiria uma regra de APAGAR
  saida — destrutiva, no campo com o melhor historico do sistema (11 correcoes contra 167 da
  chegada);
* **removeria o piso da chegada:** `piso = max(saida, saida_gravada) or t_org`. Exigir parada
  zera o `t_org` dessas cargas e a busca de chegada fica sem limite inferior — risco de
  *criar* chegada espuria. Trocar risco de regressao por ganho de 2 casos e mau negocio.

**No lugar, uma invariante do aferidor.** Nao destroi nada, pega o caso, risco zero:

```
C7 · SAIDA FABRICADA: a placa passou a 6,6 km da origem mas NUNCA PAROU la —
     quem carrega, para. A viagem desenhada pode ser de outra carga
```

#### A trava de janela degenerada — e o criterio que a medicao corrigiu

Eu ia travar por "janela de duracao zero". **Errado.** O tamanho da janela nao discrimina:

```
C-2026-000680   janela 1,2 h   rota 114 km   chegada PROVADA   <- viagem curta de verdade
C-2026-000677   janela 1,1 h   rota 959 km   sem chegada       <- conclusao fabricada
```

Janelas quase identicas, significados opostos: **o que separa e a prova de chegada.**

Criterio final — *fechada SEM prova de chegada E janela < 5% do tempo cabivel* — implementado
como a **gemea simetrica** do `_kpi_plausibilidade`, que ja barrava janela LONGA demais (3x o
cabivel, 24 cargas). Barra 5, e o valor cru fica em `_bruto` com o motivo no tooltip:

```
C-2026-000084   0,0 h    982 km   exibia 1.657,2 km   <- pior que o caso original
C-2026-000427   0,0 h    601 km   exibia   291,7 km
C-2026-000559   0,0 h     24 km   exibia     2,4 km
C-2026-000648   0,0 h    899 km   exibia    34,2 km   <- o do Gabriel
C-2026-000677   1,1 h    959 km   exibia   101,3 km
```

Controles conferidos e **intocados**: C-680 (1,2 h com chegada), C-659 (5,7 h com chegada) e
C-665 (entrega normal).

> **A licao de metodo:** eu tinha os dois consertos prontos na cabeca e os dois estavam
> errados — um no escopo e no mecanismo, o outro no criterio. Os dois so ficaram certos
> depois de medir. "Analisa antes de implementar para ter certeza" pagou duas vezes na mesma
> tarde.

### 21.22 O recorte pré-origem ancorava na passagem errada (09/09/2026)

Terceiro caso da tela, e o mais sutil. A `C-2026-000630` (Resende -> Extrema, rota de
317 km) exibia **568 km percorridos e 601 km de odômetro** — 1,79x a rota — com a linha
começando 33 h antes da saída e incluindo uma ida e volta a Duque de Caxias.

**A causa:** o `indice_saida_origem` ancora no PRIMEIRO bloco contíguo dentro de 30 km da
origem. Existiam dois:

```
bloco 0   01/09 17:50 -> 18:50    13 pts   parada    0 min   min 3,0 km   <- ancorava AQUI
          ...entre os blocos afastou-se 125 km...
bloco 1   02/09 10:42 -> 21:10    62 pts   parada  470 min   min 2,0 km   <- era AQUI
saida gravada: 02/09 21:15
```

O bloco 0 e o caminhao **saindo de Resende no dia anterior, em outra viagem**, a 66-87 km/h.
O bloco 1 e o carregamento: 10 horas de patio terminando 5 minutos antes da saida.

> A ancora no primeiro bloco **existe de proposito** — a docstring registra que ela impede
> que uma viagem que VOLTA pra base (origem->destino->origem) tenha o recorte puxado pro
> ponto da volta, o que zerava o trajeto. Ela so nao previa que o primeiro bloco pudesse ser
> uma PASSAGEM, e nao uma estadia.

#### A regra nova, e o que cada palavra dela custou

> **Âncora = o ÚLTIMO bloco com PARADA SUSTENTADA (>=60 min) que começa até a saída
> registrada.** Sem nenhum, cai no comportamento antigo.

* **parada sustentada**, nao "algum ponto parado": a primeira medicao deu 6 cargas e **nao
  pegou a C-630**, porque o bloco 0 dela tem UM ping a <=3 km/h (um pedagio). Com o
  discriminador certo foram 11. Blocos errados tem **0 a 5 min** de parada; blocos de
  carregamento tem **167 a 4.471 min** — duas ordens de grandeza, entao o limiar de 60 min
  nao e delicado.
* **até a saída registrada**: sem esse teto a regra QUEBRA a `C-2026-000376`, cujo bloco com
  parada longa comeca **dois dias depois** da saida (e o caminhao voltando e estacionando
  48 h). Ancorar ali cortaria a viagem inteira. Foi a minha primeira versao, e a medicao a
  derrubou antes de virar codigo.
* **último**, nao primeiro, porque ha dois formatos e os dois resolvem certo:

```
C-539 / C-502 / C-464   entre blocos afastou-se 32-47 km   -> manobra: nunca saiu do patio
C-630                   entre blocos afastou-se  125 km    -> saiu e voltou (viagem anterior)
```

#### O resultado

A razao km/rota **colapsou sobre a rota** — que e onde uma viagem real tem de ficar (um pouco
abaixo, porque o haversine corta curva e subestima 3 a 5%, secao 12.1):

```
carga              antes    depois     rota   razao
C-2026-000630      568,0     295,1    317,4   1,79x -> 0,93x
C-2026-000531      575,9     296,6    317,4   1,81x -> 0,93x
C-2026-000501      547,0     295,6    317,4   1,72x -> 0,93x
C-2026-000534     2141,9    1313,4   1546,9   1,38x -> 0,85x
C-2026-000611     1219,6     971,9    931,0   1,31x -> 1,04x
C-2026-000599     1073,9     911,4    931,0   1,15x -> 0,98x
C-2026-000672     1106,2     929,6   1026,4   1,08x -> 0,91x
C-2026-000547     1000,2     941,8    923,3   1,08x -> 1,02x
C-2026-000490     3096,0    3024,4   3133,9   0,99x -> 0,97x
+ C-539, C-502, C-464 (que a regra do "ultimo bloco" descobriu e a do "primeiro" nao via)
```

**12 consertos, 0 regressoes.** Os 4 do fallback ficaram identicos, inclusive a
`C-2026-000521` nos mesmos 978,8 km. Aferidor 176/234 inalterado, 15 de 15 testes.

#### Uma régua, dois chamadores

`indice_saida_origem` e chamada pelo mapa (`server.py`) **e pelo worker**, que grava o KPI no
banco. Mudar so um criaria duas reguas — exatamente o defeito que esta seção inteira combate.
Os dois passaram a mandar velocidade, instante e o teto; sem esses parametros a funcao se
comporta como antes, entao a compatibilidade fica preservada para qualquer chamador futuro.

> **O resíduo declarado:** a `C-2026-000521` nao e consertada porque o bloco certo dela
> comeca 10 min DEPOIS da saida gravada — e a saida dela e ela mesma fabricada (a placa
> passou a 28 km de Amparo sem parar). Consertar ali exigiria confiar numa saida que eu sei
> estar errada. Dano de 1,06x a rota; fica como esta, e o mecanismo ja tem codigo proprio
> no aferidor (`C7`).

---

## 22. PRODUÇÃO — o deploy de 09/09/2026 e o que ficou pela metade

> **Leia isto primeiro se você está retomando.** Em 09/09/2026 o pacote de correções foi
> para produção e **funcionou**: os fechamentos errados caíram 46% na gravidade alta e sete
> classes de defeito foram a zero. Depois disso as **109 pernas vazias** foram criadas lá — e
> a sessão acabou (limite de contexto) **antes de terminar o pipeline delas**. Produção está
> **correta, porém inacabada**: as pernas existem sem rota traçada e sem rederivação, e por
> isso a tela mostra "viagens" de 14 a 24 dias sem linha no mapa. Nada está corrompido; falta
> rodar três comandos, e eles estão na §22.7.

### 22.1 O placar da Fase C — medido em produção, não no laboratório

Mesma régua de sempre (`_auditoria_geral.py`, janela 01/08 a 09/09), contra a linha de base
tirada antes do deploy:

```
                              antes   depois
achados                         510  ->  352      -31%
cargas com achado          257 (89%)  -> 231 (80%)
gravidade ALTA                   92  ->   50      -46%
gravidade MEDIA                 209  ->   93      -55%
gravidade BAIXA                 209  ->  209        0   (hardware; ninguem conserta por software)
```

Sete classes **zeradas**:

```
C3  chegou e a chegada nao foi gravada    47  ->  0
C1  saiu e a saida nao foi gravada        21  ->  0
T2  conclusao anterior a chegada          19  ->  0
C6  chegada com o veiculo em MOVIMENTO    10  ->  0
F4  chegou e nao fechou                    8  ->  0
T3  conclusao anterior a saida             4  ->  0
T5  velocidade implicita impossivel        1  ->  0

F3  recorte da conclusao errado           71  -> 41
F2  FECHADA CEDO                          23  ->  3
```

O **F2 caindo de 23 para 3 é a medida direta do estrago** que o `baixa_ctrb` e o `timeout`
faziam — regras que produção rodava desde 02/09 e que o commit `76f07a3` (04/09) já havia
removido do código sem nunca chegar à imagem no ar (§21.14). Vinte cargas estavam com
conclusão anterior à chegada real, uma delas por **9,2 dias**.

O **C7 subiu de 2 para 4, e isso não é regressão**: com as saídas corrigidas, a invariante
enxerga dois casos que antes estavam encobertos por outro defeito.

**Convergência em produção: 225 → 8 → 0 → 0.** As oito da segunda passada eram o bug do `or`
(§22.2).

Os motivos de fechamento como ficaram (a coluna é histórica — `baixa_ctrb` e
`sequencia_viagem` **não existem mais no código**, então nenhum novo pode surgir):

```
gps_saiu_do_destino     83        (vazio)                43
baixa_ctrb              68  <-h   gps_dwell_destino      14
manifesto_novo          65        sequencia_viagem       10  <-h
sem_prova_revisar        4        manifesto_novo_carreta  3
```

**O que sobra na gravidade alta não se move por GPS:** V1 (20 — placa rastreada nunca esteve
na origem), F5 (8 — desengate lido como entrega, a família da C-2026-000677 da §21.20), C7
(4), F2 (3), C2 (3), F1 (3), S1 (9 — cega, sem sensor). Isso é **documento errado** e
**desengate**, e o conserto é a chave `EMBARQUES_MODELO_CARRETA` — a Fase D.

### 22.2 Três coisas quebraram no deploy, e duas eram legíveis daqui

O Gabriel avisou antes: *"eu queria subir pronto localmente, mas você teimou"*. Ele estava
certo, e o registro fica aqui como regra de método.

| o que quebrou | dava para achar local? |
|---|---|
| **a coluna `no_local_fonte` não existia em produção** | **SIM.** `_robo_atemporal.py:75` faz `SELECT c.no_local_fonte`, e **nenhum** dos scripts (`_robo_atemporal`, `_auditoria_geral`, `_rederivar_vazias`) chama `garantir_colunas`. Essa DDL só roda no `main()` do `embarques_auto.py` (linha 1174). Em qualquer base que ainda não tenha visto o robô diário do dia 09/09, o robô atemporal morre na primeira query. **Continua assim — ver §22.8, item 1.** |
| **o traçador de rotas ignora a perna vazia** | **SIM.** `tracar_rotas_pendentes` filtra `status NOT IN ('Entregue','Cancelada')` e a perna nasce `'Entregue'`. Eu afirmei ao Gabriel que "o robô diário traça amanhã" — errado, e o código diz isso em uma linha. Corrigido no commit `1666047`. |
| **o `or` da coerência temporal oscilava** | **NÃO.** Esse precisava da base suja: local já tinha sido limpa em tantas passadas que a combinação (sem chegada derivada + chegada gravada + conclusão documental anterior) não existia mais. Commit `4de2d8f`. Produção é o melhor teste — mas é o melhor teste **do que não dá para simular**, não desculpa para os outros dois. |

> **Regra:** o pipeline inteiro roda local antes de subir — inclusive os scripts avulsos,
> inclusive contra uma base que não foi limpa por eles. Subir e descobrir é caro porque cada
> descoberta custa um ciclo de build+push+`service update` e um pedaço da atenção do Gabriel.

### 22.3 As 110 pernas vazias em produção

Produção tinha **zero** cargas com `viagem_vazia = TRUE` (conferido antes de qualquer
escrita): as pernas nunca tinham sido criadas lá, só na base local. Nenhum risco de
duplicata, nenhum `C-` com `viagem_vazia` lançado à mão para atropelar.

O gerador foi endurecido antes de rodar em produção (commit `b314989`):

* janela cravada em agosto → `--desde` / `--ate`;
* o `DELETE FROM embarques_cargas WHERE numero LIKE 'V-2026-%'` ficou **atrás de `--refazer`**.
  Local era inofensivo (criar do zero); em produção destrói o `embarques_cargas_log` das
  pernas e **troca o número** de cada uma, que é a identidade que o operacional vê na tela;
* **guarda anti-duplicata**: sem `--refazer`, aborta se já houver perna na janela. Nada no
  banco impede a duplicata — a chave única é `manifesto_origem`, e perna vazia não tem
  manifesto (§20.2). Testado: com 63 pernas locais, abortou e não tocou em nada.

O `numero C-{ano}-{id}` do lançamento manual (`server.py:5240`) garante que o prefixo `V-` é
exclusivo do gerador — uma perna vazia lançada à mão teria `C-` e ficaria fora do alcance.

**Resultado: 110 pernas** na janela 01/08 → 09/09 (a base local tinha 63, mas só de agosto).

> ⚠ **Correção — o que eu escrevi aqui de manhã estava errado em três pontos.** Eu montei
> esta seção a partir da conversa, e o banco de produção contou outra história quando foi
> consultado (§22.9): o traçador **já tinha rodado** (108 das 110 com rota, 110
> geocodificadas) e a rederivação **já tinha sido aplicada** às 17:27, com 24 escritas. O que
> de fato faltava era a **convergência conjunta** — essa sim nunca rodou, porque a variável
> `$CT` estava velha e as três passadas não executaram nada (§22.5). **Lição:** o estado de
> produção se lê no banco, não na transcrição da sessão.

**O que faltava nelas, de verdade:** a convergência conjunta — e, escondido atrás dela, o
defeito do rótulo (§22.9), que só apareceu quando os números foram conferidos um a um.

### 22.4 A perna vazia mistura duas coisas — e é isso que parece "viagem bugada"

O Gabriel olhou a tela e disse *"muita viagem bugada"*. Não é bug de gravação; é o que a
perna vazia **é** hoje. Medido na base local (63 pernas, que já passaram por rederivação):

```
faixa de janela        n    km medio   km total
a) < 24h              19        224       4.256
b) 1-3 dias           20        494       9.880
c) 3-7 dias            7        642       4.491
d) > 7 dias           17        984      16.731   <- 27% das pernas, 47% do km
```

```
pernas limpas (reposicionamento real)  47 pernas   23.599 km
rotuladas LACUNA NAO DOCUMENTADA       16 pernas   11.760 km   (33% do km vazio)
```

A pior tem **24 dias para 422 km de rota**. Isso não é reposicionamento: é intervalo em que
a carreta não teve carga nossa (provável Carreteiro), e o `_rederivar_vazias.py` **já sabe
disso** — é ele quem carimba `LACUNA NAO DOCUMENTADA` na observação.

> **Em produção esse carimbo não existe ainda**, porque a rederivação não rodou. Por isso a
> tela mostra uma "viagem" de 24 dias, sem rota, sem cliente e sem explicação. Rodar o passo
> 2 da §22.7 já muda a leitura da tela sem apagar nada.

**Consequência para o número de negócio da §6:** o "km vazio" bruto **superestima**. O número
que vale para prospecção de frete de retorno é o das **pernas limpas**; a lacuna não
documentada é outro assunto (e provavelmente outra conversa comercial). Separar as duas na
tela — filtro ou coluna — é decisão do Gabriel, não do robô.

### 22.5 Armadilhas do dia

* **`$CT` envelhece.** `docker service update --force` **recria o container** com id novo. Uma
  variável guardada antes disso quebra com `Error response from daemon: container ... is not
  running` — e num `for` isso é silencioso, porque o `grep GRAVADO` não acha nada e o laço
  segue como se tivesse rodado. **Re-resolva o id imediatamente antes de cada bloco:**
  `CT=$(docker ps -q --filter "name=rizza-auditoria")`.
* **A perna nasce `'Entregue'`** — porque ela já aconteceu. Isso a tira do traçador do robô
  diário (§22.2) e de qualquer varredura que filtre por carga ativa.
* **O gerador não geocodifica.** Ele insere sem `origem_latitude`; quem preenche é o passo 1
  do `_tracar_rotas_agosto.py`. Rodar a rederivação antes do traçador funciona, mas a perna
  fica sem mapa até o traçador passar.
* **O ORS tem dois limites e a mesma mensagem para os dois** (§12.12): 2,6 s entre chamadas e
  backoff de 70/140/210 s no 403. ~119 rotas ≈ 6 minutos. **Não interrompa** ao ver 403.

### 22.6 O estado exato de produção (atualizado em 10/09/2026)

| item | estado |
|---|---|
| `git push origin main` | **feito** — `origin/main..main` vazio; HEAD é `1666047` |
| imagem no ar | `46818ab` — conferida DENTRO do container, não presumida (§22.9) |
| motor sem `baixa_ctrb` / `timeout` | **no ar** — provado pelo placar da §22.1 |
| convergência do robô atemporal | **atingida** (8 → 0 → 0), *antes* de as pernas entrarem |
| pernas vazias | **110 criadas** (`V-2026-000001` a `V-2026-000110`) |
| rotas ORS das pernas | **110/110 traçadas** — 108 em 09/09, as 2 últimas em 10/09 |
| convergência conjunta (motor + pernas) | ✅ **fechada em 10/09**: pernas 24 → 0 → 0, motor 26 → 0 → 0 |
| `EMBARQUES_AUTO_JANELA_DIAS` | **já estava 5** — a §13.3 dizia "hoje está 1" e estava desatualizada |
| robô diário com o código novo | ainda não rodou. Em 09/09 quem escreveu foi o `Robo atemporal` (718 escritas, última 17:19). A primeira rodada do **diário** com o código novo é a das 16:30 de **10/09** |
| coluna `no_local_fonte` | existe (criada pelo robô diário); os scripts continuam **sem** a DDL |

### 22.7 A sequência que fechou o pipeline (executada em 10/09 — fica como receita)

```bash
# --- no servidor
cd /opt/stacks/rizza-auditoria
git pull origin main
git log --oneline -1                     # tem de mostrar 1666047 fix(rotas)

docker build -t ghcr.io/ggabrielmilho-web/rizza-auditoria:latest .
docker push ghcr.io/ggabrielmilho-web/rizza-auditoria:latest
docker service update --force rizza-auditoria_app

# --- SEMPRE re-resolver o id DEPOIS do service update
CT=$(docker ps -q --filter "name=rizza-auditoria")
docker exec $CT ls embarques_regua.py                                          # imagem nova?
docker exec $CT grep -c "encerrar(cur, cid, 'baixa_ctrb')" embarques_auto.py   # tem de dar 0

# 1) tracar as rotas (~119 rotas x 2,6s = ~6 min; 403 = rajada, ele espera sozinho)
docker exec $CT python -X utf8 _tracar_rotas_agosto.py \
       --desde 2026-08-01 --ate 2026-09-09 2>&1 | tail -20

# 2) rederivar as pernas — da janela coerente e carimba a LACUNA NAO DOCUMENTADA
docker exec $CT python -X utf8 _rederivar_vazias.py \
       --desde 2026-08-01 --ate 2026-09-09 --aplicar 2>&1 | tail -12

# 3) convergencia CONJUNTA — os dois tem de zerar. Oscilou? e bug: pare e conserte.
CT=$(docker ps -q --filter "name=rizza-auditoria")
for i in 1 2 3; do
  echo "--- passada $i"
  docker exec $CT python -X utf8 _robo_atemporal.py   --desde 2026-08-01 --ate 2026-09-09 --csv /tmp/w$i.csv  --aplicar 2>&1 | grep GRAVADO
  docker exec $CT python -X utf8 _rederivar_vazias.py --desde 2026-08-01 --ate 2026-09-09 --csv /tmp/wr$i.csv --aplicar 2>&1 | grep GRAVADO
done

# 4) a janela do robo diario: 1 -> 5 (§13.3). CUIDADO: mexer na env pelo Portainer faz o
#    servico VOLTAR para a imagem antiga (armadilha de 21/08). Pela CLI:
docker service update --env-add EMBARQUES_AUTO_JANELA_DIAS=5 rizza-auditoria_app

# 5) o aferidor de novo — e a medicao que fecha o assunto das pernas
CT=$(docker ps -q --filter "name=rizza-auditoria")
docker exec $CT python -X utf8 _auditoria_geral.py --desde 2026-08-01 --ate 2026-09-09 2>&1 | head -28
```

**O que esperar no passo 5:** o `D1 — sem rota planejada` tem de voltar para perto de zero
(era 10 antes das pernas; com 109 pernas sem rota ele explode e depois volta). As classes
altas (V1, F5, S1) **não se movem** — elas dependem da Fase D.

E em **10/09 depois das 16:30**, rodar o aferidor mais uma vez: é a primeira rodada do robô
**diário** com o código novo, e ninguém viu ainda como ele abre e fecha.

### 22.8 O que fica em aberto

1. **A DDL nos scripts avulsos.** `_robo_atemporal.py` lê `no_local_fonte` sem garantir que a
   coluna exista. Hoje funciona só porque o robô diário passou antes. O conserto é uma linha
   (`from embarques_auto import garantir_colunas`, chamada logo após o `connect` com o
   `commit`), mas é código que roda em produção — **precisa do "pode fazer" do Gabriel**.
2. **Separar reposicionamento de lacuna na tela** (§22.4) — decisão de negócio: filtro,
   coluna, ou tipo de operação próprio.
3. **Fase D — `EMBARQUES_MODELO_CARRETA`.** É o que ataca os 20 V1 e os 8 F5, hoje **56% da
   gravidade alta**. A chave está no ar em `FALSE`, com 4 gates e o bloco 0 do
   `_testar_regras_fechamento.py` provando que nada dela executa.
4. **As pernas fora de 01/08 → 09/09.** A janela usada é a janela auditada; ampliar é decisão
   separada, e o gerador agora aceita qualquer uma.

### 22.9 O fecho do pipeline — e o rótulo que só sabia empilhar (09-10/09/2026)

O que faltava era rodar a convergência conjunta. Ao conferir os números antes de gravar,
apareceu um defeito que estava a **um `--aplicar` de estragar 18 pernas**.

#### O estado real, lido no banco

A primeira coisa foi consultar produção em vez de confiar na transcrição:

```
vazias = 110  (V-2026-000001 a V-2026-000110)  | ja com LACUNA = 24
com rota = 108 | geocodificadas = 110 | vazias que nao sao V-: nenhuma
quem ja escreveu nas pernas:  Robo atemporal 206 · Rederivacao de vazias 24  (ultimo 17:27)
```

Ou seja: o traçador e a rederivação **já tinham rodado** em 09/09. A §22.3 dizia o contrário
e foi corrigida. **O estado de produção se lê no banco.**

#### O defeito: o rótulo só sabia ser acrescentado

```python
if rot not in (obs or ''):
    campos['observacoes'] = ((obs + ' | ') if obs else '') + rot
```

Dois furos na mesma linha, e os dois vieram à tona no mesmo dia:

1. **o texto embute os números** (`{horas/24:.1f} dias para {dist:.0f} km`). Qualquer mudança
   no número gera um rótulo *diferente*, o `not in` deixa passar, e a perna termina com dois
   rótulos contraditórios grudados;
2. **rótulo nunca saía.** A régua da lacuna divide a janela pela distância da rota —
   `cabivel = dist/600*24 + 24` — e com `dist` nulo ela desaba para 24 h, ficando **3x mais
   severa**.

E o `dist` estava nulo porque **eu mandei rederivar antes de traçar**. Ordem errada, minha:

> **Traçar vem antes de rederivar.** A rota não é enfeite do mapa: ela é o denominador da
> régua de plausibilidade. Rederivar sem rota é julgar com um critério três vezes mais duro.

A medição, perna a perna, das 24 rotuladas:

```
20 ainda valiam pela regua de hoje   |  4 eram rotulo VELHO (V-20, V-42, V-105, V-108)
mas so 2 tinham o texto EXATO que o script escreveria agora
-> aplicar como estava poria um SEGUNDO rotulo em 18 pernas
```

#### O conserto (`46818ab`)

O script passou a **desmontar e remontar** a observação: split por `' | '`, descarta todo
trecho que comece com `LACUNA NAO DOCUMENTADA`, e recompõe com o rótulo de hoje **se** ainda
for lacuna. Split em vez de corte no fim do texto porque hoje o rótulo é o último trecho, mas
nada garante que continue sendo.

Isso devolve a **convergência**, que é a regra do modelo (§5): rodar duas vezes dá zero.

Testado na base local nos três caminhos, antes de subir:

```
nao-regressao          59 coerentes / 16 rotuladas / 4 sobrepostas, 0 gravacoes
rotulo velho           detectado, retirado, observacao voltou BYTE A BYTE ao original
numeros desatualizados substituido — a perna ficou com UM rotulo, com o km da rota atual
convergencia           a passada seguinte deu 0 nas tres vezes
```

#### O erro que custou uma rodada: aplicar sem conferir a imagem

Com o conserto commitado, eu mandei aplicar em produção. Resultado:

```
pernas rotuladas: 24 | com rotulo dobrado: 20
```

**Exatamente o estrago que o conserto existia para evitar** — porque o container ainda rodava
a imagem anterior. O sinal estava na própria saída e eu não olhei: o contador
`rotulo VELHO retirado` **não apareceu em nenhuma passada**, e ele só existe no código novo.

> É a §21.14 se repetindo, e agora vira regra dura: **antes de rodar um script que depende de
> um conserto, prove que o conserto está DENTRO do container** —
> `docker exec $CT grep -c "<trecho novo>" <arquivo>` tem de dar 1. O `docker service update`
> não é prova; a linha nova dentro do container é.

O dano foi só em `observacoes` e o próprio conserto o desfez: com a imagem certa, a passada
seguinte gravou **24 pernas** (os 20 pares colapsados em um rótulo + as 4 velhas limpas) e
zerou.

#### O estado final, medido

```
pernas vazias                 110   |  com rota   110/110
rotuladas como lacuna          20   |  dobradas   0
rederivacao (3 passadas)   24 -> 0 -> 0
robo atemporal (3 passadas) 0 ->  0 -> 0     (tinha zerado 26 -> 0 -> 0 na rodada anterior)
EMBARQUES_AUTO_JANELA_DIAS      5   |  EMBARQUES_MODELO_CARRETA ausente = false
```

**Produção convergida, pela primeira vez com as pernas vazias dentro da base.** O que sobra
está na §22.8, e a estreia do robô **diário** com o código novo é a rodada das 16:30 de
10/09 — ninguém viu ainda como ele abre e fecha.

### 22.10 A carga nasce já em viagem — e o robô atemporal passa a rodar sozinho (10/09/2026)

O Gabriel olhou a tela depois da rodada das 16:30 e disse: *"no geral rodou muito bem, o
problema é só esse rótulo aí que está bugado"*. O rótulo era o mensageiro.

#### O que a tela mostrava

Onze cargas `Aberta` com o subrótulo **"📍 placa longe"**, cujo tooltip acusa *"provável
carreta errada no documento"*. Medindo placa por placa, com a distância até a **origem** e
até o **destino**:

```
C-2026-000796   nao saiu    896.8 da origem      1.6 km do DESTINO
C-2026-000804   nao saiu    293.3                2.9
C-2026-000803   nao saiu   1032.9                6.1
C-2026-000800   nao saiu    670.3               13.2
C-2026-000797   nao saiu    773.9              172.1
C-2026-000799   nao saiu    541.1              249.7
C-2026-000795   nao saiu    294.8              560.4   <- meio do caminho
C-2026-000798   nao saiu    472.0              552.2   <- meio do caminho
+ 3 que JA TINHAM saida gravada e mesmo assim levaram o rotulo
```

Quatro caminhões **em cima do destino** e dois no meio da rota. **Nenhuma das onze era
carreta errada.** A `C-2026-000800` estava `Aberta` desde 04/09 tendo entregado no dia 07.

#### A causa: o evento acontece antes de a carga existir

O robô diário abre a carga às 16:30 a partir do manifesto do SSW, e o manifesto é **de
ontem**. Quando a carga nasce, o caminhão já saiu — às vezes já chegou. E o worker de
rastreamento **amostra ao vivo**: ele só grava a saída se estiver assistindo no instante em
que ela acontece. O evento já passou, ninguém viu, e a carga fica `Aberta` para sempre com a
placa longe da origem. É a §20.4, literalmente.

> **O defeito da régua do rótulo continua aberto** (§22.8): ela pergunta *"a placa está longe
> da origem?"* e nunca pergunta *"a carga já saiu?"*. Três das onze tinham saída gravada. O
> discriminador que separa os casos é a **distância até o destino** — carreta errada fica
> longe dos dois pontos; caminhão que saiu sem registro está indo para o destino.

#### O robô atemporal sabia consertar, e nunca era chamado

Ele relê o histórico de GPS e deriva saída, chegada e conclusão do que já aconteceu. Existia
desde 09/09 e **não estava em lugar nenhum do agendador** — nem thread, nem cron, nem import.
Rodava na mão, quando alguém lembrava. Rodado sobre as onze:

```
C-2026-000800   Aberta -> Entregue     saiu 05/09 12:00, chegou 07/09 09:20
C-2026-000796   Aberta -> No destino   saiu 08/09 20:10, chegou 09/09 14:09
C-2026-000804   Aberta -> No destino   saiu 09/09 09:13, chegou 09/09 16:09
C-795/797/798/803  Aberta -> Em rota
```

#### A janela termina HOJE, e isso não é detalhe

Medido no mesmo dia, com o mesmo motor:

```
--ate 2026-09-09   nada
--ate 2026-09-10   10 cargas, 9 delas com a saida faltando
```

A prova que resolve uma carga de 08/09 é um ponto de GPS **do dia seguinte**. Rodar com o
`--ate` no último dia que interessa é perguntar antes de a resposta existir.

#### O ciclo diário, agora com quatro passos

`server.rodar_pos_diario()` roda logo depois de `embarques_auto.executar()`, sempre — mesmo
se o diário falhou, porque a carga que ficou `Aberta` ontem não espera o manifesto de hoje.

```
1. motor    _robo_atemporal      corrige saida/chegada/conclusao        ate 3 passadas
2. pernas   _regerar_vazias      cria a viagem vazia dos intervalos     1
3. rotas    _tracar_rotas        traca no ORS o que nasceu sem rota     1
4. janela   _rederivar_vazias    rederiva a janela e rotula a lacuna    ate 3 passadas
```

Cada emenda da ordem tem um motivo que já custou uma rodada:

* **motor antes das pernas** — a perna deriva a janela das cargas vizinhas (§20.2); gerar
  antes é derivar de uma âncora que vai mudar em seguida;
* **rotas antes da rederivação** — a régua da lacuna divide a janela pela distância da rota, e
  com rota nula o critério fica 3x mais severo (§22.9);
* **o traçado precisa de passo próprio** — a perna nasce `Entregue` (ela já aconteceu) e o
  `tracar_rotas_pendentes` do robô diário exclui `Entregue`.

São **subprocessos, não import**: os três scripts têm `argparse` e código no topo, e refatorar
o que acabou de convergir em produção é procurar problema. Falha em qualquer um deles não
derruba a thread do diário — o pior caso é o dia ficar sem a releitura, e ela é idempotente.

Chaves: `EMBARQUES_ATEMPORAL=false` desliga sem deploy; `EMBARQUES_ATEMPORAL_DIAS` (padrão 40)
dimensiona a janela.

#### O gerador de pernas precisou virar idempotente para poder ser agendado

A guarda de 09/09 era de **janela inteira** ("se já existe perna nesta janela, aborta").
Protegia a execução manual; rodando todo dia, aborta sempre. E afrouxá-la sem trocar por outra
é o caminho da duplicata — nada no banco impede, porque a chave única é o `manifesto_origem` e
perna vazia não tem manifesto (§20.2).

> **A guarda agora é por PAR:** descarta a perna nova se já existe perna da **mesma carreta**,
> entre as **mesmas pontas**, com a janela **se sobrepondo**.
>
> * *sobreposição*, e não igualdade de instante, porque a rederivação **move** a janela da
>   perna a cada passada — comparar instante criaria uma perna nova a cada ajuste;
> * *e não só (carreta, pontas)*, porque a mesma carreta repete o mesmo trecho semanas depois,
>   e aquilo é outra perna, legítima.

Mais duas correções no mesmo arquivo: a **numeração continua** de onde parou (o `seq = 0`
recomeçava do 1 a cada rodada e colidiria com as pernas já na tela, que é a identidade que o
operacional vê), e a conta de duplicata saiu de dentro do `--aplicar` para que o **dry-run já
responda "criaria N, pularia M"**.

#### As medições

Base local, o ciclo inteiro de ponta a ponta:

```
motor  p1: 40 cargas   p2: 0
pernas p1: 3 viagens vazias criadas
rotas  p1: 49 rotas tracadas · 0 falhas
janela p1: 6 pernas     p2: 0
```

Idempotência do gerador, que era o risco: duas execuções seguidas → **pulou 100, criou 0**,
com 104 números distintos em 104 pernas.

E o ensaio em **produção**, antes de deixar rodar sozinho:

```
ja existiam (par com janela sobreposta)  113
candidatas                               113   (81 + 21 + 11)
a criar                                    0
```

Casou com todas. A partir de amanhã ele cria só as pernas das viagens que fecharem no dia.

#### Duas armadilhas de ambiente registradas hoje

* **O stack do Portainer diverge do serviço.** `docker service inspect` mostra
  `EMBARQUES_AUTO_JANELA_DIAS=5`, mas o arquivo do stack ainda diz `=1` — porque a mudança foi
  feita pela CLI (`--env-add`), e isso não volta para o arquivo. Não afeta nada agora; no dia
  em que alguém salvar aquele stack, a variável volta para 1 **e o serviço volta para a imagem
  antiga** (§ a armadilha de 21/08). Conferir com os dois `inspect` lado a lado, não com o
  Portainer.
* **A janela de disparo é 16:30–19:30 e o "já rodei hoje" vive na memória do processo.** Todo
  `service update` dentro dessa faixa faz o robô diário **disparar de novo**. É inofensivo (o
  índice único em `manifesto_origem` impede duplicar carga, e os quatro passos são
  idempotentes), mas explica log repetido.

#### O que isto deixa aberto

1. **A régua do rótulo `placa_longe`** — medir o resíduo depois de o atemporal rodar e trocar
   por: *já saiu* → em viagem · *não saiu + perto do destino* → saiu sem registro · *não saiu +
   longe dos dois* → placa longe (a suspeita V1 de verdade).
2. **Manifesto duplicado.** A `C-2026-000801` e a `C-2026-000802` são a mesma viagem física —
   mesmo cavalo (`OWH0F53`), mesma saída (08/09 17:21), mesma chegada (09/09 17:01), dois
   manifestos. O robô não enxerga manifesto cancelado.

---

## 23. O apagão de 30 h, a posição falsa e o ponto fixo conjunto (10/09/2026)

> **Comece por aqui se está retomando.** A sessão começou com quinze prints do mapa —
> "diversos bugs", "o robô atemporal está terrivelmente ruim" — e terminou em três defeitos
> que não tinham relação um com o outro, dois deles invisíveis para todo instrumento que
> existia. O maior: **produção passou 30 horas sem rastreamento nenhum e nada acusou**.

### 23.1 O que os quinze prints eram, de verdade

| o que parecia | o que era |
|---|---|
| traçado com buraco, ida-e-volta, salto | **posição falsa** do GPS, desenhada sem filtro (§23.3) |
| o robô atemporal decidindo mal | ele lia uma fita com teleporte dentro |
| "viagem duplicada" (C-801 × C-802) | item já conhecido — manifesto cancelado, §22.10 nº 2 |
| todos com "há 1d" na posição | **o apagão** (§23.2) — e esse era o único que importava |

O sinal que reordenou a investigação estava nos próprios prints: **quinze veículos
diferentes, nenhum com posição mais nova que um dia**. Nenhum conserto de desenho resolve
tela que não recebe presente.

### 23.2 O apagão — três fatores, e o terceiro é o que fez durar 30 h

A tabela `embarques_rastreio_dia` **nunca foi criada em produção**. O `init_db.py` a cria
desde o commit `7de4860`, mas ninguém o rodou lá depois. Então, a cada ciclo de 60 s:

```python
_persistir_posicoes(cur, posicoes)   # grava as 93 posições
_processar_cargas(cur)
if _deve_rodar_retencao():
    _consolidar_dias(cur)            # UndefinedTable  ->  levanta
    _purgar_posicoes_antigas(cur)
    _ultima_retencao = datetime.utcnow()
conn.commit()
except: conn.rollback()              # ...e leva as 93 posições junto
```

1. **a tabela não existia** → `_consolidar_dias` levantava;
2. **as três etapas dividiam uma transação** → o `rollback` descartava as posições já
   gravadas;
3. **`_ultima_retencao` era marcada DEPOIS da chamada que levantava** → nunca era marcada,
   `_deve_rodar_retencao()` seguia `True`, e **todo ciclo** repetia. Sem o item 3 teria sido
   um ciclo perdido por dia; com ele, 100% deles.

**Por que nenhum instrumento viu.** O log da 3S roda em **conexão separada**, então
`embarques_3s_log` mostrava 66 chamadas/hora, HTTP 200, **zero erros**, durante um dia e
meio. O `/api/rastreamento/health` conta os erros dessa tabela — dizia verde. A 3S estava
saudável o tempo todo: consultada direto, devolveu 93 placas, 40 com posição < 1 h, a mais
fresca de 35 segundos antes.

> **Regra que sai daqui:** a posição é **fato bruto** e não pode depender de nenhuma
> derivação nossa. Agora são três transações: a posição commita primeiro e sozinha,
> `_processar_cargas` falha por conta própria (uma carga ruim não derruba mais o GPS da
> frota), e a consolidação idem — com a **purga dentro do try dela**, porque purgar depois
> de a consolidação falhar é destruir o que ninguém guardou. `_ultima_retencao` passa a ser
> marcada mesmo na falha: o retry certo é amanhã, não em 60 s.

Teste em `_teste_ciclo_transacao.py`, conferido **nos dois sentidos** — código anterior dá
`AINDA QUEBRADO`, corrigido dá `CONSERTO PROVADO`. Teste que passa nas duas versões não
prova nada.

**A recuperação não perdeu dado:** o backfill (`/HistoricoPosicao`) rebusca o dia inteiro e
vem mais denso que o polling (100% × 81%), e o robô atemporal re-deriva os eventos. O dia
10/09 foi refeito em 18,5 min → 14.902 pontos em 71 placas, em linha com os vizinhos
(09/09: 16.889/72; 08/09: 17.486/75).

### 23.3 A posição falsa — o odômetro é o árbitro

O histórico contém pontos comprovadamente falsos, e nenhum consumidor os filtrava:

```
TYX9F52  07/09 07:37  Formosa   -> Jaborandi  257,5 km em 2,0 min  odo 37487 -> 37487
TYX9F52  07/09 07:57  Jaborandi -> Formosa    257,5 km em 2,0 min  odo 37487 -> 37487
HKE0321  08/09 14:30  Sta Luzia -> Serra      374,4 km em 3,5 min  odo 376904 -> 376904
```

Foi e voltou, centenas de km, em minutos, com o **odômetro congelado**. Ele é cumulativo no
aparelho e independente do GPS — 374 km rodados marcariam +374. É o árbitro que a §12.3 já
tinha eleito.

```
301 pares impossíveis em 34 placas · 235 com o odômetro negando
327 buracos LEGÍTIMOS (salto grande, velocidade plausível) — esses continuam passando
 74 de 334 mapas (22%) com pelo menos uma posição falsa desenhada
```

**O teste que decidiu:** não dá para saber *qual* dos dois pontos é o falso, então não se
escolhe — apenas não se soma a perna. Se a tese estiver certa, o km tem de convergir com o
odômetro, que é testemunha independente:

```
erro médio do km publicado contra o odômetro:  399,8%  ->  4,5%
C-2026-000486  cru 765,4  limpo  40,9  odo   41   -> 0,4%
C-2026-000495  cru 1405,4 limpo 914,3  odo  914   -> 0,0%
```

Os 4,5% que sobram são o **piso do instrumento**, não defeito: o próprio arquivo já
registrava que "a razão mediana é 1,046 — o haversine subestima, cortando curva".

A régua foi para `embarques_regua` (`perna_impossivel` / `cortes_do_trajeto` / `segmentos`),
onde servidor, robô diário, atemporal e aferidor já bebem. **Três consumidores**, e o
terceiro só apareceu porque o aceite foi medido pelo *endpoint* e não por um cálculo
paralelo:

1. **a linha do mapa** quebra no salto, com duas bolinhas âmbar marcando as pontas;
2. **o km do KPI** não soma a perna (a C-503 publicava 1.864 km com 718 km falsos dentro);
3. **o fallback do odômetro** — `"aparelho mudo → usa o GPS"` descrevia duas coisas com o
   mesmo teste: furo real (HKE0D21, 327 km em 12 h) e posição falsa (374 km em 3,5 min). A
   C-486 publicava `km_odometro` = 766 para uma viagem cujo odômetro andou 41. **O árbitro
   vinha contaminado pelo que devia julgar.**

> **O que a bolinha NÃO significa.** O km do trecho não se perde: quem perde é o haversine
> do GPS. O odômetro mede, porque é cumulativo no aparelho e não depende da posição — numa
> posição falsa ele marca ~0 km, que é a verdade. Só quando **falta odômetro nos dois
> pontos** é que ninguém sabe, e o popup diz isso em vez do contrário.

### 23.4 O ponto fixo é do PAR, não de cada passo

A receita manual da §22.7 — **a que produziu a base convergida** — alternava os dois:

```bash
for i in 1 2 3; do motor; janela; done
```

Quando aquilo virou código em 10/09, virou sequencial (motor 3×, depois janela 3×). E a
janela é dona da janela da perna enquanto o motor deriva a chegada **dentro** dela: rodando
por último, ela movia a janela e ninguém rodava o motor de novo.

Medido antes do conserto: **7 escritas pendentes, todas de perna vazia, todas em
`no_local_desde`** — e 4 delas reescrevendo, com valor diferente, o que o próprio "Robo
atemporal" já gravara (a V-081 querendo pôr `NULL` por cima). O número não se movia com a
janela (7 com `--ate` 08/09, 09/09, 10/09 e 15/09): não era evidência nova chegando, era um
**desacordo parado**.

E havia um segundo escritor escondido na mesma função: a trava de 09/09 entregou a janela
para a rederivação mas deixou a **chegada** passar sem conferir se cai dentro dela. A
`V-2026-000106` tinha `no_local_desde` em 13/08 para uma perna que termina em 04/08 —
**nove dias depois de a perna acabar**. Agora, fora da janela o campo não é apurado e, se já
houver valor gravado fora dela, ele é **retratado**: só não escrever de novo deixaria a
afirmação errada de pé para sempre.

```
rodando server.rodar_pos_diario REAL, três execuções seguidas:
  motor 2 -> pernas 0 -> rotas 0 -> janela 0 -> motor 0   ponto fixo conjunto
  motor 0 -> ...                            -> motor 0
  motor 0 -> ...                            -> motor 0
aferidor:  alta 39->38 (T2 zerou) · media 75->74 · achados 265->263
```

E o ciclo passa a **dizer em voz alta** quando não converge em 3 rodadas, em vez de seguir
calado.

### 23.5 A perna vazia sai de "Entregues"

O badge dizia `Entregue` numa perna que não entregou nada. O conserto é **rótulo, nunca
status** (§21.18): o status fica, o badge lê **"Concluída"** em cinza.

Mas o defeito maior não era o rótulo: o `/api/embarques/kpis` **não filtrava `viagem_vazia`
em lugar nenhum**. Medido: **35 das 130 "entregues no mês" eram pernas — 27%** de um número
que a diretoria lê como entrega ao cliente. Agora `entregues_mes` conta só carga real, e o
que saiu ganhou card próprio ("Vazias no mês", 7 cards numa linha).

E isso obrigou a distinguir **duas espécies com a mesma flag**:

```
107  V-*  criada_por_robo=TRUE  intervalo DERIVADO entre duas viagens
  1  C-2026-000036  a mão       viagem vazia lançada pelo Carvalho, ainda 'Aberta'
```

Esconder pela flag sumiria com a segunda junto com as 107 — esconder carga do caminho manual
é o que a §0 proíbe. Entrou o filtro `perna_vazia`, que exige a **conjunção**
`viagem_vazia AND criada_por_robo`.

No mesmo pacote, o **trecho pós-fechamento** (a linha laranja da §21.20) deixou de aparecer
em perna vazia: ela não é viagem rastreada, é o intervalo derivado entre duas viagens, e a
`data_conclusao` dela é o instante em que o carregamento seguinte começou — então toda
posição posterior é, por definição, da próxima viagem.

### 23.6 As ferramentas novas

| arquivo | papel |
|---|---|
| `_ensaio_pipeline.py` | **o ensaio único** — fotografia, convergência dos 4 passos, aferidor, fita/posição falsa, forma do traçado, veredito. Não escreve nada por padrão. Mesma régua local e em produção |
| `_teste_ciclo_transacao.py` | regressão do apagão: roda o `_ciclo` REAL com a consolidação explodindo e exige posição gravada, purga não executada, `_ultima_retencao` marcada |

### 23.7 Lições de método, com o preço de cada uma

* **O instrumento tem de verificar se o conserto está ativo, não só medir o potencial.** A
  seção 5 do ensaio imprimia os mesmos 74 mapas com ou sem o fix, e por isso "tá tudo igual"
  não provou nada por uma rodada inteira.
* **Medir pelo endpoint, não por cálculo paralelo.** O terceiro consumidor da régua (o
  fallback do odômetro) ficou escondido enquanto a medição usava conta própria. É a §20.5 de
  novo, mesmo estando catalogada.
* **Achado não é escrita.** Contar linhas do CSV do `_rederivar_vazias` dava 4 escritas onde
  havia 0 — o `par sobreposto` faz `continue` de propósito e sai no mesmo arquivo.
* **Quando o usuário descreve o que vê, ele geralmente está certo.** "Era sempre uma linha
  reta até o próximo ponto" foi lido como confusão entre camadas; era a descrição precisa do
  comportamento antigo. Custou dois commits, um para desfazer o outro.
* **Texto de tela é afirmação e precisa ser verdade.** O popup dizia "o km deste trecho não
  entra no total" — falso: quem perde o trecho é o haversine, o odômetro mede.

### 23.8 O que fica aberto

1. **O `/api/rastreamento/health` mente.** Conta erro do `embarques_3s_log`, que fica verde
   quando a falha é nossa. Precisa olhar a **idade de `embarques_posicoes_atuais`** — a única
   tabela que só o polling escreve — e alarmar acima de um limiar. **É o item nº 1 da fila**:
   sem ele, o próximo apagão também só aparece quando alguém olhar o mapa.
2. **Relógio adiantado em alguns aparelhos.** A 3S devolveu posição ~40 min no futuro
   (`TYO9J56`). Não atrapalha backfill nem contagem, mas adiantamento bagunça cálculo de
   velocidade.
3. **A régua do rótulo `placa_longe`** (§22.10 nº 1) e o **manifesto duplicado** (§22.10
   nº 2) continuam abertos, sem mudança.
4. **Fase D — `EMBARQUES_MODELO_CARRETA`** segue sendo o que ataca V1 e F5.

---

## 24. Continuação e desengate de pátio — a C-677 e o que ela ensinou (11/09/2026)

> **Comece por aqui se está retomando.** Implementado atrás de `EMBARQUES_CONTINUACAO`
> (nasce desligada), testado na base local com o robô REAL rodando dia a dia, **não
> deployado**. A rede de segurança tem três camadas (§24.7). Nada aqui toca o caminho
> manual (§0): zero cargas lançadas à mão no log da rodada de teste.

### 24.1 O caso

A `C-2026-000677` (Química Amparo, Amparo → Brasília, FFA2I61 + QXA9H76) aparecia `Entregue`
com `chegada —` e `KM FALTANDO 421 km`. Três fontes independentes contaram a mesma história:
o Marley levou a carreta carregada até o pátio de Uberlândia em 06/09, **desengatou**, pegou a
TZB1D35 (C-684) e foi para Brasília; a QXA9H76 ficou 3 dias parada (odômetro 423.525 congelado);
em 09/09 o Egnaldo a engatou no DIL8F42 (manifesto `UDI029149-8` → C-818) e entregou em
Brasília em 10/09. Os 3 CTes da C-677 têm `primeiro_manifesto = 029121-8` e
`ultimo_manifesto = 029149-8`. Mesma carreta, mesma mercadoria, outro cavalo — **desengate no
pátio**, não transbordo, não entrega.

O Gabriel resumiu a regra antes de eu chegar nela: *"tudo que troca o cavalo é desengate; a
primeira linha é desengate sempre; só a nova marca entregue; contaria uma carga de todo jeito."*

### 24.2 O que foi medido (01/08 → 10/09, CTe `pm ≠ um`)

```
2.002 CTes com manifesto · 80 (4,0%) atravessam dois manifestos · 55 pares
   33  mesma carreta, cavalo trocou ........ DESENGATE (em 12 o cavalo pegou outro manifesto no meio)
   10  mesma carreta, mesmo cavalo ......... parou na filial, doc novo, seguiu (2 com troca de motorista)
    8  mesmo par, <=1 dia, CTRB idêntico ... REEMISSÃO (uma viagem, dois manifestos → 2 cargas no robô)
    4  carreta trocou ...................... transbordo (Química Amparo CD25, fora deste desenho)
```

* **Continuação é fenômeno de hub**: 51 de 55 mantêm a carreta. Uberlândia na maioria,
  Seropédica (RIO) no caso Heinz. A carreta espera **16–80 h** (mediana 28 h) contra pousos
  de < 12 h. Dois cavalos chegaram a trocar de carreta entre si no pátio (Doce Mineiro).
* **Nenhum caso** de "mesmo cavalo fez outra viagem e voltou para a mesma carreta" — a regra
  existe na máquina, mas não ocorreu.
* `entregues_mes` contava a perna 1: **ago 206 → 191 (−7%), set 95 → 84 (−12%)**.
* **Km vazio fabricado**: V-029, V-050, V-059, V-120 = 1.384 km de perna entre o *destino do
  papel* de A e a origem de B, que a carreta nunca rodou (ela estava parada no pátio).
* **Risco de duplicata confirmado**: 8 viagens físicas em 40 dias geram 2 cargas (C-459/C-476
  Jundiaí→Teresina 2.635 km; C-801/C-802 da §22.10). A `manifestos` do SSW não marca cancelado;
  quem denuncia é o CTe.
* Para a auditoria, fora do painel: em 12 dos 33 desengates o **CTRB da perna 1 cobre a viagem
  inteira** e o da perna 2 cobre o trecho final de novo — ~6,7 mil km documentados duas vezes
  (`UDI028908-6 → 028921-3`: dois CTRBs Uberlândia→Três Rios de 843 km, e o primeiro cavalo
  nem saiu de Uberlândia). Se algum é agregado, é frete pago em dobro.

### 24.3 O modelo — um status novo (`Continuada`); quem manda é a ligação

| primeira linha (A) | quando | ativa? | conta entrega? |
|---|---|---|---|
| `Desengatada` (sem `continua_em`) | cavalo saiu, carreta esperando — no **destino** (regra antiga) ou no **pátio** (`desengate_local`) | sim | não |
| `Desengatada → C-B` | outro cavalo levou a carreta | **não** | não |
| `Continuada → C-B` | mesmo conjunto, manifesto novo, seguiu | não | não |
| `Cancelada → C-B` | reemissão | não | não |
| `Entregue` | chegou ao cliente | não | **sim** |

"Ativa" = o worker aplica GPS, o conflito prende cavalo/carreta, os contadores contam. A
palavra `Desengatada` é **ativa sem ligação e terminal com ela** — é a coluna `continua_em`
que decide, não o status. No **pátio** o worker não deriva chegada nem entrega: o GPS ali é
de OUTRA viagem (medido par a par: em 7 de 9 desengates a carreta chega ao destino sob o
documento B **antes** de o robô ligar A→B; na C-481 viraria `Entregue` com `entregue_auto=TRUE`
pelo GPS da C-514). Só o documento encerra o pátio. Carga B ganha o rótulo "← continuação da
C-A"; nasce `Aberta`, roda e termina `Entregue` normalmente — é a única que conta.

Dois gatilhos, duas fontes: o **desengate** vem do manifesto do cavalo (com outra carreta) +
GPS da carreta (≤ 25 km do destino = destino; longe = pátio); a **ligação** vem do CTe
(`pm → um`), exata, e só o diário enxerga CTe. **`continua_em` é o contrato** entre o diário
e todo o resto (atemporal, worker, gerador de pernas, aferidor, telas): sem a coluna, o
atemporal derivaria `Entregue` pelo manifesto novo da carreta e reescreveria o status — 27
cargas oscilando por rodada no ensaio (§20.6: oscilação é bug).

### 24.4 O que foi escrito

| arquivo | o quê |
|---|---|
| `embarques_continuacao.py` (novo) | chave, DDL (`continua_em`, `desengate_local`), `desengatar_por_cavalo`, `ligar_continuacoes`, `parada_carreta`, `filtro_ativas`/`filtro_ligadas` (fragmentos SQL vazios com a chave desligada ou sem as colunas) |
| `embarques_auto.py` | `fechar → desengatar → criar → ligar`; eixo carreta também com esta chave; `fechar_pendentes` não fecha A quando o CTe diz que seguiu; `dedup_veiculo` só por carreta; `encerrar` recusa carga ligada |
| `rastreamento_worker.py` | `_processar_cargas` exclui pátio e ligadas |
| `server.py` | conflitos, KPIs (`entregues_mes` sem continuação; `desengatadas` só as que esperam), listagem e detalhe devolvem `continua_em_numero` / `continuacao_de` |
| `_robo_atemporal.py`, `_regerar_vazias_agosto.py`, `_rederivar_vazias.py` | respeitam a ligação (atemporal não toca; A ligada não ancora perna) |
| `_auditoria_geral.py` | `Continuada`/ligada = fechada; classe nova **X1** (desengatada no pátio há ≥ 3 d sem continuação) |
| `embarques-relatorio.html`, `embarques.html`, `mapa-carga.html` | badge `Continuada`, ponteiros `→ C-B` / `← C-A`, linha "Desengate · no pátio", "Chegada — (segue em C-B)", ações escondidas em carga ligada |
| `init_db.py` | as colunas |
| `_snapshot_embarques.py` (novo) | `criar / listar / diff / restaurar --ids` ou `--desde-log` |
| `_ensaio_continuacao.py`, `_replay_producao.py`, `_rodar_diario_local.py` (novos) | os três testes (§24.5) |

### 24.5 Os testes, e o que cada um pegou

1. **`_ensaio_continuacao.py`** (estático, só leitura): aplica as regras sobre os pares e pergunta
   a cada peça como reagiria sem ser ensinada. Achou R1 (atemporal oscila), R2 (worker aplica
   GPS de B em A), R3 (pernas fabricadas), R4 (aferidor perde `Continuada`). A primeira versão
   imprimiu "0" no R2 por bug meu — refeito par a par deu 7 de 9. *Teste que dá zero na primeira
   rodada merece desconfiança.*
2. **`_replay_producao.py`** (progressivo): reproduz produção dia a dia (manifesto visto em D+1
   16:30, CTe mudando só quando B é emitido, GPS na hora). Calibração com as regras de HOJE:
   **261/276 (95%)** iguais à base. Com as regras novas: a C-677 faz `Em rota → Desengatada
   (pátio, "Uberlândia, 347 km do destino") → Desengatada → C-818`; a C-459 vira `Cancelada` no
   dia em que B aparece; **85% dos desengates só são conhecidos quando B nasce** (o cavalo
   largou a carreta sem emitir manifesto), e em 9 o GPS já tinha fechado A como `Entregue` no
   hub — o "saiu do destino" era a carreta saindo com o cavalo B.
3. **`_rodar_diario_local.py`** (o robô REAL, base local, chave ligada, 21/08 → 10/09): achou
   **dois bugs que os simuladores não viam**:
   * o `_norm` do robô compara manifesto **sem hífen** e `manifesto_origem` é gravado **com** —
     A e B nunca se achavam. Corrigido com normalização dos dois lados no SQL (`SQL_MAN`);
   * carga já ligada continuava na lista de ativas do `fechar_pendentes`, e o manifesto
     *seguinte* da carreta a fechava de novo como `Entregue (manifesto_novo)` — 19 das 21
     voltaram atrás. Corrigido com `filtro_ativas` no SELECT e guarda no `encerrar`.

   Resultado final, contra o gabarito do replay: **21 `Desengatada → B` (11 pátio, 10 destino)
   · 6 `Continuada` · 6 relabels de `Entregue` com prova · 0 carga manual · atemporal em
   dry-run propõe 0 escritas em carga ligada · gerador pula 27 pares · F5 cai de 8 para 2 ·
   X1 acusa as carretas paradas no pátio**. Com a chave desligada, `_testar_regras_fechamento.py`
   dá 15/15 e os endpoints ignoram as colunas.

   Limites do test-bed: a base local já estava convergida (as A chegam `Entregue`), então o
   estado intermediário `Desengatada (pátio)` aparece pouco — é o relabel que domina, e é o que
   produção vai fazer no dia em que a chave ligar. A C-459 saiu `Continuada` e não `Cancelada`
   porque `ctrb_origem` é NULL nas cargas locais (o fallback compara as pontas das próprias
   cargas, e a B nasceu com origem Uberlândia); muda a palavra, não o efeito. As 13 "sequência
   de viagem" da rodada são artefato: as B criadas no teste nunca passaram pelo worker.

### 24.6 O que fica aberto

1. **Aviso precoce** "⚓ parada na filial há N h" em carga `Em rota` — rótulo, nunca status
   (§21.18) — para os 85% em que o desengate só é conhecido quando B nasce.
2. **`KM RASTREADOR —` na perna 1** (`_kpi_sanidade`): a régua geométrica assume "concluída =
   chegou ao destino"; em carga fechada sem prova (C-677: 444 km de odômetro contra 455 de
   reta até Uberlândia, medição impecável) ela apaga número certo. Comparador honesto ali é o
   GPS da mesma janela, como na carga em trânsito (§14.4).
3. **Transbordo** (carreta trocou, 4 casos) continua sem ligação — de propósito.
4. **CTRB em dobro** nos desengates — assunto de auditoria de pagamento, não do painel.
5. `ctrb_origem` vazio nas cargas antigas rebaixa reemissão a `Continuada`.

### 24.7 Subir — a receita, com as três camadas de volta

| camada | protege | volta |
|---|---|---|
| tag `pre-continuacao-2026-09-11` (HEAD `2473fb0`) | código | `git diff pre-continuacao-2026-09-11` / checkout |
| `EMBARQUES_CONTINUACAO` | comportamento | `docker service update --env-add EMBARQUES_CONTINUACAO=false rizza-auditoria_app` — sem deploy; **nunca pelo stack do Portainer** (devolve a imagem antiga, 21/08) |
| `_snapshot_embarques.py` | dados | restore **por id**, nunca tabela inteira: entre o snapshot e o restore o worker e o diário continuam gravando |

```bash
# 1) deploy INERTE (chave ausente = false): build, push, service update; conferir DENTRO do container
CT=$(docker ps -q --filter "name=rizza-auditoria"); docker exec $CT ls embarques_continuacao.py

# 2) snapshot das 5 tabelas derivadas (NUNCA posições — §23.2)
docker exec $CT python -X utf8 _snapshot_embarques.py criar

# 3) ligar pela CLI e deixar o diário das 16:30 rodar (ou executar à mão dentro do container)
docker service update --env-add EMBARQUES_CONTINUACAO=true rizza-auditoria_app

# 4) medir com o mesmo gabarito: ligações, zero carga manual, atemporal sem oscilar
CT=$(docker ps -q --filter "name=rizza-auditoria")
docker exec $CT python -X utf8 _snapshot_embarques.py diff snap_XXXX
docker exec $CT python -X utf8 _robo_atemporal.py --desde 2026-08-15 --ate HOJE --csv /tmp/a.csv | tail -3
docker exec $CT python -X utf8 _auditoria_geral.py --desde 2026-08-15 --ate HOJE | head -25

# 5) divergiu? chave false no mesmo minuto, depois restore por id
docker service update --env-add EMBARQUES_CONTINUACAO=false rizza-auditoria_app
docker exec $CT python -X utf8 _snapshot_embarques.py restaurar snap_XXXX --desde-log 'AAAA-MM-DD HH:MM' --aplicar
```

O que esperar em produção no primeiro dia: o `coletar()` abre 10 dias de CTe antes da janela,
então as ligações de **~25/08 em diante** entram na primeira rodada (as de agosto exigem
`executar(dia=...)` para trás, como o `_rodar_diario_local.py` faz). `entregues_mes` de
setembro cai ~10%; o card "Carretas desengatadas" passa a mostrar só as que esperam; o
aferidor ganha a classe X1.
