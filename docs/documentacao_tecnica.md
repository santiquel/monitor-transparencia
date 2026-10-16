# Documentação técnica — Monitor de Transparência

Prova de conceito para o Desafio Técnico — Cientista de Dados (MPRJ): avaliação
automática e contínua da conformidade de portais de transparência municipais
com o checklist de 142 critérios da LAI, da LRF e normas correlatas.

Cada seção a seguir resume a decisão
e aponta o documento de detalhe:

| Documento | Conteúdo |
|---|---|
| [00 — Plano e decisões](00_plano_e_decisoes.md) | Arquitetura, critérios da PoC, decisões e cronograma |
| [01 — Setup no Windows](01_setup_windows.md) | Instalação passo a passo |
| [02 — O banco explicado](02_postgres_explicado.md) | Modelo de dados, views de histórico |
| [03 — Reconhecimento dos portais](03_reconhecimento_portais.md) | Heterogeneidade real dos 7 portais |
| [04 — O coletor](04_coletor.md) | Navegação focada, HTTP × navegador, robots.txt, snapshots |
| [05 — A avaliação](05_avaliacao.md) | Rubrica, regras, IA, pontuação, **medição contra o golden set** |
| [06 — API, dashboard e revisão](06_api_dashboard.md) | Rotas, dashboard, revisão humana, fila/worker, agendamento |

Como executar: [README](../README.md) (início rápido) e [01](01_setup_windows.md).

---

## 1. Problema identificado

