# -*- coding: utf-8 -*-
"""Conferência CIOT × CTRB × manifesto — pedido do diretor em 16/09/2026.

"Preciso fazer um check se todos os CTRBs/MDF estão com CIOT vinculados. A partir
de agora, de hora em hora."

Três cruzamentos, e o que faltar vira pendência:

  1. CTRB → CIOT        o CTRB tem um CIOT VÁLIDO (não basta o campo estar cheio)
  2. CTRB → manifesto   o CTRB está num manifesto, e no manifesto CERTO
  3. manifesto → CTRB   todo manifesto tem um CTRB (e, por ele, um CIOT)

Fontes (dataset principal do Power BI, só leitura):
  - `public ctrbs_oss` (073): `ciot`, `manifesto`, `observacao`, placas, CPF
  - `public manifestos` (916): `CHAVE_CTRB` — a coluna calculada do modelo que CASA
    OS ÓRFÃOS: quando o SSW não informa o CTRB (`numero_ctrb_os = 000000`) ela
    preenche mesmo assim. É o mesmo vínculo que a Auditoria Receita usa para dizer
    se um CTRB é órfão, então as duas telas concordam sobre quem tem manifesto.

O que a régua aprendeu na medição de 01–16/09/2026 (293 CTRBs, 264 manifestos):

  - **Campo cheio não é CIOT.** Quando a geração falha, o SSW grava a mensagem da
    ANTT/Pamcard no lugar do código ("Mensagem recebida da Pamcard:<br />4 - ...").
    CIOT válido = 12 dígitos, às vezes com o código de verificação (`/4846`, `.7599`).
  - **CTRB reemitido** não é pendência: quem responde é o novo. O SSW marca com
    "BAIXADO PELA EMISSAO DE NOVO CTRB:X", mas a mesma frase aparece quando o CTRB é só
    ENCERRADO pela viagem seguinte do veículo (24 de 29 em set/26) — aí o antigo é
    viagem e responde por si. Reemissão = X com mesma placa e rota, até 24 h depois.
  - **Diária não é viagem** e não entra (decisão do Gabriel, 17/09): `tabela_antt = 0`,
    sem manifesto e origem = destino. Os 8 casos do período batem os três.
  - **O vínculo precisa ser lido nos dois sentidos.** O 916 extraído antes do CTRB
    existir fica com `000000`; o 073 guarda o manifesto no próprio CTRB (e pode
    listar dois: "UDI-FEC  029132-3, UDI-FEC  029133-1").
  - **A fórmula pode casar errado.** UDI029221-4 foi casado com UDI027333, que já é
    do manifesto UDI029210-9 — na prática o manifesto é órfão e sai como "manifesto sem
    CTRB", com a sugestão da regra de órfãos do `_match_orfao_junho.py` (mesma placa,
    ±5 dias, CTRB sem manifesto). "Dados diferentes" fica só para vínculo que existe e
    diverge: 073 × 916, placa de cavalo/carreta ou motorista.

Frequência: a conferência (e o envio) seguem o refresh do BI — hoje 8×/dia no Pro
(02:00 05:30 08:00 10:00 12:00 14:00 16:00 20:00 BRT). Roda só depois de um refresh
CONCLUÍDO, passada a folga de CIOT_ESPERA_POS_REFRESH_MIN, e nunca durante um refresh.
Mais frequente que o refresh não traz dado novo; se o refresh virar horário (PPU), a
conferência acompanha sozinha, sem mudar nada aqui.

    python -X utf8 ciot_conferencia.py --dry-run               # só mostra, não grava
    python -X utf8 ciot_conferencia.py --dry-run --desde 2026-09-01
    python -X utf8 ciot_conferencia.py                         # grava a rodada (não envia)
    python -X utf8 ciot_conferencia.py --previa                # grava e MOSTRA a mensagem, sem enviar
    python -X utf8 ciot_conferencia.py --enviar                # grava e envia (exige CIOT_ENVIO=true)
    python -X utf8 ciot_conferencia.py --enviar --resumo       # força o resumo do dia (teste)

Variáveis:
    CIOT_CONFERENCIA=true      liga o laço no servidor (sem isto, só a linha de comando)
    CIOT_DESDE=2026-09-01      documentos emitidos a partir desta data (retroativo, decisão de 17/09)
    CIOT_INTERVALO_MIN=0       0 = roda só depois de cada refresh do BI; >0 força rodada extra
    CIOT_ESPERA_POS_REFRESH_MIN=10   folga depois do fim do refresh do BI; durante um refresh não roda
    CIOT_CARENCIA_H=2          horas que uma pendência espera antes de ir no aviso
    CIOT_ENVIO=false           liga o WhatsApp (UazAPI)
    CIOT_UAZAPI_TO             destinatário(s), separados por vírgula
    CIOT_ENVIO_HORARIO=06:00-22:00   fora disso a pendência espera a próxima rodada
    CIOT_RESUMO_HORA_BRT=08:00       resumo diário de tudo que segue aberto (1×/dia, na
                                     primeira rodada depois deste horário)
    CIOT_RESUMO_DIAS=7               o resumo lista os emitidos nesses dias; o resto só na tela
    CIOT_FORMATO=imagem              imagem + legenda curta (padrão) ou texto
    CIOT_INTERVALO_ENVIO_SEG=75
    CIOT_BASE_URL (ou PGR_BASE_URL)  base do link da tela. O link da mensagem leva um token
                                     de leitura (ciot_tokens) que não expira e só abre a aba
                                     CIOT, sem menu e sem ação; "gerar novo" na tela revoga o antigo
"""

import os
import re
import sys
import json
import time
import logging
from collections import defaultdict
from datetime import datetime, date, timedelta

import requests

import placas

_logger = logging.getLogger(__name__)

DESDE = os.getenv('CIOT_DESDE', '2026-09-01')
# 0 = só depois de cada refresh do BI (hoje, 8×/dia no Pro). Com refresh horário (PPU)
# não precisa mexer: a conferência já acompanha o refresh. >0 força uma rodada extra.
INTERVALO_MIN = int(os.getenv('CIOT_INTERVALO_MIN', '0'))
CARENCIA_H = float(os.getenv('CIOT_CARENCIA_H', '2'))
# Documento de antes do `desde` ainda é lido: o manifesto de ontem pode ser do
# CTRB de hoje, e a substituição aponta para trás.
MARGEM_DIAS = 15
# Regra de órfãos do _match_orfao_junho.py
JANELA_ORFAO_DIAS = 5
# Reemissão (mesma placa e rota) até este tanto depois do CTRB original
REEMISSAO_H = 24
POLL_MIN = 5
# Folga depois de o refresh do BI terminar, antes de ler (e nunca durante um refresh)
ESPERA_POS_REFRESH_MIN = int(os.getenv('CIOT_ESPERA_POS_REFRESH_MIN', '10'))
# Se o refresh do horário do resumo falhar, o resumo sai assim mesmo depois deste tanto
RESUMO_TOLERANCIA_H = 2

UAZAPI_URL = (os.getenv('UAZAPI_URL', '') or '').rstrip('/')
UAZAPI_TOKEN = os.getenv('UAZAPI_TOKEN', '') or ''
DESTINATARIOS = os.getenv('CIOT_UAZAPI_TO', '') or ''
ENVIO_ATIVO = os.getenv('CIOT_ENVIO', 'false').strip().lower() == 'true'
ENVIO_HORARIO = os.getenv('CIOT_ENVIO_HORARIO', '06:00-22:00')
INTERVALO_ENVIO_SEG = float(os.getenv('CIOT_INTERVALO_ENVIO_SEG', '75'))
BASE_URL = (os.getenv('CIOT_BASE_URL') or os.getenv('PGR_BASE_URL')
            or os.getenv('PGR_URL') or '').rstrip('/')
