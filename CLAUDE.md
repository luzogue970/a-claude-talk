# claude-talk — conventions de travail


## Versions : une tâche finie monte le numéro, et la montée atteint l'app

Une carte du tableau de bord qui affiche « sans version » ne peut pas dire si l'app
qu'on tient dans la main est la dernière. C'est tout ce qu'on demande à un numéro.
Avant le 4 octobre 2026, trois projets sur sept n'en avaient aucun, et aucun des
sept n'avait jamais été étiqueté : Halo affichait 0.9.1 avec quatre-vingt-un commits
depuis, et le paquet servi au téléphone avait onze jours.

**À chaque tâche aboutie, après le commit :**

```
version-projet fix     # une correction
version-projet min     # une nouveauté
version-projet maj     # une rupture
git push --follow-tags
```

`version-projet` écrit `VERSION`, propage le numéro dans les `package.json` et
`app.json`, régénère le `CHANGELOG` quand le projet sait le faire, commite et pose
une **étiquette Git `vX.Y.Z`**. C'est l'étiquette qui fait foi : un fichier dit ce
qu'on a écrit, une étiquette dit *quand on l'a sorti*.

**Puis la montée doit atteindre l'app.** Un numéro qui monte sans que l'application
change n'a rien réglé — c'était exactement le défaut. Depuis le tableau de bord,
onglet Monitoring, le bouton **« Mettre à jour »** de la carte reconstruit le paquet,
redémarre le service qui le sert, et relance le serveur Expo Go pour que le téléphone
reçoive le nouveau code. La carte affiche ensuite la version, sa date, et le nombre de
commits écrits depuis — en orange quand il y en a. Zéro commit depuis, c'est que ce
qui tourne est bien ce qui est écrit.

## La commande unique des tests : `./tests.py`

La règle TU/TI/TF est posée globalement. Ici, elle se lance d'une seule façon :

```
./tests.py              # tout, y compris les suites lentes
./tests.py --rapide     # sans ce qui appelle Azure ou le réseau
./tests.py -k micro     # une suite ou un motif
./tests.py --liste      # ce qui existe, et ce que chaque suite protège
```

**Toute suite nouvelle s'inscrit dans la liste `SUITES` de `tests.py`.** Quatre suites ont
déjà vécu hors de cette liste : elles passaient, et ne protégeaient rien.

Le dernier champ du tuple dit si la suite est **lente** (réseau, API payante) — elle sort
alors du passage rapide, jamais du passage complet.

## Les réglages généraux : `reglages.json`, et l'environnement garde le dernier mot

Chaque réglage avait sa variable d'environnement, posée au lancement par un script
shell du dépôt *tableau*. Changer le modèle par défaut demandait d'éditer ce script,
puis de relancer chaque conversation.

`voix/reglages.py` lit `~/.config/claude-talk/reglages.json` au tout début de
`config.py`, juste après les secrets et avant la première lecture d'environnement.
Le tableau de bord l'écrit depuis son onglet Dev.

- **`os.environ.setdefault`**, même règle que les secrets : une variable posée à la
  main gagne. Une variable **vide** ne gagne pas — c'est un oubli, et `float("")`
  faisait tomber l'import sans nommer ni la variable ni le script fautif.
- Le fichier stocke **l'identifiant du modèle** (`claude-opus-5`), pas la clé courte,
  parce que c'est ce que `VOIX_WORKER_MODEL` attend. Embarquer la table des modèles
  dans `reglages.py` l'aurait mise en double de `config.MODELES` ;
  `config.cle_du_modele()` fait la traduction là où on en a besoin — et la commande
  `modele` accepte donc les deux formes.
- **`POST /commande`** sur le serveur de l'agent rejoue le chemin de la WebSocket,
  garde-fou de `_executer` compris. C'est par là que le tableau change le modèle ou
  l'effort d'une conversation déjà ouverte, sans qu'elle ait rien à relancer.

## Le micro du téléphone après un aller-retour : une famille de pannes, pas une

« Je dicte, je quitte l'application, je reviens, et le micro ne répond plus. » Signalé
encore le 19 octobre 2026, après les trois portes déjà posées (sortie propre sur
`visibilitychange`, garde-temps sur `stop()`, nettoyage au retour). La raison : ce n'était
pas **une** cause. Chacune de celles-ci suffisait seule à rendre le bouton inerte, et aucune
ne disait rien à l'écran.

- **Une ouverture encore en vol au départ.** `getUserMedia` résolvait après qu'on avait
  quitté la page : soit un enregistrement démarrait dans une page que personne n'avait
  sollicitée, soit le flux restait ouvert sans preneur — et iOS rend muets tous les micros
  suivants tant qu'un flux traîne. Chaque ouverture porte maintenant un **numéro de
  génération** ; partir l'incrémente, et ce qui se résout après coup rend son flux.
