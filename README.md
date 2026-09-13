# Dofusic 🎵

[**Télécharger la dernière version**](https://github.com/Drentarra/Dofusic/releases/latest)

Dofusic change automatiquement la musique de Dofus selon la zone où vous êtes et si vous êtes en combat.

À la base je l'ai fait parce que j'avais envie de pouvoir remplacer les musiques du jeu facilement, tout en gardant des changements automatiques quand je me déplace ou que je lance un combat.

Il est aussi possible d'écouter ses propres musiques manuellement ou d'en chercher directement en ligne.

## Fonctionnement

Dofusic détecte la position et la zone affichées dans Dofus.

À partir de ça, il peut automatiquement :

- changer de musique selon la zone ;
- détecter le passage en combat ;
- lancer une musique de combat ;
- revenir à la musique de la zone à la fin du combat ;
- utiliser des musiques génériques lorsqu'aucune musique particulière n'est associée à la zone.

Tout se fait automatiquement une fois Dofus lancé.

Si Dofus n'est pas ouvert, la détection automatique reste désactivée.

## Musiques personnelles

Vous pouvez mettre vos propres morceaux dans le dossier `Musiques`.

Dofusic accepte notamment :

`OPUS` `MP3` `OGG` `FLAC` `WAV` `M4A` `AAC` `WMA` `WEBM` `MKA` `AIFF` `AC3`

et plusieurs autres formats pris en charge par FFmpeg.

### Format conseillé : Opus

**Le format recommandé pour Dofusic est l'Opus (`.opus`), idéalement en 64 kb/s.**

Il permet de réduire fortement la taille du dossier `Musiques` tout en gardant une qualité largement suffisante pour une écoute en jeu.

Les autres formats restent compatibles, il n'est donc pas obligatoire de convertir vos musiques.

Un script `CONVERTIR_EN_OPUS_64K.bat` est fourni avec Dofusic pour convertir facilement un dossier de musiques en Opus 64 kb/s sans supprimer les fichiers originaux.

Les musiques peuvent également être lancées manuellement depuis Dofusic.

## Musiques génériques

Les fichiers nommés :

`Musique.opus`  
`Musique1.opus`  
`Musique2.opus`  
`Musique3.opus`

etc. servent de musiques génériques.

Dofusic en choisit une aléatoirement lorsqu'il n'y a pas de musique particulière prévue pour la zone.

Le même principe est utilisé pour les musiques de combat.

## Musique en ligne

L'onglet Online permet de rechercher et d'écouter une musique directement depuis Dofusic.

Les morceaux téléchargés par Dofusic sont enregistrés en **Opus 64 kb/s** afin de limiter leur poids.

Après une écoute manuelle, Dofusic peut reprendre automatiquement la musique liée au jeu.

## Changements de musique

Les changements ne sont pas faits brutalement.

Dofusic utilise des fondus entre les morceaux, y compris pour :

- les changements de zone ;
- les combats ;
- les musiques locales ;
- les musiques lancées depuis l'onglet Online.

La durée du fondu peut être réglée dans les paramètres.

## Installation

Dofusic est portable.

1. Téléchargez la dernière version dans **Releases**.
2. Décompressez l'archive.
3. Lancez `Dofusic.exe`.
4. Lancez Dofus.

Il n'y a rien à installer.

Évitez simplement de déplacer ou supprimer les fichiers présents dans `Data`.

## Premier lancement

Dofusic attend d'avoir détecté une première position valide avant d'activer complètement la gestion automatique des zones et des combats.

Ça évite notamment que le programme commence à changer de musique pendant l'écran de connexion, la sélection ou la création d'un personnage.

En attendant, seules les musiques génériques peuvent être utilisées automatiquement.

## Paramètres

Les paramètres permettent notamment de modifier :

- le volume ;
- la durée des fondus ;
- le visualiseur audio ;
- les options de musique en ligne ;
- le cache ;
- l'apparence de Dofusic.

Les réglages sont conservés entre les lancements.

## Convertir ses musiques en Opus

Un script `CONVERTIR_EN_OPUS_64K.bat` est fourni pour ceux qui veulent réduire fortement la taille de leur dossier de musiques.

Placez le BAT dans le dossier contenant vos morceaux et lancez-le.

Il crée un nouveau dossier `Musiques` avec les morceaux convertis en **Opus 64 kb/s**.

Les fichiers originaux ne sont pas supprimés.

## Pourquoi Opus 64 kb/s ?

**Opus est le format conseillé pour utiliser vos propres musiques avec Dofusic.**

Il offre un très bon rapport qualité/poids et permet de réduire fortement la taille d'une bibliothèque musicale par rapport à des MP3 à haut débit.

Dofusic utilise également l'Opus 64 kb/s pour les musiques téléchargées depuis l'onglet Online.

## À savoir

Dofusic fonctionne actuellement sous **Windows 64 bits**.

Le programme utilise la lecture de ce qui est affiché par Dofus pour reconnaître les zones et les positions. Il ne modifie pas les fichiers du jeu.

Si l'interface de Dofus change fortement après une mise à jour, certaines détections peuvent nécessiter une mise à jour de Dofusic.

## Bugs / problèmes

Si vous trouvez un bug, vous pouvez ouvrir une **Issue** sur GitHub.

Si possible, indiquez :

- ce que vous faisiez au moment du problème ;
- votre version de Dofusic ;
- la zone concernée si le problème vient de la détection ;
- une capture d'écran si elle peut aider.

## Licence

Le code de Dofusic est distribué sous licence MIT.

## Dofus / Ankama

Dofusic est un projet indépendant.

Il n'est ni développé, ni approuvé, ni affilié à Ankama.

Dofus et les éléments associés à Dofus appartiennent à leurs propriétaires respectifs.
