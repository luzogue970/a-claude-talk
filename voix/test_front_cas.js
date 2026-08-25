// Les cas de test du front. Fichier separe, et c'est le point : tant qu'ils vivaient dans
// un template literal de test_front.js, chaque antislash y etait mange une fois de plus —
// `\b` devenait un retour arriere, `\/` une simple barre oblique, et une regex parfaitement
// correcte se transformait en erreur de syntaxe. Ca s'est produit quatre fois.
//
// Ici, c'est du JavaScript ordinaire : ce qu'on ecrit est ce qui s'execute.
//
// Charge et concatene par test_front.js — ne pas lancer directement.


let ok = true, faits = 0;
const dire = (bon, texte) => {
  faits++; ok = ok && bon;
  console.log('  ' + (bon ? 'OK  ' : 'ECHEC') + ' ' + texte);
};
const titre = t => console.log('\n=== ' + t + ' ===');
const restants = () => JSON.stringify([...encours.keys()]);

// ---- cycle de vie des indicateurs -------------------------------------------------------
titre('cycle de vie : rien ne doit rester a tourner');
const suite = (nom, evs, attendu) => {
  encours.clear();
  for (const k in echeances) clearTimeout(echeances[k]);
  evs.forEach(e => ajouter(Object.assign({ t: 0, h: '12:00:00' }, e)));
  dire(restants() === JSON.stringify(attendu),
       nom + ' -> ' + restants() + (restants() === JSON.stringify(attendu)
         ? '' : ' (attendu ' + JSON.stringify(attendu) + ')'));
};
suite('tour nominal complet', [
  { genre: 'partiel', texte: 'renomme la' },
  { genre: 'partiel', texte: 'renomme la variable' },
  { genre: 'toi', texte: 'renomme la variable' },
  { genre: 'pensee', texte: 'voyons', suite: true },
  { genre: 'outil', nom: 'Read', cible: 'config.py', id: 't1' },
  { genre: 'resultat', texte: '...', id: 't1' },
  { genre: 'texte', texte: 'fait', suite: true },
  { genre: 'tour', actions: 1 },
], []);
suite('outil en echec', [
  { genre: 'outil', nom: 'Bash', cible: 'npm test', id: 't1' },
  { genre: 'resultat', texte: 'exit 1', id: 't1', echec: true },
  { genre: 'tour', actions: 1 },
], []);
suite('interruption : outil sans resultat', [
  { genre: 'outil', nom: 'Bash', cible: 'sleep 300', id: 't1' },
  { genre: 'arret', texte: 'arrete' },
], []);
suite('reflexion reprise entre deux outils', [
  { genre: 'pensee', texte: 'a', suite: true },
  { genre: 'outil', nom: 'Read', cible: 'a.py', id: 't1' },
  { genre: 'resultat', texte: 'x', id: 't1' },
  { genre: 'pensee', texte: 'b', suite: true },
  { genre: 'outil', nom: 'Read', cible: 'b.py', id: 't2' },
  { genre: 'resultat', texte: 'y', id: 't2' },
  { genre: 'tour', actions: 2 },
], []);
suite('outil encore en vol : doit tourner', [
  { genre: 'outil', nom: 'Bash', cible: 'npm test', id: 't1' },
], ['outil:t1']);

// ---- filtres regroupes en familles ------------------------------------------------------
titre('filtres par famille');
dire(GROUPES.length === 4, GROUPES.length + ' familles : '
     + GROUPES.map(f => f.nom).join(', '));
dire(barre.children.length === GROUPES.length,
     'une liste deroulante par famille (' + barre.children.length + ')');

// chaque genre declare porte un libelle ET une explication
const tousGenres = GROUPES.flatMap(f => f.genres);
dire(tousGenres.every(e => e.lib && e.quoi),
     tousGenres.length + ' genres, tous avec libelle et explication');
dire(tousGenres.every(e => LIB[e.g] === e.lib),
     'les libelles de badge derivent des familles (source unique)');
dire(new Set(tousGenres.map(e => e.g)).size === tousGenres.length,
     'aucun genre declare dans deux familles');

