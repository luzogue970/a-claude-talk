// Verifie la MISE EN PAGE, dans un vrai navigateur.
//
// Pourquoi ce fichier existe : test_front.js stub le DOM, donc il ne mesure aucune
// geometrie. Il a laisse passer un bug ou `.ev.passe .pip{display:none}` retirait
// l'indicateur du flux de la grille — le corps du message glissait dans la colonne de 14 px
// prevue pour lui, et 563 caracteres dans 14 px donnaient UN CARACTERE PAR LIGNE, sur des
// lignes de 7 454 px de haut. L'historique rejoue etait illisible, et aucun test ne le
// voyait.
//
// On rend donc la page dans Chrome, on injecte un script qui releve les geometries reelles,
// et on affirme dessus.
//
//   node voix/test_rendu.js
//
// Sans Chrome installe, le test s'abstient au lieu d'echouer : il ne doit pas bloquer une
// machine qui n'en a pas.

const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const CHROMES = ["google-chrome", "chromium", "chromium-browser", "google-chrome-stable"];
function trouverChrome() {
  for (const c of CHROMES) {
    try {
      execFileSync("which", [c], { stdio: "pipe" });
      return c;
    } catch (_) { /* suivant */ }
  }
  return null;
}

const chrome = trouverChrome();
if (!chrome) {
  console.log("  Chrome introuvable — test de mise en page ignore");
  process.exit(0);
}

// --- la page, avec un historique synthetique et une sonde ------------------------------
const source = fs.readFileSync(path.join(__dirname, "tableau.py"), "utf8");
const page = /<!doctype html>[\s\S]*?<\/html>/i.exec(source)[0];

const LONG = "Oui, c'est faisable, mais le vrai obstacle n'est pas le langage : c'est "
  + "l'acces aux messages. L'API officielle exige de convertir le compte et ne permet que "
  + "de repondre dans les vingt-quatre heures, donc elle ne remplace pas une messagerie "
  + "personnelle ; le pont non officiel donne tout mais viole les conditions d'utilisation.";

const EVENEMENTS = [
  { n: 1, genre: "toi",    texte: LONG, passe: true,  h: "14:30:00", t: 1 },
  { n: 2, genre: "texte",  texte: LONG, passe: true,  h: "14:30:01", t: 2 },
  { n: 3, genre: "tour",   actions: 5, texte: "Bash ls, Bash cat", passe: true, h: "14:30:02", t: 3 },
  { n: 4, genre: "toi",    texte: LONG, h: "14:30:03", t: 4 },
  { n: 5, genre: "outil",  nom: "Read", cible: "config.py", id: "x1", h: "14:30:04", t: 5 },
  { n: 6, genre: "voix",   texte: LONG, h: "14:30:05", t: 6 },
];

const SONDE = `
<script>
setTimeout(() => {
  const cs = el => getComputedStyle(el);
  const releve = { lignes: [] };
  const flux = document.querySelector("main");
  releve.page = { hauteur: document.body.scrollHeight, largeur: document.body.clientWidth };
  releve.entete = document.querySelector("header").offsetHeight;
  for (const l of flux.children) {
    if (!l.dataset.g) continue;
    const corps = l.querySelector(".corps");
    releve.lignes.push({
      genre: l.dataset.g,
      passe: l.className.split(" ").includes("passe"),
      hauteur: l.offsetHeight,
      colonnes: cs(l).gridTemplateColumns,
      corpsLargeur: corps ? corps.offsetWidth : -1,
      corpsHauteur: corps ? corps.offsetHeight : -1,
      opacite: parseFloat(cs(l).opacity),
    });
  }
  const champ = document.getElementById("saisie");
  releve.champ = { hauteur: champ.offsetHeight, debordement: cs(champ).overflowY };
  const pre = document.createElement("pre");
  pre.id = "releve";
  pre.textContent = JSON.stringify(releve);
  document.body.appendChild(pre);
}, 500);
</script>`;

