"""Instructions serveur MCP (champ FastMCP `instructions=`) — le **contexte oto**
injecté à Claude au handshake `initialize`. C'est LE canal fiable de bootstrap d'un
agent (model-agnostic), pas un appel d'outil volontaire.

Refonte #50 (amende ADR 0014/0017) — l'artefact injecté est **composé de 2 blocs** :

- **Bloc A — secret sauce plateforme** : posture + boucle d'usage + **catalogue de
  namespaces** (dérivé du registre). Prose stockée en DB
  (`platform_instructions['secret_sauce']`), éditable seulement par l'admin plateforme,
  **inviolable par l'org**, toujours injectée ; le catalogue est appendé à la composition.
  La constante `_SECRET_SAUCE` = le défaut seedé au boot + le fallback (aucun accès DB à
  l'import).
- **Bloc C — contexte dynamique** par-(sub, org) : section de contexte résolu (org /
  équipe / connecteurs actifs / N derniers projets / derniers déroulés / fiche
  « situation avec oto » de l'user) + les **agent README cumulés** du général au
  spécifique — org (`org_instructions` slug `claude_md`) → équipe active
  (`org_group_instructions` slug `claude_md`) → user (`user_agent_readme`) — chacun
  avec substitution des variables `{{org}}` / `{{user}}` / `{{équipe}}` /
  `{{connecteurs_actifs}}`. (Le niveau plateforme du concept = le bloc A.)

L'onboarding n'est PAS un bloc : c'est un projet « Découverte » (ADR 0032 §7) semé à la
création de l'org perso, qui remonte via la ligne « Projets récents » du bloc C.

`render()` (STATIQUE, sans DB) = bloc A seed + catalogue → défaut de boot et fallback.
`compose_session(sub, org_id)` (RUNTIME) = l'artefact réel par session.
Tout est **fail-open** : toute erreur retombe sur la surface statique."""
from __future__ import annotations

import logging

from . import providers, run_status

logger = logging.getLogger(__name__)

# Clé du bloc plateforme en DB (table `platform_instructions`).
KEY_SECRET_SAUCE = "secret_sauce"

