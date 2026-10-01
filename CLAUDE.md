# Nyra : consignes pour les agents IA

Avant d'écrire ou de modifier du code, lire :

1. [`.agents/rules/ponytail.md`](.agents/rules/ponytail.md) : écrire le moins
   de code possible (réutiliser, supprimer, ne rien ajouter d'inutile).
2. [`docs/BONNES_PRATIQUES.md`](docs/BONNES_PRATIQUES.md) : les 10 règles de
   qualité (flux simple, boucles et ressources bornées, fonctions de 60 lignes
   au plus, erreurs jamais avalées, zéro avertissement).
3. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) : comment le produit est
   construit.

Avant de déclarer une tâche terminée, ces commandes passent sans
avertissement :

```bash
ruff check backend
cd frontend && npx tsc --noEmit && npm run build
pytest -q    # les tests cloud demandent TEST_DATABASE_URL (voir backend/tests/conftest.py)
```

Dire ce qui n'a pas été vérifié.
