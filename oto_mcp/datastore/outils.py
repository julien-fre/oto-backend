"""Les fonctions LIBRES du store : curseurs, horodatage, identifiant de ligne,
clauses de filtre, adresse du tableau, et les deux phrases de refus qui ne dépendent
d'aucune instance.

Extrait de `core.py` (#325 puis 07/09/2026), déplacement pur — le noyau d'identité
reste là-bas, ce qui n'a besoin ni de `self` ni d'une connexion sort ici. `core.py`
les ré-exporte : `from datastore.core import indice_de_liberation` reste vrai.
"""
from __future__ import annotations

import base64
import binascii
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from ..db.query import ds_filter_specs as _filter_specs
from . import charge_a_renvoyer as car
from .declaration import champ_declare
from .errors import BusinessKeyRequired, InvalidCursor
from .phrases_de_refus import gabarit
from .reserves import iso_utc


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


def _filter_clauses(filter: Optional[dict], filters: Optional[list]) -> list[dict]:
    """Les DEUX formes de filtre d'un même appel, réunies en une liste de clauses.

    `filter` = le raccourci `{colonne: valeur}` (une colonne à la fois) ; `filters` =
    la forme complète `[{field|fields, op, value, match?}]`, seule capable de viser
    plusieurs colonnes déclarées (oto#22). Elles se cumulent en ET.

    Point unique, et il a coûté un défaut : `aggregate` et `page_rows` recopiaient la
    conversion en la simplifiant en égalité, si bien qu'un opérateur imbriqué —
    `{"posted_at": {"gte": "2026-06-01"}}`, la forme que la fiche de `data_rows`
    documente — y comparait la colonne au TEXTE d'un dictionnaire Python. Zéro ligne,
    aucune erreur : la même syntaxe répondait juste sur un verbe et faux sur l'autre.
    """
    return _filter_specs(filter) + list(filters or [])


def _now_iso() -> str:
    # Même forme que toute estampille posée par la plateforme (#859) : plusieurs
    # sources d'une date en rendaient plusieurs, et un tri les rangeait par
    # l'alphabet. La règle vit à UN endroit — `reserves.iso_utc` — pour qu'elles
    # ne puissent plus diverger.
    return iso_utc(datetime.now(timezone.utc))


def _new_id() -> str:
    # uuid7-ish : timestamp ms + random. Construit à la main pour compat 3.10+.
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = uuid.uuid4().int & ((1 << 74) - 1)
    raw = (ms << 80) | (0x7 << 76) | (rand << 2)
    return str(uuid.UUID(int=raw))


def _ns_url(ns_id: int, sub: Optional[str] = None,
            org: Optional[int] = None) -> Optional[str]:
    """Deep-link vers la vue datastore du dashboard (surface d'édition canonique
    tant que l'export tiers — otomata#29 — n'existe pas). Par ID (`/data/<id>`,
    BIGSERIAL stable au renommage) — l'adressage `?ns=<nom>` est déprécié.

    ⚠️ **Peut valoir `None`** : le produit d'un partenaire n'a pas forcément de vue
    tableau. Celui du 13/08 n'en avait aucune ; il en a une depuis, que sa ligne de
    tenant doit encore déclarer (oto#63). On ne rend alors AUCUN lien — un lien mort ne
    se diagnostique pas, il se subit — et `adresse_servie` dit pourquoi.

    `org` = l'org dans laquelle le lien ouvre le tableau, pour un produit dont le
    patron la réclame (`/org/{org}/tables/{id}`). Transmise telle quelle ; `None`
    l'omet, et un patron qui la réclame ne rend alors aucun lien. Le registre ne la
    calcule qu'une fois par geste, et seulement si le patron la réclame."""
    from .. import links
    return links.link_for("table", sub=sub, id=int(ns_id), org=org)


def adresse_servie(url: Optional[str], sub: Optional[str]) -> dict:
    """La réponse d'adresse d'un tableau, identique sur les deux faces (oto#63).

    Un `url: null` NU se lisait « tableau introuvable » : il dit maintenant POURQUOI
    l'adresse manque (`url_absente`), et seulement quand elle manque — la réponse
    ordinaire ne porte pas de clé de plus."""
    if url is not None:
        return {"url": url}
    from .. import access, links
    raison = links.raison_sans_lien("table", sub=sub, id=0, org=access.current_org(sub))
    return {"url": None,
            "url_absente": raison or "aucune adresse n'a pu être construite pour ce tableau"}




def indice_de_liberation(issue: dict) -> str:
    """Une phrase par situation — jamais une phrase pour les deux (#517, 29/08).

    Écrite ICI plutôt que sur chaque face : les deux avaient chacune sa formule, et
    toutes deux mêlaient « rien à rendre » et « la ligne est à un autre »."""
    if issue["reason"] == "no_lease":
        return ("aucun bail sur cette ligne — rien à rendre. Ce n'est pas un échec : "
                "la ligne est libre, ton travail peut continuer.")
    bail = issue.get("lease") or {}
    jusqu = str(bail.get("claimed_until") or "?")[:16]
    return (f"bail tenu par `{bail.get('claimed_by')}` jusqu'à {jusqu} — la ligne ne "
            "t'appartient pas. Réserve-en une autre avec `data_claim_next`.")


def _backquote(noms) -> str:
    """`a`, `b` — une liste de noms rendue lisible au milieu d'un refus."""
    return ", ".join(f"`{n}`" for n in noms)


