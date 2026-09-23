# -*- coding: utf-8 -*-
"""Relatório semanal do evento 5100 (CARGA E DESCARGA C/ TERCEIROS) por e-mail.

Toda segunda-feira às 08:00 de Brasília sai, para o operacional, a planilha da
semana que fechou (segunda a domingo) com as mesmas colunas da aba Despesas mais
uma coluna `cte` logo depois do `historico_despesa`.

──────────────────────────────────────────────────────────────────────────────
POR QUE A JANELA É POR `emissao`, E NÃO PELO FILTRO DA ABA

A aba Despesas parece filtrar por data, mas `_gerar_refs_periodo` no server.py
converte o período numa lista de competências `YYYY/MM` e **descarta o dia**:
pedir 14/09→20/09 devolve setembro inteiro. Semana não existe naquele filtro.

Medido em 2026/09 (176 lançamentos do 5100, R$ 133.447,52): competência e mês de
emissão coincidem em 173; os outros 3 foram emitidos em agosto e caíram na
competência de setembro. Para recortar uma semana a régua é `emissao`.

Consequência aceita (decisão do Gabriel em 23/09/2026): lançamento incluído com
atraso não entra em relatório nenhum — a semana dele já foi enviada. O risco é
pequeno e foi medido: 384 de 396 lançamentos entram no BI no MESMO dia da
emissão, e nas 7 semanas medidas a semana estava 96–100% completa na segunda
seguinte.

──────────────────────────────────────────────────────────────────────────────
COMO A NF DO HISTÓRICO VIRA CTe

O `historico_despesa` traz a NF da carga ("CNPJFORN13274688000140 NF 3850037 -
MERIO"). Ela casa com `numero_nota_fiscal` de `conhecimentos_emitidos`, que dá o
`serie_numero_ctrc`. As duas tabelas vivem no MESMO dataset (o do DRE), então o
cruzamento não abre fonte nova.

Três coisas medidas que o dado não entrega de graça:

1. `nfiscal` é a NF da DESPESA (documento do fornecedor), não a da carga. Quem
   vincula ao CTe é a NF escrita no histórico.
2. `CNPJFORN<14 dígitos>` NÃO é o CNPJ do remetente do CTe, nem pela raiz — na
   Heinz o histórico diz 02691482000107 e o CTe traz 50955707000472. Foi testado
   como desempate e reprovado; não é âncora.
3. O histórico usa apelido comercial e o CTe razão social (YPE = QUIMICA AMPARO,
   FINI = SANCHEZ CANO, MERIO/START = LIMA E PERGHER, EMBELLEZE = DOARBELLEZA).
   Conferir nome contra nome dá falso negativo.

O que prova o casamento é a DATA: a defasagem despesa − emissão do CTe deu
mediana 4 d, p90 8 d, mínimo 0, e só 2 casos acima de 20 d em 123. Colisão
aleatória de número de NF espalharia as datas. Por isso, quando a mesma NF está
em 2 CTes (26 casos em setembro, sempre o MESMO remetente — duas pernas ou
redespacho, nunca empresa diferente), fica o CTe emitido antes ou no dia da
despesa, mais próximo; o outro vai na coluna `cte_outros`, para auditoria.

Cobertura medida em 2026/09: 146 de 176 (83%). O que sobra são os 26 lançamentos
`SD - CARREGAMENTO ... PIX <CPF>`, que não têm NF nenhuma, e 4 NFs sem CTe (duas
com cara de dígito trocado: 3481991 x 3841991).

──────────────────────────────────────────────────────────────────────────────
VARIÁVEIS

    R5100_ENVIO=true              liga o laço no servidor (nasce desligado)
    R5100_TO=a@x,b@y              destinatários, separados por vírgula
    R5100_HORA_BRT=08:00          horário de Brasília da segunda-feira
    R5100_JANELA_DISPARO_MIN=180  tolerância p/ disparar após o horário (restart)
    R5100_EVENTO=5100             evento do 477
    GMAIL_USER / APP_GMAIL        conta de envio (automacao@rizzalog.com.br)

Env sempre pela CLI (`docker service update --env-add`): editar pelo stack do
Portainer devolve a imagem antiga.

    python -X utf8 relatorio_5100.py --dry-run
    python -X utf8 relatorio_5100.py --semana 2026-09-14 --salvar saida.xlsx
    python -X utf8 relatorio_5100.py --semana 2026-09-14 --enviar
"""

