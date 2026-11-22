#!/usr/bin/env python3
"""Une proposition faite un jour ne se perd pas dans le flux.

Le symptôme, dit tel quel : « le dev est fini, je ne regarde pas le message de réponse, et
je renvoie un message pour une nouvelle chose ». Ce qui se perd là n'est jamais quelconque :
c'est la partie qui PROPOSE la suite. Elle s'écrit une fois, à la fin, au moment précis où
l'attention est déjà sur le sujet suivant — et le message d'après l'enterre.

Ce que ce fichier protège, dans l'ordre où ça casse :

1. **Ce qui propose est reconnu, ce qui raconte ne l'est pas.** Une liste qui se remplit de
   phrases au hasard se ferme au bout de trois jours et ne se rouvre plus.
2. **Une proposition a un ÉTAT.** C'est toute la différence avec le flux : ouverte, faite,
   écartée. Sans état, rien ne distingue une proposition traitée d'une jamais lue.
3. **Elle ne s'empile pas.** La même phrase répétée dans trois bilans fait une entrée.
4. **Elle se solde quand on la demande**, y compris des jours plus tard et depuis une autre
   conversation — sinon la liste ne décroît jamais, et une liste qui ne décroît pas ment.
5. **Le serveur la rend et la tranche**, parce que la page doit pouvoir la demander à
   l'ouverture : la liste survit aux conversations, le flux non.
"""

import asyncio
import json
import os
import sys
import time
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import aiohttp

import config
import propositions
from tableau import Tableau

ok = True


def dire(bon, texte):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


# --- TU : ce qui propose, et ce qui n'en est pas -----------------------------------------
def on_reconnait_ce_qui_propose():
    print("\n=== une phrase qui propose est repérée là où elle est écrite ===")
    fin = """C'est fait, 2478 vertes.

Ce qui reste, et que je n'ai pas traité : 94 conversations sur 536 n'ont pas de ligne `_fin`.

Il faudrait un filet côté tableau de bord pour les rattraper.
"""
    trouve = propositions.extraire(fin)
    dire(len(trouve) == 2, f"les deux propositions de ce bilan, et elles seules ({len(trouve)})")
    dire(any("_fin" in p for p in trouve),
         "le code en ligne est GARDÉ : « pas de ligne _fin » sans son objet ne veut plus rien dire")

    # Le défaut de départ : elle est au MILIEU, pas à la fin. C'est l'inverse d'une question,
    # qui engage la suite et se tient donc en dernier.
    milieu = ("J'ai corrigé le micro.\n\nOn pourrait aussi borner l'attente de la liaison, "
              "pendant qu'on y est.\n\nLa suite complète passe.")
    dire(propositions.extraire(milieu) == ["On pourrait aussi borner l'attente de la liaison, "
                                           "pendant qu'on y est."],
         "une proposition au milieu du message est vue — c'est là qu'on ne la lit jamais")

    print("\n=== et un compte rendu ordinaire n'en produit aucune ===")
    for texte in ["C'est fait. Le build passe, 1342 vertes.",
                  "Il aurait fallu le voir plus tôt, mais c'est corrigé.",
                  "J'ai mis en place ce que tu avais demandé.",
                  "Le test tombait parce qu'une ligne masquée mesure zéro pixel."]:
        dire(propositions.extraire(texte) == [], f"rien à retenir dans « {texte[:46]}… »")

    print("\n=== ni un bout de phrase, ni un paragraphe entier ===")
    dire(propositions.extraire("À faire ensuite.") == [],
         "trop court pour vouloir dire quelque chose hors de son message")
    long = "Il faudrait " + "x" * (propositions.MAXI + 40)
    dire(propositions.extraire(long) == [],
         "trop long pour une liste qu'on balaie du pouce")
    beaucoup = "\n".join(f"- Il faudrait corriger le point numéro {i} du rapport." for i in range(9))
    dire(len(propositions.extraire(beaucoup)) == propositions.PAR_TOUR,
         f"un bilan bavard ne verse pas neuf lignes d'un coup ({propositions.PAR_TOUR} au plus)")

    print("\n=== le code n'est pas une proposition ===")
    dans_bloc = "Voilà :\n\n```python\n# il faudrait passer par là un jour\nx = 1\n```\n"
    dire(propositions.extraire(dans_bloc) == [],
         "un commentaire DANS un bloc de code ne propose rien à personne")


