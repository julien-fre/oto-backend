"""Le palier ÉQUIPE d'une procédure, et son DÉPLACEMENT d'un palier à l'autre (#681).

**Ce fichier existe parce que la surface qu'il couvre n'a AUCUN usage réel.** Relevé
en base de production le 31/08/2026 : 137 procédures, 787 révisions, **toutes
`owner_type='org'`** — pas une seule ligne d'équipe. Le palier équipe était donc du
code que rien n'exerçait : des tests verts sur une surface que personne n'emprunte
sont le terrain du défaut silencieux, et c'est exactement là que la convergence des
deux stores allait passer.

Trois partis pris, qui expliquent pourquoi ce fichier est ce qu'il est :

1. **Contre un vrai PostgreSQL.** Ce lot ne change pas des branches Python, il change
   la CLÉ sur laquelle des requêtes filtrent et sur laquelle un `ON CONFLICT` arbitre.
   Aucun stub ne prouve quoi que ce soit là-dessus.
2. **Sur le CHEMIN SERVI.** Les gestes passent par la capacité `org.procedure.console`
   avec sa règle d'autz DÉCLARÉE, jouée sur un `RawCtx` — pas par un appel direct au
   store. Un test qui vérifie une fonction ne dit rien de son appelant, et c'est
   l'appelant qui était fermé.
3. **Le déplacement est le critère de « fini ».** Ouvrir le palier laisserait
   l'opérateur capable de créer des procédures neuves et toujours incapable d'annoter
   la sienne — celle qui a 26 versions, des slots et un projet qui la référence.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from oto_mcp.capabilities._types import AuthzDenied, RawCtx


# ── Le monde : une org, une équipe, et surtout un CHEF D'ÉQUIPE QUI N'EST PAS
#    ADMIN DE L'ORG — le seul personnage qui rende ce lot vérifiable ─────────

@pytest.fixture(scope="module")
def monde(pg_dsn):
    """Une base JETABLE à nous, bootée par le vrai `init_db`, peuplée d'un monde minimal.

    ⚠️ Base à part et pas celle du conteneur partagé (`pg_dsn` est session-scopé) :
    un boot complet y laisse ~67 tables et leurs FK, et les tests qui recréent deux
    tables autonomes n'y arrivent plus. Même recette que `test_boot_order_replay`."""
    psycopg = pytest.importorskip("psycopg")
    from oto_mcp.db import _conn as dbconn

    nom = "oto_681_" + uuid.uuid4().hex[:8]
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
        autre_org = org_store.create_org("Autre", created_by="u-admin")
        org_store.add_org_member(org, "u-admin", "org_admin")
        org_store.add_org_member(org, "u-chef", "org_member")
        org_store.add_org_member(org, "u-membre", "org_member")
        equipe = group_store.create_group(org, "Compta")
        group_store.add_group_member(equipe, "u-chef", "group_admin")
        group_store.add_group_member(equipe, "u-membre", "group_member")
        for sub in ("u-admin", "u-chef", "u-membre"):
            org_store.set_active_org(sub, org)
        group_store.set_active_group("u-chef", equipe)
        group_store.set_active_group("u-membre", equipe)
        yield {"org": org, "autre_org": autre_org, "equipe": equipe}
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


def _console():
    from oto_mcp.capabilities.registry import CAPABILITIES
    return next(c for c in CAPABILITIES if c.key == "org.procedure.console")


def _appel(sub: str, **args):
    """UN appel d'`oto_procedure` par le chemin servi : autz DÉCLARÉE puis handler.

    C'est le point de couture. Câbler le store et vérifier le store laisserait la
    règle d'autz — le seul endroit qui était fermé — hors du champ du test."""
    cap = _console()
    inp = cap.Input(**args)
    ctx = cap.authz(RawCtx(sub=sub), inp)       # ← la porte
    out = cap.handler(ctx, inp)
    return asyncio.run(out) if asyncio.iscoroutine(out) else out


def _appel_cap(key: str, sub: str, **args):
    """Le même chemin servi, pour une capacité NOMMÉE — les faces REST du palier
    équipe (`group.instruction.*`) sont des capacités comme les autres."""
    from oto_mcp.capabilities.registry import CAPABILITIES
    cap = next(c for c in CAPABILITIES if c.key == key)
    inp = cap.Input(**args)
    out = cap.handler(cap.authz(RawCtx(sub=sub), inp), inp)
    return asyncio.run(out) if asyncio.iscoroutine(out) else out


