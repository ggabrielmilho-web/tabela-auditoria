# -*- coding: utf-8 -*-
"""Lançamento automático de embarques a partir do MANIFESTO do SSW.

Por que o manifesto (e não o CTRB) é a âncora
─────────────────────────────────────────────
O manifesto é emitido quando o motorista sai — ele viaja com o documento na mão.
O CTRB é a OS de pagamento e pode atrasar. Medido em jul-ago/26 sobre 565
manifestos de frota+agregado:

  · grão limpo    — 1 manifesto = 1 CTRB = 1 par cavalo/carreta (1.176/1.176)
  · cobertura     — cavalo 100%, motorista 100%, CPF 100%, carreta 97,4%
  · CTRB no mesmo dia do manifesto: 97,3%   (D+1: 2,4%)
  · rodando D-1 às 05:10, o CTRB já existe em 96,1% dos casos

O teto é 97,3%: os 2,7% restantes têm `numero_ctrb_os = '000000'` (placeholder) e
NUNCA vão casar com um CTRB — esperar mais não resolve, por isso a janela de
varredura é curta e o que sobra vai para revisão manual.

De onde vem cada campo
──────────────────────
  manifesto   → placas, motorista, CPF, data de carregamento     (sempre existe)
  CTRB        → cidade de origem/destino, KM                     (96,1% em D-1)
  CTRCs       → tomador e entregas adicionais                    (quando há)

O destino da viagem é o do CTRB, NÃO o do CTRC: eles batem em só 69% porque são
coisas diferentes — o CTRB é a perna de transporte (onde o caminhão vai), o CTRC
é o destinatário da mercadoria.

`previsao_chegada` do CTRB é ignorada de propósito: tem 45 valores distintos em
980 registros, todos caindo em quinta-feira 18:00 — é prazo de fechamento
semanal, não previsão de entrega. A previsão sai da ETA de KM_DIA_PADRAO km/dia.

A rota planejada (ORS) é traçada AQUI, depois do commit — igual ao lançamento
manual. Delegar ao worker não funciona: ele só processa carga cujo veículo está
em `embarques_veiculos_rastreio`, então agregado sem rastreador Rizza nunca
ganhava rota (6 de 18 no primeiro dia). Medido: 7 cargas manuais sem rastreio
têm rota; 0 cargas do robô sem rastreio tinham.
"""

import os
import re
import json
import logging
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

import geocoding
import placas as _placas

_logger = logging.getLogger('embarques_auto')

M = "'public manifestos'"
OS_ = "'public ctrbs_oss'"
CE = "'public conhecimentos_emitidos'"
V45 = "'public veiculos_045'"


# ══════════════════════════════════════════════════════════════════════
# CONFIGURAÇÃO — tudo por variável de ambiente
# ══════════════════════════════════════════════════════════════════════

def _flag(nome, default='false'):
    return os.getenv(nome, default).strip().lower() == 'true'


def _int(nome, default):
    try:
        return int(os.getenv(nome, str(default)))
    except (TypeError, ValueError):
        return default


def ligado():
    """Chave geral. DESLIGADO por default: se o operacional voltar a lançar,
    basta `EMBARQUES_AUTO=false` no Portainer e o motor para — sem deploy,
    sem remover código, sem perder o que já foi criado."""
    return _flag('EMBARQUES_AUTO', 'false')


def fechamento_ligado():
    """Sub-chave: permite criar cargas sem deixar o robô ENCERRAR nenhuma.
    Útil no piloto — o risco de criar a mais é revisão, o de fechar errado é
    perder viagem em curso do mapa."""
    return _flag('EMBARQUES_AUTO_FECHAMENTO', 'true')


# Quantos dias para trás começar (1 = ontem). A carga full-refresh do SSW roda
# ~05:10 e traz o mês corrente inteiro, então D-1 já está disponível de manhã.
def _defasagem():
    return _int('EMBARQUES_AUTO_DEFASAGEM', 1)


# Janela varrida a cada execução. Cobre o manifesto cujo CTRB saiu em D+1
# (2,4% dos casos): na rodada seguinte ele entra, e o índice único impede
# duplicata. Curta de propósito — passou disso, é placeholder e não vai casar.
def _janela_dias():
    return _int('EMBARQUES_AUTO_JANELA_DIAS', 5)


def _tipos_alvo():
    """Terceiro fica de fora: 36% do volume, e é carga de quem não é da casa."""
    bruto = os.getenv('EMBARQUES_AUTO_TIPOS', 'Frota,Agregado')
    return {t.strip().title() for t in bruto.split(',') if t.strip()}


# Acima deste número de cidades, a carga é distribuição (fracionado) e vira
# 1 destino só + observação. Medido: 3,7% dos manifestos têm multi-destino, mas
# um deles tinha 110 CTRCs pela região dos lagos do RJ — 110 waypoints estouram
# a rota do ORS e não descrevem uma viagem, descrevem um roteiro de entrega.
def _max_destinos():
    return _int('EMBARQUES_AUTO_MAX_DESTINOS', 8)


def _destinos_ctrc():
    """Admitir a cidade do CTRC como destino adicional da viagem.

    DESLIGADO por default. O destino do CTRB e o do CTRC coincidem em apenas
    69% dos casos, e a divergência não é erro: o CTRB diz para onde o caminhão
    vai, o CTRC diz para quem é a mercadoria. Com isto ligado, uma carga
    Uberlândia -> Guarulhos ganhava 'Uberlândia' como segundo destino só porque
    o CD do cliente fica lá."""
    return _flag('EMBARQUES_AUTO_DESTINOS_CTRC', 'false')


# Fechamento por tempo. p90 do intervalo entre viagens da mesma placa = 6 dias,
# máximo observado = 13. 10 dias fica acima do p90 sem alcançar o máximo.
def _timeout_dias():
    return _int('EMBARQUES_AUTO_TIMEOUT_DIAS', 10)


