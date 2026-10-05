## prerequisite — se connecter avec son compte Microsoft 365

sur la fiche « SharePoint & OneDrive », clique **Se connecter avec Microsoft** et choisis ton compte professionnel. Rien d'autre à installer ni à enregistrer.
- l'agent agit **avec tes droits** : il voit ton OneDrive, les sites SharePoint et les fichiers partagés avec toi, ni plus ni moins. Chaque personne de l'org connecte son propre compte
- comptes professionnels ou scolaires seulement : un compte Microsoft personnel (outlook.com, hotmail) n'a pas SharePoint
- ⚠️ **beaucoup d'organisations exigent qu'un administrateur Microsoft 365 autorise oto une première fois.** Si Microsoft affiche « approbation de l'administrateur requise », c'est ce cas : ton admin se connecte une fois de la même façon et coche « consentir au nom de votre organisation », puis chacun peut se connecter
- la connexion tient dans la durée ; elle tombe si le mot de passe change, si l'organisation la révoque ou après une longue inactivité : la fiche dit alors « à reconnecter »

## usage — trouver, lire, déposer un document

- « mes fichiers » → `sharepoint_file()` : la racine de ton OneDrive ; `path="Dossier/Sous-dossier"` pour descendre
- « le site Marketing » → `sharepoint_site(query="Marketing")`, ou par son adresse : `sharepoint_site(op="get", url="https://contoso.sharepoint.com/sites/Marketing")`
- « ses bibliothèques de documents » → `sharepoint_site(op="drives", site_id="…")` ; chaque `id` rendu est un `drive_id`
- « le contenu de ce dossier » → `sharepoint_file(drive_id="…", path="Contrats/2026")`
- « le OneDrive de Marie » → `sharepoint_file(user="marie@contoso.com")`, si elle l'a partagé avec toi
- « retrouve le contrat Dupont » → `sharepoint_file(op="search", query="Dupont contrat")` dans ton OneDrive, ou avec `drive_id=` dans une bibliothèque
- « lis ce document » → `sharepoint_file(op="download", drive_id="…", item_id="…")` : un Word ou un PowerPoint revient en texte (converti en PDF par Microsoft), un Excel en CSV, un PDF en texte
- chaque réponse est une vue resserrée (nom, taille, type, dossier, lien, dernière modification) ; `full=true` rend l'objet Microsoft Graph complet
- « dépose ce compte rendu » → `sharepoint_file(op="upload", drive_id="…", path="Comptes rendus", name="cr-2026-10-01.md", content_text="…")` ; un fichier binaire en `content_base64`

## note — ce qui trompe

- ⚠️ **un 403 ne veut pas dire que le fichier n'existe pas** : ton compte n'y a pas accès, ou ton organisation bloque oto
- ⚠️ **la recherche passe par l'index de SharePoint** : un fichier tout juste déposé peut ne pas y être encore. Pour le retrouver tout de suite, le lister par son dossier
- ⚠️ **un nom déjà pris est refusé** au dépôt (`conflict="fail"`, le défaut) : `conflict="rename"` garde les deux, `conflict="replace"` écrase
- la recherche de sites (`op="search"`) ne trouve pas les OneDrive personnels : pour celui d'un collègue, `user=`

## note — périmètre

fichiers seulement : sites, bibliothèques, OneDrive ; lister, chercher, lire (jusqu'à 50 Mo), déposer (jusqu'à 25 Mo), créer un dossier. Rien ne supprime, ne déplace ni ne partage un fichier. Le courrier Outlook, le calendrier et Teams ne passent pas par ce connecteur.
