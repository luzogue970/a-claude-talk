"""Resolved paths, credentials and vocabulary. Nothing here is hardcoded to a machine."""

import glob
import os
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SECRETS = Path.home() / ".config" / "claude-talk" / "secrets.env"


def _load_secrets():
    """Secrets live outside the repo, so a clone can never carry a key."""
    if not SECRETS.is_file():
        return
    for line in SECRETS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_secrets()


def claude_binary():
    """The CLI is not on PATH on every machine: the VS Code extension ships its own,
    and the SDK wheel bundles one. Prefer PATH, then the newest extension build."""
    found = shutil.which("claude")
    if found:
        return found
    builds = sorted(glob.glob(str(
        Path.home() / ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude"
    )))
    return builds[-1] if builds else None


# --- audio -------------------------------------------------------------------
# The design assumes headphones: with them there is no echo, so the microphone can
# stay open and barge-in works. On speakers the agent hears itself and interrupts
# itself, which is exactly the failure the old gating tried to paper over.
INPUT_DEVICE = os.environ.get("VOIX_INPUT_DEVICE", "")
OUTPUT_DEVICE = os.environ.get("VOIX_OUTPUT_DEVICE", "")

# --- moteurs -----------------------------------------------------------------
# auto = la chaine complete : Azure, puis Deepgram, puis le moteur local. C'est le defaut
# depuis qu'Azure a rendu « 400 Quota exceeded » en pleine session sans qu'aucun filet
# existe.
#
# L'ordre n'est pas arbitraire. Azure et Deepgram sont deux pairs — les deux font du vrai
# streaming avec resultats intermediaires, autour de 300 ms — donc la bascule de l'un a
# l'autre ne se sent pas a l'oreille. Le moteur local est en dernier parce que lui se sent :
# 4 a 5 s par phrase. Il reste dans la chaine parce qu'un jour les deux services seront
# coupes en meme temps, et ce jour-la « lent » vaut infiniment mieux que « sourd ».
STT_ENGINE = os.environ.get("VOIX_STT", "auto")   # auto | azure | deepgram | local
# small : correct en francais, ~4 s pour 2 s d'audio sur ce CPU. base : ~1,7 s, moins fiable.
STT_LOCAL_MODELE = os.environ.get("VOIX_STT_LOCAL_MODELE", "small")
STT_LOCAL_THREADS = int(os.environ.get("VOIX_STT_LOCAL_THREADS", "8"))
TTS_ENGINE = os.environ.get("VOIX_TTS", "azure")      # azure | local
LANGUAGE = os.environ.get("VOIX_LANGUAGE", "fr-FR")
AZURE_KEY = os.environ.get("AZURE_SPEECH_KEY", "")
AZURE_REGION = os.environ.get("AZURE_SPEECH_REGION", "francecentral")
AZURE_VOICE = os.environ.get("AZURE_SPEECH_VOICE", "fr-FR-DeniseNeural")

# Deepgram : le repli de meme calibre qu'Azure. nova-3 fait du francais en streaming avec
# resultats intermediaires, et accepte le « keyterm prompting » — l'equivalent exact de la
# phrase_list d'Azure, donc le vocabulaire du projet survit a la bascule.
DEEPGRAM_KEY = os.environ.get("DEEPGRAM_API_KEY", "")
DEEPGRAM_MODELE = os.environ.get("VOIX_DEEPGRAM_MODELE", "nova-3")
# Le biais de vocabulaire est officiellement documente en anglais chez Deepgram. Le plugin
# l'accepte en francais sans broncher, mais si le service le refusait un jour, ce drapeau
# permet de le couper sans toucher au code : le repli doit rester debout, meme diminue.
DEEPGRAM_KEYTERM = os.environ.get("VOIX_DEEPGRAM_KEYTERM", "1") not in ("0", "non", "false")

# --- Claude ------------------------------------------------------------------
WORKER_MODEL = os.environ.get("VOIX_WORKER_MODEL", "claude-opus-5")

# Switchable at runtime, by voice or from the page. Effort is fixed for the session (the SDK
# exposes set_model but no set_effort), so a lighter model is the lever for a quick answer.
MODELES = {
    "opus": ("claude-opus-5", "Opus 5 — le plus capable"),
    "sonnet": ("claude-sonnet-5", "Sonnet 5 — équilibré"),
    "haiku": ("claude-haiku-4-5", "Haiku 4.5 — le plus rapide"),
}

def cle_de_effort(niveau: str) -> str:
    return niveau if niveau in EFFORTS else WORKER_EFFORT


