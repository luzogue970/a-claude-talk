# Journal des versions

Toutes les evolutions notables depuis `v0`. Le format suit [Keep a Changelog][kac] et le
versionnage [semantique][sv] : majeure pour une rupture, mineure pour une fonctionnalite,
correctif pour un correctif.

[kac]: https://keepachangelog.com/fr/1.1.0/
[sv]: https://semver.org/lang/fr/

## [1.16.0] — 2026-08-27

### Corrige — CINQ moteurs sur sept ne se chargeaient pas
La chaine s'annonçait « AssemblyAI → Deepgram → Speechmatics → Gladia → local → Soniox →
Azure » et il ne restait que Deepgram et le local. AssemblyAI, Speechmatics, Gladia et Soniox
etaient ecartes au demarrage, chacun avec un `RuntimeError: Plugins must be registered on the
main thread` noye dans un mur de traces.

Un plugin LiveKit s'enregistre A L'IMPORT, et `Plugin.register_plugin` refuse hors du fil
principal. Or l'agent tourne dans un fil de travail : un `from livekit.plugins import X` ecrit
paresseusement dans `construire()` arrivait donc toujours au mauvais moment. Azure et Silero
marchaient parce qu'ils sont importes en tete de `agent.py`.

Les plugins de la chaine sont desormais precharges au chargement du module de l'agent, sur le
fil principal, chacun protege : un plugin casse retire son moteur, il n'empeche pas de demarrer.

Pourquoi rien ne l'avait vu : le banc d'essai, les tests et mes verifications a la main
tournaient tous sur le fil principal. Le seul contexte ou le defaut se produit est
l'application reelle — la meme lecon que la cle lue avec `source` au lieu du parseur de
l'application. La nouvelle suite `fil` construit chaque moteur DEPUIS un fil secondaire, et
verifie dans un interpreteur NEUF que l'agent precharge vraiment. La premiere version de ce
test restait verte sans le prechargement, parce qu'elle avait charge les plugins elle-meme.

### Corrige — chaque ligne de journal s'imprimait deux fois
`logging.basicConfig` posait un handler sur la racine, et LiveKit ajoute le sien sans retirer
ceux qui existent. Le demarrage faisait donc le double de sa longueur, et une trace d'erreur
apparaissait deux fois de suite — ce qui donne l'impression que le probleme s'est produit deux
fois. LiveKit met deja la racine en DEBUG en mode console.

### Corrige — un message ecrit pouvait se perdre en silence
`composer.onsubmit` sortait avant d'appeler `envoyerCmd`, qui sait pourtant mettre en file et
reconnecter. Liaison coupee : on tapait, on faisait Entree, rien ne se passait. Le bouton
dependait de l'etat de la liaison, la touche non — deux chemins, deux comportements.

### Corrige — deux reglages documentes ne faisaient rien
`VOIX_INPUT_DEVICE` et `VOIX_OUTPUT_DEVICE` etaient lus depuis l'environnement et utilises
nulle part. Un test verifie maintenant que toute variable `VOIX_` citee dans le README est
vraiment lue par le code.

## [1.15.0] — 2026-08-27

### Verifie — micro coupe, plus un octet ne part vers le nuage
Prouve sur les vrais objets et non par lecture de code : appliquer_micro →
set_audio_enabled(False) → on_detached → ConsoleAudioInput._attached=False → la trame est
jetee. Dix secondes d'audio poussees micro coupe, zero trame sortie.

Le test verifie aussi que la coupure n'est pas un heritage vide : `TcpAudioInput`, l'autre mode
console, n'implemente ni on_attached ni on_detached — couper le micro n'y couperait rien. Si ce
test devient rouge, c'est que couper le micro ne coupe plus la depense.

### Ajoute — une veille qui coupe le micro oublie
Micro ouvert, l'audio part en CONTINU : `push_audio` transmet chaque trame sans filtre VAD.
C'est la conception du streaming, mais chaque seconde de micro ouvert est facturee et une video
en fond est transcrite. La veille coupe apres cinq minutes sans aucune parole detectee, l'annonce
et nomme le chemin du retour. Parler remet le compteur a zero.

### Corrige — une cle presente dans le fichier etait ignoree en silence
Ecrite « export CLE="..." » dans un fichier qui utilise « CLE=valeur », elle devenait une
variable nommee « export CLE » : AssemblyAI affichait « pas de cle » alors que la cle etait la.
Le parseur accepte desormais les deux formes.

### Ajoute — reprendre une conversation recharge tout son historique
Cliquer dans le selecteur vide le flux puis rejoue ce que Claude Code a ecrit sur disque. La
reprise automatique de `vv` rejoue aussi — sans ça, le contexte etait complet cote Claude et la
page restait vide, soit l'impression de conversation neuve qu'on venait de corriger.

### Corrige — le panneau des moteurs elargissait la page de 124 px
Ancre a droite et borne a la fenetre, avec defilement interne. Le test de rendu mesure le
debordement horizontal panneaux ouverts ET remplis, et rejoue la mesure en fenetre de 860 px.

