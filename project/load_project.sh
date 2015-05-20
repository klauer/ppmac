#!/bin/bash

# no ntpdate on the machine unfortunately
DATE=$1
if [ -n "$DATE" ]; then
    echo "Setting date to $DATE..."
    date --set="$DATE"
fi

PRE_MAKE_CFG=/var/ftp/usrflash/pre_make.cfg
POST_MAKE_CFG=/var/ftp/usrflash/post_make.cfg
USRALGO=/var/ftp/usrflash/usralgo.ko

read -d '' PYSCRIPT <<"EOF"
# Quick script to flatten project filenames so vim will recognize them
import os
import sys
import re

for line in sys.stdin.readlines():
    line = line.rstrip()
    m = re.match('^(\/var/ftp/usrflash/.*?): (.*)$', line)
    if m:
        fn, rest = m.groups()
        print '%s: %s' % (os.path.split(fn)[1], rest)
    else:
        print line
EOF

function run_command {
    $@ | grep -v -e "UnlinkGatherThread" -e "*** EOF" | sed '/^[[:cntrl:]]$/d' > /dev/stdout
}


if [ -f $PRE_MAKE_CFG ]
then
    echo "Running pre-make configuration..."
    run_command gpascii -i${PRE_MAKE_CFG}
    echo
fi

echo "Compiling all C source..."
#find . -name Makefile -exec dirname {} \; | xargs -I '{}' make -C {} clean
find /var/ftp/usrflash -name Makefile -exec dirname {} \; | xargs -I '{}' make -C {}

if [ -f $USRALGO ]
then
    echo Removing kernel module: $USRALGO
    rmmod $USRALGO
    # 2> /dev/null
fi

find /var/ftp/usrflash -name Makefile -exec dirname {} \; | xargs -I '{}' make -C {} before_projpp

# Load the project, but strip out control characters in the response
run_command projpp
echo

grep -v -e "unknown escape sequence" -e "PMAC_PROJECT" -e "redefined" -e "location of the previous" /var/ftp/usrflash/Project/Log/pp_error.log | python -c "${PYSCRIPT}"

find /var/ftp/usrflash -name Makefile -exec dirname {} \; | xargs -I '{}' make -C {} after_projpp

if [ -f $USRALGO ]
then
    echo Inserting kernel module: $USRALGO
    insmod $USRALGO
    lsmod |grep usralgo
fi

if [ -f $POST_MAKE_CFG ]
then
    echo "Running post-make configuration..."
    run_command gpascii -i${POST_MAKE_CFG}
    echo
fi

if [ -f /var/ftp/usrflash/load_delay.cfg ]
then
    echo "Running delayed final configuration..."
    sleep 10
    run_command gpascii -i/var/ftp/usrflash/load_delay.cfg
    echo
fi
