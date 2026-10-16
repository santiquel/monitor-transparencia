"""Detecção do fornecedor do portal (GeoSiap, Betha, e-cidade...).

Em escala, o fornecedor é a variável que mais explica a estrutura de um portal:
centenas de municípios usam o mesmo sistema. Saber o fornecedor permite ter
extratores específicos (mais baratos e precisos que heurísticas genéricas).

A detecção é por assinaturas na URL e no HTML de cada página do ramo do portal.
Um portal pode ser "casca" WordPress com os dados num sistema de terceiros
(caso de Itaboraí: WordPress + Supernova). O fornecedor que interessa é o
sistema de dados, então plataformas genéricas só valem se nada mais aparecer.
"""

import re
from collections import Counter

ASSINATURAS: list[tuple[str, re.Pattern[str]]] = [
    ("GeoSiap", re.compile(r"geosiap", re.I)),
    ("Betha", re.compile(r"betha\.cloud|plataforma\.betha", re.I)),
    ("e-cidade", re.compile(r"e-?cidade|dbseller", re.I)),
    ("Supernova", re.compile(r"supernova\.com\.br", re.I)),
    ("IPM Atende.Net", re.compile(r"atende\.net", re.I)),
    ("Fiorilli", re.compile(r"fiorilli", re.I)),
    ("Elotech", re.compile(r"elotech", re.I)),
    ("Webrun (Softwell)", re.compile(r"/webrun/", re.I)),
    ("WordPress", re.compile(r"/wp-content/|/wp-includes/", re.I)),
]
GENERICOS = {"WordPress"}

# Assinaturas costumam estar no <head> e nos primeiros scripts; não precisa ler tudo.
BYTES_ANALISADOS = 200_000


def detectar_fornecedor(url: str, conteudo: bytes | None) -> str | None:
    """Fornecedor de UMA página. A URL pesa mais: é verificada antes do HTML."""
    texto = (conteudo or b"")[:BYTES_ANALISADOS].decode("utf-8", "ignore")
    for amostra in (url, texto):
        for nome, padrao in ASSINATURAS:
            if padrao.search(amostra):
                return nome
    return None


def escolher_fornecedor(contagem: Counter[str]) -> str | None:
    """Fornecedor do portal: o específico mais frequente; o genérico só na falta dele."""
    ordem = [nome for nome, _ in ASSINATURAS]
    especificos = [n for n in contagem if n not in GENERICOS]
    candidatos = especificos or list(contagem)
    if not candidatos:
        return None
    return min(candidatos, key=lambda n: (-contagem[n], ordem.index(n)))
