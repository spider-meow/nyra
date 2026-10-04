# Feuille de route produit

Les idées pour le produit, à cocher quand c'est fait. Chaque idée dit **ce qu'on
veut**, **pourquoi**, et une **piste** de réalisation (à valider avant de coder).
Une idée terminée est cochée avec la date et le commit ; on ne la supprime pas.

Légende : `[ ]` à faire · `[~]` en cours · `[x]` fait.

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

**État (4 octobre 2026).** Code écrit et testé, pas encore commité. À faire
avant de cocher : appliquer la migration `20261004000017`, puis vérifier sur
les vraies images de Louis XIII. Risque connu : deux photos différentes qui
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
- Décision à prendre : « Ignorer » / « + Ajouter » sur un groupe agit-il sur
  toutes les variantes ? (proposition : oui, avec annulation possible).
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
