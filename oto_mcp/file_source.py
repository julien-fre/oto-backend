"""Résolveur « fichier côté oto » — désigner un fichier déjà accessible par oto et
en récupérer les OCTETS côté serveur (oto-backend#60).

Un agent MCP n'a pas de système de fichiers : il ne peut pas désigner un PDF par un
chemin disque. Ce module résout une **référence typée** vers le contenu binaire d'un
fichier qu'oto sait atteindre — pièce Gmail, fichier Drive, URL, fichier d'un projet —
pour qu'un tool (upload Pennylane, etc.) l'ingère sans passer par le disque.

Couche backend-core (ADR 0004) : imports lazy des clients connecteurs, résolution
des credentials Google via `google_oauth`. Pas de fallback : une source illisible
ou inconnue lève `FileSourceError` (traduite en erreur actionnable par l'appelant).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from . import access, url_perimeter
from .auth import google as google_oauth

# Plafond par défaut : Pennylane accepte 100 Mo, mais on borne pour ne pas charger
# un fichier géant en RAM par mégarde. Override par appel via `max_bytes`.
DEFAULT_MAX_BYTES = 25 * 1024 * 1024  # 25 Mo


class FileSourceError(RuntimeError):
    """Référence de fichier invalide, source illisible, ou dépassement de taille."""


@dataclass
class ResolvedFile:
    data: bytes
    filename: str
    mime: str


def _google_creds(account: Optional[str]):
    sub = access.current_user_sub_or_raise()
    return google_oauth.credentials_for(sub, account=account)


def _from_drive(src: dict) -> ResolvedFile:
    file_id = src.get("file_id")
    if not file_id:
        raise FileSourceError("source drive : `file_id` requis.")
    from oto.tools.google.drive.lib.drive_client import DriveClient
    client = DriveClient(credentials=_google_creds(src.get("account")))
    att = client.get_file_bytes(str(file_id))
    return ResolvedFile(att["data"], att.get("filename") or str(file_id),
                        att.get("mimeType") or "application/octet-stream")


def _from_gmail(src: dict) -> ResolvedFile:
    message_id, filename = src.get("message_id"), src.get("filename")
    if not message_id or not filename:
        raise FileSourceError("source gmail : `message_id` et `filename` requis.")
    from oto.tools.google.gmail.lib.gmail_client import GmailClient
    client = GmailClient(credentials=_google_creds(src.get("account")))
    att = client.get_attachment(message_id, filename, int(src.get("index", 0)))
    return ResolvedFile(att["data"], att.get("filename") or filename,
                        att.get("mimeType") or "application/octet-stream")


def _assert_public_host(host: str) -> None:
    """Anti-SSRF : refuse une cible qui résout vers une adresse non publique
    (boucle locale, privée, lien-local — dont les métadonnées cloud
    169.254.169.254 —, réservée, multicast). Sans ce garde-fou, un agent pourrait
    faire lire au serveur ses services internes ou l'IMDS.

    C'est la garde d'egress de la plateforme (`oto_mcp/egress.py`) sous la
    politique « URL choisie par l'agent » (aucune exception déclarée) — la même
    couture que `web_read` et que les connecteurs à hôte libre (oto#180). Elle
    portait sa propre règle jusqu'au 12/09/2026."""
    from . import egress
    if not host:
        raise FileSourceError("source url : hôte manquant.")
    hote = f"[{host}]" if ":" in host else host
    try:
        egress.check_url(f"http://{hote}/", connector="source url", field="url",
                         exceptions_declarees=False)
    except egress.EgressRefused as e:
        raise FileSourceError(str(e)) from None


def _from_url(src: dict, max_bytes: int) -> ResolvedFile:
    # Périmètre du projet (#605) : un fichier lu à une URL est une page lue. Et le
    # refus du périmètre parle en PREMIER (#632) — avant la forme http(s), avant
    # l'anti-SSRF (qui résout l'hôte : sans réseau, il parlait à sa place).
    url_perimeter.refuse_if_excluded(src.get("url"), url_perimeter.perimeter_of_call())
    url = src.get("url")
    if not url or not str(url).lower().startswith(("http://", "https://")):
        raise FileSourceError("source url : `url` http(s) requise.")
    import os
    from urllib.parse import unquote, urlsplit

    import httpx
    host = urlsplit(str(url)).hostname
    _assert_public_host(host or "")
    # Redirections DÉSACTIVÉES : un 3xx pourrait pointer une IP interne (le garde-fou
    # ci-dessus ne valide que l'hôte initial). Nos sources légitimes (URLs signées S3,
    # gmail_message(op="attachment")) sont directes → pas de redirect attendu.
    with httpx.Client(follow_redirects=False, timeout=60.0) as c:
        with c.stream("GET", url) as r:
            if 300 <= r.status_code < 400:
                raise FileSourceError(
                    "source url : redirection non suivie (anti-SSRF) — fournir l'URL finale directe.")
            r.raise_for_status()
            declared = r.headers.get("content-length")
            if declared and int(declared) > max_bytes:
                raise FileSourceError(
                    f"fichier distant trop volumineux ({declared} > {max_bytes} octets).")
            chunks, total = [], 0
            for chunk in r.iter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    raise FileSourceError(f"fichier distant > {max_bytes} octets.")
                chunks.append(chunk)
            data = b"".join(chunks)
            mime = (r.headers.get("content-type") or "application/octet-stream").split(";")[0].strip()
    name = os.path.basename(unquote(urlsplit(str(url)).path)) or "file"
    return ResolvedFile(data, name, mime)


def _from_project_file(src: dict, max_bytes: int) -> ResolvedFile:
    """Un fichier DÉPOSÉ sur un projet (« Autre document », `oto_project_files`), lu
    dans le stockage objet — sans URL signée ni aller-retour HTTP (ADR 0074).

    La garde est celle d'une lecture par-id d'un projet : visible dans l'org ACTIVE
    (`ownership.visible_in_org`, ADR 0023) ET lisible (`can_access(read)`). Un refus
    répond EXACTEMENT comme un fichier inconnu : distinguer les deux dirait à qui n'a
    pas le droit que le fichier existe."""
    try:
        pid, fid = int(src.get("project_id")), int(src.get("file_id"))
    except (TypeError, ValueError):
        raise FileSourceError(
            "source project_file : `project_id` et `file_id` entiers requis "
            "(ids rendus par oto_project_files op=list).") from None
    from . import db, media_store, ownership
    introuvable = FileSourceError(
        f"source project_file : fichier #{fid} introuvable sur le projet #{pid}.")
    sub = access.current_user_sub_or_raise()
    rid = str(pid)
    if not (ownership.visible_in_org(sub, access.current_org(sub), "project", rid)
            and ownership.can_access(sub, "project", rid, "read")):
        raise introuvable
    row = db.get_project_file(fid)
    if not row or int(row["project_id"]) != pid:
        raise introuvable
    try:
        data = media_store.fetch_object(row["s3_key"], max_bytes=max_bytes)
    except media_store.MediaError as e:
        raise FileSourceError(
            f"source project_file : lecture impossible ({e.code} : {e}).") from None
    return ResolvedFile(data, row.get("filename") or f"fichier-{fid}",
                        row.get("mime") or "application/octet-stream")


_RESOLVERS = {"drive": _from_drive, "gmail": _from_gmail}
# Les résolveurs qui bornent la taille EUX-MÊMES, avant de matérialiser les octets.
_RESOLVERS_BORNES = {"url": _from_url, "project_file": _from_project_file}


def resolve(source: Any, *, max_bytes: int = DEFAULT_MAX_BYTES) -> ResolvedFile:
    """Résout une référence de fichier côté oto vers ses octets + métadonnées.

    `source` = dict `{"kind": "drive"|"gmail"|"url"|"project_file", …}` :
      - drive        : `{file_id, account?}`
      - gmail        : `{message_id, filename, index?, account?}`
      - url          : `{url}` (http/https, redirections refusées)
      - project_file : `{project_id, file_id}` (ids d'`oto_project_files op=list`)
    Lève `FileSourceError` si `kind` manque/inconnu, source illisible, ou taille
    dépassée. Borne la taille à `max_bytes` (charge en RAM)."""
    if not isinstance(source, dict):
        raise FileSourceError("source : objet attendu `{kind, …}`.")
    kind = source.get("kind")
    if kind in _RESOLVERS_BORNES:
        rf = _RESOLVERS_BORNES[kind](source, max_bytes)
    else:
        fn = _RESOLVERS.get(kind)
        if fn is None:
            raise FileSourceError(
                f"source `kind`={kind!r} inconnu (attendu : drive, gmail, url, project_file).")
        try:
            rf = fn(source)
        except FileSourceError:
            raise
        except Exception as e:  # erreur client connecteur (Drive/Gmail) → actionnable
            raise FileSourceError(str(e))
    if len(rf.data) > max_bytes:
        raise FileSourceError(f"fichier de {len(rf.data)} octets > plafond {max_bytes}.")
    return rf
