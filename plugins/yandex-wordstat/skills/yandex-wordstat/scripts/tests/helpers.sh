#!/bin/sh
# Shared helpers for the offline tests. Sourced by the tests; run.sh only runs
# test_*.sh, so this file never runs on its own.

# windows_shell — true under Git Bash, MSYS2 and Cygwin, whose uname reports
# <ENV>_NT-<version>. There NTFS ignores umask, and LC_ALL changes how argv
# and the environment reach native Windows programs.
windows_shell() {
    case "$(uname -s)" in
        *_NT-*) return 0 ;;
    esac
    return 1
}
