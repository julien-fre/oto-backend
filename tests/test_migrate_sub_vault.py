"""Ce que `migrate_sub` a le droit de faire au coffre, et la porte cross-tenant.

Deux invariants nés du passage d'un partenaire en tenant déclaré (oto-private#83) :

1. **Une clé personnelle ne se repointe pas, elle s'abandonne.** L'AAD dérive de
   l'entité (`credentials_store._aad(entity_type, entity_id, connector, account)`),
   donc repointer `entity_id` sans rechiffrer fabrique une ligne que plus rien ne
   peut ouvrir : la fiche affiche « clé posée », chaque appel échoue en `InvalidTag`,
   et le diagnostic accuse le connecteur. Une clé ABSENTE se repose en dix secondes.

2. **Le merge par email reste borné à un tenant** (ADR 0052 §6). Seul un acte
   d'opérateur nommé ouvre le passage cross-tenant — jamais un login.
"""
import inspect
import re

from oto_mcp.db import users


def test_le_coffre_personnel_nest_pas_repointe():
    """TRIPWIRE — si quelqu'un « répare » la migration en repointant `entity_id`,
    la prod gagne des credentials présents-et-morts. C'est la régression que ce
    test existe pour rendre bruyante."""
    src = inspect.getsource(users.migrate_sub)
    fautifs = [ligne.strip() for ligne in src.splitlines()
               if "connector_credentials" in ligne
               and "entity_id" in ligne
               and ligne.lstrip().startswith("conn.execute")]
    assert not fautifs, (
        "migrate_sub repointe `connector_credentials.entity_id` :\n  "
        + "\n  ".join(fautifs)
        + "\nL'AAD dérive de l'entité — la ligne migrée serait indéchiffrable. "
        "Abandonner la clé (l'utilisateur la repose), ou rechiffrer explicitement.")


def test_lauteur_du_coffre_est_bien_repointe():
    """Le pendant du précédent : `set_by` n'entre PAS dans l'AAD, c'est de
    l'attribution. Le retirer aussi ferait perdre « qui a posé cette clé » sans
    aucun gain de sûreté — l'inverse du but."""
    src = inspect.getsource(users.migrate_sub)
    assert re.search(r"UPDATE connector_credentials SET set_by=", src), (
        "migrate_sub ne repointe plus l'auteur des credentials : l'attribution "
        "d'une clé d'org survivrait à son poseur en pointant un sub supprimé.")


def test_le_cross_tenant_exige_un_acte_doperateur_nomme():
    """La garde `same_tenant` ne doit jamais disparaître : elle doit s'OUVRIR sur
    une source explicite. Un login (`reconcile_tenant_migration`) ne la renseigne
    pas, donc reste fermé — c'est ce qui empêche qu'une inscription sous l'email
    d'autrui, chez un tenant tiers, absorbe son compte."""
    sig = inspect.signature(users.migrate_sub)
    assert "operator_source" in sig.parameters, (
        "la porte cross-tenant délibérée a disparu de migrate_sub")
    assert sig.parameters["operator_source"].kind is inspect.Parameter.KEYWORD_ONLY, (
        "`operator_source` doit être keyword-only : un troisième argument positionnel "
        "s'attraperait par mégarde à l'appel.")
    assert sig.parameters["operator_source"].default == "", (
        "le défaut doit être vide — fermé par défaut, ouvert seulement si on le nomme")

    src = inspect.getsource(users.migrate_sub)
    assert "same_tenant(old_sub, new_sub) and not operator_source" in src, (
        "la garde de tenant ne s'ouvre plus sur la source d'opérateur, ou ne garde "
        "plus du tout : les deux sont des régressions distinctes.")


def test_le_login_nouvre_jamais_la_porte():
    """`reconcile_tenant_migration` est le chemin CHAUD (à chaque login du nouveau
    tenant). S'il passait `operator_source`, la fédération d'identités que §6
    interdit redeviendrait automatique — sans qu'aucun test de forme ne bronche."""
    src = inspect.getsource(users.reconcile_tenant_migration)
    assert "operator_source" not in src, (
        "reconcile_tenant_migration nomme `operator_source` : le merge automatique "
        "par email franchirait les tenants (ADR 0052 §6).")
