# Dofusic Release Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Durcir la chaîne de build/release de Dofusic sans modifier le comportement de l’application ni le téléchargement utilisateur.

**Architecture:** `Dofusic.zip` reste l’unique téléchargement public et garde `Dofusic.exe`, `Data/` et `Musiques/`. La CI devient séparée de la publication, le pack Musiques et les dépendances sont figés, puis chaque release reçoit SHA256, SBOM, attestation GitHub et, dès activation du compte SignPath, une signature Authenticode.

**Tech Stack:** GitHub Actions Windows 2025, CPython 3.11.9, PyInstaller 6.22.2, PowerShell 7, CycloneDX 7.3.1, GitHub Attestations, SignPath.

**Spec:** `docs/superpowers/specs/2026-09-13-release-security-hardening-design.md`

## Global Constraints

- Le lien `releases/latest/download/Dofusic.zip` ne change pas.
- Aucun code OCR, combat, audio, online ou UI n’est modifié.
- Le ZIP final garde exactement un dossier `Dofusic/` avec `Dofusic.exe`, `Data/`, `Musiques/`.
- Python officiel : 3.11.9 x64.
- RapidOCR reste installé `--no-deps` et `opencv-python` reste absent.
- Toutes les actions GitHub sont épinglées à un SHA complet.
- Une release taggée échoue si tests, build, pack Musiques, ZIP, SBOM ou attestation échouent.

---

### Task 1: Documentation et sécurité dépôt

**Files:** `README.md`, `SECURITY.md`, `.github/dependabot.yml`, `tests/test_release_hardening.py`

- [ ] Ajouter un test vérifiant que README ne mentionne plus `CONVERTIR_EN_OPUS_64K.bat`, que `SECURITY.md` existe et que Dependabot couvre `pip` et `github-actions`.
- [ ] Lancer `python -m pytest tests\test_release_hardening.py -q` et constater l’échec.
- [ ] Retirer du README les deux mentions du BAT supprimé, garder la recommandation Opus 64 kb/s et ajouter la vérification SHA256 avec `Get-FileHash .\Dofusic.zip -Algorithm SHA256`.
- [ ] Créer `SECURITY.md` expliquant de ne pas publier immédiatement un détail exploitable dans une Issue publique et de passer par le profil GitHub du mainteneur pour un signalement privé.
- [ ] Créer `.github/dependabot.yml` avec mises à jour hebdomadaires `pip` dans `/Data` et `github-actions` dans `/`.
- [ ] Rejouer les tests puis commit `docs: ajoute la politique de sécurité et nettoie la documentation`.

### Task 2: Lock déterministe des dépendances

**Files:** `Data/requirements-lock.txt`, `BUILD_PORTABLE.bat`, `tests/test_release_hardening.py`

- [ ] Tester qu’un lock existe, que chaque ligne utile contient `==`, que `opencv-python-headless` est présent et que `opencv-python`/`rapidocr` sont absents.
- [ ] Créer le lock à partir du build v1.0.1 réussi : `onnxruntime==1.29.0`, `numpy==2.4.6`, `opencv-python-headless==4.14.0.94`, `rapidfuzz==3.14.6`, `mss==10.2.0`, `pywin32==312`, `Pillow==12.3.0`, `pygame==2.6.1`, `pyclipper==1.4.0`, `Shapely==2.1.2`, `six==1.17.0`, `PyYAML==6.0.3`, `tqdm==4.70.1`, `omegaconf==2.3.1`, `requests==2.34.2`, `colorlog==6.12.0`, `yt-dlp==2026.8.19`, `yt-dlp-ejs==0.8.0`, `imageio-ffmpeg==0.6.0`, `pyinstaller==6.22.2`, `pytest==9.1.1`, `flatbuffers==25.12.19`, `packaging==26.3`, `protobuf==7.36.1`, `altgraph==0.17.5`, `pefile==2024.8.26`, `pyinstaller-hooks-contrib==2026.7`, `pywin32-ctypes==0.2.3`, `colorama==0.4.6`, `antlr4-python3-runtime==4.9.3`, `charset-normalizer==3.5.1`, `idna==3.19`, `urllib3==2.7.0`, `certifi==2026.7.22`, `iniconfig==2.3.0`, `pluggy==1.6.0`, `Pygments==2.21.0`, `setuptools==65.5.0`.
- [ ] Modifier `BUILD_PORTABLE.bat` pour installer ce lock puis `rapidocr==3.9.2 --no-deps`.
- [ ] Lancer `python -m pytest -q`, puis commit `build: fige les dépendances du build public`.

### Task 3: Pack Musiques indépendant

**Files:** `.github/workflows/publish-music-pack.yml`, `Data/music-pack.json`, `tests/test_release_hardening.py`

- [ ] Ajouter un test exigeant `tag=music-v1`, `asset=Dofusic-Musiques-v1.zip` et un SHA256 hexadécimal de 64 caractères.
- [ ] Créer un workflow manuel qui télécharge `v1.0.1/Dofusic.zip`, vérifie le SHA `b282f7dabd465ba9d66ec78d9b1092e77dbb1c7082bf9c4f769784d7e6306c91`, extrait uniquement `Dofusic/Musiques`, refuse un pack vide, crée `Dofusic-Musiques-v1.zip` avec `Musiques/` à la racine, calcule son SHA256 et publie les deux assets sur la release `music-v1`.
- [ ] Exécuter une fois ce workflow, lire le digest réel de l’asset GitHub puis créer `Data/music-pack.json` avec ce digest exact.
- [ ] Rejouer le test puis commit `build: fige le pack musiques v1`.