// L'appel de niveau superieur, pas ceux imbriques dans reconnecter() ou le setTimeout :
// remplacer le premier venu injectait le code la ou il ne s'executait jamais.
const html = page.replace(/^brancher\(\);$/m,
  `const __e = ${JSON.stringify(EVENEMENTS)};\n__e.filter(e => !dejaVu(e)).forEach(ajouter);\nwindow.scrollTo(0, 0);`) + SONDE;

if (!html.includes("__e.filter")) {
  console.error("  ECHEC l'injection n'a pas trouve l'appel de niveau superieur a brancher()");
  process.exit(1);
}

const dossier = fs.mkdtempSync(path.join(os.tmpdir(), "rendu-"));
const fichier = path.join(dossier, "page.html");
fs.writeFileSync(fichier, html);

let dom;
try {
  dom = execFileSync(chrome, [
    "--headless", "--disable-gpu", "--no-sandbox", "--virtual-time-budget=3000",
    "--window-size=1280,900", "--dump-dom", "file://" + fichier,
  ], { encoding: "utf8", stdio: ["pipe", "pipe", "pipe"], maxBuffer: 40 * 1024 * 1024 });
} catch (e) {
  console.log("  Chrome n'a pas rendu la page — test ignore (" + (e.message || "").slice(0, 60) + ")");
  process.exit(0);
}

const brut = /<pre id="releve">([\s\S]*?)<\/pre>/.exec(dom);
if (!brut) {
  console.error("  ECHEC la sonde n'a rien releve : le script de la page a casse au chargement");
  process.exit(1);
}
const r = JSON.parse(brut[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                            .replace(/&lt;/g, "<").replace(/&gt;/g, ">"));

// --- les affirmations -------------------------------------------------------------------
let ok = true;
const dire = (bon, texte) => { ok = ok && bon; console.log(`  ${bon ? "OK  " : "ECHEC"} ${texte}`); };

console.log("=== mise en page, mesuree dans Chrome ===");
dire(r.entete > 30, `l'en-tête est rendu (${r.entete} px)`);
dire(r.lignes.length === EVENEMENTS.length,
     `${r.lignes.length} lignes rendues sur ${EVENEMENTS.length}`);

const avecCorps = r.lignes.filter(l => l.corpsLargeur >= 0);
const pire = avecCorps.reduce((a, b) => (b.corpsLargeur < a.corpsLargeur ? b : a));
dire(pire.corpsLargeur > 400,
     `la colonne de texte est large partout — la plus étroite : ${pire.corpsLargeur} px `
     + `(${pire.genre}${pire.passe ? ", passée" : ""})`);

const haute = r.lignes.reduce((a, b) => (b.hauteur > a.hauteur ? b : a));
dire(haute.hauteur < 300,
     `aucune ligne anormalement haute — la plus haute : ${haute.hauteur} px (${haute.genre})`);

// quatre colonnes, et la derniere prend la place restante
const cols = r.lignes[0].colonnes.split(/\s+/);
dire(cols.length === 4, `la grille garde ses quatre colonnes : ${r.lignes[0].colonnes}`);
dire(parseFloat(cols[3]) > 400, `la quatrième colonne est celle du texte (${cols[3]})`);

// une ligne passee doit rester lisible : 0,7 donne 5,4:1 sur le texte de Claude, 0,55 : 3,8:1
const passees = r.lignes.filter(l => l.passe);
dire(passees.length > 0 && passees.every(l => l.opacite >= 0.65),
     `les lignes rejouées restent lisibles (opacité ${passees[0]?.opacite})`);

// le champ ne montre pas de barre de defilement quand il n'en a pas besoin
dire(r.champ.debordement === "hidden",
     `le champ vide n'affiche aucune barre de défilement (overflow-y: ${r.champ.debordement})`);

dire(r.page.hauteur < 6000, `la page garde une hauteur saine (${r.page.hauteur} px)`);

fs.rmSync(dossier, { recursive: true, force: true });
console.log(`\n${r.lignes.length} lignes mesurées — ${ok ? "TOUT VERT" : "DES ECHECS"}`);
process.exit(ok ? 0 : 1);
