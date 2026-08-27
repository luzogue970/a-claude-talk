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

// Les selecteurs DOIVENT etre remplis : mesurer la largeur d'un select vide passerait
// l'assertion sans rien verifier, et c'est ce qui est arrive a la premiere version du test.
const LISTES = [
  { n: 1, genre: "modeles", actuel: "opus", liste: [
      { cle: "opus", libelle: "Opus 5 — le plus capable" },
      { cle: "sonnet", libelle: "Sonnet 5 — équilibré" }] },
  { n: 2, genre: "efforts", actuel: "xhigh", liste: [
      { cle: "xhigh", libelle: "très élevé — défaut" },
      { cle: "max", libelle: "maximum — le plus fouillé, le plus lent" }] },
  { n: 3, genre: "delais", actuel: 5, plafond: 12.5,
    paliers: [{ s: 3 }, { s: 5 }, { s: 10 }] },
  // Les PANNEAUX doivent etre remplis pour la meme raison que les selecteurs : mesurer la
  // largeur d'un panneau vide passe l'assertion sans rien verifier. Le debordement de 124 px
  // venait justement du panneau plein, avec ses neuf moteurs et leurs notes.
  { n: 4, genre: "moteurs_stt", actif: "assemblyai", direct: true,
    chaine: ["assemblyai", "deepgram", "speechmatics", "gladia", "local"],
    liste: [
      { cle: "assemblyai", libelle: "AssemblyAI", dispo: true, rang: 0, streaming: true,
        gratuit: "50 $ de credits a l'inscription (~300 h)",
        note: "MESURE le meilleur ici : 3,0 % d'erreur en 2,9 s, le plus rapide en ligne" },
      { cle: "deepgram", libelle: "Deepgram", dispo: true, rang: 1, streaming: true,
        gratuit: "200 $ de credits a l'inscription",
        note: "nova-3 ; mesure a 3,0 % d'erreur, aussi bon qu'AssemblyAI mais deux fois plus lent" },
      { cle: "speechmatics", libelle: "Speechmatics", dispo: true, rang: 2, streaming: true,
        gratuit: "8 h/mois, renouvele, sans carte",
        note: "bon sur les bancs publics (6,4 %), mais ici il tronque ses phrases ou reste muet" },
      { cle: "google", libelle: "Google Cloud", dispo: false, rang: null, streaming: true,
        gratuit: "60 min/mois a vie", note: "le palier a vie est petit" },
      { cle: "local", libelle: "local (faster-whisper)", dispo: true, rang: 4, streaming: false,
        gratuit: "illimite, hors ligne",
        note: "4 a 7 s par phrase, et AUCUN texte en direct ; l'audio ne quitte pas la machine" },
    ] },
  { n: 5, genre: "consommation", moteurs: [
      { cle: "assemblyai", libelle: "AssemblyAI", dispo: true, consomme_s: 1200,
        palier_s: 1188000, reste_s: 1186800, part: 0.001, renouvelable: false,
        gratuit: "50 $ de credits", epuise: false },
      { cle: "speechmatics", libelle: "Speechmatics", dispo: true, consomme_s: 28800,
        palier_s: 28800, reste_s: 0, part: 1, renouvelable: true, epuise: true,
        gratuit: "8 h/mois", motif: "Quota exceeded for this month" },
    ] },
  { n: 6, genre: "conversations", dossier: "/home/x/dev/insnap", courante: "sid-a", liste: [
      { session_id: "sid-a", projet: "insnap", tours: 59, reprises: 7, ici: true,
        maj: new Date(Date.now() - 300000).toISOString(), etat: "en cours",
        apercu: "l inscription whatsapp sur l appli semble ne pas marcher et l ux ui a ce niveau non plus" },
      { session_id: "sid-b", projet: "echec", tours: 38, reprises: 5, sous: "claudesque/echec",
        maj: new Date(Date.now() - 10800000).toISOString(), etat: "fermée",
        apercu: "tkt rien a faire pour l instant, on verra ça demain matin tranquillement" },
    ] },
];

