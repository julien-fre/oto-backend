"""La doc de sécurité dit ce que le palier gratuit ouvre — et le registre la tient (#804).

`docs/auth-logto.md` a affirmé jusqu'au 23/09/2026 que les clés plateforme « ne sont
accessibles qu'avec un grant explicite ». C'était faux : un compte neuf résout la clé
de chaque connecteur `platform_key_open` dont l'instance plateforme est en partage
`open`. Une garde décrite qui n'existe pas éteint la question chez celui qui la lit.

Ce banc tient la table « Free tier » de `SECURITY.md` contre le registre
(`providers/`) : un connecteur qui rejoint ou quitte le palier gratuit, ou dont le
quota par défaut change, rougit ici tant que la doc n'a pas suivi. Il ne peut pas
tenir l'état de la base (quelle clé est réellement posée en `open`) : la doc le dit,
et nomme l'instrument qui le lit.
"""
from __future__ import annotations

import re
from pathlib import Path

from oto_mcp import providers

_DOC = Path(__file__).resolve().parent.parent / "SECURITY.md"


def _table_de_la_doc() -> dict[str, str]:
    texte = _DOC.read_text(encoding="utf-8")
    debut = texte.index("**Free tier")
    lignes = texte[debut:].splitlines()
    table: dict[str, str] = {}
    dans_table = False
    for ligne in lignes:
        if ligne.startswith("|"):
            dans_table = True
            cellules = [c.strip() for c in ligne.strip("|").split("|")]
            nom = re.match(r"`([a-z0-9_]+)`", cellules[0])
            if nom:
                table[nom.group(1)] = cellules[1]
        elif dans_table:
            break
    assert table, "la table « Free tier » de SECURITY.md n'a pas été trouvée"
    return table


def _attendu(quota: int) -> str:
    return "no cap" if quota == 0 else str(quota)


def test_la_table_nomme_exactement_les_connecteurs_du_palier_gratuit():
    ouverts = {c.name for c in providers.REGISTRY.values() if c.platform_key_open}
    assert set(_table_de_la_doc()) == ouverts


def test_la_table_dit_le_quota_par_defaut_de_chacun():
    doc = _table_de_la_doc()
    for nom, cellule in doc.items():
        attendu = _attendu(providers.REGISTRY[nom].default_quota)
        assert cellule == attendu or cellule.startswith(attendu + ","), (nom, cellule, attendu)
