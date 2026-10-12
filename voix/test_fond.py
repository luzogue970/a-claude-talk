#!/usr/bin/env python3
"""Le travail qui continue APRÈS la réponse, et qu'on doit voir.

Le symptôme : Claude dit « je lance ça, je te dis quand c'est bon », le tour se termine, et
plus rien à l'écran ne distingue cette fin-là d'une vraie. On répond alors à une conversation
qui n'a pas fini de travailler, et le message part au milieu d'autre chose.

Ce que ces tests tiennent — et c'est subtil, parce que **deux identifiants différents**
interviennent :

- l'outil a le sien (`toolu_…`), connu dès l'appel ;
- la tâche de fond en reçoit un autre à son démarrage — « (ID: bdjie0kr9) » pour une
  commande, « agentId: … » pour un sous-agent — et c'est **celui-là** que portera la
  notification de fin.

Sans ce rattachement, la pastille ne s'éteindrait jamais. Avec un mauvais rattachement, elle
s'éteindrait trop tôt, ce qui est pire : on croit le travail fini.

Le dernier bloc est un test fonctionnel : de vrais messages du SDK traversent la vraie boucle
de drainage, du lancement jusqu'à la notification qui arrive un tour plus tard.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("VOIX_WORKDIR", str(Path(__file__).resolve().parent.parent))

from claude_agent_sdk import (AssistantMessage, ResultMessage, TextBlock, ToolResultBlock,
                              ToolUseBlock, UserMessage)

from worker import Worker

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

    def tous(self, genre):
        return [d for g, d in self.publies if g == genre]


def neuf():
    w = Worker(tableau=FauxTableau())
    return w


def taches(w):
    """Ce que la page affiche en ce moment."""
    d = w._tableau.dernier("fond")
    return [t["id"] for t in d["taches"]] if d else []


# --- 1. le rattachement des deux identifiants ---------------------------------------------

def la_tache_s_ouvre_sur_son_PROPRE_identifiant():
    print("\n=== l'identifiant de la tâche n'est pas celui de l'outil ===")
    w = neuf()
    w._attente_fond["toolu_abc"] = ("Bash", "npm test")
    w._fond_resultat("toolu_abc", "Command running in background (ID: bdjie0kr9)")
    dire(taches(w) == ["bdjie0kr9"],
         f"c'est l'ID de la TACHE qui est retenu, pas celui de l'outil : {taches(w)}")
    dire(w._fond["bdjie0kr9"]["cible"] == "npm test",
         "et la pastille sait quoi dire : « npm test », pas « Bash »")
    dire(w._attente_fond == {},
         "l'attente est consommee : un outil ne s'ouvre pas deux fois")

    w = neuf()
    w._attente_fond["toolu_x"] = ("Agent", "cherche les usages")
    w._fond_resultat("toolu_x", "Launched agent (agentId: ag_42)")
    dire(taches(w) == ["ag_42"], "un sous-agent annonce son id autrement, et ça marche aussi")


def sans_identifiant_lisible_on_retombe_sur_celui_de_l_outil():
    print("\n=== quand le retour ne contient aucun identifiant ===")
    w = neuf()
    w._attente_fond["toolu_abc"] = ("Bash", "npm test")
    w._fond_resultat("toolu_abc", "ok")
    # Mieux vaut une ligne qui ne s'eteindra peut-etre jamais qu'une tache invisible : la
    # premiere se voit et s'explique, la seconde fait repondre au milieu d'un travail.
    dire(taches(w) == ["toolu_abc"],
         "on retombe sur l'id de l'outil plutot que de perdre la tache")


def un_outil_ordinaire_n_ouvre_rien():
    print("\n=== un outil qui n'est PAS en arrière-plan ===")
    w = neuf()
    w._fond_resultat("toolu_jamais_vu", "Command running in background (ID: zz)")
    dire(w._fond == {} and w._tableau.tous("fond") == [],
         "aucune tache ouverte : seul `run_in_background` compte, pas le texte du retour")


# --- 2. la fermeture ----------------------------------------------------------------------

def la_notification_de_fin_eteint_la_bonne_ligne():
    print("\n=== la notification de fin, qui arrive un tour plus tard ===")
    w = neuf()
    w._fond_ouvrir("a1", "Bash", "npm test")
    w._fond_ouvrir("a2", "Agent", "cherche les usages")
    dire(taches(w) == ["a1", "a2"], "deux travaux en cours")

    w._fond_notifie("<task-notification><task-id>a1</task-id>fini</task-notification>")
    dire(taches(w) == ["a2"], "la notification eteint CELLE-LA, et laisse l'autre allumee")

    w._fond_notifie("<task-id>a1</task-id>")
    dire(taches(w) == ["a2"], "une notification en double ne rallume ni ne casse rien")

    w._fond_notifie("<task-id>a2</task-id><task-id>inconnue</task-id>")
    dire(taches(w) == [], "plusieurs identifiants dans un seul texte sont tous traites")
    dire(len(w._tableau.tous("fond")) == 4,
         "et la page est republiee a chaque VRAI changement, pas a chaque texte lu")


def un_texte_sans_notification_ne_ferme_rien():
    print("\n=== ce qui ressemble à une notification sans en être une ===")
    w = neuf()
    w._fond_ouvrir("a1", "Bash", "npm test")
    for bruit in ("la tache a1 est finie", "<task-id>", "task-id: a1", ""):
        w._fond_notifie(bruit)
    dire(taches(w) == ["a1"],
         "aucun de ces textes n'eteint la pastille : seule la balise fermee compte")
    dire(w._fond_fermer("") is False and w._fond_fermer("jamais vue") is False,
         "et fermer ce qui n'existe pas rend False plutot que de republier dans le vide")


def rouvrir_une_tache_deja_ouverte_ne_la_redemarre_pas():
    print("\n=== la même tâche annoncée deux fois ===")
    w = neuf()
    w._fond_ouvrir("a1", "Bash", "npm test")
    depuis = w._fond["a1"]["depuis"]
    w._fond_ouvrir("a1", "Bash", "autre chose")
    dire(w._fond["a1"]["depuis"] == depuis and w._fond["a1"]["cible"] == "npm test",
         "la premiere ouverture fait foi : son horodatage est ce qui dit « depuis quand »")
    dire(len(w._tableau.tous("fond")) == 1, "et la page n'est pas republiee pour rien")


# --- 3. fonctionnel : le parcours entier, par la vraie boucle -----------------------------

class FauxClient:
    """Rend les messages qu'on lui donne, comme le ferait le SDK."""

    def __init__(self, messages):
        self.messages = list(messages)

    async def receive_messages(self):
        for m in self.messages:
            yield m


