# Nyra : plan de la vidéo

**Ce que c'est.** Nyra lit les sites d'une marque comme un visiteur, retrouve les photos de sa bibliothèque dont la licence a expiré — même recadrées, redimensionnées ou retournées — et les classe par urgence.
**Pour qui.** Les équipes marque / droits des maisons qui publient beaucoup de visuels sous licence.
**Ce qui la distingue.** Pas d'IA générative : empreintes perceptuelles + CLIP + vérification par points clés, et « l'algorithme propose, une personne décide ».
**Accroche visuelle.** Une page produit tout à fait normale… dont la photo principale est expirée depuis 47 jours.

- **Angle :** la photo expirée que personne n'a vue, et l'outil qui la trouve pour vous.
- **Ton :** `default` teinté `polished` : net, calme, l'identité « carnet de croquis » de Nyra (papier crème, encre forêt, surligneur jaune).
- **Identité :** feuille de styles réelle du projet (`frontend/src/index.css`, compilée avec Tailwind 4.3.5, la version du projet), polices Inter / Bricolage Grotesque / Geist Mono, logo `nyra-logo` redessiné depuis `public/favicon.svg`. Markup copié des composants : `Hero`, `CompareView` + `DecisionPanel`, `OccurrenceList` / `PhotoRow`, `ConfidenceBadge`, `StatusBadge`, `DecisionBadge`, sidebar.
- **Données :** marque fictive « Maison Exemple » (celle des échantillons d'e-mails du projet), site `maison-exemple.com`, photos dessinées pour l'occasion (aucune image ni donnée client). Les chiffres du tableau de bord sont des données de démonstration, pas des promesses.
- **Langue :** français, comme l'interface.
- **Musique :** 120 BPM (temps = 0,5 s, mesure = 2 s), toutes les coupes sur un temps.

## Storyboard (20 s)

| # | Temps | Scène | À l'écran |
|---|---|---|---|
| 1 | 0,0–3,0 | Accroche | Une fiche produit de maison-exemple.com. La photo se fait entourer au surligneur, une étiquette tombe : « Licence expirée il y a 47 j ». Titre : « Cette photo n'a plus le droit d'être en ligne. » |
| 2 | 3,0–5,5 | Révélation | Logo Nyra, puis « Nyra lit vos sites et retrouve vos visuels expirés. » |
| 3 | 5,5–9,5 | Lecture | Tableau de bord réel (`Hero`) : « Dernière lecture » se remplit (pages lues, images comparées qui défilent), le titre « **3** visuels expirés sont encore en ligne » s'écrit avec le surligneur. |
| 4 | 9,5–13,5 | Comparaison | `CompareView` : la référence à gauche, la version recadrée et retournée trouvée sur le site à droite ; le badge « Confirmé » apparaît. Légende : « Même recadrée, redimensionnée ou retournée. » |
| 5 | 13,5–17,0 | Décision | Liste « À traiter » + panneau « Est-ce bien ce visuel ? » : clic sur « C'est bien lui » (touche C), la ligne passe à « À retirer », suivante. Légende : « L'algorithme propose. Vous décidez. » |
| 6 | 17,0–20,0 | Final | Logo, « Vos droits photo, vérifiés sur vos sites. », sous-titre « Nyra ». |

Accroche → révélation → 3 temps forts → final. Coupes : 3,0 / 5,5 / 9,5 / 13,5 / 17,0 (toutes sur un temps).
