# -*- coding: utf-8 -*-
"""Fita documental — retrato do que o Power BI mostra a cada refresh (HANDOFF §25.8, Passo 0).

Só leitura no Power BI; grava numa tabela própria (`fita_documentos`) que nada no app lê.
Cada execução é uma RODADA; a diferença entre rodadas consecutivas é o que hoje ninguém
mede: manifesto que nasce e some no mesmo dia (cancelado — o 916 não traz cancelado),
coleta que muda de situação, documento que muda de placa/destino depois de a carga nascer.

    python -X utf8 _fita_documentos.py            # grava a rodada e imprime o diff contra a anterior
    python -X utf8 _fita_documentos.py --diff     # só compara as duas últimas rodadas, não grava

Versão de laboratório (banco DB_NAME do ambiente). A versão de servidor entra no
`operacional_intradia.bat` da Rizza lendo o Postgres de lá direto, sem DAX.
"""
import sys
import json
import hashlib
from datetime import date, datetime, timedelta

from server import get_token, get_db
import embarques_auto as e
import _locais
import _programacao

M = "'public manifestos'"
CO = "'public coletas_0157'"
OS_ = "'public ctrbs_oss'"
CE = "'public conhecimentos_emitidos'"
CE_COLS = ('serie_numero_ctrc', 'primeiro_manifesto', 'ultimo_manifesto', 'tipo_documento', 'placa_coleta',
           'placa_cavalo', 'placa_carreta',
           'cnpj_expedidor', 'cliente_expedidor', 'cidade_expedidor', 'uf_expedidor',
           'cnpj_remetente', 'cliente_remetente', 'endereco_remetente', 'bairro_remetente', 'cidade_remetente', 'uf_remetente',
           'cnpj_destinatario', 'cliente_destinatario', 'endereco_destinatario', 'bairro_destinatario', 'cep_destinatario',
           'cidade_destinatario', 'uf_destinatario',
           'cnpj_recebedor', 'cliente_recebedor', 'local_entrega', 'cep_entrega', 'cidade_entrega', 'uf_entrega')

DDL = """
CREATE TABLE IF NOT EXISTS fita_documentos (
    rodada       TIMESTAMP NOT NULL,
    fonte        VARCHAR(12) NOT NULL,
    chave        VARCHAR(40) NOT NULL,
    importado_em TIMESTAMP,
    hash         VARCHAR(32) NOT NULL,
    payload      JSONB NOT NULL,
    PRIMARY KEY (rodada, fonte, chave)
);
CREATE INDEX IF NOT EXISTS ix_fita_fonte_chave ON fita_documentos (fonte, chave, rodada);
"""


def _d(x):
    return f'DATE({x.year},{x.month},{x.day})'


def coletar(tok, desde):
    out = {}
    m = e._dax(tok, f"EVALUATE FILTER({M}, {M}[data_emissao] >= {_d(desde)})")
    out['manifesto'] = {e._norm(r['CHAVE_MANIFESTO']): r for r in m if r.get('CHAVE_MANIFESTO')}
    c = e._dax(tok, f"EVALUATE {CO}")
    out['coleta'] = {f"{r.get('unidade')}-{r.get('numero')}": r for r in c if r.get('numero')}
    o = e._dax(tok, f"EVALUATE FILTER({OS_}, {OS_}[emissao] >= {_d(desde)})")
    out['ctrb'] = {e._chave_ctrb(r.get('ctrb')): r for r in o if e._chave_ctrb(r.get('ctrb'))}
    ct = e._dax(tok, f"EVALUATE SELECTCOLUMNS(FILTER({CE}, {CE}[data_emissao] >= {_d(desde)}), "
                     + ', '.join(f'"{c}",{CE}[{c}]' for c in CE_COLS) + ')')
    out['cte'] = {str(r['serie_numero_ctrc']).strip(): r for r in ct if r.get('serie_numero_ctrc')}
    return out


# campos que mudam sem o documento mudar: carimbo da extracao, e texto concatenado em ordem
# instavel pelo loader do 073 (observacao)
IGNORAR = ('data_importacao', 'periodo_ini', 'periodo_fim', 'emitido_em', 'observacao')


def _hash(r):
    r = {k: v for k, v in r.items() if k not in IGNORAR}
    return hashlib.md5(json.dumps(r, sort_keys=True, default=str).encode()).hexdigest()


