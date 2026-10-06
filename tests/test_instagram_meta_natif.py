"""Le connecteur `instagram_meta` — les statistiques Instagram, en natif.

⚠️ **Le cœur est MOQUÉ ici, à sa frontière** (`oto.tools.instagram_meta` posé
dans `sys.modules`), et pas par confort : le venv de ce dépôt porte une COPIE
FIGÉE d'oto-core au tag épinglé, qui ne contient pas encore ce paquet. Un test
qui importerait le vrai cœur mesurerait l'âge du venv, pas le code d'ici. Ce
qu'on vérifie est donc exactement le périmètre du backend : la déclaration au
registre, le flux hébergé, le coffre, le renouvellement (à l'usage ET quotidien),
et les messages de refus.

⚠️ **Aucun appel réel à Meta n'a jamais été joué** : l'application n'existe pas
encore à l'écriture de ce fichier. Rien ici ne le prétend — les seuls échanges
« réussis » sont ceux d'un double.

Le fil rouge : **un jeton Instagram ne se renouvelle que tant qu'il vit.** Meta
n'émet pas de `refresh_token`. C'est ce qui rend testables des choses qui
n'auraient pas d'intérêt ailleurs — qu'une expiration soit reconnue AVANT
d'appeler, que le refus porte la DATE, et qu'une passe quotidienne existe.
"""
from __future__ import annotations

import asyncio
import sys
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from oto_mcp import providers

CONNECTEUR = "instagram_meta"
ORG = 42
SUB = "user-de-test"
MEMBRE = f"{ORG}:{SUB}"

#: Coordonnées MANIFESTEMENT fictives. Les vraies appartiennent à l'application
#: Meta de l'exploitant et se posent en base (`connector_settings`, scope
#: plateforme) : rien de tout ça ne vit dans le dépôt.
_COORDONNEES = {"app_id": "app-id-fictif", "app_secret": "secret-fictif"}


# ── Le cœur, moqué à sa frontière ───────────────────────────────────────────

def _faux_coeur():
    """`oto.tools.instagram_meta` réduit à ce que le backend en importe.

    Les fonctions de DATE sont de vraies implémentations, pas des doubles : le
    backend décide de renouveler ou de refuser à partir d'elles, et un double qui
    rendrait toujours `False` ferait passer tous les tests de ce fichier sans que
    la moindre décision soit exercée. Leur exactitude, elle, est vérifiée dans
    oto-core, où elle vit."""
    mod = types.ModuleType("oto.tools.instagram_meta")

    class InstagramAuthExpired(RuntimeError):
        def __init__(self, message="", expires_at=None):
            super().__init__(message)
            self.expires_at = expires_at

    class InstagramApiError(RuntimeError):
        pass

    def parse_ts(v):
        if not v:
            return None
        try:
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    def utcnow():
        return datetime.now(timezone.utc)

    def iso(dt):
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def expiry(expires_at=None, issued_at=None):
        return parse_ts(expires_at) or (
            parse_ts(issued_at) + timedelta(days=60) if parse_ts(issued_at) else None)

    def is_expired(expires_at=None, issued_at=None, now=None):
        exp = expiry(expires_at, issued_at)
        return bool(exp and exp <= (now or utcnow()))

    def needs_refresh(expires_at=None, issued_at=None, now=None):
        maintenant = now or utcnow()
        exp = expiry(expires_at, issued_at)
        if exp is None or exp <= maintenant:
            return False
        emis = parse_ts(issued_at)
        if emis and maintenant - emis < timedelta(hours=24):
            return False
        return exp - maintenant < timedelta(days=53)

    mod.InstagramAuthExpired = InstagramAuthExpired
    mod.InstagramApiError = InstagramApiError
    mod.InstagramApp = MagicMock(name="InstagramApp")
    mod.InstagramClient = MagicMock(name="InstagramClient")
    mod.authorize_url = MagicMock(
        return_value="https://www.instagram.com/oauth/authorize?x=1")
    mod.connect = MagicMock()
    mod.refresh_long_lived = MagicMock(
        return_value={"access_token": "jeton-neuf", "expires_in": 5_184_000})
    mod.compute_best_hours = MagicMock(return_value={"sample_size": 0})
    mod.parse_ts, mod.utcnow, mod.iso = parse_ts, utcnow, iso
    mod.expiry, mod.is_expired, mod.needs_refresh = expiry, is_expired, needs_refresh
    return mod


