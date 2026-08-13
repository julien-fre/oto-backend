"""`include=procedures` — lire la RÈGLE qui a produit une fiche (#313).

Besoin partenaire : un jeton PORTÉ, borné à un projet, doit pouvoir lire le corps des
procédures que ce projet lie. La route `GET /api/me/projects/{id}` nomme déjà sa cible
dans le chemin et est déjà ouverte aux jetons portés — l'inclusion s'y greffe, plutôt
que d'ouvrir la famille `/api/me/instructions/*` entière pour un besoin qui tient à un
projet.

Quatre choses gardées ici, dont une non négociable :

1. **aucune métadonnée d'exécution** — la contrainte produit : une procédure servie ne
   dit rien des modèles. Figée par une ALLOWLIST, pas par une liste de mots interdits ;
2. **additif à l'octet près** — sans le paramètre, la réponse ne bouge pas ;
3. **le droit se vérifie procédure par procédure** — un projet peut lier une procédure
   d'une autre org (partage cross-org) : lire le projet n'emporte pas son corps ;
4. **une procédure absente ne casse rien** — lien mort ou accès refusé : le projet
   reste lisible, la procédure manque simplement à l'appel.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities import projects as cap


class _Ctx:
    def __init__(self, sub="sub-lecteur", org_id=1):
        self.sub, self.org_id, self.group_id = sub, org_id, None


PROCEDURE = {
    "id": 5, "org_id": 1, "owner_type": "org", "owner_id": "1",
    "slug": "qualification", "title": "Qualification d'un prospect",
    "description": "Comment qualifier", "body_md": "1. Vérifier le SIREN\n2. Noter",
    "slots": [{"name": "cible", "type": "tableau"}], "version": 3,
    "set_by": "sub-auteur", "created_at": "2026-08-01", "updated_at": "2026-08-10",
}

LIENS = [
    {"target_type": "procedure", "target_ref": "5", "title": "Qualification d'un prospect"},
    {"target_type": "tableau", "target_ref": "9", "namespace": "prospects"},
]


@pytest.fixture
def store(monkeypatch):
    """Le store et le seam d'accès, stubés — ce test porte sur la PROJECTION, pas
    sur le SQL (qui est exercé par le job `test` avec la vraie base)."""
    monkeypatch.setattr(cap.org_store, "get_instruction_by_id",
                        lambda i: PROCEDURE if int(i) == 5 else None)
    monkeypatch.setattr(cap.ownership, "can_access", lambda *a, **k: True)


# ── la contrainte non négociable ─────────────────────────────────────────────

def test_procedure_payload_is_an_allowlist(store):
    """⚠️ **La garde qui compte.** La contrainte produit est « aucune métadonnée
    d'exécution » ; une denylist de mots-clés la tiendrait le jour de son écriture et
    la perdrait au premier champ ajouté à `org_instructions`. On fige donc l'ensemble
    EXACT des clés servies : toute colonne future est absente par construction, et
    l'ajouter au rendu demandera de changer ce test — c'est-à-dire de le décider.

    Le stub porte exprès des champs à ne pas servir (`set_by`, `description`,
    `slots`, les horodatages) : ils existent en base, ils ne sortent pas."""
    got = cap._linked_procedures("sub-lecteur", LIENS)

    assert len(got) == 1
    assert set(got[0]) == set(cap._PROCEDURE_FIELDS)
    assert set(got[0]) == {"ref", "slug", "title", "version", "body_md"}
    assert got[0]["body_md"] == "1. Vérifier le SIREN\n2. Noter"
    assert got[0]["version"] == 3


def test_execution_metadata_never_leaks_even_if_the_row_grows(store, monkeypatch):
    """Le corollaire, joué sur le cas réel : demain quelqu'un ajoute une colonne
    d'exécution à `org_instructions`. Elle ne doit atteindre aucun client, sans que
    personne n'ait à y penser."""
    monkeypatch.setattr(cap.org_store, "get_instruction_by_id",
                        lambda i: {**PROCEDURE, "executed_by_model": "un-modèle",
                                   "candidate_models": ["a", "b"], "run_cost_eur": 0.42})

    got = cap._linked_procedures("sub-lecteur", LIENS)

    assert set(got[0]) == set(cap._PROCEDURE_FIELDS)
    rendu = repr(got)
    for interdit in ("model", "executed_by", "candidate", "cost"):
        assert interdit not in rendu, f"« {interdit} » a fuité : {rendu}"


# ── le droit, procédure par procédure ────────────────────────────────────────

def test_an_unreadable_procedure_is_absent_not_an_error(store, monkeypatch):
    """Un projet peut lier une procédure d'une AUTRE org (partage cross-org, #52) :
    lire le projet n'emporte pas le droit de lire tout ce qu'il désigne. La procédure
    interdite est ABSENTE — elle ne fait pas échouer la lecture du projet, et reste
    visible comme lien (titre, ref), seul son corps ne suit pas."""
    monkeypatch.setattr(cap.ownership, "can_access", lambda *a, **k: False)

    assert cap._linked_procedures("sub-lecteur", LIENS) == []


