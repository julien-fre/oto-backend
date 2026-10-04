"""Les clés de schéma : une déclaration par NIVEAU, et ses gardes (oto#56, 01/10/2026).

Le validateur acceptait n'importe quelle clé sur une colonne. `editable` passe,
`zorglub` passe, et surtout `read_only` passe — une faute de frappe sur un attribut de
garde est silencieuse **et** elle désarme la garde. Depuis le 01/10/2026 le vocabulaire
est FERMÉ : chacun des cinq niveaux d'un schéma (tête, colonne, sous-champ, élément de
liste, bloc `lifecycle`) déclare ce qu'il admet, et le reste est refusé.

⚠️ **Ce banc existe pour que le refus ne tombe jamais sur une clé LUE.** La liste
dérivée des `.get()` du code a perdu son rôle de fondement (elle ne voit pas les
niveaux : `strict` posé sur une colonne passait, lu en tête) ; elle garde la
déclaration, dans les deux sens et à chaque niveau.
"""
from __future__ import annotations

import pytest

from oto_mcp.datastore import schema as S
from oto_mcp.datastore import schema_keys as K


class _Espion(dict):
    """Un nœud qui note ce qu'on lui demande — `get`, `[]` et `in` sont relevés."""

    def __init__(self, source, vues):
        super().__init__(source)
        self._vues = vues

    def get(self, cle, defaut=None):
        self._vues.add(cle)
        return super().get(cle, defaut)

    def __getitem__(self, cle):
        self._vues.add(cle)
        return super().__getitem__(cle)

    def __contains__(self, cle):
        self._vues.add(cle)
        return super().__contains__(cle)


#: Une valeur PLAUSIBLE par clé, pour qu'une sonde atteigne la branche qui la lit.
#: ⚠️ Une garde par échantillon ne garde que son échantillon : les sondes sont
#: DÉRIVÉES de la déclaration (une par clé admise, à chaque niveau), cette table ne
#: fait que leur donner une forme qui ne casse pas avant la lecture.
_VALEURS = {"readonly": True, "role": "status", "required": True,
            "max_items": 3, "options": ["a"], "max_length": 5, "pattern": "^a",
            "required_when": {"x": "1"}, "display": "title",
            "lifecycle": {"states": ["a"], "transitions": {}},
            "of": {"type": "text"}, "fields": [{"key": "z"}],
            "required_layers": ["comment"], "agent_access": "read",
            "formula": "1", "meta": {"m": 1}, "type": "text",
            "states": ["a", "b"], "transitions": {"a": ["b"]}, "terminal": ["b"],
            "max_claims": 2, "abandon_state": "b", "claimable": {"x": "1"},
            "labels": {"a": "A"}, "unknown_columns": "report", "new_rows": "reject",
            "unknown_columns": "report"}

_TYPES = ({"type": "text"}, {"type": "number"}, {"type": "list", "of": {"type": "text"}},
          {"type": "object", "fields": [{"key": "z"}]},
          {"type": "enum", "options": ["a"]})


def _base(cle: str) -> dict:
    return ({"type": "list", "of": {"type": "text"}} if cle in ("max_items", "of")
            else {"type": "object"} if cle == "fields"
            else {"type": "enum"} if cle == "options"
            else {"type": "formula"} if cle == "formula" else {"type": "text"})


def _sondes(niveau: str):
    yield {}
    yield from _TYPES
    for cle in sorted(K.ADMISES[niveau] - {"key"}):
        yield {**_base(cle), cle: _VALEURS.get(cle, "x")}


