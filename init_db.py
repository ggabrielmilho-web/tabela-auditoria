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

conn.commit()
cur.close()
conn.close()
print("Banco de dados pronto. Rode: python server.py")
