-- =============================================================================
-- 0001 — Esquema inicial do Monitor de Transparência
--
-- SQL puro, executado pela migration Alembic 0001 e mantido em arquivo próprio
-- para facilitar a leitura, a revisão e a execução direta no PostgreSQL.
-- Ver docs/02_postgres_explicado.md para entender cada conceito.
--
-- Convenções:
--   * id SERIAL PRIMARY KEY  -> número inteiro gerado automaticamente
--   * TIMESTAMPTZ            -> data/hora COM fuso (guarda em UTC, exibe no fuso local)
--   * JSONB                  -> JSON binário: flexível e indexável
--   * CHECK (...)            -> regra que o próprio banco garante (dado inválido é recusado)
--   * REFERENCES ... ON DELETE CASCADE -> chave estrangeira; apagar o "pai" apaga os "filhos"
-- =============================================================================

-- ---------------------------------------------------------------------------
-- CADASTRO
-- ---------------------------------------------------------------------------
CREATE TABLE entities (
    id           SERIAL PRIMARY KEY,
    slug         VARCHAR(80)  NOT NULL UNIQUE,
    nome         VARCHAR(200) NOT NULL,
    municipio    VARCHAR(120) NOT NULL,
    uf           VARCHAR(2)   NOT NULL,
    codigo_ibge  VARCHAR(7),
    poder        VARCHAR(20)  NOT NULL,
    esfera       VARCHAR(20)  NOT NULL,
    ativo        BOOLEAN      NOT NULL DEFAULT true,
    notas        TEXT,
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);
-- entities: entidades públicas avaliadas (prefeituras, câmaras...).

CREATE TABLE entity_urls (
    id             SERIAL PRIMARY KEY,
    entity_id      INTEGER     NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    tipo           VARCHAR(10) NOT NULL,
    url            TEXT        NOT NULL,
    origem         VARCHAR(12) NOT NULL,
    fornecedor     VARCHAR(80),
    ativo          BOOLEAN     NOT NULL DEFAULT true,
    first_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at   TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (entity_id, tipo, url),
    CONSTRAINT ck_entity_urls_tipo   CHECK (tipo IN ('site', 'portal')),
    CONSTRAINT ck_entity_urls_origem CHECK (origem IN ('cadastro', 'descoberta'))
);
-- entity_urls: histórico de endereços — portais mudam de URL e de fornecedor.