_CORPS = ("> **Self-improvement digest** — jamais déroulée.\n\n"
          "# Clôture mensuelle\n\n```\n[Début] --> [Fin]\n```\n\nÉtapes.\n")


# ── 1. Le témoin : le geste que le lot débloque ────────────────────────────

def test_le_chef_dequipe_ecrit_une_procedure_sans_etre_admin_de_lorg(monde):
    """Le lot en une assertion.

    `u-chef` n'est **pas** org_admin (`org_member` + `group_admin`) : avant #681,
    `oto_procedure op=set` lui répondait « Réservé à un org_admin de ton org active »
    quoi qu'il tente. Le palier org lui reste d'ailleurs fermé — deuxième moitié du
    test, sans laquelle la première prouverait seulement qu'on a tout ouvert."""
    from oto_mcp import roles
    assert not roles.is_org_admin("u-chef", monde["org"])
    assert roles.can_admin_group("u-chef", monde["equipe"])

    out = _appel("u-chef", op="set", scope="group", slug="cloture-mensuelle",
                 body_md=_CORPS, title="Clôture mensuelle")
    assert out["ok"] and out["version"] == 1
    # La clé d'identité change de NOM avec le palier (jamais une clé nulle).
    assert out["group_id"] == monde["equipe"] and out["scope"] == "group"
    assert "org_id" not in out

    with pytest.raises(AuthzDenied) as refus:
        _appel("u-chef", op="set", slug="au-niveau-org", body_md=_CORPS)
    assert refus.value.status == 403


def test_ce_quil_ecrit_il_le_relit_et_le_supprime(monde):
    """Écrire chez soi et ne pas s'y retrouver serait le défaut #248 rejoué."""
    _appel("u-chef", op="set", scope="group", slug="relance-clients", body_md=_CORPS)
    lu = _appel("u-chef", op="get", scope="group", slug="relance-clients")
    assert lu["group_id"] == monde["equipe"] and lu["body_md"] == _CORPS.strip()
    cat = _appel("u-chef", op="list", scope="group")
    assert "relance-clients" in {g["slug"] for g in cat["guides"]}

    out = _appel("u-chef", op="delete", scope="group", slug="relance-clients")
    assert out["deleted"] and out["group_id"] == monde["equipe"]
    assert _appel("u-chef", op="list", scope="group")["guides"] == [
        g for g in cat["guides"] if g["slug"] != "relance-clients"]


def test_un_membre_de_lequipe_annote_la_procedure_quil_deroule(monde):
    """Le geste que le lot rend possible, et le seul qui ferme la boucle promise.

    `u-membre` n'est **ni** org_admin **ni** chef d'équipe : il DÉROULE la procédure.
    Réserver l'écriture au chef réservait l'apprentissage à qui n'exécute pas — et
    coûtait, en vrai, une élévation de droits sans rapport (le rôle de chef emporte les
    clés partagées de l'équipe) pour le seul motif d'annoter un mode d'emploi.

    Écrire est un geste de TRAVAIL, et il est **réversible** : chaque écriture crée une
    version de plus, et `from_version` restaure la précédente. C'est ce qui permet de
    l'ouvrir au membre sans ouvrir la suppression, qui, elle, emporte l'historique
    (`test_supprimer_une_procedure_dequipe_reste_au_chef`)."""
    from oto_mcp import roles
    assert not roles.is_org_admin("u-membre", monde["org"])
    assert not roles.can_admin_group("u-membre", monde["equipe"]), (
        "sans cette assertion, le test passerait pour la mauvaise raison — "
        "un chef qui écrit ne prouve rien de neuf")
    assert roles.can_read_group("u-membre", monde["equipe"])

    _appel("u-chef", op="set", scope="group", slug="annotable", body_md=_CORPS)
    out = _appel("u-membre", op="set", scope="group", slug="annotable",
                 body_md=_CORPS + "\nAppris au déroulé du jour : relancer avant 10 h.\n")
    assert out["ok"] and out["group_id"] == monde["equipe"] and out["scope"] == "group"
    # v2, pas v1 : il a annoté CELLE QUI EXISTE, il n'en a pas créé une à côté.
    assert out["version"] == 2
    assert "Appris au déroulé" in _appel(
        "u-membre", op="get", scope="group", slug="annotable")["body_md"]

    # …et sa mauvaise écriture se défait sans droit de plus : la réversibilité n'est pas
    # une promesse, c'est le chemin servi.
    assert _appel("u-membre", op="set", scope="group", slug="annotable",
                  from_version=1)["version"] == 3

    # Le palier ORG lui reste fermé — sans cette moitié, le test dirait seulement
    # qu'on a tout ouvert.
    with pytest.raises(AuthzDenied) as refus:
        _appel("u-membre", op="set", slug="au-niveau-org", body_md=_CORPS)
    assert refus.value.status == 403


