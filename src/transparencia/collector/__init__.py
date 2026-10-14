"""Coletor: visita o site oficial e o portal da transparência e guarda o que viu.

Fluxo de uma coleta (ver `coletor.Coletor`):

1. Sementes: site oficial + portal (se cadastrado). Sem portal cadastrado, o
   coletor procura, no site, links com "transparência" no texto (descoberta).
2. Fila com prioridade: links cujo texto/URL contém o vocabulário dos critérios
   (despesa, licitações, RGF, e-SIC...) são visitados primeiro.
3. Cada página é buscada com HTTP simples; se vier "vazia" (aplicação
   JavaScript), é buscada de novo com navegador headless (Playwright).
4. O conteúdo vai para `data/objetos/` (endereçado pelo sha256) e os metadados
   para a tabela `pages` (ver `execucao.py`).

A coleta NÃO avalia nada: só registra. A avaliação (Fase 3) roda sobre os
snapshots e pode ser refeita sem acessar o site de novo.
"""
