"""Le connecteur `planity`, natif — ce qui change et ce qui ne doit PAS changer.

Jusqu'au 2026-09-09 les vingt outils `planity_*` étaient servis par un MCP distant
que nous opérions (`kind="mount"`) ; le backend lui rejouait le `basic_auth` du
coffre par requête. Ils sont désormais montés en propre (`kind="tools"`), sur le
cœur d'oto-core. **Le credential ne bouge pas d'un iota, les noms d'outils non
plus** : ce sont les deux choses qu'un agent déjà écrit et une fiche déjà lue
tiennent pour acquises, et les deux qu'un changement de plomberie casserait sans
que rien ne le dise.

Le cœur est moqué à sa frontière ; le harnais vit dans `_planity_faux.py`, qui dit
pourquoi. Ce fichier-ci tient trois choses : la déclaration au registre, la SURFACE
servie (les noms d'outils, en dur), et le comportement d'une instance non
configurée. La vie de la session est dans `test_planity_session_pool.py`, les
projections et les bornes dans `test_planity_lectures.py`.

⚠️ **Aucun test ne parle à Planity.** Rien ici n'ouvre de connexion.
"""
from __future__ import annotations

import asyncio
import sys
import types

import pytest
from _planity_faux import (_COORDONNEES, _client_moque, _employe, _faux_coeur,
                           _iso, _outil, _poser, _salon, _serveur, coeur,
                           exige_les_sous_modules)

from oto_mcp import providers

__all__ = ["coeur"]        # la fixture, importée pour être posée


# ── La déclaration au registre ──────────────────────────────────────────────

def test_planity_est_un_connecteur_natif_et_plus_un_mcp_federe():
    c = providers.REGISTRY["planity"]
    assert c.kind == "tools"
    # `family` est DÉRIVÉE du kind : elle disait « federated », elle dit « api ».
    assert c.family == "api"


def test_le_credential_de_planity_ne_change_pas():
    """Ce qui est au coffre a été posé sous ce format et doit continuer de se
    relire : le passage en natif est une affaire de plomberie, pas de credential."""
    c = providers.REGISTRY["planity"]
    assert c.auth_modes == frozenset({"byo_user"})
    assert c.secret_kind == "basic_auth"
    assert [f.name for f in c.secret_fields] == ["email", "password"]
    assert c.auth["method"] == "secret"


def test_la_fiche_ne_renvoie_plus_vers_un_service_a_nous():
    """L'adresse du serveur que nous opérions n'a plus rien à faire dans la fiche :
    elle enverrait sur un service que le connecteur n'appelle plus."""
    c = providers.REGISTRY["planity"]
    texte = " ".join([c.help, c.href or "", c.description])
    assert "planity-mcp" not in texte and "oto.zone" not in texte
    assert "passerelle" not in texte.lower()
    assert c.publisher_name == "Otomata"


def test_les_modules_declares_sont_ceux_qui_montent_les_outils():
    """Le registre DÉRIVE le chargement de cette liste : un module d'outils ajouté
    sans y figurer ne monte rien, et rien ne le dit."""
    assert providers.REGISTRY["planity"].modules == (
        "planity", "planity_stats", "planity_pos", "planity_stock")


# ── La surface servie ───────────────────────────────────────────────────────

_ATTENDUS = sorted([
    "planity_list_salons", "planity_get_salon_info", "planity_list_employees",
    "planity_list_services", "planity_list_products",
    "planity_search_customers", "planity_get_customer",
    "planity_get_customer_stats", "planity_get_customer_receipts",
    "planity_list_appointments", "planity_get_appointment",
    "planity_list_recurring_appointments",
    "planity_get_revenue_summary", "planity_get_daily_revenue",
    "planity_get_best_customers", "planity_get_new_customers",
    "planity_get_customer_frequencies", "planity_get_revenue_breakdown",
    "planity_get_seller_stats", "planity_get_occupancy_rate",
    "planity_get_reviews_stats",
    "planity_get_revenue_by_payment_method", "planity_get_revenue_by_vat",
    "planity_get_service_stats",
    "planity_list_pos_periods", "planity_get_pos_period",
    "planity_get_receipt", "planity_list_payment_methods",
    "planity_list_stock_movements", "planity_list_suppliers",
    "planity_list_product_orders", "planity_list_mass_stock_removals",
])

