"""
Inicializa o banco de dados para o sistema de autenticação da Tabela Auditoria.
Rode UMA VEZ antes de subir o servidor:  python init_db.py
(Pode rodar novamente sem problemas — não apaga dados existentes)
"""

import os
import psycopg2
from werkzeug.security import generate_password_hash
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(
    host=os.getenv('DB_HOST', 'localhost'),
    port=os.getenv('DB_PORT', '5432'),
    dbname=os.getenv('DB_NAME', 'postgres'),
    user=os.getenv('DB_USER', 'postgres'),
    password=os.getenv('DB_PASSWORD', ''),
)
cur = conn.cursor()

# Criar tabela de usuários
cur.execute("""
    CREATE TABLE IF NOT EXISTS auditoria_users (
        id              SERIAL PRIMARY KEY,
        nome            VARCHAR(255) NOT NULL,
        email           VARCHAR(255) UNIQUE NOT NULL,
        password_hash   VARCHAR(255) NOT NULL,
        role            VARCHAR(20) DEFAULT 'viewer',
        ativo           BOOLEAN DEFAULT true,
        tipos_permitidos TEXT[] DEFAULT ARRAY['Carreteiro','Agregado','Frota'],
        criado_em       TIMESTAMP DEFAULT NOW()
    );
""")

# Adiciona coluna caso a tabela já existia sem ela (idempotente)
cur.execute("""
    ALTER TABLE auditoria_users
    ADD COLUMN IF NOT EXISTS tipos_permitidos TEXT[] DEFAULT ARRAY['Carreteiro','Agregado','Frota'];
""")

# Inserir admin padrão (ignora se já existir)
admin_email = 'admin@rizzalog.com.br'
admin_senha = 'admin123'
cur.execute("SELECT id FROM auditoria_users WHERE email = %s", (admin_email,))
if not cur.fetchone():
    cur.execute(
        """INSERT INTO auditoria_users (nome, email, password_hash, role, tipos_permitidos)
           VALUES (%s, %s, %s, 'admin', ARRAY['Carreteiro','Agregado','Frota'])""",
        ('Administrador', admin_email, generate_password_hash(admin_senha))
    )
    print(f"\n✅ Admin criado com sucesso!")
    print(f"   Email: {admin_email}")
    print(f"   Senha: {admin_senha}")
    print(f"   ⚠️  Troque a senha após o primeiro login!\n")
else:
    print(f"\n✅ Tabela já existe. Admin '{admin_email}' já cadastrado.\n")

# ════════════════════════════════════════════════════════════════════════════
# EMBARQUES — Módulo operacional de lançamento de cargas
# ════════════════════════════════════════════════════════════════════════════

