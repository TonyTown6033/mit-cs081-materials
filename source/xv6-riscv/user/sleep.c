#include "kernel/types.h"
#include "kernel/stat.h"
#include "user/user.h"

int main(int argc, char *argv[])
{
  if(argc < 2){
    fprintf(2, "Usage: sleep seconds ...\n");
    exit(1);
  }

  int seconds = atoi(argv[1]);
  if(seconds <= 0){
    fprintf(2, "sleep: invalid seconds %d\n", seconds);
    exit(1);
  }
  fprintf(1, "(nothing happens for a little while)\n");
  pause(seconds * 10);

  exit(0);
}