O problema exige um sistema que diga, para cada portal e cada critério, se a
exigência é cumprida, **com evidência**, **repetidamente no tempo** e **em
escala** (a pergunta central: "funcionaria para 5.000 portais, todo mês, de
forma confiável, explicável e economicamente viável?").

O reconhecimento dos 7 portais ([03](03_reconhecimento_portais.md)) mostrou o
que torna isso difícil:

- **Heterogeneidade técnica.** 6 fornecedores diferentes em 7 entidades
  (WordPress, Webrun/Softwell, GeoSiap, Supernova, Betha, e-cidade). **51% das
  2.110 páginas coletadas só puderam ser lidas com navegador** (JavaScript).
- **O dado raramente está "na página".** Despesas, servidores e licitações
  ficam atrás de consultas (escolher ano, órgão, clicar em "Consultar").
- **Portal em outro domínio** (GeoSiap, Fala.BR, Betha, Supernova), com
  redirecionamentos e URLs que mudam.
- **Parte dos critérios exige interpretação** ("o e-SIC exige algo que
  dificulte o acesso?", "o encarregado da LGPD está identificado?").

Reformulado, o núcleo é uma função:

```
(entidade, critério, retrato do portal numa data)  ->  veredito + evidências + confiança
```

e, sobre ela, **histórico** (repetição no tempo), **agregação** (nota) e
**exposição** (API e dashboard).

O requisito que mais pesa para um órgão de controle é a **assimetria do erro**:
afirmar que um portal descumpre a lei quando cumpre (acusação falsa) é pior do
que admitir que não foi possível verificar. Isso orienta todas as decisões
abaixo.

## 2. Arquitetura proposta

```
config/entities.yaml ─► fila (jobs, PostgreSQL) ─► worker
                                                     │
                                                     ▼
                     Coletor (httpx │ Playwright, robots.txt, limite por host)
                                                     │
                                  snapshots: data/objetos (sha256) + tabela pages
                                                     │
config/criteria_catalog.yaml ─► Motor de avaliação (sem rede, só snapshots)
config/evaluation_rules.yaml     aplicabilidade → regras → [IA opcional] → validação
                                                     │
                                criterion_results + evidences + scores (append-only)
                                                     │
                       API FastAPI (leitura + revisão + pedidos) ─► Dashboard Streamlit
                                                     ▲
                        revisoes (veredito humano, URL indicada) ──┘
```

**Decisão central: coleta separada da avaliação.**

| | Coleta | Avaliação |
|---|---|---|
| Natureza | Cara, lenta, frágil (rede, JavaScript, bloqueios) | Barata, determinística, repetível |
| Produz | Snapshots: HTML, PDF, screenshot, com hash sha256 | Vereditos, evidências, notas |
| Benefício | Auditoria: vê-se exatamente o que o robô viu | Reprocessamento: regra nova roda sobre coleta antiga, sem acessar o site |

Consequências práticas, todas exercitadas na PoC: as regras foram corrigidas 3
vezes (motor 0.1 → 0.4) e reaplicadas às mesmas coletas (`transparencia reprocessar`), e a
medição de qualidade compara versões das regras sobre o **mesmo** retrato do
portal.

Outras decisões estruturais ([00](00_plano_e_decisoes.md), [02](02_postgres_explicado.md)):

- **Histórico append-only.** Nenhuma avaliação é sobrescrita; cada execução é
  um `evaluation_run` novo, com a versão do catálogo e das regras usadas.
- **Fila no próprio PostgreSQL** (`SELECT ... FOR UPDATE SKIP LOCKED`): vários
  workers em paralelo sem Redis/RabbitMQ.
- **O dashboard só fala com a API; a API só fala com o banco.** Cada peça pode
  ir para uma máquina diferente.

## 3. Abordagem utilizada

1. **Escopo deliberado: 19 dos 142 critérios**, escolhidos para cobrir os seis
   níveis de complexidade do checklist (existência, acessibilidade, navegação, dados
   estruturados, documentos, atualização e interpretação semântica). Lista e
   justificativa em [00 §3](00_plano_e_decisoes.md). A nota declara o escopo
   (`criterios_no_escopo = 19` de 142).
2. **Catálogo derivado da planilha oficial**, nunca editado à mão: o
   `criteria_catalog.yaml` é gerado a partir de `checklistPortalTransp.xlsx`, e
   um teste falha se os dois divergirem. Decisões normativas (essenciais,
   aplicabilidade, condicionais) são constantes explícitas no gerador.
3. **Rubrica separada do catálogo.** `evaluation_rules.yaml` define, por
   critério, o que é ATENDE / PARCIAL / NAO_ATENDE e onde olhar. É a **mesma
   régua** usada pela máquina e por quem rotula o gabarito.
4. **Coleta focada, não varredura** ([04](04_coletor.md)): parte do site e do
   portal, segue links pelo vocabulário de cada tema (despesa, licitações,
   e-SIC...), com orçamento de páginas, profundidade e limite de taxa por host;
   tenta HTTP simples e cai para navegador quando a página vem "vazia"; clica em
   menus sem link (portais em JavaScript); detecta o fornecedor.
5. **Avaliação em cascata** ([05](05_avaliacao.md)): aplicabilidade → regra
   determinística (com evidência obrigatória) → IA só na zona cinzenta e só se
   configurada → validação.
6. **Medição antes de afirmar qualidade**: um golden set rotulado à mão e um
   comando que mede a máquina contra ele (seção 5).
7. **Revisão humana como parte do produto**, não como remendo: a fila de
   revisão, o veredito humano e a URL indicada fecham o ciclo (seção 5.4).

## 4. Tecnologias escolhidas

| Tecnologia | Para quê | Por quê |
|---|---|---|
| Python 3.12 + **uv** | Linguagem e ambiente | Ecossistema de dados/scraping; uv reproduz o ambiente exato (`uv.lock`) |
| **PostgreSQL 16** (Docker) | Banco, histórico, fila | JSONB, views com funções de janela (histórico), `SKIP LOCKED` (fila); uma peça só |
| SQLAlchemy 2 + **Alembic** | Modelos e migrations | SQL legível em `migrations/sql/`; teste garante que SQL e modelos não divergem |
| **httpx** + **Playwright** (Chromium) | Coleta | HTTP barato por padrão; navegador real só quando necessário (registrado em `pages.modo_render`) |
| BeautifulSoup + lxml | Leitura do HTML | Robusto a HTML malformado de portais públicos |
| Pydantic | Configuração, rubrica, esquemas da API | Validação na entrada e contrato explícito na saída |
| **FastAPI** + Uvicorn | API | Documentação interativa automática (`/docs`) a partir dos esquemas |
| **Streamlit** + Altair | Dashboard | Interface simples em Python; gráficos com tooltip |
| Ollama / API Claude (opcional) | IA na zona cinzenta | Provedor trocável; **desligada por padrão**, decisão tomada por medição (5.3) |
| pytest, ruff, GitHub Actions | Qualidade | 110 testes (inclusive contra PostgreSQL real no CI), lint e formatação |

## 5. Metodologia de avaliação

### 5.1 Cinco status

| Status | Significado | Entra na nota? |
|---|---|---|
| ATENDE | cumpre a rubrica, com evidência | sim (1) |
| ATENDE_PARCIALMENTE | cumpre em parte, conforme a rubrica | sim (0,5) |
| NAO_ATENDE | descumprimento **comprovado** | sim (0) |
| NAO_APLICAVEL | o critério não vale para este tipo de entidade | não (sai do denominador) |
| NAO_AVALIADO | não foi possível concluir com segurança | não (aparece na faixa e na cobertura) |

### 5.2 Regras determinísticas com evidência obrigatória

Cada critério tem um avaliador (`evaluation/avaliadores.py`). Todo veredito traz
evidências: URL, título da página, **trecho literal**, seletor CSS do elemento,
arquivo do snapshot (e screenshot, quando houve navegador). Um veredito sem
evidência é descartado pelo motor. A confiança é **declarada pela regra**
conforme a força do sinal (ex.: "link visível" = 0,8, porque a visibilidade é
inferida do HTML); abaixo de 0,7, o resultado vai para revisão humana.

Princípios que evitam conclusões falsas ([05 §3](05_avaliacao.md)):

- **Ausência exige busca suficiente.** "Não encontrei" só vira NAO_ATENDE se a
  coleta não parou por orçamento.
- **Evidência positiva basta; negativa exige fonte completa.** Achar um dado
  recente prova atualização; achar só dados antigos não prova atraso, porque o
  snapshot vê parte do portal. "Sem exportação", "desatualizado" e "RREO
  atrasado" só viram NAO_ATENDE com uma fonte que declara o todo.
- **Página certa, dado invisível = NAO_AVALIADO**, nunca NAO_ATENDE (dados
  atrás de formulários).

### 5.3 IA: opcional e decidida por medição

A infraestrutura existe (provedores Ollama local/gratuito e Claude/pago, só na
zona cinzenta de 2 critérios, só com **trechos**, resposta em JSON com citação
literal **verificada no snapshot**, cache, custo registrado, teto de
confiança 0,8). A **política é assimétrica**: a IA pode confirmar
conformidade, mas um NAO_ATENDE vindo dela vai para revisão humana.

Medida no golden set, a IA local (`qwen2.5:7b`) **não melhorou** a
concordância (kappa 0,40 → 0,43, dentro do ruído de 94 casos); das 5 chamadas,
3 foram inconclusivas e 1 acusação falsa foi segurada pela assimetria. Por isso
o padrão é `LLM_PROVEDOR=nenhum`. Usar IA aqui só se justifica com um modelo
maior **e** nova medição; a infraestrutura está pronta para isso.

### 5.4 Medição contra o golden set e revisão humana

`golden/golden_set.xlsx`: 95 rótulos manuais (5 municípios × 19 critérios),
feitos pela mesma rubrica e **comitados antes** de qualquer medição.
`transparencia golden medir` reporta cobertura, acurácia × baseline "sempre
ATENDE", **kappa de Cohen**, precisão/recall por status, acusações falsas,
omissões e calibração ([05 §6](05_avaliacao.md); relatórios em `golden/medicoes/`).

| Rodada | Motor | Cobertura | Acurácia (baseline) | Kappa | Acusações falsas | Omissões |
|---|---|---|---|---|---|---|
| v1 | 0.1 | 68% | 73% (84%) | 0,23 | 3 | 2 |
| v2 | 0.2 | 65% | 82% (92%) | 0,40 | 1 | 0 |
| v3 | 0.3 | 64% | 83% (92%) | 0,43 | 0 | 0 |
| **v4** | **0.4** | **64%** | **85% (92%)** | **0,46** | **0** | **0** |

Leitura honesta dos números:

- **Zero acusações falsas e zero omissões**: o sistema não afirma
  descumprimento sem prova completa, e nenhum descumprimento real passou como
  ATENDE.
- **O preço é a cobertura (64%)**: os descumprimentos reais do gabarito ficam
  em NAO_AVALIADO + revisão. Hoje o sistema é uma **triagem**: confirma
  conformidade (precisão de 100% em ATENDE) e encaminha o resto a uma pessoa,
  com evidência.
- A acurácia abaixo do baseline se explica pela composição: os casos difíceis
  viram abstenção, e entre os decididos sobra quase só ATENDE. O kappa, que
  desconta o acaso, quase dobrou da v1 para a v4.

**Revisão humana** ([06](06_api_dashboard.md)). No dashboard, o revisor
registra o veredito e/ou a **URL correta** do critério. O resultado da máquina
não é alterado; a API mostra a nota da máquina e a **nota revisada**. A URL
indicada vira semente das próximas coletas, já com o tema do critério. Caso
real (Cachoeiras de Macacu): a página tomada como "despesas" era a folha de
pagamento; com a URL indicada, a recoleta destravou o 4.1 (NAO_AVALIADO →
PARCIAL, com a evidência na página certa) e o 4.5 (NAO_AVALIADO → ATENDE), e a
cobertura da entidade foi de 73,7% para 84,2%. A revisão também encontrou um
erro de regra (o menu principal de sites Wix era tomado como submenu), corrigido
de forma geral e confirmado pela medição v4.

## 6. Metodologia de cálculo da pontuação

```
nota = Σ(peso × valor) / Σ(peso)    sobre os critérios aplicáveis E avaliados
```

| Elemento | Regra |
|---|---|
| valor | ATENDE 1 · PARCIAL 0,5 · NAO_ATENDE 0 |
| peso | essencial 2 · obrigatório 1,5 · recomendado 1 (mesmos valores do PNTP/Atricon) |
| NAO_APLICAVEL | fora do denominador (seção 7) |
| NAO_AVALIADO | fora da nota, **mas visível**: `valor_min` (como se fosse NAO_ATENDE), `valor_max` (como se fosse ATENDE) e `cobertura` |
| níveis | Diamante ≥95, Ouro ≥85, Prata ≥75 (os três exigem 100% dos essenciais), Elevado ≥75, Intermediário ≥50, Básico ≥30, Inicial >0 |
| confiança | **não** multiplica a nota; orienta a revisão |
| sensibilidade | a média simples é gravada ao lado, para mostrar o efeito dos pesos |

Por que a faixa: em 16/10, Duque de Caxias teve nota 88,7 com 52,6% de
cobertura, e Itaboraí, 88,4 com 84,2%. Notas quase iguais, firmeza muito
diferente: a faixa de Duque vai de cerca de 42 a 95. Por isso a nota **nunca
aparece sozinha** no dashboard: o gráfico principal mostra a nota como um ponto
sobre a faixa mínimo–máximo. A **nota revisada** usa a mesma fórmula, trocando
o status pelo veredito humano onde houver. Notas por categoria seguem a mesma
regra. Detalhes: [05 §5](05_avaliacao.md).

## 7. Tratamento de critérios não aplicáveis

A aplicabilidade é decidida **antes** de avaliar, pelo catálogo, e registrada
como `NAO_APLICAVEL` (método `APLICABILIDADE`), fora do denominador:

- **Por tipo de entidade (poder e esfera):** cada critério tem `aplica_a`
  (`*` = todos; `executivo:*`; `executivo:municipal`). As categorias 19, 20 e 21
  são da matriz específica do Executivo; 21.1 e 21.3 valem só para Executivo
  Municipal (a própria planilha diz isso). Uma Câmara Municipal não é cobrada
  por LOA nem RREO.
- **Condicionais** (dependem de fatos da entidade): 3.8, 4.8, 4.9, 11.4, 15.6,
  15.7 e a categoria 13 (emendas parlamentares) ficam marcados no catálogo com
  a condição. Onde a lei aceita declaração de inexistência ("não houve obras
  paralisadas"), a declaração atende; a ausência de declaração não pode ser
  presumida como "não se aplica". Nenhum dos 19 critérios da PoC é
  condicional; a marca no catálogo prepara a expansão para os 142.
- **Pelo revisor:** o veredito humano aceita `NAO_APLICAVEL`, para os casos
  que só uma pessoa pode afirmar.

Testes cobrem a regra de aplicabilidade por tipo de entidade
(`tests/test_catalog.py`).

## 8. Tratamento de erros

Princípio: **falha técnica nunca vira NAO_ATENDE.**

| Situação | Tratamento |
|---|---|
| Site fora do ar, timeout, erro HTTP | registrado em `pages.erro`; critério dependente vira NAO_AVALIADO (no 1.1, NAO_ATENDE só se site **e** portal falharem, com confiança 0,6 e revisão) |
| robots.txt bloqueia | respeitado por padrão; NAO_AVALIADO "bloqueado por robots.txt". Exceção por entidade (`ignorar_robots` em `entities.yaml`), com justificativa |
| Página "vazia" (JavaScript) | nova tentativa com navegador; modo registrado |
| Navegador travado | vigia encerra o processo; a coleta segue sem ele |
| Coleta parou por orçamento | ausências viram NAO_AVALIADO ("ausência não comprovada") |
| Arquivo grande demais (>20 MB) | não baixado, registrado |
| Coleta interrompida no meio | cada página é gravada ao ser coletada; o `crawl_run` mostra até onde chegou |
| Avaliador com defeito | exceção capturada por critério: NAO_AVALIADO com o traceback em `detalhes`; os outros critérios seguem |
| IA indisponível ou resposta inválida | fica o veredito da regra, com o erro anotado; nada vai para o cache |
| Citação da IA inexistente no snapshot | resposta descartada → NAO_AVALIADO + revisão |
| Job falhou | nova tentativa com espera crescente (1, 2, 4 min); depois `FALHA`, com o erro visível no dashboard |
| Worker morreu no meio | job `EXECUTANDO` há mais de 2 h volta para a fila |
| Arquivo de evidência pedido pela API | só arquivos registrados e dentro de `data/`; HTML de terceiros servido com CSP `sandbox` |

## 9. Estratégia de atualização

- **Ciclo mensal:** `transparencia agendar --todas` (Agendador de Tarefas do
  Windows ou cron) põe uma coleta + avaliação por entidade na fila; workers
  executam. Comandos prontos em [06](06_api_dashboard.md).
- **Sob demanda:** o revisor pede "Recoletar e reavaliar" no dashboard (mesma
  fila; pedidos duplicados não geram coletas duplicadas).
- **Mudança de regra:** sobe a versão do motor (`VERSAO_MOTOR`), reavalia as
  coletas existentes sem acessar os sites (`transparencia reprocessar`) e mede
  de novo no golden set. O histórico mostra um ponto por coleta e avisa quando
  os pontos usam réguas diferentes (`regua_unica`).
- **Mudança no checklist oficial:** a planilha nova gera um catálogo novo
  (`catalogo gerar`), gravado como nova `catalog_version`; avaliações antigas
  continuam ligadas à versão que usaram.
- **Mudança no portal:** URLs descobertas são registradas com `origem` e datas
  (`entity_urls`); a URL indicada por um revisor passa a ser semente.
- **Veredito humano ao longo do tempo:** herdado pelas coletas seguintes
  enquanto a máquina mantiver a mesma conclusão; se ela mudar (sinal de que o
  portal mudou), o item volta para a fila.

**Viabilidade para 5.000 portais/mês** (estimativas a partir das medições da
PoC; premissas explícitas):

| Item | Medido na PoC | Estimativa para 5.000 portais/mês |
|---|---|---|
| Coleta | mediana 2,9 min por entidade, 65 páginas | ~240 h de worker/mês; com 10 workers em paralelo, ~1 dia. Cada worker com navegador usa ~1 núcleo e 1–2 GB de RAM |
| Avaliação | mediana 13 s | ~18 h de CPU/mês, paralelizável |
| Armazenamento | ~48 MB de conteúdo por coleta (HTML, PDF) | ~240 GB/mês antes da deduplicação por hash (páginas iguais entre meses não são regravadas); em object storage, custo baixo |
| Banco | 19 resultados por avaliação | 142 critérios × 5.000 × 12 ≈ 8,5 milhões de linhas/ano: confortável para PostgreSQL com índices; particionar por mês acima disso |
| IA | desligada (5.3) | se um modelo maior for justificado por medição: ~2 chamadas por portal em 2 critérios, com cache; ordem de poucos dólares/mês pelos preços em `evaluation/ia.py` |
| **Revisão humana** | **61 itens pendentes nas 7 entidades** (~9 por entidade, nos 19 critérios) | **é o gargalo**: ~43 mil itens no primeiro ciclo. A herança do veredito reduz os ciclos seguintes ao que mudou; extratores por fornecedor (seção 11) atacam a causa das abstenções |

Conclusão: coleta, avaliação, armazenamento e banco escalam com hardware modesto
e custo baixo. O fator que decide a viabilidade é o **volume de revisão
humana**, e o caminho para reduzi-lo está na seção 11.

## 10. Limitações conhecidas

- **Escopo de 19 critérios.** A nota vale para eles; os outros 123 estão fora,
  declaradamente.
- **Dados atrás de formulários** não aparecem no snapshot: 4.1, 5.1, 7.1 e 4.6
  ficam muito em NAO_AVALIADO. É a principal causa da cobertura de 64%.
- **NAO_ATENDE quase nunca é afirmado** (consequência deliberada de não acusar
  sem prova): o sistema entrega a fila de revisão com evidências, não o
  veredito final de descumprimento.
- **Golden set pequeno** (94 casos, 8 NAO_ATENDE): intervalos de confiança
  largos; diferenças de poucos pontos entre rodadas são ruído. Regras e gabarito
  vêm dos mesmos 5 municípios: a medida de generalização exige rotular
  entidades novas (Niterói e Maricá são coletadas, mas não rotuladas).
- **"Visível na capa" (1.2) é inferido da estrutura do HTML**, não da tela; o
  screenshot fica como evidência para a revisão.
- **Documentos reconhecidos pelo título**, não pelo conteúdo do PDF.
- **Sem autenticação** nas rotas de escrita da API (revisão e pedidos); o
  revisor é um campo de texto.
- **Herança do veredito pela conclusão da máquina** é um indicador indireto de
  que o portal não mudou.
- **Domínio "próprio" por heurística**; o correto em escala é a Public Suffix
  List.

## 11. Possibilidades de evolução

Em ordem de impacto na pergunta central (confiável, explicável, viável):

1. **Extratores por fornecedor.** É o maior ganho de cobertura e o que mais
   reduz a revisão humana. A detecção de fornecedor já existe e registra quem
   atende cada portal; a Betha tem API por trás do portal, GeoSiap e Supernova
   têm URLs previsíveis, o Webrun de Duque de Caxias segue um padrão próprio.
   Um extrator por fornecedor vale para todos os municípios que o usam. Os 5.000
   portais devem ser medidos primeiro por fornecedor, para priorizar.
2. **Revisão como operação.** Priorizar a fila por peso do critério (essenciais
   primeiro) e por entidade; autenticação e trilha de auditoria do revisor;
   cada revisão vira rótulo novo do golden set, que cresce com o uso.
3. **Medir generalização.** Rotular uma amostra de entidades novas (estratificada
   por fornecedor) e acompanhar o kappa por fornecedor e por critério a cada
   versão das regras.
4. **Ler o conteúdo dos documentos**: período de referência dentro do PDF de
   RREO/RGF/LOA, não só o título.
5. **IA onde a medição justificar**: modelo maior na zona cinzenta, mantendo
   citação verificada e política assimétrica; candidatos naturais são os
   critérios de interpretação dos 123 restantes.
6. **Os 123 critérios restantes**, reaproveitando os sinais já existentes
   (links, tabelas, datas, exportações, documentos).
7. **Escala de infraestrutura**: vários workers (a fila já suporta), object
   storage para `data/`, particionamento de `criterion_results`, Public Suffix
   List, herança de veredito também por hash de conteúdo.
8. **Visual**: brasões das prefeituras no dashboard (símbolos públicos, com
   fonte e licença registradas).
