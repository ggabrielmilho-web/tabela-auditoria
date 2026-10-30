"""Arquivo de CT-e para o portal da Nestlé (o "bloco de notas" da planilha CNH).

A Nestlé manda uma planilha .xls com macro: uma linha por NOTA FISCAL transportada, 45 campos, e o
botão "Exportar Dados" grava um TXT de colunas fixas (375 caracteres por linha, CRLF). Quase tudo é do
CT-e; o que não é — o valor e a data de cada nota — a analista copiava à mão da tela 101 > DANFES do SSW.

Aqui o TXT nasce do XML do CT-e: as chaves das notas estão nele, e o XML de cada NF-e vem do
ConsultaDanfe (grátis) ou, se ele não achar, do MeuDanfe (pago, R$ 0,03 por consulta — tabela de 28/10/2026) — as mesmas
duas APIs do DanfeZap, na ordem inversa. NF-e enviada junto com o CT-e não é consultada.

O layout é o da macro (`Conhecimento.Init` / `Exportar` da planilha), conferido byte a byte contra um
TXT real gerado por ela (28/10/2026). A única diferença é o peso: o dela foi digitado com erro de ~1 kg;
aqui vale o do CT-e, que bate com o 455 e com a soma dos pesos das NF-e. O pedido sai completado com
zeros à direita até 13 dígitos, como no arquivo que a Nestlé aceitou.

Funções puras (sem Flask); a rede fica em `buscar_nfe` e o banco no fim — o histórico dos arquivos
gerados e a marca EXPORTADO na aba Nestlé do CRM - Documentos (30/10/2026).
"""
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import timedelta, timezone
from decimal import Decimal, InvalidOperation

import requests

NS_CTE = {'c': 'http://www.portalfiscal.inf.br/cte'}
NS_NFE = {'n': 'http://www.portalfiscal.inf.br/nfe'}
TAM_LINHA = 375
TIMEOUT = 30
BRT = timezone(timedelta(hours=-3))   # fixo, como no crm_documentos: a imagem slim não tem tzdata


class ErroArquivo(ValueError):
    """Dado que não cabe no layout ou XML que não é o esperado — a mensagem vai para a tela."""


# ── Leitura dos XMLs ─────────────────────────────────────────────────────────

def _txt(no, caminho, ns):
    e = no.find(caminho, ns)
    return e.text.strip() if e is not None and e.text else None


def _dec(v):
    try:
        return Decimal(str(v)) if v not in (None, '') else None
    except InvalidOperation:
        return None


def tipo_xml(conteudo):
    """'cte', 'nfe' ou None, pelo namespace da raiz."""
    try:
        raiz = ET.fromstring(conteudo)
    except ET.ParseError:
        return None
    if raiz.tag.startswith('{' + NS_CTE['c'] + '}'):
        return 'cte'
    if raiz.tag.startswith('{' + NS_NFE['n'] + '}'):
        return 'nfe'
    return None


