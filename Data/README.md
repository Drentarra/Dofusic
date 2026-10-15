# Dofusic — musique locale portable Windows x64

Dofusic détecte la fenêtre Dofus, reconnaît les coordonnées et les zones à l'aide de RapidOCR/ONNX Runtime, puis sélectionne les musiques **locales** de `Musiques/`. La priorité des donjons reste supérieure à celle des combats.

L'application ne contient plus de recherche de musiques en ligne, de téléchargement, de miniatures YouTube, de moteur yt-dlp ni de runtime QuickJS. Aucun compte et aucun accès réseau ne sont nécessaires pendant la lecture. La connexion n'est utilisée qu'au moment du **build** pour installer les dépendances et récupérer les modèles OCR de référence si nécessaire.

## Distribution portable

Le ZIP généré contient `Dofusic.exe`, `Data/`, `Musiques/` et les bibliothèques Windows x64. Après extraction, lancer `Dofusic.exe` sans installer Python, pip ou d'autres dépendances.

## Musiques

La fenêtre « Musiques locales » permet de filtrer les morceaux du dossier `Musiques`, de lire un morceau, de l'ajouter à la file, de l'en retirer, de répéter le morceau courant et de reprendre la sélection automatique de Dofus.

Les fichiers MP3, Opus, OGG, WAV, FLAC et autres formats pris en charge par le moteur audio restent disponibles. FFmpeg (via imageio-ffmpeg) est conservé pour l'analyse du spectre et la normalisation. Le lecteur manuel utilise le même moteur que la détection automatique.

## Compilation

Lancer `BUILD_PORTABLE.bat` ou les workflows GitHub Actions existants. Le constructeur exécute les tests, prépare le modèle OCR vérifié, produit le répertoire PyInstaller, lance l'autotest de `Dofusic.exe` et crée le ZIP.

Le modèle OCR, la base SQLite, les correspondances de zones, les musiques incluses et leurs fichiers de licences ne sont pas modifiés par cette refonte.
