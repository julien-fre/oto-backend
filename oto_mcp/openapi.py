"""Descriptif OpenAPI de l'API REST — **dérivé**, jamais tenu à la main.

Sans descriptif, toute intégration se construit par sondage : on tape des chemins
plausibles et on lit les codes de retour. C'est ainsi qu'un intégrateur a conclu que
les projets n'étaient pas sur REST — il avait sondé `/api/projects` (404) sans jamais
essayer `POST /api/me/projects {"op":"list"}`, qui existe. La surface n'était pas
absente : elle était indescriptible.

Deux sources, aucune saisie :

1. **Le registre de capacités** (`capabilities/registry.py`) — chaque capacité porte
   déjà son chemin, son verbe, sa description et le **JSON Schema** de son `Input`
   pydantic. C'est la même matière que sert `/api/admin/capabilities`.
2. **La table de routes vivante** de l'application Starlette, pour les routes encore
   écrites à la main (datastore, OAuth, jetons…). On n'a que chemin + méthodes : elles
   sont documentées comme telles, sans schéma, plutôt que passées sous silence.

⚠️ Conséquence de la consolidation ADR 0047 : le verbe d'un objet métier vit dans le
corps (`op`), pas dans le chemin. `POST /api/me/projects` n'est pas « créer un
projet » — c'est **la** surface projet (`op` ∈ create|list|get|runs|…), et l'énuméré
`op` du schéma le dit. Le document rend donc lisible ce que les chemins cachent.

Le document est **public** (comme `/api/mcp/catalog`) : il décrit des formes, aucune
valeur. `/api/admin/*` en est retiré — la console de la plateforme n'a pas
d'intégrateur tiers, et sa carte n'a pas à être publiée.
"""
from __future__ import annotations

from typing import Iterable, Optional

from .capabilities import registry
from .capabilities._types import Capability, RestBinding

_TITLE = "Oto REST API"
_BODY_VERBS = ("POST", "PUT", "PATCH")
_ADMIN_PREFIX = "/api/admin/"

_DESCRIPTION = """\
API REST de la plateforme Oto. Deux faces servent le même métier (ADR 0009) : le
MCP (`/mcp`, JWT Logto) et ce REST. Ce document est **dérivé du serveur** à chaque
requête — il décrit ce qui tourne, pas une intention.

**Authentification** — `Authorization: Bearer <jeton>`, sous deux formes :

- **JWT Logto** (session interactive : dashboard, connecteur MCP) ;
- **jeton API** `oto_…` (intégration programmatique), émis depuis une session
  interactive — un jeton ne peut ni lister, ni créer, ni révoquer de jeton.

**Portée d'un jeton** — un jeton API peut être **porté** à sa création
(`POST /api/me/tokens` avec `scopes`) : il n'ouvre alors QUE les tableaux nommés,
en lecture ou écriture, et rien d'autre de l'organisation. C'est la forme à confier
à une intégration tierce. Hors portée : `403 token_scope_forbidden`.

**Verbe dans le corps** — les surfaces consolidées (projets, pages, procédures,
ressources…) exposent un seul chemin dont le corps porte le verbe : `{"op": "list"}`.
Les valeurs possibles sont dans l'énuméré `op` du schéma de la requête.

**Contexte d'organisation** — en-tête optionnel `X-Oto-Org` (et `X-Oto-Group`) pour
travailler dans une organisation précise ; par défaut, l'organisation maison.
"""


def _param(name: str, location: str, schema: dict, required: bool,
           description: str = "") -> dict:
    out = {"name": name, "in": location, "required": required,
           "schema": schema or {"type": "string"}}
    if description:
        out["description"] = description
    return out


def _placeholders(path: str) -> list[str]:
    """Noms de placeholders d'un chemin Starlette, `{id}` ou `{id:int}`."""
    out, rest = [], path
    while "{" in rest:
        _, _, rest = rest.partition("{")
        name, _, rest = rest.partition("}")
        out.append(name.split(":")[0])
    return out


def _openapi_path(path: str) -> str:
    """`/x/{id:int}` → `/x/{id}` (le convertisseur Starlette n'est pas de l'OpenAPI)."""
    for ph in _placeholders(path):
        for raw in (f"{{{ph}:int}}", f"{{{ph}:path}}", f"{{{ph}:str}}", f"{{{ph}:float}}"):
            path = path.replace(raw, f"{{{ph}}}")
    return path


