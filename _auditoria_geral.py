# -*- coding: utf-8 -*-
"""AUDITORIA GERAL — roda em TODA a base o que ate agora se fazia abrindo carga a carga.

Motivo de existir: a auditoria anterior fazia UMA pergunta ("chegou antes de fechar?") e por
isso dizia "176 OK" enquanto uma inspecao visual achava defeito em quase toda carga aberta.
Aqui cada carga passa por uma BATERIA de invariantes, e qualquer um que falhe vira achado.

Cada achado tem codigo, gravidade e uma frase que diz o que esta errado E como se prova.

  SENSOR      quem mede a viagem existe e fala?
  VINCULO     o documento bate com a realidade fisica?
  CICLO       saida/chegada foram registradas quando o GPS mostra?
  FECHAMENTO  fechou com prova? no instante certo? deixou de fechar?
  DOCUMENTO   rota, manifesto, campos de apoio

Tolerancia de metropole: chegada tambem vale se a carreta PAROU >= PARADA_MIN_H dentro de
RAIO_METRO do centroide — o CD fica fora do raio de 20 km em regiao metropolitana.

    python -X utf8 _auditoria_geral.py                    # agosto+setembro
    python -X utf8 _auditoria_geral.py --desde 2026-08-01 --ate 2026-09-08
    python -X utf8 _auditoria_geral.py --csv saida.csv
Nao grava nada no banco.
"""
import os, sys, csv, argparse
from collections import defaultdict, Counter
from datetime import datetime, timedelta, time as _time
sys.path.insert(0, r'c:/Phyton-Projetos/Tabela Auditoria')
os.chdir(r'c:/Phyton-Projetos/Tabela Auditoria')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import psycopg2
from dotenv import load_dotenv
load_dotenv('.env')
import geocoding, placas as pl

RAIO_CHEGADA = 20.0      # raio do centroide que conta como "no destino"
RAIO_METRO = 60.0        # tolerancia de metropole, so vale com parada
PARADA_MIN_H = 2.0       # parada que prova chegada (passagem dura minutos)
RAIO_ORIGEM = 30.0       # saiu da origem
TOL_H = 12.0             # manifesto nao tem hora: 00:00 vira tolerancia
DWELL_H = 24.0           # regra de entrega por permanencia
GAP_ALERTA_H = 24.0      # buraco de sinal que compromete o julgamento
PARADA_ABERTA_D = 5      # carga aberta sem sair da origem ha N dias
HORIZONTE_D = 14         # depois disso a evidencia nao muda mais
VEL_MAX_CRIVEL = 100.0   # km/h medios: acima disso o par saida/chegada nao descreve a viagem

ap = argparse.ArgumentParser()
ap.add_argument('--desde', default='2026-08-01')
ap.add_argument('--ate', default='2026-09-08')
ap.add_argument('--csv', default='_auditoria_geral.csv')
A = ap.parse_args()
HOJE = datetime.now()

cn = psycopg2.connect(host=os.getenv('DB_HOST'), port=os.getenv('DB_PORT'), dbname=os.getenv('DB_NAME'),
                      user=os.getenv('DB_USER'), password=os.getenv('DB_PASSWORD'))
cur = cn.cursor()

cur.execute("""SELECT c.id,c.numero,c.status,c.encerrada_motivo,COALESCE(c.entregue_auto,FALSE),
                      COALESCE(c.saida_auto,FALSE),c.data_carregamento,c.data_saida_real,
                      c.inicio_viagem,c.no_local_desde,c.data_conclusao,c.origem_cidade,
                      c.origem_latitude,c.origem_longitude,c.cavalo_placa,c.carreta1_placa,
                      c.carreta2_placa,c.manifesto_origem,COALESCE(c.viagem_vazia,FALSE),
                      c.rota_planejada_polyline IS NOT NULL,COALESCE(c.criada_por_robo,FALSE),
                      c.distancia_planejada_km,
                      d.cidade,d.uf,d.latitude,d.longitude
                 FROM embarques_cargas c
                 LEFT JOIN embarques_cargas_destinos d ON d.carga_id=c.id
                      AND d.ordem=(SELECT MIN(ordem) FROM embarques_cargas_destinos x WHERE x.carga_id=c.id)
                WHERE c.data_carregamento BETWEEN %s AND %s
                ORDER BY c.data_carregamento, c.id""", (A.desde, A.ate))
