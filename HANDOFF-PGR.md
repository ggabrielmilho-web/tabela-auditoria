# Handoff — Sessão de 12/08/2026

Documento de continuidade. Cobre tudo que foi investigado, o que ficou decidido, o que
está pendente e onde estão os artefatos. **Ler inteiro antes de retomar.**

---

## ⚠️ ANTES DE QUALQUER COISA

**Atualizado em 12/08/2026 (sessão 2).** O que mudou desde a redação original:

- O grupo econômico (§1) **foi commitado** — `ab2f206`. O aviso de working tree sujo caiu.
- O teste do `/HistoricoPosicao` (§12) **foi feito**. Resultado inverteu a hipótese e mudou o
  desenho: ver §18.
- Os **70% do §10 estavam errados** por artefato de medição. O número honesto do polling é
  **81%**; com backfill, 100%. Ver §18.
- O **"pico 117 de 11/08" é de 10/08 às 21:32 BRT**. Ver §18.
- Backfill, fix do token e retenção **implementados e commitados** (`a86be6d`). Ver §19.

---

# PARTE 1 — Aba Veículos (concluído)

## 1. Grupo econômico — IMPLEMENTADO, NÃO COMMITADO

**Problema:** o Martins fatura por duas raízes de CNPJ e aparecia partido em duas linhas,
tanto na aba Veículos quanto no Faturamento por Tomador.

**Solução em `server.py`:**

```python
GRUPOS_ECONOMICOS = { '43214055': '18485037' }   # MARTINS COM E SERV → MARTINS URN-MG
def _raiz_cnpj(cnpj): ...                        # raiz já resolvida para a do grupo
```

Aplicado em 4 pontos: `api_faturamento_tomadores`, `_ctrc_tomador_map`,
`_veiculos_margem_cliente._chave`, `_veiculos_detalhe_cliente`.

**Testado (com e sem o mapa):** faturamento total idêntico (2025 R$ 56.847.585,44 · 2026
R$ 41.848.458,96), `totais_mes` byte a byte igual, contagem de clientes cai exatamente 1,
drawer reconcilia, raiz secundária abre o mesmo cliente. **Zero falhas.**

Resultado: Tomadores 2025 R$ 11.144.285,19 numa linha só; Veículos·Cliente mai/26 vai de
129 para 130 viagens (R$ 1.062.289,53).

**Falta:** commit + deploy.

## 2. Investigação Martins (encerrada — não era bug)

Diretor questionou "só 1 carga carreteiro em julho". **Está correto.**
- Série: jan 16 · fev 7 · mar 20 · abr 14 · mai 19 · jun 11 · **jul 1**
- Classificação bate 100% com o proprietário do `veiculos_045` (0 divergências)
- 6 dos 7 cavalos carreteiros de junho **nunca mais rodaram** (última viagem 03–17/06)
- O 7º (FFA2I61) migrou para agregado puxando carreta Rizza

**Lacuna real encontrada:** 94 CTEs / **R$ 393.161,27** em julho onde o Martins é
**destinatário** e quem paga é a indústria (Química Amparo, Nestlé, Heinz). Não aparece
sob Martins na aba. Ideia futura: visão por destinatário/remetente.

## 3. Margem Sanchez Cano × Química Amparo (encerrada — é preço)

No recorte **agregado** de julho: Sanchez 26,2% × Amparo 45,4%.

A diferença de 19,2pp é **exatamente** a diferença do frete pago (70,1% × 50,9% da receita).
Custo de carreta é 3,7% para os dois.

| | Sanchez | Amparo |
|---|---:|---:|
| Receita/km | 10,23 | **13,36** |
| Frete pago/km | 7,17 | 6,81 |
| Spread/km | 3,06 | **6,55** |

**O agregado custa o mesmo; o que muda é o preço de venda.** Sanchez é longa distância
saindo de Jundiaí (890–2.626 km); Amparo é curta/média (Extrema/Amparo → Uberlândia).

