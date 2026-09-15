# -*- coding: utf-8 -*-
"""Cadastro de LOCAIS por CNPJ — o ponto físico de coleta e de entrega.

Por que CNPJ: os 14 dígitos identificam o ESTABELECIMENTO (a filial), então o mesmo CNPJ é
o mesmo galpão. O CTe não traz rua do expedidor, mas em 93% dos casos expedidor = remetente
(mesmo CNPJ) e aí `endereco_remetente` é o ponto de coleta; nos demais, o CNPJ do expedidor
aparece nas coletas do 157 (que têm endereço + CEP em 100%). Medido em 15/09/26: 106
expedidores distintos desde 01/08, 40 deles já com endereço em coleta = 85,7% dos CTes.

Cada observação fica em `locais_fontes` (cnpj, fonte); a view `locais` escolhe o melhor
endereço por CNPJ (rua > CEP > fonte preferida > mais recente). Coordenada nasce NULL —
geocodificar é outro passo, e NENHUMA régua do app lê estas tabelas (HANDOFF §21.4:
âncora é aditiva, nunca substituição).
"""

DDL = """
CREATE TABLE IF NOT EXISTS locais_fontes (
    cnpj         VARCHAR(14) NOT NULL,
    fonte        VARCHAR(20) NOT NULL,
    nome         VARCHAR(160),
    endereco     VARCHAR(200),
    bairro       VARCHAR(80),
    cep          VARCHAR(9),
    cidade       VARCHAR(80),
    uf           VARCHAR(2),
    n_docs       INTEGER NOT NULL DEFAULT 0,
    primeira_vez TIMESTAMP NOT NULL DEFAULT NOW(),
    ultima_vez   TIMESTAMP NOT NULL DEFAULT NOW(),
    latitude     DOUBLE PRECISION,
    longitude    DOUBLE PRECISION,
    geocod_fonte VARCHAR(20),
    geocod_em    TIMESTAMP,
    PRIMARY KEY (cnpj, fonte)
);
CREATE OR REPLACE VIEW locais AS
SELECT DISTINCT ON (cnpj) cnpj, fonte, nome, endereco, bairro, cep, cidade, uf, n_docs, ultima_vez,
       latitude, longitude, geocod_fonte
  FROM locais_fontes
 ORDER BY cnpj,
          (endereco IS NOT NULL) DESC,
          (cep IS NOT NULL) DESC,
          CASE fonte WHEN 'manual' THEN 0 WHEN 'coleta_remetente' THEN 1 WHEN 'coleta_destinatario' THEN 2
                     WHEN 'cte_remetente' THEN 3 WHEN 'cte_destinatario' THEN 4 WHEN 'cte_entrega' THEN 5
                     ELSE 9 END,
          ultima_vez DESC;
"""

# fonte -> (campo cnpj, nome, endereco, bairro, cep, cidade, uf)
MAPA = {
    'coleta': [
        ('coleta_remetente',    'reme_cnpj', 'reme_nome', 'reme_endereco', 'reme_bairro', 'reme_cep', 'reme_cidade', None),
        ('coleta_destinatario', 'dest_cnpj', 'dest_nome', 'dest_endereco', None, 'dest_cep', 'dest_cidade', 'dest_uf'),
    ],
    'cte': [
        ('cte_remetente',    'cnpj_remetente',    'cliente_remetente',    'endereco_remetente',    'bairro_remetente',    None,               'cidade_remetente',    'uf_remetente'),
        ('cte_destinatario', 'cnpj_destinatario', 'cliente_destinatario', 'endereco_destinatario', 'bairro_destinatario', 'cep_destinatario', 'cidade_destinatario', 'uf_destinatario'),
        ('cte_entrega',      'cnpj_recebedor',    'local_entrega',        None,                    None,                  'cep_entrega',      'cidade_entrega',      'uf_entrega'),
        ('cte_expedidor',    'cnpj_expedidor',    'cliente_expedidor',    None,                    None,                  None,               'cidade_expedidor',    'uf_expedidor'),
    ],
}
TAM = {'nome': 160, 'endereco': 200, 'bairro': 80, 'cep': 9, 'cidade': 80, 'uf': 2}


