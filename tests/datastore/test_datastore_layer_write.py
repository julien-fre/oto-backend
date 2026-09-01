"""Écrire une colonne à couches : la valeur se juge déballée, l'origine survit (#318).

Deux règles, et elles ont la même raison — l'agent ne doit avoir à penser à rien :

- **la validation déballe** : un schéma strict qui déclare `email` en `text` doit
  juger la VALEUR, pas l'enveloppe. Sans ça, la primitive est inutilisable
  précisément sur les tableaux qu'on recommande de rendre stricts ;
- **l'origine survit** à une écriture ordinaire. C'est la protection contre
  l'ACCIDENT, pas contre l'intention : un geste qui vise l'origine la remplace, il
  suffit de l'écrire.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore import schema as dsv2
from oto_mcp.datastore.core import _merge_column


# --- la validation juge la valeur ----------------------------------------------

_STRICT = {"strict": True, "fields": [
    {"key": "email", "type": "email"},
    {"key": "effectif", "type": "number"},
    {"key": "statut", "type": "enum", "options": ["a", "b"]},
    {"key": "nom", "type": "text", "required": True, "max_length": 5},
]}


def _err(row):
    return dsv2.validate_row(_STRICT, row)


def test_a_layered_value_passes_the_type_check():
    """Le cas qui débloque tout : un objet là où le schéma attend un e-mail."""
    assert _err({"nom": "ACME", "email": {"valeur": "a@b.c", "comment": "hunter"}}) == []


def test_a_layered_value_is_still_JUDGED():
    """Déballer n'est pas dispenser : une valeur fausse reste fausse."""
    errs = _err({"nom": "ACME", "email": {"valeur": "pas-un-email", "comment": "x"}})
    assert errs and "email" in errs[0]


def test_options_are_checked_on_the_value():
    assert _err({"nom": "ACME", "statut": {"valeur": "a"}}) == []
    errs = _err({"nom": "ACME", "statut": {"valeur": "zzz"}})
    assert errs and "hors options" in errs[0]


def test_a_bound_measures_the_value_not_the_envelope():
    """`max_length: 5` doit mesurer « ACME », pas le JSON qui l'enveloppe — sinon
    toute écriture en couches dépasserait, quelle que soit la valeur."""
    assert _err({"nom": {"valeur": "ACME", "comment": "registre"}}) == []
    errs = _err({"nom": {"valeur": "BEAUCOUP TROP LONG", "comment": "x"}})
    assert errs and "nom" in errs[0]


def test_a_required_field_empty_in_its_layers_is_missing():
    errs = _err({"nom": {"valeur": "", "comment": "x"}})
    assert errs and "requis" in errs[0]


def test_a_flat_row_is_judged_exactly_as_before():
    assert _err({"nom": "ACME", "email": "a@b.c", "effectif": 3}) == []
    assert _err({"nom": "ACME", "email": "pas-un-email"})


# --- l'origine survit -----------------------------------------------------------

def test_an_ordinary_write_keeps_the_origin():
    """LE cas : l'agent écrit une valeur nue, sans savoir qu'il y a des couches."""
    out = _merge_column({"valeur": "ancien", "origine": "import"}, "nouveau")
    assert out == {"valeur": "nouveau", "origine": "import"}


def test_the_other_layers_follow_the_value():
    """`comment`/`link` décrivent LA VALEUR : les garder au-dessus d'une valeur
    remplacée ferait affirmer une provenance fausse — le défaut qu'on élimine, une
    couche plus haut."""
    out = _merge_column(
        {"valeur": "a@b.c", "origine": "import", "comment": "hunter",
         "link": "https://x"},
        "autre@x.fr")
    assert out == {"valeur": "autre@x.fr", "origine": "import"}


def test_an_explicit_gesture_replaces_the_origin():
    """Pas de verrou : viser l'origine suffit à la remplacer. Un ré-import repose
    simplement une nouvelle valeur de départ."""
    out = _merge_column({"valeur": "x", "origine": "vieux"},
                        {"valeur": "y", "origine": "neuf"})
    assert out["origine"] == "neuf"


