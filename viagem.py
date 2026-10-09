# -*- coding: utf-8 -*-
"""FICHA DA VIAGEM — a viagem inteira numa página (09/10/2026).

A VIAGEM é a da CARGA: nasce no primeiro manifesto e segue pelos trechos que levaram a mesma
mercadoria (as continuações). Na frente vem a perna vazia que trouxe o caminhão até a origem:
o vazio até a origem pertence à viagem que sai dela (decisão de 09/10/2026 — e é assim que o
agregado recebe: o vazio entra no CTRB da viagem SEGUINTE).

A ficha NÃO calcula nada novo. Cada número vem da tabela/função da aba dona:
  percurso e horários .... embarques_cargas (robô + motor atemporal)
  km rastreado ........... endpoint do trajeto (o mesmo do mapa), chamado pela tela
  ordem de coleta ........ embarques_programacao (fita)
  SM ..................... embarques_sm (aba SM) + insignia_sm / insignia_macros
  CIOT ................... ciot_pendencias (aba CIOT)
  excesso de velocidade .. pgr_eventos (aba PGR)
  receita ................ CTes do PRIMEIRO manifesto, sem rateio (BI) + complementos ligados
  custo de frota ......... _custo_km_cliente / _custo_viagem_km (aba Veículos, regra aprovada)
  frete pago ............. Auditoria Receita + pagamentos do 477 (adiantamento e saldo)

Duas leituras, porque o BI é lento e não pode segurar a página:
  montar(cur, ref)          banco local: percurso, linha do tempo, conformidade (rápido)
  bi(dados, ctx, ...)       Power BI: documentos e dinheiro (cache por viagem)

Horários: o que vem do GPS, do robô e da Insignia é UTC; ordem de coleta e PGR são BRT.
Tudo sai daqui em ISO UTC com 'Z' — a tela converte para Brasília.
"""
import os
import re
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import placas as pl

BRT = timedelta(hours=3)
ATIVAS = ('Aberta', 'Em rota', 'No destino', 'Desengatada')

# Tabela do analista (09/10/2026). O vazio do agregado é pago no CTRB da viagem seguinte.
TARIFA_VAZIO_AGREGADO = float(os.getenv('VIAGEM_TARIFA_VAZIO_AGREGADO', '3.50'))
TARIFA_CARREGADO = {'ES': 5.77, 'TRUCADO': 5.45, 'TOCO': 5.32}

# Km rastreado só vale quando o odômetro cobriu a viagem (régua de 09/10/2026; fora disso,
# o roteirizado). Medido em set/26: confiável em 58% das pernas vazias da frota.
KM_COBERTURA_MIN = 80
KM_GPS_MAX = 0.20
KM_TETO = 3.0

MOTIVO = {
    'gps_saiu_do_destino': 'saiu do cliente (GPS)',
    'gps_dwell_destino': '24 h no destino (GPS)',
    'sm_encerrada': 'fim de viagem na SM (Insignia)',
    'manifesto_novo': 'manifesto seguinte da carreta',
    'manifesto_novo_carreta': 'manifesto seguinte da carreta',
    'manifesto_novo_sem_gps': 'manifesto seguinte (sem GPS)',
    'sequencia_viagem': 'viagem seguinte',
    'desengate': 'desengate — seguiu em outro cavalo',
    'continuou_no_hub': 'continuou no hub',
    'reposicionamento': 'reposicionamento',
    'manifesto_cancelado': 'manifesto cancelado',
    'sem_prova_revisar': 'sem prova de chegada — revisar',
    'reemitido': 'manifesto reemitido',
    'baixa_ctrb': 'baixa do CTRB (regra antiga)',
}

SM_SEVERIDADE = {'alerta': 'alerta', 'atencao': 'atencao', 'pendente': 'neutro', 'ok': 'ok'}


def _doc(s):
    """Nº de documento sem espaço nem pontuação ('NOD 004856-9' = 'NOD004856-9')."""
    return re.sub(r'[^A-Za-z0-9]', '', str(s or '')).upper()


def _iso(t):
    if t is None:
        return None
    if isinstance(t, datetime):
        return t.replace(microsecond=0).isoformat() + 'Z'
    return t.isoformat()


def _de_brt(t):
    """Carimbo de Brasília sem fuso (ordem de coleta, PGR) → UTC sem fuso."""
    return t + BRT if isinstance(t, datetime) else None


def _h(a, b):
    return (a - b).total_seconds() / 3600.0 if (a and b) else None


def _num(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _tem_tabela(cur, nome):
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (nome,))
    return bool(cur.fetchone()[0])


# ── as cargas ───────────────────────────────────────────────────────────────────────────────
_CAMPOS = ('id', 'numero', 'tipo_operacao', 'status', 'viagem_vazia', 'cliente_nome', 'embarcador',
           'origem_cidade', 'origem_uf', 'origem_latitude', 'origem_longitude', 'cavalo_placa',
           'carreta1_placa', 'carreta2_placa', 'motorista_nome', 'manifesto_origem', 'ctrb_origem',
           'data_carregamento', 'criado_em', 'criada_por_robo', 'data_saida_real', 'inicio_viagem',
           'no_local_desde', 'no_local_fonte', 'data_conclusao', 'encerrada_motivo', 'desengatada_em',
           'continua_em', 'distancia_planejada_km', 'observacoes', 'descarga_cavalo_placa',
           'descarga_motorista_nome')


def _cargas(cur, ids):
    ids = [i for i in ids if i]
    if not ids:
        return {}
    cur.execute(f"SELECT {', '.join(_CAMPOS)} FROM embarques_cargas WHERE id = ANY(%s)", (ids,))
    out = {r[0]: dict(zip(_CAMPOS, r)) for r in cur.fetchall()}
    cur.execute("""SELECT carga_id, ordem, cidade, uf, latitude, longitude, data_agendamento
                     FROM embarques_cargas_destinos WHERE carga_id = ANY(%s) ORDER BY carga_id, ordem""", (ids,))
    for cid, ordem, cidade, uf, lat, lng, ag in cur.fetchall():
        out[cid].setdefault('destinos', []).append(
            {'cidade': cidade, 'uf': uf, 'lat': _num(lat), 'lng': _num(lng), 'agendamento': _iso(ag)})
    return out


def _id_do_numero(cur, numero):
    cur.execute("SELECT id FROM embarques_cargas WHERE numero = %s", (numero,))
    r = cur.fetchone()
    return r[0] if r else None


def resolver(cur, ref):
    """Qualquer número que identifique a viagem → id de uma carga. Aceita o nº da carga ou da
    perna vazia, o manifesto, o CTRB (com ou sem dígito), o CTe (pela fita) e a placa (a carga
    mais recente dela)."""
    bruto = (ref or '').strip().upper()
    if not bruto:
        return None
    if re.fullmatch(r'[CV]-\d{4}-\d+', bruto):
        return _id_do_numero(cur, bruto)
    if bruto.isdigit():
        cur.execute("SELECT id FROM embarques_cargas WHERE id = %s", (int(bruto),))
        r = cur.fetchone()
        return r[0] if r else None
    d = _doc(bruto)
    # manifesto ou CTRB (a carga guarda o CTRB sem o dígito: 'NOD004822' para 'NOD004822-4')
    cur.execute("""SELECT id FROM embarques_cargas
                    WHERE NOT COALESCE(viagem_vazia, FALSE) AND status <> 'Cancelada'
                      AND (regexp_replace(upper(manifesto_origem), '[^A-Z0-9]', '', 'g') = %s
                           OR regexp_replace(upper(ctrb_origem), '[^A-Z0-9]', '', 'g') IN (%s, left(%s, length(%s) - 1)))
                    ORDER BY data_carregamento DESC, id DESC LIMIT 1""", (d, d, d, d))
    r = cur.fetchone()
    if r:
        return r[0]
    # placa: a viagem mais recente dela
    if 6 <= len(d) <= 8 and re.fullmatch(r'[A-Z]{3}\d[A-Z0-9]\d{2}', d):
        g = pl.grafias(d)
        cur.execute("""SELECT id FROM embarques_cargas
                        WHERE NOT COALESCE(viagem_vazia, FALSE) AND status <> 'Cancelada'
                          AND (cavalo_placa = ANY(%s) OR carreta1_placa = ANY(%s) OR carreta2_placa = ANY(%s))
                        ORDER BY data_carregamento DESC, id DESC LIMIT 1""", (g, g, g))
        r = cur.fetchone()
        if r:
            return r[0]
    # CTe → o manifesto em que nasceu (fita do BI)
    if _tem_tabela(cur, 'fita_documentos'):
        cur.execute("""SELECT payload->>'primeiro_manifesto' FROM fita_documentos
                        WHERE fonte = 'cte' AND regexp_replace(upper(chave), '[^A-Z0-9]', '', 'g') = %s
                        ORDER BY rodada DESC LIMIT 1""", (d,))
        r = cur.fetchone()
        if r and r[0]:
            return resolver(cur, r[0])
    return None


def ancora(cur, carga_id):
    """A carga que dá nome à viagem: a perna vazia leva à carga SEGUINTE; a continuação sobe
    pela cadeia (`continua_em` aponta da primeira para a seguinte) até a primeira."""
    c = _cargas(cur, [carga_id]).get(carga_id)
    if not c:
        return None
    if c['viagem_vazia']:
        m = re.search(r'->\s*([CV]-\d{4}-\d+)', c['observacoes'] or '')
        prox = _id_do_numero(cur, m.group(1)) if m else None
        return prox or carga_id
    atual = carga_id
    for _ in range(8):
        cur.execute("SELECT id FROM embarques_cargas WHERE continua_em = %s AND status <> 'Cancelada' "
                    "ORDER BY id LIMIT 1", (atual,))
        r = cur.fetchone()
        if not r or r[0] == atual:
            break
        atual = r[0]
    return atual


def _janela(t):
    """Início e fim da janela de um trecho, para casar eventos (SM, PGR, log)."""
    ini = t['saida'] or t['criado'] or (datetime.combine(t['carregamento'], datetime.min.time())
                                        if t['carregamento'] else None)
    fim = t['conclusao'] or datetime.utcnow()
    return ini, fim


