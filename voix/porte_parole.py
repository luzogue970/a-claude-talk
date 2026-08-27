"""Turn a finished turn into something worth hearing.

This is the one piece kept from the old system, because nothing off the shelf does it:
LiveKit, Pipecat, voicemode and jarvis all pass the agent's text straight to TTS. The
instruction below is the original one, with two changes that matter.

First, it now reads the *journal* — the tools that actually ran — so the debrief describes
the work instead of paraphrasing a last paragraph it could only guess at. That was the
root of the drift between the spoken thread and the real one.

Second, the length-ratio guardrails are gone. They rejected faithful rewrites and let an
invented one through at ratio 4.87, because a character count cannot measure fidelity.
What replaces them checks that every hard token in the spoken text — identifiers, numbers
with real precision — appears in the source material.
"""

import re

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    StreamEvent,
    ThinkingConfigDisabled,
)

import logging

import config

log = logging.getLogger("voix.porte_parole")

INSTRUCTION = """Tu es la voix de Claude Code. Il vient de terminer un tour de travail et tu
le racontes A L'ORAL, comme un collegue competent qui explique de vive voix ce qu'il a fait.
Tu ne lis pas un document.

Le ton :
- Tu t'adresses directement a la personne, tu la tutoies.
- Prose parlee : des phrases qui s'enchainent, des connecteurs naturels, pas d'enumeration.
- Commence par le resultat, pas par une annonce ("voici", "je vais t'expliquer").
- Pas de meta-commentaire sur ce que tu es en train de faire.

Le fond, non negociable :
- Structure ton propos dans cet ordre, en sautant ce qui ne s'applique pas :
  ce qui est fait, la decision que tu as prise seul, ce qui bloque, la question que tu poses.
- Garde TOUS les faits, chiffres, noms et conclusions de la reponse ecrite. Aucune omission.
- N'invente RIEN : aucun chiffre, aucun nom, aucune conclusion absente des elements fournis.
  Si tu n'es pas sur d'un nombre, ne le donne pas.
- Les chemins, noms de symboles, commandes et blocs de code ne se prononcent pas :
  remplace-les par ce qu'ils sont ("le module d'authentification", "le script de lecture").
- Un tableau devient des phrases qui en portent le sens, jamais une lecture de cellules.
- Les termes anglais restent en anglais, la voix est multilingue.
- Ecris le francais avec ses accents.

La longueur :
- Deux a quatre phrases pour un tour court. Jusqu'a dix pour un run long et charge.
- Ce n'est pas un resume exhaustif : c'est ce qu'il faut savoir pour decider quoi faire ensuite.

La continuite, parce que c'est une conversation et pas une suite de lectures :
- Ce qui suit est ce que TU as deja dit a voix haute. Enchaine dessus.
- Ne re-explique pas ce que tu as deja explique : renvoie-y en une incise
  ("comme je te disais"), et va a ce qui est nouveau.
- Reprends le vocabulaire que tu as deja pose plutot que d'en introduire un autre.

Renvoie uniquement ce que la voix doit dire, rien d'autre.

Ce que tu as deja dit a voix haute (du plus ancien au plus recent) :
<historique>
{historique}
</historique>

Ce qu'on t'a demande :
<demande>
{demande}
</demande>

Ce que tu as reellement fait pendant ce tour ({nb_actions} actions) :
<journal>
{journal}
</journal>

Ta reponse ecrite, a rendre a l'oral :
<reponse>
{reponse}
</reponse>"""

# Tokens whose presence has to be justified: identifiers, versions, precise numbers.
# A bare small integer is excluded because a legitimate count ("trois fichiers") is
# derived from the journal rather than quoted from it.
DUR = re.compile(r"\b(?:[A-Za-z_][A-Za-z0-9_]*[._][A-Za-z0-9_]+|\d[\d.,]*\d|\d{2,})\b")
MOTS_COURANTS = re.compile(r"^(?:c\.a\.d|etc|p\.ex)$", re.I)