def test_ecrire_dans_une_equipe_dont_on_nest_pas_membre_reste_refuse(monde):
    """« Membre » veut dire membre de l'ÉQUIPE PROPRIÉTAIRE, pas membre de l'org.

    `can_read_group` ne subsume pas l'appartenance à l'org : un salarié d'Acme qui n'est
    pas dans Compta n'annote pas les procédures de Compta. Abaisser la garde de `set`
    d'un cran ne devait pas la faire tomber d'un étage."""
    from oto_mcp import group_store, roles
    autre_equipe = group_store.create_group(monde["org"], "Paie")
    assert not roles.can_read_group("u-membre", autre_equipe)
    with pytest.raises(AuthzDenied) as refus:
        _appel("u-membre", op="set", scope="group", group=autre_equipe,
               slug="pas-chez-moi", body_md=_CORPS)
    assert refus.value.status == 403


def test_supprimer_une_procedure_dequipe_reste_au_chef(monde):
    """Le pendant, figé : `delete` n'est PAS un geste de travail.

    Il emporte la procédure **et tout son historique**, sans corbeille — rien ne le
    défait. C'est la raison pour laquelle `set` et `delete` cessent de partager une
    règle : ce n'est pas la surface qui décide du palier, c'est le VERBE. Remettre les
    deux sur la même règle rend ce test rouge, dans un sens ou dans l'autre."""
    from oto_mcp import org_store
    _appel("u-chef", op="set", scope="group", slug="destructible", body_md=_CORPS)

    with pytest.raises(AuthzDenied) as refus:
        _appel("u-membre", op="delete", scope="group", slug="destructible")
    assert refus.value.status == 403
    # Le refus tombe AVANT la suppression — l'historique est intact.
    assert org_store.get_instruction("group", monde["equipe"], "destructible")
    assert org_store.list_instruction_versions("group", monde["equipe"], "destructible")

    # …et le chef, lui, supprime.
    assert _appel("u-chef", op="delete", scope="group", slug="destructible")["deleted"]


def test_la_face_rest_dequipe_partage_le_meme_decoupage(monde):
    """Un geste, deux transports, une seule règle.

    `PUT /api/groups/{id}/instructions/{slug}` (le tableau de bord) et `oto_procedure
    op=set scope='group'` (l'agent) écrivent la MÊME procédure. Les laisser sur deux
    gardes ferait de « qui peut annoter » une propriété du transport — la forme de
    divergence qui se paie toujours en incident, jamais en refus visible."""
    from oto_mcp import org_store
    assert _appel_cap("group.instruction.set", "u-membre", group_id=monde["equipe"],
                      slug="par-le-tableau-de-bord", body_md=_CORPS)["set"]
    # Restaurer une version passée est une écriture de plus, pas une administration.
    _appel_cap("group.instruction.set", "u-membre", group_id=monde["equipe"],
               slug="par-le-tableau-de-bord", body_md=_CORPS + "\nv2\n")
    assert _appel_cap("group.instruction.revert", "u-membre", group_id=monde["equipe"],
                      slug="par-le-tableau-de-bord", version=1)["version"] == 3

    with pytest.raises(AuthzDenied) as refus:
        _appel_cap("group.instruction.delete", "u-membre", group_id=monde["equipe"],
                   slug="par-le-tableau-de-bord")
    assert refus.value.status == 403
    assert org_store.get_instruction("group", monde["equipe"], "par-le-tableau-de-bord")


