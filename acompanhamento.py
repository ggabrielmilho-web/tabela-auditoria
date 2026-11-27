"""Acompanhamento das viagens — a planilha TRACKING (26/11/2026) virando tela.

A planilha era uma linha por CT-e, digitada à mão: CTRC, cliente, NF, placa (cópia do SSW) e depois
agenda, entrega, motorista, status, ocorrência, descarga e diária. Aqui a linha é a VIAGEM (o último
manifesto do CT-e, a mesma âncora do Embarques) e abre os CT-e dela; o que o SSW já sabe vem sozinho:

- CT-e, cliente, NF, frete, placa .......... conhecimentos_emitidos (BI Operacional)
- motorista, cavalo e carreta da viagem .... manifestos (916)
- saída, previsão e chegada ................ manifestos_200 (relatório 200, ssw0644)
- agenda ................................... ocorrência 15 do SSW ("ENTREGA AGENDADA P/ dd/mm/aa hh:mm");
                                            a 35 = aguardando agendamento do cliente
- entrega .................................. ocorrência 01 do SSW (MERCADORIA ENTREGUE) — SUGESTÃO que a
                                            pessoa confirma (ver `entrega_do_ssw`)
- documentação e custo extra ............... CRM - Documentos (só leitura)

O que só a pessoa sabe fica em `acomp_marcacao` (um JSON de campos por CT-e) com cada mudança em
`acomp_log`: status (lista fechada), entrega confirmada, agenda (quando o SSW não tem), nº da ocorrência
aberta na plataforma do cliente e anotação. A leitura do SSW nunca sobrescreve o que a pessoa marcou.

Quem entra (o dono da planilha, 26/11/2026): todo CT-e do período MENOS complementar, substituto e
RPS — no BI, documento sem chave de CT-e ("SUBC … FORM LISO") —, exceto o RPS da Nestlé com entrega em
Contagem, que é entrega. Conferido em out/2026: os 610 CT-e da planilha entram e nenhum fica de fora.

Entrega pelo SSW (medido ago–nov/2026 contra a planilha): a oc. 01 lançada à mão ou pelo SSWMobile bate com
a data digitada; a do SSWScan (lê a data da IMAGEM do canhoto) trouxe o dia da EMISSÃO em 86% das
discordâncias. Por isso a data do SSWScan igual à emissão não vira sugestão — aparece como aviso.
"""
import json
import re
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone

# ── Regras ────────────────────────────────────────────────────────────────────

TIPOS_FORA = {'COMPLEMENTAR FRETE', 'SUBSTITUTO'}

# Status: lista fechada (a planilha tinha 64 jeitos de escrever). `ordem` = quanto a viagem andou: a viagem
# mostra o status do CT-e MENOS adiantado, que é o que ainda pede atenção.
STATUS = [
    {'cod': 'CARREGANDO',  'rot': 'Carregando',            'cor': 'cinza',    'ordem': 1},
    {'cod': 'TRANSITO',    'rot': 'Em trânsito',           'cor': 'azul',     'ordem': 2},
    {'cod': 'NO_CLIENTE',  'rot': 'No cliente',            'cor': 'ciano',    'ordem': 3},
    {'cod': 'REAGENDADO',  'rot': 'Reagendado',            'cor': 'amarelo',  'ordem': 3},
    {'cod': 'PERDEU_AGENDA', 'rot': 'Perda de agendamento', 'cor': 'laranja', 'ordem': 3},
    {'cod': 'DESCARGA',    'rot': 'Em descarga',           'cor': 'indigo',   'ordem': 4},
    {'cod': 'ENTREGUE',    'rot': 'Entregue',              'cor': 'verde',    'ordem': 5},
    {'cod': 'RECUSADA',    'rot': 'Recusada / devolução',  'cor': 'vermelho', 'ordem': 5},
]
STATUS_COD = {s['cod'] for s in STATUS}