import io
import os
import re
import sys
import time
import smtplib
import unicodedata
import collections
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication

# Antes das constantes, e não no bloco de linha de comando: elas são avaliadas no
# import, então o `.env` precisa já estar carregado quando o módulo roda como
# script. Em produção as variáveis vêm do Docker e isto é inócuo — o dotenv não
# sobrescreve o que o ambiente já define.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

D = "'public consulta_despesas_477'"
C = "'public conhecimentos_emitidos'"

EVENTO      = os.getenv('R5100_EVENTO', '5100')
HORA_BRT    = os.getenv('R5100_HORA_BRT', '08:00')
JANELA_MIN  = int(os.getenv('R5100_JANELA_DISPARO_MIN', '180'))
DESTINO     = os.getenv('R5100_TO', '')

# O CTe é emitido ANTES da despesa (p90 = 8 d). 45 cobre com folga larga sem
# inchar a consulta — a janela só alarga o lado de trás.
FOLGA_CTE_DIAS = 45

RE_CNPJFORN = re.compile(r'CNPJFORN\s*(\d{14})', re.I)
RE_NF       = re.compile(r'\bNFS?\s*[:\-\.\*\s]{0,4}?(\d{2,12})\b', re.I)


def ligado():
    return os.getenv('R5100_ENVIO', '').strip().lower() == 'true'


def destinatarios():
    return [e.strip() for e in DESTINO.split(',') if e.strip()]


# ── Motor puro (sem rede, sem banco) ─────────────────────────────────────────

def _sem_acento(s):
    return ''.join(c for c in unicodedata.normalize('NFD', s or '')
                   if unicodedata.category(c) != 'Mn')


def extrair_nf(historico):
    """historico_despesa → {'nfs': [...], 'cnpj': str|None, 'cliente': str|None}.

    O CNPJFORN sai do texto ANTES de procurar NF: são 14 dígitos colados que o
    regex de número pegaria. Ele é guardado só para diagnóstico — não vale como
    desempate (ver o cabeçalho).
    """
    txt = _sem_acento((historico or '').upper())
    cnpjs = RE_CNPJFORN.findall(txt)
    limpo = RE_CNPJFORN.sub(' ', txt)
    nfs, cliente = [], None
    for m in RE_NF.finditer(limpo):
        nfs.append(m.group(1).lstrip('0') or '0')
        cliente = limpo[m.end():]          # o que vem depois da última NF
    if cliente is not None:
        cliente = re.sub(r'^[\s\-\*\:/]+', '', cliente).strip()
        cliente = re.sub(r'\s{2,}', ' ', cliente) or None
    return {'nfs': nfs, 'cnpj': cnpjs[0] if cnpjs else None, 'cliente': cliente}


def _dia(v):
    return datetime.strptime(str(v)[:10], '%Y-%m-%d')


def indexar_ctes(ctes):
    """numero_nota_fiscal (sem zeros à esquerda) → [CTes]."""
    idx = collections.defaultdict(list)
    for c in ctes:
        nf = str(c.get('nf') or '').strip().lstrip('0')
        if nf:
            idx[nf].append(c)
    return idx


