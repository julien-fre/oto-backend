"""Instagram (statistiques) — le jeton d'un appel, et sa survie.

L'autre moitié du connecteur : `auth/instagram_meta.py` porte l'ACQUISITION (le
clic « Connecter », le retour de consentement, ce que la fiche affiche) ; ici on
sert — résoudre le jeton d'un appel, le renouveler à temps, et refuser en nommant
la cause. La ligne entre les deux est celle du déclencheur.

⚠️ **Ce jeton ne se renouvelle que tant qu'il vit.** Meta n'émet pas de
`refresh_token` sur ce produit : une autorisation laissée dormir soixante jours
n'est pas dégradée, elle est PERDUE, et seule l'utilisatrice peut la refaire. D'où
deux déclencheurs de renouvellement plutôt qu'un :

- **à l'usage**, très en avance (dès 53 jours restants sur 60), pour qu'un usage
  même espacé suffise à tenir la connexion ;
- **et une passe QUOTIDIENNE** (`renouveler_les_jetons`, travail de maintenance
  `instagram-tokens`), parce qu'un renouvellement paresseux meurt de non-usage :
  une personne qui ne consulte pas ses statistiques pendant deux mois perdrait sa
  connexion *sans avoir rien fait*, et rien ne l'en aurait prévenue. Ce n'est pas
  un mode de panne qu'on peut demander à l'utilisatrice de prévenir.

Trois refus, trois gestes différents, et c'est pourquoi ils ne se ressemblent pas :
pas de compte connecté (autoriser), autorisation expirée (ré-autoriser — on dit la
DATE), panne d'appel (réessayer). Meta les rend tous les trois en 400.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Optional

from mcp.types import ErrorData, INVALID_PARAMS

from .. import credentials_store
from ..auth import instagram_meta as ig_auth
from ..auth.instagram_meta import CONNECTOR, _coeur, _ctx_org, _row, _scope
from ..connectors import health as connector_health
from ..mcp_errors import McpError

if TYPE_CHECKING:  # l'annotation seulement — jamais évaluée à l'exécution
    from oto.tools.instagram_meta import InstagramClient

logger = logging.getLogger("oto_mcp.tools.instagram_meta")

_MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
         "août", "septembre", "octobre", "novembre", "décembre")


class InstagramReauthRequired(RuntimeError):
    """L'autorisation est morte : il faut un NOUVEAU consentement, pas un retry.

    Le message porté par cette exception est celui qu'on montre à l'utilisatrice —
    il nomme la date et le geste. Type distinct parce que les appelants s'en
    servent pour marquer la ligne de coffre (`connector_health`) sans confondre
    avec une panne passagère, qui elle ne doit rien marquer du tout."""


def _date_fr(horodatage: Optional[str]) -> str:
    """« 8 novembre 2026 » — une date qu'on lit, pas un ISO 8601 qu'on déchiffre.

    Un message d'expiration sans date se lit comme une panne ; avec la date, il se
    lit comme ce qu'il est. Horodatage illisible ⇒ chaîne vide, et l'appelant dit
    la phrase sans la date plutôt que d'afficher `None`."""
    dt = _coeur().parse_ts(horodatage)
    return f"{dt.day} {_MOIS[dt.month - 1]} {dt.year}" if dt else ""


def _message_expire(expires_at: Optional[str]) -> str:
    """⚠️ **Sans adresse, et c'est délibéré.** Ce message citait notre tableau de bord en
    dur ; servi à l'agent d'un partenaire, il l'envoyait chez nous pour un geste qu'il
    doit faire chez lui. Le bon lien dépend du COMPTE (`config.dashboard_url_for`) —
    or ni cette fonction ni les deux autres sites de ce module ne tiennent le `sub` :
    ils sont appelés depuis le marquage de santé et depuis le traducteur de refus, qui
    ne portent qu'une entité. Propager le compte jusqu'ici serait une refonte sans
    rapport avec le défaut ; nommer la page sans l'adresser dit la même chose à un agent
    et n'envoie personne au mauvais endroit. Ne pas « remettre le lien » sans le `sub`.
    """
    quand = _date_fr(expires_at)
    return (
        f"Ton autorisation Instagram a expiré{f' le {quand}' if quand else ''} — "
        "elle vaut 60 jours et ne peut plus être renouvelée une fois passée. "
        "Reconnecte ton compte depuis ta page connecteurs, connecteur « Instagram "
        "(statistiques) ».")