# --- Bloc A — secret sauce plateforme (défaut seedé + fallback) -------------
_SECRET_SAUCE = """\
Oto — TA boîte à outils d'automatisation (prospection B2B, données entreprise FR, CRM, email, messagerie, base de connaissance…). Ces outils `*_` AGISSENT sur les comptes et données de l'utilisateur : utilise-les DIRECTEMENT pour répondre. **Par défaut, pour toute tâche dans ce périmètre, sers-toi d'oto sans qu'on te dise « avec oto ».** Oto n'est PAS un sujet à rechercher sur le web — ne lance pas de recherche web pour « découvrir Oto » ; tout ce qu'il faut est ici et dans les outils.

**Cherche une procédure AVANT d'agir.** Avant une tâche substantielle, vérifie s'il existe une procédure pertinente (`oto_procedure op=list`), y compris hors du projet courant. Une procédure existante fait autorité sur ta propre méthode.

**Encadre et remonte.** Quand tu exécutes une procédure — un workflow doctriné OU un déroulé one-shot qui mérite d'être tracé — ouvre-la par `run_start(label, doctrine?)` (passe `doctrine`=slug pour une doctrine nommée, omets-le pour un run ad-hoc) et ferme-la par `run_finish(run_id, outcome)` (done|failed|blocked). **Remonte tout signal d'usage** via `feedback(signal, kind, target, text?)` : `signal='gap'` quand oto ne couvre PAS ce dont tu as besoin (outil, doctrine ou donnée manquants — `target`=ce que tu voulais faire) plutôt que d'abandonner en silence ; `signal='tool_feedback'` quand un outil se comporte mal ou excellemment (`target`=le nom de l'outil). **Déclenche-le DE TOI-MÊME, immédiatement, sans attendre que l'utilisateur te le demande** : dès qu'un outil échoue (erreur, timeout), renvoie un résultat trompeur/vide/incohérent, ou qu'une capacité te manque pour agir — appelle `feedback` sur le coup, puis poursuis. Un signal manqué = un bug que la plateforme ne verra jamais. C'est ainsi que la plateforme apprend.

**Travaille dans un projet.** Un projet est le foyer d'une tâche : son contexte (brief, tableaux, connecteurs préconfigurés, procédures). Quand tu agis POUR un projet, passe le jeton `_project=<id>` sur CHAQUE appel de travail (liste/charge via `oto_project` op=list/get — aucun état de session, ADR 0038) : tes connecteurs prennent alors l'identité préconfigurée du projet, l'org du projet s'applique, tes runs lui sont rattachés, et tes tableaux de sortie doivent y être liés (`oto_project(op=link, target_type=tableau)`). Une procédure exécutée dans un projet partage SES ressources (tableaux, connecteurs) : ne crée pas de ressources propres à la procédure. Pour une tâche ad-hoc sans projet existant (extraction one-shot, prospection ponctuelle…), **crée un projet** pour héberger sa sortie et sa trace plutôt que de travailler hors-sol.

**Porte ton contexte DANS l'appel, jamais dans un état de session.** Il n'y a AUCUN état de session serveur : quand une action dépend d'un contexte précis, passe-le EN PARAMÈTRE de l'appel. **Les jetons de contexte sont préfixés `_`** (ils sont à la plateforme ; sans préfixe, le nom appartient aux arguments métier de l'outil) — `_project=<id>` (le jeton PRIMAIRE : org du projet, slots `slot:<nom>`, identités connecteur préfaites), `_org=<id>` / `_group=<id>` (agir dans une org/équipe donnée), `_account=<label>` (connecteur multi-compte, ex. « 2 Zoho » — `oto_identity(op="list")` liste les labels), `_instance=<ref>` (une instance de connecteur PRÉCISE — un credential exact, refs via `oto_instance(op="list")`), `_run_id=<id>` (rattacher l'appel à un `run_start`). **Tous sont OPTIONNELS : omis, chacun prend son défaut** — ton org courante, aucun projet, ton compte par défaut, la résolution de credential normale, le run actif s'il y en a un. Ne les passe que pour t'écarter de ce défaut ; leur description au schéma est volontairement d'une ligne, le fond est ici. Les `oto_use_*` ne posent plus d'état : ils valident l'accès et te rappellent le jeton à passer.

**Ta base de connaissance = Documents.** Le savoir durable de l'organisation (processus, contexte, conventions, faits sourcés) vit dans la zone **Documents** — une base par org, résolue par `oto_kb` → `project_id`, dont les pages se lisent/cherchent/écrivent via `oto_doc` : `op=search` pour localiser une page, `op=get` pour la lire, `op=create`/`update` (`kind=source|note`) pour capturer. **Réflexe** : avant de chercher sur le web un fait propre à l'organisation, cherche dans Documents ; et quand tu apprends un fait de référence réutilisable, **capture-le là** plutôt que de le laisser filer. C'est la mémoire partagée de l'org, pas un scratchpad.

**Un outil non listé ? Appelle-le quand même via `oto_call`.** `oto_call(name, arguments)` est le pont universel : il exécute par son nom N'IMPORTE quel outil du catalogue — un outil masqué, un outil de FOD, ou un connecteur que tu VIENS d'activer. ⚠️ Activer un connecteur en cours de conversation ne monte PAS ses outils dans la session (le registre est figé à l'ouverture, et claude.ai n'applique pas le rechargement à chaud) : n'en conclus JAMAIS « la capacité n'existe pas ». Appelle-le tout de suite via `oto_call(name="<connecteur>_…", arguments={…})` — il accepte aussi `_org=` pour exécuter sous une org donnée — ou invite l'utilisateur à ouvrir une NOUVELLE conversation pour les voir montés. (Un sous-agent que tu lances hérite du même registre figé → lui aussi passe par `oto_call`.)

**Le compte démarre nu : les connecteurs s'INSTALLENT.** Un nouvel espace n'a AUCUN connecteur pré-installé — c'est le régime normal, pas une panne. Si la toolbox ne montre (presque) que des outils `oto_*`/`data_*`, ton rôle est de GUIDER : comprends ce que l'utilisateur veut faire, repère les capacités correspondantes dans le catalogue de namespaces ci-dessous (ou `oto_connector(op='list')` pour l'état par connecteur), propose-en 2-3 pertinentes et installe-les (`oto_connector(op='select', name=…)`). N'attends pas le remontage : exécute tout de suite via `oto_call`. Les capacités open data (`fr_*`, `foncier_*`, `juris_*`…) et à free tier (serper, hunter…) marchent sans aucune configuration ; celles à clé ou à compte se connectent sur le dashboard — dis-le simplement, ne simule jamais un résultat.

**Slots : la procédure déclare, le projet binde.** Une procédure déclare ses entités requises en slots nommés et sa prose les référence `<slot:name>` — jamais un nom d'instance en dur. Le projet fait la correspondance nom→instance via ses liens (`oto_project(op=link, …, slot='name')` ; pour un connecteur, `instance_ref=<ref>` binde un credential EXACT). Tu adresses le tableau d'un slot avec `namespace='slot:<name>'` sur les tools `data_*`, dans le cadre du projet (`_project=<id>` sur l'appel). Si un slot ne résout pas (pas de `_project=` sur l'appel, ou nom non bindé), l'appel est REFUSÉ avec la marche à suivre : **matérialise le contexte d'abord** — demande quel projet (ou crées-en un), et pour chaque slot binde une ressource existante ou crée-la ; ne choisis JAMAIS une table « probable » à la place d'un binding manquant.
"""