Achado colateral: **a margem de FROTA é 56,08% para todos os clientes** — consequência
matemática do rateio ser % fixo da receita (`margem = 1 − taxa`). Não serve para comparar contas.
Simulação com rateio por KM: Sanchez cairia para 32,7%, Amparo subiria para 35,8%.

## 4. Quatro CTRBs duplicados (achado; correção no SSW pendente)

Assinatura: mesmo CTE em 2+ CTRBs com **mesma rota, mesmo cavalo, mesma data**.
Varredura na base inteira (4.230 linhas): **4 casos**.

| # | CTE | Cliente | Data | CTRB a corrigir | Ação |
|---|---|---|---|---|---|
| 1 | NOD006493-9 | L'Oréal | 16/03 | UDI024911-4 **ou** UDI024912-2 | cancelar uma |
| 2 | UDI412054-0 | Nestlé NE | 28/05 | UDI025813-0 **ou** UDI025815-6 | cancelar uma |
| 3 | UDS003669-2 | Martins | 30/05 | UDI025853-9 / UDI025854-7 | **conferir antes** |
| 4 | NOD007269-9 | Sanchez Cano | 26/07 | **UDI026574-8** | cancelar + lançar Brasília (manif. UDI028489-1) |

**Caso 3 é o único com dinheiro em jogo:** dois fretes diferentes pagos (R$ 1.798,16 e
R$ 2.152,16) numa carga de R$ 2.699,93 → prejuízo de R$ 1.250,39. Pode ser pagamento em
duplicidade. CSV em `scratchpad/CTRBs_duplicados.csv`.

**Decisão:** corrigir na fonte (SSW), **não** criar regra automática no app —
apagar linha perde receita, refazer o rateio diverge do Power BI (±1,5% por perna) e
quebraria a reconciliação com as outras telas.

Simulação da correção do caso 4: Sanchez agregado 26,2% → 27,6%; geral 35,4% → 35,9%.
Sem custo de carreta: 29,91% → 31,25%.

**Nota:** a tela de Auditoria mostra 29,91% e a aba Veículos 26,21% para o mesmo recorte —
as duas certas. A diferença é só o custo de carreta (3,70%), que a Auditoria não desconta.

---

# PARTE 2 — Relatório PGR de velocidade (EM ANDAMENTO)

## 5. O pedido

Diretor quer receber **toda manhã** as carretas que passaram de **95 km/h**, por WhatsApp.
Pediu: placa, cidade, tempo acima, e cruzamento com manifesto (origem/destino/tomador).

## 6. O que os dados permitem — e o que não permitem

**Amostragem (medido em produção, 03/08):**
- Worker já chama a 3S ~1,4×/min — o gargalo **não é nosso polling**
- Intervalo entre posições distintas: **mediana 2,0 min · p90 6,4 min**
- Parado, o aparelho reporta de 1 em 1 hora (ou 12 em 12 h)

**❌ "Tempo acumulado acima de 95" NÃO é calculável.** Cada leitura é um instante, não um
intervalo. 45 dos 66 eventos de 11/08 têm uma amostra só. Somar seria inventar número.
**Substituído por "nº de registros"** (mede recorrência, é auditável).

**Telemetria CAN (`tempoAlertaVelocidade`) — DESCARTADA definitivamente:** a maioria dos
equipamentos está em **carreta**, que não tem ECU/barramento CAN. O fluxo `Telemetria` da
3S volta vazio e vai continuar vazio.

## 7. Regra de detecção definida

```
LIMIAR       = 95 km/h
TETO         = 130 km/h   (anti-ruído)
GAP_EVENTO   = 10 min     (agrupa registros no mesmo episódio)
DIA          = horário de Brasília → filtro UTC 03:00 a 03:00
```

> ⚠️ **A regra acima está certa, mas o `pgr_extracao.sql` não a implementa.** Ele filtra
> `data_posicao >= DATE '2026-08-11' AND < DATE '2026-08-12'` — dia-calendário **UTC**, que é a
> janela de Brasília deslocada em 3 h. Consequências medidas em §18. Ao implementar, usar a
> janela de Brasília desde o início; o `backfill_historico.py` já pede o dia em BRT.