def _lues(niveau: str) -> set[str]:
    """Ce que `validate_schema_def` lit sur un nœud du `niveau` donné."""
    vues: set = set()

    def _essayer(schema):
        try:
            S.validate_schema_def(schema)
        except Exception:  # noqa: BLE001 — une sonde qui casse n'apprend rien
            pass

    if niveau == "tete":
        for cle in sorted(K.ADMISES["tete"]):
            _essayer(_Espion({"key": "x", "fields": [{"key": "x"}],
                              cle: _VALEURS.get(cle, "x")}, vues))
    elif niveau == "cycle":
        for cle in sorted(K.ADMISES["cycle"]):
            lc = {"states": ["a", "b"], "terminal": ["b"], cle: _VALEURS.get(cle, "x")}
            _essayer({"fields": [{"key": "x"},
                                 {"key": "s", "lifecycle": _Espion(lc, vues)}]})
    else:
        for sonde in _sondes(niveau):
            for nom in (("colonne", "colonne.comment") if niveau == "champ"
                        else ("sous",)):
                noeud = _Espion({"key": nom, **sonde}, vues)
                _essayer({"fields": [noeud] if niveau == "champ" else [
                    {"key": "colonne"},
                    {"key": "o", "type": "object", "fields": [noeud]}
                    if niveau == "sous_champ" else
                    {"key": "l", "type": "list", "of": noeud}]})
    return {v for v in vues if isinstance(v, str)}


@pytest.mark.parametrize("niveau", list(K.NIVEAUX))
def test_tout_ce_que_le_validateur_LIT_est_admis_a_ce_niveau(niveau):
    """Le premier sens, niveau par niveau. Une clé lue et non admise serait REFUSÉE à
    la pose alors que le code s'en sert — le refus casserait ce qu'il protège.

    ⚠️ **Ce que ce test ne garde PAS** : il observe le validateur en l'exerçant. Une
    clé lue sur une branche qu'aucune sonde n'atteint reste invisible ; les sondes sont
    dérivées de la déclaration, donc le trou ne peut s'ouvrir que sur une clé qu'on
    lirait sans l'avoir jamais déclarée."""
    hors = _lues(niveau) - K.ADMISES[niveau]
    assert not hors, (
        f"au niveau `{niveau}`, le validateur lit des clés que ce niveau n'admet pas : "
        f"{sorted(hors)}. Déclare-les dans `datastore/schema_keys.py`, sinon le refus "
        "des clés inconnues les rejettera.")


def test_le_validateur_DERIVE_ses_crans_de_la_declaration():
    """Premier client, pas documentation : si les deux divergeaient, la déclaration
    ne serait qu'un commentaire."""
    assert S._COLUMN_ONLY_KEYS == K.COLONNE_SEULEMENT


def test_le_sous_champ_est_DERIVE_de_la_colonne():
    """Un attribut ajouté à une colonne descend seul — sauf ceux qui ne se lisent qu'au
    premier niveau, et qui le disent."""
    assert K.ADMISES["sous_champ"] == K.ADMISES["champ"] - set(K.PREMIER_NIVEAU_SEULEMENT)


@pytest.mark.parametrize("niveau", list(K.NIVEAUX))
def test_tout_ce_qui_est_declare_APPLIQUE_est_reellement_lu(niveau):
    """⚠️ Le sens que personne ne pose jamais : on vérifie qu'on n'oublie rien, pas
    qu'on ne PROMET rien en trop. `enum` était déclarée « lue par le validateur » alors
    que le code ne la lit nulle part. Exact, pas échantillonné : la dérivation
    surestime, donc une clé déclarée et absente du dérivé n'est lue nulle part."""
    promises = {c.nom for c in K.NIVEAUX[niveau] if "validateur" in c.lecteurs}
    fantomes = sorted(promises - S.interpreted_keys())
    assert not fantomes, (
        f"au niveau `{niveau}`, ces clés sont déclarées appliquées par le validateur, "
        f"qui ne les lit nulle part : {fantomes}.")


def test_tout_ce_que_le_validateur_APPLIQUE_est_admis_quelque_part():
    """Le troisième sens : ce que le serveur APPLIQUE (`enforced`) doit être admis à un
    niveau — sinon on refuserait la clé même qu'on annonce faire respecter."""
    admises = set().union(*K.ADMISES.values())
    assert not set(S.enforced_keys()) - admises, sorted(set(S.enforced_keys()) - admises)


