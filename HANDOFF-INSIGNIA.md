# Handoff — Insignia GR (2ª fonte de rastreamento)

**07/10/2026 (tarde) — leia a §15**: dump de hoje, 3S CONGELADA (o tapa-buraco não pega), duas falhas do
motor que não são da Insignia (conclusão travada na chegada: 43 de 45 prematuras; saída de metrópole) e o
que acontece depois do FIM DE VIAGEM. Quatro chaves novas de laboratório, todas desligadas, E0 idêntico.

**Estado em 07/10/2026 — ⚠ COMECE PELA §14 (ONDE PARAMOS).** Em produção só a COLETA está no ar
(`e5d90bc`); nada no app lê a Insignia. A leitura (`fontes_gps.py`), o terceiro no robô, o encerramento
pela SM, o odômetro e o tapa-buraco estão prontos e medidos em laboratório, **no working tree, sem commit**.

**Antes disso — 05/10/2026: coleta NO AR em produção** (`e5d90bc`, `INSIGNIA_COLETA=true` pela CLI).
1ª rodada: 20 SMs, 19 placas rastreadas, 6.549 paradas desde 05/09, 20 rotas, 500 locais. Próximo
passo combinado: deixar acumular histórico alguns dias; **depois** o motor (§6).
Leitura do log: o contador "posições" da linha do log conta só as gravadas pela consulta de
posição/odômetro — a posição que vem junto da SM é gravada antes, com o mesmo instante, e não
conta de novo. O total real está no `--resumo`.

## 1. O que é

A Insignia é a **gerenciadora de risco** das cargas da Rizza. Toda carga com SM (solicitação de
monitoramento) passa por ela: **os terceiros** — que a 3S não vê — e frota/agregado conforme o
valor da carga. Para frota e agregado ela lê o rastreador do **cavalo** (Autotrac); a 3S lê o da
**carreta**: são duas testemunhas independentes do mesmo caminhão.

## 2. A API (o que não está na documentação)

- SOAP `http://sigma.insigniagr.com/insigWebService/IntegraGR.wso`, namespace `http://tempuri.org/`,
  login (usuário + senha + token) no corpo. Doc: `sigma.insigniagr.com/insigWebcliente/documentacao-insignia/`.
- **Unidade de negócios = CNPJ da Rizza Transp `02572512000158`.** Até 02/10 a conta apontava para
  uma base de TESTE (ER0044/ER0021 em tudo); a Insignia trocou para produção em 05/10.
- **Placa com hífen** (`AXT-6E87`); sem hífen não encontra.
- **Horários em Brasília.** Gravamos em UTC (+3 h), como `embarques_posicoes_historico`.
- **14 das 23 operações são de ESCRITA** (cancelar/finalizar viagem, solicitar monitoramento…).
  `insignia.chamar()` recusa tudo que não começa com `Get_`. Não remover.

| consulta | serve? | o que traz |
|---|---|---|
| `Get_ConsultaVeiculoEmViagem` | ✅ | as SMs ABERTAS: placas, motorista, valor, origem/destino com lat/lng, operações, última posição, **última macro** |
| `Get_ConsultaTempoParado` | ✅ | **cada parada**: início, fim, duração, local em TEXTO (sem lat/lng). Até 30 dias por chamada |
| `Get_ConsultaShapeViagem` | ✅ | a ROTA PLANEJADA da SM (sem horário) + pontos obrigatórios (PTOBRIG) |
| `Get_ConsultaPosicaoOdometro` | ✅ | posição, ignição, odômetro (unidade varia por tecnologia — cru) |
| `Get_ConsultaCNPJ` | ✅ | 500 locais da GR com ponto, raio (mediana 800 m) e polígono (25) |
| `Get_ConsultaSM` | ❌ | só SM aberta pela própria API |
| `Get_ConsultaDisponibilidade` / `Get_ConsultaModelo` | ❌ | checagem p/ abrir SM / catálogo de marcas |

**Limites medidos:**
- `dDh_Chegada`/`dDh_Saida` dos pontos da SM vêm **zerados**; NF-e (`DocFiscal`) vazia. O evento que
  existe é a **macro do motorista** (CHEGADA NO CLIENTE, INÍCIO/REINÍCIO DE VIAGEM, PARADA
  PERNOITE/EVENTUAL, ABERTURA DE BAÚ), e a API devolve só a **última** — a sequência sai do polling.
- Só há dado de veículo que **teve SM** (entrega local pequena, como a C-2026-001221, não tem).
- A API só mostra SMs abertas e só alcança 30 dias de paradas → **sem coleta, o dado some**.

## 3. A validação (05/10, lab `rizza_lab_0930`)

Paradas ≥ 1 h da Insignia (cavalo) × carreta na 3S no mesmo intervalo, 14 cavalos em setembro:

| como | concordância |
|---|---|
| **par cavalo × carreta tirado da CARGA da época, horário BRT** | **96% (96/100)** |
| mesmo par, horário lido como UTC | 41% |
| par de hoje para o mês inteiro | ~58% |

O cavalo troca de carreta: **parear sempre pela carga** (a mesma lição da §4.1 do HANDOFF-EMBARQUES).

Cobertura nas cargas desde 01/10: 41 de 102 placas com posição (agregado 23/49, terceiro 11/39,
frota 7/14). SMs abertas em 05/10: 22 (7 terceiro · 9 agregado · 6 frota); posição mediana de 6 min.

## 4. A coleta (`insignia.py` + `insignia_coleta.py`)

Thread do servidor atrás de `INSIGNIA_COLETA` (molde da fita documental: falha nunca derruba o app).

| a cada | o quê | tabela |
|---|---|---|
| **60 s** | SMs abertas (ficha + payload bruto), status, macro, última posição | `insignia_sm`, `insignia_sm_operacoes`, `insignia_sm_eventos`, `insignia_macros`, `insignia_posicoes` |
| **60 s** | posição/odômetro de cavalo E carretas das SMs abertas | `insignia_posicoes`, `insignia_placas` |
| SM nova | rota planejada (pontos + polyline precisão 5, lida por `ors_client.decodificar_polyline`) | `insignia_sm_rota` |
| 60 min/placa | paradas desde a última busca; **30 dias** na 1ª vez | `insignia_paradas` |
| SM some da lista | `encerrada_em` + busca final de paradas da viagem inteira | `insignia_sm`, `insignia_paradas` |
| 1×/dia | cadastro de locais | `insignia_locais` |

**Cadência medida (05/10, 10 min consultando a cada 30 s, 19 placas):** a posição muda a cada
**5 min** no Autotrac (12 placas) e no Onixsat (4), e a cada **2 min** no Omnilink (3). Por isso o ciclo é
de **60 s** (a mesma cadência do worker da 3S): pega todo ponto que o aparelho manda. Com 5 min o
Omnilink perderia ~60% dos pontos — e, sem histórico de posições na API, o ponto perdido não volta.

