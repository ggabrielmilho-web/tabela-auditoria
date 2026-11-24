"""Admissões do RH: o cadastro do funcionário nasce do PDF do Onvio e robôs o replicam nos benefícios.

Fluxo (combinado com o Gabriel em 23–24/11/2026):
  1. a RH exporta do Onvio o "Relatório de cadastro de empregados na folha" (PDF) e sobe aqui;
  2. o painel lê o PDF (rh_onvio.py), ela confere/completa os dados e marca "vai aderir" por benefício;
  3. "Enviar para os robôs" grava uma tarefa por benefício em `rh_tarefa`;
  4. o worker do SLTBL-10 (RH/rh_worker.py, fora deste repositório) puxa a fila por HTTP — só conexão de
     saída, sem túnel — roda o robô de cada portal e devolve situação, mensagem e print.

Decisões da RH / do Gabriel que moram aqui:
  - adesão por benefício, tudo ou nada: quem adere à Hapvida ou à Odonto leva TODOS os dependentes;
  - todo dependente é tratado como novo: parentesco, nascimento, sexo, mãe e estado civil vêm do painel
    (o Onvio só traz nome + CPF);
  - inclusão no plano (Hapvida e Odonto): até o dia 19 → 1º do mês seguinte; depois → 1º do outro mês;
  - plano odonto pelo nº de dependentes: 0 → 9, 1 → 56, 2+ → 57;
  - a RH marcou "vai aderir" → o robô envia direto (sem segunda conferência);
  - Flash vai primeiro na fila: pede o código do SMS no celular da RH, que digita aqui;
  - Flash: celular só quando não há e-mail (o convite vai por SMS).
  - dado errado: a RH corrige aqui (o campo fica marcado "editado") OU corrige no Onvio e sobe o PDF de novo.

Repositório PÚBLICO: nada de CPF, nome ou dado real em código, comentário ou teste.
"""
import base64
import hmac
import os
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone

# ── benefícios (ordem = ordem da fila) ──
SISTEMAS = [
    ("flash", "Flash", "Alimentação — convite de primeiro acesso"),
    ("rhid", "Ponto (RHID)", "Cadastro no relógio de ponto"),
    ("hapvida", "Hapvida", "Plano de saúde — titular e dependentes"),
    ("odonto", "Odonto", "Uniodonto/Odontosfera — titular e dependentes"),
]
ORDEM = {k: i for i, (k, _, _) in enumerate(SISTEMAS)}
NOME_SISTEMA = {k: n for k, n, _ in SISTEMAS}

# estados de uma tarefa
ESTADOS = {
    "fila": "Na fila",
    "rodando": "Rodando",
    "aguardando_codigo": "Aguardando o código do SMS",
    "feito": "Feito",
    "conferir": "Feito — conferir no portal",
    "erro": "Erro",
    "cancelada": "Cancelada",
}
ATIVOS = {"fila", "rodando", "aguardando_codigo"}
# o robô que some (Chrome travado, servidor reiniciado) sem devolver nada solta a tarefa depois disto
RODANDO_MAX = timedelta(minutes=40)
CODIGO_MAX = timedelta(minutes=12)

DIA_CORTE = 19
PARENTESCOS = [("conjuge", "Cônjuge"), ("companheiro", "Companheiro"), ("companheira", "Companheira"),
               ("filho", "Filho"), ("filha", "Filha"), ("enteado", "Enteado"), ("enteada", "Enteada"),
               ("pai", "Pai"), ("mae", "Mãe")]
ESTADOS_CIVIS = [("solteiro", "Solteiro(a)"), ("casado", "Casado(a)"), ("uniao_estavel", "União estável"),
                 ("divorciado", "Divorciado(a)"), ("separado", "Separado(a)"), ("viuvo", "Viúvo(a)")]
DEPENDENTE_OBRIGATORIOS = [("nome", "nome"), ("cpf", "CPF"), ("parentesco", "parentesco"),
                           ("data_nascimento", "nascimento"), ("sexo", "sexo"), ("nome_mae", "mãe"),
                           ("estado_civil", "estado civil")]

