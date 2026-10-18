"""Le refus doit NOMMER le renommage, pas décrire un champ manquant.

`namespace` est devenu `datastore` le 09/09/2026 sur les douze outils `data_*`. Le
paramètre n'existe plus sous son ancien nom — mais pydantic **ignore silencieusement**
une clé inconnue (`extra="ignore"`, le défaut), si bien que l'appel échouait un cran
plus loin sur :

    datastore: Field required

⚠️ **Le verdict était juste et le message envoyait chercher au mauvais endroit.** Un
agent qui lit « champ requis manquant » cherche ce qu'il a OUBLIÉ ; il ne peut pas
deviner qu'il a fourni la bonne valeur sous un nom retiré. C'est exactement la forme
qui, sur un autre refus le 09/09, a fait brûler 60 appels à dix agents persuadés que
leur APPEL était fautif — et les dix se sont conclus « terminé » sans une écriture.

On ne change pas le verdict : l'appel échouait, il échoue toujours. On change ce qu'il
DIT.

⚠️ **Et `namespace` n'est PAS accepté comme alias.** Les alias de ce renommage sont des
redirections ANNONCÉES (308 + `Sunset` au 08/11/2026), jamais des acceptations muettes :
un paramètre repris en silence laisserait les textes non migrés le rester, sans que
personne l'apprenne.
"""
from __future__ import annotations

import pydantic
import pytest
from pydantic import BaseModel

from oto_mcp.capabilities.datastore import (activity, claim, columns, common,
                                            datastores, rows, schema, sharing)

MODULES = (activity, claim, columns, datastores, rows, schema, sharing)


def _entrees():
    """Les entrées qui portent le paramètre renommé — DÉRIVÉES, jamais listées."""
    for m in MODULES:
        for nom, obj in vars(m).items():
            if not (isinstance(obj, type) and issubclass(obj, BaseModel)):
                continue
            if nom.endswith("Input") and "datastore" in obj.model_fields:
                yield f"{m.__name__.rsplit('.', 1)[-1]}.{nom}", obj


def test_le_refus_NOMME_le_renommage_et_rend_la_valeur():
    with pytest.raises(pydantic.ValidationError) as e:
        claim.ClaimNextInput(namespace="mon-tableau", worker="w")
    msg = str(e.value)
    assert "renamed" in msg and "datastore" in msg
    # La valeur est REPRISE : l'agent recopie au lieu de reconstruire son appel.
    assert "'mon-tableau'" in msg
    # Et on coupe court à la mauvaise piste que l'ancien message ouvrait.
    assert "Do not hunt for another parameter" in msg
    assert "nothing was written" in msg


def test_TOUTES_les_entrees_qui_portent_le_parametre_sont_couvertes():
    """⚠️ Le banc qui compte. Une entrée neuve qui déclare `datastore` sans hériter de
    la base rendrait le vieux « Field required » — et le trou serait invisible, parce
    que les autres outils, eux, répondent juste."""
    nues = [nom for nom, obj in _entrees()
            if not issubclass(obj, common.EntreeDatastore)]
    assert not nues, (
        f"{len(nues)} entrée(s) portent `datastore` sans hériter de `EntreeDatastore` : "
        f"{nues}. Elles rendront « datastore: Field required » au lieu de nommer le "
        f"renommage.")
    assert sum(1 for _ in _entrees()) >= 20, "la sonde ne trouve plus les entrées"


@pytest.mark.parametrize("nom,modele", list(_entrees()))
def test_chaque_entree_refuse_en_nommant(nom, modele):
    with pytest.raises(pydantic.ValidationError) as e:
        modele(namespace="t")
    assert "renamed" in str(e.value), nom


def test_les_SORTIES_ne_sont_PAS_touchees():
    """⚠️ La moitié qui protège la transition. Les réponses DOUBLENT la clé
    `namespace` à côté de `datastore` jusqu'au 08/11/2026 — c'est la seule panne
    MUETTE de la bascule, et un consommateur qui lit `r["namespace"]` en dépend.

    Poser la garde sur un modèle de sortie ferait donc LEVER la construction de la
    réponse. Mesuré en écrivant ce lot : la première passe avait attrapé onze modèles
    de sortie, et `DatastoreEntry(namespace=…)` levait."""
    sorties = [datastores.DatastoreEntry, schema.SchemaOut, sharing.Shared,
               claim.ClaimResult, columns.DropColumnResult]
    for s in sorties:
        assert not issubclass(s, common.EntreeDatastore), (
            f"`{s.__name__}` est une SORTIE : la garde d'entrée y casserait la clé "
            f"`namespace` doublée que la transition sert jusqu'au 08/11/2026.")


def test_la_signature_SERVIE_ne_gagne_aucun_champ():
    """La base n'apporte qu'un validateur. Un champ de plus deviendrait un paramètre
    OFFERT dans le schéma servi — et un paramètre offert sera réglé."""
    props = claim.ClaimNextInput.model_json_schema()["properties"]
    assert "namespace" not in props, "l'ancien nom ne doit pas réapparaître au schéma"
    assert set(props) == set(claim.ClaimNextInput.model_fields)


def test_l_appel_CORRECT_passe_toujours():
    assert claim.ClaimNextInput(datastore="t", worker="w").datastore == "t"
    assert schema.SetSchemaInput(datastore="t").datastore == "t"