def escolher_cte(historico, emissao_despesa, idx):
    """→ dict com o CTe escolhido e o rastro de como se chegou nele.

    Com mais de um CTe para a mesma NF fica o emitido antes ou no dia da
    despesa, mais próximo. Se todos forem posteriores (não aconteceu em
    setembro, mas é possível), fica o mais próximo em valor absoluto — melhor um
    palpite datado que uma célula vazia, e o `cte_outros` mostra o resto.

    As colunas de rastro existem para a conferência não depender de fé: quem lê
    vê a NF que saiu do texto, o cliente que o histórico declarou e o remetente
    que o CTe traz — é assim que se enxerga um casamento errado.
    """
    p = extrair_nf(historico)
    vazio = {'cte': '', 'cte_outros': '', 'nf_historico': ', '.join(p['nfs']),
             'cliente_historico': p['cliente'] or '', 'cte_remetente': '',
             'cte_emissao': '', 'cte_criterio': ''}
    if not p['nfs']:
        return dict(vazio, cte_criterio='sem NF no histórico')
    cands = [c for nf in p['nfs'] for c in idx.get(nf, [])]
    if not cands:
        return dict(vazio, cte_criterio='NF sem CTe na janela')
    dd = _dia(emissao_despesa)
    anteriores = sorted([c for c in cands if (dd - _dia(c['emissao'])).days >= 0],
                        key=lambda c: (dd - _dia(c['emissao'])).days)
    esc = anteriores[0] if anteriores else min(
        cands, key=lambda c: abs((dd - _dia(c['emissao'])).days))
    criterio = ('NF única' if len(cands) == 1
                else f'{len(cands)} CTes com a NF — ficou o anterior mais próximo')
    return {'cte': esc['ctrc'],
            'cte_outros': ', '.join(c['ctrc'] for c in cands if c['ctrc'] != esc['ctrc']),
            'nf_historico': ', '.join(p['nfs']),
            'cliente_historico': p['cliente'] or '',
            'cte_remetente': esc.get('cli_rem') or '',
            'cte_emissao': str(esc['emissao'])[:10],
            'cte_criterio': criterio}


def semana_anterior(hoje=None):
    """Segunda a domingo da semana que fechou. 21/09 (2ª) → 14/09..20/09."""
    hoje = hoje or (datetime.utcnow() - timedelta(hours=3))
    segunda_desta = hoje.date() - timedelta(days=hoje.weekday())
    ini = segunda_desta - timedelta(days=7)
    return ini, ini + timedelta(days=6)


# ── Power BI ─────────────────────────────────────────────────────────────────

def _dax_data(d):
    return f'DATE({d.year},{d.month},{d.day})'


def _dax(token, query):
    """DAX contra o dataset do DRE — é onde vivem o 477 e os conhecimentos."""
    from server import execute_dax, clean_rows, CONFIG
    res = execute_dax(token, query, dataset_id=CONFIG['dre_dataset_id'])
    r = res.get('results', [{}])[0]
    if r.get('error'):
        # O executeQueries corta a resposta e devolve HTTP 200; sem esta checagem
        # o relatório sairia curto parecendo semana fraca.
        raise RuntimeError(f"DAX cortado pelo Power BI: {r['error']}")
    return clean_rows(r.get('tables', [{}])[0].get('rows', []))


def coletar(token, ini, fim, dax=None):
    """→ (despesas da semana, CTes da janela alargada)."""
    dax = dax or (lambda q: _dax(token, q))
    despesas = dax(
        f'EVALUATE FILTER(ALL({D}), {D}[evento]="{EVENTO}" && '
        f'{D}[emissao] >= {_dax_data(ini)} && {D}[emissao] <= {_dax_data(fim)})')
    a, b = ini - timedelta(days=FOLGA_CTE_DIAS), fim + timedelta(days=2)
    ctes = dax(
        f'EVALUATE SELECTCOLUMNS(FILTER(ALL({C}), '
        f'{C}[data_emissao] >= {_dax_data(a)} && {C}[data_emissao] <= {_dax_data(b)}), '
        f'"ctrc",{C}[serie_numero_ctrc], "nf",{C}[numero_nota_fiscal], '
        f'"cli_rem",{C}[cliente_remetente], "emissao",{C}[data_emissao])')
    return despesas, ctes