CARGAS = cur.fetchall()

# indice: proxima carga de cada placa (serve de "manifesto novo", ja que 1 carga = 1 manifesto)
prox_carreta, prox_cavalo = defaultdict(list), defaultdict(list)
for r in CARGAS:
    if r[18]:
        continue
    if r[15]:
        prox_carreta[pl.mercosul(r[15])].append((r[6], r[1]))
    if r[14]:
        prox_cavalo[pl.mercosul(r[14])].append((r[6], r[1]))
for d in (prox_carreta, prox_cavalo):
    for k in d:
        d[k].sort()

_cache = {}
def pontos(placa, ini, fim):
    if not placa:
        return []
    k = (pl.mercosul(placa), ini, fim)
    if k in _cache:
        return _cache[k]
    cur.execute("""SELECT data_posicao,latitude,longitude,velocidade
                     FROM embarques_posicoes_historico
                    WHERE placa=ANY(%s) AND data_posicao>=%s AND data_posicao<%s
                    ORDER BY data_posicao""",
                (pl.grafias(str(placa).strip().upper()), ini, fim))
    v = [(d, float(la), float(ln), vel) for d, la, ln, vel in cur.fetchall()
         if la is not None and ln is not None]
    _cache[k] = v
    return v

def dists(pts, lat, lng):
    out = []
    for d, la, ln, vel in pts:
        k = geocoding.km_entre(la, ln, lat, lng)
        if k is not None:
            out.append((d, k))
    return out

def chegada(dd):
    """(instante, como) da chegada — raio normal, ou metropole com parada longa."""
    c = next((d for d, k in dd if k <= RAIO_CHEGADA), None)
    if c:
        return c, 'raio'
    perto = [(d, k) for d, k in dd if k <= RAIO_METRO]
    if len(perto) > 1:
        h = (perto[-1][0] - perto[0][0]).total_seconds() / 3600
        if h >= PARADA_MIN_H:
            return perto[0][0], f'metropole {min(k for _, k in perto):.0f}km/{h:.0f}h'
    return None, None

achados = []
def add(num, cod, grav, msg, extra=''):
    achados.append({'carga': num, 'codigo': cod, 'gravidade': grav, 'achado': msg, 'prova': extra})