def _max_rotas():
    """Teto de chamadas ao ORS por execucao. O free tier e 2.000/dia e o volume
    normal e ~12 cargas, mas um backlog (motor desligado por uma semana) nao pode
    virar centenas de chamadas de uma vez."""
    return _int('EMBARQUES_AUTO_MAX_ROTAS', 60)


def _filiais():
    """Sigla da unidade → 'Cidade/UF'. Só usada quando o CTRB não existe (e,
    portanto, não há cidade). Vazio por default: inventar origem é pior que
    deixar a carga pendente de lançamento manual."""
    try:
        return {k.strip().upper(): v for k, v in
                json.loads(os.getenv('EMBARQUES_AUTO_FILIAIS', '{}')).items()}
    except Exception:
        _logger.warning('EMBARQUES_AUTO_FILIAIS não é JSON válido — ignorando.')
        return {}


ROBO_NOME = 'Robô SSW (manifesto)'


# ══════════════════════════════════════════════════════════════════════
# LEITURA (Power BI)
# ══════════════════════════════════════════════════════════════════════

def _dax(token, query):
    """Executa DAX e ABORTA se a resposta veio cortada.

    O executeQueries tem teto de 15 MiB e devolve HTTP 200 mesmo truncando —
    resultado cortado parece resposta boa. Aqui isso criaria cargas a menos em
    silêncio, então a checagem de `error` não é opcional."""
    from server import execute_dax, clean_rows
    res = execute_dax(token, query)
    r0 = res.get('results', [{}])[0]
    if r0.get('error'):
        raise RuntimeError(f'DAX truncado ou com erro: {r0["error"]}')
    return clean_rows(r0.get('tables', [{}])[0].get('rows', []))


def _norm(s):
    """Normaliza nº de documento (tira espaço e pontuação) p/ casar
    'UDI 026011-8' com 'UDI026011-8'."""
    return re.sub(r'[^A-Za-z0-9]', '', str(s or '')).upper()


def _chave_ctrb(v):
    """ctrbs_oss[ctrb]='UDI024224-1' vs manifestos[CHAVE_CTRB]='UDI024224'."""
    return str(v or '').rsplit('-', 1)[0].strip()


def _placa(v):
    return _placas.mercosul(str(v or '').strip())


def _cpf(v):
    """Só os dígitos. O manifesto do SSW traz '044.559.306-71' e o lançamento
    manual grava '04455930671'; gravar no mesmo formato do manual é o que faz a
    trava de conflito de motorista enxergar as duas fontes como o mesmo CPF."""
    return re.sub(r'[^0-9]', '', str(v or ''))


def carregar_cadastro(token):
    """placa normalizada → {proprietario, tipo, modelo, eh_rizza}.

    Na colisão antiga×Mercosul prefere a entrada cuja placa crua JÁ é Mercosul
    (identidade atual) — senão um veículo Rizza é resolvido para o dono errado
    e a carga sai classificada como Terceiro."""
    linhas = _dax(token, f"EVALUATE SELECTCOLUMNS({V45}, "
                         f"\"p\",{V45}[placa], \"prop\",{V45}[proprietario], "
                         f"\"tipo\",{V45}[tipo], \"mod\",{V45}[modelo])")
    cad = {}
    for r in linhas:
        crua = str(r.get('p') or '').strip()
        p = _placa(crua)
        if not p:
            continue
        novo = {'proprietario': str(r.get('prop') or '').strip(),
                'tipo': str(r.get('tipo') or '').strip(),
                'modelo': str(r.get('mod') or '').strip(),
                'eh_rizza': 'RIZZA' in str(r.get('prop') or '').upper(),
                '_mercosul_real': _placas.eh_mercosul(crua)}
        antigo = cad.get(p)
        if antigo is None or (novo['_mercosul_real'] and not antigo['_mercosul_real']):
            cad[p] = novo
    return cad


def coletar(token, ini, fim):
    """Puxa manifestos da janela + os CTRBs e CTRCs necessários para enriquecer.

    A janela do CTRB/CTRC abre alguns dias antes: o CTRC de uma viagem longa é
    emitido antes do manifesto, e o CTRB pode sair no dia seguinte."""
    folga_ini = ini - timedelta(days=10)
    d = lambda x: f'DATE({x.year},{x.month},{x.day})'

    manifestos = _dax(token, f"EVALUATE FILTER({M}, {M}[data_emissao] >= {d(ini)} "
                             f"&& {M}[data_emissao] <= {d(fim)})")

    ctrbs_raw = _dax(token, f"EVALUATE SELECTCOLUMNS(FILTER({OS_}, "
                            f"{OS_}[emissao] >= {d(folga_ini)}), "
                            f"\"ctrb\",{OS_}[ctrb], \"o\",{OS_}[cidade_uf_origem], "
                            f"\"d\",{OS_}[cidade_uf_destino], \"km\",{OS_}[distancia_km], "
                            f"\"c2\",{OS_}[placa_carreta_2], \"cheg\",{OS_}[chegada], "
                            f"\"em\",{OS_}[emissao])")
    ctrbs = {}
    for r in ctrbs_raw:
        k = _chave_ctrb(r.get('ctrb'))
        if k:
            ctrbs[k] = r

    ctrcs_raw = _dax(token, f"EVALUATE SELECTCOLUMNS(FILTER({CE}, "
                            f"{CE}[data_emissao] >= {d(folga_ini)}), "
                            f"\"pm\",{CE}[primeiro_manifesto], \"um\",{CE}[ultimo_manifesto], "
                            f"\"cid\",{CE}[cidade_destinatario], \"uf\",{CE}[uf_destinatario], "
                            f"\"cli\",{CE}[cliente_pagador], \"vf\",{CE}[valor_frete])")
    ctrcs = defaultdict(list)
    for r in ctrcs_raw:
        for campo in ('pm', 'um'):
            k = _norm(r.get(campo))
            if k:
                ctrcs[k].append(r)

    return manifestos, ctrbs, ctrcs


