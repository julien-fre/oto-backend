"""Types de la couche capacité (ADR 0009).

Une `Capability` co-déclare, au même endroit que son handler : la clé stable,
le handler core, le modèle d'entrée pydantic (seule source de validation), une
règle d'autz **obligatoire**, et les bindings de surface (MCP / REST). Les
adaptateurs bouclent sur le registre et appliquent autz → validation → handler.

Aucun import d'adaptateur ni de transport ici (sens unique ADR 0004). Le refus
d'autz est un `AuthzDenied` **neutre** ; chaque adaptateur le traduit dans son
transport (McpError côté MCP, json_error+CORS côté REST).
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Callable, Optional

from pydantic import BaseModel


@dataclass
class RawCtx:
    """Identité brute résolue par l'adaptateur (deux chemins d'auth distincts :
    ContextVar de token côté MCP, `authenticate(request)` côté REST)."""
    sub: Optional[str]


@dataclass
class ResolvedCtx:
    """Contexte enrichi produit par la règle d'autz, passé au handler.
    `org_id`/`group_id` sont injectés par la règle (jamais acceptés d'un param
    client → verrou IDOR par construction)."""
    # `None` = destinataire d'un projet publié sans login (ADR 0032, règle
    # `PROJECT_SHARED_READ`) — le handler doit alors se borner à la lecture du projet
    # publié. Toute autre règle exige un `sub` et ne produit jamais None.
    sub: Optional[str]
    org_id: Optional[int] = None
    role: Optional[str] = None
    group_id: Optional[int] = None


class AuthzDenied(Exception):
    """Refus d'autz **neutre au transport**. `status` = code HTTP de référence
    (401/403/404/400) ; `code` = jeton machine stable ; `message` = détail."""

    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(message or code)
        self.status = status
        self.code = code
        self.message = message


# Une règle d'autz : (identité brute, input validé) -> contexte résolu, ou lève AuthzDenied.
AuthzRule = Callable[[RawCtx, Optional[BaseModel]], ResolvedCtx]


class NotModified:
    """Sentinelle de retour : « rien n'a changé depuis la version que tu portes ».

    Un handler la renvoie au lieu d'un corps ; chaque adaptateur la traduit dans SON
    transport — REST une `304` sans corps (la seule forme qu'un cache HTTP comprend),
    MCP un `{not_modified: True, rev}` (le protocole n'a pas de code d'état, et une
    réponse vide s'y lirait comme un résultat vide).

    Pourquoi une sentinelle et pas un `dict` convenu : le handler ne connaît pas son
    transport (sens unique ADR 0004), et un `{"not_modified": true}` renvoyé en REST
    serait une **200 avec un corps** — le client rangerait « rien n'a changé » dans son
    cache à la place des données. La différence ne se voit pas en test d'unité ; elle se
    voit au deuxième appel d'un vrai client.
    """

    __slots__ = ("rev",)

    def __init__(self, rev: str):
        self.rev = rev


@dataclass(frozen=True)
class RestBinding:
    verb: str                                   # GET | POST | PUT | PATCH | DELETE
    path: str                                   # ex "/api/me/active-org"
    # placeholder de route -> champ Input, quand ils diffèrent (routes réelles en {id}).
    path_map: dict = field(default_factory=dict)
    # Code de la réponse heureuse. 200 partout, SAUF là où un chemin historique rend
    # déjà 201 (création d'un tableau, ajout d'une ligne) : ce code est servi au
    # dashboard et à `oto-core` depuis toujours, le ramener à 200 en migrant la route
    # serait une régression silencieuse — la migration ne doit rien changer au fil.
    status: int = 200
    # Le corps JSON **entier** EST la valeur de CE champ d'`Input`, au lieu d'être
    # fusionné clé par clé dans les données validées.
    #
    # Pour les chemins dont le corps est une DONNÉE libre : la ligne d'un tableau,
    # dont les colonnes appartiennent à l'utilisateur — aucune ne peut être déclarée
    # dans un modèle. Sans ce cran, la garde de champ inconnu refuserait chacune
    # d'elles : elle vise un client qui se trompe de FORME, pas un client qui envoie
    # ses propres données. Déclaré par binding (donc greppable), jamais deviné.
    body_field: Optional[str] = None
    # Lire le corps JSON même sur un verbe qui n'en porte pas d'ordinaire (DELETE).
    # Un seul cas, historique : `DELETE …/namespaces/{ns}/share {"email": …}`, dont
    # le client vit hors de ce dépôt (`oto-core`). Opt-in explicite : le défaut reste
    # « pas de corps sur un DELETE », sinon migrer une route pourrait faire apparaître
    # un 400 `unknown_fields` sur un corps jusque-là ignoré.
    reads_body: bool = False
    # Route réservée à une SESSION INTERACTIVE : un porteur de jeton API `oto_` y est
    # refusé (403 `api_token_forbidden`).
    #
    # Un seul usage, et c'est une GARDE, pas une préférence : la gestion des jetons
    # eux-mêmes. Un jeton qui peut en créer d'autres rend sa fuite auto-entretenue —
    # révoquer le jeton fuité ne suffit plus, l'attaquant s'en est fait un second, non
    # expirant. Émettre un jeton reste donc un acte humain.
    #
    # Le cran vit sur le BINDING et non sur la capacité parce qu'il est propre au
    # transport : côté MCP, la question ne se pose pas (l'appelant est déjà une session).
    # Sans lui, migrer ces six routes aurait été une régression de sécurité — c'est pour
    # ça qu'elles étaient restées écrites à la main.
    allow_api_token: bool = True
    # Surface DÉCLARÉE PROVISOIRE : forme attendue, pas contrat figé. Publié tel quel
    # dans l'OpenAPI (`x-oto-provisoire: true`), la convention que le front a proposée
    # et qu'on a prise. Dire « provisoire » DANS le document est ce qui autorise à
    # servir tôt : un consommateur qui s'y branche sait qu'il s'y branche à ses frais,
    # et personne n'a à déduire d'une absence de mention que la forme est gravée.
    provisoire: bool = False


@dataclass(frozen=True)
class Capability:
    key: str                                    # clé stable, ≠ nom de surface (ex "org.use_org")
    handler: Callable                           # (ResolvedCtx, Input) -> dict (logique core)
    Input: type[BaseModel]                       # seule source de validation
    authz: AuthzRule                            # OBLIGATOIRE (pas de défaut → oubli = TypeError)
    # Forme de la RÉPONSE. L'entrée est dérivable depuis `Input` (schéma OpenAPI,
    # schéma de tool MCP) ; la sortie ne l'était par rien — elle n'existait que dans
    # les `return` du handler. Un consommateur tiers savait donc APPELER sans savoir
    # ce qu'il recevrait, et devait sonder. C'est la contrainte d'ADR 0059 prise au
    # mot : **on ne fige que ce qui est généré** — donc rien n'était figeable côté
    # réponse. `Output` déclaré ⟹ `/openapi.json` porte le schéma de la 200.
    # `None` = non déclarée : toléré pour l'existant, INTERDIT pour une capacité
    # neuve (garde-fou `tests/test_capability_outputs.py`, dette qui ne peut que
    # décroître). Le handler continue de renvoyer un `dict` — `Output` DÉCRIT, il
    # ne valide pas : valider la sortie ferait échouer un appel à l'exécution pour
    # une divergence de contrat, ce qui punit l'utilisateur d'un bug de serveur.
    Output: Optional[type[BaseModel]] = None
    description: str = ""                       # contrat LLM du tool MCP
    mcp: Optional[str] = None                   # nom du tool MCP, ou None (opt-out explicite)
    # un OU plusieurs bindings REST (ex. routes self-service + admin sur le même
    # métier+autz), ou None (opt-out explicite).
    rest: "Optional[RestBinding | tuple[RestBinding, ...]]" = None
    # Cette capacité change le PROFIL de visibilité (org/groupe actif) : après le
    # handler, l'adaptateur MCP re-pousse la denylist de la nouvelle org sur la
    # session courante → `tools/list_changed` live (B2/B3). No-op côté REST (le
    # dashboard n'est pas une session MCP).
    refresh_visibility: bool = False
    # Feature flag optionnel (dark launch) : callable 0-arg évalué au MONTAGE
    # (make_routes REST / register MCP), pas à l'import → le descripteur reste
    # dans le registre (introspection, tests, catalogue) mais sa surface n'est
    # pas exposée si le gate rend faux. Piloté par env par-déploiement (prod off,
    # canari on) sans divergence de branche. None = toujours exposé.
    gate: "Optional[Callable[[], bool]]" = None

    def __post_init__(self):
        if self.mcp is None and not self.rest:
            raise ValueError(
                f"Capability {self.key!r} sans surface : déclarer mcp= et/ou rest= "
                f"(un opt-out doit être explicite, pas un oubli)."
            )

    def is_exposed(self) -> bool:
        """False = déclarée mais NON montée (feature flag off à ce déploiement)."""
        return self.gate is None or bool(self.gate())

    def rest_bindings(self) -> list[RestBinding]:
        if self.rest is None:
            return []
        if isinstance(self.rest, RestBinding):
            return [self.rest]
        return list(self.rest)


def cap_limit(value, maximum: int, *, default: Optional[int] = None) -> int:
    """Borne une taille de page — **écrête, ne refuse pas**.

    Le patron vient de la recherche (`SearchInput._cap`) et vaut pour toute lentille
    paginée : sans lui, une valeur énorme part telle quelle au SQL et une valeur
    négative fait échouer la requête en 500 (oto-backend#300). Écrêter plutôt que
    refuser est un choix : le client qui demande trop reçoit le maximum servable, et
    non une erreur qu'il devra apprendre à éviter.

    `default` sert aux consoles op-aware, dont le `limit` est `Optional` parce qu'il
    dépend du verbe (un export ne se pagine pas comme une timeline).
    """
    if value is None:
        value = default if default is not None else maximum
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = default if default is not None else maximum
    return max(1, min(value, maximum))


def apply_flat_signature(fn: Callable, model: type[BaseModel]) -> Callable:
    """Expose les champs de `model` en paramètres KEYWORD_ONLY plats sur `fn`.

    FastMCP (3.4.2) génère le schéma d'un tool depuis la signature : un unique
    param pydantic donnerait un schéma IMBRIQUÉ (`{"p": {"$ref": …}}`), cassant
    le contrat plat des tools existants. On injecte donc `__signature__` +
    `__annotations__` reconstruits depuis les champs du modèle → schéma plat.
    Validé empiriquement (ADR 0009 §6 ; test `test_with_signature_flat`).
    """
    params = []
    annotations: dict = {}
    for name, f in model.model_fields.items():
        default = inspect.Parameter.empty if f.is_required() else f.default
        params.append(inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY,
                                        annotation=f.annotation, default=default))
        annotations[name] = f.annotation
    fn.__signature__ = inspect.Signature(params)
    fn.__annotations__ = annotations
    return fn