**Filtro de ruído é obrigatório.** Caso real: placa HAS8877 em 25/06 marcou **214 km/h
constante por 19 minutos** (44 pontos idênticos) — equipamento travado. Sem o teto, isso
lidera o relatório e queima a credibilidade no primeiro dia.

**Velocidade sustentada** (km percorridos ÷ tempo, entre pontos consecutivos): calcular
**ignorando segmentos com veículo parado (≤3 km/h) e intervalos > 30 min**, senão contamina
(apareceu sustentada de 120 com pico 100, e de 11 com pico 102). Usa haversine, então
**subestima 5–15%** (a estrada é mais longa que a reta).

**Identificação de carreta:** via `veiculos_045` do Power BI (NÃO por
`embarques_veiculos_rastreio`, que tinha 1 linha só no ambiente local — **verificar em
produção**). Das 98 placas com GPS: 75 carretas, 16 cavalos, 2 trucks.

## 8. Validação de carga por GPS (a parte mais importante)

**O problema:** casar o excesso com o manifesto pela data é frágil nos dois sentidos —
estrito perde trecho longo, frouxo casa 100% e mente. Risco real: dizer "a 105 carregado
de Nestlé" quando o caminhão já descarregou.

**A solução: posição, não data.** Três testes factuais:

1. **Corredor** — a cidade do excesso está entre origem e destino? (`dist_O + dist_D ≤ 1,35 × dist_OD`)
2. **Parou na origem** do manifesto antes do excesso?
3. **Ainda não parou no destino** quando o excesso aconteceu?

Comprovado em 3 casos de 11/08:
- **TZC0I41** (117 km/h): carregou em Nerópolis 06/08 → parou 3 dias em Uberlândia →
  saiu 10/08 20:22 → excessos → chegou em Pouso Alegre 11/08 07:52. **CARREGADO.**
  *(A janela por data rejeitava este — CTRC emitido 4 dias antes.)*
- **TYZ7I93**: ficou em Brasília de 09/08 até 11/08 18:05, excessos às 19:19–20:48 saindo
  de Brasília. **VAZIO** — já tinha entregue.
- **HJI3I21**: carregou em Cordeirópolis 08/08, às 18:41 de 11/08 ainda estava em Paraíso
  do Tocantins (~100 km antes de Palmas). **CARREGADO.**

**Lacuna de sinal ≠ parada.** Classificar pela **velocidade implícita** (deslocamento ÷
duração), não por distância absoluta. Acima de ~5 km/h estava rodando. Testado: 5,2 km em
246 min = 1,3 km/h (parado) vs 72,9 km em 58 min = 75 km/h (rodando).

Query de extração das paradas: `scratchpad/pgr_paradas.sql` (testada).

## 9. API da 3S — descoberta completa

Swagger em `{BASE}/swagger/v1/swagger.json`. **8 endpoints, usamos 3.**

| Endpoint | Usamos | Para quê serve |
|---|---|---|
| `POST /ValidaLogin` | ✅ | auth |
| `GET /ListaVeiculos` | ✅ | catálogo (93 veículos) |
| `GET /ListaUltimaPosicaoVeiculos/{id}` | ✅ | última posição |
| **`POST /RetornaDados`** | ❌ | **feed incremental: posições, alertas, cercas, telemetria** |
| **`POST /HistoricoPosicao`** | ❌ | **histórico de posições por período** |
| `POST /HistoricoOcorrencia` | ❌ | ocorrências por veículo/período (datas em ISO!) |
| `POST /RetornaDistanciaVeiculo` | ❌ | km rodado por período |
| `GET /ListaSensores/{id}` | ❌ | sensores |

**`/RetornaDados` com todos os IDs em 0** devolve o marcador atual de cada fluxo.
Depois se chama com esses IDs para receber o que é novo. IDs de alerta são **sequência
compartilhada** entre velocidade/sensor/cercas — para achar alertas de velocidade é preciso
recuar bastante (testado: `MAXA − 400000` trouxe 914 alertas de 01 a 12/08).

