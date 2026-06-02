# Plano — Módulo Embarques (Fase 1: Lançamento + Relatório)

## Contexto

Adicionar ao site interno **Tabela Auditoria** (`c:\Phyton-Projetos\Tabela Auditoria\`) um novo módulo operacional pra registrar carregamentos de carga (Terceiros, Agregados, Frota). Hoje esse lançamento é manual e fragmentado entre equipes; o módulo centraliza num formulário web com validação de regras de negócio + um relatório filtrável e exportável.

Esta fase **não inclui** o mapa de veículos (módulo 3), rastreamento, integração com rastreadores, pré-programação de carga ou cruzamento inteligente — ficam pra fase 2+.

**Objetivo desta fase**: equipe consegue lançar uma carga em < 1min pelo formulário, e a gestão consegue listar/filtrar/exportar em CSV o que foi lançado.

## Decisões-chave (validadas em conversa)

| Tema | Decisão |
|---|---|
| Onde gravar | Postgres local `rizza_auditoria` (não escreve no banco da Rizza). Cloudflared não é necessário nesta fase. |
| Fonte de motoristas/veículos | Power BI via DAX (mesmo padrão já usado em `/api/tarifas`). Cache server-side de 5 min. |
| Identificador de motorista | **CPF** (coluna `cpf` em `public.motoristas_047`). Único na origem, resolve homônimos. Snapshot grava `motorista_nome` + `motorista_cpf`. |
| Busca de motorista no formulário | Datalist exibe `"Nome — CPF"` e filtra por ambos client-side. |
| Tabela de clientes | Reutiliza tabela `clientes` existente (id, nome, importado_em). Cadastro manual entra com `importado_em = NULL`. |
| Embarcador (filtro relatório) | É o usuário logado que lançou (`criado_por_id` / `criado_por_nome`). Sem campo extra no form. |
| Permissões | `@login_required` em tudo. Sem rota admin-only nesta fase. |
| Exclusão de carga | Não tem DELETE. Cancelamento via mudança de status → "Cancelada". |
| Auditoria de edição | Tabela `embarques_cargas_log` com diff por campo (1 linha por campo alterado). Modal "🕐 Histórico" no relatório. |
| Regras Rizza | Identificação por `proprietario ILIKE '%RIZZA%'`. Conflito de tipo_operação = **alerta amarelo**, não bloqueio. |
| Cidades | Autocomplete via API IBGE (gratuita, sem auth). |
| Múltiplos destinos | Ordem definida pelo líder da carga (livre). Tabela auxiliar `embarques_cargas_destinos`. |
| Exportação | CSV streaming nesta fase (Excel/PDF ficam pra depois). |

## Schema SQL (adicionar em `init_db.py`)

Inserir antes do `conn.commit()` (linha 60 de `c:\Phyton-Projetos\Tabela Auditoria\init_db.py`). Tudo idempotente com `IF NOT EXISTS`.

```sql
-- Cargas (snapshot completo de motorista/veículos como TEXTO p/ preservar histórico)
CREATE TABLE IF NOT EXISTS embarques_cargas (
    id                       SERIAL PRIMARY KEY,
    numero                   VARCHAR(20) UNIQUE,           -- 'C-2026-000123', gerado pós-INSERT
    tipo_operacao            VARCHAR(20) NOT NULL,         -- 'Terceiro' | 'Agregado' | 'Frota'
    status                   VARCHAR(20) NOT NULL DEFAULT 'Aberta',
                                                            -- 'Aberta' | 'Em rota' | 'Entregue' | 'Cancelada'
    cliente_id               INTEGER REFERENCES clientes(id),
    cliente_nome             VARCHAR(180) NOT NULL,        -- snapshot

    origem_cidade            VARCHAR(120) NOT NULL,
    origem_uf                CHAR(2) NOT NULL,

    motorista_nome           VARCHAR(180) NOT NULL,
    motorista_cpf            VARCHAR(20) NOT NULL,         -- identificador único na origem (motoristas_047)
    motorista_telefone       VARCHAR(40),

    cavalo_placa             VARCHAR(10) NOT NULL,
    cavalo_tipo              VARCHAR(15) NOT NULL,         -- 'Cavalo' | 'Truck'
    cavalo_marca_modelo      VARCHAR(120),
    cavalo_carroceria        VARCHAR(80),
    cavalo_proprietario      VARCHAR(180),
    cavalo_eh_rizza          BOOLEAN DEFAULT FALSE,

    carreta1_placa           VARCHAR(10),
    carreta1_marca_modelo    VARCHAR(120),
    carreta1_carroceria      VARCHAR(80),
    carreta1_proprietario    VARCHAR(180),
    carreta1_eh_rizza        BOOLEAN DEFAULT FALSE,

    carreta2_placa           VARCHAR(10),                  -- bi-trem opcional
    carreta2_marca_modelo    VARCHAR(120),
    carreta2_carroceria      VARCHAR(80),
    carreta2_proprietario    VARCHAR(180),
    carreta2_eh_rizza        BOOLEAN DEFAULT FALSE,

    data_carregamento        DATE NOT NULL,
    previsao_entrega         DATE,
    data_conclusao           TIMESTAMP,                    -- preenchido quando status='Entregue'

    observacoes              TEXT,

    criado_em                TIMESTAMP DEFAULT NOW(),
    criado_por_id            INTEGER REFERENCES auditoria_users(id),
    criado_por_nome          VARCHAR(180),
    atualizado_em            TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_cargas_data      ON embarques_cargas (data_carregamento DESC);
CREATE INDEX IF NOT EXISTS ix_cargas_status    ON embarques_cargas (status);
CREATE INDEX IF NOT EXISTS ix_cargas_cliente   ON embarques_cargas (cliente_id);
CREATE INDEX IF NOT EXISTS ix_cargas_motorista ON embarques_cargas (motorista_nome);
CREATE INDEX IF NOT EXISTS ix_cargas_tipo      ON embarques_cargas (tipo_operacao);

-- Destinos múltiplos
CREATE TABLE IF NOT EXISTS embarques_cargas_destinos (
    id         SERIAL PRIMARY KEY,
    carga_id   INTEGER NOT NULL REFERENCES embarques_cargas(id) ON DELETE CASCADE,
    ordem      SMALLINT NOT NULL DEFAULT 1,
    cidade     VARCHAR(120) NOT NULL,
    uf         CHAR(2) NOT NULL,
    UNIQUE (carga_id, ordem)
);
CREATE INDEX IF NOT EXISTS ix_destinos_carga ON embarques_cargas_destinos (carga_id);

-- Log de edição (1 linha por campo alterado)
CREATE TABLE IF NOT EXISTS embarques_cargas_log (
    id              SERIAL PRIMARY KEY,
    carga_id        INTEGER NOT NULL REFERENCES embarques_cargas(id) ON DELETE CASCADE,
    usuario_id      INTEGER REFERENCES auditoria_users(id),
    usuario_nome    VARCHAR(180),
    editado_em      TIMESTAMP DEFAULT NOW(),
    campo           VARCHAR(60) NOT NULL,
    valor_anterior  TEXT,
    valor_novo      TEXT
);
CREATE INDEX IF NOT EXISTS ix_cargas_log_carga ON embarques_cargas_log (carga_id, editado_em DESC);

-- Suporte a clientes manuais: garantir dedup case-insensitive
CREATE UNIQUE INDEX IF NOT EXISTS ux_clientes_nome_ci ON clientes (LOWER(TRIM(nome)));
```

**Geração do `numero`**: após o INSERT, `UPDATE embarques_cargas SET numero = 'C-' || EXTRACT(YEAR FROM criado_em) || '-' || LPAD(id::text, 6, '0') WHERE id = %s`.

## Arquivos a criar / modificar

### Criar
| Arquivo | Conteúdo |
|---|---|
| `c:\Phyton-Projetos\Tabela Auditoria\embarques.html` | Landing com 4 KPIs + atalhos |
| `c:\Phyton-Projetos\Tabela Auditoria\embarques-novo.html` | Formulário de lançamento |
| `c:\Phyton-Projetos\Tabela Auditoria\embarques-relatorio.html` | Listagem + filtros + CSV + modal histórico |

### Modificar `c:\Phyton-Projetos\Tabela Auditoria\server.py`
- **Rotas HTML novas** após linha 215 (junto das outras rotas-página)
- **Bloco EMBARQUES** antes do `if __name__ == '__main__':` (linha 1551): helpers + cache + 11 endpoints

### Modificar `c:\Phyton-Projetos\Tabela Auditoria\init_db.py`
- Inserir DDL (acima) antes do `conn.commit()` (linha 60)

### Modificar — adicionar link "🚚 Embarques" no top-bar
- `c:\Phyton-Projetos\Tabela Auditoria\index.html` (linhas 303-309)
- `c:\Phyton-Projetos\Tabela Auditoria\tarifas.html` (linhas 306-310)
- `c:\Phyton-Projetos\Tabela Auditoria\dre.html`
- `c:\Phyton-Projetos\Tabela Auditoria\dre-despesas.html`
- `c:\Phyton-Projetos\Tabela Auditoria\dre-conhecimentos.html`
- `c:\Phyton-Projetos\Tabela Auditoria\reuniao.html`
- `c:\Phyton-Projetos\Tabela Auditoria\admin.html`

## Rotas HTML

| Rota | Decorator | Servir |
|---|---|---|
| `GET /embarques` | `@login_required` | `embarques.html` |
| `GET /embarques/novo` | `@login_required` | `embarques-novo.html` |
| `GET /embarques/relatorio` | `@login_required` | `embarques-relatorio.html` |

## Endpoints API

Todos sob `@login_required`. Respostas padrão: `{ok: true, data: [...], count: N}` ou `{ok: false, error: str, detail: obj}` — mesmo padrão de `/api/tarifas` (server.py:218-248).

### Leitura — reutiliza padrão DAX

**GET `/api/embarques/motoristas`** — DAX `EVALUATE 'public motoristas_047'` no dataset principal. Retorna `[{nome, cpf, telefone?, ...}]`. CPF é o identificador único da origem (nome pode repetir entre homônimos). Cache 5min.

**GET `/api/embarques/veiculos`** — DAX `EVALUATE FILTER('public veiculost_045', ...[TIPO] IN {"Cavalo","Carreta","Truck"})`. Calcula `eh_rizza` server-side. Cache 5min. Query string `?refresh=1` bypassa cache.

**GET `/api/embarques/clientes`** — `SELECT id, nome, importado_em FROM clientes ORDER BY nome`. Sem cache (lista local rápida).

### Escrita

**POST `/api/embarques/clientes`** — Body `{nome}`. INSERT com `ON CONFLICT (LOWER(TRIM(nome))) DO NOTHING RETURNING id`. Se vazio, SELECT pelo nome normalizado pra devolver `id` existente. `importado_em` fica NULL. Resposta `{ok, id, ja_existia}`.

**POST `/api/embarques/cargas`** — Body JSON completo (snapshot do front com motorista/veículos/cliente já resolvidos). Validação server-side de campos obrigatórios + regras Rizza (com warnings, não bloqueio). INSERT em `embarques_cargas` + N INSERTs em `embarques_cargas_destinos` dentro de transação. UPDATE setando `numero`. Resposta `{ok, id, numero, warnings:[...]}`.

**PATCH `/api/embarques/cargas/<int:id>`** — Body JSON com campos atualizáveis (whitelist: `status`, `observacoes`, `previsao_entrega`, `data_carregamento`, `cliente_id` + nome, observações de motorista/cavalo/carreta etc.). Backend faz SELECT do estado atual, calcula diff, faz UPDATE + N INSERTs em `embarques_cargas_log`. Auto-preenche `data_conclusao = NOW()` se status mudou pra 'Entregue'. Resposta `{ok, alteracoes: N}`.

### Listagem

**GET `/api/embarques/cargas`** — Query string opcional: `start`, `end`, `data_campo` (carregamento|previsao), `tipo_operacao`, `cliente_id`, `criado_por_id` (filtro embarcador), `motorista` (LIKE), `origem_uf`, `destino_uf`, `status`, `q` (busca livre). SQL com LEFT JOIN LATERAL p/ `string_agg` dos destinos. ORDER BY `data_carregamento DESC, id DESC`. LIMIT 1000.

**GET `/api/embarques/cargas/<int:id>`** — Detalhe completo (carga + destinos). Usado por modal de edição.

**GET `/api/embarques/cargas/<int:id>/log`** — Histórico de edições ordenado por `editado_em DESC`. Usado pelo modal "🕐 Histórico".

**GET `/api/embarques/cargas/csv`** — Mesmos filtros do listar. Usa `stream_with_context` + `_csv_linha` (server.py:1239), espelhando `/api/dre/despesas/csv` (server.py:1253-1316). BOM UTF-8 + separador `;`.

**GET `/api/embarques/kpis`** — 4 contadores em 1 query: cargas hoje, em rota, entregues no mês, abertas.

## Reuso de funções existentes (server.py)

| Função | Linha | Reuso |
|---|---|---|
| `get_db()` | 36 | Conexão Postgres pra todos endpoints de cargas/clientes/log |
| `get_token()` | 72 | Token PBI antes de chamar DAX motoristas/veículos |
| `execute_dax(token, query, dataset_id)` | 85 | Executar DAX nas tabelas Power BI |
| `clean_rows(rows)` | 104 | Normalizar `[tabela]coluna` → `coluna` |
| `_csv_linha(valores)` | 1239 | Cada linha do CSV streaming |
| `@login_required` | 46 | Decorator em todas as novas rotas |
| Padrão `/api/tarifas` | 218-248 | Template pra endpoints DAX → JSON |
| Padrão `/api/dre/despesas/csv` | 1253-1316 | Template pro CSV streaming |

## Cache motoristas/veículos

Dict em memória no topo do bloco EMBARQUES:

```python
_EMBARQUES_CACHE = {}
_CACHE_TTL_SEG = 300  # 5 minutos

def _cache_get(key):
    entry = _EMBARQUES_CACHE.get(key)
    if entry and (time.time() - entry['ts']) < _CACHE_TTL_SEG:
        return entry['data']
    return None

def _cache_set(key, data):
    _EMBARQUES_CACHE[key] = {'data': data, 'ts': time.time()}
```

- `?refresh=1` força bypass.
- Cache por processo (suficiente — app roda single-process em dev e Swarm com 1 réplica).

## Estrutura HTML/UX

### `embarques.html` (landing)
- Top-bar padrão + título "Embarques"
- 4 cards `.kpi-card` (reusa CSS de `index.html:133-167`): Hoje, Em Rota, Entregues no mês, Abertas
- 2 botões grandes: `+ Lançar carga` (→ /embarques/novo) e `📋 Ver relatório` (→ /embarques/relatorio)
- Tabela compacta "Últimas 10 cargas" (chama `/api/embarques/cargas?limit=10`)

### `embarques-novo.html` (formulário)
Layout 2 colunas no desktop (`grid-template-columns: 1fr 1fr; gap: 24px`), 1 coluna no mobile. Seções:

1. **Cliente** — `<select>` populado via `/api/embarques/clientes` + opção "+ Cadastrar novo" que expande sub-card inline (input nome + botão salvar)
2. **Tipo de operação** — select Frota/Agregado/Terceiro (preenchido/restringido dinamicamente pelas regras Rizza)
3. **Origem** — UF (27 fixos) + Cidade (IBGE)
4. **Destinos** — lista dinâmica com botão `+ Adicionar destino`; cada linha tem UF + Cidade + `✕` (mín 1)
5. **Motorista** — `<input list>` (datalist) com type-ahead exibindo `"Nome — CPF"` (ex: `José Silva — 123.456.789-00`). Busca client-side filtra por nome OU CPF (tira pontuação antes de comparar). CPF é a chave única — homônimos resolvem assim. Ao selecionar, snapshot grava `motorista_nome` + `motorista_cpf` + `motorista_telefone`.
6. **Cavalo** — datalist filtrado por TIPO IN ('Cavalo','Truck'). Card de confirmação abaixo mostrando marca/modelo/carroceria/proprietário + badge RIZZA/TERCEIRO
7. **Carreta 1** — datalist filtrado por TIPO='Carreta'. Obrigatória se cavalo.tipo='Cavalo', opcional se 'Truck' (label muda dinamicamente). Card de confirmação.
8. **Carreta 2 (bi-trem, opcional)** — só aparece após Carreta 1 selecionada. Mesmo padrão.
9. **Datas** — data_carregamento (obrigatória) + previsao_entrega (opcional)
10. **Observações** — textarea
11. **Ações** — botão "Salvar carga" + link Cancelar

**JS — regras Rizza** (ao mudar cavalo/carreta1):
- ambos `eh_rizza` → tipo = "Frota", remove "Terceiro" do select
- xor → tipo = "Agregado", remove "Terceiro"
- nenhum → todas opções disponíveis
- cavalo Rizza + tipo vazio → auto-preenche "Frota"
- conflito (usuário forçou tipo divergente) → banner amarelo discreto, NÃO bloqueia

Após POST com sucesso: toast "Carga {numero} lançada" + redirect `/embarques/relatorio?destacar={id}`.

### `embarques-relatorio.html` (listagem)
- Top-bar padrão
- `.filtros-card`: período (start/end) + radio "Por carregamento / Por previsão", tipo, cliente, embarcador (select de `auditoria_users`), motorista, origem UF, destino UF, status, busca livre. Botões "Aplicar" / "Limpar"
- Linha ações: `↓ Exportar CSV` (link direto pro endpoint com querystring) + contador
- Tabela sticky header (igual `index.html:169-186`): Nº, Data Carreg., Status (badge), Tipo, Cliente, Origem, Destinos (concat), Motorista, Cavalo, Carreta(s), Previsão, Ações (✏ Editar, 🕐 Histórico)
- Clique no `<th>` ordena
- ✏ abre modal com formulário PATCH (mesmos campos do `/novo` mas editáveis)
- 🕐 abre modal "Histórico" listando timeline do `embarques_cargas_log`

## Sequência de implementação

1. DDL em `init_db.py` + rodar `python init_db.py` 1x
2. Helpers + cache + GET `/api/embarques/motoristas` + `/veiculos` (testa DAX)
3. GET + POST `/api/embarques/clientes`
4. POST `/api/embarques/cargas`
5. `embarques-novo.html` completo (com regras Rizza)
6. GET `/api/embarques/cargas` + `/cargas/<id>` + GET `/api/embarques/kpis`
7. `embarques.html` (landing) e `embarques-relatorio.html` (listagem)
8. PATCH `/api/embarques/cargas/<id>` + GET log + modal histórico
9. GET CSV streaming
10. Adicionar link "🚚 Embarques" nos top-bars das 7 páginas existentes

## Verificação end-to-end

App rodando local em `http://localhost:5000`. Logar com admin.

1. `python init_db.py` cria as 3 tabelas + índice em clientes. Re-rodar não dá erro.
2. Link `🚚 Embarques` aparece em todas as 7 páginas existentes e leva à landing.
3. `/embarques` mostra 4 KPIs zerados (banco vazio).
4. `/embarques/novo` carrega motoristas + veículos do PBI (1ª chamada ~2s, 2ª <100ms via cache). Datalist de motoristas exibe `"Nome — CPF"`.
4b. Digitar parte do nome no campo motorista filtra; digitar dígitos do CPF também filtra. Se houver homônimos, ambos aparecem com CPFs diferentes.
5. UF Origem = SP → IBGE popula ~645 cidades. Idem RJ.
6. "+ Cadastrar cliente" inline com nome "Teste 1" → POST → `ja_existia: false`. Repetir mesmo nome → `ja_existia: true`. Cliente fica selecionado.
7. Selecionar cavalo Rizza + carreta Rizza → tipo auto-preenche "Frota", "Terceiro" some.
8. Trocar carreta pra terceiro → tipo vira "Agregado".
9. Selecionar cavalo Truck → label Carreta 1 muda pra "(opcional)", form aceita salvar sem carreta.
10. Forçar tipo "Terceiro" com cavalo Rizza → banner amarelo discreto aparece, salva mesmo assim com warning no response.
11. Salvar carga → banco tem 1 linha em `embarques_cargas` com `numero='C-2026-000001'`, N linhas em destinos, snapshot completo gravado.
12. Carga aparece em `/embarques/relatorio`. Filtro tipo=Frota mostra; tipo=Terceiro esconde.
13. Clicar ✏ na linha → modal de edição. Mudar status pra "Em rota". Salvar → 1 linha em `embarques_cargas_log` com `campo='status', valor_anterior='Aberta', valor_novo='Em rota'`.
14. Clicar 🕐 → modal de histórico exibe a edição em timeline.
15. ↓ Exportar CSV com filtros aplicados → arquivo baixa, abre no Excel com acentuação correta, separador `;`.
16. Logar como usuário não-admin → tudo funciona igual (sem restrições nesta fase).

## Fora desta fase (registrar pra próximas)

- Módulo 3 — Mapa de veículos (Leaflet + posições periódicas)
- Scraper/automação de rastreador (Autotrac + localizadores das carretas)
- Acompanhamento manual de terceiros (link público / bot WhatsApp)
- Exportação Excel/PDF
- KPIs avançados / dashboard do diretor
- Aprovação de carga (workflow)
- Pré-programação de carregamento