# Cargas — snapshot completo de motorista/veículos como TEXTO p/ preservar histórico
cur.execute("""
    CREATE TABLE IF NOT EXISTS embarques_cargas (
        id                       SERIAL PRIMARY KEY,
        numero                   VARCHAR(20) UNIQUE,
        tipo_operacao            VARCHAR(20) NOT NULL,
        status                   VARCHAR(20) NOT NULL DEFAULT 'Aberta',

        cliente_id               INTEGER REFERENCES clientes(id),
        cliente_nome             VARCHAR(180) NOT NULL,

        origem_cidade            VARCHAR(120) NOT NULL,
        origem_uf                CHAR(2) NOT NULL,

        motorista_nome           VARCHAR(180) NOT NULL,
        motorista_cpf            VARCHAR(20) NOT NULL,
        motorista_telefone       VARCHAR(40),

        cavalo_placa             VARCHAR(10) NOT NULL,
        cavalo_tipo              VARCHAR(15) NOT NULL,
        cavalo_marca_modelo      VARCHAR(120),
        cavalo_carroceria        VARCHAR(80),
        cavalo_proprietario      VARCHAR(180),
        cavalo_eh_rizza          BOOLEAN DEFAULT FALSE,

        carreta1_placa           VARCHAR(10),
        carreta1_marca_modelo    VARCHAR(120),
        carreta1_carroceria      VARCHAR(80),
        carreta1_proprietario    VARCHAR(180),
        carreta1_eh_rizza        BOOLEAN DEFAULT FALSE,

        carreta2_placa           VARCHAR(10),
        carreta2_marca_modelo    VARCHAR(120),
        carreta2_carroceria      VARCHAR(80),
        carreta2_proprietario    VARCHAR(180),
        carreta2_eh_rizza        BOOLEAN DEFAULT FALSE,

        data_carregamento        DATE NOT NULL,
        previsao_entrega         DATE,
        data_conclusao           TIMESTAMP,

        observacoes              TEXT,

        criado_em                TIMESTAMP DEFAULT NOW(),
        criado_por_id            INTEGER REFERENCES auditoria_users(id),
        criado_por_nome          VARCHAR(180),
        atualizado_em            TIMESTAMP
    );
""")
cur.execute("CREATE INDEX IF NOT EXISTS ix_cargas_data      ON embarques_cargas (data_carregamento DESC);")
cur.execute("CREATE INDEX IF NOT EXISTS ix_cargas_status    ON embarques_cargas (status);")
cur.execute("CREATE INDEX IF NOT EXISTS ix_cargas_cliente   ON embarques_cargas (cliente_id);")
cur.execute("CREATE INDEX IF NOT EXISTS ix_cargas_motorista ON embarques_cargas (motorista_nome);")
cur.execute("CREATE INDEX IF NOT EXISTS ix_cargas_tipo      ON embarques_cargas (tipo_operacao);")

# Destinos múltiplos por carga
cur.execute("""
    CREATE TABLE IF NOT EXISTS embarques_cargas_destinos (
        id         SERIAL PRIMARY KEY,
        carga_id   INTEGER NOT NULL REFERENCES embarques_cargas(id) ON DELETE CASCADE,
        ordem      SMALLINT NOT NULL DEFAULT 1,
        cidade     VARCHAR(120) NOT NULL,
        uf         CHAR(2) NOT NULL,
        UNIQUE (carga_id, ordem)
    );
""")
cur.execute("CREATE INDEX IF NOT EXISTS ix_destinos_carga ON embarques_cargas_destinos (carga_id);")

# Log de edição — 1 linha por campo alterado
cur.execute("""
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
""")
cur.execute("CREATE INDEX IF NOT EXISTS ix_cargas_log_carga ON embarques_cargas_log (carga_id, editado_em DESC);")

# Dedup case-insensitive da tabela clientes existente (para cadastro manual)
cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_clientes_nome_ci ON clientes (LOWER(TRIM(nome)));")

print("✅ Tabelas de Embarques (cargas, destinos, log) prontas.\n")

# ════════════════════════════════════════════════════════════════════════════
# RASTREAMENTO — Integração 3S Tecnologia + OpenRouteService + Simulação
# ════════════════════════════════════════════════════════════════════════════

# Token da 3S persistido (sobrevive a restart)
cur.execute("""
    CREATE TABLE IF NOT EXISTS embarques_3s_token (
        id              SMALLINT PRIMARY KEY DEFAULT 1,
        token           TEXT NOT NULL,
        expiration      TIMESTAMP NOT NULL,
        atualizado_em   TIMESTAMP DEFAULT NOW(),
        CHECK (id = 1)
    );
""")

# Mapeamento placa → idVeiculo da 3S (real ou simulado)
cur.execute("""
    CREATE TABLE IF NOT EXISTS embarques_veiculos_rastreio (
        id              SERIAL PRIMARY KEY,
        placa           VARCHAR(10) UNIQUE NOT NULL,
        id_veiculo_3s   BIGINT UNIQUE NOT NULL,
        id_equipamento  BIGINT,
        frota           VARCHAR(50),
        modelo          VARCHAR(120),
        tipo            VARCHAR(30),
        sincronizado_em TIMESTAMP DEFAULT NOW()
    );
""")

