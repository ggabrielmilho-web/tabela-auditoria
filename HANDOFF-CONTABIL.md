# Handoff — Módulo Contábil

Estado em **15/09/2026** (números de base de 20/08/2026; §11 é a rodada de 15/09). Documento
de retomada: quem pegar o projeto daqui deve conseguir continuar sem reler conversa.

> Todos os números deste documento foram **remedidos contra a fonte em 20/08/2026**. A base é
> viva: o que não muda são as identidades e as armadilhas do §5; o que muda são os valores.

---

## 1. O que é

A Rizza fecha a contabilidade na PERSETO (escritório externo). Hoje a contadora recebe
relatórios do SSW e **traduz na mão**, evento por evento, para o plano de contas dela.

O projeto tem duas metades:

| Metade | Onde | Estado |
|---|---|---|
| **Extração** — 5 relatórios do SSW → PostgreSQL | `Rizza/ssw_*` | pronta, documentada em `Rizza/docs/base_contabil.md` |
| **Consumo** — telas + configuração + arquivo de importação | `Tabela Auditoria/` (aba Contábil) | este documento |

**O objetivo final é o arquivo de importação** que a contabilidade lê:

```
Z;DATA;CONTA DÉBITO;CONTA CRÉDITO;VALOR;CÓD HISTÓRICO;COMPLEMENTO
Z;01/07/2026;755;1464;5000,00;2;PAGAMENTO ELETRONICO - PGE 257985
X                                        <- separador entre registros
```

As contas vão pelo **código reduzido**, não pela classificação.

---

## 2. As cinco fontes e o que cada uma traz

Carregadas diariamente pelos robôs em `Rizza/` (tarefa `Base Contabil`, 04:00) e publicadas
no dataset **`tabelas.contabil`** do Power BI (refresh 05:00).

| Relatório | Tabela | Papel no lançamento |
|---|---|---|
| **477** despesas | `consulta_despesas_477` (dataset **DRE**) | a despesa e o evento; traz `liq_*` da liquidação |
| **456** extrato | `extrato_bancario_456` + `_totais` | o banco, no lançamento de pagamento |
| **441** faturamento | `faturas_441` + `_ctrcs` | recebimento, juros e descontos |
| **571** ACNI | `acni_571` | adiantamento de cliente |
| **479** eventos | `eventos_479` | cadastro do evento (conta do **plano do SSW**) |

⚠ O **477 mora no dataset do DRE**, não no `tabelas.contabil`. As pontes entre eles são
cross-dataset e só existem em Python — o Power BI não relaciona datasets diferentes.

---

## 3. Decisões tomadas (não relitigar sem motivo novo)

**3.1 · Escopo: 01/2026 até a competência corrente.** Despesa anterior não se reprocessa.
Não é filtro de conveniência: no histórico completo a despesa é R$ 333,6 mi e os maiores
eventos são os fretes; no escopo são R$ 46,5 mi e a ordem de trabalho é outra. Mata também
a pendência dos 3 eventos aposentados (5213, 5216, 5410) — reconferido: os três têm **zero**
no escopo.
Código: `server.py:REF_INICIAL_477` e `ref_final_477()`.

**3.2 · A planilha da contadora é ESPECIFICAÇÃO, não hipótese.** As colunas dela são entrada;
o trabalho é automatizar em cima, não reinterpretar. *(Custou 3 rodadas de análise numa
premissa que a coluna `Tem Nota?` já negava.)*

**3.3 · Decisão dela vai para tabela; encanamento fica no código.** Ela nunca deve precisar
pedir deploy para mudar uma regra. **Nunca vira configuração:** a mecânica da partida dobrada,
qual data recorta cada relatório, e as guardas (§5) — que são correção de erro medido, não
preferência.

**3.4 · O app é dono da conta contábil.** O plano previa ela preencher na tela 503 do SSW,
mas **verificado que o campo que ela alcança ("Conta Contábil PIS/COFINS") não sai no
relatório 479**. Sem outro campo editável, a informação não chega — daí a tela no app.

**3.5 · Tabela de configuração é APPEND-ONLY.** `eventos_479` é substituição total a cada
carga; evento desativado no SSW some de lá e não pode levar junto a vinculação de um
fechamento anterior. O histórico É a tabela, e ela responde "qual era a conta em setembro".

**3.6 · Preenchimento padronizado.** Nenhum campo livre a não ser observação. Conta sai de
select do plano; flags são enumeradas. `4.1.6.01.013` digitado torto quebraria o arquivo.

