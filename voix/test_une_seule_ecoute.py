#!/usr/bin/env python3
"""Une demande, une écoute — même quand le travail se fait en plusieurs tours.

Le défaut, mesuré le 28 octobre 2026 sur les 806 tours déjà tenus : **105 d'entre eux, soit
13 %, étaient un SECOND tour arrivé sans que j'aie rien demandé entre les deux.** Toujours la
même forme — « c'est lancé, je te préviens », puis vingt minutes plus tard la vraie réponse.
Deux écoutes pour une seule demande, et la première n'apprenait rien.

La cause n'est pas un bug : c'est le fonctionnement normal du travail de fond. Claude lance
une tâche, rend la parole, et le harnais le rappelle quand la tâche finit. Chaque reprise est
un tour complet, donc un bilan parlé de plus.

Ce qui est vérifié ici :

- un tour qui ne fait qu'annoncer une attente **ne se dit pas** — il s'affiche, on le lit si
  on veut, mais on ne l'écoute pas ;
- ce qu'il racontait n'est **pas perdu** : il est recousu avec le tour qui conclut, et c'est
  l'ensemble qu'on entend, en une fois ;
- quatre situations rompent le silence, parce que se taire y serait pire : un travail coupé
  en route, une question dont j'attends la réponse, une question que je pose pendant que ça
  tourne, et une attente qui n'en finit pas.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("VOIX_WORKDIR", str(Path(__file__).resolve().parent.parent))

from test_debrief import FauxPorteParole, FausseConv, neuve, dire
import test_debrief
import worker as W

ok = True


def verifier(bon, texte):
    global ok
    ok = ok and bon
    test_debrief.dire(bon, texte)


def journal(question="", textes=(), outils=(), fond=(), fin="success", duree=1.0,
            jetons=100, stop=None):
    j = W.Journal(question=question, fin=fin, duree_s=duree, jetons=jetons, stop=stop)
    j.textes = list(textes)
    j.outils = list(outils)
    j.fond_en_cours = list(fond)
    return j


# --- TU : recoudre deux tours en un seul récit -------------------------------------------

def recoudre_ne_perd_rien():
    print("\n= deux tours recousus racontent les deux =")
    a = journal(question="lance les tests", textes=["Les tests tournent, je te préviens."],
                outils=[("Bash", "tests.py")], fond=["Bash tests.py"], duree=12.0, jetons=500)
    b = journal(question=W.RELANCE_DE_FOND, textes=["Tout est vert : 2128 vérifications."],
                outils=[("Read", "sortie.log")], duree=3.0, jetons=900,
                fin="error_max_turns", stop="max_tokens")
    u = a.enchainer(b)
    verifier(u.question == "lance les tests",
             "la demande gardée est la mienne, pas la relance du harnais")
    verifier(u.textes == ["Les tests tournent, je te préviens.",
                          "Tout est vert : 2128 vérifications."],
             "ce qui a été écrit dans les deux tours est dit en une fois")
    verifier(u.lignes() == a.lignes() + b.lignes(),
             "les actions des deux tours comptent dans le même bilan")
    verifier(u.fin == "error_max_turns" and u.stop == "max_tokens" and u.jetons == 900,
             "comment ça s'est terminé, c'est le DERNIER tour qui le dit")
    verifier(u.duree_s == 15.0, "le temps passé s'additionne : 12 s puis 3 s font 15 s")
    verifier(a.textes == ["Les tests tournent, je te préviens."],
             "recoudre ne touche pas aux tours d'origine")


# --- TU : ce qui compte comme « encore en train de tourner » -----------------------------

def une_question_pendant_que_ca_tourne_se_repond_tout_de_suite():
    print("\n= « où on en est ? » n'est pas « c'est lancé » =")
    w = W.Worker.__new__(W.Worker)
    w._fond = {"t1": {"nom": "Bash", "cible": "tests.py"}}
    w._fond_du_tour = set()
    w.journal = W.Journal(question="où on en est ?")
    verifier(w._fond_a_suivre() == [],
             "une question posée pendant une compilation n'attend pas la compilation")
    w._fond_du_tour = {"t1"}
    verifier(w._fond_a_suivre() == ["Bash tests.py"],
             "un tour qui a lancé lui-même ce qui tourne annonce une attente")
    w._fond_du_tour = set()
    w.journal = W.Journal(question=W.RELANCE_DE_FOND)
    verifier(w._fond_a_suivre() == ["Bash tests.py"],
             "une reprise après une tâche finie suit tout ce qui reste à tourner")
    w._fond = {}
    verifier(w._fond_a_suivre() == [],
             "plus rien ne tourne : le tour conclut, donc il se dit")


# --- TU : les quatre raisons de parler quand même ----------------------------------------

def quatre_situations_rompent_le_silence():
    print("\n= se taire serait pire =")
    v = neuve()
    verifier(v._en_attente_de_fond(journal(fond=["Bash tests.py"],
                                           textes=["C'est lancé, je te préviens."])),
             "« c'est lancé » ne s'écoute pas")
    verifier(not v._en_attente_de_fond(journal(textes=["C'est fait."])),
             "un tour qui ne laisse rien tourner derrière lui s'écoute")
    verifier(not v._en_attente_de_fond(
                 journal(fond=["Bash tests.py"], textes=["J'ai commencé."],
                         fin="error_max_turns")),
             "un travail coupé en route se dit tout de suite, pas quand la tâche finira")
    verifier(not v._en_attente_de_fond(
                 journal(fond=["Bash tests.py"],
                         textes=["Les tests tournent. Je pars sur SQLite ou sur Postgres ?"])),
             "une question dont j'attends la réponse ne se tait jamais")
    v._tours_en_attente = [journal()] * v.ATTENTES_MAX
    verifier(not v._en_attente_de_fond(journal(fond=["Bash tests.py"], textes=["Ça tourne."])),
             "au bout de quatre tours avalés, on parle plutôt que de risquer le mutisme")
    v._tours_en_attente = []
    import time as T
    v._silence_depuis = T.monotonic() - v.SILENCE_MAX_S - 1
    verifier(not v._en_attente_de_fond(journal(fond=["Bash tests.py"], textes=["Ça tourne."])),
             "une attente qui dépasse vingt-cinq minutes se dit, tâche finie ou pas")


# --- TI : la pompe d'événements, de bout en bout ------------------------------------------

def une_demande_une_seule_ecoute():
    print("\n= la pompe : deux tours entrent, un seul bilan sort =")
    conv = FausseConv(tours=4, titre="déjà titrée")
    v = neuve(conv)
    dits = []

    class Mouchard:
        async def dire_flux(self, j):
            dits.append(j)
            yield "bilan parlé."

    v.porte_parole = Mouchard()
    lus = []

    async def faux_lire(flux):
        lus.append("".join([m async for m in flux]))

    v._lire = faux_lire

    async def scene():
        v.worker.events = asyncio.Queue()
        tache = asyncio.create_task(v.pomper_evenements())
        await v.worker.events.put(("fin", journal(
            question="lance les tests",
            textes=["Les tests tournent, je te préviens."],
            outils=[("Bash", "tests.py")], fond=["Bash tests.py"])))
        await asyncio.sleep(0.05)
        verifier(not dits, "le « c'est lancé » ne part pas vers la voix")
        verifier(len(v._tours_en_attente) == 1, "il est mis de côté, pas jeté")
        await v.worker.events.put(("fin", journal(
            question=W.RELANCE_DE_FOND, textes=["Tout est vert : 2128 vérifications."],
            outils=[("Read", "sortie.log")])))
        await asyncio.sleep(0.05)
        tache.cancel()

    asyncio.run(scene())
    verifier(len(dits) == 1, "une seule demande, un seul bilan à écouter")
    verifier(len(lus) == 1 and lus[0] == "bilan parlé.", "et une seule lecture à voix haute")
    if dits:
        verifier(dits[0].textes == ["Les tests tournent, je te préviens.",
                                    "Tout est vert : 2128 vérifications."],
                 "ce bilan raconte les deux tours, pas seulement le dernier")
        verifier(dits[0].question == "lance les tests",
                 "il répond à ma demande, pas à la notification du harnais")
    verifier(v._tours_en_attente == [] and v._silence_depuis is None,
             "l'ardoise est effacée : le tour suivant repart d'un silence neuf")
    verifier(len(conv.tours_claude) == 1,
             "le transcrit garde une réponse, pas deux — c'est ce qu'on a vécu")
    # Ne rien entendre ne doit pas vouloir dire ne rien savoir : qui regarde l'écran doit
    # voir que ça continue, sinon « il ne répond plus » remplace « j'écoute deux fois ».
    att = v.tableau.dernier("attente_fond")
    verifier(att is not None and att.get("quoi") == ["Bash tests.py"],
             "l'écran dit ce qu'on attend pendant que la voix se tait")


def principal():
    recoudre_ne_perd_rien()
    une_question_pendant_que_ca_tourne_se_repond_tout_de_suite()
    quatre_situations_rompent_le_silence()
    une_demande_une_seule_ecoute()
    print()
    sys.exit(0 if (ok and test_debrief.ok) else 1)


if __name__ == "__main__":
    principal()
