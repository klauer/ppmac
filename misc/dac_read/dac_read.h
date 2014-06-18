#ifndef _H_DAC_READ
#define _H_DAC_READ

// vi: ts=2 sw=2
struct DACData {
    unsigned int table_size;
    unsigned int scale_factor;
    int *table;
};

bool read_dac_file(const char *fn, DACData *df);

#endif
