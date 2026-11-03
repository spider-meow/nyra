# Audit des transitions (AXE-714, parent AXE-713)

Inventaire de tout ce qui change d'état, apparaît ou disparaît sans transition dans l'interface, avec pour chaque
groupe la sous-issue qui le traite (2 à 7) ou la raison de ne rien faire. Aucune ligne de code produit n'est modifiée
par ce document. Rédigé le 3 novembre 2026.

## 1. Méthode et limites

- **Code** : tout `frontend/src` relu par quatre relecteurs indépendants (composants partagés et routage ;
  bibliothèque, À traiter, visuels, images en ligne ; tableau de bord, lectures, rapports, statistiques, réglages,
  connexion ; back-office et graphiques). Plus de 700 lignes d'inventaire au total, regroupées ici par cause.
- **Navigateur** (organisation de démonstration, lecture seule) : transitions réellement appliquées mesurées sur les
  9 écrans principaux (`getComputedStyle`), deux sauts de mise en page vérifiés (§5).
- **Références** : pages officielles de Carbon (IBM), Atlassian, Material 3 (valeurs du dépôt officiel),
  Nielsen Norman Group, MDN, WCAG 2.3.3, React Router.
- **Non fait** : parcours sur téléphone et en mode « réduire les animations » ; mesure de fluidité sur une grande
  bibliothèque ; pages Apple (illisibles). Les affirmations marquées *à vérifier* viennent du code, pas d'une mesure.

## 2. Ce que disent les références (utile pour fixer les jetons de 2/7)

| Source | Contrôles / survol | Apparition d'une surcouche | Sortie | Courbes | À éviter |
|---|---|---|---|---|---|
| Carbon (productive) | 70 à 150 ms | 240 ms (toast) | courbe d'accélération, durée non donnée | entrée `0,0,.38,.9` ; sortie `.2,0,1,.9` ; standard `.2,0,.38,.9` | mouvement « souvent remarqué » : le réduire ou le retirer |
| Atlassian | 50 à 150 ms (survol 50) | menu 150 ms ; fenêtre 250 ms | 50 à 100 ms de moins que l'entrée (fenêtre 200 ms) | ease-out `.4,1,.6,1` pour les interactions | ne pas retarder un retour critique (erreur, focus) ; 1 à 2 propriétés par transition ; squelette → contenu en fondu sans déplacement |
| NN/g | ≈ 100 ms (case, interrupteur) | 200 à 300 ms | un peu plus court que l'entrée | ease-out en entrée, ease-in en sortie | plus l'animation est fréquente, plus elle doit être courte ; au-delà de 500 ms pénible ; opacité sur beaucoup d'éléments à la fois coûteuse |
| Material 3 | 50 à 200 ms (short1-4) | 250 à 400 ms (medium) | — | standard `.2,0,0,1` | — |