# campos do titular que o painel mostra/edita, por bloco: (chave, rótulo)
BLOCOS = [
    ("Dados pessoais", [("nome", "Nome"), ("cpf", "CPF"), ("data_nascimento", "Nascimento"), ("sexo", "Sexo"),
                        ("estado_civil", "Estado civil"), ("nome_mae", "Nome da mãe"), ("nome_pai", "Nome do pai"),
                        ("rg", "RG"), ("rg_orgao", "Órgão emissor"), ("rg_uf", "UF do RG")]),
    ("Contrato", [("matricula", "Matrícula"), ("cargo", "Cargo"), ("data_admissao", "Admissão"),
                  ("pis", "PIS"), ("ctps", "CTPS"), ("ctps_serie", "Série CTPS")]),
    ("Endereço e contato", [("cep", "CEP"), ("tipo_logradouro", "Tipo"), ("logradouro", "Logradouro"),
                            ("numero", "Número"), ("complemento", "Complemento"), ("bairro", "Bairro"),
                            ("cidade", "Cidade"), ("uf", "UF"), ("email", "E-mail"), ("celular", "Celular"),
                            ("telefone", "Telefone fixo")]),
]
CAMPOS = [k for _, cs in BLOCOS for k, _ in cs]
MAX_PDF = 8 * 1024 * 1024
MAX_PRINT = 6 * 1024 * 1024