def test_une_cle_sans_lecteur_DOIT_dire_qu_elle_est_sans_effet():
    """Un lecteur vide est permis (`meta`), à une condition : que la clé DISE qu'elle
    est sans effet, puisque c'est cette description qui est servie à qui écrit."""
    for niveau, cles in K.NIVEAUX.items():
        for c in cles:
            assert set(c.lecteurs) <= {"validateur", "front"}, (niveau, c.nom)
            assert c.quoi.strip(), (niveau, c.nom)
            if not c.lecteurs:
                assert "SANS EFFET" in c.quoi.upper(), (niveau, c.nom, c.quoi)


def test_meta_est_admis_a_chaque_niveau():
    for niveau, admises in K.ADMISES.items():
        assert K.META in admises, niveau


def test_les_cles_RETIREES_ne_sont_admises_nulle_part():
    """`origine` (sans lecteur depuis le 08/09) et les quatre textes d'aide repliés
    dans `description` le 01/10/2026."""
    admises = set().union(*K.ADMISES.values())
    assert not (set(K.CLES_RETIREES) | set(K.TEXTES_D_AIDE)) & admises
    assert all("description" in K.ADMISES[n] for n in ("champ", "sous_champ", "element"))


def test_chaque_correction_proposee_pointe_une_cle_admise():
    admises = set().union(*K.ADMISES.values())
    for faute, vraie in K.FAUTES_CONNUES.items():
        assert vraie in admises and faute not in admises, (faute, vraie)


def test_la_declaration_est_SERVIE_aux_cinq_niveaux():
    servie = K.servie()
    assert {e["key"] for e in servie["keys"]} == K.ADMISES["champ"]
    assert {K.NIVEAUX_SERVIS[n] for n in K.NIVEAUX} == set(servie["levels"])
    for n, entrees in servie["levels"].items():
        interne = next(i for i, s in K.NIVEAUX_SERVIS.items() if s == n)
        assert {e["key"] for e in entrees} == K.ADMISES[interne]
        assert all(e["what"] for e in entrees)


# ── le cas fondateur, désormais refusé ───────────────────────────────────────

def test_une_faute_de_frappe_sur_un_cran_est_REFUSEE_et_nommee():
    """`read_only` désarmait le verrou en silence."""
    errs = S.validate_schema_def({"fields": [{"key": "k", "type": "text",
                                              "read_only": True}]})
    assert len(errs) == 1 and "voulais-tu `readonly` ?" in errs[0], errs


def test_le_cas_fondateur_est_refuse():
    """`editable` n'existe nulle part — l'agent l'avait découvert en comparant deux
    refus strictement identiques."""
    errs = S.validate_schema_def({"fields": [{"key": "k", "type": "text",
                                              "readonly": True, "editable": True}]})
    assert len(errs) == 1 and "`editable`" in errs[0], errs


def test_les_attributs_du_FRONT_sont_admis():
    """La moitié qui a failli couler le premier lot : invisibles au validateur, lus
    par l'écran. `label` est le plus lu de tous."""
    champs = [{"key": "k", "type": "text", "label": "Nom", "description": "d",
               "hidden": True, "width": "half", "role": "badge"}]
    assert S.validate_schema_def({"fields": champs}) == []


def test_enum_a_cote_d_options_est_refusee_vers_options():
    """⚠️ Le cas qui a laissé 504 valeurs libres : `enum` posé à côté d'`options`. Le
    refus nomme `enum` et envoie vers `options` — jamais l'inverse."""
    errs = S.validate_schema_def({"fields": [{"key": "s", "type": "enum",
                                              "options": ["a"], "enum": ["a"]}]})
    assert len(errs) == 1 and errs[0].startswith("fields.s : `enum`"), errs
    assert "voulais-tu `options` ?" in errs[0]
