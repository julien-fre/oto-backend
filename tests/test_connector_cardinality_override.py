"""La cardinalité surchargée en base — et la garde qui empêche de la lire à moitié.

« La base peut primer sur le défaut du code » (Alexis, 27/08), pour qu'élargir un
connecteur ne demande pas un déploiement. Trois crans : org > plateforme > registre.

⚠️ **LE risque de ce lot n'est pas de mal lire la surcharge, c'est de ne la lire que
d'un côté.** La cardinalité est consultée par trois chemins très éloignés — la GARDE
D'ÉCRITURE (« ce deuxième compte a-t-il le droit d'exister ? »), la RÉSOLUTION
(« va-t-on le chercher ? ») et la CARTE SERVIE (« l'écran le propose-t-il ? »). Une
surcharge lue par la première seulement accepterait une ligne que personne n'irait
jamais lire : c'est mot pour mot le défaut d'oto-backend#409, corrigé le 27/08, et le
rouvrir serait le pire résultat possible de ce lot. D'où :

1. une **source unique** (`connectors.cardinality.is_multi_account`), et un test AST
   qui dit que personne ne court-circuite en lisant la propriété du registre ;
2. un test qui pose une surcharge, recharge, et vérifie que les DEUX verdicts basculent
   **ensemble** — accepter le second compte ET aller le résoudre.
3. **§3, ajouté le 2026-09-01 (oto-backend#732)** — le même défaut pris par l'autre
   bout : la carte du catalogue (`auth.cardinality`) sortait du registre PUR, donc du
   défaut du CODE. Une org élargie voyait le serveur accepter un second compte que
   l'écran ne proposait jamais. Écart LATENT quand il a été trouvé (les 74 connecteurs
   avaient le même réglage en code et en base) — c'est pourquoi il se corrige par un
   test, pas par un correctif d'urgence.
"""
from __future__ import annotations

import os
import uuid

import pytest

from oto_mcp import credentials_store as cs
from oto_mcp import providers
from oto_mcp.connectors import cardinality

SUB = "usr_card"
ORG, AUTRE_ORG = 1, 2
MEMBRE = f"{ORG}:{SUB}"

# `crunchbase` : session par personne (cookie) ⟹ le registre le dit MONO. C'est le
# candidat exact d'un élargissement — un connecteur que le code refuse et qu'une org
# veut ouvrir sans attendre un déploiement.
MONO_PAR_DEFAUT = "crunchbase"


@pytest.fixture(autouse=True)
def _cache_propre():
    """Le registre de surcharges est un état de PROCESS : sans ce reset, un test verrait
    les lignes d'un autre — et pire, un test qui passe pour une bonne raison passerait
    ensuite pour une mauvaise."""
    cardinality._reset_for_tests()
    yield
    cardinality._reset_for_tests()


# ─── 1. Une seule source, et personne ne la court-circuite ──────────────────

def test_la_garde_d_ecriture_et_la_resolution_lisent_la_MEME_fonction():
    """Sonde AST : hors du registre lui-même et de son seam, plus personne ne lit
    `auth_multi_account`. Un lecteur direct de la propriété serait un chemin qui ignore
    les surcharges — donc un demi-élargissement, donc #409."""
    import ast
    import pathlib

    racine = pathlib.Path(__file__).resolve().parent.parent / "oto_mcp"
    # Les seuls admis : le registre (il DÉFINIT la propriété), le seam (il la lit comme
    # DÉFAUT, après les surcharges), et les surfaces d'INVENTAIRE — qui décrivent le
    # catalogue, pas un appel, et n'ont donc pas d'org à consulter.
    admis = {
        "providers/_model.py", "providers/__init__.py",
        "connectors/cardinality.py",
        "connectors/identities.py",      # enregistrement des backends, au boot
        "call_axes.py",                  # annonce dynamique de l'axe (inventaire)
    }
    lecteurs = []
    for p in racine.rglob("*.py"):
        rel = p.relative_to(racine).as_posix()
        if rel in admis:
            continue
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Attribute) and n.attr == "auth_multi_account":
                lecteurs.append(f"{rel}:{n.lineno}")
    assert not lecteurs, (
        f"lecture DIRECTE de `auth_multi_account` hors du seam : {lecteurs}. La "
        "cardinalité se tranche par `connectors.cardinality.is_multi_account`, qui lit "
        "les surcharges AVANT le défaut du code — la court-circuiter fabrique un "
        "chemin qui ignore un élargissement (oto-backend#409).")


