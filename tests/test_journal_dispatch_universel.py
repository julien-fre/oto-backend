"""Le dispatch universel (`oto_call`) écrivait deux lignes de journal, aucune sous la
règle annoncée.

Le contrat servi promet, des deux côtés (`CallDetail.args`, `RunCall.args`), des
arguments **tronqués à l'écriture** (300 caractères par valeur) et **masqués** (un
argument déclaré secret pour cet outil part en empreinte), « y compris à travers le
dispatch universel ». Mesuré le 2026-09-01 : la promesse était fausse sur les deux
lignes qu'un appel dispatché produit.

1. **La ligne écrite sous le NOM CIBLE** (`tools/meta._trace_target_call`, ADR 0036 §5)
   posait le dictionnaire d'arguments TEL QUEL dans `tool_calls.args` — ni tronqué, ni
   masqué. Ce n'est pas un chemin de bord : 40 159 des 268 016 lignes `kind='mcp'` de
   la base viennent de là, dont 6 664 portent un `run_id` et sont donc servies dans la
   timeline d'un déroulé. Les 111 lignes de la base dont une valeur dépasse 301
   caractères (jusqu'à 4 383) viennent **toutes** de ce chemin — aucune de la fabrique.

2. **La ligne d'enveloppe** (`tool='oto_call'`) reprenait bien la déclaration de l'outil
   visé, mais ne masquait qu'UN niveau de sous-dictionnaire. Or le dispatch en ajoute un
   (`{"name": …, "arguments": {…}}`) : le seul secret déclaré à deux niveaux
   (`lemlist_mailbox`, mots de passe SMTP/IMAP dans `smtp_imap`) repartait donc en clair
   dès qu'on passait par `oto_call` — c'est-à-dire toujours, puisque cet outil n'est pas
   au registre servi et n'est atteignable QUE par là.

Aucun secret n'avait fuité en pratique (0 appel à `lemlist_mailbox`/`lemlist_webhook`
sur toute la fenêtre, 0 `token`/`code` non masqué sur `oto_org`) : c'était un défaut
latent, pas un incident.

Éprouvé rouge le 2026-09-01 sur `507784f4` : les trois premiers tests échouent, le
quatrième (le masquage sur le chemin DIRECT, déjà correct) passe — c'est lui qui montre
que la recette de masquage n'a pas été affaiblie pour faire passer les autres.
"""
from __future__ import annotations

import asyncio
import json

from oto_mcp import calllog
from oto_mcp.tools import meta

SECRET = "mot-de-passe-smtp-en-clair"
# La seule déclaration existante dont le secret est enfoui dans un sous-dictionnaire.
CHARGE = {"op": "connect",
          "smtp_imap": {"smtp_password": SECRET, "imap_password": SECRET}}


def _ligne_tracee(monkeypatch, tool: str, args: dict) -> dict:
    """La ligne que `_trace_target_call` pose réellement dans `tool_calls`."""
    lignes: list[dict] = []
    monkeypatch.setattr(meta.db, "insert_tool_call", lambda row: lignes.append(row))
    monkeypatch.setattr(meta.access, "current_org", lambda sub: 7)
    asyncio.run(meta._trace_target_call("u-1", tool, dict(args), True, None, 12))
    assert len(lignes) == 1, lignes
    return lignes[0]


def test_la_ligne_du_nom_cible_est_tronquee(monkeypatch):
    """La ligne servie dans la timeline d'un déroulé doit respecter la borne annoncée
    par le contrat des DEUX surfaces qui la lisent."""
    ligne = _ligne_tracee(monkeypatch, "oto_doc", {"op": "set", "content": "x" * 5_000})
    valeur = ligne["args"]["content"]
    assert len(valeur) <= calllog.MAX_ARG_CHARS + 1, (
        f"{len(valeur)} caractères journalisés — le contrat en annonce "
        f"{calllog.MAX_ARG_CHARS}")


def test_la_ligne_du_nom_cible_est_masquee(monkeypatch):
    """Le nom sous lequel la ligne est écrite EST celui qui déclare ses secrets : la
    trace connaît l'outil visé, elle n'a aucune excuse pour ignorer sa déclaration."""
    ligne = _ligne_tracee(monkeypatch, "lemlist_mailbox", CHARGE)
    assert SECRET not in json.dumps(ligne["args"], ensure_ascii=False), ligne["args"]


def test_l_enveloppe_du_dispatch_masque_le_secret_de_l_outil_vise():
    """L'autre ligne du même appel. Le dispatch ajoute un niveau d'imbrication : le
    masquage doit le traverser, sinon la déclaration de l'outil ne vaut que sur un
    chemin par lequel il n'est jamais appelé."""
    args = calllog.truncated_args({"name": "lemlist_mailbox", "arguments": CHARGE},
                                  tool="oto_call")
    assert SECRET not in json.dumps(args, ensure_ascii=False), args


def test_le_chemin_direct_masque_toujours():
    """Témoin : la recette de masquage d'origine tient encore. Sans lui, on pourrait
    faire passer les tests ci-dessus en masquant tout, ce qui rendrait le journal
    illisible sans rien protéger de plus."""
    args = calllog.truncated_args(CHARGE, tool="lemlist_mailbox")
    rendu = json.dumps(args, ensure_ascii=False)
    assert SECRET not in rendu, args
    assert "#" in rendu, "l'empreinte corrélable a disparu avec le clair"
    assert "connect" in rendu, "le masquage a emporté un argument qui n'est pas secret"


def test_le_masquage_est_borne_en_profondeur():
    """La traversée est bornée et le dit : un argument profond ne fait pas tomber la
    journalisation, qui est best-effort mais pas facultative."""
    profond: dict = {"smtp_password": SECRET}
    for _ in range(200):
        profond = {"encore": profond}
    args = calllog.truncated_args({"op": "connect", "nid": profond},
                                  tool="lemlist_mailbox")
    assert args is not None and "op" in args