def resolve_token(sub: str) -> tuple[str, str]:
    """`(jeton, user_id)` du compte connecté — renouvelé D'ABORD si besoin.

    Trois refus, et chacun nomme sa cause parce qu'ils appellent trois gestes
    différents : aucun compte connecté (il faut autoriser), autorisation expirée
    (il faut ré-autoriser, et on dit la date), coffre incohérent (il manque le
    `user_id`, la reconnexion le repose).

    Le renouvellement préventif est joué ICI, avant de rendre le jeton, et son
    échec n'est jamais avalé : servir un jeton dont on sait qu'il va mourir
    déplacerait la panne d'un appel plus loin, où plus rien ne dirait pourquoi."""
    org_id = _ctx_org(sub)
    entity_type, entity_id = _scope(org_id, sub)
    row = _row(org_id, sub)
    if not row or not row.get("secret"):
        from .. import config
        raise RuntimeError(
            "Aucun compte Instagram connecté. Autorise oto depuis ta page "
            f"connecteurs ({config.dashboard_url_for(sub)}/, connecteur « Instagram "
            "(statistiques) ») — la connexion se fait avec ton compte Instagram, "
            "sans Facebook.")
    meta = row.get("meta") or {}
    user_id = str(meta.get("user_id") or "")
    if not user_id:
        raise RuntimeError(
            "Le compte Instagram connecté n'a pas d'identifiant de compte "
            "professionnel enregistré : reconnecte-le depuis ta page connecteurs.")
    coeur = _coeur()
    expires_at, connected_at = meta.get("expires_at"), meta.get("connected_at")
    if coeur.is_expired(expires_at, connected_at):
        _marquer_mort(entity_type, entity_id, "", expires_at)
        raise InstagramReauthRequired(_message_expire(expires_at))
    jeton = row["secret"]
    if coeur.needs_refresh(expires_at, meta.get("refreshed_at") or connected_at):
        jeton = _renouveler(coeur, entity_type, entity_id, "", jeton, meta,
                            expires_at)
    return jeton, user_id


def _marquer_mort(entity_type: str, entity_id: str, account: str,
                  expires_at: Optional[str]) -> None:
    """Inscrit le rejet sur la ligne que l'appel utilise VRAIMENT.

    C'est ce qui rend la fiche capable de dire « autorisation expirée, à
    reconnecter » AVANT qu'on appelle : sans marquage, l'utilisatrice ne découvre
    l'expiration qu'en essayant, et la carte affiche « connecté » sur un compte qui
    ne l'est plus."""
    connector_health.mark_rejected(entity_type, entity_id, CONNECTOR, account,
                                   _message_expire(expires_at))


def _renouveler(coeur, entity_type: str, entity_id: str, account: str,
                jeton: str, meta: dict, expires_at: Optional[str]) -> str:
    """Échange le jeton contre un neuf de 60 jours et réécrit la ligne. Rend le neuf.

    **Un seul renouvellement pour les deux chemins** — l'appel d'une utilisatrice et
    la passe quotidienne. Ils diffèrent par ce qui les déclenche, pas par ce qu'ils
    écrivent : les séparer aurait fait deux endroits où réécrire une échéance, donc
    un jour deux façons de l'écrire, dont une fausse.

    Le secret est remplacé ENTIER (pour ce connecteur, le blob EST le jeton) et le
    `meta` re-passé COMPLET : `set_credential` sans `meta` l'écrase par `{}`, ce qui
    effacerait l'identifiant de compte et l'échéance — c'est-à-dire tout ce qui
    permet de servir, puis de renouveler la fois suivante.

    Le marquage de santé est effacé ICI : un renouvellement réussi est la seule
    preuve que la ligne remarche, et sans cet effacement la fiche resterait rouge
    sur une connexion saine (même raison que la rotation Google).
    """
    try:
        frais = coeur.refresh_long_lived(jeton, expires_at=expires_at)
    except coeur.InstagramAuthExpired as e:
        _marquer_mort(entity_type, entity_id, account, expires_at)
        raise InstagramReauthRequired(_message_expire(expires_at)) from e
    except Exception as e:
        # Panne passagère : on ne marque RIEN — marquer ferait dire à la fiche
        # « autorisation morte » sur une autorisation vivante, et enverrait
        # l'utilisatrice refaire un consentement dont elle n'a pas besoin. On ne
        # sert pas non plus le jeton qu'on vient d'échouer à prolonger : la panne
        # se dirait alors un appel plus loin, où plus rien ne l'expliquerait.
        raise RuntimeError(
            f"Le renouvellement de l'autorisation Instagram n'a pas abouti : {e} "
            "Ce n'est pas ton autorisation qui est en cause — réessaie.") from e
    maintenant = coeur.utcnow()
    neuf = dict(meta)
    neuf["expires_at"] = coeur.iso(maintenant + timedelta(seconds=frais["expires_in"]))
    neuf["refreshed_at"] = coeur.iso(maintenant)
    neuf.pop("health_ko", None)
    neuf.pop("health_reason", None)
    # `set_by` = le propriétaire du consentement, lu sur la ligne elle-même :
    # l'écrire au nom d'un travail système ferait mentir « qui a posé cette clé ».
    # ⚠️ Le découpage n'est valide QU'au palier membre, où `entity_id` vaut
    # `{org}:{sub}`. Au scope `user`, `entity_id` EST le sub — et un sub qualifié
    # (tenant) y ressemble à s'y méprendre : on attribuerait la ligne à la moitié
    # d'un identifiant. Ce connecteur n'écrit qu'en membre ; la garde est là pour
    # que ça reste vrai si un autre palier s'ajoute.
    sub = (str(entity_id).partition(":")[2]
           if entity_type == credentials_store.MEMBER else "")
    credentials_store.set_credential(entity_type, entity_id, CONNECTOR,
                                     frais["access_token"], set_by=sub or None,
                                     meta=neuf, account=account)
    logger.info("instagram_meta : autorisation renouvelée (%s, jusqu'au %s)",
                entity_id, neuf["expires_at"])
    return frais["access_token"]


