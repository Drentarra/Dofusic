# Dofusic V25.1 ECO — portable Windows x64

Dofusic détecte automatiquement la zone et la position de **Dofus.exe** par OCR, puis joue les musiques locales associées. La musique online est optionnelle et utilise un moteur léger basé sur **yt-dlp** : aucun navigateur intégré, aucune session YouTube, aucun compte et aucun cookie ne sont nécessaires.

## Version portable

La release finale contient `Dofusic.exe`, `Data/`, `Musiques/` et les fichiers nécessaires au runtime. Aucun Python système, pip, droit administrateur ou installation Dofusic n'est requis sur le PC utilisateur.

Le mode local/OCR fonctionne hors ligne. Internet n'est utilisé que pour la recherche, les suggestions et la récupération de musique online.

## Architecture V25

- OCR zone : RapidOCR / ONNX Runtime avec **PP-OCRv6 Small** uniquement en temps réel.
- OCR position : pipeline dédié et léger.
- Capture : ciblée sur `Dofus.exe`, cadence limitée afin d'éviter les boucles inutiles.
- Audio local et online : même lecteur Dofusic ; l'online est préparé en cache par yt-dlp/FFmpeg.
- Visualiseur : FFT limitée en fréquence ; aucun décodage de spectre supplémentaire si le visualiseur est désactivé.
- Recherche online : client yt-dlp léger + suggestions HTTP, sans WebView2/Chromium embarqué.
- Données utilisateur : `Data/UserData/` reste local au dossier portable.

## Build du ZIP public

Sous Windows x64, lancer `BUILD_PORTABLE.bat`. Le builder utilise son propre environnement dans `.portable-build/` et ne modifie pas le Python éventuellement installé sur le PC.

Artefact attendu :

`Release\Dofusic_V25_1_ECO_WINDOWS_X64.zip`

Le builder exécute les tests, valide les dépendances, prépare le modèle OCR, construit l'application avec PyInstaller, lance l'auto-test du `.exe`, valide la release puis crée le ZIP.

## Dossiers utilisateur

- `Musiques/` : bibliothèque locale partageable/modifiable.
- `Data/UserData/config.json` : préférences.
- `Data/UserData/logs/` : journaux locaux.
- cache online : borné et nettoyable depuis les préférences prévues par l'application.

Lors d'une migration depuis une ancienne version, l'ancien profil `Data/UserData/webview2/` appartenant à Dofusic est supprimé automatiquement, car V25 n'utilise plus de navigateur intégré.

## Conseils de distribution

Pour Reddit ou un autre partage public, distribuer **uniquement le ZIP généré par le builder**, pas le dossier `.portable-build/`, ni les caches/tests de développement. L'utilisateur extrait le ZIP dans un dossier inscriptible puis lance `Dofusic.exe`.

## Profil ECO V25.1

- Capture Dofus : 10 FPS.
- Rafraîchissement UI : 80 ms.
- OCR adaptatif : une empreinte 32x8 détecte les changements du HUD et évite de relancer ONNX sur une image identique ; rafraîchissement de sécurité toutes les 2 s (zone) / 1 s (position).
- Visualiseur audio désactivé par défaut afin d'éviter un décodage FFmpeg supplémentaire.
- Suggestions Internet : endpoint JSON léger `client=firefox&ds=yt`.
- Miniatures YouTube : `mqdefault` et un seul téléchargement simultané.

La première lecture d'un morceau Internet télécharge toujours l'audio dans le cache local. Les relectures utilisent ensuite le cache tant que le fichier est présent.
## Audio et stockage

- Les musiques téléchargées depuis l'onglet online sont normalisées en **Opus 64 kb/s** avant d'être mises en cache ou ajoutées dans `Musiques/`.
- La bibliothèque locale reconnaît les principaux formats audio pris en charge par FFmpeg (MP3, Opus, OGG, FLAC, WAV, M4A, AAC, WMA, WebM/MKA, MP4 audio, AIFF, AC3, APE, WavPack, TTA, AMR, CAF...).
- Si pygame ne sait pas décoder directement un fichier local, Dofusic utilise FFmpeg comme solution de repli temporaire sans modifier l'original.
- `CONVERTIR_EN_OPUS_64K.bat` peut être posé dans un dossier de musiques : il crée un sous-dossier `Musiques` et convertit les fichiers en Opus 64 kb/s sans toucher aux originaux.
- Le build portable embarque QuickJS-NG et FFmpeg une seule fois et vérifie les gros doublons runtime avant de publier le ZIP.

