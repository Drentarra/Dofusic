# Dofusic 🎵

[**Télécharger la dernière version**](https://github.com/Drentarra/Dofusic/releases/latest/download/Dofusic.zip)

Dofusic change automatiquement la musique de Dofus selon la zone où vous êtes et si vous êtes en combat.

Je l'ai fait pour pouvoir remplacer facilement les musiques du jeu tout en gardant des changements automatiques quand je me déplace ou que je lance un combat.

## Fonctionnement

**Désactivez les musiques de Dofus avant d'utiliser Dofusic.**

Dofusic lit la position et la zone affichées dans le jeu pour pouvoir :

- changer de musique selon la zone ;
- détecter les combats ;
- lancer une musique de combat ;
- reprendre la musique de zone après le combat ;
- utiliser des musiques génériques quand aucune musique particulière n'est prévue.

Si Dofus n'est pas ouvert, la détection automatique reste désactivée.

## Musiques personnelles

Le téléchargement officiel contient un dossier `Musiques` sans morceaux distribués avec le programme.

Ajoutez simplement vos propres fichiers dans ce dossier.

Formats pris en charge notamment :

`OPUS` `MP3` `OGG` `FLAC` `WAV` `M4A` `AAC` `WMA` `WEBM` `MKA` `AIFF` `AC3`

Le format conseillé est **Opus 64 kb/s** pour garder une bonne qualité avec des fichiers légers.

Les fichiers nommés `Musique.opus`, `Musique1.opus`, `Musique2.opus`, etc. servent de musiques génériques.

## Musique en ligne

L'onglet Online permet de rechercher et d'écouter une musique directement depuis Dofusic.

Les morceaux téléchargés par Dofusic sont enregistrés en Opus 64 kb/s afin de limiter leur poids.

## Installation

Dofusic est portable :

1. Téléchargez `Dofusic.zip` dans **Releases**.
2. Décompressez l'archive.
3. Lancez `Dofusic.exe`.
4. Lancez Dofus.

Il n'y a rien à installer.

Évitez simplement de déplacer ou supprimer les fichiers présents dans `Data`.

Les releases officielles sont construites par GitHub Actions. Les utilisateurs avancés peuvent vérifier la provenance du ZIP avec :

```powershell
gh attestation verify Dofusic.zip --repo Drentarra/Dofusic
```

## Premier lancement

Dofusic attend d'avoir détecté une première position valide avant d'activer complètement la gestion automatique des zones et des combats.

Ça évite que le programme change de musique pendant les écrans de connexion ou de sélection de personnage.

## Paramètres

Les paramètres permettent notamment de modifier :

- le volume ;
- la durée des fondus ;
- le visualiseur audio ;
- les options de musique en ligne ;
- le cache ;
- l'apparence de Dofusic.

Les réglages sont conservés entre les lancements.

## Code source

Le code source est dans le dossier `Data` et les tests automatisés dans `tests`.

Le build portable Windows peut être lancé avec `BUILD_PORTABLE.bat`.

## À savoir

Dofusic fonctionne actuellement sous **Windows 64 bits**.

Le programme utilise uniquement ce qui est affiché par Dofus pour reconnaître les zones et les positions. Il ne modifie pas les fichiers du jeu.

## Bugs / problèmes

Si vous trouvez un bug, ouvrez une **Issue** sur GitHub avec si possible :

- ce que vous faisiez au moment du problème ;
- votre version de Dofusic ;
- la zone concernée ;
- une capture d'écran si elle peut aider.

## Licence

Le code de Dofusic est distribué sous licence MIT.

## Dofus / Ankama

Dofusic est un projet indépendant.

Il n'est ni développé, ni approuvé, ni affilié à Ankama.

Dofus et les éléments associés à Dofus appartiennent à leurs propriétaires respectifs.

<img width="1896" height="692" alt="4" src="https://github.com/user-attachments/assets/30137adc-7de3-4af7-803d-bd43aaa2e442" />
<img width="1350" height="594" alt="3" src="https://github.com/user-attachments/assets/ab23abeb-01ab-42d3-afc0-43bc2836e0c8" />
<img width="1410" height="892" alt="2" src="https://github.com/user-attachments/assets/c007098e-f295-4e77-9e0c-792670179915" />
<img width="1209" height="957" alt="1" src="https://github.com/user-attachments/assets/d669c7be-27a1-4ecc-83e3-10d5b5ea3b09" />
