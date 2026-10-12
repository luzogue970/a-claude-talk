"""Le débrief parlé : rien d'inventé n'atteint le haut-parleur.

Ce module fabrique ce que tu ENTENDS à la fin de chaque tour. Il était couvert à 17 %, et
la partie non couverte était justement la garde : une phrase qui cite un identifiant ou un
chiffre absent de la source est une invention, et une invention dite à voix haute ne se
rattrape pas — on l'entend, on la croit, on agit dessus.

Ce qui est protégé ici :

  1. la détection des jetons durs : ce qu'on refuse d'inventer, et ce qu'on a le droit de
     reformuler librement ;
  2. le filtrage phrase à phrase : une phrase fausse tombe, les autres continuent — perdre
     tout le débrief sur une seule phrase serait pire ;
  3. le repli mécanique quand tout est rejeté : dire la version plate, qui est au moins
     vraie, plutôt que de se taire sur un travail fait ;
  4. le titre d'une conversation : un modèle qui accuse réception ne doit rien laisser.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from claude_agent_sdk import StreamEvent

import porte_parole
from porte_parole import PorteParole, _nettoyer_titre, aplatir, verifier_faits

ok = True


def dire(bon: bool, texte: str):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


# --- 1. ce qui ne s'invente pas ----------------------------------------------------------

def les_faits_durs_se_verifient():
    print("\n=== un identifiant ou un chiffre doit venir de la source ===")
    source = "j'ai modifié voix/tableau.py et lancé 42 tests"
    dire(verifier_faits("J'ai touché voix/tableau.py.", source) == [],
         "un nom de fichier present dans la source passe")
    dire(verifier_faits("J'ai lancé 42 tests.", source) == [],
         "un nombre present dans la source passe")
    dire(verifier_faits("J'ai touché voix/journal.py.", source) == ["journal.py"],
         "un nom de fichier ABSENT est signale : c'est une invention")
    dire(verifier_faits("J'ai lancé 137 tests.", source) == ["137"],
         "un nombre invente est signale")
    # Le point d'equilibre : reformuler librement est tout l'interet d'un debrief parle.
    dire(verifier_faits("J'ai terminé, tout est vert et prêt à partir.", source) == [],
         "une reformulation sans fait dur passe : on reformule, on n'invente pas")
    dire(verifier_faits("J'ai corrigé trois fichiers.", source) == [],
         "« trois » en toutes lettres n'est pas un fait dur : il se derive du journal")
    # Les abreviations courantes ressemblent a des identifiants pointes sans en etre.
    dire(verifier_faits("C.a.d que tout marche, p.ex ici.", source) == [],
         "les abreviations courantes ne sont pas prises pour des identifiants")


def aplatir_retire_ce_qui_ne_se_lit_pas():
    print("\n=== le repli mecanique : enlever ce qu'une voix ne peut pas dire ===")
    md = (
        "# Titre\n"
        "Voici le **resultat** avec `du code` en ligne.\n"
        "```python\n"
        "ne_pas_lire_ceci()\n"
        "```\n"
        "| col | col |\n"
        "---\n"
        "- un point de liste\n"
        "> une citation\n"
        "Un [lien](http://exemple) et une ![image](x.png).\n"
    )
    plat = aplatir(md)
    dire("ne_pas_lire_ceci" not in plat, "un bloc de code n'est jamais lu a voix haute")
    dire("|" not in plat, "un tableau non plus")
    dire("http://exemple" not in plat and "lien" in plat,
         "un lien garde son libelle et perd son adresse : " + plat[-70:])
    dire("#" not in plat and "**" not in plat, "le balisage disparait")
    dire("un point de liste." in plat, "une puce devient une phrase ponctuee")
    dire(plat.endswith("."), "et tout finit par une ponctuation, sinon la voix ne respire pas")
    dire(aplatir("") == "", "un texte vide ne produit rien plutot que de planter")


# --- 2. le titre -------------------------------------------------------------------------

def un_titre_ou_rien():
    print("\n=== titre : un nom, pas une prise de parole ===")
    dire(_nettoyer_titre("Quota Azure") == "Quota Azure", "un titre simple passe tel quel")
    dire(_nettoyer_titre("Voici : quota Azure") == "quota Azure",
         "un preambule se retire, le titre qu'il annonce est garde")
    dire(_nettoyer_titre("Titre : voici : quota Azure") == "quota Azure",
         "deux preambules empiles se retirent aussi")
    # Mesure : « Compris. Je vais titrer les conversations… » est sorti tel quel et se
    # retrouvait dans la liste, tronque au milieu d'un mot.
    dire(_nettoyer_titre("Compris. Je vais titrer les conversations") == "",
         "un accuse de reception ne laisse RIEN : rien vaut mieux qu'une phrase tronquee")
    for refus in ("Entendu, je m'en occupe", "Bien sûr ! Voilà", "D'accord",
                  "Je propose quelque chose"):
        dire(_nettoyer_titre(refus) == "", f"« {refus} » ne donne pas de titre")
    dire(_nettoyer_titre("Refonte complète du moteur de rendu vocal avec cache")
         == "Refonte complète du moteur",
         "au-dela de quatre mots on coupe sur les MOTS, pas au milieu d'un mot")
    dire(len(_nettoyer_titre("a" * 80)) <= 40, "et jamais plus de quarante caracteres")
    dire(_nettoyer_titre("") == "" and _nettoyer_titre(None) == "",
         "l'absence de reponse ne plante pas")
    dire(_nettoyer_titre("Quota Azure. Et aussi autre chose") == "Quota Azure",
         "seule la premiere phrase est retenue")


# --- 3. integration : le flux parle, phrase a phrase -------------------------------------

class FauxJournal:
    """Ce que l'agent remet au porte-parole a la fin d'un tour."""

    def __init__(self, textes, lignes=(), question="corrige le micro", arret=None):
        self.textes = list(textes)
        self._lignes = list(lignes)
        self.outils = list(lignes)
        self.question = question
        self._arret = arret

    def lignes(self):
        return self._lignes

    def pourquoi_arrete(self):
        return self._arret


class FauxClient:
    """Un client SDK qui rend les phrases qu'on lui a posees, en flux."""

    def __init__(self, phrases):
        self.phrases = list(phrases)
        self.prompts = []

    async def query(self, prompt):
        self.prompts.append(prompt)

    async def receive_response(self):
        for p in self.phrases:
            yield StreamEvent(
                uuid="u", session_id="s",
                event={"type": "content_block_delta",
                       "delta": {"type": "text_delta", "text": p}})


