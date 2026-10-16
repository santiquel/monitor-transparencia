"""IA na zona cinzenta (Claude Haiku), com citação verificada.

Quando entra: só depois das regras, e só em critérios de interpretação
(15.2 "o e-SIC exige algo que dificulte o acesso?", 18.1 "identifica o
encarregado?") quando a regra marcou o caso como zona cinzenta.

Como funciona:
1. As regras escolhem TRECHOS relevantes das páginas coletadas (nunca a página
   inteira): menos custo, menos espaço para o modelo se distrair.
2. O modelo recebe a rubrica do critério + os trechos e responde em JSON
   validado: status, confiança, justificativa, citação literal e URL.
3. **Validação anti-alucinação:** a citação precisa existir, literalmente, no
   texto da página indicada. Se não existir, o resultado vira NAO_AVALIADO com
   revisão humana: a IA nunca decide sem prova verificável.
4. **Cache:** a chave é (critério, versão da régua, versão do prompt, modelo,
   trechos). Página igual na coleta seguinte = resposta reaproveitada, custo zero.
5. **Custo:** tokens × preço, somado em evaluation_runs.custo_llm_usd.
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from transparencia.collector.vocabulario import sem_acento
from transparencia.db.models import MetodoAvaliacao, StatusConformidade
from transparencia.evaluation.regras import RegraCriterio
from transparencia.evaluation.sinais import evidencia
from transparencia.evaluation.snapshot import PaginaSnapshot, Snapshot, trecho
from transparencia.evaluation.veredito import Veredito, nao_avaliado

VERSAO_PROMPT = "1"

# US$ por milhão de tokens (entrada, saída). Fonte: tabela de preços da Anthropic.
PRECOS = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}

MAX_TRECHOS = 6
CONFIANCA_MAXIMA_IA = 0.8
TAMANHO_TRECHO = 700  # caracteres de cada lado do termo encontrado (~350 tokens)

SISTEMA = """\
Você é auditor de portais de transparência de prefeituras brasileiras. Avalia UM
critério do checklist da Lei de Acesso à Informação, usando SOMENTE os trechos de
páginas fornecidos. Não use conhecimento externo sobre a prefeitura.

Responda pela rubrica do critério:
- ATENDE / ATENDE_PARCIALMENTE / NAO_ATENDE conforme a rubrica.
- INCONCLUSIVO se os trechos não bastam para decidir (é melhor que adivinhar).

