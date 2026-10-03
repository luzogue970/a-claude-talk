#!/usr/bin/env python3
"""Reconstruire le client ne doit JAMAIS perdre la conversation en cours.

Le defaut, tel qu'il s'est produit : on lance l'application dans un dossier, elle annonce
qu'elle reprend la conversation precedente, on pose sa premiere question — et le tableau
affiche « la reprise de 8d9102a8 n'a PAS pris ». Une ligne rouge au lancement, une fois sur
deux, sans rien avoir fait de particulier.

La cause tient a un enchainement de deux secondes :

1. `start()` construit le client avec `resume=<la session a reprendre>`.
2. Le SDK ne rend l'identifiant de session qu'au PREMIER message. Tant que rien n'a ete
   envoye, `self.session_id` vaut None.
3. La premiere question declenche l'ajustement automatique de l'effort, qui reconstruit le
   client — avec `reprendre=self.session_id`, donc avec None.

Claude Code ouvrait alors une conversation neuve, et la reprise du demarrage partait a la
poubelle a l'instant precis ou on en avait besoin. Le message d'erreur etait exact : c'est ce
qu'il annonçait qui ne devait pas arriver.

Ce fichier verifie la regle qui le corrige : ce qu'on reconstruit reprend la session vivante,
et « session vivante » veut dire celle que le SDK nous a rendue OU, a defaut, celle qu'on a
demandee au lancement. Avec une exception : « nouvelle conversation » coupe les deux.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

ok = True


def dire(bon, texte):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


def principal():
    import config
    from worker import Worker

    w = Worker.__new__(Worker)          # sans __init__ : on teste la regle, pas la connexion
    w.session_id = None
    w.reprise = None

    print("=== la session vivante, selon ce qu'on sait a cet instant ===")
    dire(w._sid_vivant() is None, "rien de demande, rien d'ouvert : rien a reprendre")

    w.reprise = {"session_id": "aaaa1111-0000-0000-0000-000000000000"}
    dire(w._sid_vivant() == "aaaa1111-0000-0000-0000-000000000000",
         "reprise demandee mais session pas encore rendue : c'est elle qu'on reprend")

    w.session_id = "bbbb2222-0000-0000-0000-000000000000"
    dire(w._sid_vivant() == "bbbb2222-0000-0000-0000-000000000000",
         "une fois la session rendue, c'est elle qui prime")

    w.session_id = None
    w.reprise = None
    dire(w._sid_vivant() is None,
         "apres « nouvelle conversation », les deux sont coupes et on ne ressuscite rien")

    print("\n=== et les options construites ne reprennent que ce qu'on leur donne ===")
    w.session_id = None
    w.reprise = None
    w.effort = "moyen"
    w.modele = config.WORKER_MODEL
    w._can_use_tool = None
    ancien = config.REPRENDRE
    try:
        # Lance avec « vvreprendre <id> » : la demande ne doit pas se rejouer toute seule
        # a chaque reconstruction, sinon « nouvelle conversation » ne fait rien.
        config.REPRENDRE = "cccc3333-0000-0000-0000-000000000000"
        dire(w._options(reprendre=None).resume is None,
             "demander explicitement aucune reprise donne bien aucune reprise")
        dire(w._options(reprendre="dddd4444").resume == "dddd4444",
             "et ce qu'on passe est ce qui part au SDK")
    finally:
        config.REPRENDRE = ancien

    print(f"\n{'TOUT VERT' if ok else 'DES ECHECS'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(principal())