def ler_cte(conteudo):
    """XML do CT-e (cteProc ou CTe) → dict com o que o TXT usa."""
    raiz = ET.fromstring(conteudo)
    inf = raiz.find('.//c:infCte', NS_CTE)
    if inf is None:
        raise ErroArquivo('XML sem infCte: não é um CT-e')
    g = lambda p: _txt(inf, p, NS_CTE)
    chave = (inf.get('Id') or '')[3:]
    tp = g('c:ide/c:tpCTe')
    if tp not in ('0', '1'):
        raise ErroArquivo(f'CT-e {chave[25:34]}: tipo {tp} (substituição/anulação) não é aceito pela planilha')

    # Peso em kg: a medida "PESO REAL" do SSW; sem ela, a primeira em KG (cUnid 01) ou TON (02).
    peso = None
    medidas = [(_txt(q, 'c:cUnid', NS_CTE), (_txt(q, 'c:tpMed', NS_CTE) or '').upper(), _dec(_txt(q, 'c:qCarga', NS_CTE)))
               for q in inf.iterfind('.//c:infCarga/c:infQ', NS_CTE)]
    for un, med, q in medidas:
        if un == '01' and 'PESO REAL' in med:
            peso = q
            break
    if peso is None:
        for un, med, q in medidas:
            if un in ('01', '02') and q:
                peso = q * 1000 if un == '02' else q
                break

    # ICMS: qualquer grupo (ICMS00, 20, 45, 60, 90, OutraUF, SN) — o que existir.
    v_icms = next((_dec(e.text) for e in inf.iterfind('.//c:imp/c:ICMS//c:vICMS', NS_CTE)), None)
    v_bc = next((_dec(e.text) for e in inf.iterfind('.//c:imp/c:ICMS//c:vBC', NS_CTE)), None)
    pedagio = sum((_dec(_txt(c, 'c:vComp', NS_CTE)) or 0
                   for c in inf.iterfind('c:vPrest/c:Comp', NS_CTE)
                   if 'PEDAG' in (_txt(c, 'c:xNome', NS_CTE) or '').upper()), Decimal(0))
    obs = g('c:compl/c:xObs') or ''
    pedido = re.search(r'PEDIDO\s*:?\s*(\d+)', obs)

    return {
        'chave': chave,
        'numero': int(g('c:ide/c:nCT')),
        'serie': g('c:ide/c:serie'),
        'complementar': tp == '1',
        'chave_original': g('c:infCteComp/c:chCTe') if tp == '1' else None,
        'emissao': (g('c:ide/c:dhEmi') or '')[:10],
        'cfop': g('c:ide/c:CFOP'),
        'cnpj_emit': g('c:emit/c:CNPJ'),
        'cnpj_dest': g('c:dest/c:CNPJ') or g('c:dest/c:CPF'),
        'destinatario': g('c:dest/c:xNome'),
        'remetente': g('c:rem/c:xNome'),
        'valor_servico': _dec(g('c:vPrest/c:vTPrest')),
        'valor_carga': _dec(g('c:infCTeNorm/c:infCarga/c:vCarga')),
        'peso': peso,
        'valor_icms': v_icms,
        'base_icms': v_bc,
        'pedagio': pedagio,
        'pedido': pedido.group(1) if pedido else '',
        'chaves_nfe': [e.text.strip() for e in inf.iterfind('c:infCTeNorm/c:infDoc/c:infNFe/c:chave', NS_CTE)],
    }


def ler_nfe(conteudo):
    """XML da NF-e (nfeProc ou NFe) → valor, data, peso. Devolve None se não for NF-e."""
    raiz = ET.fromstring(conteudo)
    inf = raiz.find('.//n:infNFe', NS_NFE)
    if inf is None:
        return None
    pesos = [_dec(e.text) for e in inf.iterfind('n:transp/n:vol/n:pesoB', NS_NFE)]
    return {
        'chave': (inf.get('Id') or '')[3:],
        'valor': _dec(_txt(inf, 'n:total/n:ICMSTot/n:vNF', NS_NFE)),
        'data': (_txt(inf, 'n:ide/n:dhEmi', NS_NFE) or _txt(inf, 'n:ide/n:dEmi', NS_NFE) or '')[:10],
        'peso': sum((p for p in pesos if p), Decimal(0)) or None,
    }


def nota_da_chave(chave):
    """O que a chave da NF-e já diz sozinha: série, número e CNPJ do emitente."""
    return {'chave': chave, 'serie': chave[22:25], 'numero': int(chave[25:34]), 'cnpj_emit': chave[6:20]}


# ── Busca da NF-e (ConsultaDanfe → MeuDanfe) ────────────────────────────────

def _consultadanfe(chave, tentativas=2):
    """Falha passageira (limite, 5xx, demora) tenta de novo ANTES de cair no MeuDanfe, que cobra.
    Fora da janela de datas é definitivo: vai direto para o pago. Em 28/10/2026 uma rodada de 67 notas
    caiu inteira no MeuDanfe sem registro do porquê; 30 consultas seguidas não deram 429."""
    import base64
    erro = None
    for i in range(tentativas):
        if i:
            time.sleep(espera)
        espera = 3
        try:
            r = requests.post('https://consultadanfe.com/api/v1/consulta', json={'chave': chave}, timeout=TIMEOUT)
        except requests.RequestException as e:
            erro = f'ConsultaDanfe: {type(e).__name__}'
            continue
        if r.status_code == 200:
            x = (r.json() or {}).get('xml_base64')
            if x:
                return base64.b64decode(x), None
            return None, 'ConsultaDanfe sem XML'
        if r.status_code == 400 and r.headers.get('X-Error-Code') == 'data_fora_da_janela':
            return None, 'ConsultaDanfe: nota fora da janela (mês atual, ou anterior até o dia 15)'
        if r.status_code == 429:
            erro = 'ConsultaDanfe: limite de consultas'
            try:
                espera = min(max(int(r.headers.get('Retry-After') or 3), 1), 15)
            except ValueError:
                pass
            continue
        if r.status_code >= 500:
            erro = f'ConsultaDanfe: status {r.status_code}'
            continue
        try:
            msg = (r.json() or {}).get('message')
        except ValueError:
            msg = None
        return None, f'ConsultaDanfe: {msg or "status " + str(r.status_code)}'
    return None, erro


