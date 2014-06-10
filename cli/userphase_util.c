#include <stdio.h>
#include <gplib.h>

extern struct SHM *pshm;

bool find_isr_function(const char *functionName, unsigned long *addr)
{
    FILE *fp;
    char *tail;
    char cmd[64];
    char result[128];
    
    
    if (!functionName) {
        printf("function_name not specified\n");
        return false;
    }
    if (!pshm) {
        printf("shm not initialized\n");
        return false;
    }

    strcpy(cmd, "cat /proc/kallsyms | grep -w ");
    strcat(cmd, functionName);

    fp = popen(cmd, "r");
    if (!fp) {
        printf("Unable to open cat\n");
        return false;
    }

    while(fgets(result, 127, fp)) {
    }

    pclose(fp);

    // if result == cmd, we didn't get a response
    if (strcmp(result, cmd) == 0) {
        printf("Address not found (no response)\n");
        return false;
    }

    tail = strchr(result, ' ');
    *addr = strtoul(&result[0], &tail, 16);
    if ((*addr) == 0) {
        printf("Address not found\n");
    }
    
    return ((*addr) != 0);
}

bool disable_isr(unsigned char isr)
{
    struct timespec time;
    
    time.tv_sec = 0;
    time.tv_nsec = 10000000;

    if (!pshm) {
        printf("Disable ISR failed pshm==NULL\n");
        return false;
    }

    pshm->Motor[isr].PhaseCtrl = 0;  // stop executing user phase interrupt
    nanosleep(&time, NULL);          // wait 10ms (arbitrary) for ISR to stop executing
    return true;
}

bool enable_isr(unsigned char isr)
{
    if (!pshm)
        return false;

    pshm->Motor[isr].PhaseCtrl = 1;  // start executing phase code

    return true;
}

bool load_isr_function_from_addr(unsigned long addr, unsigned char isr)
{
    // no need to validate ISR because maximum value of unsigned char is 255

    if (!disable_isr(isr))
        return false;

    pshm->Motor[isr].UserPhase = (PUserCtrl) addr;
    pshm->UserAlgo.PhaseAddr[isr] = addr;
    printf("Loaded OK\n");
    return true;
}

int load_isr_function(const char *functionName, unsigned char isr)
{
    unsigned long addr;

    if (!functionName || functionName[0] == '\0')
        return false;

    if (!find_isr_function(functionName, &addr))
        return false;

    printf("Got address to %s: %lx\n", functionName, addr);
    return load_isr_function_from_addr(addr, isr);
}

int main(int argc, char *argv[])
{
    int initialized=0;
    int err;
    unsigned char motor;
    char *function_name;
    unsigned long addr;

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
            printf("Address: %lx\n", addr);
            err = load_isr_function_from_addr(addr, motor);
        } else {
            printf("Function name: %s\n", function_name);
            err = load_isr_function(function_name, motor);
        }
        if (!err)
            printf("Load ISR function from addr failed\n");

    } else if (!strcmp(argv[1], "-e")) {
        if (!(err = enable_isr(motor))) {
            printf("Enable ISR returned: %d\n", err);
        }
    } else if (!strcmp(argv[1], "-d")) {
        if (!(err = disable_isr(motor))) {
            printf("Disable ISR failed\n");
        }
    } else {
        goto printusage;
    }

    CloseLibrary();
    return !err;

printusage:
    printf("User phase loading tool\n");
    printf("%s [-l/-e/-d] motor [function_name]\n", argv[0]);
    printf("Examples:\n");
    printf("    Load function on motor 1: %s -l 1 function_name\n", argv[0]);
    printf("    Enable motor phase: %s -e 1\n", argv[0]);
    printf("    Disable motor phase: %s -d 1\n", argv[0]);
    if (initialized)
        CloseLibrary();

    return 0;

}