def _tokens_durs(texte: str) -> set[str]:
    return {
        m.group(0).lower()
        for m in DUR.finditer(texte)
        if not MOTS_COURANTS.match(m.group(0))
    }


def verifier_faits(parle: str, source: str) -> list[str]:
    """Hard tokens in the spoken text that appear nowhere in the source = inventions."""
    base = source.lower()
    return sorted(t for t in _tokens_durs(parle) if t not in base)


def aplatir(markdown: str) -> str:
    """Mechanical fallback: strip what must never be read aloud. Kept from extract.py."""
    out, dans_bloc = [], False
    for brut in markdown.splitlines():
        if re.match(r"^\s*(```|~~~)", brut):
            dans_bloc = not dans_bloc
            continue
        if dans_bloc or re.match(r"^\s*\|", brut) or re.match(r"^\s*([-*_])\s*(\1\s*){2,}$", brut):
            continue
        ligne = re.sub(r"!\[[^\]\n]*\]\([^)\n]*\)", "", brut)
        ligne = re.sub(r"\[([^\]\n]*)\]\([^)\n]*\)", r"\1", ligne)
        ligne = re.sub(r"`[^`\n]*`", "", ligne)
        ligne = re.sub(r"^\s{0,3}#{1,6}\s*", "", ligne)
        ligne = re.sub(r"^\s*>\s?", "", ligne)
        ligne = re.sub(r"^\s*([-*+]|\d+[.)])\s+", "", ligne)
        ligne = re.sub(r"(\*\*|__|\*|_|~~)", "", ligne)
        ligne = re.sub(r"\s+", " ", ligne).strip()
        if ligne:
            out.append(ligne if ligne[-1] in ".!?:;" else ligne + ".")
    return " ".join(out)


class PorteParole:
    """A warm Haiku session with no tools. Warm is the point: the old version spawned a
    fresh `claude -p` per turn and paid the CLI bootstrap every single time."""

    def __init__(self, tours_memoire: int = 4):
        self.client: ClaudeSDKClient | None = None
        self.historique: list[tuple[str, str]] = []
        self.tours_memoire = tours_memoire

    async def start(self):
        self.client = ClaudeSDKClient(ClaudeAgentOptions(
            model=config.SPEAKER_MODEL,
            cli_path=config.claude_binary(),
            cwd=config.WORKDIR,
            allowed_tools=[],
            # Deltas let speech start on the first sentence instead of after the whole
            # rewrite: measured 0.9s to first token against 10.7s for the full answer.
            include_partial_messages=True,
            # Nothing to reason about — this is a register change, and thinking here is
            # pure added latency in front of the voice. It is a TypedDict, so the key has
            # to be given: a bare ThinkingConfigDisabled() is {} and the CLI builder
            # fails on a missing "type".
            thinking=ThinkingConfigDisabled(type="disabled"),
            system_prompt="Tu reformules a l'oral. Tu n'utilises aucun outil.",
        ))
        await self.client.connect()

    def _historique_rendu(self) -> str:
        if not self.historique:
            return "(rien encore, c'est le debut de l'echange)"
        return "\n\n".join(
            f"Lui : {q}\nToi : {p}" for q, p in self.historique[-self.tours_memoire:]
        )

    def _prompt(self, journal) -> tuple[str, str]:
        reponse = "\n\n".join(journal.textes) or "(aucune reponse ecrite)"
        lignes = journal.lignes()
        prompt = INSTRUCTION.format(
            historique=self._historique_rendu(),
            demande=(journal.question or "(inconnue)")[:2000],
            nb_actions=len(journal.outils),
            journal="\n".join(f"- {l}" for l in lignes) or "- (aucune action sur les fichiers)",
            reponse=reponse[:12000],
        )
        source = reponse + "\n" + "\n".join(lignes) + "\n" + (journal.question or "")
        return prompt, source

    async def dire_flux(self, journal):
        """Yield the spoken text sentence by sentence, so TTS starts almost immediately.

        Each sentence is fact-checked before it is handed to the voice. A sentence carrying
        an identifier or a precise number absent from the source is dropped rather than
        spoken: nothing invented ever reaches the speaker, and the rest of the debrief still
        flows. Aborting the whole thing on one bad sentence would be worse — that is what the
        old length-ratio guard did, and it threw away faithful rewrites."""
        prompt, source = self._prompt(journal)
        assert self.client

        await self.client.query(prompt)
        tampon, complet, rejets = "", [], 0
        async for message in self.client.receive_response():
            if not isinstance(message, StreamEvent):
                continue
            event = message.event if isinstance(message.event, dict) else {}
            if event.get("type") != "content_block_delta":
                continue
            delta = event.get("delta") or {}
            if delta.get("type") != "text_delta":
                continue
            tampon += delta.get("text") or ""
            # Flush on sentence boundaries only: handing TTS half a clause makes the
            # prosody wrong, and the fact check needs a whole statement to judge.
            while (coupe := re.search(r"[.!?…](?:\s|$)", tampon)):
                phrase, tampon = tampon[: coupe.end()].strip(), tampon[coupe.end():]
                if not phrase:
                    continue
                if verifier_faits(phrase, source):
                    rejets += 1
                    continue
                complet.append(phrase)
                yield phrase + " "
        if reste := tampon.strip():
            if not verifier_faits(reste, source):
                complet.append(reste)
                yield reste

        parle = " ".join(complet).strip()
        if parle:
            self.historique.append((journal.question, parle))
        elif rejets:
            # Everything was rejected: say the mechanically flattened answer, which is at
            # least true, rather than staying silent about a finished piece of work.
            yield aplatir("\n\n".join(journal.textes))[:1200]

    async def dire(self, journal) -> str:
        """Non-streaming convenience wrapper, used by the tests."""
        return "".join([m async for m in self.dire_flux(journal)]).strip()


