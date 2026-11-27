"""Les SECTIONS d'une app, retirées du HTML au serveur (`docs/apps.md` § Rôles d'une app).

Un rôle d'app déclare les sections qu'il ouvre (`roles.<r>.sections`, `apps_roles.py`).
L'auteur marque le HTML : tout élément qui porte `data-oto-section="nom"` (ou une
liste de noms séparés par des blancs : visible si le rôle en ouvre UN) n'appartient
qu'à ces sections. Servi à un lecteur borné par un rôle (`apps._view`, op=get), le HTML
perd **l'élément entier** de chaque section que ce rôle n'ouvre pas — balise, contenu,
`<script>` intérieurs compris : il ne part pas, même en « afficher la source ».

- Rôle sans `sections` (None = tout) : rien n'est retiré. `sections: []` : tout élément
  marqué part. Lecteur sans rôle (l'app n'en déclare pas, ou il peut la modifier) :
  rien n'est retiré.
- Sans marque, le HTML est rendu **tel quel** (le même objet) ; avec, seules les
  tranches retirées changent : le reste est recopié à l'octet.

**Comment on trouve la fin d'un élément marqué — fermé du côté sûr.** Un lecteur
maison, linéaire, qui suit le découpage du tokenizer HTML (commentaires et leurs
fermetures abruptes, `<!…>` / `<?…>` / `</…` faux commentaires, valeurs d'attribut
entre guillemets qui contiennent `>`, texte brut de `script` avec ses états
« échappés », `style`, `textarea`, `title`, `xmp`, `iframe`, `noembed`, `noframes`,
`noscript`, `plaintext`, CDATA et `/>` dans SVG/MathML). Il ne rejoue PAS la
construction de l'arbre : un élément marqué ne finit qu'à SA balise fermante, au même
niveau d'imbrication du même nom, et **jamais par la fermeture d'un ancêtre** (le
navigateur ferme souvent plus tôt, rarement plus tard : `<div><p><div marqué>x</p>S</div>`
garde S dans le div marqué). Une fermeture n'est pas comptée tant qu'un tableau, une
cellule, un `template`, un `button`, un `select`, un `object`, un SVG… ouvert DANS
l'élément reste ouvert (le navigateur l'ignorerait). Jamais fermé : **tout jusqu'à la
fin du document part**. `html`, `head`, `body` marqués : jusqu'à la fin aussi. Retirer
trop est un défaut d'affichage ; retirer trop peu serait une fuite.

**Ce qui n'est PAS protégé** : le JavaScript et les données hors d'un élément marqué
(une constante dans un `<script>` non marqué, un gabarit HTML construit en JS, le CSS)
— le texte d'un script n'est jamais lu comme du balisage. Le contenu d'un `noscript`,
`noembed`, `noframes`, `iframe` et d'un `<script type="text/template|…html…">` est,
lui, relu (un niveau) et ses éléments marqués retirés.
"""
from __future__ import annotations

import html as _html
import re
from typing import Callable, Optional

ATTRIBUT = "data-oto-section"
MAX_AVERTISSEMENTS = 10

_ASCII_BAS = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_MARQUE = re.compile(re.escape(ATTRIBUT), re.I)
_BLANCS = re.compile(r"[\t\n\f\r ]*")
_NOM = re.compile(r"[^\t\n\f\r />]*")
_ATTR = re.compile(r"""([^\t\n\f\r />][^\t\n\f\r /=>]*)(?:[\t\n\f\r ]*=[\t\n\f\r ]*"""
                   r"""(?:"([^"]*)"|'([^']*)'|(?!["'])([^\t\n\f\r >]*)))?""")
_FIN_COMMENTAIRE = re.compile(r"--!?>")
_JALONS_SCRIPT = re.compile(r"<!--|-->|<(/?)script(?=[\t\n\f\r />])", re.I)
_FIN_BRUT: dict[str, re.Pattern] = {}

VIDES = frozenset({"area", "base", "basefont", "bgsound", "br", "col", "embed", "frame",
                   "hr", "image", "img", "input", "keygen", "link", "meta", "param",
                   "source", "track", "wbr"})
BRUTS = frozenset({"script", "style", "textarea", "title", "xmp", "iframe", "noembed",
                   "noframes", "noscript", "plaintext"})
