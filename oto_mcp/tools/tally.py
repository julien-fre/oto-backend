"""Tally — formulaires en ligne : formulaires, questions, blocs, réponses,
analytics, espaces de travail, organisation, webhooks.

Wrappe `oto.tools.tally.client.TallyClient` (Bearer, `https://api.tally.so`).
keyed `api_key`, **byo-only** : une clé Tally est liée à UN utilisateur, hérite
de ses droits (aucun scope fin n'existe) et cesse de fonctionner s'il quitte
l'organisation — il ne peut donc pas y avoir de clé plateforme partagée.

38 opérations, groupées en **SIX tools**, verbe en `op` :
- `tally_form` — formulaires, questions, blocs (lecture + rédaction)
- `tally_submission` — les réponses
- `tally_analytics` — les cinq vues de statistiques
- `tally_workspace` — espaces de travail et dossiers
- `tally_account` — l'utilisateur courant, les membres, les invitations
- `tally_webhook` — abonnements aux événements et journal de livraison

**Aucun param n'est retenu au silence** : un argument qu'un `op` n'utilise pas
est REFUSÉ plutôt qu'ignoré (convention silae `_refuse_ignored`) —
`tally_submission(op="get", filter="completed")` rendrait UNE réponse en
laissant croire que le filtre a filtré.

## Ce que la couche tool ajoute au transport

**1. La jointure questions × réponses.** L'API rend les réponses sous forme
RELATIONNELLE : `questions[]` une fois par page, et chaque
`submissions[].responses[]` pointe dedans par `questionId`. Brut, un agent doit
faire la jointure lui-même avant de lire un seul champ. `tally_submission`
la fait : chaque réponse porte `answers: [{question_id, title, type, answer,
formatted}]`, et un `answers_by_title` **seulement si les titres sont uniques
sur ce formulaire** (sinon la clé est absente et `title_collisions` dit
pourquoi — on ne rend jamais une map qui écrase silencieusement une réponse).

**2. `dry_run` sur TOUTE mutation** (convention oto : `email_send`, LinkedIn,
les dispatchers Folk). La validation est identique avec ou sans ; seul l'appel
mutant final est sauté. Là où l'API offre une lecture de l'objet visé, le
preview est un VRAI diff (`changes: {champ: {from, to}}`), pas un écho de ce
qu'on enverrait — un écho ne protège pas du risque réel, qui est d'écraser une
valeur existante. Là où aucune lecture n'existe, on le DIT
(`current_available: false`) au lieu de fabriquer un diff.

**3. Le merge de `PATCH /webhooks/{id}`.** Malgré le verbe, c'est un REMPLACEMENT
complet : `formId`, `url`, `eventTypes` et `isEnabled` sont tous requis. Le
tool relit le webhook (`GET /webhooks`, il n'y a pas de `GET /webhooks/{id}`)
et fusionne, pour qu'un `op="update"` qui ne passe que `is_enabled=False`
n'efface pas l'URL.

## Opérations destructrices — exposées, et marquées

À la demande explicite : la couverture est complète, y compris les quatre
appels qui détruisent quelque chose. Ils ne sont pas cachés, ils sont annotés
et tous acceptent `dry_run` :
- `tally_submission(op="delete")` — **aucune corbeille documentée côté Tally
  pour les réponses** : c'est la réponse d'un répondant, perdue.
- `tally_account(op="remove_user")` — sort quelqu'un de l'organisation ET tue
  toutes les clés API qu'il avait créées, potentiellement celle de l'appel.
- `tally_workspace(op="delete")` / `op="delete_folder"` — emportent les
  formulaires contenus (en corbeille, restaurables).
- `tally_form(op="delete")` — corbeille, restaurable.

## Vérifié contre le spec, PAS testé en live

Tout ce fichier est dérivé du spec OpenAPI 3.0.1 réel
(`developers.tally.so/api-reference/openapi.json`, lu le 2026-08-31) : formes
de corps, `required`, enums, bornes de `limit`. **Aucun appel réel n'a encore
été fait** — il n'y avait pas de clé `tly-` disponible. Rien ici ne prétend
avoir tourné. En particulier, la valeur par défaut de l'en-tête
`tally-version` (`2026-08-04`, la dernière entrée du changelog public) reste à
confirmer contre une vraie clé.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastmcp import FastMCP
from ..mcp_errors import McpError
from mcp.types import ErrorData, INVALID_PARAMS

from .. import access
from ..connectors import verify as connector_verify

#: Périodes acceptées par les cinq endpoints d'analytics (`period` est REQUIS).
_PERIODS = ("today", "yesterday", "24h", "7d", "30d", "3m", "6m", "12m", "all")


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def _refuse_ignored(op: str, hint: str, **provided: Any) -> None:
    """Un argument fourni que CET op n'utilise pas est une erreur d'intention,
    pas un détail — sinon `tally_form(op="get", limit=5)` rendrait UN
    formulaire en laissant croire que `limit` a borné quelque chose."""
    for name, value in provided.items():
        if value is not None:
            raise _bad(f"op={op!r} n'utilise pas `{name}` — {hint}")


def _need(op: str, **required: Any) -> None:
    """Un argument obligatoire manquant se dit avant l'appel réseau."""
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise _bad(f"op={op!r} exige {', '.join('`' + m + '`' for m in missing)}.")