def _trecho(c, papel):
    saida = c['data_saida_real'] or c['inicio_viagem']
    chegada = c['no_local_desde']
    concl = c['data_conclusao']
    obs = c['observacoes'] or ''
    destinos = c.get('destinos') or []
    return {
        'id': c['id'], 'numero': c['numero'], 'papel': papel, 'tipo': c['tipo_operacao'],
        'status': c['status'], 'cliente': c['cliente_nome'], 'embarcador': c['embarcador'],
        'origem': {'cidade': c['origem_cidade'], 'uf': c['origem_uf'],
                   'lat': _num(c['origem_latitude']), 'lng': _num(c['origem_longitude'])},
        'destinos': destinos,
        'cavalo': c['cavalo_placa'], 'carreta1': c['carreta1_placa'], 'carreta2': c['carreta2_placa'],
        'motorista': c['motorista_nome'], 'manifesto': c['manifesto_origem'], 'ctrb': c['ctrb_origem'],
        'carregamento': c['data_carregamento'], 'criado': c['criado_em'], 'saida': saida,
        'chegada': chegada, 'chegada_fonte': c['no_local_fonte'], 'conclusao': concl,
        'motivo': c['encerrada_motivo'], 'desengate': c['desengatada_em'],
        'km_planejado': _num(c['distancia_planejada_km']), 'km_ate_passagem': None, 'rota_ate_o_fim': False,
        'km_rota_viagem': _num(c['distancia_planejada_km']), 'robo': bool(c['criada_por_robo']),
        'lacuna': 'LACUNA' in obs.upper(), 'sem_gps_janela': 'SEM GPS' in obs.upper(),
        'descarga_cavalo': c['descarga_cavalo_placa'], 'descarga_motorista': c['descarga_motorista_nome'],
        'continua_em': c['continua_em'],
    }


def trechos(cur, aid):
    """① a perna vazia que trouxe o caminhão · ② a carga da âncora · ③… as continuações."""
    base = _cargas(cur, [aid]).get(aid)
    if not base:
        return []
    out = []
    cur.execute("""SELECT id FROM embarques_cargas
                    WHERE viagem_vazia AND status <> 'Cancelada' AND observacoes LIKE %s
                    ORDER BY id DESC LIMIT 1""", (f"%-> {base['numero']}%",))
    r = cur.fetchone()
    if r:
        v = _cargas(cur, [r[0]]).get(r[0])
        if v:
            out.append(_trecho(v, 'vazio'))
    out.append(_trecho(base, 'carregado'))
    vistos, atual = {aid}, base
    for _ in range(8):
        nxt = atual.get('continua_em')
        if not nxt or nxt in vistos:
            break
        c = _cargas(cur, [nxt]).get(nxt)
        if not c or c['status'] == 'Cancelada':
            break
        out.append(_trecho(c, 'continuacao'))
        vistos.add(nxt)
        atual = c
    return out


# ── linha do tempo ──────────────────────────────────────────────────────────────────────────
def _ev(lista, t, tipo, titulo, detalhe=None, trecho=None, marco=True, nivel=None):
    if t is None:
        return
    lista.append({'t': _iso(t), 'tipo': tipo, 'titulo': titulo, 'detalhe': detalhe,
                  'trecho': trecho, 'marco': marco, 'nivel': nivel})


def _local(t, qual='destino'):
    if qual == 'origem':
        o = t['origem']
        return f"{o['cidade']}/{o['uf']}" if o.get('cidade') else '?'
    d = (t['destinos'] or [{}])[-1]
    return f"{d.get('cidade')}/{d.get('uf')}" if d.get('cidade') else '?'


def _ordens(cur, legs):
    if not _tem_tabela(cur, 'embarques_programacao'):
        return []
    cur.execute("""SELECT carga_id, coleta_origem, cadastrada_em, cadastrada_por, comandada_em, comandada_por,
                          coletada_em, cancelada_em, agendamento, obs, embarcador, cavalo, carreta, motorista,
                          reme_nome, dest_nome, dest_cidade, dest_uf, estado
                     FROM embarques_programacao WHERE carga_id = ANY(%s)""", ([t['id'] for t in legs],))
    cols = ('carga_id', 'coleta', 'cadastrada', 'cadastrada_por', 'comandada', 'comandada_por', 'coletada',
            'cancelada', 'agendamento', 'obs', 'embarcador', 'cavalo', 'carreta', 'motorista', 'remetente',
            'destinatario', 'dest_cidade', 'dest_uf', 'estado')
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _sms(cur, legs):
    """SMs da Insignia do cavalo de cada trecho, abertas perto da janela dele."""
    if not _tem_tabela(cur, 'insignia_sm'):
        return [], []
    sms, macros = [], []
    for t in legs:
        if t['papel'] == 'vazio' or not t['cavalo']:
            continue
        ini, fim = _janela(t)
        if not ini:
            continue
        cur.execute("""SELECT sm, status, criada_em, inicio, encerrada_em, valor_carga, link_sm
                         FROM insignia_sm WHERE placa_cavalo_chave = %s
                          AND criada_em BETWEEN %s AND %s ORDER BY criada_em""",
                    (pl.mercosul(t['cavalo']), ini - timedelta(days=3), fim + timedelta(days=1)))
        for sm, st, cri, ini_sm, enc, val, link in cur.fetchall():
            sms.append({'sm': sm, 'status': st, 'criada': cri, 'inicio': ini_sm, 'encerrada': enc,
                        'valor': _num(val), 'link': link, 'trecho': t['numero']})
    if sms and _tem_tabela(cur, 'insignia_macros'):
        cur.execute("SELECT sm, em, nome, referencia FROM insignia_macros WHERE sm = ANY(%s) ORDER BY em",
                    ([s['sm'] for s in sms],))
        trecho_da = {s['sm']: s['trecho'] for s in sms}
        for sm, em, nome, ref in cur.fetchall():
            macros.append({'sm': sm, 'em': em, 'nome': nome, 'referencia': ref, 'trecho': trecho_da.get(sm)})
    return sms, macros


def _pgr(cur, legs):
    if not _tem_tabela(cur, 'pgr_eventos'):
        return None
    out = []
    for t in legs:
        ini, fim = _janela(t)
        placas_ = [p for p in (t['cavalo'], t['carreta1']) if p]
        if not ini or not placas_:
            continue
        g = sorted({x for p in placas_ for x in pl.grafias(p)})
        # o PGR grava em BRT: a janela (UTC) vira BRT para comparar
        cur.execute("""SELECT placa, ini, vel_max, cidade, uf, endereco, situacao_carga
                         FROM pgr_eventos WHERE placa = ANY(%s) AND ini BETWEEN %s AND %s ORDER BY ini""",
                    (g, ini - BRT, fim - BRT))
        for placa, ini_e, vel, cid, uf, end, sit in cur.fetchall():
            out.append({'placa': placa, 't': _de_brt(ini_e), 'vel': vel, 'local': f'{cid}/{uf}' if cid else end,
                        'situacao': sit, 'trecho': t['numero']})
    return out


def _ciot(cur, legs):
    if not _tem_tabela(cur, 'ciot_pendencias'):
        return None
    out = []
    for t in legs:
        if t['papel'] == 'vazio':
            continue
        m, c = _doc(t['manifesto']), _doc(t['ctrb'])
        if not (m or c):
            continue
        cur.execute("""SELECT tipo, documento, ctrb, manifesto, detalhe, primeiro_visto, resolvido_em
                         FROM ciot_pendencias
                        WHERE (%s <> '' AND regexp_replace(upper(coalesce(manifesto, '')), '[^A-Z0-9]', '', 'g') = %s)
                           OR (%s <> '' AND regexp_replace(upper(coalesce(ctrb, '')), '[^A-Z0-9]', '', 'g') LIKE %s)""",
                    (m, m, c, c + '%'))
        for tipo, docm, ctrb, man, det, pv, res in cur.fetchall():
            out.append({'tipo': tipo, 'documento': docm, 'ctrb': ctrb, 'manifesto': man, 'detalhe': det,
                        'desde': pv, 'resolvido': res, 'trecho': t['numero']})
    return out


def _sm_aba(cur, legs):
    """O estado da aba SM (conformidade com a regra da gerenciadora), por manifesto."""
    if not _tem_tabela(cur, 'embarques_sm'):
        return []
    ms = [_doc(t['manifesto']) for t in legs if t['papel'] != 'vazio' and t['manifesto']]
    if not ms:
        return []
    cur.execute("SELECT manifesto, estado, severidade, detalhe, sm, sm_status FROM embarques_sm WHERE manifesto = ANY(%s)",
                (ms,))
    return [dict(zip(('manifesto', 'estado', 'severidade', 'detalhe', 'sm', 'sm_status'), r)) for r in cur.fetchall()]


def _log(cur, legs):
    cur.execute("""SELECT carga_id, editado_em, usuario_nome, campo, valor_anterior, valor_novo
                     FROM embarques_cargas_log WHERE carga_id = ANY(%s) ORDER BY editado_em""",
                ([t['id'] for t in legs],))
    num = {t['id']: t['numero'] for t in legs}
    return [{'trecho': num.get(r[0]), 't': r[1], 'quem': r[2], 'campo': r[3], 'de': r[4], 'para': r[5]}
            for r in cur.fetchall()]


