# Tabela Auditoria — Rizza Transportes

Sistema web interno da **Rizza Transportes** que reúne, numa única interface acessível via navegador, duas grandes frentes:

1. **Analítico/financeiro** — auditoria de receita, análise de tarifas, geração de atas de reunião por IA, emissão de contratos TAC por IA e relatórios DRE com assistente financeiro. Substitui múltiplos relatórios Power BI.
2. **Operacional/logística** — lançamento e acompanhamento de cargas (Embarques) e **rastreamento GPS** dos veículos em rota, com mapa em tempo real, detecção automática de saída/entrega e cálculo de rota planejada.

URL de produção: **https://rizza.carvalhoia.com**

---

## Estado atual do rastreamento e do robô (21/09/2026)

**Antes de commitar ou subir qualquer coisa deste repositório, leia esta seção.**
O detalhe de cada item está no `HANDOFF-EMBARQUES-AUTONOMO.md`, §27 em diante.

### O que está ligado em produção

| chave | estado | o que faz |
|---|---|---|
| `EMBARQUES_AUTO` | **true** | o robô abre a carga a partir do manifesto, 1×/dia, D-1 (`JANELA_DIAS=5`) |
| `EMBARQUES_CONTINUACAO` | **true** desde 11/09 | a mercadoria que atravessa duas cargas liga A → B pelo CTe (§24) |
| `EMBARQUES_RECONCILIACAO` | **true** desde 17/09 | manifesto que sumiu do 916 = cancelado → carga vira `Cancelada` (§26.3) |
| `EMBARQUES_COLETA` | **true** desde 18/09 | a ordem de coleta preenche embarcador, CNPJ de origem/destino e `coleta_origem` |
| `EMBARQUES_FITA` | **true** desde 19/09 | um retrato do BI por refresh → `fita_documentos`, `locais` e `embarques_programacao`, que é o que a aba `/embarques/ordens` lê (§27.11) |
| `EMBARQUES_AUTO_POS_REFRESH` | **true** desde 20/09 | o diário roda **após cada refresh do BI** (~8×/dia) em vez de 1×/dia; a janela das 16:30 fica como garantia diária (§27.12) |
| `EMBARQUES_AUTO_DEFASAGEM` | **0** desde 20/09 | a carga nasce do manifesto **do dia**, não de ontem. O robô já espera o CTRB: sem cidade, a carga não nasce (§27.12) |
| `EMBARQUES_ATEMPORAL_DIAS` | **28** | janela do motor convergente; 40 era maior que a retenção de GPS (§25.4) |
| `EMBARQUES_DESENGATE_CAVALO` | **ausente = false** | o gatilho de desengate por GPS, desligado em 19/09 — ver abaixo |
| `EMBARQUES_MODELO_CARRETA` | **ausente = false** | Fase D, ainda não medido com dado novo |
| `RASTREAMENTO_SYNC_AUTO` | **nasce desligada** (21/09) | sincroniza o cadastro `embarques_veiculos_rastreio` sozinho: por **lacuna** (placa que o worker vê e o cadastro não conhece — consulta local, sem cota) + **garantia de 24 h**. Sem ela, o cadastro só anda quando alguém clica no Admin, e veículo novo na 3S fica invisível **para a tela** com a viagem correndo (§27.13 nº 2) |

### O que mudou em 18–19/09, e por quê

- **Só o CTe declara desengate** (§27.9). O gatilho por GPS chamava de desengate o **fim
  normal de viagem** — a carreta chega ao destino e o cavalo sai depois: medido em 8 cargas,
  o desengate era carimbado a 0,0 h da chegada e a carga virava `Entregue` 24 h depois,
  nenhuma com ligação. Isso enchia o card "Carretas desengatadas" de entrega em descarga.
  Desligado, as ligações A → B ficam idênticas e `Desengatada` volta a significar uma coisa só.
- **O desengate "no pátio" exige prova de parada** (§27.5), quando a chave acima for religada:
  último ponto parado, bloco de 2 h e aparelho falando depois do manifesto novo. As 3 cargas
  que existiam em produção estavam no lugar errado — ponto velho ou em movimento.
- **A carga de pátio deixou de ser escondida do documento** (§27.3): o manifesto novo da
  carreta volta a encerrá-la. Antes ela congelava quando a mercadoria não seguia.
- **O `dedup_veiculo` exige prova de chegada** (§27.11). Ele fechava pela **ordem da data**,
  sem olhar GPS — em 18/09 fechou uma carga a 715 km do destino e outra a 1.345 km. A
  exigência existia desde 07/09, mas atrás do `EMBARQUES_MODELO_CARRETA`, que está desligado.

### O que mudou em 21/09, e por quê

- **A tela deixou de dizer "Sem rastreio" para carga rastreada** (§27.13 nº 2). O mapa exigia
  a placa no cadastro `embarques_veiculos_rastreio` — cópia local do `/ListaVeiculos` que só
  andava pelo botão do Admin — e, fora dele, desenhava o **cavalo**, que na C-2026-001011 era
  a placa sem GPS nenhum enquanto a carreta tinha 352 pontos. Agora escolhe a placa que TEM
  trajeto e rotula `fora do cadastro`. Gate: 9/9 pela rota real.
- **O sync do cadastro ficou automático**, atrás de `RASTREAMENTO_SYNC_AUTO`.
- **O salto do `V1` (44 → 64) foi explicado**: 19 das 64 se apoiam em GPS que entrou no banco
  depois do gabarito, e **uma carreta muda que voltou a falar responde por 11** (§27.13 nº 1).
  Nada no robô mudou. O `V1` mistura cobertura de sensor (26 casos) com documento (18).
- **Três ideias de âncora foram medidas e caíram** (§27.13 nº 4): o `location_type` do Google
  está invertido (os dois `ROOFTOP` foram os dois piores, um a 76 km), o endereço geocodificado
  **introduz regressão** (2 cargas perdem prova) e o ponto provado por GPS **não muda decisão
  nenhuma**. A precisão da âncora não era o gargalo; cobertura de sensor é.
- **O teto de 3 da reconciliação fica como está**: zero sumiço de manifesto em 11 comparações
  intradiárias, em três dias de fita (§27.13 nº 3).

### O resto do quadro

- **3S de volta desde 08/09/2026** (o corte comercial de 07/09 durou um dia). 93 veículos
  reportando; ficaram 5 carretas mudas desde 01–03/09 (§20.1).
- **O robô atemporal** roda sozinho logo depois do diário, em quatro passos (§22.10).
- **Snapshots de produção** guardados para restore por id: `snap_20260911_1944`,
  `snap_20260915_2007`.

### O que fazer antes de qualquer deploy

```bash
git status --short          # tem de estar limpo, ou você sabe exatamente o que está subindo
git diff --stat
```

Mudança em `embarques_auto.py`, `embarques_continuacao.py`, `rastreamento_worker.py`,
`_robo_atemporal.py` ou nos geradores de perna passa pelo **gate da §24.5**: rodar
`_rodar_diario_local.py` (o robô REAL na base local, com snapshot antes) e comparar com o
gabarito do `_replay_producao.py`. Os simuladores não viram dois bugs que o robô real viu.

### Voltar atrás sem deploy

| o quê | como |
|---|---|
| desligar o robô | `EMBARQUES_AUTO=false` |
| desligar a continuação | `EMBARQUES_CONTINUACAO=false` |
| desfazer o que a continuação gravou | `_snapshot_embarques.py restaurar snap_X --chave --aplicar` (por id; nunca as tabelas de posição) |
| desligar a fita documental | `EMBARQUES_FITA=false` (as tabelas ficam; só a aba de ordens as lê) |
| religar o desengate por GPS | `EMBARQUES_DESENGATE_CAVALO=true` — **medido e reprovado em 19/09**, §27.9 |
| desligar o sync automático do cadastro | `RASTREAMENTO_SYNC_AUTO=false` (o botão do Admin continua funcionando) |
| comparar código com o estado anterior | tags `modelo-manual-2026-09`, `estudo-embarques-2026-09-08`, `pre-continuacao-2026-09-11`, `pre-lab-2026-09-15`, `pre-patio-2026-09-18`, `pre-cadastro-2026-09-21` |

Env sempre pela CLI (`docker service update --env-add`): editar pelo stack do Portainer devolve
a imagem antiga (21/08/2026).

---

## Funcionalidades

### Para todos os usuários autenticados
- **Auditoria Receita** (`/`) — Dashboard com KPIs e tabela de auditoria de receita, com filtros, drag-and-drop de colunas e exportação CSV. **Abre já filtrada no mês corrente** (fallback: mês mais recente com dados), **ordenada por data decrescente** (mais recente primeiro, ancorada por chave) e com **filtro por Cliente Pagador**
- **Tarifas** (`/tarifas`) — Consulta de tabela de fretes em cascata (cliente → origem → destino → tipo veículo) + simulador de frete. **Comparativo de até 4 blocos** (rotas/clientes lado a lado, cada um com seu simulador) + **resumo consolidado** que reflete o filtro. **Total + Impostos (ICMS)** como linha informativa para o comercial — usa `icms_valor` ou a matriz `icms_aliquota` (cálculo por dentro). Mostra também **ICMS Incluso**, **Pedágio Incluso** e **Prazo de Recebimento** (colunas `icms_incluso`/`pedagio_incluso`/`prazo_recebimento` já publicadas no dataset Power BI)
- **Embarques** (`/embarques`) — Lançamento de cargas (Terceiro / Agregado / Frota), relatório filtrável + CSV, edição com log de auditoria por campo e histórico. **Agendamento por destino** (data/hora com o cliente), com filtro "Por agendamento", **badge de atraso** (agendamento vencido + carga ativa) e **ETA realista** (~600 km/dia). Suporta **viagem vazia** (carga sem cliente) e **cidades de rota/passagem** (pontos que moldam o caminho da rota planejada sem serem destino de entrega). **Desengate de carreta carregada** (drop-and-hook): libera cavalo+motorista para outra viagem com a carreta ainda no destino aguardando descarga (ver Módulo Embarques). **Continuação** (`EMBARQUES_CONTINUACAO`): quando a mercadoria atravessa duas cargas (desengate no pátio, manifesto novo no hub, reemissão), a primeira aponta para a segunda (`→ C-B`) e só a segunda conta como entrega
- **Mapa / Rastreamento** (`/embarques/mapa`, `/embarques/cargas/<id>/mapa`) — Mapa em tempo real (Leaflet): posição dos veículos e trajeto de cada carga, rota planejada **origem → cidades de rota → destinos** (completa, multi-ponto), KPIs de viagem **ao vivo** (vel. máx/média, km, tempos) e **Data de saída** no painel da carga. Fluxo de status automático **Aberta → Em rota → No destino → Entregue** (`No destino` = parado na cidade da descarga há +60 min), com desvio **Desengatada** (carreta carregada largada no destino). **Rastreia pela carreta** (carreta1 → cavalo → carreta2 — o GPS costuma estar na carreta). **Reconstrói o trajeto** mesmo em lançamento tardio: detecta a saída da origem pelo GPS (por distância, pois o 3S erra o nome da cidade) e persiste em `inicio_viagem`. Mapa geral tem filtro **🔌 Desengatadas** e marcador próprio. **Salto impossível**: a linha do GPS QUEBRA no trecho que o veículo não pode ter feito (velocidade implícita acima do teto físico, ou odômetro negando o deslocamento) — duas bolinhas âmbar marcam as pontas do buraco, e o km desse trecho sai do `KM PERCORRIDOS`, mas segue medido pelo `KM RASTREADOR` (o odômetro é cumulativo no aparelho e não depende da posição). Régua única em `embarques_regua.py`

### Restrito a admins
- **Verda — Emissões CO₂e** (`/verda`) — Acompanhamento do inventário de CO₂e enviado à plataforma **Verda** (exigência da Nestlé, escopo 3 do embarcador). Placar da rodada (enviadas / `executed` / rejeitadas / bloqueadas), inventário (t CO₂e, km, peso, diesel, **intensidade g/t·km**, escopo 1 × escopo 3), consumo aplicado por faixa de km/l, `VehicleTypeKey`, **as placas que estão bloqueando envio** (lista copiável para o cadastro resolver) e detalhe por viagem com drill nos CTes + CSV. **Não chama a Verda**: lê a `verda_envios` e recalcula o CO₂e com o fator reconstruído da API `Fuel` — na conta gratuita a Verda guarda só o consolidado mensal, então para o dado por viagem esta é a única tela que existe. Janela padrão = semana fechada anterior. **Consumo**: escala por idade do veículo (3,00 / 2,80 / 2,50 / 2,20 km/l) e 3,70 no rígido, revisada pela diretoria em 10/09/2026 — a mesma régua vale para os indicadores ABIQUIM. Ver `HANDOFF-VERDA.md` (§19 registra a divergência com o ciclo medido no ValeCard)
- **Reunião** (`/reuniao`) — Gerador de ata de reunião a partir de áudio. Transcreve via AssemblyAI (com identificação de falantes) e gera ata profissional via GPT-4.1-mini. Exporta em Word e PDF
- **Contratos** (`/contratos`) — Emissão de **contrato TAC Agregado** por IA. O operador sobe os documentos (CNH, CRLV, RNTRC/ANTT, comprovante de endereço, dados bancários); o GPT-4.1-mini (visão) **extrai os campos** de cada documento-fonte correto, o backend reconfere **pendências impeditivas** em Python e preenche o **template Word soberano** (`contrato_tac_template.docx` via docxtpl — o texto jurídico nunca é tocado). Gera **comodato de rastreador** quando o agregado não usa rastreador próprio. Exporta `.docx` e oferece **preview HTML** (para "Salvar como PDF" pelo navegador)
- **DRE** (`/dre`) — Demonstração do Resultado do Exercício com 4 gráficos analíticos (Waterfall, Donut por Grupo, Pareto 80/20, Comparativo Mensal) e chat IA financeiro com streaming em tempo real
- **Despesas** (`/dre/despesas`) — Auditoria detalhada de `consulta_despesas_477` com filtros, drilldown por grupo/evento, **AutoFilter estilo Excel por coluna** (funil no cabeçalho; via `report-filter.js`) e exportação CSV em streaming
- **Conhecimentos** (`/dre/conhecimentos`) — Auditoria detalhada de `conhecimentos_emitidos` com filtros, **AutoFilter estilo Excel por coluna** e exportação CSV em streaming
- **Faturamento por Tomador** (`/faturamento`) — Matriz **tomador × meses** (faturamento e nº de cargas) de `conhecimentos_emitidos`, **consolidada por raiz de CNPJ** (junta filiais do mesmo grupo). Toggle R$/Cargas, busca, ordenação e CSV; cards e subtotal acompanham o filtro. **Filtro de rota** (origem → destino): cada lado aceita **estado ou cidade**, com autocomplete ordenado por volume. **Carga = manifesto distinto**, contado já no grão da raiz — a mesma viagem faturada em duas filiais conta **uma vez** (ver Módulo Faturamento por Tomador)
- **Análise por Veículo** (`/veiculos`) — Análise de receita/custo por **Cavalo / Carreta / Motorista / Proprietário**, sobre `Auditoria Receita` (receita **rateada**, nunca duplica). Filtros de **mês/competência** e **tipo** (Frota/Agregado/Carreteiro) como **dropdown (caixinhas + Aplicar)**. Na visão **Cavalo + só Frota**: custos reais por cavalo — **Pedágio** (Sem Parar), **Combustível/ARLA** (ValeCard, com **consumo km/L** via hodômetro), **Pessoal** (folha), **Manut. Cavalo / Seguro / Rastreador** e **Pneu** (eventos 5411/5412, split cavalo×carreta por nº de pneus) rateados → **Resultado Frota**; mostra **KM Rota** (manifesto) e **KM Abast.** (hodômetro) lado a lado. Na visão **Carreta**: **Manut. Carreta** + **Pneu** rateados entre as carretas Rizza (frota+agregado). **Coluna Proprietário** (dono do cavalo no recorte cavalo; donos dos cavalos no drawer da carreta). **Donut "Composição do Faturamento"** quando só um tipo está marcado (custos diluídos na receita + Resultado). **Painel lateral (drawer)** com cargas, abastecimentos, pedágios, manutenção real, relacionamentos e quebra por veículo. **Placas normalizadas para Mercosul** (funde grafia antiga + nova; trata colisão preferindo a Mercosul real)
- **PGR — Excesso de velocidade** (`/pgr`) — Relatório diário das placas que passaram de 95 km/h, enviado por WhatsApp toda manhã (imagem-resumo + link). Detecção sobre o histórico de GPS **backfillado** da 3S, com **situação de carga provada por posição** (carregado / parcial / vazio / não confirmado), rodovia do pico e rodapé de **cobertura por placa**. Traz o **cavalo do par** nas linhas de carreta. **Filtros por cavalo, carreta e motorista** (digitáveis) e **seleção de mês** com múltipla escolha, mostrando só os meses já apurados. Acesso por sessão (aba `pgr`) **ou** por token de leitura no link — o token abre **um dia só**, sem período nem filtro. Ver Módulo PGR
- **Jornada** (`/jornada`) — Escala **motorista × placa × período** que o RH envia à empresa de controle de jornada (que puxa a jornada pela telemetria da placa). Montada do **manifesto** (por CPF) e confirmada pelo **ValeCard** (o nome informado na bomba — é o que cobre o truck, que roda sem manifesto); entre duas provas o motorista segue na placa da última até outro motorista assumi-la ou passar 15 dias sem registro. Lista = motoristas da folha (`custo_pessoal`) **com INSS/FGTS no mês** (quem só tem plano de saúde está parado, informado à parte). Seleção de **ciclo do RH (21→20)** ou período livre; ciclo em curso só até hoje. **Modo auditoria**: clicar no motorista abre os manifestos e abastecimentos de cada período, com as **viagens do Embarques** (saída, chegada e encerramento medidos pelo GPS). O Embarques não diz QUEM dirigia — nasce do mesmo manifesto —, ele diz QUANDO a viagem começou e acabou, e é isso que **arbitra o ValeCard**: abastecimento em nome de outra pessoa não tira o motorista da placa no meio da própria viagem (o nome vem do cartão ou é digitado na bomba do CAIS; o cartão de quem saiu da empresa ainda aparece). Quem manda continua sendo o manifesto: viagem que o robô fechou tarde não devolve o motorista para a placa velha. A linha do tempo mostra a situação de cada dia (em viagem · vazia · placa parada · andou sem viagem), da consolidação diária de GPS. Avisos: dias sem registro (com o motivo), período **só pelo ValeCard** (validar à mão), placa com dois motoristas no mesmo dia, manifesto fora da frota. **Baixa a planilha no layout `Plan1`** que o RH já usa. Só leitura — correção é pedido ao time. Lógica em `jornada.py`
- **CIOT — Conferência** (`/ciot`) — Três cruzamentos sobre o 073 e o 916: **CTRB tem CIOT válido** (a mensagem de erro da ANTT/Pamcard gravada no campo não conta), **CTRB está no manifesto certo** (073 × 916, placas e motorista) e **todo manifesto tem CTRB**. Usa o mesmo vínculo manifesto → CTRB da Auditoria Receita, incluindo a fórmula que casa os órfãos (`CHAVE_CTRB`). Roda após cada refresh do BI (10 min de folga), guarda as pendências com "vista desde" e "resolvida em" e avisa por WhatsApp (resumo diário + novas). **Filtro por data de emissão** (de/até, com atalhos Hoje · 7 dias · Mês · Tudo) que vale para cartões, tabela e CSV. Acesso pela aba `ciot` ou pelo link de leitura da mensagem. Ver Módulo CIOT
- **Contábil** (`/contabil`) — Base contábil do SSW para o fechamento: **posição por banco** (uma linha por conta, com a conta contábil do plano da PERSETO e o ✓ de conferência contra o rodapé do próprio extrato), drawer de composição por origem/regra, e os relatórios de **extrato (456)**, **faturamento (441, grão fatura e CTRC)**, **adiantamentos (571)** com AutoFilter e CSV. Traz ainda a **configuração**: `/contabil/eventos` (conta contábil por evento, as flags da contadora, histórico de edição e prévia do impacto) e `/contabil/contas-fixas`. É a aba concedida à contadora externa — ver Módulo Contábil e `HANDOFF-CONTABIL.md`
- **Admin** (`/admin`) — Gerenciamento de usuários, papéis, **permissão de acesso por aba** (cada usuário recebe quais abas enxerga; admin vê todas por bypass) e permissões por tipo de operação

