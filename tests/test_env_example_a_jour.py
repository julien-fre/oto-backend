"""`.env.example` est DÉRIVÉ de `oto_mcp/env_inventory.py`, jamais recopié à la main
(oto-backend#968, ADR 0070) — cf. `scripts/generer_env_example.py`.

Ce test ferme la boucle : `test_env_inventory_complet.py` garantit que l'inventaire ne
prend pas de retard sur le CODE ; celui-ci garantit que `.env.example` ne prend pas de
retard sur l'INVENTAIRE. Sans lui, rien n'empêche d'ajouter une variable à
`env_inventory.py` sans relancer le générateur — la chaîne resterait cassée à son
dernier maillon.
"""
from __future__ import annotations

from scripts.generer_env_example import CIBLE, generer


def test_env_example_est_derive_de_l_inventaire():
    attendu = generer()
    actuel = CIBLE.read_text(encoding="utf-8") if CIBLE.exists() else ""
    assert actuel == attendu, (
        f"{CIBLE.name} diverge de ce que produit `oto_mcp/env_inventory.py` — "
        "régénère avec `python -m scripts.generer_env_example` et committe le "
        "résultat dans le même commit que le changement d'inventaire.")


def test_le_fichier_derive_existe_et_n_est_pas_vide():
    assert CIBLE.exists(), f"{CIBLE} n'existe pas — lance `python -m scripts.generer_env_example`"
    assert len(CIBLE.read_text(encoding="utf-8")) > 500
