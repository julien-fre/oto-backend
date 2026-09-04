"""Les adresses de contact qu'un rendu markdown fait DISPARAÎTRE (signal #681).

Mesuré le 03/09/2026, en appelant le scraper hébergé (Serper) sur trois pages
réelles : il rend 200, un markdown propre de plusieurs milliers de caractères —
et **zéro adresse**, alors que le HTML en porte une, lisible à l'œil nu. Trois
motifs, tous relevés à la source :

  - `<joomla-hidden-mail text="cHJlc2lkZW50ZUBsYXZvaXhkZXNsaXZyZXMuZnI=">`
    (lavoixdeslivres.fr/index.php/l-association) — l'adresse est en base64 dans
    un ATTRIBUT ; un rendu qui ne garde que le texte ne peut rien en montrer ;
  - `mailto:&#115;&#116;ran…&#064;&#103;&#109;ail&#046;com`
    (stranumundueditions.wordpress.com) — entités décimales dans le href ;
  - `<span class="__cf_email__" data-cfemail="7f13100a…">`
    (association.lourugby.fr/rugby-loisir) — Cloudflare, XOR sur le 1ᵉʳ octet.
    Celui-là n'est pas silencieux, il est MENTEUR : le rendu affiche le texte
    littéral `[email protected]`, qu'aucune regex d'adresse ne reconnaît.

Un outil qui rend « rien » là où il y a quelque chose fabrique une affirmation
fausse chez un agent parfaitement honnête : il a ouvert la page, il n'a rien vu,
il l'écrit. Sur le palier de contrôle du 03/09, 8 fiches sur 23 portaient un
faux négatif de contact, dont 4 imputables à l'outil — cinq entreprises actives
classées « indéterminé », deux écartées du fichier.

⚠️ Le décodage a besoin du HTML, et le scraper hébergé n'en rend PAS : sa
réponse ne porte que `text`, `markdown`, `metadata`, `jsonld`, `credits`
(vérifié le 03/09 sur l'API). L'information n'est donc pas dans ce qu'on
reçoit — il faut aller chercher la page NOUS-MÊMES. C'est pourquoi ce module
porte aussi le fetch direct (UA navigateur), qui sert les trois demandes du
signal d'un seul mécanisme : décoder, rendre le HTML brut, et se replier quand
le fournisseur refuse.
"""
from __future__ import annotations

import base64
import binascii
import html as _html
import re
from typing import Optional

# Une adresse « visible » — sert à décider si la page montre DÉJÀ un contact
# (auquel cas on ne dépense pas de requête) et à valider ce qu'on décode.
ADRESSE_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")

_JOOMLA_RE = re.compile(r"<joomla-hidden-mail\b([^>]*)>", re.I)
_ATTR_RE = re.compile(r'([A-Za-z\-]+)\s*=\s*"([^"]*)"')
# Un `mailto:` qui porte au moins une entité numérique. Une adresse en clair
# n'est pas obfusquée : elle est déjà dans le rendu, rien à récupérer.
_MAILTO_ENTITE_RE = re.compile(r"mailto:([^\"'\s<>]*&\#[^\"'\s<>]*)", re.I)
_CF_RE = re.compile(
    r'(?:data-cfemail="|/cdn-cgi/l/email-protection\#)([0-9a-fA-F]{6,})')


def contient_adresse(texte: Optional[str]) -> bool:
    """La page montre-t-elle déjà une adresse en clair ?"""
    return bool(ADRESSE_RE.search(texte or ""))


def _b64(valeur: str) -> Optional[str]:
    """Décode un attribut base64 de Joomla, ou None si ce n'en est pas un."""
    if not valeur:
        return None
    try:
        brut = base64.b64decode(valeur + "=" * (-len(valeur) % 4), validate=True)
        return brut.decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None


def _joomla(page: str) -> list:
    """`<joomla-hidden-mail first="…" last="…" text="…">`, tout en base64.

    `text` porte l'adresse entière quand le composant en affiche une ; sinon on
    recompose `first@last` — c'est la même page vue par deux attributs, et un
    site qui n'a pas de `text` (lien sans libellé) reste lisible."""
    trouvees = []
    for m in _JOOMLA_RE.finditer(page):
        attrs = {k.lower(): v for k, v in _ATTR_RE.findall(m.group(1))}
        texte = _b64(attrs.get("text", ""))
        if texte and ADRESSE_RE.fullmatch(texte.strip()):
            trouvees.append(texte.strip())
            continue
        debut, fin = _b64(attrs.get("first", "")), _b64(attrs.get("last", ""))
        if debut and fin:
            recomposee = f"{debut.strip()}@{fin.strip()}"
            if ADRESSE_RE.fullmatch(recomposee):
                trouvees.append(recomposee)
    return trouvees


