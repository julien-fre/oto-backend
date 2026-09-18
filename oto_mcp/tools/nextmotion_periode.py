"""Nextmotion — le filtre de période des factures, appliqué CÔTÉ OUTIL.

`GET /v4/clinics/{clinic_id}/invoices` n'accepte que `limit` et `offset` : aucun filtre
de date, et l'ordre de la liste n'est pas documenté. L'outil lit donc l'amont par pages
de 100 et garde les factures dont `invoiced_time` tombe dans la période.

⚠️ **Il ne s'arrête jamais tôt** : s'arrêter à la première facture hors période
supposerait un tri que la spec ne promet pas, et rendrait un résultat partiel présenté
comme complet. Il lit jusqu'à la dernière page, borné par un plafond de pages ; quand le
plafond coupe, la réponse le DIT (`complet: false`) et donne l'`offset` de reprise.

La date d'une facture est celle qu'écrit `invoiced_time`, dans son propre fuseau
(`2026-01-31T23:30:00+01:00` est du 31 janvier). Une facture sans `invoiced_time`
lisible lève : la spec le déclare obligatoire, et l'écarter en silence mentirait sur
la période.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Callable, Optional

from .nextmotion_socle import _invoice, _page

PAGE = 100
MAX_PAGES_DEFAUT = 20
MAX_PAGES_MAX = 100
_JOUR = re.compile(r"\d{4}-\d{2}-\d{2}")


def _borne(valeur: Optional[str], nom: str) -> Optional[date]:
    if valeur is None:
        return None
    try:
        if not isinstance(valeur, str) or not _JOUR.fullmatch(valeur):
            raise ValueError
        return date.fromisoformat(valeur)
    except ValueError:
        raise ValueError(f"`{nom}` doit être une date YYYY-MM-DD — reçu {valeur!r}.") from None


def _jour_facture(facture: Any) -> date:
    brut = facture.get("invoiced_time") if isinstance(facture, dict) else None
    try:
        return datetime.fromisoformat(brut).date()
    except (TypeError, ValueError):
        ident = facture.get("id") if isinstance(facture, dict) else None
        raise ValueError(
            f"Nextmotion : la facture {ident!r} n'a pas d'`invoiced_time` lisible "
            f"({brut!r}) — impossible de dire si elle est dans la période.") from None


def lister(lire_page: Callable[[int], Any], invoiced_from: Optional[str],
           invoiced_to: Optional[str], *, offset: int, max_pages: Optional[int],
           fields: Optional[list]) -> dict:
    """Les factures de la période, lues depuis `offset` sur au plus `max_pages` pages.

    `lire_page(offset)` rend une page amont de `PAGE` factures. Tout est validé AVANT
    le premier appel."""
    debut = _borne(invoiced_from, "invoiced_from")
    fin = _borne(invoiced_to, "invoiced_to")
    if debut and fin and debut > fin:
        raise ValueError("`invoiced_from` est postérieur à `invoiced_to`.")
    plafond = MAX_PAGES_DEFAUT if max_pages is None else max_pages
    if not 1 <= plafond <= MAX_PAGES_MAX:
        raise ValueError(f"max_pages doit être entre 1 et {MAX_PAGES_MAX} — reçu {plafond}.")
    if offset < 0:
        raise ValueError(f"offset doit être >= 0 — reçu {offset}.")

    gardees: list = []
    pages = parcourues = 0
    total = None
    complet = False
    while pages < plafond:
        env = lire_page(offset + parcourues)
        env = env if isinstance(env, dict) else {}
        lignes = env.get("data") or []
        total = env.get("count")
        pages += 1
        parcourues += len(lignes)
        gardees += [f for f in lignes
                    if (debut is None or _jour_facture(f) >= debut)
                    and (fin is None or _jour_facture(f) <= fin)]
        if env.get("next") is None:
            complet = True
            break
        if not lignes:
            raise ValueError("Nextmotion : page vide alors que `next` annonce une suite — "
                             f"parcours interrompu à l'offset {offset + parcourues}.")

    out = _page({"count": None, "next": None, "data": gardees}, "invoices", _invoice,
                fields=fields)
    out.pop("count")
    out.pop("has_more")
    return {
        **out,
        "periode": {"invoiced_from": invoiced_from, "invoiced_to": invoiced_to},
        "trouvees": len(gardees),
        "pages_lues": pages,
        "factures_parcourues": parcourues,
        "factures_total_amont": total,
        "complet": complet,
        "offset_suivant": None if complet else offset + parcourues,
    }
