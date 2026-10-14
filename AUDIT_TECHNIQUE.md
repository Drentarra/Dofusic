# Dofusic — audit technique de la base v1.0.11

## Référence et règles de modification

- Référence immuable : commit `1617ce283ec9b11c67c4f9e5f71c5f37888eddce`, tag `v1.0.11`.
- Développement : `audit/consolidation-v1-0-11`, PR n°20, sans fusion automatique et sans publication.
- Données géographiques : aucun ID, enregistrement ou correspondance de `Data/dofus_data.sqlite`, `Data/place_aliases.json` et `Data/dungeons.json` n'a été modifié.
- Bibliothèque fournie : 162 fichiers audio, dont Incarnam, non modifiés.
- Architecture portable : Windows x64 / Python 3.11.9 / PyInstaller, via les workflows existants.
- Un fichier noté « examiné » signifie qu'on a étudié son rôle, ses dépendances et ses chemins sensibles. Cela **ne signifie pas** qu'on a prouvé chacune de ses fonctions ni qu'il fallait la réécrire.

## Chaîne fonctionnelle vérifiée dans les sources

```text
ProcessPresence -> CaptureService -> DofusCapture -> CapturedFrame (calibration incluse)
   -> extraction HUD -> filtre de changement -> ordonnanceur OCR « single flight »
   -> workers OCR zone/position isolés -> LocationResolver (SQLite)
   -> DecisionEngine (confirmation) -> MusicLibrary (priorité donjon)
   -> MusicPlayer / FFmpeg / pygame
                                      \-> overlays Tkinter
MusicSession -> recherche, lecture manuelle, téléchargement, miniatures (exécuteurs dédiés)
```

Les positions doivent déverrouiller l'automatisme au début d'une session de Dofus. Une zone inconnue ne remplace pas automatiquement le dernier lieu confirmé. En donjon, la musique de donjon est prioritaire sur la détection de combat ; hors donjon, les musiques de combat restent disponibles. Les contrôles de fichiers en ligne ne doivent pas intervenir dans la décision géographique.

## Inventaire des modules Python de production

