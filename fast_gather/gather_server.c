/*
 * (relatively) fast gather data server
 * - a simple forking TCP server that sends raw Power PMAC gather data
 *
 * Usage: gather_server [port]
 * Default port is 2332
 *
 * (Largely based - rather, copied - on beej's networking guide, 
 * the source of which is in the public domain)
 *
 * Author: K Lauer (klauer@bnl.gov)
 */

// vi: sw=4 ts=4

#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <errno.h>
#include <string.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <netdb.h>
#include <arpa/inet.h>
#include <sys/wait.h>
#include <signal.h>
#include <gplib.h>  // Power PMAC-specific

#define DEFAULT_PORT "2332"
#define BACKLOG 1           // how many pending connections queue will hold

// Input buffer size
const int BUF_SIZE = 100;

// Gather types as strings
#define N_GATHER_TYPES 8
const char *gather_type_str[] = {
    "uint32",
    "int32",
    "uint24",
    "int24",
    "float",
    "double",
    "ubits",
    "sbits"
};

// Gather types that aren't in the enum can be processed with these:
// (see notes below)
const unsigned int start_mask = 0xF800;
const unsigned int bit_count_mask = 0x07FF;
/*
from http://forums.deltatau.com/archive/index.php?thread-933.html :
 Undocumented gather types:
 When Gather.Type[i] is not in the range 0 to 5, it contains a code specifying what part of a 
 32-bit integer register the element specified by Gather.Addr[i] occupies. That is, when 
 Gather.Addr[i] is set in the Script environment to the address of a partial-word element, 
 Power PMAC automatically sets Gather.Type[i] to this code.

 Note that this code does not affect the gathered value, which will always be the full 32-bit 
 register. Rather, it can be used to isolate the desired portion of this 32-bit value.

 Gather.Type[i] is a 16-bit value. The high 5 bits (11-15) specify the starting (low) bit number 
 of the partial-word element in the 32-bit word. The low 11 bits (0-10) specify how many bits are 
 used. The values of interest are:

 1 bit: $7c6
 2 bits: $786
 3 bits: $746
 4 bits: $706
 8 bits: $606
 12 bits: $506
 16 bits: $407

 So for Motor[x].AmpEna, Gather.Type is set to 26566 ($67c6). This means 1 bit ($7c6) starting 
 at bit 12 (6*2 + 0).

 A value of 50694 ($c606) means 8 bits ($606) starting at bit 24 (c*2 + 0).

 More generally, the value in bits 6-10 is 32 minus the number of bits in the element.
*/

// Ensures that the full buffer is sent
int send_all(int s, const char *buf, unsigned int len)
{
    unsigned int total = 0;        // how many bytes we've sent
    unsigned int bytesleft = len; // how many we have left to send
    int n;

    while(total < len) {
        n = send(s, buf+total, bytesleft, 0);
        if (n == -1) {
            break; 
        }

        total += n;
        bytesleft -= n;
    }

    //*len = total; // return number actually sent here

    return n==-1?-1:0; // return -1 on failure, 0 on success
} 

// Send a simple string to the client
int send_str(int client, const char *str) {
    if (!str)
        return -1;

    return send_all(client, str, strlen(str));
}

// Send a string to the client with the packet length header
int send_str_packet(int client, const char *str) {
    if (!str)
        return -1;
    
    unsigned int length = strlen(str);

    send_all(client, (char*)&length, sizeof(unsigned int));
    return send_all(client, str, length);
}

// Send the type information for each gathered item to the client
// If phase is set, gathered phase information will be sent,
// otherwise gathered servo information will be sent.
bool send_types(int client, bool phase) {
    GATHER *gather;
    gather = &pshm->Gather;
    unsigned char items;
    unsigned short *types;
    unsigned int buf_len;

    if (phase) {
        items = gather->PhaseItems;
        types = gather->PhaseType;
    } else {
        items = gather->Items;
        types = &gather->Type[0];
    }

    buf_len = sizeof(items) + sizeof(unsigned short) * items + 1;

    printf("client %d types request. items=%d buffer length=%d (phase=%d)\n", client, items, buf_len, phase);
    send_all(client, (char*)&buf_len, sizeof(unsigned int));
    send_str(client, "T");
    send_all(client, (char*)&items, sizeof(unsigned char));
    send_all(client, (char*)types, sizeof(unsigned short) * items);

    return (items > 0);
}