def test_sans_surcharge_le_defaut_du_registre_repond(monkeypatch):
    monkeypatch.setattr(cardinality, "_LOADED", True)
    monkeypatch.setattr(cardinality, "_OVERRIDES", {})
    assert cardinality.is_multi_account(MONO_PAR_DEFAUT) is False
    assert cardinality.is_multi_account("hunter") is True


def test_l_org_prime_sur_la_plateforme_qui_prime_sur_le_code(monkeypatch):
    """L'ordre EST l'arbitrage : org > plateforme > registre."""
    monkeypatch.setattr(cardinality, "_LOADED", True)
    monkeypatch.setattr(cardinality, "_OVERRIDES", {
        ("platform", "platform", MONO_PAR_DEFAUT): "multi",
        ("org", str(AUTRE_ORG), MONO_PAR_DEFAUT): "mono",
    })
    assert cardinality.is_multi_account(MONO_PAR_DEFAUT) is True            # plateforme
    assert cardinality.is_multi_account(MONO_PAR_DEFAUT, ORG) is True       # hérite
    assert cardinality.is_multi_account(MONO_PAR_DEFAUT, AUTRE_ORG) is False  # l'org prime


def test_la_surcharge_suit_la_DÉLÉGATION_de_credential(monkeypatch):
    """Une surcharge se pose sur le PORTEUR de la clé, et un canal la voit : sans cette
    normalisation, surcharger `unipile` laisserait ses six canaux au défaut du code —
    deux réponses pour une seule clé."""
    monkeypatch.setattr(cardinality, "_LOADED", True)
    monkeypatch.setattr(cardinality, "_OVERRIDES",
                        {("platform", "platform", "unipile"): "multi"})
    assert cardinality.is_multi_account("whatsapp") is True


def test_une_valeur_inconnue_est_IGNORÉE_et_journalisée(monkeypatch, caplog):
    """Jamais interprétée : inventer un sens à une valeur qu'on n'a pas posée, c'est
    décider à la place de celui qui l'a posée."""
    import logging
    monkeypatch.setattr(cardinality, "_LOADED", False)
    monkeypatch.setattr(
        "oto_mcp.db.connector_settings.list_connector_settings",
        lambda key=None, conn=None: [
            {"scope_type": "platform", "scope_id": "platform",
             "connector": MONO_PAR_DEFAUT, "value": "beaucoup", "key": "cardinality"}])
    with caplog.at_level(logging.WARNING):
        assert cardinality.reload() == 0
    assert any("valeur inconnue" in r.message for r in caplog.records)
    assert cardinality.is_multi_account(MONO_PAR_DEFAUT) is False


def test_une_base_injoignable_retombe_sur_les_defauts_du_code(monkeypatch, caplog):
    """Fail-open, et la DIRECTION est le point : une surcharge ÉLARGIT (mono → multi),
    donc son absence ne peut que resserrer — jamais ouvrir ce qui était fermé."""
    import logging
    monkeypatch.setattr(cardinality, "_LOADED", False)
    monkeypatch.setattr(cardinality, "_OVERRIDES", {})

    def _boom(key=None, conn=None):
        raise RuntimeError("base indisponible")
    monkeypatch.setattr("oto_mcp.db.connector_settings.list_connector_settings", _boom)
    with caplog.at_level(logging.WARNING):
        assert cardinality.is_multi_account(MONO_PAR_DEFAUT) is False
    assert any("surcharges illisibles" in r.message for r in caplog.records)


def test_l_axe_d_appel_s_ouvre_des_qu_UNE_org_a_elargi(monkeypatch):
    """L'axe `_account=` est lu par le middleware, sans org de contexte : le résoudre
    par org coûterait une requête PAR APPEL. Il est donc org-agnostique et permissif —
    il ne fait que NOMMER un compte, la résolution refuse (actionnable) si ce compte
    n'existe pas. Le refuser rendrait une org élargie incapable de viser son second
    compte : la ligne posée que rien ne va lire."""
    monkeypatch.setattr(cardinality, "_LOADED", True)
    monkeypatch.setattr(cardinality, "_OVERRIDES", {})
    assert cardinality.accepted_anywhere(MONO_PAR_DEFAUT) is False
    monkeypatch.setattr(cardinality, "_OVERRIDES",
                        {("org", "42", MONO_PAR_DEFAUT): "multi"})
    assert cardinality.accepted_anywhere(MONO_PAR_DEFAUT) is True


