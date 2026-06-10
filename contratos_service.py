# -*- coding: utf-8 -*-
"""
Contratos TAC Agregado — serviço de extração de documentos e geração do contrato.

Fluxo:
  1. extrair_documentos(arquivos) — usa OpenAI vision (gpt-4.1-mini) para ler CNH,
     CRLV, RNTRC/ANTT, comprovante de endereço, etc. e devolver os dados em JSON.
  2. checar_pendencias(dados) — lista apenas as pendências IMPEDITIVAS (em Python,
     não confia só na IA).
  3. montar_contexto(dados, usa_rastreador_proprio) + gerar_docx(...) — preenche o
     template soberano `contrato_tac_template.docx` (docxtpl). O texto jurídico e os
     dados da Contratante (Rizza) nunca são tocados.

O template é a ÚNICA fonte do texto do contrato. A IA só preenche campos variáveis.
"""
import os
import io
import base64
import json
from datetime import date

TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'contrato_tac_template.docx')

MODELO_OPENAI = 'gpt-4.1-mini'

_MESES = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho',
          'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro']

# Prompt do diretor (AGENTE GPT – EMISSÃO DE CONTRATOS TAC), adaptado para EXTRAÇÃO
# estruturada em JSON. As regras de NÃO criar exigências entram aqui; a decisão final
# de pendência impeditiva é reconfirmada em Python (checar_pendencias).
PROMPT_SISTEMA = """Você é um especialista em cadastro de agregados da Rizza Transportes Ltda.
Sua função é LER os documentos enviados (imagens/PDFs) e EXTRAIR os dados do CONTRATADO e do
VEÍCULO para emissão de contrato TAC Agregado.

PASSO 1 — Classifique cada documento recebido em um destes tipos:
CNH, RG, CPF, COMPROVANTE_ENDERECO, CARTAO_CNPJ, CONTRATO_SOCIAL, CRLV, ATPV,
CONSULTA_RNTRC (ANTT), COMPROVANTE_BANCARIO, OUTRO.

PASSO 2 — Extraia CADA campo SOMENTE do seu documento-fonte correto. NÃO misture documentos:
- contratado.nome → SEMPRE da CNH (fallback: RG). NUNCA use o nome que aparece na
  CONSULTA_RNTRC/ANTT, no CRLV ou em qualquer outro documento como nome do contratado.
- contratado.cpf_cnpj → da CNH/RG/CPF (pessoa física) ou do CARTAO_CNPJ (pessoa jurídica).
- contratado.endereco/bairro/cidade/estado/cep → do COMPROVANTE_ENDERECO
  (fallback: endereço da CNH, somente se não houver comprovante de endereço).
- contratado.email/telefone → de qualquer documento onde apareça claramente.
- rntrc.numero / rntrc.situacao / rntrc.categoria → SOMENTE da CONSULTA_RNTRC (ANTT).
  Deste documento extraia APENAS esses três campos; NÃO use o nome dele.
- veiculo.* (placa, renavam, chassi, marca, modelo, ano) → SOMENTE do CRLV/ATPV.
- bancarios.* (banco, agência, conta, pix) → SOMENTE do COMPROVANTE_BANCARIO.

REGRAS GERAIS:
- Você NÃO é advogado nem auditor documental. NÃO crie exigências.
- O comprovante de endereço serve apenas para identificar o endereço — NÃO valide
  titularidade (pode estar em nome de cônjuge, pais, filhos, terceiros, empresa).
- Posse do veículo: se o veículo estiver vinculado ao RNTRC ativo do contratado, a posse
  está comprovada. NÃO exija ATPV, comodato, procuração.
- Extraia exatamente o que está nos documentos. Se o documento-fonte de um campo não tiver
  sido enviado, retorne "". NUNCA preencha um campo a partir de um documento que não seja
  a fonte correta dele (ex.: nunca tire o nome do contratado da ANTT).

Responda SOMENTE com um objeto JSON válido nesta estrutura (sem comentários):
{
  "documentos_identificados": [],
  "contratado": {
    "nome": "", "cpf_cnpj": "", "endereco": "", "bairro": "", "cidade": "",
    "estado": "", "cep": "", "email": "", "telefone": ""
  },
  "rntrc": { "numero": "", "situacao": "", "categoria": "" },
  "veiculo": {
    "tipo": "", "placa": "", "renavam": "", "chassi": "", "marca": "",
    "modelo": "", "ano_fabricacao": "", "ano_modelo": ""
  },
  "bancarios": { "banco": "", "agencia": "", "conta": "", "pix": "" },
  "observacoes": []
}
- "documentos_identificados" = lista dos tipos que você reconheceu (ex.: ["CNH","CONSULTA_RNTRC"]).
- "endereco" deve ser o logradouro + número (rua e número), sem bairro/cidade/cep.
- "observacoes": lista curta de pontos que o operador deveria conferir (ex.: divergência
  de CPF entre documentos). Não inclua exigências novas."""


