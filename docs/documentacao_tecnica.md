# Monitor de Transparência — Documentação técnica

**Autoria:** PhD. Raquel S. Nascimento · Outubro de 2026

Este projeto se propõe a avaliar, de forma automática e contínua, se os portais
de transparência de prefeituras do Rio de Janeiro cumprem as exigências legais
de publicação de informações. Para cada prefeitura, a ferramenta visita o site
oficial e o portal, guarda uma cópia do que encontrou, verifica cada exigência
com base em regras explícitas, registra a evidência de cada conclusão, calcula
uma nota e acompanha a evolução ao longo do tempo. O que não pode ser concluído
com segurança é encaminhado a uma pessoa, junto com a evidência necessária
para a decisão.

Ao longo do documento, apresento o racional do desenvolvimento na seguinte
sequência: o problema, a arquitetura, a abordagem, as tecnologias, a
metodologia de avaliação e de cálculo da nota, o tratamento de critérios não
aplicáveis e de erros, a estratégia de atualização, as limitações conhecidas e
as possibilidades de evolução. As instruções de instalação e execução estão no
[README](../README.md).

---

## 1. Problema identificado

A **Lei de Acesso à Informação** (LAI, Lei nº 12.527/2011) obriga os órgãos
públicos a divulgar, sem necessidade de pedido, informações como despesas,
licitações, contratos e a relação de servidores, e a oferecer um canal para
pedidos de informação (o e-SIC). A **Lei de Responsabilidade Fiscal** (LRF, Lei
Complementar nº 101/2000) exige a publicação periódica de relatórios fiscais,
como o Relatório Resumido da Execução Orçamentária (RREO), publicado a cada
dois meses, e o Relatório de Gestão Fiscal (RGF). Essas informações são
publicadas nos **portais de transparência**, os sites em que cada órgão
reúne seus dados públicos.

Verificar o cumprimento dessas exigências é hoje um trabalho manual, guiado por
um checklist de 142 critérios organizados em 21 categorias. Neste documento,
chamo de **entidade** cada órgão público avaliado (nesta prova de conceito,
as prefeituras do Rio de Janeiro, Duque de Caxias, Japeri, Itaboraí e Cachoeiras
de Macacu, além de Niterói e Maricá) e de **critério** cada item do checklist.

O reconhecimento inicial dos portais mostrou por que a automação não é
trivial. As 7 entidades usam 6 fornecedores de sistema diferentes (WordPress,
Webrun, GeoSiap, Supernova, Betha e e-cidade), e 51% das 2.110 páginas
coletadas só puderam ser lidas com um navegador completo, porque seu conteúdo é
montado por **JavaScript** (código que roda no navegador depois que a página
carrega). Além disso, os dados de despesas, servidores e licitações costumam
ficar atrás de formulários de consulta, os portais frequentemente estão em
domínios diferentes do site oficial, e parte dos critérios exige interpretação,
como "o e-SIC exige algo que dificulte o acesso?".

O problema, portanto, é construir uma ferramenta que diga, para cada entidade e
cada critério, se a exigência é cumprida, **mostrando a prova** dessa conclusão,
repetindo a verificação periodicamente e mantendo um custo viável para milhares
de portais. A ferramenta foi desenhada para não acusar sem prova, dado que,
para um órgão de controle, afirmar que um portal descumpre a lei quando ele
cumpre (uma **acusação falsa**) é mais grave do que admitir que não foi
possível verificar.

## 2. Arquitetura proposta

A arquitetura parte de três necessidades. A primeira é a **explicabilidade**:
cada conclusão precisa vir acompanhada da evidência que a sustenta, e essa
evidência precisa poder ser conferida depois, mesmo que o site mude. A segunda
é a **repetição no tempo**: a avaliação deve ser refeita periodicamente sem
apagar as anteriores, para que a evolução de cada portal fique registrada. A
terceira é a **escala**: o desenho precisa funcionar para milhares de portais,
com peças que possam ser multiplicadas de forma independente. Para atender às
três, dividi o sistema em etapas com responsabilidades bem separadas, que se
comunicam por meio do banco de dados.

```
cadastro das entidades ─► fila de tarefas ─► coleta ─► cópias das páginas (snapshots)
                                                              │
checklist + régua de avaliação ─────────────────────► avaliação (sem acessar a internet)
                                                              │
                                     resultados + evidências + notas (histórico completo)
                                                              │
                                  API ─► dashboard ◄─► revisão humana
```