---

## Stack Técnica

### Backend
- **Python 3.11** + **Flask** + **Flask-CORS**
- **PostgreSQL** — autenticação de usuários **e** dados operacionais (cargas, posições GPS, log, KPIs)
- **psycopg2-binary** — driver Postgres
- **python-dotenv** — variáveis de ambiente
- **werkzeug** — hash de senhas
- **threading** — worker daemon de rastreamento (roda dentro do mesmo processo Flask)

### Fontes de Dados
- **Power BI REST API** — dados analíticos (DRE, tarifas, auditoria, despesas, conhecimentos, contábil) e cadastros de origem (motoristas, veículos) via consultas DAX
  - 3 datasets diferentes:
    - Dataset principal (auditoria + tarifas + motoristas + veículos)
    - Dataset DRE (DRE + despesas + conhecimentos)
    - Dataset `tabelas.contabil` (extrato 456 + faturamento 441 + ACNI 571 + eventos 479)
- **PostgreSQL local** — toda a escrita operacional: cargas, destinos, log de edição, posições GPS, KPIs de viagem, centroides de municípios
- **3S Tecnologia (DataExportAPI)** — posições GPS dos rastreadores dos veículos (com modo simulado para desenvolvimento)
- **OpenRouteService** — cálculo de rota rodoviária para caminhões (HGV): polyline, distância e duração
- **IBGE** — autocomplete de cidades (no formulário) e centroides de municípios (geocoding do trajeto)

### Integrações IA
- **OpenAI GPT-4.1-mini** — geração de atas, chat financeiro e **extração de dados de documentos por visão** (contratos TAC: CNH, CRLV, RNTRC etc.)
- **AssemblyAI** — transcrição de áudio com diarização de falantes (universal-3-pro + universal-2)

### Frontend
- HTML + CSS + JavaScript puro (sem framework, sem build step)
- **Chart.js 4.4** + plugins `annotation` e `datalabels` (gráficos do DRE)
- **Leaflet** — mapas de rastreamento (posições e trajetos)
- **marked.js** — renderização de markdown na ata e no chat
- **Sortable.js** — drag-and-drop de colunas
- Fontes: DM Sans + JetBrains Mono

### Exportação
- **python-docx** — geração de Word (atas)
- **docxtpl** — preenchimento do template de contrato TAC (Word soberano)
- **PyMuPDF** (`fitz`) — rasteriza PDFs de documentos em imagens para a IA de extração
- **mammoth** — converte o `.docx` do contrato em HTML (preview/impressão)
- **reportlab** — geração de PDF (atas)
- CSV nativo (streaming server-side para grandes volumes)