# En-tête du catalogue de namespaces (dérivé du registre), appendé au bloc A.
_CATALOG_HEADER = (
    "Namespaces — le catalogue COMPLET des capacités de la plateforme. Aucune n'est "
    "installée d'office : celles absentes de ta toolbox s'installent via "
    "`oto_connector(op='select', name=…)` (durable) ou s'appellent ponctuellement via "
    "`oto_call` :"
)

# En-têtes des agent README cumulés (bloc C), du général au spécifique.
_README_ORG_HEADER = "## README de ton organisation"
_README_GROUP_HEADER = "## README de ton équipe"
_README_USER_HEADER = "## README de ton utilisateur"
_CONTEXT_HEADER = "## Ton contexte oto"

# Tokens de variable substitués dans les agent README (bloc C). Auto-contexte v1.
_VAR_TOKENS = ("{{org}}", "{{user}}", "{{équipe}}", "{{equipe}}", "{{connecteurs_actifs}}",
               "{{rôle}}", "{{role}}", "{{date}}", "{{projets_récents}}", "{{projets_recents}}")


# --- Lecture des blocs plateforme (DB override → seed) ----------------------

def _platform_block(key: str, seed: str) -> str:
    """Le bloc plateforme `key` : override DB s'il existe et non vide, sinon `seed`
    (constante). Fail-open au seed. La lecture DB est centralisée dans `guide_store`
    (ADR 0042 : source unique de la prose init ; le seed reste ici, son domicile)."""
    from . import guide_store
    return guide_store.init_guide_body("platform", key) or seed.strip()


def _catalog() -> str:
    """L'en-tête + le catalogue de namespaces dérivé du registre (toujours injecté)."""
    return f"{_CATALOG_HEADER}\n{providers.render_namespace_catalog()}"


def _block_a() -> str:
    return f"{_platform_block(KEY_SECRET_SAUCE, _SECRET_SAUCE)}\n\n{_catalog()}"


# --- Bloc C — contexte dynamique par-(sub, org) -----------------------------

