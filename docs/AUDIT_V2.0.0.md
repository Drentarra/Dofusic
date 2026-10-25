# Audit et validation — Dofusic 2.0.0

Date : 25 octobre 2026. Référence : archive source Dofusic 1.0.27 fournie. Livraison : sources V2.0.0, sans publication ni construction Windows effectuée ici.

## Résultat et périmètre

Les modules de capture, vision/OCR, localisation, audio, interface, configuration et contrôle, les outils de maintenance, les scripts de compilation et les workflows ont été examinés. Le contrôle statique couvre **164 fichiers Python et 22,740 lignes**, tests compris. Les 214 fichiers livrés ont été inventoriés et empreintés. Aucune erreur de syntaxe ni définition masquée n'a été détectée. Le contrôle Ruff passe avec les règles explicites du projet ; la recherche Vulture à partir de 90 % de confiance ne signale pas de code mort.

Ces contrôles ne prouvent pas l'absence de tout défaut. Les tests historiques restent présents parce qu'ils protègent des comportements utiles ; leurs noms de versions ne signifient pas que leur code est obsolète. Les préférences existantes sont conservées : la version applicative devient 2.0.0, tandis que le schéma de configuration reste à 27.

## Blocage Windows corrigé

Le test SQLite créait `zone#with?special.sqlite`. Le caractère `?` est interdit dans un nom de fichier Windows, ce qui provoquait l'erreur dans `tempfile.mkstemp` avant toute vérification SQLite. Le runtime et le Python installé sur votre ordinateur n'étaient pas en cause dans cette trace.

Le test vérifie désormais `#`, `%`, les espaces et les accents sur toutes les plates-formes. Le cas `?` est conservé uniquement lorsque le système de fichiers le permet. La connexion de test est aussi fermée explicitement. La protection en lecture seule et l'encodage des chemins restent testés.

## Changements de comportement vérifiés

- Localisation : mise en cache d'une seule liste immuable de correspondances textuelles. Les coordonnées et la confiance ne sont pas figées dans ce cache. **4 704 scénarios** parcourant les noms de lieux, alias, libellés structurés et quatre contextes de coordonnées donnent des résultats identiques à la version initiale.
- Confirmation : historique dimensionné pour le réglage demandé, jusqu'à dix observations ; intervalle vérifié entre observations consécutives ; rejet des doublons et horodatages invalides.
- Audio : abandon des demandes périmées avant travail coûteux, nettoyage tolérant aux fichiers verrouillés, et correction de l'écart entre fenêtres FFT lorsque le pas dépasse la taille de la fenêtre. Les tests comparent plusieurs tailles de blocs à une référence calculée par indices absolus.
- OCR : gestion des files fermées ou remplacées et nettoyage des créations partielles ; un résultat répété d'une même variante ne compte plus comme plusieurs votes indépendants.
- Interface : aucune lecture du spectre ni redessin de ses barres lorsque le visualiseur est désactivé ; contrôle de réactivation toutes les 250 ms au lieu de 41 ms. Les recherches musicales identiques conservent leurs lignes et le défilement.
- Configuration : valeurs booléennes normalisées et versions de configuration non finies ignorées sans perdre les préférences récupérables.

## Nettoyage et compilation

Les anciennes routines de calibrage sur image sont regroupées dans `Data/tools/hud_reference.py`. La capture réelle utilise déjà la courbe liée à la résolution et fournit sa transformation ; le runtime n'importe aucun de ces outils. Les fonctions déplacées ont été comparées structurellement, et les captures de référence restent couvertes par les tests.

Les anciens réexports audio, champs de diagnostic de combat inutilisés, imports morts, variables sans lecteurs, constantes inutilisées et compteurs sans effet ont été retirés. Les normalisations d'espaces ont été contrôlées par comparaison d'AST. Les références des tests ont été mises à jour vers les modules qui possèdent réellement les fonctions.

Le vérificateur de release contrôle maintenant tous les formats audio distribués. La SBOM est produite dans l'environnement ayant construit le portable, puis transférée au job de publication. Ce dernier ne réinstalle plus tout le moteur OCR. La CI peut contrôler un dépôt contenant seulement les sources ; le build local et la publication exigent toujours des pistes, dont un OPUS non vide.

La configuration PyInstaller conserve le mode onedir, l'optimisation Python 2, OpenCV headless et les exclusions des moteurs inutilisés. Les dépendances du moteur OCR sont conservées lorsqu'elles peuvent être importées par celui-ci. Aucun gain de taille du ZIP Windows n'est annoncé sans mesure de ce ZIP.

## Mesures avant / après