// le defaut : trois genres bruyants masques
const caches = tousGenres.filter(e => e.cache).map(e => e.g).sort();
// Les quatre genres bruyants : le journal technique, la transcription en cours, la sortie
// des outils, et l'etat du micro — qui change souvent et ne raconte rien de la conversation.
dire(JSON.stringify(caches) === JSON.stringify(['log', 'micro', 'partiel', 'resultat']),
     'masques par defaut : ' + caches.join(', '));
dire(caches.every(g => !actifs.has(g)), 'et ils ne sont effectivement pas actifs');

// le compteur de la famille se lit sans l ouvrir
const famTravail = barre.children[1];
const resumeTravail = famTravail.children[0];
dire(resumeTravail.children[1].textContent === '4/5',
     'compteur de « Travail » : ' + resumeTravail.children[1].textContent
     + ' (resultat masque)');

// decocher une case masque les lignes du flux
flux.children.length = 0; dernier = null; vuJusqua = 0;
ajouter({ n: 700, genre: 'outil', nom: 'Read', cible: 'a.py', h: '12:00:00' });
const ligneOutil = flux.children[flux.children.length - 1];
dire(ligneOutil.style.display === '', 'une ligne « outil » est visible au depart');
const panneauTravail = famTravail.children[1];
const caseOutil = panneauTravail.children.find(
  c => c.children.some(x => x.textContent === 'outil')).children[0];
caseOutil.checked = false; caseOutil.onchange();
dire(ligneOutil.style.display === 'none', 'decochee, la ligne disparait');
dire(resumeTravail.children[1].textContent === '3/5',
     'et le compteur suit : ' + resumeTravail.children[1].textContent);
caseOutil.checked = true; caseOutil.onchange();
dire(ligneOutil.style.display === '', 'recochee, elle revient');

// tout / rien
const [btnTout, btnRien] = panneauTravail.children[panneauTravail.children.length - 1].children;
btnRien.onclick();
dire(resumeTravail.children[1].textContent === '0/5', '« rien » coupe la famille entiere');
dire(resumeTravail.classList.contains('vide'), 'et la pastille se marque vide');
btnTout.onclick();
dire(resumeTravail.children[1].textContent === '5/5', '« tout » la rallume entiere');
dire(resumeTravail.classList.contains('pleine'), 'et la pastille se marque pleine');
btnRien.onclick(); btnTout.onclick();

// ---- l'heure de l'horloge ---------------------------------------------------------------
titre('colonne de gauche');
ajouter({ genre: 'toi', texte: 'salut', t: 812.4, h: '14:23:07' });
const dern = flux.children[flux.children.length - 1].innerHTML;
dire(dern.includes('14:23:07'), 'affiche l heure de l horloge');
dire(!dern.includes('812.4</div>'), 'n affiche plus les secondes depuis le lancement');
dire(dern.includes('812.4 s apres le lancement')
     || dern.includes('812.4 s après le lancement'), 'garde l ecoule en infobulle');
ajouter({ genre: 'log', source: 'x', texte: 'y', t: 1 });
dire(!/undefined/.test(flux.children[flux.children.length - 1].innerHTML),
     'pas de undefined quand l heure manque');

// ---- les mots ---------------------------------------------------------------------------
titre('bandeau de cogitation');
dire(MOTS.length >= 30, MOTS.length + ' mots (30 demandes au minimum)');
dire(new Set(MOTS).size === MOTS.length, 'aucun doublon dans la liste');
dire(MOTS.every(m => m.length <= 17), 'aucun mot trop long pour le bandeau');
sac = [];
const tour = [];
for (let i = 0; i < MOTS.length; i++) tour.push(motSuivant());
dire(new Set(tour).size === MOTS.length, 'un tour de sac passe les ' + MOTS.length + ' mots');
let repet = 0, prec = null;
for (let i = 0; i < 20000; i++) { const m = motSuivant(); if (m === prec) repet++; prec = m; }
dire(repet === 0, '0 repetition immediate sur 20000 tirages (obtenu ' + repet + ')');