def _dans(jours: float) -> str:
    return (datetime.now(timezone.utc)
            + timedelta(days=jours)).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Coffre:
    """Un coffre en mémoire, à la forme de `credentials_store`."""

    def __init__(self):
        self.lignes: dict[tuple, dict] = {}
        self.rejets: list[tuple] = []

    def poser(self, secret, meta, entity_id=MEMBRE, account=""):
        self.lignes[("member", entity_id, account)] = {
            "secret": secret, "meta": dict(meta), "set_at": "2026-09-09T00:00:00Z",
            "set_by": SUB}

    def get(self, entity_type, entity_id, connector, account=""):
        assert connector == CONNECTEUR
        ligne = self.lignes.get((entity_type, entity_id, account))
        return dict(ligne) if ligne else None

    def set(self, entity_type, entity_id, connector, secret, set_by=None,
            meta=None, conn=None, account="", expected_version=None):
        assert connector == CONNECTEUR
        self.lignes[(entity_type, entity_id, account)] = {
            "secret": secret, "meta": dict(meta or {}),
            "set_at": "2026-09-09T00:00:00Z", "set_by": set_by}

    def marquer(self, entity_type, entity_id, provider, account, error):
        self.rejets.append((entity_id, account, error))


@pytest.fixture
def env(monkeypatch):
    """Faux cœur, coordonnées posées, coffre en mémoire, org de contexte."""
    from oto_mcp import credentials_store
    from oto_mcp.auth import instagram_meta as ig_auth
    from oto_mcp.connectors import health as connector_health
    from oto_mcp.db import connector_settings as store

    mod = _faux_coeur()
    monkeypatch.setitem(sys.modules, "oto.tools.instagram_meta", mod)
    monkeypatch.setattr(store, "list_connector_settings",
                        lambda key=None, conn=None: [
                            {"scope_type": "platform", "scope_id": "platform",
                             "connector": CONNECTEUR, "key": k, "value": v}
                            for k, v in _COORDONNEES.items()])
    coffre = _Coffre()
    monkeypatch.setattr(credentials_store, "get_credential_with_meta", coffre.get)
    monkeypatch.setattr(credentials_store, "set_credential", coffre.set)
    monkeypatch.setattr(connector_health, "mark_rejected", coffre.marquer)
    monkeypatch.setattr("oto_mcp.access.current_org", lambda sub: ORG)
    monkeypatch.setattr("oto_mcp.access.current_user_sub_or_raise",
                        lambda: SUB)
    monkeypatch.setenv("OTO_MCP_PUBLIC_URL", "https://mcp.exemple.test")
    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", "secret-de-signature-de-test")
    return types.SimpleNamespace(coeur=mod, coffre=coffre, auth=ig_auth)


# ── La déclaration au registre ──────────────────────────────────────────────

def test_deux_connecteurs_instagram_et_le_nom_dit_lequel():
    """`instagram` = les DM opérés par Unipile ; `instagram_meta` = les
    statistiques par l'API de Meta. Sources différentes, credentials différents,
    droits différents : ce sont deux connecteurs, pas deux modes d'un seul."""
    dm, stats = providers.REGISTRY["instagram"], providers.REGISTRY[CONNECTEUR]
    assert dm.credential_of == "unipile" and stats.credential_of is None
    assert dm.secret_kind == "api_key" and stats.secret_kind == "oauth"
    assert stats.auth_modes == frozenset({"byo_user"})


def test_le_namespace_ne_tombe_pas_sur_le_connecteur_de_messagerie():
    """LE point qui rend le nom à deux tokens jouable. `namespace_of` résout au plus
    long préfixe DÉCLARÉ : sans ça, `instagram_meta_get_profile` serait gaté par le
    connecteur des DM — donc par la mauvaise activation, la mauvaise ACL et le
    mauvais credential, en silence."""
    from oto_mcp import tool_visibility as tv

    assert tv.namespace_of("instagram_meta_get_profile") == CONNECTEUR
    assert tv.namespace_of("instagram_chat") == "instagram"
    assert providers.connector_for_namespace(CONNECTEUR).name == CONNECTEUR


