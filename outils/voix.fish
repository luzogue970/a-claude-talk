# claude-talk — agent vocal pour Claude Code.
#
# Installation :
#     ln -s (pwd)/outils/voix.fish ~/.config/fish/conf.d/voix.fish
#     exec fish
#
# VOIX_RACINE est deduit de l'emplacement de ce fichier, symlink compris : rien a regler.
# Pour pointer ailleurs : set -Ux VOIX_RACINE /chemin/vers/claude-talk
#
# vv  = parler (Opus 5)      | vt = taper, sans micro    | vh = Haiku, pour la plomberie
# vvconv vvreprendre vvlire  | vvsessions vvstop         | voix = l'aide complete
#
# « Hey Claude » (vhey) est desactive : il demandait une unite systemd et des raccourcis i3
# qui ne peuvent pas etre livres ici. Voir la section correspondante du README.

if not set -q VOIX_RACINE
    # (status filename) donne le symlink ; realpath remonte a la vraie source, donc a la
    # racine du depot, ou qu'il ait ete clone.
    set -gx VOIX_RACINE (realpath (dirname (dirname (status filename))))
end

function __voix_py --description "Le python du venv, ou une erreur lisible"
    set -l py $VOIX_RACINE/.venv/bin/python
    if not test -x $py
        echo "voix: venv introuvable dans $VOIX_RACINE" >&2
        echo "      cd $VOIX_RACINE && python3 -m venv .venv" >&2
        echo "      .venv/bin/pip install 'livekit-agents[silero,azure,turn-detector]' claude-agent-sdk python-dotenv" >&2
        return 1
    end
    echo $py
end
function __voix_lancer --description "Lance l'agent : modele, dossier de travail, puis le reste"
    set -l modele $argv[1]
    set -l dossier $argv[2]
    set -l extra $argv[3..-1]
    set -l py (__voix_py); or return 1

    # Le dossier de travail est celui que TU donnes (ou le courant), pas celui du projet
    # claude-talk : c'est la dessus que Claude Code va travailler.
    if test -z "$dossier"
        set dossier $PWD
    end
    if not test -d $dossier
        echo "voix: $dossier n'est pas un dossier" >&2
        return 1
    end
    set -l dossier (realpath $dossier)

    # Claude Code aura les outils d'ecriture sur ce dossier. Le home entier ou la racine,
    # c'est trop large pour etre utile et assez large pour faire des degats.
    if test "$dossier" = "$HOME" -o "$dossier" = /
        echo "voix: refuse de travailler sur $dossier — c'est trop large." >&2
        echo "      place-toi dans un projet (cd ...) ou donne-le : vv ~/mon/projet" >&2
        echo "      pour forcer quand meme : set -x VOIX_JE_SAIS 1" >&2
        set -q VOIX_JE_SAIS; or return 1
        echo "      (force par VOIX_JE_SAIS)" >&2
    end

    if not test -r $HOME/.config/claude-talk/secrets.env
        echo "voix: pas de secrets dans ~/.config/claude-talk/secrets.env (cle Azure requise)" >&2
        return 1
    end

    # Un agent oublie garde le port du tableau et mange la RAM : un est reste 1j21h a 1,5 Go
    # sans que rien ne le signale. On le dit avant d'en lancer un deuxieme.
    set -l vieux (__voix_agents)
    if test -n "$vieux"
        # Le parallele est desormais une fonctionnalite : trois sujets ouverts en meme temps
        # est un usage normal. Le micro va a la derniere lancee, les autres continuent leur
        # travail sans ecouter. On informe, on n'alarme plus.
        echo "voix: $(count $vieux) conversation(s) deja ouverte(s) :" >&2
        for p in $vieux
            ps -o pid=,etime=,rss= -p $p 2>/dev/null | awk '{printf "        pid=%s age=%s rss=%.0fMo\n",$1,$2,$3/1024}' >&2
        end
        # Le PID dit qu'un agent tourne ; le journal dit SUR QUOI. « pid 4123, 1,5 Go » ne
        # permet pas de decider s'il faut le tuer, « pid 4123, projet insnap » si.
        __voix_avertir_actives $dossier
        echo "        le tableau prendra un autre port ; le micro passe a celle-ci." >&2
        echo "        « vvsessions » les liste, « vvstop » les ferme toutes." >&2
    end

    echo "  projet  $dossier"
    echo "  modele  $modele"
    VOIX_WORKDIR=$dossier VOIX_WORKER_MODEL=$modele $py $VOIX_RACINE/voix/agent.py $extra
