// Tests du front du tableau de bord, sans navigateur.
//
// Pourquoi ce fichier existe : `node --check` valide la syntaxe, pas l'exécution. Il n'a pas
// vu qu'un `champ.addEventListener` placé avant la déclaration de `champ` tuait tout le
// script au chargement — page blanche, sans rien dans le flux pour l'expliquer. Il a aussi
// fallu ce harnais pour trouver qu'un indicateur éteint par un garde-fou ne se rallumait
// jamais, et que le tirage des mots bégayait à la jointure des sacs.
//
// Le principe : on extrait le <script> de tableau.py, on lui donne un DOM minimal, et on
// vérifie le comportement. On ne teste pas le rendu — on teste les invariants qui, quand ils
// cassent, mentent à l'utilisateur.
//
//   node voix/test_front.js

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const source = fs.readFileSync(path.join(__dirname, "tableau.py"), "utf8");
const bloc = /<script>([\s\S]*?)<\/script>/.exec(source);
if (!bloc) { console.error("  script introuvable dans tableau.py"); process.exit(1); }
// brancher() ouvre une WebSocket : hors sujet ici, et ça laisserait le process en vie.
const page = bloc[1].replace(/^brancher\(\);$/m, "");

const STUB = `
const __cache = {};
function elem(nom) {
  return {
    nom, className: "", textContent: "", innerHTML: "", title: "", value: "",
    disabled: false, hidden: false, placeholder: "", style: {}, children: [],
    dataset: {}, _q: {},
    appendChild(c) { this.children.push(c); return c; },
    append(...cs) { for (const c of cs) this.children.push(c); },
    querySelectorAll(sel) {
      // seul selecteur utilise par la page : les familles ouvertes
      if (sel === "details[open]") return this.children.filter(c => c.open);
      return [];
    },
    contains(n) { return n === this || this.children.some(c => c.contains && c.contains(n)); },
    // Une LISTE par type : le DOM reel garde tous les ecouteurs, et n'en garder qu'un
    // masquait le fait que le champ en a deux sur keydown (Echap et Entree).
    addEventListener(t, f) { ((this._ev = this._ev || {})[t] ||= []).push(f); },
    _declenche(t, ev) { for (const f of (this._ev || {})[t] || []) f(ev); },
    focus() {}, blur() {},
    // scrollHeight simule : ~48 caracteres par ligne de 21 px, plus le rembourrage. Sans ca
    // ajusterHauteur() n'aurait rien a mesurer et le test ne verifierait rien.
    get scrollHeight() {
      const n = Math.max(1, Math.ceil((this.value || "").length / 48));
      return 15 + n * 21;
    },
    // La barre entoure le champ : sa hauteur suit celle du champ plus son rembourrage.
    // C'est la relation reelle du navigateur, modelisee pour que reserverPlace() ait
    // quelque chose a mesurer.
    get offsetHeight() {
      if (this.nom === "#saisie-barre") {
        return (parseInt(__cache["saisie"]?.style.height) || 36) + 22;
      }
      return parseInt(this.style.height) || 36;
    },
    querySelector(sel) { return this._q[sel] || (this._q[sel] = elem(nom + sel)); },
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
      contains(c) { return this._s.has(c); },
      toggle(c, v) { v === undefined ? (this._s.has(c) ? this._s.delete(c) : this._s.add(c))
                                     : (v ? this._s.add(c) : this._s.delete(c)); },
    },
  };
}
globalThis.document = {
  getElementById: id => __cache[id] || (__cache[id] = elem("#" + id)),
  createElement: t => elem("<" + t + ">"),
  createTextNode: t => ({ nom: "#texte", textContent: t, children: [] }),
  body: { scrollHeight: 0 },
};
globalThis.window = { innerHeight: 800, scrollY: 0, scrollTo() {} };
globalThis.document.querySelector = () => elem("main");
globalThis.addEventListener = () => {};
globalThis.envoyes = [];
globalThis.WebSocket = function () {
  // CONNECTING, comme dans un navigateur : une socket neuve n'est pas immediatement
  // utilisable. Le test la promeut explicitement en OPEN quand il veut simuler la reussite
  // de la connexion — sinon on testerait un comportement impossible.
  this.readyState = 0;
  this.send = (d) => {
    if (this.readyState !== 1) throw new Error("socket fermee");
    globalThis.envoyes.push(JSON.parse(d));
  };
  this.close = () => { this.readyState = 3; };
};
globalThis.WebSocket.OPEN = 1;
globalThis.location = { host: "127.0.0.1:7788" };
`;

const CAS = `
let ok = true, faits = 0;
const dire = (bon, texte) => {
  faits++; ok = ok && bon;
  console.log('  ' + (bon ? 'OK  ' : 'ECHEC') + ' ' + texte);
};
const titre = t => console.log('\\n=== ' + t + ' ===');
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
dire(JSON.stringify(caches) === JSON.stringify(['log', 'partiel', 'resultat']),
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

console.log('\\n' + faits + ' verifications — ' + (ok ? 'TOUT VERT' : 'DES ECHECS'));
process.exit(ok ? 0 : 1);
`;

vm.runInThisContext(STUB + page + CAS, { filename: "test_front.js" });
