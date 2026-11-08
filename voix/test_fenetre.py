#!/usr/bin/env python3
"""Ce qu'on dit au micro ne part pas tout seul : qui decide d'envoyer, et quand.

Pourquoi ces tests existent. Il y a eu trois generations ici. LiveKit decidait seul, puis
l'agent a tenu un decompte et publie son echeance, et on a empile par-dessus trois moyens de
le rattraper : le mode « retenir », le rattrapage d'un tour, la retenue d'office pendant que
Claude travaille. Empiler des garde-fous autour d'un comportement est la facon longue de dire
qu'on n'en veut pas — « je veux rarement que le message s'envoie tout seul ». Le decompte a
donc ete retire, et les trois rattrapages avec.

Ce qui doit rester vrai, et que ces tests verifient :

- une phrase dictee se DEPOSE dans la barre ; rien ne l'envoie qu'un appui sur « envoyer » ;
- reparler accumule sans rien envoyer et SANS perdre ce qui a deja ete dit ;
- un ordre local part IMMEDIATEMENT : faire attendre « arrete » est absurde. Avec la reponse
  a une permission et la reponse a une question, ce sont les SEULS envois sans appui ;
- le tour LiveKit en attente est JETE a chaque depot — sinon il repart au prochain envoi,
  ce qui faisait reapparaitre du texte deja parti ;
- une finale qui arrive apres le depart d'un tour appartient au tour precedent, pas au suivant.
"""

import asyncio
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

ok = ko = 0


def dire(vrai, quoi):
    global ok, ko
    if vrai:
        ok += 1
        print(f"  ok    {quoi}")
    else:
        ko += 1
        print(f"  ECHEC {quoi}")


class FausseSession:
    """Note ce qu'on lui demande, sans rien faire. C'est tout ce dont on a besoin : la
    question testee est « commet-on, et quand », pas « que fait LiveKit ensuite »."""

    def __init__(self, delai=5.0):
        self.commis = 0
        self.oublies = 0
        self._opts = type("O", (), {"endpointing": {"min_delay": delai}})()

    def commit_user_turn(self):
        self.commis += 1

    def clear_user_turn(self):
        self.oublies += 1


class FauxWorker:
    def __init__(self, occupe=False):
        self.occupe = occupe


class FauxTableau:
    def __init__(self):
        self.publies = []

    def publier(self, genre, **d):
        self.publies.append((genre, d))

    def genres(self):
        return [g for g, _ in self.publies]

    def dernier(self, genre):
        for g, d in reversed(self.publies):
            if g == genre:
                return d
        return None


def neuve(occupe=False, delai=0.15):
    """Une Voix minimale, avec un delai court pour que les tests restent rapides."""
    import agent as A
    v = A.Voix.__new__(A.Voix)              # sans __init__ : il monte un vrai Agent LiveKit
    v._amorcer_etat()                       # le meme amorçage que la vraie construction
    v.worker = FauxWorker(occupe)
    v.tableau = FauxTableau()
    v.quota = None
    v.conv = None
    v.permission_en_cours = None
    v.retenir = False
    v._retenir_ce_tour = False
    v._fenetre = None
    v._fin_fenetre = 0.0
    v._dit = ""
    v._retenu_en_attente = False
    v._session_directe = FausseSession(delai)
    return v