**Alerta de velocidade existe e funciona:**
```json
{"Placa":"HNH 1302","VelocidadeLimite":90,"Data":"11/08/2026 19:58:15",
 "Velocidade":92,"Cidade":"Itumbiara","UF":"GO",
 "Endereco":"Rodovia Transbrasiliana/BR-153","Latitude":"-18,212742"}
```
- Limite configurado: **90 km/h** (mais rígido que o PGR de 95)
- **Só 31 dos 93 veículos** têm alerta configurado
- **Não tem duração** — evento pontual, mesma limitação da nossa regra
- Traz **endereço da rodovia**, que a nossa detecção não tem

## 10. Eficiência da nossa regra (medida)

Gabarito = alertas da 3S (detecção a 1 Hz no aparelho), nas 8 placas com as duas fontes, 11/08:

```
Episódios capturados:  26 / 37 = 70%
Leituras capturadas:   37 / 50 = 74%
```

> ⚠️ **Estes 70% estão desatualizados — leia §18 antes de usar este número.** Parte do que foi
> contado como "perdido" estava fora da janela que a extração olhou. Corrigido: 81% para o
> polling ao vivo, 100% com backfill.

**O que perdemos:** os 11 episódios perdidos duraram **todos 0 minutos** — pico solto.
E 30 dos 37 episódios do gabarito (81%) são instantâneos. **Os 7 sustentados foram
capturados 100%.**

Nenhuma placa some do relatório; o que muda é a contagem e, em 3 casos, o pico
(OWH0F53 104 vs 111 real · QOD5F57 99 vs 103).

**Do outro lado:** 12 placas só a nossa regra viu (alerta não configurado), incluindo
**TZC0I41 (117 km/h)** e **HJI3I21 (13 registros, 115)** — o pior pico e o mais recorrente
do dia. Só 2 placas eram exclusivas da 3S.

**DECISÃO TOMADA (do Gabriel):** usar **a nossa regra**, não a da 3S. Regra que depende de
cadastro manual veículo a veículo apodrece com o tempo. 70% de precisão em 100% da frota
vale mais que 100% em 33%.

## 11. Layout do relatório — APROVADO

Artifact: **https://claude.ai/code/artifact/85bbaa6d-f5ae-4672-a79e-4a5a13b7d6e9**
Gerador: `scratchpad/gera_pgr_html.py` → `scratchpad/pgr_relatorio.html`

- **Formato lista** (não card — ocupa menos), uma linha por placa
- Tokens do app: `#0a0e17`, DM Sans + JetBrains Mono, max-width 620px, mobile-first
- Colunas: Placa (+ `car/cav/trk · frota/agreg`) · Registros · Pico · Situação e trechos
- Gravidade na **cor do pico**: amarelo 96–102 · laranja 103–109 · vermelho 110+
- Cidades **com UF** numa linha só, sigla em tom mais apagado
- Situação da carga por **ponto colorido**: carregado / parcial / vazio / não confirmado

**Quatro situações de carga, não duas** — "parcial" apareceu em 4 de 20 placas (entrega de
manhã, volta à tarde, corre nas duas pernas).

**Carreteiro não aparece por construção** — no carreteiro puro nem cavalo nem carreta são
da Rizza, então não há rastreador nosso. O relatório cobre frota e agregado. Está no rodapé.

Números de 11/08: 20 veículos · 82 registros · pico 117 · 8 frota / 11 agregado.

---

# PARTE 3 — O QUE FAZER AGORA

## 12. ✅ TESTE FEITO em 12/08 — resultado em §18

**Medir se `/HistoricoPosicao` traz histórico mais denso que o nosso polling.**

Hipótese: o aparelho grava internamente mais do que transmite ao vivo. Se for verdade,
puxar o histórico do dia anterior de madrugada elevaria os 70% de cobertura **sem depender
de configuração nenhuma da 3S**.

Como testar:
```
POST /HistoricoPosicao  {"idVeiculo": <id>, "dataInicio": "2026-08-11T00:00:00",
                         "dataFim": "2026-08-11T23:59:59"}   # datas em ISO
```
Comparar a contagem de pontos com o que está em `embarques_posicoes_historico` para a
mesma placa/dia. IDs conhecidos: HJI3I21 `20240219172450` · TZC0I41 `20260401110328` ·
OWH0F53 `20200713164913` · TYZ7I93 `20260401104352`.

