# Journal des versions

Toutes les evolutions notables depuis `v0`. Le format suit [Keep a Changelog][kac] et le
versionnage [semantique][sv] : majeure pour une rupture, mineure pour une fonctionnalite,
correctif pour un correctif.

[kac]: https://keepachangelog.com/fr/1.1.0/
[sv]: https://semver.org/lang/fr/

## [0.3.0] — 2026-08-25

### Ajoute — repli local faster-whisper quand le cloud lache

Le palier gratuit d'Azure s'arrete a 5 h de transcription par mois. Une fois
epuise il repond `400 Quota exceeded` sur chaque requete, et sans filet la
session devient sourde sans rien dire.

- `WhisperLocal`, rendu streaming par le VAD via `StreamAdapter`.
- Modele charge a la premiere phrase, pas au demarrage : quand le cloud repond,
  ce module ne coute rien.
- Vocabulaire du projet passe en `initial_prompt`, equivalent de la phrase list
  d'Azure.
- Mesure assumee : `small` prend 4,2 a 4,9 s pour 2 s d'audio sur ce portable.

## [0.2.0] — 2026-08-25

### Ajoute — reconnaitre les ordres locaux sur toute formulation

Une regex sur des sequences de mots ne matche que les formulations prevues.
La detection par co-occurrence (un verbe et un objet de meme intention, dans
n'importe quel ordre) couvre la parole naturelle sans l'enumerer.

- Six ordres repondus sans latence et jamais transmis a Claude : micro, silence,
  arret, repete, quota, statut, modele.
- Deux vetos avant toute detection : au-dela de douze mots c'est une tache qu'on
  decrit, et le vocabulaire de code (`ajoute`, `fichier`, `bouton`...) renvoie a
  Claude. Sans eux, « renomme la variable qui gere le silence » coupait l'agent.

## [0.1.1] — 2026-08-25

### Interne — ignorer secrets, archives et transcripts

Aucune cle ne doit pouvoir partir sur GitHub, et les transcripts citent le contenu
des projets.

- `*.conf`, `.env`, `secrets.env` : les cles vivent dans `~/.config/claude-talk/secrets.env`, hors du depot.
- `*.7z` : l'archive de l'ancien systeme contenait des cles en clair.
- `conversations/` : les transcripts citent le code des projets.
