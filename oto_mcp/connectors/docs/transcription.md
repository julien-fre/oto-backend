## prerequisite — une clé Mistral, entraînement désactivé

crée une clé API sur la [console Mistral](https://console.mistral.ai/api-keys), puis colle-la dans oto au niveau de l'organisation.
- **avant le premier enregistrement d'un client**, désactive l'usage de tes données pour l'entraînement dans les réglages du compte Mistral : ce sont des voix de particuliers, souvent chez eux
- byo-only : pas de clé oto partagée, chaque organisation paie sa propre minute d'audio chez Mistral (hébergement dans l'UE)

## setup — langue et vocabulaire

deux réglages facultatifs sur l'instance, non secrets :
- **langue** : le code de la langue parlée (`fr` si vide). `auto` laisse Mistral la détecter
- **vocabulaire** : les mots du métier à bien orthographier (ouvrages, matériaux, noms propres), séparés par des virgules ou des retours à la ligne. Mistral ne prend que des mots isolés : une expression est découpée en ses mots, et les mots de moins de 3 lettres sont écartés (« pompe à chaleur » → `pompe`, `chaleur`). 100 mots au plus ; ce qui est écarté est signalé dans la réponse

une instance = une clé × une langue × un vocabulaire ; un projet se rattache à l'instance voulue par un slot.

## usage — un enregistrement devient une page du projet (asynchrone)

- « transcris la visite déposée sur le projet » → `transcription_create(source={"kind":"project_file","project_id":…,"file_id":…}, _project=…)` — les ids viennent de `oto_project_files op=list`
- le fichier est lu côté serveur, jamais transporté par la conversation (25 Mo au plus, jusqu'à 3 h d'audio)
- **l'appel ne bloque pas** : il rend tout de suite `{job_id, status:"pending"}` — un enregistrement de 30 min prend de 20 s à 5 min à transcrire, en tâche de fond
- relire `transcription_status(job_id)` jusqu'à `status:"done"` (ou `"failed"` avec `error`) : c'est là que revient la page « Transcription — <fichier> — <date> » (id, titre, lien), un paragraphe par tour de parole avec locuteur et instant en tête (« Locuteur 1 [03:12] »…), le nombre de mots, la durée et les locuteurs — jamais le texte, qui se lit ensuite comme toute page du projet

## note — ce que le connecteur corrige, et ce qu'il ne fait pas

- les segments répétés à l'identique sont fusionnés ; les locuteurs « parasites » que la diarisation invente pour une phrase sont rattachés au locuteur voisin (on garde au plus 3 locuteurs, chacun pesant au moins 10 % de l'enregistrement)
- rien ne se transcrit sans appel : on ne paie que ce qui est demandé
- la transcription ne tire aucune conclusion du texte : c'est la procédure du projet qui dit quoi en faire (fiche de visite, compte rendu…)
