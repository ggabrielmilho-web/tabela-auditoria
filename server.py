"""
Auditoria Receita — Backend
Rode: python server.py
Acesse: http://localhost:5000
"""

import os
import io
import json
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


if __name__ == '__main__':
    print("\n⚡ Auditoria Receita — Backend")
    print("=" * 40)

    missing = [k for k, v in CONFIG.items() if not v]
    if missing:
        print(f"\n⚠️  Variáveis Power BI faltando no .env: {', '.join(missing)}")
    else:
        print("✅ Configuração Power BI OK")

    print(f"\n🌐 Acesse: http://localhost:5000\n")
    app.run(host='0.0.0.0', port=5000, debug=True)
