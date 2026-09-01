"""Les droits SERVIS sur les procédures disent la même chose que les gardes (#695).

#695 a dissocié deux verbes qui partageaient une règle : **écrire** (et restaurer) une
procédure d'équipe demande d'en être MEMBRE, **supprimer** demande d'en être le CHEF.
Le drapeau que l'écran lit, lui, n'avait pas suivi : `can_edit` rendait le critère de
l'administration, donc `false` à une opératrice qui pouvait écrire. Une porte fermée à
tort — et l'élargir en aurait ouvert une autre (le bouton de suppression) que le
serveur refuse. Le sens devait se DÉDOUBLER.

Ce fichier tient les trois choses qui rendent la réparation vérifiable :

1. **Les deux témoins.** Une membre non-cheffe voit le droit d'écrire à vrai et celui
   de supprimer à faux ; un chef voit les deux à vrai. Et pas seulement dans le
   bundle : le geste est ENSUITE tenté pour de vrai, et son issue doit donner raison
   au drapeau. Un drapeau qu'on ne confronte pas au geste n'est qu'une opinion.
2. **Le cliquet d'appariement.** Le drapeau servi et le refus ne peuvent pas diverger
   parce qu'ils sont la MÊME fonction : le bundle exécute la règle d'autz déclarée par
   la capacité. Le cliquet le vérifie là où ça compte — en comparant, pour chaque
   acteur, le drapeau à ce que fait la règle déclarée. C'est le défaut qu'on répare ;
   le reconstruire un cran plus loin (une copie du critère dans le handler du bundle)
   se verrait ici.
3. **Les deux surfaces disent la même chose.** Équipe et org servent les mêmes deux
   noms de champ. Sinon « qui peut annoter » redevient une propriété de la page —
   exactement ce que #695 vient de retirer du transport.

Contre un vrai PostgreSQL et par le CHEMIN SERVI (autz déclarée puis handler) : ce
qu'on teste est une règle d'autorisation, et un stub ne prouve rien sur une règle.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from oto_mcp.capabilities._types import AuthzDenied, RawCtx


# ── Le monde : une MEMBRE d'équipe qui n'est ni cheffe ni admin d'org ────────

@pytest.fixture(scope="module")
def monde(pg_dsn):
    """Base jetable à nous (même recette que `test_procedure_paliers_681`).

    Le personnage qui rend ce lot vérifiable est `u-membre` : membre de l'équipe,
    membre de l'org, chef de rien. C'est elle que l'écran fermait."""
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_695_" + uuid.uuid4().hex[:8]
    root = psycopg.connect(pg_dsn, autocommit=True)
    root.execute(f'CREATE DATABASE "{nom}"')
    url_avant, pool_avant = os.environ.get("DATABASE_URL"), dbconn._pool
    os.environ["DATABASE_URL"] = pg_dsn.rsplit("/", 1)[0] + "/" + nom
    dbconn._pool = None
    try:
        from oto_mcp import group_store, org_store
        from oto_mcp.db import init_db
        init_db()
        org = org_store.create_org("Acme", created_by="u-admin")
        org_store.add_org_member(org, "u-admin", "org_admin")
        org_store.add_org_member(org, "u-chef", "org_member")
        org_store.add_org_member(org, "u-membre", "org_member")
        equipe = group_store.create_group(org, "Compta")
        group_store.add_group_member(equipe, "u-chef", "group_admin")
        group_store.add_group_member(equipe, "u-membre", "group_member")
        for sub in ("u-admin", "u-chef", "u-membre"):
            org_store.set_active_org(sub, org)
        yield {"org": org, "equipe": equipe}
    finally:
        if dbconn._pool is not None:
            dbconn._pool.close()
        dbconn._pool = pool_avant
        if url_avant is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = url_avant
        root.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')
        root.close()


def _cap(key: str):
    from oto_mcp.capabilities.registry import CAPABILITIES
    return next(c for c in CAPABILITIES if c.key == key)


def _appel_cap(key: str, sub: str, **args):
    """UN appel par le chemin servi : autz DÉCLARÉE, puis handler. Le point de
    couture — c'est la règle d'autz, et elle seule, qui ferme ou ouvre."""
    cap = _cap(key)
    inp = cap.Input(**args)
    out = cap.handler(cap.authz(RawCtx(sub=sub), inp), inp)
    return asyncio.run(out) if asyncio.iscoroutine(out) else out


def _refus(key: str, sub: str, **args) -> AuthzDenied:
    with pytest.raises(AuthzDenied) as e:
        _appel_cap(key, sub, **args)
    return e.value


_CORPS = ("> **Self-improvement digest** — jamais déroulée.\n\n"
          "# Relance client\n\n```\n[Début] --> [Fin]\n```\n\nÉtapes.\n")


# ── 1. Les deux témoins ─────────────────────────────────────────────────────

