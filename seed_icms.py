"""
Cria e popula a matriz de ICMS de TRANSPORTE DE CARGAS (origem UF x destino UF).
Rode UMA VEZ (idempotente — pode rodar de novo sem duplicar):  python seed_icms.py

Consulta de uso:
    SELECT aliquota, isento, observacao
    FROM icms_aliquota WHERE uf_origem = 'MG' AND uf_destino = 'MG';

═══════════════════════════════════════════════════════════════════════════════
CONFIABILIDADE DOS VALORES
───────────────────────────────────────────────────────────────────────────────
• INTERESTADUAL (origem != destino): 7% ou 12%.
  Regra NACIONAL fixada pela Resolução do Senado 22/1989 — NÃO muda por estado.
  Gerada 100% por código abaixo. Pode confiar.

• INTERNA (origem == destino): alíquota interna de TRANSPORTE por UF.
  Fonte: Paulicon Contábil (Inf. Fiscal 169, jan/2025) + idealsoftwares (batem 100%).
  >>> Confirme com o contábil antes de produção; alíquotas mudam por estado. <<<

• ISENÇÕES CONDICIONAIS (campo isento + observacao):
  Alguns estados isentam o transporte interno conforme o tomador. Ex.: MG.
═══════════════════════════════════════════════════════════════════════════════
"""

import os
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# REGRA INTERESTADUAL (nacional, exata)
# ─────────────────────────────────────────────────────────────────────────────
# Estados do Sul/Sudeste (exceto ES). Quando a ORIGEM é um destes e o DESTINO
# está fora deste grupo -> 7%. Caso contrário (entre eles, ou origem fora) -> 12%.
SUL_SUDESTE_SEM_ES = {"SP", "RJ", "MG", "PR", "SC", "RS"}

UFS = [
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA",
    "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN",
    "RO", "RR", "RS", "SC", "SE", "SP", "TO",
]

# ─────────────────────────────────────────────────────────────────────────────
# ALÍQUOTAS INTERNAS DE TRANSPORTE DE CARGAS (não é a alíquota de mercadoria!)
# Fonte: TOTVS — Tabela ICMS Transporte 2026 (matriz oficial), conforme imagem.
#   https://www.totvs.com/blog/gestao-logistica/tabela-icms-transporte/
# FCP (Fundo de Combate à Pobreza) adicional não incluso: AL +1%, RJ +2%.
# ─────────────────────────────────────────────────────────────────────────────
ALIQUOTA_INTERNA = {
    "AC": 19.0, "AL": 20.0, "AP": 18.0, "AM": 20.0, "BA": 20.5,
    "CE": 20.0, "DF": 20.0, "ES": 17.0, "GO": 19.0, "MA": 23.0,
    "MG": 18.0, "MS": 17.0, "MT": 17.0, "PA": 19.0, "PB": 20.0,
    "PE": 20.5, "PI": 22.5, "PR": 19.5, "RJ": 22.0, "RN": 20.0,
    "RO": 19.5, "RR": 20.0, "RS": 17.0, "SC": 17.0, "SE": 20.0,
    "SP": 18.0, "TO": 20.0,
}

# ─────────────────────────────────────────────────────────────────────────────
# ISENÇÕES CONDICIONAIS NO TRANSPORTE INTERNO (origem == destino)
# Quando uma UF aparece aqui, o par interno é gravado como ISENTO (aliquota 0)
# e a 'observacao' explica a regra para o front. A alíquota cheia (caso a
# isenção não se aplique) fica em 'aliquota_excecao'.
# ─────────────────────────────────────────────────────────────────────────────
ISENCAO_INTERNA = {
    "MG": {
        "aliquota_excecao": 18.0,
        "observacao": "Isento quando origem/tomador em MG · Demais casos: 18%",
    },
}


def regra(origem: str, destino: str) -> tuple[float, str, bool, str | None]:
    """Retorna (aliquota, tipo, isento, observacao) para um par de UFs."""
    if origem == destino:
        if origem in ISENCAO_INTERNA:
            info = ISENCAO_INTERNA[origem]
            return 0.0, "interna", True, info["observacao"]
        return ALIQUOTA_INTERNA[origem], "interna", False, None
    if origem in SUL_SUDESTE_SEM_ES and destino not in SUL_SUDESTE_SEM_ES:
        return 7.0, "interestadual", False, None
    return 12.0, "interestadual", False, None


def gerar_matriz() -> list[tuple[str, str, float, str, bool, str | None]]:
    """729 combinações (27 x 27)."""
    linhas = []
    for o in UFS:
        for d in UFS:
            aliq, tipo, isento, obs = regra(o, d)
            linhas.append((o, d, aliq, tipo, isento, obs))
    return linhas


def main() -> None:
    conn = psycopg2.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME", "postgres"),
        user=os.getenv("DB_USER", "postgres"),
        password=os.getenv("DB_PASSWORD", ""),
    )
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS icms_aliquota (
            uf_origem     CHAR(2)      NOT NULL,
            uf_destino    CHAR(2)      NOT NULL,
            aliquota      NUMERIC(5,2) NOT NULL,
            tipo          VARCHAR(20)  NOT NULL,
            isento        BOOLEAN      NOT NULL DEFAULT FALSE,
            observacao    TEXT,
            atualizado_em TIMESTAMP    DEFAULT NOW(),
            PRIMARY KEY (uf_origem, uf_destino)
        );
    """)
    # Colunas novas (idempotente, caso a tabela já existisse sem elas)
    cur.execute("ALTER TABLE icms_aliquota ADD COLUMN IF NOT EXISTS isento BOOLEAN NOT NULL DEFAULT FALSE;")
    cur.execute("ALTER TABLE icms_aliquota ADD COLUMN IF NOT EXISTS observacao TEXT;")

    linhas = gerar_matriz()
    execute_values(
        cur,
        """
        INSERT INTO icms_aliquota (uf_origem, uf_destino, aliquota, tipo, isento, observacao)
        VALUES %s
        ON CONFLICT (uf_origem, uf_destino) DO UPDATE
            SET aliquota      = EXCLUDED.aliquota,
                tipo          = EXCLUDED.tipo,
                isento        = EXCLUDED.isento,
                observacao    = EXCLUDED.observacao,
                atualizado_em = NOW();
        """,
        linhas,
    )

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM icms_aliquota;")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM icms_aliquota WHERE isento;")
    isentos = cur.fetchone()[0]
    print(f"[OK] Matriz ICMS transporte pronta: {total} pares ({isentos} isentos) em 'icms_aliquota'.")
    print("   Exemplos:")
    for o, d in [("MG", "MG"), ("SP", "SP"), ("SP", "BA"), ("SP", "RJ"), ("BA", "MG")]:
        cur.execute(
            "SELECT aliquota, tipo, isento, observacao FROM icms_aliquota WHERE uf_origem=%s AND uf_destino=%s",
            (o, d),
        )
        aliq, tipo, isento, obs = cur.fetchone()
        rotulo = "ISENTO" if isento else f"{aliq:.2f}%"
        extra = f"  | {obs}" if obs else ""
        print(f"   {o} -> {d}: {rotulo:8} ({tipo}){extra}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
