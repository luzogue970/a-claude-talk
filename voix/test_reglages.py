#!/usr/bin/env python3
"""Un réglage changé depuis le tableau vaut pour la conversation suivante — et pour celles
qui tournent déjà.

Ce que ces tests protègent, en une phrase : *je change le modèle par défaut une fois, à un
seul endroit, et je n'ai ni script shell à éditer ni conversation à relancer.*

Les deux moitiés du mécanisme sont ici :

- **les sessions neuves**, qui lisent `~/.config/claude-talk/reglages.json` au démarrage ;
- **la porte d'ordre HTTP** (`POST /commande`), par laquelle le tableau atteint les sessions
  déjà ouvertes sans ouvrir de WebSocket.

Le piège qui a motivé la moitié de ces vérifications : un fichier de réglages qui ÉCRASE
l'environnement. Lancer une session à part avec `VOIX_WORKER_MODEL=...` se serait alors fait
corriger en silence une seconde plus tard, et rien à l'écran ne l'aurait dit.
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

ok = True


def dire(bon, texte):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


def _fichier(contenu, dossier):
    chemin = Path(dossier) / "reglages.json"
    chemin.write_text(json.dumps(contenu), encoding="utf-8")
    return chemin


async def principal():
    import reglages

    print("=== TU — un réglage devient une variable d'environnement lisible ===")
    dire(reglages._texte(True) == "1", "« vrai » s'écrit 1, pas True")
    dire(reglages._texte(False) == "0",
         "« faux » s'écrit 0 — écrit « False », config.py l'aurait lu comme vrai")
    dire(reglages._texte(5.0) == "5", "5.0 s'écrit « 5 », comme la page l'affiche")
    dire(reglages._texte(7.5) == "7.5", "une demi-seconde ne se perd pas en route")
    dire(reglages._texte("claude-opus-5") == "claude-opus-5", "un texte passe tel quel")

    print("\n=== TU — un fichier illisible ne fait pas tomber le démarrage ===")
    with tempfile.TemporaryDirectory() as d:
        reglages.FICHIER = Path(d) / "absent.json"
        dire(reglages.lire() == {}, "pas de fichier : aucun réglage, et la session démarre")
        casse = Path(d) / "casse.json"
        casse.write_text('{"modele": "claude-opus', encoding="utf-8")
        reglages.FICHIER = casse
        dire(reglages.lire() == {},
             "fichier tronqué : on repart sur les valeurs d'origine plutôt que de lever")
        liste = Path(d) / "liste.json"
        liste.write_text("[1, 2]", encoding="utf-8")
        reglages.FICHIER = liste
        dire(reglages.lire() == {}, "du JSON valide mais pas un objet ne devient pas un réglage")

    print("\n=== TU — l'environnement posé à la main gagne toujours sur le fichier ===")
    garde = {c: os.environ.get(v) for c, v in reglages.VARIABLES.items()}
    try:
        for variable in reglages.VARIABLES.values():
            os.environ.pop(variable, None)
        os.environ["VOIX_WORKER_MODEL"] = "claude-haiku-4-5"
        reglages.appliquer({"modele": "claude-opus-5", "effort": "xhigh",
                            "effort_auto": False, "ecoute_min": 8.0})
        dire(os.environ["VOIX_WORKER_MODEL"] == "claude-haiku-4-5",
             "une session lancée à la main garde SON modèle, le réglage général ne l'écrase pas")
        dire(os.environ["VOIX_WORKER_EFFORT"] == "xhigh",
             "ce que l'environnement ne dit pas, le fichier le dit")
        dire(os.environ["VOIX_EFFORT_AUTO"] == "0", "une bascule coupée arrive bien à 0")
        dire(os.environ["VOIX_ECOUTE_MIN"] == "8", "le délai d'envoi arrive en secondes")

        # Une variable presente mais VIDE est un oubli, pas un choix. Avant ce garde-fou,
        # un `export VOIX_ECOUTE_MIN=` oublie quelque part bloquait le reglage ET faisait
        # tomber config.py a l'import sur `float("")` : la session ne demarrait pas, sur un
        # message qui ne nommait ni la variable ni le script fautif.
        os.environ["VOIX_WORKER_EFFORT"] = ""
        reglages.appliquer({"effort": "max"})
        dire(os.environ["VOIX_WORKER_EFFORT"] == "max",
             "une variable vide laissée dans un script ne bloque pas le réglage")
    finally:
        for cle, valeur in garde.items():
            variable = reglages.VARIABLES[cle]
            if valeur is None:
                os.environ.pop(variable, None)
            else:
                os.environ[variable] = valeur

    print("\n=== TU — toute variable réglable est une variable que config.py lit vraiment ===")
    source = (Path(__file__).parent / "config.py").read_text(encoding="utf-8")
    for cle, variable in reglages.VARIABLES.items():
        dire(f'"{variable}"' in source,
             f"{cle} → {variable} est lu par config.py (sinon le bouton ne fait rien)")

    print("\n=== TI — config.py applique le fichier au démarrage d'une session ===")
    # Le vrai chemin, celui qu'emprunte une conversation neuve : on pose un fichier, on
    # importe config dans un interpréteur à part, et on lui demande ce qu'il a retenu.
    with tempfile.TemporaryDirectory() as d:
        chemin = _fichier({"modele": "claude-sonnet-5", "effort": "high",
                           "ecoute_min": 3.0, "permission": "acceptEdits"}, d)
        milieu = dict(os.environ, VOIX_REGLAGES=str(chemin))
        for variable in reglages.VARIABLES.values():
            milieu.pop(variable, None)
        code = ("import config, json; print(json.dumps({"
                "'modele': config.WORKER_MODEL, 'effort': config.WORKER_EFFORT,"
                "'ecoute': config.ECOUTE_MIN, 'permission': config.PERMISSION}))")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", code, cwd=str(Path(__file__).parent),
            env=milieu, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        sortie, erreur = await proc.communicate()
        if proc.returncode != 0:
            dire(False, f"config.py n'a pas démarré : {erreur.decode()[-400:]}")
        else:
            vu = json.loads(sortie.decode().strip().splitlines()[-1])
            dire(vu["modele"] == "claude-sonnet-5",
                 "une conversation neuve démarre sur le modèle réglé dans le tableau")
            dire(vu["effort"] == "high", "… et sur son niveau d'effort")
            dire(vu["ecoute"] == 3.0, "… et sur son délai d'envoi")
            dire(vu["permission"] == "acceptEdits", "… et sur son mode de permission")

    print("\n=== TI — la porte d'ordre HTTP mène au même endroit que la page ===")
    from aiohttp import web  # noqa: F401  (présent dès que l'agent l'est)
    import tableau as mod_tableau

    recus = []

    t = mod_tableau.Tableau(port=7998, ouvrir=False)

    async def gestionnaire(nom, donnees):
        recus.append((nom, donnees))

    t.on_commande = gestionnaire
    await t.demarrer()
    try:
        import aiohttp
        base = f"http://127.0.0.1:{t.port}"
        async with aiohttp.ClientSession() as http:
            async with http.post(f"{base}/commande",
                                 json={"cmd": "modele", "cle": "claude-opus-5"}) as r:
                corps = await r.json()
            dire(r.status == 200 and corps.get("recu") is True,
                 "un ordre envoyé en HTTP est accepté et accusé")
            dire(recus == [("modele", {"cle": "claude-opus-5"})],
                 f"… et arrive à l'agent tel quel, sans le champ « cmd » → {recus}")

            async with http.post(f"{base}/commande", json={"cle": "opus"}) as r:
                dire(r.status == 400, "un ordre sans nom est refusé, pas avalé en silence")
            async with http.post(f"{base}/commande", data="pas du json") as r:
                dire(r.status == 400, "un corps illisible est refusé, pas avalé en silence")

            # Une commande qui lève ne doit emporter ni la réponse ni le serveur : c'est ce
            # qui arrivait sur la WebSocket avant le garde-fou de _executer.
            async def casse(nom, donnees):
                raise RuntimeError("volontaire")
            t.on_commande = casse
            async with http.post(f"{base}/commande", json={"cmd": "effort"}) as r:
                dire(r.status == 200, "une commande qui échoue répond quand même")
            async with http.get(f"{base}/etat.json") as r:
                dire(r.status == 200, "… et le serveur de l'agent est toujours debout")
    finally:
        await t.arreter()

    print("\n=== TF — le parcours complet : je règle, une session neuve obéit ===")
    with tempfile.TemporaryDirectory() as d:
        chemin = _fichier({"modele": "claude-haiku-4-5"}, d)
        reglages.FICHIER = chemin
        dire(reglages.lire()["modele"] == "claude-haiku-4-5",
             "le fichier écrit par le tableau se relit sans traduction")
        # Puis je change d'avis, sans rien relancer : le tableau réécrit, la lecture suit.
        _fichier({"modele": "claude-opus-5"}, d)
        dire(reglages.lire()["modele"] == "claude-opus-5",
             "le réglage suivant remplace le précédent, il ne s'y ajoute pas")

    print(f"\n{'TOUT VERT' if ok else 'DES ECHECS'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
