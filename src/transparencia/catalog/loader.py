"""Leitura e validação dos arquivos YAML de configuração."""

from pathlib import Path

import yaml

from transparencia.catalog.schema import CadastroEntidades, Catalogo


def _ler_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def carregar_catalogo(path: Path) -> Catalogo:
    return Catalogo.model_validate(_ler_yaml(path))


def carregar_entidades(path: Path) -> CadastroEntidades:
    return CadastroEntidades.model_validate(_ler_yaml(path))
