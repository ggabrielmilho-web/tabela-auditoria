# Tabela Auditoria — Rizza Transportes

Sistema web interno da **Rizza Transportes** que reúne, numa única interface acessível via navegador, duas grandes frentes:

1. **Analítico/financeiro** — auditoria de receita, análise de tarifas, geração de atas de reunião por IA e relatórios DRE com assistente financeiro. Substitui múltiplos relatórios Power BI.
2. **Operacional/logística** — lançamento e acompanhamento de cargas (Embarques) e **rastreamento GPS** dos veículos em rota, com mapa em tempo real, detecção automática de saída/entrega e cálculo de rota planejada.

URL de produção: **https://rizza.carvalhoia.com**

---

## Funcionalidades

### Para todos os usuários autenticados
- **Auditoria Receita** (`/`) — Dashboard com KPIs e tabela de auditoria de receita, com filtros, drag-and-drop de colunas e exportação CSV
- **Tarifas** (`/tarifas`) — Consulta de tabela de fretes com filtros em cascata (cliente → origem → destino → tipo veículo) e simulador de frete
- **Embarques** (`/embarques`) — Módulo operacional de lançamento de cargas (Terceiro / Agregado / Frota), relatório filtrável com exportação CSV, edição com log de auditoria por campo e histórico
- **Mapa / Rastreamento** (`/embarques/mapa`) — Mapa em tempo real (Leaflet) com a posição de todos os veículos e o trajeto detalhado de cada carga, rota planejada vs. percorrida e KPIs de viagem

### Restrito a admins
- **Reunião** (`/reuniao`) — Gerador de ata de reunião a partir de áudio. Transcreve via AssemblyAI (com identificação de falantes) e gera ata profissional via GPT-4.1-mini. Exporta em Word e PDF
- **DRE** (`/dre`) — Demonstração do Resultado do Exercício com 4 gráficos analíticos (Waterfall, Donut por Grupo, Pareto 80/20, Comparativo Mensal) e chat IA financeiro com streaming em tempo real
- **Despesas** (`/dre/despesas`) — Auditoria detalhada de `consulta_despesas_477` com filtros, drilldown por grupo/evento e exportação CSV em streaming
- **Conhecimentos** (`/dre/conhecimentos`) — Auditoria detalhada de `conhecimentos_emitidos` com filtros e exportação CSV em streaming
- **Admin** (`/admin`) — Gerenciamento de usuários, papéis e permissões por tipo de operação

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
- **Power BI REST API** — dados analíticos (DRE, tarifas, auditoria, despesas, conhecimentos) e cadastros de origem (motoristas, veículos) via consultas DAX
  - 2 datasets diferentes:
    - Dataset principal (auditoria + tarifas + motoristas + veículos)
    - Dataset DRE (DRE + despesas + conhecimentos)
- **PostgreSQL local** — toda a escrita operacional: cargas, destinos, log de edição, posições GPS, KPIs de viagem, centroides de municípios
- **3S Tecnologia (DataExportAPI)** — posições GPS dos rastreadores dos veículos (com modo simulado para desenvolvimento)
- **OpenRouteService** — cálculo de rota rodoviária para caminhões (HGV): polyline, distância e duração
- **IBGE** — autocomplete de cidades (no formulário) e centroides de municípios (geocoding do trajeto)

### Integrações IA
- **OpenAI GPT-4.1-mini** — geração de atas e chat financeiro
- **AssemblyAI** — transcrição de áudio com diarização de falantes (universal-3-pro + universal-2)

### Frontend
- HTML + CSS + JavaScript puro (sem framework, sem build step)
- **Chart.js 4.4** + plugins `annotation` e `datalabels` (gráficos do DRE)
- **Leaflet** — mapas de rastreamento (posições e trajetos)
- **marked.js** — renderização de markdown na ata e no chat
- **Sortable.js** — drag-and-drop de colunas
- Fontes: DM Sans + JetBrains Mono