MAX_LINHAS_AVISO = 15
MAX_LINHAS_RESUMO = 30
# imagem (padrão, como o PGR) ou texto; a imagem cai para texto se não renderizar
FORMATO = os.getenv('CIOT_FORMATO', 'imagem').strip().lower()
RESUMO_HORA = os.getenv('CIOT_RESUMO_HORA_BRT', '08:00')
# O WhatsApp lista só os emitidos nos últimos N dias; o resto fica na tela
RESUMO_DIAS = int(os.getenv('CIOT_RESUMO_DIAS', '7'))

TIPOS = {
    'sem_ciot':            'CTRB sem CIOT',
    'ciot_erro':           'CIOT com erro na geração',
    'ctrb_sem_manifesto':  'CTRB sem manifesto',
    'manifesto_sem_ctrb':  'Manifesto sem CTRB (sem CIOT)',
    'vinculo_divergente':  'CTRB e manifesto com dados diferentes',
}
ORDEM_TIPOS = list(TIPOS)

C = "'public ctrbs_oss'"
M = "'public manifestos'"


def ligado():
    return os.getenv('CIOT_CONFERENCIA', '').strip().lower() in ('1', 'true', 'sim')


# ── Régua (funções puras) ────────────────────────────────────────────────────

_RE_CIOT = re.compile(r'\d{12}([./]\d{1,6})?')
_RE_MF_073 = re.compile(r'([A-Z]{3})-[A-Z]{3}\s+(\d{6}-\d)')
_RE_SUBST = re.compile(r'NOVO CTRB:\s*([A-Z]{3}\d{6}-\d)')
_RE_ZEROS = re.compile(r'0*')


def ciot_valido(s):
    return bool(s and _RE_CIOT.fullmatch(str(s).strip()))


def ciot_erro(s):
    """Mensagem de falha gravada no lugar do CIOT, ou ''."""
    s = str(s or '')
    if 'Mensagem recebida' not in s:
        return ''
    return re.sub(r'\s+', ' ', re.sub(r'<br\s*/?>', ' ', s)).strip()[:220]


def manifestos_do_073(s):
    """'UDI-FEC  029132-3, NOD-UDI  004852-6' → ['UDI029132-3', 'NOD004852-6']."""
    return [a + b for a, b in _RE_MF_073.findall(str(s or ''))]


def substituto(obs):
    m = _RE_SUBST.search(str(obs or ''))
    return m.group(1) if m else ''


def _vazio_ctrb(nc):
    return _RE_ZEROS.fullmatch(str(nc or '')) is not None


def _placa(p):
    return placas.mercosul(p) if p else ''


def _cpf(s):
    d = re.sub(r'\D', '', str(s or ''))
    return d[-11:].zfill(11) if d else ''


def _dia(v):
    try:
        return date.fromisoformat(str(v)[:10])
    except Exception:
        return None


def _cidade(s):
    return re.sub(r'\s+', ' ', str(s or '')).strip().upper()


def _tipo_op(r):
    return (r.get('Tipo Operação') or r.get('propriedade') or '').strip().title()