#: Les vingt noms servis avant le 2026-09-09. Ils sont ici SÉPARÉMENT des trente-deux
#: parce qu'ils portent une promesse différente : les ajouts peuvent bouger tant
#: qu'ils ne sont pas sortis, ceux-là sont dans des agents déjà écrits et dans une
#: fiche déjà lue. Un renommage se verrait ici et nulle part ailleurs.
_HISTORIQUES = sorted([
    "planity_list_salons", "planity_get_salon_info", "planity_list_employees",
    "planity_list_services", "planity_list_products",
    "planity_search_customers", "planity_get_customer",
    "planity_get_customer_stats", "planity_get_customer_receipts",
    "planity_list_appointments", "planity_get_appointment",
    "planity_get_revenue_summary", "planity_get_daily_revenue",
    "planity_get_best_customers", "planity_get_new_customers",
    "planity_get_customer_frequencies", "planity_get_revenue_breakdown",
    "planity_get_seller_stats", "planity_get_occupancy_rate",
    "planity_get_reviews_stats",
])


def test_les_outils_gardent_exactement_leurs_noms(coeur):
    """La liste EN DUR, pas dérivée du module : c'est le contrat qu'un agent déjà
    écrit tient pour acquis. Un renommage, un oubli ou un ajout se voit ici."""
    servis = sorted(t.name for t in asyncio.run(_serveur(coeur).list_tools()))
    assert servis == _ATTENDUS
    assert len(servis) == 32


def test_les_vingt_noms_historiques_sont_tous_encore_servis(coeur):
    """Les ajouts de 2026-09-09 sont ADDITIFS : aucun des noms d'avant n'a bougé."""
    servis = {t.name for t in asyncio.run(_serveur(coeur).list_tools())}
    assert set(_HISTORIQUES) <= servis
    assert not set(_HISTORIQUES) - servis


def test_tous_les_outils_sont_dans_le_namespace_du_connecteur(coeur):
    """Le gate d'activation lit le PREMIER token du nom : un outil qui sortirait du
    préfixe échapperait à l'activation du connecteur, en silence."""
    ns = providers.REGISTRY["planity"].namespaces[0]
    for t in asyncio.run(_serveur(coeur).list_tools()):
        assert t.name.split("_")[0] == ns


# ── L'instance non configurée : présent, et refusant en le disant ───────────

def test_les_outils_sont_montes_meme_sans_coordonnees(monkeypatch):
    """⚠️ Le connecteur ne DISPARAÎT pas quand l'instance n'est pas configurée.

    Un connecteur absent du catalogue ne se remarque pas et ne s'explique pas :
    l'utilisatrice ne voit rien, ne peut rien demander, et personne ne sait
    pourquoi. Présent et refusant en nommant la cause, elle lit le motif et
    l'exploitant sait quoi poser. C'est l'inverse du réflexe « ne monte pas ce qui
    ne marche pas », et c'est délibéré."""
    mod = _faux_coeur(_client_moque())
    monkeypatch.setitem(sys.modules, "oto.tools.planity", mod)
    _poser(monkeypatch, {})

    servis = sorted(t.name for t in asyncio.run(_serveur(None).list_tools()))
    assert servis == _ATTENDUS, "les 20 outils restent montés, configurés ou non"


def test_sans_coordonnees_l_appel_refuse_en_nommant_les_trois_variables(monkeypatch):
    """Et le refus ne parle PAS du credential : sans les coordonnées, l'appel
    partirait sur un 400 de Firebase, que la chaîne d'erreur traduirait — à raison
    dans son contexte, à tort ici — en « email ou mot de passe refusé ».
    L'utilisatrice reposerait alors un credential parfaitement bon, en boucle."""
    from oto_mcp.mcp_errors import McpError

    mod = _faux_coeur(_client_moque())
    monkeypatch.setitem(sys.modules, "oto.tools.planity", mod)
    monkeypatch.setattr("oto_mcp.access.resolve_credential_fields",
                        lambda provider, account=None: {"email": "demo@example.com",
                                                        "password": "s3cret"})
    from oto_mcp.tools import planity_session
    planity_session._entrees.clear()
    _poser(monkeypatch, {})

    outil = asyncio.run(_serveur(None).get_tool("planity_list_salons")).fn
    with pytest.raises(McpError) as e:
        asyncio.run(outil())
    msg = str(e.value)
    for nom in _COORDONNEES:
        assert nom in msg, f"{nom} doit être nommée dans le refus"
    assert "password" not in msg and "credential" in msg
    assert "oto_admin_connector_setting" in msg, (
        "un diagnostic qui ne dit pas le GESTE renvoie chercher — c'est ainsi "
        "qu'on relance six fois une configuration valide")


