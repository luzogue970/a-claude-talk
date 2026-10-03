#!/usr/bin/env python3
"""Une liaison qui meurt doit le DIRE et rendre la main. Jamais se taire.

Le défaut réparé, observé en vrai sur une conversation d'insnap le 3 octobre 2026 : la pompe
d'événements s'est arrêtée au tout début d'un tour. Personne n'en a rien su. `occupe` est
resté vrai, la page a affiché « au travail » pendant seize minutes, plus un seul événement
n'est remonté — pendant que Claude Code, lui, finissait tranquillement son tour et l'écrivait
sur disque. Et le bouton « arrêter », seul recours affiché, ne répondait pas non plus : il
demandait l'arrêt AVANT de relâcher l'état, et la demande partait justement dans la liaison
cassée. Bloqué, sans explication, sans bouton.

Trois règles en découlent, et ce fichier les tient :

1. **La pompe ne meurt jamais en silence.** Une exception, ou une fin de flux, se voit à
   l'écran, relâche le tour, et prévient la couche vocale — qui sinon attendrait un bilan qui
   ne viendra plus, micro fermé.
2. **« arrêter » rend la main d'abord.** On ne met pas la sortie de secours derrière l'appel
   qui est peut-être exactement ce qui est cassé.
3. **Il existe une porte de sortie.** Reconstruire la liaison sur la même conversation, sans
   rien perdre : le contexte vit sur le disque de Claude Code, pas dans ce processus.
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


class TableauFactice:
    """Note ce qui serait parti à l'écran."""

    def __init__(self):
        self.lignes = []

    def publier(self, genre, **donnees):
        self.lignes.append((genre, donnees))

    def genres(self):
        return [g for g, _ in self.lignes]

    def texte(self, genre):
        return " ".join(str(d.get("texte", "")) for g, d in self.lignes if g == genre)


class ClientQuiTombe:
    """Un client dont le flux lève — la panne du 3 octobre, en une ligne."""

    async def receive_messages(self):
        raise ConnectionResetError("tube fermé")
        yield  # noqa: unreachable — fait de la méthode un générateur asynchrone


class ClientMuet:
    """Un client qui ne répond plus à rien : le cas où « arrêter » restait en attente."""

    def __init__(self):
        self.interrompu = False

    async def interrupt(self):
        self.interrompu = True
        await asyncio.Event().wait()      # ne revient jamais

    async def disconnect(self):
        await asyncio.Event().wait()


async def principal():
    from worker import Worker

    print("=== une pompe qui tombe relâche le tour et le dit ===")
    tab = TableauFactice()
    w = Worker(tableau=tab)
    w.client = ClientQuiTombe()
    w.occupe = True
    await w._drainer()

    dire(w.occupe is False, "le tour est relâché : la page cesse d'afficher « au travail »")
    dire(w.pompe_morte is True, "la liaison est marquée morte, pour savoir quoi faire ensuite")
    dire("erreur" in tab.genres(), "la panne apparaît à l'écran au lieu d'un silence")
    dire("débloque" in tab.texte("erreur"),
         "et le message dit comment repartir, pas seulement que c'est cassé")
    dire(("travail", {"actif": False}) in tab.lignes, "l'indicateur de travail retombe")
    dire(not w.events.empty(), "la couche vocale reçoit une fin : le micro se rouvre")
    genre, journal = w.events.get_nowait()
    dire(genre == "fin" and journal.fin != "success",
         f"et cette fin est annoncée comme anormale ({journal.fin})")

    print("\n=== un flux qui se ferme proprement compte aussi comme une panne ===")

    class ClientQuiFerme:
        async def receive_messages(self):
            return
            yield  # noqa: unreachable

    tab2 = TableauFactice()
    w2 = Worker(tableau=tab2)
    w2.client = ClientQuiFerme()
    w2.occupe = True
    await w2._drainer()
    dire(w2.occupe is False and "erreur" in tab2.genres(),
         "le CLI qui ferme sa sortie sans rien dire ne laisse pas la page bloquée")

    print("\n=== « arrêter » rend la main avant de demander quoi que ce soit ===")
    tab3 = TableauFactice()
    w3 = Worker(tableau=tab3)
    w3.client = ClientMuet()
    w3.occupe = True
    debut = asyncio.get_running_loop().time()
    await w3.interrompre()
    duree = asyncio.get_running_loop().time() - debut

    dire(w3.occupe is False, "le tour est relâché même si le client ne répond jamais")
    dire(duree < 5.0, f"et l'appel ne reste pas en attente ({duree:.1f} s)")
    dire(w3.pompe_morte is True,
         "un arrêt sans réponse vaut diagnostic : la liaison est injoignable")
    dire("débloque" in tab3.texte("erreur"), "la sortie de secours est nommée")

    print("\n=== « débloque » est un ordre local reconnu à la voix ===")
    import intentions
    for phrase in ("débloque", "débloque la conversation", "ça répond plus",
                   "c'est bloqué", "relance la liaison"):
        nom, pourquoi = intentions.reconnaitre(phrase)
        dire(nom == "debloque", f"« {phrase} » → {nom} ({pourquoi})")
    # Et ce qui N'EST PAS un ordre doit continuer d'aller chez Claude.
    for phrase in ("débloque le scroll du fil quand on remonte",
                   "ajoute un bouton pour débloquer la liaison"):
        nom, _ = intentions.reconnaitre(phrase)
        dire(nom != "debloque", f"« {phrase} » reste une tâche (→ {nom})")

    print(f"\n{'TOUT VERT' if ok else 'DES ECHECS'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