A decisão central foi **separar a coleta da avaliação**. A coleta é a etapa
cara e frágil: depende da rede, do tempo de resposta dos sites, de JavaScript e
de bloqueios. Ela produz **snapshots**, cópias fiéis das páginas visitadas
(HTML, PDFs e capturas de tela), cada uma identificada por uma impressão digital
do seu conteúdo (hash sha256). A avaliação, por sua vez, é barata,
determinística e roda **apenas sobre os snapshots**, sem acessar a internet.
Essa separação traz três vantagens. Primeiro, permite auditoria: qualquer
conclusão pode ser conferida contra exatamente o que a ferramenta viu, e não
contra a versão atual do site, que pode ter mudado. Segundo, permite
reprocessamento: quando uma regra é aperfeiçoada, ela é reaplicada a todas as
coletas já feitas, sem visitar os sites de novo. Terceiro, concentra o custo
na coleta, que é a única etapa que precisa ser dimensionada com cuidado para a
escala.

As demais peças seguem o mesmo princípio de responsabilidade única:

- **Fila de tarefas e worker.** Os pedidos de coleta ficam numa fila no próprio
  banco de dados, e um ou mais *workers* (processos que executam as tarefas em
  segundo plano) os atendem. Vários workers podem trabalhar em paralelo sem que
  dois peguem a mesma tarefa.
- **Histórico sem sobrescrita.** Cada execução da avaliação gera um registro
  novo, com a versão do checklist e das regras utilizadas. Nada é apagado, o que
  permite comparar qualquer momento com qualquer outro.
- **API** (interface de programação). É a porta de acesso aos resultados: outros
  sistemas consultam as entidades, as notas, as evidências e o histórico por
  meio dela.
- **Dashboard.** O painel visual, que consome a API e apresenta os resultados
  para pessoas, incluindo a fila de revisão humana.

## 3. Abordagem utilizada

### 3.1 Escopo: 19 critérios que cobrem todos os tipos de verificação

Optei por implementar 19 dos 142 critérios, escolhidos para que cada **tipo
de verificação** exigido pelo checklist estivesse representado. O objetivo foi
demonstrar, com qualidade medida, que a ferramenta resolve cada tipo de
problema, em vez de cobrir muitos critérios de um mesmo tipo sem avaliação
rigorosa. Os critérios restantes reaproveitam os mesmos mecanismos (busca de
links, leitura de tabelas, identificação de documentos e datas), e sua
implementação passa a ser uma extensão do que já existe.

| Tipo de verificação | Critérios | O que exige da ferramenta |
|---|---|---|
| Existência simples | 1.1, 1.2, 1.3, 2.9 | Encontrar o site, o link para o portal, a busca e o canal de contato |
| Acessibilidade | 16.2, 16.3, 16.4 | Reconhecer recursos como alto contraste, ajuste de fonte e o símbolo de acessibilidade |
| Navegação até o dado | 4.1, 5.1, 7.1 | Chegar às consultas de despesas, servidores e licitações e ler suas tabelas |
| Dados estruturados | 4.5, 18.3 | Identificar exportação em formatos abertos (CSV, planilhas) e regras de uso |
| Documentos | 12.1, 20.1, 19.5 | Localizar relatórios fiscais e a lei orçamentária, com o ano de referência |
| Atualização | 4.6, 20.2 | Comparar datas com os prazos legais (o RREO vence 30 dias após cada bimestre) |
| Interpretação | 15.2, 18.1 | Julgar requisitos do e-SIC e a identificação do responsável pela LGPD |

O critério 19.5 pertence à parte do checklist exclusiva do Poder Executivo e
foi incluído para demonstrar o tratamento de critérios que não valem para todo
tipo de entidade (seção 7).

### 3.2 Checklist versionado a partir da planilha oficial

O checklist é lido diretamente da planilha oficial e convertido num arquivo
estruturado (`config/criteria_catalog.yaml`) por um programa gerador. As
decisões que não estão explícitas na planilha, como quais critérios são
essenciais ou a quais tipos de entidade cada um se aplica, ficam registradas
no próprio gerador, de forma visível e versionada. Para garantir que o arquivo
nunca seja alterado à mão, um teste automatizado gera o checklist novamente a
partir da planilha e compara o resultado, por meio de uma impressão digital do
conteúdo, com o arquivo guardado no repositório. Se os dois forem diferentes,
o teste falha e aponta a divergência.