def test_une_equipe_explicite_est_gardee_comme_lactive(monde):
    """`group=` épingle la cible, exactement comme `org=` au palier au-dessus — et la
    garde porte sur l'équipe NOMMÉE, pas sur celle de la session."""
    from oto_mcp import group_store
    etrangere = group_store.create_group(monde["autre_org"], "Ailleurs")
    with pytest.raises(AuthzDenied) as refus:
        _appel("u-chef", op="set", scope="group", group=etrangere,
               slug="ailleurs", body_md=_CORPS)
    assert refus.value.status == 403
    # …et sur la sienne, l'épingle passe et vise bien celle-là.
    out = _appel("u-chef", op="set", scope="group", group=monde["equipe"],
                 slug="epinglee", body_md=_CORPS)
    assert out["group_id"] == monde["equipe"]


def test_un_scope_inconnu_est_refuse_net(monde):
    """Pas de repli silencieux vers l'org : `scope='team'` écrirait au mauvais endroit."""
    with pytest.raises(AuthzDenied) as refus:
        _appel("u-admin", op="set", scope="team", slug="x", body_md=_CORPS)
    assert refus.value.status == 400


def test_le_palier_equipe_nest_pas_atteignable_par_une_surface_dorg(monde):
    """Le verrou : `_owner_of` lit `ctx.group_id`, que seule une règle ayant vérifié
    l'équipe injecte — il ne RELIT jamais l'équipe active.

    ⚠️ L'acteur choisi EN A une (Compta), et le test l'affirme d'abord. La première
    version prenait `u-admin`, qui n'en a pas : elle disait « pas d'équipe → refus »
    au lieu de « une équipe existe et on refuse quand même », et **restait verte
    défaut posé**. Un handler qui relirait l'équipe active écrirait chez elle depuis un
    simple champ d'entrée, sur une capacité dont la règle n'a regardé que l'org — le
    geste ABOUTIRAIT, ce qui est pire qu'un refus : il viserait une cible que personne
    n'a validée."""
    from oto_mcp import access, org_store
    from oto_mcp.capabilities._types import ResolvedCtx
    from oto_mcp.capabilities.orgs import instructions as oi
    assert access.current_group("u-chef") == monde["equipe"], (
        "sans équipe ACTIVE, ce test passerait pour la mauvaise raison")
    ctx = ResolvedCtx(sub="u-chef", org_id=monde["org"])   # group_id NON injecté
    with pytest.raises(AuthzDenied) as refus:
        oi._delete_instruction(ctx, oi.ConsoleGuideDeleteInput(
            slug="cloture-mensuelle", scope="group"))
    assert refus.value.status == 403
    # …et la procédure est toujours là : le refus tombe AVANT l'écriture.
    assert org_store.get_instruction("group", monde["equipe"], "cloture-mensuelle")


def test_aucune_entree_dequipe_nest_gardee_par_une_regle_qui_ignore_scope():
    """L'appariement, figé : une entrée qui porte `scope` et qui atteint les handlers
    d'écriture de procédure DOIT avoir une autz qui se branche sur `scope`.

    C'est le garde-fou qui compte ; le 403 de `_owner_of` n'en est que la ceinture.
    Aujourd'hui `scope` n'est porté que par les entrées de CONSOLE, dont la règle EST
    scope-aware — donc ce 403 est structurellement inatteignable et aucun test runtime
    ne peut le voir rouge. Ce qui peut réellement casser, c'est d'ajouter `scope` à
    `InstrSetInput` pour ouvrir la face REST (gardée par `ORG_ADMIN_OPT`) : le trou
    s'ouvrirait sans qu'une ligne d'autz ait bougé. Ce test est le seul moment où on
    peut encore l'apprendre."""
    from oto_mcp.capabilities.orgs import instructions as oi
    from oto_mcp.capabilities.registry import CAPABILITIES

    ecritures = {oi._set_instruction, oi._delete_instruction, oi._archive_instruction}

    def _lit_scope(regle) -> bool:
        if "scope" in (getattr(regle, "autz_fields", ()) or ()):
            return True
        return any(_lit_scope(b) for b in getattr(regle, "autz_branches", ()) or ())

    vues, fautives = 0, []
    for cap in CAPABILITIES:
        if cap.handler not in ecritures and cap.key != "org.procedure.console":
            continue
        if "scope" not in (cap.Input.model_fields or {}):
            continue
        vues += 1
        if not _lit_scope(cap.authz):
            fautives.append(cap.key)
    # Le cliquet porte SA PROPRE garde : sans ça, renommer l'axe le rendrait inerte en
    # silence — le mode de panne classique d'un cliquet, et le seul qu'il ne peut pas
    # signaler tout seul.
    assert vues >= 1, ("aucune entrée d'écriture ne porte `scope` — le cliquet ne "
                       "surveille plus rien (l'axe a-t-il été renommé ?)")
    assert not fautives, (
        f"{fautives} accepte(nt) `scope` mais leur autz ne le lit pas : le palier visé "
        f"viendrait d'un champ CLIENT, sur une garde qui n'a vu que l'org. "
        f"Se brancher dessus — `BY_OP(..., fields=(\"scope\",))`, cf. _ECRIRE dans "
        f"procedure_console.")


