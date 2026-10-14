"""Armazenamento dos snapshots, endereçado pelo conteúdo (sha256).

Cada arquivo é salvo em `data/objetos/<2 primeiros caracteres do hash>/<hash>.<ext>`.
Consequências:
- **Deduplicação automática:** se a página não mudou entre a coleta de janeiro e
  a de fevereiro, o arquivo é o mesmo e é gravado uma vez só. Em 5.000 portais
  por mês, a maior parte do conteúdo se repete: isso corta o custo de disco.
- **Integridade verificável:** recalcular o sha256 do arquivo prova que o
  snapshot não foi alterado depois da coleta (importante para um órgão de controle).
- O subdiretório de 2 caracteres evita pastas com milhões de arquivos.

O mesmo esquema funciona num bucket S3/GCS trocando só esta classe.
"""

import hashlib
import mimetypes
from pathlib import Path

EXTENSOES_POR_TIPO = {
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "application/pdf": ".pdf",
    "text/csv": ".csv",
    "application/json": ".json",
    "text/xml": ".xml",
    "application/xml": ".xml",
    "text/plain": ".txt",
    "image/png": ".png",
}


def extensao_para(content_type: str | None, url: str) -> str:
    if content_type in EXTENSOES_POR_TIPO:
        return EXTENSOES_POR_TIPO[content_type]
    sufixo = Path(url.split("?", 1)[0].split("#", 1)[0]).suffix.lower()
    if sufixo and len(sufixo) <= 6:
        return sufixo
    return mimetypes.guess_extension(content_type or "") or ".bin"


class Armazenamento:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def salvar(self, conteudo: bytes, extensao: str) -> tuple[str, str]:
        """Grava (se ainda não existir) e devolve (sha256, caminho relativo a data/)."""
        sha = hashlib.sha256(conteudo).hexdigest()
        relativo = Path("objetos") / sha[:2] / f"{sha}{extensao}"
        destino = self.data_dir / relativo
        if not destino.exists():
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(conteudo)
        return sha, relativo.as_posix()