async def titrer(question: str, reponse: str) -> str:
    """Un titre court pour la conversation, tire de son premier echange.

    Pourquoi ça existe : la liste nommait les conversations d'apres le DOSSIER. Quatre
    conversations ouvertes sur le meme projet s'appelaient donc toutes « insnap », et il
    fallait lire l'apercu de la derniere phrase pour deviner de quoi chacune parlait. Un titre
    qui dit le sujet fait ce travail une fois pour toutes.

    Genere une seule fois, au premier echange, et jamais rejoue : un titre qui change en cours
    de route rend la liste inutilisable — on cherche « celle sur les notifications » et elle
    s'appelle maintenant autrement.

    Client jetable plutot que celui du porte-parole : sa session garde l'historique des
    reformulations a l'oral, et y glisser une demande de titre la pollue pour tous les tours
    suivants. Le cout est un lancement de processus, une fois par conversation.
    """
    extrait = (question or "").strip()[:600]
    if not extrait:
        return ""
    client = ClaudeSDKClient(ClaudeAgentOptions(
        model=config.SPEAKER_MODEL,
        cli_path=config.claude_binary(),
        cwd=config.WORKDIR,
        allowed_tools=[],
        thinking=ThinkingConfigDisabled(type="disabled"),
        system_prompt=(
            "Tu donnes un titre court a une conversation de travail, en français. "
            "Trois a six mots, sans article inutile, sans guillemets, sans point final. "
            "Le SUJET, pas une reformulation de la demande : « refonte du parcours "
            "d'inscription », pas « l'utilisateur demande de refondre ». "
            "Reponds par le titre seul, rien d'autre."),
    ))
    try:
        await client.connect()
        await client.query(f"Premier message :\n{extrait}\n\n"
                           f"Debut de reponse :\n{(reponse or '').strip()[:400]}")
        morceaux: list[str] = []
        async for message in client.receive_response():
            for bloc in getattr(message, "content", []) or []:
                texte = getattr(bloc, "text", None)
                if texte:
                    morceaux.append(texte)
    except Exception:
        log.debug("titre impossible", exc_info=True)
        return ""
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
    titre = " ".join("".join(morceaux).split())
    # Un modele qui bavarde malgre la consigne ne doit pas remplir la liste d'un paragraphe.
    titre = titre.strip(" .\"'«»").split("\n")[0]
    return titre[:60]
