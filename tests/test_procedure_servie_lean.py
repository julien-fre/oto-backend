"""Ce que l'AGENT lit d'une procédure : la consigne, pas la vitrine.

Sur la face MCP, `op=get` sert le corps SANS son dessin (un marqueur à sa place) et
sans la description du catalogue ; `op=list` résume la description. Rien de tout ça
ne se demande : c'est le défaut, parce qu'une économie qu'il faut connaître pour en
bénéficier ne bénéficie à personne. `full=true` / `verbose=true` rendent tout.

Le banc qui compte est l'aller-retour : un agent RELIT puis RÉÉCRIT. Si le marqueur
ne rendait pas le dessin à l'écriture, chaque édition d'agent viderait la page du
process — et personne ne le verrait avant d'ouvrir la page.
"""
from __future__ import annotations

import asyncio

from oto_mcp import procedure_diagram
from oto_mcp.capabilities.orgs import instructions as oi
from oto_mcp.capabilities._types import ResolvedCtx

_DESSIN = """```
┌──────────────┐
│  1 · Lire    │
└──────┬───────┘
       ▼
┌──────────────┐
│  2 · Agir    │──▶  la table
└──────────────┘
```"""

_CORPS = f"""# Une procédure

Ce qu'elle fait, en une phrase.

{_DESSIN}

## Étape 1 — Lire
Lis la table avec `data_rows`.

```text
┌──┐ un échantillon TAGUÉ, pas un dessin
└──┘
```

## Étape 2 — Agir
Écris avec `data_write`.
"""

_DESCRIPTION = ("Une description de vitrine qui fait un paragraphe entier, comme celles des "
                "procédures les plus travaillées, et qui dépasse largement la borne du résumé "
                "servi au catalogue de la face agent, parce qu'elle raconte l'histoire de "
                "chaque règle au lieu de dire ce que fait la procédure.")


# ── Le marqueur : aller-retour à l'identique ────────────────────────────────────

def test_le_dessin_devient_un_marqueur_et_revient_a_l_identique():
    servi = procedure_diagram.sans_le_dessin(_CORPS, 7)
    assert _DESSIN not in servi
    assert "<!-- flowchart: v7, 9 lines" in servi
    assert not procedure_diagram.has_diagram(servi), "le marqueur ne doit pas passer pour un dessin"
    # Le bloc TAGUÉ reste : ce n'est pas un dessin, la page ne le rendrait pas.
    assert "un échantillon TAGUÉ" in servi
    assert procedure_diagram.avec_le_dessin(servi, _CORPS) == _CORPS


def test_un_corps_sans_dessin_est_servi_tel_quel():
    corps = "# Rien à dessiner\n\n## Étape 1\nFais-le."
    assert procedure_diagram.sans_le_dessin(corps, 1) == corps
    assert procedure_diagram.avec_le_dessin(corps, _CORPS) == corps


def test_sans_dessin_courant_le_marqueur_s_efface():
    """Création, ou procédure qui n'a jamais eu de dessin : le marqueur ne peut rien
    rendre, il part — et c'est cette PERTE qui est dite, pas l'absence de dessin."""
    servi = procedure_diagram.sans_le_dessin(_CORPS, 7)
    assert procedure_diagram.marqueur_sans_dessin(servi, "")
    ecrit = procedure_diagram.avec_le_dessin(servi, "")
    assert not procedure_diagram.porte_le_marqueur(ecrit)
    assert not procedure_diagram.has_diagram(ecrit)
    assert procedure_diagram.diagram_check(ecrit)["diagram_warning"] is None
    assert (procedure_diagram.diagram_check(ecrit, marqueur_perdu=True)["diagram_warning"]
            == procedure_diagram.PERDU)


def test_un_vrai_dessin_envoye_gagne_sur_le_courant():
    nouveau = _CORPS.replace("2 · Agir", "2 · Pousser")
    assert procedure_diagram.avec_le_dessin(nouveau, _CORPS) == nouveau