- **Une piste rendue `muted` à l'ouverture.** iOS coupe le micro une demi-seconde au retour,
  le temps de remettre sa session audio d'aplomb. C'était refusé sur-le-champ par
  « micro occupé par une autre application », à quelqu'un qui n'en avait ouvert aucune. On
  attend `DELAI_DEMUTE` (1,5 s) avant de juger.
- **`getUserMedia` qui ne répond ni oui ni non.** Connu sur iOS après un changement
  d'application. Le bouton restait inerte, sans un mot, et chaque appui relançait une
  demande qui ne reviendrait pas. Borné à `DELAI_OUVERTURE_MICRO` (6 s).
- **`MediaRecorder.start()` qui jette.** Il était appelé **hors de tout `try`** : le flux
  restait ouvert, donc le micro pris, et l'appui suivant refaisait exactement la même chose.
- **Un contexte audio « interrupted ».** WebKit laisse le contexte de mesure arrêté après un
  passage en arrière-plan ; tous ses échantillons valent alors 128, le silence parfait. La
  page en concluait « rien n'a été capté » et **jetait un enregistrement parfaitement bon**.
  Ne pas savoir mesurer n'est pas un silence : la mesure ne compte plus, et on tente
  `resume()`.
- **Le contexte des sons, lui aussi.** `reveillerSons()` ne reprenait que l'état
  « suspended », jamais « interrupted » : le petit son qui confirme l'enregistrement ne
  partait plus — et c'est lui qu'on écoute au lieu de regarder l'écran. Le contexte est
  désormais **rendu au départ** et recréé neuf dans le geste suivant.
- **Une liaison qui revient.** Au retour, la socket se rebranche en un dixième de seconde, et
  l'appui arrive souvent avant. On attend `DELAI_ATTENTE_LIAISON` (3 s) en le disant, au lieu
  de refuser. Pendant qu'on parle, une coupure est tolérée `DELAI_TOLERANCE_LIAISON` (2 s) :
  l'audio part en HTTP de toute façon, et couper quelqu'un à sa troisième seconde pour une
  liaison déjà revenue est le pire service à lui rendre.
- **Un envoi qui ne revient jamais.** Le bouton restait sur « transcription en cours » pour
  toujours, et l'appui suivant **annulait** au lieu d'enregistrer. Borné à deux minutes ;
  au-delà c'est un échec de transport, donc la parole est gardée.

**Le dernier filet, et il compte.** Deux ouvertures ratées d'affilée — hors refus
d'autorisation, qui n'est pas une panne de la page — veulent dire que l'état de capture est
cassé et qu'aucun appel JavaScript ne le réparera. La page **se recharge alors toute seule**,
en l'annonçant. Jamais s'il y a quelque chose à perdre : un brouillon dans la barre, un vocal
qui attend, un envoi en vol. Dans ce cas elle dit de le faire à la main, et ce que ça
coûterait.

**Pourquoi on ne rouvre pas le micro au retour sur la page.** Ce serait la réponse évidente,
et elle est mauvaise : iOS redemande l'autorisation à chaque ouverture hors d'un geste, et
tenir un flux ouvert au repos est précisément ce qui produit les pistes muettes ci-dessus.
Le micro s'ouvre sur l'appui, et l'appui seul.

**La page raconte son micro.** Chaque étape part à l'agent par `{cmd: "trace"}` et atterrit
dans son journal, donc dans le filtre « log » de la page. Quand « le micro ne marche plus »
arrive sur un téléphone, personne n'est devant la console : sans ce récit, on ne sait ni si
le micro a été demandé, ni ce qu'il a rendu, ni où ça s'est arrêté. Ce n'est **pas** une
commande — l'agent ne la voit jamais passer. Une session lancée avant cette version ignore
le mot sans broncher.

Trois niveaux de tests, et le troisième a trouvé un défaut que les deux premiers ne
pouvaient pas voir : `test_front_cas.js` (les décisions de la page, micro et réseau doublés),
`voix/test_micro_retour.py` (le contrat trace/journal contre un vrai serveur), et
`voix/test_micro_navigateur.py` — le parcours entier dans Chrome, micro factice, `ffmpeg`
réel : je dicte, je quitte, je reviens, je redicte, et le PC reçoit les deux.

**Deux pièges du harnais, chacun coûte une demi-heure.** Chrome sans fenêtre attend le
trousseau du bureau avant sa **première navigation** : sur une machine sans session
graphique, il attend pour toujours, la requête n'atteint même pas le serveur, et ça ressemble
à une panne réseau. D'où `--password-store=basic --use-mock-keychain`. Et un
`visibilitychange` fabriqué sans `bubbles: true` ne quitte jamais le document : la page
écoute sur la fenêtre, donc elle ne le voit pas, et le test croit à tort qu'elle ne réagit
pas.

