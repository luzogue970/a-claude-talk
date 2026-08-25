# Journal des versions

Toutes les evolutions notables depuis `v0`. Le format suit [Keep a Changelog][kac] et le
versionnage [semantique][sv] : majeure pour une rupture, mineure pour une fonctionnalite,
correctif pour un correctif.

[kac]: https://keepachangelog.com/fr/1.1.0/
[sv]: https://semver.org/lang/fr/

## [0.1.1] — 2026-08-25

### Interne — ignorer secrets, archives et transcripts

Aucune cle ne doit pouvoir partir sur GitHub, et les transcripts citent le contenu
des projets.

- `*.conf`, `.env`, `secrets.env` : les cles vivent dans `~/.config/claude-talk/secrets.env`, hors du depot.
- `*.7z` : l'archive de l'ancien systeme contenait des cles en clair.
- `conversations/` : les transcripts citent le code des projets.