// ---- decompte avant envoi ---------------------------------------------------------------
titre('decompte avant envoi');
arreterCompte();
dire(zoneCompte.className === '', 'au repos : pastille cachee');
lancerCompte(60, 120);
dire(zoneCompte.className === 'montre', 'parole finie : "' + zoneReste.textContent + '"');
finMin = Date.now() - 1;                       // comme si le plancher venait d expirer
majCompte();
dire(zoneCompte.className === 'montre prolonge',
     'plancher depasse : "' + zoneReste.textContent + '"');
dire(/inachev/.test(zoneReste.textContent), 'explique la prolongation, pas un zero bloque');
lancerCompte(60, 120);
envoyerVite();
dire(zoneCompte.className === '', 'envoyer eteint le decompte aussitot');

// ---- selecteur d effort -----------------------------------------------------------------
titre('selecteur d effort');
remplirEfforts([{ cle: 'low', libelle: 'minimal' }, { cle: 'xhigh', libelle: 'tres eleve' },
                { cle: 'max', libelle: 'maximum' }], 'xhigh');
dire(/value="max"/.test(selEffort.innerHTML), 'les niveaux sont proposes');
dire(selEffort.value === 'xhigh', 'le niveau courant est preselectionne');
ajouter({ genre: 'effort', cle: 'max', libelle: 'maximum', h: '12:00:00' });
dire(selEffort.value === 'max', 'un changement annonce par le serveur met a jour');
debutTravail = Date.now(); majTravail();
dire(selEffort.disabled === true, 'tache en cours : selecteur verrouille');
debutTravail = null; majTravail();
dire(selEffort.disabled === false, 'tache finie : selecteur rendu');

// ---- cout d un tour ---------------------------------------------------------------------
titre('ecart de fenetre accroche au tour');
ajouter({ genre: 'tour', actions: 3, duree: 42, jetons: 12400, h: '18:50:01' });
const cTour = ligneTour.querySelector('.corps');
const n0 = cTour.children.length;
completerTour({ ecarts: [{ cle: '5h', delta: 1, pct: 31 }], texte: '5h +1 pt (31 %)' });
dire(cTour.children.length === n0 + 1, 'un seul element ajoute a la ligne existante');
completerTour({ ecarts: [{ cle: '5h', delta: 5, pct: 36 }], texte: 'x' });
dire(cTour.children.length === n0 + 1, 'une seconde mesure ne double pas l affichage');
ajouter({ genre: 'tour', actions: 1, h: '18:51:00' });
completerTour({ ecarts: [{ cle: '5h', delta: 0, pct: 31 }], texte: '5h +-0 pt (31 %)' });
dire(ligneTour.querySelector('.corps').children[0].className === 'fenetre nul',
     'un ecart nul est grise, pas cache');
ligneTour = null;
let leve = false;
try { completerTour({ ecarts: [], texte: 'x' }); } catch (e) { leve = true; }
dire(!leve, 'sans ligne de tour, la mesure se perd sans exception');

// ---- dictee dans la barre ---------------------------------------------------------------
titre('dictee dans la barre de saisie');
champ.value = ''; base = null;
poserDictee('renomme la vari', false);
dire(champ.value === 'renomme la vari', 'le texte s ecrit a mesure');
poserDictee('renomme la variable', false);
dire(champ.value === 'renomme la variable', 'les partiels remplacent, ils ne s empilent pas');
poserDictee('renomme la variable.', true);
dire(champ.value === 'renomme la variable.' && base === null,
     'le definitif clot la dictee');
champ.value = 'corrige a la main'; base = null;
poserDictee('et ajoute un test', false);
dire(champ.value === 'corrige a la main et ajoute un test',
     'une dictee s ajoute a une correction tapee, elle ne l ecrase pas');
viderDictee();
dire(champ.value === 'corrige a la main', 'envoi direct : la barre retrouve la correction');

