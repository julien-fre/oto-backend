"""Une clé REFUSÉE par le service amont n'a aucun état : la carte reste verte.

Signal #541. La clé Linear d'une org est refusée par Linear lui-même sur l'appel le
plus simple (`op=viewer`) — « AUTHENTICATION_ERROR: Authentication required, not
authenticated ». La clé est invalide ou révoquée, ce n'est pas une forme de requête.
Or `linear` déclare `verifiable: true`, la sonde `oto_instance op=verify` existe et
ÉCRIT déjà son verdict (`meta.health_ko` + `meta.health_reason`), et pourtant :

1. **Personne ne relit ce verdict là où on regarde.** `connectors/readiness.diagnose`
   est le seam qui rend `ready` / `not_ready` / `next_step` de la carte connecteur ;
   il connaît trois couches (option, clé, geste restant) et aucune ne demande si la
   clé qui résout a été REJETÉE. Une clé morte donne donc `ready: true`.
2. **Le verdict ne s'enregistre même pas là où il tombe.** `oto_instance op=verify`
   sans `level` (le geste par défaut) résout la cascade ; si c'est une clé d'ORG qui
   répond — le seul palier possible pour `linear`, `byo_org` only — la cible de santé
   vaut `None` et rien n'est écrit. L'utilisateur voit `ok:false`, puis retourne sur
   une carte verte.
3. **Au moment de l'appel, le message est celui de l'amont, brut.** `tools/linear.py`
   porte une branche 401/403 soignée qui nomme le rejet de clé — inatteignable :
   Linear répond `AUTHENTICATION_ERROR` dans un `errors[]` GraphQL sous HTTP 200, donc
   `LinearGraphQLError` est levée avant tout `raise_for_upstream`, et c'est la branche
   générique qui répond en recopiant la phrase du fournisseur.

Les trois sont exercés ici. Aucune base : seams stubés.
"""
from __future__ import annotations

import pytest

from oto_mcp.connectors import readiness


@pytest.fixture
def seams(monkeypatch):
    """Une org dont la clé `linear` est POSÉE au palier org, et REJETÉE par l'amont.

    Toutes les couches que `diagnose` interroge sont vertes : pas d'option payante,
    la cascade résout (`mode="org"`), aucun geste en attente. C'est exactement l'état
    de l'org du signalement."""
    from oto_mcp import access, providers, status_hints

    rec = {"health": None, "lu": []}

    monkeypatch.setattr(access, "paid_option_for", lambda c: None)
    monkeypatch.setattr(access, "option_open", lambda sub, c, org=None: True)
    monkeypatch.setattr(access, "credential_mode_for",
                        lambda sub, c, org=None, group=None: "org")
    monkeypatch.setattr(providers, "credential_provider", lambda c: c)
    monkeypatch.setattr(status_hints, "pending_action",
                        lambda c, sub, org, group, st: None)

    def _rejection(sub, provider, *, org=None, group=None):
        rec["lu"].append((sub, provider, org))
        return rec["health"]

    monkeypatch.setattr(access, "credential_rejection_for", _rejection, raising=False)
    return rec


def test_une_cle_rejetee_par_lamont_laisse_la_carte_verte(seams):
    """Le fait mesuré : verdict de rejet en base, `ready: true` sur la carte.

    `oto_instance op=verify level=org` a déjà écrit le refus de Linear sur la ligne
    de coffre de l'org. La carte connecteur, elle, ne le lit pas — donc l'utilisateur
    n'apprend le rejet qu'au premier appel d'outil, sous la forme du message brut du
    fournisseur."""
    seams["health"] = "AUTHENTICATION_ERROR: Authentication required, not authenticated"
    diag = readiness.diagnose("u1", "linear", org=7, group=None)
    assert diag is not None, "une clé rejetée par l'amont rend encore `ready: true`"
    assert diag.reason == readiness.CREDENTIAL_REJECTED
    # Le geste doit nommer les DEUX sorties : reposer la clé, et lever le constat.
    assert "verify" in diag.next_step