# ─── 2. LA garde : les deux verdicts basculent ENSEMBLE ─────────────────────

@pytest.fixture(scope="module")
def live(pg_dsn):
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    name = "oto_card_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{name}"')
    dsn = pg_dsn.rsplit("/", 1)[0] + "/" + name

    avant_url, avant_pool = os.environ.get("DATABASE_URL"), dbconn._pool
    avant_key = os.environ.get("OTO_MCP_MASTER_KEY")
    os.environ["DATABASE_URL"] = dsn
    os.environ["OTO_MCP_MASTER_KEY"] = "3" * 64
    dbconn._pool = None
    try:
        from oto_mcp.db import init_db
        init_db()
        yield
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = avant_pool
        for cle, valeur in (("DATABASE_URL", avant_url),
                            ("OTO_MCP_MASTER_KEY", avant_key)):
            if valeur is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = valeur
        root.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        root.close()


def _pose_second_compte() -> str:
    """Tente de poser un DEUXIÈME compte nommé. Rend `"accepté"` ou le code du refus."""
    try:
        cs.guard_account_write(cs.MEMBER, MEMBRE, MONO_PAR_DEFAUT, "second", org=ORG)
        return "accepté"
    except cs.SingleAccountConnector:
        return "refusé"


def _resolution_va_chercher_les_comptes() -> bool:
    """La résolution emprunte-t-elle le chemin de SÉLECTION de compte pour cette org ?
    C'est le verdict qui doit basculer en même temps que la garde — sinon la ligne
    acceptée reste inerte."""
    from oto_mcp import access
    return access.cascade._is_multi_account(MONO_PAR_DEFAUT, ORG)


def test_une_surcharge_posée_puis_rechargée_change_les_DEUX_verdicts(live):
    """⚠️ LE test du lot. Il ne vérifie pas que la surcharge « marche » — il vérifie
    qu'elle ne marche pas à MOITIÉ. Trois temps : avant, après la pose SANS
    rechargement, après le rechargement."""
    from oto_mcp.db import connector_settings as store

    cardinality._reset_for_tests()
    # 1. Avant : le code dit mono, les deux refusent ensemble.
    assert _pose_second_compte() == "refusé"
    assert _resolution_va_chercher_les_comptes() is False

    # 2. La ligne est posée, mais rien n'est rechargé : RIEN ne bouge. C'est le prix
    #    assumé du zéro-requête sur le chemin chaud, et il doit être visible.
    store.set_connector_setting("org", str(ORG), MONO_PAR_DEFAUT, cardinality.KEY,
                                "multi", set_by=SUB)
    assert _pose_second_compte() == "refusé"
    assert _resolution_va_chercher_les_comptes() is False

    # 3. Après le rechargement : les DEUX basculent, ensemble.
    assert cardinality.reload() == 1
    assert _pose_second_compte() == "accepté"
    assert _resolution_va_chercher_les_comptes() is True

    # 4. Et une AUTRE org n'a rien gagné — la surcharge est scopée.
    from oto_mcp import access
    assert access.cascade._is_multi_account(MONO_PAR_DEFAUT, AUTRE_ORG) is False


def test_retirer_la_surcharge_referme_les_DEUX_verdicts(live):
    """Le mouvement inverse, et il compte autant : un réglage qui ne se retire pas
    proprement est un réglage qu'on n'ose pas poser."""
    from oto_mcp.db import connector_settings as store

    store.set_connector_setting("org", str(ORG), MONO_PAR_DEFAUT, cardinality.KEY,
                                "multi", set_by=SUB)
    cardinality.reload()
    assert _pose_second_compte() == "accepté"

    assert store.clear_connector_setting("org", str(ORG), MONO_PAR_DEFAUT,
                                         cardinality.KEY) is True
    cardinality.reload()
    assert _pose_second_compte() == "refusé"
    assert _resolution_va_chercher_les_comptes() is False


def test_poser_une_surcharge_est_IDEMPOTENT(live):
    """Reposer la même ligne ne la duplique pas (la clé primaire est le quadruplet) et
    la remplacer change bien la valeur — un réglage se corrige, il ne s'empile pas."""
    from oto_mcp.db import connector_settings as store

    for valeur in ("multi", "multi", "mono"):
        store.set_connector_setting("platform", "platform", MONO_PAR_DEFAUT,
                                    cardinality.KEY, valeur, set_by=SUB)
    lignes = store.list_connector_settings(cardinality.KEY)
    assert len(lignes) == 1 and lignes[0]["value"] == "mono"


