"""Códigos SMS do SSW (24/11/2026).

A partir de 25/11/2026 o SSW troca o reCAPTCHA do login por um código enviado por SMS ao
celular do usuário (opção 925), que libera o IP. O celular cadastrado no gfranca é o de
automação, na Rizza: o app "SMS to URL Forwarder" encaminha cada SMS para cá, e:

  - o robô do SLTBL-10 (Rizza/ssw_sessao.py) busca o código que chegou depois do login dele;
  - o Gabriel, entrando no SSW de fora da empresa, lê o código na tela "Códigos SSW".

Duas chaves, cada uma com um só poder (o repositório é público: as chaves ficam só no
ambiente do serviço, no celular e no .env do SLTBL-10):
  SSW_SMS_CHAVE   o celular  — só GRAVA SMS
  SSW_ROBO_CHAVE  o robô     — só LÊ o código mais recente
A tela é só de admin. O SMS é apagado depois de GUARDAR_DIAS; a tela mostra só os recentes.
"""

import hmac
import os
import re

GUARDAR_DIAS = 7          # depois disso o SMS é apagado
TELA_MINUTOS = 30         # a tela mostra os desta janela
MAX_TEXTO = 1000
MAX_ROBO_SEGUNDOS = 900   # o robô pergunta "chegou algo nos últimos N s?"; teto do N

DDL = """
CREATE TABLE IF NOT EXISTS ssw_sms (
    id            serial PRIMARY KEY,
    recebido_em   timestamptz NOT NULL DEFAULT now(),
    de            text,
    texto         text NOT NULL,
    codigo        text,
    origem        text NOT NULL DEFAULT 'celular',
    informado_por text,
    usado_em      timestamptz,
    usado_por     text
);
CREATE INDEX IF NOT EXISTS ssw_sms_recebido_em ON ssw_sms (recebido_em DESC);
"""

# Código = um grupo de 4 a 8 dígitos sozinho no texto. O IP (até 3 dígitos por parte), a data
# (dd/mm/aa) e a hora (hh:mm) não casam. O texto inteiro fica guardado para a pessoa conferir.
_RE_CODIGO = re.compile(r"(?<![\d.])(\d{4,8})(?![\d.])")


def garantir(cur):
    cur.execute(DDL)


def _chave_ok(recebida, variavel):
    esperada = os.getenv(variavel) or ""
    return len(esperada) >= 32 and hmac.compare_digest(str(recebida or ""), esperada)


def chave_celular_ok(recebida):
    return _chave_ok(recebida, "SSW_SMS_CHAVE")


def chave_robo_ok(recebida):
    return _chave_ok(recebida, "SSW_ROBO_CHAVE")


def extrair_codigo(texto):
    achados = _RE_CODIGO.findall(str(texto or ""))
    return achados[0] if achados else None


def _limpar(cur):
    cur.execute("DELETE FROM ssw_sms WHERE recebido_em < now() - %s * interval '1 day'", (GUARDAR_DIAS,))


def registrar(cur, texto, de=None, origem="celular", informado_por=None):
    """Grava um SMS (do celular) ou um código digitado na tela. Devolve (id, codigo)."""
    texto = str(texto or "").strip()[:MAX_TEXTO]
    if not texto:
        raise ValueError("SMS sem texto")
    codigo = extrair_codigo(texto)
    _limpar(cur)
    cur.execute(
        "INSERT INTO ssw_sms (de, texto, codigo, origem, informado_por) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (str(de or "")[:60] or None, texto, codigo, origem, informado_por))
    return cur.fetchone()[0], codigo


def recentes(cur, minutos=TELA_MINUTOS):
    cur.execute(
        """SELECT id, recebido_em, de, texto, codigo, origem, informado_por, usado_em, usado_por,
                  EXTRACT(EPOCH FROM now() - recebido_em)::int
             FROM ssw_sms
            WHERE recebido_em > now() - %s * interval '1 minute'
            ORDER BY recebido_em DESC, id DESC
            LIMIT 30""", (minutos,))
    return [{"id": r[0], "recebido_em": r[1].isoformat(), "de": r[2], "texto": r[3], "codigo": r[4],
             "origem": r[5], "informado_por": r[6], "usado_em": r[7].isoformat() if r[7] else None,
             "usado_por": r[8], "ha_segundos": r[9]} for r in cur.fetchall()]


def codigo_para_robo(cur, segundos, robo="robô"):
    """O código mais recente que chegou nos últimos `segundos` e ainda não foi usado.

    O robô conta os segundos desde que clicou em "entrar"; a conta do tempo é toda do lado
    do painel (o relógio do SLTBL-10 não precisa bater com o daqui). Marca como usado.
    """
    segundos = max(1, min(int(segundos), MAX_ROBO_SEGUNDOS))
    cur.execute(
        """SELECT id, codigo, recebido_em FROM ssw_sms
            WHERE codigo IS NOT NULL AND usado_em IS NULL
              AND recebido_em > now() - %s * interval '1 second'
            ORDER BY recebido_em DESC, id DESC
            LIMIT 1
              FOR UPDATE""", (segundos,))
    r = cur.fetchone()
    if not r:
        return None
    cur.execute("UPDATE ssw_sms SET usado_em = now(), usado_por = %s WHERE id = %s", (str(robo)[:60], r[0]))
    return {"id": r[0], "codigo": r[1], "recebido_em": r[2].isoformat()}
