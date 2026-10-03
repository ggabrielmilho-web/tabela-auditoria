# -*- coding: utf-8 -*-
"""Normalização de placa (antiga ↔ Mercosul).

Extraído de `server.py` para ser compartilhado com o módulo de PGR sem criar
import circular (server importa pgr) e sem repetir a lógica — o handoff já
lista lógica gêmea duplicada como risco conhecido do projeto.

`server.py` segue expondo `_placa_mercosul` / `_placa_grafias` como aliases,
então nenhum ponto de chamada existente muda.
"""

import re

_LETRAS = 'ABCDEFGHIJ'   # conversão oficial do 5º caractere: 0→A, 1→B, … 9→J

_RE_ANTIGA = re.compile(r'[A-Z]{3}[0-9]{4}')
_RE_MERCOSUL = re.compile(r'[A-Z]{3}[0-9][A-Z][0-9]{2}')
_RE_MERCOSUL_CONVERTIVEL = re.compile(r'[A-Z]{3}[0-9][A-J][0-9]{2}')


def limpar(placa):
    """Só alfanumérico, maiúsculo."""
    return re.sub(r'[^A-Za-z0-9]', '', str(placa or '')).upper()


def mercosul(placa):
    """Normaliza para o padrão Mercosul (rótulo único por veículo).

    Conversão oficial antigo (LLL-NNNN) → Mercosul (LLL N L NN): muda SOMENTE o
    5º caractere (o 2º dígito), trocando o dígito por letra na ordem fixa
    0→A, 1→B, 2→C, 3→D, 4→E, 5→F, 6→G, 7→H, 8→I, 9→J. Os demais não mudam.
    Placa já em Mercosul (ou fora do padrão) é mantida como está. Assim as duas
    grafias do mesmo veículo colapsam numa única chave Mercosul.
    """
    s = limpar(placa)
    if _RE_ANTIGA.fullmatch(s):
        return s[:4] + _LETRAS[int(s[4])] + s[5:]
    return s


def eh_mercosul(placa):
    """True se a placa CRUA já está em Mercosul (identidade atual do veículo).

    Usado para desempatar colisão: a conversão antiga→Mercosul pode gerar uma
    string idêntica à placa Mercosul real de OUTRO veículo.
    """
    return bool(_RE_MERCOSUL.fullmatch(limpar(placa)))


def sql_chave(coluna):
    """Expressão SQL que leva `coluna` à CHAVE Mercosul — a mesma regra de `mercosul()`.

    A placa gravada e mostrada é a do SSW (03/10/2026: GZQ3080 continua GZQ3080); a chave
    só serve para comparar duas grafias do mesmo veículo dentro do SQL."""
    return ("CASE WHEN substring(upper(trim({c})) from 5 for 1) BETWEEN '0' AND '9' "
            "THEN overlay(upper(trim({c})) placing "
            "chr(65 + substring(upper(trim({c})) from 5 for 1)::int) from 5 for 1) "
            "ELSE upper(trim({c})) END").format(c=coluna)


def rotulos(cruas):
    """{chave Mercosul: placa a MOSTRAR}, a partir das grafias cruas vistas na fonte.

    A placa mostrada é sempre uma que a fonte tem — nunca a chave convertida (03/10/2026:
    GZQ3080 aparecia como GZQ3A80, placa que não existe). `cruas` vem em ordem de preferência
    (a mais recente primeiro). Se a própria Mercosul aparece crua, ela é a placa — é a mesma
    regra de colisão do cadastro (GZV1A50 real × GZV1050 de outro dono)."""
    vistas = {}
    for c in cruas or ():
        c = limpar(c)
        if c:
            vistas.setdefault(mercosul(c), []).append(c)
    return {k: (k if k in v else v[0]) for k, v in vistas.items()}


_RE_PLACA_TEXTO = re.compile(r'\b[A-Z]{3}[0-9][A-Z0-9][0-9]{2}\b')


def trocar_no_texto(texto, mapa):
    """Troca, dentro de um texto pronto, cada placa pela do `mapa` (chave → placa real)."""
    if not texto or not mapa:
        return texto
    return _RE_PLACA_TEXTO.sub(lambda m: mapa.get(mercosul(m.group(0)), m.group(0)), texto)


def grafias(placa):
    """As grafias possíveis da mesma placa no dado bruto: Mercosul + antiga."""
    s = limpar(placa)
    formas = {s}
    if _RE_MERCOSUL_CONVERTIVEL.fullmatch(s):        # Mercosul → gera a antiga
        formas.add(s[:4] + str(_LETRAS.index(s[4])) + s[5:])
    elif _RE_ANTIGA.fullmatch(s):                    # antiga → gera a Mercosul
        formas.add(s[:4] + _LETRAS[int(s[4])] + s[5:])
    return list(formas)
