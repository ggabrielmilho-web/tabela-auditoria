"""
Leitor do "RELATÓRIO DE CADASTRO DE EMPREGADOS NA FOLHA" exportado do Onvio (Domínio).

O relatório é uma grade de 4 colunas fixas: rótulo | valor | rótulo | valor.
Lemos pela posição horizontal de cada palavra, não pelo texto corrido.

Lê com PyMuPDF (fitz) — é o que a Tabela Auditoria já tem instalado — e cai para o pdfplumber se o
fitz faltar. Aceita caminho ou os bytes do PDF (o painel recebe o upload em memória).
CÓPIA IGUAL em dois lugares: RH/onvio_pdf.py (robôs, linha de comando) e Tabela Auditoria/rh_onvio.py
(painel). Mudou um, copie para o outro.

Uso:
  python onvio_pdf.py "cadastro.pdf"            mostra os campos lidos e o funcionário montado
  from onvio_pdf import ler_funcionario
"""
import json
import re
import sys
import unicodedata

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover
    fitz = None

SECOES = {"geral", "profissionais", "pessoais", "dependentes"}
TOPO_CABECALHO = 125  # pt: acima disso é o cabeçalho repetido de cada página


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _paginas(origem):
    """[(largura, [{'x0', 'top', 'text'}…])] de cada página. origem = caminho ou bytes."""
    if fitz is not None:
        doc = fitz.open(stream=origem, filetype="pdf") if isinstance(origem, (bytes, bytearray)) else fitz.open(origem)
        with doc:
            return [(p.rect.width, [{"x0": w[0], "top": w[1], "text": w[4]} for w in p.get_text("words")])
                    for p in doc]
    import io
    import pdfplumber
    with pdfplumber.open(io.BytesIO(origem) if isinstance(origem, (bytes, bytearray)) else origem) as pdf:
        return [(p.width, p.extract_words(keep_blank_chars=False, x_tolerance=1.5, y_tolerance=2)) for p in pdf.pages]


def _linhas(palavras):
    """Agrupa as palavras por linha (top parecido)."""
    linhas = []
    for w in sorted(palavras, key=lambda w: (round(w["top"]), w["x0"])):
        if linhas and abs(linhas[-1][0] - w["top"]) <= 3:
            linhas[-1][1].append(w)
        else:
            linhas.append([w["top"], [w]])
    return linhas


def ler_campos(caminho):
    """Devolve (campos, dependentes, cabecalho). campos = {rotulo normalizado: valor}."""
    campos, dependentes, cab = {}, [], {}
    secao = None  # a seção continua de uma página para a outra
    for W, palavras in _paginas(caminho):
        c1, c2, c3 = W * 0.25, W * 0.487, W * 0.72  # limites das 4 colunas
        pend = {0: "", 1: ""}      # rótulo quebrado em duas linhas
        pend_val = {0: "", 1: ""}  # valor que veio na 1ª linha do rótulo quebrado
        ultimo = {0: None, 1: None}  # último rótulo de cada lado (valor que quebra linha)
        for top, ws in _linhas(palavras):
            texto = " ".join(w["text"] for w in ws)
            if top < TOPO_CABECALHO:
                m = re.search(r"CPF:\s*(\d{11})", texto)
                if m:
                    cab["cpf"] = m.group(1)
                m = re.search(r"Nome:\s*(.+)$", texto)
                if m:
                    cab["nome"] = m.group(1).strip()
                continue
            if _norm(texto) in SECOES:
                secao = _norm(texto)
                continue
            cols = ["", "", "", ""]
            for w in ws:
                i = 0 if w["x0"] < c1 else 1 if w["x0"] < c2 else 2 if w["x0"] < c3 else 3
                cols[i] = (cols[i] + " " + w["text"]).strip()
            for lado, (rot, val) in enumerate([(cols[0], cols[1]), (cols[2], cols[3])]):
                if rot:
                    rot = (pend[lado] + " " + rot).strip()
                    if not rot.endswith((":", "?")):
                        pend[lado] = rot        # rótulo continua na próxima linha
                        pend_val[lado] = (pend_val[lado] + " " + val).strip()
                        ultimo[lado] = None
                        continue
                    val = (pend_val[lado] + " " + val).strip()
                    pend[lado], pend_val[lado] = "", ""
                    chave = _norm(rot.rstrip(":?"))
                    if secao == "dependentes":
                        if chave == "nome" or not dependentes:
                            dependentes.append({})
                        dependentes[-1][chave] = val
                    else:
                        # chave = "secao.rotulo"; rótulo repetido na seção vira "#2", "#3"…
                        base = f"{secao}.{chave}"
                        chave, n = base, 1
                        while chave in campos:
                            n += 1
                            chave = f"{base}#{n}"
                        campos[chave] = val
                    ultimo[lado] = chave
                elif val and ultimo[lado] and secao != "dependentes":
                    campos[ultimo[lado]] = (campos.get(ultimo[lado], "") + " " + val).strip()
    dependentes = [d for d in dependentes if any(v for v in d.values())]
    return campos, dependentes, cab


