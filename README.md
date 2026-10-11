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
uv run pytest                     # testes
```

## Estrutura

```
config/
  criteria_catalog.yaml   catálogo de critérios (gerado a partir da planilha oficial)
  entities.yaml           entidades avaliadas (entrada do sistema)
docs/                     plano, decisões, guias e documentação técnica
migrations/               esquema do banco versionado (Alembic + SQL legível)
src/transparencia/
  catalog/                leitura, validação e aplicabilidade dos critérios
  db/                     modelos (SQLAlchemy), sessão e carga inicial
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

## Status

- [x] Fase 1 — Fundação: catálogo, banco, CI
- [ ] Fase 2 — Coleta
- [ ] Fase 3 — Avaliação (regras + IA) e pontuação
- [ ] Fase 4 — API, dashboard e execução periódica
- [ ] Fase 5 — Documentação técnica final