def _meudanfe(chave, api_key):
    """PUT add (cobra R$ 0,03 por NF-e consultada) + GET xml. O campo "data" do GET é o XML em
    TEXTO, não base64 (achado do DanfeZap)."""
    base = 'https://api.meudanfe.com.br/v2'
    h = {'Api-Key': api_key, 'Content-Type': 'application/json'}
    r = requests.put(f'{base}/fd/add/{chave}', headers=h, timeout=TIMEOUT)
    if r.status_code != 200:
        try:
            msg = r.json().get('message')
        except Exception:
            msg = None
        return None, f'MeuDanfe: {msg or "status " + str(r.status_code)}'
    r = requests.get(f'{base}/fd/get/xml/{chave}', headers=h, timeout=TIMEOUT)
    if r.status_code != 200:
        return None, f'MeuDanfe: XML status {r.status_code}'
    x = (r.json() or {}).get('data')
    return (x.encode('utf-8'), None) if x else (None, 'MeuDanfe sem XML')


def buscar_nfe(chave, meudanfe_key=None):
    """{'ok', 'fonte', 'valor', 'data', 'peso', 'erro', 'motivo_gratis'}. Nunca levanta: erro de rede vira
    'erro'. Cada nota deixa uma linha no log do container (`docker service logs … | grep "Arquivo Nestlé"`)
    com a origem e, se pagou, por que a grátis não serviu."""
    r = _buscar_nfe(chave, meudanfe_key)
    print(f'[Arquivo Nestlé] NF {chave[25:34]} chave {chave}: '
          + (r['fonte'] if r['ok'] else 'NÃO ACHOU')
          + (f' — grátis: {r["motivo_gratis"]}' if r.get('motivo_gratis') else '')
          + ('' if r['ok'] else f' — {r["erro"]}'), flush=True)
    return r


def _buscar_nfe(chave, meudanfe_key):
    erros = []
    fontes = [('ConsultaDanfe', lambda: _consultadanfe(chave))]
    if meudanfe_key:
        fontes.append(('MeuDanfe', lambda: _meudanfe(chave, meudanfe_key)))
    for nome, f in fontes:
        try:
            xml, erro = f()
        except requests.RequestException as e:
            xml, erro = None, f'{nome}: {type(e).__name__}'
        if xml:
            try:
                n = ler_nfe(xml)
            except ET.ParseError:
                n = None
            if n and n['chave'] == chave and n['valor'] is not None:
                return {'ok': True, 'fonte': nome, 'erro': None, 'motivo_gratis': erros[0] if erros else None, **n}
            erro = f'{nome}: XML não é desta nota'
        erros.append(erro)
    if not meudanfe_key:
        erros.append('MeuDanfe não configurado')
    return {'ok': False, 'fonte': None, 'valor': None, 'data': None, 'peso': None, 'erro': ' | '.join(erros)}


def buscar_lote(chaves, meudanfe_key=None, buscar=buscar_nfe):
    """{chave: resultado de buscar_nfe}. 3 de cada vez (~1 s por nota na grátis; 30 seguidas a 3 de cada
    vez não deram 429 em 28/10/2026). Serve à prévia e ao "Tentar de novo" das notas que nenhuma das duas achou."""
    chaves = [c for c in dict.fromkeys(chaves) if re.fullmatch(r'\d{44}', c or '')]
    if not chaves:
        return {}
    with ThreadPoolExecutor(max_workers=3) as ex:
        return dict(zip(chaves, ex.map(lambda k: buscar(k, meudanfe_key), chaves)))


