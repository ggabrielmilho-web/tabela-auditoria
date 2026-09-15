# -*- coding: utf-8 -*-
"""Ordem de coleta (SSW 157) ligada à carga — dono (embarcador) e ponto físico (local).

Fase A do desenho da `Programada` (15/09/26): a carga continua nascendo do manifesto, exatamente
como hoje; este passo só ACRESCENTA, depois de criada, o que a ordem de coleta e o CTe sabem
e a carga não tinha:

    coleta_origem     unidade-numero da ordem de coleta (chave do 157)
    coleta_via        como se achou: 'cte' (ctrc_gerado → primeiro_manifesto, exata) ou 'placa'
                      (cavalo + janela de data, reserva — 10 discordâncias em 142 na §25.6)
    embarcador        quem cadastrou/comandou a coleta (renato · pablo · rafael). NÃO é o
                      `solicitante`, que é o cliente
    origem_cnpj / destino_cnpj       o estabelecimento (14 dígitos) de coleta e de entrega
    origem_endereco / destino_endereco   texto, do cadastro `locais` — sem coordenada, e
                      NENHUMA régua (raio, rota, KPI) lê estas colunas. Âncora é aditiva (§21.4).

Regra de resolução do local, validada em 40 cargas (remessa de 15/09/26):
    origem  = coleta.reme_cnpj                           (a coleta é o ponto físico — Martins só tem aqui)
            ∨ cte.cnpj_expedidor  SE esta perna é o primeiro_manifesto E cidade_expedidor == cidade do CTRB
    destino = cte.cnpj_recebedor  SE esta perna é o ultimo_manifesto  E cidade_entrega == cidade do CTRB
    (o destino da COLETA não entra: é o destino final da mercadoria, não desta perna — em 12 de 13
    divergências o GPS parou onde o CTRB dizia, 15/09/26)
    coleta_conferencia   divergências coleta × manifesto (carreta/cavalo/motorista) — só REGISTRO;
                      quem julga qual placa rodou é o GPS, pelo aferidor (classe L1)
    O SSW copia remetente→expedidor e destinatário→recebedor quando não informados; a guarda de
    cidade é o que separa "copiado e físico" (indústria) de "copiado e fiscal" (Martins = CD).

Medido em 15/09/26 sobre 160 cargas do robô desde 01/09: 74 ligam pelo CTe, 59 pela placa,
27 sem coleta (17%). TUDO atrás de `EMBARQUES_COLETA` (nasce desligada); só toca em carga do
robô e só preenche campo vazio — nunca sobrescreve.
"""
import os
import logging
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta

_logger = logging.getLogger('embarques_coleta')
_HAS_COLS = None

CO = "'public coletas_0157'"
CE = "'public conhecimentos_emitidos'"
CE_COLS = ('serie_numero_ctrc', 'primeiro_manifesto', 'ultimo_manifesto', 'cnpj_expedidor', 'cidade_expedidor',
           'cnpj_recebedor', 'cidade_entrega', 'cnpj_remetente', 'cnpj_destinatario')
COLUNAS = (('coleta_origem', 'VARCHAR(20)'), ('coleta_via', 'VARCHAR(10)'), ('embarcador', 'VARCHAR(60)'),
           ('origem_cnpj', 'VARCHAR(14)'), ('destino_cnpj', 'VARCHAR(14)'),
           ('origem_endereco', 'VARCHAR(220)'), ('destino_endereco', 'VARCHAR(220)'),
           ('coleta_conferencia', 'VARCHAR(160)'))


def ligado():
    return str(os.getenv('EMBARQUES_COLETA', 'false')).strip().lower() in ('1', 'true', 'sim', 'yes')


def garantir_colunas(cur):
    global _HAS_COLS
    for col, tipo in COLUNAS:
        cur.execute(f"ALTER TABLE embarques_cargas ADD COLUMN IF NOT EXISTS {col} {tipo};")
    _HAS_COLS = True


def colunas_existem(cur):
    global _HAS_COLS
    if _HAS_COLS is None:
        cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name='embarques_cargas' AND column_name='coleta_origem'")
        _HAS_COLS = cur.fetchone() is not None
    return _HAS_COLS


