#!/usr/bin/env bash
# -*- coding: utf-8 -*-

openscad -o thumbnail.png \
    --imgsize=300,300 \
    --autocenter \
    --viewall \
    -D "import(\"${HOME}/Sync/Models/Unsorted/Compact Case 2 Philips OneBlade v22/Compact Case 2 Philips OneBlade v22.stl\");" \
    /dev/null