for (cid, num, status, motivo, auto, saida_auto, dcarg, dsaida, inicio, nolocal, dconc,
     ocid, ola, oln, cav, c1, c2, man, vazia, tem_rota, robo, dist_plan, dcid, duf, dla, dln) in CARGAS:

    base = datetime.combine(dcarg, _time()) - timedelta(hours=12)
    # MESMA janela do motor (_robo_atemporal.JANELA_FUTURO_D). Enquanto o auditor cortava em
    # conclusao+2d e o motor varria carregamento+20d, os dois liam series diferentes e
    # discordavam para sempre: o motor gravava uma chegada que o auditor nao encontrava.
    fim = min(HOJE, datetime.combine(dcarg, _time()) + timedelta(days=20))
    ativo = status in ('Aberta', 'Em rota', 'No destino', 'Desengatada')
    idade_d = (HOJE - datetime.combine(dcarg, _time())).days

    # ── COERENCIA TEMPORAL — nao depende de GPS, e aritmetica de datas, mas era o que
    # obrigava a abrir carga por carga: o mapa monta o trajeto ate a data_conclusao, entao
    # conclusao anterior a saida da janela negativa e o painel mostra "KM percorridos 0.0"
    # com o caminhao visivelmente do outro lado do pais.
    if nolocal and dsaida and nolocal < dsaida:
        add(num, 'T1', 'alta',
            f'chegada ({str(nolocal)[:16]}) ANTERIOR a saida ({str(dsaida)[:16]}) — impossivel')
    if dconc and nolocal and dconc < nolocal:
        add(num, 'T2', 'alta',
            f'conclusao ({str(dconc)[:16]}) ANTERIOR a chegada ({str(nolocal)[:16]})')
    if dconc and dsaida and dconc < dsaida:
        add(num, 'T3', 'alta',
            f'conclusao ({str(dconc)[:16]}) ANTERIOR a saida ({str(dsaida)[:16]}) — '
            f'a janela do trajeto fecha vazia e o km zera na tela')
    if dconc and dconc.date() < dcarg:
        add(num, 'T4', 'alta', f'conclusao ({str(dconc)[:10]}) ANTERIOR ao carregamento ({dcarg})')
    # T5 — VELOCIDADE IMPLICITA. O par saida/chegada define uma velocidade media; se ela
    # for impossivel para um caminhao, um dos dois instantes esta errado. Pega o que nenhuma
    # regra geometrica pega: a C-2026-000642 tinha saida 13:41 e chegada 14:01 para 94 km
    # de rota — 282 km/h. Nao ha caminhao assim, entao a viagem nao e essa.
    if dsaida and nolocal and nolocal > dsaida and dist_plan:
        _h = (nolocal - dsaida).total_seconds() / 3600.0
        if _h > 0:
            _v = float(dist_plan) / _h
            if _v > VEL_MAX_CRIVEL:
                add(num, 'T5', 'alta',
                    f'velocidade impossivel: {dist_plan:.0f} km em {_h:.1f} h = {_v:.0f} km/h '
                    f'— saida ou chegada esta errada')

    # ── DOCUMENTO
    if not tem_rota and not vazia:
        add(num, 'D1', 'baixa', 'sem rota planejada (linha do mapa nao desenha)')
    if robo and not vazia and not man:
        add(num, 'D2', 'media', 'carga do robo sem manifesto_origem (trava anti-duplicata cega)')
    if not vazia and dla is None:
        add(num, 'D3', 'alta', 'destino sem coordenada — nenhuma regra de chegada pode julgar')

    # ── SENSOR
    p1 = pontos(c1, base, fim) if c1 else []
    pc = pontos(cav, base, fim) if cav else []
    p2 = pontos(c2, base, fim) if c2 else []
    principal, papel = (p1, 'carreta1') if p1 else ((p2, 'carreta2') if p2 else (pc, 'cavalo'))
    if not principal:
        add(num, 'S1', 'alta', 'CEGA: nenhuma placa da carga transmitiu no periodo',
            f'cavalo={cav} carreta={c1}')
        continue
    if len(principal) > 1:
        gap = max((principal[i][0] - principal[i-1][0]).total_seconds()/3600
                  for i in range(1, len(principal)))
        if gap >= GAP_ALERTA_H:
            add(num, 'S2', 'baixa', f'buraco de sinal de {gap:.0f} h no rastreio ({papel})')
    if papel != 'cavalo' and pc and len(pc) > 3 * max(1, len(principal)):
        add(num, 'S3', 'baixa',
            f'cavalo tem {len(pc)} pontos contra {len(principal)} da carreta — sensor mais pobre em uso',
            f'{papel}={c1 or c2}')

    # ── VINCULO com a origem
    piso = None            # instante a partir do qual uma chegada e crivel (mesma regra do motor)
    if ola is not None:
        d0 = dists(principal, float(ola), float(oln))
        if d0:
            mino = min(k for _, k in d0)
            # Tolerancia de metropole tambem na ORIGEM: o patio/CD fica fora do centroide,
            # entao parada longa por perto vale como presenca (mesmo criterio do destino).
            perto_o = [(d, k) for d, k in d0 if k <= RAIO_METRO]
            horas_o = ((perto_o[-1][0] - perto_o[0][0]).total_seconds()/3600
                       if len(perto_o) > 1 else 0.0)
            esteve = mino <= RAIO_ORIGEM or (mino <= RAIO_METRO and horas_o >= PARADA_MIN_H)
            if not esteve:
                # o cavalo esteve la? entao a carreta do documento e que esta errada
                dc = dists(pc, float(ola), float(oln)) if pc else []
                quem = ('so o CAVALO esteve na origem — carreta errada no documento'
                        if dc and min(k for _, k in dc) <= RAIO_ORIGEM
                        else 'nem carreta nem cavalo estiveram na origem')
                add(num, 'V1', 'alta',
                    f'a placa rastreada NUNCA esteve na origem ({mino:.0f} km no minimo) — {quem}',
                    f'origem={ocid}')
            else:
                # PRIMEIRA presenca na origem, nao a de menor distancia: numa viagem que
                # volta para a base (A->B->A) o ponto mais proximo da origem e a VOLTA, e
                # ancorar ali joga a chegada em B para antes do piso, descartando-a.
                # (Mesma armadilha que o `indice_saida_origem` do geocoding ja documenta.)
                t_org = next((d for d, k in d0 if k <= RAIO_ORIGEM), None) or \
                        next(d for d, k in d0 if k == mino)
                saiu = next((d for d, k in d0 if k > RAIO_ORIGEM and d > t_org), None)
                piso = saiu or t_org
                # ── CICLO: saida
                if saiu and not dsaida:
                    add(num, 'C1', 'media', f'saiu da origem em {str(saiu)[:16]} e a saida NAO foi gravada')
                if dsaida and not saiu:
                    # Saida DIGITADA por pessoa nao e defeito do motor — e lancamento manual
                    # sem lastro de GPS. Separado, porque a acao e outra (falar com quem lancou).
                    if saida_auto:
                        add(num, 'C2', 'alta',
                            'saida AUTOMATICA gravada mas o GPS nao mostra a carreta deixando a origem')
                    else:
                        add(num, 'C5', 'baixa',
                            'saida digitada a mao, sem lastro no GPS (nao e defeito do motor)')
                if not saiu and ativo and idade_d >= PARADA_ABERTA_D:
                    add(num, 'C4', 'media',
                        f'carga ativa ha {idade_d} dias e a placa nunca saiu da origem')

    if dla is None:
        continue
    dd = dists(principal, float(dla), float(dln))
    if not dd:
        continue
    # Mesma regra do motor: chegada so conta DEPOIS de ter saido (ou de ter estado na
    # origem). Sem esse piso, o auditor acusa chegada em ponto anterior a partida — foi
    # o que gerou 18 falsos C3 quando o robo passou a exigir a ordem.
    # TETO, simetrico do piso: a viagem nao pode ter chegada depois de a carreta ja ter
    # comecado outra. Mesma regra do motor — reguas diferentes geram achado fantasma.
    teto = None
    if c1:
        _seg = [dt for dt, nm in prox_carreta.get(pl.mercosul(c1), []) if dt > dcarg]
        if _seg:
            teto = datetime.combine(min(_seg), _time()) + timedelta(days=1)
    dd_validos = [(d, k) for d, k in dd
                  if (piso is None or d >= piso) and (teto is None or d <= teto)]
    cheg, como = chegada(dd_validos) if dd_validos else (None, None)
    mind = min(k for _, k in dd)

    # ── CICLO: chegada
    if cheg and not nolocal:
        add(num, 'C3', 'media', f'chegou ao destino em {str(cheg)[:16]} e a chegada NAO foi gravada',
            f'{como} · destino={dcid}')

    # ── FECHAMENTO
    if status in ('Entregue', 'Cancelada') and dconc:
        conc = dconc if isinstance(dconc, datetime) else datetime.combine(dconc, _time())
        if not cheg:
            # fechou sem nenhuma prova de chegada
            so_cavalo = False
            depois = [n for dt_, n in prox_cavalo.get(pl.mercosul(cav), []) if dt_ > dcarg]
            depois_car = [n for dt_, n in prox_carreta.get(pl.mercosul(c1 or ''), []) if dt_ > dcarg]
            if depois and not depois_car:
                so_cavalo = True
            if so_cavalo:
                add(num, 'F5', 'alta',
                    f'ENTREGUE mas a carreta nunca chegou ({mind:.0f} km) e so o CAVALO pegou carga nova '
                    f'— e desengate, nao entrega', f'proxima do cavalo={depois[0]}')
            else:
                add(num, 'F1', 'alta',
                    f'FECHADA SEM PROVA: aproximacao maxima do destino {mind:.0f} km',
                    f'motivo={motivo or "(perdido no import)"} · destino={dcid}')
        else:
            atraso = (cheg - conc).total_seconds() / 3600
            if atraso > TOL_H:
                add(num, 'F2', 'alta',
                    f'FECHADA CEDO: fechou {str(conc)[:16]} e so chegou {str(cheg)[:16]} '
                    f'({atraso/24:.1f} dias depois)')
            else:
                # A conclusao legitima e um destes dois instantes: a SAIDA do destino, ou
                # chegada + DWELL_H. Nao ha teto fixo — um caminhao pode ficar tres dias na
                # fila de descarga e sair no terceiro, e fechar ali esta certo. (A 1a versao
                # desta regra usava teto de 36 h e acusava 69 cargas corretas.)
                depois = [(d, k) for d, k in dd if d > cheg]
                saiu_dst = next((d for d, k in depois if k > RAIO_ORIGEM), None)
                esperados = [x for x in (saiu_dst, cheg + timedelta(hours=DWELL_H)) if x]
                erro = min(abs((conc - e).total_seconds()/3600) for e in esperados)
                if erro > TOL_H:
                    ref = 'saida do destino' if saiu_dst and abs((conc-saiu_dst).total_seconds()/3600) == erro \
                          else f'chegada+{DWELL_H:.0f}h'
                    add(num, 'F3', 'media',
                        f'RECORTE ERRADO: fechou {str(conc)[:16]}, {erro:.0f} h longe do '
                        f'instante com lastro ({ref})', f'chegada={str(cheg)[:16]}')
    elif ativo and cheg:
        h = (HOJE - cheg).total_seconds() / 3600
        if h > DWELL_H:
            add(num, 'F4', 'media',
                f'NAO FECHOU: chegou {str(cheg)[:16]} ha {h/24:.1f} dias e segue "{status}"', como)
    if ativo and idade_d > HORIZONTE_D:
        add(num, 'F6', 'media', f'aberta ha {idade_d} dias — passou do horizonte de {HORIZONTE_D}')