def montar_previa(arquivos, meudanfe_key=None, buscar=buscar_nfe):
    """`arquivos` = [(nome, bytes)]. CT-e e NF-e misturados; NF-e enviada vale antes da consulta.
    Devolve {'ctes': [...], 'avisos': [...]} pronto para a tela — o usuário confere e manda gerar."""
    ctes, nfes, avisos = [], {}, []
    for nome, conteudo in arquivos:
        t = tipo_xml(conteudo)
        try:
            if t == 'cte':
                ctes.append(ler_cte(conteudo))
            elif t == 'nfe':
                n = ler_nfe(conteudo)
                if n:
                    nfes[n['chave']] = {**n, 'ok': True, 'fonte': 'arquivo', 'erro': None}
            else:
                avisos.append(f'{nome}: não é XML de CT-e nem de NF-e — ignorado')
        except (ET.ParseError, ErroArquivo, TypeError, ValueError) as e:
            avisos.append(f'{nome}: {e}')

    vistos, unicos = set(), []
    for c in ctes:
        if c['chave'] in vistos:
            avisos.append(f'CT-e {c["numero"]} enviado duas vezes — considerado uma vez')
            continue
        vistos.add(c['chave'])
        unicos.append(c)
    unicos.sort(key=lambda c: (c['serie'], c['numero']))

    faltam = sorted({ch for c in unicos for ch in c['chaves_nfe']} - set(nfes))
    nfes.update(buscar_lote(faltam, meudanfe_key, buscar))

    for c in unicos:
        c['notas'] = []
        for ch in c['chaves_nfe']:
            n = nfes.get(ch) or {}
            c['notas'].append({**nota_da_chave(ch), 'valor': n.get('valor'), 'data': n.get('data'),
                               'peso': n.get('peso'), 'fonte': n.get('fonte'), 'erro': n.get('erro')})
        soma = sum((n['valor'] or 0 for n in c['notas']), Decimal(0))
        c['soma_notas'] = soma
        if c['notas'] and all(n['valor'] is not None for n in c['notas']) and c['valor_carga'] is not None \
                and abs(soma - c['valor_carga']) > Decimal('0.01'):
            avisos.append(f'CT-e {c["numero"]}: soma das notas R$ {soma} ≠ valor da carga do CT-e R$ {c["valor_carga"]}')
    # Nenhuma das duas achou (raro, mas acontece: nota recém-autorizada, API fora do ar). Não trava: a
    # tela abre valor e data dessas notas para digitar e oferece consultar de novo só elas.
    return {'ctes': unicos, 'avisos': avisos}


# ── Montagem do TXT (layout da macro) ───────────────────────────────────────

def _num(v, n, casas, campo):
    """Número sem separador, zeros à esquerda (o `Format` + `Replace` da macro)."""
    d = _dec(v)
    if d is None:
        raise ErroArquivo(f'{campo}: valor inválido ({v!r})')
    s = str(int((d * 10 ** casas).quantize(Decimal(1))))
    if d < 0 or len(s) > n:
        raise ErroArquivo(f'{campo}: {v} não cabe em {n} dígitos')
    return s.zfill(n)


def _opc(v, n, casas, campo):
    """Campo numérico opcional: vazio = espaços, como a macro faz com célula em branco."""
    return ' ' * n if v in (None, '') else _num(v, n, casas, campo)


def _cnpj(c, campo):
    c = re.sub(r'\D', '', c or '')
    if len(c) != 14:
        raise ErroArquivo(f'{campo}: CNPJ inválido ({c or "vazio"})')
    return c[:8].zfill(9) + c[8:12] + c[12:]


def _data(v, campo):
    try:
        return datetime.strptime(str(v)[:10], '%Y-%m-%d').strftime('%Y%m%d')
    except ValueError:
        raise ErroArquivo(f'{campo}: data inválida ({v or "vazia"})')


def _pedido(v):
    v = re.sub(r'\s', '', str(v or ''))
    if len(v) > 13:
        raise ErroArquivo(f'Nº Pedido: "{v}" tem mais de 13 caracteres')
    return v.ljust(13, '0') if v else ' ' * 13


