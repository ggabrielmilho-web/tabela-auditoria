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


def montar(ini, fim, motoristas, eventos, carry_max=CARRY_MAX_DIAS):
    """Escala do período.

    motoristas: lista de {'chave', 'nome', 'funcao'} — a lista do RH (folha).
    eventos: lista de {'chave' (motorista) ou None, 'nome_fonte', 'dia', 'placa',
             'fonte' ('manifesto'|'valecard'), 'ref', 'detalhe'} — só placas
             da frota, já em Mercosul. `chave=None` é motorista fora da lista,
             que ainda assim "ocupa" a placa.

    Retorna {'motoristas': [...], 'compartilhadas': [...]}.
    """
    # Quem usou cada placa em cada dia (prova, não carregamento).
    uso = defaultdict(lambda: defaultdict(set))          # placa -> dia -> {nome}
    por_mot = defaultdict(list)
    for e in eventos:
        uso[e['placa']][e['dia']].add(e['chave'] or ('~' + e['nome_fonte']))
        if e['chave']:
            por_mot[e['chave']].append(e)

    nomes = {m['chave']: m['nome'] for m in motoristas}
    saida, compartilhadas = [], []

    for m in motoristas:
        evs = sorted(por_mot.get(m['chave'], []), key=lambda e: (e['dia'], e['fonte'] != 'manifesto', e['ref']))
        prova_dia = defaultdict(list)                      # dia -> [eventos]
        for e in evs:
            prova_dia[e['dia']].append(e)

        dia_placas = {}                                    # dia -> {placa: 'prova'|'carry'}
        motivo = {}                                        # dia sem placa -> por que o carregamento parou
        atual, desde_prova, parou = None, None, None
        inicio_leitura = min([ini] + [e['dia'] for e in evs])
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
                continue
            if atual is None:
                if parou:
                    motivo[d] = parou
                continue
            outros = sorted(q for q in uso[atual].get(d, ()) if q != m['chave'])
            if outros:
                parou = f"{atual} com {nomes.get(outros[0], outros[0].lstrip('~'))} em {d:%d/%m}"
            elif (d - desde_prova).days > carry_max:
                parou = f"sem prova desde {desde_prova:%d/%m}"
            if outros or (d - desde_prova).days > carry_max:
                atual = None
                motivo[d] = parou
                continue
            dia_placas[d] = {atual: 'carry'}

        dias_periodo = list(_dias(ini, fim))

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
                tem_mf = n_mf > 0 or (ancora is not None and ancora['fonte'] == 'manifesto')
                segs.append({
                    'placa': p, 'ini': a.isoformat(), 'fim': b.isoformat(),
                    'dias': (b - a).days + 1,
                    'manifestos': n_mf, 'abastecimentos': n_vc,
                    'so_valecard': not tem_mf,
                    'ancora': ({'dia': ancora['dia'].isoformat(), 'fonte': ancora['fonte'],
                                'ref': ancora['ref']} if ancora else None),
                    'provas': [{'dia': e['dia'].isoformat(), 'fonte': e['fonte'], 'ref': e['ref'],
                                'detalhe': e.get('detalhe') or ''} for e in ev],
                })
        segs.sort(key=lambda s: (s['ini'], s['fim'], s['placa']))

        sem_prova = [{'ini': a.isoformat(), 'fim': b.isoformat(), 'dias': (b - a).days + 1,
                      'motivo': motivo.get(a, '')}
                     for a, b in _faixas([d for d in dias_periodo if d not in dia_placas])]

        # Placa com dois motoristas no mesmo dia — só prova contra prova.
        dup = []
        for d in dias_periodo:
            for p, tipo in dia_placas.get(d, {}).items():
                if tipo != 'prova':
                    continue
                outros = sorted(q for q in uso[p].get(d, ()) if q != m['chave'])
                if outros:
                    dup.append({'dia': d.isoformat(), 'placa': p,
                                'com': [nomes.get(q, q.lstrip('~')) for q in outros]})
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
            if s['so_valecard']:
                alertas.append({'tipo': 'so_valecard',
                                 'texto': f"{s['placa']} {_fmt_faixa(s)} só por abastecimento (ValeCard) — validar manualmente."})
        if dup:
            alertas.append({'tipo': 'compartilhada',
                            'texto': 'Placa com outro motorista no mesmo dia: '
                                     + '; '.join(f"{_dm(x['dia'])} {x['placa']} ({', '.join(x['com'])})" for x in dup)})

        saida.append({
            'chave': m['chave'], 'nome': m['nome'], 'funcao': m.get('funcao') or '',
            'cpf': m.get('cpf') or '', 'segmentos': segs, 'sem_prova': sem_prova,
            'alertas': alertas,
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