DDL = """
CREATE TABLE IF NOT EXISTS rh_admissao (
    id            SERIAL PRIMARY KEY,
    cpf           TEXT NOT NULL,
    nome          TEXT,
    dados         JSONB NOT NULL,            -- o funcionário como os robôs leem (titular + dependentes)
    onvio         JSONB,                     -- o que veio do PDF, para marcar o que a RH editou
    beneficios    JSONB NOT NULL DEFAULT '{}',   -- {"flash": {"adere": true}, "rhid": {"adere": true, "horario": "…"}}
    arquivo       TEXT,                      -- nome do PDF que subiu (o PDF não é guardado)
    criado_em     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    criado_por    TEXT,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_por TEXT,
    enviado_em    TIMESTAMPTZ,
    enviado_por   TEXT
);
CREATE INDEX IF NOT EXISTS rh_admissao_cpf ON rh_admissao (cpf);
CREATE TABLE IF NOT EXISTS rh_tarefa (
    id            SERIAL PRIMARY KEY,
    admissao_id   INTEGER NOT NULL REFERENCES rh_admissao (id) ON DELETE CASCADE,
    sistema       TEXT NOT NULL,             -- flash | rhid | hapvida | odonto
    ordem         INTEGER NOT NULL,
    estado        TEXT NOT NULL DEFAULT 'fila',
    tentativas    INTEGER NOT NULL DEFAULT 0,
    mensagem      TEXT,
    detalhe       JSONB,                     -- o que o robô devolveu (divergências portal × Onvio, nº de controle…)
    print_png     BYTEA,                     -- a última tela do robô
    codigo        TEXT,                      -- código do SMS digitado pela RH (Flash); apagado quando o robô lê
    codigo_pedido_em TIMESTAMPTZ,
    criado_em     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    pego_em       TIMESTAMPTZ,
    terminado_em  TIMESTAMPTZ,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (admissao_id, sistema)
);
CREATE INDEX IF NOT EXISTS rh_tarefa_estado ON rh_tarefa (estado, admissao_id, ordem);
CREATE TABLE IF NOT EXISTS rh_tarefa_log (
    id          SERIAL PRIMARY KEY,
    tarefa_id   INTEGER NOT NULL REFERENCES rh_tarefa (id) ON DELETE CASCADE,
    em          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    estado      TEXT,
    mensagem    TEXT,
    quem        TEXT
);
CREATE TABLE IF NOT EXISTS rh_config (
    chave       TEXT PRIMARY KEY,            -- worker_visto | horarios_rhid
    valor       JSONB,
    em          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def garantir(cur):
    cur.execute(DDL)


# ─────────────────────────────────────────────────────────────── regras puras
def norm(t) -> str:
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", t).strip()


def dig(t) -> str:
    return re.sub(r"\D", "", str(t or ""))


def cpf_valido(c) -> bool:
    c = dig(c)
    if len(c) != 11 or c == c[0] * 11:
        return False
    for n in (9, 10):
        s = sum(int(c[i]) * (n + 1 - i) for i in range(n))
        if (s * 10 % 11) % 10 != int(c[n]):
            return False
    return True


def pis_valido(p) -> bool:
    p = dig(p)
    if len(p) != 11:
        return False
    pesos = [3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    r = 11 - sum(int(p[i]) * pesos[i] for i in range(10)) % 11
    return (0 if r >= 10 else r) == int(p[10])


def data_valida(s) -> bool:
    try:
        datetime.strptime(str(s or ""), "%d/%m/%Y")
        return True
    except ValueError:
        return False


def data_inclusao(hoje: date) -> date:
    """Mesma regra de RH/rh_regras.py: até o dia 19 → 1º do mês seguinte; depois → 1º do outro mês."""
    meses = 1 if hoje.day <= DIA_CORTE else 2
    m = hoje.month - 1 + meses
    return date(hoje.year + m // 12, m % 12 + 1, 1)


def plano_odonto(n_dep: int) -> tuple:
    if n_dep <= 0:
        return "9", "PLANO R COLETIVO EMPRESARIAL"
    if n_dep == 1:
        return "56", "TIT + 1"
    return "57", "TIT + 2"


def limpar_dados(d: dict) -> dict:
    """Normaliza o que vem da tela: só os campos conhecidos, texto aparado, números só dígitos."""
    out = {}
    for k in CAMPOS:
        v = str((d or {}).get(k) or "").strip()
        if k in ("cpf", "pis", "cep", "celular", "telefone"):
            v = dig(v)
        elif k in ("nome", "nome_mae", "nome_pai"):
            v = re.sub(r"\s+", " ", v).upper()
        elif k == "sexo":
            v = v[:1].upper() if v[:1].upper() in ("M", "F") else ""
        elif k == "uf" or k == "rg_uf":
            v = v.upper()[:2]
        elif k == "email":
            v = v.lower()
        out[k] = v
    if out["cpf"]:
        out["cpf"] = out["cpf"].zfill(11)
    deps = []
    for dp in (d or {}).get("dependentes") or []:
        x = {}
        for k, _ in DEPENDENTE_OBRIGATORIOS:
            v = str(dp.get(k) or "").strip()
            if k == "cpf":
                v = dig(v).zfill(11) if dig(v) else ""
            elif k in ("nome", "nome_mae"):
                v = re.sub(r"\s+", " ", v).upper()
            elif k == "sexo":
                v = v[:1].upper() if v[:1].upper() in ("M", "F") else ""
            x[k] = v
        if any(x.values()):
            deps.append(x)
    out["dependentes"] = deps
    return out


def mesclar_pdf(atual: dict, novo: dict) -> dict:
    """A RH corrigiu no Onvio e subiu o PDF de novo: o que o PDF novo traz vence; o que ele não traz (o número
    do endereço que ela completou, os dados dos dependentes que só o painel tem) fica como estava."""
    atual, novo = limpar_dados(atual), limpar_dados(novo)
    out = {k: (novo.get(k) or atual.get(k) or "") for k in CAMPOS}
    antigos = {d["cpf"]: d for d in atual["dependentes"] if d.get("cpf")}
    deps = []
    for d in novo["dependentes"]:
        a = antigos.get(d.get("cpf")) or {}
        deps.append({k: (d.get(k) or a.get(k) or "") for k, _ in DEPENDENTE_OBRIGATORIOS})
    out["dependentes"] = deps
    return out


def limpar_beneficios(b: dict) -> dict:
    out = {}
    for k, _, _ in SISTEMAS:
        v = (b or {}).get(k) or {}
        out[k] = {"adere": bool(v.get("adere"))}
        if k == "rhid":
            out[k]["horario"] = str(v.get("horario") or "").strip()
    return out


def pendencias(d: dict, b: dict) -> list:
    """O que impede de mandar aos robôs: [{campo, msg, sistemas}]. Lista vazia = pode enviar."""
    p = []
    adere = {k for k, v in (b or {}).items() if v.get("adere")}

    def falta(campo, msg, sistemas):
        sis = [s for s in sistemas if s in adere] if sistemas else []
        if sistemas and not sis:
            return
        p.append({"campo": campo, "msg": msg, "sistemas": sis})

    if not adere:
        p.append({"campo": "beneficios", "msg": "Marque pelo menos um benefício", "sistemas": []})
    if not d.get("nome"):
        falta("nome", "Nome em branco", None)
    if not cpf_valido(d.get("cpf")):
        falta("cpf", "CPF inválido", None)
    for k, rot in (("data_admissao", "Admissão"), ("data_nascimento", "Nascimento")):
        if d.get(k) and not data_valida(d[k]):
            falta(k, f"{rot}: use dd/mm/aaaa", None)
    if d.get("pis") and not pis_valido(d["pis"]):
        falta("pis", "PIS inválido", ["rhid"])
    if not d.get("data_admissao"):
        falta("data_admissao", "Admissão em branco", ["rhid", "hapvida", "odonto"])
    if not (b.get("rhid") or {}).get("horario"):
        falta("horario", "Escolha o horário do ponto", ["rhid"])
    for k, rot in (("data_nascimento", "Nascimento"), ("sexo", "Sexo"), ("nome_mae", "Nome da mãe"),
                   ("cep", "CEP"), ("logradouro", "Logradouro"), ("cidade", "Cidade"), ("uf", "UF")):
        if not d.get(k):
            falta(k, f"{rot} em branco", ["hapvida", "odonto"])
    if not d.get("numero"):
        falta("numero", "Número do endereço em branco (a Hapvida não aceita sem; use S/N se não houver)",
              ["hapvida", "odonto"])
    if d.get("cep") and len(d["cep"]) != 8:
        falta("cep", "CEP com 8 dígitos", ["hapvida", "odonto"])
    if not d.get("email") and len(d.get("celular") or "") != 11:
        falta("email", "Sem e-mail: informe o celular (11 dígitos) para o convite ir por SMS", ["flash"])
    if d.get("email") and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", d["email"]):
        falta("email", "E-mail inválido", ["flash", "hapvida", "odonto", "rhid"])
    for i, dp in enumerate(d.get("dependentes") or []):
        faltam = [rot for k, rot in DEPENDENTE_OBRIGATORIOS if not dp.get(k)]
        nome = dp.get("nome") or f"Dependente {i + 1}"
        if faltam:
            falta(f"dep{i}", f"{nome}: falta {', '.join(faltam)}", ["hapvida", "odonto"])
        if dp.get("cpf") and not cpf_valido(dp["cpf"]):
            falta(f"dep{i}.cpf", f"{nome}: CPF inválido", ["hapvida", "odonto"])
        if dp.get("data_nascimento") and not data_valida(dp["data_nascimento"]):
            falta(f"dep{i}.data_nascimento", f"{nome}: nascimento dd/mm/aaaa", ["hapvida", "odonto"])
        if dp.get("parentesco") and dp["parentesco"] not in dict(PARENTESCOS):
            falta(f"dep{i}.parentesco", f"{nome}: parentesco desconhecido", ["hapvida", "odonto"])
    return p


def avisos(d: dict, onvio: dict | None) -> list:
    """O que não impede, mas vale a RH olhar."""
    a = []
    if onvio:
        ed = [rot for _, cs in BLOCOS for k, rot in cs if (onvio.get(k) or "") != (d.get(k) or "")]
        if ed:
            a.append(f"Editado no painel (diferente do Onvio): {', '.join(ed)}")
    if not d.get("email"):
        a.append("Sem e-mail: o convite da Flash vai por SMS")
    return a


def resumo_beneficios(d: dict, hoje: date) -> dict:
    n = len(d.get("dependentes") or [])
    cod, nome = plano_odonto(n)
    inc = data_inclusao(hoje).strftime("%d/%m/%Y")
    return {
        "inclusao": inc,
        "n_dependentes": n,
        "flash": f"Convite por e-mail {d['email']}" if d.get("email") else
                 (f"Convite por SMS {d.get('celular')}" if d.get("celular") else "Sem e-mail nem celular"),
        "rhid": "Cadastro no ponto com o horário escolhido",
        "hapvida": f"Titular + {n} dependente(s) · início {inc} · plano 22008 INTEGRAL II",
        "odonto": f"Plano {cod} — {nome} · inclusão {inc}",
    }


def para_robo(d: dict, b: dict, sistema: str) -> dict:
    """O funcionário no formato dos robôs (RH/onvio_pdf.ler_funcionario + o que a RH completou)."""
    f = {k: d.get(k, "") for k in CAMPOS}
    f["dependentes"] = [dict(x) for x in d.get("dependentes") or []]
    if sistema == "rhid":
        f["horario"] = (b.get("rhid") or {}).get("horario", "")
    if sistema == "flash" and f.get("email"):
        f["celular"] = ""  # RH: com e-mail, o celular fica vazio
    return f


# ─────────────────────────────────────────────────────────────── banco
def _agora():
    return datetime.now(timezone.utc)


def _iso(v):
    return v.isoformat() if v else None


def log_tarefa(cur, tarefa_id, estado, mensagem, quem):
    cur.execute("INSERT INTO rh_tarefa_log (tarefa_id, estado, mensagem, quem) VALUES (%s,%s,%s,%s)",
                (tarefa_id, estado, (mensagem or "")[:2000], quem))


def _tarefas(cur, ids):
    if not ids:
        return {}
    cur.execute("""SELECT id, admissao_id, sistema, estado, tentativas, mensagem, detalhe, print_png IS NOT NULL,
                          codigo_pedido_em, criado_em, pego_em, terminado_em, atualizado_em
                   FROM rh_tarefa WHERE admissao_id = ANY(%s) ORDER BY ordem""", (list(ids),))
    out = {}
    for r in cur.fetchall():
        out.setdefault(r[1], []).append({
            "id": r[0], "sistema": r[2], "nome": NOME_SISTEMA.get(r[2], r[2]), "estado": r[3],
            "estado_nome": ESTADOS.get(r[3], r[3]), "tentativas": r[4], "mensagem": r[5] or "",
            "detalhe": r[6] or {}, "tem_print": r[7], "codigo_pedido_em": _iso(r[8]), "criado_em": _iso(r[9]),
            "pego_em": _iso(r[10]), "terminado_em": _iso(r[11]), "atualizado_em": _iso(r[12])})
    return out


def listar(cur) -> list:
    cur.execute("""SELECT id, cpf, nome, dados->>'data_admissao', beneficios, criado_em, criado_por, enviado_em,
                          atualizado_em FROM rh_admissao ORDER BY COALESCE(enviado_em, criado_em) DESC LIMIT 500""")
    rows = cur.fetchall()
    tar = _tarefas(cur, [r[0] for r in rows])
    return [{"id": r[0], "cpf": r[1], "nome": r[2], "data_admissao": r[3] or "", "beneficios": r[4] or {},
             "criado_em": _iso(r[5]), "criado_por": r[6], "enviado_em": _iso(r[7]), "atualizado_em": _iso(r[8]),
             "tarefas": tar.get(r[0], [])} for r in rows]


def obter(cur, aid: int, hoje: date):
    cur.execute("""SELECT id, cpf, nome, dados, onvio, beneficios, arquivo, criado_em, criado_por, atualizado_em,
                          atualizado_por, enviado_em, enviado_por FROM rh_admissao WHERE id = %s""", (aid,))
    r = cur.fetchone()
    if not r:
        return None
    tar = _tarefas(cur, [aid]).get(aid, [])
    ids = [t["id"] for t in tar]
    hist = {}
    if ids:
        cur.execute("""SELECT tarefa_id, em, estado, mensagem, quem FROM rh_tarefa_log
                       WHERE tarefa_id = ANY(%s) ORDER BY em""", (ids,))
        for t in cur.fetchall():
            hist.setdefault(t[0], []).append({"em": _iso(t[1]), "estado": t[2], "estado_nome": ESTADOS.get(t[2], t[2]),
                                              "mensagem": t[3], "quem": t[4]})
    for t in tar:
        t["historico"] = hist.get(t["id"], [])
    dados, ben = r[3] or {}, r[5] or {}
    return {"id": r[0], "cpf": r[1], "nome": r[2], "dados": dados, "onvio": r[4], "beneficios": ben,
            "arquivo": r[6], "criado_em": _iso(r[7]), "criado_por": r[8], "atualizado_em": _iso(r[9]),
            "atualizado_por": r[10], "enviado_em": _iso(r[11]), "enviado_por": r[12], "tarefas": tar,
            "pendencias": pendencias(dados, ben), "avisos": avisos(dados, r[4]),
            "resumo": resumo_beneficios(dados, hoje)}


def outra_com_cpf(cur, cpf: str, menos_id=None):
    cur.execute("SELECT id, nome, criado_em FROM rh_admissao WHERE cpf = %s AND id <> COALESCE(%s, -1) "
                "ORDER BY id DESC LIMIT 1", (cpf, menos_id))
    r = cur.fetchone()
    return {"id": r[0], "nome": r[1], "criado_em": _iso(r[2])} if r else None


def salvar(cur, aid, dados, onvio, beneficios, arquivo, usuario, enviar: bool):
    """Grava a admissão. enviar=True cria/reabre as tarefas dos benefícios marcados.

    Devolve (id, erro). Com tarefa rodando ou esperando código não deixa mexer (o robô está usando os dados)."""
    import json
    dados, beneficios = limpar_dados(dados), limpar_beneficios(beneficios)
    if aid:
        cur.execute("SELECT 1 FROM rh_admissao WHERE id = %s FOR UPDATE", (aid,))
        if not cur.fetchone():
            return None, "Admissão não encontrada."
        cur.execute("SELECT sistema, estado FROM rh_tarefa WHERE admissao_id = %s", (aid,))
        tar = dict(cur.fetchall())
        ocupado = [NOME_SISTEMA[s] for s, e in tar.items() if e in ("rodando", "aguardando_codigo")]
        if ocupado:
            return None, f"O robô está trabalhando nesta admissão ({', '.join(ocupado)}). Espere terminar."
        cur.execute("""UPDATE rh_admissao SET cpf=%s, nome=%s, dados=%s, beneficios=%s,
                         onvio=COALESCE(%s, onvio), arquivo=COALESCE(%s, arquivo),
                         atualizado_em=NOW(), atualizado_por=%s WHERE id=%s""",
                    (dados["cpf"], dados["nome"], json.dumps(dados), json.dumps(beneficios),
                     json.dumps(onvio) if onvio else None, arquivo, usuario, aid))
    else:
        tar = {}
        cur.execute("""INSERT INTO rh_admissao (cpf, nome, dados, onvio, beneficios, arquivo, criado_por, atualizado_por)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (dados["cpf"], dados["nome"], json.dumps(dados), json.dumps(onvio) if onvio else None,
                     json.dumps(beneficios), arquivo, usuario, usuario))
        aid = cur.fetchone()[0]
    if not enviar:
        return aid, None
    pend = pendencias(dados, beneficios)
    if pend:
        return aid, "Falta: " + "; ".join(x["msg"] for x in pend)
    for s, _, _ in SISTEMAS:
        if not beneficios[s]["adere"]:
            # desmarcou depois: tarefa que ainda não rodou é cancelada; a feita fica (já está no portal)
            if tar.get(s) in ("fila", "erro"):
                cur.execute("""UPDATE rh_tarefa SET estado='cancelada', atualizado_em=NOW()
                               WHERE admissao_id=%s AND sistema=%s RETURNING id""", (aid, s))
                log_tarefa(cur, cur.fetchone()[0], "cancelada", "benefício desmarcado", usuario)
            continue
        if s not in tar:
            cur.execute("""INSERT INTO rh_tarefa (admissao_id, sistema, ordem) VALUES (%s,%s,%s) RETURNING id""",
                        (aid, s, ORDEM[s]))
            log_tarefa(cur, cur.fetchone()[0], "fila", "enviado para o robô", usuario)
        elif tar[s] in ("erro", "cancelada"):
            cur.execute("""UPDATE rh_tarefa SET estado='fila', mensagem=NULL, atualizado_em=NOW()
                           WHERE admissao_id=%s AND sistema=%s RETURNING id""", (aid, s))
            log_tarefa(cur, cur.fetchone()[0], "fila", "reenviado depois de corrigir", usuario)
        # 'feito'/'conferir' não volta para a fila: o cadastro já está no portal
    cur.execute("UPDATE rh_admissao SET enviado_em=COALESCE(enviado_em, NOW()), enviado_por=COALESCE(enviado_por, %s) "
                "WHERE id=%s", (usuario, aid))
    return aid, None


