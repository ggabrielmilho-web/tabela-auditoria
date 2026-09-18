# -*- coding: utf-8 -*-
"""As listas de abas não podem divergir. Sem rede, sem banco.

    python -X utf8 _teste_abas.py

Aba que existe em PAGINAS_VALIDAS mas falta no admin.html é aba que NINGUÉM
consegue receber: só o admin a enxerga, pelo bypass de role. Foi o que aconteceu
com a Verda, que ficou fora das duas listas do admin desde que nasceu (18/09/2026).
Aba a mais no admin.html é o contrário: o admin concede e o servidor recusa.
"""
import re
import sys

SERVER, ADMIN, NAV = 'server.py', 'admin.html', 'nav-perms.js'


def _ler(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


def validas():
    bloco = re.search(r'PAGINAS_VALIDAS = \{(.*?)\}', _ler(SERVER), re.S).group(1)
    return {x.strip().strip("'") for x in bloco.replace('\n', ' ').split(',') if x.strip()}


def main():
    val = validas()
    html = _ler(ADMIN)
    listas = {
        'admin.html · formulário de criação': set(re.findall(r'class="pagina-check" value="(\w+)"', html)),
        'admin.html · modal de edição':       set(re.findall(r"\{ key: '(\w+)', label:", html)),
        # o nav tem a aba 'admin', que não é concedível — por isso a comparação é só de um lado
        'nav-perms.js':                       set(re.findall(r"page: '(\w+)'", _ler(NAV))),
    }
    ok = True
    for nome, tem in listas.items():
        falta = sorted(val - tem)
        sobra = sorted(tem - val - {'admin', 'inicio'})
        if falta or sobra:
            ok = False
            print(f'FALHA {nome}')
            if falta:
                print(f'      falta (ninguém consegue receber): {", ".join(falta)}')
            if sobra:
                print(f'      sobra (o servidor vai recusar):   {", ".join(sobra)}')
        else:
            print(f'OK    {nome} — {len(tem)} aba(s)')
    print(f'\nPAGINAS_VALIDAS: {len(val)} abas concedíveis')
    return ok


if __name__ == '__main__':
    raise SystemExit(0 if main() else 1)