def cle_du_modele(nom: str) -> str:
    for cle, (identifiant, _) in MODELES.items():
        if identifiant == nom:
            return cle
    return nom
# Les cinq niveaux d'effort du SDK, verifies dans le type de ClaudeAgentOptions :
# Literal['low', 'medium', 'high', 'xhigh', 'max']. Il n'y en a pas d'autre — « ultracode »
# est une notion des sessions Claude Code, pas un niveau d'effort du SDK.
#
# Piege a connaitre : le client n'expose PAS de set_effort (il n'a que set_model et
# set_permission_mode). Changer de niveau impose donc de reconstruire le client en reprenant
# la session — voir Worker.changer_effort.
EFFORTS = {
    "low": ("low", "minimal — réponse immédiate"),
    "medium": ("medium", "moyen — équilibré"),
    "high": ("high", "élevé"),
    "xhigh": ("xhigh", "très élevé — défaut"),
    "max": ("max", "maximum — le plus fouillé, le plus lent"),
}
WORKER_EFFORT = os.environ.get("VOIX_WORKER_EFFORT", "xhigh")
SPEAKER_MODEL = os.environ.get("VOIX_SPEAKER_MODEL", "claude-haiku-4-5")
WORKDIR = os.environ.get("VOIX_WORKDIR", os.getcwd())

# Measured on this machine, writing a file in the project vs a shell command writing
# outside it:
#   default            demande tout
#   auto / dontAsk     REFUSENT l'ecriture — inutilisables ici
#   acceptEdits        ecrit dans le projet sans demander, bloque hors projet
#   bypassPermissions  tout passe, y compris un shell hors projet            <- defaut
# bypassPermissions means the spoken "je le fais ?" never happens: the dashboard is the only
# control point left, which is why the startup panel spells the mode out and the header
# carries a badge for it.
PERMISSION = os.environ.get("VOIX_PERMISSION", "bypassPermissions")

# --- ecoute et interruption --------------------------------------------------
# Read by agent.py and shown in the startup panel, so what the page displays is the value
# actually in force rather than a copy that drifts.
INTERRUPT_MIN_DUREE = float(os.environ.get("VOIX_INTERRUPT_DUREE", "0.6"))
INTERRUPT_MIN_MOTS = int(os.environ.get("VOIX_INTERRUPT_MOTS", "2"))
FAUX_INTERRUPT_S = float(os.environ.get("VOIX_FAUX_INTERRUPT", "2.0"))
DETECTEUR_TOUR = os.environ.get("VOIX_DETECTEUR", "v1-mini")

# Combien de silence il faut avant que la phrase soit consideree comme finie et envoyee.
#
# Le defaut de LiveKit est 0,3 s (mesure sur une AgentSession nue ; la doc annonce 0,5, le
# code applique 0,3 / 2,5), et c'est ce qui rendait la parole impossible a poser : une
# pause de deux secondes pour rassembler son idee comptait pour une fin de phrase, le tour
# partait, et la suite arrivait comme un SECOND message par-dessus le premier.
#
# Le mecanisme est binaire, verifie dans audio_recognition.py : le detecteur de fin de tour
# donne une probabilite, et si elle passe sous son seuil c'est ECOUTE_MAX qui s'applique au
# lieu d'ECOUTE_MIN. Autrement dit MIN est le plancher garanti, MAX le plafond quand le
# modele estime que la phrase n'est pas terminee.
#
# Le prix a payer est reel et assume : chaque phrase attend maintenant ECOUTE_MIN de silence
# avant de partir, y compris « stop ». Le bouton « envoyer » du tableau et le decompte
# existent pour ca — voir aussi le raccourci Entree.
ECOUTE_MIN = float(os.environ.get("VOIX_ECOUTE_MIN", "5.0"))
ECOUTE_MAX = float(os.environ.get("VOIX_ECOUTE_MAX", "0") or 0) or None

# Les delais proposes par le tableau. 3, 5 et 10 sont les reperes ; le reste est la pour
# pouvoir descendre plus court ou monter plus long sans toucher a la configuration.
ECOUTE_PALIERS = (2.0, 3.0, 5.0, 8.0, 10.0, 15.0, 20.0)

# Retenir automatiquement ce qui est dit PENDANT que Claude travaille.
#
# Sans ca, parler pendant une tache empile un second message : le worker l'accepte, repond
# « note, j'ajoute ca », et il part sans qu'on ait relu quoi que ce soit. Or c'est exactement
# le moment ou l'on parle pour reagir a ce qu'on voit passer — donc le moment ou une phrase
# mal transcrite ou mal formulee coute le plus cher.
#
# Ce qui continue de passer, delibérement : les ordres locaux (« arrete », « coupe le micro »)
# et les reponses a une demande de permission. Les parquer dans une boite serait absurde —
# ce sont des reactions, pas des taches a relire.
RETENIR_SI_OCCUPE = os.environ.get("VOIX_RETENIR_OCCUPE", "1") not in ("0", "non", "false")