def cnpj14(v):
    d = ''.join(ch for ch in str(v or '') if ch.isdigit())
    return d.zfill(14) if 8 <= len(d) <= 14 else None


def _s(v, n):
    v = str(v or '').strip()
    return v[:n] or None


def atualizar(conn, dados):
    """`dados` = dict da fita ({'coleta': {chave: linha}, 'cte': {chave: linha}}).
    Só acrescenta/atualiza texto; nunca apaga e nunca toca em coordenada."""
    cur = conn.cursor()
    cur.execute(DDL)
    obs = {}
    for fonte_dados, regras in MAPA.items():
        for r in dados.get(fonte_dados, {}).values():
            for fonte, kc, kn, ke, kb, kcep, kcid, kuf in regras:
                c = cnpj14(r.get(kc))
                if not c:
                    continue
                # O SSW COPIA remetente->expedidor e destinatario->recebedor quando o campo nao e
                # informado (93% e 54% dos CTes, medido em 15/09/26). Copia nao e observacao nova:
                # nao alimenta o cadastro como fonte propria (senao o CD do Martins vira 339
                # "expedidores" sem rua). Expedidor/recebedor so entram quando DIFEREM do fiscal.
                if fonte == 'cte_expedidor' and c == cnpj14(r.get('cnpj_remetente')):
                    continue
                if fonte == 'cte_entrega' and c == cnpj14(r.get('cnpj_destinatario')):
                    continue
                o = obs.setdefault((c, fonte), {k: None for k in TAM} | {'n': 0})
                o['n'] += 1
                for campo, chave in (('nome', kn), ('endereco', ke), ('bairro', kb), ('cep', kcep), ('cidade', kcid), ('uf', kuf)):
                    val = _s(r.get(chave), TAM[campo]) if chave else None
                    if val:
                        o[campo] = val
    for (c, f), o in obs.items():
        cur.execute("""INSERT INTO locais_fontes (cnpj, fonte, nome, endereco, bairro, cep, cidade, uf, n_docs)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (cnpj, fonte) DO UPDATE SET
                         nome     = COALESCE(EXCLUDED.nome, locais_fontes.nome),
                         endereco = COALESCE(EXCLUDED.endereco, locais_fontes.endereco),
                         bairro   = COALESCE(EXCLUDED.bairro, locais_fontes.bairro),
                         cep      = COALESCE(EXCLUDED.cep, locais_fontes.cep),
                         cidade   = COALESCE(EXCLUDED.cidade, locais_fontes.cidade),
                         uf       = COALESCE(EXCLUDED.uf, locais_fontes.uf),
                         n_docs   = GREATEST(locais_fontes.n_docs, EXCLUDED.n_docs),
                         ultima_vez = NOW()""",
                    (c, f, o['nome'], o['endereco'], o['bairro'], o['cep'], o['cidade'], o['uf'], o['n']))
    conn.commit()
    cur.execute("SELECT count(*), count(*) FILTER (WHERE endereco IS NOT NULL), "
                "count(*) FILTER (WHERE cep IS NOT NULL) FROM locais")
    return cur.fetchone()


def cobertura(conn, dados):
    """Quantos CTes do lote têm expedidor e recebedor com rua no cadastro — a medida que importa."""
    cur = conn.cursor()
    cur.execute("SELECT cnpj FROM locais WHERE endereco IS NOT NULL")
    com_rua = {r[0] for r in cur.fetchall()}
    ctes = list(dados.get('cte', {}).values())
    if not ctes:
        return None
    ex = sum(1 for r in ctes if cnpj14(r.get('cnpj_expedidor')) in com_rua)
    rc = sum(1 for r in ctes if cnpj14(r.get('cnpj_recebedor')) in com_rua)
    return len(ctes), ex, rc
