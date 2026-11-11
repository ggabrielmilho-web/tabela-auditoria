# -*- coding: utf-8 -*-
"""Fechamento contábil dos autônomos (PF e PJ) — a planilha que o RH manda todo mês à MED Contadores.

Antes (roteiro "Rotinas Autonomos PF e PJ"): 073 em Excel → tirar frota/Rizza → separar físico de jurídico →
achar o CNAE das empresas novas → PROCV do PIS pelo NOME → 477 das RPA → montar as abas à mão.
Agora tudo sai do Power BI (073 = ctrbs_oss, 046 = proprietarios_046, 477 = consulta_despesas_477) e
a tela só pede o que o sistema não sabe: o CNAE que a Receita não achou e o PIS que diverge.

Regras (conferidas contra a planilha de out/26 feita à mão, 11/11/2026):
- BASE CONTRATO: CTRB de CARRETEIRO emitido no mês, sem cancelado (situacao, robô 073 Cancelados) e sem
  CTRB em nome da própria Rizza. Out/26: 526 de 527 — o que falta (GYN003904-7) foi cancelado depois.
- Físico × jurídico pelo DOCUMENTO: zeros na frente + CPF válido = pessoa física (há CPF que também passa
  como CNPJ — 5 pessoas em out/26). O resto com CNPJ válido = jurídica.
- PIS e nascimento pelo 046 (por documento, não pelo nome). Se o 046 está vazio/inválido (um carreteiro
  tinha 12345678900), vale a BASE PIS da planilha antiga dela; se os dois divergem, ela escolhe uma vez.
- CNAE: a BASE TIPO EMPRESA dela (1.421 CNPJs) + consulta pública (BrasilAPI) só para o CNPJ novo.
  Sai daqui SÓ o CNPJ da empresa.
- PEDAGIO = pedagio + vale_pedagio do 073 (>0). OUTROS = outros do 073 (>0).
- RPA = todo lançamento do evento 5511 com a competência do mês; EMISSÃO = data do pagamento.

A lógica é pura sobre os dados do BI (`montar`); server.py só cuida da sessão, do banco e do BI.
"""
import io
import os
import re
import threading
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone

import requests

BRT = timezone(timedelta(hours=-3))

_C = "'public ctrbs_oss'"
_P46 = "'public proprietarios_046'"
_DZ = "'public consulta_despesas_477'"

EVENTO_RPA = '5511'
# CNPJ-raiz da própria Rizza: CTRB de carreteiro em nome dela não é autônomo ("tirar frota ou empresa RIZZA").
# Pela raiz e não pelo nome: há carreteiro de verdade com "RIZA" no nome.
RAIZ_RIZZA = {'02572512'}
CONSULTA_CNAE = os.getenv('FECHAMENTO_CNAE_CONSULTA', 'true').strip().lower() != 'false'
URL_CNPJ = 'https://brasilapi.com.br/api/cnpj/v1/{}'
MAX_CONSULTAS = 25          # por abertura da tela; o resto fica para a próxima (raro: ~2-5 CNPJs novos/mês)

MESES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez']
MESES_LONGO = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro',
               'outubro', 'novembro', 'dezembro']


# ── normalização (funções puras) ─────────────────────────────────────────────

def dig(v):
    return re.sub(r'\D', '', str(v or ''))


def nome_norm(s):
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode()
    return re.sub(r'\s+', ' ', s).strip().upper()


def cpf_ok(s):
    if len(s) != 11 or s == s[0] * 11:
        return False
    for n in (9, 10):
        d = (sum(int(s[i]) * (n + 1 - i) for i in range(n)) * 10) % 11 % 10
        if d != int(s[n]):
            return False
    return True


