#include <windows.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>

#define PROBE_VERSION "v9"

typedef int (__cdecl *setup_fn) (void *callbacks);
typedef int (__cdecl *tls_handle_fn) (uint8_t *blob, uint32_t blob_len);
typedef int (__cdecl *sgx_keys_fn) (uint8_t *out);
typedef int (__cdecl *sgx_check_fn) (void);

static const uint8_t tls_key_blob[] = {
  0xed, 0x0d, 0xec, 0x0d, 0x1c, 0x00, 0x00, 0x00,
  0x20, 0x00, 0x00, 0x00, 0x4c, 0x00, 0x00, 0x00,
  0x0d, 0x00, 0x00, 0x00, 0x59, 0x00, 0x00, 0x00,
  0x20, 0x00, 0x00, 0x00, 0x56, 0x60, 0x15, 0x60,
  0x41, 0xe0, 0x99, 0x17, 0xfb, 0x5c, 0x50, 0xbf,
  0x02, 0x3e, 0x4e, 0x06, 0xfc, 0x76, 0xca, 0x08,
  0xde, 0x4c, 0x86, 0x07, 0x7e, 0x23, 0xb6, 0xd1,
  0x10, 0x37, 0xdb, 0x64, 0xe7, 0xe5, 0x56, 0x20,
  0xbe, 0x6e, 0x29, 0x92, 0xc3, 0xd1, 0xa3, 0x30,
  0x11, 0x8a, 0xe0, 0x5a, 0x46, 0x50, 0x43, 0x20,
  0x54, 0x4c, 0x53, 0x20, 0x4b, 0x65, 0x79, 0x73,
  0x00, 0xf2, 0x08, 0x0c, 0xba, 0xb2, 0x1c, 0x70,
  0x1c, 0xda, 0x55, 0x3a, 0x71, 0xb9, 0xd7, 0x05,
  0x30, 0xfb, 0x68, 0x5e, 0xd9, 0xa0, 0xeb, 0x89,
  0x6b, 0x12, 0xd6, 0xf9, 0x4b, 0x71, 0xb3, 0x87,
  0xb5
};

static const uint8_t tls_input_stream[] = {
  0x16, 0x03, 0x03, 0x00, 0x2d, 0x01, 0x00, 0x00,
  0x29, 0x03, 0x03, 0x3f, 0x98, 0x04, 0x2f, 0xc5,
  0xb4, 0xcd, 0x70, 0x39, 0x06, 0x4f, 0xb8, 0xac,
  0xac, 0x19, 0xcc, 0x22, 0xfe, 0xd3, 0xde, 0x77,
  0x0f, 0xdd, 0x93, 0x41, 0x1c, 0x46, 0x82, 0xb8,
  0xce, 0xa9, 0xdb, 0x00, 0x00, 0x02, 0x00, 0xae,
  0x01, 0x00,
  0x16, 0x03, 0x03, 0x00, 0x0f, 0x10, 0x00, 0x00,
  0x0b, 0x00, 0x09, 0x44, 0x69, 0x73, 0x75, 0x6d,
  0x20, 0x50, 0x53, 0x4b,
  0x14, 0x03, 0x03, 0x00, 0x01, 0x01,
  0x16, 0x03, 0x03, 0x00, 0x50, 0x4d, 0x67, 0xa1,
  0xd5, 0x82, 0x08, 0x7a, 0xfa, 0x5a, 0x7e, 0xa6,
  0xb3, 0x29, 0x79, 0x3a, 0x31, 0x0b, 0xa7, 0x8c,
  0x76, 0x98, 0x87, 0xae, 0x09, 0x0d, 0x4d, 0x77,
  0x8f, 0xbc, 0xed, 0xee, 0xfc, 0xf2, 0x15, 0x19,
  0x1e, 0xe8, 0x25, 0x72, 0xae, 0x02, 0x18, 0x6d,
  0xbc, 0xc4, 0x98, 0x85, 0xfc, 0x8e, 0x2a, 0x6a,
  0x30, 0x7c, 0x02, 0x00, 0x40, 0x00, 0x00, 0x00,
  0x40, 0x00, 0x00, 0x00, 0x00, 0x93, 0xb2, 0xcc,
  0x51, 0x8c, 0xff, 0xff, 0x53, 0x03, 0x82, 0x0c,
  0x03, 0x00, 0x2d, 0x3c, 0xfc
};

