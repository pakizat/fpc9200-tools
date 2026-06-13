#include <errno.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>

#include <libusb.h>
#include <openssl/ssl.h>
#include <openssl/err.h>

#define VID 0x10a5
#define PID 0x9200
#define EP_IN 0x82
#define CTRL_TIMEOUT 2000
#define DATA_TIMEOUT 5000

#define CMD_INIT 0x01
#define CMD_INDICATE_S_STATE 0x08
#define CMD_GET_TLS_KEY 0x0b
#define CMD_TLS_INIT 0x05
#define CMD_TLS_DATA 0x06
#define CMD_GET_STATE 0x50

#define FPC_HOST_MS_S0 0x10

static const unsigned char default_psk[] = {
  0x04, 0x48, 0x4b, 0xb5, 0x2a, 0xb5, 0x7e, 0x46,
  0xa5, 0x97, 0x45, 0x00, 0x91, 0x5f, 0x4b, 0x98,
  0x4d, 0xe4, 0xd1, 0x16, 0x5d, 0x2e, 0x35, 0x9d,
  0x75, 0x45, 0x89, 0x60, 0x10, 0xe5, 0x0e, 0x13,
};

static void
dump_hex (const char *label, const unsigned char *buf, int len)
{
  printf ("%s len=%d", label, len);
  for (int i = 0; i < len; i++)
    {
      if (i % 16 == 0)
        printf ("\n  ");
      printf ("%02x", buf[i]);
    }
  printf ("\n");
}

static int
ctrl (libusb_device_handle *dev,
      uint8_t request,
      uint16_t value,
      uint16_t index,
      unsigned char *data,
      uint16_t len,
      int in)
{
  uint8_t bm = (in ? LIBUSB_ENDPOINT_IN : LIBUSB_ENDPOINT_OUT) |
               LIBUSB_REQUEST_TYPE_VENDOR |
               LIBUSB_RECIPIENT_DEVICE;
  int r = libusb_control_transfer (dev, bm, request, value, index,
                                   data, len, CTRL_TIMEOUT);

  printf ("ctrl %s req=0x%02x value=0x%x len=%u -> %d\n",
          in ? "IN" : "OUT", request, value, len, r);
  if (r > 0)
    dump_hex ("ctrl data", data, r < 256 ? r : 256);

  return r;
}

static int
bulk_read (libusb_device_handle *dev, unsigned char *buf, int len)
{
  int actual = 0;
  int r = libusb_bulk_transfer (dev, EP_IN, buf, len, &actual, DATA_TIMEOUT);
  if (r < 0)
    {
      printf ("bulk IN failed: %s\n", libusb_error_name (r));
      return r;
    }

  printf ("bulk IN -> %d\n", actual);
  dump_hex ("bulk data", buf, actual < 256 ? actual : 256);
  return actual;
}

static unsigned int
psk_cb (SSL *ssl,
        const char *identity,
        unsigned char *psk,
        unsigned int max_psk_len)
{
  (void) ssl;
  printf ("psk_cb identity='%s' max=%u\n", identity ? identity : "(null)", max_psk_len);
  if (!identity || strcmp (identity, "Disum PSK") != 0)
    return 0;
  if (max_psk_len < sizeof default_psk)
    return 0;
  memcpy (psk, default_psk, sizeof default_psk);
  return sizeof default_psk;
}

static int
flush_wbio_to_device (SSL *ssl, libusb_device_handle *dev)
{
  BIO *wbio = SSL_get_wbio (ssl);
  unsigned char out[2048];
  int total = 0;

  while (BIO_pending (wbio) > 0)
    {
      int n = BIO_read (wbio, out, sizeof out);
      if (n <= 0)
        break;
      dump_hex ("TLS to device", out, n);
      int r = ctrl (dev, CMD_TLS_DATA, 1, 0, out, n, 0);
      if (r < 0)
        return r;
      total += n;
    }

  return total;
}

static const unsigned char *
find_tls_record (unsigned char *buf, int len, int *tls_len)
{
  for (int i = 0; i + 5 <= len; i++)
    {
      if ((buf[i] == 0x14 || buf[i] == 0x15 || buf[i] == 0x16 || buf[i] == 0x17) &&
          buf[i + 1] == 0x03 && buf[i + 2] == 0x03)
        {
          int n = 5 + ((int) buf[i + 3] << 8) + buf[i + 4];
          if (i + n <= len)
            {
              *tls_len = n;
              return buf + i;
            }
        }
    }

  *tls_len = 0;
  return NULL;
}

