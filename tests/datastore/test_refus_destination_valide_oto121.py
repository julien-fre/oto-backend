"""Une adresse indexée ne s'écrit pas, et le seul geste qui marche est prouvé.

⚠️ **Ce banc est né d'un refus qui n'existe plus.** Le refus opposé au nom projeté
d'une migration (`contact1_nom`) prescrivait « écrire `contacts[0].nom` » — une forme
que `_refuse_dotted_names` rejette deux gardes plus loin : deux appels, deux refus,
rien d'écrit. `flat_alias` est retirée le 07/09/2026 (zéro colonne de production), et
les trois tests qui pesaient les mots de ce message sont partis avec elle.

Ce qui RESTE, et qui n'a jamais dépendu de l'alias : une adresse indexée est une
adresse de LECTURE, pas une clé d'écriture — elle se refuse. Et il n'existe aucune
écriture au grain de l'élément : le seul geste qui écrit est de reposer la
colonne-liste ENTIÈRE, couches réémises (oto#120).

⚠️ Le banc ÉCRIT réellement — c'est le seul moyen de prouver qu'une destination est
valide. Une assertion sur le texte d'un message prouverait seulement que le test est
d'accord avec lui-même.
"""
from __future__ import annotations

import uuid

import pytest

from oto_mcp.datastore.errors import RowValidationError


SCHEMA = {
    "key": "siren",
    "fields": [
        {"key": "siren", "type": "text"},
        {"key": "contacts", "type": "list",
         "of": {"type": "object", "fields": [
             {"key": "nom", "type": "text"},
             {"key": "email", "type": "email"}]}},
    ],
}


@pytest.fixture
def table(live):
    """Une colonne-tableau, et une ligne dont un élément porte une couche."""
    from oto_mcp import db
    from oto_mcp.datastore.core import make_store
    ns = "t-" + uuid.uuid4().hex[:6]
    ns_id = db.create_datastore("user", "sub-test", ns)
    st = make_store("sub-test")
    st.set_schema(ns, SCHEMA)
    row = st.append_row(ns, {"siren": "377768379", "contacts": [
        {"nom": "Ada", "nom.comment": "registre", "email": "ada@exemple.fr"}]})
    return st, ns, ns_id, row["_id"]


def _donnees(ns_id: int, row_id: str) -> dict:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        r = conn.execute(
            "SELECT data FROM datastore_rows WHERE ns_id = %s AND row_id = %s",
            (ns_id, row_id)).fetchone()
    return dict((r or {}).get("data") or {})


def _refus(st, ns, rid, payload) -> str:
    with pytest.raises((RowValidationError, ValueError)) as exc:
        st.update_row(ns, rid, payload)
    errs = getattr(exc.value, "errors", None)
    return " ".join(errs) if errs else str(exc.value)


# ══ le fait qui rendait l'indication invalide ═══════════════════════════════════

def test_une_adresse_indexee_est_bien_refusee_a_l_ecriture(table):
    """Le second refus — celui sur lequel retombait qui suivait l'indication. Il est
    JUSTE et ne bouge pas : c'est le premier message qui avait tort d'y envoyer."""
    st, ns, _ns_id, rid = table

    assert "n'est pas un nom de colonne" in _refus(
        st, ns, rid, {"contacts[0].nom": "Ada"})


# ══ le seul geste qui écrit passe VRAIMENT ══════════════════════════════════════

def test_reposer_la_liste_ENTIERE_ecrit_pour_de_bon(table):
    """La contrepartie du refus ci-dessus : puisque `contacts[0].nom` ne s'écrit pas,
    il faut que reposer la liste ENTIÈRE, couches réémises, écrive vraiment — sans
    quoi le refus fermerait la seule porte au lieu d'en indiquer une autre."""
    st, ns, ns_id, rid = table

    st.update_row(ns, rid, {"contacts": [
        {"nom": "ADA LOVELACE", "nom.comment": "registre",
         "email": "ada@exemple.fr"}]})

    contacts = _donnees(ns_id, rid)["contacts"]
    assert contacts[0]["nom"] == {"valeur": "ADA LOVELACE", "comment": "registre"}
    assert "couches_effacees" not in st.off_schema_report(), \
        "le seul geste qui écrit ne doit rien détruire au passage"