Placa gravada **como a Insignia manda** (`placa`) + chave Mercosul (`placa_chave`) só para cruzar.

**Testado no banco local (05/10):** 1ª rodada 153 s — 20 SMs, 19 placas rastreadas (22 sem
rastreio na GR, quase todas carretas), **6.533 paradas desde 05/09**, 20 rotas (média 1.529 km),
500 locais, 19 macros. 2ª rodada **3 s**, 1 mudança de status, nada duplicado.

## 5. Subir

```bash
# 1) deploy normal (git pull + build + service update). Nasce DESLIGADA — conferir no container:
CT=$(docker ps -q --filter "name=rizza-auditoria_app"); docker exec $CT grep -c "def chamar" insignia.py
# 2) credenciais + ligar, pela CLI (NUNCA pelo stack do Portainer — §22.10):
docker service update --env-add INSIGNIA_USER=... --env-add INSIGNIA_SENHA=... --env-add INSIGNIA_TOKEN=... \
    --env-add INSIGNIA_CNPJ_UNIDADE=02572512000158 --env-add INSIGNIA_COLETA=true rizza-auditoria_app
# 3) conferir: log "✅ Coleta Insignia LIGADA" e, depois de ~3 min, o resumo
CT=$(docker ps -q --filter "name=rizza-auditoria_app"); docker exec $CT python -X utf8 insignia_coleta.py --resumo
```

Volta: `INSIGNIA_COLETA=false` (as tabelas ficam; nada no app as lê).

## 6. O motor — ONDE PARAMOS (05/10/2026) e como começar

**Não é preciso esperar dias para começar.** A coleta já trouxe o que o motor mais usa:

| já disponível | serve para |
|---|---|
| **30 dias de paradas** (6.549 desde 05/09, backfill da 1ª rodada) | saída, chegada no cliente, entrega — o insumo principal |
| fichas das SMs abertas (placas, motorista, valor, origem/destino com lat/lng) + rota planejada | casar SM ↔ carga; desvio de rota |
| 500 locais da GR (ponto, raio, polígono) | âncora curada |

| precisa acumular (só existe desde 05/10) | por quê |
|---|---|
| posições | a API não tem histórico de posição |
| sequência de macros do motorista | a API só devolve a última |
| ciclo completo da SM (abrir → encerrar) | para validar o "encerrou" |

**Plano combinado (começar em 06/10), na ordem:**
1. **Casar SM ↔ carga** — cada SM com a carga do robô pela placa (cavalo/carreta) e pela data, inclusive
   os **terceiros**, que hoje ficam fora do robô (`EMBARQUES_AUTO_TIPOS=Frota,Agregado`). Medir: quantas
   cargas têm SM, quantas SMs não têm carga, quantos terceiros entram.
2. **Eventos pelas paradas** — saída do carregamento, chegada no cliente, saída do cliente, medidos no
   laboratório contra as cargas reais dos últimos 30 dias (mesmo método do HANDOFF-EMBARQUES §28:
   régua aditiva, campos separados, replay ao vivo, bateria de falhas). Parear cavalo × carreta
   SEMPRE pela carga da época (96% assim; ~58% com o par de hoje).
3. **2ª testemunha para frota/agregado** — quando a carreta dorme/congela na 3S (§28.10–28.11), a
   parada do cavalo na Insignia preenche. Testável com setembro.
4. Posição e macros entram na validação conforme acumulam (macro "CHEGADA NO CLIENTE" como evento
   declarado para medir a régua "no cliente").

**Primeiro passo de amanhã:** dump de produção COM as tabelas da Insignia → laboratório novo
(`rizza_lab_1006`, banco NOVO, nunca por cima), receita da HANDOFF-EMBARQUES §27.14:

```bash
PG=$(docker ps -q -f name=postgres); docker exec $PG pg_dump -U postgres -d rizza_auditoria -Fc --no-owner --no-privileges -t 'embarques_*' -t 'insignia_*' -t 'municipios_ibge' -t 'locais_fontes' -t 'locais' -t 'fita_documentos' -f /tmp/emb_20261006.dump && docker cp $PG:/tmp/emb_20261006.dump /tmp/ && ls -lh /tmp/emb_20261006.dump
```

