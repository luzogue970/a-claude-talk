#!/usr/bin/env python3
"""Une conversation va jusqu'au bout, ou elle dit pourquoi elle s'arrête.

Le défaut réparé, mesuré le 1er novembre 2026 sur tout le dossier `conversations/` : 52 tours
s'y sont terminés anormalement. Trente sur « l'API a répondu 429 », quinze sur une exécution
coupée en plein vol, cinq sur un message trop gros pour le tampon. **Aucun n'a jamais été
repris** — le code ne savait pas le faire. La conversation s'arrêtait là, et on la retrouvait
des heures plus tard avec « 16 tour(s) » pour toute explication.

Et un cas pire encore, relevé sur la conversation insnap du même jour : un tour de 178
actions, puis plus rien. Cinq messages envoyés derrière, aucune réponse, quatre-vingt-dix
minutes de silence total. Rien n'avait levé d'exception, rien n'avait fermé de flux — le tour
était simplement figé, et personne ne regardait.

Trois garanties, et ce fichier les tient :

1. **Une panne technique repart toute seule**, jusqu'à trois fois, sans rien me redemander et
   sans me coûter une écoute.
2. **Une décision ne repart pas.** Un plafond de dépense ou un refus sont des réponses, pas
   des accidents : les reprendre ferait dépenser contre mon gré.
3. **Un tour figé est repéré et cassé.** Ne pas lever d'exception n'est pas une preuve de
   bonne santé : vingt minutes sans un seul message, c'est mort.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock  # noqa: E402

ok = True


def dire(bon, texte):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


class TableauFactice:
    def __init__(self):
        self.lignes = []

    def publier(self, genre, **donnees):
        self.lignes.append((genre, donnees))

    def genres(self):
        return [g for g, _ in self.lignes]

    def texte(self, genre):
        return " ".join(str(d.get("texte", "")) for g, d in self.lignes if g == genre)


class ClientScenarise:
    """Un client qui joue une scène par message envoyé.

    C'est la doublure qu'il faut pour cette famille de pannes : ce qu'on veut vérifier n'est
    pas un appel, c'est un ENCHAÎNEMENT — un tour qui casse, une reprise qui part seule, et
    le tour suivant qui aboutit.
    """

    def __init__(self, scenes):
        self.scenes = [list(s) for s in scenes]
        self.envoyes = []
        self.file = asyncio.Queue()

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def query(self, texte):
        self.envoyes.append(texte)
        for message in (self.scenes.pop(0) if self.scenes else []):
            await self.file.put(message)

    async def receive_messages(self):
        while True:
            yield await self.file.get()


def resultat(subtype="success", api=None, texte=None, tours=1):
    return ResultMessage(subtype=subtype, duration_ms=1200, duration_api_ms=1000,
                         is_error=subtype != "success", num_turns=tours,
                         session_id="s-1", total_cost_usd=0.01,
                         errors=([texte] if texte else []), api_error_status=api)


def parole(texte):
    return AssistantMessage(content=[TextBlock(text=texte)], model="claude-opus-5")


async def attendre(predicat, limite=3.0):
    """Attendre un fait, pas une durée : un test qui dort est un test qui ment un jour."""
    fin = asyncio.get_running_loop().time() + limite
    while asyncio.get_running_loop().time() < fin:
        if predicat():
            return True
        await asyncio.sleep(0.01)
    return predicat()


# ---------------------------------------------------------------------------- TU
def ce_qui_merite_une_reprise():
    """Une panne de transport se refait. Une décision, jamais."""
    import worker as W

    print("=== ce qui repart tout seul, et ce qui ne repart pas ===")
    cas = [
        (W.Journal(fin="error_during_execution"), True,
         "une exécution coupée en plein vol — c'est l'accident le plus fréquent"),
        (W.Journal(fin="error_during_execution", api_statut=429), True,
         "une limite de débit : trente de mes arrêts sur cinquante-deux venaient de là"),
        (W.Journal(fin="success", api_statut=500), True,
         "une erreur HTTP compte même si le sous-type n'a pas l'air alarmant"),
        (W.Journal(fin="error_max_turns"), True,
         "le plafond de tours interne atteint : lui redonner la main le fait finir"),
        (W.Journal(fin="success"), False,
         "un tour réussi ne se refait pas — ce serait payer deux fois la même réponse"),
        (W.Journal(fin="error_max_budget_usd"), False,
         "le plafond de dépense est une décision, pas une panne : on ne passe pas outre"),
        (W.Journal(fin="refusal"), False, "un refus est une réponse, pas un accident"),
        (W.Journal(fin="max_tokens"), False,
         "une réponse tronquée se relance à la main, pas en boucle"),
        (W.Journal(fin="un_sous_type_inconnu"), False,
         "un sous-type que le SDK inventera demain ne déclenche pas de boucle payante"),
    ]
    for journal, attendu, quoi in cas:
        dire(W.reprenable(journal) is attendu, quoi)


def on_ne_reprend_pas_indefiniment():
    """Trois reprises, puis on rend la parole — en disant qu'on a essayé."""
    import worker as W

    print("\n=== la reprise est bornée, et l'abandon se dit ===")
    w = W.Worker.__new__(W.Worker)
    w.arret_demande = False

    mort = W.Journal(question="compile le projet", fin="error_during_execution", api_statut=429)
    dire(w._relancer_ou_finir(mort, arret_voulu=True) is False,
         "un arrêt que j'ai demandé ne repart pas : c'est moi qui ai coupé")
    dire(w._relancer_ou_finir(W.Journal(fin="success"), False) is False,
         "un tour réussi ne déclenche aucune reprise")

    mort.relances = W.RELANCES_MAX
    dire(w._relancer_ou_finir(mort, False) is False,
         f"après {W.RELANCES_MAX} reprises on renonce au lieu de boucler")

    raison = mort.pourquoi_arrete()
    dire(raison and str(W.RELANCES_MAX) in raison and "repris" in raison,
         f"et l'abandon dit combien de fois on a essayé — « {raison} »")

    neuf = W.Journal(question="autre chose", fin="error_during_execution")
    dire(neuf.relances == 0,
         "le compteur voyage avec le tour : une nouvelle demande repart avec trois essais")

    # La borne ne vaut que par sa taille. Reprendre vingt fois avec des attentes qui montent,
    # c'est une heure de silence avant d'apprendre que c'etait foutu — exactement la panne
    # qu'on repare, en plus cher.
    pire = sum(W.ATTENTES_RELANCE[:W.RELANCES_MAX])
    dire(1 <= W.RELANCES_MAX <= 5 and pire <= 10 * 60,
         f"et au pire j'apprends l'échec en {pire // 60} min, pas au bout d'une heure")
    dire(len(W.ATTENTES_RELANCE) >= W.RELANCES_MAX and
         list(W.ATTENTES_RELANCE) == sorted(W.ATTENTES_RELANCE) and W.ATTENTES_RELANCE[0] > 0,
         "les attentes montent et ne sont jamais nulles : un 429 ne se vide pas en zéro seconde")


