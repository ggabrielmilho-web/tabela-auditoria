# Handoff — Insignia GR (2ª fonte de rastreamento)

**Estado em 05/10/2026:** coleta escrita e testada contra a API de produção, gravando no banco
LOCAL. **Não está em produção** (sem commit, sem deploy, `INSIGNIA_COLETA` ausente = desligada).
Próximo passo combinado: subir a coleta para acumular histórico; **depois** pensar no motor.

## 1. O que é

A Insignia é a **gerenciadora de risco** das cargas da Rizza. Toda carga com SM (solicitação de
monitoramento) passa por ela: **os terceiros** — que a 3S não vê — e frota/agregado conforme o
valor da carga. Para frota e agregado ela lê o rastreador do **cavalo** (Autotrac); a 3S lê o da
**carreta**: são duas testemunhas independentes do mesmo caminhão.

## 2. A API (o que não está na documentação)

- SOAP `http://sigma.insigniagr.com/insigWebService/IntegraGR.wso`, namespace `http://tempuri.org/`,
  login (usuário + senha + token) no corpo. Doc: `sigma.insigniagr.com/insigWebcliente/documentacao-insignia/`.
- **Unidade de negócios = CNPJ da Rizza Transp `02572512000158`.** Até 02/10 a conta apontava para
  uma base de TESTE (ER0044/ER0021 em tudo); a Insignia trocou para produção em 05/10.
- **Placa com hífen** (`AXT-6E87`); sem hífen não encontra.
- **Horários em Brasília.** Gravamos em UTC (+3 h), como `embarques_posicoes_historico`.
- **14 das 23 operações são de ESCRITA** (cancelar/finalizar viagem, solicitar monitoramento…).
  `insignia.chamar()` recusa tudo que não começa com `Get_`. Não remover.

| consulta | serve? | o que traz |
|---|---|---|
| `Get_ConsultaVeiculoEmViagem` | ✅ | as SMs ABERTAS: placas, motorista, valor, origem/destino com lat/lng, operações, última posição, **última macro** |
| `Get_ConsultaTempoParado` | ✅ | **cada parada**: início, fim, duração, local em TEXTO (sem lat/lng). Até 30 dias por chamada |
| `Get_ConsultaShapeViagem` | ✅ | a ROTA PLANEJADA da SM (sem horário) + pontos obrigatórios (PTOBRIG) |
| `Get_ConsultaPosicaoOdometro` | ✅ | posição, ignição, odômetro (unidade varia por tecnologia — cru) |
| `Get_ConsultaCNPJ` | ✅ | 500 locais da GR com ponto, raio (mediana 800 m) e polígono (25) |
| `Get_ConsultaSM` | ❌ | só SM aberta pela própria API |
| `Get_ConsultaDisponibilidade` / `Get_ConsultaModelo` | ❌ | checagem p/ abrir SM / catálogo de marcas |

**Limites medidos:**
- `dDh_Chegada`/`dDh_Saida` dos pontos da SM vêm **zerados**; NF-e (`DocFiscal`) vazia. O evento que
  existe é a **macro do motorista** (CHEGADA NO CLIENTE, INÍCIO/REINÍCIO DE VIAGEM, PARADA
  PERNOITE/EVENTUAL, ABERTURA DE BAÚ), e a API devolve só a **última** — a sequência sai do polling.
- Só há dado de veículo que **teve SM** (entrega local pequena, como a C-2026-001221, não tem).
- A API só mostra SMs abertas e só alcança 30 dias de paradas → **sem coleta, o dado some**.

## 3. A validação (05/10, lab `rizza_lab_0930`)

Paradas ≥ 1 h da Insignia (cavalo) × carreta na 3S no mesmo intervalo, 14 cavalos em setembro:

| como | concordância |
|---|---|
| **par cavalo × carreta tirado da CARGA da época, horário BRT** | **96% (96/100)** |
| mesmo par, horário lido como UTC | 41% |
| par de hoje para o mês inteiro | ~58% |

