"""oto#135 — un argument inconnu sur un outil écrit à la main est NOMMÉ, et un nom RETIRÉ
dit son remplaçant.

Sous FastMCP 3, une clé que la signature d'un outil ne connaît pas se type
`unexpected_keyword_argument` — pas `extra_forbidden`, le seul que la taxonomie
reconnaissait. Réponse servie jusqu'au 10/09/2026 à `data_write(namespace="vivier")` :

    Arguments invalides — champ(s) requis absent(s) : datastore · valeur(s) refusée(s) :
    namespace (Unexpected keyword argument). Le schéma exact : oto_tool_schema(name=…).

Un agent y lit un paramètre MANQUANT, en anglais, et recompose son appel à neuf. La face
capacité, elle, disait déjà le renommage (`EntreeDatastore`) : les deux faces servent
désormais le même texte, depuis une source unique.

⚠️ Ces bancs passent par le VRAI chemin — un outil FastMCP, l'exception qu'il lève
réellement. Une doublure `validate_call` aurait menti sur le titre de l'erreur : FastMCP
rend `call[data_write]`, pas `data_write`, et c'est ce titre qu'il faut savoir lire.
"""
from __future__ import annotations

import asyncio

from fastmcp import FastMCP
from pydantic import BaseModel, ValidationError

from oto_mcp import deprecations
from oto_mcp import error_taxonomy as T

_mcp = FastMCP("banc-135")


@_mcp.tool()
def data_write(datastore: str, row: dict | None = None) -> dict:
    return {"ok": True}


def _refus(**arguments) -> Exception:
    """L'exception que FastMCP lève réellement sur cet appel — pas une doublure."""
    try:
        asyncio.run(_mcp.call_tool("data_write", arguments))
    except Exception as exc:  # noqa: BLE001 — c'est elle qu'on examine
        assert T._is_arg_validation_error(exc), "le vrai chemin doit mener au message"
        return exc
    raise AssertionError("l'appel aurait dû être refusé")


def test_un_nom_retire_dit_son_remplacant_la_valeur_et_l_outil():
    msg = T._arg_error_message(_refus(namespace="vivier", row={}))
    assert "`namespace` a été renommé `datastore`" in msg, msg
    assert "`data_write` avec `datastore='vivier'`" in msg, msg
    assert "Unexpected keyword argument" not in msg
    assert "requis absent" not in msg, "le nom neuf n'est pas un paramètre manquant"


def test_une_cle_inconnue_sous_fastmcp_3_est_NOMMEE_comme_inconnue():
    msg = T._arg_error_message(_refus(datastore="v", op="draft"))
    assert "non reconnu" in msg and "op" in msg, msg
    assert "Unexpected keyword argument" not in msg


def test_le_schema_nomme_l_outil_quand_l_erreur_vient_de_sa_signature():
    msg = T._arg_error_message(_refus(datastore="v", op="draft"))
    assert 'oto_tool_schema(name="data_write")' in msg, msg
    assert "call[" not in msg, "le titre FastMCP se lit, il ne se recopie pas"


def test_une_erreur_de_modele_ne_sert_pas_un_nom_de_classe_comme_outil():
    class DataWriteInput(BaseModel, extra="forbid"):
        datastore: str
    try:
        DataWriteInput(datastore="v", op="x")
    except ValidationError as e:
        msg = T._arg_error_message(e)
    assert "oto_tool_schema(name=…)" in msg and "DataWriteInput" not in msg, msg


# Le texte que la face capacité servait AVANT ce lot, mot pour mot.
AVANT = ("`namespace` a été renommé `datastore` le 09/09/2026 — le paramètre "
         "n'existe plus sous ce nom, et rien n'a été écrit. Rejoue le même appel "
         "avec `datastore='vivier'` : **l'adresse ne change pas** (nom du "
         "tableau, numéro, forme `slot:<nom>`), c'est la clé qui bascule. "
         "⚠️ Ne cherche pas un paramètre manquant : tu as fourni la bonne valeur "
         "sous un nom retiré.")


def test_la_face_capacite_sert_le_MEME_texte_qu_avant_ce_lot():
    assert deprecations.refus_parametre_renomme("namespace", "vivier") == AVANT
    from oto_mcp.capabilities.datastore.common import EntreeDatastore

    class _Entree(EntreeDatastore):
        datastore: str
    try:
        _Entree(namespace="vivier")
    except ValidationError as e:
        assert AVANT in str(e)
    else:
        raise AssertionError("l'entrée aurait dû refuser l'ancien nom")


def test_un_nom_jamais_renomme_n_a_pas_de_refus_dedie():
    assert deprecations.refus_parametre_renomme("op", "x") is None


def test_les_alias_de_chemin_derivent_de_la_meme_table():
    assert deprecations._DS_PARAMS == {"namespace": "datastore"}