### Change — la zone de saisie prend la place qu'elle merite
52 px et 14,5 px de texte au lieu de 36 et 13, fond detache, halo au focus, micro a 48 px. Elle
grandit toujours avec le contenu : plus visible, pas plus envahissante.

### Change — des icones qui disent quelque chose dans le selecteur
Une pastille dont la forme porte l'information autant que la couleur, avec son sens en
infobulle, et une puce « ↻ 7 » pour les reprises.

## [1.14.0] — 2026-08-27

### Corrige — les conversations ne se dedoublent plus (et ne se dedoublaient pas vraiment)
Quatre defauts distincts derriere « des conversations qui se relancent toutes seules ». Les
donnees le montrent : 23 lignes d'index pour 5 sessions Claude. Le contexte n'etait PAS perdu.

- `vv` ouvrait une conversation NEUVE a chaque lancement — `resume` n'etait rempli que par
  `vvreprendre`. Il reprend maintenant la derniere conversation du dossier, et l'annonce.
  `vvneuf` en ouvre une neuve, ce qui est le cas rare.
- une reprise qui echoue ne disait rien : Claude Code ouvre une session neuve sans raler. On
  compare l'identifiant obtenu au demande, l'ecart devient un avertissement.
- le rang affiche ne designait pas la meme conversation que le rang resolu : `vvconv`
  numerotait le sous-arbre, `resoudre` cherchait partout. « vvreprendre 3 » pouvait reprendre
  autre chose que la troisieme affichee.
- le nom du transcript etant a la minute, deux lancements rapprochés du meme projet
  ecrivaient dans le meme fichier — le second ecrasait le premier.

### Ajoute — un selecteur de conversations sur la page
Une ligne par conversation reelle, reconnaissable a la derniere phrase qu'on y a dite : entre
quatre conversations sur le meme projet, « 22:49 » et « 22:55 » ne distinguent rien. Le nombre
de reprises est affiche. Cliquer bascule a chaud, sans quitter l'application. Une conversation
sans identifiant de session, ou tenue par un autre agent, n'est pas cliquable — et on dit
pourquoi.

### Ajoute — le micro dans la fenetre de conversation
Rond, 40 px, aligne sur le bas du champ. Le geste qu'il sert : couper pour corriger le texte,
rouvrir pour continuer a dicter. Cinq ondes bougent quand la parole est DETECTEE — pas un
volume, qui n'est pas accessible en mode console. Animer au hasard aurait donne une fausse
confirmation d'etre entendu, pire que pas d'indicateur.

### Change — la preuve avant le palier dans l'ordre des moteurs
Nouveau champ `demontre` : a-t-on VU ce moteur rendre une phrase COMPLETE ici ? AssemblyAI
(3,0 % en 2,9 s) et Deepgram (3,0 % en 5,4 s) l'ont fait. Speechmatics tronque ou reste muet,
Gladia coupe a la premiere proposition, Soniox repond « 402 balance exhausted », Azure est
epuise. Un moteur non demontre passe derriere ceux qui ont prouve, meme gratuit et
renouvelable : un quota mensuel ne vaut rien si le moteur rend la moitie de la phrase.

### Corrige — Speechmatics pouvait transcrire parfaitement et rester muet
Le plugin est par defaut en `turn_detection_mode=EXTERNAL` : il ne finalise rien lui-meme et
attend qu'un tiers ferme le segment. Or l'agent decide desormais seul de la fin de tour. Passe
en ADAPTIVE : la reconnaissance rend du TEXTE, c'est nous qui decidons quand le tour part.

### Ajoute — voix/fusionner.py
Regroupe les transcripts d'une meme conversation (19 fichiers pour 4 conversations ici). Ne
touche pas aux sessions de Claude Code. Rien sans `--vraiment`, sources archivees dans
`avant-fusion/`, relecture du fichier ecrit avant tout deplacement.

### Ajoute — le champ texte monte a 55 % de la hauteur
Une longue dictee doit se relire d'un coup, et c'est ce qu'on fait juste avant d'envoyer. Avec
une gouttiere discrete plutot que celle du systeme.

### Ajoute — pyflakes dans la suite de tests
J'ai ecrit trois `log.warning` la ou `log` n'existait pas, et appele une fonction 400 lignes
avant son `def`. Ni la compilation ni les tests qui ne passent pas par la ligne fautive ne le
voyaient : les deux auraient leve un NameError en pleine session, sur un chemin d'erreur.

### Corrige — le talon de test ne savait pas cliquer
Il rendait `[]` pour tout selecteur d'attribut. Les panneaux se dessinent en posant innerHTML
puis rebranchent leurs gestionnaires par `querySelectorAll("[data-...]")` : aucun clic n'etait
donc jamais branche dans les tests. 534 verifications, deux suites nouvelles
(`fusion`, `consommation` etendue).

## [1.13.0] — 2026-08-26