CAMPOS = {
    'status': 'status',          # código de STATUS
    'entrega': 'data',           # entrega confirmada pela pessoa
    'agenda': 'data_hora',       # agenda quando o SSW não tem (ou corrigida)
    'ocorrencia': 'texto',       # nº da ocorrência aberta na plataforma do cliente
    'anotacao': 'texto',
}
ROTULO_CAMPO = {'status': 'Status', 'entrega': 'Entrega', 'agenda': 'Agenda',
                'ocorrencia': 'Ocorrência no cliente', 'anotacao': 'Anotação'}

RE_AGENDA = re.compile(r'AGENDAD[AO]\s+(?:P/|PARA)\s*(\d{2})/(\d{2})/(\d{2,4})(?:\s+(\d{2}):(\d{2}))?', re.I)
RE_DATA_TXT = re.compile(r'(?:ENTREGUE|REALIZADA)\D{0,15}(\d{2})/(\d{2})/(\d{2,4})', re.I)


def _sem_acento(s):
    return ''.join(c for c in unicodedata.normalize('NFD', str(s or '')) if unicodedata.category(c) != 'Mn')


def hoje_brt():
    return datetime.now(timezone(timedelta(hours=-3))).date()


def _iso(v):
    """Data/hora do BI → 'AAAA-MM-DD' (ou None)."""
    s = str(v or '')[:10]
    return s if re.fullmatch(r'\d{4}-\d{2}-\d{2}', s) else None


def _iso_hora(d, h):
    """Data do BI + hora 'HH:MM' (ou dentro da data) → 'AAAA-MM-DDTHH:MM' ou só a data."""
    di = _iso(d)
    if not di:
        return None
    # o BI manda a hora como data-hora do Excel ('1899-12-30T10:40:00'); texto 'HH:MM' também vale
    m = re.search(r'(?:T|^)(\d{2}):(\d{2})', str(h or '').strip()) or re.search(r'T(\d{2}):(\d{2})', str(d or ''))
    hh = f'{m[1]}:{m[2]}' if m else None
    return f'{di}T{hh}' if hh and hh != '00:00' else di


def _ano(a):
    return a if len(a) == 4 else '20' + a


def chave_man(v):
    """'UDI 012345-6' / 'UDI012345-6' → 'UDI012345-6'."""
    s = re.sub(r'\s+', '', str(v or '')).upper()
    return s or None


def raiz_cnpj(cnpj, grupos=None):
    d = re.sub(r'\D', '', str(cnpj or ''))
    if len(d) < 8:
        return None
    r = d[:8]
    return (grupos or {}).get(r, r)


def eh_rps(ce):
    return not str(ce.get('chave') or '').strip()


def entra(ce):
    """Regra do dono da planilha: tudo menos complementar, substituto e RPS (fica o RPS da Nestlé em Contagem)."""
    if str(ce.get('tipo') or '').strip().upper() in TIPOS_FORA:
        return False
    if eh_rps(ce):
        return (_sem_acento(ce.get('pag')).upper().startswith('NESTLE')
                and _sem_acento(ce.get('cid')).upper().strip() == 'CONTAGEM')
    return True


def agenda_do_texto(txt):
    """'ENTREGA AGENDADA P/ 16/11/26 08:00 - CONTATO …' → '2026-11-16T08:00' (ou só a data)."""
    m = RE_AGENDA.search(str(txt or ''))
    if not m:
        return None
    d = f'{_ano(m[3])}-{m[2]}-{m[1]}'
    try:
        date.fromisoformat(d)
    except ValueError:
        return None
    return f'{d}T{m[4]}:{m[5]}' if m[4] else d