Lecture pour Nyra (outil où l'on reste des heures) : on prend le bas de ces plages. Les surcouches à 160-200 ms, les
sorties à 120-140 ms, les contrôles à 120 ms, une seule courbe de sortie douce `cubic-bezier(.2,0,0,1)` pour apparaître et
une courbe d'accélération courte pour disparaître. Valeurs finales à confirmer à l'œil (2/7), jamais au-dessus de 250 ms.

Techniques (MDN, React Router) : `@starting-style` est Baseline depuis août 2024 (fondu d'entrée en CSS seul) ;
`transition-behavior: allow-discrete` n'est **pas** Baseline (ne pas bâtir la sortie dessus) ; `<ViewTransition>` de React
est disponible avec React 19.3 (installé : 19.3.1), les props `viewTransition` de React Router sont marquées dépréciées ;
pour le mouvement réduit, MDN remplace un déplacement par un fondu, la page React Router supprime tout : à trancher (2/7).

## 3. État actuel mesuré

- **Une seule valeur partout : 150 ms**, courbe Tailwind par défaut `cubic-bezier(.4,0,.2,1)`, venant de `transition-colors`,
  `transition-shadow` et `transition-opacity` sans `duration-*` (13 déclarations dans `components/`, 15 dans les pages ; à
  l'écran, de 12 à 83 éléments par page en portent un effet). Aucun jeton, aucune variable de mouvement dans `index.css`.
- **Quatre `@keyframes`**, toutes des entrées : `toast-in` 160 ms, `tip-in` 140 ms, `pop-in` 180 ms (0 usage dans
  `components/`, 2 dans la bibliothèque), `progress-slide` 1,4 s en boucle. Trois durées pour la même idée, mot-clé `ease-out`
  (courbe différente de celle des transitions).
- **Durées isolées** : `duration-300` (barre de progression des toasts), `duration-500` (barre de recherche d'occurrences,
  hors plafond), `duration-200` (chevron du dépliant), `duration-150` redondant dans `Refreshing`.
- **Aucune sortie animée nulle part** : fenêtres, menus, infobulles, toasts, barre de chargement, ligne de rafraîchissement.
- **Mouvement réduit** : le bloc de `index.css` ne couvre que 4 classes ; `animate-pulse`, `animate-spin` et tous les
  `transition-*` ne sont pas neutralisés ; aucun `motion-safe:` / `motion-reduce:` dans `src`.
- **Aucun `:active`** (pas de retour au toucher), aucun `active:scale-*`.
- **Changement de page** : le routeur attend le chunk puis remplace le contenu d'un coup ; chaque page affiche ensuite son
  squelette puis le contenu, de nouveau d'un coup. Pas de `ScrollRestoration`.
- **Remontage complet** du `Layout` à chaque changement de marque (`key={brand.id}`, `Access.tsx`) ; changement
  d'organisation : le `Layout` est démonté, l'écran passe par « Un instant » (mesuré : barre latérale et contenu absents
  pendant ≈ 25 ms sur données en cache, davantage sur une connexion lente).

## 4. À NE PAS adoucir (décision écrite)

1. **Saisie de texte, curseur, valeurs tapées** (tous les `Input`, recherches, `TagEditor`, `NameField`).
2. **Anneau de focus clavier** : l'outline global `:focus-visible` reste instantané (le `focus:ring` des champs peut garder
   sa transition, mais jamais de délai en `focus-visible`).
3. **Cases à cocher et curseurs natifs** (`Checkbox`, `range` de mélange des images, `<details>`).
4. **Triage au clavier de « À traiter »** : sélection des lignes (J/K), décisions (C/F/U), disparition de la ligne décidée,
   panneau de comparaison qui change à chaque touche. Au plus la `transition-colors` actuelle, jamais de transform ni de délai.
5. **Tout ce qui suit le pointeur** : enveloppe de `CursorTip` (`style.transform` écrit à chaque `mousemove`), position
   des infobulles de graphiques, repère vertical des courbes, `StickyPanel` (hauteur recalculée au défilement), `DropBox`
   de glisser-déposer (au plus 120 ms en opacité seule), poignées de tri.
6. **Surbrillance de l'option active** des listes (`Dropdown`, `Row` des menus) pendant la navigation aux flèches.
7. **Chiffres, compteurs, valeurs sondées** (`Stat`, pastilles de la barre latérale, états des lectures toutes les 2 s,
   graphiques du serveur toutes les 5 s) et **signaux d'alerte** du back-office (`HealthBanner`, `Alerts`, « Muet depuis »).
8. **Axes et graduations** des graphiques, interpolation du tracé SVG (`d`), états vides et erreurs rares.
9. **Boucles d'attente** (`animate-spin`, `animate-pulse`, barre indéterminée) : ce sont des indicateurs, pas des transitions
   d'état ; seulement les rendre inoffensives en mouvement réduit.
10. **Minuteries de non-clignotement** `NavProgress` (150/300 ms) et `busy` (200/300 ms) : ne pas les toucher.
11. **Défilements natifs** (`scrollIntoView`) et appui long tactile.

## 5. Sauts de mise en page : à stabiliser, pas à animer

Un fondu ne corrige pas un contenu qui pousse ses voisins. Ces cas sont des défauts de mise en page (réserver la place,
aligner les squelettes) ; ils sont rattachés à 6/7 quand on y touche, sinon listés ici.

- **Vérifié** : cocher une carte de la bibliothèque remplace l'en-tête (72 px) par la barre de sélection (94 px) :
  **toute la grille descend de 22 px** (le commentaire de `Selection.tsx` affirme « même hauteur »).
- **Vérifié** : changement d'organisation = barre latérale et contenu absents un court instant.
- **Bandeaux insérés dans le flux** : indexation en cours (≈ 64 px), avis d'import, « Vignettes indisponibles », bandeau
  « Expirés », `ViewNote` des images en ligne (hauteur différente à chaque onglet), `RefreshFailed`, `SuspectReadNotice`,
  `OrgHoldNotice`, `Headline` d'un visuel, bloc `Related` de À traiter (≈ 100 px), `RunCard` des lectures (apparaît en tête).
- **Fenêtres centrées dont le contenu change de hauteur** (le cadre entier bouge) : zoom d'une image en ligne (image sans
  dimensions réservées), mode d'étiquetage (une image par réponse), occurrences d'un visuel, import, ajout à la bibliothèque,
  ZIP, menu de téléchargement.
- **Squelette de forme différente du contenu** : bibliothèque, À traiter, visuels, visuel, images en ligne, tableau de bord,
  statistiques, réglages (double squelette en cascade), occurrences, page Serveur et courbes du back-office.
- **Largeurs qui changent à l'état actif** : `font-medium` (onglets, `Segmented`, arbre de collections), `border` → `border-2`
  (choix de rôle), 4 → 5 colonnes des filtres quand les pays arrivent, bouton dont le spinner élargit le contenu.
- **Carte de connexion** recentrée verticalement à chaque erreur ou changement de champ.

## 6. Inventaire par sous-issue

Les lignes sont regroupées par composant : la plupart se règlent **à un seul endroit** partagé.

### 2/7 Fondations (AXE-715)

| Où | Constat | Traitement |
|---|---|---|
| `index.css` | aucun jeton ; 3 durées d'entrée ; courbes différentes | variables CSS (durées, courbes) dans `@theme` ; `toast-in`, `tip-in`, `pop-in` les utilisent ; classes `fade-in` et `rise-in` |
| `index.css:135` | mouvement réduit = 4 classes | une règle globale (durées ≈ 0, `animation-iteration-count`) ; décision fondu gardé / déplacement retiré à confirmer |
| `feedback.tsx:180`, `OccurrencesPanel.tsx:88` | `duration-300`, `duration-500` | ramenés aux jetons |
| `busy.tsx:21`, `Occurrences.tsx:51` | `duration-150`, `duration-200` en dur | jetons |
| `main.tsx:20` | polices chargées après le premier rendu | hors périmètre, signalé |
| `docs/BONNES_PRATIQUES.md` | pas de règle de mouvement | une phrase : durées, propriétés animables |

### 3/7 Contrôles (AXE-716)

| Où | Constat | Traitement |
|---|---|---|
| `ui.tsx:16` `buttonClass` | `transition-colors` ; `disabled:opacity-40` et `loading` instantanés ; spinner qui élargit le bouton | ajouter `opacity` ; réserver la place du spinner ; retour d'appui (`:active`) |
| `ui.tsx:305` `Segmented` ; `insights.tsx:112` `MetricTabs` ; `Settings.tsx` `SectionLink` ; `SiteImages` `ViewTabs` ; `review/Filters` onglets | aucune ou 150 ms ; `font-medium` / `border` changent la largeur | transition de couleur ; largeur stable |
| `ui.tsx:178,329`, `Reading.tsx`, `EditReference.tsx` | bordure du champ instantanée, anneau 150 ms ; `textarea` et `TagEditor` sans transition | aligner champs, `textarea`, `TagEditor` |
| `ui.tsx:293` `Chip` | `border` retiré à l'état actif (largeur) | largeur stable |
| `ui.tsx:103-144` badges, `Labels`, `StatusPill`, `STATE_PILL` | couleur d'un coup | fondu de couleur court (pas pour les alertes du back-office) |
| `Sidebar.tsx:44,64` | fond 150 ms, icône instantanée | icône dans la même transition |
| liens `hover:underline` / `hover:text-ink` (≈ 30 : Sidebar, Access, SitesCard, Reports, SiteImages, ImageCard, Pages, fil d'Ariane, etc.) | instantanés | `transition-colors` via une classe commune |
| `Grid.tsx:157`, `PhotoRow`, `ImageCard.tsx:34`, `Versions`, `FolderDialog`, `ZipDialog` | état sélectionné : bordure instantanée, ombre/`ring` pas dans `transition-colors` | `transition-[color,background-color,border-color,box-shadow]` ; coche d'`ImageCard` et de À traiter : retour immédiat (§4) |
| `Grid.tsx:193-210` | cases et « Copier le lien » révélés au survol / à la première coche (n transitions d'opacité à la fois) | garder, mais jamais animés par carte à l'ouverture |
| `PasswordStrength` | segments sans transition ; jauge qui apparaît à la première frappe | fondu de couleur ; place réservée |
| `Access.tsx` | champ mot de passe / case qui apparaissent, carte qui se recentre | place réservée |

### 4/7 Surcouches (AXE-717)

| Où | Constat | Traitement |
|---|---|---|
| `feedback.tsx:246` `Modal` (19 usages, 10 démontés par le parent) | `return null`, voile avec `backdrop-blur-[2px]`, rien d'animé | entrée voile + panneau ; sortie rapide ; garder monté le temps de la sortie (voir §7 risques) |
| `ConfirmProvider`, `ImageCard` zoom, `CompareView` zoom | état remis à `null` : titre et corps se vident à la fermeture | garder le dernier contenu pendant la sortie |
| `DangerZone.tsx:87` | `key={String(open)}` remonte la fenêtre | à retirer |
| `Dropdown.tsx:86`, `MultiDropdown:179` | liste montée / démontée ; focus perdu à la fermeture | fondu + léger glissement ; sortie |
| `SelectionActions.tsx:85-101`, `DownloadMenu.tsx:113` | menu, feuille mobile, popover | idem ; popover : taille stable |
| `RowMenu` de `SitesCard`, `<details>` (Options de lecture, Incidents, Valeurs) | `<details>` natif, ouverture instantanée | **écarter** (pas de sortie possible) ; au plus fondu du contenu |
| `SiteImages.tsx:350` `SelectionBar` | `fixed`, apparition brute | fondu + `translateY` |
| `Library` `SelectionHeader` | remplace `PageHeader` ; écart de 22 px | hauteur identique (§5) puis fondu |
| `Upload.tsx:177` `DropBox` | apparaît à chaque `dragenter` | ≤ 120 ms, opacité seule |
| `CursorTip`, `InfoTip`, `tip-in` | entrée 140 ms, sortie brute | sortie sur l'élément **intérieur** seulement |
| bulles de graphiques (`BarTooltip`, `LineTooltip`) | `transform` inline | fondu d'opacité seulement |
| bandeaux (`StaffBanner`, `RefreshFailed`, `SuspectReadNotice`, `OrgHoldNotice`, `Toolbar` Expirés, `ViewNote`) | insérés dans le flux | fondu **et** place réservée quand c'est possible |
| `Layout.tsx:28` menu mobile | dans le flux, pousse `<main>`, sans focus ni Échap | le sortir du flux (surcouche) → 5/7 |

### 5/7 Navigation (AXE-718)

| Où | Constat | Traitement |
|---|---|---|
| `main.tsx` / `Layout` `<Outlet/>` | remplacement brut du contenu | fondu court du contenu (`<ViewTransition>` de React 19.3 sur l'`Outlet`, ou classe d'entrée sur un conteneur sans `transform`) |
| `Access.tsx:301` `key={brand.id}` | remontage complet à chaque marque | garder le `Layout`, ne remonter que le contenu |
| `Sidebar.tsx:128` org → `FirstBrand` | `Navigate` ne rend rien : écran vide | rendre le `Layout` ou un squelette stable |
| `Profile.tsx:88`, `DangerZone.tsx:43` | l'adresse change avant la liste des marques (*à vérifier* : « Cette marque n'existe pas » possible) | à reproduire sur un environnement jetable, pas ici (écrit) |
| `NavProgress` | barre qui apparaît d'un coup | fondu |
| `Sidebar` pastilles de compteurs, entrées équipe | apparaissent avec `/me` ou `overview` | place réservée |
| `CollectionTree` | sous-niveaux sans animation, chevron sans `transition-transform` | chevron ; sous-niveaux sans animer la hauteur |
| `Settings.tsx:115` sections | remplacement brut | fondu du panneau |
| menu mobile `Layout.tsx:28` | voir 4/7 | tiroir en surcouche |

Scroll : aucun `ScrollRestoration` ; ne pas l'ajouter ici.

### 6/7 Chargements et listes (AXE-719)

| Où | Constat | Traitement |
|---|---|---|
| `Skeleton` (`ui.tsx:247`) → contenu | remplacement brut sur ≈ 25 écrans ; formes différentes (§5) | fondu du contenu à l'arrivée ; **d'abord** aligner les formes des squelettes |
| `Thumb`, `Grid` vignettes (icône → image), `ImageCard`, `OccurrencesPanel`, `EditReference.tsx:201` (`<a>`/`<div>` remonte l'image) | image qui « pop » | fondu à `onLoad` **seulement au changement d'URL** d'une même image, jamais par carte à l'ouverture d'une grande liste |
| grosses listes (bibliothèque 100 + 100 par clic sans plafond, À traiter, Images en ligne, Visuels sans pagination) | aucune virtualisation | **aucune animation d'entrée par carte** ; uniquement états locaux |
| listes qui perdent / gagnent une ligne (membres, invitations, collections, exclusions, rapports, audit, back-office) | brut | fondu court, sans animer la hauteur |
| `AuditLog.tsx:109` filtres ; `useServerHistory` période | pas de `placeholderData` : la liste disparaît puis squelette | garder l'ancien contenu (`Refreshing`) avant tout fondu |
| barres : `UsageBar`, `ShareBar`, `SplitBar`, `CollectionTree` santé, `Visuals` / `Summary` proportion, hauteur des barres | largeur / hauteur sans transition | `transform: scaleX/scaleY` ou fondu ; `SplitBar` : fondu seul (voisins) |
| `busy.tsx:19` ligne de `Refreshing` | apparition brute | fondu |
| graphiques (tracé SVG) | changent d'un coup | fondu du groupe, pas d'interpolation |
| spinners / squelettes en boucle | restent actifs en mouvement réduit | règle globale (2/7) |

### 7/7 Retours et finition (AXE-720)

| Où | Constat | Traitement |
|---|---|---|
| `feedback.tsx:51` `dismiss`, `:86` `update` | pas d'état « sortant » ; `update` recrée un toast absent ; pile qui saute | sortie en fondu ; `update` ne ressuscite pas un toast parti |
| `ToastProgress` | passages de ton et de barre bruts, carte de ±4 px | stabiliser |
| toasts, `toast-in` | 160 ms | jetons (2/7) |
| confirmations : « Enregistré », « Copié », « Lien envoyé », `SaveIndicator`, « Test envoyé », champs d'erreur (`role=alert`) | libellé / largeur brusques ; erreur qui pousse le formulaire | fondu court ; place réservée |
| tactile | aucun `:active`, survols collés | retour d'appui sans délai ; `@media (hover: hover)` |
| `docs/ARCHITECTURE.md`, `BONNES_PRATIQUES.md` | règle « comment ajouter une apparition » | à écrire |

## 7. Risques techniques pour les sorties animées

1. **`Modal` démonte tout de suite et son effet fait plusieurs choses à la fermeture** : retour du focus, retrait de
   l'écouteur Échap/Tab, déverrouillage du défilement. Une sortie de 150 ms doit les ordonner explicitement. Dix usages
   passent `open` en dur et sont démontés par leur parent : une sortie dans `Modal` ne les couvre pas sans changer leur
   façon de monter.
2. **Échap et pile de fenêtres** : chaque `Modal` écoute `document` sans `stopPropagation` ni test « suis-je au sommet »
   (cas réel : « Comparer » contient `CompareView` qui monte deux autres fenêtres). Une sortie allonge la fenêtre de risque.
3. **Focus** : `previous?.focus()` à la fermeture peut viser un élément déjà démonté (menu « Actions ») ; un `Modal` sans
   champ ni bouton dans le corps ne reçoit pas le focus ; `Dropdown` ne déplace jamais le focus ; le menu mobile n'a ni
   focus, ni Échap, ni `aria-controls`.
4. **Toasts** : pas d'état sortant ; `update()` recrée un toast supprimé ; `JobToasts` nettoie tout au démontage.
5. **`transform` imposé en inline** (`CursorTip`, bulles de graphiques) : toute animation `transform` sur le même élément
   écrase la position. L'animation reste sur un élément enfant.
6. **Contexte d'empilement** : les fenêtres ne sont pas dans un portail ; un `transform` / `filter` sur le conteneur de page
   (transition de page) recaleraient les `position: fixed` (voile, barre de sélection). Opacité seule, pas de `transform` sur
   `<main>`.
7. **Double déclenchement** : `ConfirmProvider` résout une promesse ; pendant une sortie, neutraliser les clics (`inert`).
8. **Largeur / hauteur animées** : barres, segments, menu mobile, panneaux dépliés. Préférer `transform: scale` ou le fondu ;
   jamais de hauteur de mise en page.

## 8. Estimations revues

| Issue | Estimation actuelle | Avis après audit |
|---|---|---|
| 714 Audit | 2 | fait (ce document) |
| 715 Fondations | 1 | 1 à 2 (règle de mouvement réduit à trancher, classes utilitaires) |
| 716 Contrôles | 2 | 3 (une dizaine de composants partagés + ≈ 30 liens + largeurs stables) |
| 717 Surcouches | 3 | 5 (mécanisme de sortie partagé, 19 `Modal`, `DangerZone`, `ConfirmProvider`, menus) ; **à découper en 717a Modal/Confirm et 717b menus, infobulles, barre de sélection, bandeaux** |
| 718 Navigation | 3 | 3 (si fondu de contenu seul) ; ajouter le remontage par marque et l'écran vide entre organisations |
| 719 Chargements et listes | 3 | 5 : **aligner les formes des squelettes est un chantier à part** (probablement 719a formes, 719b fondus) |
| 720 Retours | 2 | 2 |

## 9. Points restant à vérifier avant de coder

- Parcours sur téléphone et avec « réduire les animations » activé (non fait).
- Reproduction sur un environnement jetable : « Cette marque n'existe pas » après un renommage ; remontage lors d'une
  suppression de marque.
- Mesure de fluidité avant / après sur une bibliothèque de plusieurs centaines de visuels.
- Fondu des vignettes : confirmer sans re-déclenchement lors du défilement (`loading="lazy"`).
- Décision à prendre : mouvement réduit = fondus gardés et déplacements retirés (lecture MDN / WCAG) ou tout retiré.
- Décision à prendre : le back-office est un outil interne ; on ne lui applique que les composants partagés et les 3 sauts
  les plus visibles (période des courbes, dépliage du journal, aperçu d'e-mail), le reste est écarté.

## 10. Suivi : ce qui a été fait (AXE-714 à 720)

| Groupe de l'inventaire | Sort | Où |
|---|---|---|
| Jetons, courbes, mouvement réduit global, classes `fade-in` / `rise-in` / `fade-out`, durées isolées (§3, 2/7) | traité | AXE-715 |
| Liens, boutons, résumés : règle de base ; badges, jauge de mot de passe, icônes de barre latérale ; largeur des pastilles de À traiter ; enfoncement à l'appui (3/7) | traité | AXE-716 |
| Champs, anneau de focus, lignes de menu, cases, saisie (§4) | écarté, restent immédiats | AXE-716, décision §4 |
| `font-medium` à l'état actif (onglets, `Segmented`, arbre) ; soulignement au survol | reste (largeur de quelques pixels ; propriété non animable) | — |
| `Modal` (19 usages), `ConfirmProvider`, zoom, `DangerZone`, Échap / focus / défilement, `Present` pour les 10 fenêtres montées par un parent (4/7) | traité | AXE-717 (1/2) |
| Listes déroulantes, menu d'actions, popover de téléchargement, infobulles, barre de sélection des images, bandeaux (entrée) (4/7) | traité | AXE-717 (2/2) |
| `<details>` natifs, `RowMenu`, enveloppe de `CursorTip`, bulles des graphiques (position), `DropBox` | écarté (pas de sortie possible, ou suit le pointeur) | §4 |
| Bandeaux qui poussent le contenu, fenêtres centrées dont la hauteur change, bandeau d'indexation, `ViewNote` (§5) | **reste** : place à réserver, hors périmètre « transitions » ; l'en-tête de sélection de la bibliothèque (22 px) aussi | à ouvrir en issue séparée |
| Contenu de page, section de réglages, changement de marque sans remonter la colonne latérale, menu mobile en surcouche, barre `NavProgress`, chevron et sous-niveaux de l'arbre (5/7) | traité | AXE-718 |
| Écran vide entre deux organisations (`FirstBrand`) ; « Cette marque n'existe pas » après un renommage | **reste** (spinner retardé de 150 ms seulement) ; à reproduire sur un environnement jetable | à ouvrir en issue séparée |
| Squelettes de la forme du contenu (bibliothèque, À traiter, tableau de bord, statistiques, images en ligne, occurrences, titres des visuels et des réglages) (6/7) | traité | AXE-719 (1/2) |
| Squelettes de `AuditLog`, page Serveur, courbes du back-office, `Visual` | **reste** | idem |
| Contenu qui arrive (`Refreshing`), images qui chargent (`FadeImage`), grille de la bibliothèque, barres d'usage et de part, image de la fiche (6/7) | traité | AXE-719 (2/2) |
| Graphiques (tracé, axes), `SplitBar`, lignes ajoutées ou retirées d'une liste, `PhotoRow` | écarté (§4) | — |
| Sortie des toasts, `update()` qui ne ressuscite plus un toast parti, confirmations et messages d'erreur en fondu (7/7) | traité | AXE-720 |
| Pile de toasts : les voisins sautent quand un toast part | **reste** (la hauteur ne s'anime pas) | — |
| Tactile : survol collé | déjà évité (Tailwind 4 enveloppe `hover:` dans `@media (hover: hover)`) ; appui : AXE-716 | — |
| Durées : tout ≤ 200 ms ; ce qui dépasse 250 ms est une boucle d'attente (spinner 1 s, pulsation 2 s, barre 1,4 s) ou un retard d'apparition (`fade-in-late`, 150 ms) | vérifié par `grep` | AXE-720 |
