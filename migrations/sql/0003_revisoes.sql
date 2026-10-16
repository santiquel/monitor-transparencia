-- =============================================================================
-- 0003 — Revisão humana e resultado dos jobs
--
-- A avaliação automática encaminha à revisão o que não pode concluir com
-- segurança. Esta tabela guarda o que a pessoa decidiu. Ela NÃO altera o
-- resultado da máquina (criterion_results continua append-only): a revisão
-- fica ao lado, e a API mostra as duas notas (máquina e revisada).
--
-- Duas informações independentes, ambas opcionais (mas pelo menos uma):
-- - status: o veredito humano. Vale para a coleta revisada e é herdado pelas
--   seguintes enquanto a conclusão automática não mudar; se mudar, o item
--   volta à fila de revisão.
-- - url_indicada: a página certa para o critério. É estrutural: vira semente
--   das próximas coletas da entidade, marcada com o tema do critério.
-- =============================================================================

CREATE TABLE revisoes (
    id                   SERIAL PRIMARY KEY,
    entity_id            INTEGER     NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    criterio_codigo      VARCHAR(10) NOT NULL,          -- "4.1": vale entre versões do catálogo
    criterion_result_id  INTEGER     REFERENCES criterion_results(id) ON DELETE SET NULL,
    crawl_run_id         INTEGER     REFERENCES crawl_runs(id) ON DELETE SET NULL,
    status               VARCHAR(25),                   -- veredito humano (NULL = só indicou URL)
    url_indicada         TEXT,
    comentario           TEXT,
    revisor              VARCHAR(80),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_revisoes_status CHECK (
        status IS NULL
        OR status IN ('ATENDE', 'ATENDE_PARCIALMENTE', 'NAO_ATENDE', 'NAO_APLICAVEL')
    ),
    CONSTRAINT ck_revisoes_algo CHECK (status IS NOT NULL OR url_indicada IS NOT NULL)
);
-- "Revisão vigente" = a mais recente por (entidade, critério).
CREATE INDEX ix_revisoes_entidade_criterio ON revisoes (entity_id, criterio_codigo, created_at);

-- O que o job produziu (ex.: {"crawl_run_id": 40, "evaluation_run_id": 51}).
ALTER TABLE jobs ADD COLUMN resultado JSONB;