# ══════════════════════════════════════════════════════════════════════
# MOTOR PURO — sem I/O, testável isoladamente
# ══════════════════════════════════════════════════════════════════════

def classificar(cavalo, carreta, cadastro, vendidas=frozenset()):
    """Regra Rizza pelo par de placas. O campo `propriedade` do SSW NÃO serve:
    ele chama de CARRETEIRO tanto agregado quanto terceiro (308 × 56 em ago/26),
    e a distinção que interessa é justamente essa."""
    def rizza(p):
        pn = _placa(p)
        if not pn or pn in vendidas:      # vendido mas ainda no nome da Rizza
            return False
        return bool(cadastro.get(pn, {}).get('eh_rizza'))

    tem_carreta = bool(_placa(carreta))
    if not tem_carreta:                   # truck rígido: classifica só pelo cavalo
        return 'Frota' if rizza(cavalo) else 'Terceiro'
    if rizza(cavalo) and rizza(carreta):
        return 'Frota'
    if rizza(cavalo) or rizza(carreta):
        return 'Agregado'
    return 'Terceiro'


def _cidade_uf(valor):
    """'UBERLANDIA/MG' → ('UBERLANDIA', 'MG'). None se não der para separar."""
    s = str(valor or '').strip()
    if '/' not in s:
        return None
    cid, uf = s.rsplit('/', 1)
    cid, uf = cid.strip(), uf.strip().upper()
    if not cid or len(uf) != 2:
        return None
    return cid, uf


def _veiculo(placa, cadastro):
    pn = _placa(placa)
    if not pn:
        return None
    c = cadastro.get(pn, {})
    return {'placa': pn, 'modelo': c.get('modelo') or None,
            'proprietario': c.get('proprietario') or None,
            'eh_rizza': bool(c.get('eh_rizza')), 'tipo': c.get('tipo') or ''}


def _tipo_cavalo(v):
    """embarques_cargas.cavalo_tipo é NOT NULL. Cadastro traz variações
    ('CAVALO TRUCADO', 'TOCO'); o que importa aqui é o rótulo grosso."""
    t = str((v or {}).get('tipo') or '').upper()
    if t.startswith('TRUCK') or t == 'TOCO':
        return 'Truck'
    return 'Cavalo'