def excluir(cur, aid):
    cur.execute("SELECT estado FROM rh_tarefa WHERE admissao_id=%s", (aid,))
    est = {r[0] for r in cur.fetchall()}
    if est & {"rodando", "aguardando_codigo", "feito", "conferir"}:
        return "Já há cadastro feito (ou em andamento) nos portais: a admissão fica como registro."
    cur.execute("DELETE FROM rh_admissao WHERE id=%s", (aid,))
    return None


def acao_tarefa(cur, tid, acao, usuario):
    cur.execute("SELECT estado FROM rh_tarefa WHERE id=%s FOR UPDATE", (tid,))
    r = cur.fetchone()
    if not r:
        return "Tarefa não encontrada."
    est = r[0]
    if acao == "repetir":
        if est not in ("erro", "cancelada", "conferir"):
            return "Só dá para repetir o que deu erro, foi cancelado ou ficou para conferir."
        cur.execute("UPDATE rh_tarefa SET estado='fila', mensagem=NULL, atualizado_em=NOW() WHERE id=%s", (tid,))
        log_tarefa(cur, tid, "fila", "tentar de novo", usuario)
    elif acao == "cancelar":
        if est not in ("fila", "erro"):
            return "Só dá para cancelar o que ainda está na fila ou deu erro."
        cur.execute("UPDATE rh_tarefa SET estado='cancelada', atualizado_em=NOW() WHERE id=%s", (tid,))
        log_tarefa(cur, tid, "cancelada", "cancelada no painel", usuario)
    elif acao == "conferido":
        if est != "conferir":
            return "Só o que ficou 'conferir no portal'."
        cur.execute("UPDATE rh_tarefa SET estado='feito', atualizado_em=NOW() WHERE id=%s", (tid,))
        log_tarefa(cur, tid, "feito", "conferido no portal pela RH", usuario)
    else:
        return "Ação desconhecida."
    return None


