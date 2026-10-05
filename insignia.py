# -*- coding: utf-8 -*-
"""Cliente da API da Insignia GR (web service SOAP "IntegraGR") — SÓ LEITURA.

A Insignia é a gerenciadora de risco: monitora toda carga com SM (solicitação de monitoramento)
— os TERCEIROS, que a 3S não vê, e frota/agregado conforme o valor da carga, pelo rastreador do
CAVALO (Autotrac/Onixsat/Omnilink; a 3S lê a CARRETA). Testado em 05/10/2026 na base de produção:

  Get_ConsultaVeiculoEmViagem   as SMs em aberto: placas, motorista, valor, origem/destino com
                                lat/lng, rota de operações, última posição e ÚLTIMA MACRO do motorista
  Get_ConsultaPosicaoOdometro   posição atual + ignição + odômetro de uma lista de placas
  Get_ConsultaTempoParado       CADA parada (início, fim, duração, local em texto) — até 30 dias
                                por chamada; validado contra a 3S: 96% (parear pela CARGA)
  Get_ConsultaShapeViagem       a ROTA PLANEJADA da SM (sem horário) e os pontos obrigatórios
  Get_ConsultaCNPJ              cadastro de locais da GR: ponto, raio e polígono por CNPJ

Fatos da API que não estão na documentação (medidos):
  * a unidade de negócios é o CNPJ da Rizza Transp (02572512000158);
  * a placa vai e volta COM hífen ("AXT-6E87"); sem hífen a placa não é encontrada;
  * os horários são de BRASÍLIA (com UTC a concordância com a 3S cai de 96% para 41%);
  * `dDh_Chegada`/`dDh_Saida` dos pontos da SM vêm zerados (0000-00-00) — o evento que existe é
    a macro digitada pelo motorista, e a API devolve só a ÚLTIMA (a sequência sai do polling).

⚠️ 14 das 23 operações do serviço são de ESCRITA (cancelar/finalizar viagem, solicitar
monitoramento, alterar veículo…) e mexem no monitoramento da GR. `chamar()` recusa tudo que não
começa com `Get_` — não remover essa trava.

Credenciais pelo ambiente: INSIGNIA_USER, INSIGNIA_SENHA, INSIGNIA_TOKEN, INSIGNIA_CNPJ_UNIDADE.
"""
import os
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from xml.sax.saxutils import escape

URL = os.getenv('INSIGNIA_URL', 'http://sigma.insigniagr.com/insigWebService/IntegraGR.wso')
NS = 'http://tempuri.org/'
TIMEOUT = int(os.getenv('INSIGNIA_TIMEOUT_S', '120'))


class ErroInsignia(RuntimeError):
    pass


def configurado():
    return all(os.getenv(k) for k in ('INSIGNIA_USER', 'INSIGNIA_SENHA', 'INSIGNIA_TOKEN'))


def unidade():
    return os.getenv('INSIGNIA_CNPJ_UNIDADE', '02572512000158')


def _login():
    return ('<Login>'
            f"<sUserName>{escape(os.getenv('INSIGNIA_USER', ''))}</sUserName>"
            f"<sPassWord>{escape(os.getenv('INSIGNIA_SENHA', ''))}</sPassWord>"
            f"<sToken>{escape(os.getenv('INSIGNIA_TOKEN', ''))}</sToken>"
            '</Login>')


def chamar(operacao, corpo=''):
    """Envelope exatamente como o exemplo da documentação. Devolve o elemento `<op>Result`."""
    if not operacao.startswith('Get_'):
        raise ErroInsignia(f'{operacao}: só consultas (Get_) — escrita mexe no monitoramento da GR')
    env = ('<?xml version="1.0" encoding="utf-8"?>'
           '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
           f'<{operacao} xmlns="{NS}">{_login()}{corpo}</{operacao}>'
           '</soap:Body></soap:Envelope>')
    req = urllib.request.Request(URL, data=env.encode('utf-8'),
                                 headers={'Content-Type': 'text/xml; charset=utf-8', 'SOAPAction': '""'})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raiz = ET.fromstring(r.read())
    res = raiz.find(f'.//{{{NS}}}{operacao}Result')
    if res is None:
        raise ErroInsignia(f'{operacao}: resposta sem {operacao}Result')
    return res


# ── leitura do XML ──────────────────────────────────────────────────────────────
def _tag(el):
    return el.tag.split('}', 1)[-1]


def para_dict(el):
    """Elemento → dict. Filhos repetidos ou do tipo `st…` (as listas do serviço) viram lista;
    folhas viram texto. Nada é descartado: é o que vai para o `payload` bruto."""
    filhos = list(el)
    if not filhos:
        return (el.text or '').strip()
    out = {}
    for f in filhos:
        k = _tag(f)
        v = para_dict(f)
        if k in out:
            if not isinstance(out[k], list):
                out[k] = [out[k]]
            out[k].append(v)
        elif k.startswith('st') or k in ('string',):
            out[k] = [v]
        else:
            out[k] = v
    return out