# Le plafond suit le plancher au lieu d'etre fixe. Sinon regler le plancher a 15 s le
# placerait au-dessus d'un plafond de 12 s, et la fenetre « phrase inachevee » deviendrait
# plus COURTE que la fenetre normale — un reglage qui se retourne contre celui qui le fait.
RAPPORT_ECOUTE_MAX = 2.5


def plafond_ecoute(plancher: float | None = None) -> float:
    """Le plafond correspondant a un plancher donne."""
    plancher = ECOUTE_MIN if plancher is None else plancher
    if ECOUTE_MAX:
        return max(ECOUTE_MAX, plancher + 2.0)   # valeur imposee, mais jamais incoherente
    return round(plancher * RAPPORT_ECOUTE_MAX, 1)

# --- assistant « Hey Claude » (processus separe) ------------------------------
# Un modele leger : ce sont des questions de culture generale, et l'objectif est une reponse
# en quelques secondes, pas une analyse.
EVEIL_MODELE = os.environ.get("VOIX_EVEIL_MODELE", "claude-haiku-4-5")
# Plafond quotidien en CENTIMES de depense Azure reelle, transcription ET synthese vocale.
# Un plafond en minutes ne couvrait que la transcription : les reponses lues a voix haute
# passaient a cote du compteur. Le plafond compte maintenant les deux.
# 27 centimes/jour = 100 EUR etales sur 365 jours. Le credit etudiant expire au bout de
# 12 mois et le solde non consomme est perdu : depenser MOINS que ca, c'est laisser bruler
# de l'argent. VOIX_EVEIL_PLAFOND_CENTIMES pour changer.
EVEIL_PLAFOND_CENTIMES = float(os.environ.get("VOIX_EVEIL_PLAFOND_CENTIMES", "27"))
# Tarifs Azure, en dollars. A verifier dans ton portail : le tarif synthese depend du type de
# voix (neurale standard vs HD/MAI), et je prends volontairement une valeur haute pour que le
# plafond protege plutot qu'il rassure.
TARIF_STT_H = float(os.environ.get("VOIX_TARIF_STT", "1.0"))        # $ / heure d'audio
# 16 $/M pour les voix neurales classiques, 22 $/M pour les Neural HD (baisse depuis 30 en
# mars 2026). fr-FR-Marc:MAI-Voice-2-Flash est d'une generation recente : on prend 22.
TARIF_TTS_M = float(os.environ.get("VOIX_TARIF_TTS", "22.0"))      # $ / million de caracteres

# --- credit Azure ------------------------------------------------------------
# Le credit etudiant expire 12 mois apres l'ouverture, solde non consomme perdu. La depense
# quotidienne optimale n'est donc pas « le moins possible » mais « le credit divise par les
# jours restants ». Renseigne la date de fin pour que vheycout calcule juste.
AZURE_CREDIT_EUR = float(os.environ.get("VOIX_CREDIT_EUR", "100"))
AZURE_CREDIT_FIN = os.environ.get("VOIX_CREDIT_FIN", "")           # AAAA-MM-JJ, optionnel
# RMS du monitor du sink au-dela duquel on considere que les haut-parleurs jouent. A regler
# avec `assistant.py --diag` : le silence n'est pas a zero sur toutes les machines.
EVEIL_SEUIL = float(os.environ.get("VOIX_EVEIL_SEUIL", "400"))
# Au-dela, ce n'est plus une phrase : on ne l'envoie pas.
EVEIL_MAX_S = float(os.environ.get("VOIX_EVEIL_MAX", "15"))
# Une conversation ouverte facture chaque tour. Sans silence prolonge, elle se ferme seule.
EVEIL_INACTIVITE_S = float(os.environ.get("VOIX_EVEIL_INACTIVITE", "90"))

# --- journal des conversations ------------------------------------------------
# Centralise ici, pas dans le projet travaille : une conversation parle souvent de plusieurs
# projets, et personne ne veut voir ces fichiers apparaitre dans son git.
JOURNAL_DIR = os.environ.get("VOIX_JOURNAL", str(ROOT / "conversations"))
# Identifiant de session Claude Code a reprendre, s'il y en a un.
REPRENDRE = os.environ.get("VOIX_REPRENDRE", "")