def conferir(ctrbs, manifestos, desde):
    """Aplica os três cruzamentos. Devolve (pendencias, resumo).

    `ctrbs`/`manifestos` são as linhas do Power BI (ver `coletar`), já com a
    margem para trás; só documento emitido a partir de `desde` gera pendência.
    Função pura — é o que o `--dry-run` e a rodada gravada compartilham.
    """
    desde_s = desde.isoformat()
    por_ctrb = {r['ctrb']: r for r in ctrbs if r.get('ctrb')}
    por_chave = {k[:9]: r for k, r in por_ctrb.items()}   # CHAVE_CTRB não tem o dígito
    por_mf = {m['mf']: m for m in manifestos if m.get('mf')}

    def _viagem(r):
        return (_placa(r.get('placa_cavalo')), _cidade(r.get('cidade_uf_origem')),
                _cidade(r.get('cidade_uf_destino')))

    def _horas(a, b):
        try:
            return (datetime.fromisoformat(str(b['emissao'])[:19])
                    - datetime.fromisoformat(str(a['emissao'])[:19])).total_seconds() / 3600
        except Exception:
            return None

    mesma_viagem = defaultdict(list)
    for r in ctrbs:
        mesma_viagem[_viagem(r)].append(r)

    def reemitido_em(r):
        """CTRB que REEMITIU este, ou ''.

        Reemissão = outro CTRB com a MESMA placa e a MESMA rota, emitido até
        REEMISSAO_H depois. Dois jeitos de aparecer:

        - com aviso: "BAIXADO PELA EMISSAO DE NOVO CTRB:X". Mas a mesma frase sai quando o
          CTRB é só ENCERRADO pela viagem seguinte do veículo (24 de 29 em set/26) — por
          isso o X precisa ser a mesma viagem.
        - sem aviso: o CTRB ficou sem CIOT (ou com erro) e um irmão da mesma viagem saiu
          depois COM CIOT. UDI027256-6 (erro Pamcard) → UDI027257-4 em 11 min;
          NOD004828-3 → NOD004832-1 em 11 h, com o manifesto reemitido junto.
        """
        s = substituto(r.get('observacao'))
        n = por_ctrb.get(s)
        if n and _viagem(n) == _viagem(r):
            h = _horas(r, n)
            if h is not None and 0 <= h <= REEMISSAO_H:
                return s
        if ciot_valido(r.get('ciot')):
            return ''
        irmaos = []
        for x in mesma_viagem.get(_viagem(r), ()):
            h = _horas(r, x)
            if x is not r and h is not None and 0 < h <= REEMISSAO_H and ciot_valido(x.get('ciot')):
                irmaos.append((h, x['ctrb']))
        return min(irmaos)[1] if irmaos else ''

    def final(ctrb, _n=0):
        """Segue a cadeia de reemissão até o CTRB que vale."""
        r = por_ctrb.get(ctrb)
        s = reemitido_em(r) if r else ''
        if s and _n < 6:
            return final(s, _n + 1)
        return ctrb

    # Vínculo manifesto → CTRB, pelos dois sentidos
    mf_para_ctrb = defaultdict(set)          # manifesto → {ctrb}
    ctrb_para_mf = defaultdict(dict)         # ctrb → {manifesto: origem}
    for m in manifestos:
        kc = m.get('kc')
        r = por_chave.get(kc) if kc else None
        if r:
            origem = 'fórmula' if _vazio_ctrb(m.get('nc')) else 'SSW'
            mf_para_ctrb[m['mf']].add(r['ctrb'])
            ctrb_para_mf[r['ctrb']][m['mf']] = origem
    for r in ctrbs:
        for mf in manifestos_do_073(r.get('manifesto')):
            mf_para_ctrb[mf].add(r['ctrb'])
            ctrb_para_mf[r['ctrb']].setdefault(mf, '073')
            if ctrb_para_mf[r['ctrb']][mf] == 'fórmula':
                ctrb_para_mf[r['ctrb']][mf] = '073'   # o 073 confirma a fórmula

    # O substituto herda os manifestos do substituído: o 916 continua apontando
    # para o CTRB antigo, e sem isso o novo apareceria como sem manifesto.
    for c in list(ctrb_para_mf):
        f = final(c)
        if f != c:
            for mf_ in ctrb_para_mf[c]:
                # 'herdado' fica fora do 073 × 916: é do CTRB antigo, não deste
                ctrb_para_mf[f].setdefault(mf_, 'herdado')

    # CTRB "com manifesto declarado" = pelo SSW (916 ou 073), não só pela fórmula
    declarado = {c: {mf for mf, o in d.items() if o != 'fórmula'} for c, d in ctrb_para_mf.items()}

    # Índice por placa para a regra de órfãos
    ctrb_por_placa = defaultdict(list)
    for r in ctrbs:
        for p in {_placa(r.get('placa_cavalo')), _placa(r.get('placa_carreta'))} - {''}:
            ctrb_por_placa[p].append(r)

    def sugerir_ctrb(m):
        """CTRB da mesma placa, ±5 dias, que ainda não tem manifesto."""
        d = _dia(m.get('d'))
        achados = {}
        for p in {_placa(m.get('cav')), _placa(m.get('car'))} - {''}:
            for r in ctrb_por_placa.get(p, []):
                dr = _dia(r.get('emissao'))
                if d and dr and abs((dr - d).days) <= JANELA_ORFAO_DIAS \
                        and not declarado.get(r['ctrb']) and not reemitido_em(r):
                    achados[r['ctrb']] = r
        return sorted(achados)

    pend = []
    resumo = defaultdict(int)

    def add(tipo, doc, r=None, m=None, detalhe='', ctrb='', mfs=(), sufixo='', pelo_manifesto=False):
        base = r or {}
        emissao = (m or {}).get('d') if pelo_manifesto else (base.get('emissao') or (m or {}).get('d'))
        pend.append({
            'chave': f'{tipo}:{doc}' + (f':{sufixo}' if sufixo else ''),
            'tipo': tipo,
            'documento': doc,
            'ctrb': ctrb or base.get('ctrb') or '',
            'manifesto': ', '.join(sorted(mfs)) if mfs else (m or {}).get('mf', ''),
            'filial': doc[:3],
            'emissao': emissao,
            'tipo_operacao': _tipo_op(base) if base else '',
            'placa': base.get('placa_cavalo') or (m or {}).get('cav') or '',
            'motorista': base.get('motorista') or (m or {}).get('motorista') or '',
            'detalhe': detalhe,
        })
        resumo[tipo] += 1

    # ── 1 e 2: pelo CTRB ──
    for r in ctrbs:
        if str(r.get('emissao') or '')[:10] < desde_s:
            continue
        resumo['ctrbs'] += 1
        c = r['ctrb']
        if reemitido_em(r):
            resumo['reemitidos'] += 1
            continue
        mfs = ctrb_para_mf.get(c, {})
        if not mfs and not r.get('tabela_antt') \
                and _cidade(r.get('cidade_uf_origem')) == _cidade(r.get('cidade_uf_destino')):
            resumo['nao_viagem'] += 1
            continue

        ciot = r.get('ciot')
        if ciot_valido(ciot):
            resumo['ciot_ok'] += 1
        elif ciot_erro(ciot):
            add('ciot_erro', c, r, detalhe=ciot_erro(ciot), mfs=mfs)
        else:
            add('sem_ciot', c, r, detalhe='campo CIOT vazio no CTRB', mfs=mfs)

        if not mfs:
            # Com ou sem CIOT muda tudo: sem os dois é a multa; com CIOT falta só o MDF.
            add('ctrb_sem_manifesto', c, r, mfs=(),
                detalhe=f"{'tem CIOT' if ciot_valido(ciot) else 'SEM CIOT'} · "
                        f"{r.get('cidade_uf_origem') or '?'} → {r.get('cidade_uf_destino') or '?'} · "
                        'nenhum manifesto aponta para este CTRB, nem ele cita manifesto')
            continue

        # Manifesto certo? 073 × 916 e os dados do conjunto. O alerta diz se o CIOT
        # está em dia: dado divergente com CIOT válido é outro problema que sem CIOT.
        sit_ciot = 'tem CIOT' if ciot_valido(ciot) else 'sem CIOT válido'
        m073 = {mf for mf, o in mfs.items() if o == '073'}
        m916 = {mf for mf, o in mfs.items() if o == 'SSW'}
        if m073 and m916 and not (m073 & m916):
            add('vinculo_divergente', c, r, mfs=m073 | m916, sufixo='073x916',
                detalhe=f"{sit_ciot} · o CTRB cita o manifesto {', '.join(sorted(m073))}, "
                        f"mas no 916 o manifesto dele é {', '.join(sorted(m916))}")
        for mf in sorted(mfs):
            m = por_mf.get(mf)
            if not m:
                continue
            if mfs[mf] == 'fórmula' and declarado.get(c):
                continue   # vínculo duvidoso da fórmula: o lado do manifesto já acusa

            dif = []
            if _placa(m.get('cav')) != _placa(r.get('placa_cavalo')):
                dif.append(f"cavalo: CTRB {r.get('placa_cavalo') or '—'} × manifesto {m.get('cav') or '—'}")
            if _placa(m.get('car')) != _placa(r.get('placa_carreta')):
                dif.append(f"carreta: CTRB {r.get('placa_carreta') or '—'} × manifesto {m.get('car') or '—'}")
            if _cpf(m.get('cpf')) and _cpf(r.get('cpf_motorista')) \
                    and _cpf(m.get('cpf')) != _cpf(r.get('cpf_motorista')):
                dif.append(f"motorista: CTRB {r.get('motorista') or r.get('cpf_motorista')} × "
                           f"manifesto {m.get('motorista') or m.get('cpf')}")
            if dif:
                add('vinculo_divergente', c, r, m, mfs={mf}, sufixo=mf,
                    detalhe=f"{sit_ciot} · manifesto {mf} · " + '; '.join(dif))

    # ── 3: pelo manifesto ──
    for m in manifestos:
        if str(m.get('d') or '')[:10] < desde_s:
            continue
        resumo['manifestos'] += 1
        mf = m['mf']
        diretos = set(mf_para_ctrb.get(mf, ()))
        ligados = {final(c) for c in diretos}
        if not ligados:
            sug = sugerir_ctrb(m)
            detalhe = 'sem CTRB, logo sem CIOT'
            if not _vazio_ctrb(m.get('nc')) and m.get('kc'):
                detalhe = f"o manifesto cita o CTRB {m['kc']}, que não está no 073"
            if sug:
                detalhe += f"; provável: {', '.join(sug)} (mesma placa, ±{JANELA_ORFAO_DIAS} dias)"
            add('manifesto_sem_ctrb', mf, None, m, detalhe=detalhe)
            continue
        resumo['manifestos_com_ctrb'] += 1
        # Fórmula que casou com CTRB que já é de outro manifesto declarado
        if _vazio_ctrb(m.get('nc')) and mf not in {x for c in diretos for x in declarado.get(c, ())}:
            outros = {x for c in diretos for x in declarado.get(c, ()) if x != mf}
            if outros:
                sug = sugerir_ctrb(m)
                c0 = sorted(diretos)[0]
                # Na prática é órfão: o único CTRB que a fórmula achou já tem dono.
                add('manifesto_sem_ctrb', mf, None, m,
                    detalhe=f"a fórmula do BI ligou ao CTRB {c0}, mas ele já é do manifesto "
                            f"{', '.join(sorted(outros))}"
                            + (f"; provável: {', '.join(sug)} (mesma placa, ±{JANELA_ORFAO_DIAS} dias)"
                               if sug else ''))

    return pend, dict(resumo)


# ── Power BI ─────────────────────────────────────────────────────────────────

def _dax_date(d):
    return f'DATE({d.year},{d.month},{d.day})'


