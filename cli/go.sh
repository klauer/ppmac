#!/bin/bash

# Change to the directory this script is in
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

cd $DIR

# Start up ipython with the ppmac_plugin extension, and use the ppmac profile
ipython --ext ppmac_plugin --profile ppmac -c "%autocall 2" -i