def _linha_do_tempo(legs, ordens, sms, macros, pgr, ciot, log, livre):
    ev = []
    for o in ordens:
        tr = next((t['numero'] for t in legs if t['id'] == o['carga_id']), None)
        _ev(ev, _de_brt(o['cadastrada']), 'ordem', f"Ordem de coleta {o['coleta']} cadastrada",
            ' · '.join(x for x in (o['embarcador'], o['remetente']) if x), tr)
        if o['comandada']:
            alvo = ' / '.join(x for x in (o['cavalo'], o['carreta']) if x)
            _ev(ev, _de_brt(o['comandada']), 'ordem', f"Ordem comandada{' → ' + alvo if alvo else ''}",
                o['motorista'], tr)
        _ev(ev, _de_brt(o['coletada']), 'ordem', 'Coleta registrada no SSW', None, tr, marco=False)
        _ev(ev, _de_brt(o['cancelada']), 'ordem', 'Ordem cancelada', None, tr)
    for t in legs:
        n = t['numero']
        if t['papel'] == 'vazio':
            _ev(ev, t['saida'], 'gps', f"Saiu vazia de {_local(t, 'origem')}",
                f"{t['km_planejado'] or 0:.0f} km de rota até a origem da carga", n)
            # a conclusão da perna é a saída da carga seguinte — chegada só se o GPS viu
            _ev(ev, t['chegada'], 'gps', f"Chegou vazia a {_local(t)}", 'origem da carga', n)
            continue
        if t['criado'] and t['saida']:
            atraso = _h(t['criado'], t['saida'])
            if atraso is not None and atraso > 2:
                _ev(ev, t['criado'], 'sistema', 'Carga criada no sistema',
                    f'{atraso:.0f} h depois da saída — o documento chegou depois do caminhão', n,
                    nivel='atencao' if atraso > 6 else None)
            else:
                _ev(ev, t['criado'], 'sistema', 'Carga criada no sistema', None, n, marco=False)
        else:
            _ev(ev, t['criado'], 'sistema', 'Carga criada no sistema', None, n, marco=False)
        rotulo = 'Saiu de' if t['papel'] == 'carregado' else 'Seguiu de'
        _ev(ev, t['saida'], 'gps', f"{rotulo} {_local(t, 'origem')}",
            ' + '.join(x for x in (t['cavalo'], t['carreta1'], t['carreta2']) if x), n)
        if t['chegada']:
            fonte = (t['chegada_fonte'] or '').split('(')[0].replace('+', ' + ')
            _ev(ev, t['chegada'], 'gps', f"Chegou a {_local(t)}", f'régua: {fonte}' if fonte else None, n)
        if t['desengate']:
            _ev(ev, t['desengate'], 'gps', 'Desengate',
                'a carreta seguiu com outro cavalo' if t['continua_em'] else 'carreta largada no destino', n)
        if t['conclusao'] and not (t['desengate'] and t['motivo'] == 'desengate'):
            perm = _h(t['conclusao'], t['chegada']) if t['motivo'] == 'gps_saiu_do_destino' else None
            titulo = 'Entregue' if t['status'] == 'Entregue' else f"Encerrada ({t['status']})"
            det = MOTIVO.get(t['motivo'] or '', 'confirmado à mão' if not t['motivo'] else t['motivo'])
            if perm is not None:
                det += f' · {perm:.1f} h no cliente'
            _ev(ev, t['conclusao'], 'gps', titulo, det, n, nivel='ok' if t['status'] == 'Entregue' else None)
    for s in sms:
        _ev(ev, s['criada'], 'sm', f"SM {s['sm']} pedida à gerenciadora",
            f"carga R$ {s['valor']:,.0f}".replace(',', '.') if s['valor'] else None, s['trecho'])
        _ev(ev, s['encerrada'], 'sm', f"SM {s['sm']} encerrada", None, s['trecho'], marco=False)
    for m in macros:
        chave = (m['nome'] or '').upper()
        _ev(ev, m['em'], 'sm', f"Motorista: {(m['nome'] or '').capitalize()}", m['referencia'], m['trecho'],
            marco=any(k in chave for k in ('CHEGADA', 'FIM', 'INICIO', 'INÍCIO')))
    for p in pgr or []:
        _ev(ev, p['t'], 'pgr', f"Excesso de velocidade: {p['vel']} km/h", f"{p['local'] or ''} · {p['situacao'] or ''}".strip(' ·'),
            p['trecho'], nivel='atencao')
    for c in ciot or []:
        _ev(ev, c['desde'], 'ciot', f"CIOT: {c['tipo']}", c['detalhe'], c['trecho'],
            nivel='ok' if c['resolvido'] else 'alerta')
        _ev(ev, c['resolvido'], 'ciot', 'CIOT regularizado', None, c['trecho'], nivel='ok')
    for lg in log:
        _ev(ev, lg['t'], 'robo', f"{lg['campo']}: {str(lg['de'] or '—')[:19]} → {str(lg['para'] or '—')[:19]}",
            lg['quem'], lg['trecho'], marco=False)
    if livre:
        _ev(ev, datetime.utcnow(), 'gps', f"Terceiro livre para retorno até {livre['ate_txt']}",
            livre.get('onde'), livre['trecho'], nivel='ok')
    ev.sort(key=lambda e: e['t'])
    return ev


# ── conformidade ────────────────────────────────────────────────────────────────────────────
def _selo(id_, rotulo, estado, texto, fonte):
    return {'id': id_, 'rotulo': rotulo, 'estado': estado, 'texto': texto, 'fonte': fonte}


def _conformidade(legs, ordens, sm_aba, ciot, pgr):
    carregados = [t for t in legs if t['papel'] != 'vazio']
    anc = carregados[0]
    selos = []
    if ordens:
        selos.append(_selo('ordem', 'Ordem de coleta', 'ok', ', '.join(o['coleta'] for o in ordens), 'Coletas (SSW 157)'))
    else:
        selos.append(_selo('ordem', 'Ordem de coleta', 'neutro' if anc['tipo'] == 'Terceiro' else 'atencao',
                           'nenhuma ordem ligada a esta viagem', 'Coletas (SSW 157)'))
    agendas = sorted(o['agendamento'] for o in ordens if o['agendamento'])
    if agendas:
        ag = agendas[0]
        cheg = next((t['chegada'] for t in reversed(carregados) if t['chegada']), None)
        if cheg:
            dia = (cheg - BRT).date()
            selos.append(_selo('agenda', 'Entrega agendada', 'ok' if dia <= ag else 'alerta',
                               f"agenda {ag:%d/%m} · chegou {dia:%d/%m}", 'Ordem de coleta (AGENDA)'))
        else:
            vencida = (datetime.utcnow() - BRT).date() > ag
            selos.append(_selo('agenda', 'Entrega agendada', 'alerta' if vencida else 'neutro',
                               f"agenda {ag:%d/%m}" + (' — vencida' if vencida else ''), 'Ordem de coleta (AGENDA)'))
    else:
        selos.append(_selo('agenda', 'Entrega agendada', 'neutro', 'sem agenda na ordem', 'Ordem de coleta (AGENDA)'))
    if sm_aba:
        pior = sorted(sm_aba, key=lambda s: ('alerta', 'atencao', 'pendente', 'ok').index(s['severidade'])
                      if s['severidade'] in ('alerta', 'atencao', 'pendente', 'ok') else 9)[0]
        selos.append(_selo('sm', 'SM', SM_SEVERIDADE.get(pior['severidade'], 'neutro'),
                           pior['detalhe'] or pior['estado'], 'Aba SM'))
    else:
        selos.append(_selo('sm', 'SM', 'neutro', 'sem avaliação (anterior à aba SM)', 'Aba SM'))
    if ciot is None:
        selos.append(_selo('ciot', 'CIOT', 'neutro', 'conferência indisponível', 'Aba CIOT'))
    else:
        abertas = [c for c in ciot if not c['resolvido']]
        if abertas:
            selos.append(_selo('ciot', 'CIOT', 'alerta', f"{len(abertas)} pendência(s): {abertas[0]['tipo']}", 'Aba CIOT'))
        elif ciot:
            selos.append(_selo('ciot', 'CIOT', 'ok', 'pendência regularizada', 'Aba CIOT'))
        elif anc['carregamento'] and anc['carregamento'] >= date(2026, 9, 1):
            selos.append(_selo('ciot', 'CIOT', 'ok', 'sem pendência', 'Aba CIOT'))
        else:
            selos.append(_selo('ciot', 'CIOT', 'neutro', 'antes do início da conferência (01/09)', 'Aba CIOT'))
    if pgr is None:
        selos.append(_selo('pgr', 'Velocidade', 'neutro', 'PGR indisponível', 'Aba PGR'))
    elif pgr:
        pico = max(p['vel'] or 0 for p in pgr)
        selos.append(_selo('pgr', 'Velocidade', 'atencao', f"{len(pgr)} excesso(s) · pico {pico} km/h", 'Aba PGR'))
    else:
        selos.append(_selo('pgr', 'Velocidade', 'ok', 'nenhum excesso acima de 95 km/h', 'Aba PGR'))
    atrasos = [_h(t['criado'], t['saida']) for t in carregados if t['criado'] and t['saida']]
    pior_atraso = max([a for a in atrasos if a is not None] or [0])
    selos.append(_selo('nascimento', 'Documento × saída', 'atencao' if pior_atraso > 6 else 'ok',
                       f"carga criada {pior_atraso:.0f} h depois da saída" if pior_atraso > 6 else 'carga criada a tempo',
                       'Robô do manifesto'))
    return selos


def _terceiro_livre(cur, legs):
    """Terceiro que entregou fica 48 h em "livres para carga" da torre — a mesma régua."""
    ult = [t for t in legs if t['papel'] != 'vazio'][-1]
    if ult['tipo'] != 'Terceiro' or ult['status'] != 'Entregue' or not ult['conclusao']:
        return None
    try:
        import torre
        for l in torre._terceiros_livres(cur, datetime.utcnow()):
            if l['carga_id'] == ult['id']:
                ate = datetime.fromisoformat(l['livre_ate'])
                return {'trecho': ult['numero'], 'ate': _iso(ate), 'ate_txt': f"{ate - BRT:%d/%m %H:%M}",
                        'onde': l.get('cidade')}
    except Exception:
        return None
    return None


# ── a consulta local ────────────────────────────────────────────────────────────────────────
def _km_na_viagem(carregados):
    """Km de rota de cada trecho carregado DENTRO da viagem. A perna que desengatou no caminho
    costuma ter sido criada com o destino FINAL (10 das 36 cadeias do robô até 08/10/2026): a rota
    dela vai até o fim e a continuação refaz o fim — somar as duas contava o fim duas vezes (a
    C-2026-001275 dava 4.475 km numa viagem de 3.010). O km dela é a rota menos as pernas
    seguintes: até a passagem. Sobra menos de 10% da rota (a C-2026-001218 e a seguinte têm os
    mesmos 783 km: a carga trocou de caminhão no pátio) → 'rota_ate_o_fim', e o km dela é o que
    sobrou, mesmo que zero."""
    final = _local(carregados[-1])
    for i, t in enumerate(carregados[:-1]):
        seguintes = sum(x['km_planejado'] or 0 for x in carregados[i + 1:])
        if not t['km_planejado'] or not seguintes or _local(t) != final:
            continue
        sobra = t['km_planejado'] - seguintes
        t['km_ate_passagem'] = t['km_rota_viagem'] = round(max(sobra, 0.0), 1)
        t['rota_ate_o_fim'] = sobra < 0.1 * t['km_planejado']


def _leg_out(t):
    """Trecho para o JSON: datas em ISO, mais o que a tela mostra pronto."""
    o = dict(t)
    for k in ('carregamento', 'criado', 'saida', 'chegada', 'conclusao', 'desengate'):
        o[k] = _iso(t[k])
    o['motivo_texto'] = MOTIVO.get(t['motivo'] or '', None)
    o['permanencia_h'] = round(_h(t['conclusao'], t['chegada']), 1) if (
        t['motivo'] == 'gps_saiu_do_destino' and t['chegada'] and t['conclusao']) else None
    o['transito_h'] = round(_h(t['chegada'], t['saida']), 1) if (t['chegada'] and t['saida']) else None
    o['nasceu_depois_h'] = round(_h(t['criado'], t['saida']), 1) if (
        t['papel'] != 'vazio' and t['criado'] and t['saida'] and t['criado'] > t['saida']) else None
    return o


