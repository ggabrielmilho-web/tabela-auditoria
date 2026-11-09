# -*- coding: utf-8 -*-
"""Canhoto pelo celular — o motorista fotografa o canhoto da NF e o do CT-e (proposta de 06/11/2026).

Três partes, e só a primeira fala com o Power BI:

  1. COLETA (thread `loop`, depois de cada refresh do BI): os manifestos desde `CANHOTO_DESDE` (no máximo
     `CANHOTO_JANELA_DIAS` para trás), os CT-e de cada um pelo ÚLTIMO manifesto e o cadastro 047 →
     `canhoto_viagens`, `canhoto_ctes`, `canhoto_motoristas`. Substitui as três tabelas inteiras a cada
     rodada: elas guardam só a janela, e as fotos moram em outra tabela, que nunca é apagada.
  2. MOTORISTA (`/motorista/`): entra só com o CPF e vê as viagens que ainda têm canhoto faltando.
  3. ESCRITÓRIO (aba `canhotos`): o que chegou, o que falta e as fotos sem viagem para ligar.

Decisões medidas em 06/11/2026 (detalhe na seção "Módulo Canhotos" do README):

  * A busca parte do MANIFESTO, não da carga do robô: 27% dos manifestos de carreteiro não viram carga,
    e o manifesto traz o CPF em 100% dos casos.
  * O CT-e é de quem tem o ÚLTIMO manifesto: na continuação/desengate (5,4% dos CT-e) quem entrega é o
    motorista da última perna.
  * "Em aberto" é FALTA CANHOTO, não o status da carga: a carga vira Entregue quando o caminhão sai do
    cliente, e é depois disso que o motorista manda a foto.
  * A lista é por PARADA (recebedor + cidade). Parada com `CANHOTO_LOTE_MIN` CT-e ou mais pede um
    comprovante só: os 29 manifestos da Lima e Pergher para o RJ têm até 110 CT-e, um endereço e um carimbo.
  * O BI só conhece a 1ª NF de cada CT-e: a lista mostra essa, e o motorista acrescenta as outras.

Nada aqui manda dado para fora nem escreve no SSW. A foto nunca é apagada: descartar marca a linha.
"""
import io
import os
import re
import uuid
import hashlib
from datetime import date, datetime, timedelta

# ── chaves (todas pela CLI: `docker service update --env-add`, nunca pelo stack do Portainer) ──────────


def ligado():
    """`CANHOTO_APP=true` liga a coleta e as rotas do motorista. Nasce desligada."""
    return os.getenv('CANHOTO_APP', 'false').strip().lower() == 'true'


def janela_dias():
    return max(1, int(os.getenv('CANHOTO_JANELA_DIAS', '45')))


def lote_min():
    return max(2, int(os.getenv('CANHOTO_LOTE_MIN', '6')))


def itens_exigidos():
    """Quais canhotos cada CT-e pede. Padrão: os dois (`cte,nf`)."""
    v = [x.strip().lower() for x in os.getenv('CANHOTO_ITENS', 'cte,nf').split(',')]
    return tuple(x for x in ('cte', 'nf') if x in v) or ('cte', 'nf')


def relacoes_liberadas():
    """Relação no cadastro 047 que pode entrar (F = frota, A = agregado, C = carreteiro).
    Vazio = todos. Serve para começar por um grupo só: `CANHOTO_RELACOES=F,A`."""
    v = os.getenv('CANHOTO_RELACOES', '').upper()
    return {x.strip() for x in v.split(',') if x.strip()}


def pasta():
    """Onde as fotos ficam. Em produção TEM de ser um volume: dentro do container elas somem no
    próximo deploy. Fora dele, `canhotos_fotos/` ao lado do código (no .gitignore e no .dockerignore)."""
    return os.getenv('CANHOTO_DIR') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'canhotos_fotos')


def max_bytes():
    return int(float(os.getenv('CANHOTO_MAX_MB', '8')) * 1024 * 1024)


# ── banco ─────────────────────────────────────────────────────────────────────────────────────────────