def test_une_seule_cle_manquante_est_nommee_seule(monkeypatch):
    """Nommer les trois quand une seule manque envoie tout revérifier.

    Une clé POSÉE mais vide compte comme absente : une ligne en base qu'on croit
    configurée et que personne ne peut lire est le pire des deux états."""
    from oto_mcp.mcp_errors import McpError
    from oto_mcp.tools import planity_session

    mod = _faux_coeur(_client_moque())
    monkeypatch.setitem(sys.modules, "oto.tools.planity", mod)
    _poser(monkeypatch, dict(_COORDONNEES, rest_api="   "))
    planity_session._entrees.clear()

    with pytest.raises(McpError) as e:
        planity_session.endpoints()
    msg = str(e.value)
    assert "rest_api" in msg
    assert "firebase_api_key" not in msg


def test_sans_l_extra_du_coeur_l_appel_refuse_en_nommant_l_extra(monkeypatch):
    """Même règle pour l'autre moitié de la configuration : le connecteur reste
    monté, et le refus nomme l'extra d'oto-core au lieu de rendre « No module named
    'httpx' » — vrai, et parfaitement inutile à qui le lit."""
    from oto_mcp.mcp_errors import McpError
    from oto_mcp.tools import planity_session

    # `None` dans `sys.modules` FAIT LEVER l'import, quoi que porte le venv. Le
    # geste d'avant — retirer l'entrée — ne simulait l'extra manquant que tant que
    # l'oto-core installé n'avait pas le paquet : le jour où le pin l'apporte, il
    # importerait pour de bon et ce test passerait au vert sur autre chose.
    monkeypatch.setitem(sys.modules, "oto.tools.planity", None)
    _poser(monkeypatch, _COORDONNEES)
    planity_session._entrees.clear()

    servis = sorted(t.name for t in asyncio.run(_serveur(None).list_tools()))
    assert servis == _ATTENDUS, "tous les outils restent montés sans le cœur"

    outil = asyncio.run(_serveur(None).get_tool("planity_list_salons")).fn
    with pytest.raises(McpError) as e:
        asyncio.run(outil())
    assert "oto-core[planity]" in str(e.value)


def test_le_demarrage_dit_ce_qui_manque(monkeypatch, caplog):
    """Sans cette ligne au boot, un exploitant qui a oublié une variable ne
    l'apprendrait qu'au premier appel d'une utilisatrice."""
    from oto_mcp.tools import planity_session

    _poser(monkeypatch, {})
    with caplog.at_level("WARNING"):
        planity_session.avertir_au_demarrage()
    for nom in _COORDONNEES:
        assert nom in caplog.text
    assert "oto_admin_connector_setting" in caplog.text


# ── Une famille d'outils à la fois, cœur moqué ──────────────────────────────

def test_referentiel_un_salon_est_rendu_avec_ses_compteurs(coeur):
    coeur.client.list_salons.return_value = [
        _salon(employees=[_employe(), _employe("emp-2", "Camille")])]
    out = asyncio.run(_outil(coeur, "planity_list_salons")())
    assert out == [{"id": "biz-un", "name": "Salon Exemple", "slug": "salon-exemple",
                    "phone": "0100000000", "opening_hours": "10:00-19:00",
                    "employee_count": 2, "calendar_count": 1}]


@exige_les_sous_modules
def test_referentiel_les_prestations_sont_aplaties_et_en_euros(coeur):
    """Planity range les prestations sous des catégories et compte en CENTIMES.
    Rendre 4500 au lieu de 45 € ne lève rien : ça se lit comme un tarif.

    ⚠️ Et le prix vit dans `prices`, PAS dans `price` — qui n'existe sur aucune
    prestation. L'avoir lu là a rendu tout un catalogue à `0.00`, ce qui ne lève
    rien non plus : ça se lit comme une prestation offerte."""
    coeur.client.list_services.return_value = {
        "cat-1": {"name": "Coiffure", "children": {
            "svc-1": {"name": " Coupe ", "prices": {"default": 4500},
                      "duration": 30}}}}
    out = asyncio.run(_outil(coeur, "planity_list_services")(salon_id="biz-un"))
    assert len(out) == 1
    assert out[0]["id"] == "svc-1" and out[0]["name"] == "Coupe"
    assert out[0]["duration_minutes"] == 30
    assert out[0]["price"]["kind"] == "fixed"
    assert out[0]["price"]["default_eur"] == 45.0
    assert out[0]["price"]["default_cents"] == 4500