# --- la passe quotidienne ------------------------------------------------------

def renouveler_les_jetons(*, dry_run: bool = False) -> dict:
    """Renouvelle toutes les autorisations qui approchent du terme. Ne lève jamais.

    **Le renouvellement paresseux ne suffit pas ici, et ce n'est pas un confort.**
    Un jeton Meta ne se renouvelle que tant qu'il vit : une personne qui ne consulte
    pas ses statistiques pendant deux mois perdrait sa connexion *sans avoir rien
    fait*, et rien ne l'en aurait prévenue. Une passe quotidienne est la seule chose
    qui rende la survie de la connexion indépendante de l'usage.

    Fail-open par LIGNE : une autorisation morte — le cas normal après une
    révocation — ne doit pas empêcher de renouveler les autres. Elle est marquée,
    comptée, et la passe continue.
    """
    from ..db import _conn as db_conn

    sortie = {"examines": 0, "renouveles": 0, "expires": 0, "echecs": 0,
              "dry_run": dry_run}
    try:
        coeur = _coeur()
    except RuntimeError as e:
        # oto-core trop ancien : le connecteur ne sert pas non plus, il n'y a rien
        # à renouveler. On le DIT plutôt que de rendre un zéro rassurant.
        return {**sortie, "note": str(e)}
    with db_conn._connect() as conn:
        lignes = conn.execute(
            "SELECT entity_type, entity_id, account FROM connector_credentials "
            "WHERE connector = %s",
            (CONNECTOR,)).fetchall()
    for ligne in lignes:
        sortie["examines"] += 1
        entity_type, entity_id = ligne["entity_type"], ligne["entity_id"]
        account = ligne["account"] or ""
        try:
            row = credentials_store.get_credential_with_meta(
                entity_type, entity_id, CONNECTOR, account)
            if not row or not row.get("secret"):
                continue
            meta = dict(row.get("meta") or {})
            expires_at = meta.get("expires_at")
            emis = meta.get("refreshed_at") or meta.get("connected_at")
            if coeur.is_expired(expires_at, emis):
                # Rien à renouveler : c'est un consentement qu'il faut. On marque,
                # pour que la fiche le dise sans attendre le prochain appel — et
                # pas à blanc : « à blanc » veut dire qu'on n'écrit RIEN, y compris
                # ce marquage, sinon la commande ment sur ce qu'elle fait.
                if not dry_run:
                    _marquer_mort(entity_type, entity_id, account, expires_at)
                sortie["expires"] += 1
                continue
            if not coeur.needs_refresh(expires_at, emis):
                continue
            if not dry_run:
                _renouveler(coeur, entity_type, entity_id, account, row["secret"],
                            meta, expires_at)
            sortie["renouveles"] += 1
        except Exception:  # noqa: BLE001 — une ligne morte n'arrête pas la passe
            sortie["echecs"] += 1
            logger.warning("instagram_meta : renouvellement en échec pour %s",
                           entity_id, exc_info=True)
    return sortie


# --- le client d'un appel -------------------------------------------------------

def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


