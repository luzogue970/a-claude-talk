"""Les moteurs de reconnaissance vocale, declares en un seul endroit.

Pourquoi une table plutot que du code disperse : la chaine de repli, le selecteur du tableau,
le panneau de configuration et le banc d'essai doivent parler des memes moteurs, avec les
memes libelles. Trois listes paralleles auraient derive l'une de l'autre — c'est deja arrive
avec les libelles de filtres.

Ce que chaque entree dit, et pourquoi ca compte :

- **le palier gratuit**, et s'il se renouvelle. Un quota mensuel se depense sans regret : il
  revient. Un credit unique se garde pour quand les mensuels sont epuises. C'est ce qui dicte
  l'ordre par defaut de la chaine.
- **s'il fait du vrai streaming**. Sans streaming, il faut passer par le VAD (StreamAdapter),
  ce qui ajoute la duree de la phrase a la latence. Un moteur batch, meme excellent, ne se
  place pas en tete.
- **comment lui donner le vocabulaire du projet**. Chaque fournisseur a son propre nom pour
  ca — `custom_vocabulary`, `keyterms_prompt`, `additional_vocab`, `keyterm`, `phrase_list`,
  `prompt`. Sans ce biais, « gRPC » devient « kubedka » et la bascule d'un moteur a l'autre
  ferait perdre la qualite au pire moment.

Les chiffres de palier gratuit datent d'aout 2026 et viennent des pages de tarif des
fournisseurs. Ils changent : ce sont des indications pour choisir, pas des garanties.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import config


@dataclass(frozen=True)
class Moteur:
    cle: str
    libelle: str
    cle_env: str | None          # la variable qui porte sa cle d'API
    gratuit: str                 # ce que donne le palier gratuit
    renouvelable: bool           # mensuel (on le depense) ou credit unique (on le garde)
    streaming: bool
    note: str = ""

    @property
    def dispo(self) -> bool:
        """Utilisable maintenant : soit aucune cle requise, soit la cle est presente."""
        import os
        return self.cle_env is None or bool(os.environ.get(self.cle_env, "").strip())


# L'ordre de declaration est l'ordre par defaut de la chaine, et il suit une logique :
# les quotas MENSUELS d'abord (ils reviennent, autant les depenser), les CREDITS uniques
# ensuite (finis, on les garde pour la suite), le local en dernier (illimite mais lent).
MOTEURS: tuple[Moteur, ...] = (
    Moteur("speechmatics", "Speechmatics", "SPEECHMATICS_API_KEY",
           "8 h/mois, renouvele, sans carte", True, True,
           "le meilleur taux d'erreur des bancs publics d'aout 2026 (6,4 %)"),
    Moteur("gladia", "Gladia", "GLADIA_API_KEY",
           "4 h/mois de temps reel, renouvele", True, True,
           "annonce par son editeur comme le meilleur sur le francais"),
    Moteur("azure", "Azure", "AZURE_SPEECH_KEY",
           "5 h/mois au palier F0, renouvele", True, True,
           "aussi utilise pour la synthese vocale"),
    Moteur("assemblyai", "AssemblyAI", "ASSEMBLYAI_API_KEY",
           "50 $ de credits a l'inscription (~300 h)", False, True,
           "credit unique : garde-le pour quand les quotas mensuels sont epuises"),
    Moteur("deepgram", "Deepgram", "DEEPGRAM_API_KEY",
           "200 $ de credits a l'inscription", False, True,
           "nova-3, tres faible latence"),
    Moteur("groq", "Groq", "GROQ_API_KEY",
           "palier gratuit avec limites journalieres", True, False,
           "whisper-large-v3 tres rapide, mais SANS streaming : il faut attendre la fin"),
    Moteur("local", "local (faster-whisper)", None,
           "illimite, hors ligne", True, False,
           "4 a 5 s par phrase ; l'audio ne quitte pas la machine"),
)

PAR_CLE = {m.cle: m for m in MOTEURS}


def construire(cle: str, vad=None):
    """Fabrique un moteur pret a brancher, avec le vocabulaire du projet.

    Chaque fournisseur nomme differemment le biais de vocabulaire ; c'est le seul endroit ou
    cette diversite est traitee, pour que le reste du code n'ait pas a la connaitre.
    """
    mots = config.phrase_list()
    langue = config.LANGUAGE                    # « fr-FR »
    courte = langue.split("-")[0]               # « fr »

    if cle == "azure":
        from livekit.plugins import azure
        return azure.STT(speech_key=config.AZURE_KEY, speech_region=config.AZURE_REGION,
                         language=langue, phrase_list=mots, explicit_punctuation=True)
    if cle == "deepgram":
        from livekit.plugins import deepgram
        extra = {"keyterm": mots[:50]} if config.DEEPGRAM_KEYTERM else {}
        return deepgram.STT(api_key=config.DEEPGRAM_KEY, model=config.DEEPGRAM_MODELE,
                            language=langue, punctuate=True, **extra)
    if cle == "speechmatics":
        from livekit.plugins import speechmatics
        return speechmatics.STT(language=courte, additional_vocab=mots[:100])
    if cle == "gladia":
        from livekit.plugins import gladia
        return gladia.STT(languages=[courte], interim_results=True,
                          custom_vocabulary=mots[:100])
    if cle == "assemblyai":
        from livekit.plugins import assemblyai
        # `language_codes` au pluriel : le singulier est deprecie depuis la 1.6.
        return assemblyai.STT(language_codes=[courte], keyterms_prompt=mots[:100])
    if cle == "groq":
        from livekit.plugins import groq
        from livekit.agents import stt as stt_api
        # Batch : le VAD decoupe, le moteur transcrit. Sans StreamAdapter il ne recevrait
        # jamais de fin de phrase.
        moteur = groq.STT(language=courte, prompt=", ".join(mots[:40]))
        return stt_api.StreamAdapter(stt=moteur, vad=vad) if vad else moteur
    if cle == "local":
        import stt_local
        return stt_local.local(vad) if vad else stt_local.WhisperLocal()
    raise ValueError(f"moteur de reconnaissance inconnu : {cle}")


# La preference vit hors du depot, a cote des secrets : c'est un reglage de machine, pas de
# projet. Un fichier plutot qu'une variable d'environnement, pour qu'un choix fait dans le
# tableau survive a la fermeture du terminal.
PREFERENCE = Path.home() / ".config" / "claude-talk" / "stt.json"


def preference() -> str | None:
    """L'ordre choisi depuis le tableau, s'il y en a un."""
    try:
        import json
        d = json.loads(PREFERENCE.read_text(encoding="utf-8"))
        ordre = d.get("ordre")
        return ordre if isinstance(ordre, str) and ordre.strip() else None
    except (OSError, ValueError):
        return None


def enregistrer_preference(ordre: str | None):
    """Retenir un ordre, ou l'oublier pour revenir a l'ordre par defaut."""
    import json
    PREFERENCE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PREFERENCE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"ordre": ordre or ""}), encoding="utf-8")
    tmp.replace(PREFERENCE)


def chaine(demande: str | None = None) -> list[str]:
    """La chaine de repli reellement utilisable, dans l'ordre.

    `demande` accepte « auto » (l'ordre par defaut, filtre sur les cles presentes) ou une
    liste separee par des virgules pour imposer son propre ordre. Un moteur sans cle est
    retire plutot que de faire echouer la premiere phrase : degrader, jamais tomber.

    Le local ferme toujours la marche. C'est le seul qui ne peut pas manquer de credit, donc
    le seul qui garantit qu'on ne devienne jamais sourd.
    """
    # Priorite : ce qu'on demande explicitement, puis la variable d'environnement si elle a
    # ete posee a la main, puis le choix enregistre dans le tableau, puis l'ordre par defaut.
    import os
    if demande is None:
        env = os.environ.get("VOIX_STT", "").strip()
        demande = env or preference() or "auto"
    demande = (demande or "auto").strip()
    if demande in ("", "auto"):
        voulus = [m.cle for m in MOTEURS]
    else:
        voulus = [c.strip() for c in demande.split(",") if c.strip()]
    retenus = [c for c in voulus if c in PAR_CLE and PAR_CLE[c].dispo]
    if "local" not in retenus:
        retenus.append("local")
    return retenus


def resume() -> str:
    """Ce que le panneau de configuration affiche au lancement."""
    c = chaine()
    return " → ".join(PAR_CLE[k].libelle for k in c)


def inventaire() -> list[dict]:
    """La table, telle que le tableau de bord la consomme."""
    active = chaine()
    return [{
        "cle": m.cle, "libelle": m.libelle, "gratuit": m.gratuit,
        "renouvelable": m.renouvelable, "streaming": m.streaming,
        "note": m.note, "dispo": m.dispo,
        "rang": active.index(m.cle) if m.cle in active else None,
    } for m in MOTEURS]