# Contenu jamais exécuté comme du JS : relu, un niveau, pour ses propres marques.
_RELUS = frozenset({"noscript", "noembed", "noframes", "iframe"})
# Ouverts DANS un élément marqué, ils font ignorer au navigateur la fermeture de
# l'élément tant qu'ils restent ouverts (portée) : on ne la compte pas non plus.
BARRIERES = frozenset({"table", "caption", "colgroup", "tbody", "thead", "tfoot", "tr",
                       "td", "th", "template", "button", "select", "object", "applet",
                       "marquee", "svg", "math", "foreignobject"})
# Les éléments « spéciaux » du HTML : la fermeture d'un élément ORDINAIRE (`span`, `a`,
# `b`, un élément maison…) est ignorée tant que l'un d'eux, ouvert dedans, reste ouvert.
SPECIAUX = BARRIERES | frozenset({
    "address", "article", "aside", "blockquote", "body", "center", "dd", "details", "dir",
    "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "frameset",
    "h1", "h2", "h3", "h4", "h5", "h6", "head", "header", "hgroup", "html", "li", "listing",
    "main", "menu", "nav", "ol", "p", "pre", "search", "section", "summary", "ul",
    "desc", "title", "mi", "mo", "mn", "ms", "mtext", "annotation-xml"})
# Des éléments marqués imbriqués et encore ouverts : au-delà, tout part jusqu'à la fin.
MAX_PILES = 32
# La profondeur suivie (une pile, les SVG/MathML ouverts) : au-delà, une zone part
# jusqu'à la fin et un SVG de plus n'est plus suivi — la lecture reste linéaire.
MAX_PROFONDEUR = 512
_JUSQU_A_LA_FIN = frozenset({"html", "head", "body"})
# Une de ces balises ouvrantes dans SVG/MathML ramène au HTML (« breakout »).
_SORTIES = frozenset({"b", "big", "blockquote", "body", "br", "center", "code", "dd", "div",
                      "dl", "dt", "em", "embed", "h1", "h2", "h3", "h4", "h5", "h6", "head",
                      "hr", "i", "img", "li", "listing", "menu", "meta", "nobr", "ol", "p",
                      "pre", "ruby", "s", "small", "span", "strong", "strike", "sub", "sup",
                      "table", "tt", "u", "ul", "var"})
_INTEGRATION = {"svg": frozenset({"foreignobject", "desc", "title"}),
                "math": frozenset({"mi", "mo", "mn", "ms", "mtext", "annotation-xml"})}
_NS_NOMS = frozenset({"svg", "math"}) | _INTEGRATION["svg"] | _INTEGRATION["math"]


def _bas(s: str) -> str:
    return s.translate(_ASCII_BAS)


def _balise(s: str, i: int, fermante: bool):
    """La balise qui commence en `s[i]` (`<x` ou `</x`) : (fin, nom, attributs,
    auto-fermante) ; None si le texte s'arrête dans la balise."""
    n = len(s)
    m = _NOM.match(s, i + (2 if fermante else 1))
    nom, p = _bas(m.group(0)), m.end()
    attrs: list[tuple[str, str]] = []
    while True:
        p = _BLANCS.match(s, p).end()
        if p >= n:
            return None
        c = s[p]
        if c == ">":
            return p + 1, nom, attrs, False
        if c == "/":
            if p + 1 < n and s[p + 1] == ">":
                return p + 2, nom, attrs, True
            p += 1
            continue
        a = _ATTR.match(s, p)
        p = a.end()
        dq, sq, nu = a.group(2), a.group(3), a.group(4)
        if dq is None and sq is None and nu is None:
            q = _BLANCS.match(s, p).end()
            if q < n and s[q] == "=":
                return None     # une valeur entre guillemets jamais refermée
            val = ""
        else:
            val = dq if dq is not None else sq if sq is not None else nu
        attrs.append((_bas(a.group(1)), val))