# ── 2. Le déplacement : le critère de « fini » ─────────────────────────────

def _procedure_chargee(monde, versions: int = 26):
    """Une procédure d'org à `versions` versions, avec ses slots, un lien de projet et
    un partage — soit tout ce qu'un déplacement doit faire survivre."""
    from oto_mcp import db, org_store
    slots = [{"name": "sortie", "type": "tableau"}]
    for i in range(versions):
        org_store.set_instruction("org", monde["org"], "cloture-annuelle",
                                  f"{_CORPS}\nrévision {i} <slot:sortie>",
                                  title="Clôture annuelle", set_by="u-admin",
                                  slots=slots if i == 0 else None)
    proc = org_store.get_instruction("org", monde["org"], "cloture-annuelle")
    projet = db.create_project("org", str(monde["org"]), "Fiscal", created_by="u-admin")
    db.add_project_link(projet, "procedure", str(proc["id"]), label="Clôture")
    db.grant_resource("doctrine", str(proc["id"]), "org", str(monde["autre_org"]), "read")
    return proc, projet


def test_le_deplacement_preserve_versions_slots_lien_et_partage(monde):
    """**Le critère de fini.** Une procédure de 26 versions change de propriétaire et
    garde ses 26 versions, ses slots, son lien de projet et son partage.

    Ce qui les fait survivre est UNE chose : l'`id` surrogate ne bouge pas — c'est lui
    que `project_links.target_ref` et `resource_grants.resource_id` désignent. Recréer
    la procédure chez l'équipe perdrait les quatre, et c'est ce qu'un opérateur ferait
    à la main si le déplacement n'existait pas."""
    from oto_mcp import db, org_store, ownership
    proc, projet = _procedure_chargee(monde)
    avant = org_store.list_instruction_versions("org", monde["org"], "cloture-annuelle")
    assert len(avant) == 26

    ownership.transfer("doctrine", str(proc["id"]), "group", str(monde["equipe"]))

    apres = org_store.get_instruction_by_id(proc["id"])
    assert apres["id"] == proc["id"], "l'id surrogate est ce qui fait survivre les liens"
    assert (apres["owner_type"], apres["owner_id"]) == ("group", str(monde["equipe"]))
    assert apres["org_id"] == monde["org"], "l'org PARENTE reste renseignée (colonne NOT NULL)"
    assert apres["slots"] == [{"name": "sortie", "type": "tableau"}]
    assert apres["version"] == 26 and "révision 25" in apres["body_md"]

    # L'historique a suivi — entier, et il n'est plus lisible sous l'ancien scope.
    suivi = org_store.list_instruction_versions("group", monde["equipe"], "cloture-annuelle")
    assert [v["version"] for v in suivi] == [v["version"] for v in avant]
    assert org_store.list_instruction_versions("org", monde["org"], "cloture-annuelle") == []

    # Le lien de projet et le partage désignent le même id : ils n'ont rien à savoir.
    assert [(l["target_type"], l["target_ref"]) for l in db.list_project_links(projet)] \
        == [("procedure", str(proc["id"]))]
    assert db.get_resource_grant("doctrine", str(proc["id"]), "org",
                                 str(monde["autre_org"])) is not None
    assert ownership.owner_of("doctrine", str(proc["id"])) == ("group", str(monde["equipe"]))


