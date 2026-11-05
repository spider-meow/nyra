# Textes explicatifs de l'interface (AXE-760)

La règle est dans [`BONNES_PRATIQUES.md`](BONNES_PRATIQUES.md) (« Textes explicatifs (interface) ») : on n'ajoute pas de texte qui explique
l'écran. Ce document est l'inventaire du passage fait sur tout le site : chaque texte, sa décision, et la PR qui l'a traitée.

## Décisions possibles

- **Retirer** : le texte dit ce que l'écran est ou ce qu'on voit déjà.
- **Infobulle** : l'information est vraie mais rare ou technique ; elle passe dans le ⓘ (`InfoTip`, comme les titres de blocs des réglages).
- **Raccourcir** : une phrase qui annonce une conséquence (« Impossible à annuler ») reste, en une ligne.
- **Libellé** : le texte compensait un nom flou ; on corrige le nom (titre, bouton, onglet), pas la légende.
- **Garder** : il sert à agir (libellé de champ ou de bouton, erreur, état vide qui dit quoi faire, message de retour, texte d'une boîte de dialogue,
  texte légal ou de facturation, page `/robot` qui est elle-même l'explication).

## Ce qui n'est pas un texte explicatif (et qu'on ne touche pas)

Les valeurs (dates, nombres, noms), les `hint` de `Stat` qui sont des chiffres (« médiane 50 Ko · max 3 Mo »), les états de santé (« le worker publie, la
file avance »), l'état de la configuration d'envoi d'e-mails, les messages d'accès et d'erreur (`pages/Access.tsx`), les textes des boîtes de dialogue
(import, ZIP, collection) qui disent ce que le bouton va faire.

## Inventaire

Relevé d'après le code le 5 novembre 2026 ; la colonne « Fait » est remplie par chaque PR (voir « Découpage »).

### Tableau de bord, À traiter, Bibliothèque, Images en ligne, Sites et lectures (PR B, fait)

| Écran | Fichier | Texte | Décision |
|---|---|---|---|
| Tableau de bord | `dashboard/Start.tsx` | « Trois étapes pour savoir quels visuels retirer du site. » | Retirer |
| Tableau de bord | `dashboard/Hero.tsx` | « Leurs droits sont échus mais ils apparaissent toujours sur le site. Chaque jour compte. » / « D'après la dernière lecture… » | Retirer |
| Tableau de bord | `dashboard/Hero.tsx` | Note « Par ailleurs, N images… jamais eu ses droits vérifiés. Certaines sont peut-être expirées : elles ne sont pas comptées ici… » | Raccourcir (garder le lien) ; l'explication passe en infobulle |
| Tableau de bord | `Dashboard.tsx` | Carte « images en ligne dont les droits n'ont jamais été vérifiés » : « Certaines sont peut-être expirées. » avant le lien | Retirer (le lien « Les vérifier » reste) |
| À traiter | `review/List.tsx` | Bandeau « Ces visuels n'ont été retrouvés nulle part… » (non trouvés) | Infobulle |
| À traiter | `review/Related.tsx` | « Votre décision s'applique à toutes. Cliquez sur l'une pour la comparer. » | Raccourcir |
| Bibliothèque | `library/Toolbar.tsx` | Bandeau de l'onglet Expirés « Ces visuels ne sont plus sous droits… » | Infobulle (le lien « Voir ce qui est encore en ligne » reste) |
| Bibliothèque | `library/OccurrencesPanel.tsx` | « Vérifie chaque image déjà lue, recadrages compris. Peut prendre plusieurs minutes. » | Retirer |
| Images en ligne | `SiteImages.tsx` | Légende des raccourcis « P packshot · N pas un packshot · ⌫ » | Infobulle |
| Images en ligne | `site-images/AddToLibrary.tsx` | « Chaque image garde le nom de son fichier sur le site. L'échéance… » | Retirer |
| Sites et lectures | `scans/RunCard.tsx` | « Un plafond : un site plus petit est lu en entier… » | Retirer |
| Sites et lectures | `scans/RunCard.tsx` | « Durée inconnue : pas encore assez de lectures pour la prévoir… » | Raccourcir |
| Sites et lectures | `scans/RunCard.tsx` | Aide de la case « sans relire les pages connues » | Raccourcir |
| Sites et lectures | `scans/SitesCard.tsx` | Note « Démarre dès qu'une place se libère sur le serveur. » sous une adresse en attente (le badge dit déjà « En attente ») | Retirer |

### Statistiques et back-office (PR C, fait)

`Section.description` devient une infobulle `ⓘ` (prop `info`) ; les descriptions qui ne sont que des valeurs restent des valeurs.

| Écran | Fichier | Texte | Décision |
|---|---|---|---|
| Statistiques | `Statistics.tsx` | « Survolez une barre pour le détail. Une mesure à la fois. » | Retirer |
| Statistiques | `Statistics.tsx` | « Cumul sur les N dernières lectures. Les pages sont lues en parallèle : le cumul dépasse la durée réelle. » | Infobulle |
| Statistiques | `Statistics.tsx` | « Cumul des lectures récentes », « Toutes les tâches de l'organisation depuis le début », « Les 30 dernières… » | Retirer |
| Statistiques | `StatisticsSections.tsx` | « Fichiers distincts du site » | Retirer |
| Back-office | `BackOffice.tsx` | « Les 60 dernières sur 90 jours. Survolez une barre… », « Cumul des lectures sur 90 jours, toutes marques. » | Retirer |
| Back-office | `BackOfficeSections.tsx` | « Stockage, lectures sur 90 jours, qualité du matching, échecs sur 30 jours. », note « Détail ouvre la page Statistiques… » | Retirer |
| Back-office | `backoffice/Server.tsx` | « Mis à jour toutes les 5 s. Un worker publie ses chiffres toutes les 15 s… » | Infobulle |
| Back-office | `backoffice/ServerHistory.tsx` | « Un relevé par minute et par conteneur… Les lignes pointillées sont les limites… » | Infobulle |
| Back-office | `backoffice/Usage.tsx` | « Pages lues et invitations du jour… Orange à partir de 80 %… » | Infobulle |
| Back-office | `backoffice/Usage.tsx` | « Laissez vide pour garder la limite par défaut… » | Garder (sert à agir) |
| Back-office | `backoffice/Accounts.tsx` | « À n'importe quelle organisation… », « Contrôle périodique : chaque personne doit être connue… » | Infobulle (elles disent ce que fait l'action) |
| Back-office | `backoffice/Organizations.tsx` | « Un client, avec une première marque du même nom (facturée comme toute marque)… » | Infobulle (mention de facturation : on ne la supprime pas) |
| Statistiques, back-office | `components/insights.tsx` | Note sous le tableau « Le taux de faux positifs n'est calculé que sur les correspondances déjà décidées… » | Infobulle de la carte « Correspondances » |