# ─── 3. La CARTE SERVIE : l'écran reflète la BASE, pas le code (#732) ───────

# Le connecteur que le code dit MULTI, pour mesurer le mouvement dans les deux sens :
# une surcharge qui RESSERRE doit se voir sur la carte autant qu'une qui élargit.
MULTI_PAR_DEFAUT = "hunter"


def _carte(nom: str, org=None) -> dict:
    """La ligne de catalogue de `nom`, telle qu'une surface AUTHENTIFIÉE la sert.

    Reproduit ce que font les deux seams réels (`api.public.connectors_catalog` et
    `capabilities.connectors.selection._visible_catalog`) : la ligne du registre, puis
    l'overlay. Le test AST plus bas est ce qui garantit qu'ils sont bien les deux
    seuls — ici on mesure le CONTENU servi."""
    lignes = [r for r in providers.public_catalog() if r["name"] == nom]
    assert lignes, f"`{nom}` absent du registre — le banc ne mesure rien"
    return cardinality.overlay_for_org(lignes, org)[0]


def test_le_banc_voit_bien_les_deux_defauts_du_code(monkeypatch):
    """Témoin. Sans lui, un catalogue mal chargé ou deux connecteurs devenus
    identiques feraient passer toute la section sans rien prouver — et la mesure d'un
    élargissement sur un connecteur DÉJÀ multi ne prouve rien du tout."""
    monkeypatch.setattr(cardinality, "_LOADED", True)
    monkeypatch.setattr(cardinality, "_OVERRIDES", {})
    assert _carte(MONO_PAR_DEFAUT)["auth"]["cardinality"] == cardinality.SERVI_SINGLE
    assert _carte(MULTI_PAR_DEFAUT)["auth"]["cardinality"] == cardinality.SERVI_MULTI


def test_une_org_ÉLARGIE_lit_multi_account_SUR_LA_CARTE(monkeypatch):
    """⚠️ LE test de #732. Le serveur acceptait déjà le second compte pour cette org ;
    c'est l'écran qui l'ignorait, parce que la carte sortait du registre pur."""
    monkeypatch.setattr(cardinality, "_LOADED", True)
    monkeypatch.setattr(cardinality, "_OVERRIDES",
                        {("org", str(ORG), MONO_PAR_DEFAUT): "multi"})
    assert _carte(MONO_PAR_DEFAUT, ORG)["auth"]["cardinality"] == cardinality.SERVI_MULTI
    # Et l'org voisine n'a rien gagné : la carte est SCOPÉE, comme la garde d'écriture.
    assert _carte(MONO_PAR_DEFAUT, AUTRE_ORG)["auth"]["cardinality"] \
        == cardinality.SERVI_SINGLE
    # Ni la vitrine anonyme, qui n'a pas d'org de contexte à consulter.
    assert _carte(MONO_PAR_DEFAUT)["auth"]["cardinality"] == cardinality.SERVI_SINGLE


def test_une_org_RESSERRÉE_lit_single_sur_la_carte(monkeypatch):
    """L'autre sens, et il compte autant : une carte qui ne sait qu'élargir laisserait
    un écran proposer un geste que la garde d'écriture refuse — l'erreur miroir, et la
    plus visible des deux pour l'utilisateur."""
    monkeypatch.setattr(cardinality, "_LOADED", True)
    monkeypatch.setattr(cardinality, "_OVERRIDES",
                        {("org", str(ORG), MULTI_PAR_DEFAUT): "mono"})
    assert _carte(MULTI_PAR_DEFAUT, ORG)["auth"]["cardinality"] \
        == cardinality.SERVI_SINGLE


def test_la_carte_dit_EXACTEMENT_ce_que_la_garde_d_ecriture_accepte(monkeypatch):
    """L'invariant, sur TOUT le registre plutôt que sur un connecteur choisi : pour
    chaque connecteur et pour cette org, « l'écran propose un second compte » et « le
    serveur l'accepte » sont la même phrase. C'est cette égalité que #732 rompait."""
    monkeypatch.setattr(cardinality, "_LOADED", True)
    monkeypatch.setattr(cardinality, "_OVERRIDES", {
        ("org", str(ORG), MONO_PAR_DEFAUT): "multi",
        ("platform", "platform", MULTI_PAR_DEFAUT): "mono",
    })
    servi = cardinality.overlay_for_org(providers.public_catalog(), ORG)
    ecarts = [r["name"] for r in servi
              if (r["auth"]["cardinality"] == cardinality.SERVI_MULTI)
              is not cardinality.is_multi_account(r["name"], ORG)]
    assert not ecarts, (
        f"la carte servie contredit la garde d'écriture sur : {ecarts}. L'utilisateur "
        "y verrait un geste que le serveur refuse, ou l'inverse (#732).")