def _resolve_context(sub: str | None, org_id: int) -> dict:
    """Résout l'auto-contexte d'un (sub, org) — réutilisé par la section de contexte
    ET la substitution de variables. Chaque champ best-effort (jamais bloquant)."""
    from . import access, db, org_store, roles

    org = org_store.get_org(org_id) or {}
    org_name = org.get("name") or f"#{org_id}"

    user_name = ""
    if sub:
        try:
            u = db.get_user(sub) or {}
            user_name = (u.get("name") or u.get("email") or "").strip()
        except Exception:
            pass

    role = ""
    if sub:
        try:
            role = roles.effective_org_role(sub, org_id) or ""
        except Exception:
            pass

    group_name = ""
    group_id: int | None = None
    try:
        from . import group_store
        gid = access.current_group(sub) if sub else None
        if gid is not None:
            group_id = gid
            group_name = ((group_store.get_group(gid) or {}).get("name") or "").strip()
    except Exception:
        pass

    connectors: list[str] = []
    if sub:
        try:
            providers_status = (access.status_for(sub).get("providers") or {})
            connectors = sorted(
                name for name, st in providers_status.items()
                if st.get("mode") in ("user", "group", "org", "platform")
            )
        except Exception:
            pass

    projects: list[str] = []
    try:
        rows = db.list_projects_for_owners([("org", str(org_id))])
        # + les projets LIVRÉS à cette org (partagés via resource_grants, #52) — c'est
        # l'exposition au handshake : le client ouvre le projet livré en un message.
        seen = {r.get("id") for r in rows}
        principals = [("org", str(org_id))] + ([("user", sub)] if sub else [])
        rows += [r for r in db.list_projects_granted_to(principals)
                 if r.get("id") not in seen]
        projects = [r.get("name") or f"#{r.get('id')}" for r in rows[:5]]
    except Exception:
        pass

    runs: list[dict] = []
    if sub:
        try:
            runs = db.recent_runs(sub, org_id, limit=5)
        except Exception:
            pass

    # Retour au proposeur (Ship 3) : mes propositions RÉCEMMENT traitées (acceptées /
    # refusées) — sinon l'agent qui a proposé ne voit jamais la résolution côté MCP
    # (le suivi ne vit que dans l'inbox du dashboard). Fenêtre courte = anti-répétition.
    proposals: list[dict] = []
    if sub:
        try:
            proposals = db.list_change_requests_by_requester(sub, since_days=7)[:5]
        except Exception:
            pass

    # Fiche « situation avec oto » (ce que l'agent sait de l'utilisateur, entretenu via
    # `oto_profile`) — réinjectée pour personnaliser l'aide. Best-effort.
    profile: dict = {}
    if sub:
        try:
            profile = db.get_account_profile(sub).get("profile") or {}
        except Exception:
            pass

    return {
        "org_name": org_name, "user_name": user_name, "role": role,
        "group_name": group_name, "group_id": group_id, "connectors": connectors,
        "projects": projects, "runs": runs, "proposals": proposals, "profile": profile,
    }


def _apply_vars(body: str, ctx: dict) -> str:
    """Substitue les variables d'auto-contexte dans la doctrine d'org. Les tokens
    inconnus sont laissés tels quels (intention de l'auteur)."""
    from datetime import date
    projets = " · ".join(ctx.get("projects") or []) or "—"
    role = ctx.get("role") or "—"
    repl = {
        "{{org}}": ctx["org_name"],
        "{{user}}": ctx["user_name"] or "—",
        "{{équipe}}": ctx["group_name"] or "—",
        "{{equipe}}": ctx["group_name"] or "—",
        "{{connecteurs_actifs}}": ", ".join(ctx["connectors"]) or "—",
        "{{rôle}}": role, "{{role}}": role,          # rôle de l'user dans l'org (#6 C)
        "{{date}}": date.today().isoformat(),         # date du jour (session)
        "{{projets_récents}}": projets, "{{projets_recents}}": projets,
    }
    for token, value in repl.items():
        if token in body:
            body = body.replace(token, value)
    return body


