# claude-talk

Agent conversationnel vocal pour développer, en français. Tu parles, Claude Code travaille
aussi longtemps qu'il faut, puis il te **raconte** ce qu'il a fait — pas en lisant son
markdown à voix haute, mais en te briefant comme un collègue.

## Architecture

```
     micro du PC
             │
   ┌─────────▼──────────────────────────────────────────────┐
   │  LiveKit Agents — console locale, aucun serveur        │
   │   • AEC WebRTC : soustrait la voix de l'agent du micro  │
   │   • Silero VAD + TurnDetector v1-mini (local, FR)      │
   │   • interruption adaptative, reprise sur faux positif  │
   │   • Azure STT fr-FR + phrase_list (vocabulaire dev)    │
   └─────────┬──────────────────────────────────────────────┘
             │  llm_node : route la phrase entendue
             │
   ┌─────────▼──────────┐        ┌──────────────────────────┐
   │  routage local     │        │  worker.py               │
   │  « stop »          │───────▶│  UNE session Claude Code │
   │  « t'en es où ? »  │  0 ms  │  (Agent SDK, persistante)│
   └────────────────────┘        │  journal des outils      │
                                 │  interrupt(), permissions│
                                 └──────────┬───────────────┘
                                            │ événements réels
                                 ┌──────────▼───────────────┐
                                 │  porte_parole.py         │
                                 │  Haiku chaud, sans outil │
                                 │  débrief oral en flux    │
                                 │  + vérif anti-invention  │
                                 └──────────┬───────────────┘
                                            ▼
                              Azure TTS → haut-parleurs
```

**Un seul contexte fait autorité** : la session Claude Code. Le porte-parole ne devine pas,
il lit le journal des outils réellement appelés. C'était la faille de la version
précédente : le résumé parlé ne voyait que le dernier paragraphe de texte.

## Installation

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt          # versions figées, voir l'avertissement
.venv/bin/python voix/agent.py download-files      # modèles locaux (VAD, détecteur de tour)
```

> **Les versions sont figées volontairement.** LiveKit annonce `console mode is deprecated
> and will be removed in a future release`, avec `lk agent console` (le CLI Go de LiveKit)
> comme remplaçant. Aujourd'hui `console` marche, en local, sans compte. Ne mets pas
> `livekit-agents` à jour à l'aveugle : le jour où tu le fais, c'est ce mode-là qu'il faut
> revérifier en premier.

Les secrets vivent **hors du dépôt**, dans `~/.config/claude-talk/secrets.env` (chmod 600) :

```
AZURE_SPEECH_KEY=...
AZURE_SPEECH_REGION=francecentral
AZURE_SPEECH_VOICE=fr-FR-Marc:MAI-Voice-2-Flash
```

## Lancer

```sh
# lister les périphériques
.venv/bin/python voix/agent.py console --list-devices

# en conversation, dans le projet courant
VOIX_WORKDIR="$PWD" .venv/bin/python voix/agent.py console

# en tapant au clavier, pour mettre au point sans micro
VOIX_WORKDIR="$PWD" .venv/bin/python voix/agent.py console --text
```

### Micro et haut-parleurs du PC, sans casque

Ça fonctionne : le mode console applique l'annulation d'écho WebRTC (`echo_cancellation`,
`noise_suppression`, `high_pass_filter`, `auto_gain_control`) et lui donne la lecture en
flux inverse, avec un délai recalculé à chaque tampon d'entrée. C'est ce que l'ancien
système mesurait à la main avant de simplement couper le micro pendant 28 secondes.

Deux réglages pratiques quand on n'a pas de casque :

- **Garde un volume modéré.** L'AEC soustrait la voix de l'agent, il ne fait pas de miracle
  à plein volume avec le micro à 30 cm de l'enceinte.
- **Les 3 premières secondes** de chaque prise de parole ne sont pas interruptibles : c'est
  le temps de chauffe de l'AEC, volontaire, pour que l'agent ne se coupe pas lui-même.

Si de l'écho passe malgré tout (l'agent se coupe seul, ou tu vois ta propre synthèse
transcrite comme si tu avais parlé), il reste deux leviers, dans cet ordre : monter
`min_interruption_words` à 3 dans `agent.py`, puis charger l'AEC de PipeWire en amont
(`pactl load-module module-echo-cancel`) pour empiler une seconde passe.

## Réglages

| Variable | Défaut | Rôle |
|---|---|---|
| `VOIX_WORKDIR` | `$PWD` | le projet sur lequel Claude Code travaille |
| `VOIX_WORKER_MODEL` | `claude-opus-5` | le modèle qui fait le travail |
| `VOIX_WORKER_EFFORT` | `high` | `low`…`max` |
| `VOIX_SPEAKER_MODEL` | `claude-haiku-4-5` | le porte-parole |
| `VOIX_LANGUAGE` | `fr-FR` | STT et TTS |
| `VOIX_STT` / `VOIX_TTS` | `azure` | `azure` \| `local` (local pas encore branché) |
| `VOIX_INPUT_DEVICE` / `VOIX_OUTPUT_DEVICE` | vide | sinon le périphérique système par défaut |

Le vocabulaire biaisé pour la reconnaissance est dans `config.PHRASE_LIST` ; le nom du
projet et la branche git y sont ajoutés automatiquement. **Ajoute-y ton jargon** : sans ça
Azure transformait « MQL » en « kubedka » et « un fichier point MD » en « un point MD ».

## Commandes vocales traitées localement (latence nulle)

| Tu dis | Effet |
|---|---|
| « stop », « arrête », « laisse tomber » | `interrupt()` sur la session |
| « t'en es où ? », « ça avance ? » | résumé du journal, sans appeler de modèle |
| « oui » / « non » après une demande | réponse à une demande de permission d'outil |

Les outils en lecture seule (`Read`, `Grep`, `Glob`, `WebSearch`…) passent sans demander.
Tout ce qui écrit, exécute ou sort de la machine demande l'accord **à la voix**.

## Tests

```sh
.venv/bin/python voix/test_noyau.py    # worker + porte-parole, sans audio ni LiveKit
.venv/bin/python voix/test_boucle.py   # boucle complète via AgentSession.run(), sans micro
```

## Ce qui reste à faire

- **Chemin 100 % local** (`VOIX_STT=local`, `VOIX_TTS=local`) : `faster-whisper` pour le
  français, les voix Piper `fr_FR-*` déjà présentes dans `tts/voices/` pour la synthèse.
  Nécessaire dès qu'on parle d'un code qui ne doit pas partir chez un tiers.
- **Jamais testé avec un vrai micro** : tout ce qui précède est validé en texte et en
  headless. La première session au micro reste à faire, et c'est là que se jugera la
  qualité réelle de l'AEC sur les haut-parleurs de ce portable.
- Rotation des clés Azure et ElevenLabs : elles ont vécu en clair dans `tts.7z`.
- `tts/` est l'ancien système, gardé le temps de la transition. À supprimer ensuite.
- Suivre la dépréciation du mode console de LiveKit (migration vers `lk agent console`).
