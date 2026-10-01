# Bonnes pratiques de code

Ces règles s'appliquent à tout le code de Nyra (Python côté backend,
TypeScript/React côté interface), qu'il soit écrit par une personne ou par un
agent IA. Elles sont adaptées des « Power of 10 » de la NASA, écrites pour du
C embarqué : on en garde l'esprit (du code simple, borné, vérifiable) et on
traduit chaque règle pour nos langages. Les règles marquées **à valider**
sont des propositions d'adaptation, pas encore tranchées par l'équipe.

## Deux documents, deux rôles

| Document | Il décide… |
|---|---|
| [`.agents/rules/ponytail.md`](../.agents/rules/ponytail.md) | **combien** de code écrire : le moins possible (réutiliser, supprimer, ne rien ajouter d'inutile). |
| Ce document | **comment** écrire proprement le code qui doit exister : simple, borné, vérifié. |

En cas de tension, `ponytail` l'emporte sur le *volume* (pas de couche, de
fichier ou de dépendance ajoutés « par sécurité »), et ce document l'emporte
sur la *qualité* (validation aux frontières, erreurs gérées, zéro warning).
Aucune règle ci-dessous n'est une raison d'ajouter du code qui ne sert à rien.

## Les 10 règles

### 1. Flux de contrôle simple
Pas de récursion, sauf parcours de structure à profondeur bornée (avec la
borne dans le code). Pas de sauts exotiques : préférer les retours anticipés
(*early return*) à l'imbrication. Au-delà de trois niveaux d'indentation, on
extrait une fonction.

### 2. Boucles bornées
Toute boucle `while` a une borne explicite : un nombre maximal de tours, une
échéance (`deadline`) ou une file qui se vide à coup sûr. Les `for` sur une
collection finie sont admis. Une boucle de reprise (réessais, pagination,
attente d'un job) porte toujours son plafond. Exemples déjà en place :
`crawl.max_pages_limit`, plafond de taille des téléchargements.

### 3. Ressources bornées *(adaptation, à valider)*
La règle d'origine interdit l'allocation dynamique ; en Python et JavaScript,
la mémoire est gérée pour nous. On garde l'intention : **rien ne grandit sans
limite.** Toute entrée externe (fichier envoyé, CSV, liste dans une requête,
réponse d'un site) a une taille maximale vérifiée. Tout cache en mémoire a
une taille ou une durée de vie maximale. On lit en flux plutôt qu'en entier
quand le volume n'est pas borné.

### 4. Fonctions courtes
**60 lignes au plus** par fonction ou composant, signature et docstring
comprises. Au-delà, on découpe selon les étapes réelles de la tâche, pas en
sous-fonctions artificielles. S'applique au code **neuf ou modifié** ; voir
« Dette connue » pour l'existant.

### 5. Vérifications explicites *(adaptation, à valider)*
La règle d'origine demande deux assertions par fonction. Ce quota produirait
du bruit ; on le remplace par :
- **validation aux frontières** (corps de requête, CSV, paramètres d'URL,
  réponses de services externes) : toujours, avec un message d'erreur clair ;
- `assert` réservé aux **invariants internes** qui ne peuvent pas être faux
  si le code est juste (jamais pour valider une entrée, car Python les
  supprime avec `-O`) ;
- côté TypeScript, les types stricts jouent ce rôle (voir règle 10).

### 6. Plus petite portée possible
Une variable est déclarée au plus près de son premier usage, dans le bloc le
plus étroit. `const` par défaut en TypeScript, pas de variable de module
mutable en Python, pas de variable réutilisée pour deux sens différents.

### 7. Vérifier les retours, ne jamais avaler une erreur
- Un résultat qui peut signaler un échec est lu : nombre de lignes modifiées
  (`rowcount`), statut HTTP, valeur `None`.
- Pas de `except Exception: pass`. Une exception est soit traitée (avec un
  effet visible), soit journalisée **et** assumée en commentaire, soit
  relancée. Exception acceptée : un nettoyage « au mieux » (fichier orphelin,
  vignette absente) qui ne doit pas casser la page, avec le pourquoi dit en
  commentaire.
- Les paramètres venant de l'extérieur sont validés avant usage (règle 5).

### 8. Pas de magie *(adaptation, à valider)*
La règle d'origine limite le préprocesseur C. Notre équivalent : pas de
métaprogrammation ni de comportement caché. Pas d'`eval`, pas de
*monkeypatching* hors tests, pas de décorateur ou de hook qui change le sens
d'une fonction sans que ça se voie à la lecture, pas de `as` TypeScript pour
faire taire le compilateur.

### 9. Indirection limitée et pas d'état global partagé *(adaptation, à valider)*
La règle d'origine limite les pointeurs à un niveau. Notre équivalent : une
donnée passe par au plus un niveau d'intermédiaire (pas de fonction qui
renvoie une fonction qui renvoie une fonction), et l'état partagé et
modifiable est l'exception, documentée, avec son verrou et sa limite (règle
3). On préfère passer les données en paramètre.