async def _client() -> InstagramClient:
    """Le client Instagram de CET appelant, jeton renouvelé si besoin.

    Le nom et l'annotation de retour NON quotée sont un contrat : la sonde de
    version-skew (`tests/test_tools_client_methods_exist.py`) reconnaît les
    fabriques appelées `_client` et lit la classe qu'elles rendent pour vérifier,
    au tag oto-core épinglé, que chaque méthode appelée par les outils existe.
    Renommer cette fonction ou quoter son annotation sort le connecteur de cette
    couverture EN SILENCE — et c'est le connecteur qui en a le plus besoin, son
    cœur vivant dans l'autre dépôt.

    Tout part au FIL D'EXÉCUTION : la résolution touche la base, le renouvellement
    parle à Meta, et le client lui-même est synchrone. Ce serveur est mono-loop
    (`docs/event-loop-perf.md`) — un seul de ces trois appels joué dans la boucle
    la fige le temps que l'amont réponde.

    `renew` est le RATTRAPAGE, pas la politique : il ne joue que si Meta rejette le
    jeton en plein appel (révocation, rotation), une seule fois, et il réécrit le
    coffre au passage. Le renouvellement normal, lui, a déjà eu lieu au-dessus.
    """
    from .. import access

    sub = access.current_user_sub_or_raise()
    try:
        jeton, user_id = await asyncio.to_thread(resolve_token, sub)
    except RuntimeError as e:
        # `InstagramReauthRequired` en est une : les deux portent déjà le message
        # qu'on montre, l'un pour l'expiration (avec sa date), l'autre pour un
        # compte non connecté ou un coffre incohérent. Les distinguer ici ne
        # changerait rien à ce qu'on rend — ce qui les distingue vraiment, c'est
        # que l'un marque la ligne de coffre et l'autre non, et c'est fait plus tôt.
        raise _bad(str(e)) from e

    def renouveler_a_chaud() -> str:
        org_id = _ctx_org(sub)
        entity_type, entity_id = _scope(org_id, sub)
        row = _row(org_id, sub) or {}
        return _renouveler(_coeur(), entity_type, entity_id, "",
                           row.get("secret") or jeton, dict(row.get("meta") or {}),
                           (row.get("meta") or {}).get("expires_at"))

    return _coeur().InstagramClient(jeton, user_id, renew=renouveler_a_chaud)


async def appeler(geste: str, fn, *args):
    """Joue un appel du cœur hors de la boucle et traduit ses refus.

    Le point n'est pas d'attraper : c'est de **ne pas confondre**. Un jeton mort et
    une panne d'Instagram remontent tous deux en `RuntimeError` du cœur, et les
    présenter pareil coûte dans les deux sens — l'un fait réessayer sans fin une
    connexion qu'il faut refaire, l'autre fait refaire un consentement dont personne
    n'avait besoin.

    ⚠️ Le texte d'une exception amont ne traverse cette frontière que pour les
    erreurs que le cœur RÉDIGE lui-même (`InstagramError`), et celles-là ne portent
    ni URL ni corps brut — c'est une propriété tenue là-bas, et testée là-bas. Pour
    tout le reste on rend le TYPE, jamais le message : sur cette API le jeton voyage
    en paramètre d'URL, et une exception d'une couche intermédiaire pourrait le
    porter jusque dans un transcript d'agent.
    """
    coeur = _coeur()
    try:
        return await asyncio.to_thread(fn, *args)
    except InstagramReauthRequired as e:
        # Le rattrapage a essayé et Meta a refusé : le message porte déjà la date.
        raise _bad(str(e)) from e
    except coeur.InstagramAuthExpired as e:
        # Meta rejette le jeton alors que l'échéance stockée le disait vivant :
        # c'est une RÉVOCATION, pas une expiration — dire « expiré le <date> »
        # serait faux, et enverrait chercher une échéance qui n'y est pour rien.
        raise _bad(
            "Instagram ne reconnaît plus l'autorisation de ce compte : elle a été "
            "révoquée, ou le compte a changé. Reconnecte-le depuis ta page "
            "connecteurs, connecteur « Instagram (statistiques) ».") from e
    except (coeur.InstagramApiError, ValueError) as e:
        raise _bad(f"Instagram n'a pas pu servir {geste} : {e}") from e
    except Exception as e:
        logger.warning("instagram_meta : %s a échoué — %s", geste, type(e).__name__)
        raise _bad(
            f"Instagram n'a pas répondu à {geste} ({type(e).__name__}). Ce n'est pas "
            "un refus d'autorisation — réessaie, et si ça dure, c'est chez Instagram "
            "que ça se passe.") from e


def avertir_au_demarrage() -> None:
    """Ce que l'exploitant doit savoir AU BOOT, en une ligne. Ne lève jamais."""
    ig_auth.avertir_au_demarrage()
    try:
        _coeur()
    except RuntimeError as e:
        logger.warning(
            "instagram_meta : connecteur monté mais le cœur n'est pas installé — "
            "les outils `instagram_meta_*` refuseront en le disant. Détail : %s", e)
