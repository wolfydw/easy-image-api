# Repository Instructions

This repository is the source of truth for the `easy-image-api` skill.

- Make all future skill changes in this repository first.
- Run the complete test suite and skill validation before deployment.
- Treat `~/.codex/skills/easy-image-api/` as an installed deployment copy.
- Store the user configuration at
  `~/.codex/skills/easy-image-api/config.json`.
- The repository's `config.json` must contain placeholders only. Never commit a
  real endpoint or API key.
- During upgrades, never read, overwrite, delete, or otherwise modify the
  installed `config.json`; update all other skill files around it.