### Change — l'agent decide de l'envoi, donc le decompte affiche est celui qui decide
`turn_detection` passe en mode `manual`. Le VAD continue de signaler debut et fin de parole,
mais LiveKit ne commet plus le tour : l'agent arme une fenetre, publie son echeance absolue,
et commet a l'echeance. La page lit cette echeance au lieu de recompter.

Avant, LiveKit decidait et la page estimait a partir des memes reglages : deux horloges pour
une seule decision. Quand elles divergeaient on lisait « encore 4 s » alors que le message
etait deja parti, donc « retenir » arrivait apres la decision. C'etait la cause de « la phase
de retenir, des fois je ne peux pas la faire ». `VOIX_TOUR_MANUEL=0` rend la main a LiveKit,
et la page le detecte : sans echeance publiee elle retombe sur une estimation avec plafond.

### Ajoute — un ordre local part immediatement
« arrete », « coupe le micro », « chut » attendaient cinq secondes de silence comme n'importe
quelle phrase. Ils partent maintenant sans attendre — limite aux enonces de cinq mots au plus,
parce que la detection considere « arrêter » + « ça » comme un ordre et que *« explique-moi
pourquoi il faut arrêter de faire ça »* est donc detectee comme un arret. Ce faux positif
existait deja ; un raccourci sans limite lui aurait retire le filet de la relecture.

### Corrige — la retenue est collante, plus rien ne part par-dessus un texte en attente
On dictait, c'etait retenu (la barre gardait la phrase), on reparlait, ce second tour partait,
et la page vidait la barre : la premiere phrase disparaissait sans avoir jamais ete envoyee ni
signalee. C'est la version exacte du « texte deja envoye qui reapparait, et parfois seulement
une partie ». Vider la barre a la main libere la retenue — sans ce signal l'agent retiendrait
tout indefiniment.

### Ajoute — la page dit POURQUOI un texte est retenu
Quatre raisons distinctes (`attente`, `occupe`, `mode`, `tour`), dans le flux et contre la
barre, avec ce qu'il faut faire ensuite. « retenu » tout court laissait chercher laquelle
s'appliquait. La note s'efface des que la situation change.

### Corrige — couper le micro vaut « j'ai fini de parler »
En manuel la fin de tour vient du VAD. Si le micro se coupait pendant la parole, ce signal
pouvait ne jamais arriver et le texte deja transcrit restait dans la barre sans decompte et
sans envoi, sans que rien l'explique.

### Ajoute — combien il reste sur chaque palier de moteur
La pastille du moteur affiche au survol ce qu'il reste ; le panneau montre une jauge par
moteur. On compte le temps micro ouvert, impute au moteur actif, redecoupe a chaque bascule
pour que le temps consomme par Azure avant sa chute ne soit pas facture au suivant. Le local
n'est jamais compte.

La page distingue deux natures d'information : « il reste ~7 h 12 » est une **estimation
locale**, « epuise — constate » est un **fait** (le fournisseur a refuse, avec son motif). Le
constat prime sur l'estimation. Un compteur parti de zero annonçait « 5 h restantes » sur un
palier Azure vide depuis des semaines — un chiffre faux et rassurant est pire qu'aucun chiffre.

### Change — l'ordre de la chaine suit ce qu'il reste, plus l'ordre de declaration
Quatre classes : credit abondant, quota mensuel, credit sous reserve, moteurs sans texte en
direct. Un moteur constate epuise part a la fin. Concretement Deepgram est en tete tant qu'il
lui reste plus de 100 h, puis il recule derriere Speechmatics sans jamais vider la reserve.

### Ajoute — Soniox et Google Cloud
Neuf moteurs. Le vocabulaire du projet passe comme chacun l'attend, verifie dans les
signatures : Soniox veut une chaine de contexte, Google des couples (mot, poids).

### Corrige — le vocabulaire cassait Speechmatics entierement
Le plugin attend des objets `AdditionalVocabEntry`, pas des chaines. Une liste de chaines
levait « 'str' object has no attribute 'content' » et cassait le moteur, pas seulement le
biais lexical.

### Corrige — 155 lignes dupliquees dans config.py
Deux blocs y figuraient deux fois a l'identique. Python garde la derniere definition, donc
modifier la premiere ne faisait rien, sans le moindre message : le pire symptome possible sur
un fichier de configuration, on croit avoir agi. Suppression prouvee sans effet — les 48
valeurs publiques du module sont identiques avant et apres. 465 lignes → 310.

### Corrige — le lanceur de tests annonçait des preuves qu'il n'avait pas
Trois defauts de la meme famille : `-k front,tableau` ne correspondait a rien et rendait
« bon pour la fusion » sur zero test ; le compteur ne lisait qu'un dialecte de sortie sur
trois, rendant 97 verifications invisibles ; une suite verte sans rien de compte passait pour
une preuve — elle est desormais MUETTE et fait sortir en erreur.

`test_boucle`, la suite la plus couteuse (vrai Claude, vrai quota), n'affichait que des
compteurs et ne verifiait rien. Elle verifie maintenant huit choses.

