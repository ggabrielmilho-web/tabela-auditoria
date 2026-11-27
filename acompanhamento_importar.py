# -*- coding: utf-8 -*-
"""Importa o histórico da planilha TRACKING (jan–nov/2026) para a aba Acompanhamento.

    python -X utf8 acompanhamento_importar.py "TRACKING 2026.xlsx"             # só mostra o que faria
    python -X utf8 acompanhamento_importar.py "TRACKING 2026.xlsx" --aplicar   # grava

A planilha tem duas abas por mês (a geral e a da Nestlé), uma linha por CT-e. O que vem do SSW (cliente,
NF, frete, placa, a B10) NÃO é importado — a tela lê do SSW. Só o que o dono da planilha digitava:

    AGENDA ........................ agenda
    DATA ENTREGA .................. entrega (o que ele digitou vale como entrega confirmada)
    STATUS ........................ status, quando é um dos conhecidos (ENTREGUE, EM DESCARGA, NO CLIENTE,
                                    REAGENDA, TRANSITO/CHEGANDO, PERCA DE AGENDAMENTO, RECUSADA/DEVOLV…);
                                    o resto (64 jeitos de escrever: datas, nomes, "at 176860") vai para a anotação
    OCORRÊNCIA .................... ocorrência no cliente
    VALOR DESCARGA ................ descarga informada (o número; 27/11/2026) e também a anotação
    VALOR DESCARGA, DIÁRIA,
    STATUS fora da lista .......... anotação ("Planilha: …"), para não se perder
    MOTORISTA ..................... não entra: a tela mostra o motorista do manifesto do SSW

Nunca passa por cima do que alguém já marcou na tela: só preenche campo vazio. Cada valor gravado entra no
histórico com o autor "Planilha TRACKING". Pode rodar de novo (o que já foi importado não muda).

A planilha é dado de cliente: NUNCA entra no git (o repositório é público). Em produção:
    scp "TRACKING 2026.xlsx" root@<servidor>:/tmp/tracking.xlsx
    docker cp /tmp/tracking.xlsx $(docker ps -q -f name=rizza-auditoria_app):/tmp/tracking.xlsx
    docker exec $(docker ps -q -f name=rizza-auditoria_app) python -X utf8 acompanhamento_importar.py /tmp/tracking.xlsx
    (conferir o relatório; depois o mesmo comando com --aplicar)
"""
import os
import re
import sys
import unicodedata
from collections import Counter
from datetime import date, datetime

import openpyxl
import psycopg2

import acompanhamento as ac

POR = 'Planilha TRACKING'
COLUNAS = ('NUMERO CTRC', 'AGENDA', 'DATA ENTREGA', 'MOTORISTA', 'STATUS', 'OCORRÊNCIA', 'VALOR DESCARGA', 'DIÁRIA')


def _sem_acento(s):
    return ''.join(c for c in unicodedata.normalize('NFD', str(s or '')) if unicodedata.category(c) != 'Mn')


def status_da_planilha(txt):
    """Texto livre do STATUS → código da lista (ou None, e o texto vai para a anotação)."""
    s = _sem_acento(txt).upper().strip()
    if not s:
        return None
    if s.startswith('ENTREGUE'):
        return 'ENTREGUE'
    if 'DESCARGA' in s:
        return 'DESCARGA'
    if s.startswith('NO CLIENTE'):
        return 'NO_CLIENTE'
    if 'PERCA' in s or 'PERDA' in s:
        return 'PERDEU_AGENDA'
    if s.startswith('REAGENDA'):
        return 'REAGENDADO'
    if s.startswith('TRANSITO') or s.startswith('CHEGANDO'):
        return 'TRANSITO'
    if 'RECUSAD' in s or s.startswith('DEVOLV'):
        return 'RECUSADA'
    return None


def _data(v):
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v or '').strip()
    m = re.fullmatch(r'(\d{2})/(\d{2})/(\d{2,4})', s)
    if m:
        a = m[3] if len(m[3]) == 4 else '20' + m[3]
        try:
            return date(int(a), int(m[2]), int(m[1])).isoformat()
        except ValueError:
            return None
    return None


def _txt(v):
    if v is None:
        return ''
    if isinstance(v, (datetime, date)):
        d = v if isinstance(v, date) and not isinstance(v, datetime) else v.date()
        return d.strftime('%d/%m/%Y')
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r'\s+', ' ', str(v)).strip()


