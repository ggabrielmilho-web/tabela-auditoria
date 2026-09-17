# -*- coding: utf-8 -*-
"""Regressão da régua de ciot_conferencia.conferir — um caso por regra, sem rede nem banco.

    python -X utf8 _teste_ciot.py
"""
from datetime import date

import ciot_conferencia as cc

DESDE = date(2026, 9, 1)


def ctrb(c, **kw):
    r = {'ctrb': c, 'emissao': '2026-09-05T10:00:00', 'Tipo Operação': 'AGREGADO',
         'ciot': '520027042261', 'tabela_antt': 5000.0, 'placa_cavalo': 'AAA1B23',
         'placa_carreta': 'BBB2C34', 'cpf_motorista': '11122233344', 'motorista': 'FULANO',
         'manifesto': None, 'observacao': '', 'cidade_uf_origem': 'UBERLANDIA/MG',
         'cidade_uf_destino': 'SERRA/ES'}
    r.update(kw)
    return r


def mf(m, kc, nc=None, **kw):
    r = {'mf': m, 'd': '2026-09-05T00:00:00', 'kc': kc, 'nc': nc if nc is not None else (kc or '')[3:],
         'cav': 'AAA1B23', 'car': 'BBB2C34', 'cpf': '111.222.333-44', 'motorista': 'FULANO'}
    r.update(kw)
    return r


def tipos(pend, doc=None):
    return sorted(p['tipo'] for p in pend if doc is None or p['documento'] == doc)


def caso(nome, ctrbs, mfs, esperado):
    pend, _ = cc.conferir(ctrbs, mfs, DESDE)
    obtido = {}
    for p in pend:
        obtido.setdefault(p['documento'], []).append(p['tipo'])
    obtido = {k: sorted(v) for k, v in obtido.items()}
    esperado = {k: sorted(v) for k, v in esperado.items()}
    ok = obtido == esperado
    print(f"{'OK  ' if ok else 'FALHA'} {nome}" + ('' if ok else f'\n      esperado {esperado}\n      obtido   {obtido}'))
    return ok


