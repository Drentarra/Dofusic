# Confidentialité — Dofusic V25.1 ECO

Dofusic est une application portable locale. Les réglages, logs et caches restent dans le dossier `Data/UserData/` de la copie utilisée.

## Données locales

L'OCR analyse uniquement les zones d'écran nécessaires à la détection de Dofus lorsque le jeu est ciblé. Les résultats servent à déterminer la zone/position et la musique à jouer. Dofusic n'envoie pas ces captures à un serveur Dofusic.

## Musique online

Quand l'utilisateur utilise volontairement la fonction online, Dofusic effectue des requêtes réseau nécessaires à la recherche/suggestion et à la récupération du média via yt-dlp. Ces requêtes sont soumises aux règles et politiques des services contactés.

V25 n'embarque **aucun navigateur WebView2**, ne possède **aucune Session YouTube**, ne demande **aucun mot de passe développeur**, ne lit pas les profils Chrome/Edge/Firefox et ne crée pas de `cookies.txt`.

## Portabilité

Aucune installation Dofusic et aucun Python système ne sont requis pour la release. Le mode local/OCR ne nécessite pas Internet. Les journaux peuvent contenir des informations techniques (chemins, erreurs, noms de zones/pistes) et restent locaux au dossier portable.
