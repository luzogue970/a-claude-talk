#!/usr/bin/env python3
"""Une photo jointe doit se VOIR après l'envoi, et se servir sans ouvrir la machine.

Signalé le 23 novembre 2026, mot pour mot : « j'ai envoyé deux pièces jointes, mais on ne
voit pas en tant qu'utilisateur quand on a envoyé une pièce jointe. Je suppose qu'elles ont
été envoyées, mais en réalité je n'en sais rien. »

Les vignettes du composeur sont libérées à l'envoi, et un rejeu d'historique n'en a jamais
eu : après coup, une ligne « toi » partie avec deux photos était indiscernable d'une ligne
partie seule. Ce fichier tient les trois pièces du correctif :

1. **Ce qui part est NOMMÉ.** `_avec_jointes` rend la liste des fichiers vivants, et c'est
   elle qui va dans l'événement — pas un booléen, pas un compte reconstruit ailleurs.
2. **Un chemin mort ne compte pas.** Il était déjà signalé ; il ne doit pas en plus être
   affiché comme une photo partie.
3. **La route qui sert la photo refuse tout ce qui sort du dossier des photos.** Le chemin
   vient du flux, donc du serveur — mais il transite par le navigateur, donc il ne vaut
   rien. Sans cette borne, `?c=/etc/passwd` servirait ce fichier à qui atteint la page.
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import aiohttp

from tableau import Tableau

ok = True


def dire(bon, texte):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


class FauxAgent:
    """Juste ce que `_avec_jointes` touche : la liste en attente et le flux d'événements."""

    def __init__(self, chemins):
        self._jointes = list(chemins)
        self.vus = []

    def _voir(self, genre, **champs):
        self.vus.append((genre, champs))


def ce_qui_part_est_nomme():
    print("\n=== ce qui part avec la phrase est nommé, pas supposé ===")
    from agent import Voix

    with tempfile.TemporaryDirectory() as d:
        a = Path(d) / "a.png"
        b = Path(d) / "b.jpg"
        a.write_bytes(b"\x89PNG")
        b.write_bytes(b"\xff\xd8\xff")

        faux = FauxAgent([str(a), str(b)])
        texte, parties = Voix._avec_jointes(faux, "regarde ces deux captures")
        dire(parties == [str(a), str(b)],
             "les deux photos envoyées sont rendues à l'appelant, dans l'ordre")
        dire(str(a) in texte and str(b) in texte,
             "et elles sont toujours accolées à la phrase pour Claude")
        dire(faux._jointes == [], "le composeur est vidé : la photo suivante repart de zéro")

        # Un chemin mort est déjà signalé à l'écran ; l'afficher en plus comme une photo
        # partie serait le pire des deux mondes — on croirait qu'elle est passée.
        faux = FauxAgent([str(a), str(Path(d) / "disparue.png")])
        _, parties = Voix._avec_jointes(faux, "et celle-ci")
        dire(parties == [str(a)],
             "un chemin mort ne figure pas parmi les photos parties")
        dire(any(g == "erreur" for g, _ in faux.vus),
             "et il se dit à l'écran au lieu de disparaître en silence")

        faux = FauxAgent([])
        texte, parties = Voix._avec_jointes(faux, "sans photo")
        dire(texte == "sans photo" and parties == [],
             "un message sans photo repart intact, sans liste vide à afficher")


def la_ligne_toi_porte_la_liste():
    print("\n=== l'événement « toi » emporte la liste, sinon la page ne peut rien montrer ===")
    source = Path(__file__).with_name("agent.py").read_text(encoding="utf-8")
    debut = source.index("def vu(")
    dire("jointes=" in source[debut:debut + 400],
         "la ligne « toi » publie le champ `jointes` — c'est le seul canal vers la page")
    dire("envoi, parties = self._avec_jointes(texte)" in source,
         "et ce champ vient de l'envoi réel, pas d'une relecture du texte")
    dire(source.index("envoi, parties") < source.index("vu(parties)"),
         "dans cet ordre : on ne peut pas publier ce qui n'est pas encore parti")

    page = Path(__file__).with_name("tableau.py").read_text(encoding="utf-8")
    dire("jointes-vues" in page and "urlJointe" in page,
         "la page sait dessiner les vignettes de ce champ")


async def la_route_ne_sert_que_le_dossier_des_photos():
    print("\n=== servir la photo, et RIEN d'autre ===")
    with tempfile.TemporaryDirectory() as d:
        images = Path(d) / "images"
        images.mkdir()
        photo = images / "capture.png"
        photo.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 40)
        secret = Path(d) / "secret.txt"
        secret.write_text("mot de passe", encoding="utf-8")

        ancien = os.environ.get("VOIX_IMAGES")
        os.environ["VOIX_IMAGES"] = str(images)
        t = Tableau(port=7899, ouvrir=False)
        url = await t.demarrer()
        try:
            async with aiohttp.ClientSession() as http:
                async with http.get(f"{url}/jointe", params={"c": str(photo)}) as r:
                    corps = await r.read()
                dire(r.status == 200 and corps.startswith(b"\x89PNG"),
                     "la photo jointe se sert : la vignette s'affiche après coup, "
                     "et au rejeu d'une conversation d'il y a trois jours")

                async with http.get(f"{url}/jointe", params={"c": str(secret)}) as r:
                    dire(r.status == 404,
                         "un fichier hors du dossier des photos n'est pas servi")

                evasion = str(images / ".." / "secret.txt")
                async with http.get(f"{url}/jointe", params={"c": evasion}) as r:
                    dire(r.status == 404,
                         "et un « .. » n'y ramène pas : le chemin est résolu avant d'être jugé")

                async with http.get(f"{url}/jointe", params={"c": ""}) as r:
                    dire(r.status == 400, "un chemin vide se refuse au lieu de deviner")

                async with http.get(f"{url}/jointe",
                                    params={"c": str(images / "jamais.png")}) as r:
                    dire(r.status == 404,
                         "une photo effacée depuis rend 404, elle ne fait pas tomber la page")
        finally:
            await t.arreter()
            if ancien is None:
                os.environ.pop("VOIX_IMAGES", None)
            else:
                os.environ["VOIX_IMAGES"] = ancien


async def principal():
    ce_qui_part_est_nomme()
    la_ligne_toi_porte_la_liste()
    await la_route_ne_sert_que_le_dossier_des_photos()
    print(f"\n{'TOUT VERT' if ok else 'DES ECHECS'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
