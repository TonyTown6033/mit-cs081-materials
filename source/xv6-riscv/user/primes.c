#include "kernel/types.h"
#include "kernel/stat.h"
#include "user/user.h"


void prime(int pipe_out) {
    int first;
    if (read(pipe_out, &first, sizeof(first)) <= 0) {
         close(pipe_out);
         exit(0);
    }
    printf("prime %d\n", first);
    int pipe_inside[2];
    pipe(pipe_inside);
    int pid = fork();
    if (pid < 0) {
        printf("fork failed\n");
        exit(1);
    } else if (pid == 0) { 
        // child process
        close(pipe_inside[1]);
        prime(pipe_inside[0]);
    } else {
        // parent process
        close(pipe_inside[0]);
        int num = -1;
        while (read(pipe_out, &num, sizeof(num)) > 0) {
            if (num % first != 0) {
                write(pipe_inside[1], &num, sizeof(num));
            } else {
                continue;
            }
        }
        close(pipe_inside[1]);
    }
    close(pipe_out);
}

int main() {
    int pipe_init[2];
    pipe(pipe_init);
    int pid = fork();
    if (pid < 0) {
        printf("fork failed\n");
        exit(1);
    } else if (pid == 0) {
        // child process
        close(pipe_init[1]);
        prime(pipe_init[0]);
    } else {
        // parent process
        close(pipe_init[0]);
        for (int i = 2; i <= 35; i++) {
            write(pipe_init[1], &i, sizeof(i));
        }
    }
    close(pipe_init[1]);
    wait(0);
    exit(0);
}