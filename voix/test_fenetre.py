#!/usr/bin/env python3
"""La fenetre avant envoi : qui decide, quand, et ce qui la remet a zero.

Pourquoi ces tests existent. Avant, LiveKit decidait de l'envoi et la page affichait un
compte a rebours *estime* a partir des memes reglages : deux horloges pour une seule
decision. Quand elles divergeaient on lisait « encore 4 s » et le message etait deja parti,
donc le bouton « retenir » arrivait apres coup — « la phase de retenir, des fois je ne peux
pas la faire ». Maintenant l'agent tient la fenetre et publie son echeance exacte.

Ce qui doit rester vrai, et que ces tests verifient :

- rien ne part sans qu'un envoi soit reellement demande, a l'echeance ou au bouton ;
- reparler REMET la fenetre a zero sans rien envoyer et SANS perdre ce qui a deja ete dit
  (c'est ce qui rend une pause de trois a cinq secondes possible) ;
- un ordre local part IMMEDIATEMENT : faire attendre « arrete » cinq secondes est absurde ;
- une raison de retenir empeche l'envoi, et le tour en attente est JETE — sinon il repart au
  prochain envoi, ce qui faisait reapparaitre du texte deja parti ;
- « retenir » clique PENDANT le decompte fonctionne, y compris a la derniere seconde.
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
    print("\n=== le cas courant : on attend, puis on envoie ===")
    v = neuve()
    v._dit = "ajoute un test sur la fenetre"
    v.ouvrir_fenetre()
    ecoute = v.tableau.dernier("ecoute")
    dire(ecoute and ecoute.get("actif") is True, "le decompte est annonce")
    dire(ecoute.get("fin") and ecoute["fin"] > time.time() * 1000,
         "avec une echeance en horloge murale, dans le futur")
    dire(abs(ecoute["fin"] / 1000 - time.time() - 0.15) < 0.1,
         "et cette echeance correspond au delai reglé, pas a une constante")
    dire(v.sess.commis == 0, "rien n'est parti pendant l'attente")
    await asyncio.sleep(0.3)
    dire(v.sess.commis == 1, "a l'echeance, le tour part une fois et une seule")
    dire(v._dit == "", "et ce qui a ete dit est consomme, pas laisse pour le tour suivant")

    # --- reparler remet a zero sans perdre le debut --------------------------------------
    print("\n=== reparler prolonge, sans rien perdre ===")
    v = neuve()
    v._dit = "premiere partie"
    v.ouvrir_fenetre()
    await asyncio.sleep(0.05)
    v.fermer_fenetre(publier=False)          # ce que fait « speaking »
    v._dit = (v._dit + " et la suite").strip()
    v.ouvrir_fenetre()
    await asyncio.sleep(0.08)
    dire(v.sess.commis == 0,
         "l'ancienne echeance ne se declenche pas apres avoir reparle")
    await asyncio.sleep(0.15)
    dire(v.sess.commis == 1, "seule la nouvelle echeance envoie")

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
    dire(v.sess.commis == 0 and v._fenetre is not None,
         "mais elle est trop longue pour le raccourci : elle attend, donc reste rattrapable")
    v.fermer_fenetre()

    # Et la limite se verifie dans les deux sens : un ordre court garde son raccourci.
    v = neuve()
    v._dit = "arrête tout maintenant s'il te plaît"       # 6 mots : au-dela de la limite
    v.ouvrir_fenetre()
    dire(v.sess.commis == 0,
         "au-dela de cinq mots, meme un vrai ordre passe par la fenetre — bornage assume")
    v.fermer_fenetre()

    # --- une reponse a une permission non plus ------------------------------------------
    print("\n=== une reponse a une permission part tout de suite ===")
    v = neuve()
    v.permission_en_cours = asyncio.get_running_loop().create_future()
    v._dit = "oui vas-y"
    v.ouvrir_fenetre()
    dire(v.sess.commis == 1, "on attend une permission : la reponse ne patiente pas")
    v.permission_en_cours.cancel()

    # --- les trois raisons de retenir ---------------------------------------------------
    print("\n=== retenir : les trois raisons, et ce qu'elles font ===")
    cas = [("mode", lambda v: setattr(v, "retenir", True)),
           ("tour", lambda v: setattr(v, "_retenir_ce_tour", True)),
           ("occupe", lambda v: setattr(v.worker, "occupe", True))]
    for attendue, armer in cas:
        v = neuve()
        armer(v)
        dire(v.pourquoi_retenir() == attendue,
             f"la raison est nommee « {attendue} » — le tableau peut l'expliquer")
        v._dit = "une phrase a relire"
        v.ouvrir_fenetre()
        dire(v.sess.commis == 0, f"[{attendue}] rien n'est envoye")
        dire(v.sess.oublies == 1,
             f"[{attendue}] le tour en attente est JETE, il ne repartira pas plus tard")
        d = v.tableau.dernier("dictee")
        dire(d and d.get("raison") == attendue and d.get("texte") == "une phrase a relire",
             f"[{attendue}] le texte va dans la barre, avec sa raison")
        dire(v._dit == "", f"[{attendue}] et il n'est pas garde en double cote agent")

    # « tour » gagne sur « mode » : c'est un geste volontaire et immediat.
    v = neuve()
    v.retenir, v._retenir_ce_tour = True, True
    dire(v.pourquoi_retenir() == "tour", "le rattrapage d'un tour gagne sur le mode global")

    # --- retenir PENDANT le decompte, jusqu'a la derniere seconde ------------------------
    print("\n=== retenir clique pendant le decompte ===")
    v = neuve(delai=0.25)
    v._dit = "phrase a rattraper"
    v.ouvrir_fenetre()
    await asyncio.sleep(0.2)                  # presque a l'echeance
    v._retenir_ce_tour = True                 # ce que fait le bouton
    await asyncio.sleep(0.2)
    dire(v.sess.commis == 0,
         "arme a la derniere seconde : la raison est relue A L'ECHEANCE, donc ça marche")
    dire(v.sess.oublies == 1, "et le tour est jete la aussi")

    # --- le bouton envoyer --------------------------------------------------------------
    print("\n=== envoyer sans attendre ===")
    v = neuve(delai=5.0)
    v._dit = "j'ai fini"
    v.ouvrir_fenetre()
    v._envoyer_maintenant("bouton")
    dire(v.sess.commis == 1 and v._fenetre is None,
         "le bouton coupe l'attente et envoie, par le meme chemin que l'echeance")
    await asyncio.sleep(0.05)
    dire(v.sess.commis == 1, "et la fenetre annulee n'envoie pas une seconde fois")

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
    print("\n=== la retenue est collante ===")
    v = neuve()
    v.worker.occupe = True
    v._dit = "la premiere phrase"
    v.ouvrir_fenetre()
    dire(v._retenu_en_attente, "une retenue arme le drapeau d'attente")

    v.worker.occupe = False            # Claude a fini : plus de retenue d'office
    dire(v.pourquoi_retenir() == "attente",
         "mais un texte attend dans la barre, donc on retient encore — et on le dit")
    v._dit = "la seconde phrase"
    v.ouvrir_fenetre()
    dire(v.sess.commis == 0,
         "le tour suivant ne part PAS par-dessus : la premiere phrase n'est pas perdue")
    d = v.tableau.dernier("dictee")
    dire(d and d["raison"] == "attente",
         "et la raison affichee est « attente », pas « Claude travaille » qui serait faux")

    # Ce qui libere : la barre part, ou la barre est videe.
    v._retenu_en_attente = False       # ce que fait la commande « texte »
    v._dit = "la troisieme"
    v.ouvrir_fenetre()
    dire(v._fenetre is not None, "la barre partie, le tour suivant repart normalement")
    v.fermer_fenetre()

    # --- couper le micro vaut « j'ai fini » ----------------------------------------------
    print("\n=== couper le micro tranche le tour en cours ===")
    v = neuve()
    v.micro_voulu = False
    v.inscription = None
    applique = []
    v.sess.input = type("I", (), {"set_audio_enabled": lambda s, x: applique.append(x)})()
    v._dit = "une phrase interrompue par la coupure"
    v.appliquer_micro(publier=False)
    dire(v._fenetre is not None or v.sess.commis == 1,
         "le texte deja transcrit n'est pas abandonne : la fenetre s'ouvre ou le tour part")
    v.fermer_fenetre()

    # Et sans rien de transcrit, couper le micro ne declenche rien du tout.
    v = neuve()
    v.micro_voulu = False
    v.inscription = None
    v.sess.input = type("I", (), {"set_audio_enabled": lambda s, x: None})()
    v.appliquer_micro(publier=False)
    dire(v._fenetre is None and v.sess.commis == 0,
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

    print(f"\n  {ok} ok, {ko} echec(s)")
    return 1 if ko else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
