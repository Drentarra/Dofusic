# Dofusic — design de durcissement des releases

Date : 2026-09-13

## Objectif

Rendre la distribution publique de Dofusic plus propre, vérifiable et rassurante sans changer l’expérience utilisateur : un seul lien télécharge `Dofusic.zip`, qui contient `Dofusic.exe`, `Data/` et `Musiques/`.

Le fonctionnement de Dofusic ne doit pas être modifié par ce chantier. Les changements portent sur le build, la provenance, la signature, les dépendances et la documentation.

## État actuel

- Le dépôt est public et sous licence MIT.
- Une GitHub Action Windows compile et teste Dofusic.
- Les releases de tag publient `Dofusic.zip`, son SHA256 et un rapport de taille.
- Le workflow récupère actuellement le dossier `Musiques` depuis la release `v1.0.0`.
- Les dépendances Python sont partiellement épinglées, mais plusieurs utilisent des plages de versions.
- Les GitHub Actions sont référencées par tags majeurs (`@v7`) et non par SHA immuable.
- Il n’existe pas encore de SBOM, attestation de provenance GitHub, politique SECURITY ni configuration Dependabot.
- `README.md` mentionne encore `CONVERTIR_EN_OPUS_64K.bat`, alors que ce fichier a été retiré.
- `Dofusic.exe` n’est pas encore signé avec une identité éditeur reconnue.

## Architecture cible

### 1. Expérience utilisateur inchangée

Le lien public reste :

`https://github.com/Drentarra/Dofusic/releases/latest/download/Dofusic.zip`

Le contenu final reste :

```text
Dofusic/
├── Dofusic.exe
├── Data/
└── Musiques/
```

Les fichiers techniques supplémentaires (`.sha256`, SBOM, rapport de taille) restent des assets séparés de la release et ne compliquent pas le téléchargement principal.

### 2. Pack Musiques séparé et versionné

Le pack de musiques ne doit plus être extrait d’une ancienne release applicative.

Créer une release dédiée et stable, par exemple :

- tag : `music-v1`
- asset : `Dofusic-Musiques-v1.zip`
- asset : `Dofusic-Musiques-v1.zip.sha256`

Le dépôt contient un petit fichier de métadonnées avec :

- tag du pack ;
- nom de l’asset ;
- SHA256 attendu.

Le workflow télécharge le pack exact et refuse le build si son SHA256 ne correspond pas. Le pack est ensuite injecté dans `Release/Dofusic/Musiques` avant création du ZIP final.

Cela supprime la dépendance circulaire envers `v1.0.0` tout en conservant un seul ZIP pour l’utilisateur.

### 3. CI et release séparées

Deux responsabilités :

- **CI** sur push et pull request : installation, tests, vérifications statiques, build de contrôle ; aucune publication.
- **Release** sur tag `v*` : build complet, signature si configurée, vérification, SBOM, attestation, publication.

La release ne doit jamais être publiée si un test, une vérification d’intégrité, le build ou la validation du ZIP échoue.

### 4. Permissions minimales

Les permissions GitHub Actions sont définies au niveau des jobs.

Le job CI reste en lecture seule.

Le job de release reçoit uniquement les droits nécessaires :

- `contents: write` pour la release ;
- `id-token: write` pour l’attestation ;
- `attestations: write` pour publier la provenance.

Aucune permission inutile n’est accordée.

### 5. Actions GitHub immuables

Toutes les actions tierces (`checkout`, `setup-python`, `upload-artifact`, attestation, SignPath) doivent être épinglées sur un SHA complet vérifié, avec un commentaire indiquant la version lisible correspondante.

Cette règle évite qu’un tag d’action puisse changer silencieusement.

### 6. Dépendances Python verrouillées

Le build Windows doit utiliser un lock déterministe des dépendances runtime et build.

Approche retenue :

1. conserver `requirements.txt` comme liste lisible des dépendances directes ;
2. générer un lock Windows/Python 3.11 avec versions exactes ;
3. installer les builds publics depuis ce lock ;
4. mettre le lock à jour volontairement via une PR dédiée, pas automatiquement pendant une release.

RapidOCR reste installé sans ses dépendances automatiques afin de conserver un seul OpenCV headless, comme aujourd’hui.

### 7. SBOM

Chaque release génère un SBOM CycloneDX décrivant les dépendances réellement installées dans l’environnement de build.

Asset publié :

`Dofusic.sbom.json`

