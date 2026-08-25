# Journal des versions

Toutes les evolutions notables depuis `v0`. Le format suit [Keep a Changelog][kac] et le
versionnage [semantique][sv] : majeure pour une rupture, mineure pour une fonctionnalite,
correctif pour un correctif.

[kac]: https://keepachangelog.com/fr/1.1.0/
[sv]: https://semver.org/lang/fr/

## [0.6.0] — 2026-08-25

### Ajoute — modele a chaud, effort par reconstruction, correlation des outils

La session Claude Code unique et autoritaire, pilotable en cours de route.

- `changer_modele()` par `set_model()`. Le cout a connaitre : ca invalide le cache
  de prompt.
- `changer_effort()` par reconstruction du client : le SDK n'expose PAS de
  `set_effort`. La session est reprise, donc le contexte revient complet. Deux
  garde-fous : jamais en pleine tache, et construire avant de basculer avant de
  lacher l'ancien — l'ordre inverse laissait 2,5 s ou le client etait mort.
- `ToolUseBlock.id` et `is_error` publies : le verdict d'un outil vient du SDK, pas
  d'une supposition. Un outil sans sortie publie quand meme son resultat, sinon
  son indicateur tournerait indefiniment.
- Les deltas vides ne sont plus publies : mesure sur une session reelle, 65
  evenements « pensee » sur 65 avaient un texte vide.

## [0.5.0] — 2026-08-25

### Ajoute — transcripts, etats de conversation et reprise complete

Une conversation finie ne laissait rien : le tableau vit en memoire, la voix
passe, et au lancement suivant on repartait de zero.

- Un fichier Markdown par conversation, ecrit de facon incrementale : une coupure
  ne perd que le tour en cours.
- Index append-only : reecrire a chaque tour inviterait a le corrompre, ajouter ne
  peut laisser qu'une ligne incomplete, que le lecteur ignore.
- Trois etats, dont « interrompue » : l'autorite est le PID, pas un marqueur de
  fermeture qu'un `kill -9` n'ecrit jamais. Double verification, parce qu'un PID
  libere est reattribue.
- `dossier_de_session()` : Claude Code range ses sessions PAR repertoire, et
  reprendre depuis le mauvais dossier ne donne pas une erreur mais une session
  introuvable — donc une conversation qui repart de zero en silence.
- `rejouer_session()` : l'historique remis en forme de conversation. Une premiere
  version publiait chaque appel d'outil, soit 247 lignes de bruit sur soixante
  tours ; un tour tient maintenant en trois lignes.

## [0.4.0] — 2026-08-25

### Ajoute — fenetres de rate limit en pourcentage, et cout par tour

Sur un siege entreprise le montant en dollars ne veut rien dire : ce qui
contraint le travail, c'est la fenetre de rate limit.

- Lecture de `/api/oauth/usage` avec le jeton OAuth relu a chaque appel (la CLI le
  rafraichit, un jeton en cache devient invalide).
- Repli exponentiel jusqu'a 30 min sur 429, et la derniere lecture connue est
  conservee plutot qu'effacee : un pourcentage un peu vieux vaut mieux qu'un vide.
- Ecart de fenetre par tour. L'API renvoie des points ENTIERS, donc un tour court
  affiche « +-0 » : c'est mesure, pas casse. Et la fenetre etant partagee entre
  sessions, le libelle dit « fenetre », jamais « cout ».

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
