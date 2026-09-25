"""Le REGISTRE des tableaux : lister, créer, renommer, supprimer, adresser.

Extrait de `core.py` (déplacement pur, 07/09/2026) — un mixin que `DatastorePg`
compose, sur le modèle de `SchemaOpsMixin`. La résolution elle-même (`_resolve`,
`_active_scope`) reste au noyau : c'est l'identité du store, pas un geste de
gouvernance.

⚠️ Ce module LIT des attributs de tableau (`schema`, `owner_type`, `permission`…) :
il est listé dans `vocabulaire._read_keys`.
"""
from __future__ import annotations

from typing import Optional

from .. import db, ownership
from . import acces_agent as aga
from . import identite
from .errors import DatastoreExists, DatastoreForbidden
from .outils import _ns_url

# Sentinelle du mémo d'org des liens : `None` est une réponse légitime (aucune org
# à porter), il ne peut donc pas signifier « pas encore calculée ».
_PAS_CALCULE = object()


def _avertissement_de_portee(ns_id: int, owner_type: str, *,
                             explicite: bool) -> Optional[str]:
    """« Tu as posé un contexte d'org, et le tableau naît quand même personnel. »

    C'est JUSTE (ADR 0068 : le propriétaire ne se déduit jamais du contexte), mais
    c'est le contraire de ce qu'on attend d'un en-tête que toute la doc du datastore
    recommande pour « agir dans l'org ». Sans cette phrase, tout marche sous cet
    en-tête et personne d'autre ne voit le tableau : l'erreur ne se découvre qu'au
    second agent.

    ⚠️ On regarde l'org EXPLICITEMENT demandée pour cet appel — `X-Oto-Org` (REST) ou
    l'org du jeton (`_org=`, agent) — jamais l'org ACTIVE, toujours posée puisqu'elle
    retombe sur la maison. Avertir dessus, ce serait avertir à CHAQUE création, y
    compris quand personne n'a rien demandé, et un avertissement qui se déclenche
    toujours ne se lit plus.

    ⚠️ **Le remède dépend de la FACE, parce que les deux faces n'ont pas le même
    geste.** `owner` est un paramètre de la route REST ; le tool MCP ne l'a pas.
    Prescrire `owner` à un agent, c'est lui faire dépenser un appel pour un refus —
    le défaut même que cette phrase répare, retourné. Ce qu'un agent PEUT faire, lui,
    c'est transférer après coup (`oto_resource op=transfer`).
    """
    if explicite or owner_type != "user":
        return None
    from .. import session_org

    demandee = session_org.current_view_org() or session_org.current_call_org()
    if not demandee:
        return None
    tete = (f"Tableau créé PERSONNEL : toi seul le vois, même si l'organisation "
            f"{demandee} était le contexte de cet appel. Le contexte d'org ne décide "
            f"pas du propriétaire — il se demande.")
    if aga.appel_d_agent():
        return (f"{tete} Pour qu'il appartienne à l'organisation : "
                f"`oto_resource(op='transfer', resource_id='{ns_id}', "
                f"new_owner_org={demandee})`.")
    return (f"{tete} Pour qu'il appartienne à l'organisation : "
            f"`owner: {{\"type\": \"org\", \"id\": {demandee}}}` à la création.")


