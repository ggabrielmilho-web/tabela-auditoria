# -*- coding: utf-8 -*-
"""PGR — imagem-resumo para o WhatsApp.

A mensagem leva uma IMAGEM com o que couber legível, mais o link para a lista
completa. A imagem é resumo; a página é o relatório inteiro. São artefatos
diferentes por desenho, então não há conteúdo para manter em sincronia — só a
linguagem visual (faixas de cor, forma de escrever placa e pico).

Renderiza com `fitz.Story` (PyMuPDF), que já é dependência do projeto — o
módulo de Contratos usa `fitz` para rasterizar PDF. Não precisa de navegador
headless no servidor.

Limites conhecidos do Story: subconjunto de CSS (sem flexbox/grid — layout em
tabela) e fontes precisam ser embutidas por arquivo.
"""

import io
import os
import logging

import fitz

_logger = logging.getLogger(__name__)

DIR_FONTES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts')

# No celular, a imagem do WhatsApp aparece com ~1/3 da tela antes de precisar
# abrir. Acima disso a lista deixa de ser escaneável e a imagem perde a razão
# de existir.
MAX_LINHAS = int(os.getenv('PGR_IMG_MAX_LINHAS', '12'))
# Dia magro cabe inteiro: obrigar a clicar para ver duas linhas não faz sentido.
LINHAS_SEM_CORTE = int(os.getenv('PGR_IMG_SEM_CORTE', '3'))

LARGURA = 620

_CSS = """
* { font-family: sans-serif; }
.mono, .placa, .pico, .reg, .kpi-v { font-family: jbmono; }
"""


# Mesma semântica de cor da página: laranja carregado, amarelo parcial,
# apagado vazio, contorno vazio para não confirmado (aqui, cinza).
_SITUACAO = {
    'carregado': ('#fb923c', 'carregado'),
    'parcial': ('#fbbf24', 'parcial'),
    'vazio': ('#94a3b8', 'vazio'),
    'nao_confirmado': ('#64748b', 'não confirmado'),
}


def _cor_faixa(pico):
    """Mesma gravidade da página: amarelo 96–102 · laranja 103–109 · vermelho 110+."""
    if pico >= 110:
        return '#f87171'
    if pico >= 103:
        return '#fb923c'
    return '#fbbf24'


def ordenar_para_imagem(linhas):
    """Sustentado primeiro, depois recorrência, depois pico.

    DIFERENTE da página, que ordena por gravidade — e de propósito. 81% dos
    episódios são pico isolado: um caminhão que tocou 111 uma vez numa descida
    é ruído; um que fez 13 registros ao longo de GO e TO é conduta. Ordenar as
    6 linhas da imagem por pico mostraria o ruído e esconderia a conduta, que
    é exatamente o contrário do que serve para cobrar.
    """
    return sorted(linhas, key=lambda l: (not l.get('sustentado'),
                                         -(l.get('registros') or 0),
                                         -(l.get('pico') or 0)))


