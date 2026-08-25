# claude-talk

Agent conversationnel vocal pour développer, en français. Tu parles, Claude Code travaille
aussi longtemps qu'il faut, puis il te **raconte** ce qu'il a fait — pas en lisant son
markdown à voix haute, mais en te briefant comme un collègue.

## Architecture

```
     micro du PC
             │
   ┌─────────▼──────────────────────────────────────────────┐
   │  LiveKit Agents — console locale, aucun serveur        │
   │   • AEC WebRTC : soustrait la voix de l'agent du micro  │
   │   • Silero VAD + TurnDetector v1-mini (local, FR)      │
   │   • interruption adaptative, reprise sur faux positif  │
   │   • Azure STT fr-FR + phrase_list (vocabulaire dev)    │
   └─────────┬──────────────────────────────────────────────┘
             │  llm_node : route la phrase entendue
             │
   ┌─────────▼──────────┐        ┌──────────────────────────┐
   │  routage local     │        │  worker.py               │
   │  « stop »          │───────▶│  UNE session Claude Code │
   │  « t'en es où ? »  │  0 ms  │  (Agent SDK, persistante)│
   └────────────────────┘        │  journal des outils      │
                                 │  interrupt(), permissions│
                                 └──────────┬───────────────┘
                                            │ événements réels
                                 ┌──────────▼───────────────┐
                                 │  porte_parole.py         │
                                 │  Haiku chaud, sans outil │
                                 │  débrief oral en flux    │
                                 │  + vérif anti-invention  │
                                 └──────────┬───────────────┘
                                            ▼
                              Azure TTS → haut-parleurs
```

**Un seul contexte fait autorité** : la session Claude Code. Le porte-parole ne devine pas,
il lit le journal des outils réellement appelés. C'était la faille de la version
précédente : le résumé parlé ne voyait que le dernier paragraphe de texte.

## Installation

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt          # versions figées, voir l'avertissement
.venv/bin/python voix/agent.py download-files      # modèles locaux (VAD, détecteur de tour)
```

> **Les versions sont figées volontairement.** LiveKit annonce `console mode is deprecated
> and will be removed in a future release`, avec `lk agent console` (le CLI Go de LiveKit)
> comme remplaçant. Aujourd'hui `console` marche, en local, sans compte. Ne mets pas
> `livekit-agents` à jour à l'aveugle : le jour où tu le fais, c'est ce mode-là qu'il faut
> revérifier en premier.

Les secrets vivent **hors du dépôt**, dans `~/.config/claude-talk/secrets.env` (chmod 600) :

```
AZURE_SPEECH_KEY=...
AZURE_SPEECH_REGION=francecentral
AZURE_SPEECH_VOICE=fr-FR-Marc:MAI-Voice-2-Flash
```

## Lancer

```sh
# lister les périphériques
.venv/bin/python voix/agent.py console --list-devices

# en conversation, dans le projet courant
VOIX_WORKDIR="$PWD" .venv/bin/python voix/agent.py console

# en tapant au clavier, pour mettre au point sans micro
VOIX_WORKDIR="$PWD" .venv/bin/python voix/agent.py console --text
```

### Micro et haut-parleurs du PC, sans casque

Ça fonctionne : le mode console applique l'annulation d'écho WebRTC (`echo_cancellation`,
`noise_suppression`, `high_pass_filter`, `auto_gain_control`) et lui donne la lecture en
flux inverse, avec un délai recalculé à chaque tampon d'entrée. C'est ce que l'ancien
système mesurait à la main avant de simplement couper le micro pendant 28 secondes.

Deux réglages pratiques quand on n'a pas de casque :

- **Garde un volume modéré.** L'AEC soustrait la voix de l'agent, il ne fait pas de miracle
  à plein volume avec le micro à 30 cm de l'enceinte.
- **Les 3 premières secondes** de chaque prise de parole ne sont pas interruptibles : c'est
  le temps de chauffe de l'AEC, volontaire, pour que l'agent ne se coupe pas lui-même.

Si de l'écho passe malgré tout (l'agent se coupe seul, ou tu vois ta propre synthèse
transcrite comme si tu avais parlé), il reste deux leviers, dans cet ordre : monter
`min_interruption_words` à 3 dans `agent.py`, puis charger l'AEC de PipeWire en amont
(`pactl load-module module-echo-cancel`) pour empiler une seconde passe.

## Réglages

| Variable | Défaut | Rôle |
|---|---|---|
| `VOIX_WORKDIR` | `$PWD` | le projet sur lequel Claude Code travaille |
| `VOIX_WORKER_MODEL` | `claude-opus-5` | le modèle qui fait le travail |
| `VOIX_WORKER_EFFORT` | `xhigh` | `low` \| `medium` \| `high` \| `xhigh` \| `max` — réglable en cours de session depuis le tableau |
| `VOIX_SPEAKER_MODEL` | `claude-haiku-4-5` | le porte-parole |
| `VOIX_LANGUAGE` | `fr-FR` | STT et TTS |
| `VOIX_STT` | `auto` | `auto` (Azure → Deepgram → local) \| `azure` \| `deepgram` \| `local` |
| `DEEPGRAM_API_KEY` | vide | active le repli Deepgram ; sans elle la chaîne est Azure → local |
| `VOIX_DEEPGRAM_MODELE` | `nova-3` | seul `nova-3` accepte le biais de vocabulaire (`keyterm`) |
| `VOIX_DEEPGRAM_KEYTERM` | `1` | `0` coupe le biais si Deepgram le refusait en français |
| `VOIX_RETENIR_OCCUPE` | `1` | retenir ce qui est dit pendant que Claude travaille ; `0` rétablit l'envoi immédiat |
| `VOIX_ECOUTE_MIN` | `5.0` | silence (s) avant envoi du tour — réglable depuis le tableau ; le défaut LiveKit est 0,3 s |
| `VOIX_JOURNAL` | `<projet>/conversations` | où sont écrits les transcripts et l'index |
| `VOIX_ECOUTE_MAX` | *(déduit)* | plafond « phrase inachevée » — vide, il suit le plancher (× 2,5) |
| `VOIX_STT_LOCAL_MODELE` | `small` | `small` (~4 s pour 2 s d'audio, correct) \| `base` (~1,7 s, moins fiable) |
| `VOIX_TTS` | `azure` | `azure` \| `local` (Piper, pas encore branché) |
| `VOIX_INPUT_DEVICE` / `VOIX_OUTPUT_DEVICE` | vide | sinon le périphérique système par défaut |

Le vocabulaire biaisé pour la reconnaissance est dans `config.PHRASE_LIST` ; le nom du
projet et la branche git y sont ajoutés automatiquement. **Ajoute-y ton jargon** : sans ça
Azure transformait « MQL » en « kubedka » et « un fichier point MD » en « un point MD ».

## Tableau de bord

Au lancement, une page s'ouvre sur `http://127.0.0.1:7788` (loopback uniquement — ce flux
transporte le contenu de ton code).

Elle commence par un **panneau de configuration** : projet, modèle et effort, porte-parole,
mode de permission, compte Claude qui paie, moteur de reconnaissance et nombre de termes
biaisés, voix de synthèse, détecteur de fin de tour, seuils d'interruption, état de
l'annulation d'écho, binaire `claude` utilisé. Les valeurs affichées sont **celles en
vigueur**, lues depuis `config.py`, pas une copie qui dérive.

Puis, en temps réel :