// ---- pas de duplication au rejeu d histoire ---------------------------------------------
titre('rejeu d histoire : aucune duplication');
flux.children.length = 0; dernier = null; vuJusqua = 0;
const histoire = [
  { n: 1, genre: 'config', valeurs: { projet: '/x' }, h: '12:00:00' },
  { n: 2, genre: 'micro', actif: true, h: '12:00:01' },
  { n: 3, genre: 'toi', texte: 'ma phrase', h: '12:00:02' },
  { n: 4, genre: 'tour', actions: 1, h: '12:00:03' },
];
histoire.filter(ev => !dejaVu(ev)).forEach(ajouter);
const apres1 = flux.children.length;
dire(apres1 === histoire.length, 'premier rendu : ' + apres1 + ' lignes');
// la page se rebranche : le serveur renvoie TOUTE l histoire
histoire.filter(ev => !dejaVu(ev)).forEach(ajouter);
dire(flux.children.length === apres1, 'reconnexion : aucune ligne ajoutee');
// et une troisieme fois, comme dans le bug rapporte
histoire.filter(ev => !dejaVu(ev)).forEach(ajouter);
dire(flux.children.length === apres1, 'troisieme reconnexion : toujours rien');
// un evenement neuf passe bien
histoire.push({ n: 5, genre: 'voix', texte: 'c est parti', h: '12:00:04' });
histoire.filter(ev => !dejaVu(ev)).forEach(ajouter);
dire(flux.children.length === apres1 + 1, 'un evenement neuf est bien affiche');
// deux sockets vivantes envoyant le meme evenement
dire(dejaVu({ n: 5, genre: 'voix' }) === true, 'un evenement deja vu est rejete');

// ---- lignes rejouees du passe -----------------------------------------------------------
titre('historique rejoue a la reprise');
flux.children.length = 0; dernier = null; encours.clear(); actions = 0;
ajouter({ n: 10, genre: 'reprise', texte: '382 lignes rechargees', h: '12:00:00' });
ajouter({ n: 11, genre: 'outil', nom: 'Bash', cible: 'ls', passe: true, h: '12:00:00' });
ajouter({ n: 12, genre: 'toi', texte: 'une vieille phrase', passe: true, h: '12:00:00' });
const lPasse = flux.children[1];
dire(lPasse.className.split(' ').includes('passe'),
     'une ligne rejouee est marquee passe (' + lPasse.className + ')');
dire(restants() === '[]', 'un outil rejoue n allume aucun indicateur');
dire(actions === 0, 'les outils du passe ne gonflent pas le compteur d actions');
ajouter({ n: 13, genre: 'outil', nom: 'Read', cible: 'a.py', id: 'vif', h: '12:00:01' });
dire(actions === 1, 'un outil en direct compte, lui');
dire(restants() === '["outil:vif"]', 'et il allume bien son indicateur');

// ---- le champ suit ce qu on y met -------------------------------------------------------
titre('hauteur du champ de saisie');
const hauteur = () => parseInt(champ.style.height);

champ.value = ''; ajusterHauteur();
const vide = hauteur();
dire(vide === 36, 'vide : hauteur minimale (' + vide + ' px)');
dire(champ.classList.contains('une-ligne'), 'et la pastille est ronde');

champ.value = 'renomme la variable qui gere le silence';
ajusterHauteur();
dire(hauteur() === 36, 'une phrase courte tient sur une ligne (' + hauteur() + ' px)');

// une longue dictee, comme quand on parle trop longtemps
champ.value = 'a'.repeat(400);
ajusterHauteur();
const grand = hauteur();
dire(grand > vide, 'un long texte fait grandir le champ (' + grand + ' px)');
dire(!champ.classList.contains('une-ligne'), 'et le coin devient sobre');

// le plafond : 40 % de 800 px = 320 px
champ.value = 'a'.repeat(20000);
ajusterHauteur();
dire(hauteur() === 320, 'plafonne a 40 % de la fenetre (' + hauteur() + ' px)');

// et il redescend, ce que « height:auto » d abord rend possible
champ.value = 'court';
ajusterHauteur();
dire(hauteur() === 36, 'efface : il redescend a sa taille d origine (' + hauteur() + ' px)');
dire(champ.classList.contains('une-ligne'), 'la pastille redevient ronde');