def test_la_fiche_nomme_meta_comme_editeur():
    """L'éditeur nomme QUI REÇOIT l'appel. Ici c'est bien Meta : son API officielle,
    son dialogue de consentement, son jeton — contrairement à `planity`, où oto
    rejoue lui-même une session faute d'API publique."""
    c = providers.REGISTRY[CONNECTEUR]
    assert c.publisher_name == "Meta"
    assert "Facebook" in c.help and "Read-only" in c.help
    assert c.doc_sections, "la fiche doit être servie depuis son markdown"


def test_la_fiche_annonce_la_limite_des_testeurs_avant_qu_on_bute_dessus():
    """Sans App Review, seuls les comptes invités peuvent autoriser. C'est LA
    condition d'entrée du connecteur, et Meta la refuse avec le même message qu'un
    refus volontaire — donc si la fiche ne la dit pas, personne ne la déduira."""
    corps = " ".join(s.body_md for s in providers.REGISTRY[CONNECTEUR].doc_sections)
    assert "tester" in corps
    assert "professional" in corps
    assert "60 days" in corps


# ── Le flux hébergé ─────────────────────────────────────────────────────────

def test_l_url_de_retour_est_derivee_de_l_environnement(env):
    """Elle n'est jamais écrite en dur : la preprod et la prod n'ont pas la même, et
    une URL de prose ment dès qu'on la relit depuis l'autre."""
    from oto_mcp.connectors import flow as connector_flow

    assert connector_flow.supports(CONNECTEUR)
    assert (connector_flow.callback_url(CONNECTEUR)
            == "https://mcp.exemple.test/api/instagram_meta/oauth/callback")


def test_le_state_ne_vaut_que_pour_ce_flux(env):
    """L'audience ferme le rejeu inter-connecteurs par construction : un state signé
    pour un autre flux ne se relit pas ici."""
    from oto_mcp.auth import flow as oauth_flow

    etat = env.auth.make_state(SUB, ORG, "")
    assert env.auth.verify_state(etat) == (SUB, ORG, "")
    assert env.auth.verify_state(oauth_flow.sign_state(
        "salesforce", {"sub": SUB, "org": ORG})) is None
    assert env.auth.verify_state("n'importe quoi") is None


def test_sans_coordonnees_le_bouton_refuse_en_nommant_la_cle_et_le_geste(
        env, monkeypatch):
    """Le refus est le point : sans lui, le dialogue partirait avec un `client_id`
    vide et Instagram afficherait son écran générique — que l'utilisatrice lit comme
    « oto n'a pas le droit », alors que rien ne dépend d'elle."""
    from oto_mcp.db import connector_settings as store

    monkeypatch.setattr(store, "list_connector_settings",
                        lambda key=None, conn=None: [])
    assert env.auth.coordonnees_manquantes() == ["app_id", "app_secret"]
    assert env.auth.app_disponible(SUB) is False
    with pytest.raises(RuntimeError) as e:
        env.auth.app()
    assert "app_id" in str(e.value) and "oto_admin_connector_setting" in str(e.value)
    assert "not your account" in str(e.value)     # dit que ce n'est PAS l'utilisatrice


def test_le_dialogue_part_avec_l_application_de_l_instance(env):
    env.auth.build_auth_url(SUB, "")
    app, retour, etat = env.coeur.authorize_url.call_args.args
    assert retour == "https://mcp.exemple.test/api/instagram_meta/oauth/callback"
    assert env.auth.verify_state(etat) == (SUB, ORG, "")


# ── Le coffre ───────────────────────────────────────────────────────────────

def test_le_jeton_est_le_secret_et_le_reste_va_dans_meta(env):
    """`secret_kind="oauth"` ⟹ pas de schéma de champs, donc le blob EST le jeton.
    Le `user_id` compte autant : sans lui, aucun chemin de données ne se construit."""
    grant = types.SimpleNamespace(access_token="jeton-long", user_id="1784100",
                                  username="compte-de-test", expires_in=5_184_000)
    out = env.auth.persist_grant(SUB, ORG, grant)
    ligne = env.coffre.lignes[("member", MEMBRE, "")]
    assert ligne["secret"] == "jeton-long"
    assert ligne["meta"]["user_id"] == "1784100"
    assert ligne["meta"]["username"] == "compte-de-test"
    assert ligne["meta"]["expires_at"] == out["expires_at"]