| Piste | Contenu |
|---|---|
| **toi** | ce que la reconnaissance a compris (final), et l'interim en grisé |
| **claude** | tout ce que la voix dit, y compris les accusés de réception |
| **réflexion** | la pensée du modèle, en flux |
| **écrit** | sa réponse écrite, en flux |
| **outil** | chaque appel, avec ses **arguments complets** dépliables |
| **résultat** | ce que l'outil a renvoyé |
| **permission** | la demande et ta décision |
| **tour** | actions, durée, **jetons** consommés |
| **quota** | une ligne quand une fenêtre franchit 50, 75 ou 90 % |
| **micro** | chaque coupure et réouverture du micro |
| **arrêt** | quand tu arrêtes le travail depuis la page |
| **log** | les lignes de LiveKit et du reste |

En en-tête, à droite : le nombre d'actions, les jetons cumulés, et surtout
**le pourcentage des fenêtres de rate limit** — `5h 1 %` et `semaine 4 %`, avec l'heure de
réinitialisation au survol, et la pastille qui passe à l'ambre à 70 % puis au rouge à 90 %.
Pas de montant en dollars : sur un siège entreprise il ne veut rien dire, ce qui contraint
le travail c'est la fenêtre.

Ces pourcentages viennent de `/api/oauth/usage`, l'endpoint que le CLI utilise pour son
`/usage`. Il est **interne et non documenté** : il peut changer ou disparaître, donc en cas
d'échec l'affichage disparaît sans casser la session. À noter, sur un siège Team,
`seven_day_opus` est `null` — il n'y a pas de fenêtre Opus séparée, la fenêtre hebdomadaire
couvre tous les modèles. Elle s'affichera si le compte se met à la remonter.

**Le sélecteur de modèle** dans l'en-tête change le modèle de travail. Depuis la page le
choix est **durable** ; à la voix il est **temporaire** — « pour cette tâche » veut dire cette
tâche, et le modèle de base revient tout seul à la fin du tour (le sélecteur passe en ambre
pendant ce temps). Une bascule **invalide le cache de prompt**, donc le tour suivant relit la
conversation au tarif plein : c'est acceptable pour un choix explicite, et c'est la raison
pour laquelle ce n'est pas automatique par tâche.

L'effort (`xhigh`) reste fixe pour la session — le SDK expose `set_model` mais pas
d'équivalent pour l'effort. Le modèle plus léger est donc le levier pour une réponse rapide.

**Le bouton `⏹ arrêter`** (ou la touche **`s`**) interrompt le travail en cours. Il n'est
actif que quand il y a quelque chose à arrêter. Il existe pour une raison précise : micro
coupé, tu ne peux plus dire « stop » — la page doit donc porter l'arrêt.

À côté, une pastille **`⟳ travaille 42 s`** apparaît pendant le travail et compte. Elle
remplace la narration parlée : il n'annonce plus « toujours dessus, je viens de lancer
cat ». On ne survole pas du son, alors qu'un compteur se lit d'un coup d'œil et ne te coupe
pas la parole. Pour savoir où il en est sans regarder l'écran, « t'en es où ? » marche
toujours, à la demande.

Pour masquer la réflexion, un clic sur la pastille **réflexion** de la barre de filtres
suffit — comme pour n'importe quelle autre piste.

**Le bouton `🎤 micro`** coupe l'entrée audio, ou la touche **`m`**. Il devient rouge
(`🔇 micro coupé`) et l'agent ne t'entend plus du tout. Couper interrompt aussi ce qu'il est
en train de dire — sinon tu écouterais un débrief que tu ne peux plus interrompre. C'est le
bouton d'arrêt le plus rapide dont tu disposes, et il compte d'autant plus que rien ne
demande d'autorisation.

L'état appartient au serveur : le bouton *demande*, il n'agit pas. La page ne peut donc pas
afficher « coupé » alors que le flux tourne encore, et deux onglets restent synchronisés.

Les pistes se filtrent d'un clic ; `log`, `toi…` et `résultat` sont masquées par défaut.
L'historique est rejoué si tu ouvres la page en cours de session. Ça se coupe avec
`VOIX_UI_OUVRIR=0` (le serveur tourne quand même) ou se déplace avec `VOIX_UI_PORT`.

### Ce que le tableau montre en direct

Une action qui commence doit bouger, une action finie doit rendre un verdict. Sans ça les
lignes apparaissaient sans qu'on sache si elles tournaient encore, avaient abouti, ou
étaient mortes en silence.

- **En-tête** : les activités en cours, non filtrables — `transcription`, `réflexion`,
  `N outils`, `parole`, chacune avec un cercle qui tourne. C'est la réponse à « est-ce que
  quelque chose se passe, là, tout de suite ».
- **Par ligne** : `⟳` en cours, `✓` abouti, `✗` échoué, `–` interrompu. Le verdict des outils
  vient du SDK (`ToolUseBlock.id` ↔ `ToolResultBlock.tool_use_id` + `is_error`), pas d'une
  supposition.
- **En bas** : le bandeau de cogitation, un mot absurde qui tourne parmi quarante
  (*Emberlificotage*, *Déverminage*, *Chauffe-méninges*…). Aucun ne décrit ce qui se passe :
  le vrai avancement est dans le flux juste au-dessus, ce bandeau dit seulement que la
  machine n'est pas morte. Tirage par sac mélangé, donc les quarante passent avant qu'un
  seul revienne.

### L'en-tête, en zones

Une seule rangée en `flex-wrap` se repliait n'importe comment : la pastille « parole » sautait
à la ligne suivante et atterrissait **à gauche des filtres**. La position d'un élément
changeait selon la largeur de la fenêtre, et on ne savait plus quoi lire où.

Trois zones qui ne se coupent pas en deux — ce que tu **pilotes** (micro, arrêt, modèle,
effort), ce qui se **passe** (décompte, activité), et les **mesures** (actions, jetons,
quotas) — sur deux rangs : contrôles en haut, filtres et mesures en bas.

Les sélecteurs affichent un libellé court : `Opus 5` plutôt que `Opus 5 — le plus capable`.
Un `select` montre le libellé complet de l'option choisie, et 250 px pour dire
« très élevé — défaut » écrasait tout l'en-tête. La nuance reste, en infobulle.

La barre de saisie respire : **18 px** sous le champ plutôt que le bord de l'écran. Collée en
bas, elle donnait l'impression d'une fenêtre coupée — et sur un portable, la zone la plus
basse est celle qu'on atteint le moins bien.

### Les filtres, par famille

Dix-huit boutons alignés ne disaient ni ce qu'ils montraient ni pourquoi on voudrait les
couper : il fallait les avoir écrits pour s'en souvenir. Ils sont regroupés en quatre familles,
chacune une liste déroulante :

| Famille | Ce qu'on y trouve |
|---|---|
| **Conversation** | ce qui a été dit, d'un côté comme de l'autre |
| **Travail** | ce que Claude fait pendant qu'il travaille |
| **Commandes** | ce que tu pilotes, à la voix ou depuis la page |
| **Système** | l'état de la machinerie — utile quand quelque chose cloche |

Quatre genres sont masqués par défaut, parce qu'ils sont bruyants sans être informatifs :
`log` (journal technique), `partiel` (la transcription en cours), `resultat` (la sortie des
outils) et `micro` (son état change souvent et ne raconte rien de la conversation).

Chaque ligne porte **une phrase qui dit à quoi elle sert** (« la sortie des outils — souvent
longue »), donc le choix se fait sans documentation. Le compteur de la pastille (`4/5`) et son
style disent l'état de la famille sans l'ouvrir : pleine, partielle, ou entièrement coupée.
`tout` / `rien` évitent cinq clics pour couper une famille de cinq lignes.

