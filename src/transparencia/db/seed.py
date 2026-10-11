"""Carga dos arquivos de configuração (YAML) para o banco.

É idempotente: pode rodar várias vezes sem duplicar nada.
- Catálogo: identificado pelo hash do conteúdo. Mudou um critério? Nova versão.
  Versões antigas permanecem — avaliações antigas continuam apontando para elas.
- Entidades: identificadas pelo `slug`; campos são atualizados se mudarem.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from transparencia.catalog.schema import CadastroEntidades, Catalogo
from transparencia.db.models import CatalogVersion, Criterion, Entity, EntityUrl


@dataclass
class ResultadoSeed:
    catalogo_versao_id: int
    catalogo_novo: bool
    entidades_criadas: int
    entidades_atualizadas: int


def _ordem(codigo: str) -> int:
    cat, num = codigo.split(".")
    return int(cat) * 1000 + int(num)


def sincronizar_catalogo(session: Session, catalogo: Catalogo) -> tuple[CatalogVersion, bool]:
    content_hash = catalogo.content_hash()
    existente = session.scalar(
        select(CatalogVersion).where(CatalogVersion.content_hash == content_hash)
    )
    if existente:
        return existente, False

    versao = CatalogVersion(
        versao=catalogo.versao,
        content_hash=content_hash,
        fonte=catalogo.fonte,
        pesos_classificacao={k.value: v for k, v in catalogo.pesos_classificacao.items()},
    )
    session.add(versao)
    session.flush()  # gera o id sem encerrar a transação

    for c in catalogo.criterios:
        cat = catalogo.categoria(c.categoria)
        session.add(
            Criterion(
                catalog_version_id=versao.id,
                codigo=c.codigo,
                categoria_codigo=cat.codigo,
                categoria_nome=cat.nome,
                bloco=cat.bloco,
                matriz=cat.matriz.value,
                descricao=c.descricao,
                fundamentacao=c.fundamentacao,
                dimensao=c.dimensao.value,
                classificacao=c.classificacao.value,
                peso=Decimal(str(catalogo.pesos_classificacao[c.classificacao])),
                aplica_a=c.aplica_a,
                condicional=c.condicional,
                nota=c.nota,
                ordem=_ordem(c.codigo),
            )
        )
    return versao, True


def _garantir_url(session: Session, entity: Entity, tipo: str, url: str) -> None:
    existente = session.scalar(
        select(EntityUrl).where(
            EntityUrl.entity_id == entity.id, EntityUrl.tipo == tipo, EntityUrl.url == url
        )
    )
    agora = datetime.now(UTC)
    if existente:
        existente.ativo = True
        existente.last_seen_at = agora
        return
    # URL nova do mesmo tipo vinda do cadastro: desativa as anteriores (histórico preservado)
    for antiga in session.scalars(
        select(EntityUrl).where(
            EntityUrl.entity_id == entity.id,
            EntityUrl.tipo == tipo,
            EntityUrl.origem == "cadastro",
            EntityUrl.ativo.is_(True),
        )
    ):
        antiga.ativo = False
    session.add(
        EntityUrl(entity_id=entity.id, tipo=tipo, url=url, origem="cadastro", last_seen_at=agora)
    )


def sincronizar_entidades(session: Session, cadastro: CadastroEntidades) -> tuple[int, int]:
    criadas = atualizadas = 0
    for e in cadastro.entidades:
        entity = session.scalar(select(Entity).where(Entity.slug == e.slug))
        campos = {
            "nome": e.nome,
            "municipio": e.municipio,
            "uf": e.uf,
            "codigo_ibge": e.codigo_ibge,
            "poder": e.poder.value,
            "esfera": e.esfera.value,
            "ativo": e.ativo,
            "notas": e.notas,
        }
        if entity is None:
            entity = Entity(slug=e.slug, **campos)
            session.add(entity)
            session.flush()
            criadas += 1
        else:
            mudou = any(getattr(entity, k) != v for k, v in campos.items())
            for k, v in campos.items():
                setattr(entity, k, v)
            atualizadas += int(mudou)

        _garantir_url(session, entity, "site", str(e.url_site))
        if e.url_portal:
            _garantir_url(session, entity, "portal", str(e.url_portal))
    return criadas, atualizadas


def executar_seed(
    session: Session, catalogo: Catalogo, cadastro: CadastroEntidades
) -> ResultadoSeed:
    versao, nova = sincronizar_catalogo(session, catalogo)
    criadas, atualizadas = sincronizar_entidades(session, cadastro)
    return ResultadoSeed(versao.id, nova, criadas, atualizadas)
