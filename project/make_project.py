from __future__ import print_function
import os
import sys
import shlex
import shutil

if len(sys.argv) == 1:
    print('Usage: %s temp_project_path/ project_file1 [project_file2...]' % sys.argv[0])
    print('NOTE: temp_project_path will first be removed then recreated when making a project.')
    sys.exit(1)

PROJ_TEMPLATE = '''
# This section loads all Power PMAC programs & is run through the pre-processor/CmdProcessor.
[PMAC_PROGRAMS]
%(pmac_programs)s

[LINUX_PROGRAMS]
%(linux_programs)s

[RTUSRCCODE]
%(rtusrcode)s

[PMAC_BUFFERS]
TableBufSize=1048576
UserBufSize=1048576
LookAheadBufSize=16777216
ProgramBufSize=16777216

[CUSTOM_CONFIG_FILE]
%(custom_config_file)s
'''

remote_base_path = '/var/ftp/usrflash'


def get_c_path(base_path, fn):
    no_ext = os.path.splitext(fn)[0]
    if fn.startswith('bgcplc'):
        return 'Project/C Language/CPLCs/%s' % no_ext
    elif fn.startswith('usr_'):
        return 'Project/C Language/Realtime Routines/%s' % no_ext
    else:
        raise


def get_pmc_path(base_path, fn):
    options = [('Project/PMAC Script Language/Libraries', 'open subprog'),
               ('Project/PMAC Script Language/Motion Programs', None)]

    subdir = None
    file_contents = open(fn, 'rt').read()
    for path, to_grep in options:
        if to_grep is None or to_grep in file_contents:
            subdir = path
            break

    if subdir is None:
        raise ValueError('No default path / strings not found for %s' % fn)

    return subdir

def get_cfg_path(base_path, fn):
    if fn in ('pre_make.cfg', 'post_make.cfg'):
        return ''
    elif fn.startswith('load_delay'):
        return ''
    else:
        return 'Project/Configuration'

ext_paths = {'.plc': 'Project/PMAC Script Language/PLC Programs',
             '.pmh': 'Project/PMAC Script Language/Global Includes',
             '.pmc': get_pmc_path,
             '.cfg': get_cfg_path,
             '.ini': 'Project/Configuration',
             '.c': get_c_path,
             '.h': 'Project/C Language/Include',
             }


def get_paths(base_path, fn, include_fn=False):
    ext = os.path.splitext(fn)[1]
    just_fn = os.path.split(fn)[1]

    if ext in ext_paths:
        subdir = ext_paths[ext]
    else:
        raise ValueError('Unknown file extension (%s) ignoring' % (fn))

    if hasattr(subdir, '__call__'):
        subdir = subdir(base_path, fn)

    print('%s -> %s' % (fn, subdir))
    try:
        local_dir = os.path.join(base_path, subdir)
        #print("Making path:", local_dir)
        os.makedirs(local_dir)
    except OSError:
        pass

    if include_fn:
        subdir = os.path.join(subdir, just_fn)
        local_dir = os.path.join(base_path, subdir)

    remote_dir = os.path.join(remote_base_path, subdir)

    return local_dir, remote_dir


def create_makefile(local_path, release=True, template='bgcplc_makefile'):
    if release:
        dt_debug_flags = '-O2'
        build_type = 'Release'
    else:
        dt_debug_flags = '-g3'
        build_type = 'Debug'

    source_files = [f for f in os.listdir(local_path)
                    if f.endswith('.c')]
    source_files = ' '.join(source_files)

    subdir = os.path.split(local_path)[-1]
    if subdir.startswith('bgcplc'):
        output_fn = '/var/ftp/usrflash/Project/C\ Language/CPLCs/user/libplcc%d.so' % int(subdir[-2:])
    elif subdir.startswith('usr_'):
        output_fn = '/var/ftp/usrflash/Project/C\ Language/Realtime\ Routines/%s.so' % subdir
    else:
        raise NotImplementedError('Unknown type: %s' % subdir)

    if not os.path.exists(template):
        raise ValueError('Makefile template %s does not exist' % template)

    template = open(template, 'rt').read()
    template = template % locals()

    makefile_path = os.path.join(local_path, 'Makefile')
    with open(makefile_path, 'wt') as f:
        print(template, file=f)


def fix_path(base_path, source):
    try:
        local_dir, remote_dir = get_paths(base_path, source)
    except Exception as ex:
        print('* Failed: %s (%s)' % (source, ex))
        return

    source_fn = os.path.split(source)[1]
    local_file = os.path.join(local_dir, source_fn)
    remote_file = os.path.join(remote_dir, source_fn)

    shutil.copyfile(source, local_file)
    return local_file, remote_file


def output_config(base_path, project_files, release=True):
    pmac_programs = []
    linux_programs = []
    rtusrcode = []
    makefile_paths = set([])
    for i, fn in enumerate(project_files):
        if not os.path.exists(fn):
            print('* Failed: Unable to open %s' % fn)
            continue

        local_file, remote_file = fix_path(base_path, fn)
        file_ext = os.path.splitext(fn)[1]
        if file_ext in ('.c', ):
            rtusrcode.append('file%d=%s' % (len(rtusrcode) + 1, remote_file))
            #linux_programs.append('file%d=%s' % (len(linux_programs) + 1, remote_file))
            makefile_paths.add(os.path.split(local_file)[0])
        elif file_ext in ('.h', ):
            # Don't add it to the project ini file
            pass
        else:
            pmac_programs.append('file%d=%s' % (len(pmac_programs) + 1, remote_file))

    for path in makefile_paths:
        print('Creating makefile in', path)
        create_makefile(path, release=release)

    local_cfg, remote_cfg = get_paths(base_path, 'pp_proj.ini', include_fn=True)

    pmac_programs.append('last_file_number=%d' % i)
    pmac_programs = '\n'.join(pmac_programs)
    linux_programs = '\n'.join(linux_programs)
    #rtusrcode = '\n'.join(rtusrcode)
    rtusrcode = ''
    custom_config_file = ''

    print('Configuration in', local_cfg)
    f = open(local_cfg, 'wt')
    print(PROJ_TEMPLATE % locals(), file=f)
    f.close()

base_path = sys.argv[1]
project_files = ' '.join(sys.argv[2:])
project_files = shlex.split(project_files, ' ')

if os.path.relpath(base_path) == '.':
    print('Do not use the script directory, use a subdirectory for the base path')
    sys.exit(1)

output_config(base_path, project_files)
print('Configuration done')