### 3.3 Régua de avaliação

Para cada um dos 19 critérios, escrevi uma **régua de avaliação**
(`config/evaluation_rules.yaml`): a descrição objetiva do que caracteriza
"atende", "atende parcialmente" e "não atende", e de onde a informação costuma
estar. A régua separa **o que** se avalia (o checklist oficial) de **como** se
avalia (a interpretação operacional de cada critério). A mesma régua é usada
pela ferramenta e pela pessoa que produziu os rótulos manuais de referência
(seção 3.6), de modo que ambas são medidas pelo mesmo padrão.

### 3.4 Coleta focada

Em vez de copiar o site inteiro, a coleta parte do site oficial e do portal e
segue preferencialmente os links ligados aos temas do checklist (despesas,
licitações, e-SIC, relatórios fiscais), dentro de um **limite de páginas** por
entidade e de uma profundidade máxima de navegação. Cada página é buscada
primeiro com uma requisição simples, rápida e barata; se o conteúdo vier vazio,
sinal de que ele depende de JavaScript, a coleta **muda para um navegador
completo**. A ferramenta também clica em menus que não têm links explícitos,
respeita o arquivo robots.txt de cada site (a convenção pela qual um site indica
o que robôs podem visitar), limita a frequência de acessos a cada servidor e
identifica o fornecedor do portal.

### 3.5 Avaliação em etapas

A avaliação de cada critério percorre uma sequência de etapas, em que cada uma
só recebe o que a anterior não conseguiu decidir. Inicialmente, a ferramenta verifica
se o critério se aplica àquele tipo de entidade; se não se aplica, ele recebe o
status "não aplicável" e não segue adiante. Em seguida, uma regra
determinística examina os snapshots e, quando encontra uma resposta clara,
emite a conclusão com a evidência correspondente. Os casos que a regra não
consegue decidir com segurança podem, opcionalmente, ser examinados por um
modelo de linguagem (inteligência artificial), sob controles rígidos (seção
5.3). Por fim, uma etapa de validação descarta qualquer conclusão sem evidência
e encaminha à revisão humana os resultados de confiança baixa. Esse desenho
garante que a parte mais confiável e barata do processo resolva a maioria dos
casos, e que os recursos mais caros ou incertos atuem apenas onde são
necessários.

### 3.6 Medição da qualidade

Para medir a qualidade da ferramenta, construí um conjunto de referência com
95 rótulos manuais (5 municípios × 19 critérios), produzidos com a mesma régua
de avaliação e antes de qualquer comparação com os resultados automáticos. Um
comando da ferramenta compara as conclusões automáticas com esses rótulos e
gera um relatório com as métricas descritas na seção 5.4. As regras foram
refinadas em rodadas sucessivas, sempre medidas contra esse conjunto.

### 3.7 Revisão humana

A revisão humana integra o fluxo da ferramenta. O dashboard reúne, numa **fila
de revisão**, os critérios que a ferramenta não pôde concluir com segurança,
cada um com o motivo e a evidência. O revisor pode registrar o seu veredito e,
quando a informação está numa página que a coleta não encontrou, indicar o
endereço correto. Esse endereço passa a ser usado como ponto de partida nas
coletas seguintes da entidade (seção 9).

## 4. Tecnologias escolhidas

As tecnologias foram escolhidas por maturidade, custo zero de licenciamento e
facilidade de operação, priorizando ter poucas peças.

| Tecnologia | Para quê | Por quê |
|---|---|---|
| Python 3.12 e uv | Linguagem e gerenciamento do ambiente | Ecossistema consolidado para dados e coleta web; o uv reproduz o ambiente com as versões exatas |
| PostgreSQL 16 (via Docker) | Banco de dados, histórico e fila de tarefas | Um único componente cobre armazenamento, consultas históricas e a fila de tarefas |
| SQLAlchemy e Alembic | Acesso ao banco e evolução do esquema | Mudanças no banco versionadas e testadas automaticamente |
| httpx e Playwright | Coleta (requisição simples e navegador completo) | Requisição simples por padrão; navegador apenas quando necessário |
| BeautifulSoup | Leitura das páginas | Tolerante a páginas mal formadas, comuns em portais públicos |
| Pydantic | Validação de dados e configurações | Erros de configuração detectados na entrada; formato das respostas da API garantido |
| FastAPI | API | Gera automaticamente a documentação interativa das consultas |
| Streamlit e Altair | Dashboard e gráficos | Interface simples, escrita na mesma linguagem do restante |
| Ollama ou API Claude (opcional) | Inteligência artificial nos casos de interpretação | Provedor intercambiável; desligada por padrão (seção 5.3) |
| pytest, ruff e GitHub Actions | Testes e qualidade do código | 110 testes automatizados, executados a cada alteração, inclusive contra um banco de dados real |

