"""« Hey Claude » — assistant d'arrière-plan, question / réponse, sans conversation.

Séparé de l'agent de développement : son propre processus, sa propre session Claude, son
propre modèle. Il ne touche pas à ce que fait `vv`.

Le coût est le sujet central, et il se règle par un empilement de verrous avant Azure. À
1 $/heure d'audio, écouter en continu coûterait ~240 $/mois ; n'envoyer que ce qui a une
chance d'être un appel ramène ça à quelques dollars. Dans l'ordre, un segment n'atteint
Azure que si :

  1. le micro système n'est pas coupé (pactl) ;
  2. les haut-parleurs ne jouent rien — sinon on transcrirait les vidéos et les visios,
     ce qui est à la fois la principale source de gaspillage et la plus inutile ;
  3. le VAD local (Silero, gratuit) y a entendu de la parole ;
  4. la durée est plausible pour une phrase ;
  5. le plafond quotidien n'est pas atteint.

Rien de tout ça ne coûte un centime. Seule l'étape suivante est facturée.

Usage :
  python voix/assistant.py              écoute
  python voix/assistant.py --diag       mesure les niveaux, pour régler les seuils
  python voix/assistant.py --fichier x.wav   rejoue un fichier dans toute la chaîne
"""

import argparse
import asyncio
import logging
import json
import subprocess
import sys
import time
import re
import unicodedata
import wave
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import sounddevice as sd
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    TextBlock,
    ThinkingConfigDisabled,
)
from livekit import rtc
from livekit.agents import vad as vad_api
from livekit.plugins import silero

import config

log = logging.getLogger("heyclaude")

TAUX = 16000
TRAME = 512  # 32 ms
ETAT = Path.home() / ".config" / "claude-talk" / "assistant.json"
# Presence de ce fichier = ecoute suspendue. Un fichier plutot qu'un signal : le raccourci
# clavier doit marcher meme si l'assistant vient de redemarrer, et l'etat doit survivre.
COUPE = Path.home() / ".config" / "claude-talk" / "ecoute-coupee"

# --- réveil ------------------------------------------------------------------
# Azure francise l'anglais : « Hey Claude » revient en « Aie Claude », « Et Claude », et
# même « Dix Claude ». Chercher l'interjection est donc perdu d'avance ; ce qui survit,
# c'est « Claude ». On exige seulement qu'il apparaisse tôt dans la phrase.
NOMS = ("claude", "clode", "cloude", "clod", "glaude", "claud")
MOTS_AVANT = 3


def _sans_accent(texte: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texte.lower())
                   if unicodedata.category(c) != "Mn")


def reveil(texte: str) -> str | None:
    """Returns the question if the utterance is addressed to Claude, else None."""
    mots = _sans_accent(texte).replace(",", " ").split()
    for i, mot in enumerate(mots[:MOTS_AVANT]):
        propre = mot.strip(".!?;:")
        if any(propre.startswith(n) or n.startswith(propre) and len(propre) > 3
               for n in NOMS):
            question = " ".join(texte.replace(",", " ").split()[i + 1:]).strip(" ,.?!")
            return question or ""
    return None


# Formulations qui closent la conversation. Exigees courtes et quasi seules dans la phrase :
# « a la fin du fichier » ne doit pas raccrocher.
FIN = re.compile(
    r"^(?:c'?\s?est (?:bon|tout|fini)|fin(?: de (?:la )?conversation)?|termin(?:e|er|ee)"
    r"|on (?:arrete|termine)|merci(?: c'?\s?est (?:bon|tout))?|au revoir|a plus|bye"
    r"|stop|arrete(?: la conversation)?|laisse tomber)\b", re.I)
MOTS_FIN_MAX = 5


def fin_demandee(texte: str) -> bool:
    propre = _sans_accent(texte).strip(" ,.!?").strip()
    return len(propre.split()) <= MOTS_FIN_MAX and bool(FIN.match(propre))