**Une seule source de vérité.** Le libellé du badge, l'explication et le défaut sont déclarés
au même endroit. Une deuxième liste aurait dérivé de la première — et c'était déjà arrivé : le
genre `session` arrivait dans le flux sans figurer dans les libellés, donc sa ligne était créée
avec `display:none` et **aucun filtre ne pouvait la montrer**. Un test lit maintenant les deux
côtés dans la source (ce qui est publié, ce qui est déclaré) et refuse tout écart ; recopier la
liste dans le test n'aurait jamais vu l'oubli.

### La marque, et pourquoi elle ne bouge pas

Le logo est une **gerbe radiale à douze rayons effilés**, aux longueurs alternées. Même famille
visuelle que l'éclat de Claude, sans en être une copie — et les rayons inégaux donnent la
lecture « une voix qui rayonne », qui appartient à cette application. Elle sert dans l'en-tête
et comme favicon, en SVG inline, sans fichier à charger.

**Elle est immobile.** Une identité n'a pas de raison de tourner : le mouvement veut dire
« quelque chose se passe », et le dire en permanence, c'est ne plus rien dire. Faire tourner le
logo en continu le transformait en indicateur de chargement perpétuel, ce qui vidait de son
sens le seul endroit où le mouvement compte.

L'état du travail est donc porté par un élément **distinct** : un simple arc qui tourne, dans
le bandeau du bas, et par lui seul. Marque et état sont deux choses différentes ; les
confondre rendait les deux illisibles.

Le dessin a été jugé sur rendu, pas sur intention : une première version à huit traits
d'épaisseur constante s'effondrait en croix à grande taille et disparaissait à 17 px. Les
rayons effilés, plus nombreux et moins contrastés, tiennent sur fond sombre comme clair.

**Tout ce qui s'allume a un chemin garanti pour s'éteindre** : l'événement de résolution, la
fin de tour, une interruption, la perte de connexion, ou un garde-fou temporel. Un indicateur
bloqué mentirait dans le sens le plus coûteux — « ça travaille » alors que rien ne tourne.

La colonne de gauche donne l'**heure de l'horloge**, pas des secondes depuis le lancement :
`14:23:07` se recoupe avec un commit ou un souvenir, `812.4` ne se recoupe avec rien.
L'écoulé reste en infobulle, parce qu'il sert à mesurer une latence entre deux lignes.

### Ce qu'un tour a coûté

La ligne de bilan d'un tour porte les actions, la durée, les jetons, puis — une seconde plus
tard, quand la mesure arrive — de combien les fenêtres de quota ont bougé :

```
tour   3 actions · 42 s · 12.4 k jetons · 5h +1 pt (31 %) · semaine ±0 pt (11 %)
```

Deux choses à savoir, et l'affichage les assume au lieu de les masquer :

- **L'API renvoie des points entiers** (mesuré : `30.0`, `11.0`, jamais `30.4`). Un tour court
  affichera donc souvent `±0` : ce n'est pas une panne de mesure, c'est que le tour a coûté
  moins d'un point. Le zéro est affiché en gris plutôt que caché — savoir qu'un tour n'a rien
  coûté est une information.
- **La fenêtre est partagée.** Une autre session Claude Code ou l'extension VS Code consomme
  la même. Ce qui est mesuré est donc « de combien la fenêtre a bougé pendant ce tour », pas
  « ce que ce tour a coûté ». Le libellé dit `fenêtre`, et c'est délibéré.

La lecture n'est reprise que si la précédente a plus de 20 s, et toujours à travers le
garde-fou anti-429 : mieux vaut un écart non mesuré qu'un endpoint qui nous ferme la porte et
une en-tête vide pour le reste de la session.

### Écrire au lieu de parler

Une barre de saisie en bas de page. `/` met le focus, Entrée envoie, Échap rend le clavier
aux raccourcis.

Le message n'emprunte **pas** un second chemin : la commande appelle
`session.generate_reply(user_input=…, input_modality="text")`, ce qui l'injecte comme un tour
utilisateur et déclenche `llm_node`. Il traverse donc exactement la même suite qu'une phrase
entendue — réponse à une permission en attente, ordre local, envoi au worker, débrief parlé,
journalisation. Un chemin parallèle aurait fini par dériver de l'autre.

**Le champ suit son contenu.** C'est un `textarea`, pas un `input` : sur une seule ligne, une
longue dictée sortait du champ et on ne voyait plus ce qui était en train d'être reconnu —
donc plus moyen de le corriger, ce qui vide la fonction de son sens. Il grandit jusqu'à 40 %
de la hauteur de fenêtre, puis défile à l'intérieur : un champ qui mange l'écran cacherait le
flux qu'on est venu lire. Il redescend dès que le texte part.

Trois détails qui se remarquent seulement quand ils manquent :

- la barre est en `position:fixed`, donc **le flux recule** quand elle grandit — sinon elle
  recouvrirait précisément les dernières lignes qu'on veut voir en dictant ;
- la dictée écrit sans passer par `oninput`, donc l'ajustement est appelé explicitement, sinon
  le champ ne grandirait qu'en tapant, c'est-à-dire jamais pendant qu'on parle ;
- `height:auto` avant chaque mesure, sinon `scrollHeight` reste bloqué sur la hauteur
  précédente et le champ ne redescend jamais.

Entrée envoie, **Maj+Entrée** passe à la ligne : un `textarea` insère un retour par défaut et
ne déclenche pas le `submit` du formulaire, donc sans cela la touche la plus naturelle ne
ferait plus rien.

C'est **indépendant de l'état du micro** : micro coupé plus clavier donne un mode de travail
entièrement silencieux. La page n'affiche pas le message elle-même — c'est le serveur qui
republie `toi`, sinon un envoi refusé laisserait à l'écran un message jamais reçu.

### La dictée s'écrit dans la barre

Le texte reconnu s'écrit dans la barre de saisie à mesure qu'il arrive, en italique bleu tant
qu'il est provisoire. Un bouton `retenir` (touche `r`) décide de la suite :

| | |
|---|---|
| **retenir éteint** (défaut) | la dictée part chez Claude dès la fin de la fenêtre de silence |
| **retenir allumé** | la dictée reste dans la barre : tu relis, tu corriges une transcription ratée, tu envoies |

Le décompte change de libellé avec le mode — « envoi dans 2,7 s » ou « fin de dictée dans
2,7 s », et le bouton devient `terminer`. Annoncer un envoi qui n'aura pas lieu ferait douter
de tout l'affichage.

**Rien n'est jamais écrasé.** Le contenu de la barre est mémorisé au début de l'énoncé et le
texte reconnu s'ajoute à cette base : barre vide, la dictée la remplit ; correction déjà tapée,
la dictée s'ajoute à la suite. Taper reprend la main — l'événement `input` ne se déclenche que
pour une vraie frappe, donc ce que tu écris devient la nouvelle base. Et si un brouillon était
là avant que tu parles, il survit à l'envoi de la dictée.

La retenue est placée **après** les permissions et les ordres locaux, délibérément : « oui » à
une demande de permission et « coupe le micro » ne sont pas des tâches à relire, et les parquer
dans une boîte pour ensuite appuyer sur Entrée n'aurait aucun sens.

### Le décompte avant envoi