# --- TU : le registre --------------------------------------------------------------------
def une_proposition_a_un_etat():
    print("\n=== elle est retenue, elle ne s'empile pas, elle se tranche ===")
    with tempfile.TemporaryDirectory() as d:
        ancien = propositions.FICHIER
        propositions.FICHIER = Path(d) / "propositions.json"
        try:
            neuves = propositions.enregistrer(["Il faudrait borner l'attente de la liaison."],
                                              projet="claude-talk", session="sess-1")
            dire(len(neuves) == 1 and propositions.compter() == 1, "une proposition, une entrée")

            encore = propositions.enregistrer(["Il faudrait   borner l'attente de la LIAISON !"])
            dire(encore == [] and propositions.compter() == 1,
                 "la même phrase, reformulée à la ponctuation près, ne fait pas un doublon")

            cle = propositions.ouvertes()[0]["cle"]
            dire(propositions.marquer(cle, "ecartee"), "elle s'écarte")
            dire(propositions.compter() == 0, "et elle quitte la liste des ouvertes")

            # Le piège qui ferait fermer la liste pour de bon : une proposition écartée qui
            # revient parce que le bilan du tour suivant l'a répétée.
            propositions.enregistrer(["Il faudrait borner l'attente de la liaison."])
            dire(propositions.compter() == 0,
                 "écartée, elle ne revient pas quand la réponse suivante la répète")

            dire(not propositions.marquer("inconnue", "faite"),
                 "trancher une clé qui n'existe pas ne crée rien")

            propositions.FICHIER.write_text("{ ceci n'est pas du JSON", encoding="utf-8")
            dire(propositions.ouvertes() == [],
                 "un fichier illisible rend une liste vide, il ne fait pas tomber la session")
        finally:
            propositions.FICHIER = ancien


def demander_une_proposition_la_solde():
    print("\n=== « mets ça en place » la sort de la liste, même trois jours plus tard ===")
    with tempfile.TemporaryDirectory() as d:
        ancien = propositions.FICHIER
        propositions.FICHIER = Path(d) / "propositions.json"
        try:
            propositions.enregistrer([
                "Il faudrait un filet côté tableau pour les conversations sans ligne de fin.",
                "On pourrait colorer les propositions dans le flux de la page.",
            ])
            faites = propositions.resoudre(
                "Mets en place cette proposition : « On pourrait colorer les propositions "
                "dans le flux de la page. »")
            dire(len(faites) == 1 and "colorer" in faites[0],
                 "celle qu'on vient de demander se ferme")
            restantes = propositions.ouvertes()
            dire(len(restantes) == 1 and "filet" in restantes[0]["texte"],
                 "l'autre reste : elle n'a pas été demandée")

            dire(propositions.resoudre("ok") == [],
                 "un message trop court ne ferme rien — « ok » ne désigne aucune proposition")
            dire(propositions.resoudre("corrige le son qui se coupe dans le fil") == [],
                 "un message sur un autre sujet ne ferme rien non plus")
            dire(propositions.compter() == 1, "et la liste n'a pas bougé")
        finally:
            propositions.FICHIER = ancien


# --- TI : le serveur rend la liste et la tranche -----------------------------------------
async def le_serveur_sert_la_liste_et_la_tranche():
    print("\n=== la page peut demander la liste, et trancher dessus ===")
    with tempfile.TemporaryDirectory() as d:
        ancien = propositions.FICHIER
        propositions.FICHIER = Path(d) / "propositions.json"
        t = Tableau(port=7897, ouvrir=False)
        url = await t.demarrer()
        try:
            propositions.enregistrer(["Il faudrait borner l'attente de la liaison à trois "
                                      "secondes."], projet="claude-talk")
            async with aiohttp.ClientSession() as http:
                async with http.get(f"{url}/propositions.json") as r:
                    liste = (await r.json())["liste"]
                dire(len(liste) == 1 and liste[0]["projet"] == "claude-talk",
                     "la liste arrive par HTTP : une page qui s'ouvre la demande, "
                     "elle n'a pas à avoir été là quand la proposition est passée")
                cle = liste[0]["cle"]

                async with http.post(f"{url}/proposition",
                                     json={"cle": cle, "etat": "ecartee"}) as r:
                    rep = await r.json()
                dire(rep.get("ok") and rep.get("total") == 0, "écarter la fait sortir")

                async with http.post(f"{url}/proposition", json={"cle": cle}) as r:
                    dire(r.status == 400, "un ordre sans état est refusé, pas deviné")

                async with http.get(f"{url}/propositions.json") as r:
                    dire((await r.json())["liste"] == [], "et elle ne revient pas")

            publies = [json.loads(c).get("genre") for c in
                       [c for c in t.etat.values()]] if t.etat else []
            dire("propositions" in [e.get("genre") for e in list(t.histoire)],
                 "la décision est publiée : un onglet resté ouvert ailleurs voit la "
                 "pastille décroître sans qu'on le recharge")
        finally:
            await t.arreter()
            propositions.FICHIER = ancien


