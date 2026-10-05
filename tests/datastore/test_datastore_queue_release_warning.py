"""File de travail (ADR 0046 D) — un STATUT sans état TERMINAL ne libère aucun bail.

Signal #360 : un vivier drainé par 4 workers déclarait `role="status"` + `options`,
mais pas de `lifecycle` ; l'écriture du verdict n'a donc rien libéré et 149 lignes
traitées sont restées réservées. Le substrat faisait ce qui est écrit — c'est le
SILENCE de la configuration qui coûtait. On garde ici le fait qu'il parle."""
from __future__ import annotations

from oto_mcp.datastore import core as D
from oto_mcp.datastore import schema as dsv2

_STATUS = {"key": "statut", "role": "status", "type": "enum",
           "options": ["a_enrichir", "enrichi", "echec"]}


def _with_lifecycle(**lc):
    return {"fields": [{**_STATUS, "lifecycle": lc}]}


def test_une_colonne_qui_RESSEMBLE_a_un_etat_sans_lifecycle_avertit():
    """La colonne d'état est CELLE QUI PORTE le `lifecycle` (08/09/2026) : une colonne
    qui n'en porte pas n'est pas un état, et aucune garde du cycle de vie ne s'y
    applique. L'avertissement le dit.

    ⚠️ **Il affirmait jusqu'au 29/09/2026 que le tableau n'avait « PAS de file de
    travail »** (oto#91, retour 757) — alors que `claim_next` ne lit pas le
    `lifecycle` et servait la ligne libre DANS LA MÊME RÉPONSE que l'avertissement
    (cf. `test_claim_next_warns_the_worker`). Un agent qui lit « pas de
    file » écrit une boucle lire-puis-marquer, non atomique : c'est ce qu'il a fait."""
    w = dsv2.queue_release_warning({"fields": [_STATUS]})

    assert w and "statut" in w
    assert "c'est le bloc `lifecycle` qui fait l'état" in w
    assert "n'a PAS de file" not in w, "la réservation marche sans rien déclarer"
    assert "fonctionne quand même" in w and "data_release" in w


def test_lifecycle_without_derivable_terminal_warns():
    # tout état a une transition sortante ⇒ ensemble terminal dérivé VIDE
    schema = _with_lifecycle(states=["a", "b"], transitions={"a": ["b"], "b": ["a"]})
    assert dsv2.terminal_states(schema) == set()
    w = dsv2.queue_release_warning(schema)
    # oto#91 : il promettait qu'un `terminal` déclaré ferait relâcher le bail au
    # verdict — la libération automatique est retirée (#317). Il dit ce que le
    # terminal manquant retire vraiment, et que le bail se rend à la main.
    assert w and "abandon_state" in w and "data_release" in w
    assert "ne libère jamais" in w
    assert "AUCUN bail" not in w


def test_explicit_terminal_is_silent():
    schema = _with_lifecycle(states=["a_enrichir", "enrichi"], terminal=["enrichi"])
    assert dsv2.queue_release_warning(schema) is None


def test_derived_terminal_is_silent():
    schema = _with_lifecycle(states=["a", "fini"], transitions={"a": ["fini"]})
    assert dsv2.queue_release_warning(schema) is None


def test_no_status_field_is_silent():
    # table libre ou schéma sans statut : la file ne la concerne pas
    assert dsv2.queue_release_warning({"fields": [{"key": "nom", "type": "text"}]}) is None
    assert dsv2.queue_release_warning(None) is None


def test_set_schema_returns_the_warning(monkeypatch):
    """L'auteur du schéma l'apprend au moment où il le pose — les DEUX faces, le
    retour de `set_schema` étant servi tel quel par le tool MCP et la route REST."""
    monkeypatch.setattr(D.db, "set_datastore_schema", lambda ns_id, schema: None)
    # oto#82 : ce db stubbé ne porte aucun index — la pose reste donc faite.
    monkeypatch.setattr(D.db, "datastore_has_key_index", lambda ns_id: False)
    monkeypatch.setattr(D.db, "datastore_drop_key_index", lambda ns_id: None)
    # oto-backend#479 : le relevé des lignes en place compte d'abord le tableau — vide ici.
    monkeypatch.setattr(D.db, "datastore_count_rows", lambda *a, **k: 0)
    # oto#124 : les options feront contrat partout à la date — l'existant hors liste
    # se relève dès la pose. Aucun ici.
    monkeypatch.setattr(D.db, "datastore_offending_enum_values", lambda *a, **k: [])
    s = D.DatastorePg("u1")
    monkeypatch.setattr(s, "_resolve", lambda ns, write=False: 7)
    # `set_schema` relit le schéma en place avant de le remplacer (#388).
    monkeypatch.setattr(s, "_ns_of", lambda ns_id: {})

    out = s.set_schema("vivier", {"fields": [_STATUS]})
    assert "warning" in out
    out = s.set_schema("vivier", _with_lifecycle(states=["a", "fini"], terminal=["fini"]))
    assert "warning" not in out


def test_claim_next_warns_the_worker(monkeypatch):
    """Le worker qui claim est celui que ça concerne : sans terminal, c'est à lui
    d'appeler data_release. Rien n'est signalé quand la file est vide (pas de row)."""
    row = {"row_id": "r1", "created_at": "c", "updated_at": "u",
           "data": {"statut": "a_enrichir"}, "claimed_by": "w-1", "claimed_until": "t",
           "claimed_run": None, "claim_active": True}
    schema = {"fields": [_STATUS]}
    monkeypatch.setattr(D.db, "datastore_claim_next",
                        lambda ns_id, **kw: row if kw.get("filters") is not None else None)
    s = D.DatastorePg("u1")
    monkeypatch.setattr(s, "_resolve", lambda ns, write=False: 7)
    # Une seule lecture de la ligne datastore sert le warning ET le relevé de journal
    # (`_after_claim`) — d'où le stub à ce grain, pas sur le schéma seul.
    monkeypatch.setattr(s, "_ns_of", lambda ns_id: {"datastore": "vivier", "schema": schema})

    warnings: list = []
    assert s.claim_next("vivier", worker="w-1", warnings=warnings)["_id"] == "r1"
    # Le worker reçoit l'avertissement du schéma tel qu'il est : ici la colonne
    # ressemble à un état sans en être un — ET la ligne lui est servie quand même.
    # Le texte ne peut donc pas dire « pas de file » (oto#91).
    assert len(warnings) == 1 and "fonctionne quand même" in warnings[0]

    # schéma sain ⇒ silence ; et `warnings` reste optionnel (appelants historiques)
    monkeypatch.setattr(s, "_ns_of", lambda ns_id: {
        "datastore": "vivier",
        "schema": _with_lifecycle(states=["a_enrichir", "enrichi"], terminal=["enrichi"])})
    warnings = []
    s.claim_next("vivier", worker="w-1", warnings=warnings)
    assert warnings == []
    assert s.claim_next("vivier", worker="w-1")["_id"] == "r1"


def test_claim_next_silent_when_queue_is_empty(monkeypatch):
    monkeypatch.setattr(D.db, "datastore_claim_next", lambda ns_id, **kw: None)
    s = D.DatastorePg("u1")
    monkeypatch.setattr(s, "_resolve", lambda ns, write=False: 7)
    monkeypatch.setattr(s, "_ns_of", lambda ns_id: {"datastore": "vivier",
                                                    "schema": {"fields": [_STATUS]}})

    warnings: list = []
    assert s.claim_next("vivier", worker="w-1", warnings=warnings) is None
    assert warnings == []  # rien à traiter ⇒ rien à dire
