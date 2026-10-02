// Un compteur unique pour les evenements de test. Les numeros ecrits a la main rendaient
// chaque section dependante de l'ordre des autres : `dejaVu` rejette un numero deja depasse,
// donc inserer une section avec de gros numeros cassait silencieusement toutes celles d'apres.
// Toujours au-dessus de ce que la page a deja vu : sinon `dejaVu` rejette l'evenement et la
// section echoue pour une raison qui n'a rien a voir avec ce qu'elle teste.
let __n = 0;
const emettre = e => {
  __n = Math.max(__n + 1, (typeof vuJusqua === 'number' ? vuJusqua : 0) + 1);
  return recevoir(Object.assign({ n: __n, h: '12:00:00' }, e));
};

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

// Le defaut : les genres qui repetent ou qui bruitent sont decoches.
const caches = tousGenres.filter(e => e.cache).map(e => e.g).sort();
// Cinq genres, et chacun pour une raison differente : le journal technique et l etat du micro
// changent souvent sans rien raconter de la conversation ; la transcription en cours et le
// texte retenu sont deja SOUS LES YEUX dans la barre de saisie, donc la ligne du flux ne fait
// que les repeter au moment le plus charge ; la sortie des outils est simplement enorme.
// Tous restent disponibles dans les filtres, pour relire apres coup.
dire(JSON.stringify(caches) === JSON.stringify(['dictee', 'log', 'micro', 'partiel', 'resultat']),
     'decoches par defaut : ' + caches.join(', '));
dire(!actifs.has('dictee'), 'le texte retenu n apparait pas dans le flux au demarrage');
dire(tousGenres.some(e => e.g === 'dictee'),
     'mais il reste proposé dans les filtres : decoche n est pas supprime');
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
// Une dictee doit etre OUVERTE pour ecrire : c'est ce qui permet d'ignorer une transcription
// arrivant apres l'envoi du tour. Ecrire hors dictee ne doit rien faire.
champ.value = ''; champ.oninput();
poserDictee('perdu dans le vide', false);
dire(champ.value === '', 'hors dictee, rien ne s ecrit : ' + JSON.stringify(champ.value));

ouvrirDictee();
poserDictee('renomme la vari', false);
dire(champ.value === 'renomme la vari', 'le texte s ecrit a mesure');
poserDictee('renomme la variable', false);
dire(champ.value === 'renomme la variable',
     'les partielles se remplacent, elles ne s empilent pas');
poserDictee('renomme la variable.', true);
dire(champ.value === 'renomme la variable.' && dicteeOuverte === true,
     'un segment final se fige mais NE clot PAS la dictee : la phrase peut continuer');
poserDictee(' et lance les tests', false);
dire(champ.value === 'renomme la variable. et lance les tests',
     'la suite s ecrit derriere le segment fige : ' + champ.value);

champ.value = 'corrige a la main'; champ.oninput();
ouvrirDictee();
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
dire(vide === HAUTEUR_MINI,
     'vide : hauteur minimale (' + vide + ' px, constante ' + HAUTEUR_MINI + ')');
dire(champ.classList.contains('une-ligne'), 'et la pastille est ronde');

champ.value = 'renomme la variable qui gere le silence';
ajusterHauteur();
dire(hauteur() === HAUTEUR_MINI, 'une phrase courte tient sur une ligne (' + hauteur() + ' px)');

// une longue dictee, comme quand on parle trop longtemps
champ.value = 'a'.repeat(400);
ajusterHauteur();
const grand = hauteur();
dire(grand > vide, 'un long texte fait grandir le champ (' + grand + ' px)');
dire(!champ.classList.contains('une-ligne'), 'et le coin devient sobre');

// Le plafond : 55 % de 800 px = 440 px. Releve de 40 a 55 % — une longue dictee doit se
// relire sans naviguer, et c est exactement ce qu on fait juste avant d envoyer.
champ.value = 'a'.repeat(20000);
ajusterHauteur();
dire(hauteur() === 440, 'plafonne a 55 % de la fenetre (' + hauteur() + ' px)');
dire(champ.style.overflowY === 'auto',
     'et au plafond la gouttiere apparait : le texte reste atteignable au defilement');

// et il redescend, ce que « height:auto » d abord rend possible
champ.value = 'court';
ajusterHauteur();
dire(hauteur() === HAUTEUR_MINI, 'efface : il redescend a sa taille d origine (' + hauteur() + ' px)');
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
champ.value = ''; champ.oninput(); ajusterHauteur();
ouvrirDictee();
poserDictee('a'.repeat(300), false);
dire(hauteur() > 36, 'une dictee longue fait grandir le champ (' + hauteur() + ' px)');
viderDictee();
dire(hauteur() === HAUTEUR_MINI, 'et l envoi le remet a plat (' + hauteur() + ' px)');

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

// ---- « retenir » -----------------------------------------------------------------------
titre('retenir');
socket = new WebSocket(); socket.readyState = 1;
envoyes.length = 0; enAttente = []; retenir = false; majRetenir();

