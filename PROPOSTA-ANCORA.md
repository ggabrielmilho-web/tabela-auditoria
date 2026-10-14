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
lab mostrou que sobrescrevê-las criava "correção" na tela (horário que muda entre refreshes em 19% das chegadas, contra
4% com campos separados — refeito em 14/10, §8).

Números do lab, refeitos com o dump de 14/10 (cargas do robô; §8): âncora de destino em **82%** das cargas (com a GR);
"no cliente" provado em **75%** das entregues com âncora; 0 status recuando.

## 2. De onde vem o ponto do cliente (a âncora), em ordem

| ordem | fonte | regra | medido no lab |
|---|---|---|---|
| 1 | **aprendido pelo CNPJ** | ≥ 2 descargas anteriores paradas a ≤ 1 km entre si, fora de pátio/posto | 94% a ≤ 2 km; errada 6% |
| 2 | **local da GR** (`insignia_locais`, 512, todos com CNPJ e coordenada) | pelo `destino_cnpj` | 71% a ≤ 0,5 km; errada 17% (14/10) |
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
  resto. A série é a MESMA do motor (`fontes_gps`: 3S + Insignia + carreta descongelada), nunca só a 3S: no dump de
  14/10, 13% das viagens já não aparecem na série da carreta da 3S (§8).
- **Viagem local (origem = destino):** o cliente é procurado a partir do CARREGAMENTO, não da "saída" — nela a saída
  não existe, e a régua de hoje grava como "saída" a carreta indo embora para a viagem seguinte (C-1221, C-549: o lab
  procurava depois dessa saída e não achava a visita ao cliente).