### Ajoute — des garde-fous contre les regressions rencontrees
Aucune constante definie deux fois dans config ; tout plugin importe epingle dans
requirements avec une seule version de livekit partout ; tout etat durable present dans
`ETATS` (le bug des selecteurs vides) ; tout champ de `__init__` present dans `_amorcer_etat`
(les talons de test ne peuvent plus deriver). Suites `consommation` (49) et `fenetre` (47).
469 verifications au total.

## [1.12.0] — 2026-08-26

### Corrige — un texte deja envoye revenait dans la barre

Deux defauts d'un meme modele trop pauvre : une seule variable portait a la fois le brouillon
tape et la dictee en cours. Reproduits avant correction.

- Une transcription arrivant APRES l'envoi du tour remettait dans la barre un texte deja
  envoye, qui repartait au message suivant. LiveKit le fait et le journalise meme.
- Une phrase coupee par une pause produit deux transcriptions finales, chacune ne contenant
  que son segment. En se remplacant au lieu de se cumuler, le debut disparaissait.
- Le routage passait `final` en dur a `false`, donc les segments ne se cumulaient jamais.

Trois etats distincts desormais : le brouillon tape (il survit a l'envoi, c'est ton texte),
les segments finalises de l'enonce en cours, et un drapeau « dictee ouverte » pose quand tu
commences a parler. Une transcription hors dictee est ignoree. Seule la jonction est
normalisee : nettoyer tout le champ detruirait la mise en forme d'un brouillon.

### Ajoute — `tests.py`, une commande avant chaque fusion

Neuf suites, groupees par domaine fonctionnel. Le tableau final dit ce qui est COUVERT par
domaine, pas seulement combien de tests passent : un compte global rassure sans informer,
alors qu'un domaine a zero test se voit.

- `vtest`, `vtest --rapide`, `vtest -k <suite>`, `vtest --liste`.
- Une suite qui s'abstient (Chrome absent, pas de cle) est marquee IGNOREE, jamais confondue
  avec une suite verte : la confusion donnerait une couverture imaginaire.
- Une suite ABSENTE fait echouer le lot : un fichier de test disparu ne doit pas passer pour
  un succes.
- Il a trouve cinq echecs des sa premiere execution — d'anciens cas qui appelaient l'API de
  dictee sans l'ouvrir.

## [1.11.0] — 2026-08-26

### Corrige — selecteurs vides et panneau absent sur les conversations longues

Reproduit : `config`, `modeles`, `efforts`, `delais` et `moteurs_stt` sont publies au
demarrage, donc ils sont les PREMIERS dans une file bornee — et donc les premiers evinces. Sur
une conversation longue, ou apres un rejeu de 1 200 lignes, une page ouverte ensuite ne
recevait plus rien de tout ca. Les conversations courtes n'etaient pas touchees, ce qui rendait
le defaut incomprehensible.

La cause de fond : **un etat n'est pas un evenement**. « Quels modeles existent » reste vrai
tant que personne ne le change ; « un outil a demarre » appartient a un instant.

- L'etat est garde a part et renvoye a chaque connexion, avant l'historique.
- La capture se fait AVANT le retour « aucun client » : l'etat de demarrage est publie alors
  que le navigateur n'est pas encore connecte, et c'est precisement celui qu'on doit retenir.
  Mon premier correctif etait place apres, donc ne servait jamais — le test l'a attrape.

### Corrige — le texte qui n'apparait pas, ou seulement a la fin

Trois symptomes, une seule cause : le moteur local a `interim_results=False`. Il EST un flux,
mais sans resultats intermediaires — cette nuance rendait le comportement incomprehensible.
Rien ne s'ecrivait en parlant, le texte apparaissait 4 a 7 s plus tard, et « retenir » n'avait
rien a relire au moment de decider.

- La capacite « texte en direct » est declaree par moteur, distincte du streaming.
- La pastille du moteur affiche « · sans direct », avec la consequence en infobulle.
- Une pastille « transcription (fin de phrase) » s'allume pendant l'attente : sans elle la
  page semblait figee puis du texte apparaissait sans raison.
- Le garde-fou de cette pastille passe de 4 a 12 s : a 4 s il s'eteignait EN PLEINE attente,
  juste avant l'arrivee du texte.
- Le lancement avertit et dit quoi faire : une cle Speechmatics ou Gladia rend le direct.

### Interne — les tests ne dependent plus de leur ordre

Les numeros d'evenements ecrits a la main rendaient chaque section dependante des autres :
`dejaVu` rejette un numero deja depasse, donc inserer une section cassait silencieusement
toutes celles d'apres. Un compteur auto-corrigeant remplace les valeurs en dur.

## [1.10.0] — 2026-08-26

### Modifie — l'historique rejoue n'est plus grise, et garde tout

Le grise donnait l'impression de lire une archive alors qu'on relit son propre travail. La
classe `passe` reste, mais pour le COMPORTEMENT — aucun indicateur ne s'allume, le compteur
d'actions ne gonfle pas — jamais pour l'apparence.

- Tout ce qui a du contenu est rejoue ligne par ligne : chaque appel d'outil AVEC son
  resultat, ce que tu as dit, ce qu'il a ecrit, le bilan de chaque tour. Les filtres du
  tableau decident de ce qui s'affiche : c'est leur role.
