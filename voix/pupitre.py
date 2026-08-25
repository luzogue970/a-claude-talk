"""Plusieurs conversations en parallele, un seul micro.

Le probleme, mesure : deux agents lances en meme temps se disputent le peripherique
d'entree. Les deux entendent la meme phrase, les deux la transcrivent, les deux l'envoient a
leur Claude — et les deux consomment la meme fenetre de rate limit. On avertissait ; ca ne
suffit pas, parce que travailler sur trois sujets en parallele est un usage legitime.

Deux mecanismes, volontairement separes :

- **Un registre** : un fichier par agent vivant, avec son projet, son dossier et le port de
  son tableau. C'est ce qui permet a chaque page de savoir quelles autres conversations
  existent et d'y aller.
- **Un bail sur le micro** : un seul agent ecoute a la fois. Le bail se prend, il ne se
  demande pas — prendre le micro sur une conversation le retire aux autres, immediatement.

Ce qui n'est PAS coupe chez les autres : leur travail. Claude continue sa tache, son tableau
continue de la montrer. Seule l'ecoute s'arrete, parce que c'est la seule ressource
reellement exclusive.

L'autorite sur « cet agent est-il vivant » est le PID, verifie avec sa ligne de commande :
un fichier oublie par un `kill -9` ne doit pas bloquer le micro pour toujours.
"""

import json
import os
import time
from pathlib import Path

RACINE = Path.home() / ".config" / "claude-talk"
SESSIONS = RACINE / "sessions"
BAIL = RACINE / "micro.json"
PAROLE = RACINE / "parole.json"
# Un bail de parole abandonne en cours de lecture bloquerait les autres. Passe ce delai, il
# est considere comme perime : aucune reponse parlee ne dure trois minutes.
PAROLE_MAX = 180.0
SIGNATURE = "voix/agent.py"


def _vivant(pid) -> bool:
    """Ce PID est-il encore un agent vocal ?

    Double verification, comme ailleurs : un PID libere est reattribue, et un navigateur qui
    herite du numero ne doit pas passer pour une conversation en cours.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace")
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return False
    return SIGNATURE in cmd


def _ecrire(chemin: Path, donnees: dict):
    """Ecriture atomique : un fichier temporaire puis un rename.

    Sans ca, une page qui lit pendant qu'un agent ecrit tombe sur du JSON tronque — et le
    resultat serait un micro qui change de main sur un fichier a moitie ecrit.
    """
    chemin.parent.mkdir(parents=True, exist_ok=True)
    tmp = chemin.with_suffix(chemin.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(donnees, ensure_ascii=False), encoding="utf-8")
    tmp.replace(chemin)


def _lire(chemin: Path) -> dict:
    try:
        return json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# --- le registre ------------------------------------------------------------------------
class Inscription:
    """La presence de CET agent dans le registre, et son bail sur le micro."""

    def __init__(self, projet: str, chemin: str, port: int):
        self.pid = os.getpid()
        self.projet = projet
        self.chemin = chemin
        self.port = port
        self.fichier = SESSIONS / f"{self.pid}.json"
        self._publier()

    def _publier(self):
        _ecrire(self.fichier, {
            "pid": self.pid, "projet": self.projet, "chemin": self.chemin,
            "port": self.port, "depuis": time.time(),
        })

    def majPort(self, port: int):
        """Le tableau peut avoir pris un autre port : le registre doit dire le vrai."""
        self.port = port
        self._publier()

    # --- la parole ------------------------------------------------------------
    # Deux voix en meme temps sur les memes haut-parleurs ne s'additionnent pas, elles
    # s'annulent : on ne comprend ni l'une ni l'autre. Celle qui arrive en second attend.
    def prendre_parole(self):
        _ecrire(PAROLE, {"pid": self.pid, "depuis": time.time()})

    def liberer_parole(self):
        if _lire(PAROLE).get("pid") == self.pid:
            try:
                PAROLE.unlink()
            except OSError:
                pass

    def parole_ailleurs(self) -> int | None:
        """Le PID d'une AUTRE conversation en train de parler, ou None."""
        pid = qui_parle()
        return pid if pid is not None and pid != self.pid else None

    # --- le bail --------------------------------------------------------------
    def prendre_micro(self) -> bool:
        """Prendre le micro. Renvoie True si le bail a change de main."""
        avant = detenteur()
        if avant == self.pid:
            return False
        _ecrire(BAIL, {"pid": self.pid, "pris_a": time.time()})
        return True

    def detient_micro(self) -> bool:
        """Ce processus a-t-il le droit d'ecouter, maintenant ?

        Un bail vacant — fichier absent, ou detenteur mort — revient au premier qui regarde.
        Sinon un `kill -9` sur l'agent qui ecoutait rendrait tous les autres sourds.
        """
        d = detenteur()
        if d is None:
            self.prendre_micro()
            return True
        return d == self.pid

    def liberer(self):
        """A la fermeture : rendre le bail et quitter le registre."""
        self.liberer_parole()
        if detenteur() == self.pid:
            try:
                BAIL.unlink()
            except OSError:
                pass
        try:
            self.fichier.unlink()
        except OSError:
            pass


# --- lecture, pour tout le monde ---------------------------------------------------------
def detenteur() -> int | None:
    """Le PID qui detient le micro, ou None si le bail est vacant."""
    pid = _lire(BAIL).get("pid")
    return pid if _vivant(pid) else None


def qui_parle() -> int | None:
    """Le PID de la conversation en train de lire une reponse, ou None.

    Trois raisons de rendre None : personne, un detenteur mort, ou un bail perime. Le
    troisieme cas existe parce qu'un agent tue en pleine lecture ne libere rien, et que le
    silence definitif des autres serait un prix absurde.
    """
    d = _lire(PAROLE)
    pid = d.get("pid")
    if not _vivant(pid):
        return None
    if time.time() - float(d.get("depuis") or 0) > PAROLE_MAX:
        return None
    return pid


def sessions() -> list[dict]:
    """Les agents vivants, le plus ancien d'abord.

    Nettoie au passage les fichiers laisses par des processus morts : un registre qui grossit
    de fantomes finit par ne plus rien dire.
    """
    if not SESSIONS.is_dir():
        return []
    tenu = detenteur()
    parlant = qui_parle()
    vivants = []
    for f in SESSIONS.glob("*.json"):
        d = _lire(f)
        if _vivant(d.get("pid")):
            d["micro"] = d["pid"] == tenu
            d["parle"] = d["pid"] == parlant
            vivants.append(d)
        else:
            try:
                f.unlink()
            except OSError:
                pass
    vivants.sort(key=lambda d: d.get("depuis", 0))
    return vivants


def ceder_a(pid: int) -> bool:
    """Donner le micro a un autre agent vivant. Utilise par le tableau."""
    if not _vivant(pid):
        return False
    _ecrire(BAIL, {"pid": pid, "pris_a": time.time()})
    return True
