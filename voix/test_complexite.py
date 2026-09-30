"""L'effort qu'une demande merite — et surtout, celui qu'elle ne merite pas.

Ce que ces tests protegent : l'effort etait fige haut pour toute une session, donc « corrige
la faute de frappe » depensait autant que « diagnostique ce bug ». Le classement est local et
heuristique, donc faillible ; ces cas fixent la ou il a le droit de se tromper.

La regle d'or, verifiee ici : le doute rend « moyen ». Un classement trop bas se rattrape en
redemandant ; un classement trop haut ne se rattrape pas, il se paie.
"""
import os, sys

sys.path.insert(0, os.path.dirname(__file__))
from complexite import LIBELLES, JAMAIS_AUTO, NIVEAUX, evaluer

ok = True


def dire(bon: bool, texte: str):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


def attendu(niveau: str, demande: str, propos: str = ""):
    c = evaluer(demande)
    dire(c.niveau == niveau,
         f"{propos or demande[:52]} → {c.libelle}" +
         ("" if c.niveau == niveau else f" (attendu {LIBELLES[niveau]})"))


def le_mecanique_ne_coute_rien():
    print("\n=== ce qui n'a rien a chercher descend ===")
    attendu("low", "renomme la variable truc en machin")
    attendu("low", "corrige la faute de frappe dans le README")
    attendu("low", "ça marche ?")
    attendu("low", "combien de fichiers Python dans voix ?")
    attendu("low", "relance les tests", "une consigne de six mots")
    attendu("low", "ajoute un commentaire au-dessus de cette fonction")


def le_defaut_repond_a_presque_tout():
    print("\n=== entre les deux, « moyen » — et c'est bien le defaut ===")
    attendu("medium", "ajoute un bouton pour couper le micro dans le front")
    attendu("medium", "écris un test pour la fonction de résumé des actions")
    attendu("medium", "")
    dire(evaluer("").pourquoi == "rien à évaluer", "une demande vide ne casse rien")


def chercher_ou_toucher_large_fait_monter():
    print("\n=== ce qui demande de chercher, ou de toucher large, monte ===")
    attendu("high", "pourquoi le bouton écouter n'apparaît pas sur certains messages ?")
    attendu("high", "refactor tout le module de reconnexion")
    attendu("high", "quelle approche pour servir la page en https ?")
    attendu("high", "le micro ne marche plus sur mobile")
    attendu("high", "corrige le titre et aussi la couleur", "deux demandes")


def le_plus_cher_se_merite():
    print("\n=== « tres eleve » se merite, et ne s'attrape pas par hasard ===")
    attendu("xhigh", "creuse le sujet en profondeur et assure-toi que tout fonctionne")
    attendu("xhigh", "prends tout le temps qu'il faut pour vérifier chaque cas")
    attendu("xhigh", "le speech to text ne marche pas, diagnostique et corrige tous les cas")
    attendu("xhigh",
            "premièrement le titre, deuxièmement les couleurs, et aussi le bouton suivre",
            "quatre demandes en un message")
    # Le piege inverse : une demande longue n'est pas une demande difficile.
    attendu("medium",
            "ajoute dans le front un bouton qui permet de relancer la lecture du dernier "
            "message reçu, avec une icône de haut-parleur comme les autres boutons de la barre",
            "longue mais simple : une seule chose a faire")


def ultracode_ne_se_choisit_jamais_seul():
    print("\n=== ce que l'automatique n'a pas le droit de faire ===")
    dire("ultracode" in JAMAIS_AUTO, "« ultracode » est explicitement hors de portee")
    dire("ultracode" not in NIVEAUX, "et il ne figure pas parmi les niveaux choisissables")
    durs = ["creuse en profondeur tous les angles", "refactor complet et diagnostic",
            "prends toutes les ressources qu'il faut, sois exhaustif et minutieux"]
    dire(all(evaluer(d).niveau != "ultracode" for d in durs),
         "meme les demandes les plus lourdes ne le declenchent pas — il se demande a la main")
    dire(all(evaluer(d).niveau in NIVEAUX for d in durs + ["x", "", "renomme ça"]),
         "et tout classement rend un niveau que la configuration connait")


def chaque_choix_sait_se_justifier():
    print("\n=== un niveau sans raison est un caprice ===")
    for d in ["renomme la variable", "pourquoi ça plante ?", "ajoute un bouton",
              "creuse en profondeur"]:
        c = evaluer(d)
        dire(bool(c.pourquoi) and len(c.pourquoi) < 60,
             f"« {d[:32]} » : {c.pourquoi}")


def main():
    le_mecanique_ne_coute_rien()
    le_defaut_repond_a_presque_tout()
    chercher_ou_toucher_large_fait_monter()
    le_plus_cher_se_merite()
    ultracode_ne_se_choisit_jamais_seul()
    chaque_choix_sait_se_justifier()
    print("\nTOUT VERT" if ok else "\nDES ECHECS")
    sys.exit(0 if ok else 1)


main()
