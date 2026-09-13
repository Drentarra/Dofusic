# AUDIT DOFUSIC V25.1 ECO

Date : 2026-09-11
Cible : Windows x64 portable
Base : Dofusic V25 Public Slim

## Diagnostic issu du log utilisateur

- 351 soumissions OCR zone observées sur environ 105,2 secondes.
- Fréquence moyenne mesurée : ~3,33 OCR zone/s, intervalle médian ~265 ms.
- Le canal position possède son propre worker OCR et n'est journalisé qu'en DEBUG : la charge OCR réelle était donc supérieure au seul compteur zone visible en INFO.
- La lecture Internet fonctionnait et mettait correctement les médias en cache.
- Les suggestions pouvaient échouer silencieusement : l'ancien endpoint `client=youtube` ne garantissait pas le JSON attendu par `json.loads`, et l'exception était avalée.

## Corrections V25.1 ECO

### Suggestions Internet
- Endpoint remplacé par `client=firefox&ds=yt&hl=fr&gl=fr`, qui renvoie un tableau JSON simple.
- Les erreurs de suggestions sont désormais consignées dans le log au lieu de retourner silencieusement une liste vide.

### OCR / CPU
- Capture : 20 -> 10 FPS.
- UI : 50 -> 80 ms (20 -> 12,5 Hz).
- OCR zone minimum : 0,25 -> 0,60 s.
- OCR position minimum : 0,18 -> 0,40 s.
- Ajout d'un `OCRChangeGate` : empreinte grayscale 32x8 et seuil de changement.
- Image inchangée : pas de nouvelle inférence OCR ; rafraîchissement de sécurité 2,0 s zone / 1,0 s position.
- Les phases d'initialisation, confirmation ou changement visuel restent prioritaires.

### Audio / CPU
- Visualiseur FFT désactivé par défaut.
- Normalisation loudness désactivée par défaut.
- Quand ces deux fonctions sont désactivées, aucun second décodage FFmpeg du morceau n'est lancé pour l'analyse visuelle/loudness.

### Réseau / RAM
- Miniatures YouTube normalisées vers `mqdefault.jpg` (adapté à l'affichage compact).
- Téléchargement des miniatures limité à un worker simultané.
- Recherche, suggestions, lecture et cache audio gardent chacun un pool borné à un worker.
- Le premier Play Internet télécharge nécessairement le média ; les relectures profitent du cache local.

## Vérifications

- Tests fonctionnels/UI : 158/158 passés sous Xvfb.
- `compileall` : OK.
- Audit AST : aucune fonction/classe redéfinie deux fois dans le même scope.
- Tests ajoutés pour le nouveau endpoint de suggestions, les valeurs ECO, l'OCR adaptatif et les miniatures basse charge.

## Livrable Windows

Lancer `BUILD_PORTABLE.bat`, puis partager :

`Release\Dofusic_V25_1_ECO_WINDOWS_X64.zip`

## Verrou de démarrage final — 2026-09-12

- Au lancement, l'automatisme reste désarmé tant qu'aucune coordonnée Dofus valide `X,Y` n'a été reconnue au moins une fois.
- L'OCR position reste actif pendant cette phase : c'est lui qui sert de déclencheur de démarrage.
- Avant ce premier `X,Y`, aucun OCR zone n'est soumis et l'analyse combat ne pilote rien. Seul le pool générique d'exploration `Musique`, `Musique1`, `Musique2`, etc. peut jouer aléatoirement. Les pistes de zone et `Musique Combat*` restent verrouillées.
- Les lectures manuelles de l'onglet Musique (Internet et fichiers locaux) restent disponibles immédiatement.
- Un retour depuis une lecture manuelle avant la première position reprend le morceau générique d'exploration choisi pour cette phase de démarrage.
- Après la première coordonnée valide, le verrou est levé une seule fois pour toute la session et le fonctionnement OCR/zone/combat reprend exactement sa logique normale.
- L'indicateur combat affiche `Combat : attente position` tant que ce verrou n'est pas levé.

### Vérification finale

- Suite complète : `163 passed, 1 skipped`.
- `compileall` : OK.
- Audit AST : aucune fonction/classe redéfinie deux fois dans le même scope.
- Tests dédiés ajoutés pour le verrou OCR, le déclenchement par position, l'ambiance générique pré-position, le blocage des pistes combat/zone et l'état visuel combat.