---

## 4. Os dois planos de contas — a descoberta que reorganizou tudo

O SSW e a contabilidade usam planos **diferentes e incompatíveis**:

```
53 eventos do 479 têm conta de débito preenchida -> 49 contas distintas
 0 dessas 49 existem no plano contábil
```

A colisão é estrutural: **no SSW o grupo 5 é despesa** (`5.02.02.01.0025 CARGA E DESCARGA`);
**no plano contábil o grupo 5 é apuração** e despesa é o grupo 4 (`4.1.6.01.0018`). Casar por
parecerem parecidos joga despesa em conta de fechamento — o balanço fecha e o número mente.

**Consequência:** a tarefa "ajustar conta contábil nos eventos" é **substituir**, não completar.
Os 53 preenchidos não são trabalho adiantado.

### O de-para encolheu

A planilha dela (`EVENTOS COM INFORMAÇÕES.xlsx`, 97 eventos × 9 colunas) resolve boa parte:

```
CONTABILIZA DESPESA POR IMPORTAÇÃO SSW      CONTABILIZA PROVISÃO
  NÃO      71 eventos                        SIM  63  -> pagamento debita FORNECEDORES (fixa)
  PARCIAL   6                                NÃO  34  -> pagamento debita a CONTA DO EVENTO
  SIM      20
```

Regra achada nos dados e coerente: **todo evento com nota fiscal é `NÃO`** — onde tem nota, a
despesa entra pela escrituração fiscal e importar do SSW lançaria em duplicidade.

**Só 60 dos 97 eventos precisam de conta** (`DESPESA = SIM/PARCIAL` **ou** `PROVISÃO = NÃO`).
Os outros 37 vão na conta fixa de fornecedores.

### A mecânica do lançamento

```
despesa (competência)   DESPESA = SIM   ->  D conta do evento  /  C conta fixa fornecedores
pagamento (456 + 477)   PROVISÃO = SIM  ->  D conta fixa fornecedores  /  C conta do banco
                        PROVISÃO = NÃO  ->  D conta do evento          /  C conta do banco
```

A conta do evento vem de **cruzar `477[evento]` com o cadastro** — fecha **100%**: 90/90
eventos, 17.492/17.492 lançamentos, mesma chave sem normalização. Por isso não importa se o
SSW reescreve histórico: ela preenche uma vez e o escopo inteiro recebe.

---

## 5. Armadilhas encontradas (todas medidas, nenhuma suposta)

**5.1 · O `executeQueries` corta e devolve HTTP 200.** Dois tetos: **100.000 linhas** e
**15 MiB de payload**. O segundo morde muito antes e depende da largura da linha — a mesma
consulta devolve 10.127 linhas com 29 colunas e as 22.835 completas com 3.
**A guarda certa é `results[0]['error']`**, que só existe quando houve corte
(`DaxByteCountNotSupported`). Já custou uma medição de 8,67% de cobertura onde a real era 100%.
Implementado em `server.py:contabil_dax()` + conferência contra `COUNTROWS` no extrato.

**5.2 · `REF` é TEXTO.** `REF >= "2026/01"` é comparação de string e varre competências
futuras — a base tem **117 competências além de 2026/12** (parcelas a vencer de financiamento e
consórcio), somando **R$ 18,6 mi** em 1.556 lançamentos. Sem teto, o escopo salta de R$ 46,5 mi
para R$ 72,0 mi — **R$ 25,5 mi a mais**, porque entram também as competências de 2026/09 a
2026/12. São dois números distintos e é fácil trocá-los: R$ 18,6 mi é o que passa de 2026/12;
R$ 25,5 mi é tudo que o teto exclui. Ver `server.py:filtro_ref_477()`.

⚠ **A trava `LEN = 7` não pega o REF malformado da base.** O valor é `'20ES/6 '`, **com espaço
à direita** — tem exatamente 7 caracteres e passa. Quem o barra é o **teto**, porque na
comparação de texto `20E…` fica acima de `2026/08`. A trava de `LEN` segue valendo contra
outras deformações, mas não é ela que resolve este caso (a docstring de `filtro_ref_477()`
diz isso corretamente).

**5.3 · Analítica é FOLHA da árvore, não "tem N níveis".** O plano mistura profundidades:
`1.1.1.02.003` e `4.1.6.01.0013` têm 5 níveis, mas `4.1.6.01` tem 4 e é sintética. Contar
níveis deixava conta sintética passar na trava. 218 analíticas de 312.

