"""Un porteur de secret ne se raconte pas (#564).

`ResolvedCredential` et `CascadeRung` sont des dataclasses : leur `repr` par défaut
imprime la clé DÉCHIFFRÉE. Ce n'est pas une question de log — c'est le canal par
lequel une frame retenue dans un traceback livre le secret à un collecteur d'erreurs.
La garde se pose donc sur l'OBJET, qui voyage, et pas sur les deux ou trois
variables qui le tiennent au passage.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from oto_mcp.access import secret_repr

SECRET = "sk_live_TRESSECRET"


@dataclass
class _Faux:
    nom: str
    cle: str
    n: int = 0


def test_le_champ_nomme_est_expurge_et_les_autres_restent():
    r = secret_repr.expurge(_Faux("stripe", SECRET, 3), "cle")
    assert SECRET not in r
    assert "cle=<expurgé>" in r
    assert "nom='stripe'" in r and "n=3" in r
    assert r.startswith("_Faux(")


def test_un_champ_inconnu_LEVE():
    """Le mode d'échec qui compte : une faute de frappe rendrait la protection
    muette — le repr continuerait d'imprimer la clé, et rien ne le dirait."""
    with pytest.raises(ValueError, match="n'a pas de champ"):
        secret_repr.expurge(_Faux("stripe", SECRET), "clé")


def test_le_credential_resolu_ne_se_repr_pas_en_clair():
    from oto_mcp.access.resolve import ResolvedCredential
    rc = ResolvedCredential("stripe", SECRET, False, "user")
    assert SECRET not in repr(rc) and SECRET not in str(rc)
    assert rc.secret == SECRET                    # l'usage n'est pas dégradé
    assert "stripe" in repr(rc) and "user" in repr(rc)


def test_le_barreau_gagnant_de_la_cascade_non_plus():
    from oto_mcp.access.cascade import CascadeRung
    r = CascadeRung("platform", "platform", "label", {"secret": SECRET})
    assert SECRET not in repr(r)
    assert r.payload["secret"] == SECRET
