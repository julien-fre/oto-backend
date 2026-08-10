"""Projection des SORTIES d'outils — le seam générique, la spécificité reste au module.

Aucun outil n'offrait de mode compact sur ce qu'il RENVOIE. Mesuré le 10/08/2026 sur une
conversation d'enrichissement réelle (agent Mistral, 7 appels) : `prompt_tokens=784`,
`total=8203` — **83 % du coût, ce sont les retours d'outils**, et un run a atteint
`finish_reason: error` à 27 % d'occupation de fenêtre. Le pilote avait dû écrire une
fonction de réduction maison par outil, invisible et refaite à sa façon par chaque
consommateur (oto-core#36).

**Ce qui est retiré par DÉFAUT vs sur demande — la ligne est nette :**

- **Duplication pure** (le même contenu servi deux fois) → retiré par défaut. Personne
  n'a besoin du texte brut ET du markdown de la même page ; il n'y a rien à perdre.
- **Détail moins utilisé** (knowledge graph, sitelinks, sources de vérification) → gardé
  par défaut, retiré sur `compact=True`. C'est la leçon d'oto-core#37 : `fr_get`
  projetait sur une allowlist et a laissé tomber `liste_idcc` en silence — un champ
  « IDCC vérifié » resté vide sur 500 lignes, découvert par un audit champ par champ.
  Une projection par défaut fait disparaître des données sans rien signaler.

Le module ne connaît AUCUN outil : chaque connecteur déclare ce qu'il coupe, là où il
sait ce que ses champs valent.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional


def project(payload: Any, *, drop: Iterable[str] = (), items_path: Optional[str] = None,
            item_drop: Iterable[str] = (), fields: Optional[Iterable[str]] = None) -> Any:
    """Copie allégée d'un payload d'outil. Pur, non destructif, tolérant à la forme.

    `drop` = clés de premier niveau retirées. `items_path` = chemin pointé vers la LISTE
    de résultats (`organic`, `data.emails`) ; dans chacun de ses éléments, `item_drop`
    retire des clés et `fields`, s'il est fourni, ne garde QUE celles-là.

    Tolérant par construction : une clé absente, un chemin qui ne mène nulle part ou un
    payload d'une autre forme passent sans erreur. Une API tierce change de forme sans
    prévenir — une projection qui lève transformerait une réponse utile en panne, et
    c'est le connecteur qui porterait le blâme.

    `fields` ne filtre QUE les éléments de la liste, jamais l'enveloppe : `credits`,
    `total`, le curseur de pagination restent — sans eux l'agent croit avoir tout vu."""
    if not isinstance(payload, dict):
        return payload
    out = {k: v for k, v in payload.items() if k not in set(drop)}
    if not items_path:
        return out
    # Descente en RECOPIANT chaque niveau : `out` est une copie de surface, écrire dans
    # un sous-dict partagé muterait le payload d'appel (et le cache qui le détient).
    parts, node = items_path.split("."), out
    for p in parts[:-1]:
        child = node.get(p)
        if not isinstance(child, dict):
            return out
        copy = dict(child)
        node[p] = copy
        node = copy
    rows = node.get(parts[-1])
    if not isinstance(rows, list):
        return out
    keep, item_drop = (set(fields) if fields else None), set(item_drop)
    node[parts[-1]] = [
        row if not isinstance(row, dict) else
        ({k: v for k, v in row.items() if k in keep} if keep is not None
         else {k: v for k, v in row.items() if k not in item_drop})
        for row in rows
    ]
    return out
