"""Cliquet : la carte SERVIE ne porte aucune clé que le contrat ne nomme (#667).

`GET /api/me/connectors?verbose=true` sert la ligne entière du catalogue. Jusqu'au
2026-09-01, `MyConnectorRow` n'en déclarait que huit clés (le mode compact) et laissait
les treize autres traverser par `extra="allow"` : servies, consommées par un front
tiers, et absentes du schéma. Le précédent est #583 — `secret_kind` avait exactement ce
défaut, et on l'a réparé pour UN champ.

**Ce fichier est le garde-fou mécanique qui remplace la discipline.** Une règle qu'il
faut tenir se perd ; ici, ajouter une clé au producteur (`providers/__init__.py::
public_catalog`, `_model.Connector.auth`) sans la déclarer fait rougir la CI. C'est la
condition posée pour pouvoir un jour retirer `extra="allow"` sans qu'un champ oublié
disparaisse du payload.

⚠️ **Le sens de la comparaison compte.** On part des clés SERVIES et on vérifie
qu'elles sont déclarées — jamais l'inverse. Un modèle qui déclare un champ que
personne ne sert est une promesse creuse, mais un champ servi et non déclaré est le
défaut qu'on répare, et c'est le seul que ce cliquet doit voir rouge.

Éprouvé rouge le 2026-09-01 avant d'être posé : `verifiable` retiré de
`MyConnectorRow` et `account_noun`/`hosted_channel` d'`AuthDescriptor` ⟹ deux tests en
échec, nommant les clés manquantes. Un cliquet jamais vu rouge est une intention.
"""
from __future__ import annotations

import pytest
from fastmcp import FastMCP

from oto_mcp.capabilities.connectors import catalog_card as card
from oto_mcp.capabilities.connectors.selection import _COMPACT_KEYS, MyConnectorRow
from oto_mcp.providers import public_catalog
from oto_mcp.tools import register_all


@pytest.fixture(scope="module", autouse=True)
def _declarations():
    """Charger EXACTEMENT ce que charge le boot — ni plus, ni moins.

    Un flux, une sonde `verify`, un backend d'identités se déclarent à l'IMPORT du
    module du connecteur. Un banc qui importe un module que le boot n'importe pas
    certifie une couverture qui n'existe pas (leçon de `test_connector_flow.py`,
    12/08) ; un banc qui n'importe rien mesure un catalogue où `connect` est nul
    partout, et passe au vert sans avoir rien regardé — d'où le témoin ci-dessous."""
    register_all(FastMCP("carte-connecteur-probe"))
    from oto_mcp.api import routes as api_routes  # noqa: F401


def _declares(model) -> set[str]:
    return set(model.model_fields)


def _resume(manquantes: dict[str, set[str]]) -> str:
    """Le message d'échec dit les CLÉS, pas les 97 connecteurs qui les portent tous.

    Un diagnostic qui déverse cent lignes identiques se lit moins bien qu'un
    diagnostic court : la clé nomme le geste à faire, le compte dit l'ampleur, et un
    exemple suffit à retrouver la ligne."""
    cles: dict[str, int] = {}
    exemple: dict[str, str] = {}
    for nom, cs in manquantes.items():
        for c in cs:
            cles[c] = cles.get(c, 0) + 1
            exemple.setdefault(c, nom)
    return " ; ".join(f"`{c}` (sur {n} connecteurs, ex. {exemple[c]})"
                      for c, n in sorted(cles.items()))


def test_le_banc_voit_bien_une_carte_complete():
    """Témoin : sans lui, tous les tests ci-dessous passeraient sur un catalogue
    dégarni (`connect` nul partout, `verifiable` faux partout) et ne prouveraient
    rien. Un contrôle qui ne peut pas échouer n'est pas un contrôle."""
    cat = public_catalog()
    assert len(cat) > 50, f"catalogue anormalement court ({len(cat)}) — banc mal chargé"
    assert any(r.get("connect") for r in cat), \
        "aucun connecteur à flux : le registre n'est pas chargé, le banc ne mesure rien"
    assert any(r.get("verifiable") for r in cat), \
        "aucune sonde `verify` enregistrée : le banc ne mesure rien"
    assert any((r.get("auth") or {}).get("fields") for r in cat), \
        "aucun champ de credential : le banc ne mesure rien"
    assert any(r.get("free_tier") for r in cat), \
        "aucun free-tier : le banc ne mesure rien de `free_tier`"


def test_toute_cle_servie_est_declaree():
    """Le cœur du cliquet — les clés de la carte, connecteur par connecteur."""
    declarees = _declares(MyConnectorRow)
    inconnues = {row["name"]: set(row) - declarees
                 for row in public_catalog() if set(row) - declarees}
    assert not inconnues, (
        f"clés servies et NON déclarées sur `MyConnectorRow` : {_resume(inconnues)}. "
        "Un champ ajouté au catalogue se déclare dans le même lot — sinon il est "
        "servi, consommé, et invisible au contrat (#667, et #583 avant lui pour "
        "`secret_kind`).")