# ── La résolution à l'appel ─────────────────────────────────────────────────

def _session():
    from oto_mcp.tools import instagram_meta_session as s
    return s


def test_sans_compte_connecte_le_refus_dit_le_geste(env):
    with pytest.raises(RuntimeError) as e:
        _session().resolve_token(SUB)
    assert "No Instagram account connected" in str(e.value)
    assert "connectors" in str(e.value)


def test_un_jeton_frais_est_servi_sans_toucher_a_meta(env):
    env.coffre.poser("jeton-vivant",
                     {"user_id": "1784100", "expires_at": _dans(59),
                      "connected_at": _dans(-1)})
    assert _session().resolve_token(SUB) == ("jeton-vivant", "1784100")
    env.coeur.refresh_long_lived.assert_not_called()


def test_un_jeton_qui_approche_du_terme_est_renouvele_a_l_usage(env):
    env.coffre.poser("jeton-vieux",
                     {"user_id": "1784100", "expires_at": _dans(40),
                      "connected_at": _dans(-20)})
    jeton, user_id = _session().resolve_token(SUB)
    assert (jeton, user_id) == ("jeton-neuf", "1784100")
    ligne = env.coffre.lignes[("member", MEMBRE, "")]
    assert ligne["secret"] == "jeton-neuf"
    # le meta est re-passé COMPLET : sans ça, `set_credential` l'écraserait par {}
    # et l'identifiant de compte disparaîtrait avec l'échéance
    assert ligne["meta"]["user_id"] == "1784100"
    assert ligne["meta"]["refreshed_at"]
    assert ligne["meta"]["expires_at"] > _dans(50)


def test_un_renouvellement_efface_le_marquage_de_la_fois_d_avant(env):
    """Sans cet effacement, la fiche resterait rouge sur une connexion saine."""
    env.coffre.poser("jeton-vieux",
                     {"user_id": "1784100", "expires_at": _dans(40),
                      "connected_at": _dans(-20), "health_ko": True,
                      "health_reason": "un vieux refus"})
    _session().resolve_token(SUB)
    meta = env.coffre.lignes[("member", MEMBRE, "")]["meta"]
    assert "health_ko" not in meta and "health_reason" not in meta


def test_une_autorisation_expiree_refuse_avec_la_date_et_le_geste(env):
    """Le cas que ce connecteur ne peut pas éviter, et qu'il doit donc bien dire.
    Une date rend le message lisible comme ce qu'il est ; sans elle, il se lit
    comme une panne, et on réessaie."""
    morte = "2026-07-08T09:00:00Z"      # deux mois avant aujourd'hui
    env.coffre.poser("jeton-mort", {"user_id": "1784100", "expires_at": morte})
    with pytest.raises(_session().InstagramReauthRequired) as e:
        _session().resolve_token(SUB)
    message = str(e.value)
    assert "8 juillet 2026" in message
    assert "60 days" in message and "Reconnect" in message
    # et on n'appelle PAS Meta pour se l'entendre dire
    env.coeur.refresh_long_lived.assert_not_called()
    # la ligne est marquée : la fiche le dira sans attendre le prochain appel
    assert env.coffre.rejets and "8 juillet 2026" in env.coffre.rejets[0][2]


def test_un_renouvellement_refuse_par_meta_devient_une_demande_de_reconsentement(env):
    env.coffre.poser("jeton-vieux",
                     {"user_id": "1784100", "expires_at": _dans(3),
                      "connected_at": _dans(-57)})
    env.coeur.refresh_long_lived.side_effect = env.coeur.InstagramAuthExpired("mort")
    with pytest.raises(_session().InstagramReauthRequired):
        _session().resolve_token(SUB)
    assert env.coffre.rejets, "la ligne doit être marquée pour la fiche"


