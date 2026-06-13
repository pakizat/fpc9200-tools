/*
 * FPC 9200 单次指纹图像采集工具
 * ============================
 *
 * 直接通过 USB 协议采集单张 112×88 指纹图像
 * 不依赖 libfprint，独立运行
 *
 * 编译: make
 * 运行: sudo ./fpc9200-capture <output_file>
 *
 * 流程:
 *   1. USB 打开设备
 *   2. SET_S0 (电源状态)
 *   3. INIT (初始化)
 *   4. GET_TLS_KEY + TLS_INIT (TLS 握手)
 *   5. REFRESH_SENSOR
 *   6. ARM (等待手指)
 *   7. GET_IMG (读取加密图像)
 *   8. TLS 解密 → 保存原始图像
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <time.h>
#include <errno.h>

#include <libusb-1.0/libusb.h>
#include <openssl/ssl.h>
#include <openssl/err.h>
#include <openssl/bio.h>

/* USB 设备 */
#define VID 0x10A5
#define PID 0x9200
#define EP_IN 0x82

/* 命令 */
#define CMD_INIT 0x01
#define CMD_ARM 0x02
#define CMD_ABORT 0x03
#define CMD_INDICATE_S_STATE 0x08
#define CMD_GET_IMG 0x09
#define CMD_GET_TLS_KEY 0x0B
#define CMD_TLS_INIT 0x05
#define CMD_TLS_DATA 0x06
#define CMD_REFRESH_SENSOR 0x20
#define CMD_GET_KPI 0x0C

#define FPC_HOST_MS_S0 0x10

#define IMAGE_WIDTH 112
#define IMAGE_HEIGHT 88
#define IMAGE_SIZE (IMAGE_WIDTH * IMAGE_HEIGHT)

#define CTRL_TIMEOUT 2000
#define DATA_TIMEOUT 30000

/* TLS PSK */
static const unsigned char default_psk[] = {
    0x04, 0x48, 0x4b, 0xb5, 0x2a, 0xb5, 0x7e, 0x46,
    0xa5, 0x97, 0x45, 0x00, 0x91, 0x5f, 0x4b, 0x98,
    0x4d, 0xe4, 0xd1, 0x16, 0x5d, 0x2e, 0x35, 0x9d,
    0x75, 0x45, 0x89, 0x60, 0x10, 0xe5, 0x0e, 0x13
};
#define PSK_LEN 32
#define PSK_IDENTITY "Disum PSK"

static libusb_device_handle *dev = NULL;
static SSL_CTX *ssl_ctx = NULL;
static SSL *ssl = NULL;
static BIO *rbio = NULL;
static BIO *wbio = NULL;

/* 图像缓冲区 */
static unsigned char image_buf[IMAGE_SIZE];
static volatile int image_received = 0;
static volatile int finger_detected = 0;

/* ===== USB 通信 ===== */

static int ctrl_out(uint8_t request, uint16_t value, uint16_t index,
                    const unsigned char *data, uint16_t len) {
    return libusb_control_transfer(
        dev,
        LIBUSB_ENDPOINT_OUT | LIBUSB_REQUEST_TYPE_VENDOR | LIBUSB_RECIPIENT_DEVICE,
        request, value, index,
        (unsigned char *)data, len,
        CTRL_TIMEOUT
    );
}

static int ctrl_in(uint8_t request, uint16_t value, uint16_t index,
                   unsigned char *data, uint16_t len) {
    return libusb_control_transfer(
        dev,
        LIBUSB_ENDPOINT_IN | LIBUSB_REQUEST_TYPE_VENDOR | LIBUSB_RECIPIENT_DEVICE,
        request, value, index,
        data, len,
        CTRL_TIMEOUT
    );
}

static int bulk_read(unsigned char *buf, int len, int timeout) {
    int actual = 0;
    int r = libusb_bulk_transfer(dev, EP_IN, buf, len, &actual, timeout);
    if (r < 0 && r != LIBUSB_ERROR_TIMEOUT) {
        return r;
    }
    return actual;
}

static uint32_t event_cmd(const unsigned char *buf) {
    uint32_t raw;
    memcpy(&raw, buf, sizeof(raw));
    return raw <= 0xff ? raw : raw >> 24;
}

static uint32_t event_len(const unsigned char *buf) {
    return ((uint32_t)buf[4] << 24) |
           ((uint32_t)buf[5] << 16) |
           ((uint32_t)buf[6] << 8) |
           (uint32_t)buf[7];
}

