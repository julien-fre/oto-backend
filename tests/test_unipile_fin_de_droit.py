"""La fin du droit `unipile` : préavis, puis suppression chez unipile (oto-backend#806).

Sur une VRAIE base : ce qui compte ici, c'est ce que le stockage retient d'un passage à
l'autre (la marque, le préavis, le délai compté par l'horloge de la base) — un double
rendrait ce qu'on lui a appris à rendre. Seuls unipile et le courrier sont simulés.

Les propriétés tenues, dans l'ordre de la décision :
droit vivant → rien ; perte → une marque et UN préavis ; retour du droit → marque
effacée ; délai échu → suppression ; clé propre → jamais regardée ; rejeu → ni préavis
ni suppression en double. Et les deux gardes : fermé par défaut, et refus de tourner sur
une table des droits vide.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from oto_mcp import email as E
from oto_mcp.email_templates import _MOIS as F_MOIS
from oto_mcp import unipile_fin_de_droit as F
from oto_mcp.capabilities import unipile_seats as us
from oto_mcp.capabilities._types import AuthzDenied, ResolvedCtx


class FauxUnipile:
    """L'instance unipile de la clé plateforme : ce qu'elle liste, ce qu'on y supprime."""
    dsn = "api.unipile.test"

    def __init__(self):
        self.comptes: set[str] = set()
        self.supprimes: list[str] = []
        self.panne = False

    def list_accounts(self):
        return [{"id": a, "provider": "LINKEDIN"} for a in sorted(self.comptes)]

    def delete_account(self, account_id):
        if self.panne:
            raise RuntimeError("unipile indisponible")
        self.supprimes.append(account_id)
        self.comptes.discard(account_id)


@pytest.fixture()
def banc(live, monkeypatch):
    """Une base remise à neuf, une instance unipile simulée, un facteur qui compte."""
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("DELETE FROM unipile_accounts")
        conn.execute("DELETE FROM org_entitlements")
    instance = FauxUnipile()
    monkeypatch.setattr(us, "_platform_client", lambda: instance)
    lettres: list = []
    monkeypatch.setattr(E, "_send", lambda *a, **kw: lettres.append((a, kw)) or True)
    monkeypatch.setenv(F.ENV_ACTIF, "1")
    monkeypatch.delenv(F.ENV_DELAI, raising=False)
    # Une org témoin qui a le droit, sans compte : la table des droits est « remplie ».
    _droit(_org("témoin"))
    return instance, lettres


def _org(nom: str) -> int:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return conn.execute("INSERT INTO orgs (name) VALUES (%s) RETURNING id",
                            (nom,)).fetchone()["id"]


def _droit(org: int, **kw) -> None:
    from oto_mcp.db import entitlements
    entitlements.grant(org, "unipile", "test", **kw)


def _compte(instance, org: int, account_id: str, *, sub: str = "u-proprio",
            plateforme: bool = True, canal: str = "LINKEDIN") -> None:
    from oto_mcp import db
    db.upsert_user(sub, email=f"{sub}@exemple.invalid")
    db.set_unipile_account(sub, account_id, org_id=org, provider=canal,
                           platform_seat=plateforme)
    instance.comptes.add(account_id)


def _ligne(account_id: str, org: int) -> dict:
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        return dict(conn.execute(
            "SELECT disconnected_at, entitlement_lost_at, entitlement_notice_at "
            "FROM unipile_accounts WHERE account_id = %s AND org_id = %s",
            (account_id, org)).fetchone())


def _vieillir(jours: int) -> None:
    """Recule le premier constat de la perte : le délai se compte à l'horloge de la base."""
    from oto_mcp.db._conn import _connect
    with _connect() as conn:
        conn.execute("UPDATE unipile_accounts SET entitlement_lost_at = "
                     "entitlement_lost_at - make_interval(days => %s) "
                     "WHERE entitlement_lost_at IS NOT NULL", (jours,))


# ── les propriétés de la décision ────────────────────────────────────────────

def test_droit_vivant_rien_ne_bouge(banc):
    instance, lettres = banc
    org = _org("payante")
    _droit(org)
    _compte(instance, org, "acc_paye")
    out = F.balayer()
    assert out["marques"] == 0 and out["preavis_envoyes"] == 0 and out["supprimes"] == []
    assert _ligne("acc_paye", org)["entitlement_lost_at"] is None
    assert lettres == [] and instance.supprimes == []


