"""Deux gardes qui ont l'air de mordre, et ne mordent pas là où leur nom le dit.

Signalées le 08/09/2026 par une campagne qui les avait posées **en croyant fermer une
porte**, et qui les a mesurées avant de le dire. Les deux font exactement ce qu'elles
annoncent ; c'est ce que leur nom laisse croire qui est faux. **C'est la pire forme de
garde** — elle produit une confiance qu'elle ne soutient pas, et on cesse de chercher
ailleurs.

Le contexte donne leur poids. Cette campagne a mesuré que **44 de ses 122 travaux d'une
passe n'avaient appelé AUCUN outil de recherche et avaient écrit une note qui en
décrivait cinq** — le modèle narre au passé des appels qu'il n'a pas faits, jusqu'à
citer un paramètre de requête pour une recherche jamais lancée. L'interdiction
textuelle existait dans la procédure et n'a pas tenu. La parade décidée est d'exiger un
jeton de preuve dans la note (`[serper:12]`) et de REFUSER sans lui.

Les deux trous ci-dessous vident cette parade de la moitié de son effet — et l'agent
qui n'a rien cherché est justement celui qui peut ne rien écrire.

⚠️ **On AVERTIT, on ne refuse pas.** Les deux déclarations sont légitimes et
largement posées en production ; les refuser rétroactivement transformerait des
schémas valides en schémas cassés. Ce qui nuisait était le silence, pas la garde.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore import non_applique as na
from oto_mcp.datastore import schema as dsv2


# ── ① Un motif contraint ce qui est ÉCRIT, jamais le fait d'écrire ───────────

MOTIF = r"\[(serper|bodacc):[0-9]+\]"


@pytest.mark.parametrize("nom,ligne,ecrits,refuse", [
    ("une valeur conforme",  {"notes": "vu [serper:12]"}, {"notes"}, False),
    ("une valeur fautive",   {"notes": "vu au registre"}, {"notes"}, True),
    ("⚠️ une valeur VIDE",   {"notes": ""},               {"notes"}, False),
    ("⚠️ un champ ABSENT",   {},                          set(),     False),
])
def test_le_motif_laisse_passer_le_vide_et_l_absence(nom, ligne, ecrits, refuse):
    """Le fait, mesuré. Ce n'est pas un défaut du motif — c'est ce qu'un motif fait,
    et personne ne le disait."""
    sch = {"fields": [{"key": "notes", "type": "text",
                       "max_length": 600, "pattern": MOTIF}]}

    errs = dsv2.validate_row(sch, ligne, written=ecrits)

    assert bool(errs) is refuse, f"{nom} : attendu {'refusé' if refuse else 'passé'}"


def test_required_when_ferme_le_trou_SANS_bloquer_les_lignes_en_amont():
    """Le remède, et pourquoi c'est celui-là. `required: true` fermerait aussi le
    trou, mais refuserait les lignes qui n'ont pas encore atteint l'étape — sur une
    file de travail où les lignes avancent par paliers, ça bloque tout le monde au
    premier palier."""
    sch = {"fields": [
        {"key": "passe", "type": "text"},
        {"key": "notes", "type": "text", "max_length": 600, "pattern": MOTIF,
         "required_when": {"passe": ["F"]}},
    ]}

    assert dsv2.validate_row(sch, {"passe": "F", "notes": ""}, written={"passe", "notes"})
    assert dsv2.validate_row(sch, {"passe": "F"}, written={"passe"})
    assert not dsv2.validate_row(sch, {"passe": "C"}, written={"passe"}), (
        "une ligne qui n'est pas encore à l'étape ne doit pas être bloquée")


def test_le_releve_NOMME_les_motifs_sans_obligation():
    sch = {"fields": [
        {"key": "notes", "type": "text", "max_length": 600, "pattern": MOTIF},
        {"key": "garde", "type": "text", "max_length": 600, "pattern": MOTIF,
         "required_when": {"passe": ["F"]}},
        {"key": "libre", "type": "text"},
    ]}

    assert na.motif_sans_obligation(sch) == ["notes"]
    phrase = na.motif_sans_obligation_warning(["notes"])
    assert "never the fact of writing" in phrase
    assert "required_when" in phrase, "la phrase doit porter le geste, pas le constat"


def test_le_releve_NE_CRIE_PAS_sur_le_remede_qu_il_preconise():
    """⚠️ La borne, et elle vaut le relevé entier.

    Une couche déclarée comme un champ à part entière — `{"key": "sourcee.comment",
    "pattern": …}` — est EXACTEMENT ce que le second relevé recommande. La signaler
    ferait crier l'avertissement sur son propre remède ; et un avertissement qui crie
    à tort est celui qu'on apprend à ignorer, donc celui qui ruine les vrais."""
    sch = {"fields": [
        {"key": "sourcee", "type": "text", "required_layers": ["comment"]},
        {"key": "sourcee.comment", "type": "text", "max_length": 300,
         "pattern": "registre"},
    ]}

    assert na.motif_sans_obligation(sch) == []