# ---------------------------------------------------------------------------- TI
async def une_reprise_ne_perd_pas_le_travail_fait():
    """Ce qui était déjà fait avant la coupure reste dans le bilan."""
    import worker as W

    print("\n=== repartir n'efface pas ce qui était déjà fait ===")
    tab = TableauFactice()
    w = W.Worker(tableau=tab)
    w.client = ClientScenarise([[]])

    mort = W.Journal(question="range le dossier et lance les tests")
    mort.outils = [("Bash", "ls"), ("Edit", "worker.py")]
    mort.textes = ["j'ai rangé le dossier"]
    mort.refus = ["Write"]
    mort.fin, mort.api_statut = "error_during_execution", 429

    await w.envoyer(W.RELANCE_APRES_PANNE.format(raison="l'API a répondu 429"), reprise=mort)

    dire(w.journal.question == "range le dossier et lance les tests",
         "le bilan parlera de ce que J'AI demandé, pas de la phrase technique de reprise")
    dire(w.journal.outils == mort.outils,
         "les deux actions déjà passées restent au compte : elles ont bien eu lieu")
    dire(w.journal.textes == ["j'ai rangé le dossier"],
         "ce qui avait déjà été dit est gardé pour être dit avec la suite")
    dire(w.journal.refus == ["Write"], "un refus de permission ne disparaît pas dans la reprise")
    dire(w.journal.relances == 1, "et le compteur monte, sinon la borne ne bornerait rien")
    dire(w.journal.outils is not mort.outils,
         "les listes sont recopiées : le tour mort ne doit plus bouger derrière nous")
    dire(W.RELANCE_APRES_PANNE.format(raison="x") in
         " ".join(t.replace("x", "x") for t in w.client.envoyes) or
         any("reprise automatique" in t for t in w.client.envoyes),
         "et la consigne envoyée à Claude lui dit de reprendre sans me raconter l'incident")