def linhas_cte(c):
    """As linhas de UM CT-e, sem o sequencial (que é corrido no arquivo)."""
    rot = f'CT-e {c["numero"]}'
    ini = (_cnpj(c['cnpj_emit'], f'{rot} CNPJ Transp') + _num(c['numero'], 9, 0, f'{rot} Nº')
           + str(c['serie']).zfill(3)[:3] + c['chave'].ljust(44)[:44] + _data(c['emissao'], f'{rot} Emissão')
           + _num(c['valor_servico'], 9, 2, f'{rot} Valor Serviço'))
    base_icms = _num(c.get('base_icms') or 0, 15, 2, f'{rot} Base ICMS')
    pedagio = _num(c.get('pedagio') or 0, 15, 2, f'{rot} Pedágio')
    fim_doc = pedagio + '57' + base_icms + _pedido(c.get('pedido'))
    icms = _num(c.get('valor_icms') or 0, 9, 2, f'{rot} ICMS')
    cfop = _num(c['cfop'], 4, 0, f'{rot} CFOP')

    if c.get('complementar'):
        # Complemento: aponta o CT-e original e deixa a parte da nota (colunas 22–41) vazia — a macro
        # acusa erro se ela vier preenchida. Carga e peso são opcionais nesse caso.
        orig = c.get('chave_original') or ''
        if len(orig) != 44:
            raise ErroArquivo(f'{rot}: complemento sem a chave do CT-e original')
        meio = (_opc(c.get('valor_carga'), 9, 2, f'{rot} Vl Mercadoria') + ' ' * 9
                + _opc(c.get('peso'), 9, 3, f'{rot} Peso') + icms + cfop + ' ' * 4
                + orig[25:34] + orig[22:25] + orig + ' ' * (1 + 9 + 4 + 2))
        return [ini + meio + ' ' * (9 + 3 + 8 + 9 + 4 + 2 + 9 + 4 + 2 + 10 + 9 + 53) + fim_doc]

    if not c.get('notas'):
        raise ErroArquivo(f'{rot}: sem nota fiscal')
    cab = (ini + _num(c['valor_carga'], 9, 2, f'{rot} Vl Tot Mercadoria') + ' ' * 9
           + _num(c['peso'], 9, 3, f'{rot} Peso Bruto') + icms + cfop
           + ' ' * (4 + 9 + 3 + 44 + 1 + 9 + 4 + 2))
    dest = _cnpj(c['cnpj_dest'], f'{rot} CNPJ Dest.')
    out = []
    for n in c['notas']:
        r = f'{rot} NF {n["numero"]}'
        if n.get('valor') in (None, ''):
            raise ErroArquivo(f'{r}: falta o valor da nota')
        out.append(cab + _num(n['numero'], 9, 0, f'{r} Nº') + str(n['serie']).zfill(3)[:3]
                   + _data(n.get('data'), f'{r} Data') + _cnpj(n['cnpj_emit'], f'{r} CNPJ Remetente') + dest
                   + ' ' * 10 + _num(n['valor'], 9, 2, f'{r} Valor') + ' ' * 53 + fim_doc)
    return out


def para_json(previa):
    """Decimal → texto (sem perder centavo no caminho tela → servidor; `_num` lê texto de volta)."""
    if isinstance(previa, dict):
        return {k: para_json(v) for k, v in previa.items()}
    if isinstance(previa, list):
        return [para_json(v) for v in previa]
    return str(previa) if isinstance(previa, Decimal) else previa


def nome_arquivo(ctes, agora=None):
    """O nome da macro é "CNH" + o texto da célula A2 ("Número") + data/hora; aqui vai o nº do CT-e."""
    agora = agora or datetime.now(BRT)
    nums = '_'.join(str(c.get('numero')) for c in ctes[:5]) + ('_etc' if len(ctes) > 5 else '')
    return f'CNH{nums}_{agora:%Y%m%d%H%M%S}.txt'


def montar_txt(ctes):
    """Lista de CT-e (o formato de `montar_previa`, depois da conferência) → bytes do arquivo."""
    linhas = [l for c in ctes for l in linhas_cte(c)]
    if not linhas:
        raise ErroArquivo('Nenhuma linha para gerar')
    if len(linhas) > 9999:
        raise ErroArquivo('Mais de 9.999 linhas: o sequencial do layout tem 4 dígitos')
    linhas = [l + str(i).zfill(4) for i, l in enumerate(linhas, 1)]
    errado = [i for i, l in enumerate(linhas, 1) if len(l) != TAM_LINHA]
    if errado:
        raise ErroArquivo(f'Linha(s) {errado[:5]} fora do tamanho {TAM_LINHA} — erro de montagem, avise o suporte')
    try:
        return ('\r\n'.join(linhas) + '\r\n').encode('ascii')
    except UnicodeEncodeError:
        raise ErroArquivo('Caractere fora do ASCII no arquivo (pedido?)')


