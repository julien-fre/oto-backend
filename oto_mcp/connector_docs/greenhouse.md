## prerequisite — ta clé api greenhouse (harvest)

il te faut une clé **harvest api** greenhouse.
- dans greenhouse, va dans **configure → dev center → api credentials** et crée une clé de type *harvest*
- donne-lui les permissions candidats/jobs/applications/users
- colle-la dans tes [clés de connecteurs](https://manage.oto.cx/) (ou laisse ton org partager la sienne)
- doc éditeur : [greenhouse.io](https://www.greenhouse.io)
- ⚠️ les écritures (créer un candidat, ajouter une note) exigent un `on_behalf_of` = l'id d'un utilisateur greenhouse, récupéré via `greenhouse_users`

## usage — ce que tu peux faire

pilote ton ats greenhouse depuis la conversation : candidats, jobs, candidatures, notes.
- « liste les candidats sur le job 123 » → `greenhouse_candidates` (filtres `job_id`, `email`, `created_after`)
- « montre-moi le candidat 456 et ses candidatures » → `greenhouse_candidate`
- « ajoute une note sur le candidat 456 » → `greenhouse_add_note` (il faut un `user_id` auteur, cf. `greenhouse_users`)
- « quels jobs sont ouverts ? » → `greenhouse_jobs` (`status` open/closed/draft)