## L'historique de la page n'attend pas « claude » — et ce n'est pas le même historique

Deux choses portent le même nom, et les confondre coûte une journée. Le contexte de
**Claude** est rendu par la reprise de session : il est complet dès que le worker
démarre. L'historique de la **page** est un `deque` en mémoire du processus
(`MEMOIRE = 3000`) : un processus neuf part de rien, et la page affiche une
conversation vide même quand Claude se souvient de tout.

Mesure du 8 novembre 2026, à l'ouverture d'une conversation :

- ~7 s : la page de l'agent répond. C'est à cet instant que le tableau rend la main
  (`TALK_ATTENDRE=page`) et que le téléphone y arrive.
- ~13 s : le worker est debout.

Le rejeu de l'historique partait après le second. Six secondes devant une page
vide, puis des lignes vieilles de trois jours arrivant **en direct**, avec
l'animation du direct, après un `_bonjour` qui avait déjà annoncé « 0 ligne ».
C'était la moitié du « ça rouvre une nouvelle conversation comme si elle n'était
pas ouverte » — l'autre moitié étant le lanceur du tableau, qui tuait pour de bon
un agent lent à répondre.

Rien dans ce rejeu n'a besoin du worker : la session se lit dans l'index local,
l'historique dans un fichier. Il part donc juste après `tableau.demarrer()`.

- **`journal.session_a_reprendre` est le seul juge** de « quelle conversation ».
  `Worker._a_reprendre` lit le même index par la même fonction. Deux chemins qui
  décident séparément finissent par décider différemment, et ce jour-là la page
  montrerait l'historique d'une conversation pendant qu'on parle à une autre.
- **Le rejeu d'après le worker reste, en filet.** La devinette a parié sur l'index
  local ; « claude » peut avoir repris autre chose, ou rien si la reprise a échoué.
  Un garde-fou évite la double copie, et **vide le flux** si le pari était perdu —
  garder les deux conversations à l'écran est le pire des trois états.
- **Changer de conversation oublie ce qui a été rejoué.** Sans cette ligne, revenir
  sur une conversation qu'on vient de quitter laisse la page VIDE : le garde-fou la
  croit déjà affichée, et il a raison — mais le flux vient d'être vidé, donc plus
  personne ne la voit. Trouvé en relisant le correctif, pas en le subissant.

`voix/test_historique_tot.py` tient les trois, et vérifie dans la source que le
rejeu part bien **avant** `worker.start()` : c'est tout l'objet du correctif, et
c'est la première chose qu'un refactor remettrait au mauvais endroit.

### Un transcript n'a pas de taille bornée : on en lit la FIN, jamais le tout

Le 8 novembre 2026, rouvrir la conversation d'insnap prenait **deux minutes** et
laissait la page vide tout ce temps. Le transcript de la session pesait **334 Mo**,
et `get_session_messages` le chargeait **entier** — mesuré : **164 secondes** et
1,9 Go de RSS sur une machine qui en a 4,7 — pour en tirer les lignes affichées.

Or `rejouer_session` renvoie déjà `evenements[-limite:]` : **tout ce qui précède
était lu, parsé, puis jeté.** On se place donc à `taille - REJEU_OCTETS` (8 Mo) et
on lit la fin. Même résultat à l'écran, **164 s → 1,1 s**.

Trois choses que le raccourci a coûtées, et qu'il ne faut pas redéfaire :

- **La première ligne du budget est jetée** : elle est coupée en deux par la
  position de départ. L'oublier ne lève rien — l'erreur est avalée ligne par
  ligne — ça mange juste le plus ancien message, en silence.
- **On filtre avant de parser** (`b'"message"' not in ligne`), la même leçon que
  `poids_contexte` : c'est elle qui fait la différence entre une seconde et une
  minute.
- **`isSidechain` est écarté.** Le travail intérieur d'un sous-agent n'est pas la
  conversation ; le rejouer ferait apparaître, sous le nom de l'utilisateur, des
  consignes qu'il n'a jamais écrites. Le SDK le faisait pour nous.

Le repli par le SDK reste, pour le seul cas où le fichier n'est pas là où on
l'attend. `test_rejeu_gros` piège `get_session_messages` : s'il est appelé alors
que le fichier existe, le test tombe.

Et la conséquence qu'on ne voyait pas : pendant ces deux minutes, la page de
l'agent ne répondait pas, donc le lanceur du tableau le déclarait mort et **tuait
son groupe de processus**. Le poids du transcript ne ralentissait pas l'ouverture,
il l'empêchait.