# O que se confere vem primeiro; o resto da tabela segue atrás, intacto.
# `nfiscal` (a NF da despesa) fica colada no `cte` de propósito: é o par que o
# olho compara. As 62 colunas do 477 que não aparecem aqui entram depois, na
# ordem original — nada é descartado.
CABECALHO = ['emissao', 'uni', 'numlancto', 'parcela', 'nome_fornecedor',
             'vlr_final', 'historico_despesa', 'nfiscal', 'cte', 'cte_outros',
             'nf_historico', 'cliente_historico', 'cte_remetente', 'cte_emissao',
             'cte_criterio']

ROTULOS = {'emissao': 'Emissao', 'uni': 'Uni', 'numlancto': 'Lancto',
           'parcela': 'Parc', 'nome_fornecedor': 'Fornecedor',
           'vlr_final': 'Valor', 'historico_despesa': 'Historico',
           'nfiscal': 'NF despesa', 'cte': 'CTe', 'cte_outros': 'Outros CTes c/ a NF',
           'nf_historico': 'NF no historico', 'cliente_historico': 'Cliente (historico)',
           'cte_remetente': 'Remetente do CTe', 'cte_emissao': 'Emissao do CTe',
           'cte_criterio': 'Criterio'}

DERIVADAS = ['cte', 'cte_outros', 'nf_historico', 'cliente_historico',
             'cte_remetente', 'cte_emissao', 'cte_criterio']


def montar(despesas, ctes):
    """→ (colunas, linhas): bloco de conferência primeiro, resto do 477 depois."""
    if not despesas:
        return [], []
    idx = indexar_ctes(ctes)
    linhas = []
    for x in despesas:
        linha = dict(x)
        linha.update(escolher_cte(x.get('historico_despesa'), x.get('emissao'), idx))
        linhas.append(linha)

    originais = list(despesas[0].keys())
    cabecalho = [c for c in CABECALHO if c in originais or c in DERIVADAS]
    resto = [c for c in originais if c not in cabecalho]
    linhas.sort(key=lambda l: (str(l.get('emissao'))[:10],
                               str(l.get('nome_fornecedor') or '')))
    return cabecalho + resto, linhas


# ── Planilha ─────────────────────────────────────────────────────────────────

COLS_MOEDA = {'vlr_final', 'vlr_nota', 'vlr_parcela', 'valor_total_produtos',
              'juros', 'descontos', 'liq_valor'}
COLS_DATA  = {'inclusao', 'vencimen', 'emissao', 'data_pgto', 'aprovacao',
              'contabil', 'data_importacao', 'liq_data_bompara', 'liq_data_extrato'}