def ler(caminho):
    """→ ({ctrc: {campo: valor}}, Counter do que achou). Linha repetida do mesmo CT-e: a última aba vence."""
    wb = openpyxl.load_workbook(caminho, read_only=True, data_only=True)
    cab_padrao = None
    linhas, cont = {}, Counter()
    for ws in wb.worksheets:
        rows = ws.iter_rows(max_col=22, values_only=True)
        # o cabeçalho nem sempre é a 1ª linha (SETEMBRO tem uma linha vazia antes): procura nas 5 primeiras
        topo, cab = [], None
        for r in rows:
            topo.append(r)
            c = [str(x or '').strip().upper() for x in r]
            if 'DATA ENTREGA' in c:
                cab = c
                break
            if len(topo) >= 5:
                break
        if cab is None:
            # sem cabeçalho nas 5 primeiras: se elas já são dado (CTRC na 1ª coluna), vale o cabeçalho das outras abas
            if cab_padrao is None or not any(re.fullmatch(r'[A-Z]{3}\d{6}-\d', str(r[0] or '').strip().upper())
                                             for r in topo if r):
                continue
            cab, corpo = cab_padrao, topo
        else:
            corpo = []
            if cab_padrao is None and 'MOTORISTA' in cab:
                cab_padrao = cab
        idx = {c: cab.index(c) for c in COLUNAS if c in cab}
        if 'NUMERO CTRC' not in idx:
            continue
        for r in list(corpo) + list(rows):
            ctrc = str(r[idx['NUMERO CTRC']] or '').strip().upper().replace(' ', '')
            if not re.fullmatch(r'[A-Z]{3}\d{6}-\d', ctrc):
                continue
            g = lambda c: r[idx[c]] if c in idx and idx[c] < len(r) else None
            m, notas = {}, []
            ag, en = _data(g('AGENDA')), _data(g('DATA ENTREGA'))
            if ag:
                m['agenda'] = ag
            if en:
                m['entrega'] = en
            st_txt = _txt(g('STATUS'))
            st = status_da_planilha(st_txt)
            # com data de entrega, a carga foi entregue (fora a recusa): o "REAGENDA" que sobrou no STATUS
            # (na Química as colunas andaram — "ENTREGUE" no MOTORISTA) vira histórico na anotação
            if en and st != 'RECUSADA':
                if st_txt and st != 'ENTREGUE':
                    notas.append(f'status "{st_txt}"')
                st = 'ENTREGUE'
            elif st_txt and not st:
                notas.append(f'status "{st_txt}"')
            if st:
                m['status'] = st
            oc = _txt(g('OCORRÊNCIA'))
            if oc:
                m['ocorrencia'] = oc[:300]
            # o MOTORISTA da planilha (primeiro nome) não entra: a tela mostra o do manifesto do SSW
            # VALOR DESCARGA numérico também vira o campo `descarga` (27/11/2026): = o pago ao chapa no 477 em
            # 1.060 de 1.159 entregas. Ele lançava por destinatário, num CT-e só. Acima de R$ 20 mil é erro de
            # digitação (houve um de R$ 450.775): fica só na anotação, como ele escreveu.
            vd = g('VALOR DESCARGA')
            if isinstance(vd, (int, float)) and not isinstance(vd, bool) and 0 < vd < 20000:
                m['descarga'] = f'{vd:.2f}'
            for col, rot in (('VALOR DESCARGA', 'descarga'), ('DIÁRIA', 'diária')):
                v = _txt(g(col))
                if v:
                    valor = 'R$ ' + v if re.fullmatch(r'[\d.]+', v) else v
                    notas.append(f'{rot} {valor}')
            if notas:
                m['anotacao'] = ('Planilha: ' + ' · '.join(notas))[:300]
            if not m:
                continue
            linhas[ctrc] = m
            cont['linhas'] += 1
            for k in m:
                cont[k] += 1
            if st_txt and not st:
                cont['status fora da lista → anotação'] += 1
    cont['CT-e'] = len(linhas)
    return linhas, cont


def conectar():
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
    except ImportError:
        pass
    return psycopg2.connect(host=os.getenv('DB_HOST', 'localhost'), port=os.getenv('DB_PORT', '5432'),
                            dbname=os.getenv('DB_NAME', 'postgres'), user=os.getenv('DB_USER', 'postgres'),
                            password=os.getenv('DB_PASSWORD', ''))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print(__doc__)
        sys.exit(1)
    aplicar = '--aplicar' in sys.argv
    linhas, cont = ler(args[0])
    print('Planilha:', ', '.join(f'{k} {v}' for k, v in cont.items()))
    conn = conectar()
    novos, ja, campos = 0, 0, Counter()
    try:
        with conn.cursor() as cur:
            ac.garantir_tabelas(cur)
            atuais = ac.marcacoes(cur, list(linhas))
            for ctrc, m in linhas.items():
                tem = (atuais.get(ctrc) or {}).get('campos') or {}
                falta = {k: v for k, v in m.items() if not tem.get(k)}
                if not falta:
                    ja += 1
                    continue
                novos += 1
                for k, v in falta.items():
                    campos[k] += 1
                    if aplicar:
                        ac.gravar(cur, [ctrc], k, ac.normalizar(k, v), POR)
        if aplicar:
            conn.commit()
        else:
            conn.rollback()
    finally:
        conn.close()
    print(f'{"Gravado" if aplicar else "Gravaria"}: {novos} CT-e com campo novo '
          f'({", ".join(f"{k} {v}" for k, v in campos.items())}); {ja} CT-e já tinham tudo.')
    if not aplicar:
        print('Nada foi gravado. Para gravar: o mesmo comando com --aplicar')


if __name__ == '__main__':
    main()
