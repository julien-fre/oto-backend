"""Pose de formulaire d'une connexion en DEUX temps — la séquence que les tests des
modules OAuth ne voient pas (ils partent d'un credential déjà posé).

Ces connecteurs (zoho, salesforce) posent d'abord l'app OAuth, puis consentent — et
c'est le consentement qui produit le `refresh_token`. L'état intermédiaire est normal.
Deux pièges, tous deux SILENCIEUX :

1. **Impasse** — `api_key_save` sonde avant de persister (#106) ; un credential
   incomplet PAR CONSTRUCTION échoue la sonde → pose refusée → jamais de consentement
   → jamais de credential complet. Fermé sur `main` (289f370) en consultant l'état
   déclaré (`status_hints.credential_state`, source unique) ; ce fichier vérifie que
   **salesforce y est bien rattaché**, sans quoi il retomberait dans le blocage.

2. **Perte du champ obtenu hors formulaire** — le formulaire repacke ses seuls champs
   déclarés : corriger sa Login URL après connexion écrasait le blob SANS le
   refresh_token. L'UI dit « enregistré », ça casse au premier appel d'outil.

On exerce les deux décisions pures d'`api_key_save` (sonder ? que reprendre ?) sans
monter la stack HTTP — le chemin SQL est couvert au déploiement.
"""
import pytest

from oto_mcp import providers, status_hints
from oto_mcp.tools import salesforce as sf_tools  # noqa: F401 — enregistre le hook


def _decide(connector: str, submitted: dict, stored: dict | None):
    """Réplique les deux décisions d'`api_key_save` : ce qu'on reprend du credential
    déjà stocké, et si la sonde doit tourner."""
    c = providers.REGISTRY[connector]
    fields = dict(submitted)
    if status_hints.credential_state(connector, fields) is not None and stored:
        declared = {f.name for f in c.secret_fields}
        fields = {**{k: v for k, v in stored.items() if k not in declared and v}, **fields}
    st = status_hints.credential_state(connector, fields)
    probe = not (st is not None and not st.complete)
    return fields, probe


PREREQ = {"client_id": "3MVG...", "client_secret": "s3cr3t",
          "login_url": "https://login.salesforce.com"}


def test_salesforce_declares_prerequisites_only():
    """Le contrat de départ : le champ obtenu par consentement n'est PAS au formulaire."""
    c = providers.REGISTRY["salesforce"]
    assert {f.name for f in c.secret_fields} == {"client_id", "client_secret", "login_url"}
    # Reste un connecteur à FORMULAIRE : « il manque une étape » se dit par
    # status_hints, pas par une méthode d'auth à part (cf. le test du dessous).
    assert c.auth_method == "secret"


def test_salesforce_is_wired_to_the_single_source():
    """TRIPWIRE — sans état déclaré, `api_key_save` sonderait un credential incomplet
    par construction et le blocage circulaire reviendrait pour salesforce."""
    st = status_hints.credential_state("salesforce", PREREQ)
    assert st is not None and st.complete is False
    assert "refresh_token" in st.missing
    assert st.next_action, "l'état incomplet doit porter le GESTE, pas juste un booléen"


def test_first_save_persists_without_probing():
    """1re pose : incomplétude ATTENDUE → pas de sonde, le credential est enregistré.
    Sans ça, 400 verify_failed et le bouton Connecter reste injoignable."""
    fields, probe = _decide("salesforce", PREREQ, stored=None)
    assert probe is False
    assert fields == PREREQ


def test_edit_after_connect_keeps_the_consented_field():
    """Édition d'un credential déjà connecté : le refresh_token est REPRIS, pas perdu."""
    stored = {**PREREQ, "refresh_token": "5Aep861..."}
    edited = {**PREREQ, "login_url": "https://test.salesforce.com"}
    fields, probe = _decide("salesforce", edited, stored)
    assert fields["refresh_token"] == "5Aep861..."
    assert fields["login_url"] == "https://test.salesforce.com"   # l'édition passe
    assert probe is True   # credential complet → la sonde reprend son rôle de garde


def test_submitted_fields_win_over_stored_ones():
    """La reprise ne doit jamais ressusciter une valeur que l'user vient de changer."""
    stored = {**PREREQ, "client_secret": "ancien", "refresh_token": "tok"}
    fields, _ = _decide("salesforce", {**PREREQ, "client_secret": "nouveau"}, stored)
    assert fields["client_secret"] == "nouveau"


@pytest.mark.parametrize("name", sorted(providers.REGISTRY))
def test_probe_still_gates_connectors_without_declared_state(name):
    """TRIPWIRE — l'assouplissement ne vaut QUE pour un connecteur qui DÉCLARE son
    état. Tout autre garde le verify-avant-persist de #106, intact."""
    if status_hints.credential_state(name, {"key": "x"}) is not None:
        return
    _, probe = _decide(name, {"key": "x"}, stored=None)
    assert probe is True, f"{name} : la sonde ne doit pas être contournée"