⚠️ Cota de 10 chamadas/min **compartilhada com o worker de produção** (que usa ~1,4/min).
Espaçar as chamadas.

## 13. Decisões — TOMADAS em 12/08

1. **Truck e cavalo entram**, com a coluna Tipo visível. O sujeito do relatório é o motorista,
   não o equipamento, e o OWH0F53 é truck com pico real de 111. Reversível: com a coluna
   visível, restringir a carreta depois é um `WHERE` de uma linha.
2. **Sustentado = 2+ registros ≥95 no mesmo episódio.** A média do trecho **sai do critério**
   (é derivada, usa haversine, subestima 5–15%) mas **continua como coluna informativa** — é o
   que distingue "cruzou 20 min a 98" de "tocou 111 numa descida".
   ⚠️ Com backfill a contagem sobe, então mais episódios entram como sustentado do que no demo
   aprovado. Esperado, mas o diretor vai ver número diferente.
3. **Resumo no corpo da mensagem + link** para a página. O resumo (total, pior pico, top 3)
   garante que quem nunca clicar ainda recebe o essencial.
4. **Escopo do backfill: todos os 93**, toda madrugada.
5. **Vira aba do app (`/pgr`)**, não URL solta — ver §20.

## 14. Implementação

**Feito** (commit `a86be6d`, ver §19): backfill diário, fix do token da 3S, retenção por tempo,
tabelas `pgr_eventos` e `pgr_cobertura`, `backfill_historico.py`.

**Fase 1 concluída** — tudo implementado e commitado, **nada deployado ainda**:
- [x] Apuração (episódios, sustentada, `pgr_eventos`) — `pgr.py`
- [x] Situação de carga pelos 3 testes de GPS (§8) → ver §21
- [x] Cobertura por placa/dia, contando só lacuna **em movimento**
- [x] Página `/pgr` (aba com permissão) + acesso por token de leitura
- [x] Imagem-resumo + envio por WhatsApp (UazAPI) — `pgr_imagem.py`, `pgr_envio.py`
- [x] Caches do Power BI (`pgr_cadastro_veiculos`, `pgr_manifestos`)

**Falta:**
- [ ] **Deploy + reprocessamento de 10 e 11/08** (bloqueia o resto — ver §22)
- [ ] **Calibrar a situação de carga** contra a base backfillada (§21)
- [ ] Regenerar o demo do layout com os números certos
- [ ] Fase 2: ranking por motorista, evolução, filtros, CSV

**Ordem obrigatória do job diário — não pode inverter:**
```
1. backfill do dia anterior   (completa as posições)
2. apura pgr_eventos          (lê posições já completas)
3. envia                      (lê a tabela, não recalcula)
```
Apurar antes do backfill entrega os 81% do polling em vez dos 100% — e ninguém percebe, o
número só vem menor. E o envio lendo da tabela garante que mensagem e página mostram o mesmo
número (o problema que a aba Veículos já teve com KM do drawer × KM da tabela).

## 15. Verificar em produção

- [ ] `embarques_veiculos_rastreio` tem quantas linhas? (no local só 1 — se lá também,
      **o worker mal está processando cargas**, porque `_processar_cargas` exige a placa mapeada).
      Rodar `POST /api/rastreamento/sync-veiculos`.
- [ ] `/api/rastreamento/health` — worker rodando? `modo_simulado` false?

---

## 16. Arquivos

**No projeto:**
- `server.py` — alteração do grupo econômico, **não commitada**
- `HANDOFF-PGR.md` — este documento

**Em `scratchpad/`** (temporário, copiar o que for útil):
- `pgr_paradas.sql` — extração de paradas, testada
- `pgr_extracao.sql` — extração de excessos + vizinhos
- `gera_pgr_html.py` / `pgr_relatorio.html` — gerador e layout aprovados
- `pgr_1108.csv` — registros >95 de 11/08 (produção)
- `3s_alertas.json` — 914 alertas nativos da 3S, 01–12/08
- `eficiencia.py` — comparação nossa regra × gabarito 3S
- `CTRBs_duplicados.csv` — os 4 casos para o SSW
- `teste_grupo.py` — não-regressão do grupo econômico

