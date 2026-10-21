#!/usr/bin/env bash
set -x
mkdir -p ~/software && cd ~/software
test -d ProteinMPNN || git clone --depth 1 https://github.com/dauparas/ProteinMPNN.git
cd ProteinMPNN
git log -1 --format=%h
du -sh vanilla_model_weights soluble_model_weights
ls vanilla_model_weights
ls helper_scripts
echo DONE_MARKER