#: Précisions ajoutées au message d'un 401 selon l'appel — le message de base,
#: lui, ne suppose JAMAIS que la clé est en cause (voir `_upstream_message`).
_401_HINTS = {
    "webhook_list": "aucun webhook n'a encore jamais été créé sur ce compte "
                    "(l'intégration webhooks naît au premier `op=\"create\"`)",
    "blocks": "la lecture ou l'écriture des blocs n'est pas ouverte à ton plan",
    "workspace_write": "la gestion des espaces de travail exige un plan Pro",
    "question_write": "le renommage d'une question n'est pas ouvert à ton plan",
}


def _upstream_message(e: Any, context: Optional[str] = None) -> str:
    status = e.status_code
    if status == 401:
        # ⚠️ Tally rend 401 pour un GATE DE PLAN ou de fonctionnalité autant que
        # pour une clé invalide — vérifié en live le 2026-08-31 sur un compte
        # FREE : `GET /webhooks` (tant qu'aucun webhook n'existe), les blocs,
        # `POST /workspaces` et `PATCH .../questions/{id}` rendent tous 401 avec
        # une clé parfaitement valide. Affirmer « clé rejetée » envoie donc
        # l'utilisateur régénérer une clé saine, et le laisse chercher là où il
        # n'y a rien. On nomme les deux causes, et on donne la sonde qui tranche.
        hint = _401_HINTS.get(context)
        return ("Tally a répondu 401. Chez Tally, un 401 ne veut PAS dire « clé "
                "invalide » : c'est aussi sa façon de refuser une fonctionnalité que "
                "ton plan n'ouvre pas"
                + (f" — ici, {hint}" if hint else "")
                + ". Tranche avec `tally_account(op=\"me\")` : s'il répond, la clé est "
                  "bonne et c'est le plan (ou l'état du compte) qui bloque ; s'il échoue "
                  "aussi, repose la clé (Tally : Settings → API keys).")
    if status == 403:
        return (f"Tally a rejeté l'appel (HTTP {status}) — vérifie la clé posée sur ce "
                "connecteur (Tally : Settings → API keys). Rappel : une clé Tally est liée "
                "à UN utilisateur et cesse de fonctionner s'il quitte l'organisation.")
def _verify(fields: dict, config: dict | None = None) -> None:  # noqa: ARG001
    """Sonde « tester la connexion » : l'utilisateur courant. Sans paramètre,
    gratuite, et elle échoue exactement là où la clé est en cause."""
    from oto.tools.tally.client import TallyClient
    TallyClient(api_key=fields["key"]).get_me()


# ---------------------------------------------------------------------------
# Mise en forme des réponses (la jointure questions × responses)
# ---------------------------------------------------------------------------