def montar_carga(man, ctrbs, ctrcs, cadastro, cfg):
    """Monta UMA carga a partir de um manifesto. Devolve (carga, motivo_descarte).

    Só um dos dois vem preenchido. Motivo de descarte é string curta, para virar
    contador no relatório da execução."""
    cav_raw, car_raw = man.get('placa_cavalo'), man.get('placa_carreta')
    tipo = classificar(cav_raw, car_raw, cadastro, cfg['vendidas'])
    if tipo not in cfg['tipos']:
        return None, f'{tipo} (fora do alvo)'

    cavalo = _veiculo(cav_raw, cadastro)
    if not cavalo:
        return None, 'sem placa de cavalo'

    data_carreg = man.get('_data')
    if not data_carreg:
        return None, 'sem data de emissão'

    chave_ctrb = str(man.get('CHAVE_CTRB') or '').strip()
    placeholder = str(man.get('numero_ctrb_os') or '').strip() == '000000'
    ctrb = ctrbs.get(chave_ctrb) if (chave_ctrb and not placeholder) else None

    # ── Origem e destino: do CTRB. Sem CTRB, tenta a filial do manifesto.
    origem = destino = None
    if ctrb:
        origem = _cidade_uf(ctrb.get('o'))
        destino = _cidade_uf(ctrb.get('d'))
    if not origem:
        origem = _cidade_uf(cfg['filiais'].get(str(man.get('unidade_origem') or '').upper()))
    if not destino:
        destino = _cidade_uf(cfg['filiais'].get(str(man.get('unidade_destino') or '').upper()))
    if not origem or not destino:
        # Aguarda o CTRB. Volta na próxima rodada dentro da janela; se nunca
        # vier (placeholder), sai no relatório para lançamento manual.
        return None, 'aguardando CTRB (sem cidade)'

    # ── Tomador e entregas adicionais: dos CTRCs
    docs = ctrcs.get(_norm(man.get('CHAVE_MANIFESTO')), [])
    tomador = None
    if docs:
        pesos = Counter()
        for r in docs:
            nome = str(r.get('cli') or '').strip()
            if nome:
                pesos[nome] += float(r.get('vf') or 0) or 1
        if pesos:
            tomador = pesos.most_common(1)[0][0]

    destinos = [{'cidade': destino[0], 'uf': destino[1]}]
    extras = []
    for r in docs:
        cu = _cidade_uf(f"{str(r.get('cid') or '').strip()}/{str(r.get('uf') or '').strip()}")
        # Descarta o que for a própria ORIGEM: `cidade_destinatario` costuma ser
        # o CD/matriz (Uberlândia), e admiti-lo como destino faria a rota
        # terminar no ponto de partida — o rastreamento leria "chegou" no pátio.
        if not cu or cu == destino or cu == (origem[0], origem[1]):
            continue
        if cu not in [(e['cidade'], e['uf']) for e in extras]:
            extras.append({'cidade': cu[0], 'uf': cu[1]})

    obs = [f'Lançada automaticamente do manifesto {man.get("CHAVE_MANIFESTO")}.']
    if extras:
        cidades = ', '.join(f"{e['cidade']}/{e['uf']}" for e in extras[:12])
        if cfg['destinos_ctrc'] and len(extras) + 1 <= cfg['max_destinos']:
            destinos.extend(extras)
        else:
            # Default: o destino da viagem é o do CTRB. Ele e o do CTRC batem em
            # só 69% — são coisas diferentes (perna de transporte x destinatário
            # da mercadoria), então a entrega extra fica ANOTADA, não vira ponto
            # de rota. Ligar só depois de conferir na tela: EMBARQUES_AUTO_DESTINOS_CTRC.
            obs.append(f'{len(extras)} cidade(s) de entrega em {len(docs)} CTRC(s): {cidades}.')

    # ── Previsão de entrega pela ETA de km/dia (a do CTRB não presta)
    km = None
    try:
        km = float(ctrb.get('km')) if ctrb and ctrb.get('km') else None
    except (TypeError, ValueError):
        km = None
    previsao = None
    if km and km > 0:
        dias = max(1, int((km + cfg['km_dia'] - 1) // cfg['km_dia']))
        previsao = data_carreg + timedelta(days=dias)

    carreta2 = _veiculo((ctrb or {}).get('c2'), cadastro)

    return {
        'manifesto': str(man.get('CHAVE_MANIFESTO') or '').strip(),
        'ctrb': chave_ctrb if ctrb else None,
        'tipo_operacao': tipo,
        'data_carregamento': data_carreg,
        'previsao_entrega': previsao,
        'origem': {'cidade': origem[0], 'uf': origem[1]},
        'destinos': destinos,
        'motorista': {'nome': str(man.get('nome_motorista') or '').strip(),
                      'cpf': _cpf(man.get('cpf_motorista'))},
        'cavalo': cavalo,
        'carreta1': _veiculo(car_raw, cadastro),
        'carreta2': carreta2,
        'cliente_nome': tomador,
        'km': km,
        'observacoes': ' '.join(obs),
        'incompleta': not bool(tomador),
    }, None


def montar(manifestos, ctrbs, ctrcs, cadastro, cfg):
    """Motor sobre a janela inteira. Devolve (cargas, contador_de_descartes)."""
    cargas, descartes = [], Counter()
    for man in manifestos:
        carga, motivo = montar_carga(man, ctrbs, ctrcs, cadastro, cfg)
        if carga:
            cargas.append(carga)
        else:
            descartes[motivo] += 1
    return cargas, descartes


# ══════════════════════════════════════════════════════════════════════
# PERSISTÊNCIA
# ══════════════════════════════════════════════════════════════════════

def resolver_cidade(cur, cidade, uf):
    """Grafia do SSW -> grafia oficial do IBGE. 'GOIANIA'/GO -> 'Goiânia'.

    O formulário monta o <select> de cidade com as opções do IBGE
    (value="Goiânia") e depois faz select.value = <o que está gravado>. Com a
    grafia do SSW em caixa alta e sem acento, a opção não existe e o campo abre
    VAZIO — a carga do robô ficava impossível de editar pela tela (a validação
    ainda rejeitava o salvamento por origem incompleta).

    Degrada para o valor original se o município não estiver em municipios_ibge:
    melhor a grafia crua do que perder a cidade."""
    if not cidade or not uf:
        return cidade
    cur.execute("SELECT cidade FROM municipios_ibge "
                "WHERE cidade_normalizada = %s AND uf = %s",
                (geocoding.normalizar_cidade(cidade), str(uf).strip().upper()))
    r = cur.fetchone()
    return r[0] if r else cidade


def _norm_cliente(nome):
    """Chave de comparação de razão social: sem acento, sem pontuação, espaços
    colapsados. É o que faz 'QUIMICA AMPARO LTDA' casar com 'Quimica Amparo
    Ltda.' — a diferença entre os dois era só o ponto final."""
    import unicodedata
    s = unicodedata.normalize('NFKD', str(nome or ''))
    s = ''.join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r'[^A-Za-z0-9 ]', '', s.upper())
    return re.sub(r'\s+', ' ', s).strip()


def resolver_cliente(cur, nome, permitir_cadastro=True):
    """nome do tomador -> (cliente_id, acao).

    O formulário seleciona o cliente por ID; sem `cliente_id` a carga não abre
    para edição e nem salva (o backend exige o campo). Mas cadastrar por
    conveniência polui uma base que outras telas usam, então a busca é
    deliberadamente exaustiva ANTES de considerar cadastro:

      1. match exato normalizado  — resolve pontuação e acento
      2. match por prefixo de 30  — o SSW trunca a razão social em 30 caracteres,
         então 'PERNOD RICARD BRASIL IND E COMERCIO LTDA' e
         'PERNOD RICARD BRASIL IND E COM' são o MESMO cliente
      3. só então cadastra

    AMBIGUIDADE NÃO É RESOLVIDA POR PALPITE: se o prefixo casar com mais de um
    cliente, devolve (None, 'ambiguo') e a carga fica marcada como incompleta.
    Errar o cliente é pior que deixar em branco — e cadastrar um duplicado
    porque a busca desistiu cedo é exatamente o que suja a base.
    """
    nome = (str(nome or '').strip())
    if not nome:
        return None, 'sem nome'
    alvo = _norm_cliente(nome)
    if not alvo:
        return None, 'sem nome'

    cur.execute("SELECT id, nome FROM clientes")
    todos = [(i, n, _norm_cliente(n)) for i, n in cur.fetchall()]

    exatos = [(i, n) for i, n, nn in todos if nn == alvo]
    if len(exatos) == 1:
        return exatos[0][0], 'existente'
    if len(exatos) > 1:
        return exatos[0][0], 'existente'      # duplicata já na base: usa a 1ª

    # Truncamento do SSW (30 chars) — nos dois sentidos.
    pref = alvo[:30]
    cands = {i: n for i, n, nn in todos if nn[:30] == pref}
    if len(cands) == 1:
        return next(iter(cands)), 'existente'
    if len(cands) > 1:
        return None, 'ambiguo'

    if not permitir_cadastro:
        return None, 'nao encontrado'

    # Cliente novo de verdade. O índice único case-insensitive de `clientes` é a
    # última trava: se houver corrida, o ON CONFLICT devolve o id que já existe
    # em vez de criar irmão gêmeo.
    cur.execute("INSERT INTO clientes (nome) VALUES (%s) "
                "ON CONFLICT (LOWER(TRIM(nome))) DO UPDATE SET nome = clientes.nome "
                "RETURNING id", (nome,))
    r = cur.fetchone()
    return (r[0] if r else None), 'cadastrado'


def garantir_colunas(cur):
    """DDL idempotente (mesmo padrão do init_db.py).

    `manifesto_origem` com índice ÚNICO é o que torna o job seguro de repetir:
    rodar duas vezes o mesmo dia não duplica carga."""
    cur.execute("ALTER TABLE embarques_cargas ADD COLUMN IF NOT EXISTS manifesto_origem VARCHAR(30);")
    cur.execute("ALTER TABLE embarques_cargas ADD COLUMN IF NOT EXISTS ctrb_origem VARCHAR(20);")
    cur.execute("ALTER TABLE embarques_cargas ADD COLUMN IF NOT EXISTS criada_por_robo BOOLEAN DEFAULT FALSE;")
    cur.execute("ALTER TABLE embarques_cargas ADD COLUMN IF NOT EXISTS auto_incompleta BOOLEAN DEFAULT FALSE;")
    cur.execute("ALTER TABLE embarques_cargas ADD COLUMN IF NOT EXISTS encerrada_motivo VARCHAR(30);")
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_cargas_manifesto_origem "
                "ON embarques_cargas (manifesto_origem) WHERE manifesto_origem IS NOT NULL;")


