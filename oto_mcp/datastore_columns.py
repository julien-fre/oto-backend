"""La COLONNE côté Python : ce qu'une écriture touche, et sous quel nom on la désigne.

Extrait du store (#325), déplacement pur. Le pendant Python de `db/paths` : là-bas on
traduit un nom en SQL, ici on décide ce qu'une écriture modifie et ce qu'un ancien nom
désigne encore.

La règle que ce module porte tient en une phrase — **une écriture ne touche QUE ce
qu'elle nomme** — et elle a coûté deux défauts symétriques, l'un après l'autre :

- écrire une VALEUR effaçait l'origine : le patch par identifiant, le geste le plus
  courant d'un agent ;
- écrire une ORIGINE seule effaçait la valeur : le geste nominal du rattrapage de
  socle, quand un tableau adopte les couches après coup.

Deux correctifs symétriques auraient laissé passer le troisième. Une règle unique dont
les deux découlent, non.

S'y ajoute la traduction des anciens noms plats pendant une migration : elle vit ici
parce qu'elle répond à la même question — de quelle colonne parle-t-on ?
"""
from __future__ import annotations

from typing import Any, Optional

from . import datastore_schema as dsv2
from .datastore_errors import RowValidationError


def _writes_layers(new: Any) -> bool:
    """L'écriture NOMME-t-elle des couches ? (`{"origine": …}`, `{"valeur": …}`…)

    Strict, comme tout écrivain : un dict fait UNIQUEMENT de couches connues. Un
    `{"a": 1, "origine": "x"}` reste une donnée `json` métier qui se trouve avoir un
    champ nommé « origine » — on ne le réinterprète pas."""
    return (isinstance(new, dict) and bool(new)
            and all(k in dsv2.ALL_LAYER_KEYS for k in new))


def _existing_layers(existing: Any) -> dict:
    """Le contenu ACTUEL d'une colonne, vu comme ses couches.

    Tolérant, comme tout lecteur : un dict qui porte `valeur` est une colonne à
    couches même s'il en porte une qu'on ne connaît pas — écrite par une version plus
    récente, elle traverse intacte au lieu d'être perdue à la première réécriture par
    un nœud plus ancien. Un scalaire est une valeur sans couches ; `None` est le vide."""
    if isinstance(existing, dict) and existing and (
            dsv2.VALUE_LAYER in existing
            or all(k in dsv2.ALL_LAYER_KEYS for k in existing)):
        return dict(existing)
    return {} if existing is None else {dsv2.VALUE_LAYER: existing}


def _to_path(schema: Optional[dict], nom):
    """Un ancien nom plat → son chemin réel ; tout le reste, inchangé."""
    if not isinstance(nom, str):
        return nom
    cible = dsv2.resolve_flat_name(schema, nom)
    if cible is None:
        return nom
    colonne, rang, attr = cible
    return f"{colonne}[{rang}].{attr}"


def _resolve_filters(schema: Optional[dict], filters):
    out = []
    for f in filters or []:
        if not isinstance(f, dict):
            out.append(f)
            continue
        g = dict(f)
        if g.get("field"):
            g["field"] = _to_path(schema, g["field"])
        if isinstance(g.get("fields"), list):
            g["fields"] = [_to_path(schema, k) for k in g["fields"]]
        if isinstance(g.get("where"), list):
            g["where"] = _resolve_filters(schema, g["where"])
        out.append(g)
    return out


def _resolve_metrics(schema: Optional[dict], metrics):
    out = []
    for m in metrics or []:
        if not isinstance(m, dict):
            out.append(m)
            continue
        g = dict(m)
        if g.get("field"):
            g["field"] = _to_path(schema, g["field"])
        if isinstance(g.get("where"), list):
            g["where"] = _resolve_filters(schema, g["where"])
        out.append(g)
    return out


def _resolve_group_by(schema: Optional[dict], group_by):
    if isinstance(group_by, (list, tuple)):
        return [_to_path(schema, k) for k in group_by]
    return _to_path(schema, group_by)