def _operation(cap: Capability, binding: RestBinding) -> tuple[dict, dict]:
    """Opération OpenAPI d'un binding + les définitions `$defs` à hisser."""
    try:
        schema = cap.Input.model_json_schema(ref_template="#/components/schemas/{model}")
    except Exception:                                  # modèle exotique → sans schéma
        schema = {}
    defs = schema.pop("$defs", {}) if isinstance(schema, dict) else {}
    props = dict(schema.get("properties") or {})
    required = set(schema.get("required") or [])

    # Les placeholders de chemin sont alimentés par le champ Input homonyme (ou
    # celui que `path_map` désigne) : ils sortent du corps pour devenir des params.
    field_of = {ph: (binding.path_map or {}).get(ph, ph)
                for ph in _placeholders(binding.path)}
    params = []
    for ph, field in field_of.items():
        params.append(_param(ph, "path", props.pop(field, None), True,
                             f"champ `{field}` de la requête" if field != ph else ""))
        required.discard(field)

    # La 200 porte un schéma dès que la capacité DÉCLARE sa sortie (`Output`). Sans
    # lui, on ne peut qu'annoncer « OK » — ce qui suffit à appeler, jamais à écrire
    # le client qui consomme. Cf. `Capability.Output` et le garde-fou de dette.
    ok: dict = {"description": "OK"}
    if cap.Output is not None:
        try:
            out = cap.Output.model_json_schema(
                ref_template="#/components/schemas/{model}")
        except Exception:                              # modèle exotique → sans schéma
            out = {}
        if isinstance(out, dict):
            defs.update(out.pop("$defs", {}) or {})
        if out:
            ok = {"description": "OK",
                  "content": {"application/json": {"schema": out}}}

    op: dict = {
        "operationId": f"{cap.key}.{binding.verb.lower()}".replace(".", "_"),
        "summary": (cap.description or cap.key).strip().split(". ")[0][:180],
        "description": cap.description or "",
        "tags": [cap.key.split(".")[0]],
        "security": [{"bearerAuth": []}],
        "responses": {
            # Le code de la réponse heureuse vient du binding (201 sur les créations
            # historiques) : le document décrit ce que le serveur REND, pas 200 par
            # convention — un client généré qui n'attend que 200 traiterait un 201
            # comme une erreur.
            str(binding.status): ok,
            "401": {"description": "jeton absent ou invalide"},
            "403": {"description": "refus d'autorisation (ou hors portée du jeton)"},
        },
    }
    if binding.provisoire:
        # Forme ATTENDUE, pas contrat figé (convention proposée par le front, prise
        # telle quelle). Dire « provisoire » DANS le document est ce qui autorise à
        # servir tôt : sans la marque, une absence de mention se lit comme « gravé ».
        op["x-oto-provisoire"] = True
    if binding.verb in _BODY_VERBS or binding.reads_body:
        if binding.body_field:
            # Corps LIBRE : le corps entier est la valeur d'UN champ (`body_field`),
            # donc c'est le schéma de ce champ qu'on publie — pas un objet qui
            # l'envelopperait, ce que le fil ne porte jamais.
            body = props.pop(binding.body_field, None) or {"type": "object"}
            required.discard(binding.body_field)
            for name, sub in props.items():
                params.append(_param(name, "query", sub, name in required))
            op["parameters"] = params
            op["requestBody"] = {"required": True,
                                 "content": {"application/json": {"schema": body}}}
            return op, defs
        body = {"type": "object", "properties": props}
        if required:
            body["required"] = sorted(required)
        op["parameters"] = params
        op["requestBody"] = {"required": bool(required),
                             "content": {"application/json": {"schema": body}}}
    else:
        # GET/DELETE : l'adaptateur REST lit la query string (`_rest_adapter`).
        for name, sub in props.items():
            params.append(_param(name, "query", sub, name in required))
        op["parameters"] = params
    return op, defs


def _handwritten(routes: Iterable) -> dict:
    """Routes Starlette écrites à la main : chemin + méthodes, sans schéma.

    Les documenter sans corps vaut mieux que les taire — l'intégrateur sait au moins
    qu'elles existent, et qu'il faut demander leur forme. Elles décroissent au fil
    des migrations en capacités (`test_rest_modules_are_capabilities.py`).
    """
    out: dict = {}
    for route in routes or ():
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if not path or not methods or not path.startswith("/api/"):
            continue
        if path.startswith(_ADMIN_PREFIX):
            continue
        item = out.setdefault(_openapi_path(path), {})
        params = [_param(ph, "path", {"type": "string"}, True)
                  for ph in _placeholders(path)]
        for verb in methods:
            v = verb.lower()
            if v in ("options", "head") or v in item:
                continue
            item[v] = {
                "operationId": f"{v}_{path.strip('/').replace('/', '_').replace('{', '').replace('}', '')}",
                "summary": f"{verb} {path}",
                "description": "Route écrite à la main : forme du corps non dérivable "
                               "(elle n'est pas encore une capacité).",
                "tags": ["_legacy"],
                "security": [{"bearerAuth": []}],
                "parameters": params,
                "responses": {"200": {"description": "OK"}},
            }
    return {p: i for p, i in out.items() if i}


def build(routes: Optional[Iterable] = None, *, server_url: Optional[str] = None) -> dict:
    """Document OpenAPI 3.1 complet. `routes` = table de routes vivante (facultative :
    sans elle, seules les capacités sont décrites)."""
    paths = _handwritten(routes)
    schemas: dict = {}
    for cap in registry.CAPABILITIES:
        if not cap.is_exposed():
            continue
        for binding in cap.rest_bindings():
            if binding.path.startswith(_ADMIN_PREFIX):
                continue
            op, defs = _operation(cap, binding)
            schemas.update(defs)
            item = paths.setdefault(_openapi_path(binding.path), {})
            item[binding.verb.lower()] = op          # la capacité prime sur le legacy
    doc = {
        "openapi": "3.1.0",
        "info": {"title": _TITLE, "version": "1", "description": _DESCRIPTION},
        "paths": dict(sorted(paths.items())),
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer",
                               "description": "JWT Logto ou jeton API `oto_…`"},
            },
            "schemas": schemas,
        },
        "security": [{"bearerAuth": []}],
    }
    if server_url:
        doc["servers"] = [{"url": server_url}]
    return doc