def montar(cur, ref):
    cid = resolver(cur, ref)
    if not cid:
        return None
    aid = ancora(cur, cid)
    legs = trechos(cur, aid)
    if not legs:
        return None
    carregados = [t for t in legs if t['papel'] != 'vazio']
    anc, ult = carregados[0], carregados[-1]
    _km_na_viagem(carregados)
    ordens = _ordens(cur, legs)
    sms, macros = _sms(cur, legs)
    pgr = _pgr(cur, legs)
    ciot = _ciot(cur, legs)
    sm_aba = _sm_aba(cur, legs)
    log = _log(cur, legs)
    livre = _terceiro_livre(cur, legs)
    ini = next((t['saida'] for t in carregados if t['saida']), None)
    fim = ult['conclusao']
    # Desengatada que já seguiu (continuação) ou já concluiu não está mais ativa — a régua da torre
    ativo = any(t['status'] in ATIVAS and not (t['status'] == 'Desengatada' and (t['conclusao'] or t['continua_em']))
                for t in carregados)
    km_carr = sum(t['km_rota_viagem'] or 0 for t in carregados)
    km_vazio = sum(t['km_planejado'] or 0 for t in legs if t['papel'] == 'vazio')
    resumo = {
        'numero': anc['numero'], 'foco_id': cid, 'cliente': anc['cliente'], 'embarcador': anc['embarcador'],
        'origem': _local(anc, 'origem'), 'destino': _local(ult),
        'status': ult['status'] if not ativo else next(
            t['status'] for t in carregados if t['status'] in ATIVAS
            and not (t['status'] == 'Desengatada' and (t['conclusao'] or t['continua_em']))),
        'ativa': ativo, 'tipos': sorted({t['tipo'] for t in carregados if t['tipo']}),
        'inicio': _iso(ini), 'fim': _iso(fim) if not ativo else None,
        'duracao_h': round(_h(fim, ini), 1) if (ini and fim and not ativo) else None,
        'km_carregado': round(km_carr), 'km_vazio': round(km_vazio), 'n_trechos': len(carregados),
        'livre_retorno': livre,
    }
    return {
        'viagem': resumo,
        'trechos': [_leg_out(t) for t in legs],
        'conformidade': _conformidade(legs, ordens, sm_aba, ciot, pgr),
        'linha_do_tempo': _linha_do_tempo(legs, ordens, sms, macros, pgr, ciot, log, livre),
        'ordens': [{**o, 'cadastrada': _iso(_de_brt(o['cadastrada'])), 'comandada': _iso(_de_brt(o['comandada'])),
                    'coletada': _iso(_de_brt(o['coletada'])), 'cancelada': _iso(_de_brt(o['cancelada'])),
                    'agendamento': _iso(o['agendamento'])} for o in ordens],
        'sms': [{**s, 'criada': _iso(s['criada']), 'inicio': _iso(s['inicio']), 'encerrada': _iso(s['encerrada'])}
                for s in sms],
        'gerado_em': _iso(datetime.utcnow()),
    }


TIPOS = ('Frota', 'Agregado', 'Terceiro')


def busca(cur, q='', limite=50, de=None, ate=None, tipos=None):
    """Lista da página inicial. Filtros: termo (nº da carga, cliente, manifesto, CTRB ou placa),
    período pela data de carregamento (`de`/`ate`, 'AAAA-MM-DD') e tipo. Só a âncora aparece (a
    continuação abre a mesma ficha). Devolve as viagens, o total e a contagem por tipo — esta SEM
    o filtro de tipo, para os botões dizerem quanto há em cada um."""
    q = (q or '').strip()
    conds, args = [], []
    if q:
        d = _doc(q)
        conds.append("""(c.numero ILIKE %s OR c.cliente_nome ILIKE %s
                         OR regexp_replace(upper(c.manifesto_origem), '[^A-Z0-9]', '', 'g') LIKE %s
                         OR regexp_replace(upper(c.ctrb_origem), '[^A-Z0-9]', '', 'g') LIKE %s
                         OR c.cavalo_placa = ANY(%s) OR c.carreta1_placa = ANY(%s))""")
        g = pl.grafias(d) if len(d) == 7 else [d]
        args += [f'%{q}%', f'%{q}%', f'%{d}%', f'%{d[:9]}%', g, g]
    if de:
        conds.append('c.data_carregamento >= %s')
        args.append(de)
    if ate:
        conds.append('c.data_carregamento <= %s')
        args.append(ate)
    onde = f"""FROM embarques_cargas c
         WHERE NOT COALESCE(c.viagem_vazia, FALSE) AND c.status <> 'Cancelada'
           AND NOT EXISTS (SELECT 1 FROM embarques_cargas a WHERE a.continua_em = c.id)
           {''.join(' AND ' + x for x in conds)}"""
    cur.execute(f"SELECT c.tipo_operacao, COUNT(*) {onde} GROUP BY 1", args)
    por_tipo = {t or '?': n for t, n in cur.fetchall()}
    tipos = [t for t in (tipos or []) if t in TIPOS]
    if tipos and len(tipos) < len(TIPOS):
        onde += ' AND c.tipo_operacao = ANY(%s)'
        args.append(tipos)
        total = sum(por_tipo.get(t, 0) for t in tipos)
    else:
        total = sum(por_tipo.values())
    cur.execute(f"""
        SELECT c.numero, c.tipo_operacao, c.status, c.cliente_nome, c.origem_cidade, c.origem_uf,
               (SELECT d.cidade || '/' || d.uf FROM embarques_cargas_destinos d WHERE d.carga_id = c.id
                 ORDER BY d.ordem DESC LIMIT 1),
               c.data_carregamento, c.cavalo_placa, c.carreta1_placa, c.manifesto_origem, c.continua_em
          {onde}
         ORDER BY c.data_carregamento DESC, c.id DESC LIMIT %s""", (*args, limite))
    cols = ('numero', 'tipo', 'status', 'cliente', 'origem_cidade', 'origem_uf', 'destino', 'carregamento',
            'cavalo', 'carreta', 'manifesto', 'continua_em')
    out = []
    for r in cur.fetchall():
        x = dict(zip(cols, r))
        x['carregamento'] = _iso(x['carregamento'])
        x['origem'] = f"{x.pop('origem_cidade') or '?'}/{x.pop('origem_uf') or '?'}"
        out.append(x)
    return {'viagens': out, 'total': total, 'por_tipo': por_tipo}


# ═══════════════════════════════════════════════════════════════════════════════════════════
# POWER BI — documentos e dinheiro
#
# `ctx` vem do server (injeção: a ficha não importa o server.py):
#   dax(q, ds=None) → linhas · custo_base('AAAA-MM') → base do `_custo_km_cliente` ·
#   custo_viagem (`_custo_viagem_km`) · placa (`_placa_mercosul`) · km_expr (`_dax_km_viagem`) ·
#   ds_contabil (dataset das faturas) · carga_do_manifesto(m) → nº da carga no banco local
#
# RECEITA da viagem = os CTes do manifesto da âncora — os que nasceram nele e os que foram
# manifestados nele vindos de outro — SEM rateio (decisão de 09/10/2026). A Auditoria rateia o
# CTe entre os CTRBs por km: certo para a margem de cada CTRB, errado para a viagem (80 das 383
# viagens de set/26 mudavam).
# CUSTO = os CTRBs dos manifestos por onde os CTes passaram a partir da âncora: a cadeia do robô
# e a continuação só documental (o robô não ligou). O manifesto de onde um CTe VEIO fica fora — é
# outra viagem (6 das 476 cargas de 01/09 a 07/10). Manifesto que leva carga de outras viagens
# entra pela parte do frete desta (consolidado).
#   frete pago ... `frete_motorista_total` da Auditoria = valor a pagar + vale-pedágio + pedágio
#                  + acerto de conta corrente (CCF, que pode abater dívida de OUTRA viagem)
#   frota ........ R$/km da placa × km de rota (a conta da aba Veículos), do último mês fechado
# ═══════════════════════════════════════════════════════════════════════════════════════════
_CACHE = {}
_CACHE_TRAVA = threading.Lock()
_TRAVAS = defaultdict(threading.Lock)
TTL_VIAGEM_S = 600        # documentos e dinheiro de uma viagem
TTL_BASE_S = 3 * 3600     # base de custo de frota do mês (~20 s para montar)


def _cacheado(chave, ttl, fn):
    """Cache em memória com trava por chave: dois pedidos iguais ao mesmo tempo fazem a conta uma vez."""
    with _CACHE_TRAVA:
        hit = _CACHE.get(chave)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        trava = _TRAVAS[chave]
    with trava:
        with _CACHE_TRAVA:
            hit = _CACHE.get(chave)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        val = fn()
        with _CACHE_TRAVA:
            _CACHE[chave] = (time.time(), val)
        return val


def base_em_cache(mes):
    """A base de custo do mês já está pronta? (a tela avisa quando a conta vai levar ~20 s)"""
    with _CACHE_TRAVA:
        hit = _CACHE.get(('base', mes))
        return bool(hit and time.time() - hit[0] < TTL_BASE_S)


def _lista(vals):
    vals = [v for v in dict.fromkeys(vals) if v]
    return '{' + ','.join('"' + str(v).replace('"', '""') + '"' for v in vals) + '}'


def _f(v):
    try:
        return float(v) if v not in (None, '') else 0.0
    except (TypeError, ValueError):
        return 0.0


def _br(v):
    """'29.116,56' (texto do SSW) → 29116.56."""
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace('.', '').replace(',', '.'))
    except (TypeError, ValueError):
        return 0.0


def _dia(v):
    try:
        return datetime.fromisoformat(str(v)[:19]).date() if v else None
    except ValueError:
        return None


def _ctrb_do_historico(s):
    """'PAGAMENTO DO SALDO CTRB NOD   4887-9' (477) → 'NOD004887-9'."""
    m = re.search(r'CTRB\s+([A-Z]{3})\s*(\d{1,6})-?(\d)', (s or '').upper())
    return f"{m.group(1)}{int(m.group(2)):06d}-{m.group(3)}" if m else None


def _motivo_complemento(o):
    for k, n in (('DIARIA', 'diária'), ('ESTADIA', 'diária'), ('DESCARGA', 'descarga'), ('REENTREGA', 'reentrega'),
                 ('ARMAZENAGEM', 'armazenagem'), ('PALETIZ', 'paletização'), ('PEDAGIO', 'pedágio'),
                 ('DEVOLU', 'devolução')):
        if k in o:
            return n
    return 'outros'


# ── a observação do CTRB: o analista declara à mão o km e a tarifa pagos (09/10/2026) ──────
# "VAZIO GRU X JUNDIAI 70 KM PG A 3,00 MAIS 58,00 DE PEDAGIOS  CARREGADO FINI JUNDIAI X
#  PARNAMIRIM 2919 KM PG A 5,32 MAIS PEDAGIOS". O SSW corta o campo em 200 caracteres e o texto
# jurídico do TAC (automático) vem antes — às vezes o corte come o carregado.
_SEG = re.compile(r'(SEM\s+(?:DESLOCAMENTO\s+)?VAZIO)|\b(VAZIO|CARR+EGADO)\b((?:(?!\bCARR+EGADO\b|\bVAZIO\b).)*?)'
                  r'(\d[\d.]*)\s*KM\s+P[GA]+\s+A\s+(?:R\$\s*)?(\d+[,.]\d+)((?:(?!\bCARR+EGADO\b|\bVAZIO\b).)*)')
_PED = re.compile(r'MAIS\s+(?:R\$\s*)?(\d[\d.]*,\d{2})\s+(?:REAIS\s+)?DE\s+PEDAG')
_ROTA = re.compile(r'([A-Z][A-Z ./]*?)\s+X\s+([A-Z][A-Z ./]*?)\s*-?\s*$')
_PALAVRA = re.compile(r'\b(CARR+EGADO|VAZIO)\b')