def cnpj_ok(s):
    if len(s) != 14 or s == s[0] * 14:
        return False
    for n in (12, 13):
        pesos = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2] if n == 12 else [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        r = sum(int(s[i]) * pesos[i] for i in range(n)) % 11
        if (0 if r < 2 else 11 - r) != int(s[n]):
            return False
    return True


def pessoa(doc):
    """'PF', 'PJ' ou '?' pelo documento do SSW (CPF vem com zeros na frente, em 14 dígitos)."""
    d = dig(doc)
    if len(d) >= 11 and set(d[:-11]) <= {'0'} and cpf_ok(d[-11:]):
        return 'PF'
    if len(d) == 14 and cnpj_ok(d):
        return 'PJ'
    return '?'


def doc_fmt(doc):
    d = dig(doc)
    if pessoa(d) == 'PF':
        c = d[-11:]
        return f'{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}'
    if len(d) == 14:
        return f'{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}'
    return d


_SEQUENCIAS = {'12345678900', '12345678901', '12345678909', '01234567890'}


def pis_ok(s):
    """PIS/PASEP/NIT com dígito certo. 12345678900 passa no dígito — e estava no 046 de um carreteiro."""
    s = dig(s)
    if len(s) != 11 or s == s[0] * 11 or s in _SEQUENCIAS:
        return False
    w = [3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
    d = 11 - sum(int(s[i]) * w[i] for i in range(10)) % 11
    return (0 if d >= 10 else d) == int(s[10])


def pis_fmt(s):
    s = dig(s)
    return f'{s[:3]}.{s[3:8]}.{s[8:10]}-{s[10:]}' if len(s) == 11 else s


def cnae_fmt(codigo, descricao):
    c = dig(codigo).zfill(7)
    return f'{c[:2]}.{c[2:4]}-{c[4]}-{c[5:]} - {str(descricao or "").strip()}'.strip(' -')


def iso(v):
    if v is None or v == '':
        return None
    if isinstance(v, datetime):
        return v.isoformat()[:19]
    if isinstance(v, date):
        return v.isoformat()
    return str(v)[:19]


def num(v):
    try:
        return round(float(v or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def rotulo_mes(mes, longo=False):
    a, m = int(mes[:4]), int(mes[5:7])
    return f'{MESES_LONGO[m - 1]}/{a}' if longo else f'{MESES[m - 1]}/{str(a)[2:]}'


def mes_padrao(hoje=None):
    """O fechamento é do mês que acabou."""
    hoje = hoje or datetime.now(BRT).date()
    p = hoje.replace(day=1) - timedelta(days=1)
    return f'{p.year:04d}-{p.month:02d}'


def janela(mes):
    a, m = int(mes[:4]), int(mes[5:7])
    ini = date(a, m, 1)
    fim = date(a + m // 12, m % 12 + 1, 1)
    return ini, fim


def _d(d):
    return f'DATE({d.year},{d.month},{d.day})'


def _lista(vals):
    return '{' + ','.join('"' + str(v).replace('"', '') + '"' for v in vals) + '}'


def _fatias(vals, n=250):
    vals = sorted(set(vals))
    return [vals[i:i + n] for i in range(0, len(vals), n)]


# ── banco ────────────────────────────────────────────────────────────────────

DDL = """
CREATE TABLE IF NOT EXISTS fech_aut_cnae (
    doc           TEXT PRIMARY KEY,           -- CNPJ, 14 dígitos
    cnae          TEXT,                       -- '49.30-2-02 - Transporte rodoviário de carga, ...'
    origem        TEXT NOT NULL,              -- planilha | receita | manual | nao_encontrado
    situacao      TEXT,                       -- situação cadastral na Receita (ATIVA, BAIXADA...)
    razao_social  TEXT,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    por           TEXT
);
CREATE TABLE IF NOT EXISTS fech_aut_base_pis (
    nome_norm     TEXT PRIMARY KEY,           -- a BASE PIS da planilha antiga é por NOME
    nome          TEXT,
    pis           TEXT,
    nascimento    DATE,
    importado_em  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS fech_aut_pis (
    doc           TEXT PRIMARY KEY,           -- CPF como vem do SSW (14 dígitos)
    pis           TEXT NOT NULL,
    origem        TEXT NOT NULL,              -- 046 | base | manual
    por           TEXT,
    em            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS fech_aut_download (
    id            SERIAL PRIMARY KEY,
    mes           TEXT NOT NULL,
    ctrbs         TEXT[] NOT NULL,
    pendencias    INT NOT NULL DEFAULT 0,
    por           TEXT,
    em            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS fech_aut_download_mes ON fech_aut_download (mes, em DESC);
"""


def garantir_tabelas(cur):
    cur.execute(DDL)


def situacao_bases(cur):
    cur.execute("SELECT count(1) FILTER (WHERE cnae IS NOT NULL), "
                "count(1) FILTER (WHERE origem IN ('planilha', 'planilha_sem_cnae')), "
                "max(atualizado_em) FILTER (WHERE origem IN ('planilha', 'planilha_sem_cnae')) FROM fech_aut_cnae")
    n_cnae, n_plan, em_cnae = cur.fetchone()
    cur.execute('SELECT count(1), max(importado_em) FROM fech_aut_base_pis')
    n_pis, em_pis = cur.fetchone()
    em = max([x for x in (em_cnae, em_pis) if x], default=None)
    return {'cnae': n_cnae, 'cnae_planilha': n_plan, 'pis': n_pis,
            'importado_em': em.astimezone(BRT).strftime('%d/%m/%Y %H:%M') if em else None,
            'vazia': not (n_plan or n_pis)}


def _aba_por_prefixo(wb, prefixo):
    for ws in wb.worksheets:
        if nome_norm(ws.title).startswith(prefixo):
            return ws
    return None


def ler_bases(arquivo):
    """BASE TIPO EMPRESA (CNPJ → CNAE) e BASE PIS (nome → PIS, nascimento) de uma planilha de fechamento."""
    from openpyxl import load_workbook
    wb = load_workbook(arquivo, read_only=True, data_only=True)
    emp, pis = _aba_por_prefixo(wb, 'BASE TIPO EMPRESA'), _aba_por_prefixo(wb, 'BASE PIS')
    if not emp and not pis:
        raise ValueError('A planilha não tem as abas "BASE TIPO EMPRESA" nem "BASE PIS". Use a planilha de '
                         'fechamento de um mês anterior, do jeito que ela vai para a contabilidade.')
    cnaes, pessoas = {}, {}
    if emp:
        linhas = emp.iter_rows(values_only=True)
        cab = [nome_norm(c) for c in (next(linhas, None) or [])]
        if not cab or 'CNPJ' not in (cab[0] if cab else '') or len(cab) < 3:
            raise ValueError('A aba BASE TIPO EMPRESA não tem o cabeçalho esperado (CNPJ/CPF, PROPRIETARIO, TIPO).')
        for r in linhas:
            d, cn = dig(r[0] if r else ''), str((r[2] if len(r) > 2 else '') or '').strip()
            if len(d) != 14:
                continue
            # A coluna nem sempre é CNAE: há "INAPTA" (situação), "213-5 - Empresário (Individual)" (natureza
            # jurídica) e CNAE com dígito faltando. Esses entram sem CNAE e vão à consulta da Receita.
            ok = bool(re.match(r'\d{2}\.\d{2}-\d-\d{2}', cn))
            cnaes[d] = (cn if ok else None, str(r[1] or '').strip(), None if ok else (cn or None))
    if pis:
        linhas = pis.iter_rows(values_only=True)
        cab = [nome_norm(c) for c in (next(linhas, None) or [])]
        if not cab or 'CONTRATADO' not in cab[0] or 'PIS' not in (cab[1] if len(cab) > 1 else ''):
            raise ValueError('A aba BASE PIS não tem o cabeçalho esperado (CONTRATADO, PIS, Nascimento).')
        for r in linhas:
            n = nome_norm(r[0] if r else '')
            p = dig(r[1] if len(r) > 1 else '')
            nasc = r[2] if len(r) > 2 else None
            nasc = nasc.date() if isinstance(nasc, datetime) else (nasc if isinstance(nasc, date) else None)
            if n and (p or nasc):
                pessoas[n] = (str(r[0]).strip(), p.zfill(11) if p else None, nasc)
    wb.close()
    return cnaes, pessoas


def importar_bases(cur, arquivo, por):
    """Grava as bases da planilha. CNAE: só entra/atualiza o que veio da planilha (consulta ou digitação
    dela valem mais). PIS: a base inteira é trocada pela da planilha mais nova."""
    cnaes, pessoas = ler_bases(arquivo)
    novos = 0
    for d, (cn, razao, valor_dela) in cnaes.items():
        cur.execute("""INSERT INTO fech_aut_cnae (doc, cnae, origem, situacao, razao_social, por)
                       VALUES (%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (doc) DO UPDATE SET cnae = EXCLUDED.cnae, origem = EXCLUDED.origem,
                           situacao = EXCLUDED.situacao, razao_social = EXCLUDED.razao_social,
                           atualizado_em = now(), por = EXCLUDED.por
                       WHERE fech_aut_cnae.origem IN ('planilha', 'planilha_sem_cnae', 'nao_encontrado')
                       RETURNING (xmax = 0)""",
                    (d, cn, 'planilha' if cn else 'planilha_sem_cnae', valor_dela, razao, por))
        r = cur.fetchone()
        novos += 1 if r and r[0] else 0
    if pessoas:
        cur.execute('DELETE FROM fech_aut_base_pis')
        for n, (nome, p, nasc) in pessoas.items():
            cur.execute('INSERT INTO fech_aut_base_pis (nome_norm, nome, pis, nascimento) VALUES (%s,%s,%s,%s)',
                        (n, nome, p, nasc))
    return {'cnae': len(cnaes), 'cnae_novos': novos, 'pis': len(pessoas)}


def _carregar_bases(cur, docs_pj):
    cnae = {}
    for f in _fatias(docs_pj):
        cur.execute('SELECT doc, cnae, origem, situacao, razao_social, atualizado_em FROM fech_aut_cnae '
                    'WHERE doc = ANY(%s)', (f,))
        for d, cn, o, s, rz, em in cur.fetchall():
            cnae[d] = {'cnae': cn, 'origem': o, 'situacao': s, 'razao': rz, 'em': em}
    cur.execute('SELECT nome_norm, pis, nascimento FROM fech_aut_base_pis')
    base_pis = {n: {'pis': p, 'nasc': nasc.isoformat() if nasc else None} for n, p, nasc in cur.fetchall()}
    cur.execute('SELECT doc, pis, origem, por, em FROM fech_aut_pis')
    escolha = {d: {'pis': p, 'origem': o, 'por': por, 'em': em} for d, p, o, por, em in cur.fetchall()}
    return cnae, base_pis, escolha


def salvar_cnae(cur, doc, cnae, por):
    d = dig(doc)
    if len(d) != 14:
        raise ValueError('CNPJ inválido.')
    cn = re.sub(r'\s+', ' ', str(cnae or '')).strip()
    m = re.match(r'^(\d{2})\.?(\d{2})-?(\d)-?(\d{2})\s*-?\s*(.*)$', cn)
    if not m:
        raise ValueError('Informe o CNAE como 49.30-2-02 (e a descrição, se quiser).')
    cn = f'{m.group(1)}.{m.group(2)}-{m.group(3)}-{m.group(4)}' + (f' - {m.group(5).strip()}' if m.group(5).strip() else '')
    cur.execute("""INSERT INTO fech_aut_cnae (doc, cnae, origem, por) VALUES (%s,%s,'manual',%s)
                   ON CONFLICT (doc) DO UPDATE SET cnae = EXCLUDED.cnae, origem = 'manual', atualizado_em = now(),
                       por = EXCLUDED.por""", (d, cn, por))
    return cn


def salvar_pis(cur, doc, pis, origem, por):
    d, p = dig(doc), dig(pis)
    if not pis_ok(p):
        raise ValueError(f'PIS {pis} não confere (dígito verificador).')
    if origem not in ('046', 'base', 'manual'):
        raise ValueError('Origem inválida.')
    cur.execute("""INSERT INTO fech_aut_pis (doc, pis, origem, por) VALUES (%s,%s,%s,%s)
                   ON CONFLICT (doc) DO UPDATE SET pis = EXCLUDED.pis, origem = EXCLUDED.origem, por = EXCLUDED.por,
                       em = now()""", (d, p, origem, por))
    return p


def registrar_download(cur, mes, ctrbs, pendencias, por):
    cur.execute('INSERT INTO fech_aut_download (mes, ctrbs, pendencias, por) VALUES (%s,%s,%s,%s)',
                (mes, sorted(ctrbs), pendencias, por))


def ultimo_download(cur, mes):
    cur.execute('SELECT ctrbs, pendencias, por, em FROM fech_aut_download WHERE mes = %s ORDER BY em DESC LIMIT 1',
                (mes,))
    r = cur.fetchone()
    if not r:
        return None
    return {'ctrbs': list(r[0]), 'pendencias': r[1], 'por': r[2],
            'em': r[3].astimezone(BRT).strftime('%d/%m/%Y às %H:%M')}


# ── consulta pública do CNAE ─────────────────────────────────────────────────

def consultar_receita(cnpj, timeout=8):
    """(cnae formatado, situação, razão social) pela BrasilAPI; None se a consulta falhou (tentar de novo
    depois); ('', situação, razão) se o CNPJ não tem CNAE ou não existe."""
    try:
        r = requests.get(URL_CNPJ.format(cnpj), timeout=timeout, headers={'User-Agent': 'rizza-auditoria'})
    except requests.RequestException:
        return None
    if r.status_code == 404:
        return '', 'NÃO ENCONTRADO', None
    if not r.ok:
        return None
    j = r.json()
    cn = cnae_fmt(j['cnae_fiscal'], j.get('cnae_fiscal_descricao')) if j.get('cnae_fiscal') else ''
    return cn, (j.get('descricao_situacao_cadastral') or '').upper() or None, j.get('razao_social')


def cnpjs_sem_cnae(bi, cnae, incluir_nao_encontrados=False):
    """CNPJs do mês que ainda precisam de consulta (os 'nao_encontrado' só quando ela pede de novo)."""
    falta = []
    for r in bi['ctrbs']:
        d = dig(r.get('doc'))
        if pessoa(d) != 'PJ' or str(r.get('sit') or '').upper() == 'CANCELADO' or d in falta:
            continue
        c = cnae.get(d)
        if not c or c['origem'] == 'planilha_sem_cnae' or (incluir_nao_encontrados and c['origem'] == 'nao_encontrado'):
            falta.append(d)
    return falta


def consultar_faltantes(cur, docs, por='consulta automática', limite=MAX_CONSULTAS):
    """Consulta na Receita os CNPJs sem CNAE e grava. Devolve quantos consultou."""
    if not CONSULTA_CNAE:
        return 0
    feitos = 0
    for d in docs[:limite]:
        res = consultar_receita(d)
        if res is None:
            continue
        cn, sit, razao = res
        cur.execute("""INSERT INTO fech_aut_cnae (doc, cnae, origem, situacao, razao_social, por)
                       VALUES (%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (doc) DO UPDATE SET cnae = EXCLUDED.cnae, origem = EXCLUDED.origem,
                           situacao = EXCLUDED.situacao, razao_social = EXCLUDED.razao_social, atualizado_em = now(),
                           por = EXCLUDED.por
                       WHERE fech_aut_cnae.origem IN ('nao_encontrado', 'receita', 'planilha_sem_cnae')""",
                    (d, cn or None, 'receita' if cn else 'nao_encontrado', sit, razao, por))
        feitos += 1
        time.sleep(0.3)        # a BrasilAPI é gratuita: sem rajada
    return feitos


# ── Power BI ─────────────────────────────────────────────────────────────────

_COLS_CTRB = (f'"ctrb", {_C}[ctrb], "emissao", {_C}[emissao], "doc", {_C}[cnpj_cpf_proprietario], '
              f'"nome", {_C}[proprietario], "vap", {_C}[valor_a_pagar], "pedagio", {_C}[pedagio], '
              f'"vale_pedagio", {_C}[vale_pedagio], "outros", {_C}[outros], "prop", {_C}[propriedade]')


def buscar_bi(dax_op, dax_dre, mes):
    """Tudo o que o fechamento de um mês precisa, em 5 consultas. `dax_*` levantam erro se o BI cortar."""
    ini, fim = janela(mes)
    filtro = f'{_C}[emissao] >= {_d(ini)} && {_C}[emissao] < {_d(fim)} && {_C}[propriedade] = "CARRETEIRO"'
    tem_situacao = True
    try:
        ctrbs = dax_op(f'EVALUATE SELECTCOLUMNS(FILTER({_C}, {filtro}), {_COLS_CTRB}, "sit", {_C}[situacao])')
    except Exception:
        # `situacao` entrou no modelo em 11/11/2026. Sem ela (pbix antigo republicado) o cancelado entra —
        # a tela avisa. Se a leitura sem a coluna também falhar, o erro é outro e sobe.
        ctrbs = dax_op(f'EVALUATE SELECTCOLUMNS(FILTER({_C}, {filtro}), {_COLS_CTRB})')
        tem_situacao = False
    docs = {dig(r.get('doc')) for r in ctrbs}
    p46 = dax_op(f'EVALUATE SELECTCOLUMNS({_P46}, "doc", {_P46}[cnpj_cpf], "nome", {_P46}[nome_contratado], '
                 f'"pis", {_P46}[pis_pasep], "nasc", {_P46}[nascimento])')
    competencia = f'{mes[5:7]}/{mes[2:4]}'
    rpa = dax_dre(f'EVALUATE SELECTCOLUMNS(FILTER({_DZ}, {_DZ}[evento] = "{EVENTO_RPA}" && '
                  f'{_DZ}[mes_competencia] = "{competencia}"), "numlancto", {_DZ}[numlancto], '
                  f'"parcela", {_DZ}[parcela], "historico", {_DZ}[historico_despesa], "doc", {_DZ}[cnpj_fornecedor], '
                  f'"nome", {_DZ}[nome_fornecedor], "especialidade", {_DZ}[especialidade], '
                  f'"vlr_parcela", {_DZ}[vlr_parcela], "descontos", {_DZ}[descontos], "vlr_final", {_DZ}[vlr_final], '
                  f'"pgto", {_DZ}[data_pgto], "sit", {_DZ}[sit_des])')
    # Quem já tinha CTRB antes deste mês (o resto é proprietário novo — o CNAE dele é o que a RH buscava)
    antigos = set()
    brutos = {r.get('doc') for r in ctrbs if r.get('doc')}
    for f in _fatias(brutos):
        antigos |= {dig(r.get('doc')) for r in dax_op(
            f'EVALUATE SELECTCOLUMNS(SUMMARIZE(FILTER({_C}, {_C}[emissao] < {_d(ini)} && '
            f'{_C}[cnpj_cpf_proprietario] IN {_lista(f)}), {_C}[cnpj_cpf_proprietario]), '
            f'"doc", {_C}[cnpj_cpf_proprietario])')}
    return {'mes': mes, 'ctrbs': ctrbs, 'p46': p46, 'rpa': rpa, 'novos': sorted(docs - antigos),
            'tem_situacao': tem_situacao, 'lido_em': datetime.now(BRT).strftime('%d/%m/%Y %H:%M')}


_CACHE, _CACHE_TRAVA, CACHE_S = {}, threading.Lock(), 600


def dados_bi(dax_op, dax_dre, mes, forcar=False):
    """`buscar_bi` com cache de 10 min por mês (o BI recarrega ~8×/dia)."""
    with _CACHE_TRAVA:
        v = _CACHE.get(mes)
        if not forcar and v and time.time() - v[0] < CACHE_S:
            return v[1]
    d = buscar_bi(dax_op, dax_dre, mes)
    with _CACHE_TRAVA:
        if len(_CACHE) > 12:
            _CACHE.clear()
        _CACHE[mes] = (time.time(), d)
    return d


# ── a montagem ───────────────────────────────────────────────────────────────

ABAS = [
    {'id': 'contrato', 'rot': 'Contratos', 'xlsx': 'BASE CONTRATO', 'valor': 'vap', 'cols': [
        ('ctrb', 'CTRB', 'txt'), ('emissao', 'EMISSAO', 'dt'), ('doc', 'CNPJ/CPF PROPRIETARIO', 'doc'),
        ('nome', 'PROPRIETARIO', 'txt'), ('vap', 'VALOR A PAGAR', 'r$'), ('cnae', 'TIPO EMPRESA (cnae principal)', 'txt')]},
    {'id': 'fisico', 'rot': 'Físico', 'xlsx': 'FISICO', 'valor': 'vap', 'cols': [
        ('ctrb', 'CTRB', 'txt'), ('emissao', 'EMISSAO', 'dt'), ('doc', 'CNPJ/CPF PROPRIETARIO', 'doc'),
        ('nome', 'PROPRIETARIO', 'txt'), ('vap', 'VALOR A PAGAR', 'r$'), ('pis', 'PIS/PASEP/NIT', 'pis'),
        ('nasc', 'NASCIMENTO', 'd')]},
    {'id': 'juridico', 'rot': 'Jurídico', 'xlsx': 'JURIDICO', 'valor': 'vap', 'cols': [
        ('ctrb', 'CTRB', 'txt'), ('emissao', 'EMISSAO', 'dt'), ('doc', 'CNPJ/CPF PROPRIETARIO', 'doc'),
        ('nome', 'PROPRIETARIO', 'txt'), ('vap', 'VALOR A PAGAR', 'r$'), ('cnae', 'TIPO EMPRESA', 'txt')]},
    {'id': 'outros', 'rot': 'Outros', 'xlsx': 'OUTROS', 'valor': 'outros', 'cols': [
        ('ctrb', 'CTRB', 'txt'), ('emissao', 'EMISSAO', 'dt'), ('doc', 'CNPJ/CPF PROPRIETARIO', 'doc'),
        ('nome', 'PROPRIETARIO', 'txt'), ('outros', 'OUTROS DESPESAS', 'r$')]},
    {'id': 'pedagio', 'rot': 'Pedágio', 'xlsx': 'PEDAGIO', 'valor': 'pedagio', 'cols': [
        ('ctrb', 'CTRB', 'txt'), ('emissao', 'EMISSAO', 'dt'), ('doc', 'CNPJ/CPF PROPRIETARIO', 'doc'),
        ('nome', 'PROPRIETARIO', 'txt'), ('pedagio', 'PEDAGIO', 'r$')]},
    {'id': 'rpa', 'rot': 'RPA', 'xlsx': 'RPA', 'valor': 'vlr_final', 'cols': [
        ('numlancto', 'NUMLANCTO', 'txt'), ('historico', 'HISTORICO DA DESPESA', 'txt'),
        ('doc', 'CNPJ FORNECEDOR', 'doc'), ('nome', 'NOME FORNECEDOR', 'txt'), ('especialidade', 'ESPECIALIDADE', 'txt'),
        ('vlr_parcela', 'VLR PARCELA', 'r$'), ('descontos', 'DESCONTOS', 'r$'), ('vlr_final', 'VLR FINAL', 'r$'),
        ('emissao', 'EMISSAO', 'd'), ('sit', 'SIT DES', 'txt'), ('nasc', 'DATA NASCIMENTO', 'd'), ('pis', 'PIS', 'pis')]},
]


def _pis_da_pessoa(doc, nome, p46, base_pis, escolha):
    """(pis, nascimento, origem, alerta|None) de uma pessoa física."""
    d = dig(doc)
    m = p46.get(d) or {}
    b = base_pis.get(nome_norm(nome)) or (base_pis.get(nome_norm(m.get('nome'))) if m.get('nome') else None) or {}
    p_046, p_base = dig(m.get('pis')), dig(b.get('pis'))
    p_046 = p_046.zfill(11) if p_046 else ''
    nasc = iso(m.get('nasc'))[:10] if m.get('nasc') else b.get('nasc')
    e = escolha.get(d)
    if e and pis_ok(e['pis']):
        return e['pis'], nasc, e['origem'], None
    v046, vbase = pis_ok(p_046), pis_ok(p_base)
    if v046 and (not vbase or p_base == p_046):
        return p_046, nasc, '046', None
    if v046 and vbase:
        return p_046, nasc, '046', {'tipo': 'pis_divergente', 'p046': p_046, 'pbase': p_base}
    if vbase and not m:
        return p_base, nasc, 'base', None      # não é proprietário (ex.: RPA de manutenção): só a base tem
    if vbase:
        return p_base, nasc, 'base', {'tipo': 'pis_046_invalido', 'p046': p_046 or None, 'pbase': p_base}
    return None, nasc, None, {'tipo': 'pis_falta', 'p046': p_046 or None, 'pbase': p_base or None}


def montar(bi, cnae, base_pis, escolha, ultimo=None):
    """As abas do fechamento + o que a RH precisa conferir. Função pura (o `--dry-run` e a tela usam)."""
    p46 = {}
    for r in bi['p46']:
        d = dig(r.get('doc'))
        if d:
            p46[d] = r
    contrato, fisico, juridico, outros, pedagio, rpa = [], [], [], [], [], []
    alertas = {}
    cancelados = []

    def alerta(chave, **kw):
        a = alertas.setdefault(chave, dict(kw, ctrbs=[]))
        return a

    novos = set(bi.get('novos') or [])
    for r in sorted(bi['ctrbs'], key=lambda x: (str(x.get('emissao') or ''), str(x.get('ctrb') or ''))):
        d = dig(r.get('doc'))
        if str(r.get('sit') or '').strip().upper() == 'CANCELADO':
            cancelados.append(r['ctrb'])
            continue
        if d[:8] in RAIZ_RIZZA:
            continue
        tipo = pessoa(d)
        base = {'ctrb': r['ctrb'], 'emissao': iso(r.get('emissao')), 'doc': d, 'nome': (r.get('nome') or '').strip(),
                'vap': num(r.get('vap')), 'tipo': tipo, 'novo': d in novos and tipo != 'PF'}
        if tipo == 'PF':
            pis, nasc, origem, a = _pis_da_pessoa(d, base['nome'], p46, base_pis, escolha)
            linha = dict(base, pis=pis, nasc=nasc, pis_origem=origem)
            fisico.append(linha)
            if a:
                alerta(f'{a["tipo"]}:{d}', doc=d, nome=base['nome'], **a)['ctrbs'].append(r['ctrb'])
            contrato.append(dict(base, cnae=None))
        else:
            c = cnae.get(d) or {}
            # "Novo" = o que a RH fazia à mão: empresa que estreou no mês e cujo CNAE não estava na base dela
            # (veio da Receita ou foi digitado). Estrear sozinho não basta: out/26 teve 43 estreantes, todos já na base.
            base['novo'] = base['novo'] and c.get('origem') not in ('planilha',)
            linha = dict(base, cnae=c.get('cnae'), cnae_origem=c.get('origem'))
            juridico.append(linha)
            contrato.append(dict(base, cnae=c.get('cnae')))
            if tipo == '?':
                alerta(f'documento:{d}', tipo='documento', doc=d, nome=base['nome'])['ctrbs'].append(r['ctrb'])
            elif not c.get('cnae'):
                alerta(f'cnae_falta:{d}', tipo='cnae_falta', doc=d, nome=base['nome'],
                       nao_encontrado=c.get('origem') == 'nao_encontrado',
                       na_base=c.get('situacao') if c.get('origem') == 'planilha_sem_cnae' else None
                       )['ctrbs'].append(r['ctrb'])
            elif c.get('situacao') and c['situacao'] not in ('ATIVA',):
                alerta(f'cnae_situacao:{d}', tipo='cnae_situacao', doc=d, nome=base['nome'],
                       situacao=c['situacao'], cnae=c['cnae'])['ctrbs'].append(r['ctrb'])
        ped = num(r.get('pedagio')) + num(r.get('vale_pedagio'))
        if ped > 0:
            pedagio.append(dict(base, pedagio=round(ped, 2)))
        if num(r.get('outros')) > 0:
            outros.append(dict(base, outros=num(r.get('outros'))))

    for r in sorted(bi['rpa'], key=lambda x: (str(x.get('pgto') or ''), str(x.get('numlancto') or ''))):
        d = dig(r.get('doc'))
        nome = (r.get('nome') or '').strip()
        pis, nasc, origem, a = _pis_da_pessoa(d, nome, p46, base_pis, escolha)
        rpa.append({'numlancto': r.get('numlancto'), 'parcela': r.get('parcela'),
                    'historico': (r.get('historico') or '').strip(), 'doc': d, 'nome': nome,
                    'especialidade': (r.get('especialidade') or '').strip(), 'vlr_parcela': num(r.get('vlr_parcela')),
                    'descontos': num(r.get('descontos')), 'vlr_final': num(r.get('vlr_final')),
                    'emissao': iso(r.get('pgto'))[:10] if r.get('pgto') else None, 'sit': r.get('sit'),
                    'pis': pis, 'nasc': nasc, 'pis_origem': origem, 'tipo': 'PF', 'novo': False})
        if a:
            alerta(f'{a["tipo"]}:{d}', doc=d, nome=nome, rpa=True, **a)['ctrbs'].append(f'RPA {r.get("numlancto")}')

    # Mudou depois do último download: o que já foi para a contabilidade e precisa de aviso
    mudancas = None
    if ultimo:
        antes, agora = set(ultimo['ctrbs']), {l['ctrb'] for l in contrato}
        canc = sorted(antes & set(cancelados))
        sumiu = sorted(antes - agora - set(cancelados))
        entrou = sorted(agora - antes)
        mudancas = {'em': ultimo['em'], 'por': ultimo['por'], 'cancelados': canc, 'sumiram': sumiu,
                    'entraram': entrou}
        por_ctrb = {l['ctrb']: l for l in contrato}
        for k in canc:
            alerta(f'cancelado_depois:{k}', tipo='cancelado_depois', ctrb=k, em=ultimo['em'])
        for k in entrou:
            l = por_ctrb[k]
            alerta(f'entrou_depois:{k}', tipo='entrou_depois', ctrb=k, em=ultimo['em'], nome=l['nome'], vap=l['vap'])

    abas = {'contrato': contrato, 'fisico': fisico, 'juridico': juridico, 'outros': outros, 'pedagio': pedagio,
            'rpa': rpa}
    totais = {a['id']: {'n': len(abas[a['id']]), 'valor': round(sum(l.get(a['valor']) or 0 for l in abas[a['id']]), 2)}
              for a in ABAS}
    lista = sorted(alertas.values(), key=lambda a: (ORDEM_ALERTA.index(a['tipo']), a.get('nome') or a.get('ctrb') or ''))
    for a in lista:
        a['nivel'] = NIVEL[a['tipo']]
    return {'mes': bi['mes'], 'mes_rotulo': rotulo_mes(bi['mes'], True), 'lido_em': bi['lido_em'],
            'tem_situacao': bi.get('tem_situacao', True), 'abas': abas, 'totais': totais, 'alertas': lista,
            'pendencias': sum(1 for a in lista if a['nivel'] == 'acao'), 'cancelados_no_mes': cancelados,
            'novos': sorted({l['doc'] for l in juridico if l['novo']}), 'mudancas': mudancas,
            'colunas': [{'id': a['id'], 'rot': a['rot'], 'xlsx': a['xlsx'], 'valor': a['valor'],
                         'cols': [{'k': k, 'rot': r, 'tipo': t} for k, r, t in a['cols']]} for a in ABAS]}


ORDEM_ALERTA = ['cancelado_depois', 'pis_divergente', 'pis_falta', 'cnae_falta', 'documento', 'cnae_situacao',
                'pis_046_invalido', 'entrou_depois']
# acao = ela precisa decidir; atencao = informar a contabilidade/SSW; info = resolvido sozinho, só para saber
NIVEL = {'cancelado_depois': 'atencao', 'pis_divergente': 'acao', 'pis_falta': 'acao', 'cnae_falta': 'acao',
         'documento': 'atencao', 'cnae_situacao': 'atencao', 'pis_046_invalido': 'info', 'entrou_depois': 'info'}


# ── Excel ────────────────────────────────────────────────────────────────────

def _texto_alerta(a):
    t = a['tipo']
    quem = f"{a.get('nome') or ''} ({doc_fmt(a.get('doc'))})" if a.get('doc') else ''
    if t == 'cancelado_depois':
        return f"CTRB {a['ctrb']} foi cancelado no SSW depois do download de {a['em']} — avisar a contabilidade."
    if t == 'entrou_depois':
        return f"CTRB {a['ctrb']} ({a.get('nome')}) entrou depois do download de {a['em']}."
    if t == 'pis_divergente':
        return f"{quem}: PIS no SSW (046) {pis_fmt(a['p046'])} × base {pis_fmt(a['pbase'])} — usado o do 046."
    if t == 'pis_falta':
        return f"{quem}: sem PIS válido no 046 nem na base."
    if t == 'pis_046_invalido':
        return (f"{quem}: PIS do 046 {'inválido (' + a['p046'] + ')' if a.get('p046') else 'vazio'} — usado o da "
                f"base {pis_fmt(a['pbase'])}. Corrigir o cadastro no 027.")
    if t == 'cnae_falta':
        return (f"{quem}: CNAE não encontrado" + (' na Receita' if a.get('nao_encontrado') else '')
                + (f" (a base antiga dizia \"{a['na_base']}\")" if a.get('na_base') else '') + '.')
    if t == 'cnae_situacao':
        return f"{quem}: CNPJ com situação {a['situacao']} na Receita."
    if t == 'documento':
        return f"{quem}: documento não é CPF nem CNPJ válido — conferir o cadastro no 027."
    return t


def exportar_xlsx(f):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    wb.remove(wb.active)
    azul = PatternFill('solid', fgColor='1F4E78')
    larg = {'txt': 16, 'dt': 17, 'd': 12, 'doc': 20, 'r$': 14, 'pis': 15}
    largura_col = {'PROPRIETARIO': 42, 'NOME FORNECEDOR': 36, 'TIPO EMPRESA': 70, 'TIPO EMPRESA (cnae principal)': 70,
                   'HISTORICO DA DESPESA': 26, 'ESPECIALIDADE': 28}
    for a in ABAS:
        ws = wb.create_sheet(a['xlsx'])
        for j, (_, rot, _t) in enumerate(a['cols'], start=1):
            c = ws.cell(row=1, column=j, value=rot)
            c.font = Font(bold=True, color='FFFFFF')
            c.fill = azul
            c.alignment = Alignment(vertical='center')
            ws.column_dimensions[get_column_letter(j)].width = largura_col.get(rot, larg[_t])
        linhas = f['abas'][a['id']]
        for i, l in enumerate(linhas, start=2):
            for j, (k, _rot, t) in enumerate(a['cols'], start=1):
                v = l.get(k)
                if t == 'dt' and v:
                    v = datetime.fromisoformat(v[:19])
                elif t == 'd' and v:
                    v = date.fromisoformat(v[:10])
                elif t == 'pis' and v:
                    v = int(v)
                c = ws.cell(row=i, column=j, value=v)
                if t == 'r$':
                    c.number_format = '#,##0.00'
                elif t == 'dt':
                    c.number_format = 'dd/mm/yyyy hh:mm'
                elif t == 'd':
                    c.number_format = 'dd/mm/yyyy'
        if linhas and a['id'] in ('rpa', 'pedagio', 'outros'):
            i = len(linhas) + 2
            for j, (k, _rot, t) in enumerate(a['cols'], start=1):
                if t == 'r$':
                    c = ws.cell(row=i, column=j, value=round(sum(l.get(k) or 0 for l in linhas), 2))
                    c.font = Font(bold=True)
                    c.number_format = '#,##0.00'
        ws.freeze_panes = 'A2'
        ws.auto_filter.ref = f'A1:{get_column_letter(len(a["cols"]))}{max(2, len(linhas) + 1)}'
    if f['alertas']:
        ws = wb.create_sheet('CONFERIR')
        rot = {'acao': 'PENDENTE', 'atencao': 'ATENÇÃO', 'info': 'INFORMATIVO'}
        for j, cab in enumerate(['SITUAÇÃO', 'O QUE CONFERIR', 'DOCUMENTOS'], start=1):
            c = ws.cell(row=1, column=j, value=cab)
            c.font = Font(bold=True, color='FFFFFF')
            c.fill = azul
        for i, a in enumerate(f['alertas'], start=2):
            ws.cell(row=i, column=1, value=rot[a['nivel']])
            ws.cell(row=i, column=2, value=_texto_alerta(a))
            ws.cell(row=i, column=3, value=', '.join(a.get('ctrbs') or [a.get('ctrb') or '']))
        ws.column_dimensions['A'].width = 14
        ws.column_dimensions['B'].width = 110
        ws.column_dimensions['C'].width = 40
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def nome_arquivo(mes):
    a, m = mes[:4], int(mes[5:7])
    return f'FECHAMENTO CONTABIL - {MESES_LONGO[m - 1].upper()}- {a}.xlsx'