def _escapar(s):
    return (str(s if s is not None else '')
            .replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def _rodovia(l):
    """Rodovia do pico — é o que torna o recado acionável sem abrir nada.
    'Anhangüera' diz mais ao diretor do que 'Araras/SP'."""
    ep = l.get('episodios') or []
    for e in sorted(ep, key=lambda x: -(x.get('pico') or 0)):
        if e.get('endereco'):
            return e['endereco']
    return (l.get('cidades') or [''])[0]


def montar_html(dados):
    """HTML do resumo. Devolve (html, n_mostradas, n_ocultas)."""
    t = dados['totais']
    a, m, d = dados['dia'].split('-')
    data_br = f'{d}/{m}/{a}'
    linhas = ordenar_para_imagem(dados['linhas'])

    cab = (f'<div style="color:#38bdf8;font-size:7.5pt;letter-spacing:1pt">RIZZA TRANSPORTES &#183; PGR</div>'
           f'<div style="font-size:14pt;color:#e2e8f0;margin:2pt 0"><b>Excessos acima de {dados["limiar"]} km/h</b></div>'
           f'<div class="mono" style="color:#94a3b8;font-size:8pt">{data_br} &#183; horário de Brasília</div>')

    # Dia zerado: uma linha, e o contador de cobertura é o que separa "frota
    # comportada" de "sistema cego". Silêncio seria ambíguo.
    if not linhas:
        corpo = (f'<div style="margin-top:10pt;color:#34d399;font-size:11pt"><b>Nenhum excesso registrado</b></div>'
                 f'<div class="mono" style="color:#94a3b8;font-size:8.5pt;margin-top:3pt">'
                 f'{t["placas_monitoradas"]} veículos monitorados no dia</div>')
        return _envelope(cab + corpo), 0, 0

    corta = len(linhas) > LINHAS_SEM_CORTE and len(linhas) > MAX_LINHAS
    mostradas = linhas[:MAX_LINHAS] if corta else linhas
    ocultas = len(linhas) - len(mostradas)

    kpi = (f'<div class="mono" style="color:#94a3b8;font-size:9pt;margin-top:6pt">'
           f'<b style="color:#e2e8f0">{t["veiculos"]}</b> veículos &#183; '
           f'<b style="color:#f87171">{t["registros"]}</b> registros &#183; '
           f'pico <b style="color:#f87171">{t["pico"]}</b> km/h &#183; '
           f'<b style="color:#fb923c">{t["sustentados"]}</b> sustentados</div>')

    tr = []
    for l in mostradas:
        selo = ('<span style="color:#fb923c;font-size:6.5pt"> SUST</span>'
                if l.get('sustentado') else '')
        tipo = (l.get('tipo_veiculo') or '').upper()
        abrev = {'CARRETA': 'car', 'CAVALO': 'cav', 'TRUCK': 'trk'}.get(tipo, tipo[:3].lower())
        op = (l.get('tipo_operacao') or '').lower()
        cor_op = '#38bdf8' if op == 'frota' else '#818cf8'
        sub = (f'{abrev}' + (f' &#183; <span style="color:{cor_op}">{op}</span>' if op else ''))

        cor_sit, rot = _SITUACAO[l.get('situacao_carga', 'nao_confirmado')]
        ctx = ''
        if l.get('tomador'):
            rota = ' &#8594; '.join(x for x in (l.get('origem'), l.get('destino')) if x)
            ctx = f' &#183; {_escapar(l["tomador"])}'
            if rota:
                ctx += f' &#183; {_escapar(rota)}'
            if l.get('motorista'):
                ctx += f' &#183; {_escapar(l["motorista"])}'
        else:
            ctx = ' &#183; &#8212;'

        cidades = ', '.join(_escapar(c) for c in (l.get('cidades') or [])[:8])

        tr.append(
            f'<tr>'
            f'<td style="font-size:9.5pt;color:#e2e8f0"><b class="mono">{_escapar(l["placa"])}</b>'
            f'<div style="font-size:6pt;color:#94a3b8">{sub}</div></td>'
            f'<td class="reg" style="font-size:8.5pt;color:#94a3b8;text-align:right">{l["registros"]}x</td>'
            f'<td class="pico" style="font-size:11pt;color:{_cor_faixa(l["pico"])};text-align:right">{l["pico"]}</td>'
            f'<td style="font-size:7.5pt">'
            f'<div style="color:#94a3b8">&#160;<span style="color:{cor_sit}">&#9679; <b>{rot}</b></span>{ctx}{selo}</div>'
            f'<div style="color:#cbd5e1;font-size:7.5pt">&#160;{cidades}</div>'
            f'</td>'
            f'</tr>')
    # Sem `width` nos <td>: o Story não suporta e as colunas colapsam umas
    # sobre as outras. A largura sai do conteúdo, e a mono mantém o alinhamento.
    tabela = f'<table style="width:100%;margin-top:7pt">{"".join(tr)}</table>'

    # O corte precisa ser EXPLÍCITO: sem isto o diretor lê 6 e entende que
    # foram 6. É erro de informação, não de estética.
    rodape = ''
    if ocultas:
        rodape = (f'<div class="mono" style="color:#64748b;font-size:8pt;margin-top:7pt">'
                  f'+{ocultas} {"veículo" if ocultas == 1 else "veículos"} '
                  f'&#183; lista completa no relatório</div>')

    return _envelope(cab + kpi + tabela + rodape), len(mostradas), ocultas


def _envelope(interno):
    return (f'<div style="background-color:#0a0e17;padding:14pt">{interno}</div>')


def gerar_png(dados, dpi=150):
    """Renderiza o resumo em PNG. Devolve (bytes, n_mostradas, n_ocultas)."""
    html, mostradas, ocultas = montar_html(dados)

    arquivo = None
    css = _CSS
    if os.path.isdir(DIR_FONTES):
        arquivo = fitz.Archive(DIR_FONTES)
        # A mono é a que alinha placa e número em coluna — sem ela a lista
        # deixa de ser escaneável. A sans caindo para a de sistema custa pouco.
        css = ('@font-face { font-family: jbmono; src: url(JetBrainsMono-Regular.ttf); }\n'
               '@font-face { font-family: jbmono; font-weight: bold;'
               ' src: url(JetBrainsMono-Bold.ttf); }\n') + _CSS
    else:
        _logger.warning('PGR imagem: fonts/ ausente — caindo para fonte padrão')

    def _story():
        return fitz.Story(html=html, user_css=css, archive=arquivo)

    # 1ª passada mede a altura ocupada; 2ª renderiza no tamanho exato, senão
    # sobra faixa vazia embaixo da imagem.
    medida = fitz.Rect(0, 0, LARGURA, 4000)
    _, preenchido = _story().place(medida)
    # place() devolve o retângulo ocupado como Rect ou como tupla, conforme a
    # versão do PyMuPDF.
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

    doc = fitz.Document('pdf', buf.getvalue())
    pix = doc[0].get_pixmap(dpi=dpi)
    return pix.tobytes('png'), mostradas, ocultas
