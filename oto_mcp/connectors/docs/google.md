## prerequisite — connecter un compte google (oauth)

va sur le **dashboard oto**, section Google, et clique **connect** : tu autorises oto en OAuth (pas de clé manuelle). tu peux connecter **plusieurs comptes** Google ; chaque outil agit sur le compte par défaut ou sur celui que tu cibles par son email.
- couvre Gmail, Tasks, Calendar, Sheets, Drive et Chat en une seule autorisation

## usage — gmail, agenda, tâches, sheets, drive, chat

agis sur ton Google Workspace : mails, calendrier, tâches, feuilles de calcul, fichiers Drive et messages Chat.
- « cherche les mails non lus de cette semaine et archive les newsletters »
- « rédige un brouillon de réponse à ce mail » ou « envoie-le »
- « qu'est-ce que j'ai à l'agenda demain ? crée un créneau de relance vendredi 10h »
- « ajoute une tâche `relancer X` pour lundi », « lis l'onglet `leads` de cette sheet »
- « partage ce dossier Drive en lecture à julien@… »

## note — périmètre de projet (#605, 2026-08-29)

une pièce jointe `{kind: "url"}` de `gmail_compose` est lue côté serveur : sous un projet à `excluded_url_prefixes`, une url correspondante est refusée en nommant le motif (seam `file_source`). détail : `docs/projects.md`.