def test_perte_une_marque_et_UN_seul_preavis(banc):
    instance, lettres = banc
    payante = _org("payante")
    _droit(payante)
    _compte(instance, payante, "acc_paye", sub="u-paye")
    org = _org("sans droit")
    _compte(instance, org, "acc_li", canal="LINKEDIN")
    _compte(instance, org, "acc_wa", canal="WHATSAPP")

    out = F.balayer()
    assert out["marques"] == 2 and out["preavis_envoyes"] == 1
    ligne = _ligne("acc_li", org)
    assert ligne["entitlement_lost_at"] is not None
    assert ligne["entitlement_notice_at"] is not None
    # UN courrier pour les deux canaux du même propriétaire dans la même org.
    assert len(lettres) == 1
    (to, sujet, html), _ = lettres[0]
    assert to == "u-proprio@exemple.invalid"
    attendu = datetime.fromisoformat(ligne["entitlement_lost_at"]) + timedelta(days=7)
    assert sujet.endswith(f"supprimés le {attendu.day} {F_MOIS[attendu.month - 1]} "
                          f"{attendu.year}")
    assert "sans droit" in html and "Linkedin, Whatsapp" in html
    assert "abonnez votre organisation" in html and "propre clé Unipile" in html

    # Rejeu : la marque n'est pas repoussée, aucun second préavis.
    F.balayer()
    assert _ligne("acc_li", org)["entitlement_lost_at"] == ligne["entitlement_lost_at"]
    assert len(lettres) == 1 and instance.supprimes == []


def test_retour_du_droit_efface_la_marque(banc):
    instance, lettres = banc
    org = _org("revenue")
    _compte(instance, org, "acc_retour")
    F.balayer()
    assert _ligne("acc_retour", org)["entitlement_lost_at"] is not None
    _droit(org)
    out = F.balayer()
    assert out["droit_revenu"] == 1
    ligne = _ligne("acc_retour", org)
    assert ligne["entitlement_lost_at"] is None and ligne["entitlement_notice_at"] is None
    # Même vieillie au-delà du délai, une marque effacée ne supprime rien.
    _vieillir(30)
    F.balayer()
    assert instance.supprimes == []


def test_delai_echu_suppression_une_seule_fois(banc, caplog):
    instance, lettres = banc
    org = _org("echue")
    _compte(instance, org, "acc_fin")
    F.balayer()
    _vieillir(6)
    assert F.balayer()["supprimes"] == [], "six jours : pas encore"
    _vieillir(1)
    with caplog.at_level("INFO", logger="oto_mcp.unipile_fin_de_droit"):
        out = F.balayer()
    assert out["supprimes"] == ["acc_fin"] and instance.supprimes == ["acc_fin"]
    assert _ligne("acc_fin", org)["disconnected_at"] is not None, "délié AVANT suppression"
    assert any("acc_fin" in m and "SUPPRIMÉ" in m for m in caplog.messages)
    # Rejeu : le compte n'est plus listé par l'instance, rien n'est supprimé deux fois.
    out = F.balayer()
    assert out["supprimes"] == [] and instance.supprimes == ["acc_fin"]
    assert len(lettres) == 1


def test_delai_reglable(banc, monkeypatch):
    instance, _ = banc
    monkeypatch.setenv(F.ENV_DELAI, "2")
    org = _org("courte")
    _compte(instance, org, "acc_court")
    F.balayer()
    _vieillir(2)
    assert F.balayer()["supprimes"] == ["acc_court"]


def test_delai_illisible_leve(banc, monkeypatch):
    monkeypatch.setenv(F.ENV_DELAI, "0")
    with pytest.raises(ValueError):
        F.balayer()


def test_compte_sur_cle_propre_jamais_regarde(banc):
    instance, lettres = banc
    org = _org("byo")
    _compte(instance, org, "acc_byo", plateforme=False)
    out = F.balayer()
    _vieillir(30)
    F.balayer()
    assert out["en_service"] == 0 and out["marques"] == 0
    assert _ligne("acc_byo", org)["entitlement_lost_at"] is None
    assert lettres == [] and instance.supprimes == []


def test_suppression_ratee_reprise_au_passage_suivant(banc):
    instance, _ = banc
    org = _org("panne")
    _compte(instance, org, "acc_panne")
    F.balayer()
    _vieillir(7)
    instance.panne = True
    out = F.balayer()
    assert out["echecs"] == 1 and out["supprimes"] == []
    # Les bindings restent déliés (on ne rend pas un accès qu'on vient de retirer)…
    assert _ligne("acc_panne", org)["disconnected_at"] is not None
    instance.panne = False
    # …et la ligne morte, toujours marquée, fait reprendre le geste.
    assert F.balayer()["supprimes"] == ["acc_panne"]


def test_compte_adopte_reste_du_tant_qu_une_org_a_le_droit(banc):
    instance, lettres = banc
    sans, avec = _org("sans"), _org("avec")
    _droit(avec)
    _compte(instance, sans, "acc_partage")
    _compte(instance, avec, "acc_partage")
    F.balayer()
    _vieillir(30)
    F.balayer()
    assert _ligne("acc_partage", sans)["entitlement_lost_at"] is not None
    assert lettres == [], "un préavis annoncerait une suppression qui n'aura pas lieu"
    assert instance.supprimes == []


def test_rebrancher_efface_la_marque(banc):
    from oto_mcp import db
    instance, _ = banc
    org = _org("rebranche")
    _compte(instance, org, "acc_re")
    F.balayer()
    db.set_unipile_account("u-proprio", "acc_re2", org_id=org, platform_seat=True)
    ligne = _ligne("acc_re2", org)
    assert ligne["entitlement_lost_at"] is None and ligne["entitlement_notice_at"] is None