# ─────────────────────────────────────────────────────────────────────────────
# Extração
# ─────────────────────────────────────────────────────────────────────────────
def _arquivo_para_imagens_b64(nome, conteudo):
    """Converte um arquivo (PDF ou imagem) numa lista de data-URLs PNG base64.
    PDFs são rasterizados página a página com PyMuPDF; imagens passam direto."""
    ext = os.path.splitext(nome or '')[1].lower()
    imagens = []
    if ext == '.pdf':
        import fitz  # PyMuPDF
        doc = fitz.open(stream=conteudo, filetype='pdf')
        try:
            for page in doc:
                pix = page.get_pixmap(dpi=140)
                png = pix.tobytes('png')
                imagens.append('data:image/png;base64,' + base64.b64encode(png).decode())
        finally:
            doc.close()
    else:
        mime = {
            '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
            '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp',
        }.get(ext, 'image/jpeg')
        imagens.append(f'data:{mime};base64,' + base64.b64encode(conteudo).decode())
    return imagens


def extrair_documentos(arquivos, max_imagens=14):
    """arquivos: lista de tuplas (nome, bytes). Retorna dict de dados extraídos.
    Levanta RuntimeError se não houver chave OpenAI ou se a API falhar."""
    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        raise RuntimeError('OPENAI_API_KEY não configurada no ambiente.')
    if not arquivos:
        raise RuntimeError('Nenhum documento enviado.')

    content = [{'type': 'text',
                'text': 'Extraia os dados do contratado e do veículo dos documentos a seguir.'}]
    for nome, conteudo in arquivos:
        for url in _arquivo_para_imagens_b64(nome, conteudo):
            if len([c for c in content if c['type'] == 'image_url']) >= max_imagens:
                break
            content.append({'type': 'image_url', 'image_url': {'url': url}})

    from openai import OpenAI
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model=MODELO_OPENAI,
        messages=[
            {'role': 'system', 'content': PROMPT_SISTEMA},
            {'role': 'user', 'content': content},
        ],
        response_format={'type': 'json_object'},
        max_tokens=1500,
        temperature=0,
    )
    raw = resp.choices[0].message.content
    try:
        dados = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError('A IA não retornou um JSON válido.')
    return _normalizar(dados)


def _normalizar(dados):
    """Garante a estrutura completa mesmo que a IA omita chaves."""
    base = {
        'contratado': {'nome': '', 'cpf_cnpj': '', 'endereco': '', 'bairro': '',
                       'cidade': '', 'estado': '', 'cep': '', 'email': '', 'telefone': ''},
        'rntrc': {'numero': '', 'situacao': '', 'categoria': ''},
        'veiculo': {'tipo': '', 'placa': '', 'renavam': '', 'chassi': '', 'marca': '',
                    'modelo': '', 'ano_fabricacao': '', 'ano_modelo': ''},
        'bancarios': {'banco': '', 'agencia': '', 'conta': '', 'pix': ''},
        'observacoes': [],
    }
    for sec, campos in base.items():
        if sec == 'observacoes':
            obs = dados.get('observacoes') or []
            base['observacoes'] = obs if isinstance(obs, list) else [str(obs)]
            continue
        recebido = dados.get(sec) or {}
        if isinstance(recebido, dict):
            for k in campos:
                v = recebido.get(k, '')
                campos[k] = ('' if v is None else str(v)).strip()
    return base


def merge_dados(antigo, novo):
    """Mescla uma reextração (com mais documentos) por cima dos dados já existentes,
    preenchendo apenas campos vazios — preserva edições manuais do operador."""
    if not antigo:
        return novo
    out = json.loads(json.dumps(antigo))
    for sec in ('contratado', 'rntrc', 'veiculo', 'bancarios'):
        for k, v in (novo.get(sec) or {}).items():
            if v and not (out.get(sec, {}) or {}).get(k):
                out.setdefault(sec, {})[k] = v
    obs = list(dict.fromkeys((antigo.get('observacoes') or []) + (novo.get('observacoes') or [])))
    out['observacoes'] = obs
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Pendências impeditivas (somente estas bloqueiam a emissão)
# ─────────────────────────────────────────────────────────────────────────────
def checar_pendencias(dados):
    c = dados.get('contratado', {})
    v = dados.get('veiculo', {})
    r = dados.get('rntrc', {})
    b = dados.get('bancarios', {})
    pend = []
    if not c.get('nome'):
        pend.append('Ausência de identificação do contratado (nome).')
    if not c.get('cpf_cnpj'):
        pend.append('Ausência do CPF/CNPJ do contratado.')
    if not r.get('numero'):
        pend.append('Ausência do RNTRC.')
    if not v.get('placa'):
        pend.append('Ausência da placa do veículo.')
    if not (v.get('renavam') or v.get('chassi')):
        pend.append('Ausência do documento do veículo (RENAVAM/chassi).')
    tem_banco = b.get('banco') and b.get('agencia') and b.get('conta')
    if not (tem_banco or b.get('pix')):
        pend.append('Informe os dados bancários do contratado (Banco, Agência, Conta ou PIX).')
    return pend