A "citacao" deve ser copiada LITERALMENTE de um dos trechos (mesmas palavras e
acentos, até 300 caracteres) e a "url" deve ser a do trecho de onde ela veio.
Ela será conferida automaticamente; uma citação que não exista no trecho anula
a resposta. Em "justificativa", explique em uma ou duas frases, em português.
"""


class RespostaIA(BaseModel):
    status: Literal["ATENDE", "ATENDE_PARCIALMENTE", "NAO_ATENDE", "INCONCLUSIVO"]
    confianca: float = Field(ge=0, le=1)
    justificativa: str
    citacao: str
    url: str


# ------------------------------------------------------------- trechos

# Termos que marcam os trechos relevantes de cada critério da zona cinzenta.
TERMOS = {
    "15.2": r"pedido|solicita|requerente|cadastr|documento|identifica|cpf|assinatura"
    r"|declara|maior|formulario",
    "18.1": r"encarregad|\bdpo\b|protecao de dados|lgpd|dados pessoais",
}
TEMAS = {"15.2": {"esic"}, "18.1": {"lgpd"}}


def selecionar_trechos(codigo: str, snap: Snapshot) -> list[tuple[PaginaSnapshot, str]]:
    """Trechos relevantes (página, texto), sem repetir e no máximo MAX_TRECHOS."""
    padrao = re.compile(TERMOS[codigo])
    paginas = [p for p in snap.html if p.temas & TEMAS[codigo] or "falabr.cgu.gov.br" in p.url]
    paginas += [p for p in snap.html if p not in paginas and padrao.search(p.texto_busca)]
    escolhidos, vistos = [], set()
    for p in paginas:
        for m in padrao.finditer(p.texto_busca):
            t = trecho(p.texto, m.start(), m.end(), margem=TAMANHO_TRECHO // 2)
            if t in vistos or any(t in v or v in t for v in vistos):
                continue
            vistos.add(t)
            escolhidos.append((p, t))
            if len(escolhidos) >= MAX_TRECHOS:
                return escolhidos
            break  # um trecho por página primeiro: diversidade de fontes
    return escolhidos


# ---------------------------------------------------------- validação


def _normalizar(texto: str) -> str:
    return " ".join(sem_acento(texto).split())


def citacao_confere(citacao: str, pagina: PaginaSnapshot) -> bool:
    """A citação existe literalmente no texto da página? (ignora espaços e caixa)."""
    c = _normalizar(citacao.strip(" .\"'…"))
    return len(c) >= 10 and c in _normalizar(pagina.texto)


# --------------------------------------------------------------- juiz


class JuizIA:
    """Chama o modelo, com cache em disco e cálculo de custo."""

    criterios = frozenset(TERMOS)

    def __init__(self, cliente, modelo: str, pasta_cache: Path, versao_regras: str):
        self.cliente = cliente  # anthropic.Anthropic (ou um falso, nos testes)
        self.modelo = modelo
        self.pasta_cache = pasta_cache
        self.versao_regras = versao_regras

    def precisa(self, codigo: str, veredito: Veredito) -> bool:
        return codigo in self.criterios and bool(veredito.detalhes.get("zona_cinzenta"))

    def _chave(self, codigo: str, trechos: list[tuple[PaginaSnapshot, str]]) -> str:
        base = json.dumps(
            [
                codigo,
                self.versao_regras,
                VERSAO_PROMPT,
                self.modelo,
                [(p.url, t) for p, t in trechos],
            ],
            ensure_ascii=False,
        )
        return hashlib.sha256(base.encode()).hexdigest()

    def _perguntar(
        self, codigo: str, regra: RegraCriterio, descricao: str, trechos
    ) -> tuple[dict, float]:
        rubrica = "\n".join(f"- {s}: {t}" for s, t in regra.rubrica.items())
        blocos = "\n\n".join(
            f'<trecho n="{i}" url="{p.url}">\n{t}\n</trecho>' for i, (p, t) in enumerate(trechos, 1)
        )
        pergunta = (
            f"Critério {codigo}: {descricao}\n\nRubrica:\n{rubrica}\n\n"
            f"Trechos das páginas coletadas:\n\n{blocos}"
        )
        resposta = self.cliente.messages.parse(
            model=self.modelo,
            max_tokens=2000,
            system=SISTEMA,
            messages=[{"role": "user", "content": pergunta}],
            output_format=RespostaIA,
        )
        entrada, saida = PRECOS.get(self.modelo, (0.0, 0.0))
        uso = resposta.usage
        custo = (uso.input_tokens * entrada + uso.output_tokens * saida) / 1_000_000
        if resposta.stop_reason == "refusal" or resposta.parsed_output is None:
            raise RuntimeError(f"modelo não respondeu no formato esperado ({resposta.stop_reason})")
        return resposta.parsed_output.model_dump(), custo

    def avaliar(
        self, codigo: str, regra: RegraCriterio, descricao: str, snap: Snapshot, da_regra: Veredito
    ) -> Veredito:
        trechos = selecionar_trechos(codigo, snap)
        if not trechos:
            return da_regra
        chave = self._chave(codigo, trechos)
        arquivo = self.pasta_cache / f"{chave}.json"
        custo = 0.0
        if arquivo.exists():
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
        else:
            try:
                dados, custo = self._perguntar(codigo, regra, descricao, trechos)
            except Exception as e:  # falha da API não derruba a avaliação: fica a regra
                da_regra.detalhes["erro_ia"] = f"{type(e).__name__}: {e}"[:300]
                return da_regra
            arquivo.parent.mkdir(parents=True, exist_ok=True)
            arquivo.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")

        r = RespostaIA.model_validate(dados)
        comum = {
            "llm_modelo": self.modelo,
            "llm_cache_key": chave,
            "custo_llm_usd": custo,
            "metodo": MetodoAvaliacao.REGRA_LLM,
        }
        detalhes = {
            "regra": {"status": da_regra.status.value, "justificativa": da_regra.justificativa},
            "ia": dados,
            "trechos_enviados": len(trechos),
            "cache": custo == 0.0,
        }
        pagina = next((p for p, _ in trechos if p.url == r.url), None)

        if r.status == "INCONCLUSIVO":
            v = nao_avaliado(f"IA inconclusiva: {r.justificativa}", da_regra.evidencias, **detalhes)
        elif pagina is None or not citacao_confere(r.citacao, pagina):
            v = nao_avaliado(
                "Resposta da IA descartada: a citação não foi encontrada na página indicada "
                f"({r.url}). Requer revisão humana.",
                da_regra.evidencias,
                **detalhes,
            )
        else:
            v = Veredito(
                StatusConformidade(r.status),
                # A confiança declarada pelo modelo é limitada: a IA nunca supera uma regra
                # determinística bem calibrada, e casos de IA ficam visíveis na revisão.
                min(r.confianca, CONFIANCA_MAXIMA_IA),
                f"[IA] {r.justificativa}",
                [evidencia(pagina, "texto", "Citação verificada no snapshot", trecho=r.citacao)],
                detalhes=detalhes,
            )
        for k, valor in comum.items():
            setattr(v, k, valor)
        return v
