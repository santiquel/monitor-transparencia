"""Pontuação: transforma os vereditos dos critérios em notas explicáveis.

Metodologia (docs/00_plano_e_decisoes.md §5):

    nota = Σ(peso × valor) / Σ(peso)      sobre os critérios APLICÁVEIS e AVALIADOS

- valor: ATENDE = 1, ATENDE_PARCIALMENTE = 0,5, NAO_ATENDE = 0.
- peso: classificação legal, mesmos valores do PNTP (essencial 2, obrigatório 1,5,
  recomendado 1).
- NAO_APLICAVEL sai do denominador: não se cobra o que a lei não exige daquele
  tipo de entidade.
- NAO_AVALIADO também fica fora da nota, mas NÃO fica escondido:
    * `valor_min`: e se todos os não avaliados fossem "não atende"?
    * `valor_max`: e se todos fossem "atende"?
    * `cobertura`: % dos critérios aplicáveis que o sistema conseguiu avaliar.
  Uma nota 80 com cobertura 50% e faixa 40–90 diz algo bem diferente de uma
  nota 80 com cobertura 100%.
- A confiança NÃO multiplica a nota (decisão 6): ela orienta a revisão humana.

Níveis inspirados no PNTP (Atricon), com a trava de essenciais: sem 100% dos
critérios essenciais atendidos, a entidade não alcança Diamante, Ouro ou Prata.
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from transparencia.db.models import Criterion, StatusConformidade
from transparencia.evaluation.veredito import Veredito

METODOLOGIA_VERSAO = "1.0"

# (nota mínima, nível, exige 100% dos essenciais?) — do mais alto para o mais baixo
NIVEIS = [
    (Decimal("95"), "Diamante", True),
    (Decimal("85"), "Ouro", True),
    (Decimal("75"), "Prata", True),
    (Decimal("75"), "Elevado", False),
    (Decimal("50"), "Intermediário", False),
    (Decimal("30"), "Básico", False),
    (Decimal("0.01"), "Inicial", False),
    (Decimal("0"), "Inexistente", False),
]

CEM = Decimal("100")


def _pct(x: Decimal) -> Decimal:
    return (x * CEM).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class Nota:
    escopo: str  # 'geral' | 'categoria'
    categoria_codigo: str | None
    valor: Decimal | None
    valor_min: Decimal | None
    valor_max: Decimal | None
    cobertura: Decimal | None
    nivel: str | None = None
    essenciais_ok: bool | None = None
    detalhes: dict = field(default_factory=dict)


def calcular_nota(
    itens: list[tuple[Criterion, Veredito]], escopo: str = "geral", categoria: str | None = None
) -> Nota:
    aplicaveis = [(c, v) for c, v in itens if v.status != StatusConformidade.NAO_APLICAVEL]
    avaliados = [(c, v) for c, v in aplicaveis if v.valor is not None]
    nao_avaliados = [(c, v) for c, v in aplicaveis if v.valor is None]

    if not aplicaveis:
        return Nota(
            escopo, categoria, None, None, None, None, detalhes={"motivo": "nada aplicável"}
        )

    peso_total = sum(Decimal(c.peso) for c, _ in aplicaveis)
    peso_avaliado = sum(Decimal(c.peso) for c, _ in avaliados)
    peso_nao_avaliado = peso_total - peso_avaliado
    pontos = sum(Decimal(c.peso) * v.valor for c, v in avaliados)

    valor = _pct(pontos / peso_avaliado) if peso_avaliado else None
    valor_min = _pct(pontos / peso_total)
    valor_max = _pct((pontos + peso_nao_avaliado) / peso_total)
    cobertura = _pct(Decimal(len(avaliados)) / Decimal(len(aplicaveis)))

    # Sensibilidade: a mesma nota com média simples (todos os critérios valendo igual).
    media_simples = (
        _pct(sum(v.valor for _, v in avaliados) / Decimal(len(avaliados))) if avaliados else None
    )

    detalhes = {
        "aplicaveis": len(aplicaveis),
        "avaliados": len(avaliados),
        "nao_avaliados": [c.codigo for c, _ in nao_avaliados],
        "cobertura_ponderada": str(_pct(peso_avaliado / peso_total)),
        "media_simples": str(media_simples) if media_simples is not None else None,
    }
    nota = Nota(escopo, categoria, valor, valor_min, valor_max, cobertura, detalhes=detalhes)

    if escopo == "geral":
        essenciais = [(c, v) for c, v in aplicaveis if c.classificacao == "essencial"]
        if any(v.status == StatusConformidade.NAO_AVALIADO for _, v in essenciais):
            nota.essenciais_ok = None  # indeterminado: algum essencial não foi avaliado
        else:
            nota.essenciais_ok = all(v.status == StatusConformidade.ATENDE for _, v in essenciais)
        nota.detalhes["essenciais"] = {c.codigo: v.status.value for c, v in essenciais}
        nota.nivel = nivel(valor, nota.essenciais_ok)
    return nota


def nivel(valor: Decimal | None, essenciais_ok: bool | None) -> str | None:
    if valor is None:
        return None
    for minimo, nome, exige_essenciais in NIVEIS:
        if valor >= minimo and (not exige_essenciais or essenciais_ok is True):
            return nome
    return "Inexistente"


def pontuar(itens: list[tuple[Criterion, Veredito]]) -> list[Nota]:
    """Uma nota geral + uma por categoria do checklist."""
    notas = [calcular_nota(itens)]
    for categoria in sorted({c.categoria_codigo for c, _ in itens}, key=int):
        da_categoria = [(c, v) for c, v in itens if c.categoria_codigo == categoria]
        notas.append(calcular_nota(da_categoria, "categoria", categoria))
    return notas
