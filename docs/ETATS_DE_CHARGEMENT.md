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

« Schéma » : les trois états ci-dessus. Les autres lignes disent **ce que la page fait aujourd'hui** (relevé dans le code le 30 octobre 2026) : elles
sont à passer au schéma, une page à la fois ; aucune n'est cassée, aucune n'est cohérente avec les autres.

| Page | Premier chargement | Rafraîchissement avec données | Erreur |
|---|---|---|---|
| **Images en ligne** (`SiteImages.tsx`) | schéma : grille de squelettes (4 colonnes, 2 sur téléphone) | schéma : recherche, site, « Voir plus » (bouton `loading`) et rechargement ; le compteur dit « Mise à jour… » ; les compteurs d'onglets restent | schéma : `LoadError`, ou `RefreshFailed` si des cartes sont là |
| **Journal d'audit** (`AuditLog.tsx`) | squelette | schéma (`Refreshing`, qui remplace l'`aria-busy` nu) | schéma |
| Tableau de bord | squelette de la page | aucun | message d'erreur, sans « Réessayer » |
| À traiter (`Review.tsx`) | 6 lignes de squelette | aucun | « Réessayer » |
| Visuels à remplacer | squelette | aucun | « Réessayer » |
| Rapports | squelette | aucun | message d'erreur, sans « Réessayer » |
| Statistiques | squelette de la page | aucun | message d'erreur, sans « Réessayer » |
| Bibliothèque | 6 lignes de squelette | aucun | message d'erreur, sans « Réessayer » |
| Où est-il utilisé ? (`OccurrencesPanel.tsx`) | `Spinner` | aucun | message |
| Lectures (`History.tsx`, `SitesCard.tsx`) | squelette | aucun | message |
| Réglages (Collections, Logos, Notifications, Planification) | squelette | aucun | message |
| Back office (sections) | squelette par section ; `Spinner` pour la page | aucun | « Réessayer » pour la page |
| Accès (connexion, choix de l'organisation) | `Spinner` « Un instant » | sans objet | message |

Plusieurs requêtes gardent déjà les anciennes données le temps d'une nouvelle réponse (`keepPreviousData` dans `lib/queries.ts`), sans rien le
montrer : c'est ce que `Refreshing` rend visible, page par page.

## Ce qui n'est pas fait

- Les pages « à passer au schéma » ci-dessus (seules Images en ligne et le journal d'audit l'utilisent).
- Un script de contrôle qui échoue quand une page lit un `useQuery` sans lire son état de chargement : non écrit, les faux positifs (un hook lu
  par un parent qui gère l'état) sont à évaluer. `npm run check:busy` ne vérifie que le minuteur (200 ms puis 300 ms au moins).
- Un retour immédiat pour toutes les actions longues (ignorer des images, appliquer une décision, sélection en masse) : seul le bouton `loading` existe
  partout où il existait déjà.
- Les captures avant/après dans un vrai navigateur (bureau et téléphone), sous réseau bridé.