Trazer para `C:\Phyton-Projetos\` (fora de qualquer repositório — dado de cliente).

Material já pronto para reaproveitar: `_lab_ancora.py` (régua, defesas D2b/D3, bateria de falhas),
`_lab_replay.py` (relógio de gravação), `_lab_saida.py` (janela quando o aparelho congela), e a
validação Insignia × 3S do §3.

## 7. Primeira análise com dado de produção (06/10/2026, lab `rizza_lab_1006`)

Dump de 06/10 13:30 UTC: 27 SMs, 4.591 posições (~20 h de coleta), 11.988 paradas desde 05/09,
85 macros. Scripts em `_estudo_2026-10-06/` (só leitura): `insignia_cruzamento.py` (SM × carga ×
manifesto), `insignia_evidencia.py` (cadência, destino, 2ª testemunha, status, macros),
`insignia_locais_ancora.py` (cadastro de locais da GR como âncora).

| achado | número |
|---|---|
| SM ↔ manifesto | **26/27** casam (cavalo/carreta ± 2 d); 19 são carga do painel (12 Agregado · 7 Frota), **7 terceiros** |
| CNPJ de destino da SM × da carga | igual em **100%** das que têm os dois |
| a GR só rastreia o CAVALO | 27/27 carretas `ER0022` (sem rastreio na GR) |
| cadência da posição | Autotrac p50 4 min · Onixsat 5 · Omnilink 2 · **4 buracos > 30 min em 20 h, 21 placas** |
| cavalo (GR) × carreta (3S) | quando os dois falam: p50 0,9 km; **23% a > 5 km** (posição velha da 3S, §28.14, ou desengate) — parear pela carga |
| cobertura extra | **76%** dos pontos da GR não têm ponto da 3S a ≤ 5 min; 2 cargas com a carreta MUDA o período inteiro (C-1341, C-1364) |
| macro CHEGADA NO CLIENTE | a 0,1–0,3 km do destino da SM em 7/8; bate com a nossa chegada em **±0,2 h** nas 4 que temos chegada. C-1341 (`Em rota`) e C-1364 (`Aberta`) **já chegaram** pela macro |
| status da SM | **não serve para encerrar**: 3 SMs "Em Cliente" com a carga Entregue há dias; SM aberta desde 28/09 |
| destino da SM ≠ destino da carga | C-1313: carga Uberlândia (CTRB = hub), SM 703 km adiante; FIM DE VIAGEM a 1,7 km do destino da SM |
| paradas | só TEXTO de referência ("0.19 KM O DE POSTO BRASAO, RJ"), sem lat/lng — a posição é melhor daqui para frente |

**Âncora de destino pelo cadastro de locais da GR** (a pergunta da §28.2 do HANDOFF-EMBARQUES):
100 dos 156 CNPJs de destino das nossas cargas estão no cadastro (ponto + raio), **235/312 cargas
desde 01/09 (75%)**. Em 196 cargas entregues com GPS: a carreta parou ≥ 10 min a **≤ 0,5 km do
ponto em 71%**, ≤ 2 km em 80%, ≤ 5 km em 87% — contra 54% / 69% do Maps com endereço bom (§28.2).
Por CNPJ: 46 certo em todas, 13 com exceções, 16 nunca (vários são carga que não chegou ou
destino errado: Mateus Santa Isabel a 1.679 km é a C-662 já conhecida). Ponto da GR → centroide:
p50 5,3 km, p90 24,8 km. **É candidato à 1ª camada da âncora (antes do T1 aprendido).**

Limites: posições e macros são de ~1 dia; 27 SMs é amostra pequena — só a âncora tem amostra
grande (196 cargas).

## 8. Liberar TERCEIRO no robô com a Insignia como fonte — avaliação (06/10/2026)

Ideia do Gabriel: nenhuma regra muda; o robô passa a abrir Terceiro e, para ele, a posição vem da
Insignia em vez da 3S. Scripts: `_estudo_2026-10-06/terceiro_viabilidade.py`, `terceiro_fechamento.py`.

| pergunta | resposta medida |
|---|---|
| volume | 194 manifestos de terceiro desde 01/09 (30%), **≈ 5,7/dia**; 191 com CTRB (o robô cria) |
| terceiro tem SM (= posição na Insignia)? | **4 de 19** manifestos de 02–06/10 (Frota 4/11, Agregado 13/34 — a SM segue o valor da carga, não o tipo). Bate com o teste de 05/10 (11/39 placas de terceiro com posição) |
| a SM nasce antes da saída? | sim: em 6 de 7 SMs novas o 1º ponto está a ≤ 0,3 km da origem — a saída é observável |
| depois da entrega a posição continua? | quase sempre (SM fica "Em Cliente" 5–45 h), MAS quando a GR fecha no FIM DE VIAGEM a posição para em minutos (2 de 3) |
| fechamento documental | **75% dos manifestos de terceiro nunca têm o seguinte da mesma carreta** (119 de 146 carretas aparecem uma vez) — a rede "manifesto novo da mesma carreta" não existe para terceiro |
| odômetro | unidade por tecnologia: Autotrac ≈ ×100 (10 m), Omnilink ≈ ×1.000 (m), Onixsat ≈ km com ruído → gravar NULL ou normalizar |

Consequência: com as regras de hoje, terceiro SEM SM (≈ 80%) nasce `Aberta` e **nunca sai** (sem GPS
e sem manifesto seguinte). Terceiro com SM fecha pelas regras atuais, exceto quando a GR encerra a
SM logo no FIM DE VIAGEM (fica `No destino`).

Pontos de código para "trocar a fonte" sem mexer em regra: posição da Insignia nas MESMAS tabelas
(`embarques_posicoes_historico/atuais` + linha em `embarques_veiculos_rastreio` com id sintético), só
placa que a 3S não rastreia, marcada pela fonte; filtrar a fonte no backfill da 3S
(`_veiculos_para_backfill`) e no PGR (`pgr.py` lê todas as placas do histórico).

## 9. Laboratório E0/E1 — a camada `fontes_gps` e o terceiro com SM (06/10/2026)

**Código (working tree, NÃO commitado):** `fontes_gps.py` (nova) — cada fonte no seu lugar, uma camada
de leitura: `historico(cur)` / `atuais(cur)` devolvem a tabela da 3S com a chave desligada, ou a união
3S + Insignia com ela ligada (`EMBARQUES_FONTE_INSIGNIA`, escopo `terceiro`|`todas`); preferência POR
PLACA (placa da 3S é só 3S); odômetro da Insignia NULL. Trocados os leitores que decidem carga: worker
(`_placa_tracking` → `placa_rastreada`, posição atual, histórico, KPI), motor, aferidor,
`_chegou_ao_destino`, detecção de saída (`geocoding`), continuação, mapa da carga, lista e mapa geral.
PGR, backfill, consolidação diária e cadastro da 3S NÃO mudaram (a Insignia não entra neles).
Robô: Terceiro em `EMBARQUES_AUTO_TIPOS` só nasce com SM (`fontes_gps.tem_sm`). Worker: cidade NULL
não conta como "mudou de cidade" (sem isso o raio de saída cairia de 30 para 5 km na Insignia).

Lab: `rizza_lab_1006_ins` (gabarito), `_a` (controle) e `_b` (terceiro + Insignia), scripts em
`_estudo_2026-10-06/lab/`.

| gate | resultado |
|---|---|
| **E0** chave desligada × código de antes | motor e aferidor **idênticos byte a byte**; `_teste_rastreado_via` 9/9, `_teste_ciclo_transacao`, `_teste_gatilho` 15/15 |
| **E1** cargas existentes | **0 de 1.350 mudaram** entre controle e braço B |
| **E1** terceiro | robô criou **7** (com SM) e barrou ~114 manifestos-rodada sem SM; motor convergiu na 1ª passada |
| mapa da carga / mapa geral | rastreado pelo cavalo via Insignia, trajeto desenhado; terceiro aparece no mapa geral ligado à carga |

As 7, uma a uma (coleta da Insignia começou 05/10 14:55 BRT — 6 delas já estavam em viagem):

| carga | resultado | leitura |
|---|---|---|
| C-1382 | saída 06/10 04:18, `Em rota` | **o caso de regime**: SM criada no carregamento, saída provada |
| C-1379, C-1381 | `No destino`, SM aberta | fecham pelas regras de hoje (24 h / saída) enquanto a SM durar |
| C-1376, C-1378 | `No destino`, SM **encerrada** | **ficam presas**: sem ponto depois, nem "saiu" nem "24 h" disparam |
| C-1377 | `Aberta`, SM encerrada com FIM DE VIAGEM 4 min depois do 1º ponto | **presa para sempre** |
| C-1380 | `Aberta` em viagem a 140 km do destino | nasceu antes da coleta: saída não observável; vira `No destino` ao chegar |

Problemas achados:
- **P1 (transitório)**: 6 de 7 com `V1` e chegada carimbada no 1º ponto da coleta — viagem que começou
  antes de 05/10. Não se repete com SM nova (6 de 7 SMs novas têm o 1º ponto na origem, §8).
- **P2 (estrutural)**: **SM encerrada corta a evidência** — 3 de 7 presas. Saídas: continuar consultando
  a posição da placa ~48 h depois da SM fechar (se a API devolver posição sem SM — testar) ou tratar
  FIM DE VIAGEM / SM encerrada depois da chegada como encerramento documental (regra nova, motivo
  próprio, `entregue_auto = FALSE`).
- **P3 (pré-existente, exposto)**: a lista mede o rastreio pela carreta do texto
  (`COALESCE(carreta1, cavalo)`), não pela placa rastreada: terceiro sai `sem_posicao` e sem idade, e o
  alerta não dispara. Vale também para agregado cuja carreta não tem 3S.
- Rotas ORS não foram traçadas no lab (`EMBARQUES_AUTO_MAX_ROTAS=0`); `KM FALTANDO` sai vazio.

## 10. Encerramento pela SM + E2 (só Insignia, com gabarito da 3S) — 06/10/2026

**Decisão do Gabriel:** carga rastreada pela Insignia que fica MUDA depois que a GR encerra a SM
conclui no horário do **FIM DE VIAGEM** (sem a macro, no último ponto antes do encerramento); a que
continua posicionando fecha pela regra de hoje (saiu do destino / 24 h lá). FIM DE VIAGEM é o
motorista encerrando a viagem NO destino (3/3 a 0,3–1,8 km) — não é entrega.

**Teste da API (06/10, servidor):** depois do encerramento, LPX-4J71 seguiu devolvendo posição
(sem SM nova); DPB-4G53 e ESU-8J86 → `ER0121`. Ter 30 dias de paradas não garante posição pós-SM.

**Código (working tree):** `fontes_gps.encerramento_sm` + chave `EMBARQUES_SM_ENCERRAMENTO` (exige a
fonte ligada); regra no motor depois das de GPS, só com o cavalo como sensor e **nunca em perna vazia**
(o lab pegou a V-2026-000299 pela SM da C-1305 — corrigido); macro de chegada no destino vale como
chegada (`no_local_fonte = macro_insignia`); motivo `sm_encerrada`, `entregue_auto` FALSE. Aferidor
na mesma régua (F1d, sem F3). Coleta segue o cavalo por `INSIGNIA_POS_SM_H` (48) depois da SM — é o que
separa "continua posicionando" de "muda" em produção. E0 refeito contra o código do último commit
(worktree): motor e aferidor idênticos.

**E2** (`rizza_lab_1006_c1/c2`): nas 19 cargas F/A com SM a 3S foi ESCONDIDA e tudo zerado; o motor
decidiu só com a Insignia; gabarito = braço A (3S completa). Convergiu na 1ª passada.

| | |
|---|---|
| evento visto pelas duas fontes | saída idêntica em 2 (C-1366, C-1375); chegada a +0,1 h e −0,3 h (C-1276, C-1338) |
| a Insignia viu, a 3S não | C-1341 chegada (carreta muda na 3S); C-1363 saída; C-1364 saída + chegada |
| divergência a olhar no mapa | saída da C-1361 (+30,5 h) e C-1365 (+26 h): a SM nasce no carregamento com o cavalo na origem um dia DEPOIS da "saída" da carreta na 3S |
| não comparável | ~10 cargas: o evento foi antes da coleta (05/10 14:55) ou depois do dump (C-1340) |
| regra de encerramento (C2) | concluiu 4: C-1377 (chegada pela ABERTURA DE BAÚ 11:14, FIM 15:08), C-1376, C-1378 (último ponto), C-1305; C-1379/C-1381 seguem `No destino` com a SM aberta (regra de hoje) |

Limite: 1 dia de posição da Insignia. Refazer E1/E2 com o dump de ~13/10, só com SMs nascidas depois
de 05/10 (observadas inteiras).

## 11. O km da Insignia validado contra a 3S (06/10/2026)

`_estudo_2026-10-06/km_insignia_x_3s.py` (saída em `.txt` ao lado). Referência = odômetro da 3S, no
período em que as duas fontes falam (05/10 17:55 → 06/10 13:30 UTC), pares com ≥ 20 km.

| medida | mesmo cavalo nas duas fontes (n=6) | cavalo GR × carreta 3S da carga (n=3) |
|---|---|---|
| 3S GPS (como hoje) | mediana −2,4% · pior −7,6% | −5,7% · pior −6,1% |
| **Insignia GPS** (KM PERCORRIDOS) | **−6,8%** · pior −10,3% | −4,7% · pior −8,3% |
| **Insignia odômetro Autotrac ÷ 100** (KM RASTREADOR) | **+4,0%** · pior +9,0% | **+0,9%** · pior −3,8% |

- Autotrac: odômetro em **centenas de metros** (÷ 100), viés consistente de ~+4% contra a 3S. Os 9
  pares são todos Autotrac (os únicos cavalos com as duas fontes).
- Omnilink (÷ 1.000): NWD4H03 +1,7% contra o próprio GPS, mas **AXT6E87 com odômetro CONGELADO**
  (0 km de odômetro com 531 km de GPS). Onixsat (km): INF2D07 −3,9%, **ARM8A51 −26,8%**. Não ligar
  essas duas sem mais dado — odômetro congelado também confunde a régua de posição falsa
  (`perna_impossivel` usa o odômetro como árbitro em salto ≥ 30 km).
- A Insignia mediu onde a 3S estava muda: C-1363 (carreta sem ponto) 203 km de GPS / 211 de odômetro.
- Proposta: odômetro só **Autotrac ÷ 100** na fase 1 (cobre os cavalos de frota/agregado); Omnilink e
  Onixsat seguem NULL (o card cai no GPS, −5 a −7%).

**CORREÇÃO (06/10, mais tarde) — o Onixsat não era o problema, era a régua.** O odômetro do Onixsat
vem em km e certo, mas manda leituras **zeradas** no meio da série (139128 → 0 → 139132); o teste
descartava a queda e a volta e perdia o trecho real. Sem os zeros: ARM8A51 +1,9% e INF2D07 +8,5% contra
o próprio GPS (≈ +3% contra o real, o mesmo padrão do Autotrac). Zeros aparecem nas TRÊS tecnologias
(Autotrac 4, Omnilink 44, Onixsat 30 em 20 h). Omnilink em metros (NWD4H03 +1,7%); o AXT6E87 manda
um valor CONSTANTE (1043208320) andando — defeito do aparelho, não da tecnologia.
Os dois casos são perigosos fora do km: `embarques_regua.perna_impossivel` declara falso o salto
≥ 30 km em que o odômetro andou < 50% da distância — zero e odômetro travado quebrariam trecho real.
Regras para o adaptador (`fontes_gps`): converter (Autotrac ÷ 100, Omnilink ÷ 1.000, Onixsat × 1);
leitura 0 vira NULL; placa com odômetro que não varia (≤ 2 valores distintos em ≥ 20 pontos) fica
sem odômetro.

## 12. Odômetro da Insignia ligado no laboratório (E3, 06/10/2026)

**Código (working tree):** `fontes_gps._sql_odometro`, atrás de `EMBARQUES_FONTE_INSIGNIA_ODOMETRO`
(nasce desligada): Autotrac ÷ 100, Omnilink ÷ 1.000, Onixsat × 1, em km inteiros como a 3S; leitura 0 →
NULL; tecnologia desconhecida → NULL; placa marcada `insignia_placas.odometro_travado` → NULL. A marca é
da coleta (`insignia_coleta.marcar_odometro`, 1×/rodada: ≥ 20 pontos a > 10 km/h nas últimas 24 h e
≤ 2 valores distintos de odômetro), para a camada de leitura não varrer histórico a cada consulta.

| teste (lab `rizza_lab_1006_c2`, endpoint real do mapa, 26 cargas) | resultado |
|---|---|
| marcação de travado | só o AXT6E87 — o caso certo |
| KM RASTREADOR nas cargas da Insignia | aparece; ~+3 a +8% sobre o km de GPS da própria Insignia (o GPS subestima) |
| cortes de posição falsa, odômetro desligado × ligado | 0 × 0 — zero e travado não contaminaram a régua |
| km de GPS, desligado × ligado | idêntico nas 26 |
| motor (dry-run) com o odômetro ligado | 0 mudanças |
| E0 (chaves desligadas) × último commit | motor e aferidor idênticos |

Artefato do E2 (não é defeito): carga com chegada lê o KPI consolidado de `embarques_cargas_rastreio_kpi`,
que no clone ainda era o da 3S (C-1338: 1.309 km) — a sanidade comparou o odômetro da Insignia com ele
e barrou. Em produção o worker grava esse KPI já pela camada de fontes.

## 13. Tapa-buraco por TEMPO (E4, 06/10/2026)

**Código (working tree):** `fontes_gps`, chave `EMBARQUES_FONTE_INSIGNIA_BURACO`. Para placa que está NAS
DUAS fontes, o ponto da Insignia entra na série da 3S (com a grafia da 3S, sem odômetro — outro
aparelho) quando a 3S não tem ponto a ±15 min (andando) ou ±75 min (parado — a 3S parada fala de
hora em hora, isso não é buraco). Posição atual: a da Insignia vale se for 30+ min mais nova. Coluna
`fonte` ∈ `3s` · `insignia` · `ins_buraco` (atenção: `varchar(8)` cortava "ins_buraco" calado).

Buracos naturais (05/10 17:55 → 06/10 13:30 UTC, 8 placas nas duas): 47 buracos ≥ 30 min, 42 parados
(batimento), 5 andando — 3 da QOY6F50 com a Insignia dentro, 2 da QQA7443 com a Insignia também calada.

| teste | resultado |
|---|---|
| **E4a** caso real, C-1351 (QOY6F50, carreta muda), lab A | +51 pontos; 0 corte de posição falsa; km de GPS 1.230 → 1.343 (de −5,7% para +3,0% contra o odômetro 1.304); **motor: 0 decisões mudam** |
| **E4b** apagão simulado: 12 h sem 3S nenhuma (05/10 18:00 → 06/10 06:00 BRT), 15 cargas com evento dentro | só **5 das 15 têm SM**; nelas a Insignia recuperou os 3 eventos recuperáveis: C-1366 saída **exata** (sem ela 2 h cedo), C-1338 chegada a −16 min (sem ela +8,5 h), C-1276 chegada a +1 h (sem ela +12,7 h). As 10 sem SM: nenhuma ajuda — a Insignia não as vê |

Leitura: o tapa-buraco funciona e não estraga nada; o limite é a COBERTURA (SM ≈ 1/3 das cargas).
Também à parte (lab A, Insignia para todas, sem buraco): 3 cargas melhoram — C-1264 e C-1364 saem de
`Aberta`, C-1341 ganha a chegada (carreta muda na 3S).

## 14. ONDE PARAMOS — estado em 07/10/2026 e como retomar

**Produção:** só a coleta (`e5d90bc`, `INSIGNIA_COLETA=true`). Nada do que está abaixo subiu.

**Working tree (NÃO commitado)** — tudo atrás de chave que nasce desligada:

| arquivo | o quê |
|---|---|
| `fontes_gps.py` (novo) | `historico(cur)` / `atuais(cur)` / `placa_rastreada()` / `tem_sm()` / `encerramento_sm()`; odômetro convertido; tapa-buraco |
| `rastreamento_worker.py` | `_placa_tracking`, posição atual, histórico, KPI pela camada; cidade NULL não conta como "mudou de cidade" |
| `_robo_atemporal.py` | série pela camada; regra `sm_encerrada` (nunca em perna vazia) |
| `_auditoria_geral.py` | série pela camada; `sm_encerrada` = F1d e sem F3 (mesma régua do motor) |
| `embarques_auto.py` | Terceiro só com SM; `_chegou_ao_destino` pela camada |
| `geocoding.py`, `embarques_continuacao.py`, `server.py` | leitura pela camada (detecção de saída, continuação, mapa da carga, lista, mapa geral) |
| `insignia_coleta.py` | segue o cavalo 48 h depois da SM (`INSIGNIA_POS_SM_H`); `marcar_odometro` (coluna `insignia_placas.odometro_travado`) — **estas duas NÃO têm chave**: valem no deploy (só coletam mais) |

Chaves (README, "Fontes de GPS"): `EMBARQUES_FONTE_INSIGNIA`, `…_ESCOPO` (`terceiro`|`todas`),
`…_ODOMETRO`, `…_BURACO`, `EMBARQUES_SM_ENCERRAMENTO`; Terceiro no robô = acrescentar a
`EMBARQUES_AUTO_TIPOS`.

**Decisões do Gabriel (06/10):** terceiro entra no robô só com SM; cada fonte na sua tabela + uma camada de
leitura (não escrever a Insignia na tabela da 3S); placa MUDA depois da SM → conclui no FIM DE VIAGEM, a que
continua posicionando segue a regra de hoje; odômetro das três tecnologias; tapa-buraco por tempo.

**Gates feitos (lab):**

| | resultado | § |
|---|---|---|
| E0 chaves desligadas × último commit (worktree) | motor e aferidor idênticos — refeito depois de cada mudança | §9, §10, §12 |
| E1 terceiro + fonte | 0 de 1.350 cargas existentes mudaram; 7 terceiros | §9 |
| E2 só Insignia (3S escondida em 19 F/A) × gabarito 3S | eventos vistos pelas duas batem (saída exata, chegada ±0,3 h); 4 cargas fechadas pela regra da SM | §10 |
| km × odômetro da 3S | Autotrac +4% (9 pares); GPS da Insignia −5 a −7% | §11 |
| E3 odômetro pelo mapa | 0 corte de posição falsa, motor 0 mudanças | §12 |
| E4 tapa-buraco | caso real sem efeito colateral; apagão de 12 h: 3/3 eventos recuperados nas cargas COM SM (5 de 15) | §13 |

**Abertos, em ordem:**
1. **Jornada** — a consolidação diária (`_consolidar_dias`) ainda lê só a 3S: km e situação do dia não
   ganham a Insignia. Passar pela camada (as viagens já ganham).
2. **PGR** — desenho próprio: a Insignia nas lacunas EM MOVIMENTO da 3S, juntando o episódio do cavalo
   (Insignia) com o da carreta (3S) para não contar duas vezes.
3. **Cobertura** — só ~1/3 das cargas têm SM (4/19 terceiros, 13/34 agregados, 4/11 frota). Perguntar à
   Insignia: o que é `ER0121`; dá para manter a frota visível sem SM?
4. **Olhar no mapa** — saída da C-1361 (+30 h) e da C-1365 (+26 h): a SM põe o cavalo na origem um dia DEPOIS
   da "saída" da carreta na 3S (carreta trocada no manifesto?).
5. **Pré-existente, exposto pelo lab** — a lista de cargas mede o rastreio pela carreta do texto
   (`COALESCE(carreta1, cavalo)`), não pela placa rastreada: terceiro (e agregado sem 3S na carreta) aparece
   `sem_posicao` e o alerta não dispara.
6. **Refazer E1–E4 com o dump de ~13/10**, só com SMs nascidas depois de 05/10 (observadas inteiras).

**Para subir (proposta, nada feito):** commit → deploy inerte (chaves ausentes; conferir dentro do container
`grep -c "def historico" fontes_gps.py`) → snapshot → ligar pela CLI, uma por vez com observação:
`EMBARQUES_FONTE_INSIGNIA=true` (escopo `terceiro`) + `EMBARQUES_AUTO_TIPOS=Frota,Agregado,Terceiro` +
`EMBARQUES_SM_ENCERRAMENTO=true` → `…_ODOMETRO` → `…_ESCOPO=todas` → `…_BURACO`. Volta: a chave, sem deploy.

**Laboratório (bancos locais, nunca por cima):** `rizza_lab_1006` (dump de 06/10 13:30 UTC, com `insignia_*`);
`_ins` (base dos gates); `_a` (controle) · `_b` (terceiro + fonte); `_c1`/`_c2` (E2/E3); `_d0`/`_d1` (apagão).
Usuário local `teste@lab.local` nos clones de `_b`. Scripts e saídas em `_estudo_2026-10-06/` (e `lab/`).

**Armadilhas que custaram rodada:**
- `_teste_ciclo_transacao.py` roda o ciclo REAL do worker e **grava posição real da 3S** no banco do `.env`
  — rodado contra `_ins`, pôs 45 pontos novos e desatualizou o gabarito. Gate do E0 por **worktree** do commit
  (`git worktree add`), sobre o mesmo banco, não contra uma foto antiga.
- `varchar(8)` na coluna `fonte` cortava `ins_buraco` para `ins_bura` sem erro.
- O mapa de carga com chegada lê o KPI consolidado (`embarques_cargas_rastreio_kpi`): esconder posição no lab
  não esconde o KPI — a sanidade do odômetro comparou com o número velho (C-1338).
- Caminho com espaço (`Tabela Auditoria`) quebra `--csv $L/...` sem aspas.

## 15. Dump de 07/10 — 3S congelada, conclusão travada e o depois do FIM DE VIAGEM (07/10/2026)

**Laboratório:** dump de produção de 07/10 11:45 UTC → banco novo `rizza_lab_1007` (pg_restore limpo; as 10
FKs de sempre). 35 SMs (15 encerradas), 10.218 posições da Insignia (~42 h), 195 macros, 1.376 cargas.
**O banco não traz `clientes`/`auditoria_users`** — o robô real (E1) quebra no `resolver_cliente`; copiar de
`rizza_lab_1006_b`. Scripts e saídas em `_estudo_2026-10-07/` (e `lab/`); **a pasta não está no `.gitignore`
e o `a2.json` tem dado de carga — não commitar.** Armadilha nova: no Windows `M_base.csv` e `m_base.csv` são o
MESMO arquivo (um braço sobrescreveu o gabarito e o E0 deu "diferente" sem ser); e `aux.py` não pode existir
(nome reservado).

### 15.1 Cruzamento e macros

34 de 35 SMs casam com manifesto: 25 cargas do painel, 9 terceiros (fora do robô), 1 SM criada antes do
manifesto. CHEGADA NO CLIENTE × chegada do painel: ±0,2 h em 5 (C-1305, 1276, 1315, 1340, 1217); a C-1338 tem a
macro a 178 km do destino (apertada errado).

### 15.2 A 3S CONGELA — e o tapa-buraco da §13 não vê

O aparelho da carreta repete a mesma posição com o odômetro parado por horas e depois pula centenas de km.
C-1363: 83 pontos, 3 posições, odômetro 425842 de 06/10 08:45 a 07/10 00:04, pulo de 239 km — enquanto o
cavalo (Insignia) saiu 10:07 e chegou a S. J. do Rio Preto 14:21 (painel: saída 4 h e chegada 19 h atrasadas).
Em 4 das 23 cargas com as duas fontes a carreta ficou congelada com o cavalo andando 76–677 km (C-1376, 1363,
1276, 1366; a C-1362 tem a mesma assinatura). **O tapa-buraco por tempo não entra: a 3S não calou, repetiu.**

E a repetição fabrica fechamento: na **C-1276** a chegada foi emprestada do cavalo em Embu, mas a "saída do
destino" foi medida na carreta congelada em Uberlândia (549 km) → concluída **2 min depois de chegar**; o cavalo
ficou 22 h no cliente (bate com o FIM DE VIAGEM).

**Teste 1 — `EMBARQUES_CARRETA_CONGELADA`** (`fontes_gps.descongelar`, chamada no motor): trecho com ≥ 4 pontos
no mesmo lugar (≤ 0,3 km) e odômetro igual é tirado da série da carreta **se a SM da GR lista a carreta engatada
no cavalo E o cavalo, no mesmo instante, está a > 5 km** — e os pontos do cavalo entram no lugar. A SM tem de ser
DA carga (início de −3 a +2 dias do carregamento): sem isso a janela de 30 d alcançava a SM da viagem seguinte da
mesma dupla e mexia na C-1055 (de 20/09). O empréstimo do cavalo (`SENSOR_CAVALO`, 3× pontos) passou a contar a
série ORIGINAL. Resultado: **4 cargas mudam, todas certas** — C-1363 saída 10:28 / chegada 14:21 (as da Insignia),
C-1362 chegada 08:39, C-1276 conclusão derivada 06/10 20:22 (o cavalo saiu 20:21), V-326 (perna).

### 15.3 Duas falhas do motor que NÃO são da Insignia (valem para a 3S sozinha)

**(A) Conclusão travada na chegada.** 45 cargas desde 01/09 com `gps_saiu_do_destino` < 1 h depois da chegada
(muitas no mesmo segundo). Medido contra o GPS cru das duas placas e das duas fontes (`t4.py`): **43 de 45
prematuras** — o veículo ficou 3 a 48 h no destino depois da "conclusão". O mecanismo, lido no log: numa rodada
com GPS ralo o motor conclui cedo; o GPS novo empurra a chegada para frente; a coerência trava a conclusão NA
chegada; quando a permanência de 24 h é provada a diferença é exatamente 24 h e a guarda (`erro_h > 24`) nunca
reescreve. Presa para sempre.

**(B) Chegada de metrópole sai do destino na hora.** Chegada a 41 km (tolerância de 60) e raio de saída de 30:
o ponto seguinte, parado no mesmo lugar, já "saiu". C-852, 892, 962, 1010, 1166, 1183, 1190, 1208, 1209, 1225, 1258.

**Teste 2 — `EMBARQUES_SAIDA_DESTINO_COERENTE`**: (1) chegada provada pelo cavalo → a saída do destino também é
medida no cavalo; (2) só sai quem esteve: o raio de saída é `max(30, km da chegada + 10)` e o ponto conta só
depois de a série ter estado dentro dele. Sozinho muda pouco o banco (C-1355 deixa de concluir 5 min depois de
chegar) porque a guarda de 24 h segura o resto.
**`EMBARQUES_CONCLUSAO_TRAVADA`**: reescreve a conclusão gravada a < 1 h da chegada quando a derivada é > 1 h
depois (não vale para conclusão documental de meia-noite). Com o teste 2: **56 reescritas; 48 batem com a saída
real medida de forma independente** (quase sempre a minutos; C-955 01:59×01:58, C-989 11:55×11:55, C-1276
20:22×20:21), 2 divergem (C-1086, C-1208), 6 sem testemunha.

**(C) Saída adiantada (passagem).** 23 cargas com saída anterior ao dia do carregamento; **15 provadamente
adiantadas** — o caminhão voltou a parar na origem 5–47 h depois (carregando). A C-1361 também (carregou em
Seropédica, 40 km da origem cadastrada Duque de Caxias). A chave que já existe, `EMBARQUES_SAIDA_CARREGAMENTO`
(desligada), corrige 11 delas e cai no fim do bloco de carregamento — **mas sozinha estraga a C-1366** (tomou o
trecho CONGELADO como carregamento). Junto com o teste 1, não estraga.

Gate de todas: **E0 idêntico byte a byte** (chaves desligadas × gabarito), refeito depois de cada mudança.
`EMBARQUES_LAB_MEDIR_CONCLUSAO` é só instrumento (lista a derivação que a guarda de 24 h não grava).

### 15.4 Depois do FIM DE VIAGEM (`pos_fim.py`, 8 SMs)

FIM a 0,1–1,8 km do destino da SM em 7/8 (ARM 11,6). A GR encerra a SM 0,1–0,5 h depois em 4, 1,4–4,3 h em 3,
19 h em 1 (DIL-8F42: quatro FIM durante a descarga). **Depois do encerramento a Insignia não manda mais nada**
(produção não segue a placa — o `INSIGNIA_POS_SM_H` está só no working tree); só a QUL-3I11 continuou, porque
abriu SM nova. A 3S segue: cavalo em 3, carreta em 5 (uma congelada). Terceiros (ARM, DPB): nada. Onde deu para
ver, o veículo saiu do destino 1,6 e 2,5 h depois do FIM. FIM ≈ fim da descarga, não chegada.

### 15.5 Teste 3 — E2 e E1 refeitos

**E2 só com SMs observadas inteiras** (12 F/A nascidas depois de 05/10 17:57 UTC, 3S escondida em
`rizza_lab_1007_c1/_c2`, convergência 20→0 e 86→0): onde a 3S está certa a Insignia sozinha repete (chegada
0,0/−0,6/0,0 h; saída ≤ 1 h em 6); onde a 3S erra, a Insignia acerta (C-1363 chegada 19 h antes = FIM; C-1364
ciclo inteiro; C-1365 saída do carregamento real; C-1362 `No destino` em vez de `Entregue` travada). C1 = C2:
os consertos de hoje só atuam na 3S. Limites: C-1361 (origem do cadastro ≠ ponto de carga — a origem da SM
resolveria) e C-1398 (SM 1 h antes do dump).

**E1, robô real 05→07/10** (`rizza_lab_1007_a/_b`): **0 de 1.376 cargas existentes mudaram**; 5 terceiros com
SM nasceram, 64 manifestos-rodada sem SM barrados; ponto fixo na rodada 2. C-1402 fecha pelo FIM (ABERTURA DE
BAÚ como chegada), C-1404 chegada 13:30 × macro 13:32. **C-1405**: chegada no 1º ponto da coleta (pernoite perto
do destino) contra a macro CHEGADA NO CLIENTE de 06/10 05:13 — o P1 transitório + o raio de 20 km.

**Efeito colateral do escopo `todas` (achado no braço da fonte):** a C-1264 (30/09, carreta HOA0467 morta) ganhou
"chegada" em 06/10 pelo cavalo DIL8F42, que estava em Uberlândia carregando a carga SEGUINTE (C-1363). Antes de
ligar `todas`: a evidência do cavalo não pode passar do início da próxima carga dele (§26.7 do HANDOFF-EMBARQUES).

### 15.6 Para decidir / fazer

1. Ordem sugerida das chaves novas (todas desligadas, só working tree): `EMBARQUES_SAIDA_DESTINO_COERENTE` +
   `EMBARQUES_CONCLUSAO_TRAVADA` (3S pura, 48/56 confirmadas) → `EMBARQUES_CARRETA_CONGELADA` (exige a fonte) →
   `EMBARQUES_SAIDA_CARREGAMENTO` (só junto da congelada).
2. **O aferidor ainda não tem essas réguas** — antes de subir, a mesma régua nele (§20.6 do HANDOFF-EMBARQUES).
3. Guarda do cavalo no escopo `todas` (C-1264) antes de ligar.
4. Rodar o backfill da 3S 03–08/09 (§28.14 do HANDOFF-EMBARQUES) — prazo ~08/10.

### 15.7 A Insignia no laboratório da âncora (§28 do HANDOFF-EMBARQUES) e as macros como régua

`_lab_ancora.py` ganhou duas opções (desligadas por padrão — sem elas o placar é o de antes; a cópia anterior
está em `_estudo_2026-10-07/_lab_ancora.antes.py`): `--gr off|t0|apos_t1|reserva` (os locais da GR,
`insignia_locais`, como camada de âncora de destino pelo `destino_cnpj`) e `--ins-sensor off|buraco|congelada|ambos`
(o cavalo da Insignia na série da carga). Só cache do Google (sem `--geocodar`). Rodado em `rizza_lab_1007`,
cargas do robô desde 19/08, `--r-chegada 2 --r-chegando 5 --d2 b`. Saídas em `_estudo_2026-10-07/ancora/`.

| | hoje (T1/T2/T3) | + GR depois do T1 (`--gr apos_t1 --ins-sensor ambos`) |
|---|---|---|
| cargas com âncora de destino | 414 de 626 (66%) | **499 (80%)** |
| entregues ancoradas | 396 | **475** |
| "chegou no cliente" | 283 (71%) | **349 (73%)** — +66 cargas |
| "saiu do cliente" | 210 | **268** |

Âncora errada ("nunca a < 5 km") por camada: T1 6% · **GR 14%** · Maps 25%. Por isso a ordem é **T1 → GR → Maps →
T3** (com a GR em primeiro o número é o mesmo, mas ela tira o lugar do T1, que é mais preciso). O sensor da
Insignia soma só +3 — ela só tem posição desde 05/10.

**As macros como régua (proposta do Gabriel: CHEGADA → `No destino`, FIM → concluída), `macro_regua.py`:**
- CHEGADA NO CLIENTE: 11/12 a ≤ 0,8 km do cliente; vem 6–14 min depois de o GPS mostrar o caminhão parado no
  cliente (6 cargas); na C-1341 (carreta muda) deu a chegada 19 h antes do GPS, certa. 1 apertada a 170–178 km
  → precisa de guarda de distância ao destino DA CARGA (a SM às vezes vai além: C-1313). 5 enviadas andando
  (entrando no cliente).
- FIM DE VIAGEM: **misto** — em 4 de 7 veio 5–21 h depois da chegada (fim da descarga); em 2 de 7, 0–20 min depois
  de parar (na chegada); a DIL-8F42 mandou 4 FIM, o último "finalizando descarga" 19 h depois do primeiro. Contra a
  saída do cliente medida no GPS (só 2 casos): FIM −0,0 h e −1,5 h; encerramento da SM +0,5 h e +1,7 h.
- Recomendação: CHEGADA (≤ alguns km do destino da carga) → `No destino`; conclusão no **último** FIM ou no
  encerramento da SM, só se ≥ 1 h depois da chegada; FIM colado na chegada vale como chegada. Sempre ADITIVO à
  régua do GPS (macro é declaração do motorista). Amostra pequena (6 chegadas, 2 saídas) — refazer com o dump de
  ~13/10 antes de escrever regra.

**Cache do Google atualizado (07/10):** `_estudo_2026-10-07/geocode_cache.json` (cópia do de 30/09 + 28 endereços
novos, todos OK; o de 30/09 ficou intacto, backup em `geocode_cache.antes.json`). **Decisão do Gabriel (07/10/26): NÃO apagar — as coordenadas do Google vão ser guardadas no nosso banco.** Aviso registrado: os termos da Google Maps Platform só permitem cache de lat/lng por 30 dias (o `place_id` pode ficar para sempre); guardar a coordenada descumpre o contrato e arrisca suspensão da chave (a mesma do `preencher_km_google.py`). Alternativa oferecida e não adotada até aqui: guardar para sempre só o ponto CONFIRMADO pelo nosso GPS e o local da GR, com o Google como palpite inicial + `place_id`. Não commitar o arquivo de cache (dado de cliente). Rodar o lab com
`--cache _estudo_2026-10-07/geocode_cache.json`. Efeito: sem a GR a cobertura vai de 66% a 68% (a semana 41 de
71% a 84%); com a GR (`apos_t1`) 80% → 80%, "no cliente" 349 → **350 de 476 (74%)**. Por semana, com a GR:
cobertura 87–94% nas semanas 38–41, "no cliente" 70–100%. A semana 34 (19–23/08) segue em 11% — GPS daquelas
datas (purga de 30 d, desligada só em 25/09).

### 15.8 O worker com a fonte da Insignia ligada (07/10, `lab/worker_teste.py`, `lab/telas2.py`)

Só o `_processar_cargas` (sem 3S, ORS trocado por stub), no clone `rizza_lab_1007_b` (terceiros do E1),
transação com ROLLBACK — nada gravado. Escopo `terceiro`.
- Base convergida: 0 mudanças nos dois braços (o motor já tinha decidido tudo — zero não provava nada).
- **Pré-condição montada** (os 5 terceiros como recém-criados): chave OFF 0 mudanças; ON só os 5 terceiros, 0
  F/A. C-1406 saída 06/10 12:11 BRT (motor: 12:06); C-1405 barrada pela guarda "nunca vista na origem" (coleta
  começou com ela no destino); as outras 3 com posição > 12 h → silêncio.
- **Chegada ao vivo** (C-1405 posta `Em rota`): ON marca "chegou e parou no destino (3,2 km)"; OFF nada.
  (Lab: o Postgres local está em BRT, então o `NOW()` do worker carimba BRT; produção é UTC.)
- **Telas:** mapa da carga rastreia pelo cavalo via Insignia (até 623 km de trajeto); mapa geral ganha os 5
  terceiros (94 → 99). **Lista: a C-1406 `Em rota` sai com `rastreio_carreta_idade_h` vazio e
  `rastreio_defasado = true` com a chave ligada OU desligada** — a lista mede pela carreta do documento
  (`COALESCE(carreta1, cavalo)`), não pela placa rastreada. Ligar terceiros sem consertar isto = alerta falso de
  "sem sinal" em todo terceiro em viagem. **CONSERTADO (07/10, "pode seguir" do Gabriel, working tree):**
  `api_embarques_cargas_list` → `_gps_da_carga`: com a fonte ligada E carga `Terceiro`, a idade do rastreio e a
  posição do subrótulo de `Aberta` vêm da placa que TEM posição, na ordem do worker (carreta1 → cavalo → carreta2;
  `Desengatada` só a carreta). Chave desligada ou F/A: o SQL de sempre, por construção (o ramo padrão é o texto
  antigo). Gate pela rota real, 1.002 cargas, chave OFF × ON (`lab/lista_gate.py`): **mudam só os 5 terceiros**
  (F/A só +0,1 h de relógio entre as rodadas); a C-1406 deixa de ser "rastreio defasado" (idade 3,3 h).
