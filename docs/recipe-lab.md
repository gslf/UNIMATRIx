# Recipe lab

Open `/recipe-lab` to create a recipe draft from a bundled recipe. Select domains, conditions, roles, seeds, peers, and model candidates. Preview the generated cases before running an evaluation.

Use a smoke run for a quick engine check, a pilot for sequential seed blocks, and a full evaluation for every case. Review episode status, metrics, comparisons, and probe evidence before saving a draft as a named recipe. The recipe manager at `/recipeManager` lists saved recipes and their revisions.

Saved recipes live in `config/recipes`, which is excluded from Git and release artifacts. Give a changed recipe a new ID; its cases and scores cannot be substituted into an existing run. Only `standard-v1` and `validation-v1` are bundled with UNIMATRIx.
