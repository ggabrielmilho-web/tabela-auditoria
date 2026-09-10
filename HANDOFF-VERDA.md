# HANDOFF — Integração Verda (emissões de CO₂e)

**Estado: PRODUÇÃO ARMADA, PRIMEIRO ENVIO EM 11/09/2026.** O código está no ar no servidor, as
credenciais de produção estão no serviço e o ensaio (`--so-montar`) rodou lá dentro: **119 viagens
prontas, 6 bloqueadas, 1 sem CTRB**, ~154,40 t CO₂e previstos para a semana de 31/08 a 06/09.
Falta só disparar o envio. Ver [seção 17](#17-produção-as-decisões-de-10092026) e
[seção 18](#18-a-aba-verda-e-o-estado-do-deploy).

Antes disso, agosto inteiro rodou em **homologação** — 597 viagens, 0 rejeitadas. As primeiras
chamadas reais derrubaram **quatro** afirmações da documentação, três delas bugs que quebravam o
robô em silêncio ([seção 5](#5-o-que-a-documentação-respondeu-não-perguntar-de-novo)).

> **Produção é OUTRA CONTA, não a mesma com chave nova** — provado por `GetTransaction` (§17.1). O
> inventário de produção nasce limpo; as 574 viagens de homologação não contaminam nada.

**Ainda aberto:** o agendamento semanal automático (hoje o disparo é manual) e as perguntas 4 e 6 da
[seção 4](#4-o-que-está-travado).

---

## 1. Contexto

A **Verda** (verda.global) é uma plataforma de cálculo de emissões de CO₂e em transporte. A Rizza
precisa alimentá-la com as viagens para que o inventário de emissões exista.

**O gatilho é exigência de cliente** (Nestlé), não a lei. A Lei 15.042/2024 (MBRE) obriga inventário
só acima de 10 mil tCO₂e/ano, patamar que a Rizza provavelmente não atinge. Grandes embarcadores
vêm colocando cláusula ambiental em contrato de frete, e o número da Rizza vira linha do escopo 3
do cliente.

**Decisão tomada:** o desenvolvimento é interno, não pelo SSW. As regras (frota × agregado, qual
método, como calcular consumo) são de negócio e só a Rizza conhece; e todas as fontes já caem no
Postgres pelos robôs existentes.

### Pessoas

| Quem | Papel |
|---|---|
| Ricardo Carlotto | CRO e fundador da Verda — contato comercial |
| Rafael Fanchini | sócio-fundador, responsável técnico da Verda — é com ele que se resolve |
| Tatiane Cardoso | Rizza, interlocutora administrativa que encaminhou tudo |
| Camilo Borges | Rizza, decide o escopo e resolve as pendências de cadastro |

### Situação comercial (atenção)

A Rizza aderiu à **versão gratuita**, que guarda só o **consolidado mensal por 15 meses** — sem
detalhe de viagem, sem análise de eficiência e, principalmente, **sem compartilhamento de inventário
com o embarcador**. Se o entregável final é a Nestlé receber o dado, a gratuita não faz isso;
precisaria do ESG Básico E2 (R$ 250/mês). **Confirmar com a Tatiane o que exatamente a Nestlé pediu.**

### Documentação de origem

Em `c:\Phyton-Projetos\integracao_verda\`:
- `Verda APIs - 2026-07-21.pdf` — 77 páginas, a especificação
- `Verda - exemplo de chamada de API Fuel.pdf` — exemplo comentado (resolve várias ambiguidades)
- o `.eml` da thread com o Ricardo — traz os `VehicleTypeKey` e a prioridade Fuel > Weight, que
  **não estão na documentação**

---

## 2. O que foi construído

### Núcleo — as regras

| Arquivo | O que faz |
|---|---|
| `verda_veiculos.py` | classificação do veículo (`VehicleTypeKey`) e consumo (`FuelConsumption`) |
| `verda_payload.py` | monta o JSON das APIs Fuel e Weight a partir de um manifesto |

### Robô — o que roda em produção

| Arquivo | O que faz |
|---|---|
| `verda_client.py` | Token (cache + UTC), Fuel, Weight, GetTransaction, CancelTransaction. Retry com backoff em rede e 5xx; 4xx não repete, é erro nosso. **Modo simulado embutido.** |
| `verda_estado.py` | tabela `verda_envios` — idempotência, o par de ids que o cancelamento exige, e o erro por campo |
| `verda_job.py` | orquestra montar → enviar → conferir |

### Ferramentas de apoio

| Arquivo | O que faz |
|---|---|
| `_verda_valida.py` | valida **todas** as viagens contra as regras da documentação |
| `_verda_demo.py` | gera o JSON de manifestos reais; é também o **carregador de dados** que o job usa |
| `_verda_classifica.py` | classificação sobre todas as viagens + relatório de exceções |

```bash
cd "c:\Phyton-Projetos\Tabela Auditoria"

python -X utf8 verda_job.py                                      # D-1
python -X utf8 verda_job.py --data 2026-08-20
python -X utf8 verda_job.py --desde 2026-01-01 --ate 2026-08-27   # backfill
python -X utf8 verda_job.py --conferir                           # só o polling
python -X utf8 verda_job.py --resumo                             # fila + rejeitadas
python -X utf8 verda_job.py --data 2026-08-20 --so-montar        # não toca na rede

python -X utf8 _verda_valida.py --exemplos 3                     # antes de qualquer envio
python -X utf8 _verda_demo.py UDI028044-5                        # o JSON de um manifesto
```

**Resultado atual: 4.327 de 4.528 viagens (95,6%) passam limpas.** Os 4,4% restantes são
pendência de cadastro, listada na seção 7.

### As três etapas do job

```
1. MONTAR    manifesto → payload → grava em verda_envios como pendente
2. ENVIAR    pendentes → POST Fuel/Weight → guarda o TransactionId
3. CONFERIR  enviadas → GetTransaction → executed ou rejected (com o campo do erro)
```

Cada uma roda sozinha. **A conferência é etapa separada de propósito**: o processamento da Verda é
assíncrono (seção 5), o POST devolve só `Success`/`TransactionId`, e uma viagem pode ser aceita ali
e rejeitada depois. Marcar "enviado" e esquecer deixaria viagem fora do inventário sem ninguém saber.

### Ciclo de vida na `verda_envios`

```
pendente ──envio──> enviado ──conferência──> executed
                       │                  └─> rejected   (erro_detalhe diz o campo)
                       └──falha──> erro     (tentativas++, volta na próxima rodada)

viagem que mudou de conteúdo:  cancela a transação antiga e volta para pendente
viagem que mudou de AMBIENTE:  volta para pendente e ESQUECE o transaction_id
```

Quatro decisões que não são óbvias:

- **A idempotência é por (viagem, ambiente)**, e a tabela tem coluna `ambiente`
  (`simulado` / `teste` / `producao`). Sem isso as ~600 viagens exercitadas no simulador contariam
  como já enviadas e **nunca sairiam de verdade** — o robô diria "inalterada" e pularia. Vale igual
  de homologação para produção. Ao trocar de ambiente, a viagem volta a `pendente` e o
  `transaction_id` antigo é descartado: id de um ambiente não serve para conferir nem cancelar no
  outro. É o que faz `--resumo` mostrar as duas colunas e deixar claro quais `executed` são reais.
- **O hash de idempotência ignora o `LocalDateTime`.** Ele é a hora da chamada — se entrasse na
  impressão digital, toda viagem pareceria alterada a cada execução e o robô reenviaria a base
  inteira todo dia.
- **Viagem alterada é cancelada e reenviada, não sobrescrita.** Se um CTe for corrigido depois de
  enviado, a emissão contaria duas vezes sem o cancelamento.
- **Recusa com `TransactionId` vira `enviado`, não `erro`.** Se a Verda devolveu um id, há o que
  conferir; o veredito real vem na `GetTransaction`.

### O que ainda falta

**O agendamento automático.** Hoje o disparo é manual. O desenho combinado é uma thread no
`server.py`, no padrão do robô de embarques (flag `VERDA_AUTO` para desligar pelo Portainer sem
deploy), rodando **sexta-feira sobre a semana fechada anterior** (segunda a domingo) — 5 dias de
folga para o CTRB consolidar. Precisa estar de pé antes de 18/09/2026.

> A **tela de acompanhamento** que constava aqui como pendência já existe: é a aba `/verda`
> ([seção 18](#18-a-aba-verda-e-o-estado-do-deploy)).

### Modo simulado

`VERDA_SIMULADO=1` responde sem tocar a rede e **reprova o que a documentação reprovaria** — serve
para exercitar o robô inteiro sem gastar transação. **Não é mais o padrão**: o `.env` está com
`VERDA_SIMULADO=0` desde que a homologação subiu. Rodada de teste (do tempo em que era o padrão):

```
MONTAGEM      nova 24 | sem_payload 1
ENVIO         aceita 24
CONFERÊNCIA   executed 23 | rejected 1     ← a rejeitada caiu por VehicleModelYear,
2ª rodada     inalterada 24                   um dos 175 sem ano no cadastro
```

**Desde 01/09 ele responde no formato REAL da API**, não no da documentação — foi realinhado depois
que a primeira chamada de verdade mostrou que a doc erra em três pontos (seção 5). Um simulador fiel
à doc esconderia justamente os bugs que essas divergências causam, que foi o que aconteceu.

**O que ele continua não cobrindo:** as regras de negócio da Verda. Ele reprova campo obrigatório
ausente, não um `FuelConsumption` absurdo ou um `VehicleTypeKey` que a conta não conhece.

---

## 3. As decisões e o porquê

### 3.1 Qual API para qual viagem

| | viagens | método | escopo |
|---|---|---|---|
| Frota própria | 627 (13,8%) | `Fuel` | 1 |
| Agregado / carreteiro | 3.901 (86,2%) | `Fuel` | 3 |

Com a escala de consumo por idade (3.3), **todo veículo tem consumo**, então quase tudo vai pela
Fuel — 4.520 de 4.528. Só as 8 viagens sem tipo de veículo caem na `Weight`.

A Fuel é o método prioritário do Protocolo GHG e foi o indicado pelo Ricardo por e-mail
("os métodos de cálculo prioritários são Fuel e Weight, nessa ordem").

> **O escopo é independente da API.** `IsScopeOne` é campo próprio: viagem de frota própria continua
> escopo 1 mesmo se calculada pelo método de peso.

### 3.2 `VehicleTypeKey` — classificação por PBT

**As faixas da Verda são peso bruto, não carga útil.** Isso foi apurado por pesquisa, não está na
documentação, e **inverte 95% da frota** — é a decisão mais impactante do projeto.

Os cinco códigos da Verda são, um a um, as categorias do **DEFRA**, e tanto o DEFRA
("articulated >33 t") quanto o **GLEC** ("European articulated truck >32 t *gross combined weight*")
definem o corte em peso bruto. O GLEC é a base da **ISO 14083** e é o que se usa no Brasil — não há
régua nacional concorrente.

Cruzando com os limites do CONTRAN (cavalo + carreta 3 eixos = **45 t** de PBTC; bitrem 57 t;
rodotrem 74 t), **nenhum conjunto articulado brasileiro fica abaixo de 33 t**. A faixa
`articulado_35` cobre articulado leve europeu e praticamente não existe aqui.

```
articulado_330   4.331 viagens  (95,6%)
rigido_170         189 viagens  ( 4,2%)
não classificado     8 viagens  ( 0,2%)
```

Alternar a leitura é uma constante: `BASE_FAIXA = 'pbt'` em `verda_veiculos.py`.

### 3.3 `FuelConsumption` — escala por idade (definida pela diretoria)

```python
CONSUMO_POR_IDADE = [(5, 2.20), (10, 1.80), (999, 1.50)]   # km/l CARREGADO
```

| Faixa | km/l | Viagens |
|---|---|---|
| até 5 anos | 2,20 | 326 (7,2%) |
| até 10 anos | 1,80 | 558 (12,3%) |
| acima de 10 (faixa aberta) | 1,50 | 3.636 (80,3%) |

**Por que a escala e não a média medida.** O `FuelConsumption` é o consumo **carregado**. A medição
do ValeCard é média de **ciclo** — o hodômetro conta o km carregado *e* o vazio da volta, sobre todo
o diesel. Por isso ela dá 2,52 km/l na faixa de 6 a 10 anos, número que a volta vazia puxa para cima
e que não representa o trecho declarado. A API separa as duas coisas: a **Fuel** cobre a viagem
carregada (o manifesto) e o retorno vazio tem endpoint próprio, a **UnloadedTrip**.

A escala vale igual para frota e terceiro — **critério único é mais defensável numa auditoria** do
que dois parâmetros — e resolve o pedido que a Verda fez ao Camilo, de estipular média para
agregados e carreteiros.

Isso importa porque **76,4% das viagens são de veículo com mais de 10 anos**, e é exatamente onde não
temos medição própria (uma placa, 2.949 L de janela).

> A faixa aberta engloba veículo sem ano no cadastro (175 viagens). Quando o Camilo preencher o ano
> dessas placas, algumas podem subir de faixa sozinhas.

**Medição de ciclo guardada para conferência** (`CICLO_MEDIDO`): 2,71 km/l até 5 anos, 2,52 de 6 a 10
— 11 placas, 253 mil litros, 655 mil km de hodômetro.

### 3.4 Capacidade — cascata de pisos

O cadastro do SSW foi preenchido por cinco anos de gente diferente: **26% das carretas com histórico
declaram capacidade menor do que o peso que comprovadamente carregam** (há carreta cadastrada com
16 t transportando 25 t em dezenas de viagens). A capacidade sai do **maior** entre três fontes:

| Camada | Fonte | Define em |
|---|---|---|
| **piso estrutural** | configuração física — carreta 27 t, truck 12 t, toco 6 t | 57,9% |
| **evidência** | p90 do peso realmente transportado (≥5 viagens) | 8,1% |
| **declarado** | cadastro, só dentro de faixa plausível | 33,7% |

Em **66% das viagens o cadastro nem é consultado**. O piso é a camada que segura o dado ruim e não
depende de ninguém digitar nada — importante porque 523 das 659 carretas têm menos de 5 viagens.

Validação cruzada: piso 27 t + tara 17 t = **44 t**, contra os **45 t** de PBTC legal do cavalo +
carreta de 3 eixos. Dois caminhos independentes, 2% de diferença.

### 3.5 Identificação — só o mínimo obrigatório

Dos quatro campos que carregam documento, **só `ShipperKey` é obrigatório** (pgs. 27 e 63).

```
ShipperKey          CNPJ do embarcador     ← único enviado
CounterpartKey      ""                     ← desligado (opcional)
CarrierPartnerKey   ""                     ← desligado (opcional)
AgentKey            ""                     ← nunca preenchido
DriverId            "M761CD868500F"        ← pseudônimo estável, não é CPF
```

52% das viagens são de carreteiro autônomo — identificar o parceiro significaria mandar CPF de pessoa
física para fora, sem obrigação nenhuma. O que o inventário precisa é `IsOwnOperation` +
`IsScopeOne`, que separam escopo 1 de escopo 3 **sem nomear ninguém**.

Chaves no topo de `verda_payload.py`: `IDENTIFICAR_PARCEIRO`, `IDENTIFICAR_CONTRAPARTE`, `MOTORISTA`.

### 3.6 Outros parâmetros

| Constante | Valor | Razão |
|---|---|---|
| `VEHICLE_UTILIZATION` | `1` | dedução do exemplo oficial: 20,1 t de carga com utilization 1 — se fosse ocupação daria ~0,6, logo é dedicação do veículo |
| `KM_ENTREGA_URBANA` | `25.0` | entrega intramunicipal: o `rotas_km` devolve 0 (centroide contra ele mesmo) em 704 itens; 0 seria emissão zero |
| `FATOR_RODOVIARIO` | `1.30` | Haversine → estrada, quando o par não está no `rotas_km`. Calibrado contra 2.094 pares reais: mediana **1,268** |
| `FUEL_TYPE` | `diesel` | a Verda só tem um código de diesel (pg. 76) — não separa S10 de S500, e enxofre não entra em CO₂e |
| `ENVIAR_RENEWABLE_SHARE` | `False` | ver pergunta 3 na seção 4 |
| `WEIGHT_UNIT` / `ITEM_INDEX_UNIT` | `kgram` | o SSW já dá em kg; evita erro de conversão |

---

## 4. O que está travado

### ~~1. As URLs não resolvem~~ — RESOLVIDO em 01/09/2026

Os nomes da documentação (`verda-application*.verda.global`) **nunca existiram em DNS** e não são o
endereço da plataforma. Ela roda em **OutSystems Cloud**, e o Rafael mandou a URL por WhatsApp:

```
teste       https://personal-d33vvevh.outsystemscloud.com/VerdaIntegration/rest/
producao    A MESMA da homologação — só o par de chaves muda (confirmado em 10/09, §17.1)
```

O **caminho** a partir do host é o documentado (`/VerdaIntegration/rest/<Dominio>/<Api>`), e
`RoadOrchestration` e `Credential` foram confirmados batendo neles. Está em `AMBIENTES`, no topo do
`verda_client.py`, e dá para injetar por `VERDA_URL_TESTE` / `VERDA_URL_PRODUCAO` no `.env` sem
editar código. **Produção é `None` de propósito** — melhor falhar com mensagem clara do que chutar um
host e mandar viagem para o lugar errado.

### ~~2. Acesso à plataforma~~ — RESOLVIDO em 01/09/2026

As credenciais de homologação vieram junto, e estão no `.env` (que é gitignorado). A autenticação
funciona **exatamente como a doc descreve**: header `Authorization` com o base64 de
`ApplicationKey:SecretKey`, **sem** o prefixo `Basic` — mandar `Basic <b64>` é recusado. Token dura
30 minutos.

**A lista de `VehicleTypeKey` da conta continua pendente.** Em homologação o Rafael mandou usar o
valor fixo `"veiculo_teste"`; os códigos de produção são "os tipos de veículos que vierem a ser
cadastrados por vocês" — ou seja, **esse cadastro ainda não existe** e é tarefa da Rizza. Enquanto
não existir, `articulado_330` e `rigido_170` são nomes nossos, tirados do DEFRA, e podem não bater
com os da conta. Ver [seção 10](#10-como-ligar-quando-a-verda-responder).

### ~~3. `RenewableShare` × biodiesel~~ — RESPONDIDO pelo próprio dado (01/09/2026)

**A Verda já embute a mistura brasileira. Manter `ENVIAR_RENEWABLE_SHARE = False` está certo.**

Não foi preciso perguntar: o dashboard da viagem `NOD004785-6` expõe o suficiente para reconstruir o
motor deles. Mandamos `RenewableShare: null` e mesmo assim o desconto apareceu.

```
o que ENVIAMOS      442,0 km | 20.566,898 kg | FuelConsumption 2,2 km/l | RenewableShare null
o que a TELA mostra 22,101 ml/tkm | 50,485 g/tkm | 1,038 kg/km | 458,934 kg

litros   = 442 / 2,2            = 200,909 L
t·km     = 20,5669 × 442        = 9.090,57
diesel   = 200.909 ml / 9.090,57 = 22,101 ml/tkm   ← BATE ao milésimo
carbono  = 458,934 / 9.090,57    = 50,485 g/tkm    ← BATE ao milésimo
fator    = 458,934 / 200,909     = 2,28429 kg CO2e/L
```

Um fator de combustão de 2,284 kg/L é **fisicamente impossível** para diesel fóssil — a
estequiometria dá ~2,6–2,7 kg por litro. Logo, parte do carbono está sendo tratada como **biogênica**:
há desconto de fração renovável embutido. **Isso é fato, medido, não inferência.**

**Qual é o fator base, porém, NÃO está determinado.** Várias combinações reproduzem o mesmo número:

| fator base | desconto necessário |
|---|---|
| DEFRA, diesel 100% mineral (2,68779) | 15,01% — o B15, "exato" |
| GHG Protocol BR, combustão (~2,603) | 12,24% |
| GHG Protocol BR + CH₄/N₂O (~2,65) | 13,80% |

Cheguei a afirmar que era DEFRA × 0,85, porque o erro dava 0,015% e pareceu prova. **Era coincidência
numérica.** A tela *Configurações da conta* da plataforma diz que a metodologia é
**"Programa Brasileiro GHG Protocol - 2025.0.1"**, com **margem de segurança de 0%** — ou seja, base
brasileira, não britânica. O desconto real deve ficar na casa dos 12–14%, não 15%.

**E foi confirmado por experimento, não só por aritmética.** Mandamos a mesma viagem três vezes,
mudando só o campo, e o total do dashboard distingue as hipóteses sem ambiguidade:

| viagem | `RenewableShare` | emissão |
|---|---|---|
| `NOD004785-6` | `null` | 458,93 kg |
| `ZZTESTE-RS015` | `0,15` | **390,09 kg** (−15%) |
| `ZZTESTE-RS000` | `0,00` | 458,93 kg (sem efeito) |

O dashboard mostrou **986,397 g/km × 1.326 km = 1.307,962 kg**, contra 1.307,951 kg previstos para a
hipótese da dupla contagem — **erro de 0,0009%**. As outras hipóteses davam 1.377 kg (campo ignorado)
e 1.458 kg (campo substitui o default), ambas descartadas.

O desconto indevido aparece na plataforma como **"emissões evitadas"** — 68,84 kg numa única viagem.
Num ano inteiro seriam ~15% do inventário da Rizza declarados a menos, com aparência de ganho
ambiental.

**Consequência prática:** `ENVIAR_RENEWABLE_SHARE` fica **desligado**. As constantes
`MISTURA_BIODIESEL` continuam no `verda_payload.py` para o caso de eles mudarem de comportamento, mas
hoje não devem ser usadas. As duas viagens sintéticas foram canceladas
(`CancelTransaction` → `canceled`) e não estão no inventário.

**4. O fator da `Weight` é calibrado para o Brasil?** **Boa notícia:** a tela
*Configurações da conta* mostra que a metodologia da conta é o **"Programa Brasileiro GHG Protocol -
2025.0.1"**, com **margem de segurança 0%**. Ou seja, a base é a norma brasileira — some a preocupação
de que os fatores fossem simplesmente os europeus. Vale confirmar com o Rafael se isso vale também
para os fatores por tipo de veículo da `Weight`, cujos códigos continuam sendo as categorias DEFRA.

A documentação **não divulga o fator da `Weight` em lugar nenhum** — é caixa-preta, e ao contrário da
`Fuel` não dá para reconstruir a partir do dashboard, porque não existe um "consumo que mandamos"
para dividir. Como os códigos de veículo são as categorias europeias, é provável que os fatores
também sejam. A categoria ">33 t" foi construída sobre conjunto de 40–44 t; o brasileiro roda
**45 a 74 t**, com frota de terceiros de **15 anos** de idade mediana. Pesa pouco no total (só 8 das
4.528 viagens vão pela `Weight`), mas é a pergunta que sobra para o Rafael.

### ~~5. Volume e fluxo~~ — RESPONDIDO na prática (01/09/2026)

**Não há limite de taxa observável.** 300 chamadas sequenciais, uma por viagem, sem pausa: **300
aceitas, 0 recusadas, 0 falhas, 300 `executed`**. Ritmo de **1,08 s por viagem** no POST; com a
conferência, ~1,8 s por viagem ponta a ponta.

O que isso significa para o dimensionamento:

| | |
|---|---|
| um mês (≈600 viagens) | ~11 min de envio + ~7 min de conferência |
| o backfill do ano (4.5 mil) | ~1h20 de envio |
| o D-1 (≈25 viagens) | ~45 s |

Continua sem endpoint de lote — é uma chamada por viagem mesmo. O `TETO_LOTE = 500` do
`verda_job.py` foi calibrado no escuro e **pode ser afrouxado**, mas não há motivo: rodar em janelas
de 300 custa o mesmo tempo total e mantém a trava útil para o dia em que a Verda mudar de ideia.

O fluxo assíncrono está confirmado: o POST devolve `TransactionId` e a viagem passa por
`primary`/`processing` antes de virar `executed`, em ~1 a 2 minutos. **O dashboard consolida
depois disso** — ver a nota no fim da seção 5.

**6. Retroatividade.** Qual janela é aceita para carga histórica?

### Duas coisas que se **afirma**, não se pergunta

Chegar com trabalho feito muda o tom:

- **"Classificamos por PBT"** — as cinco faixas são as categorias DEFRA e tanto DEFRA quanto GLEC
  cortam em peso bruto; praticamente toda a frota brasileira cai em `articulado_330`. Nos corrijam se
  estiver errado.
- **"`VehicleUtilization` = 1"** — deduzido do exemplo de vocês.
- Vale dizer também que **enviamos só o mínimo obrigatório de identificação**. Num projeto de ESG isso
  pega bem, e já responde por que os outros campos vêm vazios.

---

## 5. O que a documentação respondeu (não perguntar de novo)

Estas eu levantei como dúvida e depois achei a resposta lendo com calma:

| Dúvida | Resposta | Onde |
|---|---|---|
| `WaypointDistance` repetido nos itens da mesma parada soma? | **Não.** O exemplo traz 2 itens com `WaypointOrder: 0` e `863` cada, descritos como *um* ponto a 863 km. É por parada. | exemplo Fuel |
| `/Orchestration` ou `/RoadOrchestration`? | **RoadOrchestration.** A página do domínio e o índice põem as seis APIs ali; as linhas de `Passenger`/`UnloadedTrip`/`Weight` sem "Road" são typo. | pgs. 3, 21 |
| Onde pegar as credenciais? | Menu → Conta → Aplicativo API | pg. 6 |
| Tem envelope `Request` no body? | **Não.** O `L0 = Request` da tabela é o próprio corpo; o body é plano. (Nas APIs de Ecommerce existe um `Input` de verdade em L2.) | exemplo |
| `Scope` no header | No Token vai a lista separada por espaço; em cada chamada vai só o nome daquela API | pgs. 6, 23 |
| Fuel/Weight têm `ReturnDistance`? | **Não** — só LastMile, Passenger e OffsetQuotation. Logo a pré-condição da `UnloadedTrip` ("ReturnDistance da viagem inicial deve ser zero") já está satisfeita. | pgs. 23-29, 60-65 |

### O processamento é assíncrono

O POST devolve só `Success`, `Message` e `TransactionId` — **não devolve o CO₂e nem o motivo do
erro**. O detalhe está na `GetTransaction` (pgs. 73-74), com `StatusKey` e uma lista `ErrorDetail`
trazendo `FieldName` + `ErrorDescription`. A lista de status (pg. 76) inclui `processing` e
`rejected`.

**Uma viagem pode ser aceita no POST e rejeitada depois.** O robô precisa das duas etapas.

### `UnloadedTrip` só serve para frota própria

Não tem `VehicleTypeKey` e exige `FuelConsumption` obrigatório (pg. 56). Viagem vazia de agregado não
tem como ser reportada — é o desenho da API, não adianta perguntar.

### O que a API real fez, contra o que a doc dizia (01/09/2026)

As primeiras chamadas reais derrubaram **quatro** afirmações da documentação. **Três quebravam o robô
em silêncio** — nenhuma aparecia no simulador, porque ele imitava a doc.

| # | A doc diz | A API faz | Consequência |
|---|---|---|---|
| 1 | `GetTransaction` responde `TransactionId` (pg. 73) | responde **`TransactionKey`** | **bug travante.** `conferir()` lia `TransactionId` → `None` → o `UPDATE ... WHERE transaction_id = NULL` não achava a linha, e a viagem ficava presa em `enviado` **para sempre**, reconferida a cada rodada. Nunca chegaria a `executed`. |
| 2 | `CancelTransaction` recebe `TransactionId` (pg. 69) | recebe **`TransactionKey`** | **o mais grave.** O cancelamento **nunca funcionaria** — devolve `Success: false` com *"Combination 'TransactionKey' and 'TransportationId' invalid."*. E o cancelamento é o que impede a dupla contagem quando um CTe é corrigido depois de enviado: sem ele, a viagem alterada é reenviada e a emissão conta **duas vezes** no inventário. Pior, o job só imprimia "FALHOU" e reenviava mesmo assim. |
| 3 | `"Success": "1"` (texto) | **boolean JSON** `true` | `str(True) != '1'`, então todo envio parecia recusado. O `enviar()` escapou por sorte (o job tem fallback: se veio `TransactionId`, trata como aceito), mas o `cancelar()` reportaria "FALHOU" em todo cancelamento bem-sucedido — mascarando o bug nº 2 caso ele fosse corrigido sozinho. |
| 4 | erro vem como `Success`/`Message` | **HTTP 500** com `{"Errors": [...]}` | 500 põe `_bruto` para retentar 3× com backoff. Inútil (erro determinístico) e **perigoso num POST de viagem**: uma tentativa que falhasse depois de registrar a transação duplicaria a emissão. |

O padrão é claro: **onde a doc escreve `TransactionId` num request ou numa resposta, a API usa
`TransactionKey`.** A única exceção é o `TransactionId` que volta no POST de envio, que é mesmo esse
o nome.

Corrigido: `_sucesso()` aceita as duas formas de `Success`; `conferir()` e `cancelar()` usam
`TransactionKey`; `_bruto` **não repete 500 que traga corpo `Errors`**; e o job **não reenvia** viagem
cujo cancelamento falhou — sai da rodada com alerta, em vez de duplicar em silêncio. O simulador foi
realinhado ao formato real (resposta plana, boolean, `TransactionKey`), porque um simulador fiel à
doc esconderia exatamente estes bugs, que foi o que aconteceu por semanas.

### `executed` não quer dizer "está no inventário"

O dashboard **demora alguns minutos** para consolidar. Uma viagem pode estar `executed` na
`GetTransaction` e ainda não aparecer na tela — aconteceu com as duas viagens do teste de
`RenewableShare`, que só surgiram na terceira olhada. Não é erro; é atraso.

E a `GetTransaction` **não devolve o CO₂e** — só `TransactionKey`, `UTCDate` e `StatusKey`. Não há
como conferir emissão por API: o número só existe no dashboard. Para auditar o que a Verda calculou,
alguém precisa abrir a tela.

**O que a doc acertou:** o request da `GetTransaction` é mesmo `TransactionKey` (a tabela da pg. 72
está certa, o texto da pg. 71 errado — mandar `TransactionId` devolve `Invalid 'StartDate'`); o
`LocalDateTime` em `AAAA-MM-DD HH:MM:SS` foi aceito; a resposta é plana, sem envelope `Return`; o
caminho é `RoadOrchestration`; e o processamento é mesmo assíncrono — a viagem voltou
`ainda processando` no POST e só depois virou `executed`.

`UTCDate` e `ExpiresOn` vêm em ISO com `T` e `Z` (`2026-09-01T11:29:59Z`), e o `_data_utc` já
normalizava isso.

---

## 6. Armadilhas do dado (leia antes de mexer)

Cada uma destas custou tempo e produz erro **silencioso**.

### O Power BI corta em 100.000 linhas sem avisar

`executeQueries` devolve `200 OK` com o resultado truncado. O `manifestos_ctrc` tem 392.602 linhas —
eu estava perdendo 75% e só percebi porque um relatório imprimiu 1.354 viagens quando deveriam ser
4.528.

Solução no `_verda_demo.py`: filtrar pela janela (`CHAVE_MANIFESTO IN VALUES(manifestos[...])`, dá
9.257 linhas) **e** a trava em `_dax()`, que mata o script se qualquer consulta bater no teto.

### A placa tem duas grafias e divide o mesmo veículo

Antiga × Mercosul, o 5º caractere (`5`↔`F`, `4`↔`E`, `0`↔`A`). No ValeCard cru dá 24 placas; com
`placas.mercosul()` são 19 veículos. Pior: uma grafia carrega os litros e a outra o km, então sem
normalizar um caminhão aparece com 11 km/l e outro sem distância nenhuma. **Sempre normalizar.**

### O ValeCard tem duas populações na mesma tabela

| | linhas | tem |
|---|---|---|
| transação de cartão | 767 | posto, cartão, hodômetro, distância |
| importação manual (tanque interno) | 560 | só os litros |

Somar `nsd_distancia` conta **litro completo contra km parcial** e distorce o consumo. O
`abastecido_por_placa()` usa o **método do hodômetro**: janela do primeiro ao último abastecimento com
hodômetro, `km = hodômetro final − inicial` e `litros = tudo da janela`. Isso consertou os dois
outliers (o `HGA9F47` saiu de 6,00 para 3,70 km/l) e **concorda com o método antigo onde o dado é
bom** — a assinatura de um método melhor.

### `rotas_km` devolve 0 para entrega dentro do mesmo município

Centroide contra o próprio centroide. São 704 itens. O caminhão rodou e `WaypointDistance` é
obrigatório — daí o `KM_ENTREGA_URBANA`.

### Manifesto e cadastro discordam sobre o dono

**152 viagens (3,4%)**: 89 em que o manifesto diz Rizza e o cadastro diz carreteiro, 63 no sentido
oposto. `e_frota_propria()` só aceita frota própria **quando as duas fontes concordam** — reivindicar
escopo 1 num veículo que o cadastro diz não ser da empresa é o que não se defende numa auditoria.

Caso concreto: o `OWH0F53` é de `EDVANIA SANTANA MENDES`, carreteiro, e o manifesto listava a Rizza.

### CTe de pessoa física não entra no payload

Existem 464 CTes com remetente pessoa física, **todos da série UDS** — remetente = destinatário =
pagador, 1 kg, mesma cidade. É lançamento administrativo, não transporte. **Nenhum está vinculado a
manifesto**, então nunca chegam ao payload. Não existe CPF em campo nenhum do que enviamos.

### O `cnpj` do `veiculos_045` guarda CPF também

62% CNPJ, 38% CPF zero-padded em 14 dígitos. `documento()` decide **pelo dígito verificador**, não
pelo tamanho — o padding destrói a pista do tamanho. (Hoje esse campo não é enviado, mas a função
está pronta se `IDENTIFICAR_PARCEIRO` for ligado.)

### Fontes que **não** servem

- `veiculos_045.media_min` / `media_max` (consumo): 2.939 dos 2.984 cavalos zerados
- `veiculos_045.capacidade` sozinho: 26% erram para baixo
- `conhecimentos_emitidos.co2`: existe e está **100% vazio** nos 345 mil registros. Vale perguntar ao
  SSW se eles calculam isso ou se já têm conector Verda — seria de graça.
- `abastecimentos_valecard`: tem 12 registros com **data futura** (set a dez/2026)

---

## 7. Pendências de cadastro (para o Camilo)

São as **201 viagens (4,4%)** que a validação bloqueia. Nada é problema de lógica.

| Problema | Viagens | O que fazer |
|---|---|---|
| Veículo **sem ano** no 045 | 175 | ~24 placas: `HDI1E60`, `QTQ1J07`, `DBM3I14`, `NFC9I04`, `DBM3J08`, `FJG9B62`, `DTE1F36`… |
| Veículo com tipo **`OUTROS`** | 8 | 2 placas: `DBM4F22`, `MSH6B15` (numa delas cavalo e carreta são a mesma placa) |
| CTe no manifesto que **não existe** no `conhecimentos_emitidos` | 17 | `CAR043355-1`, `GYN166838-2`, `UDI408650-3`, `UDS002842-8`… Conferir um no SSW: se for cancelamento, excluir do envio; se for defasagem, recarregar o ETL |
| CTe **sem cidade** de entrega | 17 | ficam como `? > ?`, sem como calcular km |
| CTe com **peso impossível** | 3 | `NOD006309-6` com **135.024 kg** — acima do PBTC de qualquer conjunto legal (rodotrem para em 74 t). Erro de digitação |

Resolvidas, a cobertura vai a ~100%.

**E uma para o BI:** o `cnpj` do `veiculos_045` foi publicado no dataset a pedido, mas o robô
definitivo deve ler o **Postgres do servidor** direto, não o Power BI. É processo operacional, roda no
mesmo servidor, tem as 62 colunas e não passa por DAX. O Power BI foi usado aqui só porque o `.env`
local aponta para `localhost` e a base local está meses atrás.

---

## 8. Pesquisa de referência

- **GLEC**: *"European articulated truck >32 t gross combined weight"* — peso bruto, explícito
- **DEFRA**: *"HGV (all diesel), articulated >33 t"*; rígidos em `>3,5–7,5`, `>7,5–17`, `>17` t GVW
- O **GLEC é a base da ISO 14083**, e é o que se usa no Brasil (a literatura nacional lista GHG
  Protocol, TMN, DEFRA e GLEC como os quatro métodos aplicáveis)
- **PBTC CONTRAN**: cavalo + carreta 3 eixos **45 t**; bitrem (7 eixos) **57 t**; rodotrem (9 eixos)
  **74 t**
- **Biodiesel**: B14 desde março/2024, **B15 desde 1º/agosto/2025 e ainda vigente**; B16 previsto para
  março/2026 e não cumprido; a lei prevê +1 p.p./ano até B20 em 2030
- **S10 × S500** é teor de enxofre (10 × 500 ppm); obrigatório S10 em veículos de 2012 em diante
  (Euro V / PROCONVE P7). **Não afeta CO₂e** e a Verda não separa os dois — só existe o código `diesel`
- O site da Verda cita **apenas** "metodologia do Protocolo GHG"; não menciona GLEC nem ISO 14083

---

## 9. Ao retomar

> **Produção roda no SERVIDOR, não na sua máquina.** O `verda_envios` mora no Postgres de lá e é
> fonte única — rodar local contra o banco local cria um estado paralelo, e no envio seguinte a
> Verda recusa tudo por `TransportationId` com transação viva, **sem dizer o motivo** (§13). Os
> comandos de servidor estão na [seção 18](#18-a-aba-verda-e-o-estado-do-deploy).

1. Abrir a aba **`/verda`** — é a leitura mais rápida do estado (§18).
2. `verda_job.py --resumo` — o mesmo no terminal, por ambiente. Atenção: `executed` em ambiente
   `teste` **não** está no inventário de produção; são contas diferentes.
3. `_verda_valida.py` — conferir que ainda aprova ~95,7% das que qualificam.
4. `verda_job.py --desde X --ate Y --so-montar` — monta sem tocar na rede. **Funciona em produção**
   sem `--sim-producao`, de propósito (§18.2).
5. Ver o que continua aberto: o agendamento semanal (§2) e as perguntas 4 e 6 da seção 4.

**Números de referência**, para perceber se algo mudou de forma estranha. Estes são os de HOJE, sob
a regra do CTRB (§16) e a classificação por carga útil (§17) — os antigos, de quando a viagem era o
CTe e a faixa era PBT, não servem de comparação:

```
viagens com CTe .......... 4.754      sem CTRB (não sobem) ..... 200
aprovadas ................ 4.358      = 95,7% das que qualificam
articulado_35 ............ 4.310      articulado_330 ....... 64 (9 carretas)
rigido_75 ................   157      rigido_170 ........... 16
km/l: articulado 2,20 / 1,80 / 1,50 por idade  |  rígido 3,70 fixo
inventário anual estimado ~5,9 mil t CO2e
```

---

## 10. Como ligar quando a Verda responder

**Passos 1 e 2 já foram feitos em 01/09/2026.** O `.env` está preenchido (gitignorado) e a primeira
viagem saiu e voltou `executed`. O que está no `.env`:

```ini
VERDA_AMBIENTE=teste            # teste | producao
VERDA_SIMULADO=0                # 1 = não sai da máquina
VERDA_APPLICATION_KEY=...       # homologação; produção tem chaves DIFERENTES
VERDA_SECRET_KEY=...
VERDA_VEHICLE_TYPE_KEY=veiculo_teste   # APAGAR em produção (ver abaixo)
```

### O que falta para produção

**1. Pedir a URL de produção ao Rafael.** Ele informou só que as credenciais mudam. Sem ela o
cliente se recusa a subir, com a mensagem certa. Chegando, vai em `VERDA_URL_PRODUCAO`.

**2. Cadastrar os tipos de veículo na plataforma.** É o passo que ninguém fez ainda e que **não é da
Verda, é nosso**: em produção valem "os tipos de veículos que vierem a ser cadastrados por vocês".
Enquanto isso, `VERDA_VEHICLE_TYPE_KEY=veiculo_teste` carimba o campo e a classificação real fica
registrada nos `avisos` da `verda_envios` — nada se perde, mas **a classificação não está sendo
exercitada de verdade em homologação**. Quando o cadastro existir, apagar a linha do `.env` e
conferir se os nomes batem com `FAIXAS_ARTICULADO` / `FAIXAS_RIGIDO` em `verda_veiculos.py`.

**3. Rodar o lote de homologação inteiro antes de pensar em produção.** Só 1 das 4.528 viagens
passou pela API real. O que o lote vai exercitar e a viagem única não exercitou: multi-entrega
(`WaypointOrder` > 0), `Weight` (as 8 sem tipo de veículo), `IsInbound=1`, e o teto de lote.

```bash
python -X utf8 verda_job.py --desde 2026-08-01 --ate 2026-08-31   # uma janela por vez
python -X utf8 verda_job.py --resumo
```

**4. Só então produção**, e o backfill:

```bash
python -X utf8 verda_job.py --desde 2026-01-01 --ate 2026-08-27 --sim-producao
```

Ao trocar `VERDA_AMBIENTE`, todas as viagens voltam a `pendente` sozinhas (a idempotência é por
ambiente) — não é preciso limpar nada. Depois, agendar o D-1 no Task Scheduler, no mesmo padrão dos
outros robôs da Rizza. O robô roda no servidor, junto com o worker de rastreamento — e lá **deve ler
o Postgres direto**, não o Power BI (seção 7).

> **Lixo do simulador na tabela local.** Sobraram ~4.500 linhas com ambiente `simulado` (600
> `executed` que nunca existiram na Verda). Elas não atrapalham — ficam fora do escopo de qualquer
> ambiente real —, mas se quiser limpar:
> `DELETE FROM verda_envios WHERE ambiente = 'simulado';`

### Respostas que mudam configuração, não código

| Resposta da Verda | O que mexer |
|---|---|
| as faixas são carga útil, não PBT | `BASE_FAIXA = 'carga_util'` em `verda_veiculos.py` |
| o fator do diesel deles não embute o biodiesel | `ENVIAR_RENEWABLE_SHARE = True` em `verda_payload.py` |
| `VehicleUtilization` é taxa de ocupação | `VEHICLE_UTILIZATION` — e aí passa a depender da carga, vira função |
| precisam identificar o transportador parceiro | `IDENTIFICAR_PARCEIRO = True` |
| precisam do CPF do motorista | `MOTORISTA = 'cpf'` |
| os `VehicleTypeKey` da conta são outros | as faixas em `FAIXAS_ARTICULADO` / `FAIXAS_RIGIDO` |

---

## 11. Primeiro lote real — agosto/2026

Rodado em 01/09/2026, em dois lotes de 300 (o `TETO_LOTE` barrou os 597 de uma vez, como devia).

```
montadas          597        bloqueadas   15        sem payload   2
enviadas          597        aceitas     597        recusadas      0
conferidas        597        executed    597        rejected       0
tempo             ~1,05 s/viagem no POST | ~18 min ponta a ponta
```

**Zero rejeições em 597 viagens.** O validador local (`_verda_valida.py`) está calibrado: o que ele
aprova, a Verda aceita. As 15 bloqueadas são a pendência de cadastro da seção 7 (13 sem
`VehicleModelYear`, 2 sem `WaypointDistance`), não defeito de lógica.

**O que o lote exercitou:** multi-entrega (126 viagens, uma com **110 itens**), `IsInbound=1` (104),
escopo 1 (102), e o volume. **O que NÃO exercitou: a API `Weight`** — as 597 são todas `Fuel`, porque
as 8 viagens sem tipo de veículo caem em outros meses. Continua sendo a única parte não testada.

### O número

Previsto pelo fator reconstruído (2,28429 kg CO₂e/L) **antes** de olhar o dashboard:

| | |
|---|---|
| distância | 446.777 km (748 km/viagem) |
| peso | 8.547 t |
| transporte | 7.793 mil t·km |
| diesel implícito | 275.585 L |
| **emissão** | **629,52 t CO₂e** |
| intensidade | 80,78 g/t·km |
| escopo 1 / escopo 3 | 122,80 t (19,5%) / 506,72 t (80,5%) |

> **Armadilha ao recalcular:** `WaypointDistance` é **por parada**, não por item. Somar todos os itens
> infla a distância em 3× (dá 1.316.431 km em vez de 446.777). Somar por `WaypointOrder` distinto.

### O que o dado diz

**A ineficiência de carbono é ocupação, não distância.**

| carga | viagens | km médio | t CO₂e | g/t·km |
|---|---|---|---|---|
| até 5 t | 133 | 200 | 37,09 | **596,2** |
| 5 a 10 t | 79 | 941 | 107,60 | 186,3 |
| 10 a 15 t | 77 | 882 | 96,41 | 112,6 |
| 15 a 20 t | 101 | 874 | 123,52 | 78,9 |
| acima de 20 t | 207 | 916 | 264,89 | **56,0** |

Uma viagem leve emite **10× mais por tonelada transportada** que uma cheia. **36% das viagens levam
menos de 10 t: fazem 23% da emissão e só 8,2% do t·km útil.** É a conversa de eficiência que dá para
ter com o embarcador — e é o argumento que justifica o ESG Básico E2, onde a análise por viagem
existe.

Ressalva honesta: parte dessas viagens leves é distribuição urbana, que no setor é reconhecidamente
mais intensiva que transferência de longa distância. Comparar as duas na mesma régua exagera o
problema.

**Concentração:** as 10% maiores viagens fazem 24,7% da emissão; as 50% maiores, 78,1%. Não há um
punhado de outliers dominando — a emissão é distribuída, e reduzir exige mexer na operação, não em
meia dúzia de casos.

**Exposição ao `KM_ENTREGA_URBANA` (o piso de 25 km):** 115 viagens (19,3%) usam o piso em alguma
parada, mas elas somam só **3,1% da emissão**. Mesmo que o km urbano real seja o dobro, o total sobe
3,1%. A premissa mais frágil do modelo tem impacto pequeno — bom saber antes de alguém questionar.

**Extrapolação anual:** ~7,5 mil t CO₂e no total, ~1,5 mil t de escopo 1. Continua **abaixo das 10
mil tCO₂e** da Lei 15.042 — confirma que o gatilho é a Nestlé, não a lei.

### Validação contra o relatório da plataforma

A Verda exporta um Excel com **uma linha por viagem** (`Transportation`, `Distance`,
`Energy Consumption`, `Weight Index`, `CO 2e Total`, `CI Distance`…). Cruzado com os nossos payloads,
597 de 597:

| | previsto por nós | relatório da Verda | erro |
|---|---|---|---|
| viagens | 597 | 597 | — |
| distância | 446.777,1 km | 446.777,08 km | 0% |
| peso | 8.547,0 t | 8.547,00 t | 0% |
| **CO₂e** | **629,52 t** | **629,51 t** | **0,0008%** |

**Zero divergência viagem a viagem** em distância, peso e consumo. Nenhuma duplicada (597 linhas,
597 `Transportation` distintos). `CO 2e Total Avoided` = 0, como esperado com `RenewableShare` nulo.

O `CI Distance` do relatório é constante por faixa de consumo — 1,522859 kg/km para 1,5 km/l, que é
exatamente `(1/1,5) × 2,28429`. **Terceira confirmação independente do fator.**

> Isso significa que dá para **prever o inventário antes de enviar**, e conferir depois linha a linha.
> Não é preciso confiar na caixa-preta: para a `Fuel`, ela está reconstruída.

### Duas coisas para vigiar

**1. O cancelamento some do relatório, mas NÃO do painel.** As duas viagens sintéticas foram
canceladas (`StatusKey: canceled`) e **não aparecem no Excel** — mas o painel agregado continuava
contando-as: mostrava 104 viagens / 123,64 t de escopo 1, contra 102 / 122,80 t reais. A diferença é
exatamente as duas (884,0 km, 41,13 t, 0,849 t CO₂e).

Ou é o mesmo atraso de consolidação do dashboard, ou o cancelamento não limpa o consolidado.
**É o risco que motivou a trava de `--sim-producao`**: na conta gratuita só fica o consolidado
mensal, então uma transação errada em produção suja um número que ninguém consegue inspecionar
depois. Reconferir o painel algumas horas após um cancelamento antes de concluir qualquer coisa.

**2. Uma transação em `internal_error`.** A conta tem 602 transações: 597 viagens + 2 de cancelamento
(o `CancelTransaction` **cria uma transação nova**, que fica `executed`) + 2 viagens canceladas + **1
`internal_error`** (`20260901120510-rURXvjf9ToyfiIH`), que não corresponde a nenhuma viagem no nosso
estado e não está no relatório. Sem impacto no inventário, mas pode ser sinal de um POST processado
duas vezes do lado deles. Se aparecerem mais, investigar o retry.

> Detalhe que confunde a contagem: **cada cancelamento vira uma transação `executed` própria**. Contar
> `executed` na API não dá o número de viagens.

### As abas do painel: `Consolidado` / `Upstream` / `Downstream`

| aba | o que traz | agosto/2026 |
|---|---|---|
| **Consolidado** | **todas as viagens** — é o que o botão de exportar sempre entrega | 597 · **629,51 t** |
| **Upstream (entrada)** | as viagens com `IsInbound=1` | 104 · 3,81 t |
| **Downstream (saída)** | as demais | 493 · 625,70 t |

O relatório Excel **é sempre o Consolidado**, independentemente da aba aberta (três exportações de
abas diferentes deram arquivos de conteúdo idêntico). Para analisar upstream/downstream, filtrar o
Consolidado por `IsInbound`.

> **Como isso quase virou um erro caro.** Cruzando um print do painel com o relatório, os números
> batiam ao milésimo com o escopo 1 em seis medidas simultâneas — e concluí que a plataforma exibia
> só a frota própria, entregando à Nestlé 18 t em vez de 180 t. Errado duas vezes: primeiro por
> ignorar que havia abas; depois por insistir que `Upstream` era "operação própria" quando a
> contagem (104) coincidia por acaso com o `IsInbound`. **Reconciliação perfeita responde "que fatia
> é esta?", nunca "esta é a única fatia que existe"** — e quando dois recortes diferentes dão a mesma
> contagem, é a métrica que desempata, não o número de linhas.

### As 104 viagens do Upstream são lançamento administrativo

Levantadas a pedido do Gabriel, que notou serem todas de 25 km. A assinatura é inequívoca:

| | |
|---|---|
| série do CTe | **UDS em 104 de 104** |
| rota | **UBERLANDIA/MG > UBERLANDIA/MG** em 104 de 104 |
| remetente × destinatário | **iguais** (é o que produz `IsInbound=1`) |
| peso | **300 kg exatos em 102 das 104** (as outras 2 têm ≤ 100 kg, uma delas 1 kg) |
| distância | **25 km em todas — que é o nosso `KM_ENTREGA_URBANA`, não dado real** |
| emissão | 3,81 t de 629,51 t (**0,61%**) |

É o mesmo padrão que a seção 6 já documenta para os CTes de pessoa física ("todos da série UDS,
remetente = destinatário = pagador, 1 kg, mesma cidade — lançamento administrativo, não
transporte"). A diferença é só que **estes têm remetente PJ**, então passaram pelo filtro existente,
e estão vinculados a manifesto.

O peso de 300 kg repetido em 98% das linhas é valor padrão, não medição.

**O argumento decisivo:** a emissão dessas viagens é **100% artefato nosso**. O `rotas_km` devolve 0
(mesmo centroide), e é o piso de 25 km que cria os 3,81 t. Não estamos medindo transporte com
premissa conservadora — estamos inventando transporte que não aconteceu. O viés conservador do
projeto vale para *como medir algo que existe*; não autoriza *incluir o que não existe*.

**Se a decisão for tirar, o filtro NÃO é "25 km".** Há 5 viagens legítimas de 25 km — entregas
metropolitanas curtas com carga real (CONTAGEM > BETIM com 16,8 t; SENADOR CANEDO > GOIANIA com
15,0 t e 14,5 t; CONTAGEM > BELO HORIZONTE com 7,5 t; e uma UBERLANDIA > UBERLANDIA com 8,8 t e
remetente ≠ destinatário). O critério correto é a **combinação**: série UDS + remetente =
destinatário + origem = destino. Filtrar por distância derrubaria 62 t de carga real.

Impacto de excluir as 104: 629,51 → **625,70 t** (−0,61%); intensidade 80,78 → 80,30 g/t·km.

**Pendente de decisão do Gabriel** (01/09/2026). Se sair, são dois passos: o filtro na montagem **e**
o `CancelTransaction` das 104 já enviadas em homologação.

---

## 12. Regras novas de 01/09/2026 (lançamentos administrativos e km)

Três mudanças decididas pelo Gabriel depois de ver o painel:

### 1. Lançamento administrativo não entra no inventário

`verda_payload.e_lancamento_administrativo()` — série **UDS** + remetente = destinatário + origem =
destino. Em agosto pega **109 viagens** (104 que já tinham sido enviadas + 5 que já estavam
bloqueadas). O job as marca `fora_escopo` e **cancela na Verda** se já foram enviadas (etapa EXPURGO).

O critério é a combinação dos três sinais. **Filtrar por "25 km" seria errado**: há entrega
metropolitana legítima abaixo disso (CONTAGEM > BETIM com 16,8 t) e cairiam 62 t de carga real.

### 2. `KM_ENTREGA_URBANA` virou substituto, não piso

Era `max(perna, 25)`, o que **sobrescrevia distância real menor que 25 km**: CONTAGEM > BELO
HORIZONTE tem 16,62 km e ia como 25 (+50%). Agora só entra quando a perna é zero **e** o destino é o
próprio município de origem. Entre municípios diferentes, perna zero vira `None` e o validador barra
— melhor a viagem parar do que inventar km.

### 3. Cascata de distância com o km do CTe

```
1. rotas_km                    a base de referência
2. conhecimentos_emitidos.distancia_km   só para cidades DIFERENTES
3. Haversine × 1,30            último recurso
```

O km do CTe é bom dado: contra `rotas_km` em 1.203 pares de junho/26, **erro mediano de 0,6 km** e
correlação 0,946. Melhora as 59 viagens de agosto cuja rota não existe em `rotas_km` e caíam na
estimativa geométrica.

**Nunca para viagem intramunicipal:** ali o campo traz **541 km fixos** (224 de 225 CTes na amostra),
que multiplicaria a emissão por 22 e somaria ~78 t falsas. E `rotas_km` devolvendo 0 **não é dado
faltando** — é o valor registrado para o par (47 pares, todos de mesma cidade).

Efeito nas 597 de agosto: 65 viagens mudaram de distância, **+0,50% no km total**. As longas
diminuíram (JAPERI > FORTALEZA: 2.828 → 2.590 km, o Haversine superestimava) e algumas subiram
(RIO PARDO > SÃO BERNARDO: 1.178 → 1.295 km).

### ⚠ Defeito introduzido e corrigido no mesmo dia: a PK não incluía o ambiente

A coluna `ambiente` foi criada com a PK ainda em `transportation_id` sozinho. Ambiente virou
atributo, não identidade — então **rodar em modo simulado sobrescreveu as linhas de homologação**,
apagando o `transaction_id` de **20 viagens** que continuavam vivas no inventário da Verda. Ficaram
órfãs: sem como cancelar, e prontas para duplicar no próximo envio.

Corrigido: **PK composta `(transportation_id, ambiente)`**, com migração no DDL, e o `SELECT` do
`registrar` passou a filtrar por ambiente (sem isso a linha de outro ambiente decidia o destino
desta). As linhas dos ambientes agora coexistem, e toda a lógica de "zerar ao trocar de ambiente"
deixou de ser necessária.

> **A lição:** separar ambientes é mudar a **identidade** do registro, não acrescentar uma coluna.
> Enquanto a chave não incluir o ambiente, um ambiente continua podendo apagar o outro — e o dano
> aparece longe, quando o vínculo já se perdeu.

**Recuperação das 20 órfãs:** o `CancelTransaction` valida o par
(`TransactionKey` × `TransportationId`) e devolve `Success: false` sem efeito quando erra — dá para
usá-lo como oráculo e descobrir o mapeamento por tentativa, sem risco de cancelar a viagem errada.
Feito em `_match_tmp.py`. As transações são canceladas e as viagens voltam a `pendente` para reenvio
limpo.

---

## 13. A regra que muda o desenho: `TransportationId` é de uso único

Descoberta em 01/09/2026, quando 61 viagens voltaram `rejected` — as primeiras rejeições em mais de
600 envios.

**A Verda recusa `TransportationId` que já tenha transação ATIVA.** Provado por experimento: o mesmo
payload, recusado com o id original, foi aceito e executou ao trocar só o `TransportationId`. O
cancelamento **libera** o id (as viagens cujas transações foram canceladas antes reenviaram sem
problema).

A rejeição vem **sem `ErrorDetail`** — lista vazia, nenhum campo culpado. Não há como diagnosticar
pela resposta; só correlacionando com o que já foi enviado.

### O furo que isso expôs no job

A decisão de arquitetura "viagem alterada é cancelada e reenviada" estava certa na intenção e
incompleta na implementação: **só as marcadas `alterada` eram canceladas**. Mas o `hash` não é o que
determina se existe transação viva. Uma viagem montada numa execução (`--so-montar`) e enviada em
outra chega ao envio como `inalterada` — e ainda assim carrega o `transaction_id` de uma transação
ativa. Foi exatamente isso: a montagem imprimiu `alterada: 0`, o job não cancelou nada, e as 61
foram recusadas.

**Corrigido:** o `enviar()` agora cancela a transação anterior de **qualquer** viagem que chegue à
fila com `transaction_id` preenchido, e **pula a viagem se o cancelamento falhar** — em vez de mandar
por cima. O `a_enviar()` passou a trazer o `transaction_id` junto para isso ser possível.

> A regra prática: **antes de enviar, garanta que não existe transação viva para aquele id.** Não
> confie no hash, não confie no status — olhe o `transaction_id`.

### Consequência para produção

Uma viagem corrigida **só pode ser reenviada depois do cancelamento confirmado**. Se o
`CancelTransaction` falhar, não adianta insistir no envio: a Verda vai recusar. E como a recusa vem
sem detalhe, o robô ficaria reenviando às cegas — daí a trava de pular a viagem.

### Recuperação de vínculo perdido (o oráculo)

Usada duas vezes no mesmo dia, e vale guardar: quando o `transaction_id` se perde mas a transação
continua viva, o **`CancelTransaction` serve de oráculo** — ele valida o par
(`TransactionKey` × `TransportationId`) e devolve `Success: false` **sem efeito colateral** quando
erra. Dá para reconstruir o vínculo por tentativa, sem risco de cancelar a viagem errada.

Isoladas as candidatas pela janela de horário, o acerto foi de **100% na primeira tentativa** nas
duas vezes (19/19 e 61/61), porque a ordem de envio (`ORDER BY data_viagem, transportation_id`)
corresponde à ordem cronológica das transações. **Mas confirmar sempre pelo oráculo** — a correlação
temporal sozinha já se mostrou enganosa neste projeto.

---

## 14. PENDÊNCIA ABERTA: expedidor/recebedor × remetente/destinatário

Levantada pelo Gabriel em 01/09/2026, e **não resolvida**. Pode invalidar o filtro da seção 12.

O CTe tem quatro figuras, e o `conhecimentos_emitidos` traz as quatro:

| campo | quem é |
|---|---|
| `cnpj_remetente` / `cnpj_destinatario` | as partes **fiscais** (quem vendeu, quem comprou) |
| `cnpj_expedidor` / `cnpj_recebedor` | quem **entrega ao transportador** e quem **recebe dele** |

**Hoje usamos remetente/destinatário** — no `ShipperKey` e na regra do `IsInbound`.

> Os campos de expedidor/recebedor aparecem 100% preenchidos, mas isso **não** quer dizer que sempre
> existam: quando não são informados, **o SSW repete o remetente e o destinatário** (informação do
> Gabriel). Os casos reais são os 14% em que expedidor ≠ remetente e os **44%** em que
> recebedor ≠ destinatário.

### O que já está certo

**A distância.** A `cidade_entrega` já segue o **recebedor**, não o destinatário. Exemplo de junho/26:
destinatário em RIO DAS OSTRAS, recebedor "MERIO TRANSPORTES" no RIO DE JANEIRO, `cidade_entrega` =
RIO DE JANEIRO. É redespacho — a Rizza entrega ao parceiro no Rio e outro transportador faz a última
perna. **Medir até a `cidade_entrega` é o correto**: é o trecho que a Rizza rodou. Usar a cidade do
destinatário contaria km de viagem que não é nossa.

### O que está em aberto

**O filtro de lançamento administrativo pode estar derrubando entrega urbana real.** As 109 excluídas
de agosto têm todas o mesmo `ShipperKey` — **18.485.037/0001-12 (MARTINS)** — e rota
UBERLANDIA > UBERLANDIA. Se o **expedidor** é a Martins e o **recebedor** é o cliente final, houve
movimento físico: seria entrega urbana do CD, não lançamento fiscal.

Trocar o par muda bastante: em junho/26 o `IsInbound=1` sai de **269** (por remetente=destinatário)
para **124** (por expedidor=recebedor) — os dois critérios **divergem em 9,4% dos CTes**.

**O que sustenta manter como administrativo:**
- peso de **300,0 kg exatos em 107 das 109** (e 1,0 kg nas outras duas) — valor idêntico sem
  nenhuma variação é campo padrão, não medição;
- na amostra de junho, os UDS de mesma cidade pesam **1,0 kg**;
- `rotas_km` afirma 0 km para o par, e o `distancia_km` do CTe traz os 541 km fixos.

**O que sustenta o contrário:** em 98 dos 222 CTes UDS de junho o expedidor difere do recebedor —
sinal de que alguém entregou algo a alguém.

### Como resolver

1. **Conferir 2 ou 3 CTes no SSW** (`UDS004245-5`, `UDS004325-7`, `UDS004271-4`): é entrega física ou
   acerto fiscal? Isso decide sozinho.
2. Se for entrega real, o filtro precisa usar **expedidor ≠ recebedor** em vez de
   remetente = destinatário, e as viagens voltam ao inventário — com o problema de km ainda em aberto
   (o modelo de centroide não mede rodado urbano).
3. Vale perguntar ao Rafael se o `ShipperKey` que ele espera é o remetente (parte fiscal) ou o
   expedidor (quem entrega a carga) — a regra do `IsInbound` na pg. 28 depende dessa definição.

**Enquanto não se decidir, o impacto é pequeno:** as 109 valem 3,81 t de 628,16 t (0,61%). Mas a
resposta muda o `IsInbound` de ~9% dos CTes, e é isso que alimenta as abas Upstream/Downstream.

---

## 15. A API `Weight` medida pela primeira vez (01/09/2026)

Até aqui a `Weight` nunca tinha sido exercitada — era a única parte do robô sem contato com a API real,
e o único fator ainda sem medição. Enviamos **a mesma viagem pelos dois métodos**, mudando só a API:

```
NOD004785-6   Fuel     442 km · 20.566,898 kg · 2,2 km/l   →  458,93 kg CO2e   1,038 kg/km
ZZWEIGHT-001  Weight   442 km · 20.566,898 kg · sem consumo →  615,41 kg CO2e   1,392 kg/km   (+34,1%)
```

A `Weight` funciona: aceita e `executed` na primeira tentativa, sem `FuelConsumption`,
`FuelTypeKey`, `VolumeUnitKey`, `RenewableShare` nem `ItemIndexUnitKey`; com `WeightUnitKey` e
`ItemWeight` no lugar de `ItemWeightIndex`. (A viagem de teste foi cancelada em seguida.)

### O achado não é o +34%, é a inversão

**O fator da `Weight` é FIXO por km** (1,392 kg/km); o da `Fuel` varia com a idade do veículo. O sinal
da diferença muda ao longo da frota:

| faixa | Fuel kg/km | Weight | diferença | viagens (ago) |
|---|---|---|---|---|
| até 5 anos (2,2 km/l) | 1,038 | 1,392 | **+34,1%** | 60 |
| 6 a 10 anos (1,8 km/l) | 1,269 | 1,392 | +9,7% | 81 |
| acima de 10 anos (1,5 km/l) | 1,523 | 1,392 | **−8,6%** | 352 |

A `Weight` pune o caminhão novo e **beneficia o velho** — que é 82% da operação. Um fator médio por
tipo não sabe que aquele veículo tem 15 anos.

**No agregado os dois métodos quase empatam:** o mês inteiro pela `Weight` daria **621,55 t** contra
**628,16 t** pela `Fuel` — **−1,1%**.

> Isso é um bom argumento de defesa: a escala de consumo por idade definida pela diretoria **não
> infla nem desinfla** o inventário em relação ao método alternativo da própria Verda. Os dois
> caminhos chegam praticamente ao mesmo lugar por caminhos independentes.

### Ressalvas

- O fator medido é o do **`veiculo_teste`**, tipo fictício de homologação — **não** é o do
  `articulado_330`. **Repetir o teste depois de cadastrar os tipos reais** na conta.
- O fator da `Weight` equivale a um consumo de **1,641 km/l**, e a média ponderada da nossa escala é
  **1,630 km/l**. Semelhança notável, mas tratada como **coincidência** até prova em contrário — este
  projeto já produziu três conclusões erradas a partir de números que "batiam demais".

### Por que a `Weight` nunca é usada hoje

Com a escala por idade, todo veículo de tipo conhecido tem km/l, então **tudo vai pela `Fuel`** — as
503 viagens no ar são `Fuel`, zero `Weight`. As viagens que cairiam na `Weight` são as sem tipo de
veículo no cadastro, mas a `Weight` **também exige `VehicleTypeKey`**: elas são barradas como
`sem_payload` (2 em agosto). Na configuração atual, **a `Weight` é código que nunca executa**.

Ela volta a importar se a pergunta virar de negócio: *estimar km/l para caminhão de terceiro é
defensável numa auditoria?* Hoje aplicamos a mesma escala para frota própria e agregado, e 80% da
emissão é de veículo que nunca medimos. A medição acima diz que a troca mudaria pouco no total
(−1,1%), mas redistribuiria bastante entre as faixas de idade.

---

## 16. REGRA NOVA: a viagem é o CTRB, não o CTe (01/09/2026)

Investigação a pedido do Gabriel, que perguntou se o robô tratava viagens com **mais de um manifesto
e mais de um CTRB**. Tratava não — e isso escondia **dois erros grandes em direções opostas**.

### O erro que existia

O robô usava origem/destino do **CTe** (`cidade_origem_prestacao` → `cidade_entrega`) para calcular a
distância. Mas o CTe descreve o **percurso comercial**; quem descreve o **trecho rodado** é o CTRB.

**1. Transbordo (superestimava).** 15,5% dos CTes aparecem em mais de um manifesto. Cada manifesto
declarava o percurso inteiro:

```
CTe NOD007324-5, tres veiculos, tres datas:
   05/08 CNI1I88   JUNDIAI/SP > BRASILIA/DF   970 km
   07/08 QQA7E43   JUNDIAI/SP > BRASILIA/DF   970 km      2.910 km declarados
   08/08 AVH6F10   JUNDIAI/SP > BRASILIA/DF   970 km      para um percurso de 970
```

Em 96% dos casos repetidos a distância era **idêntica** — prova de que era replicação, não trecho.

**2. Falsos "administrativos" (subestimava, muito pior).** O filtro da seção 12 excluía 109 viagens
por serem série UDS + remetente = destinatário + mesma cidade. **Eram viagens reais de longa
distância** — média de **1.023 km** (Duque de Caxias > Hidrolândia, Cariacica > Hidrolândia).

A causa: o `tipo_documento` dos UDS é **"SUBC REC FORM LISO"** — *subcontratação*. Nesses documentos
os campos de cidade trazem a **unidade emissora**, não o percurso, e o peso de 300 kg é campo padrão
do formulário. Prova interna: **224 CTes UDS têm cidade_origem = cidade_entrega E distancia_km = 541**
ao mesmo tempo.

> **A lição de método:** somei três indícios (série UDS + remetente = destinatário + mesma cidade)
> sem notar que **os três vinham da mesma fonte defeituosa** — o CTe, que não descreve percurso. Três
> sinais correlacionados não são três confirmações. E o erro passou despercebido porque os dois erros
> se cancelavam em parte: fosse só o transbordo, o número teria caído e pareceria uma correção limpa.

### A fonte certa: `Auditoria Receita`

A tabela `Auditoria Receita` (Power BI, a mesma da aba Auditoria) traz, por manifesto: `CTRB`,
`Manifesto`, `CTRC`, `Todos CTRCs da Viagem`, `cidade_uf_origem`/`destino` (o **trecho**),
`cidade_uf_origem_tarifa`/`destino_tarifa` (o **percurso comercial**), `distancia_km`,
`manifesto_rateado` e `Tipo Operacao`.

**Proveniência verificada: o `distancia_km` vem 100% do CTRB** (`ctrbs_oss`) — 110 de 110 idênticos
em origem, destino e km. Isso importa: **o CTRB é o documento que paga o motorista**, então a
distância dele tem consequência financeira (frete/km, vale-pedágio, piso ANTT) e é auditada por
natureza. O centroide de município não tem ninguém conferindo.

### Testes que a fonte passou

| teste | resultado |
|---|---|
| proveniência do km | **100%** do CTRB |
| confirma a nossa distância onde não há transbordo | **93%** com diferença < 5% |
| corrige nos **dois** sentidos | sim — aumenta onde subestimávamos (Feira de Santana: +776 km) |
| consistência do rateio | 38 grupos de transbordo, **todos** com soma coerente com o percurso |
| cobertura | **97,4%** das viagens |

O terceiro é o que mais convence: se ela só reduzisse, seria suspeita de artifício para baixar o
número.

### As cinco decisões (Gabriel, 01/09/2026)

1. **Janela = `data_ref_ctrc`**, a mesma da Auditoria Receita (resolve os manifestos de agosto com
   CTe de julho).
2. **Sem CTRB, não sobe.**
3. **Uma parada só, origem → destino direto.** Confirmado que a AR trata assim — não há paradas
   intermediárias em nenhuma das 12 viagens multi-parada. Elimina o rateio de km entre paradas.
4. **Proprietário pela regra do BI** (`Tipo Operacao`), que analisa o proprietário do CTRB. Substitui
   o `e_frota_propria()` de dupla confirmação. As 9 viagens da placa `OWH0F53` passam a escopo 1.
5. **Ler do Power BI.** A `Auditoria Receita` é dado **tratado** e não existe no Postgres — isto
   **revoga** a orientação da seção 7 de ler o Postgres direto. Dependência operacional: o robô passa
   a depender do dataset publicado e atualizado, e do teto de 100 mil linhas do `executeQueries`
   (a trava do `_dax()` continua obrigatória).

### Simulação completa (`_sim_regra_nova.py`)

```
571 viagens validas | 12 bloqueadas (VehicleModelYear, pendencia de cadastro) | 2 sem tipo | 1 sem km
486.480 km | 8.218 t | 690,78 t CO2e | escopo 1: 121,28 t (17,6%) | 1.553 itens
```

Integridade: **zero** duplicados, zero sem itens, zero com parada ≠ 1, zero com km ≤ 0, zero
`ShipperKey` vazio, zero peso ≤ 0.

**Transbordo resolvido:** dos 46 CTes que aparecem em mais de uma viagem, **42 agora têm km
diferente** (trecho real) contra 4 idênticos — e esses 4 são legítimos (dois veículos fazendo a
**mesma rota completa**, divisão de carga, não transbordo sequencial).

**36% das viagens ficam inalteradas** — as diretas, onde não havia erro. A correção é cirúrgica.

| | viagens | km | t CO₂e |
|---|---|---|---|
| hoje no ar | 493 | 446.405 | 628,16 |
| **regra nova** | **571** | **486.480** | **690,78** |

**Estávamos subestimando ~10%.**

### O que o código perde (simplificação)

Somem: `KM_ENTREGA_URBANA`, `FATOR_RODOVIARIO`/Haversine, a cascata de fontes de km,
`ordenar_waypoints`, `e_lancamento_administrativo` e `e_frota_propria`. Tudo isso vira **um lookup na
`Auditoria Receita`**.

---

## 17. Produção: as decisões de 10/09/2026

Dia de virar a chave. O Gabriel decidiu cinco coisas, três delas corrigindo erro real.

### 17.1 Credenciais e conta — produção é OUTRA conta

A URL é **a mesma da homologação**; o que muda é o par de chaves. Testado, não suposto:

```
chave da aplicação   c60c6c4f-b487-4b2a-bbc9-daa19cd578f6
chave secreta        39bdccfb-51ad-4e00-aaeb-93e9e807e486
```

> Uma chave solta **não** autentica. A primeira que chegou (`b68885c4-…`) foi testada como
> aplicação, como secreta e como as duas: `Invalid 'Authorization' key` nas três. O header exige
> o par.

**A conta é separada, e isso foi provado:** um `GetTransaction` de duas transações conhecidas da
homologação resolve `executed` com a chave antiga e volta **vazio** com a nova. Ou seja, o inventário
de produção nasce limpo — as 574 viagens de agosto que estão em homologação **não** contaminam
produção, e não há nada para reconciliar antes de começar.

Os cinco `VehicleTypeKey` **já estão cadastrados** na conta de produção, com os nomes que o código
gera (`articulado_35`, `articulado_330`, `rigido_35`, `rigido_75`, `rigido_170`). Some a pendência da
§10: `VERDA_VEHICLE_TYPE_KEY` sai do `.env`.

### 17.2 A faixa é CARGA ÚTIL, não peso bruto

`BASE_FAIXA = 'carga_util'`. A leitura anterior era PBT, por pesquisa (DEFRA e GLEC cortam em peso
bruto). O que ela produzia na frota real derruba a pesquisa: somando 27 t de piso + 17 t de tara,
**todo** articulado brasileiro passava de 33 t — **122 das 125 viagens da semana iam como
`articulado_330`**, ou seja, declarávamos a frota inteira como bitrem/rodotrem. E a faixa
`articulado_35` virava gaveta impossível de usar no Brasil.

> Categoria que nunca recebe nada é sinal de régua errada, não de frota atípica.

Pela carga útil a faixa diz o que o operador entende: **carreta é carreta até 33 t, trucada ou
simples**, e acima disso é conjunto pesado. Semana: 119 `articulado_35`, 3 `articulado_330`,
3 `rigido_75`.

Continua valendo perguntar ao Rafael. A resposta troca uma constante e **não altera o CO₂e** — na
`Fuel` a emissão é litros × fator e o `VehicleTypeKey` não entra na conta.

### 17.3 O cadastro não pode promover carreta a conjunto pesado

`TETO_PLAUSIVEL['CARRETA'] = 33.0` (era 50). Só **evidência** promove acima de 33 t.

Medido: das 284 viagens que subiam para `articulado_330`, **220 (77%) subiam só pelo campo
`capacidade`** — carretas comuns declaradas com 34 a 40,5 t que nunca carregaram mais de 24 t. Com o
teto em 33 sobram **64 viagens em 9 carretas**, e as três que mais aparecem batem com a lista de
rodotrem da Rizza. É a §3.4 levada até o fim: o cadastro é o elo mais fraco, e aqui ele estava
decidindo sozinho.

### 17.4 Placas declaradas de conjunto pesado

```python
PLACAS_CONJUNTO_PESADO = {'TYO9J49', 'TYO9J56', 'TYN8I98', 'TYN8I60'}
```

A detecção por peso só funciona **depois** que o veículo rodou (5 viagens para o p90, ou uma carga
acima de 33 t). Conjunto novo rodando leve passava por carreta comum. A lista age como **piso** — o
peso real continua valendo quando for maior.

Duas armadilhas de manutenção:

- **4 placas, 2 conjuntos.** Rodotrem é 2 semirreboques + dolly, e o manifesto grava **uma** placa
  só. Por isso a `TYN8I60` tem **zero viagens** na base inteira: a parceira é sempre a gravada. Não
  é erro de dado — e é por isso que as duas precisam estar na lista.
- **Não dá para tirar do cadastro.** No dataset publicado as quatro estão como `carroceria=CARRETA`,
  `capacidade=30,00`, `eixos=3` — a palavra RODOTREM não existe lá. Em 6.889 veículos há 3 `BITREM`,
  1 `DOLLY` e 2 `VANDERLEIA`. Enquanto o SSW não publicar a configuração do conjunto, a lista é
  mantida à mão.

> Isto responde a pergunta certa do Gabriel — "peso é uma coisa, tipo de veículo é outra". A resposta
> não é trocar peso por modelo (o modelo mente exatamente nos rodotrens): é **declarar**, e deixar o
> peso como detector do que não está declarado.

### 17.5 Rígido tem consumo próprio — estávamos inflando 121,8 t/ano

`CONSUMO_RIGIDO = 3.70`. A escala por idade (2,20/1,80/1,50) foi calibrada na frota própria, que é de
cavalos, e estava sendo aplicada **também aos rígidos**: 158 das 173 viagens de truck iam com
**1,50 km/l** — consumo de conjunto de 45 t num veículo que carrega 5,6 t em média.

O próprio arquivo já se contradizia: `FAIXA_KML` põe o piso do TRUCK em **2,5 km/l**, e o código
atribuía 1,5.

Medição do ValeCard (método do hodômetro), os dois únicos trucks com histórico: `HGA9F47` =
**3,70 km/l** (27.048 km / 7.313 L) e `OWH0F53` = 3,96.

| rígidos (173 viagens, 140.320 km) | litros | t CO₂e | efeito no inventário |
|---|---|---|---|
| escala do articulado (antes) | 91.213 | 208,4 | — |
| **3,70 km/l** | **37.924** | **86,6** | **−2,05%** |

> **Ressalva de método, registrada porque foi levantada e decidida contra:** 3,70 é média de
> **ciclo** (inclui a volta vazia), enquanto o articulado usa valor **carregado**, deliberadamente
> abaixo do ciclo medido (§3.3). Aplicando o mesmo desconto, o truck daria ~2,64 km/l — e a diferença
> entre as duas hipóteses é 0,6% do inventário. O Gabriel optou por 3,70, que é a média que a Rizza
> já tinha combinado internamente.

### 17.6 Duas correções de robô

**O filtro de ambiente que faltava.** `marcar_enviado` e `marcar_erro` atualizavam por
`transportation_id` **sem** `ambiente` — a lição da PK composta (§12) só tinha sido aplicada no
`registrar`. Com produção e homologação na mesma tabela, **21 viagens de 31/08 existem nos dois
ambientes**: o envio de produção carimbaria seu id em cima da linha de homologação, deixando a
transação de lá órfã (viva e sem como cancelar) e a linha de teste apontando para outra conta.

**O expurgo estava morto.** A lista `expurgar` era criada e nunca preenchida; `fora_de_escopo()`
nunca era chamada por ninguém. Consequência: viagem que **perdia** o CTRB (ou o CTe, ou o tipo de
veículo) era pulada em silêncio e ficava na Verda para sempre, contando emissão. Agora os três
caminhos de "não sobe" passam por `_sair_do_escopo()`, que marca e devolve o `transaction_id` a
cancelar quando ele chegou a existir.

> Marcar no nosso banco não tira nada do inventário deles. Enquanto a transação estiver viva, a
> emissão conta.

### 17.7 O lote de estreia (sexta, 11/09)

Janela **31/08 a 06/09** (segunda a domingo), rodando na sexta seguinte — 5 dias de folga para o CTRB
consolidar.

```
na janela      126        OK p/ enviar   119        bloqueadas   6 (VehicleModelYear)
sem CTRB         1        CTes         359        tempo      ~4 min ponta a ponta
distancia  111.323 km     peso     1.915,6 t      diesel   67.592 L
EMISSAO      154,40 t CO2e          escopo 1  22 viagens   IsInbound  32
tipos: articulado_35 113 | articulado_330 3 | rigido_75 3
```

As 6 bloqueadas são 4 placas sem ano: `HDI9E22` (3 viagens), `DTE1F36`, `IJJ4D59`, `DBM5I14`.

**O `verda_envios` mora no Postgres do servidor**, não na máquina local — decisão do Gabriel. Se o
primeiro lote rodasse local e o job automático subisse depois, o servidor não saberia o que já foi
enviado e a Verda recusaria as 119 por `TransportationId` com transação viva, **sem dizer o motivo**
(a rejeição vem sem `ErrorDetail`, §13).

### 17.8 O que a validação geral diz agora

```
viagens com CTe .......... 4.754      sem CTRB (não sobem) ..... 200
APROVADAS ................ 4.358      = 95,7% das que qualificam
bloqueios: VehicleModelYear 178 | ShipperKey 10 | VehicleTypeKey 7 | peso implausível 3
```

Mesmo perfil da §7 — nada novo quebrou.

---

## 18. A aba `/verda` e o estado do deploy

### 18.1 A aba

Permissão `verda`, concedível no Admin (**não** vem liberada por padrão). Arquivos:
`verda.html` (tela), `verda_painel.py` (leitura e agregação), rota `/verda` + `/api/verda` no
`server.py`, entrada no `nav-perms.js`.

**A tela não chama a Verda.** Lê só a `verda_envios`: o payload enviado está gravado em JSONB e o
CO₂e é recalculado aqui com o fator reconstruído (`FATOR_CO2E = 2.28429`).

> Isso não é atalho — é o que a torna possível. Na conta gratuita a Verda guarda **só o consolidado
> mensal, sem detalhe de viagem**. Para o dado por viagem, esta tela é o único lugar onde ele existe.
> E o fator foi conferido contra o relatório oficial deles em 597 viagens, erro de 0,0008% (§11).

O que mostra: placar da rodada · inventário (t CO₂e, km, peso, diesel, intensidade g/t·km, escopo
1 × 3) · consumo aplicado por faixa de km/l com a média ponderada · `VehicleTypeKey` · as placas que
estão **bloqueando** viagem, agrupadas e com botão de copiar · detalhe por viagem com drill nos CTes
e CSV.

Alertas automáticos: viagem presa em `enviado` sem veredito há mais de 2 h (foi o sintoma do bug do
`TransactionKey`, §5), rejeitada, e viagem fora de escopo com transação ainda viva.

**A janela padrão é a semana fechada anterior** (segunda a domingo) — o mesmo recorte do robô. Abrir
a tela sem filtro mostra o lote que acabou de subir.

**Nomenclatura: usar a da Verda, sempre.** A tela mostra o **código** (`articulado_330`) e põe a
descrição textual da própria plataforma no tooltip. Cheguei a apelidar (`rigido_75` → "truck") e o
Gabriel derrubou com razão: na Verda `rigido_75` é faixa de **peso** (7,5 a 17 t) e "truck" é
configuração de eixos — duas coisas diferentes com o mesmo nome, na mesma tela. Quem lê a tela
precisa poder procurar o código na conta da Verda e achar.

O card de **consumo** é o mais importante da tela: é o parâmetro mais sensível do inventário e o que
o freio de anomalia vigia. Se a média ponderada sair do lugar, alguma coisa mudou no cadastro e o
inventário inteiro se move junto.

### 18.2 `--so-montar` funciona em produção, de propósito

A trava de `--sim-producao` barrava o `--so-montar`, e ele é justamente o comando que **não fala com
a Verda** (lê o Power BI, grava pendente no nosso banco, pula o expurgo). O efeito era o inverso do
pretendido: para conferir o lote antes de mandar, era preciso usar a flag de envio real.

> A trava existe para que ir a produção seja **ato deliberado**. Não para impedir de olhar antes.

### 18.3 Deploy — os comandos

O `docker-compose.yml` **não está no repo**: a stack vive só dentro do Portainer. Por isso as
credenciais entram por `docker service update --env-add`, que altera o *service spec* sem tocar na
imagem nem abrir o editor de stack — e **é abrir o editor de stack que faz o serviço voltar para a
imagem antiga** neste ambiente.

```bash
# 1. build + imagem + credenciais num comando só (reinicia UMA vez)
cd /opt/stacks/rizza-auditoria && git pull && \
docker build -t ghcr.io/ggabrielmilho-web/rizza-auditoria:latest . && \
docker service update --force \
  --image ghcr.io/ggabrielmilho-web/rizza-auditoria:latest \
  --env-add VERDA_AMBIENTE=producao \
  --env-add VERDA_SIMULADO=0 \
  --env-add VERDA_URL_PRODUCAO=https://personal-d33vvevh.outsystemscloud.com \
  --env-add VERDA_APPLICATION_KEY=<chave da aplicação> \
  --env-add VERDA_SECRET_KEY=<chave secreta> \
  --env-rm VERDA_VEHICLE_TYPE_KEY \
  rizza-auditoria_app

# 2. conferir — e o que NÃO pode aparecer é o VERDA_VEHICLE_TYPE_KEY
docker exec $(docker ps -q -f name=rizza-auditoria) printenv | grep VERDA

# 3. ensaio, sem tocar na rede
docker exec $(docker ps -q -f name=rizza-auditoria) \
  python -X utf8 verda_job.py --desde 2026-08-31 --ate 2026-09-06 --so-montar

# 4. envio real
docker exec $(docker ps -q -f name=rizza-auditoria) \
  python -X utf8 verda_job.py --desde 2026-08-31 --ate 2026-09-06 --sim-producao
```

> **`VERDA_VEHICLE_TYPE_KEY` sobrando é o erro que passa despercebido.** Ele carimba o campo e toda
> a classificação por carga útil (§17.2) deixa de chegar à Verda — o envio funciona, o número fica
> certo, e o tipo do veículo vai errado sem nenhum aviso. Conferir sempre no passo 2.

Duas coisas normais neste fluxo, que assustam à toa:

- o `docker build` avisa *"image could not be accessed on a registry to record its digest"* — é
  esperado, a imagem é construída no próprio nó e não vai para o registry. Só importaria com mais de
  um nó no cluster.
- a `verda_envios` **nasce sozinha**: o job chama `garantir_tabela()` antes de tudo. Não há DDL na
  mão, e a aba aguenta a tabela não existir (mostra "nenhuma viagem nesta janela" em vez de erro).

**Cuidado com o `--env-add`:** ele grava no service spec, não na stack. No dia em que alguém
reimplantar a stack pelo Portainer, as variáveis somem e a Verda para de autenticar. Para tornar
permanente: pôr no YAML da stack, implantar, e **em seguida** rodar de novo o `--image ... --force`
para trazer a imagem de volta ao `latest`.

### 18.4 Estado em 10/09/2026, fim do dia

| | |
|---|---|
| código no servidor | ✅ imagem construída e serviço convergido |
| credenciais de produção | ✅ no service spec, `VERDA_VEHICLE_TYPE_KEY` ausente |
| ensaio no servidor | ✅ **119 novas · 6 bloqueadas · 1 sem CTRB** |
| envio real | ⬜ **combinado para 11/09/2026** |
| agendamento semanal | ⬜ pendente, precisa estar de pé antes de 18/09 |

As 6 bloqueadas são 4 placas sem ano no cadastro: `HDI9E22` (3 viagens), `DTE1F36`, `IJJ4D59`,
`DBM5I14`. Preenchido o ano no 045, elas entram sozinhas na rodada seguinte — a aba lista e copia.