## 17. Riscos conhecidos no módulo de rastreamento

Encontrados na leitura do código, **não corrigidos**:

1. **`MODO_SIMULADO` tem default `'true'`** — se a env var sumir num redeploy, o sistema sobe
   lendo o simulador e ninguém percebe
2. **Um erro numa carga derruba as posições do ciclo inteiro** — `_persistir_posicoes` e
   `_processar_cargas` estão na mesma transação; rollback descarta os pontos já gravados
3. **`NOW()` sem `AT TIME ZONE 'UTC'`** em `no_local_desde` e `data_conclusao` — funciona
   porque o container está em UTC, mas é dependência implícita
4. **Lógica gêmea duplicada** — `_indice_chegada_destino` existe em `server.py` **e** em
   `geocoding.py`; `_kpi_ao_vivo` espelha `_consolidar_kpi`
5. **Dedupe do mapa geral** ainda aberto (carga de frota aparece 2×: cavalo + carreta)
6. **KM do drawer ≠ KM da tabela** na aba Veículos — a tabela usa `rotas_km`, o drawer usa
   `distancia_km` cru

---

# PARTE 4 — SESSÃO 2 (12/08/2026)

## 18. Resultado do teste do `/HistoricoPosicao`

**A hipótese original era falsa; a conclusão prática ficou melhor.**

O aparelho **não** grava mais do que transmite: a cadência do histórico é idêntica à do nosso
polling (TZC0I41 mediana 4,96 × 4,97 min · OWH0F53 1,98 × 2,00). Não há ponto escondido.

O problema nunca foi o aparelho, era o **método de coleta**: `/ListaUltimaPosicaoVeiculos`
devolve só a **última** posição, então tudo que o equipamento transmitiu entre dois ciclos do
worker se perdia para sempre.

| 03/08, janela de ~2 h | pontos distintos | gaps > 10 min |
|---|---|---|
| TZC0I41 | 3S **38** · nosso 28 | 3S **0** · nosso 1 |
| OWH0F53 | 3S **56** · nosso 34 | 3S **1** · nosso 3 |

**Nenhum ponto nosso falta no histórico da 3S** (0 nos dois dias). É superconjunto — backfill
só adiciona, nunca sobrescreve dado bom.

### Eficiência contra o gabarito (alerta nativo 3S, 1 Hz), dia BRT 11/08

```
A) polling ao vivo, como reportado no §10 : 26/39 = 67%
B) polling na janela comparável           : 26/32 = 81%   <- número honesto
C) backfill /HistoricoPosicao             : 37/37 = 100%
```

Ler o 100% com precisão: **onde o aparelho detectou a 1 Hz, o backfill também detectou** — é o
fechamento da lacuna de *amostragem*, não detecção absoluta. A cadência continua sendo a do
aparelho (2–5 min).

O backfill também recupera o **pico real**, que o polling subestimava: OWH0F53 111 (era 104),
QOD5F57 103 (era 99), HIF2439 108 (era 106).

### Dois erros da medição anterior

1. **O artefato de janela.** `pgr_extracao.sql` filtra dia-calendário UTC; o gabarito é dia de
   Brasília. **6 dos 11 episódios "perdidos" estavam numa faixa que a extração nunca olhou** —
   não foram falha de detecção. Daí 70% → 81%.
2. **O "pico 117 de 11/08" é de 10/08 às 21:32 BRT** (Uberaba, BR-050). Confirmado contra o
   histórico da 3S, que tem o ponto idêntico. Os números do demo aprovado (20 veículos, 82
   registros, pico 117) estão numa janela deslocada em 3 h e **precisam ser regerados**.

### Achados colaterais

- O DTO do histórico traz **`Endereco`** (nome da rodovia: "Rodovia Anhangüera/SP-330") — era a
  última vantagem do alerta nativo da 3S sobre a nossa regra (§9). Some o argumento para
  depender do cadastro deles.
