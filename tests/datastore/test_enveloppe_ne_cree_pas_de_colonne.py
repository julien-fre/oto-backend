"""Une ligne ENVELOPPÉE fabriquait une colonne parasite, en silence (#117).

Sur les routes d'écriture, **le corps EST la ligne**. Qui suit la convention habituelle
l'enveloppe dans un objet qui la nomme — `{"row": {…}}` — et crée une colonne réellement
appelée `row`, contenant toute la ligne.

⚠️ **Aucune garde ne mordait, et la réponse était indiscernable d'une écriture réussie.**
Mesuré : sur un tableau SOUPLE — le régime par défaut — même le relevé hors-schéma reste
muet (`off_schema_keys` rend `[]` hors mode strict). Le piège a produit deux verdicts
faux au cours d'une seule mesure : l'agent croit avoir écrit, et rien ne le contredit.

**Le critère est l'ABSENCE TOTALE de correspondance, pas la présence d'une clé inconnue.**
Ajouter une colonne libre à un tableau schématisé reste un droit du contrat 0016 ;
refuser cela durcirait un contrat servi. Ce qui n'a aucun sens, c'est une ligne qui ne
touche **pas une seule** des colonnes déclarées.

⚠️ **Mesuré avant d'être posé, sur la base servie** : 67 980 lignes de tableaux à
colonnes déclarées, **une seule** ne correspondait à rien — sur un tableau de 1 986
lignes qui déclare huit colonnes. Le critère ne décrit donc aucun régime normal ; il ne
nomme que l'accident. La garde est gratuite, comme celle des états : elle ferme la porte
avant qu'on la pousse.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore.controles import ControlesMixin
from oto_mcp.datastore.hors_schema import enveloppe_probable

SCHEMA = {"fields": [{"key": "siren", "type": "text"}, {"key": "nom", "type": "text"}]}


class _Store(ControlesMixin):
    off_schema = set(); off_format_preavis = set(); off_geles = {}; off_notices = set()
    off_erased = []; off_rejected = []; off_ignored = []; off_non_rapprochables = {}
    off_non_declarees = set()
    dernier_tableau = None

    def _trace(self, *a, **k):
        pass


def test_l_enveloppe_est_REFUSEE_a_la_creation():
    with pytest.raises(ValueError) as e:
        _Store()._check_row(SCHEMA, {"row": {"siren": "1"}}, creation=True)
    m = str(e.value)
    assert "rien n'a été écrit" in m, "l'appelant doit savoir où il en est"
    assert "Le corps EST la ligne" in m, "la cause, en une phrase"
    assert "`siren`" in m, "et les colonnes réellement disponibles"
    assert "data_set_schema" in m, "la sortie si ces clés sont vraiment ses données"


def test_un_PATCH_n_est_PAS_touche():
    """⚠️ La borne qui rend la garde juste. Un patch vise une ligne EXISTANTE par son
    `id` et peut légitimement ne toucher qu'une colonne libre — l'y refuser casserait
    un geste courant pour attraper un accident qui n'arrive qu'à la création."""
    _Store()._check_row(SCHEMA, {"row": {"siren": "1"}})      # ne lève pas


def test_une_colonne_LIBRE_reste_un_droit():
    """Contrat 0016 : une clé non déclarée crée une colonne libre, et c'est ce qui
    permet d'explorer un tableau avant de le typer. La garde ne vise que l'absence
    TOTALE de correspondance."""
    _Store()._check_row(SCHEMA, {"siren": "1", "libre": "x"}, creation=True)


def test_un_tableau_LIBRE_n_est_jamais_juge():
    """Sans colonne déclarée, il n'y a rien à quoi comparer : inventer un refus sur une
    absence d'information serait pire que le silence."""
    assert enveloppe_probable({"fields": []}, {"quoi": "x"}) is False
    _Store()._check_row({"fields": []}, {"quoi": "x"}, creation=True)


@pytest.mark.parametrize("data", [{}, {"_id": "x"}, None, "pas un dict"])
def test_rien_a_juger_ne_refuse_rien(data):
    """Ligne vide, méta seules, données non exploitables : la primitive rend `False`
    plutôt que de lever — un contrôle de forme ne casse pas une écriture."""
    assert enveloppe_probable(SCHEMA, data) is False


def test_les_DEUX_chemins_de_creation_declarent_leur_mode():
    """⚠️ La garde contre le défaut qui revient : une garde posée sur les chemins
    auxquels on pense, absente de celui qu'on croyait couvert. `append_row` et le LOT
    créent tous deux des lignes — le fichier d'écriture porte lui-même la trace d'un
    oubli de ce genre (« les trois autres étaient branchés ; la création unitaire,
    non »)."""
    import ast
    import inspect

    from oto_mcp.datastore import ecriture, lots

    for mod in (ecriture, lots):
        trouve = False
        for n in ast.walk(ast.parse(inspect.getsource(mod))):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "_check_row"):
                kw = {k.arg: k.value for k in n.keywords}
                v = kw.get("creation")
                if isinstance(v, ast.Constant) and v.value is True:
                    trouve = True
        assert trouve, (
            f"`{mod.__name__}` ne déclare plus `creation=True` : l'enveloppe y "
            f"repasserait en silence")