def _index_questions(questions: Optional[List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    return {q.get("id"): q for q in (questions or []) if isinstance(q, dict) and q.get("id")}


def _shape_submission(sub: Dict[str, Any], by_id: Dict[str, Dict[str, Any]],
                      unique_titles: bool) -> Dict[str, Any]:
    """Rend une réponse LISIBLE : chaque `responses[]` rejoint sa question.

    Ce que l'API donne (`{questionId, answer, formattedAnswer}`) n'est
    interprétable qu'avec la table `questions` livrée à côté. On la résout ici
    une fois pour toutes plutôt que de laisser chaque agent la refaire.
    """
    answers = []
    for r in sub.get("responses") or []:
        if not isinstance(r, dict):
            continue
        q = by_id.get(r.get("questionId")) or {}
        answers.append({
            "question_id": r.get("questionId"),
            "title": q.get("title"),
            "type": q.get("type"),
            "answer": r.get("answer"),
            # `formattedAnswer` n'existe que sur les versions récentes de l'API
            # (cf. l'en-tête `tally-version` épinglé côté client) : absent, on
            # ne le fabrique pas.
            "formatted": r.get("formattedAnswer"),
        })
    shaped = {
        "id": sub.get("id"),
        "form_id": sub.get("formId"),
        "respondent_id": sub.get("respondentId"),
        "is_completed": sub.get("isCompleted"),
        "submitted_at": sub.get("submittedAt"),
        # Tally rend chaque réponse en PDF et en aperçu web — utile pour
        # archiver la pièce sans la reconstituer soi-même.
        "preview_url": sub.get("previewUrl"),
        "pdf_url": sub.get("pdfUrl"),
        "answers": answers,
    }
    if unique_titles:
        shaped["answers_by_title"] = {
            a["title"]: (a["formatted"] if a["formatted"] is not None else a["answer"])
            for a in answers if a["title"]
        }
    return shaped


def _shape_submissions_page(payload: Any) -> Any:
    """Ajoute la vue jointe SANS retirer la charge d'origine.

    On ne remplace jamais ce que l'API a rendu : `questions` et les réponses
    restent tels quels sous `raw_*`, pour qu'un cas non prévu ici reste
    traitable par l'appelant.

    ⚠️ DEUX enveloppes, pas une. `GET /forms/{f}/submissions` rend
    `{questions, submissions: [...]}` ; `GET /forms/{f}/submissions/{s}` rend
    `{questions, submission: {...}}` — au SINGULIER. Ne lire que le pluriel
    rendait la lecture d'UNE réponse silencieusement vide : la charge arrivait
    bien, et le tool annonçait zéro réponse.
    """
    if not isinstance(payload, dict):
        return payload
    questions = payload.get("questions")
    single = payload.get("submission")
    if single is not None and payload.get("submissions") is None:
        payload = {**payload, "submissions": [single] if isinstance(single, dict) else []}
    by_id = _index_questions(questions)
    titles = [q.get("title") for q in by_id.values() if q.get("title")]
    collisions = sorted({t for t in titles if titles.count(t) > 1})
    unique = not collisions
    subs = [_shape_submission(s, by_id, unique)
            for s in (payload.get("submissions") or []) if isinstance(s, dict)]
    out = {
        "page": payload.get("page"),
        "limit": payload.get("limit"),
        "has_more": payload.get("hasMore"),
        "counts": payload.get("totalNumberOfSubmissionsPerFilter"),
        "questions": [{"id": q.get("id"), "type": q.get("type"), "title": q.get("title")}
                      for q in by_id.values()],
        "submissions": subs,
    }
    if collisions:
        # Deux questions portant le même intitulé : une map par titre en
        # écraserait une. On l'omet et on dit lesquelles.
        out["title_collisions"] = collisions
        out["note"] = ("`answers_by_title` est omis : plusieurs questions partagent le même "
                       "intitulé sur ce formulaire. Utilise `answers[].question_id`.")
    if payload.get("submissions") and not subs:
        out["submissions"] = payload.get("submissions")
    out["raw_questions"] = questions
    if single is not None:
        out["raw_submission"] = single
    return out


def _diff(current: Optional[Dict[str, Any]], patch: Dict[str, Any],
          field_map: Dict[str, str]) -> Dict[str, Any]:
    """Preview d'une modification : un VRAI diff quand l'objet actuel est
    lisible, un aveu explicite quand il ne l'est pas."""
    if current is None:
        return {"dry_run": True, "current_available": False, "would_send": patch}
    changes = {}
    for api_field, value in patch.items():
        if value is None:
            continue
        before = current.get(field_map.get(api_field, api_field))
        if before != value:
            changes[api_field] = {"from": before, "to": value}
    return {"dry_run": True, "current_available": True, "changes": changes,
            "unchanged": not changes}


def register(mcp: FastMCP) -> None:
    from oto.tools.tally.client import TallyClient
    from oto.tools.common.errors import UpstreamHTTPError

    connector_verify.register("tally", _verify)

    def _client() -> TallyClient:
        key, _ = access.resolve_api_key("tally")
        return TallyClient(api_key=key)

    def _run(fn, context: Optional[str] = None):
        try:
            return fn()
        except ValueError as e:
            raise _bad(str(e))
        except UpstreamHTTPError as e:
            raise _bad(_upstream_message(e, context))

    # ================================================================
    # Formulaires, questions, blocs
    # ================================================================

    @mcp.tool()
    def tally_form(
        op: Literal["list", "get", "create", "update", "delete",
                    "questions", "update_question", "blocks", "update_blocks"] = "list",
        form_id: Optional[str] = None,
        question_id: Optional[str] = None,
        name: Optional[str] = None,
        title: Optional[str] = None,
        status: Optional[Literal["BLANK", "DRAFT", "PUBLISHED"]] = None,
        blocks: Optional[List[dict]] = None,
        settings: Optional[dict] = None,
        workspace_id: Optional[str] = None,
        folder_id: Optional[str] = None,
        template_id: Optional[str] = None,
        workspace_ids: Optional[List[str]] = None,
        page: Optional[int] = None,
        limit: Optional[int] = None,
        dry_run: bool = False,
    ) -> object:
        """Tally forms — list, read, author, and delete them.

        Returns —
            The Tally payload for reads; the created/updated object for
            writes; a `{dry_run: true, ...}` preview when `dry_run` is set.

        Args:
            op: which action.
                · "list" — the forms this key can see. `page` (1-based),
                  `limit` (1-500, default 50), `workspace_ids` to filter
                  (resolve ids with `tally_workspace(op="list")`).
                · "get" — one form with all its blocks and settings (`form_id`).
                · "create" — a new form. `blocks` and `status` are required by
                  the API; pass `blocks=[]` for an empty one. Optional
                  `workspace_id`, `folder_id`, `template_id`, `settings`.
                · "update" — change `name`, `status`, `blocks` or `settings`
                  on `form_id`.
                · "delete" — move `form_id` to the trash (restorable).
                · "questions" — the answerable questions of `form_id`. Their
                  `id` is what a submission's `question_id` points at.
                · "update_question" — rename `question_id` on `form_id` via
                  `title` (the only editable field).
                · "blocks" — the authoring view of `form_id`: every block,
                  including layout ones that are never answered.
                · "update_blocks" — replace the block list of `form_id`.
            blocks: ordered list of block objects
                (`{uuid, type, groupUuid, groupType, payload}`). 39 block
                types exist — see https://developers.tally.so/blocks-reference
                for each payload shape. ⚠️ On "update" and "update_blocks"
                this REPLACES the whole array: a block you leave out is
                deleted. Read the current set with op="blocks" first.
            settings: form settings — `language`, `isClosed`, `closeDate`,
                `closeTime`, `submissionsLimit`, `redirectOnCompletion`,
                `hasProgressBar`, `hasPartialSubmissions`, `password`,
                `submissionsDataRetentionDuration`/`Unit`, and the self /
                respondent email-notification block.
            status: "BLANK", "DRAFT" or "PUBLISHED". "DELETED" was removed by
                Tally on 2026-08-04 and now returns 400 — it left forms
                unrecoverable; use op="delete" instead.
            dry_run: validate and preview without writing. On "update" and
                "delete" the preview is a real diff against the current form.
        """
        c = _client()

        if op == "list":
            _refuse_ignored(op, "il liste les formulaires",
                            form_id=form_id, question_id=question_id, name=name,
                            title=title, status=status, blocks=blocks, settings=settings,
                            workspace_id=workspace_id, folder_id=folder_id,
                            template_id=template_id)
            return _run(lambda: c.list_forms(page=page, limit=limit,
                                             workspaceIds=workspace_ids))

        if op == "get":
            _need(op, form_id=form_id)
            _refuse_ignored(op, "il lit UN formulaire", page=page, limit=limit,
                            workspace_ids=workspace_ids, blocks=blocks, settings=settings)
            return _run(lambda: c.get_form(form_id))

        if op == "questions":
            _need(op, form_id=form_id)
            _refuse_ignored(op, "il liste les questions du formulaire",
                            page=page, limit=limit, blocks=blocks, settings=settings)
            return _run(lambda: c.list_questions(form_id))

        if op == "blocks":
            _need(op, form_id=form_id)
            _refuse_ignored(op, "il lit les blocs du formulaire",
                            page=page, limit=limit, blocks=blocks, settings=settings)
            return _run(lambda: c.get_blocks(form_id), "blocks")

        if op == "create":
            _need(op, blocks=blocks, status=status)
            _refuse_ignored(op, "il crée un formulaire",
                            form_id=form_id, question_id=question_id, title=title,
                            page=page, limit=limit, workspace_ids=workspace_ids)
            body = {"workspaceId": workspace_id, "folderId": folder_id,
                    "templateId": template_id, "settings": settings, "name": name}
            if dry_run:
                return {"dry_run": True, "would_create": {
                    "status": status, "blocks": len(blocks), **{k: v for k, v in body.items()
                                                                if v is not None}}}
            return _run(lambda: c.create_form(
                blocks=blocks, status=status,
                **{k: v for k, v in body.items() if v is not None}))

        if op == "update":
            _need(op, form_id=form_id)
            _refuse_ignored(op, "il modifie UN formulaire",
                            question_id=question_id, title=title, page=page, limit=limit,
                            workspace_ids=workspace_ids, template_id=template_id)
            patch = {"name": name, "status": status, "blocks": blocks, "settings": settings}
            if all(v is None for v in patch.values()):
                raise _bad("op='update' exige au moins un de `name`, `status`, "
                           "`blocks`, `settings`.")
            if dry_run:
                current = _run(lambda: c.get_form(form_id))
                return _diff(current if isinstance(current, dict) else None,
                             {k: v for k, v in patch.items() if v is not None}, {})
            return _run(lambda: c.update_form(
                form_id, **{k: v for k, v in patch.items() if v is not None}))

        if op == "delete":
            _need(op, form_id=form_id)
            _refuse_ignored(op, "il supprime UN formulaire", blocks=blocks,
                            settings=settings, page=page, limit=limit)
            if dry_run:
                current = _run(lambda: c.get_form(form_id))
                return {"dry_run": True, "would_delete": "form", "form_id": form_id,
                        "current": current,
                        "recoverable": "oui — Tally met le formulaire à la corbeille"}
            return _run(lambda: c.delete_form(form_id))

        if op == "update_question":
            _need(op, form_id=form_id, question_id=question_id, title=title)
            _refuse_ignored(op, "il renomme UNE question", blocks=blocks, settings=settings,
                            name=name, status=status, page=page, limit=limit)
            if dry_run:
                qs = _run(lambda: c.list_questions(form_id))
                items = qs if isinstance(qs, list) else (qs or {}).get("questions") or []
                cur = next((q for q in items if isinstance(q, dict)
                            and q.get("id") == question_id), None)
                return _diff(cur, {"title": title}, {})
            return _run(lambda: c.update_question(form_id, question_id, title=title),
                        "question_write")

        if op == "update_blocks":
            _need(op, form_id=form_id, blocks=blocks)
            _refuse_ignored(op, "il remplace les blocs", question_id=question_id,
                            title=title, name=name, status=status, page=page, limit=limit)
            if dry_run:
                current = _run(lambda: c.get_blocks(form_id), "blocks")
                n_before = len(current) if isinstance(current, list) else None
                return {"dry_run": True, "current_available": n_before is not None,
                        "blocks_before": n_before, "blocks_after": len(blocks),
                        "warning": "PATCH blocks REMPLACE la liste entière — "
                                   "tout bloc absent de `blocks` est supprimé."}
            return _run(lambda: c.update_blocks(
                form_id, blocks, **({"settings": settings} if settings else {})), "blocks")

        raise _bad(f"op inconnu : {op!r}")

    # ================================================================
    # Réponses
    # ================================================================

    @mcp.tool()
    def tally_submission(
        op: Literal["list", "get", "delete"] = "list",
        form_id: Optional[str] = None,
        submission_id: Optional[str] = None,
        filter: Optional[Literal["all", "completed", "partial"]] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        after_id: Optional[str] = None,
        page: Optional[int] = None,
        limit: Optional[int] = None,
        raw: bool = False,
        dry_run: bool = False,
    ) -> object:
        """Form submissions — the answers people sent.

        The API returns these RELATIONALLY: a `questions` table once per page,
        and each response pointing into it by `questionId`. This tool joins
        them, so every submission carries `answers: [{question_id, title,
        type, answer, formatted}]` plus `answers_by_title` when the form's
        question titles are unique. Pass `raw=True` for Tally's untouched
        payload.

        Returns —
            "list" — `{page, limit, has_more, counts, questions, submissions}`
            where each submission carries `answers`, `preview_url` and
            `pdf_url`. File-upload questions answer with the uploaded file's
            URL, so `answers` is also how you reach attachments.

        Args:
            op: which action.
                · "list" — submissions of `form_id`.
                · "get" — one submission (`form_id` + `submission_id`).
                · "delete" — ⚠️ DESTROYS a respondent's answer. Tally documents
                  a trash for forms and workspaces but NOT for submissions:
                  treat this as permanent. Supports `dry_run`.
            filter: "all", "completed" or "partial". "partial" is how you see
                who started and abandoned — a follow-up signal, not noise.
            after_id: return submissions that came AFTER this submission id.
                This is the right cursor for incremental ingestion (poll the
                last id you stored) and is cheaper than date windows.
            start_date: ISO 8601 — submitted on or after.
            end_date: ISO 8601 — submitted on or before.
            page: 1-based page number.
            limit: 1-500, default 50. `limit=1` is the cheap way to read the
                per-filter counts without pulling any rows.
            raw: return Tally's payload as-is, skipping the join.
            dry_run: on "delete", preview the submission instead of deleting.
        """
        c = _client()

        if op == "list":
            _need(op, form_id=form_id)
            _refuse_ignored(op, "il liste les réponses", submission_id=submission_id)
            payload = _run(lambda: c.list_submissions(
                form_id, page=page, limit=limit, filter=filter,
                startDate=start_date, endDate=end_date, afterId=after_id))
            return payload if raw else _shape_submissions_page(payload)

        if op == "get":
            _need(op, form_id=form_id, submission_id=submission_id)
            _refuse_ignored(op, "il lit UNE réponse", filter=filter, start_date=start_date,
                            end_date=end_date, after_id=after_id, page=page, limit=limit)
            payload = _run(lambda: c.get_submission(form_id, submission_id))
            return payload if raw else _shape_submissions_page(payload)

        if op == "delete":
            _need(op, form_id=form_id, submission_id=submission_id)
            _refuse_ignored(op, "il supprime UNE réponse", filter=filter,
                            start_date=start_date, end_date=end_date, after_id=after_id,
                            page=page, limit=limit)
            if dry_run:
                current = _run(lambda: c.get_submission(form_id, submission_id))
                return {"dry_run": True, "would_delete": "submission",
                        "submission_id": submission_id,
                        "current": current if raw else _shape_submissions_page(current),
                        "recoverable": "non — aucune corbeille documentée pour les réponses"}
            return _run(lambda: c.delete_submission(form_id, submission_id))

        raise _bad(f"op inconnu : {op!r}")

    # ================================================================
    # Analytics
    # ================================================================

    @mcp.tool()
    def tally_analytics(
        op: Literal["metrics", "visits", "submissions", "dimensions", "drop_off"] = "metrics",
        form_id: Optional[str] = None,
        period: Optional[str] = None,
    ) -> object:
        """Form analytics — five views, one required time window.

        Args:
            op: which view.
                · "metrics" — aggregate: visits, submissions, completion rate.
                · "visits" — visit counts over time.
                · "submissions" — completed and partial counts over time.
                · "dimensions" — visitors by source, browser, OS, device,
                  location.
                · "drop_off" — where respondents abandon, question by
                  question. The one that tells you WHY a form under-converts.
            form_id: the form.
            period: REQUIRED — "today", "yesterday", "24h", "7d", "30d",
                "3m", "6m", "12m" or "all".
        """
        _need(op, form_id=form_id, period=period)
        if period not in _PERIODS:
            raise _bad(f"`period` doit être l'un de {', '.join(_PERIODS)} — reçu {period!r}.")
        c = _client()
        # Appels EXPLICITES et non un dispatch par dict : le garde-fou
        # version-skew (`tests/test_tools_client_methods_exist.py`) lit les
        # appels `c.methode()` statiquement — un dict de méthodes liées sort de
        # sa portée EN SILENCE, et ces cinq-là cesseraient d'être vérifiées
        # contre le tag oto-core épinglé (le trou vécu sur `tools/apollo.py`).
        if op == "metrics":
            return _run(lambda: c.analytics_metrics(form_id, period))
        if op == "visits":
            return _run(lambda: c.analytics_visits(form_id, period))
        if op == "submissions":
            return _run(lambda: c.analytics_submissions(form_id, period))
        if op == "dimensions":
            return _run(lambda: c.analytics_dimensions(form_id, period))
        if op == "drop_off":
            return _run(lambda: c.analytics_drop_off(form_id, period))
        raise _bad(f"op inconnu : {op!r}")

    # ================================================================
    # Espaces de travail et dossiers
    # ================================================================

    @mcp.tool()
    def tally_workspace(
        op: Literal["list", "get", "create", "update", "delete",
                    "folders", "create_folder", "update_folder", "delete_folder"] = "list",
        workspace_id: Optional[str] = None,
        folder_id: Optional[str] = None,
        name: Optional[str] = None,
        parent_id: Optional[str] = None,
        page: Optional[int] = None,
        dry_run: bool = False,
    ) -> object:
        """Workspaces and folders — where forms live.

        Start here when you need a `workspace_id`: `tally_form(op="list")`
        filters on workspace ids, and this is what resolves a name to one.

        Args:
            op: which action.
                · "list" — workspaces with members, pending invites, folders.
                · "get" — one workspace (`workspace_id`).
                · "create" — a workspace named `name`. Pro plan.
                · "update" — rename `workspace_id` to `name`.
                · "delete" — ⚠️ trashes the workspace AND every form in it
                  (restorable).
                · "folders" — folders of `workspace_id`. Pro plan.
                · "create_folder" — `name`, optional `parent_id` to nest.
                · "update_folder" — rename `folder_id` to `name`.
                · "delete_folder" — ⚠️ deletes the folder and its ENTIRE
                  subtree, moving contained forms to the trash.
            dry_run: validate and preview without writing. Real diffs where
                Tally lets the current object be read.
        """
        c = _client()

        def _folders(ws: str):
            got = _run(lambda: c.list_folders(ws))
            return got if isinstance(got, list) else (got or {}).get("folders") or []

        if op == "list":
            _refuse_ignored(op, "il liste les espaces de travail",
                            workspace_id=workspace_id, folder_id=folder_id,
                            name=name, parent_id=parent_id)
            return _run(lambda: c.list_workspaces(page=page))

        if op == "get":
            _need(op, workspace_id=workspace_id)
            _refuse_ignored(op, "il lit UN espace", folder_id=folder_id, name=name,
                            parent_id=parent_id, page=page)
            return _run(lambda: c.get_workspace(workspace_id))

        if op == "folders":
            _need(op, workspace_id=workspace_id)
            _refuse_ignored(op, "il liste les dossiers", folder_id=folder_id, name=name,
                            parent_id=parent_id, page=page)
            return _run(lambda: c.list_folders(workspace_id))

        if op == "create":
            _need(op, name=name)
            _refuse_ignored(op, "il crée un espace", workspace_id=workspace_id,
                            folder_id=folder_id, parent_id=parent_id, page=page)
            if dry_run:
                return {"dry_run": True, "would_create": "workspace", "name": name}
            return _run(lambda: c.create_workspace(name), "workspace_write")

        if op == "update":
            _need(op, workspace_id=workspace_id, name=name)
            _refuse_ignored(op, "il renomme UN espace", folder_id=folder_id,
                            parent_id=parent_id, page=page)
            if dry_run:
                current = _run(lambda: c.get_workspace(workspace_id))
                return _diff(current if isinstance(current, dict) else None,
                             {"name": name}, {})
            return _run(lambda: c.update_workspace(workspace_id, name))

        if op == "delete":
            _need(op, workspace_id=workspace_id)
            _refuse_ignored(op, "il supprime UN espace", folder_id=folder_id, name=name,
                            parent_id=parent_id, page=page)
            if dry_run:
                current = _run(lambda: c.get_workspace(workspace_id))
                return {"dry_run": True, "would_delete": "workspace",
                        "workspace_id": workspace_id, "current": current,
                        "warning": "emporte TOUS les formulaires de l'espace",
                        "recoverable": "oui — espace et formulaires vont à la corbeille"}
            return _run(lambda: c.delete_workspace(workspace_id))

        if op == "create_folder":
            _need(op, workspace_id=workspace_id, name=name)
            _refuse_ignored(op, "il crée un dossier", folder_id=folder_id, page=page)
            if dry_run:
                return {"dry_run": True, "would_create": "folder", "name": name,
                        "workspace_id": workspace_id, "parent_id": parent_id}
            return _run(lambda: c.create_folder(
                workspace_id, name, **({"parentId": parent_id} if parent_id else {})))

        if op == "update_folder":
            _need(op, workspace_id=workspace_id, folder_id=folder_id, name=name)
            _refuse_ignored(op, "il renomme UN dossier", parent_id=parent_id, page=page)
            if dry_run:
                cur = next((f for f in _folders(workspace_id)
                            if isinstance(f, dict) and f.get("id") == folder_id), None)
                return _diff(cur, {"name": name}, {})
            return _run(lambda: c.update_folder(workspace_id, folder_id, name))

        if op == "delete_folder":
            _need(op, workspace_id=workspace_id, folder_id=folder_id)
            _refuse_ignored(op, "il supprime UN dossier", name=name, parent_id=parent_id,
                            page=page)
            if dry_run:
                folders = _folders(workspace_id)
                cur = next((f for f in folders
                            if isinstance(f, dict) and f.get("id") == folder_id), None)
                children = [f.get("id") for f in folders
                            if isinstance(f, dict) and f.get("parentId") == folder_id]
                return {"dry_run": True, "would_delete": "folder", "folder_id": folder_id,
                        "current_available": cur is not None, "current": cur,
                        "direct_children": children,
                        "warning": "supprime le dossier ET tout son sous-arbre ; "
                                   "les formulaires contenus vont à la corbeille"}
            return _run(lambda: c.delete_folder(workspace_id, folder_id))

        raise _bad(f"op inconnu : {op!r}")

    # ================================================================
    # Compte, membres, invitations
    # ================================================================

    @mcp.tool()
    def tally_account(
        op: Literal["me", "users", "remove_user",
                    "invites", "invite", "cancel_invite"] = "me",
        organization_id: Optional[str] = None,
        user_id: Optional[str] = None,
        invite_id: Optional[str] = None,
        emails: Optional[str] = None,
        workspace_ids: Optional[List[str]] = None,
        timezone: Optional[str] = None,
        dry_run: bool = False,
    ) -> object:
        """The current user, the organization's members, and its invitations.

        `op="me"` is where `organization_id` comes from — every other op here
        needs it, and nothing else hands it to you except a form's own
        `organizationId`.

        Args:
            op: which action.
                · "me" — the authenticated user. Optional `timezone` (IANA).
                · "users" — everyone in `organization_id`.
                · "remove_user" — ⚠️ removes `user_id` from the organization.
                  Only the org creator can remove someone else; anyone may
                  remove themselves. This also KILLS every API key that user
                  created — possibly the one making this call. `dry_run`
                  shows who would go.
                · "invites" — pending invitations.
                · "invite" — invite `emails` into `workspace_ids`. ⚠️ `emails`
                  is a STRING, not a list — that asymmetry is Tally's, not a
                  typo here.
                · "cancel_invite" — cancel `invite_id`. Only its creator can.
            dry_run: preview instead of writing.
        """
        c = _client()

        def _members(org: str):
            got = _run(lambda: c.list_organization_users(org))
            return got if isinstance(got, list) else (got or {}).get("users") or []

        if op == "me":
            _refuse_ignored(op, "il lit l'utilisateur courant",
                            organization_id=organization_id, user_id=user_id,
                            invite_id=invite_id, emails=emails, workspace_ids=workspace_ids)
            return _run(lambda: c.get_me(**({"timezone": timezone} if timezone else {})))

        if op == "users":
            _need(op, organization_id=organization_id)
            _refuse_ignored(op, "il liste les membres", user_id=user_id, invite_id=invite_id,
                            emails=emails, workspace_ids=workspace_ids, timezone=timezone)
            return _run(lambda: c.list_organization_users(organization_id))

        if op == "invites":
            _need(op, organization_id=organization_id)
            _refuse_ignored(op, "il liste les invitations", user_id=user_id,
                            invite_id=invite_id, emails=emails,
                            workspace_ids=workspace_ids, timezone=timezone)
            return _run(lambda: c.list_invites(organization_id))

        if op == "remove_user":
            _need(op, organization_id=organization_id, user_id=user_id)
            _refuse_ignored(op, "il retire UN membre", invite_id=invite_id, emails=emails,
                            workspace_ids=workspace_ids, timezone=timezone)
            if dry_run:
                cur = next((u for u in _members(organization_id)
                            if isinstance(u, dict) and u.get("id") == user_id), None)
                return {"dry_run": True, "would_remove": "organization member",
                        "user_id": user_id, "current_available": cur is not None,
                        "current": cur,
                        "warning": "révoque aussi TOUTES les clés API créées par cet "
                                   "utilisateur, y compris peut-être celle de cet appel"}
            return _run(lambda: c.remove_organization_user(organization_id, user_id))

        if op == "invite":
            _need(op, organization_id=organization_id, emails=emails,
                  workspace_ids=workspace_ids)
            _refuse_ignored(op, "il crée des invitations", user_id=user_id,
                            invite_id=invite_id, timezone=timezone)
            if dry_run:
                return {"dry_run": True, "would_invite": emails,
                        "workspace_ids": workspace_ids}
            return _run(lambda: c.create_invites(organization_id, workspace_ids, emails))

        if op == "cancel_invite":
            _need(op, organization_id=organization_id, invite_id=invite_id)
            _refuse_ignored(op, "il annule UNE invitation", user_id=user_id, emails=emails,
                            workspace_ids=workspace_ids, timezone=timezone)
            if dry_run:
                invites = _run(lambda: c.list_invites(organization_id))
                items = invites if isinstance(invites, list) else (invites or {}).get("invites") or []
                cur = next((i for i in items
                            if isinstance(i, dict) and i.get("id") == invite_id), None)
                return {"dry_run": True, "would_cancel": "invite", "invite_id": invite_id,
                        "current_available": cur is not None, "current": cur}
            return _run(lambda: c.cancel_invite(organization_id, invite_id))

        raise _bad(f"op inconnu : {op!r}")

    # ================================================================
    # Webhooks
    # ================================================================

    @mcp.tool()
    def tally_webhook(
        op: Literal["list", "create", "update", "delete", "events", "retry"] = "list",
        webhook_id: Optional[str] = None,
        event_id: Optional[str] = None,
        form_id: Optional[str] = None,
        url: Optional[str] = None,
        event_types: Optional[List[str]] = None,
        signing_secret: Optional[str] = None,
        http_headers: Optional[List[dict]] = None,
        external_subscriber: Optional[str] = None,
        is_enabled: Optional[bool] = None,
        page: Optional[int] = None,
        limit: Optional[int] = None,
        dry_run: bool = False,
    ) -> object:
        """Webhooks — push a form's responses somewhere as they arrive.

        Worth preferring over polling: Tally states webhook deliveries do NOT
        consume the 100 req/min quota. ⚠️ oto is not itself a webhook
        receiver — this registers a URL YOU control (an n8n or Make endpoint,
        your own service). To pull responses into oto, poll
        `tally_submission(op="list", after_id=...)` on a schedule instead.

        Args:
            op: which action.
                · "list" — every webhook across accessible forms. `page`,
                  `limit` (1-100, default 25). Also the ONLY way to read one
                  back: Tally has no `GET /webhooks/{id}`.
                · "create" — needs `form_id`, `url`, `event_types`.
                · "update" — change a webhook. ⚠️ Tally's PATCH is a full
                  REPLACE (`formId`, `url`, `eventTypes`, `isEnabled` all
                  required), so this tool reads the current webhook and merges
                  your changes into it. Pass only what you want changed.
                · "delete" — stop deliveries. If it is the form's last
                  webhook, Tally also marks the integration deleted.
                · "events" — the delivery log for `webhook_id`: status,
                  response code, retry state. `page`.
                · "retry" — re-deliver `event_id`. This fires a REAL request
                  at the endpoint; if the receiver is not idempotent, a retry
                  is a second delivery, not a correction.
            event_types: currently `["FORM_RESPONSE"]` is the only value the
                API defines.
            signing_secret: ⚠️ YOU supply it (Tally does not mint one). Without
                it, deliveries are unsigned and the receiver cannot tell a
                genuine payload from a forged one. It is never echoed back by
                this tool.
            http_headers: `[{"name": ..., "value": ...}]` — extra headers sent
                with each delivery, e.g. an auth header for the receiver.
            external_subscriber: your own identifier for whoever owns this
                subscription.
            dry_run: preview instead of writing; on "update" a real diff
                against the current webhook.
        """
        c = _client()

        def _find(wid: str) -> Optional[Dict[str, Any]]:
            """Il n'existe pas de GET /webhooks/{id} : on pagine la liste."""
            seen_page = 1
            while seen_page <= 20:  # borne dure : 20 × 100 = 2000 webhooks
                got = _run(lambda p=seen_page: c.list_webhooks(page=p, limit=100),
                           "webhook_list")
                items = got if isinstance(got, list) else (got or {}).get("webhooks") or []
                for w in items:
                    if isinstance(w, dict) and w.get("id") == wid:
                        return w
                if not items or (isinstance(got, dict) and not got.get("hasMore")):
                    return None
                seen_page += 1
            return None

        if op == "list":
            _refuse_ignored(op, "il liste les webhooks", webhook_id=webhook_id,
                            event_id=event_id, url=url, event_types=event_types,
                            signing_secret=signing_secret, http_headers=http_headers,
                            external_subscriber=external_subscriber, is_enabled=is_enabled)
            return _run(lambda: c.list_webhooks(page=page, limit=limit), "webhook_list")

        if op == "events":
            _need(op, webhook_id=webhook_id)
            _refuse_ignored(op, "il lit le journal de livraison", event_id=event_id,
                            url=url, event_types=event_types, signing_secret=signing_secret,
                            http_headers=http_headers, is_enabled=is_enabled, limit=limit)
            return _run(lambda: c.list_webhook_events(webhook_id, page=page),
                        "webhook_list")

        if op == "create":
            _need(op, form_id=form_id, url=url, event_types=event_types)
            _refuse_ignored(op, "il crée un webhook", webhook_id=webhook_id,
                            event_id=event_id, is_enabled=is_enabled, page=page, limit=limit)
            extra = {"signingSecret": signing_secret, "httpHeaders": http_headers,
                     "externalSubscriber": external_subscriber}
            if dry_run:
                return {"dry_run": True, "would_create": "webhook", "form_id": form_id,
                        "url": url, "event_types": event_types,
                        "signed": signing_secret is not None,
                        "header_names": [h.get("name") for h in (http_headers or [])]}
            return _run(lambda: c.create_webhook(
                form_id, url, event_types,
                **{k: v for k, v in extra.items() if v is not None}))

        if op == "update":
            _need(op, webhook_id=webhook_id)
            _refuse_ignored(op, "il modifie UN webhook", event_id=event_id,
                            external_subscriber=external_subscriber, page=page, limit=limit)
            current = _find(webhook_id)
            if current is None:
                raise _bad(
                    f"webhook {webhook_id!r} introuvable dans la liste. `PATCH /webhooks/"
                    "{id}` est un REMPLACEMENT complet (formId, url, eventTypes, isEnabled "
                    "tous requis) : sans l'état actuel, une modification partielle "
                    "effacerait les champs non fournis. Vérifie l'id avec op='list'.")
            merged = {
                "form_id": form_id if form_id is not None else current.get("formId"),
                "url": url if url is not None else current.get("url"),
                "event_types": (event_types if event_types is not None
                                else current.get("eventTypes")),
                "is_enabled": (is_enabled if is_enabled is not None
                               else current.get("isEnabled")),
            }
            missing = [k for k, v in merged.items() if v is None]
            if missing:
                raise _bad(f"impossible de reconstruire le webhook : {', '.join(missing)} "
                           "absent(s) de l'état actuel — passe-les explicitement.")
            if dry_run:
                asked = {"formId": form_id, "url": url, "eventTypes": event_types,
                         "isEnabled": is_enabled}
                out = _diff(current, {k: v for k, v in asked.items() if v is not None}, {})
                out["merged_payload_fields"] = sorted(merged)
                out["note"] = ("PATCH est un remplacement complet ; les champs non fournis "
                               "sont repris de l'état actuel, pas laissés vides.")
                return out
            extra = {"signingSecret": signing_secret, "httpHeaders": http_headers}
            return _run(lambda: c.update_webhook(
                webhook_id, merged["form_id"], merged["url"], merged["event_types"],
                merged["is_enabled"],
                **{k: v for k, v in extra.items() if v is not None}))

        if op == "delete":
            _need(op, webhook_id=webhook_id)
            _refuse_ignored(op, "il supprime UN webhook", event_id=event_id, url=url,
                            event_types=event_types, signing_secret=signing_secret,
                            http_headers=http_headers, is_enabled=is_enabled,
                            page=page, limit=limit)
            if dry_run:
                current = _find(webhook_id)
                return {"dry_run": True, "would_delete": "webhook",
                        "webhook_id": webhook_id, "current_available": current is not None,
                        "current": current,
                        "note": "si c'est le dernier webhook du formulaire, Tally marque "
                                "aussi l'intégration webhooks comme supprimée"}
            return _run(lambda: c.delete_webhook(webhook_id))

        if op == "retry":
            _need(op, webhook_id=webhook_id, event_id=event_id)
            _refuse_ignored(op, "il rejoue UN événement", url=url, event_types=event_types,
                            signing_secret=signing_secret, http_headers=http_headers,
                            is_enabled=is_enabled, page=page, limit=limit)
            if dry_run:
                return {"dry_run": True, "would_retry": event_id, "webhook_id": webhook_id,
                        "warning": "un retry est une VRAIE livraison HTTP ; si le récepteur "
                                   "n'est pas idempotent, c'est un second envoi, pas une "
                                   "correction"}
            return _run(lambda: c.retry_webhook_event(webhook_id, event_id))

        raise _bad(f"op inconnu : {op!r}")
