"""Testes da IA com um cliente falso: sem rede, sem custo, determinísticos.

O que se testa é o que depende deste código: seleção de trechos, verificação da
citação (anti-alucinação), cache e custo. A qualidade do modelo em si se mede
contra o golden set, não em teste unitário.
"""

from types import SimpleNamespace

import pytest

from tests.test_avaliadores import PORTAL, TEXTO, Montador
from transparencia.db.models import MetodoAvaliacao
from transparencia.db.models import StatusConformidade as S
from transparencia.evaluation.ia import JuizIA, RespostaIA, citacao_confere
from transparencia.evaluation.regras import RegraCriterio
from transparencia.evaluation.veredito import Veredito, nao_avaliado

REGRA = RegraCriterio(
    rubrica={"ATENDE": "identifica o encarregado e o contato", "NAO_ATENDE": "não identifica"},
    onde_olhar="LGPD",
)
PAGINA_LGPD = (
    "<p>Encarregado pelo tratamento de dados pessoais: Maria da Silva, "
    "e-mail lgpd@exemplo.rj.gov.br, telefone (21) 2222-3333.</p>"
)


class ClienteFalso:
    """Imita `anthropic.Anthropic`: só o que o juiz usa (messages.parse)."""

    def __init__(self, resposta: dict):
        self.resposta = resposta
        self.chamadas = 0
        self.messages = self

    def parse(self, **kwargs):
        self.chamadas += 1
        assert kwargs["output_format"] is RespostaIA
        assert "Maria da Silva" in kwargs["messages"][0]["content"]  # o trecho foi enviado
        return SimpleNamespace(
            parsed_output=RespostaIA.model_validate(self.resposta),
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
            stop_reason="end_turn",
        )


@pytest.fixture
def snap(tmp_path):
    m = Montador(tmp_path)
    return m.pagina(PORTAL + "lgpd", PAGINA_LGPD + TEXTO, texto_link="LGPD").snapshot()


def _juiz(tmp_path, resposta):
    cliente = ClienteFalso(resposta)
    return JuizIA(cliente, "claude-haiku-5-5", tmp_path / "cache", "v-teste"), cliente


def _resposta(citacao, url=PORTAL + "lgpd", status="ATENDE"):
    return {
        "status": status,
        "confianca": 0.95,
        "justificativa": "ok",
        "citacao": citacao,
        "url": url,
    }


DA_REGRA = Veredito(S.ATENDE_PARCIALMENTE, 0.6, "regra", detalhes={"zona_cinzenta": True})


def test_citacao_verificada_vira_veredito(tmp_path, snap):
    juiz, _ = _juiz(
        tmp_path, _resposta("Encarregado pelo tratamento de dados pessoais: Maria da Silva")
    )
    v = juiz.avaliar("18.1", REGRA, "Encarregado LGPD", snap, DA_REGRA)
    assert v.status == S.ATENDE
    assert v.metodo == MetodoAvaliacao.REGRA_LLM
    assert v.confianca == 0.8  # limitada: a IA nunca declara mais que isso
    assert v.evidencias[0].trecho.startswith("Encarregado")
    assert v.custo_llm_usd == pytest.approx((1000 * 0.10 + 200 * 0.50) / 1_000_000)


def test_citacao_inventada_e_descartada(tmp_path, snap):
    juiz, _ = _juiz(tmp_path, _resposta("O encarregado é João Pereira"))  # não está na página
    v = juiz.avaliar("18.1", REGRA, "Encarregado LGPD", snap, DA_REGRA)
    assert v.status == S.NAO_AVALIADO and v.revisao_humana
    assert "citação não foi encontrada" in v.justificativa


def test_inconclusivo_nao_vira_nao_atende(tmp_path, snap):
    juiz, _ = _juiz(tmp_path, _resposta("Maria da Silva", status="INCONCLUSIVO"))
    assert juiz.avaliar("18.1", REGRA, "x", snap, DA_REGRA).status == S.NAO_AVALIADO


def test_cache_evita_segunda_chamada(tmp_path, snap):
    juiz, cliente = _juiz(tmp_path, _resposta("Maria da Silva, e-mail lgpd@exemplo.rj.gov.br"))
    v1 = juiz.avaliar("18.1", REGRA, "x", snap, DA_REGRA)
    v2 = juiz.avaliar("18.1", REGRA, "x", snap, DA_REGRA)
    assert cliente.chamadas == 1
    assert v1.llm_cache_key == v2.llm_cache_key and v2.custo_llm_usd == 0.0


def test_so_entra_na_zona_cinzenta(tmp_path):
    juiz, _ = _juiz(tmp_path, {})
    assert juiz.precisa("18.1", DA_REGRA)
    assert not juiz.precisa("18.1", Veredito(S.ATENDE, 0.9, "claro"))
    assert not juiz.precisa("1.2", nao_avaliado("x", zona_cinzenta=True))  # critério sem IA


def test_citacao_confere_ignora_espacos_e_caixa(snap):
    pagina = snap.paginas[0]
    assert citacao_confere("encarregado   PELO tratamento", pagina)
    assert not citacao_confere("Maria", pagina)  # curta demais para servir de prova