end
function vv --description "Voix : micro + haut-parleurs, Opus 5 effort xhigh, logs visibles"
    __voix_lancer claude-opus-5 "$argv[1]" console --log-level info
end
function vt --description "Voix au clavier : meme boucle, sans micro (etape 0)"
    __voix_lancer claude-opus-5 "$argv[1]" console --text
end
function vh --description "Voix en Haiku effort low : verifier la plomberie sans depenser"
    VOIX_WORKER_EFFORT=low __voix_lancer claude-haiku-4-5 "$argv[1]" console --log-level info
end
function vhey --description "Assistant « Hey Claude » en arriere-plan (question / reponse)"
    set -l py (__voix_py); or return 1
    if not test -r $HOME/.config/claude-talk/secrets.env
        echo "voix: pas de secrets dans ~/.config/claude-talk/secrets.env (cle Azure requise)" >&2
        return 1
    end
    $py $VOIX_RACINE/voix/assistant.py $argv
end
function vheydiag --description "Regler le seuil « les haut-parleurs jouent » sur cette machine"
    set -l py (__voix_py); or return 1
    $py $VOIX_RACINE/voix/assistant.py --diag
end
function vheytest --description "Rejouer un WAV 16 kHz mono dans toute la chaine Hey Claude"
    set -l py (__voix_py); or return 1
    if test -z "$argv[1]"
        echo "usage: vheytest fichier.wav" >&2; return 1
    end
    $py $VOIX_RACINE/voix/assistant.py --fichier $argv[1]
end
function __voix_agents --description "PIDs des vrais agents vv (python seulement)"
    # pgrep -f matche la ligne de commande, donc n'importe quel shell qui MENTIONNE le
    # chemin se matche lui-meme. Le filtre sur comm=python est la garantie : un shell ne
    # sera jamais tue par erreur, quel que soit son historique de commandes.
    for p in (pgrep -f '\.venv/bin/python .*voix/[a]gent\.py' 2>/dev/null)
        set -l c (ps -o comm= -p $p 2>/dev/null | string trim)
        if string match -qr '^python' -- "$c"
            echo $p
        end
    end
end
function vvsessions --description "Les conversations ouvertes, et laquelle ecoute"
    set -l py (__voix_py); or return 1
    $py -c "
import sys; sys.path.insert(0, '$VOIX_RACINE/voix')
import pupitre
s = pupitre.sessions()
if not s:
    print('  aucune conversation ouverte')
