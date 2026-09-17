"""Nextmotion — la projection en LISTE BLANCHE des ressources qui embarquent un patient.

Séparée de `nextmotion.py` (outils) pour tenir sous 500 lignes : c'est la seule partie
qui décide ce qui SORT d'un rendez-vous, d'un devis ou d'une facture, donc la partie à
relire quand l'API change. Le patient n'est servi que par son `id` ; un champ absent
d'une liste ci-dessous ne passe pas, y compris un champ que l'API ajouterait demain.
Le pourquoi (données de santé, textes libres) est dans la docstring de `nextmotion.py`.
"""
from __future__ import annotations

from typing import Any

_WITHHELD = ("patient anonymisé : servi par son seul id, sans nom ni coordonnées, et "
             "rien ne permet d'en retrouver l'identité ; données de santé et textes "
             "libres retirés.")


_PATIENT = ("id",)
_LINE = ("id", "position", "name", "price", "quantity", "rebate", "rebate_percent",
         "vat_rate", "vat_price", "vat_excl_price")
_TOTALS = ("rebate", "rebate_percent", "rebate_vat_rate", "sub_total_vat_excl_price",
           "vat_price", "sub_total_vat_incl_price", "total_net_price",
           "total_vat_price", "total_price")


def _pick(obj: Any, keys: tuple) -> Any:
    if not isinstance(obj, dict):
        return None
    return {k: obj[k] for k in keys if k in obj}


def _pick_list(items: Any, keys: tuple) -> list:
    return [_pick(i, keys) for i in (items or []) if isinstance(i, dict)]


def _nested(out: dict, src: dict, key: str, keys: tuple) -> None:
    if key in src:
        out[key] = _pick(src[key], keys)


def _appointment(a: Any) -> Any:
    if not isinstance(a, dict):
        return a
    out = _pick(a, ("id", "created_time", "modified_time", "status", "statuses"))
    _nested(out, a, "request", ("id", "status"))
    _nested(out, a, "sub_visit_type", ("id", "subject", "duration_minutes", "price"))
    _nested(out, a, "patient", _PATIENT)
    _nested(out, a, "room", ("id", "name"))
    _nested(out, a, "device", ("id", "name"))
    if "visit_type" in a:
        vt = a["visit_type"]
        out["visit_type"] = _pick(vt, ("id", "subject", "duration_minutes", "price"))
        if isinstance(vt, dict) and "category" in vt:
            out["visit_type"]["category"] = _pick(vt["category"], ("id", "name"))
    if "calendar_event" in a:
        ev = a["calendar_event"]
        out["calendar_event"] = _pick(ev, (
            "id", "type", "start_time", "start_time_utc_offset", "end_time",
            "end_time_utc_offset", "duration_minutes", "recurrence"))
        if isinstance(ev, dict):
            out["calendar_event"]["doctors"] = _pick_list(ev.get("doctors"),
                                                          ("id", "prefixed_name"))
            out["calendar_event"]["appointment_rooms"] = _pick_list(
                ev.get("appointment_rooms"), ("id", "name"))
    return out


def _billing(doc: Any, own: tuple, lines_key: str) -> Any:
    if not isinstance(doc, dict):
        return doc
    out = _pick(doc, ("id", "created_time", "modified_time", "issued_time",
                      "number", "number_id", "status") + own + _TOTALS)
    _nested(out, doc, "patient", _PATIENT)
    if lines_key in doc:
        out[lines_key] = _pick_list(doc[lines_key], _LINE)
    return out


def _quote(q: Any) -> Any:
    return _billing(q, ("action_time", "send_time", "last_contact_time", "follow_up_count",
                        "last_follow_up_time", "next_follow_up_time", "response_received",
                        "response_time", "scheduled_appointment_time"), "quoted_treatments")


def _invoice(i: Any) -> Any:
    return _billing(i, ("overridden_created_time", "invoiced_time", "payment_methods",
                        "ref_quote_id"), "invoiced_treatments")


def _page(env: Any, key: str, shape=None) -> dict:
    env = env if isinstance(env, dict) else {}
    rows = env.get("data") or []
    if shape is not None:
        rows = [shape(r) for r in rows]
    out = {"count": env.get("count"), "has_more": env.get("next") is not None, key: rows}
    if shape is not None:
        out["withheld"] = _WITHHELD
    return out


def _one(env: Any, key: str, shape=None) -> dict:
    data = env.get("data") if isinstance(env, dict) else None
    if shape is None:
        return {key: data}
    return {key: shape(data), "withheld": _WITHHELD}