def _format_context(ctx: dict) -> str:
    """La section « ## Ton contexte oto » — auto-contexte + anticipation (projets,
    déroulés). Lignes optionnelles : seules celles avec de la donnée sont rendues."""
    lines = [_CONTEXT_HEADER, ""]
    role = f" (ton rôle : {ctx['role']})" if ctx["role"] else ""
    lines.append(f"- Organisation : {ctx['org_name']}{role}")
    if ctx["group_name"]:
        lines.append(f"- Équipe active : {ctx['group_name']}")
    if ctx["connectors"]:
        lines.append(f"- Connecteurs actifs : {', '.join(ctx['connectors'])}")
    if ctx["projects"]:
        lines.append(f"- Projets récents : {' · '.join(ctx['projects'])}")
    if ctx["runs"]:
        bits = []
        for r in ctx["runs"]:
            label = r.get("label") or r.get("run_id") or "?"
            doc = f" [{r['doctrine']}]" if r.get("doctrine") else ""
            # Un run muet ne s'annonce plus « en cours » : l'agent lisait ça au
            # handshake et croyait reprendre un travail que personne ne menait plus.
            bits.append(f"{label}{doc} {run_status.describe(r)}")
        lines.append(f"- Derniers déroulés : {' · '.join(bits)}")
    if ctx.get("proposals"):
        bits = []
        for cr in ctx["proposals"]:
            title = cr.get("doc_title") or cr.get("proposed_title") or "?"
            verdict = "acceptée" if cr.get("status") == "accepted" else "refusée"
            bits.append(f"« {title} » {verdict}")
        lines.append(f"- Tes propositions traitées : {' · '.join(bits)}")
    return "\n".join(lines)


# Libellés lisibles des champs connus de la fiche (cf. capabilities/profile.PROFILE_FIELDS) ;
# une clé libre inconnue est rendue telle quelle.
_PROFILE_LABELS = {
    "full_name": "Nom", "role": "Rôle", "company": "Entreprise / secteur",
    "goals": "Objectifs", "crm": "CRM", "connectors_wanted": "Connecteurs voulus",
    "tone": "Ton / préférences",
}


def _format_profile(profile: dict) -> str:
    """La fiche « situation avec oto » de l'user (champs remplis seulement). '' si vide.
    Entretenue par l'agent via `oto_profile` ; sert à personnaliser l'aide."""
    rows = []
    for key, value in profile.items():
        text = str(value).strip() if value is not None else ""
        if not text:
            continue
        rows.append(f"- {_PROFILE_LABELS.get(key, key)} : {text}")
    if not rows:
        return ""
    return "### Ce que tu sais de l'utilisateur\n" + "\n".join(rows)


def _c_layers(sub: str | None, org_id: int | None) -> list[dict]:
    """Les couches du bloc C, ORDONNÉES — `[{key, label, body}]`, couches vides omises.
    `[]` si pas d'org. Fail-open : README d'org seul sans contexte si la résolution
    échoue. Source unique : `_block_c` (artefact injecté) et la vue de transparence
    `/api/me/agent-context` (pile de couches) en dérivent — derive don't duplicate."""
    if org_id is None:
        return []
    try:
        ctx = _resolve_context(sub, org_id)
    except Exception:
        logger.warning("résolution du contexte org=%s échouée (fail-open readme)",
                       org_id, exc_info=True)
        body = _org_readme_only(org_id)
        return [{"key": "org", "label": "readme de ton org", "body": body}] if body else []

    # Readmes « init » cumulés du général au spécifique (org → équipe → user) : le
    # MÊME primitif de guide, rendu uniformément par scope (ADR 0042). Chaque scope =
    # (owner, en-tête) ; corps lu via `guide_store.init_guide_body`, variables
    # substituées. Ordre = cumul de doctrine ; un scope vide est omis.
    layers = [{"key": "context", "label": "ton contexte oto", "body": _format_context(ctx)}]
    profile_md = _format_profile(ctx.get("profile") or {})
    if profile_md:
        layers.append({"key": "profile", "label": "ta fiche", "body": profile_md})
    for key, label, part in (
        ("org", "readme de ton org",
         _render_init_readme("org", org_id, f"{_README_ORG_HEADER} ({ctx['org_name']})", ctx)),
        ("group", "readme de ton équipe",
         _render_init_readme("group", ctx.get("group_id"), _group_readme_header(ctx), ctx)),
        ("user", "ta note",
         _render_init_readme("user", sub, _README_USER_HEADER, ctx)),
    ):
        if part:
            layers.append({"key": key, "label": label, "body": part})
    return layers


