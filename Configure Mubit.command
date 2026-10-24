#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
exec python3 configure_mubit.py
