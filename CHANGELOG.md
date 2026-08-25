# Journal des versions

Toutes les evolutions notables depuis `v0`. Le format suit [Keep a Changelog][kac] et le
versionnage [semantique][sv] : majeure pour une rupture, mineure pour une fonctionnalite,
correctif pour un correctif.

[kac]: https://keepachangelog.com/fr/1.1.0/
[sv]: https://semver.org/lang/fr/

## [0.8.1] — 2026-08-25

### Interne — « Hey Claude » verse au depot, desactive

L'assistant d'arriere-plan est conserve mais arrete : service systemd renomme,
raccourcis i3 commentes, aucun lanceur automatique.

- Plafond de depense en centimes couvrant STT ET TTS, avec projection.
- Detection de sortie audio : inutile d'ecouter quand les haut-parleurs jouent.
- Desactive parce que le rapport interet / cout ne le justifiait pas, et parce que
  `toggle-claude.sh` affichait « ecoute REACTIVEE » sans jamais verifier que le
  service tournait.

## [0.8.0] — 2026-08-25

### Ajoute — orchestration : chaine STT, fenetre de parole, dictee retenue

Ce qui assemble le tout : LiveKit pour l'audio, Claude Code pour le travail, un
porte-parole Haiku pour la voix.

- Chaine de reconnaissance Azure -> Deepgram -> local, decidee par une seule
  fonction que l'agent ET le panneau lisent, pour qu'ils ne racontent pas deux
  histoires. Deepgram `nova-3` accepte le `keyterm`, donc le vocabulaire du
  projet survit a la bascule.
- Fenetre de parole reglable. Le defaut de LiveKit committait le tour apres 0,3 s
  de silence : une pause pour reflechir devenait une fin de phrase, et la suite
  arrivait comme un second message par-dessus. Plancher a 5 s, plafond
  proportionnel — un plafond fixe se retournait contre un plancher long.
- Mode « retenir » et rattrapage d'un seul tour pendant le decompte.
- Une ligne « toi » signifie « pris en compte », et rien d'autre : publiee par
  chaque branche qui consomme l'enonce, jamais en amont.
- `Voix.sess` : `Agent.session` leve des que l'activite est absente, or le tableau
  appelle depuis l'exterieur d'un tour. C'est ce qui faisait echouer « couper le
  micro » par intermittence.
- Couper le micro ne touche plus a la parole en cours : « arrete de m'ecouter » et
  « arrete de parler » sont deux demandes differentes.
- Avertissement de session unique : deux agents se disputent le micro et la meme
  fenetre de quota.

## [0.7.0] — 2026-08-25

### Ajoute — tableau de bord temps reel dans le navigateur

En `bypassPermissions`, plus rien n'arrete un appel d'outil : la seule chose
entre « ca marche » et « qu'est-ce qu'il vient de faire » est la visibilite.
Un fichier HTML servi par aiohttp, un WebSocket, aucune etape de build.

- Panneau de configuration au lancement : les valeurs EN VIGUEUR, pas une copie qui
  derive.
- Cycle de vie de chaque action : un indicateur qui tourne, puis un verdict. Tout
  ce qui s'allume a un chemin garanti pour s'eteindre — resolution, fin de tour,
  interruption, perte de connexion, ou garde-fou temporel.
- Barre de saisie : ecrire au lieu de parler. Le champ est un `textarea` qui suit
  son contenu, parce qu'une longue dictee sortait du champ et devenait
  incorrigible.
- Decompte avant envoi, selecteurs de modele, d'effort et de delai, bandeau de
  cogitation, heure de l'horloge en colonne.
- File de commandes : un clic ne disparait plus quand la socket est morte. C'etait
  la cause du « bouton qui ne marche pas du premier coup ».
- Rejeu d'historique idempotent : chaque evenement porte un numero, la page ignore
  ce qu'elle affiche deja. Sans ca, trois reconnexions donnaient trois copies de
  la session.
- File bornee par client avec un seul ecrivain. La version precedente creait une
  tache asyncio par evenement et par client : 1,5 Go de RSS sur deux jours.

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