def test_une_cle_saine_ne_declenche_rien(seams):
    """Le contrefactuel : sans rejet enregistré, le diagnostic reste muet.

    Sans lui, un test qui exige un refus ne prouve pas que c'est le REJET qui le
    déclenche — n'importe quelle couche cassée rendrait le premier test vert."""
    seams["health"] = None
    assert readiness.diagnose("u1", "linear", org=7, group=None) is None


def test_le_rejet_ne_masque_pas_une_couche_plus_englobante(seams):
    """L'ordre des couches est le contrat : la PREMIÈRE qui manque nomme le refus.

    Une clé absente reste `no_credential` — dire « rejetée » d'une clé qui n'existe
    pas enverrait reposer une clé qu'on n'a jamais posée."""
    from oto_mcp import access
    seams["health"] = "peu importe"
    import pytest as _p
    with _p.MonkeyPatch.context() as mp:
        mp.setattr(access, "credential_mode_for",
                   lambda sub, c, org=None, group=None: "forbidden")
        diag = readiness.diagnose("u1", "linear", org=7, group=None)
    assert diag is not None and diag.reason == readiness.NO_CREDENTIAL


def test_verify_niveau_auto_enregistre_le_verdict_sur_la_cle_dorg(monkeypatch):
    """Le geste par défaut (`op=verify` sans `level`) doit laisser une trace.

    La cible de santé n'était posée que si la clé EFFECTIVE était celle du membre :
    pour un connecteur `byo_org` only comme `linear`, la cascade résout au palier org
    et rien n'était écrit. L'utilisateur lisait `ok:false` sur la sonde, et la carte
    restait verte derrière lui."""
    from oto_mcp.capabilities.connectors import verify as V
    from oto_mcp.capabilities._types import ResolvedCtx

    class _RC:
        mode, entity_type, entity_id, account = "org", "org", "7", ""
        fields, config = {"key": "lin_xxx"}, {}

    monkeypatch.setattr(V.access, "resolve_credential",
                        lambda p, want="auto", sub=None, emit_on_failure=True: _RC())
    _, _, scope, instance, cible = V._fields_config_scope(
        ResolvedCtx(sub="u1", org_id=7), V.VerifyInput(provider="linear", level="auto"))
    assert scope is not None, "un verdict sur une clé d'org ne s'enregistre nulle part"
    assert scope[:2] == ("org", "7")
    assert instance["level"] == "org"


def test_une_cle_partagee_au_dela_de_lorg_nest_pas_flaguee(monkeypatch):
    """La contrepartie, et la raison pour laquelle la cible n'est pas « la ligne testée ».

    Une clé de PLATEFORME sert tout le monde : la sonde d'un seul membre — un
    hoquet réseau chez lui — ne doit pas la peindre en rouge pour les autres. Même
    raisonnement pour la clé d'un TENANT, partagée par toutes les orgs du partenaire."""
    from oto_mcp.capabilities.connectors import verify as V
    from oto_mcp.capabilities._types import ResolvedCtx

    class _RC:
        mode, entity_type, entity_id, account = "platform", "platform", "oto", ""
        fields, config = {"key": "k"}, {}

    monkeypatch.setattr(V.access, "resolve_credential",
                        lambda p, want="auto", sub=None, emit_on_failure=True: _RC())
    _, _, scope, _, _ = V._fields_config_scope(
        ResolvedCtx(sub="u1", org_id=7), V.VerifyInput(provider="linear", level="auto"))
    assert scope is None


