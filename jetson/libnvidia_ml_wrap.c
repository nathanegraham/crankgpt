#define _GNU_SOURCE

#include <dlfcn.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>

typedef void *nvmlDevice_t;
typedef int nvmlReturn_t;

typedef struct nvmlMemory_st {
    unsigned long long total;
    unsigned long long free;
    unsigned long long used;
} nvmlMemory_t;

#define NVML_SUCCESS 0
#define REAL_NVML_PATH "/usr/lib/aarch64-linux-gnu/nvidia/libnvidia-ml.so.1"

typedef nvmlReturn_t (*nvmlInit_v2_fn)(void);
typedef nvmlReturn_t (*nvmlShutdown_fn)(void);
typedef nvmlReturn_t (*nvmlDeviceGetHandleByUUID_fn)(const char *uuid, nvmlDevice_t *device);
typedef nvmlReturn_t (*nvmlDeviceGetMemoryInfo_fn)(nvmlDevice_t device, nvmlMemory_t *memory);
typedef nvmlReturn_t (*nvmlDeviceGetName_fn)(nvmlDevice_t device, char *name, unsigned int length);
typedef const char *(*nvmlErrorString_fn)(nvmlReturn_t result);
typedef nvmlReturn_t (*nvmlDeviceGetCount_v2_fn)(unsigned int *deviceCount);
typedef nvmlReturn_t (*nvmlDeviceGetHandleByIndex_v2_fn)(unsigned int index, nvmlDevice_t *device);
typedef nvmlReturn_t (*nvmlDeviceGetUUID_fn)(nvmlDevice_t device, char *uuid, unsigned int length);

static pthread_once_t init_once = PTHREAD_ONCE_INIT;
static void *real_nvml_handle = NULL;
static nvmlInit_v2_fn real_nvmlInit_v2 = NULL;
static nvmlShutdown_fn real_nvmlShutdown = NULL;
static nvmlDeviceGetHandleByUUID_fn real_nvmlDeviceGetHandleByUUID = NULL;
static nvmlDeviceGetMemoryInfo_fn real_nvmlDeviceGetMemoryInfo = NULL;
static nvmlDeviceGetName_fn real_nvmlDeviceGetName = NULL;
static nvmlErrorString_fn real_nvmlErrorString = NULL;
static nvmlDeviceGetCount_v2_fn real_nvmlDeviceGetCount_v2 = NULL;
static nvmlDeviceGetHandleByIndex_v2_fn real_nvmlDeviceGetHandleByIndex_v2 = NULL;
static nvmlDeviceGetUUID_fn real_nvmlDeviceGetUUID = NULL;

static void load_real_nvml(void) {
    real_nvml_handle = dlopen(REAL_NVML_PATH, RTLD_NOW | RTLD_LOCAL);
    if (!real_nvml_handle) {
        fprintf(stderr, "nvml_wrap: failed to open %s: %s\n", REAL_NVML_PATH, dlerror());
        return;
    }

    real_nvmlInit_v2 = (nvmlInit_v2_fn)dlsym(real_nvml_handle, "nvmlInit_v2");
    real_nvmlShutdown = (nvmlShutdown_fn)dlsym(real_nvml_handle, "nvmlShutdown");
    real_nvmlDeviceGetHandleByUUID =
        (nvmlDeviceGetHandleByUUID_fn)dlsym(real_nvml_handle, "nvmlDeviceGetHandleByUUID");
    real_nvmlDeviceGetMemoryInfo =
        (nvmlDeviceGetMemoryInfo_fn)dlsym(real_nvml_handle, "nvmlDeviceGetMemoryInfo");
    real_nvmlDeviceGetName = (nvmlDeviceGetName_fn)dlsym(real_nvml_handle, "nvmlDeviceGetName");
    real_nvmlErrorString = (nvmlErrorString_fn)dlsym(real_nvml_handle, "nvmlErrorString");
    real_nvmlDeviceGetCount_v2 =
        (nvmlDeviceGetCount_v2_fn)dlsym(real_nvml_handle, "nvmlDeviceGetCount_v2");
    real_nvmlDeviceGetHandleByIndex_v2 =
        (nvmlDeviceGetHandleByIndex_v2_fn)dlsym(real_nvml_handle, "nvmlDeviceGetHandleByIndex_v2");
    real_nvmlDeviceGetUUID = (nvmlDeviceGetUUID_fn)dlsym(real_nvml_handle, "nvmlDeviceGetUUID");
}

