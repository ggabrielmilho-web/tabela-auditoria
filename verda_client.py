# -*- coding: utf-8 -*-
"""
Cliente HTTP da Verda.

Cobre as quatro APIs que a Rizza usa: `Token`, `Fuel`, `Weight`, `GetTransaction`
e `CancelTransaction`. O resto do catálogo (Passenger, LastMile, GeneralLoad,
Ecommerce) não é a operação daqui.

MODO SIMULADO: `VERDA_SIMULADO=1` faz o cliente responder no formato REAL da API,
sem rede. Serve para exercitar o robô inteiro — estado, idempotência, polling, job
— sem gastar transação na plataforma. O estado guarda o ambiente `simulado`
separado, então nada do que se exercita aqui conta como enviado de verdade.

Cuidados que a documentação impõe:
  - `ExpiresOn` do token vem em **UTC** (pg. 7). Comparar com hora local derruba
    a sessão ou a estica demais; aqui é UTC com margem de segurança.
  - `Scope` no Token leva TODAS as APIs separadas por espaço; em cada chamada
    leva só o nome daquela API (pgs. 6 e 23).
  - O POST devolve só Success/Message/TransactionId. O CO2e e o motivo da
    rejeição saem pela `GetTransaction` — o processamento é assíncrono.
"""

import base64
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()


# ════════════════════════════════════════
# CONFIGURAÇÃO
# ════════════════════════════════════════

# As URLs vieram do Rafael (WhatsApp, 31/08/2026). NÃO são as da documentação:
# a plataforma roda em OutSystems Cloud, e os nomes `verda-application*.verda.global`
# das pgs. 5-6 não existem em DNS (o nameserver autoritativo devolve NXDOMAIN).
# O CAMINHO a partir do host é o documentado, e foi confirmado batendo no Token.
#
# Produção ainda não foi informada — o Rafael disse só que as CREDENCIAIS mudam.
# Fica None de propósito: melhor falhar com mensagem clara do que chutar um host
# e mandar viagem para o lugar errado. Dá para injetar por env sem editar código.
AMBIENTES = {
    'teste': os.getenv('VERDA_URL_TESTE',
                       'https://personal-d33vvevh.outsystemscloud.com'),
    'producao': os.getenv('VERDA_URL_PRODUCAO') or None,
}
CAMINHO_BASE = '/VerdaIntegration/rest'

# Domínio de cada API — a página do domínio (pg. 21) e o índice (pg. 3) põem as
# seis de transporte em RoadOrchestration; as linhas soltas com "/Orchestration"
# no rodapé de Passenger/UnloadedTrip/Weight são erro de digitação.
DOMINIO = {
    'Token': 'Credential',
    'Fuel': 'RoadOrchestration',
    'Weight': 'RoadOrchestration',
    'UnloadedTrip': 'RoadOrchestration',
    'GetTransaction': 'Transactions',
    'CancelTransaction': 'Transactions',
}
APIS_EM_USO = ('Fuel', 'Weight', 'GetTransaction', 'CancelTransaction')

TIMEOUT = int(os.getenv('VERDA_TIMEOUT', '60'))
TENTATIVAS = int(os.getenv('VERDA_TENTATIVAS', '3'))
MARGEM_TOKEN = timedelta(minutes=2)   # renova antes de expirar de fato

# Status que a Verda devolve (pg. 76)
STATUS_FINAL_OK = {'executed'}
STATUS_FINAL_RUIM = {'rejected', 'canceled', 'internal_error'}
STATUS_EM_CURSO = {'created', 'primary', 'processing'}


class VerdaErro(Exception):
    """Falha de comunicação ou de autenticação. Rejeição de negócio NÃO passa
    por aqui — vem como Success=0 no corpo, e é o chamador que decide."""


# ════════════════════════════════════════
# CLIENTE
# ════════════════════════════════════════