def ativo(cur):
    return ligado() and colunas_existem(cur)


def _n(s):
    return unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().upper().strip()


def _dt(v):
    try:
        return datetime.fromisoformat(str(v)[:19])
    except (TypeError, ValueError):
        return None


def carregar(token, desde):
    """Coletas (todas as que o 157 traz) + CTes desde `desde`, só as colunas que este passo usa."""
    from embarques_auto import _dax, _norm
    d = f'DATE({desde.year},{desde.month},{desde.day})'
    coletas = _dax(token, f"EVALUATE {CO}")
    ctes = _dax(token, f"EVALUATE SELECTCOLUMNS(FILTER({CE}, {CE}[data_emissao] >= {d}), "
                       + ", ".join(f'"{c}",{CE}[{c}]' for c in CE_COLS) + ")")
    por_ctrc = {str(r['serie_numero_ctrc']).strip(): r for r in ctes if r.get('serie_numero_ctrc')}
    por_man = defaultdict(list)
    for r in ctes:
        for k in ('primeiro_manifesto', 'ultimo_manifesto'):
            m = _norm(r.get(k))
            if m:
                por_man[m].append(r)
    return coletas, por_ctrc, por_man


def _tem_locais(cur):
    cur.execute("SELECT to_regclass('locais') IS NOT NULL")
    return cur.fetchone()[0]


def _local(cur, cnpj, tem_locais=True):
    """Endereço em texto do cadastro `locais` (view alimentada pela fita). Sem cadastro, só o CNPJ."""
    if not cnpj:
        return None, None
    if not tem_locais:
        return cnpj, None
    cur.execute("SELECT nome, endereco, bairro, cep, cidade, uf FROM locais WHERE cnpj=%s", (cnpj,))
    r = cur.fetchone()
    if not r:
        return cnpj, None
    nome, end, bairro, cep, cid, uf = r
    txt = ' · '.join(x for x in (nome, end, bairro, cep, f'{cid or ""}/{uf or ""}'.strip('/')) if x)
    return cnpj, txt[:220] or None