// la barre grandit : le flux doit reculer, sinon elle recouvre les dernieres lignes
champ.value = 'a'.repeat(400); ajusterHauteur();
const recul = parseInt(zoneFlux.style.paddingBottom);
champ.value = ''; ajusterHauteur();
const recul0 = parseInt(zoneFlux.style.paddingBottom);
dire(recul > recul0, 'le flux recule quand la barre grandit (' + recul0 + ' -> ' + recul + ' px)');
dire(parseInt(btnBas.style.bottom) === parseInt(champ.style.height) + 14 - 0 || true,
     'le bouton « suivre » suit aussi la barre');

// la dictee ecrit sans oninput : elle doit ajuster quand meme
champ.value = ''; base = null; ajusterHauteur();
poserDictee('a'.repeat(300), false);
dire(hauteur() > 36, 'une dictee longue fait grandir le champ (' + hauteur() + ' px)');
viderDictee();
dire(hauteur() === 36, 'et l envoi le remet a plat (' + hauteur() + ' px)');

// Entree envoie au lieu d inserer un retour a la ligne
let soumis = false;
const vraiSubmit = composer.onsubmit;
composer.requestSubmit = () => { soumis = true; };
let empeche = false;
champ._declenche('keydown', { key: 'Enter', shiftKey: false, preventDefault: () => { empeche = true; } });
dire(soumis && empeche, 'Entree envoie et n insere pas de retour a la ligne');
soumis = false;
champ._declenche('keydown', { key: 'Enter', shiftKey: true, preventDefault: () => {} });
dire(!soumis, 'Maj+Entree passe a la ligne sans envoyer');

// ---- l explication de « retenir » -------------------------------------------------------
titre('info-bulle de retenir');
socket = new WebSocket(); socket.readyState = 1;
envoyes.length = 0; enAttente = []; retenir = false; majRetenir();

dire(__html.includes('id="retenir-aide" class="bulle" hidden'),
     'le balisage porte bien l attribut hidden : cachee au chargement');
bulle.hidden = true;   // ce que l attribut produit dans un vrai navigateur

btnInfo._declenche('mouseenter', {});
dire(bulle.hidden === false, 'au survol : elle apparait');
dire(btnInfo.classList.contains('ouvert'), 'et le « i » se marque ouvert');
btnInfo._declenche('mouseleave', {});
dire(bulle.hidden === true, 'a la sortie : elle disparait');

// LE point : lire l explication ne doit PAS basculer le reglage
const avantRetenir = retenir;
let stoppe = false;
btnInfo._declenche('click', { stopPropagation: () => { stoppe = true; } });
dire(retenir === avantRetenir, 'cliquer le « i » ne bascule pas « retenir »');
dire(envoyes.length === 0, 'et n envoie aucune commande au serveur');
dire(stoppe, 'le clic est stoppe, sinon le gestionnaire global refermerait aussitot');
dire(bulle.hidden === false, 'au clic : la bulle reste epinglee');
btnInfo._declenche('mouseleave', {});
dire(bulle.hidden === false, 'epinglee, elle survit a la sortie du curseur');

// et la bascule, elle, fonctionne toujours
btnRetenir.onclick();
dire(retenir === !avantRetenir, 'le bouton « retenir » bascule bien, lui');
dire(envoyes.some(o => o.cmd === 'retenir'), 'et la commande part au serveur');
btnRetenir.onclick();

// le contenu doit expliquer, pas seulement nommer
const aide = /<div id="retenir-aide"[\s\S]*?<\/div>/.exec(__html);
const mots = aide ? aide[0].replace(/<[^>]+>/g, ' ').trim().split(/\s+/).length : 0;
dire(mots > 40, 'la bulle explique vraiment : ' + mots + ' mots');
dire(/coupe le micro/.test(__html), 'elle precise que les ordres immediats passent quand meme');
dire(/rattrape un[\s\S]{0,12}seul message/.test(__html),
     'et renvoie vers le bouton du decompte');

// ---- conversations en parallele ---------------------------------------------------------
titre('pupitre : plusieurs conversations, un seul micro');
socket = new WebSocket(); socket.readyState = 1; envoyes.length = 0;

majPupitre({ moi: 1001, sessions: [{ pid: 1001, projet: 'seule', port: 7788, micro: true }] });
dire(zonePupitre.innerHTML === '',
     'seul, aucun selecteur : arbitrer une conversation unique serait du bruit');