# --- TI : le branchement dans l'agent ----------------------------------------------------
def l_agent_note_et_solde_sans_jamais_tomber():
    print("\n=== l'agent note ce qu'il propose, et solde ce qu'on lui demande ===")
    source = (Path(__file__).parent / "agent.py").read_text(encoding="utf-8")
    debut = source.index("async def _debrief")
    corps = source[debut:source.index("def _noter_propositions", debut)]
    dire("_noter_propositions" in corps,
         "le relevé se fait dans _debrief : c'est le seul endroit qui tient le texte "
         "complet d'une réponse")

    import agent as A

    class AgentMinimal:
        conv = None
        _dernier_debrief = None

        def __init__(self):
            self.vus = []

        def _voir(self, genre, **d):
            self.vus.append((genre, d))

        _noter_propositions = A.Voix._noter_propositions
        _solder_propositions = A.Voix._solder_propositions

    with tempfile.TemporaryDirectory() as d:
        ancien = propositions.FICHIER
        propositions.FICHIER = Path(d) / "propositions.json"
        try:
            a = AgentMinimal()
            a._noter_propositions("C'est fait. Il faudrait maintenant borner l'attente de "
                                  "la liaison à trois secondes.")
            dire(propositions.compter() == 1, "la proposition de la réponse est retenue")
            dire(any(g == "propositions" for g, _ in a.vus),
                 "la page l'apprend tout de suite : la pastille monte sans recharger")

            a.vus.clear()
            a._solder_propositions("Mets en place cette proposition : « Il faudrait maintenant "
                                   "borner l'attente de la liaison à trois secondes. »")
            dire(propositions.compter() == 0, "et la demander la solde")

            # Le garde-fou qui compte le plus : une liste de bonnes idées ne vaut pas qu'on
            # perde une réponse parce que son fichier était en lecture seule.
            propositions.FICHIER = Path(d) / "interdit" / "propositions.json"
            Path(d, "interdit").mkdir()
            Path(d, "interdit").chmod(0o500)
            try:
                a._noter_propositions("Il faudrait essayer d'écrire là-dedans, pour voir.")
                dire(True, "un registre impossible à écrire ne fait pas tomber le débrief")
            except Exception as e:
                dire(False, f"le débrief est tombé sur le registre : {e}")
            finally:
                Path(d, "interdit").chmod(0o700)
        finally:
            propositions.FICHIER = ancien


# --- la page et le serveur parlent de la même chose --------------------------------------
def la_page_surligne_ce_que_le_registre_retient():
    print("\n=== la page et le registre s'accordent sur ce qui est une proposition ===")
    page = (Path(__file__).parent / "tableau.py").read_text(encoding="utf-8")
    dire("MARQUE_PROP" in page and "class=\"prop\"" in page,
         "la page porte sa propre liste de marqueurs — elle surligne sans attendre l'agent")
    dire('id="props"' in page and "/propositions.json" in page,
         "et le bouton, qui va chercher la liste par HTTP")
    # Les deux listes ne peuvent pas être partagées (l'une est en Python, l'autre dans du
    # JavaScript servi en dur), donc on vérifie qu'elles sont IDENTIQUES, alternative par
    # alternative. Une divergence se paie cher dans le mauvais sens : on voit une phrase
    # surlignée en jaune, on ouvre la liste pour la traiter, elle n'y est pas.
    import re as _re
    bloc = _re.search(r"const MARQUEURS_PROP = \[(.*?)\];", page, _re.S)
    dire(bloc is not None, "la page expose sa liste de marqueurs en clair")
    cote_page = [m.replace("\\\\", "\\") for m in _re.findall(r'"(.*?)",', bloc.group(1))]
    dire(cote_page == propositions.MARQUEURS,
         f"les deux listes sont la même, à l'alternative près "
         f"({len(cote_page)} côté page, {len(propositions.MARQUEURS)} côté registre)")


