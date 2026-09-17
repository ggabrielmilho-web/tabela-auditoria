# -*- coding: utf-8 -*-
"""Regressão do acesso à aba CIOT — sessão × link de leitura (token). Usa o banco do .env.

O link do WhatsApp abre SEM login, então a garantia é negativa: com ele só se lê a
aba CIOT. Nenhuma outra tela, nenhuma ação, nenhuma sessão criada.

    python -X utf8 _teste_ciot_acesso.py

⚠ GERA UM LINK NOVO no fim (testa a rotação) — o link que foi no WhatsApp deixa de abrir.
Por isso só roda em banco local; contra outro host exige --pode-rotacionar.
"""
import os
import sys
import server
import ciot_conferencia as cc

falhas = []


def confere(nome, obtido, esperado):
    ok = obtido in esperado if isinstance(esperado, (tuple, set)) else obtido == esperado
    print(f"{'OK  ' if ok else 'FALHA'} {nome}: {obtido}" + ('' if ok else f' (esperado {esperado})'))
    if not ok:
        falhas.append(nome)


def main():
    if os.getenv('DB_HOST', 'localhost') not in ('localhost', '127.0.0.1') and '--pode-rotacionar' not in sys.argv:
        print('Recusado: este teste troca o link do WhatsApp. Rode no banco local '
              '(ou passe --pode-rotacionar sabendo disso).')
        return False
    conn = server.get_db()
    with conn, conn.cursor() as cur:
        cc.garantir_tabelas(cur)
        token = cc.token_ativo(cur)
    conn.close()

    app = server.app
    c = app.test_client()

    # sem nada
    confere('sem login /ciot', c.get('/ciot').status_code, 302)
    confere('sem login API', c.get('/api/ciot/pendencias').status_code, 401)

    # com o link
    confere('link /ciot', c.get(f'/ciot?t={token}').status_code, 200)
    r = c.get(f'/api/ciot/pendencias?t={token}')
    j = r.get_json()
    confere('link API', r.status_code, 200)
    confere('link API modo', j.get('modo'), 'leitura')
    confere('link API não expõe o link', 'link' in j, False)
    with c.session_transaction() as s:
        confere('link não cria sessão', 'user_id' in s, False)

    # o link não abre mais nada
    confere('link não roda conferência', c.post(f'/api/ciot/rodar?t={token}').status_code, 401)
    confere('link não gera link novo', c.post(f'/api/ciot/link?t={token}').status_code, 401)
    for rota in ('/', '/inicio', '/dre', '/embarques', '/admin', '/pgr', '/verda', '/contabil'):
        confere(f'link não abre {rota}', c.get(f'{rota}?t={token}').status_code, (302, 401, 403, 410))
    for api in ('/api/me', '/api/auditoria', '/api/admin/users', '/api/embarques/cargas'):
        confere(f'link não abre {api}', c.get(f'{api}?t={token}').status_code, (401, 403))

    # link errado
    confere('link inválido /ciot', c.get('/ciot?t=xxx').status_code, 410)
    confere('link inválido API', c.get('/api/ciot/pendencias?t=xxx').status_code, 403)

    # sessão sem a aba e com a aba
    with c.session_transaction() as s:
        s.update(user_id=1, role='viewer', nome='t', paginas_permitidas=['auditoria'])
    confere('viewer sem aba /ciot', c.get('/ciot').status_code, 302)
    confere('viewer sem aba API', c.get('/api/ciot/pendencias').status_code, 403)
    confere('viewer sem aba gera link', c.post('/api/ciot/link').status_code, 403)
    with c.session_transaction() as s:
        s['paginas_permitidas'] = ['ciot']
    j = c.get('/api/ciot/pendencias').get_json()
    confere('viewer com aba modo', j.get('modo'), 'sessao')
    confere('viewer não vê o link', 'link' in j, False)
    confere('viewer com aba não gera link', c.post('/api/ciot/link').status_code, 403)

    # admin vê e rotaciona
    with c.session_transaction() as s:
        s['role'] = 'admin'
    j = c.get('/api/ciot/pendencias').get_json()
    confere('admin vê o link', j.get('link', {}).get('relativo'), f'/ciot?t={token}')
    novo = c.post('/api/ciot/link').get_json()['link']['relativo'].split('t=')[1]
    confere('link novo é outro', novo != token, True)

    c2 = app.test_client()
    confere('link antigo morre', c2.get(f'/ciot?t={token}').status_code, 410)
    confere('link novo abre', c2.get(f'/ciot?t={novo}').status_code, 200)

    print(f'\n{len(falhas)} falha(s)')
    return not falhas


if __name__ == '__main__':
    raise SystemExit(0 if main() else 1)
