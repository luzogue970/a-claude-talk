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
        # Le nom est a la MINUTE, et deux lancements du meme projet dans la meme minute
        # tombaient donc sur le meme fichier : le second ecrivait par-dessus le premier, et
        # l'index — qui deduplique par fichier — n'en gardait qu'un. Un relancement rapide,
        # apres une coupure ou un plantage, perdait ainsi le transcript precedent en silence.
        # Le suffixe ne s'ajoute qu'en cas de collision reelle, pour que les noms restent
        # ceux qu'on lit depuis toujours.
        self.fichier = RACINE / f"{nom}.md"
        rang = 2
        while self.fichier.exists():
            self.fichier = RACINE / f"{nom}-{rang}.md"
            rang += 1
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


def historique(limite: int = 20, ici: str | None = None,
               sous_arbre: bool = False) -> list[dict]:
    """Les conversations, dedupliquees par fichier, celles d'ICI d'abord.

    Le tri repond a la question qu'on se pose vraiment en tapant la commande : « qu'est-ce
    que j'ai fait dans CE projet ». Une liste triee seulement par date noie les conversations
    du dossier courant sous celles de tous les autres.

    `sous_arbre` restreint au dossier courant ET a ses descendants : depuis la racine on voit
    tout, depuis un projet on ne voit que lui. C'est un choix d'AFFICHAGE, donc desactive par
    defaut — deux appelants ne doivent surtout pas etre filtres :

    - `actives()` arbitre le micro entre TOUTES les conversations vivantes, ou qu'elles
      soient ; filtrer la rendrait aveugle a celle qui detient le micro ailleurs.
    - `resoudre()` sert a `vvreprendre <identifiant>`, qui doit marcher depuis n'importe quel
      dossier — sinon reprendre une conversation demanderait de savoir d'ou elle a ete lancee,
      ce qui est precisement l'information qu'on vient chercher.
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
            reel = os.path.realpath(chemin)
            d["ici"] = reel == ici
            # Descendant : le separateur evite qu'« /a/bc » passe pour un enfant d'« /a/b ».
            d["sous"] = (os.path.relpath(reel, ici)
                         if reel.startswith(ici + os.sep) else "")
            d["portee"] = d["ici"] or bool(d["sous"])
        else:
            # Conversations anterieures a l'enregistrement du chemin. Le nom du projet est le
            # seul indice disponible : c'est le nom du dossier de travail, donc la comparaison
            # tombe juste dans le cas courant. On marque le rapprochement comme suppose, pour
            # que l'affichage ne presente pas une deduction comme un fait.
            d["ici"] = bool(d.get("projet")) and d["projet"] == os.path.basename(ici)
            d["suppose"] = d["ici"]
            d["sous"] = ""
            # Sans chemin enregistre, on ne peut pas savoir si c'est un descendant. On ne le
            # garde donc que si le nom du projet correspond — deviner plus large reviendrait
            # a polluer la vue d'un projet avec l'historique de tous les autres.
            d["portee"] = d["ici"]
    if sous_arbre:
        lignes = [d for d in lignes if d.get("portee")]
    # « d'ici » d'abord, puis les descendants, puis la plus recemment active. Les booleens
    # sont inverses parce que le tri est descendant.
    lignes.sort(key=lambda d: (d["ici"], bool(d.get("sous")), d.get("maj", "")),
                reverse=True)
    return lignes[:limite]


def conversations(ici: str | None = None, sous_arbre: bool = True,
                  limite: int = 200) -> list[dict]:
    """Une entree par CONVERSATION REELLE, et non par lancement.

    C'est la difference qui rendait tout illisible. L'index compte les lancements : ouvrir
    l'agent six fois sur le meme projet en reprenant a chaque fois la meme session Claude
    ecrit six fichiers de transcript — donc six lignes — pour UNE conversation. Sur cette
    machine : 23 lignes pour 5 conversations. On croit que les conversations se multiplient
    et qu'on perd le contexte, alors que le contexte est intact et que c'est la LISTE qui
    compte mal.

    Le regroupement se fait sur `session_id`, qui est l'identite reelle d'une conversation
    cote Claude Code. Les lancements sans session_id (agent ouvert puis referme sans avoir
    rien dit) n'ont pas d'identite : ils restent separes mais se reconnaissent a leur zero
    tour, et l'appelant peut les ecarter.

    Chaque entree porte de quoi CHOISIR sans deviner : quand on y a parle pour la derniere
    fois, combien de tours en tout, en combien de reprises, et l'apercu du dernier echange.
    Un identifiant et une heure ne suffisent pas a se rappeler de quoi on parlait.
    """
    # La limite porte sur les CONVERSATIONS rendues, pas sur les lancements lus : tronquer
    # avant le regroupement amputait les conversations de leurs plus anciens lancements, et
    # une conversation de 59 tours en 7 reprises s'affichait « 13 tours, repris 2 fois ». Un
    # chiffre faux presente comme un total est pire qu'une liste plus longue a calculer.
    lancements = historique(10_000, ici, sous_arbre=sous_arbre)
    groupes: dict[str, dict] = {}
    for lc in lancements:
        sid = lc.get("session_id")
        # Sans identite cote Claude, chaque lancement reste lui-meme : les fusionner
        # inventerait une continuite qui n'existe pas.
        cle = sid or f"_sans_session_{lc['fichier']}"
        g = groupes.get(cle)
        if g is None:
            groupes[cle] = g = {
                "session_id": sid,
                "projet": lc.get("projet"),
                "chemin": lc.get("chemin"),
                "debut": lc.get("debut"),
                "maj": lc.get("maj"),
                "tours": 0,
                "reprises": 0,
                "fichiers": [],
                "etat": lc.get("etat"),
                "modele": lc.get("modele"),
                "ici": lc.get("ici"),
                "sous": lc.get("sous"),
            }
        g["tours"] += lc.get("tours") or 0
        g["reprises"] += 1
        g["fichiers"].append(lc["fichier"])
        # Le debut est le plus ancien, la mise a jour la plus recente : la conversation
        # s'etend sur tous ses lancements, ce n'est pas une suite de conversations courtes.
        if (lc.get("debut") or "") < (g["debut"] or "\uffff"):
            g["debut"] = lc.get("debut")
        if (lc.get("maj") or "") > (g["maj"] or ""):
            g["maj"] = lc.get("maj")
            g["etat"] = lc.get("etat")       # l'etat du lancement le plus recent
            g["modele"] = lc.get("modele")
            g["fichier"] = lc["fichier"]     # ou lire la suite
        if lc.get("chemin") and not g["chemin"]:
            g["chemin"] = lc["chemin"]
        g["ici"] = g["ici"] or lc.get("ici")
        g["sous"] = g["sous"] or lc.get("sous")
    # Meme tri que l'index, en une seule cle : ce qui est ICI d'abord, puis ses descendants,
    # puis le reste — et a egalite, le plus recemment touche. On se demande « qu'est-ce que je
    # faisais dans CE projet », pas « qu'ai-je fait de plus recent, ou que ce soit ».
    # `maj` est inverse par un tri decroissant sur le tuple entier, d'ou les booleens pris a
    # l'envers : `ici` vaut True et doit passer devant.
    ordonne = sorted(groupes.values(),
                     key=lambda g: (bool(g.get("ici")), bool(g.get("sous")), g.get("maj") or ""),
                     reverse=True)
    return ordonne[:limite]


def derniere_conversation(ici: str | None = None) -> dict | None:
    """La conversation a reprendre par defaut dans ce dossier, s'il y en a une.

    Deux exclusions, et chacune evite un degat precis :

    - **sans session_id** : rien a reprendre, Claude Code n'en a pas gardé trace. La
      « reprendre » repartirait de zero en silence — exactement le symptome qu'on corrige.
    - **deja ouverte ailleurs** : deux agents sur la MEME session Claude s'ecriraient
      par-dessus. Mieux vaut une nouvelle conversation qu'une conversation corrompue.

    Exige aussi que le dossier corresponde exactement : Claude Code range ses sessions par
    repertoire, et reprendre depuis un autre dossier ne donne pas une erreur mais une session
    introuvable, donc un contexte perdu sans le moindre message.
    """
    ici = os.path.realpath(ici or os.getcwd())
    occupes = {d.get("session_id") for d in actives() if d.get("session_id")}
    for c in conversations(ici, sous_arbre=False):
        if not c.get("session_id") or not c.get("tours"):
            continue
        if c["session_id"] in occupes:
            continue
        if (c.get("chemin") and os.path.realpath(c["chemin"]) != ici):
            continue
        return c
    return None


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
# Assez pour une conversation de soixante tours rejouee ENTIEREMENT : mesure, elle produit
# environ 1 200 lignes. Tronquer au milieu d'une reprise donnerait l'impression que la
# conversation commence en cours de route.
REJEU_MAX = 2000

# Les sorties d'outils completes pesaient 4,76 Mo sur cette meme session — 85 % du volume,
# pour des sorties de `cat` vieilles de trois semaines. Ce qui compte en relecture est qu'un
# outil a rendu quelque chose, et son debut.
RESULTAT_MAX = 400


def rejouer_session(sid: str, dossier: str | None = None,
                    limite: int = REJEU_MAX) -> tuple[list[dict], int]:
    """L'historique d'une session, converti en evenements du tableau.

    Reprendre une conversation rendait le contexte complet a Claude mais laissait la page
    VIDE : le tableau vit en memoire du processus, et un nouveau processus part de rien. On
    relit donc ce que Claude Code a ecrit sur disque, et on le republie.

    **On rejoue tout ce qui a du contenu**, ligne par ligne comme en direct : ce que tu as dit,
    la reflexion, ce qu'il a ecrit, chaque appel d'outil avec son resultat, et le bilan du
    tour. Les filtres du tableau decident ensuite de ce qui s'affiche — c'est leur role, et ca
    evite d'avoir a choisir a ta place.

    Deux exceptions, mesurees et non supposees sur une session de soixante tours :

    - **La reflexion est ecartee** parce qu'elle est VIDE sur disque : 349 blocs `thinking`
      pour 0 Ko de contenu. Les rejouer ajouterait 349 lignes blanches.
    - **Les sorties d'outils sont tronquees** a `RESULTAT_MAX` caracteres. Completes, elles
      pesaient 4,76 Mo — 85 % du volume total — pour des sorties de `cat` vieilles de trois
      semaines. Ce qui compte en relecture est qu'un outil a rendu quelque chose, et son
      debut ; la trace complete vit dans la session Claude Code, qui est intacte.

    Renvoie (evenements, nombre d'evenements ecartes par la limite).
    """
    if not sid:
        return [], 0
    try:
        from claude_agent_sdk import get_session_messages
        messages = get_session_messages(sid, directory=dossier)
    except Exception:
        return [], 0

    from worker import _cible

    evenements: list[dict] = []
    outils: list[str] = []      # les actions du tour en cours, pour son bilan

    def clore_tour():
        if not outils:
            return
        apercu_actions = ", ".join(outils[:8]) + (" …" if len(outils) > 8 else "")
        evenements.append({"genre": "tour", "actions": len(outils),
                           "texte": apercu_actions})
        outils.clear()

    for m in messages:
        charge = getattr(m, "message", None)
        if not isinstance(charge, dict):
            continue
        role = charge.get("role")
        contenu = charge.get("content")

        if role == "user":
            if isinstance(contenu, str) and contenu.strip():
                clore_tour()
                evenements.append({"genre": "toi", "texte": contenu.strip()})
            elif isinstance(contenu, list):
                for bloc in contenu:
                    if not isinstance(bloc, dict) or bloc.get("type") != "tool_result":
                        continue
                    brut = bloc.get("content")
                    if isinstance(brut, list):
                        brut = " ".join(b.get("text", "") for b in brut
                                        if isinstance(b, dict))
                    texte = str(brut or "").strip()
                    if not texte:
                        continue
                    coupe = len(texte) > RESULTAT_MAX
                    evenements.append({
                        "genre": "resultat",
                        "texte": texte[:RESULTAT_MAX] + (" […]" if coupe else ""),
                        "id": bloc.get("tool_use_id"),
                        "echec": bool(bloc.get("is_error")),
                        "tronque": coupe,
                    })
            continue

        if role != "assistant" or not isinstance(contenu, list):
            continue
        for bloc in contenu:
            if not isinstance(bloc, dict):
                continue
            sorte = bloc.get("type")
            if sorte == "text" and (bloc.get("text") or "").strip():
                evenements.append({"genre": "texte", "texte": bloc["text"].strip()})
            elif sorte == "tool_use":
                nom = bloc.get("name") or "?"
                cible = _cible(nom, bloc.get("input") or {})
                outils.append(f"{nom} {cible}".strip())
                # Une ligne par appel, comme en direct, avec son identifiant : c'est lui qui
                # rattache le resultat a l'action.
                evenements.append({"genre": "outil", "nom": nom, "cible": cible,
                                   "id": bloc.get("id")})

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


def apercu(limite: int = 15, ici: str | None = None, tout: bool = False) -> list[str]:
    """La liste, mise en forme. Ici plutot que dans le script fish : une logique d'affichage
    enfouie dans un heredoc n'est ni relisible ni testable.

    Par defaut, restreinte au dossier courant et a ses descendants : depuis la racine on voit
    tout, depuis un projet on ne voit que lui. `tout` leve la restriction — et le nombre de
    conversations masquees est toujours annonce, parce qu'une liste qui raccourcit sans le
    dire se lit comme une perte de donnees.
    """
    ici = os.path.realpath(ici or os.getcwd())
    # Une ligne par CONVERSATION, pas par lancement. Rouvrir l'agent six fois sur le meme
    # projet en reprenant la meme session ecrivait six lignes pour une seule conversation :
    # 23 lignes pour 5 conversations reelles. On croyait que les conversations se
    # multipliaient et qu'on perdait le contexte, alors que le contexte etait intact.
    lignes = conversations(ici, sous_arbre=not tout, limite=limite)
    caches = 0
    if not tout:
        # Compte sur l'ensemble, pas sur la page : « 3 masquees » alors qu'il y en a 40 serait
        # un chiffre faux presente comme exact.
        caches = (len(conversations(ici, sous_arbre=False, limite=10_000))
                  - len(conversations(ici, sous_arbre=True, limite=10_000)))
    if not lignes:
        vide = ["  aucune conversation lancée depuis ici"]
        if caches:
            vide += ["", f"  {caches} ailleurs sur la machine — « vvconv --tout » les montre,",
                     "  ou place-toi plus haut dans l'arborescence."]
        return vide if caches else ["  aucune conversation enregistrée"]

    sortie: list[str] = []
    vivantes = [d for d in lignes if d["etat"] == "en cours"]
    if len(vivantes) > 1:
        sortie += [f"  ⚠  {len(vivantes)} sessions actives en même temps — elles se partagent",
                   "     le micro et la fenêtre de quota. « vvstop » les ferme.", ""]
    elif vivantes:
        sortie += ["  1 session active.", ""]

    section = None
    for i, d in enumerate(lignes, 1):
        if d["ici"]:
            titre = "ce dossier"
        elif d.get("sous"):
            titre = "sous-dossiers"
        else:
            titre = "ailleurs"
        if titre != section:
            section = titre
            sortie += [f"  ── {titre}" + (f" : {ici}" if titre == "ce dossier" else "") + " ──",
                       ""]

        etiquette = {"en cours": "● en cours", "fermée": "○ fermée",
                     "interrompue": "◍ interrompue"}.get(d["etat"], d["etat"])
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
        # « en N lancements » dit ce qui manquait : cette conversation a ete reprise, elle
        # n'est pas une conversation courte de plus.
        reprises = d.get("reprises", 1)
        suite = f" · repris {reprises} fois" if reprises > 1 else ""
        sortie.append(f"      {d.get('tours', 0)} tour(s){suite} · {d.get('modele') or '?'}")
        if d.get("sous"):
            # Le sous-chemin relatif plutot que l'absolu : depuis la racine d'un projet, ce
            # qu'on veut savoir est « dans quel sous-dossier », pas le chemin complet.
            sortie.append(f"      ./{d['sous']}")
        elif d.get("chemin"):
            sortie.append(f"      {d['chemin']}")
        elif d.get("suppose"):
            sortie.append("      chemin non enregistré — rapproché d'ici par le nom du projet")
        else:
            sortie.append("      chemin non enregistré (conversation antérieure au suivi)")
        if d.get("session_id"):
            sortie.append(f"      {d['session_id']}")
        sortie.append("")

    sortie += ["  reprendre : vvreprendre 1   (ou l'identifiant de session)",
               "  relire    : vvlire 1",
               "  « vv » reprend tout seul la dernière d'ici ; « vvneuf » en ouvre une neuve."]
    if caches:
        sortie.append(f"  {caches} conversation(s) hors de ce dossier — « vvconv --tout »")
    return sortie


def resoudre(reference: str, ici: str | None = None, tout: bool = False) -> dict | None:
    """Un rang (« 1 » = la premiere de la liste), un identifiant de session, ou un bout de nom.

    Le rang est resolu sur EXACTEMENT la liste qu'affiche `apercu` — meme dossier, meme
    portee, meme regroupement. C'etait faux : l'affichage numerotait une liste restreinte au
    sous-arbre et la resolution cherchait dans la liste complete, si bien que « vvreprendre 3 »
    pouvait reprendre une autre conversation que la troisieme affichee. Silencieusement, et en
    donnant l'impression qu'une conversation repartait toute seule.

    Un identifiant, lui, est cherche PARTOUT : il designe une conversation precise, et exiger
    d'etre dans le bon dossier pour l'utiliser reviendrait a demander l'information qu'on vient
    justement chercher.
    """
    if reference.isdigit():
        liste = conversations(ici, sous_arbre=not tout)
        rang = int(reference)
        return liste[rang - 1] if 1 <= rang <= len(liste) else None
    partout = conversations(ici, sous_arbre=False, limite=1000)
    for d in partout:
        if d.get("session_id") == reference:
            return d
    for d in partout:
        if reference in (d.get("session_id") or "") or reference in (d.get("fichier") or ""):
            return d
    return None
