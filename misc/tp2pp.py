from __future__ import print_function
import re

ON_BOARD_IO = 'On_Board_IO'
ON_BOARD_SERVO_ICS = 'On_Board_Servo_ICs'
EXPANSION_SERVO_ICS = 'Expansion_Servo_ICs'
MACRO_ICS = 'MACRO_ICs'
EXPANSION_IO = 'Expansion_IO'
SHARED_MEMORY = 'Shared_memory'
UNRECOGNIZED = 'Unrecognized'

card_types = [
    'On_Board_IO',
    'On_Board_Servo_ICs',
    'Expansion_Servo_ICs',
    'MACRO_ICs',
    'Expansion_IO',
    'Shared_memory',

    'Unrecognized',
]

def trim(s):
    if ',' in s:
        s = s.split(',')[0]
    return re.sub('[$XxYy:,]', '', s)

def conv_on_board_io(addr):
    return sum([0x000000,
                (addr & 0x300) * 0x1000,
                (addr & 0x7CF8) * 0x8,
                (addr & 0x7) * 4
               ])

def conv_on_board_servo_ics(addr):
    return sum([0x400000,
                (addr & 0x100) * 0x1000,
                (addr & 0x7CF8) * 0x8,
                (addr & 0x7) * 4,
                ])

def conv_expansion_servo_ics(addr):
    return sum([0x600000,
                (addr & 0x100) * 0x1000,
                (addr & 0x7CF8) * 0x8,
                (addr & 0x7) * 4
               ])

def conv_macro_ics(addr):
    return sum([0x800000,
                0,
                (addr & 0x7BF8) * 0x8,
                (addr & 0x7) * 4
               ])

def conv_expansion_io(addr):
    return sum([0xA00000,
                (addr & 0x300) * 0x1000,
                (addr & 0x70F8) * 0x8,
                (addr & 0x7) * 4
               ])

def conv_shared_memory(addr):
    return sum([0xE00000,
                (addr & 0x10000) * 0x10,
                (addr & 0x3FF8) * 0x8,
                (addr & 0x7) * 4
               ])

def tp2pp(tp_addr):
    """
    Input: tp_addr -- turbo PMAC address
    Output: (pp_addr, chip_select_info)
    """
    card_type = card_types[-1] # unrecognized

    address_info = [
        ('DPRCS_', 0x60000, SHARED_MEMORY),
        ('CS00_',  0x78800, ON_BOARD_IO),
        ('CS02_',  0x78900, ON_BOARD_IO),
        ('CS04_',  0x78A00, ON_BOARD_IO),
        ('CS06_',  0x78B00, ON_BOARD_IO),
        ('CS0_',   0x78000, ON_BOARD_SERVO_ICS),
        ('CS1_',   0x78100, ON_BOARD_SERVO_ICS),
        ('CS2_',   0x78200, EXPANSION_SERVO_ICS),
        ('CS3_',   0x78300, EXPANSION_SERVO_ICS),
        ('CS4_',   0x78400, MACRO_ICS),
        ('CS10_',  0x78C00, EXPANSION_IO),
        ('CS12_',  0x78D00, EXPANSION_IO),
        ('CS14_',  0x78E00, EXPANSION_IO),
        ('CS16_',  0x78F00, EXPANSION_IO),
    ]

    addr_offset = 0
    addr_width = 0
    if ',' in tp_addr:
        tp_info = tp_addr.split(',')
        addr_offset = int(tp_info[1])
        if len(tp_info) > 2:
            addr_width = int(tp_info[2])
        else:
            addr_width = 1

        if addr_offset > 23 or addr_width > 23:
            addr_offset = 0
            addr_width = 24

    addr = int(trim(tp_addr), 16)
    pp_addr = None

    for chip_select, mask, desc in address_info:
        if (addr & mask) == mask:
            conv_func = globals()['conv_%s' % desc.lower()]
            pp_addr = conv_func(addr)
            cs_info = '%s CS:%s' % (desc, chip_select)

    if pp_addr is None:
        raise ValueError('Unknown address: %x' % addr)

    if 'x' in tp_addr.lower():
        pp_addr = pp_addr + 0x20

    if addr_offset > 0 or addr_width > 0:
        return ('$%x.%d.%d' % (pp_addr, addr_offset + 8, addr_width), cs_info)
    else:
        return ('$%x' % (pp_addr), cs_info)