def informar_codigo(cur, tid, codigo, usuario):
    codigo = dig(codigo)
    if len(codigo) != 6:
        return "O código tem 6 dígitos."
    cur.execute("SELECT estado FROM rh_tarefa WHERE id=%s FOR UPDATE", (tid,))
    r = cur.fetchone()
    if not r or r[0] != "aguardando_codigo":
        return "O robô não está esperando código nesta tarefa."
    cur.execute("UPDATE rh_tarefa SET codigo=%s, atualizado_em=NOW() WHERE id=%s", (codigo, tid))
    log_tarefa(cur, tid, "aguardando_codigo", "código digitado no painel", usuario)  # o código não vai para o log
    return None


def print_da_tarefa(cur, tid):
    cur.execute("SELECT print_png FROM rh_tarefa WHERE id=%s", (tid,))
    r = cur.fetchone()
    return bytes(r[0]) if r and r[0] else None


def config(cur, chave):
    cur.execute("SELECT valor, em FROM rh_config WHERE chave=%s", (chave,))
    r = cur.fetchone()
    return (r[0], r[1]) if r else (None, None)


def gravar_config(cur, chave, valor):
    import json
    cur.execute("""INSERT INTO rh_config (chave, valor, em) VALUES (%s, %s, NOW())
                   ON CONFLICT (chave) DO UPDATE SET valor=EXCLUDED.valor, em=NOW()""", (chave, json.dumps(valor)))


