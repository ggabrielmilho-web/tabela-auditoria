# -*- coding: utf-8 -*-
"""Escala motorista × placa × período para a aba Jornada.

O RH manda à empresa de controle de jornada uma planilha dizendo em que placa
cada motorista frota esteve em cada período — a empresa puxa a jornada pela
telemetria da PLACA. Hoje é digitada à mão; placa errada faz a jornada do
motorista sumir (o `UBS-5G65` do ciclo 21/07–20/08 não existe no cadastro).

Este módulo só monta a escala a partir do que já temos e não fala DAX nem
Flask — quem busca os dados é a rota, no mesmo padrão do `verda_painel`.

Fontes, em ordem de confiança:

1. **Manifesto** — o documento com que o motorista viaja; casado por CPF.
2. **ValeCard** — o motorista é informado na bomba. Prova motorista × placa ×
   dia sem manifesto, e é o que salva o truck de distribuição, que roda sem
   documento. Período sustentado SÓ por abastecimento sai marcado para o RH
   validar à mão, mesmo quando parece óbvio.

Entre duas provas o motorista segue na placa da última ("carry-forward"), que é
como o RH preenche. Esse carregamento para quando:

- a placa aparece com OUTRO motorista (manifesto ou abastecimento dele) — é o
  que encerra o Gaspar no TYX9F55 quando entra de férias e o Blender assume;
- passam `CARRY_MAX_DIAS` sem prova nenhuma — lacuna longa é ausência, não
  viagem (medido: o maior intervalo normal entre viagens foi de 9 dias).

Dia sem placa NÃO é inventado: vira "sem prova" e o RH diz o que foi (férias,
atestado, INSS, folga, admissão).
"""

from collections import defaultdict
from datetime import date, timedelta

# Janela para trás que se lê antes do início do período — para saber em que
# placa o motorista estava no dia 1 sem precisar de prova naquele dia.
LOOKBACK_DIAS = 45

# Sem prova por mais que isto, o carregamento para. O maior intervalo normal
# entre duas viagens do mesmo motorista no ciclo 21/07–20/08 foi de 9 dias.
CARRY_MAX_DIAS = 15


def _dias(ini, fim):
    d = ini
    while d <= fim:
        yield d
        d += timedelta(days=1)


def _faixas(dias_ordenados):
    """[d1, d2, d3, d5] → [(d1, d3), (d5, d5)]"""
    out = []
    for d in dias_ordenados:
        if out and d - out[-1][1] == timedelta(days=1):
            out[-1][1] = d
        else:
            out.append([d, d])
    return [(a, b) for a, b in out]


def casar_nomes(alvos, candidatos, parecido, corte):
    """{alvo: candidato} pelo nome mais parecido acima do corte.

    Um candidato só vai para UM alvo (o de maior nota): dois motoristas da
    folha com nome próximo não podem herdar o mesmo CPF."""
    notas = []
    for a in alvos:
        for c in candidatos:
            s = parecido(a, c)
            if s >= corte:
                notas.append((s, a, c))
    notas.sort(key=lambda x: -x[0])
    feito, usado = {}, set()
    for s, a, c in notas:
        if a in feito or c in usado:
            continue
        feito[a] = c
        usado.add(c)
    return feito


def casar_grafias(alvos, candidatos, parecido, corte):
    """{candidato: alvo} — cada grafia vai para o alvo mais parecido.

    Diferente do CPF, o nome do ValeCard é digitado na bomba e o mesmo motorista
    aparece com várias grafias ('BLENDER JUNIO ALMIDA', 'CRESIO'). Tratar a
    segunda grafia como outra pessoa quebrava a placa dele e acusava placa
    compartilhada consigo mesmo."""
    feito = {}
    for c in candidatos:
        melhor = max(alvos, key=lambda a: parecido(a, c), default=None)
        if melhor is not None and parecido(melhor, c) >= corte:
            feito[c] = melhor
    return feito


