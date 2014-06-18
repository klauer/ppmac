#ifndef _H_DAC_READ
#define _H_DAC_READ

// vi: ts=2 sw=2
#define DACDATA_MAGIC    0x494e54  // .INT

struct DACData {
    unsigned int table_size;
    unsigned int scale_factor;
    int *table;
};

bool read_dac_file(const char *fn, DACData *df);

#endif