def situacao_geral(cur) -> dict:
    """Para o topo da tela: o worker está vivo? há código esperando? horários do RHID."""
    _, visto = config(cur, "worker_visto")
    horarios, _ = config(cur, "horarios_rhid")
    cur.execute("""SELECT t.id, a.nome, t.codigo_pedido_em, t.codigo IS NOT NULL FROM rh_tarefa t
                   JOIN rh_admissao a ON a.id = t.admissao_id WHERE t.estado = 'aguardando_codigo'
                   ORDER BY t.codigo_pedido_em""")
    cods = [{"tarefa_id": r[0], "nome": r[1], "pedido_em": _iso(r[2]), "ja_digitado": r[3]} for r in cur.fetchall()]
    cur.execute("SELECT estado, COUNT(*) FROM rh_tarefa GROUP BY estado")
    cont = dict(cur.fetchall())
    seg = int((_agora() - visto).total_seconds()) if visto else None
    return {"worker_visto_s": seg, "worker_online": seg is not None and seg < 120, "codigos": cods,
            "contagem": cont, "horarios_rhid": horarios or []}


# ─────────────────────────────────────────────────────────────── robô (worker do SLTBL-10)
def chave_robo_ok(recebida):
    esperada = os.getenv("RH_ROBO_CHAVE") or ""
    return len(esperada) >= 32 and hmac.compare_digest(str(recebida or ""), esperada)