@exige_les_sous_modules
def test_referentiel_une_prestation_sans_prix_ne_vaut_pas_zero(coeur):
    """`0.00` prétend savoir ; `null` dit qu'on ne sait pas. La moitié d'un
    catalogue n'a pas de prix, et la moitié d'un catalogue n'est pas offerte."""
    coeur.client.list_services.return_value = {
        "cat-1": {"children": {"svc-1": {"name": "Sur mesure"},
                               "svc-2": {"name": "Devis",
                                         "prices": {"onQuotation": True}},
                               "svc-3": {"name": "Couleur",
                                         "prices": {"min": 3000, "max": 6000}}}}}
    out = {s["id"]: s["price"] for s in asyncio.run(
        _outil(coeur, "planity_list_services")(salon_id="biz-un"))}
    assert out["svc-1"]["kind"] == "unpriced" and out["svc-1"]["default_eur"] is None
    assert out["svc-2"]["kind"] == "on_quotation"
    assert out["svc-3"]["kind"] == "range"
    assert (out["svc-3"]["min_eur"], out["svc-3"]["max_eur"]) == (30.0, 60.0)


@exige_les_sous_modules
def test_referentiel_les_prestations_supprimees_sont_ecartees_par_defaut(coeur):
    """Planity garde ce qu'on supprime. Un catalogue périmé se présenterait comme
    une offre — et la suppression se porte AUSSI sur la catégorie."""
    coeur.client.list_services.return_value = {
        "cat-1": {"children": {"svc-1": {"name": "Vivante"},
                               "svc-2": {"name": "Retirée", "deletedAt": 7}}},
        "cat-2": {"deletedAt": 9, "children": {"svc-3": {"name": "Orpheline"}}}}
    outil = _outil(coeur, "planity_list_services")
    actives = asyncio.run(outil(salon_id="biz-un"))
    assert [s["id"] for s in actives] == ["svc-1"]
    toutes = asyncio.run(outil(salon_id="biz-un", include_deleted=True))
    assert sorted(s["id"] for s in toutes) == ["svc-1", "svc-2", "svc-3"]


def test_clientes_les_horodatages_passent_par_la_conversion_du_coeur(coeur):
    coeur.client.search_customers.return_value = [
        {"objectID": "cli-1", "name": " Cliente Exemple ", "createdAt": 1_700_000}]
    out = asyncio.run(_outil(coeur, "planity_search_customers")(salon_id="biz-un"))
    assert out[0]["id"] == "cli-1" and out[0]["name"] == "Cliente Exemple"
    assert out[0]["created_at"] == _iso(1_700_000)


def test_clientes_un_ticket_totalise_ses_lignes_en_euros(coeur):
    coeur.client.get_customer_receipts.return_value = [
        {"receiptId": "r-1", "createdAt": 2_000,
         "lines": [{"price": 4500, "serviceId": "svc-1"},
                   {"price": 1200, "productId": "prd-1"}]}]
    out = asyncio.run(_outil(coeur, "planity_get_customer_receipts")(
        salon_id="biz-un", customer_id="cli-1"))
    assert out[0]["total_eur"] == 57.0
    assert [l["price_eur"] for l in out[0]["lines"]] == [45.0, 12.0]


def test_agenda_la_fenetre_part_en_JOURS_pas_en_millisecondes(coeur):
    """L'index de tri de Planity porte l'heure MURALE du salon, sans décalage : la
    fenêtre se donne en jours. Convertir en millisecondes en perdrait ou en
    gagnerait une selon la saison, et un rendez-vous de plus ou de moins ne se
    remarque pas."""
    asyncio.run(_outil(coeur, "planity_list_appointments")(
        salon_id="biz-un", preset="today"))
    coeur.module.resolve_range.assert_called_with(None, None, "today")
    coeur.client.list_appointments.assert_awaited_once_with(
        "biz-un", _iso(1_000)[:10], _iso(2_000)[:10], employee_id=None)


