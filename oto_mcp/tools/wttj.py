"""Welcome to the Jungle — l'ATS des recruteurs (ex-Welcome Kit) : offres et leurs
étapes, candidats, commentaires, historique des déplacements.

Wrappe `oto.tools.wttj_ats.WttjAtsClient` (jeton en Bearer). Clé résolue par appel
via `access.resolve_api_key("wttj")` — byo (clé user ou credential partagé de
l'org), pas de clé plateforme. Le jeton ne se génère pas en libre-service : le
titulaire du compte le demande à WTTJ, avec des scopes OAuth choisis à ce moment ;
un appel hors de ses scopes revient en 403 `invalid_scope`, et le refus le dit.

Vocabulaire : tout part d'une **organisation** (`organization_reference`) ; une
offre est un **job** (`job_reference`), ses étapes de pipeline se lisent SUR le
job et s'adressent par leur `id` entier ; un **candidat** appartient à un job.
Pas de liste globale de candidats : l'API exige le job.

**Surface consolidée (ADR 0047 §Amendement)** : un tool par OBJET métier, le verbe
en `op` — `wttj_job` (list/get), `wttj_candidate` (list/get/create/update). Trois
tools restent seuls, leurs paramètres ne recouvrent pas ceux d'un voisin :
`wttj_organization` (les organisations du jeton, sans paramètre), `wttj_comment`
(une écriture, aucune lecture n'existe en amont) et `wttj_moves` (l'historique du
pipeline d'un job).

⚠️ Le détail d'une organisation (`GET /organizations/{ref}`) exige un scope de
partenaire (`su_organizations_r`) qu'un compte client n'obtient pas : il n'est pas
servi, la liste des organisations du jeton porte déjà leur nom et leur référence.

⚠️ Ce module ÉCRIT dans l'ATS du client : `wttj_candidate` op="create"/"update"
(l'update DÉPLACE un candidat par `job_stage_id` ou l'archive) et `wttj_comment`.
Le défaut d'`op` est toujours une lecture, et un argument obligatoire manquant
lève une erreur qui nomme l'op et l'argument. Les emails (qui partent à des
personnes réelles) et la publication d'offres ne sont pas servis.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access, output_projection
from ..connectors import verify as connector_verify
from ..mcp_errors import McpError

#: Où le titulaire du compte obtient son jeton.
OU_OBTENIR_LA_CLE = ("demande à WTTJ via help.welcometothejungle.com "
                     "(le jeton n'est pas généré en libre-service)")

_JOB_OPS = ("list", "get")
_CANDIDATE_OPS = ("list", "get", "create", "update")

#: Colonnes-corps rendues en `<champ>_length` dans une liste (vue de tri).
_JOB_BODIES = ("description", "profile", "company_description", "recruitment_process")
_CANDIDATE_BODIES = ("cover_letter",)


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _ops_error(ops: tuple[str, ...]) -> str:
    quoted = [f"'{o}'" for o in ops]
    return "op doit être " + ", ".join(quoted[:-1]) + " ou " + quoted[-1]


def _need(value, name: str, op: str):
    """Argument obligatoire pour CET op — une valeur vide compte comme absente."""
    if value is None or (isinstance(value, (str, list, dict)) and not value):
        raise _bad(f"op='{op}' requiert {name}")
    return value


def _refuse_ignored(op: str, hint: str, **provided) -> None:
    """Un argument fourni que CET op n'utilise pas est une erreur d'intention : le
    taire rendrait un résultat plausible mais à côté de la demande."""
    for name, value in provided.items():
        if value is not None and value != "":
            raise _bad(f"op='{op}' n'utilise pas {name} — {hint}")


def _upstream_message(e) -> str:
    status, body = e.status_code, e.body
    code = body.get("error") if isinstance(body, dict) else None
    if status == 401:
        return ("WTTJ a rejeté le jeton (HTTP 401) — vérifie la clé configurée sur "
                f"ce connecteur ({OU_OBTENIR_LA_CLE}).")
    if status == 403:
        if code == "invalid_scope":
            return ("WTTJ : le jeton n'a pas le scope requis pour cet appel (HTTP 403 "
                    f"invalid_scope) — les scopes se demandent à WTTJ. {body}")
        return f"WTTJ : accès refusé à cette ressource (HTTP 403). {body}"
    if status == 404:
        return f"WTTJ : référence introuvable (404) — vérifie la référence. {body}"
    if status == 429:
        return "WTTJ : trop de requêtes (429) — réessaie dans un instant."
    if status in (500, 502, 503, 504):
        return f"WTTJ est momentanément indisponible (HTTP {status}) — réessaie plus tard."
    return f"WTTJ a refusé la requête (HTTP {status}) : {body}"


def _listed(key: str, rows, *, bodies: tuple[str, ...], always: tuple[str, ...],
            fields: Optional[list[str]], page: Optional[int],
            per_page: Optional[int]) -> dict:
    """Une page de liste, projetée : l'API rend un tableau nu, sans total ni curseur —
    la page demandée est rendue avec, pour que l'appelant sache où il en est."""
    rows, notice = output_projection.summarize(
        rows if isinstance(rows, list) else [], body_fields=bodies, fields=fields,
        always=always)
    out = {key: rows, "page": page or 1, "per_page": per_page, "count": len(rows)}
    if notice:
        out["projection"] = notice
    return out