async def un_429_repart_sans_me_coûter_une_ecoute():
    """Le cas le plus fréquent, de bout en bout dans la vraie pompe."""
    import worker as W

    print("\n=== une limite de débit repart seule, en silence pour la voix ===")
    attentes, W.ATTENTES_RELANCE = W.ATTENTES_RELANCE, (0, 0, 0)
    try:
        tab = TableauFactice()
        w = W.Worker(tableau=tab)
        w.client = ClientScenarise([
            [parole("je commence"), resultat("error_during_execution", api=429)],
            [parole("et voilà, c'est fini"), resultat("success")],
        ])
        pompe = asyncio.create_task(w._drainer())
        await w.envoyer("compile le projet")

        recu = []

        async def ecouter():
            while True:
                recu.append(await w.events.get())

        oreille = asyncio.create_task(ecouter())
        await attendre(lambda: any(g == "fin" for g, _ in recu))
        oreille.cancel()
        pompe.cancel()

        fins = [j for g, j in recu if g == "fin"]
        dire(len(fins) == 1,
             f"je n'entends qu'UN bilan, pas un échec puis une réponse ({len(fins)})")
        dire(fins and fins[0].fin == "success",
             "et ce bilan est celui du tour qui a abouti")
        dire(fins and fins[0].question == "compile le projet",
             "il parle de ma demande, pas de la phrase de reprise")
        dire(fins and fins[0].relances == 1,
             "il garde la trace qu'il a fallu s'y reprendre à deux fois")
        dire(fins and fins[0].textes == ["je commence", "et voilà, c'est fini"],
             "et il dit TOUT ce qui a été dit, des deux côtés de la coupure")
        dire("reprise_auto" in tab.genres(),
             "la page montre la reprise : ce n'est pas silencieux, c'est juste pas parlé")
        dire("erreur" not in tab.genres(),
             "mais rien ne crie à l'incident sur un accident déjà réparé")
        dire(len(w.client.envoyes) == 2, "et Claude a bien reçu une deuxième demande")
    finally:
        W.ATTENTES_RELANCE = attentes


async def trois_echecs_rendent_la_parole():
    """Si ça ne repart vraiment pas, on me le dit. Jamais de silence définitif."""
    import worker as W

    print("\n=== quand les reprises échouent toutes, on me le dit ===")
    attentes, W.ATTENTES_RELANCE = W.ATTENTES_RELANCE, (0, 0, 0)
    try:
        tab = TableauFactice()
        w = W.Worker(tableau=tab)
        w.client = ClientScenarise([
            [resultat("error_during_execution", api=429)] for _ in range(W.RELANCES_MAX + 1)
        ])
        pompe = asyncio.create_task(w._drainer())
        await w.envoyer("compile le projet")

        recu = []

        async def ecouter():
            while True:
                recu.append(await w.events.get())

        oreille = asyncio.create_task(ecouter())
        await attendre(lambda: any(g == "fin" for g, _ in recu), limite=5.0)
        oreille.cancel()
        pompe.cancel()

        fins = [j for g, j in recu if g == "fin"]
        dire(len(fins) == 1, f"un seul bilan, celui de l'abandon ({len(fins)})")
        dire(fins and fins[0].relances == W.RELANCES_MAX,
             f"après exactement {W.RELANCES_MAX} reprises, pas une de plus")
        raison = fins[0].pourquoi_arrete() if fins else ""
        dire(raison and "429" in raison and "repris" in raison,
             f"et la raison dit ce qui s'est passé ET qu'on a essayé — « {raison} »")
        dire(len(w.client.envoyes) == W.RELANCES_MAX + 1,
             "la demande est partie quatre fois en tout, puis on s'arrête")
    finally:
        W.ATTENTES_RELANCE = attentes


