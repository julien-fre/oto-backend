"""« tenant » : 17 occurrences dans le contrat, 0 dans la doc publique, aucune
définition (oto-backend#775). Rectificatif du 06/09/2026 : le mot est juste, il
n'y a rien à renommer — seule la définition manquait. Ce banc vérifie qu'elle est
bien SERVIE (JSON schema Pydantic), pas seulement écrite en commentaire Python.

⚠️ Et qu'elle décrit les valeurs RÉELLEMENT SERVIES. La première version du lot
partageait UN texte entre les champs `level` et `InstanceOwner.type` : il énumérait
`member`, qui n'existe pas dans l'énumération de `type` (`user`, `group`, `org`,
`tenant`, `platform`), et laissait `user` — la valeur vraiment servie — sans aucune
définition. Une description partagée « pour éviter la divergence » avait donc créé
une divergence pire : le texte d'un champ décrivait l'énumération d'un autre. D'où
l'invariant principal ci-dessous, posé sur le SCHÉMA et non sur une phrase : chaque
description cite exactement les crans que son champ accepte, ni plus ni moins.
"""
from __future__ import annotations

from oto_mcp.capabilities.connectors.instances import (
    ConnectorInstance, InstanceOwner, ListInstancesInput,
)
from oto_mcp.capabilities.connectors.verify import VerifyResult

_TERMES_ATTENDUS = ("tenant", "au-dessus", "hébergeur")


# Tous les crans de l'échelle, les DEUX orthographes du plus proche comprises
# (`member` côté `level`, `user` côté `InstanceOwner.type`).
_CRANS = ("member", "user", "group", "org", "tenant", "platform")

_CHAMPS = (
    (ConnectorInstance, "level"),
    (VerifyResult, "level"),
    (ListInstancesInput, "level"),
    (InstanceOwner, "type"),
)


def _description(schema: dict, champ: str) -> str:
    return schema["properties"][champ]["description"]


def _valeurs_servies(schema: dict, champ: str) -> set:
    """Les crans que le champ ACCEPTE, lus dans le schéma servi. `Optional[Literal]`
    sort en `anyOf` (l'énumération + `null`), un `Literal` nu en `enum` direct."""
    prop = schema["properties"][champ]
    if "enum" in prop:
        return set(prop["enum"])
    for variante in prop.get("anyOf", []):
        if "enum" in variante:
            return set(variante["enum"])
    raise AssertionError(f"pas d'énumération servie sur `{champ}` : {prop}")


def test_connector_instance_level_definit_le_tenant():
    desc = _description(ConnectorInstance.model_json_schema(), "level")
    assert all(mot in desc.lower() for mot in _TERMES_ATTENDUS), desc


def test_instance_owner_type_definit_le_tenant():
    desc = _description(InstanceOwner.model_json_schema(), "type")
    assert all(mot in desc.lower() for mot in _TERMES_ATTENDUS), desc


def test_verify_result_level_definit_le_tenant():
    desc = _description(VerifyResult.model_json_schema(), "level")
    assert all(mot in desc.lower() for mot in _TERMES_ATTENDUS), desc


def test_list_instances_input_level_definit_le_tenant():
    desc = _description(ListInstancesInput.model_json_schema(), "level")
    assert all(mot in desc.lower() for mot in _TERMES_ATTENDUS), desc


def test_chaque_texte_cite_EXACTEMENT_les_crans_que_son_champ_accepte():
    """L'invariant qui a manqué au premier jet : une description servie n'énumère
    jamais un cran que son champ refuse, et n'en laisse aucun sans le nommer. Posé
    sur le schéma — si l'énumération bouge, c'est ce banc qui le dit, pas un agent
    qui aura essayé `member` sur un `owner.type` et reçu un refus de validation."""
    for modele, champ in _CHAMPS:
        schema = modele.model_json_schema()
        desc = _description(schema, champ)
        servies = _valeurs_servies(schema, champ)
        cites = {c for c in _CRANS if f"`{c}`" in desc}
        assert cites == servies, (
            f"{modele.__name__}.{champ} : le texte cite {sorted(cites)} pour une "
            f"énumération qui vaut {sorted(servies)}")


def test_les_champs_level_partagent_le_meme_texte():
    """Une seule définition pour les crans écrits pareil — pas trois formulations
    qui pourraient diverger avec le temps."""
    d1 = _description(ConnectorInstance.model_json_schema(), "level")
    d2 = _description(VerifyResult.model_json_schema(), "level")
    d3 = _description(ListInstancesInput.model_json_schema(), "level")
    assert d1 == d2 == d3


def test_le_proprietaire_a_SON_texte_et_la_MEME_definition_du_tenant():
    """`InstanceOwner.type` ne peut pas partager le texte des `level` (il n'a pas de
    cran `member`), mais la définition de `tenant` — la seule chose que #775
    demandait — reste littéralement la même chaîne des deux côtés."""
    from oto_mcp.capabilities.connectors._level_doc import DOC_TENANT

    d_level = _description(ConnectorInstance.model_json_schema(), "level")
    d_owner = _description(InstanceOwner.model_json_schema(), "type")
    assert d_level != d_owner
    assert DOC_TENANT in d_level and DOC_TENANT in d_owner
