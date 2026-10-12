#!/usr/bin/env python3
"""Ce qui passe sans demander, ce qui demande, et ce qui bascule.

Trois choses se décident dans le worker, et chacune se paie cher quand elle rate :

- **la permission.** Ce qui modifie le monde — écrire, lancer une commande, pousser — passe
  par la voix. Ce qui ne fait que lire passe tout seul, sinon chaque recherche de fichier
  demanderait l'autorisation et on finirait par répondre « oui » sans écouter, ce qui est la
  vraie faille ;
- **la question.** `AskUserQuestion` est le seul outil qui consulte le rappel **quoi qu'il
  arrive**, même en bypassPermissions. Sans ce chemin, l'outil répondait « The user did not
  answer the questions » et Claude enchaînait en expliquant que la session était non
  interactive — alors qu'il y a quelqu'un, qui écoute, et dont c'est exactement le rôle ;
- **les bascules de modèle et d'effort.** Elles coûtent — un changement de modèle invalide le
  cache de prompt — et elles peuvent tuer un tour en cours si on les laisse passer au mauvais
  moment.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("VOIX_WORKDIR", str(Path(__file__).resolve().parent.parent))

import config
import worker as W
from worker import AUTO_ALLOW, Worker

ok = True


def dire(bon: bool, texte: str):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


class FauxTableau:
    def __init__(self):
        self.publies = []

    def publier(self, genre, **d):
        self.publies.append((genre, d))

    def dernier(self, genre):
        for g, d in reversed(self.publies):
            if g == genre:
                return d
        return None


class FauxClientSDK:
    def __init__(self, *a, **k):
        self.modeles = []
        self.interruptions = 0
        self.deconnecte = False
        self.lent = False

    async def set_model(self, m):
        self.modeles.append(m)

    async def connect(self):
        pass

    async def disconnect(self):
        self.deconnecte = True

    async def interrupt(self):
        self.interruptions += 1
        if self.lent:
            await asyncio.sleep(10)


class Ctx:
    def __init__(self, nom=None):
        self.display_name = nom


def neuf(**kw):
    w = Worker(tableau=FauxTableau(), **kw)
    w.client = FauxClientSDK()
    return w


# --- 1. la permission ----------------------------------------------------------------------

def lire_ne_demande_pas_la_permission():
    print("\n=== ce qui ne fait que lire passe tout seul ===")
    demandes = []

    async def permission(action, libelle):
        demandes.append(action)
        return True

    w = neuf(on_permission=permission)
    for outil in sorted(AUTO_ALLOW):
        asyncio.run(w._can_use_tool(outil, {"pattern": "x"}, Ctx()))
    dire(demandes == [],
         f"aucun des {len(AUTO_ALLOW)} outils de lecture ne demande quoi que ce soit")
    # Le raisonnement : demander pour chaque recherche de fichier ferait repondre « oui »
    # sans ecouter, et c'est ÇA, la faille — pas la lecture d'un fichier.
    dire("Read" in AUTO_ALLOW and "Grep" in AUTO_ALLOW and "Write" not in AUTO_ALLOW,
         "la liste separe bien lire et ecrire")


def modifier_le_monde_passe_par_la_voix():
    print("\n=== ce qui modifie demande, et le refus est net ===")
    demandes = []

    async def oui(action, libelle):
        demandes.append((action, libelle))
        return True

    w = neuf(on_permission=oui)
    r = asyncio.run(w._can_use_tool("Bash", {"command": "rm -rf /tmp/x",
                                             "description": "Supprime un dossier"},
                                    Ctx("Bash")))
    dire(type(r).__name__ == "PermissionResultAllow", "un oui autorise")
    dire(demandes and "Supprime un dossier" in demandes[0][0],
         f"la demande dit CE QUI VA SE PASSER, pas le nom de l'outil : {demandes[0][0]!r}")

    async def non(action, libelle):
        return False

    w = neuf(on_permission=non)
    r = asyncio.run(w._can_use_tool("Write", {"file_path": "/tmp/x"}, Ctx()))
    dire(type(r).__name__ == "PermissionResultDeny", "un non refuse")
    dire("Propose autre chose" in r.message,
         "et le refus invite a proposer autre chose plutot que de laisser Claude deviner")
    dire(r.interrupt is False,
         "sans interrompre le tour : refuser une action n'est pas annuler le travail")


def sans_voix_branchee_rien_ne_bloque():
    print("\n=== quand aucune voix n'écoute ===")
    w = neuf()                                   # on_permission=None
    r = asyncio.run(w._can_use_tool("Bash", {"command": "ls"}, Ctx()))
    dire(type(r).__name__ == "PermissionResultAllow",
         "tout passe : bloquer sur une question que personne n'entendra fige la session")


# --- 2. la question ------------------------------------------------------------------------

def une_question_de_claude_arrive_vraiment_a_quelqu_un():
    print("\n=== AskUserQuestion : il y a quelqu'un, et c'est son rôle ===")
    async def repond(questions):
        return "prends la deuxième"

    args = {"questions": [{"question": "laquelle ?", "options": ["a", "b"]}]}
    w = neuf(on_question=repond)
    r = asyncio.run(w._can_use_tool("AskUserQuestion", args, Ctx()))
    dire(r.updated_input["response"] == "prends la deuxième",
         "la reponse revient dans `response` — le champ que le CLI lit vraiment")
    dire(r.updated_input["questions"] == args["questions"],
         "et l'entree d'origine est preservee, sinon la validation du schema casse")

    # Pas de reponse : on laisse l'outil dire lui-meme qu'il n'en a pas eu, plutot que
    # d'inventer un « l'utilisateur n'a pas repondu » qui se lirait comme un refus.
    async def muet(questions):
        return None

    w = neuf(on_question=muet)
    r = asyncio.run(w._can_use_tool("AskUserQuestion", args, Ctx()))
    dire(r.updated_input is None,
         "sans reponse, rien n'est injecte : un silence n'est pas un refus")

    w = neuf()
    r = asyncio.run(w._can_use_tool("AskUserQuestion", args, Ctx()))
    dire(type(r).__name__ == "PermissionResultAllow" and r.updated_input is None,
         "et sans voix branchee, l'outil suit son cours normal")

    w = neuf(on_question=repond)
    r = asyncio.run(w._can_use_tool("AskUserQuestion", {"questions": []}, Ctx()))
    dire(r.updated_input is None, "une question vide ne derange personne")


# --- 3. les bascules ------------------------------------------------------------------------

def changer_de_modele_est_temporaire_par_defaut():
    print("\n=== passer sur un autre modèle ===")
    w = neuf()
    libelle = asyncio.run(w.changer_modele("haiku"))
    dire(libelle == config.MODELES["haiku"][1], "le libelle a dire est rendu")
    dire(w.client.modeles == [config.MODELES["haiku"][0]], "le client bascule pour de vrai")
    dire(w._rendre_apres_tour is True,
         "et le retour au modele de base est ARME : « pour cette tache » veut dire cette tache")
    dire(w._tableau.dernier("modele")["temporaire"] is True, "la page le dit aussi")

    # Le retour, a la fin du tour.
    asyncio.run(w._rendre_le_modele())
    dire(w.modele == w._modele_base, "a la fin du tour, le modele de base revient seul")
    dire(w._rendre_apres_tour is False, "et ne revient pas deux fois")
    avant = len(w.client.modeles)
    asyncio.run(w._rendre_le_modele())
    dire(len(w.client.modeles) == avant, "un second retour ne touche plus a rien")


def revenir_au_modele_de_base_n_arme_aucun_retour():
    print("\n=== demander explicitement le modèle de base ===")
    w = neuf()
    cle = config.cle_du_modele(config.WORKER_MODEL)
    asyncio.run(w.changer_modele(cle))
    dire(w._rendre_apres_tour is False,
         "rien a rendre : on y est deja, armer un retour serait un aller-retour inutile")


def un_modele_inconnu_ne_touche_a_rien():
    print("\n=== un modèle qui n'existe pas ===")
    w = neuf()
    dire(asyncio.run(w.changer_modele("inexistant")) == "", "rien a dire")
    dire(w.client.modeles == [], "et surtout : le client n'a pas bouge")


def l_effort_ne_se_change_jamais_en_pleine_tache():
    print("\n=== changer d'effort : les deux garde-fous ===")
    w = neuf()
    cle = next(c for c, (n, _) in config.EFFORTS.items() if n != w.effort)

    w.occupe = True
    dire(asyncio.run(w.changer_effort(cle)) == "",
         "occupe : on REFUSE — reconstruire le client tuerait le travail en cours sans rien dire")

    w = neuf()
    actuel = next(c for c, (n, _) in config.EFFORTS.items() if n == w.effort)
    dire(asyncio.run(w.changer_effort(actuel)) == config.EFFORTS[actuel][1],
         "le niveau deja en place rend son libelle sans rien reconstruire")
    dire(w.client.deconnecte is False, "et l'ancien client est intact : rien n'a bouge")

    dire(asyncio.run(w.changer_effort("inexistant")) == "", "un niveau inconnu ne fait rien")


def changer_d_effort_construit_avant_de_lacher():
    print("\n=== changer d'effort : construire, basculer, PUIS lâcher ===")
    w = neuf()
    ancien = w.client
    cle = next(c for c, (n, _) in config.EFFORTS.items() if n != w.effort)

    # Le SDK n'a pas de set_effort : le niveau est fige a la construction du client, donc il
    # faut en reconstruire un. On double la construction et la pompe — ce qui est teste ici
    # est l'ORDRE des trois gestes, pas ce que fait le SDK.
    vrai_client, vraie_tache = W.ClaudeSDKClient, W.asyncio.create_task
    pompes = []

    def fausse_tache(coro):
        coro.close()
        pompes.append(coro)
        return type("T", (), {"cancel": lambda s: None})()

    W.ClaudeSDKClient = FauxClientSDK
    W.asyncio.create_task = fausse_tache
    try:
        libelle = asyncio.run(w.changer_effort(cle))
    finally:
        W.ClaudeSDKClient, W.asyncio.create_task = vrai_client, vraie_tache

    dire(libelle == config.EFFORTS[cle][1], "le libelle du nouveau niveau est rendu")
    dire(w.effort == config.EFFORTS[cle][0], "le niveau est pose")
    dire(w.client is not ancien, "le client a bien ete remplace")
    dire(len(pompes) == 1, "une pompe neuve est lancee sur le nouveau client")
    # L'ordre inverse laissait une fenetre de deux secondes et demie ou self.client etait
    # deja mort et ou toute phrase arrivant la se perdait.
    dire(ancien.deconnecte is True, "et l'ancien n'est lache qu'APRES la bascule")
    dire(w._tableau.dernier("effort")["cle"] == cle, "la page apprend le nouveau niveau")


def arreter_rend_la_main_avant_de_demander():
    print("\n=== « arrête » quand la liaison est justement ce qui est cassé ===")
    w = neuf()
    w.occupe = True
    asyncio.run(w.interrompre())
    dire(w.occupe is False and w.client.interruptions == 1,
         "cas normal : l'etat est relache et l'arret demande")

    # Le cas qui a coute une conversation entiere : interrupt() ne repond jamais quand la
    # liaison est morte. L'ancien ordre laissait le bouton en attente pour toujours — or
    # c'est exactement dans ce cas-la qu'on appuie dessus.
    w = neuf()
    w.occupe = True
    w.client.lent = True
    asyncio.run(w.interrompre())
    dire(w.occupe is False, "liaison muette : la main est rendue QUAND MEME")
    dire(w.pompe_morte is True, "la pompe est declaree morte")
    dire("débloque" in (w._tableau.dernier("erreur") or {}).get("texte", ""),
         "et le message nomme le seul ordre qui marche encore")

    w = neuf()
    w.client = None
    asyncio.run(w.interrompre())
    dire(w.occupe is False, "sans client du tout, on rend la main sans se plaindre")


def main():
    lire_ne_demande_pas_la_permission()
    modifier_le_monde_passe_par_la_voix()
    sans_voix_branchee_rien_ne_bloque()
    une_question_de_claude_arrive_vraiment_a_quelqu_un()
    changer_de_modele_est_temporaire_par_defaut()
    revenir_au_modele_de_base_n_arme_aucun_retour()
    un_modele_inconnu_ne_touche_a_rien()
    l_effort_ne_se_change_jamais_en_pleine_tache()
    changer_d_effort_construit_avant_de_lacher()
    arreter_rend_la_main_avant_de_demander()
    print("\nTOUT VERT" if ok else "\nDES ECHECS")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
