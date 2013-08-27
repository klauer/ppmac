#include <stdio.h>
#include <gplib.h>

struct SHM *pshm=NULL;

unsigned int find_isr_function( const char *functionName )
{
    FILE *fp;
    char *tail;
    char cmd[64];
    char result[128];
    unsigned long addr = 0x00;

    if( !functionName ) {
        printf("function_name not specified\n");
        return 1;
    }
    if( !pshm ) {
        printf("shm not initialized\n");
        return 1;
    }

    strcpy(cmd, "cat /proc/kallsyms | grep ");
    strcat(cmd, functionName);

    fp = popen(cmd, "r");
    if( !fp ) {
        printf("Unable to open cat\n");
        return 1;
    }

    while( fgets(result, 127, fp) ) {
    }

    pclose(fp);

    // if result == cmd, we didn't get a response
    if( strcmp(result, cmd) == 0 ) {
        printf("Address not found (no response)\n");
        return 1;
    }

    tail = strchr(result, ' ');
    addr = strtoul(&result[0], &tail, 16);
    if (addr == 0) {
        printf("Address not found\n");
    }

    return addr;
}

int disable_isr( unsigned char isr )
{
    struct timespec time = { .tv_sec = 0, .tv_nsec = 10000000 };

    if( !pshm ) {
        printf("Disable ISR failed pshm==NULL\n");
        return 1;
    }
    pshm->Motor[isr].PhaseCtrl = 0;  // stop executing user phase interrupt
    nanosleep(&time, NULL);          // wait 10ms (arbitrary) for ISR to stop executing

    return 0;
}

int enable_isr( unsigned char isr )
{
    if( !pshm ) return 1;

    pshm->Motor[isr].PhaseCtrl = 1;  // start executing phase code

    return 0;
}

int load_isr_function_from_addr( unsigned int addr, unsigned char isr )
{
    // no need to validate ISR because maximum value of unsigned char is 255

    if( disable_isr(isr) ) return 1;

    pshm->Motor[isr].UserPhase = (PUserCtrl) addr;
    pshm->UserAlgo.PhaseAddr[isr] = addr;
    printf("Loaded OK\n");
    return 0;
}

int load_isr_function( const char *functionName, unsigned char isr )
{
    if( !functionName || functionName[0] == '\0' ) return 1;
    unsigned int addr = find_isr_function(functionName);
    if( addr == 0x00 )
        return 1;

    printf("Got address to %s: %x\n", functionName, addr);
    return load_isr_function_from_addr(addr, isr);
}

int main(int argc, char *argv[])
{
    int initialized=0;
    int err;
    unsigned char motor;
    char *function_name;
    unsigned int addr;

    if (argc < 3) {
        goto printusage;
    }
    
    motor = (unsigned char)atoi(argv[2]);
    printf("Motor: %d\n", motor);

    if ((err = InitLibrary()) != 0) {
        abort();
    }
    initialized = 1;
    pshm = GetSharedMemPtr();

    if (!strcmp(argv[1], "-l")) {
        printf("Loading ISR function\n");
        if (argc < 4)
            goto printusage;

        function_name = argv[3];
        if (function_name[0] == '$' && strlen(function_name) > 1) {
            addr = (int)strtol(&function_name[1], NULL, 16);
            printf("Address: %x\n", addr);
            err = load_isr_function_from_addr(addr, motor);
            printf("Load ISR function from addr returned: %d\n", err);
        } else {
            printf("Function name: %s\n", function_name);
            err = load_isr_function(function_name, motor);
            printf("Load ISR function returned: %d\n", err);
        }

    } else if (!strcmp(argv[1], "-e")) {
        err = enable_isr(motor);
        printf("Enable ISR returned: %d\n", err);
    } else if (!strcmp(argv[1], "-d")) {
        err = disable_isr(motor);
        printf("Disable ISR returned: %d\n", err);
    } else {
        goto printusage;
    }

    CloseLibrary();
    return err;

printusage:
    printf("User phase loading tool\n");
    printf("%s [-l/-e/-d] motor [function_name]\n", argv[0]);
    printf("Examples:\n");
    printf("    Load function on motor 1: %s -l 1 function_name\n", argv[0]);
    printf("    Enable motor phase: %s -e 1\n", argv[0]);
    printf("    Disable motor phase: %s -d 1\n", argv[0]);
    if (initialized)
        CloseLibrary();

    return 1;

}