| Fichier (sous `Data/dofusic/`) | Rôle et décision d'audit |
|---|---|
| `__init__.py` | Marqueur de paquet, pas de traitement |
| `app.py` | Orchestration, processus Dofus, rendu HUD, OCR et audio ; réutilisation de la calibration et limitation des logs d'échec |
| `config.py` | Validation, configuration portable et migrations ; maintien des préférences existantes |
| `logging_setup.py` | Rotation et niveau des logs ; conservation |
| `models.py` | Contrats immuables des lieux et OCR ; conservation des champs et identifiants |
| `text.py` | Normalisation et mise en cache des calculs linguistiques ; mécanismes conservés |
| `version.py` | Identité logicielle : aucune hausse de version avant publication |
| `audio/__init__.py` | Marqueur de paquet |
| `audio/dungeons.py` | Catalogue déterministe, expéditions et alias de boss ; priorité préservée |
| `audio/library.py` | Scan et résolution des morceaux ; cache borné des recherches déterministes et invalidation après scan |
| `audio/player.py` | pygame, FFmpeg, analyse du spectre, normalisation, volume et transitions ; arrêt du décodeur, cache de gains borné |
| `capture/__init__.py` | Exports |
| `capture/dofus_window.py` | Recherche HWND, ROI MSS, PrintWindow, DPI et handles GDI ; chemins de repli conservés |
| `capture/process_presence.py` | Énumération Toolhelp32 sans psutil ; conservation |
| `capture/service.py` | Thread dédié, dernière capture, cadence et erreurs ; reprise de l'initialisation et prévention du double thread |
| `location/__init__.py` | Marqueur de paquet |
| `location/coordinates.py` | Lecture, hypothèses et compatibilité des coordonnées ; conservation |
| `location/decision.py` | Confirmation temporelle, score, ambiguïté ; conservation |
| `location/repository.py` | Schéma, index, requêtes coordonnées et outils d'import ; base et SQL géographique inchangés |
| `location/resolver.py` | Index lexical, variantes parent/enfant, contexte spatial ; mémoïsation d'une position constante |
| `location/suggestions.py` | Propositions de lieux ; conservation |
| `online/__init__.py` | Exports |
| `online/discovery.py` | Recherche YouTube et suggestions ; validation stricte des identifiants reçus |
| `online/errors.py` | Erreurs typées ; conservation |
| `online/keyed_locks.py` | Nouvelle implémentation commune des verrous temporaires par clé |
| `online/media_cache.py` | Téléchargement/convertisseur, cache et bibliothèque ; contrôle des IDs et centralisation des verrous |
| `online/models.py` | Références vidéo et playlist ; validation des IDs YouTube |
| `online/playback.py` | Préparation et succession des lectures, file manuelle ; conservation des transitions |
| `online/retrieval.py` | yt-dlp, QuickJS, reprise et classification d'erreurs ; validation des IDs |
| `online/search.py` | Exécuteurs et générations anti-résultats obsolètes ; conservation |
| `online/session.py` | Coordination musique manuelle, recherches et miniatures ; conservation |
| `online/thumbnails.py` | Miniatures PIL en tâche de fond, cache limité ; contrôle des IDs et centralisation des verrous |
| `ui/__init__.py` | Marqueur de paquet |
| `ui/brand.py` | Logo, aucune logique coûteuse à chaud |
| `ui/main_window.py` | Boucle Tk, état, visualiseur et préférences ; suppression des redraws inutiles et journalisation des exceptions |
| `ui/music_window.py` | Recherche, téléchargements et gestion de playlist ; contrôles Tk conservés |
| `ui/overlay.py` | Calque transparent Win32, capture exclue et contours ; placement et masquage conservés |
| `ui/settings_window.py` | Préférences, valeurs numériques, visibilité ; conservation |
| `ui/themes.py` | Palettes et recoloration ; conservation |
| `ui/volume.py` | Gestes volume et mute ; conservation |
| `ui/window_chrome.py` | Attributs DWM et mise à jour différée ; conservation |
| `vision/__init__.py` | Marqueur de paquet |
| `vision/change_gate.py` | Signature compacte des changements OCR ; conservation |
| `vision/combat.py` | Détection visuelle, hystérésis des états ; conservation |
| `vision/combat_shapes.py` | Silhouettes de référence des boutons combat ; aucune modification |
| `vision/engines.py` | ONNX RapidOCR et variantes coordonnées ; normalisation et repli conservés |
| `vision/layout.py` | Échelles de HUD, ROIs, crop du texte ; réutilisation de sa transformation par le contrôleur |
| `vision/position.py` | Sélection de la meilleure coordonnée ; conservation |
| `vision/position_overlay.py` | Largeur stable des contours de position ; conservation |
| `vision/scheduler.py` | Single-flight et reprise des demandes ; conservation |
| `vision/workers.py` | Processus OCR séparés, files IPC et redémarrage ; fermeture non bloquante et prévention d'orphelins |

## Autres fichiers et validation

