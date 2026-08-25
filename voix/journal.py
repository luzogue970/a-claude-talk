"""Persiste chaque conversation vocale : un transcript lisible, et de quoi la reprendre.

Le probleme resolu : une conversation finie ne laissait rien. Le tableau de bord vit en
memoire, la voix passe, et au prochain lancement on repartait de zero.

Deux choses sont conservees, et elles ne servent pas a la meme chose :

- **Un fichier Markdown par conversation**, pour la relire. C'est pour toi, pas pour la
  machine : ce qui a ete dit, ce qui a ete fait, dans l'ordre.
- **L'identifiant de session Claude Code**, pour la reprendre. Claude Code ecrit deja ses
  sessions sur disque — c'est ce qui fait marcher `--resume` — donc reprendre une
  conversation rend le contexte COMPLET, pas seulement ce que j'aurais recopie.

L'ecriture est incrementale : une coupure de courant en plein echange ne perd que le tour
en cours, pas la conversation.
"""

import json
import os
from datetime import datetime
from pathlib import Path

import config

RACINE = Path(config.JOURNAL_DIR)
INDEX = RACINE / "index.jsonl"


def _horodatage() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


class Conversation:
    """One vocal conversation, written as it happens."""

    def __init__(self, projet: str, modele: str, effort: str, reprise: str | None = None,
                 chemin: str | None = None):
        RACINE.mkdir(parents=True, exist_ok=True)
        self.debut = datetime.now()
        self.projet = projet
        # Le chemin complet, pas seulement le nom du dossier : deux projets peuvent
        # s'appeler « front », et c'est le chemin qui permet de remonter en premier les
        # conversations du dossier depuis lequel on regarde.
        self.chemin = chemin or os.getcwd()
        # Le PID est la seule autorite fiable sur « est-elle encore ouverte ». Un marqueur
        # de fermeture ne s'ecrit que si le processus meurt proprement — et les deux jours
        # de panne silencieuse etaient precisement deux processus zombies. Un PID, on peut
        # le verifier apres coup.
        self.pid = os.getpid()
        self.fin: str | None = None
        self.modele = modele
        self.effort = effort
        self.session_id: str | None = None
        self.reprise = reprise
        self.tours = 0
        nom = f"{self.debut:%Y-%m-%d_%H%M}_{projet}".replace("/", "-")
        self.fichier = RACINE / f"{nom}.md"
        self._entete()

    def _entete(self):
        lignes = [
            f"# Conversation — {self.projet}",
            "",
            f"- **début** {_horodatage()}",
            f"- **modèle** {self.modele}, effort {self.effort}",
        ]
        if self.reprise:
            lignes.append(f"- **reprise de** `{self.reprise}`")
        lignes += ["- **session** _(en attente du premier tour)_", ""]
        self.fichier.write_text("\n".join(lignes) + "\n", encoding="utf-8")

    def _ajouter(self, texte: str):
        with self.fichier.open("a", encoding="utf-8") as fh:
            fh.write(texte)

    # --- ce qui se passe ------------------------------------------------------
    def note_session(self, session_id: str):
        """Le SDK ne donne l'identifiant qu'au premier message : on le recolle apres coup."""
        if self.session_id or not session_id:
            return
        self.session_id = session_id
        contenu = self.fichier.read_text(encoding="utf-8").replace(
            "- **session** _(en attente du premier tour)_",
            f"- **session** `{session_id}`  \n"
            f"  reprendre : `vvreprendre {session_id}`")
        self.fichier.write_text(contenu, encoding="utf-8")
        self.indexer()

    def tour_utilisateur(self, texte: str):
        self.tours += 1
        self._ajouter(f"\n## {datetime.now():%H:%M} — toi\n\n{texte.strip()}\n")

    def tour_claude(self, parle: str, outils: list[str] | None = None,
                    jetons: int | None = None, duree: float | None = None):
        detail = []
        if outils:
            detail.append(f"{len(outils)} action(s) : " + ", ".join(outils))
        if duree is not None:
            detail.append(f"{duree:.0f} s")
        if jetons:
            detail.append(f"{jetons} jetons")
        entete = f"\n## {datetime.now():%H:%M} — claude\n\n"
        if detail:
            entete += f"*{' · '.join(detail)}*\n\n"
        self._ajouter(entete + (parle.strip() or "_(rien dit)_") + "\n")
        self.indexer()

    def ordre_local(self, quoi: str):
        self._ajouter(f"\n> ordre local : {quoi}\n")

    def clore(self, raison: str = ""):
        self.fin = datetime.now().isoformat(timespec="seconds")
        self._ajouter(f"\n---\n\n_fin {_horodatage()}"
                      + (f" — {raison}" if raison else "") + f", {self.tours} tour(s)._\n")
        self.indexer()

    # --- l'index -------------------------------------------------------------
    def indexer(self):
        """Append-only : le lecteur garde la derniere ligne de chaque fichier.

        Reecrire l'index a chaque tour inviterait a le corrompre sur une coupure ; ajouter
        ne peut que laisser une ligne incomplete, que le lecteur ignore."""
        ligne = {
            "fichier": self.fichier.name,
            "projet": self.projet,
            "chemin": self.chemin,
            "session_id": self.session_id,
            "debut": self.debut.isoformat(timespec="seconds"),
            "maj": datetime.now().isoformat(timespec="seconds"),
            "fin": self.fin,
            "pid": self.pid,
            "tours": self.tours,
            "modele": self.modele,
        }
        with INDEX.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ligne, ensure_ascii=False) + "\n")