def test_agenda_le_filtre_par_collaboratrice_descend_au_coeur(coeur):
    """Le filtrage ne se refait pas ici : c'est le cœur qui sait quel enfant
    d'agenda lire, et refiltrer au-dessus masquerait un mauvais balayage."""
    asyncio.run(_outil(coeur, "planity_list_appointments")(
        salon_id="biz-un", employee_id="emp-1"))
    _, kwargs = coeur.client.list_appointments.await_args
    assert kwargs["employee_id"] == "emp-1"


def test_chiffres_le_ca_est_rendu_en_euros_avec_ses_bornes(coeur):
    coeur.client.get_key_indicators.return_value = {
        "revenueWithVAT": 123_456, "revenueWithoutVAT": 102_880,
        "amountOfReceipts": 12, "VATValue": 20_576, "averageBasket": 10_288}
    out = asyncio.run(_outil(coeur, "planity_get_revenue_summary")(
        salon_id="biz-un", preset="last_month"))
    assert out["revenue_ttc_eur"] == 1234.56 and out["revenue_ht_eur"] == 1028.8
    assert out["ticket_count"] == 12
    assert (out["from"], out["to"]) == (_iso(1_000), _iso(2_000))
    assert out["period"]["timezone"] == "Europe/Paris"


def test_chiffres_les_collaboratrices_sont_nommees_et_classees(coeur):
    """Les statistiques arrivent en TUPLES anonymes : le nom vient du référentiel,
    et une ligne trop courte est ignorée plutôt que lue de travers."""
    coeur.client.get_salon.return_value = _salon(
        employees=[_employe("emp-1", "Alex"), _employe("emp-2", "Camille")])
    coeur.client.get_calendar_stats.return_value = {
        "data": [["emp-1", 3, 4, None, 30_000, 100.0],
                 ["emp-2", 9, 12, None, 90_000, 75.0],
                 ["emp-tronque", 1]],
        "bySeller": {"data": {"emp-2": {"onlineAppointments": 4}}},
    }
    out = asyncio.run(_outil(coeur, "planity_get_seller_stats")(salon_id="biz-un"))
    assert [(s["seller_id"], s["name"], s["revenue_eur"]) for s in out] == [
        ("emp-2", "Camille", 900.0), ("emp-1", "Alex", 300.0)]
    assert out[0]["appointments"] == {"onlineAppointments": 4}


# ── La sonde de connexion ───────────────────────────────────────────────────

def test_le_connecteur_est_verifiable(coeur):
    from oto_mcp.connectors import verify as connector_verify

    _serveur(coeur)                       # `register()` pose la sonde
    assert connector_verify.supports("planity")
    assert connector_verify.couverture("planity") == connector_verify.AUTH


def test_la_sonde_ouvre_la_session_puis_liste_les_salons(coeur):
    from oto_mcp.connectors import verify as connector_verify
    from oto_mcp.tools import planity

    _serveur(coeur)
    coeur.client.list_salons.return_value = [_salon()]
    asyncio.run(connector_verify.run(
        "planity", {"email": "demo@example.com", "password": "s3cret"}))
    coeur.client.auth.get_tokens.assert_awaited()
    coeur.client.list_salons.assert_awaited()
    coeur.client.close.assert_awaited()


def test_la_sonde_refuse_un_compte_qui_n_ouvre_aucun_salon(coeur):
    """Authentifié ≠ utilisable : un compte dont le jeton ne porte aucun salon
    s'authentifie parfaitement et ne peut rien lire. Le rendre « connecté » serait
    le vert creux que la sonde existe pour empêcher."""
    from oto_mcp.connectors import verify as connector_verify

    _serveur(coeur)
    coeur.client.list_salons.return_value = []
    with pytest.raises(connector_verify.NonAutorise):
        asyncio.run(connector_verify.run(
            "planity", {"email": "demo@example.com", "password": "s3cret"}))
    assert connector_verify.classer(
        connector_verify.NonAutorise("x")) == connector_verify.UNAUTHORIZED


def test_la_sonde_classe_un_refus_d_identifiants_et_ne_le_confond_pas_avec_une_panne(coeur):
    from oto_mcp.connectors import verify as connector_verify

    _serveur(coeur)
    refus = RuntimeError("400")
    refus.response = types.SimpleNamespace(status_code=400)
    coeur.client.auth.get_tokens.side_effect = refus
    with pytest.raises(connector_verify.NonAutorise):
        asyncio.run(connector_verify.run(
            "planity", {"email": "demo@example.com", "password": "faux"}))
    coeur.client.close.assert_awaited()