- **O worker ao vivo NÃO muda nesta fase:** o campo aparece no refresh seguinte (o lab mediu ~0,5–1 h de atraso,
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

## 7. Onde a carga é lida — e o que muda em cada lugar (inventário de 14/10)

**Nenhum status novo.** Os status seguem os de hoje (Aberta, Em rota, No destino, Entregue, Desengatada, Continuada,
Cancelada); quem filtra ou conta por status não muda. O que entra são os três campos da §1.

| onde | o que lê hoje | com a âncora ligada |
|---|---|---|
| Home `/embarques` — contadores (`/api/embarques/kpis`) e últimas 10 | status | **igual** (os contadores são por status) |
| Relatório `/embarques/relatorio` — lista e filtros | status, datas, placas | **3 colunas novas** + filtro novo "No cliente: provado / não provado / sem âncora" |
| Exportação CSV (`/api/embarques/cargas/csv`) | as colunas da lista | **3 colunas novas no FIM** (quem importa a planilha não perde a posição das colunas) |
| Detalhe/edição (`/embarques/<id>/editar`, PATCH) | tudo | os 3 campos **só leitura**; a edição manual não os altera; carga editada à mão é respeitada (`_editada_a_mao`) |
| Mapa da carga (`mapa-carga.html`, `/trajeto`) | trajeto, chegada | **marcador do ponto do cliente** (com o raio de 2 km) e os eventos novos na linha do tempo |
| Torre (`torre.py`, `/embarques/torre`) | status × datas ("confere") | "**viagem local**" no lugar de "Documento sem saída" quando origem = destino; o "confere" **não** usa os campos novos |
| Mapa em tempo real (`/api/rastreamento/posicoes`) | posição + carga ativa | igual |
| Ficha da viagem (`viagem.py`) | km e custo entre saída/chegada/conclusão | igual (fase 2, se quiser "tempo no cliente") |
| SM, ordens de coleta, manifestos sem ordem | documento | igual |
| Robô diário, worker, continuação, reconciliação | criam/fecham/ligam cargas | igual (não leem os campos novos) |
| Motor atemporal | deriva saída/chegada/conclusão | **grava os campos novos** (só com a chave); os de hoje intocados |
| Aferidor | achados | 1 verificação nova, informativa |
| Snapshot (`_snapshot_embarques.py`) | 5 tabelas | **+ `embarques_ancoras`** (as colunas novas já vêm com `embarques_cargas`) |
| Relatórios do WhatsApp (raspam `/dre` e `/faturamento`) | não leem embarques | igual — e o teste confere que essas telas não mudam |

### 7.1 Teste massivo (o que roda antes de pedir o ok)

Sempre num banco novo restaurado do dump do dia, cargas desde 27/08, código do commit × código novo:

1. **Chave desligada = idêntico byte a byte** em TUDO da tabela acima: motor, aferidor, robô diário real
   (`diario_lab`), e o JSON de todos os endpoints — lista em todos os filtros do relatório, detalhe, CSV, contadores,
   torre, posições, trajeto de cada carga, ficha da viagem, `/dre` e `/faturamento`.
2. **Chave ligada, nada do que existe muda:** em nenhuma carga muda status, saída, chegada, conclusão, motivo, ligação
   ou perna (compara_clones em todas as colunas); nos endpoints, a única diferença são os campos novos.
3. **Ciclo até o ponto fixo** (motor + pernas + janela) e o robô diário real rodando por cima: converge, sem
   oscilar.
4. **Ao vivo (replay pelo relógio de gravação, `_lab_replay`):** quantas vezes o campo novo muda entre refreshes —
   meta do lab: ≤ 3% (chegada) e ≤ 5% (saída do cliente).
5. **Conferência no GPS cru**, amostra por camada de âncora (aprendido / GR / endereço / tomador+cidade), com a
   carreta JUNTO do cavalo (SM ou as duas séries) — a lição da C-1472.
6. **Bateria de falhas do lab** (aparelho congelado no cliente e depois de sair, sinal perdido, posição falsa)
   contra a implementação, não contra o script do lab.
7. **Desempenho:** tempo da lista de 1.000, torre, mapa e trajeto com e sem a chave; tempo do motor por refresh.
8. **Edição manual e "confirmar entrega"** continuam funcionando, e o campo novo não sobrescreve o que foi editado à mão.
9. **Snapshot e restore** com a tabela nova, testados no lab.
10. **Produção:** prévia em simulação (`nice -n 19`), snapshot, ligar, conferir — a mesma receita de hoje.

## 8. Validação com o dump de 14/10/2026 (antes de escrever código)

Dump de produção de 14/10 13:29 UTC (`rizza_lab_1014`, escopo `todas` ligado), cargas do robô desde 19/08 (743;
582 entregues com âncora). Saídas em `_estudo_2026-10-14/` (fora do git — dado de cliente).

| ponto da proposta | lab 30/09–07/10 | 14/10 | |
|---|---|---|---|
| cargas com âncora de destino (com a GR) | 80% | **82%** | ✔ |
| "no cliente" provado nas entregues com âncora | 74% | **75%** | ✔ |
| "na região do cliente" (≤ 5 km) | — | 80% (30 só na região) | ✔ |
| status recuando / "no cliente" antes de "chegando" | 0 / 0 | **0 / 0** | ✔ |
| chegadas gravadas hoje que estão no cliente (≤ 0,5 km) | 48% | **47%** | ✔ |
| acerto ≤ 2 km, viagens observadas: aprendido CNPJ / endereço bom / tomador+cidade | 94 / 64 / 79% | **94 / 72 / 91%** | ✔ |
| ordem T1 → GR → endereço → tomador+cidade × GR por último | — | **436 × 424** "no cliente"; 84 × 93 âncoras erradas | ✔ a ordem proposta é a melhor |
| GR: "no cliente" / âncora errada | — / 14% | 65% / 17% (pega os casos mais difíceis) | ✔ |
| ao vivo — horário provisório errado: chegada hoje × campo próprio | 14% × 3% | **14% × 4%** | ✔ |
| ao vivo — entrega hoje × campo próprio | 9% × 5% | **9% × 5%** | ✔ |
| ao vivo — status recuando entre refreshes: hoje × campo próprio | 6,5% × 3,8% | **6,4% × 3,1%** | ✔ |
| ao vivo — atraso para aparecer (chegada / entrega, mediana) | 2,1 / 2,9 h | 2,1 / 2,8 h | ✔ |
| aparelho congelado 30 min no cliente: "chega" cedo, hoje × nova | 86–89% × 19–27% | **86% × 17%** | ✔ |
| sinal perdido ± 3 h: a nova perde a chegada | 9–18% | 12% | ✔ |

**O que mudou o desenho:**
- **13% das viagens não aparecem na série da 3S** (juiz só-3S: C-1420 a carreta ficou a 839 km; C-1495 a carreta
  dormiu). A queda aparente do aprendido pelo CNPJ (94% → 87%) era isso; contando só as viagens observadas, 94%. →
  a implementação usa a série do motor (§3).
- **Viagem local** precisa procurar o cliente desde o carregamento (§3): C-1221 e C-549 ficaram sem "no cliente".
- **Macros (fase 2) remedidas** no clone convergido: "CHEGADA NO CLIENTE" 22 casos (eram 6), mediana +0,9 h depois
  da chegada na cidade, ≤ 1 h em 12 — candidata a 2ª fonte do "No cliente"; último "FIM DE VIAGEM" 31 casos, > 6 h
  da conclusão em 16 — continua misto. A decisão de deixar para a fase 2 se mantém.
- O replay ainda usa só a 3S (o relógio de gravação é o `id` dela); a Insignia tem `coletada_em` e entra no replay da
  implementação.

## 9. Andamento

**Etapas 1 e 2 — feitas no lab em 14/10** (`embarques_ancora.py`, sem commit; nada o importa ainda — produção não
muda). Gate `_estudo_2026-10-14/lab/ancora_etapa12.py` num clone do dump de 14/10:
- coordenadas do cache em `locais_fontes`: 111 `endereco` (perfil bom, município conferido), 89 `endereco_fraco`, 15
  `fora_da_cidade` (sem coordenada) — os mesmos números do estudo; 1.058 CNPJs do cadastro sem nenhuma carga ficam
  para a geocodificação automática (só os de destino de carga).
- pátios/postos: 15 células (as do lab). **Achado:** o local da GR não pode "salvar" célula de pátio — a GR cadastra
  os pátios da própria Rizza ("RIZZA BARRACÃO", "GARAGEM RIZZA", CAIS); com ela ficavam só 6 pátios.
- provas: 1.440 paradas de 756 cargas concluídas, em 46 s (o robô diário aguenta).
- **O módulo reproduz o lab exatamente** (modo "só o passado"): âncora por camada 186 / 234 / 70 / 116 e 137 sem
  âncora — idênticos ao `_lab_ancora`; "no cliente" 436 — idêntico. Acerto ≤ 2 km nas viagens observadas: aprendido
  CNPJ 95%, GR 87%, endereço 84%, tomador+cidade 91%.
- **modo produção** (aprende com tudo o que já foi concluído, menos a própria carga): cobertura **90%**, "no cliente"
  em 473 de 602 entregues com âncora e GPS (79%).
- viagem local: C-1221 e C-549 passam a achar o cliente (0,1 e 0,003 km), procurando desde o carregamento.
- **separando a Martins** (modo produção): SEM ela, 568 cargas — âncora em 91%, acerto ≤ 2 km aprendido CNPJ 92% /
  GR 85% / endereço 87% / tomador+cidade 88%, "no cliente" 365 de 468 (78%); SÓ a Martins, 175 — a camada
  tomador+cidade cobre 67% (91% ≤ 2 km), âncora em 85%, "no cliente" 81%. A GR não tem nenhum local da Martins.

**Próximo: etapa 3** — os três campos pelo motor, atrás de `EMBARQUES_ANCORA`, com o teste massivo da §7.1.
Guarda nova na etapa 3: âncora a mais de 60 km do destino do CTRB é descartada (hoje não descarta nenhuma — protege
de CNPJ errado no futuro).

## 10. O CNPJ bate com o CTRB? (14/10, cargas desde 27/08, SEM a subcontratação da Martins — 489 de 641)

A Martins sai das contas: o CTe dela é fiscal (expedidor e entrega = o CD de Uberlândia, com a carreta indo de
Suzano a Hidrolândia) — nos CTes de um manifesto só, 28 de 70 batem na origem e 12 de 70 no destino.

| conta | bate |
|---|---|
| destino: endereço do CNPJ (recebedor, já com guarda de cidade) na cidade do CTRB | 428 de 431 (99%) |
| CTe de UM manifesto só (primeiro = último = este): entrega = destino do CTRB | 153 de 157 (97%) |
| CTe de UM manifesto só: expedidor = origem do CTRB | 144 de 157 (92%) |
| origem pelo CNPJ de hoje (remetente da ordem de coleta, SEM guarda de cidade) | 375 de 435 (86%) |

CTe com mais de um manifesto é perna intermediária (transbordo, hub): ali nem expedidor nem remetente é o ponto
físico desta perna. **Regra da origem para a fase 2 (Gabriel, 14/10):** CNPJ de origem só do EXPEDIDOR de CTe com um
manifesto só e com a cidade conferida; o remetente da coleta passa pela mesma guarda; o ponto de carregamento é
aprendido pelo GPS (o único que acerta também nas pernas de hub e na Martins).
