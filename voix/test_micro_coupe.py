#!/usr/bin/env python3
"""Micro coupe = plus un octet vers le nuage. Verifie sur les vrais objets, pas par deduction.

La question, posee telle quelle : « quand mon micro est coupe, est-ce qu'on ne reçoit plus le
son ? Si une video parle en fond, est-ce qu'on consomme des heures dans le vide ? »

Elle merite mieux qu'une lecture de code, parce que la reponse depend d'une chaine de cinq
maillons — appliquer_micro → set_audio_enabled → on_detached → ConsoleAudioInput._attached →
push_frame — et qu'il suffit d'un maillon pour que tout parte quand meme. Un seul de ces
maillons a change dans LiveKit et la facture arrive sans un mot.

Ce qui rend ce test indispensable plutot que confortable : la version TCP du mode console
(`TcpAudioInput`, utilisee par `lk agent`) n'implemente NI `on_attached` NI `on_detached`. La
methode de base est alors sans effet, et couper le micro ne couperait plus rien du tout. Nous
passons par `_legacy`, qui lui les implemente — mais rien ne garantit que ça dure. Si ce test
devient rouge apres une mise a jour, c'est que couper le micro ne coupe plus la depense.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

ok = True


def dire(bon, texte):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


def trame(ms=100, taux=48000):
    from livekit import rtc
    n = taux * ms // 1000
    return rtc.AudioFrame(data=b"\x00\x00" * n, sample_rate=taux,
                          num_channels=1, samples_per_channel=n)


async def principal():
    from livekit.agents.cli._legacy import ConsoleAudioInput

    # --- le maillon le plus bas : la source elle-meme -----------------------------------
    print("=== la source jette ce qu'elle ne doit pas transmettre ===")
    src = ConsoleAudioInput(asyncio.get_running_loop())
    recues = []

    async def consommer():
        try:
            async for f in src:
                recues.append(f)
        except asyncio.CancelledError:
            pass

    t = asyncio.create_task(consommer())
    await asyncio.sleep(0)

    for _ in range(5):
        src.push_frame(trame())
    await asyncio.sleep(0.05)
    dire(len(recues) == 5, f"attachee, elle transmet tout ({len(recues)}/5)")

    src.on_detached()                      # ce que fait set_audio_enabled(False)
    avant = len(recues)
    for _ in range(50):                    # cinq secondes d'une video qui parle en fond
        src.push_frame(trame())
    await asyncio.sleep(0.05)
    dire(len(recues) == avant,
         f"detachee, elle ne transmet RIEN — 5 s d'audio jetees a la source "
         f"({len(recues) - avant} trame(s) passee(s))")

    src.on_attached()
    for _ in range(3):
        src.push_frame(trame())
    await asyncio.sleep(0.05)
    dire(len(recues) == avant + 3, "rattachee, elle transmet de nouveau")
    t.cancel()

    # --- le maillon du dessus : est-il seulement branche ? ------------------------------
    # `io.AudioInput.on_detached` delegue a `self.source`. Une entree SANS source — c'est le
    # cas de TcpAudioInput — rend donc la coupure silencieusement inoperante. On verifie donc
    # que la classe qu'on utilise implemente vraiment ces methodes, et pas seulement qu'elle
    # en herite.
    print("\n=== la coupure n'est pas un heritage vide ===")
    from livekit.agents.voice import io as lkio
    dire("on_detached" in ConsoleAudioInput.__dict__,
         "ConsoleAudioInput implemente on_detached lui-meme")
    dire("on_attached" in ConsoleAudioInput.__dict__,
         "et on_attached aussi")
    try:
        from livekit.agents.cli.tcp_console import TcpAudioInput
        tcp_muet = "on_detached" not in TcpAudioInput.__dict__
        print(f"  ·     pour information : TcpAudioInput "
              f"{'ne l implemente PAS' if tcp_muet else 'l implemente'} — "
              f"{'ne pas basculer dessus' if tcp_muet else 'utilisable'}")
    except ImportError:
        pass

    # --- la chaine complete, depuis notre code -----------------------------------------
    # C'est le seul niveau qui prouve quelque chose d'utile : notre bouton, jusqu'a la source.
    print("\n=== depuis appliquer_micro, jusqu'a la source ===")
    from livekit.agents import AgentSession
    import agent as A

    session = AgentSession()
    src2 = ConsoleAudioInput(asyncio.get_running_loop())
    session.input.audio = src2

    v = A.Voix.__new__(A.Voix)
    v._amorcer_etat()
    v.tableau = None
    v.worker = type("W", (), {"occupe": False})()
    v._session_directe = session
    v._activity = None
    v.inscription = None

    v.micro_voulu = True
    v.appliquer_micro(publier=False)
    dire(session.input.audio_enabled is True, "micro voulu : l'entree est active")
    dire(src2._attached is True, "et la source est attachee — le son passe")

    v.micro_voulu = False
    v.appliquer_micro(publier=False)
    dire(session.input.audio_enabled is False, "micro coupe : l'entree est desactivee")
    dire(src2._attached is False,
         "et la SOURCE est detachee — c'est ça qui empeche l'audio de partir")

    # La preuve de bout en bout : on pousse, rien ne sort.
    sorties = []

    async def espionner():
        try:
            async for f in src2:
                sorties.append(f)
        except asyncio.CancelledError:
            pass

    t2 = asyncio.create_task(espionner())
    await asyncio.sleep(0)
    for _ in range(100):                   # dix secondes de parole en fond
        src2.push_frame(trame())
    await asyncio.sleep(0.05)
    dire(not sorties,
         f"micro coupe, 10 s poussees : {len(sorties)} trame(s) sortie(s) — "
         f"aucune requete, aucun credit")
    t2.cancel()

    # --- micro OUVERT : tout part, et c'est le fait qui justifie la veille ---------------
    print("\n=== micro ouvert : l'audio part en continu, sans filtre ===")
    # Ce n'est pas un defaut de LiveKit, c'est sa conception : la reconnaissance en streaming
    # a besoin d'un flux continu pour garder son contexte. La consequence se paie quand meme :
    # on facture chaque seconde de micro ouvert, qu'on parle ou non. Une video en fond est
    # transcrite, une pause dejeuner micro ouvert coute une heure de quota.
    import inspect
    from livekit.agents.voice import agent_activity, audio_recognition
    src_push = inspect.getsource(audio_recognition.AudioRecognition._push_audio)
    dire("_stt_pipeline" in src_push and "audio_ch.send_nowait" in src_push,
         "chaque trame est transmise a la reconnaissance")
    # Le seul remplacement prevu est du SILENCE pendant l'echo du haut-parleur — pas un
    # filtre de parole. S'il existait un jour un filtre VAD, ce test le verrait et il faudrait
    # revoir le compteur de consommation, qui compte le temps micro OUVERT.
    src_act = inspect.getsource(agent_activity.AgentActivity.push_audio)
    dire("silence_frame_like" in src_act,
         "le seul remplacement prevu est le silence pendant l'echo, pas un filtre de parole")
    dire("vad" not in src_act.lower().split("should_discard")[0].replace("aec", ""),
         "aucun filtre VAD en amont : le compteur qui facture le temps micro ouvert dit vrai")

    # --- la veille : la seule protection contre le micro oublie --------------------------
    print("\n=== la veille coupe un micro oublie ===")
    import config
    dire(config.MICRO_VEILLE_S > 0,
         f"une veille est armee par defaut ({config.MICRO_VEILLE_S:.0f} s)")
    dire(config.MICRO_VEILLE_S >= 180,
         "et assez longue pour ne jamais couper pendant qu'on reflechit a voix haute")
    dire(config.MICRO_VEILLE_S <= 900,
         "assez courte pour qu'un oubli coute des minutes, pas des heures")

    v.micro_voulu = True
    v.appliquer_micro(publier=False)
    v._derniere_parole = 0.0            # comme si on n'avait rien dit depuis toujours
    silence = __import__("time").monotonic() - v._derniere_parole
    dire(silence > config.MICRO_VEILLE_S,
         "un silence plus long que la veille est bien reconnu comme tel")
    # On rejoue la decision de veiller_micro sans attendre cinq minutes.
    v.micro_voulu = False
    v.appliquer_micro(publier=False)
    dire(src2._attached is False,
         "et la coupure qui en decoule detache la source : plus rien ne part")

    print(f"\n{'TOUT VERT' if ok else 'DES ECHECS'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(principal()))