Le défaut de LiveKit committait le tour après **0,3 s** de silence : une pause pour rassembler
son idée comptait comme une fin de phrase, et la suite arrivait comme un second message
par-dessus le premier. C'est réglé à 4 s de plancher, 12 s de plafond.

Le mécanisme est binaire (vérifié dans `audio_recognition.py`) : le détecteur de fin de tour
donne une probabilité, et si elle passe sous son seuil c'est le **plafond** qui s'applique au
lieu du plancher. La fenêtre est donc entièrement déterminée, ce qui rend le décompte exact :

```
envoi dans 2.7 s │ envoyer          ← fenêtre normale
phrase inachevée — 6.3 s │ envoyer  ← le détecteur a prolongé
```

### Régler le délai, et rattraper un message

Un sélecteur à côté de la barre de saisie fixe **au bout de combien de silence le message
part** : 3, 5 et 10 s sont les repères, avec 2, 8, 15 et 20 s pour descendre plus court ou
monter plus long. Le changement est immédiat, par l'API publique `update_options()` — rien
n'est reconstruit.

Le **plafond suit le plancher** (× 2,5) au lieu d'être fixe. Sinon régler le plancher à 15 s
le placerait au-dessus d'un plafond de 12 s, et la fenêtre « phrase inachevée » deviendrait
plus *courte* que la fenêtre normale : un réglage qui se retourne contre celui qui le fait.
`VOIX_ECOUTE_MAX` permet d'imposer une valeur, jamais incohérente pour autant.

Une valeur venue de la configuration et absente des paliers reste proposée dans la liste :
le sélecteur doit afficher ce qui est réellement en vigueur, pas le palier le plus proche.

**Retenu d'office pendant une tâche.** Parler pendant que Claude travaille ne l'envoie plus :
le message se dépose dans la barre. Sans ça, le worker acceptait le second message, répondait
« noté, j'ajoute ça », et il partait sans qu'on ait rien relu — or c'est précisément le moment
où l'on parle pour *réagir* à ce qu'on voit passer, donc celui où une phrase mal transcrite
coûte le plus cher.

Ce qui continue de passer, délibérément : **les ordres locaux** (« arrête », « coupe le
micro ») et **les réponses à une demande de permission**. Ce sont des réactions, pas des
tâches à relire — et un « arrête » parqué dans une boîte serait dangereux. Trois tests
verrouillent ces exemptions.

Le bouton `retenir` passe en **pointillés orange** dès qu'une tâche démarre, avant que tu
parles : découvrir après coup que son message n'est pas parti est la pire façon de l'apprendre.
La ligne `retenu` du flux dit laquelle des deux raisons s'applique — ton réglage, ou le travail
en cours. `VOIX_RETENIR_OCCUPE=0` rétablit l'ancien comportement.

**Comprendre « retenir » sans documentation.** Un `ⓘ` à côté de la bascule ouvre une
explication de quatre paragraphes : ce que le mode fait, quand il sert, que les ordres
immédiats passent quand même, et que le décompte a son propre bouton. Il est **à côté** du
bouton, pas dedans : mis dedans, il aurait volé les clics destinés à la bascule, et
`retenir` est fait pour être basculé, pas pour être lu. Au survol elle apparaît, au clic elle
s'épingle, Échap ou un clic ailleurs la referme.

**Rattraper un message en vol.** Le décompte porte un bouton `retenir` : cliqué pendant la
fenêtre, il garde **ce** message dans la barre au lieu de l'envoyer. Il n'arme pas le mode —
sinon le tour suivant serait retenu sans qu'on l'ait demandé, une surprise dans le sens où
l'on croit avoir envoyé. Sans ça il faudrait armer `retenir` *avant* de parler, donc savoir à
l'avance qu'on allait se tromper.

Le libellé du décompte dit ce qui va réellement se passer — `envoi dans 2.7 s` ou
`retenu dans 2.7 s` — parce qu'annoncer un envoi qui n'aura pas lieu est le genre de faux qui
fait douter de tout l'affichage.

Le bouton `envoyer` (ou Entrée) appelle `commit_user_turn()`. Il ne raccourcit pas la fenêtre
pour les tours suivants : fenêtre longue par défaut pour réfléchir à voix haute, échappatoire
immédiate quand on n'en a pas besoin. Le prix à connaître : **chaque phrase attend 4 s, y
compris « stop »** — la parole de Claude s'interrompt toujours instantanément (le barge-in est
un mécanisme séparé), mais l'arrêt du travail met 4 s.

### Choisir le modèle et l'effort

Deux sélecteurs. Le modèle change à chaud (`set_model`). L'effort, non : le SDK n'expose
**pas** de `set_effort`, le niveau est figé à la construction du client. Le changer impose
d'en reconstruire un, en reprenant la session — Claude Code écrit ses sessions sur disque,
donc le contexte revient complet. D'où deux conséquences visibles :

- le sélecteur d'effort est **grisé pendant une tâche** : reconstruire en pleine tâche tuerait
  le travail en cours ;
- comme un changement de modèle, ça **invalide le cache de prompt**.

## Conversations : historique et reprise

À la reprise, **la page recharge la conversation**. Le tableau vit en mémoire du processus,
donc un nouveau processus partait de zéro : le contexte de Claude était complet, mais l'écran
était vide. On relit maintenant ce que Claude Code a écrit sur disque et on le republie.

**La forme compte autant que le contenu.** Une première version publiait chaque appel d'outil
comme sa propre ligne : sur soixante tours, 247 lignes de `Bash cat` et `Read x.py` qui
enterraient la conversation sous un mur de bruit. Ce n'est pas ce qu'on relit.

Un tour est donc rendu comme il se lit : ce que tu as dit, ce que Claude a répondu, puis **une
seule ligne** récapitulant ses actions (`12 actions · Read config.py, Bash npm test, …`).
Plusieurs blocs de texte d'un même message sont recollés en un paragraphe au lieu d'autant de
lignes. La réflexion et les sorties d'outils sont écartées : elles font le gros des huit cents
messages et ne se relisent pas.

Pour cette conversation de soixante tours, ça donne **182 lignes** — contre 382 pour la
première version, et 813 messages bruts.

Une différence à connaître : ce que Claude a dit **à voix haute** n'est pas rejoué. C'est une
réécriture produite par le porte-parole, qui ne vit pas dans la session Claude Code. Le texte
écrit porte la même substance, et le transcript Markdown garde la version parlée.

Les lignes du passé sont grisées et ne portent pas d'indicateur : un outil rejoué a fini il y
a des heures, et le compteur d'actions de la session en cours ne doit pas gonfler du passé.
Le rejeu tourne en tâche de fond — lire huit cents messages ne doit pas retarder le premier
mot — et il est plafonné à 400 lignes, en gardant les plus récentes et en disant combien ont
été écartées.

**La reprise dépend du répertoire.** Claude Code range ses sessions PAR dossier de travail :
reprendre depuis le mauvais dossier ne donne pas une erreur, ça donne une session introuvable,
donc une conversation qui repart de zéro en silence. `vvreprendre` résout donc le dossier
d'origine dans le fichier de session lui-même (champ `cwd`), et annonce le nombre de messages
rechargés avant de lancer :

```
  reprise de 89c0690c-… — 763 message(s) rechargés
```



Chaque conversation laisse deux choses, qui ne servent pas à la même chose :

- **un fichier Markdown** dans `conversations/`, pour la relire — c'est pour toi ;
- **l'identifiant de session Claude Code**, pour la reprendre — c'est pour la machine, et
  c'est ce qui rend le contexte complet plutôt qu'un résumé approximatif.

