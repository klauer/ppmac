#include <stdio.h>
#include <gplib.h>

int
main( int argc, char *argv[] )
{
    int err = InitLibrary();
    if(err != 0) abort();
    struct SHM *pshm = GetSharedMemPtr();
    pshm->P[0]++;
    printf("P0 = %e\n", pshm->P[0]);

    CloseLibrary();
    return  0;
}
