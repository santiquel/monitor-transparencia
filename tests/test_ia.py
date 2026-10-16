"""Testes da IA com um cliente falso: sem rede, sem custo, determinísticos.

O que se testa é o que depende deste código: seleção de trechos, verificação da
citação (anti-alucinação), cache e custo. A qualidade do modelo em si se mede
contra o golden set, não em teste unitário.
"""

import json
from types import SimpleNamespace

import httpx
import pytest

from tests.test_avaliadores import PORTAL, TEXTO, Montador
from transparencia.db.models import MetodoAvaliacao
from transparencia.db.models import StatusConformidade as S
from transparencia.evaluation.ia import (
    JuizIA,
    ProvedorAnthropic,
    ProvedorOllama,
    RespostaIA,
    citacao_confere,
)
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
    juiz = JuizIA(ProvedorAnthropic(cliente), "claude-haiku-5-5", tmp_path / "cache", "v-teste")
    return juiz, cliente


def _resposta(citacao, trecho=1, status="ATENDE"):
    return {
        "status": status,
        "confianca": 0.95,
        "justificativa": "ok",
        "citacao": citacao,
        "trecho": trecho,
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


def test_trecho_inexistente_e_descartado(tmp_path, snap):
    juiz, _ = _juiz(tmp_path, _resposta("Encarregado pelo tratamento", trecho=7))  # só há 1
    v = juiz.avaliar("18.1", REGRA, "x", snap, DA_REGRA)
    assert v.status == S.NAO_AVALIADO and "n=7" in v.justificativa


def test_nao_atende_da_ia_vira_revisao_humana(tmp_path, snap):
    """Política assimétrica: a IA não acusa sozinha, mesmo com citação válida."""
    juiz, _ = _juiz(
        tmp_path, _resposta("Encarregado pelo tratamento de dados pessoais", status="NAO_ATENDE")
    )
    v = juiz.avaliar("18.1", REGRA, "x", snap, DA_REGRA)
    assert v.status == S.NAO_AVALIADO and v.revisao_humana
    assert "Acusação exige revisão humana" in v.justificativa
    assert v.evidencias[0].trecho.startswith("Encarregado")  # a citação fica para o revisor
    assert v.detalhes["ia"]["status"] == "NAO_ATENDE"


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
    assert citacao_confere("encarregado   PELO tratamento", pagina.texto)
    assert not citacao_confere("Maria", pagina.texto)  # curta demais para servir de prova


# ------------------------------------------------------------- Ollama


def _ollama_falso(resposta: dict, modelos=("qwen2.5:7b",)):
    """Servidor Ollama simulado (httpx.MockTransport): sem rede, sem modelo instalado."""
    pedidos = []

    def responder(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in modelos]})
        corpo = json.loads(request.content)
        pedidos.append(corpo)
        conteudo = json.dumps(resposta, ensure_ascii=False)
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": conteudo},
                "prompt_eval_count": 1500,
                "eval_count": 120,
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(responder))
    return ProvedorOllama("http://ollama-falso:11434", http=http), pedidos


def test_ollama_usa_o_mesmo_fluxo_e_custo_zero(tmp_path, snap):
    provedor, pedidos = _ollama_falso(
        _resposta("Encarregado pelo tratamento de dados pessoais: Maria da Silva")
    )
    juiz = JuizIA(provedor, "qwen2.5:7b", tmp_path / "cache", "v-teste")
    v = juiz.avaliar("18.1", REGRA, "Encarregado LGPD", snap, DA_REGRA)
    assert v.status == S.ATENDE and v.confianca == 0.8
    assert v.custo_llm_usd == 0.0  # modelo local: fora da tabela de preços
    assert v.detalhes["llm_provedor"] == "ollama"
    pedido = pedidos[0]
    assert pedido["format"]["properties"].keys() >= {"status", "citacao", "trecho"}  # esquema JSON
    assert pedido["options"]["temperature"] == 0
    assert pedido["options"]["num_ctx"] >= 8192  # sem isso o Ollama corta os trechos
    assert "Maria da Silva" in pedido["messages"][1]["content"]


def test_ollama_citacao_inventada_tambem_e_descartada(tmp_path, snap):
    provedor, _ = _ollama_falso(_resposta("O encarregado é João Pereira"))
    juiz = JuizIA(provedor, "qwen2.5:7b", tmp_path / "cache", "v-teste")
    v = juiz.avaliar("18.1", REGRA, "x", snap, DA_REGRA)
    assert v.status == S.NAO_AVALIADO and v.revisao_humana


def test_ollama_resposta_fora_do_formato_mantem_a_regra(tmp_path, snap):
    provedor, _ = _ollama_falso({"status": "TALVEZ"})  # modelo pequeno fugiu do esquema
    juiz = JuizIA(provedor, "qwen2.5:7b", tmp_path / "cache", "v-teste")
    v = juiz.avaliar("18.1", REGRA, "x", snap, DA_REGRA)
    assert v.status == DA_REGRA.status
    assert "erro_ia" in v.detalhes
    assert not list((tmp_path / "cache").glob("*.json"))  # erro não vai para o cache


def test_ollama_lista_modelos_e_detecta_servidor_desligado():
    provedor, _ = _ollama_falso({}, modelos=("qwen2.5:7b", "llama3.1:8b"))
    assert provedor.modelos_instalados() == ["qwen2.5:7b", "llama3.1:8b"]

    def recusa(request):
        raise httpx.ConnectError("conexão recusada", request=request)

    desligado = ProvedorOllama(http=httpx.Client(transport=httpx.MockTransport(recusa)))
    assert desligado.modelos_instalados() is None
