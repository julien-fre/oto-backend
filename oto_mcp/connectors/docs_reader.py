"""Doc « how-to » user-facing des connecteurs — un MARKDOWN par connecteur.

Le contenu vit dans `connectors/docs/<nom>.md`, à côté du code, éditable sans toucher
à Python. C'était auparavant un dict de 850 lignes ici même : écrire de la prose dans
des chaînes Python décourage de la tenir à jour, et ça s'est vu — la doc Salesforce
décrivait encore un modèle d'application que Salesforce a depuis désactivé.

**Format.** Un fichier = un connecteur, nommé comme lui (`tools/<nom>.py` ⟷
`connectors/docs/<nom>.md`). Chaque section est un titre de niveau 2 :

    ## <kind> — <titre>

    corps en markdown léger

`kind` ∈ {prerequisite, setup, usage, note} :
- `prerequisite` — ce qu'il faut AVANT de connecter (où prendre la clé, une
  autorisation à poser côté fournisseur…). Affiché avant connexion ;
- `setup`        — étapes de configuration ;
- `usage`        — ce que le connecteur permet + exemples concrets. Affiché aussi en
  découverte (marketplace, vitrine) ;
- `note`         — divers.

**Corps** = markdown léger : `[label](url)` (http(s) seulement, sinon rendu en texte),
`**gras**`, `` `code` ``, listes `- `. Rester FACTUEL : décrire ce que font réellement
les outils, et lier la page de doc de l'éditeur plutôt qu'inventer un chemin d'UI
exact — les consoles SaaS bougent, et un chemin périmé envoie l'utilisateur dans le mur.

**Valeurs dérivées** : `{{callback:/chemin}}`, résolu à la lecture (cf. `_resoudre`).

Le catalogue public et `/api/me/connectors` en dérivent (`Connector.doc_sections`) ;
c'est rendu partout où le connecteur s'affiche — carte de connexion, marketplace,
vitrine.
"""
from __future__ import annotations

import functools
import logging
import pathlib
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_DIR = pathlib.Path(__file__).parent / "docs"

KINDS = ("prerequisite", "setup", "usage", "note")

# `## kind — titre` (tiret cadratin ou simple : les deux passent à la saisie).
_TITRE = re.compile(r"^##\s+(" + "|".join(KINDS) + r")\s*[—-]\s*(.+?)\s*$")
# Un titre de section qui RESSEMBLE au patron mais dont le premier mot n'est pas un
# kind connu. Sans lui, `## plusieurs workspaces — …` ne matchait rien : la ligne
# tombait dans le CORPS de la section précédente, `##` compris, et le texte
# s'affichait au mauvais endroit — dans le prérequis, montré avant connexion. Vécu
# le 2026-08-27 sur la doc Slack. Un titre mal nommé se signale, il ne s'avale pas.
_TITRE_SUSPECT = re.compile(r"^##\s+([a-zA-Z][\w -]*?)\s*[—-]\s*(.+?)\s*$")
_MARQUEUR = re.compile(r"\{\{callback:([^}]+)\}\}")


@dataclass(frozen=True)
class DocSection:
    kind: str            # prerequisite | setup | usage | note
    title: str
    body_md: str


def _resoudre(corps: str) -> str:
    """Remplace les marqueurs `{{callback:/chemin}}` par leur valeur DÉRIVÉE.

    Une doc ne doit JAMAIS écrire une URL de rappel en dur : elle dépend de
    l'environnement, donc une URL de prose ment dès qu'on la lit depuis l'autre. Bug
    vécu : la doc de deux connecteurs affichait le domaine de PREPROD à des
    utilisateurs de production, et le `redirect_uri_mismatch` qui s'ensuivait accusait
    le client. Résolu à la LECTURE, jamais à l'import."""
    if "{{callback:" not in corps:
        return corps
    from ..auth import flow as oauth_flow
    return _MARQUEUR.sub(lambda m: oauth_flow.redirect_uri(m.group(1)), corps)