def test_le_chef_dequipe_annote_la_procedure_deplacee(monde):
    """Le bout du bout : après le déplacement, celui qui déroule peut écrire dedans —
    et l'écriture s'empile sur l'historique existant, elle ne repart pas de 1."""
    out = _appel("u-chef", op="set", scope="group", slug="cloture-annuelle",
                 body_md=f"{_CORPS}\nApprentissage du 31/08 <slot:sortie>")
    assert out["version"] == 27
    assert _appel("u-chef", op="get", scope="group", slug="cloture-annuelle",
                  with_history=True)["versions"][0]["version"] == 27


def test_le_retour_est_possible_et_symetrique(monde):
    """Un déplacement qui ne se défait pas est un piège, pas une fonctionnalité."""
    from oto_mcp import org_store, ownership
    proc = org_store.get_instruction("group", monde["equipe"], "cloture-annuelle")
    ownership.transfer("doctrine", str(proc["id"]), "org", str(monde["org"]))
    revenue = org_store.get_instruction_by_id(proc["id"])
    assert (revenue["owner_type"], revenue["owner_id"]) == ("org", str(monde["org"]))
    assert len(org_store.list_instruction_versions("org", monde["org"],
                                                   "cloture-annuelle")) == 27


def test_le_deplacement_ne_remplace_jamais_une_procedure_de_la_cible(monde):
    """Le slug est suffixé s'il est pris chez la cible — et la procédure en place, avec
    ses propres versions, est intacte. Non destructif, sans exception."""
    from oto_mcp import org_store, ownership
    org_store.set_instruction("group", monde["equipe"], "budget", _CORPS,
                              title="Budget de l'équipe", set_by="u-chef")
    org_store.set_instruction("group", monde["equipe"], "budget", _CORPS + "v2",
                              set_by="u-chef")
    org_store.set_instruction("org", monde["org"], "budget", _CORPS + "org",
                              title="Budget de l'org", set_by="u-admin")
    a_deplacer = org_store.get_instruction("org", monde["org"], "budget")

    ownership.transfer("doctrine", str(a_deplacer["id"]), "group", str(monde["equipe"]))

    en_place = org_store.get_instruction("group", monde["equipe"], "budget")
    assert en_place["title"] == "Budget de l'équipe" and en_place["version"] == 2
    arrivee = org_store.get_instruction_by_id(a_deplacer["id"])
    assert arrivee["slug"] == "budget-2" and arrivee["title"] == "Budget de l'org"


def test_le_palier_personnel_est_refuse_en_disant_pourquoi(monde):
    """Phase 2 de #681 : `org_instructions.org_id` est NOT NULL et une personne n'a pas
    d'org parente. Le refus nomme les paliers ouverts plutôt que d'affirmer « un guide
    est un objet d'org », devenu faux à la fusion des procédures d'équipe."""
    from oto_mcp import org_store, ownership
    proc = org_store.get_instruction("org", monde["org"], "cloture-annuelle")
    with pytest.raises(ValueError) as e:
        ownership.transfer("doctrine", str(proc["id"]), "user", "u-chef")
    assert "org" in str(e.value) and "group" in str(e.value)
    # …et rien n'a bougé : le refus est levé AVANT la première écriture.
    assert org_store.get_instruction_by_id(proc["id"])["owner_type"] == "org"


# ── 3. Le chemin destructeur latent : la sonde de slug libre ───────────────

