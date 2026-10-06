"""Nextmotion — two clinic AGGREGATES: its clientele (zip code, city, country,
gender, age band) and the occupancy of its devices. No patient row ever comes out.

Sibling module of `nextmotion.py` (cf. `Connector.modules`).

## The clientele: the patient list, read to COUNT

Decision of 2026-10-01 (request from the marketing team of a client clinic): the patient
list is read by THIS module to derive counts (identity, for its part, is read
through `nextmotion_patient`, decision of the same day). What holds the guard here:

- **nothing individual comes out**: no row, no id, no name; the tool reads each page,
  increments counters and discards the page. Only the dimension fields are read
  (`zip_code`, `city`, `country`, `gender`, `birth_date`); the address, name,
  contact details and comments are never touched;
- **a cell of fewer than `SEUIL` patients is masked** (statistical secrecy): it comes out
  neither by its key nor by its count, only the masked total is returned. Crossing
  several dimensions quickly drops cells below the threshold; this is intended;
- **age comes out as a band**, never as a date of birth or an exact age.

Accepted residual risk: two calls on different dimensions can, by
difference, approach a small cell. The threshold makes it costly, not impossible.

The socio-demographic profile of a territory (population, income, households) is NOT
recomputed here: that is open data, `urba_socio` / `urba_iris` by INSEE code.

## The devices: the usage BOOKED in the calendar

The API has no per-device statistic. The tool reads the calendar day by day (the only
date filter of `calendar_appointments`) and counts, per device, the appointments
held, their minutes and those not held (cancelled at the last minute, absent, suspended,
deleted). This is the **booked** usage, not the machine's real usage (shots, effective
duration), which Nextmotion does not know.

Derived from the public OpenAPI spec (read on 2026-10-01); **never exercised with a real
key**: the real shape of `gender` (typed `string` on the patient, integer 0/1/2
elsewhere) is not verified — an unknown value RAISES rather than being guessed.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any, Callable, Literal, Optional

from fastmcp import FastMCP

from .nextmotion_garde import _bad, _client, _need, _run

SEUIL = 10
PAGE = 100
MAX_PAGES_PATIENTS = 500  # 50 000 patients
MAX_PAGES_APPAREILS = 10
MAX_PAGES_JOUR = 20
MAX_JOURS = 93

_DIMENSIONS = ("zip_code", "department", "city", "country", "gender", "age_band")
_GENRES = {"0": "femme", "1": "homme", "2": "autre"}
_TRANCHES = ((18, "0-17"), (25, "18-24"), (35, "25-34"), (45, "35-44"), (55, "45-54"),
             (65, "55-64"))
_FRANCE = {"", "FR", "FRA", "FRANCE"}
_INCONNU = "inconnu"
_NON_TENUS = {"canceled_last_minute", "absent", "suspended", "deleted"}
_JOUR = re.compile(r"\d{4}-\d{2}-\d{2}")


# --- clientele ----------------------------------------------------------------------

def _texte(valeur: Any) -> str:
    texte = str(valeur).strip().upper() if valeur is not None else ""
    return texte or _INCONNU


def _departement(patient: dict) -> str:
    """The department of a French zip code (Corsica 2A/2B, overseas on 3
    digits); « étranger » if the country is not France. An empty country counts as
    France: the clinic is there, and Nextmotion does not always fill in the field."""
    pays = str(patient.get("country") or "").strip().upper()
    if pays not in _FRANCE:
        return "étranger"
    cp = str(patient.get("zip_code") or "").strip()
    if not re.fullmatch(r"\d{5}", cp):
        return _INCONNU
    if cp.startswith("20"):
        return "2A" if cp < "20200" else "2B"
    return cp[:3] if cp.startswith(("97", "98")) else cp[:2]


def _genre(patient: dict) -> str:
    brut = patient.get("gender")
    if brut is None or str(brut).strip() == "":
        return _INCONNU
    genre = _GENRES.get(str(brut).strip())
    if genre is None:
        raise ValueError(f"Nextmotion: unexpected patient gender ({str(brut)[:20]!r}) — "
                         "the spec announces 0, 1 or 2; aggregate aborted rather than guessed.")
    return genre


def _tranche(patient: dict, aujourd_hui: date) -> str:
    brut = patient.get("birth_date")
    if brut is None or str(brut).strip() == "":
        return _INCONNU
    try:
        naissance = date.fromisoformat(str(brut).strip()[:10])
    except ValueError:
        # The value is not quoted: it is a date of birth.
        raise ValueError("Nextmotion: a patient's date of birth is not in "
                         "YYYY-MM-DD format — aggregate aborted.") from None
    age = aujourd_hui.year - naissance.year - (
        (aujourd_hui.month, aujourd_hui.day) < (naissance.month, naissance.day))
    if age < 0:
        return _INCONNU
    return next((nom for borne, nom in _TRANCHES if age < borne), "65+")


def _cle(patient: dict, dimensions: list, aujourd_hui: date) -> tuple:
    lire = {
        "zip_code": lambda p: _texte(p.get("zip_code")),
        "department": _departement,
        "city": lambda p: _texte(p.get("city")),
        "country": lambda p: _texte(p.get("country")),
        "gender": _genre,
        "age_band": lambda p: _tranche(p, aujourd_hui),
    }
    return tuple(lire[d](patient) for d in dimensions)


def _parcourir(lire_page: Callable[[int], Any], plafond: int, quoi: str):
    """Every row of every upstream page, up to the last; RAISES if the page cap
    cuts the read (a partial aggregate presented as complete would lie)."""
    offset = 0
    for _ in range(plafond):
        env = lire_page(offset)
        env = env if isinstance(env, dict) else {}
        lignes = env.get("data") or []
        yield from (ligne for ligne in lignes if isinstance(ligne, dict))
        if env.get("next") is None:
            return
        if not lignes:
            raise ValueError(f"Nextmotion: empty page although `next` announces more "
                             f"({quoi}, offset {offset}).")
        offset += len(lignes)
    raise ValueError(f"Nextmotion: more than {plafond * PAGE} {quoi} — read cap "
                     "reached, aggregate not returned.")


def patientele(lire_page: Callable[[int], Any], dimensions: list,
               aujourd_hui: date) -> dict:
    """Head counts per combination of `dimensions`, cells under `SEUIL` masked."""
    effectifs: Counter = Counter()
    for patient in _parcourir(lire_page, MAX_PAGES_PATIENTS, "patients"):
        effectifs[_cle(patient, dimensions, aujourd_hui)] += 1
    visibles = sorted(((k, n) for k, n in effectifs.items() if n >= SEUIL),
                      key=lambda kn: (-kn[1], kn[0]))
    masquees = [n for n in effectifs.values() if n < SEUIL]
    return {
        "dimensions": dimensions,
        "seuil": SEUIL,
        "patients": sum(effectifs.values()),
        "cellules": [{**dict(zip(dimensions, k)), "patients": n} for k, n in visibles],
        "masquees": {"cellules": len(masquees), "patients": sum(masquees)},
    }


# --- devices -----------------------------------------------------------------------

def _jour(valeur: str, nom: str) -> date:
    if not isinstance(valeur, str) or not _JOUR.fullmatch(valeur):
        raise ValueError(f"`{nom}` must be a YYYY-MM-DD date — got {valeur!r}.")
    return date.fromisoformat(valeur)


def _periode(jour: date, period_type: Optional[str]) -> Optional[str]:
    if period_type is None:
        return None
    if period_type == "day":
        return jour.isoformat()
    if period_type == "week":
        annee, semaine, _ = jour.isocalendar()
        return f"{annee}-W{semaine:02d}"
    return jour.strftime("%Y-%m")


def _minutes(rdv: dict) -> int:
    evt = rdv.get("calendar_event") if isinstance(rdv.get("calendar_event"), dict) else {}
    if isinstance(evt.get("duration_minutes"), int):
        return evt["duration_minutes"]
    try:
        debut = datetime.fromisoformat(evt["start_time"])
        fin = datetime.fromisoformat(evt["end_time"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"Nextmotion: appointment {rdv.get('id')!r} has neither a duration nor "
                         "readable bounds.") from None
    return int((fin - debut).total_seconds() // 60)


def _appareils_du_rdv(rdv: dict) -> dict:
    """{id: name} of an appointment's devices: `device`, plus those of its event."""
    evt = rdv.get("calendar_event") if isinstance(rdv.get("calendar_event"), dict) else {}
    tous = [rdv.get("device")] + list(evt.get("appointment_devices") or [])
    return {a["id"]: a.get("name") for a in tous if isinstance(a, dict) and a.get("id")}