def entrega_do_ssw(ocorrencias_01, emissao):
    """A oc. 01 do CT-e → {'data', 'fonte', 'aviso'}.

    Vale a primeira baixa de entrega. A data é a do texto ("ENTREGUE EM dd/mm", "REALIZADA EM dd/mm") quando
    houver, senão a da ocorrência. SSWScan com a data igual à emissão não vira sugestão: em ago–nov/2026 foi
    a leitura errada (o dia impresso no canhoto) em 86% das discordâncias com a planilha."""
    if not ocorrencias_01:
        return None
    o = min(ocorrencias_01, key=lambda x: x.get('oc') or '9999')
    txt = str(o.get('compl') or '')
    m = RE_DATA_TXT.search(txt)
    d = f'{_ano(m[3])}-{m[2]}-{m[1]}' if m else _iso(o.get('oc'))
    scan = 'SSWSCAN' in txt.upper()
    mobile = 'SSWMOBILE' in txt.upper()
    fonte = 'SSWScan' if scan else 'SSWMobile' if mobile else 'SSW'
    if scan and d and emissao and d == emissao:
        return {'data': None, 'fonte': fonte, 'lida': d,
                'aviso': f'o SSWScan leu {d[8:10]}/{d[5:7]} no canhoto, o dia da emissão — confira a data real'}
    return {'data': d, 'fonte': fonte, 'hora': str(o.get('oc') or '')[11:16] or None}


# ── Leitura do BI ─────────────────────────────────────────────────────────────

def _dax_datas(ini, fim):
    return f"DATE({ini.year},{ini.month},{ini.day})", f"DATE({fim.year},{fim.month},{fim.day})"


def dax_ctes(ini, fim):
    a, b = _dax_datas(ini, fim)
    t = "'public conhecimentos_emitidos'"
    return (f"EVALUATE CALCULATETABLE(SELECTCOLUMNS({t}, \"ctrc\", [serie_numero_ctrc], \"tipo\", [tipo_documento], "
            f"\"emissao\", [data_emissao], \"cnpj_pag\", [cnpj_pagador], \"pag\", [cliente_pagador], "
            f"\"reme\", [cliente_remetente], \"dest\", [cliente_destinatario], \"cid\", [cidade_entrega], \"uf\", [uf_entrega], "
            f"\"cid_o\", [cidade_origem_prestacao], \"uf_o\", [uf_origem_prestacao], \"nf\", [numero_nota_fiscal], "
            f"\"frete\", [valor_frete], \"placa\", [placa_cavalo], \"carreta\", [placa_carreta], \"manu\", [ultimo_manifesto], "
            f"\"obs\", [observacao], \"chave\", [chave_cte], \"peso\", [peso_real_kg], \"placa_col\", [placa_coleta]), "
            f"{t}[data_emissao] >= {a}, {t}[data_emissao] <= {b})")


def dax_manifestos(ini):
    a, _ = _dax_datas(ini, ini)
    t = "'public manifestos'"
    return (f"EVALUATE CALCULATETABLE(SELECTCOLUMNS({t}, \"chave\", [CHAVE_MANIFESTO], \"emi\", [data_emissao], "
            f"\"placa\", [placa_cavalo], \"carreta\", [placa_carreta], \"mot\", [nome_motorista], \"prop\", [proprietario_cavalo]), "
            f"{t}[data_emissao] >= {a})")


def dax_200(ini):
    a, _ = _dax_datas(ini, ini)
    t = "'public manifestos_200'"
    return (f"EVALUATE CALCULATETABLE(SELECTCOLUMNS({t}, \"num\", [num_manif], \"emissao\", [emissao], "
            f"\"saida\", [saida_data], \"saida_h\", [saida_hora], \"chegada\", [chegada_data], \"chegada_h\", [chegada_hora], "
            f"\"prev\", [prev_chegada_data], \"prev_h\", [prev_chegada_hora], \"mot\", [motorista], \"placa\", [placa_cavalo], "
            f"\"sit\", [situacao_manifesto], \"motivo\", [motivo], \"dest\", [cidade_uf_dest], \"orig\", [cidade_uf_origem]), "
            f"{t}[emissao] >= {a})")


DAX_031 = ("EVALUATE CALCULATETABLE(SELECTCOLUMNS('public ocorrencias_031', \"ctrc\", [ctrc], \"cod\", [ocor_codigo], "
           "\"inc\", [incluida_em], \"oc\", [ocorrida_em], \"compl\", [ocor_complemento]), "
           "'public ocorrencias_031'[ocor_codigo] IN {1, 15, 35})")


_CACHE = {}
CACHE_S = 600   # o 031 e o 200 chegam 8×/dia; 10 min segura a tela sem esconder o que chegou