class Verda:
    def __init__(self, ambiente=None, application_key=None, secret_key=None, simulado=None):
        self.ambiente = ambiente or os.getenv('VERDA_AMBIENTE', 'teste')
        if self.ambiente not in AMBIENTES:
            raise VerdaErro('ambiente inválido: %r (use teste ou producao)' % self.ambiente)
        if not AMBIENTES[self.ambiente]:
            raise VerdaErro('a URL de %s ainda não foi informada pela Verda. Peça ao Rafael '
                            'e ponha em VERDA_URL_PRODUCAO no .env.' % self.ambiente)
        self.base = AMBIENTES[self.ambiente] + CAMINHO_BASE
        self.application_key = application_key or os.getenv('VERDA_APPLICATION_KEY', '')
        self.secret_key = secret_key or os.getenv('VERDA_SECRET_KEY', '')
        self.simulado = (os.getenv('VERDA_SIMULADO', '1') == '1') if simulado is None else simulado
        self._token = None
        self._expira = None

    @property
    def rotulo(self):
        """Nome do ambiente para efeito de ESTADO.

        O simulador é um ambiente à parte, e precisa ser: as transações `SIM-...`
        que ele inventa não existem na Verda. Sem separar, a viagem exercitada
        aqui contaria como já enviada e nunca sairia de verdade.
        """
        return 'simulado' if self.simulado else self.ambiente

    # ── autenticação ──

    def _autorizacao(self):
        bruto = '%s:%s' % (self.application_key, self.secret_key)
        return base64.b64encode(bruto.encode()).decode()

    def token(self):
        """Token válido, renovando quando falta pouco para expirar."""
        agora = datetime.now(timezone.utc)
        if self._token and self._expira and agora < self._expira - MARGEM_TOKEN:
            return self._token

        if self.simulado:
            self._token = str(uuid.uuid4())
            self._expira = agora + timedelta(minutes=30)
            return self._token

        if not self.application_key or not self.secret_key:
            raise VerdaErro('VERDA_APPLICATION_KEY/VERDA_SECRET_KEY não configuradas. '
                            'A Verda gera as duas em Menu → Conta → Aplicativo API.')

        resp = self._bruto('POST', self._url('Token'), headers={
            'Authorization': self._autorizacao(),
            'Scope': ' '.join(APIS_EM_USO),
        })
        corpo = _corpo(resp)
        ret = corpo.get('Return') or corpo
        self._token = ret.get('AccessToken')
        if not self._token:
            raise VerdaErro('Token não veio na resposta: %s' % json.dumps(corpo)[:300])
        self._expira = _data_utc(ret.get('ExpiresOn')) or (agora + timedelta(minutes=10))
        return self._token

    # ── chamadas ──

    def _url(self, api):
        return '%s/%s/%s' % (self.base, DOMINIO[api], api)

    def _bruto(self, metodo, url, headers=None, corpo=None):
        """HTTP com retry em falha de rede e 5xx TRANSITÓRIO.

        Atenção ao 500: a plataforma roda em OutSystems e devolve **500 para erro
        de negócio**, não só para falha de servidor — credencial errada volta como
        `{"Errors": ["Incorrect 'ClientId' or 'ClientSecret'."], "StatusCode": 500}`.
        Repetir isso é inútil (o erro é nosso e determinístico) e perigoso num POST
        de viagem, porque uma tentativa que falhou DEPOIS de registrar a transação
        duplicaria a emissão. Por isso: 500 com corpo `Errors` não repete.
        """
        ultimo = None
        for tentativa in range(1, TENTATIVAS + 1):
            try:
                resp = requests.request(metodo, url, headers=headers, json=corpo, timeout=TIMEOUT)
            except requests.RequestException as e:
                ultimo = 'rede: %s' % e
            else:
                if resp.status_code < 500:
                    if resp.status_code >= 400:
                        raise VerdaErro('HTTP %s em %s: %s'
                                        % (resp.status_code, url, _erros(resp)))
                    return resp
                erros = _erros(resp)
                if _tem_errors(resp):
                    raise VerdaErro('HTTP %s em %s (erro de negócio, não se repete): %s'
                                    % (resp.status_code, url, erros))
                ultimo = 'HTTP %s: %s' % (resp.status_code, erros)
            if tentativa < TENTATIVAS:
                time.sleep(2 ** tentativa)
        raise VerdaErro('falhou após %d tentativas em %s — %s' % (TENTATIVAS, url, ultimo))

    def _chamar(self, api, payload):
        if self.simulado:
            return _simular(api, payload)
        resp = self._bruto('POST', self._url(api), corpo=payload, headers={
            'Token': self.token(), 'Scope': api, 'Content-Type': 'application/json',
        })
        return _corpo(resp)

    # ── as APIs ──

    def enviar(self, api, payload):
        """Envia uma viagem pela Fuel ou pela Weight.

        Retorna (aceito, transaction_id, mensagem). `aceito` é só o aceite do
        POST — a viagem ainda pode ser rejeitada no processamento, o que só
        aparece na `conferir`.
        """
        if api not in ('Fuel', 'Weight'):
            raise VerdaErro('api inválida para envio: %r' % api)
        corpo = self._chamar(api, payload)
        ret = corpo.get('Return') or corpo
        return (_sucesso(ret.get('Success')),
                ret.get('TransactionId') or ret.get('TransactionKey') or None,
                ret.get('Message') or '')

    def conferir(self, transaction_id=None, inicio=None, fim=None, status=None):
        """`GetTransaction`. Com `transaction_id`, os outros filtros são ignorados;
        sem ele, `inicio` e `fim` são obrigatórios (pg. 71).

        Devolve lista de dicts com id, status, mensagem e erros [(campo, descrição)].
        """
        if not transaction_id and not (inicio and fim):
            raise VerdaErro('informe transaction_id, ou inicio e fim')
        pedido = {'TransactionKey': transaction_id or '', 'StatusKey': status or '',
                  'StartDate': '' if transaction_id else inicio,
                  'EndDate': '' if transaction_id else fim}
        corpo = self._chamar('GetTransaction', pedido)
        ret = corpo.get('Return') or corpo
        saida = []
        for t in ret.get('TransactionList') or []:
            erros = t.get('ErrorDetail') or []
            if isinstance(erros, dict):
                erros = [erros]
            saida.append({
                # a resposta real devolve `TransactionKey`; a pg. 73 diz
                # `TransactionId`. Sem este fallback o id vem None, o UPDATE do
                # estado não acha a linha e a viagem fica presa em `enviado`
                # para sempre, sendo conferida a cada rodada.
                'transaction_id': t.get('TransactionKey') or t.get('TransactionId'),
                'status': (t.get('StatusKey') or '').strip().lower(),
                'utc': t.get('UTCDate'),
                # QUINTA divergência doc × API, e a mais cara até agora: a razão da
                # rejeição vem em `ErrorMessage`, no próprio objeto da transação —
                # não em `Message` (pg. 73) nem na lista `ErrorDetail` (pgs. 73-74),
                # que simplesmente não existem na resposta real. Sem este campo toda
                # rejeição chega muda, e foi o que aconteceu em 11/09/2026: as 21
                # recusadas do primeiro lote de produção vieram sem motivo aparente e
                # a causa ("Fiscal month isn't open" e "Invalid 'VehicleTypeKey'")
                # levou horas de caça a padrão em peso, ritmo e horário — enquanto a
                # plataforma dizia o motivo em texto claro.
                'mensagem': t.get('ErrorMessage') or t.get('Message') or '',
                'erros': [(e.get('FieldName'), e.get('ErrorDescription'))
                          for e in erros if isinstance(e, dict)],
            })
        return saida

    def cancelar(self, transaction_id, transportation_id):
        """`CancelTransaction` — exige os DOIS ids (pg. 69).

        O nome do primeiro campo é **`TransactionKey`**, não `TransactionId` como
        está na doc — mesmo erro da `GetTransaction`. Mandando `TransactionId` a
        API responde `Success: false` com "Combination 'TransactionKey' and
        'TransportationId' invalid.", ou seja: o cancelamento NUNCA funcionaria.

        Isso importa mais do que parece. O cancelamento é o que impede a dupla
        contagem quando um CTe é corrigido depois de enviado — sem ele, a viagem
        alterada é reenviada e a emissão conta duas vezes no inventário.
        """
        corpo = self._chamar('CancelTransaction', {
            'TransactionKey': transaction_id, 'TransportationId': transportation_id})
        ret = corpo.get('Return') or corpo
        return _sucesso(ret.get('Success')), ret.get('Message') or ''


