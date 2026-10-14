# Publier Dofusic v1.0.12 depuis GitHub Actions

L'application Windows reste portable. Le workflow d'origine **Build and release Dofusic** compile Dofusic.exe, effectue l'autotest, vérifie le ZIP et le SBOM, puis publie une release contenant **Dofusic.zip**. Aucun script local, Python ou paquet de patchs n'est nécessaire pour l'utilisateur final.

## Préparer une version officielle

1. Vérifier que la PR d'audit est fusionnée dans `main` et que son workflow CI est **vert**.
2. Ouvrir [GitHub Actions — Build and release Dofusic](https://github.com/Drentarra/Dofusic/actions/workflows/build-release.yml).
3. Cliquer sur **Run workflow** ; choisir la branche **main**.
4. Saisir `v1.0.12` dans **release_tag**.
5. Cocher **create_tag_from_main** : le tag n'est créé **qu'après** compilation, validation du ZIP, hash et SBOM.
6. Lancer le workflow et vérifier que les deux jobs ont réussi.
7. Aller sur [Releases](https://github.com/Drentarra/Dofusic/releases). La version `v1.0.12` contient un ZIP portable téléchargeable sous le nom `Dofusic.zip`. Les notes françaises proviennent de `.github/RELEASE_NOTES_v1.0.12.md`.

**Ne pas cocher la case ni saisir de tag pour effectuer uniquement une construction de vérification.** Dans ce cas, choisissez la branche main, laissez `release_tag` vide et lancez le workflow. Il fournit des artefacts testés **sans publier une release**.

## En cas d'échec

- Tant que les étapes de vérification sont en échec, le tag n'est pas créé.
- Si un tag a été créé mais que la publication a échoué, relancer le même workflow avec `release_tag=v1.0.12` et **create_tag_from_main décoché**. Le workflow reconstruit à partir du tag existant, sans le déplacer.
- Si le tag existe déjà, ne pas cocher la case de création : il est interdit d'écraser une autre version.
- Ne pas relancer une ancienne release en modifiant artificiellement les notes ou le contenu du ZIP : utiliser son tag réel.

## Vérifications humaines préalables à la diffusion

Les tests CI, le contrôle des processus OCR et l'autotest du binaire ne remplacent pas un démarrage sur votre Windows avec Dofus réellement ouvert. Vérifier en particulier : Incarnam, entrée/sortie de donjon, combat, OCR position et zone, cadres translucides, lecture et normalisation, recherche en ligne, transitions et sauvegarde des paramètres.