L'écriture est incrémentale : une coupure de courant ne perd que le tour en cours.

```fish
vvconv              # lister
vvreprendre 1       # reprendre (rang, identifiant de session, ou bout de nom de fichier)
vvlire 1            # relire le transcript
```

### La reprise recharge tout, et le vérifie

Claude Code écrit **l'intégralité** de ses sessions sur disque : une conversation de 59 tours
pèse 763 messages, et `resume=<session-id>` les rend tous. La reprise n'est donc pas un résumé
approximatif, c'est le contexte complet.

Un piège s'y cachait : **Claude Code range ses sessions par répertoire de travail.** Reprendre
depuis un autre dossier ne donne pas une erreur, ça donne une session introuvable — donc une
conversation qui repart de zéro en silence, ce qu'on croyait précisément avoir réparé.
`vvreprendre` résout maintenant le dossier d'origine en lisant le champ `cwd` du fichier de
session lui-même (la source d'autorité, et elle marche aussi pour les conversations antérieures
à l'enregistrement du chemin dans notre index), puis annonce ce qu'il recharge :

```
  reprise de 89c0690c-… — 763 message(s) rechargés
  projet  /home/…/dev/insnap
```

Si le compte est à zéro, il le dit avant de lancer : mieux vaut le savoir que de parler dix
minutes à une session amnésique. Le dossier reste forçable : `vvreprendre 1 /chemin/du/projet`.

`vvconv` classe **les conversations du dossier courant d'abord** : la question qu'on se pose
en tapant la commande est « qu'est-ce que j'ai fait dans CE projet », et un tri purement
chronologique les noie sous celles de tous les autres.

```
  ── ce dossier : /home/…/dev/claude-talk ──

   1. claude-talk              ● en cours
      début     21/08 18:36      activité  à l'instant  (pid 2455949)
      12 tour(s) · claude-opus-5
      /home/…/dev/claude-talk
      99999999-8888-7777-6666-555555555555
```

Trois états, et le troisième n'est pas un détail :

| | signification |
|---|---|
| `● en cours` | le processus de l'agent tourne encore |
| `○ fermée` | arrêtée proprement, avec l'heure de fermeture |
| `◍ interrompue` | tuée sans laisser de marqueur — sa dernière activité connue est la meilleure réponse disponible |

**L'autorité sur « est-elle ouverte » est le PID, pas un marqueur de fermeture** : un agent tué
par un `kill -9` n'écrit rien, et les deux jours de panne silencieuse étaient précisément deux
processus zombies. La vérification est double — le processus existe *et* sa ligne de commande
est bien celle d'un agent — parce qu'un PID libéré est réattribué : sans ce second test, une
conversation morte réapparaîtrait comme active dès qu'un navigateur hérite du numéro, et
l'avertissement deviendrait du bruit qu'on apprend à ignorer.

### Une seule session à la fois

Deux agents ne se cassent pas mutuellement — le tableau change de port tout seul — mais ils
**se disputent le micro et consomment la même fenêtre de quota**. C'est exactement ce qui
s'était produit : un zombie qui mangeait le quota sans que personne l'écoute.

Au lancement, `vv` avertit donc si une session tourne déjà, en nommant le PID, le projet et le
chemin — « pid 4123, 1,5 Go » ne permet pas de décider s'il faut le tuer, « pid 4123, projet
insnap » si. Le tableau affiche le même avertissement. C'est un **avertissement, pas un
blocage** : bloquer empêcherait aussi les cas légitimes. Pour nettoyer : `vvstop`.

Les conversations antérieures à l'enregistrement du chemin sont rapprochées du dossier courant
par le nom du projet, et l'affichage le dit — une déduction n'est pas présentée comme un fait.

## Secrets : rien de sensible dans le dépôt

Aucune clé n'est dans le dépôt, ni dans les fichiers suivis ni dans l'historique. Tout est lu
depuis l'environnement, chargé par `config.py` depuis un fichier **hors du dépôt** :

```
~/.config/claude-talk/secrets.env
```

```sh
AZURE_SPEECH_KEY=...
AZURE_SPEECH_REGION=francecentral
AZURE_SPEECH_VOICE=fr-FR-DeniseNeural
DEEPGRAM_API_KEY=...
```

Claude Code n'a **pas** de clé à gérer : il s'authentifie avec le jeton OAuth que la CLI écrit
dans `~/.claude/.credentials.json`, relu à chaque appel plutôt que mis en cache (il est
rafraîchi régulièrement).

Le `.gitignore` exclut par ailleurs `*.conf`, `.env`, `secrets.env`, `*.7z` et
**`conversations/`** — les transcripts citent le contenu des projets, ils n'ont rien à faire
sur GitHub.

Vérifier avant un push :

```fish
git ls-files -z | xargs -0 grep -nEi '(api[_-]?key|secret|token)[" ]*[:=]'
```

Point ouvert : les clés Azure et ElevenLabs ont vécu en clair dans `tts.7z`. L'archive est
exclue de git, mais elle est toujours sur le disque — **ces deux clés restent à faire tourner**.

## Permissions

Mesuré sur cette machine, en écrivant un fichier **dans** le projet, puis en lançant un
shell qui écrit **hors** du projet :

| `VOIX_PERMISSION` | dans le projet | hors du projet |
|---|---|---|
| `default` | demande | demande |
| `auto`, `dontAsk` | **refuse** — inutilisables ici | refuse |
| `acceptEdits` | passe en silence | demande à la voix |
| **`bypassPermissions`** (défaut) | passe | **exécute sans rien demander** |

Le défaut est `bypassPermissions` : autonomie totale, aucune question. Ça veut dire que la
demande vocale « je le fais ? » **n'arrive jamais**, et que le tableau de bord est le seul
point de contrôle qui reste — d'où le panneau de configuration en tête de page et le badge
rouge dans l'en-tête, qui le rappellent à chaque session.

Souviens-toi que les instructions arrivent par **transcription vocale** : une phrase mal
comprise devient un shell arbitraire. Si tu veux récupérer une frontière au bord du projet
sans revenir aux questions incessantes : `set -x VOIX_PERMISSION acceptEdits`.

## Ordres locaux (latence nulle, jamais transmis à Claude)

| Intention | Ce que ça fait | Exemples reconnus |
|---|---|---|
| `micro` | coupe l'entrée audio et te le confirme | « coupe le micro », « tu peux couper le micro s'il te plaît », « éteins le microphone », « arrête d'écouter », « mute » |
| `silence` | se taire **sans** arrêter le travail | « chut », « tais-toi », « arrête de parler », « coupe ta voix » |
| `arret` | interrompt le travail en cours | « stop », « arrête tout », « laisse tomber », « oublie ça » |
| `statut` | résume le journal, sans modèle | « t'en es où ? », « ça avance ? », « il reste combien » |
| `repete` | redit le dernier débrief, sans le régénérer | « répète », « pardon ? », « j'ai pas compris » |
| `quota` | dit les fenêtres à voix haute | « quota », « j'ai consommé combien de tokens », « où en est la fenêtre » |
| `modele` | bascule de modèle **pour ce tour seulement** | « utilise un modèle plus rapide pour cette tâche », « passe en haiku », « reviens au modèle normal » |

**Ce n'est pas une liste de formulations à apprendre.** La détection ([`intentions.py`](voix/intentions.py))
cherche la **co-occurrence d'un verbe et d'un objet** de la même intention, dans n'importe
quel ordre et n'importe quelle conjugaison — donc « coupe le micro », « couper le micro »,
« le micro, coupe-le » marchent tous. Une regex de séquence ne couvrait que les phrases
prévues : c'est pour ça qu'elle ratait « tu peux couper le micro s'il te plaît ».

Deux garde-fous, parce que le piège est réel et pas théorique. **« ajoute un bouton pour
couper le micro dans le front »** est une phrase réellement dite à ce projet, et elle
contient les mots de l'ordre. Un ordre doit donc aussi *ressembler* à un ordre :

- **au plus 12 mots** — au-delà, c'est une tâche qu'on décrit, pas un ordre qu'on donne ;
- **aucun vocabulaire de tâche** (`ajoute`, `crée`, `corrige`, `fichier`, `fonction`,
  `bouton`, `code`, `front`…).

Tout ce qui est plus long ou qui parle de code part chez Claude, ce qui est le défaut sûr.
Chaque détection est publiée sur la piste **ordre local** avec sa raison (`« coupe » +
« micro »`), pour qu'un détournement erroné se voie comme tel au lieu de passer pour un
comportement bizarre de Claude.

Couverture testée : `python voix/test_intentions.py` — **49 formulations reconnues, 10
instructions de code préservées**.

> ⚠️ **Micro coupé, tu ne peux plus le rouvrir à la voix** — il ne t'entend plus. La
> confirmation te le rappelle : bouton, ou touche `m`.

## Tests

```fish
cd voix
../.venv/bin/python test_noyau.py         # la boucle, sans micro
../.venv/bin/python test_intentions.py    # les ordres locaux, et ce qui ne doit PAS en etre
../.venv/bin/python test_conversation.py  # journal, etats, reprise
../.venv/bin/python test_tableau.py       # invariants du serveur du tableau
node test_front.js                        # le front, sans navigateur
node test_rendu.js                        # la mise en page, dans Chrome
```

`test_rendu.js` rend la page dans Chrome et mesure la géométrie réelle. Il existe parce que
`test_front.js` stube le DOM et ne mesure donc aucune mise en page : il a laissé passer un
bug où le corps des messages tombait dans une colonne de 14 px. Sans Chrome installé, il
s'abstient au lieu d'échouer.

`test_front.js` extrait le `<script>` de `tableau.py`, lui donne un DOM minimal et vérifie les
invariants qui, quand ils cassent, **mentent à l'utilisateur** : un indicateur qui tourne
alors que rien ne tourne, un mot figé qui fait croire à un blocage, un décompte qui affiche
un zéro coincé, une dictée qui écrase une correction tapée à la main.

Il existe parce que `node --check` ne suffit pas : il valide la syntaxe, pas l'exécution. Il
n'avait pas vu qu'un `addEventListener` placé avant la déclaration de sa variable tuait tout
le script au chargement — page blanche, sans rien dans le flux pour l'expliquer. Ce harnais a
trouvé quatre bugs réels que la relecture n'avait pas vus.

### Trois lignes « toi » pour un message, puis trois copies de la session

Deux causes distinctes, souvent confondues parce que le symptôme se ressemble.

**Le message dupliqué dans le flux** venait de la publication depuis l'événement de
transcription : les transcriptions finales tombent plusieurs fois par tour dès qu'on marque
une pause — avec une fenêtre de 4 s, c'est la règle. La ligne est maintenant publiée par
`llm_node`, seul endroit qui connaisse le texte consolidé du tour.

**La session entière dupliquée** — panneau de configuration compris — venait du rejeu
d'historique : à chaque reconnexion, le serveur renvoie toute l'histoire, et la page
l'ajoutait à ce qu'elle affichait déjà. Trois reconnexions, trois copies. Chaque événement
porte désormais un **numéro croissant**, et la page ignore ce qu'elle a déjà rendu. Ça couvre
aussi le cas de deux sockets vivantes en même temps — et `brancher()` ferme l'ancienne avant
d'en ouvrir une nouvelle, en ignorant le `onclose` d'une socket périmée.

### « toi » signifie « pris en compte », et rien d'autre

En mode *retenu*, la ligne `toi` était publiée **avant** le test du mode : le flux affichait
donc un message parti alors qu'il attendait dans la barre. En relisant les logs, impossible de
savoir ce qui avait réellement été envoyé.

La ligne est maintenant publiée par chaque branche qui **consomme** l'énoncé — réponse à une
permission, ordre local, envoi au worker — et jamais en amont. Une dictée retenue ne produit
qu'une ligne `dictée`.

### « Un caractère par ligne » — display:none dans une grille

L'historique rejoué était illisible : chaque message s'affichait sur une colonne d'un
caractère de large, sur des lignes de 7 400 px de haut.

La cause tient en une ligne de CSS : `.ev.passe .pip{display:none}`, ajoutée pour masquer
l'indicateur des lignes du passé. Dans une grille, **`display:none` ne masque pas — il retire
l'élément du flux**. Le corps du message glissait donc dans la colonne de 14 px prévue pour
l'indicateur, et 563 caractères dans 14 px donnent exactement ce qui était décrit.

La règle était inutile : un indicateur sans état n'a ni bordure ni contenu, il est déjà
invisible. Il suffit de le laisser occuper sa cellule.

Deux leçons appliquées :

- **Le stub ne voit pas la mise en page.** `test_rendu.js` mesure maintenant dans Chrome, et
  vérifie que la colonne de texte fait plus de 400 px et qu'aucune ligne ne dépasse 300 px de
  haut. Le bug a été réintroduit pour confirmer que le test le rattrape : il le rattrape.
- **L'opacité du passé était trop basse.** À 0,55 le texte de Claude tombait à 3,8:1 de
  contraste, sous le minimum lisible ; à 0,7 il est à 5,4:1.

### Le clic avalé — la vraie cause du « bouton qui ne marche pas du premier coup »

Le symptôme a survécu aux corrections serveur, parce que la cause était côté page :

```js
if (socket && socket.readyState === WebSocket.OPEN) { socket.send(...) }   // sinon : rien
```

Une socket morte — après une veille du portable, un onglet longtemps en arrière-plan, un
redémarrage de l'agent — faisait **disparaître le clic en silence**. La page ne découvrait la
coupure qu'à ce moment, affichait `reconnexion…`, et le clic suivant passait.

Ce qui a été fait :

- **Une file.** Une commande qui ne peut pas partir attend la reconnexion au lieu d'être
  perdue. Le dernier état voulu gagne : trois clics hors ligne ne rejouent pas trois bascules
  (ce qui ramènerait à l'état de départ) — sauf pour `texte`, où deux messages écrits sont
  deux messages et non un état à écraser.
- **Un retour immédiat.** Le bouton micro pulse en attente. C'est une attente affichée, pas
  une vérité affirmée : l'état réel reste celui du serveur, et l'attente se lève à sa réponse.
- **Une détection en amont.** La page se rebranche sur `visibilitychange` et `online`, au lieu
  de laisser le prochain clic faire la découverte.

Toutes les commandes de la page passent par cette file — micro, arrêt, modèle, effort,
retenir, envoi immédiat, message écrit.

**Comment la cause a été trouvée.** Trois hypothèses ont été éliminées par mesure, pas par
raisonnement : l'exception dans une commande (le harnais montre qu'elle est désormais isolée),
le détachement audio du mode console (`on_detached()` ne pose qu'un drapeau, il ne bloque
rien), et un état d'agent non traduit (`idle` n'est jamais émis par LiveKit). Puis l'agent en
cours a été observé en direct sur sa WebSocket : socket stable 40 s, aucune erreur publiée, et
six événements `micro` alternant correctement. Le serveur était donc innocent, et il ne
restait que la page.