def _fin_brut(s: str, p: int, nom: str) -> int:
    """L'indice du `</nom` qui clôt le texte brut ouvert en `p`, -1 s'il n'y en a pas."""
    if nom == "plaintext":
        return -1
    if nom != "script":
        rx = _FIN_BRUT.get(nom)
        if rx is None:
            rx = _FIN_BRUT[nom] = re.compile(r"</" + nom + r"(?=[\t\n\f\r />])", re.I)
        m = rx.search(s, p)
        return m.start() if m else -1
    # Les états « script data » : `<!--` échappe, `<script` y double l'échappement,
    # où `</script` ne ferme pas ; `-->` revient au texte ordinaire.
    etat, n = 0, len(s)
    for m in _JALONS_SCRIPT.finditer(s, p):
        t = m.group(0)
        if t == "<!--":
            k = m.end()
            while k < n and s[k] == "-":
                k += 1
            if k < n and s[k] == ">":
                etat = 0
            elif etat == 0:
                etat = 1
        elif t == "-->":
            etat = 0
        elif m.group(1):            # </script
            if etat == 2:
                etat = 1
            else:
                return m.start()
        elif etat == 1:             # <script
            etat = 2
    return -1


class _Pile:
    """Un élément marqué ouvert dans la zone : son nom, ses barrières, et ce qui est
    ouvert depuis (ses homonymes et ses barrières seulement), compté pour un test
    d'appartenance en temps constant."""
    __slots__ = ("tag", "barr", "noms", "compte")

    def __init__(self, nom: str) -> None:
        self.tag, self.noms, self.compte = nom, [nom], {nom: 1}
        self.barr = BARRIERES if nom in SPECIAUX else SPECIAUX

    def ouvre(self, nom: str) -> bool:
        if nom != self.tag and nom not in self.barr:
            return False
        self.noms.append(nom)
        self.compte[nom] = self.compte.get(nom, 0) + 1
        return len(self.noms) > MAX_PROFONDEUR

    def ferme(self, nom: str) -> None:
        if not self.compte.get(nom):
            return
        if nom in self.barr:
            while True:     # jusqu'au plus proche homonyme, compris
                x = self.noms.pop()
                self.compte[x] -= 1
                if x == nom:
                    return
        elif self.noms[-1] == nom:
            self.noms.pop()
            self.compte[nom] -= 1


def _noms(attrs: list[tuple[str, str]]) -> Optional[list[list[str]]]:
    """Les noms de section de chaque occurrence de l'attribut, None sans attribut."""
    occ = [_html.unescape(v).split() for k, v in attrs if k == ATTRIBUT]
    return occ or None


class _Parcours:
    def __init__(self) -> None:
        self.coupes: list[tuple[int, int, str]] = []
        self.marques: list[tuple[int, list[list[str]]]] = []
        self.non_fermes: list[tuple[int, str]] = []