def test_writing_layers_without_an_origin_keeps_the_existing_one():
    out = _merge_column({"valeur": "x", "origine": "import"},
                        {"valeur": "y", "comment": "registre"})
    assert out == {"valeur": "y", "comment": "registre", "origine": "import"}


def test_a_column_without_an_origin_is_replaced_plainly():
    """Rien à préserver ⇒ le comportement d'avant, à l'identique. C'est le cas des
    43 782 lignes existantes, et il ne doit pas coûter une ligne de logique."""
    assert _merge_column("ancien", "nouveau") == "nouveau"
    assert _merge_column(None, "nouveau") == "nouveau"
    assert _merge_column({"a": 1}, "nouveau") == "nouveau"
    assert _merge_column({"valeur": "x", "comment": "s"}, "nouveau") == "nouveau"


# --- ce que l'agent LIT ---------------------------------------------------------

def _read(data: dict) -> dict:
    from oto_mcp.datastore.core import DatastorePg
    return DatastorePg._row_to_dict(
        {"row_id": "r1", "created_at": "t", "updated_at": "t", "data": data})


def test_the_bare_name_always_returns_the_value():
    """LE contrat de lecture : `row["email"]` rend un e-mail, provenance ou pas.
    Sans ça, tout consommateur qui lit une colonne casse le jour où quelqu'un y met
    une source — silencieusement, puisqu'il recevrait un objet au lieu d'un texte."""
    assert _read({"email": "a@b.c"})["email"] == "a@b.c"
    assert _read({"email": {"valeur": "a@b.c", "comment": "hunter"}})["email"] == "a@b.c"


def test_filled_layers_are_exposed_flat():
    out = _read({"email": {"valeur": "a@b.c", "comment": "hunter",
                           "link": "https://x", "origine": "import"}})
    assert out["email.comment"] == "hunter"
    assert out["email.link"] == "https://x"
    assert out["email.origine"] == "import"


def test_empty_layers_are_not_rendered():
    """Une colonne « plate » est une colonne dont les sous-champs sont VIDES — et on
    ne rend pas du vide. C'est ce qui garde une ligne sans provenance identique à ce
    qu'elle était."""
    assert _read({"email": "a@b.c"}) == _read({"email": {"valeur": "a@b.c"}})
    out = _read({"email": {"valeur": "a@b.c", "comment": "", "link": None}})
    assert [k for k in out if k.startswith("email.")] == []


def test_layers_are_projectable_like_any_column():
    """Elles s'atteignent par `fields` sans que la projection ait rien à apprendre :
    ce sont des clés de la ligne, comme les autres."""
    from oto_mcp.tools.datastore import _project_row
    row = _read({"nom": "ACME", "email": {"valeur": "a@b.c", "comment": "hunter"}})
    assert _project_row(row, ["nom", "email.comment"]) == {
        "_id": "r1", "nom": "ACME", "email.comment": "hunter"}


# --- lecteur tolérant, écrivain strict -------------------------------------------

def test_an_unknown_layer_is_refused_at_write():
    """Déjà payé dans l'autre sens : une clé `enum:` posée là où le validateur lit
    `options:` a été acceptée, stockée, jamais lue — et 504 lignes écrites en croyant
    le champ contraint. Une couche mal orthographiée s'apprend à l'écriture."""
    errs = _err({"nom": "ACME", "email": {"valeur": "a@b.c", "sourse": "hunter"}})
    assert errs and "sourse" in errs[0] and "comment" in errs[0]


def test_a_plain_json_value_is_not_judged_as_layers():
    """Un dict sans AUCUNE clé de couche connue est une donnée `json` ordinaire —
    pas une colonne à couches. On n'y touche pas.

    ⚠️ Ce test a affirmé le critère « sans `valeur` » jusqu'au 14/08 (#329) : il
    protégeait alors le trou — `{"origine": x, "sourse": y}` passait sans refus
    et écrasait la valeur. Le critère corrigé : ≥1 clé de couche connue présente
    ⟹ la validation s'applique, `valeur` ou pas."""
    assert dsv2.unknown_layers({"a": 1, "sourse": 2}) == []
    assert dsv2.unknown_layers({"origine": "x", "sourse": "y"}) == ["sourse"], \
        "le geste du rattrapage (#326), une faute de frappe plus loin — refusé"


