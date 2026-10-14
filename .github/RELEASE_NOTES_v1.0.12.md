# Dofusic v1.0.12 — Consolidation et fiabilité

Cette version reprend intégralement les fonctionnalités de la v1.0.11, notamment la musique d'Incarnam, la reconnaissance des zones et coordonnées, les musiques de donjons prioritaires pendant les combats et la bibliothèque musicale en ligne.

## Améliorations

- Reprise automatique de la capture Dofus après certains échecs temporaires, avec des messages d'erreur moins répétitifs.
- Gestion renforcée des threads de capture et des processus OCR : arrêt, redémarrage, nettoyage des files et récupération après échec.
- Réduction des accès SQLite lorsque les coordonnées sont inchangées ; calibration HUD calculée une seule fois par image.
- Mise en cache bornée des correspondances de fichiers musicaux ; intégrité des cycles de musique générique préservée.
- Nettoyage des décodeurs FFmpeg à l'arrêt, cache de normalisation limité et sauvegardes de musique protégées contre l'écrasement concurrent.
- Optimisations de l'affichage Tkinter et des miniatures en ligne, avec limitation des tâches et de la mémoire conservée.
- Contrôle des identifiants YouTube avant accès au cache et consolidation des verrous concurrents.
- Version intégrée à l'exécutable corrigée pour correspondre à la release.

## Compatibilité

Le ZIP reste **portable pour Windows x64** : extraction puis lancement de `Dofusic.exe`, sans installer Python ou ses dépendances. Les musiques et correspondances géographiques de la v1.0.11 restent inchangées.

## Vérifications

La branche de développement a été auditée module par module. La CI Windows exécute les tests automatisés, un contrôle réel de démarrage/arrêt des deux processus OCR, un benchmark comparatif avec v1.0.11, puis compile et teste l'exécutable. Les résultats exacts sont disponibles dans les GitHub Actions du commit publié.

Les mesures synthétiques ont confirmé une amélioration de la résolution géographique pour des coordonnées constantes ; elles ne constituent pas une mesure globale des performances pendant une session Dofus. Une vérification en jeu reste recommandée, notamment pour les overlays, les transitions audio et les différents états de combat.

Les détails techniques figurent dans `AUDIT_TECHNIQUE.md` à la racine du dépôt.
