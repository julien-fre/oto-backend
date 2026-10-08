"""Silae — generated documents (payroll reports, declaration summaries) and declarations
(monthly DSN, their content, declaration states). READ-ONLY; shared guards and client in
`silae.py`.

Documents: Silae returns them base64-encoded (PDF or XLSX; a DSN content in an
undocumented encoding). They go out through `file_content.render_for_agent`, the single
home of the inline-vs-signed-URL rule: a PDF as its extracted text plus `raw_url` to the
original, an XLSX as CSV per sheet (bounded; `raw_url` when truncated), a binary or a
large text as a short-lived signed URL — never the base64 in the agent's context. A DSN
content is decoded to text when it is text (UTF-8, else Windows-1252, the charset Silae
documents for its values) and says which encoding it read.

⚠️ A field filter cannot see inside a file: a document is refused while the org's
`silae` policy masks anything — it would carry in clear what the JSON responses mask.
The check runs BEFORE the upstream call: what will not be served is not downloaded.
"""
from __future__ import annotations

import logging
import re
from typing import Literal, Optional

from fastmcp import FastMCP

from .. import access
from .silae import _NAME, _bad, _client, check_op, months, need, only, run

logger = logging.getLogger(__name__)

DOCUMENTS_FILTERED = (
    "Silae: document not served. Your org has a field-filter policy for silae, and a "
    "filter cannot mask the inside of a file (PDF, spreadsheet, DSN): the document would "
    "carry in clear what the policy masks in the JSON responses. Silae documents are "
    "served only while the org's silae policy masks nothing.")
DOCUMENTS_POLICY_UNREADABLE = (
    "Silae: document not served. Your org's field-filter policy could not be read, and "
    "a payroll document does not go out without it. Retry in a moment; if it persists, "
    "it is an incident on oto's side.")

_REPORTS = ("charges_table", "contributions_detail", "declaration_summaries")
_GROUPINGS = {"none": "sans_groupement", "monthly": "mensuel", "quarterly": "trimestriel"}
# Control characters other than tab / CR / LF: their presence means "not text".
_CONTROLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _documents_allowed() -> None:
    """Fail-closed lock on documents: an unreadable policy is a named refusal."""
    try:
        ff = access.resolve_field_filter(_NAME)
    except Exception as e:  # noqa: BLE001 — named refusal, fail-closed
        logger.warning("silae: field-filter policy unreadable, document refused",
                       exc_info=True)
        raise _bad(DOCUMENTS_POLICY_UNREADABLE) from e
    if not ff.is_empty():
        raise _bad(DOCUMENTS_FILTERED)


def _render(blob: dict) -> dict:
    """A decoded Silae document → what the agent reads (inline text or signed URL)."""
    from .. import file_content

    sub = access.current_user_sub_or_raise()
    try:
        return file_content.render_for_agent(blob["data"], blob["filename"],
                                             blob["mimetype"], sub=sub, prefix="silae-files")
    except (file_content.MediaUnavailable, file_content.SpreadsheetError) as e:
        raise _bad(f"Silae: {e}") from None