# ════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════

def _sucesso(valor):
    """`Success` da resposta.

    A documentação mostra `"Success": "1"` (texto), mas a API real devolve
    **boolean JSON** — `"Success": true`. Aceita as duas formas para não
    depender de qual delas a Verda decide manter.
    """
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().lower() in ('1', 'true')


def _tem_errors(resp):
    """A resposta traz a lista `Errors` do OutSystems? (erro determinístico nosso)"""
    try:
        return bool((resp.json() or {}).get('Errors'))
    except ValueError:
        return False


def _erros(resp):
    """Mensagem legível: prefere a lista `Errors`, cai para o texto cru."""
    try:
        corpo = resp.json() or {}
    except ValueError:
        return resp.text[:300]
    lista = corpo.get('Errors')
    if lista:
        return '; '.join(str(x) for x in lista)[:300]
    return (corpo.get('Message') or resp.text)[:300]


def _corpo(resp):
    try:
        return resp.json()
    except ValueError:
        raise VerdaErro('resposta não é JSON: %s' % resp.text[:300])


def _data_utc(texto):
    """`CreatedOn`/`ExpiresOn` vêm em UTC (pg. 7) no formato AAAA-MM-DD HH:MM:SS.

    A 3S nos ensinou o custo de errar isto: lá o `expiration` vinha em horário de
    Brasília e era comparado com utcnow, o que fazia relogar a cada chamada.
    """
    if not texto:
        return None
    s = str(texto).strip().replace('T', ' ').replace('Z', '')[:19]
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# ════════════════════════════════════════
# SIMULADOR
# ════════════════════════════════════════
# Responde no formato REAL da API (conferido contra homologação em 01/09/2026),
# não no da documentação: resposta plana sem envelope `Return`, `Success` como
# boolean JSON e `TransactionKey` na GetTransaction. A doc diverge nos três
# pontos, e um simulador fiel à doc esconderia justamente os bugs que eles causam.
# Continua reprovando o que a doc reprovaria, para exercitar o caminho do erro.

