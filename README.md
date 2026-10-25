# Dofusic 2.0.0

[**Télécharger la dernière version**](https://github.com/Drentarra/Dofusic/releases/latest/download/Dofusic.zip)

Dofusic adapte la musique locale à la zone et aux combats de **Dofus**, à partir de la fenêtre du jeu. Application indépendante pour Windows x64.

Cette archive contient le **code source**, les tests et les ressources de référence. Elle ne contient ni exécutable, ni modèles OCR téléchargés, ni pistes musicales. Les changements et les mesures de cette version sont détaillés dans [CHANGELOG.md](CHANGELOG.md) et [l'audit V2.0.0](docs/AUDIT_V2.0.0.md).

## Utilisation

1. Désactivez la musique de Dofus pour éviter deux lectures simultanées.
2. Extrayez `Dofusic.zip` puis lancez `Dofusic.exe`.
3. Ouvrez Dofus en plein écran. Dofusic détecte sa fenêtre et attend la première position valide avant de lancer la sélection automatique.
4. Placez vos morceaux dans `Musiques/`, en conservant les noms attendus pour les zones et les musiques génériques (`Musique.opus`, `Musique1.opus`, etc.).

La fenêtre « Musiques locales » permet également la recherche, la lecture manuelle, la file d'attente, la répétition et le retour au mode automatique. Formats audio pris en charge : OPUS, MP3, OGG, FLAC, WAV, M4A, AAC, WMA, WEBM, MKA, AIFF et AC3. **Opus 64 kb/s** constitue un compromis intéressant pour réduire la taille des musiques.

**Le ZIP de la release GitHub Actions contient** `Dofusic.exe`, `Data/` et les pistes du dossier `Musiques/` suivies dans le dépôt. Le compilateur refuse les publications sans fichier OPUS ; les fichiers audio doivent donc être présents avant la compilation. La distribution ne nécessite ni Python, ni installation, ni compte en ligne.

## Vie privée et fichiers locaux

La reconnaissance des zones, positions et combats fonctionne localement. Le logiciel ne contient ni télémétrie applicative, ni client réseau pour transférer les images, ni mécanisme d'enregistrement des écrans. **Aucun journal de diagnostic ou de plantage n'est écrit par Dofusic.**

Les préférences (volume, apparence, dossiers, etc.) sont enregistrées dans `Data/UserData/config.json` lorsque l'emplacement est inscriptible, sinon dans `%LOCALAPPDATA%\Dofusic\config.json`. Les données temporaires de capture et d'OCR restent en mémoire. Si un format audio nécessite une conversion de secours, FFmpeg crée un fichier audio temporaire, nettoyé après utilisation. Ces garanties ne concernent pas les mécanismes propres à Windows, à l'antivirus ou à GitHub Actions. Informations complémentaires : [politique de confidentialité](Data/PRIVACY.md).

## Code source et compilation

Le runtime est dans `Data/dofusic/`, les ressources dans `Data/`, la compilation dans `Data/tools/build_portable.py` et la suite de tests dans `tests/`.

Sur Windows x64, ajoutez vos pistes dans `Musiques/`, dont au moins un fichier `.opus` non vide, puis exécutez `BUILD_PORTABLE.bat`. Le script utilise un environnement de compilation isolé, télécharge les dépendances et les modèles OCR vérifiés, exécute les tests, construit une distribution PyInstaller *onedir* et vérifie le portable avant publication. Ces accès réseau n'interviennent pas lors de la lecture musicale chez l'utilisateur.

Pour exécuter les tests depuis la racine du projet (avec les dépendances installées) :

```bash
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m pytest -q
```

La CI exécute les tests, l'analyse statique et les contrôles OCR même dans un dépôt sans musique. Elle construit aussi le portable lorsque les pistes sont présentes. Le build local et le workflow de publication restent stricts : ils refusent un portable sans OPUS. Après construction, `VERIFIER_PORTABLE_WINDOWS.ps1` permet de contrôler plusieurs cycles de démarrage et de fermeture.

Le build embarque les modèles OCR ONNX requis, la base de données de zones, le moteur audio local et les bibliothèques nécessaires, sans inclure les tests, journaux ni données utilisateur. Les rapports de build et les empreintes SHA-256 restent hors du ZIP public.

## Problèmes et contributions

Ouvrez une [issue](https://github.com/Drentarra/Dofusic/issues) avec le contexte, la version et les étapes de reproduction. Ne joignez pas de données personnelles, de captures sensibles ou de dossiers de configuration. Les vulnérabilités sont à signaler selon [SECURITY.md](SECURITY.md).

## Licence et indépendance

Le code source est distribué sous [licence MIT](LICENSE). Dofusic est indépendant d'Ankama ; il n'est ni développé, ni approuvé, ni affilié à Ankama. Les marques Dofus et Ankama appartiennent à leurs titulaires.
