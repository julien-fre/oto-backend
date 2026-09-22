"""La forme d'une transcription : du segment brut à la page du projet (ADR 0074).

Fonctions PURES, sans fournisseur ni base : elles prennent les segments normalisés
`{start, end, text, speaker}` (la forme que rend le client oto-core) et produisent
les tours de parole puis le markdown de la page.

Deux corrections valables pour tout fournisseur, mesurées au banc :

- **dédoublonner** — un modèle de transcription répète parfois un segment tel quel ;
  deux segments CONSÉCUTIFS au texte identique (casse et blancs ignorés) n'en font
  qu'un, qui couvre la durée des deux ;
- **fusionner les locuteurs parasites** — la diarisation invente des identifiants
  d'une phrase. On garde ceux qui pèsent au moins `PART_MINIMALE` du total, au plus
  `MAX_LOCUTEURS`, au moins un ; chaque segment d'un identifiant écarté passe au
  locuteur gardé le plus proche dans le temps (le voisin précédent ou suivant). Le
  poids est la DURÉE (le connecteur demande toujours l'horodatage par segment) ; le
  nombre de mots ne sert que si un segment arrive sans horodatage.
"""
from __future__ import annotations

from typing import Optional

MAX_LOCUTEURS = 3
PART_MINIMALE = 0.10


def _cle(texte: str) -> str:
    return " ".join(texte.split()).casefold()


def dedupe(segments: list[dict]) -> list[dict]:
    """Retire les segments consécutifs au texte identique (le premier couvre les deux)."""
    out: list[dict] = []
    for s in segments:
        if out and _cle(out[-1]["text"]) == _cle(s["text"]):
            if s.get("end") is not None:
                out[-1] = {**out[-1], "end": s["end"]}
            continue
        out.append(dict(s))
    return out


def _horodate(segments: list[dict]) -> bool:
    return bool(segments) and all(
        s.get("start") is not None and s.get("end") is not None for s in segments)


def _poids(s: dict, horodate: bool) -> float:
    if horodate:
        return max(0.0, float(s["end"]) - float(s["start"]))
    return float(len(s["text"].split()))


def locuteurs_gardes(segments: list[dict]) -> list[str]:
    """Les identifiants retenus, du plus lourd au plus léger."""
    horodate = _horodate(segments)
    poids: dict[str, float] = {}
    for s in segments:
        if s.get("speaker") is not None:
            poids[s["speaker"]] = poids.get(s["speaker"], 0.0) + _poids(s, horodate)
    if not poids:
        return []
    total = sum(poids.values()) or 1.0
    classes = sorted(poids, key=lambda k: -poids[k])
    gardes = [k for k in classes if poids[k] / total >= PART_MINIMALE][:MAX_LOCUTEURS]
    return gardes or classes[:1]


def _ecart(a: dict, b: dict) -> Optional[float]:
    """Temps entre deux segments (a avant b), None sans horodatage."""
    if a.get("end") is None or b.get("start") is None:
        return None
    return max(0.0, float(b["start"]) - float(a["end"]))


def merge_speakers(segments: list[dict]) -> list[dict]:
    """Réaffecte chaque segment d'un locuteur parasite au locuteur gardé le plus proche."""
    gardes = set(locuteurs_gardes(segments))
    if not gardes:
        return [dict(s) for s in segments]
    out = [dict(s) for s in segments]
    ancres = [i for i, s in enumerate(segments) if s.get("speaker") in gardes]
    for i, s in enumerate(segments):
        if s.get("speaker") in gardes:
            continue
        avant = next((j for j in reversed(ancres) if j < i), None)
        apres = next((j for j in ancres if j > i), None)
        if avant is None or apres is None:
            choisi = avant if apres is None else apres
        else:
            e_avant = _ecart(segments[avant], s)
            e_apres = _ecart(s, segments[apres])
            choisi = apres if (e_avant is not None and e_apres is not None
                               and e_apres < e_avant) else avant
        out[i]["speaker"] = segments[choisi]["speaker"]
    return out


def turns(segments: list[dict]) -> list[dict]:
    """Segments consécutifs d'un même locuteur → un tour `{speaker, start, text}`,
    le locuteur renommé « Locuteur N » dans l'ordre d'apparition. Sans locuteur (pas
    de diarisation), chaque segment reste son propre paragraphe : tout fondre en un
    bloc rendrait la page illisible."""
    noms: dict[str, str] = {}
    out: list[dict] = []
    for s in segments:
        brut = s.get("speaker")
        if brut is not None and brut not in noms:
            noms[brut] = f"Locuteur {len(noms) + 1}"
        nom = noms.get(brut) if brut is not None else None
        if nom is not None and out and out[-1]["speaker"] == nom:
            out[-1]["text"] += " " + s["text"]
            continue
        out.append({"speaker": nom, "start": s.get("start"), "text": s["text"]})
    return out


def process(segments: list[dict]) -> list[dict]:
    """La chaîne complète : dédoublonner, fusionner les parasites, regrouper en tours."""
    return turns(merge_speakers(dedupe(segments)))


def _horloge(secondes: float) -> str:
    s = int(secondes)
    h, reste = divmod(s, 3600)
    m, s = divmod(reste, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def title(filename: str, date: str) -> str:
    return f"Transcription — {filename} — {date}"


def render(tours: list[dict], *, filename: str, duration_s: Optional[float]) -> str:
    """Le corps markdown : une ligne de provenance, puis un paragraphe par tour,
    locuteur en tête (et l'instant du tour quand l'amont horodate)."""
    entete = f"Transcription automatique de `{filename}`"
    if duration_s is not None:
        entete += f" ({_horloge(duration_s)})"
    paragraphes = [entete + "."]
    for t in tours:
        tete = []
        if t["speaker"]:
            tete.append(f"**{t['speaker']}**")
        if t.get("start") is not None:
            tete.append(f"[{_horloge(t['start'])}]")
        paragraphes.append((" ".join(tete) + " — " if tete else "") + t["text"])
    return "\n\n".join(paragraphes) + "\n"


def word_count(tours: list[dict]) -> int:
    return sum(len(t["text"].split()) for t in tours)