# ─────────────────────────────────────────────────────────────────────────────
# Montagem do contexto e geração do .docx
# ─────────────────────────────────────────────────────────────────────────────
def _data_extenso(d=None):
    d = d or date.today()
    return f'{d.day} de {_MESES[d.month - 1]} de {d.year}'


def _compor_endereco(c):
    partes = [c.get('endereco', '')]
    if c.get('bairro'):
        partes.append(f"Bairro {c['bairro']}")
    if c.get('cep'):
        partes.append(f"CEP {c['cep']}")
    cidade_uf = ' - '.join(x for x in [c.get('cidade', ''), c.get('estado', '')] if x)
    if cidade_uf:
        partes.append(cidade_uf)
    return ', '.join(p for p in partes if p)


def _compor_pagamento(b):
    if b.get('banco') and b.get('agencia') and b.get('conta'):
        txt = (f"Dados Bancários: Banco: {b['banco']}  Agência: {b['agencia']}  "
               f"Conta-Corrente: {b['conta']}")
        if b.get('pix'):
            txt += f"  |  PIX: {b['pix']}"
        return txt
    if b.get('pix'):
        return f"Chave PIX: {b['pix']}"
    return ''


def montar_contexto(dados, usa_rastreador_proprio=False, vigencia_inicio='',
                    vigencia_termino='', comodato_numero_serie='', comodato_estado='',
                    comodato_marca_modelo=''):
    c = dados.get('contratado', {})
    v = dados.get('veiculo', {})
    inicio = vigencia_inicio.strip() or date.today().strftime('%d/%m/%Y')
    termino = vigencia_termino.strip() or 'Indeterminado'
    ano = v.get('ano_modelo', '')
    if v.get('ano_fabricacao') and v.get('ano_modelo'):
        ano = f"{v['ano_fabricacao']}/{v['ano_modelo']}"
    elif v.get('ano_fabricacao'):
        ano = v['ano_fabricacao']
    return {
        'nome': c.get('nome', ''),
        'cpf_cnpj': c.get('cpf_cnpj', ''),
        'endereco': _compor_endereco(c),
        'tipo_veiculo': v.get('tipo') or 'Cavalo mecânico',
        'placa': v.get('placa', ''),
        'renavam': v.get('renavam', ''),
        'chassi': v.get('chassi', ''),
        'marca': v.get('marca', ''),
        'ano_modelo': ano,
        'vigencia': f'INÍCIO: {inicio} / TÉRMINO: {termino}',
        'pagamento': _compor_pagamento(dados.get('bancarios', {})),
        'data_assinatura': _data_extenso(),
        'usa_comodato': not usa_rastreador_proprio,
        'comodato_marca_modelo': comodato_marca_modelo.strip(),
        'comodato_numero_serie': comodato_numero_serie.strip(),
        'comodato_estado': comodato_estado.strip() or '( ) Novo (X) Usado em bom estado',
    }


def gerar_docx(contexto):
    """Renderiza o template soberano e devolve os bytes do .docx."""
    from docxtpl import DocxTemplate
    tpl = DocxTemplate(TEMPLATE_PATH)
    tpl.render(contexto)
    buf = io.BytesIO()
    tpl.save(buf)
    buf.seek(0)
    return buf.getvalue()


def gerar_html(docx_bytes):
    """Converte o .docx gerado em HTML (para preview/impressão → 'Salvar como PDF').
    Mantém o template como fonte única — o HTML é derivado do .docx renderizado."""
    import mammoth
    result = mammoth.convert_to_html(io.BytesIO(docx_bytes))
    return result.value


def nome_arquivo(contexto):
    nome = (contexto.get('nome') or 'CONTRATADO').strip().replace(' ', '_')
    placa = (contexto.get('placa') or '').strip().replace(' ', '')
    base = f'Contrato Agregado_{nome}'
    if placa:
        base += f'_{placa}'
    return base