else:
    print(f'  {len(s)} conversation(s) — une seule ecoute a la fois :')
    print()
    for d in s:
        marque = 'ECOUTE ' if d.get('micro') else '       '
        parle = ' (lit une reponse)' if d.get('parle') else ''
        print(f\"  {marque} {d.get('projet') or '?':<20} pid {d['pid']:<8} \"
              f\"http://127.0.0.1:{d.get('port')}{parle}\")
        print(f\"           {d.get('chemin') or ''}\")
    print()
    print('  pour changer : « ecouter ici » sur le tableau de la conversation voulue')
"
end
function vvconv --description "Conversations lancees depuis ici (--tout pour toutes)"
    set -l py (__voix_py); or return 1
    # Le dossier courant decide de ce qu'on voit : depuis la racine, tout ; depuis un projet,
    # ce projet et ses sous-dossiers. Le formatage vit dans journal.apercu(), pas ici.
    set -l tout False
    if contains -- --tout $argv; or contains -- -a $argv
        set tout True
    end
    $py -c "
import sys; sys.path.insert(0, '$VOIX_RACINE/voix')
import journal
print(chr(10).join(journal.apercu(25, ici='$PWD', tout=$tout)))
"
end
function __voix_avertir_actives --description "Prevenir si une session tourne deja"
    set -l py (__voix_py); or return 0
    set -l sortie ($py -c "
import sys; sys.path.insert(0, '$VOIX_RACINE/voix')
import journal
a = journal.actives('$argv[1]')
print(len(a))
for d in a:
    print('      pid ' + str(d.get('pid')) + '  ' + str(d.get('projet') or '?') + '  ' + str(d.get('chemin') or ''))
")
    if test -z "$sortie[1]"; or test "$sortie[1]" -le 0
        return 0
    end
    echo "voix: $sortie[1] session(s) deja active(s) — micro et quota sont partages." >&2
    for l in $sortie[2..-1]
        echo $l >&2
    end
    echo "      « vvstop » les ferme, « vvconv » les liste." >&2
end
function vvreprendre --description "Relancer vv en reprenant une conversation"
    set -l py (__voix_py); or return 1
    if test -z "$argv[1]"
        echo "usage: vvreprendre <rang|session-id> [dossier]   (voir vvconv)" >&2
        return 1
    end
    # Claude Code range ses sessions PAR REPERTOIRE : reprendre depuis le mauvais dossier ne
    # donne pas d'erreur, ca donne une session introuvable — une conversation qui repart de
    # zero en silence. On resout donc le dossier d'origine depuis le fichier de session, et on
    # n'utilise le dossier courant que s'il est donne explicitement.
    set -l infos ($py -c "
import sys; sys.path.insert(0, '$VOIX_RACINE/voix')
import journal
d = journal.resoudre('$argv[1]')
sid = (d or {}).get('session_id') or ''
print(sid)
if sid:
    dossier = journal.dossier_de_session(sid) or ''
    print(dossier)
    print(journal.compte_messages(sid, dossier or None))
")
    set -l sid $infos[1]
    if test -z "$sid"
        echo "voix: conversation introuvable pour « $argv[1] » (voir vvconv)" >&2
        return 1
    end
    set -l dossier $argv[2]
    if test -z "$dossier"
        set dossier $infos[2]
    end
    if test -z "$dossier"
        echo "voix: dossier d'origine de la session $sid introuvable." >&2
        echo "      donne-le : vvreprendre $argv[1] /chemin/du/projet" >&2
        return 1
    end
    set -l n $infos[3]
    if test "$n" -le 0
        echo "voix: ATTENTION la session $sid n'a aucun message sur disque." >&2
        echo "      la reprise repartirait de zero. Verifie avec vvconv." >&2
    else
        echo "  reprise de $sid — $n message(s) rechargés"
    end
    VOIX_REPRENDRE=$sid __voix_lancer claude-opus-5 "$dossier" console --log-level info
end
function vvlire --description "Relire une conversation enregistree"
    set -l py (__voix_py); or return 1
    set -l fic ($py -c "
import sys; sys.path.insert(0, '$VOIX_RACINE/voix')
import journal, pathlib
d = journal.resoudre('$argv[1]') if '$argv[1]' else None
print(str(journal.RACINE / d['fichier']) if d else '')
")
    if test -z "$fic"; or not test -f "$fic"
        echo "voix: conversation introuvable (voir vvconv)" >&2
        return 1
    end
    if command -q bat
        bat --style=plain --language=markdown "$fic"
    else
        cat "$fic"
    end
end
function vvstop --description "Arreter tous les agents vv restes lances"
    set -l pids (__voix_agents)
    if test -z "$pids"
        echo "aucun agent vv en cours"
        return 0
    end
    for p in $pids
        ps -o pid=,etime=,rss= -p $p 2>/dev/null | awk '{printf "  arret pid=%s (age %s, %.0fMo)\n",$1,$2,$3/1024}'
    end
    kill -TERM $pids 2>/dev/null
    sleep 3
    set -l restants (__voix_agents)
    test -n "$restants"; and kill -9 $restants 2>/dev/null
    echo "  termine"
end
function vdev --description "Liste les peripheriques audio vus par LiveKit"
    set -l py (__voix_py); or return 1
    $py $VOIX_RACINE/voix/agent.py console --list-devices
end
function vtest --description "Tests sans audio : noyau puis boucle complete"
    set -l py (__voix_py); or return 1
    echo "=== noyau : worker + porte-parole ==="
    $py $VOIX_RACINE/voix/test_noyau.py; or return 1
    echo
    echo "=== boucle : via AgentSession.run() ==="
    $py $VOIX_RACINE/voix/test_boucle.py
end
function vaec --description "2e couche d'annulation d'echo (PipeWire), si de l'echo passe"
    if pactl list short sources | string match -q '*echo-cancel-source*'
        echo "voix: l'AEC PipeWire est deja chargee"
        return 0
    end
    # On memorise la source par defaut pour pouvoir revenir en arriere.
    set -Ux VOIX_SOURCE_AVANT (pactl get-default-source)
    pactl load-module module-echo-cancel \
        source_name=echo-cancel-source sink_name=echo-cancel-sink \
        aec_method=webrtc use_master_format=1; or return 1
    pactl set-default-source echo-cancel-source
    pactl set-default-sink echo-cancel-sink
    echo "voix: AEC PipeWire chargee et mise par defaut. Relance vv."
    echo "      retour en arriere : vaec-off"
end
function vaec-off --description "Retire l'AEC PipeWire et restaure l'audio d'avant"
    if set -q VOIX_SOURCE_AVANT
        pactl set-default-source $VOIX_SOURCE_AVANT 2>/dev/null
        set -e VOIX_SOURCE_AVANT
    end
    for id in (pactl list short modules | string match -r '^\d+\s+module-echo-cancel' | string split -f1 \t)
        pactl unload-module $id
    end
    echo "voix: AEC PipeWire retiree"
end
function voix --description "Rappel des commandes vocales"
    echo "  vv  [dossier]   au micro, Opus 5 xhigh        — la commande de tous les jours"
    echo "  vt  [dossier]   au clavier, sans micro        — etape 0, verifie la chaine"
    echo "  vh  [dossier]   Haiku effort low              — plomberie, sans depenser"
    echo
    echo "  vhey            « Hey Claude » lance a la main (le service tourne deja)"
    echo "                  « Hey Claude » ouvre une CONVERSATION ; « fin », « merci »,"
    echo "                  « c'est bon » la ferment, ou un clic sur la notification,"
    echo "                  ou 90 s de silence."
    echo "  vheystatus      service, plafond du jour, etat de l'ecoute"
    echo "  vheytoggle      couper / rouvrir l'ecoute Claude"
    echo "                  clavier : \$mod+Shift+s (s = silence), ou Pause"
    echo "  vheyoff/vheyon  forcer dans un sens"
    echo "  vheycout        cumul des depenses Azure (estimation locale)"
    echo "  vheylog         suivre le journal en direct"
    echo "  vheydiag        regler le seuil du verrou haut-parleurs"
    echo "  vheytest f.wav  rejouer un fichier dans la chaine Hey Claude"
    echo
    echo "  vvconv          historique des conversations"
    echo "  vvreprendre N   relancer vv en reprenant la conversation N"
    echo "  vvlire N        relire la conversation N"
    echo "  vvstop          arreter les agents vv restes lances"
    echo "  vdev            peripheriques audio"
    echo "  vtest           les deux tests sans audio"
    echo "  vaec / vaec-off 2e couche anti-echo PipeWire, si besoin"
    echo
    echo "  tableau de bord : http://127.0.0.1:7788 (s'ouvre tout seul)"
    echo "                    touche m : couper le micro | touche s : arreter le travail"
    echo "  permissions     : bypassPermissions par defaut — rien n'est demande, jamais."
    echo "                    le tableau est le seul point de controle."
    echo "                    remettre une frontiere : set -x VOIX_PERMISSION acceptEdits"
    echo
    echo "  sans argument, le dossier de travail est le dossier courant."
    echo "  ordres vocaux locaux (toute formulation) :"
    echo "     coupe le micro | chut | stop | t'en es ou | repete | quota"
    echo "  Ctrl+C pour sortir."
end