- Une frontiere explicite pleine largeur remplace la nuance de gris : « ↑ historique
  rechargé » et « ↓ ici commence le direct ».
- Deux exceptions MESUREES : la reflexion est ecartee parce qu'elle est vide sur disque (349
  blocs `thinking`, 0 Ko), et les sorties d'outils sont tronquees a 400 caracteres parce que
  completes elles pesaient 4,76 Mo, soit 85 % du volume, pour des sorties de `cat` vieilles
  de trois semaines. La troncature est signalee.
- 5,6 Mo -> 299 Ko pour 1 242 lignes, envoyees par lots : tout pousser d'un trait remplissait
  la file du client jusqu'a ce qu'elle jette ses plus anciens messages, et on perdait le debut
  de la conversation qu'on venait de recharger.

### Ajoute — le titre de la fenetre porte le projet

`claude-talk — insnap`. Avec trois conversations ouvertes, trois onglets nommes
« claude-talk » sont indiscernables — et c'est le titre qu'on lit dans la barre des taches.

## [1.9.0] — 2026-08-26

### Modifie — `vvconv` se limite au dossier courant et a ses descendants

Depuis la racine on voit tout, depuis un projet on ne voit que lui. La liste triait « ici
d'abord » mais montrait quand meme l'historique de tous les autres projets.

- Un descendant affiche son sous-chemin relatif (`./bridge`) : depuis la racine d'un projet,
  ce qu'on veut savoir est dans quel sous-dossier, pas le chemin complet.
- Le nombre de conversations masquees est toujours annonce, avec `vvconv --tout` pour les
  voir. Une liste qui raccourcit sans le dire se lit comme une perte de donnees.
- Le piege du prefixe est traite : « /a/projet-autre » commence par « /a/projet » sans en
  etre un descendant. Une comparaison de chaines naive l'aurait melange au mauvais projet.
- Le filtre est desactive par defaut dans `historique()`, parce que DEUX appelants ne doivent
  pas l'appliquer : `actives()` arbitre le micro entre toutes les conversations vivantes ou
  qu'elles soient, et `resoudre()` sert a reprendre par identifiant depuis n'importe ou.
  Filtrer la premiere la rendrait aveugle, la seconde inutilisable.
- Les conversations sans chemin enregistre restent rapprochees par le nom du projet : sans
  chemin on ne peut pas savoir si c'est un descendant, et deviner plus large reviendrait a
  polluer la vue d'un projet avec l'historique des autres.

`voix/test_journal.py` verrouille ces cas, dont les deux appelants a ne pas filtrer.

## [1.8.0] — 2026-08-25

### Ajoute — sept moteurs de reconnaissance, tous avec un palier gratuit reel

Veille d'aout 2026 sur ce qui existe. Quatre nouveaux moteurs, tous avec un plugin LiveKit de
premiere main et un palier gratuit verifie : Speechmatics (8 h/mois renouvele, meilleur WER
des bancs publics), Gladia (4 h/mois de temps reel), AssemblyAI (50 $ de credits), Groq
(whisper-large-v3, sans streaming).

- `voix/moteurs_stt.py` : une table unique. La chaine de repli, le selecteur du tableau, le
  panneau de configuration et le banc d'essai en derivent — trois listes paralleles auraient
  derive l'une de l'autre, c'est deja arrive avec les libelles de filtres.
- L'ordre par defaut suit une logique de budget : quotas MENSUELS d'abord (ils reviennent),
  CREDITS uniques ensuite (finis, on les garde), local en dernier (illimite, lent).
- Le vocabulaire du projet survit a chaque bascule. Chaque fournisseur nomme differemment le
  biais lexical — six noms differents — et un seul endroit connait cette diversite.
- `config.chaine_stt()` supprimee : elle faisait doublon avec la table. Deux sources de
  verite sur la meme question, c'est la panne qu'on cherche du mauvais cote.

### Ajoute — voir et choisir le moteur qui transcrit

- Une pastille nomme le moteur ACTIF, et suit les bascules du repli. Elle passe en orange
  quand la chaine s'est replie, avec la raison en infobulle.
- Un clic ouvre la liste : palier gratuit, rang dans la chaine, cle manquante. « X en tete »
  place un moteur devant sans jeter les autres.
- Le choix est enregistre hors du depot et vaut au prochain lancement : `AgentSession.stt`
  est en lecture seule. Le panneau le dit plutot que de laisser croire a un effet immediat.

### Ajoute — `voix/banc_stt.py`, un banc sur SA voix

Les editeurs publient tous des bancs ou ils gagnent. Celui-ci mesure le texte rendu, le WER
et la latence, sur des phrases de francais technique ou sur un enregistrement fourni.

- Une seconde de silence ajoutee en fin : sans elle le VAD ne ferme aucun segment et le
  moteur rend du vide en 0,2 s, ce qui se lit comme une mauvaise transcription.
