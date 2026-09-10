# -*- coding: utf-8 -*-
"""
Estado dos envios para a Verda — tabela `verda_envios` no Postgres.

Existe por três razões:

  1. **Idempotência.** Reenviar a mesma viagem duplicaria a emissão no inventário.
     A chave é o `TransportationId` (o manifesto), e o `hash_payload` diz se o
     conteúdo mudou desde o último envio.
  2. **Conferência.** O POST devolve só `Success`/`TransactionId`; se a viagem foi
     mesmo processada ou foi rejeitada só se descobre depois, na `GetTransaction`.
     Sem guardar o `TransactionId` não há como perguntar.
  3. **Cancelamento.** A `CancelTransaction` exige `TransactionId` **e**
     `TransportationId` (pg. 69) — os dois precisam estar gravados.
  4. **Separação de ambiente.** A idempotência é por (viagem, ambiente). Sem isso,
     as viagens exercitadas no simulador contariam como já enviadas e NUNCA
     chegariam à Verda de verdade — o robô diria "inalterada" e pularia. O mesmo
     valeria de homologação para produção. O `transaction_id` também só vale
     dentro do ambiente que o emitiu.

Ciclo de vida:

    pendente ──envio──> enviado ──conferência──> executed
                           │                  └─> rejected  (erro_detalhe diz o campo)
                           └──falha──> erro    (tentativas++, volta na próxima rodada)

    viagem que mudou de conteúdo: cancela a transação antiga e volta para pendente
"""

import hashlib
import json
import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

STATUS_PENDENTE = 'pendente'
STATUS_ENVIADO = 'enviado'
STATUS_ERRO = 'erro'
STATUS_BLOQUEADO = 'bloqueado'   # reprovado na validação local — nunca chega a sair
STATUS_FORA_ESCOPO = 'fora_escopo'   # deixou de ser transporte para o inventário
# executed / rejected / canceled vêm da própria Verda (pg. 76)

DDL = """
CREATE TABLE IF NOT EXISTS verda_envios (
    transportation_id  TEXT NOT NULL,
    data_viagem        DATE,
    ambiente           TEXT NOT NULL DEFAULT 'simulado',
    api                TEXT,
    payload            JSONB,
    hash_payload       TEXT,
    transaction_id     TEXT,
    status             TEXT NOT NULL DEFAULT 'pendente',
    mensagem           TEXT,
    erro_detalhe       JSONB,
    avisos             JSONB,
    tentativas         INTEGER NOT NULL DEFAULT 0,
    enviado_em         TIMESTAMP,
    conferido_em       TIMESTAMP,
    criado_em          TIMESTAMP NOT NULL DEFAULT NOW(),
    atualizado_em      TIMESTAMP NOT NULL DEFAULT NOW(),
    PRIMARY KEY (transportation_id, ambiente)
);
CREATE INDEX IF NOT EXISTS ix_verda_envios_status ON verda_envios (status);
CREATE INDEX IF NOT EXISTS ix_verda_envios_data   ON verda_envios (data_viagem);
CREATE INDEX IF NOT EXISTS ix_verda_envios_trans  ON verda_envios (transaction_id);
ALTER TABLE verda_envios ADD COLUMN IF NOT EXISTS ambiente TEXT NOT NULL DEFAULT 'simulado';
-- A identidade é (viagem, ambiente). Com a PK só em transportation_id, rodar em
-- modo simulado SOBRESCREVIA a linha de homologação e apagava o transaction_id
-- real — aconteceu em 01/09/2026 com 20 viagens, que ficaram no inventário da
-- Verda sem como cancelar. As linhas dos ambientes precisam coexistir.
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'verda_envios_pkey'
               AND (SELECT COUNT(*) FROM unnest(conkey)) = 1) THEN
        ALTER TABLE verda_envios DROP CONSTRAINT verda_envios_pkey;
        ALTER TABLE verda_envios ADD PRIMARY KEY (transportation_id, ambiente);
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS ix_verda_envios_amb    ON verda_envios (ambiente);
"""


def conectar():
    return psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'), port=os.getenv('DB_PORT', '5432'),
        dbname=os.getenv('DB_NAME', 'postgres'), user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD', ''))


