# Proposta de implantação — endereço do cliente como âncora (14/10/2026)

Base: laboratório de 30/09–07/10 (HANDOFF-EMBARQUES §28, HANDOFF-INSIGNIA §15.7). Nada disto está escrito em
código de produção; este documento é para revisão antes de começar.

## 1. O que muda para quem usa

Hoje a carga tem **chegada** ("entrou nos 20 km da cidade") e **conclusão**. Isso não diz se o caminhão chegou ao
CLIENTE: só 48% das chegadas gravadas estão no cliente; o resto é pátio, fila ou cidade vizinha (C-1015 "chegou" no
pátio da Rizza, a 9 km do cliente). A proposta acrescenta três informações, **sem mexer nas que existem**:

| campo novo | o que diz | quando aparece |
|---|---|---|
| **No cliente desde** | o caminhão parou ≥ 10 min a ≤ 2 km do ponto do cliente | só com prova |
| **Saiu do cliente** | afastou-se > 3 km e não voltou em 30 min (com aparelho congelado: uma janela "entre X e Y") | só com prova |
| **Na região do cliente** | rótulo: passou a ≤ 5 km sem encostar (transbordo, doca longe) | só com prova |

Sem prova o campo fica vazio e vale o de hoje. A chegada, a saída e a conclusão de hoje **continuam iguais** — o
lab mostrou que sobrescrevê-las criava "correção" na tela (horário que muda entre refreshes em 18% das chegadas, contra
3% com campos separados).

Números do lab (cargas do robô): âncora de destino em **80%** das cargas (com a GR); "no cliente" provado em **74%** das
entregues com âncora; 0 status recuando.

## 2. De onde vem o ponto do cliente (a âncora), em ordem

| ordem | fonte | regra | medido no lab |
|---|---|---|---|
| 1 | **aprendido pelo CNPJ** | ≥ 2 descargas anteriores paradas a ≤ 1 km entre si, fora de pátio/posto | 94% a ≤ 2 km; errada 6% |
| 2 | **local da GR** (`insignia_locais`, 512, todos com CNPJ e coordenada) | pelo `destino_cnpj` | 71% a ≤ 0,5 km; errada 14% |
| 3 | **endereço do cadastro geocodificado** | só endereço "bom" (número, não rodovia, CEP ≠ -000) + município confere | 64% a ≤ 2 km; errada 25% |
| 4 | **aprendido por tomador + cidade** | resolve a subcontratação Martins | 79% a ≤ 2 km |
| — | endereço ruim / nada | não usa: fica sem âncora (centroide como hoje) | — |

"Errada" = o caminhão nunca passou a menos de 5 km dela. Âncora errada não estraga nada: o campo novo fica vazio e
vale a régua de hoje.

- **Pátio/posto nunca ensina descarga:** célula onde ≥ 5 carretas dormem em ≥ 10 noites.
- **Coordenadas:** vão para as colunas que já existem em `locais_fontes` (`latitude`, `longitude`, `geocod_fonte`,
  `geocod_em`, hoje vazias). Carga inicial com o cache de 07/10 (233 endereços); endereço novo geocodificado pelo robô
  diário (são poucos: 169 CNPJs de destino distintos desde 27/08).
- **Tabela nova `embarques_ancoras`** (cnpj, tipo, lat, lng, raio, nº de provas, primeira/última prova): o aprendizado
  fica gravado e auditável, e não é recalculado a cada consulta. Atualizada pelo robô diário só com cargas JÁ
  concluídas (nunca olha o futuro da própria carga).

## 3. Onde roda

- **Módulo novo `embarques_ancora.py`:** âncora, aprendizado e a régua dos três eventos (D2b contra posição
  congelada; a série já vem com a Insignia e a carreta descongelada).
- **Quem grava:** o motor atemporal, a cada refresh (já roda), na mesma janela de 28 dias — convergente, como o
  resto. O worker ao vivo NÃO muda nesta fase: o campo aparece no refresh seguinte (o lab mediu ~0,5–1 h de atraso,
  aceitável para um campo "com prova").
- **Telas:** detalhe da carga e relatório (colunas novas); marcador do ponto do cliente no mapa; na torre, "viagem
  local" no lugar de "Documento sem saída" quando origem = destino (C-1221).
- **Aferidor:** uma verificação nova, informativa — chegada de hoje a > 2 km de um "no cliente" provado (mede quanto
  a chegada de hoje erra de lugar). Não muda nenhum achado existente.

## 4. Chaves e ordem de ligar

| chave | o que liga | volta |
|---|---|---|
| `EMBARQUES_ANCORA_GEOCODAR` | o robô geocodifica endereço bom novo | `=false`; dado fica |
| `EMBARQUES_ANCORA` | aprendizado + os três campos novos + telas | `=false`; campos novos param de ser escritos (os de hoje nunca mudaram) |
| `EMBARQUES_ANCORA_MACROS` (fase 2) | macro "CHEGADA NO CLIENTE" e último "FIM DE VIAGEM" como 2ª fonte | `=false` |

Desligadas = idêntico byte a byte ao de hoje (gate E0).

## 5. Etapas e gates (tudo no lab primeiro, dump novo → banco novo)

1. **Dado:** carga do cache + geocodificação dos faltantes em `locais_fontes`; conferir guarda de cidade e o perfil.
   Nenhuma régua lê. *Gate:* contagem por perfil, amostra no mapa.
2. **Âncoras:** `embarques_ancoras` sobre o dump novo. *Gate:* cobertura e acerto por camada contra o lab
   (80% / T1 94%), ponto a ponto contra as paradas reais.
3. **Eventos:** os três campos pelo motor. *Gate:* E0 byte a byte; ciclo até o ponto fixo; **nenhum campo de hoje
   muda** em nenhuma carga (compara_clones); % "no cliente" contra o lab; amostra conferida no GPS cru **com a carreta
   junto do cavalo** (lição da C-1472).
4. **Telas** (test_client, como o gate do mapa).
5. **Produção:** dado → `EMBARQUES_ANCORA_GEOCODAR` → `EMBARQUES_ANCORA`, com prévia em simulação e snapshot.
6. **Fase 2:** macros (refazer a medição com o dump de agora — a de 07/10 tinha 6 chegadas) e a saída do
   carregamento pela origem aprendida.

## 6. Decidido (Gabriel, 14/10/2026 — "de acordo" com as recomendações)

1. Os três campos com esses nomes: "No cliente desde", "Saiu do cliente", "Na região do cliente".
2. Macros na **fase 2** (a amostra de 07/10 é pequena e o FIM DE VIAGEM é misto).
3. Saída do carregamento (origem) na **fase 2** (o `origem_cnpj` é o remetente fiscal; precisa do aprendizado).
4. Geocodificação automática pelo robô para endereço novo: **sim**.
5. Só o **destino final** (nenhuma carga do robô com mais de um destino desde 27/08).
6. **Pedido junto:** mapear TODAS as páginas, filtros, contadores, exportações e robôs que leem a carga, decidir em
   cada um se o campo novo aparece, e um **teste massivo** cobrindo todos eles (§7).

Estimativa: etapas 1–4 em 2 a 3 sessões de trabalho; a 5 no dia seguinte, com a sua conferência.
