.PHONY: all
all: chunk

.PHONY: clean
clean:
	rm -f chunk

chunk: chunk.c
	gcc -Wall -Wextra -g -o chunk chunk.c -ltheoradec -ltheoraenc -lvorbis -logg
