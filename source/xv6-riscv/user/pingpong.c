#include "kernel/types.h"
#include "kernel/stat.h"
#include "user/user.h"

int main() {
    int p2c[2];
    int c2p[2];
    pipe(p2c); // Parent to Child
    pipe(c2p); // Child to Parent

    int pid = fork();
    if (pid < 0) {
        fprintf(2, "Fork failed\n");
        return 1;
    }

    if (pid == 0) {
        close(p2c[1]);
        close(c2p[0]);
        char buf[50];
        read(p2c[0], &buf, sizeof(buf));
        printf("<pid : %d>: received ping\n",getpid());
        write(c2p[1], buf, strlen(buf) + 1);
    } else {
        close(p2c[0]);
        close(c2p[1]);
        const char *message = "g";
        write(p2c[1], message, strlen(message) + 1);
        char buf[50];
        read(c2p[0], &buf, sizeof(buf));
        printf("<pid : %d>: received pong\n",getpid());
        wait(0);
    }

    return 0;
}