Le SBOM est créé après installation des dépendances et avant publication de la release.

### 8. Attestation de provenance GitHub

`Dofusic.zip` reçoit une attestation de build GitHub.

Elle doit relier cryptographiquement l’archive publiée au :

- dépôt ;
- workflow ;
- commit SHA ;
- événement de build.

Le README documente une commande de vérification avec GitHub CLI pour les utilisateurs avancés.

L’attestation complète le SHA256 : le hash vérifie l’intégrité du fichier ; l’attestation établit sa provenance de build.

### 9. Signature Windows / SmartScreen

Objectif : signer `Dofusic.exe` avant de créer `Dofusic.zip`.

Voie prioritaire : **SignPath Foundation** si Dofusic est accepté comme projet open source éligible.

Le workflow de release est préparé pour SignPath, mais la signature ne devient obligatoire qu’après configuration des secrets/variables SignPath et approbation du projet.

Flux de signature :

1. PyInstaller produit `Dofusic.exe` non signé ;
2. l’artefact contenant l’EXE est envoyé à SignPath depuis GitHub Actions ;
3. SignPath renvoie l’EXE signé ;
4. le workflow remplace uniquement l’EXE non signé par l’EXE signé ;
5. validation Authenticode ;
6. création du ZIP final ;
7. SHA256 + SBOM + attestation + release.

Le pack Musiques n’a aucune influence sur la signature de l’EXE.

Si SignPath Foundation refuse le projet, la même architecture pourra être branchée sur un certificat OV ou Microsoft Artifact Signing sans modifier l’expérience utilisateur.

Une signature valide ne garantit pas la disparition immédiate de SmartScreen : la réputation de l’éditeur et du fichier doit encore se construire. Le but est d’avoir une identité éditeur cohérente et vérifiable sur toutes les versions futures.

### 10. Contrôles avant publication

La release échoue si :

- `Dofusic.exe` est absent ;
- `Data/` est absent ;
- `Musiques/` est absent ou vide ;
- le SHA du pack Musiques est incorrect ;
- les tests échouent ;
- les contrôles anti-doublons échouent ;
- l’auto-test de l’EXE échoue ;
- le ZIP final n’a pas exactement un dossier racine `Dofusic/` ;
- la signature est requise mais invalide ;
- le SBOM ou l’attestation n’a pas été généré pour une release officielle.

### 11. Documentation et sécurité du dépôt

Ajouter :

- `SECURITY.md` : méthode de signalement d’un problème de sécurité ;
- `.github/dependabot.yml` : mises à jour GitHub Actions et Python ;
- documentation de vérification SHA256 / attestation ;
- section signature Windows dans le README ;
- correction du README pour retirer les mentions du BAT convertisseur supprimé.

Dependabot ouvre des PR ; il ne modifie jamais directement une release.

### 12. Ce qui ne change pas

- OCR et détection Dofus ;
- logique de combat ;
- lecteur audio ;
- recherche online ;
- formats audio ;
- architecture `Dofusic.exe + Data + Musiques` ;
- lien de téléchargement public ;
- comportement portable sans installation.

## Ordre d’implémentation

1. Nettoyer la documentation obsolète.
2. Créer le pack Musiques dédié et son SHA256.
3. Ajouter les métadonnées de pack.
4. Verrouiller les dépendances Windows.
5. Séparer CI et release.
6. Épingler toutes les actions par SHA.
7. Ajouter permissions minimales.
8. Ajouter SBOM.
9. Ajouter attestation GitHub.
10. Ajouter `SECURITY.md` et Dependabot.
11. Préparer l’intégration SignPath.
12. Tester une release candidate complète sans remplacer une release publique existante.
13. Une fois SignPath configuré et validé, rendre la signature obligatoire pour les tags officiels.

## Critères de réussite

Le chantier est terminé lorsque :

- un tag `vX.Y.Z` produit automatiquement une release ;
- `Dofusic.zip` contient exactement l’application portable complète avec les musiques ;
- les sources et le workflow ayant produit le ZIP sont traçables ;
- le pack Musiques est versionné et vérifié par SHA ;
- les dépendances du build sont verrouillées ;
- la release possède SHA256, SBOM et attestation de provenance ;
- l’EXE est signé dès que l’identité SignPath est disponible ;
- aucune modification fonctionnelle de Dofusic n’a été introduite par ce chantier ;
- README et documentation correspondent réellement aux fichiers distribués.