def coletar(token, dax_rows, desde):
    ini = _dax_date(desde - timedelta(days=MARGEM_DIAS))
    cols = ['ctrb', 'emissao', 'Tipo Operação', 'propriedade', 'ciot', 'tabela_antt',
            'placa_cavalo', 'placa_carreta', 'cpf_motorista', 'motorista', 'manifesto',
            'observacao', 'cidade_uf_origem', 'cidade_uf_destino']
    sel = ','.join(f'"{c}",{C}[{c}]' for c in cols)
    ctrbs = dax_rows(token, f"EVALUATE CALCULATETABLE(SELECTCOLUMNS({C},{sel}), {C}[emissao] >= {ini})")
    manifestos = dax_rows(token, (
        f"EVALUATE CALCULATETABLE(SELECTCOLUMNS({M},"
        f"\"mf\",{M}[CHAVE_MANIFESTO],\"d\",{M}[data_emissao],\"kc\",{M}[CHAVE_CTRB],"
        f"\"nc\",{M}[numero_ctrb_os],\"cav\",{M}[placa_cavalo],\"car\",{M}[placa_carreta],"
        f"\"cpf\",{M}[cpf_motorista],\"motorista\",{M}[nome_motorista]), {M}[data_emissao] >= {ini})"))
    return ctrbs, manifestos


def estado_refresh(token, group_id, dataset_id):
    """(fim do último refresh CONCLUÍDO em UTC naive ou None, há refresh em andamento?).

    Em andamento a API devolve status "Unknown" sem endTime. Se não der para ler, devolve
    (None, False) e quem chama segue pelo intervalo mínimo."""
    try:
        r = requests.get(
            f'https://api.powerbi.com/v1.0/myorg/groups/{group_id}/datasets/{dataset_id}/refreshes?$top=5',
            headers={'Authorization': f'Bearer {token}'}, timeout=30)
        r.raise_for_status()
        valores = r.json().get('value', [])
        rodando = any(x.get('status') == 'Unknown' and not x.get('endTime') for x in valores)
        for x in valores:
            if x.get('status') == 'Completed' and x.get('endTime'):
                return datetime.fromisoformat(x['endTime'][:19]), rodando
        return None, rodando
    except Exception as e:
        _logger.warning(f'CIOT: não li o histórico de refresh: {e}')
    return None, False


def ultimo_refresh(token, group_id, dataset_id):
    """Fim do último refresh CONCLUÍDO (UTC, naive), ou None."""
    return estado_refresh(token, group_id, dataset_id)[0]


# ── Banco ────────────────────────────────────────────────────────────────────

DDL = """
CREATE TABLE IF NOT EXISTS ciot_pendencias (
    chave           VARCHAR(90) PRIMARY KEY,
    tipo            VARCHAR(30) NOT NULL,
    documento       VARCHAR(20) NOT NULL,
    ctrb            VARCHAR(20),
    manifesto       VARCHAR(80),
    filial          VARCHAR(3),
    emissao         TIMESTAMP,
    tipo_operacao   VARCHAR(20),
    placa           VARCHAR(10),
    motorista       VARCHAR(120),
    detalhe         TEXT,
    primeiro_visto  TIMESTAMP NOT NULL,   -- UTC
    ultimo_visto    TIMESTAMP NOT NULL,   -- UTC
    resolvido_em    TIMESTAMP,            -- UTC; NULL = em aberto
    avisado_em      TIMESTAMP             -- UTC; NULL = ainda não foi no WhatsApp
);
CREATE INDEX IF NOT EXISTS ix_ciot_pend_abertas ON ciot_pendencias (resolvido_em, primeiro_visto);
CREATE TABLE IF NOT EXISTS ciot_rodadas (
    id            SERIAL PRIMARY KEY,
    executada_em  TIMESTAMP NOT NULL,     -- UTC
    refresh_bi    TIMESTAMP,              -- UTC, fim do refresh que a rodada leu
    abertas       INT,
    novas         INT,
    resolvidas    INT,
    avisadas      INT,
    resumo        JSONB,
    erro          TEXT
);
CREATE TABLE IF NOT EXISTS ciot_envios (
    id                SERIAL PRIMARY KEY,
    tipo              VARCHAR(10) NOT NULL,   -- novas | resumo
    dia_brt           DATE NOT NULL,
    enviado_em        TIMESTAMP NOT NULL,     -- UTC
    documentos        INT,
    destinatarios_ok  INT
);
-- Link de leitura sem login (o da mensagem). Não expira: um só ativo por vez, e
-- "gerar novo" revoga o anterior — é a saída se o link vazar.
CREATE TABLE IF NOT EXISTS ciot_tokens (
    token          VARCHAR(64) PRIMARY KEY,
    criado_em      TIMESTAMP NOT NULL,     -- Brasília
    criado_por     VARCHAR(120),
    revogado_em    TIMESTAMP,              -- Brasília
    acessos        INT NOT NULL DEFAULT 0,
    ultimo_acesso  TIMESTAMP
);
"""


def garantir_tabelas(cur):
    cur.execute(DDL)


def _agora_brt():
    # ciot_tokens guarda em Brasília (é só para mostrar ao admin). Explícito, e não
    # NOW(), porque o fuso do Postgres local e o do container não são o mesmo.
    return datetime.utcnow().replace(microsecond=0) - timedelta(hours=3)


def token_ativo(cur, criado_por='sistema'):
    """O token do link da mensagem; cria na primeira vez. Sempre o mesmo até alguém rotacionar,
    para o link que o diretor guardou continuar abrindo."""
    cur.execute("SELECT token FROM ciot_tokens WHERE revogado_em IS NULL ORDER BY criado_em DESC LIMIT 1")
    r = cur.fetchone()
    return r[0] if r else novo_token(cur, criado_por)


def novo_token(cur, criado_por='sistema'):
    """Revoga o link atual e cria outro (link vazado, pessoa que saiu da empresa...)."""
    import secrets
    agora = _agora_brt()
    cur.execute("UPDATE ciot_tokens SET revogado_em = %s WHERE revogado_em IS NULL", (agora,))
    token = secrets.token_urlsafe(24)
    cur.execute("INSERT INTO ciot_tokens (token, criado_em, criado_por) VALUES (%s, %s, %s)",
                (token, agora, criado_por[:120]))
    return token


def validar_token(cur, token):
    """True se o token for o ativo; conta o acesso."""
    if not token or len(token) > 64:
        return False
    cur.execute("""UPDATE ciot_tokens SET acessos = acessos + 1, ultimo_acesso = %s
                   WHERE token = %s AND revogado_em IS NULL RETURNING 1""", (_agora_brt(), token))
    return cur.fetchone() is not None


def link_leitura(cur):
    """URL da aba para quem recebe a mensagem, sem login."""
    if not BASE_URL:
        return ''
    return f'{BASE_URL}/ciot?t={token_ativo(cur)}'


def info_link(cur):
    """Para o admin na tela: o link, quando foi criado e quantas vezes abriu."""
    token_ativo(cur)
    cur.execute("""SELECT token, criado_em, criado_por, acessos, ultimo_acesso FROM ciot_tokens
                   WHERE revogado_em IS NULL ORDER BY criado_em DESC LIMIT 1""")
    t, criado, por, acessos, ultimo = cur.fetchone()
    base = BASE_URL or ''
    return {'url': f'{base}/ciot?t={t}', 'relativo': f'/ciot?t={t}',
            'criado_em': criado.isoformat() if criado else None, 'criado_por': por,
            'acessos': acessos, 'ultimo_acesso': ultimo.isoformat() if ultimo else None}