Même interpréteur Python 3.12.14, mêmes dépendances, Linux x86_64. Médiane de cinq séries, 1 000 appels par série pour la localisation et le HUD ; dix calculs par série pour le spectre. Les deux arbres sont exécutés dans des processus séparés avec le même script. Données brutes : [avant](benchmarks/before.json), [après](benchmarks/after.json).

| Scénario | 1.0.27 | 2.0.0 | Variation du temps |
|---|---:|---:|---:|
| Même texte OCR et mêmes coordonnées | 0.036428 ms | 0.007278 ms | -80.0 % |
| Six textes OCR alternés | 0.035358 ms | 0.032111 ms | -9.2 % |
| Extraction HUD, transformation explicite | 0.413860 ms | 0.428394 ms | +3.5 % |
| Spectre de trois secondes audio | 1.571941 ms | 1.538977 ms | -2.1 % |

Le gain principal mesuré concerne le texte OCR répété : **5.0 fois plus rapide** dans ce scénario. Les autres scénarios sont proches et sensibles aux variations de charge ; le relevé HUD est légèrement plus lent, aucun gain de vitesse HUD n'est revendiqué. Ces chiffres n'incluent ni les inférences ONNX, ni la capture Windows, ni les performances de Dofus en jeu.

| Périmètre | 1.0.27 | 2.0.0 | Variation |
|---|---:|---:|---:|
| Sources du runtime | 445 488 octets | 425 256 octets | -4.54 % |
| Bytecode Python sérialisé  optimisation 2 | 574 043 octets | 555 133 octets | -3.29 % |

Périmètre des tailles : `Data/dofusic/**/*.py` et `Data/main.py`, soit 53 fichiers. Le bytecode est obtenu avec `compile(..., optimize=2)` puis `marshal.dumps`, avec des chemins relatifs identiques. Il exclut les DLL, modèles, musiques et bibliothèques tierces : **ce n'est pas la taille d'un exécutable Windows**. L'archive source complète contient aussi les nouveaux tests et documents ; sa taille peut donc augmenter malgré l'allègement du runtime.

Pour reproduire les mesures, depuis la racine :

```powershell
python Data/tools/benchmark_hotpaths.py --source-root CHEMIN_SOURCE_AVANT --iterations 1000 --output Audit/before.json
python Data/tools/benchmark_hotpaths.py --source-root . --iterations 1000 --output Audit/after.json
```

## Vérifications réalisées

- Version initiale : 670 tests réussis, un test ignoré.
- Sources finales : **743 tests réussis, un test ignoré**, en 21,14 secondes avec mesure de couverture. Le test ignoré nécessite un écran Tk, absent ici. Aucun test en échec.
- Couverture combinée instructions/branches : **66.23 %**. Instructions : 68.08 % ; branches : 59.57 %. Le seuil CI de 55 % est franchi, mais ce résultat ne constitue pas une couverture totale.
- Ruff, syntaxe des sources également validée avec la grammaire Python 3.11, absence de collisions de chemins Windows, définitions masquées, version/tag `v2.0.0`, intégrité SQLite, JSON/YAML, ressources image et syntaxe du fichier PyInstaller contrôlés.
- Benchmark fonctionnel existant validé sans modèles OCR : clé de zone attendue, cache de coordonnées et budgets synthétiques respectés.
- Revue indépendante ciblée : aucune régression concrète relevée dans les changements audio, localisation, échanges OCR et déplacement HUD ; alignement des constructions de l'observation de combat vérifié par AST.

Les résultats structurés figurent dans [verification.json](verification.json).

## Vérification restante sur Windows

Le build ciblé reste CPython 3.11.9 / Windows x64 ; les tests de cette livraison ont été exécutés sous Linux / Python 3.12.14. Aucun `.exe` n'a été produit, aucune interface Win32 réelle n'a été pilotée et aucun modèle OCR réel n'a été exécuté ici. Les tests des échanges OCR utilisent notamment des doubles de test ; les captures de référence valident la géométrie et la classification sans remplacer un essai en jeu.

1. Extraire ces sources dans un dossier propre, puis y remettre votre dossier `Musiques/`, dont au moins un OPUS non vide.
2. Lancer `BUILD_PORTABLE.bat`. Il exécute les tests, prépare les modèles, construit le portable et lance son auto-test. Le correctif SQLite n'exige pas de supprimer tout le cache `.portable-build`.
3. Exécuter `VERIFIER_PORTABLE_WINDOWS.ps1`, puis contrôler dans Dofus les changements de zone, combats, modes audio, redimensionnements et fermeture.

L'archive livrée ne contient ni les pistes musicales absentes de l'archive initiale, ni un environnement Python de build, ni modèles téléchargés, caches de tests ou données personnelles.
