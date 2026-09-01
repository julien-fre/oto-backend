"""Un en-tête de tableur qui porte un point devient un nom de colonne (#684).

**Le cas qui l'impose.** Le refus des clés pointées ferme une corruption réelle — mais
il ferme aussi la porte à un fichier client parfaitement ordinaire : `N.SIREN`,
`Tel.mobile`, `contact.email` sont des en-têtes de tableur courants. Un fichier de
production en portait deux, et il devenait irrechargeable.

⚠️ **La distinction qui tient ce lot, et sans laquelle quelqu'un étendra ceci à l'appel
programmatique dans six mois :**

| | |
|---|---|
| **en-tête CSV** | une **étiquette** — chaîne humaine écrite par un tiers, à TRADUIRE |
| **clé d'appel** | une **adresse** — `{"champ.comment": …}` DÉSIGNE une annotation |

*L'import traduit déjà les types, les vides, l'encodage : traduire un point est de la
même famille.* Une adresse fautive, elle, doit lever. **Le refus protège le magasin, la
traduction protège l'ingestion — ce ne sont pas les mêmes portes.**

⚠️ **Et le CAS 1 passe AVANT.** Un en-tête qui est une adresse d'annotation valide n'est
PAS traduit : le traduire ferait de `site_web.comment` une TROISIÈME colonne
`site_web_comment`, à côté de `site_web` et de son annotation — précisément la
corruption qu'on ferme. Le store le rangera ; l'import ne s'en mêle pas.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore.errors import RowValidationError
from oto_mcp.datastore.points import traduire_les_entetes
from oto_mcp.upload_tokens import _parse_rows


def _csv(texte: str) -> bytes:
    return texte.encode("utf-8")


# ── Le CAS 1 d'abord : une adresse d'annotation n'est PAS traduite ───────────

def test_une_adresse_d_annotation_n_est_PAS_traduite_le_cas_1_passe_avant():
    """⚠️ LE témoin de l'ordre. C'est ce que faisait la première version de ce lot, et
    c'est ce qui la rendait fausse : elle fabriquait une troisième colonne."""
    lignes, traduits = _parse_rows(
        _csv("siren,site_web,site_web.comment\n1,a.fr,vérifié\n"), "csv")
    assert traduits == {}, "rien à traduire : le store la rangera dans sa couche"
    assert lignes == [{"siren": "1", "site_web": "a.fr",
                       "site_web.comment": "vérifié"}], "la clé arrive INTACTE"


def test_une_adresse_sur_une_colonne_DECLAREE_n_est_pas_traduite():
    """La colonne peut être réelle sans être dans le fichier : une extraction partielle
    exporte `site_web.comment` sans `site_web`, et le tableau la déclare."""
    schema = {"fields": [{"key": "site_web", "type": "url"}]}
    assert traduire_les_entetes(schema, ["siren", "site_web.comment"]) == {}


def test_une_annotation_sur_une_colonne_INCONNUE_est_bien_TRADUITE():
    """Le pendant : rien n'atteste `mystere` — ni le fichier, ni le schéma. Ce n'est
    donc pas une adresse, c'est une étiquette de tableur. On traduit, et on le dit."""
    assert traduire_les_entetes(None, ["mystere.comment"]) == {
        "mystere.comment": "mystere_comment"}


# ── Le CAS 2 : la traduction, et elle est DITE ───────────────────────────────

def test_un_entete_pointe_devient_une_colonne():
    lignes, traduits = _parse_rows(
        _csv("N.SIREN,Tel.mobile\n552032534,0102030405\n"), "csv")
    assert traduits == {"N.SIREN": "N_SIREN", "Tel.mobile": "Tel_mobile"}
    assert lignes == [{"N_SIREN": "552032534", "Tel_mobile": "0102030405"}]


def test_un_entete_SANS_point_n_est_pas_touche():
    """Le témoin négatif : on traduit ce qui doit l'être, rien d'autre."""
    lignes, traduits = _parse_rows(_csv("siren,raison_sociale\n1,ACME\n"), "csv")
    assert traduits == {}, "aucun renommage à signaler"
    assert lignes == [{"siren": "1", "raison_sociale": "ACME"}]


def test_le_renommage_est_DETERMINISTE():
    """⚠️ Sur un fichier client rechargé chaque mois, une traduction instable
    fabriquerait un doublon par colonne ET PAR PASSAGE. Elle ne lit que les en-têtes et
    le schéma — jamais les lignes en place, qui, elles, changent d'un mois sur l'autre."""
    entetes = ["N.SIREN", "a.b.c", "Tel.mobile"]
    assert traduire_les_entetes(None, entetes) == traduire_les_entetes(None, entetes)
    assert traduire_les_entetes(None, entetes)["a.b.c"] == "a_b_c"


def test_un_suffixe_qui_RESSEMBLE_a_une_annotation_sans_en_etre_une():
    """`a.b.comment` : le suffixe est une annotation connue, mais `a.b` ne peut être
    aucune colonne — un nom de colonne ne porte jamais de point. Donc : étiquette."""
    assert traduire_les_entetes(None, ["a.b", "a.b.comment"]) == {
        "a.b": "a_b", "a.b.comment": "a_b_comment"}


# ── Le CAS 4 : la collision se refuse, on ne fusionne jamais ─────────────────

def test_collision_avec_un_AUTRE_ENTETE_du_fichier():
    """⚠️ Le vrai risque du renommage. `N.SIREN` et `N_SIREN` dans le même fichier sont
    DEUX colonnes ; les fusionner en ferait perdre une en silence."""
    with pytest.raises(RowValidationError) as e:
        traduire_les_entetes(None, ["N.SIREN", "N_SIREN"])
    msg = str(e.value)
    assert "N.SIREN" in msg and "N_SIREN" in msg, "le refus nomme LES DEUX"


def test_collision_avec_une_colonne_DECLAREE_au_schema():
    """La cible peut être prise sans être dans le fichier : l'import écrirait alors
    dans une colonne du tableau que le client n'a jamais visée."""
    with pytest.raises(RowValidationError):
        traduire_les_entetes({"fields": [{"key": "N_SIREN", "type": "text"}]},
                             ["N.SIREN"])


def test_deux_entetes_pointes_qui_visent_la_MEME_cible():
    with pytest.raises(RowValidationError) as e:
        traduire_les_entetes(None, ["a.b", "a_b"])
    assert "a.b" in str(e.value) and "a_b" in str(e.value)


def test_la_collision_remonte_en_400_nomme():
    """Un refus d'INGESTION, pas de schéma : le client reçoit un code actionnable."""
    from oto_mcp.upload_tokens import UploadError
    with pytest.raises(UploadError) as e:
        _parse_rows(_csv("N.SIREN,N_SIREN\n1,2\n"), "csv")
    assert e.value.code == "entete_en_collision"


# ── Le NDJSON ne traduit RIEN, et c'est un choix ─────────────────────────────

def test_le_NDJSON_ne_traduit_RIEN():
    """⚠️ Il porte des CLÉS, pas des étiquettes. Une clé pointée y est une adresse : le
    store la RANGE si elle en désigne une, la REFUSE sinon. Traduire ici masquerait un
    bug d'appelant au lieu de le lui dire."""
    lignes, traduits = _parse_rows(b'{"champ.comment": "x"}\n', "ndjson")
    assert traduits == {}
    assert lignes == [{"champ.comment": "x"}], "la clé arrive INTACTE au store"