async def principal():
    import agent as A

    # --- rien a envoyer -----------------------------------------------------------------
    print("\n=== rien a envoyer, rien ne s'arme ===")
    v = neuve()
    v.ouvrir_fenetre()
    dire(v._fenetre is None and v.sess.commis == 0,
         "silence : aucune fenetre armee, aucun envoi")
    dire("ecoute" not in v.tableau.genres(),
         "et aucun decompte affiche — un decompte sur un tour vide n'annonce rien")

    # --- le cas courant -----------------------------------------------------------------
    print("\n=== le cas courant : ça se depose, ça ne part pas ===")
    v = neuve()
    v._dit = "ajoute un test sur la fenetre"
    v.ouvrir_fenetre()
    d = v.tableau.dernier("dictee")
    dire(d and d.get("texte") == "ajoute un test sur la fenetre",
         "la phrase va dans la barre, entiere")
    dire(v.sess.commis == 0, "et rien n'est envoye")
    dire(v.tableau.dernier("ecoute") is None,
         "aucun decompte n'est annonce : il n'y en a plus a annoncer")
    dire(v._fenetre is None, "aucune tache d'envoi differe ne reste en vol")
    dire(v.sess.oublies == 1,
         "le tour LiveKit est JETE — sinon il repart au prochain envoi, deja vu")
    dire(v._dit == "", "et le texte n'est pas garde en double cote agent")
    # Le point qui coute le plus cher s'il casse : attendre, et verifier que RIEN n'arrive.
    # L'ancien defaut etait exactement ça — on se relisait, et pendant ce temps c'etait parti.
    await asyncio.sleep(0.3)
    dire(v.sess.commis == 0, "et apres trois dixiemes de seconde, toujours rien")

    # --- reparler accumule sans rien perdre ----------------------------------------------
    print("\n=== reparler accumule, sans rien perdre ===")
    v = neuve()
    v._dit = "premiere partie"
    v.ouvrir_fenetre()
    v._retenu_en_attente = False             # ce que fait l'envoi depuis la barre
    v._dit = "premiere partie et la suite"
    v.ouvrir_fenetre()
    await asyncio.sleep(0.1)
    dire(v.sess.commis == 0, "reparler n'envoie rien, pas plus que se taire")
    dire(v.tableau.dernier("dictee")["texte"] == "premiere partie et la suite",
         "et la barre porte le texte complet, pas seulement la suite")

    # --- un ordre local ne patiente pas -------------------------------------------------
    print("\n=== un ordre local part tout de suite ===")
    # « arrête » seul n'est deliberement PAS un ordre : la detection exige un complement,
    # pour ne pas confisquer un mot aussi courant. « stop » et « arrête tout », si.
    for phrase in ("stop", "arrête tout", "coupe le micro", "chut"):
        v = neuve()
        v._dit = phrase
        v.ouvrir_fenetre()
        dire(v.sess.commis == 1 and v._fenetre is None,
             f"« {phrase} » : envoye immediatement, aucune attente")

    # Une phrase ordinaire ne doit PAS etre prise pour un ordre : le raccourci serait pire
    # que le probleme qu'il resout.
    v = neuve()
    # Ce cas est le vrai piege, et il n'est pas theorique : la detection considere
    # « arrêter » + « ça » comme un ordre d'arret. La phrase est donc bien detectee comme un
    # ordre — mais elle est LONGUE, donc elle passe par la fenetre et reste rattrapable.
    longue = "explique-moi pourquoi il faut arrêter de faire ça"
    from intentions import reconnaitre as _r
    dire(_r(longue)[0] == "arret",
         "la detection prend bien cette question pour un ordre (faux positif connu)")
    v._dit = longue
    v.ouvrir_fenetre()
    dire(v.sess.commis == 0 and v.tableau.dernier("dictee"),
         "mais elle est trop longue pour le raccourci : elle se depose, donc reste relisible")

    # Et la limite se verifie dans les deux sens : un ordre court garde son raccourci.
    v = neuve()
    v._dit = "arrête tout maintenant s'il te plaît"       # 6 mots : au-dela de la limite
    v.ouvrir_fenetre()
    dire(v.sess.commis == 0,
         "au-dela de cinq mots, meme un vrai ordre se depose — bornage assume")

    # --- une reponse a une permission non plus ------------------------------------------
    print("\n=== une reponse a une permission part tout de suite ===")
    v = neuve()
    v.permission_en_cours = asyncio.get_running_loop().create_future()
    v._dit = "oui vas-y"
    v.ouvrir_fenetre()
    dire(v.sess.commis == 1, "on attend une permission : la reponse ne patiente pas")
    v.permission_en_cours.cancel()

    # --- la retenue n'a plus de condition ------------------------------------------------
    # Ce bloc remplace celui des « trois raisons ». Il n'y a plus de condition a tester, et
    # c'est precisement le gain : aucun etat de l'agent, aucun reglage, aucun moment ne peut
    # rendre l'envoi automatique. L'absence de condition se teste en prenant les trois etats
    # qui, chacun, declenchaient AUTREFOIS un comportement different.
    print("\n=== aucun etat ne rend l'envoi automatique ===")
    etats = [("au repos", lambda v: None),
             ("Claude au travail", lambda v: setattr(v.worker, "occupe", True)),
             ("micro coupe puis rouvert", lambda v: setattr(v, "_dictee_ouverte", True))]
    for nom, armer in etats:
        v = neuve()
        armer(v)
        v._dit = "une phrase a relire"
        v.ouvrir_fenetre()
        dire(v.sess.commis == 0, f"[{nom}] rien n'est envoye")
        dire(v.sess.oublies == 1,
             f"[{nom}] le tour en attente est JETE, il ne repartira pas plus tard")
        d = v.tableau.dernier("dictee")
        dire(d and d.get("texte") == "une phrase a relire",
             f"[{nom}] le texte va dans la barre, entier")
        dire(v._dit == "", f"[{nom}] et il n'est pas garde en double cote agent")

    # --- envoyer : le seul geste qui commet -----------------------------------------------
    print("\n=== envoyer, et c'est le seul chemin ===")
    v = neuve()
    v._dit = "j'ai fini"
    v._envoyer_maintenant("bouton")
    dire(v.sess.commis == 1 and v._fenetre is None,
         "le bouton envoie, et c'est le seul endroit du code qui commet un tour")
    await asyncio.sleep(0.05)
    dire(v.sess.commis == 1, "et rien derriere n'envoie une seconde fois")

    # --- une session qui refuse ne casse rien -------------------------------------------
    print("\n=== robustesse ===")
    v = neuve()
    def refuse():
        raise RuntimeError("aucun tour en cours")
    v.sess.commit_user_turn = refuse
    v._dit = "quelque chose"
    v._envoyer_maintenant("test")
    dire(any(g == "log" for g in v.tableau.genres()),
         "un envoi impossible se DIT au lieu de laisser un clic sans effet")

    # Fermer deux fois de suite (deux coupures de micro) ne doit pas lever.
    v = neuve()
    v.fermer_fenetre()
    v.fermer_fenetre()
    dire(True, "fermer une fenetre inexistante est sans effet et sans erreur")

    # --- la retenue est collante ---------------------------------------------------------
    # Le defaut qu'elle corrige, precisement : on dicte, c'est retenu (la barre garde la
    # phrase), on reparle, ce second tour PART, et la page vide la barre. La premiere phrase
    # disparait sans avoir jamais ete envoyee ni signalee. C'est la version exacte du
    # « texte deja envoye qui reapparait, et parfois seulement une partie ».
    print("\n=== un depot par-dessus un autre le DIT ===")
    v = neuve()
    v._dit = "la premiere phrase"
    v.ouvrir_fenetre()
    dire(v._retenu_en_attente, "un depot arme le drapeau d'attente")
    dire(v.tableau.dernier("dictee")["raison"] == "toujours",
         "le premier depot n'a pas de raison particuliere a donner")

    v._dit = "la seconde phrase"
    v.ouvrir_fenetre()
    dire(v.sess.commis == 0,
         "le tour suivant ne part PAS par-dessus : la premiere phrase n'est pas perdue")
    dire(v.tableau.dernier("dictee")["raison"] == "attente",
         "et la page peut dire « ajoute a ce qui attendait deja » — le seul cas ou l'on "
         "peut croire avoir perdu ce qu'on venait de dire")

    # Ce qui libere : la barre part, ou la barre est videe.
    v._retenu_en_attente = False       # ce que fait la commande « texte »
    v._dit = "la troisieme"
    v.ouvrir_fenetre()
    dire(v.tableau.dernier("dictee")["raison"] == "toujours",
         "la barre partie, le depot suivant redevient un depot ordinaire")

    # --- couper le micro vaut « j'ai fini » ----------------------------------------------
    print("\n=== couper le micro tranche le tour en cours ===")
    v = neuve()
    v.micro_voulu = False
    v.inscription = None
    applique = []
    v.sess.input = type("I", (), {"set_audio_enabled": lambda s, x: applique.append(x)})()
    v._dit = "une phrase interrompue par la coupure"
    v.appliquer_micro(publier=False)
    d = v.tableau.dernier("dictee")
    dire(d and d["texte"] == "une phrase interrompue par la coupure",
         "le texte deja transcrit n'est pas abandonne : il se depose dans la barre")
    dire(v.sess.commis == 0, "mais couper le micro ne vaut pas « envoie » : on relira")

    # Et sans rien de transcrit, couper le micro ne declenche rien du tout.
    v = neuve()
    v.micro_voulu = False
    v.inscription = None
    v.sess.input = type("I", (), {"set_audio_enabled": lambda s, x: None})()
    v.appliquer_micro(publier=False)
    dire(v.tableau.dernier("dictee") is None and v.sess.commis == 0,
         "micro coupe sans rien avoir dit : aucun tour vide n'est fabrique")

    # --- le talon ne peut pas deriver de la vraie construction ---------------------------
    # Ce qui a casse deux suites : appliquer_micro a commence a lire self._dit, et les talons
    # construits par __new__ ne l'avaient pas. L'AttributeError tombait loin de sa cause.
    # _amorcer_etat existe pour ça ; encore faut-il que __init__ n'ajoute rien a cote.
    print("\n=== le talon reste complet ===")
    import inspect
    src_init = inspect.getsource(A.Voix.__init__)
    src_amorce = inspect.getsource(A.Voix._amorcer_etat)
    poses_init = set(re.findall(r"self\.(_?\w+)\s*(?::[^=]+)?=", src_init))
    poses_amorce = set(re.findall(r"self\.(_?\w+)\s*(?::[^=]+)?=", src_amorce))
    # Les dependances injectees sont posees par __init__ et n'ont rien a faire dans l'amorçage.
    injectees = {"worker", "porte_parole", "tableau", "quota", "conv"}
    hors = sorted(poses_init - poses_amorce - injectees)
    dire(not hors,
         "tout etat interne passe par _amorcer_etat"
         + (f" — POSE AILLEURS : {hors}" if hors else ""))
    dire(len(poses_amorce) >= 8,
         f"et l'amorçage couvre bien l'etat interne ({len(poses_amorce)} champs)")

    # --- LiveKit accepte reellement le mode manuel ---------------------------------------
    # Tout ce qui precede teste NOTRE decideur. Encore faut-il que LiveKit se taise vraiment :
    # si « manual » etait ignore, il commettrait les tours de son cote EN PLUS des notres, et
    # le symptome serait le pire — des messages partant deux fois, ou avant le decompte. Un
    # nom d'option qui change entre deux versions ne leve aucune erreur, il est juste ignore.
    print("\n=== LiveKit accepte le mode manuel ===")
    try:
        import config
        from livekit.agents import AgentSession
        from livekit.agents.voice.agent_session import (EndpointingOptions,
                                                        TurnHandlingOptions)
        from livekit.plugins import silero
        vraie = AgentSession(
            vad=silero.VAD.load(),
            turn_handling=TurnHandlingOptions(
                turn_detection="manual",
                endpointing=EndpointingOptions(mode="fixed", min_delay=config.ECOUTE_MIN,
                                               max_delay=config.plafond_ecoute())))
        th = dict(vraie._opts.turn_handling)
        dire(th.get("turn_detection") == "manual",
             f"l'option est retenue telle quelle : {th.get('turn_detection')!r}")
        ep = dict(vraie._opts.endpointing)
        dire(ep.get("min_delay") == config.ECOUTE_MIN,
             f"et l'endpointing reste lisible — c'est la source du delai affiche ({ep.get('min_delay')} s)")
        dire(callable(vraie.commit_user_turn) and callable(vraie.clear_user_turn),
             "commit_user_turn et clear_user_turn existent : les deux gestes du mode manuel")
    except Exception as exc:
        dire(False, f"impossible de construire une vraie session : {type(exc).__name__}: {exc}")

    # --- une transcription en retard n'appartient pas au tour suivant --------------------
    # Le defaut, rapporte tel quel : « j'envoie un message, il reapparait directement dans ma
    # barre et il est affiche dans les logs en retenue alors que pas du tout ».
    #
    # L'enchainement exact : on parle, le tour part, Claude se met au travail — puis le moteur
    # rend une DERNIERE finale pour la queue de l'audio (tous les moteurs segmentent, c'est
    # mesure sur AssemblyAI comme sur Deepgram). Sans reperage de fin d'enonce, cette finale
    # atterrit dans le tour suivant ; et comme Claude travaille, la retenue d'office la depose
    # dans la barre. On voit donc reapparaitre un morceau du message qu'on vient d'envoyer,
    # etiquete « retenu » alors qu'on n'a rien retenu.
    #
    # La page se protegeait deja de ça pour l'affichage — mais l'agent republiait un « dictee »
    # qu'elle obeit, court-circuitant sa propre protection. Le garde-fou manquait EN AMONT.
    print("\n=== une transcription en retard ne revient pas ===")

    # Le VRAI chemin de l'agent, pas une copie. Une copie aurait continue de passer apres
    # qu'on ait retire le garde-fou du code de production — c'est le genre de test qui rassure
    # sans rien prouver.
    def transcrire(v, texte, final=True):
        return v.noter_transcription(texte, final)

    v = neuve()
    v._dictee_ouverte = True                    # on a commence a parler
    dire(transcrire(v, "renomme la variable qui gère le silence"),
         "pendant l'enonce, une finale est bien accumulee")
    v.ouvrir_fenetre()
    v._envoyer_maintenant("bouton")
    dire(v.sess.commis == 1, "le tour part")
    dire(not v._dictee_ouverte, "et l'enonce est CLOS : c'est ce reperage qui manquait")

    # La finale en retard arrive maintenant, et Claude s'est mis au travail entre-temps.
    v.worker.occupe = True
    garde = transcrire(v, "dans config")
    dire(garde is False, "la finale tardive est refusee")
    dire(v._dit == "", "elle ne s'accumule pas dans le tour suivant")

    # Et la preuve par le comportement : rien ne se depose dans la barre.
    avant = len([g for g, _ in v.tableau.publies if g == "dictee"])
    v.ouvrir_fenetre()
    apres = len([g for g, _ in v.tableau.publies if g == "dictee"])
    dire(apres == avant,
         "aucun « dictee » publie : le message envoye ne reapparait pas dans la barre")
    dire(v.sess.commis == 1, "et aucun second tour n'est parti non plus")

    # Reparler rouvre l'enonce : la protection ne doit pas rendre l'agent sourd.
    v._dictee_ouverte = True                    # ce que fait « speaking »
    dire(transcrire(v, "ajoute un test dessus"),
         "reparler rouvre l'enonce : on n'est pas devenu sourd")
    dire(v._dit == "ajoute un test dessus",
         f"et le nouveau tour part d'un texte PROPRE : {v._dit!r}")

    # Meme chose apres un DEPOT : le tour est consomme, l'enonce doit se fermer.
    v2 = neuve()
    v2._dictee_ouverte = True
    v2._dit = "une phrase a relire"
    v2.ouvrir_fenetre()
    dire(not v2._dictee_ouverte,
         "apres un depot aussi, l'enonce se ferme — le tour est consomme")
    dire(transcrire(v2, "queue de phrase") is False,
         "donc une finale tardive ne s'ajoute pas au texte deja depose dans la barre")

    print(f"\n  {ok} ok, {ko} echec(s)")
    return 1 if ko else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