def periodo_do_mes(mes, hoje=None):
    """'AAAA-MM' → (1º dia, último dia ou hoje)."""
    hoje = hoje or hoje_brt()
    if not mes or not re.fullmatch(r'\d{4}-\d{2}', mes):
        mes = hoje.strftime('%Y-%m')
    ini = date(int(mes[:4]), int(mes[5:]), 1)
    prox = (ini.replace(day=28) + timedelta(days=4)).replace(day=1)
    return mes, ini, min(prox - timedelta(days=1), hoje)


def dados_bi(dax, mes, forcar=False, hoje=None):
    """BI → {'ctes', 'man', 'm200', 'oc'} do mês (cache de 10 min por mês)."""
    mes, ini, fim = periodo_do_mes(mes, hoje)
    c = _CACHE.get(mes)
    if c and not forcar and time.time() - c['t'] < CACHE_S:
        return c['d']
    folga = ini - timedelta(days=20)   # manifesto de CT-e do dia 1º pode ser do mês anterior
    d = {'mes': mes, 'ini': ini.isoformat(), 'fim': fim.isoformat(),
         'ctes': dax(dax_ctes(ini, fim)), 'man': dax(dax_manifestos(folga)),
         'm200': dax(dax_200(folga)), 'oc': dax(DAX_031),
         'lido_em': datetime.now(timezone(timedelta(hours=-3))).strftime('%d/%m/%Y %H:%M')}
    _CACHE[mes] = {'t': time.time(), 'd': d}
    return d


# ── Montagem ──────────────────────────────────────────────────────────────────

def _num(v):
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _transporte(obs):
    from crm_documentos import transporte_da_obs
    return transporte_da_obs(obs)


# O pedido do cliente na observação do CT-e (nov/2026: 137 de 1.603 CT-e): "PEDIDO DE COMPRA 7200000001" e
# "PEDIDO 7200000002" (Nestlé, a mesma leitura do Arquivo Nestlé) e "O.C. 600001" (ordem de compra).
_RE_PEDIDO = re.compile(r'\bPEDIDO(?:\s+DE\s+COMPRA)?\s*[:\-=Nº°.]*\s*(\d{5,})', re.I)
_RE_OC = re.compile(r'\bO\.?\s?C\.?\s*[:\-=Nº°.]*\s*(\d{4,})', re.I)


def pedido_da_obs(obs):
    """Observação do CT-e → '7200000001' / 'OC 600001' (vários: separados por vírgula), ou None."""
    s = str(obs or '')
    achou = [m[1] for m in _RE_PEDIDO.finditer(s)] + ['OC ' + m[1] for m in _RE_OC.finditer(s)]
    return ', '.join(dict.fromkeys(achou)) or None


def _nf(v):
    s = str(v or '').strip()
    return s[:-2] if s.endswith('.0') else s


