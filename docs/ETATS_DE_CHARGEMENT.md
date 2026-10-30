# États de chargement des données

Toute requête dont le résultat s'affiche a **trois états**, écrits une fois dans `frontend/src/components/busy.tsx` (et `lib/busy.ts` pour le
minuteur) :

| État | Quand | Ce qu'on montre |
|---|---|---|
| **Premier chargement** | rien à afficher encore | un squelette **à la forme de ce qui arrive** (même nombre de colonnes, mêmes proportions : rien ne saute à l'arrivée) : `SkeletonGrid`, ou un `Skeleton` de la taille de la zone |
| **Rafraîchissement avec données** | les données sont là, une nouvelle réponse arrive (recherche, filtre, onglet, retour sur la page, changement de marque) | le contenu **reste**, un peu atténué, avec une fine ligne de progression en haut et `aria-busy="true"` : `Refreshing`. Seulement après **200 ms** (une réponse immédiate ne change rien à l'écran) et pour **300 ms au moins** une fois montré (pas de clignotement). Une annonce polie (`role="status"`) le dit aux lecteurs d'écran, sans déplacer le focus |
| **Erreur** | la requête a échoué | rien à montrer : `LoadError` (le motif et « Réessayer »). Du contenu est déjà là : `RefreshFailed` au-dessus (« ce qui est affiché peut être périmé »), annoncé tout de suite (`role="alert"`) |

Deux règles qui vont avec :

- **Des anciennes données ne se font jamais passer pour la réponse à la nouvelle demande.** Pendant qu'une recherche ou un site charge, les cartes
  précédentes restent (`placeholderData`) mais le compteur dit « Mise à jour… » au lieu d'afficher l'ancien total ; ce qui ne dépend pas de la demande
  (les compteurs des onglets) garde sa dernière valeur connue (`useLastKnown`) au lieu de disparaître.
- **Une action longue a un retour immédiat** (bouton `loading`, ligne de progression locale), pas seulement le toast de fin.

## Page par page

« Schéma » : les trois états ci-dessus (`components/busy.tsx`). Relevé dans le code le 30 octobre 2026.

| Page | Premier chargement | Rafraîchissement avec données | Erreur |
|---|---|---|---|
| **Images en ligne** (`SiteImages.tsx`) | schéma : grille de squelettes (4 colonnes, 2 sur téléphone) | schéma : recherche, site, « Voir plus » (bouton `loading`) et rechargement ; le compteur dit « Mise à jour… » ; les compteurs d'onglets restent. « Ignorer » de la barre de sélection passe en « Ignorer… » tout de suite | schéma |
| **Journal d'audit** (`AuditLog.tsx`) | squelette | schéma | schéma |
| **Tableau de bord** | squelette de la page | schéma | schéma |
| **À traiter** (`Review.tsx`) | 6 lignes de squelette | schéma | schéma |
| **Visuels à remplacer** | squelette | schéma | schéma |
| **Où est-il utilisé ?** (`visual/Visual.tsx`) | squelette | schéma sur la liste des pages (un filtre garde l'ancienne liste, atténuée) | schéma (avant : un squelette qui ne finissait jamais, avec le message dessous) |
| **Rapports** | squelette | schéma sur la liste | schéma |
| **Statistiques** | squelette de la page | schéma | schéma |
| **Bibliothèque** | 6 lignes de squelette | **pas de schéma** : la liste est rechargée après chaque modification (étiquette, date, import) ; l'atténuer à chaque fois gênerait plus qu'elle n'informe | schéma (« Réessayer » ajouté) |
| **Où est-il utilisé ?** dans la fenêtre d'édition (`OccurrencesPanel.tsx`) | 3 lignes de squelette (avant : `Spinner`) | aucun | schéma (`RefreshFailed`) |
| **Lectures** : historique | squelette | schéma | schéma |
| **Lectures** : adresses (`SitesCard.tsx`) | squelette | aucun | message sans « Réessayer » |
| **Réglages** : Collections, Logos, Planification | squelette | aucun | schéma |
| **Réglages** : Images exclues | squelette (avant : rien, une liste vide le temps du chargement) | aucun | schéma |
| Réglages : Notifications | squelette | aucun | alerte à elle, avec « Réessayer » |
| Back office (sections) | squelette par section ; `Spinner` pour la page | aucun | « Réessayer » pour la page seulement |
| Accès (connexion, choix de l'organisation) | `Spinner` « Un instant » | sans objet | message |

Plusieurs requêtes gardaient déjà les anciennes données le temps d'une nouvelle réponse (`keepPreviousData` dans `lib/queries.ts`), sans rien le
montrer : c'est ce que `Refreshing` rend visible.

## Le contrôle automatique : évalué, pas écrit

Un script qui échoue quand une page lit le résultat d'un `useQuery` sans lire son état de chargement a été essayé (parcours de `pages/` et
`components/`, hooks de `lib/` qui renvoient un `useQuery`) : sur 57 résultats lus, 26 ne lisent ni `isLoading`, `isPending` ni `isFetching`. Presque tous
sont **légitimes** (une donnée annexe lue par une page qui a déjà son propre squelette : les tâches en cours dans le bandeau, la marque, les
étiquettes, l'aperçu pour un libellé) ; seuls deux étaient de vrais oublis (les images exclues et la page d'un visuel), corrigés ici. Un contrôle qui
échoue sur 24 faux positifs obligerait à tenir une liste d'exceptions plus longue que le code qu'elle protège : il n'est pas ajouté. La règle de
`BONNES_PRATIQUES.md` et la revue de code tiennent ce rôle.

## Ce qui n'est pas fait

- Les zones sans schéma dans le tableau (Bibliothèque, adresses des Lectures, Notifications, Back office, Accès).
- Un retour immédiat pour toutes les actions longues : fait pour « Ignorer » de la barre de sélection d'Images en ligne ; les autres ont déjà
  un bouton `loading` (À traiter, sélection de la bibliothèque, import) mais n'ont pas été relues une à une.
- Les captures avant/après dans un vrai navigateur (bureau et téléphone), sous réseau bridé : non faites (pas d'API ni de Supabase en local).
