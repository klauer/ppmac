#include <stdio.h>
#include <malloc.h>
#include "dac_read.h"

int main(int argc, char *argv[]) {
  if (argc != 2) {
    printf("Usage: %s filename.dac\n", argv[0]);
    return 1;
  }
  
  DACData df;
  df.table = NULL;

  if (read_dac_file(argv[1], &df)) {
    printf("Table size: %d\n", df.table_size);
    printf("Scale factor: %d\n", df.scale_factor);
    for (int i=0; i < 10; i++) {
      if (i < df.table_size) {
        printf("%d\t%d\n", i, df.table[i]);
      }
    }

    free(df.table);
    df.table = NULL;
    return 0;
  } else {
    return 1;
  }
}