def _entites(page: str) -> list:
    """`mailto:` écrit en entités HTML décimales (`&#64;`) ou hexa (`&#x40;`).

    `html.unescape` couvre les deux formes ; le `?subject=…` éventuel tombe."""
    trouvees = []
    for brut in _MAILTO_ENTITE_RE.findall(page):
        clair = _html.unescape(brut).split("?")[0].strip()
        if ADRESSE_RE.fullmatch(clair):
            trouvees.append(clair)
    return trouvees


def _cloudflare(page: str) -> list:
    """`data-cfemail` / `/cdn-cgi/l/email-protection#…` : hexa, XOR clé = 1ᵉʳ octet."""
    trouvees = []
    for hexa in _CF_RE.findall(page):
        if len(hexa) % 2:
            continue
        octets = bytes.fromhex(hexa)
        cle = octets[0]
        clair = "".join(chr(o ^ cle) for o in octets[1:])
        if ADRESSE_RE.fullmatch(clair):
            trouvees.append(clair)
    return trouvees


# (nom servi à l'agent, présence du motif, décodeur). La présence est testée à
# part du décodage : un motif VU qu'on ne sait pas décoder doit quand même se
# dire — « le pire n'est pas de ne pas décoder, c'est que la page semble ne rien
# contenir » (#681). C'est la demande n°2 du signal, celle du repli.
_MOTIFS = (
    ("joomla-hidden-mail", _JOOMLA_RE, _joomla),
    ("mailto en entités HTML", _MAILTO_ENTITE_RE, _entites),
    ("cloudflare-email-protection", _CF_RE, _cloudflare),
)


def lire(page: Optional[str]) -> dict:
    """`{adresses, motifs}` — ce que le HTML cache, et sous quelle forme.

    `motifs` liste ce qui a été VU, décodé ou non : c'est lui qui permet de
    dire « il y a une adresse ici » quand le décodage échoue."""
    page = page or ""
    adresses, motifs = [], []
    for nom, presence, decode in _MOTIFS:
        if not presence.search(page):
            continue
        motifs.append(nom)
        for adresse in decode(page):
            if adresse not in adresses:
                adresses.append(adresse)
    return {"adresses": adresses, "motifs": motifs}


# ── ce qu'on va chercher nous-mêmes ──────────────────────────────────────────
# Budget COURT et distinct de celui d'une lecture ordinaire : cette requête
# s'ajoute à un scrape déjà payé, sur le chemin chaud d'un agent. Le signal #662
# a mesuré ce que coûte une attente — pas la seconde perdue, le cache de
# contexte qui expire pendant : un plafond généreux ici rendrait le remède plus
# cher que le mal.
SONDE_DELAI_S = 8


def fetch(url: str, deadline_s: float = SONDE_DELAI_S) -> dict:
    """Le HTML de `url`, par notre propre requête, avec un UA de navigateur.

    Réutilise le cran ① de `web_read` : même garde SSRF, mêmes bornes de
    lecture, mêmes redirections marchées à la main. Refaire un fetch ici, c'est
    refaire ses bugs — celui-là a déjà payé #491.

    Rend le dict de `web._fetch_http` : `{ok, verdict, html?, final_url?}`."""
    from . import web  # tardif : `web` importe `browserbase`, inutile au register
    return web._fetch_http(url, deadline_s=deadline_s)


def marqueur(lu: dict) -> str:
    """La ligne à COLLER dans le contenu servi — vide s'il n'y a rien à dire.

    Elle va dans le markdown, pas seulement dans un champ à côté : un agent lit
    la page, et c'est là qu'il conclut « aucun contact publié »."""
    if lu.get("adresses"):
        return ("\n\n[adresses obfusquées dans le HTML, décodées par oto : "
                + ", ".join(lu["adresses"]) + "]")
    if lu.get("motifs"):
        return ("\n\n[obfuscation d'adresse détectée dans le HTML ("
                + ", ".join(lu["motifs"]) + ") mais non décodable : le rendu "
                "ci-dessus ne montre PAS tous les contacts de la page — "
                "reprends-la avec format=\"html\"]")
    return ""


# ── les trois usages du HTML qu'on est allé chercher ─────────────────────────
# Budget d'une lecture DEMANDÉE (format="html") ou d'un repli : plus généreux
# que la sonde, parce qu'elle est le seul chemin restant — mais toujours borné
# par le `timeout_s` de l'appelant s'il en a posé un.
LECTURE_DELAI_S = 20
# Le HTML brut part dans le contexte de l'agent : un plafond, et le total DIT à
# côté — un plafond posé sur une lecture déjà tronquée serait inatteignable.
HTML_MAX_CHARS = 120_000


def _refus(message: str):
    from ..mcp_errors import McpError
    from mcp.types import ErrorData, INVALID_REQUEST
    return McpError(ErrorData(code=INVALID_REQUEST, message=message))


def _hors_perimetre(final_url, per) -> None:
    """Le périmètre du projet vaut aussi sur l'URL où l'on ATTERRIT (#632)."""
    from .. import url_perimeter
    url_perimeter.refuse_if_excluded(final_url, per)