def test_le_compact_reste_un_sous_ensemble_de_la_carte():
    """Les deux projections sortent du MÊME champ : le compact ne peut rien porter
    que la carte n'ait. Si un jour il divergeait, le contrat devrait le dire —
    aujourd'hui il ne le dit pas, donc la divergence est interdite."""
    cles_carte = {k for row in public_catalog() for k in row}
    assert set(_COMPACT_KEYS) <= cles_carte, \
        f"`_COMPACT_KEYS` hors catalogue : {set(_COMPACT_KEYS) - cles_carte}"
    assert set(_COMPACT_KEYS) <= _declares(MyConnectorRow)


@pytest.mark.parametrize("cle,modele", [
    ("doc_sections", card.DocSection),
    ("credential_fields", card.CredentialField),
])
def test_les_objets_de_liste_sont_declares(cle, modele):
    declarees = _declares(modele)
    inconnues: dict[str, set[str]] = {}
    for row in public_catalog():
        for item in row.get(cle) or []:
            if set(item) - declarees:
                inconnues.setdefault(row["name"], set()).update(set(item) - declarees)
    assert not inconnues, \
        f"clés de `{cle}[]` servies et non déclarées : {_resume(inconnues)}"


def test_le_descripteur_d_auth_est_declare():
    declarees = _declares(card.AuthDescriptor)
    champs = _declares(card.CredentialField)
    inconnues: dict[str, set[str]] = {}
    for row in public_catalog():
        auth = row.get("auth") or {}
        manquantes = set(auth) - declarees
        for f in auth.get("fields") or []:
            manquantes |= set(f) - champs
        if manquantes:
            inconnues[row["name"]] = manquantes
    assert not inconnues, (
        f"clés du descripteur d'auth servies et non déclarées : {_resume(inconnues)}. "
        "C'est LE bloc dont dépend le formulaire de credential d'un front tiers.")


def test_credential_fields_reste_le_miroir_de_auth_fields():
    """`credential_fields` est DÉRIVÉ d'`auth["fields"]`, pas recopié. Les deux clés
    sont servies et les deux sont déclarées ; ce test dit qu'elles ne peuvent pas
    diverger — le jour où l'une sera dépréciée, ce sera daté (#519), pas silencieux."""
    for row in public_catalog():
        assert row["credential_fields"] == (row.get("auth") or {}).get("fields"), \
            f"{row['name']} : `credential_fields` a divergé d'`auth.fields`"


def test_le_flux_de_connexion_est_declare():
    """`connect` porte deux clés de plus sur la projection AUTHENTIFIÉE
    (`callback_url`, `app_ready`, posées par `_me`) : elles sont déclarées sur
    `ConnectFlow` alors que le catalogue public ne les rend jamais. C'est voulu — le
    modèle décrit la carte du MEMBRE, qui est la plus riche des deux."""
    flux = _declares(card.ConnectFlow)
    param = _declares(card.ConnectParam)
    option = _declares(card.ConnectParamOption)
    assert {"callback_url", "app_ready"} <= flux, \
        "la projection authentifiée pose ces deux clés (cf. `_me`) — elles se déclarent"
    inconnues: dict[str, set[str]] = {}
    for row in public_catalog():
        c = row.get("connect")
        if not c:
            continue
        manquantes = set(c) - flux
        for p in c.get("params") or []:
            manquantes |= set(p) - param
            for o in p.get("options") or []:
                manquantes |= set(o) - option
        if manquantes:
            inconnues[row["name"]] = manquantes
    assert not inconnues, \
        f"clés de `connect` servies et non déclarées : {_resume(inconnues)}"


def test_le_free_tier_est_declare():
    declarees = _declares(card.FreeTier)
    inconnues = {row["name"]: set(row["free_tier"]) - declarees
                 for row in public_catalog()
                 if row.get("free_tier") and set(row["free_tier"]) - declarees}
    assert not inconnues, \
        f"clés de `free_tier` servies et non déclarées : {_resume(inconnues)}"


def test_la_declaration_ne_valide_pas_donc_ne_peut_rien_retirer():
    """La garantie qui rend ce lot sûr sur une surface déjà consommée : `Output`
    DÉCRIT, il ne valide pas (cf. `capabilities/_types.py`). Le handler rend un
    `dict`, personne ne le passe par le modèle — donc déclarer ne peut pas faire
    disparaître un champ du payload. On l'éprouve plutôt que de l'affirmer : un
    modèle qui laisserait tomber un extra ferait rougir ici."""
    ligne = dict(public_catalog()[0], state="active", recommended=False,
                 guide_ref_count=0, doctrine_ref_count=0, option_ok=True,
                 champ_jamais_declare="témoin")
    reconstruit = MyConnectorRow(**ligne).model_dump()
    assert reconstruit.get("champ_jamais_declare") == "témoin", \
        "`extra=allow` ne retient plus les champs non déclarés — retirer ce cran " \
        "AVANT d'avoir tout déclaré ferait disparaître du payload ce qu'on a oublié"
    assert set(ligne) <= set(reconstruit), \
        f"clés perdues à la reconstruction : {set(ligne) - set(reconstruit)}"