def test_une_panne_de_renouvellement_ne_se_dit_pas_autorisation_morte(env):
    """Marquer ici ferait dire à la fiche « autorisation morte » sur une
    autorisation vivante, et enverrait refaire un consentement inutile."""
    env.coffre.poser("jeton-vieux",
                     {"user_id": "1784100", "expires_at": _dans(3),
                      "connected_at": _dans(-57)})
    env.coeur.refresh_long_lived.side_effect = TimeoutError("réseau")
    with pytest.raises(RuntimeError) as e:
        _session().resolve_token(SUB)
    assert not isinstance(e.value, _session().InstagramReauthRequired)
    assert "retry" in str(e.value)
    assert env.coffre.rejets == []
    # et on ne sert pas non plus le vieux jeton : la panne se dirait un appel
    # plus loin, où plus rien ne l'expliquerait
    assert env.coffre.lignes[("member", MEMBRE, "")]["secret"] == "jeton-vieux"


def test_un_coffre_sans_identifiant_de_compte_le_dit(env):
    env.coffre.poser("jeton", {"expires_at": _dans(59)})
    with pytest.raises(RuntimeError, match="account identifier"):
        _session().resolve_token(SUB)


# ── La passe quotidienne ────────────────────────────────────────────────────

def _brancher_balayage(monkeypatch, coffre):
    """`renouveler_les_jetons` lit les lignes du connecteur en SQL direct."""
    from oto_mcp.db import _conn as db_conn

    class _Curseur:
        def __init__(self, lignes):
            self._lignes = lignes

        def fetchall(self):
            return self._lignes

    class _Conn:
        def __init__(self, lignes):
            self._lignes = lignes

        def execute(self, sql, params=None):
            assert "connector_credentials" in sql
            return _Curseur(self._lignes)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    lignes = [{"entity_type": t, "entity_id": e, "account": a}
              for (t, e, a) in coffre.lignes]
    monkeypatch.setattr(db_conn, "_connect", lambda: _Conn(lignes))


def test_la_passe_quotidienne_renouvelle_ce_qui_approche_du_terme(env, monkeypatch):
    """**Le renouvellement paresseux ne suffit pas ici.** Un jeton Meta ne se
    renouvelle que tant qu'il vit : sans déclencheur indépendant de l'usage, une
    personne qui ne consulte pas ses statistiques pendant deux mois perd sa
    connexion sans avoir rien fait."""
    env.coffre.poser("frais", {"user_id": "1", "expires_at": _dans(59),
                               "connected_at": _dans(-1)}, entity_id="1:a")
    env.coffre.poser("a-renouveler", {"user_id": "2", "expires_at": _dans(40),
                                      "connected_at": _dans(-20)}, entity_id="1:b")
    env.coffre.poser("mort", {"user_id": "3", "expires_at": _dans(-1)},
                     entity_id="1:c")
    _brancher_balayage(monkeypatch, env.coffre)

    out = _session().renouveler_les_jetons()
    assert out["examines"] == 3
    assert out["renouveles"] == 1 and out["expires"] == 1 and out["echecs"] == 0
    assert env.coffre.lignes[("member", "1:a", "")]["secret"] == "frais"
    assert env.coffre.lignes[("member", "1:b", "")]["secret"] == "jeton-neuf"
    # une autorisation morte est marquée, pour que la fiche le dise
    assert [r[0] for r in env.coffre.rejets] == ["1:c"]


def test_la_passe_a_blanc_compte_sans_ecrire(env, monkeypatch):
    env.coffre.poser("a-renouveler", {"user_id": "2", "expires_at": _dans(40),
                                      "connected_at": _dans(-20)}, entity_id="1:b")
    _brancher_balayage(monkeypatch, env.coffre)
    env.coffre.poser("mort", {"user_id": "3", "expires_at": _dans(-1)},
                     entity_id="1:c")
    _brancher_balayage(monkeypatch, env.coffre)
    out = _session().renouveler_les_jetons(dry_run=True)
    assert out["renouveles"] == 1 and out["expires"] == 1 and out["dry_run"] is True
    assert env.coffre.lignes[("member", "1:b", "")]["secret"] == "a-renouveler"
    env.coeur.refresh_long_lived.assert_not_called()
    # « à blanc » veut dire qu'on n'écrit RIEN — le marquage d'une autorisation
    # morte est une écriture comme une autre.
    assert env.coffre.rejets == []