def gravar(cur, pend, agora, desde=None):
    """Sincroniza as pendências da rodada com a tabela. Devolve (novas, resolvidas)."""
    if desde:
        # Fora do escopo não é "resolvida": se o CIOT_DESDE andar, o que ficou
        # para trás sai da tabela em vez de contar como consertado.
        cur.execute("DELETE FROM ciot_pendencias WHERE emissao < %s", (desde,))
    cur.execute("SELECT chave FROM ciot_pendencias WHERE resolvido_em IS NULL")
    abertas = {r[0] for r in cur.fetchall()}
    atuais = {p['chave'] for p in pend}
    novas = 0
    for p in pend:
        if p['chave'] not in abertas:
            novas += 1
        cur.execute("""
            INSERT INTO ciot_pendencias (chave, tipo, documento, ctrb, manifesto, filial, emissao,
                tipo_operacao, placa, motorista, detalhe, primeiro_visto, ultimo_visto)
            VALUES (%(chave)s, %(tipo)s, %(documento)s, %(ctrb)s, %(manifesto)s, %(filial)s,
                %(emissao)s, %(tipo_operacao)s, %(placa)s, %(motorista)s, %(detalhe)s, %(agora)s, %(agora)s)
            ON CONFLICT (chave) DO UPDATE SET
                ctrb = EXCLUDED.ctrb, manifesto = EXCLUDED.manifesto, emissao = EXCLUDED.emissao,
                tipo_operacao = EXCLUDED.tipo_operacao, placa = EXCLUDED.placa,
                motorista = EXCLUDED.motorista, detalhe = EXCLUDED.detalhe,
                ultimo_visto = EXCLUDED.ultimo_visto,
                -- voltou depois de resolvida: é pendência nova, e vai de novo no aviso
                primeiro_visto = CASE WHEN ciot_pendencias.resolvido_em IS NULL
                                      THEN ciot_pendencias.primeiro_visto ELSE EXCLUDED.primeiro_visto END,
                avisado_em = CASE WHEN ciot_pendencias.resolvido_em IS NULL
                                  THEN ciot_pendencias.avisado_em ELSE NULL END,
                resolvido_em = NULL
        """, dict(p, emissao=p['emissao'] or None, agora=agora))
    sumiram = sorted(abertas - atuais)
    if sumiram:
        cur.execute("UPDATE ciot_pendencias SET resolvido_em = %s WHERE chave = ANY(%s)",
                    (agora, sumiram))
    return novas, len(sumiram)


def listar(cur, status='abertas', limite=500):
    where = {'abertas': 'resolvido_em IS NULL',
             'resolvidas': 'resolvido_em IS NOT NULL'}.get(status, 'TRUE')
    cur.execute(f"""
        SELECT chave, tipo, documento, ctrb, manifesto, filial, emissao, tipo_operacao, placa,
               motorista, detalhe, primeiro_visto, ultimo_visto, resolvido_em, avisado_em
        FROM ciot_pendencias WHERE {where}
        ORDER BY resolvido_em DESC NULLS FIRST, emissao DESC NULLS LAST LIMIT %s""", (limite,))
    cols = [d[0] for d in cur.description]
    linhas = []
    for row in cur.fetchall():
        d = dict(zip(cols, row))
        for k in ('emissao', 'primeiro_visto', 'ultimo_visto', 'resolvido_em', 'avisado_em'):
            v = d[k]
            if v and k != 'emissao':
                v = v - timedelta(hours=3)          # gravado em UTC, exibido em BRT
            d[k] = v.isoformat() if v else None
        d['tipo_label'] = TIPOS.get(d['tipo'], d['tipo'])
        linhas.append(d)
    cur.execute("""SELECT executada_em, refresh_bi, abertas, novas, resolvidas, avisadas, resumo, erro
                   FROM ciot_rodadas ORDER BY id DESC LIMIT 1""")
    r = cur.fetchone()
    rodada = None
    if r:
        rodada = {'executada_em': (r[0] - timedelta(hours=3)).isoformat(),
                  'refresh_bi': (r[1] - timedelta(hours=3)).isoformat() if r[1] else None,
                  'abertas': r[2], 'novas': r[3], 'resolvidas': r[4], 'avisadas': r[5],
                  'resumo': r[6], 'erro': r[7]}
    # `ordem` vai à parte: o jsonify ordena as chaves de `tipos` alfabeticamente
    return {'linhas': linhas, 'rodada': rodada, 'tipos': TIPOS, 'ordem': ORDEM_TIPOS, 'desde': DESDE,
            'carencia_h': CARENCIA_H, 'envio_ativo': ENVIO_ATIVO and _configurado()}


# ── WhatsApp (mesma política do pgr_envio: sem retry, nunca propaga) ──────────

def _configurado():
    return bool(UAZAPI_URL and UAZAPI_TOKEN and DESTINATARIOS)


def _numeros():
    out = []
    for x in DESTINATARIOS.split(','):
        d = re.sub(r'\D', '', x)
        if d:
            out.append(d if d.startswith('55') else '55' + d)
    return out


def _enviar_texto(numero, texto):
    try:
        r = requests.post(f'{UAZAPI_URL}/send/text', json={'number': numero, 'text': texto},
                          headers={'token': UAZAPI_TOKEN, 'Content-Type': 'application/json'},
                          timeout=30)
        if r.status_code in (200, 201):
            return True
        _logger.warning(f'CIOT: UazAPI HTTP {r.status_code} — {r.text[:200]}')
    except Exception as e:
        _logger.warning(f'CIOT: UazAPI exceção: {e}')
    return False


def _enviar_imagem(numero, png, legenda):
    import base64
    try:
        r = requests.post(f'{UAZAPI_URL}/send/media',
                          json={'number': numero, 'type': 'image', 'text': legenda,
                                'file': base64.b64encode(png).decode('utf-8')},
                          headers={'token': UAZAPI_TOKEN, 'Content-Type': 'application/json'},
                          timeout=30)
        if r.status_code in (200, 201):
            return True
        _logger.warning(f'CIOT: UazAPI imagem HTTP {r.status_code} — {r.text[:200]}')
    except Exception as e:
        _logger.warning(f'CIOT: UazAPI exceção na imagem: {e}')
    return False


def _no_horario(agora_brt):
    try:
        a, b = ENVIO_HORARIO.split('-')
        ha, ma = [int(x) for x in a.split(':')]
        hb, mb = [int(x) for x in b.split(':')]
    except Exception:
        ha, ma, hb, mb = 6, 0, 22, 0
    m = agora_brt.hour * 60 + agora_brt.minute
    return ha * 60 + ma <= m <= hb * 60 + mb


_CURTO = {
    'sem_ciot': 'sem CIOT',
    'ciot_erro': 'CIOT com erro',
    'ctrb_sem_manifesto': 'sem manifesto',
    'manifesto_sem_ctrb': 'MDF sem CTRB',
    'vinculo_divergente': '⚠️ dados diferentes do manifesto',
}


def _problemas(ps):
    """'sem CIOT, sem manifesto' — e, quando o CTRB tem CIOT, diz isso: é o que separa a multa
    do documento que só falta amarrar."""
    tipos = {x['tipo'] for x in ps}
    txt = ', '.join(_CURTO[x['tipo']] for x in ps)
    if tipos & {'ctrb_sem_manifesto', 'vinculo_divergente'} and not tipos & {'sem_ciot', 'ciot_erro'}:
        txt += ' (tem CIOT)'
    return txt


def _linhas_detalhe(ps):
    """O que precisa ser explicado na própria mensagem: o erro do CIOT, QUAIS dados não
    batem e o CTRB provável do manifesto órfão. "Sem CIOT"/"sem manifesto" se explicam sozinhos."""
    return [f"   _{x['detalhe'][:220]}_" for x in ps
            if x['tipo'] in ('ciot_erro', 'vinculo_divergente', 'manifesto_sem_ctrb')
            and x.get('detalhe') and x['detalhe'] != 'sem CTRB, logo sem CIOT']