def desmembrar_observacao(obs):
    """Trechos declarados na observação: [{tipo, rota, km, tarifa, pedagio, valor}], se diz 'sem
    vazio' e quais trechos o SSW cortou (a palavra está lá, o "KM PG A" ficou depois dos 200)."""
    o = re.sub(r'\s+', ' ', (obs or '').upper())
    segs, sem_vazio, usados = [], False, []
    for m in _SEG.finditer(o):
        usados.append((m.start(), m.end()))
        if m.group(1):
            sem_vazio = True
            continue
        tipo = 'vazio' if m.group(2) == 'VAZIO' else 'carregado'
        km, tar = _br(m.group(4)), _br(m.group(5))
        p = _PED.search(m.group(6) or '')
        ped = _br(p.group(1)) if p else 0.0
        r = _ROTA.search((m.group(3) or '').strip())
        segs.append({'tipo': tipo, 'rota': f"{r.group(1).strip()} → {r.group(2).strip()}" if r else None,
                     'km': km, 'tarifa': tar, 'pedagio': ped, 'valor': round(km * tar + ped, 2)})
    fim = max((b for _, b in usados), default=0)
    cortados = [('vazio' if k.group(1) == 'VAZIO' else 'carregado') for k in _PALAVRA.finditer(o)
                if k.start() >= fim] if len((obs or '').strip()) >= 190 else []
    return segs, sem_vazio, cortados


def tarifas_validas(tipo_cavalo, destino):
    """Tabela do analista (09/10/2026): destino ES 5,77 · cavalo trucado 5,45 · toco 5,32.
    O cadastro (Classificacao CTRB) diz 'CAVALO TRUCADO' em só 29 de 2.702 CTRBs de agregado —
    'CAVALO' pode ser qualquer um dos dois, e aí valem as duas tarifas."""
    if (destino or '').upper().strip().endswith('/ES'):
        return {'ES': TARIFA_CARREGADO['ES']}
    if 'TRUC' in (tipo_cavalo or '').upper():
        return {'trucado': TARIFA_CARREGADO['TRUCADO']}
    return {'toco': TARIFA_CARREGADO['TOCO'], 'trucado': TARIFA_CARREGADO['TRUCADO']}


def _qual_tarifa(tarifa, validas, folga=0.005):
    """O rótulo da tarifa da tabela mais perto, se dentro da folga (relativa, para a implícita)."""
    if not tarifa:
        return None
    rot, v = min(validas.items(), key=lambda kv: abs(tarifa - kv[1]))
    return rot if abs(tarifa - v) <= max(0.005, folga * v) else None


# ── a coleta no BI (crua, por viagem) ───────────────────────────────────────────────────────
CE = "'public conhecimentos_emitidos'"
MC = "'public manifestos_ctrc'"
AR = "'Auditoria Receita'"
OS = "'public ctrbs_oss'"
DZ = "'public consulta_despesas_477'"
_COLS_CTE = ', '.join(f'"{k}", {CE}[{c}]' for k, c in (
    ('cte', 'serie_numero_ctrc'), ('chave', 'chave_cte'), ('tipo', 'tipo_documento'), ('emissao', 'data_emissao'),
    ('remetente', 'cliente_remetente'), ('rem_cidade', 'cidade_remetente'), ('rem_uf', 'uf_remetente'),
    ('destinatario', 'cliente_destinatario'), ('dest_cidade', 'cidade_destinatario'), ('dest_uf', 'uf_destinatario'),
    ('nf', 'numero_nota_fiscal'), ('peso', 'peso_real_kg'), ('volumes', 'quantidade_volumes'),
    ('valor_mercadoria', 'valor_mercadoria'), ('valor_frete', 'valor_frete'), ('receita', 'valor_frete_sem_icms'),
    ('pm', 'primeiro_manifesto'), ('um', 'ultimo_manifesto'), ('pagador', 'cliente_pagador'),
    ('pagador_cnpj', 'cnpj_pagador')))


def _paralelo(*fns):
    """Consultas ao BI que não dependem uma da outra vão juntas (cada uma leva ~1 s)."""
    with ThreadPoolExecutor(max_workers=len(fns)) as ex:
        futuros = [ex.submit(fn) for fn in fns]
        return [f.result() for f in futuros]


def _meses(datas):
    """Competências do 477 ('MM/YY') de um mês antes a dois depois das datas dadas."""
    out = set()
    for d0 in datas:
        if d0:
            for k in range(-1, 3):
                n = d0.year * 12 + d0.month - 1 + k
                out.add(f'{n % 12 + 1:02d}/{str(n // 12)[2:]}')
    return sorted(out)


