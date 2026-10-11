from transparencia.catalog.loader import carregar_entidades
from transparencia.settings import PROJECT_ROOT


def test_cadastro_de_entidades_valido():
    cadastro = carregar_entidades(PROJECT_ROOT / "config" / "entities.yaml")
    slugs = {e.slug for e in cadastro.entidades}
    obrigatorias = {
        "rio-de-janeiro",
        "duque-de-caxias",
        "japeri",
        "itaborai",
        "cachoeiras-de-macacu",
    }
    assert obrigatorias <= slugs
    assert all(e.tipo == "executivo_municipal" for e in cadastro.entidades)