def parle(phrases, journal) -> tuple[str, FauxClient]:
    pp = PorteParole.__new__(PorteParole)
    pp.historique = []
    pp.tours_memoire = 4
    pp.client = FauxClient(phrases)
    return asyncio.run(pp.dire(journal)), pp.client


def une_phrase_fausse_tombe_les_autres_continuent():
    print("\n=== flux : une invention tombe, le debrief continue ===")
    j = FauxJournal(["J'ai corrigé le micro dans voix/tableau.py."],
                    lignes=["modifie voix/tableau.py"])
    dit, _ = parle(["J'ai corrigé le micro. ", "Tout est prêt. "], j)
    dire("corrigé le micro" in dit and "Tout est prêt" in dit,
         f"un debrief fidele passe en entier : {dit!r}")

    # La phrase du milieu invente un fichier. Elle seule doit tomber.
    dit, _ = parle(["J'ai corrigé le micro. ",
                    "J'ai aussi touché voix/inexistant.py. ",
                    "Tout est prêt. "], j)
    dire("inexistant" not in dit,
         f"la phrase qui invente un fichier ne sort pas : {dit!r}")
    dire("corrigé le micro" in dit and "Tout est prêt" in dit,
         "mais les phrases fideles qui l'encadrent sont dites quand meme")

    # Le dernier fragment sans ponctuation finale doit etre verifie lui aussi.
    dit, _ = parle(["Tout est prêt. ", "et j'ai lancé 999 tests"], j)
    dire("999" not in dit,
         f"le dernier fragment, meme sans point final, est verifie : {dit!r}")