def lista(d, *caminho):
    """d['A']['stB'] → sempre lista (o serviço manda vazio como '' e um item como dict)."""
    for k in caminho:
        if not isinstance(d, dict):
            return []
        d = d.get(k)
    if d in (None, ''):
        return []
    return d if isinstance(d, list) else [d]


def retornos(d):
    """Códigos de retorno (OK0001, ER0044…) onde quer que o serviço os ponha."""
    r = d.get('Retorno') if isinstance(d, dict) else None
    itens = lista(d, 'Retorno', 'stResult') or ([r] if isinstance(r, dict) else [])
    if not itens and isinstance(d, dict) and 'sCode' in d:
        itens = [d]
    return [(i.get('sCode', ''), i.get('sResult', '')) for i in itens if isinstance(i, dict)]


def dt(v):
    """Data da API (Brasília) → datetime ingênuo em BRT; o zerado '0000-00-00…' vira None."""
    if not v or str(v).startswith('0000'):
        return None
    try:
        return datetime.fromisoformat(str(v)[:19])
    except ValueError:
        return None


def num(v):
    try:
        return float(str(v).replace(',', '.')) if v not in (None, '') else None
    except ValueError:
        return None


def placa_api(p):
    """Placa no formato que a API aceita: AAA-9A99 (com hífen)."""
    s = ''.join(ch for ch in str(p or '').upper() if ch.isalnum())
    return f'{s[:3]}-{s[3:]}' if len(s) == 7 else s


# ── consultas ───────────────────────────────────────────────────────────────────
def viagens():
    """SMs em aberto da unidade. Devolve (lista de dicts crus, retornos)."""
    d = para_dict(chamar('Get_ConsultaVeiculoEmViagem',
                         f'<sCd_CnpjUnidNeg>{unidade()}</sCd_CnpjUnidNeg>'))
    rets = retornos(d)
    if rets and not rets[0][0].startswith('OK'):
        raise ErroInsignia(f'Get_ConsultaVeiculoEmViagem: {rets}')
    return lista(d, 'Veiculo', 'stVeiculoEmViagem'), rets


def posicoes(placas):
    """Posição + odômetro de várias placas numa chamada. Placa não encontrada vem com ER0022."""
    if not placas:
        return []
    corpo = (f'<DadosCP><nCd_CnpjUnidNeg>{unidade()}</nCd_CnpjUnidNeg><sCd_Placas>'
             + ''.join(f'<string>{escape(placa_api(p))}</string>' for p in placas)
             + '</sCd_Placas></DadosCP>')
    d = para_dict(chamar('Get_ConsultaPosicaoOdometro', corpo))
    return lista(d, 'Veiculos', 'VeiculoCPRespOdo')


def tempo_parado(placa, inicio, fim):
    """Paradas de UMA placa entre `inicio` e `fim` (datetimes BRT; a API aceita até 30 dias)."""
    corpo = (f'<DadosTP><nCd_CnpjUnidNeg>{unidade()}</nCd_CnpjUnidNeg>'
             f'<sCd_Placas>{escape(placa_api(placa))}</sCd_Placas>'
             f'<dtInicio>{inicio:%Y-%m-%dT%H:%M:%S}</dtInicio><dtFim>{fim:%Y-%m-%dT%H:%M:%S}</dtFim></DadosTP>')
    d = para_dict(chamar('Get_ConsultaTempoParado', corpo))
    return d.get('sSerial', '').strip() if isinstance(d, dict) else '', lista(d, 'Paradas', 'stParadas'), retornos(d)


def shape(sm):
    """Rota planejada da SM: (pontos de rota [ORIGEM/ENTREGA/PTOBRIG/DESTINO], desenho da estrada)."""
    d = para_dict(chamar('Get_ConsultaShapeViagem',
                         f'<sCd_CnpjUnidNeg>{unidade()}</sCd_CnpjUnidNeg><iCd_Viagem>{int(sm)}</iCd_Viagem>'))
    return lista(d, 'PontosRota', 'stPontoRota'), lista(d, 'SequenciaShape', 'stShapeRota'), retornos(d)


def locais():
    """Cadastro de locais da GR, todas as páginas (o serviço pagina por `iCodigoInicial`)."""
    todos, cod = [], 0
    for _ in range(200):
        d = para_dict(chamar('Get_ConsultaCNPJ',
                             f'<Controle><iCodigoInicial>{cod}</iCodigoInicial></Controle>'))
        regs = lista(d, 'DadosCNPJ', 'stDadosCNPJ')
        if not regs:
            break
        todos += regs
        ult = int(d.get('iUltCodigo') or 0)
        if ult <= cod:
            break
        cod = ult
    return todos
