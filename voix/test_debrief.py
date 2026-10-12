#!/usr/bin/env python3
"""Le débrief : ce que tu lis et ce que tu entends sortent du MÊME flux.

C'est le point de jonction entre trois choses qui divergeaient avant : la synthèse vocale, la
ligne qui s'écrit dans le flux de la page, et ce qui est enregistré pour « répète ». Une
seule boucle les alimente toutes les trois — sinon l'écran dit une chose et la voix une
autre, et on ne sait plus laquelle croire.

Trois propriétés sont vérifiées ici, et chacune vient d'un symptôme observé :

- **la raison d'un arrêt anormal est dite EN DUR**, après le débrief. Passée par le modèle
  elle était reformulée, et pouvait sauter — une phrase sur deux est déjà écartée par la
  vérification des faits. C'est pourtant la seule information qui explique un travail
  interrompu ; la perdre ramène à « il s'arrête sans raison » ;
- **chaque réponse porte un identifiant**, sinon « relis » ne pourrait viser que la dernière,
  alors que ce qu'on veut relire est souvent celle d'avant ;
- **l'historique parlé est borné** : garder deux jours de débriefs n'a aucun usage.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("VOIX_WORKDIR", str(Path(__file__).resolve().parent.parent))

from test_ordres import FausseSession, FauxTableau, FauxWorker

ok = True


def dire(bon: bool, texte: str):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


class FauxPorteParole:
    def __init__(self, bouts):
        self.bouts = list(bouts)

    async def dire_flux(self, journal):
        for b in self.bouts:
            yield b


class FauxJournal:
    def __init__(self, lignes=("modifie voix/a.py",), arret=None, jetons=1200, duree=4.2):
        self._lignes = list(lignes)
        self._arret = arret
        self.jetons = jetons
        self.duree_s = duree

    def lignes(self):
        return self._lignes

    def pourquoi_arrete(self):
        return self._arret


class FausseConv:
    def __init__(self, tours=1, titre=""):
        self.tours = tours
        self.titre = titre
        self.session_id = "sess-1"
        self.tours_claude = []

    def tour_claude(self, texte, outils=None, jetons=None, duree=None):
        self.tours_claude.append({"texte": texte, "outils": outils,
                                  "jetons": jetons, "duree": duree})

    def note_titre(self, t):
        self.titre = t


def neuve(conv=None):
    import agent as A
    v = A.Voix.__new__(A.Voix)
    v._amorcer_etat()
    v.worker = FauxWorker()
    v.tableau = FauxTableau()
    v.quota = None
    v.conv = conv
    v.inscription = None
    v._session_directe = FausseSession()
    return v


def debriefer(v, bouts, journal):
    v.porte_parole = FauxPorteParole(bouts)

    async def tout():
        return [b async for b in v._debrief(journal)]
    return asyncio.run(tout())


# --- 1. le flux unique ---------------------------------------------------------------------

def ce_qui_se_lit_est_ce_qui_se_dit():
    print("\n=== un seul flux pour l'oreille, l'ecran et la memoire ===")
    v = neuve()
    sortis = debriefer(v, ["j'ai corrigé le micro. ", "tout est vert."], FauxJournal())
    dire(sortis == ["j'ai corrigé le micro. ", "tout est vert."],
         "les bouts sortent au fil de l'eau, pour que la voix commence avant la fin")
    lignes = [d for g, d in v.tableau.publies if g == "voix"]
    dire([l["texte"] for l in lignes] == sortis,
         "la page reçoit EXACTEMENT les memes bouts, dans le meme ordre")
    dire(all(l.get("suite") for l in lignes),
         "marques « suite » : la page les recolle au lieu d'empiler des lignes")
    dire(v._dernier_debrief == "j'ai corrigé le micro. tout est vert.",
         "et le texte complet est garde pour « repete », sans repayer de reecriture")


def chaque_reponse_porte_son_identifiant():
    print("\n=== un identifiant par réponse, pour pouvoir relire CELLE-LA ===")
    v = neuve()
    debriefer(v, ["premier."], FauxJournal())
    debriefer(v, ["second."], FauxJournal())
    dire(sorted(v._paroles) == ["p1", "p2"], f"deux reponses, deux cles : {sorted(v._paroles)}")
    dire(v._paroles["p1"] == "premier." and v._paroles["p2"] == "second.",
         "chacune garde son texte")
    fins = [d for g, d in v.tableau.publies if g == "parole_fin"]
    dire([f["id"] for f in fins] == ["p1", "p2"],
         "la page reçoit la fin de chaque reponse, avec sa cle : c'est elle qui porte les boutons")
    dire(fins[0]["mots"] == 1, "et le nombre de mots, pour afficher la longueur sans la relire")


def l_historique_parle_est_borne():
    print("\n=== l'historique parlé ne grossit pas sans fin ===")
    v = neuve()
    for i in range(45):
        debriefer(v, [f"reponse {i}."], FauxJournal())
    dire(len(v._paroles) == 40, f"au plus quarante reponses gardees ({len(v._paroles)})")
    dire("p45" in v._paroles and "p1" not in v._paroles,
         "les plus recentes restent, les plus vieilles sortent")


# --- 2. un travail interrompu le DIT ------------------------------------------------------

def la_raison_d_un_arret_est_dite_en_dur():
    print("\n=== un travail interrompu dit pourquoi, sans passer par le modèle ===")
    v = neuve()
    sortis = debriefer(v, ["j'ai commencé la correction. "],
                       FauxJournal(arret="la limite de contexte a été atteinte"))
    dire(sortis[-1] == " la limite de contexte a été atteinte.",
         f"la raison est le dernier bout dit : {sortis[-1]!r}")
    # Elle ne traverse PAS le porte-parole : la verification des faits en ecarte une phrase
    # sur deux, et c'est la seule information qui explique un travail interrompu.
    dire("la limite de contexte" in v._dernier_debrief,
         "elle entre aussi dans ce que « repete » redira")
    textes = [d["texte"] for g, d in v.tableau.publies if g == "voix"]
    dire(textes[-1].strip().endswith("."), "la page la reçoit ponctuee, prete a etre lue")

    # Le cas limite : un tour qui n'a RIEN produit mais s'est arrete anormalement. Sans ça
    # le symptome de depart revient a l'identique — « il s'arrete sans rien dire ».
    v = neuve()
    sortis = debriefer(v, [], FauxJournal(arret="interrompu"))
    dire(sortis == ["interrompu."],
         f"meme sans un mot de debrief, la raison sort seule : {sortis}")
    dire(not sortis[0].startswith(" "), "sans espace parasite en tete")


def un_tour_vide_ne_laisse_pas_de_trace_fantome():
    print("\n=== un tour qui n'a rien à dire ===")
    v = neuve()
    sortis = debriefer(v, [], FauxJournal())
    dire(sortis == [], "rien n'est dit")
    dire(v._dernier_debrief is None, "et rien n'est garde : « repete » ne redira pas du vide")
    dire(v._paroles == {}, "aucune cle n'est creee pour une reponse qui n'existe pas")
    dire("parole_fin" not in v.tableau.genres(),
         "et la page ne reçoit pas de ligne a boutons sur du vide")


# --- 3. ce qui est enregistré dans la conversation ----------------------------------------

def la_conversation_garde_le_tour_entier():
    print("\n=== ce que la conversation enregistre du tour ===")
    conv = FausseConv(tours=5, titre="déjà titrée")
    v = neuve(conv)
    debriefer(v, ["c'est fait."], FauxJournal(lignes=["modifie voix/a.py", "lance les tests"],
                                              jetons=1200, duree=4.2))
    dire(len(conv.tours_claude) == 1, "un tour enregistre")
    t = conv.tours_claude[0]
    dire(t["texte"] == "c'est fait.", "avec ce qui a ete dit")
    dire(t["outils"] == ["modifie voix/a.py", "lance les tests"],
         "ce qui a ete fait, pour pouvoir relire le tour sans le rejouer")
    dire(t["jetons"] == 1200 and t["duree"] == 4.2,
         "et ce qu'il a coute : c'est ça qui explique une attente de trente secondes")


def un_journal_casse_n_empeche_pas_d_enregistrer():
    print("\n=== un journal qui ne répond plus ===")
    class JournalCasse:
        def lignes(self):
            raise RuntimeError("journal indisponible")

        def pourquoi_arrete(self):
            return None

    conv = FausseConv(tours=5, titre="t")
    v = neuve(conv)
    sortis = debriefer(v, ["c'est fait."], JournalCasse())
    dire(sortis == ["c'est fait."],
         "le debrief sort quand meme : un journal casse ne doit pas faire taire la voix")
    dire(conv.tours_claude[0]["outils"] == [],
         "la liste d'outils retombe sur du vide plutot que de remonter l'exception")
    dire(conv.tours_claude[0]["jetons"] is None,
         "et les mesures absentes valent None, pas un chiffre invente")


def le_titre_arrive_une_fois_et_en_fond():
    print("\n=== le titre : après le premier échange, une seule fois ===")
    async def essai(tours, titre):
        import agent as A
        conv = FausseConv(tours=tours, titre=titre)
        v = neuve(conv)
        v.porte_parole = FauxPorteParole(["c'est fait."])
        titres = []

        async def faux_titrer(question, reponse=""):
            titres.append(question)
            return "Correction du micro"
        A.Voix._titrer = lambda self, q: faux_titrer(q)
        v._dernier_tour_utilisateur = "corrige le micro"
        [b async for b in v._debrief(FauxJournal())]
        await asyncio.sleep(0)           # laisse la tache de fond demarrer
        return titres

    dire(asyncio.run(essai(1, "")) == ["corrige le micro"],
         "au premier echange, le titre est demande d'apres LA QUESTION")
    dire(asyncio.run(essai(5, "")) == [],
         "plus tard, non : titrer une conversation deja lancee n'apporte rien")
    dire(asyncio.run(essai(1, "déjà titrée")) == [],
         "et jamais deux fois : un titre pose ne se rediscute pas")


def main():
    ce_qui_se_lit_est_ce_qui_se_dit()
    chaque_reponse_porte_son_identifiant()
    l_historique_parle_est_borne()
    la_raison_d_un_arret_est_dite_en_dur()
    un_tour_vide_ne_laisse_pas_de_trace_fantome()
    la_conversation_garde_le_tour_entier()
    un_journal_casse_n_empeche_pas_d_enregistrer()
    le_titre_arrive_une_fois_et_en_fond()
    print("\nTOUT VERT" if ok else "\nDES ECHECS")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