def gravar(conn, rodada, dados):
    cur = conn.cursor()
    cur.execute(DDL)
    n = 0
    for fonte, itens in dados.items():
        for chave, r in itens.items():
            imp = r.get('data_importacao')
            cur.execute("INSERT INTO fita_documentos (rodada, fonte, chave, importado_em, hash, payload) "
                        "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        (rodada, fonte, chave[:40], imp, _hash(r), json.dumps(r, default=str)))
            n += 1
    conn.commit()
    return n


def diff(conn):
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT rodada FROM fita_documentos ORDER BY rodada DESC LIMIT 2")
    rods = [r[0] for r in cur.fetchall()]
    if len(rods) < 2:
        print('só uma rodada na fita — nada a comparar ainda')
        return
    nova, velha = rods
    print(f'\nDIFF  {velha:%d/%m %H:%M}  ->  {nova:%d/%m %H:%M}')
    for fonte in ('manifesto', 'coleta', 'ctrb', 'cte'):
        cur.execute("SELECT chave, hash, payload FROM fita_documentos WHERE rodada=%s AND fonte=%s", (velha, fonte))
        a = {c: (h, p) for c, h, p in cur.fetchall()}
        cur.execute("SELECT chave, hash, payload FROM fita_documentos WHERE rodada=%s AND fonte=%s", (nova, fonte))
        b = {c: (h, p) for c, h, p in cur.fetchall()}
        nasc = sorted(set(b) - set(a)); sum_ = sorted(set(a) - set(b))
        mud = sorted(c for c in set(a) & set(b) if a[c][0] != b[c][0])
        print(f'  {fonte:<10} {len(a):>5} -> {len(b):<5}  nasceram {len(nasc):<3} SUMIRAM {len(sum_):<3} mudaram {len(mud)}')
        for c in nasc[:12]:
            p = b[c][1]
            print(f'      + {c}  {_resumo(fonte, p)}')
        for c in sum_[:12]:
            p = a[c][1]
            print(f'      - {c}  {_resumo(fonte, p)}   <- SUMIU')
        for c in mud[:12]:
            pa, pb = a[c][1], b[c][1]
            campos = [k for k in pb if k not in IGNORAR and pa.get(k) != pb.get(k)]
            print(f'      ~ {c}  ' + ', '.join(f'{k}: {pa.get(k)!r} -> {pb.get(k)!r}' for k in campos[:5]))


def _resumo(fonte, p):
    if fonte == 'manifesto':
        return f"{str(p.get('data_emissao'))[:10]} {p.get('placa_cavalo')}/{p.get('placa_carreta')} {p.get('unidade_origem')}->{p.get('unidade_destino')} ctrb {p.get('CHAVE_CTRB')}"
    if fonte == 'coleta':
        return f"{p.get('situacao')} {p.get('veiculo')}/{p.get('veiculo_2')} {p.get('reme_cidade')} -> {p.get('dest_cidade')}/{p.get('dest_uf')} sol {p.get('solicitante')} ctrc {p.get('ctrc_gerado')}"
    if fonte == 'cte':
        return f"{p.get('primeiro_manifesto')}->{p.get('ultimo_manifesto')} {p.get('cidade_expedidor')} -> {p.get('cidade_entrega')}/{p.get('uf_entrega')} ({p.get('local_entrega')})"
    return f"{str(p.get('emissao'))[:10]} {p.get('cidade_uf_origem')} -> {p.get('cidade_uf_destino')}"


if __name__ == '__main__':
    conn = get_db()
    if '--locais' in sys.argv:      # so o cadastro, CTe desde 01/08; nao grava rodada
        dados = coletar(get_token(), date(2026, 8, 1))
        t, te, tc = _locais.atualizar(conn, dados)
        n, ex, rc = _locais.cobertura(conn, dados)
        np_, est = _programacao.atualizar(conn, dados)
        print(f'programação: {np_} ordens · ' + ' · '.join(f'{e} {c}' for e, c in est))
        print(f'locais: {t} CNPJs · {te} com rua · {tc} com CEP')
        print(f'cobertura nos {n} CTes desde 01/08: expedidor com rua {ex/n*100:.1f}% · recebedor com rua {rc/n*100:.1f}%')
        sys.exit(0)
    if '--diff' not in sys.argv:
        tok = get_token()
        dados = coletar(tok, date.today() - timedelta(days=7))
        rodada = datetime.now().replace(microsecond=0)
        n = gravar(conn, rodada, dados)
        imp = max((str(r.get('data_importacao') or '') for r in dados['manifesto'].values()), default='')
        print(f'rodada {rodada:%d/%m %H:%M:%S} gravada: {n} linhas  '
              f'(manifestos {len(dados["manifesto"])} · coletas {len(dados["coleta"])} · ctrb {len(dados["ctrb"])} · cte {len(dados["cte"])})  '
              f'último import de manifesto no BI: {imp[:19]}')
        t, te, tc = _locais.atualizar(conn, dados)
        print(f'locais: {t} CNPJs · {te} com rua · {tc} com CEP')
        np_, est = _programacao.atualizar(conn, dados)
        print(f'programação: {np_} ordens · ' + ' · '.join(f'{e} {c}' for e, c in est))
    diff(conn)
