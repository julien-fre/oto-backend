"""Le NUMÉRO d'un tableau est une adresse valide — sur TOUTES les surfaces (07/09/2026).

L'aller-retour que ce banc ferme, et il était rompu en production :

1. `data_get_schema` rend `ns_id: 609` — un NOMBRE dans le JSON ;
2. la description de `data_rows` dit « the table's NUMBER (`ns_id`) — **the form to
   use** » ;
3. le modèle repasse `609` tel quel à `data_write`… qui le REFUSE :
   `Input should be a valid string [type=string_type, input_value=609, input_type=int]`.

⚠️ **Ce n'était pas une régression de code.** L'annotation `datastore: str` de
`data_write` datait du 18/06/2026 et n'avait jamais gêné personne : rien ne disait à
l'agent de passer un nombre. C'est la moitié SERVIE de l'aller-retour, posée le
07/09/2026, qui a rendu l'autre moitié fausse. **Un contrat se casse en devenant vrai
d'un seul côté.**

Coût mesuré par une campagne sur dix lignes, juste après la mise en production :
**7 écritures refusées sur 13 (54 %)**, 3 lignes réservées et travaillées puis jamais
écrites, et le coût par fiche de 7,1 à 11,0 tours (+55 %). Le modèle se corrigeait au
tour suivant en repassant le nom — donc le défaut ne bloquait pas, il TAXAIT, ce qui
est la forme la plus difficile à voir.

⚠️ **Et il n'apparaissait dans aucun journal de travail** — seulement dans le bilan de
flotte. Qui auditait les journaux voyait des lignes qui n'avancent pas, sans cause.

**Ce banc vise la CLASSE, pas le cas** : il énumère les surfaces et exige que chacune
accepte le nombre. Une surface neuve qui typerait son adresse en texte seul rougit ici,
sans que personne ait à y penser.
"""
from __future__ import annotations

import ast
import inspect
import pathlib

import pytest
from pydantic import create_model

from oto_mcp.datastore.identite import Adresse

#: Le fichier des outils MCP — la face que les agents appellent.
_TOOLS = pathlib.Path(inspect.getfile(__import__("oto_mcp.tools.datastore",
                                                 fromlist=["x"])))


def _outils_et_leur_adresse() -> dict:
    """`{nom d'outil: l'annotation de son paramètre `datastore`}`, lue au SOURCE.

    Par AST plutôt que par introspection : les outils sont enregistrés à l'intérieur
    d'une fonction d'installation, et rien ne les expose une fois le serveur monté."""
    arbre = ast.parse(_TOOLS.read_text(encoding="utf-8"))
    out = {}
    for n in ast.walk(arbre):
        if not isinstance(n, ast.FunctionDef) or not n.name.startswith("data_"):
            continue
        for arg in list(n.args.args) + list(n.args.kwonlyargs):
            if arg.arg == "datastore" and arg.annotation is not None:
                out[n.name] = ast.unparse(arg.annotation)
    return out


def test_le_banc_voit_bien_les_outils():
    """La garde du banc lui-même : une sonde qui ne trouve plus rien passerait au vert
    en ne surveillant plus personne — le pire état d'un contrôle."""
    outils = _outils_et_leur_adresse()
    assert len(outils) >= 12, f"sonde aveugle : {len(outils)} outils vus"
    assert "data_write" in outils and "data_rows" in outils


def test_chaque_outil_accepte_le_NUMERO_comme_adresse():
    """Le cœur. `Adresse` (ou son option) partout — jamais `str` nu."""
    fautifs = {nom: ann for nom, ann in _outils_et_leur_adresse().items()
               if "Adresse" not in ann}

    assert not fautifs, (
        "ces outils refusent le numéro que la plateforme leur sert : "
        + ", ".join(f"{n} ({a})" for n, a in sorted(fautifs.items())))


@pytest.mark.parametrize("recu,attendu", [
    (609, "609"),                       # LE cas : un nombre JSON
    ("609", "609"),                     # le même, déjà en texte
    ("edition-vivier", "edition-vivier"),   # un nom continue de résoudre
])
def test_la_coercition_normalise_vers_le_texte(recu, attendu):
    """Un seul chemin de résolution en aval : on normalise à l'entrée plutôt que
    d'apprendre à chaque appelant à gérer deux formes."""
    M = create_model("call", datastore=(Adresse, ...))

    obtenu = M(datastore=recu).datastore

    assert obtenu == attendu
    assert isinstance(obtenu, str), "l'aval manipule du texte, et lui seul"


def test_un_BOOLEEN_ne_devient_pas_un_tableau():
    """`bool` est un `int` en Python. Sans cette borne, `datastore=True` deviendrait
    le tableau « True » — une coercition silencieuse vers une adresse inventée."""
    M = create_model("call", datastore=(Adresse, ...))

    with pytest.raises(Exception):
        M(datastore=True)


def test_les_ENTREES_REST_acceptent_le_numero_aussi():
    """La face REST porte les mêmes adresses : un correctif qui n'en couvrirait qu'une
    recréerait l'écart d'aujourd'hui, une surface plus loin."""
    import oto_mcp.capabilities.datastore.rows as R

    modeles = [getattr(R, n) for n in dir(R) if n.endswith("Input")]
    portants = [M for M in modeles
                if getattr(M, "model_fields", {}).get("datastore") is not None]
    assert portants, "aucun modèle d'entrée à éprouver : la sonde est aveugle"

    for M in portants:
        kw = {"datastore": 609}
        for cle, champ in M.model_fields.items():
            if cle != "datastore" and champ.is_required():
                annotation = str(champ.annotation)
                kw[cle] = ({} if "dict" in annotation
                           else [] if "list" in annotation else "x")
        assert M(**kw).datastore == "609", f"{M.__name__} refuse le numéro"