def examples():
    examples = ['78C00',
                '$78C00',
                'Y:$78C00,7',
                'Y:$78C00,0,8',
                'X:$78C00,0,8',
                'Y:$79D81,0,16',
                'Y:$79D82,0,16',
                'Y:$79D83,0,16',
                'Y:$79D84,0,16',
                'Y:$79D85,0,16',
                'Y:$79D86,0,16',
                'Y:$79D87,0,16',
                'Y:$79D88,0,16',
                'Y:$79D89,0,16',
                'Y:$79D8A,0,16',
                'Y:$79D8B,0,16',
                'Y:$79D8C,0,16',
                'Y:$79D8D,0,16',
                'Y:$79D8E,0,16',
                'Y:$79D8F,0,16',
                'Y:$79D90,0,16',
                'Y:$79D91,0,16',
                'Y:$79D92,0,16',
                'Y:$79D93,0,16',
                'Y:$79D94,0,16',
                'Y:$79D95,0,16',
                'Y:$79D96,0,16',
                'Y:$79D97,0,16',
                'Y:$79D98,0,16',
                'Y:$79D99,0,16',
                'Y:$79D9A,0,16',
                'Y:$79D9B,0,16',
                'Y:$79D9C,0,16',
                'Y:$79D9D,0,16',
                'Y:$79D9E,0,16',
                'Y:$79D9F,0,16',
                'Y:$79DA0,0,16',
                'Y:$79DA1,0,16',
                'Y:$79DA2,0,16',
                'Y:$79DA3,0,16',
                'Y:$79DA4,0,16',
                'Y:$79DA5,0,16',
                'Y:$79DA6,0,16',
                'Y:$79DA7,0,16',
                'Y:$79DA8,0,16',
                'Y:$79DA9,0,16',
                'Y:$79DAA,0,16',
                'Y:$79DAB,0,16',
                'Y:$79DAC,0,16',
                'Y:$79DAD,0,16',
                'Y:$79DAE,0,16',
                'Y:$79DAF,0,16',
                'Y:$79DB0,0,16',
                'Y:$79DB1,0,16',
                'Y:$79DB2,0,16',
                'Y:$79DB3,0,16',
                'Y:$79DB4,0,16',
                'Y:$79DB5,0,16',
                'Y:$79DB6,0,16',
                'Y:$79DB7,0,16',
                'Y:$79DB8,0,16',
                'Y:$79DB9,0,16',
                'Y:$79DBA,0,16',
                'Y:$79DBB,0,16',
                'Y:$79DBC,0,16',
                'Y:$79DBD,0,16',
                'Y:$79DBE,0,16',
                'Y:$79DBF,0,16',
                'Y:$79DC0,0,16',
                'Y:$79DC1,0,16',
                'Y:$79DC2,0,16',
                'Y:$79DC3,0,16',
                'Y:$79DC4,0,16',
                'Y:$79DC5,0,16',
                'Y:$79DC6,0,16',
                'Y:$79DC7,0,16',
                'Y:$79DC8,0,16',
                'Y:$79DC9,0,16',
                'Y:$79DCA,0,16',
                'Y:$79DCB,0,16',
                'Y:$79DCC,0,16',
                'Y:$79DCD,0,16',
                'Y:$79DCE,0,16',
                'Y:$79DCF,0,16',
                'Y:$79DD0,0,16',
                'Y:$79DD1,0,16',
                'Y:$79DD2,0,16',
                'Y:$79DD5,0,16,S',
                'Y:$79DD6,0,16,S',
                'X:$79DD7,0,16,S',
                'Y:$79DD8,0,16,S',
                'Y:$79DD9,0,16,S',
                'Y:$79DDA,0,16,S',
                'Y:$79DDB,0,16,S',
                'Y:$79DDC,0,16,S',
                'Y:$79DDD,0,16,S',
                'Y:$79DDE,0,16,S',
                'Y:$79DDF,0,16,S',
                'Y:$79DE0,0,16,S',
                'Y:$79DE1,0,16,S',
                'Y:$79DE2,0,16,S',
                'Y:$79DE3,0,16,S',
                'Y:$79DE4,0,16,S',
                'X:$79218,11',
                'X:$79218,8',
                'X:$79218,14',
                'X:$79218,16',
                'X:$79218,17',
                'X:$79218,18',
                'X:$79218,15',
                'X:$79218,20',
                'X:$79218,21',
                'X:$79218,22',
                'X:$79218,23',
                'X:$79218,20,4'
                ]

    for example in examples:
        if example.strip():
            print('Turbo PMAC: %s Power PMAC: %s' % (example, tp2pp(example)))

if __name__ == '__main__':
    import sys
    if len(sys.argv) == 1:
        print('Usage: %s (turbo pmac address 1) [address 2 [address 3] ...]' % sys.argv[0])
        sys.exit(1)

    for tpmac in sys.argv[1:]:
        print('%s\t%s' % (tpmac, '\t'.join(tp2pp(tpmac))))
