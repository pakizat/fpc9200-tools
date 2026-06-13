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

        /* 读取设备 TLS 事件 */
        unsigned char evt[4096];
        int got = bulk_read(evt, sizeof(evt), 100);
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

    /* 检查 TLS record 类型 */
    if (record[0] != 0x17) return 0; /* Application Data */

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

        /* 解析事件头 */
        uint32_t cmdid = (buf[0] << 24) | (buf[1] << 16) | (buf[2] << 8) | buf[3];
        uint32_t length = (buf[4] << 24) | (buf[5] << 16) | (buf[6] << 8) | buf[7];

        if (cmdid == 0x01000000) {
            /* ARM_RESULT */
            printf("  ARM result received\n");
        } else if (cmdid == 0x06000000) {
            /* FINGER_DOWN */
            printf("  Finger detected!\n");
            finger_detected = 1;
        } else if (cmdid == 0x05000000) {
            /* FINGER_UP */
            printf("  Finger lifted\n");
        } else if (cmdid == 0x08000000) {
            /* IMG event - 包含加密图像数据 */
            printf("  IMG event (length=%u)\n", length);

            /* 复制事件数据（包含 TLS 记录） */
            int payload_len = got;
            if (payload_len > 0) {
                /* 尝试解密 */
                int dec_len = tls_decrypt_record(buf, payload_len,
                                                  decrypted + total_decrypted,
                                                  sizeof(decrypted) - total_decrypted);
                if (dec_len > 0) {
                    total_decrypted += dec_len;

                    /* 在解密数据中查找图像 */
                    /* 图像数据通常在解密载荷的固定偏移处 */
                    for (int i = 0; i < total_decrypted - IMAGE_SIZE; i++) {
                        /* 检查是否是有效的图像数据（灰度值范围） */
                        int valid = 1;
                        for (int j = 0; j < 100 && valid; j++) {
                            if (decrypted[i + j] > 250) valid = 0;
                        }
                        if (valid) {
                            memcpy(image_buf, decrypted + i, IMAGE_SIZE);
                            image_received = 1;
                            printf("  Image extracted: %d bytes\n", IMAGE_SIZE);
                            return 0;
                        }
                    }
                }
            }
        } else if (cmdid == 0x00000005) {
            /* TLS 事件 */
            /* 写入 BIO 用于解密 */
            BIO_write(rbio, buf + 12, got - 12);

            /* 尝试读取解密数据 */
            unsigned char dec_buf[8192 + IMAGE_SIZE];
            int n = SSL_read(ssl, dec_buf, sizeof(dec_buf));
            if (n > 0) {
                /* 检查是否包含图像数据 */
                if (n >= IMAGE_SIZE + 12) {
                    /* 图像数据在偏移 12 处 */
                    memcpy(image_buf, dec_buf + 12, IMAGE_SIZE);
                    image_received = 1;
                    printf("  Image captured via TLS: %d bytes\n", IMAGE_SIZE);
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
    usleep(500000);

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
            fprintf(stderr, "Failed to save image\n");
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