### Réglages et connexion (PR D, fait)

Les descriptions de `SettingsBlock` sont déjà des infobulles : rien à faire. Restent les `hint` des lignes, un par un.

| Écran | Fichier | Texte | Décision |
|---|---|---|---|
| Réglages | `settings/Profile.tsx` | « Affiché en haut de la barre latérale. PNG, JPEG ou WebP, de préférence carré. » | Raccourci : « PNG, JPEG ou WebP, de préférence carré. » |
| Réglages | `settings/Profile.tsx` | « Le nom change aussi l'adresse de la marque. » | Garder (conséquence) |
| Réglages | `settings/Logos.tsx` | « Les images reconnues comme logos ne sont plus proposées… » | Garder (c'est la conséquence de la case) |
| Réglages | `settings/Packshots.tsx` | « Ils quittent « À qualifier » mais restent dans « Toutes »… rien n'est masqué ni ignoré. » | Raccourci à la conséquence |
| Réglages | `settings/Schedule.tsx` | Aide de la case (deux phrases sur les dates et l'e-mail mensuel) | Raccourci : deux lectures par mois, résumé par e-mail chaque mois |
| Réglages | `settings/Detection.tsx` | « Recommandé. » ; aide de « comparer une seule fois » ; aide des redirections | Raccourcis ; les cas de redirection qui ne concluent rien passent dans l'infobulle du bloc |
| Réglages | `settings/Detection.tsx` | Libellé « Délai par défaut, en jours » + aide | Libellé corrigé : « Alerter avant l'expiration, en jours », aide retirée |
| Réglages | `settings/Reading.tsx` | Aide de « Passer les bandeaux cookies et les contrôles d'âge » | Retirer (le libellé le dit) ; l'aide de robots.txt (avertissement) reste |
| Réglages | `settings/Indexation.tsx` | « Elle peut prendre quelques minutes, selon le nombre de visuels. » | Retirer (l'état « en cours » reste) |
| Réglages | `settings/DangerZone.tsx` | « Sa bibliothèque, ses adresses… sont effacés. Impossible à annuler. » | Garder (conséquence) |
| Réglages | `settings/Account.tsx`, `Usage.tsx`, `Reading.tsx` (unités) | Rôle, lien de réinitialisation, valeurs d'usage, « Par lecture. », « Les images plus petites… sont ignorées. » | Garder (valeurs et conséquences) |
| Connexion | `Access.tsx` | « Chaque marque de {org} a sa bibliothèque et ses adresses… » (Première marque) | Retirer |

## Découpage

A. cet inventaire (docs). B. écrans de travail. C. Statistiques et back-office. D. Réglages et connexion.
Chaque PR met à jour la colonne ou la ligne qu'elle traite, et joint des captures avant / après (bureau et téléphone).
