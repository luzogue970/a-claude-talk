#!/usr/bin/env python3
"""Un gros résultat d'outil ne doit pas couper la conversation. Prouvé en vrai.

Le défaut, tel qu'il s'affichait à l'écran :

    une erreur a coupé l'exécution — la liaison avec Claude Code a lâché
    (Failed to decode JSON: JSON message exceeded maximum buffer size of 1048576 bytes)

Ce n'est pas une erreur de format malgré ce que son nom laisse croire : c'est un PLAFOND.
Le CLI écrit un message JSON par ligne, et le résultat d'un outil voyage dans cette ligne —
le contenu d'une image lue en base64, la sortie d'une commande bavarde. Le SDK refuse toute
ligne de plus d'un mégaoctet, et il la refuse en levant, donc en tuant le flux : la liaison
tombe au milieu du tour et le travail en cours est perdu.

Une photo de téléphone de 3 Mo en fait 4 une fois encodée. Quatre fois le plafond. Autant
dire que montrer une capture d'écran était une façon fiable de casser la conversation.

Ce fichier appelle le VRAI Claude Code, parce que c'est la seule façon de prouver la chose :
le plafond vit dans le transport du SDK, et aucun objet factice ne le reproduit. Il demande
une commande dont la sortie dépasse largement l'ancien défaut, et vérifie que le tour va
jusqu'à son bilan.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

ok = True

# Pourquoi une IMAGE et pas une commande bavarde : le CLI tronque les sorties d'outils
# textuelles, bien en dessous du plafond — une sortie de deux mégaoctets arrive en deux
# kilo-octets, et le défaut ne se reproduit pas. Une image, elle, traverse ENTIÈRE, en
# base64, dans une seule ligne du flux. C'est précisément pour ça que montrer une capture
# d'écran était une façon fiable de casser la conversation, et que rien d'autre ne l'était.
COTE = 2600          # une photo de téléphone, en plus petit : ~3 Mo de PNG bruité


def dire(bon, texte):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


def fabriquer_image(cible: Path) -> int:
    """Une image volontairement incompressible, donc lourde. Rend sa taille en octets."""
    import os as _os
    from PIL import Image
    img = Image.frombytes("RGB", (COTE, COTE), _os.urandom(COTE * COTE * 3))
    img.save(cible, format="PNG", compress_level=0)
    return cible.stat().st_size


async def un_tour(dossier: str, tampon: int | None) -> tuple[str, int]:
    """Fait lire une grosse image. Rend (comment ça s'est fini, octets du résultat)."""
    from claude_agent_sdk import (ClaudeAgentOptions, ClaudeSDKClient, ResultMessage,
                                  UserMessage, ToolResultBlock)
    import config

    options = ClaudeAgentOptions(
        model="claude-haiku-4-5",
        cwd=dossier,
        cli_path=config.claude_binary(),
        permission_mode="bypassPermissions",
        max_buffer_size=tampon,
    )
    vus = 0
    try:
        async with ClaudeSDKClient(options) as client:
            await client.query(
                f"Lis le fichier {dossier}/grosse.png avec l'outil Read, "
                "puis réponds seulement « vu ». N'utilise aucun autre outil.")
            async for message in client.receive_response():
                if isinstance(message, UserMessage):
                    for bloc in (message.content if isinstance(message.content, list) else []):
                        if isinstance(bloc, ToolResultBlock):
                            vus = max(vus, len(str(bloc.content or "")))
                if isinstance(message, ResultMessage):
                    return message.subtype or "success", vus
    except Exception as exc:
        return f"levée: {type(exc).__name__}: {exc}"[:160], vus
    return "flux terminé sans bilan", vus


async def principal():
    import config

    dossier = tempfile.mkdtemp(prefix="tampon-")
    taille = fabriquer_image(Path(dossier) / "grosse.png")
    print(f"image d'essai : {taille / 1e6:.1f} Mo sur disque, "
          f"~{taille * 4 / 3 / 1e6:.1f} Mo une fois encodée dans le flux")

    print("\n=== l'ancien défaut casse bien, et c'est ce qu'on a vu à l'écran ===")
    casse, _ = await un_tour(dossier, 1024 * 1024)
    dire("buffer size" in casse,
         f"avec le plafond d'un mégaoctet, le flux lâche → {casse[:110]}")

    print("\n=== le MÊME tour, avec le plafond de l'application, va jusqu'au bout ===")
    # La preuve tient dans la comparaison, pas dans un seuil : même image, même demande,
    # même modèle — seul le plafond change, et le tour passe de « liaison perdue » à
    # « terminé ». Mesurer le bloc de résultat ne prouverait rien : le CLI réduit l'image
    # avant de la transmettre, donc ce bloc est plus petit que la ligne qui a cassé.
    fin, vus = await un_tour(dossier, config.MAX_TAMPON)
    dire(fin == "success", f"le tour se termine normalement → {fin[:110]}")
    dire(vus > 0, f"et l'image est bien arrivée jusqu'au modèle ({vus} octets de résultat)")

    print("\n=== le réglage est bien celui que l'application envoie au SDK ===")
    from worker import Worker
    w = Worker.__new__(Worker)
    w.session_id = None
    w.reprise = None
    w.effort = "medium"
    w.modele = config.WORKER_MODEL
    w._can_use_tool = None
    dire(w._options().max_buffer_size == config.MAX_TAMPON,
         f"les options portent le plafond ({config.MAX_TAMPON // 1024 // 1024} Mo)")
    dire(config.MAX_TAMPON >= 34 * 1024 * 1024,
         "et il couvre la plus grosse photo qu'on accepte, une fois encodée en base64")

    print(f"\n{'TOUT VERT' if ok else 'DES ECHECS'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