def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Sonde « tester la connexion » (otomata-tech/oto#69). Couvre `auth` SEUL.

    `GET /users/current` (scope `me_r`), lecture sans effet de bord. **Authentifié ≠
    utilisable** : un jeton valide peut manquer des scopes `jobs_r`/`candidates_*`
    qu'aucune sonde à un appel ne couvre."""
    from oto.tools.wttj_ats import WttjAtsClient

    WttjAtsClient(api_key=fields["key"]).get_current_user()


def register(mcp: FastMCP) -> None:
    from oto.tools.common.errors import UpstreamHTTPError
    from oto.tools.wttj_ats import WttjAtsClient

    connector_verify.register("wttj", _verify)

    def _client() -> WttjAtsClient:
        key, _ = access.resolve_api_key("wttj")
        return WttjAtsClient(api_key=key)

    def _run(fn):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e))

    # --- Organisations ------------------------------------------------------

    @mcp.tool()
    def wttj_organization() -> dict:
        """The Welcome to the Jungle organizations this token reaches — start here:
        every other wttj tool needs an `organization_reference` or a job. Returns
        the token's user with `organizations: [{reference, name, …}]`."""
        return {"user": _run(lambda: _client().get_current_user(organizations=True))}

    # --- Offres -------------------------------------------------------------

    @mcp.tool()
    def wttj_job(
        op: Literal["list", "get"] = "list",
        organization_reference: Optional[str] = None,
        job_reference: Optional[str] = None,
        status: Optional[Literal["draft", "published", "archived"]] = None,
        created_after: Optional[str] = None,
        updated_after: Optional[str] = None,
        published_after: Optional[str] = None,
        page: Optional[int] = None,
        per_page: Optional[int] = None,
        fields: Optional[list[str]] = None,
        candidates_count: Optional[bool] = None,
    ) -> dict:
        """A job (offer) in Welcome to the Jungle's ATS — list, or read one with its
        pipeline stages.

        `op`:
        - **"list"** (default): jobs of one organization (`organization_reference`).
          Sorting view: long text columns come back as `<field>_length`;
          `fields=["*"]` returns full rows, `fields=[…]` picks keys.
        - **"get"**: one job (`job_reference`) WITH its pipeline `stages` —
          `[{id, name, reference, visible, candidates_count}]`. A stage is
          addressed by its integer `id` (its `reference` may be null): that id
          feeds `wttj_candidate` (`job_stage_id`).

        Args:
            status: op="list" — draft | published | archived.
            created_after / updated_after / published_after: op="list" — YYYY-MM-DD.
            page / per_page: op="list" — 1-based page; no total is returned, an
                empty or short page means the end.
            candidates_count: op="get" — add the job's total candidate count
                (needs the `candidates_r` scope).
        """
        if op not in _JOB_OPS:
            raise _bad(_ops_error(_JOB_OPS))
        if op == "list":
            _refuse_ignored(op, "n'existe que sur op='get'", job_reference=job_reference,
                            candidates_count=candidates_count)
            org = _need(organization_reference, "organization_reference", op)
            rows = _run(lambda: _client().list_jobs(
                org, status=status, created_after=created_after,
                updated_after=updated_after, published_after=published_after,
                page=page, per_page=per_page))
            return _listed("jobs", rows, bodies=_JOB_BODIES,
                           always=("reference", "name", "status"), fields=fields,
                           page=page, per_page=per_page)
        if op == "get":
            _refuse_ignored(op, "n'existe que sur op='list'",
                            organization_reference=organization_reference,
                            status=status, created_after=created_after,
                            updated_after=updated_after,
                            published_after=published_after, page=page,
                            per_page=per_page, fields=fields)
            ref = _need(job_reference, "job_reference", op)
            return {"job": _run(lambda: _client().get_job(
                ref, stages=True, candidates_count=candidates_count))}
        raise _bad(_ops_error(_JOB_OPS))

    # --- Candidats ----------------------------------------------------------

    @mcp.tool()
    def wttj_candidate(
        op: Literal["list", "get", "create", "update"] = "list",
        job_reference: Optional[str] = None,
        candidate_reference: Optional[str] = None,
        job_stage_id: Optional[int] = None,
        email: Optional[str] = None,
        archived: Optional[bool] = None,
        created_after: Optional[str] = None,
        updated_after: Optional[str] = None,
        page: Optional[int] = None,
        per_page: Optional[int] = None,
        fields: Optional[list[str]] = None,
        organization_reference: Optional[str] = None,
        firstname: Optional[str] = None,
        lastname: Optional[str] = None,
        candidate: Optional[dict] = None,
        patch: Optional[dict] = None,
    ) -> dict:
        """A candidate on a job in Welcome to the Jungle's ATS — list, read, add,
        move or archive.

        `op`:
        - **"list"** (default): candidates of one job (`job_reference` — there is
          no cross-job listing), optionally at one stage (`job_stage_id`), by
          `email`, `archived`, dates. Sorting view: `cover_letter` comes back as
          `cover_letter_length`; `fields=["*"]` returns full rows.
        - **"get"**: one candidate (`candidate_reference`), with its stage and tags.
        - **"create"** — ⚠️ WRITES: add a candidate to a job at a stage.
          Required: `organization_reference`, `job_reference`, `job_stage_id`
          (from `wttj_job(op="get")`), `email`, `firstname`, `lastname`.
          Optional fields in `candidate`: `phone`, `subtitle`, `tag_list`
          (comma separated), `cover_letter`, `comment`, `referrer`,
          `remote_resume_url` (PDF/DOC/DOCX/ODT, 5 MB), `media_linkedin`, …
        - **"update"** — ⚠️ WRITES: change a candidate (`candidate_reference`):
          `job_stage_id` MOVES it to that stage, `archived=true` archives it;
          other fields go in `patch` (same names as `candidate`). Only what is
          passed changes.

        Args:
            created_after / updated_after: op="list" — YYYY-MM-DD.
            page / per_page: op="list" — 1-based page; an empty or short page
                means the end.
        """
        if op not in _CANDIDATE_OPS:
            raise _bad(_ops_error(_CANDIDATE_OPS))

        if op == "list":
            _refuse_ignored(op, "sert à create/update ou get",
                            candidate_reference=candidate_reference,
                            organization_reference=organization_reference,
                            firstname=firstname, lastname=lastname,
                            candidate=candidate, patch=patch)
            job = _need(job_reference, "job_reference", op)
            rows = _run(lambda: _client().list_candidates(
                job, email=email, job_stage_id=job_stage_id, archived=archived,
                created_after=created_after, updated_after=updated_after,
                stage=True, page=page, per_page=per_page))
            return _listed("candidates", rows, bodies=_CANDIDATE_BODIES,
                           always=("reference", "stage_id"), fields=fields,
                           page=page, per_page=per_page)
        if op == "get":
            ref = _need(candidate_reference, "candidate_reference", op)
            return {"candidate": _run(lambda: _client().get_candidate(
                ref, stage=True, tags=True))}
        if op == "create":
            _refuse_ignored(op, "utilise op='update' pour modifier un candidat existant",
                            candidate_reference=candidate_reference, patch=patch)
            args = (_need(organization_reference, "organization_reference", op),
                    _need(job_reference, "job_reference", op),
                    _need(job_stage_id, "job_stage_id", op),
                    _need(email, "email", op),
                    _need(firstname, "firstname", op),
                    _need(lastname, "lastname", op))
            extra = dict(candidate or {})
            if archived is not None:
                extra["archived"] = archived
            return {"candidate": _run(lambda: _client().create_candidate(*args, **extra))}
        if op == "update":
            _refuse_ignored(op, "ne sert qu'à op='create'",
                            organization_reference=organization_reference,
                            candidate=candidate)
            ref = _need(candidate_reference, "candidate_reference", op)
            changes = dict(patch or {})
            for name, value in (("job_stage_id", job_stage_id), ("archived", archived),
                                ("email", email), ("firstname", firstname),
                                ("lastname", lastname)):
                if value is not None:
                    changes[name] = value
            if not changes:
                raise _bad("op='update' requiert au moins un changement : "
                           "job_stage_id, archived, ou des champs dans patch")
            return {"candidate": _run(lambda: _client().update_candidate(ref, **changes))}
        raise _bad(_ops_error(_CANDIDATE_OPS))

    # --- Commentaires -------------------------------------------------------

    @mcp.tool()
    def wttj_comment(candidate_reference: str, content: str) -> dict:
        """⚠️ WRITES: add a comment on a candidate (text, HTML or markdown). The
        API cannot list comments back — this is write-only."""
        return {"comment": _run(lambda: _client().create_comment(
            candidate_reference, content))}

    # --- Historique du pipeline ---------------------------------------------

    @mcp.tool()
    def wttj_moves(
        organization_reference: str,
        job_reference: str,
        page: Optional[int] = None,
        per_page: Optional[int] = None,
        fields: Optional[list[str]] = None,
    ) -> dict:
        """History of stage changes of ONE job (`job_reference` is required by the
        API): each `{candidate: {reference}, from: {stage, job}, to: {stage, job},
        created_at}`. Answers « who moved where, when » on that job.

        Needs the `moves_r` scope; WTTJ may also require a partner scope
        (`su_moves_r`) that client accounts do not get — the refusal then names
        it, and the history cannot be read with that token."""
        rows = _run(lambda: _client().list_moves(
            organization_reference, job_reference=job_reference, page=page,
            per_page=per_page))
        return _listed("moves", rows, bodies=(), always=("candidate", "created_at"),
                       fields=fields, page=page, per_page=per_page)
