# Bash completion for lh-harness (https://github.com/AMAP-ML/LongHorizon-Harness)

_lh_harness() {
    local cur prev words cword
    _init_completion || return

    local subcommands="run dashboard web doctor plugin init check-update"
    local agent_choices="claude_code codex deepseek_harness opencode"
    local plugin_agent_choices="claude_code codex"
    local plugin_names="codex-computer-use open-computer-use clawdcursor"
    local lang_choices="en zh"

    local global_opts="-h --help -V --version"

    local run_opts="--task --agent --model --reasoning-effort
        --manager-agent --manager-model --manager-reasoning-effort
        --executor-agent --executor-model --executor-reasoning-effort
        --gui-executor-agent --gui-executor-model --gui-executor-reasoning-effort
        --cli-executor-agent --cli-executor-model --cli-executor-reasoning-effort
        --auditor-agent --auditor-model --auditor-reasoning-effort
        --gui-auditor-agent --gui-auditor-model --gui-auditor-reasoning-effort
        --cli-auditor-agent --cli-auditor-model --cli-auditor-reasoning-effort
        --final-response-agent --final-response-model --final-response-reasoning-effort
        --env --runs-root --run-id --workspace --harness-dir --log-dir
        --api-key --base-url --prompt-language
        --claude-mcp-config --codex-mcp-config --mcp-add-dir --guard-exclude-path
        --max-rounds --manager-timeout --gui-executor-timeout --cli-executor-timeout
        --auditor-timeout --dashboard --no-dashboard --dashboard-port
        --dashboard-host --dashboard-no-open --dashboard-auth-token --keep-dashboard
        -h --help"

    local dashboard_opts="--runs-root --log-dir --workspace-root --host --port
        --no-open --auth-token -h --help"

    local web_opts="--runs-root --workspace-root --log-dir --host --port
        --no-open --auth-token -h --help"

    local doctor_opts="-h --help"
    local init_opts="--force -h --help"
    local check_update_opts="-h --help"

    local plugin_opts="-h --help"
    local plugin_list_opts="-h --help"
    local plugin_install_opts="--agent --no-activate -h --help"
    local plugin_uninstall_opts="-h --help"

    # Find the subcommand (first non-option word after "lh-harness")
    local subcommand= subword=
    local i
    for ((i = 1; i < cword; i++)); do
        case "${words[i]}" in
            -*) ;;
            *)
                if [[ -z $subcommand ]]; then
                    subcommand=${words[i]}
                elif [[ $subcommand == "plugin" && -z $subword ]]; then
                    subword=${words[i]}
                fi
                ;;
        esac
    done

    # Complete the value of an option that takes a fixed set of choices
    case "$prev" in
        --agent|--manager-agent|--executor-agent|--gui-executor-agent| \
        --cli-executor-agent|--auditor-agent|--gui-auditor-agent| \
        --cli-auditor-agent|--final-response-agent)
            COMPREPLY=($(compgen -W "$agent_choices" -- "$cur"))
            return
            ;;
        --env)
            COMPREPLY=($(compgen -W "local" -- "$cur"))
            return
            ;;
        --prompt-language)
            COMPREPLY=($(compgen -W "$lang_choices" -- "$cur"))
            return
            ;;
        --workspace|--workspace-root|--runs-root|--harness-dir|--log-dir| \
        --claude-mcp-config|--codex-mcp-config|--mcp-add-dir|--guard-exclude-path)
            _filedir -d
            return
            ;;
        --task)
            _filedir
            return
            ;;
    esac

    if [[ $subcommand == "plugin" && $prev == "--agent" ]]; then
        COMPREPLY=($(compgen -W "$plugin_agent_choices" -- "$cur"))
        return
    fi

    if [[ -z $subcommand ]]; then
        if [[ $cur == -* ]]; then
            COMPREPLY=($(compgen -W "$global_opts" -- "$cur"))
        else
            COMPREPLY=($(compgen -W "$subcommands" -- "$cur"))
        fi
        return
    fi

    case "$subcommand" in
        run)
            COMPREPLY=($(compgen -W "$run_opts" -- "$cur"))
            ;;
        dashboard)
            COMPREPLY=($(compgen -W "$dashboard_opts" -- "$cur"))
            ;;
        web)
            COMPREPLY=($(compgen -W "$web_opts" -- "$cur"))
            ;;
        doctor)
            COMPREPLY=($(compgen -W "$doctor_opts" -- "$cur"))
            ;;
        init)
            COMPREPLY=($(compgen -W "$init_opts" -- "$cur"))
            ;;
        check-update)
            COMPREPLY=($(compgen -W "$check_update_opts" -- "$cur"))
            ;;
        plugin)
            if [[ -z $subword ]]; then
                if [[ $cur == -* ]]; then
                    COMPREPLY=($(compgen -W "$plugin_opts" -- "$cur"))
                else
                    COMPREPLY=($(compgen -W "list install uninstall" -- "$cur"))
                fi
                return
            fi
            case "$subword" in
                list)
                    COMPREPLY=($(compgen -W "$plugin_list_opts" -- "$cur"))
                    ;;
                install)
                    if [[ $cur != -* ]]; then
                        COMPREPLY=($(compgen -W "$plugin_names $plugin_install_opts" -- "$cur"))
                    else
                        COMPREPLY=($(compgen -W "$plugin_install_opts" -- "$cur"))
                    fi
                    ;;
                uninstall)
                    if [[ $cur != -* ]]; then
                        COMPREPLY=($(compgen -W "$plugin_names $plugin_uninstall_opts" -- "$cur"))
                    else
                        COMPREPLY=($(compgen -W "$plugin_uninstall_opts" -- "$cur"))
                    fi
                    ;;
            esac
            ;;
    esac
} &&
    complete -F _lh_harness lh-harness