| Ensemble | Décision |
|---|---|
| `Data/main.py` | Entrée de l'application et autotest, conservés |
| `Data/Dofusic.spec` | Onedir, assets, dépendances, architecture conservés |
| `Data/tools/build_portable.py` / `prepare_models.py` / `runtime_probe.py` | Compilation et contrôles existants maintenus |
| `Data/tools/benchmark_runtime.py` | Nouveau banc, exécutable sur base v1.0.11 et branche audit |
| `.github/workflows/ci.yml` | Tests, sondes, comparaison avant/après puis build Windows portable ; enrichi sans second constructeur |
| `.github/workflows/build-release.yml` | Publication seulement par tag ou commande explicite avec tag ; non déclenchée |
| `.github/scripts/verify_release.py` | Validation de la structure ZIP, chemins Windows et SBOM ; conservée |
| `.github/dependabot.yml`, listes de dépendances et `BUILD_PORTABLE.bat` | Politique de versions, lancement de construction et sécurité : conservés |
| `Data/dofus_data.sqlite`, `dungeons.json`, `place_aliases.json` | Données inchangées ; intégrité du référentiel contrôlée par les tests et les probes |
| `Musiques/`, `Data/dofusic/ui/assets/` | Médias inchangés ; aucun remplacement arbitraire de ressource |
| `tests/` | Régressions existantes et nouveaux tests ciblés ; aucun test historique supprimé |

Les fichiers texte de documentation, notices et licences restent en place. Les fichiers binaires ne sont pas assimilés à des modules Python à optimiser.

## Problèmes démontrés et décisions

| Impact estimé | Constats étayés par le code | Traitement |
|---|---|---|
| Fort, récupération | Un échec lors de la création du backend pouvait clore définitivement le thread de capture | Réessai temporisé ; test avec une panne puis une récupération |
| Fort, synchronisation | Fermeture de files IPC susceptibles d'attendre une vidange impossible après arrêt du worker | Annulation des jointures de feeders et destruction conditionnée à l'arrêt réel |
| Fort, performances | Résolution à position constante : deux lectures SQL géographiques répétées | Cache d'un contexte par coordonnées, invalidé lors du changement de position |
| Moyen, CPU | `estimate_hud_transform` exécuté pendant la capture puis de nouveau au tick du contrôleur | Transformation déjà présente sur la frame réutilisée |
| Moyen, CPU | Comparaisons musicales déterministes recalculées après revisites | Cache borné, invalidé après scan, sans mise en cache de la rotation générique |
| Moyen, interface | Tk actualisait couleur et taille de barres de visualiseur inchangées | Comparaison des propriétés rendues avant les appels Tk |
| Moyen, ressources | Processus de décodage FFmpeg pouvait survivre à l'arrêt du lecteur | Suivi du processus et interruption explicite |
| Moyen, mémoire | Cache des gains audio croissant à chaque morceau écouté | Limitation à 128 morceaux |
| Moyen, sécurité | Identifiant vidéo externe utilisé dans noms de fichiers et chemins glob | Validation du format YouTube à l'ingestion et aux frontières d'accès |
| Faible, maintenance | Deux copies de verrous temporaires par clé | Composant unique avec compteur d'utilisateurs et test de concurrence |
| Faible, observabilité | Exceptions Tk et répétition des erreurs de capture | Trace des exceptions et limitation des avertissements récurrents |

Les mécanismes déjà adaptés (deux workers, gate OCR, caches de spectre, priorités donjon, overlay exclu des captures) n'ont pas été supprimés à titre esthétique.

## Banc d'essai et interprétation

La CI Windows exécute `Data/tools/benchmark_runtime.py` sur le tag v1.0.11 et sur le candidat, avec la même base et le même fichier image. Un rapport lisible et les deux JSON sont livrés dans l'artefact de CI.

