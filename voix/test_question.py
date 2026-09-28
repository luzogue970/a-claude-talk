"""Les deux chemins qu'on ne pouvait pas emprunter : poser une question, et lancer ultracode.

Sans reseau. Ce qui est verifie ici, c'est le cablage — que la question de Claude arrive bien
a quelqu'un, que la reponse repart dans le champ que le CLI lit, et qu'ultracode se traduise
en ce que le SDK comprend. Le comportement du CLI lui-meme a ete verifie une fois, a la main,
contre le vrai binaire : `updated_input["response"]` rend « The user responded: … », les trois
autres formes essayees rendent « The user did not answer the questions » ou cassent le schema.
"""
import asyncio, os, sys

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("VOIX_WORKDIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from worker import Worker

ok = True


def dire(bon: bool, texte: str):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


QUESTIONS = [{"question": "Je remplace le fichier ou j'en crée un nouveau ?",
              "header": "Fichier",
              "options": [{"label": "Remplacer"}, {"label": "Nouveau fichier"}],
              "multiSelect": False}]


async def une_question_arrive_et_la_reponse_repart():
    print("\n=== une question de Claude trouve quelqu'un a qui la poser ===")
    vues = []

    async def on_question(questions):
        vues.append(questions)
        return "un nouveau, et garde l'ancien à côté"

    w = Worker(on_question=on_question)
    r = await w._can_use_tool("AskUserQuestion", dict(QUESTIONS and {"questions": QUESTIONS}), None)

    dire(bool(vues), "la question sort du worker au lieu de mourir dedans")
    dire(vues and vues[0][0]["question"].startswith("Je remplace"),
         "elle arrive entiere, options comprises")
    dire(r.behavior == "allow", "l'outil est autorise : ce n'est pas une permission")
    entree = r.updated_input or {}
    dire(entree.get("response") == "un nouveau, et garde l'ancien à côté",
         "la reponse repart dans `response` — le champ que le CLI lit")
    dire("questions" in entree,
         "et l'entree d'origine est preservee : le schema de l'outil est valide")

    # Une reponse LIBRE, pas un intitule. C'est tout l'interet a l'oral : personne ne recite
    # « Nouveau fichier » quand il peut dire ce qu'il veut vraiment.
    dire(entree.get("response") not in ("Remplacer", "Nouveau fichier"),
         "elle n'est pas contrainte aux options proposees")


async def sans_repondeur_on_ne_casse_rien():
    print("\n=== pas de voix branchee : l'outil doit le dire lui-meme ===")
    w = Worker()
    r = await w._can_use_tool("AskUserQuestion", {"questions": QUESTIONS}, None)
    dire(r.behavior == "allow" and r.updated_input is None,
         "aucune reponse inventee — le CLI dira qu'il n'en a pas eu")

    r2 = await w._can_use_tool("AskUserQuestion", {"questions": []}, None)
    dire(r2.updated_input is None, "une demande vide ne reveille personne")


async def le_rappel_est_toujours_fourni():
    print("\n=== bypassPermissions ne doit pas rendre la conversation muette ===")
    w = Worker(on_question=lambda q: None)
    dire(w._options().can_use_tool is not None,
         "le rappel est branche meme quand les permissions sont desactivees")
    # C'est le piege repare : le rappel etait pose a None dans ce mode, au motif que les
    # permissions n'y passent pas. AskUserQuestion, elle, y passe — et n'avait donc plus
    # personne. Verifie contre le vrai binaire.


async def ultracode_se_traduit_pour_le_sdk():
    print("\n=== ultracode : un mode, pas un sixieme niveau d'effort ===")
    dire("ultracode" in config.EFFORTS, "il est proposable comme les autres niveaux")
    dire(config.WORKER_EFFORT != "ultracode", "mais JAMAIS par defaut")
    dire(config.effort_sdk("ultracode") == "xhigh",
         "ce qui part au SDK est xhigh : « ultracode » n'existe pas de son cote")
    dire(config.effort_sdk("max") == "max", "et les vrais niveaux passent tels quels")

    w = Worker()
    w.effort = "ultracode"
    o = w._options()
    dire(o.effort == "xhigh", "les options portent bien xhigh")
    dire("ultracode est actif" in o.system_prompt["append"],
         "et l'autorisation d'orchestrer, que le mode SDK n'injecte pas tout seul")
    dire("deux à quatre phrases" in o.system_prompt["append"],
         "sans lever la consigne orale : plus d'agents, pas plus de mots")

    w.effort = "xhigh"
    dire("ultracode est actif" not in w._options().system_prompt["append"],
         "un xhigh ordinaire ne recoit rien de tout ca")
    dire(config.cle_de_effort("ultracode") == "ultracode",
         "et le niveau courant reste lisible : la page n'affiche pas « très élevé »")


async def main():
    await une_question_arrive_et_la_reponse_repart()
    await sans_repondeur_on_ne_casse_rien()
    await le_rappel_est_toujours_fourni()
    await ultracode_se_traduit_pour_le_sdk()
    print("\nTOUT VERT" if ok else "\nDES ECHECS")
    sys.exit(0 if ok else 1)


asyncio.run(main())