**5.4 · O rodapé do 456 INCLUI programados.** O crédito do rodapé é o total, não o do
realizado. Os 117 programados valem R$ 172.260,35 — a tela mostra os dois saldos.

**5.5 · Transferência entre contas próprias aparece 2×.** R$ 106.030.174,10 de volume em
1.586 linhas (que somam R$ 0,00, porque as duas pontas se anulam). Crédito bruto dá
R$ 132,7 mi contra R$ 79,4 mi reais.

**5.6 · `faturas_441_ctrcs[valor_frete]` é o valor CHEIO do CTRC.** Um CTRC repartido aparece
inteiro em cada fatura: são **11 CTRCs espalhados por 40 faturas**, e a diferença entre os dois
grãos é **R$ 172.850,39** (grão CTRC R$ 42.334.657,06 × grão fatura R$ 42.161.806,67). Para
valor, `vlr_ctrcs` do grão fatura. E o grão CTRC precisa ser recortado **pelas faturas do
período**, não pela emissão do CTRC — 365 faturas liquidadas em agosto contra 94 CTRCs
emitidos em agosto.

**5.7 · O saldo mensal não precisa de reextração.** A identidade
`saldo_inicial + créditos + débitos = saldo_final` fecha ao centavo nas 13 contas, e a corrente
de meses reproduz o rodapé. Conferido contra o saldo corrido do próprio SSW em 4 amostras.
**Todo mês tem que existir para toda conta**, mesmo sem movimento — senão o saldo da conta
parada some do consolidado.

**5.8 · O CPF vem preenchido com zeros até 14.** `00076617874668` é CPF, `50482284000178` é
CNPJ. Testar `LEN <= 11` dá zero.

**5.9 · `SUM(ABS(col))` não é DAX válido** — precisa de `SUMX`.

**5.10 · Não existe campo de "tem nota fiscal" no 477.** `nfiscal` está preenchido em 151.622
de 151.622 (com lixo: `DIARIAS` tem `nfiscal=1`), `chave_nfiscal` é só NFe e serviço emite
NFS-e, `cfop` é genérico (1949/2949). Por isso a flag dela é necessária, não redundante.

**5.11 · `extrato_bancario_456_totais` ACUMULA um jogo inteiro de contas por execução do
robô.** O `DELETE` da carga é por conta+período e o `periodo_fim` anda todo dia, então nada é
substituído — em 20/08/2026 eram 3 jogos (39 linhas para 13 contas). O acúmulo é intencional
(§7), então **quem escolhe a execução é quem consome**. Quem lê a tabela sem cortar repete cada
banco uma vez por dia de carga e multiplica o saldo consolidado: dava R$ -8.685.893,62 no lugar
de R$ -2.543.039,89, e derrubava o teste do rodapé (BRADESCO com 15.669 movimentos contra 5.142
carregados). O corte é o mais recente **por conta**, nunca um `MAX(periodo_fim)` global — numa
carga parcial o global derruba a conta que faltou e o saldo dela some calado, e banco faltando
é pior que banco repetido. Corrigido em `_quadro_bancos()`, em `contabil_testes.py` e na medida
`saldo_final_rodape`. ⚠ **As demais tabelas do módulo não acumulam** — 456, 441, 441_ctrcs e
571 têm um único `periodo_fim`/`data_importacao` (conferido).

---

## 6. Estado atual — o que está no ar

### Telas (`@page_required('contabil')`)

| Rota | O que faz |
|---|---|
| `/contabil` | quadro por banco, seletor de mês, drawer por conta |
| `/contabil/extrato` | 456 com AutoFilter + CSV, coluna `regra` calculada |
| `/contabil/faturas` | 441 nos dois grãos, recorte por `pagamento` |
| `/contabil/acni` | 571, recorte por `liquidac` |
| `/contabil/eventos` | **configuração**: conta + flags + histórico + prévia |
| `/contabil/contas-fixas` | 13 bancárias + contrapartida de fornecedores |

### Tabelas locais

```
contabil_plano_contas    312 contas · 218 analíticas · 65 destino de despesa/receita
contabil_evento_conta     97 eventos · APPEND-ONLY · valor corrente = linha mais nova
                          2 cargas (18/08 sem conta; 15/09 com conta — §11) · 88/97 com conta
contabil_conta_fixa       14 linhas · 12 preenchidas · 2 pendentes
```

### Números de referência (20/08/2026)