static int read_event(const char *label, uint8_t expected_cmd,
                      unsigned char *payload, int payload_max, int timeout) {
    unsigned char buf[4096];
    int got = bulk_read(buf, sizeof(buf), timeout);
    if (got < 0) {
        fprintf(stderr, "%s event read failed: %d\n", label, got);
        return got;
    }
    if (got < 12) {
        fprintf(stderr, "%s event too short: %d\n", label, got);
        return -1;
    }

    uint32_t cmd = event_cmd(buf);
    uint32_t len = event_len(buf);
    if (expected_cmd && cmd != expected_cmd) {
        fprintf(stderr, "%s unexpected event cmd=0x%02x len=%u actual=%d\n",
                label, cmd, len, got);
        return -1;
    }
    if (len < 12) {
        fprintf(stderr, "%s invalid event length: %u\n", label, len);
        return -1;
    }

    int payload_len = (int)len - 12;
    int copied = got - 12;
    if (payload && payload_len > payload_max) {
        fprintf(stderr, "%s payload too large: %d > %d\n",
                label, payload_len, payload_max);
        return -1;
    }
    if (copied > payload_len) copied = payload_len;
    if (payload && copied > 0) memcpy(payload, buf + 12, copied);

    while (copied < payload_len) {
        got = bulk_read(buf, sizeof(buf), timeout);
        if (got <= 0) {
            fprintf(stderr, "%s continuation read failed: %d\n", label, got);
            return -1;
        }
        if (copied + got > payload_len) {
            fprintf(stderr, "%s continuation too long: copied=%d got=%d payload=%d\n",
                    label, copied, got, payload_len);
            return -1;
        }
        if (payload) memcpy(payload + copied, buf, got);
        copied += got;
    }

    printf("  %s event cmd=0x%02x payload=%d\n", label, cmd, payload_len);
    return payload_len;
}

/* ===== TLS 处理 ===== */

static unsigned int psk_callback(SSL *ssl, const char *identity,
                                  unsigned char *psk, unsigned int max_psk_len) {
    (void)ssl;
    if (!identity || strcmp(identity, PSK_IDENTITY) != 0) return 0;
    if (max_psk_len < PSK_LEN) return 0;
    memcpy(psk, default_psk, PSK_LEN);
    return PSK_LEN;
}

static int tls_init(void) {
    unsigned char tls_key[1000];
    int actual;

    printf("Initializing TLS...\n");

    /* GET_TLS_KEY */
    actual = ctrl_in(CMD_GET_TLS_KEY, 0, 0, tls_key, sizeof(tls_key));
    if (actual < 0) {
        fprintf(stderr, "GET_TLS_KEY failed: %d\n", actual);
        return -1;
    }
    printf("  GET_TLS_KEY: %d bytes\n", actual);

    /* TLS_INIT */
    ctrl_out(CMD_TLS_INIT, 1, 0, NULL, 0);

    /* 初始化 OpenSSL */
    OPENSSL_init_ssl(0, NULL);

    ssl_ctx = SSL_CTX_new(TLS_server_method());
    if (!ssl_ctx) {
        fprintf(stderr, "Failed to create TLS context\n");
        return -1;
    }

    SSL_CTX_set_min_proto_version(ssl_ctx, TLS1_2_VERSION);
    SSL_CTX_set_max_proto_version(ssl_ctx, TLS1_2_VERSION);

    if (SSL_CTX_set_cipher_list(ssl_ctx, "PSK-AES128-CBC-SHA256") != 1) {
        fprintf(stderr, "Failed to set TLS cipher\n");
        return -1;
    }

    SSL_CTX_set_psk_server_callback(ssl_ctx, psk_callback);

    ssl = SSL_new(ssl_ctx);
    rbio = BIO_new(BIO_s_mem());
    wbio = BIO_new(BIO_s_mem());
    SSL_set_bio(ssl, rbio, wbio);
    SSL_set_accept_state(ssl);

    /* TLS 握手循环 */
    for (int i = 0; i < 30; i++) {
        int ret = SSL_do_handshake(ssl);
        int ssl_err = SSL_get_error(ssl, ret);

        /* 发送 TLS 数据到设备 */
        int pending = BIO_pending(wbio);
        if (pending > 0) {
            unsigned char buf[4096];
            int len = BIO_read(wbio, buf, sizeof(buf));
            if (len > 0) {
                ctrl_out(CMD_TLS_DATA, 1, 0, buf, len);
            }
        }

        if (ret == 1) {
            printf("  TLS handshake completed (iter %d)\n", i);
            return 0;
        }

        if (ssl_err != SSL_ERROR_WANT_READ && ssl_err != SSL_ERROR_WANT_WRITE) {
            fprintf(stderr, "TLS handshake error: ret=%d err=%d\n", ret, ssl_err);
            ERR_print_errors_fp(stderr);
            return -1;
        }

        /* 读取设备 TLS 事件，去掉 12 字节事件头后喂给 OpenSSL */
        unsigned char evt[4096];
        int got = read_event("TLS", CMD_TLS_INIT, evt, sizeof(evt), 2000);
        if (got > 0) {
            BIO_write(rbio, evt, got);
        }
    }

    fprintf(stderr, "TLS handshake timeout\n");
    return -1;
}