# --- TI : l'amorce depuis les transcripts ------------------------------------------------
def l_amorce_relit_l_historique_une_seule_fois():
    print("\n=== la liste ne naît pas vide : ce qui a été proposé ces jours-ci y est déjà ===")
    with tempfile.TemporaryDirectory() as d:
        dossier = Path(d)
        anciens = propositions.FICHIER, config.JOURNAL_DIR
        propositions.FICHIER = dossier / "propositions.json"
        config.JOURNAL_DIR = str(dossier)
        try:
            (dossier / "2026-11-20_0900_palier.md").write_text(
                "# Conversation — palier\n"
                "## 09:00 — toi\n"
                "Il faudrait qu'on scinde les cartes à cinq réponses, tu peux voir ?\n"
                "## 09:02 — claude\n"
                "C'est fait, les cartes se scindent.\n"
                "Je propose ensuite de replier les en-têtes quand on descend dans la liste.\n",
                encoding="utf-8")
            vieux = dossier / "2026-10-01_0900_oublie.md"
            vieux.write_text(
                "# Conversation — oublie\n"
                "## 09:00 — claude\n"
                "On pourrait aussi passer la veille en deux temps, dis-moi si j'enchaîne.\n",
                encoding="utf-8")
            os.utime(vieux, (time.time() - 40 * 86400,) * 2)

            n = propositions.amorcer()
            textes = [p["texte"] for p in propositions.ouvertes()]
            dire(n == 1 and len(textes) == 1,
                 f"une proposition retrouvée dans les transcripts récents → {textes}")
            dire(textes and "replier les en-têtes" in textes[0],
                 "c'est bien ce que Claude a proposé, pas ce que j'ai demandé moi-même")
            dire(propositions.ouvertes()[0]["projet"] == "palier",
                 "et elle sait de quel projet elle vient : le nom du fichier le dit")
            dire(abs(propositions.ouvertes()[0]["quand"]
                     - (dossier / "2026-11-20_0900_palier.md").stat().st_mtime) < 2,
                 "datée du jour où elle a été faite, pas du jour où on l'a relue")
            dire(all("veille en deux temps" not in t for t in textes),
                 "au-delà de dix jours, on laisse dormir : ça a sûrement été fait ou oublié")

            # Le piège qui ferait détester la liste : on écarte dix propositions à la main,
            # on relance l'agent le lendemain, et elles sont toutes revenues.
            propositions.marquer(propositions.ouvertes()[0]["cle"], "ecartee")
            dire(propositions.amorcer() == 0 and propositions.compter() == 0,
                 "l'amorce ne repasse jamais : ce qui a été écarté reste écarté")
        finally:
            propositions.FICHIER, config.JOURNAL_DIR = anciens


def l_amorce_se_plafonne():
    print("\n=== et elle se plafonne : une liste de quatre-vingts lignes ne se traite pas ===")
    with tempfile.TemporaryDirectory() as d:
        dossier = Path(d)
        anciens = propositions.FICHIER, config.JOURNAL_DIR
        propositions.FICHIER = dossier / "propositions.json"
        config.JOURNAL_DIR = str(dossier)
        try:
            for i in range(20):
                (dossier / f"2026-11-{i + 1:02d}_0900_projet{i}.md").write_text(
                    "## 09:00 — claude\n"
                    f"Je propose de reprendre le chantier numéro {i} des pastilles de mesure.\n",
                    encoding="utf-8")
            n = propositions.amorcer(plafond=5)
            dire(n == 5, f"cinq au plus, pas vingt → {n}")
            # Les PLUS RÉCENTES : le dernier bilan non lu est celui qui compte encore.
            textes = " ".join(p["texte"] for p in propositions.ouvertes())
            dire("numéro 19" in textes and "numéro 0" not in textes,
                 "et ce sont les dernières, pas les premières trouvées")
        finally:
            propositions.FICHIER, config.JOURNAL_DIR = anciens


def l_agent_amorce_au_demarrage():
    print("\n=== et l'agent la déclenche au lancement, sans attendre quoi que ce soit ===")
    source = (Path(__file__).parent / "agent.py").read_text(encoding="utf-8")
    dire("propositions.amorcer" in source,
         "l'amorce est branchée dans l'agent — sinon elle n'arrive jamais")
    debut = source.index("async def entrypoint")
    avant = source[debut:source.index("await worker.start()", debut)]
    dire("propositions.amorcer" in avant,
         "et avant le lancement du worker : la page lit la liste bien avant lui")


async def principal():
    on_reconnait_ce_qui_propose()
    une_proposition_a_un_etat()
    demander_une_proposition_la_solde()
    await le_serveur_sert_la_liste_et_la_tranche()
    l_agent_note_et_solde_sans_jamais_tomber()
    la_page_surligne_ce_que_le_registre_retient()
    l_amorce_relit_l_historique_une_seule_fois()
    l_amorce_se_plafonne()
    l_agent_amorce_au_demarrage()
    print(f"\n{'TOUT VERT' if ok else 'DES ECHECS'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