def garantir_tabela(con=None):
    fechar = con is None
    con = con or conectar()
    with con.cursor() as cur:
        cur.execute(DDL)
    con.commit()
    if fechar:
        con.close()


def hash_payload(payload):
    """Impressão digital do conteúdo, ignorando o que muda a cada montagem.

    `LocalDateTime` é a hora da chamada — se entrasse no hash, toda viagem
    pareceria alterada a cada execução e o robô reenviaria tudo todo dia.
    """
    limpo = {k: v for k, v in (payload or {}).items() if k != 'LocalDateTime'}
    bruto = json.dumps(limpo, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(bruto.encode()).hexdigest()


# ════════════════════════════════════════
# ESCRITA
# ════════════════════════════════════════

def registrar(con, transportation_id, data_viagem, api, payload, avisos=None,
              ambiente='simulado'):
    """Grava (ou atualiza) uma viagem a enviar.

    Retorna o que fazer com ela:
      'nova'       nunca foi enviada NESTE ambiente
      'alterada'   o conteúdo mudou depois de enviada — precisa cancelar e reenviar
      'inalterada' já está lá com o mesmo conteúdo e no mesmo ambiente — não mexe

    A comparação é sempre dentro do ambiente. Viagem que rodou no simulador (ou em
    homologação) volta a ser 'nova' em produção, e o `transaction_id` antigo é
    descartado junto — id de outro ambiente não serve nem para conferir nem para
    cancelar.
    """
    h = hash_payload(payload)
    with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # o SELECT PRECISA filtrar por ambiente: sem isso, a linha de outro
        # ambiente decidiria o destino desta.
        cur.execute('SELECT hash_payload, status FROM verda_envios '
                    'WHERE transportation_id = %s AND ambiente = %s',
                    (transportation_id, ambiente))
        atual = cur.fetchone()

        if atual and atual['hash_payload'] == h and atual['status'] not in (STATUS_ERRO,):
            return 'inalterada'

        alterada = bool(atual and atual['hash_payload'] != h
                        and atual['status'] not in (STATUS_PENDENTE, STATUS_ERRO))
        cur.execute("""
            INSERT INTO verda_envios (transportation_id, data_viagem, ambiente, api, payload,
                                      hash_payload, avisos, status, atualizado_em)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (transportation_id, ambiente) DO UPDATE SET
                data_viagem = EXCLUDED.data_viagem, api = EXCLUDED.api,
                payload = EXCLUDED.payload, hash_payload = EXCLUDED.hash_payload,
                avisos = EXCLUDED.avisos, status = %s, mensagem = NULL,
                erro_detalhe = NULL, atualizado_em = NOW()
        """, (transportation_id, data_viagem, ambiente, api, json.dumps(payload, default=str), h,
              json.dumps(avisos or [], ensure_ascii=False), STATUS_PENDENTE, STATUS_PENDENTE))
    return 'alterada' if alterada else 'nova'


def marcar_enviado(con, transportation_id, transaction_id, mensagem='',
                   ambiente='simulado'):
    """Grava o `transaction_id` devolvido pelo POST.

    O `ambiente` no WHERE não é detalhe: a identidade da linha é
    (transportation_id, ambiente), e sem ele um envio de produção carimbaria seu
    id em cima da linha de homologação da mesma viagem. Isso deixaria a
    transação de homologação órfã — viva lá dentro e sem como cancelar — e a
    linha de teste apontando para uma transação de outra conta. É a mesma lição
    da PK composta, que só tinha sido aplicada no `registrar`.
    """
    with con.cursor() as cur:
        cur.execute("""UPDATE verda_envios SET status = %s, transaction_id = %s,
                       mensagem = %s, tentativas = tentativas + 1,
                       enviado_em = NOW(), atualizado_em = NOW()
                       WHERE transportation_id = %s AND ambiente = %s""",
                    (STATUS_ENVIADO, transaction_id, mensagem[:500],
                     transportation_id, ambiente))
        return cur.rowcount


def marcar_erro(con, transportation_id, mensagem, erros=None, ambiente='simulado'):
    with con.cursor() as cur:
        cur.execute("""UPDATE verda_envios SET status = %s, mensagem = %s,
                       erro_detalhe = %s, tentativas = tentativas + 1, atualizado_em = NOW()
                       WHERE transportation_id = %s AND ambiente = %s""",
                    (STATUS_ERRO, str(mensagem)[:500],
                     json.dumps(erros or [], ensure_ascii=False),
                     transportation_id, ambiente))
        return cur.rowcount


def marcar_status(con, transaction_id, status, mensagem='', erros=None):
    """Aplica o resultado da conferência (`GetTransaction`)."""
    with con.cursor() as cur:
        cur.execute("""UPDATE verda_envios SET status = %s, mensagem = %s, erro_detalhe = %s,
                       conferido_em = NOW(), atualizado_em = NOW()
                       WHERE transaction_id = %s""",
                    (status, str(mensagem)[:500], json.dumps(erros or [], ensure_ascii=False),
                     transaction_id))
        return cur.rowcount


# ════════════════════════════════════════
# LEITURA
# ════════════════════════════════════════

def bloquear(con, transportation_id, data_viagem, api, payload, problemas,
             ambiente='simulado'):
    """Reprovado na validação local: fica registrado, mas nunca é enviado.

    Melhor gastar uma linha de tabela do que uma transação na Verda — payload
    inválido vira `rejected` lá dentro e polui o inventário com ruído.

    O `WHERE` protege o que JÁ SAIU: viagem com veredito da Verda não é rebaixada
    para `bloqueado`, senão se perde o rastro do que foi enviado. Mas a proteção
    vale só dentro do ambiente — linha de outro ambiente (o simulador, por
    exemplo) é sobrescrita, porque ali o veredito não vale nada aqui.
    """
    with con.cursor() as cur:
        cur.execute("""
            INSERT INTO verda_envios (transportation_id, data_viagem, ambiente, api, payload,
                                      hash_payload, status, mensagem, erro_detalhe, atualizado_em)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (transportation_id, ambiente) DO UPDATE SET
                data_viagem = EXCLUDED.data_viagem, ambiente = EXCLUDED.ambiente,
                api = EXCLUDED.api,
                payload = EXCLUDED.payload, hash_payload = EXCLUDED.hash_payload,
                status = EXCLUDED.status, mensagem = EXCLUDED.mensagem,
                erro_detalhe = EXCLUDED.erro_detalhe, atualizado_em = NOW()
            WHERE verda_envios.status IN (%s, %s, %s)
        """, (transportation_id, data_viagem, ambiente, api, json.dumps(payload, default=str),
              hash_payload(payload), STATUS_BLOQUEADO, problemas[0][:500],
              json.dumps([{'problema': x} for x in problemas], ensure_ascii=False),
              STATUS_PENDENTE, STATUS_ERRO, STATUS_BLOQUEADO))


def fora_de_escopo(con, transportation_id, motivo, ambiente='simulado'):
    """Viagem que saiu do inventário por regra de negócio, não por erro.

    Acontece quando uma regra nova reclassifica algo que já foi enviado — o caso
    real: os lançamentos administrativos, identificados depois que 597 viagens já
    estavam na Verda. Marcar não basta: se a transação continuar viva lá, o
    inventário segue contando emissão que decidimos não reconhecer.

    Devolve o `transaction_id` a cancelar (ou None se nunca chegou a sair).
    """
    with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT transaction_id, status FROM verda_envios
                       WHERE transportation_id = %s AND ambiente = %s""",
                    (transportation_id, ambiente))
        r = cur.fetchone()
        if not r:
            return None
        cur.execute("""UPDATE verda_envios SET status = %s, mensagem = %s, atualizado_em = NOW()
                       WHERE transportation_id = %s AND ambiente = %s""",
                    (STATUS_FORA_ESCOPO, str(motivo)[:500], transportation_id, ambiente))
        # só há o que cancelar se a transação chegou a existir na Verda
        return r['transaction_id'] if r['status'] in (STATUS_ENVIADO, 'executed') else None


def limpar_cancelada(con, transportation_id, ambiente='simulado'):
    """Cancelamento confirmado: apaga o vínculo com a transação que não existe mais."""
    with con.cursor() as cur:
        cur.execute("""UPDATE verda_envios SET transaction_id = NULL, atualizado_em = NOW()
                       WHERE transportation_id = %s AND ambiente = %s""",
                    (transportation_id, ambiente))


def media_litros_enviados(con, ambiente='simulado'):
    """Consumo médio do que já foi aceito — linha de base do freio de anomalia.

    Só conta o ambiente atual: o histórico do simulador não é referência para o
    que a Verda aceitou de verdade. `NULL` quando ainda não há histórico."""
    with con.cursor() as cur:
        cur.execute("""
            SELECT AVG((payload->>'FuelConsumption')::numeric) FILTER (WHERE api = 'Fuel'),
                   COUNT(*) FILTER (WHERE api = 'Fuel')
            FROM verda_envios WHERE status IN ('enviado', 'executed') AND ambiente = %s
        """, (ambiente,))
        media, n = cur.fetchone()
        return (float(media), n) if media and n >= 30 else (None, n or 0)


def a_enviar(con, limite=None, max_tentativas=5, ambiente='simulado'):
    """Pendentes e as que erraram e ainda têm tentativa sobrando, deste ambiente.

    Traz o `transaction_id` junto porque uma viagem na fila pode ainda ter
    transação VIVA na Verda — acontece sempre que a montagem e o envio ocorrem em
    execuções diferentes (`--so-montar` e depois o envio). Enviar por cima é
    recusado: a Verda não aceita `TransportationId` que já tenha transação ativa.
    """
    with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT transportation_id, api, payload, transaction_id FROM verda_envios
                       WHERE status IN (%s, %s) AND tentativas < %s AND ambiente = %s
                       ORDER BY data_viagem, transportation_id {limite}""".format(
                        limite='LIMIT %d' % limite if limite else ''),
                    (STATUS_PENDENTE, STATUS_ERRO, max_tentativas, ambiente))
        return cur.fetchall()


