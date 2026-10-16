"""Testes da pontuação, com contas feitas à mão nos comentários."""

from decimal import Decimal

from transparencia.db.models import Criterion
from transparencia.db.models import StatusConformidade as S
from transparencia.evaluation.pontuacao import calcular_nota, nivel, pontuar
from transparencia.evaluation.veredito import Veredito

PESOS = {"essencial": Decimal("2"), "obrigatorio": Decimal("1.5"), "recomendado": Decimal("1")}


def _item(codigo, classificacao, status, categoria="1"):
    c = Criterion(
        codigo=codigo,
        classificacao=classificacao,
        peso=PESOS[classificacao],
        categoria_codigo=categoria,
    )
    return c, Veredito(status, 0.9, "teste")


def test_media_ponderada_ignora_nao_aplicavel():
    itens = [
        _item("1.1", "essencial", S.ATENDE),  # 2 × 1
        _item("1.2", "obrigatorio", S.NAO_ATENDE),  # 1,5 × 0
        _item("1.3", "recomendado", S.NAO_APLICAVEL),  # fora da conta
    ]
    nota = calcular_nota(itens)
    assert nota.valor == Decimal("57.14")  # 2 / 3,5
    assert nota.cobertura == Decimal("100.00")
    assert nota.detalhes["media_simples"] == "50.00"  # (1 + 0) / 2: o efeito dos pesos


def test_faixa_e_cobertura_com_nao_avaliado():
    itens = [
        _item("1.1", "essencial", S.ATENDE),
        _item("1.2", "obrigatorio", S.NAO_ATENDE),
        _item("1.3", "recomendado", S.NAO_AVALIADO),
    ]
    nota = calcular_nota(itens)
    assert nota.valor == Decimal("57.14")  # não avaliado fora da nota...
    assert nota.valor_min == Decimal("44.44")  # ...mas na faixa: 2 / 4,5
    assert nota.valor_max == Decimal("66.67")  # (2 + 1) / 4,5
    assert nota.cobertura == Decimal("66.67")  # 2 de 3 aplicáveis
    assert nota.detalhes["nao_avaliados"] == ["1.3"]


def test_parcial_vale_meio():
    nota = calcular_nota([_item("1.1", "obrigatorio", S.ATENDE_PARCIALMENTE)])
    assert nota.valor == Decimal("50.00")


def test_trava_de_essenciais():
    assert nivel(Decimal("96"), essenciais_ok=True) == "Diamante"
    assert nivel(Decimal("96"), essenciais_ok=False) == "Elevado"  # trava
    assert nivel(Decimal("96"), essenciais_ok=None) == "Elevado"  # indeterminado também trava
    assert nivel(Decimal("40"), essenciais_ok=True) == "Básico"
    assert nivel(Decimal("0"), essenciais_ok=False) == "Inexistente"


def test_essencial_nao_avaliado_deixa_trava_indeterminada():
    nota = calcular_nota(
        [
            _item("1.1", "essencial", S.ATENDE),
            _item("4.1", "essencial", S.NAO_AVALIADO),
        ]
    )
    assert nota.essenciais_ok is None


def test_pontuar_gera_geral_e_categorias():
    itens = [_item("1.1", "essencial", S.ATENDE, "1"), _item("4.1", "essencial", S.NAO_ATENDE, "4")]
    notas = pontuar(itens)
    assert [(n.escopo, n.categoria_codigo) for n in notas] == [
        ("geral", None),
        ("categoria", "1"),
        ("categoria", "4"),
    ]
    assert notas[1].valor == Decimal("100.00") and notas[2].valor == Decimal("0.00")
