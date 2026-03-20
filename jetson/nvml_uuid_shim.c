#define _GNU_SOURCE

#include <dlfcn.h>
#include <stdio.h>
#include <string.h>

typedef void *nvmlDevice_t;
typedef int nvmlReturn_t;

#define NVML_SUCCESS 0

typedef nvmlReturn_t (*nvmlDeviceGetHandleByUUID_fn)(const char *uuid, nvmlDevice_t *device);
typedef nvmlReturn_t (*nvmlDeviceGetCount_v2_fn)(unsigned int *deviceCount);
typedef nvmlReturn_t (*nvmlDeviceGetHandleByIndex_v2_fn)(unsigned int index, nvmlDevice_t *device);
typedef nvmlReturn_t (*nvmlDeviceGetUUID_fn)(nvmlDevice_t device, char *uuid, unsigned int length);

static nvmlDeviceGetHandleByUUID_fn real_handle_by_uuid(void) {
    static nvmlDeviceGetHandleByUUID_fn fn = NULL;
    if (!fn) {
        fn = (nvmlDeviceGetHandleByUUID_fn)dlsym(RTLD_NEXT, "nvmlDeviceGetHandleByUUID");
    }
    return fn;
}

static nvmlDeviceGetCount_v2_fn real_get_count(void) {
    static nvmlDeviceGetCount_v2_fn fn = NULL;
    if (!fn) {
        fn = (nvmlDeviceGetCount_v2_fn)dlsym(RTLD_NEXT, "nvmlDeviceGetCount_v2");
    }
    return fn;
}

static nvmlDeviceGetHandleByIndex_v2_fn real_handle_by_index(void) {
    static nvmlDeviceGetHandleByIndex_v2_fn fn = NULL;
    if (!fn) {
        fn = (nvmlDeviceGetHandleByIndex_v2_fn)dlsym(RTLD_NEXT, "nvmlDeviceGetHandleByIndex_v2");
    }
    return fn;
}

static nvmlDeviceGetUUID_fn real_get_uuid(void) {
    static nvmlDeviceGetUUID_fn fn = NULL;
    if (!fn) {
        fn = (nvmlDeviceGetUUID_fn)dlsym(RTLD_NEXT, "nvmlDeviceGetUUID");
    }
    return fn;
}

nvmlReturn_t nvmlDeviceGetHandleByUUID(const char *uuid, nvmlDevice_t *device) {
    nvmlReturn_t rc = 999;
    nvmlDeviceGetHandleByUUID_fn by_uuid = real_handle_by_uuid();

    if (by_uuid) {
        rc = by_uuid(uuid, device);
        if (rc == NVML_SUCCESS) {
            return rc;
        }
    }

    nvmlDeviceGetCount_v2_fn get_count = real_get_count();
    nvmlDeviceGetHandleByIndex_v2_fn by_index = real_handle_by_index();
    nvmlDeviceGetUUID_fn get_uuid = real_get_uuid();

    if (!uuid || !device || !get_count || !by_index || !get_uuid) {
        return rc;
    }

    unsigned int count = 0;
    if (get_count(&count) != NVML_SUCCESS) {
        return rc;
    }

    for (unsigned int i = 0; i < count; i++) {
        nvmlDevice_t candidate = NULL;
        char candidate_uuid[96] = {0};

        if (by_index(i, &candidate) != NVML_SUCCESS) {
            continue;
        }

        if (get_uuid(candidate, candidate_uuid, sizeof(candidate_uuid)) != NVML_SUCCESS) {
            continue;
        }

        if (strcmp(candidate_uuid, uuid) == 0) {
            *device = candidate;
            fprintf(stderr, "nvml_uuid_shim: recovered handle for UUID %s via index %u\n", uuid, i);
            return NVML_SUCCESS;
        }
    }

    return rc;
}
