# Feuille de route produit

Les idées pour le produit, à cocher quand c'est fait. Chaque idée dit **ce qu'on
veut**, **pourquoi**, et une **piste** de réalisation (à valider avant de coder).
Une idée terminée est cochée avec la date et le commit ; on ne la supprime pas.

Légende : `[ ]` à faire · `[~]` en cours · `[x]` fait.

## Cap : un service qu'on peut présenter

Ce qui manque pour présenter Nyra comme un service n'est pas la détection
(solide, voir `MATCHING.md`) mais **la boucle qui rend le produit utile sans
qu'on y pense** : aujourd'hui il faut se connecter et cliquer (audit du
29 septembre, `AUDIT_PRODUIT.md`). Ordre proposé, du plus décisif au moins :

1. **La boucle automatique** (idée 3) : le produit vient à l'utilisateur.
2. **La valeur juridique** (idée 4) : de quoi demander un retrait.
3. **Résoudre, pas seulement détecter** (idée 5).
4. **Des chiffres réels** (idée 6) : sans eux, tout le reste reste théorique.
5. Les idées 1, 2 et 7 : utiles, mais elles améliorent l'exploration et le
   tri, pas la promesse centrale. L'idée 7 (étiquettes de type sur les images du
   site) rend la liste « Droits non vérifiés » lisible : elle prolonge l'idée 2
   et passe en tête de ce groupe.

Chiffrages : ceux de l'audit, à confirmer avant de s'y engager.

## En premier

### [~] 1. Regrouper les recadrages d'une même image (Droits non vérifiés, « Tous »)

**Ce qu'on veut.** Dans la liste « Droits non vérifiés », une même photo
présente sur le site sous plusieurs recadrages (ex. `magnum_6glasses_set.webp`
en 435 × 628 et en 366 × 372, ou la photo du service du Louis XIII) ne doit
apparaître qu'**une seule fois**. La carte représente le groupe ; en cliquant
dessus, on voit **chaque occurrence** : sur quelle page elle se trouve, et sur
quelle version du site (marché/langue).

**Pourquoi.** Aujourd'hui chaque variante est une carte : la liste est
gonflée et on revérifie la même image plusieurs fois.

**État (7 octobre 2026).** Code commité (`71d45bd`, 4 octobre) et testé : les
tests passent sur un Postgres jetable où la migration `20261004000017` est
appliquée. « Ignorer » agit sur toutes les variantes d'un groupe. À faire avant
de cocher : appliquer la migration sur la base réelle, puis vérifier sur les
vraies images de Louis XIII. Risque connu : deux photos différentes qui
partagent un même bandeau ou gabarit peuvent être fusionnées (le contrôle de
points clés les juge identiques) ; à calibrer sur des images réelles, et un
groupe ne peut pas encore être scindé à la main.

**Piste.**
- Le regroupement ne compare pas une référence à une image du site mais des
  **images du site entre elles**. Les briques existent : empreintes
  perceptuelles et embeddings CLIP déjà calculés pour chaque image du site, et
  le contrôle de points clés `verify.py` (SIFT + RANSAC), qui reconnaît déjà un
  recadrage. Voir `docs/MATCHING.md`, niveaux 1 à 3.
- Candidats par CLIP (cosinus élevé), puis confirmation par points clés pour
  ne pas fusionner deux prises de vue différentes du même produit. Fusion
  transitive (composantes connexes) avec un plafond de taille de groupe.
- Choisir une image « représentante » du groupe (la plus grande).
- Stocker l'appartenance à un groupe plutôt que de la recalculer à chaque
  affichage ; recalcul incrémental à chaque crawl (comme le matching).
- Interface : badge « × N » sur la carte, volet de détail listant page +
  version du site de chaque occurrence. Le crawl garde déjà la page d'origine
  de chaque image (à confirmer dans le schéma, `docs/DATABASE_SCHEMA.md`).
- Décision : « Ignorer » sur un groupe met de côté toutes les variantes
  (fait). Reste à confirmer pour « + Ajouter » et l'annulation.
- Vérification : un jeu d'images recadrées à la main (comme la calibration de
  `MATCHING.md`) pour mesurer faux regroupements et regroupements manqués.

### [ ] 2. Étiquettes automatiques de contenu (verres en cristal, carafe, pique…)

**Ce qu'on veut.** Par marque (ex. Louis XIII), définir des étiquettes
d'objets et que l'algorithme dise **lesquels sont visibles** sur chaque image :
verres en cristal, carafe, pique, etc. Permet de filtrer et de rechercher la
bibliothèque et les images du site par contenu.

**Pourquoi.** Retrouver « toutes les images avec la carafe » sans ouvrir les
fichiers un à un, et préparer des tris par produit.

**Piste (à définir, par ordre de coût croissant).**
1. **CLIP en zero-shot.** On réutilise les embeddings déjà calculés : pour
   chaque étiquette, un ou plusieurs textes (« a crystal glass », « a cognac
   decanter »), on compare au texte. Aucun nouveau modèle, aucun coût de
   calcul de plus. Limite : mal à l'aise avec les objets propres à la marque
   et avec plusieurs objets sur la même image.
2. **Exemples par étiquette (few-shot).** La marque choisit 5 à 20 images
   d'exemple par étiquette depuis sa bibliothèque ; on moyenne leurs embeddings
   (un « prototype ») et on classe par similarité. Plus fiable pour « la
   carafe Louis XIII » que pour « une carafe ». Les corrections de
   l'utilisateur (étiquette fausse/manquante) enrichissent les exemples.
