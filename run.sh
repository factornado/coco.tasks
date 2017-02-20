#!/bin/bash
cd "$(dirname "$0")"

MD5="$(md5sum env.yml | cut -d " " -f 1)"
NAME="$(head -1 env.yml | cut -d " " -f 2)"

ENV_NAME=$(echo ${NAME}_${MD5})
ENV_NAME=${ENV_NAME:0:16}


source activate $ENV_NAME
python main.py
