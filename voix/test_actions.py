"""Ce qu'une ligne « outil » raconte de ce que Claude est en train de faire.

Le defaut repare : le tableau affichait « Bash cd », « Grep def », « Bash cd ». On voyait que
ça travaillait, jamais sur quoi — et le bilan parle du tour disait la meme chose a voix haute.
Deux causes, et la seconde explique la premiere.

D'abord l'ordre des essais : le CLI FOURNIT une description en clair pour les commandes shell
et les delegations, et elle etait testee en DERNIER, donc jamais atteinte — la commande, plus
haut dans la liste, repondait toujours.

Ensuite le resume d'une commande : il gardait le premier mot. Or une commande commence
presque toujours par un changement de dossier, qui ne dit rien de ce qu'elle fait.
"""
import os, sys

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("VOIX_WORKDIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from worker import _cible, _verbe_shell, _LONGUEUR

ok = True


def dire(bon: bool, texte: str):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


def egal(nom: str, args: dict, attendu: str, propos: str):
    rendu = _cible(nom, args)
    dire(rendu == attendu, f"{propos} → {rendu!r}" + ("" if rendu == attendu else f" (attendu {attendu!r})"))


def la_description_passe_avant_tout():
    print("\n=== la phrase ecrite par Claude gagne sur tout le reste ===")
    # C'est le coeur du defaut : elle existait, elle etait juste inatteignable.
    egal("Bash",
         {"command": "cd /home/mathieu/dev/projet && grep -rn 'def _cible' voix/",
          "description": "Cherche la définition de la fonction de résumé"},
         "Cherche la définition de la fonction de résumé",
         "une commande decrite se lit par sa description")
    egal("Task",
         {"description": "Chercher les usages de la route /parler",
          "prompt": "Trouve tous les endroits qui appellent /parler"},
         "Chercher les usages de la route /parler",
         "une delegation aussi")


def une_commande_dit_ce_quelle_fait():
    print("\n=== sans description, on cherche le VERBE de la commande ===")
    # « cd » emporte son argument : sans ça on resumait par le nom du dossier, ce qui est
    # encore pire — on croit lire une action, on lit un chemin.
    egal("Bash", {"command": "cd /home/mathieu/dev/projet && npm test"},
         "npm test", "le changement de dossier s'efface")
    egal("Bash", {"command": "cd /a && cd /b && make install"},
         "make install", "meme enchaines")
    egal("Bash", {"command": "cd voix && .venv/bin/python -m pytest test_front.py -x"},
         "python -m pytest test_front.py -x", "le binaire perd son chemin")
    egal("Bash", {"command": "VOIX_STT=local .venv/bin/python voix/agent.py"},
         "python voix/agent.py", "les variables d'environnement s'effacent aussi")
    egal("Bash", {"command": "sudo systemctl restart claude-talk"},
         "systemctl restart claude-talk", "mais « sudo » n'emporte PAS son argument : il le prefixe")
    # Un tube fait partie de l'intention : le couper mutilerait la phrase.
    egal("Bash", {"command": "git status --porcelain | head -20"},
         "git status --porcelain | head -20", "un tube est garde entier")
    # Rien d'autre a dire : on rend la commande telle quelle plutot que rien.
    egal("Bash", {"command": "cd /a/b"}, "cd /a/b", "une commande qui n'est QUE un cd se rend telle quelle")
    dire(_verbe_shell("") == "", "une commande vide ne casse rien")


def une_recherche_dit_ou_elle_cherche():
    print("\n=== chercher quoi, et surtout OU ===")
    egal("Grep", {"pattern": "def _cible", "path": "/home/mathieu/dev/claude-talk/voix"},
         "def _cible dans voix", "l'endroit renseigne sur l'intention")
    egal("Grep", {"pattern": "AZURE_KEY"}, "AZURE_KEY", "sans endroit, le motif seul")
    egal("Glob", {"pattern": "**/*.py", "path": "voix"}, "**/*.py dans voix", "idem pour les fichiers")
    egal("Grep", {"pattern": "x", "path": "."}, "x", "un point n'est pas un endroit qu'on nomme")


def un_fichier_ne_se_lit_pas_en_entier():
    print("\n=== un fichier, et la portion qu'on en lit ===")
    egal("Read", {"file_path": "/home/mathieu/dev/claude-talk/voix/tableau.py"},
         "tableau.py", "le nom, jamais le chemin — personne ne veut l'entendre s'epeler")
    egal("Read", {"file_path": "/a/b/agent.py", "offset": 1200, "limit": 40},
         "agent.py, lignes 1200 à 1240",
         "relire deux lignes n'est pas relire le fichier, et ça se voit")
    egal("Edit", {"file_path": "/a/b/voix/worker.py"}, "worker.py", "une edition aussi")


def la_ligne_reste_une_ligne():
    print("\n=== bornee : cette ligne partage l'ecran avec la conversation ===")
    long = _cible("Bash", {"description": "x" * 300})
    dire(len(long) <= _LONGUEUR, f"une description interminable est tronquee ({len(long)} caracteres)")
    dire(long.endswith("…"), "et la troncature se voit, au lieu de faire croire a une fin")
    espaces = _cible("Bash", {"description": "deux   espaces\net un retour"})
    dire("\n" not in espaces and "   " not in espaces,
         f"les blancs sont normalises : {espaces!r} — un retour a la ligne casserait la mise en page")
    dire(_cible("Bash", {}) == "", "un outil sans rien d'exploitable rend une chaine vide")


def main():
    la_description_passe_avant_tout()
    une_commande_dit_ce_quelle_fait()
    une_recherche_dit_ou_elle_cherche()
    un_fichier_ne_se_lit_pas_en_entier()
    la_ligne_reste_une_ligne()
    print("\nTOUT VERT" if ok else "\nDES ECHECS")
    sys.exit(0 if ok else 1)


main()