## 5. Metodologia de avaliação

### 5.1 Os cinco status

Cada critério de cada entidade recebe um de cinco status:

| Status | Significado | Entra na nota? |
|---|---|---|
| Atende | Cumpre a régua de avaliação, com evidência | Sim (vale 1) |
| Atende parcialmente | Cumpre parte do que a régua exige | Sim (vale 0,5) |
| Não atende | Descumprimento comprovado | Sim (vale 0) |
| Não aplicável | O critério não vale para este tipo de entidade | Não |
| Não avaliado | Não foi possível concluir com segurança | Não, mas aparece na faixa de incerteza e na cobertura (seção 6) |

### 5.2 Regras com evidência obrigatória

Cada critério tem uma regra própria, que examina os snapshots em busca de
sinais objetivos: links, posição de elementos na página, cabeçalhos de tabelas,
formatos de exportação, títulos de documentos e datas. Toda conclusão vem
acompanhada de evidência: o endereço da página, o trecho literal em que a
informação aparece, a localização do elemento na página e o arquivo do
snapshot, com a captura de tela quando houve navegador.

As regras seguem três princípios. O primeiro é que **a ausência só é afirmada
após busca suficiente**: "não encontrado" só se torna "não atende" se a coleta
percorreu todos os caminhos relevantes dentro do limite de páginas. O segundo
é que **uma evidência positiva basta, mas uma negativa exige fonte completa**:
encontrar um dado recente prova que o portal está atualizado, mas encontrar
apenas dados antigos não prova atraso, porque o snapshot mostra uma parte do
portal. Por isso, conclusões como "não há exportação" ou "os dados estão
desatualizados" só se tornam "não atende" quando a própria página declara essa
informação; caso contrário, o critério é encaminhado à revisão. O terceiro é
que **página certa com dado invisível não é descumprimento**: quando a página
de consulta existe, mas os dados só aparecem após o preenchimento de um
formulário, o resultado é "não avaliado", em vez de "não atende".

Cada regra também declara a **confiança** que tem no próprio sinal, numa escala
de 0 a 1. Esse valor não é calculado estatisticamente: é atribuído conforme a
natureza do sinal. Um link visível no topo da página recebe 0,8, porque a
visibilidade é inferida da estrutura da página e não da imagem da tela; uma
ausência comprovada recebe 0,6. Resultados com confiança abaixo de 0,7 vão
automaticamente para a fila de revisão. A medição confirma que essa escala é
informativa: a confiança média foi de 0,83 nas conclusões corretas e de 0,63
nas incorretas.

### 5.3 Inteligência artificial opcional

Nos dois critérios de interpretação (15.2 e 18.1), a ferramenta pode recorrer a
um modelo de linguagem quando a regra não consegue decidir. O modelo recebe
apenas trechos selecionados das páginas, nunca a página inteira, e precisa
responder num formato estruturado que inclui uma citação literal do trecho em
que se baseou. A ferramenta confere se essa citação existe no snapshot; se não
existir, a resposta é descartada. Além disso, o modelo pode confirmar que um
critério é atendido, mas uma conclusão de "não atende" vinda dele é sempre
encaminhada à revisão humana.

Avaliei um modelo aberto, executado localmente e sem custo (Qwen 2.5, com 7
bilhões de parâmetros, via Ollama), contra o conjunto de referência. Como ele
não melhorou a concordância com os rótulos manuais, a ferramenta opera por
padrão **somente com regras**. A estrutura permanece pronta para adotar um
modelo mais capaz, desde que uma nova medição justifique o seu uso.

### 5.4 Resultados

Comparada ao conjunto de referência, a versão final da ferramenta apresentou os
seguintes resultados:

