"""Connecteur Nextmotion — les deux agrégats de `nextmotion_analyse`.

Ce que ce fichier verrouille :
- **la patientèle ne laisse sortir aucune ligne** : une réponse amont empoisonnée (nom,
  adresse, coordonnées, id, commentaire) ne laisse passer aucune valeur sentinelle ; une
  case sous le seuil ne sort ni par sa clé ni par son effectif ;
- les dimensions : département (Corse, outre-mer, étranger), tranche d'âge, genre — une
  valeur de genre inconnue LÈVE, une date de naissance illisible aussi, sans la citer ;
- le parcours complet : un plafond atteint lève au lieu de rendre un agrégat partiel ;
- l'occupation des appareils : jour par jour, non tenus sans minutes, appareils à zéro
  rendus, découpage par période, période bornée.

Toutes les valeurs sont factices.
"""
import asyncio
import json
from datetime import date
from unittest.mock import MagicMock

import pytest

from oto_mcp.mcp_errors import McpError
from oto_mcp.tools import nextmotion_analyse as analyse

C = "00000000-0000-4000-8000-00000000000c"
S = "SENTINELLE-PATIENT"
AUJOURD_HUI = date(2026, 10, 1)


def _tool(name):
    from fastmcp import FastMCP

    m = FastMCP("t")
    analyse.register(m)
    return asyncio.run(m.get_tool(name)).fn


@pytest.fixture
def client(monkeypatch):
    inst = MagicMock()
    monkeypatch.setattr("oto.tools.nextmotion.NextmotionClient", lambda **kw: inst)
    monkeypatch.setattr("oto_mcp.access.resolve_api_key",
                        lambda provider, account=None: ("nm-test", False))
    return inst


def _patient(i, **champs):
    return {"id": f"{S}-id-{i}", "first_name": S, "last_name": S, "email": S,
            "phone_number": S, "postal_address": S, "doctor_comments": S,
            "patient_number": S, "latitude": 48.8, "longitude": 2.3, **champs}


def _pages(lignes, taille=analyse.PAGE):
    """`lire_page(offset)` sur une liste, avec `next` tant qu'il reste des lignes."""
    def lire(offset):
        page = lignes[offset:offset + taille]
        reste = offset + taille < len(lignes)
        return {"count": len(lignes), "next": "suite" if reste else None, "data": page}
    return lire


# --- patientèle ----------------------------------------------------------------------

def test_aucune_valeur_individuelle_ne_sort_et_les_petites_cases_sont_masquees(client):
    lignes = ([_patient(i, zip_code="75008", gender="0") for i in range(12)]
              + [_patient(100 + i, zip_code="69001", gender="1") for i in range(3)])
    client.list_patients.side_effect = lambda cid, **kw: _pages(lignes)(kw["offset"])
    out = _tool("nextmotion_patient_demographics")(clinic_id=C, by=["zip_code", "gender"])
    assert S not in json.dumps(out)
    assert out["patients"] == 15
    assert out["cellules"] == [{"zip_code": "75008", "gender": "femme", "patients": 12}]
    assert out["masquees"] == {"cellules": 1, "patients": 3}
    assert "69001" not in json.dumps(out)
    client.list_patients.assert_called_with(C, is_archived=False, limit=100, offset=0)


def test_les_archives_ne_sont_comptees_que_sur_demande(client):
    client.list_patients.return_value = {"next": None, "data": []}
    _tool("nextmotion_patient_demographics")(clinic_id=C, include_archived=True)
    client.list_patients.assert_called_with(C, is_archived=None, limit=100, offset=0)


@pytest.mark.parametrize("patient,attendu", [
    ({"zip_code": "75008"}, "75"),
    ({"zip_code": "20000", "country": "France"}, "2A"),
    ({"zip_code": "20250"}, "2B"),
    ({"zip_code": "97400", "country": "fr"}, "974"),
    ({"zip_code": "10115", "country": "DE"}, "étranger"),
    ({"zip_code": "750"}, "inconnu"),
    ({"zip_code": "6400"}, "06"),
    ({"zip_code": "6400", "country": "BE"}, "étranger"),
    ({}, "inconnu"),
])
def test_departement(patient, attendu):
    assert analyse._departement(patient) == attendu


def test_un_code_a_4_chiffres_rejoint_sa_forme_a_5_avant_le_masquage(client):
    """Relevé le 07/10/2026 : « 6400 » (zéro initial perdu) formait une cellule à part
    et tombait en département inconnu, à côté de « 06400 »."""
    lignes = ([_patient(i, zip_code="06400") for i in range(6)]
              + [_patient(100 + i, zip_code="6400") for i in range(5)])
    client.list_patients.side_effect = lambda cid, **kw: _pages(lignes)(kw["offset"])
    out = _tool("nextmotion_patient_demographics")(clinic_id=C, by=["zip_code"])
    assert out["cellules"] == [{"zip_code": "06400", "patients": 11}]
    dep = _tool("nextmotion_patient_demographics")(clinic_id=C, by=["department"])
    assert dep["cellules"] == [{"department": "06", "patients": 11}]