# ── op=get : la face agent, la face page, et `full` ────────────────────────────

def _lecture(monkeypatch):
    monkeypatch.setattr(oi, "_project_instance", lambda member_mode: None)
    monkeypatch.setattr(oi.org_store, "get_instruction",
                        lambda otype, oid, slug, version=None: {
                            "slug": slug, "title": "T", "description": _DESCRIPTION,
                            "version": 7, "body_md": _CORPS, "slots": []})

    async def _manifest(*a, **k):
        return []
    monkeypatch.setattr(oi.tool_registry, "manifest_for", _manifest)


def _get(ctx, **champs):
    return asyncio.run(oi._get_guide(ctx, oi.GuideGetInput(slug="p", scope="org", **champs)))


def test_op_get_sur_la_face_mcp_sert_le_marqueur_et_pas_la_description(monkeypatch):
    _lecture(monkeypatch)
    out = _get(ResolvedCtx(sub="u1", org_id=3, channel="mcp"))
    assert "description" not in out
    assert _DESSIN not in out["body_md"]
    assert procedure_diagram.porte_le_marqueur(out["body_md"])
    assert out["version"] == 7 and out["title"] == "T"


def test_op_get_hors_de_la_face_mcp_sert_tout(monkeypatch):
    """La face REST nourrit la page du process — elle a besoin du dessin ; un appel
    interne aussi. Le rendu allégé ne vaut que sur `"mcp"` EXPLICITE."""
    _lecture(monkeypatch)
    for canal in ("rest", None):
        out = _get(ResolvedCtx(sub="u1", org_id=3, channel=canal))
        assert out["body_md"] == _CORPS and out["description"] == _DESCRIPTION, canal


def test_op_get_full_rend_tout_a_l_agent(monkeypatch):
    _lecture(monkeypatch)
    out = _get(ResolvedCtx(sub="u1", org_id=3, channel="mcp"), full=True)
    assert out["body_md"] == _CORPS and out["description"] == _DESCRIPTION


# ── op=list : le résumé ─────────────────────────────────────────────────────────

def test_le_resume_coupe_au_mot_et_laisse_le_court_intact():
    assert oi._resume("court") == "court"
    r = oi._resume(_DESCRIPTION)
    assert r.endswith("…") and len(r) <= oi._RESUME_MAX + 1
    assert not r[:-1].endswith(" ") and r[:-1] in _DESCRIPTION
    assert oi._resume("  des   espaces\n partout ") == "des espaces partout"


def _catalogue(monkeypatch):
    monkeypatch.setattr(oi, "_active_group", lambda ctx: None)
    monkeypatch.setattr(oi.org_store, "list_instructions",
                        lambda otype, oid, include_base=False: [] if otype == "user" else [
                            {"id": 1, "slug": "p", "title": "T", "description": _DESCRIPTION,
                             "version": 7, "updated_at": "2026-09-10 12:00:00"}])


def test_op_list_sur_la_face_mcp_resume_la_description(monkeypatch):
    _catalogue(monkeypatch)
    out = oi._list_guides(ResolvedCtx(sub="u1", org_id=3, channel="mcp"), oi.GuideListInput())
    [g] = out["guides"]
    assert "description" not in g and "updated_at" not in g
    assert g["summary"] == oi._resume(_DESCRIPTION)
    assert g["slug"] == "p" and g["version"] == 7 and g["scope"] == "org"


def test_op_list_verbose_ou_hors_mcp_rend_la_description_entiere(monkeypatch):
    _catalogue(monkeypatch)
    for ctx, inp in ((ResolvedCtx(sub="u1", org_id=3, channel="mcp"), oi.GuideListInput(verbose=True)),
                     (ResolvedCtx(sub="u1", org_id=3, channel="rest"), oi.GuideListInput())):
        [g] = oi._list_guides(ctx, inp)["guides"]
        assert g["description"] == _DESCRIPTION and "summary" not in g