def test_une_membre_non_cheffe_ecrit_et_ne_supprime_pas(monde):
    """Le lot en une assertion, côté ÉCRAN.

    La prémisse est AFFIRMÉE d'abord : sans elle, ce test passerait pour la mauvaise
    raison (une non-membre verrait aussi `can_delete_instructions: false`, et le
    drapeau d'écriture serait faux sans rien prouver du dédoublement)."""
    from oto_mcp import group_store, roles
    equipe = monde["equipe"]
    assert group_store.get_group_role(equipe, "u-membre") == "group_member"
    assert not roles.can_admin_group("u-membre", equipe), (
        "sans une membre NON cheffe, il n'y a pas deux droits à distinguer")

    bundle = _appel_cap("group.instruction.list", "u-membre", group_id=equipe)
    assert bundle["can_write_instructions"] is True
    assert bundle["can_delete_instructions"] is False

    # …et le geste donne raison au drapeau, des deux côtés.
    ecrit = _appel_cap("group.instruction.set", "u-membre", group_id=equipe,
                       slug="relance-client", body_md=_CORPS)
    assert ecrit["version"] == 1
    assert _refus("group.instruction.delete", "u-membre", group_id=equipe,
                  slug="relance-client").status == 403
    # Le refus n'a rien emporté : la procédure qu'elle vient d'écrire est toujours là.
    assert _appel_cap("group.instruction.get", "u-membre", group_id=equipe,
                      slug="relance-client")["version"] == 1


def test_le_chef_dequipe_a_les_deux_droits(monde):
    """L'autre bout : le chef voit vrai des deux côtés, et supprime pour de bon."""
    equipe = monde["equipe"]
    bundle = _appel_cap("group.instruction.list", "u-chef", group_id=equipe)
    assert bundle["can_write_instructions"] is True
    assert bundle["can_delete_instructions"] is True

    _appel_cap("group.instruction.set", "u-chef", group_id=equipe,
               slug="a-jeter", body_md=_CORPS)
    assert _appel_cap("group.instruction.delete", "u-chef", group_id=equipe,
                      slug="a-jeter")["deleted"] is True


def test_can_edit_na_pas_change_de_sens(monde):
    """`can_edit` reste le droit d'ADMINISTRER l'équipe — on a ajouté à côté, pas
    redéfini. Un intégrateur qui le lit aujourd'hui lit la même chose demain.

    C'est la moitié du lot qu'aucun autre test ne couvre : élargir sa valeur aurait
    fait passer les deux témoins ci-dessus et cassé, en silence, tout écran qui en
    dérive un bouton de suppression."""
    equipe = monde["equipe"]
    assert _appel_cap("group.instruction.list", "u-membre",
                      group_id=equipe)["can_edit"] is False
    assert _appel_cap("group.instruction.list", "u-chef",
                      group_id=equipe)["can_edit"] is True


# ── 2. Le cliquet : annoncé == refusé, parce que c'est la même fonction ──────

def _drapeaux_declares(module) -> dict:
    return dict(module._DROITS_SERVIS)


@pytest.mark.parametrize("sub", ["u-membre", "u-chef", "u-admin"])
def test_chaque_drapeau_dequipe_est_la_regle_qui_refuse(monde, sub):
    """Le cliquet d'appariement, au grain ÉQUIPE.

    Pour chaque droit annoncé : le drapeau servi doit valoir EXACTEMENT ce que fait
    la règle d'autz **déclarée par la capacité qu'il nomme**, jouée sur un `Input`
    réel de cette capacité. Recopier le critère dans le handler du bundle passerait
    tant que les deux copies s'accordent — et c'est précisément le jour où elles ne
    s'accordent plus qui compte : déplacer une garde ici fait bouger le drapeau, ou
    fait rougir ce test."""
    from oto_mcp.capabilities.groups import guide as gg
    equipe = monde["equipe"]
    entrees = {
        "group.instruction.set": dict(group_id=equipe, slug="x", body_md=_CORPS),
        "group.instruction.delete": dict(group_id=equipe, slug="x"),
    }
    bundle = _appel_cap("group.instruction.list", sub, group_id=equipe)

    vus = 0
    for nom, cle in _drapeaux_declares(gg).items():
        assert nom in gg.GroupInstructionsBundle.model_fields, (
            f"`{nom}` est calculé mais le modèle SERVI ne le déclare pas — un droit "
            f"que l'OpenAPI ne porte pas est un droit qu'aucun front ne lira")
        cap = _cap(cle)
        try:
            cap.authz(RawCtx(sub=sub), cap.Input(**entrees[cle]))
            passe = True
        except AuthzDenied:
            passe = False
        assert bundle[nom] is passe, (
            f"`{nom}` annonce {bundle[nom]} et la règle de `{cle}` fait {passe} : "
            f"l'écran et le serveur ne disent plus la même chose")
        vus += 1
    # Le cliquet porte SA PROPRE garde : sans ça, vider la table des droits le rendrait
    # inerte en silence — le seul mode de panne qu'un cliquet ne peut pas signaler.
    assert vus >= 2, ("moins de deux droits servis : le dédoublement a disparu, et "
                      "avec lui tout ce que ce fichier surveille")