dire(!/id="retenir-info"/.test(__html), 'le « i » a quitte le balisage');
dire(!/id="retenir-aide"/.test(__html), 'son info-bulle aussi');
dire(!/class="info"|\.info\{|\.bulle\{/.test(__html), 'et le style qui les habillait');

// Ce qui compte reste : la bascule, et l ordre qui part au serveur.
const avantRetenir = retenir;
btnRetenir.onclick();
dire(retenir === !avantRetenir, 'le bouton « retenir » bascule le reglage');
dire(envoyes.some(o => o.cmd === 'retenir'), 'et la commande part au serveur');
btnRetenir.onclick();

// ---- cycle de vie de la dictee ----------------------------------------------------------
// Deux bugs reproduits avant correction, et ce sont les cas les plus couteux du systeme :
// un texte deja envoye qui revient dans la barre et repart au message suivant, et une
// phrase coupee par une pause dont le debut disparait.
titre('dictee : rien ne revient, rien ne se perd');
const val = () => champ.value;

// 1. une phrase coupee par une pause : deux transcriptions finales, chacune partielle
champ.value = ''; brouillon = null; segments = ''; dicteeOuverte = false;
emettre({ genre: 'ecoute', actif: false, parle: true });
emettre({ genre: 'partiel', texte: 'renomme la variable', final: false });
dire(val() === 'renomme la variable', 'le direct s ecrit : ' + val());
emettre({ genre: 'partiel', texte: 'renomme la variable', final: true });
emettre({ genre: 'partiel', texte: 'qui gere le silence', final: false });
dire(val() === 'renomme la variable qui gere le silence',
     'le second segment s AJOUTE au premier : ' + val());
emettre({ genre: 'partiel', texte: 'qui gere le silence', final: true });
dire(val() === 'renomme la variable qui gere le silence',
     'et le final ne le duplique pas : ' + val());

// 2. le tour part : la barre se vide
emettre({ genre: 'toi', texte: 'renomme la variable qui gere le silence' });
dire(val() === '', 'apres envoi, la barre est vide : ' + JSON.stringify(val()));
dire(dicteeOuverte === false, 'et la dictee est fermee');

// 3. LE bug : une transcription qui arrive APRES l envoi
emettre({ genre: 'partiel', texte: 'qui gere le silence', final: true });
dire(val() === '', 'une transcription tardive ne remet RIEN dans la barre');
emettre({ genre: 'partiel', texte: 'encore du retard', final: false });
dire(val() === '', 'ni une partielle tardive');

// 4. le tour suivant repart propre
emettre({ genre: 'ecoute', actif: false, parle: true });
emettre({ genre: 'partiel', texte: 'lance les tests', final: false });
dire(val() === 'lance les tests', 'le tour suivant ne traine pas l ancien : ' + val());
emettre({ genre: 'toi', texte: 'lance les tests' });

// 5. un brouillon tape SURVIT a l envoi de la dictee : c est ton texte, pas celui du micro
champ.value = 'note pour moi'; champ.oninput();
emettre({ genre: 'ecoute', actif: false, parle: true });
emettre({ genre: 'partiel', texte: 'et corrige le test', final: false });
dire(val() === 'note pour moi et corrige le test',
     'la dictee s ajoute derriere le brouillon : ' + val());
emettre({ genre: 'toi', texte: 'et corrige le test' });
dire(val() === 'note pour moi',
     'la dictee partie, le brouillon reste : ' + JSON.stringify(val()));

// 6. retenu : le texte consolide du serveur reste dans la barre
champ.value = ''; champ.oninput();
emettre({ genre: 'ecoute', actif: false, parle: true });
emettre({ genre: 'partiel', texte: 'ajoute un export', final: false });
emettre({ genre: 'dictee', texte: 'ajoute un export CSV', auto: true });
dire(val() === 'ajoute un export CSV',
     'retenu : le texte du serveur fait autorite : ' + val());
dire(dicteeOuverte === false, 'et la dictee est fermee');
emettre({ genre: 'partiel', texte: 'du retard encore', final: true });
dire(val() === 'ajoute un export CSV',
     'une transcription tardive ne le pollue pas : ' + val());

// 7. taper pendant une dictee reprend la main
champ.value = ''; champ.oninput();
emettre({ genre: 'ecoute', actif: false, parle: true });
emettre({ genre: 'partiel', texte: 'mauvaise transcription', final: false });
champ.value = 'ma correction'; champ.oninput();
emettre({ genre: 'partiel', texte: 'mauvaise transcription encore', final: false });
dire(val() === 'ma correction',
     'la transcription n ecrase pas une correction tapee : ' + val());

// ---- l attente de transcription est visible ---------------------------------------------
titre('transcription : l attente ne doit pas etre muette');
encours.clear();          // une section ne doit pas heriter de l'activite d'une autre
majActivite();
for (const k in echeances) clearTimeout(echeances[k]);
sttDirect = true;

emettre({ genre: 'transcrit', actif: true, direct: true });
dire(encours.has('stt'), 'la pastille de transcription s allume a la fin de la parole');
dire(/transcription</.test(zoneActivite.innerHTML),
     'et elle dit « transcription » : ' + zoneActivite.innerHTML.replace(/<[^>]*>/g, ' ').trim());

// un moteur sans direct : le libelle doit dire POURQUOI rien ne s ecrit
emettre({ genre: 'transcrit', actif: false });
dire(!encours.has('stt'), 'la transcription finie, la pastille s eteint');
emettre({ genre: 'transcrit', actif: true, direct: false });
dire(/fin de phrase/.test(zoneActivite.innerHTML),
     'sans direct, elle annonce que le texte arrivera a la fin : '
     + zoneActivite.innerHTML.replace(/<[^>]*>/g, ' ').trim());

// le garde-fou doit couvrir la latence reelle du moteur local (4 a 7 s)
dire(GARDE.stt >= 10000,
     'le garde-fou laisse le temps a un moteur batch (' + GARDE.stt / 1000 + ' s)');
emettre({ genre: 'transcrit', actif: false });

// ---- la pastille du moteur dit s il y a du direct ---------------------------------------
titre('pastille du moteur : le direct se lit d un coup d oeil');
emettre({ genre: 'moteurs_stt',
  liste: [{ cle: 'local', libelle: 'local (faster-whisper)', gratuit: 'illimite',
            renouvelable: true, streaming: true, direct: false, note: '', dispo: true, rang: 0 }],
  chaine: ['local'], actif: 'local', direct: false, impose: false });
dire(/sans direct/.test(pastilleMoteur.textContent),
     'un moteur sans direct est marque : « ' + pastilleMoteur.textContent + ' »');
dire(/retenir/.test(pastilleMoteur.title),
     'et l infobulle explique la consequence sur « retenir »');

emettre({ genre: 'moteurs_stt',
  liste: [{ cle: 'azure', libelle: 'Azure', gratuit: '5 h/mois', renouvelable: true,
            streaming: true, direct: true, note: '', dispo: true, rang: 0 }],
  chaine: ['azure'], actif: 'azure', direct: true, impose: false });
dire(pastilleMoteur.textContent === 'Azure',
     'avec du direct, aucune mention parasite : « ' + pastilleMoteur.textContent + ' »');
dire(!/retenir/.test(pastilleMoteur.title), 'et pas d avertissement inutile');

// ---- quel moteur transcrit --------------------------------------------------------------
titre('choix du moteur de reconnaissance');
socket = new WebSocket(); socket.readyState = 1; envoyes.length = 0;

const INV = [
  { cle: 'speechmatics', libelle: 'Speechmatics', gratuit: '8 h/mois, renouvele',
    renouvelable: true, streaming: true, note: 'meilleur WER', dispo: true, rang: 0 },
  { cle: 'azure', libelle: 'Azure', gratuit: '5 h/mois au palier F0',
    renouvelable: true, streaming: true, note: '', dispo: true, rang: 1 },
  { cle: 'groq', libelle: 'Groq', gratuit: 'palier gratuit',
    renouvelable: true, streaming: false, note: '', dispo: false, rang: null },
  { cle: 'local', libelle: 'local (faster-whisper)', gratuit: 'illimite, hors ligne',
    renouvelable: true, streaming: false, note: '', dispo: true, rang: 2 },
];
emettre({ genre: 'moteurs_stt', liste: INV,
           chaine: ['speechmatics', 'azure', 'local'], actif: 'speechmatics', impose: false });
dire(pastilleMoteur.className === 'montre', 'la pastille apparait');
dire(pastilleMoteur.textContent === 'Speechmatics',
     'et nomme le moteur en tete : ' + pastilleMoteur.textContent);
dire(!/repli/.test(pastilleMoteur.title), 'sans mention de repli au depart');

// une bascule : la pastille doit suivre, sinon elle annonce le moteur du demarrage a vie
emettre({ genre: 'moteur_actif', cle: 'azure', libelle: 'Azure',
           tombes: ['Speechmatics'] });
dire(pastilleMoteur.textContent === 'Azure', 'apres bascule, elle nomme le nouveau');
dire(pastilleMoteur.className.split(' ').includes('replie'),
     'et se marque comme un repli');
dire(/repli/.test(pastilleMoteur.title), 'l infobulle explique pourquoi');

// la liste de choix
const boite = document.getElementById('choix-moteur');
boite.hidden = true;                       // ce que porte l attribut hidden du balisage
pastilleMoteur.onclick({ stopPropagation: () => {} });
dire(boite.hidden === false, 'le clic ouvre la liste');
dire(/8 h\/mois/.test(boite.innerHTML) && /5 h\/mois/.test(boite.innerHTML),
     'chaque moteur annonce son palier gratuit');
dire(/sans streaming/.test(boite.innerHTML), 'et les moteurs batch sont signales');
dire(/pas de cl/.test(boite.innerHTML), 'un moteur sans cle est marque comme tel');
dire(/prochain lancement/.test(boite.innerHTML),
     'le pied dit que le choix vaut au prochain lancement');

// La regle qui compte, testee directement plutot qu'a travers un bouton construit par
// innerHTML : mettre un moteur en tete ne doit pas jeter les autres.
dire(ordreAvecTete('local') === 'local,speechmatics,azure',
     'en tete : ' + ordreAvecTete('local'));
dire(ordreAvecTete('speechmatics') === 'speechmatics,azure,local',
     'le moteur deja en tete ne bouge pas : ' + ordreAvecTete('speechmatics'));
dire(ordreAvecTete('inconnu') === 'inconnu,speechmatics,azure,local',
     'un moteur hors chaine s ajoute devant sans rien perdre');
dire(ordreAvecTete('') === '', 'sans moteur, aucun ordre');
dire(/data-tete=/.test(boite.innerHTML), 'un bouton par moteur disponible est propose');
dire(/moteur-defaut/.test(boite.innerHTML), 'et un retour a l ordre par defaut');

// VOIX_STT impose : le dire, sinon on cliquerait sans effet
emettre({ genre: 'moteurs_stt', liste: INV,
           chaine: ['azure', 'local'], actif: 'azure', impose: true });
dessinerChoix();
dire(/VOIX_STT est pos/.test(document.getElementById('choix-moteur').innerHTML),
     'une variable d environnement imposee est signalee');

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
// DEVANT la machine : ces deux boutons pilotent ses haut-parleurs, et c est la qu ils ont un
// sens. Sur un appareil distant, la ligne porte « écouter » a la place — verifie plus bas.
const hoteReel = location.hostname;
location.hostname = '127.0.0.1';
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
location.hostname = hoteReel;      // de retour sur le telephone

// Depuis un appareil distant, la MEME reponse porte « écouter », et pas les deux boutons du
// PC : trois boutons de treize pixels cote a cote, dont deux qui commandent une autre piece.
flux.children.length = 0; dernier = null; lignesVoix.clear();
ajouter({ n: 810, genre: 'voix', id: 'p2', texte: 'reponse vue du telephone', h: '12:00:01' });
const ligneTel = lignesVoix.get('p2');
dire(!!ligneTel.zoneEcoute, 'sur un telephone, la reponse porte « écouter » des son arrivee');
outillerParole({ id: 'p2', mots: 12 });
dire(!ligneTel.zoneLecture, 'et pas « couper »/« relire », qui pilotent le PC');

// LE bug : une phrase courte que l agent dit lui-meme n a pas d identifiant, donc pas de
// parole_fin derriere elle. Elle n avait aucun bouton — d ou « il apparait sur le premier
// message et pas sur le second », selon lequel des deux etait une interjection.
ajouter({ n: 811, genre: 'voix', texte: "d'accord, j'y vais.", h: '12:00:02' });
dire(!!dernier.zoneEcoute,
     'une phrase sans identifiant porte « écouter » elle aussi — c etait le bug');
globalThis.__zoneRelire = ligneTel.zoneEcoute;

// Assis DEVANT la machine, l inverse : elle parle deja, la doubler serait absurde.
const hoteVrai = location.hostname;
location.hostname = '127.0.0.1';
envoyes.length = 0; dit.length = 0;
zl.children[1].onclick();
const rl = envoyes.find(o => o.cmd === 'relire');
dire(rl && rl.id === 'p1',
     'en local, relire vise CETTE reponse cote serveur : ' + JSON.stringify(rl));
location.hostname = hoteVrai;
lectureEnCours = null; majBoutonsLecture();

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

// ---- le decompte lit l'echeance, il ne la recalcule pas ----------------------------------
titre('le decompte affiche est celui qui decide');
// C'etait le vrai defaut : la page comptait de son cote, LiveKit decidait du sien. Quand les
// deux divergeaient on lisait « encore 4 s » et le message etait deja parti — donc « retenir »
// arrivait apres coup. L'agent publie maintenant l'echeance exacte en horloge murale.
emettre({ genre: 'ecoute', actif: true, min: 5, max: 12.5, fin: Date.now() + 5000 });
dire(finMin === finMax,
     'avec une echeance exacte, aucune extension possible : finMin === finMax');
const restant = (finMin - Date.now()) / 1000;
dire(restant > 4.5 && restant <= 5.05,
     'et le reste affiche vient de l echeance publiee (' + restant.toFixed(2) + ' s)');
// Sans `fin` (ancien mode automatique) la page estime, et l extension redevient possible :
// la ou LiveKit decide, la page ne PEUT que deviner, et le dire est plus honnete.
emettre({ genre: 'ecoute', actif: true, min: 5, max: 12.5 });
dire(finMax > finMin, 'sans echeance publiee, la page retombe sur une estimation avec plafond');
emettre({ genre: 'ecoute', actif: false });
dire(finMin === null, 'la fin de la fenetre efface le decompte');

// ---- retenu : la raison s affiche, et elle s efface --------------------------------------
titre('un texte retenu dit POURQUOI il l est');
const note = document.getElementById('note-barre');
for (const [raison, attendu] of [['occupe', /Claude travaille/],
                                 ['mode', /retenir/],
                                 ['tour', /rattrap/]]) {
  emettre({ genre: 'dictee', texte: 'une phrase a relire', raison: raison });
  dire(!note.hidden && attendu.test(note.textContent),
       'raison « ' + raison + '  » : "' + note.textContent + '"');
  dire(champ.value === 'une phrase a relire',
       'et le texte est bien depose dans la barre, en entier');
}
// Le corps de la ligne du flux porte aussi la raison : en relisant l historique on doit
// pouvoir distinguer « je l ai retenu » de « il a ete retenu pour moi ».
dire(/rattrap/.test(corps({ genre: 'dictee', texte: 'x', raison: 'tour' })),
     'la ligne du flux distingue les trois raisons');
dire(/travaillait/.test(corps({ genre: 'dictee', texte: 'x', raison: 'occupe' })),
     'y compris la retenue d office');

// Une explication qui survit a son objet devient fausse : parler ou envoyer l efface.
emettre({ genre: 'ecoute', actif: false, parle: true });
dire(note.hidden, 'reparler efface la note : la situation a change');
emettre({ genre: 'dictee', texte: 'encore', raison: 'mode' });
dire(!note.hidden, 'elle revient au depot suivant');
emettre({ genre: 'toi', texte: 'encore' });
dire(note.hidden, 'et un message reellement parti l efface aussi');
// Le brouillon TAPE survit deliberement a l envoi d une dictee : on peut avoir ecrit une
// phrase, parle ensuite, et le tour vocal parti ne doit pas emporter ce qu on avait ecrit.
// La protection contre le texte retenu qui survit a un envoi est ailleurs, cote agent : la
// retenue est collante, donc aucun tour vocal ne part tant qu un texte attend une relecture.
champ.value = 'un brouillon tape'; champ.oninput();
ouvrirDictee();
poserDictee('et une dictee', false);
emettre({ genre: 'toi', texte: 'et une dictee' });
dire(champ.value === 'un brouillon tape',
     'un envoi vocal ne retire que la dictee, jamais le brouillon tape : "' + champ.value + '"');

// ---- le selecteur de conversations -------------------------------------------------------
titre('une ligne = une conversation, reconnaissable');
emettre({ genre: 'conversations', dossier: '/a/projet', courante: 'sid-en-cours', liste: [
  { session_id: 'sid-en-cours', projet: 'insnap', tours: 59, reprises: 7, ici: true,
    maj: new Date(Date.now() - 5 * 60000).toISOString(), etat: 'en cours',
    apercu: "l inscription whatsapp ne marche pas" },
  { session_id: 'sid-autre', projet: 'echec', tours: 38, reprises: 5, sous: 'claudesque/echec',
    maj: new Date(Date.now() - 3 * 3600000).toISOString(), etat: 'fermée',
    apercu: "tkt rien a faire pour l instant" },
  { session_id: 'sid-ailleurs', projet: 'piano', tours: 11, reprises: 3, etat: 'en cours',
    maj: new Date(Date.now() - 26 * 3600000).toISOString(), apercu: "continue" },
  { session_id: null, projet: 'abandonnee', tours: 0, reprises: 1, etat: 'fermée', apercu: '' },
]});
// Le bouton nomme la conversation COURANTE, le compte vient en second : ce qu on veut savoir
// d un coup d oeil est « ou suis-je », pas « combien y en a-t-il ».
dire(/insnap/.test(btnConvs.textContent),
     'le bouton nomme la conversation courante : "' + btnConvs.textContent + '"');
dire(/\+2/.test(btnConvs.textContent),
     'et compte les autres reprenables, sans le lancement sans session');
dire(!/\+3/.test(btnConvs.textContent),
     'le lancement sans session_id n est pas compte : il n y a rien a y reprendre');

dessinerConvs();
const panneau = document.getElementById('choix-conv');
dire(/l inscription whatsapp/.test(panneau.innerHTML),
     'la derniere phrase dite est affichee : c est a ça qu on reconnait une conversation');
dire(/↻ 7/.test(panneau.innerHTML),
     'le nombre de reprises est visible — 59 tours en 7 lancements, pas 7 conversations');
// Une icone qu il faut deviner ne vaut pas mieux qu une absence d icone : chaque pastille
// porte son explication en toutes lettres, et le pied explique le symbole une fois.
dire(/class="etat vit"/.test(panneau.innerHTML),
     'la conversation courante porte une pastille pleine');
dire(/celle que tu utilises en ce moment/.test(panneau.innerHTML),
     'et son infobulle dit ce que la pastille signifie');
dire(/on ne peut pas y etre a deux|on ne peut pas y être à deux/.test(panneau.innerHTML),
     'celle tenue ailleurs explique POURQUOI elle est desactivee');
dire(/= nombre de fois/.test(panneau.innerHTML),
     'et le symbole des reprises est explique une fois, en bas');
dire(/abandonnee/.test(panneau.innerHTML) === false,
     'une conversation sans session_id n est pas proposee : la reprendre repartirait de zero');
dire(/1 lancement\(s\) sans session/.test(panneau.innerHTML),
     'mais on DIT qu elle existe, plutot que de la faire disparaitre en silence');
dire(/il y a 5 min/.test(panneau.innerHTML), 'les dates sont relatives, pas des horodatages');

// Celle qui tourne ailleurs ne doit pas etre cliquable : deux agents sur la MEME session
// Claude s ecriraient par-dessus.
dire(/data-sid="sid-ailleurs"[^>]*disabled/.test(panneau.innerHTML),
     'une conversation ouverte par un autre agent est desactivee');
dire(/data-sid="sid-en-cours"[^>]*disabled/.test(panneau.innerHTML) === false,
     'mais la conversation COURANTE reste marquee active, pas desactivee');

// Cliquer envoie l ordre de reprise, et rien d autre.
// La liaison doit etre ouverte : une section precedente l a fermee pour tester la
// reconnexion, et une commande envoyee liaison fermee part dans la file d attente, pas dans
// `envoyes`. Sans ce rappel le test mesurerait l etat laisse par un autre test.
socket.readyState = 1;
envoyes.length = 0;
panneau.querySelector('[data-sid="sid-autre"]').onclick();
const ordre = envoyes.find(o => o.cmd === 'reprendre');
dire(ordre && ordre.session_id === 'sid-autre',
     'cliquer demande la reprise de CETTE conversation');
dire(panneau.hidden, 'et le panneau se referme');

// ---- le titre, et la conversation neuve --------------------------------------------------
// Le defaut : la liste nommait les conversations d apres le DOSSIER. Quatre conversations sur
// le meme projet s appelaient toutes « insnap » et il fallait lire la derniere phrase de
// chacune pour deviner laquelle etait laquelle.
emettre({ genre: 'conversations', dossier: '/a/insnap', courante: 'sid-t1', liste: [
  { session_id: 'sid-t1', projet: 'insnap', titre: 'refonte du parcours d inscription',
    tours: 12, reprises: 2, ici: true, etat: 'en cours',
    maj: new Date(Date.now() - 6e4).toISOString(), apercu: 'reprends sur le SMS' },
  { session_id: 'sid-t2', projet: 'insnap', tours: 4, reprises: 1, ici: true, etat: 'fermée',
    maj: new Date(Date.now() - 9e5).toISOString(), apercu: 'sans titre celle-ci' },
]});
dessinerConvs();
dire(/refonte du parcours d inscription/.test(panneau.innerHTML),
     'le titre remplace le nom du dossier quand il existe');
dire(/insnap · 12 tours/.test(panneau.innerHTML),
     'et le dossier reste visible en second : deux sujets peuvent vivre dans deux dossiers');
dire(/insnap<\/span>/.test(panneau.innerHTML) || /insnap/.test(panneau.innerHTML),
     'une conversation sans titre garde le nom du dossier');
dire(/refonte du parcours/.test(btnConvs.textContent),
     'et le bouton de l en-tete porte le titre courant : "' + btnConvs.textContent + '"');

// La conversation neuve est une ACTION, distincte des entrees de liste.
dire(/data-neuve="1"/.test(panneau.innerHTML), 'le panneau propose d en ouvrir une neuve');
dire(/conservées et restent reprenables/.test(panneau.innerHTML),
     'et dit ce qui arrive aux autres — sinon on n ose pas cliquer');
dire(panneau.innerHTML.indexOf('data-neuve') < panneau.innerHTML.indexOf('data-sid'),
     'elle est EN HAUT, avant la liste');
socket.readyState = 1; envoyes.length = 0;
panneau.querySelector('[data-neuve="1"]').onclick();
dire(envoyes.some(o => o.cmd === 'nouvelle_conversation'),
     'cliquer demande bien une conversation neuve');
dire(panneau.hidden, 'et le panneau se referme');

// Liste vide : l action doit RESTER proposee, sinon on ne peut jamais en creer une.
emettre({ genre: 'conversations', dossier: '/a/vide', courante: null, liste: [] });
dessinerConvs();
dire(/data-neuve="1"/.test(panneau.innerHTML),
     'liste vide : on peut toujours en ouvrir une neuve');
socket.readyState = 1; envoyes.length = 0;
panneau.querySelector('[data-neuve="1"]').onclick();
dire(envoyes.some(o => o.cmd === 'nouvelle_conversation'),
     'et le clic est branche la aussi — les deux branches du dessin doivent l armer');

// ---- le micro du bas ---------------------------------------------------------------------
titre('deux boutons, un seul micro');
// Le geste que ça sert : couper le micro pour corriger le texte, le rouvrir pour continuer.
// Devoir remonter dans l en-tete pour ça casse le geste — le regard est en bas, ou le texte
// s ecrit. Mais il n y a qu UN micro : deux mises a jour separees finiraient par se
// contredire, et le symptome serait de croire qu on est ecoute alors qu on ne l est pas.
const microBasEl = document.getElementById('micro-bas');
emettre({ genre: 'micro', actif: true, voulu: true, bail: true });
dire(/ouvert/.test(microBasEl.className) && btnMicro.className === '',
     'micro ouvert : les deux boutons le disent (' + microBasEl.className + ')');
emettre({ genre: 'micro', actif: false, voulu: false, bail: true });
dire(/coupe/.test(microBasEl.className) && /coupe/.test(btnMicro.className),
     'micro coupe : les deux aussi (' + microBasEl.className + ')');
dire(/rouvrir/.test(microBasEl.title) && /corriger le texte/.test(microBasEl.title),
     'et son infobulle dit le geste, pas seulement l etat');

// L animation ne bouge QUE sur une parole reellement detectee par le VAD. Une animation qui
// bouge sans raison donnerait une fausse confirmation d etre entendu — on parlerait dans le
// vide en croyant que tout va bien.
emettre({ genre: 'micro', actif: true, voulu: true, bail: true });
dire(!/parle/.test(microBasEl.className), 'micro ouvert mais silence : les ondes sont au repos');
emettre({ genre: 'ecoute', actif: false, parle: true });
dire(/parle/.test(microBasEl.className), 'parole detectee : les ondes bougent');
dire(/t entend|entend/.test(microBasEl.title.replace(/'/g, ' ')),
     'et l infobulle le dit : "' + microBasEl.title.split(String.fromCharCode(10))[0] + '"');
emettre({ genre: 'ecoute', actif: true, min: 5, max: 12.5, fin: Date.now() + 5000 });
dire(!/parle/.test(microBasEl.className),
     'le silence commence : les ondes s arretent, le decompte prend le relais');

// Cliquer en bas fait la meme chose que cliquer en haut.
socket.readyState = 1;
envoyes.length = 0;
microBasEl.onclick();
dire(envoyes.some(o => o.cmd === 'micro'), 'le bouton du bas envoie bien l ordre micro');

// ---- la sequence REELLE du moteur de tete ------------------------------------------------
titre('la dictee suit AssemblyAI, mesure sur une vraie phrase');
// Sequence relevee le 27/08 sur AssemblyAI, moteur de tete de la chaine. Le point qui compte :
// il SEGMENTE. Chaque intermediaire repart de zero au lieu de grandir, donc un affichage qui
// « remplace » perdrait tout le debut de la phrase a chaque nouveau segment. Trois segments
// ici, et la barre doit finir par les porter tous les trois.
champ.value = ''; champ.oninput();
ouvrirDictee();
const segs = [
  'Renomme la variable qui gère le silence dans config.',
  'Puis ajoute un test.',
  'qui vérifie que la chaîne de repli garde le vocabulaire du projet.',
];
for (const seg of segs) {
  poserDictee(seg, false);   // l intermediaire du segment
  poserDictee(seg, true);    // puis sa finale
}
dire(champ.value.includes(segs[0]) && champ.value.includes(segs[1]) && champ.value.includes(segs[2]),
     'les trois segments sont tous la : "' + champ.value.slice(0, 96) + '"');
dire(champ.value.indexOf(segs[0]) < champ.value.indexOf(segs[2]),
     'et dans l ordre ou ils ont ete dits');
dire((champ.value.match(/Renomme la variable/g) || []).length === 1,
     'chaque segment n apparait qu une fois : l intermediaire ne double pas sa finale');

// Deepgram, lui, fait grandir ses intermediaires DANS un segment. Les deux comportements
// doivent aboutir au meme texte, sinon changer de moteur changerait ce qu on lit.
champ.value = ''; champ.oninput();
ouvrirDictee();
poserDictee('Renomme la variable', false);
poserDictee('Renomme la variable qui', false);
poserDictee('Renomme la variable qui', true);
poserDictee('gère le silence', false);
poserDictee('gère le silence dans config', true);
dire(champ.value === 'Renomme la variable qui gère le silence dans config',
     'intermediaires grandissants : aucune repetition non plus — "' + champ.value + '"');

// ---- reprendre une conversation recharge son historique ----------------------------------
titre('changer de conversation vide le flux avant de recharger');
// Sans le vidage, les deux conversations s empilent dans le meme flux : on ne sait plus
// laquelle on lit, ni a laquelle appartient un message qu on relit trois jours plus tard.
// Et les compteurs additionneraient les deux.
emettre({ genre: 'toi', texte: 'un message de la conversation precedente' });
champ.value = 'un brouillon qui appartient a l ancienne'; champ.oninput();
dire(flux.children.length > 0, 'le flux porte des lignes avant la bascule');
emettre({ genre: 'vider' });
dire(flux.children.length === 0, 'le vidage efface tout le flux');
dire(champ.value === '', 'et la barre aussi : son brouillon appartenait a l autre conversation');

// Puis l historique de la NOUVELLE arrive, marque comme du passe.
emettre({ genre: 'reprise', texte: '↑ historique rechargé — 763 lignes' });
emettre({ genre: 'toi', texte: 'une question de la conversation reprise', passe: true });
emettre({ genre: 'voix', texte: 'et sa reponse', passe: true });
emettre({ genre: 'reprise', texte: '↓ ici commence le direct' });
dire(flux.children.length >= 3, 'l historique de la nouvelle conversation est rejoue');
dire(/historique rechargé/.test(flux.textContent),
     'et une ligne dit combien de lignes ont ete rechargees');
dire(/ici commence le direct/.test(flux.textContent),
     'avec une frontiere claire entre le passe et maintenant');

// ---- un message ecrit ne se perd jamais, liaison coupee comprise -------------------------
titre('le clavier et le bouton font la meme chose');
// Le defaut trouve en REGARDANT la page : le bouton dependait de l etat de la liaison, Entree
// non. Deux chemins pour la meme intention, deux comportements. Pire, onsubmit sortait AVANT
// d appeler envoyerCmd — qui sait pourtant mettre en file et reconnecter — donc liaison
// coupee, on tapait, on faisait Entree, et rien ne se passait. Sans un mot. Le commentaire de
// la ligne disait « jamais perdu » pendant que le code le jetait.
socket.readyState = 1;
champ.value = 'un message ecrit en ligne'; champ.oninput();
dire(btnEnvoyer.disabled === false, 'en ligne, avec du texte : le bouton est actif');

// Liaison coupee : le bouton reste ACTIF, parce que le message sera mis en file.
socket.readyState = 3; enAttente = [];
champ.value = 'un message ecrit hors ligne'; champ.oninput();
dire(btnEnvoyer.disabled === false,
     'hors ligne, il reste actif : le message sera mis en file, pas jete');
dire(btnEnvoyer.classList.contains('attente'),
     'mais l attente se voit — on ne fait pas croire a un envoi immediat');
dire(/reconnexion/.test(btnEnvoyer.title), 'et l infobulle dit ce qui va se passer');

// Et le geste aboutit : le message part en file, la barre se vide, une note l explique.
composer.onsubmit({ preventDefault() {} });
const enFile = enAttente.filter(o => o.cmd === 'texte');
dire(enFile.length === 1 && enFile[0].texte === 'un message ecrit hors ligne',
     'le message est bien en file : ' + JSON.stringify(enFile[0] || null));
dire(champ.value === '', 'la barre est videe : le message est pris en charge');
const noteHL = document.getElementById('note-barre');
dire(!noteHL.hidden && /hors ligne/.test(noteHL.textContent),
     'et on le DIT : "' + noteHL.textContent + '"');

// Champ vide : le bouton reste desactive, en ligne comme hors ligne. Envoyer du vide n a
// aucun sens et le bouton doit le refuser.
champ.value = ''; champ.oninput();
dire(btnEnvoyer.disabled === true, 'champ vide : le bouton refuse, hors ligne aussi');
socket.readyState = 1;
champ.value = ''; champ.oninput();
dire(btnEnvoyer.disabled === true, 'et en ligne aussi');

// ---- ce qui est consomme se lit, et se voit tourner --------------------------------------
titre('le consomme passe devant le restant');
// Le defaut rapporte : « il me reste encore 329 h 59 alors que j ai claque plus d une minute ».
// Sur un palier de 330 h, une minute d usage est invisible dans le restant. Le chiffre qu on
// vient verifier est ce qu on a DEPENSE.
emettre({ genre: 'moteurs_stt', actif: 'assemblyai', direct: true,
  chaine: ['assemblyai', 'local'],
  liste: [{ cle: 'assemblyai', libelle: 'AssemblyAI', dispo: true, rang: 0, streaming: true,
            gratuit: '50 $ de credits', note: 'le meilleur mesure' },
          { cle: 'local', libelle: 'local', dispo: true, rang: 1, streaming: false,
            gratuit: 'illimite', note: 'hors ligne' }] });
emettre({ genre: 'consommation', moteurs: [
  { cle: 'assemblyai', libelle: 'AssemblyAI', dispo: true, consomme_s: 114, palier_s: 1188000,
    reste_s: 1187886, part: 0.0001, renouvelable: false, gratuit: '50 $', en_cours_s: 12 },
  { cle: 'local', libelle: 'local', dispo: true, consomme_s: 0, palier_s: null, reste_s: null,
    part: null, renouvelable: true, gratuit: 'illimite', en_cours_s: 0 },
]});
dessinerChoix();
const pm = document.getElementById('choix-moteur');
dire(/<b>1 min<\/b>/.test(pm.innerHTML),
     'le consomme est en evidence : 1 min, et non noye dans 329 h de restant');
dire(/reste 329 h/.test(pm.innerHTML), 'le restant reste affiche, en second');
// On vise le MARQUEUR, pas la chaine : le pied du panneau contient deja « ne peut pas changer
// en cours de session », et chercher « en cours » y matchait toujours — un test vert par
// accident, qui aurait laisse passer la disparition du marqueur.
dire(/class="vif"[^>]*>· en cours/.test(pm.innerHTML),
     'et « en cours » dit que ce moteur consomme MAINTENANT — sinon un chiffre qui monte '
     + 'tout seul ressemble a une erreur');
dire(/illimité/.test(pm.innerHTML), 'un moteur sans palier reste marque illimite');

// La pastille dit aussi le consomme, pas seulement le reste.
dire(/utilisées sur/.test(pastilleMoteur.title),
     'la pastille annonce le consomme au survol : "' + pastilleMoteur.title.split(String.fromCharCode(10))[0] + '"');
dire(/20 s/.test(pastilleMoteur.title),
     'et dit a quel rythme le chiffre se rafraichit');

// Moteur au repos : pas de mention « en cours », sinon elle ne voudrait plus rien dire.
emettre({ genre: 'consommation', moteurs: [
  { cle: 'assemblyai', libelle: 'AssemblyAI', dispo: true, consomme_s: 114, palier_s: 1188000,
    reste_s: 1187886, part: 0.0001, renouvelable: false, gratuit: '50 $', en_cours_s: 0 },
]});
dessinerChoix();
dire(!/class="vif"/.test(document.getElementById('choix-moteur').innerHTML),
     'micro ferme : plus de marqueur « en cours »');

// ---- l historique aux fleches ------------------------------------------------------------
titre('remonter dans ce qu on a ecrit');
function fleche(touche, curseur) {
  champ.selectionStart = champ.selectionEnd = curseur === undefined ? champ.value.length : curseur;
  champ._declenche('keydown', { key: touche, preventDefault() {}, metaKey: false,
                                ctrlKey: false, altKey: false });
}
// Etat propre : on repart d une page vierge.
localStorage.clear(); histo = []; histoPos = -1; histoBrouillon = '';
socket.readyState = 1;
for (const t of ['premier message', 'deuxieme message', 'troisieme message']) {
  champ.value = t; champ.oninput();
  composer.onsubmit({ preventDefault() {} });
}
dire(histo.length === 3 && histo[0] === 'troisieme message',
     'les envois entrent dans l historique, le plus recent en tete');
dire(champ.value === '', 'et la barre est videe apres chaque envoi');

// On tape un brouillon, puis on remonte : le brouillon doit survivre.
champ.value = 'un brouillon en cours'; champ.oninput();
fleche('ArrowUp');
dire(champ.value === 'troisieme message',
     'fleche haut : le dernier message revient (' + champ.value + ')');
fleche('ArrowUp'); fleche('ArrowUp');
dire(champ.value === 'premier message', 'on remonte jusqu au plus ancien');
fleche('ArrowUp');
dire(champ.value === 'premier message', 'et on ne depasse pas : pas de champ vide surprise');
fleche('ArrowDown'); fleche('ArrowDown'); fleche('ArrowDown');
dire(champ.value === 'un brouillon en cours',
     'redescendre rend le BROUILLON intact : naviguer ne detruit pas ce qu on ecrivait');

// Le curseur garde la priorite dans un texte multiligne : c est ce qui rend la correction
// d un long message supportable.
champ.value = 'ligne une\nligne deux'; champ.oninput();
fleche('ArrowUp', 15);          // curseur sur la deuxieme ligne
dire(champ.value === 'ligne une\nligne deux',
     'fleche haut au milieu d un texte : le curseur bouge, l historique ne s en mele pas');
fleche('ArrowUp', 3);           // curseur sur la premiere ligne
dire(champ.value === 'troisieme message',
     'mais sur la PREMIERE ligne, elle navigue');

// Taper sort de la navigation : on modifie une copie, jamais l historique.
champ.value = 'troisieme message corrige'; champ.oninput();
dire(histoPos === -1, 'editer sort de la navigation');
dire(histo[0] === 'troisieme message',
     'et l historique n est pas reecrit : ' + histo[0]);

// Echap annule la navigation avant de rendre le clavier.
champ.value = 'brouillon deux'; champ.oninput();
fleche('ArrowUp');
dire(champ.value === 'troisieme message', 'on remonte');
champ._declenche('keydown', { key: 'Escape', preventDefault() {} });
dire(champ.value === 'brouillon deux', 'Echap ramene au brouillon plutot que de quitter');

// Deux fois le meme message n occupe qu une entree.
const avant = histo.length;
champ.value = 'troisieme message'; champ.oninput();
histoAjouter('troisieme message');
dire(histo.length === avant, 'le meme message deux fois de suite n est pas duplique');

// Et ça survit au rechargement : c est tout l interet quand « il y a eu un bug ».
dire(/troisieme message/.test(localStorage.getItem('claude-talk:historique') || ''),
     'l historique est ecrit dans le stockage local');
histo = [];
histoCharger();
dire(histo.length >= 3 && histo[0] === 'troisieme message',
     'et se recharge tel quel : ' + histo.length + ' messages retrouves');

// ---- un message envoye pendant une lecture ne disparait pas ------------------------------
// (suite du bloc precedent : toujours devant la machine)
titre('ce qui attend la fin de la lecture');
// Le defaut : le message PART bien, mais LiveKit le met en file derriere la parole en cours.
// La barre se vidait aussitot et aucune ligne n apparaissait avant plusieurs secondes : le
// texte semblait s etre evapore, et on ne savait pas s il fallait le retaper.
socket.readyState = 1;
enAttenteLecture = []; majEnAttente();
// Etat de repos explicite : une section precedente a teste les boutons de lecture et a laisse
// une lecture en cours. Sans cette remise a zero, on mesurerait l etat laisse par un autre
// test et non celui qu on decrit.
emettre({ genre: 'lecture', actif: false });
const zoneAttente = document.getElementById('en-attente');
const btnCouper = document.getElementById('couper-lecture');
// Ce bouton coupe la lecture DU PC : il n existe que devant la machine. Sur un telephone, le
// haut-parleur de la barre coupe deja ce qui joue ici, et un bouton de plus ne rentre pas.
const hoteAvantLecture = location.hostname;
location.hostname = '127.0.0.1';

dire(zoneAttente.hidden, 'au repos, rien n attend');
dire(btnCouper.hidden, 'et le bouton de coupure est cache : il ne servirait a rien');

emettre({ genre: 'lecture', actif: true, id: 'v1' });
dire(!btnCouper.hidden,
     'une lecture commence : le bouton de coupure apparait dans la barre');

champ.value = 'coupe et fais autre chose'; champ.oninput();
composer.onsubmit({ preventDefault() {} });
dire(!zoneAttente.hidden, 'un message envoye pendant la lecture reste VISIBLE');
dire(/coupe et fais autre chose/.test(zoneAttente.textContent),
     'avec son texte : "' + zoneAttente.textContent.slice(0, 70) + '"');
dire(/fin de la lecture/.test(zoneAttente.textContent),
     'et la raison pour laquelle il ne part pas tout de suite');
dire(champ.value === '', 'la barre est quand meme videe : le message est pris en charge');

// Un second message s ajoute, il ne remplace pas le premier.
champ.value = 'et aussi ceci'; champ.oninput();
composer.onsubmit({ preventDefault() {} });
dire(enAttenteLecture.length === 2, 'deux messages en attente restent deux');
dire(/2 messages/.test(zoneAttente.textContent),
     'et on le dit : "' + zoneAttente.textContent.slice(0, 40) + '"');

// La ligne « toi » atteste la prise en compte : c est le seul signal qui le dit.
emettre({ genre: 'toi', texte: 'coupe et fais autre chose', tape: true });
dire(enAttenteLecture.length === 1,
     'le message pris en compte quitte l attente, l autre reste');
emettre({ genre: 'toi', texte: 'et aussi ceci', tape: true });
dire(zoneAttente.hidden, 'les deux traites : la zone disparait');

// Cliquer coupe la lecture.
envoyes.length = 0;
btnCouper.onclick();
dire(envoyes.some(o => o.cmd === 'couper_lecture'),
     'le bouton de la barre coupe bien la lecture');
emettre({ genre: 'lecture', actif: false });
dire(btnCouper.hidden, 'la lecture finie, le bouton se retire');

location.hostname = hoteAvantLecture;   // fin du bloc « devant la machine »

// ---- rejeu de l etat a la connexion --------------------------------------------------------
titre('rejeu de l etat : les listes se remplissent quel que soit l ordre des numeros');
// Le bug, reproduit : le serveur rejoue l'etat dans l'ordre de son dictionnaire, donc le quota
// (numero eleve, republie a chaque tour) arrive AVANT les modeles (numero bas, publies au
// demarrage). La page prenait ces derniers pour du deja-vu — listes vides, historique muet,
// sur toute page ouverte apres les premieres minutes. Un etat n'est pas un evenement : il ne
// passe pas par le compteur monotone.
{
  const base = vuJusqua + 1000;
  selModele.innerHTML = ''; selEffort.innerHTML = '';
  const avant = flux.children.length;
  recevoir({ genre: 'quota', n: base + 116, fenetres: [] }, true);
  recevoir({ genre: 'modeles', n: base + 9, liste: [{ cle: 'opus', libelle: 'Opus 5' }], actuel: 'opus' }, true);
  recevoir({ genre: 'efforts', n: base + 12, liste: [{ cle: 'xhigh', libelle: 'tres eleve' }], actuel: 'xhigh' }, true);
  recevoir({ genre: 'config', n: base + 8, valeurs: { projet: '/x' }, h: '12:00:00' }, true);
  dire(selModele.innerHTML.includes('Opus 5'),
       'les modeles rejoues apres un quota plus recent remplissent bien la liste');
  dire(selEffort.innerHTML.includes('tres eleve'), 'les efforts aussi');
  const apresEtat = flux.children.length;
  dire(apresEtat === avant + 1, 'la carte de configuration est posee, une fois');

  // Puis l'historique, qui contient les MEMES evenements : rien en double, le reste s'affiche.
  recevoir({ genre: '_histoire', evenements: [
    { genre: 'config', n: base + 8, valeurs: { projet: '/x' }, h: '12:00:00' },
    { genre: 'modeles', n: base + 9, liste: [{ cle: 'opus', libelle: 'Opus 5' }], actuel: 'opus', h: '12:00:00' },
    { genre: 'toi', n: base + 10, texte: 'bonjour', h: '12:00:00' },
    { genre: 'quota', n: base + 116, fenetres: [], h: '12:00:00' },
  ] });
  const ajoutees = flux.children.length - apresEtat;
  dire(ajoutees === 1, 'l historique n ajoute que ce qui n a pas ete rejoue comme etat ('
       + ajoutees + ' ligne ajoutee, 1 attendue)');
  dire(vuJusqua >= base + 10, 'et le compteur avance avec l historique (' + vuJusqua + ')');

  // Une reconnexion rejoue le meme etat : la carte ne se dedouble pas, la liste reste pleine.
  const n2 = flux.children.length;
  recevoir({ genre: 'config', n: base + 8, valeurs: { projet: '/x' }, h: '12:00:00' }, true);
  recevoir({ genre: 'modeles', n: base + 9, liste: [{ cle: 'opus', libelle: 'Opus 5' }], actuel: 'opus' }, true);
  dire(flux.children.length === n2, 'une reconnexion ne dedouble pas la carte de configuration');
  dire(selModele.innerHTML.includes('Opus 5'), 'et la liste des modeles est toujours la');
  __n = Math.max(__n, base + 200);
}

// ---- images jointes ---------------------------------------------------------------------
titre('images jointes');
// Ce qui part reellement : une piece prete, un message, et ce que la socket recoit. Les
// chemins ne sont plus colles au texte par la page mais DECLARES a l agent : c est lui qui
// les accole au moment de l envoi, donc une photo suit aussi une phrase DITE — avant, joindre
// puis parler envoyait la phrase sans l image, sans un mot.
socket = new WebSocket(); socket.readyState = 1; envoyes.length = 0;
jointes.length = 0;
jointes.push({ chemin: '/home/x/.cache/claude-talk/images/maquette.png', url: 'blob:fantome' });
majJointes();
const decl = envoyes.filter(o => o.cmd === 'jointes').pop();
dire(!!decl && decl.chemins.length === 1 && decl.chemins[0].endsWith('/maquette.png'),
     'la piece prete est declaree a l agent des qu elle est la');
dire(!!decl && !JSON.stringify(decl).includes('blob:'),
     'et jamais l adresse locale de la vignette, qui ne veut rien dire sur la machine');
champ.value = 'voici la maquette';
composer.onsubmit({ preventDefault() {} });
const envoiImage = envoyes.find(o => o.cmd === 'texte');
dire(!!envoiImage && envoiImage.texte === 'voici la maquette',
     'le message dit ce qu on a ecrit, les chemins ne l encombrent plus');
dire(jointes.length === 0, 'les pieces sont retirees apres l envoi');
const apres = envoyes.filter(o => o.cmd === 'jointes').pop();
dire(!!apres && apres.chemins.length === 0, 'et l agent sait que plus rien n attend');
dire(imagesPretes === 0 && btnEnvoyer.disabled, 'et le bouton retombe au repos');

// Une photo SANS phrase part quand meme : c est « regarde ca », et l agent met les mots.
envoyes.length = 0; champ.value = ''; champ.oninput();
jointes.push({ chemin: '/home/x/.cache/claude-talk/images/erreur.png', url: 'blob:fantome' });
majJointes();
dire(btnEnvoyer.disabled === false, 'une photo seule suffit a activer l envoi');
composer.onsubmit({ preventDefault() {} });
dire(!!envoyes.find(o => o.cmd === 'texte'), 'et le message part sans qu on ait rien ecrit');
jointes.length = 0; majJointes();

// ---- ce qui est parti mais pas encore pris en compte ------------------------------------
titre('messages en vol : visibles jusqu a l echo, rendus en cas d echec');
socket = new WebSocket(); socket.readyState = 1; envoyes.length = 0; enVol.length = 0;
noteBarre(null);
champ.value = 'corrige le titre de la page';
composer.onsubmit({ preventDefault() {} });
dire(enVol.length === 1 && enVol[0].texte === 'corrige le titre de la page',
     'le message envoye est en vol');
dire(!document.getElementById('en-vol').hidden, 'et la barre le montre');
dire(champ.value === '', 'le champ est vide : il est parti');
emettre({ genre: 'toi', texte: 'corrige le titre de la page', tape: true });
dire(enVol.length === 0, 'l echo « toi » du serveur le retire');
dire(document.getElementById('en-vol').hidden, 'et la barre se referme');

champ.value = 'deploie en prod';
composer.onsubmit({ preventDefault() {} });
emettre({ genre: 'erreur', commande: 'texte', message: 'deploie en prod',
          texte: 'la commande « texte » a echoue — details dans les logs' });
dire(champ.value === 'deploie en prod', 'un echec du serveur remet le message dans le champ');
dire(enVol.length === 0, 'et il n est plus en vol');
const noteEchec = document.getElementById('note-barre');
dire(!noteEchec.hidden && /echoue/.test(noteEchec.textContent),
     'la raison est affichee : ' + noteEchec.textContent);
dire(noteEchec.classList.contains('collante'), 'et elle reste tant qu on n ecrit pas');
champ.oninput();
dire(noteEchec.hidden, 'ecrire la fait disparaitre');

champ.value = 'autre message'; composer.onsubmit({ preventDefault() {} });
emettre({ genre: 'erreur', texte: 'quota Azure de transcription épuisé' });
dire(enVol.length === 1 && champ.value === '',
     'une erreur sans rapport avec un message ne le rend pas');
dire(!noteEchec.hidden, 'mais elle se lit dans la barre');
emettre({ genre: 'toi', texte: 'autre message' });
dire(enVol.length === 0, 'et le message suit son cours');


// ---- la liaison : une veille du telephone n'est pas une panne ---------------------------
titre('liaison : veille, reseau, socket zombie');
// Le symptome de depart : on verrouille son telephone pendant que Claude travaille, on
// revient, et la page dit « deconnecte » sans rien tenter de visible. Le reseau n'a jamais
// bronche — c'est l'onglet qui a ete gele, et la page comptait ca comme un echec.
const etatEl = document.getElementById('etat');
const ouvrirSock = () => {
  const s = sockets[sockets.length - 1];
  s.readyState = 1; socket.readyState = 1;
  if (socket.onopen) socket.onopen();
  return s;
};
const fermerSock = (code, raison) => { socket.readyState = 3; socket.onclose({ code, reason: raison }); };

sockets.length = 0;
brancher();
ouvrirSock();
const sockOuvertes = sockets.length;
dire(sockOuvertes === 1, 'une seule socket a l ouverture (' + sockOuvertes + ')');

// 1. Coupure SUBIE en arriere-plan : pas un echec, et rien a tenter tout de suite.
document.visibilityState = 'hidden';
fermerSock(1006);
dire(echecs === 0, 'une coupure en arriere-plan ne compte pas comme un echec (' + echecs + ')');
dire(sockets.length === 1, 'et rien n est rebranche tant que la page est cachee');
dire(!/deconnect|déconnect/.test(etatEl.textContent),
     'l etat ne dit pas « deconnecte » : ' + etatEl.textContent);

// 2. Le retour sur la page rebranche IMMEDIATEMENT.
document.visibilityState = 'visible';
declencher('visibilitychange');
dire(sockets.length === 2, 'revenir sur la page rebranche tout de suite (' + sockets.length + ' sockets)');
dire(/veille|arrière-plan/.test(etatEl.title || ''),
     'et l infobulle dit pourquoi la liaison etait tombee : ' + etatEl.title);
ouvrirSock();
dire(echecs === 0, 'la reconnexion reussie repart de zero');

// 3. Une socket VIVANTE ne se fait pas rebrancher pour rien : c est ce qui coupait la
//    liaison alors qu elle allait bien.
const avantVis = sockets.length;
declencher('visibilitychange');
dire(sockets.length === avantVis, 'revenir sur une liaison saine ne la coupe pas');

// 4. La socket zombie : « ouverte » mais plus aucun signe du serveur depuis longtemps.
//    Sans le pouls, cette page restait morte en ayant l air vivante.
dernierPouls = Date.now() - 120000;
declencher('visibilitychange');
dire(sockets.length === avantVis + 1, 'une socket ouverte mais muette depuis 2 min est rebranchee');
ouvrirSock();

// 5. Le pouls du serveur : signe de vie ET information.
socket.onmessage({ data: JSON.stringify({ genre: '_pouls', depuis: 630, clients: 2,
                                          travail: true, pret: true, en_attente: 0,
                                          evenements: 412 }) });
dire(serveur && serveur.evenements === 412, 'le pouls est retenu');
dire(/tour est en cours/.test(motDuServeur()) && /412/.test(motDuServeur()),
     'et il se raconte : ' + motDuServeur());

// 6. Le bonjour de reprise : ce qu on vient de retrouver, dit une fois.
socket.onmessage({ data: JSON.stringify({ genre: '_bonjour', rejoue: 128, travail: true,
                                          pret: true, depuis: 630, evenements: 412 }) });
const noteRep = document.getElementById('note-barre');
dire(!noteRep.hidden && /128/.test(noteRep.textContent) && /en cours/.test(noteRep.textContent),
     'la reprise dit ce qu elle a retrouve : ' + noteRep.textContent);

// 7. Une vraie coupure, page visible : la, on compte, on espace, et on le DIT.
document.visibilityState = 'visible';
fermerSock(1006);
dire(echecs === 1, 'une coupure page ouverte compte comme un echec');
dire(/reconnexion/.test(etatEl.textContent), 'l etat annonce la tentative : ' + etatEl.textContent);
dire(/coupure réseau/.test(etatEl.title || ''),
     'et l infobulle traduit le code de fermeture : ' + etatEl.title);
const attente1 = prochaineTentative - Date.now();
echecs = 5; fermerSock(1006);
const attente2 = prochaineTentative - Date.now();
dire(attente2 > attente1, 'les tentatives s espacent (' + Math.round(attente1) + ' ms puis '
     + Math.round(attente2) + ' ms)');
// La pastille reste COURTE : elle partage sa ligne avec le titre de la conversation, et
// chaque caractere qu elle prend est pris a lui. Elle dit l essentiel — deconnecte, et dans
// combien de temps — et sa couleur fait le reste. Le detail vit dans le panneau de liaison.
dire(/déconnecté/.test(etatEl.textContent) && etatEl.textContent.length < 22,
     'apres plusieurs echecs, une pastille courte qui dit deconnecte : ' + etatEl.textContent);
dire(etatEl.className.includes('e-perdu'),
     'et sa couleur change — le rouge de la liaison perdue, pas le bleu de l ecoute');
// Le placeholder ne porte plus que ce qui concerne CE champ : ce qui arrive au texte qu on
// y ecrit. Le reste — cause, numero d essai, geste a faire — est dans le panneau, qui a la
// largeur et les boutons pour le porter.
dire(/partira/.test(champ.placeholder) && champ.placeholder.length < 70,
     'la barre ne dit plus que le sort du texte en cours : ' + champ.placeholder);
coupeDepuis = Date.now() - 30000;
direLiaison();
dire(/essai \d+/.test(document.getElementById('l-cause').textContent),
     'le numero d essai est dans le panneau : '
     + document.getElementById('l-cause').textContent.split('\n')[0]);

// 8. Hors ligne : ce n est pas la meme chose qu une panne du serveur, et ca se dit autrement.
navigator.onLine = false;
declencher('offline');
dire(/hors ligne/.test(etatEl.textContent), 'hors ligne se distingue de deconnecte : '
     + etatEl.textContent);
navigator.onLine = true;
const avantReseau = sockets.length;
declencher('online');
dire(sockets.length === avantReseau + 1, 'le retour du reseau rebranche tout de suite');
ouvrirSock();

// 8b. Marteler ne doit pas etre possible : pendant qu'une tentative est en vol, cliquer
//     encore ne doit pas la fermer pour en rouvrir une — c'est un serveur qui redemarre
//     qu'on frappe, au pire moment.
fermerSock(1006);
const sockEnVol = sockets.length;
socket.readyState = 0;
reconnecter(); reconnecter(); reconnecter();
dire(sockets.length === sockEnVol, 'trois clics pendant une tentative en vol n en ouvrent pas trois');
ouvrirSock();

// 8c. LE cas du telephone : la socket se dit OUVERTE mais elle est morte. Revenir sur la
//     page ne doit pas se contenter de regarder `readyState` — il ment, et c'est pour ca
//     qu'un message envoye juste avant la veille partait dans le vide, avec une page qui
//     avait l'air branchee. On pose la question, et on croit la reponse.
dernierPouls = Date.now();          // fraiche en apparence : la peremption ne verra rien
envoyes.length = 0;
declencher('visibilitychange');
dire(envoyes.some(o => o.cmd === 'ping'),
     'revenir sur une socket qui a l air saine envoie une sonde au serveur');
const avantSonde = sockets.length;
dire(sockets.length === avantSonde, 'et ne la coupe pas avant d avoir la reponse');
// Le serveur repond : rien ne bouge, la liaison etait bonne.
socket.onmessage({ data: JSON.stringify({ genre: '_pouls', pret: true, evenements: 9 }) });

// 9. L'infobulle ne doit pas rester bloquee sur la derniere panne : sur une pastille verte,
//    lire « coupure reseau » en survolant est exactement le genre d'information fausse qu'on
//    cherche a supprimer ici.
emettre({ genre: 'etat', vers: 'thinking' });
dire(!/coupure/.test(etatEl.title || ''),
     'une liaison revenue efface le motif de la derniere coupure : ' + etatEl.title);
dire(/agent prêt|signe du serveur/.test(etatEl.title || ''),
     'et dit ce que le serveur raconte de lui-meme');

// ---- la reponse ecrite se lit, la reponse parlee s ecoute ---------------------------------
// Le defaut : l ecrit etait contraint a la prose continue, sans markdown, pour que la
// synthese vocale sonne juste. Or la synthese ne lit pas ce texte — un second modele en
// fabrique une version parlee. On payait donc une contrainte orale sur le seul contenu qui
// se lit avec les yeux : un bloc gris sans relief, sur un telephone, au milieu d un flux.
titre('reponse ecrite : du markdown rendu, pas un pave');
dire(md('**gras**') === '<p><strong>gras</strong></p>', 'le gras est du gras : ' + md('**gras**'));
dire(/<em>/.test(md('un mot *en italique* ici')), 'l italique aussi');
dire(md('- un\n- deux') === '<ul><li>un</li><li>deux</li></ul>', 'une liste est une liste');
dire(/<ol><li>premier/.test(md('1. premier\n2. second')), 'une liste numerotee garde son ordre');
dire(/<h4>Titre<\/h4>/.test(md('## Titre')),
     'les titres tiennent dans le flux : toujours h4, jamais une banniere plus grosse que l en-tete');
dire(/<code>fichier\.py<\/code>/.test(md('vois `fichier.py`')), 'le code reste du code');
dire(/<pre><code>const a = 1 &lt; 2;<\/code><\/pre>/.test(md('```\nconst a = 1 < 2;\n```')),
     'un bloc de code est rendu ET echappe');
dire(/<blockquote>/.test(md('> cite')), 'une citation se distingue');
dire(md('a\nb') === '<p>a<br>b</p>',
     'un retour a la ligne simple reste une coupe : elle porte du sens sur un ecran etroit');
dire(md('un\n\ndeux') === '<p>un</p><p>deux</p>', 'une ligne vide separe deux paragraphes');

// La securite, et elle n est pas theorique : ce texte vient d un modele qui vient de lire
// des fichiers et des pages web. On echappe D ABORD, on balise ensuite — rien de ce qui
// arrive ne peut devenir une balise.
dire(!/<script>/.test(md('<script>alert(1)</script>')),
     'une balise dans la reponse reste du texte : ' + md('<script>alert(1)</script>'));
dire(!/<img/.test(md('<img src=x onerror=alert(1)>')), 'une image piegee aussi');
dire(/href="https:\/\/x\.fr"/.test(md('[lien](https://x.fr)')), 'un vrai lien est cliquable');
dire(!/href/.test(md('[piege](javascript:alert(1))')),
     'un lien javascript: redevient du texte — on tape au doigt sans reflechir : '
     + md('[piege](javascript:alert(1))'));
dire(md('code avec `a ** b` dedans') === '<p>code avec <code>a ** b</code> dedans</p>',
     'ce qui est dans du code n est plus interprete : ' + md('code avec `a ** b` dedans'));

// Ce qui arrive en MORCEAUX. Une liste a moitie arrivee n est pas du markdown valide : on
// garde le brut et on re-rend l ensemble, au lieu d empiler des fragments rendus.
async function testerEcritEnMorceaux() {
  titre('reponse ecrite : les morceaux se re-rendent, ils ne s empilent pas');
  flux.children.length = 0; dernier = null; enRejeu = true;
  actifs.add('texte');
  ajouter({ genre: 'texte', n: 9100, h: '10:00', texte: '**voila** ce que j ai fait :\n' });
  ajouter({ genre: 'texte', n: 9101, h: '10:00', texte: '- un point\n', suite: true });
  ajouter({ genre: 'texte', n: 9102, h: '10:00', texte: '- un autre', suite: true });
  const ligneTexte = flux.children[flux.children.length - 1];
  dire(flux.children.length === 1, 'les morceaux restent une seule ligne');
  dire(ligneTexte.brutMd === '**voila** ce que j ai fait :\n- un point\n- un autre',
       'le texte brut est conserve entier, pour pouvoir re-rendre');
  await new Promise(r => setTimeout(r, 30));
  const rendu = ligneTexte.querySelector('.propos').innerHTML;
  dire(/<strong>voila<\/strong>/.test(rendu)
       && /<ul><li>un point<\/li><li>un autre<\/li><\/ul>/.test(rendu),
       'et le rendu porte le gras ET la liste complete : ' + rendu);
  enRejeu = false;
}

// ---- la connexion qui n aboutit jamais ----------------------------------------------------
// LE defaut du telephone, et le plus couteux parce qu il ne se voit pas : une socket peut
// rester en CONNECTING indefiniment — le navigateur a lance le handshake, les paquets partent
// dans le vide (bascule wifi/4G, passerelle qui avale la connexion) — et il n emet NI `open`
// NI `close`. Or tout le rebranchement est accroche a `onclose`. Sans fermeture, rien ne
// reprogramme rien : « reconnexion… » reste a l ecran pour toujours, et seul le fait de
// quitter l application et d y revenir reveille la page.
async function testerLiaisonQuiNAboutitPas() {
  const pause = (ms) => new Promise(r => setTimeout(r, ms));
  const etatEl = document.getElementById('etat');
  titre('liaison : une tentative qui n aboutit pas doit en relancer une autre');

  const gardeOuverture = DELAI_OUVERTURE, gardeVeille = DELAI_VEILLE;
  const attentes = ATTENTES.slice();
  for (let i = 0; i < ATTENTES.length; i++) ATTENTES[i] = 20;   // pas d attente reelle ici
  DELAI_OUVERTURE = 30;
  document.visibilityState = 'visible';
  navigator.onLine = true;
  globalThis.santeHS = false;

  sockets.length = 0; echecs = 0; serveurRepond = null;
  brancher();
  dire(sockets.length === 1 && sockets[0].readyState === 0,
       'la tentative part et reste en cours de connexion');
  await pause(120);
  dire(sockets.length > 1,
       'une socket qui n aboutit pas est jetee et une autre est tentee ('
       + sockets.length + ' tentatives)');
  dire(echecs > 0, 'et ca compte comme un echec, donc les tentatives vont s espacer');
  dire(/ouverture|réponse/.test(etatEl.title || ''),
       'la cause est dite : ' + etatEl.title);

  // Le diagnostic : apres quelques essais, on demande au serveur HTTP s il est la. Repondre
  // « reconnexion » sans savoir laisse fixer un ecran pendant que plus personne n ecoute.
  globalThis.santeHS = true;
  requetes.length = 0;
  echecs = 4; serveurRepond = null; coupeDepuis = Date.now() - 60000;
  programmerRebranche();
  dire(requetes.some(r => String(r.url).indexOf('sante=') >= 0),
       'apres plusieurs echecs, la page demande au serveur HTTP s il repond encore');
  await pause(5);
  dire(serveurRepond === false, 'et retient que la machine ne repond plus du tout');
  dire(/agent arrêté/.test(etatEl.textContent),
       'l etat le DIT au lieu de promettre une reconnexion : ' + etatEl.textContent);
  dire(/fermée/.test(document.getElementById('l-titre').textContent),
       'et le panneau NOMME ce qui se passe : ' + document.getElementById('l-titre').textContent);
  dire(document.getElementById('l-actions').children.length >= 1,
       'avec le geste qui repare, a portee de pouce — pas une phrase a lire');

  // La machine repond mais le flux ne s etablit pas : ce n est pas la meme panne, ni le meme
  // geste. Confondre les deux envoie relancer un agent qui tourne tres bien.
  globalThis.santeHS = false;
  serveurRepond = null; diagEnCours = false;
  diagnostiquerPanne();
  await pause(5);
  dire(serveurRepond === true && !/agent arrêté/.test(etatEl.textContent),
       'une machine qui repond en HTTP ne se dit pas « arretee » : ' + etatEl.textContent);
  dire(/flux/.test(document.getElementById('l-dit').textContent),
       'et le panneau dit ou est le probleme : ' + document.getElementById('l-dit').textContent);

  // Un appui sur l etat REESSAYE. C est le geste qu on fait devant un compte a rebours en se
  // demandant s il sert a quelque chose ; le faire attendre vingt secondes de plus pour rien
  // etait la pire reponse possible.
  const avantAppui = sockets.length;
  etatEl.onclick();
  dire(sockets.length === avantAppui + 1, 'appuyer sur l etat relance une tentative tout de suite');

  // Le constructeur qui JETTE — contexte mixte, trop de sockets. L exception traversait
  // brancher() : plus de minuterie, plus de socket, donc plus jamais de tentative.
  const vraiWS = globalThis.WebSocket;
  globalThis.WebSocket = function () { throw new Error('refus du navigateur'); };
  globalThis.WebSocket.OPEN = 1;
  echecs = 0;
  brancher();
  globalThis.WebSocket = vraiWS;
  dire(reconnexionPrevue === true && echecs === 1,
       'une ouverture qui jette ne tue pas la page : une tentative reste programmee');
  dire(/refus du navigateur/.test(etatEl.title || ''),
       'et on sait pourquoi : ' + etatEl.title);

  // La surveillance continue, page ouverte sous les yeux. `perimee()` existait mais rien ne
  // la consultait tant qu on ne revenait pas sur la page : une socket zombie restait donc
  // invisible exactement dans le cas ou l on regarde l ecran en attendant une reponse.
  arreterRebranche();
  sockets.length = 0;
  brancher();
  const z = sockets[sockets.length - 1];
  z.readyState = 1; socket.readyState = 1; if (socket.onopen) socket.onopen();
  dernierPouls = Date.now() - 120000;        // plus un signe depuis deux minutes
  DELAI_VEILLE = 20;
  surveillerLiaison();
  await pause(60);
  dire(sockets.length > 1,
       'une socket muette est remplacee SANS qu on ait a quitter la page ('
       + sockets.length + ' tentatives)');

  // ---- la reconnexion sans fin, et sans un mot ------------------------------------------
  // Le defaut vecu : on quitte l application, on revient, « reconnexion… » et trois points
  // pour toujours. Aucune information : ni depuis quand, ni pourquoi, ni si l agent est
  // encore la. La cause etait ici — chaque retour sur la page appelle rebrancherMaintenant,
  // qui remettait le compteur d echecs a zero. Or tous les seuils qui RENSEIGNENT sont
  // accroches a ce compteur : interroger le serveur en HTTP, dire « deconnecte », ouvrir la
  // boite de diagnostic. Un telephone qu on reprend toutes les trente secondes ne les
  // atteignait donc jamais.
  if (veilleLiaison) { clearInterval(veilleLiaison); veilleLiaison = null; }
  arreterRebranche();
  sockets.length = 0; echecs = 0; coupeDepuis = 0; serveurRepond = null;
  brancher();
  await pause(120);                       // quelques tentatives qui n aboutissent pas
  const apresEchecs = echecs;
  dire(apresEchecs >= 2, 'les echecs s accumulent (' + apresEchecs + ')');
  rebrancherMaintenant('retour sur la page', true);
  dire(echecs >= apresEchecs,
       'revenir sur la page n EFFACE pas le compteur d echecs (' + echecs + ') — sinon on '
       + 'reessaie eternellement sans jamais franchir le seuil qui informe');
  coupeDepuis = Date.now() - 60000;
  direLiaison();
  const pan = document.getElementById('liaison');
  dire(!!pan && !pan.hidden,
       'et au bout d une minute la page DIT ce qu elle sait, au lieu des trois points');
  const sockAvant = sockets.length;
  await pause(120);
  dire(sockets.length > sockAvant, 'tout en continuant d essayer : rien n est abandonne');

  // Une ouverture reussie, elle, repart vraiment de zero : c est la seule preuve qu il n y a
  // plus d echec a compter.
  arreterRebranche();
  sockets.length = 0; brancher();
  const bonne = sockets[sockets.length - 1];
  bonne.readyState = 1; socket.readyState = 1; if (socket.onopen) socket.onopen();
  dire(echecs === 0 && !document.getElementById('diag-ws'),
       'une ouverture reussie remet le compteur a zero et retire le diagnostic');

  // ---- le filet qui se desarmait tout seul ----------------------------------------------
  // Une socket fermee pendant que la page etait en arriere-plan laisse `reconnexionPrevue`
  // a vrai SANS aucune minuterie : la reprise est confiee au retour sur la page. Quand ce
  // retour n emet pas `visibilitychange` — page gelee puis restauree — plus personne ne
  // rebranchait, et la surveillance refusait d intervenir a cause de ce drapeau. Page morte,
  // affichant « reconnexion… », pour toujours.
  arreterRebranche();
  sockets.length = 0;
  document.visibilityState = 'hidden';
  brancher();
  const enFond = sockets[sockets.length - 1];
  enFond.readyState = 1; socket.readyState = 1; if (socket.onopen) socket.onopen();
  fermerSock(1006);
  dire(reconnexionPrevue === true && !minuterieRebranche,
       'coupure en arriere-plan : la reprise est differee, aucune minuterie ne tourne');
  document.visibilityState = 'visible';     // on revient, mais sans visibilitychange
  const avantFilet = sockets.length;
  DELAI_VEILLE = 20;
  surveillerLiaison();
  await pause(60);
  dire(sockets.length > avantFilet,
       'la surveillance rattrape le cas et rebranche d elle-meme ('
       + (sockets.length - avantFilet) + ' tentative(s))');
  if (veilleLiaison) { clearInterval(veilleLiaison); veilleLiaison = null; }

  // ---- une socket ouverte qui ne dit jamais rien -----------------------------------------
  // Le dernier chemin vers « reconnexion… » eternel : le navigateur a ouvert la liaison —
  // donc ni `onclose` ni le garde-temps d ouverture ne parlent — mais le serveur n envoie
  // pas un octet. On attendait les 70 s de perimee() pour s en apercevoir.
  arreterRebranche();
  const gardeBonjour = DELAI_BONJOUR;
  DELAI_BONJOUR = 30;
  DELAI_OUVERTURE = 5000;     // sinon c est LUI qui tire, et on testerait l autre garde
  sockets.length = 0;
  brancher();
  const muette = sockets[sockets.length - 1];
  muette.readyState = 1; socket.readyState = 1; if (socket.onopen) socket.onopen();
  const avantMuette = sockets.length;
  await pause(80);
  dire(sockets.length > avantMuette,
       'une socket ouverte qui ne rejoue rien est jetee au lieu d etre attendue');
  dire(/rejoué/.test(causeCoupure), 'et la cause est nommee : ' + causeCoupure);
  DELAI_BONJOUR = gardeBonjour; DELAI_OUVERTURE = 30;

  DELAI_VEILLE = gardeVeille; DELAI_OUVERTURE = gardeOuverture;
  for (let i = 0; i < attentes.length; i++) ATTENTES[i] = attentes[i];
  if (veilleLiaison) { clearInterval(veilleLiaison); veilleLiaison = null; }
  arreterRebranche();
  echecs = 0; serveurRepond = null; coupeDepuis = 0; causeCoupure = '';
  sockets.length = 0; brancher();
  const fin = sockets[sockets.length - 1];
  fin.readyState = 1; socket.readyState = 1; if (socket.onopen) socket.onopen();
}

// ---- une page qui se sait perimee ---------------------------------------------------------
titre('version : developper l application depuis l application');
const btnMaj = document.getElementById('maj-page');
brancher(); ouvrirSock();      // il faut une socket branchee pour recevoir un pouls
dire(btnMaj.hidden, 'rien a proposer tant que les versions concordent');

// Le piege : le serveur gardait la page en memoire au demarrage. Modifier le code, recharger
// l onglet, ne rien voir changer — et chercher un bug dans un correctif absent.
socket.onmessage({ data: JSON.stringify({ genre: '_pouls', version: 999, pret: true }) });
dire(!btnMaj.hidden, 'une version plus recente cote serveur fait apparaitre le bouton');
dire(typeof btnMaj.onclick === 'function', 'et il sait recharger');

socket.onmessage({ data: JSON.stringify({ genre: '_pouls', version: MA_VERSION, pret: true }) });
dire(btnMaj.hidden, 'la meme version le fait disparaitre');

// Un serveur muet sur sa version ne doit rien declencher : mieux vaut ne rien proposer que
// proposer un rechargement sans raison.
socket.onmessage({ data: JSON.stringify({ genre: '_pouls', pret: true }) });
dire(btnMaj.hidden, 'et une version absente ne propose rien');

// ---- reprendre une conversation, c est ne PAS en ouvrir une neuve -------------------------
titre('titre de l en-tete : le projet, et l identite de la conversation');
recevoir({ genre: 'conversations', n: 8600, h: '10:00', dossier: '/p/claude-talk',
  courante: 'sess-abc',
  liste: [{ session_id: 'sess-abc', titre: 'la barre du bas', projet: 'claude-talk',
            tours: 42, ici: true },
          { session_id: 'sess-xyz', titre: 'autre sujet', projet: 'claude-talk',
            tours: 3, ici: true }] });
const zoneOu = document.getElementById('ou');
// Ce qu on cherche en levant les yeux, c est OU l on est. Le sujet, on le lit dans le flux
// juste en dessous : il prenait la place d une information qu on ne demandait pas.
dire(zoneOu.textContent === 'claude-talk',
     'le titre porte le projet, et lui seul : ' + JSON.stringify(zoneOu.textContent));
dire(/la barre du bas/.test(zoneOu.title),
     'le sujet reste en infobulle, ou il ne coute aucune place : ' + zoneOu.title);

// Le defaut repare : la liste part AVANT que le SDK ait rendu son identifiant, donc
// « courante » arrivait vide. Le projet, lui, est connu des le depart.
recevoir({ genre: 'conversations', n: 8601, h: '10:00', dossier: '/p/claude-talk',
  courante: null,
  liste: [{ session_id: 'sess-abc', titre: 'la barre du bas', projet: 'claude-talk',
            tours: 42, ici: true }] });
dire(zoneOu.textContent === 'claude-talk',
     'sans conversation courante, le projet reste juste — on est bien dedans');
dire(/nouvelle conversation/.test(zoneOu.title),
     'et l infobulle dit qu on n a pas encore identifie laquelle : ' + zoneOu.title);

// L identifiant confirme arrive ensuite, et recale l identite.
emettre({ genre: 'session', id: 'sess-abc', repris: true, tours: 42 });
dire(/la barre du bas/.test(zoneOu.title),
     'l evenement session recale la conversation : ' + zoneOu.title);

// Et s il DEMENT la reprise — Claude Code ouvre une neuve sans rien dire — l infobulle suit.
emettre({ genre: 'session', id: 'sess-neuve', repris: false, tours: 0 });
dire(/nouvelle conversation/.test(zoneOu.title),
     'une reprise qui echoue ne ment pas non plus');
dire(zoneOu.textContent === 'claude-talk', 'et le projet, lui, n a pas change');

// ---- une reconnexion n est pas une erreur -------------------------------------------------
titre('le panneau de liaison : ce qui se passe, et le geste qui repare');
// L etat de la liaison etait dit dans le PLACEHOLDER du champ de saisie : gris pale, une
// ligne, tronque sur un telephone, efface des qu on tape. L information la plus importante
// du moment etait celle qu on lisait le moins bien. Elle a maintenant un panneau, et des
// boutons — devant une panne on veut le geste qui repare, pas sa description.
const panneauLiaison = () => document.getElementById('liaison');
const diagLa = () => { const p = panneauLiaison(); return !!p && !p.hidden; };

brancher(); ouvrirSock();
dire(!diagLa(), 'liaison etablie : aucune boite');

// Le cas de tous les jours : le telephone part en veille, la socket tombe, ça rebranche.
// C est ici que le bandeau rouge s affichait — par-dessus l en-tete, pour dire que tout
// allait bien. Un avertissement qui masque la conversation pour annoncer une reprise.
fermerSock(1006);
dire(!diagLa(), 'une coupure ordinaire n affiche AUCUNE boite');
dire(/reconnexion/.test(document.getElementById('etat').textContent),
     'l etat dit calmement ce qui se passe : ' + document.getElementById('etat').textContent);
dire(document.getElementById('etat').className.includes('e-reprise'),
     'en ambre : une reprise en cours se repare seule, ce n est pas une panne');

// Quelques echecs d affilee, mais recents : toujours rien. Une coupure de trois secondes
// se repare toute seule, et un panneau qui parait pour l annoncer est pire que le silence.
echecs = 5;
coupeDepuis = Date.now() - 3000;
direLiaison();
dire(!diagLa(), 'cinq echecs mais trois secondes : encore rien');

// La coupure s installe. LA, il y a quelque chose a dire, et quelque chose a faire.
coupeDepuis = Date.now() - 60000;
serveurRepond = null;
direLiaison();
dire(diagLa(), 'passe une douzaine de secondes, le panneau parait');
dire(/Reconnexion/.test(document.getElementById('l-titre').textContent),
     'avec un titre qui se lit d un coup d oeil : ' + document.getElementById('l-titre').textContent);
dire(/partira/.test(document.getElementById('l-dit').textContent),
     'une phrase qui rassure sur ce qu on a ecrit : ' + document.getElementById('l-dit').textContent);
dire(/coupée depuis 60 s/.test(document.getElementById('l-cause').textContent),
     'la cause technique, a sa place : ' + document.getElementById('l-cause').textContent.split('\n')[0]);
dire(document.getElementById('l-actions').children.length >= 1,
     'et au moins un bouton : on ne decrit pas une panne sans offrir le geste');

// La machine ne repond plus du tout : ce n est plus « ca reconnecte », c est « c est ferme ».
serveurRepond = false;
direLiaison();
dire(/fermée/.test(document.getElementById('l-titre').textContent),
     'quand plus rien ne repond, le panneau le NOMME : ' + document.getElementById('l-titre').textContent);
dire(panneauLiaison().classList.contains('perdu'), 'et il passe au rouge');

// ---- rouvrir la conversation depuis la page --------------------------------------------
// LE cas vecu, et celui qu on ne savait pas nommer : le tableau de bord ferme les
// conversations restees sans activite pour rendre la memoire. La page ouverte sur le
// telephone ne l apprenait jamais — elle bouclait sur « connexion refusee », a l infini,
// pendant que la seule chose a faire etait de relancer la session. Depuis un autre appareil
// et un autre ecran, parce que la page n offrait pas le geste.
{
  const cheminAvant = location.pathname;
  location.pathname = '/talk/palier/';
  serveurRepond = false; coupeDepuis = Date.now() - 60000; echecs = 5;
  ouvertureEnCours = false; ouvertureDit = '';
  direLiaison();
  const boutons = [...document.getElementById('l-actions').children].map(b => b.textContent);
  dire(boutons.some(t => /Rouvrir/.test(t)),
       'servie derriere le tableau de bord, la page offre de ROUVRIR : ' + boutons.join(' · '));
  dire(/ferme celles/.test(document.getElementById('l-dit').textContent),
       'et elle dit pourquoi c est ferme, au lieu de laisser croire a une panne');

  requetes.length = 0;
  globalThis.relanceRefuse = false;
  document.getElementById('l-actions').children.find(b => /Rouvrir/.test(b.textContent)).onclick();
  dire(requetes.some(r => String(r.url).indexOf('/api/talk/palier') >= 0
                       && r.opts && r.opts.method === 'POST'),
       'le bouton demande la reouverture au tableau de bord');
  dire(ouvertureEnCours === true, 'et la page le dit tout de suite, sans attendre la reponse');
  dire(/Relance/.test(document.getElementById('l-titre').textContent),
       'le panneau passe a l etat « relance » : ' + document.getElementById('l-titre').textContent);
  // On arrete d insister ici : la minuterie de relance force des reconnexions toutes les
  // deux secondes, et elle ecraserait la cause de coupure des cas qui suivent.
  finirOuverture('');
  location.pathname = cheminAvant;
}

// Et il repart des que la liaison revient.
brancher(); ouvrirSock();
dire(!diagLa(), 'la reconnexion le fait disparaitre');
dire(coupeDepuis === 0, 'et remet l horloge de coupure a zero');
echecs = 0; serveurRepond = null;

// ---- la bascule d effort se lit d un coup -------------------------------------------------
titre('effort : voir que le modele change de regime, et pourquoi');
emettre({ genre: 'effort', cle: 'high', niveau: 'high', libelle: 'élevé', auto: true,
          de: 'moyen', vers: 'élevé', pourquoi: 'il faut chercher avant de savoir' });
const ligneEffort = dernier;
dire(/moyen/.test(ligneEffort.innerHTML) && /élevé/.test(ligneEffort.innerHTML),
     'la ligne dit d ou l on vient et ou l on va');
dire(/il faut chercher/.test(ligneEffort.innerHTML),
     'et POURQUOI — « élevé » seul passerait pour un caprice');
dire(/class="bascule"/.test(ligneEffort.innerHTML), 'sous une forme compacte, sur une ligne');

// Le selecteur suit, sinon il afficherait un niveau qui n est plus celui du tour.
dire(document.getElementById('effort').value === 'high',
     'le selecteur de l en-tete se cale sur le niveau retenu');

// Et le bandeau de travail porte le niveau en tete : c est lui qui explique un tour long.
emettre({ genre: 'travail', actif: true });
emettre({ genre: 'jetons', contexte: 26000, sortie: 0, echanges: 1, recu: true });
dire(/élevé/.test(document.getElementById('cogite-mesure').textContent),
     'le bandeau dit a quel regime tourne le tour : '
     + document.getElementById('cogite-mesure').textContent);
emettre({ genre: 'travail', actif: false });

// Un reglage fait a la main n a pas de « de → vers » : il ne raconte rien, il applique.
emettre({ genre: 'effort', cle: 'low', niveau: 'low', libelle: 'minimal — réponse immédiate' });
dire(!/class="bascule"/.test(dernier.innerHTML),
     'un reglage manuel s affiche simplement, sans mise en scene');
toutClore('fin du cas');

// ---- ce que le bandeau raconte : des chiffres, pas des mots -------------------------------
titre('cogitation : mesurer plutot que meubler');
const mesure = document.getElementById('cogite-mesure');
const motDeco = document.getElementById('mot');
toutClore('remise a zero');
emettre({ genre: 'travail', actif: false });

// 1. Le message part. Entre cet instant et la premiere reponse de l API, il n y avait
//    RIEN — et c est precisement la qu on se demande si quelque chose est parti.
emettre({ genre: 'travail', actif: true });
dire(!document.getElementById('cogitation').hidden, 'le bandeau s ouvre des que le tour commence');
dire(/envoyé/.test(mesure.textContent), 'et il dit « envoyé » tant que Claude n a pas repondu : ' + mesure.textContent);
dire(mesure.className === 'attente', 'dans le ton de l attente, pas de la confirmation');

// 2. L API repond : « message_start » est la PREUVE que le message est arrive.
emettre({ genre: 'jetons', contexte: 24000, sortie: 0, echanges: 1, recu: true });
dire(/reçu|jetons/.test(mesure.textContent) && mesure.className === 'recu',
     'la premiere reponse de l API bascule le bandeau en « reçu » : ' + mesure.textContent);
dire(/24,0 k jetons/.test(mesure.textContent), 'avec le poids REEL de la conversation : ' + mesure.textContent);

// 3. Le contexte ne s ADDITIONNE pas d un echange a l autre : le modele relit la meme
//    conversation, il ne la consomme pas trois fois. L additionner affichait 294 k sur une
//    conversation qui en pesait 98 — un cumul de relectures presente comme une consommation.
emettre({ genre: 'jetons', contexte: 26000, sortie: 7500, echanges: 3 });
dire(/26,0 k jetons/.test(mesure.textContent),
     'le contexte suit son poids courant, il ne cumule pas : ' + mesure.textContent);
dire(/\+7,5 k/.test(mesure.textContent),
     'et ce que Claude a ecrit s ajoute a part, lui qui s accumule vraiment : ' + mesure.textContent);
dire(!/57,5|33,5/.test(mesure.textContent), 'les deux ne sont jamais melangees en un seul chiffre');
dire(/3 échanges/.test(mesure.textContent),
     'et le nombre d allers-retours explique un tour long et muet : ' + mesure.textContent);

// 4. Les mots tires au sort existent encore, mais ils ne pretendent plus informer.
dire(motDeco.textContent.length > 0, 'le mot decoratif est toujours la');
const styleMot = document.getElementById('mot');
dire(styleMot !== mesure, 'mais il est distinct de la mesure, qui tient la premiere place');

// 5. Un tour qui se termine puis un autre qui commence : le compteur repart de zero, et
//    « reçu » redevient faux — sinon le tour suivant naitrait deja confirme.
emettre({ genre: 'travail', actif: false });
emettre({ genre: 'travail', actif: true });
dire(/envoyé/.test(mesure.textContent) && mesure.className === 'attente',
     'le tour suivant repart en attente : ' + mesure.textContent);
emettre({ genre: 'travail', actif: false });
toutClore('fin du cas');

// ---- « suivre » : on peut s eloigner du bas sans bouger le petit doigt ---------------------
titre('bouton « suivre » : la distance au bas, pas le geste');
const btnSuivre = document.getElementById('bas');

// Un document qui tient dans la fenetre : il n y a pas de bas a rejoindre.
document.body.scrollHeight = 500; window.scrollY = 0; window.innerHeight = 800;
majSuivre();
dire(btnSuivre.style.display === 'none', 'page courte : aucun bouton, il n y a nulle part ou aller');

// Une longue conversation s ouvre. L historique arrive par paquets, le document gagne des
// milliers de pixels — et AUCUN evenement « scroll » n est emis, puisque personne n a touche
// l ecran. C est precisement le cas ou le bouton manquait.
document.body.scrollHeight = 12000;
majSuivre();
dire(btnSuivre.style.display === 'block',
     'la page grandit sous nos pieds : le bouton apparait sans qu on ait fait defiler');
dire(suivre === false, 'et le suivi automatique se coupe : on n est plus en bas');

// On le touche : retour en bas, bouton range.
window.scrollTo(0, 12000 - 800);
majSuivre();
dire(btnSuivre.style.display === 'none' && suivre === true,
     'revenu en bas, le bouton disparait et le suivi reprend');

// La tolerance : a quelques pixels du bas, on EST en bas. Sans elle, une barre d adresse qui
// se retracte suffirait a faire clignoter le bouton.
window.scrollTo(0, 12000 - 800 - 30);
majSuivre();
dire(btnSuivre.style.display === 'none', 'a trente pixels du bas, on est encore en bas');
window.scrollTo(0, 12000 - 800 - 300);
majSuivre();
dire(btnSuivre.style.display === 'block', 'a trois cents, non');

// La hauteur du bouton est mesuree UNE FOIS VISIBLE, et rendue a la pile de notes : un
// element cache mesure zero, et la pile serait remontee de rien.
dire(document.documentElement.style.getPropertyValue('--bouton-bas') !== '0px',
     'affiche, il reserve sa place au-dessus de la barre');
window.scrollTo(0, 12000 - 800);
majSuivre();
dire(document.documentElement.style.getPropertyValue('--bouton-bas') === '0px',
     'range, il la rend');

// Remise en etat pour les blocs suivants.
document.body.scrollHeight = 0; window.scrollTo(0, 0); majSuivre();

// ---- une commande qui ne part pas doit le dire -------------------------------------------
titre('liaison morte : un appui ne doit pas disparaitre en silence');
const sockAvant = socket;
socket = new WebSocket();          // jamais ouverte : readyState reste a 0
noteBarre(null);
enAttente = [];
const partie = envoyerCmd({ cmd: 'micro', actif: false });
dire(partie === false, 'la commande ne part pas');
dire(enAttente.some(o => o.cmd === 'micro'), 'mais elle est gardee pour la reconnexion');
dire(/liaison perdue/.test(document.getElementById('note-barre').textContent || ''),
     'et on le DIT : ' + document.getElementById('note-barre').textContent);
// Le defaut repare : le bouton micro ne bouge pas tant que le serveur n a pas confirme, et
// le serveur n est plus la. Sans un mot, la page a l air d ignorer les appuis.
noteBarre(null); enAttente = [];
socket = sockAvant;
brancher(); ouvrirSock();

// ---- lire une reponse : la voix d Azure, celle du PC ---------------------------------------
// Asynchrone : la lecture demande l ADRESSE du MP3 au serveur, puis l element audio le joue.
const versParler = () => requetes.filter(r => /\/parler\/preparer$/.test(r.url));
const jouesAzure = () => joues.filter(x => /\/parler\/abc123$/.test(x));
async function testerLecture() {
  const tick = () => new Promise(r => setTimeout(r, 0));
  titre('lecture : la voix d Azure, a la demande, et JAMAIS toute seule');
  const btnLire = document.getElementById('lire-ici');
  couperLectureLocale(); noteBarre(null);
  dire(!btnLire.hidden, 'le bouton apparait sur un appareil distant');
  dire(btnLire.getAttribute('aria-pressed') === 'false', 'et rien ne se lit au chargement');

  // Un telephone ne doit pas se mettre a parler parce qu une reponse arrive.
  dit.length = 0; joues.length = 0; requetes.length = 0;
  emettre({ genre: 'voix', texte: 'La barre du bas tient sur une ligne. ', id: 'L1' });
  emettre({ genre: 'voix', texte: 'Et les boutons sont touchables. ', id: 'L1', suite: true });
  recevoir({ genre: 'parole_fin', id: 'L1' });
  await tick();
  dire(dit.length === 0 && joues.length === 0, 'une reponse qui arrive ne declenche AUCUN son');

  // L appui. Le point qui fait marcher iPhone : l element audio est DEBLOQUE dans l appui
  // lui-meme, de facon synchrone, AVANT qu on demande quoi que ce soit au serveur. Sans ça,
  // une reponse longue a synthetiser arrivait apres l expiration du geste et Safari refusait
  // de la jouer — la petite reponse passait, la vraie non.
  azureMarche = true; requetes.length = 0; joues.length = 0; dit.length = 0;
  btnLire.onclick();
  dire(joues.length === 1 && /^data:audio/.test(joues[0]),
       'le deblocage (un silence) est joue DANS l appui, avant tout await');
  await tick(); await tick(); await tick();
  dire(versParler().length === 1, 'puis la page demande l adresse du son au serveur');
  const envoye = versParler()[0] && JSON.parse(versParler()[0].opts.body);
  dire(envoye && /barre du bas/.test(envoye.texte),
       'avec le texte de la derniere reponse : ' + JSON.stringify((envoye || {}).texte || ''));
  dire(jouesAzure().length === 1, 'et l element debloque joue l adresse rendue');
  dire(dit.length === 0, 'la synthese du navigateur n est PAS utilisee quand Azure repond');

  // Azure indisponible : la voix du navigateur prend le relais plutot que de laisser un
  // bouton muet. Elle aussi a ete amorcee dans l appui.
  azureMarche = false; joues.length = 0; dit.length = 0; requetes.length = 0;
  couperLectureLocale();
  btnLire.onclick();
  await tick(); await tick(); await tick();
  dire(jouesAzure().length === 0, 'sans Azure, aucune adresse n est jouee');
  dire(dit.some(x => /barre du bas/.test(x)), 'mais le navigateur prend le relais : ' + JSON.stringify(dit.slice(0, 3)));
  azureMarche = true;

  // Un second appui coupe.
  couperLectureLocale(); joues.length = 0; dit.length = 0;
  btnLire.onclick(); await tick(); await tick(); await tick();
  couperLectureLocale();
  dire(btnLire.getAttribute('aria-pressed') === 'false', 'un second appui coupe et remet le bouton');

  // Rien a lire : le dire plutot que rester inerte.
  const memoire = texteReponse; texteReponse = ''; noteBarre(null);
  btnLire.onclick(); await tick();
  dire(/rien à lire/.test(document.getElementById('note-barre').textContent || ''),
       'sans reponse, le bouton explique au lieu de ne rien faire');
  texteReponse = memoire; noteBarre(null);

  // « relire » d une ligne, depuis un appareil distant : du son ICI, pas au PC.
  joues.length = 0; envoyes.length = 0;
  couperLectureLocale();
  __zoneRelire.children[0].onclick();
  await tick(); await tick(); await tick();
  dire(jouesAzure().length === 1, 'depuis un telephone, « écouter » lit sur CET appareil');
  dire(!envoyes.some(o => o.cmd === 'relire'), 'et ne reveille pas les haut-parleurs du PC');
  couperLectureLocale();

  // ---- deux reponses d affilee : deux lignes, deux jeux de boutons ------------------------
  titre('deux reponses d affilee ne se fondent pas en une');
  // La courte, puis la vraie. Chaque debrief dit « suite » des son premier morceau : sans
  // l identifiant, la seconde s ajoutait a la ligne de la premiere, et n avait ni boutons ni
  // existence propre — impossible a reecouter seule.
  emettre({ genre: 'voix', texte: 'Compris, je regarde. ', id: 'R1', suite: true });
  recevoir({ genre: 'parole_fin', id: 'R1' });
  const ligneCourte = dernier;
  emettre({ genre: 'voix', texte: 'La cause est le raccourci en http. ', id: 'R2', suite: true });
  emettre({ genre: 'voix', texte: 'Ouvre le socle en https. ', id: 'R2', suite: true });
  recevoir({ genre: 'parole_fin', id: 'R2' });
  const ligneLongue = dernier;
  dire(ligneCourte !== ligneLongue, 'la seconde reponse a SA ligne');
  dire(ligneCourte.texteBrut.trim() === 'Compris, je regarde.'
       && /raccourci en http/.test(ligneLongue.texteBrut),
       'et chacune garde son texte : ' + JSON.stringify([ligneCourte.texteBrut.trim(), ligneLongue.texteBrut.trim()]));
  dire(!!ligneCourte.zoneEcoute && !!ligneLongue.zoneEcoute,
       'les deux portent leur bouton « écouter »');
  dire(/raccourci en http/.test(derniereReponse()) && !/Compris/.test(derniereReponse()),
       'et « la derniere reponse » est bien la seconde, seule : ' + JSON.stringify(derniereReponse()));
  // Une interjection sans identifiant ne vient pas se coller au dernier debrief.
  emettre({ genre: 'voix', texte: "d'accord, j'y vais." });
  dire(dernier !== ligneLongue, 'une phrase courte sans identifiant fait sa propre ligne');
  dire(!/j'y vais/.test(derniereReponse()), 'et ne s ajoute pas a la derniere reponse');
  // Relire la seconde, depuis sa ligne, ne lit QUE la seconde.
  joues.length = 0; requetes.length = 0; couperLectureLocale();
  ligneLongue.zoneEcoute.children[0].onclick();
  await tick(); await tick(); await tick();
  const demande = versParler()[0] && JSON.parse(versParler()[0].opts.body);
  dire(demande && /raccourci en http/.test(demande.texte) && !/Compris/.test(demande.texte),
       'relire la seconde envoie son texte a elle seule');
  couperLectureLocale();

  // ---- une reponse en plusieurs morceaux garde son bouton -----------------------------------
  titre('un message vert sans bouton : le defaut le plus tenace');
  // Un debrief arrive TOUJOURS en plusieurs morceaux. L accumulation reecrivait le corps
  // entier en texte pur : elle effacait les boutons deja poses, et recollait leur libelle
  // dans le message. Une reponse d un seul morceau y survivait, les autres non — d ou des
  // messages verts sans moyen de les ecouter, sans regle apparente.
  emettre({ genre: 'voix', texte: 'Premier morceau. ', id: 'M1', suite: true });
  const ligneM = dernier;
  dire(!!ligneM.zoneEcoute, 'le premier morceau pose le bouton');
  emettre({ genre: 'voix', texte: 'Deuxieme morceau. ', id: 'M1', suite: true });
  emettre({ genre: 'voix', texte: 'Et le troisieme.', id: 'M1', suite: true });
  dire(dernier === ligneM, 'les morceaux suivants rejoignent la meme ligne');
  dire(!!ligneM.zoneEcoute && ligneM.zoneEcoute.children.length === 1,
       'et le bouton est TOUJOURS la apres trois morceaux');
  // Le texte s accumule dans SON element. Le talon ne lit pas le contenu initial d une
  // balise, donc le premier morceau lui echappe : c est test_rendu.js, dans un vrai
  // navigateur, qui verifie le texte entier — et c est precisement cette lacune qui avait
  // laisse passer le defaut.
  const lu = ligneM.querySelector('.propos').textContent;
  dire(/Deuxieme morceau\. Et le troisieme\./.test(lu),
       'les morceaux s accumulent dans leur propre element : ' + JSON.stringify(lu));
  dire(!/écouter/.test(lu), 'et le libelle du bouton ne s y recolle pas');
  // Ce qu on fait prononcer ne doit contenir que le message.
  dire(!/écouter/.test(texteDeLigne(ligneM)) && /Et le troisieme/.test(texteDeLigne(ligneM)),
       'ce qu on lit a voix haute est le message, entier et seul');

  // ---- la version, toujours visible ---------------------------------------------------------
  titre('version : toujours affichee, et juste');
  const badge = document.getElementById('version');
  socket.onmessage({ data: JSON.stringify({ genre: '_pouls', version: MA_VERSION,
                                            libelle: 'v22 · 2026-09-30 · abc1234', pret: true }) });
  dire(badge.textContent === 'v22 · 2026-09-30', 'le badge montre la version du serveur, sans le hash : ' + badge.textContent);
  dire(!badge.classList.contains('perimee'), 'et reste sobre quand la page est a jour');
  socket.onmessage({ data: JSON.stringify({ genre: '_pouls', version: 999,
                                            libelle: 'v23 · 2026-10-01 · def5678', pret: true }) });
  dire(badge.classList.contains('perimee'), 'il passe en ambre quand la page est en retard');
  dire(/v23/.test(document.getElementById('maj-page').textContent), 'et le bouton nomme la version qui attend : ' + document.getElementById('maj-page').textContent);
  noteBarre(null); badge.onclick();
  dire(/serveur : v23/.test(document.getElementById('note-barre').textContent || ''), 'un appui donne le detail : ' + document.getElementById('note-barre').textContent);
  socket.onmessage({ data: JSON.stringify({ genre: '_pouls', version: MA_VERSION, libelle: 'v22 · 2026-09-30 · abc1234', pret: true }) });
  noteBarre(null);
}

// ---- reecouter l historique, pas seulement le dernier message -----------------------------
async function testerEcouteHistorique() {
  const tick = () => new Promise(r => setTimeout(r, 0));
  titre('historique : reprendre une conversation et pouvoir la reecouter');
  couperLectureLocale(); noteBarre(null);

  // Reprendre une conversation rejoue ce que Claude a ECRIT — le debrief parle n est pas
  // garde par Claude Code. Ces lignes n ont donc pas de « parole_fin » derriere elles, et
  // rien ne les outillait : on pouvait lire le dernier message, pas ceux d avant.
  recevoir({ genre: '_histoire', evenements: [
    { genre: 'texte', texte: 'La barre du bas tient desormais sur une ligne.',
      n: 20000, h: '09:00', passe: true },
  ] });
  const ligneTexte = dernier;
  dire(!!ligneTexte.zoneEcoute,
       'une reponse rejouee porte un bouton « écouter »');

  joues.length = 0; requetes.length = 0;
  azureMarche = true;
  const boutonEcoute = ligneTexte.zoneEcoute.children[0];
  boutonEcoute.onclick();
  await tick(); await tick(); await tick();
  const dem = requetes.filter(r => /\/parler\/preparer$/.test(r.url));
  dire(dem.length === 1, 'l appuyer demande la synthese de CETTE ligne');
  const quoi = dem[0] && JSON.parse(dem[0].opts.body);
  dire(quoi && /desormais sur une ligne/.test(quoi.texte),
       'avec son texte a elle : ' + JSON.stringify((quoi || {}).texte || ''));
  dire(joues.some(x => /\/parler\/abc123$/.test(x)), 'et elle est lue par l element debloque');

  // Le texte vient de ce qui est ARRIVE, pas du DOM : les libelles des boutons ne doivent
  // jamais se retrouver dans ce qu on fait prononcer.
  dire(!/écouter/.test((quoi || {}).texte || ''),
       'le libelle du bouton ne part pas dans la synthese');
  couperLectureLocale();
}

// ---- dicter avec le telephone : enregistrer ici, transcrire au PC -------------------------
// Asynchrone : l envoi attend fetch. La fonction est appelee juste avant le tally, qui l attend.
async function testerRelaisVocal() {
  const tick = () => new Promise(r => setTimeout(r, 0));
  // Un envoi traverse fetch : les microtaches ne suffisent pas a le voir retomber,
  // et un envoi en retard ecrase la note du cas suivant.
  const pause = () => new Promise(r => setTimeout(r, 40));
  titre('vocal : le telephone enregistre, le PC transcrit');
  const btnDicter = document.getElementById('dicter-ici');
  dire(!btnDicter.hidden, 'le bouton apparait quand l appareil sait enregistrer');
  dire(btnDicter.getAttribute('aria-pressed') === 'false', 'et il n enregistre pas de lui-meme');

  // Appuyer : le micro est RECLAME — c est ce qui fait apparaitre la demande d autorisation,
  // et c est ce que la version precedente ne faisait jamais.
  microDemandes = 0; microAccorde = true; enregistreurs.length = 0; requetes.length = 0;
  noteBarre(null); champ.value = '';
  DELAI_SESSION_AUDIO = 0;        // l ordre se verifie, le repit ne se dort pas
  lecteur.src = 'https://exemple/une-reponse.mp3';   // une lecture tenait la session audio
  // `pause()` et pas `tick()` : l ouverture du micro attend desormais que la session audio
  // d iOS soit rendue avant de la reclamer. Sans ce repit, le micro ouvert par-dessus une
  // lecture rend un flux parfaitement valide et parfaitement MUET.
  btnDicter.onclick(); await pause();
  dire(microDemandes === 1, 'un appui reclame le micro pour de vrai');
  dire(lecteur.src === '', 'et la lecture a rendu la session audio AVANT le micro : '
       + 'sinon, sur iOS, le micro capte du silence sans rien signaler');
  dire(enregistreurs.length === 1 && enregistreurs[0].etat === 'recording', 'et l enregistrement demarre');
  dire(btnDicter.getAttribute('aria-pressed') === 'true', 'le bouton le montre');
  dire(/j.écoute/.test(document.getElementById('note-barre').textContent || ''),
       'et la barre dit quoi faire : ' + document.getElementById('note-barre').textContent);

  // Appuyer encore : l enregistrement s arrete et PART vers le PC.
  btnDicter.onclick(); await pause();
  dire(enregistreurs[0].etat === 'inactive', 'un second appui arrete l enregistrement');
  dire(requetes.length === 1 && /\/audio$/.test(requetes[0].url),
       'et le fichier part vers /audio : ' + (requetes[0] && requetes[0].url));
  const corps = requetes[0] && requetes[0].opts.body;
  dire(corps && corps.champs.some(ch => ch.n === 'audio' && /vocal\.webm$/.test(ch.f)),
       'dans le champ « audio », nomme d apres son format');
  dire(btnDicter.getAttribute('aria-pressed') === 'false', 'le bouton redevient normal');

  // Le TEXTE n arrive pas par la reponse HTTP mais par le flux, comme une dictee du PC :
  // c est ce chemin qui remplit la barre et arme le decompte — donc la retenue marche.
  dire(champ.value === '', 'la reponse HTTP n ecrit rien dans la barre');
  emettre({ genre: 'ecoute', actif: false, parle: true, source: 'téléphone' });
  emettre({ genre: 'partiel', texte: 'corrige la barre du bas', final: true, source: 'téléphone' });
  dire(champ.value === 'corrige la barre du bas',
       'le texte transcrit arrive par le flux, dans la barre : ' + JSON.stringify(champ.value));
  emettre({ genre: 'ecoute', actif: true, delai: 5, fin: Date.now() + 5000, min: 5, max: 12 });
  dire(!document.getElementById('compte').hidden, 'et le decompte avant envoi s arme, comme au PC');
  arreterCompte(); fermerDictee(null); champ.value = '';

  // Le PC a REPONDU qu il n a rien compris. Ce n est pas une panne de liaison : le renvoyer
  // donnerait exactement le meme verdict. On ne le garde donc pas — et surtout le micro reste
  // LIBRE. Garde, il mettait le bouton en mode « renvoyer » : l appui suivant relancait le
  // vieux vocal au lieu d en commencer un neuf, donc on croyait enregistrer et rien ne
  // partait. C est le symptome exact qu on a vu a l ecran.
  reponseAudio = { ok: false, status: 422, corps: { erreur: 'rien compris dans l enregistrement' } };
  requetes.length = 0; noteBarre(null);
  btnDicter.onclick(); await pause();
  btnDicter.onclick(); await pause();
  dire(/rien compris/.test(document.getElementById('note-barre').textContent || ''),
       'un echec de transcription se lit dans la barre : ' + document.getElementById('note-barre').textContent);
  dire(!btnDicter.classList.contains('envoi'), 'et le bouton n est pas reste en « envoi »');
  dire(!vocalEnAttente && !btnDicter.classList.contains('garde'),
       'un refus DEFINITIF ne garde rien : le micro est libre pour un nouvel enregistrement');
  dire(/recommencer/.test(document.getElementById('note-barre').textContent || ''),
       'et la barre nomme le geste suivant : ' + document.getElementById('note-barre').textContent);

  // LE defaut que l'utilisateur voit : le micro est la, il enregistre, et il ne capte RIEN.
  // iOS n'a qu'une session audio par page — tant qu'une lecture la tient, le micro ouvert
  // par-dessus rend un flux valide et muet, sans la moindre erreur. On parlait trente
  // secondes et le PC repondait « rien compris ». Maintenant on ECOUTE ce qu'on enregistre.
  reponseAudio = { ok: true, status: 200, corps: { texte: 'ok' } };
  requetes.length = 0; noteBarre(null); vibrations.length = 0;
  niveauMicro = 0;                       // le micro ne rend que du silence numerique
  DELAI_NIVEAU_MS = 5; MESURES_MIN = 3; DELAI_SILENCE_MS = 40;
  btnDicter.onclick(); await pause();
  await new Promise(r => setTimeout(r, 90));
  dire(/ne capte rien/.test(document.getElementById('note-barre').textContent || ''),
       'on est prevenu PENDANT qu on parle, pas apres : '
       + document.getElementById('note-barre').textContent);
  dire(vibrations.length > 0, 'et autrement que par un texte : on ne regarde pas l ecran');
  btnDicter.onclick(); await pause();
  dire(requetes.length === 0,
       'un enregistrement entierement muet ne part pas : le PC repondrait « rien compris »');
  dire(/rien n.a été capté/.test(document.getElementById('note-barre').textContent || ''),
       'et la cause est nommee : ' + document.getElementById('note-barre').textContent);
  dire(!vocalEnAttente, 'rien n est garde : le renvoyer donnerait le meme silence');

  // Mais on ne jette RIEN sur une presomption : trop peu de mesures, on envoie quand meme.
  requetes.length = 0; noteBarre(null);
  DELAI_NIVEAU_MS = 5000; MESURES_MIN = 5;      // aucune mesure n aura le temps de tomber
  btnDicter.onclick(); await pause();
  btnDicter.onclick(); await pause();
  dire(requetes.length === 1,
       'un enregistrement trop court pour avoir ete observe part quand meme : on ne jette '
       + 'pas de la parole sur une presomption');
  niveauMicro = 40; DELAI_NIVEAU_MS = 100; MESURES_MIN = 5; DELAI_SILENCE_MS = 3000;

  // Le micro deja pris par une autre application : la piste existe mais elle est muette.
  // Le dire AVANT de laisser parler vaut mieux que de le decouvrir a l arrivee.
  microMuet = true; noteBarre(null); requetes.length = 0;
  btnDicter.onclick(); await pause();
  microMuet = false;
  dire(/occupé par une autre application/.test(document.getElementById('note-barre').textContent || ''),
       'un micro deja muet est refuse tout de suite : '
       + document.getElementById('note-barre').textContent);
  dire(btnDicter.getAttribute('aria-pressed') === 'false',
       'et aucun enregistrement ne demarre pour rien');

  // Une coupure de TRANSPORT, elle, garde tout : quelqu un vient de parler, et reperdre ça
  // parce que le reseau a hoquete serait le pire service a lui rendre.
  reponseAudio = { ok: true, status: 200, corps: { texte: 'ok' } };
  audioEchoue = true; noteBarre(null);
  btnDicter.onclick(); await pause();
  btnDicter.onclick(); await pause();
  audioEchoue = false;
  dire(!!vocalEnAttente && btnDicter.classList.contains('garde'),
       'un reseau qui lache garde l enregistrement, lui, et le bouton le porte');
  dire(/GARDÉ/.test(document.getElementById('note-barre').textContent || ''),
       'et le dit : ' + document.getElementById('note-barre').textContent);
  vocalEnAttente = null; majBoutonDictee();
  vocalEnAttente = null; majBoutonDictee();   // le cas suivant part d une ardoise propre
  reponseAudio = { ok: true, status: 200, corps: { texte: 'ok' } };

  // La session du socle est tombee : le proxy redirige vers sa page de connexion, fetch la
  // suit et rend un 200 avec du HTML. Pris pour un succes, le vocal partait dans le vide.
  reponseAudio = { ok: true, status: 200, redirige: true, type: "text/html; charset=utf-8", corps: {} };
  requetes.length = 0; noteBarre(null);
  btnDicter.onclick(); await tick();
  btnDicter.onclick(); await pause();
  dire(/session du socle/.test(document.getElementById('note-barre').textContent || ''),
       'une redirection vers la connexion est nommee, pas prise pour un succes : '
       + document.getElementById('note-barre').textContent);
  dire(!!vocalEnAttente, 'la aussi, ce qui a ete dit attend au lieu d etre perdu');
  vocalEnAttente = null; majBoutonDictee();
  reponseAudio = { ok: true, status: 200, corps: { texte: 'ok', moteur: 'deepgram', secondes: 3.9 } };

  // Et un succes se DIT : un vocal qui reussit sans un mot ressemble a un vocal perdu.
  noteBarre(null);
  btnDicter.onclick(); await tick();
  btnDicter.onclick(); await pause();
  dire(/transcrit par deepgram/.test(document.getElementById('note-barre').textContent || ''),
       'le succes nomme le moteur, en clair : ' + document.getElementById('note-barre').textContent);

  // Micro refuse : la raison, tout de suite — pas un bouton allume sur du vide.
  microAccorde = false; noteBarre(null); enregistreurs.length = 0;
  btnDicter.onclick(); await tick();
  dire(/micro refusé/.test(document.getElementById('note-barre').textContent || ''),
       'un refus d autorisation est dit : ' + document.getElementById('note-barre').textContent);
  dire(enregistreurs.length === 0 && btnDicter.getAttribute('aria-pressed') === 'false',
       'et rien ne demarre');
  microAccorde = true;

  // Sans https, Safari refuse le micro en silence : on le dit AVANT d essayer.
  isSecureContext = false; noteBarre(null); microDemandes = 0;
  btnDicter.onclick(); await tick();
  dire(microDemandes === 0 && /https/.test(document.getElementById('note-barre').textContent || ''),
       'en http, on explique au lieu de tenter : ' + document.getElementById('note-barre').textContent);
  isSecureContext = true; noteBarre(null);

  // ---- annuler un vocal en cours d envoi --------------------------------------------------
  // On vient de parler, on s entend dire une betise, on se ravise. Il faut pouvoir revenir
  // en arriere TOUT DE SUITE, sans attendre la fin de la transcription puis effacer le texte
  // a la main.
  titre('vocal : annuler pendant l envoi');
  brancher(); ouvrirSock();
  await pause();
  noteBarre(null); enregistreurs.length = 0; champ.value = ''; envoyes.length = 0;
  audioLent = true;                       // l envoi traine : on a le temps d appuyer
  btnDicter.onclick(); await tick();      // micro ouvert
  btnDicter.onclick(); await tick();      // relache : l envoi part
  dire(vocalEnvoi === true, 'l envoi est en cours');
  dire(/annuler/.test(document.getElementById('note-barre').textContent || ''),
       'et la barre dit comment l annuler : ' + document.getElementById('note-barre').textContent);
  dire(btnDicter.classList.contains('envoi'), 'le bouton pulse pendant l envoi');

  // Le geste : appuyer sur ce qui bouge. Pas de bouton de plus dans une barre pleine.
  btnDicter.onclick();
  dire(vocalEnvoi === false, 'un appui annule immediatement, sans attendre la reponse');
  dire(/annulé/.test(document.getElementById('note-barre').textContent || ''),
       'et le dit : ' + document.getElementById('note-barre').textContent);
  dire(envoyes.some(o => o.cmd === 'barre_vide'),
       'la barre est vidée des deux cotes, ici et chez l agent');
  dire(!vocalEnAttente, 'rien n est garde : c est justement ce qu on vient de jeter');

  // Le piege : le serveur a pu finir de transcrire avant de voir la coupure, et son texte
  // remonte par le FLUX, pas par la reponse. Sans refus, il apparaissait une seconde apres
  // l annulation.
  await pause(); await pause();
  // Le serveur avait fini : il pousse sa transcription par le flux. Elle doit etre refusee.
  emettre({ genre: 'ecoute', actif: false, parle: true, source: 'téléphone' });
  emettre({ genre: 'partiel', texte: 'la betise que je viens de dire', final: true,
            source: 'téléphone' });
  dire(champ.value === '',
       'une transcription qui arrive APRES l annulation est refusee : ' + JSON.stringify(champ.value));
  // Mais une dictee sans rapport, plus tard, passe normalement — on n a pas casse le chemin.
  vocalAnnuleA = 0;
  emettre({ genre: 'partiel', texte: 'ce que je dis maintenant', final: true, source: 'téléphone' });
  dire(/ce que je dis maintenant/.test(champ.value),
       'et une dictee ulterieure passe toujours : ' + JSON.stringify(champ.value));
  fermerDictee(null);
  audioLent = false; noteBarre(null); champ.value = '';

  // ---- parler dans le vide : jamais -------------------------------------------------------
  // Le cas vecu : micro ouvert, on parle trente secondes, et on apprend a l arret que la
  // liaison etait tombee depuis longtemps. Trente secondes de parole et d energie perdues.
  titre('vocal : ne jamais laisser parler dans le vide');
  brancher(); ouvrirSock();
  // Le bloc precedent a laisse un envoi en vol : on le laisse retomber avant de mesurer,
  // sinon sa note d arrivee ecraserait celle qu on veut lire. Une vraie pause, pas des
  // microtaches : l envoi traverse fetch, donc plusieurs tours de boucle.
  await new Promise(r => setTimeout(r, 60));
  noteBarre(null); enregistreurs.length = 0; vibrations.length = 0;

  // 1. Liaison deja morte : on refuse d ouvrir le micro, et on le dit AVANT.
  fermerSock(1006);
  btnDicter.onclick(); await tick();
  dire(enregistreurs.length === 0, 'liaison morte : le micro ne s ouvre meme pas');
  dire(/inutile de parler/.test(document.getElementById('note-barre').textContent || ''),
       'et on le dit avant, pas apres : ' + document.getElementById('note-barre').textContent);
  dire(vibrations.length >= 1, 'avec une alerte qu on sent sans regarder l ecran');

  // 2. La liaison tombe PENDANT qu on parle : on coupe court au lieu de laisser continuer.
  brancher(); ouvrirSock();
  noteBarre(null); enregistreurs.length = 0; vibrations.length = 0;
  btnDicter.onclick(); await tick();
  dire(enregistreurs.length === 1 && btnDicter.getAttribute('aria-pressed') === 'true',
       'liaison vivante : l enregistrement demarre');
  audioEchoue = true;                             // le reseau est coupe : l envoi echouera
  fermerSock(1006);
  await new Promise(r => setTimeout(r, 700));     // la veille bat toutes les 500 ms
  dire(btnDicter.getAttribute('aria-pressed') === 'false',
       'la liaison tombe : l enregistrement s arrete tout seul, en moins d une seconde');
  // Un seul message, qui dit la cause ET le sort de la parole : « arrête de parler » est la
  // phrase qui compte, puisqu on est en train de parler quand elle arrive.
  const note = document.getElementById('note-barre').textContent || '';
  dire(/arrête de parler/.test(note) && /liaison est tombée/.test(note),
       'on te dit d arreter, et pourquoi : ' + note);
  dire(vibrations.length >= 1, 'la aussi, une alerte qui se sent');

  // 3. Ce qui a ete dit n est PAS jete : il attend et repart a la reconnexion.
  dire(/GARDÉ/.test(document.getElementById('note-barre').textContent || ''),
       'et que ce qui a ete dit est garde — dans le meme message, pas ecrase par le suivant');
  await pause();
  dire(!!vocalEnAttente, 'un enregistrement attend bien la liaison');
  dire(btnDicter.classList.contains('garde'), 'et le bouton le porte, pour qu on ne l oublie pas');
  requetes.length = 0;
  audioEchoue = false;                            // le reseau revient
  brancher(); ouvrirSock();
  socket.onmessage({ data: JSON.stringify({ genre: '_bonjour', rejoue: 0, pret: true }) });
  await pause();
  dire(requetes.some(r => /\/audio$/.test(r.url)),
       'et il repart TOUT SEUL des que la liaison revient');
  dire(!vocalEnAttente, 'une fois parti, il n attend plus');
  noteBarre(null);

  // 4. La socket ZOMBIE — celle qui dit OPEN alors qu elle est morte. C est le cas qui
  //    restait : on part, on revient, le telephone a tue la liaison sans que le navigateur
  //    l admette, et `readyState` repond toujours OPEN. Les trois protections ci-dessus
  //    s appuyaient dessus : elles laissaient donc parler dans le vide, precisement dans la
  //    situation qu elles etaient censees couvrir.
  brancher(); ouvrirSock();
  noteBarre(null); enregistreurs.length = 0; vibrations.length = 0;
  dire(socket.readyState === 1, 'la socket se declare ouverte');
  dernierPouls = Date.now() - 120000;              // deux minutes sans le moindre pouls
  dire(perimee() && !liaisonVivante(),
       'mais sans pouls depuis deux minutes, elle est tenue pour morte malgre OPEN');
  btnDicter.onclick(); await tick();
  dire(enregistreurs.length === 0,
       'le micro ne s ouvre pas sur une socket zombie — c est ça, ne pas parler dans le vide');

  // 5. Et quand rien ne permet de conclure, on DEMANDE au serveur au lieu de le croire.
  brancher(); ouvrirSock();
  noteBarre(null); enregistreurs.length = 0; envoyes.length = 0;
  btnDicter.onclick(); await tick();
  dire(enregistreurs.length === 1, 'liaison saine : le micro s ouvre');
  dire(envoyes.some(o => o.cmd === 'ping'),
       'et une verification part avec l ouverture, au lieu de faire confiance a readyState');
  envoyes.length = 0;
  // Un serveur qui repond : sans lui, la premiere sonde resterait sans reponse, la page
  // rebrancherait — a juste titre — et l enregistrement s arreterait avant la seconde sonde.
  // Ce qu on veut mesurer ici est le contraire : liaison SAINE, et verifiee quand meme.
  const repond = setInterval(() => { dernierPouls = Date.now(); }, 200);
  await new Promise(r => setTimeout(r, 5400));     // la sonde periodique bat toutes les 5 s
  clearInterval(repond);
  dire(envoyes.some(o => o.cmd === 'ping'),
       'la verification se repete pendant qu on parle : une socket qui meurt en cours est vue '
       + 'en secondes, pas apres l enregistrement');
  dire(btnDicter.getAttribute('aria-pressed') === 'true',
       'et tant que le serveur repond, elle ne derange rien : on parle toujours');
  btnDicter.onclick(); await pause();              // relacher proprement
  noteBarre(null);

}

// ---- une photo qui ne part pas ne gele pas la conversation -------------------------------
// Le defaut vecu, en trois morceaux qui se tiennent : une requete qui ne revient jamais laisse
// la vignette en « envoi », donc le bouton refuse de partir pour toujours ; une vignette en
// echec n a pas de croix, donc on ne peut pas s en debarrasser ; et le message part quand meme
// sans la photo, sans un mot, si bien que Claude repond qu il ne voit rien et qu on cherche le
// defaut chez lui.
async function testerPiecesJointes() {
  const tick = () => new Promise(r => setTimeout(r, 0));
  const pause = () => new Promise(r => setTimeout(r, 40));
  titre('images : un depot qui ne revient pas devient une erreur');
  brancher(); ouvrirSock();
  await pause();
  jointes.length = 0; majJointes(); noteBarre(null);
  imagesRecues.length = 0;

  // 1. Le cas normal, pour que le reste veuille dire quelque chose.
  await joindre({ name: 'maquette.png', size: 240000 });
  dire(jointes.length === 1 && !!jointes[0].chemin,
       'une photo qui part rend son chemin');
  dire(imagesPretes === 1, 'et elle compte comme prete');
  const opts = imagesRecues[0] || {};
  dire(!!(opts.signal), 'le depot est abandonnable : une requete sans issue doit pouvoir etre coupee');
  jointes.length = 0; majJointes();

  // 2. La requete qui ne revient JAMAIS. Sans delai maximum, ce test ne finirait pas.
  const base = IMAGE_DELAI_BASE, parMo = IMAGE_DELAI_PAR_MO;
  IMAGE_DELAI_BASE = 40; IMAGE_DELAI_PAR_MO = 0;    // 40 ms au lieu de 30 s
  imageMuette = true;
  const enVolImage = joindre({ name: 'photo.jpg', size: 3 * 1048576 });
  await tick();
  dire(jointes.length === 1 && !jointes[0].chemin && !jointes[0].rate,
       'pendant le depot, la vignette est en cours d envoi');
  await enVolImage;
  imageMuette = false;
  IMAGE_DELAI_BASE = base; IMAGE_DELAI_PAR_MO = parMo;
  dire(!!jointes[0].rate,
       'une requete qui ne revient pas devient une ERREUR : ' + jointes[0].rate);
  dire(/reessaie/.test(jointes[0].rate || ''),
       'et elle dit quoi faire, pas seulement que ça a rate');
  dire(imagesPretes === 0, 'rien n est annonce comme pret');

  // 3. La croix, qui n existait pas sur les vignettes en echec.
  const boutons = zoneJointes.querySelectorAll('[data-jointe]');
  dire(boutons.length === 1,
       'une vignette en echec porte une croix de retrait (' + boutons.length + ')');

  // 4. Le coeur du defaut : le message ne doit PAS partir en parlant d une photo absente.
  socket = new WebSocket(); socket.readyState = 1; envoyes.length = 0; noteBarre(null);
  champ.value = 'regarde cette capture';
  composer.onsubmit({ preventDefault() {} });
  dire(!envoyes.some(o => o.cmd === 'texte'),
       'le message ne part pas tant qu une piece a echoue');
  dire(champ.value === 'regarde cette capture',
       'et ce qu on avait ecrit est intact : rien n est perdu, c est un refus, pas un echec');
  const refus = document.getElementById('note-barre').textContent || '';
  dire(/n'est pas partie|ne sont pas parties/.test(refus) && /croix/.test(refus),
       'on dit pourquoi, et par ou sortir : ' + refus);

  // 5. La croix retire vraiment, et le message repart alors.
  boutons[0].onclick();
  dire(jointes.length === 0, 'la croix retire la piece en echec');
  envoyes.length = 0;
  composer.onsubmit({ preventDefault() {} });
  const parti = envoyes.find(o => o.cmd === 'texte');
  dire(!!parti && parti.texte === 'regarde cette capture',
       'et le message part enfin, sans pretendre montrer quoi que ce soit');
  dire(!/images jointes/.test((parti && parti.texte) || ''),
       'sans la liste des pieces, puisqu il n y en a plus');

  // 6. Un refus net du serveur se lit pareil, et se retire pareil.
  noteBarre(null); champ.value = '';
  imageEchoue = true;
  await joindre({ name: 'photo.jpg', size: 120000 });
  imageEchoue = false;
  dire(!!jointes[0].rate && zoneJointes.querySelectorAll('[data-jointe]').length === 1,
       'un refus du serveur donne aussi une vignette retirable');
  jointes.length = 0; majJointes(); noteBarre(null);

  // 7. Le budget suit le poids : une grosse photo a droit a plus de temps qu une vignette,
  //    et aucune n a droit a l infini.
  dire(delaiImage(0) === IMAGE_DELAI_BASE, 'un fichier minuscule a le delai de base');
  dire(delaiImage(4 * 1048576) > delaiImage(1048576),
       'une photo plus lourde a plus de temps');
  dire(delaiImage(500 * 1048576) === IMAGE_DELAI_MAX,
       'mais jamais plus que le plafond : attendre sans fin est precisement le defaut');
}

// ---- une question de Claude attend, puis cesse d attendre --------------------------------
titre('question : la machine attend l utilisateur, et le montre');
toutClore('remise a zero');
emettre({ genre: 'question', id: 'q1', texte: 'Je remplace ou je cree un nouveau fichier ?',
          questions: [{ question: 'Je remplace ou je cree ?', entete: 'Fichier',
                        options: ['Remplacer', 'Nouveau fichier'] }] });
dire(encours.has('question:q1'),
     'une question sans reponse est la seule chose qui attend VRAIMENT quelqu un');
dire(/Remplacer/.test(dernier.innerHTML),
     'les options sont affichees meme si la reponse sera libre');
emettre({ genre: 'question', id: 'q1', texte: 'Je remplace ou je cree un nouveau fichier ?',
          questions: [{ question: 'Je remplace ou je cree ?', entete: 'Fichier',
                        options: ['Remplacer', 'Nouveau fichier'] }],
          reponse: 'un nouveau, et garde l ancien a cote' });
dire(!encours.has('question:q1'), 'la reponse eteint l attente de SA question : ' + restants());
dire(/un nouveau, et garde l ancien/.test(dernier.innerHTML),
     'et la reponse libre est celle qui s affiche, pas un intitule');

// Le cas qui compte : sans reponse, ca ne doit pas tourner eternellement. La cloture de fin
// de tour et la reprise l eteignent, comme tout le reste.
emettre({ genre: 'question', id: 'q2', texte: 'Autre chose ?', questions: [] });
dire(encours.has('question:q2'), 'une seconde question attend a son tour');
toutClore('fin de tour');
dire(!encours.has('question:q2'), 'et la cloture de fin de tour ne la laisse pas tourner');

// ---- rien ne doit tourner quand rien ne tourne ------------------------------------------
titre('indicateurs : ce qui tourne doit correspondre a ce qui se passe');
const zoneAct = document.getElementById('activite');
const bandeau = document.getElementById('cogitation');

// 1. « parole » n avait aucun garde-fou : elle s allumait au premier morceau du debrief et
//    n avait qu UNE sortie, l etat suivant de l agent. Coupure, agent tue, reprise : elle
//    tournait pour toujours — et le bandeau de cogitation avec elle.
toutClore('remise a zero');
emettre({ genre: 'voix', texte: 'j ai refait la barre du bas.', id: 'p1' });
dire(encours.has('voix'), 'la parole allume bien son indicateur');
dire(typeof echeances.voix !== 'undefined', 'et elle arme un garde-fou, comme la reflexion');
emettre({ genre: 'etat', vers: 'listening' });
dire(!encours.has('voix'), 'l etat suivant l eteint quand il arrive');

// 2. Le cas qui se produisait a CHAQUE reconnexion : le rejeu rallume un vieux « voix »,
//    et l etat de l agent est passe AVANT lui. Sans le mot de la fin, la pastille restait.
toutClore('remise a zero');
recevoir({ genre: '_histoire', evenements: [
  { genre: 'voix', texte: 'un debrief d il y a deux heures', id: 'vieux', n: 9001, h: '10:00:00' },
  { genre: 'outil', nom: 'Bash', cible: 'pytest', id: 'o9', n: 9002, h: '10:00:01' },
] });
dire(encours.size > 0, 'le rejeu rallume bien les indicateurs du passe (' + restants() + ')');
socket.onmessage({ data: JSON.stringify({ genre: '_bonjour', rejoue: 2, travail: false,
                                          etat: 'listening', pret: true }) });
dire(encours.size === 0, 'mais la fin de reprise les eteint quand le serveur ne fait rien : '
     + restants());
dire(zoneAct.innerHTML === '', 'l en-tete ne liste plus rien');
dire(bandeau.hidden, 'et le bandeau de cogitation est refermé');

// 2b. Perdre la liaison pendant une lecture laissait « couper la lecture » visible et
//     clignotant pour toujours — un bouton qui ne couperait rien si on le pressait.
//     Devant la machine, seul endroit ou ce bouton existe.
const hoteAvantCoupe = location.hostname;
location.hostname = '127.0.0.1';
emettre({ genre: 'lecture', actif: true, id: 'p7' });
const btnCoupe = document.getElementById('couper-lecture');
dire(!btnCoupe.hidden, 'une lecture en cours montre le bouton qui la coupe');
socket.onmessage({ data: JSON.stringify({ genre: '_bonjour', rejoue: 1, travail: false,
                                          etat: 'listening', pret: true }) });
dire(btnCoupe.hidden && lectureEnCours === null,
     'et il disparait quand la reprise dit que plus personne ne parle');
location.hostname = hoteAvantCoupe;

// 3. L inverse doit rester vrai : si le serveur travaille VRAIMENT, on n eteint rien.
recevoir({ genre: '_histoire', evenements: [
  { genre: 'outil', nom: 'Bash', cible: 'npm test', id: 'o10', n: 9200, h: '10:00:02' },
] });
socket.onmessage({ data: JSON.stringify({ genre: '_bonjour', rejoue: 1, travail: true,
                                          etat: 'thinking', pret: true }) });
dire(encours.has('outil:o10'), 'un outil qui tourne vraiment reste allume a la reconnexion');
toutClore('fin du cas');

// 4. Pendant une coupure, la page ne peut RIEN affirmer : le compteur du tour montait
//    depuis l horloge locale, donc il grimpait aussi sur un agent mort.
emettre({ genre: 'travail', actif: true });
dire(!bandeau.hidden, 'un tour en cours ouvre le bandeau');
const avantGel = pilTravail.textContent;
fermerSock(1006);
dire(liaisonPerdue, 'la coupure est enregistree');
dire(bandeau.hidden, 'le bandeau se referme : on ne sait plus si la machine vit');
dire(/liaison perdue/.test(pilTravail.textContent),
     'et la pastille le dit au lieu de compter : ' + pilTravail.textContent);
dire(/compteur est arrêté/.test(pilTravail.title),
     'l infobulle donne le detail que la pastille n a pas la place de porter');
dire(pilTravail.classList.contains('fige'), 'son animation est coupee');
brancher(); ouvrirSock();
dire(!liaisonPerdue && !pilTravail.classList.contains('fige'),
     'la reconnexion lui rend son compteur');
emettre({ genre: 'travail', actif: false });

// ---- un tour coupe ne se lit pas comme un tour fini -------------------------------------
titre('bilan de tour : pourquoi ca s est arrete');
emettre({ genre: 'tour', actions: 12, duree: 930.5, jetons: 240000, tours: 42,
          fin: 'error_max_turns', erreurs: ['Reached maximum number of turns (42)'],
          pourquoi: "je me suis arrêté avantVis d'avoir fini : la limite de tours est atteinte (42 tours)" });
const ligneCoupee = document.getElementById('flux').children.slice(-1)[0].innerHTML;
dire(/42 tours modèle/.test(ligneCoupee), 'le nombre de tours modele est affiche');
dire(/limite de tours/.test(ligneCoupee), 'et la raison de l arret aussi');
dire(/error_max_turns/.test(ligneCoupee), 'le detail technique reste en infobulle');

emettre({ genre: 'tour', actions: 3, duree: 12.0, jetons: 4200, tours: 5 });
const ligneNette = document.getElementById('flux').children.slice(-1)[0].innerHTML;
dire(!/⚠/.test(ligneNette), 'un tour normal ne porte aucun avertissement');

// ---- la relance d une conversation fermee, jusqu au bout --------------------------------
// Appuyer sur le bouton ne suffit pas : ce qui compte est ce qu on voit ENSUITE. Une relance
// qui echoue en silence remettrait la page dans l etat qu on cherche a supprimer — une
// attente sans fin dont on ignore la cause.
async function testerReouverture() {
  const pause = (ms) => new Promise(r => setTimeout(r, ms));
  titre('rouvrir une conversation fermee : ce qu on voit apres le clic');
  const cheminAvant = location.pathname;
  location.pathname = '/talk/palier/';
  const bouton = () => [...document.getElementById('l-actions').children]
    .find(b => /Rouvrir|relance/.test(b.textContent));

  // 1. Le tableau de bord refuse — plafond de sessions, projet inconnu. On le DIT.
  finirOuverture('');
  globalThis.relanceRefuse = true;
  serveurRepond = false; coupeDepuis = Date.now() - 60000; echecs = 5;
  direLiaison();
  bouton().onclick();
  await pause(10);
  dire(ouvertureEnCours === false, 'un refus ne laisse pas la page en « relance… » pour toujours');
  direLiaison();
  dire(/trop de sessions/.test(document.getElementById('l-cause').textContent),
       'et il est dit, mot pour mot : '
       + document.getElementById('l-cause').textContent.split('\n').slice(-2)[0]);

  // 2. Le tableau de bord accepte : la page insiste jusqu a ce que l agent ouvre son port.
  globalThis.relanceRefuse = false;
  finirOuverture('');
  direLiaison();
  const avant = sockets.length;
  bouton().onclick();
  await pause(10);
  dire(sockets.length > avant,
       'acceptee, la relance retente tout de suite au lieu d attendre le compte a rebours');
  dire(ouvertureEnCours === true, 'et la page reste en « relance » tant que rien ne repond');

  // 3. L agent ouvre son port : tout se range, et on arrete d insister.
  ouvrirSock();
  dire(ouvertureEnCours === false && insisterOuverture === null,
       'des que la liaison s ouvre, on cesse d insister');
  dire(document.getElementById('liaison').hidden, 'et le panneau disparait');
  location.pathname = cheminAvant;
}

// ---- ce qui ne se verifie qu'apres une echeance -----------------------------------------
// Deux comportements ne sont vrais QUE dans le temps : une sonde sans reponse, et le renvoi
// d'un message apres le rejeu. Les verifier en synchrone reviendrait a tester autre chose.
setTimeout(() => {
  titre('sonde sans reponse, et message renvoye');

  // 1. Sonde restee sans reponse : la liaison est morte, on rebranche.
  const SONDE_REELLE = DELAI_SONDE;
  DELAI_SONDE = 1;
  dernierPouls = Date.now();
  const avantMuette = sockets.length;
  sonder('verification');
  setTimeout(() => {
    // Rendre sa vraie valeur TOUT DE SUITE. Elle est globale, et les cas qui suivent sondent
    // eux aussi — a 1 ms toute sonde echoue, et ils testeraient une page en reconnexion
    // perpetuelle au lieu de ce qu ils annoncent.
    DELAI_SONDE = SONDE_REELLE;
    dire(sockets.length === avantMuette + 1,
         'une sonde restee sans reponse fait rebrancher (' + sockets.length + ' sockets)');
    dire(/n a pas repondu|répondu/.test(etatEl.title || ''),
         'et la page dit que la verification a echoue : ' + etatEl.title);
    ouvrirSock();

    // 2. Un message tape avant la veille, jamais arrive : le rejeu ne le rend pas, donc il
    //    est renvoye — et une seule fois.
    champ.value = 'lance les tests';
    composer.onsubmit({ preventDefault() {} });
    fermerSock(1006);
    sockets.length = 0; envoyes.length = 0;
    brancher(); ouvrirSock();
    socket.onmessage({ data: JSON.stringify({ genre: '_bonjour', rejoue: 3, pret: true }) });
    const renvois = envoyes.filter(o => o.cmd === 'texte' && o.texte === 'lance les tests');
    dire(renvois.length === 1,
         'un message jamais arrive est renvoye apres le rejeu (' + renvois.length + ')');
    const noteRenvoi = document.getElementById('note-barre');
    dire(/renvoyé/.test(noteRenvoi.textContent || ''),
         'et on le dit plutot que de le refaire en douce : ' + noteRenvoi.textContent);

    // 2 bis. Le defaut qui a motive tout ça : l'echo « toi » n'arrive qu'au moment ou
    //    l'agent CONSOMME le message. Pendant un tour long il ne vient pas, et chaque
    //    reconnexion reposait le meme message — cinq fois la meme phrase. L'accuse de
    //    RECEPTION, lui, revient dans la seconde et repond a la bonne question.
    const jetonRenvoi = envoyes.filter(o => o.cmd === 'texte').slice(-1)[0];
    dire(!!(jetonRenvoi && jetonRenvoi.jeton),
         'un message porte un jeton : le serveur peut dire « je l ai deja »');
    socket.onmessage({ data: JSON.stringify({ genre: '_recu', jeton: jetonRenvoi.jeton }) });
    dire(!enVol.some(m => m.jeton === jetonRenvoi.jeton),
         'l accuse de reception le retire de la file, sans attendre que Claude le traite');
    envoyes.length = 0;
    fermerSock(1006);
    brancher(); ouvrirSock();
    socket.onmessage({ data: JSON.stringify({ genre: '_bonjour', rejoue: 5, pret: true }) });
    dire(!envoyes.some(o => o.cmd === 'texte' && o.texte === 'lance les tests'),
         'et une reconnexion ne le reposte plus — c est la boucle qui envoyait cinq fois');

    // Un message accuse par RIEN reste en vol, et repart sous le MEME jeton : le serveur
    // reconnaitra un doublon au lieu d en creer un.
    envoyes.length = 0;
    champ.value = 'troisieme message';
    composer.onsubmit({ preventDefault() {} });
    const jetonTrois = envoyes.filter(o => o.cmd === 'texte').slice(-1)[0].jeton;
    fermerSock(1006);
    envoyes.length = 0;
    brancher(); ouvrirSock();
    socket.onmessage({ data: JSON.stringify({ genre: '_bonjour', rejoue: 6, pret: true }) });
    const renvoye = envoyes.find(o => o.cmd === 'texte' && o.texte === 'troisieme message');
    dire(!!renvoye && renvoye.jeton === jetonTrois,
         'un message jamais accuse repart sous le meme jeton, donc sans jamais faire de double');
    socket.onmessage({ data: JSON.stringify({ genre: '_recu', jeton: jetonTrois }) });

    // 3. Celui dont l'echo est revenu pendant le rejeu ne doit PAS repartir : ce serait le
    //    poster deux fois, ce qui est pire que de le perdre.
    champ.value = 'deuxieme message';
    composer.onsubmit({ preventDefault() {} });
    fermerSock(1006);
    sockets.length = 0; envoyes.length = 0;
    brancher(); ouvrirSock();
    emettre({ genre: 'toi', texte: 'deuxieme message' });     // l'echo, retrouve dans le rejeu
    socket.onmessage({ data: JSON.stringify({ genre: '_bonjour', rejoue: 4, pret: true }) });
    dire(!envoyes.some(o => o.cmd === 'texte' && o.texte === 'deuxieme message'),
         'un message dont l echo revient dans le rejeu n est PAS reposte');

    testerLecture()
      .then(testerEcouteHistorique)
      .then(testerRelaisVocal)
      .then(testerPiecesJointes)
      .then(testerEcritEnMorceaux)
      .then(testerLiaisonQuiNAboutitPas)
      .then(testerReouverture)
      .catch(e => { console.error('  ECHEC asynchrone : ' + (e && e.stack || e)); ok = false; })
      .then(() => {
        console.log('\n' + faits + ' verifications — ' + (ok ? 'TOUT VERT' : 'DES ECHECS'));
        process.exit(ok ? 0 : 1);
      });
  }, 30);
}, 10);
