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


def grafias(placa):
    """As grafias possíveis da mesma placa no dado bruto: Mercosul + antiga."""
    s = limpar(placa)
    formas = {s}
    if _RE_MERCOSUL_CONVERTIVEL.fullmatch(s):        # Mercosul → gera a antiga
        formas.add(s[:4] + str(_LETRAS.index(s[4])) + s[5:])
    elif _RE_ANTIGA.fullmatch(s):                    # antiga → gera a Mercosul
        formas.add(s[:4] + _LETRAS[int(s[4])] + s[5:])
    return list(formas)