def montar_mensagem(novas, total_abertas, agora_brt, link=None):
    """Uma linha por DOCUMENTO — o mesmo CTRB costuma estar sem CIOT e sem manifesto."""
    cont = defaultdict(int)
    for p in novas:
        cont[p['tipo']] += 1
    por_doc = _por_documento(novas)

    linhas = [f'⚠️ *Conferência CIOT · {agora_brt:%d/%m %H:%M}*',
              f'{len(por_doc)} documento(s) com pendência nova · {total_abertas} pendência(s) em aberto',
              '']
    linhas += [f'{TIPOS[t]}: *{cont[t]}*' for t in ORDEM_TIPOS if cont[t]]
    linhas.append('')
    for i, (doc, ps) in enumerate(por_doc.items()):
        if i >= MAX_LINHAS_AVISO:
            linhas.append(f'+{len(por_doc) - i} documento(s) na tela')
            break
        p = ps[0]
        partes = [f'*{doc}*']
        if p.get('tipo_operacao'):
            partes.append(p['tipo_operacao'])
        if p.get('placa'):
            partes.append(p['placa'])
        mf = next((x['manifesto'] for x in ps if x.get('manifesto') and x['manifesto'] != doc), '')
        if mf:
            partes.append(f'MDF {mf}')
        linhas.append('• ' + ' · '.join(partes) + ' — ' + _problemas(ps))
        linhas += _linhas_detalhe(ps)
    if BASE_URL:
        linhas.append(f'\n🔗 {link or BASE_URL + "/ciot"}')
    return '\n'.join(linhas)


