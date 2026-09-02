"""Le verdict d'accès d'UN connecteur, tel que `access.status_for` le produit.

Servi sous `providers` par `GET /api/me` depuis toujours, et déclaré `dict[str, Any]`
jusqu'au 2026-09-01 : riche, consommé par le dashboard produit, et nommé nulle part.
Un front qui construit une colonne « état » ne pouvait donc rien en dériver sans
observer le payload — c'est le motif de #669.

⚠️ **La crainte écrite qui gardait `Any` porte sur les CLÉS du dictionnaire, pas sur
la forme d'une valeur** : « un objet ouvert plutôt qu'une énumération qui mentirait au
premier connecteur ajouté ». Les clés restent ouvertes (`dict[str, ProviderStatus]`) —
c'est la VALEUR qui se déclare, et elle est stable depuis des mois.

Quatre familles produisent une entrée, et leurs champs diffèrent : à clé (`keyed`), sans
clé, `cookie` et `oauth`. D'où des champs optionnels **par famille** et non par
incertitude : `identity_label` n'a pas de sens pour un connecteur à clé, et son absence
est une information, pas un trou.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ProviderStatus(BaseModel):
    """L'accès effectif à un connecteur, pour l'acteur et dans l'org active.

    ⚠️ **Trois refus différents, qu'un écran ne doit pas confondre** :
    `mode='forbidden'` = aucune clé ne résout ; `rbac_restricted` = l'accès t'est
    refusé par une règle ; `health_ko` = la clé est là mais elle ne répond plus.
    Les afficher pareil produit le mur « demande à un admin » devant quelqu'un que
    rien ne bloque — le faux diagnostic réparé le 2026-07-16.
    """

    mode: str = Field(description=(
        "Comment l'accès se résout — le palier gagnant de la cascade, ou son refus. "
        "Valeurs servies au 2026-09-01 : `user` | `group` | `org` | `tenant` | "
        "`platform` (le palier qui fournit la clé), `over_quota` (une clé résout mais "
        "le quota du jour est épuisé), `forbidden` (aucune clé ne résout). Déclaré "
        "`str` et non énuméré à dessein : les cinq premiers viennent de la cascade, "
        "qui a son propre domicile — un énuméré ici ferait échouer un client généré le "
        "jour où elle en rend un sixième."))

    # ── Ce qui est POSÉ, palier par palier ────────────────────────────────────
    # Trois booléens plutôt qu'un seul `mode` : le mode dit qui GAGNE, ceux-ci disent
    # ce qui EXISTE. Un écran « retirer ma clé » a besoin de savoir qu'elle est là même
    # quand c'est celle de l'org qui résout.
    user_key_configured: bool = False
    group_secret_configured: bool = False
    org_secret_configured: bool = False
    # Le libellé de la clé plateforme quand c'est elle qui résout — `null` sinon.
    platform_key_label: Optional[str] = None
    # L'équipe dont la clé serait ATTEIGNABLE pour ce connecteur, quand il y en a une.
    team_key_group: Optional[int] = None

    # ── Le quota, quand la clé qui résout en porte un ─────────────────────────
    quota_used_today: int = 0
    # `null` = pas de quota sur ce chemin d'accès (ce n'est pas « zéro autorisé »).
    quota_daily: Optional[int] = None

    # ── Familles `cookie` et `oauth` : une session, pas une clé ───────────────
    session_set_at: Optional[str] = None
    group_session_set_at: Optional[str] = None
    org_session_set_at: Optional[str] = None
    # L'identité par défaut d'un connecteur qui en porte plusieurs (les canaux
    # hébergés). Absente partout ailleurs.
    identity_id: Optional[str] = None
    identity_label: Optional[str] = None

    # ── Les trois verdicts qu'un écran doit distinguer ────────────────────────
    pending_action: Optional[str] = Field(default=None, description=(
        "L'étape qui reste à faire alors que la clé résout déjà — lier un canal, par "
        "exemple. Renseignée par le module du connecteur, `null` partout où il n'y a "
        "rien à faire. ⚠️ Ce n'est PAS un refus : l'accès existe, il est incomplet."))
    rbac_restricted: bool = Field(default=False, description=(
        "Une règle d'org ou d'équipe refuse ce connecteur à cet acteur. ⚠️ À ne pas "
        "confondre avec `mode='forbidden'`, qui dit seulement qu'aucune clé ne "
        "résout : afficher « réservé à certaines équipes » sur une simple absence de "
        "clé oppose un mur à quelqu'un que rien ne bloque. ⚠️ Fail-open : un incident "
        "de lecture rend `false`, jamais une restriction inventée — une absence de "
        "restriction annoncée ne prouve donc pas l'accès."))
    health_ko: Optional[bool] = Field(default=None, description=(
        "La clé est posée mais le connecteur ne répond plus (session expirée, jeton "
        "révoqué…), constaté par la sonde de vérification et **persistant** jusqu'à "
        "une reconnexion ou un test réussi. Absent tant que rien n'a été constaté."))
    health_reason: Optional[str] = Field(default=None, description=(
        "Pourquoi la clé ne répond plus, quand la sonde a su le dire — son texte, "
        "tel quel. ⚠️ Peut être `null` **alors même que `health_ko` est vrai** : on "
        "sait que ça ne répond plus, sans savoir pourquoi. Afficher l'état sans "
        "inventer la cause — un message fabriqué enverrait chercher au mauvais "
        "endroit."))