- Un moteur qui ne repond pas est declare INUTILISABLE, pas mauvais. Un quota epuise ferme le
  flux sans exception ; noter 100 % d'erreur accuserait la qualite pour un probleme de credit.
  Mesure sur Azure, dont le palier F0 etait epuise.

## [1.7.0] — 2026-08-25

### Retire — l'ancien systeme `tts/`

Quinze fichiers, 2 131 lignes, entierement remplaces par `voix/`. Verifie avant de retirer :
aucune reference depuis `voix/` ni `outils/`, sauf vers `tts/voices/` qui n'etait pas suivi.

- Retire du depot, conserve dans l'historique git.
- `.gitignore` consolide : une seule regle `tts/`. Ce qui reste sur le disque contient
  `azure.conf`, une cle en clair — mieux vaut une regle large qu'une liste a maintenir.
- Les voix Piper (268 Mo) restent sur le disque et n'ont jamais ete dans le depot. Le README
  pointe maintenant vers leur source amont, puisqu'on ne peut plus dire « elles sont la ».
- Le depot passe de 40 a 25 fichiers suivis. Il n'y a plus qu'un endroit a lire.

## [1.6.0] — 2026-08-25

### Corrige — le depot etait incomplet pour une installation neuve

Un clone ne suffisait pas a demarrer, et le README supposait des choses absentes du depot.

- `faster-whisper` manquait dans `requirements.txt` : le repli local plantait sur une
  installation neuve. Un controle automatique verifie desormais que chaque import tiers a sa
  dependance declaree — c'est cette classe de bug qui echappe a la relecture.
- `outils/voix.fish` : les raccourcis `vv` etaient references dix fois dans le README sans
  jamais etre livres. `VOIX_RACINE` se deduit de l'emplacement du fichier, symlink compris.
  Les fonctions dependant d'une unite systemd ou d'un script i3 ne sont pas livrees : une
  commande qui echoue est pire qu'une commande absente.
- Une section **Prerequis** : version de Python, la CLI Claude Code authentifiee, le compte,
  Azure, et ce qui n'est PAS necessaire — aucun compte LiveKit, aucune cle API Anthropic,
  aucun GPU.
- Les etapes de creation de la ressource Azure et de la cle Deepgram, et une sequence de
  verification qui dit ou regarder quand ca ne demarre pas.

### Corrige — des references personnelles dans un depot destine a etre partage

- `PHRASE_LIST` contenait des noms de projets internes. La liste de base est generique, et le
  README explique que c'est a completer — c'est justement ce qui fait la difference entre un
  sigle maison et « kubedka ».
- `test_conversation.py` portait un chemin absolu avec un nom d'utilisateur : il ne marchait
  que sur une machine. Dossier temporaire.
- Les anecdotes de transcription sont conservees, sans nommer le projet : la mesure garde sa
  valeur, le nom n'en apportait aucune.

### Ajoute — un avertissement sur `tts/`

L'ancien systeme est toujours la, conserve pour ses voix Piper. Un lecteur devait pouvoir
l'apprendre en cinq secondes plutot qu'en le lisant.

## [1.5.0] — 2026-08-25

### Ajoute — plusieurs conversations en parallele, arbitrees

Trois sujets ouverts en meme temps est un usage normal. Ce qui ne se partage pas, c'est le
micro et les haut-parleurs : deux agents qui ecoutent transcrivent la meme phrase et l'envoient
chacun a son Claude, et deux voix sur les memes haut-parleurs s'annulent au lieu de
s'additionner. On avertissait ; ca ne suffisait pas.

- `voix/pupitre.py` : un registre des conversations vivantes et deux baux — micro et parole.
  Ecritures atomiques, autorite au PID verifie avec sa ligne de commande.
- Le bail du micro se PREND au demarrage : lancer une conversation, c'est vouloir lui parler.
  Les autres continuent leur travail, elles arretent seulement d'ecouter.
- Le bail de parole fait attendre son tour a la seconde conversation, qui le dit dans son flux.
- L'en-tete liste les conversations ouvertes, avec des liens vers leur propre tableau et un
  bouton « ecouter ici ». `vvsessions` fait la meme chose depuis le terminal.
- Ecouter = vouloir ET avoir le bail. Deux etats separes, un seul endroit qui calcule
  l'effectif : les quatre combinaisons sont testees.
- Cas degrades couverts : detenteur tue, bail de parole perime, fichier tronque, PID
  reattribue. Sans eux, un agent tue au mauvais moment rendait les autres sourds pour toujours.

### Ajoute — couper et relire une reponse, depuis sa ligne

- « couper » n'apparait que sur la lecture qui joue : « couper la parole » ne dit pas laquelle.
- « relire » fonctionne sur n'importe quelle reponse, y compris ancienne. Les quarante derniers
  textes sont conserves : une deuxieme synthese ne coute que des caracteres, la ou refaire le
  tour couterait tout le travail.

### Corrige — le panneau annoncait le port demande, pas le port reel

Avec plusieurs conversations, la bascule de port devient la regle. Un panneau qui annonce une
adresse ou personne ne repond est pire que pas d'adresse du tout.

