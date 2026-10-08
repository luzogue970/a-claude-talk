#!/usr/bin/env python3
"""Changer d'effort détruit le cache. Donc on ne le fait que tant que c'est bon marché.

La mesure qui a tout décidé, prise le 8 octobre 2026 contre le vrai Claude Code, même session
reprise, conversation de 45 000 jetons :

    tour ordinaire                 relu 45 365   réécrit      57
    tour juste après une bascule   relu      0   réécrit 45 822

Tout le préfixe est réécrit. Le code affirmait pourtant l'inverse, et de bonne foi : l'ancienne
mesure regardait le tour d'APRÈS, où le cache est effectivement revenu. Un tour trop tard.

L'arithmétique qui en découle, et c'est elle qui tranche. L'écriture de cache coûte vingt fois
sa lecture : une bascule équivaut donc à vingt allers-retours de relecture du contexte. En face,
ce que l'effort fait baisser — la sortie — ne pèse que 5 % de la facture, mesuré sur les vraies
conversations de cette machine. Et rejouées à travers `complexite.evaluer`, 40 % des demandes
changeaient de niveau : une bascule tous les deux tours et demi, jamais amortie.

D'où la règle que ce fichier tient : l'ajustement automatique vit tant que le préfixe réécrit
reste petit, et se tait au-delà — en le DISANT, parce qu'un réglage qui cesse sans un mot
ressemble à une panne. Un niveau choisi à la main, lui, passe toujours : c'est une décision,
pas une optimisation.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

ok = True


def dire(bon, texte):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


class JournalFactice:
    def __init__(self, contexte):
        self.contexte = contexte


class WorkerFactice:
    def __init__(self, contexte, effort="medium"):
        self.effort = effort
        self.journal = JournalFactice(contexte)
        self.bascules = []

    async def changer_effort(self, niveau):
        self.bascules.append(niveau)
        self.effort = niveau
        return niveau


class AgentFactice:
    """Le strict nécessaire pour appeler _ajuster_effort : on teste la règle, pas la session."""

    def __init__(self, contexte, effort="medium"):
        self.worker = WorkerFactice(contexte, effort)
        self._effort_manuel = False
        self._effort_gele = False
        self.vus = []

    def _voir(self, genre, **donnees):
        self.vus.append((genre, donnees))


async def principal():
    import config
    from agent import Voix

    ajuster = Voix._ajuster_effort
    # Une demande qui vaut clairement un autre niveau que « medium » : trois choses à tenir
    # ensemble, donc « high ». Si elle cessait d'en valoir un, le test ne prouverait plus rien.
    import complexite
    DEMANDE = ("Premièrement regarde le cache, deuxièmement compare les coûts, "
               "et ensuite dis-moi ce qui est le plus cher")
    dire(complexite.evaluer(DEMANDE).niveau != "medium",
         f"la demande d'essai vaut bien un autre niveau ({complexite.evaluer(DEMANDE).niveau})")

    print("\n=== conversation légère : l'ajustement fait son travail ===")
    a = AgentFactice(contexte=5_000)
    await ajuster(a, DEMANDE)
    dire(a.worker.bascules == [complexite.evaluer(DEMANDE).niveau],
         f"la bascule a lieu → {a.worker.bascules}")
    dire(any(g == "effort" for g, _ in a.vus), "et elle est annoncée")

    print("\n=== conversation lourde : on garde le niveau en place ===")
    b = AgentFactice(contexte=config.EFFORT_AUTO_MAX_CONTEXTE + 1)
    await ajuster(b, DEMANDE)
    dire(b.worker.bascules == [],
         "aucune bascule : elle réécrirait tout le cache pour économiser sur 5 % de la facture")
    gele = " ".join(str(d.get("texte", "")) for g, d in b.vus)
    dire("figé" in gele, f"et on le dit, au lieu de se taire → {gele[:90]}")
    dire("compacte" in gele,
         "en nommant la sortie : alléger la conversation rend sa liberté à l'ajustement")

    print("\n=== et on ne le répète pas à chaque tour ===")
    avant = len(b.vus)
    await ajuster(b, DEMANDE)
    await ajuster(b, DEMANDE)
    dire(len(b.vus) == avant, "deux tours de plus, pas une ligne de plus")

    print("\n=== redescendre sous le seuil réarme l'ajustement ===")
    b.worker.journal.contexte = 3_000
    await ajuster(b, DEMANDE)
    dire(b.worker.bascules != [], f"la bascule repart → {b.worker.bascules}")

    print("\n=== un niveau choisi à la main n'est jamais défait, quel que soit le poids ===")
    c = AgentFactice(contexte=1_000)
    c._effort_manuel = True
    await ajuster(c, DEMANDE)
    dire(c.worker.bascules == [], "une décision tient : l'automatique ne la défait pas")

    print("\n=== le seuil reste défendable ===")
    dire(0 < config.EFFORT_AUTO_MAX_CONTEXTE <= 100_000,
         f"{config.EFFORT_AUTO_MAX_CONTEXTE} jetons — assez petit pour qu'une bascule "
         "coûte des centimes")

    print(f"\n{'TOUT VERT' if ok else 'DES ECHECS'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