DDL = """
CREATE TABLE IF NOT EXISTS canhoto_config (
    chave        TEXT PRIMARY KEY,
    valor        TEXT,
    em           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS canhoto_viagens (
    manifesto     TEXT PRIMARY KEY,        -- chave sem pontuação: UDI0299481
    rotulo        TEXT NOT NULL,           -- como o SSW escreve: UDI 029948-1
    emissao       DATE NOT NULL,
    cpf           TEXT NOT NULL,           -- 11 dígitos
    motorista     TEXT,
    relacao       TEXT,                    -- F / A / C do cadastro 047
    placa_cavalo  TEXT,
    placa_carreta TEXT,
    unidade_origem  TEXT,
    unidade_destino TEXT,
    origem        TEXT,
    destinos      TEXT,
    n_ctes        INTEGER NOT NULL DEFAULT 0,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_canhoto_viagens_cpf ON canhoto_viagens (cpf);
CREATE TABLE IF NOT EXISTS canhoto_ctes (
    ctrc          TEXT PRIMARY KEY,        -- UDI418557-9
    manifesto     TEXT NOT NULL,
    tipo_documento TEXT,
    emissao       DATE,
    cte_num       BIGINT,                  -- série × 10⁹ + número, como no 455
    nf            TEXT,                    -- a 1ª NF (o BI não tem as outras)
    remetente_cnpj TEXT,
    remetente     TEXT,
    destinatario  TEXT,
    recebedor     TEXT,
    local_entrega TEXT,
    cidade        TEXT,
    uf            TEXT,
    parada        TEXT NOT NULL,
    parada_nome   TEXT
);
CREATE INDEX IF NOT EXISTS ix_canhoto_ctes_manifesto ON canhoto_ctes (manifesto);
CREATE TABLE IF NOT EXISTS canhoto_motoristas (
    cpf           TEXT PRIMARY KEY,
    nome          TEXT,
    relacao       TEXT,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS canhoto_fotos (
    id            BIGSERIAL PRIMARY KEY,
    uid           UUID NOT NULL UNIQUE,    -- gerado no celular: reenviar a mesma foto não duplica
    arquivo       TEXT NOT NULL,           -- relativo a CANHOTO_DIR: AAAA/MM/<uid>.jpg
    bytes         INTEGER,
    largura       INTEGER,
    altura        INTEGER,
    sha256        TEXT,
    cpf           TEXT NOT NULL,
    motorista     TEXT,
    manifesto     TEXT,
    ctrc          TEXT,
    parada        TEXT,
    tipo          TEXT NOT NULL,           -- cte | nf | nf_extra | parada | outro | solta
    molde         TEXT,                    -- nf | cte | folha | outro | livre
    nf_esperada   TEXT,
    referencia    TEXT,                    -- o que o celular disse quando a viagem já não existia
    tirada_em     TIMESTAMPTZ,
    recebida_em   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ip            TEXT,
    agente        TEXT,
    situacao      TEXT NOT NULL DEFAULT 'recebida',   -- recebida | descartada
    ligada_por    TEXT,
    ligada_em     TIMESTAMPTZ,
    descartada_por TEXT,
    descartada_em TIMESTAMPTZ,
    motivo        TEXT
);
CREATE INDEX IF NOT EXISTS ix_canhoto_fotos_manifesto ON canhoto_fotos (manifesto);
CREATE INDEX IF NOT EXISTS ix_canhoto_fotos_ctrc ON canhoto_fotos (ctrc);
CREATE INDEX IF NOT EXISTS ix_canhoto_fotos_cpf ON canhoto_fotos (cpf);
CREATE INDEX IF NOT EXISTS ix_canhoto_fotos_recebida ON canhoto_fotos (recebida_em);
"""

_DDL_FEITO = False


def garantir(conn):
    """DDL uma vez por processo, com `lock_timeout`: DDL em laço já derrubou o banco com
    "too many clients" (22/10/2026). Sem a trava em 3 s, desiste e tenta na próxima chamada."""
    global _DDL_FEITO
    if _DDL_FEITO:
        return
    cur = conn.cursor()
    try:
        cur.execute("SET LOCAL lock_timeout = '3s'")
        cur.execute(DDL)
        conn.commit()
        _DDL_FEITO = True
    except Exception:
        conn.rollback()
        cur.execute("SELECT to_regclass('canhoto_fotos') IS NOT NULL")
        if not cur.fetchone()[0]:
            raise


def config(cur, chave):
    cur.execute("SELECT valor FROM canhoto_config WHERE chave = %s", (chave,))
    r = cur.fetchone()
    return r[0] if r else None


def config_gravar(cur, chave, valor):
    cur.execute("""INSERT INTO canhoto_config (chave, valor, em) VALUES (%s, %s, NOW())
                   ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor, em = NOW()""", (chave, valor))


# ── documentos ────────────────────────────────────────────────────────────────────────────────────────

def so_digitos(v):
    return re.sub(r'\D', '', str(v or ''))


def cpf_norm(v):
    d = so_digitos(v)
    return d.zfill(11) if 0 < len(d) <= 11 else ''


def cpf_valido(c):
    if len(c) != 11 or c == c[0] * 11:
        return False
    for n in (9, 10):
        s = sum(int(c[i]) * (n + 1 - i) for i in range(n))
        if (s * 10) % 11 % 10 != int(c[n]):
            return False
    return True


def norm_doc(s):
    """'UDI 029948-1' e 'UDI029948-1' → 'UDI0299481' (mesma régua de `embarques_auto._norm`)."""
    return re.sub(r'[^A-Za-z0-9]', '', str(s or '')).upper()


def ctrc_norm(s):
    """O CTRC no formato de `conhecimentos_emitidos`: sem espaço ('UDI 418557-9' → 'UDI418557-9')."""
    return re.sub(r'\s+', '', str(s or '')).upper()


def _milhar(n):
    return f'{n:,}'.replace(',', '.')


