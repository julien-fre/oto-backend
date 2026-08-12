"""Ce que les capacités datastore partagent — aucun descripteur ici.

Deux choses seulement, et les deux existaient déjà dans les routes écrites à la main
qu'elles remplacent (`api_routes_datastore.py`) : le 404 qui dit OÙ vit un tableau, et
la garde de gouvernance d'un namespace. Les recopier dans chaque module de capacité
aurait fait diverger le message d'erreur d'un chemin à l'autre — c'est exactement le
drift que la couche capacité combat.

⚠️ **Horodatages : sans fuseau.** Toute date rendue par le datastore traverse
`db/_conn.py::_normalize_value`, qui fait `replace(tzinfo=None)` puis
`isoformat(sep=" ")` → `"YYYY-MM-DD HH:MM:SS"`. L'offset est **retiré sans
conversion** (forme héritée de SQLite, que le dashboard consomme telle quelle) : ces
chaînes ne sont donc PAS de l'ISO 8601 UTC, et les parser comme telles décale l'heure.
C'est dit dans chaque champ de date des modèles de sortie — un générateur de types ne
doit pas promettre un instant absolu là où le serveur rend une heure murale.
"""
from __future__ import annotations

from typing import Optional

from .. import db, org_store, ownership
from ..datastore import NamespaceNotFound, make_store
from ._types import AuthzDenied

# Phrase unique du champ de date des sorties datastore (cf. l'avertissement ci-dessus).
HORODATAGE = ("heure locale serveur, sans offset — `YYYY-MM-DD HH:MM:SS`, "
              "à ne pas parser comme de l'ISO UTC")


def ns_not_found(sub: Optional[str], namespace: str) -> AuthzDenied:
    """Le 404 qui dit OÙ vit le tableau quand il appartient à une autre org du user.

    L'API résout le store sur l'org ACTIVE ; viser le tableau d'une autre org demande
    l'en-tête `X-Oto-Org`, qui n'apparaissait ni dans la description des routes ni dans
    le moindre message. Un namespace bien réel répondait donc « namespace_not_found »,
    ce qui se lit comme « il n'existe pas » — temps perdu, et un faux diagnostic produit
    au passage (signal #316).

    On ne nomme que des orgs dont le porteur du token est MEMBRE : l'indice ne révèle
    rien qu'il ne puisse déjà lister. Fail-open : au moindre pépin, le 404 nu d'avant.

    Rend l'exception au lieu de la lever — l'appelant écrit `raise ns_not_found(…)`, ce
    qui garde le `raise` visible sur la ligne du chemin d'erreur.
    """
    try:
        orgs = {int(o["org_id"]): o.get("name") for o in org_store.list_orgs_for_user(sub)}
        owners = [("org", str(i)) for i in orgs]
        elsewhere = [n for n in db.list_datastore_namespaces_for_owners(owners)
                     if n["namespace"] == namespace]
        if elsewhere:
            where = ", ".join(
                f"{orgs.get(int(n['owner_id'])) or 'org'} (org {n['owner_id']})"
                for n in elsewhere)
            first = elsewhere[0]["owner_id"]
            return AuthzDenied(
                404, "namespace_not_found",
                f"« {namespace} » existe, mais dans une autre de tes organisations : "
                f"{where}. Rejoue la requête avec l'en-tête « X-Oto-Org: {first} ».")
    except Exception:  # noqa: BLE001 — un indice ne doit jamais casser la réponse
        pass
    return AuthzDenied(404, "namespace_not_found")


def govern_ns(sub: Optional[str], namespace: str) -> int:
    """Résout le namespace par nom + vérifie le droit de GOUVERNANCE de l'acteur
    (owner ∪ escalade `roles.py`, ADR 0030 — jamais un simple rôle d'org).

    ⚠️ Le 404 est ici **nu**, sans l'indice cross-org ci-dessus : c'est le comportement
    des routes de gouvernance d'avant la migration (renommer, partager), et un indice
    qui suggère « rejoue avec X-Oto-Org » n'aurait pas de sens sur un geste que
    l'acteur n'a de toute façon pas le droit de faire ailleurs.
    """
    try:
        ns_id = make_store(sub).resolve_ns_id(namespace)
    except NamespaceNotFound:
        raise AuthzDenied(404, "namespace_not_found")
    if not ownership.can_govern(sub, "datastore_namespace", str(ns_id)):
        raise AuthzDenied(403, "forbidden")
    return ns_id