const EVENEMENTS = [
  { n: 11, genre: "toi",    texte: LONG, passe: true,  h: "14:30:00", t: 1 },
  { n: 12, genre: "texte",  texte: LONG, passe: true,  h: "14:30:01", t: 2 },
  { n: 13, genre: "tour",   actions: 5, texte: "Bash ls, Bash cat", passe: true, h: "14:30:02", t: 3 },
  { n: 14, genre: "toi",    texte: LONG, h: "14:30:03", t: 4 },
  { n: 15, genre: "outil",  nom: "Read", cible: "config.py", id: "x1", h: "14:30:04", t: 5 },
  { n: 16, genre: "voix",   texte: LONG, h: "14:30:05", t: 6 },
];

const SONDE = `
<script>
setTimeout(async () => {
  const cs = el => getComputedStyle(el);
  // Les hauteurs des barres ont une transition CSS : mesurer juste apres avoir change la
  // classe donne un etat A MI-CHEMIN, et on ne sait plus si « coupe » s'aplatit vraiment ou
  // si c'est l'animation qu'on a photographiee. On laisse le style se poser.
  const poser = () => new Promise(r => setTimeout(r, 250));
  const releve = { lignes: [] };
  const flux = document.querySelector("main");
  releve.page = { hauteur: document.body.scrollHeight, largeur: document.body.clientWidth };

  // Le debordement HORIZONTAL, panneaux ouverts. C'est la seule facon de l'attraper : ferme,
  // un panneau ne mesure rien ; ouvert, un panneau trop large elargit la page entiere et
  // cache une partie du contenu derriere un defilement lateral qu'on ne cherche pas.
  const ouverts = ["choix-moteur", "choix-conv"];
  releve.panneaux = {};
  // Remplir avant de mesurer : c'est le panneau PLEIN qui debordait.
  try { dessinerChoix(); } catch (e) {}
  try { dessinerConvs(); } catch (e) {}
  for (const id of ouverts) {
    const el = document.getElementById(id);
    if (!el) continue;
    el.hidden = false;
    const r = el.getBoundingClientRect();
    releve.panneaux[id] = { l: Math.round(r.left), d: Math.round(r.right),
                            w: Math.round(r.width) };
  }
  // Les barres du micro : sont-elles VISIBLES ? « le rond est juste vide » — un indicateur
  // qui ne se voit pas ne vaut rien, et une largeur en fraction de pixel peut disparaitre
  // au rendu. On mesure la boite reelle de chaque barre, dans les trois etats.
  const mb = document.getElementById("micro-bas");
  releve.ondes = {};
  if (mb) {
    // Transitions et animations neutralisees le temps de la mesure. Sans ça on photographie
    // un etat A MI-CHEMIN et on ne mesure pas la regle mais l'instant : la version precedente
    // rendait « 4px 11px 14px 11px 6px » sur l'etat coupe, alors que la meme feuille de style
    // isolee donne bien « 4px » partout. Une mesure qui depend du moment ou elle tombe ne dit
    // rien de ce qu'on voulait verifier.
    const gel = document.createElement("style");
    gel.textContent = "*{transition:none !important;animation:none !important}";
    document.head.appendChild(gel);
    for (const etat of ["micro-rond ouvert", "micro-rond ouvert parle", "micro-rond coupe"]) {
      mb.className = etat;
      const barres = [...mb.querySelectorAll(".ondes i")].map(i => {
        const r = i.getBoundingClientRect();
        const c = cs(i);
        return { w: +r.width.toFixed(2), h: +r.height.toFixed(2),
                 // La hauteur CALCULEE par le CSS, distincte de la boite rendue : elle dit ce
                 // que la regle a resolu, sans dependre d'une transition en cours ni d'une
                 // animation que le rendu sans fenetre n'avance pas.
                 hCalc: c.height, anim: c.animationName,
                 fond: c.backgroundColor, aff: c.display, opac: +c.opacity };
      });
      releve.ondes[etat] = { n: barres.length, barres };
    }
    // L'animation se lit sur une feuille NON gelee : c'est la seule chose que le gel cache.
    gel.remove();
    mb.className = "micro-rond ouvert parle";
    releve.animeQuandParle = [...mb.querySelectorAll(".ondes i")]
      .map(i => cs(i).animationName).join(",");
    mb.className = "micro-rond ouvert";
  }

  // La pile au-dessus de la barre : ses trois elements ne doivent JAMAIS se recouvrir. Ils
  // etaient tous ancres au meme « bottom:100% » et se dessinaient l'un sur l'autre — visible
  // seulement avec des textes assez longs, donc au pire moment.
  {
    const pile = document.getElementById("pile-barre");
    const cog = document.getElementById("cogitation");
    const att = document.getElementById("en-attente");
    const nb = document.getElementById("note-barre");
    if (pile && cog && att && nb) {
      cog.hidden = att.hidden = nb.hidden = false;
      cog.querySelector("#mot").textContent = "solutionnage";
      att.innerHTML = '<span class="quoi">2 messages a la fin de la lecture</span>'
        + '<span class="dit">' + "un texte deliberement long ".repeat(6) + '</span>';
      nb.textContent = "message 2 sur 7 · ↑↓ pour naviguer, Echap pour revenir";
      const r = e => { const b = e.getBoundingClientRect();
        return { t: Math.round(b.top), b: Math.round(b.bottom),
                 l: Math.round(b.left), d: Math.round(b.right) }; };
      releve.pile = { cog: r(cog), att: r(att), note: r(nb), boite: r(pile),
                      barre: r(document.getElementById("saisie-barre")) };
      cog.hidden = att.hidden = nb.hidden = true;
    }
  }

  releve.deborde = {
    page: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    corps: document.body.scrollWidth - document.body.clientWidth,
    vue: innerWidth,
  };
  for (const id of ouverts) {
    const el = document.getElementById(id);
    if (el) el.hidden = true;
  }
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
  const bcc = champ.getBoundingClientRect();
  releve.champ = {
    hauteur: champ.offsetHeight, debordement: cs(champ).overflowY,
    // La respiration sous le champ : colle au bord, la barre donne l'impression d'une
    // fenetre coupee, et c'est la zone la moins accessible d'un portable.
    souffleBas: Math.round(innerHeight - bcc.bottom),
  };
  const b = s => { const e = document.querySelector(s); if (!e) return null;
    const r = e.getBoundingClientRect();
    return { t: Math.round(r.top), b: Math.round(r.bottom), l: Math.round(r.left),
             w: Math.round(r.width), h: Math.round(r.height) }; };
  releve.entete = document.querySelector("header").offsetHeight;
  releve.boites = { barre: b("#saisie-barre"), cogit: b("#cogitation"),
                    modele: b("#modele"), effort: b("#effort"), direct: b(".zone-direct"),
                    microBas: b("#micro-bas"), champBoite: b("#saisie"),
                    convs: b("#convs") };
  releve.vue = { w: innerWidth, h: innerHeight };
  const pre = document.createElement("pre");
  pre.id = "releve";
  pre.textContent = JSON.stringify(releve);
  document.body.appendChild(pre);
}, 500);
</script>`;