- Traz **`Odometro`**, e ele é **acumulado** (sem reset, sem zero: 398 e 514 km num dia). Mas
  **não é estritamente monotônico**: o TZC0I41 tem uma segunda linha de transmissão (pares de
  leituras a ~5 s, a cada ~15 min) com odômetro 8 km atrasado, enquanto parado. Soma ingênua de
  deltas positivos infla **11%** (443 × 398 km); ignorando segmentos parados, cai para 1,7%.
  Serve para o km/L por GPS — e é melhor que o hodômetro do ValeCard, que é digitado — mas
  exige a mesma defesa que o `_km_hodometro` já aplica.

## 19. O que foi implementado (commit `a86be6d`)

- **Fix do token da 3S — era bug em produção.** `/ValidaLogin` devolve `expiration` em horário
  de **Brasília e sem fuso**; `_get_token` comparava com `utcnow()`, então o token parecia
  vencido sempre e **cada requisição custava duas** (login + chamada). Metade da cota de 10/min
  ia embora em login redundante e o cache em `embarques_3s_token` nunca funcionou desde que
  existe. TTL real: **10 min** (logins repetidos dentro da validade devolvem a mesma sessão —
  foi o que confundiu o diagnóstico). O worker cai de ~2/min para ~1,1/min.
- **Backfill** (`backfill_dia` + `_loop_backfill`) em **thread separada**: 93 × 10 s ≈ 16 min
  travariam o ciclo de 60 s e segurariam a transação do polling aberta. Auto-espaçado, porque o
  token bucket levanta `RateLimitExceeded` quando a espera passa de 5 s. Idempotente.
- **Retenção por idade.** A regra antiga só purgava placas de carga **concluída** — medido:
  **0 de 34.275 linhas**, era no-op. Agora apaga por idade, preservando posições a partir do
  início de carga ainda aberta. Índice `ix_pos_hist_data` para não virar seq scan.
- **`endereco` e `odometer` no histórico**, gravados pelo backfill **e** pelo polling.
- **`pgr_eventos` + `pgr_cobertura`** criadas (§20).
- **`backfill_historico.py`** — reprocessamento manual por dia/intervalo, em dias de Brasília.

**Volume real medido:** ~676 posições/veículo/dia (não ~500). Para 93 veículos: ~63 mil
linhas/dia, ~1,9 M em 30 dias. Tranquilo com o índice novo.

## 20. Por que o relatório persiste o resultado

A retenção de 30 dias **mata a análise histórica do PGR**: em 31 dias o relatório do mês passado
não pode mais ser reproduzido, porque as posições que o geraram foram apagadas. Toda a
investigação desta e da sessão anterior só foi possível porque havia histórico.

A solução não é aumentar a retenção (o volume cresce rápido) — é o job **gravar antes de
renderizar**, em `pgr_eventos` (grão de **episódio**, não placa-dia, senão não dá para rankear
motorista nem abrir detalhe). São dezenas de linhas/dia contra ~63 mil posições. O
`UNIQUE (placa, ini)` com upsert deixa o dia **reprocessável** — necessário, porque um dia
apurado antes do backfill fica subestimado.

`pgr_cobertura` guarda minutos com/sem sinal por placa/dia: sem ela, "zero excessos" fica
ambíguo depois que as posições forem apagadas — não dá para distinguir "ninguém correu" de "o
worker estava fora do ar".

**Vira aba `/pgr`** no sistema de permissão por aba que já existe (`PAGINAS_VALIDAS`,
`_PAGINA_ROTA`, `_PAGINA_ORDEM`), junto da família de rastreamento (perto de Embarques, não de
DRE — é segurança operacional). A rota aceita **sessão OU token de leitura**: `/pgr` para quem
está logado, `/pgr?data=…&t=…` para o link do WhatsApp. Token **por relatório** (vazou, expôs um
dia), com validade de 7 dias, e a página é beco sem saída (sem navegação para o resto do app).