### Infraestrutura
- **Docker Swarm** + **Traefik** (proxy reverso com SSL via Let's Encrypt) + **Portainer**
- **GitHub** — repositório de código
- **Cloudflare** — DNS (proxy desativado, modo DNS only)
- **Hostinger VPS** — Ubuntu rodando o cluster Docker

---

## Arquitetura

```
┌─────────────────────────────────────────────────────────────┐
│                      Usuário (Browser)                       │
└─────────────────────────────────────────────────────────────┘
                            │ HTTPS
                            ▼
┌─────────────────────────────────────────────────────────────┐
│              Cloudflare DNS (sem proxy)                      │
└─────────────────────────────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│             Hostinger VPS — Ubuntu 22.04                     │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Docker Swarm                                          │  │
│  │  ┌─────────────┐  ┌─────────────────────────────────┐ │  │
│  │  │  Traefik    │──│  Flask App (server.py)          │ │  │
│  │  │  (SSL LE)   │  │   └─ Worker rastreamento (60s)   │ │  │
│  │  └─────────────┘  └────────┬────────────────────────┘ │  │
│  │                            │                           │  │
│  │                   ┌────────┴────────┐                  │  │
│  │                   ▼                 ▼                  │  │
│  │            ┌──────────────┐  ┌──────────────┐         │  │
│  │            │  Postgres    │  │ Power BI API │         │  │
│  │            │ (auth +      │  │  (via DAX)   │         │  │
│  │            │  operacional)│  └──────────────┘         │  │
│  │            └──────────────┘                           │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
            │              │              │            │
            ▼              ▼              ▼            ▼
   ┌────────────┐  ┌────────────┐  ┌──────────┐  ┌──────────┐
   │ OpenAI     │  │ AssemblyAI │  │ 3S GPS   │  │ ORS /    │
   │ (GPT-4.1)  │  │ (Transcr.) │  │ (posições)│  │ IBGE     │
   └────────────┘  └────────────┘  └──────────┘  └──────────┘
```

---

## Estrutura de Arquivos

```
Tabela Auditoria/
├── server.py                    # Backend Flask (rotas + lógica) — ~4.100 linhas
├── init_db.py                   # Criação de todas as tabelas locais (idempotente)
├── requirements.txt             # Dependências Python
├── Dockerfile                   # Imagem Docker
├── docker-compose.yml           # Stack do Portainer (Swarm)
├── .env                         # Variáveis de ambiente (não commitado)
├── .gitignore / .dockerignore
│
│   # ── Páginas HTML ──
├── login.html                   # Página de login
├── index.html                   # Auditoria Receita (dashboard principal)
├── tarifas.html                 # Tarifas de frete + simulador
├── reuniao.html                 # Gerador de ata
├── contratos.html               # Emissão de contrato TAC Agregado por IA
├── admin.html                   # Gerenciamento de usuários
├── dre.html                     # DRE com gráficos e chat IA
├── dre-despesas.html            # Auditoria detalhada de despesas
├── dre-conhecimentos.html       # Auditoria detalhada de conhecimentos
├── faturamento.html             # Faturamento por tomador (matriz tomador × mês, admin)
├── veiculos.html                # Análise por Veículo (cavalo/carreta/motorista/proprietário + drawer de detalhe, admin)
├── report-filter.js             # Componente de AutoFilter estilo Excel (compartilhado por Conhecimentos e Despesas)
├── embarques.html               # Landing de embarques (KPIs + atalhos)
├── embarques-novo.html          # Formulário de lançamento de carga
├── embarques-relatorio.html     # Listagem + filtros + CSV + edição + histórico
├── mapa.html                    # Mapa geral (todos os veículos)
├── mapa-carga.html              # Mapa de uma carga (trajeto + rota planejada)
│
│   # ── Módulo Jornada (escala motorista × placa p/ o RH) ──
├── jornada.py                   # Motor puro: carry-forward, segmentos, avisos (sem DAX/Flask)
├── jornada.html                 # Página /jornada (ciclo 21→20, auditoria por período, download .xlsx)
│
│   # ── Módulo PGR (excesso de velocidade) ──
├── pgr.py                       # Motor: episódios, sustentada, situação de carga, persistência
├── pgr_imagem.py                # Imagem-resumo do WhatsApp (fitz.Story)
├── pgr_envio.py                 # Envio via UazAPI (passo 3 do job diário)
├── pgr.html                     # Página /pgr (sessão ou token de leitura)
├── backfill_historico.py        # Reprocessamento manual do histórico da 3S
├── consolidar_dias.py           # Consolidação placa+dia avulsa (salva o que a purga ainda não levou)
├── placas.py                    # Normalização antiga ↔ Mercosul (server + pgr)
├── fonts/                       # JetBrains Mono embutida na imagem (+ OFL)
│
│   # ── Módulo CIOT (conferência CTRB × manifesto × CIOT) ──
├── ciot_conferencia.py          # Régua (função pura), gravação em ciot_pendencias, WhatsApp e laço
├── ciot.html                    # Página /ciot
├── ciot_imagem.py               # Imagem do WhatsApp (fitz.Story, mesmo molde do pgr_imagem)
├── _teste_ciot.py               # Regressão da régua (um caso por regra, sem rede nem banco)
│
│   # ── Módulo Contratos TAC ──
├── contratos_service.py         # Extração IA (visão) + pendências + render do template
├── contrato_tac_template.docx   # Template Word soberano (texto jurídico fixo + campos docxtpl)
├── _build_template.py           # Deriva o template a partir do contrato-origem (rodar 1x)
│
│   # ── Ordem de coleta, reconciliação de documento e fita (Fase A da Programada) ──
├── embarques_coleta.py          # ordem de coleta (0157) → embarcador, CNPJ e ponto físico da carga
├── embarques_reconciliacao.py   # manifesto que sumiu do 916 = cancelado → carga vira Cancelada
├── _fita_documentos.py          # retrato do BI por refresh (`fita_documentos`) + agendador (EMBARQUES_FITA)
├── _locais.py                   # `locais_fontes` + view `locais` (CNPJ → endereço), alimentada pela fita
├── _programacao.py              # `embarques_programacao`: ordens com estado derivado
├── embarques-ordens.html        # Página /embarques/ordens (ordens de coleta e seu estado)
│
│   # ── Módulo Rastreamento ──
├── rastreamento_worker.py       # Worker daemon (60s): posições, saída/entrega auto, recálculo de rota
├── embarques_continuacao.py     # §24: desengate de pátio + ligação A→B (continua_em), atrás de EMBARQUES_CONTINUACAO
├── _snapshot_embarques.py       # Snapshot por schema + restore POR ID das tabelas derivadas de embarques (nunca posições)
├── _ensaio_continuacao.py       # Gate §24 (estático): regras propostas × reação de cada peça, só leitura
├── _replay_producao.py          # Gate §24 (progressivo): reproduz produção dia a dia; gabarito do robô real
├── _rodar_diario_local.py       # Gate §24: o robô REAL dia a dia na base LOCAL (grava; nunca em produção)
├── tres_s_client.py             # Cliente da API 3S (token bucket, cache de token, retry)
├── simulador_3s.py              # Stub da 3S (lê de embarques_simulacao) p/ MODO_SIMULADO
├── ors_client.py                # Cliente OpenRouteService (rota HGV)
├── geocoding.py                 # Haversine + normalização + geocoder de municípios
│
│   # ── Scripts de seed / setup (rodar 1x) ──
├── import_municipios.py         # Importa centroides IBGE → municipios_ibge
├── seed_icms.py                 # Popula matriz ICMS (origem UF × destino UF)
└── seed_simulacao.py            # Cria 7 cargas/posições de teste p/ o fluxo de rastreamento
```

---

## Variáveis de Ambiente

Configuradas no Portainer (em produção) ou no `.env` local (desenvolvimento):

### Núcleo
| Variável | Descrição |
|---|---|
| `SECRET_KEY` | Chave de sessão do Flask |
| `DB_HOST` | Host do Postgres (em produção: `postgres`) |
| `DB_PORT` | Porta do Postgres (5432) |
| `DB_NAME` | Nome do banco (`rizza_auditoria`) |
| `DB_USER` | Usuário do Postgres |
| `DB_PASSWORD` | Senha do Postgres |

### Power BI
| Variável | Descrição |
|---|---|
| `POWERBI_TENANT_ID` | Azure AD Tenant ID |
| `POWERBI_CLIENT_ID` | Client ID do app registrado no Azure |
| `POWERBI_CLIENT_SECRET` | Client Secret |
| `POWERBI_GROUP_ID` | Workspace ID no Power BI |
| `POWERBI_DATASET_ID` | Dataset ID principal (auditoria + tarifas + motoristas + veículos) |
| `POWERBI_DRE_DATASET_ID` | Dataset ID do DRE |
| `POWERBI_CONTABIL_DATASET_ID` | Dataset ID de `tabelas.contabil` (456 · 441 · 571 · 479) |

### IA
| Variável | Descrição |
|---|---|
| `OPENAI_API_KEY` | Chave da OpenAI (ata + chat IA) |
| `ASSEMBLYAI_API_KEY` | Chave da AssemblyAI (transcrição) |

### Verda (CO₂e)
| Variável | Descrição | Default |
|---|---|---|
| `VERDA_AMBIENTE` | `teste` ou `producao` — decide URL **e** par de chaves | `teste` |
| `VERDA_SIMULADO` | `1` responde sem tocar a rede (não gasta transação) | `0` |
| `VERDA_URL_PRODUCAO` | Host de produção (é o **mesmo** da homologação; o que muda são as chaves) | — |
| `VERDA_URL_TESTE` | Host de homologação | embutido |
| `VERDA_APPLICATION_KEY` | Chave da aplicação. **Produção e homologação são contas diferentes** | — |
| `VERDA_SECRET_KEY` | Chave secreta (a autenticação exige o par; uma sozinha não vale) | — |
| `VERDA_VEHICLE_TYPE_KEY` | Carimba o `VehicleTypeKey`. **Só homologação** — em produção NÃO definir, senão a classificação real não chega à Verda | vazio |

### Rastreamento
| Variável | Descrição | Default |
|---|---|---|
| `START_WORKER` | Liga o worker de rastreamento no boot (`true` para ligar) | desligado |
| `MODO_SIMULADO` | `true` usa `simulador_3s` (lê `embarques_simulacao`); `false` chama a 3S real | `true` |
| `TRES_S_BASE_URL` | URL base da DataExportAPI da 3S | — |
| `TRES_S_USUARIO` | Usuário da 3S | — |
| `TRES_S_SENHA` | Senha da 3S | — |
| `OPENROUTE_API_KEY` | Chave do OpenRouteService (rota HGV) | — |
| `RASTREAMENTO_INTERVALO` | Intervalo do ciclo do worker (segundos) | `60` |
| `RASTREAMENTO_CICLOS_CONFIRMACAO` | Ciclos consecutivos p/ confirmar evento | `3` |
| `RASTREAMENTO_RAIO_KM` | Raio (km) de confirmação de evento (chegada na cidade) | `5` |
| `RASTREAMENTO_RAIO_SAIDA_ORIGEM` | Raio (km) p/ confirmar saída da **origem** (pátio/base, raio menor) | `30` |
| `RASTREAMENTO_RAIO_SAIDA_DESTINO` | Raio (km) p/ confirmar saída do **destino** (pode ser grande centro, raio maior) | `60` |
| `RASTREAMENTO_DESVIO_KM` | Desvio (km) da rota que dispara recálculo | `10` |
| `RASTREAMENTO_RECALCULO_MIN` | Intervalo mínimo (min) entre recálculos de rota | `30` |
| `RASTREAMENTO_RETENCAO_DIAS` | Retenção do histórico de posições | `30` |
| `RASTREAMENTO_KM_DIA_MAX` | Teto de km/dia na consolidação diária — acima disso é troca de rastreador, não viagem | `2500` |
| `RASTREAMENTO_TETO_KMH` | **Teto físico do cavalo.** Acima disso o trecho é posição falsa: a linha quebra e o km não soma | `150` |
| `RASTREAMENTO_SALTO_MIN_KM` | Deslocamento mínimo para a régua acima olhar o trecho — abaixo é jitter de GPS, não teleporte | `30` |
| `RASTREAMENTO_RECONSTRUCAO_DIAS` | Teto p/ trás na detecção da saída da origem (`inicio_viagem`) | `15` |
| `KM_DIA_PADRAO` | Km/dia usado na ETA realista | `600` |
| `BACKFILL_HISTORICO` | Liga o backfill diário do histórico da 3S | `true` |
| `PGR_HORA_BRT` | Horário do job diário em **Brasília** (`HH:MM`) | `06:35` |
| `PGR_JANELA_DISPARO_MIN` | Tolerância p/ disparar após o horário (restart) | `30` |
| `BACKFILL_ESPACO_SEG` | Segundos entre chamadas do backfill (~6/min) | `10` |

### Lançamento automático de embarques (robô do manifesto SSW)
| Variável | Descrição | Default |
|---|---|---|
| `EMBARQUES_AUTO` | **Liga o motor.** `false` para o operacional voltar a lançar à mão | `false` |
| `EMBARQUES_AUTO_FECHAMENTO` | Deixa o robô ENCERRAR cargas (criar e fechar são chaves separadas) | `true` |
| `EMBARQUES_AUTO_HORA_BRT` | Horário do job em Brasília (a carga do SSW chega ~05:10). Somado à janela de disparo tem de caber no mesmo dia — acima de ~21:00 nunca dispara | `16:30` |
| `EMBARQUES_AUTO_JANELA_DISPARO_MIN` | Tolerância p/ disparar após o horário (restart) | `180` |
| `EMBARQUES_AUTO_DEFASAGEM` | Dias para trás (1 = ontem; **0 = o dia corrente**, em produção desde 20/09) | `1` |
| `EMBARQUES_AUTO_POS_REFRESH` | O diário dispara quando o BI carrega algo novo (marcador `MAX(data_importacao)`, 1 consulta de ~1 s) em vez de 1×/dia. A janela de `HORA_BRT` **continua valendo como garantia diária** — sem ela, um dia sem refresh deixaria o operacional sem carga nenhuma. Decisão isolada em `deve_rodar`, com regressão em `_teste_gatilho.py` | `false` |
| `EMBARQUES_AUTO_JANELA_DIAS` | Dias varridos por execução (cobre o CTRB que sai em D+1) | `5` |
| `EMBARQUES_AUTO_TIPOS` | Tipos que o robô lança | `Frota,Agregado` |
| `EMBARQUES_AUTO_MAX_DESTINOS` | Acima disso é distribuição → 1 destino + observação | `8` |
| `EMBARQUES_AUTO_DESTINOS_CTRC` | Admite a cidade do CTRC como destino (batem em só 69%) | `false` |
| `EMBARQUES_AUTO_FILIAIS` | JSON sigla→`Cidade/UF`, usado só quando não há CTRB | `{}` |
| `EMBARQUES_MODELO_CARRETA` | **⛔ CONGELADA** — liga o modelo carreta-cêntrico no fechamento (manifesto novo só da mesma carreta, dedup com prova de chegada, reanálise de pendências). Depende do GPS, que está fora do ar desde 07/09/26. **Não ligar antes de ler a seção "CONGELADO" no topo** | `false` |
| `EMBARQUES_AUTO_REANALISE` | Sub-chave da anterior: revisita pendência com evidência posterior | `true` |
| `EMBARQUES_AUTO_JANELA_REANALISE_DIAS` | Até onde a reanálise olha para trás (a retenção de GPS é ~30 d) | `30` |
| `EMBARQUES_AUTO_DWELL_ENTREGA_H` | Horas paradas no destino que valem como entrega, quando não há saída | `24` |
| `EMBARQUES_CONTINUACAO` | **Continuação/desengate de pátio (§24).** Manifesto novo do cavalo com outra carreta vira `Desengatada` (não `Entregue`); CTe com `primeiro_manifesto ≠ ultimo_manifesto` liga A → B (`continua_em`) com o terminal certo (`Desengatada`/`Continuada`/`Cancelada`); só B conta como entrega. Nasce desligada; desligar pela CLI, nunca pelo stack do Portainer | `false` |
| `EMBARQUES_CONTINUACAO_RAIO_DESTINO` | Km do destino abaixo do qual o desengate é "no destino" (comportamento antigo); acima é "no pátio" (o worker não rastreia; só o documento encerra) | `25` |
| `EMBARQUES_DESENGATE_CAVALO` | **Gatilho de desengate por GPS** (o cavalo saiu com outra carreta). **Desligado em 19/09**: medido, ele chamava de desengate o fim normal de viagem — a carreta chega e o cavalo sai depois — e errava o "pátio" em cima de posição velha ou em movimento. A ligação A → B pelo CTe **não depende dele** | `false` |
| `EMBARQUES_ATEMPORAL` | Liga o robô atemporal depois do diário (motor → pernas → rotas → janela) | `true` |
| `EMBARQUES_ATEMPORAL_DIAS` | Janela do motor convergente. 28, não 40: acima da retenção de GPS ele re-deriva com a evidência sumindo | `40` (em produção: **28**) |
| `EMBARQUES_RECONCILIACAO` | **Reconciliação de documento**: manifesto que sumiu do 916 = cancelado → a carga do robô vira `Cancelada`; voltou → status anterior lido do log. Só carga do robô, nunca editada à mão, teto de 3 por rodada | `false` |
| `EMBARQUES_COLETA` | **Ordem de coleta → carga**: preenche campo vazio de carga do robô (`coleta_origem`, `embarcador`, `origem_cnpj`/`destino_cnpj` e endereços). Nenhuma régua lê o que ela grava | `false` |
| `EMBARQUES_FITA` | **Fita documental** (Passo 0): thread que vigia o `MAX(data_importacao)` do BI e, a cada refresh novo, tira um retrato completo em `fita_documentos` + cadastro `locais` + `embarques_programacao` — a tabela que a aba `/embarques/ordens` lê | `false` |
| `EMBARQUES_FITA_INTERVALO_MIN` | De quanto em quanto a fita pergunta o marcador (1 consulta, ~1 s) | `10` |
| `EMBARQUES_FITA_JANELA_DIAS` | Dias para trás em cada retrato | `7` |
| `EMBARQUES_FITA_RETENCAO_DIAS` | Purga da fita. **Não é zelo, é dimensionamento**: cada rodada grava ~660 linhas e ~1 MB (retrato inteiro, não delta), e são 8 refreshes/dia | `21` |

### Conferência CIOT
| Variável | Descrição | Default |
|---|---|---|
| `CIOT_CONFERENCIA` | **Liga o laço** no servidor. Só lê o BI e grava `ciot_pendencias` | `false` |
| `CIOT_DESDE` | Documentos emitidos a partir desta data (retroativo a 01/09, decisão de 17/09/2026) | `2026-09-01` |
| `CIOT_INTERVALO_MIN` | `0` = roda **só depois de cada refresh do BI** (hoje 8×/dia). `>0` força rodada extra | `0` |
| `CIOT_ESPERA_POS_REFRESH_MIN` | Folga depois do **fim** do refresh antes de ler; durante um refresh não roda | `10` |
| `CIOT_CARENCIA_H` | Horas que a pendência espera antes de ir no aviso (o CTRB costuma sair depois do manifesto) | `2` |
| `CIOT_ENVIO` | **Liga o WhatsApp** (chave separada da conferência) | `false` |
| `CIOT_AVISO_SEM_NOVIDADE` | **Batimento**: avisa a cada rodada mesmo sem novidade (`✅ nada novo desde HH:MM`, em texto curto). Sem ela, rodada sem pendência nova é silêncio — e silêncio não separa "nada aconteceu" de "o robô parou" | `false` |
| `CIOT_UAZAPI_TO` | Destinatário(s), separados por vírgula (usa `UAZAPI_URL`/`UAZAPI_TOKEN`) | — |
| `CIOT_ENVIO_HORARIO` | Janela de envio em Brasília; fora dela o aviso espera | `06:00-22:00` |
| `CIOT_RESUMO_HORA_BRT` | Resumo diário de tudo que segue aberto (1×/dia, na primeira rodada depois deste horário) | `08:00` |
| `CIOT_RESUMO_DIAS` | O resumo lista só os emitidos nesses dias; os mais antigos aparecem contados e ficam na tela | `7` |
| `CIOT_FORMATO` | `imagem` (imagem + legenda curta, como o PGR) ou `texto` | `imagem` |
| `CIOT_INTERVALO_ENVIO_SEG` | Segundos entre um destinatário e o próximo | `75` |
| `CIOT_BASE_URL` | Base do link da mensagem (cai no `PGR_BASE_URL`) | — |

### PGR (relatório de excesso de velocidade)
| Variável | Descrição | Default |
|---|---|---|
| `PGR_LIMIAR` | Limite de velocidade (km/h) | `95` |
| `PGR_TETO` | Teto anti-ruído — acima disso é equipamento travado | `130` |
| `PGR_GAP_EPISODIO` | Minutos que separam dois episódios | `10` |
| `PGR_MIN_REGISTROS_SUSTENTADO` | Registros no episódio p/ marcar "sustentado" | `2` |
| `PGR_GAP_COBERTURA_RUIM` | Minutos sem sinal **em movimento** p/ alarmar cobertura | `60` |
| `PGR_DESVIO_CORREDOR` | `(dist_O + dist_D) / dist_OD` aceitável | `1.35` |
| `PGR_RAIO_CIDADE` | Raio (km) que conta como "esteve na cidade" | `25` |
| `PGR_DIAS_LOOKBACK` | Dias de posição carregados p/ provar a passagem pela origem | `12` |
| `PGR_MAX_DIAS_MANIFESTO` | Teto de idade do manifesto (sanidade, não critério) | `20` |
| `PGR_MANIFESTOS_DIAS` | Janela do cache de manifestos | `30` |
| `PGR_SYNC_CADASTRO` | Liga a sincronização 12/12h do cache (server) | `true` |
| `PGR_IMG_MAX_LINHAS` | Placas na imagem do WhatsApp | `12` |
| `PGR_ENVIO` | **Liga o envio** por WhatsApp | `false` |
| `PGR_UAZAPI_TO` | Destinatário(s), separados por vírgula | — |
| `PGR_INTERVALO_ENVIO_SEG` | Segundos entre um destinatário e o próximo | `75` |
| `PGR_BASE_URL` (ou `PGR_URL`) | Base do link do relatório (`https://rizza.carvalhoia.com`) | — |
| `UAZAPI_URL` / `UAZAPI_TOKEN` | Credenciais da UazAPI | — |

---

## Endpoints da API

### Autenticação
- `GET /login` — página de login
- `POST /login` — autenticação (retorna sessão)
- `GET /logout` — limpa sessão
- `GET /api/me` — info do usuário logado

### Páginas (HTML)
- `GET /` — Auditoria Receita
- `GET /tarifas` — Tarifas
- `GET /reuniao` — Reunião (admin)
- `GET /contratos` — Contratos TAC (admin)
- `GET /dre` — DRE (admin)
- `GET /dre/despesas` — Despesas (admin)
- `GET /dre/conhecimentos` — Conhecimentos (admin)
- `GET /faturamento` — Faturamento por Tomador (admin)
- `GET /veiculos` — Análise por Veículo (admin)
- `GET /contabil` — Contábil: posição por banco
- `GET /contabil/extrato` · `/contabil/faturas` · `/contabil/acni` · `/contabil/eventos` · `/contabil/contas-fixas`
- `GET /admin` — Admin (admin)
- `GET /embarques` — Landing de embarques
- `GET /embarques/novo` — Formulário de lançamento
- `GET /embarques/relatorio` — Relatório de cargas
- `GET /embarques/<id>/editar` — Edição de carga
- `GET /embarques/ordens` — Ordens de coleta (0157) com estado derivado
- `GET /embarques/mapa` — Mapa geral de rastreamento
- `GET /embarques/cargas/<id>/mapa` — Mapa de uma carga

### Dados analíticos
- `GET /api/status` — status da config Power BI
- `GET /api/auditoria` — dados de auditoria
- `GET /api/tarifas` — tabela de tarifas
- `GET /api/icms?origem=XX&destino=YY` — alíquota de ICMS de transporte (matriz `icms_aliquota`): `{aliquota, tipo, isento, observacao}`; 404 se o par não existir
- `POST /api/dax` — query DAX customizada
- `GET /api/dre?meses=YYYY-MM,...` — DRE estruturada
- `GET /api/dre/detalhamento?meses=...` — detalhamento por subgrupo/evento
- `GET /api/dre/despesas?start=...&end=...&grupo=...&evento=...` — despesas filtradas
- `GET /api/dre/conhecimentos?start=...&end=...` — conhecimentos filtrados
- `GET /api/dre/despesas/csv?...` — CSV streaming de despesas
- `GET /api/dre/conhecimentos/csv?...` — CSV streaming de conhecimentos

### Reunião
- `POST /api/reuniao/processar` — recebe áudio, transcreve e gera ata
- `POST /api/reuniao/exportar` — exporta ata em Word ou PDF

### Contratos TAC (admin)
- `POST /api/contratos/extrair` — recebe documentos (multipart), extrai os dados via IA de visão e devolve `{dados, pendencias}`. Aceita `dados` já editados para **mesclar** numa reextração com mais documentos (preserva edições manuais)
- `POST /api/contratos/gerar` — valida pendências impeditivas (+ comodato) e devolve o `.docx` do contrato preenchido
- `POST /api/contratos/preview` — mesma validação e devolve o contrato em **HTML** (para "Salvar como PDF" pelo navegador)

### Chat IA
- `POST /api/chat-dre` — chat financeiro com streaming SSE

### Faturamento por Tomador (admin)
- `GET /api/faturamento/tomadores?ano=2026` — grão **(tomador, origem, destino, mês)** de `conhecimentos_emitidos`, consolidado por raiz de CNPJ. Cada grupo devolve a **lista de manifestos** (`mf`), não uma contagem: é o que permite à tela filtrar por rota e ainda contar carga certo, porque `cargas` é `DISTINCTCOUNT` recalculado sobre o conjunto filtrado. Origem = `cidade_origem_prestacao`, destino = `cidade_destinatario`. Payload ~0,3 MB/ano
- `GET /report-filter.js` — componente JS do AutoFilter (servido como estático, igual ao `nav-perms.js`)

### Análise por Veículo (admin)
- `GET /api/veiculos/analise?dim=&meses=&tipos=` — agrega `Auditoria Receita` por `dim` (cavalo|carreta|motorista|proprietario), filtrado por competência (`meses=YYYY-MM,...`) e tipo. Grão = `(dim, Tipo Operacao)` → mesma carreta com viagens Frota **e** Agregado vira **2 linhas**. Receita = `SUM(receita_rateada)`; FROTA tem pagamento de frete = 0. Em **cavalo+só Frota** anexa custos rateados (pedágio Sem Parar, combustível/ARLA + km hodômetro do ValeCard, pessoal/folha, manut. cavalo/seguro/rastreador/**pneu** das despesas) + `prop_cavalo`. Em **carreta** anexa manut. carreta + **pneu** (base de rateio = todas as carretas Rizza, fixa) e o flag `rizza`. **Pneu** (eventos 5411/5412, sem placa) = pool dividido cavalo×carreta pelo nº de pneus dos veículos Rizza ativos (`_pneus_por_veiculo`), rateado por faturamento. Placas normalizadas em Mercosul (colisão resolve p/ a Mercosul real); placa vendida (`PLACAS_VENDIDAS`) não conta como frota
- `GET /api/veiculos/detalhe?dim=&valor=&meses=&tipos=` — detalhe (drawer) de um veículo/pessoa: cargas, abastecimentos (ValeCard), pedágios (Sem Parar), manutenção real (despesas via placa no `historico_despesa`), relacionamentos (**com o proprietário de cada cavalo/carreta**) e quebra por veículo. Reconcilia com a linha da tabela (respeita o filtro de tipo; resolve as 2 grafias Mercosul via `_placa_grafias`)

### Jornada (aba `jornada`)
- `GET /api/jornada?inicio=&fim=` — escala do período (padrão: ciclo 21→20 em curso; máx. 4 meses). Cada motorista traz `segmentos` (placa, início, fim, nº de manifestos/abastecimentos, `so_valecard`, `provas`), `sem_prova` (com `motivo`) e `alertas`; mais `fora_lista` (rodou veículo da frota sem estar na folha) e `parados` (folha sem INSS/FGTS). Cache de 5 min
- As viagens e o km por placa/dia vêm do Postgres local (`embarques_cargas`, `embarques_cargas_destinos`, `embarques_rastreio_dia`); horários são gravados em UTC e exibidos em Brasília. Sem essas tabelas a aba funciona igual, só sem o enriquecimento (`embarques: false` na resposta)
- `GET /api/jornada/xlsx?inicio=&fim=` — a planilha no layout `Plan1` do RH (nome + pares placa/período), placa no formato do sistema

### CIOT (aba `ciot`, ou link de leitura `?t=`)
- `GET /ciot` — página. Sessão com a aba `ciot`, **ou** `?t=<token>` do link do WhatsApp (sem login, sem menu, sem ação). Token inválido/revogado → 410 com página explicativa
- `GET /api/ciot/pendencias?status=abertas|resolvidas|todas[&t=]` — lê `ciot_pendencias` + a última rodada; devolve `modo` (`sessao`/`leitura`) e, só para admin, o `link` atual
- `POST /api/ciot/rodar` — roda a conferência agora, **sem** enviar WhatsApp (só sessão com a aba)
- `POST /api/ciot/link` — **admin**: revoga o link de leitura e gera outro

### Contábil
- `GET /api/contabil/quadro` — 13 contas do 456 com conta contábil, saldo e `confere` (contagem carregada × rodapé do extrato), mais os totais **já com as guardas** (sem transferência interna, sem programado) e o contador de movimentos sem regra
- `GET /api/contabil/banco?conta=` — drawer: quebra por origem, por regra de classificação e os 12 maiores movimentos
- `GET /api/contabil/extrato?start=&end=&banco=&transferencias=&programados=` — movimentos do 456 com a coluna calculada `regra`; devolve em `oculto` o que os filtros esconderam. Confere o total recebido contra `COUNTROWS` e **aborta se vier cortado**
- `GET /api/contabil/faturas?grao=fatura|ctrc` — 441 nos dois grãos
- `GET /api/contabil/acni` — 571 + identidade recebido − aplicado = em aberto
- `GET /api/contabil/plano-contas?analitica=1&grupos=3,4` — alimenta os campos de escolha
- `GET /api/contabil/eventos` — configuração por evento, ordenada por R$ com % acumulado; a lista é a UNIÃO de (usados no 477 ∪ cadastro do 479 ∪ já configurados), para que evento desativado no SSW não desapareça
- `POST /api/contabil/eventos` — grava uma LINHA NOVA (append-only). Recusa conta que não existe no plano, conta sintética e flag fora do domínio
- `GET /api/contabil/eventos/historico?evento=` — quem mudou o quê, quando
- `GET/POST /api/contabil/contas-fixas` — 13 bancárias + contrapartida de fornecedores
- `GET /api/contabil/{extrato,faturas,acni}/csv` — CSV streaming

### Admin
- `GET /api/admin/users` — lista usuários
- `POST /api/admin/users` — cria usuário
- `PATCH /api/admin/users/<id>` — atualiza usuário
- `DELETE /api/admin/users/<id>` — remove usuário

### Embarques (todos sob `@login_required`)
- `GET /api/embarques/motoristas` — motoristas (`public motoristas_047` via DAX, cache 5min). CPF é a chave única
- `GET /api/embarques/veiculos` — veículos (`public veiculost_045` via DAX, `eh_rizza` calculado server-side, cache 5min, `?refresh=1` bypassa)
- `GET /api/embarques/clientes` / `POST /api/embarques/clientes` — lista / cadastra cliente (dedup case-insensitive)
- `GET /api/embarques/embarcadores` — lista de usuários (filtro "quem lançou")
- `GET /api/embarques/conflitos?cpf=&placas=` — checa CPF/placa já em carga ativa
- `POST /api/embarques/cargas` — cria carga (snapshot + destinos **com `data_agendamento`** em transação, geocoding origem/destinos, rota ORS origem→destino, gera `numero`, retorna warnings)
- `GET /api/embarques/cargas` — listagem com filtros (período, tipo, cliente, embarcador, motorista, UF, status, busca livre). Dois filtros distintos para viagem vazia: **`viagem_vazia`** (a flag, pega as duas espécies) e **`perna_vazia`** (só a perna DERIVADA do robô — `viagem_vazia AND criada_por_robo`). A home usa o segundo, para não esconder a viagem vazia lançada à mão junto com as pernas. Com a continuação ligada devolve também `continua_em`, `continua_em_numero`, `desengate_local`, `continuacao_de`, `continuacao_de_id` (NULL com a chave desligada)
- `GET /api/embarques/cargas/<id>` — detalhe (carga + destinos)
- `PATCH /api/embarques/cargas/<id>` — edita (diff por campo → `embarques_cargas_log`; status `Entregue` preenche `data_conclusao`)
- `POST /api/embarques/cargas/<id>/desengatar` — desengate de carreta carregada: `status='Desengatada'`, libera cavalo+motorista do conflito (carreta segue comprometida), seta `no_local_desde` se nulo e registra substituto opcional + log. Requer status `Em rota`/`No destino` e permissão de edição
- `GET /api/embarques/cargas/<id>/log` — histórico de edições
- `GET /api/embarques/cargas/csv` — CSV streaming (mesmos filtros)
- `GET /api/embarques/ordens?dia=&estado=&embarcador=` — ordens de coleta de `embarques_programacao`, com o **estado derivado** (`carga` · `documento emitido` · `aguardando manifesto` · `vencida sem documento` · `sem veículo` · `cancelada`) — a `situacao` do SSW não fecha sozinha. Alimentada pela fita; **sem `EMBARQUES_FITA` a tabela não existe e a aba abre vazia** (a API devolve 200 com lista vazia, não erro)
- `GET /api/embarques/kpis` — 7 contadores (hoje, em rota, no destino, entregues no mês, abertas, desengatadas, **vazias no mês**). **`entregues_mes` não conta viagem vazia** — a perna de reposicionamento nasce `Entregue` porque já aconteceu, e inflava o número que o operacional lê como entrega ao cliente (35 de 130 na medição de 10/09/26) — **nem carga com `continua_em`** (a perna 1 de um desengate/continuação; 7–12% a mais, §24). **`desengatadas` conta todo status `Desengatada`**, esperando ou já ligada: é o mesmo conjunto que o clique no card lista

### Rastreamento (todos sob `@login_required`)
- `GET /api/rastreamento/posicoes` — posições atuais + info da carga ativa (filtros: `carregado`, `eh_rizza`, `q`)
- `GET /api/rastreamento/cargas/<id>/trajeto` — trajeto percorrido (1 placa principal) + rota planejada origem→destino + `rastreado_via` + **KPIs ao vivo** + `distancia_restante_km`/`eta_realista_iso` (janela a partir de `inicio_viagem`). Traz **`trajeto_cortes`** (índices onde a linha deve quebrar — perna impossível) e **`kpi.pernas_falsas`** (quantas o km não somou)
- `POST /api/rastreamento/cargas/<id>/confirmar-entrega` — confirma entrega manualmente
- `POST /api/rastreamento/sync-veiculos` — sincroniza mapeamento placa → idVeiculo da 3S
- `GET /api/rastreamento/health` — saúde da integração (última posição, status do worker)
- `GET /api/rastreamento/log` — log de chamadas às APIs externas (3S/ORS/SIM)

---

## Desenvolvimento Local

### 1. Pré-requisitos
- Python 3.11+
- Postgres rodando localmente (ou tunnel SSH para o servidor)

### 2. Configurar `.env`
Criar arquivo `.env` na raiz do projeto com as variáveis listadas acima. Para desenvolvimento sem a 3S, deixe `MODO_SIMULADO=true`.

### 3. Instalar dependências
```bash
pip install -r requirements.txt
```

### 4. Inicializar o banco (primeira vez)
```bash
python init_db.py
```
Cria a tabela `auditoria_users` (+ admin padrão `admin@rizzalog.com.br` / `admin123`) e **todas** as tabelas de embarques e rastreamento. É idempotente — pode rodar novamente sem apagar dados.

### 5. Carregar dados de apoio (primeira vez)
```bash
python -X utf8 import_municipios.py   # centroides IBGE → municipios_ibge
python seed_icms.py                   # matriz ICMS (origem × destino)
```

### 6. (Opcional) Seed de simulação de rastreamento
```bash
python -X utf8 seed_simulacao.py            # cria 7 cargas/posições de teste
python -X utf8 seed_simulacao.py --reset    # apaga e refaz
python -X utf8 seed_simulacao.py --avancar AAA1234 50   # move 50km na direção do destino
```

### 7. Rodar
```bash
python server.py
```
Acesse `http://localhost:5000`. Para ligar o worker de rastreamento, defina `START_WORKER=true` no `.env`.

---

## Deploy em Produção

### Fluxo padrão
1. Commit das alterações:
```bash
git add . && git commit -m "feat: descricao" && git push origin main
```

2. No servidor via SSH:
```bash
cd /opt/stacks/rizza-auditoria && git pull && docker build -t ghcr.io/ggabrielmilho-web/rizza-auditoria:latest . && docker service update --force --image ghcr.io/ggabrielmilho-web/rizza-auditoria:latest rizza-auditoria_app
```

### Em caso de cache de layers Docker
Se a alteração não refletir, força rebuild sem cache:
```bash
docker build --no-cache -t ghcr.io/ggabrielmilho-web/rizza-auditoria:latest .
```

### Diagnóstico
Verificar se o código novo está no container:
```bash
docker exec $(docker ps -q -f name=rizza-auditoria) cat /app/<arquivo>
```

Logs em tempo real:
```bash
docker service logs rizza-auditoria_app -f
```

---

## Modelo de Dados

### Autenticação
```sql
CREATE TABLE auditoria_users (
    id              SERIAL PRIMARY KEY,
    nome            VARCHAR(255) NOT NULL,
    email           VARCHAR(255) UNIQUE NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    role            VARCHAR(20) DEFAULT 'viewer',   -- 'admin' ou 'viewer'
    ativo           BOOLEAN DEFAULT true,
    tipos_permitidos TEXT[] DEFAULT ARRAY['Carreteiro','Agregado','Frota'],
    criado_em       TIMESTAMP DEFAULT NOW()
);
```

### Embarques (Postgres local)
| Tabela | Função |
|---|---|
| `embarques_cargas` | Carga com snapshot completo de motorista/veículos como TEXTO (preserva histórico). `numero` no formato `C-AAAA-000001`. **`viagem_vazia`** = carga sem cliente (`cliente_nome` aceita NULL). `status` ∈ `Aberta`/`Em rota`/`No destino`/**`Desengatada`**/`Entregue`/**`Continuada`**/`Cancelada` (VARCHAR sem CHECK). **Continuação (§24)**: `continua_em` (id da carga em que a mercadoria seguiu — com ela preenchida o status é terminal, seja qual for a palavra) e `desengate_local` (`destino` = comportamento antigo; `patio` = carreta largada longe do destino, o worker não rastreia). Campos de rastreamento: `origem_latitude/longitude`, `data_saida_real`, `saida_auto`, `no_local_desde`, `entregue_auto`, `rota_planejada_polyline`, `distancia_planejada_km`, `duracao_estimada_min`, `rota_recalculada_em`, **`inicio_viagem`** (saída da origem detectada pelo GPS e persistida 1×). Desengate: `desengatada_em`, `desengatada_por_id/nome`, `descarga_motorista_nome`, `descarga_cavalo_placa` (substituto opcional) |
| `embarques_cargas_destinos` | Destinos múltiplos por carga (`ordem`, cidade, UF, lat/lng, **`data_agendamento`** = compromisso c/ cliente, `entregue_em`/`entregue_por_*`) |
| `embarques_cargas_rota` | **Cidades de rota/passagem** por carga (`ordem`, cidade, UF, lat/lng). Moldam o caminho da rota planejada (origem → rota → destinos) — **não são pontos de entrega** |
| `embarques_cargas_log` | Auditoria de edição — 1 linha por campo alterado |
| `clientes` | Tabela existente reutilizada; índice único case-insensitive p/ cadastro manual |

### Rastreamento (Postgres local)
| Tabela | Função |
|---|---|
| `embarques_3s_token` | Token da 3S persistido (sobrevive a restart; single-row `id=1`) |
| `embarques_veiculos_rastreio` | Mapeamento placa → idVeiculo da 3S |
| `embarques_posicoes_atuais` | Última posição por placa (UPSERT pelo worker) |
| `embarques_posicoes_historico` | Timeline de posições (dedup `placa+data_posicao`) |
| `embarques_cargas_rastreio_kpi` | KPIs consolidados por carga (distância, velocidade, tempo em movimento/parado). PK = `carga_id`, então **só existe linha enquanto o caminhão está dentro de um documento** |
| `embarques_rastreio_dia` | **Consolidação diária placa+dia** (dia de Brasília) — `odo_ini`/`odo_fim`/`km_odo`, `km_gps`, nº de posições, tempo em movimento/parado, cidade e o `carga_id` ativo. **`carga_id` nulo = dia sem documento**, que é o km vazio. Gravada 1×/dia **antes** da purga; sobrevive à retenção (ver Consolidação diária) |
| `municipios_ibge` | Centroides IBGE p/ geocoding de origem/destino |
| `embarques_3s_log` | Log de chamadas a APIs externas (provider `3S` / `ORS` / `SIM`) |
| `embarques_simulacao` | Fonte de posições quando `MODO_SIMULADO=true` |
| `pgr_eventos` | Resultado do PGR, grão de **episódio** (sobrevive à retenção das posições; `UNIQUE(placa, ini)` p/ reprocesso). Guarda o **par** `placa_cavalo`/`placa_carreta` do manifesto e o `entregue_em` que prova o "vazio" |
| `pgr_cobertura` | Cobertura por placa/dia — separa lacuna qualquer de lacuna **em movimento** |
| `pgr_tokens` | Token de leitura por relatório (link do WhatsApp), com validade e contagem de acesso |
| `pgr_cadastro_veiculos` | Cache de `veiculos_045` (o worker não fala Power BI) |
| `pgr_manifestos` | Cache de `Auditoria Receita`, já geocodificado, p/ provar a situação de carga |
| `icms_aliquota` | Matriz ICMS de transporte (origem UF × destino UF): `aliquota`, `tipo`, `isento`, `observacao`. Popula via `seed_icms.py`; consumida por `/api/icms` e pelo "Total + Impostos" das Tarifas |

### Tabelas Power BI (somente leitura via DAX)
- `public conhecimentos_emitidos` — ~149 colunas. Filtrada por `data_autorizacao`. Campos-chave p/ integração: `cliente_pagador`/`cnpj_pagador`, `valor_frete`, `primeiro_manifesto`, `placa_cavalo`/`placa_carreta`, `serie_numero_ctrc`
- `public consulta_despesas_477` — 50 colunas. Filtrada pela coluna calculada `REF` (formato `YYYY/MM`)
- `public tarifas_frete` — tabela de tarifas por cliente/rota/veículo
- `public motoristas_047` — motoristas (chave única `cpf`)
- `public veiculost_045` — veículos (cavalo/carreta/truck) — usado em Embarques
- `public veiculos_045` — cadastro de veículos (`placa`, `proprietario`, `tipo`, `disponivel`, `modelo`) — usado na Análise por Veículo
- `public semparar_lancamentos` — pedágios Sem Parar (`placa_veiculo`, `data` texto DD/MM/YYYY, `valor`, `sentido_praca`, `embarcador`)
- `public abastecimentos_valecard` — abastecimentos ValeCard (`placa`, `dch_data`, `produto`, `ncd_quantidade` litros, `mcd_valor_total/unitario`, `nsd_hodometro`, `estabelecimento`, `motorista`)
- `public custo_pessoal` — folha por funcionário (`competencia` YYYY-MM, `total_mes`) — motoristas/operação
- `Auditoria Receita` — fato de auditoria (grão CTRB; `receita_rateada`, `placa_cavalo`, `placa_carreta`, `motorista`, `Tipo Operacao`, `CTRC`)

Dataset `tabelas.contabil` (aba Contábil):
- `public extrato_bancario_456` — movimento a movimento das 13 contas. Colunas que carregam regra: `transferencia_interna` (a mesma transferência aparece 2×), `realizado` (FALSE = programado, previsão), `ref_477_uni/numlancto/parcela` (ponte para a despesa)
- `public extrato_bancario_456_totais` — 1 linha por conta/período com saldo inicial, créditos, débitos e saldo final **do rodapé do próprio extrato** — é a trilha de auditoria
- `public faturas_441` — grão fatura; `vlr_ctrcs` é o valor rateado à fatura
- `public faturas_441_ctrcs` — grão CTRC; `valor_frete` é o valor **cheio** do CTRC (ver Módulo Contábil)
- `public acni_571` — adiantamentos, grão mestre-detalhe (`linha_mestre`)
- `public eventos_479` — cadastro de eventos + de-para contábil (`conta_debito`, `conta_credito`)

---

## Módulo Embarques

Registra carregamentos de carga e centraliza o que antes era lançado manualmente e de forma fragmentada.

- **Tipos de operação**: `Frota`, `Agregado`, `Terceiro` — classificados automaticamente pelas **regras Rizza** (proprietário do cavalo/carreta `ILIKE '%RIZZA%'`): ambos Rizza → Frota; só um → Agregado; nenhum → Terceiro. Conflito gera **alerta amarelo**, não bloqueio.
- **Snapshot**: motorista (nome + CPF + telefone) e veículos são gravados como texto no momento do lançamento, preservando o histórico mesmo que a origem mude.
- **Identificador de motorista**: CPF (único em `motoristas_047`, resolve homônimos). O datalist exibe `"Nome — CPF"`.
- **Destinos múltiplos**: ordem livre definida pelo líder da carga.
- **Cidades**: autocomplete via API IBGE.
- **Exclusão**: não há DELETE — cancelamento via mudança de status para `Cancelada`.
- **Auditoria**: toda edição grava diff por campo em `embarques_cargas_log`, visível no modal "🕐 Histórico".
- **Desengate de carreta carregada** (`status='Desengatada'`): na fila de descarga, o cavalo+motorista são desengatados e seguem para outra viagem; a **carreta carregada permanece no destino**. O botão "🔌 Desengatar" (relatório) **libera cavalo+motorista** do conflito (podem entrar em carga nova) mantendo a **carreta ainda comprometida**, registra `desengatada_em`/responsável e o **substituto opcional** (cavalo/motorista que vai terminar a descarga) no histórico. A carga **finaliza automaticamente** quando a carreta sai do destino (worker), ou manualmente em "🏁 Finalizar descarga". Visibilidade: card de KPI "Carretas desengatadas", filtro/badge no relatório e filtro 🔌 no mapa geral.
- **Quem declara desengate automaticamente** (desde 19/09/2026): **só o CTe**, em
  `ligar_continuacoes` — quando `primeiro_manifesto ≠ ultimo_manifesto` prova que a mercadoria
  atravessou duas cargas, a primeira recebe `continua_em` e o terminal certo. O gatilho que
  olhava o GPS (o cavalo saiu com outra carreta) está **desligado** por medição: ele chamava de
  desengate o fim normal de viagem — a carreta chega ao destino e o cavalo sai depois, que é
  **descarga** — e errava o "pátio" em cima de posição velha ou em movimento. Ver §27.9 do
  handoff; religar é `EMBARQUES_DESENGATE_CAVALO=true`.

Detalhes da Fase 1 estão em [`PLANO-EMBARQUES.md`](PLANO-EMBARQUES.md).

### Lançamento automático a partir do manifesto (`embarques_auto.py`)

O time operacional não foi treinado para lançar, e a carga já existe no SSW —
o robô abre a carga sozinho a partir do **manifesto**, 1×/dia, em D-1.

**Por que o manifesto e não o CTRB:** o manifesto é emitido quando o motorista
sai (ele viaja com o documento na mão); o CTRB é a OS de pagamento. Medido em
jul-ago/26 sobre 565 manifestos de frota+agregado: grão limpo (1 manifesto =
1 CTRB = 1 par cavalo/carreta), cobertura de 100% em cavalo/motorista/CPF e
97,4% em carreta, e o CTRB sai **no mesmo dia em 97,3%** dos casos. Rodando
D-1 às 07:00, o CTRB já existe em **96,1%**.

**De onde vem cada campo:** manifesto → placas, motorista, CPF, data;
CTRB (`ctrbs_oss`) → cidade de origem/destino e KM; CTRCs → tomador.

Três armadilhas que o desenho evita, todas medidas:

| Armadilha | Regra |
|---|---|
| `previsao_chegada` do CTRB parece previsão de entrega | É prazo de fechamento **semanal** (45 valores distintos em 980, todos quinta 18:00). A previsão sai da ETA de `KM_DIA_PADRAO` km/dia |
| Destino do CTRC ≠ destino da viagem (batem em 69%) | O destino é o do **CTRB** (perna de transporte). A cidade do CTRC vai para a observação, e é **descartada quando é a própria origem** — `cidade_destinatario` costuma ser o CD, e viraria rota terminando no pátio de partida |
| Carga de distribuição com 110 CTRCs pelo RJ | Acima de `MAX_DESTINOS`, 1 destino só + observação: 110 waypoints estouram o ORS e descrevem um roteiro de entrega, não uma viagem |

**Fechamento — viagem nova, e só.** Roda **antes** de criar, para liberar
cavalo/carreta presos:

1. **GPS** — saída do destino (o worker, já existente)
2. **Manifesto novo da mesma placa** encerra a anterior — cobre as 67 de 139
   placas que rodaram 2+ vezes no mês (p50 de 3 dias entre viagens)
3. **Sequência de viagem** (`dedup_veiculo`, roda *depois* de criar) — as duas
   pernas do mesmo dia deixam o mesmo cavalo em duas cargas ativas; só a última
   fica aberta. Desempate pela emissão do CTRB, que tem hora

**Removidas em 03/09/26** — fechavam carga que ainda estava rodando. Medido em
28/08..01/09: as regras fechavam 34 cargas contra 18 do GPS.

| Regra | Por que saiu |
|---|---|
| `baixa_ctrb` | A `chegada` do CTRB é baixa **administrativa** da OS, não chegada física. Fechou 17 cargas (mediana de 2 dias após o carregamento) e em **6 delas o cavalo já tinha entrega provada por GPS** em outra viagem — atropelou rastreamento que funcionava |
| `timeout` | Fechava por idade, sem viagem nova nenhuma |

Viagem que não tem manifesto seguinte é fechada pelo GPS ou pela mão do
operacional. O robô não arbitra.

Encerramento por regra fica em `Entregue` (único status final que as telas
entendem) com `entregue_auto=FALSE` e `encerrada_motivo` gravado — assim não se
confunde com entrega provada por GPS.

**Continuação e desengate de pátio (`EMBARQUES_CONTINUACAO`, §24 do handoff).** A mesma
mercadoria atravessa duas cargas em ~8% das viagens: o cavalo larga a carreta carregada na
filial, outro cavalo a leva (desengate, 33 em 40 dias), ou o conjunto para no hub e ganha
manifesto novo (10), ou o manifesto é reemitido (8). Com a chave ligada, a primeira carga
(A) **não** vira `Entregue`: recebe `continua_em = B` e o terminal certo — `Desengatada`
(cavalo trocou), `Continuada` (mesmo conjunto) ou `Cancelada` (reemissão) — e só B conta
em `entregues_mes`. A palavra `Desengatada` é **ativa sem ligação** (carreta esperando; no
pátio o worker não a rastreia, só o documento encerra) e **terminal com ligação**. Medido:
`entregues_mes` caía 7–12% e 27 pernas vazias fabricadas deixam de existir. Rede de
segurança: tag `pre-continuacao-2026-09-11`, a chave, e `_snapshot_embarques.py` (snapshot
por schema + restore **por id**, nunca as tabelas de posição).

**Segurança do robô:**
- `manifesto_origem` com índice único parcial → rodar duas vezes não duplica
- **nunca encosta em carga lançada à mão** (mesmo cavalo, ±1 dia): sai de fininho
- só toca em carga com `criada_por_robo = TRUE`
- toda alteração vai para `embarques_cargas_log` como `Robô SSW (manifesto)`
- a rota ORS não é traçada aqui — o worker faz no ciclo seguinte

```bash
python embarques_auto.py --dry-run                 # mostra o que faria, sem gravar
python embarques_auto.py --dry-run --dia 2026-08-17
python embarques_auto.py                           # exige EMBARQUES_AUTO=true
```

> **Pré-requisito:** `embarques_veiculos_rastreio` precisa estar sincronizada
> (`POST /api/rastreamento/sync-veiculos`) — é a tabela que o worker consulta em
> `_placa_tracking`. Sem ela nenhuma carga rastreia, por mais que a 3S enxergue a placa.


---

## Módulo Rastreamento

Acompanhamento GPS dos veículos em rota, com mapa em tempo real e automação de eventos da viagem.

### Componentes
- **`tres_s_client.py`** — cliente da DataExportAPI da 3S. Token cacheado em `embarques_3s_token` (renovado 60s antes de expirar), **token bucket de 8 chamadas/min** (margem do limite oficial de 10/min), retry no 401. Quando `MODO_SIMULADO=true`, roteia tudo para `simulador_3s.py`.
- **`simulador_3s.py`** — stub com a mesma interface da 3S, lendo posições de `embarques_simulacao`. Permite testar o fluxo sem a API real.
- **`ors_client.py`** — OpenRouteService (`/v2/directions/driving-hgv`). Retorna polyline + distância + duração. `tracar_rota_multi(pontos)` traça a rota **multi-ponto** (origem → cidades de rota → destinos). Free tier: 2.000 chamadas/dia.
- **`geocoding.py`** — distância Haversine, normalização de nome de cidade (uppercase sem acento) e geocoder por centroide IBGE.
- **`rastreamento_worker.py`** — thread daemon dentro do processo Flask.

### Ciclo do worker (a cada `RASTREAMENTO_INTERVALO`, default 60s)
1. Busca a última posição de todos os veículos (3S real ou simulador) → UPSERT em `embarques_posicoes_atuais` + INSERT em `embarques_posicoes_historico`.
2. Para cada carga `Aberta`/`Em rota`/`No destino` com veículo mapeado, detecta automaticamente:
   - **Saída da origem** (`Aberta` → `Em rota`, marca `saida_auto` + `data_saida_real` com o horário GPS). Confirmada por **distância** (`RASTREAMENTO_RAIO_SAIDA_ORIGEM`, default 30 km) porque o 3S erra o nome da cidade (etiqueta a origem a 100+ km)
   - **Chegada na cidade do destino** (marca `no_local_desde`, segue `Em rota`)
   - **No destino** (`Em rota` → `No destino`): na cidade da descarga há ≥ 60 min **e** parado agora (velocidade ≤ 3 km/h). Limite fixo (`CHEGADA_MIN_PARADO`)
   - **Saída do destino** = **entrega automática** (`No destino`/`Em rota`/`Desengatada` → `Entregue`, marca `entregue_auto`, consolida o KPI final). Confirmada por distância (`RASTREAMENTO_RAIO_SAIDA_DESTINO`, default 60 km — raio maior p/ não bugar em grande centro). Em carga **`Desengatada`** o worker rastreia **só a carreta** (o cavalo foi liberado e pode estar em outra viagem), então é a saída da carreta carregada que finaliza a carga
   - **Cálculo da rota planejada** (ORS, multi-ponto): origem → **cidades de rota** → todos os destinos. Calculado 1× quando ainda não há rota; o "km faltando" é derivado da posição atual projetada sobre a rota completa (não recalcula truncando)
3. Eventos exigem `RASTREAMENTO_CICLOS_CONFIRMACAO` ciclos consecutivos fora do `RASTREAMENTO_RAIO_KM` para serem confirmados (evita falso positivo).
4. 1×/dia, **nesta ordem**: consolida o dia em `embarques_rastreio_dia` e **só então** purga o histórico além de `RASTREAMENTO_RETENCAO_DIAS`. Invertido, o dado não volta — a 3S serve ~35 dias de histórico (ver Consolidação diária).

O worker só inicia se `START_WORKER=true`. O modo (SIMULADO/REAL) é impresso no boot.

> ⚠️ **A posição é fato bruto: três etapas, TRÊS transações.** Até 10/09/2026 o ciclo fazia
> tudo numa transação só, e isso custou **30 horas de rastreamento parado em produção** sem
> nenhum alarme: a tabela `embarques_rastreio_dia` não existia lá, `_consolidar_dias`
> levantava, e o `rollback` descartava as 93 posições que já tinham sido gravadas — enquanto
> o `embarques_3s_log` (conexão separada) mostrava 66 chamadas/hora com HTTP 200 e zero
> erros. Hoje a posição commita **primeiro e sozinha**; `_processar_cargas` e a consolidação
> falham por conta própria. A **purga fica dentro do try da consolidação** (purgar depois de
> a consolidação falhar é destruir o que ninguém guardou), e `_ultima_retencao` é marcada
> mesmo na falha — era ela, marcada só no caminho feliz, que transformava uma falha diária
> em falha a cada ciclo. Regressão em `_teste_ciclo_transacao.py`. Ver §23.2 do
> `HANDOFF-EMBARQUES-AUTONOMO.md`.

### Consolidação diária (`embarques_rastreio_dia`)

O histórico de posições é purgado aos 30 dias, e **a 3S só serve ~35 dias** — medido em
04/09/2026: pedir 31/07 respondia, 28/07 devolvia `404`, e pedir 01/07→01/08 numa
chamada só voltava sem erro trazendo apenas 30 e 31/07. Passou disso, o dado não existe
em lugar nenhum. Julho/2026 se perdeu exatamente assim.

Consolidar por **carga** não cobre: `embarques_cargas_rastreio_kpi` tem `carga_id` como
PK, então o caminhão só deixa rastro enquanto está dentro de um documento. Dia no pátio,
deslocamento vazio e o intervalo entre duas cargas não têm `carga_id` — e são justamente
o que falta para medir produtividade. Medido em ago/2026: **17,4% do km de um cavalo não
tinha manifesto**, e o R$/km real fica 7,6% abaixo do R$/km calculado sobre o manifesto.

O grão certo é **placa + dia**, e o dia é de **Brasília**: `data_posicao` é gravada em
UTC, e o abastecimento do ValeCard (`dch_data`) **não tem hora nenhuma** — o dia é a
resolução em que as fontes se encontram. Custo: ~93 placas × 365 dias ≈ **34 mil
linhas/ano**, contra 421 mil posições por mês.

**A regra que define o que entra:** só o que morre. Manifesto, ValeCard, Sem Parar e
receita vivem no Power BI e não são purgados — junta-se na consulta, nunca se copia.

Duas decisões de robustez:

- **Odômetro pela primeira e última leitura cronológica, nunca `min`/`max`** — numa troca
  de rastreador o contador zera, e `min`/`max` viraria um delta de centenas de milhares
  de km. Delta negativo ou acima de `RASTREAMENTO_KM_DIA_MAX` vira `NULL` e sai no log;
  `odo_ini`/`odo_fim` ficam crus para auditoria.
- **`km_gps` (haversine) é conferência, não fonte.** O odômetro é cumulativo no aparelho
  e **atravessa buraco de sinal** — houve dia com 22 posições em que ele marcou 563 km
  contra 521 do haversine. Parado, o inverso: o GPS *infla* com jitter (580 contra 567 num
  dia com 15,5 h de pátio). Divergência grande entre os dois é diagnóstico do dia.

```bash
python -X utf8 consolidar_dias.py --resumo          # o que existe, sem gravar
python -X utf8 consolidar_dias.py                   # o que falta + os 2 últimos dias
python -X utf8 consolidar_dias.py --desde 2026-08-04 --refazer
```

---

## Módulo Contratos TAC

Emissão assistida por IA do **contrato TAC Agregado** (transportador autônomo de cargas). Substitui o preenchimento manual a partir dos documentos do agregado.

### Fluxo
1. **Extração** (`extrair_documentos`) — o operador sobe CNH, RG, CRLV/ATPV, consulta RNTRC/ANTT, comprovante de endereço e dados bancários. PDFs são rasterizados com PyMuPDF; o **GPT-4.1-mini (visão)** classifica cada documento e extrai **cada campo da sua fonte correta** (ex.: nome sempre da CNH, nunca da ANTT) em JSON. Reextrair com mais documentos **mescla** sobre o que já existe, preservando edições manuais (`merge_dados`).
2. **Pendências impeditivas** (`checar_pendencias`) — reconferidas em **Python** (não confia só na IA): faltando nome, CPF/CNPJ, RNTRC, placa, documento do veículo (RENAVAM/chassi) ou dados bancários (Banco+Agência+Conta ou PIX) bloqueia a emissão. A IA é instruída a **não criar exigências** novas.
3. **Geração** (`montar_contexto` + `gerar_docx`) — preenche o **template soberano** `contrato_tac_template.docx` via docxtpl. O texto jurídico e os dados da Contratante (Rizza) **nunca são tocados** — a IA só preenche os campos variáveis. Quando o agregado **não usa rastreador próprio**, inclui o bloco de **comodato** do rastreador (marca/modelo, nº de série, estado).

### Princípios
- O template `.docx` é a **única fonte** do texto do contrato; o preview HTML (mammoth) é derivado do mesmo `.docx` renderizado — não há duas versões do texto.
- O template é derivado do contrato-origem por `_build_template.py` (rodar 1×, fora do fluxo de produção).

---

## Módulo Faturamento por Tomador

Matriz tomador × mês com filtro de rota (`/faturamento`, `server.py:/api/faturamento/tomadores`). Fonte: `public conhecimentos_emitidos`, janela por `data_autorizacao`.

### Contagem de distintos não soma

Regra do negócio: **um manifesto = uma carga**, por tomador. Consequência técnica: `cargas` é `DISTINCTCOUNT` e **contagem de distintos não é somável**. O endpoint devolve a **lista de manifestos** por grupo e a tela faz união de conjuntos — nunca soma de contagens. Sem isso, dois erros aparecem:

| Erro | Efeito medido (2026) |
|---|---|
| Contar no grão da **filial** e somar na consolidação por raiz | Heinz: 328 cargas onde saíram 209 (**+57%**); 190 cargas a mais no ano (19 clientes de 283) |
| Somar as linhas para formar o **total** | Rodapé inflava **+17,5%** no ano (4.975 × 4.234), variando de +6,9% em jan a +26,7% em mai |

O caso que define a regra: o Heinz fatura a mesma viagem em duas inscrições da **mesma planta de Nerópolis** (119 dos 209 manifestos). Provado pela tarifa — `NEROPOLIS/GO → UBERLANDIA/MG` custa R$ 3.900 líquido / R$ 4.431,82 bruto **por carreta**, e a soma dos dois CTe bate esse valor ao centavo em todas as viagens. A `Auditoria Receita` já tratava assim (`receita_rateada` = R$ 3.900/viagem); só a contagem duplicava.

### Rota

Origem = `cidade_origem_prestacao` (de onde partiu **esta prestação**, não onde a mercadoria nasceu — em redespacho os dois divergem). Destino = `cidade_destinatario`. Cada lado do filtro aceita **estado (`MG`) ou cidade (`UBERLANDIA/MG`)**; o autocomplete mistura os dois **ordenado por volume**, porque são 851 pares no ano e 45% deles têm uma carga só — lista alfabética seria inútil.

Fatiar por rota também multiplica a mesma carga (uma viagem de distribuição atende vários destinos): a soma das linhas excede o total em até 46% no recorte Uberlândia. Cada linha está certa; o rodapé mostra o distinto e marca `*` quando os dois divergem.

### CTe que não é viagem

24% dos CTe não têm manifesto e **não contam como carga** — são `COMPLEMENTAR FRETE`, `SUBC REC FORM LISO` (subcontratação, cobrança de descarga) e `SUBSTITUTO`. Entram no R$, não no nº de cargas. A concentração é muito desigual (Heinz 54%, Sanchez Cano 48%, Martins 5%), então a tela avisa quando algum tomador aparece com faturamento e zero cargas — senão parece bug.

### Performance

Grão CTe do ano (11.785 linhas) é agregado **no servidor** para ~3.100 grupos → payload de **0,3 MB**, DAX ~2,3s uma vez. O filtro depois roda no cliente em **1–2 ms**. Trazer o grão CTe cru ao navegador custaria 6,3 MB.

---

## Módulo Análise por Veículo

Análise de receita e **custo real** por veículo/pessoa (`/veiculos`, `server.py:/api/veiculos/analise` e `/api/veiculos/detalhe`). Fonte: `Auditoria Receita` (grão CTRB) cruzada com pedágio/combustível/despesas/cadastro.

### Conceitos-chave
- **Receita sempre rateada** (`receita_rateada`) — nunca soma campo bruto (uma carga pode ter várias linhas de manifesto → duplicaria).
- **FROTA não tem pagamento de frete** (veículo próprio) → forçado a 0 (o `frete_motorista_total` na frota é comissão do motorista próprio, não frete a terceiro).
- **Placa Mercosul**: a conversão antigo→Mercosul muda só o 5º caractere (dígito→letra `0=A…9=J`). `_placa_mercosul` normaliza (funde as duas grafias do mesmo veículo); `_placa_grafias` faz o caminho inverso (gera as 2 grafias) para casar o dado bruto no drill-down. `veiculos_045` também é deduplicado por placa normalizada.
- **Colisão de placa (dedup do cadastro)**: a conversão antigo→Mercosul pode gerar uma string idêntica à placa Mercosul **real de outro veículo** (ex.: `HOA0466` antiga → `HOA0E66`, que é a Mercosul real de outra carreta). Por isso `_cadastro_veiculos`, na colisão, **prefere a entrada cuja placa crua já é Mercosul** (identidade atual) em vez da antiga convertida — senão um veículo Rizza era resolvido para o dono terceiro errado e saía do rateio de Manut./Pneu.
- **Regra interna `PLACAS_VENDIDAS`**: cavalo vendido mas ainda no nome da Rizza no cadastro não conta como frota (ex.: `AZM6E29`).

### Custos rateados (visão Cavalo + só Frota), por competência do mês
- **Pedágio** (Sem Parar): soma líquida por placa (todos os `tipo_uso`).
- **Combustível / ARLA** (ValeCard): diesel e ARLA separados (valor + litros). **Consumo km/L** = km do **hodômetro** (soma de deltas consecutivos válidos, ignora retrocesso e saltos > 3000 km) ÷ litros de diesel — mesma base na tela e no drawer (`_km_hodometro`).
- **Pessoal** (folha de motoristas, `custo_pessoal`) — **descarregado no veículo em que o motorista rodou**, não rateado por faturamento. Nível 1: a folha de cada motorista vai para os cavalos/trucks **Rizza** em que ele rodou no período, proporcional ao **KM rodado** em cada um (mesma régua de KM da tela: `rotas_km` com fallback no `distancia_km`, **não** o hodômetro do ValeCard, que é digitado). Se rodou mas o KM não foi medido, divide igual entre os veículos que dirigiu. Nível 2: quem não rodou em veículo Rizza no período (afastado, férias, só agregado) forma o **resíduo**, dividido **por igual dentro da classe** (truck entre trucks, demais entre cavalos — a função genérica "Motorista" conta como cavalo). Carreta não recebe folha; truck fica na visão Cavalo. O casamento folha×viagem é **por similaridade de nome** (`_nome_parecido`, corte 0,82), porque o dataset entrega os acentos corrompidos (`João` → `Jo�o`) — casa `Halisson`/`HALLISON` e `de Souza`/`de S.`. **Provisão**: competência sem folha lançada usa a **anterior mais recente que tenha**, como estimativa — quando o RH lançar o mês, o real entra no lugar sozinho (a fonte deixa de ser a anterior). O valor provisionado é sempre **alocado pelas viagens do mês pedido**, não pelas do mês de origem. Diagnóstico em `totais_custo`: `pessoal_residuo`, `pessoal_sem_viagem`, `pessoal_nome_aproximado`, `pessoal_provisionado` e `pessoal_meses_provisao` (ex.: `["2026-08<-2026-07"]`).
- **Manut. Cavalo** (eventos `5150`/`5154`) — proporcional ao faturamento.
- **Seguro** (`5402` exceto fornecedor BVIX) e **Rastreador** (fornecedor AUTOTRAC) — colunas separadas, **divididos igualmente** entre os cavalos frota.
- **Pneu** (eventos `5411`/`5412` — pneus + recapagem): o histórico **não tem placa**, então o pool do mês é **dividido cavalo×carreta pelo nº de pneus dos veículos Rizza ativos** (`_pneus_por_veiculo`: carreta=12, truck=6, cavalo `6X4`=10/`6X2`=8/`4X2`=6, fallback 8) e, dentro de cada grupo, rateado por faturamento.
- **KM Rota** (manifesto, `COALESCE('public rotas_km'[km], distancia_km)`) e **KM Abast.** (soma de deltas válidos do hodômetro ValeCard) são colunas **distintas**; o **Km/L = KM Abast. ÷ litros diesel** (não usa o KM Rota).
- **Financiamento** (consórcio / CDC / FINAME / ativo imobilizado — eventos `5512`/`5513`/`5515`/`5517`): parcela do contrato de aquisição do veículo. No **cavalo** é **atribuição direta por placa** (sem rateio) via o de-para `FIN_CAVALO` no `server.py` — o 477 nunca traz a placa, só modelo e contrato, então o de-para é mantido no código e validado com a diretoria; contrato de vários cavalos divide igual entre eles e cavalo sem contrato fica zerado. Na **carreta** é **rateio proporcional ao faturamento** sobre a base fixa de todas as carretas Rizza (frota+agregado), igual à Manut. Carreta. Vale a mesma regra realizado × provisão do 477 (competência com parcela `LIQU` usa o real; só com `PEND` entra como provisão). **Consórcio ainda não contemplado fica fora** (não há veículo rodando).
  > **Fica FORA do Resultado** (Frota e Carreta): é investimento/aquisição, não custo operacional — entra como coluna própria para não quebrar a comparação com os meses já analisados.
- **Resultado Frota** = receita − pedágio − combustível − ARLA − pessoal − manut. cavalo − seguro − rastreador − pneu.

### Visão Carreta
- **Manut. Carreta** (eventos `5153`/`5155`) e **Pneu** (parte-carreta do pool `5411`/`5412`) rateados proporcional ao faturamento entre as **carretas Rizza** (frota+agregado); base de rateio **fixa** (todas as carretas Rizza do mês, independe do filtro de tipo). Result. Carreta = receita − pagamento (frete ao cavalo, no agregado) − manut. carreta − pneu. Trucks (sem reboque) ficam fora do recorte Carreta.

### Proprietário, gráficos e filtros
- **Coluna Proprietário** (exceto frota): no recorte **Cavalo** mostra o dono do **próprio cavalo** (1:1 do `veiculos_045`); no recorte **Carreta** não há coluna — os **donos dos cavalos que puxaram** a carreta aparecem no drawer (podem ser vários).
- **Donut adaptativo**: com **mais de um tipo** marcado é "Participação por Tipo"; com **um único tipo** vira **"Composição do Faturamento"** (custos diluídos na receita + **Resultado**, a sobra). Paleta dessaturada (verde reservado só ao Resultado), legenda HTML em ordem decrescente, centro com o faturamento.
- **Filtros Mês e Tipo** são dropdowns (caixinhas + Aplicar) — marcam sem recarregar até aplicar; abrir um fecha o outro.

### Painel lateral (drawer)
Clicar em qualquer linha abre o detalhe (`/api/veiculos/detalhe`), **reconciliado com a linha** (respeita o filtro de tipo): cargas, abastecimentos detalhados, pedágios, **manutenção real** (itens identificados pela placa no `historico_despesa` — match de texto, cobertura parcial), relacionamentos (carretas/cavalos/motoristas/rotas, **com o proprietário de cada veículo**) e, em motorista/proprietário, **quebra por veículo**. Proprietário reconcilia pela base **cavalo** (não soma cavalo+carreta para evitar dupla contagem).

> **Pendência conhecida**: o Km/L usa o hodômetro do ValeCard (digitado pelo motorista, sujo) → pode distorcer; por isso **KM Rota e KM Abast. aparecem lado a lado** para tornar o número auditável. O km confiável virá do **GPS do rastreamento** numa próxima fase.

---

## Módulo Contábil

Base do SSW para o fechamento contábil (`/contabil`). Fonte: dataset `tabelas.contabil`,
alimentado pelos robôs do projeto `Rizza/` (456 · 441 · 571 · 479) numa carga diária.

### A tela é a que a contadora desenhou

O ponto de entrada é **uma linha por banco**, com saldo inicial, créditos, débitos e saldo
final — foi assim que ela pediu, no papel. O que o desenho não tinha e vale mais que o resto:

- **Conta contábil por banco** (tabela `contabil_conta_fixa`, editável em
  `/contabil/contas-fixas`), porque é o que vai no arquivo de importação. Onze das treze
  mapeadas; as duas ausentes aparecem como `⚠ sem conta`.
- **✓ confere** — o saldo vem do rodapé impresso pelo próprio extrato do SSW, e a contagem
  carregada é comparada com ele. É o que faz contador confiar sem recalcular.

⚠ **`extrato_bancario_456_totais` acumula um jogo inteiro de contas por execução do robô** — o
`DELETE` da carga é por conta+período e o `periodo_fim` anda todo dia, então nada é substituído.
O acúmulo é intencional (é o histórico entre dias, `HANDOFF-CONTABIL.md` §7): **quem escolhe a
execução é quem consome**. Quem lê essa tabela sem cortar repete cada banco uma vez por dia de
carga e multiplica o saldo consolidado — foi o que aconteceu em 20/08/2026, com 39 linhas e
R$ −8.685.893,62 no lugar das 13 contas e R$ −2.543.039,89. O corte é o mais recente **por
conta**, nunca um `MAX(periodo_fim)` global: numa carga parcial o global derruba a conta que
faltou e o saldo dela some calado, e banco faltando é pior que banco repetido. Vale para
`_quadro_bancos()`, para o gabarito e para a medida `saldo_final_rodape`.

**Duas** contas fogem de `1.1.1.02` de propósito, e o motivo está comentado no código: BB
GARANTIDA é conta garantida (passivo, `2.1.1.08.001`) e D.D SOLAR é duplicata descontada
(redutora de clientes, `1.1.2.01.117`). Outras duas — TRIBANCO e CAIXA PAMBANK — ficam **em
branco de propósito** porque ainda não existem no plano; é a pendência que a tela destaca.

### Três guardas que não são opcionais

Somar coluna crua do 456 mente. As telas aplicam a regra **e mostram o que esconderam**:

| Guarda | Sem ela |
|---|---|
| `transferencia_interna` | crédito dá R$ 132,7 mi em vez de R$ 79,4 mi — R$ 53,3 mi de dinheiro próprio virando receita |
| `realizado` (exclui `sit='P'`) | 117 lançamentos de previsão entram como extrato |
| `valor_frete` do grão CTRC | é o valor **cheio**; um CTRC repartido aparece inteiro em cada fatura (11 CTRCs em 40 faturas). A diferença entre os grãos é **R$ 172.850,39**. Para valor, `vlr_ctrcs` do grão fatura |

> Os números acima são **base viva** — remedidos em 20/08/2026. A ordem de grandeza é que
> importa; se algum inverter de sinal ou mudar de casa, a guarda parou de funcionar.

### Classificação e de-para

A coluna `regra` do extrato aplica a especificação da contadora (`PARA GABRIEL.xlsx`) e marca
o que sobra como `SEM REGRA` — 741 movimentos em 20/08/2026. `VIA RET BCO` é testado **sem
amarrar à origem**: a regra dela ficou presa a `BCO` e o mesmo histórico aparece em `MAN`.

### Configuração — decisão da contadora vai para tabela, encanamento fica no código

O princípio que rege o módulo: **ela nunca deve precisar pedir deploy para mudar uma regra.**

Três tabelas locais guardam o que é decisão dela, editável por `/contabil/eventos` e
`/contabil/contas-fixas`:

| Tabela | Conteúdo |
|---|---|
| `contabil_plano_contas` | o plano da PERSETO (312 contas, 218 analíticas). Alimenta os campos de escolha — **nenhuma conta entra por digitação** |
| `contabil_evento_conta` | conta contábil + as flags, por evento. **APPEND-ONLY** |
| `contabil_conta_fixa` | as 13 contas do 456 e a contrapartida de fornecedores |

**Nunca vira configuração:** a mecânica da partida dobrada, qual data recorta cada relatório,
e as guardas acima — que são correção de erro medido, não preferência.

**Por que append-only:** `eventos_479` é substituição total a cada carga, e evento desativado
no SSW some de lá. A vinculação não pode sumir junto, senão um fechamento anterior perde a
conta que usou. Como bônus, o histórico é a própria tabela e ela responde "qual era a conta em
setembro" — o que protege mês fechado de mudar quando alguém corrige um cadastro depois.

**A conta é editada no app, não no SSW.** O plano era ela preencher na tela 503 e o robô do 479
puxar, mas o campo que ela alcança ("Conta Contábil PIS/COFINS") **não sai no relatório**.

**Só 60 dos 97 eventos precisam de conta** — os demais vão na conta fixa de fornecedores,
conforme as flags dela. A tela marca isso e não oferece campo para quem não precisa.

Contas no formato `5.02.02.01.0025` são do plano **do SSW**, que é outro plano — lá o grupo 5
é despesa, no plano contábil o grupo 5 é apuração. Ver `HANDOFF-CONTABIL.md` §4.

### ⚠ Escopo: 01/2026 até a competência corrente

`REF` é **texto**, e `REF >= "2026/01"` varre competência futura: a base tem 117 competências
além de 2026/12 (parcela a vencer de financiamento e consórcio), somando **R$ 18,6 mi** em
1.556 lançamentos. Quem segura o número é o **teto**, não o piso: sem ele a despesa do escopo
salta de R$ 46,5 mi para R$ 72,0 mi — **R$ 25,5 mi a mais**, somando o que passou de 2026/12
com as competências de 2026/09 a 2026/12, que também são futuras.

⚠ A trava `LEN = 7` **não** derruba o `REF` malformado da base. O valor é `'20ES/6 '`, com
espaço à direita — tem exatamente 7 caracteres e **passa**. Quem o barra é o teto, porque na
comparação de texto `20E…` fica acima de `2026/08`. A trava de `LEN` continua valendo contra
outras deformações, mas não é ela que resolve este caso. Ver `filtro_ref_477()`.

### ⚠ O executeQueries corta a resposta e devolve HTTP 200

São dois tetos: 100.000 linhas e **15 MiB de payload**. O segundo morde muito antes e depende
da largura da linha — a mesma consulta devolve 10.127 linhas com 29 colunas e as 22.835
completas com 3. Resultado cortado parece resposta.

`contabil_dax()` checa `results[0]['error']` (só existe quando houve corte:
`DaxByteCountNotSupported`), e o endpoint do extrato ainda compara o total recebido com um
`COUNTROWS` do mesmo filtro. Por isso `_COLS_EXTRATO` seleciona colunas explicitamente em vez
de puxar a tabela.

> As medidas DAX vêm de `Rizza/contabil_pbi.py` (dict `MEDIDAS`), validadas por
> `Rizza/contabil_testes.py` — 17 testes. Ao mexer aqui, rodar lá e comparar.

---

## Módulo CIOT (conferência CTRB × manifesto × CIOT)

Pedido do diretor em 16/09/2026: *"check se todos os CTRBs/MDF estão com CIOT vinculados,
a partir de agora, de hora em hora"*. Régua em `ciot_conferencia.py` (função pura
`conferir`), fontes `public ctrbs_oss` (073) e `public manifestos` (916) pelo BI.

### Três cruzamentos, cinco pendências
| Pendência | Regra |
|---|---|
| CTRB sem CIOT | campo vazio |
| CIOT com erro na geração | o SSW grava a mensagem da ANTT/Pamcard no lugar do código. CIOT válido = 12 dígitos, com ou sem código de verificação (`/4846`, `.7599`) |
| CTRB sem manifesto | nenhum manifesto aponta para ele (916, inclusive pela fórmula) e ele não cita manifesto (073) |
| Manifesto sem CTRB | nem o 916 nem o 073 ligam o manifesto a um CTRB existente — logo, sem CIOT. Também entra aqui o manifesto que **a fórmula** casou com um CTRB que já tem outro manifesto declarado: na prática é órfão (sugere o CTRB provável pela regra de órfãos: mesma placa, ±5 dias) |
| CTRB e manifesto com dados diferentes | o vínculo existe, mas diverge: 073 e 916 apontam manifestos diferentes, ou placa de cavalo/carreta ou CPF diferentes. **Não é órfão** |

Frota entra (50 de 51 CTRBs de frota já têm CIOT). **Diária não entra**: `tabela_antt = 0`,
sem manifesto e origem = destino.

### Armadilhas medidas em 01–16/09/2026
- **Reemissão = mesma placa, mesma rota, até 24 h depois.** O antigo sai da conta e passa os
  manifestos para o novo. Aparece de dois jeitos:
  - *com aviso* — "BAIXADO PELA EMISSAO DE NOVO CTRB:X". Mas a mesma frase sai quando o CTRB é
    só **encerrado** pela viagem seguinte do veículo (24 de 29 casos em set/26): aí o antigo é
    viagem e responde por si, por isso o X tem de ser a mesma viagem;
  - *sem aviso* — o CTRB ficou sem CIOT (ou com erro) e um irmão da mesma viagem saiu depois
    **com** CIOT. Ex.: UDI027256-6 (erro Pamcard) → UDI027257-4 em 11 min; NOD004828-3 →
    NOD004832-1 em 11 h, com o manifesto reemitido junto.
- **O vínculo tem de ser lido nos dois sentidos.** O 916 extraído antes de o CTRB existir fica
  `000000`; o 073 guarda o manifesto no CTRB e pode listar dois.
- **`CHAVE_CTRB` é a fórmula de órfãos do modelo** (preenche mesmo com `000000`; nunca diverge
  quando o SSW informa). É o vínculo da Auditoria Receita, então as duas telas concordam.
- Padrão real encontrado: CTRBs de carreteiro RIO → Cordeirópolis sem MDF e sem CIOT, encerrados
  pelo CTRB da volta emitido na NOD (que tem CIOT).

### Frequência
A conferência e o envio **seguem o refresh do BI**: hoje 8×/dia (workspace Pro — 02:00, 05:30,
08:00, 10:00, 12:00, 14:00, 16:00 e 20:00 BRT). O laço consulta o histórico de refresh a cada
5 min, **nunca roda durante um refresh** e só lê passados `CIOT_ESPERA_POS_REFRESH_MIN` do fim
dele. O resumo das 08:00 espera o refresh das 08:00 terminar (se ele falhar, sai até as 10:00
com o que houver). Hora em hora exige o refresh horário (PPU, ~US$ 10/usuário/mês a mais) —
aí a conferência acompanha sozinha, sem mudar código.

### Estado e aviso
`ciot_pendencias` guarda uma linha por pendência (`primeiro_visto`, `ultimo_visto`,
`resolvido_em`, `avisado_em`, em UTC). Sumiu da rodada = resolvida; voltou = nova de novo.
Documento anterior ao `CIOT_DESDE` sai da tabela (não conta como resolvido).

O WhatsApp tem **três mensagens**, sempre **uma linha por documento** (o mesmo CTRB costuma estar
sem CIOT e sem manifesto):

- **Novas** — a cada rodada, o que apareceu desde o último aviso, vencida a carência. Cada
  pendência vai uma vez só nesta mensagem.
- **Resumo do dia** — na primeira rodada depois de `CIOT_RESUMO_HORA_BRT`, o que **continua
  em aberto**: os contadores de tudo, a lista só dos emitidos nos últimos `CIOT_RESUMO_DIAS`
  (mais antigo primeiro, 🆕 no que nunca foi avisado) e a contagem dos mais velhos, que ficam na
  tela. Nesse horário ele substitui a de novas. Sai mesmo com nada em aberto — silêncio seria ambíguo.
- **Nada novo** (`CIOT_AVISO_SEM_NOVIDADE`, nasce desligada) — o **batimento**: rodada sem pendência
  nova manda uma linha em **texto** (`✅ CIOT · 14:13 — nada novo desde 12:18`) com o que segue em
  aberto e quantos estão **aguardando confirmação** (vistos, ainda dentro da carência — é o que sai na
  rodada seguinte se o CTRB não aparecer). Nunca vira imagem: mensagem de "sem novidade" tem de ser
  lida na notificação. Não marca nada como avisado, e grava em `ciot_envios` como `tipo='nada'`.

> **A carência não atrasa o que mais importa.** Ela conta pela **emissão do CTRB** em tudo que é
> pendência de CTRB (sem CIOT, CIOT com erro, sem manifesto, dados diferentes) — e o CTRB chega ao BI
> 2–3 h depois de emitido, então quando o robô o vê ele já venceu a carência e sai na **mesma rodada**.
> Só o `manifesto_sem_ctrb` do próprio dia espera (`primeiro_visto + CIOT_CARENCIA_H`), que é
> justamente o caso que costuma se resolver sozinho: os carregadores do 073 e do 916 rodam em
> horários diferentes (18/09/2026: CTRBs 09:30, manifestos 09:33), então um manifesto pode chegar
> antes do CTRB dele.

**Formato:** imagem + legenda curta (`CIOT_FORMATO=imagem`, padrão), no mesmo molde do PGR
(`ciot_imagem.py`, `fitz.Story`): vermelho = falta CIOT, laranja = falta amarrar, verde = "tem
CIOT". A legenda diz **qual das duas é** — resumo = *tudo que segue em aberto, emitido de 01/09 até
hoje*; novas = *só o que apareceu desde o último aviso (hora)* — e conta **por documento**, em
categorias que não se sobrepõem e somam o total: sem CIOT e sem manifesto · sem CIOT (tem
manifesto) · MDF sem CTRB · sem manifesto (tem CIOT) · dados diferentes (tem CIOT). Contar por
pendência confundia ("22 sem CIOT · 20 sem manifesto": os 17 estavam dentro dos 22). Os cartões da aba usam as mesmas categorias (clicáveis), então mensagem e tela mostram os mesmos números. Se a imagem não renderizar, vai o texto. Na imagem o número não se copia — para operar, a aba.

**Link sem login (como o do PGR, mas sem expirar):** a mensagem leva `/ciot?t=<token>`. É um
token só (`ciot_tokens`), o mesmo em todas as mensagens até um admin clicar em **Gerar novo link**
na tela — aí o antigo passa a abrir "este link não vale mais". Com o token só se abre a aba CIOT
em leitura: a página não carrega o menu, esconde "Conferir agora", não cria sessão, e toda outra
rota continua exigindo login. Regressão em `_teste_ciot_acesso.py` (**troca o link** — só roda
em banco local).

`ciot_envios` registra cada envio (é por ela que o resumo sabe que já saiu no dia). Mesma
política da UazAPI do PGR: sem retry, nunca derruba a rodada.

```bash
python -X utf8 ciot_conferencia.py --dry-run --desde 2026-09-01   # só mostra
python -X utf8 ciot_conferencia.py                                # grava, não envia
python -X utf8 ciot_conferencia.py --previa                       # grava e mostra a mensagem da vez
python -X utf8 ciot_conferencia.py --enviar --resumo              # teste: força o resumo e envia
python -X utf8 _teste_ciot.py                                     # regressão da régua
```

## Módulo PGR (excesso de velocidade)

Relatório diário das placas que passaram de 95 km/h, por WhatsApp. Motor em
`pgr.py`, imagem em `pgr_imagem.py`, envio em `pgr_envio.py`, página em `pgr.html`.

### O job diário — a ordem não pode inverter
```
1. backfill do dia anterior   (rastreamento_worker, 04:00 UTC)  → completa as posições
2. apura pgr_eventos                                            → lê posições já completas
3. envia a imagem + link                                        → lê a tabela, não recalcula
```
Apurar antes do backfill entrega ~81% de cobertura em vez de 100% — e ninguém
percebe, o número só vem menor. O envio lendo da tabela garante que a mensagem e
a página mostram o mesmo número.

### Por que existe o backfill
`/ListaUltimaPosicaoVeiculos` devolve só o **último** ponto: tudo que o aparelho
transmitiu entre dois ciclos do worker se perdia. `POST /HistoricoPosicao` traz
o dia inteiro. Medido contra o alerta nativo da 3S (1 Hz no aparelho), em 11/08:
polling ao vivo pegou 26 de 32 episódios (81%); o backfill pegou 37 de 37. Não é
detecção absoluta — é o fechamento da lacuna de **amostragem** (a cadência
continua sendo a do aparelho, 2–5 min).

### Regras de detecção
- **Limiar 95, teto 130** (anti-ruído: houve equipamento marcando 214 km/h constante por 19 min).
- **Episódio** = registros a menos de 10 min entre si.
- **"Nº de registros", nunca "tempo acima"** — cada leitura é um instante, não um intervalo. Somar seria inventar número.
- **Pico exibido = `max(leitura, sustentada)`**. As duas são *piso* do valor real (a leitura é instantânea; a média usa haversine, que subestima 5–15%, e o máximo é ≥ a média). Erra sempre para baixo. `vel_max` e `vel_sustentada` ficam crus na tabela para auditoria.
- **Sustentado** = 2+ registros no mesmo episódio. A média do trecho é coluna informativa, não critério.

### Situação de carga — provada por posição, nunca por data
Casar por data é frágil nos dois sentidos: estrito perde viagem longa, frouxo
casa 100% e mente. O risco real é dizer *"a 105 carregado de Nestlé"* quando o
caminhão já tinha entregado. Três testes, todos geográficos:

1. **Corredor** — o ponto do excesso está entre origem e destino? (`dO + dD ≤ 1,35 × dOD`)
2. **Passou pela origem** antes do excesso (prova que pegou a carga)
3. **Ainda não chegou ao destino** (se chegou, já entregou)

Exige posições dos **dias anteriores** (`PGR_DIAS_LOOKBACK`): a carga pode ter
sido pega uma semana antes — houve caso de carregar dia 06, ficar 3 dias parado
e só exceder no dia 10.

**Vazio** só com evidência positiva (passou pelo destino antes). Sem prova fica
**não confirmado** — dizer "vazio" sem prova é o mesmo erro de dizer "carregado"
sem prova, na direção oposta. **Parcial** = pelo menos um episódio provado com
carga e outros não.

> ⚠️ **Calibração pendente:** os limiares vieram de um estudo sobre 10 dias de
> posição de produção e ainda não foram reconferidos contra a base backfillada.

### Cobertura — só lacuna EM MOVIMENTO conta
Parado, o aparelho reporta de 1 em 1 hora (ou 12 em 12), então lacuna longa é
comportamento normal, não cegueira: as maiores lacunas de 11/08 (243, 239, 142
min) tinham deslocamento de **0,0 km** — eram pátio. O discriminador é a
velocidade implícita (deslocamento ÷ duração). Placa com lacuna real em
movimento aparece rotulada: sem isso, placa ausente lê como "comportou-se bem"
quando o certo é "não sabemos".

### Por que o relatório persiste o resultado
A retenção de 30 dias apagaria as posições que geraram o relatório, e o PGR do
mês passado deixaria de ser reproduzível. `pgr_eventos` guarda o resultado (grão
de **episódio**, para permitir ranking por motorista e drill-down) e
`pgr_cobertura` guarda o rodapé. Dezenas de linhas/dia contra ~63 mil posições.
`UNIQUE (placa, ini)` faz upsert, então o dia é reprocessável.

### Caches do Power BI (o worker não fala DAX)
O módulo de rastreamento é Postgres puro. Dar DAX ao worker acoplaria dois
mundos limpos e criaria dependência de credencial num job de madrugada. O
`server.py` alimenta `pgr_cadastro_veiculos` (tipo do veículo) e
`pgr_manifestos` (já geocodificado) a cada 12h; o job só lê. Cache velho vira
aviso no log — rótulo faltando é falha macia, job quebrado não.

`tipo_operacao` (frota/agregado) sai do **manifesto**, não do cadastro: é
propriedade da viagem, porque a regra é sobre o par cavalo+carreta e o evento de
PGR tem uma placa só.

### A tela: dia e período

O modo padrão é **um dia** — é o relatório que o link do WhatsApp abre. Logado, a
mesma tela ganha seleção de mês (múltipla, no mesmo componente da aba Veículos) e
filtros de **cavalo, carreta e motorista**.

No período, a linha continua sendo **placa-no-dia**, com um cabeçalho separando os
dias. Agregar o período inteiro numa linha só perderia o *quando* e transformaria
a lista de cidades num parágrafo.

Detalhes que a implementação obrigou:

- **Mês sem dia apurado fica desabilitado.** A base começa quando a apuração
  começa; oferecer um mês vazio faria a tela parecer quebrada. Mês novo acende
  sozinho conforme o job roda.
- **A tela diz quantos dias existem** ("3 de 31 apurados") — mesma razão do
  rodapé de cobertura: ausência não pode se confundir com "nada aconteceu".
- **`veículos` nos KPIs é contagem DISTINTA no período.** Somar a contagem de
  cada dia daria mais caminhões do que a frota tem.
- **As opções de filtro saem dos dados apurados**, não do cadastro: oferecer os
  6.645 veículos do `veiculos_045` daria uma lista impossível, cheia de escolha
  que retorna vazio.
- **Tudo normalizado em Mercosul.** Sem isso o mesmo veículo aparecia duas vezes
  (`HIF2439` e `HIF2E39`: grafia crua do GPS × normalizada do manifesto) e
  escolher uma perdia os eventos da outra. O filtro casa contra as duas grafias,
  e aceita a placa digitada em minúscula ou na grafia antiga.
- **Filtrar cavalo casa a placa do evento OU a do par.** O rastreador costuma
  estar na carreta, então procurar só pela placa do evento devolveria quase nada.
- **Motorista casa por trecho** (`ILIKE`): a lista oferece o nome curto
  ("Pedro Henrique de S.") e o banco guarda o do manifesto inteiro; assim quem
  digita só o primeiro nome ou o sobrenome também encontra.

**Período e filtros exigem sessão.** O token do link é preso a um dia de
propósito: se abrisse o modo período, um link vazado exporia meses de operação
em vez de um dia.

### Entrega
Imagem com as 6 placas mais relevantes + link. A imagem ordena por **sustentado
→ recorrência → pico** (diferente da página, que ordena por gravidade): 81% dos
episódios são pico isolado, então ordenar por pico mostraria o ruído e esconderia
a conduta. O corte é explícito ("+N no relatório completo"), senão quem recebe lê
6 e entende que foram 6. Dia com ≤3 placas vai inteiro; **dia zerado também é
enviado**, com o contador de cobertura — silêncio seria ambíguo.

Renderização via `fitz.Story` (PyMuPDF, já dependência do projeto) — sem
navegador headless. JetBrains Mono embutida em `fonts/` (com a OFL).

### Reprocessamento manual
```bash
python backfill_historico.py 2026-08-11              # um dia (Brasília)
python backfill_historico.py 2026-08-01 2026-08-11   # intervalo
```
Idempotente (`UNIQUE(placa, data_posicao)`). ~16 min por dia de 93 veículos.

---

## Mapa DRE (estrutura contábil)

Definido em `server.py:MAPA_DRE` — mapeia cada `descr_evento` para um Grupo + Subgrupo. Total: ~80 mapeamentos.

| Grupo | Exemplos de Subgrupos |
|---|---|
| Operacional | Fretes, Combustível, Manutenção, Pneus, Deslocamento, Seguros, Mão de Obra, Outros |
| Administrativo | Mão de Obra, Encargos, Estrutura, Sistemas, Jurídico, Saúde, Segurança, Taxas, Comercial, Benefícios, Outros |
| Financeiro | Dívida, Custos Financeiros |
| Impostos | IR, CSLL |
| Deduções | ICMS, PIS, COFINS, ISS |
| Investimento | Investimentos (CDC, FINAME, Consórcio, Imobilizado) |
| Retirada | Retiradas de sócios |

### Fórmulas da DRE
```
Receita Líquida   = Receita Bruta - Deduções
EBITDA            = Receita Líquida - Custo Operacional - Despesas Administrativas
LAIR              = EBITDA - Despesas Financeiras
Lucro Líquido     = LAIR - Impostos
Pós Investimento  = Lucro Líquido - Investimentos
Resultado Final   = Pós Investimento - Retiradas
```

---

## Custos Operacionais Estimados

### IA (escala atual)
- **Reunião**: ~$0,03 por reunião (AssemblyAI ~$0,17/hora + GPT-4.1-mini)
- **Chat IA**: ~$0,001 por pergunta (GPT-4.1-mini com pré-cálculo no backend)
- Estimativa mensal típica: ~**R$ 30-50/mês**

### Rastreamento
- **3S**: limite de 10 chamadas/min (operamos com 8/min de margem)
- **OpenRouteService**: free tier de 2.000 chamadas/dia (recálculo throttled por carga)

### Infraestrutura
- Hostinger VPS: já contratado para outros serviços (custo compartilhado)
- Cloudflare Free
- GitHub Free

---

## Segurança

- Autenticação por sessão Flask (cookie HTTP-only)
- Senhas com hash via `werkzeug.security.generate_password_hash`
- Decoradores `@login_required`, `@admin_required` e `@page_required('<aba>')` (permissão por aba; admin bypassa) em todas as rotas sensíveis. As abas concedidas ficam em `auditoria_users.paginas_permitidas` e são carregadas na sessão no login
- Queries SQL parametrizadas (prevenção de SQL injection)
- DAX queries com escape de strings
- SSL obrigatório via Traefik + Let's Encrypt
- Acesso ao Postgres restrito à rede overlay `carvalhonet` (não exposto publicamente)
- Credenciais da 3S e chaves de API mantidas em variáveis de ambiente (nunca commitadas)

---

## Convenções de Código

- **Backend**: Python com type hints opcionais, docstrings em PT-BR para funções complexas
- **Frontend**: JavaScript ES6+, sem build step, comentários em PT-BR
- **CSS**: variáveis CSS centralizadas em `:root` para tema dark consistente
- **Commits**: prefixos convencionais (`feat:`, `fix:`, `refactor:`) em PT-BR

---

## Roadmap (ideias para evolução)

- [x] Módulo de Embarques (lançamento + relatório + auditoria de edição)
- [x] Rastreamento GPS com mapa, detecção automática de saída/entrega e rota planejada
- [x] Tracking pela carreta (carreta1 → cavalo → carreta2) + KPIs ao vivo no mapa
- [x] Agendamento por destino + filtro/badge de atraso + ETA realista
- [x] Reconstrução do trajeto em lançamento tardio (`inicio_viagem` detectado por cidade e persistido)
- [x] Comparativo de tarifas (até 4 blocos + resumo)
- [x] Faturamento por Tomador (matriz tomador × mês, consolidado por raiz de CNPJ)
- [x] Auditoria Receita abrindo no mês corrente
- [x] Emissão de contrato TAC Agregado por IA (extração de documentos + template soberano + comodato)
- [x] Viagem vazia + cidades de rota/passagem (rota planejada multi-ponto)
- [x] Desengate de carreta carregada (status `Desengatada`: libera cavalo+motorista, carreta segue no destino, finaliza automático na saída da carreta)
- [x] **Continuação / desengate de pátio** (`EMBARQUES_CONTINUACAO`, §24): a mercadoria que atravessa duas cargas liga a primeira à segunda; só a segunda conta como entrega — em produção desde 11/09/26
- [x] **Só o CTe declara desengate** (§27.9) — o gatilho por GPS chamava de desengate a descarga; desligado em 19/09 com ligações A → B idênticas
- [x] **Desengate no pátio exige prova de parada** (§27.5) e a carga de pátio voltou a ser alcançada pelo documento (§27.3)
- [x] **`dedup_veiculo` exige prova de chegada** (§27.11) — ele fechava pela ordem da data, e em 18/09 fechou carga a 715 km e a 1.345 km do destino
- [x] **Fita documental agendada** (`EMBARQUES_FITA`, §27.11) — um retrato do BI por refresh; é ela que cria `locais` e `embarques_programacao`, e sem ela a aba de ordens abre vazia
- [x] **Ordem de coleta ligada à carga** (`EMBARQUES_COLETA`) — embarcador, CNPJ de origem/destino e a classe `L1` do aferidor
- [x] **Reconciliação de documento** (`EMBARQUES_RECONCILIACAO`) — manifesto que sumiu do 916 vira `Cancelada`
- [ ] Aviso precoce por GPS "⚓ parada na filial há N h" (rótulo) — os 85% dos desengates que só aparecem quando o manifesto seguinte nasce
- [x] **`EMBARQUES_AUTO_DEFASAGEM=0` + disparo do diário após cada refresh** (§27.12) — no ar desde 20/09; a carga passou a nascer no mesmo dia do carregamento
- [x] **Aba de Coletas no menu, no Início e na landing** — a página existia e não havia como chegar nela
- [ ] **Status `Programada`** (a carga nasce na ordem comandada) e o endereço do cadastro `locais` como âncora de chegada
- [ ] `KM FALTANDO` em carga entregue e `KM RASTREADOR —` na perna 1 de um desengate (§24.6)
- [x] **Publicar no Power BI as 3 colunas novas de tarifas** (`icms_incluso`, `pedagio_incluso`, `prazo_recebimento`) — já publicadas; cards e "Total + Impostos" ativos
- [x] **Análise por Veículo** (cavalo/carreta/motorista/proprietário) com custo real da frota (pedágio + combustível + folha + manutenção/seguro/rastreador) e **drawer de detalhe** por carga; normalização de placa Mercosul
- [x] **Pneu** na Análise por Veículo (eventos 5411/5412, split cavalo×carreta por nº de pneus) — frota e carreta
- [x] **Coluna Proprietário** (dono do cavalo) + donos dos cavalos no drawer da carreta
- [x] **Donut "Composição do Faturamento"** (custos diluídos na receita quando 1 tipo) + paleta dessaturada
- [x] **KM Abast. (hodômetro)** ao lado do KM Rota, tornando o Km/L auditável
- [x] **Filtros Mês/Tipo como dropdown** (caixinhas + Aplicar)
- [x] **Fix da colisão de placa** antiga×Mercosul no cadastro (preferir a Mercosul real)
- [x] **AutoFilter estilo Excel** por coluna em Conhecimentos e Despesas (`report-filter.js`)
- [x] **Backfill do histórico da 3S** (`/HistoricoPosicao`) — cobertura de detecção de 81% → 100% contra o gabarito de 1 Hz
- [x] **Relatório PGR de excesso de velocidade** (aba `/pgr`, imagem + link por WhatsApp, situação de carga provada por GPS)
- [ ] **Calibrar a situação de carga** contra a base backfillada (limiares vieram de estudo sobre outra amostra)
- [x] **PGR: cavalo na linha, filtros (cavalo/carreta/motorista) e seleção de mês**
- [x] **Aba Contábil** (posição por banco + extrato 456 / faturamento 441 / ACNI 571 + status do de-para), com as guardas de transferência interna, programados e valor cheio do CTRC
- [x] **Configuração editável pela contadora** — eventos (conta + flags + histórico append-only) e contas fixas, com o plano de contas em tabela e trava contra conta inexistente ou sintética
- [ ] **Gerar o arquivo de importação contábil** (`Z;data;débito;crédito;valor;histórico`) — depende de ela preencher a conta dos 60 eventos e escolher entre 166 e 506 para a contrapartida de fornecedores
- [ ] **Regras de classificação do 456 → tabela** — hoje `_SWITCH_REGRA_456` no código, e já mudou uma vez (R$ 2,7 mi ficavam fora)
- [ ] **Criar no plano de contas** o TRIBANCO e o CAIXA PAMBANK (hoje sem conta na aba Contábil)
- [x] **Conferência CIOT** (CTRB × manifesto × CIOT, aba `/ciot` + WhatsApp) — preparada, desligada (`CIOT_CONFERENCIA`/`CIOT_ENVIO`)
- [ ] **PGR fase 2**: ranking por motorista, evolução mês a mês e CSV
- [x] **Consolidação diária placa+dia** (`embarques_rastreio_dia`) — odômetro real, km vazio e dias parados sobrevivendo à retenção
- [ ] **km/L pelo GPS do rastreamento** (substituir o hodômetro do ValeCard, que é sujo, na Análise por Veículo)
- [ ] **Corrigir o km/L da Análise por Veículo** — hoje soma todos os litros do mês contra um KM mutilado pela guarda de 3.000 km do `_km_hodometro`, e ~50% do litro vem da base CAIS **sem hodômetro nenhum**: o resultado sai fisicamente impossível (0,98 km/L medido num cavalo em ago/26)
- [ ] **Conectar carga (embarques) ↔ documentos fiscais** (auditoria/conhecimentos) por placa+data ou manifesto — enriquecer auditoria com Nº da carga, "Rastreada" e Embarcador
- [ ] **Dedupe do mapa geral** (carga de frota aparece 2× — cavalo + carreta)
- [ ] Cache do token Power BI (atualmente requisitado a cada chamada)
- [ ] Dashboard de auditoria com gráficos similares ao DRE
- [ ] Histórico persistente do chat IA (não só sessão)
- [ ] Notificações de eventos críticos (margem caindo, despesa anormal, atraso de carga)
- [ ] Exportação em Excel (.xlsx) além de CSV
- [ ] Acompanhamento de terceiros (link público / bot WhatsApp)
- [ ] Dark/light theme toggle

---

## Suporte

Repositório: https://github.com/ggabrielmilho-web/tabela-auditoria
Domínio: rizza.carvalhoia.com
