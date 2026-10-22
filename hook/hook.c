// DYLD interposer that logs the USB traffic an IOKit client exchanges with a device.
// Wraps the COM-style plug-in interfaces returned by IOCreatePlugInInterfaceForService.
// Output: file named by $HOBO_HOOK_LOG (default ./hook.log), one line per call.
#include <IOKit/IOKitLib.h>
#include <IOKit/IOCFPlugIn.h>
#include <IOKit/usb/IOUSBLib.h>
#include <IOKit/hid/IOHIDLib.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>

#define NSLOTS 96

static FILE *g_log;
static pthread_mutex_t g_mu = PTHREAD_MUTEX_INITIALIZER;
static double g_t0;

static double now(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return tv.tv_sec + tv.tv_usec / 1e6;
}

static void logf_(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void logf_(const char *fmt, ...) {
    pthread_mutex_lock(&g_mu);
    if (!g_log) {
        const char *p = getenv("HOBO_HOOK_LOG");
        g_log = fopen(p ? p : "hook.log", "a");
        if (!g_log) { pthread_mutex_unlock(&g_mu); return; }
        setvbuf(g_log, NULL, _IOLBF, 0);
        g_t0 = now();
    }
    fprintf(g_log, "%9.4f ", now() - g_t0);
    va_list ap;
    va_start(ap, fmt);
    vfprintf(g_log, fmt, ap);
    va_end(ap);
    fputc('\n', g_log);
    pthread_mutex_unlock(&g_mu);
}

static void hexline(char *out, size_t outsz, const void *p, size_t n) {
    const unsigned char *b = p;
    size_t o = 0;
    if (n > 256) n = 256;
    for (size_t i = 0; i < n && o + 4 < outsz; i++) o += snprintf(out + o, outsz - o, "%02x ", b[i]);
    if (o) out[o - 1] = 0; else out[0] = 0;
}

// ---- vtable bookkeeping: map interface object -> original vtable ----
typedef struct { void *self; void **orig; int kind; } Entry;
enum { K_PLUGIN, K_USBDEV, K_USBINTF, K_HID };
static Entry g_tab[64];
static int g_n;

static void **orig_of(void *self) {
    void **r = NULL;
    pthread_mutex_lock(&g_mu);
    for (int i = 0; i < g_n; i++) if (g_tab[i].self == self) r = g_tab[i].orig;
    pthread_mutex_unlock(&g_mu);
    return r;
}

static void **patch(void *self, int kind, void (*fill)(void **)) {
    void ***pself = self;
    void **orig = *pself;
    void **copy = calloc(NSLOTS, sizeof(void *));
    memcpy(copy, orig, NSLOTS * sizeof(void *));
    fill(copy);
    pthread_mutex_lock(&g_mu);
    if (g_n < 64) { g_tab[g_n].self = self; g_tab[g_n].orig = orig; g_tab[g_n].kind = kind; g_n++; }
    pthread_mutex_unlock(&g_mu);
    *pself = copy;
    return orig;
}

#define ORIG(self, T, field) \
    ((T *)orig_of(self))->field
#define SLOT(T, field) (offsetof(T, field) / sizeof(void *))

// ---- async completion wrapping ----
typedef struct { void *cb; void *refcon; void *buf; unsigned len; const char *what; int pipe; } Async;

static void async_done(void *refcon, IOReturn res, void *arg0) {
    Async *a = refcon;
    char hx[800];
    unsigned n = (unsigned)(uintptr_t)arg0;
    if (n > a->len) n = a->len;
    hexline(hx, sizeof hx, a->buf, n);
    logf_("%s done pipe=%d res=0x%x n=%u | %s", a->what, a->pipe, res, (unsigned)(uintptr_t)arg0, hx);
    ((void (*)(void *, IOReturn, void *))a->cb)(a->refcon, res, arg0);
}

static Async *mk_async(const char *what, int pipe, void *buf, unsigned len, void *cb, void *refcon) {
    Async *a = calloc(1, sizeof *a);
    *a = (Async){cb, refcon, buf, len, what, pipe};
    return a;
}

// ---- USB interface wrappers ----
typedef IOUSBInterfaceInterface300 UI;

static IOReturn w_ReadPipe(void *s, UInt8 pipe, void *buf, UInt32 *sz) {
    UInt32 want = *sz;
    IOReturn r = ORIG(s, UI, ReadPipe)(s, pipe, buf, sz);
    char hx[800]; hexline(hx, sizeof hx, buf, r ? 0 : *sz);
    logf_("ReadPipe pipe=%d want=%u got=%u res=0x%x | %s", pipe, want, *sz, r, hx);
    return r;
}
static IOReturn w_ReadPipeTO(void *s, UInt8 pipe, void *buf, UInt32 *sz, UInt32 nd, UInt32 cp) {
    UInt32 want = *sz;
    IOReturn r = ORIG(s, UI, ReadPipeTO)(s, pipe, buf, sz, nd, cp);
    char hx[800]; hexline(hx, sizeof hx, buf, r ? 0 : *sz);
    logf_("ReadPipeTO pipe=%d want=%u got=%u res=0x%x | %s", pipe, want, *sz, r, hx);
    return r;
}
static IOReturn w_WritePipe(void *s, UInt8 pipe, void *buf, UInt32 sz) {
    char hx[800]; hexline(hx, sizeof hx, buf, sz);
    logf_("WritePipe pipe=%d len=%u | %s", pipe, sz, hx);
    return ORIG(s, UI, WritePipe)(s, pipe, buf, sz);
}
static IOReturn w_WritePipeTO(void *s, UInt8 pipe, void *buf, UInt32 sz, UInt32 nd, UInt32 cp) {
    char hx[800]; hexline(hx, sizeof hx, buf, sz);
    logf_("WritePipeTO pipe=%d len=%u | %s", pipe, sz, hx);
    return ORIG(s, UI, WritePipeTO)(s, pipe, buf, sz, nd, cp);
}
static IOReturn w_ReadPipeAsync(void *s, UInt8 pipe, void *buf, UInt32 sz, IOAsyncCallback1 cb, void *rc) {
    logf_("ReadPipeAsync pipe=%d want=%u", pipe, sz);
    return ORIG(s, UI, ReadPipeAsync)(s, pipe, buf, sz, async_done, mk_async("ReadPipeAsync", pipe, buf, sz, cb, rc));
}
static IOReturn w_ReadPipeAsyncTO(void *s, UInt8 pipe, void *buf, UInt32 sz, UInt32 nd, UInt32 cpt, IOAsyncCallback1 cb, void *rc) {
    logf_("ReadPipeAsyncTO pipe=%d want=%u", pipe, sz);
    return ORIG(s, UI, ReadPipeAsyncTO)(s, pipe, buf, sz, nd, cpt, async_done, mk_async("ReadPipeAsyncTO", pipe, buf, sz, cb, rc));
}
static IOReturn w_WritePipeAsync(void *s, UInt8 pipe, void *buf, UInt32 sz, IOAsyncCallback1 cb, void *rc) {
    char hx[800]; hexline(hx, sizeof hx, buf, sz);
    logf_("WritePipeAsync pipe=%d len=%u | %s", pipe, sz, hx);
    return ORIG(s, UI, WritePipeAsync)(s, pipe, buf, sz, async_done, mk_async("WritePipeAsync", pipe, buf, 0, cb, rc));
}
static IOReturn w_WritePipeAsyncTO(void *s, UInt8 pipe, void *buf, UInt32 sz, UInt32 nd, UInt32 cpt, IOAsyncCallback1 cb, void *rc) {
    char hx[800]; hexline(hx, sizeof hx, buf, sz);
    logf_("WritePipeAsyncTO pipe=%d len=%u | %s", pipe, sz, hx);
    return ORIG(s, UI, WritePipeAsyncTO)(s, pipe, buf, sz, nd, cpt, async_done, mk_async("WritePipeAsyncTO", pipe, buf, 0, cb, rc));
}

static void log_req(const char *what, int pipe, IOUSBDevRequest *r, int after) {
    char hx[800];
    int in = r->bmRequestType & 0x80;
    unsigned n = (in && !after) ? 0 : r->wLength;
    hexline(hx, sizeof hx, r->pData, n);
    logf_("%s%s pipe=%d bmRT=%02x bReq=%02x wVal=%04x wIdx=%04x wLen=%u%s | %s", what, after ? " done" : "", pipe,
          r->bmRequestType, r->bRequest, r->wValue, r->wIndex, r->wLength, after ? "" : "", hx);
}
static IOReturn w_ControlRequest(void *s, UInt8 pipe, IOUSBDevRequest *r) {
    log_req("ControlRequest", pipe, r, 0);
    IOReturn rc = ORIG(s, UI, ControlRequest)(s, pipe, r);
    log_req("ControlRequest", pipe, r, 1);
    return rc;
}
static IOReturn w_ControlRequestTO(void *s, UInt8 pipe, IOUSBDevRequestTO *r) {
    log_req("ControlRequestTO", pipe, (IOUSBDevRequest *)r, 0);
    IOReturn rc = ORIG(s, UI, ControlRequestTO)(s, pipe, r);
    log_req("ControlRequestTO", pipe, (IOUSBDevRequest *)r, 1);
    return rc;
}

static void fill_usbintf(void **v) {
    v[SLOT(UI, ReadPipe)] = w_ReadPipe;
    v[SLOT(UI, ReadPipeTO)] = w_ReadPipeTO;
    v[SLOT(UI, WritePipe)] = w_WritePipe;
    v[SLOT(UI, WritePipeTO)] = w_WritePipeTO;
    v[SLOT(UI, ReadPipeAsync)] = w_ReadPipeAsync;
    v[SLOT(UI, ReadPipeAsyncTO)] = w_ReadPipeAsyncTO;
    v[SLOT(UI, WritePipeAsync)] = w_WritePipeAsync;
    v[SLOT(UI, WritePipeAsyncTO)] = w_WritePipeAsyncTO;
    v[SLOT(UI, ControlRequest)] = w_ControlRequest;
    v[SLOT(UI, ControlRequestTO)] = w_ControlRequestTO;
}

// ---- USB device wrappers ----
typedef IOUSBDeviceInterface320 UD;
static IOReturn w_DeviceRequest(void *s, IOUSBDevRequest *r) {
    log_req("DeviceRequest", -1, r, 0);
    IOReturn rc = ORIG(s, UD, DeviceRequest)(s, r);
    log_req("DeviceRequest", -1, r, 1);
    return rc;
}
static IOReturn w_DeviceRequestTO(void *s, IOUSBDevRequestTO *r) {
    log_req("DeviceRequestTO", -1, (IOUSBDevRequest *)r, 0);
    IOReturn rc = ORIG(s, UD, DeviceRequestTO)(s, r);
    log_req("DeviceRequestTO", -1, (IOUSBDevRequest *)r, 1);
    return rc;
}
static void fill_usbdev(void **v) {
    v[SLOT(UD, DeviceRequest)] = w_DeviceRequest;
    v[SLOT(UD, DeviceRequestTO)] = w_DeviceRequestTO;
}

// ---- HID device wrappers ----
typedef IOHIDDeviceInterface122 HD;
typedef struct { void *cb; void *target; void *refcon; uint8_t *buf; } HidCb;
static HidCb g_hidcb;

static void hid_report_cb(void *target, IOReturn res, void *refcon, void *sender, uint32_t len) {
    char hx[800]; hexline(hx, sizeof hx, g_hidcb.buf, len);
    logf_("HID interrupt report len=%u res=0x%x | %s", len, res, hx);
    ((IOHIDReportCallbackFunction)g_hidcb.cb)(g_hidcb.target, res, g_hidcb.refcon, sender, len);
}
static IOReturn w_setInterruptReportHandlerCallback(void *s, uint8_t *buf, uint32_t sz,
        IOHIDReportCallbackFunction cb, void *target, void *refcon) {
    g_hidcb = (HidCb){cb, target, refcon, buf};
    logf_("HID setInterruptReportHandlerCallback bufsz=%u", sz);
    return ORIG(s, HD, setInterruptReportHandlerCallback)(s, buf, sz, hid_report_cb, target, refcon);
}
static IOReturn w_setReport(void *s, IOHIDReportType type, uint32_t id, void *buf, uint32_t sz,
        uint32_t to, IOHIDReportCallbackFunction cb, void *target, void *refcon) {
    char hx[800]; hexline(hx, sizeof hx, buf, sz);
    logf_("HID setReport type=%d id=%u len=%u | %s", type, id, sz, hx);
    return ORIG(s, HD, setReport)(s, type, id, buf, sz, to, cb, target, refcon);
}
static IOReturn w_getReport(void *s, IOHIDReportType type, uint32_t id, void *buf, uint32_t *sz,
        uint32_t to, IOHIDReportCallbackFunction cb, void *target, void *refcon) {
    IOReturn r = ORIG(s, HD, getReport)(s, type, id, buf, sz, to, cb, target, refcon);
    char hx[800]; hexline(hx, sizeof hx, buf, *sz);
    logf_("HID getReport type=%d id=%u got=%u res=0x%x | %s", type, id, *sz, r, hx);
    return r;
}
static void fill_hid(void **v) {
    v[SLOT(HD, setInterruptReportHandlerCallback)] = w_setInterruptReportHandlerCallback;
    v[SLOT(HD, setReport)] = w_setReport;
    v[SLOT(HD, getReport)] = w_getReport;
}

// ---- plug-in QueryInterface: identify and wrap the interface that comes back ----
typedef struct { CFUUIDRef id; int kind; const char *name; } Known;
static Known known[32];
static size_t nknown;
#define K(n, kind_) do { known[nknown++] = (Known){ n, kind_, #n }; } while (0)
static void init_known(void) {
    K(kIOUSBDeviceInterfaceID, K_USBDEV); K(kIOUSBDeviceInterfaceID182, K_USBDEV);
    K(kIOUSBDeviceInterfaceID187, K_USBDEV); K(kIOUSBDeviceInterfaceID197, K_USBDEV);
    K(kIOUSBDeviceInterfaceID245, K_USBDEV); K(kIOUSBDeviceInterfaceID300, K_USBDEV);
    K(kIOUSBDeviceInterfaceID320, K_USBDEV);
    K(kIOUSBInterfaceInterfaceID, K_USBINTF); K(kIOUSBInterfaceInterfaceID183, K_USBINTF);
    K(kIOUSBInterfaceInterfaceID190, K_USBINTF); K(kIOUSBInterfaceInterfaceID197, K_USBINTF);
    K(kIOUSBInterfaceInterfaceID220, K_USBINTF); K(kIOUSBInterfaceInterfaceID245, K_USBINTF);
    K(kIOUSBInterfaceInterfaceID300, K_USBINTF);
    K(kIOHIDDeviceInterfaceID, K_HID); K(kIOHIDDeviceInterfaceID121, K_HID);
    K(kIOHIDDeviceInterfaceID122, K_HID);
}

static HRESULT w_QueryInterface(void *s, REFIID iid, LPVOID *ppv) {
    HRESULT r = ORIG(s, IOCFPlugInInterface, QueryInterface)(s, iid, ppv);
    const char *name = "UNKNOWN";
    int kind = -1;
    for (size_t i = 0; i < nknown; i++) {
        CFUUIDBytes b = CFUUIDGetUUIDBytes(known[i].id);
        if (!memcmp(&b, &iid, sizeof b)) { name = known[i].name; kind = known[i].kind; break; }
    }
    char hx[64]; hexline(hx, sizeof hx, &iid, 16);
    logf_("QueryInterface %s iid=%s res=0x%x", name, hx, (unsigned)r);
    if (r == 0 && ppv && *ppv) {
        void *obj = *ppv;
        if (kind == K_USBINTF) patch(obj, kind, fill_usbintf);
        else if (kind == K_USBDEV) patch(obj, kind, fill_usbdev);
        else if (kind == K_HID) patch(obj, kind, fill_hid);
    }
    return r;
}
static void fill_plugin(void **v) {
    v[SLOT(IOCFPlugInInterface, QueryInterface)] = w_QueryInterface;
}

static IOReturn my_IOCreatePlugInInterfaceForService(io_service_t svc, CFUUIDRef pt, CFUUIDRef it,
        IOCFPlugInInterface ***out, SInt32 *score) {
    IOReturn r = IOCreatePlugInInterfaceForService(svc, pt, it, out, score);
    logf_("IOCreatePlugInInterfaceForService svc=%u res=0x%x", svc, r);
    if (r == 0 && out && *out) patch(*out, K_PLUGIN, fill_plugin);
    return r;
}

#define INTERPOSE(repl, orig) \
    __attribute__((used)) static struct { const void *r; const void *o; } _ip_##repl \
    __attribute__((section("__DATA,__interpose"))) = { (const void *)repl, (const void *)orig }
INTERPOSE(my_IOCreatePlugInInterfaceForService, IOCreatePlugInInterfaceForService);

__attribute__((constructor)) static void init(void) { init_known(); logf_("hook loaded pid=%d", getpid()); }
