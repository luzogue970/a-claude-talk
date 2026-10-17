#!/usr/bin/env python3
"""Une question posée attend sa réponse. Indéfiniment, visiblement, et elle survit.

Il y avait ici un délai de cinq minutes au bout duquel l'outil rendait « pas de réponse » et
Claude reprenait la main — c'est-à-dire tranchait tout seul la question qu'il venait de poser.
Le raisonnement d'origine était bon (« le coût d'attendre est nul, celui d'abandonner est un
tour perdu ») ; la conclusion s'arrêtait un cran trop tôt. Une question posée veut dire
qu'aucune réponse n'est évidente : la trancher à la place de quelqu'un parce qu'il était au
téléphone est exactement le pire moment pour le faire, et personne ne saura jamais qu'un choix
a été pris à sa place.

Supprimer le délai ne suffit pourtant pas : une attente sans fin qu'on ne voit pas est un
blocage. D'où les trois autres règles que ce fichier tient avec la première.

1. **On attend, sans limite.** Aucun choix pris à la place de quelqu'un.
2. **L'attente est un ÉTAT**, pas une ligne qui défile : republiée à chaque reconnexion, et
   lisible de l'extérieur — sans quoi une conversation qui attend une réponse est
   indiscernable d'une conversation oisive, même silence et mêmes zéro événement.
3. **Elle survit à la fermeture.** C'est précisément quand on ferme l'application qu'une
   question en attente se perd, et on revient sur une conversation arrêtée sans jamais savoir
   qu'elle attendait quelque chose.
4. **Il y a une porte.** « arrête » dénoue l'attente — sinon rien ne la termine plus.
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

ok = True


def dire(bon, texte):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


class WorkerFactice:
    session_id = "sess-1111-2222-3333"


class AgentFactice:
    """Le strict nécessaire pour exercer poser_question hors d'une vraie session."""

    def __init__(self):
        self.worker = WorkerFactice()
        self.question_en_cours = None
        self._attente = None
        self.vus = []
        self.sess = self
        self.dit = []

    def _voir(self, genre, **d):
        self.vus.append((genre, d))

    def _parler(self, texte):
        return texte

    async def say(self, texte, **_):
        self.dit.append(texte)

    def etats(self, genre):
        return [d for g, d in self.vus if g == genre]

    # Les vraies méthodes, empruntées à la classe : c'est le comportement de l'application
    # qu'on teste, pas une imitation qui pourrait en diverger sans qu'on le voie.
    def _ouvrir_attente(self, *a, **k):
        from agent import Voix
        return Voix._ouvrir_attente(self, *a, **k)

    def _fermer_attente(self, *a, **k):
        from agent import Voix
        return Voix._fermer_attente(self, *a, **k)


QUESTIONS = [{"question": "Je remplace le fichier ou j'en crée un nouveau ?",
              "header": "Fichier",
              "options": [{"label": "Remplacer",
                           "description": "le fichier actuel disparaît"},
                          {"label": "Nouveau fichier",
                           "description": "les deux restent côte à côte"}],
              "multiSelect": False}]

MULTIPLE = [{"question": "Qu'est-ce qu'on branche ?",
             "header": "Suites",
             "options": [{"label": "les unitaires"}, {"label": "les fonctionnels"}],
             "multiSelect": True}]