### 10. Zéro avertissement
Ces quatre commandes passent sans aucun avertissement avant chaque fusion :

```bash
ruff check backend                 # lint Python
cd frontend && npx tsc --noEmit    # types stricts (strict, noUnusedLocals…)
cd frontend && npm run build
pytest -q                          # sans warning non justifié
```

Un avertissement qu'on ne peut pas corriger (bibliothèque tierce) est
filtré **nommément** dans la configuration, avec un commentaire qui dit
pourquoi. On ne désactive jamais une règle globalement pour se débarrasser
d'un cas.

## Tests

Conformément à `ponytail` : la logique non triviale laisse **un** test
exécutable, le plus petit qui échoue si la logique casse. Pas de framework
ajouté, pas de jeu de données lourd. Une fonction d'une ligne n'en a pas
besoin.

## Dette connue (mesurée le 2 octobre 2026)

Ces règles s'appliquent au code qu'on écrit ou qu'on modifie. On ne réécrit
pas l'existant d'un bloc : on corrige ce qu'on touche (règle du scout).

- **Fonctions longues** : plus aucune en Python (492 fonctions, la plus longue
  fait 58 lignes). En TypeScript, une seule dépasse 60 lignes : `Login`
  (71) dans `frontend/src/pages/Access.tsx`, à découper à la prochaine
  modification de ce fichier. L'API est répartie par domaine dans
  `backend/nyra/cloud/routes/`, les pages lourdes dans des sous-dossiers de
  `frontend/src/pages/`.
- **Erreurs larges** : les 21 `except Exception` de `backend/nyra` sont
  soit journalisés, soit justifiés par un commentaire, soit relancés.
- **Ressources bornées** : le cache d'URL signées (`storage.py`) et le cache
  de caractéristiques de référence du contrôle de points clés (`verify.py`,
  32 entrées) ont une taille maximale. Restent des limites à surveiller :
  la liste des visuels renvoyée d'un bloc (pas de pagination côté serveur) et
  la page du back office, qui affiche toutes les marques de la plateforme.
- **Avertissements de tests** : aucun. Celui du client de test (httpx) est
  filtré nommément dans `pyproject.toml`.
- **Couverture** : le contrôle de points clés (OpenCV) et les embeddings CLIP
  ne tournent pas en CI sans leurs dépendances (le test `test_match.py` qui
  demande torch est ignoré) ; les pages du front ne sont vérifiées que par des
  scénarios de navigateur écrits à la main, pas par des tests automatiques.

## Pour un agent IA qui travaille ici

1. Lire `.agents/rules/ponytail.md` et ce document avant d'écrire.
2. Lire le code concerné et tracer le flux réel avant de choisir une solution.
3. Écrire le minimum, respecter les 10 règles sur ce qu'on écrit.
4. Lancer les quatre commandes de la règle 10 ; ne rien déclarer terminé si
   l'une échoue.
5. Dire ce qu'on n'a pas vérifié.