def _parcourir(s: str, ouvre: Optional[Callable[[list[list[str]]], bool]],
               relu: bool = False) -> _Parcours:
    """Un passage linéaire sur `s`. `ouvre(noms)` dit si un élément marqué reste ;
    None = tout reste (on relève seulement les marques)."""
    out = _Parcours()
    n = len(s)
    ns: list[str] = []          # svg / math / points d'intégration ouverts
    # Dans un `select`, le navigateur ignore `style`, `title`, `iframe`, `xmp`… : seul `script`
    # y passe en texte brut ; `input`, `keygen`, `textarea` le ferment.
    dans_select = False
    # L'élément marqué en cours de retrait : son début, et une PILE par élément marqué
    # ouvert dedans (lui compris) — chacune ne compte que son nom et ses barrières ; la
    # zone finit quand toutes sont vides (un élément marqué intérieur resté ouvert, le
    # navigateur peut le prolonger ou le recloner après la fin de l'élément).
    zone: Optional[dict] = None
    i = 0

    def etranger() -> bool:
        return bool(ns) and ns[-1] in ("svg", "math")

    def fin_zone(fin: int) -> None:
        nonlocal zone
        out.coupes.append((zone["debut"], fin, ""))
        zone = None

    while True:
        j = s.find("<", i)
        if j < 0 or j + 1 >= n:
            break
        c = s[j + 1]
        if c == "!":
            if s.startswith("<!--", j):
                k = j + 4
                if s.startswith(">", k):
                    i = k + 1
                elif s.startswith("->", k):
                    i = k + 2
                else:
                    m = _FIN_COMMENTAIRE.search(s, k)
                    i = m.end() if m else n
                continue
            if etranger() and s.startswith("<![CDATA[", j):
                k = s.find("]]>", j + 9)
                i = k + 3 if k >= 0 else n
                continue
            k = s.find(">", j + 2)
            i = k + 1 if k >= 0 else n
            continue
        if c == "?":
            k = s.find(">", j + 2)
            i = k + 1 if k >= 0 else n
            continue
        if c == "/":
            if j + 2 >= n:
                break
            d = s[j + 2]
            if d == ">":
                i = j + 3
                continue
            if not ("a" <= d <= "z" or "A" <= d <= "Z"):
                k = s.find(">", j + 2)
                i = k + 1 if k >= 0 else n
                continue
            r = _balise(s, j, True)
            if r is None:
                break
            fin, nom, _, _ = r
            if nom in _NS_NOMS and nom in ns:
                del ns[len(ns) - 1 - ns[::-1].index(nom):]
            if nom == "select":
                dans_select = False
            if zone is not None:
                for pile in zone["piles"]:
                    pile.ferme(nom)
                zone["piles"] = [x for x in zone["piles"] if x.noms]
                if not zone["piles"]:
                    fin_zone(fin)
            i = fin
            continue
        if not ("a" <= c <= "z" or "A" <= c <= "Z"):
            i = j + 1
            continue

        r = _balise(s, j, False)
        if r is None:
            # Le texte s'arrête dans la balise : le navigateur la jette ; marquée, elle
            # part aussi (la source, elle, se lit).
            if zone is None and ouvre is not None and _MARQUE.search(s, j):
                out.coupes.append((j, n, ""))
            break
        fin, nom, attrs, auto = r
        # L'espace de noms de l'élément, comme le navigateur le choisit.
        if etranger():
            if nom in _SORTIES or (nom == "font" and any(
                    k in ("color", "face", "size") for k, _ in attrs)):
                while etranger():
                    ns.pop()
                est_etranger = False
            else:
                est_etranger = True
        else:
            est_etranger = nom in ("svg", "math")
        ferme_seul = nom in VIDES or (auto and est_etranger)
        if est_etranger and not ferme_seul:
            ctx = next((x for x in reversed(ns) if x in ("svg", "math")), None)
            if nom in ("svg", "math") or (ctx and nom in _INTEGRATION[ctx]):
                if len(ns) < MAX_PROFONDEUR:
                    ns.append(nom)
        if not est_etranger:
            if dans_select and nom in ("select", "input", "keygen", "textarea"):
                dans_select = False
            elif nom == "select":
                dans_select = True
        brut = (not est_etranger and nom in BRUTS and not ferme_seul
                and not (relu and nom in _RELUS)
                and not (dans_select and nom != "script"))
        fin_contenu = _fin_brut(s, fin, nom) if brut else None
        apres = fin
        if brut:
            if fin_contenu < 0:
                apres = n
            else:
                r2 = _balise(s, fin_contenu, True)
                apres = r2[0] if r2 else n

        noms = _noms(attrs)
        if noms is not None and zone is None:
            out.marques.append((j, noms))
        if zone is None and noms is not None and ouvre is not None and not ouvre(noms):
            if ferme_seul:
                out.coupes.append((j, fin, ""))
                i = fin
            elif brut:
                out.coupes.append((j, apres, ""))
                if fin_contenu < 0:
                    out.non_fermes.append((j, nom))
                i = apres
            elif nom in _JUSQU_A_LA_FIN:
                out.coupes.append((j, n, ""))
                out.non_fermes.append((j, nom))
                return out
            else:
                zone = {"debut": j, "nom": nom, "piles": [_Pile(nom)]}
                i = fin
            continue

        if zone is not None:
            if not ferme_seul and not brut:
                trop = False
                for pile in zone["piles"]:
                    trop = pile.ouvre(nom) or trop
                if trop:
                    out.non_fermes.append((zone["debut"], zone["nom"]))
                    fin_zone(n)
                    return out
                if noms is not None and ouvre is not None and not ouvre(noms):
                    if nom in _JUSQU_A_LA_FIN or len(zone["piles"]) >= MAX_PILES:
                        out.non_fermes.append((zone["debut"], zone["nom"]))
                        fin_zone(n)
                        return out
                    zone["piles"].append(_Pile(nom))
        elif brut and not relu and fin_contenu is not None and ouvre is not None:
            # Un contenu jamais exécuté comme du JS : relu pour ses propres marques.
            typ = next((_bas(v) for k, v in attrs if k == "type"), "")
            if nom in _RELUS or (nom == "script" and ("template" in typ or "html" in typ)):
                bout = fin_contenu if fin_contenu >= 0 else n
                dedans = s[fin:bout]
                if _MARQUE.search(dedans):
                    neuf = retirer_avec(dedans, ouvre, relu=True)
                    if neuf is not dedans:
                        out.coupes.append((fin, bout, neuf))
        i = apres if brut else fin

    if zone is not None:
        out.non_fermes.append((zone["debut"], zone["nom"]))
        fin_zone(n)
    return out