/* 解密 TLS 应用数据 */
static int tls_decrypt_record(const unsigned char *record, int record_len,
                               unsigned char *out, int out_max) {
    if (!ssl || record_len <= 5) return 0;

    /* 写入 BIO 并解密 */
    BIO_write(rbio, record, record_len);

    int total = 0;
    unsigned char buf[4096];
    int n;

    while ((n = SSL_read(ssl, buf, sizeof(buf))) > 0) {
        if (total + n <= out_max) {
            memcpy(out + total, buf, n);
        }
        total += n;
    }

    return total;
}

/* ===== 图像采集 ===== */

static int wait_and_capture(void) {
    unsigned char buf[4096];
    unsigned char decrypted[8192 + IMAGE_SIZE];
    int total_decrypted = 0;
    int get_img_sent = 0;

    printf("Waiting for finger placement...\n");

    time_t start = time(NULL);

    while (!image_received) {
        if (time(NULL) - start > DATA_TIMEOUT / 1000) {
            fprintf(stderr, "Timeout waiting for finger\n");
            return -1;
        }

        int got = bulk_read(buf, sizeof(buf), 500);
        if (got <= 0) continue;

        if (got < 12) continue;

        if (get_img_sent) {
            const unsigned char *payload = buf;
            int payload_len = got;
            uint32_t len = event_len(buf);

            if (len >= 12 && len <= (uint32_t)(got + 65536)) {
                payload = buf + 12;
                payload_len = got - 12;
            }

            int dec_len = tls_decrypt_record(payload, payload_len,
                                              decrypted + total_decrypted,
                                              sizeof(decrypted) - total_decrypted);
            if (dec_len > 0) {
                total_decrypted += dec_len;
                printf("  Decrypted image payload chunk=%d total=%d\n",
                       dec_len, total_decrypted);
                if (total_decrypted >= 24 + IMAGE_SIZE) {
                    memcpy(image_buf, decrypted + 24, IMAGE_SIZE);
                    image_received = 1;
                    printf("  Image captured: %d bytes (plain=%d)\n",
                           IMAGE_SIZE, total_decrypted);
                    return 0;
                }
            }
            continue;
        }

        /* 解析事件头 */
        uint32_t cmdid = event_cmd(buf);
        uint32_t length = event_len(buf);

        if (cmdid == 0x01) {
            /* ARM_RESULT */
            printf("  ARM result received\n");
        } else if (cmdid == 0x06) {
            /* FINGER_DOWN */
            printf("  Finger detected!\n");
            finger_detected = 1;
            if (!get_img_sent) {
                int r = ctrl_out(CMD_GET_IMG, 0, 0, NULL, 0);
                if (r < 0) {
                    fprintf(stderr, "GET_IMG failed: %d\n", r);
                    return -1;
                }
                get_img_sent = 1;
                printf("  GET_IMG sent\n");
            }
        } else if (cmdid == 0x05) {
            /* FINGER_UP */
            printf("  Finger lifted\n");
        } else if (cmdid == 0x08) {
            /* IMG event - 包含加密图像数据 */
            printf("  IMG event (length=%u)\n", length);

            if (got > 12) {
                /* 尝试解密 */
                int dec_len = tls_decrypt_record(buf + 12, got - 12,
                                                  decrypted + total_decrypted,
                                                  sizeof(decrypted) - total_decrypted);
                if (dec_len > 0) {
                    total_decrypted += dec_len;

                    if (total_decrypted >= 24 + IMAGE_SIZE) {
                        memcpy(image_buf, decrypted + 24, IMAGE_SIZE);
                        image_received = 1;
                        printf("  Image extracted: %d bytes (plain=%d)\n",
                               IMAGE_SIZE, total_decrypted);
                        return 0;
                    }
                }
            }
        } else if (cmdid == CMD_TLS_INIT) {
            int dec_len = tls_decrypt_record(buf + 12, got - 12,
                                              decrypted + total_decrypted,
                                              sizeof(decrypted) - total_decrypted);
            if (dec_len > 0) {
                total_decrypted += dec_len;
                if (total_decrypted >= 24 + IMAGE_SIZE) {
                    memcpy(image_buf, decrypted + 24, IMAGE_SIZE);
                    image_received = 1;
                    printf("  Image captured via TLS: %d bytes (plain=%d)\n",
                           IMAGE_SIZE, total_decrypted);
                    return 0;
                }
            }
        }
    }

    return 0;
}