def ligar(cur, token, ini, fim):
    """Preenche coleta/embarcador/local nas cargas do robô da janela que ainda não têm. Só lê o
    Power BI e o banco; grava campo vazio + log. Devolve Counter para o resumo do diário."""
    import placas as pl
    from embarques_auto import _norm, _log
    from _locais import cnpj14
    n = Counter()
    coletas, por_ctrc, por_man = carregar(token, ini - timedelta(days=15))
    tem_locais = _tem_locais(cur)
    por_placa = defaultdict(list)
    for r in coletas:
        p = pl.mercosul(str(r.get('veiculo') or '').strip())
        if p:
            por_placa[p].append(r)
    cur.execute("""
        SELECT c.id, c.numero, c.manifesto_origem, c.data_carregamento, c.cavalo_placa,
               c.origem_cidade, d.cidade, c.coleta_origem, c.embarcador, c.origem_cnpj, c.destino_cnpj,
               c.carreta1_placa, c.motorista_nome
          FROM embarques_cargas c
          JOIN embarques_cargas_destinos d ON d.carga_id = c.id
           AND d.ordem = (SELECT MIN(ordem) FROM embarques_cargas_destinos x WHERE x.carga_id = c.id)
         WHERE COALESCE(c.criada_por_robo, FALSE) = TRUE
           AND COALESCE(c.viagem_vazia, FALSE) = FALSE
           AND c.manifesto_origem IS NOT NULL
           AND c.data_carregamento BETWEEN %s AND %s
           AND (c.coleta_origem IS NULL OR c.origem_cnpj IS NULL OR c.destino_cnpj IS NULL)
    """, (ini - timedelta(days=2), fim))
    for cid, numero, man, dcarg, cav, ocid, dcid, col_atual, emb_atual, ocnpj_atual, dcnpj_atual, car, mot in cur.fetchall():
        mn = _norm(man)
        ctes = por_man.get(mn, [])
        # ── a ordem de coleta desta carga
        coleta, via = None, None
        for r in coletas:
            c = str(r.get('ctrc_gerado') or '').strip()
            if c and c in por_ctrc and _norm(por_ctrc[c].get('primeiro_manifesto')) == mn:
                coleta, via = r, 'cte'
                break
        if not coleta and cav:
            # reserva: cavalo E carreta iguais (o cavalo faz outra coleta no mesmo dia — só o
            # par fecha), dentro da janela de data
            for r in por_placa.get(pl.mercosul(cav), []):
                em = _dt(r.get('comandada_em')) or _dt(r.get('cadastrada_em'))
                v2 = pl.mercosul(str(r.get('veiculo_2') or ''))
                if not em or not (dcarg - timedelta(days=3) <= em.date() <= dcarg + timedelta(days=1)):
                    continue
                if car and v2 and v2 != pl.mercosul(car):
                    continue
                coleta, via = r, 'placa'
                break
        # ── o local, pela regra da remessa
        primeira = [r for r in ctes if _norm(r.get('primeiro_manifesto')) == mn]
        ultima = [r for r in ctes if _norm(r.get('ultimo_manifesto')) == mn]
        ocnpj = cnpj14(coleta.get('reme_cnpj')) if coleta else None
        if not ocnpj:
            ex = Counter(cnpj14(r.get('cnpj_expedidor')) for r in primeira if _n(r.get('cidade_expedidor')) == _n(ocid))
            ocnpj = ex.most_common(1)[0][0] if ex else None
        rc = Counter(cnpj14(r.get('cnpj_recebedor')) for r in ultima if _n(r.get('cidade_entrega')) == _n(dcid))
        dcnpj = rc.most_common(1)[0][0] if rc else None
        # ── conferência coleta × manifesto (registro, não ação)
        conf = []
        if coleta:
            v1 = pl.mercosul(str(coleta.get('veiculo') or '')); v2 = pl.mercosul(str(coleta.get('veiculo_2') or ''))
            if v1 and cav and v1 != pl.mercosul(cav):
                conf.append(f"cavalo {coleta.get('veiculo')}≠{cav}")
            if v2 and car and v2 != pl.mercosul(car):
                conf.append(f"carreta {coleta.get('veiculo_2')}≠{car}")
            m1 = _n(coleta.get('motorista')).split(); m2 = _n(mot).split()
            if m1 and m2 and m1[0] != m2[0]:
                conf.append(f"motorista {str(coleta.get('motorista'))[:18]}≠{str(mot)[:18]}")

        campos = {}
        if coleta and not col_atual:
            campos['coleta_origem'] = f"{coleta.get('unidade')}-{coleta.get('numero')}"[:20]
            campos['coleta_via'] = via
            emb = (coleta.get('comandada_por') or coleta.get('cadastrada_por') or '').strip()
            if emb and not emb_atual:
                campos['embarcador'] = emb[:60]
            if conf:
                campos['coleta_conferencia'] = ' | '.join(conf)[:160]
        if ocnpj and not ocnpj_atual:
            campos['origem_cnpj'], campos['origem_endereco'] = _local(cur, ocnpj, tem_locais)
        if dcnpj and not dcnpj_atual:
            campos['destino_cnpj'], campos['destino_endereco'] = _local(cur, dcnpj, tem_locais)
        campos = {k: v for k, v in campos.items() if v}
        if not campos:
            n['sem coleta e sem local'] += 1 if not coleta else 0
            continue
        sets = ', '.join(f'{k}=%s' for k in campos)
        cur.execute(f"UPDATE embarques_cargas SET {sets}, atualizado_em=NOW() WHERE id=%s", list(campos.values()) + [cid])
        for k, v in campos.items():
            if k in ('coleta_origem', 'embarcador', 'origem_cnpj', 'destino_cnpj'):
                _log(cur, cid, k, None, str(v))
        if 'coleta_origem' in campos:
            n[f'coleta ligada ({via})'] += 1
            if conf:
                n['coleta × manifesto DIVERGEM (registrado)'] += 1
        if 'origem_endereco' in campos:
            n['origem com local'] += 1
        if 'destino_endereco' in campos:
            n['destino com local'] += 1
        if not coleta:
            n['sem coleta (só local)'] += 1
    return n