// L'appel de niveau superieur, pas ceux imbriques dans reconnecter() ou le setTimeout :
// remplacer le premier venu injectait le code la ou il ne s'executait jamais.
const html = page.replace(/^brancher\(\);$/m,
  // recevoir() est le point d'entree reel du serveur : passer par ajouter() sauterait
  // tout le routage, et c'est ainsi qu'un apercu montrait des selecteurs vides.
  `const __l = ${JSON.stringify(LISTES)};\n__l.forEach(recevoir);\n`
  + `const __e = ${JSON.stringify(EVENEMENTS)};\n__e.forEach(recevoir);\n`
  + `zoneMot.textContent = "Emberlificotage"; zoneCogite.hidden = false;\n`
  + `window.scrollTo(0, 0);`) + SONDE;

if (!html.includes("__l.forEach")) {
  console.error("  ECHEC l'injection n'a pas trouve l'appel de niveau superieur a brancher()");
  process.exit(1);
}

const dossier = fs.mkdtempSync(path.join(os.tmpdir(), "rendu-"));
const fichier = path.join(dossier, "page.html");
fs.writeFileSync(fichier, html);

function rendre(largeur, hauteur) {
  return execFileSync(chrome, [
    "--headless", "--disable-gpu", "--no-sandbox", "--virtual-time-budget=6000",
    `--window-size=${largeur},${hauteur}`, "--dump-dom", "file://" + fichier,
  ], { encoding: "utf8", stdio: ["pipe", "pipe", "pipe"], maxBuffer: 40 * 1024 * 1024 });
}

