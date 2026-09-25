## usage — salaires par métier aux états-unis (open data bls oews)

source publique du Bureau of Labor Statistics, sans clé. un seul tool :
- « combien gagne un data scientist aux US, et à Chicago ? » → `bls_oews_wages(soc="15-2051", areas=["US", "IL", "16980"])` — par zone : P10, P25, médiane (`p50`), P75, P90 et moyenne en dollars **annuels**, plus l'emploi
- zones acceptées : `"US"`, un État (nom ou abréviation postale : `"Illinois"`, `"IL"`), ou le **code CBSA à 5 chiffres** d'une aire métropolitaine (`"16980"` = Chicago-Naperville-Elgin, `"35620"` = New York-Newark-Jersey City) — ⚠️ un NOM d'aire métropolitaine n'est pas résolu
- le métier se passe par son **code SOC à 6 chiffres** (`"15-1299"` ou `"151299"`)

## note — ⚠️ ce que le chiffre couvre, et ce qu'il ne dit pas

- **dernière année publiée seulement** : l'API ne sert aucun historique OEWS ; l'année est dans le résultat (`year`), à citer avec le chiffre
- **granularité SOC, pas O\*NET** : un code O\*NET-SOC à 8 chiffres (`"15-1299.08"`) est accepté, mais son suffixe est RETIRÉ — les salaires rendus couvrent alors tout le SOC `15-1299` (une catégorie « autres » parfois très large), et le résultat le dit dans `note`. à répéter à l'utilisateur plutôt que de présenter le chiffre comme celui du métier détaillé
- **une valeur absente n'est jamais devinée** : un salaire plafonné ou non publié par le BLS revient `null`, avec sa forme brute dans `raw` (`"-"`) et le motif dans `footnotes` ; `missing` liste les mesures sans série pour ce métier × cette zone (petites aires, métiers rares)
- **quota journalier PARTAGÉ** : sans clé d'enregistrement, le BLS sert 25 requêtes par jour à toute la plateforme ; une requête couvre 3 zones → grouper les zones dans UN appel (12 au plus), jamais un appel par zone. quota épuisé = refus explicite, retour le lendemain
