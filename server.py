"""
Auditoria Receita — Backend
Rode: python server.py
Acesse: http://localhost:5000
"""

import os
import io
import math
import tempfile
import functools
import psycopg2
import requests
from flask import Flask, jsonify, send_from_directory, request, session, redirect, url_for, send_file
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder='.')
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-change-me')
CORS(app, supports_credentials=True)

# ── Config Power BI ──
CONFIG = {
    'tenant_id':     os.getenv('POWERBI_TENANT_ID', ''),
    'client_id':     os.getenv('POWERBI_CLIENT_ID', ''),
    'client_secret': os.getenv('POWERBI_CLIENT_SECRET', ''),
    'dataset_id':    os.getenv('POWERBI_DATASET_ID', ''),
    'group_id':      os.getenv('POWERBI_GROUP_ID', ''),
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


def execute_dax(token, query):
    url = (
        f"https://api.powerbi.com/v1.0/myorg/groups/"
        f"{CONFIG['group_id']}/datasets/{CONFIG['dataset_id']}/executeQueries"
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

WHISPER_MAX_BYTES = 24 * 1024 * 1024  # 24MB por chunk (limite Whisper é 25MB)

def _transcrever_audio(caminho_arquivo):
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
    with open(caminho_arquivo, 'rb') as f:
        resultado = client.audio.transcriptions.create(
            model='whisper-1',
            file=f,
            language='pt'
        )
    return resultado.text


def _dividir_e_transcrever(caminho_arquivo):
    from pydub import AudioSegment
    audio = AudioSegment.from_file(caminho_arquivo)
    tamanho_total = os.path.getsize(caminho_arquivo)

    if tamanho_total <= WHISPER_MAX_BYTES:
        return _transcrever_audio(caminho_arquivo)

    n_chunks = math.ceil(tamanho_total / WHISPER_MAX_BYTES)
    duracao_chunk_ms = len(audio) // n_chunks
    transcricoes = []

    for i in range(n_chunks):
        inicio = i * duracao_chunk_ms
        fim = min((i + 1) * duracao_chunk_ms, len(audio))
        chunk = audio[inicio:fim]
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp:
            chunk.export(tmp.name, format='mp3')
            transcricoes.append(_transcrever_audio(tmp.name))
            os.unlink(tmp.name)

    return ' '.join(transcricoes)


def _gerar_ata(tema, transcricao):
    from openai import OpenAI
    client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
    prompt = f"""Você é um assistente especializado em redigir atas de reunião profissionais em português brasileiro.

Tema da reunião: {tema}

Transcrição:
{transcricao}

Gere uma ata formal e bem estruturada com:
- Cabeçalho (data/hora extraída da transcrição se disponível, ou deixar em branco para preenchimento)
- Participantes (se mencionados)
- Pauta
- Discussões e deliberações organizadas por tópico
- Decisões tomadas
- Encaminhamentos e responsáveis
- Próximos passos

Use formato profissional, linguagem formal, sem repetições desnecessárias."""

    resposta = client.chat.completions.create(
        model='gpt-4.1-mini',
        messages=[{'role': 'user', 'content': prompt}],
        max_tokens=4000
    )
    return resposta.choices[0].message.content


@app.route('/api/reuniao/processar', methods=['POST'])
@admin_required
def processar_reuniao():
    if 'audio' not in request.files:
        return jsonify({'ok': False, 'error': 'Arquivo de áudio não enviado'}), 400

    audio_file = request.files['audio']
    tema = request.form.get('tema', 'Reunião').strip()

    ext = os.path.splitext(audio_file.filename)[1].lower() or '.mp3'
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        audio_file.save(tmp.name)
        tmp_path = tmp.name

    try:
        transcricao = _dividir_e_transcrever(tmp_path)
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

    if formato == 'docx':
        from docx import Document
        from docx.shared import Pt
        doc = Document()
        doc.add_heading(tema, level=1)
        for linha in ata.split('\n'):
            linha = linha.strip()
            if not linha:
                doc.add_paragraph('')
            elif linha.startswith('## '):
                doc.add_heading(linha[3:], level=2)
            elif linha.startswith('# '):
                doc.add_heading(linha[2:], level=1)
            elif linha.startswith('**') and linha.endswith('**'):
                p = doc.add_paragraph()
                run = p.add_run(linha.strip('*'))
                run.bold = True
            else:
                doc.add_paragraph(linha)

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
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.enums import TA_JUSTIFY

        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4,
                                leftMargin=2.5*cm, rightMargin=2.5*cm,
                                topMargin=2.5*cm, bottomMargin=2.5*cm)
        styles = getSampleStyleSheet()
        style_normal = ParagraphStyle('normal_pt', parent=styles['Normal'],
                                      fontSize=11, leading=16, alignment=TA_JUSTIFY)
        style_heading = ParagraphStyle('heading_pt', parent=styles['Heading2'],
                                       fontSize=13, leading=18, spaceAfter=6)
        style_title = ParagraphStyle('title_pt', parent=styles['Title'],
                                     fontSize=16, leading=22, spaceAfter=12)

        story = [Paragraph(tema, style_title), Spacer(1, 12)]
        for linha in ata.split('\n'):
            linha = linha.strip()
            if not linha:
                story.append(Spacer(1, 8))
            elif linha.startswith('## ') or linha.startswith('# '):
                texto = linha.lstrip('#').strip()
                story.append(Paragraph(texto, style_heading))
            elif linha.startswith('**') and linha.endswith('**'):
                story.append(Paragraph(f'<b>{linha.strip("*")}</b>', style_normal))
            else:
                story.append(Paragraph(linha, style_normal))

        doc.build(story)
        buf.seek(0)
        nome_arquivo = f"{tema.replace(' ', '_')}.pdf"
        return send_file(buf, mimetype='application/pdf',
                         as_attachment=True, download_name=nome_arquivo)

    return jsonify({'ok': False, 'error': 'Formato inválido. Use docx ou pdf'}), 400


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
