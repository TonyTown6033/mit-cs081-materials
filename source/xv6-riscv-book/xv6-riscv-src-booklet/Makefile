# make a printout
SRC=../xv6-riscv-src
FS = $(shell grep -v '^\#' runoff.list)
FILES = $(addprefix $(SRC)/,$(FS))

PRINT = runoff.list runoff.spec $(SRC)/README toc.hdr toc.ftr $(FILES)

xv6.pdf: $(PRINT)
	./runoff
	ls -l xv6-src-booklet.pdf

clean:
	rm xv6-src-booklet.pdf
	rm -rf fmt