| Métrica | Resultado | Interpretação |
|---|---|---|
| Acusações falsas | 0 | Nenhum critério cumprido foi apontado como descumprido |
| Omissões | 0 | Nenhum descumprimento real foi apontado como cumprido |
| Precisão em "atende" | 100% | Toda vez que a ferramenta afirmou que um critério é atendido, estava correta |
| Concordância (kappa de Cohen) | 0,46 | Concordância moderada com os rótulos manuais, já descontada a concordância esperada por acaso |
| Cobertura | 64% | Proporção de casos em que a ferramenta chegou a uma conclusão; os demais foram encaminhados à revisão |
| Divergências sinalizadas | 7 de 9 | A maior parte das divergências já havia sido encaminhada à revisão pela própria ferramenta |

Esses números caracterizam a ferramenta como uma **triagem confiável**. Ela
confirma automaticamente, com precisão total, o que está conforme, e encaminha
à revisão humana, com evidência, os casos em que não há prova suficiente. As 9
divergências restantes são todas casos em que a ferramenta foi mais exigente
que o rótulo manual (atribuiu "atende parcialmente" a um critério rotulado
como "atende"), o que é coerente com a prioridade de não superestimar o
cumprimento.

A revisão humana também se mostrou eficaz na prática. Em Cachoeiras de Macacu,
a página que a coleta havia tomado como "despesas" era, na verdade, a folha de
pagamento. Ao indicar o endereço correto da consulta de despesas orçamentárias,
permiti que a coleta seguinte chegasse à página certa e que o critério 4.1
fosse avaliado sobre ela. A partir dessa página, a coleta encontrou também os
botões de exportação exigidos pelo critério 4.5, que passou a ser avaliado como
"atende". A cobertura dessa entidade subiu de 74% para 84%. Na mesma revisão,
identifiquei que o menu principal de sites feitos na plataforma Wix era
interpretado como menu suspenso; corrigi a regra de forma geral, e a medição
confirmou a melhora sem alterar nenhum outro resultado.

## 6. Metodologia de cálculo da pontuação

A nota de cada entidade foi construída para refletir três ideias. Primeiro,
nem todo critério tem o mesmo peso legal: adotei os pesos da metodologia do
Programa Nacional de Transparência Pública (PNTP), que distingue critérios
essenciais, obrigatórios e recomendados. Segundo, um critério que a ferramenta
não conseguiu avaliar não pode ser contado nem como cumprido nem como
descumprido, sob pena de beneficiar ou prejudicar a entidade por uma limitação da
ferramenta. Terceiro, a nota deve vir acompanhada de uma indicação da sua
precisão: uma nota calculada sobre poucos critérios avaliados é menos
representativa do que uma nota calculada sobre quase todos. Daí a regra:

```
nota = Σ (peso × valor) / Σ (peso)       sobre os critérios aplicáveis e avaliados
```

| Elemento | Regra |
|---|---|
| Valor | Atende = 1 · Atende parcialmente = 0,5 · Não atende = 0 |
| Peso | Essencial = 2 · Obrigatório = 1,5 · Recomendado = 1 (pesos do PNTP) |
| Não aplicável | Fora da conta |
| Não avaliado | Fora da nota, mas visível na **faixa de incerteza**: o valor mínimo (se todos os não avaliados fossem "não atende") e o máximo (se todos fossem "atende") |
| Cobertura | Percentual dos critérios aplicáveis que foram avaliados |
| Níveis | Diamante ≥ 95, Ouro ≥ 85, Prata ≥ 75 (os três exigem todos os essenciais atendidos), Elevado ≥ 75, Intermediário ≥ 50, Básico ≥ 30, Inicial > 0 |

A faixa e a cobertura são o que dá sentido à nota. Na coleta de 16/10/2026,
Duque de Caxias obteve nota 88,7 com cobertura de 53%, e Itaboraí, 88,4 com
cobertura de 84%. As notas são praticamente iguais, mas a faixa de Duque de
Caxias vai de 42 a 95, enquanto a de Itaboraí é bem mais estreita. Por isso, o
dashboard nunca apresenta a nota isoladamente: o gráfico principal mostra a
nota como um ponto sobre a sua faixa de incerteza. A confiança das regras não
altera a nota, apenas orienta a revisão. Quando há vereditos humanos, o
dashboard mostra também a **nota revisada**, calculada pela mesma fórmula,
usando o veredito humano onde ele existir.