### Exportação
- **python-docx** — geração de Word
- **reportlab** — geração de PDF
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
├── server.py                    # Backend Flask (rotas + lógica) — ~2.800 linhas
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
├── admin.html                   # Gerenciamento de usuários
├── dre.html                     # DRE com gráficos e chat IA
├── dre-despesas.html            # Auditoria detalhada de despesas
├── dre-conhecimentos.html       # Auditoria detalhada de conhecimentos
├── embarques.html               # Landing de embarques (KPIs + atalhos)
├── embarques-novo.html          # Formulário de lançamento de carga
├── embarques-relatorio.html     # Listagem + filtros + CSV + edição + histórico
├── mapa.html                    # Mapa geral (todos os veículos)
├── mapa-carga.html              # Mapa de uma carga (trajeto + rota planejada)
│
│   # ── Módulo Rastreamento ──
├── rastreamento_worker.py       # Worker daemon (60s): posições, saída/entrega auto, recálculo de rota
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

### IA
| Variável | Descrição |
|---|---|
| `OPENAI_API_KEY` | Chave da OpenAI (ata + chat IA) |
| `ASSEMBLYAI_API_KEY` | Chave da AssemblyAI (transcrição) |

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
| `RASTREAMENTO_RAIO_KM` | Raio (km) p/ considerar veículo "na" origem/destino | `5` |
| `RASTREAMENTO_DESVIO_KM` | Desvio (km) da rota que dispara recálculo | `10` |
| `RASTREAMENTO_RETENCAO_DIAS` | Retenção do histórico de posições | `30` |
| `RASTREAMENTO_RECALCULO_MIN` | Intervalo mínimo entre recálculos de rota (min) | `30` |

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
- `GET /dre` — DRE (admin)
- `GET /dre/despesas` — Despesas (admin)
- `GET /dre/conhecimentos` — Conhecimentos (admin)
- `GET /admin` — Admin (admin)
- `GET /embarques` — Landing de embarques
- `GET /embarques/novo` — Formulário de lançamento
- `GET /embarques/relatorio` — Relatório de cargas
- `GET /embarques/<id>/editar` — Edição de carga
- `GET /embarques/mapa` — Mapa geral de rastreamento
- `GET /embarques/cargas/<id>/mapa` — Mapa de uma carga

### Dados analíticos
- `GET /api/status` — status da config Power BI
- `GET /api/auditoria` — dados de auditoria
- `GET /api/tarifas` — tabela de tarifas
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

### Chat IA
- `POST /api/chat-dre` — chat financeiro com streaming SSE

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
- `POST /api/embarques/cargas` — cria carga (snapshot + destinos em transação, gera `numero`, retorna warnings)
- `GET /api/embarques/cargas` — listagem com filtros (período, tipo, cliente, embarcador, motorista, UF, status, busca livre)
- `GET /api/embarques/cargas/<id>` — detalhe (carga + destinos)
- `PATCH /api/embarques/cargas/<id>` — edita (diff por campo → `embarques_cargas_log`; status `Entregue` preenche `data_conclusao`)
- `GET /api/embarques/cargas/<id>/log` — histórico de edições
- `GET /api/embarques/cargas/csv` — CSV streaming (mesmos filtros)
- `GET /api/embarques/kpis` — 4 contadores (hoje, em rota, entregues no mês, abertas)

### Rastreamento (todos sob `@login_required`)
- `GET /api/rastreamento/posicoes` — posições atuais + info da carga ativa (filtros: `carregado`, `eh_rizza`, `q`)
- `GET /api/rastreamento/cargas/<id>/trajeto` — trajeto percorrido + rota planejada + KPIs + raios
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
| `embarques_cargas` | Carga com snapshot completo de motorista/veículos como TEXTO (preserva histórico). `numero` no formato `C-AAAA-000001`. Inclui campos de rastreamento: `origem_latitude/longitude`, `data_saida_real`, `saida_auto`, `no_local_desde`, `entregue_auto`, `rota_planejada_polyline`, `distancia_planejada_km`, `duracao_estimada_min`, `rota_recalculada_em` |
| `embarques_cargas_destinos` | Destinos múltiplos por carga (`ordem`, cidade, UF, lat/lng) |
| `embarques_cargas_log` | Auditoria de edição — 1 linha por campo alterado |
| `clientes` | Tabela existente reutilizada; índice único case-insensitive p/ cadastro manual |

