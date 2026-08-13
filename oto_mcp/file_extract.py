"""Extraire le TEXTE d'un fichier déposé — pour qu'on le trouve par ce qu'il contient (#298).

Un fichier déposé dans un projet n'était trouvable que par son nom, son titre ou sa
description. Un PDF de trente pages était donc, du point de vue de la recherche, un
nom de fichier : mal nommé, introuvable — alors que tout le reste du contenu (pages,
tableaux, guides, procédures) est cherchable plein texte. Le fichier était le seul
angle mort, et c'est souvent lui qui porte la matière apportée par le client.

## Ce que ce module fait, et ce qu'il ne fait pas

**Il rend du texte, ou il dit pourquoi il n'en rend pas.** C'est tout. Il ne parle ni
à la base, ni à S3, ni au réseau : on lui donne des octets, il rend un `Extraction`.
Cette frontière est ce qui le rend testable sans rien monter, et ce qui permet de
l'appeler depuis un worker hors de la boucle d'événements (le serveur est mono-loop —
une extraction synchrone dans la boucle la gèlerait, cf. `docs/event-loop-perf.md`).

**Il n'y a PAS d'OCR.** Lire une image est un autre métier et un autre coût ; un fichier
image ressort `unsupported`, ce qui est une réponse honnête. Le jour où l'OCR se
décide, il s'ajoute ici sans rien changer au reste.

## Les statuts sont TERMINAUX, et c'est le point de conception

Un fichier chiffré n'est pas un échec transitoire : le retenter à chaque passage du
worker coûte pour toujours et ne réussira jamais. Chaque cas se nomme donc
(`unsupported`, `encrypted`, `empty`, `failed`), et seul `failed` — l'imprévu — laisse
la porte ouverte à une reprise. Un statut nommé est aussi ce qui rend l'interface
honnête : « format non supporté » n'est pas « en cours ».

## Coût mesuré (banc du 13/08, 6 documents réels : decks, plaquette, supports)

**34 pages, 44 050 caractères, 0,78 s** — soit ~130 ms par document. Un document fait
~7 300 caractères, c'est-à-dire **quelques pages rédigées, pas cent** : le pronostic
« deux ordres de grandeur au-dessus d'une page » était faux d'un facteur dix. Indexer
ce volume coûte 3 ms par fichier déposé et 0,5 Mo pour 500 fichiers.

⚠️ Cet échantillon penche vers le PEU de texte (des slides portent ~1 200 caractères
par page, un rapport en prose 3 à 5 fois plus) : les chiffres sont un **plancher**.
Même multipliés par cinq, ils restent modestes — c'est ce qui a écarté l'idée d'un
process d'extraction séparé.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)

# Bornes du texte retenu. La première protège la base d'un fichier pathologique (un
# export de log de 500 Mo n'a pas à devenir une ligne) ; la seconde évite d'écrire un
# statut « extrait » pour trois caractères de garde d'un PDF scanné, qui est en
# réalité un cas OCR déguisé.
MAX_TEXT_CHARS = 2_000_000
MIN_TEXT_CHARS = 16

# Statuts. `ok` mis à part, tous sont TERMINAUX sauf `failed` (cf. `is_retryable`).
OK = "ok"
UNSUPPORTED = "unsupported"      # format qu'on ne sait pas lire (image, archive, binaire)
ENCRYPTED = "encrypted"          # protégé par mot de passe — ne réussira jamais seul
EMPTY = "empty"                  # lu, mais sans texte utile (scan sans OCR, doc vide)
FAILED = "failed"                # imprévu (fichier tronqué, lib qui lève) — reprenable


@dataclass(frozen=True)
class Extraction:
    """Le résultat, qu'il ait abouti ou non.

    `status` vaut toujours quelque chose ; `text` n'est rempli que sur `ok`. `detail`
    porte de quoi comprendre sans rouvrir le fichier (le type d'erreur, jamais son
    contenu — un message de lib peut recracher des octets du document)."""
    status: str
    text: str = ""
    pages: Optional[int] = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == OK


def is_retryable(status: str) -> bool:
    """Seul l'imprévu se retente. Un format non supporté ou un fichier chiffré ne
    changera pas d'avis au prochain passage du worker — les retenter, c'est payer une
    file qui ne se vide jamais."""
    return status == FAILED


# Extensions → extracteur. On route sur l'EXTENSION et non sur le mime déclaré : le
# mime d'un upload vient du client, qui se trompe couramment (`application/octet-stream`
# sur un PDF parfaitement valide). Le mime sert de repli quand l'extension manque.
_BY_EXT = {}


def _register(*exts):
    def deco(fn):
        for e in exts:
            _BY_EXT[e] = fn
        return fn
    return deco


@_register("txt", "md", "markdown", "csv", "json", "log", "rst")
def _extract_text(data: bytes) -> Extraction:
    """Texte brut. `errors='replace'` plutôt que de lever : un octet douteux au milieu
    d'un fichier de 10 000 lignes ne doit pas rendre les 9 999 autres introuvables."""
    return Extraction(OK, data.decode("utf-8", errors="replace"))


