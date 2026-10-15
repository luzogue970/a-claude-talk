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

from worker import RELANCE_DE_FOND, Worker

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


# --- 3. les outils qui travaillent en fond sans le dire ----------------------------------
#
# Mesure du 13 octobre 2026 : un workflow de six agents a tourné vingt minutes sur la
# conversation insnap sans qu'une seule pastille s'allume. `Agent` et `Workflow` ne posent
# pas `run_in_background` ; seul leur retour dit qu'ils continuent.

def un_agent_delegue_s_allume_sans_le_drapeau():
    print("\n=== un agent délégué s'allume même sans « run_in_background » ===")
    w = neuf()
    w._attente_fond["toolu_a"] = ("Agent", "Explorer les tests")
    w._fond_resultat("toolu_a", "Async agent launched successfully.\nagentId: a9f0c1b2\n"
                                "Use TaskOutput to read its result.")
    dire(taches(w) == ["a9f0c1b2"],
         f"l'agent lancé en fond est suivi sous son identifiant : {taches(w)}")

    w2 = neuf()
    w2._attente_fond["toolu_b"] = ("Agent", "Relire le module")
    w2._fond_resultat("toolu_b", "Voici mon rapport : tout est conforme, rien à signaler.")
    dire(w2._fond == {} and w2._tableau.tous("fond") == [],
         "un agent au premier plan rend son rapport : il a fini, rien ne s'allume")

    w3 = neuf()
    w3._attente_fond["toolu_c"] = ("Workflow", "Veille terrain")
    w3._fond_resultat("toolu_c", "Workflow launched in background. Task ID: w7c7dxs4d\n"
                                 "Summary: six veilles\nTranscript dir: /tmp/nulle-part/wf_1\n"
                                 "Script file: /tmp/x.js")
    dire(taches(w3) == ["w7c7dxs4d"],
         f"un workflow est suivi sous l'identifiant de sa tâche : {taches(w3)}")
    dire(w3._suivis == {},
         "hors boucle d'événements, pas de lecteur de journal — et pas de plantage")


def les_agents_d_un_workflow_se_lisent_dans_son_journal():
    print("\n=== les agents d'un workflow se lisent dans son journal ===")
    from worker import agents_du_journal
    journal = (
        '{"type":"launched"}\n'
        '{"type":"started","key":"k1","agentId":"a1","label":"veille:snapchat","phase":"Veille"}\n'
        '{"type":"started","key":"k2","agentId":"a2","label":"veille:discord","phase":"Veille"}\n'
        '{"type":"result","key":"k1","agentId":"a1","result":{"verdict":"ok"}}\n'
        '{"type":"started","key":"k3","agentId":"a3","label":"mesure:snap","phase":"Mesure"}\n'
        '{"type":"failed","key":"k2","error":"boom"}\n'
        '{"type":"started","key":"k4","agentId":"a4","lab'
    )
    agents = agents_du_journal(journal)
    dire([a["nom"] for a in agents] == ["veille:snapchat", "veille:discord", "mesure:snap"],
         f"chaque agent démarré est nommé par son libellé : {[a['nom'] for a in agents]}")
    dire([a["etat"] for a in agents] == ["fini", "échoué", "en cours"],
         f"et porte son état — fini, échoué, en cours : {[a['etat'] for a in agents]}")
    dire(agents[2]["phase"] == "Mesure", "la phase de l'orchestration est dite")
    dire(len(agents) == 3, "la dernière ligne, coupée au milieu, n'est ni comptée ni fatale")
    dire(agents_du_journal("") == [] and agents_du_journal("pas du json\n[]\n") == [],
         "un journal vide ou illisible ne donne aucun agent, sans erreur")


def le_workflow_montre_ses_agents_puis_s_eteint():
    print("\n=== fonctionnel : je vois les agents du workflow tourner, puis finir ===")
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        journal = Path(d) / "journal.jsonl"
        journal.write_text('{"type":"launched"}\n{"type":"started","key":"k1","agentId":"a1",'
                           '"label":"veille:snapchat","phase":"Veille"}\n', encoding="utf-8")
        w = neuf()
        w.PAS_SUIVI = 0.05

        async def scenario():
            # Tour 1 : Claude lance un workflow — sans drapeau — et termine son tour.
            w.client = FauxClient([
                AssistantMessage(content=[ToolUseBlock(
                    id="toolu_w", name="Workflow",
                    input={"script": "export const meta = {}"})], model="opus"),
                UserMessage(content=[ToolResultBlock(
                    tool_use_id="toolu_w",
                    content=f"Workflow launched in background. Task ID: wf9\n"
                            f"Summary: veille\nTranscript dir: {d}\nScript file: x.js")]),
                AssistantMessage(content=[TextBlock(text="Je lance la veille.")], model="opus"),
                fin(),
            ])
            await w._drainer_boucle()
            await asyncio.sleep(0.15)
            dire(w.occupe is False and taches(w) == ["wf9"],
                 "le tour est fini et le workflow reste allumé")
            agents = w._tableau.dernier("fond")["taches"][0]["agents"]
            dire([a["nom"] for a in agents] == ["veille:snapchat"],
                 f"la page sait déjà quel agent tourne : {[a['nom'] for a in agents]}")

            # L'orchestration avance : un agent finit, un autre démarre.
            with journal.open("a", encoding="utf-8") as f:
                f.write('{"type":"result","key":"k1","agentId":"a1","result":{}}\n'
                        '{"type":"started","key":"k2","agentId":"a2","label":"mesure:snap",'
                        '"phase":"Mesure"}\n')
            await asyncio.sleep(0.2)
            agents = w._tableau.dernier("fond")["taches"][0]["agents"]
            dire([(a["nom"], a["etat"]) for a in agents]
                 == [("veille:snapchat", "fini"), ("mesure:snap", "en cours")],
                 "sans rien demander, la page voit le premier fini et le second en cours")

            # Tour 2 : la notification de fin arrive, un tour plus tard.
            w.client = FauxClient([
                UserMessage(content="<task-notification><task-id>wf9</task-id>"
                                    "<status>completed</status></task-notification>"),
                AssistantMessage(content=[TextBlock(text="La veille est finie.")], model="opus"),
                fin(2),
            ])
            await w._drainer_boucle()
            dire(taches(w) == [], "la notification éteint la pastille")
            dire(w._suivis == {}, "et arrête le lecteur du journal avec elle")

        asyncio.run(scenario())



