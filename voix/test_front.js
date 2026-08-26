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
  body: { scrollHeight: 0 },
};
globalThis.window = { innerHeight: 800, scrollY: 0, scrollTo() {} };
// La page utilise requestAnimationFrame pour ne declencher une transition CSS qu'apres que
// l'element est dans le flux. Le talon l'execute TOUT DE SUITE : ce qui est asynchrone dans
// un navigateur doit rester observable dans un test, sinon on ne verifie que le premier
// etat. Ne pas le modeliser du tout faisait planter tout le script — un talon incomplet ne
// donne pas un test moins precis, il donne un test qui n'existe pas.
globalThis.requestAnimationFrame = f => { f(); return 1; };
globalThis.cancelAnimationFrame = () => {};
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

const CAS = fs.readFileSync(path.join(__dirname, "test_front_cas.js"), "utf8");


// Le HTML brut est expose au contexte : l'etat initial des elements (attribut `hidden`,
// classes) vit dans le balisage, pas dans le stub, et c'est la qu'il faut le verifier.
globalThis.__html = source;
vm.runInThisContext(STUB + page + CAS, { filename: "test_front.js" });