class RegistreMixin:
    """Le cycle de vie d'un datastore. Composé par `DatastorePg`."""

    # --- datastore lifecycle -------------------------------------------------

    def _org_des_liens(self) -> Optional[int]:
        """L'org à porter dans le lien d'un tableau — calculée UNE fois par geste, et
        seulement si le produit du compte la réclame (oto#63). Une liste de cent
        tableaux ne paie donc jamais cent résolutions d'org, et chez nous
        (`/data/{id}`) elle n'en paie aucune."""
        memo = getattr(self, "_org_liens_memo", _PAS_CALCULE)
        if memo is _PAS_CALCULE:
            from .. import access, links
            memo = (access.current_org(self.sub)
                    if links.patron_reclame("table", "org", sub=self.sub) else None)
            self._org_liens_memo = memo
        return memo

    def _entry(self, n: dict, *, shared: bool, permission: Optional[str] = None) -> dict:
        ns_id = int(n["id"])
        perso = (self.sub is not None
                 and n.get("owner_type") == "user" and n.get("owner_id") == self.sub)
        # Agissant-org (sub-less) : pas de gouvernance via l'endpoint (create/delete/
        # rename/share restent réservés à un user identifié).
        can_govern = (False if self.acting_org is not None
                      else ownership.can_govern(self.sub, ownership.TYPE_RESSOURCE_DATASTORE, str(ns_id)))
        return {
            "id": ns_id,
            # ⚠️ **Le numéro sort sous les DEUX noms, et `identite` est la source du
            # second** (oto#176). Le catalogue rendait `id` pendant que `data_rows` et
            # `data_get_schema` rendaient le même nombre sous `ns_id` — et la
            # description de `data_rows` est DIRECTIVE (« le NUMÉRO du tableau
            # (`ns_id`) — la forme à employer »). Qui la suit cherchait `ns_id` dans la
            # seule remise qui ne l'avait pas : deux confusions d'identifiant en deux
            # jours, dont une demande de suppression visant le mauvais tableau.
            # `id` RESTE : le dashboard bâtit ses liens dessus (`/data/<id>`), et on ne
            # casse pas un consommateur vivant pour réparer un vocabulaire. Un seul
            # nombre, deux noms — `identite.identite` les pose ensemble, donc ils ne
            # peuvent pas diverger, et elle rend aussi le nom CANONIQUE du tableau.
            **identite.identite(ns_id, n["datastore"]),
            "created_at": n.get("created_at"),
            "url": _ns_url(ns_id, self.sub, org=self._org_des_liens()),
            "shared": shared,
            "owner_type": n.get("owner_type"),
            "owner_id": n.get("owner_id"),
            "permission": permission if shared else "write",
            "can_write": (permission == "write") if shared else True,
            "can_govern": can_govern,
            "is_personal": perso,
            # mode typé optionnel (ADR 0032 §6 / 0029, B6) ; None = table libre.
            # oto#83 : c'est le SECOND chemin par lequel un schéma complet est servi
            # (le premier est `get_schema`) — `data_list_datastores` et
            # `GET /api/datastores` en dépendent, et un catalogue qui nomme
            # une colonne masquée l'aurait servie par la porte de derrière.
            "schema": aga.schema_servi(n.get("schema")),
        }

    def list_datastores(self) -> list[dict]:
        """Datastores visibles DANS L'ORG ACTIVE (l'org est le contexte, ADR 0023) :
        possédés par l'org active + accordés à elle ou à MES équipes dans cette org
        (grants d'org/groupe — tous mes groupes de l'org active, pas seulement le
        groupe actif : un partage d'équipe doit se voir sans basculer). Un datastore
        possédé par une AUTRE org — ou partagé à l'acteur *en propre* (grant user,
        cross-org) — ne fuite PLUS dans la vue d'une org tierce (scope décidé le
        2026-07-01). Dédupliqués par id (priorité possédé). La résolution PAR NOM
        (`_resolve`) scope désormais SUR LE MÊME contexte d'org (2026-07-03) : un
        datastore d'une autre org ne se résout plus hors de son org non plus."""
        from .. import access
        if self.acting_org is not None:
            owner = ("org", str(self.acting_org))
            proprios: list = [owner]
        else:
            org = access.current_org(self.sub)
            if ownership.active_owner(org) is None:
                return []
            # ⚠️ `active_org_principals` et non `active_owner` (oto-backend#870,
            # 04/09/2026) : l'org active ET l'acteur. Depuis l'ADR 0068 un tableau créé
            # par un agent naît PERSONNEL — et cette liste ne montrait que l'org, donc
            # le créateur ne voyait pas ce qu'il venait de créer. Il concluait qu'il
            # n'existait pas ; il était pourtant résoluble par nom, et la recherche le
            # voyait. Une écriture sans lecteur, la classe oto#42 exactement.
            # Le jeu reste borné à l'org active : rien de cross-org n'entre par là.
            proprios = ownership.active_org_principals(self.sub, org)
        # ADR 0049 (cadrage 10/07) : les tableaux TEAM-OWNED de l'org active sont listés
        # comme les org-owned. `_active_scope` est la source unique du jeu de groupes
        # (mes équipes, ou TOUS les groupes de l'org pour un org_admin — même règle que
        # `oto_project op=list`) ; le scope reste borné à l'org active.
        org_ids, group_ids = self._active_scope()
        owned = proprios + [("group", str(g)) for g in group_ids
                            if ("group", str(g)) not in proprios]
        out: dict[int, dict] = {}
        for n in db.list_datastores_for_owners(owned):
            out[int(n["id"])] = self._entry(n, shared=False)
        for n in db.list_datastores_granted_to(self.sub, org_ids, group_ids):
            if int(n["id"]) in out:
                continue
            out[int(n["id"])] = self._entry(n, shared=True, permission=n.get("permission"))
        # Scope dur d'endpoint partagé : ne lister QUE les tableaux liés au projet.
        if self.allowed_ns_ids is not None:
            return [e for e in out.values() if int(e["id"]) in self.allowed_ns_ids]
        if self.acting_org is not None:
            return list(out.values())
        # Vue bornée (oto#270) : le partage reçu en propre n'est pas de O.
        return ownership.borner_a_la_vue(self.sub, ownership.TYPE_RESSOURCE_DATASTORE,
                                         list(out.values()), rid=lambda e: e["id"])

    def _default_owner(self) -> tuple[str, str]:
        """Owner d'un datastore créé sans précision = **la personne** (ADR 0068).

        ⚠️ C'était l'**org active** — « suppression du perso », un choix assumé du temps
        où l'appelant était un humain devant un écran, qui voit ce qu'il crée et où.
        L'appelant est aujourd'hui un agent qui ne lit que le nom du verbe : le geste le
        plus banal du produit posait du contenu lisible de toute l'org, sous une
        description qui annonçait « unique per user ».

        Le paramètre reste : `owner_type='org'` (ou `'group'`) donne un classeur
        partagé, et c'est désormais une phrase qu'on écrit plutôt qu'un défaut qu'on
        subit. Les tableaux existants ne bougent pas — la décision porte sur ce qui
        NAÎT."""
        return ("user", self.sub)

    def create_datastore(
        self, datastore: str, *, owner_type: Optional[str] = None, owner_id: Optional[str] = None,
    ) -> dict:
        """Crée un datastore. Défaut = **la personne** (`_default_owner`, ADR 0068).

        ⚠️ Cette phrase disait « défaut = org active », quinze lignes sous le code
        qui rend `("user", sub)` : elle datait du régime d'avant et personne ne
        l'avait suivie jusqu'ici. Un commentaire périmé sur un défaut de
        PROPRIÉTAIRE ne se contente pas d'être faux — il fait conclure à qui le lit
        que le tableau sera visible de l'org (otomata-tech/oto#45).

        Le contexte d'org de l'appel n'entre PAS dans ce choix : pour un classeur
        d'org ou d'équipe, passer `owner_type`/`owner_id`, dont l'autorisation
        (appartenance) est vérifiée par l'appelant (capacité/route).

        ⚠️ **La réponse porte le propriétaire, et c'est ICI qu'elle le porte** — pas
        dans une face. Le 05/09 (`0e23177e`), `owner_type`/`owner_id`/`is_personal` et
        l'avertissement ont été ajoutés dans la capacité REST seule, pendant que la
        description servie du tool MCP promettait au modèle que « la réponse te dit le
        propriétaire ». Deux faces, un seul geste, une promesse vraie d'un côté : le
        créateur ne voit rien d'anormal, et l'écart se découvre au second agent. Le
        store rend donc la forme complète et les deux faces la relaient (08/09/2026)."""
        demande_explicite = owner_type is not None
        if owner_type is None:
            owner_type, owner_id = self._default_owner()
        oid = owner_id if owner_id is not None else self.sub
        try:
            ns_id = db.create_datastore(owner_type, oid, datastore,
                                        context_org_id=self._org_de_l_appel())
        except ValueError as e:
            raise DatastoreExists(str(e))
        # `id` ET `ns_id` : le même nombre sous les deux noms (oto#176) — la
        # création est la remise où l'agent LIT le numéro pour la première fois,
        # c'est donc la dernière qui puisse ne le servir que sous un seul nom.
        out = {"id": ns_id, **identite.identite(ns_id, datastore),
               "url": _ns_url(ns_id, self.sub, org=self._org_des_liens()),
               "owner_type": owner_type, "owner_id": oid,
               "is_personal": owner_type == "user"}
        avertissement = _avertissement_de_portee(ns_id, owner_type,
                                                 explicite=demande_explicite)
        if avertissement:
            out["avertissement"] = avertissement
        return out

    def delete_datastore(self, datastore: str) -> None:
        ns_id = self._resolve(datastore)
        if not ownership.can_govern(self.sub, ownership.TYPE_RESSOURCE_DATASTORE, str(ns_id)):
            raise DatastoreForbidden(datastore)
        db.delete_datastore_by_id(ns_id)  # rows + grants partent avec

    def rename_datastore(self, datastore: str, new_name: str) -> dict:
        """Renomme un datastore (l'id/URL/grants restent stables, keyés par id — cf.
        `db.rename_datastore_by_id`). Exige le droit de GOUVERNANCE, comme la
        suppression. Le nouveau nom doit être libre chez le même propriétaire (sinon
        `DatastoreExists`) — c'est ce qui lève la collision cross-org du gap #71 avant
        un transfert/merge."""
        ns_id = self._resolve(datastore)
        if not ownership.can_govern(self.sub, ownership.TYPE_RESSOURCE_DATASTORE, str(ns_id)):
            raise DatastoreForbidden(datastore)
        new_name = (new_name or "").strip()
        try:
            db.rename_datastore_by_id(ns_id, new_name)
        except ValueError as e:
            raise DatastoreExists(str(e))
        # Le numéro sous les deux noms (oto#176) : il ne bouge pas, et c'est
        # justement ce que cette remise doit dire à qui vient de perdre le nom.
        return {"id": ns_id, **identite.identite(ns_id, new_name),
                "url": _ns_url(ns_id, self.sub, org=self._org_des_liens())}

    def resolve_ns_id(self, datastore: str) -> int:
        """ns_id d'un datastore visible par l'acteur (lève `DatastoreNotFound`).
        Surface publique pour les chemins de gouvernance (partage/transfert)."""
        return self._resolve(datastore)

    def resolve_ns_id_for_write(self, datastore: str) -> int:
        """ns_id d'un datastore où l'acteur peut ÉCRIRE (lève `DatastoreNotFound`/
        `DatastoreReadOnly`). Sert à sceller la cible d'un upload signé au mint (org
        active présente) ; l'autz est réappliquée au receive via `ownership.can_access`
        sur `datastore` (org-agnostique), sans contexte d'org."""
        return self._resolve(datastore, write=True)

    def get_url(self, datastore: str) -> str:
        return _ns_url(self._resolve(datastore), self.sub, org=self._org_des_liens())  # 404 si inconnu