def montar(d, marc, crm=None, grupos=None, hoje=None):
    """Dados do BI + marcações → {'ctes': [...], 'viagens': {...}}.

    Cada CT-e leva o que o SSW diz (`ssw`: agenda, entrega sugerida, status sugerido, avisos) e o que a
    pessoa marcou (`m`). A tela junta por viagem e escolhe o efetivo (marcado vence o sugerido)."""
    hoje = (hoje or hoje_brt()).isoformat()
    crm = crm or {}
    man = {chave_man(m.get('chave')): m for m in d['man']}
    m200 = {chave_man(m.get('num')): m for m in d['m200']}
    oc = {}
    for o in d['oc']:
        oc.setdefault(str(o.get('ctrc') or '').replace(' ', '').upper(), []).append(o)

    ctes, viagens = [], {}
    for c in d['ctes']:
        if not entra(c):
            continue
        ctrc = str(c.get('ctrc') or '').replace(' ', '').upper()
        emissao = _iso(c.get('emissao'))
        km = chave_man(c.get('manu'))
        # CT-e sem manifesto no SSW (out/2026: 39, quase todos coletas da Sanchez Cano com frete de rateio): a placa
        # só existe na COLETA. Os da mesma placa no mesmo dia viram uma "viagem" só, sem motorista (ele mora no
        # manifesto). A planilha TRACKING não trazia esses CT-e (1 de 39).
        placa_col = str(c.get('placa_col') or '').strip().upper()
        vid = km or (f'SEM:{placa_col}:{emissao}' if placa_col else f'SEM:{ctrc}')
        ocs = oc.get(ctrc, [])
        o15 = sorted([x for x in ocs if int(x.get('cod') or 0) == 15], key=lambda x: x.get('inc') or '')
        o35 = [x for x in ocs if int(x.get('cod') or 0) == 35]
        agenda = agenda_do_texto(o15[-1].get('compl')) if o15 else None
        ent = entrega_do_ssw([x for x in ocs if int(x.get('cod') or 0) == 1], emissao)
        r2 = m200.get(km) if km else None
        mf = man.get(km) if km else None

        avisos = []
        if ent and ent.get('aviso'):
            avisos.append(ent['aviso'])
        # a placa que vale é a do MANIFESTO (pedido de 26/11/2026); a do CT-e só serve quando não há manifesto
        placa = str(c.get('placa') or '').strip().upper() or placa_col
        if r2 and str(r2.get('sit') or '').upper() == 'CANCELADO':
            avisos.append('manifesto CANCELADO no SSW' + (f" ({r2.get('motivo')})" if r2.get('motivo') else ''))

        # status sugerido: só o que o SSW prova
        if ent and ent.get('data'):
            sug, por = 'ENTREGUE', f"entrega no SSW ({ent['fonte']}) em {ent['data'][8:10]}/{ent['data'][5:7]}"
        elif r2 and _iso(r2.get('chegada')):
            sug, por = 'NO_CLIENTE', f"chegada do manifesto no SSW em {_iso(r2['chegada'])[8:10]}/{_iso(r2['chegada'])[5:7]}"
        elif r2 and (_iso(r2.get('saida')) or str(r2.get('sit') or '').upper() == 'EM TRANSITO'):
            sug, por = 'TRANSITO', 'manifesto em trânsito no SSW'
        else:
            sug, por = 'CARREGANDO', ('manifesto emitido, sem saída no relatório 200 do SSW' if km
                                      else 'CT-e sem manifesto no SSW')

        mk = marc.get(ctrc) or {}
        k_crm = crm.get(ctrc) or {}
        ctes.append({
            'k': ctrc, 'v': vid, 'emissao': emissao, 'tipo': c.get('tipo'), 'nf': _nf(c.get('nf')),
            'frete': _num(c.get('frete')), 'peso': _num(c.get('peso')),
            'cliente': str(c.get('pag') or '').strip(), 'raiz': raiz_cnpj(c.get('cnpj_pag'), grupos),
            'reme': c.get('reme'), 'dest': c.get('dest'), 'cid': c.get('cid'), 'uf': c.get('uf'),
            'cid_o': c.get('cid_o'), 'uf_o': c.get('uf_o'), 'placa': placa, 'rps': eh_rps(c),
            # a coluna B10 da aba Nestlé da planilha: a carga na observação (a mesma leitura do CRM)
            'b10': _transporte(c.get('obs')),
            'pedido': pedido_da_obs(c.get('obs')),
            'ssw': {'agenda': agenda, 'aguardando_agenda': bool(o35) and not o15,
                    'entrega': ent, 'status': sug, 'status_por': por, 'avisos': avisos},
            'crm': k_crm or None,
            'm': mk.get('campos') or {}, 'por': mk.get('por'), 'em': mk.get('em'),
        })
        if vid not in viagens:
            viagens[vid] = {
                'v': vid, 'manifesto': km,
                'mot': (mf or {}).get('mot') or (r2 or {}).get('mot'),
                'placa': ((mf or {}).get('placa') or (r2 or {}).get('placa') or ('' if km else placa) or '').upper(),
                'carreta': ((mf or {}).get('carreta') or c.get('carreta') or '').upper(),
                'prop': (mf or {}).get('prop'),
                'saida': _iso_hora(r2.get('saida'), r2.get('saida_h')) if r2 else None,
                'prev': _iso_hora(r2.get('prev'), r2.get('prev_h')) if r2 else None,
                'chegada': _iso_hora(r2.get('chegada'), r2.get('chegada_h')) if r2 else None,
                'sit200': (r2 or {}).get('sit'), 'orig200': (r2 or {}).get('orig'), 'dest200': (r2 or {}).get('dest'),
                'emissao_man': _iso((mf or {}).get('emi')) or _iso((r2 or {}).get('emissao')),
            }
    for v in viagens.values():
        v['atrasada'] = bool(v['prev'] and not v['chegada'] and v['prev'][:10] < hoje)
    return {'ctes': ctes, 'viagens': viagens}


