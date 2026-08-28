## prerequisite — ta clé api lever

il te faut une clé **api lever**.
- dans lever, va dans **settings → integrations and API → API credentials** et génère une clé
- colle-la dans tes [clés de connecteurs](https://manage.oto.cx/) (ou laisse ton org partager la sienne)
- doc éditeur : [lever.co](https://www.lever.co)
- ⚠️ les écritures (créer un candidat, ajouter une note) exigent un `perform_as` = l'id d'un utilisateur lever, récupéré via `lever_users`

## usage — ce que tu peux faire

pilote ton ats lever : un candidat = une **opportunity**, un poste = un **posting**.
- « liste les candidats du posting abc » → `lever_opportunities` (filtres `posting_id`, `stage_id`, `email` ; `expand` pour déplier stage/owner)
- « détaille l'opportunity xyz » → `lever_opportunity`
- « ajoute une note sur cette opportunity » → `lever_add_note` (avec `perform_as`, cf. `lever_users`)
- « quels sont mes postes publiés ? » → `lever_postings` (`state` published/closed/draft…) ; les étapes de pipeline → `lever_stages`