@pytest.mark.parametrize("sub", ["u-membre", "u-admin"])
def test_chaque_drapeau_dorg_est_la_regle_qui_refuse(monde, sub):
    """Le même cliquet au grain ORG. Les deux droits y valent la même chose
    aujourd'hui (le palier org n'a pas été redécoupé) — ce n'est pas une raison de les
    fondre : c'est cette identité-là qui doit rester VÉRIFIÉE plutôt que supposée."""
    from oto_mcp.capabilities.orgs import instructions as oi
    org = monde["org"]
    entrees = {
        "org.instruction.set": dict(slug="x", body_md=_CORPS, org=org),
        "org.instruction.delete": dict(slug="x", org=org),
    }
    bundle = _appel_cap("org.instruction.list", sub)
    assert bundle["org_id"] == org

    vus = 0
    for nom, cle in _drapeaux_declares(oi).items():
        assert nom in oi.InstructionsBundle.model_fields, (
            f"`{nom}` est calculé mais le modèle SERVI ne le déclare pas")
        cap = _cap(cle)
        try:
            cap.authz(RawCtx(sub=sub), cap.Input(**entrees[cle]))
            passe = True
        except AuthzDenied:
            passe = False
        assert bundle[nom] is passe
        vus += 1
    assert vus >= 2


def test_les_deux_surfaces_servent_les_memes_noms():
    """« Qui peut annoter » ne doit pas redevenir une propriété de la page.

    Un front qui affiche une procédure d'org et une procédure d'équipe lit le même
    champ des deux côtés. Ajouter un droit d'un seul côté le forcerait à savoir sur
    quelle page il est pour savoir quoi lire — la divergence entre deux chemins, qui
    ne se paie jamais en refus visible."""
    from oto_mcp.capabilities.groups import guide as gg
    from oto_mcp.capabilities.orgs import instructions as oi
    assert set(_drapeaux_declares(gg)) == set(_drapeaux_declares(oi))
    assert set(_drapeaux_declares(gg)) == {"can_write_instructions",
                                           "can_delete_instructions"}


_VERBES_ATTENDUS = {"can_write_instructions": {"PUT", "POST", "PATCH"},
                    "can_delete_instructions": {"DELETE"}}


@pytest.mark.parametrize("module", ["groups.guide", "orgs.instructions"])
def test_chaque_drapeau_nomme_la_capacite_de_SON_verbe(module):
    """Le cliquet ne peut pas vérifier seul qu'un drapeau nomme la BONNE capacité :
    il compare le drapeau à la règle que le drapeau désigne, donc une table qui se
    trompe de capacité reste cohérente avec elle-même.

    Ce qui l'ancre, c'est le VERBE SERVI : le droit d'écrire doit nommer une capacité
    montée en écriture, celui de supprimer une capacité montée en `DELETE`. Faire
    pointer les deux vers `.set` — la correction tentante, celle qui « débloque »
    l'écran d'un seul geste — afficherait un bouton de suppression que le serveur
    refuse, et c'est ici que ça se voit."""
    import importlib
    mod = importlib.import_module(f"oto_mcp.capabilities.{module}")
    for nom, cle in _drapeaux_declares(mod).items():
        verbes = {b.verb for b in _cap(cle).rest_bindings()}
        assert verbes, f"`{cle}` n'a aucune face REST : `{nom}` n'annonce rien de servi"
        assert verbes <= _VERBES_ATTENDUS[nom], (
            f"`{nom}` nomme `{cle}`, monté en {sorted(verbes)} — un droit annoncé doit "
            f"nommer la capacité de SON verbe, sinon l'écran ouvre un geste que le "
            f"serveur refuse")


def test_le_bundle_dorg_sans_org_active_porte_TOUS_les_droits_a_faux(monde):
    """Sans org active, le bundle est un 200 tout vide — et ses droits doivent y être
    PRÉSENTS et faux, pas absents. Un champ manquant se lit `undefined` : un front le
    prend pour « pas le droit » sur une branche et pour « peut-être » sur l'autre."""
    from oto_mcp.capabilities.orgs import instructions as oi
    bundle = _appel_cap("org.instruction.list", "u-sans-org")
    assert bundle["org_id"] is None
    for nom in _drapeaux_declares(oi):
        assert bundle[nom] is False, f"`{nom}` absent ou vrai sans org active"


def test_un_droit_qui_nomme_une_capacite_disparue_leve():
    """La sonde ne rend jamais `False` sur une clé inconnue : elle lève.

    Rendre faux serait ressusciter le défaut d'origine — une porte fermée à tort, que
    rien ne signale. Une capacité renommée doit casser bruyamment au premier appel."""
    from oto_mcp.capabilities._authz import capacite_autorise
    with pytest.raises(KeyError):
        capacite_autorise("group.instruction.disparue", "u-chef", group_id=1)