async def principal():
    import journal
    from agent import Voix

    poser = Voix.poser_question
    with tempfile.TemporaryDirectory() as tmp:
        ancien, journal.QUESTIONS = journal.QUESTIONS, Path(tmp) / "questions.json"
        try:
            print("=== on attend, et on n'invente pas de réponse ===")
            a = AgentFactice()
            tache = asyncio.create_task(poser(a, QUESTIONS))
            await asyncio.sleep(0.05)
            dire(not tache.done(), "la question reste en vol : rien n'a été tranché")
            dire(bool(a.dit), f"et elle a été dite à voix haute → {a.dit[:1]}")

            ouvertures = [d for d in a.etats("attente_reponse") if d.get("actif")]
            dire(len(ouvertures) == 1, "l'attente est publiée comme un état")
            dire("Je remplace" in str(ouvertures[0].get("texte")),
                 "avec le texte de la question, pour une page qui se connecte après")

            # Ce que la page reçoit décide de ce qu'on peut TOUCHER. Les descriptions
            # étaient jetées ici, et « multiSelect » avec : on choisissait entre deux
            # étiquettes sans savoir ce qu'elles engagent, et le premier toucher fermait un
            # choix qui en acceptait trois.
            posees = (ouvertures[0].get("questions") or [{}])[0]
            opts = posees.get("options") or []
            dire([o.get("titre") for o in opts] == ["Remplacer", "Nouveau fichier"],
                 "chaque option arrive avec son intitulé, dans l'ordre proposé")
            dire(opts and opts[0].get("detail") == "le fichier actuel disparaît",
                 "et avec ce qu'elle engage — c'est sur ça qu'on tranche")
            dire(posees.get("multi") is False,
                 "une question à réponse unique se déclare comme telle")
            dire(posees.get("entete") == "Fichier",
                 "l'en-tête voyage aussi : c'est le titre du panneau")

            print("\n=== et elle est écrite sur le disque, pour survivre à la fermeture ===")
            sur_disque = journal.question_en_attente(WorkerFactice.session_id)
            dire(bool(sur_disque), "la question est retrouvable par son identifiant de session")
            dire(sur_disque and "Je remplace" in sur_disque.get("texte", ""),
                 "et c'est bien la bonne")
            dire(journal.question_en_attente("une-autre-session") is None,
                 "sans déborder sur les autres conversations")

            print("\n=== répondre referme tout ===")
            a.question_en_cours.set_result("la deuxième, mais garde l'ancien")
            reponse = await tache
            dire(reponse == "la deuxième, mais garde l'ancien",
                 f"la réponse est rendue telle quelle, sans être découpée → {reponse!r}")
            dire(any(not d.get("actif") for d in a.etats("attente_reponse")),
                 "l'état d'attente est éteint")
            dire(journal.question_en_attente(WorkerFactice.session_id) is None,
                 "et le disque est nettoyé : on ne la retrouvera pas au prochain lancement")

            print("\n=== plusieurs réponses possibles : la page doit le savoir ===")
            m = AgentFactice()
            tm = asyncio.create_task(poser(m, MULTIPLE))
            await asyncio.sleep(0.05)
            posee_m = [d for d in m.etats("attente_reponse") if d.get("actif")][0]
            dire((posee_m.get("questions") or [{}])[0].get("multi") is True,
                 "« multiSelect » traverse jusqu'à la page")
            m.question_en_cours.set_result("les deux")
            await tm

            print("\n=== « arrête » est la porte de sortie de l'attente sans fin ===")
            b = AgentFactice()
            tache2 = asyncio.create_task(poser(b, QUESTIONS))
            await asyncio.sleep(0.05)
            dire(Voix.denouer_attente(b) is True, "le dénouement trouve bien la question en vol")
            reponse2 = await tache2
            dire(reponse2 is None,
                 "il rend « pas de réponse » — Claude reprend la main en le SACHANT")
            dire(journal.question_en_attente(WorkerFactice.session_id) is None,
                 "et rien ne traîne sur le disque")
            dire(Voix.denouer_attente(b) is False,
                 "dénouer deux fois ne fait rien : il n'y a plus de question")

            print("\n=== le format sur disque reste relisible à la main ===")
            journal.noter_question("sess-x", {"id": "q1", "texte": "alors ?"})
            brut = json.loads(journal.QUESTIONS.read_text(encoding="utf-8"))
            dire(brut.get("sess-x", {}).get("texte") == "alors ?",
                 "une session, une question, en clair dans le fichier")
            journal.effacer_question("sess-x")
            dire(journal.question_en_attente("sess-x") is None, "et l'effacement efface")
        finally:
            journal.QUESTIONS = ancien

    print("\n=== aucun délai ne traîne dans le code de la question ===")
    source = (Path(__file__).parent / "agent.py").read_text(encoding="utf-8")
    debut = source.index("async def poser_question")
    corps = source[debut:source.index("def _ouvrir_attente", debut)]
    dire("wait_for" not in corps and "timeout" not in corps,
         "poser_question n'attend plus sous condition de temps")

    print(f"\n{'TOUT VERT' if ok else 'DES ECHECS'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