def rotulo_cte(v):
    """O número que sai impresso na DACTE: o 455 guarda série × 10⁹ + número (84000001878 = nº 1.878, série 840)."""
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return ''
    if n <= 0:
        return ''
    serie, num = divmod(n, 10 ** 9)
    return f'{_milhar(num)} · série {serie}' if serie else _milhar(num)


def rotulo_nf(v):
    d = so_digitos(str(v or '').split('.')[0])
    return _milhar(int(d)) if d else ''


def _dia(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.fromisoformat(str(v)[:10]).date()
    except ValueError:
        return None


def _cidade(c, uf):
    c = (c or '').strip().title()
    uf = (uf or '').strip().upper()
    return f'{c}/{uf}' if c and uf else c or uf


def chave_parada(r):
    """Parada = quem recebe + cidade. O recebedor vem antes do destinatário: nos manifestos do RJ são
    46 destinatários e um recebedor só, num endereço só."""
    base = so_digitos(r.get('ec')) or so_digitos(r.get('dc')) or norm_doc(r.get('en') or r.get('dn'))
    return f"{base}|{norm_doc(r.get('cid'))}|{(r.get('uf') or '').strip().upper()}"


# ── coleta ────────────────────────────────────────────────────────────────────────────────────────────

M = "'public manifestos'"
CE = "'public conhecimentos_emitidos'"
MOT = "'public motoristas_047'"


def _d(x):
    return f'DATE({x.year},{x.month},{x.day})'


def desde_efetivo(cur, hoje=None):
    """O 1º dia que entra. `CANHOTO_DESDE` (AAAA-MM-DD) é a data de lançamento: sem ela, a 1ª coleta
    grava o dia em que rodou — senão o motorista abriria o app com até 15 viagens antigas na lista."""
    hoje = hoje or date.today()
    env = os.getenv('CANHOTO_DESDE', '').strip()
    d = _dia(env) if env else None
    if d is None:
        d = _dia(config(cur, 'desde'))
    if d is None:
        d = hoje
        config_gravar(cur, 'desde', d.isoformat())
    return max(d, hoje - timedelta(days=janela_dias()))


def coletar(dax, desde):
    """Três consultas ao dataset operacional. `dax(q)` devolve as linhas e LEVANTA se o BI cortar a
    resposta (`embarques_auto._dax`): lista cortada apagaria viagem da tela do motorista."""
    mans = dax(f"""EVALUATE SELECTCOLUMNS(FILTER({M}, {M}[data_emissao] >= {_d(desde)}),
        "k", {M}[CHAVE_MANIFESTO], "sig", {M}[sigla_manifesto], "num", {M}[numero_manifesto],
        "d", {M}[data_emissao], "cpf", {M}[cpf_motorista], "nome", {M}[nome_motorista],
        "cav", {M}[placa_cavalo], "car", {M}[placa_carreta], "uo", {M}[unidade_origem], "ud", {M}[unidade_destino])""")
    ctes = dax(f"""EVALUATE SELECTCOLUMNS(FILTER({CE}, {CE}[data_emissao] >= {_d(desde - timedelta(days=10))}
            && {CE}[tipo_documento] <> "COMPLEMENTAR FRETE" && {CE}[ultimo_manifesto] <> ""),
        "ctrc", {CE}[serie_numero_ctrc], "cte", {CE}[serie_numero_cte], "t", {CE}[tipo_documento],
        "d", {CE}[data_emissao], "um", {CE}[ultimo_manifesto], "nf", {CE}[numero_nota_fiscal],
        "rc", {CE}[cnpj_remetente], "rn", {CE}[cliente_remetente], "dc", {CE}[cnpj_destinatario],
        "dn", {CE}[cliente_destinatario], "ec", {CE}[cnpj_recebedor], "en", {CE}[cliente_recebedor],
        "loc", {CE}[local_entrega], "cid", {CE}[cidade_entrega], "uf", {CE}[uf_entrega],
        "co", {CE}[cidade_origem_prestacao], "uo", {CE}[uf_origem_prestacao])""")
    mot = dax(f"""EVALUATE SELECTCOLUMNS({MOT}, "cpf", {MOT}[cpf], "nome", {MOT}[nome],
        "rel", {MOT}[relacao_com_transportadora])""")
    return mans, ctes, mot


def montar(mans, ctes, mot):
    """Função pura: linhas do BI → (viagens, ctes, motoristas) prontos para gravar. Manifesto sem
    CPF ou sem CT-e não entra: não há o que mandar (o CT-e chega num refresh seguinte)."""
    cad = {}
    for r in mot:
        c = cpf_norm(r.get('cpf'))
        if c:
            rel = (str(r.get('rel') or '').strip().upper() or None)
            cad[c] = {'nome': str(r.get('nome') or '').strip(), 'relacao': rel[:1] if rel else None}
    por_man = {}
    for r in ctes:
        k = norm_doc(r.get('um'))
        if k and r.get('ctrc'):
            por_man.setdefault(k, []).append(r)
    viagens, linhas, vistos = [], [], set()
    for m in mans:
        k = norm_doc(m.get('k'))
        cpf = cpf_norm(m.get('cpf'))
        cs = por_man.get(k) or []
        emissao = _dia(m.get('d'))
        if not k or k in vistos or not cpf or not cs or not emissao:
            continue
        vistos.add(k)
        c47 = cad.get(cpf) or {}
        cidades = {}
        for r in cs:
            cid = _cidade(r.get('cid'), r.get('uf'))
            if cid:
                cidades[cid] = cidades.get(cid, 0) + 1
        ordem = sorted(cidades, key=lambda c: (-cidades[c], c))
        destinos = ' · '.join(ordem[:3]) + (f' +{len(ordem) - 3}' if len(ordem) > 3 else '')
        origem = next((_cidade(r.get('co'), r.get('uo')) for r in cs if r.get('co')), '')
        viagens.append({
            'manifesto': k, 'rotulo': f"{str(m.get('sig') or '').strip()} {str(m.get('num') or '').strip()}".strip() or k,
            'emissao': emissao, 'cpf': cpf, 'motorista': c47.get('nome') or str(m.get('nome') or '').strip(),
            'relacao': c47.get('relacao'), 'placa_cavalo': str(m.get('cav') or '').strip(),
            'placa_carreta': str(m.get('car') or '').strip(), 'unidade_origem': str(m.get('uo') or '').strip(),
            'unidade_destino': str(m.get('ud') or '').strip(), 'origem': origem, 'destinos': destinos,
            'n_ctes': len(cs)})
        for r in cs:
            nf = so_digitos(str(r.get('nf') or '').split('.')[0])
            try:
                cte_num = int(float(r.get('cte'))) if r.get('cte') not in (None, '') else None
            except (TypeError, ValueError):
                cte_num = None
            linhas.append({
                'ctrc': ctrc_norm(r.get('ctrc')), 'manifesto': k, 'tipo_documento': str(r.get('t') or '').strip(),
                'emissao': _dia(r.get('d')), 'cte_num': cte_num, 'nf': nf.lstrip('0') or None,
                'remetente_cnpj': so_digitos(r.get('rc')), 'remetente': str(r.get('rn') or '').strip(),
                'destinatario': str(r.get('dn') or '').strip(), 'recebedor': str(r.get('en') or '').strip(),
                'local_entrega': str(r.get('loc') or '').strip(),
                'cidade': str(r.get('cid') or '').strip().title(), 'uf': str(r.get('uf') or '').strip().upper(),
                'parada': chave_parada(r),
                'parada_nome': str(r.get('en') or '').strip() or str(r.get('dn') or '').strip()})
    # um CTRC num manifesto só (o último); se o BI repetir a linha, fica a primeira
    unicos, ja = [], set()
    for l in linhas:
        if l['ctrc'] and l['ctrc'] not in ja:
            ja.add(l['ctrc'])
            unicos.append(l)
    motoristas = [{'cpf': c, 'nome': v['nome'], 'relacao': v['relacao']} for c, v in cad.items()]
    return viagens, unicos, motoristas


def gravar(conn, viagens, ctes, motoristas):
    """Troca as tabelas da janela inteiras, numa transação. Lista vazia com a tabela cheia é BI
    quebrado, não "nenhuma viagem": aí não apaga nada."""
    from psycopg2.extras import execute_values
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM canhoto_viagens")
    antes = cur.fetchone()[0]
    if not viagens and antes:
        raise RuntimeError(f'o BI não trouxe nenhuma viagem e a tabela tem {antes}: nada foi apagado')
    cur.execute("DELETE FROM canhoto_ctes")
    cur.execute("DELETE FROM canhoto_viagens")
    if viagens:
        cols = ('manifesto', 'rotulo', 'emissao', 'cpf', 'motorista', 'relacao', 'placa_cavalo', 'placa_carreta',
                'unidade_origem', 'unidade_destino', 'origem', 'destinos', 'n_ctes')
        execute_values(cur, f"INSERT INTO canhoto_viagens ({', '.join(cols)}) VALUES %s",
                       [tuple(v[c] for c in cols) for v in viagens])
    if ctes:
        cols = ('ctrc', 'manifesto', 'tipo_documento', 'emissao', 'cte_num', 'nf', 'remetente_cnpj', 'remetente',
                'destinatario', 'recebedor', 'local_entrega', 'cidade', 'uf', 'parada', 'parada_nome')
        execute_values(cur, f"INSERT INTO canhoto_ctes ({', '.join(cols)}) VALUES %s",
                       [tuple(l[c] for c in cols) for l in ctes])
    if motoristas:
        execute_values(cur, """INSERT INTO canhoto_motoristas (cpf, nome, relacao) VALUES %s
                               ON CONFLICT (cpf) DO UPDATE SET nome = EXCLUDED.nome, relacao = EXCLUDED.relacao,
                                                               atualizado_em = NOW()""",
                       [(m['cpf'], m['nome'], m['relacao']) for m in motoristas])
    config_gravar(cur, 'ultima_coleta', datetime.now().replace(microsecond=0).isoformat())
    conn.commit()
    return {'viagens': len(viagens), 'ctes': len(ctes), 'motoristas': len(motoristas)}


def rodada(conn, dax, hoje=None):
    garantir(conn)
    cur = conn.cursor()
    desde = desde_efetivo(cur, hoje)
    config_gravar(cur, 'janela_desde', desde.isoformat())     # o que a aba mostra como "desde"
    conn.commit()
    mans, ctes, mot = coletar(dax, desde)
    v, c, m = montar(mans, ctes, mot)
    r = gravar(conn, v, c, m)
    r['desde'] = desde
    return r


def _ts(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace('T', ' ')[:26])
    except ValueError:
        return None


def loop():
    """Thread do servidor: pergunta ao BI se houve refresh (a régua única, `embarques_auto.marcador_bi`)
    e refaz as tabelas quando houve. Falha nunca derruba a thread: o pior caso é a lista do motorista
    ficar um refresh atrasada."""
    import time as _t
    from server import get_token, get_db
    import embarques_auto as e
    intervalo = int(os.getenv('CANHOTO_INTERVALO_MIN', '10')) * 60
    while True:
        conn = None
        try:
            conn = get_db()
            garantir(conn)
            tok = get_token()
            novo = e.marcador_bi(tok)
            cur = conn.cursor()
            visto = _ts(config(cur, 'marcador'))
            conn.commit()
            if novo is not None and (visto is None or novo > visto):
                r = rodada(conn, lambda q: e._dax(tok, q))
                cur = conn.cursor()
                config_gravar(cur, 'marcador', novo.isoformat())
                conn.commit()
                print(f"✅ Canhotos: {r['viagens']} viagens · {r['ctes']} CT-e desde {r['desde']:%d/%m} "
                      f"(refresh do BI de {novo:%d/%m %H:%M})")
        except Exception as exc:
            print(f'⚠️  Canhotos: falha na coleta: {exc}')
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        _t.sleep(intervalo)


# ── o que o motorista vê ─────────────────────────────────────────────────────────────────────────────

ROTULO = {'cte': 'Canhoto do CT-e', 'nf': 'Canhoto da NF', 'parada': 'Comprovante da parada',
          'nf_extra': 'Outra NF', 'outro': 'Outro papel', 'solta': 'Foto sem viagem'}


def motorista(cur, cpf):
    cur.execute("SELECT nome, relacao FROM canhoto_motoristas WHERE cpf = %s", (cpf,))
    r = cur.fetchone()
    if r:
        return {'cpf': cpf, 'nome': r[0] or '', 'relacao': r[1]}
    cur.execute("SELECT motorista, relacao FROM canhoto_viagens WHERE cpf = %s LIMIT 1", (cpf,))
    r = cur.fetchone()
    return {'cpf': cpf, 'nome': r[0] or '', 'relacao': r[1]} if r else None


def estruturar(v, ctes, fotos, lote=None, exigidos=None):
    """Função pura: viagem + CT-e + fotos → paradas com itens e o estado de cada um.
    `fotos` = dicts com tipo, ctrc, parada, uid, quando, molde."""
    lote = lote or lote_min()
    exigidos = exigidos or itens_exigidos()
    paradas = {}
    for c in ctes:
        paradas.setdefault(c['parada'], []).append(c)
    por_item = {}
    extras = {}
    for f in fotos:
        if f['tipo'] in ('cte', 'nf') and f.get('ctrc'):
            por_item.setdefault(f"{'C' if f['tipo'] == 'cte' else 'N'}:{f['ctrc']}", []).append(f)
        elif f['tipo'] == 'parada' and f.get('parada'):
            por_item.setdefault(f"P:{f['parada']}", []).append(f)
        elif f['tipo'] in ('nf_extra', 'outro'):
            extras.setdefault(f.get('parada') or '', []).append(f)
    saida, total, feitos = [], 0, 0
    for chave, cs in paradas.items():
        cs = sorted(cs, key=lambda c: c['ctrc'])
        itens = []
        if len(cs) >= lote:
            itens.append({'id': f'P:{chave}', 'tipo': 'parada', 'parada': chave, 'ctrc': None,
                          'rotulo': ROTULO['parada'], 'num': None,
                          'sub': f'Vale para os {len(cs)} CT-e desta parada'})
        else:
            for c in cs:
                for t in exigidos:
                    if t == 'cte':
                        itens.append({'id': f"C:{c['ctrc']}", 'tipo': 'cte', 'ctrc': c['ctrc'], 'parada': chave,
                                      'rotulo': ROTULO['cte'], 'num': rotulo_cte(c.get('cte_num')) or c['ctrc']})
                    else:
                        itens.append({'id': f"N:{c['ctrc']}", 'tipo': 'nf', 'ctrc': c['ctrc'], 'parada': chave,
                                      'rotulo': ROTULO['nf'], 'num': rotulo_nf(c.get('nf')) or '—',
                                      'nf': c.get('nf')})
        for it in itens:
            fs = por_item.get(it['id']) or []
            it['fotos'] = [{'uid': f['uid'], 'quando': f.get('quando'), 'molde': f.get('molde')} for f in fs]
            it['estado'] = 'ok' if fs else None
            total += 1
            feitos += 1 if fs else 0
        c0 = cs[0]
        saida.append({'chave': chave, 'nome': c0.get('parada_nome') or c0.get('destinatario') or '',
                      'cidade': _cidade(c0.get('cidade'), c0.get('uf')), 'n_ctes': len(cs),
                      'ctrc_unico': c0['ctrc'] if len(cs) == 1 else None,
                      'itens': itens,
                      'extras': [{'uid': f['uid'], 'tipo': f['tipo'], 'rotulo': ROTULO.get(f['tipo'], f['tipo']),
                                  'quando': f.get('quando')} for f in extras.get(chave, [])]})
    return {'manifesto': v['manifesto'], 'rotulo': v['rotulo'],
            'emissao': v['emissao'].isoformat() if isinstance(v['emissao'], date) else v['emissao'],
            'origem': v.get('origem') or '', 'destinos': v.get('destinos') or '',
            'placa': v.get('placa_cavalo') or '', 'paradas': saida, 'total': total,
            'pendentes': total - feitos}


_COLS_V = ('manifesto', 'rotulo', 'emissao', 'cpf', 'motorista', 'relacao', 'placa_cavalo', 'placa_carreta',
           'origem', 'destinos', 'n_ctes')
_COLS_C = ('ctrc', 'manifesto', 'cte_num', 'nf', 'parada', 'parada_nome', 'destinatario', 'cidade', 'uf',
           'remetente', 'tipo_documento')


def _carregar(cur, onde, args):
    cur.execute(f"SELECT {', '.join(_COLS_V)} FROM canhoto_viagens WHERE {onde} "
                f"ORDER BY emissao DESC, rotulo DESC", args)
    vs = [dict(zip(_COLS_V, r)) for r in cur.fetchall()]
    if not vs:
        return [], {}, {}
    chaves = [v['manifesto'] for v in vs]
    cur.execute(f"SELECT {', '.join(_COLS_C)} FROM canhoto_ctes WHERE manifesto = ANY(%s)", (chaves,))
    cs = {}
    for r in cur.fetchall():
        d = dict(zip(_COLS_C, r))
        cs.setdefault(d['manifesto'], []).append(d)
    cur.execute("""SELECT uid::text, manifesto, ctrc, parada, tipo, molde,
                          to_char(recebida_em AT TIME ZONE 'America/Sao_Paulo', 'DD/MM HH24:MI')
                     FROM canhoto_fotos WHERE situacao = 'recebida' AND manifesto = ANY(%s)
                    ORDER BY recebida_em""", (chaves,))
    fs = {}
    for uid, man, ctrc, parada, tipo, molde, quando in cur.fetchall():
        fs.setdefault(man, []).append({'uid': uid, 'ctrc': ctrc, 'parada': parada, 'tipo': tipo,
                                       'molde': molde, 'quando': quando})
    return vs, cs, fs


def viagens_do_motorista(cur, cpf):
    vs, cs, fs = _carregar(cur, 'cpf = %s', (cpf,))
    return [estruturar(v, cs.get(v['manifesto'], []), fs.get(v['manifesto'], [])) for v in vs]


# ── foto ──────────────────────────────────────────────────────────────────────────────────────────────

TIPOS = {'cte', 'nf', 'nf_extra', 'parada', 'outro', 'solta'}
MOLDES = {'nf', 'cte', 'folha', 'outro', 'livre'}


class Recusa(Exception):
    """Pedido que não pode virar foto (a mensagem vai para o motorista)."""

    def __init__(self, msg, http=400):
        super().__init__(msg)
        self.http = http


def inspecionar(b):
    """Confere que é um JPEG de verdade e devolve (largura, altura). O app sempre manda JPEG."""
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(b))
        fmt = im.format
        w, h = im.size
        im.verify()
    except Exception:
        raise Recusa('O arquivo não é uma foto válida.')
    if fmt != 'JPEG':
        raise Recusa('A foto precisa vir em JPEG.')
    if w < 200 or h < 60 or w * h > 40_000_000:
        raise Recusa('O tamanho da foto não serve.')
    return w, h


