# -*- coding: utf-8 -*-
"""COMPROVANTES DE ENTREGA — o saldo do CTRB só sai com o comprovante de todos os CT-e (17/11/2026).

Pedido do usuário: o saldo do agregado/terceiro sai às quintas e depende de o comprovante de entrega estar no
SSW. Hoje alguém confere à mão; o objetivo é liberar (ou segurar) o saldo sozinho. Vai em etapas:

  1. (esta) a REGRA de cada cliente e a tela: o que cada saldo vai exigir e a situação de cada CT-e;
  2. o robô no servidor do SSW lê, por CTRB, se cada CT-e tem comprovante (073 → 023 → 101) e manda a imagem;
  3. a imagem é lida às cegas (Claude, em lote, de noite) e comparada no código com o CT-e e a NF;
  4. a liberação do saldo pela regra.

A regra é do CLIENTE (o tomador/pagador do CT-e), não do CTRB: um CTRB leva CT-e de vários clientes. O cliente
é o mesmo do Faturamento — a raiz do CNPJ (8 dígitos), com os grupos econômicos juntos (`GRUPOS_ECONOMICOS` do
server.py, passado aqui como `grupos`). Decisão do usuário em 17/11/2026: todo cliente entra exigindo o canhoto
da NF + o DACTE assinado; só o Martins não exige. Cliente novo é criado sozinho com a regra padrão, na primeira
vez que aparece num CTRB, e o financeiro muda se precisar (com histórico de quem e quando).

Puro onde dá: `painel`/`situacao` recebem o que foi lido e devolvem o que a tela mostra.
"""
import base64
import hashlib
import hmac
import os
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

EXIGE = {
    'nf_cte': 'Canhoto da NF + DACTE',
    'nf': 'Só o canhoto da NF',
    'nenhum': 'Não exige',
}
PADRAO = 'nf_cte'
# Martins (raiz principal do grupo; a outra raiz cai nesta pelo GRUPOS_ECONOMICOS) — decisão do usuário, 17/11/2026
SEMENTE = {'18485037': ('nenhum', 'Não precisa de comprovante (definido na implantação).')}
SEM_ENTREGA = {'COMPLEMENTAR FRETE'}          # complemento de frete não tem entrega própria

# situação de cada CT-e: (nível do sinal, texto)
STATUS = {
    'sem_comprovante': ('vermelho', 'Entregue no SSW, sem comprovante'),
    'nao_entregue': ('vermelho', 'Não entregue no SSW'),
    'erro_leitura': ('cinza', 'O robô não conseguiu ler no SSW'),
    'divergente': ('amarelo', 'O comprovante não bate com o CT-e'),
    'ilegivel': ('amarelo', 'Imagem ilegível — conferir à mão'),
    'sem_leitura': ('cinza', 'Ainda não lido no SSW'),
    'aguardando_ia': ('cinza', 'Comprovante no SSW — falta conferir a imagem'),
    'ok': ('verde', 'Comprovante conferido'),
    'nao_exige': ('verde', 'Cliente não exige'),
    'sem_entrega': ('verde', 'Complemento de frete — sem entrega'),
}
# situação do CTRB (a pior dos CT-e que exigem comprovante)
GERAL = {
    'falta': ('vermelho', 'Falta comprovante'),
    'divergente': ('amarelo', 'Comprovante não bate'),
    'aguardando': ('cinza', 'Aguardando leitura'),
    'completo': ('verde', 'Comprovantes completos'),
    'nao_exige': ('verde', 'Não exige comprovante'),
    'sem_cte': ('info', 'Sem CT-e'),
    'fora_bi': ('info', 'CTRB fora do BI'),
}
ORDEM_GERAL = ['falta', 'divergente', 'aguardando', 'sem_cte', 'fora_bi', 'completo', 'nao_exige']


def _doc(s):
    return re.sub(r'[^A-Z0-9]', '', str(s or '').upper())


def chave_cliente(cnpj, nome, grupos=None):
    """A chave do cliente como no Faturamento: raiz do CNPJ já no grupo econômico; sem CNPJ, o nome."""
    d = re.sub(r'\D', '', str(cnpj or ''))
    if len(d) >= 8:
        r = d[:8]
        return (grupos or {}).get(r, r)
    nome = str(nome or '').strip().upper()
    return f'nome::{nome}' if nome else None