def _dsn_as_text(blob: dict) -> tuple[dict, Optional[str]]:
    """The DSN content as UTF-8 text when it is text, with the encoding read; else the
    blob untouched (served as a file)."""
    for encoding in ("utf-8", "cp1252"):
        try:
            texte = blob["data"].decode(encoding)
        except UnicodeDecodeError:
            continue
        if not _CONTROLE.search(texte):
            nom = blob["filename"].rsplit(".", 1)[0] + ".txt"
            return ({"data": texte.encode("utf-8"), "filename": nom,
                     "mimetype": "text/plain; charset=utf-8"}, encoding)
    return blob, None


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def silae_report(
        numero_dossier: str,
        report: Literal["charges_table", "contributions_detail", "declaration_summaries"],
        op: Literal["generate", "start", "status"] = "generate",
        periode: Optional[str] = None,
        periode_debut: Optional[str] = None,
        periode_fin: Optional[str] = None,
        format: Optional[Literal["pdf", "xlsx"]] = None,
        detail_salaries: Optional[bool] = None,
        grouper_par_organismes: Optional[bool] = None,
        grouper_par: Optional[Literal["none", "monthly", "quarterly"]] = None,
        task_id: Optional[str] = None,
    ) -> dict:
        """A payroll report Silae generates for ONE dossier per call: a PDF comes back as
        its text plus `raw_url` (signed, temporary) to the original, an XLSX as CSV
        (bounded; `raw_url` when truncated).

        `report`: "charges_table" (tableau des charges), "contributions_detail" (détail
        des cotisations, 12 months at most), "declaration_summaries" (the month's
        declaration summaries, one PDF).
        `op`: "generate" (default, waits), "start" (returns `task_id`, for a heavy
        report), "status" (polls it; the document once ETAT_TERMINEE).
        Refused while the org's silae field-filter policy masks anything.

        Args:
            numero_dossier: dossier number.
            periode: pay month `AAAA-MM` — declaration_summaries; on the other reports, a
                one-month range.
            periode_debut: range start `AAAA-MM` — charges_table, contributions_detail.
            periode_fin: range end `AAAA-MM`, included.
            format: charges_table, contributions_detail — pdf (default) or xlsx.
            detail_salaries: contributions_detail — with the per-employee detail.
            grouper_par_organismes: contributions_detail — group by social body.
            grouper_par: contributions_detail — none, monthly or quarterly.
            task_id: status — the `task_id` returned by op="start".
        """
        check_op(op, ("generate", "start", "status"))
        if report not in _REPORTS:
            raise _bad(f"report must be one of {', '.join(_REPORTS)}")
        args = dict(periode=periode, periode_debut=periode_debut, periode_fin=periode_fin,
                    format=format, detail_salaries=detail_salaries,
                    grouper_par_organismes=grouper_par_organismes, grouper_par=grouper_par,
                    task_id=task_id)
        edition = {"charges_table": "tableau_charges",
                   "contributions_detail": "detail_cotisations",
                   "declaration_summaries": "recap_declarations"}[report]
        if op == "status":
            only(op, ("task_id",), **args)
            need(op, task_id=task_id)
            _documents_allowed()
            etat = run(lambda: _client().statut_edition(edition, task_id,
                                                        numero_dossier=numero_dossier))
            return {"status": etat["statut"], "progress": etat["progression"],
                    "error": etat["messageErreur"], "duration": etat["dureeExecution"],
                    "document": _render(etat["document"]) if etat["document"] else None}
        if report == "declaration_summaries":
            only(op, ("periode",), **args)
            need(op, periode=periode)
        elif report == "charges_table":
            only(op, ("periode", "periode_debut", "periode_fin", "format"), **args)
        else:
            only(op, ("periode", "periode_debut", "periode_fin", "format", "detail_salaries",
                      "grouper_par_organismes", "grouper_par"), **args)
        _documents_allowed()
        c = _client()
        if report == "declaration_summaries":
            if op == "start":
                lance = run(lambda: c.lancer_recap_declarations(numero_dossier, periode=periode))
            else:
                return _render(run(lambda: c.recap_declarations(numero_dossier, periode=periode)))
        else:
            debut, fin = months(op, periode, periode_debut, periode_fin)
            plage = dict(periode_debut=debut, periode_fin=fin, format=format or "pdf")
            if report == "charges_table":
                if op == "start":
                    lance = run(lambda: c.lancer_edition_tableau_charges(numero_dossier, **plage))
                else:
                    return _render(run(lambda: c.edition_tableau_charges(numero_dossier, **plage)))
            else:
                options = dict(detail_salaries=detail_salaries,
                               grouper_par_organismes=grouper_par_organismes,
                               grouper_par=_GROUPINGS[grouper_par] if grouper_par else None)
                if op == "start":
                    lance = run(lambda: c.lancer_edition_detail_cotisations(
                        numero_dossier, **plage, **options))
                else:
                    return _render(run(lambda: c.edition_detail_cotisations(
                        numero_dossier, **plage, **options)))
        tache = lance.get("guidTache") if isinstance(lance, dict) else None
        if not tache:
            raise _bad(f"Silae: the task was not started (no guidTache in {lance!r})")
        return {"task_id": tache, "status": "started",
                "next": f"silae_report(numero_dossier={numero_dossier!r}, report={report!r}, "
                        f"op='status', task_id={tache!r})"}

    @mcp.tool()
    def silae_declaration(
        numero_dossier: str,
        op: Literal["dsn_list", "dsn_content", "states"] = "dsn_list",
        periode: Optional[str] = None,
        etablissement: Optional[str] = None,
        type_dsn: Optional[int] = None,
        fraction: Optional[int] = None,
        segments: Optional[list[str]] = None,
        code_organisme: Optional[str] = None,
        numero_affiliation: Optional[str] = None,
        type_declaration: Optional[Literal["DECLARATION", "DADSU", "DUE", "DN-AC-AE",
                                           "DSIJ"]] = None,
    ) -> dict:
        """Declarations of a dossier, as Silae generated them.

        `op`:
        - "dsn_list" (default): the month's DSN — establishment, body code, affiliation,
          type_dsn, SIRET, fraction, send date.
        - "dsn_content": one DSN's content, whole or only `segments` — inline text when it
          is text, else a signed URL. Refused while the org's silae field-filter policy
          masks anything.
        - "states": declarations and their state (ADS number, recipient, returns).
        Silae contracts "Usage interne" (1A/1B) only.

        Args:
            numero_dossier: dossier number.
            periode: pay month `AAAA-MM` — dsn_list, dsn_content; states (required for
                DECLARATION).
            etablissement: dsn_content — internal name, as dsn_list gives it.
            type_dsn: dsn_content — as dsn_list gives it (0-4).
            fraction: dsn_content — as dsn_list gives it.
            segments: dsn_content — blocks to read, e.g. ["S21.G00.30"]; omitted = Silae's
                default.
            code_organisme: dsn_content — when dsn_list gives one.
            numero_affiliation: dsn_content — when dsn_list gives one.
            type_declaration: states — DECLARATION, DADSU, DUE, DN-AC-AE or DSIJ.
        """
        check_op(op, ("dsn_list", "dsn_content", "states"))
        args = dict(periode=periode, etablissement=etablissement, type_dsn=type_dsn,
                    fraction=fraction, segments=segments, code_organisme=code_organisme,
                    numero_affiliation=numero_affiliation, type_declaration=type_declaration)
        if op == "dsn_list":
            only(op, ("periode",), **args)
            need(op, periode=periode)
            return {"dsn": run(lambda: _client().list_dsn_mensuelles(numero_dossier,
                                                                     periode=periode))}
        if op == "states":
            only(op, ("periode", "type_declaration"), **args)
            need(op, type_declaration=type_declaration)
            return {"declarations": run(lambda: _client().etat_declarations(
                numero_dossier, type_declaration=type_declaration, periode=periode))}
        only(op, tuple(n for n in args if n != "type_declaration"), **args)
        need(op, periode=periode, etablissement=etablissement, type_dsn=type_dsn,
             fraction=fraction)
        _documents_allowed()
        blob = run(lambda: _client().contenu_partiel_dsn(
            numero_dossier, periode=periode, etablissement=etablissement, type_dsn=type_dsn,
            fraction=fraction, segments=segments, code_organisme=code_organisme,
            numero_affiliation=numero_affiliation))
        texte, encoding = _dsn_as_text(blob)
        out = _render(texte)
        out["source_encoding"] = encoding
        return out