# ── Banco: marcações e histórico ──────────────────────────────────────────────

DDL = """
CREATE TABLE IF NOT EXISTS acomp_marcacao (
    chave          TEXT PRIMARY KEY,               -- CTRC (formato do conhecimentos_emitidos)
    campos         JSONB NOT NULL DEFAULT '{}'::jsonb,
    atualizado_em  TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_por TEXT
);
CREATE TABLE IF NOT EXISTS acomp_log (
    id     BIGSERIAL PRIMARY KEY,
    chave  TEXT NOT NULL,
    campo  TEXT NOT NULL,
    antes  TEXT,
    depois TEXT,
    por    TEXT,
    em     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS acomp_log_chave ON acomp_log (chave, em);
"""


def garantir_tabelas(cur):
    cur.execute(DDL)


def marcacoes(cur, chaves=None):
    if chaves is not None and not chaves:
        return {}
    sql = ("SELECT chave, campos, to_char(atualizado_em AT TIME ZONE 'America/Sao_Paulo', 'DD/MM HH24:MI'), "
           "atualizado_por FROM acomp_marcacao")
    if chaves is None:
        cur.execute(sql)
    else:
        cur.execute(sql + ' WHERE chave = ANY(%s)', (list(chaves),))
    return {k: {'campos': c or {}, 'em': e, 'por': p} for k, c, e, p in cur.fetchall()}


def crm_dos_ctes(cur, chaves, carteiras):
    """Etapa e custo extra do CRM - Documentos (só leitura): {ctrc: {'carteira', 'etapa', 'rot', 'cor', 'custos'}}."""
    if not chaves:
        return {}
    cur.execute("SELECT to_regclass('crm_doc_marcacao') IS NOT NULL")
    if not cur.fetchone()[0]:
        return {}
    cur.execute("SELECT chave, carteira, etapa, campos -> 'custos' FROM crm_doc_marcacao WHERE chave = ANY(%s)",
                (list(chaves),))
    out = {}
    for k, cart, etapa, custos in cur.fetchall():
        cfg = carteiras.get(cart) or {}
        e = next((x for x in cfg.get('etapas', []) if x.get('cod') == etapa), None)
        itens = custos if isinstance(custos, list) else []
        out[k] = {'carteira': cfg.get('nome') or cart, 'etapa': etapa, 'rot': (e or {}).get('rotulo') or etapa,
                  'cor': (e or {}).get('cor'), 'custos': [
                      {'tipo': i.get('tipo'), 'valor': _num(i.get('valor')), 'status': i.get('status')}
                      for i in itens if isinstance(i, dict)]}
    return out


def cargas_dos_manifestos(cur, manifestos):
    """{manifesto: id da carga do Embarques} — o robô abre a carga a partir do manifesto (`manifesto_origem`);
    é o id do mapa (/embarques/cargas/<id>/mapa) e da ficha (/viagem/<id>). Mesma escolha do `viagem.resolver`:
    sem perna vazia nem cancelada, a de carregamento mais recente."""
    mans = [re.sub(r'[^A-Z0-9]', '', m.upper()) for m in manifestos if m]
    if not mans:
        return {}
    cur.execute("SELECT to_regclass('embarques_cargas') IS NOT NULL")
    if not cur.fetchone()[0]:
        return {}
    cur.execute("""SELECT DISTINCT ON (m) m, id FROM (
                       SELECT regexp_replace(upper(manifesto_origem), '[^A-Z0-9]', '', 'g') AS m, id, data_carregamento
                         FROM embarques_cargas
                        WHERE manifesto_origem IS NOT NULL AND NOT COALESCE(viagem_vazia, FALSE)
                          AND status <> 'Cancelada') x
                    WHERE m = ANY(%s) ORDER BY m, data_carregamento DESC NULLS LAST, id DESC""", (mans,))
    return {m: i for m, i in cur.fetchall()}