def occupation(lire_jour: Callable[[date, int], Any], lire_appareils: Callable[[int], Any],
               debut: date, fin: date, period_type: Optional[str]) -> dict:
    """Per device (including those with no appointment): appointments held, booked
    minutes, not held — in total and, with `period_type`, per period."""
    noms = {a["id"]: a.get("name") for a in _parcourir(lire_appareils, MAX_PAGES_APPAREILS,
                                                       "appareils") if a.get("id")}
    compteurs: dict = {}
    lus = sans_appareil = 0
    jour = debut
    while jour <= fin:
        for rdv in _parcourir(lambda o, j=jour: lire_jour(j, o), MAX_PAGES_JOUR,
                              f"appointments on {jour.isoformat()}"):
            lus += 1
            statuts = set(rdv.get("statuses") or []) | {rdv.get("status")}
            tenu = not statuts & _NON_TENUS
            appareils = _appareils_du_rdv(rdv)
            if not appareils:
                sans_appareil += 1
            minutes = _minutes(rdv) if tenu and appareils else 0
            for ident, nom in appareils.items():
                noms.setdefault(ident, nom)
                for cle in {None, _periode(jour, period_type)}:
                    c = compteurs.setdefault((ident, cle), Counter())
                    c["rendez_vous" if tenu else "non_tenus"] += 1
                    c["minutes"] += minutes
        jour += timedelta(days=1)

    def _chiffres(ident: str, cle: Optional[str]) -> dict:
        c = compteurs.get((ident, cle), Counter())
        return {"rendez_vous": c["rendez_vous"], "minutes": c["minutes"],
                "non_tenus": c["non_tenus"]}

    appareils_out = []
    for ident in sorted(noms, key=lambda i: (-_chiffres(i, None)["minutes"], str(noms[i]))):
        ligne = {"device_id": ident, "name": noms[ident], **_chiffres(ident, None)}
        if period_type is not None:
            ligne["par_periode"] = [{"periode": cle, **_chiffres(ident, cle)}
                                    for cle in sorted(k for (i, k) in compteurs
                                                      if i == ident and k is not None)]
        appareils_out.append(ligne)
    return {"jours_lus": (fin - debut).days + 1, "rendez_vous_lus": lus,
            "sans_appareil": sans_appareil, "appareils": appareils_out}