## [1.4.0] — 2026-08-25

### Corrige — la pastille annonçait la taille de la fenetre, pas le temps restant

`5h` laissait croire qu'il restait cinq heures. C'est la LARGEUR du seau glissant : mesure
faite, la fenetre affichee « 5h » se reinitialisait dans 1 h 08.

- Le libelle dit la fonction (`session`, `semaine`) et le temps restant est affiche a cote.
- L'infobulle rappelle la taille reelle et l'heure exacte de reinitialisation.
- La forme parlee est formatee a part : « dans une heure et neuf minutes », parce que
  « 1 h 09 » s'entend « un h zero neuf ».

### Corrige — les pastilles ne s'affichaient pas toujours des le debut

Si la premiere lecture echouait (429, jeton en cours de rafraichissement, reseau), la boucle
attendait son cycle de cinq minutes avant de reessayer.

- Relance toutes les 15 s tant qu'aucune lecture n'a abouti.
- Une pastille en pointilles dit `quota…` en attendant : une en-tete vide laisse croire a
  une panne.

### Ajoute — un decompte en temps reel sans une requete de plus

Le serveur envoie l'echeance ABSOLUE ; la page decompte localement une fois par 30 s. Une
echeance connue n'a pas besoin d'etre redemandee pour etre affichee en temps reel. Le
pourcentage reste relu aux seuls moments utiles : fin de tour et toutes les cinq minutes.

## [1.3.0] — 2026-08-25

### Ajoute — retenir d'office ce qui est dit pendant une tache

Parler pendant que Claude travaille ne l'envoie plus : le message se depose dans la barre.
Sans ca, le worker acceptait le second message, repondait « note, j'ajoute ca », et il partait
sans qu'on ait rien relu — or c'est precisement le moment ou l'on parle pour REAGIR a ce qu'on
voit passer, donc celui ou une phrase mal transcrite coute le plus cher.

- Continuent de passer, deliberement : les ordres locaux (« arrete », « coupe le micro ») et
  les reponses a une demande de permission. Ce sont des reactions, pas des taches a relire —
  et un « arrete » parque serait dangereux. Trois tests verrouillent ces exemptions.
- Le bouton « retenir » passe en pointilles des qu'une tache demarre, AVANT qu'on parle :
  decouvrir apres coup que son message n'est pas parti est la pire facon de l'apprendre.
- `VOIX_RETENIR_OCCUPE=0` retablit l'envoi immediat.

### Ajoute — une ligne « retenu » dans le flux

Un message retenu ne laissait aucune trace : on pouvait croire avoir parle pour rien. La ligne
dit le texte et laquelle des deux raisons s'applique — le reglage, ou le travail en cours.

### Modifie — les lignes « micro » sont masquees par defaut

Leur etat change souvent et ne raconte rien de la conversation. Quatrieme genre masque, avec
le journal technique, la transcription en cours et la sortie des outils.

## [1.2.0] — 2026-08-25

### Ajoute — passe d'ergonomie sur toute la page

- La barre de saisie respire : 18 px sous le champ plutot que le bord de l'ecran.
- L'en-tete est organise en trois zones qui ne se coupent pas en deux, sur deux rangs. Une
  seule rangee en flex-wrap se repliait n'importe comment : la pastille « parole » sautait a
  la ligne suivante et atterrissait a gauche des filtres.
- Libelles courts dans les selecteurs — « Opus 5 » et non « Opus 5 — le plus capable » : un
  select affiche le libelle complet de l'option choisie, et 250 px ecrasaient l'en-tete. La
  nuance reste en infobulle. Mesure : 250 -> 96 px.
- La pastille d'etat porte un point de sa couleur : on le lit avant le mot.
- Les lignes du flux se detachent au survol : suivre une ligne dans un mur de deux cents
  sans repere est fatigant.

### Corrige — la ligne « session » s'affichait vide

L'evenement porte `id`, pas `texte`, et `corps()` n'avait pas de cas pour lui. Or c'est
precisement l'identifiant qu'on vient chercher pour reprendre une conversation. La ligne
affiche desormais l'identifiant et la commande de reprise.

### Interne — un point d'entree unique pour ce qui arrive du serveur

Le routage vivait dans `ws.onmessage`, donc un harnais appelant `ajouter()` ne le traversait
pas : un apercu montrait des selecteurs vides en croyant montrer l'application, et une
assertion sur leur largeur passait a vide.

- `recevoir(e)` est nomme et hors du gestionnaire ; l'application et les harnais partagent
  la meme entree.
- `test_rendu.js` mesure maintenant la respiration sous le champ, le nombre de rangs de
  l'en-tete, la largeur des selecteurs remplis, et que le bandeau ne recouvre pas la barre.

## [1.1.2] — 2026-08-25

### Corrige — l'historique rejoue s'affichait sur un caractere de large

`.ev.passe .pip{display:none}` retirait l'indicateur du flux de la grille : le corps du
message glissait dans la colonne de 14 px prevue pour lui, et 563 caracteres dans 14 px
donnent un caractere par ligne, sur des lignes de 7 400 px de haut.

