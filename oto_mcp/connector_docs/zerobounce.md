## prerequisite — obtenir une clé zerobounce

crée une clé api dans les réglages api de ton compte [zerobounce](https://www.zerobounce.net).
- colle-la dans tes connecteurs oto sur `/account` — zerobounce est **byo** (chacun sa clé)
- la clé consomme les crédits de vérification de ton compte

## usage — vérifier la délivrabilité d'emails

valide une ou plusieurs adresses email avant un envoi (statut valid, invalid, catch-all, spamtrap…).
- `zerobounce_verify_email` — vérifie une adresse
- `zerobounce_verify_batch` — jusqu'à 200 adresses en un appel
- `zerobounce_credits` — crédits de vérification restants