# Última posição por placa (UPSERT pelo worker)
cur.execute("""
    CREATE TABLE IF NOT EXISTS embarques_posicoes_atuais (
        placa           VARCHAR(10) PRIMARY KEY,
        id_veiculo_3s   BIGINT NOT NULL,
        data_posicao    TIMESTAMP NOT NULL,
        latitude        NUMERIC(10,7) NOT NULL,
        longitude       NUMERIC(10,7) NOT NULL,
        velocidade      INTEGER,
        ignicao         BOOLEAN,
        direcao         VARCHAR(20),
        uf              CHAR(2),
        cidade          VARCHAR(120),
        bairro          VARCHAR(120),
        endereco        VARCHAR(200),
        bloqueio        BOOLEAN,
        odometer        BIGINT,
        atualizado_em   TIMESTAMP DEFAULT NOW()
    );
""")

# Timeline de posições (INSERT a cada ciclo, dedup placa+data)
cur.execute("""
    CREATE TABLE IF NOT EXISTS embarques_posicoes_historico (
        id              BIGSERIAL PRIMARY KEY,
        placa           VARCHAR(10) NOT NULL,
        id_veiculo_3s   BIGINT NOT NULL,
        data_posicao    TIMESTAMP NOT NULL,
        latitude        NUMERIC(10,7) NOT NULL,
        longitude       NUMERIC(10,7) NOT NULL,
        velocidade      INTEGER,
        ignicao         BOOLEAN,
        uf              CHAR(2),
        cidade          VARCHAR(120),
        UNIQUE (placa, data_posicao)
    );
""")
cur.execute("CREATE INDEX IF NOT EXISTS ix_pos_hist_placa_data ON embarques_posicoes_historico (placa, data_posicao);")

# KPIs consolidados por carga (sobrevive à limpeza de posições)
cur.execute("""
    CREATE TABLE IF NOT EXISTS embarques_cargas_rastreio_kpi (
        carga_id            INTEGER PRIMARY KEY REFERENCES embarques_cargas(id) ON DELETE CASCADE,
        placa               VARCHAR(10) NOT NULL,
        distancia_metros    BIGINT,
        velocidade_max      INTEGER,
        velocidade_media    NUMERIC(5,1),
        tempo_movimento_seg BIGINT,
        tempo_parado_seg    BIGINT,
        consolidado_em      TIMESTAMP DEFAULT NOW(),
        consolidado_final   BOOLEAN DEFAULT FALSE
    );
""")

# Centroide IBGE dos municípios
cur.execute("""
    CREATE TABLE IF NOT EXISTS municipios_ibge (
        cidade_normalizada  VARCHAR(120) NOT NULL,
        uf                  CHAR(2) NOT NULL,
        cidade              VARCHAR(120) NOT NULL,
        latitude            NUMERIC(10,7) NOT NULL,
        longitude           NUMERIC(10,7) NOT NULL,
        PRIMARY KEY (cidade_normalizada, uf)
    );
""")

# Log de chamadas a APIs externas (3S, ORS, SIM)
cur.execute("""
    CREATE TABLE IF NOT EXISTS embarques_3s_log (
        id          BIGSERIAL PRIMARY KEY,
        chamado_em  TIMESTAMP DEFAULT NOW(),
        provider    VARCHAR(10) NOT NULL DEFAULT '3S',
        endpoint    VARCHAR(80) NOT NULL,
        duracao_ms  INTEGER,
        status_http INTEGER,
        erro_codigo VARCHAR(20),
        erro_msg    TEXT
    );
""")
cur.execute("CREATE INDEX IF NOT EXISTS ix_3s_log_data ON embarques_3s_log (chamado_em DESC);")

