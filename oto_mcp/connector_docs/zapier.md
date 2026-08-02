## prerequisite — ta clé api zapier ai actions

zapier expose aux agents un catalogue d'**actions** que tu autorises explicitement — pas une api de gestion des zaps.
- va sur [actions.zapier.com](https://actions.zapier.com), choisis les actions à exposer
- récupère la clé api associée (en-tête `x-api-key`) ; le jeu d'actions exposées est attaché à cette clé
- colle-la dans tes clés de connecteur oto sous `zapier`

## usage — exécuter tes actions zapier en langage naturel

découvre les actions autorisées et lance-les via une directive en langage naturel.
- `zapier_list_actions` liste les actions exposées par ta clé (id, description, champs)
- `zapier_execute_action` lance une action via son `action_id` + des `instructions` (zapier remplit les champs laissés en mode « ai guess »)
- passe `preview_only=True` pour voir ce qui serait fait sans l'exécuter
- `zapier_execution_log` te donne le détail d'une exécution