## 7. Tratamento de critérios não aplicáveis

Alguns critérios não valem para todo tipo de entidade. A aplicabilidade é
decidida antes de qualquer verificação, a partir do checklist: cada critério
indica a que tipos de entidade se aplica, por poder (Executivo ou Legislativo)
e por esfera (municipal ou estadual). As categorias de instrumentos de gestão
fiscal e de relatórios da LRF, por exemplo, valem apenas para o Poder
Executivo; uma Câmara Municipal não é cobrada pela lei orçamentária nem pelo
RREO. Nesses casos, o critério recebe o status "não aplicável" e sai do cálculo
da nota, de modo que a entidade não é penalizada por uma exigência que não lhe
cabe.

Há ainda os **critérios condicionais**, que dependem de um fato específico da
entidade. Por exemplo, a divulgação de transferências voluntárias recebidas só
é exigida se a entidade recebeu esse tipo de transferência no período. Nenhum
dos 19 critérios desta versão é condicional, mas os critérios condicionais já
estão identificados no checklist, com a condição descrita, e o tratamento
previsto para eles parte do mesmo princípio de não presumir: se o portal
publica a informação ou declara expressamente que ela não existe ("não houve
transferências no período"), o critério é atendido; se o portal é omisso, o
caso vai para a revisão humana, que pode marcá-lo como não aplicável ao
confirmar a inexistência do fato.

## 8. Tratamento de erros

O princípio que orienta o tratamento de erros é que **uma falha técnica nunca
se transforma em descumprimento**. Se a ferramenta não conseguiu ver, ela
registra que não viu, e o critério fica como "não avaliado", com o motivo
explícito.

| Situação | Tratamento |
|---|---|
| Site fora do ar, lento ou com erro | Registrado em cada página; os critérios dependentes ficam "não avaliados" |
| Acesso bloqueado pelo robots.txt | Bloqueio respeitado; critério "não avaliado" com esse motivo. Exceções são configuradas por entidade, com justificativa |
| Página que depende de JavaScript | Nova tentativa com navegador completo |
| Limite de páginas atingido | Ausências passam a "não avaliado" (ausência não comprovada) |
| Arquivo maior que 20 MB | Não baixado; ocorrência registrada |
| Coleta interrompida | Cada página é gravada assim que coletada; o registro mostra até onde a coleta chegou |
| Falha numa regra | Isolada: o critério fica "não avaliado" com o registro do erro, e os demais seguem normalmente |
| Inteligência artificial indisponível ou com resposta inválida | Prevalece a conclusão da regra |
| Tarefa da fila que falhou | Nova tentativa automática após 1, 2 e 4 minutos; depois disso, falha registrada e visível no dashboard |
| Worker interrompido no meio de uma tarefa | A tarefa volta para a fila após 2 horas |

## 9. Estratégia de atualização

A atualização acontece em ciclos. Mensalmente, um comando agendado no sistema
operacional coloca na fila uma coleta seguida de avaliação para cada entidade,
e os workers executam essas tarefas. Fora do ciclo, o revisor pode pedir uma
nova coleta de uma entidade pelo próprio dashboard. Cada execução gera um
registro novo, e o histórico de cada entidade mostra um ponto por coleta, com
os critérios que mudaram de situação entre uma coleta e outra (corrigidos,
melhorados ou que regrediram).

Quando uma regra é aperfeiçoada, a versão das regras é atualizada e todas as
coletas já realizadas são reavaliadas a partir dos snapshots, sem novos acessos
aos sites. Assim, todo o histórico passa a ser medido pela mesma régua. Da
mesma forma, quando o checklist oficial muda, a nova planilha gera uma nova
versão do checklist, e as avaliações antigas permanecem associadas à versão que
utilizaram.

A revisão humana também alimenta a atualização. Quando o revisor indica o
endereço correto de uma informação, esse endereço passa a ser um dos **pontos
de partida** das coletas seguintes da entidade, já associado ao tema do
critério. O veredito humano, por sua vez, continua valendo nas coletas
seguintes enquanto a ferramenta mantiver a mesma conclusão automática para o
critério. Se a conclusão automática mudar, sinal de que algo mudou no portal,
o veredito deixa de valer e o critério volta para a fila de revisão do
dashboard, para uma nova análise.

**Viabilidade para 5.000 portais por mês.** As estimativas abaixo partem das
medições feitas nesta prova de conceito.

| Item | Medido nesta versão | Estimativa para 5.000 portais/mês |
|---|---|---|
| Coleta | Mediana de 2,9 minutos e 65 páginas por entidade | Cerca de 240 horas de processamento por mês; com 10 workers em paralelo, aproximadamente um dia. Cada worker com navegador usa cerca de um núcleo de processador e 1 a 2 GB de memória |
| Avaliação | Mediana de 13 segundos por entidade | Cerca de 18 horas de processamento por mês, paralelizável |
| Armazenamento | Cerca de 48 MB de conteúdo por coleta | Até 240 GB por mês; páginas idênticas entre meses não são gravadas de novo |
| Banco de dados | 19 resultados por avaliação | Com os 142 critérios, cerca de 8,5 milhões de registros por ano, volume confortável para o PostgreSQL |
| Revisão humana | Cerca de 9 itens por entidade no primeiro ciclo | É o principal fator de dimensionamento. A herança dos vereditos reduz os ciclos seguintes ao que mudou, e os extratores por fornecedor (seção 11) reduzem a quantidade de casos encaminhados |

Coleta, avaliação, armazenamento e banco de dados escalam com infraestrutura
modesta e custo baixo. O dimensionamento da operação depende principalmente do
volume de revisão humana, que diminui a cada ciclo e com a evolução descrita
na seção 11.

## 10. Limitações conhecidas

- **Escopo de 19 critérios.** A nota se refere a esses critérios; os demais 123
  estão fora desta versão, de forma declarada.
- **Dados atrás de formulários.** Informações que só aparecem após o
  preenchimento de uma consulta não ficam registradas nos snapshots, o que
  explica a maior parte dos casos encaminhados à revisão.
- **Conjunto de referência restrito.** Os 95 rótulos manuais vêm de 5
  municípios. Medir a generalização para novas entidades exige ampliar esse
  conjunto.
- **Visibilidade inferida da estrutura.** A posição de um link na página (no
  topo, num menu suspenso ou no rodapé) é deduzida da estrutura do código da
  página, e não da imagem da tela; a captura de tela fica disponível como
  evidência para a revisão.
- **Documentos identificados pelo título.** Relatórios fiscais são reconhecidos
  pelo título e pelo ano indicados no portal, e não pela leitura do conteúdo do
  PDF.
- **Revisão sem autenticação.** Nesta versão, o revisor se identifica por um
  campo de texto; em produção, a identificação viria de um login.
- **Identificação de domínios.** Para decidir se uma página pertence à própria
  prefeitura ou a um site externo, a ferramenta usa uma regra que cobre os
  formatos de endereço mais comuns (como `.rj.gov.br` e `.com.br`). Em escala
  nacional, o adequado é usar a Public Suffix List, a lista pública de sufixos
  de domínio mantida pela Mozilla.

## 11. Possibilidades de evolução

1. **Extratores por fornecedor.** A ferramenta já identifica o fornecedor de
   cada portal. Como um mesmo fornecedor atende muitos municípios, um extrator
   específico para cada um (por exemplo, utilizando a interface de dados que a
   Betha mantém por trás do seu portal) permitiria ler diretamente os dados que
   hoje ficam atrás de formulários. Essa é a evolução com maior impacto na
   cobertura e na redução da revisão humana.
2. **Revisão como operação.** Ordenar a fila de revisão pelo peso dos critérios
   (essenciais primeiro), autenticar os revisores e incorporar cada revisão ao
   conjunto de referência, que passa a crescer com o uso.
3. **Ampliação do conjunto de referência.** Rotular uma amostra de novas
   entidades, distribuída por fornecedor, e acompanhar a concordância por
   fornecedor e por critério a cada versão das regras.
4. **Leitura do conteúdo dos documentos**, para identificar o período de
   referência dentro dos PDFs de relatórios fiscais.
5. **Inteligência artificial onde a medição justificar**, com modelos mais
   capazes e mantendo a citação verificada e a revisão humana das conclusões
   negativas.
6. **Os 123 critérios restantes**, reaproveitando os mecanismos já existentes.
7. **Infraestrutura para escala**: múltiplos workers, armazenamento de objetos
   para os snapshots e particionamento das tabelas de resultados.