def test_une_ligne_en_echec_n_arrete_pas_la_passe(env, monkeypatch):
    """Fail-open par LIGNE : une autorisation morte est le cas normal après une
    révocation, et elle ne doit pas empêcher de renouveler les autres."""
    env.coffre.poser("un", {"user_id": "1", "expires_at": _dans(40),
                            "connected_at": _dans(-20)}, entity_id="1:a")
    env.coffre.poser("deux", {"user_id": "2", "expires_at": _dans(40),
                              "connected_at": _dans(-20)}, entity_id="1:b")
    _brancher_balayage(monkeypatch, env.coffre)
    appels = {"n": 0}

    def _capricieux(jeton, expires_at=None):
        appels["n"] += 1
        if appels["n"] == 1:
            raise env.coeur.InstagramAuthExpired("mort")
        return {"access_token": "jeton-neuf", "expires_in": 5_184_000}

    env.coeur.refresh_long_lived = _capricieux
    out = _session().renouveler_les_jetons()
    assert out["echecs"] == 1 and out["renouveles"] == 1


def test_la_passe_sans_coeur_le_dit_au_lieu_de_rendre_un_zero(env, monkeypatch):
    """C'est l'état de la PROD entre le déploiement du backend et le tag oto-core
    qui porte le cœur — donc le premier comportement réel de ce travail. Un `{}`
    silencieux s'y lirait « rien à renouveler », c'est-à-dire tout va bien."""
    monkeypatch.delitem(sys.modules, "oto.tools.instagram_meta")
    monkeypatch.setattr(
        "importlib.import_module",
        lambda nom, *a, **k: (_ for _ in ()).throw(ImportError("absent")))
    out = _session().renouveler_les_jetons()
    assert out["examines"] == 0 and "note" in out
    assert "oto-core" in out["note"]


def test_le_travail_de_maintenance_est_dans_la_passe_quotidienne():
    """Il n'a d'effet que s'il TOURNE : hors de `_ALL`, il ne serait joué par
    personne, et le connecteur perdrait sa seule protection contre le non-usage."""
    from oto_mcp import maintenance

    assert "instagram-tokens" in maintenance._TRAVAUX
    assert "instagram-tokens" in maintenance._ALL


# ── Les outils ──────────────────────────────────────────────────────────────

_ATTENDUS = sorted([
    "instagram_meta_get_profile", "instagram_meta_get_recent_media",
    "instagram_meta_get_media_insights", "instagram_meta_get_account_insights",
    "instagram_meta_get_best_hours",
])


def _serveur():
    from fastmcp import FastMCP
    from oto_mcp.tools import instagram_meta

    m = FastMCP("t")
    instagram_meta.register(m)
    return m


def test_les_cinq_outils_gardent_exactement_leurs_noms(env):
    """La liste EN DUR, pas dérivée du module : c'est le contrat qu'un agent déjà
    écrit tient pour acquis. Un renommage, un oubli ou un ajout se voit ici."""
    servis = sorted(t.name for t in asyncio.run(_serveur().list_tools()))
    assert servis == _ATTENDUS


def test_tous_les_outils_sont_dans_le_namespace_du_connecteur(env):
    """Un outil qui sortirait du préfixe échapperait au gate d'activation du
    connecteur — en silence."""
    from oto_mcp import tool_visibility as tv

    for t in asyncio.run(_serveur().list_tools()):
        assert tv.namespace_of(t.name) == CONNECTEUR


def test_un_outil_rend_le_brut_de_meta(env):
    """« Expose le brut » : ce que Meta rend est ce que l'appelant reçoit. Une
    recomposition maison deviendrait un contrat à tenir alors que Meta fait déjà
    évoluer le sien."""
    env.coffre.poser("jeton-vivant", {"user_id": "1784100",
                                      "expires_at": _dans(59),
                                      "connected_at": _dans(-1)})
    brut = {"username": "compte-de-test", "followers_count": 1234,
            "champ_inconnu_de_nous": "gardé"}
    client = MagicMock()
    client.get_profile = MagicMock(return_value=brut)
    env.coeur.InstagramClient = MagicMock(return_value=client)

    outil = asyncio.run(_serveur().get_tool("instagram_meta_get_profile")).fn
    assert asyncio.run(outil()) == brut
    jeton, user_id = env.coeur.InstagramClient.call_args.args
    assert (jeton, user_id) == ("jeton-vivant", "1784100")
    assert env.coeur.InstagramClient.call_args.kwargs["renew"] is not None