def soltar_abandonadas(cur):
    """Tarefa 'rodando' há mais de RODANDO_MAX (ou esperando código há mais de CODIGO_MAX) vira erro."""
    cur.execute("""UPDATE rh_tarefa SET estado='erro', terminado_em=NOW(), atualizado_em=NOW(),
                          mensagem='O robô parou sem responder (servidor reiniciado ou navegador travado). Tente de novo.'
                   WHERE (estado='rodando' AND pego_em < NOW() - %s)
                      OR (estado='aguardando_codigo' AND codigo_pedido_em < NOW() - %s)
                   RETURNING id""", (RODANDO_MAX, CODIGO_MAX))
    for (tid,) in cur.fetchall():
        log_tarefa(cur, tid, "erro", "robô parou sem responder", "sistema")


def proxima(cur):
    """Pega a próxima tarefa da fila (Flash primeiro em cada admissão) e marca 'rodando'."""
    gravar_config(cur, "worker_visto", _agora().isoformat())
    soltar_abandonadas(cur)
    cur.execute("SELECT 1 FROM rh_tarefa WHERE estado IN ('rodando', 'aguardando_codigo') LIMIT 1")
    if cur.fetchone():
        return None  # um robô por vez: o Chrome do servidor é um só
    cur.execute("""SELECT t.id, t.sistema, t.admissao_id, a.dados, a.beneficios FROM rh_tarefa t
                   JOIN rh_admissao a ON a.id = t.admissao_id
                   WHERE t.estado='fila' ORDER BY t.admissao_id, t.ordem LIMIT 1 FOR UPDATE OF t SKIP LOCKED""")
    r = cur.fetchone()
    if not r:
        return None
    tid, sistema, aid, dados, ben = r
    cur.execute("""UPDATE rh_tarefa SET estado='rodando', tentativas=tentativas+1, pego_em=NOW(), codigo=NULL,
                          mensagem=NULL, atualizado_em=NOW() WHERE id=%s""", (tid,))
    log_tarefa(cur, tid, "rodando", "o robô pegou", "robô")
    return {"tarefa_id": tid, "sistema": sistema, "admissao_id": aid,
            "funcionario": para_robo(dados or {}, ben or {}, sistema)}