def raiz_fmt(chave):
    if not chave or chave.startswith('nome::'):
        return 'sem CNPJ'
    return f'{chave[:2]}.{chave[2:5]}.{chave[5:8]}'


def cte_fiscal(chave):
    """Chave de 44 dígitos → (série, número) como estão impressos no DACTE: posições 23–25 e 26–34."""
    d = re.sub(r'\D', '', str(chave or ''))
    if len(d) != 44:
        return None, None
    return str(int(d[22:25])), str(int(d[25:34]))


# ── banco ────────────────────────────────────────────────────────────────────────────────────────────
DDL = """
CREATE TABLE IF NOT EXISTS comprov_regras (
    raiz            TEXT PRIMARY KEY,           -- raiz do CNPJ do tomador (já no grupo econômico) ou 'nome::…'
    nome            TEXT,
    exige           TEXT NOT NULL,              -- nf_cte | nf | nenhum
    obs             TEXT,
    criado_em       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    criado_por      TEXT,
    atualizado_em   TIMESTAMPTZ,
    atualizado_por  TEXT
);
CREATE TABLE IF NOT EXISTS comprov_regras_hist (
    id          SERIAL PRIMARY KEY,
    raiz        TEXT NOT NULL,
    de          TEXT,
    para        TEXT NOT NULL,
    obs         TEXT,
    usuario     TEXT,
    em          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_comprov_regras_hist ON comprov_regras_hist (raiz, em DESC);
-- o que o robô leu no SSW, por CT-e (etapa 2): o comprovante é do CT-e, não do CTRB — o mesmo CT-e em dois
-- CTRBs (trecho e entrega) usa a mesma leitura
CREATE TABLE IF NOT EXISTS comprov_cte (
    ctrc            TEXT PRIMARY KEY,           -- CT-e do SSW sem pontuação ('UDI0012345')
    ctrb            TEXT,                       -- o CTRB em que o robô o encontrou
    tem_comprovante BOOLEAN NOT NULL,           -- o link "Comprovante" da tela 101 está ativo
    entrega_em      DATE,                       -- "ENTREGA REALIZADA EM dd/mm/aa" (a baixa no SSW)
    baixa_texto     TEXT,
    imagens         TEXT[] NOT NULL DEFAULT '{}',   -- sha256 das imagens guardadas
    lido_em         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    veredito        TEXT,                       -- ok | divergente | ilegivel (a conferência da imagem, etapa 3)
    motivo          TEXT,
    conferido_em    TIMESTAMPTZ
);
-- etapa 2 (17/11/2026): o que a tela 101 diz da entrega. O teste de 20 CTRBs mostrou que quem não tem
-- comprovante quase sempre nem foi entregue no SSW ("82-SAIDA DE UNIDADE") — a tela separa os dois casos
ALTER TABLE comprov_cte ADD COLUMN IF NOT EXISTS situacao_atual TEXT;   -- '1-MERCADORIA ENTREGUE', '82-SAIDA DE UNIDADE'
ALTER TABLE comprov_cte ADD COLUMN IF NOT EXISTS situacao_em DATE;
ALTER TABLE comprov_cte ADD COLUMN IF NOT EXISTS baixa_tipo TEXT;       -- scan (SSWScan) | manual (opção 038)
ALTER TABLE comprov_cte ADD COLUMN IF NOT EXISTS recebido_por TEXT;     -- da baixa manual ("CARIMBO", "RUBRICA", nome)
ALTER TABLE comprov_cte ADD COLUMN IF NOT EXISTS erro TEXT;             -- o robô não achou o CT-e ou a tela não abriu
-- cada imagem uma vez só (o mesmo carimbo pode valer para vários CT-e); o arquivo fica em pasta_imagens()
CREATE TABLE IF NOT EXISTS comprov_imagem (
    sha256      TEXT PRIMARY KEY,
    bytes       INTEGER NOT NULL,
    arquivo     TEXT NOT NULL,                  -- relativo a pasta_imagens(): ab/abcdef….jpg
    recebida_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    leitura     JSONB,                          -- o que a IA leu, às cegas (etapa 3)
    modelo      TEXT,
    lida_em     TIMESTAMPTZ
);
"""


