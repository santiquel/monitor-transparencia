"""Linha de comando do projeto.

Exemplos (no terminal do VSCode, na raiz do projeto):
    uv run transparencia --help
    uv run transparencia catalogo gerar
    uv run transparencia catalogo resumo
    uv run transparencia seed
"""

from collections import Counter
from pathlib import Path
from typing import Annotated

import typer
import yaml
from rich.console import Console
from rich.table import Table

from transparencia.catalog.builder import construir_catalogo
from transparencia.catalog.loader import carregar_catalogo, carregar_entidades
from transparencia.settings import PROJECT_ROOT, get_settings

app = typer.Typer(help="Monitor de conformidade de portais de transparência.", no_args_is_help=True)
catalogo_app = typer.Typer(help="Catálogo de critérios (derivado do checklist).")
app.add_typer(catalogo_app, name="catalogo")
console = Console()

XLSX_PADRAO = PROJECT_ROOT / "docs" / "referencia" / "checklistPortalTransp.xlsx"

CABECALHO_YAML = """\
# =============================================================================
# CATÁLOGO DE CRITÉRIOS — ARQUIVO GERADO AUTOMATICAMENTE
# Fonte: docs/referencia/checklistPortalTransp.xlsx
# Gerador: src/transparencia/catalog/builder.py
# Comando: uv run transparencia catalogo gerar
#
# Não edite à mão: altere o gerador (decisões ficam versionadas e explicadas lá)
# e rode o comando de novo. Revise a diferença com `git diff`.
# =============================================================================
"""


@catalogo_app.command("gerar")
def catalogo_gerar(
    xlsx: Annotated[Path, typer.Option(help="Planilha de quesitos")] = XLSX_PADRAO,
    saida: Annotated[Path | None, typer.Option(help="YAML de saída")] = None,
) -> None:
    """Converte o checklist .xlsx no catálogo YAML versionado."""
    saida = saida or get_settings().criteria_catalog_path
    catalogo = construir_catalogo(xlsx)
    dados = catalogo.model_dump(mode="json", exclude_none=True)
    texto = yaml.safe_dump(dados, allow_unicode=True, sort_keys=False, width=100)
    saida.write_text(CABECALHO_YAML + texto, encoding="utf-8")
    console.print(
        f"[green]OK[/green] {len(catalogo.criterios)} critérios em "
        f"{len(catalogo.categorias)} categorias -> {saida}"
    )
    for obs in catalogo.observacoes_fonte:
        console.print(f"[yellow]Observação sobre a fonte:[/yellow] {obs}")


@catalogo_app.command("resumo")
def catalogo_resumo() -> None:
    """Mostra uma tabela-resumo do catálogo (por categoria e classificação)."""
    cat = carregar_catalogo(get_settings().criteria_catalog_path)
    tabela = Table(title=f"Catálogo {cat.versao} — hash {cat.content_hash()[:12]}")
    for col in ("Cat.", "Nome", "Matriz", "Crit.", "Ess.", "Obr.", "Rec.", "Aplica a"):
        tabela.add_column(col)
    for c in cat.categorias:
        crits = [x for x in cat.criterios if x.categoria == c.codigo]
        cont = Counter(x.classificacao.value for x in crits)
        aplica = sorted({p for x in crits for p in x.aplica_a})
        tabela.add_row(
            c.codigo,
            c.nome,
            c.matriz.value,
            str(len(crits)),
            str(cont["essencial"]),
            str(cont["obrigatorio"]),
            str(cont["recomendado"]),
            ", ".join(aplica),
        )
    console.print(tabela)
    console.print(f"Total de critérios: {len(cat.criterios)}")


@app.command("seed")
def seed() -> None:
    """Carrega catálogo e entidades (YAML) no PostgreSQL. Pode rodar várias vezes."""
    from transparencia.db.seed import executar_seed
    from transparencia.db.session import session_scope

    s = get_settings()
    catalogo = carregar_catalogo(s.criteria_catalog_path)
    cadastro = carregar_entidades(s.entities_path)
    with session_scope() as session:
        r = executar_seed(session, catalogo, cadastro)
    estado = "nova versão gravada" if r.catalogo_novo else "já existia (sem mudanças)"
    console.print(f"[green]Catálogo[/green] id={r.catalogo_versao_id}: {estado}")
    console.print(
        f"[green]Entidades[/green]: {r.entidades_criadas} criadas, "
        f"{r.entidades_atualizadas} atualizadas"
    )


if __name__ == "__main__":
    app()