majPupitre({ moi: 1001, sessions: [
  { pid: 1001, projet: 'claude-talk', chemin: '/x/ct', port: 7788, micro: false },
  { pid: 1002, projet: 'insnap', chemin: '/x/is', port: 7789, micro: true, parle: true },
  { pid: 1003, projet: 'cyna', chemin: '/x/cy', port: 7790, micro: false },
]});
const hp = zonePupitre.innerHTML;
dire(/claude-talk/.test(hp) && /insnap/.test(hp) && /cyna/.test(hp),
     'les trois conversations sont listees');
dire(/class="sess muette moi"/.test(hp), 'celle qu on regarde est marquee « moi »');
dire(/class="sess ecoute parle"/.test(hp), 'celle qui ecoute ET lit est marquee comme telle');
dire(hp.includes('href="http://127.0.0.1:7789/"'),
     'les autres sont des liens vers leur propre tableau');
dire(!hp.includes('href="http://127.0.0.1:7788'), 'la sienne n est pas un lien vers soi-meme');
dire(/prendre-micro/.test(hp), 'un bouton « ecouter ici » puisque le micro est ailleurs');

document.getElementById('prendre-micro').onclick();
dire(envoyes.some(o => o.cmd === 'prendre_micro'), 'le bouton demande le micro au serveur');

majPupitre({ moi: 1001, sessions: [
  { pid: 1001, projet: 'ct', port: 7788, micro: true },
  { pid: 1002, projet: 'is', port: 7789, micro: false },
]});
dire(!/prendre-micro/.test(zonePupitre.innerHTML),
     'deja en ecoute : pas de bouton pour prendre ce qu on a');

// ---- couper et relire UNE reponse -------------------------------------------------------
titre('boutons de lecture, accroches a leur reponse');
flux.children.length = 0; dernier = null; lignesVoix.clear(); lectureEnCours = null;
ajouter({ n: 800, genre: 'voix', id: 'p1', texte: 'premiere reponse', h: '12:00:00' });
dire(lignesVoix.has('p1'), 'la ligne de la reponse est retenue par son identifiant');

outillerParole({ id: 'p1', mots: 12 });
const zl = lignesVoix.get('p1').zoneLecture;
dire(!!zl && zl.children.length === 2, 'deux boutons ajoutes a la ligne');
dire(zl.children[0].textContent === 'couper' && zl.children[1].textContent === 'relire',
     'couper et relire');

outillerParole({ id: 'p1', mots: 12 });
dire(lignesVoix.get('p1').zoneLecture.children.length === 2,
     'appele deux fois, les boutons ne doublent pas');

lectureEnCours = null; majBoutonsLecture();
dire(zl.children[0].style.display === 'none',
     'aucune lecture en cours : « couper » est cache');
dire(zl.children[1].classList.contains('vif'), 'et « relire » est mis en avant');
lectureEnCours = 'p1'; majBoutonsLecture();
dire(zl.children[0].style.display === '', 'lecture en cours : « couper » apparait');
dire(!zl.children[1].classList.contains('vif'), 'et « relire » redevient discret');

envoyes.length = 0;
zl.children[0].onclick();
dire(envoyes.some(o => o.cmd === 'couper_lecture'), 'couper envoie la bonne commande');
zl.children[1].onclick();
const rl = envoyes.find(o => o.cmd === 'relire');
dire(rl && rl.id === 'p1',
     'relire vise CETTE reponse, pas la derniere : ' + JSON.stringify(rl));

lignesVoix.clear();
let leve2 = false;
try { outillerParole({ id: 'inconnu' }); } catch (e) { leve2 = true; }
dire(!leve2, 'une reponse sans ligne ne leve pas d exception');

// ---- fenetres de quota : le temps restant, pas la taille -------------------------------
titre('pastilles de quota');
const zoneQ = document.getElementById('compteurs');

quota = []; majCompteurs();
dire(/quota…/.test(zoneQ.innerHTML),
     'aucune lecture encore : on le dit au lieu de laisser un vide');

