#!/usr/bin/env python3
"""Qui travaille, et sur quoi — pendant que ça travaille.

Le symptôme, dit par celui qui regarde la page : « plusieurs agents tournent et je n'ai
aucune idée de ce qu'ils font ». Ce n'était pas une impression. Un `Agent` rend la main
cinq ou dix minutes plus tard ; entre les deux, le SDK fait remonter **ses** appels
d'outils dans le même flux, avec `parent_tool_use_id` renseigné — la documentation du SDK
appelle ça « enough for a progress heartbeat ». On ne regardait pas ce champ.

Conséquence exacte, et double :

- le délégué n'existait nulle part : une ligne « Agent · explorer les tests », puis rien ;
- son travail s'affichait comme celui du tour principal, mélangé au sien, sans rien qui
  permette de dire lequel des deux venait de lire ce fichier-là.

Ce que ces tests tiennent :

1. **Une fiche par délégué**, ouverte à l'appel de l'outil, avec son nom.
2. **Chaque action lui est attribuée** — c'est elle qui fait la ligne « ce qu'il fait ».
3. **Elle s'éteint quand il a fini**, et pas avant : un agent lancé en fond continue après
   le tour, et c'est la notification de fin qui le termine.
4. **Le journal du tour n'enfle pas** du travail du délégué. Le bilan parlé le lit : dire
   « trente-trois actions » quand trente viennent d'un agent déjà annoncé noie les trois
   qui étaient vraiment celles du tour.
5. **Rien ne reste allumé** après un tour interrompu.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("VOIX_WORKDIR", str(Path(__file__).resolve().parent.parent))

from claude_agent_sdk import (AssistantMessage, ResultMessage, TextBlock, ToolResultBlock,
                              ToolUseBlock, UserMessage)

from worker import ACTIONS_GARDEES, Worker

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


class FauxClient:
    def __init__(self, messages):
        self.messages = list(messages)

    async def receive_messages(self):
        for m in self.messages:
            yield m


def fin(n=1):
    return ResultMessage(subtype="success", duration_ms=1200, duration_api_ms=1000,
                         is_error=False, num_turns=n, session_id="s1",
                         total_cost_usd=0.01, result="")


def neuf():
    return Worker(tableau=FauxTableau())


def drainer(w, messages):
    w.client = FauxClient(messages)
    asyncio.run(w._drainer_boucle())


def vus(w):
    """Ce que la page affiche en ce moment : un agent par entrée."""
    d = w._tableau.dernier("agents")
    return d["liste"] if d else []


def appel(tool_id, nom, entree, parent=None):
    return AssistantMessage(content=[ToolUseBlock(id=tool_id, name=nom, input=entree)],
                            model="opus", parent_tool_use_id=parent)


def retour(tool_id, texte):
    return UserMessage(content=[ToolResultBlock(tool_use_id=tool_id, content=texte)])


# --- 1. la fiche : elle s'ouvre, elle se nomme -------------------------------------------

def une_delegation_ouvre_une_fiche_nommee():
    print("\n=== déléguer ouvre une fiche, et elle porte un nom lisible ===")
    w = neuf()
    w._agent_ouvrir("toolu_a", {"subagent_type": "Explore",
                                "description": "Explorer les tests"}, "Explorer les tests")
    liste = vus(w)
    dire([a["nom"] for a in liste] == ["Explorer les tests"],
         f"la fiche prend la description écrite par Claude, pas « Agent » : {liste}")
    dire(liste[0]["sorte"] == "Explore", "et dit de quelle sorte d'agent il s'agit")
    dire(liste[0]["fait"] == "" and liste[0]["combien"] == 0,
         "elle part vide : il n'a encore rien fait, et on ne l'invente pas")

    w2 = neuf()
    w2._agent_ouvrir("toolu_b", {"subagent_type": "Explore"}, "")
    dire(vus(w2)[0]["nom"] == "Explore",
         "sans description, le type de l'agent fait le nom — jamais de ligne anonyme")


# --- 2. l'attribution des actions --------------------------------------------------------

def chaque_action_va_a_son_delegue():
    print("\n=== chaque action remontée est attribuée au bon délégué ===")
    w = neuf()
    w._agent_ouvrir("toolu_a", {}, "Relire le module")
    w._agent_ouvrir("toolu_b", {}, "Compter les suites")
    dire(w._agent_agir("toolu_a", "Read", "worker.py") is True,
         "une action dont le parent est connu lui est attribuée")
    w._agent_agir("toolu_b", "Bash", "lance les tests")
    par_nom = {a["nom"]: a for a in vus(w)}
    dire(par_nom["Relire le module"]["fait"] == "lu worker.py",
         f"et la ligne dit ce qu'il fait, en clair : {par_nom['Relire le module']['fait']!r}")
    dire(par_nom["Compter les suites"]["fait"] == "lancé lance les tests",
         "sans mélanger avec ce que fait l'autre")
    dire(w._agent_agir("", "Read", "x.py") is False
         and w._agent_agir("toolu_inconnu", "Read", "x.py") is False,
         "une action sans parent connu n'est rangée sous personne : mieux vaut aucune "
         "attribution qu'une fausse")


def l_historique_est_borne_mais_le_compte_est_juste():
    print("\n=== un agent bavard ne fait pas voyager tout son passé ===")
    w = neuf()
    w._agent_ouvrir("toolu_a", {}, "Balayer le dépôt")
    for i in range(ACTIONS_GARDEES + 15):
        w._agent_agir("toolu_a", "Read", f"f{i}.py")
    a = vus(w)[0]
    dire(len(a["actions"]) == ACTIONS_GARDEES,
         f"le chemin gardé est borné : {len(a['actions'])} actions au lieu de "
         f"{ACTIONS_GARDEES + 15}")
    dire(a["combien"] == ACTIONS_GARDEES + 15,
         "mais le compte, lui, reste juste — c'est lui qu'on lit")
    dire(a["fait"] == f"lu f{ACTIONS_GARDEES + 14}.py",
         "et la dernière action est bien la dernière")


# --- 3. l'extinction ---------------------------------------------------------------------

def un_delegue_au_premier_plan_s_eteint_en_rendant_son_rapport():
    print("\n=== l'agent qui rend son rapport a fini ===")
    w = neuf()
    w._agent_ouvrir("toolu_a", {}, "Relire le module")
    w._agent_rendu("toolu_a", "")
    dire(vus(w) == [], "sa fiche disparaît : plus rien ne tourne, et la page le dit")


def un_delegue_en_fond_reste_allume_jusqu_a_sa_notification():
    print("\n=== l'agent lancé en fond continue APRÈS le tour ===")
    w = neuf()
    w._attente_fond["toolu_a"] = ("Agent", "Veille terrain")
    w._agent_ouvrir("toolu_a", {}, "Veille terrain")
    cle = w._fond_resultat("toolu_a", "Async agent launched successfully.\nagentId: ag_42")
    dire(cle == "ag_42", f"le retour de l'outil livre l'identifiant de la tâche : {cle!r}")
    w._agent_rendu("toolu_a", cle)
    dire([a["nom"] for a in vus(w)] == ["Veille terrain"] and vus(w)[0]["fond"] is True,
         "la fiche reste allumée, et se dit « en fond »")
    w._agents_balayer()
    dire(len(vus(w)) == 1, "la fin du tour ne l'éteint pas : il travaille encore")
    w._fond_fermer("ag_42")
    dire(vus(w) == [], "c'est la notification de fin de tâche qui l'éteint, et elle seule")


def un_tour_interrompu_ne_laisse_rien_allume():
    print("\n=== un tour coupé n'abandonne pas de fiche allumée ===")
    w = neuf()
    w._agent_ouvrir("toolu_a", {}, "Relire le module")
    w._agents_balayer()
    # Une pastille qui affirme qu'un agent tourne alors qu'il est mort est pire que pas de
    # pastille : on attend une réponse qui n'arrivera jamais.
    dire(vus(w) == [], "le délégué du premier plan est balayé avec le tour qui l'attendait")


# --- 4. intégration : de vrais messages du SDK traversent la boucle ----------------------

def la_boucle_rattache_le_travail_du_delegue():
    print("\n=== intégration : la boucle voit passer le travail du délégué ===")
    w = neuf()
    drainer(w, [
        appel("toolu_ag", "Agent", {"subagent_type": "Explore",
                                    "description": "Explorer les tests"}),
        # Ce que le SDK remonte pendant que l'agent travaille : ses outils à lui, marqués
        # du `parent_tool_use_id` de la délégation. C'est CE champ qu'on ignorait.
        appel("toolu_1", "Read", {"file_path": "/x/test_front.js"}, parent="toolu_ag"),
        appel("toolu_2", "Grep", {"pattern": "titre", "path": "voix"}, parent="toolu_ag"),
        appel("toolu_3", "Read", {"file_path": "/x/worker.py"}),
    ])
    a = vus(w)[0]
    dire(a["fait"] == "cherché dans titre dans voix",
         f"la page sait ce que l'agent fait à cette seconde : {a['fait']!r}")
    dire(a["combien"] == 2, f"et combien d'actions il a menées : {a['combien']}")
    dire([n for n, _ in w.journal.outils] == ["Agent", "Read"],
         f"le journal du tour ne retient que SON travail : {w.journal.outils}")

    lignes = w._tableau.tous("outil")
    dire([bool(d["parent"]) for d in lignes] == [False, True, True, False],
         "chaque ligne du flux dit si elle vient d'un délégué — sans ça elles sont "
         "indiscernables de celles du tour principal")

    drainer(w, [retour("toolu_ag", "Voici mon rapport : quatre suites, toutes vertes."),
                fin()])
    dire(vus(w) == [], "le rapport rendu, la fiche s'éteint")


# --- 5. fonctionnel : le parcours tel qu'on le vit ---------------------------------------

def deux_agents_en_parallele_restent_distincts():
    print("\n=== fonctionnel : deux agents tournent, je vois lequel fait quoi ===")
    w = neuf()
    drainer(w, [
        appel("ag1", "Agent", {"subagent_type": "Explore", "description": "Lire les tests"}),
        appel("ag2", "Agent", {"subagent_type": "Plan", "description": "Chiffrer la reprise"}),
        appel("t1", "Read", {"file_path": "/x/test_tableau.py"}, parent="ag1"),
        appel("t2", "Bash", {"description": "Compter les lignes"}, parent="ag2"),
        appel("t3", "Read", {"file_path": "/x/test_fond.py"}, parent="ag1"),
    ])
    par_nom = {a["nom"]: a for a in vus(w)}
    dire(sorted(par_nom) == ["Chiffrer la reprise", "Lire les tests"],
         f"les deux sont là, chacun nommé : {sorted(par_nom)}")
    dire(par_nom["Lire les tests"]["fait"] == "lu test_fond.py"
         and par_nom["Chiffrer la reprise"]["fait"] == "lancé Compter les lignes",
         "et chacun dit ce qu'il fait, sans que l'un porte le travail de l'autre")
    dire(par_nom["Lire les tests"]["combien"] == 2
         and par_nom["Chiffrer la reprise"]["combien"] == 1,
         "le compte d'actions suit chacun séparément")
    dire(par_nom["Lire les tests"]["actions"] == ["lu test_tableau.py", "lu test_fond.py"],
         "et le chemin se déplie dans l'ordre où il a été parcouru")

    # Le premier rend son rapport ; le second travaille encore. C'est ce moment-là qu'on
    # ne voyait pas : la page montrait « un outil » et plus rien.
    drainer(w, [retour("ag1", "Quatre suites, toutes vertes."),
                appel("t4", "Read", {"file_path": "/x/agent.py"}, parent="ag2")])
    restants = vus(w)
    dire([a["nom"] for a in restants] == ["Chiffrer la reprise"],
         f"celui qui a fini s'efface, celui qui travaille reste : {[a['nom'] for a in restants]}")
    dire(restants[0]["fait"] == "lu agent.py", "et il continue d'être suivi")

    drainer(w, [retour("ag2", "Trois jours."), fin(2)])
    dire(vus(w) == [], "les deux finis, la pastille s'éteint pour de bon")


def main():
    une_delegation_ouvre_une_fiche_nommee()
    chaque_action_va_a_son_delegue()
    l_historique_est_borne_mais_le_compte_est_juste()
    un_delegue_au_premier_plan_s_eteint_en_rendant_son_rapport()
    un_delegue_en_fond_reste_allume_jusqu_a_sa_notification()
    un_tour_interrompu_ne_laisse_rien_allume()
    la_boucle_rattache_le_travail_du_delegue()
    deux_agents_en_parallele_restent_distincts()
    print("\nTOUT VERT" if ok else "\nDES ECHECS")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