let dom;
try {
  dom = rendre(1280, 900);
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

// Le passe n'est PAS grise : c'est la meme conversation, on la reprend. Un affaiblissement
// visuel donnait l'impression de lire une archive alors qu'on relit son propre travail.
const passees = r.lignes.filter(l => l.passe);
dire(passees.length > 0 && passees.every(l => l.opacite === 1),
     `les lignes rejouées ont la même intensité que le direct (opacité ${passees[0]?.opacite})`);

// le champ ne montre pas de barre de defilement quand il n'en a pas besoin
dire(r.champ.debordement === "hidden",
     `le champ vide n'affiche aucune barre de défilement (overflow-y: ${r.champ.debordement})`);

dire(r.page.hauteur < 6000, `la page garde une hauteur saine (${r.page.hauteur} px)`);

// --- ergonomie : ce qui se degrade sans qu'on s'en apercoive -----------------------------
dire(r.champ.souffleBas >= 12,
     `le champ respire sous lui : ${r.champ.souffleBas} px jusqu'au bas de l'écran`);

dire(r.entete <= 110, `l'en-tête tient en deux rangs (${r.entete} px)`);

const bo = r.boites;
if (bo.modele && bo.effort) {
  dire(bo.modele.w < 140 && bo.effort.w < 140,
       `les sélecteurs restent compacts (modèle ${bo.modele.w} px, effort ${bo.effort.w} px)`);
}
if (bo.direct) {
  dire(bo.direct.l > r.vue.w * 0.5,
       `la zone « ce qui se passe » reste à droite (x=${bo.direct.l} sur ${r.vue.w})`);
}
if (bo.cogit && bo.barre) {
  dire(bo.cogit.b <= bo.barre.t + 1,
       `le bandeau de cogitation ne recouvre pas la barre (${bo.cogit.b} <= ${bo.barre.t})`);
}

// --- le micro du bas : atteignable sans viser -------------------------------------------
// 40 px est la cible confortable au pouce comme a la souris. Un bouton de 24 px se rate, et
// celui-la sert precisement dans un geste rapide : couper pour corriger, rouvrir pour dicter.
if (bo.microBas && bo.champBoite) {
  dire(bo.microBas.w >= 38 && bo.microBas.h >= 38,
       `le micro du bas est atteignable (${bo.microBas.w}x${bo.microBas.h} px)`);
  // Aligne sur la DERNIERE ligne du champ : quand le champ grandit, le bouton doit rester
  // en bas avec lui, pas flotter au milieu d'une grande boite.
  dire(Math.abs(bo.microBas.b - bo.champBoite.b) <= 3,
       `il reste aligne sur le bas du champ (${bo.microBas.b} vs ${bo.champBoite.b})`);
  dire(bo.microBas.l < bo.champBoite.l,
       `et il precede le champ, du cote ou va la main (${bo.microBas.l} < ${bo.champBoite.l})`);
}
if (bo.convs) {
  dire(bo.convs.w > 0 && bo.convs.w < 240,
       `le bouton des conversations reste compact (${bo.convs.w} px)`);
}

// --- rien ne doit deborder sur le cote --------------------------------------------------
// Un panneau qui elargit la page ne se voit pas comme un bug : on croit que l'interface est
// « comme ça », on defile lateralement sans raison, et une partie du contenu reste cachee.
dire(r.deborde.page <= 0,
     `la page ne déborde pas horizontalement, panneaux ouverts (${r.deborde.page} px)`);
dire(r.deborde.corps <= 0,
     `le corps non plus (${r.deborde.corps} px)`);
for (const [id, b2] of Object.entries(r.panneaux || {})) {
  dire(b2.w > 200, `#${id} est bien REMPLI quand on le mesure (${b2.w} px)`);
  dire(b2.d <= r.deborde.vue,
       `#${id} tient dans la fenêtre (bord droit ${b2.d} <= ${r.deborde.vue})`);
  dire(b2.l >= 0, `#${id} ne sort pas à gauche (${b2.l})`);
}

// --- et en fenetre ETROITE ---------------------------------------------------------------
// Corriger un debordement a 1280 px ne prouve rien pour un portable a 1366, une fenetre en
// demi-ecran, ou un panneau lateral d'editeur ouvert a cote. C'est precisement la que les
// largeurs minimales se retournent contre l'interface : elles ne peuvent plus retrecir, donc
// c'est la page qui s'elargit.
try {
  const domEtroit = rendre(860, 780);
  const brutE = /<pre id="releve">([\s\S]*?)<\/pre>/.exec(domEtroit);
  if (brutE) {
    const re = JSON.parse(brutE[1].replace(/&quot;/g, '"').replace(/&amp;/g, "&")
                                  .replace(/&lt;/g, "<").replace(/&gt;/g, ">"));
    dire(re.deborde.page <= 0,
         `en 860 px de large, la page ne déborde toujours pas (${re.deborde.page} px)`);
    for (const [id, b2] of Object.entries(re.panneaux || {})) {
      dire(b2.d <= re.deborde.vue && b2.l >= 0,
           `#${id} tient encore (${b2.l} → ${b2.d} dans ${re.deborde.vue})`);
    }
    dire(re.champ.souffleBas >= 12,
         `et le champ respire encore sous lui (${re.champ.souffleBas} px)`);
  }
} catch (e) {
  console.log("  ~     seconde mesure en fenêtre étroite impossible — ignorée");
}

// --- les barres du micro doivent SE VOIR ------------------------------------------------
for (const [etat, o] of Object.entries(r.ondes || {})) {
  dire(o.n === 5, `${etat} : les cinq barres existent (${o.n})`);
  const visibles = o.barres.filter(b3 => b3.w >= 2 && b3.h >= 2 && b3.opac > 0.2);
  dire(visibles.length === o.n,
       `${etat} : les ${o.n} barres sont visibles (${visibles.length} le sont — `
       + `${o.barres.map(b3 => `${b3.w}x${b3.h}`).join(" ")})`);
  const transparent = o.barres.filter(b3 => /rgba\(0, 0, 0, 0\)|transparent/.test(b3.fond));
  dire(!transparent.length, `${etat} : aucune barre sans fond`);
}
const O = r.ondes || {};
if (O["micro-rond ouvert"]) {
  // Au REPOS, un profil d'égaliseur plutôt que cinq traits identiques : la forme dit « micro »
  // avant même qu'on ait parlé. C'est ce qui manquait — cinq traits de 5 px se lisaient comme
  // un rond vide.
  const h = O["micro-rond ouvert"].barres.map(b3 => parseFloat(b3.hCalc));
  dire(new Set(h).size >= 3, `au repos, les barres dessinent un profil varié (${h.join(" ")})`);
  dire(Math.max(...h) >= 12, `et la plus haute se voit vraiment (${Math.max(...h)} px)`);
}
if (O["micro-rond coupe"]) {
  const h = O["micro-rond coupe"].barres.map(b3 => parseFloat(b3.hCalc));
  dire(new Set(h).size === 1,
       `coupé, TOUTES les barres s'aplatissent à la même hauteur (${h.join(" ")})`);
  dire(h[0] <= 5, `et à plat (${h[0]} px) : la forme dit l'état sans dépendre de la couleur`);
}
if (r.animeQuandParle) {
  const noms = r.animeQuandParle.split(",");
  dire(noms.every(n => n && n !== "none"),
       `parole détectée : les cinq barres sont animées (${r.animeQuandParle})`);
}
{
}

// --- la pile ne se recouvre pas -----------------------------------------------------------
if (r.pile) {
  const { cog, att, note, barre } = r.pile;
  const chevauche = (a2, b2) => a2.b > b2.t + 1 && b2.b > a2.t + 1
                             && a2.d > b2.l + 1 && b2.d > a2.l + 1;
  dire(!chevauche(cog, att), `cogitation et attente ne se recouvrent pas `
       + `(${cog.t}..${cog.b} vs ${att.t}..${att.b})`);
  dire(!chevauche(att, note), `attente et note d'historique non plus `
       + `(${att.t}..${att.b} vs ${note.t}..${note.b})`);
  dire(!chevauche(cog, note), "cogitation et note non plus");
  dire(att.b <= barre.t + 1,
       `et la pile reste AU-DESSUS de la barre (${att.b} <= ${barre.t})`);
  dire(note.d <= r.deborde.vue && att.d <= r.deborde.vue,
       "aucun element de la pile ne sort de la fenêtre");
}

fs.rmSync(dossier, { recursive: true, force: true });
console.log(`\n${r.lignes.length} lignes mesurées — ${ok ? "TOUT VERT" : "DES ECHECS"}`);
process.exit(ok ? 0 : 1);