def retirer_avec(html: str, ouvre: Callable[[list[list[str]]], bool],
                 relu: bool = False) -> str:
    p = _parcourir(html, ouvre, relu)
    if not p.coupes:
        return html
    morceaux, fin = [], 0
    for a, b, neuf in p.coupes:
        morceaux += [html[fin:a], neuf]
        fin = b
    morceaux.append(html[fin:])
    return "".join(morceaux)


def retirer(html: Optional[str], sections: Optional[list[str]]) -> Optional[str]:
    """`html` tel que le voit un rôle qui ouvre `sections` (None = toutes : rendu tel
    quel). Un élément marqué reste si, pour CHAQUE `data-oto-section` qu'il porte, le
    rôle ouvre l'un des noms ; une marque vide n'est ouverte à aucun rôle."""
    if sections is None or not html or not _MARQUE.search(html):
        return html
    ouverts = set(sections)
    return retirer_avec(html, lambda occ: all(ouverts.intersection(x) for x in occ))


def avertissements(manifest: dict, html: Optional[str]) -> list[str]:
    """À l'écriture (`apps._avertir`) : les sections que les rôles ouvrent sans
    qu'aucun élément ne les porte, les marques qu'aucun rôle ne déclare, et les éléments
    marqués jamais fermés. Jamais un refus."""
    roles = (manifest or {}).get("roles") or {}
    declarees: dict[str, list[str]] = {}
    for r, spec in roles.items():
        for x in (spec or {}).get("sections") or []:
            declarees.setdefault(x, []).append(r)
    if not html or (not declarees and not _MARQUE.search(html)):
        return []
    ligne = lambda pos: html.count("\n", 0, pos) + 1  # noqa: E731
    marques = _parcourir(html, None).marques if _MARQUE.search(html) else []
    portees: dict[str, int] = {}
    vides: list[int] = []
    for pos, occ in marques:
        for noms in occ:
            if not noms:
                vides.append(pos)
            for x in noms:
                portees.setdefault(x, pos)
    out: list[str] = []
    if not roles:
        if marques:
            out.append(f"The HTML marks sections ({', '.join(sorted(portees)) or 'empty'}) "
                       f"with {ATTRIBUT}, but the manifest declares no `roles`: nothing is "
                       "removed, every viewer gets the whole HTML.")
        return out
    for x in sorted(set(declarees) - set(portees)):
        out.append(f'Role {", ".join(declarees[x])} opens section "{x}", but no element '
                   f'carries {ATTRIBUT}="{x}": whatever the page hides for it in script '
                   "still ships to every viewer. Mark that markup with "
                   f'{ATTRIBUT}="{x}" so it is removed for the other roles.')
    for x in sorted(set(portees) - set(declarees), key=portees.get):
        out.append(f'Line {ligne(portees[x])}: {ATTRIBUT}="{x}" names a section no role '
                   "declares: it is removed for every role that lists `sections`.")
    for pos in vides[:1]:
        out.append(f"Line {ligne(pos)}: an empty {ATTRIBUT} is removed for every role "
                   "that lists `sections`.")
    for pos, nom in _parcourir(html, lambda occ: False).non_fermes:
        out.append(f"Line {ligne(pos)}: <{nom} {ATTRIBUT}> is never closed: for a role that "
                   f"doesn't open it, everything after it is removed. Close it with </{nom}>.")
    return out[:MAX_AVERTISSEMENTS]