# ── ② `required_layers` garde qu'une couche EXISTE, jamais son contenu ───────

def test_une_provenance_creuse_satisfait_l_exigence():
    """Le fait : « vu quelque part » vaut une référence de registre. Mesuré sur un
    tableau de production, quatre colonnes exigeaient la provenance et aucune
    n'exigeait qu'elle dise quoi que ce soit."""
    sch = {"fields": [{"key": "qualification", "type": "text",
                       "required_layers": ["comment"]}]}

    ligne = {"qualification": {"valeur": "cible", "comment": "vu quelque part"}}

    assert not dsv2.validate_row(sch, ligne, written={"qualification"})


def test_le_releve_nomme_la_couche_sans_forme():
    sch = {"fields": [{"key": "qualification", "type": "text",
                       "required_layers": ["comment"]}]}

    assert na.couche_exigee_sans_forme(sch) == ["qualification.comment"]
    phrase = na.couche_exigee_sans_forme_warning(["qualification.comment"])
    assert "never what it contains" in phrase
    assert '"key": "<column>.comment"' in phrase, (
        "la phrase doit montrer la FORME, pas seulement nommer le manque")


def test_une_couche_DEJA_contrainte_ne_se_signale_plus():
    """La contre-épreuve : une fois le remède appliqué, l'avertissement se tait. Sans
    ce cas, rien ne dirait que le conseil qu'il donne le fait effectivement taire."""
    sch = {"fields": [
        {"key": "sourcee", "type": "text", "required_layers": ["comment"]},
        {"key": "sourcee.comment", "type": "text", "max_length": 300,
         "pattern": "registre"},
    ]}

    assert na.couche_exigee_sans_forme(sch) == []


def test_les_deux_releves_se_TAISENT_sur_un_schema_ordinaire():
    """Pas de clé parasite dans la réponse d'un tableau qui ne déclare ni motif ni
    couche exigée — c'est-à-dire la grande majorité."""
    sch = {"fields": [{"key": "societe", "type": "text"},
                      {"key": "statut", "type": "enum", "options": ["a", "b"]}]}

    assert na.motif_sans_obligation(sch) == []
    assert na.couche_exigee_sans_forme(sch) == []
    assert na.motif_sans_obligation_warning([]) is None
    assert na.couche_exigee_sans_forme_warning([]) is None


# ── « inerte » était plus catégorique que ce que je peux savoir ──────────────
# Signalé le 08/09/2026 par le consommateur qui affiche ces tableaux, et il avait
# raison : mon message disait « cette déclaration-ci est simplement inerte » d'un
# `lifecycle` que SON serveur et SON écran lisent.
#
# ⚠️ Elle est inerte POUR OTO. Elle était vivante pour lui. Sur la foi de ce mot, le
# bloc a été retiré ce matin sur deux tableaux, et il a fallu l'arrêter en route.
# Quatre sessions ont tourné une journée autour de six lignes.
#
# La phrase qui manquait à tout le monde est « oto ne lit pas ce bloc, scout le lit »,
# et **aucun des deux ne pouvait la dire seul** : je ne sais pas qui lit en aval, et
# lui ne lisait pas mon avertissement. La même prudence était DÉJÀ écrite pour les
# clés libres (« un consommateur peut parfaitement les lire ») — je ne l'avais pas
# étendue ici, alors que le risque y est plus grand : ce bloc a l'air d'un mécanisme.

def test_l_avertissement_ne_dit_PAS_inerte_tout_court():
    from oto_mcp.datastore import schema as dsv2

    sch = {"fields": [
        {"key": "statut", "role": "status",
         "lifecycle": {"states": ["a", "b"], "terminal": ["b"], "max_claims": 3,
                       "abandon_state": "b", "claimable": ["a"]}},
        {"key": "suivi", "lifecycle": {"states": ["x"]}}]}
    phrase = dsv2.lifecycle_hors_statut_warning(dsv2.lifecycle_hors_statut(sch), sch)

    # La réserve est dans le return, donc servie quelle que soit la branche — c'est
    # ce qui compte : elle ne doit pas dépendre de l'état du reste du schéma.
    assert "read by nobody" in phrase, "le message doit nommer le consommateur en aval"
    assert "Do not remove it on the sole basis of this message" in phrase
    assert "ask whoever displays this table" in phrase


def test_la_branche_sans_manque_ne_dit_pas_INERTE_tout_court():
    """L'autre branche — celle où tous les crans sont par ailleurs déclarés — disait
    « simplement inerte ». C'est le mot exact sur lequel un consommateur a failli
    retirer un bloc que son propre serveur lisait."""
    import inspect

    from oto_mcp.datastore import non_applique

    src = inspect.getsource(non_applique)
    assert "has no effect FOR OTO" in src
    assert "est simplement inerte" not in src and "simply inert" not in src