def main():
    r = []
    # régua do CIOT
    assert cc.ciot_valido('520027032280/4846') and cc.ciot_valido('520027146303.7599')
    assert cc.ciot_valido(' 520027042261 ') and not cc.ciot_valido('0') and not cc.ciot_valido(None)
    assert not cc.ciot_valido('Mensagem recebida da ANTT (Gerando CIOT):<br />ERRO 200 -  -')
    assert cc.ciot_erro('Mensagem recebida da Pamcard:<br />4 - Rejeicao').startswith('Mensagem recebida da Pamcard: 4')
    assert cc.manifestos_do_073('UDI-FEC  029132-3, NOD-UDI  004852-6') == ['UDI029132-3', 'NOD004852-6']
    assert cc.substituto('X BAIXADO PELA EMISSAO DE NOVO CTRB:NOD004816-0 Y') == 'NOD004816-0'

    r.append(caso('tudo certo pelo 916', [ctrb('UDI000001-1')], [mf('UDI000100-1', 'UDI000001')], {}))
    r.append(caso('tudo certo só pelo 073',
                  [ctrb('UDI000001-1', manifesto='UDI-FEC  000100-1')], [mf('UDI000100-1', None, '000000')], {}))
    r.append(caso('sem CIOT', [ctrb('UDI000001-1', ciot=None)], [mf('UDI000100-1', 'UDI000001')],
                  {'UDI000001-1': ['sem_ciot']}))
    r.append(caso('CIOT com mensagem de erro',
                  [ctrb('UDI000001-1', ciot='Mensagem recebida da Pamcard:<br />4 - Rejeicao')],
                  [mf('UDI000100-1', 'UDI000001')], {'UDI000001-1': ['ciot_erro']}))
    r.append(caso('frota também confere', [ctrb('UDI000001-1', ciot='', **{'Tipo Operação': 'FROTA'})],
                  [mf('UDI000100-1', 'UDI000001')], {'UDI000001-1': ['sem_ciot']}))
    r.append(caso('CTRB sem manifesto e sem CIOT', [ctrb('RIO000001-1', ciot=None)], [],
                  {'RIO000001-1': ['ctrb_sem_manifesto', 'sem_ciot']}))
    r.append(caso('diária não entra',
                  [ctrb('UDI000001-1', ciot=None, tabela_antt=0.0, cidade_uf_destino='UBERLANDIA/MG')], [], {}))
    r.append(caso('antt zero mas viagem de verdade entra',
                  [ctrb('UDI000001-1', ciot=None, tabela_antt=0.0)], [],
                  {'UDI000001-1': ['ctrb_sem_manifesto', 'sem_ciot']}))
    r.append(caso('substituído responde pelo novo',
                  [ctrb('UDI000001-1', ciot=None, observacao='BAIXADO PELA EMISSAO DE NOVO CTRB:UDI000002-9'),
                   ctrb('UDI000002-9')],
                  [mf('UDI000100-1', 'UDI000001')], {}))
    r.append(caso('substituto sem CIOT acusa o substituto',
                  [ctrb('UDI000001-1', ciot=None, observacao='NOVO CTRB:UDI000002-9'),
                   ctrb('UDI000002-9', ciot=None, manifesto='UDI-FEC  000100-1')],
                  [mf('UDI000100-1', 'UDI000001')], {'UDI000002-9': ['sem_ciot']}))
    r.append(caso('"baixado" pela viagem seguinte NÃO é reemissão',
                  [ctrb('RIO000001-1', ciot=None, observacao='BAIXADO PELA EMISSAO DE NOVO CTRB:NOD000002-9'),
                   ctrb('NOD000002-9', emissao='2026-09-09T07:36:00', cidade_uf_origem='SERRA/ES',
                        cidade_uf_destino='UBERLANDIA/MG', manifesto='NOD-FEC  000300-1')],
                  [mf('NOD000300-1', 'NOD000002')],
                  {'RIO000001-1': ['ctrb_sem_manifesto', 'sem_ciot']}))
    r.append(caso('mesma rota uma semana depois é outra viagem',
                  [ctrb('UDI000001-1', ciot=None, observacao='NOVO CTRB:UDI000002-9'),
                   ctrb('UDI000002-9', emissao='2026-09-12T10:00:00', manifesto='UDI-FEC  000100-1')],
                  [mf('UDI000100-1', 'UDI000002')],
                  {'UDI000001-1': ['ctrb_sem_manifesto', 'sem_ciot']}))
    r.append(caso('reemissão sem aviso: erro no CIOT e irmão com CIOT logo depois',
                  [ctrb('UDI000001-1', ciot='Mensagem recebida da Pamcard:<br />4 - Rejeicao'),
                   ctrb('UDI000002-9', emissao='2026-09-05T10:11:00', manifesto='UDI-FEC  000100-1')],
                  [mf('UDI000100-1', 'UDI000002')], {}))
    r.append(caso('reemissão sem aviso: manifesto ficou no antigo, CIOT no novo',
                  [ctrb('UDI000001-1', ciot=None),
                   ctrb('UDI000002-9', emissao='2026-09-05T18:10:00')],
                  [mf('UDI000100-1', 'UDI000001', '000000')], {}))
    r.append(caso('irmão sem CIOT não salva ninguém',
                  [ctrb('UDI000001-1', ciot=None, manifesto='UDI-FEC  000100-1'),
                   ctrb('UDI000002-9', ciot=None, emissao='2026-09-05T11:00:00', manifesto='UDI-FEC  000101-1')],
                  [mf('UDI000100-1', 'UDI000001'), mf('UDI000101-1', 'UDI000002')],
                  {'UDI000001-1': ['sem_ciot'], 'UDI000002-9': ['sem_ciot']}))
    r.append(caso('mesma viagem 2 dias depois não é reemissão',
                  [ctrb('UDI000001-1', ciot=None, manifesto='UDI-FEC  000100-1'),
                   ctrb('UDI000002-9', emissao='2026-09-07T10:00:00', manifesto='UDI-FEC  000101-1')],
                  [mf('UDI000100-1', 'UDI000001'), mf('UDI000101-1', 'UDI000002')],
                  {'UDI000001-1': ['sem_ciot']}))
    r.append(caso('manifesto sem CTRB', [], [mf('UDI000100-1', None, '000000')],
                  {'UDI000100-1': ['manifesto_sem_ctrb']}))
    r.append(caso('manifesto cita CTRB que não existe', [], [mf('UDI000100-1', 'UDI000009')],
                  {'UDI000100-1': ['manifesto_sem_ctrb']}))
    r.append(caso('manifesto de antes do período não acusa', [], [mf('UDI000100-1', None, '000000', d='2026-08-20')], {}))
    r.append(caso('fórmula casa o órfão (vale como vínculo)',
                  [ctrb('UDI000001-1')], [mf('UDI000100-1', 'UDI000001', '000000')], {}))
    r.append(caso('fórmula casou com CTRB que já tem manifesto',
                  [ctrb('UDI000001-1'), ctrb('UDI000002-9', placa_cavalo='CCC3D45')],
                  [mf('UDI000100-1', 'UDI000001'),
                   mf('UDI000101-1', 'UDI000001', '000000', cav='CCC3D45', car='DDD4E56')],
                  {'UDI000101-1': ['manifesto_sem_ctrb'], 'UDI000002-9': ['ctrb_sem_manifesto']}))
    r.append(caso('073 e 916 apontam manifestos diferentes',
                  [ctrb('UDI000001-1', manifesto='UDI-FEC  000200-1')],
                  [mf('UDI000100-1', 'UDI000001'), mf('UDI000200-1', None, '000000')],
                  {'UDI000001-1': ['vinculo_divergente']}))
    r.append(caso('placa e motorista diferentes do manifesto',
                  [ctrb('UDI000001-1')],
                  [mf('UDI000100-1', 'UDI000001', cav='ZZZ9Z99', cpf='999.888.777-66')],
                  {'UDI000001-1': ['vinculo_divergente']}))
    r.append(caso('placa na grafia antiga não é divergência',
                  [ctrb('UDI000001-1', placa_cavalo='AAA1123')],
                  [mf('UDI000100-1', 'UDI000001', cav='AAA1B23')], {}))

    pend, _ = cc.conferir([ctrb('UDI000001-1')],
                          [mf('UDI000100-1', 'UDI000001', car='ZZZ9Z99', cpf='999.888.777-66', motorista='CICLANO')],
                          DESDE)
    d = pend[0]['detalhe']
    assert d.startswith('tem CIOT · manifesto UDI000100-1 · '), d
    assert 'carreta: CTRB BBB2C34 × manifesto ZZZ9Z99' in d and 'motorista: CTRB FULANO × manifesto CICLANO' in d, d

    # mensagem
    from datetime import datetime
    base = {'documento': 'UDI000001-1', 'tipo_operacao': 'Agregado', 'placa': 'AAA1B23', 'detalhe': 'x'}
    txt = cc.montar_mensagem([dict(base, tipo='ctrb_sem_manifesto', manifesto=''),
                              dict(base, tipo='sem_ciot', manifesto='UDI000100-1')],
                             3, datetime(2026, 9, 17, 14, 5))
    assert '*UDI000001-1* · Agregado · AAA1B23 · MDF UDI000100-1 — sem CIOT, sem manifesto' in txt, txt
    assert '1 documento(s)' in txt and '3 documento(s) em aberto' in txt, txt

    from datetime import datetime as _dt
    res = cc.montar_resumo([dict(base, tipo='sem_ciot', manifesto='', emissao=_dt(2026, 9, 15, 10), avisado_em=None),
                            dict(base, documento='UDI000002-9', tipo='ciot_erro', manifesto='',
                                 emissao=_dt(2026, 9, 12, 9), avisado_em=_dt(2026, 9, 12, 12))],
                           2, datetime(2026, 9, 17, 8, 5))
    linhas = res.split('\n')
    assert '2 documento(s) · 2 pendência(s) resolvida(s)' in res, res
    # CIOT com erro também é "sem CIOT": as duas somam na mesma categoria
    assert 'sem CIOT (tem manifesto): *2*' in res and 'sem CIOT e sem manifesto' not in res, res
    i1 = next(i for i, l in enumerate(linhas) if 'UDI000002-9' in l)
    i2 = next(i for i, l in enumerate(linhas) if 'UDI000001-1' in l)
    assert i1 < i2, 'resumo: mais antigo primeiro'
    assert '🆕 *UDI000001-1*' in res and '🆕 *UDI000002-9*' not in res, res
    assert 'emitido 12/09' in linhas[i1], res
    assert cc.montar_resumo([], 0, datetime(2026, 9, 17, 8, 5)).startswith('✅'), 'dia zerado também sai'
    antigo = cc.montar_resumo([dict(base, tipo='sem_ciot', manifesto='', emissao=_dt(2026, 9, 1, 10), avisado_em=None),
                               dict(base, documento='UDI000002-9', tipo='sem_ciot', manifesto='',
                                    emissao=_dt(2026, 9, 16, 9), avisado_em=None)],
                              0, datetime(2026, 9, 17, 8, 5))
    assert 'UDI000001-1' not in antigo and 'UDI000002-9' in antigo, antigo
    assert '+1 documento(s) com mais de 7 dias' in antigo and '2 documento(s)' in antigo, antigo

    # imagem: gera PNG e a legenda diz quantos sem manifesto também estão sem CIOT
    import ciot_imagem
    pend_img = [dict(base, chave='x1', tipo='sem_ciot', manifesto='', emissao=_dt(2026, 9, 16, 9), avisado_em=None),
                dict(base, chave='x2', tipo='ctrb_sem_manifesto', manifesto='', emissao=_dt(2026, 9, 16, 9),
                     avisado_em=None, detalhe='SEM CIOT · A/MG → B/SP · nenhum'),
                dict(base, chave='x3', documento='UDI000002-9', tipo='ctrb_sem_manifesto', manifesto='',
                     emissao=_dt(2026, 9, 16, 9), avisado_em=None, detalhe='tem CIOT · C/MG → D/SP · nenhum')]
    dados = cc.dados_aviso('resumo', pend_img, 3, 0, datetime(2026, 9, 17, 8, 5))
    assert '• 1 sem CIOT e sem manifesto' in dados['legenda'], dados['legenda']
    assert '• 1 sem manifesto (tem CIOT)' in dados['legenda'], dados['legenda']
    # categorias exclusivas somam o número de documentos
    assert sum(n for _, n, _ in dados['contadores']) == 2, dados['contadores']
    docs_img = {d['documento']: d for d in dados['documentos']}
    assert docs_img['UDI000002-9']['tem_ciot'] and not docs_img['UDI000001-1']['tem_ciot']
    assert docs_img['UDI000001-1']['detalhes'] == ['A/MG → B/SP'], docs_img
    assert ciot_imagem.gerar_png(dados)[:8] == b'\x89PNG\r\n\x1a\n'
    assert cc.dados_aviso('resumo', [], 0, 0, datetime(2026, 9, 17, 8, 5))['legenda'].startswith('✅')
    # cada mensagem diz qual é: resumo = tudo em aberto no período; novas = só a diferença
    assert 'Tudo que segue em aberto (emitidos de 01/09 a 17/09): *2 documento(s)*' in dados['legenda'], dados['legenda']
    assert 'emitidos de 01/09 a 17/09' in dados['subtitulo'], dados['subtitulo']
    nv = cc.dados_aviso('novas', pend_img[:1], 5, 0, datetime(2026, 9, 17, 10, 15), None,
                        datetime(2026, 9, 17, 8, 15))
    assert 'Só o que apareceu desde o último aviso (17/09 08:15): *1 documento(s)*' in nv['legenda'], nv['legenda']
    assert 'Em aberto no total: 5 documento(s)' in nv['legenda'] and 'resumo das' in nv['rodape'], nv

    # agendamento: folga depois do refresh e nunca durante
    from datetime import timedelta as _td
    fim = datetime(2026, 9, 17, 11, 4)                      # refresh das 08:00 BRT terminou 08:04
    assert not cc.deve_rodar(fim + _td(minutes=3), fim, False, None, None), 'dentro da folga'
    assert cc.deve_rodar(fim + _td(minutes=11), fim, False, None, None), 'passada a folga'
    assert not cc.deve_rodar(fim + _td(minutes=30), fim, True, None, None), 'refresh rodando'
    assert not cc.deve_rodar(fim + _td(minutes=30), fim, False, fim + _td(minutes=11), fim), 'já rodou'
    assert not cc.deve_rodar(fim + _td(minutes=75), fim, False, fim + _td(minutes=11), fim), 'sem refresh novo não roda'
    novo_fim = fim + _td(hours=2)                            # refresh das 10:00
    assert cc.deve_rodar(novo_fim + _td(minutes=10), novo_fim, False, fim + _td(minutes=11), fim), 'refresh seguinte'
    cc.INTERVALO_MIN = 60                                    # depois do upgrade, se quiserem forçar
    assert cc.deve_rodar(fim + _td(minutes=75), fim, False, fim + _td(minutes=11), fim), 'intervalo forçado'
    cc.INTERVALO_MIN = 0

    class _Cur:                                              # resumo ainda não saiu hoje
        def execute(self, *a): pass
        def fetchone(self): return None
    antes = datetime(2026, 9, 17, 10, 50)                    # refresh das 05:30 BRT (07:50 BRT de fim)
    assert not cc.resumo_devido(_Cur(), datetime(2026, 9, 17, 8, 2), antes), 'resumo com dado velho'
    assert cc.resumo_devido(_Cur(), datetime(2026, 9, 17, 8, 15), fim), 'resumo com o refresh das 08:00'
    assert cc.resumo_devido(_Cur(), datetime(2026, 9, 17, 10, 5), antes), 'refresh falhou: sai assim mesmo'
    assert not cc.resumo_devido(_Cur(), datetime(2026, 9, 17, 7, 59), fim), 'antes da hora'

    print(f'\n{sum(r)}/{len(r)} casos')
    return all(r)


if __name__ == '__main__':
    raise SystemExit(0 if main() else 1)