def test_the_reader_tolerates_what_the_writer_refuses():
    """L'asymétrie EST le contrat d'évolution : une couche écrite par une version
    plus récente doit rester lisible, sinon un déploiement progressif casse les
    anciens nœuds. Le lecteur ignore, il ne lève jamais."""
    out = _read({"email": {"valeur": "a@b.c", "couche_du_futur": "x"}})
    assert out["email"] == "a@b.c"
    assert "email.couche_du_futur" not in out


def test_a_layer_may_become_structured():
    """Rien ne fige « une couche est un scalaire » : le jour où `comment` devient
    structuré, il traverse tel quel — c'est le patron polymorphe à réappliquer un
    niveau plus bas, pas une réécriture."""
    out = _read({"email": {"valeur": "a@b.c", "comment": {"texte": "x", "tag": "y"}}})
    assert out["email.comment"] == {"texte": "x", "tag": "y"}


# --- le champ-clé accepte les couches depuis que l'index est polymorphe ---------

def test_the_business_key_may_now_carry_layers():
    """Le gate qui refusait les couches sur la clé métier est LEVÉ : l'index
    d'unicité et le lookup lisent désormais la même expression polymorphe, donc une
    valeur enveloppée collisionne avec la même valeur nue.

    Vérifié contre PostgreSQL avant de lever : insérer `{"siren": {"valeur": "X"}}`
    à côté d'un `{"siren": "X"}` existant lève bien une violation d'unicité. Sans
    cette vérification, lever le gate aurait rouvert le doublon silencieux qu'il
    servait à empêcher."""
    keyed = {"strict": True, "key": "siren", "fields": [{"key": "siren", "type": "text"}]}
    assert dsv2.validate_row(
        keyed, {"siren": {"valeur": "552081317", "comment": "registre"}}) == []


# --- l'origine survit sur TOUS les chemins d'écriture ----------------------------

# `update_row` avait sa PROPRE fusion : la préservation n'était câblée que dans le
# chemin batch, donc un patch par `id` — le geste le plus courant d'un agent — effaçait
# l'origine quand même. Un seul chemin corrigé sur deux ne corrige rien.
#
# Ce fait était gardé ici par un test qui LISAIT le texte d'`update_row` à la recherche
# de l'appel. Retiré : il affirmait une intention (« le code appelle telle fonction »)
# là où la propriété est un COMPORTEMENT, et il virait au rouge dès qu'une autre
# session éditait le module — le décalage de `linecache` suffit, sans qu'aucun
# comportement n'ait bougé. Un faux rouge sur un tree partagé coûte à tout le monde, et
# celui-là ne disait rien de plus que ce que prouve déjà, en l'exerçant vraiment,
# `test_datastore_origin_survives_sequence.py` (écriture, RÉÉCRITURE, puis lecture de
# l'origine — par `DatastorePg.update_row`, le chemin qu'un agent emprunte).


def test_an_origin_alone_still_reads_flat():
    """Un import de socle pose `origine` sur un champ qu'aucun agent n'a renseigné :
    il n'y a pas encore de `valeur`. La lecture rendait alors l'OBJET — donc tout ce
    qui attend une chaîne cassait, sur le chemin qu'on recommande."""
    out = _read({"contact1_nom": {"origine": "DUPONT Jean (fichier client)"}})
    assert out["contact1_nom"] is None
    assert out["contact1_nom.origine"] == "DUPONT Jean (fichier client)"


def test_an_ordinary_write_over_an_origin_only_column_keeps_it():
    """Le cas de la campagne bout en bout : socle importé, puis l'agent renseigne."""
    out = _merge_column({"origine": "DUPONT Jean"}, "MARTIN Claire")
    assert out == {"valeur": "MARTIN Claire", "origine": "DUPONT Jean"}


def test_a_genuine_json_object_is_still_opaque():
    """La règle ne s'élargit qu'aux objets faits UNIQUEMENT de couches connues : un
    `json` métier garde sa forme."""
    assert dsv2.unwrap({"a": 1, "origine": "x"}) == {"a": 1, "origine": "x"}
