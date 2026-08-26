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
    # Le texte s'ecrit-il PENDANT qu'on parle ? C'est ce qui rend la relecture possible avant
    # envoi. Un moteur sans resultats intermediaires ne rend son texte qu'a la fin, apres que
    # le tour est parti — donc « retenir » devient inutilisable, et le texte semble apparaitre
    # sans raison. Distinct de `streaming` : le local est un flux, sans interim pour autant.
    direct: bool
    note: str = ""
    # Le palier gratuit en HEURES, exploitable par un compteur. `gratuit` dit la meme chose
    # en français pour l'humain ; ce champ existe pour que le suivi de consommation puisse
    # calculer un reste. None = illimite (local) ou non chiffrable (limites journalieres).
    # Les credits en dollars sont convertis au tarif temps reel du fournisseur, d'ou des
    # valeurs arrondies : un ordre de grandeur suffit pour voir venir l'epuisement.
    quota_h: float | None = None
    # Combien d'heures garder EN RESERVE sur un credit unique. Au-dessus de ce seuil le
    # credit est traite comme abondant et passe devant les quotas mensuels : autant profiter
    # du meilleur moteur. En dessous, il repasse derriere et se garde pour les mois ou les
    # mensuels sont epuises. Sans ce seuil il fallait choisir une fois pour toutes entre
    # « le meilleur maintenant » et « la reserve intacte » — le seuil rend les deux vrais,
    # chacun a son moment. Ne concerne pas les quotas renouvelables : ils reviennent.
    reserve_h: float | None = None
    # Rang de preference a qualite egale de palier, 1 = le meilleur. Vient du banc quand on
    # a mesure (Deepgram, local), des bancs publics sinon — et un moteur non mesure ne se
    # declare jamais premier. Ce champ existe parce que l'ordre de declaration ne peut pas
    # porter deux logiques a la fois : la nature du palier ET la qualite.
    qualite: int = 5

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
           "8 h/mois, renouvele, sans carte", True, True, True,
           "le meilleur taux d'erreur des bancs publics d'aout 2026 (6,4 %)", quota_h=8, qualite=2),
    Moteur("gladia", "Gladia", "GLADIA_API_KEY",
           "4 h/mois de temps reel, renouvele", True, True, True,
           "annonce par son editeur comme le meilleur sur le francais", quota_h=4, qualite=3),
    Moteur("azure", "Azure", "AZURE_SPEECH_KEY",
           "5 h/mois au palier F0, renouvele", True, True, True,
           "aussi utilise pour la synthese vocale", quota_h=5, qualite=4),
    Moteur("assemblyai", "AssemblyAI", "ASSEMBLYAI_API_KEY",
           "50 $ de credits a l'inscription (~300 h)", False, True, True,
           "credit unique : garde-le pour quand les quotas mensuels sont epuises", quota_h=330, reserve_h=50, qualite=3),
    Moteur("deepgram", "Deepgram", "DEEPGRAM_API_KEY",
           "200 $ de credits a l'inscription", False, True, True,
           "nova-3, tres faible latence", quota_h=430, reserve_h=100, qualite=1),
    Moteur("soniox", "Soniox", "SONIOX_API_KEY",
           "credits gratuits a l'inscription", False, True, True,
           "temps reel avec resultats intermediaires ; a mesurer sur ta voix",
           quota_h=50, reserve_h=10, qualite=3),
    Moteur("google", "Google Cloud", "GOOGLE_APPLICATION_CREDENTIALS",
           "60 min/mois a vie (le credit d'essai n'est pas compte)", True, True, True,
           "le palier a vie est petit : bon comme dernier recours en ligne",
           quota_h=1, qualite=4),
    Moteur("groq", "Groq", "GROQ_API_KEY",
           "palier gratuit avec limites journalieres", True, False, False,
           "whisper-large-v3 rapide, mais le texte n'arrive qu'a la fin de la phrase",
           qualite=5),
    Moteur("local", "local (faster-whisper)", None,
           "illimite, hors ligne", True, True, False,
           "4 a 7 s par phrase, et AUCUN texte en direct ; l'audio ne quitte pas la machine",
           qualite=6),
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
        from speechmatics.voice._models import AdditionalVocabEntry
        # Pas une liste de chaines : le plugin attend des objets `AdditionalVocabEntry`.
        # Mesure : passer des chaines leve « 'str' object has no attribute 'content' » — et
        # ca cassait le moteur ENTIER, pas seulement le biais de vocabulaire.
        return speechmatics.STT(
            language=courte,
            additional_vocab=[AdditionalVocabEntry(content=m) for m in mots[:100]])
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