static void ensure_real_nvml(void) {
    pthread_once(&init_once, load_real_nvml);
}

nvmlReturn_t nvmlInit_v2(void) {
    ensure_real_nvml();
    return real_nvmlInit_v2 ? real_nvmlInit_v2() : 999;
}

nvmlReturn_t nvmlShutdown(void) {
    ensure_real_nvml();
    return real_nvmlShutdown ? real_nvmlShutdown() : 999;
}

nvmlReturn_t nvmlDeviceGetHandleByUUID(const char *uuid, nvmlDevice_t *device) {
    ensure_real_nvml();

    nvmlReturn_t rc = 999;
    if (real_nvmlDeviceGetHandleByUUID) {
        rc = real_nvmlDeviceGetHandleByUUID(uuid, device);
        if (rc == NVML_SUCCESS) {
            return rc;
        }
    }

    if (!uuid || !device || !real_nvmlDeviceGetCount_v2 || !real_nvmlDeviceGetHandleByIndex_v2 ||
        !real_nvmlDeviceGetUUID) {
        return rc;
    }

    unsigned int count = 0;
    if (real_nvmlDeviceGetCount_v2(&count) != NVML_SUCCESS) {
        return rc;
    }

    for (unsigned int i = 0; i < count; i++) {
        nvmlDevice_t candidate = NULL;
        char candidate_uuid[96] = {0};

        if (real_nvmlDeviceGetHandleByIndex_v2(i, &candidate) != NVML_SUCCESS) {
            continue;
        }

        if (real_nvmlDeviceGetUUID(candidate, candidate_uuid, sizeof(candidate_uuid)) != NVML_SUCCESS) {
            continue;
        }

        if (strcmp(candidate_uuid, uuid) == 0) {
            *device = candidate;
            fprintf(stderr, "nvml_wrap: recovered handle for UUID %s via index %u\n", uuid, i);
            return NVML_SUCCESS;
        }
    }

    return rc;
}

nvmlReturn_t nvmlDeviceGetMemoryInfo(nvmlDevice_t device, nvmlMemory_t *memory) {
    ensure_real_nvml();
    return real_nvmlDeviceGetMemoryInfo ? real_nvmlDeviceGetMemoryInfo(device, memory) : 999;
}

nvmlReturn_t nvmlDeviceGetCount_v2(unsigned int *deviceCount) {
    ensure_real_nvml();
    return real_nvmlDeviceGetCount_v2 ? real_nvmlDeviceGetCount_v2(deviceCount) : 999;
}

nvmlReturn_t nvmlDeviceGetHandleByIndex_v2(unsigned int index, nvmlDevice_t *device) {
    ensure_real_nvml();
    return real_nvmlDeviceGetHandleByIndex_v2 ? real_nvmlDeviceGetHandleByIndex_v2(index, device) : 999;
}

nvmlReturn_t nvmlDeviceGetUUID(nvmlDevice_t device, char *uuid, unsigned int length) {
    ensure_real_nvml();
    return real_nvmlDeviceGetUUID ? real_nvmlDeviceGetUUID(device, uuid, length) : 999;
}

nvmlReturn_t nvmlDeviceGetName(nvmlDevice_t device, char *name, unsigned int length) {
    ensure_real_nvml();
    return real_nvmlDeviceGetName ? real_nvmlDeviceGetName(device, name, length) : 999;
}

const char *nvmlErrorString(nvmlReturn_t result) {
    ensure_real_nvml();
    return real_nvmlErrorString ? real_nvmlErrorString(result) : "nvml_wrap: no real nvml";
}