### Le bug du « micro coupé qui ne coupe pas »

Symptôme : cliquer *micro coupé* ne coupait parfois rien, l'état passait en `déconnecté`, et
il fallait recliquer.

Trois défauts empilés, tous corrigés et tous couverts par `test_tableau.py` :

1. **`await self.on_commande(...)` n'était pas protégé.** Une exception dans une commande
   sortait de la boucle `async for` du WebSocket, passait par le `finally` qui retire le
   client, et tuait la connexion. N'importe quel bouton pouvait donc déconnecter la page.
2. **Ce qui levait** : `Agent.session` passe par `_get_activity_or_raise()`, qui lève dès que
   l'activité est momentanément absente. Or le tableau appelle `couper_micro()` depuis
   l'*extérieur* d'un tour de parole. D'où l'échec intermittent — et d'où le fait que seul
   *couper* échouait, puisque *rouvrir* passait par l'objet session capturé dans l'entrypoint.
   `Voix.sess` garde une référence directe, avec repli sur le comportement d'origine.
3. **L'état restait faux après l'échec.** Le bouton gardant l'ancienne valeur, le clic suivant
   renvoyait la même demande — que `set_audio_enabled` traite comme un non-événement, puisqu'il
   commence par « si c'est déjà cet état, ne rien faire ». L'état réel est maintenant republié
   même en cas d'échec.

