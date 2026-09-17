# -*- coding: utf-8 -*-
"""Conferência CIOT — imagem para o WhatsApp.

Mesmo molde do `pgr_imagem.py` (fitz.Story, fonte mono embutida, fundo escuro): a
imagem é o resumo legível no celular; a lista completa, copiável, fica na aba /ciot.
Uma linha por DOCUMENTO, como na tela e no texto.

Cores: vermelho = falta CIOT (a multa) · laranja = falta amarrar (com CIOT) ·
verde = o CIOT está lá.
"""

import io
import os
import logging

import fitz

_logger = logging.getLogger(__name__)

DIR_FONTES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts')
LARGURA = 620
FUNDO = '#0a0e17'
_RGB_FUNDO = (0x0a / 255, 0x0e / 255, 0x17 / 255)

_CSS = """
* { font-family: sans-serif; }
.mono { font-family: jbmono; }
"""

VERMELHO, LARANJA, VERDE, CINZA, TEXTO, AZUL = '#f87171', '#fb923c', '#34d399', '#94a3b8', '#e2e8f0', '#38bdf8'

# Rótulo curto e cor de cada pendência
_ROTULO = {
    'sem_ciot': ('sem CIOT', VERMELHO),
    'ciot_erro': ('CIOT com erro', VERMELHO),
    'ctrb_sem_manifesto': ('sem manifesto', LARANJA),
    'manifesto_sem_ctrb': ('MDF sem CTRB', VERMELHO),
    'vinculo_divergente': ('dados diferentes do manifesto', LARANJA),
}


def _esc(s):
    return (str(s if s is not None else '')
            .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def montar_html(dados):
    """`dados` (ver ciot_conferencia.dados_aviso):
        titulo, subtitulo, contadores [(rótulo, n, cor)], legenda_lista,
        documentos [{documento, tipo_operacao, placa, emissao_br, novo, tipos, tem_ciot,
                     detalhes}], rodape
    """
    cab = (f'<div style="color:{AZUL};font-size:7.5pt;letter-spacing:1pt">RIZZA TRANSPORTES &#183; CONFERÊNCIA CIOT</div>'
           f'<div style="font-size:14pt;color:{TEXTO};margin:2pt 0"><b>{_esc(dados["titulo"])}</b></div>'
           f'<div class="mono" style="color:{CINZA};font-size:8pt">{_esc(dados["subtitulo"])}</div>')

    if not dados['documentos'] and not dados['contadores']:
        corpo = (f'<div style="margin-top:10pt;color:{VERDE};font-size:11pt"><b>Nada em aberto</b></div>'
                 f'<div class="mono" style="color:{CINZA};font-size:8.5pt;margin-top:3pt">'
                 f'{_esc(dados.get("rodape") or "")}</div>')
        return _envelope(cab + corpo)

    # um por linha: são categorias exclusivas por documento e somam o total
    kpi = '<br>'.join(f'<b style="color:{cor}">{n:>2}</b> {_esc(rot)}' for rot, n, cor in dados['contadores'])
    kpi = f'<div class="mono" style="color:{CINZA};font-size:8.5pt;margin-top:6pt;line-height:1.5">{kpi}</div>'

    lista = ''
    if dados.get('legenda_lista'):
        lista = (f'<div style="color:{CINZA};font-size:7.5pt;margin-top:8pt;letter-spacing:.5pt">'
                 f'{_esc(dados["legenda_lista"]).upper()}</div>')

    tr = []
    for d in dados['documentos']:
        novo = f'<span style="color:{AZUL};font-size:6.5pt">NOVO </span>' if d.get('novo') else ''
        sub = ' &#183; '.join(_esc(x) for x in (d.get('tipo_operacao'), d.get('placa')) if x)
        probs = ' &#183; '.join(
            f'<b style="color:{_ROTULO[t][1]}">{_ROTULO[t][0]}</b>' for t in d['tipos'] if t in _ROTULO)
        if d.get('tem_ciot'):
            probs += f' &#183; <b style="color:{VERDE}">tem CIOT</b>'
        det = ''.join(f'<div style="color:#cbd5e1;font-size:7pt">&#160;{_esc(x)}</div>' for x in d.get('detalhes') or [])
        tr.append(
            f'<tr>'
            f'<td style="font-size:9.5pt;color:{TEXTO}">{novo}<b class="mono">{_esc(d["documento"])}</b>'
            f'<div style="font-size:6.5pt;color:{CINZA}">{sub}</div></td>'
            f'<td class="mono" style="font-size:8pt;color:{CINZA};text-align:right">{_esc(d.get("emissao_br") or "")}</td>'
            f'<td style="font-size:8pt">&#160;{probs}{det}</td>'
            f'</tr>')
    tabela = f'<table style="width:100%;margin-top:4pt">{"".join(tr)}</table>' if tr else ''

    rodape = ''
    if dados.get('rodape'):
        rodape = (f'<div class="mono" style="color:#64748b;font-size:8pt;margin-top:7pt">'
                  f'{_esc(dados["rodape"])}</div>')
    return _envelope(cab + kpi + lista + tabela + rodape)


def _envelope(interno):
    return f'<div style="background-color:{FUNDO};padding:14pt">{interno}</div>'


def gerar_png(dados, dpi=150):
    """Renderiza em PNG (mesma técnica do pgr_imagem: mede, renderiza, pinta o fundo)."""
    html = montar_html(dados)
    arquivo, css = None, _CSS
    if os.path.isdir(DIR_FONTES):
        arquivo = fitz.Archive(DIR_FONTES)
        css = ('@font-face { font-family: jbmono; src: url(JetBrainsMono-Regular.ttf); }\n'
               '@font-face { font-family: jbmono; font-weight: bold;'
               ' src: url(JetBrainsMono-Bold.ttf); }\n') + _CSS
    else:
        _logger.warning('CIOT imagem: fonts/ ausente — caindo para fonte padrão')

    def _story():
        return fitz.Story(html=html, user_css=css, archive=arquivo)

    _, preenchido = _story().place(fitz.Rect(0, 0, LARGURA, 6000))
    y1 = preenchido.y1 if hasattr(preenchido, 'y1') else preenchido[3]
    altura = max(60, y1 + 14)

    buf = io.BytesIO()
    writer = fitz.DocumentWriter(buf)
    story = _story()
    pagina = fitz.Rect(0, 0, LARGURA, altura)
    mais = 1
    while mais:
        dev = writer.begin_page(pagina)
        mais, _ = story.place(pagina)
        story.draw(dev)
        writer.end_page()
    writer.close()

    origem = fitz.Document('pdf', buf.getvalue())
    final = fitz.open()
    destino = final.new_page(width=LARGURA, height=altura)
    destino.draw_rect(destino.rect, color=_RGB_FUNDO, fill=_RGB_FUNDO)
    destino.show_pdf_page(destino.rect, origem, 0)
    return destino.get_pixmap(dpi=dpi).tobytes('png')