O cavalo troca de carreta: **parear sempre pela carga** (a mesma lição da §4.1 do HANDOFF-EMBARQUES).

Cobertura nas cargas desde 01/10: 41 de 102 placas com posição (agregado 23/49, terceiro 11/39,
frota 7/14). SMs abertas em 05/10: 22 (7 terceiro · 9 agregado · 6 frota); posição mediana de 6 min.

## 4. A coleta (`insignia.py` + `insignia_coleta.py`)

Thread do servidor atrás de `INSIGNIA_COLETA` (molde da fita documental: falha nunca derruba o app).

| a cada | o quê | tabela |
|---|---|---|
| **60 s** | SMs abertas (ficha + payload bruto), status, macro, última posição | `insignia_sm`, `insignia_sm_operacoes`, `insignia_sm_eventos`, `insignia_macros`, `insignia_posicoes` |
| **60 s** | posição/odômetro de cavalo E carretas das SMs abertas | `insignia_posicoes`, `insignia_placas` |
| SM nova | rota planejada (pontos + polyline precisão 5, lida por `ors_client.decodificar_polyline`) | `insignia_sm_rota` |
| 60 min/placa | paradas desde a última busca; **30 dias** na 1ª vez | `insignia_paradas` |
| SM some da lista | `encerrada_em` + busca final de paradas da viagem inteira | `insignia_sm`, `insignia_paradas` |
| 1×/dia | cadastro de locais | `insignia_locais` |

**Cadência medida (05/10, 10 min consultando a cada 30 s, 19 placas):** a posição muda a cada
**5 min** no Autotrac (12 placas) e no Onixsat (4), e a cada **2 min** no Omnilink (3). Por isso o ciclo é
de **60 s** (a mesma cadência do worker da 3S): pega todo ponto que o aparelho manda. Com 5 min o
Omnilink perderia ~60% dos pontos — e, sem histórico de posições na API, o ponto perdido não volta.

Placa gravada **como a Insignia manda** (`placa`) + chave Mercosul (`placa_chave`) só para cruzar.

**Testado no banco local (05/10):** 1ª rodada 153 s — 20 SMs, 19 placas rastreadas (22 sem
rastreio na GR, quase todas carretas), **6.533 paradas desde 05/09**, 20 rotas (média 1.529 km),
500 locais, 19 macros. 2ª rodada **3 s**, 1 mudança de status, nada duplicado.

## 5. Subir

```bash
# 1) deploy normal (git pull + build + service update). Nasce DESLIGADA — conferir no container:
CT=$(docker ps -q --filter "name=rizza-auditoria_app"); docker exec $CT grep -c "def chamar" insignia.py
# 2) credenciais + ligar, pela CLI (NUNCA pelo stack do Portainer — §22.10):
docker service update --env-add INSIGNIA_USER=... --env-add INSIGNIA_SENHA=... --env-add INSIGNIA_TOKEN=... \
    --env-add INSIGNIA_CNPJ_UNIDADE=02572512000158 --env-add INSIGNIA_COLETA=true rizza-auditoria_app
# 3) conferir: log "✅ Coleta Insignia LIGADA" e, depois de ~3 min, o resumo
CT=$(docker ps -q --filter "name=rizza-auditoria_app"); docker exec $CT python -X utf8 insignia_coleta.py --resumo
```

Volta: `INSIGNIA_COLETA=false` (as tabelas ficam; nada no app as lê).

## 6. Depois da coleta: o motor (não começado)

- terceiros: carga do robô (hoje fora — `EMBARQUES_AUTO_TIPOS=Frota,Agregado`) casada com a SM pela
  placa + data → saída/chegada/entrega pelas paradas e pela posição da Insignia;
- frota/agregado: 2ª testemunha quando a carreta dorme/congela na 3S (§28.10–28.11);
- macro "CHEGADA NO CLIENTE" como evento declarado para medir a régua "no cliente" (§28.12);
- `insignia_locais` como âncora curada (cruzar com a âncora do laboratório, §28.5).