# ── op=set : l'aller-retour de l'agent garde le dessin ─────────────────────────

def _ecriture(monkeypatch, courant: str | None):
    ecrit: dict = {}

    def _get_instruction(otype, oid, slug, version=None):
        return None if courant is None else {"slug": slug, "title": "T", "description": "d",
                                             "version": 7, "body_md": courant, "slots": []}

    def _set_instruction(otype, oid, slug, body_md, **kw):
        ecrit["body_md"] = body_md
        return 8
    monkeypatch.setattr(oi.org_store, "get_instruction", _get_instruction)
    monkeypatch.setattr(oi.org_store, "set_instruction", _set_instruction)
    return ecrit


def test_op_set_avec_le_marqueur_rend_le_dessin_de_la_version_courante(monkeypatch):
    ecrit = _ecriture(monkeypatch, _CORPS)
    relu = procedure_diagram.sans_le_dessin(_CORPS, 7)          # ce que l'agent a lu
    modifie = relu.replace("Fais-le", "Fais-le").replace("Lis la table", "Lis TOUTE la table")
    out = oi._write_instruction(ResolvedCtx(sub="u1", org_id=3),
                                oi.ConsoleInstrSetInput(slug="p", body_md=modifie, scope="org"))[0]
    # `.strip()` : l'écriture ôte les blancs de bordure, comme pour tout corps.
    assert ecrit["body_md"] == _CORPS.replace("Lis la table", "Lis TOUTE la table").strip()
    assert out["diagram_warning"] is None
    assert out["retrait_warning"] is None


def test_op_set_a_la_creation_efface_le_marqueur_et_avertit(monkeypatch):
    ecrit = _ecriture(monkeypatch, None)
    relu = procedure_diagram.sans_le_dessin(_CORPS, 7)
    out = oi._write_instruction(ResolvedCtx(sub="u1", org_id=3),
                                oi.ConsoleInstrCreateInput(slug="p", body_md=relu, scope="org"),
                                must_create=True)[0]
    assert not procedure_diagram.porte_le_marqueur(ecrit["body_md"])
    assert out["diagram_warning"] == procedure_diagram.PERDU


def test_op_set_avec_un_vrai_dessin_l_ecrit_tel_quel(monkeypatch):
    ecrit = _ecriture(monkeypatch, _CORPS)
    nouveau = _CORPS.replace("2 · Agir", "2 · Pousser")
    oi._write_instruction(ResolvedCtx(sub="u1", org_id=3),
                          oi.ConsoleInstrSetInput(slug="p", body_md=nouveau, scope="org"))
    assert ecrit["body_md"] == nouveau.strip()


# ── Ce que le marqueur PROMET, et ce que la promesse vaut ──────────────────────

def test_le_marqueur_ne_promet_rien_sans_condition():
    """La ligne a d'abord dit « keep this line and op=set keeps the drawing ». Sans
    condition, et faux dans quatre cas : `op=create`, un slug neuf, un `scope` omis
    (le défaut d'écriture est `user`), et la ligne non recopiée. Un texte servi est du
    code de prod — une promesse sans condition sera crue."""
    ligne = procedure_diagram.marqueur(7, 9)
    assert "op=set keeps the drawing" not in ligne, (
        "promesse inconditionnelle revenue dans le marqueur")
    assert "SAME slug and scope" in ligne, "la condition doit être DANS la ligne servie"
    assert "op=create never does" in ligne