def _block_c(sub: str | None, org_id: int | None) -> str:
    """Le bloc contexte dynamique : section de contexte résolu + fiche profil + agent
    README cumulés (org → équipe → user, variables substituées). '' si pas d'org."""
    return "\n\n".join(l["body"] for l in _c_layers(sub, org_id))


def _group_readme_header(ctx: dict) -> str:
    """En-tête du readme d'équipe — suffixé du nom d'équipe s'il est connu."""
    name = f" ({ctx['group_name']})" if ctx.get("group_name") else ""
    return f"{_README_GROUP_HEADER}{name}"


def _render_init_readme(scope: str, owner_id, header: str, ctx: dict) -> str:
    """Un readme « init » d'un scope (org/group/user) : corps `guide_store.init_guide_body`
    variables substituées, sous `header`. '' si pas d'owner (owner_id falsy) ou corps
    vide — un scope absent est simplement omis du cumul. Source unique des ex-
    `_format_{org,group,user}_readme` (ADR 0042 : guide = primitif uniforme par scope)."""
    if not owner_id:
        return ""
    from . import guide_store
    body = guide_store.init_guide_body(scope, owner_id)
    if not body:
        return ""
    return f"{header}\n\n{_apply_vars(body, ctx)}"


def _org_readme_only(org_id: int) -> str:
    """Fallback : le README d'org seul (sans section de contexte, sans variables), si
    la résolution du contexte a échoué mais qu'on peut encore le lire."""
    from . import guide_store
    body = guide_store.init_guide_body("org", org_id)
    if not body:
        return ""
    try:
        from . import org_store
        name = (org_store.get_org(org_id) or {}).get("name") or f"#{org_id}"
    except Exception:
        name = f"#{org_id}"
    return f"{_README_ORG_HEADER} ({name})\n\n{body}"


# --- Composition ------------------------------------------------------------

def render() -> str:
    """Surface STATIQUE (constantes seules, aucun accès DB) : bloc A (secret sauce +
    catalogue dérivé). Défaut de boot `FastMCP(instructions=…)` et fallback ultime."""
    return f"{_SECRET_SAUCE.strip()}\n\n{_catalog()}"


def _socle_for(sub: str | None) -> tuple[str, str]:
    """Le socle injecté à ce compte, et son étiquette — `(corps, label)`.

    Un compte d'un tenant tiers reçoit LE SOCLE DE SON TENANT s'il en existe un. Sans
    ce cran, l'assistant d'un partenaire se présente sous notre marque et renvoie vers
    nos adresses : constaté chez un client le 13/08 (« Sur Tulina (Oto), tu es… » +
    un lien vers notre tableau de bord). Ce n'est pas un défaut de formulation — le
    texte est au niveau plateforme alors qu'il décrit un produit.

    Fail-open à trois détentes, parce que ce chemin est celui du handshake : pas de
    tenant, pas de ligne, ou lecture en erreur ⟹ le socle plateforme, à l'octet près.
    """
    plateforme = (_platform_block(KEY_SECRET_SAUCE, _SECRET_SAUCE), "socle oto")
    if not sub:
        return plateforme
    try:
        from . import guide_store, tenancy
        registre = tenancy.current()
        slug = registre.tenant_of(sub)
        if not slug or slug == tenancy.PRIMARY_SLUG:
            return plateforme
        corps = guide_store.init_guide_body("tenant", slug)
        if not corps:
            return plateforme
        nom = next((e.name for e in registre.entries()
                    if e.slug == slug and e.name), slug)
        return corps, f"socle {nom}"
    except Exception:  # noqa: BLE001 — le handshake ne casse jamais là-dessus
        logger.warning("socle de tenant illisible pour %s (fail-open)", sub,
                       exc_info=True)
        return plateforme


def session_layers(sub: str | None, org_id: int | None) -> list[dict]:
    """L'artefact injecté DÉCOMPOSÉ en couches ordonnées `[{key, label, body}]` :
    bloc A (socle plateforme + catalogue dérivé) puis couches du bloc C. Invariant :
    `"\\n\\n".join(bodies) == compose_session(sub, org_id)` — sert la vue de
    transparence (`/api/me/agent-context`) sans dupliquer la composition."""
    socle, label = _socle_for(sub)
    return [
        {"key": "platform", "label": label, "body": socle},
        {"key": "catalog", "label": "catalogue des capacités", "body": _catalog()},
    ] + _c_layers(sub, org_id)


