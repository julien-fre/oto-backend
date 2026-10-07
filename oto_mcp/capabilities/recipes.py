"""`oto_recipe` — les recettes : un outil de connecteur vers un tableau, sans modèle.

Une recette dit quel outil appeler, avec quels arguments, comment parcourir ses
pages, quel champ va dans quelle colonne et quelle colonne identifie une ligne. Un
agent l'écrit une fois — `sample` montre la forme de ce que l'outil rend, `test`
l'éprouve sur une vraie page sans rien écrire — puis le serveur l'exécute (`run`)
autant qu'on veut : l'agent ne relit plus les pages et ne les recopie plus.

**Proposer et publier sont ouverts au membre, dans sa portée** : une recette n'appelle
que des outils DÉCLARÉS EN LECTURE (`tools/lecture`) que son exécutant peut déjà
appeler, et son plafond de dépense (`limits.max_units`) est obligatoire. **Publier
éprouve** : une page réelle, sans écriture, qui doit produire des lignes ; le
remplissage par colonne est gardé avec la version. **Seule une version publiée
écrit** : une recette passée en ligne ne sert qu'à `test`.

⚠️ **Refusé dans un agent hébergé, pour l'instant.** Le jeton d'un travail du runner ne
porte pas la liste d'outils de son déclencheur ; sans elle, une recette pourrait faire
appeler à un agent un outil que sa liste ne lui donne pas. Le lien jeton → travail
viendra dans un lot à part.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .. import session_org, tool_alias, tool_registry
from ..auth.hooks import current_token_axes
from ..db import recipes as db_recipes
from ..recipes import contrat, moteur
from ._authz import BY_OP, ORG_MEMBER_OPT, SUB_ONLY
from ._types import AuthzDenied, Capability, ResolvedCtx
from .registry import CAPABILITIES

_PORTEE = BY_OP({None: ORG_MEMBER_OPT("org"), "org": ORG_MEMBER_OPT("org"),
                 "user": SUB_ONLY}, fields=("scope",))
_APPELANTES = ("sample", "test", "publish", "run")


class RecipeInput(BaseModel):
    op: Literal["list", "get", "versions", "create", "propose", "sample", "test",
                "publish", "run", "schedule", "schedules", "set_schedule", "unschedule"]
    slug: Optional[str] = None
    scope: Optional[Literal["org", "user"]] = None
    org: Optional[int] = None
    version: Optional[int] = Field(default=None, description=(
        "get/test/publish/run: the version (default: the published one, else the latest)."))
    title: Optional[str] = None
    description: Optional[str] = None
    recipe: Optional[dict[str, Any]] = Field(default=None, description=(
        "create/propose: the recipe body. test without `slug`: an inline recipe (never "
        "written: only a published version runs). "
        "Keys: tool, arguments, params, source {items, pagination {type page|cursor|none, "
        "param, start, size, size_param, last, next}}, where [{path, op, value}], "
        "map {column: path | {path, max, default} | {template} | {const}}, values, "
        "key {column, template}, on_existing skip|update, limits {max_units, max_pages}, "
        "units items|calls, for_each {datastore, status_column, filter, max_parents, "
        "max_items_per_row} (each pending row of that table drives the call; cite it as "
        "{{row.col}}). mode=per_row: one call per pending row of `datastore`, its result "
        "written back into that row — rows {status_column, filter, max_rows}, "
        "on_existing fill_empty|update, pick first, no key, no pagination; with async "
        "{id, ready, collect {tool, arguments}, keep, max_wait_seconds} it submits paid "
        "jobs then collects them ({{job.id}}). mode=push (side_effects: true): creates or "
        "updates one record per pending row in another app — rows {status_column, "
        "require, filter, max_rows}, id {column, path}, lookup {arguments, items, "
        "id_path}, update {arguments}, errors, allow_sending; op written literally. where ops: eq ne in not_in contains_any empty not_empty, and "
        "in_table / not_in_table {table, column} (match against another table); any clause "
        "takes `normalize`. Templates: {{params.x}}, {{item.a.b}}, {{row.col}}, filters "
        "|slug |lower |upper |strip |unaccent |domain |email |email_domain |linkedin_slug "
        "|url |digits."))
    note: Optional[str] = None
    expected_version: Optional[int] = Field(default=None, description=(
        "propose: the latest version you read."))
    params: Optional[dict[str, Any]] = Field(default=None, description=(
        "test/publish/run: the recipe's parameters."))
    datastore: Optional[Any] = Field(default=None, description=(
        "run: the table number to write into (test: optional, checks the key). A "
        "`per_row` recipe reads AND writes this table: required for test and publish too."))
    resume: Optional[str] = Field(default=None, description=(
        "run: the `resume` token of a previous partial run, to continue it."))
    tool: Optional[str] = Field(default=None, description="sample: the tool to call once.")
    arguments: Optional[dict[str, Any]] = Field(default=None, description=(
        "sample: its arguments (ask for the smallest page: the call is billed)."))
    items: Optional[str] = Field(default=None, description=(
        "sample: path to the list of items in the result (empty = the result itself)."))
    every_minutes: Optional[int] = Field(default=None, description=(
        "schedule: run the published recipe every N minutes (at least 15), with `params` "
        "and `datastore`, on your behalf."))
    schedule_id: Optional[int] = Field(default=None, description=(
        "set_schedule / unschedule: the schedule (from op=schedules)."))
    enabled: Optional[bool] = Field(default=None, description=(
        "set_schedule: false pauses it, true resumes it (and resets its failure count)."))


def _admin(sub: str, org_id: int) -> bool:
    from .. import roles
    return bool(roles.is_org_admin(sub, org_id))


def _programmer(ctx: ResolvedCtx, inp: RecipeInput) -> dict:
    """Les ops de programme — synchrones, hors boucle. Un programme agit SANS personne
    devant l'écran, au nom de qui le pose : seule une personne en pose un (jamais un
    agent hébergé), sur une version publiée."""
    if current_token_axes().get("token_kind") == "delegation":
        raise AuthzDenied(403, "schedules_not_in_hosted_agents",
                          "A hosted agent can't schedule a recipe: a person does.")
    fiche = _fiche(ctx, inp)
    if inp.op == "schedules":
        return {"recipe": fiche, "schedules": db_recipes.programmes_de(fiche["id"])}
    if inp.op in ("set_schedule", "unschedule"):
        sid = _besoin(inp.schedule_id, "missing_schedule_id", "`schedule_id` is required.")
        prog = db_recipes.programme(sid, fiche["id"])
        if prog is None:
            raise AuthzDenied(404, "unknown_schedule", f"No schedule {sid} on this recipe.")
        # Un programme agit AU NOM de qui l'a posé : seul lui le (ré)active ; son créateur
        # ou un admin de l'org le suspend ou le retire.
        reprend = inp.op == "set_schedule" and inp.enabled is not False
        admin = prog["org_id"] is not None and _admin(ctx.sub, prog["org_id"])
        if prog["sub"] != ctx.sub and (reprend or not admin):
            raise AuthzDenied(403, "not_schedule_owner",
                              "Only the person who set this schedule can resume it; they "
                              "or an org admin can pause or remove it.")
        if inp.op == "unschedule":
            ok = db_recipes.supprimer_programme(sid, fiche["id"])
        else:
            ok = db_recipes.regler_programme(sid, fiche["id"], enabled=bool(
                _besoin(inp.enabled, "missing_enabled", "`enabled` is required.")))
        if not ok:
            raise AuthzDenied(404, "unknown_schedule", f"No schedule {sid} on this recipe.")
        return {"recipe": fiche, "schedules": db_recipes.programmes_de(fiche["id"])}
    if not fiche["published_version"]:
        raise AuthzDenied(409, "not_published", "Only a published recipe can be scheduled.")
    minutes = _besoin(inp.every_minutes, "missing_every_minutes",
                      "`every_minutes` (at least 15) is required.")
    if minutes < 15:
        raise AuthzDenied(400, "schedule_too_frequent", "`every_minutes` must be at least 15.")
    datastore = _besoin(inp.datastore, "missing_datastore", "`datastore` is required.")
    corps = db_recipes.get_version(fiche["id"], fiche["published_version"])["body"]
    _params(corps, inp.params)  # refusés maintenant, pas au premier passage
    db_recipes.creer_programme(recipe_id=fiche["id"], version=fiche["published_version"],
                               sub=ctx.sub, org_id=ctx.org_id,
                               params=inp.params or {}, datastore=str(datastore),
                               every_minutes=int(minutes))
    return {"recipe": fiche, "schedules": db_recipes.programmes_de(fiche["id"])}


class RecipeOut(BaseModel):
    recipe: Optional[dict] = None
    recipes: Optional[list[dict]] = None
    version: Optional[dict] = None
    versions: Optional[list[dict]] = None
    receipt: Optional[dict] = None
    shape: Optional[dict] = None
    schedules: Optional[list[dict]] = None


def _besoin(valeur, code: str, message: str):
    if valeur is None or (isinstance(valeur, str) and not valeur.strip()):
        raise AuthzDenied(400, code, message)
    return valeur


def _proprietaire(ctx: ResolvedCtx, inp: RecipeInput) -> tuple[str, str]:
    if inp.scope == "user":
        return "user", ctx.sub
    if ctx.org_id is None:
        raise AuthzDenied(400, "no_active_org", "No active org: pass `org=<id>`, or "
                                                "`scope='user'` for a recipe of your own.")
    return "org", str(ctx.org_id)


def _corps(ctx: ResolvedCtx, brut: Any) -> dict:
    """Le corps validé, l'outil ramené à son nom canonique (un agent de tenant le lit
    sous son préfixe)."""
    try:
        corps = contrat.valider(brut)
    except contrat.RecetteInvalide as e:
        raise AuthzDenied(400, "invalid_recipe", str(e),
                          details={"problems": e.problemes}) from None
    corps["tool"] = tool_alias.canonical(corps["tool"], tool_alias.prefix_for(ctx.sub))
    return corps


def _fiche(ctx: ResolvedCtx, inp: RecipeInput) -> dict:
    slug = _besoin(inp.slug, "missing_slug", f"`slug` is required for op={inp.op}.")
    owner = _proprietaire(ctx, inp)
    fiche = db_recipes.get_recipe(owner[0], owner[1], slug)
    if fiche is None:
        raise AuthzDenied(404, "unknown_recipe", f"No recipe `{slug}` here. "
                                                 "`oto_recipe(op='list')` shows yours.")
    return fiche


def _version(fiche: dict, numero: Optional[int]) -> dict:
    numero = numero or fiche["published_version"] or fiche["latest_version"]
    version = db_recipes.get_version(fiche["id"], numero) if numero else None
    if version is None:
        raise AuthzDenied(404, "unknown_version", f"`{fiche['slug']}` has no version {numero}.")
    return version


def _gerer(ctx: ResolvedCtx, inp: RecipeInput) -> dict:
    """Les ops de stockage — synchrones, hors boucle."""
    if inp.op == "list":
        if inp.scope == "user":
            owners = [("user", ctx.sub)]
        else:
            owners = [("org", str(ctx.org_id))] if ctx.org_id is not None else []
            owners += [] if inp.scope == "org" else [("user", ctx.sub)]
        return {"recipes": db_recipes.list_recipes(owners)}
    if inp.op == "create":
        slug = _besoin(inp.slug, "missing_slug", "`slug` is required for create.")
        if not contrat.SLUG.match(slug):
            raise AuthzDenied(400, "invalid_slug", "`slug`: lowercase letters, digits and "
                                                   "dashes, 2 to 63 characters.")
        title = _besoin(inp.title, "missing_title", "`title` is required for create.")
        corps = _corps(ctx, _besoin(inp.recipe, "missing_recipe", "`recipe` is required."))
        owner = _proprietaire(ctx, inp)
        try:
            fiche = db_recipes.create_recipe(
                owner_type=owner[0], owner_id=owner[1], slug=slug, title=title.strip(),
                description=inp.description or "", created_by=ctx.sub, body=corps,
                note=inp.note)
        except db_recipes.RecipeExists:
            raise AuthzDenied(409, "recipe_exists", f"Recipe `{slug}` already exists here: "
                                                    "propose a new version.") from None
        return {"recipe": fiche, "version": {"version": 1, "status": "proposee"}}
    fiche = _fiche(ctx, inp)
    if inp.op == "versions":
        return {"recipe": fiche, "versions": db_recipes.list_versions(fiche["id"])}
    if inp.op == "get":
        return {"recipe": fiche, "version": _version(fiche, inp.version)}
    # propose
    attendue = _besoin(inp.expected_version, "missing_expected_version",
                       "`expected_version` is required: the latest version you read "
                       f"(currently {fiche['latest_version']}).")
    corps = _corps(ctx, _besoin(inp.recipe, "missing_recipe", "`recipe` is required."))
    try:
        numero = db_recipes.propose_version(fiche["id"], expected_version=attendue,
                                            body=corps, note=inp.note, proposed_by=ctx.sub)
    except db_recipes.VersionConflict as e:
        raise AuthzDenied(409, "version_conflict", f"The latest version is {e.courante}, "
                                                   f"you read {e.attendue}.") from None
    return {"recipe": db_recipes.get_recipe(fiche["owner_type"], fiche["owner_id"],
                                            fiche["slug"]),
            "version": {"version": numero, "status": "proposee"}}


def _params(corps: dict, fournis: Any) -> dict:
    try:
        return contrat.params_resolus(corps, fournis)
    except contrat.RecetteInvalide as e:
        raise AuthzDenied(400, "invalid_params", str(e),
                          details={"problems": e.problemes}) from None


def _instance():
    fastmcp = tool_registry.bound_instance()
    if fastmcp is None:
        raise AuthzDenied(503, "server_not_ready", "The tool registry is not bound yet.")
    return fastmcp


def _epingler_org(ctx: ResolvedCtx):
    """L'org de la RECETTE pour tout ce que l'exécution fait : la clé du connecteur,
    sa facturation et son journal suivent l'org que l'appel a résolue (garde
    d'appartenance comprise), pas l'org maison de la session. `None` sans org."""
    return session_org.set_call_org(ctx.org_id) if ctx.org_id is not None else None


def _a_effets(corps: dict) -> bool:
    """Une recette qui dépense des crédits sans lire (`async`) ou écrit chez un tiers
    (`push`)."""
    return corps.get("mode") == "push" or bool(corps.get("async")) or bool(corps.get("start"))


async def _executer(ctx: ResolvedCtx, inp: RecipeInput, corps: dict, *, ecrire: bool,
                    pages_max: Optional[int] = None, temoin: Optional[dict] = None,
                    recette_id: Optional[int] = None) -> dict:
    if _a_effets(corps) and current_token_axes().get("token_kind") == "delegation":
        # Un agent hébergé lit du texte non sûr (webhook, e-mails, CRM) : il ne pilote pas
        # une recette qui écrit chez un tiers ou dépense des crédits sans lire — même
        # quand la liste de son travail porterait l'outil.
        raise AuthzDenied(403, "side_effect_recipes_not_in_hosted_agents",
                          "Recipes that push to another app or submit paid jobs don't run "
                          "inside hosted agents. Run it yourself, or from a person's "
                          "session.")
    fastmcp = _instance()
    jeton = _epingler_org(ctx)
    try:
        return await moteur.executer(corps, _params(corps, inp.params), fastmcp=fastmcp,
                                     sub=ctx.sub, datastore=inp.datastore,
                                     reprise=inp.resume, ecrire=ecrire, pages_max=pages_max,
                                     temoin=temoin, cle_travail=(
                                         # Les paramètres RÉSOLUS (défauts compris) : la
                                         # même clé que la boucle des programmes.
                                         (recette_id, moteur.empreinte(
                                             _params(corps, inp.params)))
                                         if recette_id is not None else None))
    except moteur.RecetteRefusee as e:
        raise AuthzDenied(400, e.code, str(e)) from None
    finally:
        if jeton is not None:
            session_org.reset_call_org(jeton)


async def _recipe(ctx: ResolvedCtx, inp: RecipeInput) -> dict:
    if inp.op in ("schedule", "schedules", "set_schedule", "unschedule"):
        return await run_in_threadpool(_programmer, ctx, inp)
    if inp.op in _APPELANTES and current_token_axes().get("token_kind") == "delegation":
        raise AuthzDenied(403, "hosted_runs_not_supported",
                          "Recipes don't run inside hosted agents yet: the agent's job "
                          "doesn't carry its trigger's tool list to the server.")
    if inp.op not in _APPELANTES:
        return await run_in_threadpool(_gerer, ctx, inp)
    if inp.op == "sample":
        outil = tool_alias.canonical(_besoin(inp.tool, "missing_tool", "`tool` is required."),
                                     tool_alias.prefix_for(ctx.sub))
        fastmcp = _instance()
        jeton = _epingler_org(ctx)
        try:
            forme = await moteur.echantillon(fastmcp, ctx.sub, outil,
                                             inp.arguments or {}, inp.items or "")
        except moteur.RecetteRefusee as e:
            raise AuthzDenied(400, e.code, str(e)) from None
        finally:
            if jeton is not None:
                session_org.reset_call_org(jeton)
        return {"shape": forme}
    if inp.slug is None and inp.op == "run":
        raise AuthzDenied(400, "inline_recipe_cannot_write",
                          "Only a published recipe writes into a table: `create` it, "
                          "`publish` it (one test page), then `run` it by `slug`. An "
                          "inline `recipe` only serves `test`.")
    if inp.slug is None and inp.op == "test":
        corps = _corps(ctx, _besoin(inp.recipe, "missing_recipe",
                                    "Pass `slug` (a stored recipe) or `recipe` (inline)."))
        recu = await _executer(ctx, inp, corps, ecrire=False, pages_max=1)
        return {"receipt": recu}
    fiche = await run_in_threadpool(_fiche, ctx, inp)
    version = await run_in_threadpool(_version, fiche, inp.version)
    if inp.op == "run" and version["status"] != "publiee":
        raise AuthzDenied(409, "not_published",
                          f"Version {version['version']} of `{fiche['slug']}` is "
                          f"{version['status']}: only a published version runs. Publish "
                          "it first.")
    if inp.op == "run":
        return {"recipe": fiche, "version": {"version": version["version"]},
                "receipt": await _executer(ctx, inp, version["body"], ecrire=True,
                                           temoin=version.get("test_report"),
                                           recette_id=fiche["id"])}
    recu = await _executer(ctx, inp, version["body"], ecrire=False, pages_max=1,
                           recette_id=fiche["id"])
    if inp.op == "test":
        return {"recipe": fiche, "version": {"version": version["version"]}, "receipt": recu}
    # publish : l'épreuve doit avoir produit des lignes, sans refus.
    if recu["stopped"] in ("job_submitted", "job_running") and recu.get("resume") is None \
            and version["body"].get("start"):
        raise AuthzDenied(409, "test_pending",
                          "The job is running at the provider: call publish again in a "
                          "minute (the same job is reused, never started twice).",
                          details={"receipt": recu})
    if recu["stopped"] in ("job_submitted", "job_running"):
        # `async` : la publication exige un VRAI résultat collecté — repasser `resume`.
        raise AuthzDenied(409, "test_pending",
                          "The test job is running at the provider: call publish again "
                          "with this `resume` in a minute, until it is collected.",
                          details={"receipt": recu})
    if recu["stopped"] not in (None, "max_pages") or not recu["rows_built"]:
        raise AuthzDenied(409, "test_failed",
                          "The test page produced no row (or was refused): fix the recipe "
                          "or its params before publishing.", details={"receipt": recu})
    rapport = {k: recu.get(k) for k in ("items_seen", "rows_built", "skipped_where",
                                    "skipped_no_key", "fill")}
    await run_in_threadpool(db_recipes.decide_version, fiche["id"], version["version"],
                            status="publiee", decided_by=ctx.sub, test_report=rapport)
    return {"recipe": await run_in_threadpool(db_recipes.get_recipe, fiche["owner_type"],
                                              fiche["owner_id"], fiche["slug"]),
            "version": {"version": version["version"], "status": "publiee"}, "receipt": recu}


CAPABILITIES += [
    Capability(
        key="me.recipe", handler=_recipe, Input=RecipeInput, Output=RecipeOut,
        authz=BY_OP({op: _PORTEE for op in ("list", "get", "versions", "create", "propose",
                                            "sample", "test", "publish", "run", "schedule",
                                            "schedules", "set_schedule", "unschedule")}),
        description=(
            "RECIPES: move a connector tool's results into a table on the server, with no "
            "model reading or retyping the rows. Only connector tools DECLARED READ-ONLY can "
            "be called (never one that sends, posts or writes at the provider; never a "
            "model connector). A recipe names the tool and its arguments, "
            "how to page through the results, which field goes to which column, and the key "
            "column that identifies a row; existing rows are left untouched by default. "
            "op=sample (tool, arguments, items → the shape of its items: paths, types, fill; "
            "one billed call) · op=test (slug or inline `recipe`, params → one real page, "
            "nothing written, fill rate per column) · op=create (slug, title, recipe → "
            "version 1, PROPOSED) · op=propose (slug, expected_version, recipe) · "
            "op=publish (slug, version, params → test page, then in service) · op=run "
            "(slug of a PUBLISHED recipe, params, datastore → counts only; `max_pages` "
            "bounds each call, `resume` continues a partial run; a table without a declared "
            "key gets the recipe's key on the first run) · op=list · op=get · op=versions. "
            "`for_each` fans out: each row of a parent table whose `status_column` is empty "
            "drives the call (people per company…), then gets `done` or `empty`. "
            "`mode='per_row'` enriches the rows of `datastore` itself, one call per row "
            "(fills empty cells only by default; status done / not_found / "
            "failed:<code> / ambiguous). With `async` it submits paid jobs (Dropcontact, "
            "FullEnrich…) and collects them on the next run. `mode='push'` creates or "
            "updates records in a CRM or campaign tool (HubSpot, Folk, Attio, Pipedrive, "
            "Salesforce, lemlist), never twice: the record id comes back into the row; test "
            "and publish are a dry run that calls nothing. Neither push nor async runs "
            "inside a hosted agent. A run "
            "stops with `mapping_drift` when a column the published test filled comes back "
            "empty on a whole page. op=schedule (slug, every_minutes ≥ 15, params, "
            "datastore) runs a PUBLISHED recipe on its own, on your behalf, in this org "
            "(op=schedules lists them with their last receipt; set_schedule pauses or "
            "resumes; unschedule removes; 3 failures in a row pause it). "
            "`limits.max_units` is required: a hard cap on what the tool BILLS (its own "
            "units; `units_basis` in the receipt says when it falls back on the recipe's "
            "count). Scope: your active org (default) or `scope='user'`. Refused inside a "
            "hosted agent for now. PROVISIONAL surface."),
        mcp="oto_recipe",
    ),
]