# o que o robô devolve → estado da tarefa
SITUACAO_ROBO = {
    "enviado": "feito", "ok": "feito", "enviado_conferir": "conferir", "verificar": "conferir",
    "pulado": "feito", "erro": "erro", "erro_envio": "erro", "dados_incompletos": "erro",
}


def atualizar_pelo_robo(cur, d: dict):
    """POST do worker: {tarefa_id, estado|situacao, mensagem, detalhe, print_b64}."""
    import json
    gravar_config(cur, "worker_visto", _agora().isoformat())
    tid = int(d.get("tarefa_id") or 0)
    cur.execute("SELECT estado FROM rh_tarefa WHERE id=%s FOR UPDATE", (tid,))
    r = cur.fetchone()
    if not r:
        raise ValueError("tarefa não encontrada")
    if r[0] not in ("rodando", "aguardando_codigo"):
        raise ValueError(f"tarefa não está com o robô (estado {r[0]})")
    estado = d.get("estado") or SITUACAO_ROBO.get(d.get("situacao") or "", "erro")
    if estado not in ("rodando", "aguardando_codigo", "feito", "conferir", "erro"):
        raise ValueError("estado inválido")
    png = None
    if d.get("print_b64"):
        png = base64.b64decode(d["print_b64"], validate=True)
        if len(png) > MAX_PRINT or not png.startswith(b"\x89PNG"):
            raise ValueError("print inválido")
    msg = str(d.get("mensagem") or "")[:2000]
    fim = estado in ("feito", "conferir", "erro")
    cur.execute("""UPDATE rh_tarefa SET estado=%s, mensagem=COALESCE(NULLIF(%s,''), mensagem),
                          detalhe=COALESCE(%s, detalhe), print_png=COALESCE(%s, print_png),
                          codigo_pedido_em=CASE WHEN %s='aguardando_codigo' THEN NOW() ELSE codigo_pedido_em END,
                          codigo=CASE WHEN %s='aguardando_codigo' THEN NULL ELSE codigo END,
                          terminado_em=CASE WHEN %s THEN NOW() ELSE terminado_em END, atualizado_em=NOW()
                   WHERE id=%s""",
                (estado, msg, json.dumps(d["detalhe"], default=str) if isinstance(d.get("detalhe"), dict) else None,
                 png, estado, estado, fim, tid))
    if estado != r[0] or fim:
        log_tarefa(cur, tid, estado, msg[:500], "robô")
    if isinstance(d.get("horarios_rhid"), list) and d["horarios_rhid"]:
        gravar_config(cur, "horarios_rhid", [str(x)[:120] for x in d["horarios_rhid"]][:200])
    return {"estado": estado}


def codigo_para_robo(cur, tid):
    """O worker pergunta se a RH já digitou o código. Entrega uma vez e apaga."""
    gravar_config(cur, "worker_visto", _agora().isoformat())
    cur.execute("SELECT estado, codigo FROM rh_tarefa WHERE id=%s FOR UPDATE", (tid,))
    r = cur.fetchone()
    if not r or r[0] != "aguardando_codigo":
        return None, (r[0] if r else "inexistente")
    if not r[1]:
        return None, r[0]
    cur.execute("UPDATE rh_tarefa SET codigo=NULL, estado='rodando', atualizado_em=NOW() WHERE id=%s", (tid,))
    log_tarefa(cur, tid, "rodando", "o robô recebeu o código", "robô")
    return r[1], "rodando"