def _pendentes(cur, where, params=()):
    cur.execute(f"""SELECT chave, tipo, documento, manifesto, tipo_operacao, placa, detalhe, emissao,
                           avisado_em
                    FROM ciot_pendencias WHERE {where} ORDER BY emissao NULLS LAST, documento""", params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _por_documento(pend):
    """{documento: [pendências]}, mais antigo primeiro, com os tipos na ordem canônica."""
    por_doc = {}
    for p in sorted(pend, key=lambda p: ORDEM_TIPOS.index(p['tipo'])):
        por_doc.setdefault(p['documento'], []).append(p)
    return dict(sorted(por_doc.items(), key=lambda kv: (str(kv[1][0].get('emissao') or ''), kv[0])))


def montar_resumo(abertas, resolvidas_24h, agora_brt, link=None):
    """Resumo diário: tudo que segue em aberto, mais antigo primeiro, 🆕 no que nunca foi avisado.

    Dia sem pendência também sai — silêncio não diria se está tudo certo ou se o job caiu."""
    link = f'\n\n🔗 {link or BASE_URL + "/ciot"}' if (link or BASE_URL) else ''
    por_doc = _por_documento(abertas)
    if not por_doc:
        corpo = f'✅ *CIOT · {agora_brt:%d/%m}* — nada em aberto'
        if resolvidas_24h:
            corpo += f'\n{resolvidas_24h} pendência(s) resolvida(s) nas últimas 24 h'
        return corpo + link
    cont = defaultdict(int)
    for p in abertas:
        cont[p['tipo']] += 1
    corte = (agora_brt - timedelta(days=RESUMO_DIAS)).date()

    def recente(ps):
        em = ps[0].get('emissao')
        return not hasattr(em, 'date') or em.date() >= corte

    recentes = {d: ps for d, ps in por_doc.items() if recente(ps)}
    antigos = len(por_doc) - len(recentes)
    linhas = [f'📋 *CIOT · em aberto em {agora_brt:%d/%m %H:%M}*',
              f'{len(por_doc)} documento(s) · {resolvidas_24h} pendência(s) resolvida(s) nas últimas 24 h', '']
    linhas += [f'{TIPOS[t]}: *{cont[t]}*' for t in ORDEM_TIPOS if cont[t]]
    linhas.append('')
    linhas.append(f'_Emitidos nos últimos {RESUMO_DIAS} dias ({len(recentes)}):_' if recentes
                  else f'_Nada emitido nos últimos {RESUMO_DIAS} dias._')
    for i, (doc, ps) in enumerate(recentes.items()):
        if i >= MAX_LINHAS_RESUMO:
            linhas.append(f'+{len(recentes) - i} documento(s) na tela')
            break
        p = ps[0]
        em = p.get('emissao')
        quando = f' · emitido {em:%d/%m}' if hasattr(em, 'strftime') else ''
        novo = '🆕 ' if any(x.get('avisado_em') is None for x in ps) else ''
        partes = [f'{novo}*{doc}*'] + [v for v in (p.get('tipo_operacao'), p.get('placa')) if v]
        linhas.append('• ' + ' · '.join(partes) + ' — ' + _problemas(ps) + quando)
        linhas += _linhas_detalhe(ps)
    if antigos:
        linhas.append(f'\n⏳ +{antigos} documento(s) com mais de {RESUMO_DIAS} dias ainda em aberto — '
                      'lista completa na tela')
    return '\n'.join(linhas) + link


def dados_aviso(tipo, pend, total_abertas, resolvidas_24h, agora_brt, link=None):
    """Conteúdo da imagem (ciot_imagem) e da legenda, a partir das mesmas pendências do texto."""
    por_doc = _por_documento(pend)
    cont = defaultdict(int)
    for p in pend:
        cont[p['tipo']] += 1

    # CTRB sem manifesto: quantos também estão sem CIOT — é o número que importa
    sm = [ps for ps in por_doc.values() if any(x['tipo'] == 'ctrb_sem_manifesto' for x in ps)]
    sm_sem = sum(1 for ps in sm if {x['tipo'] for x in ps} & {'sem_ciot', 'ciot_erro'})
    rotulos = {'sem_ciot': ('sem CIOT', '#f87171'), 'ciot_erro': ('CIOT com erro', '#f87171'),
               'ctrb_sem_manifesto': ('sem manifesto', '#fb923c'),
               'manifesto_sem_ctrb': ('MDF sem CTRB', '#f87171'),
               'vinculo_divergente': ('dados diferentes', '#fb923c')}
    contadores = []
    for t in ORDEM_TIPOS:
        if cont[t]:
            rot, cor = rotulos[t]
            if t == 'ctrb_sem_manifesto':
                rot += f' ({sm_sem} também sem CIOT)'
            contadores.append((rot, cont[t], cor))

    def doc(d, ps):
        tipos = [x['tipo'] for x in ps]
        det = []
        for x in ps:
            dt = x.get('detalhe') or ''
            if x['tipo'] == 'ctrb_sem_manifesto':
                partes = dt.split(' · ')
                if len(partes) > 1:
                    det.append(partes[1])                 # a rota
            elif x['tipo'] == 'vinculo_divergente':
                # "tem CIOT" já vai no selo e o manifesto na linha de cima: fica só a divergência
                partes = [q for q in dt.split(' · ')
                          if q not in ('tem CIOT', 'sem CIOT válido') and not q.startswith('manifesto ')]
                det.append(' · '.join(partes)[:160])
            elif x['tipo'] == 'ciot_erro' or (
                    x['tipo'] == 'manifesto_sem_ctrb' and dt != 'sem CTRB, logo sem CIOT'):
                det.append(dt[:160])
        em = ps[0].get('emissao')
        mf = next((x['manifesto'] for x in ps if x.get('manifesto') and x['manifesto'] != d), '')
        if mf:
            det.insert(0, f'manifesto {mf}')
        return {'documento': d, 'tipo_operacao': ps[0].get('tipo_operacao'), 'placa': ps[0].get('placa'),
                'emissao_br': f'{em:%d/%m}' if hasattr(em, 'strftime') else '',
                'novo': tipo == 'resumo' and any(x.get('avisado_em') is None for x in ps),
                'tipos': tipos, 'detalhes': det,
                'tem_ciot': bool({'ctrb_sem_manifesto', 'vinculo_divergente'} & set(tipos))
                            and not {'sem_ciot', 'ciot_erro'} & set(tipos)}

    rodape = ''
    if tipo == 'resumo':
        corte = (agora_brt - timedelta(days=RESUMO_DIAS)).date()
        recentes = [(d, ps) for d, ps in por_doc.items()
                    if not hasattr(ps[0].get('emissao'), 'date') or ps[0]['emissao'].date() >= corte]
        antigos = len(por_doc) - len(recentes)
        mostrar = recentes[:MAX_LINHAS_RESUMO]
        titulo = 'Em aberto' if por_doc else 'Tudo em dia'
        subtitulo = (f'{agora_brt:%d/%m/%Y %H:%M} · {len(por_doc)} documento(s) · '
                     f'{resolvidas_24h} pendência(s) resolvida(s) em 24 h')
        legenda_lista = (f'Emitidos nos últimos {RESUMO_DIAS} dias ({len(recentes)})' if recentes
                         else (f'Nada emitido nos últimos {RESUMO_DIAS} dias' if por_doc else ''))
        extra = []
        if len(recentes) > len(mostrar):
            extra.append(f'+{len(recentes) - len(mostrar)} da semana')
        if antigos:
            extra.append(f'+{antigos} com mais de {RESUMO_DIAS} dias')
        if extra:
            rodape = ' · '.join(extra) + ' · lista completa na aba CIOT'
        elif not por_doc:
            rodape = 'nenhuma pendência de CIOT, manifesto ou CTRB'
    else:
        itens = list(por_doc.items())
        mostrar = itens[:MAX_LINHAS_AVISO]
        titulo = f'{len(por_doc)} documento(s) com pendência nova'
        subtitulo = f'{agora_brt:%d/%m/%Y %H:%M} · {total_abertas} pendência(s) em aberto no total'
        legenda_lista = ''
        if len(itens) > len(mostrar):
            rodape = f'+{len(itens) - len(mostrar)} documento(s) · lista completa na aba CIOT'

    # legenda: curta, só o que dá para ler sem abrir a imagem
    if tipo != 'resumo':
        cab = f'⚠️ *CIOT · {agora_brt:%d/%m %H:%M}* — {len(por_doc)} documento(s) com pendência nova'
    elif por_doc:
        cab = f'📋 *CIOT · {agora_brt:%d/%m %H:%M}* — {len(por_doc)} documento(s) em aberto'
    else:
        cab = f'✅ *CIOT · {agora_brt:%d/%m %H:%M}* — nada em aberto'
    resumo_txt = ' · '.join(f'{n} {rot}' for rot, n, _ in contadores)
    legenda = cab + (f'\n{resumo_txt}' if resumo_txt else '') + (f'\n\n🔗 {link or BASE_URL + "/ciot"}' if (link or BASE_URL) else '')

    return {'titulo': titulo, 'subtitulo': subtitulo, 'contadores': contadores,
            'legenda_lista': legenda_lista, 'rodape': rodape, 'legenda': legenda,
            'documentos': [doc(d, ps) for d, ps in mostrar]}


def _enviar_para_todos(texto, png=None):
    ok = 0
    for i, n in enumerate(_numeros()):
        if i:
            time.sleep(INTERVALO_ENVIO_SEG)
        if png is not None:
            enviado = _enviar_imagem(n, png, texto)
        else:
            enviado = _enviar_texto(n, texto)
        if enviado:
            ok += 1
        else:
            _logger.warning(f'CIOT: falha no envio para {n}')
    return ok


def _renderizar(tipo, pend, total, resolvidas, agora_brt, link=None):
    """(png, legenda) ou (None, None) se a imagem não sair — aí vai o texto."""
    if FORMATO != 'imagem':
        return None, None
    try:
        import ciot_imagem
        dados = dados_aviso(tipo, pend, total, resolvidas, agora_brt, link)
        return ciot_imagem.gerar_png(dados), dados['legenda']
    except Exception as e:
        _logger.warning(f'CIOT: imagem falhou, vai em texto: {e}')
        return None, None


def _hora_resumo():
    try:
        h, m = [int(x) for x in RESUMO_HORA.split(':')]
        return h * 60 + m
    except Exception:
        return 8 * 60


_CARENCIA_SQL = ("""(primeiro_visto <= %(corte_utc)s
    OR (tipo <> 'manifesto_sem_ctrb' AND emissao <= %(corte_brt)s)
    OR (tipo = 'manifesto_sem_ctrb' AND emissao < %(hoje_brt)s))""")


def resumo_devido(cur, agora_brt, refresh_fim=None):
    """Já passou da hora do resumo, o dado é de um refresh concluído DEPOIS dela, e o resumo
    ainda não saiu hoje?

    Sem a segunda condição, a rodada das 08:00 mandava o resumo com o dado das 05:30,
    porque o refresh das 08:00 ainda estava rodando. Se esse refresh falhar, o resumo sai
    com o que tiver depois de RESUMO_TOLERANCIA_H."""
    minutos = agora_brt.hour * 60 + agora_brt.minute
    if minutos < _hora_resumo():
        return False
    if refresh_fim is not None and minutos < _hora_resumo() + RESUMO_TOLERANCIA_H * 60:
        fim_brt = refresh_fim - timedelta(hours=3)
        hora_resumo = agora_brt.replace(hour=_hora_resumo() // 60, minute=_hora_resumo() % 60,
                                        second=0, microsecond=0)
        if fim_brt < hora_resumo:
            return False
    cur.execute("SELECT 1 FROM ciot_envios WHERE tipo = 'resumo' AND dia_brt = %s", (agora_brt.date(),))
    return cur.fetchone() is None


def avisar(cur, agora, forcar_resumo=False, so_mostrar=False, refresh_fim=None):
    """Novas a cada rodada; na primeira rodada depois de CIOT_RESUMO_HORA_BRT, o resumo do
    dia no lugar delas (as novas vão dentro do resumo, marcadas 🆕).

    Pendência dentro da carência não vai em nenhum dos dois: é o manifesto esperando o
    CTRB, e aparece na rodada seguinte. Devolve (tipo, documentos) do que saiu.
    `so_mostrar` imprime a mensagem e não envia nem marca nada.
    """
    if not so_mostrar:
        if not ENVIO_ATIVO:
            return None, 0
        if not _configurado():
            _logger.warning('CIOT: envio ligado, mas UazAPI não configurada (UAZAPI_URL/TOKEN/CIOT_UAZAPI_TO)')
            return None, 0
    agora_brt = agora - timedelta(hours=3)
    if not (so_mostrar or forcar_resumo or _no_horario(agora_brt)):
        return None, 0
    # Carência pela IDADE DO DOCUMENTO, não por quando o robô o viu: pela data de
    # "visto", no primeiro boot tudo era recém-visto e o resumo saiu "nada em aberto"
    # com 48 pendências na tabela (17/09/2026). Entra o que foi visto há mais de 2 h OU
    # cujo documento é mais velho que isso: CTRB pela hora de emissão; o manifesto (916)
    # só tem a data, então o de hoje segue a regra do "visto há 2 h".
    carencia = (_CARENCIA_SQL, {'corte_utc': agora - timedelta(hours=CARENCIA_H),
                                'corte_brt': agora_brt - timedelta(hours=CARENCIA_H),
                                'hoje_brt': agora_brt.date()})

    link = link_leitura(cur)
    cur.execute("SELECT COUNT(*) FROM ciot_pendencias WHERE resolvido_em IS NULL")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM ciot_pendencias WHERE resolvido_em >= %s",
                (agora - timedelta(hours=24),))
    resolvidas = cur.fetchone()[0]
    if forcar_resumo or resumo_devido(cur, agora_brt, refresh_fim):
        tipo = 'resumo'
        pend = _pendentes(cur, f'resolvido_em IS NULL AND {carencia[0]}', carencia[1])
        texto = montar_resumo(pend, resolvidas, agora_brt, link)
    else:
        tipo = 'novas'
        pend = _pendentes(cur, f'resolvido_em IS NULL AND avisado_em IS NULL AND {carencia[0]}',
                          carencia[1])
        if not pend:
            return None, 0
        texto = montar_mensagem(pend, total, agora_brt, link)

    docs = len({p['documento'] for p in pend})
    png, legenda = _renderizar(tipo, pend, total, resolvidas, agora_brt, link)
    if so_mostrar:
        if png is not None:
            destino = os.path.join(os.getcwd(), f'ciot_previa_{tipo}.png')
            with open(destino, 'wb') as f:
                f.write(png)
            print(legenda)
            print(f'\n[imagem gravada em {destino}]')
        else:
            print(texto)
        return tipo, docs
    ok = _enviar_para_todos(legenda, png) if png is not None else _enviar_para_todos(texto)
    if not ok:
        return None, 0
    if pend:
        cur.execute("UPDATE ciot_pendencias SET avisado_em = %s WHERE chave = ANY(%s) AND avisado_em IS NULL",
                    (agora, [p['chave'] for p in pend]))
    cur.execute("""INSERT INTO ciot_envios (tipo, dia_brt, enviado_em, documentos, destinatarios_ok)
                   VALUES (%s, %s, %s, %s, %s)""", (tipo, agora_brt.date(), agora, docs, ok))
    return tipo, docs