def test_sans_adresse_pas_de_suppression(banc):
    from oto_mcp.db._conn import _connect
    instance, lettres = banc
    org = _org("muette")
    _compte(instance, org, "acc_muet", sub="u-muet")
    with _connect() as conn:
        conn.execute("UPDATE users SET email = NULL WHERE sub = 'u-muet'")
    F.balayer()
    _vieillir(30)
    out = F.balayer()
    assert out["sans_adresse"] == 1 and out["supprimes"] == [] and lettres == []


def test_org_d_un_tenant_tiers_ecartee(banc, monkeypatch):
    from oto_mcp import db
    instance, lettres = banc
    org = _org("partenaire")
    _compte(instance, org, "acc_tiers")
    monkeypatch.setattr(db, "org_tenant_slug", lambda oid: "partenaire")
    out = F.balayer()
    assert out["tenant_tiers_ecartes"] == 1 and out["marques"] == 0
    assert _ligne("acc_tiers", org)["entitlement_lost_at"] is None and lettres == []


# ── les gardes ───────────────────────────────────────────────────────────────

def test_ferme_par_defaut_compte_sans_ecrire(banc, monkeypatch):
    instance, lettres = banc
    monkeypatch.delenv(F.ENV_ACTIF)
    org = _org("fermee")
    _compte(instance, org, "acc_ferme")
    out = F.balayer()
    assert out["actif"] is False and out["a_blanc"] is True
    assert out["marques"] == 1, "le travail DIT ce qu'il ferait"
    assert out["note"] and F.ENV_ACTIF in out["note"]
    assert _ligne("acc_ferme", org)["entitlement_lost_at"] is None
    assert lettres == [] and instance.supprimes == []


def test_dry_run_n_ecrit_rien_meme_ouvert(banc):
    instance, lettres = banc
    org = _org("a blanc")
    _compte(instance, org, "acc_blanc")
    out = F.balayer(dry_run=True)
    assert out["a_blanc"] is True and out["marques"] == 1
    assert _ligne("acc_blanc", org)["entitlement_lost_at"] is None and lettres == []


def test_refuse_de_tourner_sur_une_table_des_droits_vide(banc):
    """Des sièges en service et AUCUN droit `unipile` vivant nulle part : c'est la
    table qui n'est pas remplie, pas toutes les orgs qui ont perdu le droit."""
    from oto_mcp.db._conn import _connect
    instance, lettres = banc
    with _connect() as conn:
        conn.execute("DELETE FROM org_entitlements")
    org = _org("seule")
    _compte(instance, org, "acc_seul")
    with pytest.raises(RuntimeError, match="org_entitlements"):
        F.balayer()
    assert _ligne("acc_seul", org)["entitlement_lost_at"] is None and lettres == []


def test_droit_echu_compte_comme_perdu(banc):
    instance, _ = banc
    org = _org("don echu")
    _droit(org, expires_at=datetime.now(timezone.utc) - timedelta(days=1))
    _compte(instance, org, "acc_echu")
    F.balayer()
    assert _ligne("acc_echu", org)["entitlement_lost_at"] is not None


def test_sans_cle_plateforme_ne_tourne_pas(banc, monkeypatch):
    monkeypatch.setattr(us, "_platform_client", lambda: None)
    assert F.balayer()["configured"] is False


# ── la console `oto_admin_unipile_seat` ──────────────────────────────────────

CTX = ResolvedCtx(sub="admin")


def test_inventaire_dit_le_droit_et_la_date_de_suppression(banc):
    instance, _ = banc
    avec, sans = _org("avec"), _org("sans")
    _droit(avec)
    _compte(instance, avec, "acc_avec", sub="u-a")
    _compte(instance, sans, "acc_sans", sub="u-b")
    F.balayer()
    vue = asyncio.run(us._list_seats(CTX, us.SeatsListInput()))
    par = {s["account_id"]: s for s in vue["seats"]}
    assert par["acc_avec"]["entitled"] is True
    assert par["acc_avec"]["entitlement_lost_at"] is None
    assert par["acc_avec"]["deletion_scheduled_at"] is None
    assert par["acc_sans"]["entitled"] is False
    perte = datetime.fromisoformat(par["acc_sans"]["entitlement_lost_at"])
    prevue = datetime.fromisoformat(par["acc_sans"]["deletion_scheduled_at"])
    assert prevue - perte == timedelta(days=7)


def test_release_accepte_un_siege_en_service_SANS_droit(banc):
    instance, _ = banc
    org = _org("sans")
    _compte(instance, org, "acc_libre")
    out = asyncio.run(us._release_seat(CTX, us.SeatReleaseInput(account_id="acc_libre")))
    assert out["was"] == "bound" and out["unbound"] == 1
    assert instance.supprimes == ["acc_libre"]


def test_release_refuse_toujours_un_siege_en_service_AVEC_droit(banc):
    instance, _ = banc
    org = _org("avec")
    _droit(org)
    _compte(instance, org, "acc_garde")
    with pytest.raises(AuthzDenied) as e:
        asyncio.run(us._release_seat(CTX, us.SeatReleaseInput(account_id="acc_garde")))
    assert (e.value.status, e.value.code) == (409, "seat_in_use")
    assert instance.supprimes == []
