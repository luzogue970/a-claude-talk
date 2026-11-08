"""Rouvrir une conversation ne doit pas dépendre du poids de son transcript.

Ce qui serait cassé pour moi si ces tests tombaient : j'ouvre une conversation depuis le
tableau, elle met deux minutes à afficher son historique — ou n'y arrive jamais, parce que
le lanceur a cru l'agent mort et l'a tué entre-temps.

Mesure du 8 novembre 2026, projet insnap : le transcript de la conversation en cours pesait
**334 Mo**. `get_session_messages` le chargeait ENTIER pour en tirer les lignes affichées :
**164 secondes** et 1,9 Go de RSS sur une machine qui a 4,7 Go. Tout ce temps, la page de
l'agent ne répondait pas.

Or `rejouer_session` ne renvoie que `evenements[-limite:]` : tout ce qui précède était lu,
parsé et jeté. On lit donc la FIN du fichier, et rien d'autre.

    ../.venv/bin/python voix/test_rejeu_gros.py
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import journal  # noqa: E402

ok = True


def dire(bon: bool, texte: str):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


def ligne(role: str, contenu, **extra) -> str:
    d = {"type": role, "message": {"role": role, "content": contenu}}
    d.update(extra)
    return json.dumps(d, ensure_ascii=False) + "\n"


def poser(dossier: Path, sid: str, lignes) -> Path:
    projet = dossier / "-un-projet"
    projet.mkdir(parents=True, exist_ok=True)
    fichier = projet / f"{sid}.jsonl"
    fichier.write_text("".join(lignes), encoding="utf-8")
    return fichier


def tu_lire_par_la_fin():
    print("\nTU — ce qu'on lit du fichier")
    with tempfile.TemporaryDirectory() as tmp:
        ancien, journal.SESSIONS = journal.SESSIONS, Path(tmp)
        try:
            poser(Path(tmp), "petit", [ligne("user", f"message {i}") for i in range(5)])
            m = journal.messages_de_la_fin("petit")
            dire(m is not None and len(m) == 5,
                 "une petite conversation est lue en entier, comme avant")

            # Le gros : 2 000 lignes dont chacune pèse ~1 Ko, lues avec un budget de 20 Ko.
            # Le budget tombe donc en plein milieu d'une ligne — exactement le cas qui
            # ferait planter un lecteur naïf.
            gros = [ligne("user", f"{i:04d} " + "x" * 1000) for i in range(2000)]
            poser(Path(tmp), "gros", gros)
            m = journal.messages_de_la_fin("gros", octets=20_000)
            dire(m is not None and 0 < len(m) < 2000,
                 "une grosse conversation n'est lue que par sa fin")
            dire(bool(m) and m[-1].message["content"].startswith("1999"),
                 "et c'est bien la fin : le dernier message est le dernier écrit")
            dire(all(mm.message["content"][:4].isdigit() for mm in m),
                 "la ligne coupée en deux par le budget est jetée, pas rendue tronquée")

            dire(journal.messages_de_la_fin("jamais-vue") is None,
                 "une session sans fichier répond « je ne sais pas », pas « rien à rejouer »")

            poser(Path(tmp), "mixte", [
                ligne("user", "ce que j'ai dit"),
                ligne("user", "la consigne d'un sous-agent", isSidechain=True),
                ligne("assistant", [{"type": "text", "text": "ce qu'il a répondu"}]),
            ])
            m = journal.messages_de_la_fin("mixte")
            dire(len(m) == 2 and all("sous-agent" not in str(x.message) for x in m),
                 "le travail intérieur d'un sous-agent n'est pas remis dans ma bouche")

            poser(Path(tmp), "abime", [
                ligne("user", "avant"),
                '{"message": {"role": "user", "content": "coupé au mi\n',
                ligne("user", "après"),
            ])
            m = journal.messages_de_la_fin("abime")
            dire(len(m) == 2, "une ligne illisible est sautée, le reste de l'historique arrive")
        finally:
            journal.SESSIONS = ancien


def ti_le_rejeu_se_passe_du_sdk():
    print("\nTI — le rejeu et le fichier, branchés ensemble")
    with tempfile.TemporaryDirectory() as tmp:
        ancien, journal.SESSIONS = journal.SESSIONS, Path(tmp)
        # Un SDK piégé : s'il est appelé, il fait tomber le test au lieu de le laisser
        # passer pour de mauvaises raisons. C'est tout l'objet du changement.
        import claude_agent_sdk
        def interdit(*_a, **_k):
            raise AssertionError("le SDK a été appelé alors que le fichier était là")
        vieux = getattr(claude_agent_sdk, "get_session_messages", None)
        claude_agent_sdk.get_session_messages = interdit
        try:
            poser(Path(tmp), "vraie", [
                ligne("user", "refais la page d'accueil"),
                ligne("assistant", [{"type": "text", "text": "c'est fait"},
                                    {"type": "tool_use", "id": "t1", "name": "Bash",
                                     "input": {"description": "lancer les tests"}}]),
                ligne("user", [{"type": "tool_result", "tool_use_id": "t1",
                                "content": "42 vertes"}]),
            ])
            evenements, ecartes = journal.rejouer_session("vraie")
            genres = [e["genre"] for e in evenements]
            dire("toi" in genres and "texte" in genres and "outil" in genres
                 and "resultat" in genres,
                 "ce que j'ai dit, ce qu'il a écrit, ses outils et leurs résultats reviennent")
            dire(any(e.get("texte") == "refais la page d'accueil" for e in evenements),
                 "ma phrase est rendue telle que je l'ai tapée")
            dire(ecartes == 0, "rien n'est annoncé comme écarté quand rien ne l'est")

            # La limite s'applique toujours, et elle le DIT : une conversation tronquée
            # en silence se lit comme une conversation perdue.
            evenements, ecartes = journal.rejouer_session("vraie", limite=2)
            dire(len(evenements) == 2 and ecartes > 0,
                 "au-delà de la limite, on garde les plus récents et on annonce le reste")
        finally:
            if vieux is not None:
                claude_agent_sdk.get_session_messages = vieux
            journal.SESSIONS = ancien


def tf_une_grosse_conversation_souvre_vite():
    print("\nTF — j'ouvre une conversation de 20 Mo")
    with tempfile.TemporaryDirectory() as tmp:
        ancien, journal.SESSIONS = journal.SESSIONS, Path(tmp)
        try:
            # 20 Mo, soit un soixantième du cas réel. Au rythme mesuré sur l'ancien code
            # — 164 s pour 334 Mo — il aurait fallu une dizaine de secondes rien que pour
            # celui-ci. Le seuil est posé à 5 s : largement au-dessus du coût réel de la
            # lecture par la fin, largement en dessous de celui d'une lecture complète.
            lignes = [ligne("user", f"{i:06d} " + "y" * 2000) for i in range(10_000)]
            lignes.append(ligne("assistant", [{"type": "text", "text": "le dernier mot"}]))
            fichier = poser(Path(tmp), "enorme", lignes)
            poids = fichier.stat().st_size
            depart = time.monotonic()
            evenements, _ = journal.rejouer_session("enorme")
            duree = time.monotonic() - depart
            dire(poids > 20_000_000, f"le transcript pèse bien {poids / 1e6:.0f} Mo")
            dire(duree < 5, f"l'historique est prêt en {duree:.2f} s, pas en minutes")
            dire(bool(evenements) and evenements[-1].get("texte") == "le dernier mot",
                 "et ce que je vois en bas de page est la dernière chose dite")
            dire(len(evenements) < 10_000,
                 "on n'a pas chargé les dix mille lignes pour n'en afficher que la fin")
        finally:
            journal.SESSIONS = ancien


def main():
    tu_lire_par_la_fin()
    ti_le_rejeu_se_passe_du_sdk()
    tf_une_grosse_conversation_souvre_vite()
    print("\nTOUT VERT" if ok else "\nDES TESTS SONT ROUGES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
