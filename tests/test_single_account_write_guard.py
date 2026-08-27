"""oto-backend#409 — « accepté-et-ignoré » est exclu à la pose d'un compte nommé.

Le coffre stocke N lignes par (entité, connecteur, **compte**) pour TOUS les
connecteurs, mais seule la résolution d'un connecteur MULTI-compte va les lire.
Poser un compte nommé sur un mono-compte écrivait donc une ligne parfaitement
valide que rien n'irait jamais chercher — écrite, inerte, sans refus ni
avertissement. Deux volets, tous deux ici :

1. **la garde de pose** — un connecteur qui ne résout pas les comptes nommés
   REFUSE la déclaration d'un compte nommé, en se nommant ;
2. **la règle de cardinalité** — un credential MULTI-CHAMPS est multi-compte au
   même titre qu'une clé simple (cas Slack : un token par installation dans un
   workspace, N installations = N tokens), sauf exclusion explicite et motivée
   **par connecteur**. Jamais « toute forme à champs ⟹ multi ».

Les cas nominaux tranchent contre le REGISTRE RÉEL (`crunchbase` = cookie, donc
mono par construction ; `zoho` = multi) : c'est le montage servi qui décide, pas
une fixture qui reproduirait la règle qu'on veut prouver.
"""
import pytest

from oto_mcp import credentials_store, providers


def _vault_forbidden(monkeypatch):
    """Le coffre ne doit PAS être touché par un refus : la garde tranche sur le
    registre, avant toute lecture des comptes déjà posés."""
    def _boom(*a, **k):
        raise AssertionError("la garde a lu le coffre avant de refuser")
    monkeypatch.setattr(credentials_store, "list_accounts", _boom)


# --- 1. La garde de pose -----------------------------------------------------

def test_named_account_on_single_account_connector_is_refused(monkeypatch):
    assert providers.REGISTRY["crunchbase"].auth_multi_account is False
    _vault_forbidden(monkeypatch)
    with pytest.raises(credentials_store.SingleAccountConnector) as e:
        credentials_store.guard_account_write(
            credentials_store.MEMBER, "1:u1", "crunchbase", "second-compte")
    # Refus NOMMÉ : le connecteur et le compte refusé sont dans le message —
    # sans eux, l'appelant ne sait pas quoi corriger.
    assert "crunchbase" in str(e.value) and "second-compte" in str(e.value)


def test_unnamed_account_on_single_account_connector_passes(monkeypatch):
    """La pose ordinaire (sans compte nommé) reste intouchée — c'est le cas de
    tous les credentials posés jusqu'ici."""
    _vault_forbidden(monkeypatch)
    credentials_store.guard_account_write(
        credentials_store.MEMBER, "1:u1", "crunchbase", "")


def test_multi_account_connector_delegates_to_coexistence(monkeypatch):
    """Multi-compte : la garde ne change rien au contrat existant — elle passe la
    main à la cohérence des noms ('' et comptes nommés ne cohabitent pas)."""
    assert providers.REGISTRY["zoho"].auth_multi_account is True
    seen = []
    monkeypatch.setattr(credentials_store, "ensure_named_coexistence",
                        lambda *a: seen.append(a))
    credentials_store.guard_account_write("org", "35", "zoho", "zoho-us")
    assert seen == [("org", "35", "zoho", "zoho-us")]


def test_unknown_connector_named_account_is_refused(monkeypatch):
    """Un connecteur hors registre ne résout aucun compte nommé : même refus que
    le mono-compte, jamais un laissez-passer."""
    _vault_forbidden(monkeypatch)
    with pytest.raises(credentials_store.SingleAccountConnector):
        credentials_store.guard_account_write("org", "35", "pas-au-registre", "compte-x")


# --- 2. La règle de cardinalité ---------------------------------------------

@pytest.mark.parametrize("name", ["slack", "silae", "stripe", "salesforce",
                                  "zohodesk", "posthog", "http"])
def test_multi_field_credential_is_multi_account(name):
    """Un credential multi-champs porte N comptes comme une clé simple : la forme
    du credential n'est pas une raison de fournisseur."""
    con = providers.REGISTRY[name]
    assert con.secret_kind == "fields" and con.auth_multi_account is True


@pytest.mark.parametrize("name", ["unipile", "atlassian", "crunchbase", "culture"])
def test_excluded_families_stay_single_account(name):
    """Les deux familles exclues pour une raison qui n'est PAS la forme du
    credential — porteur d'identité cross-org (barreau de cascade mono par
    construction) et flux à consentement OAuth/cookie (N comptes = N
    consentements) — plus l'open-data, qui n'a pas de credential du tout."""
    assert providers.REGISTRY[name].auth_multi_account is False


def test_single_account_flag_excludes_a_connector():
    """L'exclusion se déclare DANS l'entrée de registre du connecteur, jamais par
    appartenance à une liste transverse."""
    con = providers._c("faux", ["faux"], auth_modes={"byo_user"},
                       secret_kind="fields", single_account=True,
                       credential_fields=(providers.CredentialField("a", "A"),))
    assert con.auth_multi_account is False


def test_no_connector_claims_single_account_today():
    """Tripwire : le mécanisme d'exclusion existe SANS porteur — aucun connecteur
    servi n'a aujourd'hui de raison de fournisseur d'être mono-compte. En ajouter
    un est une décision explicite, qui casse ce test et se motive en revue."""
    assert [c.name for c in providers._REGISTRY_LIST if c.single_account] == []