// Send the gathered raw data to the client
// If phase is set, gathered phase data will be sent,
// otherwise gathered servo data will be sent.
void send_data(int client, bool phase) {
    GATHER *gather;
    gather = &pshm->Gather;
    int line_length=0;
    unsigned int buf_len, samples, *buffer;
    unsigned short *types;
    unsigned char items;
    
    if (phase) {
        items = gather->PhaseItems;
        types = gather->PhaseType;
        samples = gather->PhaseSamples;
        buffer = gather->PhaseBuffer;
        line_length = gather->PhaseLineLength << 2;
    } else {
        items = gather->Items;
        types = gather->Type;
        samples = gather->Samples;
        buffer = gather->Buffer;
        line_length = gather->LineLength << 2;
    }

    buf_len = sizeof(unsigned int) + (line_length * samples) + 1;

    printf("client %d data request. items=%d samples=%d bytes/line=%d buffer length=%d (phase=%d)\n", 
            client, items, gather->Samples, line_length, buf_len, phase);
    
    send_all(client, (char*)&buf_len, sizeof(unsigned int));
    send_str(client, "D");
    send_all(client, (char*)&samples, sizeof(unsigned int));
    send_all(client, (char*)buffer, (line_length * samples));
}

// Strip off CR/LF from the client buffer
void strip_buffer(char buf[], int buf_size) {
    int i;
    for (i = 0; i < buf_size; i++) {
        if (buf[i] == '\n' || buf[i] == '\r') {
            buf[i] = 0;
            return;
        } else if (buf[i] == 0) {
            return;
        }
    }

    buf[buf_size - 1] = 0;
}

int handle_client(int client) {
    int received;
    char buf[BUF_SIZE];
    bool phase=false;

    while (1) {
        if ((received = recv(client, &buf, BUF_SIZE - 1, 0)) <= 0) {
            perror("recv");
            break;
        }
        
        strip_buffer(buf, BUF_SIZE);
        if (!strcmp(buf, "phase")) {
            phase = true;
            send_str_packet(client, "K");
            printf("client %d phase mode\n", client);
        } else if (!strcmp(buf, "servo")) {
            phase = false;
            send_str_packet(client, "K");
            printf("client %d servo mode\n", client);
        } else if (!strcmp(buf, "types")) {
            send_types(client, phase); 
        } else if (!strcmp(buf, "data")) {
            send_data(client, phase); 
        } else if (!strcmp(buf, "all")) {
            if (send_types(client, phase)) {
                send_data(client, phase); 
            }
        }

        buf[0] = 0;
    }
    
    printf("client %d closed\n", client);
    return 0;

}

/// Handler for the child processes
void sigchld_handler(int s)
{
    while(waitpid(-1, NULL, WNOHANG) > 0);
}

/// Get IPv4/IPv6 address info
void *get_in_addr(struct sockaddr *sa)
{
    if (sa->sa_family == AF_INET) {
        // IPv4
        return &(((struct sockaddr_in*)sa)->sin_addr);
    } else {
        // IPv6
        return &(((struct sockaddr_in6*)sa)->sin6_addr);
    }
}

// Main server loop, listens on port
int server_loop(const char *port) {
    int sockfd, new_fd;  // listen on sock_fd, new connection on new_fd
    struct addrinfo hints, *servinfo, *p;
    struct sockaddr_storage their_addr; // connector's address information
    socklen_t sin_size;
    struct sigaction sa;
    int yes=1;
    char s[INET6_ADDRSTRLEN];
    int rv;

    // Initialize the Power PMAC gplib library
    InitLibrary();

    memset(&hints, 0, sizeof hints);
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    hints.ai_flags = AI_PASSIVE;

    if ((rv = getaddrinfo(NULL, port, &hints, &servinfo)) != 0) {
        fprintf(stderr, "getaddrinfo: %s\n", gai_strerror(rv));
        return 1;
    }

    // Bind to the first result that works
    for(p = servinfo; p != NULL; p = p->ai_next) {
        if ((sockfd = socket(p->ai_family, p->ai_socktype,
                p->ai_protocol)) == -1) {
            perror("server: socket");
            continue;
        }

        if (setsockopt(sockfd, SOL_SOCKET, SO_REUSEADDR, &yes,
                sizeof(int)) == -1) {
            perror("setsockopt");
            exit(1);
        }

        if (bind(sockfd, p->ai_addr, p->ai_addrlen) == -1) {
            close(sockfd);
            perror("server: bind");
            continue;
        }

        break;
    }

    if (p == NULL)  {
        fprintf(stderr, "server: failed to bind\n");
        return 2;
    }

    freeaddrinfo(servinfo);

    if (listen(sockfd, BACKLOG) == -1) {
        perror("listen");
        exit(1);
    }

    // reap all dead processes -- set their handler to this function
    sa.sa_handler = sigchld_handler;
    sigemptyset(&sa.sa_mask);
    sa.sa_flags = SA_RESTART;
    if (sigaction(SIGCHLD, &sa, NULL) == -1) {
        perror("sigaction");
        exit(1);
    }

    printf("server: listening on port %s\n", port);

    while(1) {  // main accept() loop
        sin_size = sizeof their_addr;
        new_fd = accept(sockfd, (struct sockaddr *)&their_addr, &sin_size);
        if (new_fd == -1) {
            perror("accept");
            continue;
        }

        inet_ntop(their_addr.ss_family,
            get_in_addr((struct sockaddr *)&their_addr),
            s, sizeof s);
        printf("server: got connection from %s\n", s);

        if (fork() == 0) {
            close(sockfd); // child doesn't need the listener
            handle_client(new_fd);
            close(new_fd);
            exit(0);
        }
        close(new_fd);
    }
    
    // Close the Power PMAC gplib library
    CloseLibrary();
    return 0;
}