static int
read_tls_event (libusb_device_handle *dev, unsigned char *out, int out_len)
{
  unsigned char buf[4096];
  int r = bulk_read (dev, buf, sizeof buf);
  if (r < 0)
    return r;

  if (r >= 12 &&
      buf[0] == 0x00 && buf[1] == 0x00 && buf[2] == 0x00 && buf[3] == 0x05)
    {
      int evt_len = ((int) buf[4] << 24) | ((int) buf[5] << 16) |
                    ((int) buf[6] << 8) | buf[7];
      int payload_len = evt_len - 12;
      int got = r - 12;

      if (payload_len <= 0 || payload_len > out_len)
        {
          printf ("invalid TLS event length evt_len=%d payload_len=%d\n",
                  evt_len, payload_len);
          return -1;
        }

      memcpy (out, buf + 12, got);
      while (got < payload_len)
        {
          r = bulk_read (dev, buf, sizeof buf);
          if (r < 0)
            return r;
          if (got + r > payload_len)
            {
              printf ("TLS event continuation too long got=%d r=%d payload=%d\n",
                      got, r, payload_len);
              return -1;
            }
          memcpy (out + got, buf, r);
          got += r;
        }

      dump_hex ("TLS event payload", out, payload_len);
      return payload_len;
    }

  int tls_len = 0;
  const unsigned char *tls = find_tls_record (buf, r, &tls_len);
  if (!tls)
    {
      printf ("no TLS record found in bulk payload\n");
      return 0;
    }

  if (tls_len > out_len)
    return -1;

  memcpy (out, tls, tls_len);
  dump_hex ("TLS event payload", out, tls_len);
  return tls_len;
}

int
main (void)
{
  libusb_context *ctx = NULL;
  libusb_device_handle *dev = NULL;
  unsigned char buf[4096];
  int r;

  SSL_library_init ();
  SSL_load_error_strings ();

  r = libusb_init (&ctx);
  if (r < 0)
    {
      printf ("libusb_init failed: %s\n", libusb_error_name (r));
      return 1;
    }

  dev = libusb_open_device_with_vid_pid (ctx, VID, PID);
  if (!dev)
    {
      printf ("device %04x:%04x not found or permission denied\n", VID, PID);
      libusb_exit (ctx);
      return 1;
    }

  libusb_set_auto_detach_kernel_driver (dev, 1);
  r = libusb_claim_interface (dev, 0);
  if (r < 0)
    {
      printf ("claim interface failed: %s\n", libusb_error_name (r));
      libusb_close (dev);
      libusb_exit (ctx);
      return 1;
    }

  ctrl (dev, CMD_INDICATE_S_STATE, FPC_HOST_MS_S0, 0, NULL, 0, 0);
  memset (buf, 0, sizeof buf);
  ctrl (dev, CMD_GET_STATE, 0, 0, buf, 1000, 1);

  unsigned char init_id[4] = {0x32, 0x1e, 0x4b, 0x0f};
  ctrl (dev, CMD_INIT, 1, 0, init_id, sizeof init_id, 0);
  r = bulk_read (dev, buf, sizeof buf);
  if (r < 0)
    goto out;

  memset (buf, 0, sizeof buf);
  r = ctrl (dev, CMD_GET_TLS_KEY, 0, 0, buf, 1000, 1);
  if (r < 0)
    goto out;

  ctrl (dev, CMD_TLS_INIT, 1, 0, NULL, 0, 0);

  SSL_CTX *ssl_ctx = SSL_CTX_new (TLS_server_method ());
  SSL_CTX_set_min_proto_version (ssl_ctx, TLS1_2_VERSION);
  SSL_CTX_set_max_proto_version (ssl_ctx, TLS1_2_VERSION);
  SSL_CTX_set_cipher_list (ssl_ctx, "PSK-AES128-CBC-SHA256");
  SSL_CTX_set_psk_server_callback (ssl_ctx, psk_cb);

  SSL *ssl = SSL_new (ssl_ctx);
  BIO *rbio = BIO_new (BIO_s_mem ());
  BIO *wbio = BIO_new (BIO_s_mem ());
  SSL_set_bio (ssl, rbio, wbio);
  SSL_set_accept_state (ssl);

  for (int iter = 0; iter < 20; iter++)
    {
      int ret = SSL_do_handshake (ssl);
      int err = SSL_get_error (ssl, ret);
      printf ("SSL_do_handshake iter=%d ret=%d err=%d state=%s\n",
              iter, ret, err, SSL_state_string_long (ssl));

      r = flush_wbio_to_device (ssl, dev);
      if (r < 0)
        break;

      if (ret == 1)
        {
          printf ("TLS handshake completed\n");
          break;
        }

      if (err != SSL_ERROR_WANT_READ && err != SSL_ERROR_WANT_WRITE)
        {
          ERR_print_errors_fp (stdout);
          break;
        }

      r = read_tls_event (dev, buf, sizeof buf);
      if (r < 0)
        break;
      if (r == 0)
        continue;

      BIO_write (SSL_get_rbio (ssl), buf, r);
    }

  SSL_free (ssl);
  SSL_CTX_free (ssl_ctx);

out:
  libusb_release_interface (dev, 0);
  libusb_close (dev);
  libusb_exit (ctx);
  return 0;
}