def _bruto(dados, ctx):
    """Tudo o que a ficha lê do BI, cru, em etapas — o que não depende de nada vai junto:
    (CTes nascidos, CTes do manifesto) → (ponte, complementos, chapa) →
    (Auditoria, faturas, composição dos manifestos) → (CTRBs, classificação, 477)."""
    dax = ctx['dax']
    carregados = [t for t in dados['trechos'] if t['papel'] != 'vazio']
    m_anc = _doc(carregados[0]['manifesto'])
    avisos = []
    out = {'ctes': [], 'complementos': [], 'ponte': [], 'auditoria': [], 'composicao': {}, 'ctrbs_oss': {},
           'tipo_cavalo': {}, 'pagamentos': [], 'chapa': [], 'faturas': [], 'anteriores': {}, 'custo_manifestos': [],
           'carga_de': {}, 'de_outras_viagens': [], 'continuacao_de': None, 'avisos': avisos}
    numero = re.search(r'(\d{6}-?\d)$', carregados[0]['manifesto'] or '')
    if not numero:
        avisos.append('a carga não tem manifesto — sem documentos no BI')
        return out
    num = numero.group(1)

    # 1) os CTes da viagem: os que nasceram no manifesto da âncora e os manifestados nele
    def q_nascidos():
        return [c for c in dax(f"EVALUATE SELECTCOLUMNS(FILTER({CE}, CONTAINSSTRING({CE}[primeiro_manifesto], \"{num}\")), {_COLS_CTE})")
                if _doc(c['pm']) == m_anc]

    def q_listados():
        return [r['cte'] for r in dax(f"""EVALUATE SELECTCOLUMNS(FILTER({MC}, CONTAINSSTRING({MC}[CHAVE_MANIFESTO], "{num}")),
            "manifesto", {MC}[CHAVE_MANIFESTO], "cte", {MC}[CHAVE_CTRC])""") if _doc(r['manifesto']) == m_anc]

    nascidos, listados = _paralelo(q_nascidos, q_listados)
    ja = {_doc(c['cte']) for c in nascidos}
    extras = [c for c in dict.fromkeys(listados) if _doc(c) not in ja]
    vindos = dax(f"EVALUATE SELECTCOLUMNS(FILTER({CE}, {CE}[serie_numero_ctrc] IN {_lista(extras)}), {_COLS_CTE})") if extras else []
    busca = ctx.get('carga_do_manifesto')
    for c in vindos:
        c['veio_de'] = c['pm']
        a = out['anteriores'].setdefault(_doc(c['pm']), {'manifesto': c['pm'], 'carga': None, 'ctes': []})
        a['ctes'].append(c['cte'])
    for a in out['anteriores'].values():
        a['carga'] = busca(a['manifesto']) if busca else None
    # cada CTe conta numa viagem só: a do manifesto em que nasceu. O que veio de outra viagem
    # fica nos documentos, e o CTRB deste manifesto se divide com ela (composição, abaixo).
    out['de_outras_viagens'] = [c for c in vindos if out['anteriores'][_doc(c['pm'])]['carga']]
    sem_dono = [c for c in vindos if not out['anteriores'][_doc(c['pm'])]['carga']]
    out['ctes'] = nascidos + sem_dono     # veio de manifesto sem carga no sistema: a viagem é esta
    if not out['ctes'] and out['de_outras_viagens']:
        out['continuacao_de'] = sorted({a['carga'] for a in out['anteriores'].values() if a['carga']})
        avisos.append('esta carga só leva CTe de outra viagem (' + ', '.join(out['continuacao_de'])
                      + ') — a receita e a viagem completa estão na ficha de lá')
    elif not out['ctes']:
        avisos.append('nenhum CTe encontrado no manifesto da viagem — sem receita')
    nums_cte = [c['cte'] for c in out['ctes']]
    meses = _meses([_dia(c['emissao']) for c in out['ctes']] or [_dia(t['carregamento']) for t in carregados])

    # 2) a ponte (por onde os CTes nascidos aqui seguiram), os complementos e a chapa
    def q_ponte():
        so_daqui = [c['cte'] for c in nascidos]
        if not so_daqui:
            return []
        return dax(f"""EVALUATE SELECTCOLUMNS(FILTER({MC}, {MC}[CHAVE_CTRC] IN {_lista(so_daqui)}),
            "manifesto", {MC}[CHAVE_MANIFESTO], "cte", {MC}[CHAVE_CTRC])""")

    def q_complementos():
        # complemento de frete (descarga, diária, reentrega…) cita o CTe original na observação
        d0 = min((_dia(c['emissao']) for c in out['ctes'] if c.get('emissao')), default=None)
        cnpjs = [c['pagador_cnpj'] for c in out['ctes'] if c.get('pagador_cnpj')]
        if not (d0 and cnpjs):
            return []
        d1 = d0 + timedelta(days=120)
        comps = dax(f"""EVALUATE SELECTCOLUMNS(FILTER({CE}, {CE}[tipo_documento] = "COMPLEMENTAR FRETE"
            && {CE}[data_emissao] >= DATE({d0.year},{d0.month},{d0.day}) && {CE}[data_emissao] <= DATE({d1.year},{d1.month},{d1.day})
            && {CE}[cnpj_pagador] IN {_lista(cnpjs)}), "cte", {CE}[serie_numero_ctrc], "emissao", {CE}[data_emissao],
            "receita", {CE}[valor_frete_sem_icms], "valor_frete", {CE}[valor_frete], "obs", {CE}[observacao])""")
        por_num = {_doc(c['cte']): c['cte'] for c in out['ctes']}
        por_chave = {re.sub(r'\D', '', c.get('chave') or ''): c['cte'] for c in out['ctes'] if c.get('chave')}
        achados = []
        for x in comps:
            o = (x['obs'] or '').upper()
            m = re.search(r'COMPL\.?\s+DO\s+([A-Z]{3}\s?\d{6}-?\d)', o)
            orig = por_num.get(_doc(m.group(1))) if m else None
            if not orig:
                k = re.search(r'COMPLEMENTO AO CT-?E\s*([\d,.\s]{44,60})', o)
                orig = por_chave.get(re.sub(r'\D', '', k.group(1))[:44]) if k else None
            if orig:
                achados.append({**x, 'origem_cte': orig, 'motivo': _motivo_complemento(o)})
        return achados

    def q_chapa():
        # carga/descarga paga a chapa (477, evento 5100) — casa pela NF do CTe citada no histórico
        nfs = {re.sub(r'\D', '', str(c.get('nf') or '')).lstrip('0') for c in out['ctes']} - {''}
        if not (nfs and meses):
            return []
        ch = dax(f"""EVALUATE SELECTCOLUMNS(FILTER({DZ}, {DZ}[evento] = "5100" && {DZ}[mes_competencia] IN {_lista(meses)}),
            "lanc", {DZ}[numlancto], "valor", {DZ}[vlr_final], "pgto", {DZ}[data_pgto], "fornecedor", {DZ}[nome_fornecedor],
            "historico", {DZ}[historico_despesa], "sit", {DZ}[sit_des])""")
        return [x for x in ch if {n.lstrip('0') for n in re.findall(r'NF\s*:?\s*(\d{3,})', (x['historico'] or '').upper())} & nfs]

    out['ponte'], out['complementos'], out['chapa'] = _paralelo(q_ponte, q_complementos, q_chapa)

    # 3) os manifestos que entram no custo: a cadeia + para onde os CTes seguiram, menos de onde vieram
    cadeia = [t['manifesto'] for t in carregados if t['manifesto']]
    k_cadeia = {_doc(m) for m in cadeia}
    k_antes = set(out['anteriores']) - k_cadeia
    custo = list(dict.fromkeys(cadeia + [p['manifesto'] for p in out['ponte']
                                         if p.get('manifesto') and _doc(p['manifesto']) not in k_antes]))
    out['custo_manifestos'] = custo

    def q_auditoria():
        return dax(f"""EVALUATE SELECTCOLUMNS(FILTER({AR}, {AR}[Manifesto] IN {_lista(custo)}),
            "ctrb", {AR}[CTRB], "ctrc", {AR}[CTRC], "manifesto", {AR}[Manifesto], "tipo", {AR}[Tipo Operacao],
            "receita_rateada", {AR}[receita_rateada], "frete_pago", {AR}[frete_motorista_total],
            "valor_a_pagar", {AR}[valor_a_pagar], "vale_pedagio", {AR}[vale_pedagio], "pedagio", {AR}[pedagio],
            "saldo_ccf", {AR}[saldo_ccf], "cavalo", {AR}[placa_cavalo], "carreta", {AR}[placa_carreta],
            "data_ref", {AR}[data_ref_ctrc], "km", {ctx['km_expr'](AR)},
            "auditoria_frete", {AR}[status_auditoria_frete], "frete_tabela", {AR}[frete_tabela])""")

    def q_composicao():
        # todos os CTes de cada manifesto do custo: o que não é desta viagem divide o CTRB
        if len(custo) <= 1 and not out['de_outras_viagens']:
            return {}
        comp = defaultdict(list)
        for r in dax(f"""EVALUATE SELECTCOLUMNS(FILTER({MC}, {MC}[CHAVE_MANIFESTO] IN {_lista(custo)}),
                "manifesto", {MC}[CHAVE_MANIFESTO], "cte", {MC}[CHAVE_CTRC], "vf", {MC}[valor_frete])"""):
            comp[_doc(r['manifesto'])].append((_doc(r['cte']), _f(r['vf'])))
        return dict(comp)

    def q_faturas():
        todos = nums_cte + [c['cte'] for c in out['complementos']]
        if not (ctx.get('ds_contabil') and todos):
            return []
        fc = dax(f"""EVALUATE SELECTCOLUMNS(FILTER('public faturas_441_ctrcs', 'public faturas_441_ctrcs'[ctrc] IN {_lista(todos)}),
            "fatura", 'public faturas_441_ctrcs'[fatura], "cte", 'public faturas_441_ctrcs'[ctrc],
            "valor", 'public faturas_441_ctrcs'[valor_frete])""", ctx['ds_contabil'])
        if not fc:
            return []
        # o cabeçalho grava a fatura com dígito ('0039426-2'); a lista de CTes, sem ('0039426')
        fs = {str(r['fatura']).split('-')[0]: r for r in dax(f"""EVALUATE SELECTCOLUMNS(FILTER('public faturas_441',
            LEFT('public faturas_441'[fatura], 7) IN {_lista([str(x['fatura'])[:7] for x in fc])}),
            "fatura", 'public faturas_441'[fatura], "emissao", 'public faturas_441'[emissao],
            "vencimento", 'public faturas_441'[vencimen], "pagamento", 'public faturas_441'[pagamento],
            "valor", 'public faturas_441'[vlr_fatur], "pago", 'public faturas_441'[vlr_pago],
            "saldo", 'public faturas_441'[saldo], "dias_atraso", 'public faturas_441'[dias_atraso],
            "cancelada", 'public faturas_441'[cancelam])""", ctx['ds_contabil'])}
        achadas = []
        for x in fc:
            cab = fs.get(str(x['fatura']).split('-')[0]) or {'fatura': x['fatura']}
            achadas.append({**cab, 'cte': x['cte'], 'valor_cte': x['valor']})
        return achadas

    out['auditoria'], out['composicao'], out['faturas'] = _paralelo(q_auditoria, q_composicao, q_faturas)
    for t in carregados:
        if t['manifesto'] and not any(_doc(a['manifesto']) == _doc(t['manifesto']) for a in out['auditoria']):
            avisos.append(f"{t['numero']}: o manifesto {t['manifesto']} não tem CTRB na Auditoria — o custo deste trecho não entra")
    if busca:
        out['carga_de'] = {_doc(m): busca(m) for m in custo if _doc(m) not in k_cadeia}
    if out['ctes']:
        for k, a in out['anteriores'].items():
            if k in k_cadeia:
                continue
            if a['carga']:
                avisos.append(f"{len(a['ctes'])} CTe(s) deste manifesto nasceram na viagem {a['carga']} — contam lá; "
                              'o CTRB daqui se divide pela parte do frete de cada viagem')
            else:
                avisos.append(f"{len(a['ctes'])} CTe(s) vieram do manifesto {a['manifesto']}, que não tem carga no "
                              'sistema — a receita entra aqui')
    ctrbs = [a['ctrb'] for a in out['auditoria'] if a.get('ctrb')]
    if not ctrbs:
        return out

    # 4) o CTRB no SSW (observação, CIOT, retenções), o tipo do cavalo e os pagamentos do 477
    def q_oss():
        cols = ('ctrb', 'emissao', 'valor_a_pagar', 'total_ctrb', 'valor_liquido', 'total_retencoes', 'vale_pedagio',
                'pedagio', 'saldo_ccf', 'ciot', 'fornecedor_ciot', 'observacao', 'distancia_km', 'motorista', 'proprietario')
        sel = ', '.join(f'"{c}", {OS}[{c}]' for c in cols)
        return {r['ctrb']: r for r in dax(f"EVALUATE SELECTCOLUMNS(FILTER({OS}, {OS}[ctrb] IN {_lista(ctrbs)}), {sel})")}

    def q_cls():
        return {r['ctrb']: r for r in dax(f"""EVALUATE SELECTCOLUMNS(FILTER('Classificacao CTRB',
            'Classificacao CTRB'[ctrb] IN {_lista(ctrbs)}), "ctrb", 'Classificacao CTRB'[ctrb],
            "tipo_cavalo", 'Classificacao CTRB'[tipo_cavalo], "destino", 'Classificacao CTRB'[cidade_uf_destino])""")}

    def q_pagamentos():
        # adiantamento e saldo do frete citam o CTRB no histórico ('... CTRB NOD   4887-9');
        # sit_des LIQU = pago, PEND = programado para data_pgto
        partes = ' || '.join(f'CONTAINSSTRING({DZ}[historico_despesa], "{int(c[3:9])}-{c[-1]}")' for c in ctrbs
                             if re.fullmatch(r'[A-Z]{3}\d{6}-\d', c))
        if not (partes and meses):
            return []
        pg = dax(f"""EVALUATE SELECTCOLUMNS(FILTER({DZ}, {DZ}[evento] IN {{"5101","5102"}}
            && {DZ}[mes_competencia] IN {_lista(meses)} && ({partes})),
            "lanc", {DZ}[numlancto], "parcela", {DZ}[parcela], "valor", {DZ}[vlr_final], "pgto", {DZ}[data_pgto],
            "vencimento", {DZ}[vencimen], "historico", {DZ}[historico_despesa], "evento", {DZ}[evento], "sit", {DZ}[sit_des])""")
        alvo, achados = set(ctrbs), []
        for p in pg:
            c = _ctrb_do_historico(p['historico'])
            if c in alvo:
                h = (p['historico'] or '').upper()
                achados.append({**p, 'ctrb': c,
                                'tipo': 'saldo' if 'SALDO' in h else ('adiantamento' if 'ADIANTAMENTO' in h else 'outro')})
        return achados

    out['ctrbs_oss'], out['tipo_cavalo'], out['pagamentos'] = _paralelo(q_oss, q_cls, q_pagamentos)
    return out


def _status_fatura(f):
    canc = str(f.get('cancelada') or '').strip().upper()
    if canc and canc not in ('NAO TEM', 'NÃO TEM', 'N', 'NAO'):
        return 'cancelada'
    if not f.get('vencimento'):
        return 'sem cabeçalho'
    if _f(f.get('saldo')) <= 0.01 and _f(f.get('valor')) > 0:
        return 'paga'
    venc = _dia(f.get('vencimento'))
    if venc and venc < (datetime.utcnow() - BRT).date():
        return 'vencida'
    return 'em aberto'


def _situacao_477(sit):
    return 'pago' if (sit or '').upper().startswith('LIQ') else 'programado'