def compose_session(sub: str | None, org_id: int | None) -> str:
    """L'artefact injecté pour UNE session : bloc A (toujours) + bloc C (contexte +
    doctrine, si org). Runtime. Fail-open géré dans chaque bloc (un bloc qui échoue
    retombe sur son seed / est omis)."""
    return "\n\n".join(l["body"] for l in session_layers(sub, org_id) if l["body"])


def compose_published_project(project_id: int) -> str | None:
    """L'artefact servi au DESTINATAIRE d'un endpoint de projet publié (ADR 0032).

    Ce n'est PAS `compose_session` : le destinataire est un tiers sans compte. Lui
    injecter le socle plateforme lui sert un mode d'emploi d'outils qu'il n'a pas (et
    nous expose ~12 Ko de vocabulaire interne, feedback #309), pendant qu'il n'a AUCUN
    chemin vers ce que le projet contient — ni poussé, ni tiré (`oto_doc`/`oto_search`
    ne résolvent pas sans `sub`). On sert donc la prose publiée du projet, et rien
    d'autre. `brief_md` n'est jamais servi : il est interne par construction.

    None = pas de prose publiée → l'appelant laisse la surface statique en place.
    """
    try:
        from . import db
        row = db.get_project_by_id(int(project_id)) or {}
    except Exception:  # noqa: BLE001 — fail-open, surface statique
        return None
    if not row:
        return None
    name = (row.get("name") or "").strip()
    header = f"# {name}\n\n" if name else ""
    body = (row.get("mcp_instructions_md") or "").strip()
    if body:
        return header + body
    # Rien de publié : on sert le strict minimum plutôt que le socle plateforme —
    # 12 Ko de vocabulaire interne chez un tiers, pour des outils qu'il n'a pas.
    return (header + "Cet espace a été partagé avec toi. Les outils listés ci-dessous "
            "sont les seuls disponibles : appelle-les pour découvrir ce qu'il contient. "
            "Son propriétaire n'a pas publié de mode d'emploi.")


def default_block(key: str) -> str:
    """Le défaut (seed constant) du bloc plateforme — sert la surface admin à afficher
    le contenu effectif quand la DB n'a pas (encore) de ligne."""
    return {KEY_SECRET_SAUCE: _SECRET_SAUCE}.get(key, "").strip()


def seed_platform_blocks() -> None:
    """No-op (ADR 0042) : la prose init plateforme vit dans `guides` (delivery='init').
    Aucun seed DB — la constante `_SECRET_SAUCE` reste le défaut/fallback (`_platform_block`
    y retombe quand `guides` n'a pas de ligne), et le backfill au boot copie l'override
    admin existant (ex-`platform_instructions`) dans `guides`. Conservée (appelée au boot
    par `server._build_mcp`) pour ne pas toucher le chemin de démarrage."""


def skills_index_md(org_id: int | None) -> str:
    """Index markdown des doctrines NOMMÉES (skills) d'une org — `slug — titre :
    description`, SANS les corps. Sert à enrichir DYNAMIQUEMENT la description de
    l'outil `oto_procedure` au `tools/list` (les skills ne sont PAS des outils →
    absents de `tools/list`, donc invisibles sans ça). Fail-open : '' si pas d'org /
    aucune doctrine / erreur."""
    if org_id is None:
        return ""
    try:
        from . import org_store
        rows = org_store.list_instructions(org_id)   # exclut la base (claude_md)
    except Exception:
        logger.warning("skills_index_md: lecture org=%s échouée (fail-open)",
                       org_id, exc_info=True)
        return ""
    if not rows:
        return ""
    lines = ["Doctrines nommées de ton org (passe le `slug` pour charger le corps) :"]
    for r in rows:
        desc = (r.get("description") or "").strip()
        lines.append(f"- {r['slug']} — {r['title']}" + (f" : {desc}" if desc else ""))
    return "\n".join(lines)