def _refuse_flat_writes(schema: Optional[dict], user_data: dict) -> None:
    """Écrire sur un nom PROJETÉ est refusé, en nommant la cible neuve (oto#22 §6).

    Pendant la migration, `contact1_nom` est servi en LECTURE — calculé depuis la
    colonne-tableau, jamais stocké. L'accepter en écriture créerait une colonne libre
    du même nom : la lecture continuerait de rendre la valeur PROJETÉE, et ce qui vient
    d'être écrit serait invisible tout en ayant été accepté. C'est la forme exacte du
    défaut qu'on passe la journée à fermer — un accusé de réception pour un travail qui
    n'atteint rien.

    Le refus dit où écrire : un message qui dit seulement « non » fait deviner."""
    if not user_data:
        return
    for cle in user_data:
        cible = dsv2.resolve_flat_name(schema, cle)
        if cible is None:
            continue
        colonne, rang, attr = cible
        raise RowValidationError([
            f"{cle}: nom servi en lecture pendant la migration, il ne s'écrit pas "
            f"(il est CALCULÉ depuis `{colonne}`, jamais stocké) — écrire "
            f"`{colonne}[{rang}].{attr}`"])


def _merge_column(existing: Any, new: Any) -> Any:
    """Fusion d'UNE colonne. **Aucune couche ne s'écrit implicitement, dans aucun sens.**

    Une écriture ne touche QUE ce qu'elle nomme. C'est la protection contre
    l'ACCIDENT, pas contre l'intention — et surtout, c'est ce qui dispense l'agent d'y
    penser : il écrit ce qu'il veut poser, le reste demeure. Un geste explicite
    remplace ce qu'il vise ; il n'y a pas de verrou, donc rien à contourner.

    Les deux directions ont coûté un défaut chacune, et la seconde a failli coûter
    8 910 lignes :

      - écrire une VALEUR effaçait l'origine (#322) — le patch par `id`, le geste le
        plus courant d'un agent ;
      - écrire une ORIGINE seule effaçait la valeur (#326) — le geste nominal du
        RATTRAPAGE de socle, quand un tableau adopte les couches après coup. Aucune
        erreur, la valeur simplement disparue.

    D'où la règle unique dont les deux découlent, plutôt que deux correctifs
    symétriques : on part de l'existant, l'écriture y dépose ce qu'elle nomme.

    ⚠️ Deux conséquences qui ne se devinent pas :

    `comment` et `link` décrivent LA VALEUR : quand elle change sans qu'ils soient
    renommés, ils tombent avec elle — les garder ferait affirmer une provenance
    fausse, précisément le défaut qu'on élimine une couche plus haut. `origine`, elle,
    décrit le point de départ : elle survit.

    Une écriture ORDINAIRE (scalaire, `null`, ou donnée `json`) est une écriture de
    la valeur : elle laisse l'origine intacte. Effacer l'origine se demande —
    `{"origine": null}`. Et une colonne dont il ne reste que la valeur redevient un
    scalaire nu : les lignes sans couches ne doivent pas se mettre à porter une
    enveloppe."""
    if not _writes_layers(new):
        # Toute colonne A une origine ; quand elle est VIDE il n'y a rien à préserver,
        # et la colonne reste plate — le plat est un état, pas une nature.
        origine = _existing_layers(existing).get(dsv2.ORIGIN_LAYER)
        if origine is None:
            return new
        return ({dsv2.ORIGIN_LAYER: origine} if new is None
                else {dsv2.VALUE_LAYER: new, dsv2.ORIGIN_LAYER: origine})
    out = _existing_layers(existing)
    if dsv2.VALUE_LAYER in new:
        for couche in dsv2.VALUE_BOUND_LAYERS:
            out.pop(couche, None)
    out.update(new)
    out = {k: v for k, v in out.items() if v is not None}
    if not out:
        return None
    if set(out) == {dsv2.VALUE_LAYER}:
        return out[dsv2.VALUE_LAYER]
    return out