- La regle est retiree : un indicateur sans etat est deja invisible, il suffit de le laisser
  occuper sa cellule.
- L'opacite du passe monte de 0,55 a 0,7 : a 0,55 le texte de Claude tombait a 3,8:1 de
  contraste, sous le minimum lisible ; il est maintenant a 5,4:1.
- `voix/test_rendu.js` mesure desormais la geometrie dans Chrome. Le stub de `test_front.js`
  ne pouvait pas voir ce bug. Verifie en reintroduisant la regle : le test la rattrape.

### Ajoute — une interface qui bouge doucement

- Barres de defilement fines, sans fleches, de la couleur des bordures : les natives
  ouvraient une gouttiere claire au milieu d'une interface sombre.
- Le champ de saisie n'affiche sa barre qu'une fois plafonne, plus des la deuxieme ligne.
- Transitions de 120 ms sur tout ce qui reagit, declarees une fois plutot que recopiees.
- Les lignes qui arrivent en direct se posent au lieu d'apparaitre d'un coup — et seulement
  celles-la : animer un rejeu lancerait cinq cents animations simultanees.

## [1.1.1] — 2026-08-25

### Ajoute — expliquer « retenir » sur place

Le mot ne dit pas ce qu'il fait, et un `title` HTML se lit mal et tronque.

- Un `i` a cote de la bascule ouvre une explication : ce que le mode fait, quand il sert,
  que les ordres immediats passent quand meme, et que le decompte a son propre bouton.
- Il est A COTE du bouton, pas dedans : dedans il aurait vole les clics destines a la
  bascule, et « retenir » est fait pour etre bascule, pas pour etre lu. Un test verifie
  precisement ca.
- Survol pour lire, clic pour epingler, Echap ou clic ailleurs pour refermer.

### Interne — les cas de test du front sortent du template literal

Tant qu'ils vivaient dans un template literal, chaque antislash y etait mange une fois de
plus : `\b` devenait un retour arriere, `\/` une simple barre oblique, et une regex correcte
se transformait en erreur de syntaxe. C'est arrive quatre fois.

- `voix/test_front_cas.js` est du JavaScript ordinaire : ce qu'on ecrit est ce qui s'execute.
- L'etat initial des elements (attribut `hidden`, classes) est verifie sur le balisage reel
  et non sur le stub, qui ne peut pas l'heriter.

## [1.1.0] — 2026-08-25

### Ajoute — filtres du flux regroupes en familles

Dix-huit boutons alignes ne disaient ni ce qu'ils montraient ni pourquoi on voudrait les
couper : il fallait les avoir ecrits pour s'en souvenir.

- Quatre familles, chacune une liste deroulante : Conversation, Travail, Commandes, Systeme.
- Une phrase par ligne, qui dit a quoi elle sert — le choix se fait sans documentation.
- Le compteur de la pastille (`4/5`) et son style disent l'etat de la famille sans l'ouvrir.
- `tout` / `rien` par famille : couper cinq lignes ne demande plus cinq clics.

### Corrige — une ligne « session » invisible pour toujours

Le genre `session` arrivait dans le flux sans figurer dans les libelles : sa ligne etait donc
creee avec `display:none`, et aucun filtre ne pouvait la montrer.

- Libelle, explication et defaut sont declares au meme endroit ; une deuxieme liste aurait
  derive de la premiere, et c'est exactement ce qui s'etait produit.
- Un test lit les deux cotes dans la source (ce qui est publie, ce qui est declare) et refuse
  tout ecart. Recopier la liste dans le test n'aurait jamais vu l'oubli.

## [1.0.0] — 2026-08-25

### Documentation — README complet et CHANGELOG

De l'installation a l'usage, avec les raisons derriere les choix — et les
mesures qui les ont tranches, pour qu'un reglage ne soit pas defait par erreur.

- Architecture, installation, lancement, reglages, tableau de bord, permissions,
  ordres locaux, conversations, secrets, tests.
- Chaque piege documente avec sa cause : le quota Azure, la fenetre de 0,3 s, le
  clic avale, la session rangee par repertoire, la notification qui mentait.

## [0.9.0] — 2026-08-25

### Tests — harnais sans navigateur et invariants du serveur

`node --check` valide la syntaxe, pas l'execution : il n'avait pas vu qu'un
`addEventListener` place avant la declaration de sa variable tuait tout le
script au chargement — page blanche, sans rien pour l'expliquer.

- `test_front.js` extrait le script de `tableau.py`, lui donne un DOM minimal et
  verifie les invariants qui MENTENT a l'utilisateur quand ils cassent : un
  indicateur qui tourne sur rien, un mot fige, un decompte bloque a zero, une
  dictee qui ecrase une correction tapee.
- `test_tableau.py` : une commande qui leve ne tue pas la WebSocket, couper le
  micro marche hors activite, une dictee retenue ne sort pas dans le flux.
- Six bugs reels trouves par ce harnais, dont deux que la relecture avait laisses
  passer.

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