# ── relatorio
por_cod = Counter(a['codigo'] for a in achados)
por_grav = Counter(a['gravidade'] for a in achados)
cargas_com = len({a['carga'] for a in achados})
NOMES = {
    'S1': 'CEGA — nenhuma placa transmitiu', 'S2': 'buraco de sinal >= 24 h',
    'S3': 'cavalo mede melhor que a carreta em uso',
    'V1': 'placa rastreada nunca esteve na origem',
    'C1': 'saiu e a saida nao foi gravada', 'C2': 'saida gravada sem lastro no GPS',
    'C3': 'chegou e a chegada nao foi gravada', 'C4': 'ativa ha dias sem sair da origem',
    'F1': 'FECHADA SEM PROVA de chegada', 'F2': 'FECHADA CEDO (chegou depois)',
    'F3': 'recorte da conclusao errado', 'F4': 'chegou e nao fechou',
    'F5': 'entrega que era DESENGATE', 'F6': 'aberta alem do horizonte',
    'T5': 'velocidade implicita impossivel', 'T1': 'chegada anterior a saida', 'T2': 'conclusao anterior a chegada',
    'T3': 'conclusao anterior a saida (zera o km na tela)', 'T4': 'conclusao anterior ao carregamento',
    'C5': 'saida digitada a mao, sem lastro', 'D1': 'sem rota planejada', 'D2': 'sem manifesto_origem', 'D3': 'destino sem coordenada',
}
print(f'AUDITORIA GERAL — {len(CARGAS)} cargas entre {A.desde} e {A.ate}')
print(f'{cargas_com} cargas com pelo menos um achado ({cargas_com/max(1,len(CARGAS))*100:.0f}%) · '
      f'{len(achados)} achados no total')
print(f'gravidade: ' + ' · '.join(f'{g}={n}' for g, n in por_grav.most_common()))
print(f'\n{"cod":<5} {"n":>5}  {"grav":<6} descricao')
ordem = {'alta': 0, 'media': 1, 'baixa': 2}
grav_de = {a['codigo']: a['gravidade'] for a in achados}
for cod, n in sorted(por_cod.items(), key=lambda x: (ordem[grav_de[x[0]]], -x[1])):
    print(f'{cod:<5} {n:>5}  {grav_de[cod]:<6} {NOMES.get(cod, "")}')

print('\nOS 30 ACHADOS DE GRAVIDADE ALTA:')
altos = [a for a in achados if a['gravidade'] == 'alta']
for a in altos[:30]:
    print(f'   {a["carga"]:<15} {a["codigo"]:<4} {a["achado"]}')
if len(altos) > 30:
    print(f'   ... e mais {len(altos)-30}')

with open(A.csv, 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.DictWriter(f, ['carga', 'codigo', 'gravidade', 'achado', 'prova'], delimiter=';')
    w.writeheader(); w.writerows(achados)
print(f'\n-> {A.csv} ({len(achados)} linhas)')
cn.close()