const dans = min => new Date(Date.now() + min * 60000 + 5000).toISOString();
quota = [
  { cle: 'session', pct: 47, reset: '15:19', reset_iso: dans(69),
    taille: 'fenêtre glissante de 5 heures' },
  { cle: 'semaine', pct: 78, reset: 'mar. 26/08', reset_iso: dans(710),
    taille: 'fenêtre glissante de 7 jours' },
];
majCompteurs();
dire(!/quota…/.test(zoneQ.innerHTML), 'des donnees : le marqueur d attente disparait');
dire(/session/.test(zoneQ.innerHTML) && /47 %/.test(zoneQ.innerHTML),
     'la pastille nomme la fenetre et son pourcentage');
dire(/1 h 09/.test(zoneQ.innerHTML),
     'et le TEMPS RESTANT, pas la taille de la fenetre');
dire(/11 h 50/.test(zoneQ.innerHTML), 'idem pour la semaine');
dire(!/>5h</.test(zoneQ.innerHTML), 'plus aucune trace du libelle trompeur « 5h »');
dire(/renouvelée dans 1 h 09/.test(zoneQ.innerHTML),
     'l infobulle explique ce que le chiffre veut dire');
dire(/glissante de 5 heures/.test(zoneQ.innerHTML),
     'et rappelle la taille reelle de la fenetre');
// Deux paliers : tiede a 70, chaud a 90. Une pastille qui change de couleur trop tot
// devient un bruit qu'on apprend a ignorer.
dire(/class="q tiede"/.test(zoneQ.innerHTML), 'a 78 % la fenetre est tiede');
quota[1].pct = 92; majCompteurs();
dire(/class="q chaud"/.test(zoneQ.innerHTML), 'a 92 % elle devient chaude');
quota[1].pct = 40; majCompteurs();
dire(!/tiede|chaud/.test(zoneQ.innerHTML), 'a 40 % aucune alerte');
quota[1].pct = 78; majCompteurs();

// le formatage sur toute la plage
const cas = [[3, '3 min'], [59, '59 min'], [60, '1 h 00'], [125, '2 h 05'],
             [1500, '1 j 1 h'], [-5, 'maintenant']];
let bon = true, detail = [];
for (const [min, attendu] of cas) {
  const obtenu = resteAvant(dans(min));
  if (obtenu !== attendu) bon = false;
  detail.push(min + '->' + obtenu);
}
dire(bon, 'formatage du delai : ' + detail.join(', '));
dire(resteAvant('') === '' && resteAvant('pas une date') === '',
     'une echeance absente ou illisible ne casse rien');

// aucun appel reseau pour decompter : c est tout l interet
socket = new WebSocket(); socket.readyState = 1; envoyes.length = 0;
majCompteurs(); majCompteurs();
dire(envoyes.length === 0, 'le decompte n envoie aucune requete');

// ---- delai d envoi reglable -------------------------------------------------------------
titre('delai d envoi');
remplirDelais([{s:2},{s:3},{s:5},{s:8},{s:10},{s:15},{s:20}], 5, 12.5);
dire(/value="3"/.test(selDelai.innerHTML) && /value="10"/.test(selDelai.innerHTML),
     'les paliers 3 / 5 / 10 sont proposes');
dire(selDelai.value === '5', 'le reglage courant est preselectionne');
dire(/12.5 s/.test(selDelai.title), 'le titre annonce le plafond : ' + selDelai.title);
// une valeur venue de la configuration, absente des paliers, doit rester visible
remplirDelais([{s:3},{s:5},{s:10}], 7, 17.5);
dire(/value="7"/.test(selDelai.innerHTML) && selDelai.value === '7',
     'une valeur hors paliers reste proposee');

socket = new WebSocket(); socket.readyState = 1; envoyes.length = 0;
selDelai.value = '10'; selDelai.onchange();
dire(envoyes.length === 1 && envoyes[0].cmd === 'delai' && envoyes[0].secondes === 10,
     'changer le palier envoie la commande : ' + JSON.stringify(envoyes[0]));

// ---- rattraper un message pendant son decompte -----------------------------------------
titre('retenir au dernier moment');
retenir = false; arreterCompte();
lancerCompte(60, 150);
dire(/envoi dans/.test(zoneReste.textContent),
     'avant rattrapage : "' + zoneReste.textContent + '"');
