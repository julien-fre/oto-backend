"""#515 — le refus « ligne réservée » MÈNE par le `_run_id` manquant.

Deux sessions différentes, la même semaine, ont lu « réservée par « X » jusqu'à HH:MM »
comme la preuve d'une réservation non atomique — alors que le verrou fonctionnait et
que les titulaires étaient strictement successifs. Le texte décrivait l'ÉTAT DU MONDE
(le titulaire du moment) et laissait déduire la faute. Le refus est le moment de la
faute : une phrase lue là pèse plus que la description de l'outil, qui pèse plus que la
consigne.

Formulation retenue par Alexis le 29/08/2026 — verrouillée ici mot pour mot sur ses
trois contraintes :
- **l'ordre** : la moitié `_run_id` d'abord (cas le plus fréquent, 31 refus sur 100) ;
- **jamais le run — ni le libellé — d'un tiers** : l'échéance suffit ;
- **le texte principal**, pas un `details.hint` (#550).

Le code de refus (`row_locked`, 409) ne change pas.
"""
from __future__ import annotations

from oto_mcp.capabilities.datastore.rows import _write_refusal
from oto_mcp.datastore import errors as ds_errors

_ECHEANCE = "2026-09-05T18:00Z"


def _refus(**kw) -> ds_errors.RowLocked:
    return ds_errors.RowLocked("r1", "worker-42", _ECHEANCE, claimed_run="run-tiers-9f3a", **kw)


def test_le_message_mene_par_le_run_id_puis_dit_l_autre_cas():
    msg = str(_refus())
    run_id = msg.index("ton appel ne porte pas de `_run_id`")
    autre = msg.index("tenue par un autre travail")
    assert msg.index("Si cette ligne est la tienne") < run_id < autre, (
        "les deux cas dans l'ordre du fréquent : la moitié `_run_id` d'abord — " + msg)
    assert "obligatoire sur ce chemin" in msg and "prouve ta réservation" in msg, msg


def test_l_echeance_est_dite_et_le_geste_de_sortie_reste():
    msg = str(_refus())
    assert f"jusqu'à {_ECHEANCE}" in msg, "l'échéance suffit à dire quand attendre"
    assert "attends" in msg and "libère-la" in msg and "data_release" in msg, msg


def test_le_message_ne_nomme_ni_le_run_ni_le_libelle_d_un_tiers():
    """⚠️ `_run_id` n'autorise rien, il NOMME : le publier ferait du verrou une
    étiquette. Le libellé du titulaire (`worker`) ne sert pas non plus le lecteur —
    l'échéance suffit, la formulation retenue ne le reprend pas."""
    msg = str(_refus())
    assert "run-tiers-9f3a" not in msg
    assert "worker-42" not in msg
    assert "réservée par" not in msg, "l'ancienne phrase, qui décrivait l'état du monde"


def test_le_refus_est_le_texte_principal_pas_un_hint():
    """La même phrase sort à la face REST (`message`) et sert de `motif` au lot."""
    e = _refus()
    refus = _write_refusal(e)
    assert refus.status == 409 and refus.code == "row_locked", "le code ne change pas"
    assert "ton appel ne porte pas de `_run_id`" in refus.message
    assert e.motif in refus.message


def test_dans_un_lot_le_message_garde_sa_designation_de_ligne():
    e = _refus(row="ligne 1/1 du lot (siren=55111000)")
    assert str(e).startswith("ligne 1/1 du lot (siren=55111000) : ")
    assert "ton appel ne porte pas de `_run_id`" in str(e)