def _resolver(cur, cpf, meta):
    """O item da foto, conferido contra a viagem DO PRÓPRIO CPF. Viagem que sumiu da lista (manifesto
    cancelado, janela passou) enquanto a foto esperava sinal não perde a foto: vira 'solta' e guarda a
    referência. Viagem de OUTRO motorista é recusa."""
    tipo = meta.get('tipo')
    if tipo not in TIPOS:
        raise Recusa('Tipo de foto desconhecido.')
    if tipo == 'solta':
        return 'solta', None, None, None, None, None
    man = norm_doc(meta.get('manifesto'))
    ref = '; '.join(f'{k}={meta.get(k)}' for k in ('tipo', 'manifesto', 'ctrc', 'parada') if meta.get(k))
    cur.execute("SELECT cpf FROM canhoto_viagens WHERE manifesto = %s", (man,))
    r = cur.fetchone()
    if not r:
        return 'solta', None, None, None, None, ref
    if r[0] != cpf:
        raise Recusa('Esta viagem não está no seu CPF.', 403)
    ctrc = ctrc_norm(meta.get('ctrc')) or None
    parada = str(meta.get('parada') or '') or None
    nf = None
    if tipo in ('cte', 'nf'):
        cur.execute("SELECT parada, nf FROM canhoto_ctes WHERE ctrc = %s AND manifesto = %s", (ctrc, man))
        c = cur.fetchone()
        if not c:
            return 'solta', None, None, None, None, ref
        parada, nf = c[0], (c[1] if tipo == 'nf' else None)
    elif parada:
        cur.execute("SELECT 1 FROM canhoto_ctes WHERE manifesto = %s AND parada = %s LIMIT 1", (man, parada))
        if not cur.fetchone():
            parada = None
    if ctrc and tipo not in ('cte', 'nf'):
        cur.execute("SELECT 1 FROM canhoto_ctes WHERE ctrc = %s AND manifesto = %s", (ctrc, man))
        if not cur.fetchone():
            ctrc = None
    return tipo, man, ctrc, parada, nf, None


