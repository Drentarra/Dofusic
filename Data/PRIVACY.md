# Confidentialité — Dofusic

## Données traitées

Dofusic examine les régions utiles de la fenêtre Dofus afin de reconnaître la zone, les coordonnées et le combat. Les images et résultats OCR sont traités en mémoire par le logiciel et ses processus de reconnaissance ; ils ne sont pas enregistrés par l'application sur disque et ne sont pas transmis à un service distant.

Le lecteur et les analyses audio fonctionnent sur des fichiers locaux. Aucune connexion, authentification, service de publicité ou télémétrie propre à Dofusic n'est nécessaire à l'exécution de l'application.

## Données conservées

Dofusic stocke ses préférences applicatives dans `Data/UserData/config.json`, ou, si l'emplacement portable n'est pas inscriptible, dans `%LOCALAPPDATA%\Dofusic\config.json`. Cette configuration peut comprendre le chemin de votre bibliothèque musicale, le volume et l'apparence sélectionnée. Les caches de reconnaissance et d'analyse musicale sont en mémoire.

Lorsqu'un morceau nécessite une conversion de secours pour le lecteur, FFmpeg écrit un fichier audio temporaire dans le dossier temporaire du système. L'application tente de le supprimer après utilisation. Un fichier encore verrouillé par Windows peut subsister ; le nettoyage des conversions anciennes est retenté après sept jours lors d'une prochaine conversion. Ce fichier contient de l'audio local, pas des captures du jeu.

Dofusic ne crée ni fichiers `.log`, ni rotation de journaux, ni rapport de plantage, ni historiques des zones ou des morceaux écoutés. L'option de journalisation de diagnostic a été supprimée. Les journaux d'une ancienne version, s'ils existent déjà sur la machine, ne sont **pas** effacés automatiquement : l'utilisateur doit décider de leur suppression après sauvegarde éventuelle.

## Limites de la politique

Cette politique décrit le code applicatif livré. Elle ne couvre pas les journaux, crash dumps ou rapports éventuellement créés par Windows, la plate-forme de jeu, les pilotes, l'antivirus ou d'autres logiciels. Les pipelines GitHub Actions et le téléchargement des dépendances/modèles lors du build nécessitent Internet ; ils sont distincts du logiciel portable publié.