-- ---------------------------------------------------------------------------
-- CATÁLOGO DE CRITÉRIOS (versionado)
-- ---------------------------------------------------------------------------
CREATE TABLE catalog_versions (
    id                   SERIAL PRIMARY KEY,
    versao               VARCHAR(40)  NOT NULL,
    content_hash         VARCHAR(64)  NOT NULL UNIQUE,
    fonte                VARCHAR(200) NOT NULL,
    pesos_classificacao  JSONB        NOT NULL,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE criteria (
    id                  SERIAL PRIMARY KEY,
    catalog_version_id  INTEGER      NOT NULL REFERENCES catalog_versions(id) ON DELETE CASCADE,
    codigo              VARCHAR(10)  NOT NULL,
    categoria_codigo    VARCHAR(5)   NOT NULL,
    categoria_nome      VARCHAR(200) NOT NULL,
    bloco               VARCHAR(40)  NOT NULL,
    matriz              VARCHAR(40)  NOT NULL,
    descricao           TEXT         NOT NULL,
    fundamentacao       TEXT,
    dimensao            VARCHAR(30)  NOT NULL,
    classificacao       VARCHAR(20)  NOT NULL,
    peso                NUMERIC(4,2) NOT NULL,
    aplica_a            VARCHAR(40)[] NOT NULL,
    condicional         BOOLEAN      NOT NULL DEFAULT false,
    nota                TEXT,
    ordem               INTEGER      NOT NULL,
    UNIQUE (catalog_version_id, codigo),
    CONSTRAINT ck_criteria_classificacao
        CHECK (classificacao IN ('essencial', 'obrigatorio', 'recomendado'))
);

-- ---------------------------------------------------------------------------
-- COLETA (o que o robô VIU)
-- ---------------------------------------------------------------------------
CREATE TABLE crawl_runs (
    id                    SERIAL PRIMARY KEY,
    entity_id             INTEGER     NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    url_inicial           TEXT        NOT NULL,
    status                VARCHAR(12) NOT NULL DEFAULT 'PENDENTE',
    started_at            TIMESTAMPTZ,
    finished_at           TIMESTAMPTZ,
    paginas_coletadas     INTEGER     NOT NULL DEFAULT 0,
    paginas_com_erro      INTEGER     NOT NULL DEFAULT 0,
    fornecedor_detectado  VARCHAR(80),
    erro                  TEXT,
    parametros            JSONB,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_crawl_runs_status
        CHECK (status IN ('PENDENTE', 'EXECUTANDO', 'SUCESSO', 'PARCIAL', 'FALHA'))
);

CREATE TABLE pages (
    id               SERIAL PRIMARY KEY,
    crawl_run_id     INTEGER      NOT NULL REFERENCES crawl_runs(id) ON DELETE CASCADE,
    url              TEXT         NOT NULL,
    url_final        TEXT,
    url_origem       TEXT,
    texto_link       TEXT,
    profundidade     INTEGER      NOT NULL DEFAULT 0,
    http_status      INTEGER,
    content_type     VARCHAR(120),
    titulo           TEXT,
    modo_render      VARCHAR(10),
    tamanho_bytes    INTEGER,
    content_hash     VARCHAR(64),
    storage_path     TEXT,
    screenshot_path  TEXT,
    fetched_at       TIMESTAMPTZ,
    erro             TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);
-- Índices. O de hash verifica se uma página com o mesmo conteúdo já foi vista,
-- sem varrer a tabela inteira.
CREATE INDEX ix_pages_content_hash ON pages (content_hash);
CREATE INDEX ix_pages_crawl_url    ON pages (crawl_run_id, url);

-- ---------------------------------------------------------------------------
-- AVALIAÇÃO (o que o sistema CONCLUIU)
-- ---------------------------------------------------------------------------
CREATE TABLE evaluation_runs (
    id                  SERIAL PRIMARY KEY,
    entity_id           INTEGER     NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    crawl_run_id        INTEGER     REFERENCES crawl_runs(id) ON DELETE SET NULL,
    catalog_version_id  INTEGER     NOT NULL REFERENCES catalog_versions(id),
    versao_regras       VARCHAR(40) NOT NULL,
    origem              VARCHAR(20) NOT NULL DEFAULT 'manual',
    status              VARCHAR(12) NOT NULL DEFAULT 'PENDENTE',
    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ,
    erro                TEXT,
    custo_llm_usd       NUMERIC(10,5),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_eval_runs_status
        CHECK (status IN ('PENDENTE', 'EXECUTANDO', 'SUCESSO', 'PARCIAL', 'FALHA')),
    CONSTRAINT ck_eval_runs_origem
        CHECK (origem IN ('agendada', 'manual', 'reprocessamento'))
);
CREATE INDEX ix_eval_runs_entity_started ON evaluation_runs (entity_id, started_at);

CREATE TABLE criterion_results (
    id                 SERIAL PRIMARY KEY,
    evaluation_run_id  INTEGER     NOT NULL REFERENCES evaluation_runs(id) ON DELETE CASCADE,
    criterion_id       INTEGER     NOT NULL REFERENCES criteria(id),
    status             VARCHAR(25) NOT NULL,
    valor              NUMERIC(3,2),
    confianca          NUMERIC(4,3),
    metodo             VARCHAR(20) NOT NULL,
    justificativa      TEXT,
    detalhes           JSONB,
    llm_modelo         VARCHAR(60),
    llm_cache_key      VARCHAR(64),
    revisao_humana     BOOLEAN     NOT NULL DEFAULT false,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (evaluation_run_id, criterion_id),
    CONSTRAINT ck_results_status CHECK (status IN
        ('ATENDE', 'ATENDE_PARCIALMENTE', 'NAO_ATENDE', 'NAO_APLICAVEL', 'NAO_AVALIADO')),
    CONSTRAINT ck_results_metodo CHECK (metodo IN
        ('REGRA', 'LLM', 'REGRA_LLM', 'MANUAL', 'APLICABILIDADE')),
    CONSTRAINT ck_results_confianca CHECK (confianca BETWEEN 0 AND 1),
    CONSTRAINT ck_results_valor CHECK (valor IN (0, 0.5, 1))
);

CREATE TABLE evidences (
    id                   SERIAL PRIMARY KEY,
    criterion_result_id  INTEGER     NOT NULL REFERENCES criterion_results(id) ON DELETE CASCADE,
    page_id              INTEGER     REFERENCES pages(id) ON DELETE SET NULL,
    tipo                 VARCHAR(20) NOT NULL,
    descricao            TEXT        NOT NULL,
    url                  TEXT,
    titulo_pagina        TEXT,
    trecho               TEXT,
    seletor              TEXT,
    arquivo_path         TEXT,
    metadados            JSONB,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_evidences_tipo CHECK (tipo IN ('link', 'texto', 'tabela', 'formulario',
        'documento', 'arquivo_dados', 'screenshot', 'metadado', 'elemento'))
);

CREATE TABLE scores (
    id                  SERIAL PRIMARY KEY,
    evaluation_run_id   INTEGER     NOT NULL REFERENCES evaluation_runs(id) ON DELETE CASCADE,
    escopo              VARCHAR(10) NOT NULL,
    categoria_codigo    VARCHAR(5),
    valor               NUMERIC(5,2),
    valor_min           NUMERIC(5,2),
    valor_max           NUMERIC(5,2),
    cobertura           NUMERIC(5,2),
    nivel               VARCHAR(20),
    essenciais_ok       BOOLEAN,
    metodologia_versao  VARCHAR(20) NOT NULL,
    detalhes            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- NULLS NOT DISTINCT (PostgreSQL 15+): impede duas linhas 'geral' (categoria NULL)
    -- na mesma avaliação. Sem isso, NULL nunca é "igual" a NULL e o UNIQUE não pegaria.
    UNIQUE NULLS NOT DISTINCT (evaluation_run_id, escopo, categoria_codigo),
    CONSTRAINT ck_scores_escopo CHECK (escopo IN ('geral', 'categoria')),
    CONSTRAINT ck_scores_valor  CHECK (valor BETWEEN 0 AND 100)
);

-- ---------------------------------------------------------------------------
-- FILA DE TRABALHO
-- ---------------------------------------------------------------------------
CREATE TABLE jobs (
    id              SERIAL PRIMARY KEY,
    tipo            VARCHAR(30) NOT NULL,
    payload         JSONB       NOT NULL,
    status          VARCHAR(12) NOT NULL DEFAULT 'PENDENTE',
    tentativas      INTEGER     NOT NULL DEFAULT 0,
    max_tentativas  INTEGER     NOT NULL DEFAULT 3,
    run_after       TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_at       TIMESTAMPTZ,
    locked_by       VARCHAR(80),
    finished_at     TIMESTAMPTZ,
    ultimo_erro     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_jobs_status
        CHECK (status IN ('PENDENTE', 'EXECUTANDO', 'SUCESSO', 'FALHA', 'CANCELADO'))
);
CREATE INDEX ix_jobs_fila ON jobs (status, run_after);