_SIMULADAS = {}

_OBRIGATORIOS = ['LocalDateTime', 'TransportationId', 'TransportationDate', 'CountryVehicleKey',
                 'VehicleTypeKey', 'VehicleKey', 'VehicleModelYear', 'VehicleUtilization',
                 'DistanceUnitKey', 'ItemsList', 'IsOwnOperation']
_OBRIGATORIOS_ITEM = ['WaypointOrder', 'WaypointDistance', 'DeliveryId',
                      'ShipperCountryKey', 'ShipperKey', 'ItemId', 'IsFinalDestination']


def _simular(api, payload):
    if api == 'GetTransaction':
        alvo = payload.get('TransactionKey')
        lista = ([_SIMULADAS[alvo]] if alvo in _SIMULADAS
                 else list(_SIMULADAS.values()) if not alvo else [])
        return {'Success': True, 'Message': 'simulado', 'TransactionList': lista}

    if api == 'CancelTransaction':
        t = _SIMULADAS.get(payload.get('TransactionKey') or payload.get('TransactionId'))
        if t:
            t['StatusKey'] = 'canceled'
        return {'Success': bool(t),
                'Message': 'cancelada' if t else 'transacao nao encontrada'}

    faltando = [c for c in _OBRIGATORIOS if payload.get(c) in (None, '', [])]
    if api == 'Fuel':
        faltando += [c for c in ('FuelTypeKey', 'FuelConsumption', 'VolumeUnitKey',
                                 'ItemIndexUnitKey') if payload.get(c) in (None, '')]
    else:
        faltando += [c for c in ('WeightUnitKey',) if payload.get(c) in (None, '')]
    erros = [{'FieldName': c, 'ErrorDescription': 'campo obrigatório ausente'} for c in faltando]
    for i, item in enumerate(payload.get('ItemsList') or []):
        campo_peso = 'ItemWeightIndex' if api == 'Fuel' else 'ItemWeight'
        for c in _OBRIGATORIOS_ITEM + [campo_peso]:
            if item.get(c) in (None, ''):
                erros.append({'FieldName': 'ItemsList[%d].%s' % (i, c),
                              'ErrorDescription': 'campo obrigatório ausente'})

    tid = 'SIM-' + uuid.uuid4().hex[:20].upper()
    _SIMULADAS[tid] = {
        'TransactionKey': tid,
        'UTCDate': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
        'StatusKey': 'rejected' if erros else 'executed',
        'Message': 'rejeitada na simulação' if erros else 'processada',
        # A API real devolve a razão em `ErrorMessage`, uma string só — nunca se
        # viu a `ErrorDetail` da doc preenchida, nem em homologação nem nas 21
        # rejeições de produção de 11/09/2026. O simulador emite o que a API
        # emite: espelhar a doc aqui foi o que escondeu quatro bugs em 01/09.
        'ErrorMessage': '; '.join('%s: %s' % (e['FieldName'], e['ErrorDescription'])
                                  for e in erros),
    }
    return {'Success': not erros, 'Message': _SIMULADAS[tid]['Message'],
            'TransactionId': tid}
