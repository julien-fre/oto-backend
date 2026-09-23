"""`DatastoreEntry.owner_type` / `CreatedDatastore.owner_type` (oto-backend#775,
point 2) : chaîne libre sans description avant ce lot → `Literal["user","org",
"group"]` décrit. Les trois valeurs sont les seules posées en base
(`user_datastores.owner_type`, cf. `oto_mcp/db/_init.py` et `datastore_ns.py`).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from oto_mcp.capabilities.datastore.datastores import CreatedDatastore, DatastoreEntry


def test_owner_type_decrit_sur_datastore_entry():
    schema = DatastoreEntry.model_json_schema()
    desc = schema["properties"]["owner_type"].get("description", "")
    assert "ADR 0068" in desc
    assert "group" in desc.lower() or "équipe" in desc.lower()


def test_owner_type_decrit_sur_created_datastore():
    schema = CreatedDatastore.model_json_schema()
    desc = schema["properties"]["owner_type"].get("description", "")
    assert "ADR 0068" in desc


def test_une_valeur_hors_enum_est_refusee_sur_created_datastore():
    with pytest.raises(ValidationError):
        CreatedDatastore(datastore="x", id=1, ns_id=1, owner_type="platform")


def test_les_trois_valeurs_reelles_restent_acceptees():
    for v in ("user", "org", "group"):
        CreatedDatastore(datastore="x", id=1, ns_id=1, owner_type=v)