def tout_rejeter_ne_rend_pas_muet():
    print("\n=== flux : si tout est rejete, on dit la version plate ===")
    j = FauxJournal(["J'ai corrigé **le micro** dans `voix/tableau.py`."],
                    lignes=["modifie voix/tableau.py"])
    dit, _ = parle(["J'ai touché voix/faux1.py. ", "Puis voix/faux2.py. "], j)
    dire("faux1" not in dit and "faux2" not in dit, "aucune invention ne passe")
    dire("micro" in dit,
         f"mais on ne reste pas muet sur un travail fait : {dit!r}")
    dire("**" not in dit, "et le repli est bien la version APLATIE, pas le markdown brut")


def un_tour_coupe_ne_se_raconte_pas_comme_un_tour_fini():
    print("\n=== flux : un tour interrompu le dit dans le prompt ===")
    j = FauxJournal(["J'ai commencé."], lignes=["modifie voix/a.py"],
                    arret="interrompu par l'utilisateur")
    _, client = parle(["J'ai commencé. "], j)
    prompt = client.prompts[0]
    dire("ne s'est pas termine normalement" in prompt,
         "le prompt porte l'avertissement d'arret")
    dire("interrompu par l'utilisateur" in prompt, "avec la raison exacte")
    dire("ne conclus pas a sa place" in prompt,
         "et la consigne de ne pas conclure : un echec raconte comme un succes est le pire cas")

    j2 = FauxJournal(["J'ai fini."], lignes=["modifie voix/a.py"])
    _, client2 = parle(["J'ai fini. "], j2)
    dire("ne s'est pas termine normalement" not in client2.prompts[0],
         "un tour normal n'herite pas de cet avertissement")


def l_historique_nourrit_le_tour_suivant():
    print("\n=== flux : le porte-parole se souvient de ce qu'il a deja dit ===")
    pp = PorteParole.__new__(PorteParole)
    pp.historique = []
    pp.tours_memoire = 2
    j = FauxJournal(["ok"], lignes=["modifie voix/a.py"], question="premiere demande")
    pp.client = FauxClient(["C'est fait. "])
    asyncio.run(pp.dire(j))
    dire(len(pp.historique) == 1 and pp.historique[0][0] == "premiere demande",
         "un tour dit entre dans l'historique")
    dire("premiere demande" in pp._historique_rendu(),
         "et il est rendu au modele au tour suivant")

    # La memoire est BORNEE : sans ca le prompt grossit a chaque tour, et on paie la relecture.
    for i in range(5):
        pp.client = FauxClient(["Fait. "])
        asyncio.run(pp.dire(FauxJournal(["ok"], lignes=["modifie voix/a.py"],
                                        question=f"demande {i}")))
    rendu = pp._historique_rendu()
    dire("premiere demande" not in rendu,
         "au-dela de la memoire, les vieux tours sortent : le prompt ne grossit pas sans fin")
    dire(rendu.count("Lui :") == 2, f"exactement deux tours sont rendus ({rendu.count('Lui :')})")

    vide = PorteParole.__new__(PorteParole)
    vide.historique = []
    vide.tours_memoire = 4
    dire("c'est le debut" in vide._historique_rendu(),
         "et le tout premier tour le dit plutot que de rendre du vide")


def main():
    les_faits_durs_se_verifient()
    aplatir_retire_ce_qui_ne_se_lit_pas()
    un_titre_ou_rien()
    une_phrase_fausse_tombe_les_autres_continuent()
    tout_rejeter_ne_rend_pas_muet()
    un_tour_coupe_ne_se_raconte_pas_comme_un_tour_fini()
    l_historique_nourrit_le_tour_suivant()
    print("\nTOUT VERT" if ok else "\nDES ECHECS")
    sys.exit(0 if ok else 1)


main()
