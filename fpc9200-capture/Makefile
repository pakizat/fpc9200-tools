CC = gcc
CFLAGS = -Wall -O2 $(shell pkg-config --cflags libusb-1.0 openssl)
LDFLAGS = $(shell pkg-config --libs libusb-1.0 openssl) -lm

TARGET = fpc9200-capture

all: $(TARGET)

$(TARGET): fpc9200-capture.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

clean:
	rm -f $(TARGET)

.PHONY: all clean