# ── Histórico dos arquivos e a marca no CRM (30/10/2026) ────────────────────
#
# Cada TXT gerado fica guardado (quem, quando, os CT-e, o conteúdo para baixar de novo) e os CT-e
# dele passam para EXPORTADO na coluna "Ticket B2B/Transp" da aba Nestlé do CRM - Documentos
# (campo `conferencia`), que ela preenchia à mão ("EXP 30/10"). A data fica no histórico da linha,
# não no texto. Só o CT-e cujo XML entrou no arquivo: o complementar marca a si mesmo, não o original.
#
# O XML não traz o CTRC, que é a chave do CRM: casa pelo nº do CT-e do 455 (`serie_numero_cte` =
# série × 10⁹ + número: 2000008484 é a série 2, nº 8484). O mesmo número existe em mais de uma
# unidade (NOD e RIO usam a série 2 em faixas que se cruzam) — aí desempata pelo valor do frete.
# CT-e que ainda não está no 455 do BI (emitido hoje, antes do refresh) fica `aguardando` e é
# marcado na próxima leitura da carteira, por até DIAS_AGUARDAR dias.

DIAS_AGUARDAR = 60
VALOR_CRM = 'EXPORTADO'

DDL_HISTORICO = """
CREATE TABLE IF NOT EXISTS cte_nestle_arquivo (
    id          BIGSERIAL PRIMARY KEY,
    nome        TEXT NOT NULL,
    gerado_em   TIMESTAMPTZ NOT NULL DEFAULT now(),
    gerado_por  TEXT,
    n_linhas    INTEGER NOT NULL,
    conteudo    TEXT NOT NULL              -- o TXT como saiu (ASCII), para baixar de novo
);
CREATE TABLE IF NOT EXISTS cte_nestle_arquivo_cte (
    id            BIGSERIAL PRIMARY KEY,
    arquivo_id    BIGINT NOT NULL REFERENCES cte_nestle_arquivo (id) ON DELETE CASCADE,
    chave_cte     TEXT NOT NULL,
    numero        BIGINT NOT NULL,
    serie         TEXT NOT NULL,
    complementar  BOOLEAN NOT NULL DEFAULT false,
    emissao       DATE,
    destinatario  TEXT,
    valor_servico NUMERIC(14, 2),
    n_notas       INTEGER NOT NULL DEFAULT 0,
    situacao      TEXT NOT NULL DEFAULT 'aguardando',   -- aguardando | marcado | ambiguo
    crm_chave     TEXT,                                  -- a linha do CRM marcada (CTRC ou MAN-…)
    marcado_em    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS cte_nestle_arquivo_cte_arq ON cte_nestle_arquivo_cte (arquivo_id);
CREATE INDEX IF NOT EXISTS cte_nestle_arquivo_cte_aguardando ON cte_nestle_arquivo_cte (situacao)
    WHERE situacao = 'aguardando';
"""


def garantir_tabelas(cur):
    cur.execute(DDL_HISTORICO)


def numero_455(serie, numero):
    """Série + nº do XML → o `serie_numero_cte` do 455 (o `cte` da linha do CRM)."""
    return int(serie) * 10 ** 9 + int(numero)


def casar_crm(itens, linhas):
    """`itens` = [{'numero_455', 'valor_servico'}]; `linhas` = as da aba Nestlé do CRM.
    → uma tupla por item: ('marcado', chave) | ('aguardando', None) | ('ambiguo', None)."""
    por_num = {}
    for l in linhas:
        if l.get('cte'):
            por_num.setdefault(int(l['cte']), []).append(l)
    out = []
    for it in itens:
        cand = por_num.get(it['numero_455']) or []
        if len(cand) > 1 and it.get('valor_servico') is not None:
            v = Decimal(str(it['valor_servico']))
            cand = [l for l in cand if l.get('frete') is not None
                    and abs(Decimal(str(l['frete'])) - v) <= Decimal('0.01')]
        if len(cand) == 1:
            out.append(('marcado', cand[0]['chave']))
        else:
            out.append(('ambiguo', None) if cand else ('aguardando', None))
    return out