# ── o robô (etapa 2) ─────────────────────────────────────────────────────────────────────────────────
RELER_HORAS = 6                 # CT-e sem comprovante é relido no máximo a cada 6 h
MAX_IMAGEM = 5 * 1024 * 1024    # a do SSW tem ~70 KB (600×850); 5 MB é folga para scan colorido


def pasta_imagens():
    """COMPROV_DIR; senão uma subpasta do volume dos canhotos (já montado em produção); senão ao lado do código."""
    if os.getenv('COMPROV_DIR'):
        return os.getenv('COMPROV_DIR')
    if os.getenv('CANHOTO_DIR'):
        return os.path.join(os.getenv('CANHOTO_DIR'), 'ssw')
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'comprovantes_ssw')


def chave_robo_ok(recebida):
    """O robô se identifica pelo cabeçalho X-Robo-Chave = COMPROV_ROBO_CHAVE. Sem a variável, ninguém entra."""
    esperada = os.getenv('COMPROV_ROBO_CHAVE') or ''
    return len(esperada) >= 32 and hmac.compare_digest(str(recebida or ''), esperada)


def _data_br(s):
    m = re.fullmatch(r'(\d{2})/(\d{2})/(\d{2,4})', str(s or '').strip())
    if not m:
        return None
    a = int(m.group(3))
    try:
        return date(a + 2000 if a < 100 else a, int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def pendentes(pacote, regs, leit, grupos=None, quinta=None, agora=None):
    """O que o robô tem de ler: os CTRBs com saldo programado e os CT-e deles que exigem comprovante e ainda não
    têm imagem. Primeiro os da quinta (e os parados), depois os das próximas. CT-e sem comprovante é relido a cada
    RELER_HORAS; com imagem guardada, nunca mais."""
    agora = agora or datetime.now(timezone.utc)
    lista, vistos = [], set()
    for f in sorted((f for f in pacote['fila'] if f['tipo_pag'] == 'saldo'),
                    key=lambda f: (0 if (not f.get('lote') or not quinta or f['lote'] <= quinta) else 1,
                                   f.get('lote') or f.get('venc') or '')):
        k = f['ctrb']
        c = pacote['ctrbs'].get(k)
        if k in vistos or not c:
            continue
        vistos.add(k)
        ler = []
        for ce in c.get('ctes') or []:
            e = pacote['ctes'].get(ce) or {}
            if status_cte(e, _exige_de(chave_cliente(e.get('cnpj'), e.get('pag'), grupos), regs), None) != 'sem_leitura':
                continue                        # não exige ou não tem entrega
            lt = leit.get(_doc(e.get('cte') or ce))
            if lt and lt.get('tem_comprovante') and lt.get('n_imagens'):
                continue
            if lt and lt.get('lido_em') and not lt.get('erro'):   # o que o robô não conseguiu ler volta na hora
                if agora - datetime.fromisoformat(lt['lido_em']) < timedelta(hours=RELER_HORAS):
                    continue
            ler.append(e.get('cte') or ce)
        if ler:
            lista.append({'ctrb': k, 'ctrcs': ler, 'n_ctes': len(c.get('ctes') or []), 'lote': f.get('lote')})
    return lista


def gravar_leitura(cur, d, pasta=None):
    """Grava o que o robô leu de UM CT-e. As imagens chegam em base64; o sha256 é recalculado aqui (o nome do
    arquivo é o próprio hash: a mesma imagem em dois CT-e é guardada uma vez). Imagem nova ou trocada zera a
    conferência da IA, que roda de novo."""
    ctrc = _doc(d.get('ctrc'))
    if not re.fullmatch(r'[A-Z]{3}\d{7}', ctrc):
        raise ValueError('CT-e inválido.')
    ctrb = (d.get('ctrb') or '').strip().upper()[:12] or None
    pasta = pasta or pasta_imagens()
    shas = []
    for im in d.get('imagens') or []:
        try:
            b = base64.b64decode(im.get('b64') or '', validate=True)
        except Exception:
            raise ValueError('Imagem em base64 inválida.')
        if not b.startswith(b'\xff\xd8') or len(b) > MAX_IMAGEM:
            raise ValueError('A imagem não é um JPEG ou passou de 5 MB.')
        sha = hashlib.sha256(b).hexdigest()
        if im.get('sha') and im['sha'] != sha:
            raise ValueError('O hash da imagem não confere (chegou corrompida).')
        rel = f'{sha[:2]}/{sha}.jpg'
        destino = os.path.join(pasta, rel)
        if not os.path.exists(destino):
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            tmp = destino + '.tmp'
            with open(tmp, 'wb') as fh:
                fh.write(b)
            os.replace(tmp, destino)
        cur.execute("""INSERT INTO comprov_imagem (sha256, bytes, arquivo) VALUES (%s, %s, %s)
                       ON CONFLICT (sha256) DO NOTHING""", (sha, len(b), rel))
        if sha not in shas:
            shas.append(sha)
    tem = bool(d.get('tem_comprovante'))
    erro = (d.get('erro') or '').strip()[:300] or None
    if tem and not shas and not erro:
        erro = 'o link do comprovante está ativo, mas a imagem não veio'
    baixa = d.get('baixa_tipo') if d.get('baixa_tipo') in ('scan', 'manual') else None
    cur.execute("""
        INSERT INTO comprov_cte (ctrc, ctrb, tem_comprovante, entrega_em, baixa_texto, imagens, lido_em,
                                 situacao_atual, situacao_em, baixa_tipo, recebido_por, erro)
        VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s)
        ON CONFLICT (ctrc) DO UPDATE SET
            ctrb = EXCLUDED.ctrb, tem_comprovante = EXCLUDED.tem_comprovante, entrega_em = EXCLUDED.entrega_em,
            baixa_texto = EXCLUDED.baixa_texto, lido_em = NOW(), situacao_atual = EXCLUDED.situacao_atual,
            situacao_em = EXCLUDED.situacao_em, baixa_tipo = EXCLUDED.baixa_tipo, recebido_por = EXCLUDED.recebido_por,
            erro = EXCLUDED.erro,
            veredito = CASE WHEN comprov_cte.imagens = EXCLUDED.imagens THEN comprov_cte.veredito END,
            motivo = CASE WHEN comprov_cte.imagens = EXCLUDED.imagens THEN comprov_cte.motivo END,
            conferido_em = CASE WHEN comprov_cte.imagens = EXCLUDED.imagens THEN comprov_cte.conferido_em END,
            imagens = EXCLUDED.imagens""",
                (ctrc, ctrb, tem, _data_br(d.get('entrega_em')), (d.get('baixa_texto') or '')[:400] or None, shas,
                 (d.get('situacao_atual') or '').strip()[:60] or None, _data_br(d.get('situacao_em')), baixa,
                 (d.get('recebido_por') or '').strip()[:80] or None, erro))
    return {'ctrc': ctrc, 'imagens': len(shas), 'tem_comprovante': tem}


def arquivo_imagem(cur, sha):
    """Caminho do arquivo da imagem (para a tela), ou None."""
    if not re.fullmatch(r'[0-9a-f]{64}', sha or ''):
        return None
    cur.execute("SELECT arquivo FROM comprov_imagem WHERE sha256 = %s", (sha,))
    r = cur.fetchone()
    return os.path.join(pasta_imagens(), r[0]) if r else None


def garantir(cur):
    cur.execute(DDL)
    for raiz, (exige, obs) in SEMENTE.items():
        cur.execute("""INSERT INTO comprov_regras (raiz, exige, obs, criado_por) VALUES (%s, %s, %s, 'implantação')
                       ON CONFLICT (raiz) DO NOTHING""", (raiz, exige, obs))


def regras(cur):
    cur.execute("""SELECT raiz, nome, exige, obs, criado_em, criado_por, atualizado_em, atualizado_por
                     FROM comprov_regras""")
    return {r[0]: {'raiz': r[0], 'nome': r[1], 'exige': r[2], 'obs': r[3],
                   'criado_em': r[4].isoformat() if r[4] else None, 'criado_por': r[5],
                   'atualizado_em': r[6].isoformat() if r[6] else None, 'atualizado_por': r[7]}
            for r in cur.fetchall()}


def historico(cur, raiz=None, limite=400):
    if raiz:
        cur.execute("""SELECT raiz, de, para, obs, usuario, em FROM comprov_regras_hist WHERE raiz = %s
                        ORDER BY em DESC, id DESC""", (raiz,))
    else:
        cur.execute("""SELECT raiz, de, para, obs, usuario, em FROM comprov_regras_hist
                        ORDER BY em DESC, id DESC LIMIT %s""", (limite,))
    return [{'raiz': r[0], 'de': r[1], 'de_texto': EXIGE.get(r[1], r[1] or '—'), 'para': r[2],
             'para_texto': EXIGE.get(r[2], r[2]), 'obs': r[3], 'usuario': r[4], 'em': r[5].isoformat() if r[5] else None}
            for r in cur.fetchall()]


def sincronizar(cur, clientes, existentes):
    """Cria com a regra padrão o cliente que apareceu num CTRB e ainda não tem regra; completa o nome."""
    novos = [(c['raiz'], c['nome'], PADRAO) for c in clientes if c['raiz'] not in existentes]
    for raiz, nome, exige in novos:
        cur.execute("""INSERT INTO comprov_regras (raiz, nome, exige, criado_por) VALUES (%s, %s, %s, 'automático')
                       ON CONFLICT (raiz) DO NOTHING""", (raiz, nome, exige))
    sem_nome = [(c['nome'], c['raiz']) for c in clientes
                if c['raiz'] in existentes and not existentes[c['raiz']].get('nome') and c['nome']]
    for nome, raiz in sem_nome:
        cur.execute("UPDATE comprov_regras SET nome = %s WHERE raiz = %s AND nome IS NULL", (nome, raiz))
    return len(novos)


def definir(cur, raiz, exige, obs, usuario, nome=None):
    """Muda a regra de um cliente, com histórico. Tirar a exigência pede o motivo (é o que deixa o saldo sair
    sem comprovante)."""
    raiz = str(raiz or '').strip()
    if exige not in EXIGE:
        raise ValueError('Regra inválida.')
    if not (re.fullmatch(r'\d{8}', raiz) or raiz.startswith('nome::')):
        raise ValueError('Cliente inválido.')
    obs = (obs or '').strip()[:400] or None
    if exige == 'nenhum' and not obs:
        raise ValueError('Diga em uma linha por que este cliente não precisa de comprovante (fica no histórico).')
    cur.execute("SELECT exige FROM comprov_regras WHERE raiz = %s", (raiz,))
    r = cur.fetchone()
    de = r[0] if r else None
    if de == exige and not obs:
        return False
    if r:
        cur.execute("""UPDATE comprov_regras SET exige = %s, obs = COALESCE(%s, obs), atualizado_em = NOW(),
                              atualizado_por = %s WHERE raiz = %s""", (exige, obs, usuario, raiz))
    else:
        cur.execute("""INSERT INTO comprov_regras (raiz, nome, exige, obs, criado_por, atualizado_em, atualizado_por)
                       VALUES (%s, %s, %s, %s, %s, NOW(), %s)""", (raiz, nome, exige, obs, usuario, usuario))
    cur.execute("INSERT INTO comprov_regras_hist (raiz, de, para, obs, usuario) VALUES (%s, %s, %s, %s, %s)",
                (raiz, de, exige, obs, usuario))
    return True


def leituras(cur, ctrcs):
    """O que o robô leu no SSW para esses CT-e (chave = CT-e sem pontuação)."""
    ks = sorted({_doc(c) for c in ctrcs if c})
    if not ks:
        return {}
    cur.execute("""SELECT ctrc, ctrb, tem_comprovante, entrega_em, baixa_texto, cardinality(imagens), lido_em,
                          veredito, motivo, conferido_em, situacao_atual, situacao_em, baixa_tipo, recebido_por,
                          erro, imagens
                     FROM comprov_cte WHERE ctrc = ANY(%s)""", (ks,))
    return {r[0]: {'ctrb': r[1], 'tem_comprovante': r[2], 'entrega_em': r[3].isoformat() if r[3] else None,
                   'baixa_texto': r[4], 'n_imagens': r[5], 'lido_em': r[6].isoformat() if r[6] else None,
                   'veredito': r[7], 'motivo': r[8], 'conferido_em': r[9].isoformat() if r[9] else None,
                   'situacao_atual': r[10], 'situacao_em': r[11].isoformat() if r[11] else None,
                   'baixa_tipo': r[12], 'recebido_por': r[13], 'erro': r[14], 'imagens': list(r[15] or [])}
            for r in cur.fetchall()}


# ── a regra aplicada ─────────────────────────────────────────────────────────────────────────────────
def _exige_de(chave, regs):
    r = regs.get(chave)
    return r['exige'] if r and r.get('exige') in EXIGE else PADRAO


def entregue(leitura):
    """Baixado como entregue no SSW: a última ocorrência é "1-MERCADORIA ENTREGUE" ou a 101 traz a frase da baixa
    (a última ocorrência pode ser outra depois da entrega, ex. "6-INFORMACAO COMPLEMENTAR")."""
    return ((leitura.get('situacao_atual') or '').startswith('1-') or bool(leitura.get('baixa_tipo'))
            or bool(leitura.get('entrega_em')))


def status_cte(info, exige, leitura):
    """A situação de UM CT-e, dada a regra do cliente e o que o robô leu."""
    if (info.get('tipo') or '').upper() in SEM_ENTREGA:
        return 'sem_entrega'
    if exige == 'nenhum':
        return 'nao_exige'
    if not leitura:
        return 'sem_leitura'
    if not leitura.get('tem_comprovante'):
        if leitura.get('erro') and not leitura.get('situacao_atual'):
            return 'erro_leitura'
        return 'sem_comprovante' if entregue(leitura) else 'nao_entregue'
    if leitura.get('erro') and not leitura.get('n_imagens'):
        return 'erro_leitura'
    v = leitura.get('veredito')
    if v in ('ok', 'divergente', 'ilegivel'):
        return v
    return 'aguardando_ia'


def situacao(ctrb, pacote, regs, leit, grupos=None):
    """A situação do comprovante de um CTRB: cada CT-e com a regra do cliente, e o resumo."""
    c = pacote['ctrbs'].get(ctrb)
    if not c:
        g = 'fora_bi'
        return {'geral': g, 'nivel': GERAL[g][0], 'texto': 'CTRB ainda fora do 073 do BI — sem a lista de CT-e',
                'ctes': [], 'n': 0, 'exigidos': 0, 'ok': 0, 'faltam': 0}
    itens = []
    for ce in c.get('ctes') or []:
        e = pacote['ctes'].get(ce) or {'cte': ce}
        ch = chave_cliente(e.get('cnpj'), e.get('pag'), grupos)
        ex = _exige_de(ch, regs)
        lt = leit.get(_doc(e.get('cte') or ce))
        st = status_cte(e, ex, lt)
        itens.append({'ctrc': e.get('cte') or ce, 'serie': e.get('serie'), 'num': e.get('num'), 'nf': e.get('nf'),
                      'tipo': e.get('tipo'), 'cliente': e.get('pag'), 'raiz': ch, 'exige': ex, 'exige_texto': EXIGE[ex],
                      'status': st, 'nivel': STATUS[st][0], 'status_texto': STATUS[st][1], 'leitura': lt,
                      'n_ctrbs': e.get('n_ctrbs') or 1})
    exig = [i for i in itens if i['status'] not in ('nao_exige', 'sem_entrega')]
    cnt = Counter(i['status'] for i in exig)
    falta = cnt['sem_comprovante'] + cnt['nao_entregue']
    ok = cnt['ok']
    if not itens:
        g, txt = 'sem_cte', 'Sem CT-e no manifesto (diária ou sem manifesto) — não há comprovante a cobrar'
    elif not exig:
        g, txt = 'nao_exige', ('O cliente não exige comprovante' if len({i['raiz'] for i in itens}) == 1
                               else 'Nenhum cliente deste CTRB exige comprovante')
    elif falta:
        ne = cnt['nao_entregue']
        g, txt = 'falta', (f'Falta comprovante em {falta} de {len(exig)} CT-e'
                           + (f" ({ne} nem {'entregues' if ne > 1 else 'entregue'} no SSW)" if ne else ''))
    elif cnt['divergente'] or cnt['ilegivel']:
        nd = cnt['divergente'] + cnt['ilegivel']
        g, txt = 'divergente', f"{nd} de {len(exig)} comprovantes não {'batem' if nd > 1 else 'bate'} com o CT-e — conferir"
    elif cnt['sem_leitura'] or cnt['aguardando_ia'] or cnt['erro_leitura']:
        g = 'aguardando'
        if cnt['sem_leitura'] == len(exig):
            txt = f"{len(exig)} CT-e {'exigem' if len(exig) > 1 else 'exige'} comprovante — o SSW ainda não foi lido"
        else:
            partes = [f"{cnt['aguardando_ia'] + ok} de {len(exig)} CT-e com comprovante no SSW"]
            if cnt['aguardando_ia']:
                partes.append('falta conferir a imagem')
            if cnt['sem_leitura']:
                partes.append(f"{cnt['sem_leitura']} ainda não lido{'s' if cnt['sem_leitura'] > 1 else ''}")
            if cnt['erro_leitura']:
                partes.append(f"{cnt['erro_leitura']} o robô não conseguiu ler")
            txt = ' · '.join(partes)
    else:
        g, txt = 'completo', f'{ok} de {len(exig)} CT-e com comprovante conferido'
    return {'geral': g, 'nivel': GERAL[g][0], 'texto': txt, 'ctes': itens, 'n': len(itens), 'exigidos': len(exig),
            'ok': ok, 'faltam': falta, 'regras': sorted({i['exige'] for i in exig})}


def clientes(pacote, grupos=None):
    """Os clientes que aparecem nos CTRBs pagos (agregado/terceiro) da conferência, na régua do Faturamento:
    raiz do CNPJ no grupo econômico, nome mais frequente por CT-e. Com quantos CT-e, CTRBs e saldos programados."""
    nomes = defaultdict(Counter)
    ctes_de, ctrbs_de = defaultdict(set), defaultdict(set)
    for k, c in pacote['ctrbs'].items():
        if c.get('tipo') == 'FROTA' or not c.get('ativo'):
            continue
        for ce in c.get('ctes') or []:
            e = pacote['ctes'].get(ce)
            if not e:
                continue
            ch = chave_cliente(e.get('cnpj'), e.get('pag'), grupos)
            if not ch:
                continue
            nomes[ch][(e.get('pag') or '').strip() or '(sem nome)'] += 1
            ctes_de[ch].add(ce)
            ctrbs_de[ch].add(k)
    saldo_n, saldo_v = Counter(), Counter()
    for f in pacote['fila']:
        if f['tipo_pag'] != 'saldo':
            continue
        c = pacote['ctrbs'].get(f['ctrb']) or {}
        chs = {chave_cliente((pacote['ctes'].get(ce) or {}).get('cnpj'), (pacote['ctes'].get(ce) or {}).get('pag'), grupos)
               for ce in c.get('ctes') or []} - {None}
        for ch in chs:
            saldo_n[ch] += 1
            saldo_v[ch] += f['valor']
    return sorted(({'raiz': ch, 'nome': nomes[ch].most_common(1)[0][0], 'ctes': len(ctes_de[ch]),
                    'ctrbs': len(ctrbs_de[ch]), 'saldos': saldo_n[ch], 'saldos_valor': round(saldo_v[ch], 2)}
                   for ch in ctes_de), key=lambda x: -x['ctes'])


def painel(pacote, regs, leit, hist, grupos=None, hoje=None, quinta=None, cl=None):
    """A aba Comprovantes: os saldos programados com a situação do comprovante, e as regras por cliente.
    `cl`: o `clientes(pacote, grupos)` já calculado (a rota usa a lista antes para criar os clientes novos)."""
    saldos = []
    for f in pacote['fila']:
        if f['tipo_pag'] != 'saldo':
            continue
        s = situacao(f['ctrb'], pacote, regs, leit, grupos)
        cli = Counter(i['cliente'] or '—' for i in s['ctes'])
        saldos.append({'ctrb': f['ctrb'], 'valor': f['valor'], 'lote': f.get('lote'), 'venc': f.get('venc'),
                       'data': f.get('data'), 'aprov': f.get('aprov'), 'adiado': f.get('adiado'),
                       'favorecido': f.get('favorecido'), 'tipo': f.get('tipo'), 'o': f.get('o'), 'd': f.get('d'),
                       'lanc': f.get('lanc'), 'geral': s['geral'], 'nivel': s['nivel'], 'texto': s['texto'],
                       'n': s['n'], 'exigidos': s['exigidos'], 'ok': s['ok'], 'faltam': s['faltam'],
                       'regras': s.get('regras') or [], 'clientes': [n for n, _ in cli.most_common(3)],
                       'n_clientes': len(cli)})
    ordem = {g: i for i, g in enumerate(ORDEM_GERAL)}
    saldos.sort(key=lambda x: (x['lote'] or '0000', ordem[x['geral']], -x['valor']))
    cl = cl if cl is not None else clientes(pacote, grupos)
    ult = {}
    for h in hist:
        ult.setdefault(h['raiz'], h)
    lista = []
    vistos = set()
    for c in cl:
        r = regs.get(c['raiz']) or {}
        vistos.add(c['raiz'])
        lista.append({**c, 'exige': _exige_de(c['raiz'], regs), 'raiz_fmt': raiz_fmt(c['raiz']), 'obs': r.get('obs'),
                      'criado_em': r.get('criado_em'), 'criado_por': r.get('criado_por'),
                      'atualizado_em': r.get('atualizado_em'), 'atualizado_por': r.get('atualizado_por'),
                      'ultima': ult.get(c['raiz'])})
    for raiz, r in regs.items():           # regra cadastrada de cliente que não está em CTRB nenhum do período
        if raiz not in vistos:
            lista.append({'raiz': raiz, 'nome': r.get('nome') or raiz_fmt(raiz), 'ctes': 0, 'ctrbs': 0, 'saldos': 0,
                          'saldos_valor': 0, 'exige': r['exige'], 'raiz_fmt': raiz_fmt(raiz), 'obs': r.get('obs'),
                          'criado_em': r.get('criado_em'), 'criado_por': r.get('criado_por'),
                          'atualizado_em': r.get('atualizado_em'), 'atualizado_por': r.get('atualizado_por'),
                          'ultima': ult.get(raiz)})
    tot_ctes = sum(c['ctes'] for c in lista) or 1
    acum = 0
    for c in lista:
        acum += c['ctes']
        c['pct'] = round(100 * c['ctes'] / tot_ctes, 1)
        c['pct_acum'] = round(100 * acum / tot_ctes, 1)
    lote = [s for s in saldos if s['lote'] and (not hoje or s['lote'] >= hoje) and (not quinta or s['lote'] <= quinta)]
    resumo = {g: {'n': sum(1 for s in lote if s['geral'] == g), 'valor': round(sum(s['valor'] for s in lote if s['geral'] == g), 2)}
              for g in GERAL}
    resumo['lote'] = {'n': len(lote), 'valor': round(sum(s['valor'] for s in lote), 2),
                      'ctes_exigidos': sum(s['exigidos'] for s in lote)}
    return {'saldos': saldos, 'clientes': lista, 'resumo': resumo, 'exige': EXIGE, 'padrao': PADRAO,
            'status': {k: {'nivel': v[0], 'texto': v[1]} for k, v in STATUS.items()},
            'geral': {k: {'nivel': v[0], 'texto': v[1]} for k, v in GERAL.items()},
            'leituras': len(leit), 'robo_ligado': bool(leit),
            'ultima_leitura': max((l['lido_em'] for l in leit.values() if l.get('lido_em')), default=None)}
