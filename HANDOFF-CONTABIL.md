# Handoff — Módulo Contábil

Estado em **18/08/2026**. Documento de retomada: quem pegar o projeto daqui deve
conseguir continuar sem reler conversa.

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
Não é filtro de conveniência: no histórico completo a despesa é R$ 332,4 mi e os maiores
eventos são os fretes; no escopo são R$ 46,0 mi e a ordem de trabalho é outra. Mata também
a pendência dos 3 eventos aposentados (5213, 5216, 5410), que têm zero em 2026.
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
0 de 53 contas do 479 existem no plano contábil
0 de 49 contas de débito do 477 existem
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
eventos, 19.354/19.354 lançamentos, mesma chave sem normalização. Por isso não importa se o
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
futuras — a base tem **117 REFs além de 2026/12** (parcelas a vencer de financiamento e
consórcio), somando R$ 25,5 mi. Filtro precisa de **teto e validação de formato**
(existe `REF = '20ES/6'` na base). Ver `server.py:filtro_ref_477()`.

**5.3 · Analítica é FOLHA da árvore, não "tem N níveis".** O plano mistura profundidades:
`1.1.1.02.003` e `4.1.6.01.0013` têm 5 níveis, mas `4.1.6.01` tem 4 e é sintética. Contar
níveis deixava conta sintética passar na trava. 218 analíticas de 312.

**5.4 · O rodapé do 456 INCLUI programados.** O crédito do rodapé é o total, não o do
realizado. Os 188 programados valem R$ 95.766,49 — a tela mostra os dois saldos.

**5.5 · Transferência entre contas próprias aparece 2×.** R$ 103.218.340,04 em 1.554 linhas.
Crédito bruto dá R$ 129,2 mi contra R$ 77,4 mi reais.

**5.6 · `faturas_441_ctrcs[valor_frete]` é o valor CHEIO do CTRC.** Um CTRC repartido aparece
inteiro em cada fatura (45 faturas, R$ 172.850,39). Para valor, `vlr_ctrcs` do grão fatura.
E o grão CTRC precisa ser recortado **pelas faturas do período**, não pela emissão do CTRC —
264 faturas de agosto contra 34 CTRCs emitidos em agosto.

**5.7 · O saldo mensal não precisa de reextração.** A identidade
`saldo_inicial + créditos + débitos = saldo_final` fecha ao centavo nas 13 contas, e a corrente
de meses reproduz o rodapé. Conferido contra o saldo corrido do próprio SSW em 4 amostras.
**Todo mês tem que existir para toda conta**, mesmo sem movimento — senão o saldo da conta
parada some do consolidado.

**5.8 · O CPF vem preenchido com zeros até 14.** `00076617874668` é CPF, `50482284000178` é
CNPJ. Testar `LEN <= 11` dá zero.

**5.9 · `SUM(ABS(col))` não é DAX válido** — precisa de `SUMX`.

**5.10 · Não existe campo de "tem nota fiscal" no 477.** `nfiscal` está preenchido em 151.361
de 151.361 (com lixo: `DIARIAS` tem `nfiscal=1`), `chave_nfiscal` é só NFe e serviço emite
NFS-e, `cfop` é genérico (1949/2949). Por isso a flag dela é necessária, não redundante.

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
contabil_conta_fixa       14 linhas · 12 preenchidas · 2 pendentes
```

### Números de referência (18/08/2026)

Servem de gabarito — se mudarem sem motivo, algo quebrou.

```
456   24.577 movimentos · 188 programados · 1.554 transferências (somam R$ 0,00)
      créditos com guarda      R$  77.370.323,21   (bruto seria 129.204.825,82)
      saldo realizado          R$  -3.702.969,54   (rodapé, com programados: -3.607.203,05)
      sem regra de classificação  731 mov · R$ 2.424.869,16
441   4.195 faturas · 9.382 CTRCs · pago R$ 41.004.506,88
571   491 ACNIs em 520 linhas · em aberto R$ 272.539,38
477   escopo 2026/01–2026/08: R$ 45.981.886,37 · 90 eventos · 19.354 lançamentos
      precisam de conta: 60 eventos · R$ 13.732.457,19
pontes  456×477 = 100% · 441×455 = 99,9% · 479×477 = 0 divergências
```

**Gabarito executável:** `cd Rizza && python contabil_testes.py` → **17 ok · 0 falhas**.

---

## 7. O que falta

### Depende da contadora

| Pendência | Peso |
|---|---|
| Preencher a conta contábil dos 60 eventos em `/contabil/eventos` | R$ 13,7 mi |
| **`2.1.3.01.001` tem 2 códigos reduzidos** — 166 FORNECEDOR SC × 506 FORNECEDORES DIVERSOS. O arquivo referencia por código, então é escolha obrigatória | contrapartida de 66% dos lançamentos |
| Criar **TRIBANCO** e **CAIXA PAMBANK** no plano — hoje não existe gaveta | trava o pagamento dessas contas |
| As regras dos **6 eventos PARCIAL** — ela disse que vai passar | R$ 2,33 mi (3,3%) |

### Depende de nós

| Item | Nota |
|---|---|
| **Gerador do arquivo `Z;...`** | as 3 entradas existem; falta o de-para preenchido |
| **Regras de classificação do 456 → tabela** | hoje é `_SWITCH_REGRA_456` no código, e **já mudou uma vez** (a regra do `CREDITO VIA RET BCO` estava presa à origem `BCO` e deixava R$ 2,7 mi fora). Formato: ordem · origem · padrão no histórico · classificação, primeira que casar ganha |
| **Modelo do histórico do lançamento** | o `2` e o texto do complemento são convenção dela |
| Rotação de log da `logs_contabil\` no servidor de automação | pasta nova |

### Decidido NÃO fazer agora

- **Motor de regras para os PARCIAL** — só depois de ela dizer quais são. Se virarem "lista
  de fornecedores", tabela simples resolve; construir antes é adivinhar.
- **`data_importacao` na PK de `extrato_bancario_456_totais`** — a tabela já acumula 13 linhas
  por dia (o `DELETE` é por conta+período e o período muda), então o histórico entre dias
  existe. O que ela sobrescreve é a reexecução no mesmo dia.

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
Rizza/EVENTOS COM INFORMAÇÕES.xlsx   97 eventos × 9 colunas — a especificação
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

Conferir na tela: 13 contas · BRADESCO 111.190,15 · SICOOB −477.048,41 · 2 sem conta contábil ·
créditos R$ 77.370.323,21 (**não** 129.204.825,82 — se der o segundo, a guarda de transferência
parou de funcionar).

Travas que devem devolver **400**: conta que não existe no plano; conta sintética
(`4.1.6.01`); flag fora de `SIM/NAO/PARCIAL`.

**Permissão:** usuário com `paginas_permitidas = ['contabil']` deve ver só esta aba — a
contadora é externa e não pode enxergar Auditoria, DRE, Veículos nem PGR.
