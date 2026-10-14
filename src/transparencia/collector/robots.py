"""Política de robots.txt (decisão de projeto: respeitar por padrão).

Segue a RFC 9309, que padronizou o robots.txt:
- 200: vale o que o arquivo diz;
- 4xx (ex.: 404, não existe): tudo liberado;
- 5xx: "inalcançável" -> tudo bloqueado, por prudência;
- falha de rede: o próprio host está fora do ar; não adianta tentar as páginas.

O robots.txt vale por esquema + host + PORTA: `site:8443` e `site:8446` são
servidores diferentes, cada um com o seu arquivo.

A exceção (`ignorar_robots: true` em config/entities.yaml) é por entidade e
fica registrada nos parâmetros da coleta.
"""

from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx


@dataclass
class Decisao:
    permitido: bool
    motivo: str  # explicação legível; vira o erro registrado na página quando bloqueia
    host_inalcancavel: bool = False


@dataclass
class _Regras:
    parser: RobotFileParser | None  # None = sem arquivo útil
    decisao_padrao: Decisao  # usada quando parser é None


class PoliticaRobots:
    def __init__(self, cliente: httpx.Client, user_agent: str, ignorar: bool = False):
        self.cliente = cliente
        self.user_agent = user_agent
        self.ignorar = ignorar
        self._cache: dict[str, _Regras] = {}

    def _regras(self, url: str) -> _Regras:
        partes = urlsplit(url)
        origem = f"{partes.scheme}://{partes.netloc}"  # netloc inclui a porta
        if origem not in self._cache:
            self._cache[origem] = self._baixar(f"{origem}/robots.txt")
        return self._cache[origem]

    def _baixar(self, url_robots: str) -> _Regras:
        try:
            r = self.cliente.get(url_robots)
        except httpx.HTTPError as e:
            motivo = f"sem resposta ao buscar {url_robots}: {type(e).__name__}: {e}"[:300]
            return _Regras(None, Decisao(False, motivo, host_inalcancavel=True))
        if r.status_code >= 500:
            return _Regras(None, Decisao(False, f"{url_robots} respondeu HTTP {r.status_code}"))
        if r.status_code >= 400:
            return _Regras(None, Decisao(True, f"{url_robots} inexistente (HTTP {r.status_code})"))
        parser = RobotFileParser()
        parser.parse(r.text.splitlines())
        return _Regras(parser, Decisao(True, f"regras de {url_robots}"))

    def avaliar(self, url: str) -> Decisao:
        if self.ignorar:
            return Decisao(True, "robots.txt ignorado por exceção cadastrada")
        regras = self._regras(url)
        if regras.parser is None:
            return regras.decisao_padrao
        permitido = regras.parser.can_fetch(self.user_agent, url)
        return Decisao(permitido, regras.decisao_padrao.motivo)

    def atraso_minimo(self, url: str) -> float | None:
        """Crawl-delay pedido pelo site, se houver."""
        regras = self._regras(url)
        if regras.parser is None:
            return None
        atraso = regras.parser.crawl_delay(self.user_agent)
        return float(atraso) if atraso else None