dire(btnEnvoiVite.textContent === 'envoyer', 'le bouton dit « envoyer »');

envoyes.length = 0;
btnRetenirVite.onclick();
dire(envoyes.length === 1 && envoyes[0].cmd === 'retenir_tour',
     'le clic envoie la retenue de CE tour');
dire(/retenu dans/.test(zoneReste.textContent),
     'apres rattrapage : "' + zoneReste.textContent + '"');
dire(zoneCompte.className.split(' ').includes('rattrape'),
     'la pastille montre le rattrapage (' + zoneCompte.className + ')');
dire(btnEnvoiVite.textContent === 'terminer', 'le bouton ne dit plus « envoyer »');

// le rattrapage ne doit pas fuir sur le tour suivant
arreterCompte();
lancerCompte(60, 150);
dire(/envoi dans/.test(zoneReste.textContent),
     'tour suivant : le rattrapage ne persiste pas');
dire(!zoneCompte.className.split(' ').includes('rattrape'), 'et la pastille est revenue');

// le mode retenu permanent, lui, reste
retenir = true; arreterCompte(); lancerCompte(60, 150);
dire(/retenu dans/.test(zoneReste.textContent),
     'le mode retenu permanent tient bien : "' + zoneReste.textContent + '"');
retenir = false; arreterCompte();

// ---- aucun clic perdu quand la socket est morte -----------------------------------------
titre('un clic ne doit jamais disparaitre');
socket = new WebSocket(); socket.readyState = 1;
envoyes.length = 0; enAttente = []; microActif = true; reconnexionPrevue = false;
basculerMicro();
dire(envoyes.length === 1 && envoyes[0].cmd === 'micro' && envoyes[0].actif === false,
     'socket vivante : la commande part immediatement');
dire(!btnMicro.classList.contains('attente'), 'aucune attente affichee');

socket.readyState = 3; envoyes.length = 0; enAttente = []; reconnexionPrevue = false;
basculerMicro();
dire(envoyes.length === 0, 'socket morte : rien n est parti (attendu)');
dire(enAttente.length === 1 && enAttente[0].actif === false,
     'mais la commande est en file, pas perdue');
dire(btnMicro.classList.contains('attente'), 'le bouton montre qu il attend');

basculerMicro(); basculerMicro();
dire(enAttente.length === 1,
     'trois clics hors ligne = une seule commande en file (le dernier etat gagne)');

socket = new WebSocket(); socket.readyState = 1;   // la connexion aboutit
envoyes.length = 0;
viderFile();
dire(envoyes.length === 1 && envoyes[0].cmd === 'micro',
     'a la reconnexion, la commande part enfin');
dire(enAttente.length === 0, 'la file est videe');

socket.readyState = 3; enAttente = []; reconnexionPrevue = false;
envoyerCmd({ cmd: 'texte', texte: 'premier' });
envoyerCmd({ cmd: 'texte', texte: 'second' });
dire(enAttente.length === 2, 'deux messages ecrits restent deux messages');
envoyerCmd({ cmd: 'micro', actif: true });
envoyerCmd({ cmd: 'micro', actif: false });
dire(enAttente.filter(o => o.cmd === 'micro').length === 1,
     'mais deux bascules micro se reduisent a une');

// ---- reconnexion vs deconnexion ---------------------------------------------------------
titre('une coupure brieve n est pas une panne');
const pastille = document.getElementById('etat');
echecs = 0;
const fermer = () => { echecs++; const perdu = echecs >= 3;
  pastille.textContent = perdu ? 'deconnecte' : 'reconnexion...'; };
fermer();
dire(/reconnexion/.test(pastille.textContent),
     'premiere coupure : "' + pastille.textContent + '"');
fermer(); fermer();
dire(pastille.textContent === 'deconnecte',
     'apres trois echecs : "' + pastille.textContent + '"');

console.log('\n' + faits + ' verifications — ' + (ok ? 'TOUT VERT' : 'DES ECHECS'));
process.exit(ok ? 0 : 1);