# --- 4. le tour que personne n'a demandé ---------------------------------------------------

def un_tour_relance_par_une_tache_de_fond_rallume_le_travail():
    print("\n=== une tâche de fond qui se termine redonne la parole au modèle ===")
    # Mesuré le 15 octobre 2026 sur la conversation insnap : réponse à 15:44:03, fin de la
    # tâche à 15:44:33, nouveau Bash à 15:44:36. L'indicateur n'étant allumé que par
    # `envoyer()`, tout ce tour-là se déroulait éteint — le tableau affichait « prête »
    # pendant que la page montrait Claude en train de travailler.
    w = neuf()
    w.occupe = False
    drainer(w, [AssistantMessage(content=[TextBlock(text="Je reprends là où j'en étais.")],
                                 model="opus")])
    etats = [d["actif"] for g, d in w._tableau.publies if g == "travail"]
    dire(etats[:1] == [True],
         f"la conversation se rallume toute seule : elle n'est PAS prête ({etats})")
    dire(w.journal.question == RELANCE_DE_FOND,
         f"et le bilan dit d'où sort ce tour : {w.journal.question!r}")


def un_message_qui_traine_apres_un_arret_ne_rallume_rien():
    print("\n=== ce qui arrive encore APRÈS « arrête » ===")

    class ClientQuiRepond:
        async def interrupt(self):
            return None

    w = neuf()
    w.client = ClientQuiRepond()
    w.occupe = True
    asyncio.run(w.interrompre())
    w._tableau.publies.clear()
    # Le tube n'est pas vide quand on appuie sur « arrête » : le dernier bloc du modèle
    # arrive souvent après. Le rallumer ferait repartir un indicateur qu'on vient
    # volontairement d'éteindre, et le bouton passerait pour cassé.
    drainer(w, [AssistantMessage(content=[TextBlock(text="...je disais donc")], model="opus")])
    dire(w._tableau.tous("travail") == [],
         "un reste de message ne rallume pas un tour qu'on vient d'arrêter")
    dire(w.occupe is False, "et la conversation reste rendue")


def le_parcours_entier_d_une_relance_automatique():
    print("\n=== fonctionnel : répondre, puis repartir sans qu'on ait rien dit ===")
    w = neuf()
    w.occupe = True       # un tour demandé par nous, en cours

    drainer(w, [
        AssistantMessage(content=[ToolUseBlock(
            id="toolu_1", name="Bash",
            input={"command": "npm test", "run_in_background": True})], model="opus"),
        UserMessage(content=[ToolResultBlock(
            tool_use_id="toolu_1", content="Command running in background (ID: bd42)")]),
        AssistantMessage(content=[TextBlock(text="Version poussée. Je te dis quand c'est bon.")],
                         model="opus"),
        fin(),
    ])
    dire([d["actif"] for g, d in w._tableau.publies if g == "travail"] == [False],
         "le tour rend la main : c'est là que la notification « c'est fini » part")

    # Trente secondes plus tard, la tâche se termine : Claude Code glisse la notification
    # et le modèle reprend la parole. Personne n'a rien envoyé.
    w._tableau.publies.clear()
    drainer(w, [
        UserMessage(content=[TextBlock(
            text="<task-notification><task-id>bd42</task-id><status>completed</status>"
                 "</task-notification>")]),
        AssistantMessage(content=[ToolUseBlock(
            id="toolu_2", name="Bash", input={"command": "curl -I https://exemple"})],
            model="opus"),
        AssistantMessage(content=[TextBlock(text="C'est en ligne : version 0.20.0.")],
                         model="opus"),
        fin(2),
    ])
    etats = [d["actif"] for g, d in w._tableau.publies if g == "travail"]
    dire(etats == [True, False],
         f"la conversation se rallume pour ce tour-là, puis se rend vraiment : {etats}")
    dire(w._tableau.dernier("travail").get("reponse", "").startswith("C'est en ligne"),
         "et c'est CETTE réponse-là que la notification de fin montrera")
    dire(taches(w) == [], "la tâche de fond s'est éteinte au passage")


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
    un_agent_delegue_s_allume_sans_le_drapeau()
    les_agents_d_un_workflow_se_lisent_dans_son_journal()
    le_workflow_montre_ses_agents_puis_s_eteint()
    un_tour_relance_par_une_tache_de_fond_rallume_le_travail()
    un_message_qui_traine_apres_un_arret_ne_rallume_rien()
    le_parcours_entier_d_une_relance_automatique()
    print("\nTOUT VERT" if ok else "\nDES ECHECS")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