# --- verrous -----------------------------------------------------------------
class Sortie:
    """Is the machine producing sound? Reads the default sink's monitor.

    Without this the assistant transcribes every video and meeting that plays, which is the
    largest and least useful part of the bill."""

    def __init__(self, seuil: float, hangover: float = 1.5):
        self.seuil = seuil
        self.hangover = hangover
        self.niveau = 0.0
        self._jusqu_a = 0.0
        self._proc: subprocess.Popen | None = None

    def moniteur(self) -> str:
        sink = subprocess.run(["pactl", "get-default-sink"],
                              capture_output=True, text=True).stdout.strip()
        return f"{sink}.monitor"

    async def boucle(self):
        self._proc = subprocess.Popen(
            ["parecord", f"--device={self.moniteur()}", "--format=s16le",
             f"--rate={TAUX}", "--channels=1", "--raw", "--latency-msec=100"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        boucle = asyncio.get_running_loop()
        while self._proc and self._proc.stdout:
            brut = await boucle.run_in_executor(None, self._proc.stdout.read, TAUX)
            if not brut:
                break
            a = np.frombuffer(brut, dtype=np.int16)
            self.niveau = float(np.sqrt((a.astype(np.float64) ** 2).mean())) if a.size else 0.0
            if self.niveau >= self.seuil:
                self._jusqu_a = time.monotonic() + self.hangover

    @property
    def active(self) -> bool:
        return time.monotonic() < self._jusqu_a

    def stop(self):
        if self._proc:
            self._proc.terminate()


class MicroSysteme:
    """pactl mute state. If you muted the microphone in the OS, listening is pointless."""

    def __init__(self):
        self.coupe = False

    async def boucle(self):
        while True:
            r = subprocess.run(["pactl", "get-source-mute", "@DEFAULT_SOURCE@"],
                               capture_output=True, text=True)
            self.coupe = "yes" in r.stdout.lower() or "oui" in r.stdout.lower()
            await asyncio.sleep(2.0)


class Plafond:
    """Real Azure spend today, in cents, covering transcription AND speech synthesis.

    A cap counted in minutes only bounded the transcription; the spoken answers went past
    the counter entirely. Both are billed, so both are counted. The check happens *before*
    each call, so the ceiling cannot be overshot — a segment that would not fit is refused
    rather than truncated."""

    def __init__(self, centimes: float):
        self.limite = centimes
        self.jour = date.today().isoformat()
        self.stt_s = 0.0
        self.tts_car = 0
        # Les jours passes, archives au lieu d'etre ecrases : sans ca la question « combien
        # j'ai depense jusqu'a present » n'avait aucune reponse cote outil.
        self.histoire: dict[str, dict] = {}
        self._charger()

    # --- tarifs ---
    @staticmethod
    def cout_stt(secondes: float) -> float:
        return secondes / 3600 * config.TARIF_STT_H * 100

    @staticmethod
    def cout_tts(caracteres: int) -> float:
        return caracteres / 1_000_000 * config.TARIF_TTS_M * 100

    @property
    def centimes(self) -> float:
        return self.cout_stt(self.stt_s) + self.cout_tts(self.tts_car)

    def _charger(self):
        try:
            d = json.loads(ETAT.read_text(encoding="utf-8"))
        except Exception:
            return
        self.histoire = d.get("histoire") or {}
        if d.get("jour") == self.jour:
            self.stt_s = float(d.get("stt_s", 0.0))
            self.tts_car = int(d.get("tts_car", 0))
        elif d.get("jour"):
            # Le service a tourne hier : on archive avant de repartir de zero.
            self.histoire[d["jour"]] = {"stt_s": float(d.get("stt_s", 0.0)),
                                        "tts_car": int(d.get("tts_car", 0))}
            self._ecrire()

    def _ecrire(self):
        ETAT.parent.mkdir(parents=True, exist_ok=True)
        ETAT.write_text(json.dumps({"jour": self.jour, "stt_s": round(self.stt_s, 1),
                                    "tts_car": self.tts_car, "histoire": self.histoire},
                                   indent=1), encoding="utf-8")

    def _basculer_si_nouveau_jour(self):
        aujourd_hui = date.today().isoformat()
        if aujourd_hui == self.jour:
            return
        self.histoire[self.jour] = {"stt_s": round(self.stt_s, 1), "tts_car": self.tts_car}
        self.jour, self.stt_s, self.tts_car = aujourd_hui, 0.0, 0
        self._ecrire()

    def reste(self) -> float:
        self._basculer_si_nouveau_jour()
        return max(0.0, self.limite - self.centimes)

    def cumul(self, jours: int | None = None) -> tuple[float, float, int, int]:
        """(centimes, minutes transcrites, caracteres lus, nombre de jours) sur une fenetre.

        C'est une ESTIMATION a partir des tarifs configures, pas un releve de facturation :
        la cle Speech authentifie l'usage, pas la facture. Le chiffre qui fait foi est dans
        le portail Azure, Cost Management."""
        self._basculer_si_nouveau_jour()
        limite = None
        if jours is not None:
            limite = (date.today() - timedelta(days=jours - 1)).isoformat()
        stt = self.stt_s
        tts = self.tts_car
        compte = 1 if (self.stt_s or self.tts_car) else 0
        for j, d in self.histoire.items():
            if limite and j < limite:
                continue
            stt += float(d.get("stt_s", 0.0))
            tts += int(d.get("tts_car", 0))
            compte += 1
        return self.cout_stt(stt) + self.cout_tts(tts), stt / 60, tts, compte

    def tient(self, cout: float) -> bool:
        return cout <= self.reste()

    def consommer_stt(self, secondes: float):
        self.stt_s += secondes
        self._ecrire()

    def consommer_tts(self, caracteres: int):
        self.tts_car += caracteres
        self._ecrire()

    def resume(self) -> str:
        return (f"{self.centimes:.2f} centime(s) dépensés aujourd'hui sur {self.limite:.0f} "
                f"— {self.stt_s / 60:.1f} min transcrites, {self.tts_car} caractères lus")

    def projection(self) -> str:
        """What the current pace does to a fixed, expiring credit.

        Spending less than credit/days-left is not thrift: the student credit is forfeited at
        the 12-month mark, so under-spending burns money just as surely as over-spending."""
        ct, _, _, jours = self.cumul(7)
        par_jour = ct / max(jours, 1)
        credit_ct = config.AZURE_CREDIT_EUR * 100
        lignes = [f"  rythme des 7 derniers jours : {par_jour:.2f} ct/jour"
                  f"  ({par_jour * 365 / 100:.0f} EUR sur un an)"]
        if config.AZURE_CREDIT_FIN:
            try:
                fin = date.fromisoformat(config.AZURE_CREDIT_FIN)
                restants = max((fin - date.today()).days, 1)
                lignes.append(f"  credit : {config.AZURE_CREDIT_EUR:.0f} EUR, {restants} jours "
                              f"avant expiration")
                lignes.append(f"  a depenser pour tout consommer : "
                              f"{credit_ct / restants:.1f} ct/jour")
                if par_jour > 0:
                    lignes.append(f"  au rythme actuel : {credit_ct / par_jour:.0f} jours "
                                  f"de credit, soit "
                                  f"{min(credit_ct / par_jour / restants * 100, 999):.0f} % "
                                  f"du temps restant couvert")
            except ValueError:
                lignes.append("  VOIX_CREDIT_FIN illisible (attendu AAAA-MM-JJ)")
        else:
            lignes.append(f"  credit : {config.AZURE_CREDIT_EUR:.0f} EUR — pose VOIX_CREDIT_FIN"
                          f" (AAAA-MM-JJ) pour la projection exacte")
            lignes.append(f"  sur 12 mois pleins, tout consommer demande "
                          f"{credit_ct / 365:.1f} ct/jour")
        lignes.append(f"  plafond actuel : {self.limite:.0f} ct/jour "
                      f"= {self.limite * 365 / 100:.0f} EUR/an au maximum")
        return "\n".join(lignes)

    def resume_cumul(self) -> str:
        lignes = []
        for libelle, jours in (("aujourd'hui", 1), ("7 derniers jours", 7),
                               ("30 derniers jours", 30), ("depuis le début", None)):
            ct, mn, car, n = self.cumul(jours)
            lignes.append(f"  {libelle:<18} {ct / 100:6.2f} $   ({ct:6.2f} ct · "
                          f"{mn:5.1f} min transcrites · {car:6d} car. lus · {n} jour(s))")
        return "\n".join(lignes)


class Notification:
    """A desktop notification that stays up while the answer is being produced.

    Critical urgency persists on most desktops, so the working state cannot be missed; the
    final one carries a timeout so it dismisses itself."""

    def __init__(self):
        self.id: str | None = None

    def _envoyer(self, titre: str, corps: str, critique: bool, timeout: int | None = None):
        cmd = ["notify-send", "-a", "Hey Claude", "-i", "audio-input-microphone", "-p"]
        cmd += ["-u", "critical" if critique else "normal"]
        if timeout is not None:
            cmd += ["-t", str(timeout)]
        if self.id:
            cmd += ["-r", self.id]
        cmd += [titre, corps]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            sortie = r.stdout.strip()
            if sortie.isdigit():
                self.id = sortie
        except Exception:
            pass

    def ecoute(self):
        self._envoyer("Hey Claude", "détecté — je transcris…", critique=True)

    def reflechit(self, question: str):
        self._envoyer("Hey Claude", f"« {question} »\n\nje cherche la réponse…", critique=True)

    def repond(self, reponse: str):
        self._envoyer("Hey Claude", reponse[:400], critique=False, timeout=12000)

    def rien(self, raison: str):
        self._envoyer("Hey Claude", raison, critique=False, timeout=4000)

    def transitoire(self, corps: str, ms: int = 2500):
        """Discreet and short-lived: it says the microphone was actually used, whatever was
        said. Knowing when audio leaves the machine matters even when the content does not."""
        self._envoyer("Hey Claude", corps, critique=False, timeout=ms)


class NotificationPersistante:
    """Stays until the user clicks it. Any click closes it, which is the signal to end the
    conversation — that is what "cliquer sur la notification" means, whichever button dunst
    maps. `-t 0` keeps it from expiring on its own, so a timeout can never be mistaken for
    a click."""

    def __init__(self):
        self.proc: asyncio.subprocess.Process | None = None
        self.id: str | None = None

    async def afficher(self, titre: str, corps: str) -> asyncio.Task | None:
        await self.fermer()
        self.proc = await asyncio.create_subprocess_exec(
            "notify-send", "-a", "Hey Claude", "-u", "critical", "-t", "0", "-p", "-w",
            "-i", "audio-input-microphone", titre, corps,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            ligne = await asyncio.wait_for(self.proc.stdout.readline(), timeout=5)
            ident = ligne.decode().strip()
            self.id = ident if ident.isdigit() else None
        except (asyncio.TimeoutError, AttributeError):
            self.id = None
        return None

    async def attendre_clic(self):
        if self.proc:
            await self.proc.wait()

    async def fermer(self):
        if self.id:
            subprocess.run(["gdbus", "call", "--session",
                            "--dest", "org.freedesktop.Notifications",
                            "--object-path", "/org/freedesktop/Notifications",
                            "--method", "org.freedesktop.Notifications.CloseNotification",
                            self.id], capture_output=True)
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
        self.proc, self.id = None, None


# --- Azure -------------------------------------------------------------------
def _wav(pcm: bytes) -> bytes:
    import io

    tampon = io.BytesIO()
    with wave.open(tampon, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TAUX)
        w.writeframes(pcm)
    return tampon.getvalue()


async def transcrire(pcm: bytes) -> str:
    import aiohttp

    url = (f"https://{config.AZURE_REGION}.stt.speech.microsoft.com/speech/recognition"
           f"/conversation/cognitiveservices/v1?language={config.LANGUAGE}&format=simple")
    async with aiohttp.ClientSession() as s:
        async with s.post(url, data=_wav(pcm), headers={
            "Ocp-Apim-Subscription-Key": config.AZURE_KEY,
            "Content-Type": f"audio/wav; codecs=audio/pcm; samplerate={TAUX}",
        }, timeout=aiohttp.ClientTimeout(total=25)) as r:
            if r.status != 200:
                return ""
            return (await r.json()).get("DisplayText", "").strip()


async def synthetiser(texte: str) -> bytes:
    import aiohttp

    ssml = (f"<speak version='1.0' xml:lang='{config.LANGUAGE}'>"
            f"<voice name='{config.AZURE_VOICE}'>{texte}</voice></speak>")
    url = f"https://{config.AZURE_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
    async with aiohttp.ClientSession() as s:
        async with s.post(url, data=ssml.encode("utf-8"), headers={
            "Ocp-Apim-Subscription-Key": config.AZURE_KEY,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm",
        }, timeout=aiohttp.ClientTimeout(total=30)) as r:
            return await r.read() if r.status == 200 else b""


async def jouer(wav: bytes):
    if not wav:
        return
    p = await asyncio.create_subprocess_exec(
        "aplay", "-q", "-", stdin=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL)
    await p.communicate(wav)


# --- l'assistant -------------------------------------------------------------
SYSTEME = (
    "Tu es dans une conversation parlée : tes réponses sont lues par une synthèse vocale et "
    "la personne peut enchaîner. Réponds en prose continue, sans markdown, sans liste, sans "
    "tableau. Deux à quatre phrases, directes, sans préambule ni « voici ». Va au fait. "
    "Tiens compte de ce qui a déjà été dit dans l'échange. Si tu ne sais pas, dis-le en une "
    "phrase. Écris le français avec ses accents."
)


class Assistant:
    def __init__(self, plafond_centimes: float, seuil_sortie: float):
        self.sortie = Sortie(seuil_sortie)
        self.micro = MicroSysteme()
        self.plafond = Plafond(plafond_centimes)
        self.notif = Notification()
        # Sa propre identite de notification, sinon elle ecraserait celle de la reponse.
        self.notif_ecoute = Notification()
        self.notif_conv = NotificationPersistante()
        self.conversation = False
        self._dernier_echange = 0.0
        self._veille_clic: asyncio.Task | None = None
        # Deux jours de silence total sont passes inapercus parce qu'aucun verrou ne se
        # signalait. On memorise l'etat pour ne parler qu'aux changements, et un battement
        # periodique redit ou on en est meme quand rien ne bouge.
        self._dernier_barrage: str | None = "demarrage"
        self._plafond_annonce = False
        self._alerte_80 = False
        self.notif_plafond = NotificationPersistante()
        self.client: ClaudeSDKClient | None = None
        self.vad = None
        self.occupe = False

    async def demarrer(self):
        self.vad = silero.VAD.load(min_speech_duration=0.2, min_silence_duration=0.6)
        await self.demarrer_client()

    async def demarrer_client(self):
        self.client = ClaudeSDKClient(ClaudeAgentOptions(
            model=config.EVEIL_MODELE,
            cli_path=config.claude_binary(),
            cwd=str(Path.home()),
            allowed_tools=[],
            include_partial_messages=False,
            thinking=ThinkingConfigDisabled(type="disabled"),
            system_prompt=SYSTEME,
        ))
        await self.client.connect()

    async def parler(self, texte: str) -> bool:
        """Every spoken word goes through here, so the cap covers synthesis too. Speech is
        what actually costs — eleven times a transcription — so it cannot bypass the tally."""
        if not texte:
            return False
        cout = Plafond.cout_tts(len(texte))
        if not self.plafond.tient(cout):
            log.warning("plafond : réponse non lue à voix haute")
            self.notif.rien("plafond atteint — réponse affichée, non lue")
            return False
        self.plafond.consommer_tts(len(texte))
        await jouer(await synthetiser(texte))
        return True

    async def demarrer_conversation(self):
        self.conversation = True
        self._dernier_echange = time.monotonic()
        await self.notif_conv.afficher(
            "Conversation en cours",
            "clic pour terminer — ou dis « fin », « c'est bon », « merci »")
        self._veille_clic = asyncio.create_task(self._sur_clic())
        log.info("conversation OUVERTE")

    async def _sur_clic(self):
        await self.notif_conv.attendre_clic()
        # Le processus notify-send sort des que la notification est fermee, par n'importe
        # quel bouton. C'est le signal de fin le plus intuitif : elle a disparu, donc c'est
        # fini. Elle n'expire jamais d'elle-meme (-t 0), donc aucun risque de confusion.
        if self.conversation:
            await self.finir_conversation("notification fermée")

    async def finir_conversation(self, raison: str):
        if not self.conversation:
            return
        self.conversation = False
        if self._veille_clic:
            self._veille_clic.cancel()
            self._veille_clic = None
        await self.notif_conv.fermer()
        self.notif.rien(f"conversation terminée — {raison}")
        log.info("conversation FERMÉE — %s", raison)
        # Contexte remis a zero pour la prochaine, en arriere-plan : sinon les questions
        # d'hier traineraient dans celles de demain.
        asyncio.create_task(self._repartir_a_zero())

    async def _repartir_a_zero(self):
        """New client first, swap, then drop the old one.

        Disconnecting before reconnecting left a window where self.client pointed at a dead
        session: a segment arriving during those ~2.5 s would have failed. Build then swap."""
        ancien = self.client
        try:
            await self.demarrer_client()
        except Exception:
            self.client = ancien  # mieux vaut l'ancien contexte que plus de client du tout
            return
        if ancien is not None:
            try:
                await ancien.disconnect()
            except Exception:
                pass

    async def battement(self, periode: float = 300.0):
        """Says where things stand even when nothing happens.

        This exists because the assistant sat suspended for two days while systemd reported
        `active (running)`: a process that is alive but deaf must say so, or the silence is
        indistinguishable from a crash."""
        while True:
            await asyncio.sleep(periode)
            # Force le basculement de jour meme sans trafic : sinon le compteur reste bloque
            # sur la date d'hier et le plafond ne se libere jamais.
            avant = self.plafond.jour
            self.plafond.reste()
            if self.plafond.jour != avant:
                self._plafond_annonce = self._alerte_80 = False
                await self.notif_plafond.fermer()
                log.info("nouveau jour : plafond remis à zéro (%s -> %s)",
                         avant, self.plafond.jour)
            etat = self.barrage() or (
                "conversation en cours" if self.conversation else "à l'écoute")
            log.info("état : %s | %s", etat, self.plafond.resume())

    async def surveiller_inactivite(self):
        """A conversation left open bills every ambient sentence. Silence closes it."""
        while True:
            await asyncio.sleep(5)
            if (self.conversation and not self.occupe
                    and time.monotonic() - self._dernier_echange > config.EVEIL_INACTIVITE_S):
                await self.finir_conversation(
                    f"{config.EVEIL_INACTIVITE_S:.0f} s de silence")

    def barrage(self) -> str | None:
        """Why we are not listening right now, if we are not."""
        if self.occupe:
            return "réponse en cours"
        if COUPE.exists():
            return "écoute Claude suspendue"
        if self.micro.coupe:
            return "micro coupé au niveau système"
        if self.sortie.active:
            return "les haut-parleurs jouent quelque chose"
        return None

    async def repondre(self, question: str) -> str:
        assert self.client
        await self.client.query(question)
        morceaux = []
        async for m in self.client.receive_response():
            if isinstance(m, AssistantMessage):
                morceaux += [b.text for b in m.content if isinstance(b, TextBlock)]
        return "".join(morceaux).strip()

    async def traiter(self, pcm: bytes, duree: float):
        """One VAD segment. Everything before Azure is free; keep it that way."""
        if not (0.6 <= duree <= config.EVEIL_MAX_S):
            return
        if not self.plafond.tient(Plafond.cout_stt(duree)):
            if not self._plafond_annonce:
                self._plafond_annonce = True
                log.warning("PLAFOND ATTEINT — plus aucune requête jusqu'à demain. %s",
                            self.plafond.resume())
                # Persistante : un refus silencieux est exactement ce qui a fait croire a une
                # panne. Elle reste affichee jusqu'a ce que tu la fermes.
                await self.notif_plafond.afficher(
                    "Hey Claude — plafond atteint",
                    f"plus de requête aujourd'hui.\n{self.plafond.resume()}\n"
                    f"pour relever : VOIX_EVEIL_PLAFOND_CENTIMES")
            else:
                log.info("refusé (plafond) — %s", self.plafond.resume())
            return

        self.occupe = True
        try:
            t0 = time.monotonic()
            self.notif_ecoute.transitoire(f"écoute en cours — {duree:.1f} s envoyées")
            self.plafond.consommer_stt(duree)
            if not self._alerte_80 and self.plafond.centimes >= 0.8 * self.plafond.limite:
                self._alerte_80 = True
                log.warning("plafond à 80 %% — %s", self.plafond.resume())
                await self.parler("attention, il me reste un cinquième du budget du jour.")
            texte = await transcrire(pcm)
            if not texte:
                self.notif_ecoute.transitoire("rien de compréhensible", 2000)
                return
            if self.conversation:
                # Dans une conversation, plus besoin du mot de reveil : chaque phrase est un
                # tour. Il faut donc verifier la sortie AVANT de traiter comme une question.
                if fin_demandee(texte):
                    await self.finir_conversation(f"« {texte.strip(' .!?')} »")
                    await self.parler("d'accord, à tout à l'heure.")
                    return
                question = texte
            else:
                question = reveil(texte)
                if question is None:
                    log.info("ignoré (pas d'appel) : %r", texte[:80])
                    # Transparence : tu vois ce qui a ete transcrit, meme quand c'est jete.
                    self.notif_ecoute.transitoire(f"ignoré : « {texte[:90]} »", 3000)
                    return
                await self.demarrer_conversation()
                if not question:
                    # Juste « Hey Claude » : on ouvre et on attend la suite.
                    await self.parler("oui ?")
                    self._dernier_echange = time.monotonic()
                    return

            log.info("question : %r", question)
            self.notif.reflechit(question)
            reponse = await self.repondre(question)
            if not reponse:
                reponse = "je n'ai pas réussi à répondre."
            log.info("réponse en %.1fs : %s", time.monotonic() - t0, reponse[:110])
            self.notif.repond(reponse)
            await self.parler(reponse)
            # Apres la lecture, pas avant : le temps passe a l'ecouter ne compte pas comme
            # du silence de ta part.
            self._dernier_echange = time.monotonic()
        finally:
            self.occupe = False

    async def ecouter(self):
        assert self.vad
        flux = self.vad.stream()
        file: asyncio.Queue = asyncio.Queue(maxsize=200)
        boucle = asyncio.get_running_loop()

        def rappel(indata, _frames, _time, _status):
            try:
                boucle.call_soon_threadsafe(file.put_nowait, bytes(indata))
            except Exception:
                pass

        async def alimenter():
            while True:
                brut = await file.get()
                raison = self.barrage()
                if raison != self._dernier_barrage:
                    if raison:
                        log.info("écoute SUSPENDUE — %s", raison)
                    else:
                        log.info("écoute reprise")
                    self._dernier_barrage = raison
                if raison:
                    continue  # gated: not even the VAD runs, nothing is buffered
                flux.push_frame(rtc.AudioFrame(
                    data=brut, sample_rate=TAUX, num_channels=1,
                    samples_per_channel=len(brut) // 2))

        async def recolter():
            async for ev in flux:
                if ev.type != vad_api.VADEventType.END_OF_SPEECH:
                    continue
                pcm = b"".join(bytes(f.data) for f in ev.frames)
                duree = len(pcm) / (TAUX * 2)
                asyncio.create_task(self.traiter(pcm, duree))

        with sd.RawInputStream(samplerate=TAUX, channels=1, dtype="int16",
                               blocksize=TRAME, callback=rappel):
            log.info("à l'écoute — dis « Hey Claude » pour ouvrir une conversation")
            log.info("plafond : %s", self.plafond.resume())
            await asyncio.gather(alimenter(), recolter())


async def diagnostic(seuil: float):
    """Live levels, to set the speaker threshold on this machine."""
    s = Sortie(seuil)
    m = MicroSysteme()
    asyncio.create_task(s.boucle())
    asyncio.create_task(m.boucle())
    print(f"  moniteur : {s.moniteur()}")
    print("  joue une vidéo puis coupe-la, et regarde le niveau bouger.")
    print(f"  seuil actuel : {seuil:.0f}  (VOIX_EVEIL_SEUIL pour changer)\n")
    while True:
        await asyncio.sleep(0.5)
        etat = "SORTIE ACTIVE -> on n'ecoute pas" if s.active else "silence -> on ecoute"
        mute = " | micro coupe systeme" if m.coupe else ""
        print(f"\r  niveau {s.niveau:8.1f}   {etat}{mute}        ", end="", flush=True)


async def depuis_fichier(chemin: str, plafond: float):
    """Replay a WAV through the whole chain, to test without a microphone."""
    a = Assistant(plafond, seuil_sortie=10 ** 9)  # verrou sortie neutralise
    await a.demarrer()
    with wave.open(chemin) as w:
        assert w.getframerate() == TAUX and w.getnchannels() == 1, "attendu 16 kHz mono"
        pcm = w.readframes(w.getnframes())
    log.info("fichier : %s (%.1fs)", chemin, len(pcm) / (TAUX * 2))
    await a.traiter(pcm, len(pcm) / (TAUX * 2))
    if a.client:
        await a.client.disconnect()


async def veiller(nom: str, fabrique, delai: float = 5.0):
    """Restarts a background task that dies, and says so.

    asyncio swallows the exception of a task nobody awaits: the task simply stops. That is
    how a gate, a monitor or a heartbeat can vanish without a trace."""
    while True:
        try:
            await fabrique()
            log.warning("tâche « %s » terminée sans erreur — relance dans %.0fs", nom, delai)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("tâche « %s » a échoué — relance dans %.0fs", nom, delai)
        await asyncio.sleep(delai)


async def principal(args):
    if args.diag:
        await diagnostic(config.EVEIL_SEUIL)
        return
    if args.fichier:
        await depuis_fichier(args.fichier, config.EVEIL_PLAFOND_CENTIMES)
        return
    a = Assistant(config.EVEIL_PLAFOND_CENTIMES, config.EVEIL_SEUIL)
    await a.demarrer()
    # Surveillees : une tache de fond qui meurt en silence, c'est un assistant qui parait
    # vivant et ne fait plus rien. On la relance et on le dit.
    for nom, fabrique in (("moniteur haut-parleurs", a.sortie.boucle),
                          ("état du micro", a.micro.boucle),
                          ("inactivité", a.surveiller_inactivite),
                          ("battement", a.battement)):
        asyncio.create_task(veiller(nom, fabrique))
    try:
        await a.ecouter()
    finally:
        a.sortie.stop()
        if a.client:
            await a.client.disconnect()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Hey Claude — assistant d'arriere-plan")
    p.add_argument("--diag", action="store_true", help="mesurer les niveaux audio")
    p.add_argument("--fichier", help="rejouer un WAV 16 kHz mono dans la chaine")
    args = p.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname).1s %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        asyncio.run(principal(args))
    except KeyboardInterrupt:
        print("\n  arrete")
        sys.exit(0)
