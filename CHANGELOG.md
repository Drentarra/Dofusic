# Historique

## 2.0.0 — 25 octobre 2026

### Exécution et fiabilité

- Cache borné à une entrée pour les correspondances de texte OCR répétées ; les coordonnées continuent à recalculer le classement à chaque observation.
- Suppression des lectures audio et des dessins de barres lorsque le visualiseur est désactivé ; attente de 250 ms au lieu de 41 ms dans ce cas.
- Conservation des résultats de recherche musicale inchangés pour éviter de reconstruire les lignes et de perdre le défilement.
- Annulation des demandes audio périmées avant décodage ou analyse ; les fichiers temporaires verrouillés ne bloquent plus l'arrêt du lecteur.
- Correction du positionnement des fenêtres FFT lorsque leur pas dépasse leur largeur : le résultat ne dépend plus du découpage des blocs audio.
- Protection des échanges OCR pendant la fermeture ou le remplacement des files ; nettoyage si leur création échoue.
- Le consensus des coordonnées compte les variantes OCR distinctes, sans donner plusieurs voix à une même variante.
- Confirmation géographique fonctionnelle jusqu'à dix observations ; rejet des observations répétées, désordonnées ou à horodatage non fini.
- Lecture des configurations corrompues renforcée et normalisation des préférences booléennes. Le schéma de configuration reste à 27 pour conserver les préférences compatibles.

### Nettoyage et distribution

- Calibrage historique des captures déplacé dans les outils hors runtime, avec conservation des tests de référence.
- Suppression des anciens réexports audio, imports inutilisés, variables mortes, anciens champs de diagnostic de combat et compteurs sans effet.
- Configuration Ruff et dépendances de développement verrouillées ; correction du calcul de complexité des fonctions imbriquées.
- Test SQLite compatible Windows : `?` reste testé uniquement sur les systèmes qui l'autorisent dans les noms de fichiers ; `#`, `%`, espaces et accents restent vérifiés sur Windows.
- Vérification d'intégrité de tous les formats musicaux distribués, au-delà des seuls OPUS.
- CI utilisable pour les sources sans musique ; publication toujours conditionnée à un dossier musical valide.
- SBOM générée dans le véritable environnement de compilation puis transférée au job de publication, sans réinstallation complète des dépendances dans ce dernier.
- Documentation des fichiers audio temporaires et ajout d'un benchmark comparatif reproductible.

Les mesures, résultats des vérifications et limites sont consignés dans [l'audit V2.0.0](docs/AUDIT_V2.0.0.md).