def test_ownership_is_consulted_for_the_doctrine_not_the_project(store, monkeypatch):
    """Le seam est interrogé sur la DOCTRINE (`doctrine`, son id), pas sur le projet —
    sinon le partage cross-org d'une procédure serait décidé par l'accès au projet qui
    la lie, ce qui n'est pas la même question."""
    vus = []
    monkeypatch.setattr(cap.ownership, "can_access",
                        lambda sub, rtype, rid, perm: vus.append((rtype, rid, perm)) or True)

    cap._linked_procedures("sub-lecteur", LIENS)

    assert vus == [("doctrine", "5", "read")]


def test_a_dead_link_does_not_break_the_read(store, monkeypatch):
    """Procédure supprimée : `audit.dead_links` est l'endroit qui le SIGNALE. Ce rendu
    ne le redit pas et surtout ne fait pas tomber la lecture du projet."""
    monkeypatch.setattr(cap.org_store, "get_instruction_by_id", lambda i: None)

    assert cap._linked_procedures("sub-lecteur", LIENS) == []


def test_a_slug_reference_is_skipped_not_resolved(store):
    """L'ADR 0032 a fixé l'id comme référence stable. Un `target_ref` non numérique est
    un résidu : on le saute plutôt que d'ouvrir un second chemin de résolution."""
    assert cap._linked_procedures(
        "sub-lecteur", [{"target_type": "procedure", "target_ref": "qualification"}]) == []


def test_order_follows_the_links(store, monkeypatch):
    """L'ordre des procédures est celui des liens du projet — c'est l'ordre que
    l'utilisateur a curé, et le lecteur le retrouve tel quel."""
    monkeypatch.setattr(cap.org_store, "get_instruction_by_id",
                        lambda i: {**PROCEDURE, "id": int(i), "slug": f"p{i}"})
    liens = [{"target_type": "procedure", "target_ref": r} for r in ("7", "5", "9")]

    assert [p["ref"] for p in cap._linked_procedures("sub", liens)] == ["7", "5", "9"]


# ── additif : sans le paramètre, rien ne bouge ───────────────────────────────

def test_without_include_the_response_is_unchanged(monkeypatch):
    """Contrat publié depuis #302 : `procedures` est un champ OPTIONNEL. Sans
    `include`, la clé n'apparaît pas — pas même à `null`, qui serait déjà un
    changement de forme pour un client qui énumère les clés."""
    monkeypatch.setattr(cap.ownership, "can_access", lambda *a, **k: True)
    appels = []
    monkeypatch.setattr(cap, "_linked_procedures",
                        lambda *a, **k: appels.append(1) or [])

    modele = cap.ProjectRead.model_fields
    assert "procedures" in modele and not modele["procedures"].is_required()
    assert cap.ProjectRead.model_fields["spine"].is_required() is False
    assert appels == []


# ── la portée du jeton n'est pas contournée par le paramètre ─────────────────

def test_the_include_does_not_widen_a_scoped_token():
    """⚠️ Le point de sécurité du lot, exigé par le brief : un jeton borné au projet
    12 lit ses procédures, un jeton borné au projet 13 ne lit pas celles du 12.

    Ça pourrait sembler acquis — c'est la route qui est bornée, pas le paramètre —
    mais l'acquis tient à un détail : `token_scopes.authorize` matche le CHEMIN, et
    son motif est ancré (`$`). Si la query string atteignait un jour ce match, le
    motif ne matcherait plus, la boucle tomberait au `return False` final… ou pire,
    un motif relâché laisserait passer. On garde donc le comportement plutôt que la
    lecture du code."""
    from oto_mcp import token_scopes

    porte_12 = {"projects": {"12": "read"}}

    # Le chemin servi par le routeur — le paramètre vit dans la query, pas ici.
    assert token_scopes.authorize(porte_12, "GET", "/api/me/projects/12") is True
    # Un jeton borné AILLEURS ne lit pas ce projet, paramètre ou non.
    assert token_scopes.authorize({"projects": {"13": "read"}}, "GET",
                                  "/api/me/projects/12") is False
    # Et si un appelant passait l'URL ENTIÈRE au gate, il refuse (motif ancré) —
    # fail-closed, jamais l'inverse.
    assert token_scopes.authorize(
        porte_12, "GET", "/api/me/projects/12?include=procedures") is False


def test_include_is_declared_on_both_surfaces():
    """L'option doit être atteignable par la route REST (le besoin partenaire) ET par
    `oto_project op=get` — c'est le même handler (ADR 0009), donc le paramètre doit
    exister des deux côtés sous peine d'une divergence de surface silencieuse."""
    assert "include" in cap.ProjectReadInput.model_fields
    assert "include" in cap.ProjectInput.model_fields
