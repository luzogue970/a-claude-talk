#!/usr/bin/env python3
"""Les ordres locaux : ce qu'on dit à la voix et qui ne part JAMAIS chez Claude.

« arrête », « chut », « coupe le micro », « où on en est », « répète », « quota ». Ces
phrases-là se traitent sur place. Elles ont deux propriétés que rien d'autre n'a dans cette
application :

- elles doivent répondre **même quand tout le reste est bloqué** — c'est tout leur intérêt.
  « débloque » n'a de sens que le jour où la liaison avec Claude Code est morte ;
- elles sont la seule interface quand l'écran ne répond pas. Une réponse muette ou fausse
  ici ne se rattrape par aucun bouton.

Chaque vérification ci-dessous dit donc ce qu'on ENTEND, pas ce que la fonction renvoie.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("VOIX_WORKDIR", str(Path(__file__).resolve().parent.parent))

import config

ok = True


def dire(bon: bool, texte: str):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


# --- les doublures ------------------------------------------------------------------------

class FauxJournalW:
    def resume_court(self):
        return "je lis trois fichiers"


class FauxWorker:
    def __init__(self, occupe=False, poids_="38 % du contexte"):
        self.occupe = occupe
        self.journal = FauxJournalW()
        self.interrompu = 0
        self.modeles = []
        self._poids = poids_
        self.reponse_debloque = "liaison relancée."
        self.reponse_compacte = "conversation compactée."
        self.modele_connu = True

    def poids(self):
        return self._poids

    async def interrompre(self):
        self.interrompu += 1

    async def changer_modele(self, cle, temporaire=False):
        self.modeles.append((cle, temporaire))
        return config.MODELES[cle][1] if self.modele_connu and cle in config.MODELES else None

    async def debloquer(self):
        return self.reponse_debloque

    async def compacter(self):
        return self.reponse_compacte


class FausseEntree:
    def __init__(self):
        self.audio = None

    def set_audio_enabled(self, v):
        self.audio = v


class FausseSession:
    def __init__(self, delai=5.0):
        self.commis = 0
        self.oublies = 0
        self.interruptions = 0
        self.dits = []
        self.input = FausseEntree()
        self._opts = type("O", (), {"endpointing": {"min_delay": delai}})()

    def commit_user_turn(self):
        self.commis += 1

    def clear_user_turn(self):
        self.oublies += 1

    def interrupt(self):
        self.interruptions += 1

    async def say(self, source, allow_interruptions=True):
        self.dits.append(source)
        return FaussePoignee()


class FaussePoignee:
    def __init__(self):
        self._fini = False
        self.coupee = 0
        self._rappels = []

    def done(self):
        return self._fini

    def interrupt(self):
        self.coupee += 1

    def add_done_callback(self, f):
        self._rappels.append(f)

    def terminer(self):
        self._fini = True
        for f in self._rappels:
            f(self)


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


class FauxQuota:
    def resume_parle(self):
        return "la fenêtre de session est à 31 pour cent."


def neuve(occupe=False, quota=None, poids_="38 % du contexte"):
    import agent as A
    v = A.Voix.__new__(A.Voix)
    v._amorcer_etat()
    v.worker = FauxWorker(occupe, poids_)
    v.tableau = FauxTableau()
    v.quota = quota
    v.conv = None
    v.inscription = None
    v.porte_parole = None
    v._session_directe = FausseSession()
    return v


def ex(v, intention, texte=""):
    return asyncio.run(v._executer(intention, texte))


# --- 1. arrêter ---------------------------------------------------------------------------

def arreter_arrete_vraiment():
    print("\n=== « arrête » ===")
    v = neuve(occupe=True)
    dire(ex(v, "arret") == "ok, j'arrête.", "la confirmation est dite")
    dire(v.worker.interrompu == 1, "et le travail est reellement interrompu")
    # Le piege repare : session.interrupt() ici annulait la phrase que l'ordre vient de
    # produire — « arrete tout » finissait muet, donc on ne savait pas s'il avait porte.
    dire(v.sess.interruptions == 0,
         "la session n'est PAS coupee : elle annulerait la confirmation qu'on vient de dire")


def arreter_ferme_aussi_une_question_en_vol():
    print("\n=== « arrête » pendant une question de Claude ===")
    async def essai():
        v = neuve()
        v.question_en_cours = asyncio.get_running_loop().create_future()
        reponse = await v._executer("arret")
        return v, reponse
    v, reponse = asyncio.run(essai())
    dire(reponse == "d'accord, je laisse la question de côté.",
         "la reponse parle de la QUESTION, pas du travail")
    dire(v.worker.interrompu == 0,
         "et le travail n'est pas interrompu : on a refuse de repondre, pas demande l'arret")
    # Sans cette porte, une question sans delai restait en vol pour toujours : plus rien
    # n'attendait d'echeance pour la terminer, donc le tour ne se finissait jamais.
    dire(v.question_en_cours.done() and v.question_en_cours.result() is None,
         "la question rend None — l'outil dira qu'il n'a pas eu de reponse, et Claude le SAURA")


def se_taire_n_est_pas_s_arreter():
    print("\n=== « chut » : taire la voix sans toucher au travail ===")
    v = neuve(occupe=True)
    dire(ex(v, "silence") is None,
         "rien n'est dit en retour : confirmer a voix haute annulerait l'ordre")
    dire(v.sess.interruptions == 1, "la parole en cours est coupee")
    dire(v.worker.interrompu == 0, "mais le travail continue : ce sont deux demandes differentes")


# --- 2. le micro --------------------------------------------------------------------------

def couper_le_micro_dit_toujours_comment_revenir():
    print("\n=== « coupe le micro » ===")
    v = neuve(occupe=True)
    message = ex(v, "micro")
    dire(v.micro_voulu is False and v.sess.input.audio is False,
         "l'entree audio est reellement coupee")
    dire("la réflexion continue" in message,
         f"et on dit que le travail, lui, continue : {message!r}")
    dire("Touche m ou le bouton" in message,
         "le chemin du retour est TOUJOURS nomme : micro ferme, on ne peut plus le demander")
    dire(v.sess.interruptions == 0,
         "couper l'ecoute ne coupe pas la parole en cours — c'est ce qui coupait la phrase")

    v = neuve(occupe=False)
    dire("j'attends tes instructions" in ex(v, "micro"),
         "au repos, le message le dit autrement")
    dire(v.tableau.dernier("micro")["actif"] is False, "et la page l'apprend")


def le_micro_depend_du_bail_autant_que_du_voeu():
    print("\n=== le micro : voulu ET autorise ===")
    v = neuve()
    v.micro_voulu = True
    v.inscription = type("I", (), {"detient_micro": lambda s: False})()
    dire(v.appliquer_micro() is False,
         "vouloir le micro ne suffit pas : une autre conversation tient le bail")
    dire(v.tableau.dernier("micro")["voulu"] is True,
         "et la page voit la difference entre « je le veux » et « je l'ai »")
    v.inscription = type("I", (), {"detient_micro": lambda s: True})()
    dire(v.appliquer_micro() is True, "bail rendu, le micro suit sans qu'on redemande rien")


# --- 3. savoir où on en est ---------------------------------------------------------------

def le_statut_dit_le_travail_ET_le_poids():
    print("\n=== « où on en est » ===")
    v = neuve(occupe=True)
    dit = ex(v, "statut")
    dire("je lis trois fichiers" in dit, "ce qui est en cours est dit")
    # Le poids explique qu'une question banale mette trente secondes : on ne peut pas le
    # deviner en regardant l'ecran.
    dire("38 % du contexte" in dit, f"et le poids de la conversation avec : {dit!r}")

    v = neuve(occupe=False)
    dire(ex(v, "statut").startswith("il n'y a rien en cours"), "au repos, on le dit")

    v = neuve(occupe=False, poids_="")
    dire(ex(v, "statut") == "il n'y a rien en cours.",
         "et sans poids connu la phrase reste propre, sans tiret orphelin")


def repeter_ne_repaye_pas_une_synthese():
    print("\n=== « répète » ===")
    v = neuve()
    dire(ex(v, "repete") == "je ne t'ai encore rien dit.",
         "avant tout debrief, on le dit plutot que de se taire")
    v._dernier_debrief = "j'ai corrigé le micro."
    dire(ex(v, "repete") == "j'ai corrigé le micro.",
         "ensuite c'est le texte GARDE qui ressort : une reecriture serait payee deux fois")


def le_quota_se_dit_ou_s_avoue_absent():
    print("\n=== « quota » ===")
    dire("31 pour cent" in ex(neuve(quota=FauxQuota()), "quota"), "le quota est dit a voix haute")
    dire(ex(neuve(quota=None), "quota") == "je n'ai pas le quota.",
         "et son absence s'avoue : un silence passerait pour une panne de micro")


# --- 4. les ordres qui servent quand plus rien ne marche ----------------------------------

def debloquer_et_compacter_repondent_toujours():
    print("\n=== « débloque » et « compacte » : les ordres de dernier recours ===")
    v = neuve()
    dire(ex(v, "debloque") == "liaison relancée.", "le message du worker est relaye tel quel")
    v.worker.reponse_debloque = None
    dire(ex(v, "debloque") == "je n'ai pas réussi à relancer la liaison.",
         "et un echec se DIT : c'est le seul chemin qui marche quand l'ecran ne repond plus")

    v = neuve()
    dire(ex(v, "compacte") == "conversation compactée.", "compacter rend son compte rendu")
    v.worker.reponse_compacte = None
    dire(ex(v, "compacte") == "je n'ai pas pu compacter.", "son echec aussi")


def changer_de_modele_ne_se_devine_pas():
    print("\n=== « passe sur Opus » ===")
    v = neuve()
    dit = ex(v, "modele", "mets-toi sur haiku")
    dire(v.worker.modeles == [("haiku", True)],
         "le changement demande est TEMPORAIRE : « pour cette tâche » veut dire cette tache")
    dire("juste pour cette tâche" in dit, f"et on l'entend : {dit!r}")

    # Deviner serait pire que demander : la bascule invalide le cache de prompt, donc elle
    # se paie en relecture complete du contexte.
    v = neuve()
    dit = ex(v, "modele", "change de modèle")
    dire(v.worker.modeles == [], "sans modele nomme, RIEN ne bascule")
    dire(dit.startswith("sur quel modèle ?") and "Opus 5" in dit and "Haiku 4.5" in dit,
         f"on demande lequel, en listant ce qui existe : {dit!r}")

    v = neuve()
    v.worker.modele_connu = False
    dire(ex(v, "modele", "passe sur opus") == "je ne connais pas ce modèle.",
         "un modele refuse par le worker se dit, il ne s'avale pas")

    v = neuve()
    dit = ex(v, "modele", "passe sur opus")
    dire("juste pour cette tâche" not in dit,
         "revenir au modele de base ne s'annonce pas comme un detour")


def une_intention_inconnue_ne_dit_rien():
    print("\n=== une intention non gérée ===")
    dire(ex(neuve(), "n_importe_quoi") is None,
         "aucune phrase inventee : mieux vaut se taire que repondre a cote")


# --- 5. la lecture : couper celle-ci, pas « la parole en général » ------------------------

def couper_la_lecture_vise_CETTE_lecture():
    print("\n=== couper et relire une réponse ===")
    v = neuve()
    dire(v.couper_lecture() is False, "sans lecture en cours, couper ne pretend rien")

    async def essai():
        v = neuve()
        poignee = await v._lire("j'ai corrigé le micro")
        return v, poignee
    v, poignee = asyncio.run(essai())
    dire(v.sess.dits == ["j'ai corrigé le micro"], "la lecture part a la synthese")
    dire(v.tableau.dernier("lecture")["actif"] is True, "la page voit qu'une lecture est en cours")
    dire(v.couper_lecture() is True and poignee.coupee == 1, "et on peut couper CELLE-CI")

    # Le rappel de fin rend la parole : sans lui, une conversation gardait le micro de
    # parole des autres apres avoir fini de parler.
    poignee.terminer()
    dire(v._lecture is None and v.tableau.dernier("lecture")["actif"] is False,
         "a la fin, la poignee est rendue et la page le sait")
    dire(v.couper_lecture() is False, "couper une lecture finie ne fait rien")


def relire_ressert_le_texte_sans_refaire_le_tour():
    print("\n=== « relis » ===")
    async def sans_rien():
        return await neuve().relire()
    dire(asyncio.run(sans_rien()) is False, "sans rien a relire, on rend False plutot que du vide")

    async def avec():
        v = neuve()
        v._dernier_debrief = "j'ai corrigé le micro"
        v._paroles["p1"] = "le tout premier débrief"
        a = await v.relire()
        b = await v.relire("p1")
        c = await v.relire("p999")
        return v, a, b, c
    v, a, b, c = asyncio.run(avec())
    dire(a is True and v.sess.dits[0] == "j'ai corrigé le micro", "sans cle, la derniere reponse")
    dire(b is True and v.sess.dits[1] == "le tout premier débrief",
         "avec une cle, CELLE-LA : ce qu'on veut relire est souvent la reponse d'avant")
    dire(c is True and v.sess.dits[2] == "j'ai corrigé le micro",
         "et une cle inconnue retombe sur la derniere plutot que de ne rien dire")


# --- 6. la permission ---------------------------------------------------------------------

def une_permission_attend_vraiment_la_reponse():
    print("\n=== une permission demandée à voix haute ===")
    async def accordee():
        v = neuve()
        v._parler = lambda t: t
        tache = asyncio.create_task(v.demander_permission("supprimer le dossier", "rm"))
        await asyncio.sleep(0)
        v.permission_en_cours.set_result(True)
        return v, await tache
    v, accord = asyncio.run(accordee())
    dire(accord is True, "un oui autorise")
    dire(any("je veux supprimer le dossier" in str(d) for d in v.sess.dits),
         "la demande est DITE : c'est une permission, elle doit s'entendre")
    dire(v.tableau.dernier("permission")["decision"] == "autorisé",
         "et la decision reste ecrite dans le flux")

    async def refusee():
        v = neuve()
        v._parler = lambda t: t
        tache = asyncio.create_task(v.demander_permission("pousser sur main", "push"))
        await asyncio.sleep(0)
        v.permission_en_cours.set_result(False)
        return v, await tache
    v, accord = asyncio.run(refusee())
    dire(accord is False and v.tableau.dernier("permission")["decision"] == "refusé",
         "un non refuse, et le refus est trace aussi")


def main():
    arreter_arrete_vraiment()
    arreter_ferme_aussi_une_question_en_vol()
    se_taire_n_est_pas_s_arreter()
    couper_le_micro_dit_toujours_comment_revenir()
    le_micro_depend_du_bail_autant_que_du_voeu()
    le_statut_dit_le_travail_ET_le_poids()
    repeter_ne_repaye_pas_une_synthese()
    le_quota_se_dit_ou_s_avoue_absent()
    debloquer_et_compacter_repondent_toujours()
    changer_de_modele_ne_se_devine_pas()
    une_intention_inconnue_ne_dit_rien()
    couper_la_lecture_vise_CETTE_lecture()
    relire_ressert_le_texte_sans_refaire_le_tour()
    une_permission_attend_vraiment_la_reponse()
    print("\nTOUT VERT" if ok else "\nDES ECHECS")
    sys.exit(0 if ok else 1)


# Garde explicite : test_debrief.py importe les doublures d'ici. Sans lui, l'import lançait
# toute cette suite puis sortait par sys.exit, et l'autre ne tournait jamais — en affichant
# un « TOUT VERT » qui n'était pas le sien.
if __name__ == "__main__":
    main()
