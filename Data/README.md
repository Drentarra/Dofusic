# Dofusic — architecture et construction

Dofusic lit la fenêtre Dofus via Win32/MSS, extrait les régions HUD et exécute l'OCR dans des processus séparés avec RapidOCR et ONNX Runtime CPU. La base SQLite est ouverte en lecture seule. Les décisions de zone, de position et de combat pilotent un lecteur de musique exclusivement local, avec priorité des musiques de donjon.

## Modules

- `dofusic/capture/` : découverte de la fenêtre, acquisition et synchronisation des images.
- `dofusic/vision/` : géométrie du HUD, reconnaissance OCR, état des combats et ordonnanceur.
- `dofusic/location/` : normalisation et résolution des lieux dans la base SQLite.
- `dofusic/audio/` : lecture, décodage, analyse de spectre, fondus et caches bornés.
- `dofusic/ui/` : interface graphique et surimpressions ; `dofusic/config.py` : paramètres locaux.

## Développement

Les dépendances Windows x64 sont verrouillées dans `requirements-lock.txt` ; RapidOCR est installé séparément et les modèles ONNX sont vérifiés par SHA-256. Depuis la racine, installer aussi `requirements-dev.txt`, puis lancer `python -m ruff check .` et `python -m pytest -q`. Les outils de maintenance et de benchmark résident dans `Data/tools/` et ne sont pas exécutés par le programme publié.

La géométrie en fonctionnement provient de la résolution de la fenêtre Dofus. Les anciens algorithmes de calibrage sur captures sont isolés dans `tools/hud_reference.py` pour valider les images de référence ; ils ne sont pas importés par le runtime. `tools/benchmark_hotpaths.py` permet de comparer deux arbres de sources avec les mêmes scénarios, sans modèles OCR.

Construire sous Windows x64 avec `BUILD_PORTABLE.bat`. Le script prépare le modèle OCR, teste l'application, crée une release PyInstaller portable et contrôle son contenu. Les pistes `.opus` présentes dans `Musiques/` sont nécessaires à la publication : le constructeur refuse un dossier sans OPUS.

La version compilée fonctionne **sans installer Python** et sans connexion Internet. Elle ne produit aucun fichier de journalisation. Les écritures concernent les préférences locales et, si nécessaire, les fichiers audio temporaires de conversion. Voir [PRIVACY.md](PRIVACY.md).
