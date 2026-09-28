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
// Le marqueur de version est substitue PAR LE SERVEUR au moment de servir la page. Le test
// doit donc le substituer aussi, sinon il verifie une page que personne ne recoit jamais.
const page = bloc[1].replace(/^brancher\(\);$/m, "").replace(/__VERSION_PAGE__/g, "1000");

const STUB = `
const __cache = {};
function elem(nom) {
  const noeud = {
    nom, className: "", _texte: "", innerHTML: "", title: "", value: "",
    disabled: false, hidden: false, placeholder: "", style: {}, children: [],
    dataset: {}, _q: {},
    appendChild(c) { this.children.push(c); return c; },
    append(...cs) { for (const c of cs) this.children.push(c); },
    // Les panneaux de la page se dessinent en posant innerHTML, puis rebranchent leurs
    // gestionnaires avec querySelectorAll("[data-...]"). Le talon rendait [] pour tout
    // selecteur d attribut : les clics n etaient donc JAMAIS branches, et aucun test ne
    // pouvait verifier qu un bouton fait ce qu il annonce — ni ici, ni sur le choix du
    // moteur. On modelise donc la lecture des attributs dans le HTML pose.
    // Les panneaux de la page se dessinent en posant innerHTML, puis rebranchent leurs
    // gestionnaires avec querySelectorAll("[data-...]"). Le talon rendait [] pour tout
    // selecteur d attribut : les clics n etaient donc JAMAIS branches, et aucun test ne
    // pouvait verifier qu un bouton fait ce qu il annonce — ni ici, ni sur le choix du
    // moteur. On lit donc les attributs dans le HTML pose.
    //
    // Sans une seule barre oblique inverse, deliberement : ce fichier est evalue depuis un
    // litteral de gabarit, qui mange les echappements une seconde fois. Une expression
    // reguliere ecrite ici perd ses \\w et ses \\[ en silence — c est deja arrive.
    querySelectorAll(sel) {
      if (sel === "details[open]") return this.children.filter(c => c.open);
      if (sel.startsWith("[") && sel.endsWith("]") && this.innerHTML) {
        // Les deux formes que la page utilise reellement : [data-sid] pour tout rebrancher,
        // et [data-sid="x"] pour en viser un. Elles doivent rendre les MEMES objets, sinon
        // un gestionnaire pose par la premiere serait introuvable par la seconde.
        const dedans = sel.slice(1, -1);
        const eq = dedans.indexOf("=");
        if (eq < 0) return this._balises(dedans);
        const attr = dedans.slice(0, eq);
        const vise = dedans.slice(eq + 1).replace(/["']/g, "");
        return this._balises(attr).filter(b => b.getAttribute(attr) === vise);
      }
      return [];
    },
    _balises(attr) {
      // Cache lie au HTML courant : sans lui, deux appels rendraient deux objets differents
      // et un onclick pose sur le premier ne serait pas trouve sur le second.
      if (this._cacheHtml !== this.innerHTML) {
        this._cacheHtml = this.innerHTML;
        this._cacheBalises = {};
      }
      if (this._cacheBalises[attr]) return this._cacheBalises[attr];
      const sortie = [];
      for (const morceau of this.innerHTML.split("<").slice(1)) {
        const bout = morceau.split(">")[0];
        const cherche = attr + '="';
        const i = bout.indexOf(cherche);
        if (i < 0) continue;
        const reste = bout.slice(i + cherche.length);
        const valeur = reste.slice(0, reste.indexOf('"'));
        const balise = elem("<" + bout.split(" ")[0] + " " + attr + "=" + valeur + ">");
        balise.disabled = bout.includes("disabled");
        balise.getAttribute = nom => {
          const c = nom + '="';
          const j = bout.indexOf(c);
          if (j < 0) return null;
          const r = bout.slice(j + c.length);
          return r.slice(0, r.indexOf('"'));
        };
        sortie.push(balise);
      }
      return (this._cacheBalises[attr] = sortie);
    },
    contains(n) { return n === this || this.children.some(c => c.contains && c.contains(n)); },
    // Les attributs poses par le code, par opposition a ceux lus dans le HTML plus haut.
    // L attribut aria-pressed porte l etat d un bouton a bascule : sans stockage, le test
    // ne pouvait pas distinguer « allume » de « eteint ».
    setAttribute(n, v) { (this._attrs ||= {})[n] = String(v); },
    getAttribute(n) { return (this._attrs || {})[n] ?? null; },
    removeAttribute(n) { if (this._attrs) delete this._attrs[n]; },
    // Une LISTE par type : le DOM reel garde tous les ecouteurs, et n'en garder qu'un
    // masquait le fait que le champ en a deux sur keydown (Echap et Entree).
    addEventListener(t, f) { ((this._ev = this._ev || {})[t] ||= []).push(f); },
    _declenche(t, ev) { for (const f of (this._ev || {})[t] || []) f(ev); },
    focus() {}, blur() {},
    // Le curseur du champ. Les fleches ne prennent la main que si le curseur ne peut PAS
    // bouger — premiere ou derniere ligne — donc sans ces trois proprietes le test ne
    // verifiait rien de ce comportement, qui est justement le plus delicat.
    // La geometrie : sans navigateur il n'y a pas de mise en page, mais le CODE l'interroge —
    // le placement des panneaux mesure leur position pour ne pas sortir de l'ecran. Rendre des
    // zeros est honnete : le talon ne sait rien de la geometrie, et c'est test_rendu, dans un
    // vrai Chrome, qui la verifie. Ne pas la modeliser du tout faisait planter tout le script.
    getBoundingClientRect() {
      return { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0 };
    },
    selectionStart: 0, selectionEnd: 0,
    setSelectionRange(d, f) { this.selectionStart = d; this.selectionEnd = f; },
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
    querySelector(sel) {
      // Une vraie correspondance d abord : inventer un element pour un selecteur qui DEVRAIT
      // matcher rendait un objet sans gestionnaire, et le test echouait sur le talon plutot
      // que sur la page.
      const trouves = this.querySelectorAll(sel);
      if (trouves.length) return trouves[0];
      return this._q[sel] || (this._q[sel] = elem(nom + sel));
    },
    // Dans un vrai DOM, LIRE textContent concatene le texte des descendants et l ECRIRE
    // remplace tous les enfants. Le talon le gardait comme une chaine ordinaire : vider un
    // conteneur ne vidait rien, et chercher un mot dans un flux ne trouvait jamais ce que
    // les lignes portaient. Deux tests passaient donc pour de mauvaises raisons.
    get textContent() {
      // Poser innerHTML cree de vrais noeuds de texte dans un navigateur : leur contenu est
      // donc lisible par textContent. Sans ce depouillement, chercher un mot dans le flux ne
      // trouvait jamais rien — toutes les lignes de la page sont construites en innerHTML.
      const depuisHtml = this.innerHTML
        ? this.innerHTML.split("<").map(m => m.slice(m.indexOf(">") + 1)).join("")
        : "";
      return this._texte + depuisHtml
        + this.children.map(c => c.textContent || "").join("");
    },
    set textContent(v) {
      this._texte = v == null ? "" : String(v);
      if (this._texte === "") this.children.length = 0;
    },
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
      contains(c) { return this._s.has(c); },
      toggle(c, v) { v === undefined ? (this._s.has(c) ? this._s.delete(c) : this._s.add(c))
                                     : (v ? this._s.add(c) : this._s.delete(c)); },
    },
  };
  return noeud;
}
// Les ids reellement presents dans le balisage. Un getElementById qui inventait un element
// pour n'importe quel id masquait les chemins de creation paresseuse : le code croyait avoir
// deja son panneau et ne l'initialisait jamais.
globalThis.__ids = new Set((globalThis.__html || "").match(/id="[^"]+"/g)
  ? (globalThis.__html.match(/id="[^"]+"/g) || []).map(s => s.slice(4, -1)) : []);
globalThis.document = {
  getElementById: id => __cache[id]
    || (__ids.has(id) ? (__cache[id] = elem("#" + id)) : null),
  createElement: t => elem("<" + t + ">"),
  createTextNode: t => ({ nom: "#texte", textContent: t, children: [] }),
  // Un vrai element : la boite de diagnostic s y accroche, et le test doit pouvoir
  // constater qu elle n y est PAS la plupart du temps — c est tout l enjeu.
  body: elem("<body>"),
};
// Ce qu on cree et qu on accroche au corps devient trouvable par son id, et redevient
// introuvable quand on le retire. Sans ça, un element cree dynamiquement etait invisible au
// test : on ne pouvait affirmer ni sa presence ni son absence.
(() => {
  const corps = globalThis.document.body;
  const posee = corps.appendChild.bind(corps);
  corps.appendChild = (n) => {
    if (n && n.id) { __ids.add(n.id); __cache[n.id] = n; }
    const r = posee(n);
    if (n) n.remove = () => { __ids.delete(n.id); delete __cache[n.id];
                              corps.children = corps.children.filter(c => c !== n); };
    return r;
  };
  corps.prepend = corps.appendChild;
})();
globalThis.window = { innerHeight: 800, scrollY: 0, scrollTo() {} };
// La page utilise requestAnimationFrame pour ne declencher une transition CSS qu'apres que
// l'element est dans le flux. Le talon l'execute TOUT DE SUITE : ce qui est asynchrone dans
// un navigateur doit rester observable dans un test, sinon on ne verifie que le premier
// etat. Ne pas le modeliser du tout faisait planter tout le script — un talon incomplet ne
// donne pas un test moins precis, il donne un test qui n'existe pas.
// Le stockage local, modelise. La page y garde l'historique des messages : sans lui, le
// try/catch avalait tout et l'historique n'etait jamais teste — un test vert sur une
// fonctionnalite absente. On modelise aussi le cas du stockage REFUSE (navigation privee),
// parce que la page doit continuer de marcher sans.
globalThis.__stockage = {};
globalThis.localStorage = {
  getItem(c) { return Object.prototype.hasOwnProperty.call(__stockage, c) ? __stockage[c] : null; },
  setItem(c, v) { __stockage[c] = String(v); },
  removeItem(c) { delete __stockage[c]; },
  clear() { for (const c of Object.keys(__stockage)) delete __stockage[c]; },
};
globalThis.requestAnimationFrame = f => { f(); return 1; };
globalThis.cancelAnimationFrame = () => {};
globalThis.document.querySelector = () => elem("main");
// Les ecouteurs de fenetre etaient JETES (() => {}), donc tout ce que la page fait au retour
// de veille, au retour du reseau ou a la restauration depuis le cache etait hors de portee
// des tests — precisement la partie qu'on ne peut pas verifier a la main sur un telephone.
// On les garde, et on les declenche.
globalThis.__ecouteurs = {};
globalThis.addEventListener = (nom, f) => { (globalThis.__ecouteurs[nom] ||= []).push(f); };
globalThis.declencher = (nom, ev) => {
  for (const f of globalThis.__ecouteurs[nom] || []) f(ev || {});
};
// Le micro, tel que le navigateur le donne. Le compteur retient les fois ou l autorisation
// a REELLEMENT ete reclamee : c est tout l enjeu, la question n etait jamais posee.
globalThis.microDemandes = 0;
globalThis.microAccorde = true;
globalThis.navigator = {
  onLine: true,
  mediaDevices: {
    getUserMedia: () => {
      globalThis.microDemandes++;
      if (!globalThis.microAccorde) {
        const e = new Error("refus"); e.name = "NotAllowedError";
        return Promise.reject(e);
      }
      return Promise.resolve({ getTracks: () => [{ stop() {} }] });
    },
  },
};
globalThis.document.visibilityState = "visible";
globalThis.envoyes = [];
// Chaque socket ouverte est retenue : un test de reconnexion doit pouvoir verifier
// COMBIEN ont ete ouvertes, pas seulement que la derniere existe.
globalThis.sockets = [];

// La synthese du navigateur. Stub volontairement bete : il ENREGISTRE ce qu'on lui demande
// de dire, parce que c'est la seule chose qu'on veuille verifier — qu'une phrase coupee en
// deux par le flux ne parte pas en deux morceaux incomprehensibles.
// Enregistrer ICI, transcrire LA-BAS. Les talons retiennent ce qui compte : le micro a-t-il
// ete reclame, l enregistreur a-t-il demarre et rendu un fichier, ce fichier est-il parti
// vers /audio — et ce que la page fait de la reponse.
globalThis.isSecureContext = true;
globalThis.enregistreurs = [];
globalThis.MediaRecorder = function (flux, opts) {
  const self = this;
  this.mimeType = (opts && opts.mimeType) || "audio/webm";
  this.etat = "inactive";
  this.start = () => { self.etat = "recording"; };
  this.stop = () => {
    self.etat = "inactive";
    if (self.ondataavailable) self.ondataavailable({ data: { size: 1234, type: self.mimeType } });
    if (self.onstop) self.onstop();
  };
  globalThis.enregistreurs.push(this);
};
globalThis.MediaRecorder.isTypeSupported = (t) => /webm/.test(t);
globalThis.Blob = function (parts, opts) {
  this.size = (parts || []).reduce((n, p) => n + ((p && p.size) || 0), 0);
  this.type = (opts && opts.type) || "";
};
globalThis.FormData = function () { this.champs = []; this.append = (n, v, f) => this.champs.push({ n, v, f }); };
// fetch : la page envoie, le test decide de la reponse. La liste requetes garde chaque appel.
globalThis.requetes = [];
globalThis.reponseAudio = { ok: true, status: 200, corps: { texte: "ok" } };
// La synthese Azure, servie par /parler. Le drapeau azureMarche a false simule l absence de
// cle ou un service injoignable : la page doit se rabattre sur la voix du navigateur.
globalThis.azureMarche = true;
globalThis.fetch = (url, opts) => {
  globalThis.requetes.push({ url, opts });
  // endsWith et pas une expression reguliere : ce stub vit dans un template literal, ou
  // chaque antislash est mange une fois de plus — /\/parler$/ y devenait un commentaire.
  if (String(url).endsWith("/parler")) {
    if (!globalThis.azureMarche) {
      return Promise.resolve({ ok: false, status: 503,
                               json: () => Promise.resolve({ erreur: "pas de clé Azure" }) });
    }
    return Promise.resolve({ ok: true, status: 200,
                             blob: () => Promise.resolve({ size: 9792, type: "audio/mpeg" }) });
  }
  const r = globalThis.reponseAudio;
  return Promise.resolve({ ok: r.ok, status: r.status, json: () => Promise.resolve(r.corps) });
};
globalThis.URL = { createObjectURL: () => "blob:faux", revokeObjectURL: () => {} };
// L element audio. Il retient ce qu on lui donne a jouer — c est la seule chose a verifier :
// que le son vient bien d Azure et pas de la synthese du navigateur.
globalThis.joues = [];
globalThis.Audio = function (src) {
  const self = this;
  this.src = src;
  this.ended = false;
  this.pause = () => {};
  this.play = () => {
    globalThis.joues.push(src);
    // Joue puis se termine, comme un vrai element une fois le MP3 fini.
    setTimeout(() => { self.ended = true; if (self.onended) self.onended(); }, 0);
    return Promise.resolve();
  };
};
// localStorage : ce qui survit a la page. Un simple dictionnaire suffit.
globalThis.__stock = {};
globalThis.localStorage = {
  getItem: (k) => (k in globalThis.__stock ? globalThis.__stock[k] : null),
  setItem: (k, v) => { globalThis.__stock[k] = String(v); },
  removeItem: (k) => { delete globalThis.__stock[k]; },
  clear: () => { globalThis.__stock = {}; },
  key: (i) => Object.keys(globalThis.__stock)[i] ?? null,
  get length() { return Object.keys(globalThis.__stock).length; },
};

globalThis.dit = [];
globalThis.SpeechSynthesisUtterance = function (t) { this.text = t; };
globalThis.speechSynthesis = {
  getVoices: () => [{ lang: "fr-FR", name: "Amelie", localService: true },
                    { lang: "en-US", name: "Samantha", localService: true }],
  addEventListener: () => {},
  speak(u) { if ((u.text || "").trim()) globalThis.dit.push(u.text.trim()); },
  cancel() { globalThis.dit.push("[coupe]"); },
};
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
  this.addEventListener = () => {};
  globalThis.sockets.push(this);
};
globalThis.WebSocket.OPEN = 1;
// hostname, et pas seulement host : le code s'en sert pour savoir si l'on est ASSIS devant
// la machine qui parle. Les cas forcent « socle » quand ils veulent l'autre situation.
globalThis.location = { host: "socle:7788", hostname: "socle",
                        protocol: "https:", pathname: "/" };
`;

const CAS = fs.readFileSync(path.join(__dirname, "test_front_cas.js"), "utf8");


// Le HTML brut est expose au contexte : l'etat initial des elements (attribut `hidden`,
// classes) vit dans le balisage, pas dans le stub, et c'est la qu'il faut le verifier.
globalThis.__html = source;
vm.runInThisContext(STUB + page + CAS, { filename: "test_front.js" });