def registrar(cur, nome, por, ctes, conteudo):
    """Guarda o arquivo gerado e os CT-e dele (todos `aguardando`). Devolve o id."""
    texto = conteudo.decode('ascii')
    cur.execute("""INSERT INTO cte_nestle_arquivo (nome, gerado_por, n_linhas, conteudo)
                   VALUES (%s, %s, %s, %s) RETURNING id""", (nome, por, texto.count('\r\n'), texto))
    aid = cur.fetchone()[0]
    for c in ctes:
        cur.execute("""INSERT INTO cte_nestle_arquivo_cte (arquivo_id, chave_cte, numero, serie, complementar,
                                                           emissao, destinatario, valor_servico, n_notas)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (aid, c['chave'], int(c['numero']), str(c['serie']), bool(c.get('complementar')),
                     c.get('emissao') or None, c.get('destinatario'), _dec(c.get('valor_servico')),
                     0 if c.get('complementar') else len(c.get('notas') or [])))
    return aid


def pendentes(cur):
    """CT-e `aguardando` dos últimos DIAS_AGUARDAR dias, travados para esta transação (SKIP LOCKED:
    duas leituras ao mesmo tempo não marcam o mesmo CT-e duas vezes)."""
    cur.execute("""SELECT c.id, c.serie, c.numero, c.valor_servico, a.gerado_por,
                          to_char(a.gerado_em AT TIME ZONE 'America/Sao_Paulo', 'DD/MM/YYYY HH24:MI')
                     FROM cte_nestle_arquivo_cte c JOIN cte_nestle_arquivo a ON a.id = c.arquivo_id
                    WHERE c.situacao = 'aguardando' AND a.gerado_em > now() - make_interval(days => %s)
                      FOR UPDATE OF c SKIP LOCKED""", (DIAS_AGUARDAR,))
    return [{'id': i, 'numero_455': numero_455(s, n), 'valor_servico': v, 'por': p, 'em': e}
            for i, s, n, v, p, e in cur.fetchall()]


def por_do_log(p):
    """Quem aparece no histórico da linha do CRM: a pessoa e o arquivo (a data do arquivo vale quando
    a marca entra depois, na leitura em que o CT-e chegou ao BI)."""
    return f'{p["por"] or "usuário"} · Arquivo Nestlé de {p["em"]}'


def gravar_situacao(cur, id_cte, situacao, crm_chave=None):
    cur.execute("""UPDATE cte_nestle_arquivo_cte SET situacao = %s, crm_chave = %s,
                          marcado_em = CASE WHEN %s = 'marcado' THEN now() END WHERE id = %s""",
                (situacao, crm_chave, situacao, id_cte))


def historico(cur, limite=100):
    """Os últimos arquivos gerados, com os CT-e de cada um e a situação da marca no CRM."""
    cur.execute("""SELECT id, nome, to_char(gerado_em AT TIME ZONE 'America/Sao_Paulo', 'DD/MM/YYYY HH24:MI'),
                          gerado_por, n_linhas, gerado_em < now() - make_interval(days => %s)
                     FROM cte_nestle_arquivo ORDER BY gerado_em DESC, id DESC LIMIT %s""", (DIAS_AGUARDAR, limite))
    arqs = [{'id': i, 'nome': n, 'em': e, 'por': p, 'linhas': nl, 'expirado': x, 'ctes': []}
            for i, n, e, p, nl, x in cur.fetchall()]
    if not arqs:
        return []
    por_id = {a['id']: a for a in arqs}
    cur.execute("""SELECT arquivo_id, numero, serie, complementar, emissao, destinatario, valor_servico, n_notas,
                          situacao, crm_chave
                     FROM cte_nestle_arquivo_cte WHERE arquivo_id = ANY(%s) ORDER BY id""", (list(por_id),))
    for aid, num, ser, comp, emi, dest, val, nn, sit, ch in cur.fetchall():
        por_id[aid]['ctes'].append({'numero': num, 'serie': ser, 'complementar': comp,
                                    'emissao': emi.isoformat() if emi else None, 'destinatario': dest,
                                    'valor_servico': str(val) if val is not None else None, 'notas': nn,
                                    'situacao': sit, 'crm': ch})
    return arqs


def arquivo(cur, aid):
    """(nome, bytes) de um arquivo do histórico, ou None."""
    cur.execute('SELECT nome, conteudo FROM cte_nestle_arquivo WHERE id = %s', (aid,))
    r = cur.fetchone()
    return (r[0], r[1].encode('ascii')) if r else None