def _documentos(dados, br, com_dinheiro):
    """Documentos para a tela. Valores só com `com_dinheiro` (quem tem a aba Auditoria)."""
    trecho_do_man = {_doc(t['manifesto']): t['numero'] for t in dados['trechos'] if t['manifesto']}
    k_custo = {_doc(m) for m in br['custo_manifestos']}
    ctes = []
    outras = {_doc(c['cte']) for c in br['de_outras_viagens']}
    for c in br['ctes'] + br['de_outras_viagens']:
        x = {'cte': c['cte'], 'tipo': c['tipo'], 'emissao': c['emissao'], 'nf': c['nf'],
             'conta_em': (br['anteriores'].get(_doc(c['pm'])) or {}).get('carga') if _doc(c['cte']) in outras else None,
             'remetente': c['remetente'], 'origem': f"{c['rem_cidade']}/{c['rem_uf']}" if c.get('rem_cidade') else None,
             'destinatario': c['destinatario'],
             'destino': f"{c['dest_cidade']}/{c['dest_uf']}" if c.get('dest_cidade') else None,
             'peso': _f(c['peso']), 'volumes': c['volumes'], 'primeiro_manifesto': c['pm'],
             'ultimo_manifesto': c['um'], 'veio_de': c.get('veio_de'),
             'veio_de_carga': (br['anteriores'].get(_doc(c.get('veio_de'))) or {}).get('carga') if c.get('veio_de') else None}
        if com_dinheiro:
            x.update(receita=_f(c['receita']), valor_frete=_f(c['valor_frete']), valor_mercadoria=_f(c['valor_mercadoria']))
        ctes.append(x)
    comps = []
    for c in br['complementos']:
        x = {'cte': c['cte'], 'emissao': c['emissao'], 'motivo': c['motivo'], 'origem_cte': c['origem_cte']}
        if com_dinheiro:
            x['receita'] = _f(c['receita'])
        comps.append(x)
    mans = {}
    for t in dados['trechos']:
        if t['papel'] != 'vazio' and t['manifesto']:
            mans[_doc(t['manifesto'])] = {'manifesto': t['manifesto'], 'carga': t['numero'], 'papel': 'cadeia'}
    for m in br['custo_manifestos']:
        k = _doc(m)
        if k not in mans:
            mans[k] = {'manifesto': m, 'carga': br['carga_de'].get(k), 'papel': 'documental'}
    for k, a in br['anteriores'].items():
        if k not in mans:
            mans[k] = {'manifesto': a['manifesto'], 'carga': a['carga'], 'papel': 'anterior'}
    ctrbs = []
    for a in br['auditoria']:
        oss = br['ctrbs_oss'].get(a['ctrb'], {})
        k = _doc(a['manifesto'])
        ctrbs.append({'ctrb': a['ctrb'], 'manifesto': a['manifesto'], 'carga': trecho_do_man.get(k) or br['carga_de'].get(k),
                      'tipo': (a['tipo'] or '').capitalize(), 'ciot': oss.get('ciot'), 'emissao': oss.get('emissao'),
                      'motorista': oss.get('motorista'), 'proprietario': oss.get('proprietario'),
                      'no_custo': k in k_custo})
    faturas = []
    for f in br['faturas']:
        x = {'fatura': f['fatura'], 'cte': f['cte'], 'emissao': f.get('emissao'), 'vencimento': f.get('vencimento'),
             'pagamento': f.get('pagamento'), 'status': _status_fatura(f), 'dias_atraso': f.get('dias_atraso')}
        if com_dinheiro:
            x.update(valor=_f(f.get('valor')), pago=_f(f.get('pago')), saldo=_f(f.get('saldo')),
                     valor_cte=_f(f.get('valor_cte')))
        faturas.append(x)
    return {'ctes': ctes, 'complementos': comps, 'manifestos': list(mans.values()), 'ctrbs': ctrbs, 'faturas': faturas}


# ── o dinheiro ──────────────────────────────────────────────────────────────────────────────
FINANCIAMENTO = ('financiamento', 'fin_carreta')


def _km_nosso(t, km_trechos):
    """O km do trecho pela régua: rastreado quando confiável, senão o roteirizado."""
    if not t:
        return 0.0, None
    k = (km_trechos or {}).get(t['numero'])
    if k and k.get('km') is not None:
        return float(k['km']), k.get('fonte', 'rastreado')
    return float(t.get('km_rota_viagem') or t.get('km_planejado') or 0), 'roteirizado'


def base_mes(data_ref, hoje=None):
    """O mês da base de custo da frota: o da viagem, se já fechou; senão o último fechado (um mês
    em curso ainda não tem salário, financiamento e manutenção lançados — o R$/km sai baixo)."""
    hoje = hoje or (datetime.utcnow() - BRT).date()
    corrente = f'{hoje.year:04d}-{hoje.month:02d}'
    mes = str(data_ref or '')[:7]
    if not re.fullmatch(r'\d{4}-\d{2}', mes):
        return None, None
    if mes < corrente:
        return mes, None
    n = hoje.year * 12 + hoje.month - 2
    return f'{n // 12:04d}-{n % 12 + 1:02d}', 'mês da viagem ainda aberto — custo por km do último mês fechado'


def _desmembra(oss, cls, km_nosso, fonte_km, km_rota, vazio_leg, km_trechos):
    """O frete do agregado em carregado e vazio: o declarado na observação (quando existe),
    confrontado com o nosso km; sem observação, só o nosso km (regra de 09/10/2026).

    Três casos: 'observacao' (carregado declarado), 'observacao_parcial' (o SSW cortou o texto
    depois do vazio — o carregado é o resto do valor) e 'regra' (nada declarado). A tarifa
    implícita do resto divide pelo km de ROTA da Auditoria: o analista paga km de rota (declarado
    × nosso: −3% na C-2026-001275, −0,7% na C-2026-001349), não o que o GPS rodou."""
    valor = _f(oss.get('valor_a_pagar'))
    obs = (oss.get('observacao') or '').strip()
    segs, sem_vazio, cortados = desmembrar_observacao(obs)
    validas = tarifas_validas(cls.get('tipo_cavalo'), cls.get('destino'))
    out = {'valor_a_pagar': valor, 'texto': obs[:200] or None, 'tipo_cavalo': cls.get('tipo_cavalo'),
           'destino': cls.get('destino'), 'tarifas_tabela': validas, 'texto_cortado': bool(cortados),
           'sem_vazio_declarado': sem_vazio}
    km_v_nosso, fonte_v = _km_nosso(vazio_leg, km_trechos) if vazio_leg else (0.0, None)
    car = [s for s in segs if s['tipo'] == 'carregado']
    vaz = [s for s in segs if s['tipo'] == 'vazio']

    def _confronto(kd, kn):
        return round(100 * (kd / kn - 1), 1) if kn else None

    def _vazio_declarado():
        kv = sum(s['km'] for s in vaz)
        return {'km_declarado': kv, 'tarifa': vaz[0]['tarifa'], 'tarifa_tabela': TARIFA_VAZIO_AGREGADO,
                'tarifa_confere': abs(vaz[0]['tarifa'] - TARIFA_VAZIO_AGREGADO) < 0.005,
                'pedagio': round(sum(s['pedagio'] for s in vaz), 2), 'valor': round(sum(s['valor'] for s in vaz), 2),
                'km_nosso': round(km_v_nosso) if vazio_leg else None, 'km_fonte': fonte_v,
                'diferenca_pct': _confronto(kv, km_v_nosso), 'rota': vaz[0]['rota'], 'estimado': False}

    def _carregado_pelo_resto(v_vazio):
        resto = round(max(valor - v_vazio, 0.0), 2)
        km_t = km_rota or km_nosso
        imp = round(resto / km_t, 2) if km_t else None
        return {'valor': resto, 'km_nosso': round(km_nosso), 'km_fonte': fonte_km, 'km_tarifa': round(km_t or 0),
                'km_tarifa_fonte': 'rota da Auditoria' if km_rota else fonte_km, 'tarifa_implicita': imp,
                'tarifa_proxima': _qual_tarifa(imp, validas, folga=0.03), 'estimado': True}

    if car:
        soma = round(sum(s['valor'] for s in segs), 2)
        out.update(fonte='observacao', soma_declarada=soma,
                   fecha=valor > 0 and abs(soma - valor) <= max(5.0, 0.01 * valor))
        kd = sum(s['km'] for s in car)
        out['carregado'] = {'km_declarado': kd, 'tarifa': car[0]['tarifa'],
                            'tarifa_confere': _qual_tarifa(car[0]['tarifa'], validas),
                            'valor': round(sum(s['valor'] for s in car), 2),
                            'km_nosso': round(km_nosso), 'km_fonte': fonte_km,
                            'diferenca_pct': _confronto(kd, km_nosso), 'rota': car[0]['rota'], 'estimado': False}
        if vaz:
            out['vazio'] = _vazio_declarado()
        elif vazio_leg and km_v_nosso and not sem_vazio:
            out['vazio_nao_declarado'] = {'km_nosso': round(km_v_nosso), 'km_fonte': fonte_v,
                                          'valor_pela_regra': round(km_v_nosso * TARIFA_VAZIO_AGREGADO, 2)}
        return out
    if vaz:
        # o SSW cortou a observação nos 200 caracteres: o vazio está declarado e o carregado é o
        # resto do valor a pagar (na C-2026-000870 o resto dá 5,45/km — a tarifa do trucado)
        out['fonte'] = 'observacao_parcial'
        out['vazio'] = _vazio_declarado()
        out['carregado'] = _carregado_pelo_resto(out['vazio']['valor'])
        return out
    # sem observação aproveitável: só o nosso km
    out['fonte'] = 'regra'
    paga_vazio = bool(vazio_leg) and not sem_vazio
    v = round(km_v_nosso * TARIFA_VAZIO_AGREGADO, 2) if paga_vazio else 0.0
    out['vazio'] = ({'km_nosso': round(km_v_nosso), 'km_fonte': fonte_v, 'tarifa': TARIFA_VAZIO_AGREGADO,
                     'valor': v, 'estimado': True} if paga_vazio else None)
    out['carregado'] = _carregado_pelo_resto(v)
    return out


def _escala(d, k):
    return {c: round(v * k, 2) for c, v in d.items()}


