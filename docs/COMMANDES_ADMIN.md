# Commandes d'administration

Aide-mémoire des actions courantes. Détail des options : `CLI_REFERENCE.md`.
Les commandes `cloud-*` lisent `DATABASE_URL` et `SUPABASE_*` dans `.env`.
Sous Windows, si `nyra` n'est pas dans le PATH : `.venv\Scripts\nyra.exe`.

## Lancer en local

```bash
nyra serve                     # API + interface : http://127.0.0.1:8000
nyra worker                    # exécute les tâches (crawl, match, index, report)
cd frontend && npm run dev     # interface en mode dev : http://localhost:5173 (proxy /api vers :8000)
```

## Organisations

| Action | Commande |
|---|---|
| Créer une organisation + son premier admin | `nyra cloud-provision-org --name "Rémy Martin" --slug remy-martin --admin-email admin@example.com` |

- Crée aussi une première marque du même nom.
- L'admin reçoit une invitation par e-mail s'il n'a pas de compte (pas d'inscription publique).
- Le slug est normalisé (minuscules, tirets). Erreur si le slug existe déjà.

## Utilisateurs d'une organisation

| Action | Commande |
|---|---|
| Ajouter quelqu'un (invitation si pas de compte) | `nyra cloud-invite --org remy-martin --email a@b.com --role client` |
| Changer un rôle (client ↔ admin) | même commande avec le nouveau `--role` (met à jour si déjà membre) |

Rôles : `admin` = bibliothèque, crawls, rapports, réglages. `client` = lecture et décisions.

## Accès back office Nyra (`/interne`)

| Action | Commande |
|---|---|
| Donner l'accès | `nyra cloud-staff --email arthur@example.com` |
| Retirer l'accès | `nyra cloud-staff --email arthur@example.com --remove` |

## Marques et sites

Pas de commande CLI : tout se fait dans l'interface, **Réglages** d'une organisation (admin).
Endpoints API équivalents : `POST/PUT/DELETE /api/orgs/{org_id}/brands[/{brand_id}]` et
`.../brands/{brand_id}/sites`.

- Supprimer une marque supprime aussi ses données (refuse s'il y a une tâche en cours).

## Ce qui n'existe pas en commande (à faire en SQL sur Supabase)

Retirer un membre d'une organisation (`role` = `admin` ou `client` uniquement, pas de statut désactivé) :

```sql
delete from public.memberships
where org_id = (select id from public.organizations where slug = 'remy-martin')
  and user_id = (select id from auth.users where lower(email) = lower('a@b.com'));
```

Désactiver complètement un compte : Supabase, Authentication > Users.

## Calibrage

```bash
nyra cloud-calibrate --org remy-martin --out-csv sweep.csv
```

## Pipeline hors ligne (SQLite)

```bash
nyra ingest-refs --dir refs/ --csv refs.csv
nyra crawl --site https://www.example.com --max-pages 300
nyra match
nyra report --within-days 90
nyra run-all --site https://www.example.com --dir refs/ --csv refs.csv
```
