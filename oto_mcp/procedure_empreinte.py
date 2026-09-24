"""L'empreinte du corps STOCKÉ d'une procédure — la preuve que l'écriture rend (oto#133).

Publier un corps ne rendait rien qui permette de vérifier que le texte enregistré était
celui qu'on avait envoyé : il fallait relire la procédure par un second appel et comparer
à la main. C'est ce contrôle manuel qui a trouvé **dix-huit lignes servies aux agents
qu'aucun fichier source ne contenait** — des instructions exécutées que personne n'avait
écrites, et que rien ne signalait.

L'écriture rend donc `body_sha256` : le SHA-256 (hex) des octets UTF-8 du corps **relu
en base pour la version qu'elle vient de poser** — jamais celui du texte reçu. Hacher
l'entrée ne prouverait rien : c'est précisément l'écart entre les deux qu'on veut voir.

⚠️ **Le corps stocké n'est pas l'octet près de l'envoi, par construction** — et c'est
pourquoi l'empreinte doit se comparer en connaissance de cause :
- les blancs de tête et de fin sont retirés (un fichier qui finit par `\\n` ne donne donc
  jamais la même empreinte que son contenu envoyé tel quel : hacher `texte.strip()`) ;
- les outils cités sous le nom d'un produit (`acme_doc`) sont ramenés au canonique ;
- le dessin qu'`op=get` remplace par un marqueur est rendu au corps.
Une empreinte différente de celle de l'envoi « stripé » dit donc : la base porte autre
chose que ce que tu crois avoir publié — relis (`op=get`) avant de t'y fier.

On relit la **révision** de la version écrite, pas la ligne vivante : entre l'écriture et
la relecture, une édition concurrente peut déjà avoir posé la version suivante, et on
rendrait alors l'empreinte d'un corps qui n'est pas le nôtre.
"""
from __future__ import annotations

import hashlib

from . import org_store


def empreinte(body_md: str) -> str:
    """SHA-256 hex des octets UTF-8 — la même recette que `sha256sum` sur le fichier."""
    return hashlib.sha256(body_md.encode("utf-8")).hexdigest()


def empreinte_check(owner_type: str, owner_id: int | str, slug: str, version: int) -> dict:
    """`{"body_sha256": …}` du corps stocké pour `version` — fragment de réponse, comme
    `procedure_diagram.diagram_check` et `procedure_retrait.retrait_check`.

    La version vient d'être écrite dans une transaction validée : son absence n'est pas
    un cas à tolérer mais un défaut, qu'on lève plutôt que de rendre une preuve vide."""
    stockee = org_store.get_instruction(owner_type, owner_id, slug, version)
    if not stockee or stockee.get("body_md") is None:
        raise RuntimeError(
            f"procédure `{slug}` v{version} ({owner_type}:{owner_id}) introuvable juste "
            "après son écriture — l'empreinte du corps stocké ne peut pas être rendue")
    return {"body_sha256": empreinte(stockee["body_md"])}