@pytest.mark.parametrize("naissance,attendu", [
    ("2010-01-01", "0-17"), ("2008-10-01", "18-24"), ("2008-10-02", "0-17"),
    ("1990-05-05", "35-44"), ("1950-01-01", "65+"), (None, "inconnu"), ("", "inconnu"),
    ("1990-05-05T00:00:00Z", "35-44"),
])
def test_tranche_age(naissance, attendu):
    assert analyse._tranche({"birth_date": naissance}, AUJOURD_HUI) == attendu


@pytest.mark.parametrize("brut,attendu", [(0, "femme"), ("1", "homme"), ("2", "autre"),
                                          (None, "inconnu"), ("", "inconnu")])
def test_genre(brut, attendu):
    assert analyse._genre({"gender": brut}) == attendu


def test_un_genre_inattendu_leve_au_lieu_d_etre_devine():
    with pytest.raises(ValueError, match="gender"):
        analyse._genre({"gender": "F"})


def test_une_date_de_naissance_illisible_leve_sans_etre_citee():
    with pytest.raises(ValueError) as e:
        analyse._tranche({"birth_date": "05/05/1990"}, AUJOURD_HUI)
    assert "1990" not in str(e.value)


def test_le_plafond_de_pages_leve_au_lieu_de_rendre_un_partiel(monkeypatch):
    monkeypatch.setattr(analyse, "MAX_PAGES_PATIENTS", 2)
    lignes = [_patient(i, zip_code="75008") for i in range(5)]
    with pytest.raises(ValueError, match="cap"):
        analyse.patientele(_pages(lignes, taille=2), ["zip_code"], AUJOURD_HUI)


@pytest.mark.parametrize("by", [[], ["name"], ["zip_code", "zip_code"], "zip_code"])
def test_dimensions_refusees_avant_tout_appel(client, by):
    with pytest.raises(McpError):
        _tool("nextmotion_patient_demographics")(clinic_id=C, by=by)
    client.list_patients.assert_not_called()


# --- appareils -----------------------------------------------------------------------

LASER = {"id": "dev-laser", "name": "Laser", "color": "#f00"}
CRYO = {"id": "dev-cryo", "name": "Cryo", "color": "#00f"}
IDLE = {"id": "dev-idle", "name": "Inutilisé"}


def _rdv(device, minutes, status="confirmed", **evt):
    return {"id": "rdv", "patient": {"id": S, "first_name": S}, "device": device,
            "status": status, "statuses": [status],
            "calendar_event": {"duration_minutes": minutes, **evt}}


def test_occupation_par_appareil_et_par_mois(client):
    agenda = {
        "2026-09-30": [_rdv(LASER, 30), _rdv(LASER, 45, status="canceled_last_minute"),
                       _rdv(None, 20)],
        "2026-10-01": [_rdv(CRYO, 60, appointment_devices=[LASER])],
    }
    client.list_appointment_devices.return_value = {"next": None,
                                                    "data": [LASER, CRYO, IDLE]}
    client.list_appointments.side_effect = lambda cid, date, limit, offset: {
        "next": None, "data": agenda.get(date, [])}
    out = _tool("nextmotion_device_usage")(clinic_id=C, start_date="2026-09-30",
                                           end_date="2026-10-01", period_type="month")
    assert S not in json.dumps(out)
    assert out["jours_lus"] == 2 and out["rendez_vous_lus"] == 4
    assert out["sans_appareil"] == 1
    par_id = {a["device_id"]: a for a in out["appareils"]}
    assert par_id["dev-laser"]["rendez_vous"] == 2
    assert par_id["dev-laser"]["minutes"] == 90
    assert par_id["dev-laser"]["non_tenus"] == 1
    assert par_id["dev-laser"]["par_periode"] == [
        {"periode": "2026-09", "rendez_vous": 1, "minutes": 30, "non_tenus": 1},
        {"periode": "2026-10", "rendez_vous": 1, "minutes": 60, "non_tenus": 0}]
    assert par_id["dev-idle"] == {"device_id": "dev-idle", "name": "Inutilisé",
                                  "rendez_vous": 0, "minutes": 0, "non_tenus": 0,
                                  "par_periode": []}
    assert [c.kwargs["date"] for c in client.list_appointments.call_args_list] == [
        "2026-09-30", "2026-10-01"]


def test_duree_deduite_des_bornes_sinon_leve():
    rdv = _rdv(LASER, None, start_time="2026-10-01T10:00:00+02:00",
               end_time="2026-10-01T10:40:00+02:00")
    assert analyse._minutes(rdv) == 40
    with pytest.raises(ValueError, match="duration"):
        analyse._minutes(_rdv(LASER, None))


@pytest.mark.parametrize("debut,fin", [("2026-01-01", "2026-04-30"),
                                       ("2026-02-01", "2026-01-01"),
                                       ("01/01/2026", "2026-01-02")])
def test_periode_refusee_avant_tout_appel(client, debut, fin):
    with pytest.raises(McpError):
        _tool("nextmotion_device_usage")(clinic_id=C, start_date=debut, end_date=fin)
    client.list_appointments.assert_not_called()
