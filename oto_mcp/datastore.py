"""Datastore — substrat natif PostgreSQL (ADR 0016).

Un namespace = une ligne `user_datastores` + ses rows dans `datastore_rows`
(une row = un dict JSONB). Schéma libre : aucune colonne à provisionner, les
champs apparaissent dans `data`. Trois champs auto-managés, exposés à plat dans
la row renvoyée :

- `_id` : identifiant uuid7-like (col `row_id`).
- `_created_at` / `_updated_at` : timestamps (colonnes dédiées).

Plus de dépendance Google : la vérité est en base, types préservés nativement
par JSONB (fin de la sentinelle `__j:` de l'ère Sheets). La propriété et le partage
passent par la primitive générique `ownership` (ADR 0030) : un namespace est possédé
par `(owner_type, owner_id)` (user/org/group) et accessible via owner-match ∪ grants
(`resource_grants`). L'export vers un provider tiers (Sheets/Notion…) est une
projection optionnelle, déférée à otomata#29.
"""
from __future__ import annotations

import base64
import binascii
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from psycopg.errors import UniqueViolation

from . import datastore_schema as dsv2
from . import db, ownership, session_org
from . import config


_META_COLS = ("_id", "_created_at", "_updated_at", "_claimed_by", "_claimed_until")


def _merge_column(existing: Any, new: Any) -> Any:
    """Fusion d'UNE colonne : ce qu'on écrit gagne, sauf `origine` qui survit.

    L'origine est la valeur reçue à l'import. Une écriture ORDINAIRE ne doit pas y
    toucher — et surtout pas avoir à y penser : un agent qui met à jour un dirigeant
    écrit la valeur courante, l'origine reste, sans consigne et sans exception à
    lever. C'est la protection contre l'ACCIDENT, pas contre l'intention.

    Un geste qui vise explicitement l'origine la remplace : il suffit de l'écrire.
    Pas de verrou, donc rien à contourner — et un ré-import repose simplement une
    nouvelle valeur de départ.

    ⚠️ Les autres couches ne survivent PAS. `source`, `source_link` et `commentaire`
    décrivent LA VALEUR : les garder au-dessus d'une valeur remplacée ferait affirmer
    une provenance fausse — précisément le défaut qu'on élimine, une couche plus
    haut. Elles suivent la valeur ou disparaissent avec elle."""
    # Toute colonne A une origine ; ici elle est VIDE, il n'y a donc rien à préserver
    # — ce n'est pas « la colonne n'a pas de couches », c'est « ses couches sont
    # vides ». Le plat est un état, pas une nature.
    if not isinstance(existing, dict) or dsv2.ORIGIN_LAYER not in existing:
        return new
    origine = existing[dsv2.ORIGIN_LAYER]
    if isinstance(new, dict) and dsv2.VALUE_LAYER in new:
        return new if dsv2.ORIGIN_LAYER in new else {**new, dsv2.ORIGIN_LAYER: origine}
    return {dsv2.VALUE_LAYER: new, dsv2.ORIGIN_LAYER: origine}