Servem de gabarito — se mudarem sem motivo, algo quebrou. A base é viva: espere os valores
andarem entre um dia e outro; o que **não** pode mudar são as identidades (§5.7), as pontes e
a ordem de grandeza.

```
456   24.921 movimentos · 117 programados · 1.586 transferências (somam R$ 0,00)
      créditos com guarda      R$  79.396.255,72   (bruto seria 132.665.290,03)
      saldo rodapé             R$  -2.543.039,89   (com programados; realizado: -2.715.300,24)
      sem regra de classificação  741 mov · R$ -2.414.946,25
441   4.296 faturas · 9.568 CTRCs · pago R$ 42.202.382,50
571   503 ACNIs em 533 linhas · em aberto R$ 272.556,31
477   escopo 2026/01–2026/08: R$ 46.481.697,18 · 90 eventos · 17.492 lançamentos
      precisam de conta: 60 eventos · R$ 13.807.339,84 · preenchidos: 51 (§11)
pontes  456×477 = 100% · 441×455 = 99,9% · 479×477 = 0 divergências · evento×cadastro 90/90
```

⚠ O saldo do rodapé só vale **cortando pela execução mais recente por conta** (§5.11). Somando
a tabela crua dá R$ -8.685.893,62, que é o mesmo número visto três vezes.

**Gabarito executável:** `cd Rizza && python contabil_testes.py` → **17 ok · 0 falhas**.

---

## 7. O que falta

### Depende da contadora

| Pendência | Peso |
|---|---|
| ~~Preencher a conta dos 60 eventos~~ — **51 vieram na planilha de 14/09 (§11)**; faltam 3 com conta fora do plano e 5 de contrato | R$ 2,45 mi |
| ~~`2.1.3.01.001` 166 × 506~~ — **resolvido por uso**: ela escreveu 506 nas 37 linhas de fornecedores | — |
| **Plano atualizado**: as contas 664 (5099), 773 (5217) e 352 (5501) não existem no `rizza para teste.xls` — padrão de conta criada depois; pedido em 15/09 | R$ 0,39 mi |
| **Contas dos 12 contratos sem correspondência** — planilha `Contratos 2026 - consorcios e financiamentos.xlsx` enviada em 15/09 (§11.3) | R$ 0,45 mi |
| Criar **TRIBANCO** e **CAIXA PAMBANK** no plano — hoje não existe gaveta | trava o pagamento dessas contas |
| ~~As regras dos 6 eventos PARCIAL~~ — **decisão de 15/09: considerar o que está na planilha** (506, ver §11.2) | R$ 1,81 mi |

### Depende de nós