def test_un_outil_passe_ses_arguments_tels_quels(env):
    env.coffre.poser("jeton-vivant", {"user_id": "1784100",
                                      "expires_at": _dans(59),
                                      "connected_at": _dans(-1)})
    client = MagicMock()
    client.get_recent_media = MagicMock(return_value=[])
    env.coeur.InstagramClient = MagicMock(return_value=client)

    outil = asyncio.run(_serveur().get_tool("instagram_meta_get_recent_media")).fn
    asyncio.run(outil(limit=25))
    client.get_recent_media.assert_called_once_with(25)


def test_un_outil_appele_sur_une_autorisation_expiree_dit_la_date(env):
    """Le refus traverse la frontière de l'outil SANS être retraduit en panne : un
    agent qui lit « réessaie » réessaiera, et rien ne peut aboutir."""
    env.coffre.poser("jeton-mort", {"user_id": "1784100",
                                    "expires_at": "2026-07-08T09:00:00Z"})
    outil = asyncio.run(_serveur().get_tool("instagram_meta_get_profile")).fn
    with pytest.raises(Exception) as e:
        asyncio.run(outil())
    assert "8 juillet 2026" in str(e.value)


def test_un_outil_sans_coeur_installe_refuse_en_le_disant(env, monkeypatch):
    """Le connecteur reste MONTÉ quand oto-core est trop ancien : visible,
    sélectionnable, et il refuse en nommant ce qui manque. Un connecteur qui
    disparaît du catalogue ne se remarque pas et ne s'explique pas."""
    monkeypatch.delitem(sys.modules, "oto.tools.instagram_meta")
    monkeypatch.setattr(
        "importlib.import_module",
        lambda nom, *a, **k: (_ for _ in ()).throw(ImportError("absent")))
    with pytest.raises(RuntimeError) as e:
        env.auth._coeur()
    assert "oto-core" in str(e.value) and "operator" in str(e.value)


# ── Le secret d'application ne se relit pas ─────────────────────────────────

def test_le_secret_de_l_application_ne_ressort_pas_de_la_console_admin():
    """La table `connector_settings` n'avait porté jusqu'ici que des réglages
    PUBLICS par conception (les coordonnées de l'application Planity, que tout
    navigateur reçoit) — et `op="list"` rendait donc toute valeur telle quelle.
    `app_secret` est le premier VRAI secret qu'on y range, et cet appelant-là est
    souvent un agent : sa réponse finit dans un transcript.

    La présence est rendue, la valeur non — même tronquée. Savoir qu'une clé est
    posée est ce qu'il faut pour diagnostiquer ; la relire n'apporte rien à qui
    l'a posée."""
    from oto_mcp.capabilities import platform_connectors as pc

    lignes = pc._sans_les_secrets([
        {"connector": CONNECTEUR, "key": "app_id", "value": "app-id-fictif"},
        {"connector": CONNECTEUR, "key": "app_secret", "value": "secret-fictif"},
        {"connector": CONNECTEUR, "key": "app_secret", "value": ""},
    ])
    assert lignes[0]["value"] == "app-id-fictif"      # l'App ID n'est pas un secret
    assert "secret-fictif" not in str(lignes)
    assert lignes[1]["value"] and "set" in lignes[1]["value"]
    assert lignes[2]["value"] == ""                    # absente reste absente


def test_la_convention_de_nommage_du_secret_est_tenue():
    """La redaction va par SUFFIXE, pas par liste de clés — une liste indexée par
    nom serait à tenir à jour au prochain connecteur, et l'oubli ne casserait rien :
    il publierait le secret. Ce test est la contrepartie : la convention doit être
    respectée par les clés que CE connecteur pose."""
    from oto_mcp.auth import instagram_meta as ig_auth
    from oto_mcp.capabilities import platform_connectors as pc

    secretes = [k for k in ig_auth._REGLAGES if "secret" in k]
    assert secretes == ["app_secret"]
    for k in secretes:
        assert k.endswith(pc._SUFFIXE_SECRET), (
            f"{k} porte un secret sans le suffixe `{pc._SUFFIXE_SECRET}` : "
            "la console admin le rendrait en clair.")