# --- tableau de bord ---------------------------------------------------------
UI_PORT = int(os.environ.get("VOIX_UI_PORT", "7788"))
UI_OUVRIR = os.environ.get("VOIX_UI_OUVRIR", "1") not in ("0", "", "non")


def _compte_claude() -> str:
    """Which Claude account pays for this: read from the credentials, secrets untouched."""
    chemin = Path.home() / ".claude" / ".credentials.json"
    if not chemin.is_file():
        return "inconnu"
    try:
        import json

        oauth = json.loads(chemin.read_text(encoding="utf-8")).get("claudeAiOauth", {})
        genre = oauth.get("subscriptionType", "?")
        palier = oauth.get("rateLimitTier", "")
        return f"{genre} ({palier})" if palier else str(genre)
    except Exception:
        return "illisible"


def resume() -> dict:
    """The effective configuration, for the dashboard's startup panel."""
    risque = PERMISSION == "bypassPermissions"
    return {
        # resume() n'est appelee qu'au demarrage : cette heure est donc bien celle du
        # lancement, et elle donne le point de depart auquel rapporter les heures du flux.
        "démarrage": datetime.now().strftime("%H:%M:%S le %d/%m"),
        "projet": WORKDIR,
        "travail": f"{WORKER_MODEL} · effort {WORKER_EFFORT}",
        "porte-parole": SPEAKER_MODEL,
        "permissions": PERMISSION + (
            " — rien ne sera demandé, y compris hors du projet" if risque else ""
        ),
        "compte Claude": _compte_claude(),
        # Rempli par l'agent : la chaine est decidee par moteurs_stt, qui importe ce module —
        # l'appeler ici creerait un cycle. Une valeur par defaut plutot qu'une ligne absente,
        # pour que le panneau reste complet meme si l'agent oubliait de la poser.
        "reconnaissance": f"{LANGUAGE} · {len(phrase_list())} termes biaisés",
        "synthèse": f"{TTS_ENGINE} · {AZURE_VOICE if TTS_ENGINE == 'azure' else 'piper'}",
        "dictée": (
            ("retenue dans la barre pendant que Claude travaille"
             if RETENIR_SI_OCCUPE else "envoyée même pendant une tâche")
            + " · le bouton « retenir » la retient tout le temps"
        ),
        "fin de tour": (
            f"{DETECTEUR_TOUR} (local) · envoi après {ECOUTE_MIN:g} s de silence, "
            f"jusqu'à {plafond_ecoute():g} s si la phrase semble inachevée "
            f"(réglable depuis le tableau)"
        ),
        "interruption": (
            f"vad · {INTERRUPT_MIN_DUREE}s et {INTERRUPT_MIN_MOTS} mots minimum · "
            f"reprise après {FAUX_INTERRUPT_S}s de faux positif"
        ),
        "écho": "AEC WebRTC du mode console (annulation + réduction de bruit)",
        "binaire claude": claude_binary() or "INTROUVABLE",
        "tableau": f"http://127.0.0.1:{UI_PORT}",
        "_alerte": "permissions désactivées" if risque else "",
    }

# --- vocabulaire -------------------------------------------------------------
# Azure fr-FR alone turns "un fichier point MD" into "un point MD" and a three-letter
# in-house acronym into
# "kubedka". A phrase list biases the acoustic layer; the project name and branch
# are appended at runtime, the way the native /voice does it.
# Le vocabulaire de base, volontairement generique. AJOUTE LE TIEN : c'est ce qui fait la
# difference entre ton sigle maison et « kubedka ». Le nom du projet et la branche git
# courante sont
# ajoutes automatiquement, mais les sigles maison, les noms de services et le jargon d'equipe
# doivent etre listes ici a la main.
PHRASE_LIST = [
    "Claude Code", "LiveKit", "Pipecat",
    "markdown", "gitignore", "commit", "rebase", "pull request", "merge request",
    "refactor", "linter", "pytest", "venv", "async", "await", "JSON", "YAML",
    "TypeScript", "Python", "Docker", "Kubernetes", "CI", "pipeline",
    "point py", "point md", "point json", "point sh", "point ts",
]


def phrase_list(extra=()):
    """Project name and git branch as recognition hints, plus anything caller adds."""
    hints = list(PHRASE_LIST)
    hints.append(Path(WORKDIR).name)
    head = Path(WORKDIR) / ".git" / "HEAD"
    if head.is_file():
        ref = head.read_text(encoding="utf-8", errors="replace").strip()
        if ref.startswith("ref: refs/heads/"):
            hints.append(ref.rsplit("/", 1)[-1])
    hints.extend(extra)
    return [h for h in dict.fromkeys(hints) if h]