def test_la_sonde_de_slug_libre_voit_les_autres_paliers(monde):
    """`_free_instruction_slug` sondait `owner_type='org' AND org_id=%s`, alors que
    l'unicité est `(owner_type, owner_id, slug)`.

    Les deux coïncident tant que `owner_id = org_id::text` — c'était vrai des 137
    lignes de production au 31/08/2026, ce qui rendait le défaut INVISIBLE. Dès qu'une
    ligne d'équipe existe, l'ancienne sonde répond « libre » sur un slug pris, et
    l'`ON CONFLICT DO UPDATE` qui suit écrase la procédure en place **sans un mot**.
    On le montre ici en réintroduisant l'ancienne sonde, puis en remettant la vraie."""
    from oto_mcp.db import _connect
    from oto_mcp.org_store import instructions as store

    store.set_instruction("group", monde["equipe"], "ecrasable", _CORPS,
                          title="À NE PAS PERDRE", set_by="u-chef")
    with _connect() as conn:
        # La sonde d'aujourd'hui voit la ligne d'équipe : elle décale.
        assert store._free_instruction_slug(conn, "group", monde["equipe"],
                                            "ecrasable") == "ecrasable-2"

        # Celle d'hier, rejouée : elle filtrait sur l'org parente et le palier org.
        def _sonde_dhier(conn, _t, owner_id, slug, org_id=monde["org"]):
            row = conn.execute(
                "SELECT 1 FROM org_instructions WHERE owner_type = 'org' AND org_id = %s "
                "AND slug = %s", (org_id, slug)).fetchone()
            return slug if row is None else f"{slug}-2"
        assert _sonde_dhier(conn, "group", monde["equipe"], "ecrasable") == "ecrasable", (
            "l'ancienne sonde doit bien répondre « libre » — sinon ce test ne prouve "
            "rien et le correctif n'a pas d'objet")

    # Et la copie, qui l'emprunte, ne détruit donc rien.
    store.set_instruction("org", monde["org"], "ecrasable", _CORPS,
                          title="Homonyme d'org", set_by="u-admin")
    source = store.get_instruction("org", monde["org"], "ecrasable")
    copie = store.copy_instruction_to_owner(source["id"], "group", monde["equipe"])
    assert copie["slug"] != "ecrasable"
    assert store.get_instruction("group", monde["equipe"],
                                 "ecrasable")["title"] == "À NE PAS PERDRE"


def test_la_sonde_regarde_aussi_lhistorique(monde):
    """Un slug libre côté table vivante mais PRIS côté révisions ferait échouer
    l'insertion du snapshot — donc une copie en 500 et un déplacement bloqué. Le cas
    naît d'un `archive` (qui garde les révisions) suivi d'un `delete` de la ligne."""
    from oto_mcp.db import _connect
    from oto_mcp.org_store import instructions as store
    store.set_instruction("group", monde["equipe"], "fantome", _CORPS, set_by="u-chef")
    with _connect() as conn:
        conn.execute("DELETE FROM org_instructions WHERE owner_type = 'group' "
                     "AND owner_id = %s AND slug = 'fantome'", (str(monde["equipe"]),))
    with _connect() as conn:
        assert conn.execute(
            "SELECT count(*) AS n FROM org_instruction_revisions WHERE owner_type = 'group' "
            "AND owner_id = %s AND slug = 'fantome'", (str(monde["equipe"]),)
        ).fetchone()["n"] == 1
        assert store._free_instruction_slug(conn, "group", monde["equipe"],
                                            "fantome") == "fantome-2"


# ── 4. Ce que la gouvernance voit ──────────────────────────────────────────

def test_une_procedure_dequipe_est_visible_dans_oto_resource(monde):
    """`_OPS['doctrine']` filtrait les paires d'owner sur `t == "org"` et
    `_enrich_guide` écrivait `owner_type: "org"` en dur : une procédure d'équipe était
    absente du listing de gouvernance, et celles qui s'y trouvaient annonçaient comme
    propriétaire leur org PARENTE — la mauvaise cible dans l'écran de partage."""
    from oto_mcp import org_store
    from oto_mcp.capabilities import resources as R
    org_store.set_instruction("group", monde["equipe"], "gouvernee", _CORPS,
                              title="Gouvernée", set_by="u-chef")
    rows = R._OPS["doctrine"]["list_for_owners"]([("group", str(monde["equipe"]))])
    assert "gouvernee" in {r["slug"] for r in rows}
    enrichie = R._OPS["doctrine"]["enrich"](
        next(r for r in rows if r["slug"] == "gouvernee"))
    assert enrichie["owner_type"] == "group"
    assert enrichie["owner_id"] == str(monde["equipe"])
    # La vue opérateur ne filtre plus par palier non plus.
    assert "gouvernee" in {r["slug"] for r in R._OPS["doctrine"]["list_all"]()}