def gerar_xlsx(cols, linhas, ini, fim):
    """→ bytes do .xlsx. A coluna `cte` sai destacada, que é o que muda."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = f'5100 {ini:%d-%m} a {fim:%d-%m}'[:31]
    # Rótulo curto nas colunas da frente (é a parte que se lê); o resto da tabela
    # mantém o nome cru da coluna do 477, que é como a aba Despesas a mostra.
    ws.append([ROTULOS.get(c, c.upper()) for c in cols])

    branco = Font(bold=True, color='FFFFFF')
    azul   = PatternFill('solid', fgColor='1F4E79')
    azul_c = PatternFill('solid', fgColor='2E75B6')
    creme  = PatternFill('solid', fgColor='FFF2CC')
    for i, c in enumerate(cols, 1):
        cel = ws.cell(row=1, column=i)
        cel.font = branco
        cel.fill = azul_c if c in DERIVADAS else azul
        cel.alignment = Alignment(horizontal='center')

    for ln in linhas:
        ws.append([ln.get(c) for c in cols])

    for i, c in enumerate(cols, 1):
        if c == 'cte':
            for r in range(2, ws.max_row + 1):
                ws.cell(row=r, column=i).fill = creme
        elif c in COLS_MOEDA:
            for r in range(2, ws.max_row + 1):
                ws.cell(row=r, column=i).number_format = '#,##0.00'
        elif c in COLS_DATA:
            # O DAX devolve ISO com hora; a planilha mostra só o dia.
            for r in range(2, ws.max_row + 1):
                cel = ws.cell(row=r, column=i)
                if cel.value:
                    cel.value = str(cel.value)[:10]
        ws.column_dimensions[get_column_letter(i)].width = {
            'historico_despesa': 58, 'nome_fornecedor': 30, 'descr_evento': 28,
            'cte': 15, 'cte_outros': 18, 'cte_remetente': 32, 'cte_criterio': 40,
            'cliente_historico': 22, 'nf_historico': 16}.get(c, 14)

    ws.freeze_panes = 'A2'
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── E-mail ───────────────────────────────────────────────────────────────────

def resumo(linhas):
    total = sum(float(l.get('vlr_final') or 0) for l in linhas)
    com = sum(1 for l in linhas if l.get('cte'))
    return {'n': len(linhas), 'total': total, 'com_cte': com,
            'sem_cte': len(linhas) - com}


def _brl(v):
    """1234.5 → '1.234,50' (a troca vale só para o número, nunca para o texto)."""
    return f'{v:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def corpo_texto(ini, fim, r):
    if not r['n']:
        return (f"Carga e descarga com terceiros (evento {EVENTO})\n"
                f"Semana de {ini:%d/%m/%Y} a {fim:%d/%m/%Y}\n\n"
                f"Nenhum lançamento na semana.\n")
    return (
        f"Carga e descarga com terceiros (evento {EVENTO})\n"
        f"Semana de {ini:%d/%m/%Y} a {fim:%d/%m/%Y}\n\n"
        f"{r['n']} lançamentos, R$ {_brl(r['total'])}.\n"
        f"{r['com_cte']} com CTe identificado pela nota fiscal do histórico; "
        f"{r['sem_cte']} sem.\n\n"
        f"A planilha vai em anexo, com as mesmas colunas da aba Despesas e a "
        f"coluna CTE logo depois do HISTORICO_DESPESA.\n"
    )


def enviar_email(assunto, corpo, anexo_bytes, nome_anexo, para=None):
    usuario = os.getenv('GMAIL_USER')
    senha = os.getenv('APP_GMAIL')
    para = para or destinatarios()
    if not (usuario and senha):
        raise RuntimeError('GMAIL_USER/APP_GMAIL não configurados')
    if not para:
        raise RuntimeError('R5100_TO vazio — sem destinatário')

    msg = MIMEMultipart()
    msg['From'] = usuario
    msg['To'] = ', '.join(para)
    msg['Subject'] = assunto
    msg.attach(MIMEText(corpo, 'plain', 'utf-8'))
    anexo = MIMEApplication(
        anexo_bytes,
        _subtype='vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    anexo.add_header('Content-Disposition', 'attachment', filename=nome_anexo)
    msg.attach(anexo)

    with smtplib.SMTP('smtp.gmail.com', 587, timeout=60) as s:
        s.starttls()
        s.login(usuario, senha)
        s.send_message(msg)
    return para


# ── Execução ─────────────────────────────────────────────────────────────────

def executar(ini=None, fim=None, enviar=True, salvar=None):
    from server import get_token
    if ini is None or fim is None:
        ini, fim = semana_anterior()
    token = get_token()
    despesas, ctes = coletar(token, ini, fim)
    cols, linhas = montar(despesas, ctes)
    r = resumo(linhas)

    # Semana sem lançamento também é enviada: silêncio não separa "não houve
    # carregamento" de "o job morreu" — mesma regra do PGR.
    xlsx = gerar_xlsx(cols or ['sem_dados'], linhas, ini, fim)
    nome = f'carga_descarga_{ini:%Y-%m-%d}_a_{fim:%Y-%m-%d}.xlsx'
    if salvar:
        with open(salvar, 'wb') as f:
            f.write(xlsx)
    enviados = []
    if enviar:
        enviados = enviar_email(
            f'Carga e descarga terceiros — {ini:%d/%m} a {fim:%d/%m}',
            corpo_texto(ini, fim, r), xlsx, nome)
    return {'ini': str(ini), 'fim': str(fim), 'enviado_para': enviados, **r}


def _hora_agendada():
    """R5100_HORA_BRT → (hora, minuto). Formato inválido cai no default."""
    try:
        h, m = HORA_BRT.strip().split(':')
        h, m = int(h), int(m)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except (ValueError, AttributeError):
        pass
    print(f'⚠️  R5100_HORA_BRT inválido ({HORA_BRT!r}) — usando 08:00')
    return 8, 0


def loop():
    """Segunda-feira, no horário de Brasília. Nunca derruba o processo.

    O marcador é a SEMANA enviada, não a data de execução: restart no meio da
    manhã de segunda não manda o e-mail duas vezes, e a janela de tolerância
    cobre um container que subiu depois do horário.
    """
    hh, mm = _hora_agendada()
    print(f'✅ Relatório 5100 agendado para segunda-feira {hh:02d}:{mm:02d} '
          f'(Brasília) → {", ".join(destinatarios()) or "SEM DESTINATÁRIO"}')
    ultima_semana = None
    while True:
        try:
            agora = datetime.utcnow() - timedelta(hours=3)
            if agora.weekday() == 0:                       # segunda
                marcado = agora.replace(hour=hh, minute=mm, second=0, microsecond=0)
                atraso = (agora - marcado).total_seconds() / 60
                ini, fim = semana_anterior(agora)
                if 0 <= atraso < JANELA_MIN and ultima_semana != ini:
                    ultima_semana = ini
                    r = executar(ini, fim)
                    print(f"📧 Relatório 5100 {r['ini']}..{r['fim']}: {r['n']} lançamentos, "
                          f"R$ {r['total']:,.2f} → {', '.join(r['enviado_para'])}")
        except Exception as e:
            # Falhou hoje? `ultima_semana` já está marcada e não retenta sozinho:
            # e-mail repetido é pior que e-mail faltando, e o log mostra o motivo.
            print(f'⚠️  Relatório 5100: falha no envio: {e}')
        time.sleep(60)


if __name__ == '__main__':
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--semana', help='segunda-feira da semana (YYYY-MM-DD); '
                                     'o padrão é a semana que fechou')
    ap.add_argument('--dry-run', action='store_true', help='só mostra o resumo')
    ap.add_argument('--salvar', help='grava o .xlsx neste caminho')
    ap.add_argument('--enviar', action='store_true', help='envia o e-mail')
    a = ap.parse_args()

    if a.semana:
        ini = datetime.strptime(a.semana, '%Y-%m-%d').date()
        ini -= timedelta(days=ini.weekday())
        fim = ini + timedelta(days=6)
    else:
        ini, fim = semana_anterior()

    if a.dry_run and not a.salvar:
        from server import get_token
        despesas, ctes = coletar(get_token(), ini, fim)
        cols, linhas = montar(despesas, ctes)
        r = resumo(linhas)
        print(f'{ini} a {fim} · {r["n"]} lançamentos · R$ {r["total"]:,.2f} · '
              f'com CTe {r["com_cte"]} · sem {r["sem_cte"]}')
        for l in linhas:
            print(f'  {str(l["emissao"])[:10]} {str(l.get("nome_fornecedor") or "")[:24]:24s} '
                  f'{float(l.get("vlr_final") or 0):>9,.2f}  '
                  f'{(l.get("cte") or "—"):<14} {str(l.get("historico_despesa") or "")[:52]}')
        sys.exit(0)

    print(executar(ini, fim, enviar=a.enviar, salvar=a.salvar))