def register(mcp: FastMCP) -> None:

    @mcp.tool()
    def nextmotion_patient_demographics(
        clinic_id: str,
        by: Optional[list] = None,
        include_archived: Optional[bool] = None,
    ) -> dict:
        """Head counts of a Nextmotion clinic's clientele, by zip code, department,
        city, country, gender and/or age band — AGGREGATES ONLY: no patient row, id or
        name ever comes out, and any cell under 10 patients is masked (only the masked
        total is given). Crossing many dimensions masks more cells: start coarse.

        To profile a territory (population, income, households), do NOT ask Nextmotion:
        use the open-data tools — `urba_socio(code_insee)` per commune, `urba_iris` per
        neighbourhood (a zip code may span several INSEE communes; `foncier_geocode`
        gives the `citycode`).

        Reads the whole patient list (100 per upstream call), so a large clinic takes
        a while. Gender: femme | homme | autre | inconnu. Age bands: 0-17, 18-24,
        25-34, 35-44, 45-54, 55-64, 65+, inconnu (from the birth date, today).
        Department: from a French zip code (2A/2B, 3 digits overseas), « étranger »
        when the country is not France.

        Args:
            clinic_id: the clinic.
            by: dimensions to cross, among zip_code | department | city | country |
                gender | age_band (default ["zip_code"]).
            include_archived: also count archived patients (default False).
        """
        dimensions = ["zip_code"] if by is None else by
        if not isinstance(dimensions, list) or not dimensions:
            raise _bad("`by` must be a non-empty list of dimensions.")
        inconnues = [d for d in dimensions if d not in _DIMENSIONS]
        if inconnues or len(set(dimensions)) != len(dimensions):
            raise _bad(f"`by`: dimensions among {', '.join(_DIMENSIONS)}, no duplicates "
                       f"— got {dimensions!r}.")
        _need("demographics", clinic_id=clinic_id)
        archives = bool(include_archived)
        c = _client()
        out = _run(lambda: patientele(
            lambda o: c.list_patients(clinic_id, is_archived=None if archives else False,
                                      limit=PAGE, offset=o),
            dimensions, date.today()))
        return {"clinic_id": clinic_id, "include_archived": archives, **out}

    @mcp.tool()
    def nextmotion_device_usage(
        clinic_id: str,
        start_date: str,
        end_date: str,
        period_type: Optional[Literal["day", "week", "month"]] = None,
    ) -> dict:
        """Booked use of a Nextmotion clinic's devices (machines) over a period, per
        device — every device of the clinic, unused ones at zero: appointments held,
        booked minutes, and appointments not held (cancelled last minute, absent,
        suspended, deleted; their minutes are not counted).

        ⚠️ This is the use BOOKED in the calendar, not the machine's real use (shots,
        effective time): Nextmotion does not know it. No occupancy rate is given —
        the API has no device capacity; divide `minutes` by your own capacity.

        Nextmotion filters appointments by ONE day only, so the tool reads the
        calendar day by day: at most 93 days per call (call again for a longer span).
        `sans_appareil` counts appointments with no device.

        Args:
            clinic_id: the clinic.
            start_date / end_date: YYYY-MM-DD, both inclusive, at most 93 days.
            period_type: day | week | month — adds a `par_periode` breakdown per
                device (omitted = totals only).
        """
        _need("device_usage", clinic_id=clinic_id)
        try:
            debut, fin = _jour(start_date, "start_date"), _jour(end_date, "end_date")
        except ValueError as e:
            raise _bad(str(e)) from None
        if debut > fin:
            raise _bad("`start_date` is after `end_date`.")
        if (fin - debut).days + 1 > MAX_JOURS:
            raise _bad(f"Period of more than {MAX_JOURS} days: split it into several "
                       "calls.")
        c = _client()
        out = _run(lambda: occupation(
            lambda jour, o: c.list_appointments(clinic_id, date=jour.isoformat(),
                                                limit=PAGE, offset=o),
            lambda o: c.list_appointment_devices(clinic_id, limit=PAGE, offset=o),
            debut, fin, period_type))
        return {"clinic_id": clinic_id,
                "periode": {"start_date": start_date, "end_date": end_date,
                            "period_type": period_type}, **out}