Au passage : la page affiche `reconnexion…` pendant qu'elle se rebranche, et ne dit
`déconnecté` qu'après trois échecs d'affilée. Une coupure d'une seconde n'est pas une panne,
et l'annoncer comme telle donnait l'impression d'un bug là où il n'y avait qu'un aller-retour.



```sh
.venv/bin/python voix/test_noyau.py    # worker + porte-parole, sans audio ni LiveKit
.venv/bin/python voix/test_boucle.py   # boucle complète via AgentSession.run(), sans micro
```

## « Hey Claude » — désactivé

**La fonctionnalité est arrêtée**, et rien ne peut plus la lancer :

- l'unité systemd est renommée `hey-claude.service.desactive` — `systemctl` ne la trouve plus ;
- les raccourcis i3 (`$mod+Shift+s` et `Pause`) sont commentés ;
- aucune entrée d'autostart, aucun processus en cours.

Pourquoi elle *semblait* encore active : `toggle-claude.sh` ne démarre rien, il ne fait que
basculer un fichier drapeau et afficher une notification. Heurter **`Pause`** — une touche
facile à toucher par accident — affichait donc « écoute Claude RÉACTIVÉE » alors que le
service était mort depuis des jours. La notification mentait sur un état qu'elle ne vérifiait
pas.

Les fonctions fish `vhey*` existent toujours : elles demandent une frappe volontaire, ce qui
n'est pas le problème. Pour réactiver : renommer l'unité, `systemctl --user enable --now
hey-claude.service`, décommenter les deux lignes i3.

La documentation qui suit décrit ce que la fonctionnalité faisait, et reste valable si tu la
rallumes un jour.

## « Hey Claude » — ce que l'assistant d'arrière-plan faisait

Processus séparé, session Claude séparée, modèle léger. Il ne touche pas à ce que fait `vv`.

**C'est une conversation, pas une question isolée.** « Hey Claude » l'ouvre ; ensuite le mot
de réveil n'est plus nécessaire, chaque phrase est un tour, et **le contexte est conservé** —
« et son volume ? » juste après une question sur la Terre répond bien sur la Terre.

Trois façons de la fermer :

| | |
|---|---|
| **à la voix** | « fin », « fin de conversation », « termine », « c'est bon », « c'est tout », « merci », « au revoir », « à plus », « stop » |
| **au clic** | la notification « Conversation en cours » reste affichée tant que la conversation est ouverte ; **n'importe quel clic dessus** la ferme |
| **au silence** | 90 s sans rien dire (`VOIX_EVEIL_INACTIVITE`) — sinon chaque phrase d'ambiance deviendrait un tour facturé |

Les formulations de sortie ne sont reconnues que **courtes et quasi seules** dans la phrase :
« à la fin du fichier il y a une erreur » ne raccroche pas, « termine la fonction » non plus.