SIGNATURE = "voix/agent.py"   # ce qu'on doit lire dans la ligne de commande d'un agent


def _vivant(pid) -> bool:
    """Ce PID est-il encore un agent vocal ?

    Deux verifications, pas une. Un PID libere est reattribue, donc « le processus 12345
    existe » ne veut pas dire « ma conversation tourne encore » — ca peut etre un navigateur.
    On lit donc aussi la ligne de commande. Sans ce second test, une conversation morte
    apparaitrait comme active des qu'un autre programme herite du numero, et l'avertissement
    de session unique deviendrait du bruit qu'on apprend a ignorer.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace")
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return False
    return SIGNATURE in cmd


def etat(d: dict) -> str:
    """« en cours », « fermée » ou « interrompue ».

    Le troisieme cas n'est pas un detail : un agent tue par un kill -9 ne laisse aucun
    marqueur de fermeture. Le presenter comme « fermee » serait faux, et comme « en cours »
    serait pire. Sa derniere activite connue reste la meilleure reponse disponible.
    """
    if _vivant(d.get("pid")):
        return "en cours"
    return "fermée" if d.get("fin") else "interrompue"


def historique(limite: int = 20, ici: str | None = None) -> list[dict]:
    """Les conversations, dedupliquees par fichier, celles d'ICI d'abord.

    Le tri repond a la question qu'on se pose vraiment en tapant la commande : « qu'est-ce
    que j'ai fait dans CE projet ». Une liste triee seulement par date noie les conversations
    du dossier courant sous celles de tous les autres.
    """
    if not INDEX.is_file():
        return []
    ici = os.path.realpath(ici or os.getcwd())
    par_fichier: dict[str, dict] = {}
    for ligne in INDEX.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            d = json.loads(ligne)
        except json.JSONDecodeError:
            continue  # ligne tronquee par une coupure : on l'ignore
        par_fichier[d.get("fichier", "?")] = d

    lignes = list(par_fichier.values())
    for d in lignes:
        d["etat"] = etat(d)
        chemin = d.get("chemin")
        if chemin:
            d["ici"] = os.path.realpath(chemin) == ici
        else:
            # Conversations anterieures a l'enregistrement du chemin. Le nom du projet est le
            # seul indice disponible : c'est le nom du dossier de travail, donc la comparaison
            # tombe juste dans le cas courant. On marque le rapprochement comme suppose, pour
            # que l'affichage ne presente pas une deduction comme un fait.
            d["ici"] = bool(d.get("projet")) and d["projet"] == os.path.basename(ici)
            d["suppose"] = d["ici"]
    # « d'ici » d'abord, puis la plus recemment active. Le booleen inverse parce que le tri
    # est descendant.
    lignes.sort(key=lambda d: (d["ici"], d.get("maj", "")), reverse=True)
    return lignes[:limite]


def actives(ici: str | None = None) -> list[dict]:
    """Les conversations dont l'agent tourne encore.

    Sert a l'avertissement de session unique. Deux agents en parallele ne se cassent pas
    mutuellement — le tableau bascule de port tout seul — mais ils se disputent le micro et
    les quotas, et c'est ainsi qu'on se retrouve avec un zombie qui mange la fenetre de rate
    limit sans que personne l'ecoute.
    """
    return [d for d in historique(200, ici) if d["etat"] == "en cours"]


SESSIONS = Path.home() / ".claude" / "projects"


def dossier_de_session(sid: str) -> str | None:
    """Le repertoire de travail dans lequel une session Claude Code a ete ouverte.

    C'est la piece qui manquait, et son absence etait un vrai piege : Claude Code range ses
    sessions PAR REPERTOIRE. Reprendre depuis un autre dossier ne donne pas une erreur, ca
    donne une session introuvable — donc une conversation qui repart de zero en silence,
    exactement ce qu'on croyait avoir repare.

    On lit la source d'autorite : le champ `cwd` du fichier de session lui-meme. Ca marche
    aussi pour les conversations anterieures a l'enregistrement du chemin dans notre index, et
    ca ne demande pas de deviner un chemin depuis un nom de dossier aplati (« a-b » peut venir
    de « a/b » comme de « a-b »).

    Le PREMIER cwd, pas le plus frequent : Claude Code change de repertoire en travaillant, et
    ce qu'on veut est celui d'ouverture.
    """
    if not sid or not SESSIONS.is_dir():
        return None
    for fichier in SESSIONS.glob(f"*/{sid}.jsonl"):
        try:
            with fichier.open(encoding="utf-8") as fh:
                for ligne in fh:
                    ligne = ligne.strip()
                    if not ligne:
                        continue
                    try:
                        d = json.loads(ligne)
                    except json.JSONDecodeError:
                        continue
                    if d.get("cwd"):
                        return d["cwd"]
        except OSError:
            continue
    return None


def compte_messages(sid: str, dossier: str | None = None) -> int:
    """Combien de messages Claude Code a sur disque pour cette session.

    Sert a verifier qu'une reprise va reellement recharger quelque chose, avant de lancer
    l'agent. Zero message veut dire que la reprise partira de rien, et il vaut mieux le savoir
    avant de parler pendant dix minutes a une session amnesique.
    """
    try:
        from claude_agent_sdk import get_session_messages
        return len(get_session_messages(sid, directory=dossier))
    except Exception:
        return -1


# Combien de messages de l'historique on rejoue au maximum sur le tableau. Une conversation
# de soixante tours en compte huit cents : tout rejouer remplirait la memoire de la page et
# chasserait la session en cours. On garde donc les plus RECENTS, et on dit ce qu'on a laisse.
REJEU_MAX = 400


def rejouer_session(sid: str, dossier: str | None = None,
                    limite: int = REJEU_MAX) -> tuple[list[dict], int]:
    """L'historique d'une session, converti en evenements du tableau.

    Reprendre une conversation rendait le contexte complet a Claude mais laissait la page
    VIDE : le tableau vit en memoire du processus, et un nouveau processus part de rien. On
    relit donc ce que Claude Code a ecrit sur disque, et on le republie.

    La forme compte autant que le contenu. Une premiere version publiait chaque appel d'outil
    comme sa propre ligne : sur soixante tours, ca faisait 247 lignes d'outils qui enterraient
    la conversation sous un mur de « Bash cat », « Read x.py ». Ce n'est pas ce qu'on veut
    relire.

    Un tour est donc rendu comme il se lit : ce que tu as dit, ce que Claude a repondu, puis
    UNE ligne recapitulant ses actions. La reflexion et les sorties d'outils sont ecartees —
    elles font le gros des huit cents messages et ne se relisent pas.

    Renvoie (evenements, nombre d'evenements ecartes par la limite).
    """
    if not sid:
        return [], 0
    try:
        from claude_agent_sdk import get_session_messages
        messages = get_session_messages(sid, directory=dossier)
    except Exception:
        return [], 0

    from worker import _cible   # meme rendu que les lignes en direct

    evenements: list[dict] = []
    outils: list[str] = []      # les actions du tour en cours

    def clore_tour():
        """La ligne de bilan, une seule par tour, comme en direct."""
        if not outils:
            return
        apercu = ", ".join(outils[:8]) + (" …" if len(outils) > 8 else "")
        evenements.append({
            "genre": "tour",
            "actions": len(outils),
            "texte": apercu,
        })
        outils.clear()

    for m in messages:
        charge = getattr(m, "message", None)
        if not isinstance(charge, dict):
            continue
        role = charge.get("role")
        contenu = charge.get("content")

        if role == "user":
            # Une chaine : c'est toi, et un nouveau tour commence. Une liste : ce sont des
            # resultats d'outils, qui n'ouvrent pas de tour.
            if isinstance(contenu, str) and contenu.strip():
                clore_tour()
                evenements.append({"genre": "toi", "texte": contenu.strip()})
            continue

        if role != "assistant" or not isinstance(contenu, list):
            continue
        # Un message d'assistant peut porter plusieurs blocs de texte : on les recolle en un
        # seul paragraphe plutot qu'en autant de lignes.
        morceaux = []
        for bloc in contenu:
            if not isinstance(bloc, dict):
                continue
            sorte = bloc.get("type")
            if sorte == "text" and (bloc.get("text") or "").strip():
                morceaux.append(bloc["text"].strip())
            elif sorte == "tool_use":
                nom = bloc.get("name") or "?"
                cible = _cible(nom, bloc.get("input") or {})
                outils.append(f"{nom} {cible}".strip())
        if morceaux:
            evenements.append({"genre": "texte", "texte": "\n\n".join(morceaux)})

    clore_tour()
    ecartes = max(0, len(evenements) - limite)
    return evenements[-limite:], ecartes


def _quand(iso: str | None, relatif: bool = False) -> str:
    """Une date lisible. « il y a 3 min » vaut mieux qu'un horodatage pour ce qui vient de
    se passer, et un horodatage vaut mieux qu'« il y a 4 jours » pour ce qui est ancien."""
    if not iso:
        return "—"
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return "—"
    if not relatif:
        return t.strftime("%d/%m %H:%M")
    ecart = (datetime.now() - t).total_seconds()
    if ecart < 90:
        return "à l'instant"
    if ecart < 3600:
        return f"il y a {int(ecart // 60)} min"
    if ecart < 86400:
        return f"il y a {int(ecart // 3600)} h"
    return t.strftime("%d/%m %H:%M")


def apercu(limite: int = 15, ici: str | None = None) -> list[str]:
    """La liste, mise en forme. Ici plutot que dans le script fish : une logique d'affichage
    enfouie dans un heredoc n'est ni relisible ni testable."""
    ici = os.path.realpath(ici or os.getcwd())
    lignes = historique(limite, ici)
    if not lignes:
        return ["  aucune conversation enregistrée"]

    sortie: list[str] = []
    vivantes = [d for d in lignes if d["etat"] == "en cours"]
    if len(vivantes) > 1:
        sortie += [f"  ⚠  {len(vivantes)} sessions actives en même temps — elles se partagent",
                   "     le micro et la fenêtre de quota. « vvstop » les ferme.", ""]
    elif vivantes:
        sortie += ["  1 session active.", ""]

    section = None
    for i, d in enumerate(lignes, 1):
        titre = "ce dossier" if d["ici"] else "autres dossiers"
        if titre != section:
            section = titre
            sortie += [f"  ── {titre}" + (f" : {ici}" if d["ici"] else "") + " ──", ""]

        etiquette = {"en cours": "● en cours", "fermée": "○ fermée",
                     "interrompue": "◍ interrompue"}[d["etat"]]
        sortie.append(f"  {i:2d}. {d.get('projet') or '?':<24} {etiquette}")

        # Le couple demarrage / derniere activite. Pour une conversation vivante la
        # « fin » n'existe pas encore : ce qui compte est la derniere fois qu'elle a bouge.
        if d["etat"] == "en cours":
            droite = f"activité  {_quand(d.get('maj'), relatif=True)}  (pid {d.get('pid')})"
        elif d.get("fin"):
            droite = f"fermée    {_quand(d['fin'])}"
        else:
            droite = f"dernier signe  {_quand(d.get('maj'))}"
        sortie.append(f"      début     {_quand(d.get('debut'))}      {droite}")
        sortie.append(f"      {d.get('tours', 0)} tour(s) · {d.get('modele') or '?'}")
        if d.get("chemin"):
            sortie.append(f"      {d['chemin']}")
        elif d.get("suppose"):
            sortie.append("      chemin non enregistré — rapproché d'ici par le nom du projet")
        else:
            sortie.append("      chemin non enregistré (conversation antérieure au suivi)")
        if d.get("session_id"):
            sortie.append(f"      {d['session_id']}")
        sortie.append("")

    sortie += ["  reprendre : vvreprendre 1   (ou l'identifiant de session)",
               "  relire    : vvlire 1"]
    return sortie


def resoudre(reference: str) -> dict | None:
    """Accepte un rang (« 1 » = la plus recente), un identifiant de session, ou un bout de
    nom de fichier. Taper « vvreprendre 1 » doit suffire."""
    liste = historique(100)
    if not liste:
        return None
    if reference.isdigit():
        rang = int(reference)
        return liste[rang - 1] if 1 <= rang <= len(liste) else None
    for d in liste:
        if d.get("session_id") == reference:
            return d
    for d in liste:
        if reference in (d.get("session_id") or "") or reference in d.get("fichier", ""):
            return d
    return None