def montar(ini, fim, motoristas, eventos, viagens=None, gps_dia=None, carry_max=CARRY_MAX_DIAS):
    """Escala do período.

    motoristas: lista de {'chave', 'nome', 'funcao'} — a lista do RH (folha).
    eventos: lista de {'chave' (motorista) ou None, 'nome_fonte', 'dia', 'placa',
             'fonte' ('manifesto'|'valecard'), 'ref', 'detalhe'} — só placas
             da frota, já em Mercosul. `chave=None` é motorista fora da lista,
             que ainda assim "ocupa" a placa.
    viagens: viagens do Embarques (opcional) — {'chave', 'placa', 'd_ini', 'd_fim',
             'vazia', ...}. Nascem do mesmo manifesto, então não dizem QUEM dirigia;
             dizem QUANDO a viagem começou e acabou, medido pelo GPS. É isso que
             arbitra o abastecimento: o nome do ValeCard é o do cartão ou o digitado
             no CAIS, e já tirou motorista da placa no meio da própria viagem
             (Daniel 05–12/09, Gaspar 12–14/09, com cartão de quem saiu da empresa).
    gps_dia: {(placa, dia): km} da consolidação diária (opcional) — status do dia.

    Retorna {'motoristas': [...], 'compartilhadas': [...]}.
    """
    viagens = viagens or []
    gps_dia = gps_dia or {}

    # Motorista -> dia -> placa -> [viagens em andamento naquele dia].
    em_viagem = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for v in viagens:
        if v.get('chave'):
            for d in _dias(v['d_ini'], v['d_fim']):
                em_viagem[v['chave']][d][v['placa']].append(v)

    # Abastecimento em nome do motorista em OUTRA placa, durante viagem dele:
    # nome ou cartão trocado. Não vira placa nem ocupa a placa de ninguém.
    descartados = defaultdict(list)
    validos = []
    for e in eventos:
        if e['fonte'] == 'valecard' and e['chave']:
            vd = em_viagem[e['chave']].get(e['dia'])
            if vd and e['placa'] not in vd:
                descartados[e['chave']].append((e, sorted(vd)[0]))
                continue
        validos.append(e)

    # Quem usou cada placa em cada dia, e por qual fonte.
    uso = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))   # placa -> dia -> quem -> {fonte}
    por_mot = defaultdict(list)
    for e in validos:
        uso[e['placa']][e['dia']][e['chave'] or ('~' + e['nome_fonte'])].add(e['fonte'])
        if e['chave']:
            por_mot[e['chave']].append(e)

    nomes = {m['chave']: m['nome'] for m in motoristas}

    def _quem(q):
        return nomes.get(q, q.lstrip('~'))

    saida, compartilhadas = [], []

    for m in motoristas:
        ch = m['chave']
        evs = sorted(por_mot.get(ch, []), key=lambda e: (e['dia'], e['fonte'] != 'manifesto', e['ref']))
        prova_dia = defaultdict(list)                      # dia -> [eventos]
        for e in evs:
            prova_dia[e['dia']].append(e)
        minhas = em_viagem.get(ch, {})

        def _outros(placa, d):
            """Outro motorista na placa no dia. Abastecimento em nome de outra
            pessoa não conta quando a viagem dele próprio nessa placa está em
            andamento — é o cartão, não o motorista."""
            fora, so_cartao = [], []
            for q, fontes in uso[placa].get(d, {}).items():
                if q == ch:
                    continue
                if fontes == {'valecard'} and placa in minhas.get(d, {}):
                    so_cartao.append(q)
                else:
                    fora.append(q)
            return sorted(fora), sorted(so_cartao)

        dia_placas = {}                                    # dia -> {placa: 'prova'|'carry'}
        motivo = {}                                        # dia sem placa -> por que o carregamento parou
        ultima_placa = {}                                  # dia em que parou -> placa que ele largou
        cartao_alheio = []                                 # (dia, placa, quem)
        atual, desde_prova, parou, ultimo_mf = None, None, None, None
        inicio_leitura = min([ini] + [e['dia'] for e in evs] + list(minhas))
        for d in _dias(inicio_leitura, fim):
            hoje = prova_dia.get(d)
            if hoje:
                # Manifesto manda no dia; o abastecimento só vale como placa do
                # dia quando não houve manifesto (senão é confirmação).
                mfs = [e for e in hoje if e['fonte'] == 'manifesto']
                base = mfs or hoje
                placas = []
                for e in base:
                    if e['placa'] not in placas:
                        placas.append(e['placa'])
                dia_placas[d] = {p: 'prova' for p in placas}
                atual, desde_prova, parou = placas[-1], d, None
                if mfs:
                    ultimo_mf = placas[-1]
                continue
            vd = minhas.get(d, {})
            # Viagem dele em andamento (GPS) segura a placa mesmo depois de dias
            # sem documento. Mas quem manda é o MANIFESTO: carga que o robô abriu
            # antes e fechou tarde não pode devolver o motorista para a placa
            # velha depois de ele já ter manifesto novo em outra (Daniel, 25–28/07).
            if ultimo_mf and ultimo_mf not in vd:
                vd = {}
            if vd and (atual is None or atual in vd):
                cand = atual if atual in vd else sorted(vd)[0]
                fora, so_cartao = _outros(cand, d)
                if not fora:
                    atual = cand
                    cartao_alheio.extend((d, atual, q) for q in so_cartao)
                    dia_placas[d] = {atual: 'carry'}
                    desde_prova, parou = d, None
                    continue
            if atual is None:
                if parou:
                    motivo[d] = parou
                continue
            fora, _ = _outros(atual, d)
            if fora:
                parou = f"{atual} com {_quem(fora[0])} em {d:%d/%m}"
            elif (d - desde_prova).days > carry_max:
                parou = f"sem prova desde {desde_prova:%d/%m}"
            if fora or (d - desde_prova).days > carry_max:
                ultima_placa[d] = atual
                atual = None
                motivo[d] = parou
                continue
            dia_placas[d] = {atual: 'carry'}

        dias_periodo = list(_dias(ini, fim))

        def _status(p, d):
            vs = minhas.get(d, {}).get(p)
            if vs:
                return 'vazia' if all(v['vazia'] for v in vs) else 'viagem'
            km = gps_dia.get((p, d))
            if km is None:
                return 'sem_gps'
            return 'rodou' if km >= 30 else 'parado'

        # Segmentos: faixas contínuas por placa, dentro do período.
        segs = []
        for p in sorted({p for d in dias_periodo for p in dia_placas.get(d, {})}):
            dias_p = [d for d in dias_periodo if p in dia_placas.get(d, {})]
            for a, b in _faixas(dias_p):
                ev = [e for e in evs if e['placa'] == p and a <= e['dia'] <= b]
                # O carregamento que abre o período vem de uma prova anterior a ele.
                ancora = None
                if dia_placas[a][p] == 'carry':
                    ant = [e for e in evs if e['placa'] == p and e['dia'] < a]
                    ancora = ant[-1] if ant else None
                n_mf = len({e['ref'] for e in ev if e['fonte'] == 'manifesto'})
                n_vc = sum(1 for e in ev if e['fonte'] == 'valecard')
                vgs = {}
                for d in _dias(a, b):
                    for v in minhas.get(d, {}).get(p, []):
                        vgs[v['numero']] = v
                vgs = sorted(vgs.values(), key=lambda v: (v['ord'], v['numero']))
                tem_mf = (n_mf > 0 or (ancora is not None and ancora['fonte'] == 'manifesto')
                          or any(not v['vazia'] for v in vgs))
                # Período sustentado SÓ pela viagem do Embarques: nenhum documento
                # dentro dele. Acontece quando o robô fecha a viagem tarde e ela
                # cobre dias em que o motorista já aparece em outra placa.
                so_viagem = not ev and bool(vgs) and not (ancora is not None and ancora['fonte'] == 'manifesto')
                segs.append({
                    'placa': p, 'ini': a.isoformat(), 'fim': b.isoformat(),
                    'dias': (b - a).days + 1,
                    'manifestos': n_mf, 'abastecimentos': n_vc,
                    'so_valecard': not tem_mf,
                    'so_viagem': so_viagem,
                    'ancora': ({'dia': ancora['dia'].isoformat(), 'fonte': ancora['fonte'],
                                'ref': ancora['ref']} if ancora else None),
                    'provas': [{'dia': e['dia'].isoformat(), 'fonte': e['fonte'], 'ref': e['ref'],
                                'detalhe': e.get('detalhe') or ''} for e in ev],
                    'viagens': [{k: v.get(k) for k in ('numero', 'vazia', 'rota', 'saida', 'chegada', 'encerrada', 'status')}
                                for v in vgs],
                })
        segs.sort(key=lambda s: (s['ini'], s['fim'], s['placa']))

        # Linha do tempo: um item por dia, com a situação medida.
        linha = []
        for d in dias_periodo:
            ps = dia_placas.get(d)
            if ps:
                p = list(ps)[-1]
                linha.append({'dia': d.isoformat(), 'placas': list(ps), 'tipo': ps[p], 'status': _status(p, d)})
            else:
                linha.append({'dia': d.isoformat(), 'placas': [], 'tipo': None, 'status': None})

        sem_prova = []
        for a, b in _faixas([d for d in dias_periodo if d not in dia_placas]):
            txt = motivo.get(a, '')
            antes = [x for x in ultima_placa if x <= a]
            ultima = ultima_placa[max(antes)] if antes else None
            if ultima and gps_dia:
                st = [_status(ultima, d) for d in _dias(a, b)]
                parados = sum(1 for x in st if x == 'parado')
                if parados == len(st):
                    txt += f"; {ultima} parada no período"
                elif parados:
                    txt += f"; {ultima} parada em {parados} dia(s)"
            sem_prova.append({'ini': a.isoformat(), 'fim': b.isoformat(), 'dias': (b - a).days + 1,
                              'motivo': txt.lstrip('; ')})

        # Placa com dois motoristas no mesmo dia — só prova contra prova.
        dup = []
        for d in dias_periodo:
            for p, tipo in dia_placas.get(d, {}).items():
                if tipo != 'prova':
                    continue
                fora, so_cartao = _outros(p, d)
                cartao_alheio.extend((d, p, q) for q in so_cartao)
                if fora:
                    dup.append({'dia': d.isoformat(), 'placa': p, 'com': [_quem(q) for q in fora]})
        compartilhadas.extend(dict(x, motorista=m['nome']) for x in dup)

        alertas = []
        if not segs:
            alertas.append({'tipo': 'sem_prova_periodo',
                            'texto': 'Nenhum manifesto nem abastecimento no período — informar a situação (férias, atestado, INSS).'})
        elif sem_prova:
            alertas.append({'tipo': 'sem_prova',
                            'texto': 'Dias sem prova: ' + ', '.join(
                                _fmt_faixa(x) + (f" ({x['motivo']})" if x['motivo'] else '') for x in sem_prova)
                                     + ' — folga, férias, atestado ou admissão?'})
        for s in segs:
            if s.get('so_viagem'):
                alertas.append({'tipo': 'so_viagem',
                                'texto': f"{s['placa']} {_fmt_faixa(s)} vem só da viagem "
                                         f"{', '.join(v['numero'] for v in s['viagens'][:3])} do Embarques, "
                                         f"sem documento no período — conferir."})
            if s['so_valecard']:
                alertas.append({'tipo': 'so_valecard',
                                 'texto': f"{s['placa']} {_fmt_faixa(s)} só por abastecimento (ValeCard) — validar manualmente."})
        if dup:
            alertas.append({'tipo': 'compartilhada',
                            'texto': 'Placa com outro motorista no mesmo dia: '
                                     + '; '.join(f"{_dm(x['dia'])} {x['placa']} ({', '.join(x['com'])})" for x in dup)})
        desc = [(e, pv) for e, pv in descartados.get(ch, []) if ini <= e['dia'] <= fim]
        if desc:
            alertas.append({'tipo': 'vc_descartado',
                            'texto': 'Abastecimento em nome dele em outra placa durante a própria viagem '
                                     '(não entrou na escala — nome ou cartão trocado?): '
                                     + '; '.join(f"{e['dia']:%d/%m} {e['placa']} ({e.get('detalhe') or ''}) — viagem no {pv}"
                                                 for e, pv in desc)})
        alheio = sorted({(d, p, _quem(q)) for d, p, q in cartao_alheio if ini <= d <= fim})
        if alheio:
            alertas.append({'tipo': 'vc_outro_nome',
                            'texto': 'Abastecimento em nome de outra pessoa na placa dele durante a viagem (cartão de outro?): '
                                     + '; '.join(f"{d:%d/%m} {p} ({q})" for d, p, q in alheio)})
        rodou = [x for x in linha if x['tipo'] == 'carry' and x['status'] == 'rodou']
        if rodou and viagens:
            alertas.append({'tipo': 'rodou_sem_viagem',
                            'texto': 'Placa andou sem viagem no Embarques: '
                                     + ', '.join(f"{_dm(x['dia'])} {x['placas'][-1]} "
                                                 f"({gps_dia.get((x['placas'][-1], date.fromisoformat(x['dia'])))} km)"
                                                 for x in rodou)})

        saida.append({
            'chave': ch, 'nome': m['nome'], 'funcao': m.get('funcao') or '',
            'cpf': m.get('cpf') or '', 'segmentos': segs, 'sem_prova': sem_prova,
            'alertas': alertas, 'linha': linha,
        })

    saida.sort(key=lambda x: x['nome'].upper())
    return {'motoristas': saida, 'compartilhadas': compartilhadas}


def _dm(iso):
    return f'{iso[8:10]}/{iso[5:7]}'


def _fmt_faixa(x):
    return _dm(x['ini']) if x['ini'] == x['fim'] else f"{_dm(x['ini'])}–{_dm(x['fim'])}"


def periodo_texto(seg):
    """O texto que o RH usa na planilha: '21/07 ATE 02/08'."""
    return f"{_dm(seg['ini'])} ATE {_dm(seg['fim'])}"