int main(int argc, char *argv[])
{
    if (argc == 2) {
        int port = atoi(argv[1]);
        if (port > 0 && port < 65536) {
            return server_loop(argv[1]);
        } else {
            printf("Invalid port. Use %s [port_number]\n", argv[0]);
        }
    } else {
        return server_loop(DEFAULT_PORT);
    }

}


/*
// Some old gather tests
//
// Previously used in send_types:
    // The size of each gather element in bytes
    unsigned int gather_type_sz[] = {
        4, // sizeof(uint32),
        4, // sizeof(int32),
        4, // sizeof(uint24), // <-- TODO not sure if stored as 3 or 4 bytes
        4, // sizeof(int24),  // <-- TODO not sure if stored as 3 or 4 bytes
        sizeof(float),
        sizeof(double),
        4, //sizeof(ubits),   // <-- TODO this may be incorrect
        4  //sizeof(sbits)    // <-- TODO this may be incorrect
    };

    int j;
    unsigned short type;
    for (j = 0; j < items; j++) {
        type = types[j];
        if (type < N_GATHER_TYPES) {
            line_length += gather_type_sz[type];
        } else {
            line_length += 4;
        }
    }

   
// Miscellaneous
    unsigned int samples;
    unsigned int i;
    unsigned short type;
    GATHER *gather;
    char *p_buffer;
    unsigned int time;
    gather = &pshm->Gather;

    samples = gather->Index;
    
    printf("Index: %d\n", gather->Index);
    printf("Samples: %d (max=%d)\n", gather->Samples, gather->MaxLines);
    printf("Period: %d\n", gather->Period);
    printf("Bytes per line: %d\n", gather->LineLength); // <-- this is wrong, returns 4
    // note: turns out it's not bytes per line, but 32-bit words per line (so multiply by 4)
    printf("Items: %d\n", gather->Items);
    
    unsigned int bit_start, bit_count;
    for (i = 0; i <= gather->Items; i++) {
        type = gather->Type[i];
        printf("Type %d: %d ", i, type);
        if (type < N_GATHER_TYPES) {
            printf("(%s)", gather_type_str[type]);
        } else {
            bit_start = (type & start_mask) >> 11;
            bit_count = (type & bit_count_mask);
            bit_count = 32 - (bit_count >> 6);
            printf("(bit start %d count %d)", bit_start, bit_count);
        }
        printf("\n");
    }

    p_buffer = (char *)(gather->Buffer);

    int size, int_temp;
    unsigned int uint_temp;
    float flt_temp;
    double dbl_temp;
    int j;
    for (i = 0; i < 100; i++) {
        for (j = 0; j < gather->Items; j++) {
            type = gather->Type[j];
            if (type < N_GATHER_TYPES) {
                size = gather_type_sz[type];
            } else {
                size = 4;
            }

            switch (type) {
            case enum_uint32gat:
                memcpy(&uint_temp, (unsigned char*)p_buffer, size);
                printf("uint:%u", uint_temp);
                break;

            case enum_int32gat:
                memcpy(&int_temp, (unsigned char*)p_buffer, size);
                printf("int:%d", int_temp);
                break;

            case enum_uint24gat:
            case enum_int24gat:
                int_temp = 0;
                memcpy(&int_temp, (unsigned char*)p_buffer, size);
                printf("(int24:%d)", int_temp);
                // TODO sign extend
                break;

            case enum_floatgat:
                memcpy(&flt_temp, (unsigned char*)p_buffer, size);
                printf("float:%f", flt_temp);
                break;

            case enum_doublegat:
                memcpy(&dbl_temp, (unsigned char*)p_buffer, size);
                printf("double:%f", dbl_temp);
                break;

            case enum_ubitsgat:
            case enum_sbitsgat:
                memcpy(&uint_temp, (unsigned char*)p_buffer, sizeof(unsigned int));
                printf("?:%u", uint_temp);
                break;

            default:
                bit_start = (type & start_mask) >> 11;
                bit_count = (type & bit_count_mask);
                bit_count = 32 - (bit_count >> 6);

                memcpy(&uint_temp, (unsigned char*)p_buffer, sizeof(unsigned int));
                printf("(%u bits of):%u", bit_count, uint_temp);
                uint_temp = (uint_temp >> bit_start);
                uint_temp &= ((1 << bit_count) - 1);
                printf("=%u", uint_temp);
                break;
            }

            p_buffer += size;

            printf("\t");
        }
        printf("\n");
    }
*/