Premier rapport vérifié : [GitHub Actions 38636038896](https://github.com/Drentarra/Dofusic/actions/runs/38636038896).

| Échantillon synthétique | Référence | Candidat mesuré |
|---|---:|---:|
| Résolution lieu, moyenne | 0,6985 ms | 0,0858 ms |
| Résolution lieu, p95 | 0,7694 ms | 0,0999 ms |
| Requêtes SQL pour 80 répétitions, après amorçage | 160 | 0 |
| Extraction du HUD, moyenne | 0,1585 ms | 0,1611 ms |
| OCR zone, moyenne | 25,3156 ms | 25,2201 ms |
| OCR position, moyenne | 306,3919 ms | 308,4439 ms |

La première mesure RSS était absente à cause de la signature Win32 ; cette instrumentation a été corrigée après ce rapport. **Ne pas déduire un gain CPU, RAM ou OCR global de ces seules valeurs.**

Les mesures représentent l'exécution sur un HUD figé ; elles ne capturent ni le coût MSS/PrintWindow d'une fenêtre Dofus vivante, ni les transitions entre fenêtres, ni une session musicale longue. Les temps OCR observés sur cette fixture ne prouvent pas que tous les OCR en jeu prennent 300 ms.

## Risques résiduels et protocole de validation utilisateur

1. **Windows avec Dofus réel** : ouverture et fermeture, changement d'écran, perte de fenêtre, alt-tab, DPI multiples, reconnexion.
2. **Règles audio** : combat normal, donjon avant/durant/après combat, passage Incarnam, surcharge en ligne, reprise musique Dofus, normalisation et transitions à l'oreille.
3. **OCR** : fautes de lecture, téléportation, disparition temporaire du HUD, latence sous forte charge CPU.
4. **Audio externe** : recherches et lecture réelles (réseau, restrictions YouTube, mises à jour des plateformes).
5. **Packaging** : lancer effectivement `Dofusic.exe` sur un Windows vierge sans Python, inspecter les fichiers et permissions portables.
6. **Performance en jeu** : consigner CPU/RSS, débit de captures, OCR de chaque canal, temps de démarrage et stabilité des threads avec un même scénario et la même machine.

Les tests CI prouvent que les tests programmés et les contrôles de packaging passent, **pas** qu'aucune régression visuelle, sonore ou liée à Dofus n'existe. Aucune release et aucune fusion avant cette recette.


## Second passage : duplications, collisions et maintien dans le temps

Un second passage a été effectué sur les modules et fichiers de production, les scripts et les workflows, en distinguant les changements utiles des réécritures sans justification :

- **Cycle de vie Tkinter** (`ui/main_window.py`) : la boucle de mise à jour pouvait reprogrammer `after()` après fermeture, contrairement à la boucle du visualiseur. Les deux suivent désormais la même politique de fermeture idempotente, y compris si la fenêtre est détruite au milieu d'une mise à jour.
- **Crash OCR** (`vision/workers.py`) : `start()` remplaçait directement les références aux anciennes files après une fin inattendue. L'ancien processus et ses canaux sont désormais nettoyés ; une erreur de démarrage ferme également les files allouées.
- **Écriture concurrente** (`online/media_cache.py`) : la séquence `exists()` puis `copy2()` autorisait deux sauvegardes sous le même nom. La création exclusive `xb` protège les morceaux existants et les nouvelles écritures concurrentes ; une copie interrompue ne laisse pas de piste partielle.
- **Miniatures en ligne** (`online/session.py`) : les dictionnaires de résultats en mémoire et les demandes soumises n'avaient pas de plafond explicite. Ils sont bornés, avec suppression des plus anciens résultats non consommés. Le cache disque possédait déjà sa propre limite.
- **Version exécutable** (`version.py`) : la référence GitHub `v1.0.11` exposait encore l'identité historique `1.0.2-combat-shapes` dans les sources. L'identité intégrée est corrigée en `1.0.12` pour la prochaine version, sans changer les ressources ni la géographie.
- **Packaging/publication** (`.github/workflows/build-release.yml`) : le mode manuel ne pouvait auparavant publier qu'un tag déjà existant. Un paramètre explicite permet maintenant, **sur main uniquement**, de compiler un commit fixe, vérifier le ZIP et le SBOM, créer un nouveau tag *après les validations*, puis publier avec des notes françaises ; le comportement historique des tags existants est conservé.
- **Absence de modification nécessaire** : modules purement structuraux, normalisation du texte déjà bornée par `lru_cache`, géométrie des cadres, décision de combat/donjon, sérialisation de configuration, base SQLite et ressources : pas de réécriture ou suppression gratuite.

Les tests ajoutés reproduisent en particulier les collisions de fichiers, les démarrages OCR défectueux, la fermeture Tk pendant un tick, le plafond des miniatures, l'identité de version et la procédure de publication GitHub Actions. Le guide `PUBLICATION_V1.0.12.md` décrit le déclenchement manuel.

### Limite de preuve

Le second passage correspond à une revue statique et à des essais automatisés des zones sensibles, **pas à une mesure exhaustive de chaque fonction individuelle**. Les appels Win32 dépendant du client Dofus, la qualité du rendu et le son réel restent une recette en jeu. Les optimisations non accompagnées de mesures propres au composant ne sont pas présentées comme des gains chiffrés.

## Troisième revue transversale : code, ressources et affichage 1.0.12

Le troisième passage maintient le principe de **conservation fonctionnelle**, sans transformation cosmétique des chemins Win32, des algorithmes OCR ou des règles audio déjà couvertes. Son périmètre est vérifiable par deux inventaires CI ajoutés à `.github/workflows/ci.yml` :

1. `Data/tools/audit_sources.py` parcourt **chaque source et test Python** et produit `Audit/source_inventory.json` et `Audit/SOURCES.md` : lignes, classes, fonctions, imports, exceptions larges, erreurs de syntaxe et définitions masquées ; la CI échoue sur syntaxe ou collisions de définitions.
2. `Data/tools/audit_repository.py` parcourt **tous les fichiers suivis par Git** (y compris les musiques, l'icône, les données et les guides) et produit `Audit/repository_files.json` et `Audit/REPOSITORY.md` : catégorie, taille et SHA-256 intégral. La CI échoue si un fichier suivi n'est pas lisible ou résout hors du dépôt. L'empreinte prouve la présence d'octets, non leur comportement audio/visuel.

### Nouvelles incohérences identifiées et corrigées

| Composant | Constat du troisième passage | Décision conservatrice |
|---|---|---|
| `audio/player.py` | Le cache FFT était borné en **nombre de morceaux** mais pouvait stocker beaucoup de mémoire pour plusieurs pistes longues | Plafond global de **16 Mio** pour les pistes en cache ; la piste en cours reste affichable même si elle dépasse cette limite |
| `online/thumbnails.py` | Deux nettoyages concurrents pouvaient parcourir et supprimer les mêmes fichiers, même utilisés par d'autres tâches | Un verrou de nettoyage protège la phase de suppression et les clés de production actives sont exclues |
| `ui/music_window.py` | `close()` n'était pas idempotente ; la fenêtre pouvait être détruite entre `_poll()` et le prochain rappel Tk | Fermeture unique et gestion explicite de `tk.TclError` pendant `after()` |
| `version.py`, `main_window.py`, `main.py` | Risque perçu d'une ancienne version visible malgré la mise à jour Git | Les deux affichages utilisent `__version__ = '1.0.12'` ; test du titre et de `--version` |
| Documentation / packaging | Libellés historiques « V25.1 ECO » différents de la version publique | Bannières et rapports harmonisés sur `1.0.12` ou une désignation générique ; nom du ZIP intermédiaire historique conservé pour ne pas casser les workflows |

### Ce que cela garantit — et ce que cela ne garantit pas

Ce troisième contrôle donne une traçabilité **fichier par fichier** avec comptages structurels, empreintes et tests de non-régression ; il permet notamment de détecter une suppression accidentelle, un fichier manquant, une syntaxe cassée ou un écrasement de méthode Python. Les corrections sont volontairement minimales lorsque la chaîne existante est correcte. Une garantie d'« optimisation parfaite 10/10 » ne résulte pas mécaniquement de ce contrôle : il faudra toujours des essais avec Dofus, des mesures CPU/RAM du programme complet, des vérifications des cadres à différentes résolutions et une écoute réelle des transitions de musiques.
