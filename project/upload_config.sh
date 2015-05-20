#!/bin/bash
#

if [ -z "${PPMAC_IP}" ]
then
    echo "Usage: $0"
    echo "Be sure to set PPMAC_IP, PPMAC_PW, PPMAC_TEMPDIR, PPMAC_FILES environment variables"
    exit 1
fi

: ${PPMAC_IP:=10.0.0.98}
: ${PPMAC_PW:=deltatau}
: ${PPMAC_FILES:="plcs.plc subprog1.pmc piezo_motion.pmc $1"}
: ${PPMAC_TEMPDIR:="proj_temp"}
: ${PPMAC_OTHER:=""}

echo "--------------------------------------------"
echo "Power PMAC IP: $PPMAC_IP Password: $PPMAC_PW"
echo "Project files: $PPMAC_FILES"
echo "Temporary project directory: $PPMAC_TEMPDIR"
echo "Other project directories/files: $PPMAC_OTHER"
echo "--------------------------------------------"
echo

if [ -z "${PPMAC_IP}" ]
then
    echo "Usage: $0"
    echo "Be sure to set PPMAC_IP, PPMAC_PW, PPMAC_TEMPDIR, PPMAC_FILES environment variables"
    exit 1
fi

mkdir $PPMAC_TEMPDIR
cp load_project.sh $PPMAC_TEMPDIR

if [ -n "${PPMAC_OTHER}" ]
then
    cp -RL $PPMAC_OTHER $PPMAC_TEMPDIR
fi

echo "--- Copying configuration files"
echo
echo "--- Copying project, PLCs"
echo python make_project.py ${PPMAC_TEMPDIR} ${PPMAC_FILES}
python make_project.py ${PPMAC_TEMPDIR} ${PPMAC_FILES}
sshpass -p $PPMAC_PW rsync -az ${PPMAC_TEMPDIR}/* root@${PPMAC_IP}:/var/ftp/usrflash
sshpass -p $PPMAC_PW ssh root@${PPMAC_IP} "chmod 755 /var/ftp/usrflash/load_project.sh; /var/ftp/usrflash/load_project.sh '$(date)'"

echo "--- Done"