def _ja_lancada(cur, carga):
    """Já existe carga para este manifesto, ou o time já lançou esta viagem à mão?

    A segunda checagem evita duplicar o trabalho do operacional: mesmo cavalo, no
    mesmo dia (±1), em carga ativa. Nesse caso o robô sai de fininho — quem
    lançou à mão tem contexto que o SSW não tem."""
    cur.execute("SELECT id FROM embarques_cargas WHERE manifesto_origem = %s",
                (carga['manifesto'],))
    if cur.fetchone():
        return 'já lançada pelo robô'

    cur.execute("""
        SELECT numero FROM embarques_cargas
        WHERE cavalo_placa = %s
          AND data_carregamento BETWEEN %s AND %s
          AND COALESCE(criada_por_robo, FALSE) = FALSE
          AND status <> 'Cancelada'
        LIMIT 1
    """, (carga['cavalo']['placa'],
          carga['data_carregamento'] - timedelta(days=1),
          carga['data_carregamento'] + timedelta(days=1)))
    r = cur.fetchone()
    return f'lançada à mão ({r[0]})' if r else None


def inserir(cur, carga, conn=None):
    """Grava a carga + destinos + geocoding. Devolve (id, numero).

    Não chama o ORS: o worker traça a rota de toda carga sem polyline no ciclo
    seguinte, e fazer aqui gastaria chamada em carga que pode ser cancelada."""
    cav = carga['cavalo']
    c1 = carga.get('carreta1') or {}
    c2 = carga.get('carreta2') or {}
    ori = carga['origem']

    # Cidade na grafia do IBGE e cliente resolvido para ID: sem isso a carga
    # grava certo mas não abre para edição na tela (ver resolver_cidade/cliente).
    ori = {'cidade': resolver_cidade(cur, ori['cidade'], ori['uf']), 'uf': ori['uf']}
    destinos = [{'cidade': resolver_cidade(cur, d['cidade'], d['uf']), 'uf': d['uf']}
                for d in carga['destinos']]
    cliente_id, acao_cliente = resolver_cliente(cur, carga.get('cliente_nome'))
    if acao_cliente == 'cadastrado':
        _logger.info(f"cliente novo cadastrado: {carga.get('cliente_nome')!r}")
    elif acao_cliente == 'ambiguo':
        _logger.warning(f"cliente {carga.get('cliente_nome')!r} casa com mais de um "
                        f"cadastro — carga fica sem cliente_id para revisão")
    # Sem cliente resolvido a carga não é editável pela tela → marca incompleta.
    incompleta = bool(carga.get('incompleta')) or cliente_id is None

    cur.execute("""
        INSERT INTO embarques_cargas (
            tipo_operacao, status, viagem_vazia,
            cliente_id, cliente_nome,
            origem_cidade, origem_uf,
            motorista_nome, motorista_cpf,
            cavalo_placa, cavalo_tipo, cavalo_marca_modelo, cavalo_proprietario, cavalo_eh_rizza,
            carreta1_placa, carreta1_marca_modelo, carreta1_proprietario, carreta1_eh_rizza,
            carreta2_placa, carreta2_marca_modelo, carreta2_proprietario, carreta2_eh_rizza,
            data_carregamento, previsao_entrega, observacoes,
            criado_por_nome, manifesto_origem, ctrb_origem,
            criada_por_robo, auto_incompleta
        ) VALUES (
            %s, 'Aberta', FALSE,
            %s, %s,
            %s, %s,
            %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            TRUE, %s
        ) RETURNING id, criado_em
    """, (
        carga['tipo_operacao'],
        cliente_id, carga.get('cliente_nome'),
        ori['cidade'], ori['uf'],
        carga['motorista']['nome'], carga['motorista']['cpf'],
        cav['placa'], _tipo_cavalo(cav), cav.get('modelo'), cav.get('proprietario'), cav.get('eh_rizza'),
        c1.get('placa'), c1.get('modelo'), c1.get('proprietario'), bool(c1.get('eh_rizza')),
        c2.get('placa'), c2.get('modelo'), c2.get('proprietario'), bool(c2.get('eh_rizza')),
        carga['data_carregamento'], carga.get('previsao_entrega'), carga.get('observacoes'),
        ROBO_NOME, carga['manifesto'], carga.get('ctrb'),
        incompleta,
    ))
    carga_id, criado_em = cur.fetchone()

    for i, d in enumerate(destinos, start=1):
        dlat, dlng = geocoding.geocoder_municipio(d['cidade'], d['uf'], conn=conn)
        cur.execute("INSERT INTO embarques_cargas_destinos "
                    "(carga_id, ordem, cidade, uf, latitude, longitude) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (carga_id, i, d['cidade'], d['uf'], dlat, dlng))

    olat, olng = geocoding.geocoder_municipio(ori['cidade'], ori['uf'], conn=conn)
    numero = f"C-{criado_em.year}-{carga_id:06d}"
    cur.execute("UPDATE embarques_cargas SET origem_latitude=%s, origem_longitude=%s, "
                "numero=%s WHERE id=%s", (olat, olng, numero, carga_id))
    return carga_id, numero


def _log(cur, carga_id, campo, de, para):
    cur.execute("INSERT INTO embarques_cargas_log "
                "(carga_id, usuario_nome, campo, valor_anterior, valor_novo) "
                "VALUES (%s,%s,%s,%s,%s)", (carga_id, ROBO_NOME, campo, de, para))


def encerrar(cur, carga_id, motivo, quando=None):
    """Fecha a carga registrando POR QUE fechou.

    Fica em 'Entregue' com `entregue_auto=FALSE` e `encerrada_motivo` != 'gps':
    é o único status final que as telas entendem, e a coluna de motivo impede
    que fechamento por regra se confunda com entrega provada por GPS."""
    cur.execute("SELECT status FROM embarques_cargas WHERE id=%s", (carga_id,))
    r = cur.fetchone()
    if not r:
        return False
    cur.execute("""
        UPDATE embarques_cargas
        SET status='Entregue', entregue_auto=FALSE, encerrada_motivo=%s,
            data_conclusao=COALESCE(data_conclusao, %s), atualizado_em=NOW()
        WHERE id=%s
    """, (motivo, quando or datetime.utcnow(), carga_id))
    _log(cur, carga_id, 'status', r[0], f'Entregue (robô: {motivo})')
    return True


def fechar_pendentes(cur, manifestos, ctrbs, hoje, cfg):
    """Cascata de fechamento das cargas que o GPS não fechou.

    Ordem importa: roda ANTES de criar, para liberar cavalo/carreta que estão
    presos numa carga antiga e bloqueariam a viagem nova.

      1. manifesto novo da mesma placa  — cobre as 67 de 139 placas que rodaram
         2+ vezes no mês (p50 de 3 dias entre viagens)
      2. baixa do CTRB no SSW           — `chegada` preenchida; 86% dos CTRBs.
         NÃO é chegada física (não correlaciona com distância), é a baixa
         administrativa da OS: serve de sinal de fim, não de horário
      3. timeout                        — para a metade das placas que não teve
         viagem seguinte no período
    """
    fechadas = Counter()
    if not fechamento_ligado():
        return fechadas

    # ── 1. manifesto novo desengata a carga anterior da mesma placa
    for man in manifestos:
        dt = man.get('_data')
        if not dt:
            continue
        for bruta in (man.get('placa_cavalo'), man.get('placa_carreta')):
            p = _placa(bruta)
            if not p:
                continue
            cur.execute("""
                SELECT id FROM embarques_cargas
                WHERE (cavalo_placa=%s OR carreta1_placa=%s OR carreta2_placa=%s)
                  AND status IN ('Aberta','Em rota','No destino','Desengatada')
                  AND data_carregamento < %s
                  AND COALESCE(criada_por_robo, FALSE) = TRUE
            """, (p, p, p, dt))
            for (cid,) in cur.fetchall():
                if encerrar(cur, cid, 'manifesto_novo', datetime.combine(dt, datetime.min.time())):
                    fechadas['manifesto novo'] += 1

    # ── 2. baixa do CTRB no SSW
    cur.execute("""
        SELECT id, ctrb_origem FROM embarques_cargas
        WHERE status IN ('Aberta','Em rota','No destino','Desengatada')
          AND COALESCE(criada_por_robo, FALSE) = TRUE
          AND ctrb_origem IS NOT NULL
    """)
    for cid, ctrb_k in cur.fetchall():
        reg = ctrbs.get(ctrb_k)
        if reg and str(reg.get('cheg') or '').strip():
            if encerrar(cur, cid, 'baixa_ctrb'):
                fechadas['baixa do CTRB'] += 1

    # ── 3. timeout
    limite = hoje - timedelta(days=cfg['timeout_dias'])
    cur.execute("""
        SELECT id FROM embarques_cargas
        WHERE status IN ('Aberta','Em rota','No destino','Desengatada')
          AND COALESCE(criada_por_robo, FALSE) = TRUE
          AND data_carregamento < %s
    """, (limite,))
    for (cid,) in cur.fetchall():
        if encerrar(cur, cid, 'timeout'):
            fechadas['timeout'] += 1

    return fechadas


def tracar_rotas_pendentes(conn, limite=None):
    """Traça a rota ORS das cargas do robô que ainda não têm polyline.

    Por que existe: o lançamento manual traça a rota no próprio POST, e por isso
    a carga digitada à mão SEMPRE tem rota — inclusive sem rastreamento (medido:
    7 cargas manuais sem veículo na 3S, todas com rota). O robô delegava ao
    worker, mas o worker só processa carga cujo veículo está em
    `embarques_veiculos_rastreio`. Resultado: carga de agregado sem rastreador
    Rizza nunca ganhava rota — 6 de 18 no primeiro dia.

    Roda DEPOIS do commit, como no manual: falha de ORS não pode desfazer a
    carga, que é o dado que importa. E como busca por `polyline IS NULL`, a
    execução seguinte recupera sozinha o que falhou enquanto a API esteve fora.
    """
    import ors_client
    cur = conn.cursor()
    tracadas = falhas = 0
    try:
        cur.execute("""
            SELECT id, origem_latitude, origem_longitude
            FROM embarques_cargas
            WHERE COALESCE(criada_por_robo, FALSE) = TRUE
              AND rota_planejada_polyline IS NULL
              AND status NOT IN ('Entregue', 'Cancelada')
              AND origem_latitude IS NOT NULL
            ORDER BY id DESC
        """)
        pendentes = cur.fetchall()
        if limite:
            pendentes = pendentes[:limite]

        for carga_id, olat, olng in pendentes:
            try:
                pontos = [{'lat': float(olat), 'lng': float(olng)}]
                # origem -> cidades de rota -> destinos, na ordem (igual ao manual)
                for tabela in ('embarques_cargas_rota', 'embarques_cargas_destinos'):
                    cur.execute(f"SELECT latitude, longitude FROM {tabela} "
                                f"WHERE carga_id=%s ORDER BY ordem", (carga_id,))
                    for la, ln in cur.fetchall():
                        if la is not None and ln is not None:
                            pontos.append({'lat': float(la), 'lng': float(ln)})
                if len(pontos) < 2:
                    continue
                rota = ors_client.tracar_rota_multi(pontos)
                cur.execute("""
                    UPDATE embarques_cargas SET rota_planejada_polyline=%s,
                        distancia_planejada_km=%s, duracao_estimada_min=%s,
                        rota_recalculada_em=NOW()
                    WHERE id=%s
                """, (rota['polyline'], rota['distancia_km'], rota['duracao_min'], carga_id))
                conn.commit()
                tracadas += 1
            except Exception as e:
                # Uma rota que falha não derruba as outras: commit por carga.
                conn.rollback()
                falhas += 1
                _logger.warning(f'[Carga {carga_id}] ORS falhou: {e}')
    finally:
        cur.close()
    return tracadas, falhas


def dedup_veiculo(cur, ctrbs):
    """Garante UMA carga ativa por cavalo e por carreta.

    Duas pernas da mesma viagem saem no MESMO dia — medido: o cavalo AZK2I93
    fez Resende/RJ -> Extrema/MG e, no mesmo 17/08, Extrema/MG -> Anápolis/GO
    (o destino de uma é a origem da outra). Como `data_emissao` do manifesto não
    tem hora, a regra de "manifesto novo fecha o anterior" não desempata o mesmo
    dia e as duas ficavam abertas — situação que `_buscar_conflitos` proíbe no
    lançamento manual (HTTP 409).

    O desempate é a emissão do CTRB, que TEM hora. Sem CTRB, cai no id (ordem de
    criação). Fecha todas menos a última."""
    fechadas = Counter()
    for campo in ('cavalo_placa', 'carreta1_placa'):
        cur.execute(f"""
            SELECT {campo}, id, data_carregamento, ctrb_origem
            FROM embarques_cargas
            WHERE status IN ('Aberta','Em rota','No destino','Desengatada')
              AND COALESCE(criada_por_robo, FALSE) = TRUE
              AND {campo} IS NOT NULL AND {campo} <> ''
        """)
        grupos = defaultdict(list)
        for placa, cid, dt, ctrb_k in cur.fetchall():
            emissao = str((ctrbs.get(ctrb_k) or {}).get('em') or '')
            grupos[placa].append((dt, emissao, cid))
        for placa, itens in grupos.items():
            if len(itens) < 2:
                continue
            itens.sort()                      # (data, emissão do CTRB, id)
            for _, _, cid in itens[:-1]:      # só a última continua aberta
                if encerrar(cur, cid, 'sequencia_viagem'):
                    fechadas['sequência de viagem'] += 1
    return fechadas


def reconciliar(cur, ctrbs, ctrcs):
    """Enriquece carga criada sem tomador quando o CTRC aparece depois.
    Só preenche o que está vazio — nunca sobrescreve edição humana."""
    n = 0
    cur.execute("""
        SELECT id, manifesto_origem FROM embarques_cargas
        WHERE COALESCE(auto_incompleta, FALSE) = TRUE
          AND cliente_nome IS NULL
          AND status <> 'Cancelada'
    """)
    for cid, manifesto in cur.fetchall():
        docs = ctrcs.get(_norm(manifesto), [])
        pesos = Counter()
        for r in docs:
            nome = str(r.get('cli') or '').strip()
            if nome:
                pesos[nome] += float(r.get('vf') or 0) or 1
        if not pesos:
            continue
        tomador = pesos.most_common(1)[0][0]
        cur.execute("UPDATE embarques_cargas SET cliente_nome=%s, auto_incompleta=FALSE, "
                    "atualizado_em=NOW() WHERE id=%s", (tomador, cid))
        _log(cur, cid, 'cliente_nome', None, tomador)
        n += 1
    return n


# ══════════════════════════════════════════════════════════════════════
# ORQUESTRAÇÃO
# ══════════════════════════════════════════════════════════════════════

def _parse_data(v):
    s = str(v or '')[:19]
    for f in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass
    return None


def _config():
    try:
        from server import PLACAS_VENDIDAS
        vendidas = {_placa(p) for p in PLACAS_VENDIDAS}
    except Exception:
        vendidas = frozenset()
    try:
        from server import KM_DIA_PADRAO
        km_dia = float(KM_DIA_PADRAO)
    except Exception:
        km_dia = float(os.getenv('KM_DIA_PADRAO', '600'))
    return {'tipos': _tipos_alvo(), 'vendidas': vendidas, 'filiais': _filiais(),
            'max_destinos': _max_destinos(), 'timeout_dias': _timeout_dias(),
            'destinos_ctrc': _destinos_ctrc(), 'km_dia': km_dia}


def executar(dia=None, dry_run=False, token=None, conn=None):
    """Uma execução completa. `dia` = fim da janela (default: hoje - defasagem).

    Devolve dict com o que foi feito — é o que vai para o log e para o relatório
    do piloto."""
    from server import get_token, get_db

    if not ligado() and not dry_run:
        _logger.info('EMBARQUES_AUTO desligado — nada a fazer.')
        return {'ok': False, 'motivo': 'desligado'}

    hoje = date.today()
    fim = dia or (hoje - timedelta(days=_defasagem()))
    ini = fim - timedelta(days=_janela_dias() - 1)
    cfg = _config()

    token = token or get_token()
    manifestos, ctrbs, ctrcs = coletar(token, ini, fim)
    for m in manifestos:
        m['_data'] = _parse_data(m.get('data_emissao'))
    cadastro = carregar_cadastro(token)

    cargas, descartes = montar(manifestos, ctrbs, ctrcs, cadastro, cfg)

    fechou_conn = conn is None
    conn = conn or get_db()
    cur = conn.cursor()
    resumo = {'ok': True, 'janela': [ini.isoformat(), fim.isoformat()],
              'manifestos': len(manifestos), 'candidatas': len(cargas),
              'criadas': 0, 'puladas': Counter(), 'descartes': descartes,
              'fechadas': Counter(), 'reconciliadas': 0, 'dry_run': dry_run,
              'detalhe': []}
    try:
        # Roda até no dry-run: a checagem de duplicata lê `manifesto_origem`, que
        # precisa existir. DDL no Postgres é transacional, então o rollback do
        # dry-run desfaz.
        garantir_colunas(cur)

        resumo['fechadas'] = fechar_pendentes(cur, manifestos, ctrbs, hoje, cfg) \
            if not dry_run else Counter()

        for c in cargas:
            # A checagem de duplicata roda TAMBÉM no dry-run: ela é só leitura, e
            # sem ela o dry-run superestima grosseiramente — mostrava "60 criadas"
            # numa janela em que 48 já estavam lançadas. Número de simulação que
            # não bate com o da execução real não serve para conferir nada.
            motivo = _ja_lancada(cur, c)
            if motivo:
                resumo['puladas'][motivo.split(' (')[0]] += 1
                continue
            if dry_run:
                resumo['detalhe'].append(
                    f"{c['manifesto']} [{c['tipo_operacao']}] "
                    f"{c['cavalo']['placa']}/{(c.get('carreta1') or {}).get('placa') or '—'} "
                    f"{c['motorista']['nome'][:20]} | {c['origem']['cidade']}/{c['origem']['uf']} "
                    f"-> {' + '.join(d['cidade'] for d in c['destinos'])} "
                    f"| {c.get('cliente_nome') or '(sem tomador)'} "
                    f"| prev {c.get('previsao_entrega') or '—'}")
                resumo['criadas'] += 1
                continue
            _, numero = inserir(cur, c, conn=conn)
            resumo['criadas'] += 1
            resumo['detalhe'].append(f"{numero} <- {c['manifesto']}")

        if not dry_run:
            # Depois de criar: duas pernas da mesma viagem no mesmo dia deixam o
            # mesmo cavalo em duas cargas ativas. Só a última fica aberta.
            if fechamento_ligado():
                for k, v in dedup_veiculo(cur, ctrbs).items():
                    resumo['fechadas'][k] += v
            resumo['reconciliadas'] = reconciliar(cur, ctrbs, ctrcs)
            conn.commit()
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    # Rota planejada DEPOIS do commit (mesma ordem do lançamento manual): a carga
    # ja esta salva, entao ORS fora do ar custa uma rota, nunca a carga.
    if not dry_run:
        try:
            t, f = tracar_rotas_pendentes(conn, limite=_max_rotas())
            resumo['rotas_tracadas'] = t
            resumo['rotas_falhas'] = f
        except Exception as e:
            _logger.warning(f'Traçado de rotas falhou: {e}')
    if fechou_conn:
        conn.close()
    return resumo


def _imprimir(r):
    print(f"\njanela {r['janela'][0]} .. {r['janela'][1]}"
          f"{'  [DRY-RUN — nada gravado]' if r['dry_run'] else ''}")
    print(f"manifestos lidos ..... {r['manifestos']}")
    print(f"cargas candidatas .... {r['candidatas']}")
    print(f"CRIADAS .............. {r['criadas']}")
    if r['puladas']:
        print("puladas:")
        for k, v in r['puladas'].most_common():
            print(f"   {k:<34} {v}")
    if r['descartes']:
        print("descartadas:")
        for k, v in r['descartes'].most_common():
            print(f"   {k:<34} {v}")
    if r['fechadas']:
        print("encerradas:")
        for k, v in r['fechadas'].most_common():
            print(f"   {k:<34} {v}")
    if r['reconciliadas']:
        print(f"reconciliadas ........ {r['reconciliadas']}")
    if r.get('rotas_tracadas') or r.get('rotas_falhas'):
        print(f"rotas ORS ............ {r.get('rotas_tracadas', 0)} traçada(s), "
              f"{r.get('rotas_falhas', 0)} falha(s)")
    if r['detalhe']:
        print(f"\ndetalhe ({len(r['detalhe'])}):")
        for d in r['detalhe'][:60]:
            print(f"   {d}")


if __name__ == '__main__':
    import argparse
    import sys
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

    ap = argparse.ArgumentParser(description='Lançamento automático de embarques (manifesto SSW)')
    ap.add_argument('--dia', help='fim da janela, YYYY-MM-DD (default: hoje - EMBARQUES_AUTO_DEFASAGEM)')
    ap.add_argument('--dry-run', action='store_true', help='não grava nada; só mostra o que faria')
    args = ap.parse_args()

    dia = None
    if args.dia:
        dia = datetime.strptime(args.dia, '%Y-%m-%d').date()

    if not args.dry_run and not ligado():
        print('EMBARQUES_AUTO=false — motor desligado. Use --dry-run para simular.')
        sys.exit(0)

    _imprimir(executar(dia=dia, dry_run=args.dry_run))
