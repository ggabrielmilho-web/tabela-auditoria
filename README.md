# Tabela Auditoria — Rizza Transportes

Sistema web interno da **Rizza Transportes** para auditoria financeira, análise de tarifas, geração de atas de reunião e relatórios DRE com assistente IA. Substitui múltiplos relatórios Power BI por uma interface unificada acessível via navegador.

URL de produção: **https://rizza.carvalhoia.com**

---

## Funcionalidades

### Para todos os usuários autenticados
- **Auditoria Receita** (`/`) — Dashboard com KPIs e tabela de auditoria de receita, com filtros, drag-and-drop de colunas e exportação CSV
- **Tarifas** (`/tarifas`) — Consulta de tabela de fretes com filtros em cascata (cliente → origem → destino → tipo veículo) e simulador de frete

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
- **PostgreSQL** (apenas para autenticação de usuários da aplicação)
- **psycopg2-binary** — driver Postgres
- **python-dotenv** — variáveis de ambiente
- **werkzeug** — hash de senhas

### Fonte de Dados
- **Power BI REST API** — toda a operação de dados (DRE, tarifas, auditoria, despesas, conhecimentos) via consultas DAX
- 2 datasets diferentes:
  - Dataset principal (auditoria + tarifas)
  - Dataset DRE (DRE + despesas + conhecimentos)

### Integrações IA
- **OpenAI GPT-4.1-mini** — geração de atas e chat financeiro
- **AssemblyAI** — transcrição de áudio com diarização de falantes (universal-3-pro + universal-2)

### Frontend
- HTML + CSS + JavaScript puro (sem framework, sem build step)
- **Chart.js 4.4** + plugins `annotation` e `datalabels`
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
│  │  ┌─────────────┐  ┌─────────────────┐                 │  │
│  │  │  Traefik    │──│  Flask App      │                 │  │
│  │  │  (SSL LE)   │  │  (server.py)    │                 │  │
│  │  └─────────────┘  └────────┬────────┘                 │  │
│  │                            │                           │  │
│  │                   ┌────────┴────────┐                  │  │
│  │                   ▼                 ▼                  │  │
│  │            ┌──────────────┐  ┌──────────────┐         │  │
│  │            │  Postgres    │  │ Power BI API │         │  │
│  │            │  (auth only) │  │  (via DAX)   │         │  │
│  │            └──────────────┘  └──────────────┘         │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                                       ▼
┌──────────────────┐                  ┌──────────────────┐
│ OpenAI API       │                  │ AssemblyAI API   │
│ (GPT-4.1-mini)   │                  │ (Transcrição)    │
└──────────────────┘                  └──────────────────┘
```

---

## Estrutura de Arquivos

```
Tabela Auditoria/
├── server.py                    # Backend Flask (rotas + lógica)
├── init_db.py                   # Criação inicial da tabela de usuários
├── requirements.txt             # Dependências Python
├── Dockerfile                   # Imagem Docker
├── docker-compose.yml           # Stack do Portainer (Swarm)
├── .env                         # Variáveis de ambiente (não commitado)
├── .gitignore                   # Arquivos ignorados pelo git
├── .dockerignore                # Arquivos ignorados pelo build
│
├── login.html                   # Página de login
├── index.html                   # Auditoria Receita (dashboard principal)
├── tarifas.html                 # Tarifas de frete + simulador
├── reuniao.html                 # Gerador de ata
├── admin.html                   # Gerenciamento de usuários
├── dre.html                     # DRE com gráficos e chat IA
├── dre-despesas.html            # Auditoria detalhada de despesas
└── dre-conhecimentos.html       # Auditoria detalhada de conhecimentos
```

---

## Variáveis de Ambiente

Configuradas no Portainer (em produção) ou no `.env` local (desenvolvimento):

| Variável | Descrição |
|---|---|
| `SECRET_KEY` | Chave de sessão do Flask |
| `DB_HOST` | Host do Postgres (em produção: `postgres`) |
| `DB_PORT` | Porta do Postgres (5432) |
| `DB_NAME` | Nome do banco (`rizza_auditoria`) |
| `DB_USER` | Usuário do Postgres |
| `DB_PASSWORD` | Senha do Postgres |
| `POWERBI_TENANT_ID` | Azure AD Tenant ID |
| `POWERBI_CLIENT_ID` | Client ID do app registrado no Azure |
| `POWERBI_CLIENT_SECRET` | Client Secret |
| `POWERBI_GROUP_ID` | Workspace ID no Power BI |
| `POWERBI_DATASET_ID` | Dataset ID principal (auditoria + tarifas) |
| `POWERBI_DRE_DATASET_ID` | Dataset ID do DRE |
| `OPENAI_API_KEY` | Chave da OpenAI (ata + chat IA) |
| `ASSEMBLYAI_API_KEY` | Chave da AssemblyAI (transcrição) |

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

### Dados
- `GET /api/status` — status da config Power BI
- `GET /api/auditoria` — dados de auditoria
- `GET /api/tarifas` — tabela de tarifas
- `POST /api/dax` — query DAX customizada
- `GET /api/dre?meses=YYYY-MM,YYYY-MM,...` — DRE estruturada
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

---

## Desenvolvimento Local

### 1. Pré-requisitos
- Python 3.11+
- Postgres rodando localmente (ou tunnel SSH para o servidor)

### 2. Configurar `.env`
Criar arquivo `.env` na raiz do projeto com as variáveis listadas acima.

### 3. Instalar dependências
```bash
pip install -r requirements.txt
```

### 4. Inicializar o banco (primeira vez)
```bash
python init_db.py
```
Cria a tabela `auditoria_users` e o admin padrão `admin@rizzalog.com.br` / `admin123`.

### 5. Rodar
```bash
python server.py
```
Acesse `http://localhost:5000`

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

### Tabela de Usuários (Postgres local da app)
```sql
CREATE TABLE auditoria_users (
    id              SERIAL PRIMARY KEY,
    nome            TEXT NOT NULL,
    email           TEXT UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    role            TEXT NOT NULL DEFAULT 'viewer',  -- 'admin' ou 'viewer'
    ativo           BOOLEAN DEFAULT TRUE,
    tipos_permitidos TEXT[],  -- Carreteiro, Agregado, Frota
    criado_em       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Tabelas Power BI (somente leitura via DAX)
- `public conhecimentos_emitidos` — 141 colunas. Filtrada por `data_autorizacao`
- `public consulta_despesas_477` — 50 colunas. Filtrada pela coluna calculada `REF` (formato `YYYY/MM`)
- `public tarifas_frete` — tabela de tarifas por cliente/rota/veículo
- `Auditoria Receita` — fato de auditoria

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

---

## Convenções de Código

- **Backend**: Python com type hints opcionais, docstrings em PT-BR para funções complexas
- **Frontend**: JavaScript ES6+, sem build step, comentários em PT-BR
- **CSS**: variáveis CSS centralizadas em `:root` para tema dark consistente
- **Commits**: prefixos convencionais (`feat:`, `fix:`, `refactor:`) em PT-BR

---

## Roadmap (ideias para evolução)

- [ ] Cache do token Power BI (atualmente requisitado a cada chamada)
- [ ] Dashboard de auditoria com gráficos similares ao DRE
- [ ] Histórico persistente do chat IA (não só sessão)
- [ ] Notificações de eventos críticos (margem caindo, despesa anormal)
- [ ] Exportação em Excel (.xlsx) além de CSV
- [ ] Dark/light theme toggle

---

## Suporte

Repositório: https://github.com/ggabrielmilho-web/tabela-auditoria
Domínio: rizza.carvalhoia.com
