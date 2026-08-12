# -*- coding: utf-8 -*-
"""PGR — envio do relatório diário por WhatsApp (UazAPI).

Passo 3 do job diário, DEPOIS do backfill e da apuração. Lê de `pgr_eventos`
em vez de recalcular: é o que garante que a mensagem e a página `/pgr` mostrem
sempre o mesmo número — o problema que a aba Veículos já teve com KM do drawer
× KM da tabela.

Vive dentro do app (e não como script avulso na máquina da Rizza, como o
`tabela_auditoria_relatorio.py`) porque a ordem backfill → apuração → envio não
pode ser coordenada por relógio entre duas máquinas: apurar antes do backfill
entrega ~81% de cobertura em vez de 100%, e ninguém percebe. Também não precisa
de Selenium: a imagem é renderizada em processo pelo `pgr_imagem`.

Política de falha copiada do `uazapi_helper.py` da Rizza, de propósito:
  - nenhuma função propaga exceção; sempre devolve bool
  - SEM retry automático (risco de banimento na UazAPI)
  - sem credencial configurada, devolve False sem nem tentar

Variáveis de ambiente:
    UAZAPI_URL      https://<instancia>.uazapi.com
    UAZAPI_TOKEN    token da instância
    PGR_UAZAPI_TO   destinatário(s), separados por vírgula
    PGR_BASE_URL    https://rizza.carvalhoia.com  (base do link do relatório)
"""

import os
import re
import base64
import logging

import requests

import pgr
import pgr_imagem

_logger = logging.getLogger(__name__)

UAZAPI_URL = (os.getenv('UAZAPI_URL', '') or '').rstrip('/')
UAZAPI_TOKEN = os.getenv('UAZAPI_TOKEN', '') or ''
DESTINATARIOS = os.getenv('PGR_UAZAPI_TO', '') or ''
# Aceita os dois nomes: a stack de produção subiu com PGR_URL e a divergência
# falhava em silêncio — a mensagem saía sem link e nada no log gritava.
BASE_URL = (os.getenv('PGR_BASE_URL') or os.getenv('PGR_URL') or '').rstrip('/')
ENVIO_ATIVO = os.getenv('PGR_ENVIO', 'false').lower() == 'true'

TIMEOUT = 30


def _configurado():
    return bool(UAZAPI_URL and UAZAPI_TOKEN and DESTINATARIOS)


def normalizar_numero(raw):
    """'+55 (62) 9 9999-9999' → '5562999999999'. Garante DDI 55."""
    d = re.sub(r'\D', '', raw or '')
    if not d:
        return ''
    return d if d.startswith('55') else '55' + d


def _numeros():
    return [n for n in (normalizar_numero(x) for x in DESTINATARIOS.split(',')) if n]


def enviar_imagem(numero, file_bytes, caption=None):
    if not _configurado() or not file_bytes:
        return False
    try:
        payload = {'number': numero, 'type': 'image',
                   'file': base64.b64encode(file_bytes).decode('utf-8')}
        if caption:
            payload['text'] = caption
        r = requests.post(f'{UAZAPI_URL}/send/media', json=payload,
                          headers={'token': UAZAPI_TOKEN, 'Content-Type': 'application/json'},
                          timeout=TIMEOUT)
        if r.status_code in (200, 201):
            return True
        _logger.warning(f'UazAPI falha imagem: HTTP {r.status_code} — {r.text[:200]}')
        return False
    except Exception as e:
        _logger.warning(f'UazAPI exceção no envio de imagem: {e}')
        return False


def enviar_texto(numero, texto):
    if not _configurado():
        return False
    try:
        r = requests.post(f'{UAZAPI_URL}/send/text',
                          json={'number': numero, 'text': texto},
                          headers={'token': UAZAPI_TOKEN, 'Content-Type': 'application/json'},
                          timeout=TIMEOUT)
        if r.status_code in (200, 201):
            return True
        _logger.warning(f'UazAPI falha texto: HTTP {r.status_code} — {r.text[:200]}')
        return False
    except Exception as e:
        _logger.warning(f'UazAPI exceção no envio de texto: {e}')
        return False


def montar_legenda(dados, url, ocultas=0):
    """Legenda da imagem. Curta: a imagem já carrega a lista e os números.

    Mesmo no dia zerado a mensagem SAI. Silêncio é ambíguo — o diretor não
    saberia se ninguém correu ou se o job caiu. Num relatório de segurança,
    ausência de mensagem não pode significar ausência de violação.
    """
    t = dados['totais']
    a, m, d = dados['dia'].split('-')
    data_br = f'{d}/{m}'

    if not dados['linhas']:
        corpo = (f'*PGR · {data_br}* — nenhum excesso acima de {dados["limiar"]} km/h\n'
                 f'{t["placas_monitoradas"]} veículos monitorados')
        return corpo if not url else f'{corpo}\n\n{url}'

    linhas = [f'🚛 *PGR · {data_br}* — acima de {dados["limiar"]} km/h',
              f'{t["veiculos"]} veículos · {t["registros"]} registros · '
              f'pico {t["pico"]} km/h · {t["sustentados"]} sustentados']
    if ocultas:
        # Sem URL, o aviso do corte ainda precisa existir — senão quem recebe lê
        # as linhas da imagem e entende que foram só aquelas. Mas o rótulo do
        # link só entra se houver link, senão a frase fica pendurada.
        linhas.append(f'\nA imagem mostra os {t["veiculos"] - ocultas} de '
                      f'{t["veiculos"]} veículos.')
    if url:
        linhas.append('\n📄 relatório completo:')
        linhas.append(url)
    return '\n'.join(linhas)


def enviar_relatorio(cur, dia):
    """Renderiza e envia o relatório do dia. Devolve nº de envios bem-sucedidos.

    `cur` precisa estar numa transação que o chamador comita: o token do dia é
    gravado aqui.
    """
    if not ENVIO_ATIVO:
        _logger.info('PGR: envio desligado (PGR_ENVIO != true)')
        return 0
    if not _configurado():
        _logger.warning('PGR: UazAPI não configurada (UAZAPI_URL/TOKEN/PGR_UAZAPI_TO) — '
                        'relatório apurado mas não enviado')
        return 0

    dados = pgr.listar_dia(cur, dia)
    url = ''
    if BASE_URL:
        token = pgr.token_do_dia(cur, dia)
        url = f'{BASE_URL}/pgr?data={dia.isoformat()}&t={token}'
    else:
        _logger.warning('PGR_BASE_URL vazio — mensagem sairá sem link')

    png, _mostradas, ocultas = pgr_imagem.gerar_png(dados)
    legenda = montar_legenda(dados, url, ocultas)

    ok = 0
    for numero in _numeros():
        # Sem retry, por política: reenviar em falha é risco de banimento.
        if enviar_imagem(numero, png, caption=legenda):
            ok += 1
            _logger.info(f'PGR {dia:%d/%m}: enviado para {numero}')
        else:
            _logger.warning(f'PGR {dia:%d/%m}: FALHA no envio para {numero}')
    return ok