def a_conferir(con, limite=None, ambiente='simulado'):
    """Enviadas deste ambiente que ainda não têm veredito final da Verda.

    O filtro de ambiente é o que impede perguntar à API real por um
    `transaction_id` que o simulador inventou.
    """
    with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT transportation_id, transaction_id FROM verda_envios
                       WHERE status = %s AND transaction_id IS NOT NULL AND ambiente = %s
                       ORDER BY enviado_em {limite}""".format(
                        limite='LIMIT %d' % limite if limite else ''),
                    (STATUS_ENVIADO, ambiente))
        return cur.fetchall()


def para_cancelar(con, transportation_id, ambiente='simulado'):
    """Transação anterior de uma viagem que mudou de conteúdo, neste ambiente.

    O filtro importa: um `transaction_id` de homologação não existe em produção,
    e mandar cancelá-lo lá só produziria erro.
    """
    with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute('SELECT transaction_id FROM verda_envios WHERE transportation_id = %s '
                    'AND transaction_id IS NOT NULL AND ambiente = %s',
                    (transportation_id, ambiente))
        r = cur.fetchone()
        return r['transaction_id'] if r else None


def resumo(con, desde=None, ambiente=None):
    """Contagem por ambiente e status, para a tela e para o log do job.

    Sem `ambiente`, mostra todos — é assim que se enxerga que 600 `executed` são
    do simulador e não da Verda.
    """
    with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT ambiente, status, COUNT(*) AS n FROM verda_envios
                       WHERE (%s IS NULL OR data_viagem >= %s)
                         AND (%s IS NULL OR ambiente = %s)
                       GROUP BY ambiente, status ORDER BY ambiente, n DESC""",
                    (desde, desde, ambiente, ambiente))
        return cur.fetchall()


def rejeitadas(con, limite=50, ambiente=None):
    """As que a Verda recusou, com o campo que causou — é o que o operador lê."""
    with con.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT transportation_id, data_viagem, ambiente, api, mensagem,
                              erro_detalhe
                       FROM verda_envios WHERE status = 'rejected'
                         AND (%s IS NULL OR ambiente = %s)
                       ORDER BY data_viagem DESC LIMIT %s""", (ambiente, ambiente, limite))
        return cur.fetchall()