def test_linear_nomme_le_rejet_de_cle_au_lieu_de_recopier_lamont():
    """Linear refuse en GraphQL SOUS HTTP 200 — la branche 401/403 ne voit jamais rien.

    `_upstream_message` porte depuis toujours une phrase qui nomme le rejet de clé et
    dit où la reposer ; elle est gardée par `isinstance(e, UpstreamHTTPError)`, or
    `_execute` lève `LinearGraphQLError` dès que le corps porte un `errors[]`, ce que
    Linear fait pour l'authentification. Le message servi était donc la phrase du
    fournisseur, recopiée : « Linear a refusé la requête : linear GraphQL error
    (AUTHENTICATION_ERROR): Authentication required, not authenticated »."""
    from oto.tools.linear import LinearGraphQLError

    from oto_mcp.tools.linear import _upstream_message

    e = LinearGraphQLError(
        [{"message": "Authentication required, not authenticated",
          "extensions": {"code": "AUTHENTICATION_ERROR"}}], status_code=200)
    msg = _upstream_message(e)
    assert "rejeté la clé" in msg, msg
    assert "linear.app/settings/api" in msg


def test_une_autre_erreur_graphql_reste_relayee():
    """Le contrefactuel : seule l'authentification change de voix.

    Une erreur de requête (champ inconnu…) doit continuer à remonter le texte de
    Linear — c'est lui qui dit ce qui ne va pas, et le reformuler le perdrait."""
    from oto.tools.linear import LinearGraphQLError

    from oto_mcp.tools.linear import _upstream_message

    e = LinearGraphQLError(
        [{"message": "Field 'nope' doesn't exist",
          "extensions": {"code": "INVALID_INPUT"}}], status_code=200)
    msg = _upstream_message(e)
    assert "rejeté la clé" not in msg
    assert "nope" in msg


def test_la_sonde_enregistre_le_message_nomme_pas_celui_de_lamont(monkeypatch):
    """La chaîne complète : ce que la SONDE lève devient le texte lu sur la carte.

    `_record_health` persiste `str(exception)` en `meta.health_reason`, et
    `readiness.diagnose` recopie ce motif dans son `next_step`. Une sonde qui laisse
    remonter l'exception nue du client fait donc afficher la phrase du fournisseur à
    l'endroit même où l'outil, lui, nomme le rejet de clé — deux voix pour un fait."""
    from oto.tools.linear import LinearGraphQLError
    import oto.tools.linear.client as lc

    from oto_mcp.mcp_errors import McpError
    from oto_mcp.tools.linear import _verify

    class _Client:
        def __init__(self, api_key=None):
            pass

        def get_viewer(self):
            raise LinearGraphQLError(
                [{"message": "Authentication required, not authenticated",
                  "extensions": {"code": "AUTHENTICATION_ERROR"}}], status_code=200)

    monkeypatch.setattr(lc, "LinearClient", _Client)
    with pytest.raises(McpError) as ei:
        _verify({"key": "lin_revoquee"})
    assert "rejeté la clé" in ei.value.error.message


def test_toute_cause_de_liste_vide_est_nommee_dans_la_description_servie():
    """Une description d'outil est une INSTRUCTION relue à chaque appel — et
    `connectors.identities` PROMET d'énumérer les causes d'une liste vide.

    Elle relaie `diagnose().reason` tel quel : toute couche ajoutée à `readiness`
    apparaît donc dans `reason` sans que personne y touche. Une cause servie que la
    description ne nomme pas, c'est un contrat qui ment — l'appelant lit la liste
    comme exhaustive et conclut au bug. Dérivé des constantes du module, pour que le
    prochain jeton ne puisse pas s'échapper en silence.

    `pending_step` est la seule exception, et elle est explicite : cette surface le
    re-nomme `no_identity_connected` (cf. `_empty_reason`)."""
    from oto_mcp.capabilities.connectors.identities import CAPABILITIES_DOC_LIST

    jetons = {v for k, v in vars(readiness).items()
              if k.isupper() and not k.startswith("_") and isinstance(v, str)}
    servis = jetons - {readiness.PENDING_STEP}
    manquants = sorted(j for j in servis if j not in CAPABILITIES_DOC_LIST)
    assert manquants == [], f"causes servies mais non nommées : {manquants}"
