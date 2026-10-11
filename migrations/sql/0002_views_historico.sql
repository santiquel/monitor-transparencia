-- =============================================================================
-- 0002 — Views de histórico
--
-- Views de leitura sobre o histórico, consultadas como tabelas e sempre
-- atualizadas a partir das tabelas de origem. Respondem às perguntas de
-- evolução no tempo: o que mudou, quando e em que direção.
-- =============================================================================

-- Última avaliação concluída de cada entidade.
-- DISTINCT ON (entity_id) com ORDER BY ... DESC: a linha mais recente de cada entidade.
CREATE VIEW v_ultima_avaliacao AS
SELECT DISTINCT ON (er.entity_id)
       er.entity_id,
       er.id          AS evaluation_run_id,
       er.started_at,
       er.finished_at,
       er.status,
       er.catalog_version_id
  FROM evaluation_runs er
 WHERE er.status IN ('SUCESSO', 'PARCIAL')
 ORDER BY er.entity_id, er.started_at DESC;

-- Série histórica da pontuação geral: "62% -> 68% -> 81%".
CREATE VIEW v_score_historico AS
SELECT e.id          AS entity_id,
       e.slug,
       e.nome,
       er.id         AS evaluation_run_id,
       er.started_at AS avaliado_em,
       s.valor,
       s.valor_min,
       s.valor_max,
       s.cobertura,
       s.nivel,
       s.valor - LAG(s.valor) OVER w AS variacao
  FROM scores s
  JOIN evaluation_runs er ON er.id = s.evaluation_run_id
  JOIN entities e         ON e.id  = er.entity_id
 WHERE s.escopo = 'geral'
   AND er.status IN ('SUCESSO', 'PARCIAL')
WINDOW w AS (PARTITION BY e.id ORDER BY er.started_at);

-- Linha do tempo de cada critério, com o status anterior e o "evento".
-- LAG() traz o status da avaliação anterior na mesma partição
-- (mesma entidade + mesmo critério), ordenada no tempo.
CREATE VIEW v_criterio_historico AS
WITH base AS (
    SELECT er.entity_id,
           c.codigo                 AS criterio_codigo,
           c.categoria_codigo,
           c.descricao,
           er.id                    AS evaluation_run_id,
           er.started_at            AS avaliado_em,
           cr.status,
           cr.confianca,
           LAG(cr.status) OVER (
               PARTITION BY er.entity_id, c.codigo
               ORDER BY er.started_at
           )                        AS status_anterior
      FROM criterion_results cr
      JOIN evaluation_runs er ON er.id = cr.evaluation_run_id
      JOIN criteria c         ON c.id  = cr.criterion_id
     WHERE er.status IN ('SUCESSO', 'PARCIAL')
)
SELECT base.*,
       CASE
         WHEN status_anterior IS NULL                       THEN 'PRIMEIRA_AVALIACAO'
         WHEN status_anterior = status                      THEN 'SEM_MUDANCA'
         WHEN status = 'ATENDE'
              AND status_anterior IN ('NAO_ATENDE', 'ATENDE_PARCIALMENTE') THEN 'CORRIGIDO'
         WHEN status = 'ATENDE_PARCIALMENTE'
              AND status_anterior = 'NAO_ATENDE'            THEN 'MELHOROU'
         WHEN status IN ('NAO_ATENDE', 'ATENDE_PARCIALMENTE')
              AND status_anterior = 'ATENDE'                THEN 'REGREDIU'
         WHEN status = 'NAO_ATENDE'
              AND status_anterior = 'ATENDE_PARCIALMENTE'   THEN 'REGREDIU'
         ELSE 'MUDOU'  -- envolve NAO_AVALIADO / NAO_APLICAVEL
       END AS evento
  FROM base;