def html_brut(url: str, per, deadline_s: float = LECTURE_DELAI_S) -> dict:
    """`format="html"` — la page telle qu'elle est servie, sans le scraper.

    Passer par le scraper hébergé n'aurait servi à rien : sa réponse ne porte
    aucun champ HTML. C'est donc notre propre requête, avec un UA de
    navigateur — et zéro crédit."""
    lu = fetch(url, deadline_s=deadline_s)
    if not lu.get("ok"):
        raise _refus(
            f"Lecture directe impossible pour {url} : {lu.get('verdict')}. "
            "Le HTML brut n'a pas de repli — reprends en format=\"markdown\" "
            "pour tenter le scraper hébergé.")
    _hors_perimetre(lu.get("final_url"), per)
    page = lu["html"]
    obf = lire(page)
    sortie = {"html": page[:HTML_MAX_CHARS],
              "html_caracteres": len(page),
              "html_tronque": len(page) > HTML_MAX_CHARS,
              "final_url": lu.get("final_url"),
              "source": "lecture directe (UA navigateur), 0 crédit",
              "credits": 0}
    if obf["motifs"]:
        sortie["motifs_obfuscation"] = obf["motifs"]
    if obf["adresses"]:
        sortie["adresses_obfusquees"] = obf["adresses"]
    return sortie


def repli(url: str, per, deadline_s: float = LECTURE_DELAI_S) -> tuple:
    """Le fournisseur a refusé la page : on la lit NOUS-MÊMES. `(sortie|None, verdict)`.

    Sur le palier du 03/09, trois sites sur quatre refusés par le scraper (deux
    Wix, un WordPress.com) répondent normalement à une requête ordinaire portant
    un UA de navigateur. Le repli DIT son chemin — il ne se fait pas passer pour
    le scraper — et rend du texte, pas du markdown : on n'a pas de convertisseur
    ici, et prétendre le contraire serait pire que le dire."""
    lu = fetch(url, deadline_s=deadline_s)
    if not lu.get("ok"):
        return None, lu.get("verdict", "échec")
    _hors_perimetre(lu.get("final_url"), per)
    from .web import _EMPTY_TEXT_CHARS, extract_text
    texte, titre = extract_text(lu["html"])
    if len(texte.strip()) < _EMPTY_TEXT_CHARS:
        return None, f"page lue en direct mais vide ({len(texte.strip())} car. utiles)"
    obf = lire(lu["html"])
    sortie = {"text": texte + marqueur(obf),
              "metadata": {"title": titre},
              "final_url": lu.get("final_url"),
              "format_servi": "text",
              "source": ("lecture directe (UA navigateur) — le scraper hébergé "
                         "a refusé cette page")}
    if obf["motifs"]:
        sortie["motifs_obfuscation"] = obf["motifs"]
    if obf["adresses"]:
        sortie["adresses_obfusquees"] = obf["adresses"]
    return sortie, "lu"


def completer(res: dict, url: str, per) -> None:
    """Le scrape a réussi mais ne montre AUCUNE adresse : va voir le HTML.

    Une requête de plus, et SEULEMENT là — c'est très exactement l'instant où
    un agent s'apprête à écrire « aucun contact publié ». Une page qui affiche
    déjà une adresse ne déclenche rien : le remède ne doit pas coûter plus que
    le mal (#662).

    Le résultat est écrit dans les champs servis ET collé dans le contenu :
    l'agent lit la page, c'est là qu'il conclut."""
    if contient_adresse(" ".join(str(res.get(k) or "") for k in ("markdown", "text"))):
        return
    from ..mcp_errors import McpError
    try:
        lu = fetch(url)
        if not lu.get("ok"):
            res["sonde_obfuscation"] = f"non concluante ({lu.get('verdict')})"
            return
        _hors_perimetre(lu.get("final_url"), per)
    except McpError as refus:
        # La sonde ne SERT pas de contenu : un refus (hôte non public, page
        # sortie du périmètre d'URL du projet) l'ÉCARTE — il ne fait pas
        # échouer un scrape qui, lui, a réussi. Le refus se DIT dans la
        # réponse, il n'est pas avalé.
        res["sonde_obfuscation"] = f"écartée — {refus.error.message}"
        return
    obf = lire(lu["html"])
    if not obf["motifs"]:
        # Rien de caché : la réponse ne bouge PAS. Le silence est alors une
        # information juste — la description servie dit que l'outil va toujours
        # relire le HTML d'une page sans adresse, donc « rien » veut dire « on a
        # regardé, il n'y a rien », et non « on n'a pas regardé ». Ajouter un
        # champ ici ferait grossir la quasi-totalité des réponses pour redire
        # ce que le contrat promet déjà.
        return
    res["motifs_obfuscation"] = obf["motifs"]
    if obf["adresses"]:
        res["adresses_obfusquees"] = obf["adresses"]
    marque = marqueur(obf)
    for champ in ("markdown", "text"):
        if res.get(champ):
            res[champ] = res[champ] + marque