| Item | Nota |
|---|---|
| **Tabela de de-para POR CONTRATO** `(evento, numlancto) → conta`, append-only, com `nfiscal`/fornecedor de apoio — hoje as 24 contas de contrato dela não têm onde morar (§11.3) | esperar a planilha voltar preenchida |
| **Gerador do arquivo `Z;...`** | resolve conta em 2 níveis: contrato primeiro, evento depois; 95% do valor já tem conta |
| **Carga da planilha de 14/09 em produção** — feita só no banco local (`importar_eventos_planilha.py`, autor `…(14/09/2026)`) | `--exportar eventos.json` → `docker exec` |
| **Regras de classificação do 456 → tabela** | hoje é `_SWITCH_REGRA_456` no código, e **já mudou uma vez** (a regra do `CREDITO VIA RET BCO` estava presa à origem `BCO` e deixava R$ 2,7 mi fora). Formato: ordem · origem · padrão no histórico · classificação, primeira que casar ganha |
| **Modelo do histórico do lançamento** | o `2` e o texto do complemento são convenção dela |
| Rotação de log da `logs_contabil\` no servidor de automação | pasta nova |

### Decidido NÃO fazer agora

- **Motor de regras para os PARCIAL** — só depois de ela dizer quais são. Se virarem "lista
  de fornecedores", tabela simples resolve; construir antes é adivinhar.
- **`data_importacao` na PK de `extrato_bancario_456_totais`** — a tabela já acumula 13 linhas
  por dia (o `DELETE` é por conta+período e o período muda), então o histórico entre dias
  existe. O que ela sobrescreve é a reexecução no mesmo dia. **O acúmulo fica; quem corrige é
  o consumidor** — ver §5.11, que é o defeito que isso causou e onde foi corrigido.

---

## 8. Arquivos

### Neste repositório

```
server.py                        rotas /contabil/*, APIs, contabil_dax(), guardas,
                                 REF_INICIAL_477, filtro_ref_477(), contas_fixas()
init_db.py                       as 3 tabelas + semente das contas fixas
seed_plano_contas.py             carrega o plano da contadora (rodar quando mudar)
importar_eventos_planilha.py     carga ÚNICA da planilha dela (depois o app é dono)
contabil.html                    quadro por banco
contabil-extrato.html            456
contabil-faturas.html            441
contabil-acni.html               571
contabil-eventos.html            configuração dos eventos
contabil-contas-fixas.html       contas fixas
nav-perms.js                     a aba 'contabil' no menu
```

### No projeto Rizza (extração e análise)

```
docs/base_contabil.md            as 5 fontes, as armadilhas, a operação
contabil_pbi.py                  camada DAX + MEDIDAS validadas + pontes cross-dataset
contabil_testes.py               17 testes — o gabarito
contabil_mapeamento.py           varredura evento → conta (análise, não produção)
contabil_depara.py               planilha de trabalho (superada pela tela)
contabil_diario.bat              roda os 4 robôs, 04:00
criar_tarefa_contabil.ps1        registra a tarefa (rodar NA máquina de produção)
ssw_relatorio {456,441,571,479}.py + ssw_*_postgres.py
```

### Arquivos da contadora (fora do git)

```
Rizza/EVENTOS COM INFORMAÇÕES.xlsx            97 eventos × 9 colunas — a especificação (17/08)
Rizza/EVENTOS COM INFORMAÇÕES - contas.xlsx   + coluna CONTA (reduzido) + 24 contas por contrato (14/09)
Rizza/Contratos 2026 - consorcios e financiamentos.xlsx   os 25 contratos do 477, enviada a ela em 15/09
Rizza/rizza para teste.xls           o plano de contas (312 contas)
Rizza/PARA GABRIEL.xlsx              regras de classificação do 456 e das datas
Rizza/PARA GABRIEL - MODELO IMPORTAÇÃO.txt   layout do Z;... (exemplo, códigos fictícios)
```

---

## 9. Variáveis de ambiente

```
POWERBI_CONTABIL_DATASET_ID = 695ee940-1dd1-41e9-ab92-f4049f9ef183
```

Os demais (`POWERBI_TENANT_ID`, `CLIENT_ID`, `CLIENT_SECRET`, `GROUP_ID`, `DRE_DATASET_ID`)
já existiam.

---

## 10. Como verificar depois de mexer

```bash
cd Rizza && python contabil_testes.py          # 17 ok · 0 falhas
cd "Tabela Auditoria" && python server.py      # http://localhost:5000/contabil
```

Conferir na tela (valores de 20/08/2026 — a base anda, a estrutura não):

- **13 linhas, uma por banco.** Se aparecerem 26 ou 39, o corte por execução (§5.11) caiu e
  cada banco está sendo repetido uma vez por dia de carga.
- **2 sem conta contábil** (TRIBANCO e CAIXA PAMBANK) e **✓ confere em 13 de 13**.
- saldo consolidado **R$ −2.543.039,89** — se der −8.685.893,62, é o mesmo sintoma acima.
- BRADESCO −6.143,72 · SICOOB 694.122,31.
- créditos **R$ 79.396.255,72** (**não** 132.665.290,03 — se der o segundo, a guarda de
  transferência parou de funcionar).

Travas que devem devolver **400**: conta que não existe no plano; conta sintética
(`4.1.6.01`); flag fora de `SIM/NAO/PARCIAL`.

**Permissão:** usuário com `paginas_permitidas = ['contabil']` deve ver só esta aba — a
contadora é externa e não pode enxergar Auditoria, DRE, Veículos nem PGR.

---

## 11. Rodada de 15/09/2026 — a planilha de contas chegou

Em 14/09 ela devolveu `EVENTOS COM INFORMAÇÕES - contas.xlsx`: **mesmos 97 eventos, nenhuma
flag mudou** (conferido coluna a coluna), + coluna `CONTA` em **código reduzido** + 24 sub-linhas
de conta **por contrato**. Junto veio a mensagem: empréstimo/parcelamento são eventos unificados
no SSW mas cada contrato tem conta própria na contabilidade; ela quer ver com o Camilo se criam
mais eventos. **Não afirmamos nada sobre isso** — ela é a contadora; mandamos o que os dados
mostram (§11.3) e esperamos.

Medido contra o 477 no escopo 2026/01→09 (base local de 14/08: R$ 47,26 mi, 90 eventos):

```
37 eventos que não precisavam de conta  -> todos 506               R$ 32,39 mi   68,5%
51 dos 60 que precisavam                -> analítica válida         R$ 12,42 mi   26,3%
 4 com conta fora do plano que temos    -> 664, 773, 352, "não há"  R$  0,39 mi
 5 de contrato                          -> vazias (é por contrato)  R$  2,06 mi
```

Zero sintética, zero digitação torta, zero regressão. **95% do valor resolvido em nível de evento.**

### 11.1 · O que a coluna CONTA significa

Uma conta por evento serve os três casos da mecânica (§4): com `DESPESA = SIM` é a conta de
despesa (grupo 4); com `PROVISÃO = NÃO` é **o que o pagamento debita** — e ela deu passivo/ativo,
não despesa: 5108 ICMS → `ICMS A RECOLHER`, 5148 → `SALÁRIOS A PAGAR`, 5147 → `ADIANTAMENTO DE
SALÁRIO`, 5517 → `VEÍCULOS`, 5503 → conta-corrente do sócio. Coerente: a provisão nasce na folha
ou no fiscal por outra via; o SSW só baixa. Vai em `conta_debito`; a contrapartida é conta fixa.

### 11.2 · Decisões desta rodada

- **PARCIAL = 506, considerar como está.** Os 6 PARCIAL vieram com 506; na mecânica isso equivale
  a NÃO (a competência daria `D 506 / C 506`). Decisão do Gabriel em 15/09: seguir a planilha, não
  perguntar. Se a regra dos PARCIAL vier um dia, é linha nova na tabela.
- **Conta fora do plano fica em branco com o motivo na observação**, nunca entra torta. O
  importador aplica a mesma trava da tela (existe + analítica). Provável que o `rizza para teste.xls`
  esteja velho (o plano tem 661/662 e pula 664; tem 774 e não 773; 350 → 353 sintética) — pedimos
  o plano atualizado em vez de corrigir na mão.
- **5321 SIMPLES = "não há"** é decisão dela (Rizza não é Simples, zero no escopo), não erro.
- **5228 DIÁRIAS** veio `DESPESA = NÃO`, `PROVISÃO = NÃO`, conta 506: ninguém credita fornecedores
  e o pagamento debita 506 (R$ 307 mil). É dedução nossa de partida dobrada, **não foi perguntado**
  — deixar a prévia do arquivo mostrar; ela corrige vendo o lançamento.

### 11.3 · Contratos: a chave já existe no 477

Para 5512 consórcio, 5313 empréstimos, 5513 FINAME, 5515 CDC e 5518 capital de giro a conta é
**do contrato**. No 477, o contrato é o **`numlancto`** (parcelas 01..N) e **`nfiscal` carrega o
número do contrato/cota** — o mesmo número que ela escreveu na descrição das contas
(`5525366` → "CONSORCIO BB AMAROK … 5525366" = 1367). `historico_da_despesa` vem vazio.

25 contratos no escopo (R$ 2,06 mi): **7 casam exato** pelo número (R$ 0,51 mi), **6 prováveis**
(3 por 1 dígito de diferença — `179139546` × "CDC **1790**139546", `20469` × "ITAU **0**2469"; 3 por
fornecedor único — MAGGI, RANDON, capital de giro BB), **12 sem pista** (4 cotas BB, ARACOOP,
FINAME BB e ITAU, ITAUCARD, SAFRA ×2, ADAUTO PIRES pessoa física). 17 das 24 contas dela não
têm uso em 2026 — contratos antigos do balancete, não erro.

Enviada a ela em 15/09 a planilha `Contratos 2026 - consorcios e financiamentos.xlsx` com os 25
(fornecedor, nº lançamento, nº contrato, parcelas, valor; 7 "casou", 6 "a confirmar", coluna em
branco para ela). Quando voltar: criar a tabela `(evento, numlancto) → conta` e o gerador resolve
em dois níveis (contrato → evento). ⚠ Não decidir por ela se cria evento no SSW ou não.

### 11.4 · O que mudou no código

`importar_eventos_planilha.py`: lê `CONTA`, traduz reduzido → classificação pelo plano com a trava
da tela, ignora as sub-linhas de contrato (evento vazio ou em texto) e lista tudo no fim. Autor da
carga: `importação da planilha da contadora (14/09/2026)` — o check de "já importou" é por autor,
então a carga nova entra sem `--force` e a de agosto fica no histórico. **Rodado só no banco
local**; em produção falta `--exportar` + `docker exec`. Testes: 17 ok.