class RowValidationError(ValueError):
    """Écriture refusée par le schéma strict / le cycle de vie (ADR 0046 B/C).
    Le message liste les champs fautifs — actionnable, jamais un refus muet."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("écriture refusée par le schéma : " + " ; ".join(errors))


class InvalidCursor(ValueError):
    """Curseur de pagination illisible (mal formé / tronqué)."""


def _encode_cursor(row_id: str) -> str:
    """Curseur opaque = base64url du dernier `row_id` de la page (keyset)."""
    return base64.urlsafe_b64encode(row_id.encode()).decode()


def _decode_cursor(cursor: str) -> str:
    try:
        return base64.urlsafe_b64decode(cursor.encode()).decode()
    except (binascii.Error, ValueError, UnicodeDecodeError) as e:
        raise InvalidCursor(cursor) from e


_OFFSET_CURSOR_PREFIX = "off:"


def _encode_offset_cursor(offset: int) -> str:
    """Curseur du chemin TRIÉ (`order_by`) : l'ordre n'étant plus celui du keyset
    `row_id`, la page suivante se repère par offset. Même forme opaque que le curseur
    keyset, préfixée pour ne jamais confondre les deux régimes."""
    return _encode_cursor(f"{_OFFSET_CURSOR_PREFIX}{offset}")


def _decode_offset_cursor(cursor: str) -> int:
    raw = _decode_cursor(cursor)
    if not raw.startswith(_OFFSET_CURSOR_PREFIX):
        raise InvalidCursor(cursor)  # curseur keyset repassé sur un appel trié
    try:
        return max(0, int(raw[len(_OFFSET_CURSOR_PREFIX):]))
    except ValueError as e:
        raise InvalidCursor(cursor) from e


def _filter_specs(filter: Optional[dict]) -> list[dict]:
    """`{col: valeur}` ou `{col: {op: valeur}}` → la liste `{field, op, value}` du
    moteur SQL (ops whitelistés par `db._ds_filter_clauses`, qui lève sur inconnu).

    Une valeur scalaire reste une égalité (contrat historique) ; un dict ouvre les
    opérateurs déjà servis au dashboard — `contains`, `ne`, `in`, `gt/gte/lt/lte`,
    `empty`/`not_empty`. Sans ça, une question triviale (« quel post a une autrice
    prénommée Sylvie ? ») obligeait à dumper tout le namespace et à filtrer en
    local, alors que le SQL savait le faire.
    """
    out: list[dict] = []
    for k, v in (filter or {}).items():
        if isinstance(v, dict):
            if len(v) != 1:
                raise ValueError(
                    f"filtre `{k}` : un seul opérateur par colonne "
                    f"(reçu {sorted(v)!r})")
            op, value = next(iter(v.items()))
            out.append({"field": k, "op": str(op), "value": value})
        else:
            out.append({"field": k, "op": "eq", "value": v})
    return out


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    # uuid7-ish : timestamp ms + random. Construit à la main pour compat 3.10+.
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = uuid.uuid4().int & ((1 << 74) - 1)
    raw = (ms << 80) | (0x7 << 76) | (rand << 2)
    return str(uuid.UUID(int=raw))


def _ns_url(ns_id: int, sub: Optional[str] = None) -> Optional[str]:
    """Deep-link vers la vue datastore du dashboard (surface d'édition canonique
    tant que l'export tiers — otomata#29 — n'existe pas). Par ID (`/data/<id>`,
    BIGSERIAL stable au renommage) — l'adressage `?ns=<nom>` est déprécié.

    ⚠️ **Peut valoir `None`** : le produit d'un partenaire n'a pas forcément de vue
    tableau (celui du 13/08 n'en a aucune). On ne rend alors AUCUN lien — un lien mort
    ne se diagnostique pas, il se subit."""
    from . import links
    return links.link_for("table", sub=sub, id=int(ns_id))


class NamespaceNotFound(Exception):
    pass


class RowNotFound(Exception):
    pass


class NamespaceExists(Exception):
    pass


class NamespaceReadOnly(Exception):
    """Écriture tentée sur un namespace partagé en lecture seule."""
    pass


class NamespaceForbidden(Exception):
    """Action de gouvernance (supprimer/transférer) tentée sans droit de gouvernance."""
    pass


class RowClaimed(Exception):
    """Row nommée déjà sous bail ACTIF d'un autre worker (ADR 0046 D).

    Le conflit qu'il faut rendre visible : deux personnes qui prennent la même
    ligne à la même seconde, l'une des deux doit l'apprendre. Porte le bail en
    place pour que la surface dise QUI la tient et jusqu'à QUAND."""

    def __init__(self, row_id: str, claimed_by: Any = None, claimed_until: Any = None):
        self.row_id = row_id
        self.claimed_by = claimed_by
        self.claimed_until = claimed_until
        super().__init__(f"row {row_id} sous bail de {claimed_by!r} jusqu'à {claimed_until!r}")


def make_store(sub: str) -> "DatastorePg":
    """Construit un store PG pour `sub`. Plus aucune dépendance externe (ADR 0016)
    — datastore est une surface plateforme self-contained."""
    return DatastorePg(sub)


def make_org_store(org_id: int, *, allowed_ns_ids: Optional[set] = None,
                   read_only: bool = False) -> "DatastorePg":
    """Store agissant SOUS L'AUTORITÉ d'une ORG, sans user (`sub=None`). Sert un
    endpoint MCP `secret` opt-in datastore (ADR 0032) : la résolution de namespace et
    le droit d'écriture se décident sur le principal ORG (owner-match / grant d'org),
    jamais sur un membre. N'expose PAS la gouvernance (create/delete/rename/share) —
    ces actes restent réservés à un user identifié (tools sub-only).

    `allowed_ns_ids` (non None) = **scope dur** : seuls ces namespaces sont listables/
    résolvables — les tableaux LIÉS au projet partagé (anti-fuite #193 : sans ce scope
    l'endpoint exposerait TOUT le datastore de l'org). Set vide ⇒ rien d'exposé.
    `read_only=True` ⇒ l'écriture (`data_write`/`data_set_schema`) lève `NamespaceReadOnly`."""
    return DatastorePg(None, acting_org=int(org_id),
                       allowed_ns_ids=allowed_ns_ids, read_only=read_only)


class DatastorePg:
    """Store tabulaire adossé à PostgreSQL.

    State-less, instancié par requête. Normalement à partir du `sub` (l'acteur user) ;
    ou, pour un endpoint MCP agissant sous une org (`acting_org`, secret opt-in), avec
    `sub=None` — l'autorité est alors l'org propriétaire. Résout chaque namespace en
    `ns_id` (possédé OU partagé) et opère sur `datastore_rows`.
    """

    def __init__(self, sub: Optional[str], *, acting_org: Optional[int] = None,
                 allowed_ns_ids: Optional[set] = None, read_only: bool = False):
        self.sub = sub
        self.acting_org = acting_org
        # Scope dur (endpoint partagé) : None = pas de restriction ; set = ces ns_ids seuls.
        self.allowed_ns_ids: Optional[set] = (None if allowed_ns_ids is None
                                              else {int(x) for x in allowed_ns_ids})
        self.read_only = bool(read_only)
        self._active_scope_cache: Optional[tuple[list[int], list[int]]] = None
        # Relevé des champs écrits HORS SCHÉMA par ce store (#294), union sur un lot :
        # rempli par `_check_row`, lu par les surfaces via `off_schema_report()`. Le
        # store est instancié par requête, donc la portée est celle du geste.
        self.off_schema: set = set()
        self.off_options: dict = {}

    # --- résolution namespace -> ns_id ---------------------------------------

    def _active_scope(self) -> tuple[list[int], list[int]]:
        """Contexte de l'ORG ACTIVE (ADR 0023) : `([org active], [mes groupes dans cette
        org])`. La résolution par NOM scope là-dessus — comme `list_namespaces` — de sorte
        qu'un namespace d'une AUTRE de mes orgs ne se résout plus hors de son org (fuite
        cross-org, symétrique au fix projets). L'ownership PERSO (`owner=user`) et les
        grants perso (`principal user`) suivent l'acteur : ils n'appartiennent à aucune
        org, donc ne sont pas une fuite d'org — `resolve_datastore_ns` les garde via `sub`."""
        if self._active_scope_cache is None:
            if self.acting_org is not None:
                # Endpoint agissant-org (sub-less) : contexte = l'org propriétaire seule,
                # aucun groupe (pas de membre → pas de scope de groupe).
                self._active_scope_cache = ([int(self.acting_org)], [])
                return self._active_scope_cache
            from . import access, group_store
            oid = access.current_org(self.sub)
            if oid is None:
                self._active_scope_cache = ([], [])
            else:
                org = int(oid)
                # ADR 0049 (cadrage 10/07) : les groupes du contexte = mes équipes dans
                # l'org active — ou TOUS les groupes de l'org pour un org_admin (même
                # escalade que `roles.can_read_group`, alignée sur `oto_project op=list`).
                from . import roles
                if roles.is_org_admin(self.sub, org):
                    groups = [int(g["id"]) for g in group_store.list_groups(org)]
                else:
                    groups = [int(g["group_id"])
                              for g in group_store.list_groups_for_user(self.sub, org)]
                self._active_scope_cache = ([org], groups)
        return self._active_scope_cache

    def _resolve(self, namespace: str, *, write: bool = False) -> int:
        """ns_id d'un namespace VISIBLE DANS L'ORG ACTIVE (possédé par elle, perso, ou
        accordé à son contexte). `write=True` exige le droit d'écriture via
        `ownership.can_access`."""
        org_ids, group_ids = self._active_scope()
        ns = db.resolve_datastore_ns(
            namespace, sub=self.sub, org_ids=org_ids, group_ids=group_ids)
        if not ns:
            raise NamespaceNotFound(namespace)
        ns_id = int(ns["id"])
        # Scope dur d'endpoint partagé : hors des tableaux liés au projet ⇒ invisible
        # (anti-fuite #193 ; NamespaceNotFound plutôt que Forbidden — on ne divulgue pas
        # l'existence d'un namespace hors périmètre).
        if self.allowed_ns_ids is not None and ns_id not in self.allowed_ns_ids:
            raise NamespaceNotFound(namespace)
        if write and self.read_only:
            raise NamespaceReadOnly(namespace)
        if write:
            ok = (ownership.org_can_access(self.acting_org, "datastore_namespace",
                                           str(ns_id), "write")
                  if self.acting_org is not None
                  else ownership.can_access(self.sub, "datastore_namespace",
                                            str(ns_id), "write"))
            if not ok:
                raise NamespaceReadOnly(namespace)
        # Le journal cite l'ENTITÉ, pas la chaîne tapée : `data_write("mucho-leads")`,
        # `data_write("160")` et `data_write("slot:vivier")` visent le même tableau.
        # Consigné APRÈS les gardes (un namespace refusé ne laisse pas de trace) ;
        # no-op hors appel MCP — la face REST tient déjà son propre relevé.
        session_org.note_call_trace(ns_id=ns_id, ns_name=ns.get("namespace"))
        return ns_id

    @staticmethod
    def _row_to_dict(row: dict) -> dict:
        """Ligne `datastore_rows` → row API (`_id`/`_created_at`/`_updated_at` à
        plat + champs user). Le bail de claim (ADR 0046 D) n'apparaît que s'il est
        posé (les lectures ordinaires ne le SELECTent pas → absent, pas None)."""
        data = row.get("data") or {}
        out = {
            "_id": row["row_id"],
            "_created_at": row["created_at"],
            "_updated_at": row["updated_at"],
        }
        # Toute colonne a des sous-champs (#318) — c'est le contrat du datastore, pas
        # une forme que certaines valeurs adoptent. Une colonne « plate » est une
        # colonne dont les sous-champs sont VIDES, et on ne rend pas du vide.
        #
        # Le NOM NU rend donc toujours la valeur : un lecteur qui fait `row["email"]`
        # reçoit un e-mail, qu'il y ait une provenance ou non. Les sous-champs
        # renseignés s'ajoutent à plat sous `champ.couche` — visibles sans être
        # imposés, et projetables par `fields` comme n'importe quelle colonne.
        for k, v in data.items():
            if k in _META_COLS:
                continue
            out[k] = dsv2.unwrap(v)
            if isinstance(v, dict) and dsv2.VALUE_LAYER in v:
                for _layer in dsv2.LAYER_KEYS:
                    if v.get(_layer) not in (None, ""):
                        out[f"{k}.{_layer}"] = v[_layer]
        if row.get("claimed_by") is not None:
            out["_claimed_by"] = row["claimed_by"]
            out["_claimed_until"] = row.get("claimed_until")
        return out

    # --- schéma v2 : validation d'écriture + cycle de vie (ADR 0046) ---------

    def _ns_of(self, ns_id: int) -> dict:
        """La ligne `user_datastores` (nom canonique + schéma + propriétaire)."""
        return db.get_datastore_namespace_by_id(ns_id) or {}

    def _schema_of(self, ns_id: int) -> Optional[dict]:
        return self._ns_of(ns_id).get("schema")

    def _trace(self, trace: Optional[dict], ns_id: int, ns: dict,
               *, prev_status: Any = None) -> None:
        """RELEVÉ du geste pour le journal (seam ADR 0046 b4) : ce que la surface REST
        doit savoir, pris DANS la mutation qui l'a déjà calculé.

        ⚠️ `prev_status` **doit** venir d'ici et pas d'une relecture séparée : c'est
        l'état sur lequel la transition a été VALIDÉE. Une relecture faite avant
        l'appel court avec un write concurrent (un agent qui bouge la ligne entre
        les deux) et ferait proposer au cockpit une annulation vers un état que la
        ligne n'a jamais eu. Bénéfice second : zéro requête ajoutée (D2)."""
        if trace is None:
            return
        schema = ns.get("schema")
        trace.update({
            "ns_id": int(ns_id),
            "namespace": ns.get("namespace"),
            "status_key": (dsv2.status_field(schema) or {}).get("key"),
            "title_key": (dsv2.title_field(schema) or {}).get("key"),
            "prev_status": prev_status,
        })

    @staticmethod
    def _declared_key_of(schema: Optional[dict]) -> Optional[str]:
        k = (schema or {}).get("key")
        return k if isinstance(k, str) and k else None

    @staticmethod
    def _reject_layered_business_key(schema: Optional[dict], data: dict) -> None:
        """Refuse des couches sur LE champ qui sert de clé métier, tant que l'index
        d'unicité n'est pas migré (#318).

        Le lookup d'upsert et l'index UNIQUE lisent tous deux `data->>clé` — l'objet,
        pas la valeur. Écrire `{"siren": {"valeur": "552081317", …}}` donnerait donc :
        la validation ACCEPTE (elle déballe), le lookup ne matche pas (il compare
        l'objet), l'index ne collisionne pas (même raison) ⟹ **doublon silencieux du
        SIREN existant**. Un doublon qu'aucune des trois protections ne voit.

        La fenêtre est aujourd'hui théorique — personne n'écrit encore de couches.
        On refuse quand même : le coût d'un refus nommé est nul, celui d'un doublon
        de clé métier découvert plus tard ne l'est pas, et c'est précisément le genre
        d'écart qu'on ne détecte qu'en cherchant autre chose.

        Le refus tombe avec la migration de l'index, qui rendra les deux lectures
        polymorphes : le gate et sa levée sont le même invariant vu des deux bouts."""
        key = (schema or {}).get("key")
        if not key or not isinstance(data, dict):
            return
        if dsv2.unwrap(data.get(key)) is not data.get(key):
            raise RowValidationError([
                f"`{key}` est la clé métier de ce tableau : elle ne peut pas encore "
                "porter de sous-champs. L'unicité et la déduplication lisent la "
                "colonne telle quelle, donc une valeur enveloppée créerait un doublon "
                f"sans que rien ne le signale. Écris `{key}` en valeur nue ; les "
                "sous-champs y arriveront avec la migration de l'index."])

    def _check_row(self, schema: Optional[dict], merged: dict, *,
                   prev_status=None, written: Optional[set] = None) -> None:
        """Valide la row TELLE QU'ÉCRITE (résultat mergé). No-op si le schéma ne
        déclare ni strict/required/max_length ni lifecycle (défaut 0016 soft).

        `written` = les clés que le geste réécrit (None sur un insert/remplacement,
        où tout est écrit) : borne `max_length` restreinte à celles-là, cf.
        `dsv2.validate_row`.

        C'est aussi LE seam d'écriture — tous les chemins (append, batch, merge de
        clé métier, upsert, patch) y passent — donc l'endroit unique où relever les
        champs HORS SCHÉMA du geste (#294), sur les seules clés posées. Un schéma
        `strict` active la validation, donc l'appel a bien lieu."""
        self._reject_layered_business_key(schema, merged)
        errors = dsv2.validate_row(schema, merged, prev_status=prev_status,
                                   written=written)
        if errors:
            raise RowValidationError(errors)   # refusée ⇒ rien à relever
        posed = merged if written is None else {k: merged[k] for k in written
                                                if k in merged}
        self.off_schema.update(dsv2.off_schema_keys(schema, posed))
        # Valeurs hors des options DÉCLARÉES quand rien ne les fait respecter (#319) :
        # écrites quand même — le tableau est en régime souple — mais plus en silence.
        # Vide dès que la validation est armée : là, `validate_row` ci-dessus a déjà
        # refusé, et le redire serait un doublon sur un chemin qui ne passe pas.
        self.off_options.update(dsv2.unenforced_options(schema, posed))

    @staticmethod
    def _reject_misplaced_id(data: dict, row_id: Optional[str], *,
                             batch: bool = False) -> None:
        """REFUSE un `_id` posé DANS le payload au lieu du paramètre `id` (#390).

        `_id` est géré par le datastore : il vit dans la colonne `row_id`, jamais
        dans le blob. Il était donc filtré des données écrites — en SILENCE, et c'est
        ce silence qui coûte : une écriture `row={"_id": "019f…", "statut": …}` sans
        `id=` a INSÉRÉ une ligne neuve portant tout le travail d'un enrichissement,
        la ligne visée restant vide, sans une erreur. 28 champs repris à la main.

        Refuser ne casse aucun appelant légitime : personne n'écrit `_id` comme
        DONNÉE, puisque le faire n'avait déjà aucun effet. Un `_id` cohérent avec le
        `id=` fourni passe en revanche — c'est le round-trip normal (relire une ligne
        entière, la modifier, la repousser), et le refuser n'apprendrait rien à
        personne. Les autres colonnes de plateforme (`_created_at`, `_claimed_by`…)
        restent ignorées sans bruit pour la même raison : leur présence dans un
        round-trip est bénigne, elles ne DÉSIGNENT pas la cible de l'écriture."""
        if not isinstance(data, dict) or "_id" not in data:
            return
        posed = data.get("_id")
        if row_id is not None and str(posed) == str(row_id):
            return   # round-trip cohérent : l'intention est claire
        if batch:
            raise ValueError(
                f"`_id` ({posed!r}) dans une row du LOT : un batch dédouble par clé "
                "métier (`key`), il ne cible pas une ligne par son `_id`. Pour "
                "modifier UNE ligne précise, appelle data_write(id=…, row={…}) ; "
                "pour un lot, déclare la clé métier et laisse-la dédoublonner.")
        if row_id is None:
            raise ValueError(
                f"`_id` ({posed!r}) posé DANS `row` : il y serait ignoré et ton "
                "écriture INSÉRERAIT une nouvelle ligne au lieu de modifier "
                "celle-là. L'identifiant est un paramètre : data_write(id=" +
                f"{posed!r}, row={{…}}).")
        raise ValueError(
            f"`_id` ({posed!r}) dans `row` ne correspond pas au `id` visé "
            f"({row_id!r}) — deux cibles pour une écriture. Retire `_id` du corps : "
            "seul le paramètre `id` désigne la ligne.")

    def off_schema_report(self) -> dict:
        """Le relevé « hors schéma » du geste, prêt à fusionner dans une réponse
        d'écriture : `{}` quand tout est dans le format (le cas normal — pas de clé
        parasite dans la réponse), sinon la liste des champs + la phrase qui dit
        quoi en faire. Union sur un lot : un renommage fautif se voit une fois,
        pas une par row."""
        out: dict = {}
        keys = sorted(self.off_schema)
        if keys:
            out["hors_schema"] = keys
            out["hors_schema_hint"] = dsv2.off_schema_warning(keys)
        # #319 : les options déclarées mais inertes. Clé DISTINCTE de `hors_schema` —
        # ce n'est pas la même faute : là une colonne inconnue, ici une valeur hors
        # d'une liste que le schéma laissait croire fermée.
        if self.off_options:
            out["hors_options"] = dict(sorted(self.off_options.items()))
            out["hors_options_hint"] = dsv2.unenforced_options_warning(self.off_options)
        return out

    @staticmethod
    def _release_if_terminal(schema: Optional[dict], ns_id: int, row_id: str,
                             merged: dict) -> None:
        """Entrée dans un état terminal du cycle de vie ⇒ le bail de claim tombe
        (fin de traitement, façon log_exploration_attempt). Best-effort."""
        sf = dsv2.status_field(schema)
        if not sf:
            return
        if dsv2.is_terminal_status(schema, merged.get(sf.get("key"))):
            db.datastore_release_claim(ns_id, row_id, None)

    # --- namespace lifecycle -------------------------------------------------

    def _entry(self, n: dict, *, shared: bool, permission: Optional[str] = None) -> dict:
        ns_id = int(n["id"])
        perso = (self.sub is not None
                 and n.get("owner_type") == "user" and n.get("owner_id") == self.sub)
        # Agissant-org (sub-less) : pas de gouvernance via l'endpoint (create/delete/
        # rename/share restent réservés à un user identifié).
        can_govern = (False if self.acting_org is not None
                      else ownership.can_govern(self.sub, "datastore_namespace", str(ns_id)))
        return {
            "id": ns_id,
            "namespace": n["namespace"],
            "created_at": n.get("created_at"),
            "url": _ns_url(ns_id, self.sub),
            "shared": shared,
            "owner_type": n.get("owner_type"),
            "owner_id": n.get("owner_id"),
            "permission": permission if shared else "write",
            "can_write": (permission == "write") if shared else True,
            "can_govern": can_govern,
            "is_personal": perso,
            "schema": n.get("schema"),   # mode typé optionnel (ADR 0032 §6 / 0029, B6) ; None = table libre
        }

    def list_namespaces(self) -> list[dict]:
        """Namespaces visibles DANS L'ORG ACTIVE (l'org est le contexte, ADR 0023) :
        possédés par l'org active + accordés à elle ou à MES équipes dans cette org
        (grants d'org/groupe — tous mes groupes de l'org active, pas seulement le
        groupe actif : un partage d'équipe doit se voir sans basculer). Un namespace
        possédé par une AUTRE org — ou partagé à l'acteur *en propre* (grant user,
        cross-org) — ne fuite PLUS dans la vue d'une org tierce (scope décidé le
        2026-07-01). Dédupliqués par id (priorité possédé). La résolution PAR NOM
        (`_resolve`) scope désormais SUR LE MÊME contexte d'org (2026-07-03) : un
        namespace d'une autre org ne se résout plus hors de son org non plus."""
        from . import access
        if self.acting_org is not None:
            owner = ("org", str(self.acting_org))
        else:
            owner = ownership.active_owner(access.current_org(self.sub))
            if owner is None:
                return []
        # ADR 0049 (cadrage 10/07) : les tableaux TEAM-OWNED de l'org active sont listés
        # comme les org-owned. `_active_scope` est la source unique du jeu de groupes
        # (mes équipes, ou TOUS les groupes de l'org pour un org_admin — même règle que
        # `oto_project op=list`) ; le scope reste borné à l'org active.
        org_ids, group_ids = self._active_scope()
        owned = [owner] + [("group", str(g)) for g in group_ids]
        out: dict[int, dict] = {}
        for n in db.list_datastore_namespaces_for_owners(owned):
            out[int(n["id"])] = self._entry(n, shared=False)
        for n in db.list_datastore_namespaces_granted_to(self.sub, org_ids, group_ids):
            if int(n["id"]) in out:
                continue
            out[int(n["id"])] = self._entry(n, shared=True, permission=n.get("permission"))
        # Scope dur d'endpoint partagé : ne lister QUE les tableaux liés au projet.
        if self.allowed_ns_ids is not None:
            return [e for e in out.values() if int(e["id"]) in self.allowed_ns_ids]
        return list(out.values())

    def _default_owner(self) -> tuple[str, str]:
        """Owner d'un namespace créé sans précision = l'**org ACTIVE** (suppression du
        perso ; `current_org` toujours posé). Filet `user` si jamais None (ne devrait
        plus arriver)."""
        from . import access
        oid = access.current_org(self.sub)
        return ("org", str(oid)) if oid is not None else ("user", self.sub)

    def create_namespace(
        self, namespace: str, *, owner_type: Optional[str] = None, owner_id: Optional[str] = None,
    ) -> dict:
        """Crée un namespace. Défaut = **org active** de l'user (plus de perso). Pour un
        classeur d'org/groupe précis, passer `owner_type`/`owner_id` — l'autorisation
        (appartenance) est vérifiée par l'appelant (capacité/route)."""
        if owner_type is None:
            owner_type, owner_id = self._default_owner()
        oid = owner_id if owner_id is not None else self.sub
        try:
            ns_id = db.create_datastore_namespace(owner_type, oid, namespace)
        except ValueError as e:
            raise NamespaceExists(str(e))
        return {"namespace": namespace, "id": ns_id, "url": _ns_url(ns_id, self.sub)}

    def delete_namespace(self, namespace: str) -> None:
        ns_id = self._resolve(namespace)
        if not ownership.can_govern(self.sub, "datastore_namespace", str(ns_id)):
            raise NamespaceForbidden(namespace)
        db.delete_datastore_namespace_by_id(ns_id)  # rows + grants partent avec

    def rename_namespace(self, namespace: str, new_name: str) -> dict:
        """Renomme un namespace (l'id/URL/grants restent stables, keyés par id — cf.
        `db.rename_datastore_namespace_by_id`). Exige le droit de GOUVERNANCE, comme la
        suppression. Le nouveau nom doit être libre chez le même propriétaire (sinon
        `NamespaceExists`) — c'est ce qui lève la collision cross-org du gap #71 avant
        un transfert/merge."""
        ns_id = self._resolve(namespace)
        if not ownership.can_govern(self.sub, "datastore_namespace", str(ns_id)):
            raise NamespaceForbidden(namespace)
        new_name = (new_name or "").strip()
        try:
            db.rename_datastore_namespace_by_id(ns_id, new_name)
        except ValueError as e:
            raise NamespaceExists(str(e))
        return {"id": ns_id, "namespace": new_name, "url": _ns_url(ns_id, self.sub)}

    def resolve_ns_id(self, namespace: str) -> int:
        """ns_id d'un namespace visible par l'acteur (lève `NamespaceNotFound`).
        Surface publique pour les chemins de gouvernance (partage/transfert)."""
        return self._resolve(namespace)

    def resolve_ns_id_for_write(self, namespace: str) -> int:
        """ns_id d'un namespace où l'acteur peut ÉCRIRE (lève `NamespaceNotFound`/
        `NamespaceReadOnly`). Sert à sceller la cible d'un upload signé au mint (org
        active présente) ; l'autz est réappliquée au receive via `ownership.can_access`
        sur `datastore_namespace` (org-agnostique), sans contexte d'org."""
        return self._resolve(namespace, write=True)

    def get_url(self, namespace: str) -> str:
        return _ns_url(self._resolve(namespace), self.sub)  # 404 si inconnu

    # --- mode typé (ADR 0032 §6 / 0029, B6) ----------------------------------

    def get_schema(self, namespace: str) -> Optional[dict]:
        ns_id = self._resolve(namespace)
        ns = db.get_datastore_namespace_by_id(ns_id)
        return (ns or {}).get("schema")

    def set_schema(self, namespace: str, schema: Optional[dict]) -> dict:
        """Pose (ou retire si None) le schéma typé d'un namespace. Exige le droit
        d'écriture. SOFT pour les champs (schéma de rendu, pas de validation des
        rows) — SAUF `schema.key` (#109 ch.3) : la clé métier déclarée devient une
        CONTRAINTE (index UNIQUE partiel `data->>key`) → dédup concurrent-safe et
        lookup indexé. Des doublons existants sur la clé = REFUS actionnable (on ne
        pose pas un UNIQUE sur des données sales en silence)."""
        ns_id = self._resolve(namespace, write=True)
        if schema is not None and not isinstance(schema, dict):
            raise ValueError("schema doit être un objet {fields:[...]} ou null")
        def_errors = dsv2.validate_schema_def(schema)
        if def_errors:
            raise ValueError("schéma invalide : " + " ; ".join(def_errors))
        new_key = (schema or {}).get("key")
        new_key = new_key if isinstance(new_key, str) and new_key else None
        if new_key:
            dups = db.datastore_key_dup_groups(ns_id, new_key)
            if dups:
                sample = ", ".join(f"{d['value']!r}×{d['n']}" for d in dups[:5])
                raise ValueError(
                    f"schema.key='{new_key}' refusée : {len(dups)}+ valeurs en DOUBLON "
                    f"dans les rows existantes (ex. {sample}). Résorbe-les d'abord "
                    f"(data_write avec key='{new_key}' merge les doublons, ou supprime "
                    "les rows en trop), puis re-déclare la clé.")
        db.set_datastore_schema(ns_id, schema)
        if new_key:
            db.datastore_ensure_key_index(ns_id, new_key)
        else:
            db.datastore_drop_key_index(ns_id)
        out = {"namespace": namespace, "schema": schema}
        # Un statut sans état terminal = file de travail qui ne libère rien : le dire
        # ICI, à l'auteur du schéma, au moment où il le pose (les deux faces l'ont).
        warnings = [w for w in (dsv2.queue_release_warning(schema),
                                # Clés de déclaration qu'oto n'interprète PAS (#316) :
                                # posées, stockées, rendues fidèlement… et jamais lues.
                                # Le cas réel : `enum:` au lieu d'`options:` sur trois
                                # champs — 504 valeurs libres sur un tableau qui se
                                # croyait contraint, sans le moindre signal. On ne
                                # refuse pas (les consommateurs posent leurs propres
                                # déclarations, que le datastore transporte), on DIT.
                                dsv2.unknown_keys_warning(
                                    dsv2.unknown_declaration_keys(schema)),
                                # #319 : des options déclarées mais qu'aucun régime ne
                                # fait respecter — dit AU MOMENT où on pose le schéma,
                                # pas six semaines plus tard devant des valeurs libres.
                                dsv2.options_not_enforced_warning(
                                    dsv2.options_not_enforced(schema)),
                                # Et le fait, sur les champs `json` : stockés, rendus,
                                # mais pas interrogeables en profondeur.
                                dsv2.json_depth_warning(dsv2.json_fields_depth(schema)),
                                self._overlong_warning(ns_id, schema),
                                self._offending_enum_warning(ns_id, schema),
                                self._orphan_columns_warning(ns_id, schema)) if w]
        if warnings:
            out["warning"] = "\n".join(warnings)
        return out

    def patch_schema(self, namespace: str, *, fields: Optional[list] = None,
                     remove: Optional[list] = None,
                     strict: Optional[bool] = None,
                     key: Optional[str] = None) -> dict:
        """Modifie le schéma PAR CLÉ, sans réécrire la liste entière (#388).

        `data_set_schema` REMPLACE : c'est le bon geste pour poser un format, et un
        piège pour le retoucher. Deux appels indiscernables — même méthode, même
        succès, même réponse — n'ont pas le même effet selon que l'appelant a patché
        en mémoire ou reconstruit la liste : le premier a préservé 78 notes de champ,
        le second a détruit un `pattern` et un `max_length`, et 52 notes ont disparu
        entre deux sessions par le même mécanisme. Rien dans la réponse ne le disait.
        Un avertissement n'aurait pas suffi : personne ne lit un avertissement sur un
        appel qui réussit. Il faut un geste qui ne PEUT pas détruire.

        `fields` = fusion par clé (complète l'existant, ajoute l'inconnu) ; `remove`
        = le retrait EXPLICITE, sans quoi on rendrait le nettoyage impossible ;
        `strict`/`key` = les clés de tête, inchangées si omises. Le schéma résultant
        repasse par `set_schema`, donc par ses gardes (doublons de clé métier, index
        UNIQUE) et ses avertissements (file de travail, bornes, colonnes orphelines)
        — on ne double pas cette logique."""
        ns_id = self._resolve(namespace, write=True)
        current = self._schema_of(ns_id) or {}
        if not isinstance(current, dict):
            raise ValueError("le schéma courant n'est pas un objet — repose-le avec "
                             "data_set_schema avant de le patcher")
        if fields is None and remove is None and strict is None and key is None:
            raise ValueError(
                "rien à patcher : passe `fields` (fusion par clé), `remove` (retrait "
                "explicite), `strict` ou `key`")
        merged = [f for f in (current.get("fields") or []) if isinstance(f, dict)]
        merged, added, updated = dsv2.merge_fields(merged, fields or [])
        merged, unknown = dsv2.remove_fields(merged, remove or [])
        if unknown:
            raise ValueError(
                "`remove` nomme des champs que le schéma ne déclare pas : "
                + ", ".join(f"`{k}`" for k in unknown)
                + ". Rien n'a été touché — vérifie l'orthographe (data_get_schema). "
                "Pour effacer la COLONNE des données, c'est data_drop_column.")
        out_schema = {**current, "fields": merged}
        if strict is not None:
            out_schema["strict"] = bool(strict)
        if key is not None:
            out_schema["key"] = key
        result = self.set_schema(namespace, out_schema)
        return {**result, "added": added, "updated": updated,
                "removed": [str(k) for k in (remove or [])]}

    @staticmethod
    def _orphan_columns_warning(ns_id: int, schema: Optional[dict]) -> Optional[str]:
        """Des colonnes vivent dans les DONNÉES sans être déclarées au schéma qu'on
        vient de poser : le dire ici, à l'auteur du renommage (#296).

        C'est le moment utile — renommer `actualite_sociale` en `analyse1` sort
        l'ancien nom de la vue, mais la clé reste dans chaque ligne. Elle continue
        de se rendre à la lecture, et son nom décrit souvent le contenu mieux que le
        nouveau : trois agents successifs ont écrit dedans en la prenant pour la
        bonne cible. Le silence à la pose du schéma est ce qui laisse le piège armé.

        Strict seulement : sur un schéma souple, un champ libre est un droit du
        contrat (0016) — la table qu'on explore avant de la typer en est pleine, et
        signaler y serait du bruit sur un usage normal."""
        if not isinstance(schema, dict) or not schema.get("strict"):
            return None
        declared = {f.get("key") for f in dsv2._fields(schema)}
        orphans = [k for k in db.datastore_row_keys(ns_id) if k not in declared]
        if not orphans:
            return None
        noms = ", ".join(f"`{k}`" for k in orphans)
        return (f"colonnes présentes dans les données mais PAS dans ce schéma : {noms}. "
                "Elles se rendent encore à la lecture — après un renommage, leur nom "
                "décrit souvent le contenu mieux que le nouveau, et un agent qui relit "
                "une ligne écrit dedans en croyant viser juste. Purge-les "
                "(`data_drop_column(namespace, key, confirm=True)`) ou déclare-les.")

    @staticmethod
    def _overlong_warning(ns_id: int, schema: Optional[dict]) -> Optional[str]:
        """Des rows existantes dépassent déjà une borne `max_length` fraîchement
        posée : le dire à celui qui la pose (#383). Pas un refus — la borne vaut
        pour ce qu'on ÉCRIT, l'historique n'est refusé qu'au geste qui le réécrit —
        mais un silence ferait croire la table conforme."""
        bounds = dsv2.top_level_bounds(schema)
        if not bounds:
            return None
        over = db.datastore_overlong_fields(ns_id, bounds)
        if not over:
            return None
        detail = ", ".join(
            f"`{o['field']}` : {o['rows']} ligne(s) jusqu'à {o['longest']} car. "
            f"(max {o['max_length']})" for o in over)
        return (f"borne posée sur des données déjà hors borne — {detail}. Les "
                "écritures futures sont refusées, ces lignes-là restent en place "
                "jusqu'à ce qu'on réécrive le champ (un patch d'un AUTRE champ "
                "passe).")

    @staticmethod
    def _offending_enum_warning(ns_id: int, schema: Optional[dict]) -> Optional[str]:
        """Des rows existantes portent une valeur qu'un enum fraîchement déclaré
        condamne : le dire à celui qui le déclare.

        Un schéma ne vaut que pour l'AVENIR — le poser ne revalide pas l'existant.
        Or l'ordre normal des choses est d'écrire d'abord et de formaliser ensuite :
        au moment où le format arrive, la table est déjà pleine. Sans cet
        avertissement elle *paraît* conforme (elle a un schéma) tout en contenant
        des valeurs invisibles au filtrage et aux facettes. Vécu : 504 lignes en
        « Oui »/« Non » sur un enum `oui`/`non`/`inconnu`.

        AVERTIT, ne refuse pas : refuser rendrait impossible de déclarer un format
        sur un tableau existant, c'est-à-dire le cas normal. Et rend les valeurs
        fautives avec leur compte — c'est ce qui permet de choisir entre corriger la
        donnée et élargir les options, là où un total nu laisse chercher."""
        # Gate = la validation sera-t-elle ACTIVE ? On avertit exactement quand les
        # écritures futures seront refusées. Sur un schéma souple, l'enum ne
        # condamne rien (validation opt-in, 0016) : signaler l'existant y annoncerait
        # un refus qui n'aura pas lieu — un faux avertissement coûte la confiance
        # qu'on met dans les vrais.
        if not dsv2.validation_active(schema):
            return None
        options = dsv2.top_level_enum_options(schema)
        if not options:
            return None
        bad = db.datastore_offending_enum_values(ns_id, options)
        if not bad:
            return None
        detail = "\n".join(
            f"  `{b['field']}` : {b['rows']} ligne(s) hors options — "
            + ", ".join(f"« {v['value']} » ({v['rows']})" for v in b["values"])
            + (f", et {b['distinct'] - len(b['values'])} autre(s) valeur(s)"
               if b["distinct"] > len(b["values"]) else "")
            + f"  [options : {', '.join(options[b['field']])}]"
            for b in bad)
        return ("enum déclaré sur des données qui en sortent déjà :\n" + detail +
                "\nCes lignes restent en place et resteront INVISIBLES au filtrage "
                "et aux facettes. Corrige-les (réécris le champ) ou élargis les "
                "options ; les écritures futures, elles, sont refusées.")

    def drop_column(self, namespace: str, key: str, *, confirm: bool) -> dict:
        """Retire une colonne des DONNÉES de toutes les rows (#296). Destructif et
        irréversible : `confirm=True` exigé — la garde vit ICI, pas dans la surface,
        pour qu'aucune face ne puisse l'oublier. Exige le droit d'écriture (même
        palier que `set_schema` : c'est le même geste, la forme de la table).

        Retirer un champ du schéma le sort de la vue mais laisse la clé dans chaque
        ligne, où elle continue de se rendre — et d'attirer les écritures. Mettre la
        valeur à `null` ne l'efface pas non plus (une clé nulle reste une clé). D'où
        ce geste, le seul qui fasse disparaître la colonne.

        REFUSE une clé encore DÉCLARÉE au schéma : un `confirm` ne protège pas d'une
        faute de nom, et l'échappatoire est le geste naturel du renommage — retirer
        d'abord le champ du schéma. Ainsi la purge ne peut viser qu'une colonne dont
        le format a déjà acté la sortie."""
        key = (key or "").strip()
        if not key:
            raise ValueError("key requise (le nom de la colonne à purger)")
        if key in _META_COLS:
            raise ValueError(
                f"`{key}` est une colonne gérée par la plateforme, pas une donnée")
        if not confirm:
            raise ValueError(
                f"purge de la colonne `{key}` non confirmée — c'est irréversible sur "
                "toutes les lignes : rappelle l'appel avec confirm=True")
        ns_id = self._resolve(namespace, write=True)
        schema = self._schema_of(ns_id)
        if key in {f.get("key") for f in dsv2._fields(schema)}:
            raise ValueError(
                f"`{key}` est encore DÉCLARÉE au schéma de `{namespace}` : purger une "
                "colonne vivante est presque toujours une faute de nom. Si la sortie "
                "est voulue, retire d'abord le champ du schéma (data_set_schema), puis "
                "purge.")
        rows = db.datastore_drop_column(ns_id, key)
        return {"namespace": namespace, "key": key, "rows": rows}

    def set_semantic(self, namespace: str, enabled: bool) -> dict:
        """Active/désactive la recherche SÉMANTIQUE des lignes du namespace (#67 V2.2,
        opt-in — coût d'embedding). Exige le droit d'écriture. À l'activation, les rows
        sont mises en file d'indexation (worker) ; à la désactivation, leurs embeddings
        sont purgés."""
        ns_id = self._resolve(namespace, write=True)
        queued = db.set_datastore_semantic(ns_id, bool(enabled))
        return {"namespace": namespace, "semantic_search": bool(enabled), "rows_queued": queued}

    # --- row ops -------------------------------------------------------------

    def append_row(self, namespace: str, data: dict, *,
                   trace: Optional[dict] = None) -> dict:
        """Écrit UNE row. Si le namespace déclare une clé métier (`schema.key`),
        applique la MÊME dédup upsert que le batch `write_rows` : une row de même
        valeur de clé est MERGÉE (pas de doublon, l'index `ds_bkey_<ns>` la refuse) ;
        sinon append. Renvoie la row (nouvelle ou mise à jour).

        `trace` (dict mutable, optionnel) = relevé pour le journal, cf. `_trace`."""
        self._reject_misplaced_id(data, None)
        ns_id = self._resolve(namespace, write=True)
        user_data = {k: v for k, v in data.items() if k not in _META_COLS}
        ns = self._ns_of(ns_id)
        schema = ns.get("schema")
        self._trace(trace, ns_id, ns)
        # La clé métier sort du MÊME schéma que ci-dessus (`declared_key` re-résolvait
        # le namespace et relisait la ligne pour le même résultat).
        key = self._declared_key_of(schema)
        kv = user_data.get(key) if key else None
        if key and kv is not None and str(kv) != "":
            existing_id = db.datastore_find_row_id_by_key(ns_id, key, kv)
            if existing_id is not None:
                return self._row_to_dict(
                    self._merge_into_row(ns_id, existing_id, user_data, schema=schema))
        self._check_row(schema, user_data)
        try:
            row = db.datastore_insert_row(ns_id, _new_id(), user_data)
        except UniqueViolation:
            # Course perdue sous l'index UNIQUE de clé métier (#109 ch.3) : un write
            # concurrent a inséré la même clé entre le lookup et l'insert — le doublon
            # que la contrainte empêche. On converge en merge (même chemin que le batch).
            existing_id = (db.datastore_find_row_id_by_key(ns_id, key, kv)
                           if key and kv is not None else None)
            if existing_id is None:
                raise  # violation inexpliquée → erreur franche, pas de repli muet
            return self._row_to_dict(
                self._merge_into_row(ns_id, existing_id, user_data, schema=schema))
        return self._row_to_dict(row)

    def _merge_into_row(self, ns_id: int, row_id: str, user_data: dict,
                        *, schema: Optional[dict] = None) -> dict:
        """MERGE `user_data` dans la row existante (dernier écrit gagne par champ),
        en appliquant le schéma v2 (ADR 0046) au résultat mergé : validation avec
        `prev_status` (transition de lifecycle) puis release du claim si l'état
        devient terminal. Renvoie la row brute persistée. Corps commun à l'append
        unitaire et au batch.

        Le read-merge-write est ATOMIQUE (verrou de ligne, #197) : le get + le
        merge + l'update tournent dans une seule transaction `FOR UPDATE`, sinon
        deux writes concurrents de la même clé (même row_id) s'écrasaient
        mutuellement (last-writer-wins) et perdaient des champs silencieusement."""
        if schema is None:
            schema = self._schema_of(ns_id)
        sk = (dsv2.status_field(schema) or {}).get("key")

        def _apply(current: dict) -> dict:
            merged = dict(current or {})
            prev_status = merged.get(sk) if sk else None
            # Colonne par colonne, pour que l'origine survive à une écriture
            # ordinaire. Un `update` en bloc l'emporterait avec le reste — et
            # silencieusement, puisque remplacer une valeur est le geste normal.
            for _k, _v in user_data.items():
                merged[_k] = _merge_column(merged.get(_k), _v)
            self._check_row(schema, merged, prev_status=prev_status,
                            written=set(user_data))
            return merged

        result = db.datastore_merge_row_locked(ns_id, row_id, _apply, _now_iso())
        if result is None:
            raise RowNotFound(row_id)  # supprimée entre le lookup et le verrou (course)
        row, merged = result
        self._release_if_terminal(schema, ns_id, row_id, merged)
        return row

    def upsert_row(self, namespace: str, row_id: str, data: dict) -> tuple[dict, bool]:
        """Écrit une row à une clé `row_id` EXPLICITE (≠ append_row qui génère un
        id), en remplaçant si elle existe. Crée le namespace au besoin. Sert le
        stockage dédupliqué par clé stable (ex. urn LinkedIn). Renvoie
        `(row, inserted)` — `inserted` False = la row existait déjà."""
        self._reject_misplaced_id(data, row_id)
        try:
            ns_id = self._resolve(namespace, write=True)
        except NamespaceNotFound:
            _ot, _oid = self._default_owner()
            db.create_datastore_namespace(_ot, _oid, namespace)
            self._active_scope_cache = None  # invalide le cache (le ns créé est dans l'org active)
            ns_id = self._resolve(namespace, write=True)
        user_data = {k: v for k, v in data.items() if k not in _META_COLS}
        schema = self._schema_of(ns_id)
        if dsv2.validation_active(schema) or dsv2.lifecycle_of(schema):
            prev = db.datastore_get_row(ns_id, row_id)  # remplacement intégral → prev pour la transition
            sk = (dsv2.status_field(schema) or {}).get("key")
            prev_status = ((prev or {}).get("data") or {}).get(sk) if sk else None
            self._check_row(schema, user_data, prev_status=prev_status)
        row, inserted = db.datastore_upsert_row(ns_id, row_id, user_data)
        if not inserted:
            self._release_if_terminal(schema, ns_id, row_id, user_data)
        return self._row_to_dict(row), inserted

    def declared_key(self, namespace: str) -> Optional[str]:
        """Clé métier déclarée au schéma (`schema.key`) — sert la dédup au batch
        write. None si aucune (table libre / schéma sans clé)."""
        return self._declared_key_of(self.get_schema(namespace))

    def write_rows(self, namespace: str, rows: list, *, key: Optional[str] = None) -> dict:
        """Écrit un LOT de rows en un appel. Si une clé métier est en vigueur (param
        `key` explicite, sinon `schema.key` déclarée), chaque row qui la porte fait un
        UPSERT (merge) sur la row existante de même valeur de clé — pas de doublon ;
        sinon append d'une nouvelle row. Renvoie un récap {inserted, updated, count,
        key, ids}. Résout le namespace UNE fois (write) pour tout le lot."""
        ns_id = self._resolve(namespace, write=True)
        return self._write_rows_to_ns(ns_id, rows, key=key or self.declared_key(namespace))

    def _write_rows_to_ns(self, ns_id: int, rows: list, *, key: Optional[str]) -> dict:
        """Cœur du batch, keyé par `ns_id` déjà résolu (réutilisable hors contexte
        d'org — matérialisation d'un upload signé, où l'org de session est absente).
        Le schéma v2 (validation/lifecycle, ADR 0046) s'applique à CHAQUE row du
        lot, sur son résultat mergé — une row fautive fait échouer le lot avec le
        champ en cause (pas d'écriture partielle silencieuse au-delà)."""
        schema = self._schema_of(ns_id)
        inserted, updated, ids = 0, 0, []
        for data in rows:
            if not isinstance(data, dict):
                raise ValueError("chaque row doit être un objet")
            self._reject_misplaced_id(data, None, batch=True)
            user_data = {k: v for k, v in data.items() if k not in _META_COLS}
            kv = user_data.get(key) if key else None
            if key and kv is not None and str(kv) != "":
                existing_id = db.datastore_find_row_id_by_key(ns_id, key, kv)
                if existing_id is not None:
                    self._merge_into_row(ns_id, existing_id, user_data, schema=schema)
                    updated += 1
                    ids.append(existing_id)
                    continue
            self._check_row(schema, user_data)
            try:
                row = db.datastore_insert_row(ns_id, _new_id(), user_data)
            except UniqueViolation:
                # Course perdue sous l'index UNIQUE de clé métier (#109 ch.3) : un
                # write concurrent vient d'insérer la même clé entre le lookup et
                # l'insert — c'est PRÉCISÉMENT le doublon que la contrainte empêche.
                # On converge en update (même merge que le chemin nominal). La clé
                # violée est la clé DÉCLARÉE du namespace (l'index ne porte qu'elle),
                # qui peut différer d'un `key` explicite passé à l'appel.
                dk = ((db.get_datastore_namespace_by_id(ns_id) or {}).get("schema")
                      or {}).get("key")
                dkv = user_data.get(dk) if dk else None
                existing_id = (db.datastore_find_row_id_by_key(ns_id, dk, dkv)
                               if dk and dkv is not None else None)
                if existing_id is None:
                    raise  # violation inexpliquée → erreur franche, pas de repli muet
                self._merge_into_row(ns_id, existing_id, user_data, schema=schema)
                updated += 1
                ids.append(existing_id)
                continue
            inserted += 1
            ids.append(row["row_id"])
        return {"inserted": inserted, "updated": updated, "count": inserted + updated,
                "key": key, "ids": ids}

    def get_row(self, namespace: str, row_id: str) -> dict:
        ns_id = self._resolve(namespace)
        row = db.datastore_get_row(ns_id, row_id)
        if not row:
            raise RowNotFound(row_id)
        return self._row_to_dict(row)

    def list_rows(
        self,
        namespace: str,
        filter: Optional[dict] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Filtre exact k:v en Python (chemin MCP `data_rows`). Ordre stable plus
        ancien d'abord (compat historique)."""
        ns_id = self._resolve(namespace)
        out: list[dict] = []
        for row in db.datastore_list_rows(ns_id, order_by="_created_at", order_dir="asc"):
            record = self._row_to_dict(row)
            if filter and not all(str(record.get(k)) == str(v) for k, v in filter.items()):
                continue
            out.append(record)
            if len(out) >= limit:
                break
        return out

    def cursor_rows(
        self,
        namespace: str,
        *,
        filter: Optional[dict] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
        q: Optional[str] = None,
        order_by: Optional[str] = None,
        order_dir: str = "desc",
    ) -> dict:
        """Page pour l'agent (chemin MCP `data_rows`), filtre/recherche/tri poussés en
        SQL. Renvoie `{rows, next_cursor}` — `next_cursor` non nul ⇒ il reste des lignes
        (repasse-le pour la suite).

        Deux régimes de pagination, et le curseur porte lequel :
          - **sans `order_by`** (défaut) → keyset sur `row_id` = ordre de création,
            robuste aux écritures concurrentes (pas d'OFFSET qui dérive) ;
          - **avec `order_by`** → tri SQL demandé + pagination par offset, faute de clé
            keyset stable pour un tri arbitraire.

        Repasser le curseur d'un régime dans l'autre lève `InvalidCursor` plutôt que de
        rendre une page fausse — un curseur d'offset relu comme un `row_id` cadrerait
        silencieusement sur les mauvaises lignes."""
        ns_id = self._resolve(namespace)
        filters = _filter_specs(filter)
        if order_by:
            offset = _decode_offset_cursor(cursor) if cursor else 0
            rows = db.datastore_list_rows(
                ns_id, offset=offset, limit=limit, order_by=order_by,
                order_dir=order_dir, q=q, filters=filters)
            next_cursor = (_encode_offset_cursor(offset + len(rows))
                           if len(rows) == limit else None)
            return {"rows": [self._row_to_dict(r) for r in rows],
                    "next_cursor": next_cursor}
        after = _decode_cursor(cursor) if cursor else None
        if after and after.startswith(_OFFSET_CURSOR_PREFIX):
            raise InvalidCursor(cursor)  # curseur trié repassé sans `order_by`
        rows = db.datastore_list_rows_after(
            ns_id, after_row_id=after, limit=limit, q=q, filters=filters)
        out = [self._row_to_dict(r) for r in rows]
        next_cursor = _encode_cursor(rows[-1]["row_id"]) if len(rows) == limit else None
        return {"rows": out, "next_cursor": next_cursor}

    def count_rows(self, namespace: str, *, filter: Optional[dict] = None,
                   q: Optional[str] = None) -> int:
        """Nombre de lignes (mêmes `filter`/`q` que `cursor_rows`), poussé en SQL
        (`COUNT(*)`) — sans rapatrier les lignes (feedback #191 : stats d'un gros
        vivier sans charger 300+ lignes en contexte)."""
        ns_id = self._resolve(namespace)
        return db.datastore_count_rows(ns_id, q=q, filters=_filter_specs(filter))

    def aggregate(self, namespace: str, *, group_by: Optional[str] = None,
                  metrics: Optional[list] = None, filter: Optional[dict] = None,
                  q: Optional[str] = None, filters: Optional[list] = None) -> list[dict]:
        """Agrégat serveur (feedback #191) : COUNT/SUM/AVG/MIN/MAX sur des champs JSONB,
        `group_by` optionnel — stats d'un vivier sans rapatrier les lignes. Délègue à
        `db.datastore_aggregate`. Deux formes de filtre cumulables : `filter` exact
        `{col: val}` (chemin MCP) et `q`/`filters` riches ({field, op, value}, mêmes
        clauses que `page_rows`) — le dashboard agrège ainsi le MÊME jeu que sa vue
        filtrée (tuiles metric)."""
        ns_id = self._resolve(namespace)
        clauses = [{"field": k, "op": "eq", "value": v} for k, v in (filter or {}).items()]
        clauses.extend(filters or [])
        return db.datastore_aggregate(
            ns_id, group_by=group_by, metrics=metrics, q=q, filters=clauses)

    def page_rows(
        self,
        namespace: str,
        *,
        offset: int = 0,
        limit: int = 50,
        order_by: Optional[str] = None,
        order_dir: str = "desc",
        q: Optional[str] = None,
        filter: Optional[dict] = None,
        filters: Optional[list] = None,
    ) -> dict:
        """Page server-side (tri/recherche/filtres SQL) + total — pour le dashboard.
        Deux formes de filtre CUMULABLES, comme `aggregate` : `filter` exact
        `{col: val}` (chemin MCP, et la CLI `--filter`) et `filters` riches
        (liste `{field, op, value}`, combinées en ET). Renvoie
        `{rows, total, offset, limit}`.

        `filter` manquait ici alors que `cursor_rows`, `aggregate` et `claim_next`
        le portent : la face REST du même verbe ignorait donc **en silence** un
        paramètre que la face MCP honore (#303)."""
        ns_id = self._resolve(namespace)
        clauses = [{"field": k, "op": "eq", "value": v} for k, v in (filter or {}).items()]
        clauses.extend(filters or [])
        clauses = clauses or None
        rows = db.datastore_list_rows(
            ns_id, offset=offset, limit=limit, order_by=order_by,
            order_dir=order_dir, q=q, filters=clauses)
        return {
            "rows": [self._row_to_dict(r) for r in rows],
            # Le total doit décrire le MÊME jeu que la page : filtré aussi, sinon la
            # pagination du dashboard annonce des lignes qu'elle ne servira jamais.
            "total": db.datastore_count_rows(ns_id, q=q, filters=clauses),
            "offset": offset, "limit": limit,
        }

    def update_row(self, namespace: str, row_id: str, patch: dict, *,
                   trace: Optional[dict] = None) -> dict:
        """Patch partiel d'une row. `trace` (dict mutable, optionnel) = relevé pour
        le journal — dont l'état AVANT, celui-là même sur lequel la transition de
        cycle de vie est validée juste en dessous (cf. `_trace`)."""
        self._reject_misplaced_id(patch, row_id)
        ns_id = self._resolve(namespace, write=True)
        existing = db.datastore_get_row(ns_id, row_id)
        if not existing:
            raise RowNotFound(row_id)
        data = dict(existing.get("data") or {})
        ns = self._ns_of(ns_id)
        schema = ns.get("schema")
        status_key = (dsv2.status_field(schema) or {}).get("key")
        prev_status = data.get(status_key) if status_key else None
        self._trace(trace, ns_id, ns, prev_status=prev_status)
        written = set()
        for k, v in patch.items():
            if k in _META_COLS:
                continue
            data[k] = v
            written.add(k)
        # Validation sur le RÉSULTAT mergé (un patch partiel ne doit pas échouer
        # sur un requis déjà présent) + transition de cycle de vie (ADR 0046 B/C).
        # Seule la borne de longueur se limite aux clés du patch (#383).
        self._check_row(schema, data, prev_status=prev_status, written=written)
        try:
            row = db.datastore_update_row(ns_id, row_id, data, _now_iso())
        except UniqueViolation:
            # Un AUTRE enregistrement porte déjà cette valeur de clé métier (index
            # UNIQUE ds_bkey_<ns_id>). Contrairement au batch write (qui converge en
            # merge sur la row de même clé), un update ciblé sur `row_id` ne peut pas
            # basculer silencieusement sur une autre row → erreur actionnable
            # (ValueError → INVALID_PARAMS), jamais un 500 opaque.
            dk = (schema or {}).get("key")
            dkv = data.get(dk) if dk else None
            if dk and dkv is not None:
                raise ValueError(
                    f"un autre enregistrement porte déjà {dk}={dkv} "
                    "(clé métier unique) — impossible de dupliquer") from None
            raise  # violation inexpliquée → erreur franche, pas de repli muet
        self._release_if_terminal(schema, ns_id, row_id, data)
        return self._row_to_dict(row)

    # --- file de travail (ADR 0046 D) -----------------------------------------

    def claim_next(self, namespace: str, *, worker: str,
                   filter: Optional[dict] = None, lease_s: int = 900,
                   warnings: Optional[list] = None,
                   trace: Optional[dict] = None) -> Optional[dict]:
        """Pick + claim atomique de la prochaine row claimable (bail NULL ou
        expiré), `FOR UPDATE SKIP LOCKED` — N workers drainent sans collision.
        `filter` = filtre exact `{col: val}` (ex. {"status": "nouveau"}). Renvoie
        la row (avec `_claimed_by`/`_claimed_until`) ou None (file vide).

        `warnings` = liste OUT (patron `trace`) où est déposé, le cas échéant, le
        défaut de configuration qui rend l'auto-release inopérante — le worker qui
        claim est celui que ça concerne, et il peut alors libérer explicitement."""
        worker = (worker or "").strip()
        if not worker:
            raise ValueError("worker requis (libellé stable rejoué sur release)")
        ns_id = self._resolve(namespace, write=True)
        filters = [{"field": k, "op": "eq", "value": v} for k, v in (filter or {}).items()]
        row = db.datastore_claim_next(ns_id, worker=worker,
                                      lease_seconds=int(lease_s), filters=filters)
        if row is not None:
            self._after_claim(ns_id, warnings=warnings, trace=trace)
        return self._row_to_dict(row) if row else None

    def claim_row(self, namespace: str, row_id: str, *, worker: str,
                  lease_s: int = 900, warnings: Optional[list] = None,
                  trace: Optional[dict] = None) -> dict:
        """Réserve une row NOMMÉE — la file pilotée par un humain (il choisit qui
        appeler), là où `claim_next` sert un worker qui draine.

        Même bail, même garde au release. Renouvelable par le même `worker` (un
        rafraîchissement d'écran ne perd pas la ligne). Lève `RowNotFound` (row
        absente) ou `RowClaimed` (bail actif d'un autre) — la distinction est ce
        que la surface doit dire à l'utilisateur, un `None` commun ne le peut pas."""
        worker = (worker or "").strip()
        if not worker:
            raise ValueError("worker requis (libellé stable rejoué sur release)")
        ns_id = self._resolve(namespace, write=True)
        row = db.datastore_claim_row(ns_id, row_id, worker=worker, lease_seconds=int(lease_s))
        if row is None:
            existing = db.datastore_get_row(ns_id, row_id)
            if not existing:
                raise RowNotFound(row_id)
            raise RowClaimed(row_id, existing.get("claimed_by"), existing.get("claimed_until"))
        self._after_claim(ns_id, warnings=warnings, trace=trace)
        return self._row_to_dict(row)

    def _after_claim(self, ns_id: int, *, warnings: Optional[list],
                     trace: Optional[dict]) -> None:
        """Relevés communs aux deux claims, sur un ns_id DÉJÀ résolu : le défaut de
        configuration qui rend l'auto-release inopérante, et le contexte de journal.
        Une seule lecture de la ligne namespace pour les deux."""
        if warnings is None and trace is None:
            return
        ns = self._ns_of(ns_id)
        if warnings is not None:
            w = dsv2.queue_release_warning(ns.get("schema"))
            if w:
                warnings.append(w)
        self._trace(trace, ns_id, ns)

    def release_claim(self, namespace: str, row_id: str, *, worker: str,
                      trace: Optional[dict] = None) -> bool:
        """Libère le bail (abandon sans verdict). Gardé par `worker` — on ne
        libère pas le claim d'un autre. L'entrée dans un état terminal libère
        déjà automatiquement (pas besoin d'appeler release après)."""
        ns_id = self._resolve(namespace, write=True)
        if trace is not None:
            self._trace(trace, ns_id, self._ns_of(ns_id))
        return db.datastore_release_claim(ns_id, row_id, str(worker))

    def queue(self, namespace: str) -> list[dict]:
        """Vue de SUPERVISION de la file (dashboard) : les rows sous bail —
        actif ou expiré, le consommateur tranche sur `_claimed_until`. Lecture
        seule (aucun droit d'écriture requis)."""
        ns_id = self._resolve(namespace)
        return [self._row_to_dict(r) for r in db.datastore_claimed_rows(ns_id)]

    def force_release(self, namespace: str, row_id: str, *,
                      trace: Optional[dict] = None) -> bool:
        """Libère le bail SANS garde de worker — supervision humaine (dashboard),
        ≠ `release_claim` (agent, gardé). Exige le droit d'écriture. False = pas
        de bail à libérer."""
        ns_id = self._resolve(namespace, write=True)
        if trace is not None:
            self._trace(trace, ns_id, self._ns_of(ns_id))
        return db.datastore_release_claim(ns_id, row_id, None)

    def delete_row(self, namespace: str, row_id: str, *,
                   trace: Optional[dict] = None) -> None:
        ns_id = self._resolve(namespace, write=True)
        if trace is not None:
            # Relevé demandé : on lit l'état de la row DANS le chemin de suppression
            # (au plus près du delete), jamais par un `get_row` séparé côté route —
            # qui re-résoudrait le namespace et courrait avec un write concurrent.
            ns = self._ns_of(ns_id)
            sk = (dsv2.status_field(ns.get("schema")) or {}).get("key")
            prev = ((db.datastore_get_row(ns_id, row_id) or {}).get("data") or {}) if sk else {}
            self._trace(trace, ns_id, ns, prev_status=prev.get(sk) if sk else None)
        if not db.datastore_delete_row(ns_id, row_id):
            raise RowNotFound(row_id)
