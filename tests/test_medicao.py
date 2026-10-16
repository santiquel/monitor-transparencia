"""Testes da medição contra o golden set: métricas conferidas à mão, sem banco."""

import pytest
from openpyxl import Workbook

from transparencia.evaluation.medicao import (
    Previsao,
    Rotulo,
    gerar_relatorio,
    kappa_cohen,
    ler_rotulos,
    medir,
)


def _r(crit, status, ent="japeri"):
    return Rotulo(ent, crit, status)


def _p(crit, status, ent="japeri", conf=0.9, revisar=False):
    return Previsao(ent, crit, status, confianca=conf, revisao_humana=revisar)


def test_kappa_cohen_casos_conhecidos():
    assert kappa_cohen([("A", "A"), ("B", "B")]) == 1.0
    # Concordância observada 0,5 e esperada 0,5 (o acaso): kappa 0
    assert kappa_cohen([("A", "A"), ("A", "B"), ("B", "A"), ("B", "B")]) == 0.0
    # Exemplo clássico: 20 AA, 5 AB, 10 BA, 15 BB -> po=0,7; pe=0,5 -> kappa=0,4
    pares = [("A", "A")] * 20 + [("A", "B")] * 5 + [("B", "A")] * 10 + [("B", "B")] * 15
    assert kappa_cohen(pares) == pytest.approx(0.4)
    assert kappa_cohen([("A", "A")] * 3) is None  # tudo numa classe: indefinido
    assert kappa_cohen([]) is None


def test_metricas_principais():
    rotulos = [
        _r("1.1", "ATENDE"),
        _r("1.2", "ATENDE"),
        _r("1.3", "ATENDE"),
        _r("2.9", "NAO_ATENDE"),
        _r("4.1", "ATENDE"),  # sistema se absteve
        _r("4.5", "NAO_SEI"),  # fora da medição
        _r("5.1", "ATENDE_PARCIALMENTE"),  # sistema não tem resultado
    ]
    previsoes = [
        _p("1.1", "ATENDE", conf=0.9),
        _p("1.2", "ATENDE", conf=0.9),
        _p("1.3", "NAO_ATENDE", conf=0.5, revisar=True),  # acusação falsa, sinalizada
        _p("2.9", "NAO_ATENDE", conf=0.7),
        _p("4.1", "NAO_AVALIADO", conf=0.0),
        _p("4.5", "ATENDE"),
    ]
    m = medir(rotulos, previsoes)

    assert m.n == 6 and m.ignorados_nao_sei == 1
    assert m.decididos == 4 and m.cobertura == pytest.approx(4 / 6)
    assert m.acertos == 3 and m.acuracia == pytest.approx(0.75)
    assert (m.baseline_classe, m.baseline_acuracia) == ("ATENDE", 0.75)
    assert [(p.rotulo.criterio) for p in m.acusacoes_falsas] == ["1.3"]
    assert m.omissoes == []
    assert m.erros_sinalizados == 1 and len(m.divergencias) == 1
    assert {p.rotulo.criterio for p in m.abstencoes} == {"4.1", "5.1"}

    na = m.por_status["NAO_ATENDE"]
    assert na["previstos"] == 2 and na["precisao"] == 0.5 and na["recall"] == 1.0
    at = m.por_status["ATENDE"]
    assert at["precisao"] == 1.0 and at["recall"] == pytest.approx(2 / 3)

    assert m.matriz["ATENDE"]["NAO_AVALIADO"] == 1
    assert m.matriz["ATENDE_PARCIALMENTE"]["NAO_AVALIADO"] == 1  # sem resultado = abstenção
    assert m.confianca_media_acertos == pytest.approx((0.9 + 0.9 + 0.7) / 3)
    assert m.confianca_media_erros == pytest.approx(0.5)
    assert m.por_criterio["1.3"] == {"n": 1, "decididos": 1, "acertos": 0}


def test_entidades_nao_se_misturam():
    rotulos = [_r("1.1", "ATENDE", "japeri"), _r("1.1", "NAO_ATENDE", "itaborai")]
    previsoes = [_p("1.1", "ATENDE", "itaborai"), _p("1.1", "ATENDE", "japeri")]
    m = medir(rotulos, previsoes)
    assert m.acertos == 1 and [p.rotulo.entidade for p in m.omissoes] == ["itaborai"]


def test_ler_rotulos_normaliza_planilha(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "rotulos"
    ws.append(["entidade", "criterio", "status", "url_evidencia", "observacao"])
    ws.append(["japeri", "1.1", " atende ", "https://x", "ok"])
    ws.append(["japeri", "1.2", "NAO_ATENDE", "Portal da Transparência", None])
    ws["D3"].hyperlink = "https://japeri.exemplo/portal"  # link colado: mostra o título
    ws.append([None, None, None, None, None])  # linha vazia no fim
    caminho = tmp_path / "g.xlsx"
    wb.save(caminho)
    assert ler_rotulos(caminho) == [
        Rotulo("japeri", "1.1", "ATENDE", "https://x", "ok"),
        Rotulo("japeri", "1.2", "NAO_ATENDE", "https://japeri.exemplo/portal"),
    ]


def test_relatorio_tem_as_secoes_e_escapa_barras():
    rotulos = [_r("1.1", "ATENDE"), _r("1.2", "ATENDE")]
    previsoes = [_p("1.1", "ATENDE"), _p("1.2", "NAO_ATENDE")]
    previsoes[1].justificativa = "link | sem rótulo"
    texto = gerar_relatorio(medir(rotulos, previsoes), "teste", {}, {}, "v1")
    for secao in ("## Resumo", "## Matriz de confusão", "## Por critério", "## Divergências (1)"):
        assert secao in texto
    assert "link \\| sem rótulo" in texto  # "|" quebraria a tabela Markdown
