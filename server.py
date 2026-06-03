"""
Auditoria Receita — Backend
Rode: python server.py
Acesse: http://localhost:5000
"""

import os
import io
import json
import time
import tempfile
import functools
import psycopg2
import requests
from flask import Flask, Response, jsonify, send_from_directory, request, session, redirect, url_for, send_file, stream_with_context
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder='.')
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-change-me')
CORS(app, supports_credentials=True)

# ── Config Power BI ──
CONFIG = {
    'tenant_id':       os.getenv('POWERBI_TENANT_ID', ''),
    'client_id':       os.getenv('POWERBI_CLIENT_ID', ''),
    'client_secret':   os.getenv('POWERBI_CLIENT_SECRET', ''),
    'dataset_id':      os.getenv('POWERBI_DATASET_ID', ''),       # Auditoria + Tarifas
    'group_id':        os.getenv('POWERBI_GROUP_ID', ''),
    'dre_dataset_id':  os.getenv('POWERBI_DRE_DATASET_ID', ''),   # DRE
}

# ── Banco de dados ──
def get_db():
    return psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        port=os.getenv('DB_PORT', '5432'),
        dbname=os.getenv('DB_NAME', 'postgres'),
        user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD', ''),
    )

# ── Decorators de autenticação ──
def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'ok': False, 'error': 'Não autenticado'}), 401
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'ok': False, 'error': 'Não autenticado'}), 401
            return redirect('/login')
        if session.get('role') != 'admin':
            if request.path.startswith('/api/'):
                return jsonify({'ok': False, 'error': 'Acesso negado'}), 403
            return redirect('/')
        return f(*args, **kwargs)
    return decorated


# ── Power BI helpers ──
def get_token():
    url = f"https://login.microsoftonline.com/{CONFIG['tenant_id']}/oauth2/v2.0/token"
    data = {
        'grant_type': 'client_credentials',
        'client_id': CONFIG['client_id'],
        'client_secret': CONFIG['client_secret'],
        'scope': 'https://analysis.windows.net/powerbi/api/.default'
    }
    resp = requests.post(url, data=data, timeout=30)
    resp.raise_for_status()
    return resp.json()['access_token']


def execute_dax(token, query, dataset_id=None):
    ds = dataset_id or CONFIG['dataset_id']
    url = (
        f"https://api.powerbi.com/v1.0/myorg/groups/"
        f"{CONFIG['group_id']}/datasets/{ds}/executeQueries"
    )
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json'
    }
    body = {
        'queries': [{'query': query}],
        'serializerSettings': {'includeNulls': True}
    }
    resp = requests.post(url, json=body, headers=headers, timeout=120)
    resp.raise_for_status()
    return resp.json()


def clean_rows(rows):
    result = []
    for row in rows:
        clean = {}
        for k, v in row.items():
            short_key = k.split('[')[-1].rstrip(']') if '[' in k else k
            clean[short_key] = v
        result.append(clean)
    return result


# ════════════════════════════════════════
# ROTAS DE AUTENTICAÇÃO
# ════════════════════════════════════════

@app.route('/login', methods=['GET'])
def login_page():
    if 'user_id' in session:
        return redirect('/')
    return send_from_directory('.', 'login.html')


@app.route('/login', methods=['POST'])
def login_post():
    data = request.get_json() or {}
    email = data.get('email', '').strip().lower()
    senha = data.get('senha', '')

    if not email or not senha:
        return jsonify({'ok': False, 'error': 'Preencha e-mail e senha'}), 400

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, nome, password_hash, role, ativo, tipos_permitidos FROM auditoria_users WHERE email = %s",
            (email,)
        )
        user = cur.fetchone()
        cur.close()
        conn.close()
    except Exception as e:
        return jsonify({'ok': False, 'error': f'Erro de banco: {str(e)}'}), 500

    if not user:
        return jsonify({'ok': False, 'error': 'E-mail ou senha inválidos'}), 401

    uid, nome, pw_hash, role, ativo, tipos_permitidos = user

    if not ativo:
        return jsonify({'ok': False, 'error': 'Conta desativada. Contate o administrador.'}), 403

    if not check_password_hash(pw_hash, senha):
        return jsonify({'ok': False, 'error': 'E-mail ou senha inválidos'}), 401

    session['user_id']         = uid
    session['nome']            = nome
    session['role']            = role
    session['tipos_permitidos'] = tipos_permitidos or []
    return jsonify({'ok': True, 'redirect': '/'})


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


# ════════════════════════════════════════
# ROTAS PRINCIPAIS
# ════════════════════════════════════════

@app.route('/')
@login_required
def index():
    return send_from_directory('.', 'index.html')


@app.route('/admin')
@admin_required
def admin_page():
    return send_from_directory('.', 'admin.html')


@app.route('/tarifas')
@login_required
def tarifas_page():
    return send_from_directory('.', 'tarifas.html')


@app.route('/reuniao')
@admin_required
def reuniao_page():
    return send_from_directory('.', 'reuniao.html')


@app.route('/dre')
@admin_required
def dre_page():
    return send_from_directory('.', 'dre.html')


@app.route('/dre/despesas')
@admin_required
def dre_despesas_page():
    return send_from_directory('.', 'dre-despesas.html')


@app.route('/dre/conhecimentos')
@admin_required
def dre_conhecimentos_page():
    return send_from_directory('.', 'dre-conhecimentos.html')


@app.route('/embarques')
@login_required
def embarques_page():
    return send_from_directory('.', 'embarques.html')


@app.route('/embarques/novo')
@login_required
def embarques_novo_page():
    return send_from_directory('.', 'embarques-novo.html')


@app.route('/embarques/relatorio')
@login_required
def embarques_relatorio_page():
    return send_from_directory('.', 'embarques-relatorio.html')


@app.route('/embarques/<int:carga_id>/editar')
@login_required
def embarques_editar_page(carga_id):
    # A permissão é verificada na API ao buscar a carga; aqui só serve o HTML
    return send_from_directory('.', 'embarques-novo.html')


@app.route('/embarques/mapa')
@login_required
def embarques_mapa_page():
    return send_from_directory('.', 'mapa.html')


@app.route('/embarques/cargas/<int:carga_id>/mapa')
@login_required
def embarques_mapa_carga_page(carga_id):
    return send_from_directory('.', 'mapa-carga.html')