def fin(n=1):
    return ResultMessage(subtype="success", duration_ms=1200, duration_api_ms=1000,
                         is_error=False, num_turns=n, session_id="s1",
                         total_cost_usd=0.01, result="")


def drainer(w, messages):
    w.client = FauxClient(messages)
    asyncio.run(w._drainer_boucle())


def le_parcours_entier_d_un_travail_en_fond():
    print("\n=== fonctionnel : « je lance ça, je te dis quand c'est bon » ===")
    w = neuf()

    # Tour 1 : Claude lance une commande en arriere-plan, puis termine son tour.
    drainer(w, [
        AssistantMessage(content=[ToolUseBlock(
            id="toolu_1", name="Bash",
            input={"command": "npm test", "run_in_background": True,
                   "description": "Lance la suite de tests"})], model="opus"),
        UserMessage(content=[ToolResultBlock(
            tool_use_id="toolu_1",
            content="Command running in background (ID: bdjie0kr9)")]),
        AssistantMessage(content=[TextBlock(text="Je lance les tests, je te dis quand c'est bon.")],
                         model="opus"),
        fin(),
    ])

    dire(w.occupe is False, "le tour est bien termine")
    dire(taches(w) == ["bdjie0kr9"],
         "MAIS la tache reste allumee : c'est tout l'interet, elle survit a la fin du tour")
    ligne = w._tableau.dernier("outil")
    dire(ligne["fond"] is True,
         "et la ligne de l'outil porte sa marque, pour qu'on la reconnaisse dans le flux")

    # Tour 2 : la notification de fin arrive, sur le tour utilisateur, sans aucun outil.
    drainer(w, [
        UserMessage(content=[TextBlock(
            text="<task-notification><task-id>bdjie0kr9</task-id>"
                 "<status>completed</status></task-notification>")]),
        AssistantMessage(content=[TextBlock(text="Les tests passent.")], model="opus"),
        fin(2),
    ])
    dire(taches(w) == [], "au tour suivant, la notification eteint la pastille")


def une_notification_en_simple_chaine_est_vue_aussi():
    print("\n=== fonctionnel : la notification arrivée sans blocs ===")
    # Mesure : quand le contenu du tour utilisateur est une simple chaine, la boucle qui
    # parcourt les blocs ne la voyait pas passer, et la ligne restait allumee pour toujours.
    w = neuf()
    w._fond_ouvrir("bdjie0kr9", "Bash", "npm test")
    drainer(w, [
        UserMessage(content="<task-notification><task-id>bdjie0kr9</task-id></task-notification>"),
        fin(),
    ])
    dire(taches(w) == [],
         "une notification en texte brut eteint la pastille comme une autre")


def un_outil_ordinaire_traverse_sans_rien_allumer():
    print("\n=== fonctionnel : un tour normal ne laisse rien derrière lui ===")
    w = neuf()
    drainer(w, [
        AssistantMessage(content=[ToolUseBlock(
            id="toolu_9", name="Bash", input={"command": "ls", "description": "Liste"})],
            model="opus"),
        UserMessage(content=[ToolResultBlock(tool_use_id="toolu_9", content="a\nb")]),
        fin(),
    ])
    dire(w._fond == {}, "rien n'est allume")
    dire(w._tableau.tous("fond") == [],
         "et la page ne reçoit meme pas d'evenement : pas de pastille vide a afficher")
    dire(w._tableau.dernier("outil")["fond"] is False, "la ligne dit explicitement « pas en fond »")


def main():
    la_tache_s_ouvre_sur_son_PROPRE_identifiant()
    sans_identifiant_lisible_on_retombe_sur_celui_de_l_outil()
    un_outil_ordinaire_n_ouvre_rien()
    la_notification_de_fin_eteint_la_bonne_ligne()
    un_texte_sans_notification_ne_ferme_rien()
    rouvrir_une_tache_deja_ouverte_ne_la_redemarre_pas()
    le_parcours_entier_d_un_travail_en_fond()
    une_notification_en_simple_chaine_est_vue_aussi()
    un_outil_ordinaire_traverse_sans_rien_allumer()
    print("\nTOUT VERT" if ok else "\nDES ECHECS")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
