# Monitor de Transparência

Plataforma para **avaliar automaticamente e de forma contínua** a conformidade
de portais públicos de transparência, usando como referência o checklist de
quesitos da LAI, da LRF e normas correlatas (142 critérios em 21 categorias).

Para cada entidade, a plataforma coleta o portal, avalia cada critério com
**regras determinísticas + IA validada**, registra **evidências auditáveis**,
calcula uma **pontuação ponderada e explicável** e guarda **todo o histórico**
para acompanhar a evolução no tempo.

> Prova de conceito desenvolvida para o Desafio Técnico — Cientista de Dados (MPRJ).
> Municípios: Rio de Janeiro, Duque de Caxias, Japeri, Itaboraí e Cachoeiras de
> Macacu (+ Niterói e Maricá).

## Início rápido

Pré-requisitos: Git, [uv](https://docs.astral.sh/uv/) e Docker. Passo a passo
para Windows: [docs/01_setup_windows.md](docs/01_setup_windows.md).

```bash
cp .env.example .env              # PowerShell: Copy-Item .env.example .env
docker compose up -d              # PostgreSQL 16
uv sync                           # Python 3.12 + dependências
uv run alembic upgrade head       # cria tabelas e views
uv run transparencia seed         # carrega 142 critérios e 7 entidades
uv run playwright install chromium   # navegador para portais em JavaScript (uma vez)
uv run transparencia coletar japeri  # coleta site + portal (ou --todas)
uv run transparencia avaliar japeri  # avalia os 19 critérios sobre a coleta (ou --todas)
uv run transparencia golden medir    # mede contra os rótulos manuais (golden/)
uv run transparencia api            # API em http://localhost:8000/docs
uv run transparencia dashboard      # dashboard em http://localhost:8501
uv run transparencia worker         # processa a fila (coletas pedidas/agendadas)
uv run pytest                     # testes
```

A IA é opcional: sem configurar nada, a avaliação roda só com regras. Para
testar um modelo local e gratuito, veja o passo 11 do guia de setup (Ollama).

## Estrutura

```
config/
  criteria_catalog.yaml   catálogo de critérios (gerado a partir da planilha oficial)
  entities.yaml           entidades avaliadas (entrada do sistema)
  evaluation_rules.yaml   rubrica de cada critério da PoC ("como avaliar")
docs/                     plano, decisões, guias e documentação técnica
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
tests/                    testes (unitários + integração com PostgreSQL no CI)
```

## Documentação

| Documento | Conteúdo |
|---|---|
| [00 — Plano e decisões](docs/00_plano_e_decisoes.md) | Arquitetura, critérios da PoC, uso de IA, pontuação, cronograma |
| [01 — Setup no Windows](docs/01_setup_windows.md) | Do zero ao banco rodando |
| [02 — O banco explicado](docs/02_postgres_explicado.md) | Modelo de dados, diagrama, views de histórico, decisões |
| [03 — Reconhecimento dos portais](docs/03_reconhecimento_portais.md) | Heterogeneidade real dos 7 portais e implicações |
| [04 — O coletor](docs/04_coletor.md) | Navegação focada, HTTP × navegador, cliques em menus JS, robots.txt, snapshots |
| [05 — A avaliação](docs/05_avaliacao.md) | Rubrica, cascata regra → IA, pontuação, medição contra o golden set |
| [06 — API, dashboard e revisão](docs/06_api_dashboard.md) | Rotas, dashboard, revisão humana (veredito + URL indicada), fila/worker, agendamento mensal |

## Status

- [x] Fase 1 — Fundação: catálogo, banco, CI
- [x] Fase 2 — Coleta
- [x] Fase 3 — Avaliação (regras + IA), pontuação e medição (kappa 0,43; 0 acusações falsas)
- [x] Fase 4 — API, dashboard, revisão humana, fila/worker e agendamento
- [ ] Fase 5 — Documentação técnica final