### Rastreamento (Postgres local)
| Tabela | Função |
|---|---|
| `embarques_3s_token` | Token da 3S persistido (sobrevive a restart; single-row `id=1`) |
| `embarques_veiculos_rastreio` | Mapeamento placa → idVeiculo da 3S |
| `embarques_posicoes_atuais` | Última posição por placa (UPSERT pelo worker) |
| `embarques_posicoes_historico` | Timeline de posições (dedup `placa+data_posicao`) |
| `embarques_cargas_rastreio_kpi` | KPIs consolidados por carga (distância, velocidade, tempo em movimento/parado) |
| `municipios_ibge` | Centroides IBGE p/ geocoding de origem/destino |
| `embarques_3s_log` | Log de chamadas a APIs externas (provider `3S` / `ORS` / `SIM`) |
| `embarques_simulacao` | Fonte de posições quando `MODO_SIMULADO=true` |
| `icms_aliquota` | Matriz ICMS (origem UF × destino UF) usada pelo simulador de frete |

### Tabelas Power BI (somente leitura via DAX)
- `public conhecimentos_emitidos` — 141 colunas. Filtrada por `data_autorizacao`
- `public consulta_despesas_477` — 50 colunas. Filtrada pela coluna calculada `REF` (formato `YYYY/MM`)
- `public tarifas_frete` — tabela de tarifas por cliente/rota/veículo
- `public motoristas_047` — motoristas (chave única `cpf`)
- `public veiculost_045` — veículos (cavalo/carreta/truck)
- `Auditoria Receita` — fato de auditoria

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

Detalhes da Fase 1 estão em [`PLANO-EMBARQUES.md`](PLANO-EMBARQUES.md).

---

## Módulo Rastreamento

Acompanhamento GPS dos veículos em rota, com mapa em tempo real e automação de eventos da viagem.

### Componentes
- **`tres_s_client.py`** — cliente da DataExportAPI da 3S. Token cacheado em `embarques_3s_token` (renovado 60s antes de expirar), **token bucket de 8 chamadas/min** (margem do limite oficial de 10/min), retry no 401. Quando `MODO_SIMULADO=true`, roteia tudo para `simulador_3s.py`.
- **`simulador_3s.py`** — stub com a mesma interface da 3S, lendo posições de `embarques_simulacao`. Permite testar o fluxo sem a API real.
- **`ors_client.py`** — OpenRouteService (`/v2/directions/driving-hgv`). Retorna polyline + distância + duração. Free tier: 2.000 chamadas/dia.
- **`geocoding.py`** — distância Haversine, normalização de nome de cidade (uppercase sem acento) e geocoder por centroide IBGE.
- **`rastreamento_worker.py`** — thread daemon dentro do processo Flask.

### Ciclo do worker (a cada `RASTREAMENTO_INTERVALO`, default 60s)
1. Busca a última posição de todos os veículos (3S real ou simulador) → UPSERT em `embarques_posicoes_atuais` + INSERT em `embarques_posicoes_historico`.
2. Para cada carga `Aberta`/`Em rota` com veículo mapeado, detecta automaticamente:
   - **Saída da origem** (`Aberta` → `Em rota`, marca `saida_auto`)
   - **Chegada na cidade do destino** (marca `no_local_desde`)
   - **Saída do destino** = **entrega automática** (`entregue_auto`)
   - **Recálculo da rota** (ORS) se passou `RASTREAMENTO_RECALCULO_MIN` ou divergiu mais que `RASTREAMENTO_DESVIO_KM` da rota planejada
3. Eventos exigem `RASTREAMENTO_CICLOS_CONFIRMACAO` ciclos consecutivos dentro do `RASTREAMENTO_RAIO_KM` para serem confirmados (evita falso positivo).
4. Job de retenção 1×/dia limpa histórico além de `RASTREAMENTO_RETENCAO_DIAS`.

O worker só inicia se `START_WORKER=true`. O modo (SIMULADO/REAL) é impresso no boot.

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
- Decoradores `@login_required` e `@admin_required` em todas as rotas sensíveis
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
</content>
</invoke>