def _parse(texte: str, source: str) -> tuple[DocSection, ...]:
    sections: list[DocSection] = []
    kind: str | None = None
    titre = ""
    corps: list[str] = []

    def _fermer() -> None:
        if kind is not None:
            sections.append(DocSection(kind, titre, "\n".join(corps).strip()))

    for ligne in texte.splitlines():
        m = _TITRE.match(ligne)
        if m:
            _fermer()
            kind, titre, corps = m.group(1), m.group(2), []
            continue
        suspect = _TITRE_SUSPECT.match(ligne)
        if suspect and suspect.group(1) not in KINDS:
            logger.warning(
                "connectors/docs/%s : section `%s` — kind inconnu, la section entière "
                "part dans le corps de la précédente. Attendus : %s",
                source, suspect.group(1), ", ".join(KINDS))
        if kind is not None:
            corps.append(ligne)
        elif ligne.strip():
            # Texte avant tout titre : il ne serait affiché nulle part. On le signale
            # plutôt que de le laisser disparaître en silence.
            logger.warning("connectors/docs/%s : texte hors section, ignoré : %.60s",
                           source, ligne.strip())
    _fermer()
    return tuple(sections)


@functools.lru_cache(maxsize=1)
def _fichiers() -> dict[str, tuple[DocSection, ...]]:
    """Lu une fois par processus : le contenu est statique, livré avec le code."""
    out: dict[str, tuple[DocSection, ...]] = {}
    if not _DIR.is_dir():
        logger.warning("connectors/docs/ absent : les fiches seront sans doc")
        return out
    for f in sorted(_DIR.glob("*.md")):
        sections = _parse(f.read_text(encoding="utf-8"), f.name)
        if sections:
            out[f.stem] = sections
    return out


def sections_for(connector: str) -> tuple[DocSection, ...]:
    """Sections du connecteur, marqueurs résolus. Vide si aucune doc."""
    return tuple(DocSection(s.kind, s.title, _resoudre(s.body_md))
                 for s in _fichiers().get(connector, ()))


class _Vue(dict):
    """`DOC_SECTIONS[nom]`, `in`, `.get()` marchent comme avec l'ancien dict — mais les
    valeurs sont résolues À LA LECTURE : l'URL de rappel dépend de l'environnement et
    ne peut pas être figée au chargement du module."""

    def __getitem__(self, k):
        s = sections_for(k)
        if not s:
            raise KeyError(k)
        return s

    def get(self, k, default=None):
        return sections_for(k) or default

    def __contains__(self, k):
        return k in _fichiers()

    def __iter__(self):
        return iter(_fichiers())

    def __len__(self):
        return len(_fichiers())

    def keys(self):
        return _fichiers().keys()

    def items(self):
        return ((n, sections_for(n)) for n in _fichiers())

    def values(self):
        return (sections_for(n) for n in _fichiers())


DOC_SECTIONS = _Vue()


def multi_account_section(connector: str, noun: str) -> DocSection:
    """La section « plusieurs <noun>s » d'un connecteur multi-compte — ÉCRITE UNE
    FOIS pour tous : la mécanique (compte nommé, défaut, `_account`, refus en cas
    d'ambiguïté) est celle de la plateforme, pas celle d'un fournisseur. Une fiche
    n'écrit que ce qui lui est propre (pourquoi il faut une clé par <noun>)."""
    return DocSection("setup", f"plusieurs {noun}s", "\n".join((
        f"ce connecteur accepte plusieurs {noun}s : chaque credential posé devient un "
        f"**compte nommé** (un nom par {noun}), à ton niveau, à celui de ton équipe ou "
        f"de ton org.",
        "- le premier n'a pas besoin de nom ; à partir du deuxième, chacun porte le sien "
        "(le premier prend alors le nom « principal », renommable)",
        f"- sans précision, l'agent prend le seul {noun} posé, sinon celui marqué par "
        f"défaut ; sinon l'appel est **refusé** en nommant les {noun}s disponibles — "
        f"jamais un choix au hasard",
        f"- viser un {noun} pour un appel : `_account=\"<nom>\"` sur l'outil ; les lister : "
        f"`oto_identity(op='list', connector='{connector}')` (`scope='org'` ou "
        f"`scope='group'` pour ceux de l'org ou de l'équipe)",
        f"- fixer le défaut : `oto_identity(op='set', connector='{connector}', "
        f"identity_id='<nom>')` ; renommer : `op='rename'` avec `new_name`",
        f"- ⚠️ aucun outil ne parcourt les {noun}s tout seul : un total sur plusieurs "
        f"{noun}s s'obtient en appelant chacun (`_account`) et en additionnant",
    )))