static size_t tls_input_stream_pos;

static void
dump_hex (const char *label, const uint8_t *buf, size_t len)
{
  printf ("%s len=%zu", label, len);
  for (size_t i = 0; i < len; i++)
    {
      if (i % 16 == 0)
        printf ("\n  ");
      printf ("%02x", buf[i]);
    }
  printf ("\n");
}

static int
cb0 (void)
{
  printf ("cb0()\n");
  return 0;
}

static int
cb8 (uint8_t request, uint32_t value, uint32_t index, void *data, uint32_t len)
{
  printf ("cb8 control request=0x%02x value=0x%x index=0x%x len=%u data=%p\n",
          request, value, index, len, data);
  if (data && len)
    dump_hex ("cb8 data", data, len < 128 ? len : 128);
  return 0;
}

static int
cb10 (uint8_t *buf, uintptr_t len, uint32_t timeout_ms)
{
  uint32_t remaining = (uint32_t) (sizeof tls_input_stream - tls_input_stream_pos);
  uint32_t n = remaining < len ? remaining : (uint32_t) len;

  printf ("cb10 recv_timeout buf=%p max_len=%llu timeout_ms=%u remaining=%u returning=%u\n",
          buf, (unsigned long long) len, timeout_ms, remaining, n);

  if (!buf || n == 0)
    return n == 0 ? -0x6800 : -1;

  memcpy (buf, tls_input_stream + tls_input_stream_pos, n);
  tls_input_stream_pos += n;
  dump_hex ("cb10 supplied", tls_input_stream + tls_input_stream_pos - n, n);

  return (int) n;
}

static int
cb18 (const uint8_t *buf, uintptr_t len)
{
  printf ("cb18 send buf=%p len=%llu\n", buf, (unsigned long long) len);

  if (buf && len > 0 && len < 4096)
    dump_hex ("cb18 send data", buf, len);

  return (int) len;
}

static int
cb20 (void *ptr)
{
  printf ("cb20 free/log? ptr=%p\n", ptr);
  return 0;
}

static int
cb28 (void)
{
  printf ("cb28()\n");
  return 0;
}

static void *callbacks[] = {
  cb0,
  cb8,
  cb10,
  cb18,
  cb20,
  cb28,
};

static void
dump_tls_globals (HMODULE dll, const char *label)
{
  uintptr_t base = (uintptr_t) dll;
  uint8_t **key_ptr = (uint8_t **) (base + 0xfc190);
  uint32_t *key_len = (uint32_t *) (base + 0xfc198);

  printf ("%s tls_global_ptr=%p tls_global_len=%u\n",
          label, *key_ptr, *key_len);

  if (*key_ptr && *key_len > 0 && *key_len <= 256)
    dump_hex ("tls_global_key", *key_ptr, *key_len);
}

static const char *exports[] = {
  "fpc_setup_enclave",
  "FpcTaInitialize_ecall",
  "FpcTaTerminate_ecall",
  "FpcTaOp_ecall",
  "fpc_enclave_get_tls_state_ecall",
  "fpc_enclave_process_data_ecall",
  "fpc_enclave_tls_handle_connection_ecall",
  "FpcBioCreate",
  NULL,
};