@app.route('/api/tarifas')
@login_required
def tarifas():
    try:
        token = get_token()
        result = execute_dax(token, "EVALUATE 'public tarifas_frete'")
        rows = result.get('results', [{}])[0].get('tables', [{}])[0].get('rows', [])
        data = clean_rows(rows)

        # Mantém apenas a última versão (maior versao_id) por cliente
        max_versao = {}
        for r in data:
            cliente = r.get('cliente_nome')
            v = r.get('versao_id')
            if cliente and v is not None:
                if cliente not in max_versao or v > max_versao[cliente]:
                    max_versao[cliente] = v

        data = [r for r in data if r.get('versao_id') == max_versao.get(r.get('cliente_nome'))]

        return jsonify({'ok': True, 'data': data, 'count': len(data)})

    except requests.exceptions.HTTPError as e:
        detail = ''
        try:
            detail = e.response.json()
        except Exception:
            detail = e.response.text
        return jsonify({'ok': False, 'error': str(e), 'detail': detail}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/me')
@login_required
def me():
    return jsonify({
        'ok':              True,
        'nome':            session.get('nome'),
        'role':            session.get('role'),
        'tipos_permitidos': session.get('tipos_permitidos', []),
    })


@app.route('/api/status')
@login_required
def status():
    missing = [k for k, v in CONFIG.items() if not v]
    if missing:
        return jsonify({'ok': False, 'missing': missing}), 400
    return jsonify({'ok': True})


@app.route('/api/auditoria')
@login_required
def auditoria():
    try:
        token = get_token()
        result = execute_dax(token, "EVALUATE 'Auditoria Receita'")
        rows = result.get('results', [{}])[0].get('tables', [{}])[0].get('rows', [])
        data = clean_rows(rows)

        # Filtrar por tipos permitidos
        tipos = session.get('tipos_permitidos', [])
        if tipos:
            tipos_lower = [t.lower() for t in tipos]
            data = [
                r for r in data
                if any(t in (r.get('Tipo Operacao') or '').lower() for t in tipos_lower)
            ]

        return jsonify({'ok': True, 'data': data, 'count': len(data)})

    except requests.exceptions.HTTPError as e:
        detail = ''
        try:
            detail = e.response.json()
        except Exception:
            detail = e.response.text
        return jsonify({'ok': False, 'error': str(e), 'detail': detail}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/dax', methods=['POST'])
@login_required
def dax_query():
    try:
        body = request.get_json()
        query = body.get('query', '')
        if not query:
            return jsonify({'ok': False, 'error': 'Query vazia'}), 400

        token = get_token()
        result = execute_dax(token, query)
        rows = result.get('results', [{}])[0].get('tables', [{}])[0].get('rows', [])
        data = clean_rows(rows)
        return jsonify({'ok': True, 'data': data, 'count': len(data)})

    except requests.exceptions.HTTPError as e:
        detail = ''
        try:
            detail = e.response.json()
        except Exception:
            detail = e.response.text
        return jsonify({'ok': False, 'error': str(e), 'detail': detail}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ════════════════════════════════════════
# ROTAS ADMIN — GERENCIAMENTO DE USUÁRIOS
# ════════════════════════════════════════

@app.route('/api/admin/users', methods=['GET'])
@admin_required
def admin_list_users():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, nome, email, role, ativo, tipos_permitidos, criado_em FROM auditoria_users ORDER BY criado_em")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        users = [
            {'id': r[0], 'nome': r[1], 'email': r[2], 'role': r[3], 'ativo': r[4],
             'tipos_permitidos': r[5] or [],
             'criado_em': r[6].strftime('%d/%m/%Y %H:%M') if r[6] else ''}
            for r in rows
        ]
        return jsonify({'ok': True, 'users': users})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


TIPOS_VALIDOS = {'Carreteiro', 'Agregado', 'Frota'}

@app.route('/api/admin/users', methods=['POST'])
@admin_required
def admin_create_user():
    data = request.get_json() or {}
    nome             = data.get('nome', '').strip()
    email            = data.get('email', '').strip().lower()
    senha            = data.get('senha', '')
    role             = data.get('role', 'viewer')
    tipos_permitidos = data.get('tipos_permitidos', list(TIPOS_VALIDOS))

    if not nome or not email or not senha:
        return jsonify({'ok': False, 'error': 'Nome, e-mail e senha são obrigatórios'}), 400
    if role not in ('admin', 'viewer'):
        return jsonify({'ok': False, 'error': 'Role inválido'}), 400
    tipos_permitidos = [t for t in tipos_permitidos if t in TIPOS_VALIDOS]
    if not tipos_permitidos:
        return jsonify({'ok': False, 'error': 'Selecione ao menos um tipo de operação'}), 400

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO auditoria_users (nome, email, password_hash, role, tipos_permitidos)
               VALUES (%s, %s, %s, %s, %s) RETURNING id""",
            (nome, email, generate_password_hash(senha), role, tipos_permitidos)
        )
        new_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'ok': True, 'id': new_id})
    except psycopg2.errors.UniqueViolation:
        return jsonify({'ok': False, 'error': 'E-mail já cadastrado'}), 409
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/admin/users/<int:uid>', methods=['PATCH'])
@admin_required
def admin_toggle_user(uid):
    data = request.get_json() or {}

    # Atualizar tipos_permitidos
    if 'tipos_permitidos' in data:
        tipos = [t for t in data['tipos_permitidos'] if t in TIPOS_VALIDOS]
        if not tipos:
            return jsonify({'ok': False, 'error': 'Selecione ao menos um tipo'}), 400
        try:
            conn = get_db()
            cur = conn.cursor()
            cur.execute("UPDATE auditoria_users SET tipos_permitidos = %s WHERE id = %s", (tipos, uid))
            conn.commit()
            cur.close()
            conn.close()
            return jsonify({'ok': True})
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)}), 500

    # Ativar/Desativar
    ativo = data.get('ativo')
    if ativo is None:
        return jsonify({'ok': False, 'error': 'Campo "ativo" ou "tipos_permitidos" obrigatório'}), 400
    if uid == session.get('user_id') and not ativo:
        return jsonify({'ok': False, 'error': 'Você não pode desativar sua própria conta'}), 400
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("UPDATE auditoria_users SET ativo = %s WHERE id = %s", (ativo, uid))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/admin/users/<int:uid>', methods=['DELETE'])
@admin_required
def admin_delete_user(uid):
    if uid == session.get('user_id'):
        return jsonify({'ok': False, 'error': 'Você não pode excluir sua própria conta'}), 400
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM auditoria_users WHERE id = %s", (uid,))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ════════════════════════════════════════
# ROTAS REUNIÃO — TRANSCRIÇÃO + ATA
# ════════════════════════════════════════

def _transcrever_com_assemblyai(caminho_arquivo, speakers_expected=None):
    import assemblyai as aai
    aai.settings.api_key = os.getenv('ASSEMBLYAI_API_KEY')
    config_args = dict(
        speech_models=['universal-3-pro', 'universal-2'],
        language_detection=True,
        speaker_labels=True,
    )
    if speakers_expected:
        config_args['speakers_expected'] = int(speakers_expected)
    config = aai.TranscriptionConfig(**config_args)
    transcriber = aai.Transcriber(config=config)
    transcript = transcriber.transcribe(caminho_arquivo)
    if transcript.status == aai.TranscriptStatus.error:
        raise Exception(f'AssemblyAI erro: {transcript.error}')
    if transcript.utterances:
        linhas = [f'Speaker {u.speaker}: {u.text}' for u in transcript.utterances]
        return '\n'.join(linhas)
    return transcript.text


def _gerar_ata(tema, transcricao):
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

    prompt_geracao = f"""Você é um assistente especializado em redigir atas de reunião para empresas de transporte rodoviário de cargas.

Contexto da empresa: transportadora com operações em múltiplos estados (SP, RJ, GO, ES, BA e outros), frota própria de carretas, motoristas agregados e carreteiros, clientes industriais (L'Oreal, Nestlé, Heinz, etc.), atuação com cargas spot e contratos fixos.

Tema da reunião: {tema}

Transcrição:
{transcricao}

Instruções obrigatórias:
- Speakers identificados (Speaker A, Speaker B, etc.) = participantes reais da reunião. NENHUM outro nome deve aparecer como participante
- Tente inferir o nome real de cada Speaker pelo contexto (ex: se alguém chama "João" e Speaker A responde, provavelmente é João). Se não for possível inferir, use "Speaker A"
- Nomes citados na conversa (motoristas, coordenadores, clientes) aparecem APENAS no corpo do texto, nunca como participantes
- Nomes como "Martins" podem ser terminais/clientes — use o contexto para distinguir pessoas de empresas/localidades
- Prazos: use apenas datas mencionadas explicitamente na transcrição. Se não houver data, escreva "A definir". NUNCA invente datas
- Termos do setor são válidos: carreta, frota, agregado, carreteiro, spot, frete líquido, recuperação judicial (RJ), diária, escala, etc.
- Linguagem: profissional e objetiva, sem excesso de formalidade

Estrutura obrigatória da ata (nesta ordem):
1. **Cabeçalho** — data/hora se mencionada, senão deixar em branco; tema; local se mencionado
2. **Participantes** — apenas os Speakers com nome inferido ou label (ex: "João (Speaker A)")
3. **Resumo Executivo** — 3 a 5 decisões/pontos principais em bullets, para leitura rápida
4. **Encaminhamentos** — tabela com: Encaminhamento | Responsável | Prazo
5. **Pauta abordada** — tópicos discutidos
6. **Discussões e Deliberações** — detalhamento por tópico
7. **Próximos Passos** — se houver"""

    primeira_ata = client.chat.completions.create(
        model='gpt-4.1-mini',
        messages=[{'role': 'user', 'content': prompt_geracao}],
        max_tokens=4000
    ).choices[0].message.content

    prompt_revisao = f"""Revise a ata de reunião de uma transportadora abaixo. Corrija especificamente:

1. **Participantes incorretos** — remova qualquer nome que não seja um Speaker identificado na transcrição original
2. **Datas inventadas** — substitua por "A definir" qualquer prazo que não foi mencionado explicitamente na reunião
3. **Pessoas vs empresas/terminais** — confirme que "Martins", "Raiz", "Start" e similares estão como clientes/terminais, não como pessoas
4. **Encaminhamentos sem responsável real** — se o responsável for desconhecido, use o Speaker mais provável pelo contexto
5. **Repetições e redundâncias** entre seções
6. **Ordem da estrutura** — garanta que Resumo Executivo e Encaminhamentos vêm ANTES das discussões detalhadas

Retorne apenas a ata final revisada, sem comentários.

Ata a revisar:
{primeira_ata}"""

    ata_final = client.chat.completions.create(
        model='gpt-4.1-mini',
        messages=[{'role': 'user', 'content': prompt_revisao}],
        max_tokens=4000
    ).choices[0].message.content

    return ata_final


@app.route('/api/reuniao/processar', methods=['POST'])
@admin_required
def processar_reuniao():
    if 'audio' not in request.files:
        return jsonify({'ok': False, 'error': 'Arquivo de áudio não enviado'}), 400

    audio_file = request.files['audio']
    tema = request.form.get('tema', 'Reunião').strip()
    participantes = request.form.get('participantes')

    ext = os.path.splitext(audio_file.filename)[1].lower() or '.mp3'
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        audio_file.save(tmp.name)
        tmp_path = tmp.name

    try:
        transcricao = _transcrever_com_assemblyai(tmp_path, speakers_expected=participantes)
        ata = _gerar_ata(tema, transcricao)
        return jsonify({'ok': True, 'ata': ata, 'transcricao': transcricao})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


@app.route('/api/reuniao/exportar', methods=['POST'])
@admin_required
def exportar_reuniao():
    data = request.get_json() or {}
    ata = data.get('ata', '')
    tema = data.get('tema', 'Ata de Reunião')
    formato = data.get('formato', 'docx').lower()

    if not ata:
        return jsonify({'ok': False, 'error': 'Conteúdo da ata não informado'}), 400

    def _parse_md_linha(texto):
        import re
        texto = re.sub(r'\*\*(.+?)\*\*', r'\1', texto)
        texto = re.sub(r'\*(.+?)\*', r'\1', texto)
        return texto.strip()

    def _e_separador_tabela(linha):
        return all(c in '|- :' for c in linha) and '|' in linha

    def _extrair_tabela(linhas, idx):
        rows = []
        while idx < len(linhas) and '|' in linhas[idx]:
            celulas = [c.strip() for c in linhas[idx].strip().strip('|').split('|')]
            if not _e_separador_tabela(linhas[idx]):
                rows.append(celulas)
            idx += 1
        return rows, idx

    if formato == 'docx':
        from docx import Document
        from docx.shared import Pt, RGBColor
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement

        doc = Document()
        doc.add_heading(tema, level=1)
        linhas = ata.split('\n')
        i = 0
        while i < len(linhas):
            linha = linhas[i].strip()
            if not linha or linha == '---':
                i += 1
                continue
            if linha.startswith('### '):
                doc.add_heading(_parse_md_linha(linha[4:]), level=3)
            elif linha.startswith('## '):
                doc.add_heading(_parse_md_linha(linha[3:]), level=2)
            elif linha.startswith('# '):
                doc.add_heading(_parse_md_linha(linha[2:]), level=1)
            elif '|' in linha and not _e_separador_tabela(linha):
                rows, i = _extrair_tabela(linhas, i)
                if rows:
                    table = doc.add_table(rows=len(rows), cols=len(rows[0]))
                    table.style = 'Table Grid'
                    for r_idx, row in enumerate(rows):
                        for c_idx, cell_text in enumerate(row):
                            cell = table.cell(r_idx, c_idx)
                            cell.text = _parse_md_linha(cell_text)
                            if r_idx == 0:
                                for run in cell.paragraphs[0].runs:
                                    run.bold = True
                continue
            elif linha.startswith('- ') or linha.startswith('* '):
                doc.add_paragraph(_parse_md_linha(linha[2:]), style='List Bullet')
            else:
                p = doc.add_paragraph()
                partes = linha.split('**')
                for j, parte in enumerate(partes):
                    run = p.add_run(parte)
                    run.bold = (j % 2 == 1)
            i += 1

        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
        nome_arquivo = f"{tema.replace(' ', '_')}.docx"
        return send_file(buf, mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                         as_attachment=True, download_name=nome_arquivo)

    elif formato == 'pdf':
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        from reportlab.lib.enums import TA_LEFT

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                leftMargin=2.5*cm, rightMargin=2.5*cm,
                                topMargin=2.5*cm, bottomMargin=2.5*cm)
        styles = getSampleStyleSheet()
        s_normal  = ParagraphStyle('n', parent=styles['Normal'], fontSize=10, leading=15, spaceAfter=3)
        s_h1      = ParagraphStyle('h1', parent=styles['Heading1'], fontSize=15, spaceAfter=8)
        s_h2      = ParagraphStyle('h2', parent=styles['Heading2'], fontSize=12, spaceAfter=6, spaceBefore=10)
        s_h3      = ParagraphStyle('h3', parent=styles['Heading3'], fontSize=11, spaceAfter=4, spaceBefore=8)
        s_bullet  = ParagraphStyle('b', parent=s_normal, leftIndent=14, bulletIndent=4)
        s_title   = ParagraphStyle('t', parent=styles['Title'], fontSize=16, spaceAfter=12)
        s_cell    = ParagraphStyle('c', parent=s_normal, fontSize=9, leading=13)
        s_cell_h  = ParagraphStyle('ch', parent=s_cell, fontName='Helvetica-Bold')

        story = [Paragraph(tema, s_title), Spacer(1, 8)]
        linhas = ata.split('\n')
        i = 0
        while i < len(linhas):
            linha = linhas[i].strip()
            if not linha or linha == '---':
                story.append(Spacer(1, 6))
                i += 1
                continue
            if linha.startswith('### '):
                story.append(Paragraph(_parse_md_linha(linha[4:]), s_h3))
            elif linha.startswith('## '):
                story.append(Paragraph(_parse_md_linha(linha[3:]), s_h2))
            elif linha.startswith('# '):
                story.append(Paragraph(_parse_md_linha(linha[2:]), s_h1))
            elif '|' in linha and not _e_separador_tabela(linha):
                rows, i = _extrair_tabela(linhas, i)
                if rows:
                    col_w = (A4[0] - 5*cm) / max(len(rows[0]), 1)
                    data = []
                    for r_idx, row in enumerate(rows):
                        estilo = s_cell_h if r_idx == 0 else s_cell
                        data.append([Paragraph(_parse_md_linha(c), estilo) for c in row])
                    t = Table(data, colWidths=[col_w]*len(rows[0]))
                    t.setStyle(TableStyle([
                        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1a2235')),
                        ('TEXTCOLOR', (0,0), (-1,0), colors.HexColor('#94a3b8')),
                        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#1e293b')),
                        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#f8fafc')]),
                        ('VALIGN', (0,0), (-1,-1), 'TOP'),
                        ('PADDING', (0,0), (-1,-1), 6),
                    ]))
                    story.append(t)
                    story.append(Spacer(1, 8))
                continue
            elif linha.startswith('- ') or linha.startswith('* '):
                story.append(Paragraph(f'• {_parse_md_linha(linha[2:])}', s_bullet))
            else:
                import re
                texto_html = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', linha)
                story.append(Paragraph(texto_html, s_normal))
            i += 1

        doc.build(story)
        buf.seek(0)
        nome_arquivo = f"{tema.replace(' ', '_')}.pdf"
        return send_file(buf, mimetype='application/pdf',
                         as_attachment=True, download_name=nome_arquivo)

    return jsonify({'ok': False, 'error': 'Formato inválido. Use docx ou pdf'}), 400


# ════════════════════════════════════════
# DRE — Demonstrativo do Resultado do Exercício
# ════════════════════════════════════════

# Mapeamento de cada descr_evento para (Grupo, Subgrupo) — traduzido do DAX Mapa_DRE
MAPA_DRE = {
    # OPERACIONAL — FRETES
    'FRETE COLETA/ENTREGA DE VEICULOS TERCEIROS': ('Operacional', 'Fretes'),
    'FRETE TRANSFERENCIA C/ AGREGADOS': ('Operacional', 'Fretes'),
    'FRETE TRANSFERENCIA C/ TERCEIROS': ('Operacional', 'Fretes'),
    'FRETE FLUVIAL': ('Operacional', 'Fretes'),
    # COMBUSTÍVEL
    'COMBUSTIVEIS E LUBRIFICANTES': ('Operacional', 'Combustível'),
    # MANUTENÇÃO
    'MANUTENCAO E CONSERVACAO DE VEICULOS': ('Operacional', 'Manutenção'),
    'SERVICO MANUTENCAO CAVALOS': ('Operacional', 'Manutenção'),
    'SERVICOS MANUTENCAO CARRETA': ('Operacional', 'Manutenção'),
    'PECAS MANUTENCAO CAVALO': ('Operacional', 'Manutenção'),
    'PECAS MANUTENCAO CARRETA': ('Operacional', 'Manutenção'),
    'PECAS E ACESSORIOS': ('Operacional', 'Manutenção'),
    # PNEUS
    'PNEUS E CAMARAS': ('Operacional', 'Pneus'),
    'RECAPAGEM DE PNEUS': ('Operacional', 'Pneus'),
    # DESLOCAMENTO
    'PEDAGIOS': ('Operacional', 'Deslocamento'),
    'ESTACIONAMENTOS': ('Operacional', 'Deslocamento'),
    'DIARIAS': ('Operacional', 'Deslocamento'),
    # SEGUROS
    'SEGURO DE CARGAS': ('Operacional', 'Seguros'),
    'SEGURO DE VEICULOS': ('Operacional', 'Seguros'),
    'GERENCIAMENTO DE RISCO': ('Operacional', 'Seguros'),
    # MÃO DE OBRA OPERACIONAL
    'SALARIO MENSAL - OPERACIONAL': ('Operacional', 'Mão de Obra'),
    'SALARIO MENSAL - APOIO OPERACIONAL': ('Operacional', 'Mão de Obra'),
    'ADIANTAMENTO SALARIAL OPERACIONAL': ('Operacional', 'Mão de Obra'),
    'ADIANTAMENTO SALARIAL - APOIO OPERACIONAL': ('Operacional', 'Mão de Obra'),
    'SALARIOS': ('Operacional', 'Mão de Obra'),
    'ADIANTAMENTO SALARIOS': ('Operacional', 'Mão de Obra'),
    'RPA- RECIBO DE PAGAMENTO AUTONOMO': ('Operacional', 'Mão de Obra'),
    'ADIANTAMENTO SALARIAL - ADMINISTRATIVO/ APOIO': ('Operacional', 'Mão de Obra'),
    # OUTROS OPERACIONAIS
    'CARGA E DESCARGA C/ TERCEIROS': ('Operacional', 'Outros'),
    'INDENIZACAO DE MERCADORIAS - ONUS DE CONTRATO': ('Operacional', 'Outros'),
    'LOCACAO DE CARRETA': ('Operacional', 'Outros'),
    'DESPESAS PJ - OPERACIONAL': ('Operacional', 'Outros'),
    'OUTRAS DESPESAS OPERACIONAIS': ('Operacional', 'Outros'),
    'ACERTO CONTA FORNECEDOR': ('Operacional', 'Outros'),
    # ADMINISTRATIVO — MÃO DE OBRA
    'SALARIOS ADMINISTRATIVOS - APOIO': ('Administrativo', 'Mão de Obra'),
    'PRO-LABORE': ('Administrativo', 'Mão de Obra'),
    'FERIAS': ('Administrativo', 'Mão de Obra'),
    'RESCISOES': ('Administrativo', 'Mão de Obra'),
    '13O SALARIOS': ('Administrativo', 'Mão de Obra'),
    'PENSAO ALIMENTICIA': ('Administrativo', 'Mão de Obra'),
    # ENCARGOS
    'INSS': ('Administrativo', 'Encargos'),
    'FGTS': ('Administrativo', 'Encargos'),
    'IRRF- AUTONOMOS - CLT': ('Administrativo', 'Encargos'),
    # ESTRUTURA
    'ALUGUEL DO IMOVEL': ('Administrativo', 'Estrutura'),
    'ENERGIA ELETRICA': ('Administrativo', 'Estrutura'),
    'AGUA E ESGOTO': ('Administrativo', 'Estrutura'),
    'MANUTENCAO E CONSERVACAO DO IMOVEL': ('Administrativo', 'Estrutura'),
    'DESPESAS COM HIGIENE E LIMPEZA': ('Administrativo', 'Estrutura'),
    # SISTEMAS
    'SOFTWARE E LICENCAS': ('Administrativo', 'Sistemas'),
    'TELEFONIA E COMUNICACAO DE DADOS': ('Administrativo', 'Sistemas'),
    'DESPESAS CONTABEIS': ('Administrativo', 'Sistemas'),
    'DESPESAS PJ - APOIO ADMINISTRATIVO E COMERCIAL': ('Administrativo', 'Sistemas'),
    'OUTROS SERVICOS PJ': ('Administrativo', 'Sistemas'),
    # JURÍDICO
    'DESPESAS JURIDICAS - INDENIZAACAOES TRABALHISTAS': ('Administrativo', 'Jurídico'),
    'DESPESAS SINDICAIS': ('Administrativo', 'Jurídico'),
    # SAÚDE E SEGURANÇA
    'PLANO DE SAUDE': ('Administrativo', 'Saúde'),
    'SAUDE OCUPACIONAL - EXAMES LTCAT PPRA PCMSO': ('Administrativo', 'Saúde'),
    'SEGURO DE VIDA': ('Administrativo', 'Saúde'),
    'SEGURANCA DO TRABALHO - BRIGADA - EPIS - UNIFORME': ('Administrativo', 'Segurança'),
    'SEGURANCA E VIGILANCIA PATRIMONIAL': ('Administrativo', 'Segurança'),
    'SEGURO DE IMOVEIS': ('Administrativo', 'Segurança'),
    # TAXAS
    'LICENCAS- SUATRANS - ANVISA- BOMBEIRO': ('Administrativo', 'Taxas'),
    'TAXAS PREFEITURA - TAXA INCENDIO RENOVACAO': ('Administrativo', 'Taxas'),
    'IPVA': ('Administrativo', 'Taxas'),
    'IPTU': ('Administrativo', 'Taxas'),
    'MULTAS E INFRACOES JUNTO AOS ORGAOS FEDERAIS MUNIC': ('Administrativo', 'Taxas'),
    'INFRACOES DE TRANSITO': ('Administrativo', 'Taxas'),
    'LICENCIAMENTO DE VEICULOS': ('Administrativo', 'Taxas'),
    # COMERCIAL
    'COMISSAO AGENTE': ('Administrativo', 'Comercial'),
    'DESPESA COMERCIAL - VIAGENS E RELATORIO DESPESA': ('Administrativo', 'Comercial'),
    'CORREIOS E TELEGRAFOS': ('Administrativo', 'Comercial'),
    'BRINDES DOACOES CONFRATERNIZACOES': ('Administrativo', 'Comercial'),
    # BENEFÍCIOS
    'VALE REFEICAO': ('Administrativo', 'Benefícios'),
    'VALE TRANSPORTE': ('Administrativo', 'Benefícios'),
    'ALIMENTACAO': ('Administrativo', 'Benefícios'),
    # OUTROS ADM
    'DESPACHANTE': ('Administrativo', 'Outros'),
    'MATERIAL DE ESCRITORIO': ('Administrativo', 'Outros'),
    'TARIFA - PEF CTRB': ('Administrativo', 'Outros'),
    'TREINAMENTO DESENVOLVIMENTO BENEFICIOS': ('Administrativo', 'Outros'),
    # FINANCEIRO
    'EMPRESTIMOS': ('Financeiro', 'Dívida'),
    'CAPITAL DE GIRO': ('Financeiro', 'Dívida'),
    'JUROS E ENCARGOS': ('Financeiro', 'Custos Financeiros'),
    'IOF': ('Financeiro', 'Custos Financeiros'),
    'TARIFA BANCARIA': ('Financeiro', 'Custos Financeiros'),
    # IMPOSTOS
    'IR': ('Impostos', 'Impostos'),
    'CSLL': ('Impostos', 'Impostos'),
    # DEDUÇÕES
    'ICMS': ('Deduções', 'Deduções'),
    'PIS': ('Deduções', 'Deduções'),
    'COFINS': ('Deduções', 'Deduções'),
    'ISS': ('Deduções', 'Deduções'),
    # INVESTIMENTOS
    'ATIVO IMOBILIZADO - IMOVEIS': ('Investimento', 'Investimentos'),
    'ATIVO IMOBILIZADO- VEICULOS': ('Investimento', 'Investimentos'),
    'INVESTIMENTO - CONSORCIO': ('Investimento', 'Investimentos'),
    'INVESTIMENTO- CDC': ('Investimento', 'Investimentos'),
    'INVESTIMENTO- FINAME': ('Investimento', 'Investimentos'),
    # RETIRADAS
    'RETIRADA CLEIVON': ('Retirada', 'Retirada'),
    'RETIRADA SOCIOS': ('Retirada', 'Retirada'),
    'RETIRADA PATRICIA': ('Retirada', 'Retirada'),
    'MANUTENCAO DE MAQUINAS E EQUIPAMENTOS': ('Retirada', 'Retirada'),
}

# Filtragem das tabelas (via Power BI DAX, igual /api/tarifas e /api/auditoria):
# - despesas: filtra pela coluna REF (formato 'YYYY/MM') — competência
# - conhecimentos: filtra por data_autorizacao


def _dre_zerado(receita_bruta=0.0):
    return {
        'receita_bruta': receita_bruta, 'deducoes': 0.0, 'receita_liquida': receita_bruta,
        'custo_operacional': 0.0, 'despesas_administrativas': 0.0, 'ebitda': receita_bruta,
        'despesas_financeiras': 0.0, 'lair': receita_bruta, 'impostos': 0.0,
        'lucro_liquido': receita_bruta, 'investimentos': 0.0, 'pos_investimento': receita_bruta,
        'retiradas': 0.0, 'resultado_final': receita_bruta,
    }


def _gerar_refs_periodo(start_date, end_date):
    """Gera lista de strings YYYY/MM cobrindo o período."""
    refs = []
    cur_y, cur_m = start_date.year, start_date.month
    end_y, end_m = end_date.year, end_date.month
    while (cur_y, cur_m) <= (end_y, end_m):
        refs.append(f"{cur_y:04d}/{cur_m:02d}")
        cur_m += 1
        if cur_m > 12:
            cur_m = 1
            cur_y += 1
    return refs

DRE_LINHAS = [
    ('Receita Bruta',                'Subtotal', 'receita_bruta'),
    ('(-) Deduções',                 'Grupo',    'deducoes'),
    ('= Receita Líquida',            'Subtotal', 'receita_liquida'),
    ('(-) Custo Operacional',        'Grupo',    'custo_operacional'),
    ('(-) Despesas Administrativas', 'Grupo',    'despesas_administrativas'),
    ('= EBITDA',                     'Subtotal', 'ebitda'),
    ('(-) Despesas Financeiras',     'Grupo',    'despesas_financeiras'),
    ('= LAIR',                       'Subtotal', 'lair'),
    ('(-) Impostos',                 'Grupo',    'impostos'),
    ('= Lucro Líquido',              'Subtotal', 'lucro_liquido'),
    ('(-) Investimentos',            'Grupo',    'investimentos'),
    ('= Pós Investimento',           'Subtotal', 'pos_investimento'),
    ('(-) Retiradas',                'Grupo',    'retiradas'),
    ('= Resultado Final',            'Subtotal', 'resultado_final'),
]

# Mapeamento: linha da DRE → Grupo do MAPA_DRE (para drilldown)
DRE_LINHA_GRUPO = {
    '(-) Deduções':                 'Deduções',
    '(-) Custo Operacional':        'Operacional',
    '(-) Despesas Administrativas': 'Administrativo',
    '(-) Despesas Financeiras':     'Financeiro',
    '(-) Impostos':                 'Impostos',
    '(-) Investimentos':            'Investimento',
    '(-) Retiradas':                'Retirada',
}


def _dax_lista_refs(refs):
    """Formata lista Python ['2026/01','2026/02'] como literal DAX: { "2026/01", "2026/02" }"""
    return '{ ' + ', '.join(f'"{r}"' for r in refs) + ' }'


def _dax_data(d):
    """Formata date como literal DAX: DATE(2026,3,1)"""
    return f'DATE({d.year},{d.month},{d.day})'


def _calcular_dre_periodo(start_date, end_date):
    """Calcula uma DRE para um período fechado via Power BI DAX."""
    token = get_token()

    # Receita Bruta = SUM(valor_frete) filtrado por data_autorizacao
    dax_receita = (
        f'EVALUATE ROW("total", '
        f'CALCULATE(SUM(\'public conhecimentos_emitidos\'[valor_frete]), '
        f'FILTER(\'public conhecimentos_emitidos\', '
        f'\'public conhecimentos_emitidos\'[data_autorizacao] >= {_dax_data(start_date)} && '
        f'\'public conhecimentos_emitidos\'[data_autorizacao] <= {_dax_data(end_date)})))'
    )
    result = execute_dax(token, dax_receita, dataset_id=CONFIG['dre_dataset_id'])
    rows = result.get('results', [{}])[0].get('tables', [{}])[0].get('rows', [])
    receita_bruta = float((rows[0].get('[total]') if rows else 0) or 0)

    # Despesas filtradas por REF (YYYY/MM)
    refs = _gerar_refs_periodo(start_date, end_date)
    grupos = {'Deduções': 0.0, 'Operacional': 0.0, 'Administrativo': 0.0,
              'Financeiro': 0.0, 'Impostos': 0.0, 'Investimento': 0.0, 'Retirada': 0.0}
    if refs:
        dax_despesas = (
            f'EVALUATE CALCULATETABLE('
            f'SUMMARIZE(\'public consulta_despesas_477\', '
            f'\'public consulta_despesas_477\'[descr_evento], '
            f'"Total", SUM(\'public consulta_despesas_477\'[vlr_final])), '
            f'\'public consulta_despesas_477\'[REF] IN {_dax_lista_refs(refs)})'
        )
        result = execute_dax(token, dax_despesas, dataset_id=CONFIG['dre_dataset_id'])
        rows = result.get('results', [{}])[0].get('tables', [{}])[0].get('rows', [])
        data = clean_rows(rows)
        for r in data:
            descr = r.get('descr_evento')
            valor = float(r.get('Total') or 0)
            if descr in MAPA_DRE:
                grupo = MAPA_DRE[descr][0]
                grupos[grupo] += valor

    receita_liquida   = receita_bruta - grupos['Deduções']
    ebitda            = receita_liquida - grupos['Operacional'] - grupos['Administrativo']
    lair              = ebitda - grupos['Financeiro']
    lucro_liquido     = lair - grupos['Impostos']
    pos_investimento  = lucro_liquido - grupos['Investimento']
    resultado_final   = pos_investimento - grupos['Retirada']

    return {
        'receita_bruta':            receita_bruta,
        'deducoes':                 grupos['Deduções'],
        'receita_liquida':          receita_liquida,
        'custo_operacional':        grupos['Operacional'],
        'despesas_administrativas': grupos['Administrativo'],
        'ebitda':                   ebitda,
        'despesas_financeiras':     grupos['Financeiro'],
        'lair':                     lair,
        'impostos':                 grupos['Impostos'],
        'lucro_liquido':            lucro_liquido,
        'investimentos':            grupos['Investimento'],
        'pos_investimento':         pos_investimento,
        'retiradas':                grupos['Retirada'],
        'resultado_final':          resultado_final,
    }


def _parse_meses_param(meses_str):
    """'2025-01,2025-03,2026-01' → lista ordenada [(2025,1),(2025,3),(2026,1)]"""
    pares = []
    for m in meses_str.split(','):
        m = m.strip()
        if not m: continue
        y, mo = m.split('-')
        pares.append((int(y), int(mo)))
    return sorted(set(pares))


def _meses_para_periodos(pares):
    """[(2025,1),(2025,3)] → [(y, m, nome_curto, primeiro_dia, ultimo_dia), ...]"""
    from datetime import date
    from calendar import monthrange
    nomes_curtos = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun',
                    'jul', 'ago', 'set', 'out', 'nov', 'dez']
    result = []
    for y, m in pares:
        ult = monthrange(y, m)[1]
        nome = f"{nomes_curtos[m-1]}/{str(y)[2:]}"
        result.append((y, m, nome, date(y, m, 1), date(y, m, ult)))
    return result


def _iterar_meses(start_date, end_date):
    """Gera tuplas (ano, mes, nome_mes, primeiro_dia, ultimo_dia) entre as datas."""
    from datetime import date
    from calendar import monthrange
    nomes = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho',
             'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro']
    cur_y, cur_m = start_date.year, start_date.month
    end_y, end_m = end_date.year, end_date.month
    while (cur_y, cur_m) <= (end_y, end_m):
        ult_dia = monthrange(cur_y, cur_m)[1]
        primeiro = date(cur_y, cur_m, 1)
        ultimo = date(cur_y, cur_m, ult_dia)
        if primeiro < start_date: primeiro = start_date
        if ultimo > end_date: ultimo = end_date
        yield (cur_y, cur_m, nomes[cur_m - 1], primeiro, ultimo)
        cur_m += 1
        if cur_m > 12:
            cur_m = 1
            cur_y += 1


@app.route('/api/dre')
@admin_required
def api_dre():
    from datetime import datetime
    meses_param = request.args.get('meses')
    if meses_param:
        try:
            pares = _parse_meses_param(meses_param)
            if not pares:
                raise ValueError('lista vazia')
            meses = _meses_para_periodos(pares)
        except Exception:
            return jsonify({'ok': False, 'error': 'Parâmetro meses inválido. Use YYYY-MM,YYYY-MM,...'}), 400
    else:
        try:
            start = datetime.strptime(request.args.get('start'), '%Y-%m-%d').date()
            end   = datetime.strptime(request.args.get('end'), '%Y-%m-%d').date()
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'Informe meses=YYYY-MM,... ou start/end'}), 400
        meses = list(_iterar_meses(start, end))

    estrutura = [{'linha': l, 'tipo': t, 'key': k} for l, t, k in DRE_LINHAS]

    if len(meses) == 1:
        (_, _, _, prim, ult) = meses[0]
        totais = _calcular_dre_periodo(prim, ult)
        rb = totais['receita_bruta']
        for item in estrutura:
            v = totais[item['key']]
            item['valor'] = v
            item['pct'] = (v / rb) if (item['tipo'] == 'Subtotal' and rb) else None
        return jsonify({'ok': True, 'modo': 'acumulado', 'estrutura': estrutura})

    # Modo mensal
    totais_por_mes = []
    totais_geral = {k: 0.0 for _, _, k in DRE_LINHAS}
    for (_, _, nome, prim, ult) in meses:
        t = _calcular_dre_periodo(prim, ult)
        totais_por_mes.append({'nome': nome, 'totais': t})
        for k in totais_geral:
            totais_geral[k] += t[k]

    for item in estrutura:
        item['meses'] = []
        for tm in totais_por_mes:
            v = tm['totais'][item['key']]
            rb = tm['totais']['receita_bruta']
            item['meses'].append({
                'nome': tm['nome'],
                'valor': v,
                'pct': (v / rb) if (item['tipo'] == 'Subtotal' and rb) else None
            })
        v = totais_geral[item['key']]
        rb_total = totais_geral['receita_bruta']
        item['total'] = v
        item['total_pct'] = (v / rb_total) if (item['tipo'] == 'Subtotal' and rb_total) else None

    return jsonify({'ok': True, 'modo': 'mensal', 'meses': [m[2] for m in meses], 'estrutura': estrutura})


def _query_despesas_periodo(start, end, grupo=None, evento=None):
    """Filtra consulta_despesas_477 via DAX por REF (YYYY/MM), opcionalmente por grupo ou evento específico."""
    from datetime import datetime
    if isinstance(start, str):
        start = datetime.strptime(start, '%Y-%m-%d').date()
    if isinstance(end, str):
        end = datetime.strptime(end, '%Y-%m-%d').date()

    refs = _gerar_refs_periodo(start, end)
    if not refs:
        return [], []

    filtro_ref = f"'public consulta_despesas_477'[REF] IN {_dax_lista_refs(refs)}"

    if evento:
        dax = (
            f'EVALUATE FILTER(\'public consulta_despesas_477\', '
            f'{filtro_ref} && \'public consulta_despesas_477\'[descr_evento] = "{evento}")'
        )
    elif grupo:
        eventos = [e for e, (g, _) in MAPA_DRE.items() if g == grupo]
        if not eventos:
            return [], []
        lista_eventos = '{ ' + ', '.join(f'"{e}"' for e in eventos) + ' }'
        dax = (
            f'EVALUATE FILTER(\'public consulta_despesas_477\', '
            f'{filtro_ref} && \'public consulta_despesas_477\'[descr_evento] IN {lista_eventos})'
        )
    else:
        dax = f'EVALUATE FILTER(\'public consulta_despesas_477\', {filtro_ref})'

    token = get_token()
    result = execute_dax(token, dax, dataset_id=CONFIG['dre_dataset_id'])
    rows = result.get('results', [{}])[0].get('tables', [{}])[0].get('rows', [])
    data = clean_rows(rows)
    cols = list(data[0].keys()) if data else []
    return cols, data


def _query_conhecimentos_periodo(start, end):
    """Filtra conhecimentos_emitidos via DAX por data_autorizacao."""
    from datetime import datetime
    if isinstance(start, str):
        start = datetime.strptime(start, '%Y-%m-%d').date()
    if isinstance(end, str):
        end = datetime.strptime(end, '%Y-%m-%d').date()

    dax = (
        f'EVALUATE FILTER(\'public conhecimentos_emitidos\', '
        f'\'public conhecimentos_emitidos\'[data_autorizacao] >= {_dax_data(start)} && '
        f'\'public conhecimentos_emitidos\'[data_autorizacao] <= {_dax_data(end)})'
    )
    token = get_token()
    result = execute_dax(token, dax, dataset_id=CONFIG['dre_dataset_id'])
    rows = result.get('results', [{}])[0].get('tables', [{}])[0].get('rows', [])
    data = clean_rows(rows)
    cols = list(data[0].keys()) if data else []
    return cols, data


@app.route('/api/dre/detalhamento')
@admin_required
def api_dre_detalhamento():
    """Retorna despesas agrupadas por Subgrupo (com eventos dentro) para o período/lista de meses."""
    from datetime import datetime
    meses_param = request.args.get('meses')
    if meses_param:
        try:
            pares = _parse_meses_param(meses_param)
        except Exception:
            return jsonify({'ok': False, 'error': 'Parâmetro meses inválido'}), 400
        refs = [f"{y:04d}/{m:02d}" for y, m in pares]
    else:
        try:
            start = datetime.strptime(request.args.get('start'), '%Y-%m-%d').date()
            end   = datetime.strptime(request.args.get('end'), '%Y-%m-%d').date()
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'error': 'Datas inválidas'}), 400
        refs = _gerar_refs_periodo(start, end)

    if not refs:
        return jsonify({'ok': True, 'subgrupos': []})

    try:
        dax = (
            f'EVALUATE CALCULATETABLE('
            f'SUMMARIZE(\'public consulta_despesas_477\', '
            f'\'public consulta_despesas_477\'[descr_evento], '
            f'"Total", SUM(\'public consulta_despesas_477\'[vlr_final])), '
            f'\'public consulta_despesas_477\'[REF] IN {_dax_lista_refs(refs)})'
        )
        token = get_token()
        result = execute_dax(token, dax, dataset_id=CONFIG['dre_dataset_id'])
        rows = result.get('results', [{}])[0].get('tables', [{}])[0].get('rows', [])
        data = clean_rows(rows)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

    # Agrupa por Subgrupo
    subgrupos = {}
    for r in data:
        descr = r.get('descr_evento')
        valor = float(r.get('Total') or 0)
        if descr in MAPA_DRE:
            grupo, sub = MAPA_DRE[descr]
            if sub not in subgrupos:
                subgrupos[sub] = {'nome': sub, 'grupo': grupo, 'total': 0.0, 'eventos': []}
            subgrupos[sub]['total'] += valor
            subgrupos[sub]['eventos'].append({'descr_evento': descr, 'total': valor})

    # Ordena: subgrupos por nome, eventos por valor desc
    lista = sorted(subgrupos.values(), key=lambda x: x['nome'])
    for s in lista:
        s['eventos'].sort(key=lambda e: e['total'], reverse=True)

    total_geral = sum(s['total'] for s in lista)
    return jsonify({'ok': True, 'subgrupos': lista, 'total_geral': total_geral})


@app.route('/api/dre/despesas')
@admin_required
def api_dre_despesas():
    start = request.args.get('start')
    end = request.args.get('end')
    grupo = request.args.get('grupo')
    evento = request.args.get('evento')
    if not start or not end:
        return jsonify({'ok': False, 'error': 'Informe start e end (YYYY-MM-DD)'}), 400
    try:
        cols, data = _query_despesas_periodo(start, end, grupo, evento)
        return jsonify({'ok': True, 'columns': cols, 'data': data, 'count': len(data),
                        'grupo': grupo, 'evento': evento})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/dre/conhecimentos')
@admin_required
def api_dre_conhecimentos():
    start = request.args.get('start')
    end = request.args.get('end')
    if not start or not end:
        return jsonify({'ok': False, 'error': 'Informe start e end (YYYY-MM-DD)'}), 400
    try:
        cols, data = _query_conhecimentos_periodo(start, end)
        return jsonify({'ok': True, 'columns': cols, 'data': data, 'count': len(data)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


def _gerar_csv(cols, data):
    import csv
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, delimiter=';')
    writer.writeheader()
    for row in data:
        writer.writerow({c: ('' if row.get(c) is None else row.get(c)) for c in cols})
    csv_bytes = '﻿'.encode('utf-8') + buf.getvalue().encode('utf-8')
    return io.BytesIO(csv_bytes)


def _csv_linha(valores):
    """Formata lista de valores em uma linha CSV com separador ; e BOM-safe."""
    out = []
    for v in valores:
        if v is None:
            out.append('')
        else:
            s = str(v).replace('"', '""')
            if ';' in s or '"' in s or '\n' in s:
                s = f'"{s}"'
            out.append(s)
    return ';'.join(out) + '\n'


@app.route('/api/dre/despesas/csv')
@admin_required
def api_dre_despesas_csv():
    from datetime import datetime
    start = request.args.get('start')
    end = request.args.get('end')
    meses_param = request.args.get('meses')
    grupo = request.args.get('grupo')
    evento = request.args.get('evento')

    if meses_param:
        pares = _parse_meses_param(meses_param)
        if not pares:
            return jsonify({'ok': False, 'error': 'Parâmetro meses inválido'}), 400
        meses = _meses_para_periodos(pares)
        sorted_meses = sorted(pares)
        start = f"{sorted_meses[0][0]}-{sorted_meses[0][1]:02d}-01"
        end = f"{sorted_meses[-1][0]}-{sorted_meses[-1][1]:02d}"
    elif start and end:
        start_d = datetime.strptime(start, '%Y-%m-%d').date()
        end_d   = datetime.strptime(end, '%Y-%m-%d').date()
        meses = list(_iterar_meses(start_d, end_d))
    else:
        return jsonify({'ok': False, 'error': 'Informe meses ou start/end'}), 400

    sufixo = ('_' + evento.replace(' ', '_')[:30]) if evento else (('_' + grupo) if grupo else '')
    nome = f"despesas_{start}_{end}{sufixo}.csv"

    def gerar():
        yield '﻿'  # BOM para Excel reconhecer UTF-8
        token = get_token()
        cols = None
        for (y, m, _, _, _) in meses:
            ref = f"{y:04d}/{m:02d}"
            dax = f'EVALUATE FILTER(\'public consulta_despesas_477\', \'public consulta_despesas_477\'[REF] = "{ref}"'
            if evento:
                dax += f' && \'public consulta_despesas_477\'[descr_evento] = "{evento}"'
            elif grupo:
                eventos = [e for e, (g, _) in MAPA_DRE.items() if g == grupo]
                if eventos:
                    lista = '{ ' + ', '.join(f'"{e}"' for e in eventos) + ' }'
                    dax += f' && \'public consulta_despesas_477\'[descr_evento] IN {lista}'
            dax += ')'

            try:
                result = execute_dax(token, dax, dataset_id=CONFIG['dre_dataset_id'])
            except Exception:
                # Renovar token e tentar de novo
                token = get_token()
                result = execute_dax(token, dax, dataset_id=CONFIG['dre_dataset_id'])

            rows = result.get('results', [{}])[0].get('tables', [{}])[0].get('rows', [])
            data = clean_rows(rows)
            if not cols and data:
                cols = list(data[0].keys())
                yield _csv_linha(cols)
            for row in data:
                yield _csv_linha([row.get(c) for c in cols] if cols else [])

    return Response(
        stream_with_context(gerar()),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{nome}"'}
    )


@app.route('/api/dre/conhecimentos/csv')
@admin_required
def api_dre_conhecimentos_csv():
    from datetime import datetime
    start = request.args.get('start')
    end = request.args.get('end')
    meses_param = request.args.get('meses')

    if meses_param:
        pares = _parse_meses_param(meses_param)
        if not pares:
            return jsonify({'ok': False, 'error': 'Parâmetro meses inválido'}), 400
        meses = _meses_para_periodos(pares)
        sorted_meses = sorted(pares)
        start = f"{sorted_meses[0][0]}-{sorted_meses[0][1]:02d}-01"
        end = f"{sorted_meses[-1][0]}-{sorted_meses[-1][1]:02d}"
    elif start and end:
        start_d = datetime.strptime(start, '%Y-%m-%d').date()
        end_d   = datetime.strptime(end, '%Y-%m-%d').date()
        meses = list(_iterar_meses(start_d, end_d))
    else:
        return jsonify({'ok': False, 'error': 'Informe meses ou start/end'}), 400

    nome = f"conhecimentos_{start}_{end}.csv"

    def gerar():
        yield '﻿'
        token = get_token()
        cols = None
        for (_, _, _, prim, ult) in meses:
            dax = (
                f'EVALUATE FILTER(\'public conhecimentos_emitidos\', '
                f'\'public conhecimentos_emitidos\'[data_autorizacao] >= {_dax_data(prim)} && '
                f'\'public conhecimentos_emitidos\'[data_autorizacao] <= {_dax_data(ult)})'
            )
            try:
                result = execute_dax(token, dax, dataset_id=CONFIG['dre_dataset_id'])
            except Exception:
                token = get_token()
                result = execute_dax(token, dax, dataset_id=CONFIG['dre_dataset_id'])

            rows = result.get('results', [{}])[0].get('tables', [{}])[0].get('rows', [])
            data = clean_rows(rows)
            if not cols and data:
                cols = list(data[0].keys())
                yield _csv_linha(cols)
            for row in data:
                yield _csv_linha([row.get(c) for c in cols] if cols else [])

    return Response(
        stream_with_context(gerar()),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{nome}"'}
    )


# ════════════════════════════════════════
# CHAT IA — Analista Financeiro DRE
# ════════════════════════════════════════

def _fmt_brl(v):
    try:
        v = float(v or 0)
    except (TypeError, ValueError):
        return 'R$ 0,00'
    return f"R$ {v:,.2f}".replace(',', '_').replace('.', ',').replace('_', '.')


_DESCRICAO_PADRAO = {
    'unico': 'Mês único — análise pontual.',
    'contiguo': 'Período contíguo (meses consecutivos no mesmo ano) — analise como série temporal sequencial. Cite o período inteiro e variação MoM.',
    'mesmo_mes_varios_anos': 'COMPARATIVO ANUAL: o mesmo mês selecionado em vários anos (ex: Mai/21, Mai/22, ..., Mai/26). NÃO analise como se fosse só o último mês. Faça comparação ano-a-ano: identifique tendência (melhorou/piorou ao longo dos anos), mês com melhor/pior performance, evolução das margens.',
    'multi_anos_multi_meses': 'COMPARATIVO MISTO: múltiplos meses em múltiplos anos. Analise cada bloco de ano separadamente. Identifique padrões sazonais (mesmo mês comporta-se igual entre anos?) e tendências (cada ano melhor ou pior que o anterior?).',
    'esparso_mesmo_ano': 'MESES NÃO-CONSECUTIVOS NO MESMO ANO. Analise cada mês como ponto independente, não como série contínua.',
    'esparso': 'Seleção esparsa. Analise mês a mês.',
}


def _montar_prompt_chat(contexto):
    """Monta system prompt com dados financeiros estruturados (todos pré-calculados)."""
    partes = []
    periodo = contexto.get('periodo', [])
    modo = contexto.get('modo', 'acumulado')
    padrao = contexto.get('padrao', 'contiguo')
    partes.append(f"PERÍODO ANALISADO: {', '.join(periodo) if periodo else 'não informado'}")
    partes.append(f"MODO: {modo}")
    partes.append(f"PADRÃO DE SELEÇÃO: {padrao}")
    partes.append(f"COMO INTERPRETAR: {_DESCRICAO_PADRAO.get(padrao, '')}\n")

    dre = contexto.get('dre') or {}
    if modo == 'acumulado' and dre:
        partes.append("--- DRE (ACUMULADO) ---")
        labels = [
            ('receita_bruta',            'Receita Bruta'),
            ('deducoes',                 '(-) Deduções'),
            ('receita_liquida',          '= Receita Líquida'),
            ('custo_operacional',        '(-) Custo Operacional'),
            ('despesas_administrativas', '(-) Despesas Administrativas'),
            ('ebitda',                   '= EBITDA'),
            ('despesas_financeiras',     '(-) Despesas Financeiras'),
            ('lair',                     '= LAIR'),
            ('impostos',                 '(-) Impostos'),
            ('lucro_liquido',            '= Lucro Líquido'),
            ('investimentos',            '(-) Investimentos'),
            ('pos_investimento',         '= Pós Investimento'),
            ('retiradas',                '(-) Retiradas'),
            ('resultado_final',          '= Resultado Final'),
        ]
        for key, label in labels:
            if key in dre:
                partes.append(f"{label}: {_fmt_brl(dre[key])}")

    elif modo == 'mensal':
        dre_meses = contexto.get('dre_por_mes', [])
        partes.append("--- DRE POR MÊS ---")
        for d in dre_meses:
            partes.append(f"\n[{d.get('mes', '?')}]")
            for k, v in d.items():
                if k != 'mes' and isinstance(v, (int, float)):
                    partes.append(f"  {k}: {_fmt_brl(v)}")

    margens = contexto.get('margens_agregadas') or contexto.get('margens') or {}
    if margens:
        partes.append("\n--- MARGENS AGREGADAS DO PERÍODO TOTAL (%) ---")
        for k, v in margens.items():
            try:
                partes.append(f"{k.replace('_', ' ').title()}: {float(v):.1f}%".replace('.', ','))
            except (TypeError, ValueError):
                pass

    variacoes = contexto.get('variacao_ultimo_vs_anterior') or {}
    if variacoes:
        partes.append("\n--- VARIAÇÃO ENTRE ÚLTIMO MÊS E O ANTERIOR DA SELEÇÃO ---")
        for k, v in variacoes.items():
            try:
                seta = '↑' if v > 0 else ('↓' if v < 0 else '→')
                partes.append(f"{k}: {seta} {float(v):.1f}%".replace('.', ','))
            except (TypeError, ValueError):
                pass

    top_sub = contexto.get('top_subgrupos') or []
    if top_sub:
        partes.append("\n--- TOP SUBGRUPOS DE DESPESA ---")
        for s in top_sub:
            partes.append(f"- {s.get('nome')} ({s.get('grupo')}): {_fmt_brl(s.get('valor'))} ({s.get('pct', 0):.1f}%)".replace('.', ','))

    pareto = contexto.get('pareto_80') or []
    if pareto:
        partes.append(f"\n--- PARETO 80% ({len(pareto)} subgrupos respondem por 80% das despesas) ---")
        partes.append(', '.join(pareto))

    total_desp = contexto.get('total_despesas')
    if total_desp is not None:
        partes.append(f"\nTOTAL DE DESPESAS: {_fmt_brl(total_desp)}")

    dados_texto = '\n'.join(partes)

    return f"""Você é o Analista Financeiro da Rizza Transportes — uma transportadora rodoviária de cargas com operações em SP, RJ, GO, ES, BA. Você analisa DRE e despesas.

REGRAS INVIOLÁVEIS:
1. Use APENAS os números fornecidos abaixo — todos JÁ CALCULADOS. Não recalcule.
2. Não invente nada. Se a pergunta exigir dado que não foi enviado, diga "essa informação não está no período selecionado".
3. Máximo 3 parágrafos curtos. Diretor não lê longo.
4. Formate em **markdown**: use **negrito** para destacar números/conclusões críticas.
5. Sempre cite valores em R$ (formato brasileiro) e %.
6. Tom: direto, profissional, sem rodeios.
7. Se identificar problema (margem negativa, queda, custo alto), DESTAQUE em negrito.
8. Perguntas fora de finanças/DRE → responda: "Só consigo analisar dados financeiros da DRE."

REGRA CRÍTICA SOBRE SELEÇÕES MÚLTIPLAS:
- Quando "PADRÃO DE SELEÇÃO" for DIFERENTE de 'unico' ou 'contiguo', você DEVE percorrer TODOS os meses listados em "DRE POR MÊS" — não foque só no último mês.
- Em 'mesmo_mes_varios_anos' (ex: Mai/21 a Mai/26): trate como COMPARATIVO ANUAL. Identifique evolução, melhor/pior ano, tendência.
- Em 'multi_anos_multi_meses' (ex: Mar+Abr+Mai de 24/25/26): trate como COMPARATIVO MISTO. Compare blocos de ano, identifique padrões sazonais.
- Em 'esparso_mesmo_ano': analise cada mês como ponto independente.
- NUNCA responda como se o período fosse apenas o último mês quando há vários meses na seleção.

CONTEXTO DA EMPRESA:
- Operação "fretes-pesada": terceiriza muita carga (subgrupo Fretes domina ~38%)
- Margens saudáveis para o setor: EBITDA acima de 10%, Líquida acima de 5%
- Resultado negativo é alerta vermelho

DADOS DO PERÍODO ATUAL:
{dados_texto}
"""


@app.route('/api/chat-dre', methods=['POST'])
@admin_required
def chat_dre():
    from openai import OpenAI
    data = request.get_json() or {}
    pergunta = (data.get('pergunta') or '').strip()
    contexto = data.get('contexto') or {}
    historico = data.get('historico') or []

    if not pergunta:
        return jsonify({'ok': False, 'error': 'Pergunta vazia'}), 400

    system_prompt = _montar_prompt_chat(contexto)
    messages = [{'role': 'system', 'content': system_prompt}]
    for m in historico[-6:]:
        role = m.get('role')
        content = m.get('content', '')
        if role in ('user', 'assistant') and content:
            messages.append({'role': role, 'content': content})
    if not messages or messages[-1].get('content') != pergunta:
        messages.append({'role': 'user', 'content': pergunta})

    def gerar():
        try:
            client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
            stream = client.chat.completions.create(
                model='gpt-4.1-mini',
                messages=messages,
                max_tokens=800,
                temperature=0.3,
                stream=True,
            )
            for chunk in stream:
                if not chunk.choices: continue
                delta = chunk.choices[0].delta.content
                if delta:
                    yield f"data: {json.dumps({'token': delta})}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'erro': str(e)})}\n\n"

    return Response(stream_with_context(gerar()),
                    mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


# ════════════════════════════════════════════════════════════════════════
# EMBARQUES — Módulo operacional de lançamento de cargas
# ════════════════════════════════════════════════════════════════════════

# Cache em memória (5min) para motoristas/veículos vindos do Power BI
_EMBARQUES_CACHE = {}
_CACHE_TTL_SEG = 300

def _cache_get(key):
    entry = _EMBARQUES_CACHE.get(key)
    if entry and (time.time() - entry['ts']) < _CACHE_TTL_SEG:
        return entry['data']
    return None

def _cache_set(key, data):
    _EMBARQUES_CACHE[key] = {'data': data, 'ts': time.time()}


def _eh_rizza(proprietario):
    """Identifica se o proprietário é Rizza (busca parcial case-insensitive)."""
    return 'RIZZA' in (proprietario or '').upper()


def _pode_editar_carga(criado_por_id):
    """Admin ou quem criou a carga pode editar."""
    if session.get('role') == 'admin':
        return True
    return session.get('user_id') == criado_por_id


def _classifica_tipo_operacao(cavalo_eh_rizza, carreta_eh_rizza):
    """Tipo de operação esperado conforme proprietários do cavalo e carreta1."""
    if cavalo_eh_rizza and carreta_eh_rizza:
        return 'Frota'
    if cavalo_eh_rizza or carreta_eh_rizza:
        return 'Agregado'
    return 'Terceiro'


def _pick(row, *keys):
    """Pega o primeiro valor não-vazio entre variações de nome de coluna."""
    for k in keys:
        v = row.get(k)
        if v not in (None, ''):
            return v
    return None


def _csv_linha_embarques(valores):
    """Wrapper local em torno de _csv_linha para clareza."""
    return _csv_linha(valores)


def _buscar_conflitos(cpf, placas, exclude_id=0):
    """Retorna lista de conflitos com cargas ativas (Aberta/Em rota).
       'placas' é lista de strings uppercase (cavalo, carreta1, carreta2 — sem nulls).
       'exclude_id' permite ignorar a própria carga ao editar."""
    placas = [p for p in (placas or []) if p]
    cpf = (cpf or '').strip()
    if not cpf and not placas:
        return []
    conn = get_db(); cur = conn.cursor()
    try:
        conflitos = []

        if cpf:
            cur.execute("""
                SELECT id, numero, status, data_carregamento, motorista_nome
                FROM embarques_cargas
                WHERE motorista_cpf = %s
                  AND status IN ('Aberta', 'Em rota')
                  AND id <> %s
                ORDER BY data_carregamento DESC
                LIMIT 5
            """, (cpf, exclude_id))
            for r in cur.fetchall():
                conflitos.append({
                    'tipo': 'motorista',
                    'recurso': r[4] or cpf,
                    'carga_id': r[0],
                    'numero': r[1],
                    'status': r[2],
                    'data_carregamento': r[3].isoformat() if r[3] else None,
                })

        if placas:
            ph = ','.join(['%s'] * len(placas))
            cur.execute(f"""
                SELECT id, numero, status, data_carregamento,
                       cavalo_placa, carreta1_placa, carreta2_placa
                FROM embarques_cargas
                WHERE status IN ('Aberta', 'Em rota')
                  AND id <> %s
                  AND (cavalo_placa IN ({ph})
                       OR carreta1_placa IN ({ph})
                       OR carreta2_placa IN ({ph}))
                ORDER BY data_carregamento DESC
                LIMIT 10
            """, (exclude_id, *placas, *placas, *placas))
            for r in cur.fetchall():
                cid, num, st, dt, cav, c1, c2 = r
                dt_iso = dt.isoformat() if dt else None
                for placa in placas:
                    if cav == placa:
                        conflitos.append({'tipo': 'cavalo',  'recurso': placa, 'carga_id': cid, 'numero': num, 'status': st, 'data_carregamento': dt_iso})
                    if c1 == placa or c2 == placa:
                        conflitos.append({'tipo': 'carreta', 'recurso': placa, 'carga_id': cid, 'numero': num, 'status': st, 'data_carregamento': dt_iso})
        return conflitos
    finally:
        cur.close(); conn.close()


@app.route('/api/embarques/conflitos')
@login_required
def api_embarques_conflitos():
    cpf = (request.args.get('cpf') or '').strip()
    placas_raw = (request.args.get('placas') or '').strip()
    placas = [p.strip().upper() for p in placas_raw.split(',') if p.strip()]
    try:
        exclude_id = int(request.args.get('exclude_id') or 0)
    except (TypeError, ValueError):
        exclude_id = 0
    try:
        data = _buscar_conflitos(cpf, placas, exclude_id)
        return jsonify({'ok': True, 'data': data, 'count': len(data)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ── Leitura DAX: motoristas ─────────────────────────────────────────────
@app.route('/api/embarques/motoristas')
@login_required
def api_embarques_motoristas():
    if request.args.get('refresh') != '1':
        cached = _cache_get('motoristas')
        if cached is not None:
            return jsonify({'ok': True, 'data': cached, 'count': len(cached), 'cached': True})
    try:
        token = get_token()
        result = execute_dax(token, "EVALUATE 'public motoristas_047'")
        rows = result.get('results', [{}])[0].get('tables', [{}])[0].get('rows', [])
        data = clean_rows(rows)

        normalizados = []
        for r in data:
            nome = _pick(r, 'nome', 'Nome', 'NOME')
            cpf  = _pick(r, 'cpf', 'CPF', 'Cpf')
            tel  = _pick(r, 'telefone', 'Telefone', 'TELEFONE', 'celular', 'Celular')
            if not nome or not cpf:
                continue
            normalizados.append({
                'nome':      str(nome).strip(),
                'cpf':       str(cpf).strip(),
                'telefone':  str(tel).strip() if tel else None,
            })
        # Dedup por CPF (única chave confiável)
        vistos = {}
        for m in normalizados:
            vistos[m['cpf']] = m
        final = sorted(vistos.values(), key=lambda x: x['nome'])

        _cache_set('motoristas', final)
        return jsonify({'ok': True, 'data': final, 'count': len(final), 'cached': False})

    except requests.exceptions.HTTPError as e:
        detail = ''
        try: detail = e.response.json()
        except Exception: detail = e.response.text
        return jsonify({'ok': False, 'error': str(e), 'detail': detail}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ── Leitura DAX: veículos ───────────────────────────────────────────────
@app.route('/api/embarques/veiculos')
@login_required
def api_embarques_veiculos():
    if request.args.get('refresh') != '1':
        cached = _cache_get('veiculos')
        if cached is not None:
            return jsonify({'ok': True, 'data': cached, 'count': len(cached), 'cached': True})
    try:
        token = get_token()
        # EVALUATE simples — filtro feito em Python (mais robusto que IN no DAX)
        result = execute_dax(token, "EVALUATE 'public veiculos_045'")
        rows = result.get('results', [{}])[0].get('tables', [{}])[0].get('rows', [])
        data = clean_rows(rows)

        tipos_map = {'CAVALO': 'Cavalo', 'CARRETA': 'Carreta', 'TRUCK': 'Truck'}
        normalizados = []
        for r in data:
            placa        = _pick(r, 'placa', 'PLACA', 'Placa')
            tipo         = _pick(r, 'TIPO', 'tipo', 'Tipo')
            marca        = _pick(r, 'marca', 'MARCA', 'Marca')
            modelo       = _pick(r, 'modelo', 'MODELO', 'Modelo')
            carroceria   = _pick(r, 'carroceria', 'CARROCERIA', 'Carroceria')
            proprietario = _pick(r, 'proprietario', 'PROPRIETARIO', 'Proprietario', 'proprietário', 'Proprietário')
            if not placa or not tipo:
                continue
            tipo_norm = tipos_map.get(str(tipo).strip().upper())
            if not tipo_norm:
                continue
            partes = [str(marca or '').strip(), str(modelo or '').strip()]
            marca_modelo = ' '.join(p for p in partes if p) or None
            normalizados.append({
                'placa':        str(placa).strip().upper(),
                'tipo':         tipo_norm,
                'marca_modelo': marca_modelo,
                'carroceria':   str(carroceria).strip() if carroceria else None,
                'proprietario': str(proprietario).strip() if proprietario else None,
                'eh_rizza':     _eh_rizza(proprietario),
            })
        vistos = {}
        for v in normalizados:
            vistos[v['placa']] = v
        final = sorted(vistos.values(), key=lambda x: x['placa'])

        _cache_set('veiculos', final)
        return jsonify({'ok': True, 'data': final, 'count': len(final), 'cached': False})

    except requests.exceptions.HTTPError as e:
        detail = ''
        try: detail = e.response.json()
        except Exception: detail = e.response.text
        return jsonify({'ok': False, 'error': str(e), 'detail': detail}), 500
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ── Clientes (Postgres local) ───────────────────────────────────────────
@app.route('/api/embarques/clientes')
@login_required
def api_embarques_clientes_list():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT id, nome, importado_em FROM clientes ORDER BY nome")
        data = [
            {'id': r[0], 'nome': r[1], 'importado_em': r[2].isoformat() if r[2] else None}
            for r in cur.fetchall()
        ]
        cur.close(); conn.close()
        return jsonify({'ok': True, 'data': data, 'count': len(data)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/embarques/clientes', methods=['POST'])
@login_required
def api_embarques_clientes_create():
    body = request.get_json(silent=True) or {}
    nome = (body.get('nome') or '').strip()
    if len(nome) < 3:
        return jsonify({'ok': False, 'error': 'Nome inválido (mínimo 3 caracteres)'}), 400
    try:
        conn = get_db()
        cur = conn.cursor()
        # Tenta inserir; se conflita com índice case-insensitive, pega o existente
        cur.execute(
            "INSERT INTO clientes (nome) VALUES (%s) "
            "ON CONFLICT ON CONSTRAINT ux_clientes_nome_ci DO NOTHING RETURNING id",
            (nome,)
        )
        row = cur.fetchone()
        if row:
            new_id = row[0]
            ja_existia = False
        else:
            cur.execute(
                "SELECT id FROM clientes WHERE LOWER(TRIM(nome)) = LOWER(TRIM(%s))",
                (nome,)
            )
            r = cur.fetchone()
            new_id = r[0] if r else None
            ja_existia = True
        conn.commit()
        cur.close(); conn.close()
        return jsonify({'ok': True, 'id': new_id, 'ja_existia': ja_existia})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ── Cargas: criação ─────────────────────────────────────────────────────
def _validar_carga_payload(b):
    erros = []
    obrig = ['tipo_operacao', 'cliente_id', 'cliente_nome', 'origem', 'destinos',
             'motorista', 'cavalo', 'data_carregamento']
    for c in obrig:
        if c not in b or b.get(c) in (None, '', []):
            erros.append(f'Campo obrigatório ausente: {c}')
    if not erros:
        if b['tipo_operacao'] not in ('Frota', 'Agregado', 'Terceiro'):
            erros.append('tipo_operacao inválido')
        if not isinstance(b['destinos'], list) or len(b['destinos']) < 1:
            erros.append('Informe pelo menos 1 destino')
        ori = b.get('origem') or {}
        if not ori.get('cidade') or not ori.get('uf'):
            erros.append('Origem incompleta (cidade + uf)')
        for i, d in enumerate(b.get('destinos') or []):
            if not d.get('cidade') or not d.get('uf'):
                erros.append(f'Destino {i+1} incompleto')
        mot = b.get('motorista') or {}
        if not mot.get('nome') or not mot.get('cpf'):
            erros.append('Motorista incompleto (nome + cpf)')
        cav = b.get('cavalo') or {}
        if not cav.get('placa') or not cav.get('tipo'):
            erros.append('Cavalo incompleto (placa + tipo)')
        if cav.get('tipo') == 'Cavalo' and not (b.get('carreta1') or {}).get('placa'):
            erros.append('Carreta 1 obrigatória quando o tipo do veículo é Cavalo')
    return erros


@app.route('/api/embarques/cargas', methods=['POST'])
@login_required
def api_embarques_cargas_create():
    b = request.get_json(silent=True) or {}
    erros = _validar_carga_payload(b)
    if erros:
        return jsonify({'ok': False, 'error': 'Validação falhou', 'detail': erros}), 400

    warnings = []
    cav = b.get('cavalo') or {}
    c1  = b.get('carreta1') or {}
    c2  = b.get('carreta2') or {}
    esperado = _classifica_tipo_operacao(bool(cav.get('eh_rizza')), bool(c1.get('eh_rizza')))
    if b['tipo_operacao'] != esperado:
        warnings.append(f"tipo_operacao '{b['tipo_operacao']}' diverge do esperado '{esperado}' pelos proprietários.")

    # Bloqueio de conflito (motorista/veículos em carga ativa)
    mot_pre = b.get('motorista') or {}
    placas_check = [
        (cav.get('placa') or '').upper().strip(),
        (c1.get('placa') or '').upper().strip(),
        (c2.get('placa') or '').upper().strip(),
    ]
    conflitos = _buscar_conflitos(mot_pre.get('cpf'), [p for p in placas_check if p])
    if conflitos:
        # Constrói mensagem detalhada (útil mesmo se o frontend ignorar o campo 'conflitos')
        nums = sorted({c['numero'] for c in conflitos if c.get('numero')})
        msg = 'Recurso já em uso na(s) carga(s) ativa(s): ' + ', '.join(nums)
        return jsonify({
            'ok': False,
            'error': msg,
            'tipo': 'conflito',
            'conflitos': conflitos
        }), 409

    try:
        conn = get_db()
        cur = conn.cursor()
        mot = b['motorista']
        ori = b['origem']
        cur.execute("""
            INSERT INTO embarques_cargas (
                tipo_operacao, status,
                cliente_id, cliente_nome,
                origem_cidade, origem_uf,
                motorista_nome, motorista_cpf, motorista_telefone,
                cavalo_placa, cavalo_tipo, cavalo_marca_modelo, cavalo_carroceria, cavalo_proprietario, cavalo_eh_rizza,
                carreta1_placa, carreta1_marca_modelo, carreta1_carroceria, carreta1_proprietario, carreta1_eh_rizza,
                carreta2_placa, carreta2_marca_modelo, carreta2_carroceria, carreta2_proprietario, carreta2_eh_rizza,
                data_carregamento, previsao_entrega, observacoes,
                criado_por_id, criado_por_nome
            ) VALUES (
                %s, 'Aberta',
                %s, %s,
                %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s
            ) RETURNING id, criado_em
        """, (
            b['tipo_operacao'],
            b['cliente_id'], b['cliente_nome'],
            ori['cidade'], ori['uf'],
            mot['nome'], mot['cpf'], mot.get('telefone'),
            cav['placa'], cav['tipo'], cav.get('marca_modelo'), cav.get('carroceria'), cav.get('proprietario'), bool(cav.get('eh_rizza')),
            c1.get('placa'), c1.get('marca_modelo'), c1.get('carroceria'), c1.get('proprietario'), bool(c1.get('eh_rizza')),
            c2.get('placa'), c2.get('marca_modelo'), c2.get('carroceria'), c2.get('proprietario'), bool(c2.get('eh_rizza')),
            b['data_carregamento'], b.get('previsao_entrega'), b.get('observacoes'),
            session.get('user_id'), session.get('nome'),
        ))
        carga_id, criado_em = cur.fetchone()

        # Destinos + geocoding (centroide IBGE)
        import geocoding
        destinos_inseridos = []
        for i, d in enumerate(b['destinos'], start=1):
            dlat, dlng = geocoding.geocoder_municipio(d['cidade'], d['uf'], conn=conn)
            cur.execute(
                "INSERT INTO embarques_cargas_destinos (carga_id, ordem, cidade, uf, latitude, longitude) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (carga_id, i, d['cidade'], d['uf'], dlat, dlng)
            )
            destinos_inseridos.append({'cidade': d['cidade'], 'uf': d['uf'], 'lat': dlat, 'lng': dlng})

        # Geocoding origem
        olat, olng = geocoding.geocoder_municipio(ori['cidade'], ori['uf'], conn=conn)
        cur.execute(
            "UPDATE embarques_cargas SET origem_latitude=%s, origem_longitude=%s WHERE id=%s",
            (olat, olng, carga_id)
        )

        # Gera numero
        ano = criado_em.year
        numero = f"C-{ano}-{carga_id:06d}"
        cur.execute("UPDATE embarques_cargas SET numero = %s WHERE id = %s", (numero, carga_id))

        conn.commit()
        cur.close(); conn.close()

        # Calcula rota planejada via ORS (após commit; falha não derruba o POST)
        ors_warn = None
        if olat is not None and destinos_inseridos and destinos_inseridos[-1]['lat'] is not None:
            try:
                import ors_client
                dest_final = destinos_inseridos[-1]
                rota = ors_client.tracar_rota(
                    {'lat': olat, 'lng': olng},
                    {'lat': dest_final['lat'], 'lng': dest_final['lng']}
                )
                conn2 = get_db()
                cur2 = conn2.cursor()
                cur2.execute("""
                    UPDATE embarques_cargas SET
                        rota_planejada_polyline=%s,
                        distancia_planejada_km=%s,
                        duracao_estimada_min=%s,
                        rota_recalculada_em=NOW()
                    WHERE id=%s
                """, (rota['polyline'], rota['distancia_km'], rota['duracao_min'], carga_id))
                conn2.commit()
                cur2.close(); conn2.close()
            except Exception as e:
                ors_warn = f'ORS falhou: {e}'
        elif olat is None:
            ors_warn = 'Origem sem coordenadas IBGE (cidade não encontrada)'
        elif not destinos_inseridos or destinos_inseridos[-1]['lat'] is None:
            ors_warn = 'Destino final sem coordenadas IBGE'

        if ors_warn:
            warnings.append(ors_warn)

        return jsonify({'ok': True, 'id': carga_id, 'numero': numero, 'warnings': warnings})

    except Exception as e:
        try: conn.rollback()
        except Exception: pass
        return jsonify({'ok': False, 'error': str(e)}), 500


# ── Cargas: listagem com filtros ────────────────────────────────────────
@app.route('/api/embarques/cargas')
@login_required
def api_embarques_cargas_list():
    args = request.args
    where = ["1=1"]
    params = []

    data_campo = 'data_carregamento' if args.get('data_campo', 'carregamento') == 'carregamento' else 'previsao_entrega'

    if args.get('start'):
        where.append(f"c.{data_campo} >= %s"); params.append(args['start'])
    if args.get('end'):
        where.append(f"c.{data_campo} <= %s"); params.append(args['end'])
    if args.get('tipo_operacao'):
        where.append("c.tipo_operacao = %s"); params.append(args['tipo_operacao'])
    if args.get('cliente_id'):
        where.append("c.cliente_id = %s"); params.append(args['cliente_id'])
    if args.get('criado_por_id'):
        where.append("c.criado_por_id = %s"); params.append(args['criado_por_id'])
    if args.get('motorista'):
        where.append("c.motorista_nome ILIKE %s"); params.append(f"%{args['motorista']}%")
    if args.get('origem_uf'):
        where.append("c.origem_uf = %s"); params.append(args['origem_uf'])
    if args.get('destino_uf'):
        where.append("EXISTS (SELECT 1 FROM embarques_cargas_destinos d WHERE d.carga_id = c.id AND d.uf = %s)")
        params.append(args['destino_uf'])
    if args.get('status'):
        where.append("c.status = %s"); params.append(args['status'])
    if args.get('q'):
        q = f"%{args['q']}%"
        where.append("(c.numero ILIKE %s OR c.motorista_nome ILIKE %s OR c.cliente_nome ILIKE %s OR c.cavalo_placa ILIKE %s OR c.carreta1_placa ILIKE %s OR c.carreta2_placa ILIKE %s)")
        params.extend([q, q, q, q, q, q])

    try:
        limite = int(args.get('limit', 1000))
    except Exception:
        limite = 1000
    limite = max(1, min(limite, 1000))

    sql = f"""
        SELECT c.id, c.numero, c.status, c.tipo_operacao,
               c.cliente_id, c.cliente_nome,
               c.origem_cidade, c.origem_uf,
               c.motorista_nome, c.motorista_cpf,
               c.cavalo_placa, c.cavalo_tipo, c.cavalo_marca_modelo, c.cavalo_proprietario,
               c.carreta1_placa, c.carreta2_placa,
               c.data_carregamento, c.previsao_entrega, c.data_conclusao,
               c.observacoes,
               c.criado_em, c.criado_por_id, c.criado_por_nome, c.atualizado_em,
               c.no_local_desde, c.saida_auto, c.entregue_auto,
               c.distancia_planejada_km, c.duracao_estimada_min,
               (
                 SELECT string_agg(d.cidade || '/' || d.uf, '; ' ORDER BY d.ordem)
                 FROM embarques_cargas_destinos d WHERE d.carga_id = c.id
               ) AS destinos
        FROM embarques_cargas c
        WHERE {' AND '.join(where)}
        ORDER BY c.data_carregamento DESC, c.id DESC
        LIMIT {limite}
    """

    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        data = []
        for r in rows:
            obj = dict(zip(cols, r))
            for k in ('data_carregamento', 'previsao_entrega'):
                if obj.get(k): obj[k] = obj[k].isoformat()
            for k in ('data_conclusao', 'criado_em', 'atualizado_em'):
                if obj.get(k): obj[k] = obj[k].isoformat()
            obj['pode_editar'] = _pode_editar_carga(obj.get('criado_por_id'))
            data.append(obj)
        cur.close(); conn.close()
        return jsonify({'ok': True, 'data': data, 'count': len(data)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ── Carga: detalhe ──────────────────────────────────────────────────────
@app.route('/api/embarques/cargas/<int:carga_id>')
@login_required
def api_embarques_carga_detail(carga_id):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT * FROM embarques_cargas WHERE id = %s", (carga_id,))
        row = cur.fetchone()
        if not row:
            cur.close(); conn.close()
            return jsonify({'ok': False, 'error': 'Carga não encontrada'}), 404
        cols = [d[0] for d in cur.description]
        carga = dict(zip(cols, row))
        for k in ('data_carregamento', 'previsao_entrega'):
            if carga.get(k): carga[k] = carga[k].isoformat()
        for k in ('data_conclusao', 'criado_em', 'atualizado_em'):
            if carga.get(k): carga[k] = carga[k].isoformat()

        cur.execute("SELECT id, ordem, cidade, uf FROM embarques_cargas_destinos WHERE carga_id = %s ORDER BY ordem", (carga_id,))
        destinos = [{'id': r[0], 'ordem': r[1], 'cidade': r[2], 'uf': r[3]} for r in cur.fetchall()]
        carga['destinos'] = destinos
        carga['pode_editar'] = _pode_editar_carga(carga.get('criado_por_id'))
        cur.close(); conn.close()
        return jsonify({'ok': True, 'data': carga})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ── Carga: edição com log ───────────────────────────────────────────────
_PATCH_WHITELIST = (
    'status', 'observacoes', 'previsao_entrega', 'data_carregamento',
    'cliente_id', 'cliente_nome', 'tipo_operacao',
    'motorista_nome', 'motorista_cpf', 'motorista_telefone',
    'cavalo_placa', 'cavalo_tipo', 'cavalo_marca_modelo', 'cavalo_carroceria', 'cavalo_proprietario', 'cavalo_eh_rizza',
    'carreta1_placa', 'carreta1_marca_modelo', 'carreta1_carroceria', 'carreta1_proprietario', 'carreta1_eh_rizza',
    'carreta2_placa', 'carreta2_marca_modelo', 'carreta2_carroceria', 'carreta2_proprietario', 'carreta2_eh_rizza',
    'origem_cidade', 'origem_uf',
)


@app.route('/api/embarques/cargas/<int:carga_id>', methods=['PATCH'])
@login_required
def api_embarques_carga_patch(carga_id):
    b = request.get_json(silent=True) or {}
    campos = {k: b[k] for k in b if k in _PATCH_WHITELIST}
    if not campos and 'destinos' not in b:
        return jsonify({'ok': False, 'error': 'Nada a atualizar'}), 400

    try:
        conn = get_db(); cur = conn.cursor()
        # Estado atual
        cur.execute("SELECT * FROM embarques_cargas WHERE id = %s", (carga_id,))
        row = cur.fetchone()
        if not row:
            cur.close(); conn.close()
            return jsonify({'ok': False, 'error': 'Carga não encontrada'}), 404
        cols_atuais = [d[0] for d in cur.description]
        atual = dict(zip(cols_atuais, row))

        # Permissão: admin ou criador da carga
        if not _pode_editar_carga(atual.get('criado_por_id')):
            cur.close(); conn.close()
            return jsonify({
                'ok': False,
                'error': 'Você não pode editar esta carga. Apenas quem lançou ou um administrador.'
            }), 403

        # Bloqueio de conflito quando o novo estado fica/continua ativo
        novo_status = campos.get('status', atual.get('status'))
        if novo_status in ('Aberta', 'Em rota'):
            novo_cpf = campos.get('motorista_cpf', atual.get('motorista_cpf'))
            placas_novas = [
                (campos.get('cavalo_placa',   atual.get('cavalo_placa'))   or '').upper().strip(),
                (campos.get('carreta1_placa', atual.get('carreta1_placa')) or '').upper().strip(),
                (campos.get('carreta2_placa', atual.get('carreta2_placa')) or '').upper().strip(),
            ]
            conflitos = _buscar_conflitos(novo_cpf, [p for p in placas_novas if p], exclude_id=carga_id)
            if conflitos:
                cur.close(); conn.close()
                nums = sorted({c['numero'] for c in conflitos if c.get('numero')})
                msg = 'Recurso já em uso na(s) carga(s) ativa(s): ' + ', '.join(nums)
                return jsonify({'ok': False, 'error': msg, 'tipo': 'conflito', 'conflitos': conflitos}), 409

        # Diff: só campos cujo valor mudou
        diffs = []
        sets = []
        params = []
        for k, v in campos.items():
            antigo = atual.get(k)
            if isinstance(antigo, (bool,)):
                novo_norm = bool(v)
            elif hasattr(antigo, 'isoformat'):
                novo_norm = v  # comparar como veio
                antigo = antigo.isoformat() if antigo else None
            else:
                novo_norm = v
            if str(antigo) != str(novo_norm) and not (antigo is None and novo_norm in (None, '')):
                diffs.append((k, antigo, novo_norm))
                sets.append(f"{k} = %s")
                params.append(novo_norm)

        # Auto data_conclusao quando muda para Entregue
        if campos.get('status') == 'Entregue' and atual.get('status') != 'Entregue':
            sets.append("data_conclusao = NOW()")

        # Destinos — substituir lista inteira se fornecida
        destinos_mudaram = False
        novos_destinos = b.get('destinos')
        if isinstance(novos_destinos, list):
            cur.execute(
                "SELECT ordem, cidade, uf FROM embarques_cargas_destinos WHERE carga_id = %s ORDER BY ordem",
                (carga_id,)
            )
            atuais = [{'ordem': r[0], 'cidade': r[1], 'uf': r[2]} for r in cur.fetchall()]
            atuais_repr = '; '.join(f"{d['cidade']}/{d['uf']}" for d in atuais)
            novos_repr  = '; '.join(f"{d.get('cidade','?')}/{d.get('uf','?')}" for d in novos_destinos)
            if atuais_repr != novos_repr:
                destinos_mudaram = True
                cur.execute("DELETE FROM embarques_cargas_destinos WHERE carga_id = %s", (carga_id,))
                for i, d in enumerate(novos_destinos, start=1):
                    if not d.get('cidade') or not d.get('uf'):
                        continue
                    cur.execute(
                        "INSERT INTO embarques_cargas_destinos (carga_id, ordem, cidade, uf) VALUES (%s, %s, %s, %s)",
                        (carga_id, i, d['cidade'], d['uf'])
                    )

        if sets or destinos_mudaram:
            if sets:
                sets.append("atualizado_em = NOW()")
                params.append(carga_id)
                cur.execute(f"UPDATE embarques_cargas SET {', '.join(sets)} WHERE id = %s", params)
            elif destinos_mudaram:
                cur.execute("UPDATE embarques_cargas SET atualizado_em = NOW() WHERE id = %s", (carga_id,))
            for campo, va, vn in diffs:
                cur.execute("""
                    INSERT INTO embarques_cargas_log
                    (carga_id, usuario_id, usuario_nome, campo, valor_anterior, valor_novo)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (carga_id, session.get('user_id'), session.get('nome'),
                      campo,
                      None if va is None else str(va),
                      None if vn is None else str(vn)))
            if destinos_mudaram:
                cur.execute("""
                    INSERT INTO embarques_cargas_log
                    (carga_id, usuario_id, usuario_nome, campo, valor_anterior, valor_novo)
                    VALUES (%s, %s, %s, 'destinos', %s, %s)
                """, (carga_id, session.get('user_id'), session.get('nome'),
                      atuais_repr, novos_repr))

        conn.commit()
        cur.close(); conn.close()
        total_alt = len(diffs) + (1 if destinos_mudaram else 0)
        return jsonify({'ok': True, 'alteracoes': total_alt})
    except Exception as e:
        try: conn.rollback()
        except Exception: pass
        return jsonify({'ok': False, 'error': str(e)}), 500


# ── Carga: log de edição ────────────────────────────────────────────────
@app.route('/api/embarques/cargas/<int:carga_id>/log')
@login_required
def api_embarques_carga_log(carga_id):
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            SELECT id, usuario_id, usuario_nome, editado_em, campo, valor_anterior, valor_novo
            FROM embarques_cargas_log
            WHERE carga_id = %s
            ORDER BY editado_em DESC, id DESC
        """, (carga_id,))
        data = [{
            'id': r[0], 'usuario_id': r[1], 'usuario_nome': r[2],
            'editado_em': r[3].isoformat() if r[3] else None,
            'campo': r[4], 'valor_anterior': r[5], 'valor_novo': r[6],
        } for r in cur.fetchall()]
        cur.close(); conn.close()
        return jsonify({'ok': True, 'data': data, 'count': len(data)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ── Cargas: CSV streaming ───────────────────────────────────────────────
@app.route('/api/embarques/cargas/csv')
@login_required
def api_embarques_cargas_csv():
    args = request.args
    where = ["1=1"]
    params = []
    data_campo = 'data_carregamento' if args.get('data_campo', 'carregamento') == 'carregamento' else 'previsao_entrega'
    if args.get('start'):
        where.append(f"c.{data_campo} >= %s"); params.append(args['start'])
    if args.get('end'):
        where.append(f"c.{data_campo} <= %s"); params.append(args['end'])
    if args.get('tipo_operacao'):
        where.append("c.tipo_operacao = %s"); params.append(args['tipo_operacao'])
    if args.get('cliente_id'):
        where.append("c.cliente_id = %s"); params.append(args['cliente_id'])
    if args.get('criado_por_id'):
        where.append("c.criado_por_id = %s"); params.append(args['criado_por_id'])
    if args.get('motorista'):
        where.append("c.motorista_nome ILIKE %s"); params.append(f"%{args['motorista']}%")
    if args.get('origem_uf'):
        where.append("c.origem_uf = %s"); params.append(args['origem_uf'])
    if args.get('destino_uf'):
        where.append("EXISTS (SELECT 1 FROM embarques_cargas_destinos d WHERE d.carga_id = c.id AND d.uf = %s)")
        params.append(args['destino_uf'])
    if args.get('status'):
        where.append("c.status = %s"); params.append(args['status'])

    nome = f"cargas_{args.get('start','')}_{args.get('end','')}.csv".strip('_')

    sql = f"""
        SELECT c.numero, c.data_carregamento, c.previsao_entrega, c.status, c.tipo_operacao,
               c.cliente_nome,
               c.origem_cidade || '/' || c.origem_uf AS origem,
               (SELECT string_agg(d.cidade || '/' || d.uf, '; ' ORDER BY d.ordem)
                FROM embarques_cargas_destinos d WHERE d.carga_id = c.id) AS destinos,
               c.motorista_nome, c.motorista_cpf,
               c.cavalo_placa, c.cavalo_marca_modelo, c.cavalo_proprietario,
               c.carreta1_placa, c.carreta1_proprietario,
               c.carreta2_placa, c.carreta2_proprietario,
               c.observacoes,
               c.criado_por_nome, c.criado_em
        FROM embarques_cargas c
        WHERE {' AND '.join(where)}
        ORDER BY c.data_carregamento DESC, c.id DESC
    """
    headers_csv = [
        'numero', 'data_carregamento', 'previsao_entrega', 'status', 'tipo_operacao',
        'cliente', 'origem', 'destinos',
        'motorista', 'motorista_cpf',
        'cavalo_placa', 'cavalo_marca_modelo', 'cavalo_proprietario',
        'carreta1_placa', 'carreta1_proprietario',
        'carreta2_placa', 'carreta2_proprietario',
        'observacoes', 'lancado_por', 'lancado_em'
    ]

    def gerar():
        yield '﻿'
        yield _csv_linha(headers_csv)
        conn = get_db(); cur = conn.cursor()
        cur.execute(sql, params)
        for r in cur.fetchall():
            valores = []
            for v in r:
                if hasattr(v, 'isoformat'):
                    valores.append(v.isoformat())
                else:
                    valores.append(v)
            yield _csv_linha(valores)
        cur.close(); conn.close()

    return Response(
        stream_with_context(gerar()),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{nome}"'}
    )


# ── KPIs da landing ─────────────────────────────────────────────────────
@app.route('/api/embarques/kpis')
@login_required
def api_embarques_kpis():
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            SELECT
              COUNT(*) FILTER (WHERE data_carregamento = CURRENT_DATE) AS hoje,
              COUNT(*) FILTER (WHERE status = 'Em rota')                AS em_rota,
              COUNT(*) FILTER (WHERE status = 'Entregue'
                               AND date_trunc('month', data_conclusao) = date_trunc('month', CURRENT_DATE)) AS entregues_mes,
              COUNT(*) FILTER (WHERE status = 'Aberta')                 AS abertas
            FROM embarques_cargas
        """)
        r = cur.fetchone()
        cur.close(); conn.close()
        return jsonify({
            'ok': True,
            'data': {
                'hoje':           r[0] or 0,
                'em_rota':        r[1] or 0,
                'entregues_mes':  r[2] or 0,
                'abertas':        r[3] or 0,
            }
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ── Lista de embarcadores (usuários do sistema) p/ filtro do relatório ─
@app.route('/api/embarques/embarcadores')
@login_required
def api_embarques_embarcadores():
    try:
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT criado_por_id, criado_por_nome
            FROM embarques_cargas
            WHERE criado_por_id IS NOT NULL AND criado_por_nome IS NOT NULL
            ORDER BY criado_por_nome
        """)
        data = [{'id': r[0], 'nome': r[1]} for r in cur.fetchall()]
        cur.close(); conn.close()
        return jsonify({'ok': True, 'data': data, 'count': len(data)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


# ════════════════════════════════════════════════════════════════════════════
# RASTREAMENTO — Endpoints API (todos @login_required)
# ════════════════════════════════════════════════════════════════════════════

import tres_s_client
import rastreamento_worker


@app.route('/api/rastreamento/posicoes')
@login_required
def api_rastreamento_posicoes():
    """Lista posições atuais com info da carga ativa (se houver).
    Filtros: carregado=1|0, eh_rizza=1, q (placa/motorista).
    """
    args = request.args
    where = ["1=1"]
    params = []

    carregado = args.get('carregado')
    eh_rizza = args.get('eh_rizza')
    q = (args.get('q') or '').strip()

    base_join = """
        FROM embarques_posicoes_atuais p
        LEFT JOIN embarques_veiculos_rastreio v ON v.placa = p.placa
        LEFT JOIN LATERAL (
            SELECT id, numero, status, cliente_nome, motorista_nome, cavalo_proprietario, cavalo_eh_rizza,
                   no_local_desde, saida_auto, entregue_auto, data_carregamento, origem_cidade, origem_uf
            FROM embarques_cargas c
            WHERE c.cavalo_placa = p.placa AND c.status IN ('Aberta','Em rota')
            ORDER BY c.id DESC LIMIT 1
        ) ca ON true
    """

    if carregado == '1':
        where.append("ca.id IS NOT NULL")
    elif carregado == '0':
        where.append("ca.id IS NULL")

    if eh_rizza == '1':
        where.append("(ca.cavalo_eh_rizza = TRUE OR v.frota ILIKE %s)")
        params.append('%RIZZA%')

    if q:
        where.append("(p.placa ILIKE %s OR ca.motorista_nome ILIKE %s)")
        params.extend([f'%{q}%', f'%{q}%'])

    sql = f"""
        SELECT p.placa, p.latitude, p.longitude, p.velocidade, p.ignicao, p.direcao,
               p.cidade, p.uf, p.data_posicao, p.bloqueio, p.atualizado_em,
               v.frota, v.modelo, v.tipo,
               ca.id AS carga_id, ca.numero, ca.status, ca.cliente_nome, ca.motorista_nome,
               ca.cavalo_proprietario, ca.cavalo_eh_rizza, ca.no_local_desde, ca.saida_auto,
               ca.origem_cidade, ca.origem_uf
        {base_join}
        WHERE {' AND '.join(where)}
        ORDER BY p.placa
        LIMIT 2000
    """

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]
        data = [dict(zip(cols, r)) for r in rows]
        # Normalização: lat/lng → float, data → ISO UTC (com Z)
        for d in data:
            d['latitude'] = float(d['latitude']) if d['latitude'] is not None else None
            d['longitude'] = float(d['longitude']) if d['longitude'] is not None else None
            for k in ('data_posicao', 'atualizado_em', 'no_local_desde'):
                if d.get(k) is not None:
                    d[k] = d[k].isoformat() + 'Z'
            d['carregado'] = d.get('carga_id') is not None
        cur.close(); conn.close()
        return jsonify({'ok': True, 'data': data, 'count': len(data)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/rastreamento/cargas/<int:carga_id>/trajeto')
@login_required
def api_rastreamento_trajeto(carga_id):
    """Retorna trajeto + rota planejada + KPIs + raios."""
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, numero, status, cliente_nome, motorista_nome, cavalo_placa,
                   carreta1_placa, carreta2_placa,
                   origem_cidade, origem_uf, origem_latitude, origem_longitude,
                   data_carregamento, data_saida_real, data_conclusao,
                   no_local_desde, saida_auto, entregue_auto,
                   rota_planejada_polyline, distancia_planejada_km, duracao_estimada_min,
                   rota_recalculada_em
            FROM embarques_cargas WHERE id=%s
        """, (carga_id,))
        r = cur.fetchone()
        if not r:
            cur.close(); conn.close()
            return jsonify({'ok': False, 'error': 'Carga não encontrada'}), 404
        cols = [c[0] for c in cur.description]
        carga = dict(zip(cols, r))

        cur.execute("""
            SELECT ordem, cidade, uf, latitude, longitude
            FROM embarques_cargas_destinos WHERE carga_id=%s ORDER BY ordem
        """, (carga_id,))
        destinos = []
        for ord_, cidade, uf, lat, lng in cur.fetchall():
            destinos.append({
                'ordem': ord_, 'cidade': cidade, 'uf': uf,
                'latitude': float(lat) if lat is not None else None,
                'longitude': float(lng) if lng is not None else None,
            })

        # Período pra buscar histórico
        inicio = carga.get('data_saida_real') or carga.get('data_carregamento')
        from datetime import datetime as _dt
        fim = carga.get('data_conclusao') or _dt.utcnow()

        def _buscar_trajeto(placa):
            if not placa:
                return []
            cur.execute("""
                SELECT data_posicao, latitude, longitude, velocidade, ignicao, cidade, uf
                FROM embarques_posicoes_historico
                WHERE placa=%s AND data_posicao BETWEEN %s AND %s
                ORDER BY data_posicao
            """, (placa, inicio, fim))
            return [{
                'data': dp.isoformat() + 'Z',
                'lat': float(la),
                'lng': float(ln),
                'velocidade': vel,
                'ignicao': ig,
                'cidade': cid,
                'uf': uff,
            } for (dp, la, ln, vel, ig, cid, uff) in cur.fetchall()]

        traj_cavalo = _buscar_trajeto(carga['cavalo_placa'])
        traj_c1 = _buscar_trajeto(carga.get('carreta1_placa')) if carga.get('carreta1_placa') else []
        traj_c2 = _buscar_trajeto(carga.get('carreta2_placa')) if carga.get('carreta2_placa') else []

        # KPIs já consolidados?
        cur.execute("""
            SELECT distancia_metros, velocidade_max, velocidade_media,
                   tempo_movimento_seg, tempo_parado_seg, consolidado_final
            FROM embarques_cargas_rastreio_kpi WHERE carga_id=%s
        """, (carga_id,))
        rk = cur.fetchone()
        kpi = None
        if rk:
            kpi = {
                'distancia_km': round((rk[0] or 0) / 1000, 1),
                'velocidade_max': rk[1],
                'velocidade_media': float(rk[2]) if rk[2] is not None else None,
                'tempo_movimento_seg': rk[3],
                'tempo_parado_seg': rk[4],
                'consolidado_final': rk[5],
            }

        cur.close(); conn.close()

        # Última posição = último ponto do trajeto cavalo
        ultima = traj_cavalo[-1] if traj_cavalo else None

        # Format origem/destinos pra JSON
        origem = {
            'cidade': carga['origem_cidade'], 'uf': carga['origem_uf'],
            'latitude': float(carga['origem_latitude']) if carga['origem_latitude'] is not None else None,
            'longitude': float(carga['origem_longitude']) if carga['origem_longitude'] is not None else None,
        }

        resp = {
            'ok': True,
            'carga': {
                'id': carga['id'],
                'numero': carga['numero'],
                'status': carga['status'],
                'cliente_nome': carga['cliente_nome'],
                'motorista_nome': carga['motorista_nome'],
                'cavalo_placa': carga['cavalo_placa'],
                'carreta1_placa': carga.get('carreta1_placa'),
                'carreta2_placa': carga.get('carreta2_placa'),
                'data_carregamento': carga['data_carregamento'].isoformat() if carga['data_carregamento'] else None,
                'data_saida_real': (carga['data_saida_real'].isoformat() + 'Z') if carga['data_saida_real'] else None,
                'data_conclusao': (carga['data_conclusao'].isoformat() + 'Z') if carga['data_conclusao'] else None,
                'no_local_desde': (carga['no_local_desde'].isoformat() + 'Z') if carga['no_local_desde'] else None,
                'saida_auto': carga['saida_auto'],
                'entregue_auto': carga['entregue_auto'],
            },
            'origem': origem,
            'destinos': destinos,
            'trajeto': {
                'cavalo': traj_cavalo,
                'carreta1': traj_c1,
                'carreta2': traj_c2,
            },
            'rota_planejada': {
                'polyline': carga.get('rota_planejada_polyline'),
                'distancia_km': float(carga['distancia_planejada_km']) if carga.get('distancia_planejada_km') is not None else None,
                'duracao_min': carga.get('duracao_estimada_min'),
                'recalculada_em': (carga['rota_recalculada_em'].isoformat() + 'Z') if carga.get('rota_recalculada_em') else None,
            },
            'ultima_posicao': ultima,
            'kpi': kpi,
        }
        return jsonify(resp)
    except Exception as e:
        try: conn.close()
        except Exception: pass
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/rastreamento/cargas/<int:carga_id>/confirmar-entrega', methods=['POST'])
@login_required
def api_rastreamento_confirmar_entrega(carga_id):
    """Confirma manualmente a entrega. status='Entregue', entregue_auto=false."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT status FROM embarques_cargas WHERE id=%s", (carga_id,))
        r = cur.fetchone()
        if not r:
            cur.close(); conn.close()
            return jsonify({'ok': False, 'error': 'Carga não encontrada'}), 404
        if r[0] in ('Entregue', 'Cancelada'):
            cur.close(); conn.close()
            return jsonify({'ok': False, 'error': f'Carga já está {r[0]}'}), 400
        cur.execute("""
            UPDATE embarques_cargas
            SET status='Entregue', entregue_auto=FALSE, data_conclusao=NOW(), atualizado_em=NOW()
            WHERE id=%s
        """, (carga_id,))
        # Log no embarques_cargas_log
        cur.execute("""
            INSERT INTO embarques_cargas_log (carga_id, usuario_id, usuario_nome, campo, valor_anterior, valor_novo)
            VALUES (%s, %s, %s, 'status', %s, 'Entregue')
        """, (carga_id, session.get('user_id'), session.get('nome'), r[0]))
        # Consolida KPI
        rastreamento_worker._consolidar_kpi(cur, carga_id, final=True)
        conn.commit()
        cur.close(); conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        try: conn.rollback(); conn.close()
        except Exception: pass
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/rastreamento/sync-veiculos', methods=['POST'])
@admin_required
def api_rastreamento_sync_veiculos():
    """Força sync com /ListaVeiculos da 3S. UPSERT em embarques_veiculos_rastreio."""
    try:
        veiculos = tres_s_client.lista_veiculos()
        conn = get_db()
        cur = conn.cursor()
        novos = 0
        atualizados = 0
        for v in veiculos:
            placa = (v.get('placa') or '').strip().upper()
            id_veiculo = v.get('idVeiculo')
            if not placa or not id_veiculo:
                continue
            cur.execute("SELECT id FROM embarques_veiculos_rastreio WHERE placa=%s", (placa,))
            existe = cur.fetchone() is not None
            cur.execute("""
                INSERT INTO embarques_veiculos_rastreio
                    (placa, id_veiculo_3s, id_equipamento, frota, modelo, tipo, sincronizado_em)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (placa) DO UPDATE SET
                    id_veiculo_3s = EXCLUDED.id_veiculo_3s,
                    id_equipamento = EXCLUDED.id_equipamento,
                    frota = EXCLUDED.frota,
                    modelo = EXCLUDED.modelo,
                    tipo = EXCLUDED.tipo,
                    sincronizado_em = NOW()
            """, (placa, id_veiculo, v.get('idEquipamento'), v.get('frota'),
                  v.get('modelo'), v.get('tipo')))
            if existe:
                atualizados += 1
            else:
                novos += 1
        conn.commit()
        cur.close(); conn.close()
        return jsonify({'ok': True, 'total': len(veiculos), 'novos': novos, 'atualizados': atualizados})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/rastreamento/health')
@admin_required
def api_rastreamento_health():
    """Status do worker, última sync, token, contadores."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT MAX(atualizado_em) FROM embarques_posicoes_atuais")
        ultima_sync = cur.fetchone()[0]
        cur.execute("SELECT expiration FROM embarques_3s_token WHERE id=1")
        r = cur.fetchone()
        token_valido_ate = (r[0].isoformat() + 'Z') if r else None
        cur.execute("SELECT COUNT(*) FROM embarques_3s_log WHERE chamado_em > NOW() - INTERVAL '60 seconds'")
        chamadas_60s = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM embarques_3s_log WHERE provider='ORS' AND chamado_em > NOW() - INTERVAL '24 hours'")
        ors_24h = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM embarques_3s_log WHERE erro_codigo IS NOT NULL AND chamado_em > NOW() - INTERVAL '24 hours'")
        erros_24h = cur.fetchone()[0]
        cur.close(); conn.close()
        return jsonify({
            'ok': True,
            'worker_running': rastreamento_worker.is_running(),
            'modo_simulado': tres_s_client.is_modo_simulado(),
            'ultima_sync': (ultima_sync.isoformat() + 'Z') if ultima_sync else None,
            'token_valido_ate': token_valido_ate,
            'chamadas_60s': chamadas_60s,
            'ors_chamadas_24h': ors_24h,
            'erros_24h': erros_24h,
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/api/rastreamento/log')
@admin_required
def api_rastreamento_log():
    """Últimas 200 linhas do log (3S + ORS + SIM)."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            SELECT chamado_em, provider, endpoint, duracao_ms, status_http, erro_codigo, erro_msg
            FROM embarques_3s_log
            ORDER BY chamado_em DESC LIMIT 200
        """)
        cols = ['chamado_em', 'provider', 'endpoint', 'duracao_ms', 'status_http', 'erro_codigo', 'erro_msg']
        data = []
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            d['chamado_em'] = d['chamado_em'].isoformat() + 'Z'
            data.append(d)
        cur.close(); conn.close()
        return jsonify({'ok': True, 'data': data, 'count': len(data)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


if __name__ == '__main__':
    print("\n⚡ Auditoria Receita — Backend")
    print("=" * 40)

    missing = [k for k, v in CONFIG.items() if not v]
    if missing:
        print(f"\n⚠️  Variáveis Power BI faltando no .env: {', '.join(missing)}")
    else:
        print("✅ Configuração Power BI OK")

    # Boot do worker de rastreamento
    if os.getenv('START_WORKER', '').lower() == 'true':
        try:
            rastreamento_worker.start()
            modo = 'SIMULADO' if tres_s_client.is_modo_simulado() else 'REAL'
            print(f"✅ Worker de rastreamento iniciado (modo {modo})")
        except Exception as e:
            print(f"⚠️  Worker não iniciou: {e}")
    else:
        print("ℹ️  Worker de rastreamento desligado (START_WORKER != true)")

    print(f"\n🌐 Acesse: http://localhost:5000\n")
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)
