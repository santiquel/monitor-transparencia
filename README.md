# Monitor de Transparência

Plataforma para **avaliar automaticamente e de forma contínua** a conformidade
de portais públicos de transparência, usando como referência o checklist de
quesitos da LAI, da LRF e normas correlatas (142 critérios em 21 categorias).

Para cada entidade, a plataforma coleta o portal, avalia cada critério com
**regras determinísticas** (IA opcional, ligada só se a medição justificar),
registra **evidências auditáveis**, calcula uma **pontuação ponderada e
explicável** (com faixa de incerteza e cobertura), encaminha à **revisão
humana** o que não pode concluir com segurança e guarda **todo o histórico**.

> Prova de conceito desenvolvida para o Desafio Técnico — Cientista de Dados (MPRJ).
> Municípios: Rio de Janeiro, Duque de Caxias, Japeri, Itaboraí e Cachoeiras de
> Macacu (+ Niterói e Maricá).

**Documentação técnica:** [docs/documentacao_tecnica.md](docs/documentacao_tecnica.md)
— problema, arquitetura, abordagem, tecnologias, metodologia de avaliação e de
pontuação, critérios não aplicáveis, tratamento de erros, estratégia de
atualização, limitações e evolução.

## Pré-requisitos

- **Git**, **[uv](https://docs.astral.sh/uv/)** (gerencia o Python 3.12 e as
  dependências) e **Docker** (roda o PostgreSQL 16).
- No Windows (PowerShell como administrador), por exemplo:

  ```powershell
  winget install -e --id Git.Git
  winget install -e --id astral-sh.uv
  wsl --install                      # pré-requisito do Docker Desktop; reinicie depois
  winget install -e --id Docker.DockerDesktop
  ```

  Abra o Docker Desktop uma vez e aguarde "Engine running".

## Instalação e primeira execução

Na pasta do projeto (os comandos valem para PowerShell e para bash):

```bash
cp .env.example .env                 # PowerShell: Copy-Item .env.example .env
docker compose up -d                 # PostgreSQL 16 em segundo plano
uv sync                              # Python 3.12 + dependências (versões fixas em uv.lock)
uv run alembic upgrade head          # cria tabelas e views
uv run transparencia seed            # carrega os 142 critérios e as 7 entidades
uv run playwright install chromium   # navegador para portais em JavaScript (uma vez)
```

Como conferir:

- `docker compose ps` mostra `transparencia-db` como `Up (healthy)`.
- `uv run alembic current` mostra a versão mais recente (`head`).
- `uv run transparencia seed` informa 142 critérios e 7 entidades; rodar de novo
  não duplica nada.
- Porta 5432 ocupada? No `.env`, use `POSTGRES_PORT=5433` e ajuste a porta na
  `DATABASE_URL`.

## Coletar e avaliar

```bash
uv run transparencia coletar japeri     # visita site e portal, guarda os snapshots (ou --todas)
uv run transparencia avaliar japeri     # avalia os 19 critérios sobre a coleta (ou --todas)
```

A coleta acessa os sites (2 a 3 minutos por entidade, com limite de taxa e
respeito ao robots.txt) e guarda HTML, PDFs e screenshots em `data/`. A
avaliação **não acessa a internet**: trabalha só sobre os snapshots e pode ser
refeita a qualquer momento. `transparencia reprocessar` reavalia coletas antigas
com as regras atuais.

## API, dashboard e worker

São três processos, cada um em um terminal:

```bash
uv run transparencia api          # API em http://localhost:8000 (documentação interativa em /docs)
uv run transparencia dashboard    # dashboard em http://localhost:8501 (consome a API)
uv run transparencia worker       # executa os pedidos de coleta da fila
```

No dashboard: visão geral (nota com faixa de incerteza e cobertura), página de
cada entidade (critérios com evidências, histórico, categorias), **fila de
revisão humana** e metodologia. Na revisão, a pessoa registra o veredito e/ou a
URL correta do critério; o botão "Recoletar e reavaliar" põe um pedido na fila,
que o worker executa usando a URL indicada como ponto de partida.

## Agendamento mensal

`transparencia agendar --todas` põe na fila uma coleta + avaliação por entidade.
Basta o agendador do sistema chamá-lo, com um worker rodando (ou
`worker --uma-vez` logo depois).