def test_un_scope_omis_ecrit_ailleurs_et_perd_le_dessin(monkeypatch):
    """Le cas que la promesse d'origine niait : on relit une procédure d'ORG, on
    réécrit sans `scope`, l'écriture part au palier `user` (ADR 0068) — il n'y a rien
    à relire là-bas, le marqueur s'efface, le dessin est perdu. Non silencieux
    (`diagram_warning`), mais après coup."""
    from oto_mcp.capabilities import procedure_console as pc
    # 1. Sur la face AGENT, une écriture sans `scope` vise le palier PERSONNEL.
    assert pc._ECRIT_SCOPE(pc.ProcedureInput(op="set", slug="p")) == "user", (
        "le défaut d'écriture de `oto_procedure` n'est plus `user` : le marqueur peut "
        "reformuler sa condition, ce banc dit pourquoi elle existe")
    # 2. Le dessin, lui, est resté chez l'org : il n'y a rien à relire chez soi.
    vus = []

    def _get_instruction(otype, oid, slug):
        vus.append(otype)
        return None                       # rien de stocké au palier personnel
    monkeypatch.setattr(oi.org_store, "get_instruction", _get_instruction)
    monkeypatch.setattr(oi.org_store, "set_instruction", lambda *a, **k: 1)
    relu = procedure_diagram.sans_le_dessin(_CORPS, 7)          # lu chez l'org
    out = oi._write_instruction(ResolvedCtx(sub="u1", org_id=3),
                                oi.ConsoleInstrSetInput(slug="p", body_md=relu,
                                                        scope="user"))[0]
    assert vus and vus[0] == "user", "l'écriture doit relire le palier qu'elle VISE"
    assert out["diagram_warning"] == procedure_diagram.PERDU


def test_marqueur_plus_vrai_dessin_annonce_les_deux_blocs(monkeypatch):
    """Le seul cas où la perte était MUETTE : le corps porte le marqueur ET un dessin
    neuf. `avec_le_dessin` remet le tracé stocké à côté du neuf, `has_diagram` disait
    « oui, il y a un dessin » — et la page n'en rend qu'un, le premier."""
    ecrit = _ecriture(monkeypatch, _CORPS)
    autre = _DESSIN.replace("1 · Lire", "1 · Relire")
    corps = procedure_diagram.sans_le_dessin(_CORPS, 7) + "\n\n" + autre + "\n"
    out = oi._write_instruction(ResolvedCtx(sub="u1", org_id=3),
                                oi.ConsoleInstrSetInput(slug="p", body_md=corps,
                                                        scope="org"))[0]
    assert procedure_diagram.compter_les_dessins(ecrit["body_md"]) == 2
    assert out["diagram_warning"] == procedure_diagram.DOUBLE


# ── Le texte servi tient dans ce que le client en lit ──────────────────────────
#
# Claude Code coupe la description d'un outil MCP à 2048 caractères (mesuré le
# 10/09/2026). Ce qui est écrit au-delà n'atteint pas le modèle : ajouter en TÊTE
# pousse dehors ce qui était vu. La règle n'est donc pas « écris court », c'est
# « ce qui coûte du travail à qui l'ignore passe devant ».
_COUPE_CLIENT = 2048

# Chacune de ces phrases a un coût mesuré si elle manque : perdre le dessin d'une
# procédure, publier chez soi ce qu'on croyait publier pour l'org, ou découvrir la
# forme de `slots` par essais (une procédure a atteint la v3 comme ça).
_A_SAUVER = (
    "`diagram_warning`",
    "same slug and scope you read from",
    "Omitting `scope` writes YOUR OWN procedure",
    "`[{name, type}]`",
)


def _description_servie(nom: str) -> str:
    from _mcp_app import static_mcp
    outils = {t.name: t for t in asyncio.run(static_mcp().list_tools(run_middleware=False))}
    return outils[nom].description or ""


def test_l_essentiel_d_oto_procedure_survit_a_la_coupe_du_client():
    d = _description_servie("oto_procedure")
    dehors = []
    for phrase in _A_SAUVER:
        i = d.find(phrase)
        if i < 0 or i + len(phrase) > _COUPE_CLIENT:
            dehors.append((phrase, i))
    assert not dehors, (
        f"Hors des {_COUPE_CLIENT} premiers caractères servis (longueur totale "
        f"{len(d)}) : {dehors}. Claude Code ne lira pas ces phrases. N'allonge pas la "
        "tête de la description : déplace vers la fin ce qui se rattrape autrement.")