def normalizar(campo, valor):
    """Valor digitado → o que se grava (None apaga). Levanta ValueError com a mensagem para a tela."""
    if campo not in CAMPOS:
        raise ValueError('Campo desconhecido.')
    if valor is None or str(valor).strip() == '':
        return None
    s = str(valor).strip()
    tipo = CAMPOS[campo]
    if tipo == 'status':
        if s not in STATUS_COD:
            raise ValueError('Status fora da lista.')
        return s
    if tipo in ('data', 'data_hora'):
        m = re.fullmatch(r'(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?', s) or \
            re.fullmatch(r'(\d{2})/(\d{2})/(\d{2,4})(?:\s+(\d{2}):(\d{2}))?', s)
        if not m:
            raise ValueError('Data inválida — use dd/mm/aaaa.')
        if '/' in s:
            d = f'{_ano(m[3])}-{m[2]}-{m[1]}'
        else:
            d = f'{m[1]}-{m[2]}-{m[3]}'
        try:
            date.fromisoformat(d)
        except ValueError:
            raise ValueError('Data inválida.')
        if tipo == 'data_hora' and m[4]:
            return f'{d}T{m[4]}:{m[5]}'
        return d
    return s[:300]


def gravar(cur, chaves, campo, valor, por):
    """Grava `campo` = `valor` nas `chaves` (CT-e), com log de cada mudança. Devolve {chave: antes} das que mudaram."""
    mudou = {}
    for k in chaves:
        cur.execute('SELECT campos FROM acomp_marcacao WHERE chave = %s FOR UPDATE', (k,))
        r = cur.fetchone()
        campos = (r[0] if r else None) or {}
        antes = campos.get(campo)
        if (antes or None) == (valor or None):
            continue
        if valor is None:
            campos.pop(campo, None)
        else:
            campos[campo] = valor
        cur.execute("""INSERT INTO acomp_marcacao (chave, campos, atualizado_em, atualizado_por)
                       VALUES (%s, %s::jsonb, now(), %s)
                       ON CONFLICT (chave) DO UPDATE SET campos = EXCLUDED.campos, atualizado_em = now(),
                                                         atualizado_por = EXCLUDED.atualizado_por""",
                    (k, json.dumps(campos, ensure_ascii=False), por))
        cur.execute('INSERT INTO acomp_log (chave, campo, antes, depois, por) VALUES (%s, %s, %s, %s, %s)',
                    (k, campo, antes, valor, por))
        mudou[k] = antes
    return mudou


def historico(cur, chave):
    cur.execute("""SELECT campo, antes, depois, por, to_char(em AT TIME ZONE 'America/Sao_Paulo', 'DD/MM/YYYY HH24:MI')
                     FROM acomp_log WHERE chave = %s ORDER BY em DESC, id DESC LIMIT 200""", (chave,))
    return [{'campo': c, 'antes': a, 'depois': d, 'por': p, 'em': e} for c, a, d, p, e in cur.fetchall()]


def ocorrencias_do_cte(d, chave):
    """As ocorrências 01/15/35 do SSW do CT-e (para a gaveta), da mais nova para a mais velha."""
    rot = {1: 'Mercadoria entregue', 15: 'Entrega agendada', 35: 'Aguardando agendamento'}
    out = [{'cod': int(o.get('cod') or 0), 'rot': rot.get(int(o.get('cod') or 0)), 'em': str(o.get('oc') or '')[:16],
            'texto': o.get('compl')}
           for o in d['oc'] if str(o.get('ctrc') or '').replace(' ', '').upper() == chave]
    return sorted(out, key=lambda x: x['em'], reverse=True)
