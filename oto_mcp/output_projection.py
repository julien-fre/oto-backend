"""Projection des SORTIES d'outils — le seam générique, la spécificité reste au module.

Aucun outil n'offrait de mode compact sur ce qu'il RENVOIE. Mesuré le 10/08/2026 sur une
conversation d'enrichissement réelle (agent Mistral, 7 appels) : `prompt_tokens=784`,
`total=8203` — **83 % du coût, ce sont les retours d'outils**, et un run a atteint
`finish_reason: error` à 27 % d'occupation de fenêtre. Le pilote avait dû écrire une
fonction de réduction maison par outil, invisible et refaite à sa façon par chaque
consommateur (oto-core#36).

**Ce qui est retiré par DÉFAUT vs sur demande — la ligne a bougé le 11/08/2026 :**

- **Duplication pure** (le même contenu servi deux fois) → retiré par défaut. Personne
  n'a besoin du texte brut ET du markdown de la même page ; il n'y a rien à perdre.
- **Détail moins utilisé** (knowledge graph, sitelinks, sources de vérification) → aussi
  retiré par défaut désormais, et rendu sur **`full=True`**. C'était l'inverse (opt-in
  `compact=True`), par prudence héritée d'oto-core#37 : `fr_get` projetait sur une
  ALLOWLIST et a laissé tomber `liste_idcc` en silence — un champ « IDCC vérifié » resté
  vide sur 500 lignes. Cette leçon vaut pour une allowlist, pas pour une **denylist de
  clés nommées** : celle-ci ne peut pas faire disparaître un champ imprévu, seulement
  ceux qu'on a écrits. Et l'opt-in ne servait personne — mesuré, aucun agent branché en
  direct ne passait `compact` : il ne peut pas savoir qu'il existe avant d'avoir lu le
  schéma, et le guide qui le pilote ne nomme aucun outil par choix. **Une économie
  qu'il faut connaître pour en bénéficier ne bénéficie à personne** : le défaut servait
  le cas rare et faisait payer le cas général.

Le nom porte l'intention (ADR 0047 §Amendement) : `full=True` dit ce qu'on obtient, là où
`compact=False` se lisait comme une double négation.

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


RAW = "*"  # `fields=["*"]` — le chemin vers le brut, même jeton que data_rows/le feed


_HINT_TRI = (f'Vue de tri. Corps et colonnes écartés : `fields=["{RAW}"]` '
             "rend la ligne entière, `fields=[…]` choisit.")


def summarize(rows: list[dict], *, body_fields: Iterable[str],
              fields: Optional[Iterable[str]] = None,
              always: Iterable[str] = (),
              hint: Optional[str] = None) -> tuple[list[dict], Optional[dict]]:
    """Vue de LISTE : un corps devient sa TAILLE, jamais un extrait.

    Une liste est une étape de NAVIGATION — elle sert à décider quoi ouvrir ensuite,
    donc à adresser, trier et écarter sans se tromper. Le contenu se demande dans un
    second temps. Rendre le corps de chaque élément fait dépasser le plafond d'un tool
    result (mesuré : 201 K caractères pour 37 pages), et le client doit alors déverser
    en fichier puis reparser — un agent sans shell, lui, cale simplement.

    ⚠️ **Projeter ≠ tronquer.** On retire des COLONNES (réversible, et le retour dit
    lesquelles) ; on ne coupe pas un texte à N caractères. Un extrait arbitraire tombe
    pile avant ce qui départage deux éléments et l'agent croit avoir lu — mesuré le
    11/08 sur un feed coupé à 600 c., deux cas limites sur cinq tranchés à l'aveugle.
    D'où `<champ>_length` : l'agent sait ce qu'il n'a PAS.

    `body_fields` = les colonnes-corps. `fields` : omis → vue de tri ; `["*"]` → le
    brut ; `[…]` → exactement ces clés, plus `always` (de quoi ADRESSER l'élément
    ensuite — sans quoi la liste rendrait des lignes inutilisables).

    `hint` remplace la phrase servie dans la notice. Le seam ne sert pas que des LISTES :
    la même projection s'applique à une page unique — lecture projetée, et surtout ACCUSÉ
    d'écriture (`oto_doc` op=update/patch, signaux #506/#530). Dire « vue de tri » à un
    agent qui vient d'écrire serait faux, et une notice fausse est pire qu'absente : elle
    est lue avec confiance. C'est un paramètre plutôt qu'une seconde fonction — deux
    façons de projeter divergeraient (leçon `fr_get`, cf. l'en-tête du module).

    Rend `(rows, notice)` — `notice` NOMME ce qui a été écarté et le chemin vers le
    brut ; `None` quand rien ne l'a été."""
    bodies = list(body_fields)
    wanted = None if fields is None else list(fields)
    if wanted is not None and RAW in wanted:
        return rows, None
    keep = None if wanted is None else (set(wanted) | set(always))
    dropped: set[str] = set()
    out: list[dict] = []
    for row in rows:
        item = {k: v for k, v in row.items() if keep is None or k in keep}
        for b in bodies:
            if keep is not None and b in keep:
                continue
            if b in row:
                item.pop(b, None)
                dropped.add(b)
            # La taille est rendue même quand la colonne est absente de la ligne :
            # « 0 » et « pas de corps » se lisent pareil pour qui trie.
            item[f"{b}_length"] = len(row.get(b) or "")
        out.append(item)
    if keep is not None:
        dropped |= {k for row in rows for k in row if k not in keep}
    if not dropped:
        return out, None
    return out, {"omitted": sorted(dropped), "hint": hint or _HINT_TRI}