# ---------------------------------------------------------------- montagem do funcionário
ESTADO_CIVIL = {"solteiro": "solteiro", "casado": "casado", "viuvo": "viuvo",
                "divorciado": "divorciado", "separado": "separado", "uniao estavel": "uniao_estavel"}


def _so_dig(v):
    return re.sub(r"\D", "", v or "")


def _telefone(v):
    d = _so_dig(v)
    return d if len(d) in (10, 11) else ""


def ler_funcionario(caminho) -> dict:
    """Funcionário no formato comum dos robôs (RHID, Hapvida…). Campos ausentes ficam ''."""
    c, deps, cab = ler_campos(caminho)
    g = lambda k: (c.get(k) or "").strip()

    tipo_end = g("pessoais.tipo de endereco")
    logradouro = g("pessoais.endereco")
    if tipo_end and _norm(logradouro).startswith(_norm(tipo_end) + " "):
        logradouro = logradouro[len(tipo_end):].strip()  # "Alameda João…" -> "João…"

    ec = _norm(g("pessoais.estado civil")).replace("(a)", "")
    ec = next((v for k, v in ESTADO_CIVIL.items() if _norm(ec).startswith(k)), "")

    tels = [_telefone(g("pessoais.telefone 1")), _telefone(g("pessoais.telefone 2"))]
    celular = next((t for t in tels if len(t) == 11 and t[2] == "9"), "")
    fixo = next((t for t in tels if t and t != celular), "")

    sexo = _norm(g("pessoais.sexo"))
    return {
        "cpf": _so_dig(cab.get("cpf", "")).zfill(11) if cab.get("cpf") else "",
        "nome": cab.get("nome", ""),
        "matricula": g("geral.matricula"),
        "cargo": g("geral.cargo") or g("geral.funcao"),
        "data_admissao": g("geral.data de admissao"),
        "data_nascimento": g("pessoais.data de nascimento"),
        "sexo": "M" if sexo.startswith("masc") else "F" if sexo.startswith("fem") else "",
        "estado_civil": ec,
        "nome_mae": g("pessoais.nome da mae"),
        "nome_pai": g("pessoais.nome do pai"),
        "rg": g("pessoais.numero identidade"),
        "rg_orgao": g("pessoais.orgao de expedicao da identidade"),
        "rg_uf": g("pessoais.uf identidade"),
        "pis": _so_dig(g("profissionais.numero do pis")),
        "ctps": g("profissionais.numero da carteira profissional"),
        "ctps_serie": g("profissionais.serie da carteira profissional"),
        "email": g("pessoais.e mail principal"),
        "celular": celular,
        "telefone": fixo,
        "cep": _so_dig(g("pessoais.cep")),
        "tipo_logradouro": tipo_end,
        "logradouro": logradouro,
        "numero": g("pessoais.numero"),
        "complemento": g("pessoais.complemento"),
        "bairro": g("pessoais.bairro"),
        "cidade": g("pessoais.municipio"),
        "uf": g("pessoais.uf"),  # 1ª UF de Pessoais = endereço; a #2 é a de nascimento
        "dependentes": [{"nome": d.get("nome", ""), "cpf": _so_dig(d.get("cpf", ""))} for d in deps],
    }


if __name__ == "__main__":
    campos, deps, cab = ler_campos(sys.argv[1])
    if "--campos" in sys.argv:
        for k, v in campos.items():
            print(f"{k:45} = {v}")
        print("dependentes:", deps, "| cabeçalho:", cab)
    print(json.dumps(ler_funcionario(sys.argv[1]), ensure_ascii=False, indent=2))