def _dinheiro(dados, br, ctx, km_trechos):
    legs = dados['trechos']
    carregados = [t for t in legs if t['papel'] != 'vazio']
    vazio = next((t for t in legs if t['papel'] == 'vazio'), None)
    pm = ctx['placa']
    avisos = []
    rec_ctes = round(sum(_f(c['receita']) for c in br['ctes']), 2)
    comps = defaultdict(float)
    for c in br['complementos']:
        comps[c['motivo']] += _f(c['receita'])
    rec_comp = round(sum(comps.values()), 2)
    pag_por = defaultdict(list)
    for p in br['pagamentos']:
        pag_por[p['ctrb']].append({'tipo': p['tipo'], 'valor': _f(p['valor']), 'data': p['pgto'],
                                   'situacao': _situacao_477(p.get('sit')), 'lancamento': p['lanc'],
                                   'parcela': p.get('parcela')})
    aud_por = defaultdict(list)
    for a in br['auditoria']:
        aud_por[_doc(a['manifesto'])].append(a)
    # os trechos do custo: os da cadeia e os só documentais (o CTe seguiu num manifesto que o robô não ligou)
    k_cadeia = {_doc(t['manifesto']) for t in carregados if t['manifesto']}
    pernas = list(carregados)
    for m in br['custo_manifestos']:
        k = _doc(m)
        if k not in k_cadeia and aud_por.get(k):
            pernas.append({'numero': br['carga_de'].get(k) or m, 'papel': 'documental', 'manifesto': m,
                           'tipo': (aud_por[k][0]['tipo'] or '').capitalize(), 'km_planejado': None})
    meus = {_doc(c['cte']) for c in br['ctes']}
    trechos, custo_op, financ = [], 0.0, 0.0
    bases_usadas = {}
    desm_ancora = None
    for i, t in enumerate(pernas):
        k_man = _doc(t['manifesto'])
        rows = aud_por.get(k_man, [])
        # consolidado: o manifesto leva CTes de outras viagens — entra a parte do frete desta
        compo = br['composicao'].get(k_man)
        part = 1.0
        if compo:
            tot = sum(v for _, v in compo)
            meu = sum(v for c, v in compo if c in meus)
            if tot > 0 and 0 < meu < tot - 0.01:
                part = meu / tot
            elif meu == 0 and t['papel'] != 'documental' and not br['continuacao_de']:
                avisos.append(f"{t['numero']}: nenhum CTe desta viagem no manifesto {t['manifesto']} (BI) — custo entra inteiro")
        itens, sub = [], 0.0
        aud = {'receita_rateada': round(sum(_f(a['receita_rateada']) for a in rows), 2),
               'frete_pago': round(sum(_f(a['frete_pago']) for a in rows), 2)}
        aud['resultado'] = round(aud['receita_rateada'] - aud['frete_pago'], 2)
        km_nosso, fonte_km = _km_nosso(t, km_trechos)
        for a in rows:
            tipo = (a['tipo'] or '').upper()
            km = _f(a['km'])
            oss = br['ctrbs_oss'].get(a['ctrb'], {})
            mes, motivo_mes = base_mes(a.get('data_ref'))
            if tipo == 'FROTA':
                if not mes:
                    avisos.append(f"{a['ctrb']}: sem data na Auditoria — custo da frota não calculado")
                    continue
                base = _cacheado(('base', mes), TTL_BASE_S, lambda m=mes: ctx['custo_base'](m))
                bases_usadas[mes] = motivo_mes
                cav_c, car_c = ctx['custo_viagem'](base, 'FROTA', pm(a['cavalo']), pm(a['carreta']), km, _f(a['receita_rateada']))
                if not cav_c:
                    avisos.append(f"{a['ctrb']}: o cavalo {a['cavalo']} não tem custo por km em {mes} — custo da frota incompleto")
                comp = defaultdict(float)
                for d in (cav_c, car_c):
                    for k_, v_ in d.items():
                        comp[k_] += v_
                fin = sum(comp.get(k_, 0.0) for k_ in FINANCIAMENTO) * part
                op = sum(v for k_, v in comp.items() if k_ not in FINANCIAMENTO) * part
                itens.append({'tipo': 'frota', 'ctrb': a['ctrb'], 'km': km, 'rkm': round(op / (km * part), 2) if (km and part) else None,
                              'componentes': _escala({k_: v for k_, v in comp.items() if abs(v) >= 0.01}, part),
                              'operacional': round(op, 2), 'financiamento': round(fin, 2), 'base_mes': mes,
                              'cavalo': a['cavalo'], 'carreta': a['carreta']})
                sub += op
                financ += fin
                continue
            frete = _f(a['frete_pago'])
            pagos = pag_por.get(a['ctrb'], [])
            liquido = _f(oss.get('valor_liquido'))
            lancado = round(sum(p['valor'] for p in pagos), 2)
            item = {'tipo': 'agregado' if tipo == 'AGREGADO' else 'terceiro', 'ctrb': a['ctrb'], 'km': km,
                    'frete_pago': round(frete * part, 2), 'frete_cheio': round(frete, 2),
                    'composicao': {'valor_a_pagar': _f(a.get('valor_a_pagar')), 'vale_pedagio': _f(a.get('vale_pedagio')),
                                   'pedagio': _f(a.get('pedagio')), 'ccf': _f(a.get('saldo_ccf'))},
                    'retencoes': _f(oss.get('total_retencoes')), 'liquido': liquido, 'ciot': oss.get('ciot'),
                    'pagamentos': pagos,
                    'conciliacao': {'liquido': liquido, 'lancado': lancado,
                                    'pago': round(sum(p['valor'] for p in pagos if p['situacao'] == 'pago'), 2),
                                    'programado': round(sum(p['valor'] for p in pagos if p['situacao'] != 'pago'), 2),
                                    'fecha': bool(pagos) and liquido > 0 and abs(lancado - liquido) <= 1.0},
                    'auditoria_frete': a.get('auditoria_frete'), 'frete_tabela': _f(a.get('frete_tabela'))}
            if abs(item['composicao']['ccf']) >= 0.01:
                item['nota_ccf'] = ('acerto de conta corrente do motorista abatido neste CTRB — pode ser dívida de '
                                    'outra viagem; a Auditoria desconta do frete')
            if tipo == 'AGREGADO':
                if mes:   # o que a Rizza paga além do frete: rastreador e carreta Rizza (aba Veículos)
                    base = _cacheado(('base', mes), TTL_BASE_S, lambda m=mes: ctx['custo_base'](m))
                    bases_usadas[mes] = motivo_mes
                    cav_c, car_c = ctx['custo_viagem'](base, 'AGREGADO', pm(a['cavalo']), pm(a['carreta']), km,
                                                       _f(a['receita_rateada']))
                    extra = {k_: v * part for d in (cav_c, car_c) for k_, v in d.items() if abs(v) >= 0.01}
                    extra_op = sum(v for k_, v in extra.items() if k_ not in FINANCIAMENTO)
                    item['custo_rizza'] = {k_: round(v, 2) for k_, v in extra.items()}
                    item['custo_rizza_op'] = round(extra_op, 2)
                    sub += extra_op
                    financ += sum(v for k_, v in extra.items() if k_ in FINANCIAMENTO)
                if t['papel'] != 'documental':
                    item['desmembrado'] = _desmembra(oss, br['tipo_cavalo'].get(a['ctrb'], {}), km_nosso, fonte_km, km,
                                                     vazio if i == 0 else None, km_trechos)
                    if i == 0 and desm_ancora is None:
                        desm_ancora = item['desmembrado']
            itens.append(item)
            sub += frete * part
        custo_op += sub
        trechos.append({'numero': t['numero'], 'papel': t['papel'], 'tipo': t['tipo'], 'manifesto': t['manifesto'],
                        'itens': itens, 'custo': round(sub, 2), 'auditoria': aud, 'participacao': round(part, 4),
                        'km_nosso': round(km_nosso) if t['papel'] != 'documental' else None, 'km_fonte': fonte_km})
        if part < 1:
            avisos.append(f"{t['numero']}: o manifesto leva carga de outras viagens — entra {100 * part:.0f}% do custo "
                          '(a parte do frete desta viagem)')
    chapa = round(sum(_f(x['valor']) for x in br['chapa']), 2)
    custo_op += chapa
    receita = round(rec_ctes + rec_comp, 2)
    resultado = round(receita - custo_op, 2)
    km_vazio, fonte_v = _km_nosso(vazio, km_trechos)
    # R$/km pelo nosso km (rastreado quando confiável, senão a rota da viagem) — o km da Auditoria
    # é o do CTRB, que na perna que desengatou no caminho vai até o destino final
    kms = [_km_nosso(t, km_trechos) for t in carregados]
    km_carr = sum(k for k, _ in kms)
    fontes = {f for _, f in kms}
    km_carr_fonte = 'rastreado' if fontes == {'rastreado'} else ('roteirizado' if 'rastreado' not in fontes else 'rastreado + rota')
    bloco_vazio = None
    if vazio:
        tar = f'{TARIFA_VAZIO_AGREGADO:.2f}'.replace('.', ',')
        bloco_vazio = {'numero': vazio['numero'], 'km': round(km_vazio), 'km_fonte': fonte_v, 'tipo': vazio['tipo'],
                       'de': _local(vazio, 'origem'), 'ate': _local(vazio), 'lacuna': vazio['lacuna']}
        desm = desm_ancora or {}
        if desm.get('vazio') and not desm['vazio'].get('estimado'):
            bloco_vazio.update(modo='declarado', pago_no_ctrb=desm['vazio'],
                               regra='pago no CTRB desta viagem — declarado pelo analista na observação')
        elif desm.get('vazio'):
            bloco_vazio.update(modo='estimado', pago_no_ctrb=desm['vazio'],
                               regra=f'pago no CTRB desta viagem — sem declaração: nosso km × R$ {tar}')
        elif desm.get('vazio_nao_declarado'):
            bloco_vazio.update(modo='nao_declarado', nao_declarado=desm['vazio_nao_declarado'],
                               regra=f'o CTRB declara só o carregado — pela regra o vazio seria nosso km × R$ {tar}')
        elif desm.get('sem_vazio_declarado'):
            bloco_vazio.update(modo='sem_vazio', regra='o analista declarou "sem vazio" neste CTRB')
        elif carregados[0]['tipo'] == 'Frota':
            rkm = next((it['rkm'] for x in trechos for it in x['itens'] if it['tipo'] == 'frota' and it.get('rkm')), None)
            bloco_vazio.update(modo='frota',
                               regra='custo da frota — no método da aba Veículos o vazio já está dentro do R$/km da placa',
                               informativo={'rkm': rkm, 'valor': round(km_vazio * rkm, 2)} if rkm else None)
        elif carregados[0]['tipo'] == 'Terceiro':
            bloco_vazio.update(modo='terceiro', regra='terceiro: o deslocamento vazio é por conta dele')
    soma_rateada = round(sum(x['auditoria']['receita_rateada'] for x in trechos), 2)
    so_custo = bool(br['continuacao_de'])     # a receita é da viagem de origem: aqui só o custo
    return {
        'continuacao_de': br['continuacao_de'],
        'receita': {'total': receita, 'ctes': rec_ctes, 'complementos': rec_comp,
                    'complementos_por_motivo': {k: round(v, 2) for k, v in comps.items()}},
        'trechos': trechos,
        'chapa': {'total': chapa, 'itens': [{'valor': _f(x['valor']), 'fornecedor': x['fornecedor'], 'data': x['pgto'],
                                             'situacao': _situacao_477(x.get('sit')),
                                             'historico': (x['historico'] or '')[:160]} for x in br['chapa']]},
        'custo_operacional': round(custo_op, 2), 'financiamento': round(financ, 2),
        'resultado': None if so_custo else resultado,
        'margem': round(resultado / receita, 4) if (receita and not so_custo) else None,
        'resultado_apos_financiamento': None if so_custo else round(resultado - financ, 2),
        'km_carregado': round(km_carr), 'km_carregado_fonte': km_carr_fonte, 'km_vazio': round(km_vazio),
        'km_vazio_fonte': fonte_v,
        'rkm_carregado': round(receita / km_carr, 2) if (km_carr and not so_custo) else None,
        'rkm_completo': round(receita / (km_carr + km_vazio), 2) if ((km_carr + km_vazio) and not so_custo) else None,
        'vazio': bloco_vazio,
        'auditoria': {'receita_rateada': soma_rateada, 'confere_ctes': abs(soma_rateada - rec_ctes) <= 1.0,
                      'por_trecho': [{'numero': x['numero'], **x['auditoria']} for x in trechos]},
        'bases': [{'mes': m, 'motivo': mo} for m, mo in sorted(bases_usadas.items())],
        'avisos': avisos,
    }


def bi(dados, ctx, com_dinheiro=False, km_trechos=None):
    """Documentos (sempre) e dinheiro (só com `com_dinheiro`) da viagem montada por `montar`."""
    chave = ('bruto', dados['viagem']['numero'], tuple(t['numero'] for t in dados['trechos']))
    br = _cacheado(chave, TTL_VIAGEM_S, lambda: _bruto(dados, ctx))
    out = {'documentos': _documentos(dados, br, com_dinheiro), 'avisos': list(br['avisos']),
           'continuacao_de': br['continuacao_de'], 'gerado_em': _iso(datetime.utcnow())}
    if com_dinheiro:
        out['dinheiro'] = _dinheiro(dados, br, ctx, km_trechos)
        out['avisos'] += out['dinheiro'].pop('avisos')
    return out
