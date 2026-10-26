"""« Est-ce que je dois ecouter cette reponse en entier ? »

Le test part de l'usage, pas de la fonction : chaque cas est une reponse telle que Claude
en ecrit vraiment, et ce qu'on verifie est la decision qu'on aurait voulu prendre en
l'entendant. Un nom comme `test_regex_marqueurs` ne dirait rien le jour ou il tombe.

Rien ici n'appelle le reseau ni un modele.

    .venv/bin/python voix/test_attente.py
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from question_en_plan import attend_une_reponse, question_posee

ok = True


def dire(bon: bool, texte: str):
    global ok
    ok = ok and bon
    print(f"  {'OK  ' if bon else 'ECHEC'} {texte}")


def un_compte_rendu_n_attend_rien():
    print("\nune reponse qui dit juste « c'est fait » ne me demande rien")
    for t in [
        "**Fait.** `v0.24.0` poussée. **2076 vertes, 0 rouge.**",
        "C'est corrigé : le micro se rouvre au retour sur la page.\n\n"
        "Reste à presser « Mettre à jour » dans Monitoring.",
        "Trois suites passent, une tombe — je regarde la quatrième.",
        "",
        "   \n\n  ",
    ]:
        dire(question_posee(t) is None,
             f"rien a repondre a : {t.splitlines()[0][:48] if t.strip() else '(vide)'!r}")


def une_question_en_clair_se_voit():
    print("\nune reponse qui se termine par une question doit se signaler")
    cas = [
        "J'ai enlevé la limite. Tu veux que je monte la version maintenant ?",
        "Deux approches possibles.\n\nLaquelle tu préfères ?",
        "C'est prêt côté serveur.\n\nIl me faut ton avis sur trois points :\n"
        "- le nom du genre\n- la couleur\n- faut-il le dire à voix haute",
        "Le push est refusé par la protection de branche. Dis-moi comment tu veux "
        "qu'on s'y prenne.",
        "Je peux le faire des deux façons, je te laisse choisir.",
    ]
    for t in cas:
        q = question_posee(t)
        dire(bool(q), f"question reperee dans : {t.splitlines()[-1][:52]!r} -> {q!r}")


def une_question_rhetorique_ne_compte_pas():
    print("\nune question a laquelle Claude repond lui-meme n'attend rien de moi")
    dire(question_posee(
        "J'ai séparé les deux suites.\n\nPourquoi deux ? Parce qu'elles ne tombent pas "
        "pour les mêmes raisons, et qu'une seule les aurait confondues.") is None,
        "« Pourquoi deux ? Parce que… » n'est pas une demande")
    dire(question_posee(
        "Est-ce que ça tenait au cache ? Non : le cache était vide, et le défaut "
        "revenait quand même. C'était la session audio.") is None,
        "« Est-ce que… ? Non : … » non plus")


def un_point_d_interrogation_dans_du_code_n_est_pas_une_question():
    print("\nun « ? » qui vient du code ne doit pas allumer la pastille")
    dire(question_posee(
        "La regex est posée.\n\n```\nconst R = /^(a|b)?$/;\n```") is None,
        "un bloc de code qui contient un « ? »")
    dire(question_posee(
        "Corrigé : le motif est maintenant `\\d+(\\.\\d+)?` dans le parseur.") is None,
        "du code en ligne qui contient un « ? »")
    dire(question_posee(
        "La doc est là : https://exemple.fr/p?q=1&r=2") is None,
        "une URL avec des parametres")


def une_question_au_milieu_ne_suffit_pas():
    print("\nce qui engage la suite est ce sur quoi la reponse SE TERMINE")
    dire(question_posee(
        "Tu demandais si la limite venait du navigateur ? Elle venait du champ.\n\n"
        "J'ai enlevé l'attribut, enlevé le plafond serveur, et agrandi la trame. "
        "La suite complète passe, 2076 vertes, et la version est poussée. "
        "Le téléphone recevra le nouveau code au prochain « Mettre à jour ».") is None,
        "une question posee au debut, reglee ensuite, n'attend plus rien")


def une_citation_n_engage_personne():
    print("\nune question citee est du rapporte, pas une demande")
    dire(question_posee(
        "Le message d'erreur du SDK dit :\n\n> Did you forget to await the handle?") is None,
        "une question dans un bloc de citation")


def l_ecrit_et_le_parle_sont_regardes_tous_les_deux():
    print("\nle bilan parle est une reecriture : il peut perdre ou ajouter la question")
    dire(bool(attend_une_reponse(
        "C'est fait. Tu préfères que je monte la version ?", "C'est fait.")),
        "la question est dans l'ecrit, pas dans le parle : elle compte quand meme")
    dire(bool(attend_une_reponse(
        "C'est fait.", "C'est fait. Et tu veux que je pousse tout de suite ?")),
        "la question est dans le parle seulement : elle compte aussi")
    dire(attend_une_reponse("C'est fait.", "C'est fait, rien d'autre.") is None,
         "aucun des deux ne demande rien")
    dire(attend_une_reponse(None, "") is None,
         "un tour sans texte ne fait pas tomber la detection")


def la_question_rendue_est_lisible():
    print("\nce qu'on rend est la phrase elle-meme : c'est elle qu'on relit sans rouvrir")
    q = question_posee("J'ai poussé la branche.\n\nTu veux que je fasse la PR maintenant ?")
    dire(q == "Tu veux que je fasse la PR maintenant ?", f"la phrase exacte, et elle seule ({q!r})")


def la_conversation_se_souvient_qu_elle_attend():
    """TI : le detecteur, le journal et la liste des conversations, branches ensemble.

    C'est le contrat que chaque TU rate : `question_posee` peut etre parfaite et la liste
    ne rien afficher, parce que le drapeau ne traverse pas l'index.
    """
    print("\nune conversation laissee sur une question se reconnait dans la LISTE")
    with tempfile.TemporaryDirectory() as dossier:
        os.environ["VOIX_JOURNAL"] = dossier
        for mod in ("config", "journal"):
            sys.modules.pop(mod, None)
        import journal

        ici = os.getcwd()
        a = journal.Conversation(projet="projet-a", modele="m", effort="high", chemin=ici)
        a.note_session("sid-a")
        a.tour_utilisateur("fais le truc")
        a.tour_claude("C'est fait.", attend=attend_une_reponse("C'est fait."))

        b = journal.Conversation(projet="projet-b", modele="m", effort="high", chemin=ici)
        b.note_session("sid-b")
        b.tour_utilisateur("et ça ?")
        demande = attend_une_reponse("J'ai deux options. Laquelle tu préfères ?")
        b.tour_claude("J'ai deux options. Laquelle tu préfères ?", attend=demande)

        vues = {c["session_id"]: c for c in journal.conversations(ici, sous_arbre=False)}
        dire(not (vues.get("sid-a") or {}).get("attend"),
             "celle qui a dit « c'est fait » n'attend rien")
        dire(bool((vues.get("sid-b") or {}).get("attend")),
             "celle qui a pose une question est signalee comme attendant ma reponse")
        dire((vues.get("sid-b") or {}).get("attend") == demande,
             "et elle porte la question elle-meme, pas un simple drapeau")

        # Repondre solde l'attente TOUT DE SUITE : le tour qui suit peut durer vingt
        # minutes, et la liste ne doit pas reclamer pendant ce temps une reponse deja donnee.
        b.tour_utilisateur("la premiere", repond=True)
        vues = {c["session_id"]: c for c in journal.conversations(ici, sous_arbre=False)}
        dire(not (vues.get("sid-b") or {}).get("attend"),
             "des que j'ai repondu, elle ne reclame plus rien — avant meme la reponse de Claude")

        # Et une conversation reprise ne doit pas trainer la question de sa vie precedente.
        reprise = journal.Conversation(projet="projet-b", modele="m", effort="high",
                                       chemin=ici, reprise="sid-b")
        reprise.note_session("sid-b")
        reprise.tour_claude("Voilà, c'est en place.", attend=None)
        vues = {c["session_id"]: c for c in journal.conversations(ici, sous_arbre=False)}
        dire(not (vues.get("sid-b") or {}).get("attend"),
             "une conversation poursuivie n'attend plus la question de sa premiere vie")

    os.environ.pop("VOIX_JOURNAL", None)
    for mod in ("config", "journal"):
        sys.modules.pop(mod, None)


def principal():
    un_compte_rendu_n_attend_rien()
    une_question_en_clair_se_voit()
    une_question_rhetorique_ne_compte_pas()
    un_point_d_interrogation_dans_du_code_n_est_pas_une_question()
    une_question_au_milieu_ne_suffit_pas()
    une_citation_n_engage_personne()
    l_ecrit_et_le_parle_sont_regardes_tous_les_deux()
    la_question_rendue_est_lisible()
    la_conversation_se_souvient_qu_elle_attend()
    print(f"\n{'TOUT VERT' if ok else 'DES ECHECS'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(principal())