# --- l'ordre automatique -----------------------------------------------------------------
# Quatre classes, dans cet ordre. Ce qui les separe est une question a chaque fois differente :
#
#   0. un credit unique encore ABONDANT (au-dessus de sa reserve). Autant profiter du
#      meilleur moteur maintenant : 430 h couvrent cinq ans a raison de 7 h/mois, donc
#      « garder la reserve » serait theorique tant qu'elle est pleine.
#   1. un quota mensuel. Il revient le mois prochain : le depenser ne coute rien.
#   2. un credit unique passe SOUS sa reserve. Il se garde pour les mois ou les mensuels
#      seront epuises — c'est precisement a ça qu'il sert.
#   3. les moteurs sans texte en direct, et le local. Ils transcrivent tres bien mais ne
#      rendent rien avant la fin de la phrase, ce qui supprime la relecture avant envoi.
#      Utilisables, jamais souhaitables.
#
# Et hors classe : un moteur constate epuise part a la fin, quelle que soit sa qualite.
# A classe egale, `qualite` tranche.


def _classe(cle: str, restes: dict) -> tuple[int, int]:
    m = PAR_CLE[cle]
    e = restes.get(cle) or {}
    if cle == "local" or not m.direct:
        return (3, m.qualite)
    if e.get("epuise") or (e.get("reste_s") is not None and e["reste_s"] <= 0):
        return (4, m.qualite)
    if not m.renouvelable:
        assez = e.get("reste_s") is None or e["reste_s"] > (m.reserve_h or 0) * 3600
        return ((0 if assez else 2), m.qualite)
    return (1, m.qualite)


def ordre_auto() -> list[str]:
    """L'ordre par defaut, recalcule d'apres ce qu'il reste reellement sur chaque palier."""
    try:
        import consommation
        restes = {e["cle"]: e for e in consommation.etat(MOTEURS)}
    except Exception:
        # Sans compteur lisible on retombe sur l'ordre de declaration : degrader, jamais
        # tomber. Un ordre imparfait vaut mieux qu'une session qui refuse de demarrer.
        restes = {}
    return sorted((m.cle for m in MOTEURS), key=lambda c: _classe(c, restes))


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
        voulus = ordre_auto()
    else:
        voulus = [c.strip() for c in demande.split(",") if c.strip()]
    retenus = [c for c in voulus if c in PAR_CLE and PAR_CLE[c].dispo]
    if "local" not in retenus:
        retenus.append("local")
    return retenus


def tete() -> Moteur:
    """Le moteur qui transcrira, sauf panne."""
    return PAR_CLE[chaine()[0]]


def resume() -> str:
    """Ce que le panneau de configuration affiche au lancement."""
    c = chaine()
    return " → ".join(PAR_CLE[k].libelle for k in c)


def inventaire() -> list[dict]:
    """La table, telle que le tableau de bord la consomme."""
    active = chaine()
    return [{
        "cle": m.cle, "libelle": m.libelle, "gratuit": m.gratuit,
        "renouvelable": m.renouvelable, "streaming": m.streaming, "direct": m.direct,
        "note": m.note, "dispo": m.dispo,
        "rang": active.index(m.cle) if m.cle in active else None,
    } for m in MOTEURS]