# Tabela de simulação — fonte de dados quando MODO_SIMULADO=true
cur.execute("""
    CREATE TABLE IF NOT EXISTS embarques_simulacao (
        id              BIGSERIAL PRIMARY KEY,
        placa           VARCHAR(10) NOT NULL,
        id_veiculo_3s   BIGINT NOT NULL,
        data_posicao    TIMESTAMP NOT NULL DEFAULT NOW(),
        latitude        NUMERIC(10,7) NOT NULL,
        longitude       NUMERIC(10,7) NOT NULL,
        velocidade      INTEGER DEFAULT 0,
        ignicao         BOOLEAN DEFAULT TRUE,
        direcao         VARCHAR(20),
        uf              CHAR(2) NOT NULL,
        cidade          VARCHAR(120) NOT NULL,
        bairro          VARCHAR(120),
        endereco        VARCHAR(200),
        bloqueio        BOOLEAN DEFAULT FALSE,
        odometer        BIGINT
    );
""")
cur.execute("CREATE INDEX IF NOT EXISTS ix_sim_placa_data ON embarques_simulacao (placa, data_posicao DESC);")

# Colunas adicionais em embarques_cargas
cur.execute("ALTER TABLE embarques_cargas ADD COLUMN IF NOT EXISTS origem_latitude NUMERIC(10,7);")
cur.execute("ALTER TABLE embarques_cargas ADD COLUMN IF NOT EXISTS origem_longitude NUMERIC(10,7);")
cur.execute("ALTER TABLE embarques_cargas ADD COLUMN IF NOT EXISTS data_saida_real TIMESTAMP;")
cur.execute("ALTER TABLE embarques_cargas ADD COLUMN IF NOT EXISTS saida_auto BOOLEAN DEFAULT FALSE;")
cur.execute("ALTER TABLE embarques_cargas ADD COLUMN IF NOT EXISTS no_local_desde TIMESTAMP;")
cur.execute("ALTER TABLE embarques_cargas ADD COLUMN IF NOT EXISTS entregue_auto BOOLEAN DEFAULT FALSE;")
cur.execute("ALTER TABLE embarques_cargas ADD COLUMN IF NOT EXISTS rota_planejada_polyline TEXT;")
cur.execute("ALTER TABLE embarques_cargas ADD COLUMN IF NOT EXISTS distancia_planejada_km NUMERIC(7,1);")
cur.execute("ALTER TABLE embarques_cargas ADD COLUMN IF NOT EXISTS duracao_estimada_min INTEGER;")
cur.execute("ALTER TABLE embarques_cargas ADD COLUMN IF NOT EXISTS rota_recalculada_em TIMESTAMP;")

# Colunas adicionais em embarques_cargas_destinos
cur.execute("ALTER TABLE embarques_cargas_destinos ADD COLUMN IF NOT EXISTS latitude NUMERIC(10,7);")
cur.execute("ALTER TABLE embarques_cargas_destinos ADD COLUMN IF NOT EXISTS longitude NUMERIC(10,7);")

print("✅ Tabelas de Rastreamento (token, veiculos_rastreio, posicoes, kpi, municipios, log, simulacao) prontas.\n")

# ════════════════════════════════════════════════════════════════════════════
# AGENDAMENTO POR DESTINO (Fase 3) — compromisso de prazo + entrega por destino
# ════════════════════════════════════════════════════════════════════════════

# Compromisso firmado com o cliente recebedor (TIMESTAMP naive em UTC)
cur.execute("ALTER TABLE embarques_cargas_destinos ADD COLUMN IF NOT EXISTS data_agendamento TIMESTAMP;")
# Marcação manual de entrega por destino (Sprint 4 — opcional, DDL antecipada)
cur.execute("ALTER TABLE embarques_cargas_destinos ADD COLUMN IF NOT EXISTS entregue_em TIMESTAMP;")
cur.execute("ALTER TABLE embarques_cargas_destinos ADD COLUMN IF NOT EXISTS entregue_por_id INTEGER REFERENCES auditoria_users(id);")
cur.execute("ALTER TABLE embarques_cargas_destinos ADD COLUMN IF NOT EXISTS entregue_por_nome VARCHAR(180);")

print("✅ Colunas de agendamento por destino (data_agendamento, entregue_em) prontas.\n")

conn.commit()
cur.close()
conn.close()
print("Banco de dados pronto. Rode: python server.py")