### Task 4: CI lecture seule sur main et PR

**Files:** `.github/workflows/ci.yml`, `tests/test_release_hardening.py`

- [ ] Tester que CI contient `push`, `pull_request`, `contents: read`, `requirements-lock.txt` et aucun `contents: write`.
- [ ] Créer la CI avec `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1` (v7) et `actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97` (v7).
- [ ] Installer Python `3.11.9`, le lock, puis RapidOCR sans dépendances ; exécuter le garde OpenCV, `pytest -q`, `runtime_probe.py`, `prepare_models.py`.
- [ ] Ne télécharger aucune musique et ne publier aucun artefact.
- [ ] Rejouer tous les tests puis commit `ci: ajoute les contrôles en lecture seule sur main et les PR`.

### Task 5: Release durcie, SBOM et attestation

**Files:** `.github/workflows/build-release.yml`, `tests/test_release_hardening.py`

- [ ] Tester la présence des SHA : checkout `3d3c42e5aac5ba805825da76410c181273ba90b1`, setup-python `5fda3b95a4ea91299a34e894583c3862153e4b97`, upload-artifact `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a`, attest `1e69f48acb82d1966a394da916b4c1698aa569d6`.
- [ ] Donner au job release uniquement `contents: write`, `id-token: write`, `attestations: write`.
- [ ] Remplacer la récupération depuis `v1.0.0` par la lecture de `Data/music-pack.json`, téléchargement de l’asset `music-v1`, vérification stricte du SHA puis extraction de `Musiques/`.
- [ ] Utiliser Python `3.11.9`, `Data/requirements-lock.txt`, puis RapidOCR `--no-deps`.
- [ ] Garder `python Data/tools/build_portable.py --root .` et les validations finales `Dofusic.exe`, `Data`, `Musiques` non vide.
- [ ] Créer un venv `.sbom-venv`, installer `cyclonedx-bom==7.3.1`, puis générer `Release/Dofusic.sbom.json` avec `python -m cyclonedx_py requirements Data\requirements-lock.txt --output-reproducible --spec-version 1.6 --output-format JSON --output-file Release\Dofusic.sbom.json`.
- [ ] Sur tag `v*`, attester `Release/Dofusic.zip` avec `actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6`.
- [ ] Publier `Dofusic.zip`, `.sha256`, `Dofusic.sbom.json`, `BUILD_SIZE_REPORT.txt`.
- [ ] Rejouer les tests puis commit `build: ajoute provenance SBOM et vérification du pack musiques`.

### Task 6: Préparer SignPath / Authenticode

**Files:** `.github/workflows/build-release.yml`, `.signpath/README.md`, `README.md`, `tests/test_release_hardening.py`

- [ ] Tester la présence de l’action SignPath épinglée `signpath/github-action-submit-signing-request@c92b958760219087e01f8d67a1669ed57afe2627` (v2) et de `SIGNPATH_REQUIRED`.
- [ ] Documenter dans `.signpath/README.md` les paramètres externes à configurer : token API SignPath, organization ID, project slug, signing policy slug, artifact configuration slug et variable `SIGNPATH_REQUIRED`.
- [ ] Quand SignPath est configuré, uploader `Release/Dofusic.zip` avec `actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` et `archive: false`, soumettre son `artifact-id` à l’action SignPath épinglée, attendre le résultat puis remplacer le ZIP unsigned par le ZIP signé.
- [ ] Extraire le ZIP signé et vérifier `Get-AuthenticodeSignature Dofusic.exe`; exiger `Status=Valid` lorsque la signature est activée.
- [ ] Tant que `SIGNPATH_REQUIRED=false`, permettre les builds unsigned. Une fois l’onboarding SignPath terminé, passer la variable à `true`; toute release taggée unsigned doit alors échouer.
- [ ] Ajouter au README une phrase précise : les releases sont construites par GitHub Actions ; la signature Authenticode est appliquée dès que SignPath est activé ; SmartScreen peut néanmoins demander du temps pour construire la réputation.
- [ ] Rejouer les tests puis commit `build: prépare la signature Authenticode SignPath`.

### Task 7: Vérification finale avant v1.0.2

- [ ] Lancer `python -m pytest -q` et exiger 100 % de réussite.
- [ ] `git push` puis vérifier que la CI `main` est verte.
- [ ] Lancer manuellement le workflow de build sur `main`; il doit produire un artefact sans créer de release.
- [ ] Vérifier que l’artefact contient `Dofusic.zip`, `.sha256`, `Dofusic.sbom.json`, `BUILD_SIZE_REPORT.txt`.
- [ ] Vérifier que `Dofusic.zip` contient exactement `Dofusic/Dofusic.exe`, `Dofusic/Data/`, `Dofusic/Musiques/`.
- [ ] Faire un smoke test Windows : démarrage, lecture Opus locale, online, détection Dofus, OCR zone/position, combat.
- [ ] Vérifier le SHA avec `Get-FileHash` et le JSON du SBOM.
- [ ] Après une release taggée, vérifier la provenance avec `gh attestation verify Dofusic.zip --repo Drentarra/Dofusic`.
- [ ] Seulement après ces contrôles : `git tag v1.0.2` puis `git push origin v1.0.2`.