Windows (Agendador de Tarefas), dia 1 de cada mês às 2h:

```powershell
schtasks /Create /TN "MonitorTransparencia" /SC MONTHLY /D 1 /ST 02:00 /TR "cmd /c cd /d \"C:\caminho\monitor-transparencia\" && uv run transparencia agendar --todas && uv run transparencia worker --uma-vez"
```

Linux (cron):

```
0 2 1 * *  cd /srv/monitor-transparencia && uv run transparencia agendar --todas
```

## IA opcional

O padrão é `LLM_PROVEDOR=nenhum`: medida contra os rótulos manuais, a IA local
não melhorou os resultados (documentação técnica, seção 5.3). Para reproduzir a
medição com um modelo aberto, local e gratuito ([Ollama](https://ollama.com),
~8 GB de RAM livres, sem placa de vídeo):

```powershell
winget install -e --id Ollama.Ollama
ollama pull qwen2.5:7b
```

e, no `.env`: `LLM_PROVEDOR=ollama` e `LLM_MODEL=qwen2.5:7b`. Também é possível
usar o Claude (`LLM_PROVEDOR=anthropic`, chave em `ANTHROPIC_API_KEY`).

## Qualidade: rótulos manuais e medição

`golden/golden_set.xlsx` tem 95 rótulos manuais (5 municípios × 19 critérios),
feitos com a mesma rubrica da máquina (`config/evaluation_rules.yaml`).

```bash
uv run transparencia golden medir --nome minha-medicao
```

compara a avaliação mais recente de cada entidade com os rótulos e grava o
relatório (cobertura, kappa, precisão/recall, acusações falsas, divergências)
em `golden/medicoes/`. Os relatórios das rodadas já feitas estão lá.

## Testes

```bash
uv run pytest                        # testes unitários (sem rede, sem banco)
uv run ruff check . && uv run ruff format --check .
```

Os testes de integração precisam de um banco descartável:

```powershell
docker exec transparencia-db psql -U transparencia -c "CREATE DATABASE transparencia_test"
$env:TEST_DATABASE_URL="postgresql+psycopg://transparencia:transparencia@localhost:5432/transparencia_test"
uv run pytest
```

O CI (`.github/workflows/ci.yml`) roda lint e todos os testes, inclusive contra
um PostgreSQL próprio, a cada push.

## Comandos

| Comando | O que faz |
|---|---|
| `transparencia catalogo gerar` / `resumo` | gera o catálogo a partir da planilha oficial / mostra o resumo |
| `transparencia seed` | carrega catálogo e entidades no banco (idempotente) |
| `transparencia coletar <slug>` / `--todas` | coleta site e portal |
| `transparencia avaliar <slug>` / `--todas` | avalia a coleta mais recente |
| `transparencia reprocessar` | reavalia coletas antigas com as regras atuais |
| `transparencia golden modelo` / `medir` | gera a planilha de rótulos / mede contra ela |
| `transparencia agendar --todas` | põe coletas na fila |
| `transparencia worker` | processa a fila |
| `transparencia api` / `dashboard` | sobe a API / o dashboard |

## Estrutura

```
config/
  criteria_catalog.yaml   catálogo de critérios (gerado a partir da planilha oficial)
  entities.yaml           entidades avaliadas (entrada do sistema)
  evaluation_rules.yaml   rubrica de cada critério da PoC ("como avaliar")
docs/
  documentacao_tecnica.md      documentação técnica
  referencia/                  checklist oficial (planilha de origem)
golden/                   rótulos manuais (gabarito) e relatórios de medição
migrations/               esquema do banco versionado (Alembic + SQL legível)
src/transparencia/
  catalog/                leitura, validação e aplicabilidade dos critérios
  collector/              coleta focada (httpx + Playwright), robots.txt, snapshots
  db/                     modelos (SQLAlchemy), sessão e carga inicial
  evaluation/             avaliadores por regra, IA, pontuação e medição
  api/                    API FastAPI (leitura + revisão + pedidos de coleta)
  dashboard/              dashboard Streamlit (consome a API)
  fila.py                 fila de jobs no PostgreSQL (SKIP LOCKED) e worker
  cli.py                  comandos `transparencia ...`
tests/                    testes (unitários + integração com PostgreSQL)
```