def _current_run() -> Optional[str]:
    """Le run de l'appel courant, ou None hors de tout run.

    Lu ICI et pas passé par les surfaces : le run est un CONTEXTE d'appel (ADR 0038),
    pas un argument métier — l'exiger des surfaces reviendrait à demander à chaque
    appelant de déclarer ce que le serveur sait déjà, et à l'oublier une fois sur
    deux."""
    from .. import session_org
    try:
        return session_org.current_call_run()
    # noqa: SILENT — hors run : pas de corrélation d'exécution à poser
    except Exception:      # noqa: BLE001 — hors contexte de requête (script, test)
        return None


def _refus_de_creation(datastore: str, key: str, value: Any = None, *,
                       schema: Optional[dict], ligne: dict,
                       cle_du_lot: Optional[str] = None) -> BusinessKeyRequired:
    """Le refus d'une CRÉATION sur un tableau fermé (`key_required`, #516).

    Deux formes, parce que les deux gestes qui l'atteignent sont différents — et que
    dire « clé requise » à qui vient d'en fournir une le ferait chercher longtemps :

    - **l'écriture ne porte pas la clé** : le geste du 28/08, une ligne née sans
      `siren` sur un tableau qui en déclare un ;
    - **la clé ne désigne aucune ligne** : le geste du 29/08, un SIREN inconnu qui a
      fabriqué une entreprise fictive. La valeur refusée est DITE — sans elle, il
      reste à deviner si c'est la valeur ou le tableau qui est en cause.

    Les deux nomment la clé ET les DEUX gestes de sortie : viser la ligne par son
    identifiant, et — si la ligne doit vraiment naître — lever le cran. Un refus qui
    ne dit que « non » fait deviner (cf. `errors.py`).

    ⚠️ **La seconde sortie n'est pas un confort** (#668, 02/09/2026). « Vise-la par
    son identifiant » est vrai et IMPRATICABLE dans le cas qui déclenche le refus :
    la ligne n'existe pas, et sur un tableau fermé encore vide il n'y a aucun `_id`
    à viser — le tableau ne peut alors plus recevoir sa première ligne par aucune
    écriture. Le journal a daté les deux moitiés du coût, sur la MÊME procédure de
    journalisation : le 01/09 (run `493e624c…`) l'agent refusé relit le schéma,
    trouve seul la manœuvre — ouvrir, écrire, refermer — et pose les 47 lignes du
    tableau ; le 02/09 (run `a2da6c1e…`) un autre passage ne la retrouve pas, essaie
    les trois formes d'écriture, et s'arrête sur 19 lignes non journalisées. La
    sortie existait, documentée dans `docs/datastore.md` et dans la description de
    `data_patch_schema` — nulle part où la lit celui qui vient d'être refusé.

    ⚠️ **La forme se lit aussi sans la phrase** (oto#151) : `details` porte `cle_portee`
    et la charge à renvoyer (`a_renvoyer`, gabarit de la colonne-clé DÉCLARÉE). Un client
    REST qui venait d'envoyer sa clé a lu « n'est pas renseigné » : il avait posé `key`
    dans le corps, lu comme une colonne, ou dédoublonnait son lot sur une AUTRE colonne
    (`cle_du_lot`) que la clé déclarée, seule jugée par le cran. Les deux sont nommés.

    Le cran ne bouge pas pour autant : la sortie passe par le SCHÉMA, jamais par un
    paramètre « forcer » sur l'écriture (#516 — un bouton force devient un réflexe,
    et le cran redevient une étiquette)."""
    ferme = ("ce tableau n'accepte que les écritures qui visent une ligne EXISTANTE "
             "(`key_required`) — rien n'a été créé.")
    sortie = ("Vise-la par son identifiant : data_write(id=…, row={…}) — son `_id` "
              "est rendu par data_rows et data_claim_next.")
    naissance = (
        f"Si cette ligne doit VRAIMENT naître, c'est une décision de SCHÉMA et pas "
        f"d'écriture — le cran a été posé sur ce tableau : "
        f"data_patch_schema(datastore='{datastore}', key_required=false), ton "
        f"écriture, puis data_patch_schema(datastore='{datastore}', "
        f"key_required=true) pour refermer.")
    portee = value is not None and str(value) != ""
    details: dict = {"key": key, "cle_portee": portee}
    if portee:
        details["valeur"] = value
        message = (
            f"cette écriture porte `{key}` = {str(value)!r}, mais aucune ligne de "
            f"`{datastore}` n'a cette valeur : {ferme} Vérifie la valeur (une clé "
            f"inventée créerait une ligne que rien ne rapproche). {sortie} {naissance}")
    else:
        message = (f"cette écriture ne porte pas `{key}`, la clé métier de "
                   f"`{datastore}` : {ferme}")
        if cle_du_lot and cle_du_lot != key and ligne.get(cle_du_lot) is not None:
            details["cle_du_lot"] = cle_du_lot
            message += (f" Ce lot dédoublonne sur `{cle_du_lot}`, que la ligne porte ; "
                        f"le cran, lui, se juge sur la clé DÉCLARÉE `{key}`.")
        elif key != "key" and isinstance(ligne.get("key"), str):
            message += (f" `key` est lu comme une COLONNE de la ligne, pas comme le nom "
                        f"de la clé : écris `{key}` lui-même, avec sa valeur.")
        message += (f" {sortie} Sinon renseigne `{key}` avec la valeur que porte la "
                    f"ligne visée. {naissance}")
    details.update(car.rendre({car.champ(car.RACINE, key):
                               gabarit(champ_declare(schema, key) or {})}))
    return BusinessKeyRequired(message, key=key, datastore=datastore,
                               value=value if portee else None, details=details)