# ── Rodada ───────────────────────────────────────────────────────────────────

def executar(enviar=True, log=print, forcar_resumo=False, so_mostrar=False):
    """Uma rodada completa: lê o BI, confere, grava e (se ligado) avisa."""
    from server import get_token, get_db, _dax_rows, CONFIG
    agora = datetime.utcnow().replace(microsecond=0)
    desde = date.fromisoformat(DESDE)
    token = get_token()
    refresh = ultimo_refresh(token, CONFIG['group_id'], CONFIG['dataset_id'])
    conn = get_db()
    try:
        with conn:
            with conn.cursor() as cur:
                garantir_tabelas(cur)
        try:
            ctrbs, manifestos = coletar(token, _dax_rows, desde)
            pend, resumo = conferir(ctrbs, manifestos, desde)
            with conn:
                with conn.cursor() as cur:
                    novas, resolvidas = gravar(cur, pend, agora, desde)
            # O envio vem depois do commit: falha na UazAPI não desfaz a conferência.
            avisadas, tipo_envio = 0, None
            if enviar or so_mostrar:
                with conn:
                    with conn.cursor() as cur:
                        tipo_envio, avisadas = avisar(cur, agora, forcar_resumo, so_mostrar, refresh)
            with conn:
                with conn.cursor() as cur:
                    cur.execute("""INSERT INTO ciot_rodadas (executada_em, refresh_bi, abertas, novas,
                                   resolvidas, avisadas, resumo) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                                (agora, refresh, len(pend), novas, resolvidas, avisadas, json.dumps(resumo)))
            envio = f'{tipo_envio} com {avisadas} documento(s)' if tipo_envio else 'nada enviado'
            log(f'✅ CIOT: {len(pend)} em aberto ({novas} nova(s), {resolvidas} resolvida(s); {envio}) · '
                f'{resumo.get("ctrbs", 0)} CTRBs, '
                f'{resumo.get("manifestos", 0)} manifestos desde {DESDE}')
            return {'ok': True, 'abertas': len(pend), 'novas': novas, 'resolvidas': resolvidas,
                    'avisadas': avisadas, 'envio': tipo_envio, 'resumo': resumo}
        except Exception as e:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO ciot_rodadas (executada_em, refresh_bi, erro) VALUES (%s,%s,%s)",
                                (agora, refresh, str(e)[:1000]))
            raise
    finally:
        conn.close()


def deve_rodar(agora, fim, rodando, ultima_exec, ultimo_ref):
    """Decisão do laço (pura, para teste): nunca durante um refresh; depois de um refresh novo,
    só passada a folga. Sem refresh novo, não roda — a menos que CIOT_INTERVALO_MIN > 0."""
    if rodando:
        return False
    if fim is not None and agora < fim + timedelta(minutes=ESPERA_POS_REFRESH_MIN):
        return False
    if fim is not None and fim != ultimo_ref:
        return True
    if INTERVALO_MIN <= 0:
        return False
    return ultima_exec is None or agora - ultima_exec >= timedelta(minutes=INTERVALO_MIN)


def loop():
    """Laço do servidor: roda depois de cada refresh novo do BI, passada a folga. Nunca durante
    um refresh. Nunca derruba o processo."""
    from server import get_token, CONFIG
    ultima_exec = None
    ultimo_ref = None
    falhou_em = None
    no_boot = True
    while True:
        try:
            agora = datetime.utcnow()
            # Depois de uma falha, espera 30 min antes de tentar de novo o mesmo refresh
            if falhou_em is None or agora - falhou_em >= timedelta(minutes=30):
                fim, rodando = estado_refresh(get_token(), CONFIG['group_id'], CONFIG['dataset_id'])
                if no_boot and fim is not None:
                    # Ligar o robô (ou reiniciar o container) não dispara nada: o refresh
                    # que já tinha terminado fica como visto e a primeira rodada é a do
                    # próximo refresh.
                    ultimo_ref, ultima_exec, no_boot = fim, agora, False
                    print(f'ℹ️  CIOT: aguardando o próximo refresh do BI (último terminou '
                          f'{(fim - timedelta(hours=3)):%d/%m %H:%M} BRT)')
                elif deve_rodar(agora, fim, rodando, ultima_exec, ultimo_ref):
                    executar()
                    ultima_exec, ultimo_ref, falhou_em = agora, fim, None
        except Exception as e:
            print(f'⚠️  CIOT: falha na rodada: {e}')
            falhou_em = datetime.utcnow()
        time.sleep(POLL_MIN * 60)


# ── Linha de comando ─────────────────────────────────────────────────────────

def _dry_run(desde):
    from server import get_token, _dax_rows
    token = get_token()
    ctrbs, manifestos = coletar(token, _dax_rows, desde)
    pend, resumo = conferir(ctrbs, manifestos, desde)
    print(f'desde {desde} · {json.dumps(resumo, ensure_ascii=False)}')
    for t in ORDEM_TIPOS:
        itens = [p for p in pend if p['tipo'] == t]
        if not itens:
            continue
        print(f'\n== {TIPOS[t]} ({len(itens)}) ==')
        for p in sorted(itens, key=lambda p: str(p['emissao'])):
            print(f"  {p['documento']:<12} {str(p['emissao'])[:16]:<16} {p['tipo_operacao']:<10} "
                  f"{p['placa']:<8} {p['manifesto'][:28]:<28} {p['detalhe']}")


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--desde')
    ap.add_argument('--enviar', action='store_true')
    ap.add_argument('--resumo', action='store_true', help='força o resumo do dia')
    ap.add_argument('--previa', action='store_true', help='mostra a mensagem, não envia')
    a = ap.parse_args()
    if a.desde:
        DESDE = a.desde
    if a.dry_run:
        _dry_run(date.fromisoformat(DESDE))
    else:
        r = executar(enviar=a.enviar, forcar_resumo=a.resumo, so_mostrar=a.previa)
        sys.exit(0 if r.get('ok') else 1)