3. **Détecteur d'objets à vocabulaire ouvert** (OWL-ViT, Grounding DINO) : donne
   aussi l'emplacement. Plus lourd (GPU, nouveau modèle) ; à garder si on a
   besoin de savoir *où* est l'objet.

Recommandation de départ : 1 + 2, calibrés comme les seuils de matching
(précision/rappel sur des images étiquetées à la main). Étiquettes multiples
par image, seuil par étiquette, résultat stocké avec le score pour pouvoir
re-seuiller sans recalculer.

Questions ouvertes : qui définit les étiquettes (admin de la marque, nous) ?
Les étiquettes sont-elles propres à une marque ou partagées ? Que veut dire
« pique » pour Louis XIII (l'objet tenu par le sommelier sur la photo) ?

### [ ] 3. La boucle automatique (passage hebdomadaire, résumé, alertes)

**Ce qu'on veut.** Un passage de lecture chaque semaine par marque, un e-mail de
résumé (« 3 nouveaux visuels expirés détectés ») et une alerte d'expiration de
la bibliothèque (« 5 visuels expirent dans 30 jours »), indépendante du crawl.

**Pourquoi.** Un outil de surveillance qu'il faut penser à lancer perd
l'essentiel de sa valeur. Aujourd'hui il n'y a ni passage planifié ni alerte.

**Piste.** Réglage par organisation et déclencheur pg_cron qui insère un job
`crawl` avec `fresh: true` (sinon le mode reprise saute les pages déjà vues) ;
e-mails par Resend, destinataires par organisation. Environ 4 à 6 jours.

### [ ] 4. Preuve datée

**Ce qu'on veut.** Au moment de la détection, une capture de la page et un
horodatage, joints au rapport.

**Pourquoi.** Donne une valeur juridique au rapport, nécessaire pour demander
un retrait. Environ 2 à 4 jours.

### [ ] 5. Suivi de retrait

**Ce qu'on veut.** Assigner, commenter, fixer une échéance, relancer.

**Pourquoi.** Fait passer de « détecter » à « résoudre ». Environ 3 à 5 jours.

### [ ] 6. Chiffres réels

**Ce qu'on veut.** Importer une vraie bibliothèque, lancer une vraie lecture,
prendre 20 à 30 décisions, et mesurer le rappel sur 30 à 50 paires connues à la
main (`nyra calibrate`).

**Pourquoi.** Les 97,5 % de `MATCHING.md` viennent de copies retouchées
fabriquées, pas de vrais cas de réutilisation ; le rappel réel est inconnu.

### [ ] 7. Étiqueter les images du site (logo, packshot…) pour trier « Droits non vérifiés »

**Ce qu'on veut.** Le même principe que l'idée 2, appliqué aux **images du
site** : chaque image porte des étiquettes de type (« logo », « packshot
produit »…), posées par l'algorithme, et la liste « Droits non vérifiés » se
filtre dessus. On trie ainsi les images sans enjeu de droits sans les écarter
une par une. Pour que l'algorithme les reconnaisse, il faut **définir l'aspect
de chaque type** : qu'est-ce qu'un logo, qu'est-ce qu'un packshot ?

**Pourquoi.** La liste mélange de vraies photos à vérifier et des images
récurrentes sans enjeu, aujourd'hui écartées une à une (exclusions, voir
`MATCHING.md`). Le bruit cache l'essentiel.

**Piste.**
- Les étiquettes existent déjà sur les références de la bibliothèque (libres,
  posées à la main, jamais lues par le matching). Les images du site n'en ont
  pas : c'est le manque à combler, avec la même notion.
- Même mécanisme que l'idée 2 : prototypes CLIP à partir d'exemples choisis
  par type, score stocké pour re-seuiller sans recalculer. Les signaux simples
  (petite taille, fond uni ou transparent) peuvent aider à les définir.
- Les types sont des étiquettes comme les autres : on ne masque rien d'office,
  on filtre. Une vraie photo mal étiquetée « parasite » reste un droit non
  vérifié : l'écarter est une action de l'utilisateur, annulable.
- Vérification : des images réelles étiquetées à la main par type, précision
  et rappel mesurés comme pour le matching.

**Questions ouvertes.** Quels types exactement ? Un packshot est-il toujours
sans enjeu, ou parfois soumis à des droits (le contrat décide, voir « produit
découpé » dans `MATCHING.md`) ?

## Avant d'ouvrir à un client

Limitation de débit sur l'API, tests cloud en CI sur une vraie base, vraie
adresse de contact du robot dans `config.yaml`, politique de conservation des
images (`AUDIT_PRODUIT.md`, §3.2).

## Propositions à discuter

- [ ] **Aperçu des occurrences dans la liste** : survol d'une carte groupée =
  miniatures des autres recadrages (dépend de l'idée 1).
- [ ] **Étiquettes dans les rapports** : regrouper ou filtrer le rapport par
  étiquette (dépend de l'idée 2).
- [ ] **Recherche par image dans la bibliothèque** : déposer une photo, voir
  les références proches (embeddings déjà disponibles).
- [ ] **Détection de doublons dans la bibliothèque** : même regroupement que
  l'idée 1, appliqué aux références.
- [ ] **Mesures de qualité affichées** : précision/rappel du regroupement et
  des étiquettes, visibles dans la page Statistiques.

## Faits

_(rien pour l'instant)_
