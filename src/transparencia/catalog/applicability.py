"""Regra de aplicabilidade: um critério vale para este tipo de entidade?

Decidir isso ANTES de coletar/avaliar evita penalizar uma entidade por um
requisito que não pertence à sua categoria (ex.: "lista de espera em creches"
não se aplica a uma Câmara Municipal). Critérios não aplicáveis recebem o status
NAO_APLICAVEL e saem do denominador da pontuação.
"""

from transparencia.catalog.schema import Criterio, Esfera, Poder


def padrao_casa(padrao: str, poder: Poder, esfera: Esfera) -> bool:
    if padrao == "*":
        return True
    p_poder, _, p_esfera = padrao.partition(":")
    if p_poder != poder.value:
        return False
    return p_esfera in ("", "*") or p_esfera == esfera.value


def criterio_aplicavel(criterio: Criterio, poder: Poder, esfera: Esfera) -> bool:
    return any(padrao_casa(p, poder, esfera) for p in criterio.aplica_a)