@_register("pdf")
def _extract_pdf(data: bytes) -> Extraction:
    import io

    from pypdf import PdfReader
    from pypdf.errors import FileNotDecryptedError, PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if getattr(reader, "is_encrypted", False):
            # Un PDF « chiffré » sans mot de passe s'ouvre avec une chaîne vide — le
            # cas est courant (protection en écriture seule). On ne renonce donc
            # qu'après avoir essayé.
            try:
                reader.decrypt("")
            except Exception:
                return Extraction(ENCRYPTED, detail="pdf protégé par mot de passe")
        pages = [(p.extract_text() or "") for p in reader.pages]
    except FileNotDecryptedError:
        return Extraction(ENCRYPTED, detail="pdf protégé par mot de passe")
    except (PdfReadError, Exception) as e:            # tronqué, corrompu, inattendu
        return Extraction(FAILED, detail=type(e).__name__)
    return Extraction(OK, "\n".join(pages), pages=len(pages))


@_register("docx")
def _extract_docx(data: bytes) -> Extraction:
    import io

    from docx import Document

    try:
        doc = Document(io.BytesIO(data))
        # Les paragraphes ET les tableaux : dans un document bureautique, une part
        # notable du contenu utile (chiffres, engagements) vit dans les tableaux, que
        # `doc.paragraphs` seul ne voit pas.
        parts = [p.text for p in doc.paragraphs]
        for t in doc.tables:
            for row in t.rows:
                parts.extend(c.text for c in row.cells)
    except Exception as e:
        return Extraction(FAILED, detail=type(e).__name__)
    return Extraction(OK, "\n".join(x for x in parts if x))


@_register("xlsx")
def _extract_xlsx(data: bytes) -> Extraction:
    import io

    from openpyxl import load_workbook

    try:
        # `read_only` + `data_only` : on veut les VALEURS, pas les formules, et sans
        # charger le classeur entier en mémoire (un export de 50 000 lignes existe).
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        parts = []
        for ws in wb.worksheets:
            parts.append(str(ws.title))
            for row in ws.iter_rows(values_only=True):
                parts.extend(str(v) for v in row if v is not None)
        wb.close()
    except Exception as e:
        return Extraction(FAILED, detail=type(e).__name__)
    return Extraction(OK, "\n".join(parts))


def supported_extensions() -> set:
    """Ce qu'on sait lire — dérivé du registre, jamais recopié : une liste en double
    finit par mentir (celle de la doc, celle du code, et la vraie)."""
    return set(_BY_EXT)


def _extension_of(filename: str, mime: str = "") -> str:
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if ext in _BY_EXT:
        return ext
    # Repli sur le mime, pour un fichier SANS extension (un upload d'API, un `blob`).
    return {"application/pdf": "pdf", "text/plain": "txt", "text/markdown": "md",
            "text/csv": "csv", "application/json": "json"}.get(
                (mime or "").split(";")[0].strip().lower(), ext)


def extract(data: bytes, filename: str, mime: str = "") -> Extraction:
    """Le texte d'un fichier, ou la raison nommée de son absence.

    Ne lève JAMAIS : ce module est appelé par un worker de fond qui traite une file.
    Une exception qui remonte, c'est un fichier qui bloque la file ou un worker qui
    meurt — alors qu'un `failed` nommé laisse la file avancer et le cas visible.
    """
    if not data:
        return Extraction(EMPTY, detail="fichier vide")
    ext = _extension_of(filename, mime)
    fn = _BY_EXT.get(ext)
    if fn is None:
        return Extraction(UNSUPPORTED, detail=f"format « {ext or 'inconnu'} » non supporté")
    try:
        out = fn(data)
    except Exception as e:                    # ceinture : un extracteur ne doit pas tuer la file
        log.warning("extraction %s a levé : %s", ext, type(e).__name__)
        return Extraction(FAILED, detail=type(e).__name__)
    if not out.ok:
        return out

    text = out.text.strip()
    if len(text) < MIN_TEXT_CHARS:
        # Le cas le plus courant ici est le PDF SCANNÉ : la lecture réussit et ne rend
        # rien, parce que le texte est une image. Le nommer `empty` (et non `ok` avec
        # un texte vide) est ce qui permet à l'interface de le dire, et à un futur
        # lot d'OCR de retrouver exactement la population concernée.
        return Extraction(EMPTY, pages=out.pages,
                          detail="aucun texte extractible (document scanné ?)")
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS]
        return Extraction(OK, text, pages=out.pages,
                          detail=f"tronqué à {MAX_TEXT_CHARS} caractères")
    return Extraction(OK, text, pages=out.pages, detail=out.detail)