def test_l_overlay_suit_la_DÉLÉGATION_de_credential(monkeypatch):
    """Une surcharge posée sur le PORTEUR se voit sur la carte de ses canaux — même
    normalisation que `is_multi_account`, puisque c'est lui qu'on rappelle. Sans elle,
    la carte WhatsApp afficherait un autre verdict que la clé qu'elle fait poser."""
    monkeypatch.setattr(cardinality, "_LOADED", True)
    monkeypatch.setattr(cardinality, "_OVERRIDES",
                        {("platform", "platform", "unipile"): "mono"})
    assert _carte("whatsapp")["auth"]["cardinality"] == cardinality.SERVI_SINGLE


def test_l_overlay_ne_REECRIT_PAS_la_ligne_qu_on_lui_donne(monkeypatch):
    """Copie à l'écriture. L'appelant peut passer un cache ou une ligne partagée : la
    réécrire en place ferait porter à une org la cardinalité de la précédente — un bug
    de fuite inter-org, silencieux, et impossible à voir sur une seule requête."""
    monkeypatch.setattr(cardinality, "_LOADED", True)
    monkeypatch.setattr(cardinality, "_OVERRIDES",
                        {("org", str(ORG), MONO_PAR_DEFAUT): "multi"})
    origine = [r for r in providers.public_catalog() if r["name"] == MONO_PAR_DEFAUT]
    servi = cardinality.overlay_for_org(origine, ORG)
    assert servi[0]["auth"]["cardinality"] == cardinality.SERVI_MULTI
    assert origine[0]["auth"]["cardinality"] == cardinality.SERVI_SINGLE, \
        "l'overlay a muté la ligne d'origine"
    # Et `credential_fields`, dérivé du même descripteur, n'a pas suivi la copie par
    # accident : les deux clés doivent rester la MÊME liste de champs.
    assert servi[0]["credential_fields"] == origine[0]["auth"]["fields"]


def test_une_ligne_sans_descripteur_traverse_intacte(monkeypatch):
    """La projection COMPACTE de `/api/me/connectors` ne porte pas `auth`. L'overlay
    ne filtre jamais : une ligne qu'il ne sait pas réécrire ressort telle quelle."""
    monkeypatch.setattr(cardinality, "_LOADED", True)
    monkeypatch.setattr(cardinality, "_OVERRIDES", {})
    compact = [{"name": MONO_PAR_DEFAUT, "label": "Crunchbase"}]
    assert cardinality.overlay_for_org(compact, ORG) == compact


def test_toute_surface_qui_SERT_le_catalogue_passe_par_l_overlay():
    """Sonde AST — le garde-fou MÉCANIQUE de #732, et la vraie leçon du lot.

    Le défaut n'était pas une mauvaise valeur : c'était une surface servie qui lisait
    le registre sans repasser par le seam. Une règle écrite dans une doc ne l'aurait
    pas empêché de revenir au prochain endpoint qui rend un catalogue. La règle est
    donc forcée : **toute fonction qui appelle `public_catalog()` appelle aussi
    `overlay_for_org()`** — ce sont deux moitiés d'un même geste."""
    import ast
    import pathlib

    racine = pathlib.Path(__file__).resolve().parent.parent / "oto_mcp"
    # Le seul admis : le producteur lui-même, qui DÉFINIT `public_catalog`.
    admis = {"providers/__init__.py"}
    nus = []
    for p in racine.rglob("*.py"):
        rel = p.relative_to(racine).as_posix()
        if rel in admis:
            continue
        arbre = ast.parse(p.read_text(encoding="utf-8"))
        for n in ast.walk(arbre):
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            appels = {c.func.attr for c in ast.walk(n)
                      if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
            if "public_catalog" in appels and "overlay_for_org" not in appels:
                nus.append(f"{rel}:{n.name}:{n.lineno}")
    assert not nus, (
        f"catalogue servi SANS la cardinalité effective : {nus}. `public_catalog()` "
        "rend le défaut du CODE (le registre est pur) ; une surface qui connaît un "
        "requérant doit repasser par `cardinality.overlay_for_org(rows, org)` — sinon "
        "elle propose à l'écran autre chose que ce que le serveur accepte (#732).")