def registrar_foto(conn, cpf, nome, meta, b, ip=None, agente=None):
    """Grava o arquivo e a linha. Mesma `uid` duas vezes (o celular reenviou depois de perder a
    resposta) devolve a primeira, sem duplicar. Devolve {'uid', 'repetida', 'tipo'}."""
    garantir(conn)
    try:
        uid = str(uuid.UUID(str(meta.get('uid'))))
    except (ValueError, TypeError):
        uid = str(uuid.uuid4())
    cur = conn.cursor()
    cur.execute("SELECT tipo FROM canhoto_fotos WHERE uid = %s", (uid,))
    r = cur.fetchone()
    if r:
        conn.commit()
        return {'uid': uid, 'repetida': True, 'tipo': r[0]}
    w, h = inspecionar(b)
    tipo, man, ctrc, parada, nf, ref = _resolver(cur, cpf, meta)
    molde = meta.get('molde') if meta.get('molde') in MOLDES else None
    tirada = _ts(meta.get('tirada_em'))
    agora = datetime.now()
    rel = os.path.join(f'{agora:%Y}', f'{agora:%m}', f'{uid}.jpg')
    destino = os.path.join(pasta(), rel)
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    tmp = destino + '.parcial'
    with open(tmp, 'wb') as fh:
        fh.write(b)
    os.replace(tmp, destino)
    try:
        cur.execute("""INSERT INTO canhoto_fotos (uid, arquivo, bytes, largura, altura, sha256, cpf, motorista,
                                                  manifesto, ctrc, parada, tipo, molde, nf_esperada, referencia,
                                                  tirada_em, ip, agente)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (uid, rel.replace(os.sep, '/'), len(b), w, h, hashlib.sha256(b).hexdigest(), cpf, nome,
                     man, ctrc, parada, tipo, molde, nf, ref, tirada, (ip or '')[:64], (agente or '')[:300]))
        conn.commit()
    except Exception:
        conn.rollback()
        cur = conn.cursor()
        cur.execute("SELECT tipo FROM canhoto_fotos WHERE uid = %s", (uid,))
        r = cur.fetchone()
        conn.commit()
        if r:                                   # corrida com o reenvio da mesma foto: ficou a outra
            return {'uid': uid, 'repetida': True, 'tipo': r[0]}
        try:
            os.remove(destino)
        except OSError:
            pass
        raise
    return {'uid': uid, 'repetida': False, 'tipo': tipo}


def caminho_foto(cur, uid):
    try:
        uid = str(uuid.UUID(str(uid)))
    except ValueError:
        return None
    cur.execute("SELECT arquivo FROM canhoto_fotos WHERE uid = %s", (uid,))
    r = cur.fetchone()
    if not r:
        return None
    base = os.path.realpath(pasta())
    p = os.path.realpath(os.path.join(base, r[0]))
    return p if p.startswith(base + os.sep) and os.path.isfile(p) else None


def miniatura(caminho, largura=420):
    from PIL import Image
    with Image.open(caminho) as im:
        im = im.convert('RGB')
        im.thumbnail((largura, largura * 3))
        out = io.BytesIO()
        im.save(out, 'JPEG', quality=78)
        return out.getvalue()


# ── escritório ────────────────────────────────────────────────────────────────────────────────────────

def painel(cur):
    """Uma linha por viagem da janela (o detalhe abre na gaveta) + as fotos sem viagem + o resumo."""
    vs, cs, fs = _carregar(cur, 'TRUE', ())
    linhas, itens, feitos, com_pend = [], 0, 0, 0
    for v in vs:
        e = estruturar(v, cs.get(v['manifesto'], []), fs.get(v['manifesto'], []))
        ultima = (fs.get(v['manifesto']) or [{}])[-1].get('quando')      # vem em ordem de chegada
        linhas.append({'manifesto': v['manifesto'], 'rotulo': v['rotulo'], 'emissao': e['emissao'],
                       'cpf': v['cpf'], 'motorista': v['motorista'] or '', 'relacao': v['relacao'],
                       'placa': v['placa_cavalo'], 'carreta': v['placa_carreta'], 'origem': v['origem'],
                       'destinos': v['destinos'], 'n_ctes': v['n_ctes'], 'total': e['total'],
                       'pendentes': e['pendentes'], 'fotos': len(fs.get(v['manifesto'], [])),
                       'ultima_foto': ultima,
                       'ctrcs': sorted(c['ctrc'] for c in cs.get(v['manifesto'], [])),
                       'nfs': sorted({c['nf'] for c in cs.get(v['manifesto'], []) if c.get('nf')})})
        itens += e['total']
        feitos += e['total'] - e['pendentes']
        com_pend += 1 if e['pendentes'] else 0
    cur.execute("""SELECT uid::text, cpf, motorista, tipo, molde, referencia,
                          to_char(recebida_em AT TIME ZONE 'America/Sao_Paulo', 'DD/MM/YYYY HH24:MI')
                     FROM canhoto_fotos WHERE situacao = 'recebida' AND tipo = 'solta'
                    ORDER BY recebida_em DESC LIMIT 300""")
    soltas = [{'uid': u, 'cpf': c, 'motorista': n or '', 'tipo': t, 'molde': mo, 'referencia': ref, 'quando': q}
              for u, c, n, t, mo, ref, q in cur.fetchall()]
    cur.execute("""SELECT count(*) FILTER (WHERE (recebida_em AT TIME ZONE 'America/Sao_Paulo')::date
                                                 = (NOW() AT TIME ZONE 'America/Sao_Paulo')::date),
                          count(*) FILTER (WHERE situacao = 'recebida')
                     FROM canhoto_fotos""")
    hoje, todas = cur.fetchone()
    cur.execute("SELECT chave, valor FROM canhoto_config")
    cfg = dict(cur.fetchall())
    return {'viagens': linhas, 'soltas': soltas,
            'resumo': {'viagens': len(vs), 'com_pendencia': com_pend, 'itens': itens, 'recebidos': feitos,
                       'fotos_hoje': hoje, 'fotos': todas, 'sem_viagem': len(soltas)},
            'ultima_coleta': cfg.get('ultima_coleta'), 'desde': cfg.get('janela_desde') or cfg.get('desde'),
            'janela_dias': janela_dias(), 'lote_min': lote_min(), 'itens_exigidos': list(itens_exigidos())}


def detalhe(cur, manifesto):
    vs, cs, fs = _carregar(cur, 'manifesto = %s', (norm_doc(manifesto),))
    if not vs:
        return None
    v = vs[0]
    e = estruturar(v, cs.get(v['manifesto'], []), fs.get(v['manifesto'], []))
    e.update({'motorista': v['motorista'], 'relacao': v['relacao'], 'carreta': v['placa_carreta'],
              'n_ctes': v['n_ctes']})
    return e


def descartar(conn, uid, usuario, motivo):
    cur = conn.cursor()
    cur.execute("""UPDATE canhoto_fotos SET situacao = 'descartada', descartada_por = %s, descartada_em = NOW(),
                          motivo = %s
                    WHERE uid::text = %s AND situacao = 'recebida'""", (usuario, (motivo or '')[:300], str(uid)))
    n = cur.rowcount
    conn.commit()
    return n


def ligar(conn, uid, ctrc, tipo, usuario):
    """Foto sem viagem → o CT-e certo, como canhoto do CT-e, da NF ou outro papel. Só liga CT-e que está
    na janela: é o que faz o item sair da lista do motorista."""
    if tipo not in ('cte', 'nf', 'outro'):
        raise Recusa('Escolha se a foto é do CT-e, da NF ou outro papel.')
    ctrc = ctrc_norm(ctrc)
    cur = conn.cursor()
    cur.execute("SELECT manifesto, parada FROM canhoto_ctes WHERE ctrc = %s", (ctrc,))
    r = cur.fetchone()
    if not r:
        raise Recusa(f'O CTRC {ctrc or "(vazio)"} não está entre as viagens do app.', 404)
    man, parada = r
    cur.execute("""UPDATE canhoto_fotos SET tipo = %s, manifesto = %s, ctrc = %s, parada = %s,
                          ligada_por = %s, ligada_em = NOW()
                    WHERE uid::text = %s AND tipo = 'solta' AND situacao = 'recebida'""",
                (tipo, man, ctrc, parada, usuario, str(uid)))
    n = cur.rowcount
    conn.commit()
    if not n:
        raise Recusa('A foto já foi ligada ou descartada.', 409)
    return {'manifesto': man, 'ctrc': ctrc}