Fase 1 = tabela + visão de dia + WhatsApp. Fase 2 = acumulado (ranking por motorista, evolução,
filtros, CSV). **Ressalva do ranking por motorista:** o motorista vem do casamento com o
manifesto, então onde a carga ficou "não confirmado" não há motorista — o ranking por placa é
sempre completo, o por motorista terá buracos. Mostrar a cobertura do ranking, não fingir que
está inteiro.

---

## 21. ⚠️ Situação de carga — o que mudou e o que falta calibrar

**Uma armadilha ao portar o `pgr_validado.py`.** Aquele script usava
**corredor + sentido + janela por data**. Portado como estava, ele rejeita o
`TZC0I41` com *"emissão 5d antes (limite 3d)"* — e esse é exatamente o caso que
o §8 usa como **prova de que validar por data não serve** (carregou em Nerópolis
06/08, ficou 3 dias parado em Uberlândia, excedeu em 10/08). Qualquer limite de
"dias plausíveis" rejeita essa viagem, que era legítima.

Implementado, então, o que o §8 **descreve** — três testes, todos por posição:

1. **corredor** — `(dO + dD) / dOD ≤ 1,35`
2. **passou pela ORIGEM** antes do excesso (prova que pegou a carga)
3. **ainda NÃO tinha chegado ao destino** (se chegou, já entregou)

A data entra só como sanidade (excesso não pode ser anterior à emissão; teto de
20 dias para não casar manifesto antigo demais).

Isso exige posições dos **dias anteriores** — `_historico_lookback` carrega 12
dias amostrados a cada 15 min (para saber se passou por uma cidade não é preciso
a série inteira).

### O que ainda não dá para afirmar

**Os limiares não foram recalibrados.** Vieram do estudo da sessão anterior, que
rodou sobre 10 dias de posição de produção. No banco de desenvolvimento só há
**1 dia** backfillado, e com 1 dia o teste "passou pela origem" fica fraco
justamente onde mais importa: **Uberlândia é a base**, quase todo veículo passa
por lá, então manifesto com origem Uberlândia casa fácil demais.

Rodando local em 11/08 (9 placas): 7 casaram como `carregado`, 2 `não
confirmado`. Contra o layout aprovado, **3 batem exatamente** (HNH1302, QOD5F57,
HKE0321) e os demais divergem — o esperado, dado que o demo enxergava 10 dias de
trajetória e a base local enxerga 1.

**Ao calibrar depois do reprocessamento:** comparar placa a placa com o demo do
§11 e, se aparecer falso positivo, considerar **reativar o teste de SENTIDO**
(estava se aproximando do destino?) como guarda extra — ele existia no script
validado e é o que melhor separa a perna de ida da de volta.

## 22. Deploy — o que rodar, em ordem

Nada foi deployado ainda. Todos os commits estão em `main`.

```bash
cd /opt/stacks/rizza-auditoria && git pull \
  && docker build -t ghcr.io/ggabrielmilho-web/rizza-auditoria:latest . \
  && docker service update --force --image ghcr.io/ggabrielmilho-web/rizza-auditoria:latest rizza-auditoria_app

docker exec $(docker ps -q -f name=rizza-auditoria) python init_db.py
docker exec $(docker ps -q -f name=rizza-auditoria) python backfill_historico.py 2026-08-10 2026-08-11
```

**Conferir logo após o deploy** — se `/ValidaLogin` continuar 1:1 com as outras
chamadas, o fix do token não pegou e o backfill gasta o dobro da cota:
```sql
select endpoint, count(*) from embarques_3s_log
where chamado_em > now() - interval '10 min' group by 1;
```

**Variáveis novas no Portainer** (ver README para a lista completa):
`UAZAPI_URL`, `UAZAPI_TOKEN`, `PGR_UAZAPI_TO`, `PGR_BASE_URL`, `PGR_ENVIO`.

Subir com **`PGR_ENVIO=false`** no primeiro deploy: o job apura, dá para conferir
em `/pgr`, e só então ligar o envio — assim o diretor não recebe a primeira
mensagem antes de alguém ter visto o conteúdo.

Conceder a aba **`pgr`** aos usuários no Admin (admin já vê por bypass).