# ---------------------------------------------------------------------------- TF
async def un_tour_fige_ne_me_fait_pas_attendre_une_heure():
    """Le parcours de la conversation insnap : plus un message, et personne ne regardait."""
    import worker as W

    print("\n=== un tour qui ne dit plus rien finit par être cassé, et repart ===")
    seuils = (W.PAS_DE_VEILLE, W.SILENCE_SIGNALE_S, W.SILENCE_MAX_TOUR_S, W.ATTENTES_RELANCE)
    W.PAS_DE_VEILLE, W.SILENCE_SIGNALE_S, W.SILENCE_MAX_TOUR_S = 0.02, 0.05, 0.3
    W.ATTENTES_RELANCE = (0, 0, 0)
    try:
        tab = TableauFactice()
        w = W.Worker(tableau=tab)
        # Un client qui accepte la demande et ne répond JAMAIS : aucune exception, aucun
        # flux fermé. Rien de ce que le code savait détecter avant.
        w.client = ClientScenarise([[], [parole("repris et fini"), resultat("success")]])
        # La reprise passe par `debloquer`, qui reconstruit un client : on lui rend le même,
        # sinon le test parlerait à un vrai Claude Code.
        vrai_client, W.ClaudeSDKClient = W.ClaudeSDKClient, lambda *a, **k: w.client
        try:
            pompe = asyncio.create_task(w._drainer())
            await w.envoyer("écris la doc")
            dire(w.occupe is True, "au départ, le tour est bien en cours")

            recu = []

            async def ecouter():
                while True:
                    recu.append(await w.events.get())

            oreille = asyncio.create_task(ecouter())
            vu = await attendre(lambda: any(g == "fin" for g, _ in recu), limite=5.0)
            oreille.cancel()
            pompe.cancel()
        finally:
            W.ClaudeSDKClient = vrai_client

        dire(vu, "le blocage est repéré tout seul au lieu de durer quatre-vingt-dix minutes")
        fins = [j for g, j in recu if g == "fin"]
        dire(len(fins) == 1 and fins[0].fin == "success",
             "et ce que j'entends est la réponse, pas le constat de panne")
        dire(fins and fins[0].question == "écris la doc",
             "le bilan parle toujours de ma demande")
        dire("reprise_auto" in tab.genres(), "la page dit que ça a été repris tout seul")
        dire(any("rien depuis" in d.get("texte", "") for g, d in tab.lignes if g == "log"),
             "et elle l'avait annoncé avant de couper, pour que je ne relance pas pour rien")
    finally:
        W.PAS_DE_VEILLE, W.SILENCE_SIGNALE_S, W.SILENCE_MAX_TOUR_S, W.ATTENTES_RELANCE = seuils


async def la_veille_ne_coupe_pas_un_tour_qui_travaille():
    """Un tour long mais vivant ne doit JAMAIS être cassé. C'est le risque de la veille."""
    import worker as W

    print("\n=== un tour long mais vivant n'est pas coupé ===")
    seuils = (W.PAS_DE_VEILLE, W.SILENCE_SIGNALE_S, W.SILENCE_MAX_TOUR_S)
    W.PAS_DE_VEILLE, W.SILENCE_SIGNALE_S, W.SILENCE_MAX_TOUR_S = 0.02, 0.05, 0.2
    try:
        tab = TableauFactice()
        w = W.Worker(tableau=tab)
        w.client = ClientScenarise([[]])
        pompe = asyncio.create_task(w._drainer())
        await w.envoyer("lance la suite complète")
        # Il parle régulièrement, comme un vrai tour qui enchaîne des outils pendant
        # longtemps : chaque message repousse l'échéance.
        for _ in range(25):
            await w.client.file.put(parole("toujours dessus"))
            await asyncio.sleep(0.02)
        encore = w.occupe
        await w.client.file.put(resultat("success"))
        await attendre(lambda: not w.occupe)
        pompe.cancel()

        dire(encore is True,
             "un demi-seuil de messages réguliers ne déclenche rien : on n'a pas coupé")
        dire("reprise_auto" not in tab.genres(), "et aucune reprise n'a été lancée pour rien")
    finally:
        W.PAS_DE_VEILLE, W.SILENCE_SIGNALE_S, W.SILENCE_MAX_TOUR_S = seuils


def la_fin_dit_dans_quel_etat_on_s_arrete():
    """« 16 tour(s) » ne dit pas si la conversation a fini ou si on l'a coupée."""
    import worker as W

    print("\n=== la dernière ligne du journal de bord dit l'état réel ===")
    w = W.Worker.__new__(W.Worker)
    w.pompe_morte, w.occupe = False, False
    w.journal = W.Journal()
    dire(w.etat_a_la_fermeture() == "session terminée",
         "une conversation qui dormait se clôt sobrement")

    w.occupe = True
    w.journal.outils = [("Bash", "npm test")]
    fin = w.etat_a_la_fermeture()
    dire("ENCORE en cours" in fin and "npm test" in fin,
         f"coupée en plein travail, elle le dit ET nomme le dernier geste — « {fin} »")

    w.occupe, w.pompe_morte = False, True
    dire("liaison" in w.etat_a_la_fermeture(),
         "et une liaison morte se lit dans le fichier, pas seulement dans les logs")


async def principal():
    ce_qui_merite_une_reprise()
    on_ne_reprend_pas_indefiniment()
    await une_reprise_ne_perd_pas_le_travail_fait()
    await un_429_repart_sans_me_coûter_une_ecoute()
    await trois_echecs_rendent_la_parole()
    await un_tour_fige_ne_me_fait_pas_attendre_une_heure()
    await la_veille_ne_coupe_pas_un_tour_qui_travaille()
    la_fin_dit_dans_quel_etat_on_s_arrete()
    print(f"\n{'TOUT VERT' if ok else 'DES ECHECS'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