/* ===== 主流程 ===== */

static int capture_image(const char *output_file) {
    int r;

    /* 初始化 libusb */
    r = libusb_init(NULL);
    if (r < 0) {
        fprintf(stderr, "libusb_init failed: %d\n", r);
        return 1;
    }

    /* 打开设备 */
    dev = libusb_open_device_with_vid_pid(NULL, VID, PID);
    if (!dev) {
        fprintf(stderr, "Device %04x:%04x not found\n", VID, PID);
        libusb_exit(NULL);
        return 1;
    }

    /* 声明接口 */
    r = libusb_claim_interface(dev, 0);
    if (r < 0) {
        fprintf(stderr, "claim_interface failed: %d\n", r);
        libusb_close(dev);
        libusb_exit(NULL);
        return 1;
    }

    printf("FPC 9200 capture tool\n");
    printf("======================\n\n");

    /* 1. SET_S0 */
    printf("Step 1: SET_S0\n");
    ctrl_out(CMD_INDICATE_S_STATE, FPC_HOST_MS_S0, 0, NULL, 0);
    usleep(500000);

    /* 2. INIT */
    printf("Step 2: INIT\n");
    uint32_t session_id = 0x0f4b1e32;
    ctrl_out(CMD_INIT, 1, 0, (unsigned char *)&session_id, sizeof(session_id));
    if (read_event("INIT", 0x02, NULL, 0, DATA_TIMEOUT) < 0) {
        fprintf(stderr, "INIT event failed\n");
        goto cleanup;
    }

    /* 3. TLS 初始化 */
    printf("Step 3: TLS init\n");
    if (tls_init() < 0) {
        fprintf(stderr, "TLS init failed\n");
        goto cleanup;
    }

    /* 4. REFRESH_SENSOR */
    printf("Step 4: REFRESH_SENSOR\n");
    ctrl_out(CMD_REFRESH_SENSOR, 0, 0, NULL, 0);
    usleep(500000);

    /* 5. ARM */
    printf("Step 5: ARM (waiting for finger)\n");
    uint32_t capture_id = (uint32_t)rand();
    ctrl_out(CMD_ARM, 1, 0, (unsigned char *)&capture_id, sizeof(capture_id));

    /* 6. 等待并采集图像 */
    printf("Step 6: Capture image\n");
    if (wait_and_capture() < 0) {
        fprintf(stderr, "Capture failed\n");
        goto cleanup;
    }

    /* 7. 保存图像 */
    if (image_received) {
        FILE *f = fopen(output_file, "wb");
        if (f) {
            fwrite(image_buf, 1, IMAGE_SIZE, f);
            fclose(f);
            printf("\nImage saved to: %s (%d bytes)\n", output_file, IMAGE_SIZE);
        } else {
            fprintf(stderr, "Failed to save image %s: %s\n",
                    output_file, strerror(errno));
        }
    } else {
        fprintf(stderr, "No image received\n");
    }

cleanup:
    if (ssl) SSL_free(ssl);
    if (ssl_ctx) SSL_CTX_free(ssl_ctx);
    libusb_release_interface(dev, 0);
    libusb_close(dev);
    libusb_exit(NULL);

    return image_received ? 0 : 1;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <output_file>\n", argv[0]);
        fprintf(stderr, "\nCapture a single fingerprint image from FPC 9200 sensor.\n");
        fprintf(stderr, "Place your finger on the sensor when prompted.\n");
        return 1;
    }

    srand(time(NULL));
    return capture_image(argv[1]);
}