int
main (int argc, char **argv)
{
  const char *mode = argc > 1 ? argv[1] : "safe";

  setvbuf (stdout, NULL, _IONBF, 0);
  setvbuf (stderr, NULL, _IONBF, 0);

  printf ("mode=%s\n", mode);
  printf ("probe_version=%s\n", PROBE_VERSION);

  HMODULE dll = LoadLibraryA ("fpc_enclave.dll");

  if (!dll)
    {
      printf ("LoadLibrary failed: %lu\n", GetLastError ());
      return 1;
    }

  printf ("loaded fpc_enclave.dll at %p\n", dll);

  for (int i = 0; exports[i]; i++)
    {
      FARPROC proc = GetProcAddress (dll, exports[i]);
      printf ("%s = %p\n", exports[i], proc);
    }

  if (strcmp (mode, "safe") == 0)
    {
      FreeLibrary (dll);
      return 0;
    }

  if (strcmp (mode, "setup") == 0 || strcmp (mode, "tls") == 0)
    {
      setup_fn setup = (setup_fn) GetProcAddress (dll, "fpc_setup_enclave");
      if (!setup)
        {
          printf ("fpc_setup_enclave missing\n");
          FreeLibrary (dll);
          return 2;
        }

      printf ("calling fpc_setup_enclave(callbacks)\n");
      int rc = setup (callbacks);
      printf ("fpc_setup_enclave -> %d (0x%x)\n", rc, (unsigned) rc);
      dump_tls_globals (dll, "after_setup");

      if (strcmp (mode, "setup") == 0)
        {
          FreeLibrary (dll);
          return rc == 0 ? 0 : 3;
        }
    }

  if (strcmp (mode, "sgx") == 0)
    {
      HMODULE sgx = LoadLibraryA ("SealTlsKey_sgx.dll");
      printf ("loaded SealTlsKey_sgx.dll at %p lastError=%lu\n",
              sgx, GetLastError ());
      if (!sgx)
        {
          FreeLibrary (dll);
          return 4;
        }

      sgx_check_fn sgx_check =
        (sgx_check_fn) GetProcAddress (sgx, "fpc_sgx_check_and_enable");
      sgx_keys_fn sgx_keys =
        (sgx_keys_fn) GetProcAddress (sgx, "fpc_sgx_keys");
      printf ("fpc_sgx_check_and_enable = %p\n", sgx_check);
      printf ("fpc_sgx_keys = %p\n", sgx_keys);
      if (sgx_check)
        {
          int rc = sgx_check ();
          printf ("fpc_sgx_check_and_enable -> %d (0x%x)\n", rc, (unsigned) rc);
        }
      if (sgx_keys)
        {
          uint8_t out[0x800];
          memset (out, 0, sizeof out);
          int rc = sgx_keys (out);
          printf ("fpc_sgx_keys -> %d (0x%x)\n", rc, (unsigned) rc);
          dump_hex ("sgx_keys out", out, 0x80);
          dump_hex ("sgx_keys out+0x200", out + 0x200, 0x40);
          dump_hex ("sgx_keys out+0x400", out + 0x400, 0x40);
        }

      FreeLibrary (sgx);
      FreeLibrary (dll);
      return 0;
    }

  if (strcmp (mode, "tls") == 0)
    {
      tls_handle_fn tls_handle =
        (tls_handle_fn) GetProcAddress (dll, "fpc_enclave_tls_handle_connection_ecall");
      if (!tls_handle)
        {
          printf ("fpc_enclave_tls_handle_connection_ecall missing\n");
          FreeLibrary (dll);
          return 5;
        }

      uint8_t blob[sizeof tls_key_blob];
      memcpy (blob, tls_key_blob, sizeof blob);

      printf ("calling fpc_enclave_tls_handle_connection_ecall(blob, %zu)\n",
              sizeof blob);
      dump_tls_globals (dll, "before_tls_handle");
      int rc = tls_handle (blob, (uint32_t) sizeof blob);
      printf ("fpc_enclave_tls_handle_connection_ecall -> %d (0x%x)\n",
              rc, (unsigned) rc);
      dump_tls_globals (dll, "after_tls_handle");
    }

  FreeLibrary (dll);
  return 0;
}