À la fermeture, le contexte est remis à zéro (nouveau client construit, puis bascule, puis
abandon de l'ancien — pour qu'aucun tour n'arrive sur une session morte). Les questions
d'aujourd'hui ne traîneront pas dans celles de demain.

> **Le plafond limite la longueur des conversations.** Un tour coûte ~1 centime, dont 90 % de
> synthèse vocale. À 10 centimes/jour, ça fait **une dizaine de tours**. Avec tes voix Piper
> locales, la synthèse devient gratuite et on passe à ~120 tours pour le même plafond.

**Il tourne déjà** : service systemd utilisateur `hey-claude.service`, activé au démarrage.

```fish
vheystatus              # service, plafond du jour, état de l'écoute
vheylog                 # suivre le journal en direct
vheytoggle              # couper / rouvrir l'écoute  (= $mod + touche micro, ou Pause)
vheydiag                # régler le seuil du verrou haut-parleurs
vheytest question.wav   # rejouer un WAV 16 kHz mono dans toute la chaîne
```

Mesures au repos : **320 Mo de RAM, 4,5 % d'un cœur**. L'essentiel de la RAM est le
sous-processus `claude` maintenu chaud pour que la première question ne paie pas les ~3 s de
démarrage. Le connecter à la demande rendrait ~200 Mo, au prix d'une première question plus
lente — dis-le si la RAM compte plus que ces trois secondes.

### Couper l'écoute — uniquement celle de Claude

**`$mod` + la touche micro du clavier**, ou **`Pause`**, ou `vheytoggle`.

Le choix n'est pas arbitraire : la touche micro **seule** était déjà liée au mute système
(`pactl set-source-mute`), et ça arrête Claude par ricochet puisque c'est son premier verrou.
La même touche **avec `$mod`** ne coupe que Claude. Une paire cohérente sur une seule touche
physique.

(`$mod+Shift+m` était mon premier choix et c'était une erreur : i3 l'utilise pour
`move right`, il refusait le doublon et gardait le sien.) Ça pose `~/.config/claude-talk/ecoute-coupee` et affiche
une notification **persistante** « écoute Claude COUPÉE ». Le **micro système reste actif** :
tes visios et tes enregistrements continuent normalement, c'est bien Claude qu'on coupe. Un
fichier plutôt qu'un signal, pour que l'état survive à un redémarrage du service.

### La notification d'écoute

À chaque segment envoyé à Azure, une notification courte apparaît : « écoute en cours —
2,3 s envoyées ». Si ce n'était pas un appel, elle est remplacée par « ignoré : « … » » avec
le texte transcrit. Tu sais donc **quand** du son quitte la machine et **ce** qui en a été
compris, même quand le contenu n'a aucune importance.

Sous i3 avec dunst et aucun `dunstrc` personnel, les notifications s'affichent **en haut à
droite** — c'est un réglage de dunst (`origin`), pas de l'application.

### Ce qui protège la facture

Azure STT coûte **1 $ par heure d'audio** : écouter en continu ferait ~240 $/mois. Un segment
n'atteint Azure que s'il franchit cinq verrous, tous gratuits :

1. le **micro système** n'est pas coupé (`pactl`) ;
2. les **haut-parleurs ne jouent rien** — sinon on transcrirait les vidéos et les visios,
   la plus grosse et la plus inutile part de la facture. Lu sur le monitor du sink, avec
   1,5 s de rémanence pour la fin d'une vidéo ;
3. le **VAD local** (Silero) y a entendu de la parole ;
4. la **durée est plausible** (0,6 s à 15 s — au-delà c'est une conversation) ;
5. le **plafond quotidien** n'est pas atteint (20 min par défaut, soit ~0,33 $ au pire).

Le mot de réveil est cherché **après** la transcription, sur le mot « Claude » dans les trois
premiers mots. Azure francise l'anglais — « Hey Claude » revient en « Aie Claude », « Et
Claude », parfois « Dix Claude » — donc chercher l'interjection est perdu d'avance. Une phrase
qui mentionne Claude plus loin (« j'ai vu que Claude avait sorti un modèle ») ne déclenche pas.

### La notification

Impossible d'être surpris : dès la détection, une notification **d'urgence critique** s'affiche
et **reste** — « détecté, je transcris… », puis ta question et « je cherche la réponse… ». Elle
n'est remplacée par la réponse, avec un délai d'effacement, qu'une fois celle-ci prête.

### Réglages

| Variable | Défaut | Rôle |
|---|---|---|
| `VOIX_EVEIL_MODELE` | `claude-haiku-4-5` | mesuré à **3,2 s** du segment à la réponse |
| `VOIX_EVEIL_PLAFOND` | `6` | minutes/jour envoyées à Azure — **6 min = 10 centimes/jour** au pire. 1 min = 1,7 ct, 3 min = 5 ct, 20 min = 33 ct |
| `VOIX_EVEIL_SEUIL` | `400` | RMS du monitor au-delà duquel on se tait — **règle-le avec `vheydiag`** |
| `VOIX_EVEIL_MAX` | `15` | durée max d'un segment, en secondes |

### Savoir ce que ça coûte

```fish
vheycout
```

Cumuls sur le jour, 7 jours, 30 jours et depuis le début. Les jours écoulés sont **archivés**
dans `~/.config/claude-talk/assistant.json` au lieu d'être écrasés, donc l'historique se
construit à partir de maintenant.

> **C'est une estimation, pas un relevé.** Elle multiplie l'usage mesuré localement par les
> tarifs de `TARIF_STT_H` et `TARIF_TTS_M`, dont j'ai pris le second volontairement haut.
> La clé Speech authentifie l'**usage**, pas la **facturation** : les coûts réels vivent sur
> `management.azure.com` et exigent un jeton de compte Azure, pas la clé du service.
>
> Le chiffre qui fait foi : **portal.azure.com → Cost Management → Cost analysis**, filtré sur
> la ressource Speech. En ligne de commande, `az login` puis
> `az consumption usage list --start-date … --end-date …`.

> ⚠️ **Au bureau, réfléchis avant.** Même avec les verrous, tout ce qui est reconnu comme de
> la parole part chez Microsoft. Dans une boîte qui fait de l'analyse de binaires, ça se
> discute — et le plafond limite la facture, pas l'exposition.

## Quand Azure lâche

Le palier gratuit F0 donne **5 h de transcription par mois**, et une fois épuisées Azure
répond `400 Quota exceeded` sur **chaque** requête. C'est arrivé en pleine session : une
erreur toutes les quinze secondes dans le tableau, plus rien d'entendu, et aucun filet.

La chaîne est maintenant à trois niveaux, assemblée par `config.chaine_stt()` — une seule
fonction décide, et l'agent comme le panneau de démarrage la lisent, pour que le tableau ne
puisse pas afficher une configuration pendant que l'agent tourne sur une autre :

```
azure → deepgram → local
```

L'ordre n'est pas arbitraire. **Azure et Deepgram sont deux pairs** : les deux font du vrai
streaming avec résultats intermédiaires, autour de 300 ms, donc la bascule de l'un à l'autre
ne s'entend pas. Deepgram `nova-3` accepte le `keyterm prompting`, l'équivalent exact de la
`phrase_list` d'Azure — le vocabulaire du projet survit donc à la bascule, et « MQL » ne
redevient pas « kubedka » juste parce qu'Azure a manqué de crédit.

**Le moteur local est en dernier**, parce que lui s'entend : 4 à 5 s par phrase. Il reste
dans la chaîne pour le jour où les deux services sont coupés ensemble — lent vaut
infiniment mieux que sourd. Une clé absente retire simplement son moteur de la chaîne au
lieu de la faire échouer au premier mot.

Deux autres corrections :

- **Les bascules s'annoncent.** `FallbackAdapter` émet `stt_availability_changed` : le
  tableau dit quel moteur est tombé et lequel prend le relais. Il retente aussi en arrière-
  plan, donc au renouvellement du quota mensuel Azure reprend sa place tout seul.
- **Les erreurs sont résumées une fois.** Le vrai message ne se perd plus dans la répétition,
  et il nomme le suivant réel dans la chaîne, pas un repli supposé.

Le modèle local est **préchargé en tâche de fond** au démarrage, sinon la première phrase
après la bascule paierait aussi les 3 à 5 s de chargement.

Ce que le local vaut, mesuré sur ce portable (i5-1335U, sans GPU, int8) :

| modèle | 2 s d'audio | qualité |
|---|---|---|
| `base` | 1,5-1,9 s | moyenne — perd des mots |
| **`small`** (défaut) | 4,2-4,9 s | correcte, garde `config.py` et `MQL` |

C'est plus lent qu'Azure et il faut l'assumer. En échange : aucun quota, aucune facture, et
**l'audio ne quitte pas la machine** — ce qui pour du code d'entreprise n'est pas un détail.
Le vocabulaire de `config.PHRASE_LIST` est passé au modèle en `initial_prompt`, l'équivalent
de la phrase list d'Azure.

Note : les quotas STT et TTS sont **séparés**. Azure a continué à parler alors qu'il
n'entendait plus rien.

## Ce qui reste à faire

- **Chemin 100 % local** (`VOIX_STT=local`, `VOIX_TTS=local`) : `faster-whisper` pour le
  français, les voix Piper `fr_FR-*` déjà présentes dans `tts/voices/` pour la synthèse.
  Nécessaire dès qu'on parle d'un code qui ne doit pas partir chez un tiers.
- **Jamais testé avec un vrai micro** : tout ce qui précède est validé en texte et en
  headless. La première session au micro reste à faire, et c'est là que se jugera la
  qualité réelle de l'AEC sur les haut-parleurs de ce portable.
- **Rotation des clés Azure et ElevenLabs** : elles ont vécu en clair dans `tts.7z`. L'archive
  est exclue de git, mais elle est toujours sur le disque.
- **Azure S0** : le palier F0 plafonne la transcription à 5 h/mois. Passer en S0 lève le mur
  (~2,50 $/mois à cet usage) mais fait aussi payer le TTS, gratuit jusqu'à 500 k caractères en
  F0 — soit une dizaine de dollars par mois, plus que le STT. D'où la priorité de Piper.
- **Fichiers non suivis par git** : `voix/journal.py`, `voix/intentions.py`,
  `voix/stt_local.py`, `voix/assistant.py` et les tests. Le dépôt est incomplet sans eux.
- `tts/` est l'ancien système, gardé le temps de la transition. À supprimer ensuite.
- Suivre la dépréciation du mode console de LiveKit (migration vers `lk agent console`).
